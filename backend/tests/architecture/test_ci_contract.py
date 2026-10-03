"""K. 보안·품질 — CI 워크플로 계약 (DESIGN.md §18.3).

게이트가 조용히 무력화되는 경로를 막는다.
  ① ci-ok 잡의 needs에서 잡을 빠뜨리면, 그 잡이 빨개도 게이트는 초록이 된다.
  ② timeout-minutes가 없는 잡은 행이 걸리면 기본 360분을 태워 무료 분을 소진한다.
  ③ 테스트 샤드 분할(ADR-0072): 샤드가 빠지거나 커버리지 병합 없이 94% 게이트가 사라지거나,
     린트·타입·드라이런이 샤드 분리 중에 누락되면 "CI 초록"이 일부 검사만 돈 결과가 된다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

# CI(리포 전체 체크아웃)에서는 parents[3]에 있고, dev 컨테이너에서는 ./backend만
# 마운트되므로 compose가 /repo/.github로 read-only 마운트해 준다.
_CANDIDATES = [
    Path("/repo/.github/workflows/ci.yml"),
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml",
]
CI_YAML = next((p for p in _CANDIDATES if p.exists()), None)
GATE_JOB = "ci-ok"
#: 테스트 샤드 매트릭스 잡과 커버리지 병합·게이트 잡 (ADR-0072)
BACKEND_JOB = "backend"
COVERAGE_JOB = "backend-coverage"


@pytest.fixture(scope="module")
def workflow() -> dict:
    if CI_YAML is None:
        pytest.fail(
            "ci.yml을 찾을 수 없습니다. dev 컨테이너라면 compose가 /repo/.github를 "
            "마운트하는지 확인하세요(docker-compose.yml api 서비스)."
        )
    # GitHub Actions의 'on' 키가 YAML에서 True로 파싱되는 것 등은 신경 쓰지 않는다.
    return yaml.safe_load(CI_YAML.read_text(encoding="utf-8"))


def test_ci_file_exists() -> None:
    """CI 워크플로 파일이 존재한다"""
    assert CI_YAML is not None and CI_YAML.exists()


def test_gate_job_exists(workflow: dict) -> None:
    """단일 게이트 잡(ci-ok)이 있다"""
    assert GATE_JOB in workflow["jobs"]


def test_gate_needs_every_other_job(workflow: dict) -> None:
    """ci-ok의 needs가 다른 모든 잡을 빠짐없이 포함한다 (무음 통과 차단)"""
    jobs = workflow["jobs"]
    other_jobs = {name for name in jobs if name != GATE_JOB}
    gate_needs = set(jobs[GATE_JOB].get("needs", []))
    missing = other_jobs - gate_needs
    assert not missing, (
        f"ci-ok.needs에서 빠진 잡: {sorted(missing)}. "
        "이 잡이 빨개도 게이트가 초록이 되어 실패가 병합된다."
    )


def test_every_job_has_timeout(workflow: dict) -> None:
    """모든 잡에 timeout-minutes가 있다 (행 걸림으로 무료 분 소진 방지)"""
    no_timeout = [name for name, job in workflow["jobs"].items() if "timeout-minutes" not in job]
    assert not no_timeout, f"timeout-minutes 없는 잡: {no_timeout}"


def test_triggers_on_push(workflow: dict) -> None:
    """push마다 실행된다 (§18.3)"""
    # 'on'은 YAML에서 True로 파싱될 수 있어 두 키를 모두 본다.
    triggers = workflow.get("on") or workflow.get(True)
    assert triggers is not None
    assert "push" in triggers


def _runs(job: dict) -> list[str]:
    return [step.get("run", "") for step in job.get("steps", [])]


def _all_runs(workflow: dict) -> dict[str, str]:
    return {name: "\n".join(_runs(job)) for name, job in workflow["jobs"].items()}


def test_coverage_gate_is_armed(workflow: dict) -> None:
    """병합 커버리지에 94% 게이트가 걸려 있다 (ADR-0031 채택 — 94%, ADR-0072 샤드 병합)

    게이트를 제거·완화하는 커밋이 조용히 통과하면 커버리지 하락을 다시 아무도
    못 본다. 임계 조정은 ADR-0031의 실측 절차(전체 실행 → 실측−2%p) 재적용 +
    이 테스트 갱신이 세트다. 샤드는 각자 일부만 돌아 개별 게이트가 무의미하므로
    backend-coverage 잡이 전 샤드 데이터를 combine한 **뒤** 게이트를 건다.
    """
    runs = _runs(workflow["jobs"][COVERAGE_JOB])
    gate = [run for run in runs if "coverage report" in run]
    assert gate, f"{COVERAGE_JOB} 잡에 coverage report 스텝이 없습니다."
    assert any("--fail-under=94" in run for run in gate), (
        f"{COVERAGE_JOB} 잡의 coverage report에 --fail-under=94가 없습니다 (ADR-0031 채택 문면). "
        "임계를 바꾸려면 ADR-0031 절차를 따르고 이 테스트를 함께 갱신하세요."
    )
    combined = "\n".join(gate)
    assert "coverage combine" in combined, (
        "샤드 데이터를 병합(coverage combine)하지 않고 게이트를 건다."
    )
    assert combined.index("coverage combine") < combined.index("coverage report"), (
        "게이트는 전 샤드 데이터를 병합(coverage combine)한 뒤에 걸어야 합니다."
    )


# ── 테스트 샤드 분할 (ADR-0072) ────────────────────────────────────────────
def test_backend_is_a_shard_matrix(workflow: dict) -> None:
    """backend 잡은 shard 1..N(N≥2) 매트릭스이고, 샤드 지정 N은 매트릭스 크기에서 온다"""
    backend = workflow["jobs"][BACKEND_JOB]
    matrix = backend["strategy"]["matrix"]
    # 다른 축(include·exclude 포함)이 끼면 job-total ≠ 샤드 수가 되어 일부 샤드가 영영 안 돈다.
    assert set(matrix) == {"shard"}, f"매트릭스 축은 shard 하나여야 합니다: {sorted(matrix)}"
    shards = matrix["shard"]
    assert shards == list(range(1, len(shards) + 1)) and len(shards) >= 2, shards
    assert backend["env"].get("KBOS_TEST_SHARD") == "${{ matrix.shard }}/${{ strategy.job-total }}"
    assert backend["strategy"].get("fail-fast") is False, (
        "한 샤드 실패가 다른 샤드 결과를 가리면 안 됩니다."
    )


def test_every_shard_collects_coverage_into_its_own_data_file(workflow: dict) -> None:
    """샤드마다 --cov=app로 고유 데이터 파일(.coverage.shard-i)을 남기고 숨김 파일까지 업로드한다"""
    backend = workflow["jobs"][BACKEND_JOB]
    assert backend["env"].get("COVERAGE_FILE") == ".coverage.shard-${{ matrix.shard }}"
    pytest_runs = [run for run in _runs(backend) if run.lstrip().startswith("pytest")]
    assert len(pytest_runs) == 1, pytest_runs
    assert "--cov=app" in pytest_runs[0]
    # 부분 커버리지에 게이트를 걸면 샤드가 전부 빨개진다 — 게이트는 병합 잡의 몫.
    assert "--cov-fail-under" not in pytest_runs[0]
    uploads = [
        s for s in backend["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert len(uploads) == 1
    upload = uploads[0]["with"]
    assert upload.get("include-hidden-files") is True, (
        ".coverage.*는 숨김 파일 — 없으면 업로드에서 빠진다."
    )
    assert upload.get("if-no-files-found") == "error"
    junit_match = re.search(r"--junitxml=(\S.*?\.xml)", pytest_runs[0])
    assert junit_match, "샤드 pytest에 --junitxml이 없습니다(소요 시간 갱신본의 원천)."
    junit = junit_match.group(1)
    for needed in (
        backend["env"]["COVERAGE_FILE"],
        backend["env"]["KBOS_TEST_SHARD_REPORT"],
        junit,
    ):
        assert "${{ matrix.shard }}" in needed, (
            f"{needed}: 샤드마다 이름이 달라야 병합 때 덮어쓰지 않는다."
        )
        assert f"backend/{needed}" in upload["path"], f"샤드 아티팩트에 {needed}가 없습니다."


def test_coverage_job_needs_every_shard_and_verifies_completeness(workflow: dict) -> None:
    """병합 잡은 backend(매트릭스 전체) 뒤에 돌고, 샤드 합집합 = 독립 전체 수집을 대조한다"""
    job = workflow["jobs"][COVERAGE_JOB]
    needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
    assert BACKEND_JOB in needs
    assert "if" not in job, "샤드가 실패해도 병합 잡이 돌면 부분 데이터로 게이트를 통과할 수 있다."
    runs = "\n".join(_runs(job))
    assert "pytest --collect-only -q" in runs
    assert "tests.support.shard_tools verify" in runs
    assert runs.index("shard_tools verify") < runs.index("coverage report"), (
        "완전성 대조가 게이트보다 먼저여야 합니다(빠진 샤드로 게이트를 통과하지 않게)."
    )
    downloads = [
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact")
    ]
    assert downloads and downloads[0]["with"]["pattern"] == "backend-shard-*"


def test_gate_needs_backend_shards_and_coverage_merge(workflow: dict) -> None:
    """ci-ok가 backend(전 샤드)와 backend-coverage를 needs로 가진다"""
    needs = set(workflow["jobs"][GATE_JOB]["needs"])
    assert {BACKEND_JOB, COVERAGE_JOB} <= needs


def test_gate_checks_the_result_of_every_need(workflow: dict) -> None:
    """ci-ok 스크립트가 needs 전부의 결과를 본다 (needs에 넣고 결과 검사에서 빠뜨리는 무음 통과 차단)"""
    gate = workflow["jobs"][GATE_JOB]
    assert gate.get("if") == "always()", "선행 잡이 실패해도 ci-ok가 돌아 실패로 끝나야 합니다."
    script = "\n".join(_runs(gate))
    if "needs.*.result" not in script:
        unchecked = [n for n in gate["needs"] if f"needs.{n}.result" not in script]
        assert not unchecked, f"ci-ok가 결과를 보지 않는 잡: {unchecked}"


def test_script_tests_are_required_in_every_shard(workflow: dict) -> None:
    """KBOS_REQUIRE_SCRIPT_TESTS=1과 pg_dump 16 준비가 모든 샤드에 적용된다 (skip으로 조용히 사라지지 않게)"""
    backend = workflow["jobs"][BACKEND_JOB]
    assert backend["env"].get("KBOS_REQUIRE_SCRIPT_TESTS") == "1", (
        "모든 샤드에 KBOS_REQUIRE_SCRIPT_TESTS=1이 있어야 스크립트 시험이 skip으로 사라지지 않는다."
    )
    pg_steps = [s for s in backend["steps"] if "pg_dump" in s.get("run", "")]
    assert pg_steps, "backend 잡에 pg_dump 준비 스텝이 없습니다."
    assert all("if" not in s for s in pg_steps), (
        "pg_dump 준비는 샤드 조건 없이 전 샤드에서 돌아야 합니다."
    )
    assert any("postgresql-client-16" in s["run"] for s in pg_steps)


@pytest.mark.parametrize("job_name", [BACKEND_JOB, "backend-checks"])
def test_ci_database_runs_without_fsync(workflow: dict, job_name: str) -> None:
    """DB를 쓰는 백엔드 잡은 CI 전용 fsync off로 부트스트랩한다 (PR #42 느린 러너 선례)"""
    runs = "\n".join(_runs(workflow["jobs"][job_name]))
    for setting in ("fsync = off", "synchronous_commit = off", "full_page_writes = off"):
        assert setting in runs, f"{job_name}: {setting} 없음"
    assert "infra/postgres/init/*.sql" in runs


#: 샤드 분리 전 backend 잡이 돌던 정적 검사·드라이런 — 하나도 빠지지 않고, 샤드마다 중복되지 않는다.
_SINGLE_RUN_CHECKS = (
    "ruff check .",
    "ruff format --check .",
    "mypy app",
    "alembic heads",
    "alembic upgrade head",
    "alembic downgrade base",
    "alembic check",
)


@pytest.mark.parametrize("command", _SINGLE_RUN_CHECKS)
def test_static_checks_and_migration_dryrun_run_exactly_once(workflow: dict, command: str) -> None:
    """린트·타입·마이그레이션 드라이런 4종이 정확히 한 잡(매트릭스 아님)에서 돈다 (§18.3)"""
    holders = [name for name, runs in _all_runs(workflow).items() if command in runs]
    assert len(holders) == 1, f"{command!r}를 도는 잡: {holders} (정확히 1개여야 함)"
    assert "strategy" not in workflow["jobs"][holders[0]], f"{command!r}가 샤드마다 중복 실행된다."
