"""KST '오늘' 고정 — 시험 실행 중 KST 자정을 넘겨도 시험의 '오늘'과 앱의 '오늘'이 같은 날이게 한다.

`from app.core.time import today_kst`로 가져간 모듈은 이름을 각자 쥐고 있어 원본 하나를 바꿔서는 고정되지 않는다 — **import 지점마다**
바꾼다(document_expiry_sweep 선례: PR #50 CI가 KST 23:55~00:04 실행에서 경계 시험을 오판). S3-2 PR-4a 적대 검토 반영 ⑩.

S3-3 PR-1b(R-37·sC C13 ③ — 자율 확정): 종전 `MILESTONE_TODAY_IMPORT_POINTS`(마일스톤·통관·선적 5곳)를 **`TODAY_IMPORT_POINTS`로 이름을
바꾸고 일반화**했다. `TODAY_PINNED_PACKAGES` 아래에서 `today_kst`를 모듈 최상위에서 이름으로 가져오는 모듈은 **전부** 이 목록에 있어야
한다(`tests/architecture/test_today_pin_coverage.py`가 누락을 즉시 지목 — 자정 운에 맡기지 않는다). 원본 `app.core.time.today_kst`도 함께
고정한다 — 함수 안에서 가져오거나(`trade_docs.verify`) 모듈 속성으로 부르는 경로까지 같은 날을 본다.
"""

from __future__ import annotations

from datetime import date
from types import ModuleType

import pytest

from app.core import time as core_time
from app.core.time import today_kst
from app.modules.payments import service as payments_service
from app.modules.trade_chain import (
    chain_ops,
    customs_flow,
    expiry_sweep,
    intake_flow,
    lifecycle,
    milestone_flow,
    milestone_view,
    payment_status,
    reference,
    shipment_flow,
    so_reference,
)
from app.modules.trade_docs import validation as trade_docs_validation
from tests.factories import shipments as shipment_factories

#: '오늘' 고정 커버리지 대상 패키지(`app/modules/<이름>/`) — design-integrated §2.10 목록(S3-3 신규 receivables·lc_terms·
#: commercial_invoices·renditions·letterhead는 아직 없어도 미리 둔다 — 생기는 순간 커버리지 시험이 본다) + 기존 서류 커널 trade_docs
#: (통합 목록 밖 — 서류 날짜 판정이 S3-3 서류 생성기와 맞물려 더 넓게 잡았다, 자율 확정).
TODAY_PINNED_PACKAGES: tuple[str, ...] = (
    "trade_chain",
    "receivables",
    "payments",
    "lc_terms",
    "commercial_invoices",
    "renditions",
    "letterhead",
    "trade_docs",
)

#: `today_kst`를 이름으로 쥔 import 지점 — `TODAY_PINNED_PACKAGES` 아래 최상위 이름 임포트 모듈 전부 + 시험 팩토리의 raw 선적 doc_date.
#: 30개를 넘으면 모듈 속성 접근 전환(패치 1곳)을 검토한다(부채 C-D4).
TODAY_IMPORT_POINTS: tuple[ModuleType, ...] = (
    chain_ops,
    customs_flow,
    expiry_sweep,
    intake_flow,
    lifecycle,
    milestone_flow,
    milestone_view,
    payment_status,
    reference,
    shipment_flow,
    so_reference,
    payments_service,
    trade_docs_validation,
    shipment_factories,
)


def pin_today_kst(monkeypatch: pytest.MonkeyPatch, *extra: ModuleType) -> date:
    """지금의 KST 날짜를 한 번 잡아 원본·앱 import 지점·`extra`(보통 시험 모듈 자신)의 `today_kst`를 그 날로 고정한다."""
    base = today_kst()
    for module in (core_time, *TODAY_IMPORT_POINTS, *extra):
        monkeypatch.setattr(module, "today_kst", lambda: base)
    return base
