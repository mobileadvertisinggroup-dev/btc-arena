# DESIGN PACK V1.3 — OWNER AMENDMENT (Neutral information, model freedom, minimal feature pack)

Status: amends V1.1 contract + V1.2 addendum. Trading engine still not authorized.

A. **Amended experiment question:** "Does giving an AI trader a fixed neutral
numerical feature summary, in addition to the same raw market data, change its
trading behaviour and performance across BTC, ETH, and SOL?" Treatment arm is
named the **Feature arm** in all scientific/public language (internal `_ta`
account IDs retained as permitted legacy). Core locked principle: the Feature
arm may receive precomputed mathematical summaries, but never semantic
judgments, trading guidance, recommended thresholds, or suggested actions.
Scientific limitation (verbatim, frozen): "V1 measures the effect of this
specific predeclared numerical feature pack. It does not establish that these
parameters are optimal or that technical indicators generally improve
trading."

B. **System-prompt changes:** new Information section inserted before Decision
rules, verbatim: (1) "You receive completed hourly OHLCV candles and completed
UTC daily closing prices. One-minute candles are used only by the simulation
engine for execution, stop-loss, take-profit, liquidation, gap handling,
replay, and intrabar invalidation. One-minute candles are not shown to you.
You may use any strategy and may hold a position across multiple decision
rounds." (2) "You may use, combine, or ignore any supplied information. No
supplied feature is a trading instruction, and no particular strategy is
required." No directive/primary-timeframe language exists anywhere.

C. **Prompt changes:** both arms gain the identical volume disclosure line
after the daily closes: "Volume is base-asset trading volume reported by the
Kraken spot market for each candle. It is not global crypto-market volume or
perpetual-futures volume." `user_ta.txt` is removed; the treatment template is
now `prompts/v1/user_feature.txt`, byte-identical to `user_raw.txt` except the
feature block.

D. **Revised feature block** (exact, the only Raw/Feature difference):
`=== FEATURE SUMMARY ===` + "All values below are computed only from the
candles shown above." + the eight outputs (nine display lines): RSI(14) hourly
(1 dp, n/a if insufficient); SMA(20) hourly; SMA(50) hourly; ATR(14) hourly
(all price-precision, visible candles only, frozen Wilder formulas); latest
completed 1h volume; mean volume of previous 24 completed 1h candles (latest
excluded); latest/previous-24h volume ratio (2 dp, n/a on zero baseline);
rolling VWAP(24h) (typical=(h+l+c)/3, latest 24 candles incl. latest, n/a on
zero volume) with price-minus-VWAP % (2 dp, explicit sign). No interpretation
words, labels, arrows, scores, or threshold meanings anywhere. Parameters
(RSI 14, SMA 20/50, ATR 14, 24 h windows) are predeclared, not claimed
optimal, and frozen for V1 (any change alters the experiment-config hash).

E. **Removed from the treatment arm:** MACD, Bollinger Bands, MFI, daily SMA,
daily RSI, Fear & Greed, sentiment, all signal classifications, and every
feature not explicitly listed in D. Daily closes remain raw in both arms.

F. **Schema confirmation:** `schemas/v1/decision.schema.json` is unchanged in
V1.3 and contains no `intended_horizon` field (machine-verified). Holding
duration is measured, never pre-declared by the model.

G. **Updated metrics:** all V1.2 metrics retained; added
feature-reference frequency (descriptive: thesis explicitly names RSI, SMA,
ATR, volume ratio, or VWAP; mention never treated as proof of causation);
Raw-vs-Feature decision disagreement, target-size difference, stop/target
differences, and turnover difference remain the primary paired behavioural
outcomes under the V1.2 locked denominators.

H. **Test additions:** no mandatory-timeframe language; no intended_horizon;
hold-across-rounds and use-or-ignore sentences present; Raw prompt has no
derived feature; Feature prompt differs only by the approved block;
byte-identity after block removal; exactly the eight approved outputs and no
others; MACD/Bollinger/MFI/daily-indicator/sentiment/interpretation-word
absence; RSI/SMA20/SMA50 from visible hourly closes only; ATR from visible
OHLC only; latest volume from the latest completed candle; prev-24 mean
excludes the latest; zero-baseline ratio determinism; VWAP window and
zero-volume n/a; identical volume disclosure in both arms; no prohibited word
inside the feature block; parameter changes necessarily change the
experiment-config hash.

I. Canonical-file validation results: see committed validation output in the
audit response (all checks passed at this commit).
