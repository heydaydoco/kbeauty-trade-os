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


pytestmark.append(  # type: ignore[attr-defined]
    pytest.mark.skipif(not _tools_available(), reason="pg_dump/openssl 또는 슈퍼유저 접속이 없다")
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
    """백업 → 복원 리허설이 통과하고, 산출물에는 평문 본문이 없다(모드 600/700·매니페스트 정합)"""
    _put_file(env, "a" * 8, b"PLAINTEXT-MARKER-FILE-BODY")
    backup = env.run("backup.sh")
    assert backup.returncode == 0, backup.stderr
    (set_dir,) = env.sets()
    assert oct(set_dir.stat().st_mode & 0o777) == "0o700"
    for name in ("db.dump.enc", "files.tar.gz.enc", "manifest.json"):
        assert oct((set_dir / name).stat().st_mode & 0o777) == "0o600", name
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
    assert env.run("restore-rehearsal.sh").returncode == 0
