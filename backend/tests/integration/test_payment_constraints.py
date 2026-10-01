"""K. 입금 원장 `payments`의 불변식을 DB가 강제한다 — IMMUTABLE(kbos_app 42501 실측)·CHECK·복합 FK·부분 유니크·제약명 63자·역할 게이트 (S3-1 M08 / ADR-0068 / design-E E7).

서비스를 거치지 않고 raw INSERT로 위반시킨다(alembic check는 CHECK·GRANT를 못 본다 — 함정 ①). 모든 위반 케이스는 같은 조건의 **양성 대조 INSERT가 성공**함을 함께 확인한다
(TRUNCATE 하네스 공회전 방지). 앱 계정(`kbos_app`)이 UPDATE·DELETE·TRUNCATE를 못 한다는 것이 "원본 불변·정정=역기록"의 DB 층이다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.session import engine, owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES
from app.modules.identity.models import RoleCode
from tests.factories.payments import advance_pi, ledger, pay, scalar
from tests.factories.trade import logged_in, unique

pytestmark = pytest.mark.group_k

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"
PERMISSION_DENIED = "42501"

_COLUMNS = (
    "partner_id, pi_id, kind, received_amount, received_currency, received_on, reference,"
    " reverses_payment_id, reason, recorded_by_id"
)


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


@pytest.fixture
def base() -> dict[str, Any]:
    """유효한 RECEIPT 한 줄의 재료 — PI 1건(partner·USD)과 사용자."""
    pi = advance_pi()
    with owner_engine.connect() as connection:
        buyer = connection.execute(
            text("SELECT buyer_partner_id FROM proforma_invoices WHERE id = :i"), {"i": pi}
        ).scalar_one()
        user = connection.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
    return {
        "partner_id": buyer,
        "pi_id": pi,
        "kind": "RECEIPT",
        "received_amount": 10_000,
        "received_currency": "USD",
        "received_on": "2026-09-20",
        "reference": unique("REF"),
        "reverses_payment_id": None,
        "reason": None,
        "recorded_by_id": user,
    }


def _insert(connection: Connection, values: dict[str, Any]) -> int:
    names = list(values)
    return int(
        connection.execute(
            text(
                f"INSERT INTO payments ({', '.join(names)})"
                f" VALUES ({', '.join(':' + n for n in names)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


def _reversal(base: dict[str, Any], original_id: int, **overrides: Any) -> dict[str, Any]:
    return {
        **base,
        "kind": "REVERSAL",
        "received_amount": -base["received_amount"],
        "reverses_payment_id": original_id,
        "reason": "정정",
        **overrides,
    }


def _violates(values: dict[str, Any], code: str, constraint: str) -> None:
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, values)
    assert _state(caught.value) == (code, constraint)


def _app_denied(sql: str, **params: Any) -> None:
    with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
        connection.execute(text(sql), params)
    assert _state(caught.value)[0] == PERMISSION_DENIED


# ── IMMUTABLE — 앱 계정 INSERT·SELECT만 ─────────────────────────────────────────


def test_payments_is_immutable_and_the_app_account_can_only_insert_and_select(
    base: dict[str, Any],
) -> None:
    """kbos_app의 UPDATE·DELETE·TRUNCATE는 SQLSTATE 42501(실측) — INSERT·SELECT는 허용. 분류표(IMMUTABLE_TABLES)에도 등재"""
    assert "payments" in IMMUTABLE_TABLES
    with engine.begin() as connection:  # 양성 대조 — 앱 계정이 INSERT·SELECT는 한다
        row_id = _insert(connection, base)
        assert (
            connection.execute(
                text("SELECT received_amount FROM payments WHERE id = :i"), {"i": row_id}
            ).scalar_one()
            == 10_000
        )
    _app_denied("UPDATE payments SET received_amount = 1 WHERE id = :i", i=row_id)
    _app_denied("UPDATE payments SET reference = 'x' WHERE id = :i", i=row_id)
    _app_denied("DELETE FROM payments WHERE id = :i", i=row_id)
    _app_denied("TRUNCATE payments")
    with engine.connect() as connection:
        privileges = {
            p: connection.execute(
                text("SELECT has_table_privilege('kbos_app', 'payments', :p)"), {"p": p}
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
    # 열 단위로도 UPDATE 권한이 어디에도 없다(컬럼 GRANT 우회 없음)
    with engine.connect() as connection:
        columns = connection.execute(
            text(
                "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = 'public.payments'::regclass"
                " AND a.attnum > 0 AND NOT a.attisdropped"
            )
        ).scalars()
        denied = [
            c
            for c in columns
            if connection.execute(
                text("SELECT has_column_privilege('kbos_app', 'public.payments', :c, 'UPDATE')"),
                {"c": c},
            ).scalar_one()
        ]
    assert denied == []


# ── CHECK ──────────────────────────────────────────────────────────────────────


def test_the_positive_controls_insert_fine(base: dict[str, Any]) -> None:
    """양성 대조 — 유효한 RECEIPT와 그 역기록은 들어간다(위반 케이스가 공회전이 아님을 보증)"""
    with owner_engine.begin() as connection:
        original = _insert(connection, base)
        _insert(connection, _reversal(base, original))
    assert [r["kind"] for r in ledger(base["pi_id"])] == ["RECEIPT", "REVERSAL"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"received_amount": -10_000},  # RECEIPT 부호 반전
        {"received_amount": 0},  # 0원
        {"reason": "사유가 있는 입금"},  # RECEIPT에 사유
    ],
    ids=["receipt-negative", "receipt-zero", "receipt-with-reason"],
)
def test_kind_sign_rejects_malformed_receipts(
    base: dict[str, Any], overrides: dict[str, Any]
) -> None:
    """CHECK kind_sign — RECEIPT는 양수·사유 없음·역기록 대상 없음"""
    with owner_engine.begin() as connection:
        _insert(connection, base)  # 양성 대조
    _violates({**base, **overrides}, CHECK_VIOLATION, "ck_payments_kind_sign")


def test_kind_sign_rejects_a_receipt_pointing_at_a_reversal_target(base: dict[str, Any]) -> None:
    """RECEIPT가 reverses_payment_id를 가지면 거부(복합 FK가 통과해도 CHECK가 막는다)"""
    with owner_engine.begin() as connection:
        original = _insert(connection, base)
    _violates({**base, "reverses_payment_id": original}, CHECK_VIOLATION, "ck_payments_kind_sign")


@pytest.mark.parametrize(
    "overrides",
    [
        {"received_amount": 10_000},  # REVERSAL 부호 반전(양수)
        {"received_amount": 0},
        {"reason": None},  # 사유 없음
        {"reason": "가"},  # 사유 1자
        {"reason": "   "},  # 공백뿐
        {"reverses_payment_id": None},  # 대상 없음(MATCH SIMPLE 공백을 CHECK가 메운다)
    ],
    ids=["positive", "zero", "no-reason", "one-char", "blank", "no-target"],
)
def test_kind_sign_rejects_malformed_reversals(
    base: dict[str, Any], overrides: dict[str, Any]
) -> None:
    """CHECK kind_sign — REVERSAL은 음수·원 입금 지정·사유 2자 이상"""
    with owner_engine.begin() as connection:
        original = _insert(connection, base)
        _insert(connection, _reversal(base, original, reference=unique("OK")))  # 양성 대조
        second = _insert(connection, {**base, "reference": unique("R2")})
    _violates(_reversal(base, second, **overrides), CHECK_VIOLATION, "ck_payments_kind_sign")


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"kind": "ADJUST"}, "ck_payments_kind_valid"),
        ({"received_currency": "usd"}, "ck_payments_currency_upper"),
        ({"reference": "   "}, "ck_payments_reference_not_blank"),
        ({"reference": ""}, "ck_payments_reference_not_blank"),
        ({"received_amount": 2**53}, "ck_payments_received_amount_range"),
    ],
    ids=["kind", "currency-lower", "reference-blank", "reference-empty", "amount-range"],
)
def test_other_checks_reject_bad_values(
    base: dict[str, Any], overrides: dict[str, Any], constraint: str
) -> None:
    """kind 열거·통화 대문자·참조 비공백·금액 상한 CHECK(kind_sign보다 먼저 걸릴 수 있는 열은 이름까지 고정)"""
    with owner_engine.begin() as connection:
        _insert(connection, base)  # 양성 대조
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, {**base, **overrides})
    code, name = _state(caught.value)
    assert code == CHECK_VIOLATION
    # kind 위반은 kind_valid·kind_sign이 함께 걸릴 수 있어 DB가 고르는 첫 위반 이름을 허용한다
    assert name in (constraint, "ck_payments_kind_sign")


# ── 복합 FK·부분 유니크 ──────────────────────────────────────────────────────────


def test_a_reversal_must_point_at_a_payment_of_the_same_pi(base: dict[str, Any]) -> None:
    """복합 FK — 다른 PI의 입금을 가리키는 역기록은 23503(같은 PI만)"""
    other = advance_pi()
    with owner_engine.begin() as connection:
        mine = _insert(connection, base)
        _insert(connection, _reversal(base, mine))  # 양성 대조(같은 PI)
        theirs = _insert(connection, {**base, "pi_id": other, "reference": unique("OT")})
    _violates(
        _reversal(base, theirs, reference=unique("X")),
        FK_VIOLATION,
        "fk_payments_reverses_same_pi_currency",
    )


def test_a_reversal_must_share_the_currency_of_its_original(base: dict[str, Any]) -> None:
    """복합 FK — 같은 PI라도 통화가 다른 역기록은 23503"""
    with owner_engine.begin() as connection:
        eur = _insert(connection, {**base, "received_currency": "EUR", "reference": unique("EUR")})
    _violates(
        _reversal(base, eur, reference=unique("X")),  # USD 역기록 → EUR 원 입금
        FK_VIOLATION,
        "fk_payments_reverses_same_pi_currency",
    )
    with owner_engine.begin() as connection:  # 양성 대조 — 같은 통화(EUR)면 들어간다
        _insert(
            connection, _reversal({**base, "received_currency": "EUR"}, eur, reference=unique("Y"))
        )


def test_a_payment_can_be_reversed_only_once(base: dict[str, Any]) -> None:
    """부분 유니크 uq_payments_reverses_payment_id — 같은 입금의 두 번째 역기록은 23505, 서로 다른 입금의 역기록은 각각 가능"""
    with owner_engine.begin() as connection:
        first = _insert(connection, base)
        second = _insert(connection, {**base, "reference": unique("S")})
        _insert(connection, _reversal(base, first))
        _insert(connection, _reversal(base, second, reference=unique("RS")))  # 양성 대조
    _violates(
        _reversal(base, first, reference=unique("DUP")),
        UNIQUE_VIOLATION,
        "uq_payments_reverses_payment_id",
    )


def test_reference_is_deliberately_not_unique(base: dict[str, Any]) -> None:
    """같은 은행 참조가 반복돼도 DB는 막지 않는다(0-1 #13 — 이중 입력 탐지는 S3-3/P7) — 금액·참조는 어떤 유니크 키에도 없다"""
    with owner_engine.begin() as connection:
        _insert(connection, base)
        _insert(connection, base)
    with owner_engine.connect() as connection:
        unique_cols = (
            connection.execute(
                text(
                    "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid = 'public.payments'::regclass"
                    " AND indisunique"
                )
            )
            .scalars()
            .all()
        )
    assert unique_cols and not any("reference" in d or "amount" in d for d in unique_cols)


def test_foreign_keys_restrict_deletion_of_referenced_rows(base: dict[str, Any]) -> None:
    """FK는 RESTRICT — 입금이 붙은 PI·거래처는 지울 수 없다(23503)"""
    with owner_engine.begin() as connection:
        _insert(connection, base)
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM proforma_invoices WHERE id = :i"), {"i": base["pi_id"]}
        )
    assert _state(caught.value)[0] == FK_VIOLATION


# ── 정의문·이름 ──────────────────────────────────────────────────────────────────


def test_constraint_and_index_names_fit_63_chars_and_the_defs_are_pinned() -> None:
    """제약·인덱스 이름이 63자 이내(잘림 없음)이고 핵심 정의문이 고정돼 있다(alembic check가 CHECK를 못 본다)"""
    with owner_engine.connect() as connection:
        constraints = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'public.payments'::regclass"
                )
            )
            .tuples()
            .all()
        )
        indexes = dict(
            connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'payments'")
            )
            .tuples()
            .all()
        )
    assert max(len(n) for n in [*constraints, *indexes]) <= 63
    assert {
        "pk_payments",
        "uq_payments_id_pi_id_received_currency",
        "fk_payments_reverses_same_pi_currency",
        "ck_payments_kind_sign",
        "ck_payments_kind_valid",
        "ck_payments_currency_upper",
        "ck_payments_reference_not_blank",
    } <= set(constraints)
    fk = constraints["fk_payments_reverses_same_pi_currency"]
    assert "(reverses_payment_id, pi_id, received_currency)" in fk
    assert "REFERENCES payments(id, pi_id, received_currency)" in fk and "ON DELETE RESTRICT" in fk
    sign = constraints["ck_payments_kind_sign"]
    assert "received_amount > 0" in sign and "received_amount < 0" in sign
    assert "reverses_payment_id IS NOT NULL" in sign and "char_length(btrim(" in sign
    # NULL 사유가 CHECK를 통과하던 구멍(3값 논리) 방지 — 명시적 IS NOT NULL
    assert "reason IS NOT NULL" in sign
    partial = indexes["uq_payments_reverses_payment_id"]
    assert "UNIQUE" in partial and "WHERE (reverses_payment_id IS NOT NULL)" in partial
    assert "ix_payments_pi" in indexes and "(pi_id, id)" in indexes["ix_payments_pi"]


def test_no_float_column_and_no_ledger_mixin_columns_exist() -> None:
    """금액은 BIGINT(Float 부재)이고 원장에는 수정·삭제 흔적 열(updated_at·deleted_at·version)이 없다(믹스인 없음 — INSERT-only)"""
    with owner_engine.connect() as connection:
        columns = dict(
            connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns"
                    " WHERE table_name = 'payments'"
                )
            )
            .tuples()
            .all()
        )
    assert columns["received_amount"] == "bigint"
    assert not {"updated_at", "deleted_at", "version", "created_by_id", "updated_by_id"} & set(
        columns
    )
    assert not any(t in ("double precision", "real", "numeric") for t in columns.values())


# ── 역할 게이트(실 API) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER])
def test_roles_without_write_rights_get_403_on_writes_and_200_on_reads(role: RoleCode) -> None:
    """LOGISTICS·CERT·VIEWER — 입금 기록·역기록 403(원장 불변), 열람 200"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as trade:
        paid = pay(trade, pi, "100.00")
    pid = paid.json()["payment"]["id"]
    with logged_in(role) as client:
        assert pay(client, pi, "10.00").status_code == 403
        denied = client.post(
            f"/api/v1/payments/{pid}/reversal",
            json={"reason": "권한 없는 정정"},
            headers={"Idempotency-Key": unique("k")},
        )
        assert denied.status_code == 403
        assert client.get(f"/api/v1/proforma-invoices/{pi}/payments").status_code == 200
    assert len(ledger(pi)) == 1


@pytest.mark.parametrize("role", [RoleCode.TRADE, RoleCode.ADMIN])
def test_trade_and_admin_can_write(role: RoleCode) -> None:
    """TRADE·ADMIN — 입금 기록 201·역기록 201"""
    pi = advance_pi()
    with logged_in(role) as client:
        paid = pay(client, pi, "100.00")
        assert paid.status_code == 201, paid.text
        back = client.post(
            f"/api/v1/payments/{paid.json()['payment']['id']}/reversal",
            json={"reason": "정정 사유"},
            headers={"Idempotency-Key": unique("k")},
        )
        assert back.status_code == 201, back.text
    assert scalar("SELECT count(*) FROM payments WHERE pi_id = :p", p=pi) == 2
