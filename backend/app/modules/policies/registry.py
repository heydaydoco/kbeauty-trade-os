"""정책 레지스트리 — 키·값 형·미설정 동작의 코드 정본 (S3-1 ADR-0065 / design-E E8).

"값=데이터, 키·타입·기본값=코드"다(ADR-11). 키는 소비 코드와 1:1이라 **행을 추가하는 것만으로
새 동작이 생기지 않는다** — 새 키는 소비 코드+DB CHECK 재정의 마이그레이션+ADR 부기(의도된 마찰).
DB의 policy_key CHECK와 이 표의 키 집합은 아키텍처 테스트가 1:1로 대사한다.

여신 게이트는 **끄는 스위치가 없다**(§2 승인 통제 우회 통로 금지) — 그래서 여신 관련 키가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PolicyKind = Literal["MODE", "BASIS_POINTS"]

#: PI 입금 게이트 모드 값(§7.3 "경고/차단 설정") — 게이트 결과 의미는 D·E6이 정한다.
PI_GATE_MODES = ("OFF", "WARN", "BLOCK")


@dataclass(frozen=True, slots=True)
class PolicySpec:
    kind: PolicyKind
    label_ko: str
    description_ko: str
    #: 행이 없을 때의 동작 값 — **가장 엄격한 쪽**(fail-closed). 조용한 기본값이 아니라
    #: `source='UNSET_DEFAULT'`로 화면·게이트 응답·증적에 드러난다.
    unset_value: str | int
    #: MODE=허용 값 튜플, BASIS_POINTS=(하한, 상한).
    allowed: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 0


POLICY_REGISTRY: dict[str, PolicySpec] = {
    "pi_advance_gate_mode": PolicySpec(
        kind="MODE",
        label_ko="선수금 입금 게이트 모드",
        description_ko=(
            "결제유형이 선수금 T/T인 수주를 확정할 때 선수금 입금을 확인하는 강도입니다. "
            "차단=미입금이면 확정 불가, 경고=사유를 남기면 확정 가능, 끔=확인하지 않음. "
            "미설정이면 차단으로 동작합니다."
        ),
        unset_value="BLOCK",
        allowed=PI_GATE_MODES,
    ),
    "price_deviation_tolerance_bp": PolicySpec(
        kind="BASIS_POINTS",
        label_ko="단가 편차 허용치(bp)",
        description_ko=(
            "바이어 단가가 기준 단가에서 벗어나도 통과시키는 폭입니다(100bp = 1%). "
            "미설정이면 0(어떤 편차도 통과하지 않음)으로 동작합니다."
        ),
        unset_value=0,
        minimum=0,
        maximum=10_000,
    ),
}
