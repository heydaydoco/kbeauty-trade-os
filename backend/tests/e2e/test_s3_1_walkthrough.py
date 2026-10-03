"""H·J. S3-1 입구~출구 워크스루 고정 — PR-16 렌즈 11 실 브라우저 관통과 같은 흐름을 API 수준에서 반복 가능하게 (design-integrated 362행 PR-16).

흐름(실 HTTP 스택 — TestClient·실 DB·사람 3명):
  CSV 업로드 → 검토(품번 미매핑) → 품번 등록 → 재해석 → 인테이크 확정(SO 접수) → SO 편집(환율·결제조건·인코텀즈)
  → override(가격 편차=무역, 시장 준비=관리자) → 여신 초과 확정 거부 → 승인 요청 → (기안자 자기 결정 403) → 다른 사용자 승인 → 확정
  / QT 작성·발행 → PI → SO(PI 참조) → PI 선수금 게이트 차단 → 입금 → 확정 → QT 수주전환.
H 대표는 승인 우회 차단·다른 사용자 결재·승인 소비 후 불변, J 대표는 같은 키 재생(확정·입금·업로드)이 업무를 두 번 일으키지 않는 것.
테스트 데이터는 전부 익명 합성이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from tests.factories.approvals import add_line, client_for, make_user, set_credit_limit
from tests.factories.gates import find, override_body, set_policy
from tests.factories.intake import (
    INTAKES,
    csv_bytes,
    future,
    past,
    scalar,
    upload_csv,
)
from tests.factories.trade import (
    create_bank_account,
    create_buyer,
    create_priced_sku,
    idem,
    unique,
)
from tests.support.factories import create_market

pytestmark = [pytest.mark.group_h, pytest.mark.group_j]

SO = "/api/v1/sales-orders"


@pytest.fixture
def people() -> Iterator[dict[str, TestClient]]:
    """무역담당(기안·확정)·관리자(준비도 override)·결재자(제2 ADMIN — 다른 사용자 결재)."""
    users = {
        "trade": make_user(RoleCode.TRADE),
        "admin": make_user(RoleCode.ADMIN),
        "approver": make_user(RoleCode.ADMIN),
    }
    with ExitStack() as stack:
        yield {name: stack.enter_context(client_for(user)) for name, user in users.items()}


def _ok(response: Any, *codes: int) -> dict[str, Any]:
    assert response.status_code in (codes or (200, 201)), response.text
    body: dict[str, Any] = response.json()
    return body


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _gates(client: TestClient, so_id: int) -> dict[str, Any]:
    return _ok(client.get(f"{SO}/{so_id}/gates"))


def _override(client: TestClient, so_id: int, gate: str, line_id: int | None) -> None:
    item = find(_gates(client, so_id), gate, line_id)
    _ok(
        client.post(
            f"{SO}/{so_id}/gate-overrides",
            json=override_body(item, "워크스루 — 사유를 남긴 예외 통과"),
            headers=idem(),
        ),
        200,
        201,
    )


def _version(client: TestClient, so_id: int) -> int:
    return int(_ok(client.get(f"{SO}/{so_id}"))["version"])


def _world() -> dict[str, Any]:
    """운영 개시 절차의 최소 상태 — 결재선(USD 임계 0·ADMIN)·정책(PI 차단·편차 5%)·시장·판가 SKU(10.00 USD)·여신 한도 1,000 USD 바이어."""
    create_market("US")
    add_line(0, currency="USD", role="ADMIN")
    set_policy("pi_advance_gate_mode", "BLOCK")
    set_policy("price_deviation_tolerance_bp", 500)
    code = unique("BUY")
    buyer = create_buyer(code=code)
    set_credit_limit(buyer, 100_000)  # 1,000.00 USD(최소단위)
    sku = create_priced_sku(amount=1000)
    return {
        "buyer": buyer,
        "buyer_code": code,
        "sku": sku,
        "po": unique("PO"),
        "item": unique("BX"),
    }


def _csv(w: dict[str, Any]) -> bytes:
    """9열 1행 — 100개 × 12.00 USD = 1,200.00 USD(한도 1,000 초과·판가 10.00 대비 +20%)."""
    return csv_bytes(
        [[w["buyer_code"], w["po"], past(), "USD", "US", w["item"], "100", "12.00", future(45)]]
    )


def _land_and_edit(people: dict[str, TestClient], w: dict[str, Any]) -> dict[str, Any]:
    """CSV 업로드 → 검토 → 품번 등록 → 재해석 → 인테이크 확정 → SO 편집. 편집된 SO 상세를 돌려준다."""
    trade = people["trade"]
    report = _ok(upload_csv(trade, _csv(w)), 201)
    intake_id = int(report["intakes"][0]["id"])
    intake = _ok(trade.get(f"{INTAKES}/{intake_id}"))
    assert intake["status"] == "PENDING" and intake["lines"][0]["sku_id"] is None  # 검토: 미매핑

    _ok(
        trade.post(
            f"/api/v1/partners/{w['buyer']}/item-codes",
            json={"sku_id": w["sku"], "buyer_item_code": w["item"]},
            headers=idem(),
        ),
        201,
    )
    intake = _ok(
        trade.post(
            f"{INTAKES}/{intake_id}/resolve", json={"version": intake["version"]}, headers=idem()
        )
    )
    assert intake["lines"][0]["sku_id"] == w["sku"]
    landed = _ok(
        trade.post(
            f"{INTAKES}/{intake_id}/confirm", json={"version": intake["version"]}, headers=idem()
        )
    )
    so_id = int(landed["sales_order_id"])

    today = today_kst().isoformat()
    edited = _ok(
        trade.patch(
            f"{SO}/{so_id}",
            json={
                "version": _version(trade, so_id),
                "fx_rate": "1350",
                "fx_rate_date": today,
                "payment_terms": {
                    "payment_type": "TT_DEFERRED",
                    "balance_anchor": "BL_DATE",
                    "balance_days": 30,
                },
                "incoterm": {"code": "FOB", "place": "Busan", "year": 2020},
            },
            headers=idem(),
        )
    )
    assert edited["status"] == "RECEIVED"
    return edited


def test_entry_to_exit_credit_approval_by_another_user_then_immutable(
    people: dict[str, TestClient],
) -> None:
    """H — 입구(CSV)~출구(확정): override 2종 뒤에도 여신 초과는 확정 거부, 기안자(ADMIN 포함)는 자기 승인 불가, 다른 사용자 승인 후에만 확정되고 확정 SO는 고칠 수 없다"""
    w = _world()
    trade, admin, approver = people["trade"], people["admin"], people["approver"]
    so = _land_and_edit(people, w)
    so_id, line_id = int(so["id"]), int(so["lines"][0]["id"])

    # override: 가격 편차는 무역이, 시장 준비(요건 0건=판정 불가)는 관리자만.
    item = find(_gates(trade, so_id), "MARKET_READINESS", line_id)
    denied = trade.post(f"{SO}/{so_id}/gate-overrides", json=override_body(item), headers=idem())
    assert denied.status_code == 403, denied.text
    _override(trade, so_id, "PRICE_DEVIATION", line_id)
    _override(admin, so_id, "MARKET_READINESS", line_id)

    # 여신 초과 — override로 풀 수 없고 확정은 거부(실패도 증적으로 커밋).
    credit = find(_gates(trade, so_id), "CREDIT", None)
    assert credit["level"] == "BLOCK" and credit["resolution"] == "APPROVAL"
    cannot = trade.post(f"{SO}/{so_id}/gate-overrides", json=override_body(credit), headers=idem())
    assert cannot.status_code in (403, 409, 422), cannot.text
    refused = trade.post(
        f"{SO}/{so_id}/confirm", json={"version": _version(trade, so_id)}, headers=idem()
    )
    assert refused.status_code == 409, refused.text
    assert _ok(trade.get(f"{SO}/{so_id}"))["status"] == "RECEIVED"

    # 승인 요청(기안) → 기안자 본인 결정 403 → 다른 사용자(결재자) 승인.
    requested = _ok(
        trade.post(
            f"{SO}/{so_id}/approval-requests",
            json={"version": _version(trade, so_id)},
            headers=idem(),
        ),
        200,
        201,
    )
    approval_id = int(requested["id"])
    detail = _ok(approver.get(f"/api/v1/approvals/{approval_id}"))
    self_decide = trade.post(
        f"/api/v1/approvals/{approval_id}/decisions",
        json={"verb": "APPROVE", "version": detail["version"]},
        headers=idem(),
    )
    assert self_decide.status_code == 403, self_decide.text
    _ok(
        approver.post(
            f"/api/v1/approvals/{approval_id}/decisions",
            json={"verb": "APPROVE", "reason": "초과분 200 USD 승인", "version": detail["version"]},
            headers=idem(),
        )
    )

    confirmed = _ok(
        trade.post(
            f"{SO}/{so_id}/confirm", json={"version": _version(trade, so_id)}, headers=idem()
        )
    )
    assert confirmed["sales_order"]["status"] == "CONFIRMED"
    after = _ok(trade.get(f"{SO}/{so_id}"))
    assert after["status"] == "CONFIRMED" and after["confirmed_at"] is not None
    assert _ok(approver.get(f"/api/v1/approvals/{approval_id}"))["status"] == "CONSUMED"

    # 확정 후 불변(DoD ④): 환율·단가 편집은 409, 금액은 그대로.
    frozen = trade.patch(
        f"{SO}/{so_id}", json={"version": after["version"], "fx_rate": "1400"}, headers=idem()
    )
    assert frozen.status_code == 409, frozen.text
    assert _ok(trade.get(f"{SO}/{so_id}"))["fx_rate"] == after["fx_rate"]


def test_entry_to_exit_replays_never_double_the_business(people: dict[str, TestClient]) -> None:
    """J — 같은 파일 재업로드·같은 키 확정·같은 키 입금은 업무를 두 번 일으키지 않는다. 참조 사슬(QT→PI→SO)은 PI 게이트 차단 → 입금 → 확정 → QT 수주전환"""
    w = _world()
    trade, admin = people["trade"], people["admin"]

    # 같은 CSV 파일 재업로드 = 인테이크 1건(파일 해시 멱등 — 새 키여도 FILE.DUPLICATE).
    content = _csv(w)
    _ok(upload_csv(trade, content), 201)
    again = upload_csv(trade, content)
    assert again.status_code == 409 and _code(again) == "ORDER_INTAKE.FILE.DUPLICATE", again.text
    assert int(scalar("SELECT count(*) FROM order_intakes")) == 1

    # 참조 사슬: 여신 미관리 바이어 + 선수금 T/T 30%.
    buyer = create_buyer()
    bank = create_bank_account("USD")
    qt = _ok(
        trade.post(
            "/api/v1/quotations",
            json={
                "buyer_partner_id": buyer,
                "dest_market_code": "US",
                "currency": "USD",
                "valid_until": future(30),
                "fx_rate": "1350",
                "fx_rate_date": today_kst().isoformat(),
                "payment_terms": {
                    "payment_type": "TT_ADVANCE",
                    "advance_pct": "30",
                    "balance_anchor": "BL_DATE",
                    "balance_days": 30,
                },
                "incoterm": {"code": "FOB", "place": "Busan", "year": 2020},
                "lines": [{"sku_id": w["sku"], "quantity": 50}],
            },
            headers=idem(),
        ),
        201,
    )
    qt = _ok(
        trade.post(
            f"/api/v1/quotations/{qt['id']}/issue", json={"version": qt["version"]}, headers=idem()
        )
    )
    pi = _ok(
        trade.post(
            f"/api/v1/quotations/{qt['id']}/proforma-invoices",
            json={"version": qt["version"], "valid_until": future(20), "bank_account_id": bank},
            headers=idem(),
        ),
        201,
    )
    so = _ok(
        trade.post(
            f"/api/v1/proforma-invoices/{pi['id']}/sales-orders",
            json={"version": pi["version"]},
            headers=idem(),
        ),
        201,
    )
    so_id, line_id = int(so["id"]), int(so["lines"][0]["id"])
    _override(admin, so_id, "MARKET_READINESS", line_id)

    # PI 게이트 차단(미입금).
    blocked = trade.post(
        f"{SO}/{so_id}/confirm", json={"version": _version(trade, so_id)}, headers=idem()
    )
    assert blocked.status_code == 409, blocked.text
    assert find(_gates(trade, so_id), "PI_DEPOSIT", None)["level"] == "BLOCK"

    # 입금 — 같은 키 2회 = 1행(재생), 선수금 150.00 USD 충족.
    pay_key = idem()
    payment = {
        "received_amount": "150.00",
        "received_currency": "USD",
        "received_on": today_kst().isoformat(),
        "reference": "은행 입금 통지 WIRE-0001",
    }
    first = trade.post(
        f"/api/v1/proforma-invoices/{pi['id']}/payments", json=payment, headers=pay_key
    )
    second = trade.post(
        f"/api/v1/proforma-invoices/{pi['id']}/payments", json=payment, headers=pay_key
    )
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json() == second.json()
    assert int(scalar("SELECT count(*) FROM payments WHERE pi_id = :p", p=pi["id"])) == 1
    assert find(_gates(trade, so_id), "PI_DEPOSIT", None)["level"] == "PASS"

    # 확정 — 같은 키 2회 = 한 번 확정(이력 확정 행 1개), QT는 수주전환.
    confirm_key = idem()
    body = {"version": _version(trade, so_id)}
    one = trade.post(f"{SO}/{so_id}/confirm", json=body, headers=confirm_key)
    two = trade.post(f"{SO}/{so_id}/confirm", json=body, headers=confirm_key)
    assert one.status_code == two.status_code == 200, (one.text, two.text)
    assert one.json() == two.json()
    assert (
        int(
            scalar(
                "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :s AND to_status = 'CONFIRMED'",
                s=so_id,
            )
        )
        == 1
    )
    assert _ok(trade.get(f"/api/v1/quotations/{qt['id']}"))["status"] == "CONVERTED"
