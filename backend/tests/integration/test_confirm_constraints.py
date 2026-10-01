"""A/K. M10 확정 증적 제약 — 게이트 통과 기록 없는 확정을 **DB가 거부**한다 (S3-1 PR-12a / design-E E4 · design-integrated X-11 / ADR-0070).

서비스를 우회한 원시 SQL(앱 계정)이 (1) `confirmed_at`만 채우거나 (2) 증적 값을 위조하거나 (3) 같은 승인을 두 SO에 다는 시도가 각 CHECK·FK·UNIQUE에서 거부된다(23514·23503·23505, 제약 이름 일치).
CHECK는 autogenerate가 못 보므로 정의문(`pg_get_constraintdef`)도 고정한다. 상태이력의 `approval_id`(RECEIVED→CONFIRMED 행에만·1승인=1이력행)와 IMMUTABLE도 함께 본다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db.session import engine, owner_engine
from tests.factories.approvals import approved_for, credit_so
from tests.factories.confirm import ready_so, so_row

pytestmark = pytest.mark.group_a

CHECK_VIOLATION = "23514"
FK_VIOLATION = "23503"
UNIQUE_VIOLATION = "23505"

CONFIRM = "status = 'CONFIRMED', confirmed_at = now()"
EVIDENCE = "credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE'"


def _fail(sql: str, **params: Any) -> tuple[str, str]:
    """앱 계정으로 실행해 거부되는 SQL의 (SQLSTATE, 제약 이름)."""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(text(sql), params)
    orig = caught.value.orig
    return str(getattr(orig, "sqlstate", "")), str(
        getattr(getattr(orig, "diag", None), "constraint_name", "")
    )


def _run(sql: str, **params: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


@pytest.mark.parametrize(
    ("name", "set_clause", "constraint"),
    [
        # 확정 시각만 채우면 — 게이트 통과 기록(두 판정 값)이 없어 거부
        ("confirmed_at_only", CONFIRM, "ck_sales_orders_credit_verdict_iff_confirmed"),
        (
            "credit_only",
            f"{CONFIRM}, credit_verdict = 'NOT_MANAGED'",
            "ck_sales_orders_pi_gate_verdict_iff_confirmed",
        ),
        (
            "pi_only",
            f"{CONFIRM}, pi_gate_verdict = 'NOT_APPLICABLE'",
            "ck_sales_orders_credit_verdict_iff_confirmed",
        ),
        # 접수 상태에 증적을 미리 달 수 없다(확정 시각 ⇔ 판정 값)
        (
            "evidence_without_confirmation",
            "credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE'",
            "ck_sales_orders_credit_verdict_iff_confirmed",
        ),
        # 저장 불가 값 — EXCEEDED·UNEVALUABLE·BLOCK은 확정에 도달할 수 없다
        (
            "credit_exceeded_is_not_storable",
            f"{CONFIRM}, credit_verdict = 'EXCEEDED', pi_gate_verdict = 'NOT_APPLICABLE'",
            "ck_sales_orders_credit_verdict_valid",
        ),
        (
            "pi_block_is_not_storable",
            f"{CONFIRM}, credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'BLOCK'",
            "ck_sales_orders_pi_gate_verdict_valid",
        ),
        # 승인 판정 ⇔ 승인 id
        (
            "approved_without_approval_id",
            f"{CONFIRM}, credit_verdict = 'APPROVED', pi_gate_verdict = 'NOT_APPLICABLE'",
            "ck_sales_orders_credit_approval_iff_approved",
        ),
        (
            "approval_id_without_approved",
            f"{CONFIRM}, {EVIDENCE}, credit_approval_id = 999999",
            "ck_sales_orders_credit_approval_iff_approved",
        ),
    ],
)
def test_raw_sql_cannot_confirm_an_order_without_the_gate_evidence(
    name: str, set_clause: str, constraint: str
) -> None:
    """원시 UPDATE로 증적 없이·위조 값으로 확정을 만들려는 시도는 전부 CHECK에서 거부된다 — 같은 SO에 올바른 증적을 함께 쓰면 성공한다(양성 대조)"""
    so = ready_so()
    state, violated = _fail(f"UPDATE sales_orders SET {set_clause} WHERE id = :i", i=so["id"])
    assert (state, violated) == (CHECK_VIOLATION, constraint), name
    assert so_row(so["id"])["status"] == "RECEIVED"
    _run(f"UPDATE sales_orders SET {CONFIRM}, {EVIDENCE} WHERE id = :i", i=so["id"])  # 양성 대조
    assert so_row(so["id"])["credit_verdict"] == "NOT_MANAGED"


def test_an_approved_verdict_requires_a_real_approval_and_one_approval_serves_one_order() -> None:
    """APPROVED 판정은 실재하는 승인 id(FK)가 필요하고, **같은 승인을 두 SO에 달 수 없다**(부분 유니크 — 승인 코어의 CONSUMED 종결과 이중 방어)"""
    first = credit_so()
    approval_id, _, _ = approved_for(first)
    second = credit_so()
    third = ready_so()
    approved = f"{CONFIRM}, credit_verdict = 'APPROVED', pi_gate_verdict = 'NOT_APPLICABLE'"
    state, constraint = _fail(
        f"UPDATE sales_orders SET {approved}, credit_approval_id = 999999 WHERE id = :i",
        i=third["id"],
    )
    assert state == FK_VIOLATION and "credit_approval_id" in constraint
    # 같은 승인을 두 SO에 — 첫 번째는 통과(코드 경로 밖의 원시 위조지만 CHECK·FK는 만족), 두 번째는 유니크 위반
    for so in (first, second):  # credit_so는 동결 완결 값이 비어 있어 채운다
        _run(
            "UPDATE sales_orders SET incoterm_code = 'FOB', incoterm_place = 'Busan', incoterm_year = 2020,"
            " payment_type = 'LC', fx_rate = 1350, fx_rate_date = doc_date WHERE id = :i",
            i=so["id"],
        )
    _run(
        f"UPDATE sales_orders SET {approved}, credit_approval_id = :a WHERE id = :i",
        a=approval_id,
        i=first["id"],
    )
    state, constraint = _fail(
        f"UPDATE sales_orders SET {approved}, credit_approval_id = :a WHERE id = :i",
        a=approval_id,
        i=second["id"],
    )
    assert (state, constraint) == (UNIQUE_VIOLATION, "uq_sales_orders_credit_approval_id")


def test_the_status_log_accepts_an_approval_only_on_the_confirmation_row_and_only_once() -> None:
    """상태이력 `approval_id` — RECEIVED→CONFIRMED 행에만(그 밖의 전이는 CHECK 거부) · 같은 승인은 이력 한 행에만(유니크) · 없는 승인 id는 FK 거부 · 이력은 UPDATE·DELETE 불가"""
    first, second = credit_so(), credit_so()
    approval_id, _, _ = approved_for(first)
    reason_insert = (
        "INSERT INTO sales_order_status_log (sales_order_id, from_status, to_status, reason, actor_user_id,"
        " automatic, approval_id) VALUES (:s, :f, :t, '보류', NULL, true, :a)"
    )
    assert _fail(reason_insert, s=first["id"], f="RECEIVED", t="ON_HOLD", a=approval_id) == (
        CHECK_VIOLATION,
        "ck_sales_order_status_log_approval_only_on_confirm",
    )
    ok = "INSERT INTO sales_order_status_log (sales_order_id, from_status, to_status, actor_user_id, automatic, approval_id) VALUES (:s, 'RECEIVED', 'CONFIRMED', NULL, true, :a)"
    _run(ok, s=first["id"], a=approval_id)
    state, constraint = _fail(ok, s=second["id"], a=approval_id)
    assert (state, constraint) == (UNIQUE_VIOLATION, "uq_sales_order_status_log_approval_id")
    state, constraint = _fail(ok, s=second["id"], a=987654)
    assert state == FK_VIOLATION
    for statement in (
        "UPDATE sales_order_status_log SET approval_id = NULL WHERE sales_order_id = :s",
        "DELETE FROM sales_order_status_log WHERE sales_order_id = :s",
    ):
        with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
            connection.execute(text(statement), {"s": first["id"]})
        assert getattr(caught.value.orig, "sqlstate", None) == "42501"  # 불변 표


def test_the_new_constraints_have_their_designed_definitions() -> None:
    """CHECK는 autogenerate가 못 본다 — 정의문 고정: `IS NULL` 동치 비교 2·`IS TRUE` 승인 결속·값 집합 3(저장 가능 값만)·승인 유니크 부분 인덱스·노출 조회 인덱스"""
    with owner_engine.connect() as connection:
        defs = {
            r[0]: r[1]
            for r in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid IN ('sales_orders'::regclass, 'sales_order_status_log'::regclass)"
                )
            )
        }
        indexes = {
            r[0]: r[1]
            for r in connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('sales_orders', 'sales_order_status_log')"
                )
            )
        }
    assert "confirmed_at IS NULL" in defs["ck_sales_orders_credit_verdict_iff_confirmed"]
    assert "credit_verdict IS NULL" in defs["ck_sales_orders_credit_verdict_iff_confirmed"]
    assert "confirmed_at IS NULL" in defs["ck_sales_orders_pi_gate_verdict_iff_confirmed"]
    assert "IS TRUE" in defs["ck_sales_orders_credit_approval_iff_approved"]
    assert "credit_approval_id IS NOT NULL" in defs["ck_sales_orders_credit_approval_iff_approved"]
    for stored in ("WITHIN_LIMIT", "NOT_MANAGED", "APPROVED"):
        assert stored in defs["ck_sales_orders_credit_verdict_valid"]
    assert "EXCEEDED" not in defs["ck_sales_orders_credit_verdict_valid"]
    for stored in ("PASS", "NOT_APPLICABLE", "WARN", "OVERRIDDEN", "SKIPPED_OFF"):
        assert stored in defs["ck_sales_orders_pi_gate_verdict_valid"]
    assert "BLOCK" not in defs["ck_sales_orders_pi_gate_verdict_valid"]
    assert "RECEIVED" in defs["ck_sales_order_status_log_approval_only_on_confirm"]
    assert "UNIQUE" in indexes["uq_sales_orders_credit_approval_id"].upper()
    assert "WHERE (credit_approval_id IS NOT NULL)" in indexes["uq_sales_orders_credit_approval_id"]
    assert "WHERE (approval_id IS NOT NULL)" in indexes["uq_sales_order_status_log_approval_id"]
    assert "confirmed_at IS NOT NULL" in indexes["ix_sales_orders_open_exposure"]
    assert all(len(name) <= 63 for name in list(defs) + list(indexes))


def test_the_app_role_can_write_the_evidence_columns_only_through_the_normal_update_path() -> None:
    """증적 3열은 일반 UPDATE 대상(SYSTEM 열)이고 컬럼 권한 제한은 승인 코어 표 몫이다 — 확정 통로 밖의 쓰기는 CHECK와 서비스 계층 스캔(AST)이 막는다는 경계를 명시한다"""
    so = ready_so()
    _run(f"UPDATE sales_orders SET {CONFIRM}, {EVIDENCE} WHERE id = :i", i=so["id"])
    row = so_row(so["id"])
    assert (row["credit_verdict"], row["credit_approval_id"], row["pi_gate_verdict"]) == (
        "NOT_MANAGED",
        None,
        "NOT_APPLICABLE",
    )
