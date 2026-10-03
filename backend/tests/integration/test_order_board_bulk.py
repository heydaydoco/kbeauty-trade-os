"""H·J·K. 오더 보드 벌크 — 단일 통로 그대로·건별 독립 트랜잭션·결과 리포트·승인/override 우회 불가 (S3-1 PR-15a / design-D D6 / ADR-0066 / DESIGN §7.4·§17.6).

벌크는 새 확정 경로가 아니다: 각 건은 `confirm_intake`·`confirm_sales_order`·담당 편집 통로를 그대로 부르고, 그 결과(성공·거부)를 건별로 보고한다.
부분 성공이 정상(200)이고 합계는 모든 건이 끝난 뒤 센다. 실 HTTP·실 DB.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import uow as uow_module
from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import ForbiddenError
from app.modules.identity.models import RoleCode
from app.modules.order_board import bulk as bulk_module
from app.modules.order_board.constants import BulkAction, BulkOutcome, CardKind
from tests.factories.approvals import audit_actions, count, decide, make_user
from tests.factories.board import (
    actor,
    board_user,
    bulk,
    code_of,
    result_of,
    target,
)
from tests.factories.confirm import (
    ensure_approval_line,
    evaluations,
    ready_so,
    request_credit_approval,
    so_row,
    so_version,
)
from tests.factories.gates import find, gates_of, line_ids, override_body
from tests.factories.intake import land, world
from tests.factories.trade import idem, logged_in

TRADE = RoleCode.TRADE
ADMIN = RoleCode.ADMIN


def _intake_row(intake_id: int) -> dict[str, Any]:
    with owner_engine.connect() as connection:
        return dict(
            connection.execute(text("SELECT * FROM order_intakes WHERE id = :i"), {"i": intake_id})
            .mappings()
            .one()
        )


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["total"] == len(body["results"])
    assert body["ok_count"] + body["skipped_count"] + body["fail_count"] == body["total"]
    return body


def _so_targets(sos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [target("SO", so["id"], so_version(so["id"])) for so in sos]


# ══ H — 부분 성공·우회 불가 ═══════════════════════════════════════════════════════════


@pytest.mark.group_h
def test_fifty_targets_with_three_blocked_give_47_ok_and_3_blocked_with_server_gates() -> None:
    """50건 중 3건 BLOCKED(여신 초과·MOQ 미달·준비도 RED) → 47 OK + 3 BLOCKED(`blocked_gates` = 단일 통로의 서버 값) · 카운트=리포트=실제 DB ·
    BLOCKED 증거는 커밋되어 남고(건별 TX) 나머지 47건은 확정·증거 1행씩"""
    ensure_approval_line()
    sos = [ready_so() for _ in range(47)]
    credit = ready_so(limit=3_000)  # 총액 5,000 > 한도
    moq = ready_so(moq=100)  # 수량 5 < MOQ 100
    red = ready_so(market_color="RED")
    blocked = {credit["id"]: "CREDIT", moq["id"]: "MOQ", red["id"]: "MARKET_READINESS"}
    everything = [*sos, credit, moq, red]
    with logged_in(TRADE) as client:
        report = _ok(bulk(client, "CONFIRM_SO", _so_targets(everything)))
    assert report["ok_count"] == 47 and report["fail_count"] == 3
    assert report["outcome_counts"] == {
        "OK": 47,
        "SKIPPED": 0,
        "BLOCKED": 3,
        "CONFLICT": 0,
        "FORBIDDEN": 0,
        "FAILED": 0,
    }
    expected_resolution = {"CREDIT": "APPROVAL", "MOQ": "OVERRIDE", "MARKET_READINESS": "OVERRIDE"}
    for so_id, gate in blocked.items():
        item = result_of(report, "SO", so_id)
        assert item["outcome"] == "BLOCKED" and item["code"] == "TRADE_CHAIN.CONFIRM.GATE_BLOCKED"
        assert "개별" in item["message_ko"] and item["version"] is None
        gates = {g["gate_code"]: g for g in item["blocked_gates"]}
        assert set(gates) == {gate}
        assert gates[gate]["resolution"] == expected_resolution[gate]
        assert set(gates[gate]) == {
            "gate_code",
            "line_id",
            "line_no",
            "level",
            "resolution",
            "reason_code",
            "message_ko",
        }
        assert so_row(so_id)["status"] == "RECEIVED"
        assert len(evaluations(so_id, "BLOCKED")) == 1  # 실패도 커밋(건별 독립 TX)
    for so in sos:
        item = result_of(report, "SO", so["id"])
        row = so_row(so["id"])
        assert item["outcome"] == "OK" and item["code"] is None
        assert row["status"] == "CONFIRMED" and item["version"] == row["version"]
        assert item["doc_number"] == row["doc_number"] and item["sales_order_id"] == so["id"]
        assert len(evaluations(so["id"], "CONFIRMED")) == 1
    assert [r["id"] for r in report["results"]] == sorted(r["id"] for r in report["results"])


@pytest.mark.group_h
@pytest.mark.parametrize("role", [TRADE, ADMIN], ids=["무역", "관리자"])
def test_bulk_never_grants_or_bypasses_an_approval_and_consumes_it_like_the_single_path(
    role: RoleCode,
) -> None:
    """여신 초과 SO 벌크 확정 → BLOCKED(CREDIT·APPROVAL) — **관리자도 동일**, 벌크는 승인을 만들지 않고(승인 행 0) 우회 시도 audit은 단일 통로대로 남는다.
    사람이 개별로 승인을 요청·결정한 뒤의 벌크 확정은 그 승인을 **1회 소비**한다(증적 APPROVED·승인 CONSUMED)"""
    ensure_approval_line()
    so = ready_so(limit=3_000)
    with logged_in(role) as client:
        first = _ok(bulk(client, "CONFIRM_SO", _so_targets([so])))
        item = result_of(first, "SO", so["id"])
        assert item["outcome"] == "BLOCKED"
        assert item["blocked_gates"][0]["gate_code"] == "CREDIT"
        assert item["blocked_gates"][0]["resolution"] == "APPROVAL"
        assert count("approvals") == 0  # 벌크는 승인을 기안하지 않는다
        assert len(audit_actions("approvals.approval.bypass_blocked")) == 1
        assert so_row(so["id"])["status"] == "RECEIVED"
        # 사람이 개별로 승인 요청 → 다른 사람이 승인
        requested = request_credit_approval(client, so["id"])
        assert requested.status_code == 201, requested.text
        approval_id = int(requested.json()["id"])
        decide(approval_id, make_user(TRADE), "APPROVE")
        second = _ok(bulk(client, "CONFIRM_SO", _so_targets([so])))
    assert result_of(second, "SO", so["id"])["outcome"] == "OK"
    row = so_row(so["id"])
    assert row["status"] == "CONFIRMED" and row["credit_verdict"] == "APPROVED"
    assert row["credit_approval_id"] == approval_id
    assert count("approvals", "id = :i AND status = 'CONSUMED'", i=approval_id) == 1


@pytest.mark.group_h
def test_bulk_cannot_override_a_gate_and_uses_only_an_individually_granted_override() -> None:
    """MOQ 미달 SO는 벌크로 통과시킬 수 없다(BLOCKED·OVERRIDE) — 사람이 개별 화면에서 사유와 함께 override를 부여한 뒤에만 벌크 확정이 통과한다(부여 0 → 통과 0)"""
    so = ready_so(moq=100)
    with logged_in(TRADE) as client:
        first = _ok(bulk(client, "CONFIRM_SO", _so_targets([so])))
        assert result_of(first, "SO", so["id"])["outcome"] == "BLOCKED"
        assert count("gate_overrides") == 0
        (line,) = line_ids(so["id"])
        item = find(gates_of(client, so["id"]), "MOQ", line)
        granted = client.post(
            f"/api/v1/sales-orders/{so['id']}/gate-overrides",
            json=override_body(item),
            headers=idem(),
        )
        assert granted.status_code == 201, granted.text
        second = _ok(bulk(client, "CONFIRM_SO", _so_targets([so])))
    assert result_of(second, "SO", so["id"])["outcome"] == "OK"
    assert so_row(so["id"])["status"] == "CONFIRMED"


@pytest.mark.group_h
def test_bulk_confirm_intake_lands_one_so_each_through_the_single_path() -> None:
    """인테이크 벌크 확정 = 건마다 `confirm_intake`(SO 접수 생성) — 결과의 수주 id·번호가 DB 백링크와 같고, 대상 밖 인테이크는 그대로다"""
    w = world()
    intakes = [land(w) for _ in range(3)]
    untouched = land(w)
    with logged_in(TRADE) as client:
        report = _ok(
            bulk(
                client,
                "CONFIRM_INTAKE",
                [target("INTAKE", i["id"], i["version"]) for i in intakes],
            )
        )
    assert report["ok_count"] == 3
    for intake in intakes:
        item = result_of(report, "INTAKE", intake["id"])
        row = _intake_row(intake["id"])
        assert row["status"] == "CONFIRMED" and item["sales_order_id"] == row["sales_order_id"]
        assert so_row(row["sales_order_id"])["doc_number"] == item["doc_number"]
        assert so_row(row["sales_order_id"])["status"] == "RECEIVED"  # 접수 생성뿐 — 확정 아님
        assert item["version"] == row["version"]
    assert _intake_row(untouched["id"])["status"] == "PENDING"


@pytest.mark.group_h
def test_processing_order_is_ascending_id_whatever_the_request_order() -> None:
    """요청 순서와 무관하게 (종류, id) 오름차순으로 처리한다 — 채번 순서가 id 순서와 같고 리포트도 id 오름차순"""
    w = world()
    intakes = [land(w) for _ in range(4)]
    shuffled = [intakes[2], intakes[0], intakes[3], intakes[1]]
    with logged_in(TRADE) as client:
        report = _ok(
            bulk(
                client,
                "CONFIRM_INTAKE",
                [target("INTAKE", i["id"], i["version"]) for i in shuffled],
            )
        )
    ids = [r["id"] for r in report["results"]]
    assert ids == sorted(i["id"] for i in intakes)
    numbers = [r["doc_number"] for r in report["results"]]
    assert numbers == sorted(numbers)  # 처리 순서 = 채번 순서


@pytest.mark.group_h
def test_assign_bumps_the_version_skips_the_same_assignee_and_conflicts_on_a_stale_version() -> (
    None
):
    """담당자 지정 — SO(FREE 열·확정 뒤에도)·인테이크 모두 version +1로 OK, 이미 그 담당자면 SKIPPED(version 불변), 낡은 version은 CONFLICT(409 코드)"""
    w = world()
    intake = land(w)
    so = ready_so()
    confirmed = ready_so()
    new_owner = board_user(TRADE)
    with logged_in(TRADE) as client:
        assert _ok(bulk(client, "CONFIRM_SO", _so_targets([confirmed])))["ok_count"] == 1
        targets = [
            target("INTAKE", intake["id"], intake["version"]),
            *_so_targets([so, confirmed]),
        ]
        first = _ok(bulk(client, "ASSIGN", targets, assignee_id=new_owner))
        assert first["ok_count"] == 3
        for item in first["results"]:
            assert item["outcome"] == "OK" and item["version"] is not None
        assert so_row(so["id"])["assignee_id"] == new_owner
        assert so_row(confirmed["id"])["assignee_id"] == new_owner
        assert _intake_row(intake["id"])["assignee_id"] == new_owner
        fresh = [target(r["kind"], r["id"], r["version"]) for r in first["results"]]
        second = _ok(bulk(client, "ASSIGN", fresh, assignee_id=new_owner))
        assert second["skipped_count"] == 3 and second["ok_count"] == 0
        assert [r["version"] for r in second["results"]] == [r["version"] for r in first["results"]]
        stale = _ok(bulk(client, "ASSIGN", targets, assignee_id=board_user(TRADE)))
        assert stale["outcome_counts"]["CONFLICT"] == 3
        assert {r["code"] for r in stale["results"]} == {"COMMON.CONCURRENCY.VERSION_CONFLICT"}
        patch = client.patch(
            f"/api/v1/sales-orders/{so['id']}/meta",
            json={"version": so["version"], "assignee_id": new_owner},
        )
        assert (
            patch.status_code == 409
        )  # 벌크 ASSIGN이 version을 올렸다 — 낡은 version의 개별 편집은 409


@pytest.mark.group_h
@pytest.mark.parametrize(
    ("roles", "active", "expected"),
    [
        ((RoleCode.VIEWER,), True, 422),
        ((RoleCode.LOGISTICS,), True, 422),
        ((RoleCode.TRADE,), False, 422),
        ((RoleCode.ADMIN,), True, 200),
        ((RoleCode.CERT, RoleCode.TRADE), True, 200),
    ],
)
def test_assignee_must_be_an_active_trade_or_admin_user(
    roles: tuple[RoleCode, ...], active: bool, expected: int
) -> None:
    """담당자 = 활성+무역/관리자 보유(ADR-0067 ③) — 아니면 아무 건도 처리하지 않고 422(fail-visible), 겸직은 통과"""
    so = ready_so()
    user = make_user(*roles, active=active)
    with logged_in(TRADE) as client:
        response = bulk(client, "ASSIGN", _so_targets([so]), assignee_id=user.id)
    assert response.status_code == expected, response.text
    if expected == 422:
        assert so_row(so["id"])["assignee_id"] != user.id
        assert response.json()["error"]["code"] == "COMMON.VALIDATION.INVALID_FIELD"
    else:
        assert so_row(so["id"])["assignee_id"] == user.id
    with logged_in(TRADE) as client:
        nobody = bulk(client, "ASSIGN", _so_targets([so]), assignee_id=987_654_321)
    assert nobody.status_code == 422


# ══ K — 요청 모양·상한·권한 ═══════════════════════════════════════════════════════════


@pytest.mark.group_k
@pytest.mark.parametrize(
    "extra",
    [
        {"override": True},
        {"approval_id": 1},
        {"force": True},
        {"reason": "일괄 처리 사유"},
        {"skip_gates": True},
    ],
)
def test_the_bulk_request_has_no_override_approval_or_force_fields(extra: dict[str, Any]) -> None:
    """벌크 요청에 override·승인·사유·force 필드가 없다 — 보내면 조용히 무시하지 않고 422(대상 무변)"""
    so = ready_so()
    with logged_in(ADMIN) as client:
        body = {"action": "CONFIRM_SO", "targets": _so_targets([so]), **extra}
        response = client.post("/api/v1/order-board/bulk", json=body, headers=idem())
        nested = client.post(
            "/api/v1/order-board/bulk",
            json={"action": "CONFIRM_SO", "targets": [{**_so_targets([so])[0], **extra}]},
            headers=idem(),
        )
    assert response.status_code == 422 and nested.status_code == 422
    assert so_row(so["id"])["status"] == "RECEIVED"


@pytest.mark.group_k
def test_bulk_shape_rules_limits_and_duplicate_targets() -> None:
    """51건(중복 제거 후)=422 `ORDER_BOARD.BULK.TOO_MANY`·50건은 통과 · 완전 중복은 한 건으로 · version이 다른 중복은 422 · 액션과 대상 종류 불일치 422 ·
    ASSIGN의 담당자 누락·확정 액션의 담당자 동봉 422 · 빈 대상 422 · Idempotency-Key 없으면 400(KEY_REQUIRED) · 모르는 액션(보류·취소 포함) 422"""
    so = ready_so()
    version = so_version(so["id"])
    with logged_in(TRADE) as client:
        too_many = bulk(client, "CONFIRM_SO", [target("SO", 900_000_000 + n, 1) for n in range(51)])
        assert too_many.status_code == 422 and code_of(too_many) == "ORDER_BOARD.BULK.TOO_MANY"
        fifty = bulk(client, "CONFIRM_SO", [target("SO", 900_000_000 + n, 1) for n in range(50)])
        assert fifty.status_code == 200 and fifty.json()["outcome_counts"]["FAILED"] == 50
        same = [target("SO", so["id"], version)] * 3
        once = _ok(bulk(client, "CONFIRM_SO", same))
        assert once["total"] == 1 and once["ok_count"] == 1
        twice = bulk(client, "ASSIGN", [target("SO", 5, 1), target("SO", 5, 2)], assignee_id=1)
        assert twice.status_code == 422
        assert bulk(client, "CONFIRM_SO", [target("INTAKE", 1, 1)]).status_code == 422
        assert bulk(client, "CONFIRM_INTAKE", [target("SO", 1, 1)]).status_code == 422
        assert bulk(client, "ASSIGN", [target("SO", 1, 1)]).status_code == 422
        assert (
            bulk(client, "CONFIRM_SO", [target("SO", 1, 1)], assignee_id=so["id"]).status_code
            == 422
        )
        assert bulk(client, "CONFIRM_SO", []).status_code == 422
        assert bulk(client, "HOLD", [target("SO", 1, 1)]).status_code == 422
        assert bulk(client, "CANCEL", [target("SO", 1, 1)]).status_code == 422
        no_key = client.post(
            "/api/v1/order-board/bulk",
            json={"action": "CONFIRM_SO", "targets": [target("SO", 1, 1)]},
        )
        assert no_key.status_code == 400 and code_of(no_key) == "COMMON.IDEMPOTENCY.KEY_REQUIRED"
        missing_version = client.post(
            "/api/v1/order-board/bulk",
            json={"action": "CONFIRM_SO", "targets": [{"kind": "SO", "id": 1}]},
            headers=idem(),
        )
        assert missing_version.status_code == 422


@pytest.mark.group_k
@pytest.mark.parametrize("role", [RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT])
def test_non_trade_roles_get_403_at_the_route_and_the_service(role: RoleCode) -> None:
    """벌크는 무역·관리자만 — 라우트 403, 서비스 직접 호출도 403(이중 방어), 대상 무변"""
    so = ready_so()
    with logged_in(role) as client:
        assert bulk(client, "CONFIRM_SO", _so_targets([so])).status_code == 403
    with pytest.raises(ForbiddenError):
        bulk_module.run_bulk(
            actor=actor(board_user(role), role),
            idempotency_key="k",
            action=BulkAction.CONFIRM_SO,
            targets=[bulk_module.Target(CardKind.SO, so["id"], so_version(so["id"]))],
        )
    assert so_row(so["id"])["status"] == "RECEIVED"


@pytest.mark.group_k
@pytest.mark.parametrize("action", list(BulkAction))
def test_each_row_is_rechecked_server_side_and_a_role_without_rights_is_forbidden(
    action: BulkAction,
) -> None:
    """행별 서버측 역할 검증 — 벌크 진입 검사를 건너뛰고 건을 직접 실행해도 단일 통로(또는 SO 담당 편집의 행별 재검증)가 403 → `FORBIDDEN`(대상 무변)"""
    viewer = actor(board_user(RoleCode.VIEWER), RoleCode.VIEWER)
    if action is BulkAction.CONFIRM_INTAKE:
        intake = land(world())
        tgt = bulk_module.Target(CardKind.INTAKE, intake["id"], intake["version"])
    else:
        so = ready_so()
        tgt = bulk_module.Target(CardKind.SO, so["id"], so_version(so["id"]))
    result = bulk_module.run_item(
        viewer,
        "bulk-key",
        action,
        tgt,
        board_user(TRADE) if action is BulkAction.ASSIGN else None,
    )
    assert result.outcome is BulkOutcome.FORBIDDEN and result.code == "COMMON.AUTH.FORBIDDEN"
    if tgt.kind is CardKind.SO:
        row = so_row(tgt.id)
        assert row["status"] == "RECEIVED" and row["version"] == tgt.expected_version
    else:
        assert _intake_row(tgt.id)["status"] == "PENDING"


# ══ H — 건별 독립 트랜잭션·커밋 후 합산 ════════════════════════════════════════════════


@pytest.mark.group_h
def test_each_item_is_committed_before_the_next_one_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """건별 독립 TX — 2번째 건을 처리하기 직전에 다른 연결에서 1번째 건의 확정이 이미 보인다(한 트랜잭션으로 묶이지 않는다)"""
    sos = [ready_so() for _ in range(3)]
    seen: list[str] = []
    original = bulk_module.so_confirm.confirm_sales_order

    def spy(**kwargs: Any) -> Any:
        if kwargs["so_id"] != sos[0]["id"]:
            seen.append(so_row(sos[0]["id"])["status"])
        return original(**kwargs)

    monkeypatch.setattr(bulk_module.so_confirm, "confirm_sales_order", spy)
    with logged_in(TRADE) as client:
        report = _ok(bulk(client, "CONFIRM_SO", _so_targets(sos)))
    assert report["ok_count"] == 3
    assert seen == ["CONFIRMED", "CONFIRMED"]


@pytest.mark.group_h
def test_a_failed_commit_is_reported_as_failed_and_not_counted_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """카운트 = 커밋 후 합산 — 2번째 건의 커밋이 실패하면 그 건은 FAILED(대상 무변)이고 OK 합계에 들어가지 않으며, 리포트가 실제 DB와 같다"""
    sos = [ready_so() for _ in range(3)]
    new_owner = board_user(TRADE)
    real_commit = uow_module._commit
    real_update = bulk_module.sales_orders_service.update_meta
    armed = {"on": False}

    def arm_on_second(**kwargs: Any) -> Any:
        # 2번째 건의 편집이 끝난 바로 그 트랜잭션의 커밋만 실패시킨다(커밋 횟수 세기에 기대지 않는다)
        body = real_update(**kwargs)
        armed["on"] = kwargs["so_id"] == sos[1]["id"]
        return body

    def flaky(session: Any) -> None:
        if armed["on"]:
            armed["on"] = False
            raise RuntimeError("커밋 실패 주입")
        real_commit(session)

    targets = [
        bulk_module.Target(CardKind.SO, t["id"], t["expected_version"]) for t in _so_targets(sos)
    ]
    trade_actor = actor(board_user(TRADE), TRADE)
    monkeypatch.setattr(bulk_module.sales_orders_service, "update_meta", arm_on_second)
    monkeypatch.setattr(uow_module, "_commit", flaky)
    report = bulk_module.run_bulk(
        actor=trade_actor,
        idempotency_key="commit-fail",
        action=BulkAction.ASSIGN,
        targets=targets,
        assignee_id=new_owner,
    )
    monkeypatch.setattr(uow_module, "_commit", real_commit)
    by_id = {r["id"]: r for r in report["results"]}
    assert by_id[sos[1]["id"]]["outcome"] == "FAILED"
    assert by_id[sos[1]["id"]]["code"] == "COMMON.INTERNAL.UNEXPECTED"
    assert report["ok_count"] == 2 and report["fail_count"] == 1
    for so in sos:
        ok = by_id[so["id"]]["outcome"] == "OK"
        assert (so_row(so["id"])["assignee_id"] == new_owner) is ok  # 리포트 = 실제 DB


@pytest.mark.group_h
def test_an_unexpected_error_on_one_item_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 건의 예상 못 한 오류는 그 건만 FAILED(500 코드)이고 다음 건은 계속 처리된다 — 오류 건은 롤백(대상 무변)"""
    w = world()
    intakes = [land(w) for _ in range(3)]
    original = bulk_module.intake_flow.confirm_intake

    def boom(**kwargs: Any) -> Any:
        if kwargs["intake_id"] == intakes[1]["id"]:
            raise ValueError("주입된 오류")
        return original(**kwargs)

    monkeypatch.setattr(bulk_module.intake_flow, "confirm_intake", boom)
    with logged_in(TRADE) as client:
        report = _ok(
            bulk(
                client, "CONFIRM_INTAKE", [target("INTAKE", i["id"], i["version"]) for i in intakes]
            )
        )
    assert [r["outcome"] for r in report["results"]] == ["OK", "FAILED", "OK"]
    assert report["results"][1]["code"] == "COMMON.INTERNAL.UNEXPECTED"
    assert _intake_row(intakes[1]["id"])["status"] == "PENDING"


@pytest.mark.group_k
def test_the_bulk_refuses_to_run_inside_an_open_transaction() -> None:
    """바깥 트랜잭션 안에서 부르면(건이 한 TX로 합류) 실행하지 않고 프로그래밍 오류로 멈춘다(fail-closed) — 대상 무변"""
    so = ready_so()
    with pytest.raises(RuntimeError), unit_of_work():
        bulk_module.run_bulk(
            actor=actor(board_user(TRADE), TRADE),
            idempotency_key="nested",
            action=BulkAction.CONFIRM_SO,
            targets=[bulk_module.Target(CardKind.SO, so["id"], so_version(so["id"]))],
        )
    assert so_row(so["id"])["status"] == "RECEIVED"


# ══ K — 처리 도중 권한 변화(TOCTOU)·로그 위생 ══════════════════════════════════════════════


def _revoke(user_id: int, how: str) -> None:
    with owner_engine.begin() as connection:
        if how == "deactivate":
            connection.execute(
                text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user_id}
            )
        else:
            connection.execute(
                text("UPDATE user_roles SET deleted_at = now() WHERE user_id = :u"), {"u": user_id}
            )


@pytest.mark.group_k
@pytest.mark.parametrize("how", ["deactivate", "role_lost"], ids=["비활성화", "역할상실"])
def test_an_actor_who_loses_rights_mid_bulk_runs_nothing_more(
    monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """행위자 자격은 건마다 DB에서 다시 읽는다 — 1번째 건 처리 직후 행위자가 비활성화(또는 역할 상실)되면 남은 건은 실행하지 않고
    `FORBIDDEN`(`COMMON.AUTH.FORBIDDEN`)·대상 무변, 리포트 = 실제 DB"""
    sos = [ready_so() for _ in range(3)]
    user_id = board_user(TRADE)
    original = bulk_module.so_confirm.confirm_sales_order

    def revoke_after_first(**kwargs: Any) -> Any:
        result = original(**kwargs)
        if kwargs["so_id"] == sos[0]["id"]:
            _revoke(user_id, how)
        return result

    monkeypatch.setattr(bulk_module.so_confirm, "confirm_sales_order", revoke_after_first)
    report = bulk_module.run_bulk(
        actor=actor(user_id, TRADE),
        idempotency_key=f"revoked-{how}",
        action=BulkAction.CONFIRM_SO,
        targets=[
            bulk_module.Target(CardKind.SO, t["id"], t["expected_version"])
            for t in _so_targets(sos)
        ],
    )
    assert [r["outcome"] for r in report["results"]] == ["OK", "FORBIDDEN", "FORBIDDEN"]
    assert {r["code"] for r in report["results"][1:]} == {"COMMON.AUTH.FORBIDDEN"}
    assert report["ok_count"] == 1 and report["fail_count"] == 2
    assert so_row(sos[0]["id"])["status"] == "CONFIRMED"
    for so in sos[1:]:
        assert so_row(so["id"])["status"] == "RECEIVED"


@pytest.mark.group_k
@pytest.mark.parametrize("how", ["deactivate", "role_lost"], ids=["비활성화", "역할상실"])
def test_an_assignee_deactivated_after_the_precheck_fails_each_item_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """담당자 TOCTOU — 사전 검증(422)을 통과한 직후 담당자가 비활성화(또는 무역 역할 상실)되면 건 트랜잭션 안의 재확인이 잡아 `FAILED`(`INVALID_FIELD`)·
    대상 무변(담당자·version). 역할 상실은 단일 편집 통로가 보지 않으므로(활성만 확인) 벌크의 건별 재확인만이 막는다"""
    sos = [ready_so() for _ in range(2)]
    new_owner = board_user(TRADE)
    original = bulk_module.require_assignee

    def then_deactivate(assignee_id: int) -> None:
        original(assignee_id)
        _revoke(assignee_id, how)

    monkeypatch.setattr(bulk_module, "require_assignee", then_deactivate)
    before = {so["id"]: (so_row(so["id"])["assignee_id"], so_version(so["id"])) for so in sos}
    with logged_in(TRADE) as client:
        report = _ok(bulk(client, "ASSIGN", _so_targets(sos), assignee_id=new_owner))
    assert [r["outcome"] for r in report["results"]] == ["FAILED", "FAILED"]
    assert {r["code"] for r in report["results"]} == {"COMMON.VALIDATION.INVALID_FIELD"}
    for so in sos:
        assert (so_row(so["id"])["assignee_id"], so_version(so["id"])) == before[so["id"]]


def _money_needles(minor: int) -> set[str]:
    return {str(minor), f"{minor:,}", f"{minor / 100:.2f}", f"{minor / 100:,.2f}"}


@pytest.mark.group_k
def test_bulk_logs_carry_no_amounts_prices_or_credit_basis(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """로그 위생 — BLOCKED(여신 한도 초과)·FAILED(주입 오류)·OK가 섞인 벌크 확정의 로그 어디에도 금액·단가·한도 수치·basis가 없다
    (벌크 로그는 액션·종류·id·결과·코드만, 실패 로그는 예외 트레이스백뿐). 7자리 이상 특이값으로 시각·id와 겹치지 않게 한다"""
    ensure_approval_line()
    price, quantity, limit = 7_654_321, 9, 31_337_017
    blocked = ready_so(limit=limit, price=price, quantity=quantity)
    failing = ready_so(price=price, quantity=quantity)
    fine = ready_so(price=price, quantity=quantity)
    original = bulk_module.so_confirm.confirm_sales_order

    def boom(**kwargs: Any) -> Any:
        if kwargs["so_id"] == failing["id"]:
            raise RuntimeError("주입된 오류")
        return original(**kwargs)

    monkeypatch.setattr(bulk_module.so_confirm, "confirm_sales_order", boom)
    with caplog.at_level(logging.DEBUG), logged_in(TRADE) as client:
        report = _ok(bulk(client, "CONFIRM_SO", _so_targets([blocked, failing, fine])))
    assert {r["id"]: r["outcome"] for r in report["results"]} == {
        blocked["id"]: "BLOCKED",
        failing["id"]: "FAILED",
        fine["id"]: "OK",
    }
    logged = caplog.text
    assert "order_board_bulk_item" in logged and "order_board_bulk_item_failed" in logged
    needles = (_money_needles(price) | _money_needles(price * quantity) | _money_needles(limit)) | {
        "limit_amount",
        "basis",
        "unit_price",
        "exposure",
        "total_amount",
    }
    for needle in sorted(needles):
        assert needle not in logged, needle
    assert "credit" not in logged.lower() and "여신" not in logged and "한도" not in logged


# ══ J — 같은 벌크 키 재요청 = 재생 ══════════════════════════════════════════════════════


def _effects() -> dict[str, int]:
    return {
        "confirmed": count("sales_orders", "status = 'CONFIRMED'"),
        "sales_orders": count("sales_orders"),
        "evaluations": count("gate_evaluations"),
        "events": count("events"),
        "audit": count("audit_log"),
        "status_log": count("sales_order_status_log"),
        "intake_confirmed": count("order_intakes", "status = 'CONFIRMED'"),
    }


@pytest.mark.group_j
def test_the_same_bulk_key_replays_the_same_report_without_new_effects() -> None:
    """같은 벌크 키·같은 본문 재요청 = 건별 재생으로 **같은 리포트**(대상 순서만 바뀐 것은 같은 요청) — 확정·증거·이벤트·audit·이력이 늘지 않는다
    (OK는 최초 결과 재생, BLOCKED는 같은 판정·증거 재사용)"""
    ensure_approval_line()
    w = world()
    intakes = [land(w) for _ in range(2)]
    sos = [ready_so(), ready_so(), ready_so(limit=3_000)]
    intake_key, so_key = idem(), idem()  # 같은 키는 완전히 같은 요청에만 — 액션마다 다른 키
    with logged_in(TRADE) as client:
        intake_body = [target("INTAKE", i["id"], i["version"]) for i in intakes]
        so_body = _so_targets(sos)
        first_i = _ok(bulk(client, "CONFIRM_INTAKE", intake_body, headers=intake_key))
        first_s = _ok(bulk(client, "CONFIRM_SO", so_body, headers=so_key))
        before = _effects()
        again_i = _ok(bulk(client, "CONFIRM_INTAKE", intake_body, headers=intake_key))
        again_s = _ok(bulk(client, "CONFIRM_SO", list(reversed(so_body)), headers=so_key))
    assert again_i == first_i and again_s == first_s
    assert [r["outcome"] for r in first_s["results"]] == ["OK", "OK", "BLOCKED"]
    assert _effects() == before


@pytest.mark.group_j
def test_the_same_bulk_key_replays_assign_instead_of_reporting_a_stale_version() -> None:
    """담당자 지정의 같은 키 재요청은 낡은 version CONFLICT가 아니라 최초 결과(OK) 재생 — version이 다시 오르지 않는다"""
    so = ready_so()
    owner = board_user(TRADE)
    headers = idem()
    body = _so_targets([so])
    with logged_in(TRADE) as client:
        first = _ok(bulk(client, "ASSIGN", body, assignee_id=owner, headers=headers))
        version_after = so_version(so["id"])
        again = _ok(bulk(client, "ASSIGN", body, assignee_id=owner, headers=headers))
        other = _ok(
            bulk(client, "ASSIGN", body, assignee_id=owner)
        )  # 새 키 = 새 시도 → 낡은 version
    assert first == again and first["ok_count"] == 1
    assert so_version(so["id"]) == version_after
    assert other["results"][0]["outcome"] == "CONFLICT"


@pytest.mark.group_j
def test_reusing_a_bulk_key_for_a_different_request_is_refused_as_a_whole() -> None:
    """같은 벌크 키에 다른 요청(version·대상·액션·담당자 중 하나라도 다름)을 실으면 **요청 전체** 409 `COMMON.IDEMPOTENCY.KEY_CONFLICT` —
    어떤 건도 실행하지 않는다(조용히 최초 결과로 흡수하거나 일부만 실행하지 않는다). 같은 요청은 다시 보내도 같은 리포트(재생)"""
    so, other_so = ready_so(), ready_so()
    owner, other_owner = board_user(TRADE), board_user(TRADE)
    headers = idem()
    body = _so_targets([so])
    with logged_in(TRADE) as client:
        first = _ok(bulk(client, "ASSIGN", body, assignee_id=owner, headers=headers))
        version_after = so_version(so["id"])
        other_version = so_version(other_so["id"])
        before = _effects()
        refused = [
            bulk(
                client,
                "ASSIGN",
                [target("SO", so["id"], version_after)],
                assignee_id=owner,
                headers=headers,
            ),
            bulk(
                client,
                "ASSIGN",
                [*body, *_so_targets([other_so])],
                assignee_id=owner,
                headers=headers,
            ),
            bulk(client, "ASSIGN", body, assignee_id=other_owner, headers=headers),
            bulk(client, "CONFIRM_SO", body, headers=headers),
        ]
        again = _ok(bulk(client, "ASSIGN", body + body, assignee_id=owner, headers=headers))
    for response in refused:
        assert response.status_code == 409, response.text
        assert code_of(response) == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    assert again == first
    assert so_version(so["id"]) == version_after and so_version(other_so["id"]) == other_version
    assert so_row(so["id"])["assignee_id"] == owner and so_row(so["id"])["status"] == "RECEIVED"
    assert so_row(other_so["id"])["assignee_id"] != owner
    assert _effects() == before


@pytest.mark.group_j
def test_a_bulk_key_longer_than_the_key_column_still_works_and_replays() -> None:
    """헤더 키 길이는 검증되지 않는다(D-D13) — 300자 벌크 키도 500이 아니라 정상 처리·같은 키 재요청 재생·다른 요청 409(지문 대조 키는 sha256 파생)"""
    so = ready_so()
    owner = board_user(TRADE)
    headers = {"Idempotency-Key": "k" * 300}
    body = _so_targets([so])
    with logged_in(TRADE) as client:
        first = _ok(bulk(client, "ASSIGN", body, assignee_id=owner, headers=headers))
        again = _ok(bulk(client, "ASSIGN", body, assignee_id=owner, headers=headers))
        other = bulk(client, "ASSIGN", body, assignee_id=board_user(TRADE), headers=headers)
    assert first == again and first["ok_count"] == 1
    assert other.status_code == 409 and code_of(other) == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
