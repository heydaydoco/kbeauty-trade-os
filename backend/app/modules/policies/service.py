"""정책 설정 서비스 (S3-1 ADR-0065 / design-E E8).

★ 조회 통로는 `get_policy` 하나다 — 행이 없으면 **가장 엄격한 기본 동작**(레지스트리 unset_value)
  으로 동작하되 `source='UNSET_DEFAULT'`로 드러낸다("조용한 기본값 금지"). 게이트 평가는 자기
  트랜잭션 안에서 이 함수를 불러 값과 출처를 증적에 싣는다(캐시하지 않는다).
★ 변경은 ADMIN이 사유와 함께 한다 — 값은 낙관 잠금(version), 이력은 audit_log(불변)가 정본이다.
  삭제·"기본값으로 되돌리기"는 없다(기본값과 같은 값을 명시 저장하면 이력이 명료하다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, VersionConflictError
from app.core.logging import get_logger
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import User
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox
from app.modules.policies.models import PolicySetting
from app.modules.policies.registry import POLICY_REGISTRY, PolicySpec

logger = get_logger(__name__)

POLICY_UPDATE_ENDPOINT = "PUT /api/v1/policies/{policy_key}"

PolicySource = Literal["SET", "UNSET_DEFAULT"]
PolicyValue = str | int

REASON_MIN, REASON_MAX = 2, 300


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    key: str
    value: PolicyValue
    source: PolicySource


@dataclass(frozen=True, slots=True)
class PolicyView:
    key: str
    kind: str
    label_ko: str
    description_ko: str
    allowed: tuple[str, ...]
    minimum: int
    maximum: int
    value: PolicyValue
    source: PolicySource
    version: int | None
    updated_by_name: str | None
    updated_at: datetime | None


def _spec(key: str) -> PolicySpec:
    spec = POLICY_REGISTRY.get(key)
    if spec is None:
        raise AppError(ErrorCode.POLICIES_POLICY_UNKNOWN_KEY, log_context={"policy_key": key})
    return spec


def _stored_value(spec: PolicySpec, row: PolicySetting) -> PolicyValue | None:
    """행의 값을 형에 맞게 읽는다 — 형이 안 맞으면 None(손상 = 미설정 취급)."""
    if spec.kind == "MODE":
        return row.value_text if row.value_text in spec.allowed else None
    if row.value_int is not None and spec.minimum <= row.value_int <= spec.maximum:
        return row.value_int
    return None


def get_policy(session: Session, key: str) -> ResolvedPolicy:
    """정책의 실효 값과 출처. 알 수 없는 키는 404 — 오타가 조용히 기본값이 되지 않는다."""
    spec = _spec(key)
    row = session.execute(
        select(PolicySetting).where(
            PolicySetting.policy_key == key, PolicySetting.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if row is not None:
        value = _stored_value(spec, row)
        if value is not None:
            return ResolvedPolicy(key, value, "SET")
        # CHECK가 있어 정상 경로로는 못 오는 방어 분기 — 손상은 미설정과 같이 엄격하게 처리한다.
        logger.warning("policy_value_corrupt", policy_key=key)
    return ResolvedPolicy(key, spec.unset_value, "UNSET_DEFAULT")


def _validate(spec: PolicySpec, value: Any) -> PolicyValue:
    if spec.kind == "MODE":
        if isinstance(value, str) and value in spec.allowed:
            return value
    elif (
        isinstance(value, int)
        and not isinstance(value, bool)
        and spec.minimum <= value <= spec.maximum
    ):
        return value
    raise AppError(
        ErrorCode.POLICIES_POLICY_INVALID_VALUE,
        detail={"value": _allowed_text(spec)},
    )


def _allowed_text(spec: PolicySpec) -> str:
    if spec.kind == "MODE":
        return f"{', '.join(spec.allowed)} 중 하나여야 합니다."
    return f"{spec.minimum}~{spec.maximum} 사이의 정수여야 합니다."


def list_policies() -> list[PolicyView]:
    """레지스트리 전 키를 행으로 — 저장 여부와 무관하게 항상 전건(미설정도 보인다)."""
    with unit_of_work() as uow:
        session = uow.session
        rows = {
            r.policy_key: r
            for r in session.execute(
                select(PolicySetting).where(PolicySetting.deleted_at.is_(None))
            ).scalars()
        }
        editor_ids = {r.updated_by_id or r.created_by_id for r in rows.values()} - {None}
        names: dict[int, str] = {}
        for user_id, display_name in session.execute(
            select(User.id, User.display_name).where(User.id.in_(editor_ids))
        ):
            names[user_id] = display_name
        views: list[PolicyView] = []
        for key, spec in POLICY_REGISTRY.items():
            resolved = get_policy(session, key)
            row = rows.get(key)
            editor = (row.updated_by_id or row.created_by_id) if row is not None else None
            views.append(
                PolicyView(
                    key=key,
                    kind=spec.kind,
                    label_ko=spec.label_ko,
                    description_ko=spec.description_ko,
                    allowed=spec.allowed,
                    minimum=spec.minimum,
                    maximum=spec.maximum,
                    value=resolved.value,
                    source=resolved.source,
                    version=row.version if row is not None else None,
                    updated_by_name=names.get(editor) if editor is not None else None,
                    updated_at=row.updated_at if row is not None else None,
                )
            )
        return views


def update_policy(
    *, actor: AuthenticatedUser, idempotency_key: str, policy_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """정책 값 저장(최초 생성 또는 낙관 잠금 갱신) — 사유 필수·audit·outbox가 한 트랜잭션이다.

    `payload.version`: 행이 없으면 null이어야 하고(최초 생성), 있으면 화면이 본 version이어야 한다.
    동시 최초 생성 두 요청은 하나가 성공하고 나머지는 409다(500 아님 — ON CONFLICT DO NOTHING).
    """
    spec = _spec(policy_key)
    value = _validate(spec, payload.get("value"))
    reason = str(payload.get("reason", "")).strip()
    if not (REASON_MIN <= len(reason) <= REASON_MAX):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"reason": f"변경 사유는 {REASON_MIN}~{REASON_MAX}자로 입력해 주세요."},
        )
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=POLICY_UPDATE_ENDPOINT,
            key=idempotency_key,
            request_body={"policy_key": policy_key, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        before = get_policy(session, policy_key)
        row = session.execute(
            select(PolicySetting)
            .where(PolicySetting.policy_key == policy_key, PolicySetting.deleted_at.is_(None))
            .with_for_update()
        ).scalar_one_or_none()
        expected = payload.get("version")
        column = "value_text" if spec.kind == "MODE" else "value_int"
        if row is None:
            if expected is not None:
                raise VersionConflictError(log_context={"policy_key": policy_key})
            inserted = session.execute(
                insert(PolicySetting)
                .values(policy_key=policy_key, created_by_id=actor.id, **{column: value})
                .on_conflict_do_nothing(
                    index_elements=["policy_key"], index_where=PolicySetting.deleted_at.is_(None)
                )
                .returning(PolicySetting.id)
            ).scalar_one_or_none()
            if inserted is None:  # 동시에 먼저 만든 요청이 있다 — 새로고침 후 다시 저장하게 한다
                raise VersionConflictError(log_context={"policy_key": policy_key})
            row = session.execute(
                select(PolicySetting).where(PolicySetting.id == inserted)
            ).scalar_one()
        else:
            if expected is None or int(expected) != row.version:
                raise VersionConflictError(
                    log_context={"policy_key": policy_key, "actual": row.version}
                )
            setattr(row, column, value)
            row.updated_by_id = actor.id
            session.flush()

        audit.record(
            session,
            action=AuditAction.POLICY_UPDATED,
            actor_user_id=actor.id,
            entity_type="policy_settings",
            entity_id=row.id,
            detail={
                "key": policy_key,
                "old": before.value,
                "new": value,
                "old_source": before.source,
                "reason": reason,
            },
        )
        outbox.publish(
            session,
            event_type="policies.policy.changed",
            aggregate_type="policy_settings",
            aggregate_id=row.id,
            payload={"policy_key": policy_key, "policy_id": row.id},
        )
        body = {"policy_key": policy_key, "value": value, "source": "SET", "version": row.version}
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body
