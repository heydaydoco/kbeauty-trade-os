"""A·K. SO·SO 라인·SO 상태이력의 불변식을 DB가 강제한다 (S3-1 M05 / ADR-0051~0053 / design-A A2·B1·B2·B6).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 모든 위반 케이스는 같은
  조건의 **양성 대조 INSERT가 성공**함을 함께 확인한다(TRUNCATE 하네스 공회전 방지). 결제조건·Incoterms·환율·통화 CHECK는 QT·PI와 같은
  생성기라 대표 표본만 본다. 핵심은 SO 고유 규칙 — **중복 바이어 PO 0건(부분 유니크)**·**PI→SO 활성 1:1**·`confirmed_at` 결속·복합 FK다.
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
from tests.factories.trade import (
    create_buyer,
    raw_pi,
    raw_quotation,
    raw_so,
    unique,
)
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
    """유효한 직접(인테이크형) SO 한 줄의 기본값 — 접수 상태·확정 시각 없음·결제조건 없음."""
    buyer = create_buyer()
    owner = create_user(f"{unique('soc')}@example.com")
    from tests.support.factories import create_market

    create_market("US")
    return {
        "doc_number": "SO-2026-0001",
        "doc_date": date(2026, 9, 1),
        "status": "RECEIVED",
        "currency": "USD",
        "assignee_id": owner,
        "buyer_partner_id": buyer,
        "buyer_name": "Raw Buyer",
        "dest_market_code": "US",
        "qt_id": None,
        "pi_id": None,
        "buyer_po_no": None,
        "buyer_po_no_key": None,
        "confirmed_at": None,
        "payment_type": None,
        "advance_pct_bp": None,
        "balance_anchor": None,
        "balance_days": None,
        "incoterm_code": None,
        "incoterm_place": None,
        "incoterm_year": None,
        "fx_rate": None,
        "fx_rate_date": None,
    }


#: 동결(확정)에 필요한 서류 값 전부 — 확정 시각을 넣는 케이스가 덮어쓴다.
FROZEN_TERMS: dict[str, Any] = {
    "payment_type": "TT_ADVANCE",
    "advance_pct_bp": 3000,
    "balance_anchor": "ETD_DATE",
    "balance_days": -7,
    "incoterm_code": "FOB",
    "incoterm_place": "Busan",
    "incoterm_year": 2020,
    "fx_rate": 1350,
    "fx_rate_date": date(2026, 9, 1),
}

_COLUMNS = (
    "doc_number, doc_date, status, currency, assignee_id, buyer_partner_id, buyer_name,"
    " dest_market_code, qt_id, pi_id, buyer_po_no, buyer_po_no_key, confirmed_at, payment_type,"
    " advance_pct_bp, balance_anchor, balance_days, incoterm_code, incoterm_place, incoterm_year,"
    " fx_rate, fx_rate_date, copied_from_id, total_amount, last_line_no"
)


def _insert(connection: Connection, **values: Any) -> int:
    row = {"copied_from_id": None, "total_amount": 0, "last_line_no": 0, **values}
    names = [c.strip() for c in _COLUMNS.split(",")]
    return int(
        connection.execute(
            text(
                f"INSERT INTO sales_orders ({_COLUMNS}) VALUES ({', '.join(':' + n for n in names)})"
                " RETURNING id"
            ),
            row,
        ).scalar_one()
    )


_CHECK_CASES: list[tuple[str, dict[str, Any], str]] = [
    # 알 수 없는 상태는 status_valid와 confirmed_at 결속 CHECK가 **함께** 위반이라 DB가 먼저 보고하는(이름순) 결속 CHECK로 잡힌다 —
    # status_valid 자체의 값 집합은 test_doc_machines의 StrEnum↔machine↔DB 3자 대사가 고정한다.
    ("status_unknown", {"status": "CONVERTED"}, "ck_sales_orders_confirmed_at_consistent"),
    ("status_draft", {"status": "DRAFT"}, "ck_sales_orders_confirmed_at_consistent"),
    ("doc_number_prefix", {"doc_number": "QT-2026-0001"}, "ck_sales_orders_doc_number_format"),
    ("currency_lower", {"currency": "usd"}, "ck_sales_orders_currency_uppercase"),
    ("total_over_2_53", {"total_amount": 2**53}, "ck_sales_orders_total_range"),
    ("buyer_name_blank", {"buyer_name": " "}, "ck_sales_orders_buyer_name_not_blank"),
    ("fx_zero", {"fx_rate": 0, "fx_rate_date": date(2026, 9, 1)}, "ck_sales_orders_fx_rate_range"),
    ("fx_without_date", {"fx_rate": 1350}, "ck_sales_orders_fx_pair"),
    (
        "krw_rate_not_one",
        {"currency": "KRW", "fx_rate": 2, "fx_rate_date": date(2026, 9, 1)},
        "ck_sales_orders_krw_fx_is_one",
    ),
    (
        "fx_date_after_doc",
        {"fx_rate": 1350, "fx_rate_date": date(2026, 9, 2)},
        "ck_sales_orders_fx_date_not_future",
    ),
    (
        "partial_advance_without_anchor",
        {"payment_type": "TT_ADVANCE", "advance_pct_bp": 3000},
        "ck_sales_orders_payment_terms_shape",
    ),
    ("incoterm_partial", {"incoterm_code": "FOB"}, "ck_sales_orders_incoterm_all_or_none"),
    # SO 고유 — PO번호 원문·키는 한 쌍이고 공백일 수 없다
    ("po_key_without_no", {"buyer_po_no_key": "PO1"}, "ck_sales_orders_po_pair"),
    ("po_no_without_key", {"buyer_po_no": "PO1"}, "ck_sales_orders_po_pair"),
    (
        "po_blank",
        {"buyer_po_no": "  ", "buyer_po_no_key": "X"},
        "ck_sales_orders_po_not_blank",
    ),
    # confirmed_at 결속 — 접수는 NULL·확정 이후는 NOT NULL(확정 SO가 접수로 되돌아가는 위반을 DB가 막는다)
    (
        "received_with_confirmed_at",
        {"status": "RECEIVED", "confirmed_at": "2026-09-02T00:00:00+00:00"},
        "ck_sales_orders_confirmed_at_consistent",
    ),
    (
        "confirmed_without_confirmed_at",
        {"status": "CONFIRMED", **FROZEN_TERMS},
        "ck_sales_orders_confirmed_at_consistent",
    ),
    (
        "in_shipment_without_confirmed_at",
        {"status": "IN_SHIPMENT", **FROZEN_TERMS},
        "ck_sales_orders_confirmed_at_consistent",
    ),
    # 동결 완결성 — 확정 시각이 있으면 서류 필수 값이 모두 있어야 한다
    (
        "confirmed_without_terms",
        {"status": "CONFIRMED", "confirmed_at": "2026-09-02T00:00:00+00:00"},
        "ck_sales_orders_frozen_complete",
    ),
    (
        "confirmed_without_fx",
        {
            "status": "CONFIRMED",
            "confirmed_at": "2026-09-02T00:00:00+00:00",
            **{**FROZEN_TERMS, "fx_rate": None, "fx_rate_date": None},
        },
        "ck_sales_orders_frozen_complete",
    ),
]


def test_a_valid_received_so_inserts_and_confirmed_states_need_a_complete_freeze(
    base: dict[str, Any],
) -> None:
    """양성 대조 — 접수 SO 저장 · 확정 SO(확정 시각+완결 서류)·보류·취소는 확정 시각 유무와 무관하게 저장(ON_HOLD·CANCELLED는 무제약)"""
    with engine.begin() as connection:
        _insert(connection, **base)
        _insert(
            connection,
            **{
                **base,
                **FROZEN_TERMS,
                "doc_number": "SO-2026-0002",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-02T00:00:00+00:00",
            },
        )
        for number, status, confirmed in (
            ("SO-2026-0003", "ON_HOLD", None),
            ("SO-2026-0004", "ON_HOLD", "2026-09-02T00:00:00+00:00"),
            ("SO-2026-0005", "CANCELLED", None),
        ):
            values = {**base, "doc_number": number, "status": status, "confirmed_at": confirmed}
            if confirmed:
                values.update(FROZEN_TERMS)
            _insert(connection, **values)


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _CHECK_CASES, ids=[c[0] for c in _CHECK_CASES]
)
def test_check_constraints_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """서비스를 우회한 직접 INSERT가 각 CHECK에서 거부된다(23514·제약 이름 일치) — 같은 base는 양성 대조로 통과한다"""
    with engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "SO-2026-0009"})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, **override})
    state, violated = _state(caught.value)
    assert state == CHECK_VIOLATION and violated == constraint, (name, violated)


def test_a_confirmed_so_cannot_be_rewound_to_received(base: dict[str, Any]) -> None:
    """확정 SO를 RECEIVED로 되돌리는 UPDATE는 confirmed_at 결속 CHECK가 거부한다 — 되돌림 경로 자체가 DB에 없다(취소+신규뿐)"""
    with engine.begin() as connection:
        so_id = _insert(
            connection,
            **{
                **base,
                **FROZEN_TERMS,
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-02T00:00:00+00:00",
            },
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET status = 'RECEIVED' WHERE id = :i"), {"i": so_id}
        )
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_sales_orders_confirmed_at_consistent")
    with engine.begin() as connection:  # 양성 대조 — 보류로는 갈 수 있다
        connection.execute(
            text("UPDATE sales_orders SET status = 'ON_HOLD' WHERE id = :i"), {"i": so_id}
        )


@pytest.mark.parametrize(
    "missing",
    [
        "doc_number",
        "doc_date",
        "status",
        "currency",
        "buyer_partner_id",
        "buyer_name",
        "dest_market_code",
        "assignee_id",
    ],
)
def test_required_columns_are_not_null(base: dict[str, Any], missing: str) -> None:
    """필수 열은 NOT NULL — 거래처·통화·시장·담당자 없는 SO가 DB에서 불가능하다(qt_id·pi_id·PO번호는 NULL 허용: 직접 수주)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, missing: None})
    assert _state(caught.value)[0] == "23502"


def test_doc_number_is_globally_unique_even_after_soft_delete(base: dict[str, Any]) -> None:
    """doc_number는 전역 UNIQUE — soft delete 뒤에도 같은 번호를 다시 못 쓴다(재발급 금지 §17.3)"""
    with engine.begin() as connection:
        first = _insert(connection, **base)
        connection.execute(
            text("UPDATE sales_orders SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **base)
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_sales_orders_doc_number")


# ── 중복 바이어 PO 0건 ──────────────────────────────────────────────────────


def test_a_second_live_so_cannot_take_the_same_buyer_po_key(base: dict[str, Any]) -> None:
    """(바이어, 정규화 PO 키) 부분 유니크 — 접수·보류·확정 어느 상태든 살아 있는 비취소 SO가 있으면 두 번째는 23505"""
    with engine.begin() as connection:
        _insert(connection, **{**base, "buyer_po_no": "PO-1", "buyer_po_no_key": "PO-1"})
    for number, status in (("SO-2026-0002", "RECEIVED"), ("SO-2026-0003", "ON_HOLD")):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _insert(
                connection,
                **{
                    **base,
                    "doc_number": number,
                    "status": status,
                    "buyer_po_no": "po-1",
                    "buyer_po_no_key": "PO-1",
                },
            )
        assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_sales_orders_buyer_po_live"), status


def test_the_po_key_is_released_by_cancel_and_by_soft_delete_and_is_per_buyer(
    base: dict[str, Any],
) -> None:
    """취소 SO·삭제된 SO는 PO번호를 점유하지 않는다(정정=취소+신규) · 다른 바이어는 같은 키를 쓴다 · 키 NULL은 몇 건이든 · 취소로 옮기는 UPDATE도 같은 규칙"""
    po = {"buyer_po_no": "PO-1", "buyer_po_no_key": "PO-1"}
    with engine.begin() as connection:
        first = _insert(connection, **{**base, **po})
        connection.execute(
            text("UPDATE sales_orders SET status = 'CANCELLED' WHERE id = :i"), {"i": first}
        )
        second = _insert(connection, **{**base, **po, "doc_number": "SO-2026-0002"})
        connection.execute(
            text("UPDATE sales_orders SET deleted_at = now() WHERE id = :i"), {"i": second}
        )
        _insert(connection, **{**base, **po, "doc_number": "SO-2026-0003"})
        other = create_buyer()
        _insert(
            connection, **{**base, **po, "doc_number": "SO-2026-0004", "buyer_partner_id": other}
        )
        for n in range(5, 8):
            _insert(connection, **{**base, "doc_number": f"SO-2026-000{n}"})  # 키 NULL 여러 건
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        # 취소된 SO는 되살릴 수 없다는 뜻은 아니지만, 살아 있는 점유자가 있으면 취소→접수 UPDATE는 유니크가 막는다
        connection.execute(
            text("UPDATE sales_orders SET status = 'RECEIVED' WHERE id = :i"), {"i": first}
        )
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_sales_orders_buyer_po_live")


# ── PI → SO 활성 1:1 · 복합 FK ──────────────────────────────────────────────


@pytest.fixture
def chain() -> dict[str, Any]:
    """원천 QT·PI(DB 직행)와 그 통화·바이어를 물려받은 SO 기본값."""
    buyer = create_buyer()
    qt_id = raw_quotation("ISSUED", buyer_partner_id=buyer)
    pi_id = raw_pi(qt_id)
    return {"buyer": buyer, "qt_id": qt_id, "pi_id": pi_id}


def _from_pi(base: dict[str, Any], chain: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        **base,
        "buyer_partner_id": chain["buyer"],
        "qt_id": chain["qt_id"],
        "pi_id": chain["pi_id"],
        **extra,
    }


def test_one_live_sales_order_per_pi_and_cancel_frees_it(
    base: dict[str, Any], chain: dict[str, Any]
) -> None:
    """PI→SO 활성 1:1 부분 유니크 — 두 번째 살아 있는 SO는 23505, 첫 SO를 취소하면 재생성 허용·삭제된 SO도 점유하지 않는다"""
    with engine.begin() as connection:
        first = _insert(connection, **_from_pi(base, chain))
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **_from_pi(base, chain, doc_number="SO-2026-0002"))
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_sales_orders_pi_id_live")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET status = 'CANCELLED' WHERE id = :i"), {"i": first}
        )
        second = _insert(connection, **_from_pi(base, chain, doc_number="SO-2026-0002"))
        connection.execute(
            text("UPDATE sales_orders SET deleted_at = now() WHERE id = :i"), {"i": second}
        )
        _insert(connection, **_from_pi(base, chain, doc_number="SO-2026-0003"))


def test_qt_direct_sos_are_many_to_one_and_pi_needs_its_own_qt(
    base: dict[str, Any], chain: dict[str, Any]
) -> None:
    """QT→SO 직접은 1:N(같은 QT에 여러 SO) · PI만 있고 QT가 없는 SO는 CHECK가 거부(복합 FK의 MATCH SIMPLE 공백 보강)"""
    with engine.begin() as connection:
        for n in (1, 2, 3):
            _insert(
                connection,
                **{
                    **base,
                    "buyer_partner_id": chain["buyer"],
                    "qt_id": chain["qt_id"],
                    "doc_number": f"SO-2026-000{n}",
                },
            )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(
            connection,
            **{
                **base,
                "buyer_partner_id": chain["buyer"],
                "pi_id": chain["pi_id"],
                "doc_number": "SO-2026-0009",
            },
        )
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_sales_orders_pi_requires_qt")


def test_composite_fk_forces_the_sos_qt_to_be_the_pis_qt(
    base: dict[str, Any], chain: dict[str, Any]
) -> None:
    """복합 FK (pi_id, qt_id)→PI(id, qt_id) — PI의 QT와 다른 qt_id를 단 SO는 23503(양성 대조: 같은 QT는 통과)"""
    other_qt = raw_quotation("ISSUED", buyer_partner_id=chain["buyer"])
    with engine.begin() as connection:
        _insert(connection, **_from_pi(base, chain, status="CANCELLED"))  # 양성 대조(같은 QT)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(
            connection,
            **_from_pi(base, chain, doc_number="SO-2026-0002", qt_id=other_qt),
        )
    assert _state(caught.value) == (
        FK_VIOLATION,
        "fk_sales_orders_pi_id_qt_id_proforma_invoices",
    )


def test_unknown_source_market_and_partner_are_rejected_by_fk(base: dict[str, Any]) -> None:
    """FK — 없는 견적·시장·거래처는 DB가 막는다"""
    for override in ({"qt_id": 999_999}, {"dest_market_code": "ZZ"}, {"buyer_partner_id": 999_999}):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _insert(connection, **{**base, **override})
        assert _state(caught.value)[0] == FK_VIOLATION, override


def test_only_one_live_copy_per_source_and_no_self_copy(base: dict[str, Any]) -> None:
    """복제본은 원본당 살아 있는 것 하나(취소된 복제본은 제외) · 자기 자신을 원본으로 못 가리킨다"""
    with engine.begin() as connection:
        source = _insert(connection, **{**base, "status": "CANCELLED"})
        _insert(connection, **{**base, "doc_number": "SO-2026-0002", "copied_from_id": source})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "SO-2026-0003", "copied_from_id": source})
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_sales_orders_copied_from_id_live")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET status = 'CANCELLED' WHERE copied_from_id = :s"),
            {"s": source},
        )
        _insert(connection, **{**base, "doc_number": "SO-2026-0004", "copied_from_id": source})
    with pytest.raises(IntegrityError) as selfref, engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET copied_from_id = id WHERE id = :i"), {"i": source}
        )
    assert _state(selfref.value) == (CHECK_VIOLATION, "ck_sales_orders_copied_from_not_self")


def test_composite_key_targets_exist_for_the_lines(base: dict[str, Any]) -> None:
    """UNIQUE(id, currency)가 있다 — 라인 복합 FK(통화 일치)의 대상이다"""
    with owner_engine.connect() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'sales_orders'::regclass AND contype = 'u'"
                )
            ).scalars()
        )
    assert {"uq_sales_orders_doc_number", "uq_sales_orders_id_currency"} <= names


# ── 라인 ────────────────────────────────────────────────────────────────────

_LINE_COLUMNS = (
    "so_id, currency, line_no, sku_id, sku_code, sku_name_ko, sku_kind, quantity,"
    " unit_price_amount, list_price_amount, line_amount, price_basis, is_free, price_reason,"
    " qt_line_id, pi_line_id"
)


def _insert_line(connection: Connection, **values: Any) -> int:
    row = {
        "list_price_amount": None,
        "price_reason": None,
        "qt_line_id": None,
        "pi_line_id": None,
        **values,
    }
    names = [c.strip() for c in _LINE_COLUMNS.split(",")]
    return int(
        connection.execute(
            text(
                f"INSERT INTO sales_order_lines ({_LINE_COLUMNS}) VALUES"
                f" ({', '.join(':' + n for n in names)}) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


@pytest.fixture
def line_base(base: dict[str, Any]) -> dict[str, Any]:
    """유효한 SO 라인 한 줄 — 직접 수주(원천 없음)."""
    sku_id = create_sku(unique("SKU-SO"))
    with engine.begin() as connection:
        so_id = _insert(connection, **base)
    return {
        "so_id": so_id,
        "currency": "USD",
        "line_no": 1,
        "sku_id": sku_id,
        "sku_code": "X",
        "sku_name_ko": "테스트",
        "sku_kind": "SINGLE",
        "quantity": 10,
        "unit_price_amount": 250,
        "line_amount": 2500,
        "price_basis": "BUYER_PO",
        "is_free": False,
    }


_LINE_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("amount_mismatch", {"line_amount": 2501}, "ck_sales_order_lines_line_amount_matches"),
    ("quantity_zero", {"quantity": 0, "line_amount": 0}, "ck_sales_order_lines_quantity_range"),
    ("currency_lower", {"currency": "usd"}, "ck_sales_order_lines_currency_uppercase"),
    (
        "free_flag_with_price",
        {"is_free": True, "price_reason": "샘플"},
        "ck_sales_order_lines_free_iff_zero_price",
    ),
    (
        "free_from_master",
        {
            "unit_price_amount": 0,
            "line_amount": 0,
            "is_free": True,
            "price_reason": "샘플",
            "price_basis": "MASTER",
        },
        "ck_sales_order_lines_free_not_from_master",
    ),
    ("price_basis_unknown", {"price_basis": "AUTO"}, "ck_sales_order_lines_price_basis_valid"),
]


def test_a_valid_line_inserts_with_buyer_po_price_basis(line_base: dict[str, Any]) -> None:
    """양성 대조 — 유효 라인 저장(직접 수주 라인은 바이어 PO 단가 BUYER_PO 허용 · 요청납기 열 없이도)"""
    with engine.begin() as connection:
        _insert_line(connection, **line_base)


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LINE_CASES, ids=[c[0] for c in _LINE_CASES]
)
def test_line_checks_reject_violations(
    line_base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """SO 라인 CHECK — 금액=수량×단가·수량 범위·무상 양방향·기준 값이 DB에서 거부된다(QT·PI 라인과 같은 생성기)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, **override})
    state, violated = _state(caught.value)
    assert state == CHECK_VIOLATION and violated == constraint, (name, violated)


def test_a_line_points_at_one_source_only_and_at_a_real_line(line_base: dict[str, Any]) -> None:
    """출처 라인은 하나만(`num_nonnulls <= 1` — 조상 이중 기록 차단) · 없는 원천 라인은 FK 위반 · 한쪽만 채운 라인은 통과"""
    qt = raw_quotation("ISSUED")
    sku = create_sku(unique("SKU-SOQ"))
    with engine.begin() as connection:
        qt_line = int(
            connection.execute(
                text(
                    "INSERT INTO quotation_lines (qt_id, currency, line_no, sku_id, sku_code, sku_name_ko,"
                    " sku_kind, quantity, unit_price_amount, line_amount, price_basis, is_free)"
                    " VALUES (:q, 'USD', 1, :s, 'X', 'X', 'SINGLE', 10, 250, 2500, 'MASTER', false)"
                    " RETURNING id"
                ),
                {"q": qt, "s": sku},
            ).scalar_one()
        )
        pi_line = int(
            connection.execute(
                text(
                    "INSERT INTO proforma_invoice_lines (pi_id, qt_id, qt_line_id, currency, line_no,"
                    " sku_id, sku_code, sku_name_ko, sku_kind, quantity, unit_price_amount, line_amount,"
                    " price_basis, is_free) SELECT :p, :q, :ql, 'USD', 1, :s, 'X', 'X', 'SINGLE', 10,"
                    " 250, 2500, 'MASTER', false RETURNING id"
                ),
                {"p": raw_pi(qt), "q": qt, "ql": qt_line, "s": sku},
            ).scalar_one()
        )
        _insert_line(connection, **{**line_base, "qt_line_id": qt_line})  # 양성 대조
    with pytest.raises(IntegrityError) as both, engine.begin() as connection:
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 2,
                "sku_id": create_sku(unique("S2")),
                "qt_line_id": qt_line,
                "pi_line_id": pi_line,
            },
        )
    assert _state(both.value) == (CHECK_VIOLATION, "ck_sales_order_lines_one_source_only")
    with pytest.raises(IntegrityError) as missing, engine.begin() as connection:
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 3,
                "sku_id": create_sku(unique("S3")),
                "pi_line_id": 999_999,
            },
        )
    assert _state(missing.value)[0] == FK_VIOLATION
    with engine.begin() as connection:
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 4,
                "sku_id": create_sku(unique("S4")),
                "pi_line_id": pi_line,
            },
        )


def test_line_currency_must_match_the_header_and_header_currency_is_locked_by_lines(
    line_base: dict[str, Any],
) -> None:
    """복합 FK (so_id, currency) — 헤더와 다른 통화 라인은 거부 · 라인이 있으면 헤더 통화 변경도 거부(혼합 통화 불가능)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2, "currency": "EUR"})
    assert _state(caught.value) == (
        FK_VIOLATION,
        "fk_sales_order_lines_so_id_currency_sales_orders",
    )
    with engine.begin() as connection:
        _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as change, engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_orders SET currency = 'EUR' WHERE id = :i"),
            {"i": line_base["so_id"]},
        )
    assert _state(change.value)[0] == FK_VIOLATION


def test_line_uniqueness_is_active_only(line_base: dict[str, Any]) -> None:
    """(so, line_no)·(so, sku, is_free) 활성 유니크 — soft delete 뒤 재추가는 허용된다"""
    with engine.begin() as connection:
        first = _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as dup, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2})
    assert _state(dup.value) == (
        UNIQUE_VIOLATION,
        "uq_sales_order_lines_so_id_sku_id_is_free_active",
    )
    with pytest.raises(IntegrityError) as dup_no, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "sku_id": create_sku(unique("S5"))})
    assert _state(dup_no.value) == (UNIQUE_VIOLATION, "uq_sales_order_lines_so_id_line_no_active")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_order_lines SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        _insert_line(connection, **{**line_base, "line_no": 2})


# ── 상태이력 ────────────────────────────────────────────────────────────────


def _log(connection: Connection, so_id: int, **values: Any) -> None:
    row = {
        "from_status": None,
        "to_status": "RECEIVED",
        "reason": None,
        "actor_user_id": None,
        "automatic": True,
        **values,
    }
    connection.execute(
        text(
            "INSERT INTO sales_order_status_log (sales_order_id, from_status, to_status, reason,"
            " actor_user_id, automatic) VALUES (:p, :from_status, :to_status, :reason, :actor_user_id,"
            " :automatic)"
        ),
        {"p": so_id, **row},
    )


_LOG_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "to_draft",
        {"from_status": "RECEIVED", "to_status": "DRAFT"},
        "ck_sales_order_status_log_to_status_valid",
    ),
    (
        "self_transition",
        {"from_status": "CONFIRMED", "to_status": "CONFIRMED"},
        "ck_sales_order_status_log_no_self_transition",
    ),
    (
        "birth_not_received",
        {"from_status": None, "to_status": "CONFIRMED"},
        "ck_sales_order_status_log_birth_row",
    ),
    (
        "cancel_without_reason",
        {"from_status": "RECEIVED", "to_status": "CANCELLED"},
        "ck_sales_order_status_log_reason_required",
    ),
    (
        "hold_without_reason",
        {"from_status": "RECEIVED", "to_status": "ON_HOLD"},
        "ck_sales_order_status_log_reason_required",
    ),
    (
        "no_actor_not_automatic",
        {"from_status": "RECEIVED", "to_status": "CONFIRMED", "automatic": False},
        "ck_sales_order_status_log_actor_or_automatic",
    ),
]


def test_status_log_accepts_confirm_resume_and_reasoned_hold(base: dict[str, Any]) -> None:
    """양성 대조 — 탄생·확정·재개는 사유 없이, 보류·취소는 사유와 함께 저장된다"""
    with engine.begin() as connection:
        so_id = _insert(connection, **base)
        _log(connection, so_id)
        _log(connection, so_id, from_status="RECEIVED", to_status="CONFIRMED")
        _log(connection, so_id, from_status="CONFIRMED", to_status="ON_HOLD", reason="대기")
        _log(connection, so_id, from_status="ON_HOLD", to_status="CONFIRMED")
        _log(connection, so_id, from_status="CONFIRMED", to_status="CANCELLED", reason="정정")


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LOG_CASES, ids=[c[0] for c in _LOG_CASES]
)
def test_status_log_checks_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """SO 상태이력 CHECK — 값·자기전이·탄생 행(RECEIVED)·사유 필수(취소·보류)·행위자 또는 자동이 DB에서 거부된다"""
    with engine.begin() as connection:
        so_id = _insert(connection, **base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, so_id, **{"automatic": True, **override})
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), name


def test_the_birth_row_is_unique_per_document(base: dict[str, Any]) -> None:
    """문서당 탄생 행(from NULL)은 하나 — 두 번째는 23505"""
    with engine.begin() as connection:
        so_id = _insert(connection, **base)
        _log(connection, so_id)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, so_id)
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_sales_order_status_log_sales_order_id_birth",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sales_order_status_log SET reason = 'x'",
        "DELETE FROM sales_order_status_log",
        "TRUNCATE sales_order_status_log",
    ],
)
def test_status_log_is_immutable_for_the_app_role(base: dict[str, Any], statement: str) -> None:
    """이력은 INSERT/SELECT만 — 앱 계정의 UPDATE/DELETE/TRUNCATE는 42501로 거부된다(§17.5)"""
    with engine.begin() as connection:
        so_id = _insert(connection, **base)
        _log(connection, so_id)
    with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
        connection.execute(text(statement))
    assert _state(caught.value)[0] == PERMISSION_DENIED
    with owner_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM sales_order_status_log")).scalar_one()
            == 1
        )


def test_check_and_index_definitions_are_pinned() -> None:
    """CHECK·부분 인덱스 정의문이 DB에 실재한다(pg_get_constraintdef/indexdef — alembic check는 CHECK를 못 본다): 취소 제외 술어·삭제 술어·결속 식"""
    with owner_engine.connect() as connection:
        defs = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'sales_orders'::regclass AND contype = 'c'"
                )
            ).all()
        )
        indexes = dict(
            connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'sales_orders'")
            ).all()
        )
    assert re.search(r"SO-\[0-9\]\{4\}-\[0-9\]\{4,\}", defs["ck_sales_orders_doc_number_format"])
    po = indexes["uq_sales_orders_buyer_po_live"]
    assert "UNIQUE" in po and "(buyer_partner_id, buyer_po_no_key)" in po
    assert "deleted_at IS NULL" in po and "buyer_po_no_key IS NOT NULL" in po
    assert "status)::text <> 'CANCELLED'" in po or "status <> 'CANCELLED'" in po
    pi = indexes["uq_sales_orders_pi_id_live"]
    assert "UNIQUE" in pi and "(pi_id)" in pi and "pi_id IS NOT NULL" in pi and "CANCELLED" in pi
    live = indexes["uq_sales_orders_copied_from_id_live"]
    assert "CANCELLED" in live and "EXPIRED" in live
    consistent = defs["ck_sales_orders_confirmed_at_consistent"]
    assert "RECEIVED" in consistent and "confirmed_at IS NULL" in consistent
    assert "IN_SHIPMENT" in consistent and "ON_HOLD" in consistent
    assert "confirmed_at IS NULL" in defs["ck_sales_orders_frozen_complete"]
    assert "num_nonnulls" in _line_check_defs()["ck_sales_order_lines_one_source_only"]


def _line_check_defs() -> dict[str, str]:
    with owner_engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'sales_order_lines'::regclass AND contype = 'c'"
                )
            ).all()
        )


def test_raw_so_factory_produces_a_row_that_satisfies_every_check() -> None:
    """공회전 방지 — 테스트 팩토리가 만드는 DB 직행 SO(상태별·PI 경유·QT 직접)가 전 CHECK를 통과한다(다른 테스트의 전제)"""
    for status in (
        "RECEIVED",
        "CONFIRMED",
        "ON_HOLD",
        "CANCELLED",
        "PARTIALLY_ALLOCATED",
        "ALLOCATED",
        "IN_SHIPMENT",
        "COMPLETED",
    ):
        assert raw_so(status) > 0
    qt = raw_quotation("ISSUED", buyer_partner_id=create_buyer())
    assert raw_so("RECEIVED", qt_id=qt) > 0
    assert raw_so("RECEIVED", pi_id=raw_pi(qt)) > 0
