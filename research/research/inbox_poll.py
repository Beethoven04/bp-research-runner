"""Read new mail in the sending mailbox (emma@) and hand it to the n8n workflow "BP · 4 Inbox".

Why not the n8n IMAP trigger: TransIP closes an IMAP connection after 30 s without client traffic, also during IDLE
(tested 21 Nov 2026). n8n's IMAP library refreshes IDLE only every 5 min, so the trigger died every 30 s.
This poller connects, reads the messages with a UID above the last one seen, posts them to the Inbox webhook and
logs out. Messages are read with BODY.PEEK, so they stay unread in the mailbox.

State (last UID + UIDVALIDITY) lives in the database: config key 'inbox_state'. The UID only moves forward after
n8n answered 200, so a failed post is retried on the next run.

Runs every 5 min on GitHub Actions (runner/.github/workflows/inbox.yml); locally:
    python -m research.inbox_poll [--dry-run]
Env: IMAP_HOST, IMAP_USER, IMAP_PASSWORD, INBOX_WEBHOOK_URL, INBOX_WEBHOOK_KEY, DATABASE_URL.
Logs print counts only (the runner repo is public).
"""
import argparse
import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
import sys
import urllib.error
import urllib.request

from . import config, db

MAX_PER_RUN = 50                 # one run never posts more than this; the rest waits for the next run
MAX_TEXT = 60000                 # characters of text / html kept per message


def _env(k, default=""):
    v = os.environ.get(k, default)
    if not v:
        raise SystemExit(f"{k} missing")
    return v


def _addr(value):
    """'Name <a@b.c>, x@y.z' -> mailparser-like {text, value:[{address, name}]} (what classifyInbound expects)."""
    pairs = email.utils.getaddresses([str(value or "")])
    return {"text": str(value or ""), "value": [{"address": a, "name": n} for n, a in pairs if a]}


def _body(msg):
    text, html = "", ""
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html", "message/delivery-status"):
            continue
        try:
            payload = part.get_content()
        except Exception:
            raw = part.get_payload(decode=True) or b""
            payload = raw.decode(part.get_content_charset() or "utf-8", "replace")
        if not isinstance(payload, str):
            payload = str(payload)
        if ctype == "text/html":
            html += payload
        else:
            text += payload + "\n"
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", html)
    return text[:MAX_TEXT], html[:MAX_TEXT]


def parse(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text, html = _body(msg)
    headers = {}
    for k, v in msg.items():
        k = k.lower()
        headers[k] = headers[k] + " " + str(v) if k in headers else str(v)
    return {
        "from": _addr(msg.get("From")),
        "to": _addr(msg.get("To")),
        "subject": str(msg.get("Subject") or ""),
        "messageId": str(msg.get("Message-ID") or ""),
        "inReplyTo": str(msg.get("In-Reply-To") or ""),
        "references": str(msg.get("References") or ""),
        "date": str(msg.get("Date") or ""),
        "headers": headers,
        "text": text,
        "html": html,
    }


def load_state(c):
    row = c.execute("select value from config where tenant_id=%s and key='inbox_state'", (config.TENANT,)).fetchone()
    return row["value"] if row else {}


def save_state(c, st):
    c.execute("insert into config(tenant_id,key,value) values (%s,'inbox_state',%s) on conflict (tenant_id,key) "
              "do update set value=excluded.value, updated_at=now()", (config.TENANT, json.dumps(st)))
    c.commit()


def post(messages):
    req = urllib.request.Request(_env("INBOX_WEBHOOK_URL"), method="POST",
                                 data=json.dumps({"messages": messages}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "curl/8.7.1",
                                          "X-BP-Key": _env("INBOX_WEBHOOK_KEY")})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status != 200:
                raise RuntimeError(f"webhook answered {r.status}")
    except urllib.error.HTTPError as e:
        # public log: say who refused (n8n key check or Cloudflare), never the body itself
        body = e.read(2000).decode("utf-8", "replace")
        who = ("n8n: wrong key" if "Authorization data is wrong" in body else
               "cloudflare" if "cloudflare" in body.lower() or e.headers.get("cf-mitigated") else "unknown")
        raise SystemExit(f"inbox: webhook answered {e.code} ({who}, cf-ray {e.headers.get('cf-ray', '-')})")


def run(dry=False):
    m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.transip.email"), 993, timeout=60)
    m.login(_env("IMAP_USER"), _env("IMAP_PASSWORD"))
    typ, _ = m.select("INBOX", readonly=True)
    if typ != "OK":
        raise SystemExit("cannot open INBOX")
    validity = int((m.response("UIDVALIDITY")[1] or [b"0"])[0])
    with db.conn() as c:
        st = load_state(c)
        last = int(st.get("last_uid", 0)) if int(st.get("uidvalidity", 0)) == validity else 0
        typ, data = m.uid("search", None, f"UID {last + 1}:*")
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last)[:MAX_PER_RUN]
        msgs = []
        for uid in uids:
            typ, d = m.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((x[1] for x in d if isinstance(x, tuple)), None)
            if raw:
                msgs.append({"uid": uid, **parse(raw)})
        if msgs and not dry:
            post(msgs)
        if uids and not dry:
            save_state(c, {"uidvalidity": validity, "last_uid": uids[-1]})
    m.logout()
    print(f"inbox: {len(uids)} new message(s){' (dry run, nothing posted)' if dry else ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        run(a.dry_run)
    except imaplib.IMAP4.error as e:
        print("inbox: IMAP error", type(e).__name__)
        sys.exit(1)
