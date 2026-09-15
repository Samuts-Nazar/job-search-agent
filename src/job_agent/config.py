"""Loads config.yaml (non-secret settings) and .env (secrets) into typed models."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["wide", "selective"]


class ThresholdConfig(BaseModel):
    fit_score_min: int = Field(ge=0, le=100)
    salary_floor_usd: int | None = None
    seniority_strict: bool = False


class FallbacksConfig(BaseModel):
    bulk: list[str] = Field(default_factory=list)
    quality: list[str] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    bulk: str
    quality: str
    fallbacks: FallbacksConfig = Field(default_factory=FallbacksConfig)
    eval_candidates: list[str] = Field(default_factory=list)


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
