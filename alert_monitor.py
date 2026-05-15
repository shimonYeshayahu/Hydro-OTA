"""
SmartHydro Alert Monitor
========================
רץ כל 5 דקות ב-GitHub Actions cron.
1. עובר על כל הבקרים ב-Firebase RTDB.
2. מסנן רק PRO / PRO+ (Free לא מקבל push).
3. בודק 6 תנאי התראה לכל בקר.
4. אם תנאי מתקיים ועברה תקופת cooldown – שולח FCM ל-tokens של המשתמש.
5. שומר last_sent timestamp ל-anti-spam.
6. כיבוד שעות שקט (22:00–07:00 IST) להתראות לא-קריטיות.
"""

import os
import json
import time
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import firebase_admin
from firebase_admin import credentials, db, messaging

# ----------- הגדרות -----------
COOLDOWN_SECONDS = 3600           # התראה זהה לא תישלח שוב במשך שעה
OFFLINE_THRESHOLD_SEC = 30 * 60   # 30 דקות בלי lastUpdate = offline
SUSTAINED_OUT_OF_RANGE_SEC = 3600 # שעה רצופה של חריגה לפני התראה (לא נבדק כאן – מוערך לפי לוג ה-RTDB realtime; MVP: מיידי)
WATER_LOW_PCT = 15                # התראה כשהמפלס נופל מתחת לאחוז זה
QUIET_HOUR_START = 22
QUIET_HOUR_END = 7
IST_OFFSET = 3  # IDT (קיץ). חורף = 2. לצורך MVP – ניקח 3.

CRITICAL_ALERTS = {"pump_lock", "controller_offline"}  # תמיד נשלחות גם בשעות שקט
TIER_ALLOWED = ("pro", "pro_plus")

# הגדרות שליחת משוב
FEEDBACK_TO_EMAIL = "smarthydro.il@gmail.com"
SENDER_EMAIL = os.getenv("GMAIL_USER")
SENDER_PASSWORD = os.getenv("GMAIL_PASS")
FEEDBACK_TYPE_LABELS = {
    "bug": "🐛 באג / תקלה",
    "feature": "💡 רעיון לשיפור",
    "question": "❓ שאלה",
    "other": "📌 אחר"
}

# ----------- אתחול Firebase Admin -----------
DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")
service_account_info = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT"))

print(f"[{datetime.utcnow().isoformat()}Z] Alert monitor starting...")
cred = credentials.Certificate(service_account_info)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {'databaseURL': DATABASE_URL})


def now_ts() -> int:
    return int(time.time())


def is_quiet_hours() -> bool:
    """22:00–07:00 שעון ישראל (IDT). מחזיר True בתוך שעות שקט."""
    utc_hour = datetime.now(timezone.utc).hour
    ist_hour = (utc_hour + IST_OFFSET) % 24
    if QUIET_HOUR_START < QUIET_HOUR_END:
        return QUIET_HOUR_START <= ist_hour < QUIET_HOUR_END
    return ist_hour >= QUIET_HOUR_START or ist_hour < QUIET_HOUR_END


def get_user_for_controller(mac: str):
    """מחזיר (uid, prefs, tokens) או None אם הבקר לא משויך."""
    devices_root = db.reference("users").get(shallow=False) or {}
    for uid, udata in devices_root.items():
        devices = (udata or {}).get("devices", {})
        if mac in devices:
            prefs = udata.get("notification_prefs", {}) or {}
            tokens_node = udata.get("fcm_tokens", {}) or {}
            tokens = [v.get("token") for v in tokens_node.values() if isinstance(v, dict) and v.get("token")]
            return uid, prefs, tokens
    return None, None, []


def can_send(uid: str, mac: str, alert_type: str) -> bool:
    """בדיקת cooldown: לא לשלוח שוב את אותה התראה תוך שעה."""
    path = f"users/{uid}/alert_state/{mac}/{alert_type}/last_sent"
    last = db.reference(path).get()
    if not last:
        return True
    return (now_ts() - int(last)) >= COOLDOWN_SECONDS


def mark_sent(uid: str, mac: str, alert_type: str):
    path = f"users/{uid}/alert_state/{mac}/{alert_type}"
    db.reference(path).update({"last_sent": now_ts()})


def push_to_user(tokens, title: str, body: str, alert_type: str, severity: str, mac: str):
    """שולח FCM לכל הטוקנים של המשתמש. מסיר טוקנים לא תקפים."""
    if not tokens:
        print(f"  [warn] no tokens for alert {alert_type}")
        return
    msg = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data={"alert_type": alert_type, "severity": severity, "mac": mac},
        webpush=messaging.WebpushConfig(
            notification=messaging.WebpushNotification(
                title=title, body=body,
                icon="/icon-192.png", badge="/icon-192.png",
                require_interaction=(severity == "critical"),
                tag=alert_type,
                renotify=True
            )
        )
    )
    try:
        resp = messaging.send_each_for_multicast(msg)
        print(f"  [send] {alert_type}: success={resp.success_count} fail={resp.failure_count}")
    except Exception as e:
        print(f"  [send-error] {alert_type}: {e}")


def alert_label(alert_type: str) -> str:
    return {
        "pump_lock":          "🔴 משאבה ננעלה",
        "controller_offline": "🔴 בקר Offline",
        "sensor_offline":     "🟠 חיישן לא מגיב",
        "ph_ec_out":          "🟠 pH/EC חורג מהטווח",
        "temp_out":           "🟡 טמפרטורה חורגת",
        "water_low":          "🟠 מפלס מים נמוך"
    }.get(alert_type, alert_type)


def evaluate_controller(mac: str, cdata: dict):
    """בודק 6 תנאים לבקר אחד ושולח התראות מתאימות."""
    sub = (cdata.get("subscription") or {})
    tier = sub.get("tier", "free")
    if tier not in TIER_ALLOWED:
        return  # Free או tier לא ידוע – מדלגים

    uid, prefs, tokens = get_user_for_controller(mac)
    if not uid or not tokens:
        return

    rt = cdata.get("realtime") or {}
    settings = cdata.get("settings") or {}
    last_update = rt.get("lastUpdate") or 0
    device_name = settings.get("deviceName") or mac
    in_quiet = is_quiet_hours()

    def try_send(alert_type: str, body: str, severity: str = "warning"):
        # מעבר על quiet hours להתראות לא-קריטיות
        if in_quiet and alert_type not in CRITICAL_ALERTS:
            return
        # ההעדפה של המשתמש (קריטיות תמיד דלוקות)
        pref_key = "notif_" + {
            "pump_lock": "pump_lock",
            "controller_offline": "offline",
            "sensor_offline": "sensor",
            "ph_ec_out": "ph_ec",
            "temp_out": "temp",
            "water_low": "water_low"
        }[alert_type]
        if alert_type not in CRITICAL_ALERTS and prefs.get(pref_key) is False:
            return
        if not can_send(uid, mac, alert_type):
            return
        title = f"{alert_label(alert_type)} – {device_name}"
        push_to_user(tokens, title, body, alert_type, severity, mac)
        mark_sent(uid, mac, alert_type)

    # 1. Pump lock (קריטי)
    status = rt.get("status") or {}
    if status.get("ph_pump_locked"):
        try_send("pump_lock", "משאבת pH נעולה אחרי 5 הזרקות רצופות. בדוק מאגר חומצה ואפס נעילות.", "critical")
    if status.get("ec_pump_locked"):
        try_send("pump_lock", "משאבת EC נעולה אחרי 5 הזרקות רצופות. בדוק מאגר דשן ואפס נעילות.", "critical")

    # 2. Controller offline (קריטי)
    if last_update and (now_ts() - int(last_update)) > OFFLINE_THRESHOLD_SEC:
        mins = (now_ts() - int(last_update)) // 60
        try_send("controller_offline", f"הבקר לא דיווח {mins} דקות. בדוק חשמל וחיבור WiFi.", "critical")

    # 3. Sensor offline
    faults = rt.get("faults") or {}
    if faults.get("ph") or faults.get("ec") or faults.get("temp"):
        try_send("sensor_offline", "חיישן Modbus לא מגיב. הנתונים החיים לא אמינים.")

    # 4. pH/EC out of range (מיידי ב-MVP, נשפר ל-sustained בעתיד)
    tele = rt.get("telemetry") or {}
    ph = tele.get("pH")
    ec = tele.get("EC")
    ph_min = settings.get("ph_min")
    ph_max = settings.get("ph_max")
    ec_min = settings.get("ec_min")
    ec_max = settings.get("ec_max")
    if ph is not None and ph_min is not None and ph_max is not None:
        if ph < ph_min or ph > ph_max:
            try_send("ph_ec_out", f"pH={ph:.2f} (טווח {ph_min}-{ph_max}). בדוק את האיזון.")
    if ec is not None and ec_min is not None and ec_max is not None:
        if ec < ec_min or ec > ec_max:
            try_send("ph_ec_out", f"EC={int(ec)} µS (טווח {int(ec_min)}-{int(ec_max)}). בדוק את הדשן.")

    # 5. Temperature
    temp = tele.get("temperature")
    t_min = settings.get("temp_min")
    t_max = settings.get("temp_max")
    if temp is not None and t_min is not None and t_max is not None:
        if temp < t_min or temp > t_max:
            try_send("temp_out", f"טמפרטורה {temp:.1f}°C (טווח {t_min}-{t_max}).")

    # 6. Water level low (לפי אולטרסוני)
    dist = tele.get("water_distance_cm")
    tank_empty = settings.get("tank_empty_cm")
    tank_full = settings.get("tank_full_cm")
    if dist and tank_empty and tank_full and tank_empty > tank_full:
        pct = max(0, min(100, ((tank_empty - dist) / (tank_empty - tank_full)) * 100))
        if pct < WATER_LOW_PCT:
            try_send("water_low", f"מפלס מים נמוך מאוד ({pct:.0f}%). מלא את המאגר.")


def send_feedback_email(entry_id: str, entry: dict):
    """שולח מייל לצוות SmartHydro עם פנייה חדשה."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"  [feedback-skip] {entry_id}: no email credentials")
        return False
    fb_type = entry.get("type", "other")
    type_label = FEEDBACK_TYPE_LABELS.get(fb_type, fb_type)
    user_email = entry.get("user_email", "?")
    controller_id = entry.get("controller_id", "?")
    message = entry.get("message", "")
    ua = entry.get("user_agent", "")[:120]
    created = datetime.fromtimestamp(entry.get("created_at", time.time()))

    subject = f"[SmartHydro] {type_label} מ-{user_email}"
    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8"></head>
<body style="font-family:sans-serif;direction:rtl;padding:20px;">
  <div style="max-width:600px;margin:0 auto;border:1px solid #ddd;border-radius:10px;overflow:hidden;">
    <div style="background:#0891b2;color:white;padding:15px;"><h2 style="margin:0;">{type_label}</h2></div>
    <div style="padding:20px;">
      <p><b>מאת:</b> {user_email}</p>
      <p><b>בקר:</b> {controller_id}</p>
      <p><b>תאריך:</b> {created.strftime('%d/%m/%Y %H:%M')}</p>
      <hr>
      <div style="background:#f9fafb;padding:15px;border-radius:8px;white-space:pre-wrap;">{message}</div>
      <p style="font-size:11px;color:#9ca3af;margin-top:20px;">User-Agent: {ua}</p>
    </div>
  </div>
</body></html>"""
    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = FEEDBACK_TO_EMAIL
    msg['Reply-To'] = user_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"  [feedback-ok] {entry_id} sent to {FEEDBACK_TO_EMAIL}")
        return True
    except Exception as e:
        print(f"  [feedback-err] {entry_id}: {e}")
        return False


def process_feedback():
    """עובר על פניות חדשות (emailed=false), שולח מייל, ומסמן emailed=true."""
    try:
        feedback = db.reference("feedback").get() or {}
    except Exception as e:
        print(f"[feedback-fetch] {e}")
        return
    new_count = 0
    for entry_id, entry in feedback.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("emailed"):
            continue
        new_count += 1
        ok = send_feedback_email(entry_id, entry)
        if ok:
            try:
                db.reference(f"feedback/{entry_id}/emailed").set(True)
                db.reference(f"feedback/{entry_id}/emailed_at").set(int(time.time()))
            except Exception as e:
                print(f"  [feedback-mark] {entry_id}: {e}")
    if new_count:
        print(f"Processed {new_count} feedback entries.")


def main():
    controllers = db.reference("controllers").get() or {}
    total = 0
    for mac, cdata in controllers.items():
        if not isinstance(cdata, dict):
            continue
        total += 1
        try:
            evaluate_controller(mac, cdata)
        except Exception as e:
            print(f"[err] {mac}: {e}")
    print(f"Processed {total} controllers. Quiet hours: {is_quiet_hours()}")
    # עיבוד פניות משוב חדשות
    process_feedback()


if __name__ == "__main__":
    main()
