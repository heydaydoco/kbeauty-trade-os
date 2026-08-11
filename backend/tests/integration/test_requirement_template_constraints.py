"""K. 보안·품질 — 요건 템플릿 4테이블의 불변식을 DB가 강제한다 (§5.1 / S2-1 조건 1·2).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — 모델이 선언한 CHECK·유니크가
  DB에 실재하는지를 실측한다(test_market_constraints와 같은 방식).

★ 이 파일의 핵심 둘:
  ① **확정 게이트의 DB 층**(confirmed_requires_evidence) — 서비스 층이
    뚫려도 근거 없는 CONFIRMED 행은 DB가 거부한다(§5.5 / GC-C8 / ADR-0033).
  ② **자기참조 금지 CHECK**(조건 1) — 순환 참조의 깊이 1을 DB가 막는다.
    깊이 2+의 순환은 서비스 검증 몫이고 e2e(test_requirement_templates)가 본다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.requirements.models import (
    ItemProfileRequirementTemplate,
    RequirementTemplate,
    TemplateChecklistItem,
    TemplatePrerequisite,
)
from tests.support.factories import create_item_profile, create_market

pytestmark = pytest.mark.group_k

#: 근거 2필드 완비 세트 — 확정 게이트 케이스가 재사용한다.
_EVIDENCE = {"source_url": "https://example.test/rule", "last_verified_on": date(2026, 8, 1)}


def _insert_template(**overrides: Any) -> int:
    market_id = overrides.pop("market_id", None)
    if market_id is None:
        market_id = create_market("US")
    columns: dict[str, Any] = {
        "market_id": market_id,
        "name": "MoCRA 시설등록",
        "applies_to": "FACILITY",
        "requirement_type": "REGISTRATION",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = RequirementTemplate(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _constraint_definition(name: str) -> str | None:
    with unit_of_work() as uow:
        return uow.session.execute(
            text(f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = '{name}'")
        ).scalar_one_or_none()


# ── CHECK 실재 전수 (함정 ① — 수기 반영 누락 검출) ──────────────────────────

_DECLARED_CHECKS = {
    "requirement_templates": frozenset(
        {
            "ck_requirement_templates_applies_to_valid",
            "ck_requirement_templates_status_valid",
            "ck_requirement_templates_requirement_type_format",
            "ck_requirement_templates_validity_months_positive",
            "ck_requirement_templates_renewal_cycle_months_positive",
            "ck_requirement_templates_renewal_lead_days_positive",
            "ck_requirement_templates_estimated_cost_pair",
            "ck_requirement_templates_estimated_cost_amount_nonnegative",
            "ck_requirement_templates_estimated_cost_currency_uppercase",
            "ck_requirement_templates_source_url_not_blank",
            "ck_requirement_templates_confirmed_requires_evidence",
        }
    ),
    "template_checklist": frozenset({"ck_template_checklist_seq_positive"}),
    "template_prerequisites": frozenset({"ck_template_prerequisites_no_self_reference"}),
}


def test_every_declared_check_constraint_exists() -> None:
    """모델이 선언한 CHECK가 전부 DB에 실재한다 (마이그레이션 수기 반영 누락 검출)"""
    for table, declared in _DECLARED_CHECKS.items():
        with unit_of_work() as uow:
            names = set(
                uow.session.execute(
                    text(
                        f"""
                        SELECT conname FROM pg_constraint
                        WHERE conrelid = 'public.{table}'::regclass AND contype = 'c'
                        """
                    )
                )
                .scalars()
                .all()
            )
        missing = declared - names
        assert not missing, (
            f"{table}: 모델에는 있는데 DB에 없는 CHECK: {sorted(missing)} — "
            "autogenerate는 CHECK를 감지하지 못합니다. 마이그레이션에 손으로 추가하세요."
        )


def test_confirmed_gate_definition_matches_the_model() -> None:
    """확정 게이트 CHECK 정의문이 모델 선언 그대로 실재한다 (조건 2 — pg_get_constraintdef)"""
    definition = _constraint_definition("ck_requirement_templates_confirmed_requires_evidence")
    assert definition is not None
    assert "CONFIRMED" in definition
    assert "source_url IS NOT NULL" in definition
    assert "last_verified_on IS NOT NULL" in definition


def test_self_reference_check_definition_matches_the_model() -> None:
    """자기참조 금지 CHECK 정의문 실재 (조건 1)"""
    definition = _constraint_definition("ck_template_prerequisites_no_self_reference")
    assert definition is not None
    assert "template_id <> prerequisite_template_id" in definition


def test_shortened_fk_definitions_match_the_model() -> None:
    """63자 초과로 수기 축약한 FK 3건의 정의문이 실재한다 (조건 5 양식)"""
    expected = {
        "fk_template_prerequisites_prerequisite_requirement_templates": (
            "FOREIGN KEY (prerequisite_template_id) REFERENCES requirement_templates(id)"
        ),
        "fk_item_profile_requirement_templates_profile": (
            "FOREIGN KEY (item_profile_id) REFERENCES item_profiles(id)"
        ),
        "fk_item_profile_requirement_templates_template": (
            "FOREIGN KEY (requirement_template_id) REFERENCES requirement_templates(id)"
        ),
    }
    for name, fragment in expected.items():
        definition = _constraint_definition(name)
        assert definition is not None, f"{name}가 DB에 없습니다."
        assert fragment in definition, (name, definition)
        assert "ON DELETE RESTRICT" in definition, (name, definition)


def test_partial_unique_indexes_exist_with_the_soft_delete_condition() -> None:
    """멱등 UNIQUE 4건이 전부 부분 인덱스로 실재한다 (§17.4 — 수기 축약 2건 포함)"""
    expected = {
        "requirement_templates": "uq_requirement_templates_market_id_name_active",
        "template_checklist": "uq_template_checklist_template_id_seq_active",
        "template_prerequisites": "uq_template_prerequisites_pair_active",
        "item_profile_requirement_templates": (
            "uq_item_profile_requirement_templates_profile_template_active"
        ),
    }
    with unit_of_work() as uow:
        for table, index_name in expected.items():
            definition = uow.session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes"
                    f" WHERE tablename = '{table}' AND indexname = '{index_name}'"
                )
            ).scalar_one_or_none()
            assert definition is not None, f"{index_name}가 DB에 없습니다."
            assert "UNIQUE" in definition, (index_name, definition)
            assert "deleted_at IS NULL" in definition, (index_name, definition)


# ── 확정 게이트 (DB 층 — §5.5·GC-C8 계보) ──────────────────────────────────


def test_confirmed_without_any_evidence_is_rejected_by_the_database() -> None:
    """근거 2필드 없는 CONFIRMED 행은 DB가 거부한다 — 서비스가 뚫려도 마지막 층이 막는다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_template(status="CONFIRMED")
    assert "confirmed_requires_evidence" in str(exc.value)


def test_confirmed_without_the_verified_date_is_rejected_by_the_database() -> None:
    """근거링크만 있고 최종확인일이 없는 CONFIRMED도 거부한다 (둘 다 필수 — ADR-03)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_template(status="CONFIRMED", source_url=_EVIDENCE["source_url"])
    assert "confirmed_requires_evidence" in str(exc.value)


def test_confirmed_with_full_evidence_is_accepted() -> None:
    """근거 완비 CONFIRMED는 통과한다 — 게이트는 근거의 부재만 막는다"""
    _insert_template(status="CONFIRMED", **_EVIDENCE)


def test_a_draft_needs_no_evidence() -> None:
    """초안은 근거 없이 실재할 수 있다 — nullable의 이유(§5.5 확정 차단의 전제)"""
    _insert_template()


def test_a_blank_source_url_is_rejected_by_the_database() -> None:
    """공백 근거링크는 거부한다 — 공백이 게이트를 통과하면 게이트가 형식이 된다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_template(source_url="   ")
    assert "source_url_not_blank" in str(exc.value)


# ── 값·형식·금액 CHECK ─────────────────────────────────────────────────────


def test_an_unknown_applies_to_is_rejected_by_the_database() -> None:
    """적용단위는 5종뿐이다 (WBS DoD 문면 — PRODUCT/SKU/FACILITY/COMPANY/INGREDIENT)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_template(applies_to="COUNTRY")
    assert "applies_to_valid" in str(exc.value)


def test_an_unknown_status_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_template(status="PENDING")
    assert "status_valid" in str(exc.value)


def test_a_lowercase_requirement_type_is_rejected_by_the_database() -> None:
    """유형 코드는 대문자 코드 문자열이다 — 정규화는 서비스, 강제는 CHECK"""
    with pytest.raises(IntegrityError) as exc:
        _insert_template(requirement_type="registration")
    assert "requirement_type_format" in str(exc.value)


def test_a_zero_validity_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_template(validity_months=0)
    assert "validity_months_positive" in str(exc.value)


def test_an_amount_without_currency_is_rejected_by_the_database() -> None:
    """금액과 통화는 한 쌍이다 (ADR-0003 ④ — credit_limit 선례 꼴)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_template(estimated_cost_amount=500_000)
    assert "estimated_cost_pair" in str(exc.value)


def test_a_negative_amount_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_template(estimated_cost_amount=-1, estimated_cost_currency="USD")
    assert "estimated_cost_amount_nonnegative" in str(exc.value)


def test_a_lowercase_currency_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_template(estimated_cost_amount=100, estimated_cost_currency="usd")
    assert "estimated_cost_currency_uppercase" in str(exc.value)


# ── 유일키 거동 (§17.4 — 멱등 UNIQUE는 부분 인덱스) ─────────────────────────


def test_a_duplicate_name_in_the_same_market_is_rejected() -> None:
    market_id = create_market("US")
    _insert_template(market_id=market_id)
    with pytest.raises(IntegrityError) as exc:
        _insert_template(market_id=market_id)
    assert "uq_requirement_templates_market_id_name_active" in str(exc.value)


def test_the_same_name_in_another_market_is_allowed() -> None:
    """유일키는 (시장, 이름)이다 — 같은 이름이 시장마다 실재한다(시설등록 등)"""
    _insert_template(market_id=create_market("US"))
    _insert_template(market_id=create_market("CA"))


def test_a_soft_deleted_name_can_be_registered_again() -> None:
    """soft delete된 이름의 재등록은 부활이 아니라 신규다 (§17.4 — markets와 다른 규율)"""
    market_id = create_market("US")
    _insert_template(market_id=market_id, deleted_at=utcnow())
    _insert_template(market_id=market_id)


def test_a_duplicate_checklist_seq_is_rejected() -> None:
    template_id = _insert_template()
    with unit_of_work() as uow:
        uow.session.add(
            TemplateChecklistItem(
                template_id=template_id, seq=1, item_name="시설 정보 양식", is_required=True
            )
        )
        uow.session.flush()
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            TemplateChecklistItem(
                template_id=template_id, seq=1, item_name="중복 순번", is_required=False
            )
        )
        uow.session.flush()
    assert "uq_template_checklist_template_id_seq_active" in str(exc.value)


def test_a_zero_checklist_seq_is_rejected() -> None:
    template_id = _insert_template()
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            TemplateChecklistItem(
                template_id=template_id, seq=0, item_name="순번 0", is_required=True
            )
        )
        uow.session.flush()
    assert "seq_positive" in str(exc.value)


# ── 선행요건 교차 테이블 (조건 1) ───────────────────────────────────────────


def test_a_self_prerequisite_is_rejected_by_the_database() -> None:
    """자기 자신을 선행요건으로 걸 수 없다 — 조건 1의 DB 층"""
    template_id = _insert_template()
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            TemplatePrerequisite(template_id=template_id, prerequisite_template_id=template_id)
        )
        uow.session.flush()
    assert "no_self_reference" in str(exc.value)


def test_a_duplicate_prerequisite_pair_is_rejected() -> None:
    market_id = create_market("CN")
    first = _insert_template(market_id=market_id, name="NMPA 비안")
    second = _insert_template(market_id=market_id, name="경내책임자 지정")
    with unit_of_work() as uow:
        uow.session.add(TemplatePrerequisite(template_id=first, prerequisite_template_id=second))
        uow.session.flush()
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(TemplatePrerequisite(template_id=first, prerequisite_template_id=second))
        uow.session.flush()
    assert "uq_template_prerequisites_pair_active" in str(exc.value)


# ── 품목군 연결 (부채 #15 요건 몫) ──────────────────────────────────────────


def test_a_duplicate_profile_template_pair_is_rejected() -> None:
    profile_id = create_item_profile("PRF-REQ")
    template_id = _insert_template()
    with unit_of_work() as uow:
        uow.session.add(
            ItemProfileRequirementTemplate(
                item_profile_id=profile_id, requirement_template_id=template_id
            )
        )
        uow.session.flush()
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            ItemProfileRequirementTemplate(
                item_profile_id=profile_id, requirement_template_id=template_id
            )
        )
        uow.session.flush()
    assert "uq_item_profile_requirement_templates_profile_template_active" in str(exc.value)


def test_a_soft_deleted_profile_link_can_be_added_again() -> None:
    """세트 해제 후 재추가는 신규다 (§17.4 — item_profile_document_types 선례)"""
    profile_id = create_item_profile("PRF-REQ2")
    template_id = _insert_template()
    with unit_of_work() as uow:
        uow.session.add(
            ItemProfileRequirementTemplate(
                item_profile_id=profile_id,
                requirement_template_id=template_id,
                deleted_at=utcnow(),
            )
        )
        uow.session.flush()
    with unit_of_work() as uow:
        uow.session.add(
            ItemProfileRequirementTemplate(
                item_profile_id=profile_id, requirement_template_id=template_id
            )
        )
        uow.session.flush()
