"""게이트 증적 2표 — `gate_evaluations`(확정 시도 스냅샷)·`gate_overrides`(통제된 예외) (S3-1 PR-11a / design-D D3 / ADR-0069).

■ **둘 다 INSERT-only(IMMUTABLE)** — PkMixin만(Version·SoftDelete·Actor·Timestamp 믹스인 없음). 앱 계정은 INSERT·SELECT만(마이그레이션의 `revoke_mutations`).
  정정은 새 행이다: override 철회는 `REVOKE` 행 추가, 확정 시도는 시도마다 1행.
■ `gate_evaluations`: **기록 시점은 확정 시도(성공·차단)뿐**이다(PR-12가 쓴다 — 조회 `GET /gates`는 저장하지 않는다, 조회 부작용 금지). 확정 증거 스냅샷은 SO당
  정확히 1행(`UNIQUE(subject_type, subject_id) WHERE outcome='CONFIRMED'`) — "확정 시점 값" 재현을 SO 열 추가 없이 한다. `input_digest`는 평가 시점 SO의
  `gate_input_digest`(승인 결속 토큰과 같은 정의 — X-07).
■ `gate_overrides`: `gate_code` CHECK가 **override 가능한 4종만** 열거한다 — ITEM_MAPPING·DUPLICATE_PO·CREDIT은 DB가 우회 불가를 강제한다. 유효성은
  `(subject, gate_code, line_id, basis_hash)`별 **최신 행(max id)이 GRANT**일 때다. 판정 입력이 바뀌면 `basis_hash`가 달라져 기존 override는 자동 무효가 된다.
  `subject_type`은 폴리모픽(S5-1이 `CHANNEL_LISTING` 추가)이라 `subject_id`·`line_id`에 FK를 두지 않는다.
■ 컬럼·테이블 이름에 집계·신호등 성격의 단어를 쓰지 않는다(`results`·`basis` — 계산값 미저장 스캔 통과).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import PkMixin
from app.modules.gates.text import REASON_MAX, REASON_MIN

SUBJECT_SALES_ORDER = "SALES_ORDER"
#: S5-1이 `CHANNEL_LISTING`을 추가한다(소비분 한정 재정의 — ADR-0041 규율).
SUBJECT_TYPES = (SUBJECT_SALES_ORDER,)

EVALUATION_CONFIRMED = "CONFIRMED"
EVALUATION_BLOCKED = "BLOCKED"
EVALUATION_OUTCOMES = (EVALUATION_CONFIRMED, EVALUATION_BLOCKED)

ACTION_GRANT = "GRANT"
ACTION_REVOKE = "REVOKE"
ACTIONS = (ACTION_GRANT, ACTION_REVOKE)

#: override 가능한 게이트 4종 — DB CHECK와 코드(`gates.policy.OVERRIDE_ROLES`)가 같은 집합임을 아키텍처 테스트가 대사한다.
OVERRIDABLE_GATE_CODES = ("MARKET_READINESS", "MOQ", "PI_DEPOSIT", "PRICE_DEVIATION")
#: override를 부여할 수 있는 판정 결과 — BLOCK·UNKNOWN만(PASS·WARN은 해소가 필요 없다).
OVERRIDE_RESULTS = ("BLOCK", "UNKNOWN")
ROLE_CODES = ("ADMIN", "CERT", "LOGISTICS", "TRADE", "VIEWER")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class GateEvaluation(PkMixin, Base):
    """확정 시도 1건의 게이트 평가 스냅샷 — 차단(BLOCKED)이든 성공(CONFIRMED)이든 시도마다 1행."""

    __tablename__ = "gate_evaluations"

    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outcome: Mapped[str] = mapped_column(String(9), nullable=False)
    #: 비PASS 결과 목록 + passed_gates + 사용한 override id·승인 ref + policy_source (`gates.service.evaluation_results`).
    results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: 평가 시점 SO 판정 입력 digest(`sales_orders.digest.gate_input_digest`) — 승인 결속 토큰과 같은 정의.
    input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    evaluated_by_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"subject_type IN ({_in_list(SUBJECT_TYPES)})", name="subject_type_valid"),
        CheckConstraint(f"outcome IN ({_in_list(EVALUATION_OUTCOMES)})", name="outcome_valid"),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$'", name="input_digest_format"),
        CheckConstraint("jsonb_typeof(results) = 'object'", name="results_is_object"),
        # SO당 확정 증거 스냅샷은 정확히 1행(차단 시도는 여러 행이어도 된다).
        Index(
            "uq_gate_evaluations_confirmed_once",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("outcome = 'CONFIRMED'"),
        ),
        Index("ix_gate_evaluations_subject", "subject_type", "subject_id"),
        Index("ix_gate_evaluations_evaluated_by_id", "evaluated_by_id"),
    )


class GateOverride(PkMixin, Base):
    """통제된 예외(override) 부여·철회 1건 — 사유·권한·판정 해시에 결속된 불변 기록."""

    __tablename__ = "gate_overrides"

    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: SO 라인 id — 폴리모픽이라 FK 없음. 게이트 단위 결과(예: PI 입금)는 NULL.
    line_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gate_code: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(6), nullable=False)
    #: 부여 시점의 판정 결과(BLOCK·UNKNOWN) — REVOKE 행은 원 GRANT 값을 복사한다.
    result_at_grant: Mapped[str] = mapped_column(String(7), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: 판정 근거 스냅샷(판매 단가·기준가·수량·준비 상태 요약만 — 원가·마진·매입가 금지).
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    basis_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    granted_by_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: 행위자가 보유한 역할 중 게이트 허용 역할(통제 근거 — 감사에서 "어느 권한으로 통과시켰는가").
    authorized_role: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"subject_type IN ({_in_list(SUBJECT_TYPES)})", name="subject_type_valid"),
        # ITEM_MAPPING·DUPLICATE_PO·CREDIT은 열거에 없다 — override 자체가 DB에서 불가능하다.
        CheckConstraint(
            f"gate_code IN ({_in_list(OVERRIDABLE_GATE_CODES)})", name="gate_code_overridable"
        ),
        CheckConstraint(f"action IN ({_in_list(ACTIONS)})", name="action_valid"),
        CheckConstraint(
            f"result_at_grant IN ({_in_list(OVERRIDE_RESULTS)})", name="result_at_grant_valid"
        ),
        CheckConstraint(
            f"char_length(btrim(reason)) BETWEEN {REASON_MIN} AND {REASON_MAX}",
            name="reason_length",
        ),
        # 로캘·ASCII에 의존하는 최후 방어선 — 사유의 정본 검증은 `gates.text.reason_problem`(유니코드 Cc/Cf/Zl/Zp·한글 채움·실질 5자)이다.
        CheckConstraint("reason !~ '[[:cntrl:]]'", name="reason_clean"),
        CheckConstraint("basis_hash ~ '^[0-9a-f]{64}$'", name="basis_hash_format"),
        CheckConstraint("jsonb_typeof(basis) = 'object'", name="basis_is_object"),
        CheckConstraint("line_id IS NULL OR line_id > 0", name="line_id_positive"),
        CheckConstraint(
            f"authorized_role IN ({_in_list(ROLE_CODES)})", name="authorized_role_valid"
        ),
        Index("ix_gate_overrides_subject_gate", "subject_type", "subject_id", "gate_code"),
        Index("ix_gate_overrides_granted_by_id", "granted_by_id"),
    )
