"""C. 라벨·아트웍 버전 (§4.5 / §14 ③ / WBS S1-2).

라벨의 실체는 **SKU×시장**이다(§4.5) — 등록·수정은 SKU 하위 자원이고, 제품
상세의 라벨 자리(§14 ③)는 소속 SKU 라벨의 **읽기 전용 롤업**이다.
시스템이 라벨 이미지를 읽어 판정하지 않는다(§1 비범위) — 사람이 확인한
검증 항목(INCI 현지어·원산지 표기)의 기록이다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_product, create_sku, create_user

pytestmark = pytest.mark.group_c

LOGIN = "/api/v1/auth/login"
SKUS = "/api/v1/skus"
PRODUCTS = "/api/v1/products"

Create = Callable[..., Response]


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "trade@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    create_user("viewer@example.com", roles=(RoleCode.VIEWER,))
    with TestClient(app) as client:
        response = client.post(
            LOGIN, json={"email": "viewer@example.com", "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def sku_id() -> int:
    return create_sku()


@pytest.fixture
def create_label(trader: TestClient, sku_id: int) -> Create:
    def _create(*, key: str = "label1", **overrides: Any) -> Response:
        body: dict[str, Any] = {
            "country_code": "US",
            "label_version": 1,
            "language": "en",
        }
        body.update(overrides)
        return trader.post(f"{SKUS}/{sku_id}/labels", json=body, headers={"Idempotency-Key": key})

    return _create


# ── 등록·조회 ───────────────────────────────────────────────────────────────


def test_create_then_list(trader: TestClient, sku_id: int, create_label: Create) -> None:
    """라벨 판을 등록하고 목록에서 다시 본다 (§22 렌즈 11)"""
    created = create_label()
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["approval_status"] == "DRAFT"  # 기본값
    assert body["inci_local_verified"] is False
    assert body["origin_mark_verified"] is False

    listed = trader.get(f"{SKUS}/{sku_id}/labels").json()
    assert listed["total"] == 1
    assert listed["items"][0]["label_version"] == 1


def test_country_code_is_normalized_to_uppercase(create_label: Create) -> None:
    """'us'로 적어도 'US'로 저장된다 — 유일키가 표기 차이에 뚫리면 안 된다"""
    created = create_label(country_code="us")
    assert created.status_code == 201, created.text
    assert created.json()["country_code"] == "US"


def test_verification_flags_are_recorded(create_label: Create) -> None:
    """§4.5 검증 항목 2종은 사람이 확인했다는 **기록**으로 저장된다"""
    created = create_label(inci_local_verified=True, origin_mark_verified=True)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["inci_local_verified"] is True
    assert body["origin_mark_verified"] is True


def test_all_three_approval_statuses_are_accepted(create_label: Create) -> None:
    """초안·승인·폐기 3값이 저장된다. 전이는 강제하지 않는다(§5.2는 인증 전용)"""
    for index, status_value in enumerate(("DRAFT", "APPROVED", "RETIRED")):
        created = create_label(
            key=f"s{index}", label_version=index + 1, approval_status=status_value
        )
        assert created.status_code == 201, created.text
        assert created.json()["approval_status"] == status_value


def test_unknown_approval_status_is_rejected(create_label: Create) -> None:
    rejected = create_label(approval_status="PENDING_REVIEW")
    assert rejected.status_code == 422, rejected.text


def test_cut_in_date_may_be_set_before_approval(create_label: Create) -> None:
    """승인 전에도 컷인일을 적을 수 있다 — 일정이 먼저 잡히는 실무가 있다"""
    created = create_label(approval_status="DRAFT", cut_in_date="2026-09-01")
    assert created.status_code == 201, created.text
    assert created.json()["cut_in_date"] == "2026-09-01"


def test_same_market_and_version_twice_is_rejected(create_label: Create) -> None:
    """같은 시장·판번은 한 번만 — 새 판이면 판번을 올린다"""
    first = create_label(key="v1")
    assert first.status_code == 201, first.text

    duplicate = create_label(key="v2")
    assert duplicate.status_code == 422, duplicate.text
    assert "판번을 올려" in duplicate.text


def test_new_version_for_the_same_market_is_allowed(create_label: Create) -> None:
    """같은 시장의 다음 판은 등록된다 — 판 이력이 남는다(§4.5 "버전")"""
    assert create_label(key="v1", label_version=1).status_code == 201
    second = create_label(key="v2", label_version=2)
    assert second.status_code == 201, second.text


def test_a_set_sku_can_have_a_label(trader: TestClient) -> None:
    """세트 SKU도 라벨을 갖는다 — 라벨은 SKU 단위다(처방 단위가 아니다)

    성분·BOM이 처방(제품) 축인 것과 대비된다(ADR-0016).
    """
    from app.core.db.uow import unit_of_work
    from app.modules.catalog.models import Sku

    with unit_of_work() as uow:
        set_sku = Sku(sku_code="SET-LBL", name_ko="테스트 세트", kind="SET", product_id=None)
        uow.session.add(set_sku)
        uow.session.flush()
        set_sku_id = set_sku.id

    created = trader.post(
        f"{SKUS}/{set_sku_id}/labels",
        json={"country_code": "US", "label_version": 1, "language": "en"},
        headers={"Idempotency-Key": "set-label"},
    )
    assert created.status_code == 201, created.text


# ── 제품 상세의 롤업 (§14 ③ — 읽기 전용) ──────────────────────────────────


def test_product_rollup_lists_labels_of_its_skus(trader: TestClient) -> None:
    """제품 롤업은 소속 SKU들의 라벨을 모아 보여 준다 (§14 ③)"""
    product_id = create_product("PRD-ROLL")
    first_sku = create_sku("SKU-R1", product_id=product_id)
    second_sku = create_sku("SKU-R2", product_id=product_id)
    other_sku = create_sku("SKU-OTHER")  # 다른 처방 — 롤업에 끼면 안 된다

    for index, target in enumerate((first_sku, second_sku, other_sku)):
        response = trader.post(
            f"{SKUS}/{target}/labels",
            json={"country_code": "US", "label_version": 1, "language": "en"},
            headers={"Idempotency-Key": f"roll{index}"},
        )
        assert response.status_code == 201, response.text

    rolled = trader.get(f"{PRODUCTS}/{product_id}/labels").json()
    assert rolled["total"] == 2
    assert {item["sku_code"] for item in rolled["items"]} == {"SKU-R1", "SKU-R2"}


def test_product_rollup_is_read_only(trader: TestClient) -> None:
    """롤업 경로로는 라벨을 만들 수 없다 — 등록 지점은 SKU 하나다(§4.5)"""
    product_id = create_product("PRD-RO")
    denied = trader.post(
        f"{PRODUCTS}/{product_id}/labels",
        json={"country_code": "US", "label_version": 1, "language": "en"},
        headers={"Idempotency-Key": "ro"},
    )
    assert denied.status_code == 405, denied.text


# ── 권한·구조 ───────────────────────────────────────────────────────────────


@pytest.mark.group_k
def test_viewer_cannot_create_a_label(viewer: TestClient, sku_id: int) -> None:
    """조회 역할은 라벨을 등록할 수 없다 (§18.1)"""
    denied = viewer.post(
        f"{SKUS}/{sku_id}/labels",
        json={"country_code": "US", "label_version": 1, "language": "en"},
        headers={"Idempotency-Key": "v-label"},
    )
    assert denied.status_code == 403, denied.text


@pytest.mark.group_k
def test_viewer_can_read_labels(viewer: TestClient, sku_id: int, create_label: Create) -> None:
    """라벨에는 원가가 없다 — 조회 역할이 그대로 본다"""
    assert create_label().status_code == 201
    listed = viewer.get(f"{SKUS}/{sku_id}/labels")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1


@pytest.mark.group_j
def test_same_key_replays_the_first_result(
    create_label: Create, trader: TestClient, sku_id: int
) -> None:
    """라벨 등록 더블클릭 → 1건 (§17.4)"""
    first = create_label(key="lbl-dup")
    replay = create_label(key="lbl-dup")

    assert first.status_code == 201 and replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert trader.get(f"{SKUS}/{sku_id}/labels").json()["total"] == 1


@pytest.mark.group_k
def test_labels_of_a_missing_sku_is_404(trader: TestClient) -> None:
    missing = trader.get(f"{SKUS}/999999/labels")
    assert missing.status_code == 404, missing.text
