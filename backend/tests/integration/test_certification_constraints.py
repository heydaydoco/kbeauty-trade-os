"""K. 보안·품질 — 인증 인스턴스 3테이블의 불변식을 DB가 강제한다 (§5.1·§5.2 / S2-2).

★ 서비스를 거치지 않고 ORM·SQL로 직접 넣는다 — 모델이 선언한 CHECK·유니크가
  DB에 실재하는지를 실측한다(test_requirement_template_constraints와 같은 방식).

★ 이 파일의 핵심 셋:
  ① **활성 한정 유니크의 술어**(판정 안건 ①) — 종결 2태(REJECTED·SUSPENDED)는
    유니크 밖(재도전=신규 인스턴스), 만료(EXPIRED)는 안(만료 후 재개=기존 행).
    COMPANY(target_id NULL)는 전용 부분 유니크가 잡는다(PG NULL 구별).
  ② **reason_required가 만료를 포함**(조건 B) — §5.2 "반려·만료·중단(사유
    필수)"의 기계화. 자동 전이가 사유를 채우므로 앱 비용은 0이다.
  ③ **certification_status_log 불변**(판정 안건 ④ — §17.5 확장) — 권한 목록이
    아니라 실제 UPDATE/DELETE/TRUNCATE를 때려 보고 DB 거부(42501)를 실측한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.certifications.models import (
    Certification,
    CertificationStatusLog,
    CertificationTask,
)
from app.modules.requirements.models import RequirementTemplate
from tests.support.factories import create_market, create_sku

pytestmark = pytest.mark.group_k

PERMISSION_DENIED = "42501"


def _insert_template(market_code: str = "US") -> int:
    with unit_of_work() as uow:
        row = RequirementTemplate(
            market_id=create_market(market_code),
            name=f"{market_code} 제품 등록",
            applies_to="SKU",
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 8, 1),
            status="CONFIRMED",
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _insert_certification(**overrides: Any) -> int:
    """제약 실측용 직접 INSERT — 스냅샷 필수 2필드는 기본값을 채운다."""
    columns: dict[str, Any] = {
        "target_type": "SKU",
        "template_name": "US 제품 등록",
        "requirement_type": "REGISTRATION",
    }
    columns.update(overrides)
    if "template_id" not in columns:
        columns["template_id"] = _insert_template()
    if columns["target_type"] != "COMPANY" and "target_id" not in columns:
        columns["target_id"] = create_sku()
    with unit_of_work() as uow:
        row = Certification(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _constraint_definition(name: str) -> str | None:
    with unit_of_work() as uow:
        return uow.session.execute(
            text(f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = '{name}'")
        ).scalar_one_or_none()


# ── CHECK 실재 전수 (함정 ① 계열 — 선언·실재 어긋남 검출) ────────────────────

_DECLARED_CHECKS = {
    "certifications": frozenset(
        {
            "ck_certifications_status_valid",
            "ck_certifications_target_type_valid",
            "ck_certifications_company_target_null",
            "ck_certifications_period_order",
            "ck_certifications_validity_months_positive",
            "ck_certifications_renewal_cycle_months_positive",
            "ck_certifications_renewal_lead_days_positive",
            "ck_certifications_source_url_not_blank",
            "ck_certifications_cert_number_not_blank",
        }
    ),
    "certification_tasks": frozenset(
        {
            "ck_certification_tasks_seq_positive",
            "ck_certification_tasks_done_pair",
        }
    ),
    "certification_status_log": frozenset(
        {
            "ck_certification_status_log_from_status_valid",
            "ck_certification_status_log_to_status_valid",
            "ck_certification_status_log_no_self_transition",
            "ck_certification_status_log_reason_required",
        }
    ),
}


@pytest.mark.parametrize(
    "constraint_name",
    sorted(name for names in _DECLARED_CHECKS.values() for name in names),
)
def test_declared_check_exists_in_db(constraint_name: str) -> None:
    """모델이 선언한 CHECK가 DB에 실재한다 — 이름으로 지목해 정의문을 받는다"""
    definition = _constraint_definition(constraint_name)
    assert definition is not None, f"{constraint_name}이 DB에 없습니다"
    assert definition.startswith("CHECK")


def test_reason_required_covers_expired() -> None:
    """조건 B — 사유 필수 CHECK의 대상이 반려·중단·만료 3종이다 (정의문 실측)"""
    definition = _constraint_definition("ck_certification_status_log_reason_required")
    assert definition is not None
    for status in ("EXPIRED", "REJECTED", "SUSPENDED"):
        assert status in definition


def test_open_unique_predicates_exclude_exactly_terminal_two() -> None:
    """활성 유니크 술어=종결 2태 제외·만료 포함 (판정 안건 ① 술어 전문 실측)"""
    with unit_of_work() as uow:
        rows = uow.session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE indexname IN ('uq_certifications_template_target_open',"
                " 'uq_certifications_company_open')"
            )
        ).all()
    assert len(rows) == 2
    for _name, definition in rows:
        assert "REJECTED" in definition and "SUSPENDED" in definition
        assert "EXPIRED" not in definition  # 만료는 유니크 안 — 재개=기존 행 경유
        assert "deleted_at IS NULL" in definition


# ── CHECK 거동 실측 ─────────────────────────────────────────────────────────


def test_status_value_check_rejects_unknown() -> None:
    """상태 값 밖 문자열은 DB가 거부한다 (층1 — 값 원천 차단)"""
    with pytest.raises(IntegrityError, match="ck_certifications_status_valid"):
        _insert_certification(status="ARCHIVED")


def test_company_requires_null_target() -> None:
    """COMPANY(자사 단일)에 target_id가 있으면 거부 — 양방향 CHECK의 한쪽"""
    with pytest.raises(IntegrityError, match="ck_certifications_company_target_null"):
        _insert_certification(target_type="COMPANY", target_id=1)


def test_non_company_requires_target() -> None:
    """COMPANY 아닌 대상에 target_id가 없으면 거부 — 양방향 CHECK의 다른 쪽"""
    with pytest.raises(IntegrityError, match="ck_certifications_company_target_null"):
        _insert_certification(target_type="SKU", target_id=None)


def test_company_with_null_target_is_accepted() -> None:
    """COMPANY+target_id NULL은 정상 — 과차단 방지 자기검사"""
    row_id = _insert_certification(target_type="COMPANY")
    assert row_id > 0


def test_period_order_check() -> None:
    """유효 시작이 만료일보다 뒤면 거부 (§17.5 기간 순서)"""
    with pytest.raises(IntegrityError, match="ck_certifications_period_order"):
        _insert_certification(valid_from=date(2027, 1, 1), expires_on=date(2026, 1, 1))


def test_task_done_pair_check() -> None:
    """체크 여부와 체크 시각은 한 쌍 — 어긋난 행은 거부"""
    certification_id = _insert_certification()
    with (
        pytest.raises(IntegrityError, match="ck_certification_tasks_done_pair"),
        unit_of_work() as uow,
    ):
        uow.session.add(
            CertificationTask(
                certification_id=certification_id,
                seq=1,
                item_name="CFS 준비",
                is_required=True,
                done=True,  # done_at 없이 완료 — 모순
            )
        )
        uow.session.flush()


def test_log_reason_required_for_expired() -> None:
    """만료 이력에 사유가 없으면 거부 (조건 B — 자동 기록이 채워야 한다)"""
    certification_id = _insert_certification()
    with (
        pytest.raises(IntegrityError, match="ck_certification_status_log_reason_required"),
        unit_of_work() as uow,
    ):
        uow.session.add(
            CertificationStatusLog(
                certification_id=certification_id,
                from_status="EXPIRING",
                to_status="EXPIRED",
                reason=None,
            )
        )
        uow.session.flush()


def test_log_rejects_self_transition() -> None:
    """제자리 전이 이력은 거부 — machine.py에 자기 쌍이 없다는 사실의 DB 층"""
    certification_id = _insert_certification()
    with (
        pytest.raises(IntegrityError, match="ck_certification_status_log_no_self_transition"),
        unit_of_work() as uow,
    ):
        uow.session.add(
            CertificationStatusLog(
                certification_id=certification_id,
                from_status="APPROVED",
                to_status="APPROVED",
            )
        )
        uow.session.flush()


# ── 활성 한정 유니크 거동 (판정 안건 ①) ─────────────────────────────────────


def test_open_unique_blocks_duplicate_active_instance() -> None:
    """같은 대상×템플릿의 활성 인스턴스 중복은 거부"""
    template_id = _insert_template()
    target_id = create_sku()
    _insert_certification(template_id=template_id, target_id=target_id)
    with pytest.raises(IntegrityError, match="uq_certifications_template_target_open"):
        _insert_certification(template_id=template_id, target_id=target_id)


def test_expired_instance_still_blocks_duplicate() -> None:
    """만료 인스턴스도 유니크 안 — 만료 후 재개는 신규가 아니라 기존 행 경유다"""
    template_id = _insert_template()
    target_id = create_sku()
    _insert_certification(template_id=template_id, target_id=target_id, status="EXPIRED")
    with pytest.raises(IntegrityError, match="uq_certifications_template_target_open"):
        _insert_certification(template_id=template_id, target_id=target_id)


def test_terminal_instance_frees_the_key() -> None:
    """종결(반려·중단) 행은 유니크 밖 — 재도전 신규 등록이 열린다"""
    template_id = _insert_template()
    target_id = create_sku()
    for terminal in ("REJECTED", "SUSPENDED"):
        _insert_certification(template_id=template_id, target_id=target_id, status=terminal)
    # 종결 2건이 쌓여 있어도 활성 신규 1건은 성립한다.
    row_id = _insert_certification(template_id=template_id, target_id=target_id)
    assert row_id > 0


def test_company_unique_blocks_duplicate_and_frees_on_terminal() -> None:
    """COMPANY 전용 유니크 — NULL target을 일반 유니크 대신 잡고, 종결이면 푼다"""
    template_id = _insert_template("EU")
    _insert_certification(template_id=template_id, target_type="COMPANY")
    with pytest.raises(IntegrityError, match="uq_certifications_company_open"):
        _insert_certification(template_id=template_id, target_type="COMPANY")
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(Certification).where(Certification.template_id == template_id)
        ).scalar_one()
        row.status = "SUSPENDED"
    row_id = _insert_certification(template_id=template_id, target_type="COMPANY")
    assert row_id > 0


def test_task_seq_unique_within_certification() -> None:
    """태스크 순번은 인스턴스 안에서 유일 (unique_active)"""
    certification_id = _insert_certification()
    with unit_of_work() as uow:
        uow.session.add(
            CertificationTask(
                certification_id=certification_id, seq=1, item_name="CFS", is_required=True
            )
        )
        uow.session.flush()
    with (
        pytest.raises(IntegrityError, match="uq_certification_tasks_certification_id_seq"),
        unit_of_work() as uow,
    ):
        uow.session.add(
            CertificationTask(
                certification_id=certification_id, seq=1, item_name="GMP", is_required=False
            )
        )
        uow.session.flush()


def test_soft_deleted_task_frees_seq() -> None:
    """soft delete된 태스크의 순번은 재사용 가능 — 재추가는 부활이 아니라 신규(§17.4)"""
    certification_id = _insert_certification()
    with unit_of_work() as uow:
        row = CertificationTask(
            certification_id=certification_id, seq=1, item_name="CFS", is_required=True
        )
        uow.session.add(row)
        uow.session.flush()
        row.deleted_at = utcnow()
    with unit_of_work() as uow:
        uow.session.add(
            CertificationTask(
                certification_id=certification_id, seq=1, item_name="CFS 재등록", is_required=True
            )
        )
        uow.session.flush()


# ── certification_status_log 불변 (판정 안건 ④ — audit_log 선례 실측 방식) ──


def _insert_log_row() -> int:
    certification_id = _insert_certification()
    with unit_of_work() as uow:
        row = CertificationStatusLog(
            certification_id=certification_id,
            from_status="NOT_STARTED",
            to_status="PREPARING",
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


@pytest.mark.group_j
def test_app_account_can_insert_status_log() -> None:
    """앱 계정은 이력을 남길 수 있다 (기록까지 막으면 이력 자동이 불가능)"""
    row_id = _insert_log_row()
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT to_status FROM certification_status_log WHERE id = :id"), {"id": row_id}
        ).scalar_one()
    assert found == "PREPARING"


@pytest.mark.group_j
def test_app_account_cannot_update_status_log() -> None:
    """앱 계정의 이력 UPDATE는 DB가 거부한다 (42501 — §17.5 확장·판정 ④)"""
    row_id = _insert_log_row()
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(
            text("UPDATE certification_status_log SET to_status = 'APPROVED' WHERE id = :id"),
            {"id": row_id},
        )
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED


@pytest.mark.group_j
def test_app_account_cannot_delete_status_log() -> None:
    """앱 계정의 이력 DELETE는 DB가 거부한다 (42501)"""
    row_id = _insert_log_row()
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(
            text("DELETE FROM certification_status_log WHERE id = :id"), {"id": row_id}
        )
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED


@pytest.mark.group_j
def test_app_account_cannot_truncate_status_log() -> None:
    """앱 계정의 이력 TRUNCATE도 거부한다 (DELETE만 막으면 우회로가 남는다)"""
    _insert_log_row()
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(text("TRUNCATE certification_status_log"))
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED
