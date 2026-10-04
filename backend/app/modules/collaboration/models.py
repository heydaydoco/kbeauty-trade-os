"""대행 협업 — 대행 계약·통신 기록 (DESIGN.md §5.4 / s2-4-plan.md §2 안건 ① — S2-4 PR-1).

■ 이 모듈이 담는 것과 담지 않는 것

  §5.4 "대행 협업(파트너 허브 공통 패턴)"의 데이터 몫이다 — 처리방식·현재 액션
  주체·대행사 지정 컬럼은 인증 본체에 있고(certifications), 여기는 **대행 계약**
  (agency_contracts)과 **커뮤니케이션 기록**(comm_logs)이다. 이 패턴은 포워더·
  관세사에도 동일 적용된다(§7.9) — 그래서 모듈 이름이 agencies가 아니라
  collaboration이고 comm_logs가 폴리모픽이다. 이번 세션이 소비하는 주제는
  CERTIFICATION 1종이고, 포워더·관세사 주제는 그 소비 세션(P3·P4)이 CHECK 확장
  마이그레이션으로 추가한다(ADR-0028 "열거는 소비분 한정" 규율 — 검증할 수 없는
  값을 미리 넣지 않는다).

■ 계약에는 상태 컬럼이 없다 (계산값 미저장)

  계약이 지금 유효한가는 (시작일 ≤ 오늘 ≤ 종료일)의 순수 함수다 — 저장하면
  자정 이후 거짓이 된다(매트릭스와 같은 이유, ADR-0047). 종료일 NULL=기간 미정.

■ comm_logs — 일어난 일의 기록이지 발송이 아니다 (§5.4 "생성까지 시스템, 발송은 사람")

  요지·다음 액션·기한을 사람이 적는다. 다음 액션의 기한이 오늘이거나 지났는데
  완료되지 않았으면 stagnation-scan이 독촉 알림을 만든다(발송 없음 — 알림센터 한정).
  첨부는 documents 소유 열거 COMM_LOG로 붙는다(폴리모픽 소유 — ADR-0028).

■ 마스킹 비대상 — 계약 수수료는 원가·마진이 아니다 (여신한도 선례 — ADR-0026 계보)
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)

#: 통신 기록의 주제 유형 — 소비분 한정 확장(위 독스트링). 폭 20은 후속 주제 여유다.
#: S3-2 PR-4a(M15·ADR-0083): SHIPMENT = 선적 마일스톤 롤오버 통보 기록 — **선적 전용 통로(M6)만** 만든다.
COMM_SUBJECT_TYPES = ("CERTIFICATION", "SHIPMENT")
#: 범용 `/comm-logs`가 다루는 주제(쓰기·목록·id 접근·문서 첨부) — SHIPMENT는 범위 밖이다(R-05: POST 스키마 422·목록 기본 제외·
#: id 접근 404·COMM_LOG 첨부 거부). 선적 통보는 선적 권한·화면(`/shipments/{id}/milestone-changes…`)으로만 오간다.
GENERIC_COMM_SUBJECT_TYPES = ("CERTIFICATION",)


class AgencyContract(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """대행 계약 (§5.4 "대행 계약") — 대행사(거래처 유형 CERT_AGENCY)와의 계약 대장."""

    __tablename__ = "agency_contracts"

    partner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    #: 계약 번호 — 사람이 정한다(같은 대행사 안에서 유일). 채번 대상이 아니다.
    contract_no: Mapped[str] = mapped_column(String(60), nullable=False)
    #: 계약 범위·수수료 기준 설명(건당·정액 등은 문장으로 — 합산 의미가 없는 값이다).
    scope_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL = 기간 미정(상시 계약).
    end_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 수수료 — 금액 규약(정수 최소단위+통화 쌍, ADR-0003 ④). NULL = 미기재.
    fee_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("length(btrim(contract_no)) > 0", name="contract_no_not_blank"),
        CheckConstraint("end_on IS NULL OR end_on >= start_on", name="period_order"),
        # 금액과 통화는 한 쌍이다(partners.credit_limit_pair와 같은 꼴).
        CheckConstraint(
            "(fee_amount IS NULL AND fee_currency IS NULL)"
            " OR (fee_amount IS NOT NULL AND fee_currency IS NOT NULL)",
            name="fee_pair",
        ),
        CheckConstraint("fee_amount >= 0", name="fee_amount_nonnegative"),
        CheckConstraint("fee_currency = upper(fee_currency)", name="fee_currency_uppercase"),
        unique_active("agency_contracts", "partner_id", "contract_no"),
    )


class CommLog(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """커뮤니케이션 기록 (§5.4 comm_logs — 요지·첨부·다음 액션·기한)."""

    __tablename__ = "comm_logs"

    #: 폴리모픽 주제 — FK가 불가능해 CHECK+서비스 실재 검증이다(documents.owner와 같은 방식).
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 상대 거래처(대행사·포워더 등). 기관 담당자 등 거래처가 아닌 상대는 요지에 적는다.
    partner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=True
    )
    #: 실제로 오간 날(KST 업무일) — 입력일(created_at)과 다를 수 있다(소급 입력 — §21 날짜 3종).
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_action_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: 다음 액션을 끝낸 날 — NULL이면 미완료. 값이 있으면 독촉 대상에서 빠진다.
    next_action_done_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        value_in("subject_type", COMM_SUBJECT_TYPES),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_not_blank"),
        CheckConstraint(
            "next_action IS NULL OR length(btrim(next_action)) > 0", name="next_action_not_blank"
        ),
        # 기한·완료일은 다음 액션이 있을 때만 의미가 있다 — 주인 없는 날짜 금지.
        CheckConstraint(
            "next_action IS NOT NULL OR (next_action_due IS NULL AND next_action_done_on IS NULL)",
            name="follow_up_requires_action",
        ),
        CheckConstraint(
            "next_action_due IS NULL OR next_action_due >= occurred_on", name="due_after_occurred"
        ),
        CheckConstraint(
            "next_action_done_on IS NULL OR next_action_done_on >= occurred_on",
            name="done_after_occurred",
        ),
        Index("ix_comm_logs_subject_type_subject_id", "subject_type", "subject_id"),
    )
