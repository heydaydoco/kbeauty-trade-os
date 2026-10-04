"""A·K. OEM 생산 일정·마일스톤 세트 계약 스캔 — 'OEM 4종 ⇔ OEM 생산 PO'(교차 표 규칙)·PO 무수정·원가 비조회·세트 통제
(S3-2 PR-4c / design-B B15 "서비스가 검증하고 교차 테이블 규칙이라 아키텍처 테스트로도 고정한다" / ADR-0024·0078·0079·0085).

■ DB는 `ck_milestones_owner_type_scope`로 'OEM 4종 ⇔ PO 소유'까지만 강제한다 — 'PO가 OEM 생산 발주인가'는 표를 건너는 규칙이라 서비스(`require_oem`)가
  판정한다. 이 파일은 그 판정이 **PO 소유 행을 읽고 쓰는 모든 공개 경로에 걸려 있음**을 소스로 고정한다(경로 하나가 판정을 빼먹으면 실패).
■ T13은 PO를 `FOR SHARE`로 읽기만 한다 — 마일스톤 모듈은 PO ORM 모델(원가 열)을 임포트하지 않고 purchase_orders에 쓰지 않는다(원가 9채널 봉쇄·PO 무수정).
■ 마일스톤 세트 경로는 통제 접두어에 있고(R-14 — 행 누락 = 완전성 시험 실패) 쓰기 행은 관리자만 허용한다.
■ (PR-4c 적대 검토 반영 ⑫) 판정식은 헬퍼(`_po_write_sites`·`_po_owner_sites`)로 뽑고 자기검사가 **같은 헬퍼**를 부른다 — 함수형 `update()`뿐 아니라
  `_PO_OWNERS.update()` 같은 테이블 객체 메서드·문자열 SQL 'UPDATE purchase_orders'·`MilestoneOwner(po_id=…)` 직접 생성·`Milestone.po_id` 비교식까지 잡는다.
스캔은 공회전하지 않도록 자기검사로 위반을 실제로 잡는다.
"""

from __future__ import annotations

import ast
import re

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


SERVICE = "modules/shipments/service.py"
SET_FLOW = "modules/trade_chain/milestone_set_flow.py"
#: 쓰기 문(SQLAlchemy Core 함수·테이블 메서드)의 이름.
_WRITE_VERBS = {"update", "insert", "delete"}
#: purchase_orders를 가리키는 테이블 객체의 관례 이름(모듈이 `table("purchase_orders", …)`로 만든 이름은 스캔이 따로 모은다).
_PO_TABLE_NAMES = {"_PO_OWNERS", "_PURCHASE_ORDERS"}
_PO_SQL = re.compile(
    r"\b(UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+\"?purchase_orders\b", re.IGNORECASE
)


def _po_table_names(tree: ast.Module) -> set[str]:
    """`이름 = table("purchase_orders", …)`로 만든 테이블 객체 이름 + 관례 이름."""
    names = set(_PO_TABLE_NAMES)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "table"
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and node.value.args[0].value == "purchase_orders"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _po_write_sites(tree: ast.Module) -> list[str]:
    """purchase_orders(또는 임의 표)에 쓰는 형태 — ① 함수형 `update()`·`insert()`·`delete()` ② PO 테이블 객체의 `.update()`·`.insert()`·
    `.delete()` ③ 문자열 SQL 'UPDATE/INSERT INTO/DELETE FROM purchase_orders'. 마일스톤 모듈에서는 0이어야 한다(T13 = PO 무수정)."""
    po_tables = _po_table_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _WRITE_VERBS:
                found.append(f"{node.lineno}: {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in _WRITE_VERBS:
                receiver = ast.unparse(func.value)
                if (
                    isinstance(func.value, ast.Name) and func.value.id in po_tables
                ) or "purchase_orders" in receiver:
                    found.append(f"{node.lineno}: {receiver}.{func.attr}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _PO_SQL.search(node.value):
                found.append(f"{node.lineno}: SQL {node.value[:40]!r}")
    return found


def _po_owner_sites(tree: ast.Module) -> list[str]:
    """PO 소유자를 만드는·고르는 형태 — `.of_po`(속성)·`MilestoneOwner(po_id=…)`/`MilestoneOwner(None, x)` 직접 생성·`Milestone.po_id` 참조.
    허용 위치: `of_po`는 판정을 거친 `PoOwner.owner`(milestone_view) 1곳, `Milestone.po_id`는 L1 `MilestoneOwner.clause()`(shipments 서비스) 1곳,
    직접 생성은 0(분류 메서드만)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "of_po":
                found.append("of_po")
            elif (
                node.attr == "po_id"
                and isinstance(node.value, ast.Name)
                and node.value.id == "Milestone"
            ):
                found.append("Milestone.po_id")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "MilestoneOwner" and (
                any(kw.arg == "po_id" for kw in node.keywords) or len(node.args) >= 2
            ):
                found.append("MilestoneOwner(po_id)")
    return found


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
    # PO 소유자를 만드는·고르는 곳은 정해진 2곳뿐 — 판정 없이 PO 소유자를 조립하거나 PO 소유 행을 직접 고르는 우회로 0
    sites = {(rel, site) for rel, tree in sources.items() for site in _po_owner_sites(tree)}
    assert sites == {(VIEW, "of_po"), (SERVICE, "Milestone.po_id")}, sites


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
    for rel in (FLOW, VIEW, SET_FLOW):
        tree = sources[rel]
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not {m for m in modules if m.startswith("app.modules.purchase_orders")}, rel
        assert "PurchaseOrder" not in referenced_names(tree), rel
        assert _po_write_sites(tree) == [], rel
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
    """자기검사 — 본 스캔과 **같은 헬퍼**로: 판정 없는 PO 소유 경로·PO 모델 임포트·쓰기 형태 4종(함수형·테이블 객체 메서드·`table()`로 만든 이름·
    문자열 SQL)·PO 소유자 우회 3종(직접 생성 키워드·위치 인자·`Milestone.po_id`)을 실제로 잡고, 읽기 전용 코드에는 조용하다"""
    leaky = parse_source(
        "from app.modules.purchase_orders.models import PurchaseOrder\n"
        "def sneak(session, po_id):\n    po = po_owner(session, po_id, lock=False)\n"
        "    session.execute(update(PurchaseOrder))\n"
    )
    assert _po_touching_functions(leaky) == {"sneak"}
    assert "require_oem" not in referenced_names(_functions(leaky)["sneak"])  # type: ignore[arg-type]
    assert "PurchaseOrder" in referenced_names(leaky)
    assert _po_write_sites(leaky) == ["4: update()"]
    method = parse_source(
        "def f(session):\n    session.execute(_PO_OWNERS.update().values(status='X'))\n"
    )
    assert _po_write_sites(method) == ["2: _PO_OWNERS.update()"]
    named = parse_source(
        "ORDERS = table('purchase_orders', column('id'))\n"
        "def f(session):\n    session.execute(ORDERS.delete())\n"
    )
    assert _po_write_sites(named) == ["3: ORDERS.delete()"]
    raw = parse_source(
        "def f(session):\n    session.execute(text('update purchase_orders set x = 1'))\n"
    )
    assert len(_po_write_sites(raw)) == 1
    reads = parse_source(
        "def f(session):\n    session.execute(select(_PO_OWNERS.c.id).with_for_update(read=True))\n"
        "    session.execute(text('SELECT id FROM purchase_orders'))\n"
    )
    assert _po_write_sites(reads) == []
    owners = parse_source(
        "def f(po_id):\n    a = MilestoneOwner(po_id=po_id)\n    b = MilestoneOwner(None, po_id)\n"
        "    return select(Milestone).where(Milestone.po_id == po_id)\n"
    )
    assert sorted(_po_owner_sites(owners)) == [
        "Milestone.po_id",
        "MilestoneOwner(po_id)",
        "MilestoneOwner(po_id)",
    ]
    assert _po_owner_sites(parse_source("o = MilestoneOwner(shipment_id=1)\n")) == []
