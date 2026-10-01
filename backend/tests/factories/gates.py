"""게이트(S3-1 PR-11a) 테스트 팩토리 — 게이트 7종이 전부 통과하는 기준 SO와 게이트별 조건 변형 헬퍼. 테스트 데이터는 전부 익명 합성이다."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.gates.types import GateCode, GateOutcome, GatePhase
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.trade_chain import gate_flow
from tests.factories.trade import (
    create_buyer,
    create_direct_so,
    create_priced_sku,
    map_buyer_item_code,
    unique,
)
from tests.support.factories import (
    create_certification_instance,
    create_item_profile,
    create_market,
    create_requirement_template,
)

FAR = date(2027, 12, 31)

#: 준비도 색 → 요건 인스턴스 상태(None = 품목군 없음 → 필수 요건 0건 = GRAY).
_COLOR_STATUS = {"GREEN": "APPROVED", "YELLOW": "PREPARING", "RED": "NOT_STARTED"}


def set_readiness(sku_id: int, color: str, market: str = "US") -> None:
    """SKU×시장의 준비도 셀 색을 만든다 — 품목군+SKU 요건 템플릿+인증 인스턴스(상태로 색 결정). GRAY는 품목군 없음."""
    if color == "GRAY":
        return
    create_market(market)
    profile = create_item_profile(unique("PRF"))
    template = create_requirement_template(
        market, name=unique("REQ"), applies_to="SKU", profile_id=profile
    )
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE skus SET item_profile_id = :p WHERE id = :s"), {"p": profile, "s": sku_id}
        )
    create_certification_instance(
        template,
        "SKU",
        sku_id,
        status=_COLOR_STATUS[color],
        expires_on=FAR if color == "GREEN" else None,
    )


def set_moq(sku_id: int, moq: int | None) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text("UPDATE skus SET moq = :m WHERE id = :s"), {"m": moq, "s": sku_id})


def set_policy(key: str, value: int | str | None) -> None:
    """정책 행을 직접 저장(소유자 권한) — None이면 행을 지워 미설정으로 되돌린다."""
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM policy_settings WHERE policy_key = :k"), {"k": key})
        if value is None:
            return
        column = "value_text" if isinstance(value, str) else "value_int"
        connection.execute(
            text(f"INSERT INTO policy_settings (policy_key, {column}) VALUES (:k, :v)"),
            {"k": key, "v": value},
        )


def set_terms(so_id: int, payment_type: str | None, *, bp: int = 3000) -> None:
    """SO 결제조건을 직접 바꾼다(결제조건 형태 CHECK를 만족하는 값으로)."""
    shapes: dict[str | None, str] = {
        None: "payment_type = NULL, advance_pct_bp = NULL, balance_anchor = NULL, balance_days = NULL",
        "LC": "payment_type = 'LC', advance_pct_bp = NULL, balance_anchor = NULL, balance_days = NULL",
        "TT_DEFERRED": "payment_type = 'TT_DEFERRED', advance_pct_bp = NULL, balance_anchor = 'ETD_DATE', balance_days = -7",
        "TT_ADVANCE": f"payment_type = 'TT_ADVANCE', advance_pct_bp = {bp}, balance_anchor = 'ETD_DATE', balance_days = -7",
    }
    with owner_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE sales_orders SET {shapes[payment_type]} WHERE id = :i"), {"i": so_id}
        )


def set_line(
    so_id: int,
    line_no: int = 1,
    *,
    unit_price: int | None = None,
    quantity: int | None = None,
    list_price: int | str | None = "keep",
    sku_id: int | None = None,
    buyer_item_code: str | str | None = "keep",
) -> None:
    """SO 라인 값을 직접 바꾼다(라인금액·헤더 합계를 맞춰 CHECK를 지킨다 — 무상 전환은 `make_free`)."""
    with owner_engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT id, quantity, unit_price_amount FROM sales_order_lines"
                    " WHERE so_id = :s AND line_no = :n AND deleted_at IS NULL"
                ),
                {"s": so_id, "n": line_no},
            )
            .mappings()
            .one()
        )
        price = row["unit_price_amount"] if unit_price is None else unit_price
        qty = row["quantity"] if quantity is None else quantity
        sets = ["unit_price_amount = :p", "quantity = :q", "line_amount = :p * :q"]
        params: dict[str, Any] = {"p": price, "q": qty, "i": row["id"]}
        if list_price != "keep":
            sets.append("list_price_amount = :lp")
            params["lp"] = list_price
        if sku_id is not None:
            sets.append("sku_id = :sk")
            params["sk"] = sku_id
        if buyer_item_code != "keep":
            sets.append("buyer_item_code = :bc")
            params["bc"] = buyer_item_code
        connection.execute(
            text(f"UPDATE sales_order_lines SET {', '.join(sets)} WHERE id = :i"), params
        )
        connection.execute(
            text(
                "UPDATE sales_orders SET total_amount = (SELECT COALESCE(SUM(line_amount), 0)"
                " FROM sales_order_lines WHERE so_id = :s AND deleted_at IS NULL),"
                " version = version + 1 WHERE id = :s"
            ),
            {"s": so_id},
        )


def make_free(so_id: int, line_no: int = 1) -> None:
    """라인을 무상으로 — 단가 0·무상 표지·사유(CHECK 3종을 한 번에 만족)."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE sales_order_lines SET unit_price_amount = 0, line_amount = 0, is_free = true,"
                " price_basis = 'MANUAL', price_reason = '샘플' WHERE so_id = :s AND line_no = :n"
            ),
            {"s": so_id, "n": line_no},
        )
        connection.execute(
            text(
                "UPDATE sales_orders SET total_amount = (SELECT COALESCE(SUM(line_amount), 0)"
                " FROM sales_order_lines WHERE so_id = :s AND deleted_at IS NULL) WHERE id = :s"
            ),
            {"s": so_id},
        )


def passing_so(
    *,
    quantity: int = 5,
    price: int = 1000,
    moq: int | None = None,
    market_color: str = "GREEN",
    terms: str | None = "LC",
    po_no: str | None = "auto",
    map_code: bool = False,
    skus: int = 1,
) -> dict[str, Any]:
    """게이트 7종이 전부 PASS인 RECEIVED SO — 직접(인테이크형) 수주 + 준비도 GREEN + LC + 여신 미관리 + PO번호 있음.

    `map_code=True`면 라인에 바이어 품번과 매핑을 건다(품번 게이트의 매핑 일치 분기). 반환: SO 상세 본문 + `buyer`·`sku_ids`.
    """
    buyer = create_buyer()
    sku_ids = [create_priced_sku(amount=price) for _ in range(skus)]
    so = create_direct_so(
        buyer,
        sku_ids,
        quantity=quantity,
        buyer_po_no=unique("PO") if po_no == "auto" else po_no,
        price_amount=price,
    )
    for sku in sku_ids:
        set_readiness(sku, market_color)
        if moq is not None:
            set_moq(sku, moq)
    set_terms(so["id"], terms)
    if map_code:
        map_buyer_item_code(buyer, sku_ids[0], "BUYER-CODE-1")
        set_line(so["id"], 1, buyer_item_code="BUYER-CODE-1")
    so["buyer"] = buyer
    so["sku_ids"] = sku_ids
    return so


def evaluate(
    so_id: int,
    *,
    only: list[GateCode] | None = None,
    authoritative: bool = False,
    phase: GatePhase = GatePhase.CONFIRM,
) -> list[GateOutcome]:
    """SO를 평가해 결과 목록을 돌려준다(쓰기 없음). `phase=INTAKE`는 평가 틀을 직접 부른다(SO 평가 진입점은 확정 시점 전용)."""
    with unit_of_work() as uow:
        if phase is GatePhase.CONFIRM:
            return gate_flow.evaluate_sales_order(
                uow.session, so_id, authoritative=authoritative, only=only
            ).outcomes
        from app.modules.gates import service as gates_service
        from app.modules.trade_chain import gate_evaluators

        subject = gate_evaluators.subject_from_sales_order(
            uow.session, so_id, authoritative=authoritative
        )
        return gates_service.evaluate_all(uow.session, subject, phase, only=only)


def one(outcomes: list[GateOutcome], gate: GateCode, line_id: int | None = None) -> GateOutcome:
    """(게이트, 라인) 결과 1건 — 없으면 AssertionError."""
    found = [o for o in outcomes if o.gate_code == gate.value and o.line_id == line_id]
    assert len(found) == 1, (gate, line_id, [(o.gate_code, o.line_id) for o in outcomes])
    return found[0]


def gate_of(outcomes: list[GateOutcome], gate: GateCode) -> list[GateOutcome]:
    return [o for o in outcomes if o.gate_code == gate.value]


def line_ids(so_id: int) -> list[int]:
    with owner_engine.connect() as connection:
        return [
            int(r[0])
            for r in connection.execute(
                text(
                    "SELECT id FROM sales_order_lines WHERE so_id = :s AND deleted_at IS NULL"
                    " ORDER BY line_no, id"
                ),
                {"s": so_id},
            )
        ]


def actor_of_user(user_id: int, *roles: RoleCode) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id,
        email="gate@example.com",
        display_name="게이트",
        roles=frozenset(roles),
        session_id=0,
    )


def gates_of(client: TestClient, so_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/sales-orders/{so_id}/gates")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def find(report: dict[str, Any], gate: str, line_id: int | None = None) -> dict[str, Any]:
    found = [g for g in report["gates"] if g["gate_code"] == gate and g["line_id"] == line_id]
    assert len(found) == 1, (
        gate,
        line_id,
        [(g["gate_code"], g["line_id"]) for g in report["gates"]],
    )
    return found[0]


def override_body(
    item: dict[str, Any], reason: str = "고객 사정으로 예외 처리", **overrides: Any
) -> dict[str, Any]:
    """GET 결과 항목에서 override 요청 본문을 만든다."""
    body: dict[str, Any] = {
        "gate_code": item["gate_code"],
        "line_id": item["line_id"],
        "basis_hash": item["basis_hash"],
        "reason": reason,
    }
    body.update(overrides)
    return body


def scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()
