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


def generate_report(controller_id, settings, history_entries, cycle_start_dt, cycle_count, device_name=None):
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

    today_summary = ""
    if history_entries:
        d, data = history_entries[-1]
        today_summary = (f"היום ({d.strftime('%d/%m')}): pH={data.get('ph_avg', 0):.2f}, "
                         f"EC={data.get('ec_avg', 0):.0f}, Temp={data.get('temp_avg', 0):.1f}°C")

    # V13.27+: מידע על הצמחים שגדלים – לדוח מותאם אישית
    plants_list = settings.get('plants', []) if isinstance(settings, dict) else []
    plants_text = format_plants_for_prompt(plants_list)
    plants_section = f"\nצמחים בגידול נוכחי: {plants_text}\n" if plants_text else "\n(לא הוגדרו צמחים ספציפיים בהגדרות.)\n"

    # סיכום צריכת חומרים מצטברת
    total_ph_sec = sum(int(d.get('ph_sec_total', 0) or 0) for _, d in history_entries)
    total_ec_sec = sum(int(d.get('ec_sec_total', 0) or 0) for _, d in history_entries)
    consumption_text = f"\nצריכת חומרים מצטברת במחזור: חומצה pH – {total_ph_sec} שניות, דשן EC – {total_ec_sec} שניות.\n"

    prompt = f"""אתה אגרונום מומחה למערכות הידרופוניקה. הפק דוח יומי השוואתי לגינה הבאה:

מחזור גידול נוכחי #{cycle_count}, החל ב-{cycle_str} (יום {days_into_cycle} למחזור).
{plants_section}
יעדי הגידול:
- טמפרטורה: {targets.get('temp_min')}–{targets.get('temp_max')} °C
- pH: {targets.get('ph_min')}–{targets.get('ph_max')}
- EC: {targets.get('ec_min')}–{targets.get('ec_max')} µS

{today_summary}
{consumption_text}
היסטוריה יומית מאז תחילת המחזור:
{format_history_for_prompt(history_entries)}

הנחיות לדוח:
1. **התחל ישר במהות** – אל תפתח במשפט ברכה כמו "שלום למגדל/ת היקר/ה". פתח בכותרת ## או בהצהרת מצב.
2. **אל תזכיר את מזהה הבקר (MAC address)** – זה מספר טכני, לא שייך לדוח.
3. **תייחס במפורש לצמחים שהמשתמש מגדל** (אם הוגדרו) – האם היעדים והקריאות מתאימים להם? מה הם דורשים בשלב זה?
4. סיכום היום (איך עבר היום ביחס ליעדים).
5. השוואה למחזור עד כה: מגמות, יציבות, חריגות.
6. ציון נקודות מפנה אם קיימות.
7. **המלצות פרקטיות ספציפיות לסוג הגידול** (לא המלצות כלליות).
8. אם הצמחים בשלב פריחה/פרי – שים דגש על EC ושעות תאורה.
9. אם הצמחים הם עלים – שים דגש על pH ויציבות.
10. אם רוב הקריאות 0 או חסרות נתונים – ציין שזה מצב תקין במחזור צעיר (פחות מ-2-3 ימים) ולא תקלה.

החזר אך ורק את תוכן הדוח בפורמט Markdown בעברית. השתמש בכותרות ## ובהדגשות **. בלי משפטי פתיחה מנומסים."""

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    return response.text


def send_report_email(client_email, controller_id, report_md, cycle_count, days_into_cycle, chart_url=None, plants_text=None, device_name=None):
    html_body = markdown.markdown(report_md)
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
    html_template = f"""
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; direction: rtl; text-align: right; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 10px; overflow: hidden;">
        <div style="background: #8b5cf6; color: white; padding: 20px; text-align: center;">
            <h1 style="margin: 0;">דוח אגרונומי יומי</h1>
            <p style="margin: 5px 0 0 0;">{friendly_name} | מחזור #{cycle_count} | יום {days_into_cycle}</p>
        </div>
        {plants_html}
        <div style="padding: 20px;">{html_body}</div>
        {chart_html}
        <div style="background: #f8fafc; padding: 10px; text-align: center; font-size: 12px;">
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
            device_name = settings.get('deviceName') or None
            report_md = generate_report(controller_id, settings, history, cycle_start_dt, cycle_count, device_name=device_name)
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
