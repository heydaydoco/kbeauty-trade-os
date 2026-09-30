"""A·K. 견적 3테이블의 불변식을 DB가 강제한다 (S3-1 M03 / ADR-0051~0054 / design-A A2·B6).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다 — 모델이 선언한 CHECK·유니크·FK·권한이 DB에 **실재**하는지
  실측한다(alembic check는 CHECK를 못 본다 — 함정 ①). 모든 위반 케이스는 같은 조건의 **양성 대조 INSERT가 성공**함을
  함께 확인한다(TRUNCATE 하네스 공회전 방지).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.session import engine, owner_engine
from app.modules.identity.models import RoleCode
from tests.support.factories import create_market, create_partner, create_sku, create_user

pytestmark = pytest.mark.group_a

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"
PERMISSION_DENIED = "42501"


@pytest.fixture
def base() -> dict[str, Any]:
    """유효한 초안 QT 한 줄의 기본값(이 값으로 INSERT하면 통과한다)."""
    user_id = create_user("qt-constraint@example.com", roles=(RoleCode.TRADE,))
    partner_id = create_partner("BUY-C", types=("BUYER",))
    create_market("US")
    return {
        "doc_number": "QT-2026-0001",
        "doc_date": date(2026, 9, 1),
        "status": "DRAFT",
        "currency": "USD",
        "assignee_id": user_id,
        "buyer_partner_id": partner_id,
        "buyer_name": "Acme Trading",
        "dest_market_code": "US",
    }


_COLUMNS = (
    "doc_number, doc_date, status, currency, assignee_id, buyer_partner_id, buyer_name,"
    " dest_market_code, fx_rate, fx_rate_date, payment_type, advance_pct_bp, balance_anchor,"
    " balance_days, incoterm_code, incoterm_place, incoterm_year, buyer_address, valid_until,"
    " frozen_at, copied_from_id, total_amount, last_line_no"
)


def _insert(connection: Connection, **values: Any) -> int:
    row = {
        "fx_rate": None,
        "fx_rate_date": None,
        "payment_type": None,
        "advance_pct_bp": None,
        "balance_anchor": None,
        "balance_days": None,
        "incoterm_code": None,
        "incoterm_place": None,
        "incoterm_year": None,
        "buyer_address": None,
        "valid_until": None,
        "frozen_at": None,
        "copied_from_id": None,
        "total_amount": 0,
        "last_line_no": 0,
        **values,
    }
    placeholders = ", ".join(f":{name}" for name in (c.strip() for c in _COLUMNS.split(",")))
    return int(
        connection.execute(
            text(f"INSERT INTO quotations ({_COLUMNS}) VALUES ({placeholders}) RETURNING id"), row
        ).scalar_one()
    )


def _sqlstate_and_constraint(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


#: (이름, 덮어쓸 값, 위반되는 제약 이름) — 전부 23514.
_FROZEN_OK: dict[str, Any] = {
    "status": "ISSUED",
    "frozen_at": "2026-09-02T00:00:00+00:00",
    "payment_type": "TT_ADVANCE",
    "advance_pct_bp": 3000,
    "balance_anchor": "ETD_DATE",
    "balance_days": -7,
    "incoterm_code": "FOB",
    "incoterm_place": "Busan",
    "incoterm_year": 2020,
    "fx_rate": 1350,
    "fx_rate_date": date(2026, 9, 1),
    "buyer_address": "1 Main St",
    "valid_until": date(2026, 10, 1),
}

_CHECK_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "status_unknown",
        {"status": "PAID"},
        "ck_quotations_status_valid|ck_quotations_frozen_matches_status",
    ),
    ("doc_number_prefix", {"doc_number": "PI-2026-0001"}, "ck_quotations_doc_number_format"),
    ("doc_number_short", {"doc_number": "QT-2026-1"}, "ck_quotations_doc_number_format"),
    ("currency_lower", {"currency": "usd"}, "ck_quotations_currency_uppercase"),
    ("total_negative", {"total_amount": -1}, "ck_quotations_total_range"),
    ("total_over_2_53", {"total_amount": 2**53}, "ck_quotations_total_range"),
    ("last_line_no_negative", {"last_line_no": -1}, "ck_quotations_last_line_no_nonnegative"),
    ("fx_zero", {"fx_rate": 0, "fx_rate_date": date(2026, 9, 1)}, "ck_quotations_fx_rate_range"),
    (
        "fx_over_1e6",
        {"fx_rate": 1_000_001, "fx_rate_date": date(2026, 9, 1)},
        "ck_quotations_fx_rate_range",
    ),
    ("fx_without_date", {"fx_rate": 1300}, "ck_quotations_fx_pair"),
    ("fx_date_without_rate", {"fx_rate_date": date(2026, 9, 1)}, "ck_quotations_fx_pair"),
    (
        "krw_rate_not_one",
        {"currency": "KRW", "fx_rate": 2, "fx_rate_date": date(2026, 9, 1)},
        "ck_quotations_krw_fx_is_one",
    ),
    (
        "fx_date_after_doc_date",
        {"fx_rate": 1300, "fx_rate_date": date(2026, 9, 2)},
        "ck_quotations_fx_date_not_future",
    ),
    (
        "valid_until_before_doc",
        {"valid_until": date(2026, 8, 31)},
        "ck_quotations_valid_until_after_doc",
    ),
    ("buyer_name_blank", {"buyer_name": "  "}, "ck_quotations_buyer_name_not_blank"),
    # 결제조건 — 4열 형태
    (
        "payment_type_unknown",
        {"payment_type": "OTHER"},
        "ck_quotations_payment_type_valid|ck_quotations_payment_terms_shape",
    ),
    (
        "advance_bp_zero",
        {
            "payment_type": "TT_ADVANCE",
            "advance_pct_bp": 0,
            "balance_anchor": "ETD_DATE",
            "balance_days": 0,
        },
        "ck_quotations_payment_terms_shape",
    ),
    (
        "advance_bp_over",
        {"payment_type": "TT_ADVANCE", "advance_pct_bp": 10001},
        "ck_quotations_payment_terms_shape",
    ),
    (
        "full_advance_with_anchor",
        {
            "payment_type": "TT_ADVANCE",
            "advance_pct_bp": 10000,
            "balance_anchor": "ETD_DATE",
            "balance_days": 0,
        },
        "ck_quotations_payment_terms_shape",
    ),
    (
        "partial_advance_without_anchor",
        {"payment_type": "TT_ADVANCE", "advance_pct_bp": 3000},
        "ck_quotations_payment_terms_shape",
    ),
    (
        "deferred_with_advance",
        {
            "payment_type": "TT_DEFERRED",
            "advance_pct_bp": 100,
            "balance_anchor": "BL_DATE",
            "balance_days": 30,
        },
        "ck_quotations_payment_terms_shape",
    ),
    (
        "deferred_without_anchor",
        {"payment_type": "TT_DEFERRED"},
        "ck_quotations_payment_terms_shape",
    ),
    (
        "lc_with_days",
        {"payment_type": "LC", "balance_days": 30},
        "ck_quotations_payment_terms_shape",
    ),
    (
        "negative_days_not_etd",
        {"payment_type": "TT_DEFERRED", "balance_anchor": "BL_DATE", "balance_days": -1},
        "ck_quotations_neg_days_etd_only",
    ),
    (
        "anchor_unknown",
        {"payment_type": "TT_DEFERRED", "balance_anchor": "MOON_DATE", "balance_days": 1},
        "ck_quotations_balance_anchor_valid",
    ),
    (
        "days_out_of_range",
        {"payment_type": "TT_DEFERRED", "balance_anchor": "BL_DATE", "balance_days": 366},
        "ck_quotations_balance_days_range",
    ),
    # Incoterms
    ("incoterm_partial", {"incoterm_code": "FOB"}, "ck_quotations_incoterm_all_or_none"),
    (
        "incoterm_unknown_code",
        {"incoterm_code": "XXX", "incoterm_place": "Busan", "incoterm_year": 2020},
        "ck_quotations_incoterm_code_valid",
    ),
    (
        "incoterm_unknown_year",
        {"incoterm_code": "FOB", "incoterm_place": "Busan", "incoterm_year": 2015},
        "ck_quotations_incoterm_year_valid",
    ),
    (
        "incoterm_place_blank",
        {"incoterm_code": "FOB", "incoterm_place": " ", "incoterm_year": 2020},
        "ck_quotations_incoterm_place_not_blank",
    ),
    (
        "dat_2020",
        {"incoterm_code": "DAT", "incoterm_place": "Busan", "incoterm_year": 2020},
        "ck_quotations_incoterm_dat_2010",
    ),
    (
        "dpu_2010",
        {"incoterm_code": "DPU", "incoterm_place": "Busan", "incoterm_year": 2010},
        "ck_quotations_incoterm_dpu_2020",
    ),
    # 동결 결속
    (
        "draft_but_frozen",
        {**_FROZEN_OK, "status": "DRAFT"},
        "ck_quotations_frozen_matches_status",
    ),
    (
        "issued_without_frozen_at",
        {**_FROZEN_OK, "frozen_at": None},
        "ck_quotations_frozen_matches_status",
    ),
    (
        "frozen_without_payment",
        {
            **_FROZEN_OK,
            "payment_type": None,
            "advance_pct_bp": None,
            "balance_anchor": None,
            "balance_days": None,
        },
        "ck_quotations_frozen_complete",
    ),
    (
        "frozen_without_incoterm",
        {**_FROZEN_OK, "incoterm_code": None, "incoterm_place": None, "incoterm_year": None},
        "ck_quotations_frozen_complete",
    ),
    (
        "frozen_without_fx",
        {**_FROZEN_OK, "fx_rate": None, "fx_rate_date": None},
        "ck_quotations_frozen_complete",
    ),
    (
        "frozen_without_valid_until",
        {**_FROZEN_OK, "valid_until": None},
        "ck_quotations_frozen_complete",
    ),
    (
        "frozen_without_address",
        {**_FROZEN_OK, "buyer_address": None},
        "ck_quotations_frozen_complete",
    ),
    (
        "frozen_blank_address",
        {**_FROZEN_OK, "buyer_address": "  "},
        "ck_quotations_frozen_complete",
    ),
]


def test_a_valid_draft_and_a_valid_frozen_quotation_insert(base: dict[str, Any]) -> None:
    """양성 대조 — 기본 초안과 모든 동결 조건을 채운 발행본은 그대로 저장된다"""
    with engine.begin() as connection:
        _insert(connection, **base)
        _insert(connection, **{**base, **_FROZEN_OK, "doc_number": "QT-2026-0002"})


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _CHECK_CASES, ids=[c[0] for c in _CHECK_CASES]
)
def test_check_constraints_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """서비스를 우회한 직접 INSERT가 각 CHECK에서 거부된다(23514·제약 이름 일치)"""
    with engine.begin() as connection:
        _insert(
            connection, **{**base, "doc_number": "QT-2026-0009"}
        )  # 양성 대조 — 같은 base는 통과
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, **override})
    state, violated = _sqlstate_and_constraint(caught.value)
    # 한 값이 여러 CHECK를 동시에 깰 수 있다(예: 알 수 없는 상태는 status_valid와 frozen_matches_status) —
    # PG는 이름순으로 첫 위반을 보고하므로 `|`로 허용 집합을 적는다.
    assert state == CHECK_VIOLATION and violated in constraint.split("|"), (name, violated)


def test_a_cancelled_draft_needs_no_valid_until(base: dict[str, Any]) -> None:
    """초안 폐기(CANCELLED)는 유효기간·동결 시각 없이도 저장된다(초안 폐기=취소 — B4)"""
    with engine.begin() as connection:
        _insert(connection, **{**base, "status": "CANCELLED"})


def test_doc_number_is_globally_unique_even_after_soft_delete(base: dict[str, Any]) -> None:
    """doc_number는 전역 UNIQUE — soft delete 행이 있어도 같은 번호를 다시 못 쓴다(재발급 금지 §17.3)"""
    with engine.begin() as connection:
        first = _insert(connection, **base)
        connection.execute(
            text("UPDATE quotations SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **base)
    assert _sqlstate_and_constraint(caught.value) == (UNIQUE_VIOLATION, "uq_quotations_doc_number")


def test_only_one_live_copy_per_source(base: dict[str, Any]) -> None:
    """복제본은 원본당 살아 있는 것 하나 — 취소·만료된 복제본은 세지 않는다(X-08)"""
    with engine.begin() as connection:
        source = _insert(connection, **{**base, "status": "CANCELLED"})
        _insert(connection, **{**base, "doc_number": "QT-2026-0002", "copied_from_id": source})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "QT-2026-0003", "copied_from_id": source})
    assert _sqlstate_and_constraint(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_quotations_copied_from_id_live",
    )
    with engine.begin() as connection:  # 이전 복제본이 취소되면 새 복제본이 가능하다
        connection.execute(
            text("UPDATE quotations SET status = 'CANCELLED' WHERE copied_from_id = :s"),
            {"s": source},
        )
        _insert(connection, **{**base, "doc_number": "QT-2026-0004", "copied_from_id": source})


def test_a_quotation_cannot_be_its_own_copy_source(base: dict[str, Any]) -> None:
    """자기 자신을 복제 원본으로 가리킬 수 없다"""
    with engine.begin() as connection:
        qid = _insert(connection, **base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET copied_from_id = id WHERE id = :i"), {"i": qid}
        )
    assert _sqlstate_and_constraint(caught.value) == (
        CHECK_VIOLATION,
        "ck_quotations_copied_from_not_self",
    )


def test_unknown_market_and_buyer_are_rejected_by_fk(base: dict[str, Any]) -> None:
    """FK — 등록되지 않은 시장·존재하지 않는 거래처는 DB가 막는다"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, "dest_market_code": "ZZ"})
    assert _sqlstate_and_constraint(caught.value)[0] == FK_VIOLATION
    with pytest.raises(IntegrityError) as caught2, engine.begin() as connection:
        _insert(connection, **{**base, "buyer_partner_id": 999_999})
    assert _sqlstate_and_constraint(caught2.value)[0] == FK_VIOLATION


# ── 라인 ────────────────────────────────────────────────────────────────────

_LINE_COLUMNS = (
    "qt_id, currency, line_no, sku_id, sku_code, sku_name_ko, sku_kind, quantity,"
    " unit_price_amount, list_price_amount, line_amount, price_basis, is_free, price_reason"
)


def _insert_line(connection: Connection, **values: Any) -> int:
    row = {"list_price_amount": None, "price_reason": None, **values}
    placeholders = ", ".join(f":{name}" for name in (c.strip() for c in _LINE_COLUMNS.split(",")))
    return int(
        connection.execute(
            text(
                f"INSERT INTO quotation_lines ({_LINE_COLUMNS}) VALUES ({placeholders}) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


@pytest.fixture
def line_base(base: dict[str, Any]) -> dict[str, Any]:
    with engine.begin() as connection:
        qt_id = _insert(connection, **base)
    sku_id = create_sku("SKU-QC")
    return {
        "qt_id": qt_id,
        "currency": "USD",
        "line_no": 1,
        "sku_id": sku_id,
        "sku_code": "SKU-QC",
        "sku_name_ko": "테스트",
        "sku_kind": "SINGLE",
        "quantity": 10,
        "unit_price_amount": 250,
        "line_amount": 2500,
        "price_basis": "MASTER",
        "is_free": False,
    }


_LINE_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("currency_lower", {"currency": "usd"}, "ck_quotation_lines_currency_uppercase"),
    ("line_no_zero", {"line_no": 0}, "ck_quotation_lines_line_no_positive"),
    ("quantity_zero", {"quantity": 0, "line_amount": 0}, "ck_quotation_lines_quantity_range"),
    (
        "quantity_over",
        {"quantity": 100_000_000, "line_amount": 25_000_000_000},
        "ck_quotation_lines_quantity_range",
    ),
    ("sku_kind_unknown", {"sku_kind": "BOX"}, "ck_quotation_lines_sku_kind_valid"),
    ("price_basis_unknown", {"price_basis": "GUESS"}, "ck_quotation_lines_price_basis_valid"),
    ("amount_mismatch", {"line_amount": 2501}, "ck_quotation_lines_line_amount_matches"),
    (
        "unit_price_over_2_53",
        {"unit_price_amount": 2**53, "quantity": 1, "line_amount": 2**53},
        "ck_quotation_lines_unit_price_range|ck_quotation_lines_line_amount_range",
    ),
    ("list_price_negative", {"list_price_amount": -1}, "ck_quotation_lines_list_price_range"),
    (
        "free_flag_with_price",
        {"is_free": True, "price_reason": "샘플"},
        "ck_quotation_lines_free_iff_zero_price",
    ),
    (
        "zero_price_without_flag",
        {"unit_price_amount": 0, "line_amount": 0, "price_basis": "MANUAL"},
        "ck_quotation_lines_free_iff_zero_price",
    ),
    (
        "free_without_reason",
        {"unit_price_amount": 0, "line_amount": 0, "is_free": True, "price_basis": "MANUAL"},
        "ck_quotation_lines_free_requires_reason",
    ),
    (
        "free_from_master",
        {"unit_price_amount": 0, "line_amount": 0, "is_free": True, "price_reason": "샘플"},
        "ck_quotation_lines_free_not_from_master",
    ),
]


def test_a_valid_line_and_a_valid_free_line_insert(line_base: dict[str, Any]) -> None:
    """양성 대조 — 유상 라인과 명시 무상 라인(같은 SKU)이 함께 저장된다"""
    with engine.begin() as connection:
        _insert_line(connection, **line_base)
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 2,
                "unit_price_amount": 0,
                "line_amount": 0,
                "is_free": True,
                "price_basis": "MANUAL",
                "price_reason": "샘플 증정",
            },
        )


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LINE_CASES, ids=[c[0] for c in _LINE_CASES]
)
def test_line_checks_reject_violations(
    line_base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """라인 CHECK — 금액=수량×단가(numeric 곱)·무상 양방향·상한·열거가 DB에서 거부된다"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, **override})
    state, violated = _sqlstate_and_constraint(caught.value)
    assert state == CHECK_VIOLATION and violated in constraint.split("|"), (name, violated)


def test_line_amount_check_does_not_overflow_bigint(line_base: dict[str, Any]) -> None:
    """quantity×단가가 bigint를 넘어도 500(오버플로)이 아니라 CHECK 위반으로 거부된다(numeric 곱)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(
            connection,
            **{
                **line_base,
                "quantity": 99_999_999,
                "unit_price_amount": 2**53 - 1,
                "line_amount": 2**53 - 1,
            },
        )
    assert _sqlstate_and_constraint(caught.value)[0] == CHECK_VIOLATION


def test_line_currency_must_match_the_header_currency(line_base: dict[str, Any]) -> None:
    """복합 FK (qt_id, currency) — 헤더와 다른 통화 라인은 DB가 거부한다(혼합 통화 불가능)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "currency": "EUR"})
    assert _sqlstate_and_constraint(caught.value) == (
        FK_VIOLATION,
        "fk_quotation_lines_qt_id_currency_quotations",
    )


def test_header_currency_cannot_change_while_lines_exist(line_base: dict[str, Any]) -> None:
    """라인이 있는 헤더의 통화 변경은 복합 FK가 막는다"""
    with engine.begin() as connection:
        _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET currency = 'EUR' WHERE id = :i"),
            {"i": line_base["qt_id"]},
        )
    assert _sqlstate_and_constraint(caught.value)[0] == FK_VIOLATION


def test_line_uniqueness_is_active_only_and_allows_one_paid_and_one_free_per_sku(
    line_base: dict[str, Any],
) -> None:
    """(qt, line_no)·(qt, sku, is_free) 활성 유니크 — soft delete 뒤 재추가는 허용된다"""
    with engine.begin() as connection:
        first = _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as dup_line_no, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "sku_id": create_sku("SKU-QC2")})
    assert _sqlstate_and_constraint(dup_line_no.value) == (
        UNIQUE_VIOLATION,
        "uq_quotation_lines_qt_id_line_no_active",
    )
    with pytest.raises(IntegrityError) as dup_sku, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2})
    assert _sqlstate_and_constraint(dup_sku.value) == (
        UNIQUE_VIOLATION,
        "uq_quotation_lines_qt_id_sku_id_is_free_active",
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE quotation_lines SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        _insert_line(connection, **{**line_base, "line_no": 2})  # 삭제 후 같은 SKU 재추가


# ── 상태이력 ────────────────────────────────────────────────────────────────


def _log(connection: Connection, qt_id: int, **values: Any) -> None:
    row = {
        "from_status": None,
        "to_status": "DRAFT",
        "reason": None,
        "actor_user_id": None,
        "automatic": True,
        **values,
    }
    connection.execute(
        text(
            "INSERT INTO quotation_status_log (quotation_id, from_status, to_status, reason,"
            " actor_user_id, automatic) VALUES (:q, :from_status, :to_status, :reason,"
            " :actor_user_id, :automatic)"
        ),
        {"q": qt_id, **row},
    )


_LOG_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "to_unknown",
        {"from_status": "DRAFT", "to_status": "PAID"},
        "ck_quotation_status_log_to_status_valid",
    ),
    (
        "from_unknown",
        {"from_status": "PAID", "to_status": "ISSUED"},
        "ck_quotation_status_log_from_status_valid",
    ),
    (
        "self_transition",
        {"from_status": "ISSUED", "to_status": "ISSUED"},
        "ck_quotation_status_log_no_self_transition",
    ),
    (
        "birth_not_draft",
        {"from_status": None, "to_status": "ISSUED"},
        "ck_quotation_status_log_birth_row",
    ),
    (
        "cancel_without_reason",
        {"from_status": "DRAFT", "to_status": "CANCELLED", "automatic": False, "actor_user_id": 1},
        "ck_quotation_status_log_reason_required",
    ),
    (
        "expire_without_reason",
        {"from_status": "ISSUED", "to_status": "EXPIRED"},
        "ck_quotation_status_log_reason_required",
    ),
    (
        "blank_reason",
        {"from_status": "DRAFT", "to_status": "CANCELLED", "reason": "   "},
        "ck_quotation_status_log_reason_not_blank",
    ),
    (
        "reason_too_long",
        {"from_status": "DRAFT", "to_status": "CANCELLED", "reason": "가" * 501},
        "ck_quotation_status_log_reason_not_blank",
    ),
    (
        "no_actor_not_automatic",
        {"from_status": "DRAFT", "to_status": "ISSUED", "automatic": False, "actor_user_id": None},
        "ck_quotation_status_log_actor_or_automatic",
    ),
]


@pytest.fixture
def qt_id(base: dict[str, Any]) -> int:
    with engine.begin() as connection:
        return _insert(connection, **base)


def test_status_log_accepts_birth_and_a_valid_transition(qt_id: int, base: dict[str, Any]) -> None:
    """양성 대조 — 탄생 행과 정상 전이 행이 저장된다"""
    with engine.begin() as connection:
        _log(connection, qt_id, automatic=False, actor_user_id=base["assignee_id"])
        _log(
            connection,
            qt_id,
            from_status="DRAFT",
            to_status="CANCELLED",
            reason="초안 폐기",
            automatic=False,
            actor_user_id=base["assignee_id"],
        )


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LOG_CASES, ids=[c[0] for c in _LOG_CASES]
)
def test_status_log_checks_reject_violations(
    qt_id: int, base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """상태이력 CHECK 7종 — 서비스를 우회한 직접 INSERT도 DB가 거부한다"""
    values = {"actor_user_id": base["assignee_id"], "automatic": False, **override}
    if override.get("actor_user_id", 0) is None:
        values["actor_user_id"] = None
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, qt_id, **values)
    assert _sqlstate_and_constraint(caught.value) == (CHECK_VIOLATION, constraint), name


def test_a_document_has_exactly_one_birth_row(qt_id: int) -> None:
    """문서당 탄생 행(from NULL)은 하나 — 두 번째는 부분 유니크가 거부한다"""
    with engine.begin() as connection:
        _log(connection, qt_id)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, qt_id)
    assert _sqlstate_and_constraint(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_quotation_status_log_quotation_id_birth",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE quotation_status_log SET reason = 'x'",
        "DELETE FROM quotation_status_log",
        "TRUNCATE quotation_status_log",
    ],
)
def test_status_log_is_immutable_for_the_app_role(qt_id: int, statement: str) -> None:
    """이력은 INSERT/SELECT만 — 앱 계정의 UPDATE/DELETE/TRUNCATE는 42501로 거부된다(§17.5)"""
    with engine.begin() as connection:
        _log(connection, qt_id)
    with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
        connection.execute(text(statement))
    assert _sqlstate_and_constraint(caught.value)[0] == PERMISSION_DENIED
    with owner_engine.connect() as connection:  # 실제로 행이 남아 있다
        assert (
            connection.execute(text("SELECT count(*) FROM quotation_status_log")).scalar_one() == 1
        )


def test_check_definitions_are_pinned(qt_id: int) -> None:
    """CHECK·부분 인덱스의 정의문이 DB에 실재한다(pg_get_constraintdef — alembic check는 CHECK를 못 본다)"""
    with owner_engine.connect() as connection:
        defs = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'quotations'::regclass AND contype = 'c'"
                )
            ).all()
        )
        indexdef = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_quotations_copied_from_id_live'"
            )
        ).scalar_one()
    assert "'DRAFT'" in defs["ck_quotations_frozen_matches_status"]
    assert re.search(r"QT-\[0-9\]\{4\}-\[0-9\]\{4,\}", defs["ck_quotations_doc_number_format"])
    assert "IS NOT NULL" in defs["ck_quotations_frozen_complete"]
    assert (
        "CANCELLED" in indexdef
        and "EXPIRED" in indexdef
        and "copied_from_id IS NOT NULL" in indexdef
    )
