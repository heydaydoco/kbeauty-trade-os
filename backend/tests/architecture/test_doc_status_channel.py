"""K. 전표 쓰기 통로 스캔 — 상태 대입·동결 시각·합계·채번·삭제·이력 생성의 단일 통로 (S3-1 ADR-0051·0053 / design-B B1·B9).

"한 곳만 지키는" 우회 결함을 계획 단계에서 표로 닫는다(B9 차단표 #1·#7 등). 각 규칙은 AST로 앱 소스를 훑고, **자기검사**가
위반 코퍼스를 실제로 잡는지 확인한다(스캔이 공회전해 조용히 초록이 되는 것을 막는다).

■ PROTECTED(상태·동결 시각·합계·번호)는 **통로가 하나뿐이어야 하는 열**이라 이 파일의 스캔 대상이다. 은행 스냅샷 6열·`valid_until`·
  `buyer_address` 같은 **CONTENT 동결 열**은 이 스캔이 아니라 ① FIELD_POLICY 완전성(test_doc_field_policy — 모든 컬럼이 정확히 하나로
  분류돼야 CI 통과) ② 동결 가드(QT는 `assert_editable`, PI는 편집 구간이 없어 CONTENT를 쓰는 서비스 함수 자체가 없음 —
  아래 `test_pi_frozen_columns_are_never_assigned_after_creation`이 PI·사슬 모듈의 속성 대입 0건을 고정)가 커버한다.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

import pytest

from tests.support.astscan import app_sources, module_of, parse_source, referenced_names

pytestmark = pytest.mark.group_k

#: 전표 도메인 모듈 — 각 전표 PR이 자기 모듈을 여기 더한다(스캔 대상 확장).
DOC_MODULES = {"trade_docs", "quotations", "proforma_invoices", "sales_orders", "trade_chain"}
DOC_MODEL_NAMES = {
    "Quotation",
    "QuotationLine",
    "ProformaInvoice",
    "ProformaInvoiceLine",
    "SalesOrder",
    "SalesOrderLine",
}
STATUS_LOG_NAMES = {"QuotationStatusLog", "ProformaInvoiceStatusLog", "SalesOrderStatusLog"}
PREFIX_LITERALS = {"QT", "PI", "SO", "PO"}

TRANSITION = "modules/trade_docs/transition.py"
CONSTANTS = "modules/trade_docs/constants.py"
DOC_NUMBER = "modules/trade_docs/doc_number.py"
NUMBERING = "modules/numbering/service.py"
QT_SERVICE = "modules/quotations/service.py"
PI_SERVICE = "modules/proforma_invoices/service.py"
SO_SERVICE = "modules/sales_orders/service.py"


def _flatten(target: ast.expr) -> list[ast.expr]:
    """튜플·리스트·스타 언패킹 대입(`a.status, b = …`)까지 펼친다."""
    if isinstance(target, ast.Tuple | ast.List):
        return [leaf for element in target.elts for leaf in _flatten(element)]
    if isinstance(target, ast.Starred):
        return _flatten(target.value)
    return [target]


def _targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return [leaf for target in node.targets for leaf in _flatten(target)]
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return _flatten(node.target)
    return []


def attribute_assignments(tree: ast.Module, attr: str) -> list[tuple[int, str]]:
    """`<receiver>.<attr> = …` 대입 위치 (줄, 수신자 이름 또는 '?') — 튜플 언패킹 포함."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        for target in _targets(node):
            if isinstance(target, ast.Attribute) and target.attr == attr:
                receiver = target.value.id if isinstance(target.value, ast.Name) else "?"
                found.append((target.lineno, receiver))
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


# ── 보호 열 쓰기 지점 탐지 (통로 밖 대입 차단의 공통 엔진) ─────────────────────────────

#: 통로(record_transition·recompute_total·insert_draft) 밖에서 쓰면 안 되는 헤더 열.
PROTECTED = frozenset(
    {"status", "frozen_at", "confirmed_at", "total_amount", "total_cost", "doc_number"}
)
#: 키를 정적으로 알 수 없는 쓰기(`**dict`·동적 setattr·비리터럴 values) 표식.
UNKNOWN = "**?"

#: 허용된 쓰기 지점 — (파일, 함수, 열). **파일:함수 단위**로 좁게 둔다. 각 항목은 실제로 쓰여야 한다(썩은 허용 목록 방지).
ALLOWED_SITES: frozenset[tuple[str, str, str]] = frozenset(
    {
        (TRANSITION, "record_birth", "status"),  # 탄생 상태 대입(이력·이벤트와 한 쌍)
        (TRANSITION, "record_transition", "status"),  # 유일한 전이 통로
        (TRANSITION, "record_transition", UNKNOWN),  # setattr(doc, FREEZE_COLUMN[kind], now)
        ("modules/trade_docs/editing.py", "recompute_total", UNKNOWN),  # setattr(헤더 합계 열)
        (QT_SERVICE, "insert_draft", "total_amount"),  # 생성 시점 합계(라인 합을 미리 계산)
        (QT_SERVICE, "insert_draft", "doc_number"),  # 채번은 생성자에서만
        (QT_SERVICE, "insert_draft", UNKNOWN),  # **header — _header_columns의 화이트리스트 결과
        # PI 생성 착지 — 참조 생성 오케스트레이터(reference._plan)가 만든 헤더 dict(리터럴 키만, 아래 자기검사가 확인)
        (PI_SERVICE, "insert_issued", "total_amount"),
        (PI_SERVICE, "insert_issued", "doc_number"),
        (PI_SERVICE, "insert_issued", UNKNOWN),
        # SO 생성 착지 — 참조 생성 오케스트레이터(so_reference)가 만든 draft의 결제조건·Incoterms 열 dict(`**draft.payment_columns`)
        (SO_SERVICE, "create_received_sales_order", "total_amount"),
        (SO_SERVICE, "create_received_sales_order", "doc_number"),
        (SO_SERVICE, "create_received_sales_order", UNKNOWN),
        # 헤더 편집: setattr(row, name, value)의 name은 _header_columns가 만든 화이트리스트 cols의 키(아래 자기검사가 확인)
        (QT_SERVICE, "update_quotation", UNKNOWN),
        (QT_SERVICE, "update_meta", UNKNOWN),
        (
            SO_SERVICE,
            "update_sales_order",
            UNKNOWN,
        ),  # setattr(row, name, value) — name은 `_header_columns` 화이트리스트 결과
        # 담당 이관: update(target.model).values({target.column.key: …}) — 열은 ASSIGNMENT_TARGETS(아래 테스트가 확인)
        ("modules/handover/service.py", "reassign_all", UNKNOWN),
    }
)
_MODEL_CALLS = DOC_MODEL_NAMES
_WRITE_STARTERS = {"update", "insert"}


def _call_name(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")


def _dict_keys(node: ast.expr) -> set[str]:
    if not isinstance(node, ast.Dict):
        return {UNKNOWN}
    keys: set[str] = set()
    for key in node.keys:
        keys.add(
            key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else UNKNOWN
        )
    return keys


def _write_keys(call: ast.Call) -> set[str]:
    """호출이 쓰는 열 이름들 — 키워드·`**{…}`·위치 dict. 리터럴이 아니면 UNKNOWN."""
    keys: set[str] = set()
    for kw in call.keywords:
        keys |= {kw.arg} if kw.arg else _dict_keys(kw.value)
    for arg in call.args:
        keys |= _dict_keys(arg)
    return keys


def _root_name(node: ast.expr) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _targets_doc_or_dynamic(arg: ast.expr) -> bool:
    """update()의 대상이 전표 모델이거나, 정적으로 알 수 없는 모델(`target.model`처럼 대문자 클래스명이 아닌 것)인가."""
    root = _root_name(arg)
    return root in DOC_MODEL_NAMES or not root[:1].isupper()


def _chain_targets_doc_model(call: ast.Call) -> bool:
    """`update(Quotation)…values(…)` 체인의 뿌리가 전표 모델인가."""
    node: ast.expr = call.func
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            if (
                _call_name(node) in _WRITE_STARTERS
                and node.args
                and _targets_doc_or_dynamic(node.args[0])
            ):
                return True
            node = node.func
        else:
            return False


def walk_with_function(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """(노드, 둘러싼 함수 이름) — 재귀 순회라 BFS/DFS 순서에 기대지 않는다. 최상위는 '<module>'."""

    def visit(node: ast.AST, function: str) -> Iterator[tuple[ast.AST, str]]:
        yield node, function
        inner = node.name if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else function
        for child in ast.iter_child_nodes(node):
            yield from visit(child, inner)

    yield from visit(tree, "<module>")


def is_doc_scope(rel: str, tree: ast.Module) -> bool:
    """전표 모듈이거나 전표 모델을 언급하는 파일(예: handover) — 속성 대입·setattr 스캔 대상."""
    return module_of(rel) in DOC_MODULES or bool(referenced_names(tree) & DOC_MODEL_NAMES)


def protected_write_sites(
    rel: str, tree: ast.Module, *, doc_scope: bool
) -> list[tuple[int, str, str, str]]:
    """보호 열을 쓰는(또는 쓸 수 있는) 모든 지점 — (줄, 함수, 열, 방식).

    범위 밖 파일은 전표 모델 생성자·`update(전표)…values`만 본다(다른 모델의 같은 이름 열은 무관).
    """
    sites: list[tuple[int, str, str, str]] = []
    for node, function in walk_with_function(tree):
        if doc_scope:
            for target in _targets(node):
                if isinstance(target, ast.Attribute) and target.attr in PROTECTED:
                    sites.append((target.lineno, function, target.attr, "attr"))
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if (
            doc_scope
            and name == "setattr"
            and isinstance(node.func, ast.Name)
            and len(node.args) >= 2
        ):
            arg = node.args[1]
            column = (
                arg.value
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                else UNKNOWN
            )
            if column in PROTECTED or column == UNKNOWN:
                sites.append((node.lineno, function, column, "setattr"))
        elif name in _MODEL_CALLS:
            sites.extend(
                (node.lineno, function, key, "ctor")
                for key in _write_keys(node)
                if key in PROTECTED or key == UNKNOWN
            )
        elif name == "values" and (doc_scope or _chain_targets_doc_model(node)):
            sites.extend(
                (node.lineno, function, key, "values")
                for key in _write_keys(node)
                if key in PROTECTED or key == UNKNOWN
            )
    return sites


#: 문자열 SQL 스캔이 보는 전표 계열 테이블 — 헤더·라인·상태이력·은행 계좌(PI가 스냅샷을 복사하는 마스터).
_SQL_DOC_TABLES = (
    "quotations",
    "quotation_lines",
    "quotation_status_log",
    "proforma_invoices",
    "proforma_invoice_lines",
    "proforma_invoice_status_log",
    "sales_orders",
    "sales_order_lines",
    "sales_order_status_log",
    "bank_accounts",
)
#: `UPDATE [ONLY] [public.]["]table` · `INSERT INTO …` · `DELETE FROM …` (대소문자·개행 무시). f-string은 값 자리를 `{}`로 접어 본다.
_SQL_WRITE = re.compile(
    r"(?is)\b(update|insert\s+into|delete\s+from)\s+(only\s+)?(public\s*\.\s*)?[\"]?"
    r"(" + "|".join(_SQL_DOC_TABLES) + r")\b"
)
_SQL_DYNAMIC_TABLE = re.compile(
    r"(?is)\b(update|insert\s+into|delete\s+from)\s+(only\s+)?(public\s*\.\s*)?[\"]?\{\}"
)


def _sql_texts(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """문자열 상수와 f-string(JoinedStr — 값 자리는 `{}`)의 (줄, 텍스트). f-string 안의 조각 상수는 중복 집계하지 않는다."""
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text_parts = [
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                for v in node.values
            ]
            skip.update(id(v) for v in node.values)
            yield node.lineno, "".join(text_parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def raw_sql_doc_writes(tree: ast.Module) -> list[int]:
    """문자열 SQL로 전표 계열 테이블을 쓰는 지점(`UPDATE quotations …`·`UPDATE ONLY public.proforma_invoice_lines …`·
    `INSERT INTO "bank_accounts"`·f-string의 `UPDATE {table}` 동적 표 이름) — ORM 통로를 우회하는 길."""
    return sorted(
        line
        for line, text in _sql_texts(tree)
        if _SQL_WRITE.search(text) or _SQL_DYNAMIC_TABLE.search(text)
    )


def all_protected_sites() -> dict[tuple[str, str, str], list[int]]:
    """앱 전체의 보호 열 쓰기 지점 — (파일, 함수, 열) → 줄들."""
    found: dict[tuple[str, str, str], list[int]] = {}
    for rel, tree in app_sources().items():
        for line, function, column, _how in protected_write_sites(
            rel, tree, doc_scope=is_doc_scope(rel, tree)
        ):
            found.setdefault((rel, function, column), []).append(line)
    return found


def _doc_module_sources() -> dict[str, ast.Module]:
    return {rel: tree for rel, tree in app_sources().items() if module_of(rel) in DOC_MODULES}


def test_the_scan_is_not_vacuous() -> None:
    """스캔 대상 파일이 실제로 있고 전이 통로가 status를 실제로 대입한다"""
    sources = _doc_module_sources()
    assert {"modules/trade_docs/transition.py", "modules/quotations/service.py"} <= set(sources)
    assert attribute_assignments(sources[TRANSITION], "status"), (
        "transition.py가 status를 대입하지 않는다"
    )


def test_protected_columns_are_written_only_at_allowed_sites() -> None:
    """상태·동결 시각·합계·번호는 허용 지점(파일:함수) 밖에서 쓰지 않는다 — 속성 대입·튜플 언패킹·setattr(리터럴·동적)·
    생성자 키워드(`**dict` 포함)·`update(전표).values(키워드·dict·**dict)`, 전표 모듈 밖(handover 등)까지 (B9 #1)"""
    sites = all_protected_sites()
    offenders = sorted(
        f"{rel}:{fn} {column} @{lines}"
        for (rel, fn, column), lines in sites.items()
        if (rel, fn, column) not in ALLOWED_SITES
    )
    assert offenders == [], f"통로 밖 쓰기: {offenders}"
    stale = sorted(ALLOWED_SITES - set(sites))
    assert stale == [], f"쓰이지 않는 허용 항목(목록에서 제거): {stale}"


def test_raw_sql_never_writes_document_tables() -> None:
    """문자열 SQL(`UPDATE quotations …`)로 ORM 통로를 우회하는 길 0건"""
    offenders = {
        rel: raw_sql_doc_writes(tree)
        for rel, tree in app_sources().items()
        if raw_sql_doc_writes(tree)
    }
    assert offenders == {}


def test_the_dynamic_write_allowlist_entries_are_bounded_by_whitelists() -> None:
    """허용 목록의 동적 쓰기(UNKNOWN)가 실제로는 좁은 화이트리스트를 거친다 — 보호 열이 그 안에 들어갈 수 없다"""
    from app.modules.handover.targets import ASSIGNMENT_TARGETS
    from app.modules.quotations.service import _FREE_FIELDS, CONTENT_REQUEST_FIELDS
    from app.modules.trade_docs.incoterms import EMPTY_INCOTERM_COLUMNS
    from app.modules.trade_docs.payment_terms import EMPTY_TERMS_COLUMNS, build_payment_terms

    assert not {t.column.key for t in ASSIGNMENT_TARGETS} & PROTECTED  # 담당 이관이 쓰는 열
    assert not (set(_FREE_FIELDS) | CONTENT_REQUEST_FIELDS) & PROTECTED  # 헤더 편집 요청 필드
    assert not set(EMPTY_TERMS_COLUMNS) & PROTECTED and not set(EMPTY_INCOTERM_COLUMNS) & PROTECTED
    terms = build_payment_terms({"payment_type": "LC"})
    assert terms is not None and not set(terms.columns()) & PROTECTED
    # _header_columns가 cols에 넣는 키는 전부 리터럴(동적 키 0)이고 보호 열이 아니다
    tree = app_sources()[QT_SERVICE]
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_header_columns"
    )
    keys: list[ast.expr] = [
        node.slice
        for node in ast.walk(function)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "cols"
    ]
    assert keys and all(isinstance(k, ast.Constant) for k in keys), "cols에 동적 키가 들어간다"
    assert not {k.value for k in keys if isinstance(k, ast.Constant)} & PROTECTED
    # 개정 초안의 헤더 dict 리터럴도 보호 열을 싣지 않는다
    lifecycle = app_sources()["modules/trade_chain/lifecycle.py"]
    literals = [
        n.value
        for n in ast.walk(lifecycle)
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "header"
        and isinstance(n.value, ast.Dict)
    ]
    assert literals and not _dict_keys(literals[0]) & (PROTECTED | {UNKNOWN})
    # SO 편집 — `_header_columns`의 cols 키도 전부 리터럴이고 보호 열이 아니며, 요청 필드 집합도 보호 열을 싣지 않는다
    from app.modules.sales_orders.service import _FREE_FIELDS as SO_FREE_FIELDS
    from app.modules.sales_orders.service import CONTENT_REQUEST_FIELDS as SO_CONTENT_FIELDS

    assert not (set(SO_FREE_FIELDS) | SO_CONTENT_FIELDS) & PROTECTED
    so_function = next(
        n
        for n in app_sources()[SO_SERVICE].body
        if isinstance(n, ast.FunctionDef) and n.name == "_header_columns"
    )
    so_keys: list[ast.expr] = [
        node.slice
        for node in ast.walk(so_function)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "cols"
    ]
    assert so_keys and all(isinstance(k, ast.Constant) for k in so_keys)
    assert not {k.value for k in so_keys if isinstance(k, ast.Constant)} & PROTECTED


def test_totals_and_numbers_have_a_single_creator_per_document() -> None:
    """생성자의 total_amount=·doc_number=는 전표별 생성 착지 한 곳씩(QT insert_draft·PI insert_issued·SO create_received_sales_order) · 헤더 합계는 recompute_total의 setattr로만 오른다"""
    sites = all_protected_sites()
    creators = {
        (QT_SERVICE, "insert_draft"),
        (PI_SERVICE, "insert_issued"),
        (SO_SERVICE, "create_received_sales_order"),
    }
    for column in ("total_amount", "doc_number"):
        assert {k[:2] for k in sites if k[2] == column} == creators, column
        for creator in creators:
            assert len(sites[(*creator, column)]) == 1
    editing = app_sources()["modules/trade_docs/editing.py"]
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setattr"
        for n in ast.walk(editing)
    ), "recompute_total이 합계를 setattr로 쓰지 않는다"
    source = app_sources()[TRANSITION]
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr"
        for node in ast.walk(source)
    ), "transition.py가 동결 시각을 setattr로 쓰지 않는다"


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
    assert sorted(users) == sorted([QT_SERVICE, PI_SERVICE, SO_SERVICE])


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


def _calls_in_source_order(function: ast.FunctionDef) -> list[str]:
    """호출 이름을 **소스 위치 순**으로(ast.walk의 BFS 순서에 기대지 않는다)."""
    calls = [c for c in ast.walk(function) if isinstance(c, ast.Call)]
    calls.sort(key=lambda c: (c.lineno, c.col_offset))
    return [
        c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
        for c in calls
    ]


def test_line_writes_go_through_the_editable_guard() -> None:
    """라인 쓰기 3함수(add·update·remove)는 assert_editable을 호출하고 헤더를 lock_document로 먼저 잠근다 (B9 #7)"""
    for service in (QT_SERVICE, SO_SERVICE):
        tree = app_sources()[service]
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("add_line", "update_line", "remove_line"):
            calls = _calls_in_source_order(functions[name])
            assert "assert_editable" in calls and "lock_document" in calls, (service, name)
            assert calls.index("lock_document") < calls.index("assert_editable"), (
                f"{service}:{name}: 잠금이 먼저"
            )


def test_the_lock_order_of_a_line_write_is_checked_in_source_order() -> None:
    """공회전 방지 — 중첩 호출이 있어도 소스 위치 순으로 잠금이 먼저인지 가려낸다"""
    good = ast.parse("def f():\n    g(lock_document(a))\n    assert_editable(x)\n").body[0]
    bad = ast.parse(
        "def f():\n    assert_editable(lock_document_later(x))\n    lock_document(a)\n"
    ).body[0]
    assert isinstance(good, ast.FunctionDef) and isinstance(bad, ast.FunctionDef)
    order_good = _calls_in_source_order(good)
    order_bad = _calls_in_source_order(bad)
    assert order_good.index("lock_document") < order_good.index("assert_editable")
    assert order_bad.index("assert_editable") < order_bad.index("lock_document")


def test_no_http_delete_on_the_document_itself() -> None:
    """전표 자체의 DELETE 엔드포인트는 없다 — 폐기는 CANCELLED 전이뿐(라인 제외 DELETE만 있다)"""
    from app.main import app

    deletes = {
        path
        for path, operations in app.openapi()["paths"].items()
        if "delete" in operations and "/quotations" in path
    }
    assert deletes == {"/api/v1/quotations/{qt_id}/lines/{line_id}"}
    # PI에는 어떤 DELETE도 없다(라인 편집도 없다 — 생성=발행=동결)
    assert not {
        path
        for path, operations in app.openapi()["paths"].items()
        if "delete" in operations and "/proforma-invoices" in path
    }
    # SO는 라인 제외 DELETE만 있다(폐기=취소 전이, 번호는 남는다)
    assert {
        path
        for path, operations in app.openapi()["paths"].items()
        if "delete" in operations and "/sales-orders" in path
    } == {"/api/v1/sales-orders/{so_id}/lines/{line_id}"}


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


def _sites(
    source: str, *, rel: str = "modules/other/x.py", doc_scope: bool = False
) -> set[tuple[str, str, str]]:
    tree = parse_source(source)
    return {
        (fn, col, how) for _l, fn, col, how in protected_write_sites(rel, tree, doc_scope=doc_scope)
    }


def test_the_protected_write_scan_catches_every_bypass_shape() -> None:
    """양성 표본 — 우회 형태마다 위반 소스가 실제로 잡힌다(스캔 공회전 방지)"""
    assert ("f", "status", "attr") in _sites("def f(d):\n    d.status = 'X'\n", doc_scope=True)
    assert ("f", "status", "attr") in _sites(
        "def f(a, d):\n    a, d.status = 1, 'X'\n", doc_scope=True
    )  # 튜플 언패킹
    assert ("f", "total_amount", "attr") in _sites(
        "def f(d):\n    d.total_amount += 1\n", doc_scope=True
    )
    assert ("f", "status", "setattr") in _sites(
        "def f(d):\n    setattr(d, 'status', 'X')\n", doc_scope=True
    )
    assert ("f", UNKNOWN, "setattr") in _sites(
        "def f(d, n):\n    setattr(d, n, 'X')\n", doc_scope=True
    )  # 동적 setattr
    assert ("f", "frozen_at", "ctor") in _sites(
        "def f():\n    Quotation(frozen_at=now)\n"
    )  # 범위 밖 파일도
    assert ("f", UNKNOWN, "ctor") in _sites("def f(p):\n    Quotation(**p)\n")
    assert ("f", "status", "ctor") in _sites("def f():\n    Quotation(**{'status': 'X'})\n")
    assert ("f", "total_amount", "values") in _sites(
        "def f():\n    session.execute(update(Quotation).values(total_amount=5))\n"
    )  # DOC_MODEL_NAMES 밖 모듈에서의 합계 쓰기
    assert ("f", "status", "values") in _sites(
        "def f():\n    session.execute(update(Quotation).where(x).values({'status': 'X'}))\n"
    )
    assert ("f", "status", "values") in _sites(
        "def f():\n    session.execute(update(Quotation).values(**{'status': 'X'}))\n"
    )
    assert ("f", UNKNOWN, "values") in _sites(
        "def f(c):\n    session.execute(update(Quotation).values({c: 1}))\n"
    )
    assert ("f", "doc_number", "values") in _sites(
        "def f():\n    session.execute(insert(Quotation.__table__).values(doc_number='x'))\n"
    )


def test_the_protected_write_scan_is_quiet_on_legitimate_code() -> None:
    """정상 표본 — 다른 모델의 같은 이름 열·전표의 자유 열 쓰기·라인 생성은 조용하다"""
    assert (
        _sites("def f(c):\n    c.status = 'X'\n    setattr(c, n, 1)\n") == set()
    )  # 범위 밖 파일의 다른 모델
    assert _sites("def f():\n    update(Certification).values(status='X')\n") == set()
    assert _sites("def f():\n    update(Quotation).values(assignee_id=3)\n") == set()
    assert _sites("def f():\n    QuotationLine(qt_id=1, quantity=2)\n") == set()
    assert _sites("def f(d):\n    d.internal_note = 'x'\n", doc_scope=True) == set()


def test_raw_sql_scan_catches_a_document_write_string() -> None:
    """양성 표본 — 문자열 SQL로 전표·라인·은행 계좌를 쓰면 잡힌다(public. 접두·따옴표·UPDATE ONLY·f-string·동적 표 이름 포함)"""
    hits = [
        "s = 'UPDATE quotations SET status = 1'",
        "s = 'UPDATE quotation_lines SET quantity = 1'",
        "s = 'update public.proforma_invoices set status = 1'",
        "s = 'UPDATE ONLY public.proforma_invoice_lines SET quantity = 1'",
        "s = 'INSERT INTO \"bank_accounts\" (label) VALUES (1)'",
        "s = 'DELETE FROM proforma_invoice_status_log'",
        "s = 'UPDATE\\n  quotations SET status = 1'",
        "s = f'UPDATE {table} SET status = 1'",
        "s = f'UPDATE proforma_invoices SET status = {x}'",
    ]
    for source in hits:
        assert raw_sql_doc_writes(parse_source(source)) == [1], source
    quiet = [
        "s = 'SELECT 1 FROM quotations'",
        "s = 'SELECT * FROM bank_accounts WHERE id = 1'",
        "s = 'UPDATE certifications SET status = 1'",
        "s = f'SELECT count(*) FROM {table}'",
    ]
    for source in quiet:
        assert raw_sql_doc_writes(parse_source(source)) == [], source


def test_the_allowlist_is_narrow_and_scoped_by_function() -> None:
    """허용 목록은 파일:함수 단위 — 같은 파일의 다른 함수가 같은 열을 쓰면 위반으로 잡힌다"""
    sites = _sites("def other(d, n):\n    setattr(d, n, 1)\n", rel=QT_SERVICE, doc_scope=True)
    assert ("other", UNKNOWN, "setattr") in sites
    assert (QT_SERVICE, "other", UNKNOWN) not in ALLOWED_SITES
    assert all(len(entry) == 3 and entry[1] != "<module>" for entry in ALLOWED_SITES)


def test_pi_frozen_columns_are_never_assigned_after_creation() -> None:
    """PI의 CONTENT·ORIGIN 열(은행 스냅샷 6열·valid_until·buyer_address 등)을 속성 대입으로 쓰는 코드가 PI·사슬 모듈에 0건 —
    PI는 생성자(insert_issued의 헤더 dict)로만 값이 들어가고 이후 서비스 통로가 없다(FIELD_POLICY 파생 집합 전수 대조)"""
    from app.modules.trade_docs.policy import frozen_columns

    frozen = frozen_columns("proforma_invoices") - {"status"}  # status는 위 PROTECTED 스캔 몫
    assert {"bank_account_no", "bank_swift_code", "valid_until", "buyer_address"} <= frozen
    checked = 0
    for rel, tree in app_sources().items():
        if module_of(rel) not in ("proforma_invoices", "trade_chain"):
            continue
        checked += 1
        for column in sorted(frozen):
            assert attribute_assignments(tree, column) == [], (rel, column)
    assert checked >= 6  # 공회전 방지
