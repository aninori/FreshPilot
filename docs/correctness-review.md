# Correctness repair and delivery investigation

The user requested that milestone gates remain enforced. M7–M10 have not been
started. The old M5 passing result is superseded by the corrected evaluation.

## Repairs

- Ordering consumes calibrated forecast endpoints. The test-tuned `0.50`
  target multiplier is removed; the target is the quantile at the critical ratio.
- Calculation IDs include a hash of all calculation inputs. Different inventory
  overrides no longer share one ID. Invalid/non-finite overrides are rejected.
- The simulator conserves stock. Sold units cannot also be counted as waste.
- Baseline decisions use their own book inventory and simulated sales history.
  Physical stock is reserved for fulfillment and evaluation.
- Historical waste rates are estimated before the holdout. Future realized waste
  cannot change prior orders. The report includes sales, waste, lost sales,
  margin, opening stock, receipts, and closing stock.
- Receipt opportunities and forecast horizons agree within each scenario.
  Generated-cadence simulations recover the delivery phase from historical
  receipts. Daily-delivery simulations explicitly use a one-day horizon.
- Bakery plans use a one-day horizon, single-unit production and same-day expiry,
  as ORD-5 requires, regardless of the longer catalog shelf life of packaged bread.
- Zero book inventory is no longer classified as phantom stock.
- G6 counts products actually escalated by routing, rather than all flagged items.
- G7/G8 report measured fallback behavior and explicitly do not claim live-provider
  verification. `make eval` returns a failure exit code when a goal fails.
- `FRESHPILOT_DEMO_DATE` selects the date for exception/agent evaluation and
  rejects malformed dates or dates without forecasts.

## Agent correctness

Each investigation has a separate ledger of trusted tool results. Provenance
and evidence are verified against that ledger, not against numbers or IDs supplied
by the model. Policy-band checks are part of graph validation. Failure history
survives retries and fallback; narration failures update the final route.

The LangGraph retry counter is updated in a state-writing node. Conditional
routing is pure; fallback is an explicit node. This prevents repeated invalid
responses from retrying indefinitely or losing the fallback state.

Explanations include three reasons and pass numerical checks. Thousands
separators, scientific notation, decimal fractions and percentages are parsed.
Only recorded impact calculations may authorize a dollar-impact claim.

The OpenAI client has a shared run budget, per-item deadline, and a limit of eight
actual tool invocations across retries. Async request cancellation enforces the
deadline; SDK retries are disabled. Unknown request usage retains its conservative
cost reservation. Provider exception strings are excluded from logs.

The cost model currently supports GPT-4o mini and its dated snapshot, using
[documented standard text-token rates](https://developers.openai.com/api/docs/models/gpt-4o-mini).
Unsupported model prices fail closed to templates rather than using an incorrect rate.
Mocked provider tests cover failure, timeout, usage accounting and malicious outputs.
Live-provider acceptance, full pipeline trace persistence, and a catalog-backed
shelf-capacity field are still outstanding M6/M7 integration work.

## Delivery assumptions and the milestone gate

Run `uv run python -m freshpilot.evals.diagnose` to regenerate
`reports/delivery-diagnostic.json` and `.md`. The diagnostic compares:

1. **Generated cadence, literal BRD baseline:** the synthetic department delivery
   intervals apply to both policies, and the baseline orders last week's same-day
   sales minus book inventory. This is the main evaluation.
2. **Daily deliveries:** both policies can replenish daily; both cover one day.
   This is a sensitivity check, not a substitute for the main gate.
3. **Generated cadence, cycle baseline:** the baseline sums last week's sales for
   every day until the next delivery. This is a proposed baseline clarification,
   not the formula currently specified by EVAL-2.

The baseline ambiguity is that FC-7/ORD-2 specify delivery-cycle demand, while EVAL-2's
baseline specifies a single day's sales. Supplying one day every several days
creates severe under-ordering and artificially low baseline waste. Choosing a
passing scenario after seeing the test results would not resolve that ambiguity.

The corrected main evaluation passes without changing the baseline: waste is
6.15% versus 6.50%, and stockout frequency is 18.92% versus 61.46%. Both sensitivity
checks also pass. Under the stronger cycle-aware baseline, baseline waste is
6.39% and stockouts are 21.36%. These results preserve the original milestone gate;
they do not require selecting a different baseline or tuning on the holdout.

No forecast multiplier, goodwill penalty, or disposal cost has been selected using
these test results. Any policy calibration must be fitted before the final test
window and reported separately.

Remaining simulator limitations: supplier lead times/weekdays are not connected
to the generator's department schedule; multi-day goods lack FIFO expiry; rolling
forecasts use the recorded historical sales path rather than counterfactual
retraining; new phantom events are not replayed in the counterfactual simulator.

## Verification

Final check counts and measured gate results are recorded in `docs/progress.md`
and generated `reports/eval.md`. A failing business metric is kept visible and
does not justify changing a safety threshold or weakening the milestone gate.
