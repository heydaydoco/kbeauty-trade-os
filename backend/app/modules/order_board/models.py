"""오더 보드 저장 필터 (S3-1 PR-15a / design-D D6 / ADR-0066 — 마이그레이션 M12).

행 = 사용자 1명의 이름 붙은 보드 필터 1개. **본인 것만**(타인 id는 404 — 존재 오라클 방지, §18.1 부기 ③)이고 전 역할이 쓸 수 있다(원가 없음·개인 설정).
`filter_config`는 `BoardFilter`(extra=forbid)로 **저장 시·읽기 시 재검증**하며, 스키마 변경으로 낡은 값은 무시하지 않고 응답에 `needs_resave=true`로 드러낸다.

■ 사용자 컬럼명은 **`user_id`**다 — `owner_user_id` 등은 handover 감지 이름(ASSIGNMENT_COLUMN_NAMES)이라 이관 대상 등록을 강제당한다. 저장 필터는 개인 설정이라
  이관하지 않는다(USER_FK_CLASSIFICATION=IDENTITY_LINK).
■ 사용자당 활성 20개 상한은 서비스가 강제한다(경계 경합은 그 사용자의 기존 행 잠금으로 직렬화). 이름 유일은 `unique_active(user_id, name)`.
■ **시드 0**(ActorMixin users FK 보유 — 함정 ⑩). MUTABLE(이름·조건 수정·soft delete가 앱 계정의 정상 UPDATE).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
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
from app.modules.order_board.constants import SAVED_FILTER_NAME_MAX
from app.modules.order_intake.models import OrderIntake
from app.modules.sales_orders.models import SalesOrder

SAVED_FILTER_NAME_UNIQUE = "uq_board_saved_filters_user_id_name_active"


class BoardSavedFilter(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    __tablename__ = "board_saved_filters"

    #: 필터 소유자 — 접근 제어 축(본인만). 이관 대상이 아니다(개인 설정).
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: 표시 이름(앞뒤 공백 제거 저장, 사용자 안에서 유일).
    name: Mapped[str] = mapped_column(String(SAVED_FILTER_NAME_MAX), nullable=False)
    #: `BoardFilter` 직렬화(JSON 객체). 원가·여신 키는 스키마에 없다.
    filter_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
        CheckConstraint("name !~ '[[:cntrl:]]'", name="name_clean"),
        CheckConstraint("jsonb_typeof(filter_config) = 'object'", name="filter_config_is_object"),
        unique_active("board_saved_filters", "user_id", "name"),
    )


# ── 보드 조회 전용 부분 인덱스(M12 — 보드가 소유: 소비자가 보드 쿼리 하나라 보드 모듈에 둔다) ─────────────────────
# 열 카드 쿼리(`service._so_cards`·`_intake_cards`)의 필터·정렬과 같은 모양이다 — 열 하나 = 인덱스 범위 스캔 + LIMIT 50.
# trigram(`pg_trgm`) GIN으로 `q` 부분 일치를 받치는 것은 부채다(재판정 트리거: CONFIRMED SO 1만 건 또는 보드 p95 300ms).

#: 확정 열 — `status='CONFIRMED' ORDER BY confirmed_at DESC, id DESC LIMIT 50`.
ix_sales_orders_board_confirmed = Index(
    "ix_sales_orders_board_confirmed",
    SalesOrder.status,
    SalesOrder.confirmed_at.desc(),
    SalesOrder.id.desc(),
    postgresql_where=text("deleted_at IS NULL"),
)
#: 접수·보류 열(+상태별 건수) — `status=… ORDER BY created_at, id LIMIT 50`.
ix_sales_orders_board_created = Index(
    "ix_sales_orders_board_created",
    SalesOrder.status,
    SalesOrder.created_at,
    SalesOrder.id,
    postgresql_where=text("deleted_at IS NULL"),
)
#: 인테이크 대기 열 — `status='PENDING' ORDER BY created_at, id LIMIT 50`.
ix_order_intakes_board_pending = Index(
    "ix_order_intakes_board_pending",
    OrderIntake.created_at,
    OrderIntake.id,
    postgresql_where=text("status = 'PENDING' AND deleted_at IS NULL"),
)
