"""C — 기일 캘린더 조회 서비스 (§14 ④ 인증 보드 / S2-3 PR-3 안건 ⑥).

★ 고정하는 것: ① 후보 정의가 **기일 스캔과 같다**(활성·종결 2태 제외·만료일/유효기간 있음) —
  다르면 "알림은 왔는데 캘린더에 없다"가 생긴다 ② 기간 경계(포함) ③ 날짜순·종류순 정렬과 쪽 자름
  ④ 도과 표시가 계산값(오늘 기준) ⑤ 기간 상한 93일과 입력 오류의 사용자 안내 ⑥ 질의 수가 행 수와 무관.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy import event

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import utcnow
from app.modules.certifications import service as certifications
from app.modules.certifications.models import Certification
from app.modules.deadlines import board
from app.modules.deadlines import service as scan
from app.modules.documents.models import Document
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    create_certification_instance,
    create_item_profile,
    create_link_document,
    create_requirement_template,
    create_sku_with_axes,
    create_user,
)

pytestmark = pytest.mark.group_c

TODAY = date(2026, 9, 29)
START = date(2026, 9, 1)
END = date(2026, 9, 30)


def _cert(
    template: int,
    sku: int,
    *,
    on: date | None,
    status: str = "APPROVED",
    assignee_id: int | None = None,
) -> int:
    return create_certification_instance(
        template, "SKU", sku, status=status, expires_on=on, assignee_id=assignee_id
    )


def _soft_delete(model: type, row_id: int) -> None:
    with unit_of_work() as uow:
        row = uow.session.get(model, row_id)
        assert row is not None
        row.deleted_at = utcnow()  # type: ignore[attr-defined]


@pytest.fixture
def setup() -> tuple[int, int]:
    """(요건 템플릿, SKU) — 인증 여러 건은 SKU를 늘려 만든다."""
    profile = create_item_profile("PRF-CAL")
    template = create_requirement_template("US", "MoCRA 제품 리스팅", profile_id=profile)
    sku, _ = create_sku_with_axes("CAL-1", profile_id=profile, name_ko="수분 세럼")
    return template, sku


def _items(**kwargs: object) -> tuple[list[board.CalendarItem], int]:
    params: dict[str, object] = {
        "start": START,
        "end": END,
        "offset": 0,
        "limit": 200,
        "base_date": TODAY,
    }
    params.update(kwargs)
    return board.list_calendar_items(**params)  # type: ignore[arg-type]


def test_window_is_inclusive_and_excludes_outside(setup: tuple[int, int]) -> None:
    template, sku = setup
    others = [create_sku_with_axes(f"CAL-{i}")[0] for i in range(2, 6)]
    inside_first = _cert(template, sku, on=START)  # 시작일 포함
    inside_last = _cert(template, others[0], on=END)  # 종료일 포함
    _cert(template, others[1], on=date(2026, 8, 31))  # 하루 앞
    _cert(template, others[2], on=date(2026, 10, 1))  # 하루 뒤
    _cert(template, others[3], on=None)  # 무기한 — 기일 없음
    items, total = _items()
    assert total == 2
    assert [item.id for item in items] == [inside_first, inside_last]
    assert [item.on for item in items] == [START, END]


def test_candidates_match_the_deadline_scan(setup: tuple[int, int]) -> None:
    """종결 2태(반려·중단)·삭제 행은 제외 — 기일 스캔의 후보 정의와 같다"""
    template, sku = setup
    skus = [create_sku_with_axes(f"CAL-X{i}")[0] for i in range(4)]
    kept = _cert(template, sku, on=date(2026, 9, 10), status="RENEWING")  # 갱신중도 포함
    _cert(template, skus[0], on=date(2026, 9, 11), status="REJECTED")
    _cert(template, skus[1], on=date(2026, 9, 12), status="SUSPENDED")
    deleted = _cert(template, skus[2], on=date(2026, 9, 13))
    with unit_of_work() as uow:
        row = uow.session.get(Certification, deleted)
        assert row is not None
        row.deleted_at = utcnow()
    expired = _cert(template, skus[3], on=date(2026, 9, 14), status="EXPIRED")  # 만료도 포함
    items, _ = _items()
    assert [item.id for item in items] == [kept, expired]


def test_documents_appear_by_valid_until_and_only_when_active(setup: tuple[int, int]) -> None:
    _, sku = setup
    shown = create_link_document(sku, valid_until=date(2026, 9, 15), tag="a")
    create_link_document(sku, valid_until=None, tag="none")  # 유효기간 없음
    create_link_document(sku, valid_until=date(2026, 10, 15), tag="out")  # 기간 밖
    gone = create_link_document(sku, valid_until=date(2026, 9, 16), tag="gone")
    with unit_of_work() as uow:
        row = uow.session.get(Document, gone)
        assert row is not None
        row.deleted_at = utcnow()
    items, total = _items()
    assert total == 1
    (item,) = items
    assert (item.kind, item.id, item.status, item.assignee_id) == ("DOCUMENT", shown, None, None)
    assert item.title == "자유판매증명서(CFS) · CAL-1"  # 종류명 · 소유자 표시(SKU 코드)


def test_certifications_and_documents_merge_by_date_then_kind(setup: tuple[int, int]) -> None:
    template, sku = setup
    other, _ = create_sku_with_axes("CAL-M2")
    day = date(2026, 9, 20)
    doc = create_link_document(sku, valid_until=day, tag="same-day")
    cert = _cert(template, sku, on=day)
    early_doc = create_link_document(other, valid_until=date(2026, 9, 5), tag="early")
    items, total = _items()
    assert total == 3
    assert [(item.kind, item.id) for item in items] == [
        ("DOCUMENT", early_doc),
        ("CERTIFICATION", cert),  # 같은 날은 인증이 먼저
        ("DOCUMENT", doc),
    ]


def test_titles_carry_market_requirement_and_target(setup: tuple[int, int]) -> None:
    template, sku = setup
    _cert(template, sku, on=date(2026, 9, 10))
    (item,), _ = _items()
    assert item.title == "[US] 스냅샷 — 수분 세럼"  # 시장 · 스냅샷 요건명 — 대상명


def test_overdue_is_the_calendar_definition_relative_to_today(setup: tuple[int, int]) -> None:
    template, sku = setup
    yesterday_sku, _ = create_sku_with_axes("CAL-Y")
    renewing_sku, _ = create_sku_with_axes("CAL-R")
    today_cert = _cert(template, sku, on=TODAY)  # 당일은 도과가 아니다
    past = _cert(template, yesterday_sku, on=date(2026, 9, 28))
    renewing = _cert(template, renewing_sku, on=date(2026, 9, 1), status="RENEWING")  # 갱신중 도과
    doc_past = create_link_document(sku, valid_until=date(2026, 9, 2), tag="past")
    doc_today = create_link_document(sku, valid_until=TODAY, tag="today")
    items, _ = _items()
    flags = {(item.kind, item.id): item.is_overdue for item in items}
    assert flags[("CERTIFICATION", today_cert)] is False
    assert flags[("CERTIFICATION", past)] is True
    assert flags[("CERTIFICATION", renewing)] is True
    assert flags[("DOCUMENT", doc_past)] is True
    assert flags[("DOCUMENT", doc_today)] is False


def test_assignee_name_is_shown(setup: tuple[int, int]) -> None:
    template, sku = setup
    assignee = create_user("cal-owner@example.com", roles=(RoleCode.CERT,))
    _cert(template, sku, on=date(2026, 9, 10), assignee_id=assignee)
    (item,), _ = _items()
    assert (item.assignee_id, item.assignee_name) == (assignee, "테스트 사용자")


def test_pages_slice_the_merged_list_and_total_counts_everything(setup: tuple[int, int]) -> None:
    _, sku = setup
    for day in range(1, 8):
        create_link_document(sku, valid_until=date(2026, 9, day), tag=f"p{day}")
    first, total = _items(offset=0, limit=3)
    second, _ = _items(offset=3, limit=3)
    last, _ = _items(offset=6, limit=3)
    assert total == 7
    assert [item.on.day for item in first] == [1, 2, 3]
    assert [item.on.day for item in second] == [4, 5, 6]
    assert [item.on.day for item in last] == [7]


@pytest.mark.parametrize(
    ("start", "end", "field"),
    [
        (date(2026, 9, 30), date(2026, 9, 1), "to"),  # 종료가 시작보다 앞
        (date(2026, 1, 1), date(2026, 12, 31), "to"),  # 93일 초과
    ],
)
def test_bad_windows_are_refused_with_guidance(start: date, end: date, field: str) -> None:
    with pytest.raises(AppError) as raised:
        _items(start=start, end=end)
    assert field in raised.value.detail
    assert "기간" in raised.value.detail[field]


def test_the_window_limit_counts_both_end_days() -> None:
    """최대 93일 = 시작일·종료일을 포함해 93일(종료−시작 92) — 94일째는 거절"""
    from datetime import timedelta

    _items(start=START, end=START + timedelta(days=board.MAX_SPAN_DAYS - 1))  # 93일 — 통과
    with pytest.raises(AppError) as raised:
        _items(start=START, end=START + timedelta(days=board.MAX_SPAN_DAYS))  # 94일
    assert "포함해 최대 93일" in raised.value.detail["to"]


def test_a_one_day_window_is_allowed_but_a_reversed_one_is_not(setup: tuple[int, int]) -> None:
    from datetime import timedelta

    template, sku = setup
    day = date(2026, 9, 10)
    on_day = _cert(template, sku, on=day)
    items, total = _items(start=day, end=day)  # from == to 는 하루짜리 창이다
    assert (total, [item.id for item in items]) == (1, [on_day])
    with pytest.raises(AppError):
        _items(start=day + timedelta(days=1), end=day)


def test_the_calendar_candidates_are_exactly_the_scan_candidates(setup: tuple[int, int]) -> None:
    """ "알림은 왔는데 캘린더에 없다"의 구조적 방지 — 후보 정의를 스캔 함수와 직접 대조한다.

    스캔 쪽 제외 조건이 바뀌면(예: 새 종결 상태) 손으로 쓴 기대 목록은 그대로 초록이지만,
    이 대조는 바로 빨개진다.
    """
    template, sku = setup
    others = [create_sku_with_axes(f"EQ-{index}")[0] for index in range(8)]
    _cert(template, sku, on=date(2026, 9, 10), status="APPROVED")
    _cert(template, others[0], on=date(2026, 9, 11), status="RENEWING")  # 갱신중 도과도 후보
    _cert(template, others[1], on=date(2026, 9, 12), status="EXPIRED")
    _cert(template, others[2], on=date(2026, 9, 13), status="REJECTED")  # 제외
    _cert(template, others[3], on=date(2026, 9, 14), status="SUSPENDED")  # 제외
    _cert(template, others[4], on=None)  # 무기한 — 기일 없음
    deleted = _cert(template, others[5], on=date(2026, 9, 15))
    _soft_delete(Certification, deleted)
    create_link_document(sku, valid_until=date(2026, 9, 16), tag="eq-keep")
    create_link_document(sku, valid_until=None, tag="eq-none")
    gone = create_link_document(sku, valid_until=date(2026, 9, 17), tag="eq-gone")
    _soft_delete(Document, gone)

    with unit_of_work() as uow:
        scan_certifications = set(scan._certification_candidate_ids(uow.session))
        scan_documents = set(scan._document_candidate_ids(uow.session))
    items, _ = _items()
    assert scan_certifications and scan_documents  # 공허 통과 방지
    assert {item.id for item in items if item.kind == "CERTIFICATION"} == scan_certifications
    assert {item.id for item in items if item.kind == "DOCUMENT"} == scan_documents


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


def test_query_count_is_flat_for_the_calendar_and_the_certification_list() -> None:
    """캘린더·인증 목록의 질의 수는 행 수와 무관하다 — 대상·담당자 표기 N+1 방지(§18.4)"""
    profile = create_item_profile("PRF-CAL-PERF")
    template = create_requirement_template("US", "MoCRA 제품 리스팅", profile_id=profile)
    owner = create_user("cal-perf@example.com", roles=(RoleCode.CERT,))

    def grow(start: int, stop: int) -> None:
        for index in range(start, stop):
            sku, _ = create_sku_with_axes(f"CP-{index:03d}", profile_id=profile)
            _cert(template, sku, on=date(2026, 9, 10), assignee_id=owner)
            create_link_document(sku, valid_until=date(2026, 9, 12), tag=f"perf{index}")

    grow(0, 3)
    calendar_few = _statements(lambda: _items())
    list_few = _statements(
        lambda: certifications.list_certifications(
            template_id=None, target_type=None, status=None, offset=0, limit=50
        )
    )
    grow(3, 30)
    calendar_many = _statements(lambda: _items())
    list_many = _statements(
        lambda: certifications.list_certifications(
            template_id=None, target_type=None, status=None, offset=0, limit=50
        )
    )
    assert calendar_few > 0 and list_few > 0  # 리스너가 실제로 붙어 있다(0 == 0 공허 통과 방지)
    assert calendar_few == calendar_many, f"캘린더 3건 {calendar_few}회 vs 30건 {calendar_many}회"
    assert list_few == list_many, f"인증 목록 3건 {list_few}회 vs 30건 {list_many}회"
