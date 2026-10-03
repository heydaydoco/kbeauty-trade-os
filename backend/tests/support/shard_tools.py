"""샤드 도구 — 소요 시간 파일 갱신(``durations``)·완전성 대조(``verify``). ADR-0072.

backend/ 에서 실행한다.

  # 소요 시간 파일 갱신 — 균형만 바뀐다(완전성과 무관). 둘 중 하나:
  #  (a) CI backend-coverage 잡의 'shard-durations' 아티팩트(실제 러너 실측)를 받아
  #      tests/.shard_durations.json 으로 교체해 커밋
  #  (b) 로컬 전체 1회 실행에서 생성
  pytest --junitxml=junit.xml -o junit_family=xunit1
  python -m tests.support.shard_tools durations junit.xml

  # 완전성 대조 — CI backend-coverage 잡이 돈다(누락·중복·샤드 빠짐이면 exit 1)
  pytest --collect-only -q -p no:cacheprovider > collect-only.txt
  python -m tests.support.shard_tools verify --collect-only collect-only.txt shard-report-*.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from tests.support.sharding import DURATIONS_FILE

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_COLLECTED = re.compile(r"^(\d+) tests? collected")


# ── durations ──────────────────────────────────────────────────────────────
def _file_of_testcase(case: ET.Element, root: Path) -> str | None:
    """xunit1은 file 속성이 있다. 없으면 classname(점 경로)의 가장 긴 실재 .py 접두로 푼다."""
    explicit = case.get("file")
    if explicit:
        return Path(explicit).as_posix()
    parts = (case.get("classname") or "").split(".")
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end]) + ".py"
        if (root / candidate).is_file():
            return candidate
    return None


def durations_from_junit(paths: Iterable[Path], root: Path = BACKEND_ROOT) -> dict[str, object]:
    seconds: Counter[str] = Counter()
    tests = 0
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            name = _file_of_testcase(case, root)
            if name is None:
                raise SystemExit(f"테스트 파일을 알 수 없는 testcase: {case.attrib}")
            seconds[name] += float(case.get("time") or 0.0)
            tests += 1
    if tests == 0:
        raise SystemExit("junitxml에 testcase가 없습니다.")
    total = sum(seconds.values())
    return {
        "_how_to_update": (
            "backend/에서: pytest --junitxml=junit.xml -o junit_family=xunit1 → "
            "python -m tests.support.shard_tools durations junit.xml "
            "(또는 CI backend-coverage 잡의 shard-durations 아티팩트로 교체). "
            "균형에만 쓰이며 누락·중복 방지와는 무관하다(ADR-0072)."
        ),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "tests": tests,
        "total_seconds": round(total, 1),
        "seconds_per_test": round(total / tests, 4),
        "files": {name: round(seconds[name], 2) for name in sorted(seconds)},
    }


# ── verify ─────────────────────────────────────────────────────────────────
def parse_collect_only(text: str) -> Counter[str]:
    """``pytest --collect-only -q`` 출력 → 파일별 테스트 수. 첫 빈 줄까지가 nodeid 목록이다.

    끝의 'N tests collected'와 개수가 다르면 실패한다(형식이 바뀌어 조용히 일부만 읽는 것을 막는다).
    """
    lines = text.splitlines()
    counts: Counter[str] = Counter()
    for line in lines:
        if not line.strip():
            break
        head, sep, _ = line.partition("::")
        if not sep or not head.endswith(".py"):
            raise ValueError(f"nodeid가 아닌 줄: {line!r}")
        counts[head] += 1
    declared = [int(m.group(1)) for line in lines if (m := _COLLECTED.match(line))]
    if declared != [sum(counts.values())]:
        raise ValueError(
            f"수집 요약({declared})과 nodeid 수({sum(counts.values())})가 다릅니다 — 출력 형식 확인."
        )
    return counts


def verify(reports: Sequence[dict[str, object]], collected: Counter[str]) -> list[str]:
    """샤드 보고서들이 독립 수집 결과의 정확한 분할인지 본다. 반환: 오류 목록(비면 통과)."""
    errors: list[str] = []
    if not reports:
        return ["샤드 보고서가 하나도 없습니다."]
    totals = {int(str(r["total"])) for r in reports}
    if len(totals) != 1:
        return [f"샤드 수(N)가 보고서마다 다릅니다: {sorted(totals)}"]
    total = totals.pop()
    indexes = sorted(int(str(r["index"])) for r in reports)
    if indexes != list(range(1, total + 1)):
        errors.append(f"샤드 번호가 1..{total}과 다릅니다(빠짐·중복): {indexes}")

    expected = set(collected)
    for report in reports:
        seen = set(_strings(report["collected_files"]))
        if seen != expected:
            errors.append(
                f"샤드 {report['shard']}의 수집 파일이 독립 수집과 다릅니다 — "
                f"없음 {sorted(expected - seen)} / 초과 {sorted(seen - expected)}"
            )
        if report["collected_tests"] != sum(collected.values()):
            errors.append(
                f"샤드 {report['shard']}의 수집 테스트 수 {report['collected_tests']} ≠ 독립 수집 "
                f"{sum(collected.values())}"
            )

    owner: dict[str, str] = {}
    for report in reports:
        for name in _strings(report["assigned_files"]):
            if name in owner:
                errors.append(f"중복 배정: {name} — 샤드 {owner[name]}와 {report['shard']}")
            owner[name] = str(report["shard"])
    missing = expected - set(owner)
    extra = set(owner) - expected
    if missing:
        errors.append(f"어느 샤드에도 배정되지 않은 파일(누락): {sorted(missing)}")
    if extra:
        errors.append(f"수집되지 않은 파일이 배정됨: {sorted(extra)}")
    assigned_tests = sum(int(str(r["assigned_tests"])) for r in reports)
    if assigned_tests != sum(collected.values()):
        errors.append(f"샤드 배정 테스트 합 {assigned_tests} ≠ 독립 수집 {sum(collected.values())}")
    return errors


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"문자열 목록이어야 합니다: {value!r}")
    return value


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.support.shard_tools")
    sub = parser.add_subparsers(dest="command", required=True)
    dur = sub.add_parser("durations", help="junitxml → tests/.shard_durations.json")
    dur.add_argument("junit", nargs="+", type=Path)
    dur.add_argument("--output", type=Path, default=DURATIONS_FILE)
    ver = sub.add_parser("verify", help="샤드 보고서 합집합 = 독립 --collect-only 대조")
    ver.add_argument("--collect-only", dest="collect_only", type=Path, required=True)
    ver.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.command == "durations":
        data = durations_from_junit(args.junit)
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(
            f"{args.output}: 파일 {len(data['files'])}개 · 테스트 {data['tests']}건 · {data['total_seconds']}초"
        )
        return 0

    collected = parse_collect_only(args.collect_only.read_text(encoding="utf-8"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    errors = verify(reports, collected)
    for error in errors:
        print(f"::error::샤드 완전성 — {error}")
    if errors:
        return 1
    print(
        f"샤드 완전성 통과: 샤드 {len(reports)}개 · 파일 {len(collected)}개 · "
        f"테스트 {sum(collected.values())}건 — 합집합 = 전체, 교집합 = ∅"
    )
    for report in sorted(reports, key=lambda r: int(r["index"])):
        print(
            f"  샤드 {report['shard']}: 파일 {len(report['assigned_files'])}개 · "
            f"테스트 {report['assigned_tests']}건 · 예상 {report['estimated_seconds']}초"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
