"""바이어 PO번호 정규화 — 중복 수주(같은 바이어의 같은 PO) 차단의 키 (S3-1 design-A A2·A11 / design-D ⑤ / ADR-0052).

`buyer_po_no`는 사람이 본 원문(strip만)이고 `buyer_po_no_key`가 **서버가 산출하는 비교 키**다(요청 스키마에 키 필드는 없다).
정규화 = NFKC → **제로폭(유니코드 Cf) 문자 제거**(엑셀·PDF 복붙 오염이 키 불일치의 주원인) → 대문자 → **모든 공백 제거**.
구두점은 보존한다 — 'PO-1'과 'PO1'은 서로 다른 PO다(과병합은 HARD 차단이라 오차단 위험이 더 크다). 정규화 결과가 비거나
60자를 넘으면 422다(키 열 폭 60).
"""

from __future__ import annotations

import unicodedata

from app.modules.trade_docs.validation import invalid

BUYER_PO_MAX_LENGTH = 60


def normalize_buyer_po_no(raw: str) -> str:
    """정규화 키(빈 문자열일 수 있다 — 호출자가 빈 키를 거부한다)."""
    folded = unicodedata.normalize("NFKC", raw)
    kept = (
        ch
        for ch in folded
        if unicodedata.category(ch) != "Cf"
        and not unicodedata.category(ch).startswith("Z")
        and not ch.isspace()
    )
    return "".join(kept).upper()


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
