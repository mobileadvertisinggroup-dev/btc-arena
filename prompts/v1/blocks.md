# V1.1 prompt sub-blocks — verbatim templates

Placeholders in `{BRACES}` are substituted by the engine. Every alternative a
block can render is listed. No other text may appear in these positions.

## POSITION_BLOCK

Flat:
```
flat — no position
```

Open position:
```
{SIDE} {QTY} {COIN} (${NOTIONAL} notional) | entry {ENTRY} | unrealized P&L {UPNL_SIGNED} | stop-loss {STOP_OR_none} | take-profit {TP_OR_none} | approx. liquidation price {LIQ}
```

## TRADES_BLOCK

No closed trades:
```
Closed trades: none yet.
```

With trades (at most 5 shown, oldest first):
```
Closed trades ({N_TOTAL} total, most recent 5 shown, oldest first):
{SIDE} | entry {ENTRY} | exit {EXIT} | P&L {PNL_SIGNED} | closed {CLOSED_TS} | reason {REASON}
```
`{REASON}` is one of: `decision_close`, `decision_flip`, `stop_loss`,
`take_profit`, `liquidation`.

## CONDITION_BLOCK

Holding a position, invalidation not yet triggered:
```
Your invalidation for this position (set when the position was opened; immutable for its lifecycle): {COIN} 1h close at or {below|above} {LEVEL} [intrabar variant: {COIN} price trades at or {below|above} {LEVEL}]. Status: NOT TRIGGERED as of this round. Do not submit a new invalidation while this position remains open.
```

Holding a position, invalidation triggered (latched):
```
Your invalidation for this position (set when the position was opened; immutable for its lifecycle): {COIN} 1h close at or {below|above} {LEVEL}. Status: TRIGGERED at {TRIGGER_TS} (price {TRIGGER_PRICE}). This record is permanent. Your thesis was invalidated by your own stated condition; decide and explain how you respond.
```

Flat, watch condition set:
```
Your watch condition (informational, not scored): {COIN} 1h close at or {below|above} {LEVEL}. Status: {NOT TRIGGERED as of this round | TRIGGERED at {TRIGGER_TS} (price {TRIGGER_PRICE})}.
```

Flat, no watch condition:
```
No watch condition set.
```

## MEMORY_BLOCK

First round of the account:
```
This is your first round. You have no prior notes.
```

Later rounds (last 3 theses, oldest first):
```
Your notes from previous rounds (oldest first):
[{ROUND_TS}] {THESIS_TEXT}
```

## RETRY / FAILURE MESSAGE (single validation retry)

Appended as a second user message when a decision fails semantic validation.
Exactly one retry is permitted; a second failure aborts the matched pair's
round (PAIR_ABORTED).

```
Your previous decision was rejected: {REASON_LIST}. The market snapshot and your account are unchanged. Submit a corrected decision using the submit_decision tool.
```

`{REASON_LIST}` items are drawn verbatim from this fixed set (no other feedback
text is ever shown to a model):

- "size_usd must be 0 when position is flat"
- "size_usd must be at least 10 when opening or holding a position"
- "size_usd must be a finite, non-negative number"
- "target notional {SIZE} exceeds the 5x leverage limit ({MAX}) — TARGET_EXCEEDS_MAX_LEVERAGE"
- "an invalidation is required when opening or reversing a position"
- "this position already has an immutable invalidation; invalidation must be null when holding, increasing, or reducing"
- "invalidation must be null when your decision leaves you flat"
- "watch_condition must be null when your decision leaves you holding a position"
- "stop_loss must be below the execution price for a long position"
- "stop_loss must be above the execution price for a short position"
- "take_profit must be above the execution price for a long position"
- "take_profit must be below the execution price for a short position"
- "invalidation level {LEVEL} is outside the accepted range (0.2x to 5x the execution price)"
- "watch_condition level {LEVEL} is outside the accepted range (0.2x to 5x the execution price)"

Transport errors (HTTP failures) are retried silently with the identical
original request and never produce model-visible text.
