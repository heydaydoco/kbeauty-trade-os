"""K. 바이어 PO번호 정규화 — 중복 수주 차단의 비교 키 (S3-1 PR-7a / design-A A2·A11 / design-D ⑤).

정규화 = NFKC → 제로폭(유니코드 Cf) 문자 제거 → 대문자 → **모든 공백 제거**, 구두점은 보존한다('PO-1'≠'PO1' — 과병합은 HARD 차단이라
오차단 위험이 더 크다). 정규화 결과가 비거나 60자를 넘으면 422다. 이 키는 서버만 만들고 요청 스키마에는 필드가 없다.
"""

from __future__ import annotations

import pytest

from app.core.errors.exceptions import AppError
from app.modules.trade_docs.buyer_po import (
    BUYER_PO_MAX_LENGTH,
    normalize_buyer_po_no,
    po_columns,
)

pytestmark = pytest.mark.group_k


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("PO-2026-001", "PO-2026-001"),
        ("po-2026-001", "PO-2026-001"),  # 대문자
        ("  PO-2026-001\t", "PO-2026-001"),  # 앞뒤 공백·탭
        ("P O - 2026 - 001", "PO-2026-001"),  # 중간 공백
        ("PO -2026 -001", "PO-2026-001"),  # NBSP·em space(NFKC가 일반 공백으로)
        ("PO​-2026‍-001﻿", "PO-2026-001"),  # 제로폭 공백·ZWJ·BOM(엑셀·PDF 복붙 오염)
        ("ＰＯ－２０２６－００１", "PO-2026-001"),  # 전각 → NFKC 반각
        ("po\n-2026\r\n-001", "PO-2026-001"),  # 줄바꿈
        # 대시류 통일 — Word·Excel 자동 교정이 만드는 en-dash·em-dash 등(2026-09-30 보강)
        ("PO\u2013123", "PO-123"),
        ("PO\u2014123", "PO-123"),
        ("PO\u2010123", "PO-123"),
        ("PO\u2011123", "PO-123"),
        ("PO\u2012123", "PO-123"),
        ("PO\u2015123", "PO-123"),
        ("PO\u2212123", "PO-123"),  # 마이너스 기호
        ("PO\ufe58123", "PO-123"),
        # 불가시 문자 — 변형 선택자·CGJ·Hangul filler·결합 표식(Mn/Cf)
        ("PO-\ufe0f123", "PO-123"),
        ("PO-\u034f123", "PO-123"),
        ("PO\u3164-123", "PO-123"),
        ("PO\u115f-\u1160123", "PO-123"),
        ("PO\uffa0-123", "PO-123"),
        ("PO-\U000e0100123", "PO-123"),  # 변형 선택자 보충
        ("PO-\u180b123", "PO-123"),  # 몽골 자유 변형 선택자
        ("PO-\x00123\x07", "PO-123"),  # 제어 문자
        ("ß-1", "SS-1"),  # 대문자화 규칙(길이가 늘 수 있다 — 60자 검사는 키 기준)
    ],
)
def test_equivalent_spellings_share_one_key(raw: str, key: str) -> None:
    """소문자·공백·탭·NBSP·제로폭·전각·줄바꿈 변형이 모두 같은 키로 모인다"""
    assert normalize_buyer_po_no(raw) == key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("PO-1", "PO1"),  # 구두점 보존 — 과병합 금지
        ("PO_1", "PO-1"),
        ("PO/1", "PO-1"),
        ("PO-1", "PO-01"),  # 선행 0은 다른 번호
        ("PO.1", "PO1"),
        ("PO-123", "PO-124"),  # 정상 케이스 — 다른 번호는 다른 키
        ("PO\u2013123", "PO-1234"),
        ("PO\u2013123", "PO123"),  # 대시는 통일할 뿐 지우지 않는다
        ("PO\u2013123", "PO_123"),
    ],
)
def test_punctuation_and_digits_are_preserved(a: str, b: str) -> None:
    """구두점·숫자는 그대로 비교한다 — 서로 다른 PO가 한 키로 합쳐지지 않는다"""
    assert normalize_buyer_po_no(a) != normalize_buyer_po_no(b)


def test_po_columns_returns_the_stripped_original_and_the_key() -> None:
    """원문은 strip만, 키는 정규화 — 비었으면 둘 다 None(PO 미기재)"""
    assert po_columns("  po-1 x ") == ("po-1 x", "PO-1X")
    for blank in (None, "", "   ", "\t\n"):
        assert po_columns(blank) == (None, None)


@pytest.mark.parametrize("raw", ["​", "​‍﻿", " ​ "])
def test_a_value_with_no_visible_character_is_rejected(raw: str) -> None:
    """제로폭 문자뿐인 값은 '비었음'이 아니라 오염 입력이라 422 — 조용히 PO 미기재로 받지 않는다(공백뿐인 값과 구분)"""
    if not raw.strip():  # 일반 공백뿐이면 strip이 비워 '미기재'
        assert po_columns(raw) == (None, None)
        return
    with pytest.raises(AppError) as caught:
        po_columns(raw)
    assert caught.value.code == "COMMON.VALIDATION.INVALID_FIELD"
    assert "buyer_po_no" in (caught.value.detail or {})


def test_length_limits_apply_to_the_original_and_to_the_key() -> None:
    """원문 60자는 허용·61자는 422 · 키 기준 길이도 60자 제한(대문자화로 늘어난 경우 422)"""
    assert po_columns("A" * BUYER_PO_MAX_LENGTH)[1] == "A" * BUYER_PO_MAX_LENGTH
    for too_long in ("A" * (BUYER_PO_MAX_LENGTH + 1), "ß" * 31):  # ß×31 → 키 SS×31=62자
        with pytest.raises(AppError):
            po_columns(too_long)


def test_the_normalizer_is_idempotent() -> None:
    """정규화는 멱등이다 — 키를 다시 정규화해도 같다(저장 키를 비교 입력으로 재사용해도 안전)"""
    for raw in ("po - 1", "ＰＯ-２", "a​b"):
        key = normalize_buyer_po_no(raw)
        assert normalize_buyer_po_no(key) == key
