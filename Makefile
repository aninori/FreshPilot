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
	uv run python -m freshpilot.catalog.run
	uv run python -m freshpilot.forecasting.run

pipeline:
	@echo "pipeline: daily pipeline orchestrator lands in M7"

eval:
	uv run python -m freshpilot.evals.run
	@echo "eval: G7 (faithfulness) and G8 (LLM cost) land in M6 (agents/guardrails)"

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
