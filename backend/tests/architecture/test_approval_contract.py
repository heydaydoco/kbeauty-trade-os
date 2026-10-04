"""K. 승인 코어 계약 — 상태 통로·자동 승인 경로 부재·우회 파라미터 부재·스키마·분류 (S3-1 PR-9a / ADR-0060·0061 / design-C C2·C4·C8).

승인 통제(§2 우회 통로 금지)의 핵심을 **문서가 아니라 코드로** 고정한다:
  ① 상태 대입 통로는 `service._record_transition` 하나(모듈 안 어디에도 `.status =` 대입이 없다) ② 스냅샷 컬럼은 서비스가 대입하지 않는다(DB 권한도 막는다)
  ③ 사람 결정 통로 `decide_approval`의 호출처는 라우터 1곳 ④ 잡·CLI·임포트·아웃박스·시드는 승인 상태를 바꾸는 함수에 닿지 못한다(`stagnation`만)
  ⑤ `force`·`override`·`admin` 같은 우회·자동 표식 파라미터가 시그니처에 없다 ⑥ 요청 스키마는 status·결정자 필드가 없고 extra=forbid ⑦ 승인 요청 생성 HTTP 라우트 부재.
공회전 방지: 각 스캔은 대상이 실제로 잡히는지(`>= N`)와 위반 코퍼스를 잡는지 자기검사를 함께 한다.
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest

from app.core.errors.catalog import ERROR_CATALOG
from app.core.errors.codes import ErrorCode
from app.modules.approvals import machine, schemas, service
from app.modules.approvals.machine import (
    ACTIVE_STATUSES,
    ALLOWED,
    HUMAN,
    REASON_REQUIRED_TO,
    SYSTEM,
    TERMINAL,
    ApprovalStatus,
    DecisionVerb,
)
from app.modules.handover.targets import ASSIGNMENT_COLUMN_NAMES, ASSIGNMENT_TARGETS
from tests.support.astscan import app_sources, imported_modules, module_of, parse_source

pytestmark = pytest.mark.group_k

APPROVALS_PREFIX = "modules/approvals/"
#: 승인 스냅샷 컬럼 — INSERT 이후 불변(DB 컬럼 권한이 막는다). 서비스도 대입하지 않는다.
SNAPSHOT_COLUMNS = frozenset(
    {
        "approval_type",
        "target_type",
        "target_id",
        "target_label",
        "requested_by_id",
        "basis_amount",
        "basis_currency",
        "snapshot_digest",
        "snapshot",
        "required_role",
        "approval_line_id",
    }
)


# ── ① 상태 대입 통로 ──────────────────────────────────────────────────────────


def _attribute_assignments(
    tree: ast.Module, names: frozenset[str] | set[str]
) -> list[tuple[str, int]]:
    """`x.<name> = …` / `x.<name> += …` 대입의 (속성, 줄) 목록 — 주석·문자열은 AST라 걸리지 않는다."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in names:
                found.append((target.attr, node.lineno))
    return found


def _enclosing_function(tree: ast.Module, line: int) -> str | None:
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.lineno <= line <= (node.end_lineno or node.lineno)
            and (best is None or node.lineno > best[0])
        ):
            best = (node.lineno, node.name)
    return best[1] if best else None


def _approval_sources() -> dict[str, ast.Module]:
    return {rel: tree for rel, tree in app_sources().items() if rel.startswith(APPROVALS_PREFIX)}


def test_the_scan_sees_the_approvals_package() -> None:
    """스캔이 승인 패키지의 실제 소스를 본다(빈 목록으로 초록을 사지 않는다)"""
    sources = _approval_sources()
    assert len(sources) >= 10
    assert {"modules/approvals/service.py", "modules/approvals/router.py"} <= set(sources)


def test_status_is_assigned_in_exactly_one_place_the_transition_function() -> None:
    """`approval.status = …` 대입은 승인 패키지 전체에서 `service._record_transition` 안 1곳뿐이다 — 전이 통로의 단일성"""
    hits = [
        (rel, line, _enclosing_function(tree, line))
        for rel, tree in _approval_sources().items()
        for attr, line in _attribute_assignments(tree, {"status"})
    ]
    assert hits == [("modules/approvals/service.py", hits[0][1], "_record_transition")], hits


def test_the_status_scan_catches_a_violation_corpus() -> None:
    """자기검사 — 다른 함수에서의 상태 대입을 실제로 잡는다"""
    bad = parse_source("def x(a):\n    a.status = 'APPROVED'\n")
    assert [(a, ln) for a, ln in _attribute_assignments(bad, {"status"})] == [("status", 2)]
    assert _enclosing_function(bad, 2) == "x"
    assert not _attribute_assignments(parse_source("x = a.status == 1\n"), {"status"})


def test_services_never_assign_the_frozen_snapshot_columns() -> None:
    """승인 패키지는 스냅샷 컬럼(유형·대상·금액·digest·역할…)에 속성 대입을 하지 않는다 — 생성자 인자로만 채운다(DB 컬럼 권한이 이중으로 막는다)"""
    offenders = [
        (rel, attr, line)
        for rel, tree in _approval_sources().items()
        for attr, line in _attribute_assignments(tree, SNAPSHOT_COLUMNS)
    ]
    assert offenders == []


def test_nobody_updates_the_approval_tables_with_raw_update_statements() -> None:
    """앱 전체에 승인 3표를 겨냥한 `update(...)`·`__table__.update`·원시 `UPDATE <표>` 문이 없다 — 변경은 ORM 속성 대입(통로)뿐이다"""
    pattern = re.compile(
        r"(update\(\s*(Approval|ApprovalLine|Delegation)\b|(Approval|ApprovalLine|Delegation)\.__table__\.update"
        r"|UPDATE\s+(approvals|approval_lines|delegations)\b)",
        re.IGNORECASE,
    )
    from pathlib import Path

    from tests.support.astscan import APP_DIR

    offenders = [
        str(path.relative_to(APP_DIR))
        for path in sorted(APP_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    assert pattern.search("session.execute(update(Approval).values(status='x'))")  # 자기검사
    assert pattern.search("UPDATE approvals SET basis_amount = 1")
    assert Path(APP_DIR).exists()


# ── 상태 기계 계약 ────────────────────────────────────────────────────────────


def test_the_state_machine_is_the_designed_one() -> None:
    """상태 6·허용 7(사람 4+도메인 3)·종결 4 — 총수 고정. 사람 전이와 시스템 전이는 겹치지 않는다"""
    assert len(ApprovalStatus) == 6
    assert (len(HUMAN), len(SYSTEM), len(ALLOWED)) == (4, 3, 7)
    assert not HUMAN & SYSTEM
    assert {
        ApprovalStatus.REJECTED,
        ApprovalStatus.WITHDRAWN,
        ApprovalStatus.CONSUMED,
        ApprovalStatus.VOIDED,
    } == TERMINAL
    # 29쌍(서로 다른 순서쌍 30 − 허용 7 + 자기 전이 6)은 전부 미허용
    all_pairs = {(a, b) for a in ApprovalStatus for b in ApprovalStatus}
    assert len(all_pairs - ALLOWED) == 29


def test_terminal_states_have_no_exit_and_every_state_is_reachable() -> None:
    """종결 4태는 탈출 전이가 없고(재기안은 신규 행), 비종결 2태는 탈출이 있으며, 모든 상태가 REQUESTED에서 도달 가능하다"""
    for state in TERMINAL:
        assert not [pair for pair in ALLOWED if pair[0] is state], state
    for state in set(ApprovalStatus) - TERMINAL:
        assert [pair for pair in ALLOWED if pair[0] is state], state
    reachable = {ApprovalStatus.REQUESTED}
    changed = True
    while changed:
        changed = False
        for a, b in ALLOWED:
            if a in reachable and b not in reachable:
                reachable.add(b)
                changed = True
    assert reachable == set(ApprovalStatus)


def test_the_active_predicate_is_exactly_the_non_terminal_states() -> None:
    """활성 유니크의 술어(REQUESTED·APPROVED) == 종결 4태의 여집합 — 대상당 활성 승인 1건과 종결 집합이 어긋나지 않는다"""
    assert set(ACTIVE_STATUSES) == set(ApprovalStatus) - TERMINAL
    from app.modules.approvals.models import _ACTIVE_PREDICATE

    assert _ACTIVE_PREDICATE == "status IN ('REQUESTED', 'APPROVED')"


def test_reason_required_targets_are_reject_and_withdraw_only() -> None:
    """사유 필수 도착 상태는 반려·회수뿐(무효는 사유 코드) — 이력 CHECK 문면과 같다"""
    assert {ApprovalStatus.REJECTED, ApprovalStatus.WITHDRAWN} == REASON_REQUIRED_TO
    from app.modules.approvals.models import ApprovalEvent

    texts = [
        str(c.sqltext)
        for c in ApprovalEvent.__table__.constraints
        if str(getattr(c, "name", "")).endswith("reason_required")
    ]
    assert len(texts) == 1 and "'REJECTED'" in texts[0] and "'WITHDRAWN'" in texts[0]
    assert "VOIDED" not in texts[0] and "CONSUMED" not in texts[0]


def test_human_verbs_map_to_human_transitions_only() -> None:
    """결정 동사 3종의 도착 상태가 사람 전이 집합의 도착 상태와 같다 — 동사로 시스템 전이(소비·무효)를 만들 수 없다"""
    assert {machine.VERB_TO[v] for v in DecisionVerb} == {b for _, b in HUMAN}
    assert not {machine.VERB_TO[v] for v in DecisionVerb} & {
        ApprovalStatus.CONSUMED,
        ApprovalStatus.VOIDED,
    }


# ── ⑤ 우회·자동 표식 파라미터 부재 ─────────────────────────────────────────────

FORBIDDEN_PARAMS = frozenset(
    {
        "force",
        "override",
        "admin",
        "admin_bypass",
        "bypass",
        "skip",
        "skip_checks",
        "system",
        "auto",
        "automatic",
        "sudo",
        "as_admin",
    }
)
PROTECTED = (
    "decide_approval",
    "request_approval",
    "consume_approval",
    "void_for_target",
    "peek_active_approved",
    "note_bypass_attempt",
)


@pytest.mark.parametrize("name", PROTECTED)
def test_protected_functions_have_no_bypass_or_automatic_parameters(name: str) -> None:
    """승인 통로 함수의 시그니처에 force·override·admin·system·auto 같은 우회·자동 표식이 없다 — ADMIN 분기가 구조적으로 불가능하다"""
    params = set(inspect.signature(getattr(service, name)).parameters)
    assert not params & FORBIDDEN_PARAMS, params & FORBIDDEN_PARAMS


def test_the_human_channel_requires_a_real_user_and_the_event_actor_is_not_nullable() -> None:
    """결정·요청은 실 사용자(`actor: AuthenticatedUser`)를 요구하고, 이력의 행위자 컬럼은 NOT NULL이다 — 시스템 행위자가 없다(자동 결정 경로 부재의 DB 표현)"""
    for name in ("decide_approval", "request_approval"):
        params = inspect.signature(getattr(service, name)).parameters
        assert "actor" in params and params["actor"].kind is inspect.Parameter.KEYWORD_ONLY
        assert "AuthenticatedUser" in str(params["actor"].annotation)
        assert "None" not in str(params["actor"].annotation)
    from app.modules.approvals.models import ApprovalEvent

    assert ApprovalEvent.__table__.c.actor_user_id.nullable is False
    for name in ("consume_approval", "void_for_target", "note_bypass_attempt"):
        param = inspect.signature(getattr(service, name)).parameters["actor_user_id"]
        assert "None" not in str(param.annotation)  # 시스템(None) 행위자 불가


def test_no_decide_function_accepts_an_approval_verb_other_than_the_three() -> None:
    """동사 열거는 APPROVE·REJECT·WITHDRAW 3종뿐(자동 승인 동사 없음)"""
    assert {v.value for v in DecisionVerb} == {"APPROVE", "REJECT", "WITHDRAW"}


# ── ⑥ 스키마 ─────────────────────────────────────────────────────────────────


def _pydantic_models(module: object) -> list[type]:
    from pydantic import BaseModel

    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == module.__name__
    ]


def test_every_request_schema_forbids_extra_fields_and_has_no_state_or_decider_fields() -> None:
    """`…Request` 스키마는 전부 extra=forbid이고 status·decided_*·approved 같은 필드가 없다(상태는 서버가 정한다) — 결정·대결 요청에는 결재자 지정 필드도 없다"""
    requests = [m for m in _pydantic_models(schemas) if m.__name__.endswith("Request")]
    assert len(requests) >= 5
    banned = re.compile(
        r"^(status|decided.*|approved.*|consumed.*|requested_by.*|basis_.*|snapshot.*|required_role)$"
    )
    approver_banned = re.compile(r"^approver.*$")  # 결재선 등록 요청의 approver_role과 구분한다
    for model in requests:
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert not [f for f in model.model_fields if banned.match(f)], (
            model.__name__,
            list(model.model_fields),
        )
        if model.__name__ in {
            "DecisionRequest",
            "DelegationCreateRequest",
            "DelegationRevokeRequest",
        }:
            assert not [f for f in model.model_fields if approver_banned.match(f)], model.__name__


def test_response_schemas_give_every_field_a_default_or_it_is_required_core() -> None:
    """응답 스키마의 서버 계산 필드(can_decide 등)는 기본값을 가진다 — 멱등 재생이 옛 응답을 새 스키마로 검증해도 500이 나지 않는다(R8)"""
    view = schemas.ApprovalView.model_fields
    for field in (
        "can_decide",
        "decide_blocked_reason",
        "can_withdraw",
        "status_reason",
        "void_reason_code",
    ):
        assert not view[field].is_required(), field
    assert not schemas.DelegationOut.model_fields["state"].is_required()


# ── ⑦ 라우트 표면 ─────────────────────────────────────────────────────────────


def _app_routes() -> list[tuple[str, set[str]]]:
    """(경로, 메서드 집합) — OpenAPI 스키마의 선언 순서 그대로(정적 경로 선행 검사용). 최신 FastAPI는 포함된 라우터를 지연 포장해 `app.routes`로는 안 보인다."""
    from app.main import app

    return [
        (path, {method.upper() for method in operations})
        for path, operations in app.openapi()["paths"].items()
    ]


def _routes() -> set[tuple[str, str]]:
    return {(method, path) for path, methods in _app_routes() for method in methods}


def test_there_is_no_http_route_that_creates_an_approval() -> None:
    """승인 요청 생성 라우트가 없다 — `/approvals` 아래 POST는 결정(`/{id}/decisions`) 하나뿐이고 `POST /approvals`는 존재하지 않는다"""
    routes = _routes()
    posts = {
        path for method, path in routes if method == "POST" and path.startswith("/api/v1/approvals")
    }
    assert posts == {"/api/v1/approvals/{approval_id}/decisions"}
    assert ("POST", "/api/v1/approvals") not in routes
    assert not [
        r
        for r in routes
        if r[0] in ("PUT", "PATCH", "DELETE") and r[1].startswith("/api/v1/approvals")
    ]


def test_static_paths_are_declared_before_the_id_path() -> None:
    """정적 경로(inbox-count·delegation-candidates)가 `/{id}`보다 먼저 선언돼 있다 — 정수 경로 파라미터와 충돌하지 않는다"""
    order = [path for path, _ in _app_routes() if path.startswith("/api/v1/approvals")]
    assert order.index("/api/v1/approvals/inbox-count") < order.index(
        "/api/v1/approvals/{approval_id}"
    )
    assert order.index("/api/v1/approvals/delegation-candidates") < order.index(
        "/api/v1/approvals/{approval_id}"
    )


def test_the_decision_route_requires_an_idempotency_key_and_a_role_gate() -> None:
    """결정 라우트는 Idempotency-Key 필수·비조회 역할 게이트를 쓰고, 자격 판정은 서비스 호출(`decide_approval`)에 맡긴다 — `require_roles`만으로 자격을 판정하지 않는다"""
    from app.modules.approvals import router as router_module

    text = inspect.getsource(router_module)
    assert "IdempotencyKey" in text and "require_roles(*CAN_ACT)" in text
    assert "service.decide_approval(" in text


# ── ④ 임포트 경계 — 잡·CLI·임포트·아웃박스·시드 ─────────────────────────────────


def _approval_submodules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            parts = name.split(".")
            if parts[:3] == ["app", "modules", "approvals"] and len(parts) >= 4:
                found.add(parts[3])
    return found


def test_scheduler_and_cli_reach_only_the_stagnation_scan() -> None:
    """스케줄러·CLI가 임포트하는 승인 서브모듈은 `stagnation`과 (S3-2 PR-1b) 읽기 전용 대사 `integrity`뿐 — 요청·결정·소비·무효 함수에 닿지 못한다(자동 승인 경로 부재)"""
    for rel in ("modules/platform/scheduler.py", "cli.py"):
        assert _approval_submodules(app_sources()[rel]) == {"stagnation", "integrity"}, rel
    # 대사 모듈 자신도 상태 기계(값)·모델(읽기)만 — 서비스·결정·소비·무효 모듈에 닿지 않는다(ADR-0087 ⑤).
    assert _approval_submodules(app_sources()["modules/approvals/integrity.py"]) == {
        "machine",
        "models",
    }


def test_background_modules_never_import_the_approval_service() -> None:
    """임포트·아웃박스 디스패처·시드·이관·알림 코어·플랫폼 어디에도 `approvals.service` 임포트가 없다 — 승인 서비스의 소비자는 라우터와 (PR-12) 전표 확정 모듈뿐이다"""
    forbidden = {
        "imports",
        "seeds",
        "handover",
        "outbox",
        "notifications",
        "platform",
        "deadlines",
        "collaboration",
        "certifications",
        "worklist",
    }
    offenders = [
        rel
        for rel, tree in app_sources().items()
        if module_of(rel) in forbidden
        and "approvals" in imported_modules(tree)
        and "service" in _approval_submodules(tree)
    ]
    assert offenders == []


def test_the_stagnation_scan_cannot_transitively_reach_the_service() -> None:
    """정체 스캔 모듈의 승인 패키지 내 임포트 폐포에 `service`가 없다 — 알림 코드가 상태 변경 함수를 부를 길이 없다"""
    sources = {
        rel.removeprefix(APPROVALS_PREFIX).removesuffix(".py"): tree
        for rel, tree in _approval_sources().items()
    }
    seen: set[str] = set()
    stack = ["stagnation"]
    while stack:
        current = stack.pop()
        if current in seen or current not in sources:
            continue
        seen.add(current)
        stack.extend(_approval_submodules(sources[current]))
    assert (
        "stagnation" in seen and len(seen) >= 3
    )  # 공회전 방지: alerts·authority 등을 실제로 따라간다
    assert "service" not in seen and "router" not in seen


def test_only_the_router_and_the_service_mention_the_decision_channel() -> None:
    """`decide_approval`을 언급하는 파일은 서비스(정의)와 라우터(호출) 둘뿐이다 — P7(Slack 어댑터)이 호출처를 더할 때 이 집합과 ADR을 함께 갱신한다"""
    from tests.support.astscan import referenced_names

    mentioning = {
        rel for rel, tree in app_sources().items() if "decide_approval" in referenced_names(tree)
    }
    assert mentioning == {
        "modules/approvals/router.py"
    }  # 정의(service.py)는 참조가 아니라 def라 잡히지 않는다


# ── 소비 접점 — PR-12로 미룬 항목의 장부 ──────────────────────────────────────

#: spec이 등록됐지만 소비자(`consume_approval(` 호출)가 아직 없는 유형 → 소비 PR. **PR-12a가 소비 호출을 더하면서 항목을 지웠다 — 스캔이 켜져 있다.**
#: 새 승인 유형이 spec만 등록하고 소비 PR을 미룰 때만 여기에 사유와 함께 적는다(조용히 넘기지 않는다).
PENDING_CONSUMER_SCAN: dict[str, str] = {}


def test_every_spec_either_has_a_consumer_call_or_is_listed_as_pending() -> None:
    """소비 접점 스캔 — 등록된 spec의 `consumer_module` 소스에 `consume_approval(` 호출이 있거나, 이 파일의 PENDING 목록에 사유와 함께 있다.
    목록에 있는데 소비 호출이 이미 생겼다면 항목을 지우게 한다(스캔이 조용히 꺼진 채 남지 않게)"""
    from app.modules.approvals.registry import registered_specs
    from tests.support.astscan import called_names

    specs = registered_specs()
    assert specs, "등록된 spec이 없다 — 공회전"
    for approval_type, spec in specs.items():
        consumer_has_call = any(
            module_of(rel) == spec.consumer_module and "consume_approval" in called_names(tree)
            for rel, tree in app_sources().items()
        )
        pending = approval_type in PENDING_CONSUMER_SCAN
        assert consumer_has_call != pending, (
            f"{approval_type}: 소비 호출 {consumer_has_call} / 미룸 목록 {pending} — 소비 호출을 더했다면 PENDING_CONSUMER_SCAN에서 지우세요"
        )


#: 승인 시스템 통로 4종의 **도메인 쪽 호출처 장부**(PR-12a — 소비 접점 스캔) — 새 호출처는 이 표와 `test_no_auto_confirm` 엔트리를 짝으로 갱신해야 한다.
SYSTEM_CHANNEL_CALLERS: dict[str, set[str]] = {
    "request_approval": {"modules/trade_chain/approval_requests.py"},
    "consume_approval": {"modules/trade_chain/confirm.py"},
    "void_for_target": {"modules/sales_orders/service.py"},
    "note_bypass_attempt": {"modules/trade_chain/confirm.py"},
}


def test_system_channels_are_called_only_from_their_registered_domain_callers() -> None:
    """`request_approval`(요청 모듈)·`consume_approval`+`note_bypass_attempt`(확정 통로)·`void_for_target`(SO 편집·취소 훅)의 호출자는 승인 패키지 밖에서 등록된 파일뿐이다 — 소비 접점이 코드로 고정된다"""
    from tests.support.astscan import called_names

    found: dict[str, set[str]] = {name: set() for name in SYSTEM_CHANNEL_CALLERS}
    for rel, tree in app_sources().items():
        if rel.startswith(APPROVALS_PREFIX):
            continue
        for name in called_names(tree) & set(SYSTEM_CHANNEL_CALLERS):
            found[name].add(rel)
    assert found == SYSTEM_CHANNEL_CALLERS


# ── 에러 코드·분류 ────────────────────────────────────────────────────────────

APPROVAL_ERROR_STATUS = {
    "APPROVALS.LINE.NOT_CONFIGURED": 422,
    "APPROVALS.LINE.DUPLICATE": 409,
    "APPROVALS.APPROVAL.NO_ELIGIBLE_APPROVER": 422,
    "APPROVALS.APPROVAL.ALREADY_ACTIVE": 409,
    "APPROVALS.APPROVAL.REQUIRED": 422,
    "APPROVALS.APPROVAL.STALE": 409,
    "APPROVALS.TRANSITION.NOT_ALLOWED": 409,
    "APPROVALS.TRANSITION.REASON_REQUIRED": 422,
    "APPROVALS.DECISION.NOT_APPROVER": 403,
    "APPROVALS.DECISION.SELF_APPROVAL": 403,
    "APPROVALS.DELEGATION.OVERLAP": 409,
    "APPROVALS.DELEGATION.NOT_ACTIVE": 409,
}


def test_the_twelve_approval_error_codes_exist_with_the_designed_statuses() -> None:
    """승인 에러 코드 12종이 설계 상태코드로 카탈로그에 있다(와이어 계약 — 프런트 분기 대상)"""
    codes = {
        str(c): ERROR_CATALOG[c].status_code for c in ErrorCode if str(c).startswith("APPROVALS.")
    }
    assert codes == APPROVAL_ERROR_STATUS


def test_approval_tables_have_no_assignment_columns_and_are_not_handover_targets() -> None:
    """승인 대기 건은 역할 기반이라 담당 이관 대상이 아니다 — ASSIGNMENT 이름 4종 컬럼이 승인 4표에 없고 이관 등록에도 없다"""
    from app.core.db.base import Base

    for table in ("approvals", "approval_events", "approval_lines", "delegations"):
        names = {c.name for c in Base.metadata.tables[table].columns}
        assert not names & ASSIGNMENT_COLUMN_NAMES, (table, names & ASSIGNMENT_COLUMN_NAMES)
    assert not {t.model.__tablename__ for t in ASSIGNMENT_TARGETS} & {
        "approvals",
        "approval_events",
        "approval_lines",
        "delegations",
    }


def test_every_approval_audit_action_is_used_by_the_code() -> None:
    """승인 audit 액션 상수 7종이 모두 실제 코드에서 쓰인다(죽은 상수 금지)"""
    from app.modules.audit.models import AuditAction
    from tests.support.astscan import referenced_names

    constants = [
        "APPROVAL_LINE_CREATED",
        "APPROVAL_LINE_UPDATED",
        "APPROVAL_LINE_DELETED",
        "DELEGATION_CREATED",
        "DELEGATION_REVOKED",
        "APPROVAL_DECISION_DENIED",
        "APPROVAL_BYPASS_BLOCKED",
    ]
    used = set().union(*(referenced_names(t) for rel, t in _approval_sources().items()))
    for name in constants:
        assert hasattr(AuditAction, name) and name in used, name


def test_credit_responses_and_snapshots_have_no_cost_fields() -> None:
    """여신 평가 값 객체·승인 응답 스키마의 필드명에 cost·margin·purchase·원가 계열이 구조적으로 없다(E2 — 마스킹 비대상 유지의 고정 장치)"""
    import dataclasses

    from app.modules.credit.evaluation import CreditEvaluation

    banned = re.compile(r"cost|margin|purchase|원가")
    fields = {f.name for f in dataclasses.fields(CreditEvaluation)}
    assert len(fields) >= 15 and not [f for f in fields if banned.search(f)]
    for model in _pydantic_models(schemas):
        assert not [f for f in model.model_fields if banned.search(f)], model.__name__


def test_the_credit_lock_helper_is_the_only_partner_no_key_update_locker() -> None:
    """거래처 행 `FOR NO KEY UPDATE`(여신 직렬화 잠금 — `with_for_update(key_share=True)`)를 거는 곳은 `credit/locking.py` 하나뿐이다.
    평가 함수는 LockedBuyer 토큰을 요구한다(잠그지 않은 평가는 형 수준에서 불가능). 기존 `FOR SHARE`(read=True) 유형 검증 잠금은 다른 모드라 대상이 아니다"""
    from app.modules.credit import evaluation

    def locks_no_key_update(node: ast.AST) -> bool:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            return False
        if node.func.attr != "with_for_update" or "Partner" not in ast.dump(node):
            return False
        keywords = {k.arg: k.value for k in node.keywords}
        key_share = (
            isinstance(keywords.get("key_share"), ast.Constant)
            and keywords["key_share"].value is True
        )
        read = isinstance(keywords.get("read"), ast.Constant) and keywords["read"].value is True
        return key_share and not read

    holders = sorted(
        {
            rel
            for rel, tree in app_sources().items()
            if any(locks_no_key_update(n) for n in ast.walk(tree))
        }
    )
    assert holders == ["modules/credit/locking.py"], holders
    sample = ast.parse("select(Partner).with_for_update(key_share=True)").body[0]
    assert locks_no_key_update(sample.value)  # type: ignore[attr-defined]  # 자기검사
    params = inspect.signature(evaluation.evaluate_credit).parameters
    assert "LockedBuyer" in str(params["locked"].annotation)


def test_the_open_order_predicate_has_a_single_definition() -> None:
    """미결 SO 술어(`open_orders_stmt`)·잔액식(`open_order_amount`)의 정의는 소스 전체에서 1곳(재구현 금지)"""
    for name in ("open_orders_stmt", "open_order_amount"):
        defs = [
            rel
            for rel, tree in app_sources().items()
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        ]
        assert defs == ["modules/credit/exposure.py"], (name, defs)


def test_the_after_evaluate_hook_is_a_noop_in_production_and_nobody_assigns_it() -> None:
    """평가 직후 테스트 이음새는 프로덕션 기본이 no-op이고 앱 코드 어디서도 대입하지 않는다"""
    from app.modules.credit import evaluation

    tree = ast.parse(inspect.getsource(evaluation._after_evaluate_hook).lstrip())
    body = [n for n in tree.body[0].body if not isinstance(n, ast.Expr)]  # type: ignore[attr-defined]
    assert body == [], "no-op이어야 한다(docstring 외 문장 없음)"
    for rel, source_tree in app_sources().items():
        assert not _attribute_assignments(source_tree, {"_after_evaluate_hook"}), rel


# ── 승인 위조 통로 차단 (적대 검토 반영: 모델 임포트·생성·대입·원시 UPDATE 전수) ──────────────────────────────

#: 승인 모델 임포트 예외 — 앱 루트 `registry.py`(Alembic 메타데이터용 임포트뿐, 생성 호출은 아래 별도 검사로 0건 강제). 늘리려면 사유와 함께 여기에만 추가한다.
APPROVAL_MODEL_IMPORT_ALLOWLIST: frozenset[str] = frozenset(
    {"registry.py"}
)  # 메타데이터 등록(모델 클래스 생성·사용 없음)
#: 승인 결정·소비 컬럼 — 승인 패키지 밖의 어떤 대입도 위조 시도다.
DECISION_COLUMNS = frozenset(
    {
        "decided_by_id",
        "decided_at",
        "decided_on_behalf_of_id",
        "decided_delegation_id",
        "consumed_at",
        "consumed_by_id",
    }
)
MODEL_NAMES = frozenset({"Approval", "ApprovalEvent"})
#: 이름이 겹치는 **비승인** 결정 열의 소유 파일 — 오더 인테이크(PR-13a)의 `decided_at`·`decided_by_id`는 확정·거부 결정 기록이며 승인과 무관하다.
#: 대입 통로가 `order_intake/machine.py::apply_intake_transition` 1곳임은 `test_order_intake_contract`가 고정한다(여기서 면제해도 구멍이 되지 않는다).
NON_APPROVAL_DECISION_OWNERS: frozenset[str] = frozenset({"modules/order_intake/machine.py"})


def _imports_approval_models(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("approvals.models"):
                return True
            if node.module.endswith("modules.approvals") and any(
                a.name == "models" for a in node.names
            ):
                return True
        elif isinstance(node, ast.Import) and any(
            a.name.endswith("approvals.models") for a in node.names
        ):
            return True
    return False


def _constructs_approval_models(tree: ast.Module) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name in MODEL_NAMES:
                lines.append(node.lineno)
    return lines


def _flatten_targets(target: ast.expr) -> list[ast.expr]:
    if isinstance(target, ast.Tuple | ast.List):
        return [leaf for element in target.elts for leaf in _flatten_targets(element)]
    if isinstance(target, ast.Starred):
        return _flatten_targets(target.value)
    return [target]


def _assigned_attributes(tree: ast.Module) -> list[tuple[str, int, str]]:
    """(속성, 줄, 대입 대상 변수명) — 튜플 언패킹·augassign·`setattr(x, '<이름>', …)`까지 포함한다."""
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = [leaf for t in node.targets for leaf in _flatten_targets(t)]
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute):
                owner = target.value.id if isinstance(target.value, ast.Name) else ""
                found.append((target.attr, node.lineno, owner))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = node.args[0].id if isinstance(node.args[0], ast.Name) else ""
            found.append((node.args[1].value, node.lineno, owner))
    return found


def _outside_approvals() -> dict[str, ast.Module]:
    return {
        rel: tree for rel, tree in app_sources().items() if not rel.startswith(APPROVALS_PREFIX)
    }


def test_nobody_outside_the_approvals_package_imports_or_constructs_approval_rows() -> None:
    """승인 모델(`approvals.models`)은 승인 패키지 밖에서 임포트도·`Approval(`/`ApprovalEvent(` 생성도 못 한다 — 승인 행은 서비스 통로로만 태어난다"""
    offenders = [
        rel
        for rel, tree in _outside_approvals().items()
        if rel not in APPROVAL_MODEL_IMPORT_ALLOWLIST
        and (_imports_approval_models(tree) or _constructs_approval_models(tree))
    ]
    assert offenders == []


def test_the_model_scan_catches_a_violation_corpus() -> None:
    """모델 스캔 자기검사 — 직접·모듈 경유·별칭 임포트와 생성 호출을 모두 잡고, 무관한 코드는 통과시킨다"""
    for source in (
        "from app.modules.approvals.models import Approval",
        "import app.modules.approvals.models as m",
        "from app.modules.approvals import models",
    ):
        assert _imports_approval_models(parse_source(source)), source
    assert _constructs_approval_models(parse_source("x = Approval(status='APPROVED')")) == [1]
    assert _constructs_approval_models(parse_source("x = models.ApprovalEvent(approval_id=1)")) == [
        1
    ]
    assert not _imports_approval_models(parse_source("from app.modules.approvals.schemas import X"))
    assert _constructs_approval_models(parse_source("x = SalesOrder(id=1)")) == []


def test_no_one_outside_the_approvals_package_assigns_decision_columns_or_an_approval_status() -> (
    None
):
    """승인 패키지 밖에서는 결정·소비 컬럼 대입이 0건이고, `approval….status` 대입·`setattr(approval, 'status', …)`·튜플 언패킹 대입도 0건이다"""
    offenders: list[str] = []
    for rel, tree in _outside_approvals().items():
        if rel in NON_APPROVAL_DECISION_OWNERS:
            continue
        for attr, line, owner in _assigned_attributes(tree):
            if attr in DECISION_COLUMNS or (attr == "status" and "approval" in owner.lower()):
                offenders.append(f"{rel}:{line}:{owner}.{attr}")
    assert offenders == []


def test_the_assignment_scan_catches_a_violation_corpus() -> None:
    """대입 스캔 자기검사 — 일반 대입·튜플 언패킹·augassign·setattr을 모두 잡는다"""
    corpus = parse_source(
        "approval.status = 'APPROVED'\n"
        "a.decided_by_id, a.decided_at = 1, 2\n"
        "[x.consumed_at, *rest] = [1, 2]\n"
        "setattr(approval, 'status', 'CONSUMED')\n"
        "row.consumed_by_id += 1\n"
    )
    found = {(attr, owner) for attr, _line, owner in _assigned_attributes(corpus)}
    assert {
        ("status", "approval"),
        ("decided_by_id", "a"),
        ("decided_at", "a"),
        ("consumed_at", "x"),
        ("consumed_by_id", "row"),
    } <= found


RAW_UPDATE_PATTERN = re.compile(
    r"(update\(\s*(\w+\.)*(Approval|ApprovalLine|Delegation)\b"
    r"|(Approval|ApprovalLine|Delegation)\.__table__\.update"
    r"|UPDATE\s+(ONLY\s+)?(public\s*\.\s*)?[\"']?(approvals|approval_lines|delegations)[\"']?(\s|$)"
    r"|query\(\s*(\w+\.)*(Approval|ApprovalLine|Delegation)\b[^)]*\)\s*\.\s*update"
    r"|\.update\(\s*\{\s*[\"']?status)",
    re.IGNORECASE,
)


def test_the_broadened_raw_update_scan_finds_nothing_in_the_app_and_catches_every_spelling() -> (
    None
):
    """원시 UPDATE 전수 — 모듈 한정 `update(m.Approval)`, `UPDATE public.approvals`, `UPDATE "approvals"`, `session.query(Approval).update(...)`까지 앱 전체에 0건"""
    from tests.support.astscan import APP_DIR

    offenders = [
        str(path.relative_to(APP_DIR))
        for path in sorted(APP_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts
        and RAW_UPDATE_PATTERN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    for sample in (
        "update(models.Approval).values(status='x')",
        "UPDATE public.approvals SET status = 'x'",
        'UPDATE "approvals" SET status = 1',
        "session.query(Approval).filter(a).update({'status': 'x'})",
        "session.query(m.Approval).update(values)",
        "Approval.__table__.update()",
        "UPDATE delegations SET revoked_at = now()",
    ):
        assert RAW_UPDATE_PATTERN.search(sample), sample
    assert not RAW_UPDATE_PATTERN.search("select(Approval).where(Approval.id == 1)")


def test_status_scan_in_the_package_also_sees_tuple_unpacking_and_setattr() -> None:
    """패키지 안에서도 `status`(및 결정·소비 컬럼) 대입은 `_record_transition` 단 1곳이다 — 튜플 언패킹·setattr 우회까지 포함"""
    hits = [
        (rel, attr, _enclosing_function(tree, line))
        for rel, tree in _approval_sources().items()
        for attr, line, _owner in _assigned_attributes(tree)
        if attr == "status" or attr in DECISION_COLUMNS
    ]
    assert hits, "스캔이 공회전하고 있다"
    assert {(rel, fn) for rel, _attr, fn in hits} <= {
        ("modules/approvals/service.py", "_record_transition")
    }, hits
