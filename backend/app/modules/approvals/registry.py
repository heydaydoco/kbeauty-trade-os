"""TargetSpec 레지스트리 — 승인 대상 도메인이 자기 대상을 승인 코어에 **등록**한다 (S3-1 ADR-0060 / design-C C6).

승인 모듈은 어떤 도메인 모듈도 임포트하지 않는다(모듈 DAG: approvals는 플랫폼 계층). 대신 소비 모듈이
`TargetSpec`을 등록한다 — "이 승인 유형의 대상은 이런 모양이고, 지금 승인이 허용하는 금액·결속 digest는 이것이다"를
알려 주는 계약이다. 등록은 앱 조립 시점(`api/router.py`)에 한 번 일어난다(`register_target_spec`은 같은 유형의 2차 등록을 거부한다).

★ **등록 없는 유형은 요청·결정·소비 모두 실패한다(fail-closed)** — 대상을 모르는 채 승인을 내주지 않는다.
★ **소비 없는 spec 금지**: `consumer_module` 소스에 `consume_approval(` 호출이 있어야 한다(소비 접점 스캔 — PR-12가 호출을 더하면서
  스캔을 켠다. PR-9a 시점에는 spec만 있고 소비자가 없어 스캔은 PR-12 몫이다 — 한 PR 창 동안의 부재는 PROGRESS에 기록).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.modules.approvals.machine import APPROVAL_TYPES, TYPE_TARGET


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    """승인 시점·소비 시점에 대상 모듈이 알려 주는 "지금의 사실"."""

    #: 표시명(전표번호 등) — 요청 시점에 approvals.target_label로 동결된다.
    label: str
    #: 판정 입력 동결 필드 집합의 sha256 hex(64자). version이 아니라 **내용**이다(메모·담당자 제외).
    digest: str
    #: **지금 승인이 필요한 금액**(SO_CREDIT_EXCEEDED: 여신 통화 기준 초과분). 0 이하면 승인이 필요 없다.
    amount: int
    currency: str
    #: 표시용 상세 — 스칼라(int/str/bool/None)만·키 20개 이하·4KB 이하·민감 키 금지(서비스가 검증). 판정에 쓰지 않는다.
    detail: dict[str, Any] = field(default_factory=dict)
    #: 대상이 아직 게이트 대상 상태인가(확정 전·미취소·미삭제).
    gate_open: bool = True


#: `snapshot(session, target_id, *, lock) -> TargetSnapshot | None`
#: - `lock=True`: **대상 행을 확정 통로와 같은 순서로 잠근다**(거래처 → 대상 — LOCK_ORDER). 대상이 없거나 삭제됐으면 None.
#: - 평가 불능이면 spec이 자기 `AppError`를 던진다(통과 취급 금지 — fail-closed).
SnapshotFn = Callable[..., TargetSnapshot | None]


@dataclass(frozen=True, slots=True)
class TargetSpec:
    approval_type: str
    target_type: str
    #: 이 유형을 소비하는(= `consume_approval`을 호출하는) 모듈 이름 — 소비 접점 스캔의 대상.
    consumer_module: str
    snapshot: SnapshotFn


_SPECS: dict[str, TargetSpec] = {}


def register_target_spec(spec: TargetSpec) -> None:
    """spec 등록 — 알려진 유형·대상 쌍만, 같은 유형의 중복 등록은 거부한다."""
    if spec.approval_type not in APPROVAL_TYPES:
        raise ValueError(f"알 수 없는 승인 유형입니다: {spec.approval_type!r}")
    if TYPE_TARGET[spec.approval_type] != spec.target_type:
        raise ValueError(
            f"{spec.approval_type}의 대상 종류는 {TYPE_TARGET[spec.approval_type]}입니다"
            f"(받은 값: {spec.target_type!r})."
        )
    if spec.approval_type in _SPECS:
        raise ValueError(f"{spec.approval_type}의 TargetSpec은 이미 등록돼 있습니다.")
    _SPECS[spec.approval_type] = spec


def get_target_spec(approval_type: str) -> TargetSpec:
    """등록된 spec — 없으면 RuntimeError(요청·결정·소비를 **통과시키지 않는다**: fail-closed)."""
    spec = _SPECS.get(approval_type)
    if spec is None:
        raise RuntimeError(
            f"승인 유형 {approval_type!r}의 TargetSpec이 등록되지 않았습니다 — "
            "소비 모듈이 api/router.py 조립 시점에 등록해야 합니다."
        )
    return spec


def registered_specs() -> dict[str, TargetSpec]:
    return dict(_SPECS)


def unregister_for_tests(approval_type: str) -> TargetSpec | None:
    """테스트 전용 — 등록을 빼고 돌려준다(복원은 호출부 책임)."""
    return _SPECS.pop(approval_type, None)
