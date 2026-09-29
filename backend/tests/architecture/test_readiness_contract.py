"""K(아키텍처) — 준비도 매트릭스는 계산값이다: 저장 없음·쓰기 없음·발행 없음 (§5.3 / DoD).

★ DoD "만료일 변경 → 매트릭스 즉시 반영(**집계 저장 없음 증명**)"의 구조 절반이다.
  e2e가 동작으로(정정 직후 즉시 🔴) 증명하고, 이 파일이 **저장할 수단 자체가 없음**을
  코드가 고정한다:
  ① 집계·신호등 성격의 테이블·컬럼이 스키마에 없다.
  ② readiness 모듈 소스에 쓰기 호출(add·delete·flush·insert·update…)과 이벤트 발행·알림
     생성이 0건이다.
  ③ 라우터가 GET뿐이다.
  ④ 색 표가 상태 머신 11값을 빠짐없이 덮고, 모집합 축에 성분이 없다(조건 A).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core.db.base import Base
from app.modules.certifications.machine import CERTIFICATION_STATUSES
from app.modules.readiness import router as readiness_router
from app.modules.readiness.rules import AXES, COLORS, SCOPE_NOTE, STATUS_COLOR

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

_MODULE_DIR = Path(readiness_router.__file__).resolve().parent

#: 집계 결과를 담는 성격의 이름 — 테이블·컬럼 어디에도 없어야 한다.
_STORED_AGGREGATE = re.compile(
    r"readiness|matrix|traffic|signal|cell_color|market_ready|sellable", re.IGNORECASE
)

#: 세션에 대한 쓰기 호출 — 수신자가 세션일 때만 쓰기다. 이름만 보면 `seen.add(key)`
#: 같은 파이썬 집합 연산이 오탐이 된다.
_SESSION_WRITES = {
    "add",
    "add_all",
    "delete",
    "merge",
    "flush",
    "commit",
    "rollback",
    "bulk_save_objects",
    "bulk_insert_mappings",
}

#: 수신자와 무관하게 쓰기·발행인 이름(SQLAlchemy 표현식 insert/update/delete 포함).
_ANY_WRITES = {"insert", "update", "delete", "publish", "notify"}

#: 원시 SQL 문자열로 쓰는 우회 — 문자열 안의 DML.
_RAW_DML = re.compile(r"INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET", re.IGNORECASE)

_FORBIDDEN_IMPORTS = ("app.modules.outbox", "app.modules.notifications", "app.modules.audit")


def _sources() -> list[Path]:
    return sorted(path for path in _MODULE_DIR.glob("*.py") if path.name != "__init__.py")


def _write_calls(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver = ast.unparse(func.value)
            on_session = receiver == "session" or receiver.endswith(".session")
            if (func.attr in _SESSION_WRITES and on_session) or func.attr in _ANY_WRITES:
                found.append(f"{func.attr}@{node.lineno}")
        elif isinstance(func, ast.Name) and func.id in _ANY_WRITES:
            found.append(f"{func.id}@{node.lineno}")
    if _RAW_DML.search(source):
        found.append("raw-dml")
    return found


def test_no_table_or_column_stores_an_aggregate() -> None:
    """스키마에 집계·신호등 성격의 테이블·컬럼이 없다 — 저장할 곳이 없다"""
    offenders: list[str] = []
    for table in Base.metadata.tables.values():
        if _STORED_AGGREGATE.search(table.name):
            offenders.append(table.name)
        offenders.extend(
            f"{table.name}.{column.name}"
            for column in table.columns
            if _STORED_AGGREGATE.search(column.name)
        )
    assert offenders == [], f"집계를 저장하는 스키마가 생겼습니다: {offenders}"
    assert len(Base.metadata.tables) > 30  # 공회전 방지 — 실제 스키마를 훑었다


def test_the_readiness_module_has_no_write_path() -> None:
    """readiness 모듈에 쓰기 호출·발행·알림 생성이 없다 (계산값 미저장이 스펙)"""
    offenders = {path.name: _write_calls(path.read_text("utf-8")) for path in _sources()}
    offenders = {name: calls for name, calls in offenders.items() if calls}
    assert offenders == {}, f"읽기 전용 모듈에 쓰기 호출: {offenders}"


def test_the_readiness_module_does_not_reach_the_outbox_or_alerts() -> None:
    """이벤트 발행·알림·감사 로그 모듈을 임포트하지 않는다 (S2-1 판정 ⑦ 계보)"""
    for path in _sources():
        source = path.read_text("utf-8")
        for module in _FORBIDDEN_IMPORTS:
            assert module not in source, f"{path.name}이 {module}을 참조합니다"


def test_the_write_scan_is_not_idle() -> None:
    """공회전 방지 — 스캔이 실제 쓰기 코드를 알아본다"""
    assert _write_calls("session.add(row)\nsession.flush()\noutbox.publish(x)") == [
        "add@1",
        "flush@2",
        "publish@3",
    ]
    assert _write_calls("session.execute(select(Sku))") == []
    assert _write_calls("seen.add(key)") == []  # 파이썬 집합 연산은 쓰기가 아니다
    assert _write_calls("uow.session.add(row)") == ["add@1"]
    assert _write_calls("insert(Alert).values(x=1)") == ["insert@1"]
    assert _write_calls('session.execute(text("delete from x"))') == ["raw-dml"]
    assert _STORED_AGGREGATE.search("readiness_cache") is not None
    assert _STORED_AGGREGATE.search("sku_code") is None
    assert len(_sources()) >= 4


def test_the_router_is_get_only() -> None:
    methods = {method for route in readiness_router.router.routes for method in route.methods}  # type: ignore[attr-defined]
    assert methods == {"GET"}


def test_the_color_table_covers_every_status() -> None:
    """상태 11값 전부에 색이 있다 — 빠지면 그 상태의 셀이 KeyError로 죽는다"""
    assert set(STATUS_COLOR) == set(CERTIFICATION_STATUSES)
    assert set(STATUS_COLOR.values()) <= set(COLORS)


def test_the_population_excludes_ingredients_and_says_so() -> None:
    """조건 A — 모집합은 4축이고 성분은 밖이며, 범례 문구가 그 사실을 공개한다"""
    assert AXES == ("SKU", "PRODUCT", "COMPANY", "FACILITY")
    assert "INGREDIENT" not in AXES
    assert "성분" in SCOPE_NOTE


def test_colors_are_the_four_of_the_design() -> None:
    """§5.3 신호등 4색 — 🟢판매가능/🟡진행·임박/🔴미충족/⚪대상외"""
    assert set(COLORS) == {"GREEN", "YELLOW", "RED", "GRAY"}
