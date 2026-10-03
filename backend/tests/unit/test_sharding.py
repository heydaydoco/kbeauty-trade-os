"""K. 보안·품질 — CI 테스트 샤드 분배의 완전성(누락·중복 0)·결정성 (ADR-0072).

샤드 분할이 조용히 틀리면 "CI 초록"이 일부 테스트만 돈 결과가 된다 — 가장 늦게 발견되는 게이트
무력화다. 여기서는 배정 함수·플러그인·CI 대조 도구를 가짜 파일 목록으로 고정하고, CI의
backend-coverage 잡이 실제 샤드 보고서를 독립 ``--collect-only``와 대조한다(이중 방어).
"""

from __future__ import annotations

import json
import os
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.support import shard_tools
from tests.support.sharding import (
    DURATIONS_FILE,
    PLUGIN_NAME,
    SHARD_ENV,
    Durations,
    ShardConfigError,
    ShardPlugin,
    ShardSpec,
    assign_files,
    file_weights,
    load_durations,
    parse_shard_spec,
    plan,
    register_from_env,
    shard_spec_from_env,
)

pytestmark = [pytest.mark.group_k, pytest.mark.meta]


def _fake_files(count: int, seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    # 동률 가중치도 섞는다(결정성은 동률 처리에서 깨지기 쉽다).
    return {
        f"tests/d{i % 4}/test_{i:03d}.py": rng.choice([0, 5, 5, 120, 900, 4000])
        for i in range(count)
    }


# ── 지정 파싱 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "index", "total"), [("1/3", 1, 3), (" 2 / 3 ", 2, 3), ("1/1", 1, 1), ("4/4", 4, 4)]
)
def test_valid_shard_specs_parse(raw: str, index: int, total: int) -> None:
    """'i/N'(1 ≤ i ≤ N)을 읽는다"""
    assert parse_shard_spec(raw) == ShardSpec(index, total)


@pytest.mark.parametrize("raw", ["0/3", "4/3", "3", "a/b", "1/0", "-1/3", "1/3/5", "1 3"])
def test_invalid_shard_specs_fail_instead_of_running_everything(raw: str) -> None:
    """잘못된 지정은 실패한다 — 조용히 전체 실행(또는 0건)으로 넘어가지 않는다"""
    with pytest.raises(ShardConfigError):
        parse_shard_spec(raw)


@pytest.mark.parametrize("environ", [{}, {SHARD_ENV: ""}, {SHARD_ENV: "   "}])
def test_without_the_env_var_sharding_is_off(environ: dict[str, str]) -> None:
    """환경변수가 없거나 비면 꺼진다 — 로컬 pytest는 전체 그대로"""
    assert shard_spec_from_env(environ) is None


@dataclass
class _FakePluginManager:
    registered: list[tuple[object, str]] = field(default_factory=list)

    def register(self, plugin: object, name: str) -> None:
        self.registered.append((plugin, name))


@dataclass
class _FakeConfig:
    pluginmanager: _FakePluginManager = field(default_factory=_FakePluginManager)


def test_register_is_a_no_op_without_the_env_var() -> None:
    """환경변수가 없으면 플러그인을 등록하지 않는다"""
    config = _FakeConfig()
    register_from_env(config, {})
    assert config.pluginmanager.registered == []


def test_register_adds_the_plugin_with_the_report_path(tmp_path: Path) -> None:
    """'2/3'이면 샤드 플러그인을 등록하고 보고서 경로를 넘긴다"""
    config = _FakeConfig()
    report = tmp_path / "r.json"
    register_from_env(
        config, {SHARD_ENV: "2/3", "KBOS_TEST_SHARD_REPORT": str(report)}, tmp_path / "none.json"
    )
    [(plugin, name)] = config.pluginmanager.registered
    assert name == PLUGIN_NAME
    assert isinstance(plugin, ShardPlugin)
    assert plugin.spec == ShardSpec(2, 3)
    assert plugin.report_path == report


@pytest.mark.parametrize(
    "content", ["{", '{"files": []}', '{"seconds_per_test": 1}', '{"files": {"a.py": -1}}']
)
def test_a_broken_durations_file_is_a_usage_error(tmp_path: Path, content: str) -> None:
    """소요 시간 파일이 깨져 있으면 사용 오류로 멈춘다(균형이 조용히 망가지지 않게)"""
    path = tmp_path / "d.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(pytest.UsageError):
        register_from_env(_FakeConfig(), {SHARD_ENV: "1/2"}, path)


@pytest.mark.parametrize("raw", ["0/3", "x"])
def test_a_bad_spec_from_env_is_a_usage_error(raw: str) -> None:
    """환경변수 오타는 사용 오류다"""
    with pytest.raises(pytest.UsageError):
        register_from_env(_FakeConfig(), {SHARD_ENV: raw})


# ── 배정: 분할·결정성·균형 ───────────────────────────────────────────────────
@pytest.mark.parametrize("count", [0, 1, 2, 5, 37, 190])
@pytest.mark.parametrize("total", [1, 2, 3, 4, 6])
def test_every_file_lands_in_exactly_one_shard(count: int, total: int) -> None:
    """모든 i/N의 합집합 = 전체, 교집합 = ∅ (누락·중복 0)"""
    weights = _fake_files(count, seed=count * 31 + total)
    buckets = assign_files(weights, total)
    assert len(buckets) == total
    flat = [name for bucket in buckets for name in bucket]
    assert sorted(flat) == sorted(weights)  # 합집합 = 전체
    assert len(flat) == len(set(flat))  # 교집합 = ∅


def test_assignment_is_deterministic_regardless_of_input_order() -> None:
    """입력 순서·반복 호출과 무관하게 같은 배정 — 러너마다 같은 결과"""
    weights = _fake_files(120, seed=7)
    expected = assign_files(weights, 3)
    for seed in range(5):
        items = list(weights.items())
        random.Random(seed).shuffle(items)
        assert assign_files(dict(items), 3) == expected


@pytest.mark.parametrize("total", [2, 3, 4])
def test_lpt_balance_is_within_the_greedy_bound(total: int) -> None:
    """균형: 최대 샤드 ≤ 평균 + 가장 큰 파일 (greedy 상한 — 한 샤드에 몰리지 않는다)"""
    weights = _fake_files(190, seed=total)
    loads = [sum(weights[n] for n in bucket) for bucket in assign_files(weights, total)]
    assert max(loads) <= sum(weights.values()) / total + max(weights.values())


def test_unmeasured_files_are_estimated_from_their_test_count() -> None:
    """소요 시간 파일에 없는 파일은 테스트 수 × 테스트당 초로 추정한다"""
    durations = Durations(files={"tests/a.py": 10.0}, seconds_per_test=0.5)
    assert file_weights({"tests/a.py": 3, "tests/new.py": 4}, durations) == {
        "tests/a.py": 10_000,
        "tests/new.py": 2_000,
    }


def test_the_committed_durations_file_is_valid() -> None:
    """커밋된 소요 시간 파일이 읽히고 경로가 rootdir 기준 테스트 파일 형식이다"""
    durations = load_durations(DURATIONS_FILE)
    assert durations.files, "tests/.shard_durations.json이 비어 있거나 없습니다."
    assert durations.seconds_per_test > 0
    assert all(name.startswith("tests/") and name.endswith(".py") for name in durations.files)


# ── 플러그인: 가짜 아이템으로 실제 훅을 돌린다 ─────────────────────────────────
@dataclass
class _Item:
    path: Path
    name: str


@dataclass
class _Hook:
    deselected: list[_Item] = field(default_factory=list)

    def pytest_deselected(self, items: list[_Item]) -> None:
        self.deselected.extend(items)


@dataclass
class _Config:
    rootpath: Path
    hook: _Hook = field(default_factory=_Hook)


def _fake_items(root: Path, files: dict[str, int]) -> list[_Item]:
    # 수집 순서: 파일이 섞여 나오지 않는 pytest와 같게 파일별 연속.
    return [_Item(root / name, f"{name}::t{k}") for name, n in files.items() for k in range(n)]


@pytest.mark.parametrize("total", [1, 2, 3, 5])
def test_the_plugin_partitions_items_by_whole_files(tmp_path: Path, total: int) -> None:
    """플러그인 훅: 샤드별 선택의 합 = 전체 아이템, 파일은 쪼개지지 않고, 순서는 보존된다"""
    rng = random.Random(total)
    files = {f"tests/m{i:02d}/test_{i}.py": rng.randint(1, 9) for i in range(23)}
    durations = Durations(files={name: rng.random() * 50 for name in list(files)[:15]})
    original = _fake_items(tmp_path, files)
    kept_by_shard: list[list[_Item]] = []
    reports = []
    for index in range(1, total + 1):
        items = list(original)
        config = _Config(rootpath=tmp_path)
        report_path = tmp_path / f"report-{index}.json"
        plugin = ShardPlugin(ShardSpec(index, total), durations, report_path=report_path)
        plugin.pytest_collection_modifyitems(config, items)
        assert items == [i for i in original if i in items], "수집 순서가 바뀌었다"
        assert sorted(map(id, items + config.hook.deselected)) == sorted(map(id, original))
        kept_by_shard.append(items)
        reports.append(json.loads(report_path.read_text(encoding="utf-8")))
        assert plugin.pytest_report_collectionfinish()[0].startswith(f"[샤드 {index}/{total}]")

    flat = [id(item) for kept in kept_by_shard for item in kept]
    assert sorted(flat) == sorted(map(id, original)) and len(flat) == len(set(flat))
    for name in files:  # 한 파일의 아이템은 전부 같은 샤드에
        holders = {
            k
            for k, kept in enumerate(kept_by_shard)
            for item in kept
            if item.path == tmp_path / name
        }
        assert len(holders) == 1, name
    # 보고서를 CI 대조 도구에 그대로 넣어도 통과한다.
    assert shard_tools.verify(reports, Counter(files)) == []


def test_the_real_suite_is_partitioned_for_every_shard_count(
    request: pytest.FixtureRequest,
) -> None:
    """실제 수집된 테스트 파일로도 i/N 합집합 = 전체·교집합 = ∅·결정적이다"""
    plugin = request.config.pluginmanager.get_plugin(PLUGIN_NAME)
    if plugin is not None and plugin.assignment is not None:
        # 샤드 실행 중이면 session.items는 이 샤드 몫뿐이다 — 걸러내기 전 전체 수집을 쓴다.
        files = list(Counter(plugin.assignment.test_counts).elements())
    else:
        root = request.config.rootpath
        files = [item.path.relative_to(root).as_posix() for item in request.session.items]
    durations = load_durations(DURATIONS_FILE)
    for total in (2, 3, 4, 5):
        plans = [plan(ShardSpec(i, total), files, durations) for i in range(1, total + 1)]
        assigned = [name for p in plans for name in p.assigned]
        assert sorted(assigned) == sorted(set(files))
        assert len(assigned) == len(set(assigned))
        assert all(p.buckets == plans[0].buckets for p in plans)  # 어느 샤드에서 계산해도 같다


def test_the_plugin_is_registered_only_when_sharding(request: pytest.FixtureRequest) -> None:
    """이 세션의 플러그인 등록 여부가 환경변수와 일치한다(로컬 기본 = 미등록)"""
    active = shard_spec_from_env(os.environ) is not None
    assert (request.config.pluginmanager.get_plugin(PLUGIN_NAME) is not None) is active


# ── CI 대조 도구 ─────────────────────────────────────────────────────────────
def _reports(files: dict[str, int], total: int) -> list[dict[str, object]]:
    names = list(Counter(files).elements())
    return [
        plan(ShardSpec(i, total), names, Durations(files={})).report() for i in range(1, total + 1)
    ]


_FILES = {
    "tests/a/test_1.py": 3,
    "tests/a/test_2.py": 1,
    "tests/b/test_3.py": 7,
    "tests/b/test_4.py": 2,
}


def test_verify_passes_an_exact_partition() -> None:
    """정확한 분할이면 오류 0"""
    assert shard_tools.verify(_reports(_FILES, 3), Counter(_FILES)) == []


def test_verify_catches_a_missing_shard() -> None:
    """샤드 하나가 빠지면(매트릭스 오타·업로드 누락) 실패"""
    errors = shard_tools.verify(_reports(_FILES, 3)[:2], Counter(_FILES))
    assert any("샤드 번호" in e for e in errors)
    assert any("누락" in e for e in errors)


def test_verify_catches_a_duplicated_shard() -> None:
    """같은 샤드가 두 번 돌면(1,2,2) 실패"""
    reports = _reports(_FILES, 3)
    errors = shard_tools.verify([reports[0], reports[1], reports[1]], Counter(_FILES))
    assert any("샤드 번호" in e for e in errors)
    assert any("중복 배정" in e for e in errors)


def test_verify_catches_a_file_the_shards_never_saw() -> None:
    """독립 수집에만 있는 파일(샤드 러너가 덜 수집)이면 실패"""
    errors = shard_tools.verify(_reports(_FILES, 2), Counter({**_FILES, "tests/c/test_5.py": 1}))
    assert any("독립 수집과 다릅니다" in e for e in errors)
    assert any("누락" in e for e in errors)


def test_verify_catches_disagreeing_shard_counts() -> None:
    """보고서마다 N이 다르면 실패"""
    errors = shard_tools.verify([_reports(_FILES, 2)[0], _reports(_FILES, 3)[1]], Counter(_FILES))
    assert errors and "샤드 수" in errors[0]


def test_verify_catches_a_test_count_mismatch() -> None:
    """파일 집합이 같아도 테스트 수가 다르면 실패(파일 안에서 일부만 수집)"""
    errors = shard_tools.verify(_reports(_FILES, 2), Counter({**_FILES, "tests/a/test_1.py": 4}))
    assert any("테스트 수" in e or "테스트 합" in e for e in errors)


def test_verify_rejects_no_reports() -> None:
    """보고서가 하나도 없으면 실패"""
    assert shard_tools.verify([], Counter(_FILES))


_COLLECT_OUTPUT = """\
tests/a/test_1.py::test_x :: 설명 :: 콜론 포함
tests/a/test_1.py::TestK::test_y[a::b]
tests/b/test_3.py::test_z

=============================== warnings summary ===============================
tests/a/test_1.py::test_x
  /x.py:1: DeprecationWarning: boom

3 tests collected in 0.10s
"""


def test_collect_only_output_is_parsed_up_to_the_summary() -> None:
    """--collect-only -q 출력에서 nodeid만 읽는다(경고 요약은 제외)"""
    assert shard_tools.parse_collect_only(_COLLECT_OUTPUT) == Counter(
        {"tests/a/test_1.py": 2, "tests/b/test_3.py": 1}
    )


@pytest.mark.parametrize(
    "text",
    [
        _COLLECT_OUTPUT.replace("3 tests collected", "4 tests collected"),
        _COLLECT_OUTPUT.replace("3 tests collected in 0.10s\n", ""),
        "rootdir: /x\n" + _COLLECT_OUTPUT,
    ],
    ids=["count-mismatch", "no-summary", "non-nodeid-line"],
)
def test_collect_only_parsing_fails_on_unexpected_format(text: str) -> None:
    """요약 개수 불일치·요약 없음·nodeid 아닌 줄은 실패(조용히 일부만 읽지 않는다)"""
    with pytest.raises(ValueError):
        shard_tools.parse_collect_only(text)


def test_cli_verify_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI verify: 통과 0 / 샤드 빠짐 1(::error:: 출력)"""
    collect = tmp_path / "collect.txt"
    collect.write_text(_COLLECT_OUTPUT, encoding="utf-8")
    files = {"tests/a/test_1.py": 2, "tests/b/test_3.py": 1}
    paths = []
    for k, report in enumerate(_reports(files, 2), start=1):
        path = tmp_path / f"shard-report-{k}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(str(path))
    assert shard_tools.main(["verify", "--collect-only", str(collect), *paths]) == 0
    assert shard_tools.main(["verify", "--collect-only", str(collect), paths[0]]) == 1
    assert "::error::" in capsys.readouterr().out


# ── 소요 시간 파일 생성 ──────────────────────────────────────────────────────
def test_durations_are_summed_per_file_from_junitxml(tmp_path: Path) -> None:
    """junitxml(xunit1 file 속성·xunit2 classname 둘 다) → 파일별 초 합·테스트당 초"""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_b.py").write_text("", encoding="utf-8")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase file="tests/unit/test_a.py" classname="tests.unit.test_a" name="t1" time="1.5"/>'
        '<testcase file="tests/unit/test_a.py" classname="tests.unit.test_a" name="t2" time="0.5"/>'
        # xunit2: file 속성 없음 + conftest가 nodeid에 붙인 설명 때문에 함수명이 classname에 섞인다
        '<testcase classname="tests.unit.test_b.TestX.test_y " name=" 설명" time="2.0"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    data = shard_tools.durations_from_junit([junit], root=tmp_path)
    assert data["files"] == {"tests/unit/test_a.py": 2.0, "tests/unit/test_b.py": 2.0}
    assert data["tests"] == 3
    assert data["seconds_per_test"] == round(4.0 / 3, 4)


def test_cli_durations_writes_a_file_the_plugin_reads(tmp_path: Path) -> None:
    """CLI durations → 출력 파일을 플러그인(load_durations)이 그대로 읽는다"""
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite><testcase file="tests/x/test_p.py" classname="c" name="t" time="3.25"/>'
        '<testcase file="tests/x/test_q.py" classname="c" name="t" time="0.75"/></testsuite>',
        encoding="utf-8",
    )
    out = tmp_path / "d.json"
    assert shard_tools.main(["durations", str(junit), "--output", str(out)]) == 0
    loaded = load_durations(out)
    assert loaded.files == {"tests/x/test_p.py": 3.25, "tests/x/test_q.py": 0.75}
    assert loaded.seconds_per_test == 2.0
