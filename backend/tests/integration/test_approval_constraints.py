"""A·K. 승인 4테이블의 불변식을 DB가 강제한다 — CHECK·부분 유니크·IMMUTABLE·컬럼 단위 UPDATE 권한 (S3-1 M07 / ADR-0060·0061 / design-C C1·C5·C6).

서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK·GRANT를 못 본다 — 함정 ①). 모든 위반 케이스는 같은 조건의 **양성 대조 INSERT가 성공**함을
함께 확인한다(TRUNCATE 하네스 공회전 방지). 핵심은 승인 통제의 DB 마지막 방어선이다 — SoD CHECK(기안자≠결정자·위임자≠기안자)·상태 쌍·1회 소비 쌍·활성 대상 1건·
스냅샷 컬럼 UPDATE 권한 부재·승인 이력 IMMUTABLE.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.core.db.base import Base
from app.core.db.session import build_engine, engine, owner_engine
from app.core.db.table_policy import (
    COLUMN_UPDATE_ALLOWLIST,
    IMMUTABLE_TABLES,
    MUTABLE_TABLES,
)
from app.modules.approvals.machine import (
    ALLOWED,
    APPROVAL_TYPES,
    APPROVER_ROLES,
    TARGET_TYPES,
    ApprovalStatus,
    VoidReasonCode,
)
from app.modules.identity.models import RoleCode
from tests.factories.approvals import add_line, make_user
from tests.factories.trade import unique

pytestmark = pytest.mark.group_a

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
PERMISSION_DENIED = "42501"
APPROVAL_TABLES = ("approval_lines", "delegations", "approvals", "approval_events")
DIGEST = "a" * 64


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


@pytest.fixture
def ctx() -> dict[str, Any]:
    """유효한 승인 한 줄의 재료 — 사용자 3명(기안자·결정자·위임자 후보)과 결재선 1행."""
    requester = make_user(RoleCode.TRADE)
    decider = make_user(RoleCode.TRADE)
    third = make_user(RoleCode.TRADE)
    return {
        "requester": requester.id,
        "decider": decider.id,
        "third": third.id,
        "line": add_line(0),
    }


def _insert_approval(connection: Connection, ctx: dict[str, Any], **overrides: Any) -> int:
    values: dict[str, Any] = {
        "approval_type": "SO_CREDIT_EXCEEDED",
        "target_type": "SALES_ORDER",
        "target_id": 1,
        "target_label": "SO-2026-0001",
        "status": "REQUESTED",
        "requested_by_id": ctx["requester"],
        "basis_amount": 1000,
        "basis_currency": "USD",
        "snapshot_digest": DIGEST,
        "snapshot": "{}",
        "required_role": "TRADE",
        "approval_line_id": ctx["line"],
        "decided_by_id": None,
        "decided_on_behalf_of_id": None,
        "decided_delegation_id": None,
        "decided_at": None,
        "consumed_at": None,
        "consumed_by_id": None,
    }
    values.update(overrides)
    names = list(values)
    return int(
        connection.execute(
            text(
                f"INSERT INTO approvals ({', '.join(names)}) VALUES"
                f" ({', '.join('CAST(:snapshot AS jsonb)' if n == 'snapshot' else ':' + n for n in names)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


def _insert_event(connection: Connection, approval_id: int, actor: int, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "approval_id": approval_id,
        "from_status": None,
        "to_status": "REQUESTED",
        "actor_user_id": actor,
        "on_behalf_of_user_id": None,
        "delegation_id": None,
        "reason": None,
        "reason_code": None,
    }
    values.update(overrides)
    names = list(values)
    connection.execute(
        text(
            f"INSERT INTO approval_events ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"
        ),
        values,
    )


def _violates(ctx: dict[str, Any], constraint: str, **overrides: Any) -> None:
    """승인 INSERT가 지정 CHECK 제약으로 거부된다 — 위반은 savepoint 안에서 시험해 트랜잭션을 살려 둔다."""
    with owner_engine.connect() as connection:
        try:
            _insert_approval(connection, ctx, **overrides)
        except IntegrityError as exc:
            assert _state(exc) == (CHECK_VIOLATION, constraint), _state(exc)
        else:
            raise AssertionError(f"{constraint}: 위반 INSERT가 통과했다")
        connection.rollback()


def test_a_valid_row_is_accepted_so_the_violations_below_are_meaningful(
    ctx: dict[str, Any],
) -> None:
    """양성 대조 — 기본 값 한 줄은 들어간다(위반 케이스가 다른 이유로 실패하는 일을 막는다)"""
    with owner_engine.begin() as connection:
        approval_id = _insert_approval(connection, ctx)
        _insert_event(connection, approval_id, ctx["requester"])


_APPROVAL_CHECK_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("type_unknown", {"approval_type": "NEW_PARTNER"}, "ck_approvals_approval_type_valid"),
    ("target_unknown", {"target_type": "PURCHASE_ORDER"}, "ck_approvals_target_type_valid"),
    # 알 수 없는 상태는 열거 CHECK와 결정 쌍 CHECK가 **함께** 위반이라 PG가 이름순으로 먼저 보고하는 쪽으로 잡힌다(상태 열거 정의 자체는 아래 대사 테스트가 고정).
    ("status_unknown", {"status": "PENDING"}, "ck_approvals_decided_state"),
    ("role_viewer", {"required_role": "VIEWER"}, "ck_approvals_required_role_valid"),
    ("basis_zero", {"basis_amount": 0}, "ck_approvals_basis_amount_positive"),
    ("basis_negative", {"basis_amount": -5}, "ck_approvals_basis_amount_positive"),
    ("basis_over_2_53", {"basis_amount": 2**53}, "ck_approvals_basis_amount_positive"),
    ("currency_lower", {"basis_currency": "usd"}, "ck_approvals_basis_currency_upper"),
    ("digest_short", {"snapshot_digest": "abc"}, "ck_approvals_snapshot_digest_format"),
    ("digest_upper", {"snapshot_digest": "A" * 64}, "ck_approvals_snapshot_digest_format"),
    ("snapshot_array", {"snapshot": "[]"}, "ck_approvals_snapshot_is_object"),
    ("label_blank", {"target_label": "  "}, "ck_approvals_target_label_not_blank"),
]


@pytest.mark.parametrize(
    ("name", "overrides", "constraint"),
    _APPROVAL_CHECK_CASES,
    ids=[c[0] for c in _APPROVAL_CHECK_CASES],
)
def test_value_checks_reject_bad_values(
    ctx: dict[str, Any], name: str, overrides: dict[str, Any], constraint: str
) -> None:
    """값 형식 CHECK — 유형·대상·상태·역할 열거, 금액>0(상한 2^53−1), 통화 대문자, digest 64자 소문자 hex, snapshot은 객체"""
    _violates(ctx, constraint, **overrides)


def test_state_pairing_checks(ctx: dict[str, Any]) -> None:
    """상태 쌍 CHECK — 요청 중이면 결정 시각 없음 / 승인·반려·소비는 결정 시각 필수 / 결정자·결정시각 한 쌍 / 소비 쌍"""
    decider, requester = ctx["decider"], ctx["requester"]
    now = "2026-10-01T00:00:00+00:00"
    _violates(
        ctx, "ck_approvals_decided_state", status="REQUESTED", decided_by_id=decider, decided_at=now
    )
    _violates(ctx, "ck_approvals_decided_state", status="APPROVED")  # 결정 시각 없는 승인
    _violates(
        ctx, "ck_approvals_decided_pair", status="WITHDRAWN", decided_by_id=decider
    )  # 결정자만 있고 시각 없음
    _violates(
        ctx, "ck_approvals_consumed_pair", status="CONSUMED", decided_by_id=decider, decided_at=now
    )
    _violates(
        ctx,
        "ck_approvals_consumed_pair",
        status="APPROVED",
        decided_by_id=decider,
        decided_at=now,
        consumed_at=now,
        consumed_by_id=decider,
    )
    _violates(
        ctx,
        "ck_approvals_consumed_by_pair",
        status="CONSUMED",
        decided_by_id=decider,
        decided_at=now,
        consumed_at=now,
    )
    # 양성 대조 — 정상 승인·소비 행은 들어간다
    with owner_engine.begin() as connection:
        _insert_approval(connection, ctx, status="APPROVED", decided_by_id=decider, decided_at=now)
        _insert_approval(
            connection,
            ctx,
            target_id=2,
            status="CONSUMED",
            decided_by_id=decider,
            decided_at=now,
            consumed_at=now,
            consumed_by_id=decider,
        )
        # 승인 후 회수·무효는 결정 기록을 그대로 남긴다(원 결정 컬럼 보존)
        _insert_approval(
            connection, ctx, target_id=3, status="WITHDRAWN", decided_by_id=decider, decided_at=now
        )
        _insert_approval(connection, ctx, target_id=4, status="VOIDED")
    assert requester != decider


def test_separation_of_duties_is_enforced_by_the_database(ctx: dict[str, Any]) -> None:
    """SoD의 마지막 방어선 — 결정자=기안자(ADMIN 포함 누구든)·위임자=기안자는 서비스를 우회해도 DB가 거부한다"""
    requester, decider, third = ctx["requester"], ctx["decider"], ctx["third"]
    now = "2026-10-01T00:00:00+00:00"
    _violates(ctx, "ck_approvals_sod", status="APPROVED", decided_by_id=requester, decided_at=now)
    _violates(ctx, "ck_approvals_sod", status="REJECTED", decided_by_id=requester, decided_at=now)
    with owner_engine.begin() as connection:
        delegation = int(
            connection.execute(
                text(
                    "INSERT INTO delegations (delegator_user_id, delegate_user_id, approval_type, delegated_role,"
                    " start_on, end_on) VALUES (:a, :b, 'SO_CREDIT_EXCEEDED', 'TRADE', '2026-10-01', '2026-10-05')"
                    " RETURNING id"
                ),
                {"a": requester, "b": decider},
            ).scalar_one()
        )
    # 위임자=기안자인 대결로 결정(수임자가 대신 승인) 금지
    _violates(
        ctx,
        "ck_approvals_on_behalf_not_requester",
        status="APPROVED",
        decided_by_id=decider,
        decided_on_behalf_of_id=requester,
        decided_delegation_id=delegation,
        decided_at=now,
    )
    # 쌍·결정자 필수
    _violates(
        ctx,
        "ck_approvals_on_behalf_pair",
        status="APPROVED",
        decided_by_id=decider,
        decided_on_behalf_of_id=third,
        decided_at=now,
    )
    _violates(
        ctx,
        "ck_approvals_on_behalf_pair",
        status="APPROVED",
        decided_by_id=decider,
        decided_delegation_id=delegation,
        decided_at=now,
    )
    # 양성 대조 — 다른 사람의 승인은 들어간다
    with owner_engine.begin() as connection:
        _insert_approval(connection, ctx, status="APPROVED", decided_by_id=decider, decided_at=now)


def test_only_one_active_approval_per_target_but_terminal_ones_do_not_block(
    ctx: dict[str, Any],
) -> None:
    """대상당 활성(REQUESTED·APPROVED) 승인은 1건 — 종결 4태(반려·회수·소비·무효) 뒤 재기안은 신규 행으로 성공한다"""
    now = "2026-10-01T00:00:00+00:00"
    decided = {"decided_by_id": ctx["decider"], "decided_at": now}
    with owner_engine.begin() as connection:
        _insert_approval(connection, ctx, status="REQUESTED")
    for status in ("REQUESTED", "APPROVED"):
        with owner_engine.connect() as connection:
            with pytest.raises(IntegrityError) as caught:
                extra = decided if status == "APPROVED" else {}
                _insert_approval(connection, ctx, status=status, **extra)
            assert _state(caught.value)[0] == UNIQUE_VIOLATION
            assert _state(caught.value)[1] == "uq_approvals_active_target"
            connection.rollback()
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM approvals"))  # 소유자 권한 — 테스트 정리
        for status, extra in (
            ("REJECTED", decided),
            ("WITHDRAWN", {}),
            ("VOIDED", {}),
            ("CONSUMED", {**decided, "consumed_at": now, "consumed_by_id": ctx["decider"]}),
        ):
            _insert_approval(connection, ctx, status=status, **extra)  # 같은 대상에 종결 4건 병존
        _insert_approval(connection, ctx, status="REQUESTED")  # 종결 뒤 재기안


def test_the_active_unique_index_definition_is_pinned() -> None:
    """활성 유니크의 술어는 REQUESTED·APPROVED뿐(종결 집합 밖) — 정의문으로 고정한다"""
    with owner_engine.connect() as connection:
        definition = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_approvals_active_target'")
        ).scalar_one()
    assert "UNIQUE" in definition
    assert "(approval_type, target_type, target_id)" in definition
    assert "REQUESTED" in definition and "APPROVED" in definition
    for terminal in ("REJECTED", "WITHDRAWN", "CONSUMED", "VOIDED"):
        assert terminal not in definition


def test_event_table_enforces_the_transition_table_and_reason_rules(ctx: dict[str, Any]) -> None:
    """이력 CHECK — 허용 7쌍+탄생만(상태 기계의 DB 사본)·반려·회수는 사유 필수·무효는 사유 코드 필수·행위자 NOT NULL"""
    with owner_engine.begin() as connection:
        approval_id = _insert_approval(connection, ctx)
    actor = ctx["decider"]

    def violates(constraint: str, **values: Any) -> None:
        with owner_engine.connect() as connection:
            with pytest.raises(IntegrityError) as caught:
                _insert_event(connection, approval_id, actor, **values)
            state = _state(caught.value)
            assert state[0] in (CHECK_VIOLATION, "23502"), state
            if state[0] == CHECK_VIOLATION:
                assert state[1] == constraint, state
            connection.rollback()

    # 허용 쌍 전수 — 허용 7쌍은 들어가고 나머지 순서쌍은 전부 거부된다(상태 기계 이중 정의가 어긋나지 않는다)
    statuses = [s.value for s in ApprovalStatus]
    for a in statuses:
        for b in statuses:
            reason = {"reason": "사유"} if b in ("REJECTED", "WITHDRAWN") else {}
            code = {"reason_code": "TARGET_CHANGED"} if b == "VOIDED" else {}
            if (ApprovalStatus(a), ApprovalStatus(b)) in ALLOWED:
                with owner_engine.begin() as connection:
                    _insert_event(
                        connection, approval_id, actor, from_status=a, to_status=b, **reason, **code
                    )
            else:
                violates(
                    "ck_approval_events_pair_allowed", from_status=a, to_status=b, **reason, **code
                )
    violates(
        "ck_approval_events_pair_allowed", from_status=None, to_status="APPROVED"
    )  # 탄생은 REQUESTED만
    violates(
        "ck_approval_events_reason_required",
        from_status="REQUESTED",
        to_status="REJECTED",
        reason="  ",
    )
    violates("ck_approval_events_reason_required", from_status="REQUESTED", to_status="WITHDRAWN")
    violates("ck_approval_events_void_code", from_status="REQUESTED", to_status="VOIDED")
    violates(
        "ck_approval_events_void_code",
        from_status="REQUESTED",
        to_status="APPROVED",
        reason_code="TARGET_CHANGED",
    )
    violates(
        "ck_approval_events_reason_code_valid",
        from_status="REQUESTED",
        to_status="VOIDED",
        reason_code="WHATEVER",
    )
    violates(
        "ck_approval_events_reason_len",
        from_status="REQUESTED",
        to_status="REJECTED",
        reason="x" * 1001,
    )
    violates(
        "ck_approval_events_actor_not_on_behalf",
        from_status="REQUESTED",
        to_status="APPROVED",
        on_behalf_of_user_id=actor,
        delegation_id=None,
    )
    violates("", from_status="REQUESTED", to_status="APPROVED", actor_user_id=None)


def test_line_and_delegation_checks(ctx: dict[str, Any]) -> None:
    """결재선·대결 CHECK — 임계 범위·통화 대문자·역할 4값·본인 위임 금지·기간 순서·종료 쌍·유형 열거"""

    def line_violates(constraint: str, **overrides: Any) -> None:
        values: dict[str, Any] = {
            "approval_type": "SO_CREDIT_EXCEEDED",
            "threshold_amount": 7,
            "threshold_currency": "EUR",
            "approver_role": "TRADE",
            "note": None,
        }
        values.update(overrides)
        with owner_engine.connect() as connection:
            with pytest.raises(IntegrityError) as caught:
                connection.execute(
                    text(
                        "INSERT INTO approval_lines (approval_type, threshold_amount, threshold_currency,"
                        " approver_role, note) VALUES (:approval_type, :threshold_amount, :threshold_currency,"
                        " :approver_role, :note)"
                    ),
                    values,
                )
            assert _state(caught.value) == (CHECK_VIOLATION, constraint)
            connection.rollback()

    line_violates("ck_approval_lines_threshold_amount_range", threshold_amount=-1)
    line_violates("ck_approval_lines_threshold_amount_range", threshold_amount=2**53)
    line_violates("ck_approval_lines_threshold_currency_upper", threshold_currency="eur")
    line_violates("ck_approval_lines_approver_role_valid", approver_role="VIEWER")
    line_violates("ck_approval_lines_approval_type_valid", approval_type="EXPENSE_OVER_THRESHOLD")
    line_violates("ck_approval_lines_note_not_blank", note="  ")

    def delegation_violates(constraint: str, **overrides: Any) -> None:
        values: dict[str, Any] = {
            "a": ctx["requester"],
            "b": ctx["decider"],
            "t": "SO_CREDIT_EXCEEDED",
            "r": "TRADE",
            "s": "2026-10-01",
            "e": "2026-10-05",
            "rv": None,
            "rb": None,
        }
        values.update(overrides)
        with owner_engine.connect() as connection:
            with pytest.raises(IntegrityError) as caught:
                connection.execute(
                    text(
                        "INSERT INTO delegations (delegator_user_id, delegate_user_id, approval_type, delegated_role,"
                        " start_on, end_on, revoked_at, revoked_by_id) VALUES (:a, :b, :t, :r, :s, :e, :rv, :rb)"
                    ),
                    values,
                )
            assert _state(caught.value) == (CHECK_VIOLATION, constraint)
            connection.rollback()

    delegation_violates("ck_delegations_parties_differ", b=ctx["requester"])
    delegation_violates("ck_delegations_period_order", s="2026-10-06")
    delegation_violates("ck_delegations_revoked_pair", rv="2026-10-02T00:00:00+00:00")
    delegation_violates("ck_delegations_delegated_role_valid", r="VIEWER")
    delegation_violates("ck_delegations_approval_type_valid", t="X")
    with owner_engine.begin() as connection:  # 양성 대조 — 시작일=종료일(하루짜리) 대결은 유효하다
        connection.execute(
            text(
                "INSERT INTO delegations (delegator_user_id, delegate_user_id, approval_type, delegated_role,"
                " start_on, end_on) VALUES (:a, :b, 'SO_CREDIT_EXCEEDED', 'TRADE', '2026-10-01', '2026-10-01')"
            ),
            {"a": ctx["requester"], "b": ctx["decider"]},
        )


def test_line_threshold_is_unique_among_active_rows_only(ctx: dict[str, Any]) -> None:
    """같은 (유형·통화·임계)는 활성 행끼리 유일하다 — 삭제된 행은 제외, 통화·유형이 다르면 병존"""
    add_line(500, currency="USD")
    add_line(500, currency="KRW")  # 통화가 다르면 병존
    with owner_engine.connect() as connection:
        with pytest.raises(IntegrityError) as caught:
            connection.execute(
                text(
                    "INSERT INTO approval_lines (approval_type, threshold_amount, threshold_currency, approver_role)"
                    " VALUES ('SO_CREDIT_EXCEEDED', 500, 'USD', 'ADMIN')"
                )
            )
        assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_approval_lines_threshold_active")
        connection.rollback()
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE approval_lines SET deleted_at = now() WHERE threshold_amount = 500 AND threshold_currency = 'USD'"
            )
        )
    add_line(500, currency="USD")  # 삭제 뒤 같은 임계를 신규로 등록할 수 있다


def test_a_delegation_start_day_is_unique_per_delegator_type_role_while_active(
    ctx: dict[str, Any],
) -> None:
    """같은 위임자·유형·역할·시작일의 미종료 대결은 하나뿐(더블클릭 DB 방어망) — 종료된 행은 제외"""
    sql = (
        "INSERT INTO delegations (delegator_user_id, delegate_user_id, approval_type, delegated_role, start_on, end_on)"
        " VALUES (:a, :b, 'SO_CREDIT_EXCEEDED', 'TRADE', '2026-10-01', '2026-10-05')"
    )
    params = {"a": ctx["requester"], "b": ctx["decider"]}
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)
    with owner_engine.connect() as connection:
        with pytest.raises(IntegrityError) as caught:
            connection.execute(text(sql), {"a": ctx["requester"], "b": ctx["third"]})
        assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_delegations_start_active")
        connection.rollback()
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE delegations SET revoked_at = now(), revoked_by_id = :a"),
            {"a": ctx["requester"]},
        )
        connection.execute(text(sql), {"a": ctx["requester"], "b": ctx["third"]})


# ── 권한 — IMMUTABLE 이력·컬럼 단위 UPDATE ─────────────────────────────────────


def _app_denied(sql: str, **params: Any) -> None:
    """kbos_app 계정으로 실행하면 42501(권한 없음)이다."""
    with engine.connect() as connection:
        try:
            connection.execute(text(sql), params)
        except (ProgrammingError, DBAPIError) as exc:
            assert _state(exc)[0] == PERMISSION_DENIED, _state(exc)
        else:
            raise AssertionError(f"권한 없이 통과했다: {sql}")
        finally:
            connection.rollback()


def test_the_event_table_is_immutable_for_the_app_account(ctx: dict[str, Any]) -> None:
    """approval_events: kbos_app는 UPDATE·DELETE·TRUNCATE가 불가(42501) — INSERT·SELECT만. 정정은 새 전이 기록이다"""
    with owner_engine.begin() as connection:
        approval_id = _insert_approval(connection, ctx)
        _insert_event(connection, approval_id, ctx["requester"])
    _app_denied("UPDATE approval_events SET reason = 'x'")
    _app_denied("DELETE FROM approval_events")
    _app_denied("TRUNCATE approval_events")
    with engine.connect() as connection:
        privileges = {
            p: connection.execute(
                text("SELECT has_table_privilege('kbos_app', 'approval_events', :p)"), {"p": p}
            ).scalar_one()
            for p in ("INSERT", "SELECT", "UPDATE", "DELETE", "TRUNCATE")
        }
    assert privileges == {
        "INSERT": True,
        "SELECT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
    }
    assert "approval_events" in IMMUTABLE_TABLES


@pytest.mark.parametrize("table", ["approvals", "approval_lines", "delegations"])
def test_delete_and_truncate_are_revoked_on_the_mutable_tables(table: str) -> None:
    """승인·결재선·대결은 행을 지우지 않는다 — kbos_app의 DELETE·TRUNCATE·테이블 단위 UPDATE는 회수돼 있다(정정은 새 행·soft delete)"""
    _app_denied(f"DELETE FROM {table}")
    _app_denied(f"TRUNCATE {table}")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT has_table_privilege('kbos_app', :t, 'UPDATE')"), {"t": table}
            ).scalar_one()
            is False
        )  # 테이블 단위 UPDATE가 남아 있으면 컬럼 제한이 조용히 무효다


def test_column_update_privileges_match_the_allowlist_exactly() -> None:
    """has_column_privilege 전수 — 3테이블의 모든 컬럼에서 kbos_app의 UPDATE 권한 ⇔ COLUMN_UPDATE_ALLOWLIST 등재. 스냅샷 컬럼은 전부 불변이다"""
    assert set(COLUMN_UPDATE_ALLOWLIST) == {"approvals", "approval_lines", "delegations"}
    with engine.connect() as connection:
        for table, allowed in COLUMN_UPDATE_ALLOWLIST.items():
            columns = [c.name for c in Base.metadata.tables[table].columns]
            assert allowed <= set(columns), (
                f"{table}: 모델에 없는 허용 컬럼 {allowed - set(columns)}"
            )
            for column in columns:
                granted = connection.execute(
                    text("SELECT has_column_privilege('kbos_app', :t, :c, 'UPDATE')"),
                    {"t": table, "c": column},
                ).scalar_one()
                assert granted == (column in allowed), (
                    f"{table}.{column}: 권한 {granted} ≠ 등재 {column in allowed}"
                )
    approvals_cols = {c.name for c in Base.metadata.tables["approvals"].columns}
    frozen = approvals_cols - COLUMN_UPDATE_ALLOWLIST["approvals"]
    assert {
        "approval_type",
        "target_type",
        "target_id",
        "target_label",
        "requested_by_id",
        "basis_amount",
        "basis_currency",
        "snapshot_digest",
        "snapshot",
        "required_role",
        "approval_line_id",
    } <= frozen


def test_snapshot_columns_cannot_be_updated_by_the_app_account(ctx: dict[str, Any]) -> None:
    """raw UPDATE로 승인 금액·digest·역할·유형·대상·요청자를 바꾸면 42501 — 허용 컬럼(status 등)은 성공한다(승인 후 불변의 DB 층)"""
    with owner_engine.begin() as connection:
        approval_id = _insert_approval(connection, ctx)
    for column, value in (
        ("basis_amount", 1),
        ("basis_currency", "'KRW'"),
        ("snapshot_digest", f"'{'b' * 64}'"),
        ("snapshot", "'{}'::jsonb"),
        ("required_role", "'ADMIN'"),
        ("approval_type", "'SO_CREDIT_EXCEEDED'"),
        ("target_id", 99),
        ("target_label", "'x'"),
        ("requested_by_id", ctx["decider"]),
        ("approval_line_id", ctx["line"]),
    ):
        _app_denied(f"UPDATE approvals SET {column} = {value} WHERE id = :i", i=approval_id)
    with engine.begin() as connection:  # 양성 대조 — 허용 컬럼은 갱신된다
        connection.execute(
            text(
                "UPDATE approvals SET status = 'WITHDRAWN', updated_by_id = :u, version = version + 1 WHERE id = :i"
            ),
            {"u": ctx["requester"], "i": approval_id},
        )
    with owner_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM approvals WHERE id = :i"), {"i": approval_id}
            ).scalar_one()
            == "WITHDRAWN"
        )


def test_delegation_period_and_parties_are_immutable_but_revocation_is_allowed(
    ctx: dict[str, Any],
) -> None:
    """대결의 기간·당사자·범위는 raw UPDATE로 못 바꾼다(이력 위변조 차단) — 종료 컬럼은 갱신된다. 결재선도 임계는 불변·역할은 수정 가능"""
    with owner_engine.begin() as connection:
        delegation_id = int(
            connection.execute(
                text(
                    "INSERT INTO delegations (delegator_user_id, delegate_user_id, approval_type, delegated_role,"
                    " start_on, end_on) VALUES (:a, :b, 'SO_CREDIT_EXCEEDED', 'TRADE', '2026-10-01', '2026-10-05')"
                    " RETURNING id"
                ),
                {"a": ctx["requester"], "b": ctx["decider"]},
            ).scalar_one()
        )
    for assignment in (
        "end_on = '2027-01-01'",
        "start_on = '2026-09-01'",
        "delegate_user_id = " + str(ctx["third"]),
        "delegated_role = 'ADMIN'",
    ):
        _app_denied(f"UPDATE delegations SET {assignment} WHERE id = :i", i=delegation_id)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE delegations SET revoked_at = now(), revoked_by_id = :u WHERE id = :i"),
            {"u": ctx["requester"], "i": delegation_id},
        )
    _app_denied("UPDATE approval_lines SET threshold_amount = 5 WHERE id = :i", i=ctx["line"])
    _app_denied("UPDATE approval_lines SET threshold_currency = 'KRW' WHERE id = :i", i=ctx["line"])
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE approval_lines SET approver_role = 'ADMIN', note = 'ok' WHERE id = :i"),
            {"i": ctx["line"]},
        )


def test_references_are_restricted_so_used_rows_cannot_disappear(ctx: dict[str, Any]) -> None:
    """FK RESTRICT — 승인이 참조하는 결재선·이력이 참조하는 승인은 (소유자 권한으로도) 삭제할 수 없다"""
    with owner_engine.begin() as connection:
        approval_id = _insert_approval(connection, ctx)
        _insert_event(connection, approval_id, ctx["requester"])
    for sql, params in (
        ("DELETE FROM approval_lines WHERE id = :i", {"i": ctx["line"]}),
        ("DELETE FROM approvals WHERE id = :i", {"i": approval_id}),
    ):
        with owner_engine.connect() as connection:
            with pytest.raises(IntegrityError) as caught:
                connection.execute(text(sql), params)
            assert _state(caught.value)[0] == "23503"
            connection.rollback()


# ── 분류·열거 대사 ───────────────────────────────────────────────────────────────


def test_table_policy_classification_and_the_allowlist_subset() -> None:
    """분류: approval_events=IMMUTABLE, 나머지 3표=MUTABLE(+컬럼 허용 목록) — 허용 목록은 MUTABLE의 부분집합이고 IMMUTABLE과 겹치지 않는다"""
    assert "approval_events" in IMMUTABLE_TABLES
    assert {"approvals", "approval_lines", "delegations"} <= MUTABLE_TABLES
    assert set(COLUMN_UPDATE_ALLOWLIST) <= MUTABLE_TABLES
    assert not set(COLUMN_UPDATE_ALLOWLIST) & IMMUTABLE_TABLES


def _check_values(constraint_suffix: str, table: str) -> set[str]:
    with owner_engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid"
                " WHERE t.relname = :t AND c.conname = :n"
            ),
            {"t": table, "n": f"ck_{table}_{constraint_suffix}"},
        ).scalar_one()
    return set(re.findall(r"'([A-Z_]+)'", definition))


@pytest.mark.parametrize(
    ("table", "suffix", "expected"),
    [
        ("approvals", "approval_type_valid", set(APPROVAL_TYPES)),
        ("approval_lines", "approval_type_valid", set(APPROVAL_TYPES)),
        ("delegations", "approval_type_valid", set(APPROVAL_TYPES)),
        ("approvals", "target_type_valid", set(TARGET_TYPES)),
        ("approvals", "status_valid", {s.value for s in ApprovalStatus}),
        ("approval_events", "from_status_valid", {s.value for s in ApprovalStatus}),
        ("approval_events", "to_status_valid", {s.value for s in ApprovalStatus}),
        ("approvals", "required_role_valid", set(APPROVER_ROLES)),
        ("approval_lines", "approver_role_valid", set(APPROVER_ROLES)),
        ("delegations", "delegated_role_valid", set(APPROVER_ROLES)),
        ("approval_events", "reason_code_valid", {c.value for c in VoidReasonCode}),
    ],
)
def test_check_constraints_match_the_code_enumerations(
    table: str, suffix: str, expected: set[str]
) -> None:
    """DB CHECK의 열거값 == 코드 열거(StrEnum·상수) 1:1 — 값을 한쪽에만 추가하면 실패한다"""
    assert _check_values(suffix, table) == expected


def test_every_table_with_an_approval_type_column_is_covered_by_the_enumeration_check() -> None:
    """approval_type 컬럼을 가진 전 테이블이 위 대사에 들어 있다 — 새 테이블에 이 컬럼이 생기면 CHECK 대사를 더해야 한다"""
    with owner_engine.connect() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public'"
                    " AND column_name = 'approval_type'"
                )
            ).scalars()
        )
    assert tables == {"approvals", "approval_lines", "delegations"}


def test_the_event_pair_check_is_generated_from_the_machine_not_hand_written() -> None:
    """이력 pair_allowed CHECK에 실린 쌍 집합이 machine.ALLOWED와 정확히 같다(허용 7쌍)"""
    with owner_engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c WHERE c.conname = 'ck_approval_events_pair_allowed'"
            )
        ).scalar_one()
    pairs = set(
        re.findall(
            r"from_status\)?::text = '([A-Z]+)'::text\) AND \(?\(?to_status\)?::text = '([A-Z]+)'::text",
            definition,
        )
    )
    assert pairs == {(a.value, b.value) for a, b in ALLOWED}
    assert len(ALLOWED) == 7


def test_migrations_seed_nothing_in_the_approval_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """마이그레이션 직후 승인 4표의 행 수는 0이다(실측) — 결재선은 ADMIN 화면/API로만 공급된다(함정 ⑩)"""
    from alembic.config import Config

    from app.core.config import settings
    from tests.conftest import ALEMBIC_INI

    assert settings.migration_check_database_url is not None
    url = settings.migration_check_database_url.get_secret_value()
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    config = Config(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    check_engine = build_engine(url)
    try:
        with check_engine.connect() as connection:
            for table in APPROVAL_TABLES:
                assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    finally:
        check_engine.dispose()
    assert unique("x")  # 가드: unique 팩토리 임포트가 살아 있다
