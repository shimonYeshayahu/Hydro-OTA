import os
import json
import firebase_admin
from firebase_admin import credentials, db
from google import genai
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import markdown

# משתני סביבה (Environment Variables)
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

def process_all_controllers():
    print("Fetching all controllers from Firebase...")
    controllers_ref = db.reference('controllers')
    all_controllers = controllers_ref.get()

    if not all_controllers:
        print("No controllers found in database.")
        return

    now_ts = datetime.now().timestamp()

    for controller_id, data in all_controllers.items():
        print(f"\n[{controller_id}] -----------------------------------")

        # --- 1. שליפת היסטוריית החיישנים מ-weekly_logs (גרסה 12.2) ---
        weekly_logs = data.get('weekly_logs', {})
        all_history_entries = []
        
        if weekly_logs:
            print(f"[{controller_id}] Consolidating weekly logs...")
            # איחוד כל הימים (day_0 עד day_6) לרשימה אחת
            for day_key, day_data in weekly_logs.items():
                if isinstance(day_data, dict):
                    all_history_entries.extend(day_data.values())
            
            # מיון לפי זמן
            all_history_entries = sorted(all_history_entries, key=lambda x: x.get('time', 0))
        
        # --- 2. בדיקת דופק (Pulse Check) ---
        latest_timestamp = 0
        if all_history_entries:
            latest_timestamp = all_history_entries[-1].get('time', 0)

        # אם הדגימה האחרונה זקנה יותר מ-24 שעות - הבקר לא פעיל
        if now_ts - latest_timestamp > (24 * 60 * 60):
            print(f"[{controller_id}] Skipped: Controller is offline (No data in last 24h).")
            continue

        # --- 3. הפקת דוח AI ---
        settings = data.get('settings', {})
        if not settings.get('ai_optin', False):
            print(f"[{controller_id}] Skipped: AI reports disabled.")
            continue

        client_email = settings.get('ai_email', '')
        if not client_email:
            print(f"[{controller_id}] Error: AI enabled, but no email provided!")
            continue

        print(f"[{controller_id}] Generating report for {client_email}...")
        style = settings.get('ai_style', 'professional')

        history_text = ""
        if all_history_entries:
            # לוקחים את 336 הרשומות האחרונות (שבוע שלם של דגימות כל 10-30 דקות)
            recent_history = all_history_entries[-336:]
            for item in recent_history:
                dt_object = datetime.fromtimestamp(item.get('time', 0))
                time_str = dt_object.strftime('%d/%m %H:%M')
                temp = item.get('temp', 0)
                ph = item.get('ph_val', item.get('pH', 0)) # תומך בשמות השדות החדשים והישנים
                ec = item.get('ec_val', item.get('EC', 0))
                history_text += f"[{time_str}] Temp: {temp:.1f}, pH: {ph:.2f}, EC: {ec:.0f}\n"
        else:
            history_text = "אין עדיין מספיק נתונים היסטוריים ב-weekly_logs."

        prompt = f"""
                    אתה אגרונום מומחה למערכות הידרופוניקה. עליך להפיק דוח סטטוס מקיף עבור בקר {controller_id}.
                    סגנון הכתיבה המבוקש: {'מקצועי, מדעי ואנליטי' if style == 'professional' else 'קליל, ידידותי, ובגובה העיניים למגדל הביתי'}.

                    יעדי הגידול (מוגדרים במערכת):
                    - טמפרטורה: {settings.get('temp_min')} - {settings.get('temp_max')} °C
                    - חומציות (pH): {settings.get('ph_min')} - {settings.get('ph_max')}
                    - מוליכות (EC): {settings.get('ec_min')} - {settings.get('ec_max')} uS

                    היסטוריית מדדים (מתוך weekly_logs):
                    {history_text}

                    הנחיות לניתוח:
                    1. נתח את היציבות של המערכת לאורך השבוע האחרון.
                    2. ציין חריגות מהיעדים אם היו.
                    3. ספק המלצות פרקטיות לתחזוקה.

                    החזר אך ורק את תוכן הדוח בפורמט Markdown.
                    """

        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            html_body = markdown.markdown(response.text)

            html_template = f"""
                <!DOCTYPE html>
                <html lang="he" dir="rtl">
                <head><meta charset="UTF-8"></head>
                <body style="font-family: sans-serif; direction: rtl; text-align: right; padding: 20px;">
                    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 10px; overflow: hidden;">
                        <div style="background: #8b5cf6; color: white; padding: 20px; text-align: center;">
                            <h1>דוח אגרונומי חכם (v12.2)</h1>
                            <p>בקר: {controller_id}</p>
                        </div>
                        <div style="padding: 20px;">{html_body}</div>
                        <div style="background: #f8fafc; padding: 10px; text-align: center; font-size: 12px;">
                            &copy; 2026 SmartHydro System
                        </div>
                    </div>
                </body>
                </html>
                """

            msg = MIMEMultipart('alternative')
            msg['From'] = SENDER_EMAIL
            msg['To'] = client_email
            msg['Subject'] = f"דוח אגרונומי חכם - בקר {controller_id}"
            msg.attach(MIMEText(html_template, 'html', 'utf-8'))

            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
            server.quit()
            print(f"[{controller_id}] Email sent successfully!")

        except Exception as e:
            print(f"[{controller_id}] Error: {e}")

if __name__ == "__main__":
    process_all_controllers()