"""시장 준비도 매트릭스 집계 규칙 — 순수 함수 (DB·세션 없음).

DESIGN §5.3: 행 SKU × 열 시장, 셀 = 필수 요건 집계 신호등 4색(🟢판매가능 /
🟡진행·임박 / 🔴미충족 / ⚪대상외), **저장하지 않고 계산한다**. 이 파일은 그 계산의
규칙 전부이고, DB를 모른다 — 서비스가 사실(요건·인스턴스)을 모아 오면 여기서 색이
정해진다. 규칙이 순수 함수라야 상태 11값 전수를 파라미터라이즈로 고정할 수 있다.

■ 색 매핑 (S2-3 판정 요청 1 — 표 하나가 정본)
    🟢 승인(실효)                          — 임박 아님·도과 아님·무기한 포함
    🟡 만료임박 / 서류준비·신청제출·심사중·보완요청 / 갱신중(미도과)
    🔴 활성 인스턴스 부재 · 미착수 · 만료 · 갱신중 도과 · 반려·중단(종결 — 활성 아님)
    ⚪ 필수 요건 0건(대상외)
  **미착수 = 🔴** — 착수 사실이 0이면 실질 미충족이다(🟡로 두면 "진행 중"이라는
  거짓 신호가 된다). **셀 = 최악값 집계**(🔴 > 🟡 > 🟢, ⚪은 요건이 하나도 없을 때만).

■ 실효 상태 — 저장 상태를 그대로 믿지 않는다
  달력 파생 3태(승인·만료임박·만료)는 만료일·리드타임의 순수 함수이므로, 스윕이
  아직 안 돈 창(만료일 지난 날 06:00 전)에도 그 자리에서 다시 계산한다
  (`certifications.calendar.effective_status`). 매트릭스는 스윕의 지각에 종속되지
  않는다 — "만료일 변경 → 매트릭스 즉시 반영"(DoD)의 다른 절반이다.

■ 법적 판정이 아니다 (§1 비범위·§15 L3 금지 4영역 비저촉)
  이 집계는 인스턴스 상태의 기계적 요약이다. 요건이 충족됐는지의 판단은 사람이
  전이(승인)로 기록한 것이고, 여기서 새 판정을 만들지 않는다. 🟢의 라벨
  "판매가능"은 §5.3 명문이며, 모집합 밖 축(성분)은 범례가 공개한다(조건 A).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.modules.certifications.calendar import effective_status, overdue_days

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"
GRAY = "GRAY"
COLORS = (GREEN, YELLOW, RED, GRAY)

#: 상태 11값 → 요건 색. 표 하나가 정본이고, 상태를 더하면 이 표와 테스트가 함께
#: 움직인다(전 상태 커버는 아키텍처 테스트가 고정 — 빠진 상태는 KeyError가 아니라
#: 계약 위반으로 잡힌다).
STATUS_COLOR: dict[str, str] = {
    "NOT_STARTED": RED,
    "PREPARING": YELLOW,
    "SUBMITTED": YELLOW,
    "IN_REVIEW": YELLOW,
    "SUPPLEMENTING": YELLOW,
    "APPROVED": GREEN,
    "EXPIRING": YELLOW,
    "RENEWING": YELLOW,  # 도과하면 🔴 — requirement_color
    "REJECTED": RED,
    "EXPIRED": RED,
    "SUSPENDED": RED,
}

#: 집계 축(필수 집합의 출처) — INGREDIENT는 모집합 밖이다(판정 요청 2 (나)).
AXES = ("SKU", "PRODUCT", "COMPANY", "FACILITY")

#: 범례에 노출하는 모집합 문구(조건 A) — 서버가 정본이고 화면은 그대로 보여 준다.
#: 화면과 API가 각자 문구를 들면 한쪽만 고쳐져 "🟢 과신 방지" 안내가 어긋난다.
SCOPE_NOTE = (
    "집계 모집합: 품목군 요건 세트의 확정 요건 중 SKU·제품(등록 시 자동 적용과 같은 축)·"
    "자사(기업)·제조사(시설) 단위만 셉니다. 성분(INGREDIENT) 단위 요건은 집계에 "
    "포함되지 않으므로 '판매가능'이 성분 요건 충족까지 뜻하지는 않습니다."
)


def requirement_color(status: str | None, *, renewing_overdue: bool = False) -> str:
    """요건 1건의 색. status=None은 활성 인스턴스 부재(🔴 — fail-closed)."""
    if status is None:
        return RED
    if status == "RENEWING" and renewing_overdue:
        return RED
    return STATUS_COLOR[status]


def aggregate_colors(colors: Iterable[str]) -> str:
    """셀 색 = 최악값. 요건이 하나도 없으면 ⚪(대상외)."""
    seen = set(colors)
    if not seen:
        return GRAY
    if RED in seen:
        return RED
    if YELLOW in seen:
        return YELLOW
    return GREEN


@dataclass(frozen=True, slots=True)
class InstanceFacts:
    """활성 인증 인스턴스의 사실 — 집계에 필요한 필드만."""

    certification_id: int
    status: str
    expires_on: date | None
    renewal_lead_days: int | None


@dataclass(frozen=True, slots=True)
class Requirement:
    """필수 요건 1건 — 어느 템플릿이 어느 대상에게 요구되는가."""

    template_id: int
    template_name: str
    axis: str  # AXES 중 하나
    #: 대상 식별 — COMPANY는 target_id=None(자사 단일).
    target_type: str
    target_id: int | None
    #: 제조사 미지정 SKU의 FACILITY 요건 — 대상이 없어 충족을 확인할 수 없다(🔴).
    target_missing: bool = False


@dataclass(frozen=True, slots=True)
class RequirementResult:
    template_id: int
    template_name: str
    axis: str
    #: 요건의 대상 — 상세 패널이 "누구의 요건인가"를 말하고, 세트 롤업의 중복 제거
    #: 키가 된다(같은 제품·같은 제조사·자사 요건은 여러 구성품에 걸려도 1건이다).
    target_type: str
    target_id: int | None
    #: 실효 상태 — 활성 인스턴스가 없으면 None.
    status: str | None
    color: str
    certification_id: int | None
    note: str | None
    #: 제조사 미지정으로 대상이 없는 시설 요건.
    target_missing: bool = False
    #: 세트 셀에서 이 결과가 어느 구성품에서 왔는가(자기 것이면 None).
    via_component_sku_id: int | None = None
    via_component_sku_code: str | None = None


def evaluate_requirement(
    requirement: Requirement, instance: InstanceFacts | None, *, base_date: date
) -> RequirementResult:
    """요건 1건 + 그 대상의 활성 인스턴스 → 색·실효 상태·사유."""

    def result(status: str | None, color: str, note: str | None) -> RequirementResult:
        return RequirementResult(
            template_id=requirement.template_id,
            template_name=requirement.template_name,
            axis=requirement.axis,
            target_type=requirement.target_type,
            target_id=requirement.target_id,
            status=status,
            color=color,
            certification_id=instance.certification_id if instance is not None else None,
            note=note,
            target_missing=requirement.target_missing,
        )

    if requirement.target_missing:
        return result(None, RED, "제조사가 지정되지 않아 시설 요건을 확인할 수 없습니다.")
    if instance is None:
        return result(None, RED, "활성 인증 인스턴스가 없습니다(미등록·반려·중단).")

    effective = effective_status(
        instance.status, instance.expires_on, instance.renewal_lead_days, base_date
    )
    overdue = overdue_days(instance.status, instance.expires_on, base_date) is not None
    renewing_overdue = instance.status == "RENEWING" and overdue
    note = "갱신중이지만 만료일이 지났습니다." if renewing_overdue else None
    return result(effective, requirement_color(effective, renewing_overdue=renewing_overdue), note)


@dataclass(frozen=True, slots=True)
class CellSummary:
    color: str
    required: int
    approved: int
    in_progress: int
    unmet: int


def summarize(results: Sequence[RequirementResult]) -> CellSummary:
    """셀 요약 — 색 + 건수 3분(🟢/🟡/🔴). 건수는 툴팁·상세 패널의 근거다."""
    colors = [result.color for result in results]
    return CellSummary(
        color=aggregate_colors(colors),
        required=len(results),
        approved=colors.count(GREEN),
        in_progress=colors.count(YELLOW),
        unmet=colors.count(RED),
    )


def _identity(item: RequirementResult, component_sku_id: int | None) -> tuple[object, ...]:
    """같은 요건인가의 기준 — (템플릿, 대상). 제조사 미지정은 구성품마다 별개다."""
    if item.target_missing:
        return (item.template_id, item.target_type, "missing", component_sku_id)
    return (item.template_id, item.target_type, item.target_id)


def merge_component_results(
    own: Sequence[RequirementResult],
    components: Sequence[tuple[int, str, Sequence[RequirementResult]]],
) -> list[RequirementResult]:
    """세트 셀 = 자기 요건 + 구성품 요건 롤업 (§4.2·[M2] S2-2 PR-2 "판정 롤업은 §5.3 몫").

    구성품 요건은 `via_component_*`를 달아 붙이고, **같은 (템플릿, 대상)은 한 번만**
    센다 — 기업 단위 요건은 모든 구성품에 걸리고 같은 제품·같은 제조사의 요건도
    여러 구성품에 반복되므로, 안 세면 "필수 5건"이 실제로는 서로 다른 1건이 5번
    세어진 것이다. 색은 최악값이라 중복 제거가 색을 바꾸지는 않는다(건수와 상세의
    정직성 문제다).
    """
    merged = list(own)
    seen: set[tuple[object, ...]] = {_identity(item, None) for item in own}
    for sku_id, sku_code, results in components:
        for item in results:
            key = _identity(item, sku_id)
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                RequirementResult(
                    template_id=item.template_id,
                    template_name=item.template_name,
                    axis=item.axis,
                    target_type=item.target_type,
                    target_id=item.target_id,
                    status=item.status,
                    color=item.color,
                    certification_id=item.certification_id,
                    note=item.note,
                    target_missing=item.target_missing,
                    via_component_sku_id=sku_id,
                    via_component_sku_code=sku_code,
                )
            )
    return merged
