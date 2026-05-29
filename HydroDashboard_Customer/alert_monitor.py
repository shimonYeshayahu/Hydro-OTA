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
COOLDOWN_EMAIL_WARNING = 7200     # מייל Warning לא יישלח שוב תוך שעתיים
COOLDOWN_EMAIL_CRITICAL = 1800    # מייל Critical לא יישלח שוב תוך 30 דקות
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


def can_send_alert_email(mac: str, level: int) -> bool:
    """בדיקת cooldown למיילי התראה מדורגות (נפרד מ-FCM)."""
    path = f"controllers/{mac}/alert_email_state/last_sent"
    last = db.reference(path).get()
    if not last:
        return True
    cooldown = COOLDOWN_EMAIL_CRITICAL if level >= 3 else COOLDOWN_EMAIL_WARNING
    return (now_ts() - int(last)) >= cooldown


def mark_alert_email_sent(mac: str, level: int):
    db.reference(f"controllers/{mac}/alert_email_state").update({
        "last_sent": now_ts(),
        "last_level": level
    })


def send_tiered_alert_email(to_email: str, mac: str, device_name: str, level: int,
                            ph=None, ec=None, temp=None,
                            ph_min=None, ph_max=None, ec_min=None, ec_max=None,
                            alert_since_sec=0):
    """V14.0: שולח מייל התראה מדורג — Warning (level 2) או Critical (level 3)."""
    if not SENDER_EMAIL or not SENDER_PASSWORD or not to_email:
        return False

    is_critical = level >= 3
    bg_color = "#dc2626" if is_critical else "#ea580c"
    emoji = "🚨" if is_critical else "⚠️"
    level_he = "קריטית" if is_critical else "אזהרה"
    duration_min = int(alert_since_sec / 60) if alert_since_sec else 0

    # בניית תיאור החריגות
    issues = []
    if ph is not None and ph_min is not None and ph_max is not None:
        if ph < ph_min or ph > ph_max:
            issues.append(f"pH = {ph:.2f} (טווח תקין: {ph_min}-{ph_max})")
    if ec is not None and ec_min is not None and ec_max is not None:
        if ec < ec_min or ec > ec_max:
            issues.append(f"EC = {int(ec)} µS (טווח תקין: {int(ec_min)}-{int(ec_max)})")
    if temp is not None:
        issues.append(f"טמפרטורה = {temp:.1f}°C")
    if not issues:
        issues.append("חריגה מטווחי היעד")

    issues_html = "".join(f'<li style="margin:6px 0;font-size:15px;">{i}</li>' for i in issues)
    duration_text = f"<p>משך החריגה: <b>{duration_min} דקות</b></p>" if duration_min > 0 else ""

    critical_box = ""
    if is_critical:
        critical_box = """
        <div style="background:#fef2f2;border-right:4px solid #dc2626;padding:15px;border-radius:8px;margin:20px 0;">
          <h3 style="margin:0 0 10px 0;color:#991b1b;">⚡ פעולה נדרשת מיידית</h3>
          <p style="margin:0;color:#7f1d1d;">ערכי pH/EC חרגו מסף הנזק. בדוק את המערכת בהקדם — ייתכן נזק לצמחים.</p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8"></head>
<body style="font-family:sans-serif;direction:rtl;padding:20px;background:#f3f4f6;">
  <div style="max-width:600px;margin:0 auto;background:white;border-radius:16px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
    <div style="background:linear-gradient(135deg,{bg_color},{bg_color}dd);color:white;padding:30px;text-align:center;">
      <div style="font-size:48px;margin-bottom:10px;">{emoji}</div>
      <h1 style="margin:0;font-size:22px;">התראה {level_he} — {device_name}</h1>
    </div>
    <div style="padding:30px;line-height:1.8;">
      <p>שלום,</p>
      <p>הבקר <b>{device_name}</b> מדווח על <b>חריגה ברמה {level}</b>:</p>
      <ul style="background:#fef3c7;border-right:4px solid {bg_color};padding:15px 35px;border-radius:8px;list-style:none;">
        {issues_html}
      </ul>
      {duration_text}
      {critical_box}
      <div style="background:#f0fdfa;border-right:4px solid #059669;padding:15px;border-radius:8px;margin:20px 0;">
        <h3 style="margin:0 0 10px 0;color:#065f46;">מה לעשות?</h3>
        <ol style="margin:0;padding-right:20px;color:#064e3b;">
          <li>פתח את אפליקציית SmartHydro ובדוק את הקריאות בזמן אמת</li>
          <li>בדוק שמאגרי הדשן/חומצה מלאים</li>
          <li>אם משאבה נעולה — שחרר דרך האפליקציה</li>
        </ol>
      </div>
      <p style="color:#6b7280;font-size:13px;margin-top:30px;">בקר: {device_name} ({mac})<br>
      שאלות? השב למייל זה או צור קשר ב-WhatsApp: 052-211-4095</p>
    </div>
    <div style="background:#f9fafb;padding:12px;text-align:center;font-size:11px;color:#9ca3af;">
      התראה אוטומטית • SmartHydro Systems • רמה {level}
    </div>
  </div>
</body></html>"""

    subject = f"{emoji} התראה {level_he}: {device_name}"
    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"  [alert-email-ok] {mac} level={level} → {to_email}")
        return True
    except Exception as e:
        print(f"  [alert-email-err] {mac}: {e}")
        return False


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

    # 7. V14.0: Tiered alert email — מייל מדורג לפי alert_level מהקושחה
    fw_alert_level = status.get("alert_level", 0)
    fw_alert_since = status.get("alert_since", 0)  # שניות מאז תחילת החריגה
    if isinstance(fw_alert_level, (int, float)) and fw_alert_level >= 2:
        fw_alert_level = int(fw_alert_level)
        # Level 3 עוקף quiet hours; Level 2 מכבד
        if fw_alert_level >= 3 or not in_quiet:
            owner_email = cdata.get("owner_email") or ""
            if owner_email and can_send_alert_email(mac, fw_alert_level):
                sent = send_tiered_alert_email(
                    to_email=owner_email, mac=mac, device_name=device_name,
                    level=fw_alert_level,
                    ph=ph, ec=ec, temp=temp,
                    ph_min=ph_min, ph_max=ph_max,
                    ec_min=ec_min, ec_max=ec_max,
                    alert_since_sec=int(fw_alert_since) if fw_alert_since else 0
                )
                if sent:
                    mark_alert_email_sent(mac, fw_alert_level)


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


def send_subscription_reminder_email(to_email: str, mac: str, device_name: str, tier: str, days_left: int) -> bool:
    """שולח מייל ידידותי על מנוי שעומד להסתיים. נשלח ב-7/3/1 ימים לפני התפוגה."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        return False
    if not to_email:
        return False

    tier_display = "PRO+" if tier == "pro_plus" else ("PRO" if tier == "pro" else tier)
    urgency_color = "#dc2626" if days_left <= 1 else ("#ea580c" if days_left <= 3 else "#0891b2")
    urgency_emoji = "🚨" if days_left <= 1 else ("⚠️" if days_left <= 3 else "📅")
    if days_left <= 1:
        urgency_label = "מנויך מסתיים מחר"
    elif days_left <= 3:
        urgency_label = f"מנויך מסתיים בעוד {days_left} ימים"
    else:
        urgency_label = f"מנויך מסתיים בעוד {days_left} ימים"

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="UTF-8"></head>
<body style="font-family:sans-serif;direction:rtl;padding:20px;background:#f3f4f6;">
  <div style="max-width:600px;margin:0 auto;background:white;border-radius:16px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
    <div style="background:linear-gradient(135deg,{urgency_color},{urgency_color}dd);color:white;padding:30px;text-align:center;">
      <div style="font-size:48px;margin-bottom:10px;">{urgency_emoji}</div>
      <h1 style="margin:0;font-size:22px;">{urgency_label}</h1>
    </div>
    <div style="padding:30px;line-height:1.6;">
      <p>שלום,</p>
      <p>מנוי <b>{tier_display}</b> עבור הבקר <b>{device_name}</b> יסתיים בעוד <b>{days_left} ימים</b>.</p>
      <p>כדי להמשיך ליהנות מ:</p>
      <ul style="background:#f0fdfa;border-right:4px solid {urgency_color};padding:15px 35px;border-radius:8px;list-style:none;">
        <li style="margin:6px 0;">🤖 דוחות AI אגרונומיים יומיים</li>
        <li style="margin:6px 0;">📊 גרפים והיסטוריה מלאה</li>
        <li style="margin:6px 0;">🌱 מחזורי גידול עם השוואות</li>
        <li style="margin:6px 0;">🔔 התראות Push לטלפון</li>
      </ul>
      <p>אנא חדש את המנוי לפני התפוגה. ללא חידוש, החשבון יחזור למסלול חינמי (הבקר ימשיך לפעול אוטונומית, אבל גישה לדשבורד הענני תיחסם).</p>
      <div style="background:#fef3c7;border-right:4px solid #f59e0b;padding:15px;border-radius:8px;margin:20px 0;">
        <h3 style="margin:0 0 10px 0;color:#92400e;">לחידוש המנוי</h3>
        <p style="margin:0;">השב למייל זה או צור קשר:<br>
        📩 <a href="mailto:smarthydro.il@gmail.com">smarthydro.il@gmail.com</a><br>
        💬 <a href="https://wa.me/972526730423">0526730423</a></p>
      </div>
      <p style="color:#6b7280;font-size:13px;margin-top:30px;">תזכורת אוטומטית • SmartHydro Systems</p>
    </div>
    <div style="background:#f9fafb;padding:12px;text-align:center;font-size:11px;color:#9ca3af;">
      בקר: {device_name} · מסלול נוכחי: {tier_display}
    </div>
  </div>
</body></html>"""

    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Cc'] = SENDER_EMAIL  # עותק לי כדי לדעת מי קיבל מה
    msg['Subject'] = f"{urgency_emoji} {urgency_label} - {device_name}"
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"  [sub-reminder-err] {to_email}: {e}")
        return False


def check_subscription_reminder(mac: str, cdata: dict) -> bool:
    """תזכורת מנוי: שולחת מייל ב-7/3/1 ימים לפני תפוגת המנוי.
    משתמשת ב-RTDB subscription/reminder_sent_days כדי למנוע spam.

    מחזירה True אם נשלחה תזכורת.
    """
    sub = (cdata.get("subscription") or {})
    tier = sub.get("tier", "free")
    if tier == "free":
        return False
    expires_at = sub.get("expiresAt") or sub.get("expires_at") or 0
    if not expires_at:
        return False

    seconds_left = int(expires_at) - now_ts()
    if seconds_left <= 0:
        return False  # כבר פג – יטופל ע"י enforce_subscription_expiry
    days_left = seconds_left // 86400

    # נשלחת תזכורת רק ב-7 / 3 / 1 ימים שנותרו
    reminder_milestones = [7, 3, 1]
    matching = next((m for m in reminder_milestones if days_left == m), None)
    if matching is None:
        return False

    # מניעת spam – אם כבר נשלחה תזכורת לאותה אבן דרך
    reminders_sent = sub.get("reminders_sent") or []
    if isinstance(reminders_sent, list) and matching in reminders_sent:
        return False
    if not isinstance(reminders_sent, list):
        reminders_sent = []

    owner_email = cdata.get("owner_email") or sub.get("owner_email")
    if not owner_email:
        return False
    device_name = (cdata.get("settings") or {}).get("deviceName") or mac

    if send_subscription_reminder_email(owner_email, mac, device_name, tier, days_left):
        reminders_sent.append(matching)
        try:
            db.reference(f"controllers/{mac}/subscription").update({
                "reminders_sent": reminders_sent,
                "last_reminder_at": now_ts()
            })
        except Exception as e:
            print(f"  [sub-reminder-mark-err] {mac}: {e}")
        print(f"  [sub-reminder] {mac}: tier={tier} days_left={days_left} → email sent to {owner_email}")
        return True
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


EX_PRO_GRACE_SECONDS = 365 * 86400      # 365 יום grace ללקוח שהיה PRO וירד ל-Free

# V13.33: Slots לפי tier (היברידי – slots ויזואליים + נעילה ידנית)
SLOTS_BY_TIER = {
    "pro": 4,           # 4 עונות / מחזורים
    "pro_plus": 10,     # 2.5 שנות גידול
    "free_grace": 4,    # Free שירד מ-PRO ועדיין ב-365 יום
}


def enforce_cycle_retention(mac: str, cdata: dict):
    """V13.33: ניהול slots של מחזורי גידול עם תמיכה בנעילה ידנית ובמחיקת לקוח.
    - PRO=4 slots, PRO+=10 slots, Free-grace=4 slots, Free טהור / grace פג = 0.
    - מחזור נעול (meta.locked=true): לא נמחק אוטומטית, אבל סופר ל-slot.
    - מחזור שהלקוח סימן למחיקה (meta.deleted=true): נמחק מיידית, ללא קשר ל-cap.
    - בעודף slots: מוחק את הישנים שאינם נעולים, מהישן ביותר.
    מחזיר (kept_count, deleted_count).
    """
    sub = (cdata.get("subscription") or {})
    tier = sub.get("tier", "free")
    downgraded_at = sub.get("downgraded_at") or sub.get("downgradedAt") or 0
    cycles_data = cdata.get("cycles") or {}
    if not isinstance(cycles_data, dict) or not cycles_data:
        return (0, 0)

    # קביעת slots לפי tier
    slots = 0
    if tier == "pro":
        slots = SLOTS_BY_TIER["pro"]
    elif tier == "pro_plus":
        slots = SLOTS_BY_TIER["pro_plus"]
    elif tier == "free" and downgraded_at and (now_ts() - int(downgraded_at)) < EX_PRO_GRACE_SECONDS:
        slots = SLOTS_BY_TIER["free_grace"]
    # אחרת slots=0 → מחיקה מלאה

    try:
        # מיון מחזורים: גבוה=חדש; שליפת locked + deleted מתוך meta
        items = []
        for k, v in cycles_data.items():
            if not str(k).isdigit() or not isinstance(v, dict):
                continue
            meta = v.get("meta") or {}
            items.append({
                "n": int(k),
                "locked": bool(meta.get("locked")),
                "deleted": bool(meta.get("deleted")),
            })
        items.sort(key=lambda x: x["n"], reverse=True)

        deleted = 0

        # 1) מחיקות שהלקוח ביקש ידנית (meta.deleted=true) – ללא קשר ל-slots
        for it in list(items):
            if it["deleted"]:
                try:
                    db.reference(f"controllers/{mac}/cycles/{it['n']}").delete()
                    deleted += 1
                    items.remove(it)
                except Exception as e:
                    print(f"  [retention-del-err] {mac} cycle {it['n']}: {e}")

        # 2) אם אין slots – מחיקה גורפת + ניקוי hourly
        if slots <= 0:
            for it in items:
                try:
                    db.reference(f"controllers/{mac}/cycles/{it['n']}").delete()
                    deleted += 1
                except Exception as e:
                    print(f"  [retention-err] {mac} cycle {it['n']}: {e}")
            try:
                db.reference(f"controllers/{mac}/hourly").delete()
            except Exception:
                pass
            if deleted:
                print(f"  [retention] {mac}: tier={tier} no_slots → cleared {deleted}")
            return (0, deleted)

        # 3) Cap לפי slots – מוחק את הישנים שאינם נעולים
        if len(items) > slots:
            # מוחקים מהישן ביותר, רק לא-נעולים, עד שמגיעים ל-slots
            overflow = len(items) - slots
            # מיון מהישן לחדש
            items_oldest_first = sorted(items, key=lambda x: x["n"])
            for it in items_oldest_first:
                if overflow <= 0:
                    break
                if it["locked"]:
                    continue  # מדלגים – נעול
                try:
                    db.reference(f"controllers/{mac}/cycles/{it['n']}").delete()
                    deleted += 1
                    overflow -= 1
                except Exception as e:
                    print(f"  [retention-err] {mac} cycle {it['n']}: {e}")
            if overflow > 0:
                # כל המחזורים העודפים נעולים – לא מוחקים, אזהרה בלוג
                print(f"  [retention-warn] {mac}: tier={tier} overflow={overflow} but all locked – kept all")

        kept = len(items) - deleted if (slots > 0 and len(items) > slots) else len(items)
        if deleted:
            print(f"  [retention] {mac}: tier={tier} slots={slots} kept={kept} deleted={deleted}")
        return (kept, deleted)
    except Exception as e:
        print(f"  [retention-err] {mac}: {e}")
        return (0, 0)


def send_email_pref_confirmation(event_id, evt):
    """V25.1: שולח מייל אישור על שינוי העדפות מייל.
    evt: {uid, email, action, new_prefs, prev_prefs, ...}"""
    to_email = (evt.get('email') or '').strip()
    if not to_email:
        return False
    action = evt.get('action', 'updated')
    np = evt.get('new_prefs', {}) or {}
    enabled = bool(np.get('enabled'))
    freq = np.get('frequency', 'daily')
    style = np.get('style', 'brief')

    freq_he = 'שבועי (כל יום ראשון בבוקר)' if freq == 'weekly' else 'יומי (כל בוקר ב-7:00)'
    style_he = {'brief': 'תקציר קצר', 'detailed': 'דוח מפורט', 'agronomist': 'אגרונום AI מקצועי'}.get(style, 'תקציר')

    if action == 'unsubscribed':
        subject = 'SmartHydro – אישור ביטול דוחות במייל'
        body_html = f"""
        <div dir="rtl" style="font-family:Arial,sans-serif;padding:20px;max-width:600px">
          <h2 style="color:#dc2626">📭 ביטול דוחות במייל</h2>
          <p>שלום,</p>
          <p>אישור: <b>הסרת את עצמך מקבלת דוחות מייל אגרונומיים</b> מ-SmartHydro.</p>
          <p>לא תקבל יותר מיילים ממערכת הדוחות האוטומטית. ההתראות הקריטיות
             (משאבה ננעלה, בקר offline) ימשיכו להגיע ב-Push notifications באפליקציה.</p>
          <p style="color:#6b7280;font-size:13px">אם זה לא היית אתה - כנס לאפליקציה ובדוק את הגדרות המייל.</p>
          <hr>
          <p style="color:#9ca3af;font-size:12px">SmartHydro Dashboard · {SENDER_EMAIL}</p>
        </div>
        """
    else:
        subject = 'SmartHydro – אישור רישום לדוחות במייל'
        body_html = f"""
        <div dir="rtl" style="font-family:Arial,sans-serif;padding:20px;max-width:600px">
          <h2 style="color:#059669">✅ נרשמת לדוחות אגרונומיים במייל</h2>
          <p>שלום,</p>
          <p>ההעדפות שלך עודכנו:</p>
          <ul style="background:#f0fdf4;padding:15px 30px;border-radius:8px;border-right:4px solid #10b981">
            <li><b>תדירות:</b> {freq_he}</li>
            <li><b>סגנון:</b> {style_he}</li>
            <li><b>נשלח אל:</b> {to_email}</li>
          </ul>
          <p>תוכל לשנות או לבטל בכל עת מאפליקציית SmartHydro → הגדרות → דוח אגרונומי במייל.</p>
          <p style="color:#6b7280;font-size:13px">הדוח הראשון יישלח בריצה הבאה של המערכת.</p>
          <hr>
          <p style="color:#9ca3af;font-size:12px">SmartHydro Dashboard · {SENDER_EMAIL}</p>
        </div>
        """

    if not enabled and action != 'unsubscribed':
        # שינוי שאינו אמיתי (העדפות נשמרו כמושבתות) - אל תשלח
        return False

    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"  [email-pref-ok] {event_id} → {to_email} ({action})")
        return True
    except Exception as e:
        print(f"  [email-pref-err] {event_id}: {e}")
        return False


def process_email_pref_confirmations():
    """V25.1: עובר על pending_email_confirmations ושולח מייל אישור לכל אחד."""
    try:
        pending = db.reference('pending_email_confirmations').get() or {}
    except Exception as e:
        print(f"[pref-err] read pending: {e}")
        return 0

    sent = 0
    for event_id, evt in (pending or {}).items():
        if not isinstance(evt, dict) or evt.get('sent'):
            continue
        ok = send_email_pref_confirmation(event_id, evt)
        try:
            if ok:
                db.reference(f'pending_email_confirmations/{event_id}').update({
                    'sent': True,
                    'sent_at': int(time.time())
                })
                sent += 1
            else:
                # סמן כניסיון נכשל - אל תנסה שוב לנצח
                db.reference(f'pending_email_confirmations/{event_id}').update({
                    'sent': True,
                    'sent_at': int(time.time()),
                    'send_error': True
                })
        except Exception as e:
            print(f"[pref-err] update {event_id}: {e}")
    return sent


def cleanup_reboot_log(mac: str, cdata: dict):
    """V13.26: ניקוי רשומות reboot_log ישנות מ-30 יום.
    הקושחה דוחפת שורה לכל אתחול ב-/controllers/{MAC}/reboot_log/{epoch}.
    """
    reboot_log = cdata.get("reboot_log")
    if not reboot_log or not isinstance(reboot_log, dict):
        return 0
    cutoff = int(time.time()) - 30 * 86400  # 30 ימים
    deleted = 0
    for ts_key in list(reboot_log.keys()):
        try:
            ts_val = int(ts_key)
        except (ValueError, TypeError):
            continue
        if ts_val < cutoff:
            try:
                db.reference(f"controllers/{mac}/reboot_log/{ts_key}").delete()
                deleted += 1
            except Exception as e:
                print(f"  [reboot-log-cleanup-err] {mac}/{ts_key}: {e}")
    if deleted:
        print(f"  [reboot-log] {mac}: cleaned {deleted} old entries")
    return deleted


def main():
    controllers = db.reference("controllers").get() or {}
    total = 0
    downgraded = 0
    cal_reminders = 0
    sub_reminders = 0
    retention_deleted = 0
    reboot_log_cleaned = 0
    for mac, cdata in controllers.items():
        if not isinstance(cdata, dict):
            continue
        total += 1
        try:
            # אכיפת תוקף לפני בדיקת התראות
            if enforce_subscription_expiry(mac, cdata):
                downgraded += 1
                cdata = db.reference(f"controllers/{mac}").get() or cdata
            # V13.47: תזכורת מנוי לפני תפוגה (7/3/1 ימים)
            if check_subscription_reminder(mac, cdata):
                sub_reminders += 1
            # V13.31: אכיפת שמירת מחזורים לפי tier (כולל grace period)
            _, d = enforce_cycle_retention(mac, cdata)
            retention_deleted += d
            # V13.26: ניקוי reboot_log ישן מ-30 יום
            reboot_log_cleaned += cleanup_reboot_log(mac, cdata)
            # תזכורת כיול שנתית
            if check_calibration_reminder(mac, cdata):
                cal_reminders += 1
            evaluate_controller(mac, cdata)
        except Exception as e:
            print(f"[err] {mac}: {e}")
    pref_sent = process_email_pref_confirmations()
    print(f"Processed {total} controllers. Downgraded: {downgraded}. Sub reminders: {sub_reminders}. Cal reminders: {cal_reminders}. Cycles deleted: {retention_deleted}. Reboot logs cleaned: {reboot_log_cleaned}. Email pref confirms: {pref_sent}. Quiet hours: {is_quiet_hours()}")
    # עיבוד פניות משוב חדשות
    process_feedback()


if __name__ == "__main__":
    main()
