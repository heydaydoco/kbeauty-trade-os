"""정책 설정 저장 (S3-1 ADR-0065 / design-E E8).

행 = 정책 키 하나의 현재 값. 키 집합은 폐쇄 CHECK이고 코드 레지스트리(registry.py)와 1:1이다.
**시드하지 않는다** — 행이 없는 것이 정상 초기 상태이고 그때 동작은 레지스트리의 unset_value다
(fail-closed, 화면에 '미설정'으로 드러남). 변경 이력의 정본은 audit_log다.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)


class PolicySetting(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    __tablename__ = "policy_settings"

    policy_key: Mapped[str] = mapped_column(String(60), nullable=False)
    value_text: Mapped[str | None] = mapped_column(String(20), nullable=True)
    value_int: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # 키 집합 — registry.POLICY_REGISTRY와 1:1(test_policy_contract가 대사한다).
        CheckConstraint(
            "policy_key IN ('pi_advance_gate_mode', 'price_deviation_tolerance_bp')",
            name="policy_key_closed",
        ),
        # 키별 값 형·범위 — 값 형 교차(모드에 정수·허용치에 문자)도 거부한다.
        CheckConstraint(
            "(policy_key = 'pi_advance_gate_mode' AND value_int IS NULL"
            " AND value_text IN ('OFF', 'WARN', 'BLOCK'))"
            " OR (policy_key = 'price_deviation_tolerance_bp' AND value_text IS NULL"
            " AND value_int BETWEEN 0 AND 10000)",
            name="policy_value_shape",
        ),
        unique_active("policy_settings", "policy_key"),
    )
