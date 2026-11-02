"""Verification chain: syntax → MX (DNS) → Reacher SMTP check (self-hosted) → Reoon (optional second opinion).

Verdicts: valid | invalid | risky (catch-all / unconfirmable) | unknown (no verifier answered) | no_mx
Every answer is cached in data/research_cache/verify.json so an address is never checked twice.
"""
import json
import time

import dns.resolver
import requests

from . import config
from .emails import EMAIL_RE, is_role

_CACHE_FILE = config.CACHE / "verify.json"
_cache = json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
_mx_cache = {}


def _save():
    _CACHE_FILE.write_text(json.dumps(_cache, indent=1, sort_keys=True))


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
    r = reacher(email)
    if r is not None:
        if "error" in r:
            detail = "reacher error: " + r["error"]
        else:
            reach = r.get("is_reachable", "unknown")
            smtp = r.get("smtp") or {}
            catch_all = smtp.get("is_catch_all")
            detail = f"reacher: {reach}, catch_all={catch_all}, deliverable={smtp.get('is_deliverable')}"
            verdict = {"safe": "valid", "invalid": "invalid", "risky": "risky"}.get(reach, "unknown")
            res["catch_all"] = catch_all
    if verdict == "unknown" and use_reoon_on_unknown:
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
