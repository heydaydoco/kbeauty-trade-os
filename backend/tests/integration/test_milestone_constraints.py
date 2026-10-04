"""A·J·K. 통관·마일스톤 계열 5표(M15)의 불변식을 DB가 강제한다 (S3-2 PR-4a / ADR-0074·0080·0083 / design-integrated §2.1 (e)~(i)·§9 R-16·R-26).

★ 서비스를 거치지 않고 raw INSERT/UPDATE로 위반시킨다(alembic check는 CHECK 정의를 못 본다 — 함정 ①). 위반 케이스마다 같은 조건의 **양성 대조**가
  통과함을 함께 확인한다(공회전 방지). 핵심: **파생 3종 DB 거부**(덮어쓰기 금지 2중의 DB 층)·신고수리 실적 열 NULL 강제(X-02)·소유자 정확히 하나·
  OEM 4종 ⇔ PO·날짜형/시각형 형태·이력 사유·무변경 이력 금지·IMMUTABLE 2표 권한 회수·재유입 = 신규·M15 downgrade 가드·번역표 완결성(J-09).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.constraints import BLANK_CHAR_CLASS, SPACE_CHAR_CLASS
from app.core.db.session import engine, owner_engine
from app.modules.shipments.service import CONSTRAINT_ERRORS, MILESTONE_CONSTRAINT_ERRORS
from app.modules.trade_docs.constants import DERIVED_MILESTONES, STORED_MILESTONES
from tests.factories.shipments import confirmed_so, raw_shipment
from tests.factories.trade import raw_po
from tests.support.factories import create_item_profile, create_user
from tests.support.kst import pin_today_kst

pytestmark = pytest.mark.group_a


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정(적대 검토 반영 ⑩) — 시험마다 base 날짜를 한 번 잡아
    앱 import 지점(마일스톤·통관·선적 흐름·보드)의 `today_kst`를 같은 날로 맞춘다."""
    pin_today_kst(monkeypatch)


CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
PERMISSION_DENIED = "42501"
NEW_TABLES = (
    "customs_records",
    "milestones",
    "milestone_changes",
    "milestone_change_notices",
    "item_profile_milestone_types",
)


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


def _insert(connection: Connection, table: str, values: dict[str, Any]) -> int:
    columns = list(values)
    return int(
        connection.execute(
            text(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES"
                f" ({', '.join(':' + c for c in columns)}) RETURNING id"
            ),
            values,
        ).scalar_one()
    )


@pytest.fixture
def shipment() -> int:
    return raw_shipment(confirmed_so((5,))["id"])


# ── milestones ───────────────────────────────────────────────────────────────


def _milestone(shipment_id: int | None, **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "shipment_id": shipment_id,
        "po_id": None,
        "milestone_type": "ETD",
        "planned_on": date(2026, 11, 5),
    }
    values.update(override)
    return values


def test_milestones_store_every_live_shape(shipment: int) -> None:
    """양성 — 날짜형(ETD 계획·실적)·시각형(Cargo Closing UTC+tz)·빈 초안 행(값 없음)·OEM PO 소유 행이 저장된다"""
    po = raw_po()
    with owner_engine.begin() as connection:
        _insert(connection, "milestones", _milestone(shipment, actual_on=date(2026, 10, 1)))
        _insert(
            connection,
            "milestones",
            _milestone(
                shipment,
                milestone_type="CARGO_CLOSING",
                planned_on=None,
                planned_at="2026-11-01T09:00:00+00:00",
                tz="Asia/Seoul",
            ),
        )
        _insert(
            connection, "milestones", _milestone(shipment, milestone_type="PSI", planned_on=None)
        )
        _insert(
            connection,
            "milestones",
            _milestone(None, po_id=po, milestone_type="FILLING", planned_on=date(2026, 11, 1)),
        )


_MILESTONE_CASES: list[tuple[str, dict[str, Any], str]] = [
    # 파생 3종은 값 공간에 없다 — DB 직접 INSERT도 거부(덮어쓰기 금지 2중의 DB 층, GC-21)
    ("derived_loading", {"milestone_type": "LOADING_DEADLINE"}, "ck_milestones_type_valid"),
    ("derived_payment", {"milestone_type": "PAYMENT_DUE"}, "ck_milestones_type_valid"),
    (
        "derived_presentation",
        {"milestone_type": "PRESENTATION_DEADLINE"},
        "ck_milestones_type_valid",
    ),
    ("unknown_type", {"milestone_type": "ATD"}, "ck_milestones_type_valid"),
    # OEM 4종은 PO 소유만
    ("oem_on_shipment", {"milestone_type": "FILLING"}, "ck_milestones_owner_type_scope"),
    # 날짜형에 시각 값·시간대
    (
        "date_type_with_instant",
        {"planned_on": None, "planned_at": "2026-11-05T00:00:00+00:00", "tz": "UTC"},
        "ck_milestones_date_shape",
    ),
    ("date_type_with_tz", {"tz": "Asia/Seoul"}, "ck_milestones_date_shape"),
    # 시각형에 날짜 값
    (
        "datetime_type_with_date",
        {"milestone_type": "DOC_CUTOFF"},
        "ck_milestones_datetime_shape",
    ),
    # 시각 값 ⇔ tz
    (
        "instant_without_tz",
        {"milestone_type": "DOC_CUTOFF", "planned_on": None, "planned_at": "2026-11-01T00:00:00Z"},
        "ck_milestones_tz_iff_instant",
    ),
    (
        "tz_without_instant",
        {"milestone_type": "DOC_CUTOFF", "planned_on": None, "tz": "Asia/Seoul"},
        "ck_milestones_tz_iff_instant",
    ),
    (
        "tz_with_control",
        {
            "milestone_type": "DOC_CUTOFF",
            "planned_on": None,
            "planned_at": "2026-11-01T00:00:00Z",
            "tz": "Asia/Seoul\n",
        },
        "ck_milestones_tz_format",
    ),
    # 신고수리 실적은 통관 기록에서만(X-02)
    (
        "customs_actual",
        {"milestone_type": "CUSTOMS_CLEARED", "actual_on": date(2026, 10, 1)},
        "ck_milestones_customs_actual_from_records",
    ),
    # 업무 날짜 범위 2000~2999(적대 검토 반영 ⑤ — 달력 끝 값의 파생 산술 OverflowError 500 방지)
    ("planned_before_2000", {"planned_on": date(1999, 12, 31)}, "ck_milestones_value_range"),
    ("actual_after_2999", {"actual_on": date(3000, 1, 1)}, "ck_milestones_value_range"),
    (
        "instant_at_year_3000",
        {
            "milestone_type": "DOC_CUTOFF",
            "planned_on": None,
            "planned_at": "3000-01-01T00:00:00Z",
            "tz": "UTC",
        },
        "ck_milestones_value_range",
    ),
    (
        "instant_before_2000",
        {
            "milestone_type": "CARGO_CLOSING",
            "planned_on": None,
            "actual_at": "1999-12-31T23:59:59Z",
            "tz": "UTC",
        },
        "ck_milestones_value_range",
    ),
]


def test_business_date_range_boundaries_are_stored(shipment: int) -> None:
    """양성 경계 — 2000-01-01·2999-12-31(날짜형), 2000-01-01T00:00Z·2999-12-31T23:59:59Z(시각형)는 저장된다"""
    with owner_engine.begin() as connection:
        _insert(
            connection,
            "milestones",
            _milestone(shipment, planned_on=date(2000, 1, 1), actual_on=date(2999, 12, 31)),
        )
        _insert(
            connection,
            "milestones",
            _milestone(
                shipment,
                milestone_type="DOC_CUTOFF",
                planned_on=None,
                planned_at="2000-01-01T00:00:00Z",
                actual_at="2999-12-31T23:59:59Z",
                tz="UTC",
            ),
        )


@pytest.mark.parametrize(("case", "override", "constraint"), _MILESTONE_CASES)
def test_milestone_checks_reject_violations(
    shipment: int, case: str, override: dict[str, Any], constraint: str
) -> None:
    """마일스톤 CHECK — 파생 종류·OEM 소유·형태·시간대·신고수리 실적 위반은 DB가 거부한다(제약 이름까지 단언)"""
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, "milestones", _milestone(shipment, **override))
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), case


def test_milestone_owner_is_exactly_one(shipment: int) -> None:
    """소유자 정확히 하나 — 둘 다·둘 다 없음 거부, PO 소유는 OEM 4종만(선적 종류를 PO에 두면 거부)"""
    po = raw_po()
    for values in (
        _milestone(shipment, po_id=po, milestone_type="FILLING"),
        _milestone(None),
    ):
        with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
            _insert(connection, "milestones", values)
        assert _state(caught.value)[0] == CHECK_VIOLATION
        assert _state(caught.value)[1] in {
            "ck_milestones_one_owner",
            "ck_milestones_owner_type_scope",
        }
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, "milestones", _milestone(None, po_id=po, milestone_type="ETD"))
    assert _state(caught.value) == (CHECK_VIOLATION, "ck_milestones_owner_type_scope")


@pytest.mark.group_j
def test_milestone_type_is_one_live_row_per_owner_and_reenters_as_new(shipment: int) -> None:
    """J-13 — (선적, 종류) 살아 있는 행 유일, soft delete 뒤 같은 키 재유입은 부활이 아니라 신규 행"""
    with owner_engine.begin() as connection:
        first = _insert(connection, "milestones", _milestone(shipment))
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, "milestones", _milestone(shipment))
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_milestones_shipment_id_milestone_type_active",
    )
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE milestones SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        again = _insert(connection, "milestones", _milestone(shipment))
    assert again != first


# ── customs_records ──────────────────────────────────────────────────────────


def _customs(shipment_id: int, **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "shipment_id": shipment_id,
        "declaration_kind": "EXPORT",
        "declaration_no": "12345-26-0001234",
        "declared_on": date(2026, 9, 20),
        "accepted_on": date(2026, 9, 21),
    }
    values.update(override)
    return values


_CUSTOMS_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("kind_unknown", {"declaration_kind": "TRANSIT"}, "ck_customs_records_kind_valid"),
    (
        "accept_before_declare",
        {"accepted_on": date(2026, 9, 19)},
        "ck_customs_records_accept_after_declare",
    ),
    ("number_blank", {"declaration_no": "  "}, "ck_customs_records_declaration_no_shape"),
    ("number_inner_space", {"declaration_no": "123 45"}, "ck_customs_records_declaration_no_shape"),
    ("number_lowercase", {"declaration_no": "abc-1"}, "ck_customs_records_declaration_no_shape"),
    ("number_control", {"declaration_no": "AB\u00851"}, "ck_customs_records_declaration_no_shape"),
    # ASCII 패턴(적대 검토 반영 ④) — 비ASCII 대문자·선두 구분자·허용 밖 기호
    ("number_non_ascii", {"declaration_no": "ÄB-1"}, "ck_customs_records_declaration_no_shape"),
    ("number_eszett_upper", {"declaration_no": "ẞ1"}, "ck_customs_records_declaration_no_shape"),
    (
        "number_leading_hyphen",
        {"declaration_no": "-AB1"},
        "ck_customs_records_declaration_no_shape",
    ),
    ("number_symbol", {"declaration_no": "AB_1"}, "ck_customs_records_declaration_no_shape"),
    ("note_blank", {"note": "   "}, "ck_customs_records_note_clean"),
    # 유니코드 공백·보이지 않는 글자만의 메모(btrim은 U+0020만 자른다 — 적대 검토 반영 ⑥)
    ("note_unicode_spaces", {"note": "　  "}, "ck_customs_records_note_clean"),
    ("note_zero_width", {"note": "​ㅤ"}, "ck_customs_records_note_clean"),
    ("note_control", {"note": "메모\u0007"}, "ck_customs_records_note_clean"),
    # 업무 날짜 범위(적대 검토 반영 ⑤)
    (
        "declared_before_2000",
        {"declared_on": date(1999, 12, 31), "accepted_on": None},
        "ck_customs_records_date_range",
    ),
    ("accepted_after_2999", {"accepted_on": date(3000, 1, 1)}, "ck_customs_records_date_range"),
]


@pytest.mark.parametrize(("case", "override", "constraint"), _CUSTOMS_CASES)
def test_customs_checks_reject_violations(
    shipment: int, case: str, override: dict[str, Any], constraint: str
) -> None:
    """통관 기록 CHECK — 구분·수리일 ≥ 신고일·신고번호 형태(비공백·공백/제어문자 없음·대문자)·메모 위생"""
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, "customs_records", _customs(shipment, **override))
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), case


def test_customs_records_accept_multi_line_notes_and_pending_acceptance(shipment: int) -> None:
    """양성 — 미수리(수리일 NULL)·여러 줄 메모(탭·LF·CR)·수리일 = 신고일 경계는 저장된다(1:N 분할 신고 — X-03)"""
    with owner_engine.begin() as connection:
        _insert(
            connection,
            "customs_records",
            _customs(shipment, accepted_on=None, declaration_no="A-1"),
        )
        _insert(
            connection,
            "customs_records",
            _customs(shipment, declaration_no="A-2", note="1행\r\n2행\t끝"),
        )
        _insert(
            connection,
            "customs_records",
            _customs(shipment, declaration_no="A-3", accepted_on=date(2026, 9, 20)),
        )


@pytest.mark.group_j
def test_declaration_number_is_unique_per_kind_and_reenters_as_new(shipment: int) -> None:
    """J-13 — (구분, 신고번호) 살아 있는 기록 유일(같은 번호의 다른 구분은 별개), soft delete 뒤 재유입 = 신규"""
    with owner_engine.begin() as connection:
        first = _insert(connection, "customs_records", _customs(shipment))
        _insert(connection, "customs_records", _customs(shipment, declaration_kind="IMPORT"))
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, "customs_records", _customs(shipment))
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_customs_records_declaration_kind_declaration_no_active",
    )
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE customs_records SET deleted_at = now() WHERE id = :i"), {"i": first}
        )
        assert _insert(connection, "customs_records", _customs(shipment)) != first


# ── milestone_changes·notices (IMMUTABLE) ─────────────────────────────────────


@pytest.fixture
def milestone(shipment: int) -> int:
    with owner_engine.begin() as connection:
        return _insert(connection, "milestones", _milestone(shipment))


def _change(milestone_id: int, actor: int, **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "milestone_id": milestone_id,
        "change_kind": "PLAN_SET",
        "new_on": date(2026, 11, 5),
        "actor_user_id": actor,
    }
    values.update(override)
    return values


_CHANGE_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("kind_unknown", {"change_kind": "PLAN_DELETED"}, "ck_milestone_changes_change_kind_valid"),
    (
        "rollover_without_reason",
        {"change_kind": "PLAN_CHANGED", "old_on": date(2026, 11, 1)},
        "ck_milestone_changes_reason_required",
    ),
    (
        "correction_without_reason",
        {"change_kind": "ACTUAL_CORRECTED", "old_on": date(2026, 11, 1), "new_on": None},
        "ck_milestone_changes_reason_required",
    ),
    ("reason_blank", {"reason": "  "}, "ck_milestone_changes_reason_clean"),
    # 유니코드 공백만(U+3000·U+00A0·U+2003)·보이지 않는 글자만 — 서비스를 우회해도 DB가 거부(적대 검토 반영 ⑥)
    ("reason_ideographic_space", {"reason": "　　"}, "ck_milestone_changes_reason_clean"),
    ("reason_nbsp", {"reason": " "}, "ck_milestone_changes_reason_clean"),
    ("reason_em_space", {"reason": "   "}, "ck_milestone_changes_reason_clean"),
    ("reason_zero_width", {"reason": "​﻿"}, "ck_milestone_changes_reason_clean"),
    ("reason_hangul_filler", {"reason": "ㅤ"}, "ck_milestone_changes_reason_clean"),
    ("reason_control", {"reason": "사유\u0085"}, "ck_milestone_changes_reason_clean"),
    ("reason_too_long", {"reason": "가" * 501}, "ck_milestone_changes_reason_clean"),
    (
        "value_both_shapes",
        {"new_at": "2026-11-05T00:00:00Z", "new_tz": "UTC"},
        "ck_milestone_changes_value_pairs",
    ),
    (
        "instant_without_tz",
        {"new_on": None, "new_at": "2026-11-05T00:00:00Z"},
        "ck_milestone_changes_value_pairs",
    ),
    ("set_with_old", {"old_on": date(2026, 11, 1)}, "ck_milestone_changes_kind_values"),
    (
        "rollover_without_old",
        {"change_kind": "PLAN_CHANGED", "reason": "선사 사정"},
        "ck_milestone_changes_kind_values",
    ),
    # 새 값 없음(삭제)은 실적 정정에만 — 계획 변경으로 계획을 지울 수 없다(계획 삭제 경로 0)
    (
        "rollover_to_nothing",
        {"change_kind": "PLAN_CHANGED", "old_on": date(2026, 11, 1), "new_on": None, "reason": "x"},
        "ck_milestone_changes_kind_values",
    ),
    (
        "no_change",
        {
            "change_kind": "PLAN_CHANGED",
            "old_on": date(2026, 11, 5),
            "reason": "같은 값",
        },
        "ck_milestone_changes_changed",
    ),
]


@pytest.mark.parametrize(("case", "override", "constraint"), _CHANGE_CASES)
def test_change_log_checks_reject_violations(
    milestone: int, case: str, override: dict[str, Any], constraint: str
) -> None:
    """변경 이력 CHECK — 종류·사유 필수(롤오버·정정)·사유 위생·값 쌍·종류 의미·무변경 이력 금지"""
    actor = create_user(f"mc-{case}@example.com")
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, "milestone_changes", _change(milestone, actor, **override))
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), case


def test_change_log_stores_every_kind(milestone: int) -> None:
    """양성 — 설정·롤오버(사유)·실적 기록·실적 정정 삭제(새 값 없음 + 사유)·시각형 값(tz 동반)이 저장된다"""
    actor = create_user("mc-ok@example.com")
    rows = [
        _change(milestone, actor),
        _change(
            milestone,
            actor,
            change_kind="PLAN_CHANGED",
            old_on=date(2026, 11, 5),
            new_on=date(2026, 11, 12),
            reason="선사 스케줄 변경",
        ),
        _change(milestone, actor, change_kind="ACTUAL_RECORDED", new_on=date(2026, 10, 1)),
        _change(
            milestone,
            actor,
            change_kind="ACTUAL_CORRECTED",
            old_on=date(2026, 10, 1),
            new_on=None,
            reason="오입력 삭제",
        ),
        _change(
            milestone,
            actor,
            new_on=None,
            new_at="2026-11-01T00:00:00Z",
            new_tz="America/Los_Angeles",
        ),
    ]
    with engine.begin() as connection:
        for values in rows:
            _insert(connection, "milestone_changes", values)


@pytest.mark.group_j
def test_change_log_and_notices_are_immutable_for_the_app_account(milestone: int) -> None:
    """J-11 — 앱 계정은 변경 이력·통보 연결에 INSERT만 한다: UPDATE·DELETE·TRUNCATE는 권한 거부(42501)"""
    actor = create_user("mc-imm@example.com")
    with engine.begin() as connection:
        change = _insert(connection, "milestone_changes", _change(milestone, actor))
        log = _insert(
            connection,
            "comm_logs",
            {
                "subject_type": "SHIPMENT",
                "subject_id": 1,
                "occurred_on": date(2026, 9, 1),
                "summary": "포워더에 롤오버 통보",
            },
        )
        notice = _insert(
            connection,
            "milestone_change_notices",
            {"change_id": change, "comm_log_id": log, "actor_user_id": actor},
        )
    for table, row_id, column in (
        ("milestone_changes", change, "reason"),
        ("milestone_change_notices", notice, "actor_user_id"),
    ):
        for sql in (
            f"UPDATE {table} SET {column} = {column} WHERE id = :i",
            f"DELETE FROM {table} WHERE id = :i",
            f"TRUNCATE {table}",
        ):
            with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
                connection.execute(text(sql), {"i": row_id})
            assert _state(caught.value)[0] == PERMISSION_DENIED, sql
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(
            connection,
            "milestone_change_notices",
            {"change_id": change, "comm_log_id": log, "actor_user_id": actor},
        )
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_milestone_change_notices_change_id_comm_log_id",
    )


def _comm_log(**override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "subject_type": "SHIPMENT",
        "subject_id": 1,
        "occurred_on": date(2026, 9, 1),
        "summary": "포워더 통보",
    }
    values.update(override)
    return values


@pytest.mark.parametrize(
    ("case", "override", "constraint"),
    [
        ("shipment_ideographic", {"summary": "　"}, "ck_comm_logs_summary_not_blank"),
        ("shipment_nbsp_em", {"summary": "  "}, "ck_comm_logs_summary_not_blank"),
        ("shipment_zero_width", {"summary": "​"}, "ck_comm_logs_summary_not_blank"),
        (
            "generic_ideographic",
            {"subject_type": "CERTIFICATION", "summary": "　"},
            "ck_comm_logs_summary_not_blank",
        ),
        (
            "shipment_before_2000",
            {"occurred_on": date(1999, 12, 31)},
            "ck_comm_logs_shipment_occurred_on_range",
        ),
    ],
)
def test_comm_log_summary_needs_a_visible_char_and_shipment_dates_stay_in_range(
    case: str, override: dict[str, Any], constraint: str
) -> None:
    """적대 검토 반영 ⑤·⑥ — comm_logs 요지는 보이는 글자 1개 이상(유니코드 공백만 = 거부, 선적 통보는 보이지 않는 글자만도 거부),
    선적 통보의 오간 날은 2000~2999(범용 주제 행은 대상 밖 — 기존 데이터 무접촉)"""
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        _insert(connection, "comm_logs", _comm_log(**override))
    assert _state(caught.value) == (CHECK_VIOLATION, constraint), case


def test_generic_comm_logs_keep_their_former_space_rule() -> None:
    """양성 대조 — 범용 주제는 기존 규칙과 호환(보이지 않는 서식 글자만의 요지·2000년 전 날짜는 기존 서비스 규약 밖이라 DB가 막지 않는다),
    선적 통보는 경계 날짜(2000-01-01)·여러 줄 요지가 저장된다"""
    with engine.begin() as connection:
        _insert(connection, "comm_logs", _comm_log(subject_type="CERTIFICATION", summary="​"))
        _insert(
            connection,
            "comm_logs",
            _comm_log(subject_type="CERTIFICATION", occurred_on=date(1999, 12, 31)),
        )
        _insert(
            connection, "comm_logs", _comm_log(occurred_on=date(2000, 1, 1), summary="1행\n2행")
        )


# ── item_profile_milestone_types ──────────────────────────────────────────────


@pytest.mark.group_j
def test_profile_milestone_sets_take_stored_shipment_types_only_and_reenter_as_new() -> None:
    """품목군 세트 — 선적 저장형 8종만(파생·OEM 거부), (품목군, 종류) 살아 있는 행 유일, soft delete 뒤 재유입 = 신규(J-13)"""
    profile = create_item_profile("PRF-MS")
    with owner_engine.begin() as connection:
        first = _insert(
            connection,
            "item_profile_milestone_types",
            {"profile_id": profile, "milestone_type": "PSI"},
        )
    for bad in ("FILLING", "LOADING_DEADLINE", "ETA "):
        with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
            _insert(
                connection,
                "item_profile_milestone_types",
                {"profile_id": profile, "milestone_type": bad},
            )
        assert _state(caught.value) == (
            CHECK_VIOLATION,
            "ck_item_profile_milestone_types_type_valid",
        ), bad
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(
            connection,
            "item_profile_milestone_types",
            {"profile_id": profile, "milestone_type": "PSI"},
        )
    assert _state(caught.value) == (
        UNIQUE_VIOLATION,
        "uq_item_profile_milestone_types_profile_type_active",
    )
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE item_profile_milestone_types SET deleted_at = now() WHERE id = :i"),
            {"i": first},
        )
        again = _insert(
            connection,
            "item_profile_milestone_types",
            {"profile_id": profile, "milestone_type": "PSI"},
        )
    assert again != first


# ── 정의문·식별자·번역표·downgrade 가드 ──────────────────────────────────────────


def _date_range(col: str) -> str:
    return f"(({col} IS NULL) OR (({col} >= '2000-01-01'::date) AND ({col} <= '2999-12-31'::date)))"


def _instant_range(col: str) -> str:
    return (
        f"(({col} IS NULL) OR (({col} >= '2000-01-01 00:00:00+00'::timestamp with time zone)"
        f" AND ({col} < '3000-01-01 00:00:00+00'::timestamp with time zone)))"
    )


@pytest.mark.group_k
def test_check_definitions_are_pinned() -> None:
    """정의문 고정(함정 ①·⑪) — 저장형 값 공간(파생 0)·신고수리 실적 NULL·OEM 소유 범위·comm_logs 주제(CERTIFICATION·SHIPMENT)"""
    stored = sorted(STORED_MILESTONES)
    expected = {
        "ck_milestones_type_valid": (
            "((milestone_type)::text = ANY ((ARRAY["
            + ", ".join(f"'{t}'::character varying" for t in stored)
            + "])::text[]))"
        ),
        "ck_milestones_customs_actual_from_records": (
            "(((milestone_type)::text <> 'CUSTOMS_CLEARED'::text)"
            " OR ((actual_on IS NULL) AND (actual_at IS NULL)))"
        ),
        "ck_milestones_owner_type_scope": (
            "(((milestone_type)::text = ANY ((ARRAY['FILLING'::character varying,"
            " 'OUTGOING_INSPECTION'::character varying, 'PACKING'::character varying,"
            " 'RAW_MATERIAL_READY'::character varying])::text[])) = (po_id IS NOT NULL))"
        ),
        "ck_comm_logs_subject_type_valid": (
            "((subject_type)::text = ANY ((ARRAY['CERTIFICATION'::character varying,"
            " 'SHIPMENT'::character varying])::text[]))"
        ),
        "ck_customs_records_accept_after_declare": (
            "((accepted_on IS NULL) OR (accepted_on >= declared_on))"
        ),
        # ── 적대 검토 반영 ④·⑤·⑥ ──
        "ck_customs_records_declaration_no_shape": (
            "((declaration_no)::text ~ '^[A-Z0-9][A-Z0-9/-]*$'::text)"
        ),
        "ck_customs_records_note_clean": (
            f"((note IS NULL) OR (((note)::text ~ '[^{BLANK_CHAR_CLASS}]'::text) AND"
            " (translate((note)::text, ((chr(9) || chr(10)) || chr(13)), ''::text)"
            " !~ '[[:cntrl:]]'::text)))"
        ),
        "ck_customs_records_date_range": (
            "(" + _date_range("declared_on") + " AND " + _date_range("accepted_on") + ")"
        ),
        "ck_milestones_value_range": (
            "("
            + " AND ".join(
                [
                    _date_range("planned_on"),
                    _date_range("actual_on"),
                    _instant_range("planned_at"),
                    _instant_range("actual_at"),
                ]
            )
            + ")"
        ),
        "ck_milestone_changes_reason_clean": (
            "((reason IS NULL) OR (((char_length(reason) >= 1) AND (char_length(reason) <= 500))"
            f" AND (reason ~ '[^{BLANK_CHAR_CLASS}]'::text) AND (reason !~ '[[:cntrl:]]'::text)))"
        ),
        "ck_comm_logs_summary_not_blank": (
            f"((summary ~ '[^{SPACE_CHAR_CLASS}]'::text) AND (((subject_type)::text <> 'SHIPMENT'::text)"
            f" OR (summary ~ '[^{BLANK_CHAR_CLASS}]'::text)))"
        ),
        "ck_comm_logs_shipment_occurred_on_range": (
            "(((subject_type)::text <> 'SHIPMENT'::text)"
            " OR ((occurred_on >= '2000-01-01'::date) AND (occurred_on <= '2999-12-31'::date)))"
        ),
    }
    # 마이그레이션은 앱 상수를 임포트하지 않고 같은 집합을 스스로 만든다 — 두 정의가 갈라지지 않음을 대사
    module = _m15()
    assert (module._SPACE, module._BLANK) == (SPACE_CHAR_CLASS, BLANK_CHAR_CLASS)
    assert (
        "\\u3000" in SPACE_CHAR_CLASS and "\\u200b" in BLANK_CHAR_CLASS
    )  # 이스케이프 문자열(보이지 않는 글자 직접 기입 0)
    with owner_engine.connect() as connection:
        found = {
            r[0]: r[1]
            for r in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = ANY(:n)"
                ),
                {"n": list(expected)},
            )
        }
    assert {
        name: found.get(name, "").removeprefix("CHECK (").removesuffix(")") for name in expected
    } == expected
    assert not set(DERIVED_MILESTONES) & set(stored)


@pytest.mark.group_k
def test_constraint_names_fit_postgres_identifier_limit() -> None:
    """식별자 63자 이내(잘린 이름은 마이그레이션이 지목하지 못한다) — 신규 5표의 제약·인덱스 전부"""
    with owner_engine.connect() as connection:
        names = [
            r[0]
            for r in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid::regclass::text = ANY(:t)"
                    " UNION ALL SELECT indexname FROM pg_indexes WHERE tablename = ANY(:t)"
                ),
                {"t": list(NEW_TABLES)},
            )
        ]
    assert len(names) > 40 and max(len(n) for n in names) <= 63


#: 입력(사람·경로)에서 값이 오는 열 — 이 열을 검사하는 CHECK는 서비스 검사를 빠져나간 값이 닿을 수 있으므로 번역표 대상이다(PR-3a J-09 패턴).
INPUT_COLUMNS: dict[str, tuple[str, ...]] = {
    "customs_records": ("declaration_kind", "declaration_no", "declared_on", "accepted_on", "note"),
    "milestones": ("milestone_type", "planned_on", "actual_on", "planned_at", "actual_at", "tz"),
    "milestone_changes": ("reason",),
    "milestone_change_notices": (),
    "item_profile_milestone_types": ("milestone_type",),
}


@pytest.mark.group_j
def test_the_translation_table_covers_every_unique_index_and_input_check_of_the_m15_tables() -> (
    None
):
    """J-09 — 번역표(MILESTONE_CONSTRAINT_ERRORS) = M15 5표의 유니크 인덱스(PK 제외) ∪ 입력 열 CHECK ∪ 선적 통보 요지 CHECK(comm_logs).
    빠진 이름도 죽은 이름도 없다(500 누수 0). 내부 불변식(소유자 하나·이력 값 쌍·무변경 금지)은 번역하지 않는다 — 결함은 500으로 드러난다"""
    with owner_engine.connect() as connection:
        uniques = {
            r[0]
            for r in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = ANY(:t)"
                    " AND indexdef LIKE 'CREATE UNIQUE%' AND indexname NOT LIKE 'pk_%'"
                ),
                {"t": list(NEW_TABLES)},
            )
        }
        input_checks: set[str] = set()
        for table, columns in INPUT_COLUMNS.items():
            if not columns:
                continue
            input_checks |= {
                r[0]
                for r in connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE contype = 'c'"
                        " AND conrelid::regclass::text = :t AND pg_get_constraintdef(oid) ~ :cols"
                    ),
                    {"t": table, "cols": r"\m(" + "|".join(columns) + r")\M"},
                )
            }
    assert {
        "ck_milestones_type_valid",
        "ck_milestones_customs_actual_from_records",
        "ck_milestones_value_range",
        "ck_customs_records_accept_after_declare",
        "ck_customs_records_date_range",
        "ck_milestone_changes_reason_required",
    } <= input_checks  # 공회전 방지 — 대상 CHECK를 실제로 찾는다
    assert "ck_milestones_one_owner" not in input_checks  # 내부 불변식은 입력 유래가 아니다
    names = (
        uniques
        | input_checks
        | {"ck_comm_logs_summary_not_blank", "ck_comm_logs_shipment_occurred_on_range"}
    )
    assert names == set(MILESTONE_CONSTRAINT_ERRORS), names ^ set(MILESTONE_CONSTRAINT_ERRORS)
    assert not set(MILESTONE_CONSTRAINT_ERRORS) & set(CONSTRAINT_ERRORS)  # 두 표는 겹치지 않는다


def _m15() -> Any:
    import importlib.util
    from pathlib import Path

    path = next(
        (Path(__file__).resolve().parents[2] / "migrations" / "versions").glob(
            "*_281da4794717_s32_milestones_customs.py"
        )
    )
    spec = importlib.util.spec_from_file_location("m15_s32_milestones_customs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.group_k
def test_m15_downgrade_refuses_to_drop_live_records(shipment: int) -> None:
    """M15 downgrade 가드 — 신규 5표에 행이 있거나 SHIPMENT 주제 통신 기록이 있으면 DDL 전에 RuntimeError(통관·일정·이력·통보 소실 방지).
    빈 상태는 통과. 실제 `downgrade()`를 롤백할 트랜잭션 안에서 불러 표가 그대로임을 확인한다(가드 제거 변이 = 표가 drop되어 실패)"""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _m15()
    with owner_engine.connect() as connection:
        module.refuse_lossy_downgrade(connection)  # 선적만 있고 통관·마일스톤 0 → 통과
    with owner_engine.begin() as connection:
        log = _insert(
            connection,
            "comm_logs",
            {
                "subject_type": "SHIPMENT",
                "subject_id": shipment,
                "occurred_on": date(2026, 9, 1),
                "summary": "통보",
            },
        )
    with owner_engine.connect() as connection, pytest.raises(RuntimeError, match="M15"):
        module.refuse_lossy_downgrade(connection)
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM comm_logs WHERE id = :i"), {"i": log})
        # 삭제된(soft delete) 통관 기록도 기록이다 — 상태와 무관하게 센다
        record = _insert(connection, "customs_records", _customs(shipment))
        connection.execute(
            text("UPDATE customs_records SET deleted_at = now() WHERE id = :i"), {"i": record}
        )
    with owner_engine.connect() as connection:
        tx = connection.begin()
        try:
            with (
                Operations.context(MigrationContext.configure(connection)),
                pytest.raises(RuntimeError, match="M15"),
            ):
                module.downgrade()
        finally:
            tx.rollback()
    with owner_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT to_regclass('customs_records')")).scalar_one()
            is not None
        )
        assert connection.execute(text("SELECT count(*) FROM customs_records")).scalar_one() == 1
