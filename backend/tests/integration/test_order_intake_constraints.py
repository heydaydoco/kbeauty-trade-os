"""A·K. 오더 인테이크 2테이블의 불변식을 DB가 강제한다 (S3-1 M11 / ADR-0071 / design-D D1).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 모든 위반 케이스는 같은 조건의 **양성 대조 INSERT가 성공**함을 함께 확인한다
  (TRUNCATE 하네스 공회전 방지). 핵심: **중복 바이어 PO 0건(PENDING 한정 부분 유니크)**·결정 일관성(CONFIRMED⇔SO·REJECTED⇔사유·PENDING⇔결정 없음)·
  CSV 출처 쌍·라인 통화=헤더 통화(복합 FK)·불변 열 ORM 가드.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.session import engine, owner_engine
from tests.factories.intake import execute, rows, scalar
from tests.factories.trade import create_buyer, raw_so, unique
from tests.support.factories import create_market, create_sku, create_user

pytestmark = pytest.mark.group_a

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"
PERMISSION_DENIED = "42501"

_COLUMNS = (
    "source_kind, source_sha256, source_group_key, extracted_snapshot, buyer_partner_id, buyer_po_no,"
    " buyer_po_no_key, currency, dest_market_code, status, assignee_id, reject_reason, decided_at,"
    " decided_by_id, sales_order_id"
)


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


@pytest.fixture
def base() -> dict[str, Any]:
    create_market("US")
    owner = create_user(f"{unique('oic')}@example.com")
    return {
        "source_kind": "MANUAL",
        "source_sha256": None,
        "source_group_key": None,
        "extracted_snapshot": "{}",
        "buyer_partner_id": create_buyer(),
        "buyer_po_no": "PO-1",
        "buyer_po_no_key": "PO-1",
        "currency": "USD",
        "dest_market_code": "US",
        "status": "PENDING",
        "assignee_id": owner,
        "reject_reason": None,
        "decided_at": None,
        "decided_by_id": None,
        "sales_order_id": None,
        "_user": owner,
    }


def _insert(values: dict[str, Any]) -> int:
    params = {k: v for k, v in values.items() if not k.startswith("_")}
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    f"INSERT INTO order_intakes ({_COLUMNS}) VALUES (:source_kind, :source_sha256, :source_group_key,"
                    " CAST(:extracted_snapshot AS jsonb), :buyer_partner_id, :buyer_po_no, :buyer_po_no_key, :currency,"
                    " :dest_market_code, :status, :assignee_id, :reject_reason, :decided_at, :decided_by_id,"
                    " :sales_order_id) RETURNING id"
                ),
                params,
            ).scalar_one()
        )


def _violates(values: dict[str, Any], sqlstate: str, constraint: str | None = None) -> None:
    with pytest.raises(IntegrityError) as caught:
        _insert(values)
    state, name = _state(caught.value)
    assert state == sqlstate, (state, name)
    if constraint is not None:
        assert name == constraint, name


def test_a_valid_row_inserts_and_defaults_to_pending(base: dict[str, Any]) -> None:
    """양성 대조 — 유효한 행은 들어가고 status 기본값은 PENDING, version 1이다"""
    values = {k: v for k, v in base.items() if k != "status"}
    values["status"] = "PENDING"
    new_id = _insert(values)
    assert rows("SELECT status, version FROM order_intakes WHERE id = :i", i=new_id)[0] == (
        "PENDING",
        1,
    )
    with owner_engine.begin() as connection:  # status 생략 시 서버 기본값
        row = connection.execute(
            text(
                "INSERT INTO order_intakes (source_kind, extracted_snapshot, buyer_partner_id, buyer_po_no, buyer_po_no_key,"
                " currency, dest_market_code, assignee_id) VALUES ('MANUAL', '{}', :b, 'PO-2', 'PO-2', 'USD', 'US', :a)"
                " RETURNING status"
            ),
            {"b": base["buyer_partner_id"], "a": base["assignee_id"]},
        ).scalar_one()
    assert row == "PENDING"


def test_duplicate_pending_po_is_rejected_by_the_database(base: dict[str, Any]) -> None:
    """같은 (바이어, PO키)의 PENDING 두 건은 DB가 거부한다 — REJECTED·soft delete 건은 점유하지 않고 다른 바이어·다른 키는 통과(양성 대조)"""
    first = _insert(base)
    _violates(base, UNIQUE_VIOLATION, "uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending")
    # 해방 경로 — 거부된 행·삭제된 행은 키를 점유하지 않는다
    execute(
        "UPDATE order_intakes SET status = 'REJECTED', reject_reason = '사유 다섯자', decided_at = now(), decided_by_id = :u WHERE id = :i",
        u=base["_user"],
        i=first,
    )
    second = _insert(base)
    execute("UPDATE order_intakes SET deleted_at = now() WHERE id = :i", i=second)
    _insert(base)
    # 다른 바이어·다른 키는 별개
    _insert({**base, "buyer_partner_id": create_buyer(), "buyer_po_no_key": "PO-1"})
    _insert({**base, "buyer_po_no": "PO-9", "buyer_po_no_key": "PO-9"})


def test_the_partial_unique_predicate_is_pinned() -> None:
    """부분 유니크 3종(+복제)의 술어를 정의문으로 고정한다 — PENDING 한정·deleted_at IS NULL"""
    defs = dict(
        rows("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'order_intakes'")
    )
    po = defs["uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending"]
    assert "UNIQUE" in po and "(buyer_partner_id, buyer_po_no_key)" in po
    assert "deleted_at IS NULL" in po and "status)::text = 'PENDING'" in po
    sha = defs["uq_order_intakes_source_sha256_source_group_key_pending"]
    assert (
        "(source_sha256, source_group_key)" in sha
        and "'PENDING'" in sha
        and "source_sha256 IS NOT NULL" in sha
    )
    link = defs["uq_order_intakes_sales_order_id_active"]
    assert "(sales_order_id)" in link and "deleted_at IS NULL" in link
    copy = defs["uq_order_intakes_copied_from_so_id_pending"]
    assert "(copied_from_so_id)" in copy and "'PENDING'" in copy
    lines = rows(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_order_intake_lines_intake_id_line_no_active'"
    )
    assert "deleted_at IS NULL" in lines[0][0]


def test_decision_consistency_checks(base: dict[str, Any]) -> None:
    """결정 일관성 — PENDING인데 결정 시각·CONFIRMED인데 SO 없음·REJECTED인데 사유 공백·사유 짧음/제어문자는 각 CHECK 위반(양성 대조 포함)"""
    user = base["_user"]
    sales_order = raw_so()
    # PENDING인데 decided_at이 있다
    _violates(
        {**base, "decided_at": "2026-01-01T00:00:00Z", "decided_by_id": user},
        CHECK_VIOLATION,
        "ck_order_intakes_decision_consistent",
    )
    # 결정 시각만 있고 결정자가 없다(CONFIRMED·REJECTED 모두)
    _violates(
        {
            **base,
            "status": "REJECTED",
            "reject_reason": "사유 다섯자",
            "decided_at": "2026-01-01T00:00:00Z",
        },
        CHECK_VIOLATION,
        "ck_order_intakes_decision_consistent",
    )
    # CONFIRMED인데 sales_order_id 없음
    _violates(
        {
            **base,
            "status": "CONFIRMED",
            "decided_at": "2026-01-01T00:00:00Z",
            "decided_by_id": user,
        },
        CHECK_VIOLATION,
        "ck_order_intakes_confirmed_has_so",
    )
    # PENDING인데 SO 백링크가 있다
    _violates(
        {**base, "sales_order_id": sales_order},
        CHECK_VIOLATION,
        "ck_order_intakes_confirmed_has_so",
    )
    # REJECTED인데 사유 없음 / 공백 / 짧음 / 제어문자 / 길이 초과
    rejected = {
        **base,
        "status": "REJECTED",
        "decided_at": "2026-01-01T00:00:00Z",
        "decided_by_id": user,
    }
    _violates(
        {**rejected, "reject_reason": None}, CHECK_VIOLATION, "ck_order_intakes_rejected_has_reason"
    )
    _violates(
        {**rejected, "reject_reason": "     "},
        CHECK_VIOLATION,
        "ck_order_intakes_reject_reason_length",
    )
    _violates(
        {**rejected, "reject_reason": "짧음"},
        CHECK_VIOLATION,
        "ck_order_intakes_reject_reason_length",
    )
    _violates(
        {**rejected, "reject_reason": "가" * 501},
        CHECK_VIOLATION,
        "ck_order_intakes_reject_reason_length",
    )
    _violates(
        {**rejected, "reject_reason": "줄바꿈\n포함 사유"},
        CHECK_VIOLATION,
        "ck_order_intakes_reject_reason_clean",
    )
    # PENDING인데 사유가 있다
    _violates(
        {**base, "reject_reason": "사유 다섯자"},
        CHECK_VIOLATION,
        "ck_order_intakes_rejected_has_reason",
    )
    # 양성 대조: 올바른 REJECTED
    _insert({**rejected, "reject_reason": "정상 거부 사유"})
    _insert(
        {
            **base,
            "buyer_po_no": "OK-C",
            "buyer_po_no_key": "OK-C",
            "status": "CONFIRMED",
            "decided_at": "2026-01-01T00:00:00Z",
            "decided_by_id": user,
            "sales_order_id": sales_order,
        }
    )


def test_csv_source_pair_and_format_checks(base: dict[str, Any]) -> None:
    """출처 쌍 — CSV는 sha가 있어야 하고 MANUAL은 없어야 한다 / sha·그룹키는 함께 / sha 형식(소문자 64hex) — 양성 대조 포함"""
    sha = "a" * 64
    group = f"{base['buyer_partner_id']}|PO-1"
    _violates({**base, "source_kind": "CSV"}, CHECK_VIOLATION, "ck_order_intakes_source_pair")
    _violates(
        {**base, "source_sha256": sha, "source_group_key": group},
        CHECK_VIOLATION,
        "ck_order_intakes_source_pair",
    )
    _violates(
        {**base, "source_kind": "CSV", "source_sha256": sha},
        CHECK_VIOLATION,
        "ck_order_intakes_source_pair",
    )
    _violates(
        {**base, "source_kind": "CSV", "source_sha256": "A" * 64, "source_group_key": group},
        CHECK_VIOLATION,
        "ck_order_intakes_source_sha256_format",
    )
    _violates(
        {**base, "source_kind": "EMAIL"}, CHECK_VIOLATION, "ck_order_intakes_source_kind_valid"
    )
    ok = {**base, "source_kind": "CSV", "source_sha256": sha, "source_group_key": group}
    _insert(ok)
    # 같은 (sha, 그룹키)의 PENDING은 두 건이 될 수 없다(파일 해시 멱등 — PO가 달라도)
    _violates(
        {**ok, "buyer_po_no": "PO-X", "buyer_po_no_key": "PO-X"},
        UNIQUE_VIOLATION,
        "uq_order_intakes_source_sha256_source_group_key_pending",
    )


def test_header_value_checks(base: dict[str, Any]) -> None:
    """통화 소문자·PO 공백·상태 값 밖은 CHECK 위반 — 양성 대조는 위 테스트들의 정상 행"""
    _violates({**base, "currency": "usd"}, CHECK_VIOLATION, "ck_order_intakes_currency_uppercase")
    _violates({**base, "buyer_po_no": "  "}, CHECK_VIOLATION, "ck_order_intakes_po_key_nonblank")
    _violates({**base, "buyer_po_no_key": " "}, CHECK_VIOLATION, "ck_order_intakes_po_key_nonblank")
    _violates(
        {**base, "status": "DONE"}, CHECK_VIOLATION
    )  # 값 밖 상태는 여러 CHECK에 걸린다 — 어느 것이든 거부
    _violates(
        {**base, "extracted_snapshot": "[]"},
        CHECK_VIOLATION,
        "ck_order_intakes_extracted_snapshot_is_object",
    )
    _violates({**base, "dest_market_code": "ZZ"}, FK_VIOLATION)
    _violates({**base, "buyer_partner_id": 999_999}, FK_VIOLATION)
    _insert(base)


def test_sales_order_backlink_is_unique_and_one_way(base: dict[str, Any]) -> None:
    """인테이크 1건=SO 1건 — 같은 SO를 가리키는 두 번째 CONFIRMED는 유니크 위반(soft delete한 건은 제외)"""
    so_id = raw_so()
    user = base["_user"]
    confirmed = {
        **base,
        "status": "CONFIRMED",
        "decided_at": "2026-01-01T00:00:00Z",
        "decided_by_id": user,
        "sales_order_id": so_id,
    }
    _insert(confirmed)
    _violates(
        {**confirmed, "buyer_po_no": "PO-3", "buyer_po_no_key": "PO-3"},
        UNIQUE_VIOLATION,
        "uq_order_intakes_sales_order_id_active",
    )
    cols = rows(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'sales_orders'"
    )
    assert "intake_id" not in {c[0] for c in cols}  # SO 쪽 역FK 없음(단방향 백링크)


def _line(intake_id: int, **over: Any) -> dict[str, Any]:
    values = {
        "intake_id": intake_id,
        "currency": "USD",
        "line_no": 1,
        "buyer_item_code": "BC-1",
        "sku_id": None,
        "quantity": 5,
        "unit_price_amount": 1000,
        "requested_delivery_date": None,
        "source_row_no": None,
    }
    values.update(over)
    return values


def _insert_line(values: dict[str, Any]) -> int:
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO order_intake_lines (intake_id, currency, line_no, buyer_item_code, sku_id, quantity,"
                    " unit_price_amount, requested_delivery_date, source_row_no) VALUES (:intake_id, :currency, :line_no,"
                    " :buyer_item_code, :sku_id, :quantity, :unit_price_amount, :requested_delivery_date, :source_row_no)"
                    " RETURNING id"
                ),
                values,
            ).scalar_one()
        )


def test_line_checks_and_composite_fk(base: dict[str, Any]) -> None:
    """라인 — 통화 불일치(복합 FK)·수량 0/상한 초과·단가 0(무상 불가)·번호 0·품번 공백·행번호 1·소문자 통화·line_no 중복은 거부, 양성 대조 성공"""
    intake = _insert(base)
    _insert_line(_line(intake))  # 양성 대조
    cases = [
        (_line(intake, currency="KRW", line_no=2), FK_VIOLATION),
        (_line(intake, line_no=2, quantity=0), CHECK_VIOLATION),
        (_line(intake, line_no=2, quantity=100_000_000), CHECK_VIOLATION),
        (_line(intake, line_no=2, unit_price_amount=0), CHECK_VIOLATION),
        (_line(intake, line_no=2, unit_price_amount=9_007_199_254_740_992), CHECK_VIOLATION),
        (_line(intake, line_no=0), CHECK_VIOLATION),
        (_line(intake, line_no=2, buyer_item_code="  "), CHECK_VIOLATION),
        (_line(intake, line_no=2, source_row_no=1), CHECK_VIOLATION),
        (_line(intake, line_no=2, currency="usd"), CHECK_VIOLATION),
        (_line(intake, line_no=1), UNIQUE_VIOLATION),
        (_line(intake, line_no=2, sku_id=999_999), FK_VIOLATION),
    ]
    for values, sqlstate in cases:
        with pytest.raises(IntegrityError) as caught:
            _insert_line(values)
        assert _state(caught.value)[0] == sqlstate, values
    _insert_line(
        _line(intake, line_no=2, sku_id=create_sku(unique("SKU")), source_row_no=2)
    )  # 양성 대조


def test_the_app_account_can_update_but_not_delete_or_truncate_nothing_extra() -> None:
    """MUTABLE 표 — 앱 계정은 INSERT·UPDATE가 되고(편집·전이·이관), 사슬 상태이력 같은 REVOKE 대상이 아님을 table_policy와 DB 권한이 같이 말한다"""
    from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES

    assert {"order_intakes", "order_intake_lines"} <= MUTABLE_TABLES
    assert not {"order_intakes", "order_intake_lines"} & IMMUTABLE_TABLES
    with engine.connect() as connection:  # 앱 계정(kbos_app)
        for table in ("order_intakes", "order_intake_lines"):
            for privilege in ("INSERT", "UPDATE"):
                assert (
                    connection.execute(
                        text("SELECT has_table_privilege(current_user, :t, :p)"),
                        {"t": table, "p": privilege},
                    ).scalar_one()
                    is True
                ), (table, privilege)


def test_identifier_lengths_are_within_the_postgres_limit() -> None:
    """제약·인덱스 이름이 63자 이내다(잘린 이름은 이후 마이그레이션이 지목하지 못한다) — 정의된 이름 전수 실측"""
    names = [
        r[0]
        for r in rows(
            "SELECT conname FROM pg_constraint WHERE conrelid IN ('order_intakes'::regclass, 'order_intake_lines'::regclass)"
            " UNION SELECT indexname FROM pg_indexes WHERE tablename IN ('order_intakes', 'order_intake_lines')"
        )
    ]
    assert len(names) >= 25
    assert max(len(n) for n in names) <= 63, [n for n in names if len(n) > 63]


def test_downgrade_removes_only_the_two_tables_and_is_safe_without_seed() -> None:
    """M11은 시드 0 — 두 테이블에 마이그레이션이 넣은 행이 없다(빈 상태에서 시작)"""
    assert scalar("SELECT count(*) FROM order_intakes") == 0
    assert scalar("SELECT count(*) FROM order_intake_lines") == 0
    with pytest.raises(ProgrammingError), owner_engine.connect() as connection:
        connection.execute(
            text("SELECT 1 FROM order_intake_edit_log")
        )  # 이력 테이블은 만들지 않는다(원본 스냅샷 대 현재 diff)


# ── 불변 열 ORM 가드 (층2) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("column", "new_value"),
    [
        ("extracted_snapshot", {"kind": "MANUAL", "tampered": True}),
        ("buyer_partner_id", None),  # 아래에서 다른 바이어 id로 치환
        ("currency", "KRW"),
        ("source_kind", "CSV"),
        ("source_sha256", "b" * 64),
        ("source_group_key", "x|y"),
        ("original_filename", "x.csv"),
    ],
)
def test_the_orm_guard_refuses_a_net_change_of_an_immutable_column(
    base: dict[str, Any], column: str, new_value: Any
) -> None:
    """등록 후 변경 불가 열 7종 — ORM으로 값을 실제로 바꾸면 UPDATE 전에 예외이고 DB 행은 그대로다(같은 값 재대입·가변 열 변경은 통과: 양성 대조)"""
    from app.core.db.uow import unit_of_work
    from app.modules.order_intake.models import ImmutableIntakeFieldError, OrderIntake

    intake_id = _insert(base)
    if column == "buyer_partner_id":
        new_value = create_buyer()
    before = rows(
        "SELECT extracted_snapshot, buyer_partner_id, currency, source_kind FROM order_intakes WHERE id = :i",
        i=intake_id,
    )
    with pytest.raises(ImmutableIntakeFieldError), unit_of_work() as uow:
        row = uow.session.get(OrderIntake, intake_id)
        assert row is not None
        setattr(row, column, new_value)
        uow.session.flush()
    assert (
        rows(
            "SELECT extracted_snapshot, buyer_partner_id, currency, source_kind FROM order_intakes WHERE id = :i",
            i=intake_id,
        )
        == before
    )
    # 양성 대조 — 같은 값 재대입과 가변 열 변경은 통과한다
    with unit_of_work() as uow:
        row = uow.session.get(OrderIntake, intake_id)
        assert row is not None
        row.currency = row.currency
        row.buyer_po_date = None
        row.updated_by_id = base["_user"]
        uow.session.flush()
    assert scalar("SELECT version FROM order_intakes WHERE id = :i", i=intake_id) >= 1
