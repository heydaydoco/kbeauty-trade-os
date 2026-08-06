"""K. 임포트 레지스트리 계약 — 어댑터·모델·내보내기가 한 몸으로 움직이는지 (§12.2).

왕복의 전제는 "양식 = 내보내기 = 파서가 아는 컬럼"이 한 상수라는 것이다.
셋 중 하나가 따로 움직이면 diff가 그 자리에서 오염되므로, 사람이 기억하는
대신 여기서 기계로 대조한다.
"""

from __future__ import annotations

import pytest

from app.modules.imports.models import IMPORT_REGISTRY_CODES
from app.modules.imports.registry import IMPORT_TARGETS

pytestmark = pytest.mark.group_k


def test_registry_codes_match_the_db_check() -> None:
    """어댑터 목록과 DB CHECK 열거가 1:1이다 — 한쪽만 늘면 저장이 막히거나 유령 코드가 생긴다"""
    assert set(IMPORT_TARGETS) == set(IMPORT_REGISTRY_CODES)


def test_every_target_leads_with_the_id_column() -> None:
    """왕복 CSV의 1열은 ID다 — diff의 키(빈 ID=신규 / ID 있는 행=변경분만)"""
    for target in IMPORT_TARGETS.values():
        assert target.header[0] == "ID"
        assert "ID" not in {column.header for column in target.columns}


def test_columns_map_headers_and_fields_one_to_one() -> None:
    """헤더↔정규형 필드는 1:1이다 — 겹치면 changes 표기가 어느 칸인지 모호해진다"""
    for target in IMPORT_TARGETS.values():
        headers = [column.header for column in target.columns]
        fields = [column.field for column in target.columns]
        assert len(set(headers)) == len(headers), target.code
        assert len(set(fields)) == len(fields), target.code
        assert target.code_field in fields, target.code


def test_string_columns_are_a_subset_of_the_header() -> None:
    """역변환 대상은 실재 컬럼이어야 한다 — 오타는 역변환 누락으로 조용히 샌다"""
    for target in IMPORT_TARGETS.values():
        assert target.string_columns <= set(target.header), target.code
        assert "ID" not in target.string_columns, target.code


def test_export_routers_reuse_the_registry_header() -> None:
    """내보내기 라우터의 헤더가 레지스트리 상수 그 자체다(복사본이 아니다)"""
    from app.modules.catalog import router as catalog_router
    from app.modules.imports.registry import (
        MATERIALS_CSV_HEADER,
        PARTNERS_CSV_HEADER,
        SKUS_ROUNDTRIP_CSV_HEADER,
    )
    from app.modules.materials.router import MATERIAL_CSV_HEADER
    from app.modules.partners.router import PARTNER_CSV_HEADER

    assert PARTNER_CSV_HEADER is PARTNERS_CSV_HEADER
    assert MATERIAL_CSV_HEADER is MATERIALS_CSV_HEADER
    assert catalog_router.SKUS_ROUNDTRIP_CSV_HEADER is SKUS_ROUNDTRIP_CSV_HEADER
    assert IMPORT_TARGETS["partners"].header == PARTNERS_CSV_HEADER
    assert IMPORT_TARGETS["materials"].header == MATERIALS_CSV_HEADER
    assert IMPORT_TARGETS["skus"].header == SKUS_ROUNDTRIP_CSV_HEADER


def test_sku_roundtrip_form_carries_no_derived_display_columns() -> None:
    """SKU 왕복 양식 = 기록 가능 필드+ID만 (S1.5 판정 ① — 구조적 부재의 고정)

    파생 표시 2칸(제품명·브랜드명)과 제조사 **표시명**은 왕복 결정성이 없어
    양식에 없다 — 표시 수요는 목록 CSV(SKU_CSV_HEADER)가 맡는다(ADR-0030 부기).
    """
    from app.modules.catalog.router import SKU_CSV_HEADER

    roundtrip = IMPORT_TARGETS["skus"].header
    assert "제품명(국문)" not in roundtrip
    assert "브랜드명(국문)" not in roundtrip
    assert "제조사" not in roundtrip  # 표시명 칸 — 코드 칸(제조사코드)만 있다
    assert "제조사코드" in roundtrip
    assert "제품명(국문)" in SKU_CSV_HEADER
    assert "브랜드명(국문)" in SKU_CSV_HEADER


def test_no_auto_confirm_code_path_exists() -> None:
    """자동 확정 경로가 없다 — 확정은 사람 1클릭뿐 (§15 L2, ADR-09 예외 비대상)

    ADR-09의 조건부 자동 확정은 "인테이크 전표까지"이고 마스터는 범위 밖이다.
    함수 이름이든 호출이든 auto_confirm이 모듈에 등장하면 그 자체가 위반이다.
    """
    from pathlib import Path

    module_root = Path(__file__).resolve().parents[2] / "app" / "modules" / "imports"
    offenders = [
        path.name
        for path in module_root.glob("*.py")
        if "auto_confirm" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"임포트 모듈에 자동 확정 흔적이 있다: {offenders}"
