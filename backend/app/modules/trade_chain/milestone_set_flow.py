"""품목군 마일스톤 세트 쓰기 — 추가·제거 (S3-2 PR-4c / design-integrated N-01·§9 R-14·R-26 / ADR-0079 ⑥·0085 ④ / design-B B16).

■ 세트 = 품목군 × **선적 저장형 8종**(DB CHECK `ck_item_profile_milestone_types_type_valid`). 선적 계획 초안(M4 — 사람 1클릭)의 적용 종류 =
  구분별 적용 집합 ∩ 라인 SKU 품목군 세트 합집합이다(`milestone_flow._draft_types`가 읽는다 — 세트를 바꾸면 다음 초안부터 반영, 이미 만든
  선적 행은 건드리지 않는다).
■ 쓰기 = **관리자 전용**(CERT 미배정 — §2 인증 편집은 시장·요건 템플릿 한정, R-14). 조회 = 전 역할. 라우터 가드가 존재·입력 검사보다 먼저(403).
■ 서류 세트 선례(`/item-profiles/{id}/document-types`) 동형 — 추가 = Idempotency-Key + 부분 유니크, 제거 = soft delete(재추가 = 신규 행).
  선례보다 엄격하게: 경로의 품목군이 없으면 **404**(선례 422 — 경로 자원), 중복은 **409 `SHIPMENTS.MILESTONE.DUPLICATE_TYPE`**(R-26 — 선례 422),
  파생·OEM 종류는 **422 `SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE`**(R-26), 모르는 종류 = 스키마 422.
■ 오류 우선순위(ADR-0079 ⑧): 403(라우터) → 품목군 404 → 중복 409(무잠금 peek + 경합은 부분 유니크 번역 — 500 0) → 비적용 422.
  비적용 종류는 CHECK 때문에 세트에 있을 수 없어 409·422가 한 요청에 겹치지 않는다.
■ 잠금: item_profiles·세트 행은 LOCK_ORDER 밖이다(전표 사슬과 한 TX에 섞이지 않는다 — 품목군 삭제 경로도 없다). 제거는 세트 행 `FOR UPDATE`.
■ 자동 경로 0 — 호출처는 세트 라우터 1곳(no_auto_confirm 레지스트리). 아웃박스·audit 없음(서류·요건 세트 선례 — 행위자 열·soft delete가 이력).
"""

from __future__ import annotations

from typing import Any

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.shipments import service as shipments
from app.modules.trade_chain.milestone_view import profile_milestone_type_body, require_profile
from app.modules.trade_docs.constants import SHIPMENT_STORED_MILESTONES

ADD_ENDPOINT = "POST /api/v1/item-profiles/{id}/milestone-types"


def add_profile_milestone_type(
    *, actor: AuthenticatedUser, idempotency_key: str, profile_id: int, milestone_type: str
) -> tuple[int, dict[str, Any]]:
    """세트에 종류 1개 추가(201) — 404 → 409 DUPLICATE_TYPE → 422 TYPE_NOT_APPLICABLE. 같은 키 재요청 = 최초 결과."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=ADD_ENDPOINT,
            key=idempotency_key,
            request_body={"profile_id": profile_id, "milestone_type": milestone_type},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        require_profile(session, profile_id)
        if shipments.find_profile_milestone_type(session, profile_id, milestone_type) is not None:
            raise AppError(
                ErrorCode.SHIPMENTS_MILESTONE_DUPLICATE_TYPE,
                detail={"milestone_type": "이 품목군의 마일스톤 세트에 이미 있는 종류입니다."},
                log_context={"profile_id": profile_id, "milestone_type": milestone_type},
            )
        if milestone_type not in SHIPMENT_STORED_MILESTONES:
            raise AppError(
                ErrorCode.SHIPMENTS_MILESTONE_TYPE_NOT_APPLICABLE,
                detail={
                    "milestone_type": "마일스톤 세트에는 선적 일정 종류(서류마감·Cargo Closing·PSI·신고수리·ETD·"
                    "B/L 발행·ETA·수입 세금 납부기한)만 넣을 수 있습니다 — 자동 계산·OEM 생산 종류는 제외."
                },
                log_context={"profile_id": profile_id, "milestone_type": milestone_type},
            )
        row = shipments.insert_profile_milestone_type(
            session, profile_id=profile_id, milestone_type=milestone_type, actor_id=actor.id
        )
        body = profile_milestone_type_body(row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def remove_profile_milestone_type(
    *, actor: AuthenticatedUser, profile_id: int, link_id: int
) -> None:
    """세트에서 종류 제거(soft delete — 204). 품목군 404 → 세트 행이 경로의 품목군 소속이 아니거나 이미 제거됨 404(부작용 0)."""
    with unit_of_work() as uow:
        session = uow.session
        require_profile(session, profile_id)
        row = shipments.require_profile_milestone_type(session, profile_id, link_id)
        shipments.soft_delete_profile_milestone_type(row, actor_id=actor.id)
