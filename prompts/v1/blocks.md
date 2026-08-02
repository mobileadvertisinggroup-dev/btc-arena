# V1 prompt sub-blocks — verbatim templates

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

With trades (most recent last; at most 5 shown):
```
Closed trades ({N_TOTAL} total, most recent 5 shown, oldest first):
{SIDE} | entry {ENTRY} | exit {EXIT} | P&L {PNL_SIGNED} | closed {CLOSED_TS} | reason {REASON}
```
`{REASON}` is one of: `decision_close`, `decision_flip`, `stop_loss`,
`take_profit`, `liquidation`.

## CONDITION_BLOCK

Holding a position, invalidation not yet triggered:
```
Your active invalidation (set {SET_TS}, permanent record): {COIN} 1h close {below|above} {LEVEL} [intrabar variant: {COIN} price trades {below|above} {LEVEL}]. Status: NOT TRIGGERED as of this round.
```

Holding a position, invalidation triggered (latched):
```
Your active invalidation (set {SET_TS}, permanent record): {COIN} 1h close {below|above} {LEVEL}. Status: TRIGGERED at {TRIGGER_TS} (price {TRIGGER_PRICE}). This record is permanent. Your thesis was invalidated by your own stated condition; decide and explain how you respond.
```

Flat, watch condition set and not triggered:
```
Your watch condition (informational, not scored): {COIN} 1h close {below|above} {LEVEL}. Status: NOT TRIGGERED as of this round.
```

Flat, watch condition triggered:
```
Your watch condition (informational, not scored): {COIN} 1h close {below|above} {LEVEL}. Status: TRIGGERED at {TRIGGER_TS} (price {TRIGGER_PRICE}).
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

## RETRY / FAILURE MESSAGE (validation retry)

Appended as a second user message when a decision fails semantic validation
and the round retry policy allows one retry:

```
Your previous decision was rejected: {REASON_LIST}. The market snapshot and your account are unchanged. Submit a corrected decision using the submit_decision tool.
```

`{REASON_LIST}` items are drawn verbatim from this fixed set:
- "stop_loss must be below the current price for a long position"
- "stop_loss must be above the current price for a short position"
- "take_profit must be above the current price for a long position"
- "take_profit must be below the current price for a short position"
- "invalidation is required when your decision leaves you holding a position"
- "invalidation level {LEVEL} is outside the accepted range (0.2x to 5x current price)"
- "watch_condition level {LEVEL} is outside the accepted range (0.2x to 5x current price)"
- "size_usd must be a non-negative number"

No other feedback text is ever shown to a model. Transport errors (HTTP
failures) are retried silently with the identical original request.
