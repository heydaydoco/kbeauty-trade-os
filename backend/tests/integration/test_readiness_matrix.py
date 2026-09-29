"""C. 인증 — 준비도 매트릭스 서비스 (§5.3 / S2-3 판정 요청 1·2 / 안건 ①·⑦ / 조건 A·C).

★ 이 파일이 고정하는 것 — **필수 집합 도출**(가장 틀리기 쉬운 곳):
  ① SKU·제품 축은 **자동 적용과 같은 축**이다(요청 2 (나) — ADR-0042). SKU 요건은
    그 SKU 자신의 품목군에서, 제품 요건은 소속 제품 자신의 품목군에서만 나온다.
    축이 어긋나면 영원히 🔴이거나 영원히 ⚪인 칸이 생긴다.
  ② 기업·시설 축은 두 품목군 세트의 합집합이고, **제조사 미지정 = 🔴(fail-closed)**.
  ③ 성분 단위는 모집합 밖이다(조건 A). 확정(CONFIRMED) 템플릿만 요건이다.
  ④ 활성 인스턴스만 본다 — 반려·중단만 남은 대상은 "활성 인스턴스 부재"(🔴).
  ⑤ 세트 = 자기 요건 + 구성품 롤업(§4.2).
색 표·집계 규칙 자체는 tests/unit/test_readiness_rules.py가 순수 함수로 전수 고정한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy import event, func, select

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.modules.audit.models import AuditLog
from app.modules.catalog.models import Product
from app.modules.certifications.models import Certification
from app.modules.outbox.models import Event
from app.modules.readiness import service
from app.modules.readiness.rules import GRAY, GREEN, RED, YELLOW
from app.modules.readiness.service import MatrixRowView, MatrixView
from app.modules.worklist.models import Alert
from tests.support.factories import (
    create_certification_instance,
    create_item_profile,
    create_market,
    create_partner,
    create_product,
    create_requirement_template,
    create_set_sku,
    create_sku,
    create_sku_with_axes,
    link_profile_template,
)

pytestmark = pytest.mark.group_c

TODAY = date(2026, 9, 29)
FAR = date(2027, 12, 31)


# ── 픽스처 헬퍼 — 생성 지점은 tests/support/factories.py 하나다 ─────────────────

_template = create_requirement_template
_link = link_profile_template
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


# ── 기본 모양 ────────────────────────────────────────────────────────────────


def test_a_sku_without_a_profile_is_out_of_scope_everywhere() -> None:
    """품목군이 없으면 필수 요건 0건 — 전 시장 ⚪(대상외). 열은 등록된 시장 전부다"""
    create_market("US")
    create_market("CA")
    sku_id, _ = _sku("NO-PROFILE")
    view = _matrix()
    assert [market.code for market in view.markets] == ["CA", "US"]
    row = _row(view, sku_id)
    assert [cell.summary.color for cell in row.cells] == [GRAY, GRAY]
    assert all(cell.items == [] for cell in row.cells)
    assert len(row.cells) == len(view.markets)  # 셀은 열과 같은 순서·같은 수


def test_a_profile_with_no_confirmed_requirement_is_gray() -> None:
    create_market("US")
    profile = create_item_profile("PRF-EMPTY")
    sku_id, _ = _sku("EMPTY", profile_id=profile)
    assert _color(_matrix(), sku_id) == GRAY


# ── ① SKU·제품 축 = 자동 적용과 같은 축 (ADR-0042) ───────────────────────────


@pytest.mark.parametrize(
    ("stored", "expires_on", "expected"),
    [
        (None, None, RED),  # 활성 인스턴스 부재
        ("NOT_STARTED", None, RED),  # 미착수 = 🔴
        ("PREPARING", None, YELLOW),
        ("SUBMITTED", None, YELLOW),
        ("IN_REVIEW", None, YELLOW),
        ("SUPPLEMENTING", None, YELLOW),
        ("APPROVED", FAR, GREEN),
        ("APPROVED", None, GREEN),  # 무기한
        ("EXPIRING", date(2026, 12, 1), YELLOW),
        ("RENEWING", FAR, YELLOW),
        ("RENEWING", date(2026, 9, 1), RED),  # 갱신중 도과
        ("EXPIRED", date(2026, 1, 1), RED),
        ("REJECTED", None, RED),  # 종결 — 활성 아님
        ("SUSPENDED", None, RED),
    ],
)
def test_sku_axis_colors_follow_the_instance_state(
    stored: str | None, expires_on: date | None, expected: str
) -> None:
    """SKU 요건 1건 — 인스턴스 상태(실효)가 곧 셀 색 (상태 11값 + 부재 + 도과)"""
    profile = create_item_profile("PRF-SKU")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("SKU-A", profile_id=profile)
    if stored is not None:
        _instance(template, "SKU", sku_id, status=stored, expires_on=expires_on)
    view = _matrix()
    assert _color(view, sku_id) == expected
    cell = _row(view, sku_id).cells[0]
    assert cell.summary.required == 1


def test_sku_requirement_comes_only_from_the_skus_own_profile() -> None:
    """SKU 요건은 SKU 자신의 품목군에서만 — 제품의 품목군에 SKU 템플릿이 있어도 세지 않는다"""
    product_profile = create_item_profile("PRF-PRODUCT-ONLY")
    _template(profile_id=product_profile, applies_to="SKU")
    sku_id, _ = _sku("SKU-NO-OWN", profile_id=None, product_profile_id=product_profile)
    assert _color(_matrix(), sku_id) == GRAY


def test_product_requirement_comes_only_from_the_products_own_profile() -> None:
    """제품 요건은 소속 제품 자신의 품목군에서만 — SKU의 품목군에 PRODUCT 템플릿이 있어도 세지 않는다"""
    sku_profile = create_item_profile("PRF-SKU-ONLY")
    _template(profile_id=sku_profile, applies_to="PRODUCT")
    sku_id, _ = _sku("PRD-NO-OWN", profile_id=sku_profile, product_profile_id=None)
    assert _color(_matrix(), sku_id) == GRAY


def test_product_axis_is_read_from_the_product_instance() -> None:
    profile = create_item_profile("PRF-PRD")
    template = _template("US", "제품 안전성 평가", "PRODUCT")
    _link(profile, template)
    sku_id, product_id = _sku("PRD-A", product_profile_id=profile)
    assert _color(_matrix(), sku_id) == RED  # 제품 인스턴스 없음
    _instance(template, "PRODUCT", product_id, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), sku_id) == GREEN


def test_two_skus_of_one_product_share_the_product_instance() -> None:
    """같은 제품의 두 SKU는 제품 인스턴스 하나를 함께 읽는다"""
    profile = create_item_profile("PRF-SHARED")
    template = _template("US", "제품 안전성 평가", "PRODUCT")
    _link(profile, template)
    product_id = create_product("P-SHARED")
    sku_a = create_sku("SHARED-A", product_id=product_id)
    sku_b = create_sku("SHARED-B", product_id=product_id)
    with unit_of_work() as uow:
        product = uow.session.get(Product, product_id)
        assert product is not None
        product.item_profile_id = profile
    _instance(template, "PRODUCT", product_id, status="IN_REVIEW")
    view = _matrix()
    assert _color(view, sku_a) == _color(view, sku_b) == YELLOW


# ── ② 기업·시설 축 ───────────────────────────────────────────────────────────


def test_company_requirement_is_read_from_the_company_instance() -> None:
    """기업 요건 — 자사 인스턴스(target 없음)와 대조, 모든 SKU가 같은 인스턴스를 본다"""
    profile = create_item_profile("PRF-CO")
    template = _template("US", "US Agent 지정", "COMPANY", profile_id=profile)
    sku_a, _ = _sku("CO-A", profile_id=profile)
    sku_b, _ = _sku("CO-B", profile_id=profile)
    assert _color(_matrix(), sku_a) == RED  # 자사 인스턴스 없음
    _instance(template, "COMPANY", None, status="APPROVED", expires_on=FAR)
    view = _matrix()
    assert _color(view, sku_a) == _color(view, sku_b) == GREEN


def test_company_requirement_is_the_union_of_both_profiles_counted_once() -> None:
    """기업 요건은 SKU·제품 품목군의 합집합 — 같은 템플릿이 양쪽에 있어도 1건"""
    sku_profile = create_item_profile("PRF-U-SKU")
    product_profile = create_item_profile("PRF-U-PRD")
    shared = _template("US", "US Agent 지정", "COMPANY")
    _link(sku_profile, shared)
    _link(product_profile, shared)
    only_product = _template("US", "기업 등록", "COMPANY", profile_id=product_profile)
    sku_id, _ = _sku("UNION", profile_id=sku_profile, product_profile_id=product_profile)
    _instance(shared, "COMPANY", None, status="APPROVED", expires_on=FAR)
    view = _matrix()
    cell = _row(view, sku_id).cells[0]
    assert cell.summary.required == 2  # shared 1 + only_product 1 (중복 없음)
    assert cell.summary.color == RED  # only_product 인스턴스 없음
    _instance(only_product, "COMPANY", None, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), sku_id) == GREEN


def test_facility_requirement_with_no_manufacturer_is_red() -> None:
    """제조사 미지정 SKU의 시설 요건 = 🔴 fail-closed(확인할 대상이 없다 ≠ 해당 없음)"""
    profile = create_item_profile("PRF-FAC")
    template = _template("US", "시설 등록", "FACILITY", profile_id=profile)
    sku_id, _ = _sku("FAC-NONE", profile_id=profile, manufacturer_id=None)
    view = _matrix()
    assert _color(view, sku_id) == RED
    item = _row(view, sku_id).cells[0].items[0]
    assert item.target_missing and item.status is None and "제조사" in (item.note or "")
    # 다른 파트너 앞으로 승인 인스턴스가 있어도 이 SKU의 근거가 아니다
    other = create_partner("PTN-OTHER", types=("OEM",))
    _instance(template, "FACILITY", other, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), sku_id) == RED


def test_facility_requirement_reads_the_manufacturers_instance() -> None:
    profile = create_item_profile("PRF-FAC2")
    template = _template("US", "시설 등록", "FACILITY", profile_id=profile)
    maker = create_partner("PTN-MAKER", types=("OEM",))
    other = create_partner("PTN-NOT-MAKER", types=("OEM",))
    sku_id, _ = _sku("FAC-OK", profile_id=profile, manufacturer_id=maker)
    _instance(template, "FACILITY", other, status="APPROVED", expires_on=FAR)  # 남의 시설
    assert _color(_matrix(), sku_id) == RED
    _instance(template, "FACILITY", maker, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), sku_id) == GREEN


# ── ③ 모집합 밖·비확정 ───────────────────────────────────────────────────────


def test_ingredient_requirements_are_outside_the_population() -> None:
    """성분 단위 요건은 집계에 넣지 않는다(조건 A) — 이것만 있는 SKU는 ⚪"""
    profile = create_item_profile("PRF-ING")
    _template("US", "성분 안전성", "INGREDIENT", profile_id=profile)
    sku_id, _ = _sku("ING", profile_id=profile)
    assert _color(_matrix(), sku_id) == GRAY


@pytest.mark.parametrize("status", ["DRAFT", "RETIRED"])
def test_only_confirmed_templates_are_requirements(status: str) -> None:
    """초안은 검증되지 않은 정의, 폐기는 더 이상 요구되지 않는 요건 — 둘 다 세지 않는다"""
    profile = create_item_profile("PRF-STATUS")
    _template(status=status, profile_id=profile)
    sku_id, _ = _sku("STATUS", profile_id=profile)
    assert _color(_matrix(), sku_id) == GRAY


def test_a_removed_profile_link_is_not_a_requirement() -> None:
    profile = create_item_profile("PRF-DEL")
    template = _template()
    _link(profile, template, deleted=True)
    sku_id, _ = _sku("DEL", profile_id=profile)
    assert _color(_matrix(), sku_id) == GRAY


def test_markets_are_independent_columns() -> None:
    """시장별 요건·인증은 서로 독립 — 한 시장의 승인이 다른 시장을 채우지 않는다"""
    profile = create_item_profile("PRF-MKT")
    us = _template("US", "US 리스팅", profile_id=profile)
    _template("CA", "CA 통보", profile_id=profile)
    sku_id, _ = _sku("MKT", profile_id=profile)
    _instance(us, "SKU", sku_id, status="APPROVED", expires_on=FAR)
    view = _matrix()
    assert _color(view, sku_id, "US") == GREEN
    assert _color(view, sku_id, "CA") == RED


def test_a_market_with_no_requirement_for_the_sku_is_gray() -> None:
    profile = create_item_profile("PRF-GRAYMKT")
    _template("US", profile_id=profile)
    create_market("JP")
    sku_id, _ = _sku("GRAYMKT", profile_id=profile)
    view = _matrix()
    assert _color(view, sku_id, "JP") == GRAY
    assert _color(view, sku_id, "US") == RED


def test_multiple_requirements_in_one_cell_take_the_worst() -> None:
    profile = create_item_profile("PRF-MULTI")
    one = _template("US", "요건 하나", profile_id=profile)
    two = _template("US", "요건 둘", profile_id=profile)
    sku_id, _ = _sku("MULTI", profile_id=profile)
    _instance(one, "SKU", sku_id, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), sku_id) == RED  # 둘째 인스턴스 없음
    _instance(two, "SKU", sku_id, status="PREPARING")
    view = _matrix()
    cell = _row(view, sku_id).cells[0]
    assert cell.summary.color == YELLOW
    assert (cell.summary.required, cell.summary.approved, cell.summary.in_progress) == (2, 1, 1)


# ── ④ 활성 인스턴스 ──────────────────────────────────────────────────────────


def test_a_terminal_instance_is_not_active_but_a_new_attempt_is() -> None:
    """반려된 인스턴스만 있으면 부재(🔴), 재도전 신규 인스턴스가 생기면 그것을 본다"""
    profile = create_item_profile("PRF-RETRY")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("RETRY", profile_id=profile)
    _instance(template, "SKU", sku_id, status="REJECTED")
    assert _color(_matrix(), sku_id) == RED
    _instance(template, "SKU", sku_id, status="PREPARING")
    assert _color(_matrix(), sku_id) == YELLOW


def test_a_soft_deleted_instance_is_not_active() -> None:
    from app.core.time import utcnow

    profile = create_item_profile("PRF-SOFT")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("SOFT", profile_id=profile)
    instance_id = _instance(template, "SKU", sku_id, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), sku_id) == GREEN
    with unit_of_work() as uow:
        row = uow.session.get(Certification, instance_id)
        assert row is not None
        row.deleted_at = utcnow()
    assert _color(_matrix(), sku_id) == RED


def test_stored_status_is_not_trusted_over_the_calendar() -> None:
    """저장 승인이어도 만료일이 어제면 🔴 — 스윕(06:00) 전 창에도 🟢을 보이지 않는다"""
    profile = create_item_profile("PRF-STALE")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("STALE", profile_id=profile)
    instance_id = _instance(
        template, "SKU", sku_id, status="APPROVED", expires_on=date(2026, 9, 28)
    )
    view = _matrix()
    assert _color(view, sku_id) == RED
    item = _row(view, sku_id).cells[0].items[0]
    assert (item.status, item.certification_id) == ("EXPIRED", instance_id)  # 실효 상태


# ── ⑤ 세트 롤업 ──────────────────────────────────────────────────────────────


def test_a_set_rolls_up_its_components_worst_first() -> None:
    profile = create_item_profile("PRF-SET")
    template = _template(profile_id=profile)
    comp_a, _ = _sku("CMP-A", profile_id=profile)
    comp_b, _ = _sku("CMP-B", profile_id=profile)
    set_id = _set_sku("SET-1", [comp_a, comp_b])
    _instance(template, "SKU", comp_a, status="APPROVED", expires_on=FAR)
    _instance(template, "SKU", comp_b, status="IN_REVIEW")
    view = _matrix()
    assert _color(view, comp_a) == GREEN and _color(view, comp_b) == YELLOW
    assert _color(view, set_id) == YELLOW  # 최악값
    cell = _row(view, set_id).cells[0]
    assert {item.via_component_sku_code for item in cell.items} == {"CMP-A", "CMP-B"}
    assert [c.sku_code for c in _row(view, set_id).components] == ["CMP-A", "CMP-B"]
    # 한 구성품이 🔴이면 세트도 🔴
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(Certification).where(
                Certification.target_id == comp_b, Certification.target_type == "SKU"
            )
        ).scalar_one()
        row.status = "NOT_STARTED"
    assert _color(_matrix(), set_id) == RED


def test_a_set_adds_its_own_requirements_to_the_rollup() -> None:
    """세트 자신의 SKU 요건도 셈에 든다 — 구성품이 모두 🟢이어도 세트 요건이 비면 🔴"""
    comp_profile = create_item_profile("PRF-SET-C")
    set_profile = create_item_profile("PRF-SET-S")
    comp_template = _template("US", "구성품 요건", profile_id=comp_profile)
    set_template = _template("US", "세트 요건", profile_id=set_profile)
    comp, _ = _sku("CMP-OWN", profile_id=comp_profile)
    set_id = _set_sku("SET-OWN", [comp], profile_id=set_profile)
    _instance(comp_template, "SKU", comp, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), set_id) == RED
    _instance(set_template, "SKU", set_id, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), set_id) == GREEN


def test_set_components_with_no_requirements_do_not_dilute_or_block() -> None:
    """요건 없는 구성품(⚪)은 세트 색에 영향을 주지 않는다 — ⚪은 중립"""
    profile = create_item_profile("PRF-SET-N")
    template = _template(profile_id=profile)
    with_req, _ = _sku("CMP-REQ", profile_id=profile)
    without_req, _ = _sku("CMP-NONE")
    set_id = _set_sku("SET-N", [with_req, without_req])
    _instance(template, "SKU", with_req, status="APPROVED", expires_on=FAR)
    assert _color(_matrix(), set_id) == GREEN


def test_an_empty_set_is_gray() -> None:
    set_id = _set_sku("SET-EMPTY", [])
    create_market("US")
    assert _color(_matrix(), set_id) == GRAY


def test_shared_company_requirement_is_counted_once_in_a_set() -> None:
    profile = create_item_profile("PRF-SET-CO")
    template = _template("US", "US Agent 지정", "COMPANY", profile_id=profile)
    comp_a, _ = _sku("SETCO-A", profile_id=profile)
    comp_b, _ = _sku("SETCO-B", profile_id=profile)
    set_id = _set_sku("SETCO", [comp_a, comp_b])
    _instance(template, "COMPANY", None, status="APPROVED", expires_on=FAR)
    cell = _row(_matrix(), set_id).cells[0]
    assert cell.summary.required == 1  # 두 구성품이 같은 자사 요건을 걸어도 1건


# ── 페이지·필터·성능 ─────────────────────────────────────────────────────────


def test_pages_split_the_skus_and_keep_the_same_columns() -> None:
    """SKU 쪽 나눔 — 열(시장)은 모든 쪽에 같다. 51건 = 50 + 1"""
    create_market("US")
    for index in range(51):
        _sku(f"PAGE-{index:03d}")
    first = _matrix(offset=0, limit=50)
    second = _matrix(offset=50, limit=50)
    assert (first.total, len(first.rows), len(second.rows)) == (51, 50, 1)
    assert [m.code for m in first.markets] == [m.code for m in second.markets]
    assert first.rows[0].sku_code == "PAGE-000" and second.rows[0].sku_code == "PAGE-050"


def test_filters_narrow_the_rows() -> None:
    profile = create_item_profile("PRF-FILTER")
    a, _ = _sku("FIL-A", profile_id=profile, name_ko="수분 세럼")
    b, _ = _sku("FIL-B", name_ko="진정 크림")
    set_id = _set_sku("FIL-SET", [a])
    assert {r.sku_id for r in _matrix(q="세럼").rows} == {a}
    assert {r.sku_id for r in _matrix(q="fil-b").rows} == {b}  # 대소문자 무시
    assert {r.sku_id for r in _matrix(item_profile_id=profile).rows} == {a}
    assert {r.sku_id for r in _matrix(kind="SET").rows} == {set_id}
    assert {r.sku_id for r in _matrix(kind="SINGLE").rows} == {a, b}


def test_search_treats_percent_and_underscore_literally() -> None:
    """검색어의 %·_는 와일드카드가 아니다 — '10%'가 전 행을 끌어오지 않는다"""
    a, _ = _sku("LIT-A", name_ko="10% 할인 세럼")
    b, _ = _sku("LIT-B", name_ko="일반 세럼")
    assert {r.sku_id for r in _matrix(q="10%").rows} == {a}
    assert {r.sku_id for r in _matrix(q="%").rows} == {a}
    assert {r.sku_id for r in _matrix(q="_").rows} == set()
    assert b not in {r.sku_id for r in _matrix(q="%").rows}


def test_query_count_does_not_grow_with_the_number_of_skus() -> None:
    """한 화면의 질의 수는 행 수와 무관하다 — N+1 금지(§18.4)"""
    profile = create_item_profile("PRF-PERF")
    template = _template(profile_id=profile)
    _template("US", "제품 요건", "PRODUCT", profile_id=profile)
    _template("US", "기업 요건", "COMPANY", profile_id=profile)
    maker = create_partner("PTN-PERF", types=("OEM",))
    _template("US", "시설 요건", "FACILITY", profile_id=profile)
    for index in range(3):
        sku_id, _ = _sku(
            f"PF-{index:02d}", profile_id=profile, product_profile_id=profile, manufacturer_id=maker
        )
        _instance(template, "SKU", sku_id, status="PREPARING")
    few = _statements(lambda: _matrix())
    for index in range(3, 30):
        sku_id, _ = _sku(
            f"PF-{index:02d}", profile_id=profile, product_profile_id=profile, manufacturer_id=maker
        )
        _instance(template, "SKU", sku_id, status="PREPARING")
    many = _statements(lambda: _matrix())
    assert few == many, f"SKU 3건 {few}회 vs 30건 {many}회 — 행당 질의가 늘었다"


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


# ── 계산값 미저장 ────────────────────────────────────────────────────────────


def test_computing_the_matrix_writes_nothing() -> None:
    """읽기 전용 — 인증·알림·이벤트·감사 로그 어느 것도 늘지 않는다(집계 저장 없음의 실측)"""
    profile = create_item_profile("PRF-RO")
    template = _template(profile_id=profile)
    sku_id, _ = _sku("RO", profile_id=profile)
    _instance(template, "SKU", sku_id, status="APPROVED", expires_on=date(2026, 9, 28))

    def counts() -> tuple[int, ...]:
        with unit_of_work() as uow:
            return tuple(
                uow.session.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (Certification, Alert, Event, AuditLog)
            )

    before = counts()
    _matrix()
    assert counts() == before
    # 스윕이 아직 안 돈 만료 건도 저장 상태는 그대로다 — 실효 상태는 계산일 뿐이다
    with unit_of_work() as uow:
        stored = uow.session.execute(select(Certification.status)).scalar_one()
    assert stored == "APPROVED"
