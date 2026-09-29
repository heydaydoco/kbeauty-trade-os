"""H·C. 파일 저장소 감시·고아/유실·실물 물리 정리 (§18.4 / S2-3 판정 요청 17 / S2-4 PR-3 안건 ⑧ — ADR-0050).

★ 되돌릴 수 없는 동작(실물 삭제)이라 기본 OFF·dry-run 기본이 계약이다 — 지우지 않는 경로(dry-run·OFF·
  보존기한·유예기간·아직 안 지운 문서)를 삭제 경로만큼 강하게 고정한다.
★ 저장소는 테스트마다 임시 폴더, 디스크 여유는 monkeypatch로 만든다(실제 디스크에 의존하지 않는다).
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.cli import main as cli_main
from app.core.config import settings
from app.core.db.uow import unit_of_work
from app.modules.audit.models import AuditLog
from app.modules.documents.models import Document, DocumentType
from app.modules.identity.models import RoleCode
from app.modules.platform import scheduler, storage
from app.modules.worklist.models import Alert, AlertRule
from tests.support.factories import create_user

pytestmark = [pytest.mark.group_h, pytest.mark.group_c]

NOW = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)  # KST 12:00
TODAY = date(2026, 9, 30)
_Usage = shutil._ntuple_diskusage


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "files"
    directory.mkdir()
    monkeypatch.setattr(settings, "file_storage_root", str(directory))
    monkeypatch.setattr(settings, "kbos_file_purge_enabled", False)
    monkeypatch.setattr(settings, "kbos_file_purge_grace_days", 30)
    return directory


@pytest.fixture
def admins() -> tuple[int, int]:
    return (
        create_user("st-admin-a@example.com", roles=(RoleCode.ADMIN,)),
        create_user("st-admin-b@example.com", roles=(RoleCode.ADMIN,)),
    )


def _free(monkeypatch: pytest.MonkeyPatch, pct: float) -> None:
    total = 1000 * 1024 * 1024
    monkeypatch.setattr(
        storage,
        "_disk_usage",
        lambda _root: _Usage(total, int(total * (1 - pct / 100)), int(total * pct / 100)),
    )


def _write(root: Path, name: str, content: bytes = b"data", *, age_hours: float = 5) -> Path:
    path = root / name
    path.write_bytes(content)
    moment = NOW.timestamp() - age_hours * 3600
    os.utime(path, (moment, moment))
    return path


def _doc(
    root: Path | None,
    *,
    deleted_days_ago: float | None = None,
    retention: date | None = None,
    kind: str = "FILE",
    write: bool = True,
    purged: bool = False,
    size: int = 4,
) -> tuple[int, str]:
    stored = uuid.uuid4().hex
    with unit_of_work() as uow:
        type_id = uow.session.execute(
            select(DocumentType.id).where(DocumentType.code == "CFS")
        ).scalar_one()
        row = Document(
            owner_type="SKU",
            owner_id=1,
            document_type_id=type_id,
            storage_kind=kind,
            stored_name=stored if kind == "FILE" else None,
            original_filename="x.pdf" if kind == "FILE" else None,
            content_type="application/pdf" if kind == "FILE" else None,
            size_bytes=size if kind == "FILE" else None,
            sha256=(uuid.uuid4().hex * 2) if kind == "FILE" else None,
            url=None if kind == "FILE" else "https://example.com/x",
            retention_until=retention,
            issued_on=date(2020, 1, 1) if retention else None,
        )
        if deleted_days_ago is not None:
            row.deleted_at = NOW - timedelta(days=deleted_days_ago)
        if purged:
            row.purged_at = NOW - timedelta(days=1)
        uow.session.add(row)
        uow.session.flush()
        doc_id = row.id
    if kind == "FILE" and write and root is not None:
        _write(root, stored, b"x" * size)
    return doc_id, stored


def _purged(doc_id: int) -> datetime | None:
    with unit_of_work() as uow:
        return uow.session.execute(
            select(Document.purged_at).where(Document.id == doc_id)
        ).scalar_one()


def _alerts() -> list[Alert]:
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(Alert).where(Alert.dedup_key.like("storage.monitor:%")).order_by(Alert.id)
            ).scalars()
        )
        for row in rows:
            uow.session.expunge(row)
        return rows


# ── 임계 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, (20, 10)),
        ({}, (20, 10)),
        ({"warn_pct": 30, "critical_pct": 5}, (30, 5)),
        ({"warn_pct": "x", "critical_pct": 5}, (20, 5)),
        (
            {"warn_pct": 10, "critical_pct": 10},
            (20, 10),
        ),  # 경고 ≤ 위험 — 기본값(감시를 끄는 규칙 방지)
        ({"warn_pct": 5, "critical_pct": 10}, (20, 10)),
        ({"warn_pct": 100, "critical_pct": 0}, (20, 10)),
        ({"warn_pct": True, "critical_pct": 5}, (20, 5)),
        (["x"], (20, 10)),
    ],
)
def test_thresholds_are_normalized(config: Any, expected: tuple[int, int]) -> None:
    assert storage.normalize_thresholds(config) == expected


@pytest.mark.parametrize(
    ("free", "level"),
    [
        (20.0, "OK"),
        (19.99, "WARN"),
        (10.0, "WARN"),
        (9.99, "CRITICAL"),
        (0.0, "CRITICAL"),
        (55.0, "OK"),
    ],
)
def test_levels_at_the_boundaries(free: float, level: str) -> None:
    assert storage.level_for(free, 20, 10) == level


# ── 스캔 ──────────────────────────────────────────────────────────────────


def test_scan_reports_orphans_and_missing_files_but_ignores_young_files_and_foreign_names(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 50)
    ok_id, _ = _doc(root)
    lost_id, _ = _doc(root, write=False)
    purged_id, _ = _doc(root, deleted_days_ago=40, write=False, purged=True)
    orphan = uuid.uuid4().hex
    _write(root, orphan, b"orphan", age_hours=3)
    young = uuid.uuid4().hex
    _write(root, young, b"upload-in-flight", age_hours=0.2)  # 방금 올라온 실물 — 고아 아님
    _write(
        root, "notes.txt", b"not a stored name", age_hours=9
    )  # 우리 이름 규칙이 아니면 손대지 않는다
    report = storage.scan_storage(now=NOW)
    assert report.orphan_files == [orphan]
    assert report.missing_documents == [lost_id]
    assert ok_id not in report.missing_documents and purged_id not in report.missing_documents
    assert report.file_count == 4 and report.app_bytes == 4 + 6 + 16 + 17
    assert report.level == "OK"


def test_scan_of_an_absent_storage_root_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "file_storage_root", str(tmp_path / "never-created" / "files"))
    report = storage.scan_storage(now=NOW)
    assert report.file_count == 0 and report.app_bytes == 0 and report.total_bytes > 0


# ── 잡·알림 ───────────────────────────────────────────────────────────────


def test_low_free_space_alerts_admins_once_per_day_and_level(
    root: Path, admins: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 15)
    first = storage.run_storage_monitor(now=NOW)
    assert first["level"] == 1 and first["alerted"] == 2
    assert storage.run_storage_monitor(now=NOW + timedelta(hours=1))["alerted"] == 0
    assert {a.recipient_user_id for a in _alerts()} == set(admins)
    assert all(a.severity == "WARN" for a in _alerts())
    _free(monkeypatch, 5)
    critical = storage.run_storage_monitor(now=NOW + timedelta(hours=2))
    assert critical["level"] == 2 and critical["alerted"] == 2  # 수준이 바뀌면 새 알림
    assert {a.severity for a in _alerts()} == {"WARN", "CRITICAL"}
    assert storage.run_storage_monitor(now=NOW + timedelta(days=1))["alerted"] == 2  # 다음 날 다시


def test_thresholds_come_from_the_alert_rule_data(
    root: Path, admins: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 30)
    assert storage.run_storage_monitor(now=NOW)["level"] == 0  # 기본 임계(20)에서는 정상
    with unit_of_work() as uow:
        uow.session.add(
            AlertRule(
                code="STORAGE-FREE",
                name_ko="용량",
                event_type=storage.FREE_SPACE_EVENT,
                config={"warn_pct": 40, "critical_pct": 20},
            )
        )
    assert storage.run_storage_monitor(now=NOW + timedelta(days=1))["level"] == 1


def test_missing_files_alert_critical_and_orphans_alert_info_without_touching_anything(
    root: Path, admins: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 50)
    _doc(root, write=False)
    orphan = uuid.uuid4().hex
    _write(root, orphan, age_hours=4)
    counts = storage.run_storage_monitor(now=NOW)
    assert (counts["missing"], counts["orphans"], counts["alerted"]) == (1, 1, 4)
    severities = sorted({a.severity for a in _alerts()})
    assert severities == ["CRITICAL", "INFO"]
    assert (root / orphan).exists()  # 고아는 보고만 — 지우지 않는다


def test_a_healthy_store_is_silent(
    root: Path, admins: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 60)
    _doc(root)
    assert storage.run_storage_monitor(now=NOW)["alerted"] == 0 and _alerts() == []


def test_the_registered_job_is_the_monitor(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _free(monkeypatch, 60)
    spec = scheduler.JOBS_BY_CODE["storage-monitor"]
    assert spec.schedule == "daily@05:00"
    assert spec.run()["purge_enabled"] == 0


# ── 물리 정리 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "eligible"),
    [
        ({"deleted_days_ago": 31}, True),
        ({"deleted_days_ago": 30}, True),  # 유예기간 경계 — 정확히 30일이면 후보
        ({"deleted_days_ago": 29.9}, False),
        ({"deleted_days_ago": None}, False),  # 삭제되지 않은 문서는 절대 대상이 아니다
        ({"deleted_days_ago": 90, "retention": date(2026, 10, 1)}, False),  # 보존기한 남음
        ({"deleted_days_ago": 90, "retention": TODAY}, False),  # 보존기한 당일은 아직 잠금
        ({"deleted_days_ago": 90, "retention": date(2026, 9, 29)}, True),  # 보존기한 지남
        ({"deleted_days_ago": 90, "purged": True}, False),  # 이미 정리됨
        ({"deleted_days_ago": 90, "kind": "LINK"}, False),  # 실물이 없는 LINK형
    ],
)
def test_purge_eligibility(root: Path, kwargs: dict[str, Any], eligible: bool) -> None:
    doc_id, _ = _doc(root, **kwargs)
    with unit_of_work() as uow:
        found = storage.purge_candidates(uow.session, cutoff=NOW - timedelta(days=30), today=TODAY)
    assert (doc_id in [c.id for c in found]) is eligible


def test_dry_run_changes_nothing(root: Path) -> None:
    doc_id, stored = _doc(root, deleted_days_ago=60)
    counts = storage.purge_files(apply=False, now=NOW)
    assert counts == {"candidates": 1, "purged": 0, "failed": 0, "bytes": 0}
    assert (root / stored).exists() and _purged(doc_id) is None


def test_apply_deletes_the_file_records_purged_at_and_audits_it(root: Path) -> None:
    doc_id, stored = _doc(root, deleted_days_ago=60, size=7)
    keep_id, keep_stored = _doc(root, deleted_days_ago=5)
    counts = storage.purge_files(apply=True, now=NOW)
    assert counts == {"candidates": 1, "purged": 1, "failed": 0, "bytes": 7}
    assert not (root / stored).exists() and _purged(doc_id) is not None
    assert (root / keep_stored).exists() and _purged(keep_id) is None  # 유예 중인 것은 그대로
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(AuditLog).where(AuditLog.action == "documents.file.purged")
            ).scalars()
        )
    assert (
        len(rows) == 1 and rows[0].entity_id == doc_id and rows[0].detail["stored_name"] == stored
    )  # type: ignore[index]
    assert storage.purge_files(apply=True, now=NOW)["candidates"] == 0  # 재실행은 후보 0


def test_a_file_that_is_already_gone_is_still_recorded_as_purged(root: Path) -> None:
    """삭제 후 기록 전에 죽었던 경우 — 다음 실행이 '이미 없음'으로 기록을 마무리한다"""
    doc_id, stored = _doc(root, deleted_days_ago=60)
    (root / stored).unlink()
    assert storage.purge_files(apply=True, now=NOW)["purged"] == 1
    assert _purged(doc_id) is not None


def test_the_job_reports_candidates_but_deletes_nothing_while_the_switch_is_off(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 60)
    doc_id, stored = _doc(root, deleted_days_ago=60)
    off = storage.run_storage_monitor(now=NOW)
    assert (off["purge_enabled"], off["purge_candidates"], off["purged"]) == (0, 1, 0)
    assert (root / stored).exists() and _purged(doc_id) is None
    monkeypatch.setattr(settings, "kbos_file_purge_enabled", True)
    on = storage.run_storage_monitor(now=NOW)
    assert (on["purge_enabled"], on["purged"]) == (1, 1)
    assert not (root / stored).exists() and _purged(doc_id) is not None


def test_a_failing_file_does_not_block_the_rest_fails_the_job_and_is_finished_by_the_next_run(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """삭제가 실패해도(기록은 이미 커밋) 나머지는 계속되고 잡은 실패로 알리며, 다음 실행이 남은 실물을 마무리한다"""
    _free(monkeypatch, 60)
    monkeypatch.setattr(settings, "kbos_file_purge_enabled", True)
    bad_id, bad = _doc(root, deleted_days_ago=60)
    good_id, good = _doc(root, deleted_days_ago=60)
    original = Path.unlink

    def flaky(self: Path, missing_ok: bool = False) -> None:
        if self.name == bad:
            raise PermissionError("denied")
        original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky)
    with pytest.raises(RuntimeError, match="실패"):
        storage.run_storage_monitor(now=NOW)
    assert _purged(good_id) is not None and not (root / good).exists()
    assert _purged(bad_id) is not None and (root / bad).exists()  # 기록은 됐고 실물이 남은 상태
    monkeypatch.setattr(Path, "unlink", original)
    again = storage.purge_files(apply=True, now=NOW)  # 잔여물 정리가 마무리한다
    assert again["failed"] == 0 and not (root / bad).exists()


@pytest.mark.parametrize(
    "change",
    [
        "retention_until = DATE '2030-01-01', issued_on = DATE '2020-01-01'",  # 보존기한 연장
        "deleted_at = NULL",  # 삭제 취소(복원)
        "purged_at = now()",  # 다른 프로세스가 이미 정리
    ],
)
def test_conditions_are_rechecked_after_locking_and_the_file_survives(
    root: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """후보 계산 뒤 잠금 전에 조건이 바뀌면 **실물을 지우지 않는다**(TOCTOU) — 잠금·재확인이 삭제보다 먼저다"""
    doc_id, stored = _doc(root, deleted_days_ago=60)
    real = storage.purge_candidates

    def then_change(session: Any, **kwargs: Any) -> Any:
        found = real(session, **kwargs)
        with unit_of_work() as other:
            if "purged_at" in change:
                other.session.execute(
                    text(
                        "UPDATE documents SET deleted_at = COALESCE(deleted_at, now()), purged_at = now() WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            else:
                other.session.execute(
                    text(f"UPDATE documents SET {change} WHERE id = :id"), {"id": doc_id}
                )
        return found

    monkeypatch.setattr(storage, "purge_candidates", then_change)
    counts = storage.purge_files(apply=True, now=NOW)
    assert counts["purged"] == 0
    if "purged_at" not in change:
        assert _purged(doc_id) is None
        assert (root / stored).exists()  # ★ 실물이 그대로다
    with unit_of_work() as uow:
        assert (
            uow.session.execute(
                select(AuditLog).where(AuditLog.action == "documents.file.purged")
            ).first()
            is None
        )


def test_the_grace_period_comes_from_the_setting(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """유예기간은 설정값 — 7일로 낮추면 8일 전 삭제분만 후보(6일 전은 아님)"""
    monkeypatch.setattr(settings, "kbos_file_purge_grace_days", 7)
    _doc(root, deleted_days_ago=8)
    _doc(root, deleted_days_ago=6)
    assert storage.purge_files(apply=False, now=NOW)["candidates"] == 1
    monkeypatch.setattr(settings, "kbos_file_purge_grace_days", 30)
    assert storage.purge_files(apply=False, now=NOW)["candidates"] == 0


def test_the_retention_day_is_judged_in_kst(root: Path) -> None:
    """보존기한 KST 9/30인 문서 — KST 9/30 23:00(UTC 14:00)은 당일 잠금, KST 10/1 01:00(UTC 16:00)부터 후보"""
    _doc(root, deleted_days_ago=90, retention=date(2026, 9, 30))
    assert (
        storage.purge_files(apply=False, now=datetime(2026, 9, 30, 14, 0, tzinfo=UTC))["candidates"]
        == 0
    )
    assert (
        storage.purge_files(apply=False, now=datetime(2026, 9, 30, 16, 0, tzinfo=UTC))["candidates"]
        == 1
    )


def test_soft_deleted_files_within_the_grace_period_are_not_orphans(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """삭제됐지만 아직 정리 안 된 문서의 실물은 행이 있으므로 고아가 아니다"""
    _free(monkeypatch, 60)
    _doc(root, deleted_days_ago=3)
    assert storage.scan_storage(now=NOW).orphan_files == []


def test_a_zero_sized_volume_is_critical_not_a_crash(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "_disk_usage", lambda _root: _Usage(0, 0, 0))
    report = storage.scan_storage(now=NOW)
    assert report.free_pct == 0.0 and report.level == "CRITICAL"


def test_purged_at_check_rejects_live_documents_and_links(root: Path) -> None:
    live_id, _ = _doc(root)
    link_id, _ = _doc(None, deleted_days_ago=60, kind="LINK")
    for doc_id in (live_id, link_id):
        with pytest.raises(IntegrityError), unit_of_work() as uow:
            uow.session.execute(
                text("UPDATE documents SET purged_at = now() WHERE id = :id"), {"id": doc_id}
            )


def test_the_cli_purge_is_a_dry_run_unless_apply_is_given(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc_id, stored = _doc(root, deleted_days_ago=60)
    assert cli_main(["purge-files", "--base-date", "2026-09-30"]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "후보 1건" in out and (root / stored).exists()
    assert cli_main(["purge-files", "--apply", "--base-date", "2026-09-30"]) == 0
    assert "1건 삭제" in capsys.readouterr().out
    assert not (root / stored).exists() and _purged(doc_id) is not None


def test_the_cli_storage_monitor_prints_a_summary(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _free(monkeypatch, 60)
    assert cli_main(["storage-monitor"]) == 0
    out = capsys.readouterr().out
    assert "저장소 점검 완료" in out and "자동 정리 OFF" in out
