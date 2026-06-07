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
import time
import urllib.parse
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# V25.30: heavy cloud SDKs imported lazily so the test harness can import
# this module without requiring firebase-admin / google-genai on the local
# machine. The production cron path (process_all_controllers) imports them
# inside _ensure_firebase / _ensure_gemini before any external call.
try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:
    firebase_admin = None  # type: ignore
    credentials = None     # type: ignore
    db = None              # type: ignore

try:
    from google import genai
except ImportError:
    genai = None  # type: ignore

try:
    import markdown
except ImportError:
    markdown = None  # type: ignore

# ============ Plant catalog access (V25.30) ============
# Previous PLANT_NAMES_HE was a hardcoded 30-entry dict — the LLM never saw
# `notes_he`, `family_he`, `cycle_days`, or per-stage pH/EC targets, so
# crop-specific insight was structurally impossible.
#
# `plants_catalog.py` (auto-generated from public/plants.js by
# scripts/build_plants_catalog.py) gives full 54-species context. See briefing
# §3.4 + §11.4 for the data we now surface to Gemini.
try:
    from plants_catalog import get_plant_context, PLANTS as _PLANTS_CATALOG
except ImportError:
    _PLANTS_CATALOG = {}
    def get_plant_context(plant_id, stage_id=None):  # type: ignore[no-redef]
        return None


def _short_plant_label(plants_list):
    """Compact one-line label for header / chart caption. Falls back to id
    when the plant is not in the catalog (shouldn't happen in normal use)."""
    if not plants_list:
        return None
    parts = []
    for p in plants_list:
        pid = p.get('id', '')
        qty = p.get('qty', 1)
        ctx = get_plant_context(pid, p.get('stage'))
        name = (ctx['name_he'] if ctx else pid)
        stage_label = (ctx['stage_label_he'] if ctx else p.get('stage', ''))
        parts.append(f"{name} ({stage_label}) ×{qty}")
    return ', '.join(parts)


# Kept for backward compatibility with the existing email-send call site that
# still asks for a plants_text label.
def format_plants_for_prompt(plants_list):
    return _short_plant_label(plants_list)


def _build_plants_section(plants_list):
    """Return the enriched per-plant block that goes into the LLM prompt.
    Each plant gets: name_he, family_he, cycle_days, qty, stage label,
    stage pH/EC targets, ec_critical, ph_critical, notes_he verbatim.
    Returns a multi-line Hebrew string."""
    if not plants_list:
        return '(לא הוגדרו צמחים — דווח על המים כללית, ללא תובנות אגרונומיות ספציפיות.)'
    lines = []
    for p in plants_list:
        pid = p.get('id', '')
        qty = p.get('qty', 1)
        stage_id = p.get('stage', 'all')
        ctx = get_plant_context(pid, stage_id)
        if not ctx:
            # Unknown plant — give the model just the id so it doesn't bluff
            lines.append(f"- {pid} ×{qty} (אין נתוני קטלוג — דווח רק על המדדים הגנריים).")
            continue
        ph_t = ctx['ph_target']
        ec_t = ctx['ec_target']
        ph_c = ctx['ph_critical']
        ph_range = f"{ph_t[0]}-{ph_t[1]}" if ph_t else '?'
        ec_range = f"{ec_t[0]}-{ec_t[1]}" if ec_t else '?'
        ec_crit = ctx['ec_critical'] or '?'
        ph_crit = f"{ph_c[0]}-{ph_c[1]}" if ph_c else '?'
        lines.append(
            f"- {ctx['name_he']} (משפחה: {ctx['family_he']}, מחזור טיפוסי: "
            f"{ctx['cycle_days']} ימים), {qty} צמחים, שלב: {ctx['stage_label_he']}.\n"
            f"    יעדים לשלב: pH {ph_range}, EC {ec_range} µS, EC קריטי {ec_crit} µS.\n"
            f"    pH קריטי: {ph_crit}.\n"
            f"    הערות אגרונומיות: {ctx['notes_he']}"
        )
    return '\n'.join(lines)


def _allowed_history_dates_set(history_entries):
    """Convert history list-of-tuples to a set of DD/MM strings for the
    date-integrity validator."""
    return {d.strftime('%d/%m') for d, _ in history_entries}


def build_consumption_chart_url(history_entries, targets=None):
    """V25.26: REPLACED bar chart with a trend line chart.
    Old: stacked bars of acid/nutrient consumption per day — mostly empty bars,
         a single spike dominated, useless visualization.
    New: line chart of pH and EC over time with target range bands.
         Customer instantly sees stability/drift relative to targets.

    Two Y axes: pH on the right (~5-7), EC on the left (~0-3000).
    Up to 30 most recent days. Returns None if no history.
    """
    if not history_entries:
        return None
    recent = history_entries[-30:]
    labels = [d.strftime('%d/%m') for d, _ in recent]
    ph_data = [round(float(data.get('ph_avg', 0) or 0), 2) for _, data in recent]
    ec_data = [int(data.get('ec_avg', 0) or 0) for _, data in recent]

    # Targets — show as dashed reference lines (optional)
    targets = targets or {}
    ph_min = targets.get('ph_min')
    ph_max = targets.get('ph_max')
    ec_min = targets.get('ec_min')
    ec_max = targets.get('ec_max')

    datasets = [
        {
            "label": "pH", "data": ph_data, "borderColor": "#3b82f6",
            "backgroundColor": "rgba(59,130,246,0.08)", "borderWidth": 3,
            "pointRadius": 3, "pointBackgroundColor": "#3b82f6",
            "yAxisID": "yPh", "fill": False, "tension": 0.3
        },
        {
            "label": "EC (µS)", "data": ec_data, "borderColor": "#10b981",
            "backgroundColor": "rgba(16,185,129,0.08)", "borderWidth": 3,
            "pointRadius": 3, "pointBackgroundColor": "#10b981",
            "yAxisID": "yEc", "fill": False, "tension": 0.3
        }
    ]

    # Add target range bands as faint dashed lines if available.
    # V25.30: only the upper bound gets a legend entry (single labeled trace
    # per axis). The lower bound is suppressed from the legend by routing it
    # to a "hidden" dataset technique — Chart.js v3 has no `filter:` string
    # support in QuickChart (must be a real function), so we drop the legend
    # filter and instead omit the redundant lower-bound trace from the legend
    # by giving it `showLine=True` but no label, and setting
    # `plugins.legend.labels.boxWidth=0` on the dataset via a workaround.
    # Pragmatic choice: just skip the lower-bound trace entirely. The customer
    # sees the upper "ceiling" dashed line which is the more useful guide
    # (tip-burn and lockout are upper-bound failures).
    if ph_max is not None:
        datasets.append({
            "label": f"תקרת pH ({ph_max})", "data": [ph_max] * len(labels),
            "borderColor": "rgba(59,130,246,0.35)", "borderDash": [4, 4],
            "borderWidth": 1, "pointRadius": 0, "yAxisID": "yPh", "fill": False
        })
    if ec_max is not None:
        datasets.append({
            "label": f"תקרת EC ({ec_max} µS)", "data": [ec_max] * len(labels),
            "borderColor": "rgba(16,185,129,0.35)", "borderDash": [4, 4],
            "borderWidth": 1, "pointRadius": 0, "yAxisID": "yEc", "fill": False
        })

    # V25.30: Chart.js v3+ schema — QuickChart upgraded and the old v2 syntax
    # (yAxes array, scaleLabel, gridLines, legend.labels.filter as string)
    # now returns HTTP 400. Equivalent v3 mapping:
    #   yAxes: [{id:'yPh'}]            → scales: {yPh: {...}}
    #   scaleLabel: {display, label}   → title: {display, text}
    #   gridLines: {drawOnChartArea}   → grid: {drawOnChartArea}
    #   top-level title / legend       → plugins.title / plugins.legend
    chart_config = {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "plugins": {
                "title": {"display": True, "text": "מגמת pH ו-EC לאורך המחזור",
                          "font": {"size": 16}},
                "legend": {"position": "bottom"}
            },
            "scales": {
                "yPh": {"position": "right",
                        "title": {"display": True, "text": "pH"},
                        "min": 4, "max": 8,
                        "ticks": {"stepSize": 0.5},
                        "grid": {"drawOnChartArea": False}},
                "yEc": {"position": "left",
                        "title": {"display": True, "text": "EC (µS)"},
                        "min": 0,
                        "ticks": {"stepSize": 500}},
                "x": {"title": {"display": True, "text": "תאריך"}}
            }
        }
    }
    # Each dataset's yAxisID also moves: in v3 the key matches scales keys.
    for ds in datasets:
        if ds.get("yAxisID") == "yPh":
            ds["yAxisID"] = "yPh"
        elif ds.get("yAxisID") == "yEc":
            ds["yAxisID"] = "yEc"
    encoded = urllib.parse.quote(json.dumps(chart_config, ensure_ascii=False))
    return f"https://quickchart.io/chart?c={encoded}&w=640&h=360&bkg=white&v=4"

# --- משתני סביבה (GitHub Actions secrets) ---
# V25.30: lazy initialization — Firebase and Gemini clients are created on
# first use, not at import. Lets the test harness in scripts/test_reports.py
# import ai_manager without supplying Firebase credentials. The production
# cron path (process_all_controllers) still calls _ensure_clients() before
# any external calls.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")
SENDER_EMAIL = os.getenv("GMAIL_USER")
SENDER_PASSWORD = os.getenv("GMAIL_PASS")

client = None  # genai.Client — initialized lazily


def _ensure_firebase():
    """Initialize Firebase Admin once. Raises if FIREBASE_SERVICE_ACCOUNT is
    missing — production runs always have it via GitHub Actions secrets."""
    if firebase_admin is None:
        raise RuntimeError("firebase-admin not installed; install with "
                           "`pip install firebase-admin` for production use.")
    if firebase_admin._apps:
        return
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT env var is required")
    print("Connecting to Firebase...")
    cred = credentials.Certificate(json.loads(raw))
    firebase_admin.initialize_app(cred, {'databaseURL': DATABASE_URL})


def _ensure_gemini():
    """Create the Gemini client on first call. Raises if GEMINI_API_KEY is
    missing — caller decides how to surface that to the user."""
    global client
    if client is not None:
        return client
    if genai is None:
        raise RuntimeError("google-genai not installed; install with "
                           "`pip install google-genai` to run live reports.")
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY env var is required")
    print("Connecting to Gemini...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client


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


# V25.30: Insight-driven report generator with automated quality gates.
#
# Why the rewrite (full context in REPORTS_AGENT_BRIEFING.md):
# - The previous prompt was monolithic (~400 lines mixing role, rules,
#   forbidden examples, insight examples, output template, all crammed into
#   one f-string). The LLM prioritized the wrong sections.
# - Plant context lost — only the plant name was passed. notes_he,
#   family_he, cycle_days, per-stage targets, ec_critical never reached
#   the model.
# - No post-generation validation — reports with forbidden phrases shipped
#   to customers anyway. Five iterations of prompt edits failed to fix
#   this because the failure mode was structural, not textual.
#
# The new flow:
#   1. Build a modular prompt: anchored date/cycle (Python-computed),
#      enriched per-plant block (catalog-driven), targets context, history,
#      style-specific output template demanding **[Category]** tags on
#      every insight.
#   2. Call _gemini_generate_with_fallback (3-model chain, unchanged).
#   3. Run 5 quality validators from scripts/report_validators.py.
#   4. On any test fail, regenerate with a targeted hint (max 3 attempts).
#   5. If all attempts fail → return a hand-written degraded report
#      ("no insights available today") rather than ship bad output.
#
# Validation runs are logged so we can track which tests fail most often
# and tune the prompt over time.
_MAX_REGEN_ATTEMPTS = 3

# Allow `from scripts.report_validators import ...` regardless of where the
# script is invoked from. The Hydro-OTA deployment ships scripts/ alongside.
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402
_SCRIPTS_DIR = _Path(__file__).resolve().parent / 'scripts'
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from report_validators import run_all as _validate_run_all  # type: ignore
except Exception:
    _validate_run_all = None  # validators missing → skip gates, log a warning


HEBREW_WEEKDAYS = ['שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון']


def _format_history_compact(entries, last_n=14):
    """Last N days of history, one line per day. Saves tokens vs full cycle."""
    if not entries:
        return '(אין עדיין נתונים יומיים מאז תחילת המחזור)'
    recent = entries[-last_n:]
    lines = []
    for d, data in recent:
        ph = data.get('ph_avg', 0) or 0
        ec = int(data.get('ec_avg', 0) or 0)
        t = data.get('temp_avg', 0) or 0
        lines.append(f"[{d.strftime('%d/%m')}] pH={ph:.2f}, EC={ec}, T={t:.1f}°C")
    return '\n'.join(lines)


def _build_prompt(*, today_dt, cycle_start_dt, cycle_count, days_into_cycle,
                  settings, history_entries, report_style, retry_hint=None):
    """Assemble the modular prompt. retry_hint is appended only on regeneration
    attempts after a validation failure."""
    today_iso = today_dt.strftime('%Y-%m-%d')
    today_dmy = today_dt.strftime('%d/%m/%Y')
    weekday_he = HEBREW_WEEKDAYS[today_dt.weekday()]
    cycle_str = cycle_start_dt.strftime('%d/%m/%Y') if cycle_start_dt else 'לא הוגדר'

    plants_list = settings.get('plants', []) if isinstance(settings, dict) else []
    plants_section = _build_plants_section(plants_list)

    targets_auto_or_manual = ('יעדים שהוגדרו ידנית על ידי המגדל '
                              '(override של בחירת הצמח)'
                              if settings.get('targets_manual_override')
                              else 'יעדים אוטומטיים על-פי בחירת הצמח')

    today_summary = ''
    if history_entries:
        d, data = history_entries[-1]
        today_summary = (f"pH {data.get('ph_avg', 0):.2f} | "
                         f"EC {int(data.get('ec_avg', 0) or 0)} µS | "
                         f"טמפ' {data.get('temp_avg', 0):.1f}°C")

    total_ph_sec = sum(int(d.get('ph_sec_total', 0) or 0) for _, d in history_entries)
    total_ec_sec = sum(int(d.get('ec_sec_total', 0) or 0) for _, d in history_entries)

    rules_block = """=== חוקים נוקשים ===
אסור להשתמש בביטויים הבאים — המגדל רואה אותם באפליקציה:
- "בטווח התקין" / "ערכים תקינים" / "מצב תקין"
- "המערכת תקינה" / "המערכת פועלת כראוי"
- "המשך בניטור" / "עקוב אחרי" / "מומלץ לבדוק"
- "בדוק את תקינות החיישן" (ללא נימוק ספציפי מהנתונים)
- "אמת את הרצף" / "הקפד על תיעוד"
- "אולי" / "כדאי" (חסר החלטיות — אסור בפעולות)

כל תובנה חייבת לפתוח בתג קטגוריה באחד מ-5 הסוגים, בדיוק בתבנית הזו:
**[Trend]**: קצב שינוי מספרי + תאריך/ערך עתידי + פעולה
**[Correlation]**: שני מדדים + עוצמת קשר + הסבר ביולוגי
**[Stage]**: יום במחזור + שם הצמח + מאפיין השלב + אירוע צפוי
**[Agronomy]**: שם הצמח + עובדה ספציפית + קשר לנתון הנוכחי
**[Anomaly]**: תאריך ספציפי + ערך + הסבר סביר"""

    anchors_block = f"""=== עוגני תאריך ומחזור ===
תאריך היום: {today_dmy} ({weekday_he}, ISO {today_iso}).
מחזור #{cycle_count}, יום {days_into_cycle} מתחילת המחזור (החל {cycle_str}).
אל תחשב מחדש את היום. אל תמציא תאריך אחר."""

    plants_block = f"""=== צמחים במחזור ===
{plants_section}"""

    targets_block = f"""=== יעדים נוכחיים ===
pH {settings.get('ph_min', '?')}-{settings.get('ph_max', '?')} | EC {settings.get('ec_min', '?')}-{settings.get('ec_max', '?')} µS | טמפ׳ {settings.get('temp_min', '?')}-{settings.get('temp_max', '?')}°C
({targets_auto_or_manual})"""

    readings_block = f"""=== נתוני היום ===
{today_summary if today_summary else '(אין עדיין מדידה יומית להיום)'}
צריכה מצטברת במחזור: חומצה pH — {total_ph_sec} שניות, דשן EC — {total_ec_sec} שניות."""

    history_block = f"""=== היסטוריה (עד 14 ימים אחרונים) ===
{_format_history_compact(history_entries)}"""

    if report_style == 'brief':
        task_block = """=== משימה ===
סגנון: תקציר יומי. מבנה חובה (6-10 שורות בלבד):

## ✓ או ⚠ + סיכום סטטוס במשפט אחד
[שורה ריקה]
pH: X.XX ← מילה אחת על המגמה (יציב/עולה/יורד)
[שורה ריקה]
EC: XXXX µS ← מילה אחת על המגמה
[שורה ריקה]
טמפ': XX.X°C ← מילה אחת על המגמה
[שורה ריקה]
**[Trend|Agronomy|Stage|Correlation|Anomaly]**: תובנה אחת מבוססת מספרים. עד 18 מילים.
[שורה ריקה]
**פעולה**: פעולה ספציפית עם מספר. אם באמת אין צורך — "אין פעולה נדרשת — קצב הצריכה תואם את הצפוי".

פלט: רק Markdown בעברית. בלי הקדמה. בלי סיכום מסביב."""
    else:
        task_block = """=== משימה ===
סגנון: דוח אגרונומי. מבנה חובה — בדיוק 4 חלקים בסדר הזה:

## 1. סטטוס נוכחי

| מדד | היום | אתמול | יעד | סטטוס |
|---|---|---|---|---|
| pH | X.XX | X.XX | X.X-X.X | ✓ או ⚠ |
| EC | XXXX | XXXX | XXXX-XXXX | ✓ או ⚠ |
| טמפ' | XX.X | XX.X | XX-XX | ✓ או ⚠ |

טבלה בלבד. בלי משפטים סביב.

## 2. תובנות

2-3 תובנות, **כל אחת בקטגוריה שונה**. כל תובנה במשפט אחד-שניים, מתחילה בתג:

**[Trend]**: ...

**[Correlation]**: ...

**[Stage]** או **[Agronomy]** או **[Anomaly]**: ...

## 3. תחזית 3-5 ימים

1-2 משפטים. **כל משפט חייב לכלול תאריך ספציפי או מספר ימים**. בלי "אולי" ו"כדאי".

## 4. פעולות

1-3 פעולות. כל פעולה בשורה ממוספרת, מתחילה בפועל בציווי (הוסף / הפעל / הכן / בצע) + נימוק מספרי מהנתונים.
אם אין צורך בפעולה — בשורה אחת: "אין פעולות נדרשות — קצב הצריכה תואם את הצפוי".

פלט: רק Markdown בעברית. בלי הקדמה. בלי סיכום."""

    # Tighten the rules when the cycle is too young for trend extrapolation
    young_cycle_note = ''
    if isinstance(days_into_cycle, int) and days_into_cycle < 4:
        young_cycle_note = (
            "\n\n=== הערה על המחזור ===\n"
            f"המחזור צעיר ({days_into_cycle} ימים). אסור להמציא מגמת קצב — "
            "השתמש בקטגוריות **[Agronomy]** או **[Stage]** עם עובדות מהקטלוג, "
            "ובחלק התחזית כתוב במפורש שאין מספיק נתונים לחיזוי קצב."
        )

    role = ("אתה אגרונום הידרופוניקה ותיק (20 שנות ניסיון) הכותב דוח יומי "
            "למגדל בישראל שמשלם על המנוי. הדוח חייב לתת תובנה אגרונומית או "
            "חיזוי שלא נראה באפליקציה — לא לחזור על מה שכבר מוצג שם.")

    retry_appendix = ''
    if retry_hint:
        retry_appendix = (
            "\n\n=== תיקון מהניסיון הקודם ===\n"
            f"הפלט הקודם שלך נכשל בבדיקה אוטומטית: {retry_hint}\n"
            "תקן את הנקודה הזו בלבד. שמור על אותו מבנה."
        )

    return '\n\n'.join([
        role,
        rules_block,
        anchors_block,
        plants_block,
        targets_block,
        readings_block,
        history_block,
        task_block,
    ]) + young_cycle_note + retry_appendix


def _first_failure_hint(validate_result):
    """Pick the most useful failed-test reason to feed back to the LLM."""
    if not validate_result:
        return None
    for name, ok, reason in validate_result['results']:
        if not ok:
            return f"{name} — {reason}"
    return None


def _degraded_report(today_dt, days_into_cycle, history_entries, report_style):
    """Hand-written fallback when 3 LLM attempts all fail validation.
    Better to ship a short honest message than a forbidden-pattern wall."""
    today_dmy = today_dt.strftime('%d/%m/%Y')
    today_metric = ''
    if history_entries:
        d, data = history_entries[-1]
        today_metric = (f"pH {data.get('ph_avg', 0):.2f} | "
                        f"EC {int(data.get('ec_avg', 0) or 0)} µS | "
                        f"טמפ' {data.get('temp_avg', 0):.1f}°C")
    if report_style == 'brief':
        return (
            f"## ℹ דוח קצר ({today_dmy})\n\n"
            f"{today_metric}\n\n"
            f"יום {days_into_cycle} למחזור.\n\n"
            "**[Agronomy]**: לא הצלחנו להפיק תובנה אגרונומית איכותית להיום. "
            "נחזור עם דוח מלא מחר.\n\n"
            "**פעולה**: אין פעולה נדרשת היום.\n"
        )
    return (
        f"## 1. סטטוס נוכחי\n\n{today_metric}\n\n"
        "## 2. תובנות\n\n"
        "**[Agronomy]**: לא הצלחנו להפיק תובנה אגרונומית איכותית להיום.\n\n"
        "**[Stage]**: יום " + str(days_into_cycle) + " למחזור — נחזור עם דוח מלא מחר.\n\n"
        "## 3. תחזית 3-5 ימים\n\n"
        "אין תחזית להיום. הדוח יתחדש אוטומטית בריצה הבאה.\n\n"
        "## 4. פעולות\n\n"
        "אין פעולות נדרשות — קצב הצריכה תואם את הצפוי.\n"
    )


def generate_report(controller_id, settings, history_entries, cycle_start_dt,
                    cycle_count, device_name=None, report_style='brief',
                    today_dt=None):
    """Generate a daily report with automated quality gates.

    today_dt: optional override for the date anchor (test harness uses this
              to simulate any day; production calls leave it None and we use
              datetime.now())."""
    today_dt = today_dt or datetime.now()
    days_into_cycle = ((today_dt.date() - cycle_start_dt.date()).days
                       if cycle_start_dt else 0)
    today_iso = today_dt.strftime('%Y-%m-%d')
    allowed_dates = _allowed_history_dates_set(history_entries)

    retry_hint = None
    last_md = None
    last_validate = None
    for attempt in range(1, _MAX_REGEN_ATTEMPTS + 1):
        prompt = _build_prompt(
            today_dt=today_dt,
            cycle_start_dt=cycle_start_dt,
            cycle_count=cycle_count,
            days_into_cycle=days_into_cycle,
            settings=settings if isinstance(settings, dict) else {},
            history_entries=history_entries,
            report_style=report_style,
            retry_hint=retry_hint,
        )
        try:
            response = _gemini_generate_with_fallback(prompt)
            md = response.text
        except Exception as e:
            print(f"  [{controller_id}] generation attempt {attempt} raised: {e}")
            continue

        if _validate_run_all is None:
            # Validators missing — ship whatever we got, log a warning.
            print(f"  [{controller_id}] WARNING: validators unavailable, "
                  "skipping quality gates.")
            return md

        validate = _validate_run_all(
            md,
            today_iso=today_iso,
            style=report_style,
            allowed_dates=allowed_dates,
        )
        last_md = md
        last_validate = validate
        if validate['overall_pass']:
            print(f"  [{controller_id}] attempt {attempt}: PASS all 5 tests.")
            return md
        retry_hint = _first_failure_hint(validate)
        print(f"  [{controller_id}] attempt {attempt}: FAIL — {retry_hint}")

    # All attempts exhausted.
    print(f"  [{controller_id}] ALL {_MAX_REGEN_ATTEMPTS} ATTEMPTS FAILED — "
          "shipping degraded report.")
    if last_md and last_validate:
        # Optional: log full failure detail for debugging
        for name, ok, reason in last_validate['results']:
            if not ok:
                print(f"    final fail: {name} — {reason}")
    return _degraded_report(today_dt, days_into_cycle, history_entries, report_style)


# V25.27: Multi-model fallback chain.
# Each model has its own retry-with-backoff. If one model completely exhausts retries,
# we move to the next model in the chain. The 3 models share similar quality for
# agronomic reports but draw from DIFFERENT capacity pools at Google's side, so they
# rarely all go down at once.
#
# Models in priority order:
#   1. gemini-2.5-flash      — primary (best balance)
#   2. gemini-2.0-flash      — different generation, separate capacity pool
#   3. gemini-2.5-flash-lite — cheaper sibling, separate quota bucket
#
# V25.30: gemini-1.5-flash was removed from the chain — Google deprecated it
# from the v1beta endpoint (returns 404 NOT_FOUND for generateContent).
# Replaced with gemini-2.5-flash-lite which is cheaper and shares the 2.5
# family's prompt understanding so the same prompt works without re-tuning.
#
# Free-tier limits per model are independent, so this also TRIPLES our daily quota.
_FALLBACK_MODEL_CHAIN = [
    'gemini-2.5-flash',
    'gemini-2.0-flash',
    'gemini-2.5-flash-lite',
]


def _gemini_generate_with_fallback(prompt):
    """Try each model in the chain. Returns response from first success.
    Raises last exception only if ALL models fail across ALL retries.
    """
    last_exc = None
    for i, model in enumerate(_FALLBACK_MODEL_CHAIN):
        try:
            print(f"  Trying model {i+1}/{len(_FALLBACK_MODEL_CHAIN)}: {model}")
            return _gemini_generate_with_retry(prompt, model=model)
        except Exception as e:
            last_exc = e
            err_str = str(e)[:150]
            is_last_model = (i == len(_FALLBACK_MODEL_CHAIN) - 1)
            if is_last_model:
                print(f"  ALL MODELS EXHAUSTED — final failure on {model}: {err_str}")
                raise
            print(f"  Model {model} exhausted retries: {err_str}")
            print(f"  Falling through to next model in chain...")
    # Defensive — should never reach here
    if last_exc:
        raise last_exc
    raise RuntimeError("Fallback chain exited unexpectedly")


# V25.24: Retry transient Gemini errors (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED, etc.)
# Without this, the daily cron at 06:00 UTC fails silently when Google has a load spike,
# and customers lose their daily report for that day with no explanation.
def _gemini_generate_with_retry(prompt, model='gemini-2.5-flash', max_attempts=3):
    """Call a SPECIFIC model with exponential backoff on transient errors.
    Delays: 5s -> 15s -> 30s. Total worst case per model: ~50s.
    Raises the last exception if all attempts fail.
    """
    transient_markers = ('503', '429', 'UNAVAILABLE', 'RESOURCE_EXHAUSTED',
                         'DEADLINE_EXCEEDED', 'timeout', 'Timeout')
    delays = [5, 15, 30]  # seconds between attempts

    last_exc = None
    gemini = _ensure_gemini()
    for attempt in range(max_attempts):
        try:
            return gemini.models.generate_content(model=model, contents=prompt)
        except Exception as e:
            last_exc = e
            err_str = str(e)
            is_transient = any(m in err_str for m in transient_markers)
            is_last = (attempt == max_attempts - 1)
            if not is_transient or is_last:
                # Permanent error OR ran out of attempts — give up THIS model
                print(f"    [{model}] FAILED (attempt {attempt+1}/{max_attempts}): {err_str[:200]}")
                raise
            delay = delays[attempt]
            print(f"    [{model}] transient error (attempt {attempt+1}/{max_attempts}), "
                  f"retrying in {delay}s: {err_str[:120]}")
            time.sleep(delay)
    # Should never reach here but for safety:
    raise last_exc if last_exc else RuntimeError("Retry loop exited unexpectedly")


def _split_sentences_to_paragraphs(md_text):
    """V25.28: split each line containing multiple sentences into separate paragraphs.
    Sentence boundary: '.', '!', '?' or ':' followed by space + Hebrew/Latin letter.
    Avoids splitting decimals like '1.5' (lookbehind requires non-digit).
    Skips tables, headings, list items, code blocks.
    Output: each sentence on its own line, with blank line between → renders as
    separate <p> tags in Markdown.
    """
    import re
    out_blocks = []
    for line in md_text.split('\n'):
        stripped = line.lstrip()
        # Skip non-paragraph lines
        if (not stripped or stripped.startswith('|') or stripped.startswith('#') or
                stripped.startswith('- ') or stripped.startswith('* ') or
                stripped.startswith('```') or
                re.match(r'^\d+\.\s', stripped)):
            out_blocks.append(line)
            continue
        # Split at sentence boundaries
        # Pattern: (non-digit char)(. or : or ! or ?)(space)(Hebrew or uppercase Latin letter)
        sentences = re.split(
            r'(?<=[א-תa-zA-Z\)\]])([\.\:\!\?])\s+(?=[א-תA-Z])',
            line
        )
        # Re-join keeping the delimiters
        if len(sentences) <= 1:
            out_blocks.append(line)
            continue
        result = []
        for i in range(0, len(sentences), 2):
            sent = sentences[i]
            delim = sentences[i+1] if i+1 < len(sentences) else ''
            full = sent + delim
            if full.strip():
                result.append(full.strip())
        # Each sentence becomes its own paragraph (blank line between)
        out_blocks.append('\n\n'.join(result))
    return '\n'.join(out_blocks)


def _polish_html_for_email(html):
    """V25.28: post-process Markdown-rendered HTML to bulletproof email-client display.
    - Force every <table> to width=584, table-layout:fixed, border-collapse:collapse
    - Add explicit max-width on <p> and <li>
    - Wrap loose text after periods with <br> (defensive — should already be split)
    """
    import re
    # Force tables to behave inside the 640px container
    html = re.sub(
        r'<table>',
        '<table width="584" cellpadding="6" cellspacing="0" border="0" style="width:584px;max-width:584px;border-collapse:collapse;table-layout:fixed;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;margin:12px 0;">',
        html
    )
    html = re.sub(
        r'<th>',
        '<th style="background:#ede9fe;color:#5b21b6;padding:10px 8px;text-align:right;font-weight:700;font-size:13px;border-bottom:2px solid #ddd6fe;word-wrap:break-word;">',
        html
    )
    html = re.sub(
        r'<td>',
        '<td style="padding:10px 8px;text-align:right;font-size:13px;border-bottom:1px solid #e5e7eb;word-wrap:break-word;vertical-align:top;">',
        html
    )
    # Make headings prominent
    html = re.sub(
        r'<h2>',
        '<h2 style="color:#6d28d9;font-size:18px;font-weight:700;margin:24px 0 12px 0;padding:8px 12px;background:#f5f3ff;border-right:4px solid #6d28d9;border-radius:6px;">',
        html
    )
    html = re.sub(
        r'<h3>',
        '<h3 style="color:#374151;font-size:15px;font-weight:700;margin:16px 0 8px 0;">',
        html
    )
    # Paragraphs: explicit width + line height + spacing
    html = re.sub(
        r'<p>',
        '<p style="margin:8px 0;font-size:14px;line-height:1.7;color:#1f2937;max-width:584px;">',
        html
    )
    # List items: card style + numbered badges
    html = re.sub(
        r'<ol>',
        '<ol style="padding:0;margin:14px 0;list-style:none;counter-reset:rec;">',
        html
    )
    html = re.sub(
        r'<ul>',
        '<ul style="padding:0;margin:14px 0;list-style:none;">',
        html
    )
    html = re.sub(
        r'<li>',
        '<li style="background:#f9fafb;border-right:3px solid #8b5cf6;border-radius:6px;padding:12px 16px;margin:8px 0;font-size:14px;line-height:1.7;color:#1f2937;max-width:584px;">',
        html
    )
    # Strong: brand color
    html = re.sub(
        r'<strong>',
        '<strong style="color:#5b21b6;font-weight:700;">',
        html
    )
    return html


def send_report_email(client_email, controller_id, report_md, cycle_count, days_into_cycle, chart_url=None, plants_text=None, device_name=None):
    # V25.28: aggressive sentence-splitting + HTML inline-styling.
    # Step 1: split multi-sentence paragraphs into separate Markdown paragraphs.
    report_md = _split_sentences_to_paragraphs(report_md)
    # Step 2: convert Markdown to HTML. tables=Markdown tables, nl2br=NOT here
    #         (we use \n\n paragraph breaks instead, more reliable).
    html_body = markdown.markdown(report_md, extensions=['tables'])
    # Step 3: inject inline styles on EVERY tag because Outlook ignores <style> tags
    html_body = _polish_html_for_email(html_body)
    friendly_name = device_name if device_name else "SmartHydro"
    # V25.25: email-client-safe chart row (table-based, not div)
    chart_html = ""
    if chart_url:
        chart_html = f"""
        <tr>
          <td width="640" style="width:640px;padding:20px 24px;background-color:#fafafa;border-top:1px solid #e5e7eb;text-align:center;">
            <div style="font-size:13px;font-weight:bold;color:#374151;margin-bottom:10px;">📈 מגמת pH ו-EC לאורך המחזור</div>
            <img src="{chart_url}" alt="גרף מגמה" width="592" style="width:592px;max-width:592px;height:auto;border:1px solid #e5e7eb;border-radius:8px;display:block;margin:0 auto;">
            <div style="font-size:10px;color:#6b7280;margin-top:8px;line-height:1.5;">קווים מלאים: ערך יומי. קווים מקווקווים: גבולות היעד.</div>
          </td>
        </tr>"""
    else:
        chart_html = """
        <tr>
          <td width="640" style="width:640px;padding:14px;background-color:#fafafa;border-top:1px solid #e5e7eb;text-align:center;color:#9ca3af;font-size:11px;">
            📈 גרף מגמה יופיע כשיצטברו נתונים יומיים.
          </td>
        </tr>"""

    # V25.27: plants row at locked 640px width
    plants_html_row = ""
    if plants_text:
        plants_html_row = f"""
        <tr>
          <td width="640" bgcolor="#f0fdf4" style="width:640px;background-color:#f0fdf4;padding:10px 24px;border-bottom:1px solid #bbf7d0;text-align:right;font-size:12px;">
            <span style="color:#15803d;font-weight:bold;">🌱 גידול נוכחי:</span>
            <span style="color:#166534;"> {plants_text}</span>
          </td>
        </tr>"""

    # V25.28: Two-layer wrapper (industry standard for email).
    # Layer 1: 100%-wide table with light-gray background (visible "page" around content)
    # Layer 2: 640px-wide content table centered inside, with visible frame
    # ALL inline styles — no <style> tag, no @media. Outlook respects this 100%.
    html_template = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<title>דוח אגרונומי</title>
</head>
<body style="margin:0;padding:0;background-color:#e5e7eb;font-family:'Heebo','Segoe UI',Arial,sans-serif;direction:rtl;">

  <!-- Layer 1: outer 100% width with page background -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#e5e7eb" style="background-color:#e5e7eb;">
    <tr>
      <td align="center" valign="top" style="padding:20px 10px;">

        <!-- Layer 2: 640px content with visible frame -->
        <table align="center" cellpadding="0" cellspacing="0" border="0" width="640" style="width:640px;max-width:640px;background-color:#ffffff;border:2px solid #6d28d9;border-radius:12px;">

          <!-- Header (centered, prominent) -->
          <tr>
            <td width="640" bgcolor="#6d28d9" style="width:640px;background-color:#6d28d9;color:#ffffff;padding:20px;text-align:center;">
              <div style="font-size:20px;font-weight:700;color:#ffffff;margin-bottom:6px;">📊 דוח אגרונומי יומי</div>
              <div style="font-size:13px;color:#ffffff;">{friendly_name}</div>
              <div style="font-size:12px;color:#ddd6fe;margin-top:4px;">מחזור #{cycle_count} &middot; יום {days_into_cycle}</div>
            </td>
          </tr>

          {plants_html_row}

          <!-- Content -->
          <tr>
            <td width="640" style="width:640px;padding:20px 24px;font-family:'Heebo','Segoe UI',Arial,sans-serif;color:#1f2937;font-size:14px;line-height:1.75;text-align:right;direction:rtl;word-break:break-word;">
              {html_body}
            </td>
          </tr>

          {chart_html}

          <!-- Footer -->
          <tr>
            <td width="640" bgcolor="#f5f3ff" style="width:640px;background-color:#f5f3ff;padding:14px;text-align:center;font-size:11px;color:#6d28d9;border-top:1px solid #ddd6fe;">
              &copy; 2026 SmartHydro Systems
            </td>
          </tr>
        </table>

      </td>
    </tr>
  </table>

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


def get_email_prefs_by_email(target_email, all_users=None):
    """PWA v25: מוצא העדפות מייל לפי כתובת. מחזיר dict או None.
    all_users: optional cache - אם מועבר, חוסך קריאה חוזרת."""
    if not target_email:
        return None
    target = target_email.strip().lower()
    try:
        if all_users is None:
            all_users = db.reference('users').get() or {}
        for uid, user_data in (all_users or {}).items():
            if not isinstance(user_data, dict):
                continue
            prefs = user_data.get('email_prefs')
            if not isinstance(prefs, dict):
                continue
            user_email = (prefs.get('email') or '').strip().lower()
            if user_email == target:
                return prefs
    except Exception as e:
        print(f"get_email_prefs_by_email error: {e}")
    return None


def should_send_today(prefs, now_dt=None):
    """PWA v25: האם לשלוח דוח היום לפי email_prefs?
    Returns: (should_send: bool, reason: str)
    """
    if prefs is None:
        # ברירת מחדל לחשבונות ישנים שלא הגדירו - כן שלח (התנהגות קיימת)
        return (True, 'no_prefs_default_send')
    if not prefs.get('enabled', False):
        return (False, 'disabled_by_user')
    freq = prefs.get('frequency', 'daily')
    if freq == 'weekly':
        # שלח רק ביום ראשון (weekday()==6 ב-Python, או isoweekday()==7)
        now_dt = now_dt or datetime.now()
        if now_dt.isoweekday() != 7:
            return (False, f'weekly_not_sunday_today={now_dt.isoweekday()}')
    return (True, 'ok')


def process_all_controllers():
    _ensure_firebase()
    print("Fetching all controllers from Firebase...")
    all_controllers = db.reference('controllers').get() or {}
    if not all_controllers:
        print("No controllers found.")
        return

    # PWA v25: cache של כל ה-users לחיפוש email_prefs (חוסך N קריאות)
    try:
        all_users = db.reference('users').get() or {}
    except Exception:
        all_users = {}

    now_ts = datetime.now().timestamp()
    today_str = datetime.now().strftime('%Y-%m-%d')
    run_start_ts = now_ts

    # V25.24: monitoring counters — saved to RTDB + alert if error rate high
    stats = {
        'total_controllers': len(all_controllers),
        'sent_ok': 0,
        'gemini_errors': 0,
        'tier_blocked': 0,
        'expired': 0,
        'offline': 0,
        'no_owner_email': 0,
        'opted_out': 0,
        'email_errors': 0,
        'rtdb_save_errors': 0,
    }

    for controller_id, data in all_controllers.items():
        print(f"\n[{controller_id}] -----------------------------------")
        if not isinstance(data, dict):
            continue

        # 1. סינון לפי tier (subscription/tier; ברירת מחדל = free)
        sub = data.get('subscription', {}) if isinstance(data.get('subscription'), dict) else {}
        tier = sub.get('tier', 'free')
        if tier not in REPORT_TIERS:
            print(f"[{controller_id}] Skipped: tier={tier}")
            stats['tier_blocked'] += 1
            continue
        # תוקף פג? עדיין נשלח דוח אם זה הוא יום התפוגה (לקוח אמור לקבל אישור עד הרגע האחרון)
        expires_at = sub.get('expiresAt', 0)
        if expires_at and expires_at < now_ts:
            print(f"[{controller_id}] Skipped: subscription expired")
            stats['expired'] += 1
            continue

        # 2. בדיקת דופק (realtime/lastUpdate בתוך 24h אחרונות)
        realtime = data.get('realtime', {}) if isinstance(data.get('realtime'), dict) else {}
        last_update = realtime.get('lastUpdate', 0) or 0
        if now_ts - last_update > 86400:
            print(f"[{controller_id}] Skipped: offline (last_update={last_update})")
            stats['offline'] += 1
            continue

        # 3. owner email
        owner_email = data.get('owner_email') or ''
        if not owner_email:
            print(f"[{controller_id}] Skipped: no owner_email")
            stats['no_owner_email'] += 1
            continue

        # PWA v25: בדיקת email_prefs של המשתמש
        email_prefs = get_email_prefs_by_email(owner_email, all_users=all_users)
        should_send, reason = should_send_today(email_prefs)
        if not should_send:
            print(f"[{controller_id}] Skipped email (per user prefs): {reason}")
            stats['opted_out'] += 1
            continue
        report_style = (email_prefs or {}).get('style', 'brief')

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
            report_md = generate_report(controller_id, settings, history, cycle_start_dt, cycle_count,
                                        device_name=device_name, report_style=report_style)
        except Exception as e_gen:
            print(f"[{controller_id}] Gemini error: {e_gen}")
            stats['gemini_errors'] += 1
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
            stats['rtdb_save_errors'] += 1

        # 8. שליחת email – עם גרף צריכה ומידע על צמחים
        try:
            days_into_cycle = (datetime.now().date() - cycle_start_dt.date()).days if cycle_start_dt else 0
            settings_for_chart = data.get('settings', {}) if isinstance(data.get('settings'), dict) else {}
            chart_targets = {
                'ph_min': settings_for_chart.get('ph_min'),
                'ph_max': settings_for_chart.get('ph_max'),
                'ec_min': settings_for_chart.get('ec_min'),
                'ec_max': settings_for_chart.get('ec_max'),
            }
            chart_url = build_consumption_chart_url(history, targets=chart_targets)
            plants_text = format_plants_for_prompt(settings.get('plants', []) if isinstance(settings, dict) else [])
            send_report_email(owner_email, controller_id, report_md, cycle_count, days_into_cycle,
                              chart_url=chart_url, plants_text=plants_text, device_name=device_name)
            print(f"[{controller_id}] Report sent to {owner_email} (chart={'yes' if chart_url else 'no'})")
            stats['sent_ok'] += 1
        except Exception as e_email:
            print(f"[{controller_id}] Email error: {e_email}")
            stats['email_errors'] += 1

    # V25.24: monitoring — save run stats to Firebase + alert if errors are high
    _save_and_alert_run_stats(stats, run_start_ts)


def _save_and_alert_run_stats(stats, run_start_ts):
    """Persist run statistics to Firebase RTDB and send admin alert if error rate exceeds threshold.
    Saves to system/ai_manager_stats/last (overwritten each run) AND
    system/ai_manager_stats/history/{YYYY-MM-DD} (one entry per day).
    """
    run_end_ts = datetime.now().timestamp()
    eligible = (stats['sent_ok'] + stats['gemini_errors'] + stats['email_errors']
                + stats['rtdb_save_errors'])
    error_count = stats['gemini_errors'] + stats['email_errors']
    error_rate = (error_count / eligible) if eligible > 0 else 0.0

    full_stats = {
        **stats,
        'eligible': eligible,
        'error_count': error_count,
        'error_rate': round(error_rate, 4),
        'run_duration_sec': round(run_end_ts - run_start_ts, 1),
        'run_finished_at': int(run_end_ts),
        'run_date': datetime.now().strftime('%Y-%m-%d'),
    }

    print(f"\n=== Run summary ===")
    for k, v in full_stats.items():
        print(f"  {k}: {v}")

    # Save to Firebase
    try:
        db.reference('system/ai_manager_stats/last').set(full_stats)
        db.reference(f"system/ai_manager_stats/history/{full_stats['run_date']}").set(full_stats)
        print("  Stats saved to Firebase: system/ai_manager_stats/")
    except Exception as e:
        print(f"  WARNING: failed to save stats to Firebase: {e}")

    # Alert if error rate > 10% AND at least 1 eligible controller (avoid false alert at scale 0)
    ALERT_THRESHOLD = 0.10
    ADMIN_EMAIL = 'smarthydro.il@gmail.com'
    if eligible > 0 and error_rate > ALERT_THRESHOLD:
        try:
            _send_admin_alert(ADMIN_EMAIL, full_stats)
            print(f"  ALERT EMAIL SENT to {ADMIN_EMAIL} — error rate {error_rate*100:.1f}%")
        except Exception as e:
            print(f"  ALERT EMAIL FAILED: {e}")


def _send_admin_alert(admin_email, stats):
    """Sends a plain-text alert email to the admin when run had too many errors."""
    subject = f"⚠️ SmartHydro AI Manager — error rate {stats['error_rate']*100:.1f}% on {stats['run_date']}"
    body = f"""SmartHydro AI Manager run finished with high error rate.

Run date: {stats['run_date']}
Run duration: {stats['run_duration_sec']}s

Counts:
  Total controllers seen:    {stats['total_controllers']}
  Eligible (passed gates):   {stats['eligible']}
  Reports sent OK:           {stats['sent_ok']}
  Gemini errors:             {stats['gemini_errors']}
  Email errors:              {stats['email_errors']}
  RTDB save errors:          {stats['rtdb_save_errors']}

Filtered out (normal):
  tier_blocked:    {stats['tier_blocked']}
  expired:         {stats['expired']}
  offline:         {stats['offline']}
  no_owner_email:  {stats['no_owner_email']}
  opted_out:       {stats['opted_out']}

Error rate: {stats['error_rate']*100:.2f}% (threshold: 10%)

Check GitHub Actions logs:
https://github.com/shimonYeshayahu/Hydro-OTA/actions

Or Firebase Console:
https://console.firebase.google.com/project/smarthydrosystem-e27fe/database/smarthydrosystem-e27fe-default-rtdb/data/~2Fsystem~2Fai_manager_stats
"""
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['From'] = SENDER_EMAIL
    msg['To'] = admin_email
    msg['Subject'] = subject

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(SENDER_EMAIL, SENDER_PASSWORD)
    server.send_message(msg)
    server.quit()


if __name__ == "__main__":
    process_all_controllers()
