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

    # מחיקת נתונים ישנים (מעל 7 ימים) כדי לא להעמיס על מסד הנתונים
    now_ts = datetime.now().timestamp()
    retention_days = 7
    expiration_threshold = now_ts - (retention_days * 24 * 60 * 60)

    for controller_id, data in all_controllers.items():
        print(f"\n[{controller_id}] -----------------------------------")

        # --- 1. שליפת היסטוריית החיישנים (מהתיקייה החדשה!) ---
        history_data = data.get('telemetry_history', {})
        if history_data:
            print(f"[{controller_id}] Checking for old history records to clean...")
            deleted_count = 0

            for key, entry in history_data.items():
                # שימוש ב-'time' בדיוק כמו שהבקר שולח
                if entry.get('time', 0) < expiration_threshold:
                    db.reference(f'controllers/{controller_id}/telemetry_history/{key}').delete()
                    deleted_count += 1

            if deleted_count > 0:
                print(f"[{controller_id}] Cleaned up {deleted_count} old records (older than 7 days).")
            else:
                print(f"[{controller_id}] No old records to clean.")

        # --- 2. הפקת דוח AI ---
        settings = data.get('settings', {})

        if not settings.get('ai_optin', False):
            print(f"[{controller_id}] Skipped: AI reports disabled.")
            continue

        client_email = settings.get('ai_email', '')
        if not client_email:
            print(f"[{controller_id}] Error: AI enabled, but no email address provided!")
            continue

        print(f"[{controller_id}] Generating report for {client_email}...")

        style = settings.get('ai_style', 'professional')

        history_text = ""
        if history_data:
            # מיון לפי שדה 'time'
            sorted_history = sorted(history_data.values(), key=lambda x: x.get('time', 0))
            # לוקחים את 336 הרשומות האחרונות (שבוע שלם של דגימות כל חצי שעה)
            recent_history = sorted_history[-336:] 
            for item in recent_history:
                dt_object = datetime.fromtimestamp(item.get('time', 0))
                time_str = dt_object.strftime('%d/%m %H:%M')
                temp = item.get('temp', 0)
                ph = item.get('pH', 0)
                ec = item.get('EC', 0)
                history_text += f"[{time_str}] Temp: {temp:.1f}, pH: {ph:.2f}, EC: {ec:.0f}\n"
        else:
            history_text = "אין עדיין מספיק נתונים היסטוריים השבוע. המערכת החלה באיסוף נתונים."

        prompt = f"""
            אתה אגרונום מומחה למערכות הידרופוניקה. עליך להפיק דוח סטטוס שבועי עבור בקר {controller_id}.
            סגנון הכתיבה המבוקש: {'מקצועי, מדעי ואנליטי' if style == 'professional' else 'קליל, ידידותי, ובגובה העיניים למגדל הביתי'}.

            יעדי הגידול (מוגדרים במערכת):
            - טמפרטורה: {settings.get('temp_min')} - {settings.get('temp_max')} °C
            - חומציות (pH): {settings.get('ph_min')} - {settings.get('ph_max')}
            - מוליכות (EC): {settings.get('ec_min')} - {settings.get('ec_max')} uS

            היסטוריית מדדים (דגימה כל חצי שעה מהשבוע האחרון):
            {history_text}

            נתח את הנתונים מהשבוע האחרון. 
            1. האם המדדים יציבים ובטווח? 
            2. האם יש חריגות או מגמות שמצריכות התערבות? 
            3. סכם במספר המלצות פרקטיות להמשך הגידול.
            """

        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            print(f"[{controller_id}] Report generated! Formatting HTML and sending email to {client_email}...")

            html_body = markdown.markdown(response.text)

            html_template = f"""
                <!DOCTYPE html>
                <html lang="he" dir="rtl">
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; color: #333333; margin: 0; padding: 20px; }}
                        .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
                        .header {{ background-color: #8b5cf6; color: #ffffff; padding: 20px; text-align: center; }}
                        .header h1 {{ margin: 0; font-size: 24px; }}
                        .content {{ padding: 30px; line-height: 1.6; font-size: 16px; }}
                        .content h2, .content h3 {{ color: #8b5cf6; }}
                        .content strong {{ color: #1e293b; }}
                        .footer {{ background-color: #f8fafc; text-align: center; padding: 15px; font-size: 12px; color: #64748b; border-top: 1px solid #e2e8f0; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <img src="https://raw.githubusercontent.com/shimonYeshayahu/Hydro-OTA/76c988554de2585c9236f2450fb0aa55985b0a1d/logo_shimon.png" alt="לוגו המערכת" style="max-height: 70px; margin-bottom: 15px;">
                            <h1>דוח אגרונומי חכם</h1>
                            <p style="margin: 5px 0 0 0; font-size: 14px;">מערכת בקרה הידרופונית | בקר {controller_id}</p>
                        </div>
                        <div class="content">
                            {html_body}
                        </div>
                        <div class="footer">
                            דוח זה הופק אוטומטית על ידי מנוע הבינה המלאכותית של מערכת סמארט הידרו.<br>
                            &copy; 2026 כל הזכויות שמורות לשמעון ישעיהו (Shimon Yeshayahu)
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
            print(f"[{controller_id}] Failed to generate or send report: {e}")

        print(f"[{controller_id}] Finished processing.")

if __name__ == "__main__":
    process_all_controllers()
