"""로그인 요청·응답 (DESIGN.md §18.4).

ORM 모델을 그대로 내보내지 않는다. password_hash·failed_login_count처럼
화면이 알 필요 없는 값이 응답 스키마를 바꾸는 것만으로 새는 경로를 없앤다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser


class LoginRequest(BaseModel):
    # 형식 검증(EmailStr)을 붙이지 않는 이유: 실패 응답이 "이메일 형식이 틀렸다"와
    # "자격 증명이 틀렸다"로 갈리면 그 차이로 계정 존재 여부를 떠볼 수 있다.
    # 정규화는 서비스가 하고, 존재 여부는 한 가지 응답으로 수렴시킨다.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class AccountActiveRequest(BaseModel):
    is_active: bool


class RoleAssignmentRequest(BaseModel):
    role: RoleCode


class UserSummary(BaseModel):
    """목록·상세 공통 표현.

    password_hash·failed_login_count·locked_until은 담지 않는다. 계정 잠금 상태를
    화면에 보여 줄 필요가 생기면 그때 별도 필드로 명시적으로 연다 — 스키마가
    ORM을 그대로 따라가면 컬럼이 늘 때마다 조용히 응답이 늘어난다.
    """

    id: int
    email: str
    display_name: str
    is_active: bool
    roles: list[str]


class MeResponse(BaseModel):
    """로그인한 사람이 자기 자신에 대해 볼 수 있는 것."""

    id: int
    email: str
    display_name: str
    roles: list[str]

    @classmethod
    def of(cls, principal: AuthenticatedUser) -> MeResponse:
        return cls(
            id=principal.id,
            email=principal.email,
            display_name=principal.display_name,
            roles=sorted(role.value for role in principal.roles),
        )
