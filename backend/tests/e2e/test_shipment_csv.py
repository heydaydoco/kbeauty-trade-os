"""G·K. 선적 목록 CSV 내보내기 `GET /shipments/export.csv` (S20 — S3-2 PR-3c / design-integrated X-24 / design-C C8 / design-D D2-1).

■ G(AI·보안 — S3-1 부기 `D:417` ① "PO 원가 마스킹" 승계, R-07): **원가·단가 열 0**(헤더 단위 행 — 수출 단가·수입 PO 원가 모두 미노출),
  UTF-8 BOM·CRLF, 수식으로 해석될 문자열 셀 `'` 접두(공용 통로 `core.csv_export`).
■ K(보안·품질): 전 역할 200·**같은 헤더·같은 행**(역할별 분기 없음), 필터 = 목록(S1)과 같은 조건(같은 선적 집합), 상한 초과 422(조용히 자르지
  않음), 쿼리 수는 행 수와 무관(N+1 0). 역할 게이트 자체는 `test_authz_matrix` 프로브가 5역할로 친다.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.trade_chain.shipment_view import EXPORT_HEADER
from tests.factories.shipments import (
    SHIPMENTS,
    cancel,
    confirmed_so,
    created,
    raw_shipment,
    release,
)
from tests.factories.trade import (
    create_po_via_api,
    create_purchase_priced_sku,
    create_supplier,
    logged_in,
    raw_po,
    unique,
)
from tests.support.factories import create_user

pytestmark = pytest.mark.group_g

EXPORT = f"{SHIPMENTS}/export.csv"
BOM = b"\xef\xbb\xbf"
#: 헤더에 있으면 안 되는 낱말 — 원가·마진·단가·매입 계열(국문·영문).
COST_WORDS = ("원가", "마진", "단가", "매입", "cost", "margin", "price", "purchase")


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _exec(sql: str, **params: Any) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


def _table(response: Any) -> list[list[str]]:
    """BOM을 확인하고 CSV를 셀 표로 — 첫 행이 헤더."""
    assert response.status_code == 200, response.text
    assert response.content.startswith(BOM)
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))


def _records(table: list[list[str]]) -> list[dict[str, str]]:
    return [dict(zip(table[0], row, strict=True)) for row in table[1:]]


# ── G. BOM·고정 헤더·원가 열 0 ─────────────────────────────────────────────────


def test_csv_is_bom_crlf_with_a_fixed_header_and_no_cost_or_price_column(
    trade: TestClient,
) -> None:
    """G — BOM·CRLF·헤더 상수 그대로, 헤더에 원가·마진·단가·매입 열 0, 라인 단가 값도 파일에 없다(헤더 단위 행 — 합계만, 판매가 축)"""
    so = confirmed_so((10,), price=1234)  # 단가 12.34 USD
    shipment = created(trade, so["id"], [(so["line_ids"][0], 4)])
    response = trade.get(EXPORT)
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert b"\r\n" in response.content
    table = _table(response)
    assert table[0] == list(EXPORT_HEADER)
    assert not [h for h in table[0] if any(word in h.lower() for word in COST_WORDS)]
    [record] = _records(table)
    assert record == {
        "선적번호": shipment["doc_number"],
        "증빙일": today_kst().isoformat(),
        "상태": "PLANNED",
        "구분": "EXPORT",
        "원천전표": so["doc_number"],
        "원천상태": "IN_SHIPMENT",
        "거래상대": shipment["counterparty"]["name"],
        "출발국": "KR",
        "도착국": "US",
        "통화": "USD",
        "합계": "49.36",
        "라인수": "1",
        "Incoterms": "FOB Busan",
        "출고지시일": "",
        "담당자": trade.get(f"{SHIPMENTS}/{shipment['id']}").json()["assignee"]["display_name"],
        "생성일": today_kst().isoformat(),
    }
    assert record["담당자"]  # 담당자 표시명(SO 담당 승계 — 상세 API의 담당자와 같은 사람)
    assert "12.34" not in response.text  # 라인 단가 채널 0
    assert release(trade, shipment["id"]).status_code == 200
    [after] = _records(_table(trade.get(EXPORT)))
    assert after["상태"] == "RELEASE_ORDERED" and after["출고지시일"] == today_kst().isoformat()


@pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@SUM(A1)", "'quoted"])
def test_text_cells_that_could_run_as_formulas_are_escaped(trade: TestClient, value: str) -> None:
    """G — 거래상대명 같은 자유 문자열이 `=`·`+`·`-`·`@`·`'`로 시작하면 `'` 하나를 접두한다(CSV 인젝션 차단·전단사, ADR-0027). 숫자 셀은 그대로"""
    so = confirmed_so((5,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 2)])
    _exec("UPDATE shipments SET counterparty_name = :v WHERE id = :i", v=value, i=shipment["id"])
    response = trade.get(EXPORT)
    [record] = _records(_table(response))
    assert record["거래상대"] == "'" + value
    assert record["라인수"] == "1" and record["합계"] == "20.00"  # 숫자·금액 셀은 이스케이프 비대상
    assert f",{value}," not in response.text


def test_import_rows_leave_the_amount_blank_and_never_carry_the_po_cost(trade: TestClient) -> None:
    """G(GC-G3 계열) — 수입선적 행은 원천 PO 번호·상태만, **합계는 빈칸**(금액 축 없음 — 0으로 쓰지 않는다). PO 원가(단가·합계)는
    원가 열람 역할(무역·관리자)로 내보내도 파일에 없다"""
    sku = create_purchase_priced_sku(amount=777_777)  # 매입 단가 7777.77 USD — PO 원가
    po = create_po_via_api(trade, create_supplier(), [sku])
    holder = confirmed_so((1,))  # 원시 수입선적 헤더의 통화·조건 원천(선적은 수입 — so_id 없음)
    raw_shipment(holder["id"], kind="IMPORT", po_id=po["id"])
    for role in (RoleCode.TRADE, RoleCode.ADMIN):
        with logged_in(role) as client:
            response = client.get(EXPORT)
            [record] = _records(_table(response))
            assert record["구분"] == "IMPORT"
            assert record["원천전표"] == po["doc_number"] and record["원천상태"] == "ISSUED"
            assert (
                record["합계"] == "" and record["통화"] == ""
            )  # PO 통화도 PO 필드 — 원가 비열람 역할에게 가려진 값(G3)
            for leaked in ("7777.77", "77777.7", "777777"):
                assert leaked not in response.text, (role, leaked)


@pytest.mark.parametrize(
    ("utc_instant", "kst_day"),
    [("2026-03-09 15:30:00+00", "2026-03-10"), ("2026-03-09 14:59:59+00", "2026-03-09")],
)
def test_created_and_released_dates_are_kst_dates_not_utc_dates(
    trade: TestClient, utc_instant: str, kst_day: str
) -> None:
    """G(렌즈 6 시간) — 생성일·출고지시일은 UTC 시각의 **KST 날짜**다. 실행 시각과 무관하게 경계를 고정해 단언한다(UTC 15:30 = KST 다음 날 00:30,
    UTC 14:59 = KST 같은 날 23:59 — UTC 날짜로 바꾸는 회귀는 첫 칸에서 실패)"""
    so = confirmed_so((10,))
    shipment = created(trade, so["id"], [(so["line_ids"][0], 1)])
    assert release(trade, shipment["id"]).status_code == 200
    _exec(
        "UPDATE shipments SET created_at = CAST(:t AS timestamptz), frozen_at = CAST(:t AS timestamptz)"
        " WHERE id = :i",
        t=utc_instant,
        i=shipment["id"],
    )
    [record] = _records(_table(trade.get(EXPORT)))
    assert record["생성일"] == kst_day and record["출고지시일"] == kst_day


# ── K. 전 역할·필터·상한·쿼리 수 ──────────────────────────────────────────────


@pytest.mark.group_k
def test_every_role_exports_the_same_header_and_the_same_rows() -> None:
    """K — 5역할 전부 200이고 헤더·행이 바이트 단위로 같다(원가 열이 없어 역할별 헤더 분기가 없다 — PO CSV와 다름)"""
    so = confirmed_so((5,))
    with logged_in(RoleCode.TRADE) as trade:
        created(trade, so["id"], [(so["line_ids"][0], 2)])
    bodies: dict[RoleCode, bytes] = {}
    for role in RoleCode:
        with logged_in(role) as client:
            response = client.get(EXPORT)
            assert len(_table(response)) == 2, role
            bodies[role] = response.content
    assert len(set(bodies.values())) == 1, {r.value: len(b) for r, b in bodies.items()}


@pytest.mark.group_k
def test_the_export_takes_exactly_the_list_filters(trade: TestClient) -> None:
    """K — 같은 필터면 CSV 선적 집합 = 목록(S1) 선적 집합(상태 다중·구분·SO·담당·검색어·조합), CSV는 id 오름차순. 잘못된 필터는 둘 다 422"""
    a = confirmed_so((10, 10))
    b = confirmed_so((10,))
    a1 = created(trade, a["id"], [(a["line_ids"][0], 1)])
    a2 = created(trade, a["id"], [(a["line_ids"][1], 1)])
    b1 = created(trade, b["id"], [(b["line_ids"][0], 1)])
    assert release(trade, a2["id"]).status_code == 200
    assert cancel(trade, b1["id"]).status_code == 200
    imported = raw_shipment(b["id"], kind="IMPORT", po_id=raw_po("ISSUED"))
    reassigned = create_user(
        f"{unique('owner')}@example.com", display_name="이관 담당 물류", roles=(RoleCode.LOGISTICS,)
    )
    _exec("UPDATE shipments SET assignee_id = :u WHERE id = :i", u=reassigned, i=a2["id"])
    _exec("UPDATE shipments SET counterparty_name = 'Zeta Partial Name' WHERE id = :i", i=b1["id"])
    cases: list[dict[str, Any]] = [
        {},
        {"so_id": a["id"]},
        {"status": ["PLANNED", "CANCELLED"]},
        {"status": "RELEASE_ORDERED"},
        {"shipment_kind": "IMPORT"},
        {"shipment_kind": "EXPORT"},
        {"assignee_id": reassigned},
        {"q": b1["doc_number"]},
        {"q": "partial na"},
        {"so_id": a["id"], "status": ["PLANNED"]},
        {"po_id": 999_999_999},
    ]
    imported_no = trade.get(f"{SHIPMENTS}/{imported}").json()["doc_number"]
    exported_by_case: dict[int, list[str]] = {}
    for n, params in enumerate(cases):
        listed = [
            item["doc_number"]
            for item in trade.get(SHIPMENTS, params={**params, "size": 200}).json()["items"]
        ]
        exported = [row[0] for row in _table(trade.get(EXPORT, params=params))[1:]]
        assert exported == list(reversed(listed)), params  # 같은 집합 · 목록 id↓ / CSV id↑
        exported_by_case[n] = exported
    # 공회전 방지 — 필터가 실제로 고르는 집합(조건 함수가 둘 다에서 무시돼도 대조가 통과하지 않게)
    assert exported_by_case[0] == [
        a1["doc_number"],
        a2["doc_number"],
        b1["doc_number"],
        imported_no,
    ]
    assert exported_by_case[1] == [a1["doc_number"], a2["doc_number"]]
    assert exported_by_case[2] == [a1["doc_number"], b1["doc_number"], imported_no]
    assert exported_by_case[3] == exported_by_case[6] == [a2["doc_number"]]
    # 담당 이관 뒤 CSV '담당자' 셀 = 새 담당의 표시명(작성자·SO 담당 조인 오류를 잡는다 — 목록 API와 대조)
    [moved] = _records(_table(trade.get(EXPORT, params={"assignee_id": reassigned})))
    [listed_moved] = trade.get(SHIPMENTS, params={"assignee_id": reassigned}).json()["items"]
    assert moved["담당자"] == listed_moved["assignee"]["display_name"] == "이관 담당 물류"
    assert (
        moved["담당자"]
        != _records(_table(trade.get(EXPORT, params={"q": a1["doc_number"]})))[0]["담당자"]
    )
    assert exported_by_case[4] == [imported_no]
    assert exported_by_case[7] == exported_by_case[8] == [b1["doc_number"]]
    assert exported_by_case[9] == [a1["doc_number"]] and exported_by_case[10] == []
    for bad in ({"so_id": 0}, {"q": "x" * 101}, {"shipment_kind": "export"}):
        assert trade.get(SHIPMENTS, params=bad).status_code == 422, bad
        assert trade.get(EXPORT, params=bad).status_code == 422, bad


@pytest.mark.group_k
def test_the_export_refuses_over_the_row_cap_instead_of_truncating(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K — 상한 초과는 422(조건을 좁히라는 안내 — 잘린 파일을 주지 않는다), 상한과 같으면 전건 출력"""
    from app.modules.trade_chain import shipment_view

    so = confirmed_so((10,))
    first = created(trade, so["id"], [(so["line_ids"][0], 1)])
    created(trade, so["id"], [(so["line_ids"][0], 1)])
    monkeypatch.setattr(shipment_view, "EXPORT_MAX_ROWS", 1)
    over = trade.get(EXPORT)
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "COMMON.VALIDATION.INVALID_FIELD"
    assert "size" in over.json()["error"]["detail"]
    assert len(_table(trade.get(EXPORT, params={"q": first["doc_number"]}))) == 2
    monkeypatch.setattr(shipment_view, "EXPORT_MAX_ROWS", 2)
    assert len(_table(trade.get(EXPORT))) == 3


@pytest.mark.group_k
def test_the_export_query_count_does_not_grow_with_rows(trade: TestClient) -> None:
    """K(렌즈 7) — 내보내기 쿼리 수는 행 수와 무관한 상수다(원천 번호·담당자·라인 수를 조인·상관 서브쿼리로 — N+1 0)"""
    from sqlalchemy import event

    from app.core.db.session import engine

    so = confirmed_so((10, 10, 10))
    created(trade, so["id"], [(so["line_ids"][0], 1)])

    def count() -> int:
        statements: list[str] = []

        def hook(*_args: Any) -> None:
            statements.append("q")

        event.listen(engine, "before_cursor_execute", hook)
        try:
            assert trade.get(EXPORT).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return len(statements)

    small = count()
    for line_id in so["line_ids"]:
        created(trade, so["id"], [(line_id, 1)])
    assert len(_table(trade.get(EXPORT))) == 5
    assert count() == small
