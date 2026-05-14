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
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import firebase_admin
from firebase_admin import credentials, db
from google import genai
import markdown

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


def generate_report(controller_id, settings, history_entries, cycle_start_dt, cycle_count):
    style_pref = settings.get('ai_style', 'professional') if isinstance(settings, dict) else 'professional'
    style_text = ('מקצועי, מדעי ואנליטי' if style_pref == 'professional'
                  else 'קליל, ידידותי, ובגובה העיניים למגדל הביתי')

    targets = {
        'temp_min': settings.get('temp_min', '?'), 'temp_max': settings.get('temp_max', '?'),
        'ph_min': settings.get('ph_min', '?'), 'ph_max': settings.get('ph_max', '?'),
        'ec_min': settings.get('ec_min', '?'), 'ec_max': settings.get('ec_max', '?'),
    } if isinstance(settings, dict) else {}

    cycle_str = cycle_start_dt.strftime('%d/%m/%Y') if cycle_start_dt else "לא הוגדר"
    days_into_cycle = (datetime.now().date() - cycle_start_dt.date()).days if cycle_start_dt else "?"

    today_summary = ""
    if history_entries:
        d, data = history_entries[-1]
        today_summary = (f"היום ({d.strftime('%d/%m')}): pH={data.get('ph_avg', 0):.2f}, "
                         f"EC={data.get('ec_avg', 0):.0f}, Temp={data.get('temp_avg', 0):.1f}°C")

    prompt = f"""אתה אגרונום מומחה למערכות הידרופוניקה. הפק דוח יומי השוואתי עבור בקר {controller_id}.
סגנון: {style_text}.

מחזור גידול נוכחי #{cycle_count}, החל ב-{cycle_str} (יום {days_into_cycle} למחזור).

יעדי הגידול:
- טמפרטורה: {targets.get('temp_min')}–{targets.get('temp_max')} °C
- pH: {targets.get('ph_min')}–{targets.get('ph_max')}
- EC: {targets.get('ec_min')}–{targets.get('ec_max')} µS

{today_summary}

היסטוריה יומית מאז תחילת המחזור:
{format_history_for_prompt(history_entries)}

הנחיות לדוח:
1. סיכום היום (איך עבר היום ביחס ליעדים).
2. השוואה למחזור עד כה: מגמות, יציבות, חריגות.
3. ציון נקודות מפנה אם קיימות (יום שבו משהו השתנה משמעותית).
4. המלצות פרקטיות להמשך המחזור.

החזר אך ורק את תוכן הדוח בפורמט Markdown בעברית."""

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    return response.text


def send_report_email(client_email, controller_id, report_md, cycle_count, days_into_cycle):
    html_body = markdown.markdown(report_md)
    html_template = f"""
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; direction: rtl; text-align: right; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 10px; overflow: hidden;">
        <div style="background: #8b5cf6; color: white; padding: 20px; text-align: center;">
            <h1>דוח אגרונומי יומי</h1>
            <p>בקר: {controller_id} | מחזור #{cycle_count} | יום {days_into_cycle}</p>
        </div>
        <div style="padding: 20px;">{html_body}</div>
        <div style="background: #f8fafc; padding: 10px; text-align: center; font-size: 12px;">
            &copy; 2026 SmartHydro Systems
        </div>
    </div>
</body>
</html>"""
    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = client_email
    msg['Subject'] = f"דוח אגרונומי יומי - בקר {controller_id}"
    msg.attach(MIMEText(html_template, 'html', 'utf-8'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(SENDER_EMAIL, SENDER_PASSWORD)
    server.send_message(msg)
    server.quit()


def process_all_controllers():
    print("Fetching all controllers from Firebase...")
    all_controllers = db.reference('controllers').get() or {}
    if not all_controllers:
        print("No controllers found.")
        return

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
            report_md = generate_report(controller_id, settings, history, cycle_start_dt, cycle_count)
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

        # 8. שליחת email
        try:
            days_into_cycle = (datetime.now().date() - cycle_start_dt.date()).days if cycle_start_dt else 0
            send_report_email(owner_email, controller_id, report_md, cycle_count, days_into_cycle)
            print(f"[{controller_id}] Report sent to {owner_email}")
        except Exception as e_email:
            print(f"[{controller_id}] Email error: {e_email}")


if __name__ == "__main__":
    process_all_controllers()
