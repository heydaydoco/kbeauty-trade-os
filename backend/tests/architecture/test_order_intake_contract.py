"""K·I·G. 오더 인테이크 구조 계약 — 상태 기계 총수·상태 대입 단일 통로·불변 열·착지/확정 통로·자동 확정 부재·스키마 우회 표면 (S3-1 PR-13a / ADR-0071 / design-D D1·D2·D5-④).

스캔 테스트는 공회전하기 쉽다 — 각 스캔은 (a) 실제로 뭔가 찾았는지 (b) 위반 코퍼스를 넣으면 잡는지를 함께 검사한다.
"""

from __future__ import annotations

import ast
import inspect
from itertools import permutations
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.db.table_policy import MUTABLE_TABLES
from app.modules.handover.targets import ASSIGNMENT_TARGETS
from app.modules.order_intake import machine, schemas
from app.modules.order_intake import service as intake_service
from app.modules.order_intake.models import (
    IMMUTABLE_COLUMNS,
    STATUSES,
    OrderIntake,
)
from tests.support.astscan import app_sources, imported_modules, module_of, parse_source

pytestmark = pytest.mark.group_k

INTAKE_FILES = {rel for rel in app_sources() if module_of(rel) == "order_intake"} | {
    "modules/trade_chain/intake_flow.py"
}
#: 상태·결정 열 — 대입은 `apply_intake_transition` 한 곳뿐이다.
DECISION_ATTRS = frozenset(
    {"status", "decided_at", "decided_by_id", "reject_reason", "sales_order_id"}
)


# ── 상태 기계 총수 (K) ─────────────────────────────────────────────────────────


def test_the_transition_table_has_exactly_two_allowed_and_four_refused_directions() -> None:
    """3상태의 서로 다른 방향 쌍 6 = 허용 2(PENDING→CONFIRMED·PENDING→REJECTED) + 미허용 4 — 종결 2태 탈출 0, 자동 전이 0"""
    pairs = list(permutations(STATUSES, 2))
    assert len(pairs) == 6
    allowed = [p for p in pairs if machine.is_allowed(*p)]
    refused = [p for p in pairs if not machine.is_allowed(*p)]
    assert (len(allowed), len(refused)) == machine.EXPECTED_COUNTS == (2, 4)
    assert set(allowed) == {("PENDING", "CONFIRMED"), ("PENDING", "REJECTED")}
    assert not any(src in machine.TERMINAL_STATUSES for src, _ in allowed)  # 종결 탈출 0
    assert {"CONFIRMED", "REJECTED"} == machine.TERMINAL_STATUSES


class _Sess:
    """`apply_intake_transition`이 쓰는 세션 표면(flush·add)만 흉내 — 전이 표 전수를 DB 없이 시험한다."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def flush(self) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)


@pytest.mark.parametrize(("src", "dst"), list(permutations(STATUSES, 2)))
def test_every_direction_is_exercised_through_the_single_channel(src: str, dst: str) -> None:
    """6방향 전수 — 허용 2는 결정 열이 채워지고 이벤트가 나가며, 미허용 4는 409 NOT_PENDING(상태 불변)"""
    from app.core.errors.exceptions import AppError

    intake = OrderIntake(
        source_kind="MANUAL",
        buyer_partner_id=1,
        assignee_id=1,
        buyer_po_no="P",
        buyer_po_no_key="P",
        currency="USD",
    )
    intake.id = 7
    intake.status = src
    session = _Sess()
    kwargs: dict[str, Any] = (
        {"sales_order_id": 5}
        if dst == "CONFIRMED"
        else {"reason": "사유 다섯자 이상"}
        if dst == "REJECTED"
        else {}
    )
    if machine.is_allowed(src, dst):
        machine.apply_intake_transition(session, intake, dst, actor_id=3, **kwargs)  # type: ignore[arg-type]
        assert intake.status == dst and intake.decided_by_id == 3 and intake.decided_at is not None
        assert len(session.added) == 1  # status_changed 이벤트 1건
        payload = session.added[0].payload
        assert set(payload) == set(machine.STATUS_PAYLOAD_KEYS) and payload["from_status"] == src
    else:
        with pytest.raises(AppError) as caught:
            machine.apply_intake_transition(session, intake, dst, actor_id=3, **kwargs)  # type: ignore[arg-type]
        assert caught.value.code.value == "ORDER_INTAKE.STATE.NOT_PENDING"
        assert intake.status == src and intake.decided_at is None and not session.added


# ── 상태 대입 단일 통로 (층2 AST) ───────────────────────────────────────────────


def _assigned(tree: ast.Module) -> list[tuple[str, int, str]]:
    """(속성, 줄, 포함 함수) — 속성 대입·증강 대입·`setattr(x, '속성', …)`·튜플 언패킹 대입을 모두 본다."""
    found: list[tuple[str, int, str]] = []

    def enclosing(line: int) -> str:
        best = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.lineno <= line <= (
                node.end_lineno or node.lineno
            ):
                best = node.name
        return best

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store):
                    found.append((sub.attr, sub.lineno, enclosing(sub.lineno)))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.append((node.args[1].value, node.lineno, enclosing(node.lineno)))
    return found


def _constructor_keywords(tree: ast.Module, class_name: str) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            )
            if name == class_name:
                for kw in node.keywords:
                    if kw.arg:
                        found.append((kw.arg, node.lineno, class_name))
    return found


def test_decision_columns_are_assigned_only_inside_apply_intake_transition() -> None:
    """`status`·`decided_at`·`decided_by_id`·`reject_reason`·`sales_order_id` 대입은 order_intake 패키지·확정 통로 전체에서 `machine.apply_intake_transition` 안뿐이다(튜플·setattr 포함)"""
    hits: list[tuple[str, str, str]] = []
    for rel in sorted(INTAKE_FILES):
        for attr, _line, func in _assigned(app_sources()[rel]):
            if attr in DECISION_ATTRS:
                hits.append((rel, attr, func))
    assert hits, "스캔이 공회전하고 있다"
    assert {(rel, func) for rel, _attr, func in hits} == {
        ("modules/order_intake/machine.py", "apply_intake_transition")
    }, hits


@pytest.mark.group_g
def test_the_intake_constructor_is_called_only_by_register_intake_and_never_with_a_status() -> None:
    """`OrderIntake(...)` 생성은 `register_intake` 한 곳뿐이고 status·결정 열·백링크 키워드를 넘기지 않는다(라우터·파서는 직접 생성 0 — 착지 단일 통로)"""
    callers: dict[str, set[str]] = {}
    for rel, tree in app_sources().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "OrderIntake"
            ):
                callers.setdefault(rel, set()).add("x")
                keywords = {kw.arg for kw in node.keywords}
                assert not keywords & DECISION_ATTRS, (rel, keywords)
    assert set(callers) == {"modules/order_intake/service.py"}
    tree = app_sources()["modules/order_intake/service.py"]
    register = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "register_intake"
    )
    inside = [
        n
        for n in ast.walk(register)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "OrderIntake"
    ]
    assert len(inside) == 1


def test_immutable_columns_are_never_assigned_in_the_intake_code() -> None:
    """불변 열(원본 스냅샷·소스·거래처·통화)은 order_intake 패키지·확정 통로 어디서도 대입하지 않는다 — 생성자 키워드는 `register_intake`에서만"""
    assert set(IMMUTABLE_COLUMNS) >= {
        "extracted_snapshot",
        "buyer_partner_id",
        "currency",
        "source_kind",
    }
    offenders = [
        (rel, attr, line)
        for rel in sorted(INTAKE_FILES)
        for attr, line, _f in _assigned(app_sources()[rel])
        if attr in IMMUTABLE_COLUMNS
    ]
    assert offenders == []
    for rel in sorted(INTAKE_FILES):
        for attr, _line, _cls in _constructor_keywords(app_sources()[rel], "OrderIntake"):
            if attr in IMMUTABLE_COLUMNS:
                assert rel == "modules/order_intake/service.py", (rel, attr)


def test_no_core_update_insert_or_delete_targets_the_intake_tables_except_the_handover() -> None:
    """Core `update()`/`insert()`/`delete()`·원시 SQL이 인테이크 표를 겨냥하는 코드는 앱 전체에 0건 — 담당 이관(handover)은 `update(target.model)` 일반형이고 `assignee_id`만 바꾼다"""
    bad: list[str] = []
    for rel, tree in app_sources().items():
        text = ast.unparse(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"update", "insert", "delete"}
                and any(
                    "OrderIntake" in ast.unparse(a) or "order_intakes" in ast.unparse(a)
                    for a in node.args
                )
            ):
                bad.append(f"{rel}:{node.lineno}")
        if rel != "migrations" and (
            "UPDATE order_intakes" in text or "DELETE FROM order_intakes" in text
        ):
            bad.append(rel)
    assert bad == []
    assert any(
        t.label == "order_intakes" and t.column.key == "assignee_id" for t in ASSIGNMENT_TARGETS
    )
    handover = ast.unparse(app_sources()["modules/handover/service.py"])
    assert (
        "update(target.model)" in handover
        and ".values({target.column.key: to_user_id})" in handover
    )


def test_the_scanners_catch_violation_corpora() -> None:
    """자기검사 — 대입·setattr·튜플 언패킹·생성자 키워드·불변 열 대입을 실제로 잡고, 무관한 코드는 통과시킨다"""
    assert _assigned(parse_source("def f(i):\n    i.status = 'X'\n"))[0][0] == "status"
    assert (
        _assigned(parse_source("def f(i):\n    setattr(i, 'decided_at', 1)\n"))[0][0]
        == "decided_at"
    )
    assert [
        a for a, _l, _f in _assigned(parse_source("def f(i):\n    i.a, i.sales_order_id = 1, 2\n"))
    ] == ["a", "sales_order_id"]
    assert (
        _constructor_keywords(
            parse_source("x = OrderIntake(status='CONFIRMED', currency='USD')\n"), "OrderIntake"
        )[0][0]
        == "status"
    )
    assert _assigned(parse_source("x = 1\nprint(x)\n")) == []


# ── 착지·확정·거부 통로 (G·I) ──────────────────────────────────────────────────


@pytest.mark.group_g
def test_register_intake_has_no_status_parameter_and_no_confirmation_call() -> None:
    """착지 함수 시그니처에 status 인자가 없고(항상 PENDING), 함수 본문에 확정·전이 호출이 없다 — CONFIRMED로의 직행 불가"""
    params = inspect.signature(intake_service.register_intake).parameters
    assert "status" not in params and "sales_order_id" not in params
    assert params["actor"].kind is inspect.Parameter.KEYWORD_ONLY
    tree = app_sources()["modules/order_intake/service.py"]
    register = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "register_intake"
    )
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(register)
        if isinstance(n, ast.Call)
    }
    assert not called & {"apply_intake_transition", "confirm_intake", "create_received_sales_order"}
    assert not {"confirm_intake", "confirm_sales_order", "create_received_sales_order"} & {
        name
        for rel in app_sources()
        if module_of(rel) == "order_intake"
        for name in {getattr(n, "id", getattr(n, "attr", "")) for n in ast.walk(app_sources()[rel])}
    }


@pytest.mark.group_i
def test_confirm_and_reject_signatures_require_a_human_actor_and_a_keyword_only_idempotency_key() -> (
    None
):
    """`confirm_intake`·`reject_intake`는 `actor`(사람 세션)·무기본값 키워드 전용 `idempotency_key`를 요구하고 force·skip·bypass·override 인자가 없다(GC-H1 — 신뢰도 임계값 류가 들어갈 자리가 없다)"""
    from app.modules.trade_chain import intake_flow

    for func in (intake_flow.confirm_intake, intake_service.reject_intake):
        params = inspect.signature(func).parameters
        assert params["actor"].kind is inspect.Parameter.KEYWORD_ONLY
        key = params["idempotency_key"]
        assert key.kind is inspect.Parameter.KEYWORD_ONLY and key.default is inspect.Parameter.empty
        assert not {"force", "skip", "bypass", "override", "auto", "threshold"} & set(params)


@pytest.mark.group_i
def test_the_transition_callers_are_exactly_confirm_and_reject() -> None:
    """`apply_intake_transition(CONFIRMED)` 호출은 `confirm_intake` 1곳, `(REJECTED)`는 `reject_intake` 1곳뿐이다(자동 전이 호출처 0)"""
    calls: list[tuple[str, str, str]] = []
    for rel, tree in app_sources().items():
        if rel == "modules/order_intake/machine.py":
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", getattr(node.func, "id", ""))
                == "apply_intake_transition"
            ):
                target = ast.unparse(node.args[2]) if len(node.args) > 2 else "?"
                enclosing = next(
                    (
                        f.name
                        for f in ast.walk(tree)
                        if isinstance(f, ast.FunctionDef)
                        and f.lineno <= node.lineno <= (f.end_lineno or 0)
                    ),
                    "",
                )
                calls.append((rel, enclosing, target))
    assert sorted(calls) == [
        ("modules/order_intake/service.py", "reject_intake", "machine.REJECTED"),
        ("modules/trade_chain/intake_flow.py", "confirm_intake", "machine.CONFIRMED"),
    ], calls


@pytest.mark.group_i
def test_intake_confirmation_is_requested_only_by_the_trade_chain_router_and_never_by_machinery() -> (
    None
):
    """`confirm_intake`를 언급하는 파일은 정의(intake_flow)와 trade_chain 라우터뿐이다 — 스케줄러·CLI·아웃박스 디스패처·알림·시드·이관·임포트 어디서도 부르지 않는다(자동 확정 부재)"""
    mentioners = {
        rel
        for rel, tree in app_sources().items()
        if any(
            (isinstance(n, ast.FunctionDef) and n.name == "confirm_intake")
            or (isinstance(n, ast.Name) and n.id == "confirm_intake")
            or (isinstance(n, ast.Attribute) and n.attr == "confirm_intake")
            or (isinstance(n, ast.ImportFrom) and any(a.name == "confirm_intake" for a in n.names))
            for n in ast.walk(tree)
        )
    }
    assert mentioners == {"modules/trade_chain/intake_flow.py", "modules/trade_chain/router.py"}
    for rel in ("modules/platform/scheduler.py", "cli.py", "modules/outbox/service.py"):
        assert "intake_flow" not in ast.unparse(app_sources()[rel])
    for rel, tree in app_sources().items():
        if module_of(rel) in {
            "platform",
            "notifications",
            "outbox",
            "worklist",
            "handover",
            "seeds",
            "imports",
        }:
            assert "intake_flow" not in imported_modules(tree) and "intake_flow" not in ast.unparse(
                tree
            ), rel
    for rel in INTAKE_FILES:
        text = ast.unparse(app_sources()[rel]).lower()
        assert not {"auto_confirm", "autoconfirm", "auto_approve"} & set(
            text.replace(".", " ").split()
        )


def test_order_intake_never_imports_trade_chain_and_the_gates_module_stays_domain_free() -> None:
    """order_intake→trade_chain 임포트 0(역방향 금지)·gates는 order_intake를 임포트하지 않는다 — 평가기는 trade_chain이 등록부에 등록한다"""
    for rel, tree in app_sources().items():
        if module_of(rel) == "order_intake":
            assert "trade_chain" not in imported_modules(tree), rel
        if module_of(rel) == "gates":
            assert "order_intake" not in imported_modules(tree), rel


# ── 스키마 우회 표면 (K) ─────────────────────────────────────────────────────────


def _request_models() -> list[type[BaseModel]]:
    return sorted(
        (
            obj
            for _name, obj in vars(schemas).items()
            if isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj.__name__.endswith(("Request", "Edit"))
        ),
        key=lambda m: m.__name__,
    )


def test_every_write_schema_forbids_extras_and_has_no_bypass_fields() -> None:
    """쓰기 스키마 전건이 extra=forbid이고 status·sales_order_id·decided_*·source_*·buyer_po_no_key·extracted_snapshot·라인 sku_id·거래처/통화 변경 필드가 구조적으로 없다"""
    models = _request_models()
    assert len(models) >= 6, [m.__name__ for m in models]  # 공회전 방지
    forbidden = {
        "status",
        "sales_order_id",
        "decided_at",
        "decided_by_id",
        "source_kind",
        "source_sha256",
        "source_group_key",
        "original_filename",
        "buyer_po_no_key",
        "extracted_snapshot",
        "sku_id",
        "reject_reason",
    }
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert not forbidden & set(model.model_fields), (
            model.__name__,
            forbidden & set(model.model_fields),
        )
    assert {"buyer_partner_id", "currency"} & set(
        schemas.IntakeUpdateRequest.model_fields
    ) == set()  # 거래처·통화는 등록 후 불변
    assert "version" in schemas.IntakeUpdateRequest.model_fields  # 낙관 잠금 필수


def test_the_http_surface_has_no_delete_and_only_expected_write_routes() -> None:
    """`/order-intakes`의 쓰기 라우트는 POST 등록·PATCH 편집·POST resolve/reject/confirm 5개뿐이고 DELETE는 없다(폐기=거부, 행 영구 보존)"""
    from app.main import app

    ops = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/v1/order-intakes")
        for method in item
    }
    writes = {op for op in ops if op[0] != "GET"}
    assert writes == {
        ("POST", "/api/v1/order-intakes"),
        ("PATCH", "/api/v1/order-intakes/{intake_id}"),
        ("POST", "/api/v1/order-intakes/{intake_id}/resolve"),
        ("POST", "/api/v1/order-intakes/{intake_id}/reject"),
        ("POST", "/api/v1/order-intakes/{intake_id}/confirm"),
    }
    assert not any(m == "DELETE" for m, _p in ops)
    assert len({op for op in ops if op[0] == "GET"}) == 3  # 목록·상세·게이트


# ── 등재 (K) ─────────────────────────────────────────────────────────────────


def test_tables_are_registered_in_table_policy_registry_and_handover() -> None:
    """MUTABLE_TABLES·모델 등록소·담당 이관 대상(라벨=테이블명)·users FK 분류(결정자=ACTOR_LOG)에 등재돼 있다"""
    import app.registry as registry
    from app.core.db.base import Base
    from app.modules.handover.targets import USER_FK_CLASSIFICATION

    assert {"order_intakes", "order_intake_lines"} <= MUTABLE_TABLES
    assert {"order_intakes", "order_intake_lines"} <= set(Base.metadata.tables)
    assert "order_intake_models" in registry.__all__
    assert USER_FK_CLASSIFICATION[("order_intakes", "decided_by_id")] == "ACTOR_LOG"
    assert any(t.label == "order_intakes" and t.model is OrderIntake for t in ASSIGNMENT_TARGETS)


def test_the_intake_error_codes_are_cataloged_with_the_designed_statuses() -> None:
    """인테이크 에러코드 6종이 3세그먼트·카탈로그(한국어+조치)·설계 상태 코드로 등재돼 있다(중복 PO는 공용 TRADE_DOCS 코드)"""
    from app.core.errors.catalog import spec_for
    from app.core.errors.codes import ErrorCode

    expected = {
        "ORDER_INTAKE.STATE.NOT_PENDING": 409,
        "ORDER_INTAKE.LINE.UNMAPPED_ITEMS": 422,
        "ORDER_INTAKE.LINE.STALE_MAPPING": 409,
        "ORDER_INTAKE.LINE.DUPLICATE_SKU": 422,
        "ORDER_INTAKE.LINE.LIMIT_EXCEEDED": 422,
        "ORDER_INTAKE.GATE.UNRESOLVED": 409,
    }
    live = {
        str(c): spec_for(c).status_code for c in ErrorCode if str(c).startswith("ORDER_INTAKE.")
    }
    assert live == expected
    assert spec_for(ErrorCode.TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO).status_code == 409
