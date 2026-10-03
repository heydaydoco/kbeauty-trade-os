"""보이지 않는 글자 판정 — 사람이 입력하는 짧은 텍스트(사유·이름·검색어)의 공용 위생 규칙.

판정 대상(코드포인트 기준): 유니코드 범주 **Cc**(제어 — C0 0x00-0x1F·DEL·C1 0x80-0x9F, NUL 포함)·**Cf**(서식 — 제로폭 U+200B·방향 제어 U+202E 등)·
**Zl**(줄 구분 U+2028)·**Zp**(문단 구분 U+2029)와 **한글 채움 문자**(U+115F·U+1160·U+3164·U+FFA0). 눈에 안 보이는 글자로 길이·이름 유일성을
속이거나(같아 보이는 두 이름), 화면·CSV의 표시 방향을 뒤집거나, DB에 NUL을 보내 500을 만드는 경로를 입력 경계에서 막는다.

★ DB CHECK `!~ '[[:cntrl:]]'`(PG16)는 로캘과 무관하게 **0x00-0x1F·0x7F-0x9F(C0·C1 제어)만** 잡는다 — Zl·Zp·Cf·한글 채움은 못 잡는다.
  그래서 이 판정이 1차 방어선이고 DB CHECK는 최후 방어선이다(둘은 같지 않다).
"""

from __future__ import annotations

import unicodedata

INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
HANGUL_FILLERS = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0"})


def is_invisible_char(char: str) -> bool:
    """한 글자가 판정 대상인가."""
    return char in HANGUL_FILLERS or unicodedata.category(char) in INVISIBLE_CATEGORIES


def invisible_char_problem(value: str, *, label: str) -> str | None:
    """원문 전체에 보이지 않는 글자가 있으면 사용자용 한국어 문구, 없으면 None. **strip 전에** 원문 그대로 검사한다."""
    if any(is_invisible_char(char) for char in value):
        return f"{label}에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다."
    return None
