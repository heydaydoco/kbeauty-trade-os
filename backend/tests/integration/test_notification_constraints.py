"""K. 보안·품질 — 알림 채널·구독과 아웃박스 재시도의 DB 계약 (ADR-07·§17.4·§17.6).

★ 이 파일이 고정하는 것:
  ① 채널 종류 CHECK의 **정의문**이 모델 열거와 1:1이다(수기 반영 누락의 최종
    검출 — 함정 ①).
  ② 두 신규 테이블의 부분 유니크가 실재하고 `deleted_at IS NULL` 술어를
    갖는다(§17.4 — 삭제 행이 재유입을 영구 차단하지 않는다).
  ③ **`ix_events_pending`의 인덱스 정의문**이 (next_retry_at, id)로 재정의됐고
    부분 조건을 유지한다(판정 조건 D — 인덱스 재정의도 정의문으로 실측한다.
    이름만 남고 구 정의가 사는 실패 모양은 CHECK와 똑같이 조용하다).
  ④ 두 테이블이 **0행으로 서 있다**(판정 요청 5) — 마이그레이션 시드 금지의
    실측. 행이 생기면 그 순간 대외 발신 통로가 데이터로 열린다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.notifications.models import (
    NOTIFICATION_CHANNEL_KINDS,
    NotificationChannel,
    WebhookSubscription,
)

pytestmark = pytest.mark.group_k


def _insert_channel(**overrides: Any) -> int:
    columns: dict[str, Any] = {
        "code": "IN_APP_DEFAULT",
        "name_ko": "화면 알림센터",
        "kind": "IN_APP",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = NotificationChannel(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _insert_subscription(channel_id: int, **overrides: Any) -> int:
    columns: dict[str, Any] = {
        "event_type": "certifications.certification.status_changed",
        "channel_id": channel_id,
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = WebhookSubscription(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


# ── ① 채널 종류 CHECK ────────────────────────────────────────────────────────


def test_channel_kind_check_definition_lists_every_model_kind() -> None:
    """kind CHECK의 **정의문**에 모델 열거 5종이 전부 실재한다"""
    with unit_of_work() as uow:
        definition = uow.session.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'public.notification_channels'::regclass
                  AND conname = 'ck_notification_channels_kind_valid'
                """
            )
        ).scalar_one()
    for kind in NOTIFICATION_CHANNEL_KINDS:
        assert f"'{kind}'" in definition, definition


def test_an_unknown_channel_kind_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_channel(kind="CARRIER_PIGEON")
    assert "kind" in str(exc.value)


# ── ② 부분 유니크 (§17.4) ────────────────────────────────────────────────────


def test_duplicate_channel_code_is_rejected_while_active() -> None:
    _insert_channel(code="DUP")
    with pytest.raises(IntegrityError):
        _insert_channel(code="DUP")


def test_a_soft_deleted_channel_code_can_be_reused() -> None:
    """삭제된 코드가 재유입을 영구 차단하지 않는다 — 재유입은 부활이 아니라 신규"""
    first = _insert_channel(code="REUSE")
    with unit_of_work() as uow:
        row = uow.session.get(NotificationChannel, first)
        assert row is not None
        row.deleted_at = utcnow()
    second = _insert_channel(code="REUSE")
    assert second != first


def test_duplicate_subscription_pair_is_rejected_while_active() -> None:
    channel_id = _insert_channel(code="SUBDUP")
    _insert_subscription(channel_id)
    with pytest.raises(IntegrityError):
        _insert_subscription(channel_id)


def test_a_subscription_needs_an_existing_channel() -> None:
    """구독은 실재하는 채널만 가리킨다 — FK RESTRICT"""
    with pytest.raises(IntegrityError):
        _insert_subscription(999_999)


# ── ③ 아웃박스 대기열 인덱스 재정의 (판정 조건 D) ────────────────────────────


def test_pending_index_definition_covers_retry_ordering() -> None:
    """ix_events_pending 정의문이 (next_retry_at, id) + 부분 조건이다.

    조건 D: 인덱스 재정의도 CHECK와 같은 지위로 정의문을 실측한다. 이름만
    남고 구 정의(id 단독)가 살아 있으면 대기열 조회가 재시도 시각을 못 타고,
    실패 건이 매 주기 앞줄을 차지하는 스핀이 조용히 생긴다.
    """
    with unit_of_work() as uow:
        definition = uow.session.execute(
            text(
                """
                SELECT pg_get_indexdef(indexrelid) FROM pg_index
                WHERE indrelid = 'public.events'::regclass
                  AND indexrelid = 'public.ix_events_pending'::regclass
                """
            )
        ).scalar_one()
    assert "next_retry_at" in definition, definition
    assert "published_at IS NULL" in definition, definition
    #: 구 정의가 남았는지의 직접 판별 — 컬럼 목록이 (id) 단독이면 실패다.
    assert "(next_retry_at, id)" in definition.replace(" NULLS LAST", ""), definition


# ── ④ 0행 유지 (판정 요청 5) ─────────────────────────────────────────────────


def test_notification_tables_ship_empty() -> None:
    """채널·구독은 0행으로 선다 — 마이그레이션 시드 금지(함정 ⑩)의 실측.

    이 테스트가 빨개지는 경우는 둘뿐이다: 마이그레이션이 시드를 넣었거나,
    S6-3 전에 행 공급 경로가 생겼거나. 둘 다 판정 대상이다.
    """
    with unit_of_work() as uow:
        channels = uow.session.execute(select(func.count()).select_from(NotificationChannel))
        subscriptions = uow.session.execute(select(func.count()).select_from(WebhookSubscription))
        assert channels.scalar_one() == 0
        assert subscriptions.scalar_one() == 0
