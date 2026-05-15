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


def check_calibration_reminder(mac: str, cdata: dict):
    """תזכורת כיול שנתית – אם עברה שנה מאז הכיול האחרון, שולח מייל ללקוח.
    משתמש ב-controllers/{MAC}/last_calibration/ts (epoch) כתאריך הכיול האחרון.
    אם לא קיים – משתמש ב-installation_date (כעת לא קיים ב-RTDB, אז dilemma).
    ל-MVP: רק אם יש last_calibration. אם אין – לא מטריד.

    Anti-spam: לא שולח שוב במשך 30 ימים מאז התזכורת האחרונה.
    """
    sub = cdata.get("subscription") or {}
    tier = sub.get("tier", "free")
    if tier not in TIER_ALLOWED:
        return False  # רק PRO/PRO+ מקבלים תזכורות

    last_cal = cdata.get("last_calibration") or {}
    last_cal_ts = last_cal.get("ts", 0) if isinstance(last_cal, dict) else 0
    if not last_cal_ts:
        return False

    now = now_ts()
    days_since_cal = (now - last_cal_ts) / 86400
    if days_since_cal < 365:
        return False  # עוד לא עברה שנה

    # בדיקת anti-spam: התזכורת האחרונה הייתה לפני יותר מ-30 יום?
    meta = cdata.get("meta") or {}
    last_reminder = meta.get("cal_reminder_sent_at", 0) if isinstance(meta, dict) else 0
    if last_reminder and (now - last_reminder) < (30 * 86400):
        return False  # נשלח לאחרונה

    owner_email = cdata.get("owner_email") or ""
    if not owner_email:
        return False

    settings = cdata.get("settings") or {}
    device_name = settings.get("deviceName") or mac

    # שליחת המייל
    ok = send_calibration_reminder_email(owner_email, mac, device_name, int(days_since_cal))
    if ok:
        try:
            db.reference(f"controllers/{mac}/meta").update({"cal_reminder_sent_at": now})
            print(f"  [cal-reminder] {mac} ({device_name}): sent to {owner_email} ({int(days_since_cal)} days since last cal)")
        except Exception as e:
            print(f"  [cal-reminder-mark] {mac}: {e}")
    return ok


def send_calibration_reminder_email(to_email: str, mac: str, device_name: str, days_since: int) -> bool:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        return False
    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8"></head>
<body style="font-family:sans-serif;direction:rtl;padding:20px;background:#f3f4f6;">
  <div style="max-width:600px;margin:0 auto;background:white;border-radius:16px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
    <div style="background:linear-gradient(135deg,#0891b2,#06b6d4);color:white;padding:30px;text-align:center;">
      <div style="font-size:48px;margin-bottom:10px;">🎯</div>
      <h1 style="margin:0;font-size:22px;">הגיע הזמן לכיול שנתי</h1>
    </div>
    <div style="padding:30px;line-height:1.6;">
      <p>שלום,</p>
      <p>הבקר ההידרופוני שלך <b>{device_name}</b> כויל לאחרונה לפני <b>{days_since} ימים</b>.</p>
      <p>אנו ממליצים על כיול שנתי של חיישני ה-pH וה-EC כדי לשמור על דיוק הקריאות והבטיחות של מערכת ההזרקה.</p>
      <div style="background:#ecfeff;border-right:4px solid #06b6d4;padding:15px;border-radius:8px;margin:20px 0;">
        <h3 style="margin:0 0 10px 0;color:#0891b2;">איך לתאם כיול?</h3>
        <p style="margin:0;">השב למייל זה או צור קשר ב-WhatsApp:<br>
        📩 <a href="mailto:smarthydro.il@gmail.com">smarthydro.il@gmail.com</a><br>
        💬 <a href="https://wa.me/972526730423">0526730423</a></p>
      </div>
      <p style="font-size:13px;color:#6b7280;">מומלץ לתאם הגעה לפני שתבחין בקריאות לא יציבות. כיול עצמאי אפשרי דרך אפליקציית SmartHydro תחת "הגדרות → כיול חיישנים".</p>
      <p style="color:#6b7280;font-size:13px;margin-top:30px;">עם הוקרה,<br>שמעון – SmartHydro Systems</p>
    </div>
    <div style="background:#f9fafb;padding:12px;text-align:center;font-size:11px;color:#9ca3af;">
      תזכורת זו נשלחה אחת לשנה. בקר: {device_name}
    </div>
  </div>
</body></html>"""
    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = f"🎯 הגיע הזמן לכיול שנתי של {device_name}"
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"  [cal-email-err] {to_email}: {e}")
        return False


def enforce_subscription_expiry(mac: str, cdata: dict):
    """אם המנוי פג תוקף – מוריד את ה-tier ל-free וגורר expiresAt.
    קריטי לאכיפת מודל החיוב.
    """
    sub = (cdata.get("subscription") or {})
    tier = sub.get("tier", "free")
    if tier == "free":
        return False
    expires_at = sub.get("expiresAt") or sub.get("expires_at") or 0
    if not expires_at:
        return False
    if expires_at >= now_ts():
        return False  # עוד בתוקף
    # פג תוקף – הורד ל-free
    try:
        db.reference(f"controllers/{mac}/subscription").update({
            "tier": "free",
            "expiresAt": None,
            "downgraded_at": now_ts(),
            "previous_tier": tier
        })
        print(f"  [downgrade] {mac}: {tier} → free (expired {datetime.fromtimestamp(expires_at)})")
        return True
    except Exception as e:
        print(f"  [downgrade-err] {mac}: {e}")
        return False


def main():
    controllers = db.reference("controllers").get() or {}
    total = 0
    downgraded = 0
    cal_reminders = 0
    for mac, cdata in controllers.items():
        if not isinstance(cdata, dict):
            continue
        total += 1
        try:
            # אכיפת תוקף לפני בדיקת התראות
            if enforce_subscription_expiry(mac, cdata):
                downgraded += 1
                cdata = db.reference(f"controllers/{mac}").get() or cdata
            # תזכורת כיול שנתית
            if check_calibration_reminder(mac, cdata):
                cal_reminders += 1
            evaluate_controller(mac, cdata)
        except Exception as e:
            print(f"[err] {mac}: {e}")
    print(f"Processed {total} controllers. Downgraded: {downgraded}. Calibration reminders: {cal_reminders}. Quiet hours: {is_quiet_hours()}")
    # עיבוד פניות משוב חדשות
    process_feedback()


if __name__ == "__main__":
    main()
