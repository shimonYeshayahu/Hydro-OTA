# SmartHydro Firmware Changelog

> היסטוריית גרסאות קושחה לבקרי SmartHydro (ESP32).
> קבצי `.bin` מוכנים לצריבה OTA או USB.

---

## V14.0 — `HydroSystem_V14.ino.bin` ⭐ נוכחית
**תאריך**: 2026-05-25 | **בקר**: Universal (כל הבקרים)

שינוי שם קובץ מ-V13.2 → V14.0. שיכתוב לוגיקת הבקרה.

- **Midpoint-seeking control** — לוגיקת בקרה חדשה: שואפת לאמצע הטווח (לא floor-guard כמו קודם). היסטרזיס 15%.
- **Smart Dilution** — דילול חכם עם מים נקיים כשהערכים גבוהים מדי (pH/EC).
- **Tiered Alerts (0-3)** — התראות מדורגות: 0=תקין, 1=שימו לב, 2=אזהרה, 3=קריטי.
- **Critical thresholds** — סף קריטי מטבלת גידולים מקצועית.
- לוגי הזרקות מפורטים ב-Serial.

---

## V13.x — סדרת Modbus (SN001)

### V13.2 — `HydroSystem_V13.2_MODBUS_SN001.ino.bin`
**תאריך**: ~2026-05-16 | **בקר**: SN-001

גרסת Modbus יציבה. כוללת את כל התיקונים V13.1-V13.56:

- **V13.1**: FirebaseJsonData per get (תיקון cascade EC↔PH); תאורה manual override 30 דק'
- **V13.2**: PUMP_EC ↔ PUMP_PH swap; ברז ידני 10 שניות + float safety
- **V13.3**: NTP wait לפני Firebase.begin (SSL handshake fix)
- **V13.4**: LIGHT_1 ↔ WATER_VALVE swap (חיווט פיזי הפוך לסכמה)
- **V13.5**: handlers ל-reset/calibration; uptime/bootTime/rebootReason
- **V13.6**: startNewCycle/resetCycleCount — תיקון crash
- **V13.7**: command polling 500ms; emergencySync; Remote OTA
- **V13.8**: emergencySync בכל שינוי state פנימי
- **V13.9**: OTA progress overlay + LCD feedback
- **V13.10**: OTA boot splash "SmartHydro Booting..."
- **V13.11**: OTA דרך HTTPUpdate library (HTTPS+redirects של GitHub)
- **V13.12**: נוריות חיווי פעילות (PH/EC/Temp green/red + fault blink)
- **V13.13-V13.17**: סדרת כיול EC — ניסיונות compensation, חזרה ל-auto-cal, K register
- **V13.18**: Serial diagnostic prints למעקב EC raw vs parsed
- **V13.19**: lcdPrintLine helper — תיקון תווים שרידים ב-LCD
- **V13.20**: ec_scale_factor תוכנתי + calibrateECScale ✅ מוכן לגינה
- **V13.21-V13.22**: UI חינמי בעברית RTL + תיקון UTF-8 Content-Type
- **V13.26**: 5 תיקונים מהגינה + עקביות Free/PRO
- **V13.27**: השוואת מחזורי גידול
- **V13.28**: כפתור שחרור נעילת משאבות בדף המקומי
- **V13.29**: Watchdog + Diagnostics — פתרון "בקר תקוע"
- **V13.30**: פיצוי טמפרטורה תוכנתי ל-EC (ATC, alpha=0.02)
- **V13.31**: Cycle archive עם merge — שמירת meta מ-PWA (name/rating/notes)
- **V13.34**: NTP retry יציב + Firebase watchdog 5 דק'
- **V13.35**: תיקון hardResetCycle → cycle_count=0
- **V13.49-V13.50**: כיול מיכל מודרך; latency: sensor 3s, ultrasonic 2s, cloud 5s
- **V13.51**: כיול מיכל בדף המקומי + לוג הזרקות במ"ל (לא שניות)
- **V13.52**: סינון תוכנתי לאולטרסוני (median filter + blind zone)
- **V13.53**: stuck-Firebase watchdog — reboot אחרי 10 דק' בלי push
- **V13.54**: מצב ידני אמיתי לברז מים (waterManualOverride)
- **V13.55**: מצב התייצבות אוטומטי 15-35 דק' אחרי מילוי מים
- **V13.56**: saveLog בכל הזרקה (תיקון: today_ph_sec לא נשמר ב-NVS)
- **V13.57**: Reboot Log — כל אתחול נשמר ב-`/reboot_log/{epoch}`

### V13.2 Test — `HydroSystem_V13.2_Test_MODBUS_SN001.ino.bin`
**בקר**: SN-001

גרסת בדיקה של Modbus — לבדיקות בלבד, לא production.

---

## V12.x — סדרת Debug/Official (SN001)

### V12.2.2 — `HydroSystem_V12.2.2_OFFICIAL_SN001.ino.bin`
**בקר**: SN-001

גרסה רשמית אחרונה לפני מעבר ל-Modbus. חיישנים אנלוגיים.

### V12.2.1 — `HydroSystem_V12.2.1_OFFICIAL_SN001.ino.bin`
**בקר**: SN-001

גרסה רשמית — תיקוני באגים מ-V12.2.

### V12.2 — `HydroSystem_V12.2_OFFICIAL_SN001.ino.bin`
**בקר**: SN-001

גרסה רשמית ראשונה — ייצוב אחרי שלב Debug.

### V12.1 DEBUG — `HydroSystem_V12.1_DEBUG_SN001.ino.bin`
**בקר**: SN-001

גרסת דיבאג — לוגים מורחבים ל-Serial. לא לשימוש production.

### V12.00 DEBUG — `HydroSystem_V12.00_DEBUG_SN001.ino.bin`
**בקר**: SN-001

גרסת דיבאג ראשונה של V12. מעבר לארכיטקטורה חדשה (לפני Modbus).

---

## V11.x — גרסאות אמצע (SN001 / SN002 / SN003)

### V11.9 — `HydroSystem_V11.9_SN001.ino.bin` / `HydroSystem_ver_11.9_SN002.ino.bin`
**בקרים**: SN-001, SN-002

גרסה אחרונה של V11 — שדרוגים לפני מעבר ל-V12.

### V11.8 — `HydroSystem_V11.8_SN001.ino.bin` / `HydroSystem_ver_11.8_SN002.ino.bin`
**בקרים**: SN-001, SN-002

שיפורי יציבות.

### V11.7 — `HydroSystem_ver_11.7_SN002.ino.bin`
**בקר**: SN-002

### V11.5 — `HydroSystem_ver_11.5_SN002.ino.bin`
**בקר**: SN-002

### V11.4 — `HydroSystem_ver_11.4_SN002.ino.bin`
**בקר**: SN-002

### V11.2 — `HydroSystem_V11.2_SN001.ino.bin` / `HydroSystem_ver_11.2_SN002.ino.bin`
**בקרים**: SN-001, SN-002

### V11.1 — `HydroSystem_V11.1_SN001.ino.bin` / `HydroSystem_ver_11.1_SN002.ino.bin`
**בקרים**: SN-001, SN-002

### V11.0 — `HydroSystem_V11.00_SN001.ino.bin` / `HydroSystem_ver_11.0_SN003.ino.bin`
**בקרים**: SN-001, SN-003

גרסת V11 ראשונה — חיישנים אנלוגיים, WiFi + Firebase, שרת מקומי.

---

## V10.x — גרסאות מוקדמות (SN003)

### V10.10 — `HydroSystem_ver_10.10_SN003.ino.bin`
**בקר**: SN-003

### V10.07 — `HydroSystem_ver_10.07_SN003.ino.bin`
**בקר**: SN-003

### V10.06 — `HydroSystem_ver_10.06_SN003.ino.bin`
**בקר**: SN-003

גרסאות מוקדמות — פיתוח ראשוני, חיישנים אנלוגיים בסיסיים.

---

## סיכום כללי לפי תקופות

| תקופה | גרסאות | חיישנים | תכונות עיקריות |
|---|---|---|---|
| מוקדם | V10.06–V10.10 | אנלוגיים | פיתוח ראשוני, בסיס |
| אמצע | V11.0–V11.9 | אנלוגיים | WiFi, Firebase, שרת מקומי, OTA |
| מעבר | V12.0–V12.2.2 | אנלוגיים | דיבאג, ייצוב, ארכיטקטורה חדשה |
| Modbus | V13.1–V13.57 | Modbus pH/EC | כיול, FreeRTOS, בטיחות, PWA מלא |
| נוכחי | **V14.0** | Modbus pH/EC | Midpoint-seeking, Smart Dilution, Tiered Alerts |
