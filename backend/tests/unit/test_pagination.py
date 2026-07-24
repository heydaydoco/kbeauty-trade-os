"""K. 보안·품질 — 페이지네이션 계약 (DESIGN.md §18.4)."""

from __future__ import annotations

import pytest

from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, PageParams

pytestmark = pytest.mark.group_k


def test_default_page_size_is_50() -> None:
    """기본 페이지 크기는 50이다"""
    assert PageParams().size == DEFAULT_PAGE_SIZE == 50


def test_offset_and_limit() -> None:
    """offset/limit이 페이지·크기에서 올바로 계산된다"""
    params = PageParams(page=3, size=20)
    assert params.offset == 40
    assert params.limit == 20


def test_page_envelope() -> None:
    """목록 응답 봉투가 items·total·page·size를 담는다"""
    page = Page.of(["a", "b"], total=57, params=PageParams(page=2, size=20))
    assert page.items == ["a", "b"]
    assert (page.total, page.page, page.size) == (57, 2, 20)


def test_max_page_size_is_capped() -> None:
    """상한(200)이 정의돼 있다 — 무제한 조회를 막는다"""
    assert MAX_PAGE_SIZE == 200
