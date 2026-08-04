# 12-HOUR PILOT RESULTS (2026-08-03, Asia/Beirut)

PAPER MONEY — NOT OFFICIAL EXPERIMENTAL EVIDENCE. Full immutable evidence:
docs/pilot-12h-archive/ (sealed tarball SHA-256
8d80b7cf7b9002aa03b847d8fdc47728c8e5794c8de3f93765e2292662dd823d).

## Headline
- 12/12 scheduled boundaries terminal: **10 rounds committed, 2 honest
  aborts**. 108 pair records = 90 PAIR_COMMITTED + 18 PAIR_ABORTED (all
  deadline_exceeded). 25/25 publications publicly verified. 11 closed
  trades; 15 positions open at the final mark.
- Preflight same morning: 18/18 PASS (all semantically valid first try).

## The two aborted rounds (explicit record)
- **20:00 Beirut**: home-network/DNS outage at publication time. The
  THINKING hard gate refused all model calls; the T+12 deadline expired
  during the outage; restart retried publication only and closed the round
  aborted. Zero trades, zero mutations.
- **21:00 Beirut**: GitHub Pages verification latency consumed the T..T+12
  budget; no request's 120s timeout could fit, so all 9 pairs aborted.
- CORRECTION: live commentary during the run initially called the 21:00
  round "committed" when its closure payload published. The authoritative
  ledger says PAIR_ABORTED x9. The engine records were always correct; the
  human commentary was wrong and is corrected here and in the archive's
  FINAL_REPORT.md.

## Final leaderboard (marked at BTC 63807.2 / ETH 1866.88 / SOL 73.9)
1. sol_haiku_ta   10551.36   7. sol_sonnet_ta  9966.36   13. eth_haiku_ta 9825.17
2. btc_haiku_ta   10406.63   8. eth_sonnet_raw 9963.84   14. eth_opus_ta  9806.40
3. eth_haiku_raw  10379.77   9. btc_sonnet_ta  9963.12   15. btc_opus_ta  9775.22
4. sol_haiku_raw  10330.81  10. btc_sonnet_raw 9960.57   16. btc_opus_raw 9775.22
5. btc_haiku_raw  10163.38  11. sol_sonnet_raw 9957.76
6. sol_opus_raw   10000.00  12. eth_sonnet_ta  9956.13
(exact Decimals in the archive's FINAL_REPORT.md and accounts data)

## Aggregates and observations (pilot-grade, NOT evidence)
- RAW total 90379.17 vs TA total 90115.21 (gap +263.95 to RAW, driven
  largely by one TA stop-out: eth_haiku_ta -188 net on a stopped long).
- Model behavior: Haiku traded patiently (early longs, few trades) and swept
  the top 5 when the market rallied all day; Opus shorted the morning and
  fought the rally to the bottom two; Sonnet clustered slightly negative
  with small sizes. One account (sol_opus_raw) never traded: exactly
  $10,000.00.
- Raw-vs-TA twin divergences occurred (e.g. BTC-haiku agreed rounds 1–2 then
  diverged 3–6; SOL-opus raw stayed flat while ta traded).
- 12 hours is far too short for conclusions — that is what the official
  14-day run is for.
