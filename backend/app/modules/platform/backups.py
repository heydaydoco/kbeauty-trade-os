"""백업 세트 조회·신선도 감시 (§21 / S2-4 PR-3 안건 ⑦ (b) — ADR-0050).

★ 앱은 백업 볼륨을 **읽기 전용**으로만 본다 — 매니페스트(행수·해시·크기·시각)와 리허설 결과 JSON뿐이다.
  산출물 본체(암호화 덤프·파일 묶음)를 읽거나 내려주는 코드는 **만들지 않는다**(전 데이터 유출면).
★ 신선도 감시가 이 기능의 존재 이유다 — "백업이 조용히 안 도는" 실패가 가장 비싸다. 최신 백업이 26시간을
  넘었거나, 복원 리허설이 8일 넘게 없거나, 마지막 리허설이 실패했으면 ADMIN에게 알린다(일자+종류 dedup).
★ 백업 볼륨이 구성되지 않은 환경(dev 기본)에서는 감시가 조용히 건너뛴다(OK) — 그 환경의 알림 소음을 만들지 않는다.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.db.uow import unit_of_work
from app.core.time import KST, utcnow
from app.modules.notifications import service as notifications

#: 백업 세트 디렉터리 이름 — infra/backup/common.sh의 SET_NAME_REGEX와 같다.
SET_NAME = re.compile(r"^kbos-(\d{8}T\d{6}Z)$")
REHEARSAL_NAME = re.compile(r"^rehearsal-(\d{8}T\d{6}Z)\.json$")

#: 신선도 임계 — 일일 백업이 03:00 KST이므로 26시간(2시간 여유)을 넘으면 "안 돈 것"이다.
BACKUP_MAX_AGE = timedelta(hours=26)
#: 복원 리허설은 주 1회(일요일) — 8일을 넘으면 한 번 건너뛴 것이다.
REHEARSAL_MAX_AGE = timedelta(days=8)

#: 신선도 문제 종류 — dedup 키의 일부이자 관리자에게 보이는 코드다.
NO_BACKUP = "NO_BACKUP"
BACKUP_STALE = "BACKUP_STALE"
NO_REHEARSAL = "NO_REHEARSAL"
REHEARSAL_STALE = "REHEARSAL_STALE"
REHEARSAL_FAILED = "REHEARSAL_FAILED"


@dataclass(frozen=True, slots=True)
class BackupView:
    name: str
    created_at: datetime
    database: str | None
    migration_head: str | None
    file_documents: int | None
    table_count: int | None
    total_rows: int | None
    total_bytes: int | None
    #: 매니페스트를 읽지 못했거나 필수 값이 없으면 False — 목록에는 남겨 보인다(숨기면 깨진 세트를 못 본다).
    manifest_ok: bool


@dataclass(frozen=True, slots=True)
class RehearsalView:
    name: str
    finished_at: datetime
    ok: bool
    set_name: str | None
    failures: list[str]


@dataclass(frozen=True, slots=True)
class FreshnessProblem:
    code: str
    message: str


def backup_dir() -> Path | None:
    raw = settings.kbos_backup_dir
    return Path(raw) if raw else None


def _stamp(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def list_backups(directory: Path) -> list[BackupView]:
    """이름 규칙에 맞는 세트를 최신순으로 — 매니페스트 파일 하나만 읽는다."""
    views: list[BackupView] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for entry in entries:
        match = SET_NAME.match(entry.name)
        if match is None or not entry.is_dir():
            continue
        created = _stamp(match.group(1))
        manifest = _read_json(entry / "manifest.json")
        if manifest is None:
            views.append(BackupView(entry.name, created, None, None, None, None, None, None, False))
            continue
        tables = manifest.get("tables")
        artifacts = manifest.get("artifacts")
        rows = (
            sum(v for v in tables.values() if isinstance(v, int))
            if isinstance(tables, dict)
            else None
        )
        size = (
            sum(_as_int(a.get("bytes")) or 0 for a in artifacts.values() if isinstance(a, dict))
            if isinstance(artifacts, dict)
            else None
        )
        head = manifest.get("migration_head")
        stamp_text = manifest.get("created_at_utc")
        with contextlib.suppress(ValueError):  # 파싱 실패면 이름의 시각을 그대로 쓴다
            created = datetime.strptime(str(stamp_text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        views.append(
            BackupView(
                name=entry.name,
                created_at=created,
                database=manifest.get("database")
                if isinstance(manifest.get("database"), str)
                else None,
                migration_head=head if isinstance(head, str) else None,
                file_documents=_as_int(manifest.get("file_documents")),
                table_count=len(tables) if isinstance(tables, dict) else None,
                total_rows=rows,
                total_bytes=size,
                manifest_ok=isinstance(head, str)
                and isinstance(tables, dict)
                and isinstance(artifacts, dict),
            )
        )
    views.sort(key=lambda view: view.name, reverse=True)
    return views


def list_rehearsals(directory: Path) -> list[RehearsalView]:
    views: list[RehearsalView] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for entry in entries:
        match = REHEARSAL_NAME.match(entry.name)
        if match is None or not entry.is_file():
            continue
        data = _read_json(entry)
        finished = _stamp(match.group(1))
        if data is None:
            # 읽을 수 없는 결과 파일은 실패로 취급한다 — 성공으로 보이는 것보다 낫다.
            views.append(
                RehearsalView(entry.name, finished, False, None, ["결과 파일을 읽을 수 없음"])
            )
            continue
        failures = data.get("failures")
        views.append(
            RehearsalView(
                name=entry.name,
                finished_at=finished,
                ok=data.get("ok") is True,
                set_name=data.get("set") if isinstance(data.get("set"), str) else None,
                failures=[str(item) for item in failures] if isinstance(failures, list) else [],
            )
        )
    views.sort(key=lambda view: view.name, reverse=True)
    return views


def freshness_problems(directory: Path, *, now: datetime | None = None) -> list[FreshnessProblem]:
    """신선도 문제 목록 — 없으면 빈 목록(정상)."""
    current = now or utcnow()
    problems: list[FreshnessProblem] = []
    backups = [view for view in list_backups(directory) if view.manifest_ok]
    rehearsals = list_rehearsals(directory)

    if not backups:
        problems.append(
            FreshnessProblem(
                NO_BACKUP,
                "사용할 수 있는 백업 세트가 없습니다(백업이 한 번도 성공하지 않았습니다).",
            )
        )
    else:
        age = current - backups[0].created_at
        if age > BACKUP_MAX_AGE:
            hours = int(age.total_seconds() // 3600)
            problems.append(
                FreshnessProblem(
                    BACKUP_STALE,
                    f"최신 백업이 {hours}시간 전입니다(기준 26시간). 백업 컨테이너가 도는지 확인해 주세요.",
                )
            )

    if rehearsals:
        latest = rehearsals[0]
        if not latest.ok:
            problems.append(
                FreshnessProblem(
                    REHEARSAL_FAILED,
                    "마지막 복원 리허설이 실패했습니다 — 지금 백업으로는 복원이 안 될 수 있습니다: "
                    + "; ".join(latest.failures[:3]),
                )
            )
        elif current - latest.finished_at > REHEARSAL_MAX_AGE:
            days = (current - latest.finished_at).days
            problems.append(
                FreshnessProblem(
                    REHEARSAL_STALE,
                    f"복원 리허설이 {days}일째 없습니다(기준 8일). 주 1회 리허설이 도는지 확인해 주세요.",
                )
            )
    elif backups and current - backups[-1].created_at > REHEARSAL_MAX_AGE:
        problems.append(
            FreshnessProblem(
                NO_REHEARSAL, "복원 리허설이 한 번도 실행되지 않았습니다(백업만 쌓이고 있습니다)."
            )
        )
    return problems


def run_backup_freshness(*, now: datetime | None = None) -> dict[str, int]:
    """백업 신선도 감시 잡 — 문제마다 ADMIN 알림 1건(일자+종류 dedup). 미구성이면 건너뛴다(OK)."""
    directory = backup_dir()
    if directory is None:
        return {"configured": 0, "problems": 0, "alerted": 0}
    current = now or utcnow()
    problems = freshness_problems(directory, now=current)
    alerted = 0
    if problems:
        day = current.astimezone(KST).strftime("%Y%m%d")
        with unit_of_work() as uow:
            for problem in problems:
                created = notifications.notify(
                    uow.session,
                    subject_key=f"backup.freshness:{problem.code}:{day}",
                    title=f"백업 점검 필요 — {problem.code}",
                    body=problem.message,
                    severity="CRITICAL",
                    routing=notifications.Routing.ADMIN,
                )
                alerted += len(created)
    return {"configured": 1, "problems": len(problems), "alerted": alerted}
