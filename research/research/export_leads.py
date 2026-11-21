"""Excel export of every lead with a verified email, read live from the database at run time.

Verified = primary contact is `valid`, or `risky` but published on the shop's own site (same email rule as the
`ready_leads` view). Excluded domains/emails (customers, competitors, opt-outs) are left out. Unlike `ready_leads`,
this export ignores the offer rule, so Louai can choose who to contact first. Every country can be emailed
(4 Nov 2026 decision); "Law confirmed" is an info flag from config countries.law_confirmed.

Tabs: "All leads" (filters on every column), "Summary" (country x type), one tab per country.
Run: python -m research.export_leads   ->  data/verified_leads_<date>.xlsx
"""
import datetime as dt
import pathlib

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import db

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Type = business type only (4 Nov 2026). How they sell (store / online) is the "Sales channel" column, and
# "sells Arabic perfume" is in "Sells". Search-found sites without a clearer type are "Retailer".
TYPE = {
    "pharmacy": "Pharmacy", "drugstore": "Drugstore", "perfumery": "Perfumery", "beauty_store": "Beauty store",
    "beauty_salon": "Salon", "wholesaler": "Wholesaler", "distributor": "Distributor (contact last)",
    "retail_chain": "Chain", "drugstore_chain": "Chain", "pharmacy_chain": "Chain",
    "marketplace": "Marketplace", "marketplace_seller": "Marketplace",
    "small_retailer": "Retailer", "online_store": "Retailer", "arabic_perfume_seller": "Retailer",
}
CHANNEL = {"store": "Physical store", "online": "Online shop", "store+online": "Store + online shop",
           "unknown": "Unknown"}

COUNTRY = {
    "AL": "Albania", "AT": "Austria", "BA": "Bosnia and Herzegovina", "BE": "Belgium", "BG": "Bulgaria",
    "CH": "Switzerland", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom", "UK": "United Kingdom", "GR": "Greece",
    "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IS": "Iceland", "IT": "Italy", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "ME": "Montenegro", "MK": "North Macedonia", "MT": "Malta",
    "NL": "Netherlands", "NO": "Norway", "PL": "Poland", "PT": "Portugal", "RO": "Romania", "RS": "Serbia",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "XK": "Kosovo",
}

SQL = """
select c.id as company_id, c.priority, c.name, c.domain, c.country, c.city, c.segment, c.is_chain, c.store_count, c.sells_arabic, c.sells_perfume,
       c.range_status, c.gap_brands, c.email_rule, c.first_source,
       ch.value as channel,
       k.email, k.kind, k.name as contact_name, k.role_title, k.verdict, k.catch_all, k.source_url, k.checked_at
from companies c
join contacts k on k.company_id = c.id and k.is_primary
left join lateral (select e.value from company_evidence e where e.company_id = c.id and e.fact = 'sales_channel'
                   order by e.fetched_at desc limit 1) ch on true
where c.status = 'fit'
  and (k.verdict = 'valid' or (k.verdict = 'risky' and k.source_url like '%%' || c.domain || '%%'))
  and not exists (select 1 from exclusions x where x.tenant_id = c.tenant_id
                  and ((x.kind = 'domain' and x.value = c.domain) or (x.kind = 'email' and x.value = k.email)))
order by c.country, c.priority desc, c.name
"""

COLS = ["Country", "Type", "Sales channel", "Segment (detail)", "Business", "Website", "City", "Email", "Email kind", "Contact name",
        "Contact role", "Email check", "Sells", "What they don't sell", "Range check", "Chain (stores)",
        "Can email now", "Law confirmed", "Priority", "Email found on", "Email checked"]
CAN, LAW = COLS.index("Can email now"), COLS.index("Law confirmed")
WIDTH = {"Business": 30, "Website": 30, "Email": 34, "What they don't sell": 45, "Sells": 20, "Email found on": 40,
         "Segment (detail)": 22, "Country": 16}


def _row(r, law):
    seg = r["segment"] or ""
    kind = "Named person" if r["kind"] == "named" else "Role address (info@...)"
    check = "Valid" if r["verdict"] == "valid" else ("Risky (catch-all)" if r["catch_all"] else "Risky")
    if r["range_status"] == "read":
        offer = ", ".join(r["gap_brands"]) or "(sells all our brands)"
    else:
        offer = "Unknown (range not read)"
    sells = ("Arabic perfume" if r["sells_arabic"] or seg == "arabic_perfume_seller" else "Other perfume" if r["sells_perfume"]
             else "Beauty products only")
    chain = (f"Yes ({r['store_count']})" if r["store_count"] else "Yes") if r["is_chain"] else "No"
    can = "Yes" if r["email_rule"] == "opt_out" else "No"
    lawc = "Yes" if r["country"] in law else "No"
    checked = r["checked_at"].strftime("%Y-%m-%d") if r["checked_at"] else ""
    return [COUNTRY.get(r["country"], r["country"]), TYPE.get(seg, seg.replace("_", " ").title() or "Unknown"),
            CHANNEL.get(r["channel"] or "unknown", "Unknown"), seg.replace("_", " "), r["name"], f"https://{r['domain']}" if r["domain"] else "", r["city"] or "",
            r["email"], kind, r["contact_name"] or "", r["role_title"] or "", check,
            sells, offer, r["range_status"], chain, can, lawc, r["priority"],
            r["source_url"], checked]


def _sheet(ws, rows):
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4B3A2A")
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}{max(len(rows) + 1, 2)}"
    for i, name in enumerate(COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTH.get(name, 15)


def main():
    with db.conn() as c:
        cur = c.cursor()
        cur.execute(SQL)
        data = cur.fetchall()
        law = set((db.cfg(c, "countries") or {}).get("law_confirmed") or [])
    rows = [_row(r, law) for r in data]

    wb = Workbook()
    ws = wb.active
    ws.title = "All leads"
    _sheet(ws, rows)

    # Summary: countries x types, with totals.
    types = sorted({r[1] for r in rows})
    countries = sorted({r[0] for r in rows})
    sm = wb.create_sheet("Summary")
    sm.append([f"Built {dt.datetime.now():%Y-%m-%d %H:%M} from the live database. {len(rows)} verified emails."])
    sm.append([])
    sm.append(["Country"] + types + ["Total", "Can email now", "Law confirmed"])
    for c_ in sm[3]:
        c_.font = Font(bold=True)
    for ctry in countries:
        sub = [r for r in rows if r[0] == ctry]
        sm.append([ctry] + [sum(r[1] == t for r in sub) for t in types]
                  + [len(sub), sum(r[CAN] == "Yes" for r in sub), sum(r[LAW] == "Yes" for r in sub)])
    sm.append(["Total"] + [sum(r[1] == t for r in rows) for t in types]
              + [len(rows), sum(r[CAN] == "Yes" for r in rows), sum(r[LAW] == "Yes" for r in rows)])
    for c_ in sm[sm.max_row]:
        c_.font = Font(bold=True)
    # second table: sales channel x type (how each kind of business sells)
    chans = [v for v in CHANNEL.values() if any(r[2] == v for r in rows)]
    sm.append([])
    sm.append(["Type"] + chans + ["Total"])
    for c_ in sm[sm.max_row]:
        c_.font = Font(bold=True)
    for t in types:
        sub = [r for r in rows if r[1] == t]
        sm.append([t] + [sum(r[2] == ch for r in sub) for ch in chans] + [len(sub)])
    sm.append(["Total"] + [sum(r[2] == ch for r in rows) for ch in chans] + [len(rows)])
    for c_ in sm[sm.max_row]:
        c_.font = Font(bold=True)
    sm.column_dimensions["A"].width = 24
    wb.move_sheet("Summary", offset=-1)

    for ctry in countries:
        _sheet(wb.create_sheet(ctry[:31]), [r for r in rows if r[0] == ctry])

    out = ROOT / "data" / f"verified_leads_{dt.date.today():%Y-%m-%d}.xlsx"
    wb.save(out)
    print(f"{len(rows)} leads in {len(countries)} countries -> {out}")


if __name__ == "__main__":
    main()
