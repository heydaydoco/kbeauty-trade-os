"""K. T1 시드 카탈로그 계약 — DoD "시드 데이터 전건 근거링크 보유" (§5.5 / S2-4 PR-2).

★ 카탈로그가 저장소의 데이터라 이 테스트가 CI에서 매번 계약을 지킨다. 공식 기관 도메인 허용목록은
  "https이고 알려진 공식 호스트"만 통과시켜 오타·임의 사이트 링크를 막는다(링크의 내용 진위는 사람이
  확정 전에 연다 — 이 테스트는 형식·출처 도메인까지만 본다).
"""

from __future__ import annotations

import copy
import json
from typing import Any
from urllib.parse import urlparse

import pytest

from app.modules.requirements.models import APPLIES_TO_VALUES
from app.modules.seeds.catalog import CATALOG_PATH, CatalogError, load_catalog, parse_catalog

pytestmark = pytest.mark.group_k


def _raw() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_the_shipped_catalog_loads_and_covers_the_t1_markets() -> None:
    """DESIGN §5.5 T1 — 미국·EU·영국·중국·일본·아세안·캐나다·호주"""
    codes = {market.code for market in load_catalog().markets}
    assert {"US", "EU", "GB", "CN", "JP", "CA", "AU"} <= codes
    assert {"ID", "MY", "SG", "TH", "VN", "PH"} <= codes  # 아세안 6 (문면의 '아세안 6')


#: 공식 기관·법령 사이트의 명시 허용목록 — 새 호스트를 카탈로그에 넣으려면 이 목록을 함께 고쳐
#: 리뷰에서 "정말 공식 사이트인가"를 사람이 보게 한다(와일드카드 없음).
OFFICIAL_HOSTS = frozenset(
    {
        "asean.org",
        "eur-lex.europa.eu",
        "halal.go.id",
        "www.canada.ca",
        "www.fda.gov",
        "www.halal.gov.my",
        "www.industrialchemicals.gov.au",
        "www.legislation.gov.uk",
        "www.mhlw.go.jp",
        "www.mofcom.gov.cn",
        "www.nmpa.gov.cn",
        "www.tga.gov.au",
    }
)


def test_every_template_has_an_https_evidence_link_on_an_official_host() -> None:
    """전 항목 근거링크 — https이고 공식 호스트 허용목록 안(임의 도메인 거부)"""
    for market in load_catalog().markets:
        for item in market.templates:
            parsed = urlparse(item.source_url)
            assert parsed.scheme == "https", item.key
            assert parsed.hostname in OFFICIAL_HOSTS, (item.key, parsed.hostname)


def test_the_catalog_never_carries_a_verification_date() -> None:
    """확인일은 사람의 몫 — 카탈로그 항목에 last_verified_on이 있으면 안 된다(투입 payload도 None)"""
    for market in _raw()["markets"]:
        for item in market["templates"]:
            assert "last_verified_on" not in item
    for market in load_catalog().markets:
        for item in market.templates:
            assert item.payload()["last_verified_on"] is None


def test_applies_to_values_are_within_the_enumeration() -> None:
    for market in load_catalog().markets:
        for item in market.templates:
            assert item.applies_to in APPLIES_TO_VALUES


def test_china_and_eu_prerequisites_are_declared() -> None:
    """도메인 판정 2건 — 중국 비안은 경내책임자·안전성 자료 선행, EU 통보는 RP 선행"""
    catalog = load_catalog()
    eu = {item.key: item for item in catalog.market("EU").templates}  # type: ignore[union-attr]
    assert eu["eu-cpnp"].prerequisites == ("eu-rp",)
    cn = catalog.market("CN")
    assert cn is not None and any(item.prerequisites for item in cn.templates)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["markets"][0]["templates"][0].update(source_url=""), "근거링크"),
        (lambda d: d["markets"][0]["templates"][0].update(source_url="   "), "근거링크"),
        (
            lambda d: d["markets"][0]["templates"][0].update(source_url="http://www.fda.gov/x"),
            "https",
        ),
        (lambda d: d["markets"][0]["templates"][0].pop("source_url"), "근거링크"),
        (lambda d: d["markets"][0]["templates"][0].update(applies_to="BOGUS"), "적용단위"),
        (lambda d: d["markets"][0]["templates"][0].update(requirement_type="lower"), "요건유형"),
        (lambda d: d["markets"][0]["templates"][0].update(prerequisites=["no-such"]), "선행"),
        (lambda d: d["markets"][0]["templates"][0].update(note=""), "note"),
        (lambda d: d["markets"][0]["templates"][0].update(renewal_cycle_months=0), "1 이상"),
        (lambda d: d["markets"][0].update(code="usa"), "시장 코드"),
        (lambda d: d["markets"].append(copy.deepcopy(d["markets"][0])), "중복"),
        (lambda d: d.update(notice=[]), "notice"),
    ],
)
def test_the_parser_rejects_contract_violations(mutate: Any, message: str) -> None:
    """계약 위반 카탈로그는 로드 자체가 실패한다 — 빈 값·http·열거 밖·없는 선행 키·중복"""
    raw = _raw()
    mutate(raw)
    with pytest.raises(CatalogError, match=message):
        parse_catalog(raw)


def test_duplicate_keys_names_and_cycles_are_rejected() -> None:
    raw = _raw()
    first = raw["markets"][0]["templates"]
    first[1]["key"] = first[0]["key"]
    with pytest.raises(CatalogError, match="key 중복"):
        parse_catalog(raw)
    raw = _raw()
    first = raw["markets"][0]["templates"]
    first[1]["name"] = first[0]["name"]
    with pytest.raises(CatalogError, match="이름"):
        parse_catalog(raw)
    raw = _raw()
    eu = next(m for m in raw["markets"] if m["code"] == "EU")
    by_key = {item["key"]: item for item in eu["templates"]}
    by_key["eu-rp"]["prerequisites"] = ["eu-cpnp"]  # eu-cpnp → eu-rp → eu-cpnp
    with pytest.raises(CatalogError, match="순환"):
        parse_catalog(raw)
    raw = _raw()
    raw["markets"][0]["templates"][0]["prerequisites"] = [raw["markets"][0]["templates"][0]["key"]]
    with pytest.raises(CatalogError, match="자기 자신"):
        parse_catalog(raw)
