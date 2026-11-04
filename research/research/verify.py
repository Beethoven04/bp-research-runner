"""Verification chain: syntax → MX (DNS) → Reacher SMTP check (self-hosted) → Truemail SMTP check (self-hosted,
open source, only when Reacher has no answer) → Reoon (free credits, last opinion).

Verdicts: valid | invalid | risky (catch-all / unconfirmable) | unknown (no verifier answered) | no_mx
Every answer is cached in data/research_cache/verify.json so an address is never checked twice.
"""
import json
import threading
import time
import uuid

import dns.resolver
import requests

from . import config
from .emails import EMAIL_RE, is_role

_CACHE_FILE = config.CACHE / "verify.json"
_cache = json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
_mx_cache = {}


_lock = threading.Lock()


def _save():
    with _lock:            # verify() runs in parallel threads (verify_inline, 3 Nov 2026)
        for _ in range(5):     # another thread may add a key while we copy
            try:
                data = json.dumps(dict(_cache), indent=1, sort_keys=True)
                break
            except RuntimeError:
                time.sleep(0.05)
        else:
            return
        _CACHE_FILE.write_text(data)


def mx(domain: str):
    """Returns (hosts, provider) or ([], 'none')."""
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        ans = dns.resolver.resolve(domain, "MX", lifetime=8)
        hosts = sorted((r.preference, str(r.exchange).rstrip(".").lower()) for r in ans)
        hosts = [h for _, h in hosts]
    except Exception:
        hosts = []
    joined = " ".join(hosts)
    provider = ("google" if "google" in joined else "microsoft" if "outlook" in joined or "protection.outlook" in joined
                else "proofpoint" if "pphosted" in joined else "mimecast" if "mimecast" in joined
                else "none" if not hosts else "other")
    _mx_cache[domain] = (hosts, provider)
    return hosts, provider


def reacher(email: str):
    if not config.REACHER_URL:
        return None
    try:
        r = requests.post(config.REACHER_URL.rstrip("/") + "/v0/check_email",
                          headers={"x-reacher-secret": config.REACHER_SECRET, "content-type": "application/json"},
                          json={"to_email": email}, timeout=90)
        if r.status_code != 200:
            return {"error": f"http {r.status_code}: {r.text[:120]}"}
        return r.json()
    except requests.RequestException as e:
        return {"error": str(e)[:160]}


def truemail(email: str):
    """Truemail answers only "accepted or not". It cannot see a catch-all, so a random address on the same domain is
    tried too: both accepted = catch-all (risky); real accepted and random refused = valid. A refusal is not trusted
    as invalid (blocked IP, greylisting look the same), so it stays unknown."""
    if not config.TRUEMAIL_URL:
        return None
    def ask(addr):
        r = requests.get(config.TRUEMAIL_URL.rstrip("/"), params={"email": addr}, timeout=90,
                         headers={"Authorization": config.TRUEMAIL_TOKEN or "", "Accept": "application/json"})
        r.raise_for_status()
        return r.json()
    try:
        real = ask(email)
        if not real.get("success"):
            return {"verdict": "unknown", "detail": f"truemail: refused {list((real.get('errors') or {}).keys())}"}
        probe = ask(f"zq{uuid.uuid4().hex[:10]}@{email.split('@')[1]}")
        if probe.get("success"):
            return {"verdict": "risky", "catch_all": True, "detail": "truemail: accepted, domain accepts anything"}
        return {"verdict": "valid", "catch_all": False, "detail": "truemail: accepted, random address refused"}
    except (requests.RequestException, ValueError) as e:
        return {"verdict": "unknown", "detail": f"truemail error: {str(e)[:100]}"}


# Measured 4 Nov 2026 on ~4,500 checks from GitHub runners (cloud IPs):
# these providers answer 90-100% "unknown": they refuse mailbox checks from cloud servers. Asking them again only
# costs time, so the address is marked "risky / unverifiable" (usable only when published on the shop's own site).
NO_ANSWER_MX = ("ionos.de", "t-online.de", "web.de", "gmx.net", "ovh.net", "agenturserver.de", "ispgateway.de",
                "hostedemail.com", "yahoodns.net")
# These reject probes from many cloud IPs with a hard "no" (All-Inkl: 305 normal info@ addresses "invalid", while the
# same kind of address passed from other runner IPs). A rejection here is not trusted: unknown, retried later.
UNTRUSTED_REJECT_MX = ("kasserver.com",)


def mx_suffix(hosts) -> str:
    """'mx00.ionos.de' -> 'ionos.de' (first MX host)."""
    if not hosts:
        return ""
    h = hosts[0] if isinstance(hosts[0], str) else hosts[0][-1]
    return ".".join(str(h).rstrip(".").lower().split(".")[-2:])


_reoon_empty = []    # set once Reoon reports no credits left in this process


def reoon_has_credits() -> bool:
    if _reoon_empty:
        return False
    try:
        b = requests.get("https://emailverifier.reoon.com/api/v1/check-account-balance/",
                         params={"key": config.REOON_KEY}, timeout=20).json()
        if (b.get("remaining_daily_credits") or 0) + (b.get("remaining_instant_credits") or 0) <= 0:
            _reoon_empty.append(1)
            return False
    except (requests.RequestException, ValueError):
        pass
    return True


def reacher_verdict(reach, smtp):
    """Reacher says "risky" for every role address (info@...) even when the server confirmed the mailbox.
    Mailbox confirmed on a domain that is not catch-all = valid (the role flag is kept on the contact).
    Server refused it (full inbox / disabled) = invalid: it would bounce (3 Nov 2026)."""
    verdict = {"safe": "valid", "invalid": "invalid", "risky": "risky"}.get(reach, "unknown")
    if reach == "risky" and smtp.get("is_catch_all") is False:
        if smtp.get("is_deliverable") is True and not smtp.get("has_full_inbox") and not smtp.get("is_disabled"):
            verdict = "valid"
        elif smtp.get("is_deliverable") is False:
            verdict = "invalid"
    return verdict


def reoon(email: str):
    if not config.REOON_KEY:
        return None
    try:
        r = requests.get("https://emailverifier.reoon.com/api/v1/verify",
                         params={"email": email, "key": config.REOON_KEY, "mode": "power"}, timeout=60)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        return {"error": str(e)[:160]}


def verify(email: str, use_reoon_on_unknown: bool = True, force: bool = False) -> dict:
    email = email.strip().lower()
    if not force and email in _cache and _cache[email]["verdict"] in ("valid", "invalid", "risky", "no_mx"):
        return _cache[email]
    res = {"email": email, "checked_at": time.strftime("%Y-%m-%d %H:%M"), "role": is_role(email)}
    if not EMAIL_RE.fullmatch(email):
        res.update(verdict="invalid", detail="bad syntax")
        return res
    hosts, provider = mx(email.split("@")[1])
    res["mx_provider"] = provider
    if not hosts:
        res.update(verdict="no_mx", detail="domain has no MX record")
        _cache[email] = res; _save()
        return res
    verdict, detail = "unknown", "no verifier configured"
    sfx = mx_suffix(hosts)
    if sfx in NO_ANSWER_MX:
        verdict, detail = "risky", f"unverifiable: {sfx} refuses checks from cloud servers"
        if use_reoon_on_unknown and config.REOON_KEY and reoon_has_credits():   # Reoon checks from its own servers
            o = reoon(email)
            st = (o or {}).get("status")
            if st in ("safe", "valid"):
                verdict, detail = "valid", detail + f" | reoon: {st}"
            elif st in ("invalid", "disabled"):
                verdict, detail = "invalid", detail + f" | reoon: {st}"
            elif st:
                detail += f" | reoon: {st}"
        res.update(verdict=verdict, detail=detail)
        _cache[email] = res
        _save()
        return res
    r = reacher(email)
    if r is not None:
        if "error" in r:
            detail = "reacher error: " + r["error"]
        else:
            reach = r.get("is_reachable", "unknown")
            smtp = r.get("smtp") or {}
            catch_all = smtp.get("is_catch_all")
            detail = f"reacher: {reach}, catch_all={catch_all}, deliverable={smtp.get('is_deliverable')}"
            verdict = reacher_verdict(reach, smtp)
            res["catch_all"] = catch_all
            if verdict == "invalid" and sfx in UNTRUSTED_REJECT_MX:
                verdict, detail = "unknown", detail + f" | {sfx} rejects many cloud IPs: not trusted, retry"
    if verdict == "unknown":
        t = truemail(email)
        if t is not None:
            detail += " | " + t["detail"]
            if t["verdict"] != "unknown":
                verdict = t["verdict"]
                res["catch_all"] = t.get("catch_all")
    if verdict == "unknown" and use_reoon_on_unknown and config.REOON_KEY and reoon_has_credits():
        o = reoon(email)
        if o is not None:
            if "error" in o or "status" not in o:
                detail += f" | reoon error: {str(o)[:120]}"
            else:
                st = o.get("status")
                detail += f" | reoon: {st}, score {o.get('overall_score')}"
                verdict = {"safe": "valid", "valid": "valid", "invalid": "invalid", "disabled": "invalid",
                           "catch_all": "risky", "role_account": "risky", "inbox_full": "risky"}.get(st, "unknown")
                if st == "catch_all":
                    res["catch_all"] = True
    res.update(verdict=verdict, detail=detail)
    _cache[email] = res
    _save()
    return res
