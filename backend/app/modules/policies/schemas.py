"""정책 설정 요청·응답 (S3-1 ADR-0065)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.modules.policies.service import PolicyView


class PolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 모드는 문자열, 허용치(bp)는 정수 — 형 검증은 레지스트리 기준으로 서비스가 한다.
    #: 엄격 형 — True가 1로 조용히 바뀌어 통과하는 것을 막는다(불리언은 422).
    value: StrictStr | StrictInt
    #: 행이 없으면 null(최초 생성), 있으면 화면이 본 version(낙관 잠금).
    version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=400)


class PolicySummary(BaseModel):
    key: str
    kind: str
    label_ko: str
    description_ko: str
    allowed: list[str]
    minimum: int
    maximum: int
    value: str | int
    source: Literal["SET", "UNSET_DEFAULT"]
    version: int | None
    updated_by_name: str | None
    updated_at: datetime | None

    @classmethod
    def of(cls, view: PolicyView) -> PolicySummary:
        return cls(
            key=view.key,
            kind=view.kind,
            label_ko=view.label_ko,
            description_ko=view.description_ko,
            allowed=list(view.allowed),
            minimum=view.minimum,
            maximum=view.maximum,
            value=view.value,
            source=view.source,
            version=view.version,
            updated_by_name=view.updated_by_name,
            updated_at=view.updated_at,
        )


class PolicyUpdated(BaseModel):
    policy_key: str
    value: str | int
    source: Literal["SET"]
    version: int
