"""알림 채널·구독 (DESIGN.md §2 ADR-07 / §16 "이벤트 발행 → 구독 → 커넥터").

★ **이 두 테이블은 S2-3에서 0행으로 선다**(판정 요청 5). 구조를 먼저 세우는
  이유는 ADR-07의 라우팅이 "이벤트×채널×포맷을 데이터로"이기 때문이고, 행을
  넣지 않는 이유는 **행을 넣는 순간 대외 발신 통로가 데이터로 열리기** 때문이다
  (§15 L3 금지 "대외 최초 발송"). 이번 세션의 유일한 실장 채널은 화면 알림센터
  (IN_APP)이고, 그것은 채널 행 없이 도는 내부 경로다 — 알림 생성 코어가
  alerts에 직접 적는다.

★ 행 공급 경로가 이 세션에 **없다**: CRUD·CLI·시드 전부 부재. 공급 방식과
  구독 평가, config 시크릿 취급(로그 마스킹 — 현행 redaction은 `config` 키를
  잡지 않는다)은 외부 커넥터가 서는 S6-3에서 동석 판정한다.

★ 마이그레이션 시드는 **항구 금지**다(함정 ⑩) — 두 테이블 다 ActorMixin의
  users FK를 달고 있어, 시드로 넣으면 테스트 정리의 TRUNCATE CASCADE가
  PRESERVED_TABLES를 무력화한다.

★ IN_APP은 ADR-07의 채널 열거(텔레그램/슬랙 웹훅/메일/노션) **밖의 신설
  판단**이다. 문면 근거는 같은 ADR의 별도 문구 "화면 알림센터"이고, 알림센터를
  채널 종류의 하나로 모델링한 것은 설계 선택이다(ADR 명문 — 판정 요청 4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
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

#: 채널 종류. 4종은 ADR-07 문면(텔레그램/슬랙 웹훅/메일/노션), IN_APP은
#: 화면 알림센터의 채널화(신설 판단 — 파일 독스트링).
#: ★ 커넥터가 실장된 것은 IN_APP뿐이다. 나머지는 이름만 있고 보내는 코드가
#:   없다 — 그 부재가 §15 "대외 최초 발송" 금지의 물리적 담보다(S6-3에서 실장).
NOTIFICATION_CHANNEL_KINDS = ("IN_APP", "SLACK_WEBHOOK", "TELEGRAM", "EMAIL", "NOTION")


class NotificationChannel(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """발송 채널 하나 (ADR-07 "notification_channels 추상화")."""

    __tablename__ = "notification_channels"

    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    #: 채널별 접속 설정(웹훅 URL·토큰 등). 시크릿 성격이라 로그·응답 노출
    #: 규약이 S6-3 판정 대상이다 — 그전까지 0행이라 담을 값 자체가 없다.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        value_in("kind", NOTIFICATION_CHANNEL_KINDS, name="kind_valid"),
        unique_active("notification_channels", "code"),
    )


class WebhookSubscription(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """이벤트 → 채널 라우팅 (§16 "webhook_subscriptions(이벤트×채널×포맷×대상)").

    ★ 디스패처는 이 테이블을 **아직 평가하지 않는다**(S2-3 범위 — 평가 대상은
      alert_rules뿐). 구독 평가는 보낼 커넥터가 생기는 S6-3의 일이고, 그전에
      평가만 붙이면 매칭돼도 갈 곳이 없는 코드가 된다.
    """

    __tablename__ = "webhook_subscriptions"

    #: events.event_type와 같은 값 공간(<도메인>.<대상>.<사건>).
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notification_channels.id", ondelete="RESTRICT"), nullable=False
    )
    #: 포맷·대상 등 구독별 설정.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        unique_active("webhook_subscriptions", "event_type", "channel_id"),
        Index("ix_webhook_subscriptions_event_type", "event_type"),
    )
