"""T1 시드 요청·응답 (S2-4 PR-2)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.modules.seeds.service import CatalogStatus


class TemplateStateSummary(BaseModel):
    key: str
    name: str
    applies_to: str
    requirement_type: str
    source_url: str
    existing_status: str | None


class MarketStateSummary(BaseModel):
    code: str
    name_ko: str
    registered: bool
    templates: list[TemplateStateSummary]


class CatalogStatusSummary(BaseModel):
    version: str
    notice: list[str]
    markets: list[MarketStateSummary]

    @classmethod
    def of(cls, view: CatalogStatus) -> CatalogStatusSummary:
        return cls.model_validate(asdict(view))


class ApplyT1Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markets: Annotated[list[str], Field(min_length=1, max_length=20)]


class MarketApplySummary(BaseModel):
    code: str
    market_created: bool
    created: list[str]
    skipped: list[str]


class ApplyT1Response(BaseModel):
    version: str
    results: list[MarketApplySummary]


class WizardStepSummary(BaseModel):
    step: int
    key: str
    title: str
    status: str
    counts: dict[str, int]
    note: str | None


class WizardSummary(BaseModel):
    code: str
    market_id: int | None
    market_name: str | None
    catalog_available: bool
    catalog_template_count: int
    steps: list[WizardStepSummary]
    counted_steps: int
    done_steps: int
