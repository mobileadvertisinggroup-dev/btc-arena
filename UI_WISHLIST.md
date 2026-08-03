# AKRA ARENA — Owner UI wish list (recorded 2026-08-03, during 12h pilot)

Implementation window: AFTER the 12h pilot completes, bundled with the
always-on cloud runner. Both ship in ONE audit round → mentor issues new
engine + site digests together. The UI is integrity-locked until then.

## 1. Mobile fit (bug)
Page is slightly wider than the phone viewport — wiggles left/right when
swiping. Must be 100% mobile-friendly: no horizontal scroll anywhere
(`overflow-x` audit: ticker strip, chart svg, tables, panel cards).

## 2. Tidy the landing page
- **Leaderboard**: remove from the always-visible landing flow. Open it on
  click only (nav "Leaderboard" → separate view/overlay).
- **About the experiment**: same — collapsed by default, shown on click.
- Landing should lead with: ticker (tiny), chart, and the live panel.

## 3. Rename "Feature" → "TA" everywhere (display only)
Owner reads the feature arm as technical analysis. Replace every visible
"Feature"/"Feat" label with "TA" (cards, pair details, chart legend/labels,
leaderboard, filters). Internal ids/payload stay unchanged.

## 4. Open Positions — expandable thought process
Clicking a position card expands/collapses the full thesis (the model's
submitted reasoning that opened the position) — like Model Chat cards do.

## 5. Age column actually works
"age" currently shows "—". Show how long the position has been open
(boundary the position was entered at → now). Needs the engine payload to
expose the entry boundary/timestamp per open position (dashboard.py addition
→ part of the new engine digest anyway).

## 6. Invalidation — show the condition, not just the status
Instead of bare "NOT TRIGGERED", render the actual rule, e.g.
"NOT TRIGGERED — invalidates if price ≥ 63,900 (1h close)". Data already in
the payload (operator/level/timeframe); Open Positions panel must render it.

## 7. Model Chat: good as is — no changes.

## 8. Remove "View demo scenario" button
Demo era is over. Remove the button from the visible UI; demo stays
reachable only via `?demo=1` URL (keeps DOM tests + labeled demo honest).

## 9. Coin tab selected state
When BTC / ETH / SOL is selected the tab must clearly light up (obvious
active styling). Currently not visible enough — audit the `.tab.active`
style on the chart coin tabs.

## 10. Raw-vs-TA comparison table (replaces the always-on leaderboard space)
Per model+coin trade comparison, two columns (RAW | TA), covering ongoing
AND closed trades: entry, stop loss, target, size, status
(ongoing/closed), and each arm's stated reasoning/analysis snippet — a
side-by-side "what did TA do vs what did Raw do" view.

## 11. Ticker much smaller
BTC/ETH/SOL display prices take far too much space. Shrink drastically —
small inline row, tiny "updated HH:MM:SS" stamp. Keep the
non-authoritative disclaimer (can be a tooltip/footnote).

## 12. "Make it fly" — always-on cloud runner (already-committed requirement)
Official 14-day experiment must not depend on the Mac: VPS + systemd
runner (restart-safe), secrets on the server. New official runner script →
new engine digest → mentor approval → owner activation.

## Process notes
- Every item above changes the SITE digest (and #5/#12 the ENGINE digest);
  nothing may deploy mid-pilot (publisher verifies site integrity before
  every publication).
- After implementation: run full suite + DOM contract tests, regenerate
  audit archive, report new digests to mentor, await approval, then deploy.
