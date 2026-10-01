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
                # PR-6a — 자동 전이 2종: 입금 수렴(테스트 호출, PR-10이 배선)·만료 스윕(잡 본체, 두 엣지뿐)
                "modules/trade_chain/payment_status.py",
                "modules/trade_chain/expiry_sweep.py",
            }
        ),
        forbidden_modules=frozenset(
            {
                "quotations",
                "proforma_invoices",
                "bank_accounts",
                "platform",
                "imports",
                "handover",
                "notifications",
            }
        ),
        requires_actor=False,  # 자동 전이는 행위자가 없을 수 있다(스윕) — 호출처 제한이 통제다
        forbid_module_import=False,  # 같은 모듈의 record_birth를 견적 서비스가 임포트한다 — 언급 검사로 충분
        notes="상태 전이 통로 — L1(quotations)은 전이하지 않는다(CRUD·라인만). L2 오케스트레이션만 호출",
    ),
    Entry(
        name="record_birth",
        defined_in="app.modules.trade_docs.transition",
        allowed_files=frozenset(
            {
                "modules/trade_docs/transition.py",
                "modules/quotations/service.py",
                "modules/proforma_invoices/service.py",  # PI 생성 착지(insert_issued) — 참조 생성 오케스트레이터가 부른다
                "modules/sales_orders/service.py",  # SO 생성 착지(create_received_sales_order) — 참조 생성·인테이크 확정이 부른다
                "modules/purchase_orders/service.py",  # PO 생성 착지(insert_issued) — create_purchase_order가 부른다(PR-8a)
            }
        ),
        forbidden_modules=frozenset({"platform", "imports", "handover", "notifications"}),
        requires_actor=False,  # actor_user_id를 받는다(생성 서비스가 actor를 가진다)
        forbid_module_import=False,
        notes="전표 탄생(초기 상태 대입) — 생성 서비스 1곳",
    ),
    Entry(
        name="create_proforma_invoice",
        defined_in="app.modules.trade_chain.reference",
        allowed_files=frozenset(
            {"modules/trade_chain/reference.py", "modules/trade_chain/router.py"}
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
        notes="PI 생성(참조 생성 = 발행·동결) — 라우터 1곳+행위자 필수. 스케줄러·임포트·이관·알림 경로에서 import 0",
    ),
    Entry(
        name="transition_proforma_invoice",
        defined_in="app.modules.trade_chain.lifecycle",
        allowed_files=frozenset(
            {"modules/trade_chain/lifecycle.py", "modules/trade_chain/router.py"}
        ),
        forbidden_modules=frozenset({"platform", "imports", "handover", "notifications"}),
        forbid_module_import=False,  # 같은 모듈의 다른 보호 함수(issue_quotation)와 정의 모듈이 같다 — 언급 검사로 충분
        notes="PI 취소(사람 전이) — 라우터 1곳+행위자 필수",
    ),
    Entry(
        name="create_sales_order_from_quotation",
        defined_in="app.modules.trade_chain.so_reference",
        allowed_files=frozenset(
            {"modules/trade_chain/so_reference.py", "modules/trade_chain/router.py"}
        ),
        forbidden_modules=frozenset(
            {"platform", "imports", "handover", "notifications", "outbox", "worklist"}
        ),
        forbid_module_import=False,  # 같은 모듈의 `create_sales_order_from_proforma_invoice`와 정의 모듈이 같다 — 언급 검사로 충분
        notes="QT→SO 참조 생성(접수 RECEIVED로만 태어난다 — 확정을 부르지 않는다) — 라우터 1곳+행위자 필수",
    ),
    Entry(
        name="create_sales_order_from_proforma_invoice",
        defined_in="app.modules.trade_chain.so_reference",
        allowed_files=frozenset(
            {"modules/trade_chain/so_reference.py", "modules/trade_chain/router.py"}
        ),
        forbidden_modules=frozenset(
            {"platform", "imports", "handover", "notifications", "outbox", "worklist"}
        ),
        forbid_module_import=False,
        notes="PI→SO 참조 생성(활성 1:1, 접수로만) — 라우터 1곳+행위자 필수",
    ),
    Entry(
        name="create_received_sales_order",
        defined_in="app.modules.sales_orders.service",
        allowed_files=frozenset(
            {
                "modules/sales_orders/service.py",
                "modules/trade_chain/so_reference.py",  # 참조 생성(QT/PI→SO) — PR-13 인테이크 확정이 여기 한 줄을 더한다
            }
        ),
        forbidden_modules=frozenset(
            {"platform", "imports", "handover", "notifications", "outbox", "worklist"}
        ),
        forbid_module_import=False,  # 정의 모듈이 조회·편집 함수도 품는다 — 언급 검사로 충분
        notes="SO 생성의 단일 착지 — RECEIVED로만 만든다(확정·승격 자동화 금지, B9 #3). 스케줄러·임포트·이관·알림 경로에서 언급 0",
    ),
    Entry(
        name="transition_sales_order",
        defined_in="app.modules.trade_chain.lifecycle",
        allowed_files=frozenset(
            {"modules/trade_chain/lifecycle.py", "modules/trade_chain/router.py"}
        ),
        forbidden_modules=frozenset({"platform", "imports", "handover", "notifications"}),
        forbid_module_import=False,
        notes="SO 보류·재개·취소(사람 전이) — 라우터 1곳+행위자 필수. 확정은 이 함수에 없다(PR-12)",
    ),
    Entry(
        name="create_purchase_order",
        defined_in="app.modules.purchase_orders.service",
        allowed_files=frozenset(
            {"modules/purchase_orders/service.py", "modules/purchase_orders/router.py"}
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
                "seeds",
                "readiness",
                "policies",
                # 후속 PR이 만드는 모듈 — 만들어지는 순간 이 금지가 적용된다(PO 자동 생성 경로 부재, design-F F2 (f)).
                "order_intake",
                "order_board",
                "approvals",
                "gates",
                "credit",
                "payments",
            }
        ),
        notes="**PO 생성 = 발행 = 발주 확정**(§15 L3 4금 ①) — 라우터 1곳+행위자 필수. 스케줄러·CLI·임포트·이관·알림·인테이크·보드·승인·게이트 경로에서 import·언급 0",
    ),
    Entry(
        name="transition_purchase_order",
        defined_in="app.modules.trade_chain.lifecycle",
        allowed_files=frozenset(
            {"modules/trade_chain/lifecycle.py", "modules/trade_chain/router.py"}
        ),
        forbidden_modules=frozenset({"platform", "imports", "handover", "notifications"}),
        forbid_module_import=False,
        notes="PO 공급사 확인(OC)·취소(사람 전이) — 라우터 1곳+행위자 필수. PO 자동 엣지는 0이다",
    ),
    Entry(
        name="sweep_expired_documents",
        defined_in="app.modules.trade_chain.expiry_sweep",
        allowed_files=frozenset(
            {
                "modules/trade_chain/expiry_sweep.py",
                "modules/platform/scheduler.py",  # 잡 레지스트리(자동 실행 — 두 엣지뿐)
                "cli.py",  # 수동 실행(--base-date)
            }
        ),
        forbidden_modules=frozenset({"imports", "handover", "notifications", "quotations"}),
        requires_actor=False,  # 자동 스윕 — 행위자 없음(actor NULL·automatic=true)
        notes="만료 스윕 — (QT,ISSUED→EXPIRED)·(PI,ISSUED→EXPIRED) 두 엣지만(ADR-0056 4금 논증)",
    ),
    # S3-1 PR-9a — 승인 통로 5종. 승인 결정은 **사람만**(자동 승인 경로 0): 호출처를 코드로 고정하고, 잡·CLI·임포트·이관·알림·아웃박스·시드는 닿지 못한다.
    # 요청·소비·무효의 호출 모듈(trade_chain 확정·요청 엔드포인트·편집·취소)은 PR-12가 자기 파일을 이 엔트리의 allowed_files에 더한다.
    Entry(
        name="decide_approval",
        defined_in="app.modules.approvals.service",
        allowed_files=frozenset({"modules/approvals/service.py", "modules/approvals/router.py"}),
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
                "seeds",
                "readiness",
                "policies",
                "order_intake",
                "order_board",
                "gates",
                "credit",
                "payments",
                "trade_chain",
                "sales_orders",
            }
        ),
        forbid_module_import=False,  # 정의 모듈이 요청·소비·무효도 품는다 — 언급 검사로 충분(임포트 경계는 test_approval_contract가 따로 고정)
        notes="**사람 결정 통로(승인·반려·회수)** — 라우터 1곳+실 사용자 행위자 필수. 호출처 집합 = DECIDE_CALLERS(P7 Slack 어댑터가 더할 때 ADR 동반)",
    ),
    # request_approval·consume_approval·void_for_target·note_bypass_attempt: **호출자가 생기는 PR-12가 엔트리를 더한다** — 호출처가 없는 지금 등록하면 "죽은 등록 금지" 자기검사가
    # 실패한다. PR-9a~PR-11 동안의 부재는 test_approval_contract(`test_no_domain_module_calls_the_system_channels_yet`·소비 접점 PENDING 장부)가 고정한다.
    # PR-11a — 게이트 override(사람 결정 통로): 부여·철회는 라우터 1곳+실 사용자 행위자 필수. 스케줄러·CLI·임포트·이관·알림·인테이크·보드 어디서도 호출·임포트하지 않는다(자동·벌크 부여 경로 0).
    Entry(
        name="grant_gate_override",
        defined_in="app.modules.trade_chain.gate_flow",
        allowed_files=frozenset(
            {"modules/trade_chain/gate_flow.py", "modules/trade_chain/router.py"}
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
                "seeds",
                "identity",
                "idempotency",
                "order_intake",
                "order_board",
            }
        ),
        notes="**게이트 override 부여** — 라우터 1곳+행위자 필수+멱등 키. 자동 부여·벌크 부여 경로 없음(D3 (d))",
    ),
    Entry(
        name="revoke_gate_override",
        defined_in="app.modules.trade_chain.gate_flow",
        allowed_files=frozenset(
            {"modules/trade_chain/gate_flow.py", "modules/trade_chain/router.py"}
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
                "seeds",
                "identity",
                "idempotency",
                "order_intake",
                "order_board",
            }
        ),
        notes="**게이트 override 철회** — 라우터 1곳+행위자 필수+멱등 키",
    ),
    Entry(
        name="grant_override",
        defined_in="app.modules.gates.service",
        allowed_files=frozenset({"modules/gates/service.py", "modules/trade_chain/gate_flow.py"}),
        forbidden_modules=frozenset(
            {
                "platform",
                "imports",
                "handover",
                "notifications",
                "outbox",
                "seeds",
                "order_intake",
                "order_board",
            }
        ),
        requires_actor=False,  # 서비스 계층은 actor_user_id·actor_roles를 받는다(행위자 필수 — None 불가는 test_gate_contract가 고정)
        forbid_module_import=False,  # 정의 모듈이 clearance·평가 틀도 품는다 — 언급 검사로 충분
        notes="override 부여 서비스 — 오케스트레이터(gate_flow) 1곳만 호출한다",
    ),
    Entry(
        name="revoke_override",
        defined_in="app.modules.gates.service",
        allowed_files=frozenset({"modules/gates/service.py", "modules/trade_chain/gate_flow.py"}),
        forbidden_modules=frozenset(
            {
                "platform",
                "imports",
                "handover",
                "notifications",
                "outbox",
                "seeds",
                "order_intake",
                "order_board",
            }
        ),
        requires_actor=False,
        forbid_module_import=False,
        notes="override 철회 서비스 — 오케스트레이터(gate_flow) 1곳만 호출한다",
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


def _trade_chain_imports(tree: ast.Module) -> set[str]:
    """이 트리가 임포트하는 `app.modules.trade_chain.<서브모듈>` 이름 집합(from/import 양쪽)."""
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
            if parts[:3] == ["app", "modules", "trade_chain"] and len(parts) >= 4:
                found.add(parts[3])
    return found


def test_scheduler_and_cli_reach_only_the_totals_check_and_the_expiry_sweep() -> None:
    """스케줄러·CLI가 임포트하는 전표 모듈은 검산(trade_docs.verify)과 **만료 스윕(trade_chain.expiry_sweep) 하나뿐**이다 —
    발행·전이·확정 함수(lifecycle·reference·payment_status)와 전표 CRUD 모듈은 언급조차 못 한다"""
    from tests.support.astscan import imported_modules

    for rel in ("modules/platform/scheduler.py", "cli.py"):
        tree = app_sources()[rel]
        assert _trade_chain_imports(tree) == {"expiry_sweep"}, rel
        modules = imported_modules(tree)
        assert not modules & {"quotations", "proforma_invoices", "bank_accounts"}, rel
        for forbidden in (
            "record_transition",
            "issue_quotation",
            "create_proforma_invoice",
            "transition_proforma_invoice",
            "converge_payment_status",
            "lock_chain",
        ):
            assert not _mentions(tree, forbidden), (rel, forbidden)


def test_the_expiry_sweep_creates_exactly_two_edges_and_touches_no_orders_or_numbers() -> None:
    """만료 스윕이 만드는 (전표, from, to)는 {(QT,ISSUED,EXPIRED),(PI,ISSUED,EXPIRED)}뿐이고, 발주·SO·채번·알림 모듈을 임포트하지 않는다
    (4금 ①③④ — 발주 확정·대외 발송·장부 확정 무접촉)"""
    from app.modules.trade_chain.expiry_sweep import SWEEP_EDGES
    from app.modules.trade_docs.constants import DocKind
    from tests.support.astscan import imported_modules

    assert {(k, a, b) for k, a, b in SWEEP_EDGES} == {
        (DocKind.QUOTATION, "ISSUED", "EXPIRED"),
        (DocKind.PROFORMA_INVOICE, "ISSUED", "EXPIRED"),
    }
    tree = app_sources()["modules/trade_chain/expiry_sweep.py"]
    used = imported_modules(tree)
    assert not used & {
        "sales_orders",
        "purchase_orders",
        "numbering",
        "notifications",
        "outbox",
        "worklist",
        "deadlines",
        "platform",
        "approvals",
        "credit",
        "payments",
    }, used
    assert not _mentions(tree, "issue_document_number") and not _mentions(tree, "notify")


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
