"""notification channels and outbox retry

리비전 ID: f619b469c74d
직전 리비전: e7a1d4c8b2f6
생성 시각(UTC): 2026-08-12 04:27:35.514010+00:00

알림 채널·구독 2테이블 신설 + 아웃박스 재시도 컬럼 (ADR-07·§16·§17.6 /
WBS S2-3 / s2-3-plan.md §0-1 판정 요청 5·12·조건 D / ADR-0043·0044 예정).

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ rename 없음 — 신규 테이블 2개 + events 컬럼 1개 추가뿐(drop+add로 둔갑한
    rename 없음).
  ■ **기존 인덱스 재정의 1건**(함정 ① 계보): `ix_events_pending`을 (id)에서
    (next_retry_at, id)로 바꾼다. autogenerate가 "expression number 1 to 2"로
    감지했으나 초안을 그대로 쓰지 않고 전건 수기 확인했다 —
    ★ 이름은 op.f()로 감싼다(함정 ⑪ "이미 최종 이름" 표식). 이 인덱스는
      모델이 이름을 직접 지어(`Index("ix_events_pending", …)`) naming
      convention의 `ix_%(table_name)s_%(column_0_N_name)s`를 타지 않는다 —
      평문으로 주면 규약이 덧씌워질 여지를 남기므로 표식을 붙인다.
    ★ 부분 조건(`published_at IS NULL`)은 sa.text()로 준다. 초안의 평문
      문자열 `'(published_at IS NULL)'`은 표기 흔들림이라 정리했다.
    ★ 선두 컬럼이 next_retry_at인 이유는 디스패처 클레임 질의가 "재시도
      시각이 없거나 지난 것"을 시각 순으로 집기 때문이다(판정 요청 12).
  ■ CHECK 1건(notification_channels.kind 5값)은 신규 테이블이라 create_table
    안에 포함 — 모델 NOTIFICATION_CHANNEL_KINDS와 1:1(정의문 테스트가 대조).
  ■ 유니크 2건 전부 unique_active() 부분 인덱스다(§17.4) —
    (code) / (event_type, channel_id), 술어는 `deleted_at IS NULL`.
  ■ server_default는 믹스인(now()·version=1) + config '{}'::jsonb +
    is_enabled true뿐.
  ■ **시드 없음 — 항구 금지**(함정 ⑩). 두 테이블 다 ActorMixin의 users FK를
    달아, 시드로 넣으면 테스트 정리의 TRUNCATE CASCADE가 PRESERVED_TABLES를
    무력화한다. 이번 세션은 애초에 0행 유지다(판정 요청 5).
  ■ GRANT/REVOKE 없음 — 두 테이블 다 MUTABLE(table_policy 등록). 불변
    대상이 아니다.
  ■ downgrade는 역순 완결이다: 새 인덱스 drop → 구 인덱스(id) 복원 →
    next_retry_at drop → 구독·채널 순 drop_table(FK 역순). CI 드라이런의
    upgrade→downgrade→upgrade 왕복 대상.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f619b469c74d"
down_revision: str | None = "e7a1d4c8b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PENDING_INDEX = "ix_events_pending"
_PENDING_WHERE = "published_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name_ko", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('EMAIL', 'IN_APP', 'NOTION', 'SLACK_WEBHOOK', 'TELEGRAM')",
            name=op.f("ck_notification_channels_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_notification_channels_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_notification_channels_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
    )
    op.create_index(
        "uq_notification_channels_code_active",
        "notification_channels",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_webhook_subscriptions_channel_id_notification_channels"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_webhook_subscriptions_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_webhook_subscriptions_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_subscriptions")),
    )
    op.create_index(
        "ix_webhook_subscriptions_event_type",
        "webhook_subscriptions",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "uq_webhook_subscriptions_event_type_channel_id_active",
        "webhook_subscriptions",
        ["event_type", "channel_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 아웃박스 재시도 (판정 요청 12) — 컬럼 추가가 인덱스 재정의보다 먼저다.
    op.add_column("events", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_index(op.f(_PENDING_INDEX), table_name="events")
    op.create_index(
        op.f(_PENDING_INDEX),
        "events",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(_PENDING_WHERE),
    )


def downgrade() -> None:
    op.drop_index(op.f(_PENDING_INDEX), table_name="events")
    op.create_index(
        op.f(_PENDING_INDEX),
        "events",
        ["id"],
        unique=False,
        postgresql_where=sa.text(_PENDING_WHERE),
    )
    op.drop_column("events", "next_retry_at")
    op.drop_index(
        "uq_webhook_subscriptions_event_type_channel_id_active",
        table_name="webhook_subscriptions",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_webhook_subscriptions_event_type", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
    op.drop_index(
        "uq_notification_channels_code_active",
        table_name="notification_channels",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("notification_channels")
