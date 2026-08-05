"""K. CSV 수식 이스케이프 — 전단사 왕복 (§12.2 보강 / ADR-0027 / 부채 #17).

고정하는 것 세 가지: ① 수식 트리거로 시작하는 **문자열** 셀은 `'` 접두로
중화된다 ② 숫자·금액 타입 셀은 건드리지 않는다(음수가 텍스트로 변하면 안
된다) ③ 변환은 전단사다 — unescape(escape(s)) == s가 어떤 문자열에도 성립해야
왕복 편집(S1-3 PR-3)의 diff가 이스케이프를 변경으로 오검출하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.csv_export import (
    escape_formula_cell,
    render_csv,
    unescape_formula_cell,
)

pytestmark = pytest.mark.group_k

#: 실측 말뭉치 — 수식 4종·탭·CR·접두 문자 자신·일반 텍스트·한글·빈 문자열.
ROUND_TRIP_CORPUS = (
    "=SUM(A1:A9)",
    '=HYPERLINK("http://evil","click")',
    "+82-10-0000-0000",
    "-감가 조정분",
    "@user",
    "\tTAB으로 시작",
    "\rCR로 시작",
    "'=이미 접두된 것처럼 보이는 원값",
    "''이중 접두처럼 보이는 원값",
    "'일반 텍스트에 붙은 접두",
    "일반 텍스트",
    '앰플, "고농축" 50ml',
    "",
    "=",
    "'",
)


# ── 이스케이프 규칙 ─────────────────────────────────────────────────────────


def test_formula_triggers_are_escaped() -> None:
    assert escape_formula_cell("=SUM(A1)") == "'=SUM(A1)"
    assert escape_formula_cell("+8210") == "'+8210"
    assert escape_formula_cell("-5%") == "'-5%"
    assert escape_formula_cell("@user") == "'@user"
    assert escape_formula_cell("\t x") == "'\t x"
    assert escape_formula_cell("\r x") == "'\r x"


def test_a_leading_quote_is_escaped_too() -> None:
    """`'` 자신도 트리거다 — 이 포함이 전단사의 조건이다 (ADR-0027)

    빼면 원값 `'=x`와 이스케이프 결과 `'=x`(원값 `=x`)가 같은 표기가 되어
    임포트가 어느 쪽인지 알 수 없다.
    """
    assert escape_formula_cell("'=x") == "''=x"
    assert escape_formula_cell("'hello") == "''hello"


def test_plain_text_is_untouched() -> None:
    assert escape_formula_cell("한국콜마") == "한국콜마"
    assert escape_formula_cell("Amore 30ml") == "Amore 30ml"
    assert escape_formula_cell("") == ""


# ── 역변환 — 전단사 (§12.2 왕복 값 보존) ───────────────────────────────────


def test_round_trip_preserves_every_value() -> None:
    """unescape(escape(s)) == s — 왕복 diff가 이스케이프를 변경으로 안 읽는 근거"""
    for value in ROUND_TRIP_CORPUS:
        assert unescape_formula_cell(escape_formula_cell(value)) == value, repr(value)


def test_unescape_leaves_a_manual_text_prefix_alone() -> None:
    """사용자가 Excel에서 직접 친 `'일반텍스트`는 벗기지 않는다 —

    트리거가 아닌 문자 앞의 `'`는 우리 이스케이프가 만든 것일 수 없다."""
    assert unescape_formula_cell("'일반텍스트") == "'일반텍스트"


# ── render_csv 통합 — 타입 경계 ────────────────────────────────────────────


def test_string_cells_are_escaped_but_numbers_are_not() -> None:
    """음수 -5가 `'-5`로 변하면 숫자 편집이 깨진다 — 이스케이프는 str 한정이다"""
    body = render_csv(
        ("이름", "수량", "단가"),
        [("=cmd", -5, Decimal("-1.5"))],
    )
    line = body.splitlines()[1]
    assert line == "'=cmd,-5,-1.5"


def test_none_still_renders_as_empty() -> None:
    body = render_csv(("a", "b"), [(None, "x")])
    assert body.splitlines()[1] == ",x"
