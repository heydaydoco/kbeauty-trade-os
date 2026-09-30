"""Incoterms 3열 — 코드·장소·연도 (S3-1 ADR-0055 / design-A A6 / DESIGN §7.5).

2020판 11종 + 2010판 DAT. 연도는 데이터다(§7.5 문면이 연도를 요구 — 상수열이면 죽은 열). DAT는 2010판에만,
DPU는 2020판에만 존재한다(DB 교차 CHECK와 같은 규칙). 값 입력·형식 검증뿐이고 판정 문구는 만들지 않는다
(법적 판정 아님 — 완전성 검증 본체는 S3-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.trade_docs.constants import DEFAULT_INCOTERM_YEAR, INCOTERM_YEARS, IncotermCode

FIELD = "incoterm"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class Incoterm:
    code: str
    place: str
    year: int

    def columns(self) -> dict[str, Any]:
        return {
            "incoterm_code": self.code,
            "incoterm_place": self.place,
            "incoterm_year": self.year,
        }


EMPTY_INCOTERM_COLUMNS: dict[str, Any] = {
    "incoterm_code": None,
    "incoterm_place": None,
    "incoterm_year": None,
}


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def build_incoterm(payload: dict[str, Any] | None) -> Incoterm | None:
    """요청 본문 → 검증된 Incoterms. None이면 3열 전부 NULL(작성 중 미정 허용)."""
    if payload is None:
        return None
    try:
        code = IncotermCode(str(payload.get("code"))).value
    except ValueError:
        raise _invalid(f"{FIELD}.code", "Incoterms 코드를 확인해 주세요.") from None
    place = str(payload.get("place") or "").strip()
    if not place or len(place) > 100 or _CONTROL.search(place):
        raise _invalid(
            f"{FIELD}.place", "Incoterms 장소를 100자 이내로 입력해 주세요(개행·제어문자 불가)."
        )
    year = payload.get("year", DEFAULT_INCOTERM_YEAR)
    if isinstance(year, bool) or year not in INCOTERM_YEARS:
        raise _invalid(f"{FIELD}.year", "Incoterms 판은 2010 또는 2020이어야 합니다.")
    if code == IncotermCode.DAT.value and year != 2010:
        raise _invalid(
            f"{FIELD}.year", "DAT는 2010판에만 있습니다. DPU(2020판)를 쓰시려면 코드를 바꿔 주세요."
        )
    if code == IncotermCode.DPU.value and year != 2020:
        raise _invalid(f"{FIELD}.year", "DPU는 2020판에만 있습니다.")
    return Incoterm(code, place, year)
