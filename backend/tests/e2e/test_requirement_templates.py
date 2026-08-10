"""C·G·J·K. 요건 템플릿 API — 적용단위 5종 CRUD·확정 게이트·권한 (WBS S2-1 DoD).

이 파일이 고정하는 것: 적용단위 5종이 실 API로 등록·조회·편집된다는 것(DoD),
근거 없는 확정이 422로 거부되고 근거 완비 확정이 성공한다는 것(GC-C8 양방향 —
골든), 편집·확정은 인증+관리자뿐이라는 것(판정 ⑤ — 무역·물류·조회는 열람),
확정 더블클릭이 1건이라는 것(§17.4), 체크리스트·선행요건·품목군 세트가 실
API로 왕복한다는 것.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_item_profile,
    create_market,
    create_user,
)

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
TEMPLATES = "/api/v1/requirement-templates"

Create = Callable[..., Response]

_EVIDENCE = {"source_url": "https://example.test/mocra", "last_verified_on": "2026-08-01"}


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("cert@example.com", RoleCode.CERT)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer@example.com", RoleCode.VIEWER)


@pytest.fixture
def market_id() -> int:
    return create_market("US", name_ko="미국")


@pytest.fixture
def create(cert: TestClient, market_id: int) -> Create:
    def _create(*, key: str = "tpl-1", **overrides: Any) -> Response:
        payload: dict[str, Any] = {
            "market_id": market_id,
            "name": "MoCRA 시설등록",
            "applies_to": "FACILITY",
            "requirement_type": "REGISTRATION",
        }
        payload.update(overrides)
        return cert.post(TEMPLATES, json=payload, headers={"Idempotency-Key": key})

    return _create


def _error_code(response: Response) -> str:
    return response.json()["error"]["code"]


# ── DoD: 적용단위 5종 동작 (생성·조회·편집 e2e) ─────────────────────────────


@pytest.mark.parametrize("applies_to", ["PRODUCT", "SKU", "FACILITY", "COMPANY", "INGREDIENT"])
def test_every_applies_to_kind_can_be_registered_read_and_edited(
    cert: TestClient, create: Create, applies_to: str
) -> None:
    """적용단위 5종 각각 등록→상세→목록→편집이 왕복한다 (WBS S2-1 DoD 문면)"""
    created = create(key=f"dod-{applies_to}", name=f"{applies_to} 요건", applies_to=applies_to)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["applies_to"] == applies_to
    assert body["status"] == "DRAFT", "등록은 항상 초안으로 시작한다"

    detail = cert.get(f"{TEMPLATES}/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["market_code"] == "US"

    listed = cert.get(TEMPLATES, params={"market_id": body["market_id"]})
    assert listed.status_code == 200
    names = [row["name"] for row in listed.json()["items"]]
    assert f"{applies_to} 요건" in names

    edited = cert.patch(
        f"{TEMPLATES}/{body['id']}",
        json={
            "version": body["version"],
            "name": f"{applies_to} 요건(개정)",
            "applies_to": applies_to,
            "requirement_type": "NOTIFICATION",
            "status": "DRAFT",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == f"{applies_to} 요건(개정)"
    assert edited.json()["requirement_type"] == "NOTIFICATION"
    assert edited.json()["version"] == body["version"] + 1


def test_the_type_code_is_normalized_like_market_codes(create: Create) -> None:
    """소문자 유형 코드는 대문자로 정규화된다 ('us'→'US' 선례)"""
    created = create(key="norm", requirement_type="registration")
    assert created.status_code == 201
    assert created.json()["requirement_type"] == "REGISTRATION"


def test_a_duplicate_name_in_the_same_market_is_guided(create: Create) -> None:
    assert create(key="dup-1").status_code == 201
    duplicated = create(key="dup-2")
    assert duplicated.status_code == 422
    assert "이미 있습니다" in duplicated.json()["error"]["detail"]["name"]


def test_the_status_filter_narrows_the_list(cert: TestClient, create: Create) -> None:
    draft = create(key="f-draft", name="초안 요건")
    confirmed = create(key="f-conf", name="확정 요건", **_EVIDENCE)
    assert draft.status_code == 201 and confirmed.status_code == 201
    body = confirmed.json()
    ok = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "f-conf-go"},
    )
    assert ok.status_code == 200

    only_confirmed = cert.get(TEMPLATES, params={"status": "CONFIRMED"})
    names = [row["name"] for row in only_confirmed.json()["items"]]
    assert names == ["확정 요건"]


# ── GC-C8: 근거 없는 템플릿 확정 거부 (골든 — 양방향 자기검사, 조건 7) ──────


@pytest.mark.golden
@pytest.mark.group_g
def test_gc_c8_confirming_without_evidence_is_rejected_and_the_draft_survives(
    cert: TestClient, create: Create
) -> None:
    """GC-C8 거부 방향 — 근거 2필드 결여 확정: 422·초안 유지·차단 사유 표시"""
    body = create(key="gc-none").json()
    rejected = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "gc-none-go"},
    )
    assert rejected.status_code == 422, rejected.text
    assert _error_code(rejected) == "REQUIREMENTS.TEMPLATE.EVIDENCE_REQUIRED"
    detail = rejected.json()["error"]["detail"]
    assert set(detail) == {"source_url", "last_verified_on"}, "차단 사유가 필드별로 표시된다"

    after = cert.get(f"{TEMPLATES}/{body['id']}").json()
    assert after["status"] == "DRAFT", "거부 후에도 초안이 유지된다"


@pytest.mark.golden
@pytest.mark.group_g
def test_gc_c8_confirming_with_one_field_missing_is_still_rejected(
    cert: TestClient, create: Create
) -> None:
    """GC-C8 거부 방향(부분 결여) — 근거링크만으로는 확정할 수 없다 (ADR-03 둘 다 필수)"""
    body = create(key="gc-half", source_url=_EVIDENCE["source_url"]).json()
    rejected = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "gc-half-go"},
    )
    assert rejected.status_code == 422
    assert set(rejected.json()["error"]["detail"]) == {"last_verified_on"}


@pytest.mark.golden
@pytest.mark.group_g
def test_gc_c8_confirming_with_full_evidence_succeeds(cert: TestClient, create: Create) -> None:
    """GC-C8 성공 방향 — 근거 완비 확정은 성공한다 (게이트 과차단 자기검사)"""
    body = create(key="gc-full", **_EVIDENCE).json()
    confirmed = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "gc-full-go"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"


# ── 확정 편집 잠금·재확정 경로 (조건 11 — 실 API) ───────────────────────────


def test_a_confirmed_template_rejects_edits_until_reverted(
    cert: TestClient, create: Create
) -> None:
    """확정 → 편집 409 → 초안 전환 → 편집 → 재확정이 실 API로 왕복한다"""
    body = create(key="lock", **_EVIDENCE).json()
    confirmed = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "lock-go"},
    ).json()

    edit = {
        "version": confirmed["version"],
        "name": "바꿔치기",
        "applies_to": "FACILITY",
        "requirement_type": "REGISTRATION",
        "source_url": _EVIDENCE["source_url"],
        "last_verified_on": _EVIDENCE["last_verified_on"],
        "status": "DRAFT",
    }
    locked = cert.patch(f"{TEMPLATES}/{body['id']}", json=edit)
    assert locked.status_code == 409
    assert _error_code(locked) == "REQUIREMENTS.TEMPLATE.CONFIRMED_LOCKED"

    reverted = cert.post(
        f"{TEMPLATES}/{body['id']}/revert-to-draft", json={"version": confirmed["version"]}
    )
    assert reverted.status_code == 200
    assert reverted.json()["status"] == "DRAFT"
    assert reverted.json()["source_url"] == _EVIDENCE["source_url"], "전환은 근거를 지우지 않는다"

    edited = cert.patch(
        f"{TEMPLATES}/{body['id']}", json={**edit, "version": reverted.json()["version"]}
    )
    assert edited.status_code == 200
    reconfirmed = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": edited.json()["version"]},
        headers={"Idempotency-Key": "lock-rego"},
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["status"] == "CONFIRMED"


def test_the_edit_status_field_cannot_confirm(cert: TestClient, create: Create) -> None:
    """PATCH의 status로는 CONFIRMED에 들어갈 수 없다 — 스키마가 422로 거부한다"""
    body = create(key="smuggle", **_EVIDENCE).json()
    smuggled = cert.patch(
        f"{TEMPLATES}/{body['id']}",
        json={
            "version": body["version"],
            "name": body["name"],
            "applies_to": body["applies_to"],
            "requirement_type": body["requirement_type"],
            "status": "CONFIRMED",
        },
    )
    assert smuggled.status_code == 422
    assert cert.get(f"{TEMPLATES}/{body['id']}").json()["status"] == "DRAFT"


# ── 확정 멱등 (§17.4 — J) ──────────────────────────────────────────────────


@pytest.mark.group_j
def test_confirm_double_click_replays_the_first_result(cert: TestClient, create: Create) -> None:
    """확정 더블클릭 = 확정 1건·동일 응답 재생 (§20 J — 판정 핵심 계약)"""
    body = create(key="dbl", **_EVIDENCE).json()
    first = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "dbl-go"},
    )
    second = cert.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "dbl-go"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json(), "재클릭은 최초 결과 그대로다"
    assert cert.get(f"{TEMPLATES}/{body['id']}").json()["version"] == body["version"] + 1


# ── 권한 (판정 ⑤ — K) ──────────────────────────────────────────────────────


@pytest.mark.group_k
def test_a_trader_can_read_but_not_edit(trader: TestClient, create: Create) -> None:
    """무역은 열람까지다 — 등록·확정은 403 (판정 ⑤ 문면)"""
    body = create(key="perm").json()
    assert trader.get(TEMPLATES).status_code == 200
    assert trader.get(f"{TEMPLATES}/{body['id']}").status_code == 200
    denied = trader.post(
        TEMPLATES,
        json={
            "market_id": body["market_id"],
            "name": "무역이 만든 요건",
            "applies_to": "SKU",
            "requirement_type": "REGISTRATION",
        },
        headers={"Idempotency-Key": "perm-trade"},
    )
    assert denied.status_code == 403
    denied_confirm = trader.post(
        f"{TEMPLATES}/{body['id']}/confirm",
        json={"version": body["version"]},
        headers={"Idempotency-Key": "perm-trade-confirm"},
    )
    assert denied_confirm.status_code == 403


@pytest.mark.group_k
def test_a_viewer_can_read_but_not_edit(viewer: TestClient, create: Create) -> None:
    body = create(key="perm-v").json()
    assert viewer.get(f"{TEMPLATES}/{body['id']}/checklist").status_code == 200
    denied = viewer.patch(
        f"{TEMPLATES}/{body['id']}",
        json={
            "version": body["version"],
            "name": "조회가 고친 요건",
            "applies_to": "SKU",
            "requirement_type": "REGISTRATION",
            "status": "DRAFT",
        },
    )
    assert denied.status_code == 403


# ── 체크리스트 왕복 (§5.1) ─────────────────────────────────────────────────


def test_the_checklist_roundtrip_with_a_document_type(cert: TestClient, create: Create) -> None:
    """항목 추가(서류 참조·비참조)→목록(순번순)→수정→제거가 왕복한다"""
    template_id = create(key="chk").json()["id"]
    with_doc = cert.post(
        f"{TEMPLATES}/{template_id}/checklist",
        json={"seq": 2, "item_name": "MSDS 첨부", "document_type": "MSDS", "is_required": True},
        headers={"Idempotency-Key": "chk-doc"},
    )
    assert with_doc.status_code == 201, with_doc.text
    assert with_doc.json()["document_type_code"] == "MSDS"
    without_doc = cert.post(
        f"{TEMPLATES}/{template_id}/checklist",
        json={"seq": 1, "item_name": "시설 정보 확인", "is_required": False},
        headers={"Idempotency-Key": "chk-plain"},
    )
    assert without_doc.status_code == 201
    assert without_doc.json()["document_type_code"] is None

    listed = cert.get(f"{TEMPLATES}/{template_id}/checklist").json()
    assert [row["seq"] for row in listed["items"]] == [1, 2], "목록은 순번순이다"

    item = without_doc.json()
    edited = cert.patch(
        f"{TEMPLATES}/{template_id}/checklist/{item['id']}",
        json={
            "version": item["version"],
            "seq": 3,
            "item_name": "시설 정보 확인(개정)",
            "is_required": True,
        },
    )
    assert edited.status_code == 200
    assert edited.json()["seq"] == 3

    removed = cert.delete(f"{TEMPLATES}/{template_id}/checklist/{item['id']}")
    assert removed.status_code == 204
    remaining = cert.get(f"{TEMPLATES}/{template_id}/checklist").json()
    assert remaining["total"] == 1


def test_a_duplicate_checklist_seq_is_guided(cert: TestClient, create: Create) -> None:
    template_id = create(key="chk-dup").json()["id"]
    first = cert.post(
        f"{TEMPLATES}/{template_id}/checklist",
        json={"seq": 1, "item_name": "항목", "is_required": True},
        headers={"Idempotency-Key": "chk-dup-1"},
    )
    assert first.status_code == 201
    duplicated = cert.post(
        f"{TEMPLATES}/{template_id}/checklist",
        json={"seq": 1, "item_name": "다른 항목", "is_required": True},
        headers={"Idempotency-Key": "chk-dup-2"},
    )
    assert duplicated.status_code == 422
    assert "순번" in duplicated.json()["error"]["detail"]["seq"]


# ── 선행요건 왕복 (조건 1 — 실 API) ────────────────────────────────────────


def test_the_prerequisite_roundtrip_and_cycle_rejection(cert: TestClient, create: Create) -> None:
    """추가→목록→자기참조 422→역방향(순환) 422→해제가 왕복한다"""
    a = create(key="pre-a", name="NMPA 비안").json()
    b = create(key="pre-b", name="경내책임자 지정").json()

    added = cert.post(
        f"{TEMPLATES}/{a['id']}/prerequisites",
        json={"prerequisite_template_id": b["id"]},
        headers={"Idempotency-Key": "pre-ab"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["prerequisite_name"] == "경내책임자 지정"

    self_ref = cert.post(
        f"{TEMPLATES}/{a['id']}/prerequisites",
        json={"prerequisite_template_id": a["id"]},
        headers={"Idempotency-Key": "pre-self"},
    )
    assert self_ref.status_code == 422
    assert _error_code(self_ref) == "REQUIREMENTS.PREREQUISITE.CYCLE"

    cycle = cert.post(
        f"{TEMPLATES}/{b['id']}/prerequisites",
        json={"prerequisite_template_id": a["id"]},
        headers={"Idempotency-Key": "pre-ba"},
    )
    assert cycle.status_code == 422
    assert _error_code(cycle) == "REQUIREMENTS.PREREQUISITE.CYCLE"

    listed = cert.get(f"{TEMPLATES}/{a['id']}/prerequisites").json()
    assert listed["total"] == 1
    removed = cert.delete(f"{TEMPLATES}/{a['id']}/prerequisites/{listed['items'][0]['id']}")
    assert removed.status_code == 204
    assert cert.get(f"{TEMPLATES}/{a['id']}/prerequisites").json()["total"] == 0


# ── 품목군 요건 세트 왕복 (§4.8 — ADR-0035) ────────────────────────────────


def test_the_profile_requirement_set_roundtrip(cert: TestClient, create: Create) -> None:
    """세트 추가→중복 422→목록→제거가 왕복한다 (item_profile_document_types 선례 동형)"""
    profile_id = create_item_profile("PRF-E2E")
    template = create(key="set").json()
    base = f"/api/v1/item-profiles/{profile_id}/requirement-templates"

    added = cert.post(
        base,
        json={"requirement_template_id": template["id"]},
        headers={"Idempotency-Key": "set-add"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["template_name"] == "MoCRA 시설등록"
    assert added.json()["market_code"] == "US"

    duplicated = cert.post(
        base,
        json={"requirement_template_id": template["id"]},
        headers={"Idempotency-Key": "set-add-2"},
    )
    assert duplicated.status_code == 422

    listed = cert.get(base).json()
    assert listed["total"] == 1

    removed = cert.delete(f"{base}/{listed['items'][0]['id']}")
    assert removed.status_code == 204
    assert cert.get(base).json()["total"] == 0
