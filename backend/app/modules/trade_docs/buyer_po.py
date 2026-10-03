"""바이어 PO번호 정규화 — 중복 수주(같은 바이어의 같은 PO) 차단의 키 (S3-1 design-A A2·A11 / design-D ⑤ / ADR-0052).

`buyer_po_no`는 사람이 본 원문(strip만)이고 `buyer_po_no_key`가 **서버가 산출하는 비교 키**다(요청 스키마에 키 필드는 없다).
정규화 = NFKC → **대시류 통일**(하이픈 U+2010~2015·마이너스 U+2212 등 → '-' — Word·Excel 자동 교정이 만드는 en-dash 오염) →
**불가시 문자 제거**(제로폭·변형 선택자·CGJ·Hangul filler 등 Cf·Mn·Me·Cc와 채움 문자 — 엑셀·PDF 복붙 오염이 키 불일치의 주원인) →
대문자 → **모든 공백 제거**. 구두점은 대시만 통일하고 나머지는 보존한다 — 'PO-1'과 'PO1'은 서로 다른 PO다(과병합은 HARD 차단이라 오차단 위험이 더 크다). 정규화 결과가 비거나
60자를 넘으면 422다(키 열 폭 60).
"""

from __future__ import annotations

import unicodedata

from app.modules.trade_docs.validation import invalid

BUYER_PO_MAX_LENGTH = 60


#: 대시류 — NFKC 뒤에도 남는 것(전각 하이픈 U+FF0D 등은 NFKC가 이미 '-'로 접는다). 'PO–123'(en-dash)이 'PO-123'과 같은 키가 되게 한다.
_DASHES = dict.fromkeys(
    [*range(0x2010, 0x2016), 0x2043, 0x2212, 0xFE58, 0xFE63, 0x2E3A, 0x2E3B], ord("-")
)
#: 모양이 없는 한글 채움 문자(NFKC 뒤 U+1160 계열) — 카테고리가 Lo라 별도로 제거한다.
_FILLERS = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0"})
#: 범주가 So(기호)라 카테고리 필터에 안 걸리지만 **렌더가 비는** 문자 — 점자 빈칸(U+2800) 등. 눈에 같은 PO번호가 다른 키가 되는 우회를 막는다(PR-13a 적대 검토).
_BLANK_SYMBOLS = frozenset({"\u2800"})
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Mn", "Me"})
_MAX_PASSES = 4


def _clean(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_DASHES)
    kept = (
        ch
        for ch in folded
        if ch not in _FILLERS
        and ch not in _BLANK_SYMBOLS
        and unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
        and not unicodedata.category(ch).startswith("Z")
        and not ch.isspace()
    )
    return "".join(kept).upper()


def normalize_buyer_po_no(raw: str) -> str:
    """정규화 키(빈 문자열일 수 있다 — 호출자가 빈 키를 거부한다).

    `upper()`가 새 불가시·결합 문자·공백을 만들 수 있어(예: 대문자화 확장) **고정점까지 반복**한다(결과 안정성 — 키를 다시 정규화해도 같다). 수용 사항:
    앞자리 0('PO007'≠'PO7')·키릴/그리스 동형문자('РО'≠'PO')는 같은 키로 접지 않는다(과병합이 오차단이라 더 위험 — 힌트·사람 확인 몫).
    """
    current = raw
    for _ in range(_MAX_PASSES):
        cleaned = _clean(current)
        if cleaned == current:
            break
        current = cleaned
    return current


def po_columns(raw: object, *, field: str = "buyer_po_no") -> tuple[str | None, str | None]:
    """요청 원문 → (`buyer_po_no`, `buyer_po_no_key`). 비었으면 둘 다 None(PO 미기재 — QT·PI 유래 SO 등).

    원문은 strip 후 60자 이내여야 하고, 정규화 키는 비어 있지 않고 60자 이내여야 한다(422).
    """
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    if len(text) > BUYER_PO_MAX_LENGTH:
        raise invalid(field, f"바이어 PO번호는 {BUYER_PO_MAX_LENGTH}자 이내로 입력해 주세요.")
    key = normalize_buyer_po_no(text)
    if not key:
        raise invalid(field, "바이어 PO번호에 유효한 문자가 없습니다. 번호를 다시 확인해 주세요.")
    if len(key) > BUYER_PO_MAX_LENGTH:
        raise invalid(field, f"바이어 PO번호는 {BUYER_PO_MAX_LENGTH}자 이내로 입력해 주세요.")
    return text, key
