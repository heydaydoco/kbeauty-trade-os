"""K. 입금 원장 계약 스캔 — 방향 반전(trade_chain→payments)·순입금 단일 정의·원장 함수의 무잠금 규율·오케스트레이션 순서·로그 무참조·불변 원장 (S3-1 PR-10a / ADR-0068 / design-E E7).

스캔 테스트는 공회전하기 쉽다 — 각 검사는 (a) 대상이 실제로 있고 (b) 위반 코퍼스를 잡는지(자기검사)를 함께 본다.
"""

from __future__ import annotations

import ast

import pytest

from app.core.db.base import Base
from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES
from tests.support.astscan import (
    app_sources,
    called_names,
    imported_modules,
    module_of,
    parse_source,
    referenced_names,
)

pytestmark = pytest.mark.group_k

LEDGER = "modules/payments/service.py"
FLOW = "modules/trade_chain/payment_flow.py"


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _first_call_line(node: ast.AST, name: str) -> int:
    lines = [
        n.lineno
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        )
    ]
    assert lines, f"{name} 호출이 없다"
    return min(lines)


def test_the_direction_is_reversed_trade_chain_imports_payments_never_the_other_way() -> None:
    """방향 반전 — payments 모듈은 trade_chain을 임포트하지 않고, 오케스트레이터(trade_chain/payment_flow)가 payments를 임포트한다(§2.8 정방향)"""
    sources = app_sources()
    owned = {rel: tree for rel, tree in sources.items() if module_of(rel) == "payments"}
    assert {"modules/payments/models.py", LEDGER, "modules/payments/router.py"} <= set(owned)
    for rel, tree in owned.items():
        assert "trade_chain" not in imported_modules(tree), rel
    assert "payments" in imported_modules(sources[FLOW])
    assert "payments" in imported_modules(sources["modules/trade_chain/router.py"])


def test_the_pure_ledger_never_locks_claims_keys_or_converges() -> None:
    """원장 함수는 PI 잠금·멱등 선점·상태 수렴·트랜잭션 개설을 하지 않는다 — 그것은 오케스트레이터 한 곳의 책임이다(잠금 단일 진입 lock_chain)"""
    names = referenced_names(app_sources()[LEDGER])
    for forbidden in (
        "lock_chain",
        "lock_document",
        "converge_payment_status",
        "record_transition",
        "claim",
        "complete",
        "unit_of_work",
        "with_for_update",
    ):
        assert forbidden not in names, forbidden


def test_net_received_has_a_single_definition_and_nobody_else_sums_the_ledger() -> None:
    """순입금의 유일한 정의는 `payments.service.net_received_for_pi` — `received_amount`의 SUM은 그 함수 한 곳뿐이고(다른 집계·PI 상태로 대체 금지), 오케스트레이터는 그 함수를 쓴다"""
    sums: list[tuple[str, str]] = []
    for rel, tree in app_sources().items():
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            src = ast.unparse(fn)
            if "func.sum" in src and "received_amount" in src:
                sums.append((rel, fn.name))
    assert sums == [(LEDGER, "net_received_for_pi")]
    assert "net_received_for_pi" in referenced_names(app_sources()[LEDGER])
    assert "append_receipt" in called_names(app_sources()[FLOW])


def test_the_orchestrator_orders_claim_then_lock_then_ledger_then_converge_then_complete() -> None:
    """두 오케스트레이션 모두 멱등 선점 → PI lock_chain → 원장 함수 → converge_payment_status → complete 순서(AST 줄 순서) — 잠금 전 쓰기·수렴 전 완료 기록 금지"""
    tree = app_sources()[FLOW]
    for function, ledger_call in (
        ("record_receipt", "append_receipt"),
        ("reverse_payment", "append_reversal"),
    ):
        node = _function(tree, function)
        order = [
            _first_call_line(node, "claim"),
            _first_call_line(node, "lock_chain"),
            _first_call_line(node, ledger_call),
            _first_call_line(node, "_converge"),
            _first_call_line(node, "complete"),
        ]
        assert order == sorted(order), (function, order)
    # 수렴 호출은 헬퍼 한 곳에서만 일어나고, 헬퍼가 converge_payment_status를 부른다
    assert "converge_payment_status" in called_names(
        ast.Module(body=[_function(tree, "_converge")], type_ignores=[])
    )


def test_the_reversal_peeks_without_a_lock_then_locks_the_pi_before_any_write() -> None:
    """역기록은 무잠금으로 원 입금→PI id만 얻고(입금 행은 불변) PI를 잠근 뒤에야 원장에 쓴다 — 원 입금 id로 직접 쓰기·잠금 생략 금지"""
    node = _function(app_sources()[FLOW], "reverse_payment")
    assert _first_call_line(node, "get_payment") < _first_call_line(node, "lock_chain")
    assert _first_call_line(node, "lock_chain") < _first_call_line(node, "append_reversal")


def _keys_of_audit_and_outbox(tree: ast.Module) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("record", "publish")
        ):
            for kw in node.keywords:
                if kw.arg in ("detail", "payload") and isinstance(kw.value, ast.Dict):
                    keys |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
    return keys


def test_audit_and_outbox_payload_keys_never_include_free_text() -> None:
    """audit detail·outbox payload에는 참조 텍스트(reference)·사유(reason)·금액 문자열 키가 없다 — id·금액(정수)·통화·증빙일·net_after뿐(로그·이벤트로 새지 않게)"""
    keys: set[str] = set()
    for rel in (LEDGER, FLOW):
        keys |= _keys_of_audit_and_outbox(app_sources()[rel])
    assert keys, "스캔이 audit·outbox 호출을 하나도 못 찾았다(공회전)"
    assert not keys & {"reference", "reason", "bank_reference", "memo", "note"}, keys


def test_the_free_text_scan_catches_a_synthetic_leak() -> None:
    """자기검사 — reference를 audit detail에 싣는 코퍼스를 잡는다"""
    bad = parse_source(
        "def f(s, r):\n    audit.record(s, action='x', detail={'reference': r.reference})\n"
    )
    assert "reference" in _keys_of_audit_and_outbox(bad)


def test_payments_is_an_immutable_ledger_with_no_update_or_delete_in_app_code() -> None:
    """payments는 IMMUTABLE 분류(MUTABLE 아님)이고, 앱 코드 어디에도 payments에 대한 UPDATE·DELETE 구문(`update(Payment)`·`delete(Payment)`·`session.delete(...Payment)`)이 없다"""
    assert "payments" in IMMUTABLE_TABLES and "payments" not in MUTABLE_TABLES
    offenders: list[str] = []
    for rel, tree in app_sources().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("update", "delete")
                and node.args
                and "Payment" in ast.unparse(node.args[0])
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
    assert "payments" in Base.metadata.tables


def test_the_ledger_model_has_no_mutation_trail_columns() -> None:
    """원장 모델에는 믹스인 열(updated_at·deleted_at·version·created_by_id·updated_by_id)이 없다 — INSERT-only"""
    columns = set(Base.metadata.tables["payments"].columns.keys())
    assert not columns & {"updated_at", "deleted_at", "version", "created_by_id", "updated_by_id"}
    assert {"recorded_by_id", "created_at", "reverses_payment_id", "received_on"} <= columns


def test_the_list_endpoint_is_paginated_and_writes_require_trade_roles() -> None:
    """GET 원장은 PageParams(기본 50), 두 쓰기 라우트는 require_roles(TRADE)(+관리자 상시 통과)·Idempotency-Key 필수 의존성이 있다"""
    tree = app_sources()["modules/payments/router.py"]
    assert "PageParams" in ast.unparse(_function(tree, "list_pi_payments").args)
    for function in ("record_pi_payment", "reverse_payment"):
        node = _function(app_sources()["modules/trade_chain/router.py"], function)
        assert "IdempotencyKey" in ast.unparse(node.args), function
    for decorator_owner in ("record_pi_payment", "reverse_payment"):
        node = _function(app_sources()["modules/trade_chain/router.py"], decorator_owner)
        assert "require_roles(*CAN_WRITE)" in ast.unparse(node.decorator_list)


def test_amount_is_never_a_float_in_the_payment_layer() -> None:
    """입금 계층(payments·payment_flow)에 float 사용이 없다 — 금액은 문자열→정수 최소단위"""
    for rel in (LEDGER, FLOW, "modules/payments/models.py", "modules/payments/schemas.py"):
        names = referenced_names(app_sources()[rel])
        assert "float" not in names and "Float" not in names, rel
