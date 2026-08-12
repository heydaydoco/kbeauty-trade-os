"""K(아키텍처) — 배치 레지스트리의 구조 계약 (§15 / 자동화 4금 / 부채 #12).

★ 이 파일이 고정하는 것:
  ① **코드→함수 매핑이 폐쇄 열거다.** 행을 넣는 것만으로 새 동작이 생기면
    scheduled_jobs에 한 줄 적는 행위가 곧 코드 배포이고, 자동화 4금(§15 L3)을
    데이터로 우회하는 통로가 열린다. 잡을 추가하려면 코드가 늘어야 한다.
  ② **scheduled_jobs에 행을 쓰는 코드는 실행기·서비스뿐이다.** 마이그레이션
    시드는 항구 금지다(함정 ⑩ — users FK가 TRUNCATE CASCADE로 PRESERVED_TABLES를
    무력화한다).
  ③ **등록 잡 전건이 4금 비저촉 동작이다** — 지출·법적 판정·대외 발신·장부
    확정을 하는 잡이 레지스트리에 없다는 사실을 이름과 대상으로 확인한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.platform import models as platform_models
from app.modules.platform import scheduler

pytestmark = pytest.mark.group_k

_APP_DIR = Path(platform_models.__file__).resolve().parent.parent.parent
_MIGRATIONS_DIR = _APP_DIR.parent / "migrations" / "versions"

_JOB_INSERT = re.compile(r"ScheduledJob\s*\(")


def test_the_registry_is_a_closed_enumeration() -> None:
    """JOBS_BY_CODE는 JOB_REGISTRY에서만 만들어진다 — 런타임 등록 통로 0"""
    assert set(scheduler.JOBS_BY_CODE) == {spec.code for spec in scheduler.JOB_REGISTRY}
    assert len(scheduler.JOB_REGISTRY) == len(scheduler.JOBS_BY_CODE)


def test_every_registered_job_has_a_callable() -> None:
    for spec in scheduler.JOB_REGISTRY:
        assert callable(spec.run), spec.code


def test_registry_codes_are_stable_identifiers() -> None:
    """코드는 소문자 하이픈 — DB 행과 코드가 이 문자열로 만난다"""
    for spec in scheduler.JOB_REGISTRY:
        assert re.fullmatch(r"[a-z][a-z0-9-]{2,59}", spec.code), spec.code


#: 이 리포의 시드 관용구는 `op.execute("INSERT INTO …")`다(roles·document_types
#: 선례). `op.bulk_insert`만 찾으면 실제 시드를 하나도 못 잡는다.
_SEEDS = re.compile(r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE)

#: 앱 경로로만 채워야 하는 테이블 — users FK(ActorMixin)를 달아 시드하면
#: 테스트 정리의 TRUNCATE CASCADE가 PRESERVED_TABLES를 무력화한다(함정 ⑩).
_NEVER_SEEDED = ("scheduled_jobs", "notification_channels", "webhook_subscriptions")


def test_no_migration_seeds_the_app_owned_tables() -> None:
    """마이그레이션 시드 항구 금지(함정 ⑩) — 등록 경로는 앱(CLI·API)뿐이다"""
    offenders: list[str] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        seeded = {match.lower() for match in _SEEDS.findall(path.read_text(encoding="utf-8"))}
        for table in _NEVER_SEEDED:
            if table in seeded:
                offenders.append(f"{path.name}:{table}")
    assert offenders == [], f"마이그레이션이 앱 소유 테이블을 시드합니다: {offenders}"


def test_the_seed_scan_recognises_this_repos_idiom() -> None:
    """공회전 방지 — 스캔이 리포의 실제 시드 관용구를 알아본다.

    `op.bulk_insert`만 찾던 종전 스캔은 이 리포에서 단 한 건도 잡지 못했다
    (roles·document_types·partners 시드가 전부 op.execute + INSERT INTO다).
    """
    assert _SEEDS.findall("op.execute(\"INSERT INTO roles (code) VALUES ('X')\")") == ["roles"]
    seeded_somewhere = any(
        _SEEDS.search(path.read_text(encoding="utf-8")) for path in _MIGRATIONS_DIR.glob("*.py")
    )
    assert seeded_somewhere, "시드하는 마이그레이션이 하나도 안 잡혔습니다 — 스캔이 헛돕니다"


def test_no_app_code_writes_notification_channel_rows() -> None:
    """채널·구독의 '0행 유지'(판정 요청 5)를 지키는 진짜 장치.

    행수를 세는 통합 테스트는 TRUNCATE 하네스 때문에 늘 0이라 아무것도 보증하지
    못한다 — 보증은 **쓰기 경로의 부재**다. S6-3에서 공급 경로를 열 때 이
    테스트가 먼저 빨개지고, 그 순간이 판정 지점이다.
    """
    offenders: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "models.py":
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?<!class )\b(NotificationChannel|WebhookSubscription)\s*\(", source):
            offenders.append(str(path.relative_to(_APP_DIR)))
    assert offenders == [], f"채널·구독 쓰기 경로가 생겼습니다: {offenders}"


def test_scheduled_jobs_rows_are_written_only_by_the_platform_module() -> None:
    """app 안에서 ScheduledJob 행을 만드는 코드는 실행기 하나뿐"""
    offenders: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name in {"scheduler.py", "models.py"}:
            continue
        if _JOB_INSERT.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_APP_DIR)))
    assert offenders == [], f"배치 등록이 실행기 밖에 있습니다: {offenders}"


def test_the_scan_is_not_idle() -> None:
    source = (_APP_DIR / "modules" / "platform" / "scheduler.py").read_text(encoding="utf-8")
    assert _JOB_INSERT.search(source) is not None


def test_registered_jobs_stay_clear_of_the_four_bans() -> None:
    """등록 잡 전건이 §15 L3 금지 4영역 밖이다.

    지금 도는 것은 ⓐ 달력 파생 상태 수렴(기판정 승인 — ADR-0038·0039)과
    ⓑ 내부 알림 생성뿐이다. 지출 확정·법적 판정·대외 발송·장부 확정을 하는
    잡이 들어오면 이 목록이 먼저 바뀌므로, 그때 판정을 거치게 된다.
    """
    assert {spec.code for spec in scheduler.JOB_REGISTRY} == {
        "certification-sweep",
        "outbox-dispatch",
    }
