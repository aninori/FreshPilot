"""Shared run-level spend accounting and deadline-bounded OpenAI calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from freshpilot.config import Settings


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class RunBudget:
    max_usd: float
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)


def complete(settings: Settings, budget: RunBudget, deadline: float, **kwargs: Any) -> Any:
    # Text-only standard pricing, verified 2026-09-25:
    # https://developers.openai.com/api/docs/models/gpt-4o-mini
    # Unknown models fail closed rather than silently applying the wrong rate.
    if settings.openai_model not in {"gpt-4o-mini", "gpt-4o-mini-2024-07-18"}:
        raise BudgetExceeded("No verified cost model configured for this model")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BudgetExceeded("Item deadline reached")
    max_output = 1024
    # Reserve a whole context plus maximum output. This deliberately overestimates
    # short prompts; successful calls refund the unused amount using token usage.
    reservation = (128000 * 0.15 + max_output * 0.60) / 1_000_000
    if budget.cost_usd + reservation > budget.max_usd:
        raise BudgetExceeded("Daily LLM cost cap reached")
    budget.cost_usd += reservation
    started = time.monotonic()
    record: dict[str, Any] = {"type": "llm", "cost_usd": reservation, "status": "failed_or_unknown_usage"}
    budget.calls.append(record)

    async def request() -> Any:
        async with AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=remaining) as client:
            return await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.openai_model, max_completion_tokens=max_output, **kwargs,
                ), timeout=max(0.001, deadline - time.monotonic()),
            )

    try:
        response = asyncio.run(request())
        if response.usage is not None:
            inp, out = response.usage.prompt_tokens, response.usage.completion_tokens
            actual = (inp * 0.15 + out * 0.60) / 1_000_000
            budget.cost_usd += actual - reservation
            budget.tokens_in += inp
            budget.tokens_out += out
            record.update(tokens_in=inp, tokens_out=out, cost_usd=actual, status="complete")
        if time.monotonic() >= deadline:
            raise BudgetExceeded("Item deadline reached")
        return response
    finally:
        record["duration_s"] = time.monotonic() - started
