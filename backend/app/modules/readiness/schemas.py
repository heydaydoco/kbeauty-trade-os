"""시장 준비도 매트릭스 응답 (§5.3 — 계산값이라 요청 본문이 없다).

그리드형 응답이다: 행(SKU 한 쪽) × 열(전 시장). 열 목록은 봉투 최상위(`markets`)에
한 번만 싣고 각 행의 `cells`는 같은 순서로 따라온다 — 셀마다 시장 이름을 되풀이하면
50행 × 시장 수십 개에서 응답이 불필요하게 커진다. 페이지 봉투(items·total·page·
size)는 그대로다(§18.4 — 기존 K 자동 스캔은 그리드형을 못 알아보므로 명시 케이스가
계약을 고정한다).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.core.pagination import Page, PageParams
from app.modules.readiness.rules import SCOPE_NOTE
from app.modules.readiness.service import MatrixRowView, MatrixView


class MatrixMarket(BaseModel):
    id: int
    code: str
    name_ko: str


class MatrixRequirement(BaseModel):
    """셀을 이루는 필수 요건 1건 — 클릭 상세 패널의 한 줄."""

    template_id: int
    template_name: str
    #: SKU·PRODUCT·COMPANY·FACILITY — 요건이 어느 대상에게 걸리는가.
    axis: str
    target_type: str
    target_id: int | None
    #: 실효 상태(달력 재계산 반영). 활성 인스턴스가 없으면 null.
    status: str | None
    color: str
    certification_id: int | None
    note: str | None
    via_component_sku_id: int | None
    via_component_sku_code: str | None


class MatrixCell(BaseModel):
    market_id: int
    market_code: str
    #: GREEN·YELLOW·RED·GRAY — 색은 글자와 함께 표시한다(색만으로 구분하지 않는다).
    color: str
    required: int
    approved: int
    in_progress: int
    unmet: int
    items: list[MatrixRequirement]


class MatrixComponent(BaseModel):
    sku_id: int
    sku_code: str


class MatrixRow(BaseModel):
    sku_id: int
    sku_code: str
    name_ko: str
    kind: str
    status: str
    item_profile_id: int | None
    cells: list[MatrixCell]
    components: list[MatrixComponent]


class MatrixPage(Page[MatrixRow]):
    markets: list[MatrixMarket]
    #: 계산 기준일(KST) — 화면이 "언제 기준의 값인가"를 말한다.
    as_of: date
    #: 집계 모집합 안내(조건 A) — 서버가 정본이다.
    scope_note: str

    @classmethod
    def from_view(cls, view: MatrixView, params: PageParams) -> MatrixPage:
        return cls(
            items=[_row(row) for row in view.rows],
            total=view.total,
            page=params.page,
            size=params.size,
            markets=[MatrixMarket(id=m.id, code=m.code, name_ko=m.name_ko) for m in view.markets],
            as_of=view.as_of,
            scope_note=SCOPE_NOTE,
        )


def _row(row: MatrixRowView) -> MatrixRow:
    return MatrixRow(
        sku_id=row.sku_id,
        sku_code=row.sku_code,
        name_ko=row.name_ko,
        kind=row.kind,
        status=row.status,
        item_profile_id=row.item_profile_id,
        cells=[
            MatrixCell(
                market_id=cell.market_id,
                market_code=cell.market_code,
                color=cell.summary.color,
                required=cell.summary.required,
                approved=cell.summary.approved,
                in_progress=cell.summary.in_progress,
                unmet=cell.summary.unmet,
                items=[
                    MatrixRequirement(
                        template_id=item.template_id,
                        template_name=item.template_name,
                        axis=item.axis,
                        target_type=item.target_type,
                        target_id=item.target_id,
                        status=item.status,
                        color=item.color,
                        certification_id=item.certification_id,
                        note=item.note,
                        via_component_sku_id=item.via_component_sku_id,
                        via_component_sku_code=item.via_component_sku_code,
                    )
                    for item in cell.items
                ],
            )
            for cell in row.cells
        ],
        components=[MatrixComponent(sku_id=c.sku_id, sku_code=c.sku_code) for c in row.components],
    )
