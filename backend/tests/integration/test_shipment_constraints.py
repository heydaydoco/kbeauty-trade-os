"""A·J·K. 선적 4표(M14)의 불변식을 DB가 강제한다 (S3-2 PR-3a / ADR-0074 / design-integrated §2.1 (a)~(d)·§9 R-04 / design-A A2·A3·A5·A9).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 위반 케이스는 같은 조건의 **양성 대조**가
  통과함을 함께 확인한다(공회전 방지). 핵심: **원천 FK 정확히 하나 + 수출·수입만**(채널입고·샘플무상 행 DB 거부)·수입 금액 0·동결 정합·
  라인 numeric 곱(R-04)·무상 규약 승계·당사자 역할·자동 스냅샷 역할·상태이력 IMMUTABLE(권한 회수).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.session import engine, owner_engine
from app.core.time import today_kst
from tests.factories.shipments import confirmed_so, raw_shipment, rows
from tests.factories.trade import create_buyer, raw_po
from tests.support.factories import create_user

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
def so() -> dict[str, Any]:
    return confirmed_so((10, 4))


def _header(so: dict[str, Any], **override: Any) -> dict[str, Any]:
    row = rows("SELECT * FROM sales_orders WHERE id = :i", i=so["id"])[0]
    values = {
        "doc_number": "SH-2026-0001",
        "doc_date": today_kst(),
        "status": "PLANNED",
        "currency": row["currency"],
        "fx_rate": row["fx_rate"],
        "fx_rate_date": row["fx_rate_date"],
        "payment_type": row["payment_type"],
        "advance_pct_bp": row["advance_pct_bp"],
        "balance_anchor": row["balance_anchor"],
        "balance_days": row["balance_days"],
        "incoterm_code": row["incoterm_code"],
        "incoterm_place": row["incoterm_place"],
        "incoterm_year": row["incoterm_year"],
        "assignee_id": row["assignee_id"],
        "shipment_kind": "EXPORT",
        "so_id": so["id"],
        "po_id": None,
        "counterparty_partner_id": row["buyer_partner_id"],
        "counterparty_name": row["buyer_name"],
        "origin_country_code": "KR",
        "dest_country_code": "US",
        "frozen_at": None,
        "total_amount": 0,
        "copied_from_id": None,
    }
    values.update(override)
    return values


def _insert(connection: Connection, values: dict[str, Any]) -> int:
    columns = list(values)
    return int(
        connection.execute(
            text(
                f"INSERT INTO shipments ({', '.join(columns)}) VALUES"
                f" ({', '.join(':' + c for c in columns)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


_HEADER_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("status_unknown", {"status": "DRAFT"}, "ck_shipments_status_valid"),
    ("status_so_value", {"status": "CONFIRMED"}, "ck_shipments_status_valid"),
    ("doc_number_prefix", {"doc_number": "SO-2026-0001"}, "ck_shipments_doc_number_format"),
    # 모르는 구분은 kind_valid·kind_source를 함께 깨고 DB는 이름순으로 kind_source를 먼저 보고한다(kind_valid 정의문은 아래 핀이 고정)
    ("kind_unknown", {"shipment_kind": "TRANSIT"}, "ck_shipments_kind_source"),
    # 채널입고·샘플무상은 값으로는 존재하지만 행은 DB가 거부한다(생성 경로 미개방 — ADR-0074)
    ("channel_inbound_closed", {"shipment_kind": "CHANNEL_INBOUND"}, "ck_shipments_kind_source"),
    ("sample_free_closed", {"shipment_kind": "SAMPLE_FREE"}, "ck_shipments_kind_source"),
    ("export_without_so", {"so_id": None}, "ck_shipments_kind_source"),
    ("import_with_so", {"shipment_kind": "IMPORT"}, "ck_shipments_kind_source"),
    ("country_lower", {"dest_country_code": "us"}, "ck_shipments_country_format"),
    ("country_digits", {"origin_country_code": "K1"}, "ck_shipments_country_format"),
    ("counterparty_blank", {"counterparty_name": "  "}, "ck_shipments_counterparty_name_not_blank"),
    (
        "terms_missing",
        {
            "payment_type": None,
            "advance_pct_bp": None,
            "balance_anchor": None,
            "balance_days": None,
        },
        "ck_shipments_source_terms_complete",
    ),
    (
        "incoterm_missing",
        {"incoterm_code": None, "incoterm_place": None, "incoterm_year": None},
        "ck_shipments_source_terms_complete",
    ),
    (
        "planned_but_frozen",
        {"frozen_at": "2026-09-02T00:00:00+00:00"},
        "ck_shipments_planned_not_frozen",
    ),
    ("released_not_frozen", {"status": "RELEASE_ORDERED"}, "ck_shipments_released_frozen"),
    ("reserved_not_frozen", {"status": "SHIPPED"}, "ck_shipments_released_frozen"),
    ("total_negative", {"total_amount": -1}, "ck_shipments_total_range"),
    ("total_over_2_53", {"total_amount": 2**53}, "ck_shipments_total_range"),
]


def test_valid_shipments_insert_in_every_live_shape(so: dict[str, Any]) -> None:
    """양성 대조 — 계획·출고지시(동결)·계획에서 취소(미동결)·출고지시 뒤 취소(동결)·후반 예약 값(동결)이 전부 저장된다"""
    with engine.begin() as connection:
        _insert(connection, _header(so))
        frozen = "2026-09-02T00:00:00+00:00"
        for n, (status, frozen_at) in enumerate(
            [
                ("RELEASE_ORDERED", frozen),
                ("CANCELLED", None),
                ("CANCELLED", frozen),
                ("PICKING", frozen),
                ("CLOSED", frozen),
            ],
            start=2,
        ):
            _insert(
                connection,
                _header(so, doc_number=f"SH-2026-000{n}", status=status, frozen_at=frozen_at),
            )


def test_an_import_shipment_is_storable_only_without_amount(so: dict[str, Any]) -> None:
    """수입선적 = PO 원천 + 합계 0(PO 원가 비복사 — 금액 축 없음). 합계가 있으면 import_has_no_amount가 거부한다"""
    po = raw_po("ISSUED")
    base = _header(so, doc_number="SH-2026-0101", shipment_kind="IMPORT", so_id=None, po_id=po)
    with engine.begin() as connection:
        _insert(connection, base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, {**base, "doc_number": "SH-2026-0102", "total_amount": 100})
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_shipments_import_has_no_amount")


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _HEADER_CASES, ids=[c[0] for c in _HEADER_CASES]
)
def test_header_checks_reject_violations(
    so: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """서비스를 우회한 직접 INSERT가 각 CHECK에서 거부된다(23514·제약 이름 일치) — 같은 base는 양성 대조로 통과한다"""
    with engine.begin() as connection:
        _insert(connection, _header(so, doc_number="SH-2026-0900"))
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, _header(so, **override))
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), name


def test_copy_lineage_is_always_empty(so: dict[str, Any]) -> None:
    """선적은 복제 경로가 없다 — copied_from_id를 채우는 UPDATE는 CHECK가 거부한다"""
    shipment = raw_shipment(so["id"])
    other = raw_shipment(so["id"])
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text("UPDATE shipments SET copied_from_id = :o WHERE id = :i"),
            {"o": other, "i": shipment},
        )
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_shipments_no_copy_lineage")


def test_doc_number_is_globally_unique_even_after_soft_delete(so: dict[str, Any]) -> None:
    """doc_number 전역 UNIQUE — soft delete 뒤에도 같은 번호를 다시 못 쓴다(재발급 금지 §17.3)"""
    with engine.begin() as connection:
        first = _insert(connection, _header(so))
        connection.execute(
            text("UPDATE shipments SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, _header(so))
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_shipments_doc_number")


def test_source_and_master_foreign_keys_are_restrict(so: dict[str, Any]) -> None:
    """원천 SO·거래 상대·담당자를 지우는 DELETE는 FK RESTRICT가 거부한다"""
    raw_shipment(so["id"])
    for sql, value in (
        ("DELETE FROM sales_orders WHERE id = :i", so["id"]),
        ("DELETE FROM partners WHERE id = :i", so["buyer"]),
        ("DELETE FROM users WHERE id = :i", so["assignee_id"]),
    ):
        with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
            connection.execute(text(sql), {"i": value})
        assert _state(caught.value)[0] == FK_VIOLATION, sql


# ── 라인 ────────────────────────────────────────────────────────────────────


def _line(connection: Connection, **values: Any) -> int:
    columns = list(values)
    return int(
        connection.execute(
            text(
                f"INSERT INTO shipment_lines ({', '.join(columns)}) VALUES"
                f" ({', '.join(':' + c for c in columns)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


@pytest.fixture
def line_base(so: dict[str, Any]) -> dict[str, Any]:
    shipment = raw_shipment(so["id"])
    src = rows("SELECT * FROM sales_order_lines WHERE id = :i", i=so["line_ids"][0])[0]
    return {
        "shipment_id": shipment,
        "line_no": 1,
        "so_line_id": src["id"],
        "po_line_id": None,
        "sku_id": src["sku_id"],
        "sku_code": src["sku_code"],
        "sku_name_ko": src["sku_name_ko"],
        "sku_kind": src["sku_kind"],
        "currency": src["currency"],
        "quantity": 3,
        "unit_price_amount": src["unit_price_amount"],
        "is_free": False,
        "line_amount": 3 * src["unit_price_amount"],
    }


_LINE_CASES: list[tuple[str, dict[str, Any], frozenset[str]]] = [
    ("no_source", {"so_line_id": None}, frozenset({"ck_shipment_lines_one_source"})),
    (
        "quantity_zero",
        {"quantity": 0, "line_amount": 0},
        frozenset({"ck_shipment_lines_quantity_range"}),
    ),
    (
        "quantity_over_max",
        {"quantity": 100_000_000, "line_amount": 100_000_000_000},
        frozenset({"ck_shipment_lines_quantity_range"}),
    ),
    ("amount_mismatch", {"line_amount": 1}, frozenset({"ck_shipment_lines_export_priced"})),
    (
        "export_without_price",
        {"unit_price_amount": None, "line_amount": 0},
        frozenset({"ck_shipment_lines_export_priced"}),
    ),
    # R-04 — bigint 곱이 아니라 numeric 곱이라 2^53 넘는 곱이 500이 아니라 CHECK로 거부된다
    (
        "amount_overflow",
        {"quantity": 99_999_999, "unit_price_amount": 9_007_199_254_740_991, "line_amount": 1},
        frozenset({"ck_shipment_lines_export_priced"}),
    ),
    (
        "free_flag_with_price",
        {"is_free": True},
        frozenset({"ck_shipment_lines_export_free_iff_zero_price"}),
    ),
    (
        "zero_price_not_free",
        {"unit_price_amount": 0, "line_amount": 0},
        frozenset({"ck_shipment_lines_export_free_iff_zero_price"}),
    ),
    ("line_no_zero", {"line_no": 0}, frozenset({"ck_shipment_lines_line_no_positive"})),
    ("currency_lower", {"currency": "usd"}, frozenset({"ck_shipment_lines_currency_uppercase"})),
    ("sku_kind_unknown", {"sku_kind": "BOX"}, frozenset({"ck_shipment_lines_sku_kind_valid"})),
    (
        "price_negative",
        {"unit_price_amount": -1, "line_amount": -3},
        frozenset({"ck_shipment_lines_amount_range", "ck_shipment_lines_unit_price_range"}),
    ),
]


def test_valid_lines_insert(line_base: dict[str, Any], so: dict[str, Any]) -> None:
    """양성 대조 — 유상 수출 라인·다른 원천 라인의 2번째 줄이 저장된다"""
    with engine.begin() as connection:
        _line(connection, **line_base)
        src = rows("SELECT * FROM sales_order_lines WHERE id = :i", i=so["line_ids"][1])[0]
        _line(
            connection,
            **{
                **line_base,
                "line_no": 2,
                "so_line_id": src["id"],
                "sku_id": src["sku_id"],
                "quantity": 1,
                "unit_price_amount": src["unit_price_amount"],
                "line_amount": src["unit_price_amount"],
            },
        )


@pytest.mark.parametrize(
    ("name", "override", "allowed"), _LINE_CASES, ids=[c[0] for c in _LINE_CASES]
)
def test_line_checks_reject_violations(
    line_base: dict[str, Any], name: str, override: dict[str, Any], allowed: frozenset[str]
) -> None:
    """라인 CHECK 위반은 23514 — 한 위반이 여러 CHECK를 깨면 DB가 먼저 보고한 것이 허용 집합에 있으면 된다"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _line(connection, **{**line_base, **override})
    state, violated = _state(caught.value)
    assert state == CHECK_VIOLATION and violated in allowed, (name, violated)


def test_a_free_export_line_copies_the_zero_price(so: dict[str, Any]) -> None:
    """무상 규약 승계 — 단가 0·무상 표식이 함께면 저장된다(is_free ⇔ 단가 0)"""
    free_so = confirmed_so((5, 2), free_line=True)
    shipment = raw_shipment(free_so["id"])
    src = rows("SELECT * FROM sales_order_lines WHERE id = :i", i=free_so["line_ids"][1])[0]
    assert src["is_free"] is True
    with engine.begin() as connection:
        _line(
            connection,
            shipment_id=shipment,
            line_no=1,
            so_line_id=src["id"],
            sku_id=src["sku_id"],
            sku_code=src["sku_code"],
            sku_name_ko=src["sku_name_ko"],
            sku_kind=src["sku_kind"],
            currency=src["currency"],
            quantity=2,
            unit_price_amount=0,
            is_free=True,
            line_amount=0,
        )


def test_import_lines_carry_no_price(so: dict[str, Any]) -> None:
    """수입 라인(po_line_id) — 단가·금액·무상 표식이 있으면 import_no_price가 거부한다(원가 비복사)"""
    po = raw_po("ISSUED")
    shipment = raw_shipment(
        so["id"]
    )  # 헤더 구분과의 대응은 서비스 계약 — 여기서는 라인 CHECK만 본다
    with owner_engine.begin() as connection:
        sku, currency = connection.execute(
            text("SELECT sku_id, currency FROM sales_order_lines WHERE id = :i"),
            {"i": so["line_ids"][0]},
        ).one()
        po_line = int(
            connection.execute(
                text(
                    "INSERT INTO purchase_order_lines (po_id, currency, line_no, sku_id, sku_code,"
                    " sku_name_ko, sku_kind, quantity, unit_cost, line_cost, price_basis)"
                    " VALUES (:p, :c, 1, :s, 'S', '품', 'SINGLE', 10, 5, 50, 'MANUAL') RETURNING id"
                ),
                {"p": po, "c": currency, "s": sku},
            ).scalar_one()
        )
    base = {
        "shipment_id": shipment,
        "line_no": 1,
        "so_line_id": None,
        "po_line_id": po_line,
        "sku_id": sku,
        "sku_code": "S",
        "sku_name_ko": "품",
        "sku_kind": "SINGLE",
        "currency": currency,
        "quantity": 3,
        "unit_price_amount": None,
        "is_free": False,
        "line_amount": 0,
    }
    with engine.begin() as connection:
        _line(connection, **base)
    for override in (
        {"unit_price_amount": 5, "line_amount": 15},
        {"line_amount": 1},
        {"is_free": True},
    ):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _line(connection, **{**base, "line_no": 2, **override})
        assert _state(caught.value) == (CHECK_VIOLATION, "ck_shipment_lines_import_no_price")


def test_line_currency_is_bound_to_the_header(line_base: dict[str, Any]) -> None:
    """라인 통화 ≠ 헤더 통화는 복합 FK가 거부한다(혼합 통화 불가능)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _line(connection, **{**line_base, "currency": "EUR"})
    assert _state(caught.value) == (
        FK_VIOLATION,
        "fk_shipment_lines_shipment_id_currency_shipments",
    )


def test_same_source_line_is_one_row_per_shipment_and_reenters_as_new(
    line_base: dict[str, Any],
) -> None:
    """한 선적에 같은 원천 라인은 살아 있는 1행 — soft delete 뒤 재유입은 신규(부활 아님)"""
    with engine.begin() as connection:
        first = _line(connection, **line_base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _line(connection, **{**line_base, "line_no": 2})
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_shipment_lines_shipment_id_so_line_id_active",
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE shipment_lines SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        again = _line(connection, **{**line_base, "line_no": 2})
    assert again != first


# ── 당사자 ───────────────────────────────────────────────────────────────────


def _party(connection: Connection, **values: Any) -> int:
    columns = list(values)
    return int(
        connection.execute(
            text(
                f"INSERT INTO shipment_parties ({', '.join(columns)}) VALUES"
                f" ({', '.join(':' + c for c in columns)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


def test_party_constraints(so: dict[str, Any]) -> None:
    """당사자 — 역할 폐쇄 5값·자동 행은 CONSIGNEE·SHIPPER만·영문명 비공백/제어문자 금지·주소 공백 금지(여러 줄 허용)·(선적, 역할) 유일(삭제 후 재유입 = 신규)"""
    shipment = raw_shipment(so["id"])
    partner = create_buyer()
    base = {
        "shipment_id": shipment,
        "role": "CONSIGNEE",
        "partner_id": partner,
        "name_en": "Acme",
        "address_en": "1 Main St\nLos Angeles",
        "is_auto": True,
    }
    with engine.begin() as connection:
        first = _party(connection, **base)
        _party(connection, **{**base, "role": "NOTIFY", "is_auto": False})
    cases = [
        ({"role": "CARRIER", "is_auto": False}, "ck_shipment_parties_role_valid"),
        ({"role": "FORWARDER", "is_auto": True}, "ck_shipment_parties_auto_role"),
        (
            {"role": "FORWARDER", "is_auto": False, "name_en": " "},
            "ck_shipment_parties_name_en_clean",
        ),
        (
            {"role": "FORWARDER", "is_auto": False, "name_en": "A\tB"},
            "ck_shipment_parties_name_en_clean",
        ),
        (
            {"role": "FORWARDER", "is_auto": False, "address_en": "  "},
            "ck_shipment_parties_address_en_not_blank",
        ),
    ]
    for override, constraint in cases:
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _party(connection, **{**base, **override})
        assert _state(caught.value) == (CHECK_VIOLATION, constraint), override
    with pytest.raises(IntegrityError) as dup, engine.begin() as connection:
        _party(connection, **base)
    assert _state(dup.value) == (UNIQUE_VIOLATION, "uq_shipment_parties_shipment_id_role_active")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE shipment_parties SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        assert _party(connection, **base) != first


# ── 상태이력(IMMUTABLE) ─────────────────────────────────────────────────────


def test_status_log_is_immutable_for_the_app_account(so: dict[str, Any]) -> None:
    """J-11 — 앱 계정은 선적 상태이력에 INSERT만 한다: UPDATE·DELETE·TRUNCATE는 권한 거부(42501)"""
    shipment = raw_shipment(so["id"])
    actor = create_user("shlog@example.com")
    with engine.begin() as connection:
        log_id = int(
            connection.execute(
                text(
                    "INSERT INTO shipment_status_log (shipment_id, from_status, to_status, actor_user_id,"
                    " automatic) VALUES (:s, NULL, 'PLANNED', :a, false) RETURNING id"
                ),
                {"s": shipment, "a": actor},
            ).scalar_one()
        )
    for sql in (
        "UPDATE shipment_status_log SET reason = 'x' WHERE id = :i",
        "DELETE FROM shipment_status_log WHERE id = :i",
        "TRUNCATE shipment_status_log",
    ):
        with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
            connection.execute(text(sql), {"i": log_id})
        assert _state(caught.value)[0] == PERMISSION_DENIED, sql


def test_status_log_checks(so: dict[str, Any]) -> None:
    """상태이력 CHECK — 탄생 행은 PLANNED만·취소는 사유 필수·행위자 없는 사람 전이 금지·문서당 탄생 1행"""
    shipment = raw_shipment(so["id"])
    actor = create_user("shlog2@example.com")
    insert = (
        "INSERT INTO shipment_status_log (shipment_id, from_status, to_status, reason, actor_user_id,"
        " automatic) VALUES (:s, :f, :t, :r, :a, :auto)"
    )
    with engine.begin() as connection:
        connection.execute(
            text(insert),
            {"s": shipment, "f": None, "t": "PLANNED", "r": None, "a": actor, "auto": False},
        )
    cases = [
        ({"f": None, "t": "RELEASE_ORDERED"}, "ck_shipment_status_log_birth_row"),
        ({"f": "PLANNED", "t": "CANCELLED", "r": None}, "ck_shipment_status_log_reason_required"),
        (
            {"f": "PLANNED", "t": "RELEASE_ORDERED", "a": None},
            "ck_shipment_status_log_actor_or_automatic",
        ),
        ({"f": "PLANNED", "t": "CONFIRMED"}, "ck_shipment_status_log_to_status_valid"),
    ]
    for override, constraint in cases:
        params = {
            "s": shipment,
            "f": "PLANNED",
            "t": "RELEASE_ORDERED",
            "r": None,
            "a": actor,
            "auto": False,
        }
        params.update(override)
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            connection.execute(text(insert), params)
        assert _state(caught.value) == (CHECK_VIOLATION, constraint), override
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text(insert),
            {"s": shipment, "f": None, "t": "PLANNED", "r": None, "a": actor, "auto": False},
        )
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_shipment_status_log_shipment_id_birth")


@pytest.mark.group_k
def test_check_definitions_are_pinned() -> None:
    """정의문 고정(함정 ①·⑪) — 핵심 CHECK 정의문이 설계 문면과 같다(alembic check가 못 보는 드리프트 검출)"""
    expected = {
        "ck_shipments_kind_source": (
            "((((shipment_kind)::text = 'EXPORT'::text) AND (so_id IS NOT NULL) AND (po_id IS NULL))"
            " OR (((shipment_kind)::text = 'IMPORT'::text) AND (po_id IS NOT NULL) AND (so_id IS NULL)))"
        ),
        "ck_shipments_import_has_no_amount": "(((shipment_kind)::text <> 'IMPORT'::text) OR (total_amount = 0))",
        "ck_shipment_lines_export_priced": (
            "((so_line_id IS NULL) OR ((unit_price_amount IS NOT NULL)"
            " AND (((quantity)::numeric * (unit_price_amount)::numeric) = (line_amount)::numeric)))"
        ),
        "ck_shipment_lines_export_free_iff_zero_price": (
            "((so_line_id IS NULL) OR (is_free = (unit_price_amount = 0)))"
        ),
        "ck_shipment_lines_one_source": "((so_line_id IS NULL) <> (po_line_id IS NULL))",
        "ck_shipments_planned_not_frozen": "(((status)::text <> 'PLANNED'::text) OR (frozen_at IS NULL))",
        "ck_shipments_kind_valid": (
            "((shipment_kind)::text = ANY ((ARRAY['CHANNEL_INBOUND'::character varying, 'EXPORT'::character varying,"
            " 'IMPORT'::character varying, 'SAMPLE_FREE'::character varying])::text[]))"
        ),
    }
    with owner_engine.connect() as connection:
        found = {
            r[0]: r[1]
            for r in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = ANY(:n)"
                ),
                {"n": list(expected)},
            )
        }
    assert {
        name: found.get(name, "").removeprefix("CHECK (").removesuffix(")") for name in expected
    } == expected


@pytest.mark.group_k
def test_constraint_names_fit_postgres_identifier_limit() -> None:
    """식별자 63자 이내(잘린 이름은 마이그레이션이 지목하지 못한다) — 선적 4표의 제약·인덱스 전부"""
    with owner_engine.connect() as connection:
        names = [
            r[0]
            for r in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid::regclass::text IN"
                    " ('shipments','shipment_lines','shipment_parties','shipment_status_log')"
                    " UNION ALL SELECT indexname FROM pg_indexes WHERE tablename IN"
                    " ('shipments','shipment_lines','shipment_parties','shipment_status_log')"
                )
            )
        ]
    assert len(names) > 50 and max(len(n) for n in names) <= 63
