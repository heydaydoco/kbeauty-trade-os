"""I. 자동화 경로 부재 — **PO(발주) 자동 발행·자동 전이 경로가 코드에 없다** (S3-1 PR-8a / 자동화 4금 ① §15 L3 · ADR-0057 ② / design-F F2 (d)(f) · design-B B9 #10).

§15 L3 4금 ① "지출·발주 확정 자동화 금지": PO 생성은 곧 발주 확정이고 **사람이 세션 로그인 상태에서 1클릭**으로 `POST /purchase-orders`를 부르는 경로가 유일하다.
이 파일은 그 사실을 문서가 아니라 코드로 지킨다(엔트리 등록 스캔은 test_no_auto_confirm_code_path_exists가 하고, 여기는 PO 전용 **전이적 도달성**과 표면 구조를 본다):

  ① 호출 지점: `create_purchase_order` 호출은 PO 라우터 1곳  ② **전이적 도달성**: 스케줄러·CLI·디스패처(outbox 핸들러)·임포트·시드·이관이 PO 서비스·라우터에 import 경로로도
  닿지 못한다  ③ HTTP 표면: 생성 POST는 무역 역할 게이트+멱등 키, 요청 스키마에 서버 소유 필드·`automatic`·`approval_id`가 없다  ④ 상태 기계: PO 자동 엣지 0, `automatic=True`
  전이를 만드는 함수는 PO를 언급하지 못한다  ⑤ 후속 PR이 만드는 모듈(인테이크·보드·승인·게이트)은 PO를 언급하지 못한다(만들어지는 순간 적용 — 지금은 양성 표본으로 스캐너를 시험).
모든 스캐너는 위반 코퍼스를 실제로 잡는지 자기검사한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import AUTO_TRANSITIONS, public_transition_targets
from tests.support.astscan import APP_DIR, app_sources, module_of, parse_source, referenced_names

pytestmark = pytest.mark.group_i

PO_SERVICE = "modules/purchase_orders/service.py"
PO_ROUTER = "modules/purchase_orders/router.py"
#: 발주 확정 통로 — 이 파일들에 닿으면 자동 경로다.
PO_ENTRY_FILES = frozenset({PO_SERVICE, PO_ROUTER})
#: 자동 실행 주체의 루트 파일/모듈 — 여기서 출발해 PO 통로에 도달하면 안 된다.
AUTOMATIC_ROOT_PREFIXES = (
    "modules/platform/",  # 스케줄러·잡 레지스트리
    "modules/notifications/dispatcher.py",  # outbox 핸들러(디스패처)
    "modules/imports/",  # 임포트 확정
    "modules/seeds/",  # 시드
    "modules/handover/",  # 담당 이관
    "modules/worklist/",
    "modules/deadlines/",
    "cli.py",  # CLI 서브커맨드
)
PO_NAMES = frozenset(
    {
        "PurchaseOrder",
        "PurchaseOrderLine",
        "PURCHASE_ORDER",
        "purchase_orders",
        "create_purchase_order",
        "transition_purchase_order",
    }
)


# ── 전이적 import 도달성 ────────────────────────────────────────────────────────


def _dotted(rel: str) -> str:
    parts = rel[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["app", *parts])


def _edges(sources: dict[str, ast.Module]) -> dict[str, set[str]]:
    """앱 파일(rel) → 그 파일이 import하는 앱 파일들(rel). 함수 안 지연 import까지 포함한다."""
    by_dotted = {_dotted(rel): rel for rel in sources}
    graph: dict[str, set[str]] = {rel: set() for rel in sources}
    for rel, tree in sources.items():
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                candidates.append(node.module)
                candidates.extend(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                candidates.extend(
                    alias.name for alias in node.names if alias.name.startswith("app")
                )
            graph[rel].update(by_dotted[c] for c in candidates if c in by_dotted)
    return graph


def _reachable(graph: dict[str, set[str]], roots: set[str]) -> set[str]:
    seen, stack = set(), list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return seen


def _automatic_roots(sources: dict[str, ast.Module]) -> set[str]:
    return {rel for rel in sources if rel.startswith(AUTOMATIC_ROOT_PREFIXES)}


def test_the_reachability_scan_is_not_vacuous() -> None:
    """스캔 대상이 실제로 있다 — 자동 실행 루트가 여럿이고, 스케줄러에서 전표 서비스(만료 스윕 경유 견적·PI)까지는 **실제로 닿는다**(닿는 경로를 본다는 증거)"""
    sources = app_sources()
    roots = _automatic_roots(sources)
    assert {
        "modules/platform/scheduler.py",
        "cli.py",
        "modules/notifications/dispatcher.py",
    } <= roots
    reached = _reachable(_edges(sources), roots)
    assert (
        "modules/trade_chain/expiry_sweep.py" in reached
        and "modules/quotations/models.py" in reached
    )
    assert (
        "modules/trade_docs/transition.py" in reached
    )  # 전이 통로는 닿는다(만료 스윕이 쓴다) — PO 통로만 안 닿아야 한다


def test_no_automatic_actor_can_reach_the_po_entry_points_even_transitively() -> None:
    """스케줄러·CLI·디스패처(outbox 핸들러)·임포트·시드·이관·워크리스트·기일 스캔에서 출발한 **전이적 import 도달 집합**에 PO 서비스·라우터가 없다 — 중간 모듈을 거쳐 우회해도 잡는다"""
    sources = app_sources()
    reached = _reachable(_edges(sources), _automatic_roots(sources))
    assert reached & PO_ENTRY_FILES == set(), sorted(reached & PO_ENTRY_FILES)
    # PO 전이 통로(lifecycle)는 자동 주체가 닿는 집합 밖에 있다 — 스윕은 lifecycle을 쓰지 않는다
    assert "modules/trade_chain/lifecycle.py" not in reached


def test_the_reachability_scan_catches_a_synthetic_bypass() -> None:
    """자기검사 — 스케줄러가 중간 모듈을 거쳐 PO 서비스에 닿는 가짜 그래프를 넣으면 잡는다(직접 import·2단 간접·함수 안 지연 import 모두)"""
    direct = {
        "modules/platform/scheduler.py": parse_source(
            "from app.modules.purchase_orders import service\n"
        ),
        PO_SERVICE: parse_source(""),
        "modules/purchase_orders/__init__.py": parse_source(""),
    }
    assert PO_SERVICE in _reachable(_edges(direct), {"modules/platform/scheduler.py"})
    indirect = {
        "cli.py": parse_source("import app.modules.helpers.run\n"),
        "modules/helpers/run.py": parse_source(
            "def go():\n    from app.modules.purchase_orders.service import create_purchase_order\n"
        ),
        PO_SERVICE: parse_source(""),
    }
    assert PO_SERVICE in _reachable(_edges(indirect), {"cli.py"})
    clean = {
        "cli.py": parse_source("from app.modules.helpers import run\n"),
        "modules/helpers/run.py": parse_source(""),
        "modules/helpers/__init__.py": parse_source(""),
        PO_SERVICE: parse_source(""),
    }
    assert PO_SERVICE not in _reachable(_edges(clean), {"cli.py"})


# ── 호출 지점 ──────────────────────────────────────────────────────────────────


def _call_nodes(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) == name or getattr(n.func, "attr", None) == name)
    ]


def test_create_purchase_order_is_called_from_the_po_router_only() -> None:
    """`create_purchase_order`의 호출 노드는 앱 전체에서 PO 라우터 1곳(1회)뿐이다 — 정의 파일(service)에는 호출이 없다"""
    callers = {
        rel: len(_call_nodes(tree, "create_purchase_order")) for rel, tree in app_sources().items()
    }
    assert {rel: n for rel, n in callers.items() if n} == {PO_ROUTER: 1}


def test_the_po_transition_function_is_called_from_the_chain_router_only() -> None:
    """`transition_purchase_order`(OC·취소)의 호출은 trade_chain 라우터 1곳뿐이다"""
    callers = {
        rel: len(_call_nodes(tree, "transition_purchase_order"))
        for rel, tree in app_sources().items()
    }
    assert {rel: n for rel, n in callers.items() if n} == {"modules/trade_chain/router.py": 1}


def test_files_outside_the_po_and_chain_modules_never_mention_po_entry_names() -> None:
    """PO 통로 이름(create_purchase_order·transition_purchase_order)은 purchase_orders·trade_chain 모듈 밖 어떤 파일에서도 언급되지 않는다(문자열·주석 제외 — 식별자 기준)"""
    names = {"create_purchase_order", "transition_purchase_order", "preview_purchase_order"}
    for rel, tree in app_sources().items():
        if module_of(rel) in {"purchase_orders", "trade_chain"}:
            continue
        assert not names & referenced_names(tree), rel


# ── HTTP 표면 ──────────────────────────────────────────────────────────────────


def _post_decorators(
    tree: ast.Module, routers: set[str]
) -> list[tuple[str, ast.FunctionDef, ast.Call]]:
    out: list[tuple[str, ast.FunctionDef, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "post"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id in routers
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                out.append((str(decorator.args[0].value), node, decorator))
    return out


def test_every_po_write_route_needs_a_trade_role_and_the_creation_needs_an_idempotency_key() -> (
    None
):
    """PO의 쓰기 POST(생성·미리보기·전이)는 전부 `require_roles(*CAN_WRITE)`(무역 — 관리자 상시 통과)를 달고, 발주 확정(생성)·전이는 `key: IdempotencyKey`를 받는다 — 미리보기만 저장이 없어 키가 없다"""
    routes = _post_decorators(app_sources()[PO_ROUTER], {"router"}) + _post_decorators(
        app_sources()["modules/trade_chain/router.py"], {"po_router"}
    )
    assert {path for path, _f, _d in routes} == {"", "/preview", "/{po_id}/transitions"}
    for path, function, decorator in routes:
        text = ast.unparse(decorator)
        assert "require_roles(*CAN_WRITE)" in text, path
        annotations = {ast.unparse(arg.annotation) for arg in function.args.args if arg.annotation}
        has_key = "IdempotencyKey" in annotations
        assert has_key == (path != "/preview"), path
    assert not any(p.startswith("/api") for p, _f, _d in routes)


def test_po_request_schemas_forbid_extra_fields_and_carry_no_server_owned_or_automation_names() -> (
    None
):
    """PO 요청 스키마는 전부 extra=forbid이고 status·doc_number·frozen_at·total_cost·line_cost·id·automatic·approval_id 같은 서버 소유·자동화 필드가 구조적으로 없다(생성 본문에는 version도 없다 — 생성이 곧 발행이라 낙관 잠금 대상이 없다)"""
    from app.modules.purchase_orders import schemas as po_schemas
    from app.modules.trade_chain import router as chain_router

    forbidden = {
        "status", "doc_number", "frozen_at", "total_cost", "line_cost", "id", "last_line_no",
        "automatic", "approval_id", "actor", "actor_user_id", "po_id", "material_id", "item_type",
    }  # fmt: skip
    checked = 0
    for module in (po_schemas, chain_router):
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and name.endswith("Request")
                and "PurchaseOrder" in name
                and obj.__module__ == module.__name__
            ):
                assert obj.model_config.get("extra") == "forbid", name
                assert not set(obj.model_fields) & forbidden, (
                    name,
                    set(obj.model_fields) & forbidden,
                )
                checked += 1
    assert checked == 3  # 생성·메타·전이
    assert "version" not in po_schemas.PurchaseOrderCreateRequest.model_fields
    line_fields = set(po_schemas.PoLineIn.model_fields)
    assert po_schemas.PoLineIn.model_config.get("extra") == "forbid" and not line_fields & forbidden


# ── 상태 기계 ──────────────────────────────────────────────────────────────────


def test_po_has_no_automatic_edge_and_the_public_targets_exclude_reserved_states() -> None:
    """PO 자동 엣지는 0 — 전이는 사람 3방향뿐이고 공개 `to`는 공급사 확인·취소다(예약 후반 3값은 엣지도 공개 대상도 아니다)"""
    assert AUTO_TRANSITIONS[DocKind.PURCHASE_ORDER] == frozenset()
    assert public_transition_targets(DocKind.PURCHASE_ORDER) == {"SUPPLIER_CONFIRMED", "CANCELLED"}


def _automatic_transition_functions(
    sources: dict[str, ast.Module],
) -> dict[tuple[str, str], ast.FunctionDef]:
    """`record_transition(..., automatic=True)`를 부르는 함수 — (파일, 함수) → 정의."""
    found: dict[tuple[str, str], ast.FunctionDef] = {}
    for rel, tree in sources.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in _call_nodes(ast.Module(body=[node], type_ignores=[]), "record_transition"):
                if any(
                    kw.arg == "automatic"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in call.keywords
                ):
                    found[(rel, node.name)] = node
    return found


def test_functions_that_create_automatic_transitions_never_touch_po() -> None:
    """`automatic=True` 전이를 만드는 함수(QT 수렴·PI 입금 수렴·만료 스윕·SO 선적 수렴)는 PO 이름을 언급하지 않는다 — PO는 자동 전이의 대상이 될 수 없다. 대상 함수 집합도 고정한다(새 자동 전이 함수는 이 목록을 갱신하며 PO 무관을 증명해야 한다)"""
    functions = _automatic_transition_functions(app_sources())
    assert set(functions) == {
        ("modules/trade_chain/chain_ops.py", "converge_quotation"),
        ("modules/trade_chain/payment_status.py", "converge_payment_status"),
        ("modules/trade_chain/expiry_sweep.py", "_expire_one"),
        # S3-2 PR-3a — SO 선적 수렴(CONFIRMED↔IN_SHIPMENT). 수입선적(PO 원천)은 converge_parent가 None으로 돌려보낸다(PO 상태 무변경)
        ("modules/trade_chain/chain_ops.py", "converge_sales_order_shipping"),
    }
    for key, function in functions.items():
        names = referenced_names(ast.Module(body=[function], type_ignores=[]))
        assert not names & PO_NAMES, (key, names & PO_NAMES)


def test_the_automatic_transition_scan_catches_a_synthetic_po_touch() -> None:
    """자기검사 — 자동 전이 함수가 PO를 언급하면 잡는다"""
    bad = parse_source(
        "def sweep(session):\n    doc = session.get(PurchaseOrder, 1)\n"
        "    record_transition(session, doc, 'CANCELLED', actor_user_id=None, reason='x', automatic=True)\n"
    )
    found = _automatic_transition_functions({"modules/x.py": bad})
    assert list(found) == [("modules/x.py", "sweep")]
    assert (
        referenced_names(ast.Module(body=[found[("modules/x.py", "sweep")]], type_ignores=[]))
        & PO_NAMES
    )


# ── 후속 모듈(인테이크·보드·승인·게이트) ────────────────────────────────────────────


FUTURE_MODULES = ("order_intake", "order_board", "approvals", "gates", "credit", "payments")


def _po_mentions(sources: dict[str, ast.Module]) -> dict[str, set[str]]:
    return {
        rel: referenced_names(tree) & PO_NAMES
        for rel, tree in sources.items()
        if module_of(rel) in FUTURE_MODULES and referenced_names(tree) & PO_NAMES
    }


def test_future_order_modules_do_not_mention_po() -> None:
    """인테이크·오더 보드(벌크 액션 열거)·승인·게이트·여신·입금 모듈이 PO를 언급하지 않는다 — PO는 보드 벌크 열거에 없고 승인 대상 열거에도 없다(§2 승인 대상에 PO 발행 없음). 이 모듈들이 생기는 순간 이 스캔이 적용된다"""
    assert _po_mentions(app_sources()) == {}


def test_the_future_module_scan_catches_a_synthetic_mention() -> None:
    """자기검사 — 보드 모듈이 PO 생성을 벌크 액션으로 끼우면 잡는다"""
    bad = {
        "modules/order_board/bulk.py": parse_source(
            "ACTIONS = ['CONFIRM_SO', DocKind.PURCHASE_ORDER]\nfrom x import create_purchase_order\n"
        )
    }
    assert _po_mentions(bad) == {
        "modules/order_board/bulk.py": {"PURCHASE_ORDER", "create_purchase_order"}
    }
    assert (
        _po_mentions({"modules/order_board/ok.py": parse_source("ACTIONS = ['CONFIRM_SO']\n")})
        == {}
    )


def test_the_scheduler_registry_and_cli_do_not_name_any_po_job() -> None:
    """잡 레지스트리·CLI 서브커맨드 이름에 발주·PO 자동화가 없다 — 잡 코드는 만료 스윕·검산·스캔 계열뿐이고 §8.8 발주점 권고도 PO를 만들지 않는다(P4 — 권고는 알림만)"""
    from app.modules.platform.scheduler import JOB_REGISTRY

    assert JOB_REGISTRY
    for spec in JOB_REGISTRY:
        lowered = f"{spec.code} {spec.name_ko}".lower()
        assert "purchase" not in lowered and "발주" not in lowered and "-po-" not in lowered, (
            spec.code
        )
    source = (Path(APP_DIR) / "cli.py").read_text(encoding="utf-8")
    assert "purchase" not in source.lower() and "create_purchase_order" not in source
