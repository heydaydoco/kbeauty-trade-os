"""F. 엑셀 왕복 임포트 — 목록 다운로드 → 수정 → 업로드 → diff → 확정 (§12.2 / §20 F).

F 그룹 최초 도입 파일이다(P1=F·K의 F는 전부 S1-3 몫 — §20 S1-2 매핑 보강).
이 파일이 고정하는 것:
  - "왕복 diff 3행만 반영"(§20 F 문면·WBS S1-3 DoD) — 수정한 행만 스테이징된다
  - "ID 훼손 업로드 → 오류"(§20 F 문면) — 행번호+사유 리포트(§12.2)
  - 수식 이스케이프 왕복 보존(ADR-0027 — 부채 #17 종결의 증명)
  - "동일 임포트 재실행 변화 0"(§20 J) / 확정 멱등 재생(§17.4)
  - 스테이징 후 선수정 → 확정 전체 409(§17.2 — 부분 반영 없음)
  - 확정=사람 1클릭 + 역할 게이트(§15 L2 — 자동 확정 경로 없음)
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.partners.models import Partner
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_material,
    create_partner,
    create_user,
)

pytestmark = pytest.mark.group_f

LOGIN = "/api/v1/auth/login"
PARTNERS_EXPORT = "/api/v1/partners/export.csv"
MATERIALS_EXPORT = "/api/v1/materials/export.csv"
STAGE_PARTNERS = "/api/v1/imports/partners/staging"
STAGE_MATERIALS = "/api/v1/imports/materials/staging"
STAGING = "/api/v1/imports/staging"


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
    """내보내기 본문 → 행 목록(BOM 제거). 첫 행이 헤더다."""
    return list(csv.reader(io.StringIO(csv_text.removeprefix("﻿"), newline="")))


def _to_file(rows: list[list[str]]) -> bytes:
    """행 목록 → 업로드 파일 바이트 — 내보내기와 같은 UTF-8 BOM + CRLF."""
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _upload(
    client: TestClient, url: str, content: bytes, *, key: str, filename: str = "목록.csv"
) -> Response:
    return client.post(
        url,
        files={"file": (filename, content, "text/csv")},
        headers={"Idempotency-Key": key},
    )


def _staging_rows(client: TestClient, staging_id: int) -> list[dict[str, Any]]:
    response = client.get(f"{STAGING}/{staging_id}/rows", params={"size": 200})
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _confirm(client: TestClient, staging_id: int, *, key: str) -> Response:
    return client.post(f"{STAGING}/{staging_id}/confirm", headers={"Idempotency-Key": key})


# ── 왕복 diff — §20 F "왕복 diff 3행만 반영" ────────────────────────────────


def test_unmodified_roundtrip_stages_nothing(trader: TestClient) -> None:
    """내려받아 그대로 올리면 변화 0 — 왕복 전단사의 끝-끝 증명 (§12.2)"""
    for index in range(3):
        create_partner(f"PTN-R{index}", name_ko=f"왕복거래처{index}", types=("SUPPLIER",))
    export = trader.get(PARTNERS_EXPORT).text
    staged = _upload(trader, STAGE_PARTNERS, _to_file(_rows(export)), key="rt0")
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["total_data_rows"] == 3
    assert body["unchanged_rows"] == 3
    assert body["new_rows"] == 0
    assert body["changed_rows"] == 0
    assert body["error_rows"] == 0


def test_editing_three_rows_stages_exactly_three_changes(trader: TestClient) -> None:
    """5행 중 3행 수정 → 변경 3행만 스테이징·반영 (§20 F·WBS S1-3 DoD 문면)"""
    for index in range(5):
        create_partner(f"PTN-D{index}", name_ko=f"디프거래처{index}", types=("SUPPLIER",))
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    header, data = rows[0], rows[1:]
    name_col = header.index("거래처명")
    credit_col = header.index("여신한도")
    currency_col = header.index("여신통화")
    type_col = header.index("유형")
    data[0][name_col] = "이름이 바뀐 거래처"
    data[1][credit_col] = "10000.50"
    data[1][currency_col] = "USD"
    data[2][type_col] = "FORWARDER|SUPPLIER"

    staged = _upload(trader, STAGE_PARTNERS, _to_file([header, *data]), key="d1")
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["changed_rows"] == 3
    assert body["unchanged_rows"] == 2
    assert body["new_rows"] == 0
    assert body["error_rows"] == 0

    staged_rows = _staging_rows(trader, body["id"])
    assert [row["kind"] for row in staged_rows] == ["CHANGED"] * 3
    # 변경 내용은 CSV 헤더 이름으로, [이전, 이후] 표기로 보인다(검토 화면의 재료).
    first = next(row for row in staged_rows if row["row_no"] == 2)
    assert first["changes"] == {"거래처명": ["디프거래처0", "이름이 바뀐 거래처"]}

    confirmed = _confirm(trader, body["id"], key="d1c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {
        "id": body["id"],
        "status": "CONFIRMED",
        "created_rows": 0,
        "updated_rows": 3,
    }

    partners = {
        row["partner_code"]: row
        for row in trader.get("/api/v1/partners", params={"size": 200}).json()["items"]
    }
    assert partners["PTN-D0"]["name_ko"] == "이름이 바뀐 거래처"
    assert partners["PTN-D1"]["credit_limit_amount"] == 1_000_050
    assert partners["PTN-D1"]["credit_limit_currency"] == "USD"
    assert partners["PTN-D2"]["type_codes"] == ["FORWARDER", "SUPPLIER"]
    # 안 고친 행은 그대로다.
    assert partners["PTN-D3"]["name_ko"] == "디프거래처3"


def test_blank_id_creates_and_absent_row_is_ignored(trader: TestClient) -> None:
    """빈 ID = 신규, 파일에 없는 행 = 무시(삭제 아님) — §12.2 diff 규칙 문면"""
    create_partner("PTN-KEEP", name_ko="파일에서 뺄 거래처", types=("SUPPLIER",))
    create_partner("PTN-STAY", name_ko="남길 거래처", types=("SUPPLIER",))
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    header = rows[0]
    kept = [row for row in rows[1:] if row[1] == "PTN-STAY"]  # PTN-KEEP 행을 파일에서 뺀다
    new_row = ["", "PTN-NEW", "새 거래처", "BUYER|SUPPLIER", "1000.50", "USD", "True", "", ""]

    staged = _upload(trader, STAGE_PARTNERS, _to_file([header, *kept, new_row]), key="n1")
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["new_rows"] == 1
    assert body["changed_rows"] == 0
    assert body["error_rows"] == 0

    confirmed = _confirm(trader, body["id"], key="n1c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_rows"] == 1

    partners = {
        row["partner_code"]: row
        for row in trader.get("/api/v1/partners", params={"size": 200}).json()["items"]
    }
    # 파일에 없던 행은 삭제되지 않았다.
    assert "PTN-KEEP" in partners
    created = partners["PTN-NEW"]
    assert created["type_codes"] == ["BUYER", "SUPPLIER"]
    assert created["credit_limit_amount"] == 100_050
    assert created["dg_capable"] is True


# ── 오류 행 리포트 — §20 F "ID 훼손 업로드 → 오류" ─────────────────────────


def test_corrupted_ids_become_error_rows_with_reasons(trader: TestClient) -> None:
    """ID 훼손 = 행번호+사유의 오류 행 — 유효한 행은 그대로 스테이징된다 (§12.2)"""
    partner_id = create_partner("PTN-OK", name_ko="정상 거래처", types=("SUPPLIER",))
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    header = rows[0]
    garbled = ["abc", "PTN-G", "훼손된 행", "SUPPLIER", "", "", "", "", ""]
    missing = ["999999", "PTN-M", "없는 ID", "SUPPLIER", "", "", "", "", ""]
    dup_a = [str(partner_id), "PTN-A", "중복 ID a", "SUPPLIER", "", "", "", "", ""]
    dup_b = [str(partner_id), "PTN-B", "중복 ID b", "SUPPLIER", "", "", "", "", ""]
    fresh = ["", "PTN-FRESH", "유효한 신규", "SUPPLIER", "", "", "", "", ""]

    staged = _upload(
        trader, STAGE_PARTNERS, _to_file([header, garbled, missing, dup_a, dup_b, fresh]), key="e1"
    )
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["error_rows"] == 4
    assert body["new_rows"] == 1

    by_row = {row["row_no"]: row for row in _staging_rows(trader, body["id"])}
    assert "ID를 읽을 수 없습니다" in by_row[2]["error_reason"]
    assert "존재하지 않거나 삭제된 ID" in by_row[3]["error_reason"]
    assert "여러 번" in by_row[4]["error_reason"]
    assert "여러 번" in by_row[5]["error_reason"]
    assert by_row[6]["kind"] == "NEW"

    # 오류 행이 있어도 유효한 행은 사람 판단으로 확정할 수 있다 — 오류는
    # 리포트로 왕복해 고쳐 다시 올린다(§12.2 "오류 행 리포트 왕복").
    confirmed = _confirm(trader, body["id"], key="e1c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {
        "id": body["id"],
        "status": "CONFIRMED",
        "created_rows": 1,
        "updated_rows": 0,
    }


def test_value_errors_are_reported_per_row(trader: TestClient) -> None:
    """값 검증 실패도 행번호+사유다 — 등록 API와 같은 규칙·같은 문구(§12.2)"""
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    header = rows[0]
    no_type = ["", "PTN-V1", "유형 없음", "", "", "", "", "", ""]
    bad_pair = ["", "PTN-V2", "통화 없음", "SUPPLIER", "1000", "", "", "", ""]
    bad_bool = ["", "PTN-V3", "불리언 오류", "SUPPLIER", "", "", "maybe", "", ""]
    short = ["", "PTN-V4", "열 부족"]

    staged = _upload(
        trader, STAGE_PARTNERS, _to_file([header, no_type, bad_pair, bad_bool, short]), key="v1"
    )
    assert staged.status_code == 201, staged.text
    assert staged.json()["error_rows"] == 4

    by_row = {row["row_no"]: row for row in _staging_rows(trader, staged.json()["id"])}
    assert "유형" in by_row[2]["error_reason"]
    assert "함께" in by_row[3]["error_reason"]  # 여신한도·통화 쌍 — 등록 API와 같은 문구
    assert "True 또는 False" in by_row[4]["error_reason"]
    assert "열 개수" in by_row[5]["error_reason"]


# ── 수식 이스케이프 왕복 — ADR-0027 (부채 #17 종결 증명) ───────────────────


def test_formula_escape_roundtrip_preserves_values(trader: TestClient) -> None:
    """이스케이프된 셀이 역변환으로 원값 복원 → diff 0 — 전단사의 실증 (ADR-0027)"""
    body = {
        "partner_code": "PTN-ESC",
        "name_ko": '=HYPERLINK("http://evil")',
        "type_codes": ["SUPPLIER"],
        "strengths": "'홑따옴표로 시작",
        "weaknesses": "-리드 하이픈",
    }
    created = trader.post("/api/v1/partners", json=body, headers={"Idempotency-Key": "esc"})
    assert created.status_code == 201, created.text

    export = trader.get(PARTNERS_EXPORT).text
    # 내보내기는 이스케이프한다 — 파일에는 `'` 접두가 실재한다(§12.2 보강).
    assert "'=HYPERLINK" in export
    assert "''홑따옴표로 시작" in export
    assert "'-리드 하이픈" in export

    staged = _upload(trader, STAGE_PARTNERS, _to_file(_rows(export)), key="esc1")
    assert staged.status_code == 201, staged.text
    body_staged = staged.json()
    # 역변환이 정확하면 전 행이 무변경이다 — 이스케이프가 diff를 오염시키지 않는다.
    assert body_staged["changed_rows"] == 0
    assert body_staged["error_rows"] == 0
    assert body_staged["unchanged_rows"] == body_staged["total_data_rows"]


# ── 멱등·재실행 — §20 J ────────────────────────────────────────────────────


@pytest.mark.group_j
def test_rerunning_the_same_import_changes_nothing(trader: TestClient) -> None:
    """동일 임포트 재실행 = 변화 0 (§20 J 문면) — 확정 후 같은 파일은 diff 0"""
    create_partner("PTN-J", name_ko="멱등 거래처", types=("SUPPLIER",))
    export = trader.get(PARTNERS_EXPORT).text
    content = _to_file(_rows(export))

    first = _upload(trader, STAGE_PARTNERS, content, key="j1")
    assert first.status_code == 201, first.text
    assert _confirm(trader, first.json()["id"], key="j1c").status_code == 200

    second = _upload(trader, STAGE_PARTNERS, content, key="j2")
    assert second.status_code == 201, second.text
    assert second.json()["changed_rows"] == 0
    assert second.json()["new_rows"] == 0
    confirmed = _confirm(trader, second.json()["id"], key="j2c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_rows"] == 0
    assert confirmed.json()["updated_rows"] == 0


@pytest.mark.group_j
def test_same_pending_file_is_rejected(trader: TestClient) -> None:
    """같은 파일(해시 일치)은 검토 대기 중 한 번만 — ADR-09 파일 해시 멱등"""
    create_partner("PTN-P", name_ko="대기 중복", types=("SUPPLIER",))
    content = _to_file(_rows(trader.get(PARTNERS_EXPORT).text))
    assert _upload(trader, STAGE_PARTNERS, content, key="p1").status_code == 201
    duplicate = _upload(trader, STAGE_PARTNERS, content, key="p2")
    assert duplicate.status_code == 422, duplicate.text
    assert duplicate.json()["error"]["code"] == "IMPORTS.STAGING.DUPLICATE_PENDING"


@pytest.mark.group_j
def test_confirm_replays_with_the_same_key(trader: TestClient) -> None:
    """확정 더블클릭 → 반영 1회, 같은 키 재수신은 최초 결과 반환 (§17.4)"""
    partner_id = create_partner("PTN-C", name_ko="확정 멱등", types=("SUPPLIER",))
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    target = next(row for row in rows[1:] if row[0] == str(partner_id))
    target[2] = "확정 후 이름"
    staged = _upload(trader, STAGE_PARTNERS, _to_file(rows), key="c1")
    staging_id = staged.json()["id"]

    first = _confirm(trader, staging_id, key="cfk")
    assert first.status_code == 200, first.text
    replay = _confirm(trader, staging_id, key="cfk")
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    # 다른 키의 재확정은 409다 — 이미 PENDING이 아니다.
    again = _confirm(trader, staging_id, key="cfk2")
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "IMPORTS.STAGING.NOT_PENDING"


# ── 확정 충돌 — §17.2 version 409 ──────────────────────────────────────────


def test_confirm_after_someone_edited_is_rejected_atomically(trader: TestClient) -> None:
    """스테이징 후 대상이 먼저 수정되면 확정은 전체 409 — 부분 반영 없음 (§17.2)"""
    partner_id = create_partner("PTN-VC", name_ko="원래 이름", types=("SUPPLIER",))
    create_partner("PTN-VC2", name_ko="같이 고친 행", types=("SUPPLIER",))
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    for row in rows[1:]:
        row[2] = row[2] + " (파일 수정)"
    staged = _upload(trader, STAGE_PARTNERS, _to_file(rows), key="vc1")
    assert staged.json()["changed_rows"] == 2

    # 스테이징과 확정 사이에 다른 사용자가 먼저 고쳤다(version 증가).
    with unit_of_work() as uow:
        partner = uow.session.execute(
            select(Partner).where(Partner.id == partner_id)
        ).scalar_one()
        partner.name_ko = "다른 사용자가 먼저 수정"

    conflicted = _confirm(trader, staged.json()["id"], key="vc1c")
    assert conflicted.status_code == 409, conflicted.text
    assert conflicted.json()["error"]["code"] == "IMPORTS.CONFIRM.VERSION_CONFLICT"
    assert "2행" in conflicted.json()["error"]["detail"]["rows"]

    partners = {
        row["partner_code"]: row
        for row in trader.get("/api/v1/partners", params={"size": 200}).json()["items"]
    }
    # 충돌하지 않은 행까지 **아무것도** 반영되지 않았다 — 원자성.
    assert partners["PTN-VC"]["name_ko"] == "다른 사용자가 먼저 수정"
    assert partners["PTN-VC2"]["name_ko"] == "같이 고친 행"
    # 스테이징은 PENDING으로 남아 다시 시도하거나 파기할 수 있다.
    status = trader.get(f"{STAGING}/{staged.json()['id']}").json()["status"]
    assert status == "PENDING"


# ── 자재 왕복 — 기본공급사는 코드로 오간다 ─────────────────────────────────


def test_materials_roundtrip_with_supplier_codes(trader: TestClient) -> None:
    """자재 왕복 — 공급사코드 해석·SUPPLIER 검증·로트⊃재고 규칙이 행 단위로 선다"""
    create_partner("SUP-1", name_ko="공급사", types=("SUPPLIER",))
    create_partner("BUY-1", name_ko="바이어만", types=("BUYER",))
    create_material("MAT-R1", name_ko="정제수")
    create_material("MAT-R2", name_ko="글리세린")

    rows = _rows(trader.get(MATERIALS_EXPORT).text)
    header = rows[0]
    assert header == [
        "ID",
        "자재코드",
        "자재명(국문)",
        "유형",
        "HS6",
        "기본공급사코드",
        "재고관리",
        "로트관리",
    ]
    data = rows[1:]
    data[0][4] = "330499"  # HS6 입력
    data[0][5] = "SUP-1"  # 공급사 연결
    data[1][5] = "BUY-1"  # 공급사 유형이 아니다 → 오류 행
    lot_only = ["", "MAT-R3", "로트만 켬", "RAW_MATERIAL", "", "", "False", "True"]
    valid_new = ["", "MAT-R4", "새 자재", "CONTAINER", "", "SUP-1", "TRUE", "FALSE"]

    staged = _upload(
        trader, STAGE_MATERIALS, _to_file([header, *data, lot_only, valid_new]), key="m1"
    )
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["changed_rows"] == 1
    assert body["error_rows"] == 2
    assert body["new_rows"] == 1

    by_row = {row["row_no"]: row for row in _staging_rows(trader, body["id"])}
    assert "공급사 유형이 아닌" in by_row[3]["error_reason"]
    assert "로트관리는 재고관리를 전제" in by_row[4]["error_reason"]

    confirmed = _confirm(trader, body["id"], key="m1c")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created_rows"] == 1
    assert confirmed.json()["updated_rows"] == 1

    materials = {
        row["material_code"]: row
        for row in trader.get("/api/v1/materials", params={"size": 200}).json()["items"]
    }
    assert materials["MAT-R1"]["hs6"] == "330499"
    assert materials["MAT-R1"]["default_supplier_name"] == "공급사"
    assert materials["MAT-R2"]["default_supplier_partner_id"] is None  # 오류 행은 미반영
    assert materials["MAT-R4"]["material_type"] == "CONTAINER"
    # 대문자 TRUE/FALSE(엑셀 표기)도 읽힌다.
    assert materials["MAT-R4"]["inventory_managed"] is True
    assert materials["MAT-R4"]["lot_managed"] is False


# ── 파일 검증 — 양식·확장자·빈 파일 ────────────────────────────────────────


def test_wrong_header_is_rejected_before_parsing(trader: TestClient) -> None:
    """헤더가 표준 양식과 다르면 통째로 422 — 열 어긋난 diff를 만들지 않는다"""
    content = "ID,거래처코드,거래처명\r\n,PTN-X,이름\r\n".encode("utf-8-sig")
    response = _upload(trader, STAGE_PARTNERS, content, key="h1")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "IMPORTS.FILE.HEADER_MISMATCH"
    assert "기대한 컬럼" in response.json()["error"]["detail"]["header"]


def test_non_csv_extension_is_rejected(trader: TestClient) -> None:
    response = _upload(trader, STAGE_PARTNERS, b"whatever", key="x1", filename="목록.xlsx")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "IMPORTS.FILE.TYPE_NOT_ALLOWED"


def test_header_only_file_is_rejected(trader: TestClient) -> None:
    """데이터 행 0 = 오류 — "올릴 것이 없었다"를 성공으로 두지 않는다(빈 목록 규율)"""
    header_only = _to_file(
        [["ID", "거래처코드", "거래처명", "유형", "여신한도", "여신통화", "DG취급", "강점", "약점"]]
    )
    response = _upload(trader, STAGE_PARTNERS, header_only, key="empty1")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "IMPORTS.FILE.EMPTY"


def test_cp949_saved_file_still_roundtrips(trader: TestClient) -> None:
    """한국어 엑셀이 CP949로 저장해도 한 번은 받아 준다 — 깨진 글자 diff 방지 폴백"""
    create_partner("PTN-CP", name_ko="씨피구사구", types=("SUPPLIER",))
    rows = _rows(trader.get(PARTNERS_EXPORT).text)
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    content = buffer.getvalue().encode("cp949")  # 엑셀 "CSV(쉼표로 분리)" 저장 경로
    staged = _upload(trader, STAGE_PARTNERS, content, key="cp1")
    assert staged.status_code == 201, staged.text
    assert staged.json()["unchanged_rows"] == staged.json()["total_data_rows"]


# ── 스테이징 관리 — 파기·권한 (K) ──────────────────────────────────────────


@pytest.mark.group_k
def test_pending_staging_can_be_discarded_but_confirmed_cannot(trader: TestClient) -> None:
    """검토가 검토이려면 "아니다"가 가능해야 한다 — 파기는 PENDING만, soft delete"""
    create_partner("PTN-DEL", name_ko="파기 대상", types=("SUPPLIER",))
    content = _to_file(_rows(trader.get(PARTNERS_EXPORT).text))
    staged = _upload(trader, STAGE_PARTNERS, content, key="del1")
    staging_id = staged.json()["id"]

    assert trader.delete(f"{STAGING}/{staging_id}").status_code == 204
    assert trader.get(f"{STAGING}/{staging_id}").status_code == 404
    # 파기했으니 같은 파일을 다시 올릴 수 있다(부분 유니크가 PENDING·미삭제 한정).
    restaged = _upload(trader, STAGE_PARTNERS, content, key="del2")
    assert restaged.status_code == 201, restaged.text

    confirmed_id = restaged.json()["id"]
    assert _confirm(trader, confirmed_id, key="del2c").status_code == 200
    blocked = trader.delete(f"{STAGING}/{confirmed_id}")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "IMPORTS.STAGING.NOT_PENDING"


@pytest.mark.group_k
def test_viewer_cannot_stage_or_confirm(trader: TestClient, viewer: TestClient) -> None:
    """임포트는 마스터를 만드는 경로다 — 등록과 같은 역할 게이트 (§18.1)"""
    create_partner("PTN-VW", name_ko="권한 검증", types=("SUPPLIER",))
    content = _to_file(_rows(trader.get(PARTNERS_EXPORT).text))
    assert _upload(viewer, STAGE_PARTNERS, content, key="vw1").status_code == 403

    staged = _upload(trader, STAGE_PARTNERS, content, key="vw2")
    staging_id = staged.json()["id"]
    assert _confirm(viewer, staging_id, key="vw3").status_code == 403
    assert viewer.delete(f"{STAGING}/{staging_id}").status_code == 403
    # 읽기는 인증만으로 된다 — 검토 내용 공유(여신한도는 마스킹 비대상).
    assert viewer.get(STAGING).status_code == 200
    assert viewer.get(f"{STAGING}/{staging_id}/rows").status_code == 200


@pytest.mark.group_k
def test_import_endpoints_require_login() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/imports/registry").status_code == 401
        assert anonymous.get("/api/v1/imports/partners/template.csv").status_code == 401
        assert anonymous.get(STAGING).status_code == 401


@pytest.mark.group_k
def test_template_header_equals_export_header(trader: TestClient) -> None:
    """표준 양식 헤더 = 내보내기 헤더 — 한 상수의 두 얼굴임을 끝-끝으로 재확인"""
    for registry, export_url in (
        ("partners", PARTNERS_EXPORT),
        ("materials", MATERIALS_EXPORT),
    ):
        template = trader.get(f"/api/v1/imports/{registry}/template.csv")
        assert template.status_code == 200, template.text
        export = trader.get(export_url)
        template_header = template.text.removeprefix("﻿").splitlines()[0]
        export_header = export.text.removeprefix("﻿").splitlines()[0]
        assert template_header == export_header


def test_unknown_registry_is_not_found(trader: TestClient) -> None:
    assert trader.get("/api/v1/imports/mystery/template.csv").status_code == 404
    assert _upload(trader, "/api/v1/imports/mystery/staging", b"x", key="u1").status_code == 404
