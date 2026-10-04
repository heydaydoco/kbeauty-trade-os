"""K·A. 품목군 마일스톤 세트 쓰기 경로 — `/item-profiles/{profile_id}/milestone-types`(관리자 전용) → 선적 계획 초안 반영
(S3-2 PR-4c / design-integrated N-01·§9 R-14·R-26 / ADR-0079 ⑥·0085 ④ / design-B B16 / 부채 #15 마일스톤 몫 종결).

■ 검증 K: 추가·제거 = 관리자만(무역·물류·인증·조회 403 — 존재·입력 검사보다 먼저), 조회 = 전 역할, 중복 409 `DUPLICATE_TYPE`, 파생·OEM 종류
  422 `TYPE_NOT_APPLICABLE`, 없는 품목군·남의 세트 행 404(부작용 0), Page 기본 50·업무 흐름 순, 같은 키 재요청 = 최초 결과.
■ 검증 A(B16 배선): API로 만든 세트가 선적 계획 초안의 적용 종류를 좁힌다(구분별 적용 집합 ∩ 세트 합집합), 세트를 비우면 다시 구분별 전부.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from tests.factories.shipments import SHIPMENTS, confirmed_so, created, rows, scalar
from tests.factories.trade import idem, logged_in, unique
from tests.support.factories import create_item_profile

pytestmark = pytest.mark.group_k

PROFILES = "/api/v1/item-profiles"


@pytest.fixture
def admin():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.ADMIN) as client:
        yield client


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _add(client: TestClient, profile_id: int, milestone_type: str, *, key: str = "") -> Any:
    return client.post(
        f"{PROFILES}/{profile_id}/milestone-types",
        json={"milestone_type": milestone_type},
        headers={"Idempotency-Key": key or unique("set")},
    )


def _types(client: TestClient, profile_id: int) -> list[str]:
    response = client.get(f"{PROFILES}/{profile_id}/milestone-types")
    assert response.status_code == 200, response.text
    return [item["milestone_type"] for item in response.json()["items"]]


def _live(profile_id: int) -> int:
    return int(
        scalar(
            "SELECT count(*) FROM item_profile_milestone_types"
            " WHERE profile_id = :p AND deleted_at IS NULL",
            p=profile_id,
        )
    )


def test_admin_builds_a_set_in_flow_order_and_everyone_reads_it(admin: TestClient) -> None:
    """추가 201(행위자 기록) → 목록 = 업무 흐름 순(ETA 먼저 넣어도 ETD 뒤)·Page 기본 50, 전 역할 조회 200"""
    profile = create_item_profile(unique("PRF"))
    for milestone_type in ("ETA", "DOC_CUTOFF", "ETD"):
        response = _add(admin, profile, milestone_type)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["item_profile_id"] == profile and body["milestone_type"] == milestone_type
    page = admin.get(f"{PROFILES}/{profile}/milestone-types").json()
    assert page["size"] == 50 and page["total"] == 3
    assert [item["milestone_type"] for item in page["items"]] == ["DOC_CUTOFF", "ETD", "ETA"]
    actor = scalar(
        "SELECT count(DISTINCT created_by_id) FROM item_profile_milestone_types WHERE profile_id = :p",
        p=profile,
    )
    assert actor == 1
    for role in (RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert _types(client, profile) == ["DOC_CUTOFF", "ETD", "ETA"]


def test_only_admin_writes_the_set_before_any_existence_or_input_check(admin: TestClient) -> None:
    """R-14 — 무역·물류·인증·조회 전용의 추가·제거 = 403(없는 품목군·모르는 종류여도 403 — 401→403→404→422), 행 0"""
    profile = create_item_profile(unique("PRF"))
    link = _add(admin, profile, "ETD").json()["id"]
    for role in (RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert _add(client, profile, "ETA").status_code == 403
            assert _add(client, 99_999_999, "NOPE").status_code == 403
            assert client.delete(f"{PROFILES}/{profile}/milestone-types/{link}").status_code == 403
    assert _types(admin, profile) == ["ETD"]


def test_duplicates_are_409_and_derived_or_oem_types_are_422(admin: TestClient) -> None:
    """R-26 — 같은 종류 재추가 = 409 DUPLICATE_TYPE(detail 동반, 행 1), 파생 3종·OEM 4종 = 422 TYPE_NOT_APPLICABLE(행 0), 모르는 종류·
    여분 필드 = 스키마 422. 같은 Idempotency-Key 재요청 = 최초 201 결과(중복 409 아님)"""
    profile = create_item_profile(unique("PRF"))
    key = unique("same")
    first = _add(admin, profile, "PSI", key=key)
    again = _add(admin, profile, "PSI", key=key)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    duplicate = _add(admin, profile, "PSI")
    assert (duplicate.status_code, _code(duplicate)) == (409, "SHIPMENTS.MILESTONE.DUPLICATE_TYPE")
    assert "milestone_type" in duplicate.json()["error"]["detail"]
    for milestone_type in (
        "LOADING_DEADLINE",
        "PAYMENT_DUE",
        "PRESENTATION_DEADLINE",
        "RAW_MATERIAL_READY",
        "FILLING",
        "PACKING",
        "OUTGOING_INSPECTION",
    ):
        response = _add(admin, profile, milestone_type)
        assert (response.status_code, _code(response)) == (
            422,
            "SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE",
        ), milestone_type
        # 서비스 1차 검사가 낸 422(입력처 안내 detail 동반) — DB CHECK 번역(2차 방어선)은 detail이 없다(층 구분 — 4a ⑤ 선례)
        assert "선적 일정 종류" in response.json()["error"]["detail"]["milestone_type"]
    assert _add(admin, profile, "ATD").status_code == 422
    extra = admin.post(
        f"{PROFILES}/{profile}/milestone-types",
        json={"milestone_type": "ETA", "profile_id": profile},
        headers=idem(),
    )
    assert extra.status_code == 422
    assert _live(profile) == 1


def test_missing_profiles_and_foreign_links_are_404_without_side_effects(admin: TestClient) -> None:
    """D:370 — 없는 품목군의 목록·추가·제거 = 404(서류 세트 선례의 422보다 엄격 — 경로 자원), 다른 품목군의 세트 행 id·이미 제거한 행 = 404,
    404 순서가 입력 422보다 먼저(없는 품목군 + 파생 종류 = 404)"""
    profile = create_item_profile(unique("PRF"))
    other = create_item_profile(unique("PRF"))
    link = _add(admin, profile, "ETA").json()["id"]
    assert admin.get(f"{PROFILES}/99999999/milestone-types").status_code == 404
    assert _add(admin, 99_999_999, "ETD").status_code == 404
    assert _add(admin, 99_999_999, "PAYMENT_DUE").status_code == 404
    assert admin.delete(f"{PROFILES}/99999999/milestone-types/{link}").status_code == 404
    assert admin.delete(f"{PROFILES}/{other}/milestone-types/{link}").status_code == 404
    assert _live(profile) == 1
    assert admin.delete(f"{PROFILES}/{profile}/milestone-types/{link}").status_code == 204
    assert admin.delete(f"{PROFILES}/{profile}/milestone-types/{link}").status_code == 404
    assert _live(profile) == 0


def test_a_removed_type_reenters_as_a_new_row(admin: TestClient) -> None:
    """§17.4 — 제거 = soft delete(행 보존), 재추가 = 새 행(부활 아님 — 다른 id)"""
    profile = create_item_profile(unique("PRF"))
    old = _add(admin, profile, "CARGO_CLOSING").json()["id"]
    assert admin.delete(f"{PROFILES}/{profile}/milestone-types/{old}").status_code == 204
    new = _add(admin, profile, "CARGO_CLOSING")
    assert new.status_code == 201 and new.json()["id"] != old
    assert (
        scalar("SELECT count(*) FROM item_profile_milestone_types WHERE profile_id = :p", p=profile)
        == 2
    )
    assert _types(admin, profile) == ["CARGO_CLOSING"]


@pytest.mark.group_a
def test_sets_written_through_the_api_narrow_the_plan_draft(admin: TestClient) -> None:
    """B16 배선 — API로 만든 세트 합집합 ∩ 수출 적용 집합만 초안 행이 된다(수입 세금 납부기한은 수출 비적용이라 빠짐). 한 SKU라도 세트가 비면
    구분별 전부(누락보다 과다). 세트를 바꾸면 **다음** 초안부터 반영되고 이미 만든 선적 행은 그대로다"""
    so = confirmed_so((5, 5))
    profile_a = create_item_profile(unique("PRF"))
    profile_b = create_item_profile(unique("PRF"))
    with owner_engine.begin() as connection:
        for sku, profile in zip(so["sku_ids"], (profile_a, profile_b), strict=True):
            connection.execute(
                text("UPDATE skus SET item_profile_id = :p WHERE id = :s"), {"p": profile, "s": sku}
            )
    for milestone_type in ("ETD", "ETA"):
        assert _add(admin, profile_a, milestone_type).status_code == 201
    for milestone_type in ("PSI", "IMPORT_TAX_DUE"):
        assert _add(admin, profile_b, milestone_type).status_code == 201
    with logged_in(RoleCode.TRADE) as trade:
        shipment = created(trade, so["id"], [(line, 1) for line in so["line_ids"]])
        draft = trade.post(
            f"{SHIPMENTS}/{shipment['id']}/milestones/plan-draft", json={}, headers=idem()
        )
        assert draft.status_code == 200, draft.text
        first = {
            r["milestone_type"]
            for r in rows(
                "SELECT milestone_type FROM milestones WHERE shipment_id = :s", s=shipment["id"]
            )
        }
        assert first == {"ETD", "ETA", "PSI"}
        # 품목군 B의 세트를 비우면(제거) 다음 초안은 구분별 전부 — 이미 만든 행은 건너뛰고 나머지만 더한다
        for item in admin.get(f"{PROFILES}/{profile_b}/milestone-types").json()["items"]:
            assert (
                admin.delete(f"{PROFILES}/{profile_b}/milestone-types/{item['id']}").status_code
                == 204
            )
        again = trade.post(
            f"{SHIPMENTS}/{shipment['id']}/milestones/plan-draft", json={}, headers=idem()
        )
        assert again.status_code == 200
        assert (
            scalar("SELECT count(*) FROM milestones WHERE shipment_id = :s", s=shipment["id"]) == 7
        )
