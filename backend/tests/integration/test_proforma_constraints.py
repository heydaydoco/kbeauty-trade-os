"""A·K. PI·은행계좌·PI 라인·PI 상태이력의 불변식을 DB가 강제한다 (S3-1 M04 / ADR-0051~0056 / design-A A2·A8·B6).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 모든 위반 케이스는 같은
  조건의 **양성 대조 INSERT가 성공**함을 함께 확인한다(TRUNCATE 하네스 공회전 방지). QT 견적 3테이블과 공통인 규칙(결제조건 형태·
  Incoterms·환율·통화)은 test_quotation_constraints가 전수 시험하므로, 여기서는 PI 고유 규칙 + 공통 규칙의 대표 표본을 본다.
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
    create_bank_account,
    create_buyer,
    raw_pi,
    raw_quotation,
    unique,
)
from tests.support.factories import create_sku

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
    """유효한 PI 한 줄의 기본값 — 원천 QT를 SQL로 만들고 그 통화·바이어를 물려받는다."""
    qt_id = raw_quotation("ISSUED")
    with owner_engine.connect() as connection:
        qt = connection.execute(
            text(
                "SELECT currency, buyer_partner_id, buyer_name, dest_market_code, assignee_id,"
                " fx_rate, fx_rate_date FROM quotations WHERE id = :i"
            ),
            {"i": qt_id},
        ).one()
    bank = create_bank_account(qt[0])
    return {
        "doc_number": "PI-2026-0001",
        "doc_date": date(2026, 9, 1),
        "status": "ISSUED",
        "currency": qt[0],
        "assignee_id": qt[4],
        "buyer_partner_id": qt[1],
        "buyer_name": qt[2],
        "dest_market_code": qt[3],
        "qt_id": qt_id,
        "buyer_address": "1 Main St",
        "valid_until": date(2026, 10, 1),
        "bank_account_id": bank,
        "bank_beneficiary_name": "Kbeauty Trading",
        "bank_beneficiary_address": "Seoul",
        "bank_name": "Synthetic Bank",
        "bank_address": "Seoul",
        "bank_account_no": "110-1",
        "bank_swift_code": "SYNTKRSE",
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
    " dest_market_code, qt_id, buyer_address, valid_until, bank_account_id, bank_beneficiary_name,"
    " bank_beneficiary_address, bank_name, bank_address, bank_account_no, bank_swift_code,"
    " payment_type, advance_pct_bp, balance_anchor, balance_days, incoterm_code, incoterm_place,"
    " incoterm_year, fx_rate, fx_rate_date, copied_from_id, total_amount, last_line_no"
)


def _insert(connection: Connection, **values: Any) -> int:
    row = {"copied_from_id": None, "total_amount": 0, "last_line_no": 0, **values}
    names = [c.strip() for c in _COLUMNS.split(",")]
    return int(
        connection.execute(
            text(
                f"INSERT INTO proforma_invoices ({_COLUMNS}) VALUES ({', '.join(':' + n for n in names)})"
                " RETURNING id"
            ),
            row,
        ).scalar_one()
    )


_CHECK_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("status_unknown", {"status": "CONVERTED"}, "ck_proforma_invoices_status_valid"),
    ("status_draft", {"status": "DRAFT"}, "ck_proforma_invoices_status_valid"),
    ("doc_number_prefix", {"doc_number": "QT-2026-0001"}, "ck_proforma_invoices_doc_number_format"),
    ("currency_lower", {"currency": "usd"}, "ck_proforma_invoices_currency_uppercase"),
    ("total_over_2_53", {"total_amount": 2**53}, "ck_proforma_invoices_total_range"),
    (
        "valid_until_before_doc",
        {"valid_until": date(2026, 8, 31)},
        "ck_proforma_invoices_valid_until_after_doc",
    ),
    ("buyer_name_blank", {"buyer_name": " "}, "ck_proforma_invoices_buyer_name_not_blank"),
    ("swift_short", {"bank_swift_code": "SYNTKRS"}, "ck_proforma_invoices_bank_swift_format"),
    ("swift_lower", {"bank_swift_code": "syntkrse"}, "ck_proforma_invoices_bank_swift_format"),
    ("fx_zero", {"fx_rate": 0}, "ck_proforma_invoices_fx_rate_range"),
    ("fx_without_date", {"fx_rate_date": None}, "ck_proforma_invoices_fx_pair"),
    (
        "krw_rate_not_one",
        {"currency": "KRW", "fx_rate": 2},
        "ck_proforma_invoices_krw_fx_is_one",
    ),
    (
        "partial_advance_without_anchor",
        {"balance_anchor": None, "balance_days": None},
        "ck_proforma_invoices_payment_terms_shape",
    ),
    ("incoterm_partial", {"incoterm_place": None}, "ck_proforma_invoices_incoterm_all_or_none"),
    (
        "dat_2020",
        {"incoterm_code": "DAT", "incoterm_year": 2020},
        "ck_proforma_invoices_incoterm_dat_2010",
    ),
    # PI는 생성=동결이라 frozen_at이 항상 있다 → 서류 필수 값이 하나라도 비면 동결 완결성 CHECK가 거부한다
    (
        "no_payment_terms",
        {
            "payment_type": None,
            "advance_pct_bp": None,
            "balance_anchor": None,
            "balance_days": None,
        },
        "ck_proforma_invoices_frozen_complete",
    ),
    (
        "no_incoterm",
        {"incoterm_code": None, "incoterm_place": None, "incoterm_year": None},
        "ck_proforma_invoices_frozen_complete",
    ),
    ("no_fx", {"fx_rate": None, "fx_rate_date": None}, "ck_proforma_invoices_frozen_complete"),
    ("blank_address", {"buyer_address": "  "}, "ck_proforma_invoices_frozen_complete"),
    ("blank_bank_name", {"bank_name": " "}, "ck_proforma_invoices_frozen_complete"),
    ("blank_account_no", {"bank_account_no": " "}, "ck_proforma_invoices_frozen_complete"),
    (
        "blank_beneficiary",
        {"bank_beneficiary_name": " "},
        "ck_proforma_invoices_frozen_complete",
    ),
]


def test_a_valid_pi_inserts_and_frozen_at_defaults_to_now(base: dict[str, Any]) -> None:
    """양성 대조 — 기본값 PI는 저장되고 동결 시각은 DB 기본값(now())으로 채워진다(생성=발행=동결)"""
    with engine.begin() as connection:
        pi_id = _insert(connection, **base)
        frozen = connection.execute(
            text("SELECT frozen_at IS NOT NULL FROM proforma_invoices WHERE id = :i"), {"i": pi_id}
        ).scalar_one()
    assert frozen is True


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _CHECK_CASES, ids=[c[0] for c in _CHECK_CASES]
)
def test_check_constraints_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """서비스를 우회한 직접 INSERT가 각 CHECK에서 거부된다(23514·제약 이름 일치) — 같은 base는 양성 대조로 통과한다"""
    with engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "PI-2026-0009"})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, **override})
    state, violated = _state(caught.value)
    assert state == CHECK_VIOLATION and violated == constraint, (name, violated)


@pytest.mark.parametrize(
    "missing", ["qt_id", "valid_until", "buyer_address", "bank_account_id", "bank_swift_code"]
)
def test_required_columns_are_not_null(base: dict[str, Any], missing: str) -> None:
    """QT 참조·유효기간·바이어 주소·은행 계좌·은행 스냅샷은 NOT NULL — 직접 PI 발행 경로·주소 없는 PI가 DB에서 불가능하다"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, missing: None})
    assert _state(caught.value)[0] == "23502"


def test_doc_number_is_globally_unique_even_after_soft_delete(base: dict[str, Any]) -> None:
    """doc_number는 전역 UNIQUE — soft delete 뒤에도 같은 번호를 다시 못 쓴다(재발급 금지 §17.3)"""
    with engine.begin() as connection:
        first = _insert(connection, **base)
        connection.execute(
            text("UPDATE proforma_invoices SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **base)
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_proforma_invoices_doc_number")


def test_only_one_live_copy_per_source_and_no_self_copy(base: dict[str, Any]) -> None:
    """복제본은 원본당 살아 있는 것 하나(취소·만료 복제본은 제외) · 자기 자신을 원본으로 못 가리킨다"""
    with engine.begin() as connection:
        source = _insert(connection, **{**base, "status": "CANCELLED"})
        _insert(connection, **{**base, "doc_number": "PI-2026-0002", "copied_from_id": source})
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, **{**base, "doc_number": "PI-2026-0003", "copied_from_id": source})
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_proforma_invoices_copied_from_id_live")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE proforma_invoices SET status = 'EXPIRED' WHERE copied_from_id = :s"),
            {"s": source},
        )
        _insert(connection, **{**base, "doc_number": "PI-2026-0004", "copied_from_id": source})
    with pytest.raises(IntegrityError) as selfref, engine.begin() as connection:
        connection.execute(
            text("UPDATE proforma_invoices SET copied_from_id = id WHERE id = :i"), {"i": source}
        )
    assert _state(selfref.value) == (CHECK_VIOLATION, "ck_proforma_invoices_copied_from_not_self")


def test_unknown_qt_bank_and_market_are_rejected_by_fk(base: dict[str, Any]) -> None:
    """FK — 없는 견적·계좌·시장은 DB가 막는다(PI는 QT 참조가 필수)"""
    for override in ({"qt_id": 999_999}, {"bank_account_id": 999_999}, {"dest_market_code": "ZZ"}):
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _insert(connection, **{**base, **override})
        assert _state(caught.value)[0] == FK_VIOLATION, override


def test_composite_key_targets_exist_for_the_sales_order_and_lines(base: dict[str, Any]) -> None:
    """UNIQUE(id, currency)·UNIQUE(id, qt_id)가 있다 — 라인 복합 FK(통화 일치)와 SO 복합 FK((pi_id, qt_id), PR-7)의 대상이다"""
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'proforma_invoices'::regclass AND contype = 'u'"
            )
        ).scalars()
        names = set(rows)
    assert {
        "uq_proforma_invoices_doc_number",
        "uq_proforma_invoices_id_currency",
        "uq_proforma_invoices_id_qt_id",
    } <= names


# ── 라인 ────────────────────────────────────────────────────────────────────

_LINE_COLUMNS = (
    "pi_id, qt_id, qt_line_id, currency, line_no, sku_id, sku_code, sku_name_ko, sku_kind, quantity,"
    " unit_price_amount, list_price_amount, line_amount, price_basis, is_free, price_reason"
)


def _insert_line(connection: Connection, **values: Any) -> int:
    row = {"list_price_amount": None, "price_reason": None, **values}
    names = [c.strip() for c in _LINE_COLUMNS.split(",")]
    return int(
        connection.execute(
            text(
                f"INSERT INTO proforma_invoice_lines ({_LINE_COLUMNS}) VALUES"
                f" ({', '.join(':' + n for n in names)}) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


@pytest.fixture
def line_base(base: dict[str, Any]) -> dict[str, Any]:
    """유효한 PI 라인 한 줄 — 원천 QT 라인 1개를 SQL로 만든다."""
    sku_id = create_sku(unique("SKU-PC"))
    with engine.begin() as connection:
        pi_id = _insert(connection, **base)
        qt_line = connection.execute(
            text(
                "INSERT INTO quotation_lines (qt_id, currency, line_no, sku_id, sku_code, sku_name_ko,"
                " sku_kind, quantity, unit_price_amount, line_amount, price_basis, is_free)"
                " VALUES (:q, :c, 1, :s, 'X', 'X', 'SINGLE', 10, 250, 2500, 'MASTER', false) RETURNING id"
            ),
            {"q": base["qt_id"], "c": base["currency"], "s": sku_id},
        ).scalar_one()
    return {
        "pi_id": pi_id,
        "qt_id": base["qt_id"],
        "qt_line_id": int(qt_line),
        "currency": base["currency"],
        "line_no": 1,
        "sku_id": sku_id,
        "sku_code": "X",
        "sku_name_ko": "테스트",
        "sku_kind": "SINGLE",
        "quantity": 10,
        "unit_price_amount": 250,
        "line_amount": 2500,
        "price_basis": "MASTER",
        "is_free": False,
    }


_LINE_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("amount_mismatch", {"line_amount": 2501}, "ck_proforma_invoice_lines_line_amount_matches"),
    (
        "quantity_zero",
        {"quantity": 0, "line_amount": 0},
        "ck_proforma_invoice_lines_quantity_range",
    ),
    ("currency_lower", {"currency": "usd"}, "ck_proforma_invoice_lines_currency_uppercase"),
    (
        "free_flag_with_price",
        {"is_free": True, "price_reason": "샘플"},
        "ck_proforma_invoice_lines_free_iff_zero_price",
    ),
    (
        "free_from_master",
        {"unit_price_amount": 0, "line_amount": 0, "is_free": True, "price_reason": "샘플"},
        "ck_proforma_invoice_lines_free_not_from_master",
    ),
]


def test_a_valid_line_inserts_and_the_qt_line_reference_is_required(
    line_base: dict[str, Any],
) -> None:
    """양성 대조 — 유효 라인 저장 · qt_line_id는 NOT NULL(참조 없는 PI 라인 불가) · 없는 원천 라인은 FK 위반"""
    with engine.begin() as connection:
        _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as null, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2, "qt_line_id": None})
    assert _state(null.value)[0] == "23502"
    with pytest.raises(IntegrityError) as missing, engine.begin() as connection:
        _insert_line(
            connection,
            **{
                **line_base,
                "line_no": 2,
                "sku_id": create_sku(unique("SKU-PC2")),  # 같은 SKU는 유니크가 먼저 걸린다
                "qt_line_id": 999_999,
            },
        )
    assert _state(missing.value)[0] == FK_VIOLATION


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LINE_CASES, ids=[c[0] for c in _LINE_CASES]
)
def test_line_checks_reject_violations(
    line_base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """PI 라인 CHECK — 금액=수량×단가·수량 범위·무상 양방향이 DB에서 거부된다(QT 라인과 같은 생성기)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, **override})
    state, violated = _state(caught.value)
    assert state == CHECK_VIOLATION and violated == constraint, (name, violated)


def test_line_currency_must_match_the_header_and_header_currency_is_locked_by_lines(
    line_base: dict[str, Any],
) -> None:
    """복합 FK (pi_id, currency) — 헤더와 다른 통화 라인은 거부 · 라인이 있으면 헤더 통화 변경도 거부(혼합 통화 불가능)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2, "currency": "EUR"})
    assert _state(caught.value) == (
        FK_VIOLATION,
        "fk_proforma_invoice_lines_pi_id_currency_proforma_invoices",
    )
    with engine.begin() as connection:
        _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as change, engine.begin() as connection:
        connection.execute(
            text("UPDATE proforma_invoices SET currency = 'EUR' WHERE id = :i"),
            {"i": line_base["pi_id"]},
        )
    assert _state(change.value)[0] == FK_VIOLATION


def test_line_uniqueness_is_active_only(line_base: dict[str, Any]) -> None:
    """(pi, line_no)·(pi, sku, is_free) 활성 유니크 — soft delete 뒤 재추가는 허용된다"""
    with engine.begin() as connection:
        first = _insert_line(connection, **line_base)
    with pytest.raises(IntegrityError) as dup, engine.begin() as connection:
        _insert_line(connection, **{**line_base, "line_no": 2})
    assert _state(dup.value) == (
        UNIQUE_VIOLATION,
        "uq_proforma_invoice_lines_pi_id_sku_id_is_free_active",
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE proforma_invoice_lines SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        _insert_line(connection, **{**line_base, "line_no": 2})


# ── 상태이력 ────────────────────────────────────────────────────────────────


def _log(connection: Connection, pi_id: int, **values: Any) -> None:
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
            "INSERT INTO proforma_invoice_status_log (proforma_invoice_id, from_status, to_status, reason,"
            " actor_user_id, automatic) VALUES (:p, :from_status, :to_status, :reason, :actor_user_id,"
            " :automatic)"
        ),
        {"p": pi_id, **row},
    )


_LOG_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "to_draft",
        {"from_status": "ISSUED", "to_status": "DRAFT"},
        "ck_proforma_invoice_status_log_to_status_valid",
    ),
    (
        "self_transition",
        {"from_status": "PAID", "to_status": "PAID"},
        "ck_proforma_invoice_status_log_no_self_transition",
    ),
    (
        "birth_not_issued",
        {"from_status": None, "to_status": "PAID"},
        "ck_proforma_invoice_status_log_birth_row",
    ),
    (
        "cancel_without_reason",
        {"from_status": "ISSUED", "to_status": "CANCELLED"},
        "ck_proforma_invoice_status_log_reason_required",
    ),
    (
        "expire_without_reason",
        {"from_status": "ISSUED", "to_status": "EXPIRED"},
        "ck_proforma_invoice_status_log_reason_required",
    ),
]


def test_status_log_accepts_payment_convergence_without_a_reason(base: dict[str, Any]) -> None:
    """양성 대조 — 탄생·입금 수렴(PARTIALLY_PAID·PAID·복귀)은 사유 없이 저장되고 취소·만료만 사유가 필수다"""
    with engine.begin() as connection:
        pi_id = _insert(connection, **base)
        _log(connection, pi_id)
        _log(connection, pi_id, from_status="ISSUED", to_status="PARTIALLY_PAID")
        _log(connection, pi_id, from_status="PARTIALLY_PAID", to_status="PAID")
        _log(connection, pi_id, from_status="PAID", to_status="ISSUED")


@pytest.mark.parametrize(
    ("name", "override", "constraint"), _LOG_CASES, ids=[c[0] for c in _LOG_CASES]
)
def test_status_log_checks_reject_violations(
    base: dict[str, Any], name: str, override: dict[str, Any], constraint: str
) -> None:
    """PI 상태이력 CHECK — 값·자기전이·탄생 행(ISSUED)·사유 필수(취소·만료)가 DB에서 거부된다"""
    with engine.begin() as connection:
        pi_id = _insert(connection, **base)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _log(connection, pi_id, **{"automatic": True, **override})
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), name


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE proforma_invoice_status_log SET reason = 'x'",
        "DELETE FROM proforma_invoice_status_log",
        "TRUNCATE proforma_invoice_status_log",
    ],
)
def test_status_log_is_immutable_for_the_app_role(base: dict[str, Any], statement: str) -> None:
    """이력은 INSERT/SELECT만 — 앱 계정의 UPDATE/DELETE/TRUNCATE는 42501로 거부된다(§17.5)"""
    with engine.begin() as connection:
        pi_id = _insert(connection, **base)
        _log(connection, pi_id)
    with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
        connection.execute(text(statement))
    assert _state(caught.value)[0] == PERMISSION_DENIED
    with owner_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM proforma_invoice_status_log")
            ).scalar_one()
            == 1
        )


# ── 은행 계좌 ───────────────────────────────────────────────────────────────


def _bank(connection: Connection, **values: Any) -> int:
    row = {
        "label": unique("BK"),
        "currency": "USD",
        "beneficiary_name": "N",
        "beneficiary_address": "A",
        "bank_name": "B",
        "bank_address": "BA",
        "account_no": "1",
        "swift_code": "SYNTKRSE",
        **values,
    }
    return int(
        connection.execute(
            text(
                "INSERT INTO bank_accounts (label, currency, beneficiary_name, beneficiary_address,"
                " bank_name, bank_address, account_no, swift_code) VALUES (:label, :currency,"
                " :beneficiary_name, :beneficiary_address, :bank_name, :bank_address, :account_no,"
                " :swift_code) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


@pytest.mark.parametrize(
    ("override", "constraint"),
    [
        ({"swift_code": "SYNTKRS"}, "ck_bank_accounts_swift_format"),
        ({"swift_code": "SYNT-KRSE"}, "ck_bank_accounts_swift_format"),
        ({"currency": "usd"}, "ck_bank_accounts_currency_uppercase"),
        ({"account_no": "  "}, "ck_bank_accounts_text_not_blank"),
        ({"bank_name": ""}, "ck_bank_accounts_text_not_blank"),
    ],
)
def test_bank_account_checks(override: dict[str, Any], constraint: str) -> None:
    """은행 계좌 CHECK — SWIFT 형식·통화 대문자·공백 값이 DB에서 거부된다(양성 대조: 기본값은 통과)"""
    with engine.begin() as connection:
        _bank(connection)
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _bank(connection, **override)
    assert _state(caught.value) == (CHECK_VIOLATION, constraint)


def test_bank_label_is_unique_among_active_rows_and_account_no_is_never_a_unique_key() -> None:
    """이름은 활성 행 유니크(비활성 뒤 재사용) · 계좌번호는 어떤 유니크 키에도 없다(중복 계좌는 서비스가 검사 — 같은 번호 2행이 DB에선 허용)"""
    with engine.begin() as connection:
        first = _bank(connection, label="주거래", account_no="777")
        _bank(connection, label="다른 이름", account_no="777")  # 같은 번호 — DB는 막지 않는다
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _bank(connection, label="주거래")
    assert _state(caught.value) == (UNIQUE_VIOLATION, "uq_bank_accounts_label_active")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE bank_accounts SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        _bank(connection, label="주거래")
    with owner_engine.connect() as connection:
        indexes = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename IN ('bank_accounts', 'proforma_invoices')"
            )
        ).scalars()
        assert not [d for d in indexes if "account_no" in d and "UNIQUE" in d.upper()]


def test_check_definitions_are_pinned(base: dict[str, Any]) -> None:
    """CHECK·부분 인덱스 정의문이 DB에 실재한다(pg_get_constraintdef/indexdef — alembic check는 CHECK를 못 본다)"""
    with owner_engine.connect() as connection:
        defs = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'proforma_invoices'::regclass AND contype = 'c'"
                )
            ).all()
        )
        indexes = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'proforma_invoices'"
                )
            ).all()
        )
        swift = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_bank_accounts_swift_format'"
            )
        ).scalar_one()
    assert re.search(
        r"PI-\[0-9\]\{4\}-\[0-9\]\{4,\}", defs["ck_proforma_invoices_doc_number_format"]
    )
    frozen = defs["ck_proforma_invoices_frozen_complete"]
    assert (
        "frozen_at IS NULL" in frozen and "bank_account_no" in frozen and "buyer_address" in frozen
    )
    assert "'PARTIALLY_PAID'" in defs["ck_proforma_invoices_status_valid"]
    assert "'PAID'" in defs["ck_proforma_invoices_status_valid"]
    live = indexes["uq_proforma_invoices_copied_from_id_live"]
    assert "CANCELLED" in live and "EXPIRED" in live and "copied_from_id IS NOT NULL" in live
    expiry = indexes["ix_proforma_invoices_expiry"]
    assert "status" in expiry and "ISSUED" in expiry and "deleted_at IS NULL" in expiry
    assert "[A-Z0-9]{8}" in swift


def test_raw_pi_factory_produces_a_row_that_satisfies_every_check() -> None:
    """공회전 방지 — 테스트 팩토리가 만드는 DB 직행 PI(상태별)가 전 CHECK를 통과한다(다른 테스트의 전제)"""
    qt = raw_quotation("ISSUED", buyer_partner_id=create_buyer())
    for status in ("ISSUED", "PARTIALLY_PAID", "PAID", "EXPIRED", "CANCELLED"):
        assert raw_pi(qt, status) > 0


def test_a_pi_line_cannot_point_at_another_quotations_line(line_base: dict[str, Any]) -> None:
    """복합 FK — PI 라인의 원천 QT 라인은 PI 헤더의 QT 소속이어야 한다: 다른 QT의 라인 id·다른 qt_id 표기 오염 삽입은 DB가 거부한다(양성 대조 통과 뒤)"""
    with engine.begin() as connection:
        _insert_line(connection, **line_base)  # 양성 대조 — 같은 QT의 라인
    other_qt = raw_quotation("ISSUED")
    sku = create_sku(unique("SKU-PX"))
    with engine.begin() as connection:
        foreign_line = int(
            connection.execute(
                text(
                    "INSERT INTO quotation_lines (qt_id, currency, line_no, sku_id, sku_code, sku_name_ko,"
                    " sku_kind, quantity, unit_price_amount, line_amount, price_basis, is_free)"
                    " VALUES (:q, 'USD', 1, :s, 'X', 'X', 'SINGLE', 5, 100, 500, 'MASTER', false)"
                    " RETURNING id"
                ),
                {"q": other_qt, "s": sku},
            ).scalar_one()
        )
    second_sku = create_sku(unique("SKU-PX2"))
    polluted = [
        {"qt_line_id": foreign_line},  # 헤더 qt_id + 다른 QT의 라인 → (qt_id, qt_line_id) FK 위반
        {
            "qt_id": other_qt,
            "qt_line_id": foreign_line,
        },  # 라인이 자기 QT는 맞지만 헤더의 qt_id와 다름 → (pi_id, qt_id) FK 위반
    ]
    for override in polluted:
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            _insert_line(
                connection, **{**line_base, "line_no": 2, "sku_id": second_sku, **override}
            )
        assert _state(caught.value)[0] == FK_VIOLATION, override
