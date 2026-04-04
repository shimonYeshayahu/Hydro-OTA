import os
import json
import firebase_admin
from firebase_admin import credentials, db
from google import genai
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import markdown # הספרייה החדשה שממירה את הטקסט לעיצוב נקי!

# קריאת סודות מהסביבה (Environment Variables)
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

    for controller_id, data in all_controllers.items():
        print(f"\n[{controller_id}] -----------------------------------")
        settings = data.get('settings', {})

        if not settings.get('ai_optin', False):
            print(f"[{controller_id}] Skipped: AI reports disabled.")
            continue

        client_email = settings.get('ai_email', '')
        if not client_email:
            print(f"[{controller_id}] Error: AI enabled, but no email address provided!")
            continue

        print(f"[{controller_id}] Generating report for {client_email}...")

        telemetry = data.get('telemetry', {})
        targets = data.get('targets', {})
        faults = data.get('faults', {})
        history_data = data.get('history', {})
        style = settings.get('ai_style', 'professional')

        history_text = ""
        if history_data:
            sorted_history = sorted(history_data.values(), key=lambda x: x.get('timestamp', 0))
            recent_history = sorted_history[-144:]
            for item in recent_history:
                dt_object = datetime.fromtimestamp(item.get('timestamp', 0))
                time_str = dt_object.strftime('%H:%M')
                temp = item.get('temperature', 0)
                ph = item.get('pH', 0)
                ec = item.get('EC', 0)
                if item.get('faults', {}).get('temp'): temp = "תקלה"
                if item.get('faults', {}).get('ph'): ph = "תקלה"
                if item.get('faults', {}).get('ec'): ec = "תקלה"
                history_text += f"[{time_str}] Temp: {temp}, pH: {ph}, EC: {ec}\n"
        else:
            history_text = "אין עדיין נתונים היסטוריים מספיקים."

        prompt = f"""
        אתה אגרונום מומחה לגידול הידרופוני. תפקידך לנתח את נתוני המערכת של בקר {controller_id}.
        סגנון הכתיבה הנדרש: {'דוח מקצועי, רשמי ומדויק' if style == 'professional' else 'קליל, ידידותי, מעודד ובגובה העיניים'}.

        יעדי הגידול:
        - טמפרטורה: {settings.get('temp_min')} - {settings.get('temp_max')} °C
        - pH: {settings.get('ph_min')} - {settings.get('ph_max')}
        - EC: {settings.get('ec_min')} - {settings.get('ec_max')} uS

        היסטוריה (כל 10 דק'):
        {history_text}

        אנא כתוב דוח קצר (עד 3 פסקאות) הכולל סיכום, מגמות, תקלות (אם יש המילה "תקלה") והמלצות. עברית בלבד.
        """

        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            print(f"[{controller_id}] Report generated! Formatting HTML and sending email to {client_email}...")

            # --- 1. המרת הטקסט של ג'מיני לעיצוב HTML תקין ---
            html_body = markdown.markdown(response.text)

            # --- 2. תבנית העיצוב המדהימה של המייל ---
            html_template = f"""
            <!DOCTYPE html>
            <html lang="he" dir="rtl">
            <head>
                <meta charset="UTF-8">
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background-color: #f4f7f6;
                        color: #333333;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 600px;
                        margin: 0 auto;
                        background-color: #ffffff;
                        border-radius: 10px;
                        overflow: hidden;
                        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
                    }}
                    .header {{
                        background-color: #8b5cf6; /* צבע סגול שמתאים לדשבורד שלך */
                        color: #ffffff;
                        padding: 20px;
                        text-align: center;
                    }}
                    .header h1 {{
                        margin: 0;
                        font-size: 24px;
                    }}
                    .content {{
                        padding: 30px;
                        line-height: 1.6;
                        font-size: 16px;
                    }}
                    .content h2, .content h3 {{
                        color: #8b5cf6;
                    }}
                    .content strong {{
                        color: #1e293b;
                    }}
                    .footer {{
                        background-color: #f8fafc;
                        text-align: center;
                        padding: 15px;
                        font-size: 12px;
                        color: #64748b;
                        border-top: 1px solid #e2e8f0;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <img src="URL_HERE" alt="https://raw.githubusercontent.com/shimonYeshayahu/Hydro-OTA/76c988554de2585c9236f2450fb0aa55985b0a1d/logo_shimon.png" style="max-height: 70px; margin-bottom: 15px;">
                        
                        <h1>דוח אגרונומי חכם 🌿</h1>
                        <p style="margin: 5px 0 0 0; font-size: 14px;">מערכת ניהול הידרופוניקה | בקר {controller_id}</p>
                    </div>
                    <div class="content">
                        {html_body}
                    </div>
                    <div class="footer">
                        דוח זה הופק אוטומטית על ידי בינה מלאכותית ונשלח ממערכת הבקרה שלכם.<br>
                        &copy; 2026 כל הזכויות שמורות לשמעון ישעיהו (Shimon Yeshayahu)
                    </div>
                </div>
            </body>
            </html>
            """

            # --- 3. בניית האימייל ושליחתו ---
            msg = MIMEMultipart('alternative')
            msg['From'] = SENDER_EMAIL
            msg['To'] = client_email
            msg['Subject'] = f"דוח אגרונומי חכם - מערכת {controller_id}"

            # אנחנו מצרפים את ה-HTML שעיצבנו
            msg.attach(MIMEText(html_template, 'html', 'utf-8'))

            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
            server.quit()

            print(f"[{controller_id}] Email sent successfully!")

        except Exception as e:
            print(f"[{controller_id}] Failed to generate or send report: {e}")

if __name__ == "__main__":
    process_all_controllers()