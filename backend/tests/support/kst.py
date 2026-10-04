"""KST '오늘' 고정 — 시험 실행 중 KST 자정을 넘겨도 시험의 '오늘'과 앱의 '오늘'이 같은 날이게 한다.

`from app.core.time import today_kst`로 가져간 모듈은 이름을 각자 쥐고 있어 원본 하나를 바꿔서는 고정되지 않는다 — **import 지점마다**
바꾼다(document_expiry_sweep 선례: PR #50 CI가 KST 23:55~00:04 실행에서 경계 시험을 오판). S3-2 PR-4a 적대 검토 반영 ⑩.
"""

from __future__ import annotations

from datetime import date
from types import ModuleType

import pytest

from app.core.time import today_kst
from app.modules.trade_chain import customs_flow, milestone_flow, milestone_view, shipment_flow
from tests.factories import shipments as shipment_factories

#: 마일스톤·통관 흐름이 '오늘'을 읽는 import 지점(실적 미래 판정·통관 날짜·보드 D-N·선적 doc_date·시험 팩토리의 raw 선적 doc_date).
MILESTONE_TODAY_IMPORT_POINTS: tuple[ModuleType, ...] = (
    milestone_flow,
    milestone_view,
    customs_flow,
    shipment_flow,
    shipment_factories,
)


def pin_today_kst(monkeypatch: pytest.MonkeyPatch, *extra: ModuleType) -> date:
    """지금의 KST 날짜를 한 번 잡아 앱 import 지점과 `extra`(보통 시험 모듈 자신)의 `today_kst`를 그 날로 고정한다."""
    base = today_kst()
    for module in (*MILESTONE_TODAY_IMPORT_POINTS, *extra):
        monkeypatch.setattr(module, "today_kst", lambda: base)
    return base
