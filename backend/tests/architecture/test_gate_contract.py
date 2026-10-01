"""K. 게이트 계약 스캔 — 도메인 무임포트·통과 판정 단일 정의·평가기 읽기 전용·원가 참조 0·정수 연산·override 통로 (S3-1 PR-11a / design-D D3·D4 / ADR-0069).

소스 구조로 고정하는 규칙(런타임 테스트가 못 잡는 "다른 곳에 같은 판정을 복제하는" 회귀):
  ① `gates`는 어떤 도메인 모듈도 임포트하지 않는다(평가기는 소비 모듈이 등록한다) ② **통과 판정은 `gates.service.clearance` 하나** — 결과값(`GateLevel`)·`'PASS'` 비교로 통과를 가르는 코드가 그 밖에 없다
  ③ 평가기는 읽기만 한다(쓰기 호출 0)·원가·매입가·마진을 참조하지 않는다 ④ 금액·편차 계산은 정수만(Float·참 나눗셈 0) ⑤ PI 게이트는 PI 상태가 아니라 금액으로 판정한다
  ⑥ override 부여·철회는 사람 통로(라우터+멱등 키+무역 게이트)뿐이고 우회·자동 표식 인자가 없다 ⑦ 증거 스냅샷 기록 함수는 확정 통로(PR-12) 전에는 호출처가 없다(조회 부작용 금지).
각 스캔에는 위반 코퍼스를 넣어 잡는지 보는 자기검사가 있다(공회전 방지).
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest

from tests.support.astscan import (
    app_sources,
    called_names,
    imported_modules,
    module_of,
    parse_source,
)

pytestmark = pytest.mark.group_k

#: gates가 임포트해도 되는 모듈 — 도메인이 아닌 플랫폼(감사·아웃박스·신원)과 자기 자신뿐.
GATES_ALLOWED_IMPORTS = {"gates", "identity", "audit", "outbox"}

LEVEL_NAMES = {"PASS", "WARN", "BLOCK", "UNKNOWN"}

#: 통과 판정(결과값 비교)을 해도 되는 유일한 파일 — 정의(types)·명세 팩토리(policy)는 비교가 아니라 선언이다.
JUDGEMENT_FILES = {"modules/gates/service.py"}
#: 결과값 비교 스캔 대상 — 게이트·평가기·오케스트레이터·PI 판정 모듈 전체(다른 모듈의 'PASS' 문자열은 무관).
SCANNED_MODULES = {"gates", "payments"}
SCANNED_FILES = {
    "modules/trade_chain/gate_evaluators.py",
    "modules/trade_chain/gate_flow.py",
}


def _gate_sources() -> dict[str, ast.Module]:
    return {rel: tree for rel, tree in app_sources().items() if module_of(rel) == "gates"}


def _level_comparisons(tree: ast.Module) -> list[int]:
    """결과값으로 통과를 가르는 비교의 줄 번호 — `GateLevel.X`·`'PASS'`·`.level` 이 비교 피연산자에 있는 경우."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands: list[ast.expr] = [node.left, *node.comparators]
        for operand in ast.walk(ast.Tuple(elts=operands, ctx=ast.Load())):
            if (
                isinstance(operand, ast.Attribute)
                and (
                    (
                        isinstance(operand.value, ast.Name)
                        and operand.value.id == "GateLevel"
                        and operand.attr in LEVEL_NAMES
                    )
                    or operand.attr == "level"
                )
            ) or (isinstance(operand, ast.Constant) and operand.value in LEVEL_NAMES):
                lines.append(node.lineno)
                break
    return lines


# ── ① 도메인 무임포트 ────────────────────────────────────────────────────────────


def test_gates_imports_no_domain_module() -> None:
    """`gates` 소스는 도메인 모듈(sales_orders·trade_chain·credit·payments·catalog·partners·readiness·…)을 하나도 임포트하지 않는다 — 허용은 신원·감사·아웃박스와 자기 자신뿐"""
    sources = _gate_sources()
    assert len(sources) >= 7  # 공회전 방지 — models·types·policy·registry·service·schemas·__init__
    for rel, tree in sources.items():
        used = imported_modules(tree)
        assert used <= GATES_ALLOWED_IMPORTS, (rel, sorted(used - GATES_ALLOWED_IMPORTS))


def test_the_domain_import_scan_catches_a_violation() -> None:
    """자기검사 — 도메인 임포트를 넣으면 허용 집합 밖으로 잡힌다"""
    tree = parse_source("from app.modules.sales_orders.models import SalesOrder\n")
    assert imported_modules(tree) - GATES_ALLOWED_IMPORTS == {"sales_orders"}


# ── ② 통과 판정 단일 정의 ────────────────────────────────────────────────────────


def test_no_code_outside_clearance_judges_a_pass_from_the_result_value() -> None:
    """게이트·평가기·오케스트레이터·PI 판정 모듈 어디에도 결과값(`GateLevel.X`·`'PASS'`·`.level`) 비교가 없다 — 통과 판정은 `gates.service`의 `settle`·`clearance` 하나(복제 금지)"""
    scanned = {
        rel: tree
        for rel, tree in app_sources().items()
        if (module_of(rel) in SCANNED_MODULES or rel in SCANNED_FILES)
        and rel not in JUDGEMENT_FILES
    }
    assert len(scanned) >= 8 and set(scanned) >= SCANNED_FILES  # 공회전 방지
    offenders = {
        rel: _level_comparisons(tree) for rel, tree in scanned.items() if _level_comparisons(tree)
    }
    assert offenders == {}, f"결과값 비교로 통과를 판정하는 코드: {offenders}"
    # 정의 파일(service)에는 있다 — 스캐너가 실제 비교를 본다는 증거
    assert _level_comparisons(app_sources()["modules/gates/service.py"])


@pytest.mark.parametrize(
    "source",
    [
        "if outcome.level == GateLevel.PASS:\n    ok = True\n",
        "ok = result.level in (GateLevel.PASS, GateLevel.WARN)\n",
        "ok = status == 'PASS'\n",
        "ok = GateLevel.WARN is x\n",
    ],
    ids=["eq-attr", "in-tuple", "string", "is"],
)
def test_the_judgement_scan_catches_every_comparison_shape(source: str) -> None:
    """자기검사 — 결과값 비교 4형태(==·in·문자열·is)를 전부 잡고, 비교가 아닌 대입·구성은 잡지 않는다"""
    assert _level_comparisons(parse_source(source)) == [1]
    assert _level_comparisons(parse_source("x = outcome(code, GateLevel.PASS, R.NONE)\n")) == []
    assert _level_comparisons(parse_source("if phase is GatePhase.INTAKE:\n    pass\n")) == []


# ── ③ 평가기 읽기 전용·원가 참조 0 ───────────────────────────────────────────────

EVALUATOR_FILES = (
    "modules/trade_chain/gate_evaluators.py",
    "modules/payments/pi_gate.py",
    "modules/gates/policy.py",
)
WRITE_CALLS = {
    "add",
    "add_all",
    "flush",
    "commit",
    "rollback",
    "delete",
    "merge",
    "bulk_save_objects",
    "insert",
    "update",
    "begin_nested",
}
FORBIDDEN_WORDS = re.compile(
    r"purchase|margin|_cost$|^cost$|unit_cost|purchase_price", re.IGNORECASE
)


def _identifiers(tree: ast.Module) -> set[str]:
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ids.add(node.id)
        elif isinstance(node, ast.Attribute):
            ids.add(node.attr)
        elif isinstance(node, ast.alias):
            ids.add(node.name.split(".")[-1])
        elif isinstance(node, ast.keyword) and node.arg:
            ids.add(node.arg)
    return ids


def test_gate_evaluators_never_write_and_never_reference_cost_data() -> None:
    """평가기 모듈(SO 어댑터·PI 판정·명세 팩토리)에 쓰기 호출(add·flush·commit·insert·update·delete…)과 원가·매입가·마진 참조가 없다 — 평가는 계산이고 원가 마스킹 계약을 지킨다"""
    sources = app_sources()
    for rel in EVALUATOR_FILES:
        tree = sources[rel]
        assert not called_names(tree) & WRITE_CALLS, (rel, called_names(tree) & WRITE_CALLS)
        assert not {i for i in _identifiers(tree) if FORBIDDEN_WORDS.search(i)}, rel
    # 게이트 응답 스키마·결과 타입에도 원가 계열 필드가 없다
    for rel in ("modules/gates/schemas.py", "modules/gates/types.py"):
        assert not {i for i in _identifiers(sources[rel]) if FORBIDDEN_WORDS.search(i)}, rel


def test_the_cost_scan_catches_a_purchase_reference() -> None:
    """자기검사 — 매입가·PURCHASE 참조와 쓰기 호출을 넣으면 잡힌다"""
    bad = parse_source("price = sku.purchase_price\nsession.add(x)\nm = PriceType.PURCHASE\n")
    assert {i for i in _identifiers(bad) if FORBIDDEN_WORDS.search(i)} == {
        "purchase_price",
        "PURCHASE",
    }
    assert called_names(bad) & WRITE_CALLS == {"add"}


# ── ④ 정수 연산 ─────────────────────────────────────────────────────────────────


def test_gate_money_code_uses_integers_only() -> None:
    """게이트·평가기·PI 판정 코드에 float 사용과 참 나눗셈(`/`)이 없다 — 금액·편차는 정수(교차곱셈·`//`)로만 계산한다"""
    for rel in (*EVALUATOR_FILES, "modules/gates/service.py", "modules/trade_chain/gate_flow.py"):
        tree = app_sources()[rel]
        assert "float" not in _identifiers(tree), rel
        assert not [
            n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
        ], rel
    assert [
        n
        for n in ast.walk(parse_source("x = a / b\n"))
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
    ]


# ── ⑤ PI 게이트는 금액으로 판정한다 ──────────────────────────────────────────────


def test_the_pi_gate_reads_the_pi_status_only_to_check_usability() -> None:
    """`evaluate_pi_advance`는 PI 상태를 **입금 가능 여부 멤버십(`not in OPEN_PI_STATES`) 한 곳**에서만 읽고, 충족 판정에 PAID·PARTIALLY_PAID 같은 상태값을 쓰지 않는다(상태 조작으로 게이트를 우회하지 못한다)"""
    tree = app_sources()["modules/payments/pi_gate.py"]
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    status_reads = [
        n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "status"
    ]
    assert len(status_reads) == 1
    compare = parents[status_reads[0]]
    assert isinstance(compare, ast.Compare) and isinstance(compare.ops[0], ast.NotIn)
    assert (
        isinstance(compare.comparators[0], ast.Name)
        and compare.comparators[0].id == "OPEN_PI_STATES"
    )
    strings = {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert not strings & {"PAID", "PARTIALLY_PAID", "ISSUED"}


# ── ⑥ override 통로 ──────────────────────────────────────────────────────────────

FORBIDDEN_PARAMS = frozenset(
    {
        "force",
        "override",
        "admin",
        "admin_bypass",
        "bypass",
        "skip",
        "system",
        "auto",
        "automatic",
        "sudo",
        "as_admin",
    }
)


def test_override_functions_require_a_real_actor_and_have_no_bypass_parameters() -> None:
    """override 서비스·오케스트레이터 시그니처에 force·bypass·admin·system·auto 표식이 없고 행위자(`actor_user_id`: int·`actor_roles`·`actor: AuthenticatedUser`)가 필수다 — 시스템 행위자(None)로 부여할 수 없다"""
    from app.modules.gates import service
    from app.modules.trade_chain import gate_flow

    for function in (service.grant_override, service.revoke_override):
        params = inspect.signature(function).parameters
        assert not set(params) & FORBIDDEN_PARAMS
        assert "None" not in str(params["actor_user_id"].annotation) and "actor_roles" in params
        assert all(
            p.kind is inspect.Parameter.KEYWORD_ONLY
            for name, p in params.items()
            if name != "session"
        )
    for function in (gate_flow.grant_gate_override, gate_flow.revoke_gate_override):
        params = inspect.signature(function).parameters
        assert not set(params) & FORBIDDEN_PARAMS
        assert params["actor"].kind is inspect.Parameter.KEYWORD_ONLY
        assert "AuthenticatedUser" in str(params["actor"].annotation) and "None" not in str(
            params["actor"].annotation
        )
        assert params["idempotency_key"].default is inspect.Parameter.empty  # 기본값 없는 멱등 키


def test_the_override_routes_are_trade_gated_idempotent_and_the_report_route_is_open() -> None:
    """부여·철회 라우트는 무역 게이트(`require_roles(*CAN_WRITE)`)와 멱등 키를 요구하고 201이며, 조회 라우트는 게이트·키가 없다 — 역할별 판정은 서비스(이중 방어)"""
    tree = app_sources()["modules/trade_chain/router.py"]
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("grant_sales_order_gate_override", "revoke_sales_order_gate_override"):
        node = functions[name]
        decorator = ast.unparse(node.decorator_list[0])
        assert "require_roles(*CAN_WRITE)" in decorator and "HTTP_201_CREATED" in decorator, name
        assert any(
            a.arg == "key" and "IdempotencyKey" in ast.unparse(a.annotation) for a in node.args.args
        ), name
    report = functions["get_sales_order_gates"]
    assert "require_roles" not in ast.unparse(report.decorator_list[0])
    assert not any(
        "IdempotencyKey" in ast.unparse(a.annotation) for a in report.args.args if a.annotation
    )


def test_override_callers_are_only_the_router_and_the_orchestrator() -> None:
    """override 부여·철회 오케스트레이터의 호출처는 라우터 1곳이고 서비스 함수의 호출처는 오케스트레이터 1곳이다 — 스케줄러·CLI·임포트·인테이크·보드·벌크 경로가 부여하지 않는다(no_auto_confirm 엔트리와 이중 고정)"""
    callers: dict[str, set[str]] = {}
    for rel, tree in app_sources().items():
        for name in (
            "grant_gate_override",
            "revoke_gate_override",
            "grant_override",
            "revoke_override",
        ):
            if name in called_names(tree):
                callers.setdefault(name, set()).add(rel)
    assert callers == {
        "grant_gate_override": {"modules/trade_chain/router.py"},
        "revoke_gate_override": {"modules/trade_chain/router.py"},
        "grant_override": {"modules/trade_chain/gate_flow.py"},
        "revoke_override": {"modules/trade_chain/gate_flow.py"},
    }


def test_the_override_request_schema_is_strict() -> None:
    """override 요청 스키마는 extra=forbid이고 상태·행위자·승인 같은 서버 결정 필드가 없다 — 모르는 필드로 권한·역할을 끼워 넣을 수 없다"""
    from app.modules.gates.schemas import GateOverrideRequest

    assert GateOverrideRequest.model_config.get("extra") == "forbid"
    assert set(GateOverrideRequest.model_fields) == {"gate_code", "line_id", "basis_hash", "reason"}


# ── ⑦ 증거 스냅샷 기록은 확정 통로 전 호출처가 없다 ───────────────────────────────

#: `record_evaluation` 호출처 장부 — **PR-12가 확정 통로(`trade_chain/confirm.py`)를 더하며 이 집합과 짝으로 갱신한다**. 조회·override 경로는 증거 스냅샷을 쓰지 않는다(조회 부작용 금지).
RECORD_EVALUATION_CALLERS: set[str] = set()


def test_record_evaluation_has_no_caller_before_the_confirm_wiring() -> None:
    """PR-11a 시점에 `gates.service.record_evaluation`을 부르는 코드가 없다(확정 시도만 기록한다 — PR-12). 조회·override 오케스트레이터가 증거를 쓰는 회귀를 막는다"""
    callers = {
        rel for rel, tree in app_sources().items() if "record_evaluation" in called_names(tree)
    }
    assert callers == RECORD_EVALUATION_CALLERS
    flow_names = _identifiers(app_sources()["modules/trade_chain/gate_flow.py"])
    assert not flow_names & {"record_evaluation", "GateEvaluation", "evaluation_results"}


# ── MARKET_READINESS 워딩 ────────────────────────────────────────────────────────


def test_market_gate_messages_state_it_is_not_a_legal_judgement_and_use_no_approval_wording() -> (
    None
):
    """준비도 게이트 평가기의 모든 사용자 문구에 '준비 상태 안내(법적 판정 아님)' 고정 문구가 쓰이고 '판매 가능·적합·승인·허가' 워딩이 없다(§15 4금 ② 법적 판정 금지)"""
    tree = app_sources()["modules/trade_chain/gate_evaluators.py"]
    function = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "evaluate_market_readiness"
    )
    body = function.body[1:]  # 독스트링 제외
    strings = [
        n.value
        for stmt in body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    joined = " ".join(strings)
    for banned in ("판매 가능", "판매가능", "적합", "승인", "허가"):
        assert banned not in joined, banned
    constants = {t.id for t in ast.walk(function) if isinstance(t, ast.Name)}
    assert "NOTE_READINESS" in constants
    note = next(
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "NOTE_READINESS" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    )
    assert note == "준비 상태 안내(법적 판정 아님)"
