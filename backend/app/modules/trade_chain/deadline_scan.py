"""무역 기일 스캔 — 선적 마일스톤 4종 + 견적·PI 만료 임박 D-N (S3-2 PR-6 / ADR-0084 / design-B B13·B18 / design-C C11 / 통합 §9 R-20).

잡 `trade-deadline-scan`(daily@06:40 KST)과 CLI `trade-deadline-scan`의 본체다. **알림(alerts)을 만드는 것 말고는 아무것도 하지 않는다.**
4금 논증(ADR-0084): ① 전표 상태 무변경(전이·잠금·채번·확정 함수를 임포트하지 않는다 — 아키텍처 시험 고정) ② 법적 판정 0(사람이 입력한
날짜와 달력의 산술 비교) ③ 대외 발송 0(인앱 알림뿐 — 아웃박스 이벤트도 내지 않는다) ④ 장부·원장·발주 무접촉.

■ **대상(B13)** — 죽지 않은 선적(삭제 아님·CANCELLED 아님)의
    서류마감·Cargo Closing·수입 세금 납부기한 = 계획 있음 + 실적 없음(충족 신호 = 실적)
    적재기한(파생, 수출) = 기한 OK + 이행 OPEN/OVERDUE(충족 = ETD·B/L 실적 중 존재값의 MAX — R-10)
  **대금만기·제시기한은 알리지 않는다**(충족 신호 입금·제시가 S3-3 — 지금 알리면 이미 입금된 건에 도과 알림). L/C는 운영 경로에서
  산정 불가(UNKNOWN)라 더더욱 0이다. **OEM 생산 마일스톤도 대상이 아니다**(B15 '알림 없음' — 보드 조립이 선적 소유 행만 읽는다).
■ **판정은 화면과 같은 함수**(`milestone_view.assemble` — 유효값·`scan_date`·시각형 도과·TZ_UNRESOLVED·DATE_OUT_OF_RANGE·통관 MIN·적재
  이행 판정). 정의 이원화 금지 — 스캔이 따로 계산하면 화면의 'D-3'과 알림의 'D-3'이 갈린다.
    D-N 문턱 = 날짜형은 유효일, 시각형은 `scan_date`(min(현지, KST) — 이른 경고).
    **도과 = 시각형은 `now_utc > effective_at`**(R-20 — 날짜 비교면 기한 전 최대 ~16시간 '도과' 오표시), 날짜형은 `오늘(KST) > 날짜`.
  시각형 행의 시간대를 tzdata가 모르면(TZ_UNRESOLVED) D-N은 추정하지 않고 **'기일 판정 불가' 알림 1건**(키 종류 `deadline-unresolved:` —
  에스컬레이션 근거에서 분리)을 내며, **도과만큼은 시간대 없이 UTC 비교(`now > at_utc`)로 판정해 도과 알림**을 낸다(적대 검토 ②③).
■ **견적·PI(B18)** — 후보 = 만료 스윕과 같은 정의(`EXPIRY_CANDIDATE_STATUS`·삭제 아님·`is_lapsed` 아님·살아 있는 후속 없음).
  `valid_until` 당일까지 유효(D-0 포함), 경과분은 스윕이 EXPIRED로 닫으므로 도과 알림은 없다.
■ **의미론 = S2-3 승계**(`deadlines` 공용 함수 — `days_left`·`passed_thresholds`·`policy`·`has_unacknowledged_alert`):
    문턱은 '지났다'로 판정(지각 발송) · 도과 건에 지난 문턱 소급 없음 · D-3 이내 + 같은 종류·같은 기일의 기일 알림 중 **지금의 수신자가
    받았고 min(스캔일 KST 0시, now−24h) 이전에 만든** 미확인 → ADMIN 에스컬레이션(같은 날·24시간 안에 만든 알림은 '받을 틈이 없었다',
    담당 변경·비활성 담당자의 옛 알림은 새 수신자의 미확인이 아니다 — 자율 확정·적대 검토 ④⑦) · 수신자 = 담당자 → 규칙 → ADMIN 폴백(`Routing.DEADLINE`) · 문턱 = `alert_rules.config.thresholds`,
    규칙이 없으면 **D-7/3/1**.
■ **dedup = DB 부분 유니크**(`alerts.dedup_key`, `ON CONFLICT DO NOTHING` — 확인 후 INSERT 없음):
    `deadline:shipments:{선적 id}:{종류}/{문턱}@{기일}:{수신자}` · `deadline:quotations|proforma_invoices:{id}:VALIDITY/{문턱}@{valid_until}:{수신자}`
    `{문턱}` = `D-7`·`D-3`·`D-1`·`overdue`·`UNRESOLVED`, 에스컬레이션은 `esc:` 접두. **기일이 바뀌면(롤오버) 새 키 = 새 알림**, 옛 키는
    다시 나가지 않는다. 시각형 기일은 UTC 시각 `YYYY-MM-DDTHHMMSSZ`(같은 날 안의 시각 롤오버도 새 기일 — 자율 확정; 콜론 없음 — 키의 `:` 구분 규약 유지), 날짜형은 `YYYY-MM-DD`.
■ **실행 규율** — 한 실행의 기준 시각 1개(`now` → KST 오늘)로 전 건을 판정한다. 후보 id는 keyset 페이지(`page_size`)로 읽고 **건마다
  독립 트랜잭션**(§17.6 — 한 건 실패가 나머지를 막지 않는다, 롤백된 건의 알림은 집계하지 않는다). 열린 unit_of_work 안에서 부르면 거부한다
  (합류하면 건별 독립 커밋이 소리 없이 깨진다 — `purge_in_batches` 선례). 1건이라도 실패하면 호출자(스케줄러)가 잡을 FAILED로 올린다.
  **발송 직전 재확인**(잠금 없음): 같은 트랜잭션에서 캐시를 버리고 다시 판정해 담당자·실적·통관·계획이 바뀐 기일은 보내지 않고
  다음 실행에 맡긴다(`deferred` 집계 — 이관·실적 입력 경합, 적대 검토 ⑤⑥).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.core.db.uow import in_unit_of_work, unit_of_work
from app.core.logging import get_logger
from app.core.logging.redaction import scrub_text
from app.core.time import KST, utcnow
from app.modules.deadlines import service as deadlines
from app.modules.notifications import service as notifications
from app.modules.notifications.service import Routing
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.quotations.models import Quotation
from app.modules.shipments.models import CustomsRecord, Milestone, Shipment
from app.modules.trade_chain import milestone_view
from app.modules.trade_docs import schedule
from app.modules.trade_docs.chain import has_live_children
from app.modules.trade_docs.constants import (
    DATETIME_MILESTONES,
    DocKind,
    MilestoneType,
    ShipmentKind,
)
from app.modules.trade_docs.expiry import EXPIRY_CANDIDATE_STATUS, is_lapsed
from app.modules.trade_docs.machine import TERMINAL_STATUSES

logger = get_logger(__name__)

#: 기일 이벤트 종류 — `alert_rules.event_type` 값 공간(<도메인>.<대상>.<사건>). 규칙의 `config.thresholds`가 문턱을 덮어쓴다.
SHIPMENT_EVENT = "shipments.milestone.approaching"
QUOTATION_EVENT = "quotations.validity.approaching"
PROFORMA_EVENT = "proforma_invoices.validity.approaching"

#: 무역 기일 기본 문턱(design-B B13 — 인증·문서 축의 D-180/90/30은 선적에 부적합).
DEFAULT_THRESHOLDS: tuple[int, ...] = (7, 3, 1)

#: 저장형 3종(계획 있음·실적 없음이 대상) — 순서는 보드 순서와 무관하게 이 집합으로만 거른다.
STORED_SCAN_TYPES: frozenset[str] = frozenset(
    {
        MilestoneType.DOC_CUTOFF.value,
        MilestoneType.CARGO_CLOSING.value,
        MilestoneType.IMPORT_TAX_DUE.value,
    }
)
#: 스캔 대상 전부(저장형 3 + 파생 적재기한). 대금만기·제시기한·OEM 4종·ETD·ETA 등은 **여기 없다**(아키텍처 시험이 집합을 고정).
SCAN_TYPES: frozenset[str] = STORED_SCAN_TYPES | {MilestoneType.LOADING_DEADLINE.value}

#: 견적·PI 기일 세그먼트(dedup 키 `…:VALIDITY/{문턱}@{valid_until}`).
VALIDITY_SEGMENT = "VALIDITY"

#: 이 문턱 이하(D-3·D-1)와 도과·에스컬레이션·판정 불가는 긴급, 그 위(D-7)는 주의 — 등급은 표시 힌트이지 라우팅 축이 아니다.
CRITICAL_THRESHOLD_DAYS = deadlines.ESCALATION_DAYS

#: 후보 id keyset 페이지 크기(대량 대상에서 id 목록 전체를 한 번에 메모리에 올리지 않는다).
CANDIDATE_PAGE = 500

_LOADING_OPEN = frozenset({schedule.LoadingState.OPEN.value, schedule.LoadingState.OVERDUE.value})


@dataclass(frozen=True, slots=True)
class Due:
    """알림 판정 대상 기일 1개 — 선적 마일스톤 1종 또는 견적·PI 유효기간."""

    entity_type: str  # "shipments" | "quotations" | "proforma_invoices"
    entity_id: int
    segment: str  # 마일스톤 종류 코드 또는 VALIDITY
    stamp: str  # dedup 키의 기일(날짜형 YYYY-MM-DD, 시각형 UTC YYYY-MM-DDTHHMMSSZ)
    shown: str  # 본문에 보일 기일 문구(시각형은 KST 병기)
    remaining: int | None  # D-N(시각형은 scan_date 기준) — None = 시간대 해석 불가(도과만 판정)
    overdue: bool
    title: str  # 알림 제목 꼬리(전표 번호·종류명)
    assignee_id: int | None


@dataclass(frozen=True, slots=True)
class Unresolved:
    """시간대를 해석할 수 없는 시각형 기일 1개(판정 불가 알림 대상)."""

    milestone_type: str
    stamp: str
    shown: str  # KST 표기


#: 판정 불가 알림의 키 종류 — 기일 알림(`deadline:`)과 분리해 에스컬레이션 근거·문턱 키와 섞이지 않게 한다.
KIND_UNRESOLVED = "deadline-unresolved"

#: 에스컬레이션 근거 알림의 최소 경과 시간(스캔일 KST 0시 기준과 함께 — `escalation_cutoff`).
ESCALATION_MIN_AGE = timedelta(hours=24)


#: 알림 본문의 종류명(한국어 UI — 코드값 노출 금지).
_TYPE_NAME_KO: dict[str, str] = {
    MilestoneType.DOC_CUTOFF.value: "서류마감",
    MilestoneType.CARGO_CLOSING.value: "Cargo Closing",
    MilestoneType.IMPORT_TAX_DUE.value: "수입 세금 납부기한",
    MilestoneType.LOADING_DEADLINE.value: "적재기한",
    VALIDITY_SEGMENT: "유효기간",
}


# ── 키·순수 조립 ──────────────────────────────────────────────────────────────


def _key(kind: str, due: Due, tail: str) -> str:
    """`{kind}:{대상}:{id}:{종류}/{tail}` — 코어(`notify`)가 `:{수신자}`를 붙인다."""
    return f"{kind}:{due.entity_type}:{due.entity_id}:{due.segment}/{tail}"


def instant_stamp(value: datetime) -> str:
    """시각형 기일의 키 표기 — UTC 초 단위 `YYYY-MM-DDTHHMMSSZ`(시간대 표기 흔들림 없이 같은 시각 = 같은 키).

    앞 10자가 날짜형 키와 같은 `YYYY-MM-DD`(X-25 `@{YYYY-MM-DD}` 계보)이고, 콜론을 쓰지 않는다 — dedup 키는 `:`로 세그먼트를 가르는
    규약(`…:{문턱}@{기일}:{수신자}`)이라 기일 안에 `:`가 있으면 사람이·도구가 키를 읽을 때 수신자 경계가 흐려진다."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")


def _instant_shown(value: datetime, tz: str | None) -> str:
    kst = value.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    if tz is None:
        return f"{kst} KST"
    try:
        local = value.astimezone(schedule.zone(tz)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError):
        return f"{kst} KST"
    return f"{kst} KST(현지 {local} {tz})"


def _title(text: str) -> str:
    limit = deadlines.ALERT_TITLE_MAX
    return text if len(text) <= limit else text[: limit - 1] + "…"


def shipment_dues(
    board: dict[str, Any],
    *,
    shipment_id: int,
    doc_number: str,
    assignee_id: int | None,
    now: datetime,
) -> tuple[list[Due], list[Unresolved]]:
    """보드(화면과 같은 조립 결과) → (알림 판정 대상 기일, 판정 불가 행) — 순수 함수.

    보드 행의 `days_left`·`is_overdue`가 판정의 유일 원천이다(시각형 도과 = UTC 비교, D-N = scan_date — milestone_view가 계산).
    시간대를 해석할 수 없는 시각형 행(TZ_UNRESOLVED)은 D-N을 추정하지 않지만, **도과만큼은 시간대와 무관한 UTC 시각 비교**
    (`now > at_utc`)로 판정해 도과 기일로 낸다(적대 검토 ② — 판정 불가 알림만 내고 도과를 숨기지 않는다).
    """
    dues: list[Due] = []
    unresolved: list[Unresolved] = []
    for row in board["rows"]:
        milestone_type = row["milestone_type"]
        if milestone_type not in SCAN_TYPES or not row["applicable"]:
            continue
        name = _TYPE_NAME_KO[milestone_type]
        if milestone_type == MilestoneType.LOADING_DEADLINE.value:
            derived = row["derived"] or {}
            if derived.get("status") != schedule.DueStatus.OK.value:
                continue  # 수리 실적 없음(NOT_CLEARED)·범위 밖 — 기한 자체가 없다(B7)
            if row["fulfilment"] not in _LOADING_OPEN:
                continue  # 이행(MET·MET_LATE) — 충족 신호
            stamp = str(derived["value"])
            shown = stamp
        else:
            if row["actual"] is not None or row["planned"] is None:
                continue  # 실적 있음(충족) 또는 계획 없음(기일 없음)
            if milestone_type in DATETIME_MILESTONES:
                planned = row["planned"]
                instant = datetime.fromisoformat(planned["at_utc"])
                stamp = instant_stamp(instant)
                if row["unknown_reason"] == schedule.DueReason.TZ_UNRESOLVED.value:
                    kst_shown = _instant_shown(instant, None)
                    unresolved.append(Unresolved(milestone_type, stamp, kst_shown))
                    if (
                        now > instant
                    ):  # 도과는 UTC 비교라 시간대 없이도 확정이다 — D-N 문턱은 추정하지 않는다
                        dues.append(
                            Due(
                                entity_type="shipments",
                                entity_id=shipment_id,
                                segment=milestone_type,
                                stamp=stamp,
                                shown=kst_shown,
                                remaining=None,
                                overdue=True,
                                title=f"{doc_number} {name}",
                                assignee_id=assignee_id,
                            )
                        )
                    continue
                shown = _instant_shown(instant, planned.get("tz"))
            else:
                stamp = str(row["planned"])
                shown = stamp
        if row["days_left"] is None or row["is_overdue"] is None:
            # 위 조건이면 보드는 늘 판정을 낸다(CHECK가 시각형 tz를 강제) — 그래도 비면 조용히 건너뛰지 않고 이 건을 실패로 올린다(fail-visible)
            raise RuntimeError(f"보드가 기일 판정을 내지 않았습니다: {milestone_type}")
        dues.append(
            Due(
                entity_type="shipments",
                entity_id=shipment_id,
                segment=milestone_type,
                stamp=stamp,
                shown=shown,
                remaining=int(row["days_left"]),
                overdue=bool(row["is_overdue"]),
                title=f"{doc_number} {name}",
                assignee_id=assignee_id,
            )
        )
    return dues, unresolved


# ── 알림 1건 판정(S2-3 의미론) ────────────────────────────────────────────────


def escalation_cutoff(now: datetime, today: date) -> datetime:
    """에스컬레이션 근거가 되는 알림의 생성 시각 상한 = min(스캔일 KST 0시, now − 24시간).

    같은 날 앞선 실행(CLI 재실행)뿐 아니라 **전날 늦게 만든 알림**(미스파이어 수렴으로 잡이 밤늦게 돈 경우 등)도 '받을 틈이
    없었다'로 본다 — 최소 24시간 미확인이어야 관리자를 부른다(적대 검토 ⑦, ADR-0084 부기)."""
    day_start = datetime(today.year, today.month, today.day, tzinfo=KST).astimezone(UTC)
    return min(day_start, now - ESCALATION_MIN_AGE)


def _who(recipients: notifications.Recipients, assignee_id: int | None) -> str:
    if not recipients.fallback and recipients.user_ids == (assignee_id,):
        return "담당자가"
    return "수신자(규칙·관리자)가"


def _alert_due(
    session: Session, due: Due, policy: deadlines.Policy, *, escalate_before: datetime
) -> dict[str, int]:
    """에스컬레이션 → 도과 또는 지난 문턱. 반환 = 이 건이 **새로** 만든 알림 수(커밋 뒤 합산).

    에스컬레이션 근거 = 같은 종류·같은 기일의 기일 알림 중 ⓐ `escalate_before` 이전에 만들었고 ⓑ **지금 이 기일의 수신자**(담당자 →
    규칙 → ADMIN 폴백 — `resolve_recipients` 결과)가 받은 미확인 알림(적대 검토 ④ — 담당 변경·비활성 담당자의 옛 알림이 새 수신자를
    '안 읽었다'로 만들지 않는다). 판정 불가 알림은 키 종류가 달라(`deadline-unresolved:`) 근거가 아니다(적대 검토 ③)."""
    made = {"threshold": 0, "overdue": 0, "escalated": 0}
    label = deadlines.d_label(due.remaining) if due.remaining is not None else "도과"
    name = _TYPE_NAME_KO[due.segment]
    recipients = notifications.resolve_recipients(
        session, rule=policy.rule, assignee_id=due.assignee_id, routing=Routing.DEADLINE
    )

    # ① 에스컬레이션 — D-3 이내(도과 포함)이고 근거 알림이 남아 있을 때(이번 알림보다 먼저 판정).
    near = due.overdue or (due.remaining is not None and due.remaining <= deadlines.ESCALATION_DAYS)
    if (
        near
        and recipients.user_ids
        and deadlines.has_unacknowledged_alert(
            session,
            key_prefix=_key(deadlines.KIND_DEADLINE, due, ""),
            stamp=due.stamp,
            created_before=escalate_before,
            recipient_ids=recipients.user_ids,
        )
    ):
        made["escalated"] += len(
            notifications.notify(
                session,
                subject_key=_key(
                    deadlines.KIND_ESCALATION, due, f"D-{deadlines.ESCALATION_DAYS}@{due.stamp}"
                ),
                title=_title(f"미확인 무역 기일 에스컬레이션 — {due.title}"),
                body=(
                    f"{name} {due.shown}({label})까지 {deadlines.ESCALATION_DAYS}일 이내인데 "
                    f"{_who(recipients, due.assignee_id)} 기일 알림을 확인하지 않았습니다. 진행 상황을 확인해 주세요."
                ),
                severity="CRITICAL",
                routing=Routing.ADMIN,
                entity_type=due.entity_type,
                entity_id=due.entity_id,
            )
        )

    routed: dict[str, Any] = {
        "event_type": policy.event_type,
        "rule": policy.rule,
        "assignee_id": due.assignee_id,
        "routing": Routing.DEADLINE,
        "entity_type": due.entity_type,
        "entity_id": due.entity_id,
    }

    # ② 도과 — 지난 문턱을 소급 발송하지 않는다(도과 알림 1건이 그 상태의 전부 — S2-3 ②).
    if due.overdue:
        made["overdue"] += len(
            notifications.notify(
                session,
                subject_key=_key(deadlines.KIND_DEADLINE, due, f"overdue@{due.stamp}"),
                title=_title(f"무역 기일 도과 — {due.title}"),
                body=f"{name} {due.shown}이(가) 지났습니다({label}). 실적 입력 또는 일정 변경(롤오버)을 확인해 주세요.",
                severity="CRITICAL",
                **routed,
            )
        )
        return made
    if due.remaining is None:
        return made  # D-N을 모르는 기일에 문턱을 추정하지 않는다(도과 아님 — 판정 불가 알림만)

    # ③ 지난 문턱 전부(지각 발송 포함) — 이미 있는 키는 코어가 생략한다.
    for threshold in deadlines.passed_thresholds(due.remaining, policy.thresholds):
        mark = deadlines.threshold_label(threshold)
        made["threshold"] += len(
            notifications.notify(
                session,
                subject_key=_key(deadlines.KIND_DEADLINE, due, f"{mark}@{due.stamp}"),
                title=_title(f"무역 기일 {mark} — {due.title}"),
                body=f"{name} {due.shown}까지 {label}입니다({mark} 문턱). 준비 상황을 확인해 주세요.",
                severity="CRITICAL" if threshold <= CRITICAL_THRESHOLD_DAYS else "WARN",
                **routed,
            )
        )
    return made


def _alert_unresolved(
    session: Session,
    *,
    shipment_id: int,
    doc_number: str,
    assignee_id: int | None,
    item: Unresolved,
    policy: deadlines.Policy,
) -> int:
    """시각형 기일의 시간대를 해석할 수 없다 — D-N을 KST로 추정하지 않고 '판정 불가'를 1회 알린다(fail-visible).

    키 종류는 `deadline-unresolved:`(기일 알림 `deadline:`과 분리) — 에스컬레이션 근거·문턱 키와 섞이지 않는다(적대 검토 ③)."""
    name = _TYPE_NAME_KO[item.milestone_type]
    return len(
        notifications.notify(
            session,
            subject_key=f"{KIND_UNRESOLVED}:shipments:{shipment_id}:{item.milestone_type}/UNRESOLVED@{item.stamp}",
            title=_title(f"무역 기일 판정 불가 — {doc_number} {name}"),
            body=(
                f"{name}({item.shown})의 시간대를 해석할 수 없어 D-N을 판정하지 못했습니다(도과는 UTC 시각으로 따로 알립니다). "
                "선적 상세에서 시간대를 다시 입력해 주세요."
            ),
            severity="CRITICAL",
            event_type=policy.event_type,
            rule=policy.rule,
            assignee_id=assignee_id,
            routing=Routing.DEADLINE,
            entity_type="shipments",
            entity_id=shipment_id,
        )
    )


# ── 건별 작업(한 건 = 한 트랜잭션) ────────────────────────────────────────────


def _live_shipment(session: Session, shipment_id: int) -> Shipment | None:
    found: Shipment | None = session.execute(
        select(Shipment).where(
            Shipment.id == shipment_id,
            Shipment.deleted_at.is_(None),
            Shipment.status.not_in(tuple(TERMINAL_STATUSES[DocKind.SHIPMENT])),
        )
    ).scalar_one_or_none()
    return found


def _shipment_verdict(
    session: Session, shipment_id: int, today: date, now: datetime
) -> tuple[int | None, list[Due], list[Unresolved]] | None:
    """(담당자, 기일, 판정 불가) — 선적이 죽었으면 None."""
    row = _live_shipment(session, shipment_id)
    if row is None:
        return None
    board = milestone_view.assemble(session, row, today=today, now=now).board
    dues, unresolved = shipment_dues(
        board, shipment_id=row.id, doc_number=row.doc_number, assignee_id=row.assignee_id, now=now
    )
    return row.assignee_id, dues, unresolved


def _scan_shipment(
    session: Session, shipment_id: int, today: date, now: datetime
) -> dict[str, int]:
    first = _shipment_verdict(session, shipment_id, today, now)
    if first is None:  # 후보 수집 뒤 취소·삭제됐다 — 건너뛴다
        return {}
    assignee_id, dues, unresolved = first
    made: dict[str, int] = {"shipments": 1}
    if not dues and not unresolved:
        return made
    # 발송 직전 재확인(적대 검토 ⑤⑥ — 잠금 없음): 같은 TX에서 캐시를 버리고 선적·마일스톤·통관을 다시 읽어 판정을 다시 낸다.
    # 담당자가 바뀌었거나(이관 경합) 실적이 들어왔거나(실적 입력 경합) 통관·계획이 바뀌어 처음 판정과 다른 기일은 보내지 않고
    # 다음 실행에 맡긴다(READ COMMITTED — 새 문장은 그 사이 커밋을 본다).
    session.expire_all()
    second = _shipment_verdict(session, shipment_id, today, now)
    if second is None or second[0] != assignee_id:
        made["deferred"] = len(dues) + len(unresolved)
        return made
    fresh_dues, fresh_unresolved = set(second[1]), set(second[2])
    sendable = [due for due in dues if due in fresh_dues]
    sendable_unresolved = [item for item in unresolved if item in fresh_unresolved]
    deferred = len(dues) - len(sendable) + len(unresolved) - len(sendable_unresolved)
    if deferred:
        made["deferred"] = deferred
    row = _live_shipment(session, shipment_id)
    assert row is not None
    doc_number = row.doc_number
    policy = deadlines.policy(session, SHIPMENT_EVENT, defaults=DEFAULT_THRESHOLDS)
    cutoff = escalation_cutoff(now, today)
    for item in sendable_unresolved:
        made["unresolved"] = made.get("unresolved", 0) + _alert_unresolved(
            session,
            shipment_id=shipment_id,
            doc_number=doc_number,
            assignee_id=assignee_id,
            item=item,
            policy=policy,
        )
    for due in sendable:
        for key, value in _alert_due(session, due, policy, escalate_before=cutoff).items():
            made[key] = made.get(key, 0) + value
    return made


_VALIDITY_TARGETS: dict[str, tuple[DocKind, type[Quotation] | type[ProformaInvoice], str]] = {
    "quotations": (DocKind.QUOTATION, Quotation, QUOTATION_EVENT),
    "proforma_invoices": (DocKind.PROFORMA_INVOICE, ProformaInvoice, PROFORMA_EVENT),
}


def _validity_work(entity_type: str) -> Callable[[Session, int, date, datetime], dict[str, int]]:
    kind, model, event_type = _VALIDITY_TARGETS[entity_type]

    def verdict(session: Session, doc_id: int, today: date) -> Due | None:
        found = session.execute(
            select(model).where(
                model.id == doc_id,
                model.status == EXPIRY_CANDIDATE_STATUS,
                model.deleted_at.is_(None),
                model.valid_until.is_not(None),
            )
        ).scalar_one_or_none()
        row = cast(
            Quotation | ProformaInvoice | None, found
        )  # 두 모델 합집합 select — mypy가 Base로 좁힌다
        if row is None or row.valid_until is None:
            return None
        # 만료 스윕과 같은 술어 — 경과분은 스윕이 닫는다(도과 알림 없음), 살아 있는 후속이 부모를 붙잡으면 알림도 없다(X-18)
        if is_lapsed(kind, row.status, row.valid_until, today):
            return None
        if has_live_children(session, kind, row.id):
            return None
        return Due(
            entity_type=entity_type,
            entity_id=row.id,
            segment=VALIDITY_SEGMENT,
            stamp=row.valid_until.isoformat(),
            shown=f"{row.valid_until.isoformat()}(당일 KST 24:00까지 유효)",
            remaining=deadlines.days_left(row.valid_until, today),
            overdue=False,
            title=f"{row.doc_number} 유효기간",
            assignee_id=row.assignee_id,
        )

    def work(session: Session, doc_id: int, today: date, now: datetime) -> dict[str, int]:
        due = verdict(session, doc_id, today)
        if due is None:
            return {}
        made: dict[str, int] = {entity_type: 1}
        session.expire_all()  # 발송 직전 재확인(담당 이관·후속 생성·유효기간 정정 경합 — 다르면 다음 실행에)
        if verdict(session, doc_id, today) != due:
            made["deferred"] = 1
            return made
        policy = deadlines.policy(session, event_type, defaults=DEFAULT_THRESHOLDS)
        cutoff = escalation_cutoff(now, today)
        for key, value in _alert_due(session, due, policy, escalate_before=cutoff).items():
            made[key] = made.get(key, 0) + value
        return made

    work.__name__ = f"_scan_{entity_type}"
    return work


# ── 후보(keyset 페이지) ──────────────────────────────────────────────────────


def _shipment_candidate_page(session: Session, after: int, limit: int) -> list[int]:
    """기일 후보 선적 id(상위 집합) — 살아 있는 선적 중 ① 대상 저장형 행에 계획 있음·실적 없음 또는 ② 수출이면서 수리된 통관 기록 있음.

    최종 판정은 건별 트랜잭션의 보드 조립이 한다(여기는 거르기만 — 판정 규칙을 두 벌 두지 않는다).
    """
    open_stored = exists().where(
        Milestone.shipment_id == Shipment.id,
        Milestone.deleted_at.is_(None),
        Milestone.milestone_type.in_(sorted(STORED_SCAN_TYPES)),
        Milestone.actual_on.is_(None),
        Milestone.actual_at.is_(None),
        or_(Milestone.planned_on.is_not(None), Milestone.planned_at.is_not(None)),
    )
    cleared_export = (Shipment.shipment_kind == ShipmentKind.EXPORT.value) & exists().where(
        CustomsRecord.shipment_id == Shipment.id,
        CustomsRecord.deleted_at.is_(None),
        CustomsRecord.accepted_on.is_not(None),
    )
    return list(
        session.execute(
            select(Shipment.id)
            .where(
                Shipment.id > after,
                Shipment.deleted_at.is_(None),
                Shipment.status.not_in(tuple(TERMINAL_STATUSES[DocKind.SHIPMENT])),
                or_(open_stored, cleared_export),
            )
            .order_by(Shipment.id)
            .limit(limit)
        ).scalars()
    )


def _validity_candidate_page(
    model: type[Quotation] | type[ProformaInvoice], today: date
) -> Callable[[Session, int, int], list[int]]:
    def page(session: Session, after: int, limit: int) -> list[int]:
        # 만료 스윕 후보(`valid_until < today`)의 여집합 — 같은 상태·삭제 조건(EXPIRY_CANDIDATE_STATUS), 후속 검사는 건별(has_live_children)
        return list(
            session.execute(
                select(model.id)
                .where(
                    model.id > after,
                    model.status == EXPIRY_CANDIDATE_STATUS,
                    model.deleted_at.is_(None),
                    model.valid_until.is_not(None),
                    model.valid_until >= today,
                )
                .order_by(model.id)
                .limit(limit)
            ).scalars()
        )

    return page


def _candidates(
    page: Callable[[Session, int, int], list[int]], page_size: int
) -> Iterator[list[int]]:
    """keyset 페이지 — 페이지마다 짧은 읽기 트랜잭션(건별 트랜잭션과 겹치지 않는다)."""
    after = 0
    while True:
        with unit_of_work() as uow:
            ids = page(uow.session, after, page_size)
        if not ids:
            return
        yield ids
        if len(ids) < page_size:
            return
        after = ids[-1]


# ── 진입점 ───────────────────────────────────────────────────────────────────


def _scan_one(
    work: Callable[[Session, int, date, datetime], dict[str, int]],
    entity_id: int,
    *,
    today: date,
    now: datetime,
    counts: dict[str, int],
) -> None:
    """한 건 = 한 트랜잭션. 실패는 그 건만 롤백·집계하고 마스킹 로그를 남긴다(§17.6). 집계는 커밋 뒤 합산."""
    try:
        with unit_of_work() as uow:
            made = work(uow.session, entity_id, today, now)
    except Exception as error:
        counts["failed"] += 1
        logger.error(
            "trade_deadline_scan_failed",
            work=getattr(work, "__name__", "work"),
            entity_id=entity_id,
            error=scrub_text(f"{type(error).__name__}: {error}")[:500],
        )
        return
    for key, value in made.items():
        counts[key] += value


def scan_trade_deadlines(
    *, now: datetime | None = None, page_size: int = CANDIDATE_PAGE
) -> dict[str, int]:
    """선적 마일스톤 4종 + 견적·PI 만료 임박 기일 알림을 만든다(멱등 — 같은 시각 재실행 신규 0).

    Returns:
        {"shipments", "quotations", "proforma_invoices"(스캔한 건수), "threshold", "overdue", "escalated", "unresolved"
        (**새로** 만든 알림 수 — dedup 생략분 제외), "deferred"(발송 직전 재확인에서 바뀌어 다음 실행에 맡긴 기일 수),
        "failed"(건별 트랜잭션 실패 수)}.
    """
    if page_size < 1:
        raise ValueError("page_size는 1 이상이어야 합니다.")
    if in_unit_of_work():
        raise RuntimeError(
            "scan_trade_deadlines는 열린 unit_of_work 밖에서만 호출할 수 있습니다(건별 독립 트랜잭션)."
        )
    moment = now if now is not None else utcnow()
    today = moment.astimezone(KST).date()
    counts = {
        "shipments": 0,
        "quotations": 0,
        "proforma_invoices": 0,
        "threshold": 0,
        "overdue": 0,
        "escalated": 0,
        "unresolved": 0,
        "deferred": 0,
        "failed": 0,
    }
    for ids in _candidates(_shipment_candidate_page, page_size):
        for shipment_id in ids:
            _scan_one(_scan_shipment, shipment_id, today=today, now=moment, counts=counts)
    for entity_type, (_kind, model, _event) in _VALIDITY_TARGETS.items():
        work = _validity_work(entity_type)
        for ids in _candidates(_validity_candidate_page(model, today), page_size):
            for doc_id in ids:
                _scan_one(work, doc_id, today=today, now=moment, counts=counts)
    return counts
