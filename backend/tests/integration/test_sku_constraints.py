"""K. 보안·품질 — SKU의 불변식을 DB가 강제한다 (DESIGN.md §4.1·§4.2·§7.7·§17.5 / ADR-0016).

종류↔처방 짝, DG 속성의 두 방향 잠금, 물류 값의 상식 범위를 전부 여기서 본다.

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다.
  앱 코드의 검증만 검사하면, 마이그레이션·배치·직접 SQL처럼 서비스를 우회하는
  경로가 열려 있는지 알 수 없다. §17.5가 "가능한 불변식은 CHECK"라고 하는 이유가
  그것이고, 이 파일은 그 CHECK가 실제로 걸려 있는지를 본다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.modules.catalog.models import Sku
from tests.support.factories import create_product

pytestmark = pytest.mark.group_k


def _insert_sku(**columns: Any) -> None:
    with unit_of_work() as uow:
        uow.session.add(Sku(**columns))
        uow.session.flush()


def test_single_sku_without_a_formulation_is_rejected_by_the_database() -> None:
    """처방 없는 단품은 DB가 거부한다 (판정 단위가 사라진 SKU가 생기면 안 된다)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SER-001", name_ko="처방 없는 단품", kind="SINGLE", product_id=None)

    assert "kind_product_link" in str(exc.value)


def test_set_sku_with_a_formulation_is_rejected_by_the_database() -> None:
    """처방을 단 세트는 DB가 거부한다 (§4.2 — 판정은 구성품별 롤업이다)"""
    product_id = create_product()

    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SET-001", name_ko="처방 단 세트", kind="SET", product_id=product_id)

    assert "kind_product_link" in str(exc.value)


def test_unknown_kind_is_rejected_by_the_database() -> None:
    """정해지지 않은 종류 값은 DB가 거부한다 (§17.5 CHECK)

    ★ 어느 CHECK 이름으로 거부되는지는 못박지 않는다. 'BUNDLE'은 kind_valid도
      깨지만, "SINGLE 아니면 SET"을 전제로 하는 kind_product_link도 함께 깬다 —
      PostgreSQL은 둘 중 먼저 걸린 하나만 보고하고 그 순서는 우리가 정하는 게
      아니다. 이름을 못박으면 기능과 무관한 이유로 빨개진다.
    """
    product_id = create_product()

    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SER-001", name_ko="이상한 종류", kind="BUNDLE", product_id=product_id)

    message = str(exc.value)
    assert "ck_skus_kind_valid" in message or "ck_skus_kind_product_link" in message


#: 모델이 선언한 skus의 CHECK 전건. autogenerate가 **CHECK를 감지하지 못하므로**
#: 마이그레이션에 손으로 옮겨 적어야 하고, 빠뜨리면 아무 증상 없이 제약 없는
#: 테이블이 배포된다. 이 목록이 그 누락을 잡는다.
_DECLARED_SKU_CHECKS = frozenset(
    {
        "ck_skus_status_valid",
        "ck_skus_kind_valid",
        "ck_skus_kind_product_link",
        "ck_skus_set_has_no_dg",
        "ck_skus_dg_classification_only_when_dangerous",
        "ck_skus_unit_weight_g_positive",
        "ck_skus_box_qty_positive",
        "ck_skus_shelf_life_months_positive",
        "ck_skus_alcohol_content_pct_range",
    }
)


def test_every_declared_check_constraint_exists() -> None:
    """모델이 선언한 CHECK가 전부 DB에 실재한다 (마이그레이션 수기 반영 누락 검출)"""
    with unit_of_work() as uow:
        names = set(
            uow.session.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'public.skus'::regclass AND contype = 'c'
                    """
                )
            )
            .scalars()
            .all()
        )

    missing = _DECLARED_SKU_CHECKS - names
    assert not missing, (
        f"모델에는 있는데 DB에 없는 CHECK: {sorted(missing)} — "
        "autogenerate는 CHECK를 감지하지 못합니다. 마이그레이션에 손으로 추가하세요."
    )


# ── DG 속성의 두 방향 (§4.1·§7.7 / ADR-0016 정정) ──────────────────────────


def test_set_sku_with_dangerous_goods_info_is_rejected_by_the_database() -> None:
    """DG 속성을 단 세트는 DB가 거부한다 (판정은 구성품 단위 — P4까지 미결)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SET-001", name_ko="세트", kind="SET", product_id=None, dg_flag=True)

    assert "set_has_no_dg" in str(exc.value)


def test_classification_without_the_dg_flag_is_rejected_by_the_database() -> None:
    """비DG 행의 UN번호를 DB가 거부한다 (분류는 DG 판정이 있어야 의미가 있다)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_sku(
            sku_code="SER-001",
            name_ko="단품",
            kind="SINGLE",
            product_id=create_product(),
            dg_flag=False,
            un_number="1993",
        )

    assert "dg_classification_only_when_dangerous" in str(exc.value)


def test_out_of_range_weight_is_rejected_by_the_database() -> None:
    """0 이하 중량을 DB가 거부한다 (상식 범위 CHECK)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_sku(
            sku_code="SER-001",
            name_ko="단품",
            kind="SINGLE",
            product_id=create_product(),
            unit_weight_g=Decimal("0"),
        )

    assert "unit_weight_g_positive" in str(exc.value)


# ── 통과해야 하는 것들 (제약이 과하게 좁지 않다는 자기검사) ────────────────


def test_valid_pairings_are_accepted() -> None:
    """올바른 짝은 통과한다 (위 거부들이 "무엇이든 거부"가 아님을 증명한다)

    ★ 이 자기검사가 없으면, 제약이 과하게 좁아져 정상 등록까지 막는 상태에서도
      위 거부 테스트들은 그대로 초록이다.
    """
    _insert_sku(
        sku_code="SER-001", name_ko="단품", kind="SINGLE", product_id=create_product("PRD-001")
    )
    _insert_sku(sku_code="SET-001", name_ko="세트", kind="SET", product_id=None)


def test_non_dg_row_may_keep_its_evidence_values() -> None:
    """비DG 행도 인화점·알코올함량·에어로졸을 갖는다 (ADR-0016 정정의 핵심)

    이 셋은 "왜 DG가 아닌지"의 근거 입력값이다. DB가 여기까지 잠그면 비DG
    증빙을 남길 자리가 사라진다. (MSDS는 §4.7 documents로 승격 완료 —
    ADR-0020, 컬럼이 없다.)
    """
    _insert_sku(
        sku_code="TON-001",
        name_ko="알코올 함유 토너",
        kind="SINGLE",
        product_id=create_product(),
        dg_flag=False,
        flash_point_c=Decimal("65.0"),
        alcohol_content_pct=Decimal("4.50"),
        is_aerosol=True,
    )


def test_set_has_no_dg_check_no_longer_references_msds() -> None:
    """재정의된 set_has_no_dg CHECK가 실재하고 msds_url을 참조하지 않는다 (함정 ①)

    이름만 같은 구 정의가 남아 있으면(재정의 누락) 컬럼이 없는 식이 되어
    있거나, 반대로 CHECK 자체가 사라졌을 수 있다 — 정의문을 직접 읽어 둘 다
    잡는다(웹 세션 승인 문면: 수기 재정의 + 제약 실재 테스트).
    """
    with unit_of_work() as uow:
        definition = uow.session.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'public.skus'::regclass
                  AND conname = 'ck_skus_set_has_no_dg'
                """
            )
        ).scalar_one()
    assert "msds_url" not in definition
    assert "is_limited_quantity" in definition  # 나머지 잠금은 그대로다


def test_the_msds_url_column_is_gone() -> None:
    """skus.msds_url 컬럼이 없다 (documents 승격 — ADR-0020, 잔존 컬럼 검출)

    승격 뒤 문자열 컬럼이 남아 있으면 "이미 값이 있으니까"의 영구 잔류 경로가
    되살아난다 — 부재 자체를 고정한다. set_has_no_dg CHECK의 수기 재정의
    (함정 ①)가 실재하는지는 위 _DECLARED_SKU_CHECKS 대조가 함께 지킨다.
    """
    with unit_of_work() as uow:
        columns = set(
            uow.session.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'skus'
                    """
                )
            )
            .scalars()
            .all()
        )
    assert "msds_url" not in columns


def test_dangerous_goods_row_without_classification_is_accepted() -> None:
    """위험물인데 분류값이 비어 있어도 DB는 통과시킨다 (완결성은 §7.7 P4 게이트 소관)"""
    _insert_sku(
        sku_code="SPR-001",
        name_ko="분류 미상 위험물",
        kind="SINGLE",
        product_id=create_product(),
        dg_flag=True,
    )
