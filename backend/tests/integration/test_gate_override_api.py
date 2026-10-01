"""H. 게이트 판정 조회·override 통제 — 권한·사유·낡은 판정 409·CREDIT override 불가·재평가·멱등·증적 (S3-1 PR-11a / design-D D3·D7 / ADR-0069).

`GET /sales-orders/{id}/gates`는 7종 판정과 확정 가능 여부(참고값 — 잠금·저장 없음)를 주고, override는 **별도 액션**이다(`POST …/gate-overrides`): 사유 5~500자·역할 제한(가격·MOQ=무역·관리자,
준비도·PI=관리자)·판정 해시 결속(입력이 바뀌면 자동 무효·낡은 해시는 409)·불변 기록·`gates.override.*` 이벤트. 품번·중복 PO·여신은 ADMIN도 override할 수 없다(422). 전부 실 HTTP·실 DB.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.logging.redaction import is_sensitive_key
from app.modules.gates.registry import DEFAULT_REGISTRY
from app.modules.identity.models import RoleCode
from tests.factories.approvals import approved_for, credit_so
from tests.factories.gates import (
    find,
    gates_of,
    line_ids,
    make_free,
    override_body,
    passing_so,
    scalar,
    set_line,
    set_policy,
)
from tests.factories.payments import advance_pi
from tests.factories.trade import idem, logged_in, raw_so

pytestmark = pytest.mark.group_h

TRADE = RoleCode.TRADE
ADMIN = RoleCode.ADMIN


def _post(
    client: TestClient,
    so_id: int,
    body: dict[str, Any],
    *,
    revoke: bool = False,
    key: dict[str, str] | None = None,
) -> Any:
    path = "gate-overrides/revoke" if revoke else "gate-overrides"
    return client.post(f"/api/v1/sales-orders/{so_id}/{path}", json=body, headers=key or idem())


def _rows(table: str, where: str = "true", **params: Any) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text(f"SELECT * FROM {table} WHERE {where} ORDER BY id"), params
            ).mappings()
        ]


def _price_block(unit: int = 1100) -> tuple[dict[str, Any], int]:
    """단가 편차 BLOCK인 SO(기준 1000·허용 500bp·단가 `unit`) → (SO, 라인 id). 나머지 6종은 PASS."""
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=unit, list_price=1000)
    (line,) = line_ids(so["id"])
    return so, line


@contextmanager
def _clients(*roles: RoleCode) -> Iterator[list[TestClient]]:
    with ExitStack() as stack:
        yield [stack.enter_context(logged_in(role)) for role in roles]


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _walk_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk_keys(inner)


# ══ 조회 ═══════════════════════════════════════════════════════════════════════


def test_the_gate_report_lists_all_seven_gates_and_a_clearable_baseline() -> None:
    """기준 SO의 GET /gates: 7종 PASS·확정 가능(cleared)·참고값(authoritative=false)·고정 문구와 범위 문구 동봉·정책 실효값과 출처 — 응답이 SO 상태와 입력 digest를 싣는다"""
    so = passing_so()
    with logged_in(RoleCode.VIEWER) as client:
        report = gates_of(client, so["id"])
    assert [g["gate_code"] for g in report["gates"]] == [
        "ITEM_MAPPING",
        "DUPLICATE_PO",
        "PRICE_DEVIATION",
        "CREDIT",
        "MARKET_READINESS",
        "MOQ",
        "PI_DEPOSIT",
    ]
    assert {g["level"] for g in report["gates"]} == {"PASS"}
    assert report["clearance"] == {"cleared": True, "needs_approval": False, "unresolved_count": 0}
    assert report["authoritative"] is False and report["status"] == "RECEIVED"
    assert report["note"] == "준비 상태 안내(법적 판정 아님)" and report["readiness_scope_note"]
    assert len(report["input_digest"]) == 64
    assert report["policies"]["pi_advance_gate_mode"] == {
        "value": "BLOCK",
        "source": "UNSET_DEFAULT",
    }
    assert report["policies"]["price_deviation_tolerance_bp"]["source"] == "UNSET_DEFAULT"


def test_the_gate_report_is_read_only_and_needs_a_login_and_a_real_order() -> None:
    """GET은 게이트 증적·override 행을 만들지 않고(조회 부작용 금지) 로그인 없이는 401, 없는 SO는 404"""
    so = passing_so()
    with logged_in(TRADE) as client:
        gates_of(client, so["id"])
        gates_of(client, so["id"])
        assert client.get("/api/v1/sales-orders/9999999/gates").status_code == 404
    assert scalar("SELECT count(*) FROM gate_evaluations") == 0
    assert scalar("SELECT count(*) FROM gate_overrides") == 0
    assert scalar("SELECT count(*) FROM events WHERE event_type LIKE 'gates.%'") == 0
    from app.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get(f"/api/v1/sales-orders/{so['id']}/gates").status_code == 401


def test_the_credit_figures_are_only_for_trade_and_admin_and_no_cost_field_exists() -> None:
    """여신 수치(한도·노출)는 무역·관리자 응답에만 있고 조회·물류·인증 역할 응답의 CREDIT 근거는 비어 있다(필드 부재). 어느 역할의 응답에도 원가·마진 키가 없다"""
    so = credit_so(limit=100_000, unit_price=40_000, quantity=5)
    with _clients(TRADE, ADMIN, RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT) as (
        trade,
        admin,
        viewer,
        logistics,
        cert,
    ):
        reports = {
            "trade": gates_of(trade, so["id"]),
            "admin": gates_of(admin, so["id"]),
            "viewer": gates_of(viewer, so["id"]),
            "logistics": gates_of(logistics, so["id"]),
            "cert": gates_of(cert, so["id"]),
        }
    for who in ("trade", "admin"):
        credit = find(reports[who], "CREDIT")
        assert (
            credit["basis"]["limit_amount"] == 100_000
            and credit["basis"]["excess_amount"] == 100_000
        )
    for who in ("viewer", "logistics", "cert"):
        credit = find(reports[who], "CREDIT")
        assert credit["basis"] == {} and credit["level"] == "BLOCK"  # 판정은 보이되 수치는 없다
        assert "100000" not in str(credit)
    for report in reports.values():
        assert not [key for key in _walk_keys(report) if is_sensitive_key(key)]


def test_a_crashing_evaluator_shows_up_as_unknown_not_a_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """평가기 하나가 예외를 던져도 GET은 200이고 그 게이트만 UNKNOWN(EVALUATION_ERROR)·확정 불가 — 500도 PASS도 아니다(응답에는 예외 클래스명만)"""
    so = passing_so()

    def boom(*_args: Any) -> list[Any]:
        raise RuntimeError("internal secret: purchase_price=1")

    monkeypatch.setitem(DEFAULT_REGISTRY._evaluators, "MOQ", boom)
    with logged_in(TRADE) as client:
        report = gates_of(client, so["id"])
    moq = find(report, "MOQ")
    assert (moq["level"], moq["reason_code"], moq["basis"]) == (
        "UNKNOWN",
        "EVALUATION_ERROR",
        {"error_class": "RuntimeError"},
    )
    assert report["clearance"]["cleared"] is False
    assert "secret" not in str(report) and "purchase_price" not in str(report)


# ══ override 부여 ══════════════════════════════════════════════════════════════


def test_a_trade_user_can_override_a_price_deviation_and_the_order_becomes_clearable() -> None:
    """가격 편차 BLOCK을 무역이 사유와 함께 override → 201 · 판정이 OVERRIDDEN(누가·왜 포함)·확정 가능. 불변 행(GRANT)·audit·outbox 이벤트(id·게이트만, 사유 없음)가 한 번씩 남는다"""
    so, line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert (item["level"], item["resolution"], item["settlement"], item["can_override"]) == (
            "BLOCK",
            "OVERRIDE",
            "UNRESOLVED",
            True,
        )
        assert gates_of(client, so["id"])["clearance"]["cleared"] is False
        response = _post(client, so["id"], override_body(item, "바이어 특별가 합의"))
        assert response.status_code == 201, response.text
        granted = response.json()
        report = gates_of(client, so["id"])
    assert (
        granted["action"],
        granted["gate_code"],
        granted["line_id"],
        granted["result_at_grant"],
    ) == ("GRANT", "PRICE_DEVIATION", line, "BLOCK")
    assert granted["authorized_role"] == "TRADE" and granted["basis_hash"] == item["basis_hash"]
    shown = find(report, "PRICE_DEVIATION", line)
    assert shown["settlement"] == "OVERRIDDEN" and shown["can_override"] is False
    assert (
        shown["override"]["reason"] == "바이어 특별가 합의"
        and shown["override"]["authorized_role"] == "TRADE"
    )
    assert report["clearance"]["cleared"] is True
    (row,) = _rows("gate_overrides")
    assert (row["action"], row["subject_type"], row["subject_id"]) == (
        "GRANT",
        "SALES_ORDER",
        so["id"],
    )
    (event,) = _rows("events", "event_type = 'gates.override.granted'")
    assert set(event["payload"]) == {
        "override_id",
        "subject_type",
        "subject_id",
        "gate_code",
        "line_id",
    }
    assert "특별가" not in str(event["payload"])
    (audit,) = _rows("audit_log", "action = 'gates.override.granted'")
    assert audit["entity_id"] == row["id"] and "특별가" not in str(audit["detail"])


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("1234", 422),
        ("12345", 201),
        ("가" * 500, 201),
        ("가" * 501, 422),
        ("    abc    ", 422),
        ("줄\n바꿈사유입니다", 422),
    ],
    ids=["4", "5", "500", "501", "padded-3", "newline"],
)
def test_the_reason_must_be_five_to_five_hundred_characters(reason: str, status: int) -> None:
    """사유 4자·공백 패딩 3자·501자·제어문자는 422, 5자·500자는 통과 — 경계"""
    so, line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        response = _post(client, so["id"], override_body(item, reason))
    assert response.status_code == status, response.text
    assert len(_rows("gate_overrides")) == (1 if status == 201 else 0)


@pytest.mark.parametrize("role", [RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER])
def test_logistics_cert_and_viewer_can_never_override(role: RoleCode) -> None:
    """물류·인증·조회 역할은 어느 override도 403(라우트 상한) — 행이 생기지 않는다"""
    so, line = _price_block()
    with logged_in(TRADE) as trade, logged_in(role) as other:
        item = find(gates_of(trade, so["id"]), "PRICE_DEVIATION", line)
        response = _post(other, so["id"], override_body(item))
    assert response.status_code == 403
    assert _rows("gate_overrides") == []


def test_the_market_readiness_gate_is_admin_only_trade_gets_403_from_the_service() -> None:
    """준비도 override는 ADMIN만 — 무역은 서비스가 403 GATES.OVERRIDE.NOT_ALLOWED(라우트는 통과한다: 서비스 판정). 판정 응답의 can_override도 역할별로 다르다. ADMIN은 201"""
    so = passing_so(market_color="RED")
    (line,) = line_ids(so["id"])
    with _clients(TRADE, ADMIN) as (trade, admin):
        t_item = find(gates_of(trade, so["id"]), "MARKET_READINESS", line)
        a_item = find(gates_of(admin, so["id"]), "MARKET_READINESS", line)
        assert (t_item["can_override"], a_item["can_override"]) == (False, True)
        assert t_item["override_roles"] == ["ADMIN"]
        denied = _post(trade, so["id"], override_body(t_item))
        assert (
            denied.status_code == 403
            and denied.json()["error"]["code"] == "GATES.OVERRIDE.NOT_ALLOWED"
        )
        assert _rows("gate_overrides") == []
        ok = _post(admin, so["id"], override_body(a_item, "관리자 확인 후 예외"))
        assert ok.status_code == 201 and ok.json()["authorized_role"] == "ADMIN"


def test_an_unknown_readiness_state_can_be_overridden_by_an_admin_with_the_unknown_result_recorded() -> (
    None
):
    """준비도 GRAY(UNKNOWN)는 ADMIN override 대상 — 부여 행의 result_at_grant가 UNKNOWN으로 남는다(통과로 읽히지 않았던 판정의 기록)"""
    so = passing_so(market_color="GRAY")
    (line,) = line_ids(so["id"])
    with logged_in(ADMIN) as admin:
        item = find(gates_of(admin, so["id"]), "MARKET_READINESS", line)
        assert item["level"] == "UNKNOWN"
        response = _post(admin, so["id"], override_body(item, "요건 미등록 시장 — 관리자 판단"))
        assert response.status_code == 201 and response.json()["result_at_grant"] == "UNKNOWN"
        assert gates_of(admin, so["id"])["clearance"]["cleared"] is True


def test_moq_override_is_allowed_for_trade_and_admin() -> None:
    """MOQ 미달 override는 무역·관리자 모두 가능"""
    for role in (TRADE, ADMIN):
        so = passing_so(quantity=3, moq=10)
        (line,) = line_ids(so["id"])
        with logged_in(role) as client:
            item = find(gates_of(client, so["id"]), "MOQ", line)
            assert item["level"] == "BLOCK"
            assert _post(client, so["id"], override_body(item, "샘플 소량 합의")).status_code == 201
            assert gates_of(client, so["id"])["clearance"]["cleared"] is True


def test_the_pi_deposit_gate_is_admin_only_and_both_block_and_unknown_are_overridable() -> None:
    """PI 입금 게이트: BLOCK(입금 부족)·UNKNOWN(PI 없음) 모두 ADMIN override — 무역은 403"""
    pi = advance_pi()
    short = raw_so("RECEIVED", pi_id=pi)
    with _clients(TRADE, ADMIN) as (trade, admin):
        item = find(gates_of(admin, short), "PI_DEPOSIT")
        assert (item["level"], item["reason_code"]) == ("BLOCK", "SHORT")
        assert _post(trade, short, override_body(item)).status_code == 403
        assert _post(admin, short, override_body(item, "선수금 입금 예정 확인")).status_code == 201
        assert find(gates_of(admin, short), "PI_DEPOSIT")["settlement"] == "OVERRIDDEN"
        no_pi = raw_so("RECEIVED")
        missing = find(gates_of(admin, no_pi), "PI_DEPOSIT")
        assert (missing["level"], missing["reason_code"]) == ("UNKNOWN", "PI_MISSING")
        granted = _post(admin, no_pi, override_body(missing, "PI 발행 전 예외"))
        assert granted.status_code == 201 and granted.json()["result_at_grant"] == "UNKNOWN"


# ══ override 불가 게이트 ═══════════════════════════════════════════════════════


def test_item_mapping_duplicate_po_and_credit_cannot_be_overridden_by_anyone() -> None:
    """품번 매핑(BLOCK/NONE)·중복 PO(WARN)·여신(BLOCK/APPROVAL) override는 ADMIN·무역 모두 422 NOT_APPLICABLE — 행이 생기지 않는다. 여신은 승인으로만 해소된다"""
    so = credit_so(limit=100_000, unit_price=40_000, quantity=5)
    _sql(
        "UPDATE skus SET deleted_at = now() WHERE id = :s", s=_sku_of(so["id"])
    )  # 품번 게이트 BLOCK(SKU 삭제)
    with _clients(TRADE, ADMIN) as (trade, admin):
        report = gates_of(admin, so["id"])
        (mapping_line,) = line_ids(so["id"])
        targets = [
            find(report, "CREDIT"),
            find(report, "DUPLICATE_PO"),
            find(report, "ITEM_MAPPING", mapping_line),
        ]
        assert [(t["level"], t["resolution"]) for t in targets] == [
            ("BLOCK", "APPROVAL"),
            ("WARN", "NONE"),
            ("BLOCK", "NONE"),
        ]
        for client in (admin, trade):
            for target in targets:
                response = _post(
                    client, so["id"], override_body(target, "우회를 시도합니다 관리자")
                )
                assert response.status_code == 422, (target["gate_code"], response.text)
                assert response.json()["error"]["code"] == "GATES.OVERRIDE.NOT_APPLICABLE"
    assert _rows("gate_overrides") == []
    assert all(t["can_override"] is False and t["override_roles"] == [] for t in targets)


def _sql(sql: str, **params: Any) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


def _sku_of(so_id: int) -> int:
    return int(scalar("SELECT sku_id FROM sales_order_lines WHERE so_id = :i", i=so_id))


def test_a_credit_approval_resolves_the_credit_gate_but_an_override_never_does() -> None:
    """여신 초과는 승인(소비 가능한 APPROVED)이 있을 때만 해소로 정산된다(settlement=APPROVED·approval_id) — 승인 전에는 미해소·needs_approval, ADMIN override 시도는 422"""
    so = credit_so(limit=100_000, unit_price=40_000, quantity=5)
    with logged_in(ADMIN) as admin:
        before = gates_of(admin, so["id"])
        credit = find(before, "CREDIT")
        assert (
            credit["settlement"] == "UNRESOLVED" and before["clearance"]["needs_approval"] is True
        )
        assert before["approval"] == {"available": False, "approval_id": None}
        assert _post(admin, so["id"], override_body(credit)).status_code == 422
        approval_id, _, _ = approved_for(so)
        after = gates_of(admin, so["id"])
        assert find(after, "CREDIT")["settlement"] == "APPROVED"
        assert after["approval"] == {"available": True, "approval_id": approval_id}


# ══ 낡은 판정·해시 결속 ════════════════════════════════════════════════════════


def test_a_stale_judgement_hash_is_refused_with_409() -> None:
    """사람이 본 판정 이후 단가가 1원 바뀌어 판정(해시)이 달라졌으면 409 GATES.OVERRIDE.STALE — 새로 불러온 해시로는 통과"""
    so, line = _price_block(1100)
    with logged_in(TRADE) as client:
        seen = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        set_line(so["id"], 1, unit_price=1101)
        stale = _post(client, so["id"], override_body(seen))
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "GATES.OVERRIDE.STALE"
        assert _rows("gate_overrides") == []
        fresh = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert fresh["basis_hash"] != seen["basis_hash"]
        assert _post(client, so["id"], override_body(fresh)).status_code == 201


def test_changing_the_input_after_a_grant_invalidates_it_and_the_gate_blocks_again() -> None:
    """부여 뒤 단가를 1원 바꾸면 해시가 달라져 override가 자동 무효 — 다시 BLOCK·확정 불가(재승인 필요). 입력을 원래대로 되돌리면 같은 해시라 부여가 다시 유효하다(해시 결속의 정의)"""
    so, line = _price_block(1100)
    with logged_in(TRADE) as client:
        original = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert _post(client, so["id"], override_body(original)).status_code == 201
        assert gates_of(client, so["id"])["clearance"]["cleared"] is True
        set_line(so["id"], 1, unit_price=1101)
        moved = gates_of(client, so["id"])
        assert (
            find(moved, "PRICE_DEVIATION", line)["settlement"] == "UNRESOLVED"
            and moved["clearance"]["cleared"] is False
        )
        set_line(so["id"], 1, unit_price=1100)
        back = gates_of(client, so["id"])
        assert (
            find(back, "PRICE_DEVIATION", line)["settlement"] == "OVERRIDDEN"
            and back["clearance"]["cleared"] is True
        )


def test_an_override_for_a_result_that_is_now_a_pass_or_a_warning_is_not_applicable() -> None:
    """지금 PASS(이내)이거나 WARN(무상 라인)인 결과에 override를 부여하려 하면 422 NOT_APPLICABLE — 조용한 통과가 되지 않는다"""
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    with logged_in(ADMIN) as client:
        passed = find(gates_of(client, so["id"]), "PRICE_DEVIATION")
        assert passed["level"] == "PASS"
        r1 = _post(client, so["id"], override_body(passed))
        assert (
            r1.status_code == 422 and r1.json()["error"]["code"] == "GATES.OVERRIDE.NOT_APPLICABLE"
        )
        make_free(so["id"])
        (line,) = line_ids(so["id"])
        warned = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert warned["level"] == "WARN"
        r2 = _post(client, so["id"], override_body(warned))
        assert (
            r2.status_code == 422 and r2.json()["error"]["code"] == "GATES.OVERRIDE.NOT_APPLICABLE"
        )
    assert _rows("gate_overrides") == []


def test_other_orders_lines_and_unknown_orders_are_404_and_the_body_is_strict() -> None:
    """다른 SO의 라인 id는 404(IDOR 방어)·없는 SO는 404, 모르는 필드·잘못된 게이트·형식이 틀린 해시는 422"""
    so, line = _price_block()
    other, other_line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert _post(client, so["id"], override_body(item, line_id=other_line)).status_code == 404
        assert _post(client, 9_999_999, override_body(item)).status_code == 404
        assert _post(client, so["id"], {**override_body(item), "extra": 1}).status_code == 422
        assert _post(client, so["id"], override_body(item, gate_code="BOGUS")).status_code == 422
        assert _post(client, so["id"], override_body(item, basis_hash="xyz")).status_code == 422
        assert _post(client, so["id"], override_body(item, line_id=0)).status_code == 422
    assert _rows("gate_overrides") == []
    assert other["id"] != so["id"]


@pytest.mark.parametrize("status", ["ON_HOLD", "CANCELLED"])
def test_a_non_open_order_does_not_accept_overrides(status: str) -> None:
    """접수(RECEIVED)가 아닌 SO(보류·취소)에는 override를 부여·철회할 수 없다 — 409"""
    so, line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert _post(client, so["id"], override_body(item)).status_code == 201
        _sql("UPDATE sales_orders SET status = :s WHERE id = :i", s=status, i=so["id"])
        refused = _post(client, so["id"], override_body(item, "재시도 사유입니다"))
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "GATES.OVERRIDE.ORDER_NOT_OPEN"
        assert _post(client, so["id"], override_body(item), revoke=True).status_code == 409
        assert find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)["can_override"] is False
    assert len(_rows("gate_overrides")) == 1


# ══ 철회 ═══════════════════════════════════════════════════════════════════════


def test_revoking_removes_the_clearance_and_only_the_granter_or_an_admin_may_revoke() -> None:
    """철회(REVOKE 행 추가): 부여자 본인은 가능·다른 무역은 403·ADMIN은 가능 — 철회 뒤 다시 미해소이고 같은 판정에 새로 부여할 수 있다. 이미 철회한 것·부여한 적 없는 해시는 422"""
    so, line = _price_block()
    with _clients(TRADE, TRADE, ADMIN) as (granter, other, admin):
        item = find(gates_of(granter, so["id"]), "PRICE_DEVIATION", line)
        assert _post(granter, so["id"], override_body(item)).status_code == 201
        denied = _post(other, so["id"], override_body(item, "남의 부여 철회"), revoke=True)
        assert (
            denied.status_code == 403
            and denied.json()["error"]["code"] == "GATES.OVERRIDE.NOT_ALLOWED"
        )
        revoked = _post(granter, so["id"], override_body(item, "합의에 따른 철회"), revoke=True)
        assert revoked.status_code == 201 and revoked.json()["action"] == "REVOKE"
        report = gates_of(granter, so["id"])
        assert (
            find(report, "PRICE_DEVIATION", line)["settlement"] == "UNRESOLVED"
            and report["clearance"]["cleared"] is False
        )
        again = _post(granter, so["id"], override_body(item, "합의에 따른 철회"), revoke=True)
        assert again.status_code == 422
        assert again.json()["error"]["code"] == "GATES.OVERRIDE.NOT_GRANTED"
        unknown = _post(granter, so["id"], override_body(item, basis_hash="b" * 64), revoke=True)
        assert unknown.status_code == 422
        # 철회된 판정의 재부여는 ADMIN만 — 부여자 본인(무역)이 자기 철회를 되돌리지 못한다
        retry = _post(granter, so["id"], override_body(item, "다시 합의합니다"))
        assert retry.status_code == 403
        assert retry.json()["error"]["code"] == "GATES.OVERRIDE.NOT_ALLOWED"
        assert _post(admin, so["id"], override_body(item, "관리자 재부여")).status_code == 201
        assert (
            find(gates_of(granter, so["id"]), "PRICE_DEVIATION", line)["settlement"] == "OVERRIDDEN"
        )
        assert (
            _post(admin, so["id"], override_body(item, "관리자 정리"), revoke=True).status_code
            == 201
        )
    actions = [r["action"] for r in _rows("gate_overrides")]
    assert actions == ["GRANT", "REVOKE", "GRANT", "REVOKE"]
    assert [e["event_type"] for e in _rows("events", "event_type LIKE 'gates.override.%'")] == [
        "gates.override.granted",
        "gates.override.revoked",
        "gates.override.granted",
        "gates.override.revoked",
    ]


def test_a_stale_grant_can_still_be_revoked_by_its_hash() -> None:
    """입력이 바뀌어 낡아진 부여도 해시로 찾아 철회할 수 있다 — 철회는 현재 판정과 무관하다(정리 가능)"""
    so, line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert _post(client, so["id"], override_body(item)).status_code == 201
        set_line(so["id"], 1, unit_price=1199)
        assert (
            _post(client, so["id"], override_body(item, "정리합니다"), revoke=True).status_code
            == 201
        )


# ══ 멱등 ═══════════════════════════════════════════════════════════════════════


def test_the_same_key_and_body_replays_the_first_result_and_a_different_body_conflicts() -> None:
    """같은 키·같은 본문 재전송은 최초 응답을 재생(행·이벤트·감사 1건), 같은 키+다른 본문은 409 KEY_CONFLICT, 키 없는 요청은 거부"""
    so, line = _price_block()
    key = idem()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        first = _post(client, so["id"], override_body(item, "더블클릭 방어"), key=key)
        second = _post(client, so["id"], override_body(item, "더블클릭 방어"), key=key)
        assert first.status_code == second.status_code == 201 and first.json() == second.json()
        clash = _post(client, so["id"], override_body(item, "다른 사유로 재전송"), key=key)
        assert (
            clash.status_code == 409
            and clash.json()["error"]["code"] == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
        )
        no_key = client.post(
            f"/api/v1/sales-orders/{so['id']}/gate-overrides", json=override_body(item)
        )
        assert no_key.status_code == 400
    assert len(_rows("gate_overrides")) == 1
    assert len(_rows("events", "event_type = 'gates.override.granted'")) == 1
    assert len(_rows("audit_log", "action = 'gates.override.granted'")) == 1


def test_a_refused_override_does_not_consume_the_idempotency_key() -> None:
    """거부된 부여(409 낡은 해시)는 키를 소비하지 않는다 — 같은 키로 고친 본문을 보내면 KEY_CONFLICT가 아니라 처리된다(키가 소비됐다면 지문 불일치로 409 KEY_CONFLICT)"""
    so, line = _price_block()
    key = idem()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        bad = _post(client, so["id"], override_body(item, basis_hash="c" * 64), key=key)
        assert bad.status_code == 409 and bad.json()["error"]["code"] == "GATES.OVERRIDE.STALE"
        good = _post(client, so["id"], override_body(item), key=key)
        assert good.status_code == 201, good.text


# ══ 적대 검토 반영 — 재부여·마스킹·사유 위생 ═══════════════════════════════════


def test_a_valid_grant_cannot_be_granted_again_and_leaves_no_duplicate_trace() -> None:
    """이미 유효한 부여가 있는 같은 판정에 다시 부여하면 409 GATES.OVERRIDE.ALREADY_GRANTED — 행·audit·outbox가 늘지 않는다(중복 행으로 철회를 무력화하는 경로 차단)"""
    so, line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        assert _post(client, so["id"], override_body(item)).status_code == 201
        again = _post(client, so["id"], override_body(item, "중복 부여 시도"))
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "GATES.OVERRIDE.ALREADY_GRANTED"
    assert len(_rows("gate_overrides")) == 1
    assert len(_rows("events", "event_type = 'gates.override.granted'")) == 1
    assert len(_rows("audit_log", "action = 'gates.override.granted'")) == 1


def test_after_an_admin_revoke_a_trade_user_cannot_regrant_but_an_admin_can() -> None:
    """부여→ADMIN 철회→TRADE 재부여는 403, ADMIN 재부여는 201 — 상급 철회를 하위 역할이 되돌리지 못한다. (본인 철회 뒤 본인 재부여 403은 철회 테스트가 고정)"""
    so, line = _price_block()
    with _clients(TRADE, ADMIN) as (trade, admin):
        item = find(gates_of(trade, so["id"]), "PRICE_DEVIATION", line)
        assert _post(trade, so["id"], override_body(item)).status_code == 201
        assert (
            _post(admin, so["id"], override_body(item, "관리자 철회"), revoke=True).status_code
            == 201
        )
        assert _post(trade, so["id"], override_body(item, "되돌리기를 시도")).status_code == 403
        assert gates_of(trade, so["id"])["clearance"]["cleared"] is False
        assert _post(admin, so["id"], override_body(item, "관리자 재부여")).status_code == 201
        assert gates_of(trade, so["id"])["clearance"]["cleared"] is True
    assert [r["action"] for r in _rows("gate_overrides")] == ["GRANT", "REVOKE", "GRANT"]


def test_credit_hash_duplicate_detail_and_override_reasons_are_hidden_from_other_roles() -> None:
    """마스킹 역할(조회·물류)에게: CREDIT 결과의 basis_hash도 비고, override 사유(자유 텍스트)는 null. 무역에게는 보인다"""
    so, line = _price_block()
    with _clients(TRADE, RoleCode.VIEWER, RoleCode.LOGISTICS) as (trade, viewer, logistics):
        item = find(gates_of(trade, so["id"]), "PRICE_DEVIATION", line)
        assert (
            _post(trade, so["id"], override_body(item, "원가 서술이 섞인 사유")).status_code == 201
        )
        seen = gates_of(trade, so["id"])
        assert find(seen, "PRICE_DEVIATION", line)["override"]["reason"] == "원가 서술이 섞인 사유"
        assert len(find(seen, "CREDIT")["basis_hash"]) == 64
        for client in (viewer, logistics):
            hidden = gates_of(client, so["id"])
            assert find(hidden, "PRICE_DEVIATION", line)["override"]["reason"] is None
            assert find(hidden, "CREDIT")["basis_hash"] == ""
            assert find(hidden, "DUPLICATE_PO")["detail"] == {}


@pytest.mark.parametrize(
    "reason",
    [
        "가나다라\u3164",
        "가나다라\u200b마",
        "가나다라\u115f마",
        "\u1160" * 5,
        "가나다라\u2028마",
        "a b c d",
        "\u00a0" * 3 + "ab" + "\u3000" * 3,
        "가나다\ufeff라마",
    ],
    ids=[
        "hangul-filler",
        "zero-width",
        "choseong",
        "jungseong",
        "line-sep",
        "four-real",
        "nbsp",
        "bom",
    ],
)
def test_invisible_and_whitespace_only_reasons_are_refused(reason: str) -> None:
    """보이지 않는 글자(Cf·한글 채움·줄 구분)·공백으로 길이만 채운 사유·실질 글자 4자 이하는 422 — 5자 규칙 우회 차단"""
    so, line = _price_block()
    with logged_in(TRADE) as client:
        item = find(gates_of(client, so["id"]), "PRICE_DEVIATION", line)
        response = _post(client, so["id"], override_body(item, reason))
    assert response.status_code == 422, response.text
    assert _rows("gate_overrides") == []


def test_the_duplicate_po_detail_is_shown_to_trade_and_admin_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """중복 PO 결과의 다른 문서번호·상태(표시 전용 detail)는 무역·관리자 응답에만 있고 조회·인증 역할은 빈 값 — 판정 해시에는 영향이 없다"""
    from app.modules.gates.policy import outcome
    from app.modules.gates.types import GateLevel, GateResolution

    def duplicate(*_args: object) -> list[object]:
        return [
            outcome(
                "DUPLICATE_PO",
                GateLevel.BLOCK,
                GateResolution.NONE,
                "DUPLICATE_PO_NO",
                "중복",
                {"buyer_po_no_key": "K"},
                detail={"other_doc_number": "SO-2026-0001", "other_status": "RECEIVED"},
            )
        ]

    monkeypatch.setitem(DEFAULT_REGISTRY._evaluators, "DUPLICATE_PO", duplicate)
    so = passing_so()
    with _clients(TRADE, ADMIN, RoleCode.VIEWER, RoleCode.CERT) as (trade, admin, viewer, cert):
        for client in (trade, admin):
            shown = find(gates_of(client, so["id"]), "DUPLICATE_PO")["detail"]
            assert shown["other_doc_number"] == "SO-2026-0001"
        for client in (viewer, cert):
            assert find(gates_of(client, so["id"]), "DUPLICATE_PO")["detail"] == {}
