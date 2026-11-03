"""Database access (Postgres / Supabase). Every company fact is written together with its evidence."""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from . import config


def _url():
    if config.DATABASE_URL:
        return config.DATABASE_URL
    line = (config.REPO / "private" / "supabase.env").read_text().strip()
    return line.split("=", 1)[1]


def _connect():
    """Connect, retrying for ~5 minutes: Wi-Fi/DNS blips (Mac wake-up) must not kill a 24/7 worker."""
    import time
    for i in range(10):
        try:
            url, kw = _url(), {}
            if os.environ.get("DB_POOL") == "transaction":   # many parallel runners: Supavisor transaction mode
                url, kw = url.replace(".pooler.supabase.com:5432", ".pooler.supabase.com:6543"), {"prepare_threshold": None}
            # Supabase already stops a statement after 2 min; keepalives make a dead network link fail in ~1 min
            # instead of hanging a lane for half an hour (seen 3 Nov 2026)
            return psycopg.connect(url, row_factory=dict_row, connect_timeout=20, keepalives=1, keepalives_idle=30,
                                   keepalives_interval=10, keepalives_count=3, **kw)
        except psycopg.OperationalError:
            if i == 9:
                raise
            time.sleep(min(60, 5 * (i + 1)))


@contextmanager
def conn():
    with _connect() as c:
        yield c


def cfg(c, key, tenant=None):
    row = c.execute("select value from config where tenant_id=%s and key=%s", (tenant or config.TENANT, key)).fetchone()
    return row["value"] if row else None


def excluded_domain(c, domain, tenant=None):
    return bool(c.execute("select 1 from exclusions where tenant_id=%s and kind='domain' and value=%s",
                          (tenant or config.TENANT, domain)).fetchone())


def upsert_company(c, *, name, domain, country, segment, source, source_url, city=None, is_chain=None,
                   store_count=None, email_rule="check", tenant=None):
    """Insert or update by (tenant, domain). Without a domain: by (tenant, country, lower(name), city).
    Never overwrites a segment/status a later stage has already decided. Returns (id, created)."""
    t = tenant or config.TENANT
    if domain:
        row = c.execute("select id from companies where tenant_id=%s and domain=%s", (t, domain)).fetchone()
    else:
        row = c.execute("select id from companies where tenant_id=%s and domain is null and country=%s "
                        "and lower(name)=lower(%s) and coalesce(city,'')=coalesce(%s,'')",
                        (t, country, name, city)).fetchone()
    if row:
        # a chain stays a chain: a single branch with the same website never downgrades it
        c.execute("update companies set is_chain=(coalesce(is_chain,false) or coalesce(%s,false)), "
                  "store_count=nullif(greatest(coalesce(%s,0),coalesce(store_count,0)),0), updated_at=now() where id=%s",
                  (is_chain, store_count, row["id"]))
        return row["id"], False
    status = "excluded" if domain and excluded_domain(c, domain, t) else "new"
    row = c.execute(
        "insert into companies(tenant_id,name,domain,country,city,segment,is_chain,store_count,email_rule,status,"
        "first_source,first_source_url) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (t, name, domain, country, city, segment, is_chain, store_count, email_rule, status, source, source_url)).fetchone()
    return row["id"], True


def evidence(c, company_id, fact, value, source_url, snippet=None, method="code"):
    c.execute("insert into company_evidence(company_id,fact,value,source_url,snippet,method) values (%s,%s,%s,%s,%s,%s)",
              (company_id, fact, str(value), source_url, (snippet or "")[:500], method))


def enqueue(c, kind, payload, dedupe, priority=0, tenant=None):
    import json
    c.execute("insert into jobs(tenant_id,kind,payload,dedupe,priority) values (%s,%s,%s,%s,%s) "
              "on conflict (tenant_id,dedupe) do nothing", (tenant or config.TENANT, kind, json.dumps(payload), dedupe, priority))
