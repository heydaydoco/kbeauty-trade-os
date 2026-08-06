"""임포트 레지스트리 (DESIGN.md §12.2 — "임포트 레지스트리·표준 양식 다운로드").

■ 레지스트리 = 왕복 대상별 어댑터

  대상 하나가 아는 것: CSV 컬럼(내보내기 헤더와 **같은 상수** — 왕복 전단사의
  전제), 셀 → 정규형 파싱(등록 API와 같은 검증·같은 문구), 현재 DB 스냅샷,
  신규 생성·변경 반영. diff 계산 자체는 service가 공통으로 한다.

■ 왕복 CSV의 1열은 ID다 (§12.2 diff의 키)

  빈 ID=신규 / ID 있는 행=변경분만 / 파일에 없는 행=무시 / ID 훼손=오류.
  내보내기 헤더 상수를 여기 한 곳에 두고 각 모듈 라우터가 가져다 쓴다 —
  내보내기와 양식이 갈라지면 왕복이 그 자리에서 깨지기 때문이다.

■ 어댑터는 코드다 (ADR-11의 "검증 골격은 코드 고정" 쪽)

  컬럼·검증·반영이 대상 모델에 결합되어 데이터로 옮길 수 없다. 대상을 늘리려면
  여기 어댑터와 models.IMPORT_REGISTRY_CODES가 같이 늘어난다(테스트가 대조).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.exceptions import AppError
from app.core.money import Money
from app.core.time import utcnow
from app.modules.materials.models import MATERIAL_TYPES, Material
from app.modules.partners import service as partners_service
from app.modules.partners.models import Partner, PartnerTypeLink

_HS6_PATTERN = re.compile(r"^[0-9]{6}$")


@dataclass(frozen=True, slots=True)
class Column:
    """CSV 컬럼 ↔ 정규형 필드 1:1 매핑.

    is_string: 내보내기가 파이썬 str로 쓰는 셀 — 수식 이스케이프 역변환 대상
    (ADR-0027 "문자열 타입 셀"). 불리언·ID 셀은 내보내기가 이스케이프하지
    않으므로 역변환도 하지 않는다(전단사의 조건).
    """

    header: str
    field: str
    is_string: bool = True


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    """diff 시점의 대상 행 — version이 확정 시 충돌 판정 기준이 된다(§17.2)."""

    id: int
    version: int
    payload: dict[str, Any]


def detail_text(exc: AppError) -> str:
    """AppError의 사용자용 detail을 오류 행 사유 한 줄로."""
    parts = [str(value) for value in exc.detail.values()]
    return " / ".join(parts) if parts else exc.message


def _blank_to_none(cell: str) -> str | None:
    """빈 셀 ↔ None — 내보내기가 None을 빈 셀로 쓰는 것의 역방향."""
    return cell if cell else None


def _parse_bool(cell: str, label: str, *, required: bool) -> tuple[bool | None, str | None]:
    """불리언 셀 파싱 — 내보내기 표기(True/False)의 역방향.

    엑셀이 불리언을 대문자(TRUE/FALSE)로 바꿔 두는 실무가 있어 대소문자는
    받아 주되, 그 밖의 값은 추측하지 않는다(0/1·예/아니오 해석 금지).
    """
    text = cell.strip()
    if not text:
        if required:
            return None, f"{label} 값이 비어 있습니다. True 또는 False를 입력해 주세요."
        return None, None
    lowered = text.lower()
    if lowered == "true":
        return True, None
    if lowered == "false":
        return False, None
    return None, f"{label} 값을 읽지 못했습니다({text}). True 또는 False만 쓸 수 있습니다."


def _required_text(cell: str, label: str, *, max_length: int, problems: list[str]) -> str:
    """필수 문자열 셀 — 등록 API의 strip 관례와 같다(Pydantic 우회 경로라 직접 본다)."""
    value = cell.strip()
    if not value:
        problems.append(f"{label}이(가) 비어 있습니다.")
    elif len(value) > max_length:
        problems.append(f"{label}이(가) 너무 깁니다(최대 {max_length}자).")
    return value


class PartnersImportTarget:
    """거래처 왕복 (§4.6) — 내보내기·검증을 partners 모듈과 공유한다."""

    code = "partners"
    label_ko = "거래처"
    #: 파일 안 중복을 잡는 자연키 필드(활성 행 유일키와 같은 축).
    code_field = "partner_code"
    columns: tuple[Column, ...] = (
        Column("거래처코드", "partner_code"),
        Column("거래처명", "name_ko"),
        Column("유형", "type_codes"),
        # 여신한도는 사람 표기(12.34) — 내보내기가 str로 쓰므로 역변환 대상이다.
        Column("여신한도", "credit_limit_amount"),
        Column("여신통화", "credit_limit_currency"),
        Column("DG취급", "dg_capable", is_string=False),
        Column("강점", "strengths"),
        Column("약점", "weaknesses"),
    )

    @property
    def header(self) -> tuple[str, ...]:
        return ("ID", *(column.header for column in self.columns))

    @property
    def string_columns(self) -> frozenset[str]:
        return frozenset(column.header for column in self.columns if column.is_string)

    def prepare(self, session: Session, rows: list[Any]) -> Any:
        return None

    def parse_row(
        self, cells: dict[str, str], context: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        problems: list[str] = []
        partner_code = _required_text(
            cells["거래처코드"], "거래처코드", max_length=40, problems=problems
        )
        name_ko = _required_text(cells["거래처명"], "거래처명", max_length=200, problems=problems)

        type_codes: list[str] = []
        try:
            type_codes = partners_service.normalized_type_codes(
                {"type_codes": cells["유형"].split("|")}
            )
        except AppError as exc:
            problems.append(detail_text(exc))

        credit_amount: int | None = None
        credit_currency: str | None = None
        try:
            credit_amount, credit_currency = partners_service.parse_credit_limit(
                {
                    "credit_limit": cells["여신한도"].strip() or None,
                    "credit_limit_currency": cells["여신통화"].strip() or None,
                }
            )
        except AppError as exc:
            problems.append(detail_text(exc))

        dg_capable, dg_problem = _parse_bool(cells["DG취급"], "DG취급", required=False)
        if dg_problem:
            problems.append(dg_problem)

        if problems:
            return None, " / ".join(problems)
        return {
            "partner_code": partner_code,
            "name_ko": name_ko,
            "type_codes": type_codes,
            "credit_limit_amount": credit_amount,
            "credit_limit_currency": credit_currency,
            "dg_capable": dg_capable,
            "strengths": _blank_to_none(cells["강점"]),
            "weaknesses": _blank_to_none(cells["약점"]),
        }, None

    def display(self, field: str, payload: dict[str, Any]) -> str:
        if field == "type_codes":
            return "|".join(payload["type_codes"])
        if field == "credit_limit_amount":
            amount = payload["credit_limit_amount"]
            currency = payload["credit_limit_currency"]
            if amount is None or currency is None:
                return ""
            return str(Money(amount, currency).to_decimal())
        value = payload[field]
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value)
        return str(value)

    def load_snapshots(self, session: Session, ids: list[int]) -> dict[int, TargetSnapshot]:
        if not ids:
            return {}
        rows = list(
            session.execute(
                select(Partner).where(Partner.id.in_(ids), Partner.deleted_at.is_(None))
            ).scalars()
        )
        grouped: dict[int, list[str]] = {}
        for partner_id, type_code in session.execute(
            select(PartnerTypeLink.partner_id, PartnerTypeLink.type_code).where(
                PartnerTypeLink.partner_id.in_([row.id for row in rows]),
                PartnerTypeLink.deleted_at.is_(None),
            )
        ).all():
            grouped.setdefault(partner_id, []).append(type_code)
        return {
            row.id: TargetSnapshot(
                id=row.id,
                version=row.version,
                payload={
                    "partner_code": row.partner_code,
                    "name_ko": row.name_ko,
                    "type_codes": sorted(grouped.get(row.id, [])),
                    "credit_limit_amount": row.credit_limit_amount,
                    "credit_limit_currency": row.credit_limit_currency,
                    "dg_capable": row.dg_capable,
                    "strengths": _blank_to_none(row.strengths or ""),
                    "weaknesses": _blank_to_none(row.weaknesses or ""),
                },
            )
            for row in rows
        }

    def load_targets_for_update(self, session: Session, ids: list[int]) -> dict[int, Any]:
        if not ids:
            return {}
        rows = session.execute(
            select(Partner)
            .where(Partner.id.in_(ids), Partner.deleted_at.is_(None))
            .with_for_update()
        ).scalars()
        return {row.id: row for row in rows}

    def existing_code_owners(self, session: Session, codes: list[str]) -> dict[str, int]:
        """활성 행이 이미 쓰는 코드 → 그 행의 id — 스테이징 단계에서 중복을 리포트한다."""
        if not codes:
            return {}
        rows = (
            session.execute(
                select(Partner.partner_code, Partner.id).where(
                    Partner.partner_code.in_(codes), Partner.deleted_at.is_(None)
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)

    def create(self, session: Session, actor_id: int, payload: dict[str, Any]) -> int:
        partner = Partner(
            partner_code=payload["partner_code"],
            name_ko=payload["name_ko"],
            credit_limit_amount=payload["credit_limit_amount"],
            credit_limit_currency=payload["credit_limit_currency"],
            dg_capable=payload["dg_capable"],
            strengths=payload["strengths"],
            weaknesses=payload["weaknesses"],
            created_by_id=actor_id,
        )
        session.add(partner)
        session.flush()
        for type_code in payload["type_codes"]:
            session.add(
                PartnerTypeLink(partner_id=partner.id, type_code=type_code, created_by_id=actor_id)
            )
        session.flush()
        return partner.id

    def apply_changes(
        self,
        session: Session,
        actor_id: int,
        target: Any,
        payload: dict[str, Any],
        changed_fields: set[str],
    ) -> None:
        scalar_fields = (
            "partner_code",
            "name_ko",
            "credit_limit_amount",
            "credit_limit_currency",
            "dg_capable",
            "strengths",
            "weaknesses",
        )
        for field in scalar_fields:
            if field in changed_fields:
                setattr(target, field, payload[field])
        if changed_fields - {"type_codes"}:
            target.updated_by_id = actor_id
        if "type_codes" in changed_fields:
            # 유형 해제는 soft delete, 재부여는 신규 행(§17.4 — 모델 관례 그대로).
            wanted = set(payload["type_codes"])
            links = session.execute(
                select(PartnerTypeLink).where(
                    PartnerTypeLink.partner_id == target.id,
                    PartnerTypeLink.deleted_at.is_(None),
                )
            ).scalars()
            for link in links:
                if link.type_code in wanted:
                    wanted.discard(link.type_code)
                else:
                    link.deleted_at = utcnow()
                    link.updated_by_id = actor_id
            for type_code in sorted(wanted):
                session.add(
                    PartnerTypeLink(
                        partner_id=target.id, type_code=type_code, created_by_id=actor_id
                    )
                )
        session.flush()


class MaterialsImportTarget:
    """자재 왕복 (§4.4).

    기본공급사는 CSV에서 **거래처 코드**로 오간다 — 표시명(name_ko)은 유일키가
    아니라 왕복 결정성이 없다. 코드→거래처 해석과 SUPPLIER 유형 검증은 등록
    API(_live_supplier)와 같은 규칙·같은 결과다(문구는 코드 문맥으로 보강).
    """

    code = "materials"
    label_ko = "자재"
    code_field = "material_code"
    columns: tuple[Column, ...] = (
        Column("자재코드", "material_code"),
        Column("자재명(국문)", "name_ko"),
        Column("유형", "material_type"),
        Column("HS6", "hs6"),
        Column("기본공급사코드", "default_supplier_code"),
        Column("재고관리", "inventory_managed", is_string=False),
        Column("로트관리", "lot_managed", is_string=False),
    )

    @property
    def header(self) -> tuple[str, ...]:
        return ("ID", *(column.header for column in self.columns))

    @property
    def string_columns(self) -> frozenset[str]:
        return frozenset(column.header for column in self.columns if column.is_string)

    def prepare(self, session: Session, rows: list[Any]) -> Any:
        """기본공급사코드 → (거래처 id, SUPPLIER 여부) 일괄 해석 — 행마다 조회하면 N+1이다."""
        codes = {row.cells["기본공급사코드"].strip() for row in rows} - {""}
        if not codes:
            return {}
        by_code: dict[str, tuple[int, bool]] = {}
        id_rows = session.execute(
            select(Partner.id, Partner.partner_code).where(
                Partner.partner_code.in_(codes), Partner.deleted_at.is_(None)
            )
        ).all()
        supplier_ids = set(
            session.execute(
                select(PartnerTypeLink.partner_id).where(
                    PartnerTypeLink.partner_id.in_([partner_id for partner_id, _ in id_rows]),
                    PartnerTypeLink.type_code == "SUPPLIER",
                    PartnerTypeLink.deleted_at.is_(None),
                )
            ).scalars()
        )
        for partner_id, partner_code in id_rows:
            by_code[partner_code] = (partner_id, partner_id in supplier_ids)
        return by_code

    def parse_row(
        self, cells: dict[str, str], context: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        suppliers: dict[str, tuple[int, bool]] = context or {}
        problems: list[str] = []
        material_code = _required_text(
            cells["자재코드"], "자재코드", max_length=40, problems=problems
        )
        name_ko = _required_text(
            cells["자재명(국문)"], "자재명(국문)", max_length=200, problems=problems
        )

        material_type = cells["유형"].strip()
        if material_type not in MATERIAL_TYPES:
            problems.append(
                f"알 수 없는 자재 유형입니다({material_type or '빈 값'}). "
                f"{', '.join(MATERIAL_TYPES)} 중 하나를 입력해 주세요."
            )

        hs6 = cells["HS6"].strip() or None
        if hs6 is not None and not _HS6_PATTERN.fullmatch(hs6):
            problems.append(f"HS6는 숫자 6자리여야 합니다({hs6}).")

        supplier_code = cells["기본공급사코드"].strip() or None
        supplier_id: int | None = None
        if supplier_code is not None:
            resolved = suppliers.get(supplier_code)
            if resolved is None:
                problems.append(
                    f"등록되지 않은 거래처 코드입니다({supplier_code}). 거래처를 먼저 등록해 주세요."
                )
            elif not resolved[1]:
                problems.append(
                    f"공급사 유형이 아닌 거래처입니다({supplier_code}). 기본공급사는 "
                    "SUPPLIER 유형을 가진 거래처만 지정할 수 있습니다."
                )
            else:
                supplier_id = resolved[0]

        inventory_managed, inventory_problem = _parse_bool(
            cells["재고관리"], "재고관리", required=True
        )
        if inventory_problem:
            problems.append(inventory_problem)
        lot_managed, lot_problem = _parse_bool(cells["로트관리"], "로트관리", required=True)
        if lot_problem:
            problems.append(lot_problem)
        if lot_managed and not inventory_managed:
            # 등록 API와 같은 규칙·같은 문구(최종 판정자는 DB CHECK다).
            problems.append(
                "로트관리는 재고관리를 전제합니다. 재고관리를 함께 켜거나 로트관리를 꺼 주세요."
            )

        if problems:
            return None, " / ".join(problems)
        return {
            "material_code": material_code,
            "name_ko": name_ko,
            "material_type": material_type,
            "hs6": hs6,
            "default_supplier_code": supplier_code,
            "default_supplier_partner_id": supplier_id,
            "inventory_managed": inventory_managed,
            "lot_managed": lot_managed,
        }, None

    def display(self, field: str, payload: dict[str, Any]) -> str:
        value = payload[field]
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value)
        return str(value)

    def load_snapshots(self, session: Session, ids: list[int]) -> dict[int, TargetSnapshot]:
        if not ids:
            return {}
        rows = session.execute(
            select(Material, Partner.partner_code)
            .outerjoin(Partner, Material.default_supplier_partner_id == Partner.id)
            .where(Material.id.in_(ids), Material.deleted_at.is_(None))
        ).all()
        return {
            material.id: TargetSnapshot(
                id=material.id,
                version=material.version,
                payload={
                    "material_code": material.material_code,
                    "name_ko": material.name_ko,
                    "material_type": material.material_type,
                    "hs6": material.hs6,
                    "default_supplier_code": supplier_code,
                    "default_supplier_partner_id": material.default_supplier_partner_id,
                    "inventory_managed": material.inventory_managed,
                    "lot_managed": material.lot_managed,
                },
            )
            for material, supplier_code in rows
        }

    def load_targets_for_update(self, session: Session, ids: list[int]) -> dict[int, Any]:
        if not ids:
            return {}
        rows = session.execute(
            select(Material)
            .where(Material.id.in_(ids), Material.deleted_at.is_(None))
            .with_for_update()
        ).scalars()
        return {row.id: row for row in rows}

    def existing_code_owners(self, session: Session, codes: list[str]) -> dict[str, int]:
        """활성 행이 이미 쓰는 코드 → 그 행의 id — 스테이징 단계에서 중복을 리포트한다."""
        if not codes:
            return {}
        rows = (
            session.execute(
                select(Material.material_code, Material.id).where(
                    Material.material_code.in_(codes), Material.deleted_at.is_(None)
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)

    def create(self, session: Session, actor_id: int, payload: dict[str, Any]) -> int:
        material = Material(
            material_code=payload["material_code"],
            name_ko=payload["name_ko"],
            material_type=payload["material_type"],
            hs6=payload["hs6"],
            default_supplier_partner_id=payload["default_supplier_partner_id"],
            inventory_managed=payload["inventory_managed"],
            lot_managed=payload["lot_managed"],
            created_by_id=actor_id,
        )
        session.add(material)
        session.flush()
        return material.id

    def apply_changes(
        self,
        session: Session,
        actor_id: int,
        target: Any,
        payload: dict[str, Any],
        changed_fields: set[str],
    ) -> None:
        for field in ("material_code", "name_ko", "material_type", "hs6"):
            if field in changed_fields:
                setattr(target, field, payload[field])
        if "default_supplier_code" in changed_fields:
            target.default_supplier_partner_id = payload["default_supplier_partner_id"]
        for field in ("inventory_managed", "lot_managed"):
            if field in changed_fields:
                setattr(target, field, payload[field])
        target.updated_by_id = actor_id
        session.flush()


ImportTarget = PartnersImportTarget | MaterialsImportTarget

#: 레지스트리 본체 — models.IMPORT_REGISTRY_CODES와 1:1(테스트가 대조).
IMPORT_TARGETS: dict[str, ImportTarget] = {
    target.code: target for target in (MaterialsImportTarget(), PartnersImportTarget())
}

#: 내보내기 라우터가 쓰는 헤더 상수 — 양식과 내보내기가 한 상수를 공유해야
#: 왕복(내려받아 그대로 올리면 변화 0)이 구조적으로 성립한다.
PARTNERS_CSV_HEADER = IMPORT_TARGETS["partners"].header
MATERIALS_CSV_HEADER = IMPORT_TARGETS["materials"].header
