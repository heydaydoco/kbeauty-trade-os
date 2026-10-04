"""A·K. OEM 생산 일정·마일스톤 세트 계약 스캔 — 'OEM 4종 ⇔ OEM 생산 PO'(교차 표 규칙)·PO 무수정·원가 비조회·세트 통제
(S3-2 PR-4c / design-B B15 "서비스가 검증하고 교차 테이블 규칙이라 아키텍처 테스트로도 고정한다" / ADR-0024·0078·0079·0085).

■ DB는 `ck_milestones_owner_type_scope`로 'OEM 4종 ⇔ PO 소유'까지만 강제한다 — 'PO가 OEM 생산 발주인가'는 표를 건너는 규칙이라 서비스(`require_oem`)가
  판정한다. 이 파일은 그 판정이 **PO 소유 행을 읽고 쓰는 모든 공개 경로에 걸려 있음**을 소스로 고정한다(경로 하나가 판정을 빼먹으면 실패).
■ T13은 PO를 `FOR SHARE`로 읽기만 한다 — 마일스톤 모듈은 PO ORM 모델(원가 열)을 임포트하지 않고 purchase_orders에 쓰지 않는다(원가 9채널 봉쇄·PO 무수정).
■ 마일스톤 세트 경로는 통제 접두어에 있고(R-14 — 행 누락 = 완전성 시험 실패) 쓰기 행은 관리자만 허용한다.
스캔은 공회전하지 않도록 자기검사로 위반을 실제로 잡는다.
"""

from __future__ import annotations

import ast

import pytest

from app.core.errors.exceptions import AppError
from app.modules.trade_chain.milestone_view import PoOwner, require_oem
from app.modules.trade_docs.constants import (
    OEM_BOARD_ORDER,
    OEM_MILESTONES,
    SHIPMENT_STORED_MILESTONES,
    PoKind,
)
from tests.architecture.authz_matrix import ALLOW, DENY, EXPECTED, GOVERNED_PREFIXES
from tests.support.astscan import app_sources, parse_source, referenced_names

pytestmark = pytest.mark.group_k

FLOW = "modules/trade_chain/milestone_flow.py"
VIEW = "modules/trade_chain/milestone_view.py"
SET_PREFIX = "/api/v1/item-profiles/{profile_id}/milestone-types"

#: PO 소유 마일스톤에 닿는 공개 경로 → 그 경로가 (직접) 불러야 하는 OEM 판정 함수.
OEM_ENTRYPOINTS: dict[tuple[str, str], str] = {
    (VIEW, "get_oem_board"): "require_oem",
    (VIEW, "list_po_changes"): "require_oem",
    (FLOW, "record_oem_milestone_plan"): "_require_oem_writable",
    (FLOW, "record_oem_milestone_actual"): "_require_oem_writable",
}


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _po_touching_functions(tree: ast.Module) -> set[str]:
    """PO를 소유자로 읽는 최상위 함수(`po_owner`·`_lock_oem_owner`를 부르는 함수)."""
    found: set[str] = set()
    for name, function in _functions(tree).items():
        if referenced_names(function) & {"po_owner", "_lock_oem_owner"}:  # type: ignore[arg-type]
            found.add(name)
    return found


@pytest.mark.group_a
def test_every_po_owned_milestone_path_checks_the_oem_kind() -> None:
    """B15 — PO 소유 마일스톤을 읽거나 쓰는 공개 경로 4개(보드·이력·계획·실적)가 OEM 판정을 부르고, 쓰기 판정은 `require_oem`을 거친다.
    PO를 소유자로 읽는 함수는 이 4개 + 내부 보조(잠금·재생·판정 정의)뿐이다 — 새 경로가 판정 없이 PO 소유 행을 만들면 실패"""
    sources = app_sources()
    for (rel, name), guard in OEM_ENTRYPOINTS.items():
        function = _functions(sources[rel])[name]
        assert guard in referenced_names(function), (rel, name, guard)  # type: ignore[arg-type]
    writable = _functions(sources[FLOW])["_require_oem_writable"]
    assert "require_oem" in referenced_names(writable)  # type: ignore[arg-type]
    assert _po_touching_functions(sources[FLOW]) == {
        "_lock_oem_owner",  # PO FOR SHARE + 취소 409(판정은 version 뒤 _require_oem_writable)
        "_oem_replay",  # 재생 = 저장된 change + 지금 보드(쓰기 0 — 원 요청이 판정을 통과했다)
        "record_oem_milestone_plan",
        "record_oem_milestone_actual",
    }
    assert _po_touching_functions(sources[VIEW]) == {"get_oem_board", "list_po_changes"}
    # PO 소유자 값(of_po)을 만드는 곳은 PoOwner.owner 한 곳뿐 — 판정 없이 PO 소유자를 조립하는 우회로 0
    makers = {
        rel
        for rel, tree in sources.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "of_po"
    }
    assert makers == {VIEW}, makers


def test_the_oem_rule_itself() -> None:
    """`require_oem` — OEM 생산 PO만 통과, 일반 구매 PO는 422 OWNER_NOT_OEM. OEM 4종은 선적 저장형과 서로소이고 보드 순서 = 4종"""
    require_oem(PoOwner(1, "ISSUED", PoKind.OEM_PRODUCTION.value))
    with pytest.raises(AppError) as caught:
        require_oem(PoOwner(1, "ISSUED", PoKind.PURCHASE.value))
    assert str(caught.value.code) == "SHIPMENTS.MILESTONE.OWNER_NOT_OEM"
    assert caught.value.status_code == 422
    assert not OEM_MILESTONES & SHIPMENT_STORED_MILESTONES
    assert set(OEM_BOARD_ORDER) == OEM_MILESTONES and len(OEM_BOARD_ORDER) == 4


def test_t13_never_imports_the_po_model_or_writes_purchase_orders() -> None:
    """T13 = PO `FOR SHARE` 읽기뿐 — 마일스톤 모듈 3파일은 PO ORM 모델(원가 열)·purchase_orders 서비스를 임포트하지 않고, purchase_orders 행에
    UPDATE·INSERT를 내지 않는다(Core `table()`로 상태·구분 열만 — 원가 9채널 봉쇄, PO 무수정)"""
    sources = app_sources()
    for rel in (FLOW, VIEW, "modules/trade_chain/milestone_set_flow.py"):
        tree = sources[rel]
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not {m for m in modules if m.startswith("app.modules.purchase_orders")}, rel
        assert "PurchaseOrder" not in referenced_names(tree), rel
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"update", "insert"}
        ]
        assert writes == [], rel
    po_columns = {
        node.args[0].value
        for node in ast.walk(sources[VIEW])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "column"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert not {c for c in po_columns if "cost" in c or "currency" in c or "fx" in c}, po_columns


def test_the_milestone_set_path_is_governed_and_admin_only() -> None:
    """R-14 — 세트 경로는 통제 접두어에 있고(행 누락 = 완전성 실패), 추가·제거 행은 관리자만 ALLOW·조회 행은 전 역할 ALLOW"""
    assert SET_PREFIX in GOVERNED_PREFIXES
    rows = {key: value for key, value in EXPECTED.items() if key[1].startswith(SET_PREFIX)}
    assert set(rows) == {
        ("GET", SET_PREFIX),
        ("POST", SET_PREFIX),
        ("DELETE", f"{SET_PREFIX}/{{link_id}}"),
    }
    for (method, _), roles in rows.items():
        allowed = {role for role, verdict in roles.items() if verdict == ALLOW}
        if method == "GET":
            assert set(roles.values()) == {ALLOW}
        else:
            assert len(allowed) == 1 and next(iter(allowed)).value == "ADMIN"
            assert list(roles.values()).count(DENY) == 4


def test_the_scans_catch_synthetic_violations() -> None:
    """자기검사 — 판정 없는 PO 소유 경로·PO 모델 임포트·PO 쓰기를 실제로 잡는다"""
    leaky = parse_source(
        "from app.modules.purchase_orders.models import PurchaseOrder\n"
        "def sneak(session, po_id):\n    po = po_owner(session, po_id, lock=False)\n"
        "    session.execute(update(PurchaseOrder))\n"
    )
    assert _po_touching_functions(leaky) == {"sneak"}
    assert "require_oem" not in referenced_names(_functions(leaky)["sneak"])  # type: ignore[arg-type]
    assert "PurchaseOrder" in referenced_names(leaky)
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "update"
        for n in ast.walk(leaky)
    )
