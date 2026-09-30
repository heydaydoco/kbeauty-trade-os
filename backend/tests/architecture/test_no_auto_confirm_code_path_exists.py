"""K·I. 자동 확정·자동 전이 경로 부재 — 보호 함수의 호출처를 기계로 고정한다 (S3-1 ADR-0051 / design-B B9 #3·#10 / X-46).

DoD "발행·확정·발주는 사람 1클릭" (§15 L3 금지 4영역)을 문서가 아니라 코드로 지킨다. 한 파일에 **엔트리 등록 방식**으로
모은다: 각 묶음이 (보호 함수, 허용 호출처 집합, 금지 임포트 모듈 집합)을 1행씩 등록한다 — QT 발행(PR-5a)·SO 확정(PR-12)·
PO 생성(PR-8)·인테이크 확정(PR-13)·승인 결정/소비/무효/요청(PR-9·12)·벌크(PR-15)는 각자 자기 행을 더한다.
공회전 방지 자기검사(엔트리가 실재하고 허용 호출처가 실제로 부르며 스캐너가 위반을 잡는다)는 프레임워크가 일괄 수행한다.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

import pytest

from tests.support.astscan import app_sources, module_of, parse_source, referenced_names

pytestmark = pytest.mark.group_k


@dataclass(frozen=True)
class Entry:
    """보호 함수 1건의 등록 행."""

    name: str  # 보호 함수 이름(식별자)
    defined_in: str  # 정의 모듈(점 표기, 임포트해 시그니처를 검사한다)
    allowed_files: frozenset[str]  # 이 함수를 언급해도 되는 앱 상대 경로(정의 파일 포함)
    forbidden_modules: frozenset[str] = (
        frozenset()
    )  # 이 모듈들의 파일은 정의 모듈을 임포트하면 안 된다
    requires_actor: bool = True  # 시그니처에 keyword-only `actor`가 있어야 한다
    #: 금지 모듈이 정의 모듈을 **임포트하는 것 자체**를 막는가(정의 모듈이 여러 보호 함수를 품으면 False — 언급 검사만).
    forbid_module_import: bool = True
    notes: str = field(default="", compare=False)


REGISTRY: tuple[Entry, ...] = (
    Entry(
        name="issue_quotation",
        defined_in="app.modules.trade_chain.lifecycle",
        allowed_files=frozenset(
            {"modules/trade_chain/lifecycle.py", "modules/trade_chain/router.py"}
        ),
        forbidden_modules=frozenset(
            {
                "platform",
                "imports",
                "handover",
                "notifications",
                "outbox",
                "worklist",
                "deadlines",
                "collaboration",
                "certifications",
                "identity",
                "idempotency",
            }
        ),
        notes="QT 발행(동결 액션) — 라우터 1곳+행위자 필수. 스케줄러·임포트·이관·알림 경로에서 import 0",
    ),
    Entry(
        name="record_transition",
        defined_in="app.modules.trade_docs.transition",
        allowed_files=frozenset(
            {
                "modules/trade_docs/transition.py",
                "modules/trade_chain/lifecycle.py",
                "modules/trade_chain/chain_ops.py",
            }
        ),
        forbidden_modules=frozenset(
            {"quotations", "platform", "imports", "handover", "notifications"}
        ),
        requires_actor=False,  # 자동 전이는 행위자가 없을 수 있다(스윕) — 호출처 제한이 통제다
        forbid_module_import=False,  # 같은 모듈의 record_birth를 견적 서비스가 임포트한다 — 언급 검사로 충분
        notes="상태 전이 통로 — L1(quotations)은 전이하지 않는다(CRUD·라인만). L2 오케스트레이션만 호출",
    ),
    Entry(
        name="record_birth",
        defined_in="app.modules.trade_docs.transition",
        allowed_files=frozenset(
            {"modules/trade_docs/transition.py", "modules/quotations/service.py"}
        ),
        forbidden_modules=frozenset({"platform", "imports", "handover", "notifications"}),
        requires_actor=False,  # actor_user_id를 받는다(생성 서비스가 actor를 가진다)
        forbid_module_import=False,
        notes="전표 탄생(초기 상태 대입) — 생성 서비스 1곳",
    ),
    Entry(
        name="converge_quotation",
        defined_in="app.modules.trade_chain.chain_ops",
        allowed_files=frozenset({"modules/trade_chain/chain_ops.py"}),
        forbidden_modules=frozenset({"platform", "imports", "handover"}),
        requires_actor=False,
        notes="QT 자동 수렴 — 현재 호출처는 converge_parent뿐(후속 전표 PR이 호출부를 더한다)",
    ),
)


def _mentions(tree: ast.Module, name: str) -> bool:
    return name in referenced_names(tree)


def _imports_module(tree: ast.Module, dotted: str) -> bool:
    """`from a.b import c`·`import a.b.c`·`from a.b import c(=모듈)` 어느 형태로든 dotted(a.b.c)를 임포트하는가."""
    parent, _, leaf = dotted.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == dotted:
                return True
            if node.module == parent and any(alias.name == leaf for alias in node.names):
                return True
        elif isinstance(node, ast.Import) and any(alias.name == dotted for alias in node.names):
            return True
    return False


def _violations(entry: Entry, sources: dict[str, ast.Module]) -> list[str]:
    out: list[str] = []
    for rel, tree in sources.items():
        if rel not in entry.allowed_files and _mentions(tree, entry.name):
            out.append(f"{rel}: 허용 밖에서 {entry.name} 언급")
        if (
            entry.forbid_module_import
            and module_of(rel) in entry.forbidden_modules
            and _imports_module(tree, entry.defined_in)
        ):
            out.append(f"{rel}: 금지 모듈이 {entry.defined_in}를 임포트")
    return out


@pytest.mark.parametrize("entry", REGISTRY, ids=[e.name for e in REGISTRY])
def test_protected_function_is_reachable_only_from_the_registered_callers(entry: Entry) -> None:
    """보호 함수는 등록된 호출처에서만 언급된다 · 금지 모듈은 정의 모듈을 임포트하지 않는다"""
    assert _violations(entry, app_sources()) == []


@pytest.mark.parametrize("entry", REGISTRY, ids=[e.name for e in REGISTRY])
def test_registry_entries_are_not_vacuous(entry: Entry) -> None:
    """엔트리가 실재한다 — 함수가 정의돼 있고, 허용 호출처가 실제로 그것을 언급하며(죽은 등록 금지), 시그니처에 actor가 있다"""
    module = import_module(entry.defined_in)
    function = getattr(module, entry.name)
    assert callable(function)
    if entry.requires_actor:
        params = inspect.signature(function).parameters
        assert "actor" in params and params["actor"].kind is inspect.Parameter.KEYWORD_ONLY
    sources = app_sources()
    mentioning = {rel for rel, tree in sources.items() if _mentions(tree, entry.name)}
    assert mentioning, f"{entry.name}: 어디에서도 언급되지 않는다(죽은 등록)"
    assert mentioning <= entry.allowed_files
    assert entry.allowed_files <= set(sources), "허용 호출처에 실재하지 않는 파일이 있다"


def test_the_only_router_caller_of_issue_is_the_trade_chain_router() -> None:
    """QT 발행을 부르는 HTTP 표면은 trade_chain 라우터 1곳이고, 그 라우트는 무역 역할 게이트+멱등 키를 요구한다"""
    sources = app_sources()
    routers = {
        rel
        for rel, tree in sources.items()
        if rel.endswith("router.py") and _mentions(tree, "issue_quotation")
    }
    assert routers == {"modules/trade_chain/router.py"}
    text = Path(
        inspect.getsourcefile(import_module("app.modules.trade_chain.router")) or ""
    ).read_text(encoding="utf-8")
    assert "require_roles(*CAN_WRITE)" in text and "IdempotencyKey" in text


def test_scheduler_and_cli_cannot_reach_document_transitions() -> None:
    """스케줄러·CLI가 임포트하는 전표 모듈은 검산(trade_docs.verify)뿐이다 — 상태를 바꾸는 함수는 없다"""
    from tests.support.astscan import imported_modules

    for rel in ("modules/platform/scheduler.py", "cli.py"):
        tree = app_sources()[rel]
        modules = imported_modules(tree)
        assert "trade_chain" not in modules and "quotations" not in modules, rel
        assert not _mentions(tree, "record_transition") and not _mentions(tree, "issue_quotation")


def test_the_scanner_flags_a_forbidden_mention_and_a_forbidden_import() -> None:
    """자기검사 — 허용 밖 언급과 금지 모듈의 임포트를 실제로 잡는다(정상 소스에는 조용하다)"""
    entry = REGISTRY[0]
    bad = {
        "modules/platform/scheduler.py": parse_source(
            "from app.modules.trade_chain.lifecycle import issue_quotation\n"
        ),
        "modules/trade_chain/router.py": parse_source("issue_quotation()\n"),
    }
    found = _violations(entry, bad)
    assert len(found) >= 2
    assert not any("trade_chain/router.py" in line for line in found)
    clean = {"modules/trade_chain/router.py": parse_source("issue_quotation()\n")}
    assert _violations(entry, clean) == []
