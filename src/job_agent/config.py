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
    fit_score_min: int = Field(ge=0, le=100)
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


class DjinniSourceConfig(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)


class DouSourceConfig(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)


class RemoteOkSourceConfig(BaseModel):
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class WwrSourceConfig(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)


class LinkedinSourceConfig(BaseModel):
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    results_wanted: int = 40


class NoFluffJobsSourceConfig(BaseModel):
    enabled: bool = False


class SourcesConfig(BaseModel):
    djinni: DjinniSourceConfig = Field(default_factory=DjinniSourceConfig)
    dou: DouSourceConfig = Field(default_factory=DouSourceConfig)
    remoteok: RemoteOkSourceConfig = Field(default_factory=RemoteOkSourceConfig)
    wwr: WwrSourceConfig = Field(default_factory=WwrSourceConfig)
    linkedin: LinkedinSourceConfig = Field(default_factory=LinkedinSourceConfig)
    nofluffjobs: NoFluffJobsSourceConfig = Field(default_factory=NoFluffJobsSourceConfig)


class BudgetConfig(BaseModel):
    monthly_usd: float = 10.0


class Config(BaseModel):
    mode: Mode = "wide"
    thresholds: dict[Mode, ThresholdConfig]
    models: ModelsConfig
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    categories: list[str] = Field(default_factory=list)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)

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
