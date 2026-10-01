"""미수채권 provider — 여신 노출의 미수 항 (S3-1 ADR-0064 / design-E E1 4).

S3-1에는 채권·미수 자체가 없다(S3-3 소관). 기본 구현 `UnreflectedReceivables`는 **`(reflected=False, amount=None)`만 반환한다 — 0 반환 금지**
(타입상 `None`): "못 셈"을 "없음(0)"과 같게 만들지 않는다. 평가 결과에 `receivables_reflected=false`·`exposure_is_partial=true`가 실리고
화면이 '미수 미반영' 배지를 항상 표시한다(색+글자). 이것이 "평가 불능을 통과로 취급 금지" 원칙의 **유일한 의도적 예외**다(막으면 S3-3 전 모든 확정이
영구 정지) — 대신 fail-visible 표시 의무를 진다. 실질 위험은 **시스템 도입 전 이월 미수**뿐이며 runbook(잔여 한도로 설정)으로 완화한다(PROGRESS P-51).

★ provider 등록은 1회만 — 두 번째 등록은 예외다. provider가 **예외를 던지면 통과가 아니라 UNEVALUABLE**(`RECEIVABLE_PROVIDER_ERROR`)로 확정 거부한다(fail-closed).
★ S3-3이 실 provider를 등록하는 릴리스에서 `test_receivable_provider_is_not_default_after_s33`가 기본 구현 잔존을 실패시킨다(S3-3 DoD) —
  선적분 차감(`open_order_amount`)도 같은 PR에서만 바꾼다(선적 후~미수 발생 전 노출 공백·이중 계산 방지).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class ReceivableTerm:
    #: 미수가 노출에 반영됐는가. False면 amount는 반드시 None이다.
    reflected: bool
    amount: int | None

    def __post_init__(self) -> None:
        if self.reflected != (self.amount is not None):
            raise ValueError(
                "미수 항은 reflected=True일 때만 금액을 가진다(0으로 대신하지 않는다)."
            )
        if self.amount is not None and self.amount < 0:
            raise ValueError(
                "미수 금액은 음수일 수 없다(노출을 줄이는 방향의 오염 — 평가 불능으로 처리)."
            )


class ReceivableExposureProvider(Protocol):
    def outstanding(
        self, session: Session, partner_id: int, limit_currency: str
    ) -> ReceivableTerm: ...


class UnreflectedReceivables:
    """S3-1 기본 구현 — 미수는 **반영되지 않았다**(값 없음). 0을 돌려주지 않는다."""

    def outstanding(self, session: Session, partner_id: int, limit_currency: str) -> ReceivableTerm:
        return ReceivableTerm(reflected=False, amount=None)


_DEFAULT: ReceivableExposureProvider = UnreflectedReceivables()
_provider: ReceivableExposureProvider = _DEFAULT


def register_receivable_provider(provider: ReceivableExposureProvider) -> None:
    """실 provider 등록(S3-3) — 1회만. 이미 등록돼 있으면 예외(조용한 덮어쓰기 금지)."""
    global _provider
    if _provider is not _DEFAULT:
        raise RuntimeError("미수채권 provider는 이미 등록되어 있습니다(1회만 등록).")
    _provider = provider


def get_receivable_provider() -> ReceivableExposureProvider:
    return _provider


def is_default_provider() -> bool:
    """기본 구현(미반영)이 아직 남아 있는가 — S3-3 DoD 테스트가 쓴다."""
    return _provider is _DEFAULT


def reset_receivable_provider_for_tests() -> None:
    """테스트 전용 — 기본 구현으로 되돌린다."""
    global _provider
    _provider = _DEFAULT
