# CLAUDE.md — How to build FreshPilot

You are building FreshPilot, a proof of concept described in full in `BRD.md`. Read `BRD.md` completely before writing any code. The visual and interaction target for the web app is `docs/ui-reference.html`; open it and study every screen before starting milestone M9.

## Core rules

1. **LLMs plan and narrate; deterministic code computes and decides.** Never let an LLM produce an order quantity, forecast, probability or metric. Agents may only call read-only tools and `simulate_order`.
2. **Every quantity needs a `calc_id`.** If you add a number to an explanation or the UI, trace it back to an engine calculation or an eval output.
3. **No hardcoded metrics in the UI.** KPI tiles, impact numbers, model-health charts and counts come from the API, which reads pipeline and eval outputs.
4. **Fallback mode must always work.** Everything must run with `FRESHPILOT_LLM=off` and no `ANTHROPIC_API_KEY`. Tests and CI run in fallback mode.
5. **Synthetic data only.** Never fetch or reference real retailer data. Keep the independence notice in the README and the app footer.
6. **Deterministic.** Use `freshpilot.config.SEED` everywhere randomness appears.

## Working method

- Build milestone by milestone (BRD §12). Do not start a milestone until the previous acceptance check passes.
- At the start of each milestone, write a short plan as a checklist in `docs/progress.md`; tick items as you finish them.
- Write tests alongside code. Requirement IDs from the BRD (e.g. `ORD-8`, `GRD-3`) go in test names or docstrings.
- After each milestone, run `make test` and the milestone's acceptance command, then summarise results in `docs/progress.md`.
- If a BRD requirement looks wrong or conflicts with another, stop and explain the conflict with a proposed resolution instead of silently changing the design.
- Keep functions small and typed. `engine/` and `guardrails/` pass `mypy --strict`.

## Commands (create these in M0)

```bash
make setup       # uv sync, install web deps
make data        # generate synthetic data into data/freshpilot.duckdb
make train       # train forecasting and matching models (MLflow logs in mlruns/)
make pipeline    # run the daily pipeline for the demo date
make eval        # metrics + backtest -> reports/eval.json, reports/eval.md
make api         # FastAPI on :8000
make web         # Vite dev server on :5173
make demo        # data + train + pipeline + eval + api + web, one command
make test        # pytest (fallback mode) + ruff + mypy
```

## Environment

```
ANTHROPIC_API_KEY=            # optional; fallback mode if empty
ANTHROPIC_MODEL=claude-sonnet-5
FRESHPILOT_LLM=auto           # auto | on | off
FRESHPILOT_MAX_RUN_USD=1.00
FRESHPILOT_DEMO_DATE=         # defaults to last day of generated history
LANGFUSE_PUBLIC_KEY=          # optional
LANGFUSE_SECRET_KEY=          # optional
LANGFUSE_HOST=                # optional
SEED=42
```

## Agent implementation notes

- Use LangGraph for the investigator: nodes `triage`, `investigate`, `validate`, `route`, `narrate`. `triage`, `validate` and `route` are pure Python.
- Tool outputs are the only source of truth for numbers. Record every tool call (name, args, result summary, duration) in the trace store.
- Validate agent output against the Pydantic schemas in `agents/schemas.py`. On failure: retry once with the validation error, then use the fallback template.
- The faithfulness verifier (`guardrails/faithfulness.py`) extracts numbers from text and matches them against tool outputs and `calculations`; unit-test it with tricky cases ("6 cases (48 lb)", "15.2 lb/day", "< 0.1%", "−18 to −24%").
- Prompts live in `agents/prompts/` as plain text files, one per agent, with a short style guide: plain language, no jargon on manager-facing text, ≤ 60 words.

## Web app notes

- React 18 + Vite + TypeScript. Plain CSS with the colour tokens from `docs/ui-reference.html` (light and dark). No UI kit.
- Port the reference screen by screen; replace every hardcoded array in the reference with API data.
- Charts are small hand-built SVG components (as in the reference) with hover tooltips; no heavy chart library.
- Use the Tabler icon webfont as the reference does.

## Definition of done (whole project)

- `make demo` works from a clean clone in ≤ 10 minutes with no API key.
- All BRD §2 targets met and shown in `reports/eval.md`.
- Playwright smoke test passes: open Today → approve all decisions → send orders → open a trace in Observability.
- README has: pitch line, screenshot, architecture diagram, results table, how to run, independence notice.
