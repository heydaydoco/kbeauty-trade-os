"""A·K. PO·PO 라인·PO 상태이력의 불변식을 DB가 강제한다 (S3-1 M06 / ADR-0051~0053·0057 / design-A A2·A12 / design-B B1·B2·B6).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 모든 위반 케이스는 같은 조건의
  **양성 대조 INSERT가 성공**함을 함께 확인한다(TRUNCATE 하네스 공회전 방지). 결제조건·Incoterms·환율·통화 CHECK는 QT·PI·SO와 같은 생성기라 대표
  표본만 본다. 핵심은 PO 고유 규칙 — **원가 열 이름**·**단가>0**·**라인원가=수량×단가**·`po_kind`·**OC 결속 CHECK**·동결 완결성(생성=발행)이다.
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
from tests.factories.trade import create_supplier, raw_po, unique
from tests.support.factories import create_sku, create_user

pytestmark = pytest.mark.group_a

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"
PERMISSION_DENIED = "42501"


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


@pytest.fixture
def base() -> dict[str, Any]:
    """유효한 PO 한 줄의 기본값 — 발행 상태·OC 없음·동결 서류 값 전부."""
    return {
        "doc_number": "PO-2026-0001",
        "doc_date": date(2026, 9, 1),
        "status": "ISSUED",
        "currency": "USD",
        "assignee_id": create_user(f"{unique('poc')}@example.com"),
        "supplier_partner_id": create_supplier(),
        "supplier_name": "Raw Supplier",
        "po_kind": "PURCHASE",
        "oc_received_on": None,
        "oc_reference": None,
        "payment_type": "TT_DEFERRED",
        "advance_pct_bp": None,
        "balance_anchor": "RECEIPT_DATE",
        "balance_days": 30,
        "incoterm_code": "EXW",
        "incoterm_place": "Seoul",
        "incoterm_year": 2020,
        "fx_rate": 1350,
        "fx_rate_date": date(2026, 9, 1),
    }


_COLUMNS = (
    "doc_number, doc_date, status, currency, assignee_id, supplier_partner_id, supplier_name,"
    " po_kind, oc_received_on, oc_reference, payment_type, advance_pct_bp, balance_anchor,"
    " balance_days, incoterm_code, incoterm_place, incoterm_year, fx_rate, fx_rate_date,"
    " copied_from_id, total_cost, last_line_no"
)


def _insert(connection: Connection, **values: Any) -> int:
    row = {"copied_from_id": None, "total_cost": 0, "last_line_no": 0, **values}
    names = [c.strip() for c in _COLUMNS.split(",")]
    return int(
        connection.execute(
            text(
                f"INSERT INTO purchase_orders ({_COLUMNS}) VALUES ({', '.join(':' + n for n in names)})"
                " RETURNING id"
            ),
            row,
        ).scalar_one()
    )


_CHECK_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("status_unknown", {"status": "DRAFT"}, "ck_purchase_orders_status_valid"),
    ("status_so_value", {"status": "RECEIVED"}, "ck_purchase_orders_status_valid"),
    ("doc_number_prefix", {"doc_number": "SO-2026-0001"}, "ck_purchase_orders_doc_number_format"),
    ("currency_lower", {"currency": "usd"}, "ck_purchase_orders_currency_uppercase"),
    ("total_over_2_53", {"total_cost": 2**53}, "ck_purchase_orders_total_range"),
    ("total_negative", {"total_cost": -1}, "ck_purchase_orders_total_range"),
    # 공백 이름은 동결 완결성(btrim<>'')과 이름 CHECK가 **함께** 위반이라 DB가 먼저 보고하는(이름순) 완결성 CHECK로 잡힌다.
    ("supplier_name_blank", {"supplier_name": " "}, "ck_purchase_orders_frozen_complete"),
    ("po_kind_unknown", {"po_kind": "SAMPLE"}, "ck_purchase_orders_po_kind_valid"),
    ("po_kind_lowercase", {"po_kind": "purchase"}, "ck_purchase_orders_po_kind_valid"),
    ("fx_zero", {"fx_rate": 0}, "ck_purchase_orders_fx_rate_range"),
    ("fx_without_date", {"fx_rate": 1350, "fx_rate_date": None}, "ck_purchase_orders_fx_pair"),
    (
        "krw_rate_not_one",
        {"currency": "KRW", "fx_rate": 2},
        "ck_purchase_orders_krw_fx_is_one",
    ),
    (
        "deferred_without_anchor",
        {"balance_anchor": None, "balance_days": None},
        "ck_purchase_orders_payment_terms_shape",
    ),
    ("incoterm_partial", {"incoterm_place": None}, "ck_purchase_orders_incoterm_all_or_none"),
    # 동결 완결성 — 생성=발행이라 frozen_at이 항상 있다(기본 now()) → 서류 필수 값이 전부 있어야 한다
    (
        "frozen_without_terms",
        {
            "payment_type": None,
            "advance_pct_bp": None,
            "balance_anchor": None,
            "balance_days": None,
        },
        "ck_purchase_orders_frozen_complete",
    ),
    (
        "frozen_without_incoterm",
        {"incoterm_code": None, "incoterm_place": None, "incoterm_year": None},
        "ck_purchase_orders_frozen_complete",
    ),
    (
        "frozen_without_fx",
        {"fx_rate": None, "fx_rate_date": None},
        "ck_purchase_orders_frozen_complete",
    ),
    # OC 결속 — 발행 중에는 비어 있고 공급사 확인 이후에는 OC 일자가 필수, OC ≥ 증빙일, 참조는 공백 불가
    (
        "issued_with_oc_date",
        {"status": "ISSUED", "oc_received_on": date(2026, 9, 2)},
        "ck_purchase_orders_oc_empty_when_issued",
    ),
    (
        "issued_with_oc_reference",
        {"status": "ISSUED", "oc_reference": "OC-1"},
        "ck_purchase_orders_oc_empty_when_issued",
    ),
    (
        "confirmed_without_oc_date",
        {"status": "SUPPLIER_CONFIRMED"},
        "ck_purchase_orders_oc_required_after_confirm",
    ),
    (
        "closed_without_oc_date",
        {"status": "CLOSED"},
        "ck_purchase_orders_oc_required_after_confirm",
    ),
    (
        "oc_before_doc_date",
        {"status": "SUPPLIER_CONFIRMED", "oc_received_on": date(2026, 8, 31)},
        "ck_purchase_orders_oc_not_before_doc",
    ),
    (
        "oc_reference_blank",
        {"status": "SUPPLIER_CONFIRMED", "oc_received_on": date(2026, 9, 2), "oc_reference": "  "},
        "ck_purchase_orders_oc_reference_not_blank",
    ),
]


def test_valid_pos_insert_in_every_state_shape(base: dict[str, Any]) -> None:
    """양성 대조 — 발행 PO·OC 기록된 공급사 확인 PO·후반 예약 상태 값·OEM 생산 발주·OC 없이 바로 취소된 PO·OC 있는 취소 PO가 전부 저장된다"""
    oc = {"oc_received_on": date(2026, 9, 2), "oc_reference": "OC-1"}
    with engine.begin() as connection:
        _insert(connection, **base)
        rows = [
            ("PO-2026-0002", "SUPPLIER_CONFIRMED", oc),
            ("PO-2026-0003", "PARTIALLY_RECEIVED", oc),
            ("PO-2026-0004", "FULLY_RECEIVED", oc),
            ("PO-2026-0005", "CLOSED", oc),
            ("PO-2026-0006", "CANCELLED", {}),  # ISSUED에서 바로 취소 — OC NULL
            ("PO-2026-0007", "CANCELLED", oc),  # 공급사 확인 뒤 취소 — OC 유지
            ("PO-2026-0008", "ISSUED", {"po_kind": "OEM_PRODUCTION"}),
        ]
        for number, status, extra in rows:
            _insert(connection, **{**base, "doc_number": number, "status": status, **extra})
        assert _insert(connection, **{**base, "doc_number": "PO-2026-0009", "oc_reference": None})


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _CHECK_CASES, ids=[c[0] for c in _CHECK_CASES]
)
def test_check_constraints_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """서비스를 우회한 직접 INSERT가 각 CHECK에서 거부된다(23514·제약 이름 일치) — 같은 base는 양성 대조로 통과한다"""
    with engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "PO-2026-0009"})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, **override})
    state, violated = _state(caught.value)
    assert state == CHECK_VIOLATION and violated == constraint, (name, violated)


def test_the_oc_columns_cannot_be_unset_or_contradict_the_state_by_update(
    base: dict[str, Any],
) -> None:
    """UPDATE로도 결속이 깨지지 않는다 — 공급사 확인 PO의 OC 일자를 NULL로, 발행 PO에 OC를 채우는 UPDATE는 23514"""
    with engine.begin() as connection:
        confirmed = _insert(
            connection,
            **{**base, "status": "SUPPLIER_CONFIRMED", "oc_received_on": date(2026, 9, 2)},
        )
        issued = _insert(connection, **{**base, "doc_number": "PO-2026-0002"})
    for sql, po_id, constraint in (
        (
            "UPDATE purchase_orders SET oc_received_on = NULL WHERE id = :i",
            confirmed,
            "ck_purchase_orders_oc_required_after_confirm",
        ),
        (
            "UPDATE purchase_orders SET oc_received_on = '2026-09-02' WHERE id = :i",
            issued,
            "ck_purchase_orders_oc_empty_when_issued",
        ),
        (
            "UPDATE purchase_orders SET status = 'SUPPLIER_CONFIRMED' WHERE id = :i",
            issued,
            "ck_purchase_orders_oc_required_after_confirm",
        ),
    ):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            connection.execute(text(sql), {"i": po_id})
        assert _state(caught.value) == (CHECK_VIOLATION, constraint)
    with engine.begin() as connection:  # 양성 대조 — 같은 UPDATE에 OC를 함께 실으면 통과
        connection.execute(
            text(
                "UPDATE purchase_orders SET status = 'SUPPLIER_CONFIRMED',"
                " oc_received_on = '2026-09-02' WHERE id = :i"
            ),
            {"i": issued},
        )


@pytest.mark.parametrize(
    "missing",
    [
        "doc_number",
        "doc_date",
        "status",
        "currency",
        "supplier_partner_id",
        "supplier_name",
        "assignee_id",
        "po_kind",
    ],
)
def test_required_columns_are_not_null(base: dict[str, Any], missing: str) -> None:
    """필수 열은 NOT NULL — 공급사·통화·구분·담당자 없는 PO가 DB에서 불가능하다"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, missing: None})
    assert _state(caught.value)[0] == "23502"


def test_po_kind_defaults_to_purchase_and_frozen_at_defaults_to_now() -> None:
    """po_kind는 생략하면 PURCHASE, frozen_at은 생략하면 생성 시각(생성=발행=동결) — 둘 다 DB 기본값이다"""
    po_id = raw_po("ISSUED")
    with owner_engine.connect() as connection:
        row = connection.execute(
            text("SELECT po_kind, frozen_at IS NOT NULL FROM purchase_orders WHERE id = :i"),
            {"i": po_id},
        ).one()
    assert tuple(row) == ("PURCHASE", True)
    with owner_engine.connect() as connection:
        default = connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns WHERE table_name = 'purchase_orders'"
                " AND column_name = 'po_kind'"
            )
        ).scalar_one()
    assert "PURCHASE" in default


def test_doc_number_is_globally_unique_even_after_soft_delete(base: dict[str, Any]) -> None:
    """doc_number는 전역 UNIQUE — soft delete 뒤에도 같은 번호를 다시 못 쓴다(재발급 금지 §17.3)"""
    with engine.begin() as connection:
        first = _insert(connection, **base)
        connection.execute(
            text("UPDATE purchase_orders SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **base)
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_purchase_orders_doc_number")


def test_a_po_may_repeat_the_supplier_and_there_is_no_buyer_po_key(base: dict[str, Any]) -> None:
    """같은 공급사에 PO는 몇 건이든(중복 금지 술어는 판매 SO의 바이어 PO 키 전용) — PO에는 그런 키 열이 없다"""
    with engine.begin() as connection:
        for n in range(1, 4):
            _insert(connection, **{**base, "doc_number": f"PO-2026-000{n}"})
    with owner_engine.connect() as connection:
        columns = {
            r[0]
            for r in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'purchase_orders'"
                )
            )
        }
    assert not {c for c in columns if c.startswith("buyer_")} and "supplier_partner_id" in columns


def test_a_live_copy_is_unique_per_source_and_released_by_cancellation(
    base: dict[str, Any],
) -> None:
    """살아 있는 복제본은 원본당 하나(X-08) — 취소된 복제본은 세지 않아 다시 복제 가능, 자기 자신 복제는 CHECK가 거부"""
    with engine.begin() as connection:
        source = _insert(connection, **{**base, "status": "CANCELLED"})
        first = _insert(
            connection, **{**base, "doc_number": "PO-2026-0002", "copied_from_id": source}
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "PO-2026-0003", "copied_from_id": source})
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_purchase_orders_copied_from_id_live")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE purchase_orders SET status = 'CANCELLED' WHERE id = :i"), {"i": first}
        )
        _insert(connection, **{**base, "doc_number": "PO-2026-0004", "copied_from_id": source})
    with pytest.raises(IntegrityError) as selfcopy, engine.begin() as connection:
        connection.execute(
            text("UPDATE purchase_orders SET copied_from_id = id WHERE id = :i"), {"i": source}
        )
    assert _state(selfcopy.value) == (CHECK_VIOLATION, "ck_purchase_orders_copied_from_not_self")


def test_foreign_keys_are_restrict(base: dict[str, Any]) -> None:
    """공급사·담당자를 지우려는 DELETE는 FK RESTRICT가 거부한다(전표가 참조하는 마스터는 사라지지 않는다)"""
    with engine.begin() as connection:
        _insert(connection, **base)
    for table, column in (("partners", "supplier_partner_id"), ("users", "assignee_id")):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": base[column]})
        assert _state(caught.value)[0] == FK_VIOLATION


# ── 라인 ────────────────────────────────────────────────────────────────────


_LINE_COLUMNS = (
    "po_id, currency, line_no, sku_id, sku_code, sku_name_ko, sku_kind, quantity,"
    " unit_cost, line_cost, price_basis"
)


def _insert_line(connection: Connection, **values: Any) -> int:
    names = [c.strip() for c in _LINE_COLUMNS.split(",")]
    return int(
        connection.execute(
            text(
                f"INSERT INTO purchase_order_lines ({_LINE_COLUMNS}) VALUES"
                f" ({', '.join(':' + n for n in names)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


@pytest.fixture
def line_base(base: dict[str, Any]) -> dict[str, Any]:
    with engine.begin() as connection:
        po_id = _insert(connection, **base)
    return {
        "po_id": po_id,
        "currency": "USD",
        "line_no": 1,
        "sku_id": create_sku(unique("PL")),
        "sku_code": "SKU",
        "sku_name_ko": "테스트",
        "sku_kind": "SINGLE",
        "quantity": 10,
        "unit_cost": 500,
        "line_cost": 5000,
        "price_basis": "MASTER",
    }


#: 한 위반이 여러 CHECK를 함께 깨는 경우(단가 0 → 라인원가 0 등)는 DB가 이름순으로 먼저 보고한 것이 허용 집합에 있으면 된다.
_LINE_CASES: list[tuple[str, dict[str, Any], frozenset[str]]] = [
    (
        "unit_cost_zero",
        {"unit_cost": 0, "line_cost": 0},
        frozenset(
            {"ck_purchase_order_lines_unit_cost_range", "ck_purchase_order_lines_line_cost_range"}
        ),
    ),
    (
        "unit_cost_negative",
        {"unit_cost": -5, "line_cost": -50},
        frozenset(
            {"ck_purchase_order_lines_unit_cost_range", "ck_purchase_order_lines_line_cost_range"}
        ),
    ),
    (
        "line_cost_zero",
        {"unit_cost": 500, "line_cost": 0},
        frozenset(
            {"ck_purchase_order_lines_line_cost_range", "ck_purchase_order_lines_line_cost_matches"}
        ),
    ),
    (
        "line_cost_mismatch",
        {"line_cost": 5001},
        frozenset({"ck_purchase_order_lines_line_cost_matches"}),
    ),
    (
        "unit_cost_over_2_53",
        {"unit_cost": 2**53, "quantity": 1, "line_cost": 2**53},
        frozenset(
            {"ck_purchase_order_lines_unit_cost_range", "ck_purchase_order_lines_line_cost_range"}
        ),
    ),
    (
        "quantity_zero",
        {"quantity": 0, "line_cost": 0},
        frozenset(
            {"ck_purchase_order_lines_quantity_range", "ck_purchase_order_lines_line_cost_range"}
        ),
    ),
    (
        "quantity_over_cap",
        {"quantity": 100_000_000, "line_cost": 50_000_000_000},
        frozenset({"ck_purchase_order_lines_quantity_range"}),
    ),
    (
        "price_basis_buyer_po",
        {"price_basis": "BUYER_PO"},
        frozenset({"ck_purchase_order_lines_price_basis_valid"}),
    ),
    (
        "price_basis_lower",
        {"price_basis": "master"},
        frozenset({"ck_purchase_order_lines_price_basis_valid"}),
    ),
    (
        "sku_kind_unknown",
        {"sku_kind": "BUNDLE"},
        frozenset({"ck_purchase_order_lines_sku_kind_valid"}),
    ),
    ("line_no_zero", {"line_no": 0}, frozenset({"ck_purchase_order_lines_line_no_positive"})),
    (
        "currency_lower",
        {"currency": "usd"},
        frozenset(
            {
                "ck_purchase_order_lines_currency_uppercase",
                "fk_purchase_order_lines_po_id_currency_purchase_orders",
            }
        ),
    ),
]


def test_a_valid_line_inserts_and_a_product_beyond_bigint_cannot_overflow(
    line_base: dict[str, Any],
) -> None:
    """양성 대조 — 유효 라인 저장, 수동·마스터 기준 모두 통과 · 수량×단가의 numeric 곱이라 bigint 경계 근처 곱도 500이 아니라 CHECK로 거부된다"""
    with engine.begin() as connection:
        _insert_line(connection, **line_base)
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 2,
                "sku_id": create_sku(unique("PL2")),
                "price_basis": "MANUAL",
            },
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 3,
                "sku_id": create_sku(unique("PL3")),
                "quantity": 99_999_999,
                "unit_cost": 2**53 - 1,
                "line_cost": 2**53 - 1,
            },
        )
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_purchase_order_lines_line_cost_matches")


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LINE_CASES, ids=[c[0] for c in _LINE_CASES]
)
def test_line_check_constraints_reject_violations(
    line_base: dict[str, Any], name: str, override: dict[str, Any], constraint: frozenset[str]
) -> None:
    """PO 라인 CHECK — 단가>0(무상 매입 미지원)·라인원가=수량×단가·상한·기준 값·SKU 종류·라인번호·통화가 DB에서 거부된다"""
    with engine.begin() as connection:  # 양성 대조 — 같은 base 라인은 통과한다
        _insert_line(connection, **{**line_base, "line_no": 9, "sku_id": create_sku(unique("PLX"))})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, **override})
    state, violated = _state(caught.value)
    assert state in (CHECK_VIOLATION, FK_VIOLATION) and violated in constraint, (name, violated)


def test_a_line_currency_must_match_the_header_currency(line_base: dict[str, Any]) -> None:
    """라인 통화는 헤더 통화와 복합 FK로 묶인다 — 다른 통화의 라인은 FK 위반(혼합 통화 불가능)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "currency": "EUR"})
    assert _state(caught.value) == (
        FK_VIOLATION,
        "fk_purchase_order_lines_po_id_currency_purchase_orders",
    )


def test_a_line_needs_a_real_sku_and_a_real_po(line_base: dict[str, Any]) -> None:
    """SKU 전용 — sku_id는 NOT NULL·FK(자재 id를 넣을 열이 없다), po_id도 실재해야 한다"""
    for field, value, expected in (
        ("sku_id", None, "23502"),
        ("sku_id", 999_999, FK_VIOLATION),
        ("po_id", 999_999, FK_VIOLATION),
    ):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _insert_line(connection, **{**line_base, field: value})
        assert _state(caught.value)[0] == expected, field
    with owner_engine.connect() as connection:
        columns = {
            r[0]
            for r in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'purchase_order_lines'"
                )
            )
        }
    assert not columns & {"material_id", "item_type", "uom", "unit_price_amount", "line_amount"}


def test_a_sku_appears_once_per_po_but_can_come_back_after_soft_delete(
    line_base: dict[str, Any],
) -> None:
    """(po_id, sku_id)·(po_id, line_no)는 살아 있는 행 기준 유일 — 분할 납기 미지원, 삭제 뒤에는 재사용"""
    with engine.begin() as connection:
        first = _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as dup, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2})
    assert _state(dup.value) == (UNIQUE_VIOLATION, "uq_purchase_order_lines_po_id_sku_id_active")
    with pytest.raises(IntegrityError) as dup_no, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "sku_id": create_sku(unique("PL5"))})
    assert _state(dup_no.value) == (
        UNIQUE_VIOLATION,
        "uq_purchase_order_lines_po_id_line_no_active",
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE purchase_order_lines SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        _insert_line(connection, **{**line_base, "line_no": 2})


# ── 상태이력 ────────────────────────────────────────────────────────────────


def _log(connection: Connection, po_id: int, **values: Any) -> None:
    row = {
        "from_status": None,
        "to_status": "ISSUED",
        "reason": None,
        "actor_user_id": None,
        "automatic": True,
        **values,
    }
    connection.execute(
        text(
            "INSERT INTO purchase_order_status_log (purchase_order_id, from_status, to_status, reason,"
            " actor_user_id, automatic) VALUES (:p, :from_status, :to_status, :reason, :actor_user_id,"
            " :automatic)"
        ),
        {"p": po_id, **row},
    )


_LOG_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "to_draft",
        {"from_status": "ISSUED", "to_status": "DRAFT"},
        "ck_purchase_order_status_log_to_status_valid",
    ),
    (
        "self_transition",
        {"from_status": "ISSUED", "to_status": "ISSUED"},
        "ck_purchase_order_status_log_no_self_transition",
    ),
    (
        "birth_not_issued",
        {"from_status": None, "to_status": "SUPPLIER_CONFIRMED"},
        "ck_purchase_order_status_log_birth_row",
    ),
    (
        "cancel_without_reason",
        {"from_status": "ISSUED", "to_status": "CANCELLED"},
        "ck_purchase_order_status_log_reason_required",
    ),
    (
        "no_actor_not_automatic",
        {"from_status": "ISSUED", "to_status": "SUPPLIER_CONFIRMED", "automatic": False},
        "ck_purchase_order_status_log_actor_or_automatic",
    ),
]


def test_status_log_accepts_birth_confirmation_and_reasoned_cancel(base: dict[str, Any]) -> None:
    """양성 대조 — 탄생·공급사 확인은 사유 없이, 취소는 사유와 함께 저장된다"""
    with engine.begin() as connection:
        po_id = _insert(connection, **base)
        _log(connection, po_id)
        _log(connection, po_id, from_status="ISSUED", to_status="SUPPLIER_CONFIRMED")
        _log(
            connection,
            po_id,
            from_status="SUPPLIER_CONFIRMED",
            to_status="CANCELLED",
            reason="사정",
        )


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LOG_CASES, ids=[c[0] for c in _LOG_CASES]
)
def test_status_log_checks_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """PO 상태이력 CHECK — 값·자기전이·탄생 행(ISSUED)·사유 필수(취소)·행위자 또는 자동이 DB에서 거부된다"""
    with engine.begin() as connection:
        po_id = _insert(connection, **base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, po_id, **{"automatic": True, **override})
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), name


def test_the_birth_row_is_unique_per_document(base: dict[str, Any]) -> None:
    """문서당 탄생 행(from NULL)은 하나 — 두 번째는 23505"""
    with engine.begin() as connection:
        po_id = _insert(connection, **base)
        _log(connection, po_id)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, po_id)
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_purchase_order_status_log_purchase_order_id_birth",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE purchase_order_status_log SET reason = 'x'",
        "DELETE FROM purchase_order_status_log",
        "TRUNCATE purchase_order_status_log",
    ],
)
def test_status_log_is_immutable_for_the_app_role(base: dict[str, Any], statement: str) -> None:
    """이력은 INSERT/SELECT만 — 앱 계정의 UPDATE/DELETE/TRUNCATE는 42501로 거부된다(§17.5)"""
    with engine.begin() as connection:
        po_id = _insert(connection, **base)
        _log(connection, po_id)
    with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
        connection.execute(text(statement))
    assert _state(caught.value)[0] == PERMISSION_DENIED
    with owner_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM purchase_order_status_log")).scalar_one()
            == 1
        )


def test_check_and_index_definitions_are_pinned() -> None:
    """CHECK·부분 인덱스 정의문이 DB에 실재한다(pg_get_constraintdef/indexdef — alembic check는 CHECK를 못 본다): 접두어·po_kind 값 집합·OC 결속 식·동결 완결성·라인 원가 식·복제 술어"""
    with owner_engine.connect() as connection:
        defs = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid IN ('purchase_orders'::regclass, 'purchase_order_lines'::regclass)"
                    " AND contype = 'c'"
                )
            ).all()
        )
        indexes = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes"
                    " WHERE tablename IN ('purchase_orders', 'purchase_order_lines')"
                )
            ).all()
        )
    assert re.search(r"PO-\[0-9\]\{4\}-\[0-9\]\{4,\}", defs["ck_purchase_orders_doc_number_format"])
    kind = defs["ck_purchase_orders_po_kind_valid"]
    assert "OEM_PRODUCTION" in kind and "PURCHASE" in kind and "SAMPLE" not in kind
    issued = defs["ck_purchase_orders_oc_empty_when_issued"]
    assert (
        "ISSUED" in issued
        and "oc_received_on IS NULL" in issued
        and "oc_reference IS NULL" in issued
    )
    required = defs["ck_purchase_orders_oc_required_after_confirm"]
    for state in ("SUPPLIER_CONFIRMED", "PARTIALLY_RECEIVED", "FULLY_RECEIVED", "CLOSED"):
        assert state in required
    assert "CANCELLED" not in required  # 취소는 OC 무제약(발행 중 바로 취소 가능)
    assert "frozen_at IS NULL" in defs["ck_purchase_orders_frozen_complete"]
    assert "supplier_name" in defs["ck_purchase_orders_frozen_complete"]
    assert "quantity" in defs["ck_purchase_order_lines_line_cost_matches"]
    assert "numeric" in defs["ck_purchase_order_lines_line_cost_matches"]
    assert "BUYER_PO" not in defs["ck_purchase_order_lines_price_basis_valid"]
    live = indexes["uq_purchase_orders_copied_from_id_live"]
    assert "CANCELLED" in live and "EXPIRED" in live
    assert "deleted_at IS NULL" in indexes["uq_purchase_order_lines_po_id_sku_id_active"]


def test_the_cost_columns_end_in_cost_so_log_masking_and_money_detection_apply() -> None:
    """원가 열 이름은 `_cost` 접미(로그 마스킹·금액 판정 자동 편입) — `_amount`·`price` 이름이 아니다(ADR-0057 ④)"""
    from app.core.logging.redaction import is_sensitive_key
    from app.core.money import is_money_column_name

    for name in ("unit_cost", "line_cost", "total_cost"):
        assert is_sensitive_key(name) and is_money_column_name(name), name
    with owner_engine.connect() as connection:
        money_like = {
            (r[0], r[1])
            for r in connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns"
                    " WHERE table_name IN ('purchase_orders', 'purchase_order_lines')"
                    " AND (column_name LIKE '%amount%' OR column_name LIKE '%price%')"
                )
            )
        }
    # `price_basis`(단가 기준 MASTER/MANUAL)는 금액이 아니라 가격 정보 — 응답에서 `price_*` 부재 규칙으로 함께 숨긴다(CostHidden)
    assert money_like == {("purchase_order_lines", "price_basis")}


def test_raw_po_factory_produces_a_row_that_satisfies_every_check() -> None:
    """공회전 방지 — 테스트 팩토리가 만드는 DB 직행 PO(상태별)가 전 CHECK를 통과한다(다른 테스트의 전제)"""
    for status in (
        "ISSUED",
        "SUPPLIER_CONFIRMED",
        "PARTIALLY_RECEIVED",
        "FULLY_RECEIVED",
        "CLOSED",
        "CANCELLED",
    ):
        assert raw_po(status) > 0
