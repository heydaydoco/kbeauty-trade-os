"""게이트 명세 단일 출처 — `GATE_SPECS`·`OVERRIDE_ROLES` (S3-1 PR-11a / design-D D3·D4 / design-integrated X-27·X-36 / ADR-0069).

DESIGN §7.4 보강의 게이트 명세표가 **코드로는 이 상수 하나**다: 게이트별 평가 시점·도달 가능한 (결과, 해소) 조합·override 허용 역할.
평가기가 명세에 없는 조합을 돌려주면 `outcome()`이 ValueError를 던지고 평가 틀이 그것을 UNKNOWN(EVALUATION_ERROR)으로 바꾼다 — 명세 밖의 결과가 조용히 통과로 흐르지 않는다.
메타 테스트(`GATE_SPECS`가 선언한 조합마다 최소 1개 테스트)가 공회전을 막는다.

■ **override 가능 게이트는 4종뿐**이다: PRICE_DEVIATION·MOQ = TRADE·ADMIN / MARKET_READINESS·PI_DEPOSIT = ADMIN. ITEM_MAPPING·DUPLICATE_PO(데이터 무결성)·CREDIT(승인 게이트)은 override 자체가 불가하며
  DB `gate_overrides.gate_code` CHECK가 같은 집합을 강제한다(대사 테스트). LOGISTICS·CERT·VIEWER는 어느 override도 못 한다(SoD는 승인의 몫 — override에 별도 SoD 없음).
■ UNKNOWN/NONE은 **모든 게이트가 가질 수 있는 틀 결과**다(평가기 미등록·평가 중 예외 — `service.evaluate_all`) — 게이트별 선언과 별개로 허용된다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.modules.gates.types import (
    BasisValue,
    GateCode,
    GateLevel,
    GateOutcome,
    GatePhase,
    GateResolution,
)
from app.modules.identity.models import RoleCode

_BOTH = frozenset({GatePhase.INTAKE, GatePhase.CONFIRM})
_CONFIRM_ONLY = frozenset({GatePhase.CONFIRM})
_INTAKE_ONLY = frozenset({GatePhase.INTAKE})

L = GateLevel
R = GateResolution


@dataclass(frozen=True, slots=True)
class ResultSpec:
    """게이트가 낼 수 있는 (결과, 해소) 조합 하나 — `phases`는 그 조합이 나올 수 있는 평가 시점."""

    level: GateLevel
    resolution: GateResolution
    phases: frozenset[GatePhase] = _BOTH


@dataclass(frozen=True, slots=True)
class GateSpec:
    code: GateCode
    #: 이 게이트가 평가되는 시점(인테이크 검토·SO 확정).
    phases: frozenset[GatePhase]
    results: tuple[ResultSpec, ...]


#: 게이트 명세표(정본). 순서 = 표시 순서(통합 X-35).
GATE_SPECS: dict[GateCode, GateSpec] = {
    GateCode.ITEM_MAPPING: GateSpec(
        GateCode.ITEM_MAPPING,
        _BOTH,
        (
            ResultSpec(L.PASS, R.NONE),
            # 미매핑·거래처 BUYER 상실·SKU 삭제·(확정 시점) 단종 SKU — 마스터를 고쳐야 한다.
            ResultSpec(L.BLOCK, R.NONE),
            # 단종 SKU는 접수(인테이크)에서는 경고 — 확정에서는 BLOCK(마스터 상태를 되돌리는 가시적 조치가 선행, A11-2).
            ResultSpec(L.WARN, R.NONE, _INTAKE_ONLY),
        ),
    ),
    GateCode.DUPLICATE_PO: GateSpec(
        GateCode.DUPLICATE_PO,
        _BOTH,
        (
            ResultSpec(L.PASS, R.NONE),
            # 같은 (거래처, 바이어 PO 키)의 다른 비취소 SO — 데이터 무결성이라 override 불가.
            ResultSpec(L.BLOCK, R.NONE),
            # SO에 바이어 PO번호가 없다(QT·PI 유래 SO는 정상) — 중복 확인 불가를 기록으로 남긴다.
            ResultSpec(L.WARN, R.NONE),
        ),
    ),
    GateCode.PRICE_DEVIATION: GateSpec(
        GateCode.PRICE_DEVIATION,
        _BOTH,
        (
            ResultSpec(L.PASS, R.NONE),
            ResultSpec(L.WARN, R.NONE),  # 무상 라인
            ResultSpec(L.BLOCK, R.OVERRIDE),  # 허용치 초과
            ResultSpec(L.UNKNOWN, R.OVERRIDE),  # 기준가 없음·기준가≤0
        ),
    ),
    GateCode.CREDIT: GateSpec(
        GateCode.CREDIT,
        _CONFIRM_ONLY,
        (
            ResultSpec(L.PASS, R.NONE),  # 한도 이내·한도 NULL(여신 관리 안 함)
            ResultSpec(L.BLOCK, R.APPROVAL),  # 한도 초과 — 승인 소비로만 해소(override 불가)
            # 평가 불능(환율 부재·미수 조회 실패)은 UNKNOWN/NONE(틀 결과) — 승인 경로 없이 확정 거부(X-27).
        ),
    ),
    GateCode.MARKET_READINESS: GateSpec(
        GateCode.MARKET_READINESS,
        _BOTH,
        (
            ResultSpec(L.PASS, R.NONE),
            ResultSpec(L.WARN, R.NONE),  # 진행 중인 요건
            ResultSpec(L.BLOCK, R.OVERRIDE),  # 미충족 요건
            ResultSpec(
                L.UNKNOWN, R.OVERRIDE
            ),  # 필수 요건 0건(GRAY)·시장/SKU 소멸 — 통과로 읽지 않는다
        ),
    ),
    GateCode.MOQ: GateSpec(
        GateCode.MOQ,
        _BOTH,
        (
            ResultSpec(L.PASS, R.NONE),  # 이내·`moq` NULL(정책 없음은 평가 실패가 아니다)
            ResultSpec(L.BLOCK, R.OVERRIDE),
            # SKU 소멸 등 판독 불능은 UNKNOWN/NONE(틀 결과) — ITEM_MAPPING이 먼저 막는다.
        ),
    ),
    GateCode.PI_DEPOSIT: GateSpec(
        GateCode.PI_DEPOSIT,
        _CONFIRM_ONLY,
        (
            ResultSpec(L.PASS, R.NONE),  # 비선수금·모드 OFF(스킵 사실 기록)·입금 충족
            ResultSpec(L.WARN, R.NONE),  # 모드 WARN·미충족 — 진행하되 확정 증거에 기록
            ResultSpec(L.BLOCK, R.OVERRIDE),  # 모드 BLOCK·입금 부족
            ResultSpec(L.UNKNOWN, R.OVERRIDE),  # 모드 BLOCK·PI 미연결/사용 불가
            # 결제유형 부재 등은 UNKNOWN/NONE(틀 결과) — 결제조건이 정해져야 판정할 수 있다.
        ),
    ),
}

#: 모든 게이트가 가질 수 있는 틀 결과(미등록·예외 — 해소 수단 없음).
FRAMEWORK_RESULT = ResultSpec(L.UNKNOWN, R.NONE)

#: 표시·평가 순서.
GATE_ORDER: tuple[GateCode, ...] = tuple(GATE_SPECS)

#: override 허용 역할(서비스가 검증 — 라우터가 아니라 서비스, 이중 방어). 여기 없는 게이트는 override 자체가 불가능하다.
OVERRIDE_ROLES: dict[GateCode, tuple[RoleCode, ...]] = {
    GateCode.PRICE_DEVIATION: (RoleCode.TRADE, RoleCode.ADMIN),
    GateCode.MOQ: (RoleCode.TRADE, RoleCode.ADMIN),
    GateCode.MARKET_READINESS: (RoleCode.ADMIN,),
    GateCode.PI_DEPOSIT: (RoleCode.ADMIN,),
}

OVERRIDABLE_GATES: frozenset[str] = frozenset(code.value for code in OVERRIDE_ROLES)


def declared_results(code: GateCode) -> frozenset[tuple[GateLevel, GateResolution]]:
    """게이트가 낼 수 있는 (결과, 해소) 조합 — 선언분 + 틀 결과."""
    spec = GATE_SPECS[code]
    return frozenset(
        {(r.level, r.resolution) for r in spec.results}
        | {(FRAMEWORK_RESULT.level, FRAMEWORK_RESULT.resolution)}
    )


def outcome(
    gate_code: GateCode | str,
    level: GateLevel,
    resolution: GateResolution,
    reason_code: str,
    message_ko: str,
    basis: Mapping[str, BasisValue] | None = None,
    *,
    line_id: int | None = None,
) -> GateOutcome:
    """GateOutcome 팩토리 — 명세(`GATE_SPECS`)에 없는 (결과, 해소) 조합이면 ValueError.

    `override_roles`는 명세에서 채운다(해소가 OVERRIDE일 때만). 평가기는 이 함수로만 결과를 만든다(명세 단일 출처).
    """
    code = GateCode(gate_code)
    if (level, resolution) not in declared_results(code):
        raise ValueError(
            f"{code.value}에 선언되지 않은 결과 조합입니다: {level.value}/{resolution.value}"
        )
    roles = OVERRIDE_ROLES.get(code, ()) if resolution is GateResolution.OVERRIDE else ()
    return GateOutcome(
        gate_code=code.value,
        line_id=line_id,
        level=level,
        resolution=resolution,
        reason_code=reason_code,
        message_ko=message_ko,
        basis=dict(basis or {}),
        override_roles=roles,
    )
