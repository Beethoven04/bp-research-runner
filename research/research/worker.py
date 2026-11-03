"""24/7 research worker. Runs in batches (--minutes) so a supervisor can review errors between batches.

Loop:  plan (countries -> discovery jobs)  ->  feed (new companies -> fit jobs, fit companies -> range jobs)
       ->  run jobs (discovery one at a time, site jobs in parallel)  ->  record done / failed (+ error)
Every job is idempotent (dedupe key) and retried up to 3 times with back-off.
"""
import argparse
import json
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, db, discover, fit, range_reader
from .verify import mx

PLAN_COUNTRIES = ["SE", "NO", "FI", "DK", "NL", "BE", "FR", "ES", "IT", "PL", "PT", "BA", "AL", "IE", "AT", "CZ", "GR",
                  "HR", "RS", "SI", "SK", "HU", "RO", "BG", "LT", "LV", "EE", "LU", "CH", "GB", "ME", "MK", "XK", "DE"]
CONTACT_RANK = [r"^(inkop|innkjop|indkob|osto|purchas|buy|b2b|wholesale|grossist|tukku|inkoop|achat|compras|acquisti|zakup)",
                r"^(info|kontakt|contact|hello|hej|hei|post|mail|office|shop|butik|butikk|store|sales|salg|myynti)",
                r"^(kund|customer|support|service|asiakas|kunde|klant|order)"]


def plan(c, countries):
    for cc in countries:
        db.enqueue(c, "discover_osm", {"country": cc}, f"osm:{cc}", priority=50)
        lang, qs = discover.search_queries(c, cc)
        for q in qs:
            db.enqueue(c, "discover_search", {"country": cc, "q": q, "lang": lang}, f"search:{cc}:{q}", priority=40)


def feed(c):
    c.execute("insert into jobs(tenant_id,kind,payload,dedupe,priority) "
              "select tenant_id,'fit',json_build_object('id',id),'fit:'||id,"
              "case when first_source='search' then 35 when segment in ('perfumery','beauty_store','wholesaler') then 32 "
              "when is_chain then 32 when segment='drugstore' then 25 when segment='pharmacy' then 15 "
              "when segment='beauty_salon' then 10 else 30 end from companies "
              "where status='new' and domain is not null on conflict do nothing")
    c.execute("insert into jobs(tenant_id,kind,payload,dedupe,priority) "
              "select tenant_id,'range',json_build_object('id',id),'range:'||id,40 from companies "
              "where status in ('fit','review') and range_status='todo' and domain is not null on conflict do nothing")


def pick(c, kinds, n):
    rows = c.execute("update jobs set status='running', attempts=attempts+1, updated_at=now() where id in ("
                     " select id from jobs where status='queued' and run_after<=now() and kind = any(%s) "
                     " order by priority desc, id limit %s for update skip locked) returning *", (kinds, n)).fetchall()
    return rows


def finish(job, err=None):
    with db.conn() as c, c.transaction():
        if err is None:
            c.execute("update jobs set status='done', last_error=null, updated_at=now() where id=%s", (job["id"],))
        elif job["attempts"] >= 3:
            c.execute("update jobs set status='failed', last_error=%s, updated_at=now() where id=%s", (err[:2000], job["id"]))
        else:
            c.execute("update jobs set status='queued', last_error=%s, run_after=now()+(%s||' minutes')::interval, "
                      "updated_at=now() where id=%s", (err[:2000], str(5 * job["attempts"] ** 2), job["id"]))


class Ctx:
    def __init__(self):
        with db.conn() as c:
            self.ours = db.cfg(c, "our_brands")
            self.arabic = db.cfg(c, "detect_arabic_brands")
            self.rules = db.cfg(c, "countries")
            self.stock = {k: v["in_stock"] for k, v in (db.cfg(c, "shop_brands") or {}).items()}
            self.products = db.cfg(c, "brand_products") or {}     # our product names per brand (blocks false gaps)
        self.our_names = [b["name"] for b in self.ours]


def choose_primary(c, cid):
    rows = c.execute("select id,email,kind from contacts where company_id=%s", (cid,)).fetchall()
    if not rows:
        return None

    def rank(r):
        local = r["email"].split("@")[0]
        if r["kind"] == "named":
            return 0
        for i, p in enumerate(CONTACT_RANK, 1):
            if re.search(p, local, re.I):
                return i
        return 9
    best = sorted(rows, key=lambda r: (rank(r), len(r["email"])))[0]
    c.execute("update contacts set is_primary=(id=%s) where company_id=%s", (best["id"], cid))
    hosts, provider = mx(best["email"].split("@")[1])
    if not hosts:
        c.execute("update contacts set verdict='no_mx', mx_provider='none', checked_at=now() where id=%s", (best["id"],))
    else:
        c.execute("update contacts set mx_provider=%s where id=%s", (provider, best["id"]))
    return best["email"]


def do_job(job, ctx):
    p = job["payload"]
    if job["kind"] == "discover_osm":
        discover.osm(p["country"])
    elif job["kind"] == "discover_search":
        discover.search(p["country"], p["q"], p["lang"])
    elif job["kind"] == "fit":
        with db.conn() as c:
            comp = c.execute("select * from companies where id=%s", (p["id"],)).fetchone()
        if not comp or comp["status"] != "new":
            return
        if comp["first_source"] == "search":
            ev = c2 = None
            with db.conn() as c2:
                ev = c2.execute("select snippet from company_evidence where company_id=%s and fact='search_hit' order by id limit 3",
                                (comp["id"],)).fetchall()
            comp["search_snippet"] = " ".join(e["snippet"] or "" for e in ev)
        res = fit.check(comp, ctx.ours, ctx.arabic)
        with db.conn() as c, c.transaction():
            fit.save(c, comp, res, ctx.our_names, ctx.rules)
            if res.get("platform"):
                db.evidence(c, comp["id"], "platform", res["platform"], f"https://{comp['domain']}/")
    elif job["kind"] == "range":
        with db.conn() as c:
            comp = c.execute("select * from companies where id=%s", (p["id"],)).fetchone()
            plat = c.execute("select value from company_evidence where company_id=%s and fact='platform' order by id desc limit 1",
                             (p["id"],)).fetchone()
        if not comp or comp["range_status"] != "todo":
            return
        rr = range_reader.read(comp, plat["value"] if plat else None, ctx.ours, ctx.arabic, ctx.stock, ctx.products)
        with db.conn() as c, c.transaction():
            for h in rr["hits"]:
                c.execute("insert into company_brands(company_id,brand,ours,kind,url,snippet) values (%s,%s,%s,%s,%s,%s) "
                          "on conflict (company_id,brand) do update set kind=excluded.kind, url=excluded.url, "
                          "snippet=excluded.snippet, fetched_at=now()",
                          (comp["id"], h["brand"], h["brand"] in ctx.our_names, h["kind"], h["url"], h["snippet"]))
            db.evidence(c, comp["id"], "range_method", rr["method"] or "none", f"https://{comp['domain']}/",
                        f"products_seen={rr['products_seen']}; {rr.get('note', '')}"[:500])
            arabic = any(True for _ in rr["hits"])
            status = comp["status"]
            is_chain = comp["is_chain"] or (comp["segment"] or "").endswith("_chain")
            if comp["status"] == "review" and is_chain:   # chain without perfume on home page: range decides
                status = "fit" if rr["hits"] else "not_fit"
            if rr.get("own_brand_only") and status in ("fit", "review"):
                status = "not_fit"
                db.evidence(c, comp["id"], "not_fit_reason", "own_brand_only", f"https://{comp['domain']}/",
                            "complete catalogue read: one vendor = the shop itself")
            c.execute("update companies set range_status=%s, gap_brands=%s, sells_arabic=(coalesce(sells_arabic,false) or %s), "
                      "status=%s, updated_at=now() where id=%s",
                      (rr["status"], rr["gap"], arabic, status, comp["id"]))
            choose_primary(c, comp["id"])
            score(c, comp["id"])
    else:
        raise ValueError(f"unknown job kind {job['kind']}")


def score(c, cid):
    """Priority: sells Arabic perfume (+50), segment order, named contact (+15) > role (+5), has contact (+10)."""
    comp = c.execute("select * from companies where id=%s", (cid,)).fetchone()
    order = db.cfg(c, "segment_order") or []
    seg = comp["segment"] or ""
    s = 50 if comp["sells_arabic"] else 0
    s += max(0, 30 - 3 * order.index(seg)) if seg in order else 0
    k = c.execute("select kind from contacts where company_id=%s and is_primary", (cid,)).fetchone()
    if k:
        s += 10 + (15 if k["kind"] == "named" else 5)
    if comp["gap_brands"]:
        s += min(10, len(comp["gap_brands"]))
    if seg == "distributor":
        s -= 100        # distributors are contacted last (decided 2 Nov 2026)
    c.execute("update companies set priority=%s where id=%s", (s, cid))


def _lane(kinds, slots, end, ctx, stats, lock):
    """Keeps `slots` jobs of `kinds` running until `end`; refills as soon as one finishes."""
    with ThreadPoolExecutor(slots) as ex:
        running = {}
        while time.time() < end or running:
            try:
                _tick(kinds, slots, end, ctx, stats, lock, ex, running)
            except Exception as e:     # a lane must never die (DB/network blip): log, wait, go on
                print(f"lane {kinds} error: {type(e).__name__}: {e}", flush=True)
                time.sleep(30)


def _tick(kinds, slots, end, ctx, stats, lock, ex, running):
    """One round of a lane: refill free slots, then collect finished jobs. A job left 'running' by an error here
    is put back in the queue at the start of the next batch."""
    from concurrent.futures import FIRST_COMPLETED, wait
    free = slots - len(running)
    if free > 0 and time.time() < end:
        with db.conn() as c, c.transaction():
            if "fit" in kinds:
                feed(c)
            for j in pick(c, kinds, free):
                running[ex.submit(do_job, j, ctx)] = j
    if not running:
        time.sleep(20)
        return
    done, _ = wait(list(running), timeout=10, return_when=FIRST_COMPLETED)
    for f in done:
        j = running.pop(f)
        try:
            f.result()
            finish(j)
            key = "done"
        except Exception as e:
            finish(j, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1200:]}")
            key = "errors"
        with lock:
            stats[key] += 1
            stats[j["kind"]] = stats.get(j["kind"], 0) + 1


def run(minutes: float, countries, threads: int = 12):
    import threading
    end = time.time() + minutes * 60
    ctx = Ctx()
    stats, lock = {"done": 0, "errors": 0}, threading.Lock()
    with db.conn() as c, c.transaction():
        c.execute("update jobs set status='queued' where status='running'")   # recover after a crash
        plan(c, countries)
    lanes = [threading.Thread(target=_lane, args=(["discover_osm"], 1, end, ctx, stats, lock)),
             threading.Thread(target=_lane, args=(["discover_search"], 1, end, ctx, stats, lock)),
             threading.Thread(target=_lane, args=(["fit"], threads, end, ctx, stats, lock)),
             threading.Thread(target=_lane, args=(["range"], 16, end, ctx, stats, lock))]
    for t in lanes:
        t.start()
    for t in lanes:
        t.join()
    with db.conn() as c, c.transaction():
        c.execute("update jobs set status='queued' where status='running'")
        c.execute("insert into research_runs(tenant_id,stage,finished_at,counts) values (%s,'worker_batch',now(),%s)",
                  (config.TENANT, json.dumps(stats)))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--countries", default=",".join(PLAN_COUNTRIES[:13]))
    ap.add_argument("--threads", type=int, default=12)
    a = ap.parse_args()
    print(json.dumps(run(a.minutes, [x.strip().upper() for x in a.countries.split(",") if x.strip()], a.threads)))
