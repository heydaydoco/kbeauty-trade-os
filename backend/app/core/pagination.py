"""페이지네이션 계약 (DESIGN.md §18.4 "전 목록 API 페이지네이션 기본 50, 무페이지네이션 금지").

목록 응답의 모양을 처음부터 하나로 고정한다. 화면 30개가 각자 다른 모양으로
목록을 받으면 나중에 통일할 수 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PageParams:
    """목록 조회 파라미터. 상한을 넘기면 422로 거부된다."""

    page: Annotated[int, Query(ge=1, description="1부터 시작")] = 1
    size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"기본 {DEFAULT_PAGE_SIZE}, 최대 {MAX_PAGE_SIZE}")
    ] = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        return self.size


class Page(BaseModel, Generic[T]):
    """목록 응답 봉투."""

    items: list[T]
    total: int = Field(description="필터 적용 후 전체 건수")
    page: int
    size: int

    @classmethod
    def of(cls, items: Sequence[T], total: int, params: PageParams) -> Page[T]:
        return cls(items=list(items), total=total, page=params.page, size=params.size)
