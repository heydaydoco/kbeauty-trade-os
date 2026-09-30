"""A·K. S3-1 PR-3 읽기 확장 — 품번 삭제·품번 해석·검색·벌크 단가·준비도 단건 (F12·D4·F16).

전표·게이트(후속 PR)가 그대로 쓰는 읽기 통로다. 여기서 고정하는 것: 삭제가 해석에서 즉시
빠지고 재등록이 새 행이라는 것, 부재를 0으로 채우지 않는다는 것, 검색 와일드카드 이스케이프,
그리고 준비도 단건 셀이 매트릭스와 **같은 색**이라는 것(규칙이 한 곳).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.main import app
from app.modules.catalog.pricing import prices_at
from app.modules.identity.models import RoleCode
from app.modules.partners import service as partners
from app.modules.readiness import service as readiness
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_certification_instance,
    create_item_profile,
    create_market,
    create_partner,
    create_requirement_template,
    create_sku,
    create_sku_with_axes,
    create_user,
)

pytestmark = pytest.mark.group_a


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        ).is_success
        yield client


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("rh-trade@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("rh-viewer@example.com", RoleCode.VIEWER)


def _map(client: TestClient, partner_id: int, sku_id: int, code: str, key: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/partners/{partner_id}/item-codes",
        json={"sku_id": sku_id, "buyer_item_code": code},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_item_code_delete_removes_it_from_resolution_and_allows_reregistration(
    trader: TestClient,
) -> None:
    """삭제 → 해석에서 즉시 빠지고, 같은 (거래처, 품번) 재등록은 새 행이다(정정 = 삭제 후 재등록)"""
    buyer = create_partner("PTN-B1", name_ko="바이어", types=("BUYER",))
    sku_a, sku_b = create_sku("SKU-A"), create_sku("SKU-B")
    first = _map(trader, buyer, sku_a, "BUY-001", "ic1")
    with unit_of_work() as uow:
        assert partners.resolve_buyer_items(uow.session, buyer, [" BUY-001 ", "NOPE"]).keys() == {
            "BUY-001"
        }

    assert trader.delete(f"/api/v1/partners/{buyer}/item-codes/{first['id']}").status_code == 204
    with unit_of_work() as uow:
        assert partners.resolve_buyer_items(uow.session, buyer, ["BUY-001"]) == {}

    second = _map(trader, buyer, sku_b, "BUY-001", "ic2")  # 삭제 후 같은 품번을 다른 SKU로
    assert second["id"] != first["id"] and second["sku_code"] == "SKU-B"
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE action='partners.item_code.deleted'")
        ).scalar_one()
    assert n == 1


def test_item_code_delete_is_scoped_and_role_gated(trader: TestClient, viewer: TestClient) -> None:
    """타 거래처 id·이미 삭제된 id는 404, 조회 역할은 403"""
    b1 = create_partner("PTN-B2", name_ko="바이어2", types=("BUYER",))
    b2 = create_partner("PTN-B3", name_ko="바이어3", types=("BUYER",))
    sku = create_sku("SKU-C")
    row = _map(trader, b1, sku, "BUY-002", "ic3")
    assert trader.delete(f"/api/v1/partners/{b2}/item-codes/{row['id']}").status_code == 404
    assert viewer.delete(f"/api/v1/partners/{b1}/item-codes/{row['id']}").status_code == 403
    assert trader.delete(f"/api/v1/partners/{b1}/item-codes/{row['id']}").status_code == 204
    assert trader.delete(f"/api/v1/partners/{b1}/item-codes/{row['id']}").status_code == 404


def test_resolution_skips_deleted_skus_and_is_one_query(trader: TestClient) -> None:
    """삭제된 SKU는 해석되지 않고, 품번 N개도 쿼리 1번이다(N+1 금지)"""
    from sqlalchemy import event

    buyer = create_partner("PTN-B4", name_ko="바이어4", types=("BUYER",))
    skus = [create_sku(f"SKU-R{i}") for i in range(4)]
    for i, sku in enumerate(skus):
        _map(trader, buyer, sku, f"R-{i}", f"icr{i}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE skus SET deleted_at = now() WHERE id = :i"), {"i": skus[3]})
    statements: list[str] = []
    with unit_of_work() as uow:
        bind = uow.session.get_bind()
        listener = lambda *args: statements.append(args[2])  # noqa: E731
        event.listen(bind, "before_cursor_execute", listener)
        try:
            found = partners.resolve_buyer_items(uow.session, buyer, ["R-0", "R-1", "R-2", "R-3"])
        finally:
            event.remove(bind, "before_cursor_execute", listener)
    assert set(found) == {"R-0", "R-1", "R-2"}
    assert len([s for s in statements if "customer_item_codes" in s]) == 1


def test_require_partner_of_any_type_is_fail_closed() -> None:
    """유형 하나라도 있으면 통과, 없거나 삭제·미존재는 422"""
    both = create_partner("PTN-T1", name_ko="공급", types=("SUPPLIER",))
    forwarder = create_partner("PTN-T2", name_ko="포워더", types=("FORWARDER",))
    with unit_of_work() as uow:
        s = uow.session
        assert (
            partners.require_partner_of_any_type(
                s,
                both,
                ("SUPPLIER", "OEM"),
                field="supplier_id",
                type_label="공급사/제조사",
                lock=True,
            ).id
            == both
        )
        for bad in (forwarder, 999999):
            with pytest.raises(AppError):
                partners.require_partner_of_any_type(
                    s, bad, ("SUPPLIER", "OEM"), field="supplier_id", type_label="공급사/제조사"
                )


def test_lists_support_search_and_type_filter(trader: TestClient) -> None:
    """q(코드·국문·영문 부분 검색, 와일드카드 이스케이프)와 type 필터 — 페이지 계약 유지"""
    create_partner("PTN-S1", name_ko="알파 바이어", types=("BUYER",))
    create_partner("PTN-S2", name_ko="베타 공급", types=("SUPPLIER",))
    create_partner("PTN-S3", name_ko="100% 특가", types=("BUYER",))
    body = trader.get("/api/v1/partners", params={"q": "알파"}).json()
    assert [i["partner_code"] for i in body["items"]] == ["PTN-S1"] and body["total"] == 1
    assert [
        i["partner_code"]
        for i in trader.get("/api/v1/partners", params={"type": "SUPPLIER"}).json()["items"]
    ] == ["PTN-S2"]
    # '%'는 와일드카드가 아니라 글자다 — 전건이 걸리지 않는다.
    assert [
        i["partner_code"] for i in trader.get("/api/v1/partners", params={"q": "%"}).json()["items"]
    ] == ["PTN-S3"]
    assert trader.get("/api/v1/partners", params={"type": "NOPE"}).status_code == 422

    create_sku("SKU-Q1", name_ko="비타민 세럼")
    create_sku("SKU-Q2", name_ko="토너")
    assert [
        i["sku_code"] for i in trader.get("/api/v1/skus", params={"q": "비타민"}).json()["items"]
    ] == ["SKU-Q1"]
    assert trader.get("/api/v1/skus", params={"q": "x" * 101}).status_code == 422


def test_bulk_prices_use_the_latest_effective_row_and_omit_missing() -> None:
    """벌크 단가: 발효일 ≤ 기준일 중 최신, 미발효·통화 없음은 결과에 없다(0으로 채우지 않는다)"""
    a, b, c = create_sku("SKU-P1"), create_sku("SKU-P2"), create_sku("SKU-P3")
    today = today_kst()
    with engine.begin() as conn:
        for sku, eff, amount in (
            (a, today - timedelta(days=30), 1000),
            (a, today - timedelta(days=1), 1200),
            (a, today + timedelta(days=5), 9999),  # 미래 발효 — 제외
            (b, today + timedelta(days=5), 500),  # 아직 발효 전 — 결과에 없어야 한다
        ):
            conn.execute(
                text(
                    "INSERT INTO sku_prices (sku_id, price_type, currency, amount, effective_from, version)"
                    " VALUES (:s, 'SALES', 'USD', :a, :e, 1)"
                ),
                {"s": sku, "a": amount, "e": eff},
            )
    with unit_of_work() as uow:
        found = prices_at(uow.session, sku_ids=[a, b, c], price_type="SALES", currency="usd")
    assert set(found) == {a} and found[a].amount == 1200


def test_cells_for_matches_the_matrix_colors_for_a_single_market() -> None:
    """준비도 단건 셀이 매트릭스와 같은 색이고, 삭제·미존재 SKU는 결과에 없다(평가 불능)"""
    create_market("US")
    profile = create_item_profile("PRF-CF")
    template = create_requirement_template("US", "리스팅", profile_id=profile)
    far = today_kst() + timedelta(days=500)
    green, _ = create_sku_with_axes("CF-GREEN", profile_id=profile)
    red, _ = create_sku_with_axes("CF-RED", profile_id=profile)
    gray, _ = create_sku_with_axes("CF-GRAY")
    gone, _ = create_sku_with_axes("CF-GONE", profile_id=profile)
    create_certification_instance(template, "SKU", green, status="APPROVED", expires_on=far)
    create_certification_instance(template, "SKU", red, status="NOT_STARTED")
    with engine.begin() as conn:
        conn.execute(text("UPDATE skus SET deleted_at = now() WHERE id = :i"), {"i": gone})

    cells = readiness.cells_for(sku_ids=[green, red, gray, gone, 999999], market_code="US")
    assert {k: v.summary.color for k, v in cells.items()} == {
        green: "GREEN",
        red: "RED",
        gray: "GRAY",
    }
    matrix = readiness.get_matrix(offset=0, limit=50)
    compared = 0
    for row in matrix.rows:
        if row.sku_id in cells:
            us = next(c for c in row.cells if c.market_code == "US")
            assert us.summary == cells[row.sku_id].summary  # 규칙이 한 곳이다
            compared += 1
    assert compared == 3  # 공회전 방지 — 비교가 실제로 일어났다

    with pytest.raises(AppError):
        readiness.cells_for(sku_ids=[green], market_code="ZZ")
    assert readiness.cells_for(sku_ids=[], market_code="US") == {}


def test_lock_true_takes_a_key_share_lock_that_conflicts_with_for_update() -> None:
    """lock=True는 FOR KEY SHARE다 — 유형 해제 임포트가 잡은 FOR UPDATE와 충돌해 대기한다(제거 시 실패)"""
    from sqlalchemy.exc import DBAPIError

    partner = create_partner("PTN-LK", name_ko="잠금", types=("SUPPLIER",))
    holder = engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM partners WHERE id=:i FOR UPDATE"), {"i": partner})
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            partners.require_partner_of_any_type(
                uow.session, partner, ("SUPPLIER",), field="f", type_label="공급사", lock=True
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
        # lock=False는 대기하지 않는다(읽기만) — 대조군
        with unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            partners.require_partner_of_any_type(
                uow.session, partner, ("SUPPLIER",), field="f", type_label="공급사", lock=False
            )
    finally:
        tx.rollback()
        holder.close()


def test_lock_true_does_not_block_the_credit_serialization_lock() -> None:
    """KEY SHARE는 FOR NO KEY UPDATE(여신 직렬화 경로)와 충돌하지 않는다 — 전표 생성이 확정을 막지 않는다(X-37)"""
    partner = create_partner("PTN-NK", name_ko="비블록", types=("BUYER",))
    holder = engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM partners WHERE id=:i FOR NO KEY UPDATE"), {"i": partner})
        with unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            partners.require_partner_of_any_type(
                uow.session, partner, ("BUYER",), field="f", type_label="바이어", lock=True
            )
    finally:
        tx.rollback()
        holder.close()
