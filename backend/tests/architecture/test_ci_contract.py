"""K. 보안·품질 — CI 워크플로 계약 (DESIGN.md §18.3).

게이트가 조용히 무력화되는 두 가지를 막는다.
  ① ci-ok 잡의 needs에서 잡을 빠뜨리면, 그 잡이 빨개도 게이트는 초록이 된다.
  ② timeout-minutes가 없는 잡은 행이 걸리면 기본 360분을 태워 무료 분을 소진한다.
"""

from __future__ import annotations

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


def test_coverage_gate_is_armed(workflow: dict) -> None:
    """backend 잡의 pytest에 커버리지 게이트가 걸려 있다 (ADR-0031 채택 — 94%)

    게이트를 제거·완화하는 커밋이 조용히 통과하면 커버리지 하락을 다시 아무도
    못 본다. 임계 조정은 ADR-0031의 실측 절차(전체 실행 → 실측−2%p) 재적용 +
    이 테스트 갱신이 세트다.
    """
    steps = workflow["jobs"]["backend"]["steps"]
    pytest_runs = [step.get("run", "") for step in steps if "pytest" in step.get("run", "")]
    assert pytest_runs, "backend 잡에 pytest 스텝이 없습니다."
    assert any("--cov-fail-under=94" in run for run in pytest_runs), (
        "backend 잡의 pytest에 --cov-fail-under=94가 없습니다 (ADR-0031 채택 문면). "
        "임계를 바꾸려면 ADR-0031 절차를 따르고 이 테스트를 함께 갱신하세요."
    )
