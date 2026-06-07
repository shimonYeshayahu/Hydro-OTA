"""
SmartHydro AI Manager – Daily Gemini Report Generator
=====================================================
רץ פעם ביום ב-GitHub Actions cron (06:00 UTC).
1. עובר על כל הבקרים ב-Firebase RTDB.
2. מסנן רק PRO / PRO+ (תרים אחרים מדולגים).
3. בודק שהבקר חי (realtime/lastUpdate < 24h).
4. אוסף נתוני daily/ לכל הימים מתחילת המחזור הנוכחי.
5. קורא ל-Gemini API → דוח השוואתי בעברית.
6. כותב את הדוח חזרה ל-RTDB ב-controllers/{MAC}/daily/{today}/gemini_report.
7. שולח email ל-owner.
"""

import os
import json
import time
import urllib.parse
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import firebase_admin
from firebase_admin import credentials, db
from google import genai
import markdown

# ============ Plant data (mirror של plants.js – לתיאור בדוח Gemini) ============
PLANT_NAMES_HE = {
    'tomato': 'עגבנייה', 'cucumber': 'מלפפון', 'pepper': 'פלפל', 'lettuce': 'חסה',
    'basil': 'בזיליקום', 'strawberry': 'תות שדה', 'kale': 'קייל', 'spinach': 'תרד',
    'mint': 'נענע', 'parsley': 'פטרוזיליה', 'cilantro': 'כוסברה', 'chives': 'עירית',
    'bok_choy': 'בוק צ\'וי', 'swiss_chard': 'מנגולד', 'arugula': 'אורוגולה',
    'celery': 'סלרי', 'broccoli': 'ברוקולי', 'cauliflower': 'כרובית',
    'eggplant': 'חציל', 'zucchini': 'קישוא', 'beans': 'שעועית', 'peas': 'אפונה',
    'oregano': 'אורגנו', 'thyme': 'טימין', 'rosemary': 'רוזמרין', 'sage': 'מרווה',
    'stevia': 'סטיביה', 'watercress': 'גרגיר הנחלים', 'dill': 'שמיר', 'mustard': 'חרדל'
}
STAGE_NAMES_HE = {
    'seedling': 'סטרטר', 'vegetative': 'צמיחה', 'fruiting': 'פריחה/פרי',
    'all': 'כל השלבים', 'vegfruit': 'צמיחה/פרי'
}


def format_plants_for_prompt(plants_list):
    """ממיר רשימת צמחים מ-RTDB לטקסט קריא ל-Gemini."""
    if not plants_list:
        return None
    parts = []
    for p in plants_list:
        pid = p.get('id', '')
        qty = p.get('qty', 1)
        stage = p.get('stage', '')
        name = PLANT_NAMES_HE.get(pid, pid)
        stage_he = STAGE_NAMES_HE.get(stage, stage)
        parts.append(f"{name} ({stage_he}) ×{qty}")
    return ", ".join(parts)


def build_consumption_chart_url(history_entries, targets=None):
    """V25.26: REPLACED bar chart with a trend line chart.
    Old: stacked bars of acid/nutrient consumption per day — mostly empty bars,
         a single spike dominated, useless visualization.
    New: line chart of pH and EC over time with target range bands.
         Customer instantly sees stability/drift relative to targets.

    Two Y axes: pH on the right (~5-7), EC on the left (~0-3000).
    Up to 30 most recent days. Returns None if no history.
    """
    if not history_entries:
        return None
    recent = history_entries[-30:]
    labels = [d.strftime('%d/%m') for d, _ in recent]
    ph_data = [round(float(data.get('ph_avg', 0) or 0), 2) for _, data in recent]
    ec_data = [int(data.get('ec_avg', 0) or 0) for _, data in recent]

    # Targets — show as dashed reference lines (optional)
    targets = targets or {}
    ph_min = targets.get('ph_min')
    ph_max = targets.get('ph_max')
    ec_min = targets.get('ec_min')
    ec_max = targets.get('ec_max')

    datasets = [
        {
            "label": "pH", "data": ph_data, "borderColor": "#3b82f6",
            "backgroundColor": "rgba(59,130,246,0.08)", "borderWidth": 3,
            "pointRadius": 3, "pointBackgroundColor": "#3b82f6",
            "yAxisID": "yPh", "fill": False, "tension": 0.3
        },
        {
            "label": "EC (µS)", "data": ec_data, "borderColor": "#10b981",
            "backgroundColor": "rgba(16,185,129,0.08)", "borderWidth": 3,
            "pointRadius": 3, "pointBackgroundColor": "#10b981",
            "yAxisID": "yEc", "fill": False, "tension": 0.3
        }
    ]

    # Add target range bands as faint dashed lines if available
    if ph_min is not None and ph_max is not None:
        datasets.append({
            "label": f"יעד pH ({ph_min}-{ph_max})", "data": [ph_max] * len(labels),
            "borderColor": "rgba(59,130,246,0.35)", "borderDash": [4, 4],
            "borderWidth": 1, "pointRadius": 0, "yAxisID": "yPh", "fill": False
        })
        datasets.append({
            "label": "", "data": [ph_min] * len(labels),
            "borderColor": "rgba(59,130,246,0.35)", "borderDash": [4, 4],
            "borderWidth": 1, "pointRadius": 0, "yAxisID": "yPh", "fill": False
        })
    if ec_min is not None and ec_max is not None:
        datasets.append({
            "label": f"יעד EC ({ec_min}-{ec_max})", "data": [ec_max] * len(labels),
            "borderColor": "rgba(16,185,129,0.35)", "borderDash": [4, 4],
            "borderWidth": 1, "pointRadius": 0, "yAxisID": "yEc", "fill": False
        })
        datasets.append({
            "label": "", "data": [ec_min] * len(labels),
            "borderColor": "rgba(16,185,129,0.35)", "borderDash": [4, 4],
            "borderWidth": 1, "pointRadius": 0, "yAxisID": "yEc", "fill": False
        })

    chart_config = {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "title": {"display": True, "text": "מגמת pH ו-EC לאורך המחזור", "fontSize": 16},
            "legend": {"position": "bottom", "labels": {"filter": "function(item){return item.text!=='';}"}},
            "scales": {
                "yAxes": [
                    {"id": "yPh", "position": "right", "scaleLabel": {"display": True, "labelString": "pH"},
                     "ticks": {"min": 4, "max": 8, "stepSize": 0.5}, "gridLines": {"drawOnChartArea": False}},
                    {"id": "yEc", "position": "left", "scaleLabel": {"display": True, "labelString": "EC (µS)"},
                     "ticks": {"min": 0, "stepSize": 500}}
                ],
                "xAxes": [{"scaleLabel": {"display": True, "labelString": "תאריך"}}]
            }
        }
    }
    encoded = urllib.parse.quote(json.dumps(chart_config, ensure_ascii=False))
    return f"https://quickchart.io/chart?c={encoded}&w=640&h=360&bkg=white"

# --- משתני סביבה (GitHub Actions secrets) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")
SENDER_EMAIL = os.getenv("GMAIL_USER")
SENDER_PASSWORD = os.getenv("GMAIL_PASS")
service_account_info = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT"))

print("Connecting to Firebase and Gemini...")
cred = credentials.Certificate(service_account_info)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {'databaseURL': DATABASE_URL})

client = genai.Client(api_key=GEMINI_API_KEY)

REPORT_TIERS = ('pro', 'pro_plus')


def parse_cycle_start(cycle_start_str):
    """ממיר 'DD/MM/YYYY' ל-datetime, או None."""
    if not cycle_start_str or cycle_start_str == "Not Set":
        return None
    try:
        return datetime.strptime(cycle_start_str, '%d/%m/%Y')
    except (ValueError, TypeError):
        return None


def collect_daily_history(daily_node, cycle_start_dt):
    """
    מחזיר רשימה של רשומות daily מתחילת המחזור הנוכחי, ממוינות לפי תאריך.
    daily_node: dict של controllers/{MAC}/daily/{YYYY-MM-DD}: {...}
    """
    if not isinstance(daily_node, dict):
        return []
    entries = []
    cutoff = cycle_start_dt.date() if cycle_start_dt else None
    for date_key, day_data in daily_node.items():
        if not isinstance(day_data, dict):
            continue
        try:
            d = datetime.strptime(date_key, '%Y-%m-%d').date()
        except ValueError:
            continue
        if cutoff and d < cutoff:
            continue
        if 'gemini_report' in day_data:
            day_data = {k: v for k, v in day_data.items() if k != 'gemini_report'}
        entries.append((d, day_data))
    entries.sort(key=lambda e: e[0])
    return entries


def format_history_for_prompt(entries):
    if not entries:
        return "(אין עדיין נתונים יומיים מאז תחילת המחזור)"
    lines = []
    for d, data in entries:
        ph = data.get('ph_avg', 0)
        ec = data.get('ec_avg', 0)
        t = data.get('temp_avg', 0)
        ph_min = data.get('ph_min', 0); ph_max = data.get('ph_max', 0)
        ec_min = data.get('ec_min', 0); ec_max = data.get('ec_max', 0)
        lines.append(
            f"[{d.strftime('%d/%m')}] pH={ph:.2f} (min {ph_min:.2f}, max {ph_max:.2f}), "
            f"EC={ec:.0f} (min {ec_min:.0f}, max {ec_max:.0f}), Temp={t:.1f}°C"
        )
    return "\n".join(lines)


def generate_report(controller_id, settings, history_entries, cycle_start_dt, cycle_count, device_name=None, report_style='brief'):
    # PWA v25: report_style מגיע מ-email_prefs של המשתמש: 'brief' | 'detailed' | 'agronomist'
    style_pref = settings.get('ai_style', 'professional') if isinstance(settings, dict) else 'professional'
    style_text = ('מקצועי, מדעי ואנליטי' if style_pref == 'professional'
                  else 'קליל, ידידותי, ובגובה העיניים למגדל הביתי')

    # שם ידידותי במקום MAC – או כינוי המשתמש או "המערכת שלך"
    friendly_name = device_name if device_name else "המערכת"

    targets = {
        'temp_min': settings.get('temp_min', '?'), 'temp_max': settings.get('temp_max', '?'),
        'ph_min': settings.get('ph_min', '?'), 'ph_max': settings.get('ph_max', '?'),
        'ec_min': settings.get('ec_min', '?'), 'ec_max': settings.get('ec_max', '?'),
    } if isinstance(settings, dict) else {}

    cycle_str = cycle_start_dt.strftime('%d/%m/%Y') if cycle_start_dt else "לא הוגדר"
    days_into_cycle = (datetime.now().date() - cycle_start_dt.date()).days if cycle_start_dt else "?"

    # V25.24: Explicit date anchor — prevents Gemini from hallucinating dates
    today_iso = datetime.now().strftime('%Y-%m-%d')      # 2026-06-07
    today_dmy = datetime.now().strftime('%d/%m/%Y')      # 07/06/2026
    today_weekday_he = ['שני','שלישי','רביעי','חמישי','שישי','שבת','ראשון'][datetime.now().weekday()]

    today_summary = ""
    if history_entries:
        d, data = history_entries[-1]
        today_summary = (f"היום ({d.strftime('%d/%m/%Y')}): pH={data.get('ph_avg', 0):.2f}, "
                         f"EC={data.get('ec_avg', 0):.0f}, Temp={data.get('temp_avg', 0):.1f}°C")

    # V13.27+: מידע על הצמחים שגדלים – לדוח מותאם אישית
    plants_list = settings.get('plants', []) if isinstance(settings, dict) else []
    plants_text = format_plants_for_prompt(plants_list)
    plants_section = f"\nצמחים בגידול נוכחי: {plants_text}\n" if plants_text else "\n(לא הוגדרו צמחים ספציפיים בהגדרות.)\n"

    # סיכום צריכת חומרים מצטברת
    total_ph_sec = sum(int(d.get('ph_sec_total', 0) or 0) for _, d in history_entries)
    total_ec_sec = sum(int(d.get('ec_sec_total', 0) or 0) for _, d in history_entries)
    consumption_text = f"\nצריכת חומרים מצטברת במחזור: חומצה pH – {total_ph_sec} שניות, דשן EC – {total_ec_sec} שניות.\n"

    # V25.28: ULTRA-STRICT format — every sentence on its own paragraph.
    # This is enforced both in the prompt AND by post-processing the HTML output.
    # Gemini-generated long paragraphs were the #1 reason emails looked like
    # walls of text. We now force each sentence to be its own <p> tag.
    if report_style == 'brief':
        style_instructions = """**סגנון: תקציר יומי קצרצר.**

מבנה חובה (5-7 שורות בלבד):
- ## כותרת: סטטוס יום במשפט אחד.
- שורה לpH: ערך + יעד + ✓ או ⚠.
- שורה לEC: ערך + יעד + ✓ או ⚠.
- שורה לטמפ': ערך + יעד + ✓ או ⚠.
- אם תקין: "אין פעולה נדרשת."
- אם חורג: משפט אחד עם הפעולה הנדרשת.

**חוקי כתיבה קריטיים** (חובה לפעול לפיהם):
- כל משפט בשורה נפרדת. בין משפטים — שורה ריקה (`\\n\\n`).
- משפטים קצרים: עד 12 מילים.
- אסור פסקאות ארוכות. אסור משפטים מורכבים.
- אסור טבלאות. אסור רשימות."""
    else:  # 'agronomist' (PRO+) — also catches legacy 'detailed'
        style_instructions = """**סגנון: דוח אגרונומי מובנה.**

מבנה חובה (3 חלקים):

## 1. סטטוס נוכחי
משפט סטטוס אחד. שורה ריקה.

| מדד | היום | אתמול | יעד | סטטוס |
|---|---|---|---|---|
| pH | X.XX | X.XX | X-X | ✓/⚠ |
| EC | XXXX | XXXX | XXXX-XXXX | ✓/⚠ |
| טמפ' | XX.X | XX.X | XX-XX | ✓/⚠ |

## 2. ניתוח
2-3 משפטים על המגמה. כל משפט בשורה נפרדת. בין משפטים — שורה ריקה.

## 3. המלצות
1. פעולה ראשונה. נימוק קצר.
2. פעולה שנייה. נימוק קצר.
3. פעולה שלישית. נימוק קצר.
(עד 3 פעולות בלבד. אם אין צורך — דלג חלק זה.)

**חוקי כתיבה קריטיים** (חובה לפעול לפיהם):
- כל משפט בשורה נפרדת. בין משפטים — שורה ריקה (`\\n\\n`).
- משפטים קצרים: עד 15 מילים.
- אסור פסקאות ארוכות. אסור משפטים מורכבים.
- אסור הסברים תיאורטיים."""

    prompt = f"""אתה אגרונום מומחה למערכות הידרופוניקה. הפק דוח קצר ומדויק.

תאריך היום: {today_dmy} ({today_weekday_he}). אל תכתוב תאריך אחר.
מחזור #{cycle_count}, יום {days_into_cycle} (החל {cycle_str}). אל תחשב מחדש.
{plants_section}
יעדים: pH {targets.get('ph_min')}-{targets.get('ph_max')} | EC {targets.get('ec_min')}-{targets.get('ec_max')} µS | טמפ׳ {targets.get('temp_min')}-{targets.get('temp_max')} °C

{today_summary}
{consumption_text}
היסטוריה:
{format_history_for_prompt(history_entries)}

{style_instructions}

חוקים:
- התחל ישר במהות, ללא ברכות.
- מספר הימים = {days_into_cycle}. התאריך = {today_dmy}. ללא המצאות.
- אם מחזור צעיר (פחות מ-3 ימים) — ציין שאין מספיק נתונים לניתוח.
- Markdown בעברית, כותרות ## והדגשות **. טבלאות בפורמט |---|."""

    response = _gemini_generate_with_fallback(prompt)
    return response.text


# V25.27: Multi-model fallback chain.
# Each model has its own retry-with-backoff. If one model completely exhausts retries,
# we move to the next model in the chain. The 3 models share similar quality for
# agronomic reports but draw from DIFFERENT capacity pools at Google's side, so they
# rarely all go down at once.
#
# Models in priority order:
#   1. gemini-2.5-flash      — primary (best balance)
#   2. gemini-2.0-flash      — different generation, separate capacity pool
#   3. gemini-1.5-flash      — legacy, very stable, almost never busy
#
# Free-tier limits per model are independent, so this also TRIPLES our daily quota.
_FALLBACK_MODEL_CHAIN = [
    'gemini-2.5-flash',
    'gemini-2.0-flash',
    'gemini-1.5-flash',
]


def _gemini_generate_with_fallback(prompt):
    """Try each model in the chain. Returns response from first success.
    Raises last exception only if ALL models fail across ALL retries.
    """
    last_exc = None
    for i, model in enumerate(_FALLBACK_MODEL_CHAIN):
        try:
            print(f"  Trying model {i+1}/{len(_FALLBACK_MODEL_CHAIN)}: {model}")
            return _gemini_generate_with_retry(prompt, model=model)
        except Exception as e:
            last_exc = e
            err_str = str(e)[:150]
            is_last_model = (i == len(_FALLBACK_MODEL_CHAIN) - 1)
            if is_last_model:
                print(f"  ALL MODELS EXHAUSTED — final failure on {model}: {err_str}")
                raise
            print(f"  Model {model} exhausted retries: {err_str}")
            print(f"  Falling through to next model in chain...")
    # Defensive — should never reach here
    if last_exc:
        raise last_exc
    raise RuntimeError("Fallback chain exited unexpectedly")


# V25.24: Retry transient Gemini errors (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED, etc.)
# Without this, the daily cron at 06:00 UTC fails silently when Google has a load spike,
# and customers lose their daily report for that day with no explanation.
def _gemini_generate_with_retry(prompt, model='gemini-2.5-flash', max_attempts=3):
    """Call a SPECIFIC model with exponential backoff on transient errors.
    Delays: 5s -> 15s -> 30s. Total worst case per model: ~50s.
    Raises the last exception if all attempts fail.
    """
    transient_markers = ('503', '429', 'UNAVAILABLE', 'RESOURCE_EXHAUSTED',
                         'DEADLINE_EXCEEDED', 'timeout', 'Timeout')
    delays = [5, 15, 30]  # seconds between attempts

    last_exc = None
    for attempt in range(max_attempts):
        try:
            return client.models.generate_content(model=model, contents=prompt)
        except Exception as e:
            last_exc = e
            err_str = str(e)
            is_transient = any(m in err_str for m in transient_markers)
            is_last = (attempt == max_attempts - 1)
            if not is_transient or is_last:
                # Permanent error OR ran out of attempts — give up THIS model
                print(f"    [{model}] FAILED (attempt {attempt+1}/{max_attempts}): {err_str[:200]}")
                raise
            delay = delays[attempt]
            print(f"    [{model}] transient error (attempt {attempt+1}/{max_attempts}), "
                  f"retrying in {delay}s: {err_str[:120]}")
            time.sleep(delay)
    # Should never reach here but for safety:
    raise last_exc if last_exc else RuntimeError("Retry loop exited unexpectedly")


def _split_sentences_to_paragraphs(md_text):
    """V25.28: split each line containing multiple sentences into separate paragraphs.
    Sentence boundary: '.', '!', '?' or ':' followed by space + Hebrew/Latin letter.
    Avoids splitting decimals like '1.5' (lookbehind requires non-digit).
    Skips tables, headings, list items, code blocks.
    Output: each sentence on its own line, with blank line between → renders as
    separate <p> tags in Markdown.
    """
    import re
    out_blocks = []
    for line in md_text.split('\n'):
        stripped = line.lstrip()
        # Skip non-paragraph lines
        if (not stripped or stripped.startswith('|') or stripped.startswith('#') or
                stripped.startswith('- ') or stripped.startswith('* ') or
                stripped.startswith('```') or
                re.match(r'^\d+\.\s', stripped)):
            out_blocks.append(line)
            continue
        # Split at sentence boundaries
        # Pattern: (non-digit char)(. or : or ! or ?)(space)(Hebrew or uppercase Latin letter)
        sentences = re.split(
            r'(?<=[א-תa-zA-Z\)\]])([\.\:\!\?])\s+(?=[א-תA-Z])',
            line
        )
        # Re-join keeping the delimiters
        if len(sentences) <= 1:
            out_blocks.append(line)
            continue
        result = []
        for i in range(0, len(sentences), 2):
            sent = sentences[i]
            delim = sentences[i+1] if i+1 < len(sentences) else ''
            full = sent + delim
            if full.strip():
                result.append(full.strip())
        # Each sentence becomes its own paragraph (blank line between)
        out_blocks.append('\n\n'.join(result))
    return '\n'.join(out_blocks)


def _polish_html_for_email(html):
    """V25.28: post-process Markdown-rendered HTML to bulletproof email-client display.
    - Force every <table> to width=584, table-layout:fixed, border-collapse:collapse
    - Add explicit max-width on <p> and <li>
    - Wrap loose text after periods with <br> (defensive — should already be split)
    """
    import re
    # Force tables to behave inside the 640px container
    html = re.sub(
        r'<table>',
        '<table width="584" cellpadding="6" cellspacing="0" border="0" style="width:584px;max-width:584px;border-collapse:collapse;table-layout:fixed;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;margin:12px 0;">',
        html
    )
    html = re.sub(
        r'<th>',
        '<th style="background:#ede9fe;color:#5b21b6;padding:10px 8px;text-align:right;font-weight:700;font-size:13px;border-bottom:2px solid #ddd6fe;word-wrap:break-word;">',
        html
    )
    html = re.sub(
        r'<td>',
        '<td style="padding:10px 8px;text-align:right;font-size:13px;border-bottom:1px solid #e5e7eb;word-wrap:break-word;vertical-align:top;">',
        html
    )
    # Make headings prominent
    html = re.sub(
        r'<h2>',
        '<h2 style="color:#6d28d9;font-size:18px;font-weight:700;margin:24px 0 12px 0;padding:8px 12px;background:#f5f3ff;border-right:4px solid #6d28d9;border-radius:6px;">',
        html
    )
    html = re.sub(
        r'<h3>',
        '<h3 style="color:#374151;font-size:15px;font-weight:700;margin:16px 0 8px 0;">',
        html
    )
    # Paragraphs: explicit width + line height + spacing
    html = re.sub(
        r'<p>',
        '<p style="margin:8px 0;font-size:14px;line-height:1.7;color:#1f2937;max-width:584px;">',
        html
    )
    # List items: card style + numbered badges
    html = re.sub(
        r'<ol>',
        '<ol style="padding:0;margin:14px 0;list-style:none;counter-reset:rec;">',
        html
    )
    html = re.sub(
        r'<ul>',
        '<ul style="padding:0;margin:14px 0;list-style:none;">',
        html
    )
    html = re.sub(
        r'<li>',
        '<li style="background:#f9fafb;border-right:3px solid #8b5cf6;border-radius:6px;padding:12px 16px;margin:8px 0;font-size:14px;line-height:1.7;color:#1f2937;max-width:584px;">',
        html
    )
    # Strong: brand color
    html = re.sub(
        r'<strong>',
        '<strong style="color:#5b21b6;font-weight:700;">',
        html
    )
    return html


def send_report_email(client_email, controller_id, report_md, cycle_count, days_into_cycle, chart_url=None, plants_text=None, device_name=None):
    # V25.28: aggressive sentence-splitting + HTML inline-styling.
    # Step 1: split multi-sentence paragraphs into separate Markdown paragraphs.
    report_md = _split_sentences_to_paragraphs(report_md)
    # Step 2: convert Markdown to HTML. tables=Markdown tables, nl2br=NOT here
    #         (we use \n\n paragraph breaks instead, more reliable).
    html_body = markdown.markdown(report_md, extensions=['tables'])
    # Step 3: inject inline styles on EVERY tag because Outlook ignores <style> tags
    html_body = _polish_html_for_email(html_body)
    friendly_name = device_name if device_name else "SmartHydro"
    # V25.25: email-client-safe chart row (table-based, not div)
    chart_html = ""
    if chart_url:
        chart_html = f"""
        <tr>
          <td width="640" style="width:640px;padding:20px 24px;background-color:#fafafa;border-top:1px solid #e5e7eb;text-align:center;">
            <div style="font-size:13px;font-weight:bold;color:#374151;margin-bottom:10px;">📈 מגמת pH ו-EC לאורך המחזור</div>
            <img src="{chart_url}" alt="גרף מגמה" width="592" style="width:592px;max-width:592px;height:auto;border:1px solid #e5e7eb;border-radius:8px;display:block;margin:0 auto;">
            <div style="font-size:10px;color:#6b7280;margin-top:8px;line-height:1.5;">קווים מלאים: ערך יומי. קווים מקווקווים: גבולות היעד.</div>
          </td>
        </tr>"""
    else:
        chart_html = """
        <tr>
          <td width="640" style="width:640px;padding:14px;background-color:#fafafa;border-top:1px solid #e5e7eb;text-align:center;color:#9ca3af;font-size:11px;">
            📈 גרף מגמה יופיע כשיצטברו נתונים יומיים.
          </td>
        </tr>"""

    # V25.27: plants row at locked 640px width
    plants_html_row = ""
    if plants_text:
        plants_html_row = f"""
        <tr>
          <td width="640" bgcolor="#f0fdf4" style="width:640px;background-color:#f0fdf4;padding:10px 24px;border-bottom:1px solid #bbf7d0;text-align:right;font-size:12px;">
            <span style="color:#15803d;font-weight:bold;">🌱 גידול נוכחי:</span>
            <span style="color:#166534;"> {plants_text}</span>
          </td>
        </tr>"""

    # V25.28: Two-layer wrapper (industry standard for email).
    # Layer 1: 100%-wide table with light-gray background (visible "page" around content)
    # Layer 2: 640px-wide content table centered inside, with visible frame
    # ALL inline styles — no <style> tag, no @media. Outlook respects this 100%.
    html_template = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<title>דוח אגרונומי</title>
</head>
<body style="margin:0;padding:0;background-color:#e5e7eb;font-family:'Heebo','Segoe UI',Arial,sans-serif;direction:rtl;">

  <!-- Layer 1: outer 100% width with page background -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#e5e7eb" style="background-color:#e5e7eb;">
    <tr>
      <td align="center" valign="top" style="padding:20px 10px;">

        <!-- Layer 2: 640px content with visible frame -->
        <table align="center" cellpadding="0" cellspacing="0" border="0" width="640" style="width:640px;max-width:640px;background-color:#ffffff;border:2px solid #6d28d9;border-radius:12px;">

          <!-- Header (centered, prominent) -->
          <tr>
            <td width="640" bgcolor="#6d28d9" style="width:640px;background-color:#6d28d9;color:#ffffff;padding:20px;text-align:center;">
              <div style="font-size:20px;font-weight:700;color:#ffffff;margin-bottom:6px;">📊 דוח אגרונומי יומי</div>
              <div style="font-size:13px;color:#ffffff;">{friendly_name}</div>
              <div style="font-size:12px;color:#ddd6fe;margin-top:4px;">מחזור #{cycle_count} &middot; יום {days_into_cycle}</div>
            </td>
          </tr>

          {plants_html_row}

          <!-- Content -->
          <tr>
            <td width="640" style="width:640px;padding:20px 24px;font-family:'Heebo','Segoe UI',Arial,sans-serif;color:#1f2937;font-size:14px;line-height:1.75;text-align:right;direction:rtl;word-break:break-word;">
              {html_body}
            </td>
          </tr>

          {chart_html}

          <!-- Footer -->
          <tr>
            <td width="640" bgcolor="#f5f3ff" style="width:640px;background-color:#f5f3ff;padding:14px;text-align:center;font-size:11px;color:#6d28d9;border-top:1px solid #ddd6fe;">
              &copy; 2026 SmartHydro Systems
            </td>
          </tr>
        </table>

      </td>
    </tr>
  </table>

</body>
</html>"""
    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = client_email
    msg['Subject'] = f"דוח אגרונומי יומי – {friendly_name}"
    msg.attach(MIMEText(html_template, 'html', 'utf-8'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(SENDER_EMAIL, SENDER_PASSWORD)
    server.send_message(msg)
    server.quit()


def get_email_prefs_by_email(target_email, all_users=None):
    """PWA v25: מוצא העדפות מייל לפי כתובת. מחזיר dict או None.
    all_users: optional cache - אם מועבר, חוסך קריאה חוזרת."""
    if not target_email:
        return None
    target = target_email.strip().lower()
    try:
        if all_users is None:
            all_users = db.reference('users').get() or {}
        for uid, user_data in (all_users or {}).items():
            if not isinstance(user_data, dict):
                continue
            prefs = user_data.get('email_prefs')
            if not isinstance(prefs, dict):
                continue
            user_email = (prefs.get('email') or '').strip().lower()
            if user_email == target:
                return prefs
    except Exception as e:
        print(f"get_email_prefs_by_email error: {e}")
    return None


def should_send_today(prefs, now_dt=None):
    """PWA v25: האם לשלוח דוח היום לפי email_prefs?
    Returns: (should_send: bool, reason: str)
    """
    if prefs is None:
        # ברירת מחדל לחשבונות ישנים שלא הגדירו - כן שלח (התנהגות קיימת)
        return (True, 'no_prefs_default_send')
    if not prefs.get('enabled', False):
        return (False, 'disabled_by_user')
    freq = prefs.get('frequency', 'daily')
    if freq == 'weekly':
        # שלח רק ביום ראשון (weekday()==6 ב-Python, או isoweekday()==7)
        now_dt = now_dt or datetime.now()
        if now_dt.isoweekday() != 7:
            return (False, f'weekly_not_sunday_today={now_dt.isoweekday()}')
    return (True, 'ok')


def process_all_controllers():
    print("Fetching all controllers from Firebase...")
    all_controllers = db.reference('controllers').get() or {}
    if not all_controllers:
        print("No controllers found.")
        return

    # PWA v25: cache של כל ה-users לחיפוש email_prefs (חוסך N קריאות)
    try:
        all_users = db.reference('users').get() or {}
    except Exception:
        all_users = {}

    now_ts = datetime.now().timestamp()
    today_str = datetime.now().strftime('%Y-%m-%d')
    run_start_ts = now_ts

    # V25.24: monitoring counters — saved to RTDB + alert if error rate high
    stats = {
        'total_controllers': len(all_controllers),
        'sent_ok': 0,
        'gemini_errors': 0,
        'tier_blocked': 0,
        'expired': 0,
        'offline': 0,
        'no_owner_email': 0,
        'opted_out': 0,
        'email_errors': 0,
        'rtdb_save_errors': 0,
    }

    for controller_id, data in all_controllers.items():
        print(f"\n[{controller_id}] -----------------------------------")
        if not isinstance(data, dict):
            continue

        # 1. סינון לפי tier (subscription/tier; ברירת מחדל = free)
        sub = data.get('subscription', {}) if isinstance(data.get('subscription'), dict) else {}
        tier = sub.get('tier', 'free')
        if tier not in REPORT_TIERS:
            print(f"[{controller_id}] Skipped: tier={tier}")
            stats['tier_blocked'] += 1
            continue
        # תוקף פג? עדיין נשלח דוח אם זה הוא יום התפוגה (לקוח אמור לקבל אישור עד הרגע האחרון)
        expires_at = sub.get('expiresAt', 0)
        if expires_at and expires_at < now_ts:
            print(f"[{controller_id}] Skipped: subscription expired")
            stats['expired'] += 1
            continue

        # 2. בדיקת דופק (realtime/lastUpdate בתוך 24h אחרונות)
        realtime = data.get('realtime', {}) if isinstance(data.get('realtime'), dict) else {}
        last_update = realtime.get('lastUpdate', 0) or 0
        if now_ts - last_update > 86400:
            print(f"[{controller_id}] Skipped: offline (last_update={last_update})")
            stats['offline'] += 1
            continue

        # 3. owner email
        owner_email = data.get('owner_email') or ''
        if not owner_email:
            print(f"[{controller_id}] Skipped: no owner_email")
            stats['no_owner_email'] += 1
            continue

        # PWA v25: בדיקת email_prefs של המשתמש
        email_prefs = get_email_prefs_by_email(owner_email, all_users=all_users)
        should_send, reason = should_send_today(email_prefs)
        if not should_send:
            print(f"[{controller_id}] Skipped email (per user prefs): {reason}")
            stats['opted_out'] += 1
            continue
        report_style = (email_prefs or {}).get('style', 'brief')

        # 4. cycle metadata
        meta = data.get('meta', {}) if isinstance(data.get('meta'), dict) else {}
        cycle_start_dt = parse_cycle_start(meta.get('cycleStartDate'))
        cycle_count = meta.get('cycleCount', 1)

        # 5. היסטוריה יומית מתחילת המחזור
        daily_node = data.get('daily', {})
        history = collect_daily_history(daily_node, cycle_start_dt)

        # 6. הפקת דוח
        try:
            settings = data.get('settings', {}) if isinstance(data.get('settings'), dict) else {}
            device_name = settings.get('deviceName') or None
            report_md = generate_report(controller_id, settings, history, cycle_start_dt, cycle_count,
                                        device_name=device_name, report_style=report_style)
        except Exception as e_gen:
            print(f"[{controller_id}] Gemini error: {e_gen}")
            stats['gemini_errors'] += 1
            continue

        # 7. שמירה ב-RTDB (כדי שהדשבורד יוכל להציג)
        try:
            db.reference(f'controllers/{controller_id}/daily/{today_str}/gemini_report').set({
                'generated_at': int(now_ts),
                'markdown': report_md,
                'cycle_count': cycle_count
            })
        except Exception as e_save:
            print(f"[{controller_id}] RTDB save warning: {e_save}")
            stats['rtdb_save_errors'] += 1

        # 8. שליחת email – עם גרף צריכה ומידע על צמחים
        try:
            days_into_cycle = (datetime.now().date() - cycle_start_dt.date()).days if cycle_start_dt else 0
            settings_for_chart = data.get('settings', {}) if isinstance(data.get('settings'), dict) else {}
            chart_targets = {
                'ph_min': settings_for_chart.get('ph_min'),
                'ph_max': settings_for_chart.get('ph_max'),
                'ec_min': settings_for_chart.get('ec_min'),
                'ec_max': settings_for_chart.get('ec_max'),
            }
            chart_url = build_consumption_chart_url(history, targets=chart_targets)
            plants_text = format_plants_for_prompt(settings.get('plants', []) if isinstance(settings, dict) else [])
            send_report_email(owner_email, controller_id, report_md, cycle_count, days_into_cycle,
                              chart_url=chart_url, plants_text=plants_text, device_name=device_name)
            print(f"[{controller_id}] Report sent to {owner_email} (chart={'yes' if chart_url else 'no'})")
            stats['sent_ok'] += 1
        except Exception as e_email:
            print(f"[{controller_id}] Email error: {e_email}")
            stats['email_errors'] += 1

    # V25.24: monitoring — save run stats to Firebase + alert if errors are high
    _save_and_alert_run_stats(stats, run_start_ts)


def _save_and_alert_run_stats(stats, run_start_ts):
    """Persist run statistics to Firebase RTDB and send admin alert if error rate exceeds threshold.
    Saves to system/ai_manager_stats/last (overwritten each run) AND
    system/ai_manager_stats/history/{YYYY-MM-DD} (one entry per day).
    """
    run_end_ts = datetime.now().timestamp()
    eligible = (stats['sent_ok'] + stats['gemini_errors'] + stats['email_errors']
                + stats['rtdb_save_errors'])
    error_count = stats['gemini_errors'] + stats['email_errors']
    error_rate = (error_count / eligible) if eligible > 0 else 0.0

    full_stats = {
        **stats,
        'eligible': eligible,
        'error_count': error_count,
        'error_rate': round(error_rate, 4),
        'run_duration_sec': round(run_end_ts - run_start_ts, 1),
        'run_finished_at': int(run_end_ts),
        'run_date': datetime.now().strftime('%Y-%m-%d'),
    }

    print(f"\n=== Run summary ===")
    for k, v in full_stats.items():
        print(f"  {k}: {v}")

    # Save to Firebase
    try:
        db.reference('system/ai_manager_stats/last').set(full_stats)
        db.reference(f"system/ai_manager_stats/history/{full_stats['run_date']}").set(full_stats)
        print("  Stats saved to Firebase: system/ai_manager_stats/")
    except Exception as e:
        print(f"  WARNING: failed to save stats to Firebase: {e}")

    # Alert if error rate > 10% AND at least 1 eligible controller (avoid false alert at scale 0)
    ALERT_THRESHOLD = 0.10
    ADMIN_EMAIL = 'smarthydro.il@gmail.com'
    if eligible > 0 and error_rate > ALERT_THRESHOLD:
        try:
            _send_admin_alert(ADMIN_EMAIL, full_stats)
            print(f"  ALERT EMAIL SENT to {ADMIN_EMAIL} — error rate {error_rate*100:.1f}%")
        except Exception as e:
            print(f"  ALERT EMAIL FAILED: {e}")


def _send_admin_alert(admin_email, stats):
    """Sends a plain-text alert email to the admin when run had too many errors."""
    subject = f"⚠️ SmartHydro AI Manager — error rate {stats['error_rate']*100:.1f}% on {stats['run_date']}"
    body = f"""SmartHydro AI Manager run finished with high error rate.

Run date: {stats['run_date']}
Run duration: {stats['run_duration_sec']}s

Counts:
  Total controllers seen:    {stats['total_controllers']}
  Eligible (passed gates):   {stats['eligible']}
  Reports sent OK:           {stats['sent_ok']}
  Gemini errors:             {stats['gemini_errors']}
  Email errors:              {stats['email_errors']}
  RTDB save errors:          {stats['rtdb_save_errors']}

Filtered out (normal):
  tier_blocked:    {stats['tier_blocked']}
  expired:         {stats['expired']}
  offline:         {stats['offline']}
  no_owner_email:  {stats['no_owner_email']}
  opted_out:       {stats['opted_out']}

Error rate: {stats['error_rate']*100:.2f}% (threshold: 10%)

Check GitHub Actions logs:
https://github.com/shimonYeshayahu/Hydro-OTA/actions

Or Firebase Console:
https://console.firebase.google.com/project/smarthydrosystem-e27fe/database/smarthydrosystem-e27fe-default-rtdb/data/~2Fsystem~2Fai_manager_stats
"""
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['From'] = SENDER_EMAIL
    msg['To'] = admin_email
    msg['Subject'] = subject

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(SENDER_EMAIL, SENDER_PASSWORD)
    server.send_message(msg)
    server.quit()


if __name__ == "__main__":
    process_all_controllers()
