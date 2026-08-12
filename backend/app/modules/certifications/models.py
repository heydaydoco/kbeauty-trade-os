"""인증 인스턴스 3테이블 (DESIGN.md §5.1·§5.2 / s2-2-plan.md §2 — S2-2 PR-1).

■ 대상은 폴리모픽이다 (판정 안건 ② — ADR-0028 계보)

  target_type 5값은 템플릿 적용단위와 같은 열거이고, 생성 시 서비스가 템플릿
  applies_to와의 일치를 강제한다. 폴리모픽이라 실 FK는 걸 수 없다 — 존재
  검증은 서비스 몫이다. 매핑: PRODUCT→products / SKU→skus /
  INGREDIENT→ingredients / FACILITY→partners(**잠정** — 복수 공장 실수요 시
  facilities 마스터 재판정, 관찰 원장 등재) / COMPANY→자사(target_id NULL —
  §4.6 파트너 유형에 자사가 없다. CHECK가 양방향으로 강제).

■ 스냅샷 7필드 — "그때 요건"의 동결 (ADR-0033 예약분)

  인스턴스 생성 시 CONFIRMED 템플릿에서 복사하고, 이후 템플릿 개정은
  소급되지 않는다(테스트 고정). estimated_cost는 **명시 예외**로 스냅샷에
  없다 — 실비는 S3-4 비용 원장, 예상비 최신값은 템플릿 참조(중복 보관 금지).

■ 활성 한정 유니크 — 재도전은 신규 인스턴스다 (판정 안건 ①)

  술어가 deleted_at IS NULL에 더해 종결 2태(REJECTED·SUSPENDED)를 제외한다 —
  종결 행이 유니크 밖이라 재도전 신규 등록이 항상 열리고, 만료(EXPIRED)는
  **포함**이라 만료 후 재개는 기존 행(만료→갱신중) 경유로 단일화된다.
  COMPANY는 target_id가 NULL이고 PG 유니크는 NULL을 구별 취급하므로
  전용 부분 유니크를 분리해 건다.

■ certification_status_log는 불변 이력이다 (판정 안건 ④ — §17.5 확장)

  audit_log 선례 그대로 믹스인을 대부분 쓰지 않고(불변 테이블에 updated_at·
  deleted_at·version은 모순), IMMUTABLE_TABLES 등재+revoke_mutations()로
  앱 계정의 UPDATE/DELETE를 DB가 거부한다. 정정은 새 전이 기록이다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import positive, unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.certifications.machine import CERTIFICATION_STATUSES
from app.modules.requirements.models import APPLIES_TO_VALUES

#: 대상 유형 = 템플릿 적용단위와 같은 열거 (§5.1 — 생성 시 일치 강제).
TARGET_TYPES = APPLIES_TO_VALUES

#: 활성 유니크가 제외하는 종결 2태 — machine.TERMINAL_STATUSES와 같은 값이다.
#: (모델 층은 문자열 술어가 필요해 여기 다시 적고, 두 정의의 일치는
#: tests/architecture/test_certification_machine.py가 고정한다.)
_OPEN_PREDICATE = "deleted_at IS NULL AND status NOT IN ('REJECTED', 'SUSPENDED')"


class Certification(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """인증 인스턴스 — 대상×템플릿 (§5.1 3층 모델의 2층)."""

    __tablename__ = "certifications"

    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("requirement_templates.id", ondelete="RESTRICT"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)
    #: COMPANY(자사 단일)만 NULL — CHECK company_target_null이 양방향 강제.
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(13), nullable=False, server_default="NOT_STARTED")

    # ── 스냅샷 7필드 (생성 시 CONFIRMED 템플릿에서 복사 — 개정 비소급) ──
    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    validity_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    renewal_cycle_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 만료임박 리드 — 부재 시 임박 단계 생략(임의 기본값 발명 금지, s2-2-plan §3).
    renewal_lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ── 인스턴스 실적 (전이 부속 데이터로 기록) ──
    #: §14 전역 검색이 "인증번호"를 열거한다 — 검색 소비는 후속 세션.
    cert_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    applied_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: NULL=무기한 인증 — 날짜 스윕 비대상.
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: 담당자 (§2 "담당 건(전표·인증·태스크·알림) 일괄 재배정" — handover 등재).
    assignee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        value_in("status", CERTIFICATION_STATUSES),
        value_in("target_type", TARGET_TYPES),
        # COMPANY=자사 단일이라 target_id 없음, 그 외는 반드시 있음 — 양방향.
        CheckConstraint(
            "(target_type = 'COMPANY') = (target_id IS NULL)",
            name="company_target_null",
        ),
        # 기간 순서 (§17.5 "가능한 불변식은 CHECK").
        CheckConstraint(
            "valid_from IS NULL OR expires_on IS NULL OR valid_from <= expires_on",
            name="period_order",
        ),
        positive("validity_months"),
        positive("renewal_cycle_months"),
        positive("renewal_lead_days"),
        CheckConstraint(
            "source_url IS NULL OR length(btrim(source_url)) > 0",
            name="source_url_not_blank",
        ),
        CheckConstraint(
            "cert_number IS NULL OR length(btrim(cert_number)) > 0",
            name="cert_number_not_blank",
        ),
        # 활성 중복 차단 — 종결 2태 제외·만료 포함 (판정 안건 ① 술어 전문).
        # unique_active()는 deleted_at 술어만 지원해 수기 Index다(선례:
        # uq_template_prerequisites_pair_active의 수기 사정과 동류).
        Index(
            "uq_certifications_template_target_open",
            "template_id",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text(_OPEN_PREDICATE),
        ),
        # COMPANY 전용 — target_id NULL은 위 유니크가 못 잡는다(PG NULL 구별).
        Index(
            "uq_certifications_company_open",
            "template_id",
            unique=True,
            postgresql_where=text(f"target_type = 'COMPANY' AND {_OPEN_PREDICATE}"),
        ),
    )


class CertificationTask(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """인스턴스 체크리스트 (§5.1 "체크리스트, 서류 링크" — ADR-11 "태스크는 자유").

    생성 시 template_checklist에서 3필드(item_name·document_type_id·
    is_required)를 참조 복사하고(ADR-05), 이후 인스턴스별 추가·편집·soft
    delete가 자유다(§4.8 "아이템별 프로세스 변형 흡수"). document_id는 공용
    문서 참조다(§5.6 — CFS·CoA는 SKU 소유 문서를 여러 태스크가 참조한다).
    """

    __tablename__ = "certification_tasks"

    certification_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("certifications.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    document_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=True
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 서류 링크 — 실물 연결(§5.1). 서류 링크 조작 화면·documents 소유 확장은 PR-2.
    document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        positive("seq"),
        # 체크 여부와 체크 시각은 한 쌍이다 — 어긋난 행은 "언제 했는지 모르는
        # 완료"거나 "완료 아닌데 시각만 있는" 모순이다.
        CheckConstraint(
            "(done AND done_at IS NOT NULL) OR ((NOT done) AND done_at IS NULL)",
            name="done_pair",
        ),
        unique_active("certification_tasks", "certification_id", "seq"),
    )


class CertificationStatusLog(PkMixin, Base):
    """상태 변경 이력 — 불변 (§5.2 "이력 자동" / §3 "상태 변경 이력" / 판정 ④).

    audit_log 선례: TimestampMixin(updated_at)·SoftDeleteMixin·VersionMixin·
    ActorMixin을 쓰지 않는다 — 불변 테이블에 "고칠 수 있다"는 신호를 두지
    않는다. 행위자는 actor_user_id 하나이며 NULL=시스템(자동 전이 — cli
    create-admin "행위자 NULL" 계보).

    스냅샷 2필드: 갱신 재승인(갱신중→승인)이 본체의 만료일·인증번호를
    덮어쓰므로, "그 시점에 어느 만료일의 인증이 유효했나"는 이력 행이 동결한
    값으로 재구성한다(§3 스냅샷 규율 — 적대 검증 검출분).
    """

    __tablename__ = "certification_status_log"

    certification_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("certifications.id", ondelete="RESTRICT"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    from_status: Mapped[str] = mapped_column(String(13), nullable=False)
    to_status: Mapped[str] = mapped_column(String(13), nullable=False)
    #: 반려·중단=사람 입력 필수, 만료=자동 기록 — CHECK reason_required(조건 B).
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: NULL=시스템(날짜 스윕·즉시 수렴 — 조건 A).
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    #: 전이 시점의 본체 값 동결 — 재승인 덮어쓰기의 "그때 값" 재구성용.
    expires_on_snapshot: Mapped[date | None] = mapped_column(Date, nullable=True)
    cert_number_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        value_in("from_status", CERTIFICATION_STATUSES, name="from_status_valid"),
        value_in("to_status", CERTIFICATION_STATUSES, name="to_status_valid"),
        # 제자리 전이는 존재하지 않는다 (machine.py에 자기 쌍이 없다).
        CheckConstraint("from_status <> to_status", name="no_self_transition"),
        # 조건 B — §5.2 "반려·만료·중단(사유 필수)"의 기계화. 만료의 사유는
        # 자동 전이가 채운다("만료일 도과" 등)라 비용 0.
        CheckConstraint(
            "to_status NOT IN ('EXPIRED', 'REJECTED', 'SUSPENDED') OR reason IS NOT NULL",
            name="reason_required",
        ),
        Index("ix_certification_status_log_certification_id", "certification_id"),
        Index("ix_certification_status_log_occurred_at", "occurred_at"),
    )
