"""K. 확정 통로 계약 — 유일 통로·우회 인자 부재·잠금/소비/커밋 순서·증적 열 쓰기 통로·void 훅 배선 (S3-1 PR-12a / design-E E4 · design-integrated §2.10 / ADR-0070).

정의가 아니라 **코드 구조**를 고정한다(AST): 확정 서비스의 호출 순서(거래처 잠금 → 사슬 잠금 → 권위 평가 → 소비 → 전이 → 수렴 → 할당 → 멱등 완료)·실패 응답이 UoW **밖에서** raise되는 구조(커밋 후 raise)·
SO가 CONFIRMED가 되는 호출처 1곳·증적 3열의 쓰기 통로 1곳·편집·취소 6경로의 승인 무효화 훅. 변이(순서 반전·훅 삭제 등)는 이 구조 검사와 동작 테스트가 각각 잡는다.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.modules.trade_chain import approval_requests, confirm
from app.modules.trade_chain.schemas import SalesOrderApprovalRequest, SalesOrderConfirmRequest
from tests.support.astscan import app_sources, called_names

pytestmark = pytest.mark.group_k

CONFIRM = "modules/trade_chain/confirm.py"
FORBIDDEN_PARAMS = frozenset(
    {"force", "override", "admin", "admin_bypass", "bypass", "skip", "skip_checks", "system", "auto", "automatic", "sudo", "as_admin", "approval_id", "status"}
)  # fmt: skip


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _call_lines(node: ast.AST) -> dict[str, list[int]]:
    """함수 안 호출 이름 → 줄 번호 목록(`a.b.f()`의 f)."""
    lines: dict[str, list[int]] = {}
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            target = call.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name:
                lines.setdefault(name, []).append(call.lineno)
    return lines


# ── 시그니처·요청 스키마 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "function",
    [confirm.confirm_sales_order, approval_requests.request_credit_approval],
    ids=["confirm_sales_order", "request_credit_approval"],
)
def test_the_confirm_channel_signatures_have_no_bypass_or_automatic_parameters(
    function: object,
) -> None:
    """확정·승인 요청 함수는 keyword-only `actor`(실 사용자, None 불가)·`idempotency_key`·`so_id`·`version`뿐이다 — force·skip·bypass·override·admin·system·auto가 시그니처에 없다(ADMIN 분기가 구조적으로 불가능)"""
    params = inspect.signature(function).parameters  # type: ignore[arg-type]
    assert set(params) == {"actor", "idempotency_key", "so_id", "version"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert not set(params) & FORBIDDEN_PARAMS
    assert "AuthenticatedUser" in str(params["actor"].annotation)
    assert "None" not in str(params["actor"].annotation)


def test_the_request_schemas_carry_only_the_version_and_forbid_extras() -> None:
    """확정·승인 요청 본문 = `version` 하나 · extra=forbid — 승인·판정·상태·우회 필드를 실을 수 없다"""
    for schema in (SalesOrderConfirmRequest, SalesOrderApprovalRequest):
        assert set(schema.model_fields) == {"version"}
        assert schema.model_config.get("extra") == "forbid"


def test_no_role_branch_exists_in_the_confirm_channel_bodies() -> None:
    """확정·승인 요청 본문에 `ADMIN` 참조가 없다 — 관리자 분기·예외가 코드에 없다(역할 사전 검증은 모듈 상수 `…_ROLES` 하나뿐이고 TRADE·ADMIN을 **같이** 허용할 뿐이다)"""
    sources = app_sources()
    for rel, name in (
        (CONFIRM, "confirm_sales_order"),
        ("modules/trade_chain/approval_requests.py", "request_credit_approval"),
    ):
        body = _function(sources[rel], name)
        attrs = {n.attr for n in ast.walk(body) if isinstance(n, ast.Attribute)}
        assert "ADMIN" not in attrs and "is_admin" not in attrs, (rel, name)
        assert "roles" in attrs  # 사전 검증 자체는 존재한다(공회전 방지)


# ── 호출 순서 — 잠금 → 평가 → 소비 → 전이 → 수렴 → 할당 → 완료 ────────────────────────────


def test_the_confirmation_runs_its_steps_in_the_documented_order() -> None:
    """`confirm_sales_order`의 첫 호출 순서: 멱등 claim → 거래처 잠금 → 사슬 잠금 → 권위 평가 → **소비** → 전이 → 증거(CONFIRMED) → 부모 수렴 → 할당 포트 → 멱등 완료.
    거래처가 SO보다 먼저 잠기고(교착 방지), 소비는 평가 뒤·전이 앞이며, 할당 포트는 전이 뒤(예외 시 전체 롤백)다"""
    body = _function(app_sources()[CONFIRM], "confirm_sales_order")
    lines = _call_lines(body)
    order = [
        "claim",
        "lock_buyer_for_credit",
        "lock_chain",
        "evaluate_sales_order",
        "consume_approval",
        "record_transition",
        "converge_parent",
        "on_confirmed",
        "complete",
    ]
    firsts = [min(lines[name]) for name in order]
    assert firsts == sorted(firsts), dict(zip(order, firsts, strict=True))
    # 증거 기록은 두 번 이상 — 차단 시도(BLOCKED)와 성공(CONFIRMED). 성공 증거는 전이 뒤다.
    assert len(lines["record_evaluation"]) >= 3
    assert max(lines["record_evaluation"]) > min(lines["record_transition"])


def test_the_blocked_response_is_raised_after_the_unit_of_work_commits() -> None:
    """실패도 커밋 — 차단·소비 시점 거부는 `deferred`에 담았다가 `with unit_of_work()` 블록 **밖에서** raise한다(안에서 raise하면 BLOCKED 증거·우회 시도 audit·VOID가 롤백된다 — 11a 인계 ⑦)"""
    body = _function(app_sources()[CONFIRM], "confirm_sales_order")
    with_block = next(
        n
        for n in body.body
        if isinstance(n, ast.With)
        and any("unit_of_work" in ast.unparse(item.context_expr) for item in n.items)
    )
    inside = {id(n) for n in ast.walk(with_block)}
    raises = [n for n in ast.walk(body) if isinstance(n, ast.Raise) and n.exc is not None]
    deferred_raises = [r for r in raises if ast.unparse(r.exc) == "deferred"]
    assert len(deferred_raises) == 1 and id(deferred_raises[0]) not in inside
    # 블록 안의 raise는 롤백을 원하는 경우뿐 — 차단 응답(GATE_BLOCKED)은 안에서 raise하지 않는다
    for r in raises:
        if id(r) in inside:
            assert "GATE_BLOCKED" not in ast.unparse(r.exc), ast.unparse(r)


def test_the_consume_result_is_inspected_and_never_ignored() -> None:
    """`consume_approval`은 예외 대신 결과값을 돌려준다 — 확정 통로는 `ConsumeOutcome.BLOCKED`를 반드시 검사한다(결과 무시 = 승인 없는 확정)"""
    tree = app_sources()[CONFIRM]
    body = _function(tree, "confirm_sales_order")
    names = {n.attr for n in ast.walk(body) if isinstance(n, ast.Attribute)}
    assert {"BLOCKED", "CONSUMED"} <= names
    assert "consumed" in {n.id for n in ast.walk(body) if isinstance(n, ast.Name)}


def test_the_approval_is_consumed_only_when_the_clearance_is_cleared() -> None:
    """소비는 `clr.cleared` 분기의 else 쪽에서만 호출된다 — 미해소 시도에서는 소비하지 않는다(승인은 해소가 확정된 뒤의 마지막 부작용)"""
    body = _function(app_sources()[CONFIRM], "confirm_sales_order")
    branch = next(
        n for n in ast.walk(body) if isinstance(n, ast.If) and "cleared" in ast.unparse(n.test)
    )
    assert "consume_approval" not in {c for stmt in branch.body for c in _call_lines(stmt)}, (
        "미해소 분기에서 승인을 소비한다"
    )
    assert "consume_approval" in {c for stmt in branch.orelse for c in _call_lines(stmt)}


# ── SO를 CONFIRMED로 만드는 길은 하나 ──────────────────────────────────────────────────────


def test_only_the_confirm_channel_names_confirmed_when_transitioning_an_order() -> None:
    """`record_transition(…, "CONFIRMED", …)`을 리터럴로 부르는 파일은 확정 통로 하나뿐이다(재개 전이는 `to` 변수로 받는다) — 동결 액션 엣지의 호출처 1곳"""
    found = set()
    for rel, tree in app_sources().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Name) and node.func.id == "record_transition")
                    or (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "record_transition"
                    )
                )
                and any(isinstance(a, ast.Constant) and a.value == "CONFIRMED" for a in node.args)
            ):
                found.add(rel)
    assert found == {CONFIRM}


def test_the_evidence_columns_are_written_only_by_the_confirm_channel() -> None:
    """확정 증적 3열(`credit_verdict`·`credit_approval_id`·`pi_gate_verdict`)을 대입하거나 생성자에 넘기는 코드는 확정 통로뿐이다 — 마이그레이션·모델 정의 외에 다른 쓰기 경로가 없다(PATCH 스키마에도 필드 없음)"""
    columns = {"credit_verdict", "credit_approval_id", "pi_gate_verdict"}
    writers: set[str] = set()
    for rel, tree in app_sources().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr in columns:
                        writers.add(rel)
            if isinstance(node, ast.Call) and any(kw.arg in columns for kw in node.keywords):
                writers.add(rel)
    assert writers == {CONFIRM}
    from app.modules.sales_orders.schemas import (
        SalesOrderMetaUpdateRequest,
        SalesOrderUpdateRequest,
    )

    for schema in (SalesOrderUpdateRequest, SalesOrderMetaUpdateRequest):
        assert not columns & set(schema.model_fields)


def test_converge_parent_and_the_allocation_port_are_reached_from_the_confirm_channel() -> None:
    """QT 수주전환(`converge_parent`)·할당 포트(`on_confirmed`)의 확정 쪽 호출처는 확정 통로다 — 확정 서비스 밖에서 `on_confirmed`를 부르는 코드가 없다"""
    callers = {rel for rel, tree in app_sources().items() if "on_confirmed" in called_names(tree)}
    assert callers == {CONFIRM}


# ── 편집·취소 void 훅 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rel", "function"),
    [
        ("modules/sales_orders/service.py", "update_sales_order"),
        ("modules/sales_orders/service.py", "add_line"),
        ("modules/sales_orders/service.py", "update_line"),
        ("modules/sales_orders/service.py", "remove_line"),
        ("modules/trade_chain/lifecycle.py", "_cancel_sales_order"),
    ],
)
def test_every_input_changing_edit_and_the_cancellation_call_the_void_hook(
    rel: str, function: str
) -> None:
    """게이트 입력을 바꾸는 편집 4경로(헤더 환율·라인 추가·수정·제외)와 SO 취소는 같은 트랜잭션에서 열린 승인을 무효화하는 훅을 부른다(eager) — 훅이 빠지면 결재함에 낡은 승인이 남는다(lazy digest가 백스톱이지만 둘 다 둔다)"""
    body = _function(app_sources()[rel], function)
    assert "void_open_approval_on_input_change" in _call_lines(body), (rel, function)


def test_the_void_hook_only_narrows_approvals_and_calls_the_core_void_channel() -> None:
    """훅 본체는 승인 코어의 `void_for_target`(승인을 좁히기만 하는 통로)만 부른다 — 승인 부여·소비·요청 함수를 부르지 않는다"""
    body = _function(
        app_sources()["modules/sales_orders/service.py"], "void_open_approval_on_input_change"
    )
    called = set(_call_lines(body))
    assert "void_for_target" in called
    assert not called & {"decide_approval", "consume_approval", "request_approval"}


def test_the_credit_target_spec_is_registered_whenever_the_confirm_modules_are_imported() -> None:
    """소비 접점 — 확정·승인 요청 모듈이 import되면 `SO_CREDIT_EXCEEDED` TargetSpec이 이미 등록돼 있다(spec 없이 소비·요청하면 fail-closed라, 등록 누락이 조용히 통과하지 못하게)"""
    from app.modules.approvals.registry import registered_specs

    spec = registered_specs()["SO_CREDIT_EXCEEDED"]
    assert spec.consumer_module == "trade_chain"
    assert "consume_approval" in called_names(app_sources()[CONFIRM])


def test_so_edit_paths_lock_the_header_row_with_lock_document_before_changing_anything() -> None:
    """11a 인계 ⑥ — SO 편집·메타·라인 4경로는 `lock_document`로 SO 행을 `FOR UPDATE` 잠근 뒤 변경한다(확정 통로의 "잠금 하 평가" 가정이 편집 쪽에서도 성립: 확정과 편집이 같은 행 잠금으로 직렬화된다) —
    잠금 호출이 void 훅·변경보다 앞선다"""
    tree = app_sources()["modules/sales_orders/service.py"]
    for name in ("update_sales_order", "update_meta", "add_line", "update_line", "remove_line"):
        lines = _call_lines(_function(tree, name))
        assert "lock_document" in lines, name
        if "void_open_approval_on_input_change" in lines:
            assert min(lines["lock_document"]) < min(lines["void_open_approval_on_input_change"]), (
                name
            )
