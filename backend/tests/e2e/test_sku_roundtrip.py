"""F. SKU 엑셀 왕복 — 조건 8 "SKU 왕복 스트레치"의 재실증 (§12.2 / S1.5 판정 ①).

F 그룹 왕복 계약(무수정 diff 0·변경분만·빈 ID 신규·ID 훼손 오류·이스케이프
보존·재실행 변화 0·확정 멱등·선수정 409)을 **SKU 대상**으로 확장한다. SKU 고유
분은 이 파일이 처음 고정한다:
  - 왕복 양식 = 기록 가능 필드+ID만(판정 ①) — 파생 표시 2칸 구조적 부재
  - 종류·제품코드는 왕복 불변(immutable_fields) — 변경 diff에 잡히면 오류 행
  - DG 잠금 2방향·처방 짝 검사 — 등록 API 가드 **함수 재사용**(같은 문구)
  - 제조사코드=OEM 검증(스테이징) + 확정 시점 재검증(verify_references —
    materials 기본공급사 선례의 대칭)
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select, update

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.main import app
from app.modules.catalog.models import Sku
from app.modules.identity.models import RoleCode
from app.modules.partners.models import PartnerTypeLink
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_partner,
    create_product,
    create_user,
)

pytestmark = pytest.mark.group_f

LOGIN = "/api/v1/auth/login"
ROUNDTRIP_EXPORT = "/api/v1/skus/roundtrip.csv"
DISPLAY_EXPORT = "/api/v1/skus/export.csv"
STAGE_SKUS = "/api/v1/imports/skus/staging"
STAGING = "/api/v1/imports/staging"
TEMPLATE = "/api/v1/imports/skus/template.csv"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer@example.com", RoleCode.VIEWER)


def _rows(csv_text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(csv_text.removeprefix("﻿"), newline="")))


def _to_file(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _upload(
    client: TestClient, content: bytes, *, key: str, filename: str = "SKU왕복.csv"
) -> Response:
    return client.post(
        STAGE_SKUS,
        files={"file": (filename, content, "text/csv")},
        headers={"Idempotency-Key": key},
    )


def _staging_rows(client: TestClient, staging_id: int) -> list[dict[str, Any]]:
    response = client.get(f"{STAGING}/{staging_id}/rows", params={"size": 200})
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _confirm(client: TestClient, staging_id: int, *, key: str) -> Response:
    return client.post(f"{STAGING}/{staging_id}/confirm", headers={"Idempotency-Key": key})


def _create_full_sku(
    sku_code: str,
    *,
    product_id: int | None,
    kind: str = "SINGLE",
    manufacturer_partner_id: int | None = None,
    **overrides: Any,
) -> int:
    """전 칸이 찬 SKU — 수치·불리언·DG 값의 왕복 표기를 함께 실증하기 위한 준비."""
    columns: dict[str, Any] = {
        "sku_code": sku_code,
        "name_ko": f"{sku_code} 세럼",
        "name_en": f"{sku_code} Serum",
        "kind": kind,
        "product_id": product_id,
        "barcode": "8801234567890",
        "unit_weight_g": Decimal("12.500"),
        "box_qty": 24,
        "shelf_life_months": 36,
        "manufacturer_partner_id": manufacturer_partner_id,
        "dg_flag": True,
        "un_number": "UN1266",
        "dg_class": "3",
        "packing_group": "II",
        "flash_point_c": Decimal("-13.0"),
        "alcohol_content_pct": Decimal("62.50"),
        "is_aerosol": False,
        "is_limited_quantity": True,
    }
    if kind == "SET":
        columns.update(
            dg_flag=False,
            un_number=None,
            dg_class=None,
            packing_group=None,
            flash_point_c=None,
            alcohol_content_pct=None,
            is_aerosol=False,
            is_limited_quantity=False,
        )
    columns.update(overrides)
    with unit_of_work() as uow:
        sku = Sku(**columns)
        uow.session.add(sku)
        uow.session.flush()
        return sku.id


# ── 왕복 diff — 무수정 0·변경분만·신규·오류 ────────────────────────────────


def test_unmodified_roundtrip_stages_nothing(trader: TestClient) -> None:
    """내려받아 그대로 올리면 변화 0 — 수치(음수 인화점 포함)·불리언·SET까지 (§12.2)"""
    oem = create_partner("OEM-RT", name_ko="왕복제조사", types=("OEM",))
    product_id = create_product("PRD-RT")
    _create_full_sku("SKU-RT1", product_id=product_id, manufacturer_partner_id=oem)
    _create_full_sku("SKU-RT2", product_id=product_id)
    _create_full_sku("SKU-RT3", product_id=None, kind="SET")
    export = trader.get(ROUNDTRIP_EXPORT).text
    staged = _upload(trader, _to_file(_rows(export)), key="srt0")
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["total_data_rows"] == 3
    assert body["unchanged_rows"] == 3
    assert body["new_rows"] == 0
    assert body["changed_rows"] == 0
    assert body["error_rows"] == 0


def test_editing_rows_stages_and_applies_only_those_changes(trader: TestClient) -> None:
    """5행 중 3행 수정 → 변경 3행만 스테이징·반영 (§20 F 문면의 SKU 재실증)"""
    product_id = create_product("PRD-ED")
    for index in range(5):
        _create_full_sku(f"SKU-ED{index}", product_id=product_id)
    rows = _rows(trader.get(ROUNDTRIP_EXPORT).text)
    header, data = rows[0], rows[1:]
    name_col = header.index("품명(국문)")
    weight_col = header.index("중량(g)")
    barcode_col = header.index("바코드")
    data[0][name_col] = "이름이 바뀐 세럼"
    data[1][weight_col] = "30.000"
    data[2][barcode_col] = "8809999999999"

    staged = _upload(trader, _to_file([header, *data]), key="sed1")
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["changed_rows"] == 3
    assert body["unchanged_rows"] == 2
    assert body["error_rows"] == 0

    confirmed = _confirm(trader, body["id"], key="sed1-c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {
        "id": body["id"],
        "status": "CONFIRMED",
        "created_rows": 0,
        "updated_rows": 3,
    }
    with unit_of_work() as uow:
        changed = uow.session.execute(
            select(Sku.sku_code, Sku.name_ko, Sku.unit_weight_g, Sku.barcode).where(
                Sku.sku_code.in_(("SKU-ED0", "SKU-ED1", "SKU-ED2"))
            )
        ).all()
    by_code = {code: (name, weight, barcode) for code, name, weight, barcode in changed}
    assert by_code["SKU-ED0"][0] == "이름이 바뀐 세럼"
    assert by_code["SKU-ED1"][1] == Decimal("30.000")
    assert by_code["SKU-ED2"][2] == "8809999999999"

    # 재실행 변화 0 (§20 J) — 반영 뒤 다시 내려받아 올리면 전 행 무변경이다.
    rerun = _upload(trader, _to_file(_rows(trader.get(ROUNDTRIP_EXPORT).text)), key="sed2")
    assert rerun.status_code == 201, rerun.text
    assert rerun.json()["unchanged_rows"] == 5
    assert rerun.json()["changed_rows"] == 0


def test_blank_id_creates_a_new_sku(trader: TestClient) -> None:
    """빈 ID = 신규 — 제품코드·제조사코드가 FK로 해석되어 생성된다 (§12.2)"""
    create_partner("OEM-NEW", name_ko="신규제조사", types=("OEM",))
    create_product("PRD-NEW")
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    new_row = [
        "",
        "SKU-NEW1",
        "신규 세럼",
        "New Serum",
        "SINGLE",
        "PRD-NEW",
        "ACTIVE",
        "8801111111111",
        "30.000",
        "12",
        "24",
        "OEM-NEW",
        "False",
        "",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, new_row]), key="snew1")
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["new_rows"] == 1
    assert body["error_rows"] == 0

    confirmed = _confirm(trader, body["id"], key="snew1-c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_rows"] == 1
    with unit_of_work() as uow:
        sku = uow.session.execute(select(Sku).where(Sku.sku_code == "SKU-NEW1")).scalar_one()
        assert sku.kind == "SINGLE"
        assert sku.product_id is not None
        assert sku.manufacturer_partner_id is not None
        assert sku.unit_weight_g == Decimal("30.000")
        assert sku.dg_flag is False


def test_corrupted_id_reports_an_error_row(trader: TestClient) -> None:
    """ID 훼손 = 오류 행 리포트(행번호+사유) — §20 F 문면"""
    product_id = create_product("PRD-ID")
    _create_full_sku("SKU-ID1", product_id=product_id)
    rows = _rows(trader.get(ROUNDTRIP_EXPORT).text)
    rows[1][0] = "oops"
    staged = _upload(trader, _to_file(rows), key="sid1")
    assert staged.status_code == 201, staged.text
    assert staged.json()["error_rows"] == 1
    row = _staging_rows(trader, staged.json()["id"])[0]
    assert row["row_no"] == 2
    assert "ID를 읽을 수 없습니다(oops)" in row["error_reason"]


def test_formula_prefixed_name_survives_the_roundtrip(trader: TestClient) -> None:
    """이스케이프 왕복 보존 — 수식 문자로 시작하는 품명이 원값 그대로다 (ADR-0027)"""
    create_product("PRD-ESC")
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    new_row = [
        "",
        "SKU-ESC1",
        '=HYPERLINK("x") 세럼',
        "",
        "SINGLE",
        "PRD-ESC",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "",
        "False",
        "",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, new_row]), key="sesc1")
    assert staged.status_code == 201, staged.text
    assert staged.json()["error_rows"] == 0
    confirmed = _confirm(trader, staged.json()["id"], key="sesc1-c")
    assert confirmed.status_code == 200, confirmed.text
    with unit_of_work() as uow:
        name = uow.session.execute(
            select(Sku.name_ko).where(Sku.sku_code == "SKU-ESC1")
        ).scalar_one()
    assert name == '=HYPERLINK("x") 세럼'
    # 내보내기에는 이스케이프된 형태로 실리고(`'=`), 재업로드하면 diff 0이다.
    export = trader.get(ROUNDTRIP_EXPORT).text
    assert "'=HYPERLINK" in export
    rerun = _upload(trader, _to_file(_rows(export)), key="sesc2")
    assert rerun.json()["unchanged_rows"] == 1


# ── 왕복 불변 칸 — 종류·제품코드 (판정 ①) ─────────────────────────────────


def test_kind_change_is_an_error_row(trader: TestClient) -> None:
    """종류 전환은 왕복으로 불가 — DG CHECK·처방 결합을 깨므로 오류 행이다 (판정 ①)"""
    product_id = create_product("PRD-KD")
    _create_full_sku(
        "SKU-KD1",
        product_id=product_id,
        dg_flag=False,
        un_number=None,
        dg_class=None,
        packing_group=None,
        flash_point_c=None,
        alcohol_content_pct=None,
        is_limited_quantity=False,
    )
    rows = _rows(trader.get(ROUNDTRIP_EXPORT).text)
    header = rows[0]
    kind_col = header.index("종류")
    product_col = header.index("제품코드")
    rows[1][kind_col] = "SET"
    rows[1][product_col] = ""
    staged = _upload(trader, _to_file(rows), key="skd1")
    assert staged.status_code == 201, staged.text
    assert staged.json()["error_rows"] == 1
    row = _staging_rows(trader, staged.json()["id"])[0]
    assert "왕복 편집으로 변경할 수 없습니다" in row["error_reason"]
    assert "종류" in row["error_reason"]


def test_product_reassignment_is_an_error_row(trader: TestClient) -> None:
    """제품코드 변경(소속 이전)도 보수적으로 잠근다 — 판정 침묵분, 보고 등재"""
    product_id = create_product("PRD-MV1")
    create_product("PRD-MV2")
    _create_full_sku("SKU-MV1", product_id=product_id)
    rows = _rows(trader.get(ROUNDTRIP_EXPORT).text)
    rows[1][rows[0].index("제품코드")] = "PRD-MV2"
    staged = _upload(trader, _to_file(rows), key="smv1")
    assert staged.status_code == 201, staged.text
    assert staged.json()["error_rows"] == 1
    row = _staging_rows(trader, staged.json()["id"])[0]
    assert "제품코드" in row["error_reason"]


# ── 등록 API 가드 재사용 — 같은 규칙·같은 문구 ─────────────────────────────


def test_single_without_product_is_an_error(trader: TestClient) -> None:
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    new_row = [
        "",
        "SKU-NP1",
        "처방 없는 단품",
        "",
        "SINGLE",
        "",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "",
        "False",
        "",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, new_row]), key="snp1")
    row = _staging_rows(trader, staged.json()["id"])[0]
    assert "단품 SKU에는 제품(처방)이 필요합니다" in row["error_reason"]


def test_set_with_dg_values_is_an_error(trader: TestClient) -> None:
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    new_row = [
        "",
        "SKU-SD1",
        "DG 세트",
        "",
        "SET",
        "",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "",
        "True",
        "UN1266",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, new_row]), key="ssd1")
    row = _staging_rows(trader, staged.json()["id"])[0]
    assert "세트 SKU에는 위험물 정보를 입력할 수 없습니다" in row["error_reason"]


def test_dg_classification_without_flag_is_an_error(trader: TestClient) -> None:
    """비DG 행의 UN번호 — 등록 API와 같은 문구로 오류 행이 된다 ([M1] ⑤-1)"""
    create_product("PRD-DG")
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    new_row = [
        "",
        "SKU-DG1",
        "비DG인데 UN번호",
        "",
        "SINGLE",
        "PRD-DG",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "",
        "False",
        "UN1266",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, new_row]), key="sdg1")
    row = _staging_rows(trader, staged.json()["id"])[0]
    assert "위험물이 아닌 SKU에는" in row["error_reason"]


def test_unknown_and_non_oem_manufacturer_codes_are_errors(trader: TestClient) -> None:
    """제조사코드 검증 — 미등록·비OEM 모두 오류 행 (등록 API 규칙의 코드 문맥 보강)"""
    create_partner("SUP-ONLY", name_ko="공급사만", types=("SUPPLIER",))
    create_product("PRD-MF")
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    unknown = [
        "",
        "SKU-MF1",
        "미등록 제조사",
        "",
        "SINGLE",
        "PRD-MF",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "NO-SUCH",
        "False",
        "",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    non_oem = [
        "",
        "SKU-MF2",
        "비OEM 제조사",
        "",
        "SINGLE",
        "PRD-MF",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "SUP-ONLY",
        "False",
        "",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, unknown, non_oem]), key="smf1")
    rows = _staging_rows(trader, staged.json()["id"])
    assert "등록되지 않은 거래처 코드입니다(NO-SUCH)" in rows[0]["error_reason"]
    assert "OEM 유형이 아닌 거래처입니다(SUP-ONLY)" in rows[1]["error_reason"]


# ── 확정 — 멱등·선수정 409·확정 시점 재검증 (§17.2·§17.4) ──────────────────


@pytest.mark.group_j
def test_confirm_replays_the_first_result_for_the_same_key(trader: TestClient) -> None:
    product_id = create_product("PRD-IK")
    _create_full_sku("SKU-IK1", product_id=product_id)
    rows = _rows(trader.get(ROUNDTRIP_EXPORT).text)
    rows[1][rows[0].index("품명(국문)")] = "멱등 확인 세럼"
    staged = _upload(trader, _to_file(rows), key="sik1")
    first = _confirm(trader, staged.json()["id"], key="sik1-c")
    replay = _confirm(trader, staged.json()["id"], key="sik1-c")
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()


@pytest.mark.group_j
def test_prior_edit_rejects_the_whole_confirmation(trader: TestClient) -> None:
    """스테이징 후 선수정 → 확정 전체 409 — 부분 반영 없음 (§17.2)"""
    product_id = create_product("PRD-VC")
    sku_id = _create_full_sku("SKU-VC1", product_id=product_id)
    rows = _rows(trader.get(ROUNDTRIP_EXPORT).text)
    rows[1][rows[0].index("품명(국문)")] = "충돌 세럼"
    staged = _upload(trader, _to_file(rows), key="svc1")
    with unit_of_work() as uow:
        sku = uow.session.get(Sku, sku_id)
        assert sku is not None
        sku.name_ko = "먼저 수정된 세럼"
        uow.session.flush()
    conflicted = _confirm(trader, staged.json()["id"], key="svc1-c")
    assert conflicted.status_code == 409, conflicted.text
    assert "다른 사용자가 먼저 수정함" in conflicted.json()["error"]["detail"]["rows"]


@pytest.mark.group_j
def test_oem_type_removal_between_staging_and_confirm_is_rejected(
    trader: TestClient,
) -> None:
    """스테이징~확정 사이 OEM 해제 → 409 — materials 재검증 계약의 대칭 (판정 ①)"""
    oem = create_partner("OEM-GONE", name_ko="해제될 제조사", types=("OEM",))
    create_product("PRD-VR")
    header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    new_row = [
        "",
        "SKU-VR1",
        "재검증 세럼",
        "",
        "SINGLE",
        "PRD-VR",
        "ACTIVE",
        "",
        "",
        "",
        "",
        "OEM-GONE",
        "False",
        "",
        "",
        "",
        "",
        "",
        "False",
        "False",
    ]
    staged = _upload(trader, _to_file([header, new_row]), key="svr1")
    assert staged.json()["error_rows"] == 0
    with unit_of_work() as uow:
        uow.session.execute(
            update(PartnerTypeLink)
            .where(PartnerTypeLink.partner_id == oem, PartnerTypeLink.type_code == "OEM")
            .values(deleted_at=utcnow())
        )
    conflicted = _confirm(trader, staged.json()["id"], key="svr1-c")
    assert conflicted.status_code == 409, conflicted.text
    assert "제조사가 더 이상 OEM 유형이 아님" in conflicted.json()["error"]["detail"]["rows"]


# ── 양식·권한 (K) ──────────────────────────────────────────────────────────


@pytest.mark.group_k
def test_template_header_equals_the_roundtrip_export_header(trader: TestClient) -> None:
    """표준 양식 헤더 = 왕복 내보내기 헤더 — 한 상수의 끝-끝 증명 (§12.2 보강 ②)"""
    template_header = _rows(trader.get(TEMPLATE).text)[0]
    export_header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    assert template_header == export_header
    assert template_header[0] == "ID"


@pytest.mark.group_k
def test_derived_display_columns_are_absent_from_the_roundtrip_form(
    trader: TestClient,
) -> None:
    """파생 표시 2칸은 왕복 양식에 없다(구조적 부재) — 표시는 목록 CSV 몫 (판정 ①)"""
    roundtrip_header = _rows(trader.get(ROUNDTRIP_EXPORT).text)[0]
    display_header = _rows(trader.get(DISPLAY_EXPORT).text)[0]
    assert "제품명(국문)" not in roundtrip_header
    assert "브랜드명(국문)" not in roundtrip_header
    assert "제조사코드" in roundtrip_header
    assert "제품명(국문)" in display_header
    assert "브랜드명(국문)" in display_header
    assert "ID" not in display_header


@pytest.mark.group_k
def test_viewer_cannot_stage_a_sku_import(viewer: TestClient) -> None:
    """확정·스테이징은 등록 역할이다 — 조회 역할 403 (§18.1)"""
    header_only = _to_file([["ID"]])
    response = _upload(viewer, header_only, key="svw1")
    assert response.status_code == 403, response.text
