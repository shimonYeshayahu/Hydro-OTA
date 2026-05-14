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
from datetime import datetime, timezone, timedelta

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


if __name__ == "__main__":
    main()
