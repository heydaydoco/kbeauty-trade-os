"""수주 확정(S3-1 PR-12a) 테스트 팩토리 — 확정 가능한 기준 SO·확정/승인 요청 호출·증적 조회. 테스트 데이터는 전부 익명 합성이다.

`passing_so`(게이트 7종 PASS)는 직접(인테이크형) 수주라 Incoterms·환율이 비어 있다 — 확정은 동결 완결성(결제조건·Incoterms·환율·라인≥1)을 먼저 검사하므로 `ready_so`가
이를 채워 **확정 가능한 RECEIVED SO**를 만든다(채우는 UPDATE는 승인 전에 끝내야 한다 — 환율은 승인 결속 digest의 입력이다).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from tests.factories.approvals import add_line, count, make_user, set_credit_limit
from tests.factories.gates import passing_so
from tests.factories.trade import idem

SO = "/api/v1/sales-orders"


def complete_for_confirm(so_id: int) -> None:
    """Incoterms·환율을 채운다(동결 완결성 — 직접 수주는 비어 있다). KRW가 아닌 통화 기준(환율 1,350)."""
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE sales_orders SET incoterm_code = 'FOB', incoterm_place = 'Busan',"
                " incoterm_year = 2020, fx_rate = 1350, fx_rate_date = doc_date WHERE id = :i"
            ),
            {"i": so_id},
        )


def ready_so(
    *,
    limit: int | None = None,
    limit_currency: str = "USD",
    price: int = 1000,
    quantity: int = 5,
    buyer: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """확정 가능한 RECEIVED SO(게이트 7종 PASS·동결 완결). `limit`이 있으면 거래처 여신 한도를 건다(총액 = 단가×수량, 초과면 승인 게이트)."""
    so = passing_so(price=price, quantity=quantity, **kwargs)
    if buyer is not None:  # 다른 거래처로 만들 수는 없다(ORIGIN) — 지정은 `ready_so_for`를 쓴다
        raise ValueError("buyer는 ready_so_for를 사용하세요.")
    complete_for_confirm(so["id"])
    if limit is not None:
        set_credit_limit(so["buyer"], limit, limit_currency)
    return so


def ready_so_for(
    buyer: int, *, price: int = 1000, quantity: int = 5, po_no: str | None = None
) -> dict[str, Any]:
    """같은 거래처의 또 다른 확정 가능 SO(여신 합산·경합 시험) — 준비도 GREEN·LC·동결 완결."""
    from tests.factories.gates import set_readiness, set_terms
    from tests.factories.trade import create_direct_so, create_priced_sku, unique

    sku = create_priced_sku(amount=price)
    so = create_direct_so(
        buyer, [sku], quantity=quantity, buyer_po_no=po_no or unique("PO"), price_amount=price
    )
    set_readiness(sku, "GREEN")
    set_terms(so["id"], "LC")
    complete_for_confirm(so["id"])
    so["buyer"] = buyer
    so["sku_ids"] = [sku]
    return so


def so_version(so_id: int) -> int:
    return int(scalar("SELECT version FROM sales_orders WHERE id = :i", i=so_id))


def scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def rows(table: str, where: str = "true", **params: Any) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text(f"SELECT * FROM {table} WHERE {where} ORDER BY id"), params
            ).mappings()
        ]


def so_row(so_id: int) -> dict[str, Any]:
    return rows("sales_orders", "id = :i", i=so_id)[0]


def evaluations(so_id: int, outcome: str | None = None) -> list[dict[str, Any]]:
    where = "subject_type = 'SALES_ORDER' AND subject_id = :i" + (
        " AND outcome = :o" if outcome else ""
    )
    return rows("gate_evaluations", where, i=so_id, **({"o": outcome} if outcome else {}))


def status_log(so_id: int) -> list[dict[str, Any]]:
    return rows("sales_order_status_log", "sales_order_id = :i", i=so_id)


def approvals_of(so_id: int) -> list[dict[str, Any]]:
    return rows("approvals", "target_type = 'SALES_ORDER' AND target_id = :i", i=so_id)


def confirm(
    client: TestClient,
    so_id: int,
    *,
    version: int | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """`POST /sales-orders/{id}/confirm` — version을 안 주면 DB의 현재 값."""
    return client.post(
        f"{SO}/{so_id}/confirm",
        json={"version": version if version is not None else so_version(so_id)},
        headers=headers or idem(),
    )


def request_credit_approval(
    client: TestClient,
    so_id: int,
    *,
    version: int | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """`POST /sales-orders/{id}/approval-requests`."""
    return client.post(
        f"{SO}/{so_id}/approval-requests",
        json={"version": version if version is not None else so_version(so_id)},
        headers=headers or idem(),
    )


def error_of(response: Any) -> dict[str, Any]:
    body: dict[str, Any] = response.json()["error"]
    return body


def code_of(response: Any) -> str:
    return str(error_of(response)["code"])


def ensure_approval_line(role: str = "TRADE") -> None:
    """임계 0 결재선(모든 초과를 `role`이 결재) — 이미 있으면 건너뛴다."""
    if count("approval_lines") == 0:
        add_line(0, role=role)


def new_actor(*roles: RoleCode) -> AuthenticatedUser:
    return make_user(*roles)


def confirm_service(
    actor: AuthenticatedUser, so_id: int, *, version: int | None = None, key: str | None = None
) -> tuple[int, dict[str, Any]]:
    """서비스 직접 호출(스레드·동시성 테스트용 — 자기 UoW를 연다)."""
    from app.modules.trade_chain import confirm as confirm_module
    from tests.factories.trade import unique

    return confirm_module.confirm_sales_order(
        actor=actor,
        idempotency_key=key or unique("cf"),
        so_id=so_id,
        version=version if version is not None else so_version(so_id),
    )


def request_service(
    actor: AuthenticatedUser, so_id: int, *, version: int | None = None
) -> tuple[int, dict[str, Any]]:
    from app.modules.trade_chain import approval_requests
    from tests.factories.trade import unique

    return approval_requests.request_credit_approval(
        actor=actor,
        idempotency_key=unique("rq"),
        so_id=so_id,
        version=version if version is not None else so_version(so_id),
    )
