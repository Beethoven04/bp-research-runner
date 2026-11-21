"""Build the marketing email send plan (table marketing_calendar, db/marketing.sql) from config/marketing.json
and config/marketing_holidays.csv. Rules (Louai, 21 Nov 2026):

  * one regular email every 2 weeks on a fixed weekday, never on a weekend;
  * before each big gifting day two emails: 14 and 7 days before (a weekend date moves to the Friday before);
  * at most one holiday per country per calendar month (config priority decides);
  * at most one marketing email per country per ISO week: a holiday email replaces the regular one, and of two
    holiday emails in one week the one for the earlier holiday stays.

python -m research.marketing_plan [--until 2027-12-31] [--dry]
Also pushes config/marketing.json to the database config key 'marketing' (keeping the live switches enabled and
test_mode) and loads the holiday CSV.
"""
import argparse
import csv
import json
from datetime import date, timedelta

from . import config

CFG_PATH = config.REPO / "config" / "marketing.json"
CSV_PATH = config.REPO / "config" / "marketing_holidays.csv"


def load():
    cfg = json.loads(CFG_PATH.read_text())
    with open(CSV_PATH, newline="") as fh:
        hol = [{**r, "date": date.fromisoformat(r["date"])} for r in csv.DictReader(fh)]
    return cfg, hol


def weekday_before(d: date) -> date:
    """Saturday/Sunday -> the Friday before."""
    return d - timedelta(days=max(0, d.weekday() - 4))


def pick_holidays(hol, cfg):
    """Per country and calendar month keep only the highest-priority holiday (max_per_month)."""
    prio = {k: i for i, k in enumerate(cfg["holiday"]["priority"])}
    limit = cfg["holiday"].get("max_per_month", 1)
    groups = {}
    for h in hol:
        if h["kind"] in prio:
            groups.setdefault((h["country"], h["date"].year, h["date"].month), []).append(h)
    keep = []
    for rows in groups.values():
        keep += sorted(rows, key=lambda h: (prio[h["kind"]], h["date"]))[:limit]
    return keep


def plan(cfg, hol, start: date, until: date):
    """Return rows {country, send_date, kind, holiday_kind, holiday_name, holiday_date, round}, sorted."""
    countries = set(cfg["countries"])
    days = cfg["holiday"]["days_before"]
    per_week = cfg.get("max_per_week", 1)
    out = []
    for cc in sorted(countries):
        # holiday emails first (they take the week)
        cand = []
        for h in pick_holidays([h for h in hol if h["country"] == cc], cfg):
            for rnd, n in enumerate(days, 1):
                d = weekday_before(h["date"] - timedelta(days=n))
                if start <= d <= until:
                    cand.append({"country": cc, "send_date": d, "kind": "holiday", "holiday_kind": h["kind"],
                                 "holiday_name": h["name"], "holiday_date": h["date"], "round": rnd})
        cand.sort(key=lambda r: (r["holiday_date"], r["send_date"]))
        weeks, taken = {}, set()
        for r in cand:
            wk = r["send_date"].isocalendar()[:2]
            if weeks.get(wk, 0) < per_week and r["send_date"] not in taken:
                weeks[wk] = weeks.get(wk, 0) + 1
                taken.add(r["send_date"])
                out.append(r)
        # regular emails every N weeks, only in weeks without a holiday email
        reg = cfg["regular"]
        d = date.fromisoformat(reg["first_date"])
        d += timedelta(days=(reg["weekday"] - 1 - d.weekday()) % 7)
        while d <= until:
            wk = d.isocalendar()[:2]
            if d >= start and weeks.get(wk, 0) < per_week:
                weeks[wk] = weeks.get(wk, 0) + 1
                out.append({"country": cc, "send_date": d, "kind": "regular", "holiday_kind": None,
                            "holiday_name": None, "holiday_date": None, "round": None})
            d += timedelta(weeks=reg["every_weeks"])
    return sorted(out, key=lambda r: (r["send_date"], r["country"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default=None, help="last day to plan (default: last holiday in the CSV)")
    ap.add_argument("--dry", action="store_true", help="print a summary, write nothing")
    a = ap.parse_args()
    cfg, hol = load()
    start = date.today()
    until = date.fromisoformat(a.until) if a.until else max(h["date"] for h in hol)
    rows = plan(cfg, hol, start, until)
    print(f"plan {start} .. {until}: {len(rows)} send days, "
          f"{sum(r['kind'] == 'holiday' for r in rows)} holiday, {sum(r['kind'] == 'regular' for r in rows)} regular")
    if a.dry:
        return
    from . import db
    t = config.TENANT
    with db.conn() as c:
        live = c.execute("select value from config where tenant_id = %s and key = 'marketing'", (t,)).fetchone()
        for k in ("enabled", "test_mode"):     # switches are changed in the database, never by a push
            if live and k in live["value"]:
                cfg[k] = live["value"][k]
        c.execute("""insert into config (tenant_id, key, value, updated_at) values (%s, 'marketing', %s, now())
                     on conflict (tenant_id, key) do update set value = excluded.value, updated_at = now()""",
                  (t, json.dumps(cfg)))
        c.execute("delete from marketing_holidays where tenant_id = %s", (t,))
        for h in hol:
            c.execute("""insert into marketing_holidays (tenant_id, country, date, kind, name, local_name, source)
                         values (%s, %s, %s, %s, %s, %s, %s)""",
                      (t, h["country"], h["date"], h["kind"], h["name"], h["local_name"], h["source"]))
        # only future days are rebuilt; past rows stay as a record
        c.execute("delete from marketing_calendar where tenant_id = %s and send_date >= %s", (t, start))
        for r in rows:
            c.execute("""insert into marketing_calendar (tenant_id, country, send_date, kind, holiday_kind,
                           holiday_name, holiday_date, round) values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                      (t, r["country"], r["send_date"], r["kind"], r["holiday_kind"], r["holiday_name"],
                       r["holiday_date"], r["round"]))
        c.commit()
    print("written: config 'marketing', marketing_holidays, marketing_calendar")


if __name__ == "__main__":
    main()
