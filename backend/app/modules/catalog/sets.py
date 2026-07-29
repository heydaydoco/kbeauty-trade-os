"""세트 계산 (DESIGN.md §4.2 / §8.2 / GC-E1 / WBS S1-1 DoD).

§4.2: "**세트 로트의 유통기한 = 구성품 로트 유통기한의 MIN 자동 산정**
(채널 최소기한 판정도 이 값)."

여기 있는 것은 **순수 함수**다. 로트(lots)·원장(stock_movements)은 Phase 4라
아직 없고, WBS S1-1 DoD도 "계산 함수 존재(원장 연결은 P4)"까지를 요구한다.
P4의 ASSEMBLY가 구성품 로트를 고른 뒤 그 유통기한들을 이 함수에 넘긴다.

★ 왜 지금 만드나: 규칙(MIN)과 그 규칙을 쓰는 곳(원장)을 같은 세션에서 만들면,
  규칙이 원장 코드 안에 녹아들어 따로 검증할 수 없게 된다. 규칙을 먼저 순수
  함수로 고정해 두면 P4는 그것을 호출만 하면 된다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date


class NoComponentExpiryError(ValueError):
    """구성품 유통기한이 하나도 없다.

    ★ 빈 목록을 "제한 없음"으로 해석해 None이나 먼 미래 날짜를 돌려주면, 유통기한
      없는 세트 로트가 조용히 생기고 채널 최소기한 판정(§9)이 그것을 통과시킨다.
      빈 목록은 정상이 아니라 오류다(S0-1에서 같은 형태의 사고를 겪었다 —
      PROGRESS 주의 인계 "빈 목록은 정상이 아니라 오류로 취급한다").
    """


def set_lot_expiry(component_expiries: Iterable[date]) -> date:
    """세트 로트의 유통기한 = 구성품 로트 유통기한의 최솟값 (§4.2).

        >>> set_lot_expiry([date(2027, 5, 31), date(2026, 11, 30)])
        datetime.date(2026, 11, 30)

    가장 빨리 만료되는 구성품이 세트 전체의 수명을 정한다 — 세트를 열면 그
    구성품이 먼저 못 쓰게 되기 때문이다.
    """
    dates = list(component_expiries)
    if not dates:
        raise NoComponentExpiryError(
            "구성품 유통기한이 없습니다. 세트 유통기한은 구성품에서만 나옵니다 — "
            "구성품 로트를 먼저 지정하세요."
        )
    return min(dates)
