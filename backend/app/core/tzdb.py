"""IANA 시간대 데이터 — **앱 버전에 고정**된 pip `tzdata` 패키지만 읽는다(S3-2 PR-4a 적대 검토 반영 ①).

호스트 OS의 tzdata(`/usr/share/zoneinfo`)는 이미지마다 다르다 — `python:*-slim`에는 아예 없고, 배포판 갱신으로 이름이 빠질 수도 있다.
그 차이로 어제 저장한 시각형 마일스톤의 tz가 오늘 해석되지 않으면 선적 상세·쓰기 응답이 500이 된다. 그래서 이름 집합·규칙·링크를
`requirements.txt`가 고정한 `tzdata` 패키지 파일에서 직접 연다(`ZoneInfo.from_file` — 호스트 TZPATH 미경유).

■ 순수: DB·시계 의존 0(패키지 데이터 파일 읽기뿐 — `trade_docs/schedule.py` 순수성 허용 목록에 등재).
■ `canonical_zone_name`: 쓰기 시점 정규화 — 폐지·별칭 이름(예: `Asia/Saigon`·`US/Eastern`·`UTC`)을 tzdb 링크를 따라
  국가 대표 이름(`zone.tab`)이나 링크 아닌 이름에 닿을 때까지 바꾼다. `zone.tab`에 있는 이름은 그대로 둔다
  (예: `Europe/Amsterdam`은 tzdb 기본 빌드에서 `Europe/Brussels`의 링크지만 네덜란드 대표 이름이라 바꾸지 않는다 — 화면 표기 보존).
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from zoneinfo import ZoneInfo

#: 지역이 아닌 키 — 호스트 설정·관례에 따라 뜻이 달라 받지 않는다.
_NON_GEOGRAPHIC = frozenset({"localtime", "Factory", "posixrules"})


def _read_text(*parts: str) -> str:
    return resources.files("tzdata").joinpath(*parts).read_text(encoding="utf-8")


@cache
def zone_names() -> frozenset[str]:
    """고정된 tzdata의 IANA 이름 집합(링크 포함, 비지역 키 제외)."""
    names = {line.strip() for line in _read_text("zones").splitlines()}
    return frozenset(name for name in names if name) - _NON_GEOGRAPHIC


@cache
def _links() -> dict[str, str]:
    """링크 이름 → 대상 이름(`tzdata.zi`의 `L 대상 링크` 줄)."""
    found: dict[str, str] = {}
    for line in _read_text("zoneinfo", "tzdata.zi").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "L":
            found[parts[2]] = parts[1]
    return found


@cache
def _country_zones() -> frozenset[str]:
    """국가 대표 이름(`zone.tab` 3열) — 정규화가 멈추는 자리."""
    found: set[str] = set()
    for line in _read_text("zoneinfo", "zone.tab").splitlines():
        if line and not line.startswith("#"):
            columns = line.split("\t")
            if len(columns) >= 3:
                found.add(columns[2])
    return frozenset(found)


def canonical_zone_name(name: str) -> str:
    """검증된 이름의 정규형 — 모르는 이름은 KeyError(호출자가 422로 번역)."""
    if name not in zone_names():
        raise KeyError(name)
    links, country = _links(), _country_zones()
    current = name
    for _ in range(8):  # 링크 사슬 상한(tzdb는 1단계 — 순환 방어)
        if current in country or current not in links:
            break
        current = links[current]
    return current if current in zone_names() else name


@cache
def bundled_zone(name: str) -> ZoneInfo:
    """고정 tzdata 파일에서 연 시간대 — 이름이 집합 밖이면 KeyError(호스트 OS 시간대로 대체하지 않는다)."""
    if name not in zone_names():
        raise KeyError(name)
    with resources.files("tzdata").joinpath("zoneinfo", *name.split("/")).open("rb") as handle:
        return ZoneInfo.from_file(handle, key=name)
