# FreshPilot

> FreshPilot is an independent proof of concept built on fully synthetic data. It is not affiliated with, and uses no data, code or branding from, any retailer or grocery software company.

**ML forecasts the range. Math decides the order. Agents explain and escalate.**

FreshPilot is an agentic operations layer for fresh-grocery ordering: machine learning forecasts demand as a range, deterministic math decides every order, and LLM agents investigate exceptions, explain decisions in plain language, and escalate only what a human must decide. See [`BRD.md`](BRD.md) for the full business requirements and [`docs/progress.md`](docs/progress.md) for build status.

This project is under active milestone-by-milestone development (see BRD §12). Screenshot, architecture diagram, results table and full run instructions land at M10.

## Status

Currently at **M0 — repo scaffold**. Nothing beyond tooling is implemented yet.

## Requirements

- Python 3.11+ ([uv](https://docs.astral.sh/uv/) manages the environment and lockfile)
- Node.js (for the web app, from M9 onward)
- GNU Make to run the `make` targets below. On Windows without Make on `PATH`, run the commands inside `Makefile` directly via `uv run ...`, or use WSL/Git Bash with Make installed.

## Commands

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

`make test` and CI always run in fallback mode (`FRESHPILOT_LLM=off`, no `ANTHROPIC_API_KEY` required) — the full system must work with no API key.

## License

MIT
