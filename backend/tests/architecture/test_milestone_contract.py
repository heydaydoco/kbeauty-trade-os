"""H·I·K. 마일스톤·통관 계약 스캔 — 상태 전이 0·발송 0·SHIPMENT 통신 기록 단일 통로·종류 열거 ↔ DB CHECK 대사
(S3-2 PR-4a / ADR-0080·0083 / design-B B8 ⑦·B9 / design-integrated §9 R-05 / design-C C12).

■ **상태 전이 0**(B8 ⑦): 마일스톤·통관 쓰기는 선적·SO 상태를 바꾸지 않는다 — 전이·탄생·사슬 잠금·수렴 함수를 언급하지 않는다(재계산 = 읽기 결과의 변화).
■ **발송 0**(H — 통보 = 기록): 통보 경로는 HTTP·메일·메신저 클라이언트·알림 코어·아웃박스를 쓰지 않는다(통보는 comm_logs 행 + 연결 행뿐).
■ **SHIPMENT 통신 기록 단일 통로**(R-05): `CommLog(` 생성은 협업 서비스 2곳(범용 생성 — 스키마가 CERTIFICATION만 받음 / 선적 통보 착지)뿐이다.
■ 종류 열거 ↔ DB CHECK: 저장형 12종 = `ck_milestones_type_valid`, 파생 3종은 값 공간 밖, 품목군 세트 = 선적 저장형 8종, 보드 순서 = 11종.
스캔은 공회전하지 않도록 자기검사로 위반을 실제로 잡는다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.modules.shipments.models import ItemProfileMilestoneType, Milestone
from app.modules.trade_docs.constants import (
    DATETIME_MILESTONES,
    DERIVED_MILESTONES,
    OEM_MILESTONES,
    RELEASE_BOUND_ACTUALS,
    ROLLOVER_TYPES,
    SHIPMENT_BOARD_ORDER,
    SHIPMENT_MILESTONES_BY_KIND,
    SHIPMENT_STORED_MILESTONES,
    STORED_MILESTONES,
    MilestoneType,
)
from tests.support.astscan import app_sources, imported_modules, parse_source, referenced_names
from tests.support.kst import pin_today_kst

pytestmark = pytest.mark.group_k


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정(적대 검토 반영 ⑩) — 시험마다 base 날짜를 한 번 잡아
    앱 import 지점(마일스톤·통관·선적 흐름·보드)의 `today_kst`를 같은 날로 맞춘다."""
    pin_today_kst(monkeypatch)


MILESTONE_FILES = (
    "modules/trade_chain/milestone_flow.py",
    "modules/trade_chain/customs_flow.py",
    "modules/trade_chain/milestone_view.py",
    "modules/trade_chain/milestone_router.py",
    # S3-2 PR-4c — 품목군 마일스톤 세트 쓰기(전이·발송 0 — OEM 쓰기는 milestone_flow 안)
    "modules/trade_chain/milestone_set_flow.py",
)
#: 상태를 바꾸는 커널·사슬 함수 — 마일스톤·통관 모듈은 언급조차 하지 않는다.
TRANSITION_NAMES = {
    "record_transition",
    "record_birth",
    "lock_chain",
    "converge_parent",
    "converge_sales_order_shipping",
    "issue_document_number",
}
#: 대외 발송 수단(HTTP·메일·메신저)과 알림 코어 — 통보 경로에서 0.
OUTBOUND_MODULES = {"httpx", "requests", "smtplib", "urllib", "aiohttp", "slack_sdk", "email"}
OUTBOUND_NAMES = {"notify", "send", "send_message", "sendmail", "post_message"}


@pytest.mark.group_i
def test_milestone_and_customs_writes_never_transition_documents() -> None:
    """B8 ⑦ — 마일스톤·통관 5파일(PR-4c 세트 쓰기 포함)은 전이·탄생·사슬 잠금·수렴·채번을 언급하지 않는다(실적 입력이 선적·SO·PO 상태를 바꾸지
    않는다 — 자동 엣지 0, OEM 실적도 PO 상태 무변경)"""
    sources = app_sources()
    for rel in MILESTONE_FILES:
        assert rel in sources, rel
        assert not referenced_names(sources[rel]) & TRANSITION_NAMES, (
            rel,
            referenced_names(sources[rel]) & TRANSITION_NAMES,
        )


@pytest.mark.group_h
def test_the_notice_path_sends_nothing() -> None:
    """H(통보 = 기록, 발송 0) — 마일스톤·통관 5파일은 HTTP·메일·메신저 클라이언트·알림 코어를 임포트하지 않고 발송 함수를 부르지 않는다.
    통보 착지(`record_shipment_comm_log`)는 아웃박스도 쓰지 않는다(행 1개 추가뿐)"""
    sources = app_sources()
    for rel in MILESTONE_FILES:
        tree = sources[rel]
        modules = imported_modules(tree)
        assert not modules & {"notifications", "deadlines", "worklist"}, (rel, modules)
        raw = _raw_imports(tree)
        assert not {name for name in raw if name.split(".")[0] in OUTBOUND_MODULES}, (rel, raw)
        assert not referenced_names(tree) & OUTBOUND_NAMES, rel
    flow = sources["modules/trade_chain/milestone_flow.py"]
    notice = next(
        n
        for n in ast.walk(flow)
        if isinstance(n, ast.FunctionDef) and n.name == "record_milestone_notice"
    )
    assert "publish" not in {
        node.attr for node in ast.walk(notice) if isinstance(node, ast.Attribute)
    }, "통보 경로가 아웃박스 이벤트를 쓴다"
    collab = sources["modules/collaboration/service.py"]
    landing = next(
        n
        for n in ast.walk(collab)
        if isinstance(n, ast.FunctionDef) and n.name == "record_shipment_comm_log"
    )
    assert not {"publish", "notify"} & {
        node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
        for node in ast.walk(landing)
    }


def _raw_imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _comm_log_constructors(tree: ast.Module) -> list[str]:
    """`CommLog(`를 호출하는 함수 이름 목록."""
    found: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CommLog"
            ):
                found.append(function.name)
    return found


def test_shipment_comm_logs_have_a_single_landing() -> None:
    """R-05 — 통신 기록 행 생성(`CommLog(`)은 협업 서비스의 두 함수뿐: 범용 생성(`create_comm_log` — 스키마 Literal이 CERTIFICATION만)과
    선적 통보 착지(`record_shipment_comm_log` — 호출처는 선적 통보 통로 1곳, no_auto_confirm 레지스트리)"""
    sites = {
        (rel, name) for rel, tree in app_sources().items() for name in _comm_log_constructors(tree)
    }
    assert sites == {
        ("modules/collaboration/service.py", "create_comm_log"),
        ("modules/collaboration/service.py", "record_shipment_comm_log"),
    }, sites


def test_the_constructor_scan_is_not_vacuous() -> None:
    """자기검사 — 함수 안의 `CommLog(` 생성을 실제로 잡고, 발송 클라이언트 임포트도 잡는다"""
    tree = parse_source("def leak():\n    return CommLog(subject_type='SHIPMENT')\n")
    assert _comm_log_constructors(tree) == ["leak"]
    assert "httpx" in _raw_imports(parse_source("import httpx\n"))


def _check_values(model: type, name: str) -> set[str]:
    table = model.__table__  # type: ignore[attr-defined]
    for constraint in table.constraints:
        if str(getattr(constraint, "name", "")) in (name, f"ck_{table.name}_{name}"):
            return set(re.findall(r"'([A-Z_]+)'", str(constraint.sqltext)))
    raise AssertionError(f"제약 {name} 없음")


def test_milestone_types_match_the_db_check_and_derived_types_are_outside_it() -> None:
    """MilestoneType ↔ DB CHECK 대사 — 저장형 12종 = `type_valid` 값 공간(파생 3종 밖 — 덮어쓰기 금지 2중의 DB 층), 품목군 세트 = 선적 저장형 8종,
    보드 = 저장형 8 + 파생 3 = 11행(중복 0), 출고 결속 실적·시각형 ⊂ 선적 저장형, 구분별 적용 집합 ⊂ 선적 저장형"""
    every = {t.value for t in MilestoneType}
    assert every == STORED_MILESTONES | DERIVED_MILESTONES
    assert not STORED_MILESTONES & DERIVED_MILESTONES
    assert STORED_MILESTONES == SHIPMENT_STORED_MILESTONES | OEM_MILESTONES
    assert len(SHIPMENT_STORED_MILESTONES) == 8 and len(DERIVED_MILESTONES) == 3
    assert _check_values(Milestone, "type_valid") == STORED_MILESTONES
    assert _check_values(ItemProfileMilestoneType, "type_valid") == SHIPMENT_STORED_MILESTONES
    assert len(SHIPMENT_BOARD_ORDER) == len(set(SHIPMENT_BOARD_ORDER)) == 11
    assert set(SHIPMENT_BOARD_ORDER) == SHIPMENT_STORED_MILESTONES | DERIVED_MILESTONES
    assert {"ETD", "BL_ISSUED", "ETA"} == RELEASE_BOUND_ACTUALS
    assert {"DOC_CUTOFF", "CARGO_CLOSING"} == DATETIME_MILESTONES
    # 롤오버 배지 대상(design-B B9 — PR-6 공유)
    assert {"ETD", "ETA", "CARGO_CLOSING"} == ROLLOVER_TYPES
    for kind, types in SHIPMENT_MILESTONES_BY_KIND.items():
        assert types < SHIPMENT_STORED_MILESTONES, kind
        assert types >= RELEASE_BOUND_ACTUALS, kind


def test_the_today_pin_covers_every_milestone_import_point() -> None:
    """적대 검토 반영 ⑩ 자기검사 — 마일스톤·통관·선적 흐름 중 `today_kst`를 부르는 trade_chain 모듈은 전부 고정 목록에 있고
    (새 import 지점이 생기면 이 시험이 지목), autouse 고정이 실제로 같은 날을 돌려준다"""
    from tests.support.kst import MILESTONE_TODAY_IMPORT_POINTS

    pinned = {module.__name__ for module in MILESTONE_TODAY_IMPORT_POINTS}
    readers = {
        "app." + rel.removesuffix(".py").replace("/", ".")
        for rel, tree in app_sources().items()
        if rel.startswith("modules/trade_chain/")
        and rel.split("/")[-1].startswith(("milestone_", "customs_", "shipment_"))
        and "today_kst" in referenced_names(tree)
    }
    assert readers, "공회전 — today_kst를 부르는 모듈을 하나도 못 찾음"
    assert readers <= pinned, readers - pinned
    assert len({module.today_kst() for module in MILESTONE_TODAY_IMPORT_POINTS}) == 1


#: '마일스톤 계열' 시험 모듈 — 이름에 milestone·customs가 든 시험 파일(선적 마일스톤·통관·OEM 생산 일정·품목군 세트).
_MILESTONE_TEST_NAME = re.compile(r"^test_.*(milestone|customs).*\.py$")


def _pin_problems(rel: str, tree: ast.Module) -> list[str]:
    """시험 모듈 1개의 KST 고정 규율 위반 — ① `today_kst`를 참조하면 `pin_today_kst`도 참조해야 하고 ② `today_kst`를 **자기 이름으로
    가져오면**(`from … import today_kst`) 고정 호출에 그 모듈 자신(추가 인자)을 넘겨야 한다(안 넘기면 시험의 '오늘'만 자정을 넘는다)."""
    names = referenced_names(tree)
    if "today_kst" not in names:
        return []
    if "pin_today_kst" not in names:
        return [f"{rel}: today_kst를 쓰는데 pin_today_kst 고정이 없다"]
    binds_name = any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "today_kst" for alias in node.names)
        for node in ast.walk(tree)
    )
    pin_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "pin_today_kst"
    ]
    if binds_name and not any(len(call.args) >= 2 for call in pin_calls):
        return [f"{rel}: today_kst를 직접 가져오는데 고정 호출에 시험 모듈 자신을 넘기지 않는다"]
    return []


def test_every_milestone_test_module_that_reads_today_also_pins_it() -> None:
    """PR-4c 적대 검토 반영 ① 재발 방지 — 마일스톤 계열 시험 모듈(이름에 milestone·customs) 중 `today_kst`를 참조하는 것은 전부
    `pin_today_kst`도 참조하고, `today_kst`를 직접 가져오는 모듈은 고정 호출에 자기 모듈을 넘긴다(KST 자정 경계 오판 0)"""
    root = Path(__file__).resolve().parents[1]
    modules = {
        str(path.relative_to(root.parent)): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("test_*.py"))
        if _MILESTONE_TEST_NAME.match(path.name)
    }
    readers = [rel for rel, tree in modules.items() if "today_kst" in referenced_names(tree)]
    assert len(readers) >= 5, readers  # 공회전 방지 — 선적 마일스톤·통관·동시성·OEM e2e·OEM 동시성
    problems = [problem for rel, tree in modules.items() for problem in _pin_problems(rel, tree)]
    assert problems == [], problems


def test_the_pin_scan_catches_a_missing_pin() -> None:
    """자기검사 — 고정 없는 모듈·자기 모듈을 안 넘긴 고정을 실제로 잡고, 올바른 모듈에는 조용하다"""
    missing = parse_source("from app.core.time import today_kst\nx = today_kst()\n")
    assert _pin_problems("a.py", missing)
    unbound = parse_source(
        "from app.core.time import today_kst\nfrom tests.support.kst import pin_today_kst\n"
        "def f(m):\n    pin_today_kst(m)\n"
    )
    assert _pin_problems("b.py", unbound)
    good = parse_source(
        "import sys\nfrom app.core.time import today_kst\nfrom tests.support.kst import pin_today_kst\n"
        "def f(m):\n    pin_today_kst(m, sys.modules[__name__])\n"
    )
    assert _pin_problems("c.py", good) == []
