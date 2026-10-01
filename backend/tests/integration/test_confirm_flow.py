"""A. 수주 확정 전수 — 확정 증적·게이트 조합·QT 수주전환·할당 포트·outbox·멱등·입력 검증·역할 (S3-1 PR-12a / design-integrated §2.10 / design-D D5 · design-E E4).

실 HTTP·실 DB. 확정은 한 트랜잭션에서 **게이트 7종 재평가 → (승인 소비) → SO 증적 3열+동결 → 증거 스냅샷 → QT 수렴 → 할당 포트**를 끝내고, 거부(미해소 게이트)는 **커밋된 BLOCKED 증거**를 남기고 409다.
승인·여신 초과 경로(H)는 `test_confirm_credit_approval.py`, 동시성(J)은 `test_confirm_concurrency.py`다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from tests.factories.approvals import audit_actions, count
from tests.factories.confirm import (
    approvals_of,
    code_of,
    complete_for_confirm,
    confirm,
    confirm_service,
    error_of,
    evaluations,
    ready_so,
    scalar,
    so_row,
    so_version,
    status_log,
)
from tests.factories.gates import (
    find,
    gates_of,
    line_ids,
    override_body,
    set_line,
    set_moq,
    set_policy,
    set_readiness,
    set_terms,
)
from tests.factories.trade import (
    create_so_from_qt_via_api,
    idem,
    issued_quotation,
    logged_in,
)

pytestmark = pytest.mark.group_a

TRADE = RoleCode.TRADE
ADMIN = RoleCode.ADMIN
SO = "/api/v1/sales-orders"


def _blocked(response: Any) -> dict[str, dict[str, Any]]:
    """409 GATE_BLOCKED 응답의 미해소 게이트 → {게이트 코드: 항목}."""
    assert response.status_code == 409, response.text
    assert code_of(response) == "TRADE_CHAIN.CONFIRM.GATE_BLOCKED", response.text
    return {g["gate_code"]: g for g in error_of(response)["detail"]["blocked_gates"]}


def _assert_untouched(so_id: int) -> None:
    """거부된 확정은 SO를 건드리지 않는다 — 접수 상태·확정 시각·증적 3열 NULL·상태이력은 탄생 1행뿐."""
    row = so_row(so_id)
    assert row["status"] == "RECEIVED" and row["confirmed_at"] is None
    assert (row["credit_verdict"], row["credit_approval_id"], row["pi_gate_verdict"]) == (
        None,
        None,
        None,
    )
    assert len(status_log(so_id)) == 1
    assert evaluations(so_id, "CONFIRMED") == []


# ══ 성공 — 증적·이력·이벤트 ═════════════════════════════════════════════════════


def test_confirming_a_passing_order_freezes_it_and_records_every_piece_of_evidence() -> None:
    """게이트 7종이 전부 통과하는 접수 SO를 확정하면 200 — 상태 CONFIRMED·확정 시각·증적 3열(NOT_MANAGED·NOT_APPLICABLE)·
    CONFIRMED 증거 스냅샷 1행·상태이력 1행(approval_id NULL·automatic=false)·status_changed 이벤트 1건·할당 포트 NOT_IMPLEMENTED·게이트 결과 전건 응답"""
    so = ready_so()
    with logged_in(TRADE) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sales_order"]["status"] == "CONFIRMED" and body["sales_order"]["confirmed_at"]
    assert body["sales_order"]["credit_verdict"] == "NOT_MANAGED"
    assert body["sales_order"]["pi_gate_verdict"] == "NOT_APPLICABLE"
    assert body["sales_order"]["credit_approval_id"] is None
    assert body["allocation"]["status"] == "NOT_IMPLEMENTED" and body["allocation"]["note"]
    assert {
        "ITEM_MAPPING",
        "PRICE_DEVIATION",
        "CREDIT",
        "MARKET_READINESS",
        "MOQ",
        "PI_DEPOSIT",
    } <= {g["gate_code"] for g in body["gates"]}
    assert all(g["settlement"] == "NOT_REQUIRED" for g in body["gates"])  # 전부 통과 — 해소 필요 0

    row = so_row(so["id"])
    assert row["status"] == "CONFIRMED" and row["confirmed_at"] is not None
    assert (row["credit_verdict"], row["pi_gate_verdict"]) == ("NOT_MANAGED", "NOT_APPLICABLE")
    (evidence,) = evaluations(so["id"], "CONFIRMED")
    assert evidence["id"] == body["evaluation_id"]
    assert evidence["results"]["credit_verdict"] == "NOT_MANAGED"
    assert (
        evidence["results"]["used_override_ids"] == []
        and evidence["results"]["approval_id"] is None
    )
    assert len(evidence["input_digest"]) == 64
    log = status_log(so["id"])
    assert [(r["from_status"], r["to_status"]) for r in log] == [
        (None, "RECEIVED"),
        ("RECEIVED", "CONFIRMED"),
    ]
    assert log[-1]["approval_id"] is None and log[-1]["automatic"] is False
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE aggregate_type = 'sales_orders' AND aggregate_id = :i"
            " AND event_type = 'sales_orders.sales_order.status_changed'",
            i=so["id"],
        )
        == 1
    )


def test_a_confirmed_order_within_the_credit_limit_is_recorded_as_within_limit() -> None:
    """한도 이내(한도 100,000·총액 5,000) 확정은 credit_verdict=WITHIN_LIMIT — 승인 행·승인 이벤트는 만들어지지 않는다"""
    so = ready_so(limit=100_000)
    with logged_in(TRADE) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    assert response.json()["sales_order"]["credit_verdict"] == "WITHIN_LIMIT"
    assert approvals_of(so["id"]) == [] and count("approval_events") == 0


def test_the_status_log_endpoint_exposes_the_consumed_approval_column() -> None:
    """상태이력 API가 `approval_id`를 싣는다(확정 행 외에는 None) — 확정 후 이력 2행"""
    so = ready_so()
    with logged_in(TRADE) as client:
        assert confirm(client, so["id"]).status_code == 200
        response = client.get(f"{SO}/{so['id']}/status-log")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["to_status"] for i in items] == ["CONFIRMED", "RECEIVED"]
    assert all("approval_id" in i and i["approval_id"] is None for i in items)


# ══ 멱등·더블클릭 ═══════════════════════════════════════════════════════════════


def test_the_same_idempotency_key_replays_the_first_result_without_a_second_confirmation() -> None:
    """같은 키 재전송은 최초 응답을 그대로 재생한다 — 이력·증거·이벤트가 늘지 않는다"""
    so = ready_so()
    key = idem()
    version = so_version(so["id"])
    with logged_in(TRADE) as client:
        first = confirm(client, so["id"], version=version, headers=key)
        again = confirm(client, so["id"], version=version, headers=key)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(status_log(so["id"])) == 2 and len(evaluations(so["id"], "CONFIRMED")) == 1


def test_a_second_confirmation_with_a_new_key_is_rejected_and_changes_nothing() -> None:
    """다른 키의 더블클릭(오래된 version) = 409 version 충돌, 최신 version으로 다시 눌러도 이미 확정이라 409 NOT_ALLOWED — 증거·이력은 그대로"""
    so = ready_so()
    stale = so_version(so["id"])
    with logged_in(TRADE) as client:
        assert confirm(client, so["id"], version=stale).status_code == 200
        conflict = confirm(client, so["id"], version=stale)
        assert conflict.status_code == 409
        assert code_of(conflict) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
        again = confirm(client, so["id"])
        assert again.status_code == 409 and code_of(again) == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert len(status_log(so["id"])) == 2 and len(evaluations(so["id"], "CONFIRMED")) == 1


# ══ 게이트별 거부 — 커밋된 BLOCKED 증거 ═══════════════════════════════════════════


def _price_block() -> int:
    so = ready_so(price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=1100, list_price=1000)
    return int(so["id"])


def _moq_block() -> int:
    so = ready_so(quantity=5)
    for sku in so["sku_ids"]:
        set_moq(sku, 100)
    return int(so["id"])


def _readiness_block() -> int:
    return int(ready_so(market_color="RED")["id"])


def _pi_block() -> int:
    so = ready_so(terms="TT_ADVANCE")  # PI 없는 선수금 SO — 정책 미설정=BLOCK(PI_MISSING)
    return int(so["id"])


def _credit_unevaluable() -> int:
    # 한도 통화(EUR)와 전표 통화(USD)가 달라 환산 불가 — 승인으로 우회할 수 없는 UNKNOWN
    return int(ready_so(limit=100_000, limit_currency="EUR")["id"])


def _item_mapping_block() -> int:
    so = ready_so(map_code=True)
    with owner_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM customer_item_codes WHERE partner_id = :p"), {"p": so["buyer"]}
        )
    return int(so["id"])


@pytest.mark.parametrize(
    ("make", "gate", "reason", "resolution"),
    [
        (_price_block, "PRICE_DEVIATION", None, "OVERRIDE"),
        (_moq_block, "MOQ", None, "OVERRIDE"),
        (_readiness_block, "MARKET_READINESS", None, "OVERRIDE"),
        (_pi_block, "PI_DEPOSIT", "PI_MISSING", "OVERRIDE"),
        (_credit_unevaluable, "CREDIT", "CURRENCY_NOT_CONVERTIBLE", "NONE"),
        (_item_mapping_block, "ITEM_MAPPING", None, "NONE"),
    ],
    ids=["가격편차", "MOQ", "시장준비도", "PI입금", "여신평가불능", "품번매핑"],
)
def test_each_unresolved_gate_blocks_the_confirmation_and_leaves_committed_evidence(
    make: Any, gate: str, reason: str | None, resolution: str
) -> None:
    """미해소 게이트 1종 = 409 GATE_BLOCKED(`blocked_gates`에 그 게이트의 결과·해소 방식·사유 코드) · SO는 그대로 · **BLOCKED 증거가 커밋돼 남는다**(예외 롤백으로 사라지지 않음) ·
    승인 행·승인 이벤트는 생기지 않는다(확정 시도는 승인을 만들지 않는다)"""
    so_id = make()
    with logged_in(TRADE) as client:
        response = confirm(client, so_id)
    blocked = _blocked(response)
    assert gate in blocked, blocked.keys()
    item = blocked[gate]
    assert item["resolution"] == resolution
    if reason is not None:
        assert item["reason_code"] == reason
    _assert_untouched(so_id)
    (evidence,) = evaluations(so_id, "BLOCKED")
    assert evidence["id"] == error_of(response)["detail"]["evaluation_id"]
    assert any(g["gate_code"] == gate for g in evidence["results"]["gates"])
    assert approvals_of(so_id) == [] and count("approval_events") == 0


def test_several_unresolved_gates_are_all_reported_in_one_response() -> None:
    """미해소가 여럿(가격 편차+MOQ+시장 준비도)이면 한 응답에 전부 실린다 — 하나씩 고치며 반복 시도하게 만들지 않는다"""
    so = ready_so(price=1000, market_color="RED", quantity=5)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=1100, list_price=1000)
    for sku in so["sku_ids"]:
        set_moq(sku, 100)
    with logged_in(TRADE) as client:
        response = confirm(client, so["id"])
    assert set(_blocked(response)) == {"PRICE_DEVIATION", "MOQ", "MARKET_READINESS"}
    _assert_untouched(so["id"])


def test_a_blocked_attempt_is_recorded_each_time_and_a_later_success_adds_the_single_confirmed_row() -> (
    None
):
    """차단 시도마다 BLOCKED 증거 1행 — 해소 후 성공하면 CONFIRMED 1행이 더해진다(SO당 1행 유니크)"""
    so_id = _price_block()
    with logged_in(TRADE) as client:
        assert confirm(client, so_id).status_code == 409
        assert confirm(client, so_id).status_code == 409
        assert len(evaluations(so_id, "BLOCKED")) == 2
        report = gates_of(client, so_id)
        item = find(report, "PRICE_DEVIATION", line_ids(so_id)[0])
        override = client.post(
            f"{SO}/{so_id}/gate-overrides", json=override_body(item), headers=idem()
        )
        assert override.status_code == 201, override.text
        ok = confirm(client, so_id)
    assert ok.status_code == 200, ok.text
    assert len(evaluations(so_id, "BLOCKED")) == 2 and len(evaluations(so_id, "CONFIRMED")) == 1


def test_an_override_resolves_a_price_gate_and_the_override_is_named_in_the_evidence() -> None:
    """가격 편차를 override(사유·해시 결속)로 해소하면 확정된다 — CONFIRMED 증거의 `used_override_ids`에 부여 행이 남는다(PI 게이트는 PASS 그대로)"""
    so_id = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so_id), "PRICE_DEVIATION", line_ids(so_id)[0])
        granted = client.post(
            f"{SO}/{so_id}/gate-overrides", json=override_body(item), headers=idem()
        ).json()
        response = confirm(client, so_id)
    assert response.status_code == 200, response.text
    (evidence,) = evaluations(so_id, "CONFIRMED")
    assert evidence["results"]["used_override_ids"] == [granted["id"]]
    price = [g for g in response.json()["gates"] if g["gate_code"] == "PRICE_DEVIATION"]
    assert price and price[0]["settlement"] == "OVERRIDDEN"


def test_an_override_granted_before_an_input_change_no_longer_clears_the_gate() -> None:
    """override는 판정 해시에 결속된다 — 부여 뒤 단가가 바뀌면 판정이 달라져 옛 override로는 확정되지 않는다(409)"""
    so_id = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so_id), "PRICE_DEVIATION", line_ids(so_id)[0])
        client.post(f"{SO}/{so_id}/gate-overrides", json=override_body(item), headers=idem())
        set_line(so_id, 1, unit_price=1200, list_price=1000)
        response = confirm(client, so_id)
    assert "PRICE_DEVIATION" in _blocked(response)
    _assert_untouched(so_id)


@pytest.mark.parametrize(
    ("mode", "expected"), [("WARN", "WARN"), ("OFF", "SKIPPED_OFF")], ids=["경고모드", "끔"]
)
def test_the_pi_gate_mode_decides_how_an_unpaid_advance_order_is_recorded(
    mode: str, expected: str
) -> None:
    """선수금 T/T 수주(PI 없음)는 정책 모드에 따라 — WARN=진행하되 pi_gate_verdict=WARN으로 기록 / OFF=확인 생략 SKIPPED_OFF(스킵 사실 기록) / BLOCK(기본)=차단(위 테스트)"""
    so = ready_so(terms="TT_ADVANCE")
    set_policy("pi_advance_gate_mode", mode)
    with logged_in(TRADE) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    assert response.json()["sales_order"]["pi_gate_verdict"] == expected
    (evidence,) = evaluations(so["id"], "CONFIRMED")
    assert evidence["results"]["pi_gate_verdict"] == expected
    assert evidence["results"]["policy_source"]["pi_advance_gate_mode"] == "SET"


def test_an_admin_override_resolves_the_pi_gate_and_is_recorded_as_overridden() -> None:
    """PI 입금 BLOCK(PI 없음)은 관리자 override로 해소 — pi_gate_verdict=OVERRIDDEN"""
    so = ready_so(terms="TT_ADVANCE")
    with logged_in(ADMIN) as client:
        item = find(gates_of(client, so["id"]), "PI_DEPOSIT")
        r = client.post(
            f"{SO}/{so['id']}/gate-overrides",
            json=override_body(item, "선수금 입금 확인서 별도 수령"),
            headers=idem(),
        )
        assert r.status_code == 201, r.text
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    assert response.json()["sales_order"]["pi_gate_verdict"] == "OVERRIDDEN"


# ══ QT 수주전환 ═════════════════════════════════════════════════════════════════


def test_confirming_a_reference_order_converts_the_parent_quotation_in_the_same_transaction() -> (
    None
):
    """QT에서 만든 SO를 확정하면 같은 트랜잭션에서 QT가 CONVERTED가 된다(자동 엣지·행위자=확정자) — SO 확정이 롤백되면 QT도 그대로다(`test_confirm_concurrency`의 포트 실패 시험)"""
    with logged_in(TRADE) as client:
        qt = issued_quotation(client, quantity=10)
        so = create_so_from_qt_via_api(client, qt)
        for line in client.get(f"{SO}/{so['id']}").json()["lines"]:
            set_readiness(line["sku_id"], "GREEN")
        set_policy("pi_advance_gate_mode", "OFF")
        set_terms(so["id"], "LC")
        assert scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "ISSUED"
        response = confirm(client, so["id"])
    assert response.status_code == 200, response.text
    assert scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "CONVERTED"
    log = scalar(
        "SELECT automatic FROM quotation_status_log WHERE quotation_id = :i AND to_status = 'CONVERTED'",
        i=qt["id"],
    )
    assert log is True


# ══ 입력·상태 검증 ═══════════════════════════════════════════════════════════════


def test_an_incomplete_order_is_a_422_not_a_server_error_and_names_the_missing_fields() -> None:
    """동결 완결성(Incoterms·환율·라인≥1)이 비면 500이 아니라 422 DOCUMENT.INCOMPLETE — 누락 항목 안내·SO 무변(증거도 남지 않는다: 게이트 평가 전 거부)"""
    so = ready_so()
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE sales_orders SET incoterm_code = NULL, incoterm_place = NULL, incoterm_year = NULL WHERE id = :i"
            ),
            {"i": so["id"]},
        )
    with logged_in(TRADE) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 422 and code_of(response) == "TRADE_DOCS.DOCUMENT.INCOMPLETE"
    assert set(error_of(response)["detail"]) == {"incoterm"}
    _assert_untouched(so["id"])
    assert evaluations(so["id"]) == []


def test_an_order_without_live_lines_cannot_be_confirmed() -> None:
    """라인이 없는(전부 제외된) SO는 확정할 수 없다 — 422 INCOMPLETE(lines)"""
    so = ready_so()
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE sales_order_lines SET deleted_at = now() WHERE so_id = :i"),
            {"i": so["id"]},
        )
        connection.execute(
            text("UPDATE sales_orders SET total_amount = 0 WHERE id = :i"), {"i": so["id"]}
        )
    with logged_in(TRADE) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 422 and "lines" in error_of(response)["detail"]


@pytest.mark.parametrize("status", ["ON_HOLD", "CANCELLED"])
def test_only_a_received_order_can_be_confirmed(status: str) -> None:
    """보류·취소 SO의 확정은 409 TRANSITION.NOT_ALLOWED — 확정 시도 증거도 남지 않는다(상태 검증이 게이트 평가보다 먼저)"""
    so = ready_so()
    with logged_in(TRADE) as client:
        to = {"ON_HOLD": "ON_HOLD", "CANCELLED": "CANCELLED"}[status]
        moved = client.post(
            f"{SO}/{so['id']}/transitions",
            json={"to": to, "version": so_version(so["id"]), "reason": "테스트"},
            headers=idem(),
        )
        assert moved.status_code == 200, moved.text
        response = confirm(client, so["id"])
    assert response.status_code == 409 and code_of(response) == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert evaluations(so["id"]) == []


def test_a_stale_version_is_a_409_and_an_unknown_order_is_a_404() -> None:
    """화면이 본 version과 다르면 409(version 충돌 — 새로고침 안내), 없는 SO는 404"""
    so = ready_so()
    with logged_in(TRADE) as client:
        stale = confirm(client, so["id"], version=so_version(so["id"]) + 5)
        missing = confirm(client, 987_654_321, version=1)
    assert stale.status_code == 409 and code_of(stale) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
    assert missing.status_code == 404
    _assert_untouched(so["id"])


def test_the_request_body_has_no_bypass_fields_and_requires_an_idempotency_key() -> None:
    """요청 스키마는 extra=forbid — force·skip·bypass·override·status·승인 id를 실으면 422(조용히 무시되지 않는다) · Idempotency-Key 없는 확정은 거부"""
    so = ready_so()
    with logged_in(ADMIN) as client:
        for field in (
            "force",
            "skip",
            "bypass",
            "override",
            "status",
            "approval_id",
            "credit_verdict",
        ):
            r = client.post(
                f"{SO}/{so['id']}/confirm",
                json={"version": so_version(so["id"]), field: True},
                headers=idem(),
            )
            assert r.status_code == 422, field
        no_key = client.post(f"{SO}/{so['id']}/confirm", json={"version": so_version(so["id"])})
        assert no_key.status_code in (400, 422)
    _assert_untouched(so["id"])


@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT])
def test_roles_without_trade_authority_cannot_confirm(role: RoleCode) -> None:
    """조회·물류·인증 역할은 확정할 수 없다(403) — SO·증거 무변"""
    so = ready_so()
    with logged_in(role) as client:
        response = confirm(client, so["id"])
    assert response.status_code == 403
    _assert_untouched(so["id"])
    assert evaluations(so["id"]) == []


def test_unauthenticated_confirmation_is_a_401() -> None:
    so = ready_so()

    from app.main import app

    with TestClient(app) as anonymous:
        response = anonymous.post(f"{SO}/{so['id']}/confirm", json={"version": 1}, headers=idem())
    assert response.status_code == 401


def test_confirm_does_not_leak_cost_or_margin_fields() -> None:
    """확정 응답(SO·게이트 결과)에 원가·마진·매입가 계열 키가 없다 — 판매 단가·수량·여신 수치만"""
    from app.core.logging.redaction import is_sensitive_key

    so = ready_so(limit=100_000)
    with logged_in(TRADE) as client:
        body = confirm(client, so["id"]).json()

    def keys(value: Any) -> set[str]:
        out: set[str] = set()
        if isinstance(value, dict):
            for k, v in value.items():
                out.add(str(k))
                out |= keys(v)
        elif isinstance(value, list):
            for v in value:
                out |= keys(v)
        return out

    bad = {k for k in keys(body) if is_sensitive_key(k) or "cost" in k or "margin" in k}
    assert bad == set(), bad


def test_complete_for_confirm_is_idempotent_for_a_prepared_order() -> None:
    """팩토리 자기검사 — 확정 가능 SO는 입력 완결 상태라 같은 채움을 다시 해도 변하지 않는다(공회전 방지)"""
    so = ready_so()
    before = so_row(so["id"])
    complete_for_confirm(so["id"])
    assert so_row(so["id"])["fx_rate"] == before["fx_rate"]
    assert audit_actions("approvals.approval.bypass_blocked") == []


# ══ 증거용 digest — 승인 결속 digest가 덮지 않는 입력까지 ═══════════════════════════════════


def test_the_evidence_digest_changes_with_gate_inputs_the_approval_digest_does_not_cover() -> None:
    """`results.evaluation_digest`(증거용)는 MOQ·준비도·결제조건 같은 **승인 결속 digest 밖의** 게이트 입력이 바뀌면 달라지고, `input_digest`(승인 결속)는 그대로다 —
    11a 인계 ⑧(승인 결속 digest 변경은 미소비 승인을 전부 무효화하므로 별도 값으로 둔다) · 같은 입력으로 다시 시도하면 같은 값(결정적)"""
    so = ready_so(quantity=5)
    for sku in so["sku_ids"]:
        set_moq(sku, 100)
    with logged_in(TRADE) as client:
        assert confirm(client, so["id"]).status_code == 409
        assert confirm(client, so["id"]).status_code == 409
        for sku in so["sku_ids"]:
            set_moq(sku, 200)  # MOQ 기준값만 바뀜 — 라인·환율·거래처는 그대로
        assert confirm(client, so["id"]).status_code == 409
    first, second, third = evaluations(so["id"], "BLOCKED")
    assert first["results"]["evaluation_digest"] == second["results"]["evaluation_digest"]
    assert first["results"]["evaluation_digest"] != third["results"]["evaluation_digest"]
    assert len(third["results"]["evaluation_digest"]) == 64
    assert first["input_digest"] == second["input_digest"] == third["input_digest"]


def test_the_confirmation_query_count_does_not_grow_with_the_number_of_lines() -> None:
    """확정 전체 트랜잭션의 SQL 문 수는 라인 수와 무관하다(2줄 = 8줄) — 확정 통로에 라인별 질의(N+1)가 없다"""
    from tests.support.sqlcount import count_statements

    small = ready_so(skus=2)
    large = ready_so(skus=8)
    actor = make_actor()
    few = count_statements(lambda: confirm_service(actor, small["id"]))
    many = count_statements(lambda: confirm_service(actor, large["id"]))
    assert few > 0 and many > 0
    assert few == many, f"질의 수가 라인 수에 비례한다: 2줄={few}, 8줄={many}"


def make_actor() -> Any:
    from tests.factories.approvals import make_user

    return make_user(TRADE)
