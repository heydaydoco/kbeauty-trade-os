"""대행사 스코어카드 — 소요일·보완율·비용(현행 계약) (§5.4 / s2-4-plan.md §2 안건 ③ — S2-4 PR-1).

★ **저장하지 않고 계산한다** — 집계 테이블·컬럼·마이그레이션·이벤트 발행 0(매트릭스와 같은
  계보, ADR-0047). 원료는 상태 이력(불변)과 인증 본체·계약 대장이라, 정정이 즉시 반영되고
  스캔·배치에 종속되지 않는다. 아키텍처 테스트가 소스 스캔으로 저장 금지를 고정한다.

■ 정의 (전건 계획 §5 자율 확정 5 — 화면·API가 같은 문구를 보인다: SCORECARD_NOTE)

  · 귀속 = **현재 대행사**(처리방식 AGENCY·agency_partner_id 기준) — 대행사 교체 이력은 남기지
    않는다(관찰 등재). 삭제되지 않은 인증 전건(종결 포함)이 담당 건수다.
  · 소요일 = 첫 서류준비 진입일 → 첫 승인 진입일의 KST 날짜 차(평균·중앙값). **승인에 도달한
    건만** 표본이다. 갱신 재승인은 첫 승인 이후라 표본에 영향이 없다.
  · 보완율 = 보완요청을 한 번이라도 거친 건 ÷ 신청제출에 도달한 건. 분모 0이면 값이 없다(None —
    0%로 위장하지 않는다). 재신청 루프가 있어도 건당 1로 센다.
  · 비용 = 그 대행사의 **현행 계약**(오늘 KST가 기간 안, 겹치면 시작일이 늦은 것)의 번호·수수료·
    통화·기간 — 합산·환산이 없다(통화 혼재 가능·환율 축 없음). 실비는 S3-4 비용 원장 도래 후
    연결한다(S2-2 판정 "인스턴스 비용 컬럼 0" 유지).

■ 질의 수는 대행사 수와 무관하다(§18.4): 대행사 페이지 1 + 건수 1 + 이력 집계 1 + 현행 계약 1.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import ColumnElement, func, or_, select

from app.core.db.uow import unit_of_work
from app.core.time import to_kst, today_kst
from app.modules.certifications.models import Certification, CertificationStatusLog
from app.modules.collaboration.models import AgencyContract
from app.modules.partners.models import Partner, PartnerTypeLink

#: 화면·API가 함께 보이는 정의 문구 — 서버가 정본이다(화면에 복사본을 두지 않는다).
SCORECARD_NOTE = (
    "대행사별 담당 건은 현재 지정된 대행사 기준입니다(교체 이력은 남기지 않습니다). "
    "소요일은 첫 서류준비 진입일부터 첫 승인 진입일까지(승인에 도달한 건만), "
    "보완율은 보완요청을 거친 건 ÷ 신청제출에 도달한 건입니다. "
    "비용은 현행 계약의 수수료를 그대로 보이며 합산·환산하지 않습니다 — 실비 집계는 비용 원장 도입 후 연결됩니다."
)


@dataclass(frozen=True, slots=True)
class CurrentContractView:
    id: int
    contract_no: str
    start_on: date
    end_on: date | None
    fee_amount: int | None
    fee_currency: str | None
    scope_note: str | None


@dataclass(frozen=True, slots=True)
class AgencyScoreView:
    partner_id: int
    partner_code: str
    partner_name: str
    case_count: int
    submitted_count: int
    approved_count: int
    supplemented_count: int
    #: 보완요청을 거친 건 ÷ 신청제출 도달 건 — 분모 0이면 None(0%로 위장 금지).
    supplement_rate: float | None
    #: 소요일 표본 수(승인에 도달한 건) — 평균·중앙값의 근거 건수를 함께 보인다.
    lead_sample_count: int
    lead_days_avg: float | None
    lead_days_median: float | None
    current_contract: CurrentContractView | None


def lead_days(prepared_at: datetime, approved_at: datetime) -> int:
    """첫 서류준비 진입 → 첫 승인 진입의 KST 날짜 차(일). 같은 날이면 0."""
    return (to_kst(approved_at).date() - to_kst(prepared_at).date()).days


def _agency_partner_ids() -> ColumnElement[bool]:
    """스코어카드에 오르는 대행사 = 인증대행 유형 거래처 ∪ 지정된 인증이 있는 거래처(유형이 뒤에
    해제돼도 실적이 있는 대행사는 사라지지 않는다)."""
    typed = select(PartnerTypeLink.partner_id).where(
        PartnerTypeLink.type_code == "CERT_AGENCY", PartnerTypeLink.deleted_at.is_(None)
    )
    used = select(Certification.agency_partner_id).where(
        Certification.deleted_at.is_(None), Certification.agency_partner_id.is_not(None)
    )
    return or_(Partner.id.in_(typed), Partner.id.in_(used))


def scorecard(*, offset: int, limit: int) -> tuple[list[AgencyScoreView], int]:
    today = today_kst()
    with unit_of_work() as uow:
        session = uow.session
        conditions = (Partner.deleted_at.is_(None), _agency_partner_ids())
        total = session.execute(
            select(func.count()).select_from(Partner).where(*conditions)
        ).scalar_one()
        partners = session.execute(
            select(Partner.id, Partner.partner_code, Partner.name_ko)
            .where(*conditions)
            .order_by(Partner.name_ko, Partner.id)
            .offset(offset)
            .limit(limit)
        ).all()
        partner_ids = [row.id for row in partners]

        # 건별 이력 집계 — 인증 1건 = 1행(LEFT JOIN이라 이력 없는 건도 담당 건수에 든다).
        per_case: dict[int, list[tuple[bool, bool, datetime | None, datetime | None]]] = {}
        if partner_ids:
            rows = session.execute(
                select(
                    Certification.agency_partner_id,
                    func.bool_or(CertificationStatusLog.to_status == "SUBMITTED"),
                    func.bool_or(CertificationStatusLog.to_status == "SUPPLEMENTING"),
                    func.min(CertificationStatusLog.occurred_at).filter(
                        CertificationStatusLog.to_status == "PREPARING"
                    ),
                    func.min(CertificationStatusLog.occurred_at).filter(
                        CertificationStatusLog.to_status == "APPROVED"
                    ),
                )
                .select_from(Certification)
                .outerjoin(
                    CertificationStatusLog,
                    CertificationStatusLog.certification_id == Certification.id,
                )
                .where(
                    Certification.deleted_at.is_(None),
                    Certification.agency_partner_id.in_(partner_ids),
                )
                .group_by(Certification.id, Certification.agency_partner_id)
            ).all()
            for partner_id, submitted, supplemented, prepared_at, approved_at in rows:
                per_case.setdefault(partner_id, []).append(
                    (bool(submitted), bool(supplemented), prepared_at, approved_at)
                )

        # 현행 계약 — 오늘 기간 안, 겹치면 시작일이 늦은 것(같으면 id가 큰 것).
        contracts: dict[int, CurrentContractView] = {}
        if partner_ids:
            for row in session.execute(
                select(AgencyContract)
                .where(
                    AgencyContract.deleted_at.is_(None),
                    AgencyContract.partner_id.in_(partner_ids),
                    AgencyContract.start_on <= today,
                    or_(AgencyContract.end_on.is_(None), AgencyContract.end_on >= today),
                )
                .order_by(AgencyContract.start_on.desc(), AgencyContract.id.desc())
            ).scalars():
                contracts.setdefault(
                    row.partner_id,
                    CurrentContractView(
                        id=row.id,
                        contract_no=row.contract_no,
                        start_on=row.start_on,
                        end_on=row.end_on,
                        fee_amount=row.fee_amount,
                        fee_currency=row.fee_currency,
                        scope_note=row.scope_note,
                    ),
                )

        views: list[AgencyScoreView] = []
        for partner in partners:
            cases = per_case.get(partner.id, [])
            submitted = sum(1 for case in cases if case[0])
            supplemented = sum(1 for case in cases if case[1])
            durations = [
                lead_days(prepared_at, approved_at)
                for _, _, prepared_at, approved_at in cases
                if prepared_at is not None and approved_at is not None
            ]
            views.append(
                AgencyScoreView(
                    partner_id=partner.id,
                    partner_code=partner.partner_code,
                    partner_name=partner.name_ko,
                    case_count=len(cases),
                    submitted_count=submitted,
                    approved_count=sum(1 for case in cases if case[3] is not None),
                    supplemented_count=supplemented,
                    supplement_rate=round(supplemented / submitted, 4) if submitted else None,
                    lead_sample_count=len(durations),
                    lead_days_avg=round(statistics.fmean(durations), 1) if durations else None,
                    lead_days_median=float(statistics.median(durations)) if durations else None,
                    current_contract=contracts.get(partner.id),
                )
            )
        return views, int(total)
