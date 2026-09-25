.PHONY: setup data train pipeline eval api web demo test

setup:
	uv sync
	@if [ -f web/package.json ]; then \
		cd web && npm install; \
	else \
		echo "setup: web/package.json not present yet (lands in M9) — skipping npm install"; \
	fi

data:
	uv run python -m freshpilot.data.generate
	uv run python -m freshpilot.data.summary

train:
	@echo "train: forecasting/matching training lands in M2-M3 (make train will log to mlruns/)"

pipeline:
	@echo "pipeline: daily pipeline orchestrator lands in M7"

eval:
	@echo "eval: metrics + backtest lands in M5/M6 (make eval will write reports/eval.json, reports/eval.md)"

api:
	@echo "api: FastAPI app lands in M8 (make api will serve on :8000)"

web:
	@echo "web: Vite dev server lands in M9 (make web will serve on :5173)"

demo:
	$(MAKE) data
	$(MAKE) train
	$(MAKE) pipeline
	$(MAKE) eval
	@echo "demo: api/web stages land in M8/M9 — full one-command demo arrives at M10"

test:
	FRESHPILOT_LLM=off uv run pytest
	uv run ruff check .
	uv run mypy freshpilot
