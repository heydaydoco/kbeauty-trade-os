"""K. 상태이력·이벤트 계약 — 불변 표 구조·payload 화이트리스트 (S3-1 ADR-0051 / design-B B6).

이력 표는 PkMixin+Base만(Version·Actor·SoftDelete 없음)이고 IMMUTABLE이다. 이벤트 payload는 화이트리스트 키뿐이며
금액·단가·원가·마진·여신·사유 원문을 싣지 않는다(외부 채널로 나갈 수 있다).
"""

from __future__ import annotations

import pytest

import app.registry  # noqa: F401 — 모든 모델 등록
from app.core.db.base import Base
from app.core.db.table_policy import IMMUTABLE_TABLES
from app.core.logging.redaction import is_sensitive_key
from app.core.money import is_money_column_name
from app.modules.trade_docs.constants import STATUS_LOG_FK, STATUS_LOG_TABLES, DocKind
from app.modules.trade_docs.models import STATUS_LOG_MODELS
from app.modules.trade_docs.transition import PAYLOAD_KEYS

pytestmark = pytest.mark.group_k

COMMON_COLUMNS = {
    "id",
    "occurred_at",
    "from_status",
    "to_status",
    "reason",
    "actor_user_id",
    "automatic",
}
CHECKS = {
    "from_status_valid", "to_status_valid", "no_self_transition", "birth_row", "reason_required",
    "reason_not_blank", "actor_or_automatic",
}  # fmt: skip


def _existing_kinds() -> list[DocKind]:
    return [kind for kind, table in STATUS_LOG_TABLES.items() if table in Base.metadata.tables]


def test_the_scan_is_not_vacuous() -> None:
    """검사할 이력 표가 실제로 있다(견적)"""
    assert DocKind.QUOTATION in _existing_kinds()


def test_every_existing_status_log_table_has_a_registered_model_and_the_expected_shape() -> None:
    """이력 표 ↔ STATUS_LOG_MODELS 1:1, 열은 공통 7 + 문서 FK(SO의 approval_id는 M10[PR-12]이 더한다), 감사·버전·soft delete 열 없음"""
    kinds = _existing_kinds()
    assert set(STATUS_LOG_MODELS) == set(kinds)
    for kind in kinds:
        table = Base.metadata.tables[STATUS_LOG_TABLES[kind]]
        expected = COMMON_COLUMNS | {STATUS_LOG_FK[kind]}
        # SO의 `approval_id`(확정 행이 소비한 승인 참조)는 승인 코어 뒤 확정 배선 마이그레이션 M10(PR-12)이 ADD한다 — PR-7a에는 없다(X-49).
        assert set(table.c.keys()) == expected, kind
        assert not (
            {"version", "deleted_at", "created_by_id", "updated_by_id", "updated_at"}
            & set(table.c.keys())
        )
        assert STATUS_LOG_MODELS[kind].__tablename__ == table.name


def test_status_log_tables_are_immutable_and_carry_the_seven_checks() -> None:
    """이력 표는 IMMUTABLE_TABLES에 등재돼 있고 CHECK 7종(값·자기전이·탄생·사유·행위자)이 모델에 선언돼 있다"""
    for kind in _existing_kinds():
        table = Base.metadata.tables[STATUS_LOG_TABLES[kind]]
        assert table.name in IMMUTABLE_TABLES
        names = {
            str(c.name).removeprefix(f"ck_{table.name}_")
            for c in table.constraints
            if type(c).__name__ == "CheckConstraint"
        }
        assert names >= CHECKS, (kind, CHECKS - names)


def test_event_payload_keys_are_the_whitelist_and_carry_no_money_or_secrets() -> None:
    """payload 허용 키 = 8종 고정 — 금액·단가·원가·마진·여신·사유 계열 이름이 없다(마스킹·금액 판정 양쪽 통과)"""
    assert set(PAYLOAD_KEYS) == {
        "doc_type", "doc_id", "doc_number", "from_status", "to_status", "automatic", "assignee_id", "partner_id",
    }  # fmt: skip
    for key in PAYLOAD_KEYS:
        assert not is_money_column_name(key) and not is_sensitive_key(key), key
        assert not any(
            word in key
            for word in ("amount", "price", "cost", "margin", "credit", "reason", "note")
        )
