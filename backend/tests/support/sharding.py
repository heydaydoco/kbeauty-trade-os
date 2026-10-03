"""CI 테스트 샤드 분배 — 테스트 **파일** 단위·LPT greedy·결정적 배정 (ADR-0073).

환경변수 ``KBOS_TEST_SHARD="i/N"``이 있을 때만 켜진다. 없거나 빈 값이면 아무것도 하지 않는다 —
로컬 ``pytest``는 지금처럼 전체를 돈다.

★ 파일 단위로 묶는다: 모듈 스코프 픽스처와 파일 안 실행 순서가 그대로 보존된다(샤드 안에서도
  수집 순서를 바꾸지 않고 걸러내기만 한다).
★ 결정적: 모든 러너가 같은 커밋에서 같은 수집 결과를 보므로 같은 배정을 계산한다. 가중치는 정수
  밀리초, 동률은 파일 경로·샤드 번호로 깬다 — 부동소수 누적·dict 순서에 기대지 않는다.
★ 균형: ``tests/.shard_durations.json``(파일별 실측 초)으로 LPT(무거운 파일부터 가장 가벼운 샤드에).
  파일에 없는 테스트 파일(새로 생긴 파일)은 ``테스트 수 × seconds_per_test`` 추정치로 넣는다.
  소요 시간 파일이 낡으면 균형만 나빠질 뿐 **완전성은 영향받지 않는다**.
★ 완전성(누락·중복 0): 배정은 수집된 파일 전부의 분할(partition)이다. 단위 시험
  (tests/unit/test_sharding.py)과 CI backend-coverage 잡의 대조(``shard_tools verify`` — 각 샤드
  보고서의 합집합 = 플러그인 없이 돌린 독립 ``--collect-only``)가 이중으로 고정한다.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SHARD_ENV = "KBOS_TEST_SHARD"
#: 이 경로에 샤드 보고서(JSON)를 쓴다 — CI가 아티팩트로 올려 backend-coverage 잡이 대조한다.
REPORT_ENV = "KBOS_TEST_SHARD_REPORT"
PLUGIN_NAME = "kbos-shard"
DURATIONS_FILE = Path(__file__).resolve().parents[1] / ".shard_durations.json"
#: 소요 시간 파일이 없을 때의 테스트당 추정 초(있으면 파일의 seconds_per_test가 우선).
DEFAULT_SECONDS_PER_TEST = 0.5

_SPEC = re.compile(r"\s*(\d+)\s*/\s*(\d+)\s*")


class ShardConfigError(ValueError):
    """샤드 지정·소요 시간 파일이 잘못됐다 — 조용히 전체 실행으로 넘어가지 않고 실패한다."""


@dataclass(frozen=True)
class ShardSpec:
    index: int  # 1부터
    total: int

    def __str__(self) -> str:
        return f"{self.index}/{self.total}"


def parse_shard_spec(raw: str) -> ShardSpec:
    match = _SPEC.fullmatch(raw)
    if match is None:
        raise ShardConfigError(f"{SHARD_ENV}={raw!r} — 'i/N' 형식이어야 합니다(예: 2/3).")
    index, total = int(match.group(1)), int(match.group(2))
    if total < 1 or not 1 <= index <= total:
        raise ShardConfigError(f"{SHARD_ENV}={raw!r} — 1 ≤ i ≤ N 이어야 합니다.")
    return ShardSpec(index, total)


def shard_spec_from_env(environ: Mapping[str, str]) -> ShardSpec | None:
    raw = environ.get(SHARD_ENV, "")
    if not raw.strip():
        return None
    return parse_shard_spec(raw)


@dataclass(frozen=True)
class Durations:
    files: Mapping[str, float]
    seconds_per_test: float = DEFAULT_SECONDS_PER_TEST


def load_durations(path: Path) -> Durations:
    """소요 시간 파일을 읽는다. 없으면 전부 추정치로, 깨져 있으면 실패한다(오타가 균형을 조용히 망치지 않게)."""
    if not path.exists():
        return Durations(files={})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data["files"]
        per_test = float(data.get("seconds_per_test", DEFAULT_SECONDS_PER_TEST))
        if not isinstance(files, dict) or per_test <= 0:
            raise TypeError
        parsed = {str(name): float(seconds) for name, seconds in files.items()}
    except (ValueError, KeyError, TypeError) as exc:
        raise ShardConfigError(f"소요 시간 파일 {path}가 올바르지 않습니다: {exc!r}") from exc
    if any(seconds < 0 for seconds in parsed.values()):
        raise ShardConfigError(f"소요 시간 파일 {path}에 음수 소요 시간이 있습니다.")
    return Durations(files=parsed, seconds_per_test=per_test)


def file_weights(test_counts: Mapping[str, int], durations: Durations) -> dict[str, int]:
    """파일별 가중치(정수 밀리초). 소요 시간 파일에 있으면 실측, 없으면 테스트 수 × 테스트당 초."""
    weights: dict[str, int] = {}
    for path, count in test_counts.items():
        seconds = durations.files.get(path)
        if seconds is None:
            seconds = count * durations.seconds_per_test
        weights[path] = round(seconds * 1000)
    return weights


def assign_files(weights: Mapping[str, int], total: int) -> list[list[str]]:
    """LPT greedy — 무거운 파일부터 현재 가장 가벼운 샤드에 넣는다. 반환: 샤드별 파일 목록(정렬)."""
    if total < 1:
        raise ShardConfigError("샤드 수는 1 이상이어야 합니다.")
    loads = [0] * total
    buckets: list[list[str]] = [[] for _ in range(total)]
    for path in sorted(weights, key=lambda name: (-weights[name], name)):
        target = min(range(total), key=lambda k: (loads[k], k))
        buckets[target].append(path)
        loads[target] += weights[path]
    return [sorted(bucket) for bucket in buckets]


def item_file(item: pytest.Item, rootpath: Path) -> str:
    """아이템이 속한 테스트 파일(rootdir 기준 posix 경로). nodeid는 conftest가 설명을 덧붙여 쓰지 않는다."""
    try:
        return item.path.relative_to(rootpath).as_posix()
    except ValueError:
        return item.path.as_posix()


@dataclass
class ShardAssignment:
    spec: ShardSpec
    test_counts: dict[str, int]
    weights: dict[str, int]
    buckets: list[list[str]]
    estimated_files: list[str] = field(default_factory=list)

    @property
    def assigned(self) -> list[str]:
        return self.buckets[self.spec.index - 1]

    def report(self) -> dict[str, object]:
        assigned = self.assigned
        return {
            "shard": str(self.spec),
            "index": self.spec.index,
            "total": self.spec.total,
            "collected_files": sorted(self.test_counts),
            "collected_tests": sum(self.test_counts.values()),
            "assigned_files": assigned,
            "assigned_tests": sum(self.test_counts[name] for name in assigned),
            "estimated_seconds": round(sum(self.weights[name] for name in assigned) / 1000, 1),
            "estimated_seconds_by_shard": [
                round(sum(self.weights[name] for name in bucket) / 1000, 1)
                for bucket in self.buckets
            ],
            "files_without_measured_duration": self.estimated_files,
        }


def plan(spec: ShardSpec, files_in_order: Sequence[str], durations: Durations) -> ShardAssignment:
    counts = dict(Counter(files_in_order))
    weights = file_weights(counts, durations)
    return ShardAssignment(
        spec=spec,
        test_counts=counts,
        weights=weights,
        buckets=assign_files(weights, spec.total),
        estimated_files=sorted(name for name in counts if name not in durations.files),
    )


class ShardPlugin:
    """``conftest.pytest_configure``가 환경변수가 있을 때만 등록한다."""

    def __init__(self, spec: ShardSpec, durations: Durations, *, report_path: Path | None) -> None:
        self.spec = spec
        self.durations = durations
        self.report_path = report_path
        self.assignment: ShardAssignment | None = None

    # tryfirst: -k·-m·--deselect보다 먼저 **전체 수집 결과**로 배정한다 — 필터가 배정을 흔들지 않게.
    # 그룹 마커 검사(conftest)는 그 뒤 각 샤드의 아이템에 돈다 — 파일은 정확히 한 샤드에 가므로 전체가 검사된다.
    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        files = [item_file(item, config.rootpath) for item in items]
        self.assignment = plan(self.spec, files, self.durations)
        mine = set(self.assignment.assigned)
        selected = [item for item, name in zip(items, files, strict=True) if name in mine]
        deselected = [item for item, name in zip(items, files, strict=True) if name not in mine]
        if deselected:
            config.hook.pytest_deselected(items=deselected)
        items[:] = selected
        if self.report_path is not None:
            self.report_path.write_text(
                json.dumps(self.assignment.report(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def pytest_report_collectionfinish(self) -> list[str]:
        if self.assignment is None:
            return []
        report = self.assignment.report()
        return [
            f"[샤드 {self.spec}] 파일 {len(self.assignment.assigned)}/{len(self.assignment.test_counts)}개 · "
            f"테스트 {report['assigned_tests']}/{report['collected_tests']}건 · "
            f"예상 {report['estimated_seconds']}초 (샤드별 {report['estimated_seconds_by_shard']}) · "
            f"소요 시간 미등재 파일 {len(self.assignment.estimated_files)}개(테스트 수로 추정)"
        ]


def register_from_env(
    config: pytest.Config,
    environ: Mapping[str, str] = os.environ,
    durations_path: Path = DURATIONS_FILE,
) -> None:
    """환경변수가 있을 때만 샤드 플러그인을 등록한다. 지정·소요 시간 파일 오류는 사용 오류로 즉시 멈춘다."""
    try:
        spec = shard_spec_from_env(environ)
        if spec is None:
            return
        durations = load_durations(durations_path)
    except ShardConfigError as exc:
        raise pytest.UsageError(str(exc)) from exc
    report = environ.get(REPORT_ENV, "").strip()
    config.pluginmanager.register(
        ShardPlugin(spec, durations, report_path=Path(report) if report else None), PLUGIN_NAME
    )
