"""K·I. 오더 보드 계약 스캔 — 열 매핑 완전성·응답 모델의 원가/여신 필드 부재·요청 스키마·벌크 경계 (S3-1 PR-15a / design-D D6 / ADR-0066).

벌크는 **새 확정 경로가 아니다**: 단일 통로(`confirm_intake`·`confirm_sales_order`·담당 편집)만 부르고 전이·게이트·승인·채번 함수를 직접 부르지 않으며 상태를 대입하지 않는다.
보드는 조회 전용이고 카드에 원가·마진·매입가·여신·게이트 필드가 없다. 스캔은 공회전하지 않도록 합성 위반 코퍼스로 자기검사한다.
"""

from __future__ import annotations

import ast
import typing
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.logging.redaction import is_sensitive_key
from app.main import app
from app.modules.order_board import schemas
from app.modules.order_board.constants import (
    BOARD_STAGE_STATUSES,
    EXCLUDED_INTAKE_STATUSES,
    EXCLUDED_SO_STATUSES,
    INTAKE_STAGE_STATUS,
    STAGE_LABELS_KO,
    STAGE_ORDER,
    BoardStage,
    BulkAction,
)
from app.modules.order_intake.models import IntakeStatus
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import RESERVED, STATUSES
from tests.support.astscan import (
    app_sources,
    called_names,
    imported_modules,
    module_of,
    parse_source,
    referenced_names,
)

pytestmark = pytest.mark.group_k

BOARD_FILES = {rel: tree for rel, tree in app_sources().items() if module_of(rel) == "order_board"}

# ── 열 매핑 완전성 ─────────────────────────────────────────────────────────────


def _mapping_problems(
    so_statuses: set[str],
    mapping: dict[Any, tuple[str, ...]],
    excluded: set[str],
    reserved: set[str],
) -> list[str]:
    mapped = [status for statuses in mapping.values() for status in statuses]
    problems: list[str] = []
    if len(mapped) != len(set(mapped)):
        problems.append("한 SO 상태가 두 열에 매핑됨")
    missing = so_statuses - set(mapped) - excluded - reserved
    if missing:
        problems.append(
            f"보드에도 제외·예약에도 없는 SO 상태(카드가 조용히 사라진다): {sorted(missing)}"
        )
    if set(mapped) & (excluded | reserved):
        problems.append("제외·예약 상태가 열에 매핑됨")
    if set(mapped) - so_statuses:
        problems.append("존재하지 않는 SO 상태가 매핑됨")
    return problems


@pytest.mark.group_i
def test_every_so_status_is_on_the_board_or_explicitly_excluded_or_reserved() -> None:
    """`BOARD_STAGE_STATUSES` 완전성 — 모든 SO 상태 = 매핑됨 ∪ {CANCELLED} ∪ RESERVED(예약 상태가 줄거나 상태가 늘면 실패 — 카드 소실 방지)"""
    assert (
        _mapping_problems(
            set(STATUSES[DocKind.SALES_ORDER]),
            BOARD_STAGE_STATUSES,
            set(EXCLUDED_SO_STATUSES),
            set(RESERVED[DocKind.SALES_ORDER]),
        )
        == []
    )
    assert set(STAGE_ORDER) == set(BoardStage) and len(STAGE_ORDER) == 4
    assert set(BOARD_STAGE_STATUSES) == set(BoardStage) - {BoardStage.INTAKE_PENDING}
    assert set(STAGE_LABELS_KO) == set(BoardStage)


def test_the_mapping_check_is_not_vacuous() -> None:
    """자기검사 — 예약 상태가 줄어 매핑 밖으로 떨어진 상태·중복 매핑·제외 상태 매핑을 실제로 잡는다"""
    statuses = {"RECEIVED", "CONFIRMED", "ON_HOLD", "CANCELLED", "COMPLETED"}
    base = {"A": ("RECEIVED",), "B": ("ON_HOLD",), "C": ("CONFIRMED",)}
    assert _mapping_problems(statuses, base, {"CANCELLED"}, {"COMPLETED"}) == []
    assert _mapping_problems(statuses, base, {"CANCELLED"}, set())  # 예약이 줄었다
    assert _mapping_problems(statuses, {**base, "D": ("RECEIVED",)}, {"CANCELLED"}, {"COMPLETED"})
    assert _mapping_problems(statuses, {**base, "D": ("CANCELLED",)}, {"CANCELLED"}, {"COMPLETED"})


@pytest.mark.group_i
def test_every_intake_status_is_on_the_board_or_explicitly_excluded() -> None:
    """인테이크 열 완전성 — `IntakeStatus` 전체 = 보드 포함({PENDING}) ∪ 보드 제외(확정·거부), 서로소. 상태가 늘면 실패한다(카드 소실·문자열 하드코딩 방지)"""
    included = {INTAKE_STAGE_STATUS}
    assert included | set(EXCLUDED_INTAKE_STATUSES) == {s.value for s in IntakeStatus}
    assert not included & set(EXCLUDED_INTAKE_STATUSES)
    assert IntakeStatus.PENDING.value == INTAKE_STAGE_STATUS


# ── 응답 모델 필드 부재 ─────────────────────────────────────────────────────────

#: 보드·저장 필터 응답 어디에도 나오면 안 되는 이름 조각(카드에 게이트 배지도 없다).
BOARD_FORBIDDEN = (
    "cost",
    "margin",
    "purchase",
    "credit",
    "exposure",
    "limit",
    "gate",
    "list_price",
    "override",
    "approval",
)
#: 벌크 리포트는 확정 거부의 게이트 결과(서버 값)를 싣지만 원가·여신 수치는 없다.
BULK_FORBIDDEN = (
    "cost",
    "margin",
    "purchase",
    "credit",
    "exposure",
    "limit",
    "basis",
    "list_price",
)


def _model_field_names(model: type[BaseModel], seen: set[type] | None = None) -> list[str]:
    seen = seen or set()
    if model in seen:
        return []
    seen.add(model)
    names: list[str] = []
    for name, field in model.model_fields.items():
        names.append(name)
        for inner in _models_in(field.annotation):
            names.extend(_model_field_names(inner, seen))
    return names


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for arg in typing.get_args(annotation) for m in _models_in(arg)]


@pytest.mark.parametrize(
    "model",
    [schemas.OrderBoardOut, schemas.BoardCard, schemas.SavedFilterOut, schemas.BoardFilter],
    ids=lambda m: m.__name__,
)
def test_board_response_models_have_no_cost_margin_credit_or_gate_fields(
    model: type[BaseModel],
) -> None:
    """보드·저장 필터 응답 모델(중첩 포함)에 원가·마진·매입가·여신·게이트 필드가 **존재하지 않는다** — 역할 분기가 아니라 필드 부재(VIEWER 동일)"""
    names = _model_field_names(model)
    assert names
    for name in names:
        assert not is_sensitive_key(name), name
        assert not any(fragment in name.lower() for fragment in BOARD_FORBIDDEN), name


def test_bulk_report_models_carry_no_cost_or_credit_numbers() -> None:
    """벌크 리포트(중첩 포함)에 원가·마진·여신 수치·판정 basis 필드가 없다 — 게이트 항목은 코드·수준·해소 방식·사유 코드·문구뿐"""
    names = _model_field_names(schemas.OrderBoardBulkOut)
    assert {"gate_code", "resolution", "reason_code"} <= set(names)
    for name in names:
        assert not is_sensitive_key(name), name
        assert not any(fragment in name.lower() for fragment in BULK_FORBIDDEN), name


def test_the_card_field_scan_is_not_vacuous() -> None:
    """자기검사 — 원가·여신 필드를 가진 합성 모델은 스캔에 걸린다"""

    class Leaky(BaseModel):
        unit_cost: int
        nested: schemas.BoardCard

    names = _model_field_names(Leaky)
    assert any(is_sensitive_key(n) for n in names)
    assert "ref_label" in names  # 중첩 모델까지 내려간다


# ── 요청 스키마 ───────────────────────────────────────────────────────────────

REQUEST_MODELS = (
    schemas.BoardFilter,
    schemas.BoardItemsQuery,
    schemas.BoardExportQuery,
    schemas.BulkTargetIn,
    schemas.OrderBoardBulkRequest,
    schemas.SavedFilterCreateRequest,
    schemas.SavedFilterUpdateRequest,
)


@pytest.mark.parametrize("model", REQUEST_MODELS, ids=lambda m: m.__name__)
def test_every_board_request_and_query_model_forbids_extra_keys(model: type[BaseModel]) -> None:
    """요청·쿼리 모델은 전부 extra=forbid(모르는 키 422 — 조용한 무시 금지)"""
    assert model.model_config.get("extra") == "forbid"


@pytest.mark.group_i
def test_the_bulk_request_has_exactly_action_targets_and_assignee() -> None:
    """벌크 요청 필드는 정확히 action·targets·assignee_id, 대상은 kind·id·expected_version — override·승인·사유·force·skip 필드가 구조적으로 없다. 액션은 3종(보류·거부·취소 없음)"""
    assert set(schemas.OrderBoardBulkRequest.model_fields) == {"action", "targets", "assignee_id"}
    assert set(schemas.BulkTargetIn.model_fields) == {"kind", "id", "expected_version"}
    assert {a.value for a in BulkAction} == {"CONFIRM_INTAKE", "CONFIRM_SO", "ASSIGN"}


def test_the_board_endpoint_is_a_single_object_and_the_lists_are_pages() -> None:
    """`GET /order-board`는 비-Page 단일 객체(operationId가 `list_`로 시작하지 않고 최상위 배열 아님) · 드릴다운·저장 필터 목록은 Page 봉투"""
    paths = app.openapi()["paths"]
    board = paths["/api/v1/order-board"]["get"]
    assert not board["operationId"].startswith("list_")
    ref = board["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/OrderBoardOut")
    for path in ("/api/v1/order-board/items", "/api/v1/order-board/saved-filters"):
        op = paths[path]["get"]
        assert op["operationId"].startswith("list_")
        assert "Page" in op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]


# ── 벌크 경계(단일 통로만) ─────────────────────────────────────────────────────

#: 벌크·보드가 직접 부르거나 언급하면 안 되는 이름 — 상태 대입·전이·게이트·승인·override·채번·착지의 하위 통로(단일 통로를 우회하는 길).
PROTECTED_NAMES = frozenset(
    {
        "record_transition",
        "record_birth",
        "apply_intake_transition",
        "register_intake",
        "create_received_sales_order",
        "issue_document_number",
        "lock_buyer_for_credit",
        "evaluate_sales_order",
        "evaluate_intake",
        "evaluate_all",
        "clearance",
        "record_evaluation",
        "consume_approval",
        "request_approval",
        "request_credit_approval",
        "decide_approval",
        "void_for_target",
        "grant_override",
        "grant_gate_override",
        "revoke_gate_override",
        "transition_sales_order",
        "update_sales_order",
        "converge_parent",
        "lock_chain",
        "lock_document",
    }
)

#: 확정 진입점은 이 두 함수뿐이고, 담당 편집은 FREE 열 통로 두 개뿐이다.
SINGLE_PATHS = {"confirm_intake", "confirm_sales_order", "update_meta", "update_intake"}

#: 대입하면 안 되는 상태·결정·증적 열(벌크는 단일 통로가 대입한다).
STATE_COLUMNS = frozenset(
    {
        "status",
        "confirmed_at",
        "credit_verdict",
        "credit_approval_id",
        "pi_gate_verdict",
        "sales_order_id",
        "decided_at",
        "decided_by_id",
        "assignee_id",
        "version",
    }
)


def _state_assignments(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in STATE_COLUMNS:
                found.append(target.attr)
    return found


def _protected_mentions(tree: ast.Module) -> set[str]:
    return referenced_names(tree) & PROTECTED_NAMES


@pytest.mark.group_i
def test_the_board_module_never_assigns_state_or_reaches_below_the_single_paths() -> None:
    """order_board 어디에도 상태·결정·증적·담당 열 대입이 없고, 전이·게이트·승인·override·채번·착지의 하위 함수를 언급하지 않는다 — 벌크가 부르는 것은 단일 통로 4개뿐"""
    assert len(BOARD_FILES) >= 6
    for rel, tree in BOARD_FILES.items():
        assert _state_assignments(tree) == [], rel
        assert _protected_mentions(tree) == set(), rel
    bulk_tree = BOARD_FILES["modules/order_board/bulk.py"]
    assert called_names(bulk_tree) >= SINGLE_PATHS


@pytest.mark.group_i
def test_only_bulk_reaches_the_trade_chain_and_only_its_two_confirm_modules() -> None:
    """order_board에서 trade_chain을 임포트하는 파일은 bulk.py 하나이고, 임포트하는 서브모듈은 confirm·intake_flow 둘뿐이다(게이트 오케스트레이터·override·승인 요청 모듈 임포트 0)"""
    importers: dict[str, set[str]] = {}
    for rel, tree in BOARD_FILES.items():
        subs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.modules.trade_chain":
                subs.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.modules.trade_chain."
            ):
                subs.add((node.module or "").rsplit(".", 1)[-1])
        if subs:
            importers[rel] = subs
    assert importers == {"modules/order_board/bulk.py": {"confirm", "intake_flow"}}


def test_the_board_module_is_a_leaf_imported_only_by_the_wiring_points() -> None:
    """오더 보드는 잎(leaf) 모듈 — 다른 모듈은 order_board를 임포트하지 않는다(모델 등록소·API 라우터 배선만). 스케줄러·CLI·아웃박스에서 0"""
    importers = {
        rel
        for rel, tree in app_sources().items()
        if module_of(rel) != "order_board" and "order_board" in imported_modules(tree)
    }
    assert importers == {"registry.py", "api/router.py"}


def test_each_bulk_item_checks_it_is_not_inside_an_open_transaction() -> None:
    """건 실행(`run_item`)은 단일 통로를 부르기 전에 `in_unit_of_work()`를 확인한다(건별 독립 TX의 구조 보증)"""
    tree = BOARD_FILES["modules/order_board/bulk.py"]
    run_item = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_item"
    )
    assert "in_unit_of_work" in called_names(ast.Module(body=run_item.body, type_ignores=[]))


def test_the_boundary_scans_are_not_vacuous() -> None:
    """자기검사 — 상태 대입·하위 통로 언급을 담은 합성 소스는 스캔에 걸린다"""
    bad = parse_source(
        "so.status = 'CONFIRMED'\n"
        "row.assignee_id += 0\n"
        "from app.modules.trade_docs.transition import record_transition\n"
        "record_transition(s, so, 'CONFIRMED')\n"
    )
    assert set(_state_assignments(bad)) == {"status", "assignee_id"}
    assert _protected_mentions(bad) == {"record_transition"}


# ── 임포트 화이트리스트·잠금 키 공간 ─────────────────────────────────────────────

#: order_board가 임포트해도 되는 S3 도메인 모듈 — 단일 통로(확정 2종·담당 편집 2종)와 그 모델·상태 열거·검증 도우미가 사는 곳뿐.
ALLOWED_DOMAIN_MODULES = frozenset({"trade_chain", "order_intake", "sales_orders", "trade_docs"})
#: 도메인이 아닌 공용 기반(행위자·멱등·거래처 마스터 이름).
ALLOWED_KERNEL_MODULES = frozenset({"identity", "idempotency", "partners"})


def _import_violations(trees: dict[str, ast.Module]) -> dict[str, set[str]]:
    allowed = ALLOWED_DOMAIN_MODULES | ALLOWED_KERNEL_MODULES | {"order_board"}
    found: dict[str, set[str]] = {}
    for rel, tree in trees.items():
        extra = imported_modules(tree) - allowed
        if extra:
            found[rel] = extra
    return found


@pytest.mark.group_i
def test_the_board_imports_only_the_four_s3_domain_modules_and_the_kernel() -> None:
    """order_board가 임포트하는 도메인 모듈 ⊆ {trade_chain, order_intake, sales_orders, trade_docs}(+ 공용 identity·idempotency·partners) —
    게이트·승인·여신·override·견적·PI·발주 등 다른 도메인을 직접 부르면 단일 통로 우회 경로가 생긴다"""
    assert _import_violations(BOARD_FILES) == {}
    used = set().union(*(imported_modules(tree) for tree in BOARD_FILES.values()))
    assert used >= ALLOWED_DOMAIN_MODULES  # 화이트리스트가 실제 사용과 맞다(죽은 항목 없음)


def test_the_import_whitelist_scan_is_not_vacuous() -> None:
    """자기검사 — 게이트·승인·여신 모듈을 임포트하는 합성 소스는 위반으로 잡힌다(from/import 양쪽)"""
    bad = parse_source(
        "from app.modules.gates import service as gates\n"
        "import app.modules.approvals.service\n"
        "from app.modules.credit.locking import lock_buyer_for_credit\n"
        "from app.modules.trade_docs.validation import invalid\n"
    )
    assert _import_violations({"modules/order_board/x.py": bad}) == {
        "modules/order_board/x.py": {"gates", "approvals", "credit"}
    }


def _advisory_lock_sites() -> dict[str, list[str]]:
    sites: dict[str, list[str]] = {}
    for rel, tree in app_sources().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "text"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "advisory" in node.args[0].value
            ):
                sites.setdefault(rel, []).append(node.args[0].value)
    return sites


@pytest.mark.group_j
def test_the_saved_filter_lock_namespace_does_not_collide_with_other_advisory_locks() -> None:
    """advisory lock 키 공간 — 앱 전체의 advisory 잠금 호출은 스케줄러(2인자: SCHEDULER_LOCK_KEY, 잡 id)·시드(1인자 bigint)·저장 필터(2인자: 네임스페이스, 사용자)·
    CSV 입구(1인자: 파일 sha256의 hashtextextended 64비트 — LOCK_ORDER (−1), 시드 상수와의 충돌 확률 2^-64) 넷뿐이고, 2인자 네임스페이스끼리 다르다(같으면 사용자 id와 잡 id가 같을 때 서로를 막는다). 새 호출처가 생기면 실패한다(LOCK_ORDER 등재 강제)"""
    from app.modules.order_board.saved_filters import SAVED_FILTER_LOCK_NS
    from app.modules.platform.scheduler import SCHEDULER_LOCK_KEY
    from app.modules.seeds import service as seeds_service

    sites = _advisory_lock_sites()
    assert set(sites) == {
        "modules/platform/scheduler.py",
        "modules/seeds/service.py",
        "modules/order_board/saved_filters.py",
        "modules/order_intake/csv_import.py",
    }
    assert all(":ns" in sql for sql in sites["modules/platform/scheduler.py"])
    assert all(":ns" in sql for sql in sites["modules/order_board/saved_filters.py"])
    assert all("(:key)" in sql for sql in sites["modules/seeds/service.py"])  # 1인자 = 다른 키 공간
    assert all(
        "hashtextextended(:key, 0))" in sql for sql in sites["modules/order_intake/csv_import.py"]
    )  # 1인자
    assert SAVED_FILTER_LOCK_NS != SCHEDULER_LOCK_KEY
    assert SAVED_FILTER_LOCK_NS != seeds_service._APPLY_LOCK_KEY
