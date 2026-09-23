"""Loads config.yaml (non-secret settings) and .env (secrets) into typed models."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["wide", "selective"]

# OpenRouter reasoning.effort values (verified live 2026-09-16), plus "omit"
# -- our own sentinel meaning "send no reasoning param at all" (some
# endpoints, e.g. z-ai/glm-5.3-flash, reject an explicit effort and require
# the param to be absent; see llm/client.py's reasoning-mandatory fallback).
ReasoningSetting = Literal["none", "minimal", "low", "medium", "high", "omit"]


class ThresholdConfig(BaseModel):
    """fit_score_min / maybe_score_min define the verdict bands the model's
    fit_score is sorted into (verdict is no longer LLM output -- see
    scoring.derive_verdict): >= fit_score_min -> "apply", >= maybe_score_min
    -> "maybe", else "skip". Unrelated to telegram.min_score (TelegramConfig
    below), which only controls which cards get sent."""

    fit_score_min: int = Field(ge=0, le=100)
    maybe_score_min: int = Field(ge=0, le=100, default=20)
    salary_floor_usd: int | None = None
    seniority_strict: bool = False


class ModelEntry(BaseModel):
    id: str
    reasoning: ReasoningSetting = "none"

    @property
    def reasoning_effort(self) -> str | None:
        return None if self.reasoning == "omit" else self.reasoning


def _coerce_model_entry(value: Any) -> Any:
    """Allows config.yaml to write a plain model-id string as shorthand for
    `{id: <string>}` (default reasoning: "none")."""
    if isinstance(value, str):
        return {"id": value}
    return value


ModelEntryField = Annotated[ModelEntry, BeforeValidator(_coerce_model_entry)]


class FallbacksConfig(BaseModel):
    bulk: list[ModelEntryField] = Field(default_factory=list)
    quality: list[ModelEntryField] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    bulk: ModelEntryField
    quality: ModelEntryField
    fallbacks: FallbacksConfig = Field(default_factory=FallbacksConfig)
    eval_candidates: list[ModelEntryField] = Field(default_factory=list)


# PROJECT.md §5.7: whether the package flow produces a manual-apply bundle
# (CV + message + direct apply link + checklist, with a "mark as
# submitted" button once the user confirms they applied) or -- once built
# -- hands off to an automated ATS filler. "manual" is not a placeholder
# for missing automation; for djinni/dou it's a permanent decision (see
# PROJECT.md §5.7's research note: DOU's own rules explicitly ban
# automated-application tools, and Djinni's automation policy couldn't be
# verified, so neither is ever automated regardless of what gets built).
ApplyPolicy = Literal["manual", "automated"]


class DjinniSourceConfig(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)
    apply_policy: ApplyPolicy = "manual"


class DouSourceConfig(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)
    apply_policy: ApplyPolicy = "manual"


class RemoteOkSourceConfig(BaseModel):
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    apply_policy: ApplyPolicy = "automated"


class WwrSourceConfig(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)
    # WWR has no QA/QA Automation/ML AI/Python category, and its ?term=
    # search param isn't filtered server-side (verified live 2026-09-22) --
    # this maps a WWR category slug to keywords matched client-side against
    # each item's title, for categories where that's a sound proxy (see
    # sources/wwr.py). {} by default: no source-of-truth match, no guess.
    keyword_categories: dict[str, list[str]] = Field(default_factory=dict)
    apply_policy: ApplyPolicy = "automated"


class LinkedinSourceConfig(BaseModel):
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    results_wanted: int = 40
    apply_policy: ApplyPolicy = "automated"


class NoFluffJobsSourceConfig(BaseModel):
    enabled: bool = False
    apply_policy: ApplyPolicy = "automated"


class SourcesConfig(BaseModel):
    djinni: DjinniSourceConfig = Field(default_factory=DjinniSourceConfig)
    dou: DouSourceConfig = Field(default_factory=DouSourceConfig)
    remoteok: RemoteOkSourceConfig = Field(default_factory=RemoteOkSourceConfig)
    wwr: WwrSourceConfig = Field(default_factory=WwrSourceConfig)
    linkedin: LinkedinSourceConfig = Field(default_factory=LinkedinSourceConfig)
    nofluffjobs: NoFluffJobsSourceConfig = Field(default_factory=NoFluffJobsSourceConfig)

    def apply_policy_for(self, source: str) -> ApplyPolicy:
        """`source` is a posting's own `source` column value (e.g. "djinni",
        "dou") -- returns "automated" for anything unrecognized rather than
        raising, so a future new source defaults to the safer (non-manual-
        bundle) path until explicitly configured."""
        by_source: dict[str, ApplyPolicy] = {
            "djinni": self.djinni.apply_policy,
            "dou": self.dou.apply_policy,
            "remoteok": self.remoteok.apply_policy,
            "wwr": self.wwr.apply_policy,
            "linkedin": self.linkedin.apply_policy,
            "nofluffjobs": self.nofluffjobs.apply_policy,
        }
        return by_source.get(source, "automated")


class BudgetConfig(BaseModel):
    monthly_usd: float = 10.0


class TelegramConfig(BaseModel):
    """Decoupled from the scoring/analysis thresholds above: min_score only
    controls which scored postings get a card sent to Telegram."""

    min_score: int = Field(ge=0, le=100, default=40)


class Config(BaseModel):
    mode: Mode = "wide"
    thresholds: dict[Mode, ThresholdConfig]
    models: ModelsConfig
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    categories: list[str] = Field(default_factory=list)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    @property
    def active_threshold(self) -> ThresholdConfig:
        return self.thresholds[self.mode]


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config(path: Path | str = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.yaml to {path} and edit it."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


def load_secrets(env_path: Path | str = ".env") -> Secrets:
    return Secrets(_env_file=str(env_path))
