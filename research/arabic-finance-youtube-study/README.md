# Arabic Faceless Finance YouTube — Forensic Competitive Study

Standalone research project. **Touches nothing in the BTC Arena trading project.**

## Status

| Stage | State |
|---|---|
| Machine inspected, project scaffolded | Done |
| Collection + analysis pipeline built and unit-tested | Done |
| Live data collected | **Blocked — needs a YouTube Data API v3 key** |
| Report generated | Waiting on data |

## Why an API key is required

This session's network egress policy blocks almost everything:

| Source | Reachable | Note |
|---|---|---|
| `youtube.googleapis.com` | **Yes** | Official Data API v3 — the only viable source |
| `www.youtube.com` | No | Proxy denies CONNECT (403) |
| `vidiq.com`, `socialblade.com`, `playboard.co`, `noxinfluencer.com` | No | Egress blocked |
| `support.google.com` (YouTube policy docs) | No | Egress blocked |
| `en.wikipedia.org` | No | Egress blocked |

So the study runs entirely on the official, lawful, public metadata API — no scraping,
no video downloads, no private analytics.

## How to run

```bash
export YOUTUBE_API_KEY="AIza..."     # Google Cloud Console -> APIs & Services -> Credentials
./run_all.sh
```

Free tier is 10,000 quota units/day. A full run costs roughly:

| Step | Calls | Units |
|---|---|---|
| Discovery (`search.list`) | 24 | 2,400 |
| Verify candidates (`channels.list`) | ~12 | 12 |
| Collect ~40 channels × 24 months | ~900 | ~900 |
| **Total** | | **~3,300 / 10,000** |

## Layout

```
scripts/ytapi.py            API client: quota ledger, retries, raw-response archiving
scripts/db.py               SQLite schema (facts kept separate from derived coding)
scripts/discover.py         Phase 1  — find channels via the platform's own index
scripts/select_channels.py  Phase 1B — verify every channel exists, then rank
scripts/collect.py          Phase 2  — channel + video harvest, 24-month window
scripts/classify.py         Phase 4  — rule-based subject/title coding (labelled ESTIMATE)
scripts/analyze.py          Phase 3  — metrics with age-bias controls
data/youtube_study.sqlite   The database
data/processed/*.csv        Analysis outputs
evidence/raw_api/*.json     Every raw API response, timestamped — the audit trail
logs/quota_ledger.jsonl     Every call, its cost, and the running daily total
METHODOLOGY.json            Machine-readable methodology, definitions and limitations
```

## Evidence discipline

Every claim in the final report carries one of four labels:

- **FACT** — verbatim from the API, archived in `evidence/raw_api/`
- **CALCULATION** — deterministically derived by `scripts/analyze.py`
- **ESTIMATE** — rule-based inference (subject classification, the Shorts heuristic)
- **OPINION** — analyst judgement, including all creative recommendations

## Known limits, stated up front

- The API exposes **lifetime view counts only**. No watch time, CTR, retention or
  impressions — those are private analytics and are **not** estimated here.
- Whether a presenter shows their face, or whether whiteboard animation is used,
  **cannot** be read from the API. Those fields need human review and are recorded
  with the coder's name and the date.
- `search.list` ranking is personalised and time-varying, so the candidate set is
  dated and the query list recorded rather than claimed to be perfectly reproducible.
