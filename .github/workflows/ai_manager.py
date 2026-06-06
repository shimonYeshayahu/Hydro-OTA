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
    plants_html = ""
    if plants_text:
        plants_html = f"""
        <div style="background: #f0fdf4; padding: 10px 15px; margin: 0; border-bottom: 1px solid #bbf7d0; text-align: right;">
            <span style="color: #15803d; font-weight: bold;">🌱 גידול נוכחי:</span>
            <span style="color: #166534;"> {plants_text}</span>
        </div>"""
    chart_html = ""
    if chart_url:
        chart_html = f"""
        <div style="padding: 20px; background: #fafafa; border-top: 1px solid #eee; text-align: center;">
            <h3 style="margin: 0 0 10px 0; color: #374151;">📊 גרף צריכת חומרים – כל יום במחזור</h3>
            <img src="{chart_url}" alt="צריכת חומרים יומית" style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">
            <p style="font-size: 11px; color: #6b7280; margin-top: 8px;">כל עמודה = יום אחד במחזור. שניות הפעלה של משאבת חומצה (כחול) ודשן (ירוק).</p>
        </div>"""
    else:
        chart_html = """
        <div style="padding: 15px; background: #fafafa; border-top: 1px solid #eee; text-align: center; color: #9ca3af; font-size: 12px;">
            📊 גרף הצריכה יופיע כשיצטברו נתונים יומיים (החל מהיום השני במחזור).
        </div>"""
    # V25.24: inline CSS for clean tables + readable text inside the body
    body_styles = """
    <style>
        .report-body { font-family: 'Heebo', sans-serif; color: #1f2937; line-height: 1.6; font-size: 14px; }
        .report-body h2 { color: #6d28d9; border-bottom: 2px solid #ede9fe; padding-bottom: 6px; margin-top: 20px; font-size: 18px; }
        .report-body h3 { color: #374151; margin-top: 16px; font-size: 15px; }
        .report-body p { margin: 8px 0; }
        .report-body strong { color: #6d28d9; }
        .report-body table { border-collapse: collapse; width: 100%; margin: 12px 0; background: #fafafa; border-radius: 8px; overflow: hidden; }
        .report-body th { background: #ede9fe; color: #5b21b6; padding: 10px; text-align: right; font-weight: bold; border-bottom: 2px solid #ddd6fe; }
        .report-body td { padding: 10px; text-align: right; border-bottom: 1px solid #e5e7eb; }
        .report-body tr:last-child td { border-bottom: none; }
        .report-body ul, .report-body ol { padding-right: 22px; padding-left: 0; }
        .report-body li { margin: 6px 0; }
        .report-body hr { border: none; border-top: 1px solid #e5e7eb; margin: 16px 0; }
    </style>"""
    html_template = f"""
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="UTF-8">{body_styles}</head>
<body style="font-family: 'Heebo', sans-serif; direction: rtl; text-align: right; padding: 20px; background: #f3f4f6;">
    <div style="max-width: 640px; margin: 0 auto; background: white; border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden;">
        <div style="background: linear-gradient(135deg, #8b5cf6, #6d28d9); color: white; padding: 22px; text-align: center;">
            <h1 style="margin: 0; font-size: 22px;">דוח אגרונומי יומי</h1>
            <p style="margin: 8px 0 0 0; opacity: 0.92; font-size: 13px;">{friendly_name} · מחזור #{cycle_count} · יום {days_into_cycle}</p>
        </div>
        {plants_html}
        <div class="report-body" style="padding: 22px;">{html_body}</div>
        {chart_html}
        <div style="background: #f8fafc; padding: 12px; text-align: center; font-size: 11px; color: #6b7280;">
            &copy; 2026 SmartHydro Systems
        </div>
    </div>
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

    for controller_id, data in all_controllers.items():
        print(f"\n[{controller_id}] -----------------------------------")
        if not isinstance(data, dict):
            continue

        # 1. סינון לפי tier (subscription/tier; ברירת מחדל = free)
        sub = data.get('subscription', {}) if isinstance(data.get('subscription'), dict) else {}
        tier = sub.get('tier', 'free')
        if tier not in REPORT_TIERS:
            print(f"[{controller_id}] Skipped: tier={tier}")
            continue
        # תוקף פג? עדיין נשלח דוח אם זה הוא יום התפוגה (לקוח אמור לקבל אישור עד הרגע האחרון)
        expires_at = sub.get('expiresAt', 0)
        if expires_at and expires_at < now_ts:
            print(f"[{controller_id}] Skipped: subscription expired")
            continue

        # 2. בדיקת דופק (realtime/lastUpdate בתוך 24h אחרונות)
        realtime = data.get('realtime', {}) if isinstance(data.get('realtime'), dict) else {}
        last_update = realtime.get('lastUpdate', 0) or 0
        if now_ts - last_update > 86400:
            print(f"[{controller_id}] Skipped: offline (last_update={last_update})")
            continue

        # 3. owner email
        owner_email = data.get('owner_email') or ''
        if not owner_email:
            print(f"[{controller_id}] Skipped: no owner_email")
            continue

        # PWA v25: בדיקת email_prefs של המשתמש
        email_prefs = get_email_prefs_by_email(owner_email, all_users=all_users)
        should_send, reason = should_send_today(email_prefs)
        if not should_send:
            print(f"[{controller_id}] Skipped email (per user prefs): {reason}")
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

        # 8. שליחת email – עם גרף צריכה ומידע על צמחים
        try:
            days_into_cycle = (datetime.now().date() - cycle_start_dt.date()).days if cycle_start_dt else 0
            chart_url = build_consumption_chart_url(history)
            plants_text = format_plants_for_prompt(settings.get('plants', []) if isinstance(settings, dict) else [])
            send_report_email(owner_email, controller_id, report_md, cycle_count, days_into_cycle,
                              chart_url=chart_url, plants_text=plants_text, device_name=device_name)
            print(f"[{controller_id}] Report sent to {owner_email} (chart={'yes' if chart_url else 'no'})")
        except Exception as e_email:
            print(f"[{controller_id}] Email error: {e_email}")


if __name__ == "__main__":
    process_all_controllers()
