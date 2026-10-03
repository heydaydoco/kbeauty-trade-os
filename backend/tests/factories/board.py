"""오더 보드(S3-1 PR-15a) 테스트 팩토리 — 보드 시딩(원시 SQL — 대량 카드)·벌크 호출·리포트 조회. 테스트 데이터는 전부 익명 합성이다.

원시 SQL 시딩은 **보드 조회 시험 전용**이다(인테이크·SO 생성 통로의 규칙은 각자의 테스트가 지킨다). 확정·벌크 시험은 실제 통로(`land`·`ready_so`)로 만든 대상을 쓴다.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from tests.factories.trade import create_buyer, idem, raw_so, unique
from tests.support.factories import create_market, create_user

BOARD = "/api/v1/order-board"
BULK = f"{BOARD}/bulk"
ITEMS = f"{BOARD}/items"
EXPORT = f"{BOARD}/export.csv"
SAVED = f"{BOARD}/saved-filters"

_po = itertools.count(1)


def board_user(*roles: RoleCode, name: str | None = None) -> int:
    return create_user(
        f"{unique('bd')}@example.com",
        roles=roles or (RoleCode.TRADE,),
        display_name=name or f"보드 사용자 {unique('n')}",
    )


def actor(user_id: int, *roles: RoleCode) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        email="board@example.com",
        display_name="보드 행위자",
        roles=frozenset(roles or (RoleCode.TRADE,)),
        session_id=0,
    )


def raw_intake(
    *,
    buyer: int,
    assignee: int,
    po_no: str | None = None,
    currency: str = "USD",
    market: str = "US",
    lines: list[tuple[int, int]] | None = None,
    created_at: datetime | None = None,
    status: str = "PENDING",
) -> int:
    """보드 시딩용 PENDING 인테이크(원시 SQL) — 라인 (수량, 단가) 목록. 품번은 미매핑(보드는 매핑을 보지 않는다)."""
    create_market(market)
    po = po_no or f"BPO-{next(_po):05d}"
    values: dict[str, Any] = {
        "buyer": buyer,
        "po": po,
        "key": po.upper().replace(" ", ""),
        "currency": currency,
        "market": market,
        "assignee": assignee,
        "n": len(lines or [(1, 100)]),
    }
    created = ", created_at" if created_at is not None else ""
    created_value = ", :created_at" if created_at is not None else ""
    if created_at is not None:
        values["created_at"] = created_at
    with owner_engine.begin() as connection:
        intake_id = int(
            connection.execute(
                text(
                    "INSERT INTO order_intakes (source_kind, extracted_snapshot, buyer_partner_id, buyer_po_no,"
                    " buyer_po_no_key, currency, dest_market_code, assignee_id, last_line_no"
                    f"{created}) VALUES ('MANUAL', '{{}}'::jsonb, :buyer, :po, :key, :currency, :market,"
                    f" :assignee, :n{created_value}) RETURNING id"
                ),
                values,
            ).scalar_one()
        )
        for line_no, (quantity, price) in enumerate(lines or [(1, 100)], start=1):
            connection.execute(
                text(
                    "INSERT INTO order_intake_lines (intake_id, currency, line_no, buyer_item_code, quantity,"
                    " unit_price_amount, requested_delivery_date) VALUES (:i, :c, :n, :code, :q, :p,"
                    " :d)"
                ),
                {
                    "i": intake_id,
                    "c": currency,
                    "n": line_no,
                    "code": f"RAW-{line_no}",
                    "q": quantity,
                    "p": price,
                    # DB CURRENT_DATE는 세션 시간대(UTC) 날짜라 KST 자정~09시에 하루 어긋난다 — 시험의 기대값과 같은 KST 기준으로 넣는다
                    "d": today_kst() + timedelta(days=30 + line_no),
                },
            )
    return intake_id


def seed_so(status: str, *, buyer: int, assignee: int, **kwargs: Any) -> int:
    """보드 시딩용 SO(원시 SQL — `raw_so`, 라인 없음). PO번호를 주면 정규화 키를 함께 채운다(PO 쌍 CHECK)."""
    if kwargs.get("buyer_po_no") is not None and "buyer_po_no_key" not in kwargs:
        from app.modules.trade_docs.buyer_po import po_columns

        kwargs["buyer_po_no"], kwargs["buyer_po_no_key"] = po_columns(kwargs["buyer_po_no"])
    return raw_so(status, buyer_partner_id=buyer, assignee_id=assignee, **kwargs)


def set_column(table: str, row_id: int, **values: Any) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    with owner_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {table} SET {assignments} WHERE id = :row_id"),
            {**values, "row_id": row_id},
        )


def scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def world_ids() -> tuple[int, int]:
    """(바이어, 무역 담당자) — 시딩 공용."""
    create_market("US")
    return create_buyer(), board_user(RoleCode.TRADE)


def bulk(
    client: TestClient,
    action: str,
    targets: list[dict[str, Any]],
    *,
    assignee_id: int | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    body: dict[str, Any] = {"action": action, "targets": targets}
    if assignee_id is not None:
        body["assignee_id"] = assignee_id
    return client.post(BULK, json=body, headers=headers or idem())


def target(kind: str, target_id: int, version: int) -> dict[str, Any]:
    return {"kind": kind, "id": target_id, "expected_version": version}


def column(board: dict[str, Any], stage: str) -> dict[str, Any]:
    found = [c for c in board["columns"] if c["stage"] == stage]
    assert len(found) == 1, stage
    return found[0]


def result_of(report: dict[str, Any], kind: str, target_id: int) -> dict[str, Any]:
    found = [r for r in report["results"] if r["kind"] == kind and r["id"] == target_id]
    assert len(found) == 1, (kind, target_id)
    return found[0]


def code_of(response: Any) -> str:
    return str(response.json()["error"]["code"])
