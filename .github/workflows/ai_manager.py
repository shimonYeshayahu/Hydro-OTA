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


def build_consumption_chart_url(history_entries):
    """בונה URL ל-QuickChart.io עם גרף צריכה יומית של pH ו-EC (שניות).
    מקסימום 30 ימים אחרונים.
    """
    if not history_entries:
        return None
    recent = history_entries[-30:]
    labels = [d.strftime('%d/%m') for d, _ in recent]
    ph_data = [int(data.get('ph_sec_total', 0) or 0) for _, data in recent]
    ec_data = [int(data.get('ec_sec_total', 0) or 0) for _, data in recent]

    # מציגים גרף גם עם כל הערכים 0, כל עוד יש לפחות יום אחד של נתונים

    chart_config = {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {"label": "חומצה (pH) – שניות", "backgroundColor": "#3b82f6", "data": ph_data},
                {"label": "דשן (EC) – שניות", "backgroundColor": "#10b981", "data": ec_data}
            ]
        },
        "options": {
            "title": {"display": True, "text": "צריכה יומית במחזור הנוכחי", "fontSize": 16},
            "legend": {"position": "bottom"},
            "scales": {"yAxes": [{"ticks": {"beginAtZero": True}, "scaleLabel": {"display": True, "labelString": "שניות הפעלה"}}]}
        }
    }
    encoded = urllib.parse.quote(json.dumps(chart_config, ensure_ascii=False))
    return f"https://quickchart.io/chart?c={encoded}&w=600&h=350&bkg=white"

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

    # PWA v25: הנחיות שונות לפי סגנון הדוח שהמשתמש בחר
    if report_style == 'brief':
        style_instructions = """**סגנון: תקציר קצר ועניינו** (5-7 שורות, רק עיקרי + התראות).
1. כותרת ## עם סטטוס היום (תקין/חורג/לא יציב)
2. שורה אחת על pH, שורה אחת על EC, שורה אחת על טמפרטורה
3. **התראות בלבד** – לא תיאוריות. אם הכל תקין: "✓ הכל בטווח, אין מה לעשות".
4. **בלי המלצות מפורטות** – רק "מומלץ לבדוק X" אם יש בעיה אמיתית.
5. עד 7 שורות סה"כ. בלי טבלאות, בלי גרפים."""
    elif report_style == 'detailed':
        style_instructions = """**סגנון: דוח מפורט** (טבלאות, ניתוח מגמות, גרפים).
1. כותרת ## עם סטטוס היום
2. **טבלה** של 3 העמודות (pH, EC, Temp) - היום, אתמול, ממוצע מחזור, יעד
3. ניתוח מגמות מצטבר - יציבות לעומת אתמול, מגמה לאורך המחזור
4. הסבר הסיבות לחריגות (אם יש)
5. סיכום צריכת חומרים + צפי לסוף השבוע
6. המלצות מפורטות עם נימוקים"""
    else:  # agronomist
        style_instructions = """**סגנון: אגרונום AI מקצועי** – לקוחות PRO+, המלצות פעולה ותובנות.
1. **דיאגנוזה אגרונומית** של מצב הצמחים לפי כל הנתונים יחד
2. **תובנות מיקרו** – צירופי תופעות שמשתמש רגיל לא יזהה (לדוגמה: "EC יורד עם עליית טמפ' = שורשים פעילים, צריכת מים מואצת")
3. **המלצות פעולה ספציפיות לשבוע הקרוב** – לא כללי, אלא: "ביום שלישי בערב הוסף 50ml דשן" / "צמצם תאורה ב-2 שעות"
4. **תחזית בעיות פוטנציאליות** ב-3-5 ימים הקרובים בהתבסס על מגמות
5. **כיוון לאופטימיזציה** – איך לשפר עוד 10-15% תפוקה
6. שפה מקצועית אגרונומית, בלי "אולי" ו"כדאי" – אמירה ברורה."""

    prompt = f"""אתה אגרונום מומחה למערכות הידרופוניקה. הפק דוח לגינה הבאה.

=== עיגון תאריך (חובה לדבוק בו!) ===
התאריך היום: {today_dmy} (יום {today_weekday_he}, {today_iso})
זה היום עבורו מופק הדוח. אל תכתוב תאריך אחר. אל תמציא תאריכים.
כל אזכור של "היום" או "כעת" חייב להתייחס ל-{today_dmy}.

=== נתוני המחזור ===
מחזור גידול #{cycle_count}, החל ב-{cycle_str}, יום {days_into_cycle} למחזור.
(אל תחשב מחדש את מספר הימים — השתמש בערך {days_into_cycle} שניתן לך כאן.)
{plants_section}
יעדי הגידול:
- טמפרטורה: {targets.get('temp_min')}–{targets.get('temp_max')} °C
- pH: {targets.get('ph_min')}–{targets.get('ph_max')}
- EC: {targets.get('ec_min')}–{targets.get('ec_max')} µS

{today_summary}
{consumption_text}
היסטוריה יומית מאז תחילת המחזור:
{format_history_for_prompt(history_entries)}

{style_instructions}

=== הנחיות כלליות ===
- **התחל ישר במהות** – בלי "שלום למגדל היקר".
- **אל תזכיר MAC address**.
- **תייחס לצמחים** אם הוגדרו.
- אם רוב הקריאות 0 או חסרות (מחזור צעיר <3 ימים) – ציין שזה תקין ולא תקלה.
- **התאריך היום הוא {today_dmy}** — אל תכתוב תאריך אחר בכותרת או בגוף.
- **מספר הימים במחזור הוא {days_into_cycle}** — אל תחשב מחדש.

החזר אך ורק את תוכן הדוח בפורמט Markdown בעברית. השתמש בכותרות ## ובהדגשות **.
**טבלאות**: השתמש בפורמט Markdown סטנדרטי עם |---| בין הכותרת לשורות."""

    response = _gemini_generate_with_retry(prompt)
    return response.text


# V25.24: Retry transient Gemini errors (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED, etc.)
# Without this, the daily cron at 06:00 UTC fails silently when Google has a load spike,
# and customers lose their daily report for that day with no explanation.
def _gemini_generate_with_retry(prompt, model='gemini-2.5-flash', max_attempts=3):
    """Call Gemini with exponential backoff on transient errors.
    Delays: 5s -> 15s -> 30s. Total worst case: ~50s extra.
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
                # Permanent error OR ran out of attempts — give up
                print(f"  Gemini call FAILED (attempt {attempt+1}/{max_attempts}): {err_str[:200]}")
                raise
            delay = delays[attempt]
            print(f"  Gemini transient error (attempt {attempt+1}/{max_attempts}), "
                  f"retrying in {delay}s: {err_str[:120]}")
            time.sleep(delay)
    # Should never reach here but for safety:
    raise last_exc if last_exc else RuntimeError("Gemini retry loop exited unexpectedly")


def send_report_email(client_email, controller_id, report_md, cycle_count, days_into_cycle, chart_url=None, plants_text=None, device_name=None):
    # V25.24: extensions=['tables','nl2br'] — renders Markdown pipe-tables as real <table>
    # and converts single newlines to <br> for better paragraph spacing.
    html_body = markdown.markdown(report_md, extensions=['tables', 'nl2br'])
    friendly_name = device_name if device_name else "SmartHydro"
    # V25.25: email-client-safe chart row (table-based, not div)
    chart_html = ""
    if chart_url:
        chart_html = f"""
        <tr>
          <td style="padding:24px 20px;background:#fafafa;border-top:1px solid #e5e7eb;text-align:center;">
            <div style="font-size:14px;font-weight:bold;color:#374151;margin-bottom:12px;">📊 גרף צריכת חומרים – כל יום במחזור</div>
            <img src="{chart_url}" alt="צריכת חומרים יומית" style="max-width:100%;height:auto;border:1px solid #e5e7eb;border-radius:8px;display:block;margin:0 auto;">
            <div style="font-size:11px;color:#6b7280;margin-top:10px;line-height:1.5;">כל עמודה = יום אחד במחזור. שניות הפעלה של משאבת חומצה (כחול) ודשן (ירוק).</div>
          </td>
        </tr>"""
    else:
        chart_html = """
        <tr>
          <td style="padding:16px;background:#fafafa;border-top:1px solid #e5e7eb;text-align:center;color:#9ca3af;font-size:12px;">
            📊 גרף הצריכה יופיע כשיצטברו נתונים יומיים (החל מהיום השני במחזור).
          </td>
        </tr>"""

    # V25.25: plants_html wrapped as table row for email-safe layout
    plants_html_row = ""
    if plants_text:
        plants_html_row = f"""
        <tr>
          <td style="background:#f0fdf4;padding:12px 20px;border-bottom:1px solid #bbf7d0;text-align:right;font-size:13px;">
            <span style="color:#15803d;font-weight:bold;">🌱 גידול נוכחי:</span>
            <span style="color:#166534;"> {plants_text}</span>
          </td>
        </tr>"""

    # V25.26: Email-client CSS. Inner tables MUST use table-layout:fixed +
    # width:100% so long text doesn't push them past 584px (640 - 56 padding).
    body_styles = """
    <style>
      /* Gmail/web client typography */
      .report-content { font-family: 'Heebo','Segoe UI',Arial,sans-serif; color:#1f2937; font-size:15px; line-height:1.85; max-width:584px; }
      .report-content h2 { color:#6d28d9; font-size:18px; font-weight:700; margin:24px 0 10px 0; padding-bottom:6px; border-bottom:2px solid #ede9fe; }
      .report-content h3 { color:#374151; font-size:15px; font-weight:700; margin:18px 0 8px 0; }
      .report-content p { margin:10px 0; max-width:584px; word-wrap:break-word; }
      .report-content strong { color:#5b21b6; font-weight:700; }
      .report-content em { color:#6b7280; font-style:normal; font-size:13px; }
      /* Inner tables from Markdown — constrain to content width */
      .report-content table { border-collapse:collapse; width:100% !important; max-width:584px; margin:14px 0; background:#fafafa; border-radius:8px; table-layout:fixed; }
      .report-content th { background:#ede9fe; color:#5b21b6; padding:10px 8px; text-align:right; font-weight:700; font-size:13px; border-bottom:2px solid #ddd6fe; word-wrap:break-word; }
      .report-content td { padding:10px 8px; text-align:right; font-size:13px; border-bottom:1px solid #e5e7eb; word-wrap:break-word; vertical-align:top; }
      .report-content tr:last-child td { border-bottom:none; }
      /* Recommendation cards */
      .report-content ol, .report-content ul { padding-right:0; padding-left:0; margin:14px 0; list-style:none; counter-reset:rec-counter; max-width:584px; }
      .report-content ol li, .report-content ul li {
        background:#f9fafb; border-right:3px solid #8b5cf6; border-radius:6px;
        padding:12px 16px; margin:8px 0; font-size:14px; line-height:1.7; position:relative; max-width:584px; word-wrap:break-word;
      }
      .report-content ol li { counter-increment:rec-counter; padding-right:50px; }
      .report-content ol li:before {
        content:counter(rec-counter); position:absolute; right:14px; top:12px;
        background:#8b5cf6; color:#fff; width:24px; height:24px; border-radius:50%;
        text-align:center; line-height:24px; font-size:13px; font-weight:700;
      }
      .report-content hr { border:none; border-top:1px solid #e5e7eb; margin:18px 0; }
      .report-content code { background:#f3f4f6; color:#5b21b6; padding:1px 6px; border-radius:4px; font-family:monospace; font-size:13px; }
      /* Mobile: shrink container + content */
      @media only screen and (max-width:600px) {
        .report-content { font-size:14px; max-width:100%; }
        .report-content td, .report-content th { padding:6px 4px; font-size:12px; }
        .report-content ol li, .report-content ul li { padding:10px 12px; }
        .report-content ol li { padding-right:42px; }
      }
    </style>"""

    # V25.26: Outlook-strict layout. Three-layer defense:
    # (1) MSO conditional table forces Outlook to render at fixed 640px
    # (2) Modern clients use a <div> with max-width:640px (Outlook ignores divs in MSO)
    # (3) Inner content table has NO width:100% — only width="640" attribute + style width:640px
    # CRITICAL: never set width:100% on the container table. It overrides max-width in Outlook.
    html_template = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>דוח אגרונומי</title>
{body_styles}
<!--[if mso]>
<style type="text/css">
  table {{ border-collapse:collapse; }}
  .report-content {{ font-family: Arial, sans-serif !important; }}
</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:'Heebo','Segoe UI',Arial,sans-serif;direction:rtl;">
  <!-- Outer wrapper: full window width with background -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f3f4f6" style="background-color:#f3f4f6;">
    <tr>
      <td align="center" valign="top" style="padding:24px 12px;">

        <!--[if mso]>
        <table role="presentation" align="center" width="640" cellpadding="0" cellspacing="0" border="0">
        <tr><td align="center" valign="top" width="640" style="width:640px;">
        <![endif]-->

        <!--[if !mso]><!-- -->
        <div style="max-width:640px;margin:0 auto;">
        <!--<![endif]-->

          <!-- Container: fixed 640px in Outlook (HTML attribute), max-width via parent div elsewhere -->
          <table role="presentation" align="center" width="640" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="width:640px;max-width:640px;background-color:#ffffff;border-radius:14px;">

            <!-- Header -->
            <tr>
              <td bgcolor="#6d28d9" style="background-color:#6d28d9;background-image:linear-gradient(135deg,#8b5cf6,#6d28d9);color:#ffffff;padding:26px 24px;text-align:center;border-radius:14px 14px 0 0;">
                <div style="font-size:22px;font-weight:700;margin-bottom:6px;color:#ffffff;">דוח אגרונומי יומי</div>
                <div style="font-size:13px;color:#ffffff;opacity:0.92;">{friendly_name} &middot; מחזור #{cycle_count} &middot; יום {days_into_cycle}</div>
              </td>
            </tr>

            {plants_html_row}

            <!-- Content cell: explicit width 640 minus padding = 584 content area -->
            <tr>
              <td class="report-content" width="640" style="width:640px;padding:28px 28px 24px 28px;font-family:'Heebo','Segoe UI',Arial,sans-serif;color:#1f2937;font-size:15px;line-height:1.85;text-align:right;direction:rtl;word-break:break-word;">
                {html_body}
              </td>
            </tr>

            {chart_html}

            <!-- Footer -->
            <tr>
              <td bgcolor="#f8fafc" style="background-color:#f8fafc;padding:14px;text-align:center;font-size:11px;color:#6b7280;border-radius:0 0 14px 14px;">
                &copy; 2026 SmartHydro Systems
              </td>
            </tr>
          </table>

        <!--[if !mso]><!-- -->
        </div>
        <!--<![endif]-->

        <!--[if mso]>
        </td></tr>
        </table>
        <![endif]-->

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
            chart_url = build_consumption_chart_url(history)
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
