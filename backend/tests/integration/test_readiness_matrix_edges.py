"""C. 인증 — 준비도 매트릭스 경계 (S2-3 PR-3 적대적 리뷰 반영: 정확성·문면·테스트 실질 3렌즈).

test_readiness_matrix.py가 필수 집합·색·롤업의 **기본 동작**을 고정한다면, 이 파일은 그
기본 동작이 **어디서 조용히 틀릴 수 있는가**를 고정한다:
  ① 쪽 밖 구성품 — 세트가 구성품과 다른 쪽(또는 kind=SET 필터)에 있어도 롤업이 온전하다.
  ② 삭제 필터 — 삭제된 SKU·시장·구성품·제품이 행·열·롤업·요건 축에 남지 않는다.
  ③ 종결 인스턴스 — 반려·중단이 "활성"으로 잡히면 색은 같아도(🔴) 상세(상태·인스턴스
    링크·안내)가 거짓이 된다.
  ④ 정렬·총계 — SKU 코드순, 셀 안 항목은 축 순서(SKU→제품→기업→시설)·템플릿 id순.
  ⑤ 세트의 제품·시설 요건 — 세트 자신의 품목군에 걸린 것은 구성품의 제품·제조사에게 물린다
    (어느 셀에도 집계되지 않는 조용한 누락 금지).
  ⑥ 초안으로 되돌린 템플릿 — 활성 인스턴스가 있는 대상에게는 계속 센다(편집 창의 🟢 과신 금지).
  ⑦ 유효 시작일 전 승인 — 🟢이 아니라 🟡.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy import event, select

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.catalog.models import Product, SetComponent, Sku
from app.modules.markets.models import Market
from app.modules.readiness import service
from app.modules.readiness.rules import DRAFT_NOTE, GRAY, GREEN, RED, YELLOW
from app.modules.readiness.service import MatrixRowView, MatrixView
from app.modules.requirements.models import RequirementTemplate
from tests.support.factories import (
    create_certification_instance,
    create_item_profile,
    create_market,
    create_partner,
    create_requirement_template,
    create_set_sku,
    create_sku,
    create_sku_with_axes,
)

pytestmark = pytest.mark.group_c

TODAY = date(2026, 9, 29)
FAR = date(2027, 12, 31)

_template = create_requirement_template
_sku = create_sku_with_axes
_set_sku = create_set_sku
_instance = create_certification_instance


def _matrix(**kwargs: object) -> MatrixView:
    params: dict[str, object] = {"offset": 0, "limit": 50, "base_date": TODAY}
    params.update(kwargs)
    return service.get_matrix(**params)  # type: ignore[arg-type]


def _row(view: MatrixView, sku_id: int) -> MatrixRowView:
    return next(row for row in view.rows if row.sku_id == sku_id)


def _color(view: MatrixView, sku_id: int, market: str = "US") -> str:
    return next(
        cell.summary.color for cell in _row(view, sku_id).cells if cell.market_code == market
    )


def _soft_delete(model: type, row_id: int) -> None:
    with unit_of_work() as uow:
        row = uow.session.get(model, row_id)
        assert row is not None
        row.deleted_at = utcnow()  # type: ignore[attr-defined]


def _set_template_status(template_id: int, status: str) -> None:
    with unit_of_work() as uow:
        row = uow.session.get(RequirementTemplate, template_id)
        assert row is not None
        row.status = status


# ── ① 쪽 밖 구성품 ───────────────────────────────────────────────────────────


def test_a_set_alone_on_its_page_still_sees_components_off_the_page() -> None:
    """구성품을 쪽 밖에서 읽는 경로 — kind=SET 필터·쪽 분할에서 세트가 🟢/⚪으로 새지 않는다"""
    profile = create_item_profile("PRF-OFFPAGE")
    template = _template(profile_id=profile)
    good, _ = _sku("CMP-A", profile_id=profile)
    bad, _ = _sku("CMP-B", profile_id=profile)  # 인스턴스 없음 → 🔴
    _set_sku("SET-1", [good, bad])
    _instance(template, "SKU", good, status="APPROVED", expires_on=FAR)

    # 코드순: CMP-A < CMP-B < SET-1 — 세트만 있는 쪽 두 가지
    for view in (_matrix(kind="SET"), _matrix(offset=2, limit=1)):
        assert [row.sku_code for row in view.rows] == ["SET-1"]
        row = view.rows[0]
        cell = row.cells[0]
        assert cell.summary.color == RED  # 구성품 B의 미충족이 세트로 올라온다
        assert {item.via_component_sku_code for item in cell.items} == {"CMP-A", "CMP-B"}
        assert [component.sku_code for component in row.components] == ["CMP-A", "CMP-B"]


# ── ② 삭제 필터 ─────────────────────────────────────────────────────────────


def test_deleted_skus_are_neither_rows_nor_counted() -> None:
    create_market("US")
    _sku("ALIVE")
    gone, _ = _sku("GONE")
    _soft_delete(Sku, gone)
    view = _matrix()
    assert [row.sku_code for row in view.rows] == ["ALIVE"]
    assert view.total == 1


def test_deleted_markets_are_not_columns() -> None:
    create_market("US")
    canada = create_market("CA")
    sku_id, _ = _sku("COLS")
    _soft_delete(Market, canada)
    view = _matrix()
    assert [market.code for market in view.markets] == ["US"]
    assert len(_row(view, sku_id).cells) == 1


def test_a_removed_component_link_leaves_the_rollup() -> None:
    profile = create_item_profile("PRF-RM-LINK")
    template = _template(profile_id=profile)
    keep, _ = _sku("RM-KEEP", profile_id=profile)
    drop, _ = _sku("RM-DROP", profile_id=profile)
    set_id = _set_sku("RM-SET", [keep, drop])
    _instance(template, "SKU", keep, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), set_id) == RED  # drop 인스턴스 없음
    with unit_of_work() as uow:
        link = uow.session.execute(
            select(SetComponent).where(
                SetComponent.set_sku_id == set_id, SetComponent.component_sku_id == drop
            )
        ).scalar_one()
        link.deleted_at = utcnow()
    view = _matrix()
    assert [component.sku_code for component in _row(view, set_id).components] == ["RM-KEEP"]
    assert _color(view, set_id) == GREEN


def test_a_deleted_component_sku_leaves_the_rollup() -> None:
    profile = create_item_profile("PRF-RM-SKU")
    template = _template(profile_id=profile)
    keep, _ = _sku("DS-KEEP", profile_id=profile)
    drop, _ = _sku("DS-DROP", profile_id=profile)
    set_id = _set_sku("DS-SET", [keep, drop])
    _instance(template, "SKU", keep, status="APPROVED", expires_on=FAR)
    _soft_delete(Sku, drop)
    view = _matrix()
    assert [component.sku_code for component in _row(view, set_id).components] == ["DS-KEEP"]
    assert _color(view, set_id) == GREEN
    assert drop not in {row.sku_id for row in view.rows}


def test_a_deleted_product_contributes_no_product_axis() -> None:
    profile = create_item_profile("PRF-PRD-DEL")
    _template("US", "제품 안전성 평가", "PRODUCT")
    sku_id, product_id = _sku("PD-DEL", product_profile_id=profile)
    _template("US", "제품 안전성 평가 2", "PRODUCT", profile_id=profile)
    assert _color(_matrix(), sku_id) == RED  # 제품 인스턴스 없음
    _soft_delete(Product, product_id)
    assert _color(_matrix(), sku_id) == GRAY  # 삭제된 제품은 요건 축에서 빠진다


def test_discontinued_skus_are_rows_with_their_status() -> None:
    """단종 SKU도 행으로 나오되 status를 싣는다(계획 §0-5 ①) — 화면이 표식을 붙일 근거"""
    profile = create_item_profile("PRF-OLD")
    _template(profile_id=profile)
    sku_id, _ = _sku("OLD-1", profile_id=profile, status="DISCONTINUED")
    view = _matrix()
    row = _row(view, sku_id)
    assert row.status == "DISCONTINUED"
    assert _color(view, sku_id) == RED  # 요건 평가는 판매중과 똑같이 한다


# ── ③ 종결 인스턴스 ─────────────────────────────────────────────────────────


def test_a_rejected_only_target_reports_no_active_instance_in_the_detail() -> None:
    """색은 🔴로 같지만 상세는 달라야 한다 — 반려 인스턴스를 활성으로 잡으면 상태·링크가 거짓이 된다"""
    profile = create_item_profile("PRF-TERM")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("TERM", profile_id=profile)
    _instance(template, "SKU", sku_id, status="REJECTED")
    item = _row(_matrix(), sku_id).cells[0].items[0]
    assert item.color == RED
    assert (item.status, item.certification_id) == (None, None)
    assert "활성 인증 인스턴스가 없습니다" in (item.note or "")


def test_an_active_instance_wins_over_a_newer_terminal_one() -> None:
    profile = create_item_profile("PRF-MIX")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("MIX", profile_id=profile)
    active = _instance(template, "SKU", sku_id, status="PREPARING")
    _instance(template, "SKU", sku_id, status="REJECTED")  # id가 더 크다
    view = _matrix()
    item = _row(view, sku_id).cells[0].items[0]
    assert (item.certification_id, item.status) == (active, "PREPARING")
    assert _color(view, sku_id) == YELLOW


# ── ④ 정렬·총계 ─────────────────────────────────────────────────────────────


def test_rows_are_ordered_by_sku_code_not_by_creation() -> None:
    create_market("US")
    for code in ("ZZ-1", "MM-1", "AA-1"):  # id 순서는 코드 역순
        _sku(code)
    assert [row.sku_code for row in _matrix().rows] == ["AA-1", "MM-1", "ZZ-1"]


def test_cell_items_are_ordered_by_axis_then_template() -> None:
    profile = create_item_profile("PRF-ORDER")
    _template("US", "시설", "FACILITY", profile_id=profile)
    _template("US", "기업", "COMPANY", profile_id=profile)
    _template("US", "제품", "PRODUCT", profile_id=profile)
    sku_first = _template("US", "SKU 하나", "SKU", profile_id=profile)
    sku_second = _template("US", "SKU 둘", "SKU", profile_id=profile)
    maker = create_partner("PTN-ORDER", types=("OEM",))
    sku_id, _ = _sku("ORDER", profile_id=profile, product_profile_id=profile, manufacturer_id=maker)
    items = _row(_matrix(), sku_id).cells[0].items
    assert [(item.axis, item.template_name) for item in items] == [
        ("SKU", "SKU 하나"),
        ("SKU", "SKU 둘"),
        ("PRODUCT", "제품"),
        ("COMPANY", "기업"),
        ("FACILITY", "시설"),
    ]
    assert sku_first < sku_second  # 같은 축 안에서는 템플릿 id순


def test_totals_follow_the_filters() -> None:
    profile = create_item_profile("PRF-TOTAL")
    a, _ = _sku("TOT-A", profile_id=profile, name_ko="수분 세럼")
    b, _ = _sku("TOT-B", profile_id=profile, name_ko="수분 크림")
    _sku("TOT-C", name_ko="진정 토너")
    _set_sku("TOT-SET", [a, b])
    assert _matrix(limit=1).total == 4 and len(_matrix(limit=1).rows) == 1
    assert _matrix(kind="SET", limit=1).total == 1
    assert _matrix(kind="SINGLE", limit=1).total == 3
    assert _matrix(q="수분", limit=1).total == 2
    assert _matrix(item_profile_id=profile, limit=1).total == 2


def test_a_page_beyond_the_end_is_empty_but_keeps_the_total() -> None:
    create_market("US")
    for index in range(3):
        _sku(f"END-{index}")
    view = _matrix(offset=500)
    assert view.rows == [] and view.total == 3
    assert [market.code for market in view.markets] == ["US"]  # 열은 그대로


# ── ⑤ 세트의 제품·시설 요건은 구성품에게 ─────────────────────────────────────


def test_set_facility_requirement_is_delegated_to_component_manufacturers() -> None:
    """세트 품목군에만 걸린 시설 요건도 집계된다 — 구성품의 제조사가 대상(같은 제조사는 1건)"""
    set_profile = create_item_profile("PRF-SETFAC")
    facility = _template("US", "시설 등록", "FACILITY", profile_id=set_profile)
    maker = create_partner("PTN-SETFAC", types=("OEM",))
    comp_a, _ = _sku("SF-A", manufacturer_id=maker)
    comp_b, _ = _sku("SF-B", manufacturer_id=maker)  # 같은 제조사
    comp_c, _ = _sku("SF-C", manufacturer_id=None)  # 미지정
    set_id = _set_sku("SF-SET", [comp_a, comp_b, comp_c], profile_id=set_profile)
    _instance(facility, "FACILITY", maker, status="APPROVED", expires_on=FAR)

    cell = _row(_matrix(), set_id).cells[0]
    items = [item for item in cell.items if item.axis == "FACILITY"]
    assert len(items) == 2  # 제조사 1건(A·B 합침) + 미지정 1건(C)
    assert all(item.via_component_sku_code is not None for item in items)  # 세트 자신의 항목 없음
    known = next(item for item in items if not item.target_missing)
    assert (known.color, known.target_id, known.via_component_sku_code) == (GREEN, maker, "SF-A")
    missing = next(item for item in items if item.target_missing)
    assert (missing.color, missing.via_component_sku_code) == (RED, "SF-C")
    assert cell.summary.color == RED


def test_a_set_with_its_own_manufacturer_counts_its_own_facility_once() -> None:
    set_profile = create_item_profile("PRF-SETOWN")
    facility = _template("US", "시설 등록", "FACILITY", profile_id=set_profile)
    maker = create_partner("PTN-SETOWN", types=("OEM",))
    comp, _ = _sku("SO-A", manufacturer_id=maker)
    set_id = _set_sku("SO-SET", [comp], profile_id=set_profile, manufacturer_id=maker)
    _instance(facility, "FACILITY", maker, status="APPROVED", expires_on=FAR)
    cell = _row(_matrix(), set_id).cells[0]
    assert cell.summary.required == 1  # 자기 제조사 몫과 구성품 몫이 같은 (템플릿, 대상)이라 1건
    assert cell.items[0].via_component_sku_code is None  # 자기 몫이 먼저다
    assert cell.summary.color == GREEN


def test_set_product_requirement_is_delegated_to_component_products() -> None:
    set_profile = create_item_profile("PRF-SETPRD")
    product_template = _template("US", "제품 안전성 평가", "PRODUCT", profile_id=set_profile)
    comp_a, product_one = _sku("SP-A")
    comp_b = create_sku("SP-B", product_id=product_one)  # 같은 제품
    comp_c, product_two = _sku("SP-C")  # 다른 제품
    set_id = _set_sku("SP-SET", [comp_a, comp_b, comp_c], profile_id=set_profile)
    _instance(product_template, "PRODUCT", product_one, status="APPROVED", expires_on=FAR)

    cell = _row(_matrix(), set_id).cells[0]
    items = [item for item in cell.items if item.axis == "PRODUCT"]
    assert len(items) == 2  # 같은 제품은 1건
    by_target = {item.target_id: item for item in items}
    assert by_target[product_one].color == GREEN
    assert by_target[product_one].via_component_sku_code == "SP-A"
    assert by_target[product_two].color == RED  # 제품 인스턴스 없음
    assert cell.summary.color == RED


def test_set_rollup_is_kept_apart_per_market() -> None:
    profile = create_item_profile("PRF-SET-MKT")
    us = _template("US", "US 리스팅", profile_id=profile)
    _template("CA", "CA 통보", profile_id=profile)
    comp, _ = _sku("PM-A", profile_id=profile)
    set_id = _set_sku("PM-SET", [comp])
    _instance(us, "SKU", comp, status="APPROVED", expires_on=FAR)
    view = _matrix()
    assert _color(view, set_id, "US") == GREEN
    assert _color(view, set_id, "CA") == RED
    us_cell = next(cell for cell in _row(view, set_id).cells if cell.market_code == "US")
    assert [item.template_name for item in us_cell.items] == [
        "US 리스팅"
    ]  # CA 요건이 섞이지 않는다
    assert us_cell.items[0].via_component_sku_id == comp  # 출처는 구성품 id다


# ── ⑥ 초안으로 되돌린 템플릿 ────────────────────────────────────────────────


def test_a_reverted_template_still_counts_where_an_instance_exists() -> None:
    """편집하려고 내린 확정 요건이 편집 창에 조용히 사라져 셀이 🟢이 되면 안 된다"""
    profile = create_item_profile("PRF-DRAFT")
    template = _template("US", "MoCRA 제품 리스팅", profile_id=profile)
    holder, _ = _sku("DRAFT-1", profile_id=profile)
    _instance(template, "SKU", holder, status="NOT_STARTED")
    assert _color(_matrix(), holder) == RED

    _set_template_status(template, "DRAFT")  # 편집을 위해 초안 전환
    cell = _row(_matrix(), holder).cells[0]
    assert cell.summary.required == 1 and cell.summary.color == RED  # 계속 센다
    assert cell.items[0].note == DRAFT_NOTE

    _set_template_status(template, "CONFIRMED")  # 재확정
    assert _row(_matrix(), holder).cells[0].items[0].note is None


def test_a_reverted_template_is_not_counted_for_targets_without_an_instance() -> None:
    profile = create_item_profile("PRF-DRAFT2")
    template = _template(profile_id=profile)
    holder, _ = _sku("DR-HOLD", profile_id=profile)
    newcomer, _ = _sku("DR-NEW", profile_id=profile)
    _instance(template, "SKU", holder, status="APPROVED", expires_on=FAR)
    _set_template_status(template, "DRAFT")
    view = _matrix()
    assert _color(view, holder) == GREEN  # 이미 걸린 인스턴스가 있으니 계속 센다
    assert (
        _color(view, newcomer) == GRAY
    )  # 걸린 적 없는 대상에게는 세지 않는다(자동 적용도 초안은 건너뜀)


def test_a_retired_template_stops_counting_even_with_instances() -> None:
    profile = create_item_profile("PRF-RETIRED")
    template = _template(profile_id=profile)
    holder, _ = _sku("RT-1", profile_id=profile)
    _instance(template, "SKU", holder, status="NOT_STARTED")
    _set_template_status(template, "RETIRED")
    assert _color(_matrix(), holder) == GRAY  # 폐기 = 더 이상 요구되지 않는 요건


# ── ⑦ 유효 시작일 전 승인 ───────────────────────────────────────────────────


def test_an_approval_not_yet_in_force_is_yellow_with_a_reason() -> None:
    profile = create_item_profile("PRF-START")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("START", profile_id=profile)
    _instance(
        template, "SKU", sku_id, status="APPROVED", expires_on=FAR, valid_from=date(2026, 10, 15)
    )
    view = _matrix()
    assert _color(view, sku_id) == YELLOW
    item = _row(view, sku_id).cells[0].items[0]
    assert item.status == "APPROVED" and "유효 시작일(2026-10-15)" in (item.note or "")
    assert (
        _matrix(base_date=date(2026, 10, 15)).rows[0].cells[0].summary.color == GREEN
    )  # 시작일 당일


# ── 질의 수: 세트·전 축 부하에서도 행 수와 무관 ─────────────────────────────


def test_query_count_stays_flat_with_sets_delegation_and_every_axis() -> None:
    profile = create_item_profile("PRF-PERF2")
    for applies_to in ("SKU", "PRODUCT", "COMPANY", "FACILITY"):
        _template("US", f"{applies_to} 요건", applies_to, profile_id=profile)
    maker = create_partner("PTN-PERF2", types=("OEM",))

    def grow(start: int, stop: int) -> None:
        for index in range(start, stop):
            a, _ = _sku(
                f"Q{index:02d}-A",
                profile_id=profile,
                product_profile_id=profile,
                manufacturer_id=maker,
            )
            b, _ = _sku(f"Q{index:02d}-B", profile_id=profile, product_profile_id=profile)
            _set_sku(f"Q{index:02d}-S", [a, b], profile_id=profile)

    grow(0, 3)
    few = _statements(lambda: _matrix())
    assert few > 0  # 리스너가 실제로 붙어 있다(0 == 0 공허 통과 방지)
    grow(3, 12)
    many = _statements(lambda: _matrix())
    assert len(_matrix().rows) == 36
    assert few == many, f"세트 3건 {few}회 vs 12건 {many}회 — 행당 질의가 늘었다"


def _statements(work: Callable[[], object]) -> int:
    seen: list[str] = []

    def listener(*args: object) -> None:
        seen.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return len(seen)
