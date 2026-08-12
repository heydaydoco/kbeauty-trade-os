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


def test_no_migration_seeds_scheduled_jobs() -> None:
    """마이그레이션 시드 항구 금지(함정 ⑩) — 등록 경로는 앱(CLI)뿐이다"""
    offenders = [
        path.name
        for path in sorted(_MIGRATIONS_DIR.glob("*.py"))
        if "scheduled_jobs" in path.read_text(encoding="utf-8")
        and "op.bulk_insert" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"마이그레이션이 배치를 시드합니다: {offenders}"


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
