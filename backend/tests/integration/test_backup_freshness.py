"""H·C. 백업 목록 API와 신선도 감시 잡 (§21 / S2-4 PR-3 안건 ⑦ (b) — ADR-0050).

★ 앱은 백업 볼륨을 읽기 전용으로 본다 — 매니페스트·리허설 결과 JSON뿐. 여기서는 그 파일들을 직접 써서
  (스크립트를 돌리지 않는다 — 스크립트 계약은 test_backup_scripts.py) 목록·신선도·알림·감사 기록을 본다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.identity.models import RoleCode
from app.modules.platform import backups, scheduler
from app.modules.worklist.models import Alert
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = [pytest.mark.group_h, pytest.mark.group_c]

BACKUPS = "/api/v1/system/backups"
NOW = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _write_set(
    root: Path, moment: datetime, *, tables: dict[str, int] | None = None, valid: bool = True
) -> Path:
    directory = root / f"kbos-{_stamp(moment)}"
    directory.mkdir()
    if valid:
        manifest = {
            "format_version": 1,
            "created_at_utc": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "database": "kbos_dev",
            "migration_head": "abc123",
            "file_documents": 2,
            "tables": tables or {"a": 3, "b": 4},
            "artifacts": {
                "db.dump.enc": {"sha256": "0" * 64, "bytes": 100},
                "files.tar.gz.enc": {"sha256": "1" * 64, "bytes": 50},
            },
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
    else:
        (directory / "manifest.json").write_text("{not json")
    return directory


def _write_rehearsal(
    root: Path, moment: datetime, *, ok: bool, failures: list[str] | None = None
) -> None:
    (root / f"rehearsal-{_stamp(moment)}.json").write_text(
        json.dumps({"ok": ok, "set": "kbos-x", "failures": failures or []})
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "kbos_backup_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def admin_ids() -> tuple[int, int]:
    return (
        create_user("bk-admin-a@example.com", roles=(RoleCode.ADMIN,)),
        create_user("bk-admin-b@example.com", roles=(RoleCode.ADMIN,)),
    )


def _alerts() -> list[Alert]:
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(Alert).where(Alert.dedup_key.like("backup.freshness:%")).order_by(Alert.id)
            ).scalars()
        )
        for row in rows:
            uow.session.expunge(row)
        return rows


# ── 목록 ──────────────────────────────────────────────────────────────────


def test_only_pattern_named_sets_are_listed_newest_first_and_broken_manifests_stay_visible(
    root: Path,
) -> None:
    _write_set(root, NOW - timedelta(days=2))
    newest = _write_set(root, NOW - timedelta(hours=1), tables={"a": 10, "b": 20, "c": 30})
    _write_set(root, NOW - timedelta(days=1), valid=False)
    (root / "kbos-notaset").mkdir()
    (root / "random.txt").write_text("x")
    views = backups.list_backups(root)
    assert views[0].name == newest.name and len(views) == 3
    assert (views[0].total_rows, views[0].table_count, views[0].total_bytes) == (60, 3, 150)
    assert views[0].migration_head == "abc123" and views[0].file_documents == 2
    broken = next(v for v in views if not v.manifest_ok)
    assert broken.migration_head is None and broken.total_rows is None
    assert views[0].created_at == NOW - timedelta(hours=1)


def test_a_missing_directory_lists_nothing() -> None:
    assert backups.list_backups(Path("/nonexistent/backups-dir")) == []
    assert backups.list_rehearsals(Path("/nonexistent/backups-dir")) == []


# ── 신선도 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("backup_age_h", "rehearsal_age_d", "rehearsal_ok", "expected"),
    [
        (25, 7, True, []),  # 경계 안쪽 — 정상
        (26, 8, True, []),  # 정확히 기준 — 아직 정상(초과만 문제)
        (27, 7, True, ["BACKUP_STALE"]),
        (1, 9, True, ["REHEARSAL_STALE"]),
        (1, 1, False, ["REHEARSAL_FAILED"]),
        (100, 20, True, ["BACKUP_STALE", "REHEARSAL_STALE"]),
    ],
)
def test_freshness_thresholds(
    root: Path, backup_age_h: int, rehearsal_age_d: int, rehearsal_ok: bool, expected: list[str]
) -> None:
    _write_set(root, NOW - timedelta(hours=backup_age_h))
    _write_rehearsal(
        root, NOW - timedelta(days=rehearsal_age_d), ok=rehearsal_ok, failures=["행수 불일치 x"]
    )
    assert [p.code for p in backups.freshness_problems(root, now=NOW)] == expected


def test_no_backup_and_a_backup_only_history(root: Path) -> None:
    assert [p.code for p in backups.freshness_problems(root, now=NOW)] == ["NO_BACKUP"]
    _write_set(root, NOW - timedelta(days=10))
    codes = [p.code for p in backups.freshness_problems(root, now=NOW)]
    assert "BACKUP_STALE" in codes and "NO_REHEARSAL" in codes
    # 설치 직후(백업 8일 미만)에는 리허설이 없어도 문제가 아니다
    fresh = root / "sub"
    fresh.mkdir()
    _write_set(fresh, NOW - timedelta(hours=2))
    assert backups.freshness_problems(fresh, now=NOW) == []


def test_only_the_latest_rehearsal_decides_and_an_unreadable_result_counts_as_failed(
    root: Path,
) -> None:
    _write_set(root, NOW - timedelta(hours=1))
    _write_rehearsal(root, NOW - timedelta(days=3), ok=False, failures=["옛 실패"])
    _write_rehearsal(root, NOW - timedelta(days=1), ok=True)
    assert backups.freshness_problems(root, now=NOW) == []
    (root / f"rehearsal-{_stamp(NOW)}.json").write_text("garbage")
    assert [p.code for p in backups.freshness_problems(root, now=NOW)] == ["REHEARSAL_FAILED"]


def test_a_set_with_a_broken_manifest_does_not_count_as_a_fresh_backup(root: Path) -> None:
    _write_set(root, NOW - timedelta(hours=1), valid=False)
    assert [p.code for p in backups.freshness_problems(root, now=NOW)] == ["NO_BACKUP"]


# ── 잡 ────────────────────────────────────────────────────────────────────


def test_the_job_skips_quietly_when_no_backup_volume_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "kbos_backup_dir", None)
    assert backups.run_backup_freshness(now=NOW) == {"configured": 0, "problems": 0, "alerted": 0}
    assert _alerts() == []


def test_the_job_alerts_every_admin_once_per_day_and_kind(
    root: Path, admin_ids: tuple[int, int]
) -> None:
    _write_set(root, NOW - timedelta(hours=40))
    first = backups.run_backup_freshness(now=NOW)
    assert first["problems"] == 1 and first["alerted"] == 2  # ADMIN 2명 × 문제 1건
    again = backups.run_backup_freshness(now=NOW + timedelta(hours=1))
    assert again["alerted"] == 0 and len(_alerts()) == 2  # 같은 날(KST)·같은 종류는 재발송 없음
    assert {a.recipient_user_id for a in _alerts()} == set(admin_ids)
    assert all(a.severity == "CRITICAL" for a in _alerts())
    next_day = backups.run_backup_freshness(now=NOW + timedelta(days=1))
    assert next_day["alerted"] == 2  # 다음 날은 새 알림(문제가 계속되면 매일 알린다)


def test_the_registered_job_runs_the_freshness_check(root: Path) -> None:
    spec = scheduler.JOBS_BY_CODE["backup-freshness"]
    assert spec.schedule == "daily@08:00"
    _write_set(root, utc_now_minus(hours=1))
    assert spec.run()["problems"] == 0


def utc_now_minus(*, hours: int) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(hours=hours)


# ── API ───────────────────────────────────────────────────────────────────


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
            ).status_code
            == 200
        )
        yield client


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("bk-api-admin@example.com", RoleCode.ADMIN)


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("bk-api-cert@example.com", RoleCode.CERT)


def test_the_api_is_admin_only_and_never_exposes_artifacts(cert: TestClient, root: Path) -> None:
    assert cert.get(BACKUPS).status_code == 403
    routes = {getattr(r, "path", "") for r in app.routes}
    assert not any("backups" in path and ("download" in path or "{" in path) for path in routes)


def test_the_api_lists_sets_the_latest_rehearsal_and_problems_and_writes_an_audit_row(
    admin: TestClient, root: Path
) -> None:
    _write_set(root, NOW - timedelta(days=30))
    newest = _write_set(root, datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2))
    _write_rehearsal(
        root,
        datetime.now(UTC).replace(microsecond=0) - timedelta(days=1),
        ok=False,
        failures=["행수 불일치 a"],
    )
    response = admin.get(BACKUPS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True and body["total"] == 2
    assert body["items"][0]["name"] == newest.name and body["items"][0]["manifest_ok"] is True
    assert body["latest_rehearsal"]["ok"] is False and body["latest_rehearsal"]["failures"] == [
        "행수 불일치 a"
    ]
    assert [p["code"] for p in body["problems"]] == ["REHEARSAL_FAILED"]
    assert "stored" not in json.dumps(body)
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(AuditLog).where(AuditLog.action == "system.backups.viewed")
            ).scalars()
        )
    assert len(rows) == 1 and rows[0].actor_user_id is not None
    admin.get(BACKUPS)
    with unit_of_work() as uow:
        assert (
            len(
                list(
                    uow.session.execute(
                        select(AuditLog).where(AuditLog.action == "system.backups.viewed")
                    ).scalars()
                )
            )
            == 2
        )


def test_the_api_paginates_and_reports_an_unconfigured_volume(
    admin: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for hours in (1, 2, 3):
        _write_set(root, datetime.now(UTC).replace(microsecond=0) - timedelta(hours=hours))
    page = admin.get(BACKUPS, params={"page": 2, "size": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2
    assert admin.get(BACKUPS, params={"size": 201}).status_code == 422
    monkeypatch.setattr(settings, "kbos_backup_dir", None)
    empty = admin.get(BACKUPS).json()
    assert empty["configured"] is False and empty["items"] == [] and empty["problems"] == []
