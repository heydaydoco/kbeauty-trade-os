"""유효기간 경과 판정 (S3-1 design-B B7) — 만료 스윕(PR-6)과 후속 생성 가드가 **공유**하는 술어.

유효기간은 `valid_until` **당일 KST 24:00까지 유효**하다(포함 경계) — `today_kst() > valid_until`일 때만 경과.
QT는 발행·전환 상태에서, PI는 **미입금 발행 상태에서만** 경과가 의미를 갖는다(일부입금·입금완료는 입금으로 수락이
이행되어 유효기간 경과가 SO 생성을 막지 않는다 — 선수금이 갇히는 사고 방지).
"""

from __future__ import annotations

from datetime import date

from app.modules.trade_docs.constants import DocKind

#: 만료 스윕(경과 → EXPIRED)·만료 임박 D-N 알림(S3-2 PR-6 `trade_chain.deadline_scan`)의 **공통 후보 상태** — 미입금 발행(ISSUED)뿐
#: (일부입금·입금완료 PI·전환 QT는 상태로 비대상 — design-B B18 "후보는 만료 스윕과 같은 정의, 둘로 갈리면 안 된다").
#: 후보의 나머지 조건(삭제 아님·`valid_until` 있음·살아 있는 후속 없음)도 두 경로가 같은 술어(`is_lapsed`·`has_live_children`)를 쓴다.
EXPIRY_CANDIDATE_STATUS = "ISSUED"

#: 경과 판정 대상 상태.
LAPSE_STATUSES: dict[DocKind, frozenset[str]] = {
    DocKind.QUOTATION: frozenset({"ISSUED", "CONVERTED"}),
    DocKind.PROFORMA_INVOICE: frozenset({"ISSUED"}),
}


def is_lapsed(kind: DocKind, status: str, valid_until: date | None, today: date) -> bool:
    if valid_until is None or status not in LAPSE_STATUSES.get(kind, frozenset()):
        return False
    return valid_until < today
