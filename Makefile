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
	uv run python -m freshpilot.pipeline.run

eval:
	uv run python -m freshpilot.evals.run

api:
	uv run uvicorn freshpilot.api.app:app --host 127.0.0.1 --port 8000

web:
	cd web && npm run dev

demo:
	uv run python -m freshpilot.demo

test:
	FRESHPILOT_LLM=off uv run pytest
	uv run ruff check .
	uv run mypy freshpilot
