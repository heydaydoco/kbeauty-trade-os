"""C·K. T1 요건 템플릿 시드 투입 API (§5.5 / S2-4 PR-2 — ADR-0049).

★ 투입은 항상 초안·확인일 없음 — 확정은 사람이 링크를 확인해 확인일을 넣은 뒤 기존 확정 게이트로
  한다. 같은 (시장, 이름)이 있으면 건너뛴다(사람이 고친 템플릿을 덮지 않는다).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.markets.models import Market
from app.modules.requirements.models import RequirementTemplate, TemplatePrerequisite
from app.modules.seeds.catalog import load_catalog
from tests.support.factories import DEFAULT_PASSWORD, create_market, create_user

pytestmark = [pytest.mark.group_c, pytest.mark.group_k]

LOGIN = "/api/v1/auth/login"
STATUS = "/api/v1/seeds/t1"
APPLY = "/api/v1/seeds/t1/apply"
_KEYS = itertools.count(1)


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"seed-{next(_KEYS)}"}


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert (
            client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD}).status_code
            == 200
        )
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("seed-cert@example.com", RoleCode.CERT)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("seed-trade@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("seed-viewer@example.com", RoleCode.VIEWER)


def _catalog_count(code: str) -> int:
    market = load_catalog().market(code)
    assert market is not None
    return len(market.templates)


def _templates(code: str) -> list[RequirementTemplate]:
    with unit_of_work() as uow:
        rows = list(
            uow.session.execute(
                select(RequirementTemplate)
                .join(Market, Market.id == RequirementTemplate.market_id)
                .where(Market.code == code, RequirementTemplate.deleted_at.is_(None))
                .order_by(RequirementTemplate.id)
            ).scalars()
        )
        for row in rows:
            uow.session.expunge(row)
        return rows


def _edit_body(row: RequirementTemplate, **changes: Any) -> dict[str, Any]:
    """PATCH는 전체 본문(낙관 잠금 포함) — 시드 행을 그대로 옮기고 바꿀 필드만 덮는다."""
    body: dict[str, Any] = {
        "version": row.version,
        "name": row.name,
        "applies_to": row.applies_to,
        "requirement_type": row.requirement_type,
        "status": row.status,
        "source_url": row.source_url,
        "note": row.note,
    }
    body.update(changes)
    return body


def _apply(client: TestClient, *codes: str, **kwargs: Any) -> Any:
    return client.post(
        APPLY, json={"markets": list(codes)}, headers=kwargs.get("headers") or _key()
    )


def test_applying_registers_the_market_and_creates_draft_templates_without_a_verification_date(
    cert: TestClient,
) -> None:
    """시장이 없으면 등록하고, 템플릿은 전부 DRAFT·확인일 없음·근거링크 보유로 만든다"""
    response = _apply(cert, "US")
    assert response.status_code == 200, response.text
    (result,) = response.json()["results"]
    assert result["code"] == "US" and result["market_created"] is True
    assert len(result["created"]) == _catalog_count("US") and result["skipped"] == []
    rows = _templates("US")
    assert len(rows) == _catalog_count("US")
    for row in rows:
        assert row.status == "DRAFT"
        assert row.last_verified_on is None
        assert row.source_url is not None and row.source_url.startswith("https://")
        assert row.created_by_id is not None  # 감사 컬럼 — 투입자가 남는다


def test_applying_again_skips_everything_and_never_overwrites_edits(cert: TestClient) -> None:
    """멱등 — 다시 투입하면 전부 건너뜀, 사람이 고친 이름·메모는 그대로"""
    _apply(cert, "US")
    first = _templates("US")[0]
    edited = cert.patch(
        f"/api/v1/requirement-templates/{first.id}",
        json=_edit_body(first, note="사람이 고친 메모"),
    )
    assert edited.status_code == 200, edited.text
    again = _apply(cert, "US").json()["results"][0]
    assert again["created"] == [] and len(again["skipped"]) == _catalog_count("US")
    assert again["market_created"] is False
    assert _templates("US")[0].note == "사람이 고친 메모"
    assert len(_templates("US")) == _catalog_count("US")


def test_the_same_idempotency_key_replays_the_first_result(cert: TestClient) -> None:
    headers = {"Idempotency-Key": "seed-replay"}
    first = _apply(cert, "GB", headers=headers).json()
    replay = _apply(cert, "GB", headers=headers).json()
    assert first == replay
    assert len(_templates("GB")) == _catalog_count("GB")


def test_an_existing_market_is_reused_and_only_missing_templates_are_added(
    cert: TestClient,
) -> None:
    """이미 등록된 시장은 재사용 — 같은 이름의 사람 템플릿이 있으면 그것만 건너뛴다"""
    create_market("US", name_ko="미국(수기)")
    name = load_catalog().market("US").templates[0].name  # type: ignore[union-attr]
    market_id = _market_id("US")
    with unit_of_work() as uow:
        uow.session.add(
            RequirementTemplate(
                market_id=market_id, name=name, applies_to="COMPANY", requirement_type="MANUAL"
            )
        )
    result = _apply(cert, "US").json()["results"][0]
    assert result["market_created"] is False
    assert len(result["skipped"]) == 1 and len(result["created"]) == _catalog_count("US") - 1
    with unit_of_work() as uow:
        assert uow.session.execute(select(func.count()).select_from(Market)).scalar_one() == 1


def _market_id(code: str) -> int:
    with unit_of_work() as uow:
        return uow.session.execute(select(Market.id).where(Market.code == code)).scalar_one()


def test_prerequisites_follow_the_catalog_and_are_acyclic(cert: TestClient) -> None:
    """EU 통보는 RP를 선행요건으로 — 링크가 생기고, 이미 있는 선행 대상도 이름으로 이어진다"""
    _apply(cert, "EU")
    by_name = {row.name: row for row in _templates("EU")}
    catalog = {item.key: item for item in load_catalog().market("EU").templates}  # type: ignore[union-attr]
    cpnp = by_name[catalog["eu-cpnp"].name]
    rp = by_name[catalog["eu-rp"].name]
    with unit_of_work() as uow:
        links = list(
            uow.session.execute(
                select(TemplatePrerequisite).where(TemplatePrerequisite.template_id == cpnp.id)
            ).scalars()
        )
    assert [link.prerequisite_template_id for link in links] == [rp.id]


def test_prerequisites_are_not_added_to_skipped_templates(cert: TestClient) -> None:
    """건너뛴 템플릿에는 선행요건을 더하지 않는다(사람의 것) — 재투입해도 링크 수가 그대로"""
    _apply(cert, "EU")
    with unit_of_work() as uow:
        before = uow.session.execute(
            select(func.count()).select_from(TemplatePrerequisite)
        ).scalar_one()
    _apply(cert, "EU")
    with unit_of_work() as uow:
        after = uow.session.execute(
            select(func.count()).select_from(TemplatePrerequisite)
        ).scalar_one()
    assert before == after and before >= 1


def test_a_seeded_draft_cannot_be_confirmed_until_a_human_verifies_it(cert: TestClient) -> None:
    """확정 게이트 재확인 — 확인일이 없으면 422, 사람이 확인일을 넣은 뒤에야 확정된다"""
    _apply(cert, "CA")
    row = _templates("CA")[0]
    denied = cert.post(
        f"/api/v1/requirement-templates/{row.id}/confirm",
        json={"version": row.version},
        headers=_key(),
    )
    assert denied.status_code == 422
    patched = cert.patch(
        f"/api/v1/requirement-templates/{row.id}",
        json=_edit_body(row, last_verified_on=date(2026, 9, 30).isoformat()),
    )
    assert patched.status_code == 200, patched.text
    confirmed = cert.post(
        f"/api/v1/requirement-templates/{row.id}/confirm",
        json={"version": patched.json()["version"]},
        headers=_key(),
    )
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "CONFIRMED"


def test_several_markets_apply_in_one_transaction_and_unknown_markets_are_rejected(
    cert: TestClient,
) -> None:
    """시장 여러 개 — 모두 성공하거나(중복 코드는 합침), 카탈로그에 없는 코드가 있으면 아무것도 안 만든다"""
    bad = _apply(cert, "US", "ZZ")
    assert bad.status_code == 422 and "markets" in bad.json()["error"]["detail"]
    assert _templates("US") == []
    good = _apply(cert, "us", "US", "JP").json()["results"]
    assert [item["code"] for item in good] == ["US", "JP"]


def test_a_deleted_market_that_holds_the_code_blocks_the_seed(cert: TestClient) -> None:
    """삭제된 시장이 코드를 점유하면 되살리지 않고 안내한다(복원 액션 없음) — 부분 성공도 없다"""
    from app.core.time import utcnow

    market_id = create_market("AU")
    with unit_of_work() as uow:
        row = uow.session.get(Market, market_id)
        assert row is not None
        row.deleted_at = utcnow()
    response = _apply(cert, "JP", "AU")
    assert response.status_code == 422 and "AU" in response.json()["error"]["detail"]["markets"]
    assert _templates("JP") == []  # 트랜잭션 1건 — JP도 롤백


def test_apply_needs_the_certification_role_but_status_is_readable_by_everyone(
    trader: TestClient, viewer: TestClient, cert: TestClient
) -> None:
    """투입=인증+관리자, 현황 열람=전 역할"""
    for client in (trader, viewer):
        assert client.get(STATUS).status_code == 200
        assert _apply(client, "US").status_code == 403
    assert _apply(cert, "US").status_code == 200


def test_the_status_reports_registration_and_per_template_progress(cert: TestClient) -> None:
    before = cert.get(STATUS).json()
    assert before["notice"] and before["version"]
    us = next(item for item in before["markets"] if item["code"] == "US")
    assert us["registered"] is False and all(t["existing_status"] is None for t in us["templates"])
    _apply(cert, "US")
    after = cert.get(STATUS).json()
    us = next(item for item in after["markets"] if item["code"] == "US")
    assert us["registered"] is True and {t["existing_status"] for t in us["templates"]} == {"DRAFT"}
    eu = next(item for item in after["markets"] if item["code"] == "EU")
    assert eu["registered"] is False


def test_the_apply_body_is_validated(cert: TestClient) -> None:
    assert cert.post(APPLY, json={"markets": []}, headers=_key()).status_code == 422
    assert cert.post(APPLY, json={"markets": ["US"], "x": 1}, headers=_key()).status_code == 422
    assert cert.post(APPLY, json={}, headers=_key()).status_code == 422


def test_the_cli_seeds_with_a_registered_actor_and_fails_cleanly_otherwise(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`seed-t1` — 등록된 계정이 투입자, 없는 계정·없는 시장은 만들지 않고 실패(종료 1)"""
    from app.cli import main as cli_main

    create_user("seed-cli@example.com", roles=(RoleCode.CERT,))
    assert cli_main(["seed-t1", "--market", "us", "--actor-email", "nobody@example.com"]) == 1
    assert cli_main(["seed-t1", "--market", "ZZ", "--actor-email", "seed-cli@example.com"]) == 1
    assert _templates("US") == []
    assert cli_main(["seed-t1", "--market", "US", "--actor-email", "seed-cli@example.com"]) == 0
    out = capsys.readouterr().out
    assert "US: 시장 신규 등록" in out and f"초안 {_catalog_count('US')}건 투입" in out
    assert cli_main(["seed-t1", "--market", "US", "--actor-email", "seed-cli@example.com"]) == 0
    assert "기존" in capsys.readouterr().out
    assert len(_templates("US")) == _catalog_count("US")
