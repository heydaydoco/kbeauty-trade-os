"""K. 오더 보드 조회·드릴다운·CSV·검색형 선택 (S3-1 PR-15a / design-D D6 / ADR-0066).

보드는 **비-Page 단일 객체**(고정 4열·열당 50·`total`·`has_more`)이고 카드에는 원가·마진·매입가·여신·게이트 필드가 없다(어느 역할에도). 쿼리 수는 카드 수와 무관한 상수다.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.csv_export import UTF8_BOM
from app.core.logging.redaction import is_sensitive_key
from app.core.time import KST, today_kst
from app.modules.identity.models import RoleCode
from app.modules.order_board import service as board_service
from app.modules.order_board.constants import COLUMN_LIMIT, STAGE_ORDER
from app.modules.order_board.schemas import BoardFilter
from tests.factories.board import (
    BOARD,
    EXPORT,
    ITEMS,
    board_user,
    column,
    raw_intake,
    scalar,
    seed_so,
    set_column,
    world_ids,
)
from tests.factories.trade import create_buyer, logged_in
from tests.support.sqlcount import count_statements

pytestmark = pytest.mark.group_k

CARD_FIELDS = {
    "kind",
    "id",
    "ref_label",
    "buyer_partner_id",
    "buyer_name",
    "buyer_po_no",
    "line_count",
    "total_amount",
    "total_text",
    "currency",
    "age_days",
    "assignee_id",
    "assignee_name",
    "updated_at",
    "version",
}

#: 보드 응답 어디에도 나오면 안 되는 이름 조각 — 원가·마진·매입가·여신·게이트 배지.
FORBIDDEN_FRAGMENTS = (
    "cost",
    "margin",
    "purchase",
    "credit",
    "exposure",
    "limit",
    "gate",
    "list_price",
    "override",
    "approval",
)


@pytest.fixture
def client() -> Any:
    with logged_in(RoleCode.TRADE) as c:
        yield c


def _board(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(BOARD, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return list(value) + [x for v in value.values() for x in _walk_keys(v)]
    if isinstance(value, list):
        return [x for v in value for x in _walk_keys(v)]
    return []


def test_the_board_is_one_object_with_four_fixed_columns(client: TestClient) -> None:
    """보드는 최상위 객체(columns·generated_at)이고 열은 고정 4개(대기·접수·보류·확정 순)·한국어 이름·빈 보드도 4열이다"""
    body = _board(client)
    assert set(body) == {"columns", "generated_at"}
    assert [c["stage"] for c in body["columns"]] == [s.value for s in STAGE_ORDER]
    assert [c["label_ko"] for c in body["columns"]] == [
        "인테이크 대기",
        "수주 접수",
        "수주 보류",
        "수주 확정",
    ]
    for col in body["columns"]:
        assert col == {
            "stage": col["stage"],
            "label_ko": col["label_ko"],
            "total": 0,
            "has_more": False,
            "items": [],
        }


def test_120_cards_cap_each_column_at_50_with_the_true_total_and_drilldown_covers_all(
    client: TestClient,
) -> None:
    """120건 시딩 — 열당 카드 50·total 120·has_more, 작은 열은 has_more 없음, '더 보기'(Page)가 120건 전부를 중복 없이 준다"""
    buyer, owner = world_ids()
    for _ in range(120):
        raw_intake(buyer=buyer, assignee=owner)
        seed_so("RECEIVED", buyer=buyer, assignee=owner)
    for _ in range(3):
        seed_so("ON_HOLD", buyer=buyer, assignee=owner)
    body = _board(client)
    for stage, total in (("INTAKE_PENDING", 120), ("SO_RECEIVED", 120)):
        col = column(body, stage)
        assert col["total"] == total and len(col["items"]) == COLUMN_LIMIT == 50
        assert col["has_more"] is True
    hold = column(body, "SO_ON_HOLD")
    assert hold["total"] == 3 and len(hold["items"]) == 3 and hold["has_more"] is False
    assert column(body, "SO_CONFIRMED")["total"] == 0

    for stage in ("INTAKE_PENDING", "SO_RECEIVED"):
        seen: list[int] = []
        for page in (1, 2, 3):
            r = client.get(ITEMS, params={"stage": stage, "page": page, "size": 50})
            assert r.status_code == 200, r.text
            payload = r.json()
            assert set(payload) == {"items", "total", "page", "size"}
            assert payload["total"] == 120
            seen.extend(item["id"] for item in payload["items"])
        assert len(seen) == 120 and len(set(seen)) == 120
        first_page = [i["id"] for i in column(body, stage)["items"]]
        assert seen[:50] == first_page  # 보드 열과 드릴다운의 정렬이 같다


def test_cards_carry_sales_side_fields_only_with_derived_counts_and_kst_age(
    client: TestClient,
) -> None:
    """카드 필드 집합이 정확하다 — 인테이크 라인 수·합계(수량×단가)·IN-{id}·거래처명·담당자명·KST 경과일, SO는 수주번호·헤더 합계"""
    buyer = create_buyer(name_en="Board Buyer Co.")
    owner = board_user(RoleCode.TRADE, name="담당 김")
    three_days_ago = datetime.combine(today_kst() - timedelta(days=3), datetime.min.time(), KST)
    intake = raw_intake(
        buyer=buyer,
        assignee=owner,
        po_no="PO-CARD-1",
        lines=[(2, 1500), (3, 250)],
        created_at=three_days_ago + timedelta(hours=1),
    )
    so = seed_so("RECEIVED", buyer=buyer, assignee=owner, buyer_po_no="PO-CARD-2")
    set_column("sales_orders", so, total_amount=12345)
    body = _board(client)
    card = column(body, "INTAKE_PENDING")["items"][0]
    assert set(card) == CARD_FIELDS
    assert card["kind"] == "INTAKE" and card["id"] == intake
    assert card["ref_label"] == f"IN-{intake}"
    assert card["buyer_name"] == "Board Buyer Co." and card["buyer_po_no"] == "PO-CARD-1"
    assert card["line_count"] == 2 and card["total_amount"] == 2 * 1500 + 3 * 250
    assert card["total_text"] == "37.50" and card["currency"] == "USD"
    assert card["assignee_id"] == owner and card["assignee_name"] == "담당 김"
    assert card["age_days"] == 3
    so_card = column(body, "SO_RECEIVED")["items"][0]
    assert set(so_card) == CARD_FIELDS
    assert so_card["kind"] == "SO" and so_card["ref_label"].startswith("SO-")
    assert so_card["total_amount"] == 12345 and so_card["line_count"] == 0
    assert so_card["age_days"] == 0


def test_cancelled_rejected_confirmed_intakes_and_reserved_states_stay_off_the_board(
    client: TestClient,
) -> None:
    """취소 SO·확정/거부 인테이크·예약 상태(완료 등)·soft delete는 보드에 없다 — 보드는 접수 이후~확정까지만"""
    buyer, owner = world_ids()
    seed_so("CANCELLED", buyer=buyer, assignee=owner)
    seed_so("COMPLETED", buyer=buyer, assignee=owner)
    deleted = seed_so("RECEIVED", buyer=buyer, assignee=owner)
    set_column("sales_orders", deleted, deleted_at=datetime.now(UTC))
    rejected = raw_intake(buyer=buyer, assignee=owner)
    set_column(
        "order_intakes",
        rejected,
        status="REJECTED",
        decided_at=datetime.now(UTC),
        decided_by_id=owner,
        reject_reason="합성 거부 사유",
    )
    kept = seed_so("CONFIRMED", buyer=buyer, assignee=owner)
    body = _board(client)
    assert [c["total"] for c in body["columns"]] == [0, 0, 0, 1]
    assert column(body, "SO_CONFIRMED")["items"][0]["id"] == kept


def test_confirmed_column_is_newest_confirmed_first_and_the_others_oldest_first(
    client: TestClient,
) -> None:
    """확정 열은 최근 확정 순, 대기·접수 열은 접수 오래된 순(주의 필요가 위)"""
    buyer, owner = world_ids()
    old_so = seed_so("CONFIRMED", buyer=buyer, assignee=owner)
    new_so = seed_so("CONFIRMED", buyer=buyer, assignee=owner)
    set_column("sales_orders", old_so, confirmed_at=datetime(2026, 9, 1, tzinfo=UTC))
    set_column("sales_orders", new_so, confirmed_at=datetime(2026, 9, 20, tzinfo=UTC))
    first = raw_intake(buyer=buyer, assignee=owner, created_at=datetime(2026, 9, 5, tzinfo=UTC))
    second = raw_intake(buyer=buyer, assignee=owner, created_at=datetime(2026, 9, 1, tzinfo=UTC))
    a = seed_so("RECEIVED", buyer=buyer, assignee=owner)
    b = seed_so("RECEIVED", buyer=buyer, assignee=owner)
    set_column("sales_orders", a, created_at=datetime(2026, 9, 9, tzinfo=UTC))
    set_column("sales_orders", b, created_at=datetime(2026, 9, 2, tzinfo=UTC))
    body = _board(client)
    assert [c["id"] for c in column(body, "SO_CONFIRMED")["items"]] == [new_so, old_so]
    assert [c["id"] for c in column(body, "INTAKE_PENDING")["items"]] == [second, first]
    assert [c["id"] for c in column(body, "SO_RECEIVED")["items"]] == [b, a]


def test_filters_narrow_every_column_and_q_escapes_like_wildcards(client: TestClient) -> None:
    """필터 — 거래처·담당자·통화·시장·q(바이어명·PO번호·수주번호 부분 일치, `%`·`_`는 글자 그대로)가 4열 모두에 걸린다"""
    buyer, owner = world_ids()
    other_buyer = create_buyer(name_en="Zeta Imports")
    other_owner = board_user(RoleCode.TRADE)
    i1 = raw_intake(buyer=buyer, assignee=owner, po_no="PO-100%OFF")
    i2 = raw_intake(buyer=other_buyer, assignee=other_owner, po_no="PO-ZZZ", currency="EUR")
    s1 = seed_so("RECEIVED", buyer=buyer, assignee=owner, buyer_po_no="PO-A_1")
    s2 = seed_so("ON_HOLD", buyer=other_buyer, assignee=other_owner, buyer_po_no="PO-AB1")

    def ids(**params: Any) -> set[int]:
        body = _board(client, **params)
        return {item["id"] for col in body["columns"] for item in col["items"]}

    assert ids(buyer_partner_id=buyer) == {i1, s1}
    assert ids(assignee_id=other_owner) == {i2, s2}
    assert ids(currency="EUR") == {i2} and ids(currency="USD") == {i1, s1, s2}
    assert ids(q="100%") == {i1}  # `%`가 와일드카드였다면 PO-ZZZ도 걸렸다
    assert ids(q="A_1") == {s1}  # `_`가 와일드카드였다면 PO-AB1도 걸렸다
    # 거래처 마스터 영문명 부분 일치(대소문자 무시) — 인테이크·수주 같은 의미
    assert ids(q="zeta") == {i2, s2}
    assert ids(q="raw buyer") == {s1, s2}  # 수주: 헤더 바이어 표기 부분 일치
    assert ids(dest_market_code="KR") == set()


def test_q_finds_so_cards_by_the_buyer_master_korean_name_and_intakes_by_their_ref_label(
    client: TestClient,
) -> None:
    """q — 수주 카드도 거래처 마스터 국문·영문명으로 찾는다(헤더 표기가 영문뿐이어도), 카드 표기 `IN-{id}`(대소문자 무시)는 그 인테이크 하나"""
    _, owner = world_ids()
    ganada = create_buyer(name_ko="가나다상사", name_en="Ganada Trading")
    so = seed_so("RECEIVED", buyer=ganada, assignee=owner)
    intake = raw_intake(buyer=ganada, assignee=owner)
    other = raw_intake(buyer=create_buyer(), assignee=owner)

    def ids(q: str) -> set[tuple[str, int]]:
        body = _board(client, q=q)
        return {(item["kind"], item["id"]) for col in body["columns"] for item in col["items"]}

    assert scalar("SELECT buyer_name FROM sales_orders WHERE id = :i", i=so) != "가나다상사"
    assert ids("가나다") == {("SO", so), ("INTAKE", intake)}
    assert ids("ganada") == {("SO", so), ("INTAKE", intake)}
    assert ids(f"IN-{other}") == {("INTAKE", other)}
    assert ids(f"in-{other}") == {("INTAKE", other)}
    assert ids(f"IN-{other}0") == set()  # 접두 일치가 아니라 정확히 그 id


def test_created_range_is_kst_inclusive_on_both_ends(client: TestClient) -> None:
    """접수일 필터는 KST 날짜·양끝 포함 — UTC 14:59는 그날, UTC 15:00은 KST 다음 날"""
    buyer, owner = world_ids()
    late = raw_intake(
        buyer=buyer, assignee=owner, created_at=datetime(2026, 9, 10, 14, 59, tzinfo=UTC)
    )
    next_day = raw_intake(
        buyer=buyer, assignee=owner, created_at=datetime(2026, 9, 10, 15, 0, tzinfo=UTC)
    )

    def ids(**params: Any) -> set[int]:
        return {i["id"] for i in column(_board(client, **params), "INTAKE_PENDING")["items"]}

    assert ids(created_from="2026-09-10", created_to="2026-09-10") == {late}
    assert ids(created_from="2026-09-11", created_to="2026-09-11") == {next_day}
    assert ids(created_from="2026-09-10") == {late, next_day}
    assert ids(created_to="2026-09-09") == set()


@pytest.mark.parametrize(
    "params",
    [
        {"cost": "1"},
        {"unit_cost": "1"},
        {"currency": "usd"},
        {"dest_market_code": "USA"},
        {"buyer_partner_id": "0"},
        {"created_from": "2026-09-10", "created_to": "2026-09-09"},
        {"q": "x" * 101},
        {"created_from": "9999-12-31"},
        {"created_to": "9999-12-31"},
        {"created_from": "0001-01-01"},
        {"created_to": "0001-01-01"},
        {"created_from": "1999-12-31"},
        {"created_to": "3000-01-01"},
        {"q": "a\x00b"},
        {"q": "주간\u200b"},
        {"q": "\u202e주간"},
        {"q": "a\u2028b"},
        {"q": "\u3164"},
    ],
)
def test_unknown_or_malformed_filter_keys_are_422(
    client: TestClient, params: dict[str, str]
) -> None:
    """필터 모델은 extra=forbid — 모르는 쿼리 키(원가 이름 포함)·형식 오류·범위 밖 날짜(2000-01-01~2999-12-31 밖 — `+1일` 오버플로 500 차단)·
    보이지 않는 글자(NUL·제로폭·방향 제어·줄 구분·한글 채움)가 든 q는 조용히 무시되지 않고 422 — 보드·드릴다운·CSV 모두"""
    for path, extra in ((BOARD, {}), (ITEMS, {"stage": "SO_RECEIVED"}), (EXPORT, {})):
        response = client.get(path, params={**params, **extra})
        assert response.status_code == 422, (path, response.text)
        assert response.json()["error"]["code"] == "COMMON.VALIDATION.INVALID_FIELD"


def test_the_date_filter_accepts_the_range_ends(client: TestClient) -> None:
    """접수일 허용 범위의 양끝(2000-01-01·2999-12-31)은 200 — 경계 바로 밖만 422"""
    response = client.get(BOARD, params={"created_from": "2000-01-01", "created_to": "2999-12-31"})
    assert response.status_code == 200, response.text
    assert client.get(EXPORT, params={"created_to": "2999-12-31"}).status_code == 200


def test_drilldown_requires_a_known_stage_and_bounded_page_size(client: TestClient) -> None:
    """드릴다운은 열(stage) 필수·알려진 값만·size ≤ 200·모르는 키 422"""
    assert client.get(ITEMS).status_code == 422
    assert client.get(ITEMS, params={"stage": "SO_CANCELLED"}).status_code == 422
    assert client.get(ITEMS, params={"stage": "SO_RECEIVED", "size": 201}).status_code == 422
    assert client.get(ITEMS, params={"stage": "SO_RECEIVED", "margin": 1}).status_code == 422
    ok = client.get(ITEMS, params={"stage": "SO_RECEIVED"})
    assert ok.status_code == 200 and ok.json()["size"] == 50


def test_board_query_count_is_constant_regardless_of_card_count() -> None:
    """N+1 없음 — 카드 3장과 200장(열 3개에 걸쳐)에서 SQL 문 수가 같다(빈 측정 가드 포함)"""
    buyer, owner = world_ids()
    raw_intake(buyer=buyer, assignee=owner)
    seed_so("RECEIVED", buyer=buyer, assignee=owner)
    seed_so("CONFIRMED", buyer=buyer, assignee=owner)
    small = count_statements(lambda: board_service.get_order_board(BoardFilter()))
    owners = [board_user(RoleCode.TRADE) for _ in range(3)]
    buyers = [create_buyer() for _ in range(3)]
    for n in range(70):
        raw_intake(buyer=buyers[n % 3], assignee=owners[n % 3], lines=[(1, 10), (2, 20)])
    for n in range(65):
        seed_so("RECEIVED", buyer=buyers[n % 3], assignee=owners[n % 3])
    for _ in range(62):
        seed_so("CONFIRMED", buyer=buyer, assignee=owner)
    large = count_statements(lambda: board_service.get_order_board(BoardFilter()))
    assert small > 0
    assert large == small
    drill_small = count_statements(
        lambda: board_service.list_board_items(BoardFilter(), STAGE_ORDER[0], offset=0, limit=5)
    )
    drill_large = count_statements(
        lambda: board_service.list_board_items(BoardFilter(), STAGE_ORDER[0], offset=0, limit=200)
    )
    assert drill_small == drill_large > 0


@pytest.mark.parametrize(
    "role", [RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.TRADE, RoleCode.ADMIN]
)
def test_every_role_sees_the_board_and_no_key_is_sensitive(role: RoleCode) -> None:
    """전 역할이 보드를 본다 — 응답 어디에도 민감 키(`is_sensitive_key`)·원가·마진·매입가·여신·게이트 이름이 없다(VIEWER 동일 모양)"""
    buyer, owner = world_ids()
    raw_intake(buyer=buyer, assignee=owner)
    seed_so("RECEIVED", buyer=buyer, assignee=owner)
    with logged_in(role) as c:
        body = _board(c)
        drill = c.get(ITEMS, params={"stage": "SO_RECEIVED"}).json()
    for key in _walk_keys(body) + _walk_keys(drill):
        assert not is_sensitive_key(key), key
        assert not any(fragment in key.lower() for fragment in FORBIDDEN_FRAGMENTS), key
    assert set(column(body, "SO_RECEIVED")["items"][0]) == CARD_FIELDS


# ── CSV ────────────────────────────────────────────────────────────────────────


def _csv(client: TestClient, **params: Any) -> list[list[str]]:
    response = client.get(EXPORT, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.startswith(UTF8_BOM)
    return list(csv.reader(io.StringIO(response.text[len(UTF8_BOM) :])))


def test_csv_has_the_fixed_header_without_cost_columns_and_kst_dates(client: TestClient) -> None:
    """보드 CSV — 고정 헤더(원가·마진·게이트·여신 열 없음)·BOM·KST 접수일·납기요청 최소일·한국어 열 이름·열 순서"""
    buyer, owner = world_ids()
    intake = raw_intake(
        buyer=buyer,
        assignee=owner,
        po_no="PO-CSV-1",
        lines=[(1, 100), (1, 200)],
        created_at=datetime(2026, 9, 10, 15, 30, tzinfo=UTC),
    )
    so = seed_so("ON_HOLD", buyer=buyer, assignee=owner, buyer_po_no="PO-CSV-2")
    rows = _csv(client)
    header, data = rows[0], rows[1:]
    assert header == [
        "구분",
        "문서번호",
        "거래처",
        "바이어PO번호",
        "통화",
        "합계금액",
        "라인수",
        "담당자",
        "접수일(KST)",
        "납기요청 최소일",
        "상태",
    ]
    for name in header:
        assert not any(f in name for f in ("원가", "마진", "매입", "게이트", "여신", "한도"))
    by_ref = {row[1]: row for row in data}
    intake_row = by_ref[f"IN-{intake}"]
    assert intake_row[0] == "인테이크" and intake_row[3] == "PO-CSV-1"
    assert intake_row[5] == "3.00" and intake_row[6] == "2"
    assert intake_row[8] == "2026-09-11"  # UTC 15:30 = KST 다음 날 00:30
    assert intake_row[9] == (today_kst() + timedelta(days=31)).isoformat()
    assert intake_row[10] == "인테이크 대기"
    so_rows = [row for row in data if row[0] == "수주"]
    assert len(so_rows) == 1 and so_rows[0][10] == "수주 보류" and so_rows[0][9] == ""
    assert so_rows[0][1] == scalar("SELECT doc_number FROM sales_orders WHERE id = :i", i=so)


def test_csv_cells_go_through_the_formula_escape_path(client: TestClient) -> None:
    """수식으로 시작하는 문자열 셀('=1+1'·'+'·'-'·'@')은 render_csv 통로에서 `'`가 붙는다(CSV 인젝션 차단)"""
    buyer, owner = world_ids()
    so = seed_so("RECEIVED", buyer=buyer, assignee=owner, buyer_po_no="@SUM(A1)")
    set_column("sales_orders", so, buyer_name="=1+1")
    rows = _csv(client, stage="SO_RECEIVED")
    assert len(rows) == 2
    assert rows[1][2] == "'=1+1" and rows[1][3] == "'@SUM(A1)"


def test_csv_respects_the_stage_and_filters_and_refuses_beyond_the_row_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSV는 보드와 같은 필터·stage를 따르고, 상한을 넘으면 잘라내지 않고 422로 조건을 좁히게 한다"""
    buyer, owner = world_ids()
    for _ in range(3):
        raw_intake(buyer=buyer, assignee=owner)
    seed_so("RECEIVED", buyer=buyer, assignee=owner)
    assert len(_csv(client, stage="INTAKE_PENDING")) == 1 + 3
    assert len(_csv(client)) == 1 + 4
    assert len(_csv(client, currency="EUR")) == 1
    monkeypatch.setattr(board_service, "EXPORT_MAX_ROWS", 3)
    response = client.get(EXPORT)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "COMMON.VALIDATION.INVALID_FIELD"
    assert len(_csv(client, stage="INTAKE_PENDING")) == 1 + 3  # 정확히 상한은 통과
    assert client.get(EXPORT, params={"cost": "1"}).status_code == 422


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.LOGISTICS])
def test_csv_is_open_to_every_role(role: RoleCode) -> None:
    """보드 CSV는 전 역할(원가 열이 없다)"""
    with logged_in(role) as c:
        assert c.get(EXPORT).status_code == 200


# ── 검색형 선택(서버 q) — 담당자 조회 ────────────────────────────────────────────

LOOKUP = "/api/v1/users/lookup"


def test_user_lookup_supports_q_and_the_assignee_target_filter() -> None:
    """담당자 조회 — `q` 표시명 부분 일치(와일드카드 이스케이프), `assignee_target=true`면 무역·관리자만, 기본은 종전대로 활성 전원·키 2개"""
    board_user(RoleCode.TRADE, name="김무역")
    board_user(RoleCode.ADMIN, name="김관리")
    board_user(RoleCode.VIEWER, name="김조회")
    board_user(RoleCode.LOGISTICS, name="100%물류")
    with logged_in(RoleCode.TRADE) as c:

        def names(**params: Any) -> set[str]:
            response = c.get(LOOKUP, params=params)
            assert response.status_code == 200, response.text
            for item in response.json()["items"]:
                assert set(item) == {"id", "display_name"}
            return {item["display_name"] for item in response.json()["items"]}

        assert names(q="김") == {"김무역", "김관리", "김조회"}
        assert names(q="김", assignee_target="true") == {"김무역", "김관리"}
        assert names(q="100%") == {"100%물류"}
        assert names(q="0%물") == {"100%물류"}
        assert "김조회" in names(size=200)
        assert c.get(LOOKUP, params={"q": "x" * 101}).status_code == 422
