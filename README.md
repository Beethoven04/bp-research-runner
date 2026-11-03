# bp-research-runner

Public runner for the research worker. It holds only code: no data, no keys, no business documents.

- `.github/workflows/research.yml` runs one batch: search engine (SearXNG) and email checker (Reacher) start as
  containers, then the worker finds and checks shops, verifies new contacts, and updates the Google Sheet.
- Runs every hour on a GitHub schedule (cron "17 * * * *"), or by hand. One run at a time.
- Keys come from encrypted GitHub Secrets (`DATABASE_URL`, `REOON_KEY`, `SHEET_ID`, `GOOGLE_SA_JSON`).
- Logs print counts only. All details are in the database.

This repository is generated from the private project by `scripts/publish_runner.sh`. Do not edit it here.
