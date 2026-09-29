"""신규 시장 개설 위저드 — 단계별 진행 계산 (§5.5 / S2-4 PR-2 — ADR-0049).

★ **저장하지 않는다** — 각 단계의 완료는 데이터에서 그때그때 계산한다(진행 체크리스트+딥링크).
  세율 마스터·FTA 마스터는 Phase 4 몫이라 3단계는 "HS 등록 건수"까지, 4단계는 "모듈 도래 전"으로
  표시하고 완료 계산에서 **제외**한다(미도래 단계를 미완으로 세면 위저드가 영영 안 끝난다).
  AI 규제 리서치 결과의 스테이징 변환은 Phase 6(비포함).
★ 질의 수는 고정(시장 1·템플릿 1·HS 1·성분 규칙 1)이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.modules.catalog.models import SkuHsCode
from app.modules.ingredients.models import IngredientRule
from app.modules.markets.models import Market
from app.modules.markets.service import normalized_code
from app.modules.requirements.models import RequirementTemplate
from app.modules.seeds.catalog import load_catalog

StepStatus = Literal["DONE", "IN_PROGRESS", "TODO", "NOT_AVAILABLE"]

AGREEMENT_NOTE = (
    "협정(FTA) 마스터는 Phase 4에 도입됩니다 — 모듈 도래 전이라 완료 계산에서 제외합니다."
)
RATE_NOTE = (
    "세율 마스터는 Phase 4에 도입됩니다 — 지금은 SKU의 HS 세번 등록 건수까지 봅니다(메모+근거링크)."
)


@dataclass(frozen=True, slots=True)
class WizardStep:
    step: int
    key: str
    title: str
    status: StepStatus
    #: 화면이 보이는 근거 숫자(없으면 None) — 라벨은 화면이 붙인다.
    counts: dict[str, int]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class WizardView:
    code: str
    market_id: int | None
    market_name: str | None
    catalog_available: bool
    catalog_template_count: int
    steps: list[WizardStep]
    #: 완료 계산에 든 단계 수(미도래 단계 제외)와 그중 완료 수.
    counted_steps: int
    done_steps: int


def market_wizard(code: str) -> WizardView:
    normalized = normalized_code(code)
    catalog_market = load_catalog().market(normalized)
    with unit_of_work() as uow:
        session = uow.session
        market = session.execute(
            select(Market).where(Market.code == normalized, Market.deleted_at.is_(None))
        ).scalar_one_or_none()
        template_counts: dict[str, int] = {}
        if market is not None:
            template_counts = {
                status: int(count)
                for status, count in session.execute(
                    select(RequirementTemplate.status, func.count())
                    .where(
                        RequirementTemplate.market_id == market.id,
                        RequirementTemplate.deleted_at.is_(None),
                    )
                    .group_by(RequirementTemplate.status)
                ).all()
            }
        hs_count = int(
            session.execute(
                select(func.count())
                .select_from(SkuHsCode)
                .where(SkuHsCode.country_code == normalized, SkuHsCode.deleted_at.is_(None))
            ).scalar_one()
        )
        rule_count = int(
            session.execute(
                select(func.count())
                .select_from(IngredientRule)
                .where(
                    IngredientRule.country_code == normalized, IngredientRule.deleted_at.is_(None)
                )
            ).scalar_one()
        )
        market_id = market.id if market is not None else None
        market_name = market.name_ko if market is not None else None

    draft = template_counts.get("DRAFT", 0)
    confirmed = template_counts.get("CONFIRMED", 0)
    total_templates = sum(template_counts.values())
    if confirmed > 0 and draft == 0:
        template_status: StepStatus = "DONE"
    elif total_templates > 0:
        template_status = "IN_PROGRESS"
    else:
        template_status = "TODO"
    steps = [
        WizardStep(1, "market", "시장 등록", "DONE" if market_id is not None else "TODO", {}),
        WizardStep(
            2,
            "templates",
            "요건 템플릿",
            template_status,
            {"draft": draft, "confirmed": confirmed, "total": total_templates},
            "초안은 근거링크를 열어 확인일을 입력한 뒤 확정해야 합니다.",
        ),
        WizardStep(
            3,
            "hs",
            "HS 세번·세율",
            "DONE" if hs_count > 0 else "TODO",
            {"hs_codes": hs_count},
            RATE_NOTE,
        ),
        WizardStep(4, "agreements", "협정", "NOT_AVAILABLE", {}, AGREEMENT_NOTE),
        WizardStep(
            5,
            "ingredient_rules",
            "성분 규칙",
            "DONE" if rule_count > 0 else "TODO",
            {"rules": rule_count},
        ),
    ]
    counted = [step for step in steps if step.status != "NOT_AVAILABLE"]
    return WizardView(
        code=normalized,
        market_id=market_id,
        market_name=market_name,
        catalog_available=catalog_market is not None,
        catalog_template_count=len(catalog_market.templates) if catalog_market else 0,
        steps=steps,
        counted_steps=len(counted),
        done_steps=sum(1 for step in counted if step.status == "DONE"),
    )
