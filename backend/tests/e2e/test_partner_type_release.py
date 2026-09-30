"""A·J. 거래처 유형 해제가 이미 성립한 마스터 의존을 깨면 임포트 확정이 거부된다 (S3-1 F11).

PROGRESS "거래처 유형 해제가 참조 마스터를 확인하지 않음"(S1-3 PR-3 리뷰 검출)의 종결.
SUPPLIER 해제 → 기본공급사로 쓰는 활성 자재, OEM 해제 → 제조사로 쓰는 활성 SKU가 있으면
배치 전체가 409(부분 반영 없음). 전표는 여기서 막지 않는다(확정 시점 유형 재검증이 담당).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import engine
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_material,
    create_partner,
    create_sku,
    create_user,
)

pytestmark = pytest.mark.group_j

EXPORT = "/api/v1/partners/export.csv"
STAGE = "/api/v1/imports/partners/staging"
STAGING = "/api/v1/imports/staging"


@pytest.fixture
def admin() -> Iterator[TestClient]:
    create_user("rel-admin@example.com", roles=(RoleCode.ADMIN,))
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login",
            json={"email": "rel-admin@example.com", "password": DEFAULT_PASSWORD},
        ).is_success
        yield client


def _stage_type_change(client: TestClient, code: str, new_types: str, key: str) -> int:
    rows = list(csv.reader(io.StringIO(client.get(EXPORT).content.decode("utf-8-sig"))))
    header = rows[0]
    for row in rows[1:]:
        if row[header.index("거래처코드")] == code:
            row[header.index("유형")] = new_types
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    staged = client.post(
        STAGE,
        files={"file": ("p.csv", buffer.getvalue().encode("utf-8-sig"), "text/csv")},
        headers={"Idempotency-Key": key},
    )
    assert staged.status_code == 201, staged.text
    return int(staged.json()["id"])


def _confirm(client: TestClient, staging_id: int, key: str):  # type: ignore[no-untyped-def]
    return client.post(f"{STAGING}/{staging_id}/confirm", headers={"Idempotency-Key": key})


def _active_types(code: str) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT l.type_code FROM partner_type_links l JOIN partners p ON p.id=l.partner_id"
                    " WHERE p.partner_code=:c AND l.deleted_at IS NULL"
                ),
                {"c": code},
            )
        }


def test_releasing_supplier_or_oem_with_live_references_is_rejected(admin: TestClient) -> None:
    """참조가 있으면 전체 409·문구에 유형과 건수·DB 불변, 참조를 지우면 같은 파일이 통과한다"""
    partner_id = create_partner("PTN-REL", name_ko="공급·제조", types=("SUPPLIER", "OEM", "BUYER"))
    material_id = create_material("MAT-REL")
    sku_id = create_sku("SKU-REL")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE materials SET default_supplier_partner_id=:p WHERE id=:m"),
            {"p": partner_id, "m": material_id},
        )
        conn.execute(
            text("UPDATE skus SET manufacturer_partner_id=:p WHERE id=:s"),
            {"p": partner_id, "s": sku_id},
        )

    staging_id = _stage_type_change(admin, "PTN-REL", "BUYER", "rel1")
    blocked = _confirm(admin, staging_id, "rel1-c")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "IMPORTS.CONFIRM.VERSION_CONFLICT"
    reason = blocked.json()["error"]["detail"]["rows"]
    assert "SUPPLIER" in reason and "OEM" in reason and "1건" in reason
    assert _active_types("PTN-REL") == {"SUPPLIER", "OEM", "BUYER"}  # 부분 반영 0

    # 참조를 소프트 삭제하면(자재·SKU 정리) 같은 스테이징이 통과한다.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE materials SET deleted_at = now() WHERE id=:m"), {"m": material_id}
        )
        conn.execute(text("UPDATE skus SET deleted_at = now() WHERE id=:s"), {"s": sku_id})
    # 스테이징 뒤에 버전이 바뀌지 않았으므로 재확정이 가능하다.
    ok = _confirm(admin, staging_id, "rel1-c2")
    assert ok.status_code == 200, ok.text
    assert _active_types("PTN-REL") == {"BUYER"}


def test_releasing_a_type_nobody_references_is_allowed(admin: TestClient) -> None:
    """참조가 없으면 해제는 자유다(전표·미완성 등록을 막지 않는다 — §7.7 사상)"""
    create_partner("PTN-FREE", name_ko="자유", types=("SUPPLIER", "BUYER"))
    staging_id = _stage_type_change(admin, "PTN-FREE", "BUYER", "rel2")
    assert _confirm(admin, staging_id, "rel2-c").status_code == 200
    assert _active_types("PTN-FREE") == {"BUYER"}


def test_keeping_the_referenced_type_and_renaming_is_allowed(admin: TestClient) -> None:
    """참조 중인 유형을 **유지**하면(이름만 변경) 참조가 있어도 통과한다 — released 계산은 (현재 − 페이로드)"""
    partner_id = create_partner("PTN-KEEP2", name_ko="유지", types=("SUPPLIER", "BUYER"))
    material_id = create_material("MAT-KEEP2")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE materials SET default_supplier_partner_id=:p WHERE id=:m"),
            {"p": partner_id, "m": material_id},
        )
    staging_id = _stage_type_change(admin, "PTN-KEEP2", "SUPPLIER|BUYER", "rel3")
    assert _confirm(admin, staging_id, "rel3-c").status_code == 200


def test_only_the_released_type_is_checked(admin: TestClient) -> None:
    """SUPPLIER만 해제하는데 참조가 OEM(제조사) 쪽뿐이면 통과한다(유형별 참조는 독립이다)"""
    partner_id = create_partner("PTN-ONLY", name_ko="OEM 참조", types=("SUPPLIER", "OEM"))
    sku_id = create_sku("SKU-ONLY")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE skus SET manufacturer_partner_id=:p WHERE id=:s"), {"p": partner_id, "s": sku_id}
        )
    staging_id = _stage_type_change(admin, "PTN-ONLY", "OEM", "rel4")
    assert _confirm(admin, staging_id, "rel4-c").status_code == 200
    assert _active_types("PTN-ONLY") == {"OEM"}


def test_discontinued_sku_counts_but_deleted_material_does_not(admin: TestClient) -> None:
    """단종 SKU는 참조로 세고(삭제만 제외), 삭제된 자재는 세지 않는다 — 각각 단독으로"""
    partner_id = create_partner("PTN-DIS", name_ko="단종 참조", types=("SUPPLIER", "OEM", "BUYER"))
    sku_id = create_sku("SKU-DIS", status="DISCONTINUED")
    material_id = create_material("MAT-DIS")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE skus SET manufacturer_partner_id=:p WHERE id=:s"), {"p": partner_id, "s": sku_id}
        )
        conn.execute(
            text(
                "UPDATE materials SET default_supplier_partner_id=:p, deleted_at=now() WHERE id=:m"
            ),
            {"p": partner_id, "m": material_id},
        )
    # SUPPLIER 해제 — 참조 자재는 삭제됐으므로 통과
    staging_id = _stage_type_change(admin, "PTN-DIS", "OEM|BUYER", "rel5")
    assert _confirm(admin, staging_id, "rel5-c").status_code == 200
    # OEM 해제 — 단종(삭제 아님) SKU가 참조하므로 409
    staging_id = _stage_type_change(admin, "PTN-DIS", "BUYER", "rel6")
    blocked = _confirm(admin, staging_id, "rel6-c")
    assert blocked.status_code == 409, blocked.text
    assert "OEM" in blocked.json()["error"]["detail"]["rows"]
