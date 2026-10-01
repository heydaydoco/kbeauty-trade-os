"""여신 직렬화 잠금 — 거래처 행 `FOR NO KEY UPDATE` (S3-1 ADR-0064·0059 / design-E E3).

여신 체크의 "확인→기록" 창(§17.2)을 닫는다. 잠금 단위는 **거래처(partners) 행**이고 모드는 `FOR NO KEY UPDATE`다(SQLAlchemy
`with_for_update(key_share=True)`): `FOR UPDATE`는 자식 행(QT·PI·SO·payments) INSERT의 FK 검사 잠금(`FOR KEY SHARE`)과 충돌해 무관한 신규 작성까지
막지만 `NO KEY UPDATE`는 비충돌이면서 같은 거래처의 여신 체크끼리·임포트의 partners `FOR UPDATE`·거래처 UPDATE와는 직렬화된다.

★ `lock_buyer_for_credit`이 **단일 진입점**이다 — `Partner`에 대한 `with_for_update`는 이 파일 밖에 없어야 한다(아키텍처 스캔). 조회에
  `populate_existing`을 걸어 **잠금 획득 후 한도를 새로 읽는다**(ORM 아이덴티티 캐시가 옛 한도로 판정하는 것을 막는다). 한도가 NULL이어도 먼저 잠근다
  (NULL→값 변경 경합 방지). 평가 함수는 `LockedBuyer` 토큰을 요구한다 — 잠그지 않은 평가는 형 수준에서 불가능하다(advisory 조회는 별도 이름·`advisory=true`).
★ S4-1(재고·할당 직렬화 ADR)이 advisory 등으로 바꿀 수 있다 — 바꿀 때는 **이 함수 내부만** 교체하고 호출 계약(`LockedBuyer`)·전역 잠금 순서·409 계약은 승계한다.
★ 한 트랜잭션은 거래처 행을 최대 1개만 잠근다(벌크는 건별 독립 트랜잭션 §17.6).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.exceptions import NotFoundError
from app.modules.partners.models import Partner


@dataclass(frozen=True, slots=True)
class LockedBuyer:
    """잠금을 쥔 거래처 — `evaluate_credit`의 입장권이다(잠금 없이는 만들 수 없는 값)."""

    partner: Partner


def lock_buyer_for_credit(session: Session, partner_id: int) -> LockedBuyer:
    """거래처 행을 `FOR NO KEY UPDATE`로 잠그고 **최신 값을 새로 읽어** 돌려준다. 없거나 삭제됐으면 404."""
    partner = session.execute(
        select(Partner)
        .where(Partner.id == partner_id, Partner.deleted_at.is_(None))
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if partner is None:
        raise NotFoundError(log_context={"partner_id": partner_id, "op": "lock_buyer_for_credit"})
    return LockedBuyer(partner=partner)
