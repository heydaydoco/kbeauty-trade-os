"""게이트 공통 타입 — 결과값·해소 방식·평가 대상 (S3-1 PR-11a / design-D D3 / ADR-0069).

게이트 7종(+S5-1 채널 리스팅)을 **한 모델**로 표현한다: 결과 4값(`GateLevel`) × 해소 3값(`GateResolution`).

■ **UNKNOWN(평가 불능: 원천 부재·통화 불일치·준비도 GRAY·평가기 미등록·예외)은 통과가 아니다** — 별도 표기하되 효과는 BLOCK과 같다(fail-closed). 통과 판정은
  `gates.service.clearance` **하나**가 한다(복제 금지 — 아키텍처 스캔).
■ `GateSubject`는 **도메인 무의존** 데이터 클래스다. 인테이크·SO·(S5-1) 채널 리스팅이 자기 데이터를 어댑트해 같은 평가기를 부른다 — `gates`는 어떤 도메인 모듈도 임포트하지 않는다
  (평가기는 소비 모듈이 `gates.registry.register`로 **등록**한다 — 승인 코어의 TargetSpec과 같은 방향).
■ `GateOutcome.basis`는 판정 입력값 요약(판매 단가·기준가·허용치·수량·MOQ·준비 상태 요약)만 담는다 — **원가·마진·매입가·내부 예외 문자열 금지**(원가 마스킹 계약).
  `basis_hash`는 `{gate_code, line_id, level, reason_code, basis}`의 canonical JSON sha256이며 override의 결속 값이다: 판정 입력이 바뀌면 해시가 달라져 기존 override는 자동 무효다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any

from app.modules.identity.models import RoleCode


class GateLevel(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    #: 평가 불능 — 통과가 아니다(효과는 BLOCK). 화면은 BLOCK과 다른 표기로 보여 준다.
    UNKNOWN = "UNKNOWN"


class GateResolution(StrEnum):
    #: 해소 수단 없음 — 데이터를 고쳐야 한다.
    NONE = "NONE"
    #: 권한자 + 사유로 통과(별도 액션, 판정 해시 결속).
    OVERRIDE = "OVERRIDE"
    #: 승인 소비로 해소(여신 초과).
    APPROVAL = "APPROVAL"


class GateCode(StrEnum):
    """게이트 코드 7종 — 표시 순서(통합 X-35)이기도 하다."""

    ITEM_MAPPING = "ITEM_MAPPING"
    DUPLICATE_PO = "DUPLICATE_PO"
    PRICE_DEVIATION = "PRICE_DEVIATION"
    CREDIT = "CREDIT"
    MARKET_READINESS = "MARKET_READINESS"
    MOQ = "MOQ"
    PI_DEPOSIT = "PI_DEPOSIT"


class GatePhase(StrEnum):
    """평가 시점 — 인테이크 검토(실시간 계산·정보)와 SO 확정(잠금 하 재평가·권위)."""

    INTAKE = "INTAKE"
    CONFIRM = "CONFIRM"


#: `GateSubject.kind` 값 — 평가 대상의 종류. SO는 DB `subject_type`(증거·override 저장)과 같은 문자열이고, INTAKE는 **저장하지 않는 실시간 계산 대상**이다(인테이크 검토 시점 — 증거·override 없음).
SUBJECT_INTAKE = "INTAKE"

#: 판정 근거 값의 형 — JSON 스칼라만(중첩 금지).
BasisValue = int | str | bool | None


@dataclass(frozen=True, slots=True)
class GateLine:
    """평가 대상 라인 — 판매 단가·수량 등 스냅샷 값만(원가 없음)."""

    line_id: int
    line_no: int
    sku_id: int | None
    buyer_item_code: str | None
    quantity: int
    unit_price_amount: int
    #: 기준 판가(최소단위) — 인테이크=마스터 판가 조회, SO=라인 생성 시점 스냅샷. 없으면 None(→ UNKNOWN).
    list_price_amount: int | None
    is_free: bool


@dataclass(frozen=True, slots=True)
class GateSubject:
    """게이트 평가 대상 — 도메인 무의존. 인테이크·SO·채널 리스팅이 어댑트한다."""

    kind: str
    id: int
    buyer_partner_id: int
    currency: str
    dest_market_code: str | None
    lines: tuple[GateLine, ...]
    total_amount: int
    #: 바이어 PO번호 정규화 키(중복 PO 판정 입력 — 읽기만, SO 서비스가 만든 값을 그대로 옮긴다).
    po_no_key: str | None = None
    payment_type: str | None = None
    advance_pct_bp: int | None = None
    pi_id: int | None = None
    #: True면 호출자가 거래처 잠금을 이미 쥔 확정 통로의 권위 평가다(CREDIT이 잠금 하 평가를 한다). 조회(`GET /gates`)는 False — 참고값.
    authoritative: bool = False


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """게이트 1건(또는 게이트×라인 1건)의 판정 결과."""

    gate_code: str
    line_id: int | None
    level: GateLevel
    resolution: GateResolution
    reason_code: str
    message_ko: str
    basis: Mapping[str, BasisValue] = field(default_factory=dict)
    #: 이 결과에 override를 부여할 수 있는 역할(해소가 OVERRIDE일 때만 값이 있다).
    override_roles: tuple[RoleCode, ...] = ()
    #: 표시 전용 상세 — **판정 입력이 아니다**(해시·증거에 들어가지 않음). 노출 역할 제한이 필요한 값(다른 문서번호 등)을 basis와 분리해 담는다.
    detail: Mapping[str, BasisValue] = field(default_factory=dict)

    @property
    def basis_hash(self) -> str:
        """판정 결속 해시 — 판정 입력이 바뀌면 달라진다(메시지 문구는 입력이 아니다)."""
        payload: dict[str, Any] = {
            "gate_code": self.gate_code,
            "line_id": self.line_id,
            "level": self.level.value,
            "reason_code": self.reason_code,
            "basis": dict(self.basis),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def key(self) -> tuple[str, int | None]:
        """(게이트, 라인) — 한 평가에서 유일해야 한다."""
        return (self.gate_code, self.line_id)
