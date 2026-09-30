"""K. 전표 쓰기 통로 스캔 — 상태 대입·동결 시각·합계·채번·삭제·이력 생성의 단일 통로 (S3-1 ADR-0051·0053 / design-B B1·B9).

"한 곳만 지키는" 우회 결함을 계획 단계에서 표로 닫는다(B9 차단표 #1·#7 등). 각 규칙은 AST로 앱 소스를 훑고, **자기검사**가
위반 코퍼스를 실제로 잡는지 확인한다(스캔이 공회전해 조용히 초록이 되는 것을 막는다).
"""

from __future__ import annotations

import ast

import pytest

from tests.support.astscan import app_sources, module_of, parse_source

pytestmark = pytest.mark.group_k

#: 전표 도메인 모듈 — 각 전표 PR이 자기 모듈을 여기 더한다(스캔 대상 확장).
DOC_MODULES = {"trade_docs", "quotations", "trade_chain"}
DOC_MODEL_NAMES = {"Quotation", "QuotationLine"}
STATUS_LOG_NAMES = {"QuotationStatusLog"}
PREFIX_LITERALS = {"QT", "PI", "SO", "PO"}

TRANSITION = "modules/trade_docs/transition.py"
CONSTANTS = "modules/trade_docs/constants.py"
DOC_NUMBER = "modules/trade_docs/doc_number.py"
NUMBERING = "modules/numbering/service.py"
QT_SERVICE = "modules/quotations/service.py"


def _targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return [node.target]
    return []


def attribute_assignments(tree: ast.Module, attr: str) -> list[tuple[int, str]]:
    """`<receiver>.<attr> = …` 대입 위치 (줄, 수신자 이름 또는 '?')."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        for target in _targets(node):
            if isinstance(target, ast.Attribute) and target.attr == attr:
                receiver = target.value.id if isinstance(target.value, ast.Name) else "?"
                found.append((node.lineno, receiver))
    return found


def keyword_calls(tree: ast.Module, keyword: str, callees: set[str] | None = None) -> list[int]:
    """`Callee(..., keyword=…)` 호출 위치. callees가 있으면 그 이름의 호출만."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if callees is not None and name not in callees:
            continue
        if any(kw.arg == keyword for kw in node.keywords):
            lines.append(node.lineno)
    return lines


def setattr_with_literal(tree: ast.Module, attr: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == attr
    ]


def call_lines(tree: ast.Module, names: set[str]) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            )
            if name in names:
                lines.append(node.lineno)
    return lines


def _doc_module_sources() -> dict[str, ast.Module]:
    return {rel: tree for rel, tree in app_sources().items() if module_of(rel) in DOC_MODULES}


def test_the_scan_is_not_vacuous() -> None:
    """스캔 대상 파일이 실제로 있고 전이 통로가 status를 실제로 대입한다"""
    sources = _doc_module_sources()
    assert {"modules/trade_docs/transition.py", "modules/quotations/service.py"} <= set(sources)
    assert attribute_assignments(sources[TRANSITION], "status"), (
        "transition.py가 status를 대입하지 않는다"
    )


def test_status_is_assigned_only_by_the_transition_channel() -> None:
    """`.status =`·`status=` 생성자·setattr·update().values(status=)는 transition.py 밖 전표 모듈에 0건 (B9 #1)"""
    offenders: list[str] = []
    for rel, tree in _doc_module_sources().items():
        if rel == TRANSITION:
            continue
        for line, _receiver in attribute_assignments(tree, "status"):
            offenders.append(f"{rel}:{line} .status = …")
        for line in keyword_calls(tree, "status", DOC_MODEL_NAMES | {"values"}):
            offenders.append(f"{rel}:{line} status=…")
        for line in setattr_with_literal(tree, "status"):
            offenders.append(f"{rel}:{line} setattr(…, 'status')")
    assert offenders == [], f"상태 대입 통로 밖의 대입: {offenders}"


def test_freeze_marker_is_written_only_through_the_transition_channel() -> None:
    """동결 시각(frozen_at·confirmed_at)은 record_transition이 setattr(FREEZE_COLUMN)로만 쓴다 — 직접 대입 0건"""
    offenders = [
        f"{rel}:{line}"
        for rel, tree in _doc_module_sources().items()
        for attr in ("frozen_at", "confirmed_at")
        for line, _r in attribute_assignments(tree, attr)
    ]
    assert offenders == []
    source = app_sources()[TRANSITION]
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr"
        for node in ast.walk(source)
    ), "transition.py가 동결 시각을 setattr로 쓰지 않는다"


def test_totals_are_never_assigned_directly_and_only_the_create_path_passes_them() -> None:
    """헤더 합계는 대입하지 않는다(recompute_total의 setattr뿐) · 생성자 total_amount=는 견적 서비스 insert_draft 한 곳"""
    for rel, tree in _doc_module_sources().items():
        for attr in ("total_amount", "total_cost"):
            assert attribute_assignments(tree, attr) == [], f"{rel}: {attr} 직접 대입"
    creators = {
        rel: keyword_calls(tree, "total_amount", DOC_MODEL_NAMES)
        for rel, tree in _doc_module_sources().items()
    }
    assert {rel for rel, lines in creators.items() if lines} == {QT_SERVICE}
    assert len(creators[QT_SERVICE]) == 1
    editing = app_sources()["modules/trade_docs/editing.py"]
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setattr"
        for n in ast.walk(editing)
    ), "recompute_total이 합계를 setattr로 쓰지 않는다"


def test_document_headers_are_never_soft_deleted_and_doc_number_is_never_reassigned() -> None:
    """전표 삭제 경로 0 — `.deleted_at =`의 수신자는 라인(line)뿐이다 · doc_number는 생성자에서만(insert_draft) (B9)"""
    for rel, tree in _doc_module_sources().items():
        for line, receiver in attribute_assignments(tree, "deleted_at"):
            assert receiver == "line", f"{rel}:{line} 헤더 soft delete 의심(수신자 {receiver})"
        assert attribute_assignments(tree, "doc_number") == [], rel
    users = [
        rel
        for rel, tree in _doc_module_sources().items()
        if keyword_calls(tree, "doc_number", DOC_MODEL_NAMES)
    ]
    assert users == [QT_SERVICE]


def test_document_numbers_are_issued_only_through_the_kernel_wrapper() -> None:
    """next_document_number 호출은 trade_docs/doc_number.py 한 곳 · 전표 모듈에 접두어 문자열 리터럴·MAX+1 패턴 0건"""
    callers = {
        rel for rel, tree in app_sources().items() if call_lines(tree, {"next_document_number"})
    }
    assert callers == {DOC_NUMBER}, callers
    for rel, tree in _doc_module_sources().items():
        if rel == CONSTANTS:
            continue
        literals = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value in PREFIX_LITERALS
        }
        assert literals == set(), f"{rel}: 접두어 리터럴 {literals}"
        assert call_lines(tree, {"max"}) == [], (
            rel
        )  # func.max(...)+1 채번 금지(빌트인 max도 전표 모듈엔 불필요)


def test_status_log_rows_are_created_only_by_the_transition_channel() -> None:
    """상태이력 행 생성(QuotationStatusLog(…)·STATUS_LOG_MODELS[…])은 transition.py·models.py 밖에 0건 — 이력 위조 통로 차단"""
    allowed = {TRANSITION, "modules/trade_docs/models.py"}
    for rel, tree in app_sources().items():
        if rel in allowed:
            continue
        assert call_lines(tree, STATUS_LOG_NAMES) == [], rel
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                assert node.value.id != "STATUS_LOG_MODELS", rel


def test_line_writes_go_through_the_editable_guard() -> None:
    """라인 쓰기 3함수(add·update·remove)는 assert_editable을 호출하고 헤더를 lock_document로 먼저 잠근다 (B9 #7)"""
    tree = app_sources()[QT_SERVICE]
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("add_line", "update_line", "remove_line"):
        calls = [
            c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
            for c in ast.walk(functions[name])
            if isinstance(c, ast.Call)
        ]
        assert "assert_editable" in calls and "lock_document" in calls, name
        assert calls.index("lock_document") < calls.index("assert_editable"), f"{name}: 잠금이 먼저"


def test_no_http_delete_on_the_document_itself() -> None:
    """전표 자체의 DELETE 엔드포인트는 없다 — 폐기는 CANCELLED 전이뿐(라인 제외 DELETE만 있다)"""
    from app.main import app

    deletes = {
        path
        for path, operations in app.openapi()["paths"].items()
        if "delete" in operations and "/quotations" in path
    }
    assert deletes == {"/api/v1/quotations/{qt_id}/lines/{line_id}"}


# ── 자기검사: 스캐너가 위반 코퍼스를 실제로 잡는다 ────────────────────────────────


def test_scanners_detect_violations_in_a_synthetic_corpus() -> None:
    """공회전 방지 — 위반 코드 조각에서 각 스캐너가 반응하고, 정상 조각에서는 조용하다"""
    bad = parse_source(
        "doc.status = 'CANCELLED'\n"
        "row.deleted_at = now\n"
        "Quotation(status='X', doc_number='QT-1', total_amount=1)\n"
        "setattr(doc, 'status', 'X')\n"
        "session.execute(update(T).values(status='X'))\n"
        "n = next_document_number(s, 'QT')\n"
    )
    assert attribute_assignments(bad, "status") == [(1, "doc")]
    assert attribute_assignments(bad, "deleted_at") == [(2, "row")]
    assert keyword_calls(bad, "status", DOC_MODEL_NAMES | {"values"}) == [3, 5]
    assert keyword_calls(bad, "doc_number", DOC_MODEL_NAMES) == [3]
    assert setattr_with_literal(bad, "status") == [4]
    assert call_lines(bad, {"next_document_number"}) == [6]
    good = parse_source("line.deleted_at = now\nx = doc.status\nQuotationLine(qt_id=1)\n")
    assert attribute_assignments(good, "status") == []
    assert attribute_assignments(good, "deleted_at") == [(1, "line")]
    assert keyword_calls(good, "status", DOC_MODEL_NAMES) == []
