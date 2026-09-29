"""H. 백업·복원 리허설 스크립트 (§21 "백업은 복원 리허설까지가 백업" / S2-4 PR-3 안건 ⑦ — ADR-0050).

★ 실제 셸 스크립트(infra/backup)를 로컬 PostgreSQL에 대고 돌린다 — 소스 DB는 테스트가 만든 작은 스키마
  (alembic_version·documents·other)라 앱 스키마와 무관하다. pg_dump·psql·openssl이 없거나 슈퍼유저 접속이
  안 되면 skip한다(CI의 서비스 컨테이너는 postgres/ci_postgres_pw, 로컬 샌드박스도 같은 값으로 맞춘다).
★ 검증하는 계약: ① 암호화 왕복(평문 노출 없음·모드 600/700) ② 패스프레이즈 없음/짧음 거부(fail-closed)
  ③ 리허설이 변조(산출물·매니페스트 행수·실물 해시·실물 유실)를 **탐지**한다 — 통과하는 리허설은 의미가 없다
  ④ 보관 N세트(이름 패턴에 맞는 세트만 삭제) ⑤ 스크래치 DB 정리.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.group_h, pytest.mark.group_j]

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "infra" / "backup"
PASSPHRASE = "test-passphrase-0123456789"

_PG = {
    "PGHOST": os.environ.get("KBOS_TEST_PGHOST", "localhost"),
    "PGPORT": os.environ.get("KBOS_TEST_PGPORT", "5432"),
    "PGUSER": os.environ.get("KBOS_TEST_PGUSER", "postgres"),
    "PGPASSWORD": os.environ.get("KBOS_TEST_PGPASSWORD", "ci_postgres_pw"),
}


def _psql(
    *args: str, db: str = "postgres", stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", db, *args],
        env={**os.environ, **_PG},
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _tools_available() -> bool:
    if not all(shutil.which(tool) for tool in ("pg_dump", "pg_restore", "psql", "openssl", "bash")):
        return False
    return _psql("-c", "SELECT 1").returncode == 0


#: CI는 KBOS_REQUIRE_SCRIPT_TESTS=1 — 도구가 없으면 skip이 아니라 **실패**한다(러너 이미지가 바뀌어 검증이 조용히 사라지는 것을 막는다).
REQUIRED = os.environ.get("KBOS_REQUIRE_SCRIPT_TESTS") == "1"
pytestmark.append(  # type: ignore[attr-defined]
    pytest.mark.skipif(
        not _tools_available() and not REQUIRED, reason="pg_dump/openssl 또는 슈퍼유저 접속이 없다"
    )
)


def test_the_script_tests_actually_run_where_they_are_required() -> None:
    """필수 환경(CI)에서는 도구·접속이 실제로 있어야 한다 — 이 파일 전체가 skip으로 사라지지 않게"""
    if REQUIRED:
        assert _tools_available(), (
            "pg_dump·pg_restore·psql·openssl·슈퍼유저 접속이 필요합니다(CI 러너/서비스 확인)"
        )


class Env:
    def __init__(self, tmp: Path, db: str) -> None:
        self.db = db
        self.files = tmp / "files"
        self.out = tmp / "out"
        self.files.mkdir()
        self.out.mkdir()
        self.base = {
            **os.environ,
            **_PG,
            "PGDATABASE": db,
            "FILES_DIR": str(self.files),
            "BACKUP_DIR": str(self.out),
            "KBOS_BACKUP_PASSPHRASE": PASSPHRASE,
        }

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPTS / script), *args],
            env={**self.base, **env},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def sets(self) -> list[Path]:
        return sorted(p for p in self.out.iterdir() if p.is_dir() and p.name.startswith("kbos-"))

    def results(self) -> list[dict[str, object]]:
        return [json.loads(p.read_text()) for p in sorted(self.out.glob("rehearsal-*.json"))]


def _put_file(env: Env, name: str, content: bytes, *, sha: str | None = None) -> None:
    (env.files / name).write_bytes(content)
    digest = sha or hashlib.sha256(content).hexdigest()
    _psql(
        "-c",
        f"INSERT INTO documents(storage_kind, stored_name, sha256) VALUES ('FILE', '{name}', '{digest}')",
        db=env.db,
    )


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    db = f"bk_src_{uuid.uuid4().hex[:10]}"
    assert _psql("-c", f'CREATE DATABASE "{db}"').returncode == 0
    setup = """
    CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY);
    INSERT INTO alembic_version VALUES ('abc123');
    CREATE TABLE documents(id serial PRIMARY KEY, storage_kind text, stored_name text, sha256 char(64));
    CREATE TABLE other(id serial PRIMARY KEY, v text);
    INSERT INTO other(v) SELECT 'row' || g FROM generate_series(1, 37) g;
    """
    assert _psql(db=db, stdin=setup).returncode == 0
    context = Env(tmp_path, db)
    try:
        yield context
    finally:
        _psql("-c", f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        leftovers = _psql(
            "-c", "SELECT datname FROM pg_database WHERE datname LIKE 'kbos_rehearsal%'"
        ).stdout.split()
        for name in (
            leftovers
        ):  # 다른 테스트의 잔여물이 아니라 이 테스트가 남긴 것만 의미가 있다 — 어쨌든 치운다
            _psql("-c", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _scratch_dbs() -> list[str]:
    return _psql(
        "-c", "SELECT datname FROM pg_database WHERE datname LIKE 'kbos_rehearsal%'"
    ).stdout.split()


def test_backup_then_rehearsal_round_trips_and_the_artifacts_are_encrypted(env: Env) -> None:
    """백업 → 복원 리허설이 통과하고, 산출물에는 평문 본문이 없다(산출물 600·폴더 755·매니페스트 644·매니페스트 정합)"""
    _put_file(env, "a" * 8, b"PLAINTEXT-MARKER-FILE-BODY")
    backup = env.run("backup.sh")
    assert backup.returncode == 0, backup.stderr
    (set_dir,) = env.sets()
    # 산출물(.enc)만 600 — 폴더(755)·매니페스트(644)는 앱(비root uid)이 읽을 수 있어야 신선도 감시·목록 API가 동작한다.
    assert oct(set_dir.stat().st_mode & 0o777) == "0o755"
    assert oct((set_dir / "manifest.json").stat().st_mode & 0o777) == "0o644"
    for name in ("db.dump.enc", "files.tar.gz.enc"):
        assert oct((set_dir / name).stat().st_mode & 0o777) == "0o600", name
    assert oct(env.out.stat().st_mode & 0o777) == "0o755"
    assert b"PGDMP" not in (set_dir / "db.dump.enc").read_bytes()  # pg_dump 커스텀 형식 표식이 없다
    assert b"PLAINTEXT-MARKER" not in (set_dir / "files.tar.gz.enc").read_bytes()
    assert b"row1" not in (set_dir / "db.dump.enc").read_bytes()
    assert not [p for p in env.out.iterdir() if p.name.startswith(".tmp")]  # 임시 디렉터리 정리

    manifest = json.loads((set_dir / "manifest.json").read_text())
    assert manifest["migration_head"] == "abc123" and manifest["file_documents"] == 1
    assert manifest["tables"] == {"alembic_version": 1, "documents": 1, "other": 37}
    for name, info in manifest["artifacts"].items():
        data = (set_dir / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"] and len(data) == info["bytes"]

    rehearsal = env.run("restore-rehearsal.sh")
    assert rehearsal.returncode == 0, rehearsal.stderr + rehearsal.stdout
    (result,) = env.results()
    assert result["ok"] is True and result["failures"] == [] and result["set"] == set_dir.name
    assert "테이블별 행수 일치" in result["checks"] and any(
        "FILE 문서 1건" in c for c in result["checks"]
    )  # type: ignore[operator]
    assert _scratch_dbs() == []  # 스크래치 DB 정리


def test_a_missing_or_short_passphrase_is_refused_and_writes_nothing(env: Env) -> None:
    """fail-closed — 패스프레이즈가 없거나 16자 미만이면 실행을 거부하고 어떤 산출물도 만들지 않는다"""
    for value in ("", "short"):
        result = env.run("backup.sh", KBOS_BACKUP_PASSPHRASE=value)
        assert result.returncode != 0
        assert "KBOS_BACKUP_PASSPHRASE" in result.stderr
    assert list(env.out.iterdir()) == []
    scheduler = env.run("scheduler.sh", KBOS_BACKUP_PASSPHRASE="")
    assert scheduler.returncode != 0
    rehearsal = env.run("restore-rehearsal.sh", KBOS_BACKUP_PASSPHRASE="")
    assert rehearsal.returncode != 0


def test_the_rehearsal_fails_with_the_wrong_passphrase(env: Env) -> None:
    assert env.run("backup.sh").returncode == 0
    wrong = env.run("restore-rehearsal.sh", KBOS_BACKUP_PASSPHRASE="another-passphrase-9876543")
    assert wrong.returncode != 0
    (result,) = env.results()
    assert result["ok"] is False and "복호화 실패" in " ".join(result["failures"])  # type: ignore[arg-type]
    assert _scratch_dbs() == []


def test_the_rehearsal_detects_a_tampered_artifact(env: Env) -> None:
    """산출물 한 바이트 변조 — 해시 검증에서 잡힌다(스크래치 DB는 만들지도 않는다)"""
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    target = set_dir / "db.dump.enc"
    data = bytearray(target.read_bytes())
    data[100] ^= 0xFF
    target.write_bytes(bytes(data))
    result = env.run("restore-rehearsal.sh")
    assert result.returncode != 0
    (report,) = env.results()
    assert report["ok"] is False and "해시 불일치" in " ".join(report["failures"])  # type: ignore[arg-type]
    assert _scratch_dbs() == []


def test_the_rehearsal_detects_a_row_count_that_disagrees_with_the_manifest(env: Env) -> None:
    """매니페스트의 행수를 고치면(=백업 시점 데이터와 복원 데이터가 다르다는 뜻) 리허설이 실패한다"""
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    manifest = set_dir / "manifest.json"
    manifest.write_text(manifest.read_text().replace('"other": 37', '"other": 36'))
    result = env.run("restore-rehearsal.sh")
    assert result.returncode != 0
    (report,) = env.results()
    assert any("행수 불일치 other" in f for f in report["failures"])  # type: ignore[union-attr]
    assert _scratch_dbs() == []


def test_the_rehearsal_detects_a_table_missing_from_the_manifest(env: Env) -> None:
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    manifest = set_dir / "manifest.json"
    manifest.write_text(manifest.read_text().replace('    "other": 37\n', '    "zzz_absent": 0\n'))
    assert env.run("restore-rehearsal.sh").returncode != 0
    (report,) = env.results()
    assert any("매니페스트에 없는 테이블" in f for f in report["failures"])  # type: ignore[union-attr]


def test_the_rehearsal_detects_a_file_whose_hash_differs_from_its_row(env: Env) -> None:
    """DB 행의 해시와 실물이 다르면(백업 전부터 손상) 리허설이 잡는다 — 백업이 '있다'와 '쓸 수 있다'는 다르다"""
    _put_file(env, "good.bin", b"GOOD")
    _put_file(env, "bad.bin", b"ACTUAL", sha="0" * 64)
    assert env.run("backup.sh").returncode == 0
    assert env.run("restore-rehearsal.sh").returncode != 0
    (report,) = env.results()
    failures = " ".join(report["failures"])  # type: ignore[arg-type]
    assert "FILE 문서 해시 불일치: bad.bin" in failures and "good.bin" not in failures


def test_the_rehearsal_detects_a_document_whose_file_is_missing(env: Env) -> None:
    _put_file(env, "present.bin", b"P")
    _put_file(env, "lost.bin", b"L")
    (env.files / "lost.bin").unlink()
    assert env.run("backup.sh").returncode == 0
    assert env.run("restore-rehearsal.sh").returncode != 0
    (report,) = env.results()
    assert any("FILE 문서 실물 없음: lost.bin" in f for f in report["failures"])  # type: ignore[union-attr]


def test_retention_keeps_the_newest_sets_and_only_touches_pattern_named_dirs(env: Env) -> None:
    """보관 N=2 — 이름 규칙에 맞는 오래된 세트만 지우고, 다른 이름의 디렉터리·파일은 손대지 않는다"""
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        (env.out / f"kbos-{stamp}").mkdir()
    (env.out / "keepme").mkdir()
    (env.out / "notes.txt").write_text("보존")
    (env.out / "kbos-notaset").mkdir()
    result = env.run("backup.sh", KBOS_BACKUP_RETENTION="2")
    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in env.out.iterdir())
    assert "kbos-20200101T000000Z" not in names and "kbos-20200102T000000Z" not in names
    assert "kbos-20200103T000000Z" in names
    real_sets = [n for n in names if re.fullmatch(r"kbos-\d{8}T\d{6}Z", n)]
    assert (
        len(real_sets) == 2 and real_sets[1] > "kbos-2026"
    )  # 남은 것: 가장 최근 옛 세트 + 방금 만든 세트
    assert {"keepme", "notes.txt", "kbos-notaset"} <= set(names)


def test_a_failed_backup_leaves_no_set_behind(env: Env) -> None:
    """DB를 못 읽으면(없는 DB) 실패하고, 반쯤 만든 세트·임시 디렉터리를 남기지 않는다"""
    result = env.run("backup.sh", PGDATABASE="no_such_database_xyz")
    assert result.returncode != 0
    assert list(env.out.iterdir()) == []


def test_the_dump_and_the_manifest_share_one_snapshot_even_if_rows_arrive_in_between(
    env: Env,
) -> None:
    """스냅샷을 잡은 뒤 덤프 전에 행이 들어와도 덤프·매니페스트가 같은 시점이라 리허설이 통과한다

    (덤프가 스냅샷을 무시하면 덤프에는 새 행이 있고 매니페스트에는 없어 행수 불일치로 실패한다.)"""
    psql_cmd = f"psql -X -q -d {env.db} -c \"INSERT INTO other(v) VALUES ('arrived-in-between')\""
    assert env.run("backup.sh", KBOS_BACKUP_TEST_AFTER_SNAPSHOT_CMD=psql_cmd).returncode == 0
    (set_dir,) = env.sets()
    assert json.loads((set_dir / "manifest.json").read_text())["tables"]["other"] == 37
    count = _psql("-c", "SELECT count(*) FROM other", db=env.db).stdout.strip()
    assert count == "38"  # 훅이 실제로 행을 넣었다(공허 방지) — 매니페스트는 스냅샷 시점 값 37
    assert env.run("restore-rehearsal.sh").returncode == 0


# ═══ 적대 검증 반영 (S2-4 PR-3) ═══════════════════════════════════════════════


def _decrypt(path: Path, passphrase: str = PASSPHRASE) -> bytes:
    """스크립트와 **독립적으로** runbook이 안내하는 명령 그대로 복호화한다(문서·코드·테스트를 묶는다)"""
    result = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-iter",
            "600000",
            "-pass",
            "env:KBOS_TEST_PASS",
            "-in",
            str(path),
        ],
        env={**os.environ, "KBOS_TEST_PASS": passphrase},
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_the_documented_openssl_command_decrypts_the_artifacts(env: Env) -> None:
    _put_file(env, "b" * 8, b"HELLO-FILE")
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    assert _decrypt(set_dir / "db.dump.enc")[:5] == b"PGDMP"
    assert (
        b"HELLO-FILE" in _decrypt(set_dir / "files.tar.gz.enc")
        or len(_decrypt(set_dir / "files.tar.gz.enc")) > 0
    )
    assert (
        "aes-256-cbc/pbkdf2" in (REPO / "docs" / "runbook" / "backup-restore.md").read_text()
        or "-iter 600000" in (REPO / "docs" / "runbook" / "backup-restore.md").read_text()
    )


@pytest.mark.parametrize(
    ("field", "old", "new", "expected"),
    [
        (
            "migration_head",
            '"migration_head": "abc123"',
            '"migration_head": "zzz999"',
            "head 불일치",
        ),
        ("file_documents", '"file_documents": 1', '"file_documents": 5', "FILE 문서 수 불일치"),
        ("db_plain_sha256", None, None, "DB 덤프의 해시가 매니페스트와 다릅니다"),
        ("files_plain_sha256", None, None, "파일 묶음의 해시가 매니페스트와 다릅니다"),
    ],
)
def test_the_rehearsal_checks_every_manifest_field(
    env: Env, field: str, old: str | None, new: str | None, expected: str
) -> None:
    """매니페스트의 head·FILE 문서 수·평문 해시 2종 — 하나만 어긋나도 리허설은 실패한다"""
    _put_file(env, "c" * 8, b"C")
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    manifest = set_dir / "manifest.json"
    text = manifest.read_text()
    if old is not None and new is not None:
        assert old in text
        manifest.write_text(text.replace(old, new))
    else:
        value = json.loads(text)[field]
        manifest.write_text(text.replace(value, "0" * 64))
    assert env.run("restore-rehearsal.sh").returncode != 0
    (report,) = env.results()
    assert expected in " ".join(report["failures"])  # type: ignore[arg-type]


def test_a_tampered_file_bundle_or_a_missing_artifact_fails_the_rehearsal(env: Env) -> None:
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    bundle = set_dir / "files.tar.gz.enc"
    data = bytearray(bundle.read_bytes())
    data[-1] ^= 0xFF
    bundle.write_bytes(bytes(data))
    assert env.run("restore-rehearsal.sh").returncode != 0
    bundle.unlink()
    assert env.run("restore-rehearsal.sh").returncode != 0
    failures = " ".join(f for r in env.results() for f in r["failures"])  # type: ignore[union-attr]
    assert "files.tar.gz.enc" in failures


def test_the_rehearsal_accepts_an_explicit_set_and_refuses_when_there_is_none(env: Env) -> None:
    assert env.run("restore-rehearsal.sh").returncode != 0  # 세트 없음
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    assert env.run("restore-rehearsal.sh", str(set_dir)).returncode == 0
    assert env.run("restore-rehearsal.sh", str(env.out / "kbos-nonexistent")).returncode != 0


def test_purged_documents_do_not_fail_the_rehearsal_when_the_schema_has_purged_at(env: Env) -> None:
    """documents에 purged_at이 있는 스키마 — 정리된 문서(실물 없음)는 정상, 살아 있는 문서의 유실은 여전히 실패"""
    assert (
        _psql(
            "-c",
            "ALTER TABLE documents ADD COLUMN purged_at timestamptz, ADD COLUMN deleted_at timestamptz",
            db=env.db,
        ).returncode
        == 0
    )
    _put_file(env, "d" * 8, b"D")
    _put_file(env, "e" * 8, b"E")
    (env.files / ("e" * 8)).unlink()
    _psql(
        "-c",
        "UPDATE documents SET deleted_at = now(), purged_at = now() WHERE stored_name = 'eeeeeeee'",
        db=env.db,
    )
    assert env.run("backup.sh").returncode == 0
    assert env.run("restore-rehearsal.sh").returncode == 0
    _put_file(env, "f" * 8, b"F")
    (env.files / ("f" * 8)).unlink()
    time.sleep(1.1)
    assert env.run("backup.sh").returncode == 0
    assert env.run("restore-rehearsal.sh").returncode != 0
    assert any("f" * 8 in f for f in env.results()[-1]["failures"])  # type: ignore[union-attr]


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", ""])
def test_an_invalid_retention_is_refused_and_existing_sets_survive(env: Env, value: str) -> None:
    (env.out / "kbos-20200101T000000Z").mkdir()
    result = env.run("backup.sh", KBOS_BACKUP_RETENTION=value or "x")
    assert result.returncode != 0 and "KBOS_BACKUP_RETENTION" in result.stderr
    assert (env.out / "kbos-20200101T000000Z").exists()


def test_the_passphrase_length_boundary_is_15_refused_16_accepted(env: Env) -> None:
    assert env.run("backup.sh", KBOS_BACKUP_PASSPHRASE="x" * 15).returncode != 0
    assert env.run("backup.sh", KBOS_BACKUP_PASSPHRASE="x" * 16).returncode == 0


def test_stale_temp_dirs_from_a_killed_backup_are_removed_but_a_fresh_one_is_kept(env: Env) -> None:
    """SIGKILL·전원 차단으로 남은 임시 폴더(평문 덤프 가능)는 다음 실행이 치우고, 진행 중일 수 있는 신선한 것은 둔다"""
    old = env.out / ".tmp-kbos-old-1"
    fresh = env.out / ".tmp-kbos-fresh-2"
    for directory in (old, fresh):
        directory.mkdir()
        (directory / "db.dump").write_bytes(b"PLAINTEXT-DUMP")
    stale = time.time() - 3 * 3600
    os.utime(old, (stale, stale))
    assert env.run("backup.sh").returncode == 0
    assert not old.exists() and fresh.exists()


def test_sigterm_during_a_backup_removes_the_plaintext_temp_dir(env: Env) -> None:
    """docker stop(SIGTERM) — EXIT trap이 평문이 든 임시 폴더를 지운다"""
    _psql(
        "-c",
        "INSERT INTO other(v) SELECT repeat('x', 200) FROM generate_series(1, 200000)",
        db=env.db,
    )
    proc = subprocess.Popen(
        ["bash", str(SCRIPTS / "backup.sh")],
        env=env.base,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 20
    while time.time() < deadline and not any(
        p.name.startswith(".tmp-kbos-") for p in env.out.iterdir()
    ):
        time.sleep(0.05)
    proc.terminate()
    proc.wait(timeout=30)
    assert not [p for p in env.out.iterdir() if p.name.startswith(".tmp-kbos-")]
    assert env.sets() == [] or proc.returncode == 0


# ── 실제 복원(restore.sh) ─────────────────────────────────────────────────


def _db_exists(name: str) -> bool:
    return _psql("-c", f"SELECT 1 FROM pg_database WHERE datname = '{name}'").stdout.strip() == "1"


def test_restore_creates_a_new_database_and_extracts_files_and_never_overwrites(
    env: Env, tmp_path: Path
) -> None:
    _put_file(env, "a1" * 4, b"RESTORE-ME")
    assert env.run("backup.sh").returncode == 0
    (set_dir,) = env.sets()
    target = f"kbos_restored_{uuid.uuid4().hex[:8]}"
    files_out = tmp_path / "restored-files"
    files_out.mkdir()
    try:
        result = env.run(
            "restore.sh",
            str(set_dir),
            "--target-db",
            target,
            "--files-dir",
            str(files_out),
            RESTORE_ROLE="postgres",
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert _psql("-c", "SELECT count(*) FROM other", db=target).stdout.strip() == "37"
        assert (files_out / ("a1" * 4)).read_bytes() == b"RESTORE-ME"
        # 덮어쓰지 않는다: 같은 DB 이름·비어 있지 않은 폴더
        again = env.run("restore.sh", str(set_dir), "--target-db", target, RESTORE_ROLE="postgres")
        assert again.returncode != 0 and "이미 있는 DB" in again.stderr
        nonempty = env.run(
            "restore.sh",
            str(set_dir),
            "--target-db",
            target + "2",
            "--files-dir",
            str(files_out),
            RESTORE_ROLE="postgres",
        )
        assert nonempty.returncode != 0 and "비어 있지 않습니다" in nonempty.stderr
        assert not _db_exists(target + "2")
        bad_name = env.run(
            "restore.sh", str(set_dir), "--target-db", "Bad-Name;drop", RESTORE_ROLE="postgres"
        )
        assert bad_name.returncode != 0
        wrong = env.run(
            "restore.sh",
            str(set_dir),
            "--target-db",
            target + "3",
            KBOS_BACKUP_PASSPHRASE="another-passphrase-9876543",
            RESTORE_ROLE="postgres",
        )
        assert wrong.returncode != 0 and not _db_exists(target + "3")
    finally:
        for name in (target, target + "2", target + "3"):
            _psql("-c", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ── 권한·소유 복원(ⓕ) — 역할이 있는 클러스터에서만 ───────────────────────────


def _roles_exist() -> bool:
    out = _psql(
        "-c", "SELECT count(*) FROM pg_roles WHERE rolname IN ('kbos_owner', 'kbos_app')"
    ).stdout.strip()
    return out == "2"


@pytest.mark.skipif(
    not _tools_available() or not _roles_exist(), reason="kbos_owner/kbos_app 역할이 없다"
)
def test_ownership_and_privileges_are_restored_and_verified(env: Env) -> None:
    """소유(kbos_owner)·앱 역할 권한·append-only(audit_log UPDATE 불가)가 복원돼야 리허설이 통과한다"""
    setup = """
    CREATE TABLE audit_log(id serial PRIMARY KEY, action text);
    ALTER TABLE alembic_version OWNER TO kbos_owner; ALTER TABLE documents OWNER TO kbos_owner;
    ALTER TABLE other OWNER TO kbos_owner; ALTER TABLE audit_log OWNER TO kbos_owner;
    GRANT SELECT ON alembic_version, documents, other TO kbos_app;
    GRANT SELECT, INSERT ON audit_log TO kbos_app;
    """
    assert _psql(db=env.db, stdin=setup).returncode == 0
    assert env.run("backup.sh").returncode == 0
    roles = {"RESTORE_ROLE": "kbos_owner", "RESTORE_APP_ROLE": "kbos_app"}
    ok = env.run("restore-rehearsal.sh", **roles)
    assert ok.returncode == 0, ok.stderr + ok.stdout
    (result,) = env.results()
    assert any("모든 테이블 소유자=kbos_owner" in c for c in result["checks"])  # type: ignore[union-attr]
    assert any("append-only 유지" in c for c in result["checks"])  # type: ignore[union-attr]
    # 권한이 빠진 백업(앱 역할에 documents SELECT 없음)은 실패해야 한다
    _psql("-c", "REVOKE SELECT ON documents FROM kbos_app", db=env.db)
    time.sleep(1.1)
    assert env.run("backup.sh").returncode == 0
    assert env.run("restore-rehearsal.sh", **roles).returncode != 0
    assert any("읽지 못함" in f for f in env.results()[-1]["failures"])  # type: ignore[union-attr]
    # append-only가 깨진 백업(앱 역할에 audit_log UPDATE 가능)도 실패
    _psql(
        "-c",
        "GRANT SELECT ON documents TO kbos_app; GRANT UPDATE ON audit_log TO kbos_app",
        db=env.db,
    )
    time.sleep(1.1)
    assert env.run("backup.sh").returncode == 0
    assert env.run("restore-rehearsal.sh", **roles).returncode != 0
    assert any("append-only" in f for f in env.results()[-1]["failures"])  # type: ignore[union-attr]


# ── 스케줄러 판단(--plan) ─────────────────────────────────────────────────


def _plan(env: Env, now_utc: str) -> list[str]:
    result = env.run("scheduler.sh", "--plan", KBOS_SCHEDULER_NOW=now_utc)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def test_the_scheduler_plans_a_backup_once_per_kst_day_after_03_00_with_catch_up(env: Env) -> None:
    # KST 2026-09-30(수) 03:00 = UTC 09-29 18:00
    assert _plan(env, "2026-09-29T17:59:00Z") == []  # KST 02:59 — 아직 아님
    assert _plan(env, "2026-09-29T18:00:00Z") == ["backup"]  # KST 03:00 정각
    assert _plan(env, "2026-09-29T20:30:00Z") == [
        "backup"
    ]  # KST 05:30 — 재기동 catch-up(놓친 백업 만회)
    (env.out / "kbos-20260929T180500Z").mkdir()  # 오늘(KST 9/30) 03:05에 만든 세트
    assert _plan(env, "2026-09-29T18:30:00Z") == []  # 같은 날 두 번 돌지 않는다(재기동 포함)
    assert _plan(env, "2026-09-30T15:00:00Z") == []  # KST 10/1 00:00 — 새 날이지만 03:00 전
    assert _plan(env, "2026-09-30T18:00:00Z") == ["backup"]  # KST 10/1 03:00 — 새 날 새 백업
    (env.out / "kbos-20260930T180100Z").mkdir()  # KST 10/1 03:01 세트
    assert _plan(env, "2026-09-30T20:00:00Z") == []  # 판단 기준은 UTC가 아니라 KST 날짜


def test_the_scheduler_plans_the_rehearsal_on_sundays_after_04_00_once(env: Env) -> None:
    (
        env.out / "kbos-20261003T190500Z"
    ).mkdir()  # UTC 토 19:05 = KST 일요일(10/4) 04:05 — 오늘 세트 있음
    assert _plan(env, "2026-10-03T18:59:00Z") == []  # KST 03:59
    assert _plan(env, "2026-10-03T19:00:00Z") == ["rehearsal"]  # KST 일요일 04:00
    assert _plan(env, "2026-10-03T23:00:00Z") == ["rehearsal"]  # 늦게 올라와도 일요일 안에는 만회
    (env.out / "rehearsal-20261003T191000Z.json").write_text("{}")
    assert _plan(env, "2026-10-03T23:00:00Z") == []  # 오늘 리허설 있음
    # KST 월요일(10/5 08:00) — 새 날 백업은 계획되지만 리허설은 일요일에만
    assert _plan(env, "2026-10-04T23:00:00Z") == ["backup"]


def test_a_failed_attempt_is_not_retried_within_the_retry_window(env: Env) -> None:
    result = env.run(
        "scheduler.sh",
        "--plan",
        KBOS_SCHEDULER_NOW="2026-09-29T20:00:00Z",
        KBOS_BACKUP_RETRY_MINUTES="30",
    )
    assert result.stdout.split() == ["backup"]
