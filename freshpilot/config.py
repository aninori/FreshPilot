"""Central config: every module reads randomness/env settings from here.

CLAUDE.md rule 6: use SEED everywhere randomness appears, so a run is
reproducible. Rule 4: everything must work with FRESHPILOT_LLM=off and no
API key.

Provider note (docs/progress.md M6): BRD/CLAUDE.md specify Anthropic
(ANTHROPIC_API_KEY/ANTHROPIC_MODEL). This project uses OpenAI instead, per
an explicit user decision — the user has OpenAI keys, not Anthropic ones.
CLAUDE.md itself is left unedited; this is a documented deviation, same
treatment as the M2 sentence-transformers substitution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, cast, get_args

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

LlmMode = Literal["auto", "on", "off"]

SEED: int = int(os.environ.get("SEED", "42"))


@dataclass(frozen=True)
class Settings:
    seed: int
    openai_api_key: str | None
    openai_model: str
    llm_mode: LlmMode
    max_run_usd: float
    demo_date: str | None
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str | None

    def __post_init__(self) -> None:
        if self.demo_date and date.fromisoformat(self.demo_date).isoformat() != self.demo_date:
            raise ValueError("FRESHPILOT_DEMO_DATE must be YYYY-MM-DD")

    @property
    def llm_enabled(self) -> bool:
        """Whether agents should attempt real LLM calls this run."""
        if self.llm_mode == "off":
            return False
        if self.llm_mode == "on":
            return True
        return bool(self.openai_api_key)


def load_settings() -> Settings:
    llm_mode_raw = os.environ.get("FRESHPILOT_LLM", "auto")
    llm_mode = cast(LlmMode, llm_mode_raw) if llm_mode_raw in get_args(LlmMode) else "auto"
    return Settings(
        seed=SEED,
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        llm_mode=llm_mode,
        max_run_usd=float(os.environ.get("FRESHPILOT_MAX_RUN_USD", "1.00")),
        demo_date=os.environ.get("FRESHPILOT_DEMO_DATE") or None,
        langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY") or None,
        langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY") or None,
        langfuse_host=os.environ.get("LANGFUSE_HOST") or None,
    )
