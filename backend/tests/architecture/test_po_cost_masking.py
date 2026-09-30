"""K. PO 원가 마스킹의 **구조** — 필드 부재 스키마 2종·갈림 1곳·9채널 봉쇄가 코드에서 유지된다 (S3-1 PR-8a / ADR-0057 ⑤ · ADR-0024 / design-F F5 (f)).

BOM 선례(test_bom_cost_masking)를 계승한다. e2e(G 그룹, test_purchase_order_cost_masking)가 **실제 응답·로그·이벤트**를 보고, 이 파일은 그 행동이 **구조로 고정**돼
있음을 본다: ① CostHidden 스키마는 원가·통화·가격 계열 필드를 **가질 수 없다**(이름 정규식)·Full을 상속하지 않는다 ② 두 스키마의 차이는 정확히 원가 계열이다
③ PO 엔드포인트는 전부 `response_model=None`(FastAPI Union 재검증이 필드를 지우는 실측 함정) ④ 원가 노출 판정은 라우터 경계 한 곳 ⑤ 역할 목록은 매입가와 **같은 단일 출처**
⑥ 예외·로그 문자열에 원가 이름을 끼워 넣는 코드 없음(AST) ⑦ 이벤트는 커널 통로뿐·audit 미사용 ⑧ 정렬 화이트리스트에 원가 키 없음 ⑨ 다른 전표 응답에 원가 계열 필드 부재.
각 스캔은 위반 코퍼스를 실제로 잡는지 자기검사한다(공회전 방지).
"""

from __future__ import annotations

import ast
import re
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.logging.redaction import is_sensitive_key
from app.core.money import is_money_column_name
from app.main import app
from app.modules.purchase_orders import schemas as po_schemas
from app.modules.purchase_orders.schemas import (
    PoLineCostHiddenOut,
    PoLineOut,
    PoSortKey,
    PurchaseOrderCostHiddenDetail,
    PurchaseOrderCostHiddenSummary,
    PurchaseOrderDetail,
    PurchaseOrderSummary,
)
from tests.support.astscan import app_sources, module_of, parse_source

pytestmark = pytest.mark.group_k

#: 설계 F5(f)가 정한 정규식 — CostHidden 필드명이 걸리면 안 된다.
COST_NAME = re.compile(r"(_cost$|^cost$|^currency$|^price_)")
#: 예외·로그 문자열에 끼워 넣으면 안 되는 이름 조각.
COST_FRAGMENTS = ("cost", "unit_", "price", "amount", "money")
#: 다른 전표(QT·PI·SO·보드·인테이크) 응답에 어느 역할에게도 도입하면 안 되는 이름(마진은 S3-4·S6-2 몫).
OTHER_DOC_BANNED = re.compile(r"cost|margin|purchase|landed", re.IGNORECASE)

HIDDEN_MODELS = (PoLineCostHiddenOut, PurchaseOrderCostHiddenSummary, PurchaseOrderCostHiddenDetail)


def _property_names(model: type[BaseModel]) -> set[str]:
    """스키마의 **모든** 속성 이름(중첩 모델 포함)."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    names.update(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(model.model_json_schema())
    return names


def test_the_hidden_schemas_cannot_carry_cost_currency_or_price_fields() -> None:
    """CostHidden 3종(라인·목록·상세)의 모든 속성(중첩 포함)이 원가 정규식·금액 이름·민감 키에 걸리지 않는다 — 원가 없는 스키마에는 담을 자리가 없다"""
    for model in HIDDEN_MODELS:
        offenders = sorted(
            name
            for name in _property_names(model)
            if COST_NAME.search(name) or is_money_column_name(name) or is_sensitive_key(name)
        )
        assert offenders == [], (model.__name__, offenders)
    extra_hidden = {
        "total_text",
        "unit_cost_text",
        "line_cost_text",
        "minor_units",
        "fx_rate",
        "fx_rate_date",
    }
    for model in HIDDEN_MODELS:
        assert not _property_names(model) & extra_hidden, (
            model.__name__
        )  # 통화를 역추론할 수 있는 표기도 없다


def test_the_hidden_schemas_do_not_inherit_the_full_ones() -> None:
    """CostHidden은 Full을 상속하지 않는다(상속하면 Full에 더한 원가 필드가 조용히 따라온다)"""
    pairs = (
        (PoLineCostHiddenOut, PoLineOut),
        (PurchaseOrderCostHiddenSummary, PurchaseOrderSummary),
        (PurchaseOrderCostHiddenDetail, PurchaseOrderDetail),
    )
    for hidden, full in pairs:
        assert not issubclass(hidden, full) and not issubclass(full, hidden)
        assert hidden.__bases__ == (BaseModel,), hidden.__name__


def test_the_two_schema_pairs_differ_exactly_by_the_cost_family() -> None:
    """두 스키마의 차이는 정확히 원가 계열이다 — 다른 필드까지 사라지면 조회 역할의 업무가 끊기고(ADR-0024 행 단위를 쓰지 않은 이유), 차이가 좁아지면 원가가 샌다"""
    full_only_line = set(PoLineOut.model_fields) - set(PoLineCostHiddenOut.model_fields)
    assert full_only_line == {
        "unit_cost",
        "unit_cost_text",
        "line_cost",
        "line_cost_text",
        "price_basis",
    }
    assert set(PoLineCostHiddenOut.model_fields) <= set(PoLineOut.model_fields)
    cost_family_summary = {"currency", "total_cost", "total_text"}
    assert (
        set(PurchaseOrderSummary.model_fields) - set(PurchaseOrderCostHiddenSummary.model_fields)
        == cost_family_summary
    )
    assert set(PurchaseOrderCostHiddenSummary.model_fields) <= set(
        PurchaseOrderSummary.model_fields
    )
    cost_family_detail = cost_family_summary | {"minor_units", "fx_rate", "fx_rate_date"}
    full_only_detail = set(PurchaseOrderDetail.model_fields) - set(
        PurchaseOrderCostHiddenDetail.model_fields
    )
    assert full_only_detail == cost_family_detail
    # 업무에 필요한 값은 그대로 남는다
    for kept in (
        "status",
        "supplier_name",
        "po_kind",
        "oc_received_on",
        "lines",
        "doc_number",
        "version",
    ):
        assert kept in PurchaseOrderCostHiddenDetail.model_fields, kept
    for kept in ("sku_code", "quantity", "requested_delivery_date"):
        assert kept in PoLineCostHiddenOut.model_fields, kept


def _route_decorators(tree: ast.Module, routers: set[str]) -> list[ast.Call]:
    """`@<router>.<get|post|…>(…)` 데코레이터 호출 — 라우트 목록의 내부 구조(FastAPI 버전마다 바뀐다)가 아니라 **소스**를 본다(test_auth_coverage와 같은 이유)."""
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id in routers
                and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
            ):
                found.append(decorator)
    return found


def _without_response_model_none(calls: list[ast.Call]) -> list[int]:
    lines = []
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        value = keywords.get("response_model", ast.Constant(value="MISSING"))
        if not (isinstance(value, ast.Constant) and value.value is None):
            lines.append(call.lineno)
    return lines


def test_every_po_endpoint_declares_response_model_none() -> None:
    """PO 엔드포인트(생성·미리보기·조회·메타·전이·CSV·이력)는 전부 `response_model=None` — FastAPI가 반환 Union으로 응답 모델을 추론해 한 갈래로 재검증하면 원가 필드가 지워지거나 덮어써진다(BOM 실측)"""
    po_router = _route_decorators(app_sources()["modules/purchase_orders/router.py"], {"router"})
    chain_router = _route_decorators(app_sources()["modules/trade_chain/router.py"], {"po_router"})
    assert (
        len(po_router) == 7 and len(chain_router) == 1
    )  # 공회전 방지 — 목록·CSV·미리보기·생성·상세·메타·이력 + 전이
    assert _without_response_model_none(po_router + chain_router) == []


def test_the_response_model_scan_catches_a_missing_declaration() -> None:
    """자기검사 — response_model을 빠뜨리거나 None이 아닌 값을 준 라우트를 실제로 잡는다"""
    bad = parse_source(
        "@router.get('/a')\ndef a(): ...\n@router.get('/b', response_model=Foo)\ndef b(): ...\n"
        "@router.get('/c', response_model=None)\ndef c(): ...\n"
    )
    calls = _route_decorators(bad, {"router"})
    assert len(calls) == 3 and _without_response_model_none(calls) == [1, 3]


def _callers(dirname: str, needle: str) -> set[str]:
    return {
        rel.split("/")[-1]
        for rel, tree in app_sources().items()
        if module_of(rel) == dirname
        and any(isinstance(n, ast.Call) and _call_name(n) == needle for n in ast.walk(tree))
    }


def _call_name(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")


def test_the_cost_decision_is_made_at_one_response_boundary() -> None:
    """원가 노출 판정(`may_see_po_cost`) 호출은 라우터의 응답 경계(+쓰기 서비스의 방어 가드)뿐이다 — trade_chain(PO 전이)은 판정 함수를 직접 부르지 않고 PO 라우터의 `detail_response`를 쓴다"""
    assert _callers("purchase_orders", "may_see_po_cost") == {"router.py", "service.py"}
    tree = app_sources()["modules/purchase_orders/service.py"]
    definition_users = [
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(
            isinstance(n, ast.Call) and _call_name(n) == "may_see_po_cost" for n in ast.walk(fn)
        )
    ]
    assert definition_users == ["_require_cost_role"]  # 서비스 안의 호출은 방어 가드 한 곳
    assert _callers("trade_chain", "may_see_po_cost") == set()
    assert _callers("trade_chain", "may_see_cost") == set()
    chain_router = app_sources()["modules/trade_chain/router.py"]
    assert any(
        isinstance(n, ast.Call) and _call_name(n) == "purchase_order_response"
        for n in ast.walk(chain_router)
    )


def test_the_masking_rule_reuses_the_single_role_list() -> None:
    """PO 원가는 매입가·BOM 원가와 **같은 역할 목록**(`pricing.may_see_cost`)을 쓴다 — 역할 목록을 다시 정의하거나 역할을 직접 비교하는 곳이 없다(두 곳이 갈리면 한쪽에서 막은 원가가 다른 쪽으로 샌다)"""
    for rel in (
        "modules/purchase_orders/service.py",
        "modules/purchase_orders/schemas.py",
        "modules/purchase_orders/models.py",
    ):
        source = ast.unparse(app_sources()[rel])
        assert "COST_VISIBLE_ROLES" not in source.replace("pricing.may_see_cost", ""), rel
        assert "RoleCode." not in source, rel
    service_source = ast.unparse(app_sources()["modules/purchase_orders/service.py"])
    assert (
        "from app.modules.catalog.pricing import" in service_source
        and "may_see_cost" in service_source
    )


def _fstring_leaks(tree: ast.AST) -> list[tuple[int, str]]:
    """f-string이 **원가 이름이 든 식**을 끼워 넣는 지점 — (줄, 식). 예외 문구·detail·로그 메시지에 원가 값을 싣는 길이다."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                expression = ast.unparse(part.value).lower()
                if any(fragment in expression for fragment in COST_FRAGMENTS):
                    found.append((node.lineno, expression))
    return found


def _log_context_leaks(tree: ast.AST) -> list[tuple[int, str]]:
    """`log_context=`/logger 키워드 값에 원가 이름이 든 식을 싣는 지점."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "log_context" or _call_name(node) in {
                    "debug",
                    "info",
                    "warning",
                    "error",
                    "exception",
                    "critical",
                }:
                    text = ast.unparse(keyword.value).lower()
                    if any(fragment in text for fragment in COST_FRAGMENTS):
                        found.append((node.lineno, text))
    return found


def _compose_leaks(tree: ast.AST) -> list[tuple[int, str]]:
    """f-string 밖의 문자열 합성(`"…" % x`·`"…" + x`·`"…".format(x)`·`str(x)`/`repr(x)`)이 원가 이름이 든 식을 끼우는 지점 — (줄, 식)."""
    found: list[tuple[int, str]] = []

    def hit(node: ast.AST) -> bool:
        return any(fragment in ast.unparse(node).lower() for fragment in COST_FRAGMENTS)

    def is_text(node: ast.AST) -> bool:
        return isinstance(node, ast.JoinedStr) or (
            isinstance(node, ast.Constant) and isinstance(node.value, str)
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod | ast.Add):
            if (is_text(node.left) and hit(node.right)) or (is_text(node.right) and hit(node.left)):
                found.append((node.lineno, ast.unparse(node).lower()))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "format" and is_text(func.value):
                if any(hit(arg) for arg in [*node.args, *(kw.value for kw in node.keywords)]):
                    found.append((node.lineno, ast.unparse(node).lower()))
            elif (
                isinstance(func, ast.Name)
                and func.id in {"str", "repr"}
                and node.args
                and hit(node.args[0])
            ):
                found.append((node.lineno, ast.unparse(node).lower()))
    return found


PO_FILES = tuple(rel for rel in app_sources() if module_of(rel) == "purchase_orders")


def test_no_exception_message_or_log_carries_a_cost_expression() -> None:
    """purchase_orders 모듈과 PO 전이 함수: f-string이 원가 이름(cost·unit_·price·amount·money)이 든 식을 끼우지 않고, `log_context`·로거 인자에도 원가 이름이 없다(이름 기반 로그 마스킹은 값 추론을 막지 못한다 — 값을 애초에 싣지 않는다)"""
    assert len(PO_FILES) >= 4
    for rel in PO_FILES:
        tree = app_sources()[rel]
        assert _fstring_leaks(tree) == [], (rel, _fstring_leaks(tree))
        assert _log_context_leaks(tree) == [], (rel, _log_context_leaks(tree))
        assert _compose_leaks(tree) == [], (rel, _compose_leaks(tree))
    lifecycle = app_sources()["modules/trade_chain/lifecycle.py"]
    function = next(
        n
        for n in lifecycle.body
        if isinstance(n, ast.FunctionDef) and n.name == "transition_purchase_order"
    )
    assert _fstring_leaks(function) == [] and _log_context_leaks(function) == []
    assert _compose_leaks(function) == []


def test_the_leak_scanners_catch_a_synthetic_leak() -> None:
    """자기검사 — 원가 이름을 끼운 f-string·로그 컨텍스트를 실제로 잡고, 정상 문구에는 조용하다(스캔 공회전 방지)"""
    bad = parse_source(
        "def f(unit_cost, line):\n    raise AppError(detail={'x': f'단가 {unit_cost}'})\n"
        "    log.error('x', extra=1, log_context={'v': line.total_cost})\n"
        "    g(f'{line.unit_cost_text}')\n"
    )
    assert len(_fstring_leaks(bad)) == 2 and len(_log_context_leaks(bad)) == 1
    good = parse_source(
        "def f(doc_date, currency, sku):\n    raise AppError(detail={'x': f'{doc_date} {currency}'},"
        " log_context={'sku_id': sku.id, 'currency': currency})\n"
    )
    assert _fstring_leaks(good) == [] and _log_context_leaks(good) == []


def test_the_sort_whitelist_has_no_cost_keys_and_filters_take_no_cost_parameters() -> None:
    """정렬 키 Literal·서비스 정렬 열 사전·라우터 필터 파라미터 어디에도 원가 계열 이름이 없다 — 원가 값으로 정렬·필터하는 길이 없다(ADR-0057 ⑤-1)"""
    from app.modules.purchase_orders import router as po_router
    from app.modules.purchase_orders import service

    keys = set(PoSortKey.__args__)  # type: ignore[attr-defined]
    assert keys == set(service.SORT_COLUMNS) and keys
    assert not {k for k in keys if COST_NAME.search(k) or OTHER_DOC_BANNED.search(k)}
    import inspect

    parameters = set(inspect.signature(po_router._filters).parameters)
    assert parameters and not {
        p for p in parameters if COST_NAME.search(p) or OTHER_DOC_BANNED.search(p)
    }
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/v1/purchase-orders"):
            names = {q.name for q in route.dependant.query_params}  # type: ignore[attr-defined]
            assert not {n for n in names if COST_NAME.search(n) or OTHER_DOC_BANNED.search(n)}, (
                route.path
            )  # type: ignore[attr-defined]


def test_events_go_through_the_kernel_channel_and_nothing_writes_audit_for_po() -> None:
    """이벤트는 커널 통로(record_birth·record_transition)로만 나간다(payload 화이트리스트에 원가·통화 없음) — purchase_orders 모듈과 PO 전이 함수는 outbox를 직접 부르지 않고 audit를 쓰지 않는다(X-24)"""
    from app.modules.trade_docs.transition import PAYLOAD_KEYS

    assert not {k for k in PAYLOAD_KEYS if COST_NAME.search(k) or "amount" in k}
    for rel in PO_FILES:
        tree = app_sources()[rel]
        assert not any(
            isinstance(n, ast.Call) and _call_name(n) == "publish" for n in ast.walk(tree)
        ), rel
        imports = {
            alias.name.split(".")[-1]
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom | ast.Import)
            for alias in n.names
        } | {
            (n.module or "").split(".")[-1] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        }
        assert not imports & {"audit", "outbox", "notifications", "worklist"}, (rel, imports)
    lifecycle = app_sources()["modules/trade_chain/lifecycle.py"]
    function = next(
        n
        for n in lifecycle.body
        if isinstance(n, ast.FunctionDef) and n.name == "transition_purchase_order"
    )
    called = {_call_name(n) for n in ast.walk(function) if isinstance(n, ast.Call)}
    assert not called & {"publish", "notify", "record_audit", "log_event", "audit"}


def test_the_po_money_columns_are_named_cost() -> None:
    """PO 모델의 금액·가격 성격 열은 전부 `_cost` 이름이다 — `_amount`·`price` 이름을 쓰면 로그 마스킹에 안 걸린다(ADR-0057 ④). 이름을 `_amount`로 바꾸면 이 테스트가 실패한다"""
    from app.core.db.base import Base

    for table in ("purchase_orders", "purchase_order_lines"):
        for column in Base.metadata.tables[table].columns:
            name = column.name
            if name == "price_basis":
                continue  # 단가 기준(MASTER/MANUAL) — 금액이 아니라 응답에서 `price_*` 부재 규칙으로 함께 숨긴다
            assert "amount" not in name and "price" not in name, (table, name)
    for name in ("unit_cost", "line_cost", "total_cost"):
        assert is_sensitive_key(name) and is_money_column_name(name), name
    # 양방향 — 이름을 바꾸면 마스킹에서 빠진다는 것을 확인한다(변이 감각)
    assert not is_sensitive_key("unit_amount") and not is_sensitive_key("total_amount")


def _schema_field_names(module: Any) -> set[str]:
    names: set[str] = set()
    for obj in vars(module).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj is not BaseModel
            and obj.__module__
            == module.__name__  # 다른 모듈에서 가져온 모델은 그 모듈의 스캔 몫이다
        ):
            try:
                names |= _property_names(obj)
            except Exception:  # 제네릭 원형(Page[T])은 스키마를 못 만든다 — 자식 모델은 따로 훑는다
                names |= set(obj.model_fields)
    return names


def test_no_other_document_response_schema_has_a_cost_margin_or_purchase_field() -> None:
    """QT·PI·SO·문서 흐름 응답 모델의 필드명에 cost·margin·purchase·landed가 없다 — 어느 역할에도(마진은 S3-4·S6-2 몫). 보드·인테이크 PR은 자기 스키마 모듈을 이 목록에 더한다"""
    from app.modules.proforma_invoices import schemas as pi
    from app.modules.quotations import schemas as qt
    from app.modules.sales_orders import schemas as so
    from app.modules.trade_chain import router as chain_router

    for label, module in (("QT", qt), ("PI", pi), ("SO", so), ("trade_chain", chain_router)):
        names = _schema_field_names(module)
        assert names, label  # 공회전 방지
        assert not {n for n in names if OTHER_DOC_BANNED.search(n)}, (
            label,
            sorted(n for n in names if OTHER_DOC_BANNED.search(n)),
        )
    assert _schema_field_names(po_schemas) & {
        "unit_cost",
        "total_cost",
    }  # 스캔이 PO에서는 실제로 원가 이름을 본다(양성 대조)


def test_the_hidden_scan_would_catch_a_leaky_hidden_schema() -> None:
    """자기검사 — 원가 필드를 가진 가짜 CostHidden 스키마를 넣으면 위 정규식·금액 이름 검사가 실제로 잡는다"""

    class Leaky(BaseModel):
        id: int
        total_cost: int
        currency: str
        price_basis: str

    offenders = {n for n in _property_names(Leaky) if COST_NAME.search(n)}
    assert offenders == {"total_cost", "currency", "price_basis"}


def test_the_compose_scanner_catches_non_fstring_leaks_and_is_quiet_on_clean_code() -> None:
    """양성 표본 — `%`·`+`·`.format`·`str()`/`repr()`로 원가 이름을 문자열에 합성하는 코드를 잡고, 정상 문구(날짜·통화·SKU 코드)에는 조용하다"""
    bad = parse_source(
        "def f(unit_cost, line):\n"
        "    a = '단가 %s' % unit_cost\n"
        "    b = '단가 ' + str(line.unit_cost)\n"
        "    c = '단가 {}'.format(line.total_cost)\n"
        "    d = repr(unit_cost)\n"
    )
    assert len(_compose_leaks(bad)) >= 4
    good = parse_source(
        "def f(doc_date, currency, sku):\n"
        "    a = '기준일 %s' % doc_date\n    b = '통화 ' + currency\n    c = '{} {}'.format(sku.sku_code, currency)\n    d = str(doc_date)\n"
    )
    assert _compose_leaks(good) == []


@pytest.mark.parametrize(
    "raw",
    [
        "7654321.987",
        "-7654321",
        "7,65,4321",
        "7654321e9",
        "76543210000000000000000",
    ],
)
def test_money_parse_errors_never_carry_the_input_value(raw: str) -> None:
    """`parse_minor_amount`의 오류 메시지는 입력 값을 싣지 않는다 — PO가 `str(exc)`로 그 메시지를 422 detail에 쓰므로(원가 입력 센티널이 되돌아오지 않는다)"""
    from app.core.money import parse_minor_amount

    with pytest.raises(ValueError) as caught:
        parse_minor_amount(raw, "USD", field="unit_cost", max_digits=16)
    assert "7654321" not in str(caught.value)
    assert (
        parse_minor_amount("76543.21", "USD", field="unit_cost") == 7654321
    )  # 양성 대조 — 정상 입력은 통과
