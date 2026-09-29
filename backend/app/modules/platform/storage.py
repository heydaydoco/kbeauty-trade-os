"""파일 저장소 용량 감시·고아/유실 점검·실물 물리 정리 (§18.4 / S2-3 판정 요청 17 / S2-4 PR-3 안건 ⑧ — ADR-0050).

■ 감시(잡 `storage-monitor`, daily@05:00 KST): files/ 볼륨의 여유율(shutil.disk_usage)·앱 사용량·
  **고아 파일**(DB 행이 없는 실물)·**유실 파일**(실물이 없는 FILE 문서 행). 임계는 데이터다 —
  `alert_rules`(event_type `storage.free_space`)의 `config.warn_pct`·`critical_pct`(기본 20·10). 수신=ADMIN
  (일자+수준 dedup). **고아·유실은 보고만 한다 — 자동 삭제·복구 금지.**
  · 업로드는 실물을 먼저 쓰고 DB 행을 나중에 만든다(§17.1) — 방금 올라온 파일이 고아로 잡히지 않게
    **수정 1시간 이내 파일은 고아로 세지 않는다.**
■ 물리 정리(`purge_files`): 소프트 삭제된 FILE 문서의 실물을 **유예기간(기본 30일) 경과 + 보존기한 없음/경과**
  일 때만 지우고 `documents.purged_at`을 기록한다. **되돌릴 수 없다 → 기본 OFF**(`KBOS_FILE_PURGE_ENABLED`)
  이고 CLI는 기본 dry-run(`--apply`만 실행). 실물 삭제(파일 IO)는 트랜잭션 **밖**에서, 기록·감사는 건별
  트랜잭션에서 한다(§17.1). 삭제 후 기록 전에 죽어도 다음 실행이 "이미 없음"으로 기록을 마무리한다.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db.uow import unit_of_work
from app.core.time import KST, utcnow
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.documents.models import Document
from app.modules.documents.service import storage_root
from app.modules.notifications import service as notifications

FREE_SPACE_EVENT = "storage.free_space"
DEFAULT_WARN_PCT = 20
DEFAULT_CRITICAL_PCT = 10
#: 방금 올라온 실물(DB 행 생성 직전)이 고아로 잡히지 않게 하는 최소 나이.
ORPHAN_MIN_AGE = timedelta(hours=1)
_STORED_NAME = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class StorageReport:
    total_bytes: int
    free_bytes: int
    free_pct: float
    app_bytes: int
    file_count: int
    orphan_files: list[str] = field(default_factory=list)
    missing_documents: list[int] = field(default_factory=list)
    #: OK | WARN | CRITICAL — 여유율 임계 판정.
    level: str = "OK"


def normalize_thresholds(config: Any) -> tuple[int, int]:
    """임계 정상화 — 형이 흐리거나 순서가 뒤집히면(경고 ≤ 위험) 기본값으로 되돌린다(잘못된 규칙이 감시를 끄지 않게)."""
    raw = config if isinstance(config, dict) else {}

    def pick(key: str, default: int) -> int:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 99:
            return value
        return default

    warn, critical = pick("warn_pct", DEFAULT_WARN_PCT), pick("critical_pct", DEFAULT_CRITICAL_PCT)
    if warn <= critical:
        return DEFAULT_WARN_PCT, DEFAULT_CRITICAL_PCT
    return warn, critical


def level_for(free_pct: float, warn: int, critical: int) -> str:
    if free_pct < critical:
        return "CRITICAL"
    if free_pct < warn:
        return "WARN"
    return "OK"


def _thresholds(session: Session) -> tuple[int, int, Any]:
    rules = notifications.matching_rules(session, FREE_SPACE_EVENT)
    rule = rules[0] if rules else None
    warn, critical = normalize_thresholds(rule.config if rule is not None else None)
    return warn, critical, rule


def _disk_usage(root: Path) -> shutil._ntuple_diskusage:
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe)


def scan_storage(*, now: datetime | None = None) -> StorageReport:
    current = now or utcnow()
    root = storage_root()
    on_disk: dict[str, float] = {}
    app_bytes = 0
    if root.is_dir():
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    on_disk[entry.name] = stat.st_mtime
                    app_bytes += stat.st_size
    usage = _disk_usage(root)
    free_pct = usage.free * 100 / usage.total if usage.total else 0.0

    with unit_of_work() as uow:
        session = uow.session
        rows = session.execute(
            select(Document.id, Document.stored_name, Document.purged_at).where(
                Document.storage_kind == "FILE"
            )
        ).all()
        warn, critical, _ = _thresholds(session)
    known = {row.stored_name for row in rows}
    cutoff = current.timestamp() - ORPHAN_MIN_AGE.total_seconds()
    orphans = sorted(
        name
        for name, mtime in on_disk.items()
        if _STORED_NAME.match(name) and name not in known and mtime <= cutoff
    )
    missing = sorted(
        row.id for row in rows if row.purged_at is None and row.stored_name not in on_disk
    )
    return StorageReport(
        total_bytes=usage.total,
        free_bytes=usage.free,
        free_pct=round(free_pct, 2),
        app_bytes=app_bytes,
        file_count=len(on_disk),
        orphan_files=orphans,
        missing_documents=missing,
        level=level_for(free_pct, warn, critical),
    )


# ── 물리 정리 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PurgeCandidate:
    id: int
    stored_name: str
    size_bytes: int


def _eligible(row: Document, *, cutoff: datetime, today: date) -> bool:
    return (
        row.storage_kind == "FILE"
        and row.deleted_at is not None
        and row.deleted_at <= cutoff
        and row.purged_at is None
        and (row.retention_until is None or row.retention_until < today)
    )


def purge_candidates(session: Session, *, cutoff: datetime, today: date) -> list[PurgeCandidate]:
    rows = session.execute(
        select(Document)
        .where(
            Document.storage_kind == "FILE",
            Document.deleted_at.is_not(None),
            Document.deleted_at <= cutoff,
            Document.purged_at.is_(None),
            or_(Document.retention_until.is_(None), Document.retention_until < today),
        )
        .order_by(Document.id)
    ).scalars()
    return [
        PurgeCandidate(id=row.id, stored_name=str(row.stored_name), size_bytes=row.size_bytes or 0)
        for row in rows
    ]


def purge_files(*, apply: bool, now: datetime | None = None) -> dict[str, int]:
    """후보를 계산하고, apply일 때만 실물을 지운다. dry-run은 어떤 것도 바꾸지 않는다."""
    current = now or utcnow()
    grace = max(1, int(settings.kbos_file_purge_grace_days))
    cutoff = current - timedelta(days=grace)
    today = current.astimezone(KST).date()
    with unit_of_work() as uow:
        candidates = purge_candidates(uow.session, cutoff=cutoff, today=today)
    counts = {"candidates": len(candidates), "purged": 0, "failed": 0, "bytes": 0}
    if not apply:
        return counts
    root = storage_root()
    for candidate in candidates:
        try:
            (root / candidate.stored_name).unlink(missing_ok=True)  # 파일 IO는 트랜잭션 밖
            with unit_of_work() as uow:
                row = uow.session.execute(
                    select(Document).where(Document.id == candidate.id).with_for_update()
                ).scalar_one_or_none()
                # 잠금 뒤 재확인 — 그 사이 조건이 바뀌었으면(보존기한 갱신 등) 기록하지 않는다.
                if row is None or not _eligible(row, cutoff=cutoff, today=today):
                    continue
                row.purged_at = utcnow()
                audit.record(
                    uow.session,
                    action=AuditAction.DOCUMENT_FILE_PURGED,
                    entity_type="documents",
                    entity_id=row.id,
                    detail={
                        "stored_name": candidate.stored_name,
                        "size_bytes": candidate.size_bytes,
                        "sha256": row.sha256,
                    },
                )
            counts["purged"] += 1
            counts["bytes"] += candidate.size_bytes
        except Exception:  # 건별 격리 — 한 건 실패가 나머지를 막지 않는다(잡이 FAILED로 올린다)
            counts["failed"] += 1
    return counts


# ── 잡 ───────────────────────────────────────────────────────────────────────


def run_storage_monitor(*, now: datetime | None = None) -> dict[str, int]:
    """용량·고아·유실 점검 → ADMIN 알림 → 물리 정리(설정이 켜져 있을 때만; 꺼져 있으면 후보 수만 보고)."""
    current = now or utcnow()
    report = scan_storage(now=current)
    day = current.astimezone(KST).strftime("%Y%m%d")
    alerted = 0
    with unit_of_work() as uow:
        session = uow.session
        _, _, rule = _thresholds(session)

        def alert(key: str, title: str, body: str, severity: str) -> None:
            nonlocal alerted
            created = notifications.notify(
                session,
                subject_key=f"storage.monitor:{key}:{day}",
                title=title,
                body=body,
                severity=severity,
                rule=rule,
                routing=notifications.Routing.ADMIN,
            )
            alerted += len(created)

        if report.level != "OK":
            alert(
                f"free_space:{report.level}",
                f"파일 저장소 여유 공간 부족 — {report.level}",
                f"여유율 {report.free_pct}%(남은 {report.free_bytes // (1024 * 1024)}MiB). "
                "오래된 삭제 문서의 물리 정리·디스크 증설을 검토해 주세요.",
                "CRITICAL" if report.level == "CRITICAL" else "WARN",
            )
        if report.missing_documents:
            alert(
                "missing",
                f"문서 실물 유실 {len(report.missing_documents)}건",
                "행은 있는데 실물 파일이 없는 문서가 있습니다(문서 id: "
                + ", ".join(str(i) for i in report.missing_documents[:10])
                + "). 백업에서 복구할 수 있는지 확인해 주세요. 자동으로 고치지 않습니다.",
                "CRITICAL",
            )
        if report.orphan_files:
            alert(
                "orphans",
                f"DB 행이 없는 고아 파일 {len(report.orphan_files)}건",
                "저장소에 행이 없는 파일이 있습니다(업로드 중단 등). 자동으로 지우지 않습니다 — 확인 후 정리해 주세요.",
                "INFO",
            )
    purge = purge_files(apply=bool(settings.kbos_file_purge_enabled), now=current)
    if purge["failed"]:
        raise RuntimeError(f"실물 물리 정리 {purge['failed']}건 실패 — 로그를 확인해 주세요.")
    return {
        "free_pct": int(report.free_pct),
        "level": {"OK": 0, "WARN": 1, "CRITICAL": 2}[report.level],
        "orphans": len(report.orphan_files),
        "missing": len(report.missing_documents),
        "purge_enabled": int(bool(settings.kbos_file_purge_enabled)),
        "purge_candidates": purge["candidates"],
        "purged": purge["purged"],
        "alerted": alerted,
    }


def kst_midnight(day: date) -> datetime:
    """CLI --base-date용 — 그 날 KST 정오(날짜 판정이 모호하지 않은 시각)."""
    return datetime.combine(day, time(12, 0), tzinfo=KST).astimezone(UTC)
