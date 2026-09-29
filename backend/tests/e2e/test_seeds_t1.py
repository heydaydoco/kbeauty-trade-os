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


# ═══ 신규 시장 위저드 ═════════════════════════════════════════════════════════

WIZARD = "/api/v1/market-wizard"


def _step(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(step for step in body["steps"] if step["key"] == key)


def _add_hs(sku_id: int, country: str) -> None:
    from app.core.time import today_kst
    from app.modules.catalog.models import SkuHsCode

    create_market(country)
    with unit_of_work() as uow:
        uow.session.add(
            SkuHsCode(
                sku_id=sku_id,
                country_code=country,
                hs_version="HS2022",
                hs_code="330499",
                source_url="https://example.com/hs",
                last_verified_on=today_kst(),
            )
        )


def test_the_wizard_for_an_unregistered_market_is_all_todo_except_the_unavailable_step(
    cert: TestClient,
) -> None:
    """시장 부재 — 1단계 TODO, 협정은 '모듈 도래 전'으로 완료 계산에서 제외"""
    body = cert.get(f"{WIZARD}/us").json()  # 소문자도 정규화
    assert body["code"] == "US" and body["market_id"] is None
    assert [step["status"] for step in body["steps"]] == [
        "TODO",
        "TODO",
        "TODO",
        "NOT_AVAILABLE",
        "TODO",
    ]
    assert (body["counted_steps"], body["done_steps"]) == (4, 0)
    assert body["catalog_available"] is True and body["catalog_template_count"] == _catalog_count(
        "US"
    )
    assert _step(body, "agreements")["note"]


def test_the_wizard_follows_seeding_confirming_hs_and_rules(cert: TestClient) -> None:
    """투입 전→후→확정→HS·성분 규칙 등록까지 단계가 데이터에서 계산된다"""
    from tests.support.factories import create_ingredient, create_ingredient_rule, create_sku

    seeded = _apply(cert, "JP")
    assert seeded.status_code == 200
    body = cert.get(f"{WIZARD}/JP").json()
    assert _step(body, "market")["status"] == "DONE"
    templates = _step(body, "templates")
    assert templates["status"] == "IN_PROGRESS"
    assert templates["counts"] == {
        "draft": _catalog_count("JP"),
        "confirmed": 0,
        "retired": 0,
        "total": _catalog_count("JP"),
    }

    # 전부 확정하면 DONE — 확인일 입력 후 확정
    for row in _templates("JP"):
        patched = cert.patch(
            f"/api/v1/requirement-templates/{row.id}",
            json=_edit_body(row, last_verified_on=date(2026, 9, 30).isoformat()),
        )
        assert patched.status_code == 200, patched.text
        assert (
            cert.post(
                f"/api/v1/requirement-templates/{row.id}/confirm",
                json={"version": patched.json()["version"]},
                headers=_key(),
            ).status_code
            == 200
        )
    body = cert.get(f"{WIZARD}/JP").json()
    assert _step(body, "templates")["status"] == "DONE"

    assert (
        _step(body, "hs")["status"] == "TODO"
        and _step(body, "ingredient_rules")["status"] == "TODO"
    )
    _add_hs(create_sku("SKU-WIZ-1"), "JP")
    create_ingredient_rule(create_ingredient("Glycerin"), country_code="JP")
    body = cert.get(f"{WIZARD}/JP").json()
    assert _step(body, "hs")["counts"] == {"hs_codes": 1}
    assert _step(body, "ingredient_rules")["counts"] == {"rules": 1}
    assert (body["counted_steps"], body["done_steps"]) == (4, 4)
    # 다른 나라의 HS는 세지 않는다
    other = cert.get(f"{WIZARD}/US").json()
    assert _step(other, "hs")["status"] == "TODO"


def test_the_wizard_rejects_a_bad_code_and_is_readable_by_every_role(
    viewer: TestClient,
) -> None:
    assert viewer.get(f"{WIZARD}/USA").status_code == 422
    assert viewer.get(f"{WIZARD}/1").status_code == 422
    assert viewer.get(f"{WIZARD}/ZZ").status_code == 200  # 카탈로그 밖 시장도 위저드는 열린다
    assert viewer.get(f"{WIZARD}/ZZ").json()["catalog_available"] is False


def test_a_market_with_drafts_only_is_in_progress_and_a_deleted_market_counts_as_unregistered(
    cert: TestClient,
) -> None:
    from app.core.time import utcnow

    _apply(cert, "SG")
    assert _step(cert.get(f"{WIZARD}/SG").json(), "templates")["status"] == "IN_PROGRESS"
    market_id = _market_id("SG")
    with unit_of_work() as uow:
        row = uow.session.get(Market, market_id)
        assert row is not None
        row.deleted_at = utcnow()
    body = cert.get(f"{WIZARD}/SG").json()
    assert body["market_id"] is None and _step(body, "market")["status"] == "TODO"


def test_templates_step_stays_in_progress_while_any_draft_remains_and_ignores_other_markets(
    cert: TestClient,
) -> None:
    """확정 1건+초안 잔존은 진행 중(완료 아님) — 다른 시장의 템플릿은 세지 않는다"""
    _apply(cert, "EU", "US")
    row = _templates("EU")[0]
    patched = cert.patch(
        f"/api/v1/requirement-templates/{row.id}",
        json=_edit_body(row, last_verified_on=date(2026, 9, 30).isoformat()),
    )
    assert (
        cert.post(
            f"/api/v1/requirement-templates/{row.id}/confirm",
            json={"version": patched.json()["version"]},
            headers=_key(),
        ).status_code
        == 200
    )
    eu = _step(cert.get(f"{WIZARD}/EU").json(), "templates")
    assert eu["status"] == "IN_PROGRESS"
    assert eu["counts"] == {
        "draft": _catalog_count("EU") - 1,
        "confirmed": 1,
        "retired": 0,
        "total": _catalog_count("EU"),
    }
    us = _step(cert.get(f"{WIZARD}/US").json(), "templates")
    assert us["counts"] == {
        "draft": _catalog_count("US"),
        "confirmed": 0,
        "retired": 0,
        "total": _catalog_count("US"),
    }


# ═══ 적대 검증 반영 (S2-4 PR-2 렌즈 C) ═══════════════════════════════════════


def test_the_wizard_ignores_other_countries_rules_and_counts_every_row(
    cert: TestClient,
) -> None:
    """성분 규칙·HS 건수는 그 국가만, 여러 건이면 전부 센다"""
    from tests.support.factories import create_ingredient, create_ingredient_rule, create_sku

    create_ingredient_rule(create_ingredient("Aqua"), country_code="JP")
    body = cert.get(f"{WIZARD}/US").json()
    assert _step(body, "ingredient_rules")["counts"] == {"rules": 0}
    for name in ("Glycerin", "Niacinamide", "Retinol"):
        create_ingredient_rule(create_ingredient(name), country_code="US")
    _add_hs(create_sku("SKU-WIZ-A"), "US")
    _add_hs(create_sku("SKU-WIZ-B"), "US")
    body = cert.get(f"{WIZARD}/US").json()
    assert _step(body, "ingredient_rules")["counts"] == {"rules": 3}
    assert _step(body, "hs")["counts"] == {"hs_codes": 2}


def test_only_retired_templates_leave_the_step_as_todo_and_registered_without_seed_is_todo(
    cert: TestClient,
) -> None:
    """등록만 하고 미투입 → 할 일, 은퇴(RETIRED)만 남아도 진행 중이 아니라 할 일"""
    create_market("AU", name_ko="호주")
    assert _step(cert.get(f"{WIZARD}/AU").json(), "templates")["status"] == "TODO"
    _apply(cert, "SG")
    (row,) = _templates("SG")
    retired = cert.patch(
        f"/api/v1/requirement-templates/{row.id}", json=_edit_body(row, status="RETIRED")
    )
    assert retired.status_code == 200, retired.text
    step = _step(cert.get(f"{WIZARD}/SG").json(), "templates")
    assert step["status"] == "TODO"
    assert step["counts"] == {"draft": 0, "confirmed": 0, "retired": 1, "total": 1}


def test_a_prerequisite_that_already_exists_is_linked_by_name(cert: TestClient) -> None:
    """사람이 먼저 만든 같은 이름의 RP가 있으면 새 CPNP의 선행요건은 그 행이다(링크가 조용히 사라지지 않는다)"""
    catalog = {item.key: item for item in load_catalog().market("EU").templates}  # type: ignore[union-attr]
    market_id = create_market("EU")
    with unit_of_work() as uow:
        manual = RequirementTemplate(
            market_id=market_id,
            name=catalog["eu-rp"].name,
            applies_to="COMPANY",
            requirement_type="MANUAL",
        )
        uow.session.add(manual)
        uow.session.flush()
        manual_id = manual.id
    result = _apply(cert, "EU").json()["results"][0]
    assert "eu-rp" in result["skipped"] and "eu-cpnp" in result["created"]
    cpnp = next(row for row in _templates("EU") if row.name == catalog["eu-cpnp"].name)
    with unit_of_work() as uow:
        links = list(
            uow.session.execute(
                select(TemplatePrerequisite.prerequisite_template_id).where(
                    TemplatePrerequisite.template_id == cpnp.id
                )
            ).scalars()
        )
    assert links == [manual_id]


def test_concurrent_applies_with_different_keys_never_fail(cert: TestClient) -> None:
    """서로 다른 멱등 키로 동시에 같은 시장을 투입해도 유일 제약 위반 없이 한쪽만 만들고 나머지는 건너뛴다

    ★ TestClient는 요청을 한 줄로 세우므로 서비스를 스레드로 직접 부른다(연결이 따로 열린다)."""
    from concurrent.futures import ThreadPoolExecutor

    from app.modules.identity.service import AuthenticatedUser
    from app.modules.seeds import service as seeds

    actor = AuthenticatedUser(
        id=create_user("seed-race@example.com", roles=(RoleCode.CERT,)),
        email="seed-race@example.com",
        display_name="경쟁",
        roles=frozenset({RoleCode.CERT}),
        session_id=0,
    )

    def call(index: int) -> dict[str, Any]:
        return seeds.apply_t1(
            actor=actor, idempotency_key=f"seed-race-{index}", payload={"markets": ["US"]}
        )[1]

    with ThreadPoolExecutor(max_workers=4) as pool:
        bodies = list(pool.map(call, range(4)))
    created = sum(len(body["results"][0]["created"]) for body in bodies)
    assert created == _catalog_count("US")
    assert len(_templates("US")) == _catalog_count("US")
    assert cert  # fixture 사용 표시


def test_admin_and_disabled_cli_actor(capsys: pytest.CaptureFixture[str]) -> None:
    """관리자는 투입 가능(인증+관리자), CLI는 비활성 계정을 투입자로 받지 않는다"""
    from app.cli import main as cli_main
    from app.modules.identity.models import User

    admin_id = create_user("seed-admin@example.com", roles=(RoleCode.ADMIN,))
    with TestClient(app) as client:
        assert (
            client.post(
                LOGIN, json={"email": "seed-admin@example.com", "password": DEFAULT_PASSWORD}
            ).status_code
            == 200
        )
        assert _apply(client, "CA").status_code == 200
    with unit_of_work() as uow:
        user = uow.session.get(User, admin_id)
        assert user is not None
        user.is_active = False
    assert cli_main(["seed-t1", "--market", "AU", "--actor-email", "seed-admin@example.com"]) == 1
    assert "찾을 수 없습니다" in capsys.readouterr().err
    assert _templates("AU") == []
