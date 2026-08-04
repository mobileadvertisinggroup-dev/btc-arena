# AKRA ARENA V1

A mentor-audited AI trading experiment. Three Claude models — **Haiku 4.5,
Sonnet 5, Opus 4.8** — trade **BTC, ETH and SOL** with paper money in **18
isolated $10,000 accounts**: for every coin+model, one **Raw** account (sees
only candles) and one **TA** account (same candles plus a fixed neutral
numerical feature summary). Hourly pair-atomic decision rounds, 5x max
leverage, machine-checked invalidations, stops enforced on 1-minute candles,
Kraken as the authoritative market-data source. PAPER MONEY ONLY.

Question: does the TA feature summary change an AI trader's behaviour and
performance?

**Dashboard (GitHub Pages, display only):**
https://mobileadvertisinggroup-dev.github.io/btc-arena/
The dashboard reads its live payload from the direct VPS endpoint
(https://live.akraarena.online/).

**Where it runs:** the official 14-day experiment executes on an always-on
VPS under systemd (`scripts/run_official_14d.py`, see `deploy/`), never on a
personal machine and never on GitHub Actions. GitHub hosts the source of
truth and the Pages dashboard; an optional asynchronous mirror pushes each
published payload after the fact.

**Governance:** engine and site are frozen by externally issued audit
digests; an independent mentor audits every change; the owner is the only
activation authority. See `CLAUDE.md` and `docs/handoff/`.

The original casual BTC-only pilot (separate repo history) was formally
frozen as PILOT / SYSTEM TEST / NOT VALID EXPERIMENTAL EVIDENCE. Inspired by
Nof1's Alpha Arena.
