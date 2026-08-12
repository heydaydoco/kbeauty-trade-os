"""K(아키텍처) — 알림 생성 코어의 단일 통로 (판정 조건 B / ADR-07 / §17.1).

★ 이 파일이 고정하는 것:
  ① **알림을 만드는 통로는 notifications/service.py의 notify() 하나뿐이다.**
    스캔(PR-2)과 디스패처가 각자 Alert를 만들면 dedup 키 규약과 수신자 규칙이
    두 벌이 되고, 그 순간 "중복 발송 0"과 "전사 폭포 0건"이 한쪽에서만 성립한다.
    통로가 하나라는 것은 규약이 하나라는 뜻이다.
  ② **디스패처는 트랜잭션 안에서 외부를 부르지 않는다**(§17.1). 이번 세션의
    발송은 alerts INSERT뿐이고, 외부 커넥터가 서는 S6-3에서도 호출은 커밋
    뒤여야 한다 — 지금 그 부재를 고정해 두면 나중에 어기기 어려워진다.
  ③ **수신자 결정 열거가 3값**이다 — 조용히 네 번째 경로가 생기지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.notifications import models as notification_models
from app.modules.notifications.service import Routing

pytestmark = pytest.mark.group_k

_MODULE_DIR = Path(notification_models.__file__).resolve().parent
_APP_DIR = _MODULE_DIR.parent.parent

#: `Alert(` 생성자 호출. 클래스 **정의**(`class Alert(...)`)는 제외한다 —
#: 모델 선언까지 위반으로 잡으면 스캔이 정의 파일에서 늘 빨개진다.
#: `AlertRule(`은 애초에 걸리지 않는다(`Alert` 다음이 여는 괄호여야 한다).
_ALERT_CONSTRUCTION = re.compile(r"(?<!class )\bAlert\s*\(")


def _python_sources(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_alerts_are_created_only_by_the_notification_core() -> None:
    """app 어디에도 Alert를 직접 만드는 코드가 없다 — 통로는 notify() 하나"""
    offenders: list[str] = []
    for path in _python_sources(_APP_DIR):
        if path == _MODULE_DIR / "service.py":
            continue
        source = path.read_text(encoding="utf-8")
        if _ALERT_CONSTRUCTION.search(source) or "pg_insert(Alert)" in source:
            offenders.append(str(path.relative_to(_APP_DIR)))
    assert offenders == [], f"알림 생성이 코어 밖에 있습니다: {offenders}"


def test_the_core_scan_is_not_idle() -> None:
    """공회전 방지 — 스캔 패턴이 실제 생성 코드를 알아본다"""
    core = (_MODULE_DIR / "service.py").read_text(encoding="utf-8")
    assert "pg_insert(Alert)" in core
    assert _ALERT_CONSTRUCTION.search("Alert(recipient_user_id=1)") is not None
    assert _ALERT_CONSTRUCTION.search("AlertRule(code='x')") is None
    assert _ALERT_CONSTRUCTION.search("class Alert(PkMixin, Base):") is None


def test_the_dispatcher_makes_no_external_calls() -> None:
    """디스패처 안에 HTTP·메일 호출이 없다 (§17.1 — 외부는 커밋 뒤에만)"""
    source = (_MODULE_DIR / "dispatcher.py").read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "urllib", "smtplib", "aiohttp"):
        assert forbidden not in source, forbidden


def test_routing_modes_are_exactly_three() -> None:
    """수신자 결정 경로 3값 — 기일 폴백·이벤트 구독·운영 알림"""
    assert {mode.value for mode in Routing} == {"DEADLINE", "EVENT", "ADMIN"}


def test_channel_kinds_stay_a_closed_enumeration() -> None:
    """채널 종류는 코드 고정 열거다 — 행이 아니라 코드가 늘어야 새 채널이 생긴다"""
    assert notification_models.NOTIFICATION_CHANNEL_KINDS == (
        "IN_APP",
        "SLACK_WEBHOOK",
        "TELEGRAM",
        "EMAIL",
        "NOTION",
    )
