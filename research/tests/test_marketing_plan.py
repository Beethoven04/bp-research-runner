"""Marketing send plan rules (decided 21 Nov 2026)."""
from datetime import date

from research.marketing_plan import load, pick_holidays, plan, weekday_before

CFG, HOL = load()
ROWS = plan(CFG, HOL, date(2026, 11, 21), date(2027, 12, 31))


def test_never_on_weekend():
    assert ROWS and all(r["send_date"].weekday() < 5 for r in ROWS)


def test_only_euro_countries():
    assert {r["country"] for r in ROWS} == set(CFG["countries"])
    assert not {"SE", "DK", "PL", "CZ", "HU", "RO", "NO"} & {r["country"] for r in ROWS}


def test_max_one_per_week_per_country():
    seen = set()
    for r in ROWS:
        key = (r["country"], r["send_date"].isocalendar()[:2])
        assert key not in seen, r
        seen.add(key)


def test_weekend_moves_to_friday():
    assert weekday_before(date(2027, 1, 30)) == date(2027, 1, 29)   # Saturday
    assert weekday_before(date(2027, 1, 31)) == date(2027, 1, 29)   # Sunday
    assert weekday_before(date(2027, 2, 1)) == date(2027, 2, 1)     # Monday stays


def test_christmas_2026_two_emails():
    nl = [r for r in ROWS if r["country"] == "NL" and r["holiday_kind"] == "christmas" and r["holiday_date"].year == 2026]
    assert [(r["send_date"], r["round"]) for r in nl] == [(date(2026, 12, 11), 1), (date(2026, 12, 18), 2)]


def test_one_holiday_per_month():
    nl_dec = [h for h in pick_holidays([h for h in HOL if h["country"] == "NL"], CFG)
              if h["date"].year == 2027 and h["date"].month == 12]
    assert [h["kind"] for h in nl_dec] == ["christmas"]           # Sinterklaas loses to Christmas


def test_regular_every_two_weeks_on_tuesday():
    reg = [r["send_date"] for r in ROWS if r["country"] == "NL" and r["kind"] == "regular"]
    assert reg and all(d.weekday() == 1 for d in reg)
    assert all((b - a).days % 14 == 0 for a, b in zip(reg, reg[1:]))


def test_holiday_replaces_regular_that_week():
    # 15 Dec 2026 is a regular Tuesday (1 Dec + 14 days); the Christmas reminder of 18 Dec takes that week.
    nl = {r["send_date"]: r["kind"] for r in ROWS if r["country"] == "NL"}
    assert date(2026, 12, 15) not in nl and nl[date(2026, 12, 18)] == "holiday"
    assert nl[date(2026, 12, 1)] == "regular"
