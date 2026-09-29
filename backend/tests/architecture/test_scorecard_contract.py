"""K(아키텍처) — 대행사 스코어카드는 계산값이다: 저장 없음·쓰기 없음·발행 없음 (§5.4 / S2-4 PR-1).

★ 매트릭스(ADR-0047)와 같은 계보다 — 집계를 저장하면 자정 뒤·정정 뒤에 거짓이 되고, 그
  거짓을 고치는 배치가 또 하나의 조용한 실패 지점이 된다. 이 파일이 **저장할 수단 자체가
  없음**을 코드가 고정한다:
  ① 스코어카드 성격의 집계 컬럼·테이블이 스키마에 없다.
  ② scorecard.py 소스에 쓰기 호출(add·flush·insert·update…)·이벤트 발행·알림 생성이 0건이다.
  ③ 스코어카드 라우트가 GET뿐이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.db.base import Base
from app.modules.collaboration import router as collaboration_router
from app.modules.collaboration import scorecard
from tests.architecture.test_readiness_contract import _write_calls

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

#: 집계 결과를 담는 성격의 이름 — 테이블·컬럼 어디에도 없어야 한다.
#: `renewal_lead_days`(갱신 리드타임 — 정당한 컬럼)와 겹치지 않게 집계 파생 이름만 잡는다.
_STORED_AGGREGATE = re.compile(
    r"scorecard|agency_score|lead_days_(avg|median)|supplement_(rate|ratio)|avg_lead|median_lead",
    re.IGNORECASE,
)


def test_no_table_or_column_stores_a_scorecard_aggregate() -> None:
    """스키마에 스코어카드 집계 성격의 테이블·컬럼이 없다 — 저장할 곳이 없다"""
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


def test_the_scorecard_module_has_no_write_path() -> None:
    """scorecard.py에 쓰기 호출·발행·알림 생성이 없다 (계산값 미저장이 스펙)"""
    source = Path(scorecard.__file__).read_text("utf-8")
    assert _write_calls(source) == []
    for module in ("app.modules.outbox", "app.modules.notifications", "app.modules.audit"):
        assert module not in source, f"scorecard.py가 {module}을 참조합니다"


def test_the_scan_is_not_idle() -> None:
    """공회전 방지 — 이 파일의 정규식·스캔이 실제 위반을 알아본다"""
    assert _STORED_AGGREGATE.search("agency_scorecard_cache") is not None
    assert _STORED_AGGREGATE.search("lead_days_avg") is not None
    assert _STORED_AGGREGATE.search("partner_code") is None
    assert _STORED_AGGREGATE.search("renewal_lead_days") is None  # 정당한 컬럼은 오탐하지 않는다
    assert _write_calls("session.add(row)") == ["add@1"]


def test_the_scorecard_route_is_get_only() -> None:
    """스코어카드 라우터는 GET뿐이다"""
    methods = {
        method
        for route in collaboration_router.agencies_router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }
    assert methods == {"GET"}
