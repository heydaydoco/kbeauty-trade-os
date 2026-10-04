"""K. 앱 버전 고정 IANA 시간대(`app.core.tzdb`) — 호스트 OS tzdata 무의존·정규 이름·고정 버전 대사
(S3-2 PR-4a 적대 검토 반영 ① — 저장된 tz가 이미지 tzdata에서 빠지면 상세·쓰기 응답 500이던 구멍).
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path

import pytest

from app.core import tzdb
from app.modules.trade_docs import schedule

pytestmark = pytest.mark.group_k

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"


def test_the_zone_set_comes_from_the_pinned_tzdata_package() -> None:
    """requirements.txt가 `tzdata`를 == 고정하고, 설치본 버전이 그 고정값과 같다(시간대 집합 = 앱 버전의 일부)"""
    pins = [
        line.split("==", 1)[1].strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.startswith("tzdata==")
    ]
    assert len(pins) == 1, pins
    assert metadata.version("tzdata") == pins[0]


def test_zone_names_exclude_host_dependent_keys() -> None:
    names = tzdb.zone_names()
    assert {"Asia/Seoul", "America/New_York", "Etc/UTC", "Asia/Saigon"} <= names
    assert not names & {"localtime", "Factory", "posixrules"}


def test_bundled_zones_open_without_any_host_time_zone_data() -> None:
    """호스트 TZPATH를 비워도(slim 이미지 = 시스템 tzdata 없음) 고정 tzdata 파일에서 연다 — 결과가 호스트에 따라 달라지지 않는다"""
    tzdb.bundled_zone.cache_clear()
    zoneinfo.reset_tzpath(to=[])
    try:
        seoul = tzdb.bundled_zone("Asia/Seoul")
        assert seoul.utcoffset(datetime(2026, 10, 1, tzinfo=UTC)) == timedelta(hours=9)
        assert schedule.zone("America/New_York").key == "America/New_York"
    finally:
        zoneinfo.reset_tzpath()
        tzdb.bundled_zone.cache_clear()


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("Asia/Saigon", "Asia/Ho_Chi_Minh"),  # 폐지 별칭 → 국가 대표 이름
        ("US/Eastern", "America/New_York"),
        ("Asia/Calcutta", "Asia/Kolkata"),
        ("UTC", "Etc/UTC"),
        ("Europe/Amsterdam", "Europe/Amsterdam"),  # tzdb 링크지만 zone.tab 대표 이름 — 표기 보존
        ("Asia/Seoul", "Asia/Seoul"),
        ("Etc/GMT+5", "Etc/GMT+5"),
    ],
)
def test_canonical_names(given: str, stored: str) -> None:
    assert tzdb.canonical_zone_name(given) == stored


@pytest.mark.parametrize(
    "bad", ["Mars/Olympus_Mons", "localtime", "asia/seoul", "../etc/passwd", ""]
)
def test_unknown_names_are_refused_everywhere(bad: str) -> None:
    with pytest.raises(KeyError):
        tzdb.canonical_zone_name(bad)
    with pytest.raises(KeyError):
        tzdb.bundled_zone(bad)
    with pytest.raises(ValueError, match="IANA"):
        schedule.zone(bad)
