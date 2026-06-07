"""
SmartHydro Report Validators
============================
Five automated quality tests applied to every generated report before it
ships to a customer. Implements REPORTS_AGENT_BRIEFING.md §10.

Each test is a pure function (markdown_text, today_iso, style) -> (bool, str)
where the bool is pass/fail and the string is a short reason. Functions are
defensive against malformed input — they never raise on a bad report; they
return (False, reason) instead.

The `run_all` helper runs every test and returns a structured dict so the
test harness and `ai_manager.py` can decide whether to ship or regenerate.
"""

from __future__ import annotations

import re
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Test 1 — forbidden patterns (REPORTS_AGENT_BRIEFING §9)
# ---------------------------------------------------------------------------

# Each entry is (compiled_regex, human_label). Regex flags = re.IGNORECASE.
# Patterns aim at the SHAPES the LLM keeps producing — not exact strings.
# Keep this list tight; over-aggressive matching will cause false rejections.
_FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    # §9.1 — restating the app's status display
    (re.compile(r'\bבטווח\s+(התקין|המומלץ|היעד|התקני)\b'),
     'phrase "בטווח התקין/המומלץ/היעד"'),
    (re.compile(r'\bערכי(\s+ה[א-ת]+)*\s+תקינים\b'),
     'phrase "הערכים תקינים"'),
    (re.compile(r'\bהמערכת\s+(תקינה|פועלת\s+כראוי|לא\s+תקינה)\b'),
     'phrase "המערכת תקינה/פועלת כראוי"'),
    (re.compile(r'\bמ[צ]ב\s+(יומי\s+)?תקין\b'),
     'phrase "מצב תקין"'),
    (re.compile(r'\b(ה[א-ת]+\s+)?חורג(ים|ת)?\s+מהיעד\b'),
     'phrase "חורג מהיעד" without insight'),
    # §9.2 — empty filler
    (re.compile(r'\bהמשך\s+ב?(ניטור|מעקב|הקצב\s+הנוכחי\s+ו)\b'),
     'phrase "המשך בניטור/במעקב"'),
    (re.compile(r'\bעקוב\s+אחר[יי]?\b'),
     'phrase "עקוב אחרי"'),
    (re.compile(r'\bמומלץ\s+(לבדוק|לוודא|לעקוב)\b'),
     'phrase "מומלץ לבדוק/לוודא"'),
    (re.compile(r'\bאמת\s+את\s+ה?רצף\b'),
     'phrase "אמת את הרצף"'),
    # §9.3 — generic operational
    (re.compile(r'\bבדוק\s+את\s+תקינות\s+ה?חיישן\b'),
     'phrase "בדוק את תקינות החיישן" (generic — needs data justification)'),
    (re.compile(r'\bהקפד\s+על\s+תיעוד\b'),
     'phrase "הקפד על תיעוד"'),
    # §9.4 — vague qualifiers (only flag in action/recommendation context)
    (re.compile(r'^\s*(אולי|כדאי\s+ש?ת?)\s+', re.MULTILINE),
     'sentence starting with "אולי" / "כדאי"'),
]


def test_forbidden_patterns(md: str, today_iso: str = '', style: str = '') -> tuple[bool, str]:
    """Return (pass, reason). Pass = zero forbidden patterns hit."""
    hits = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(md):
            hits.append(label)
    if hits:
        return False, f"forbidden patterns found: {'; '.join(hits[:3])}" + (
            f" (+ {len(hits) - 3} more)" if len(hits) > 3 else '')
    return True, 'ok'


# ---------------------------------------------------------------------------
# Test 2 — insight category coverage (agronomist only)
# ---------------------------------------------------------------------------

_CATEGORY_TAGS = {'Trend', 'Correlation', 'Stage', 'Agronomy', 'Anomaly'}
_TAG_REGEX = re.compile(r'\*\*\[(Trend|Correlation|Stage|Agronomy|Anomaly)\]\*\*')


def test_insight_categories(md: str, today_iso: str = '', style: str = '') -> tuple[bool, str]:
    """For agronomist style: section 'תובנות' must contain 2-3 insights, each
    prefixed with **[Category]** tag, from at least 2 distinct categories.

    Brief style: minimum 1 tagged insight, any category."""
    tags = _TAG_REGEX.findall(md)
    if style == 'brief':
        if len(tags) < 1:
            return False, 'brief report has zero tagged insights'
        return True, f"ok ({len(tags)} tagged: {','.join(tags)})"
    # agronomist
    if len(tags) < 2:
        return False, f'agronomist needs 2-3 tagged insights, found {len(tags)}'
    if len(tags) > 4:
        return False, f'agronomist over-produced insights ({len(tags)}; max 3 expected)'
    distinct = set(tags)
    if len(distinct) < 2:
        return False, f'insights all share category "{tags[0]}" — need 2+ distinct'
    return True, f"ok ({len(tags)} insights, {len(distinct)} categories: {','.join(distinct)})"


# ---------------------------------------------------------------------------
# Test 3 — specificity (numeric density in insights/forecast/actions)
# ---------------------------------------------------------------------------

# Count standalone numbers outside the status table. Decimals, percentages,
# day counts ("בעוד 4 ימים") all count.
_NUMBER_REGEX = re.compile(r'(?<![|\w])(\d+(?:\.\d+)?)')


def test_specificity(md: str, today_iso: str = '', style: str = '') -> tuple[bool, str]:
    """At least 4 numeric tokens outside the status table.
    For brief style, threshold lowered to 3 since the report is shorter."""
    # Strip table rows (lines starting with `|` after lstrip)
    non_table = '\n'.join(
        line for line in md.split('\n')
        if not line.lstrip().startswith('|')
    )
    numbers = _NUMBER_REGEX.findall(non_table)
    threshold = 3 if style == 'brief' else 4
    if len(numbers) < threshold:
        return False, f'only {len(numbers)} numeric tokens outside table (need ≥{threshold})'
    return True, f'ok ({len(numbers)} numbers)'


# ---------------------------------------------------------------------------
# Test 4 — date integrity
# ---------------------------------------------------------------------------

_DATE_REGEX = re.compile(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b')


def test_date_integrity(md: str, today_iso: str, style: str = '',
                        allowed_dates: set[str] | None = None) -> tuple[bool, str]:
    """Every DD/MM or DD/MM/YYYY date in the report must be either today's
    date or a date in `allowed_dates` (history + future projections).

    today_iso: 'YYYY-MM-DD'.
    allowed_dates: optional set of 'DD/MM' strings (history dates from input).
                   If None, only today's DD/MM is allowed; future-projection
                   dates within 30 days of today are also accepted.
    """
    if not today_iso:
        # Without an anchor we can't validate — pass conservatively
        return True, 'skipped (no today_iso provided)'
    try:
        today_dt = datetime.strptime(today_iso, '%Y-%m-%d').date()
    except ValueError:
        return True, 'skipped (bad today_iso format)'

    today_dm = f"{today_dt.day:02d}/{today_dt.month:02d}"
    allowed_dates = set(allowed_dates or set())
    allowed_dates.add(today_dm)
    # Strip leading zero variants ("2/06" alongside "02/06")
    allowed_dates |= {f"{d.lstrip('0') or '0'}/{m.lstrip('0') or '0'}"
                      for d, m in (s.split('/') for s in list(allowed_dates) if '/' in s)}

    hallucinated = []
    for d_str, m_str, _y in _DATE_REGEX.findall(md):
        try:
            d, m = int(d_str), int(m_str)
        except ValueError:
            continue
        if not (1 <= d <= 31 and 1 <= m <= 12):
            continue
        dm_norm = f"{d:02d}/{m:02d}"
        if dm_norm in allowed_dates:
            continue
        # Allow future projection within 30 days of today
        try:
            candidate = date(today_dt.year, m, d)
            delta_days = (candidate - today_dt).days
            if 0 <= delta_days <= 30:
                continue
        except ValueError:
            pass
        hallucinated.append(dm_norm)
    if hallucinated:
        return False, f"unrecognized dates: {','.join(sorted(set(hallucinated)))}"
    return True, 'ok'


# ---------------------------------------------------------------------------
# Test 5 — length
# ---------------------------------------------------------------------------

def test_length(md: str, today_iso: str = '', style: str = '') -> tuple[bool, str]:
    """Brief: 6-10 non-blank lines. Agronomist: 18-30 non-blank lines.
    Table rows count as 1 line each (not collapsed). HTML/markdown frame
    chars like `|---|` count as one line each."""
    lines = [ln for ln in md.split('\n') if ln.strip()]
    n = len(lines)
    if style == 'brief':
        lo, hi = 6, 12  # +2 slack on upper bound — bullet items wrap
    else:
        lo, hi = 14, 36  # agronomist; table adds ~5 lines, give room
    if not (lo <= n <= hi):
        return False, f'{n} non-blank lines (expected {lo}-{hi} for {style})'
    return True, f'ok ({n} lines)'


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    ('forbidden_patterns', test_forbidden_patterns),
    ('insight_categories', test_insight_categories),
    ('specificity',        test_specificity),
    ('date_integrity',     test_date_integrity),
    ('length',             test_length),
]


def run_all(md: str, today_iso: str = '', style: str = 'brief',
            allowed_dates: set[str] | None = None) -> dict:
    """Run every validator. Returns:
        {
          'overall_pass': bool,
          'results': [(name, pass, reason), ...],
        }
    """
    results = []
    for name, fn in ALL_TESTS:
        if name == 'date_integrity':
            ok, reason = fn(md, today_iso, style, allowed_dates=allowed_dates)
        else:
            ok, reason = fn(md, today_iso, style)
        results.append((name, ok, reason))
    overall = all(ok for _, ok, _ in results)
    return {'overall_pass': overall, 'results': results}


def format_report_card(results_dict: dict) -> str:
    """Pretty-print a results dict for terminal output."""
    lines = []
    icon = '✓' if results_dict['overall_pass'] else '✗'
    lines.append(f"  Overall: {icon}")
    for name, ok, reason in results_dict['results']:
        mark = '✓' if ok else '✗'
        lines.append(f"    {mark} {name:22s} {reason}")
    return '\n'.join(lines)
