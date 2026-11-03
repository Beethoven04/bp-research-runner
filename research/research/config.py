"""Settings. Secrets come from research/.env (git-ignored); tenant facts from config/<tenant>.json (checked in)."""
import json
import os
from pathlib import Path

PKG = Path(__file__).resolve().parent          # research/research/
ROOT = PKG.parent                              # research/
REPO = ROOT.parent                             # repo root
DATA = REPO / "data"                           # private, git-ignored


def _load_env():
    """research/.env first, then the key files in private/ (reoon.env, woocommerce.env, ...). First value wins."""
    for env in [ROOT / ".env"] + sorted((REPO / "private").glob("*.env")):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


TENANT = get("TENANT", "b2bperfume")
DATABASE_URL = get("DATABASE_URL")          # Supabase Postgres connection string
REACHER_URL = get("REACHER_URL")            # e.g. http://127.0.0.1:8080 through an SSH tunnel
REACHER_SECRET = get("REACHER_SECRET")
TRUEMAIL_URL = get("TRUEMAIL_URL")          # open-source second SMTP checker (truemail-rack container)
TRUEMAIL_TOKEN = get("TRUEMAIL_TOKEN")
REOON_KEY = get("REOON_KEY")                # second opinion for Microsoft 365 / unknown
SEARXNG_URL = get("SEARXNG_URL")            # self-hosted web search
CACHE = DATA / "research_cache"
CACHE.mkdir(parents=True, exist_ok=True)


def tenant(name: str = "") -> dict:
    """Tenant config: our brands, detection-only brands, countries, segments. No secrets in it."""
    path = REPO / "config" / f"{name or TENANT}.json"
    return json.loads(path.read_text())
