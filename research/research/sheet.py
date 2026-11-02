"""Google Sheet view for Sohaib and Louai. The database stays the master; this file only writes a read view.

Tabs: "Summary" (counts per country) + one tab per country ("SE", "NO", ...) with fit leads, best first.
Run: python -m research.sheet
"""
import time

import gspread

from . import config, db

COLS = ["Priority", "Business", "Website", "Segment", "Chain (stores)", "Sells Arabic perfume", "Our brands they sell",
        "Our brands they lack (offer)", "Range check", "Email", "Email type", "Email check", "Email rule (country)",
        "Status", "Found via"]
STATUSES = ("fit", "review")


def _w(fn, *a, **k):
    """One Sheets write; waits and retries on the per-minute quota (HTTP 429)."""
    for i in range(6):
        try:
            return fn(*a, **k)
        except gspread.exceptions.APIError as e:
            if "429" not in str(e) or i == 5:
                raise
            time.sleep(20 * (i + 1))


def _client():
    """Keys from the environment (server / GitHub Secrets) or from private/ on a laptop."""
    import json, os
    sa = os.environ.get("GOOGLE_SA_JSON")
    gc = (gspread.service_account_from_dict(json.loads(sa)) if sa else
          gspread.service_account(filename=str(config.REPO / "private" / "google_service_account.json")))
    return gc.open_by_key(config.get("SHEET_ID").strip())


def _rows(c, country):
    q = """
    select c.priority, c.name, c.domain, c.segment, c.is_chain, c.store_count, c.sells_arabic, c.range_status,
           c.gap_brands, c.email_rule, c.status, c.first_source_url,
           (select string_agg(b.brand, ', ' order by b.brand) from company_brands b where b.company_id=c.id and b.ours) ours,
           k.email, k.kind, coalesce(k.verdict,'') verdict, k.mx_provider
    from companies c left join contacts k on k.company_id=c.id and k.is_primary
    where c.tenant_id=%s and c.country=%s and c.status = any(%s)
    order by c.priority desc, c.name"""
    out = []
    for r in c.execute(q, (config.TENANT, country, list(STATUSES))):
        check = r["verdict"] if r["verdict"] not in ("", "unchecked") else ("mx ok" if r["mx_provider"] and r["mx_provider"] != "none" else
                                                                           ("no mail server" if r["mx_provider"] == "none" else ""))
        out.append([r["priority"], r["name"], f"https://{r['domain']}" if r["domain"] else "", r["segment"] or "",
                    f"yes ({r['store_count']})" if r["is_chain"] else "no",
                    {True: "yes", False: "no"}.get(r["sells_arabic"], "not seen"), r["ours"] or "",
                    ", ".join(r["gap_brands"] or []), r["range_status"], r["email"] or "", r["kind"] or "", check,
                    r["email_rule"], r["status"], r["first_source_url"] or ""])
    return out


def sync():
    sh = _client()
    tabs = {w.title: w for w in sh.worksheets()}
    with db.conn() as c:
        summary = c.execute("""
          select country,
            count(*) found,
            count(*) filter (where status in ('fit','review')) fit,
            count(*) filter (where status='not_fit') not_fit,
            count(*) filter (where status='new') waiting,
            count(*) filter (where sells_arabic) sells_arabic,
            count(*) filter (where range_status='read') range_read,
            count(*) filter (where exists (select 1 from contacts k where k.company_id=companies.id and k.is_primary)) with_email,
            max(email_rule) email_rule
          from companies where tenant_id=%s group by country order by found desc""", (config.TENANT,)).fetchall()
        ready = c.execute("select count(*) n from ready_leads").fetchone()["n"]
        countries = [s["country"] for s in summary if s["fit"] > 0]
        data = {cc: _rows(c, cc) for cc in countries}
    head = ["Country", "Found", "Fit", "Not fit", "Waiting for check", "Sells Arabic perfume", "Range read",
            "Has email", "Email rule"]
    body = [[s["country"], s["found"], s["fit"], s["not_fit"], s["waiting"], s["sells_arabic"], s["range_read"],
             s["with_email"], s["email_rule"]] for s in summary]
    tot = ["TOTAL"] + [sum(r[i] for r in body) for i in range(1, 8)] + [""]
    stamp = [f"Updated {time.strftime('%Y-%m-%d %H:%M')} · database is the master · ready for email (verified + allowed country): {ready}"]
    ws = tabs.get("Summary") or _w(sh.add_worksheet, "Summary", rows=200, cols=12)
    _w(ws.clear)
    _w(ws.update, [stamp, [], head] + body + [tot], "A1")
    _w(ws.format, "A3:I3", {"textFormat": {"bold": True}})
    for cc in countries:
        rows = data[cc]
        ws = tabs.get(cc) or _w(sh.add_worksheet, cc, rows=max(100, len(rows) + 10), cols=len(COLS))
        _w(ws.clear)
        if ws.row_count < len(rows) + 2:
            _w(ws.add_rows, len(rows) + 2 - ws.row_count)
        _w(ws.update, [COLS] + rows, "A1")
        if cc not in tabs:      # header format, freeze and filter only once per new tab
            _w(ws.format, "A1:O1", {"textFormat": {"bold": True}})
            _w(ws.freeze, rows=1)
        try:
            _w(ws.set_basic_filter, f"A1:O{len(rows) + 1}")
        except Exception:
            pass
        time.sleep(3)         # Sheets API: stay under the per-minute write quota
    if "Sheet1" in tabs and len(sh.worksheets()) > 1:
        sh.del_worksheet(tabs["Sheet1"])
    return {"countries": countries, "rows": {k: len(v) for k, v in data.items()}, "ready": ready}


if __name__ == "__main__":
    print(sync())
