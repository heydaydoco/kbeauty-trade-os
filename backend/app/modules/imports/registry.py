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
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.exceptions import AppError
from app.core.money import Money
from app.core.time import utcnow
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import SKU_KINDS, SKU_STATUSES, Product, Sku
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


def _optional_text(cell: str, label: str, *, max_length: int, problems: list[str]) -> str | None:
    """선택 문자열 셀 — 빈 셀은 None, 값이 있으면 길이만 본다."""
    value = cell.strip()
    if not value:
        return None
    if len(value) > max_length:
        problems.append(f"{label}이(가) 너무 깁니다(최대 {max_length}자).")
    return value


def _parse_decimal(cell: str, label: str) -> tuple[str | None, str | None]:
    """소수 셀 → 정규 문자열 (payload는 JSON이라 Decimal을 문자열로 담는다).

    float을 거치지 않는다(§2 ADR-02 — 등록 API `_decimal`과 같은 이유). 내보내기가
    DB Decimal을 str로 쓰므로(예: "12.500") 무수정 왕복은 문자 그대로 일치한다.
    """
    text = cell.strip()
    if not text:
        return None, None
    try:
        return str(Decimal(text)), None
    except InvalidOperation:
        return None, f"{label}은(는) 숫자여야 합니다({text})."


def _parse_positive_int(cell: str, label: str) -> tuple[int | None, str | None]:
    """양의 정수 셀 — DB CHECK(positive)와 같은 경계를 한국어로 먼저 알려 준다."""
    text = cell.strip()
    if not text:
        return None, None
    if not text.isdecimal() or int(text) <= 0:
        return None, f"{label}은(는) 0보다 큰 정수여야 합니다({text})."
    return int(text), None


def _decimal_text(value: Decimal | None) -> str | None:
    """DB Decimal → payload 문자열 — 내보내기(str(Decimal))와 같은 표기다."""
    return None if value is None else str(value)


class PartnersImportTarget:
    """거래처 왕복 (§4.6) — 내보내기·검증을 partners 모듈과 공유한다."""

    code = "partners"
    label_ko = "거래처"
    #: 파일 안 중복을 잡는 자연키 필드(활성 행 유일키와 같은 축).
    code_field = "partner_code"
    #: 왕복으로 바꿀 수 없는 필드 — 변경 diff에 잡히면 오류 행이 된다(서비스 공통 검사).
    immutable_fields: frozenset[str] = frozenset()
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
        # ★ 유형만 바뀌어도 부모 행을 **반드시** dirty로 만든다(리뷰 검출).
        #   version_id_col은 partners 행 자신의 UPDATE에서만 증가하므로, 여기서
        #   행을 안 건드리면 유형 전용 변경이 version을 안 올리고 — 겹쳐 있던
        #   다른 스테이징의 확정 대조(§17.2 전체 409)가 그 변경을 못 본다.
        #   updated_at 명시 대입은 항상 dirty를 보장한다(같은 actor 재수정 시
        #   updated_by_id만으로는 값이 같아 UPDATE가 안 나갈 수 있다).
        target.updated_by_id = actor_id
        target.updated_at = utcnow()
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

    def verify_references(
        self, session: Session, rows: list[tuple[int, dict[str, Any]]]
    ) -> list[str]:
        """확정 시점 참조 재검증 — 거래처 왕복에는 외부 참조가 없다."""
        return []


class MaterialsImportTarget:
    """자재 왕복 (§4.4).

    기본공급사는 CSV에서 **거래처 코드**로 오간다 — 표시명(name_ko)은 유일키가
    아니라 왕복 결정성이 없다. 코드→거래처 해석과 SUPPLIER 유형 검증은 등록
    API(_live_supplier)와 같은 규칙·같은 결과다(문구는 코드 문맥으로 보강).
    """

    code = "materials"
    label_ko = "자재"
    code_field = "material_code"
    immutable_fields: frozenset[str] = frozenset()
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

    def verify_references(
        self, session: Session, rows: list[tuple[int, dict[str, Any]]]
    ) -> list[str]:
        """확정 시점의 기본공급사 재검증 — 스테이징~확정 사이 창을 닫는다(리뷰 검출).

        스테이징이 굳혀 둔 partner id는 그 시점의 검증 결과다. PENDING은 무기한
        대기할 수 있고, 그 사이 거래처 왕복이 SUPPLIER 유형을 해제할 수 있다 —
        재검증 없이 반영하면 확정 경로만 [M1] ⑤(기본공급사=SUPPLIER)를 우회한다.
        """
        wanted = {
            payload["default_supplier_partner_id"]
            for _, payload in rows
            if payload.get("default_supplier_partner_id") is not None
        }
        if not wanted:
            return []
        live = set(
            session.execute(
                select(Partner.id).where(Partner.id.in_(wanted), Partner.deleted_at.is_(None))
            ).scalars()
        )
        supplier_ids = set(
            session.execute(
                select(PartnerTypeLink.partner_id).where(
                    PartnerTypeLink.partner_id.in_(wanted),
                    PartnerTypeLink.type_code == "SUPPLIER",
                    PartnerTypeLink.deleted_at.is_(None),
                )
            ).scalars()
        )
        problems: list[str] = []
        for row_no, payload in rows:
            partner_id = payload.get("default_supplier_partner_id")
            if partner_id is None:
                continue
            if partner_id not in live:
                problems.append(f"{row_no}행(기본공급사 거래처가 삭제됨)")
            elif partner_id not in supplier_ids:
                problems.append(f"{row_no}행(기본공급사가 더 이상 공급사 유형이 아님)")
        return problems


class SkusImportTarget:
    """SKU 왕복 (§4.1 / S1.5 판정 ① — 조건 8 SKU 왕복 스트레치).

    왕복 양식 = **기록 가능 필드 + ID만**(판정 ① 문면). 파생 표시 2칸(제품명·
    브랜드명)은 구조적 부재이고 표시 수요는 목록 CSV(`/skus/export.csv`)가
    맡는다 — 그래서 SKU만 왕복 내보내기(`/skus/roundtrip.csv`)가 따로 있다
    (ADR-0030 부기). 제조사는 materials 기본공급사 선례대로 **코드** 열이다.

    종류·제품코드는 왕복 불변이다(immutable_fields) — 종류 전환은 DG CHECK·
    처방 결합을 깨고(판정 ① "종류 전환=오류 행"), 소속 이전은 판정 문면이
    침묵해 보수적으로 함께 잠근다(PR-1 보고 등재 — 승인 밖 확장 금지).
    """

    code = "skus"
    label_ko = "SKU"
    code_field = "sku_code"
    immutable_fields: frozenset[str] = frozenset({"kind", "product_code"})
    columns: tuple[Column, ...] = (
        Column("품번", "sku_code"),
        Column("품명(국문)", "name_ko"),
        Column("품명(영문)", "name_en"),
        Column("종류", "kind"),
        Column("제품코드", "product_code"),
        Column("상태", "status"),
        Column("바코드", "barcode"),
        # 수치 셀은 내보내기가 Decimal/int로 쓴다 — 이스케이프 비대상(ADR-0027
        # "숫자 타입 셀"). 인화점 음수(-13.0)가 텍스트로 변하지 않는 조건이다.
        Column("중량(g)", "unit_weight_g", is_string=False),
        Column("박스입수", "box_qty", is_string=False),
        Column("사용기한(개월)", "shelf_life_months", is_string=False),
        Column("제조사코드", "manufacturer_code"),
        Column("위험물", "dg_flag", is_string=False),
        Column("UN번호", "un_number"),
        Column("Class", "dg_class"),
        Column("포장등급", "packing_group"),
        Column("인화점(℃)", "flash_point_c", is_string=False),
        Column("알코올함량(%)", "alcohol_content_pct", is_string=False),
        Column("에어로졸", "is_aerosol", is_string=False),
        Column("LQ", "is_limited_quantity", is_string=False),
    )

    @property
    def header(self) -> tuple[str, ...]:
        return ("ID", *(column.header for column in self.columns))

    @property
    def string_columns(self) -> frozenset[str]:
        return frozenset(column.header for column in self.columns if column.is_string)

    def prepare(self, session: Session, rows: list[Any]) -> Any:
        """제품코드→id, 제조사코드→(id, OEM 여부) 일괄 해석 — 행마다 조회하면 N+1이다."""
        product_codes = {row.cells["제품코드"].strip() for row in rows} - {""}
        manufacturer_codes = {row.cells["제조사코드"].strip() for row in rows} - {""}
        products: dict[str, int] = {}
        if product_codes:
            products = dict(
                session.execute(
                    select(Product.product_code, Product.id).where(
                        Product.product_code.in_(product_codes), Product.deleted_at.is_(None)
                    )
                )
                .tuples()
                .all()
            )
        manufacturers: dict[str, tuple[int, bool]] = {}
        if manufacturer_codes:
            id_rows = session.execute(
                select(Partner.id, Partner.partner_code).where(
                    Partner.partner_code.in_(manufacturer_codes), Partner.deleted_at.is_(None)
                )
            ).all()
            oem_ids = set(
                session.execute(
                    select(PartnerTypeLink.partner_id).where(
                        PartnerTypeLink.partner_id.in_([pid for pid, _ in id_rows]),
                        PartnerTypeLink.type_code == "OEM",
                        PartnerTypeLink.deleted_at.is_(None),
                    )
                ).scalars()
            )
            for partner_id, partner_code in id_rows:
                manufacturers[partner_code] = (partner_id, partner_id in oem_ids)
        return {"products": products, "manufacturers": manufacturers}

    def parse_row(
        self, cells: dict[str, str], context: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        products: dict[str, int] = (context or {}).get("products", {})
        manufacturers: dict[str, tuple[int, bool]] = (context or {}).get("manufacturers", {})
        problems: list[str] = []

        sku_code = _required_text(cells["품번"], "품번", max_length=40, problems=problems)
        name_ko = _required_text(
            cells["품명(국문)"], "품명(국문)", max_length=200, problems=problems
        )
        name_en = _optional_text(
            cells["품명(영문)"], "품명(영문)", max_length=200, problems=problems
        )

        kind = cells["종류"].strip()
        if kind not in SKU_KINDS:
            problems.append(
                f"알 수 없는 종류입니다({kind or '빈 값'}). "
                f"{', '.join(SKU_KINDS)} 중 하나를 입력해 주세요."
            )
        status = cells["상태"].strip()
        if status not in SKU_STATUSES:
            problems.append(
                f"알 수 없는 상태입니다({status or '빈 값'}). "
                f"{', '.join(SKU_STATUSES)} 중 하나를 입력해 주세요."
            )
        barcode = _optional_text(cells["바코드"], "바코드", max_length=20, problems=problems)

        unit_weight_g, weight_problem = _parse_decimal(cells["중량(g)"], "중량(g)")
        if weight_problem:
            problems.append(weight_problem)
        elif unit_weight_g is not None and Decimal(unit_weight_g) <= 0:
            problems.append(f"중량(g)은 0보다 커야 합니다({unit_weight_g}).")
        box_qty, box_problem = _parse_positive_int(cells["박스입수"], "박스입수")
        if box_problem:
            problems.append(box_problem)
        shelf_life_months, shelf_problem = _parse_positive_int(
            cells["사용기한(개월)"], "사용기한(개월)"
        )
        if shelf_problem:
            problems.append(shelf_problem)

        product_code = cells["제품코드"].strip() or None
        product_id: int | None = None
        product_unresolved = False
        if product_code is not None:
            product_id = products.get(product_code)
            if product_id is None:
                product_unresolved = True
                problems.append(
                    f"등록되지 않은 제품 코드입니다({product_code}). 제품을 먼저 등록해 주세요."
                )

        manufacturer_code = cells["제조사코드"].strip() or None
        manufacturer_id: int | None = None
        if manufacturer_code is not None:
            resolved = manufacturers.get(manufacturer_code)
            if resolved is None:
                problems.append(
                    f"등록되지 않은 거래처 코드입니다({manufacturer_code}). "
                    "거래처를 먼저 등록해 주세요."
                )
            elif not resolved[1]:
                # 등록 API(_live_manufacturer)와 같은 규칙 — 문구는 코드 문맥으로 보강.
                problems.append(
                    f"OEM 유형이 아닌 거래처입니다({manufacturer_code}). 제조사는 "
                    "OEM 유형을 가진 거래처만 지정할 수 있습니다."
                )
            else:
                manufacturer_id = resolved[0]

        dg_flag, dg_problem = _parse_bool(cells["위험물"], "위험물", required=True)
        if dg_problem:
            problems.append(dg_problem)
        un_number = _optional_text(cells["UN번호"], "UN번호", max_length=10, problems=problems)
        dg_class = _optional_text(cells["Class"], "Class", max_length=10, problems=problems)
        packing_group = _optional_text(
            cells["포장등급"], "포장등급", max_length=3, problems=problems
        )
        flash_point_c, flash_problem = _parse_decimal(cells["인화점(℃)"], "인화점(℃)")
        if flash_problem:
            problems.append(flash_problem)
        alcohol_content_pct, alcohol_problem = _parse_decimal(
            cells["알코올함량(%)"], "알코올함량(%)"
        )
        if alcohol_problem:
            problems.append(alcohol_problem)
        elif alcohol_content_pct is not None and not (
            Decimal("0") <= Decimal(alcohol_content_pct) <= Decimal("100")
        ):
            problems.append(f"알코올함량(%)은 0에서 100 사이여야 합니다({alcohol_content_pct}).")
        is_aerosol, aerosol_problem = _parse_bool(cells["에어로졸"], "에어로졸", required=True)
        if aerosol_problem:
            problems.append(aerosol_problem)
        is_limited_quantity, lq_problem = _parse_bool(cells["LQ"], "LQ", required=True)
        if lq_problem:
            problems.append(lq_problem)

        # 등록 API의 가드 **함수 자체**를 재사용한다 — 같은 규칙·같은 문구의
        # 구조적 보장(최종 판정자는 DB CHECK다). 제품 코드가 해석 불능이면
        # 짝 검사는 건너뛴다(미등록 오류와 "제품이 필요합니다"가 겹치면 혼란).
        if kind in SKU_KINDS:
            if not product_unresolved:
                try:
                    catalog_service._guard_kind_product_pairing(kind, product_id)
                except AppError as exc:
                    problems.append(detail_text(exc))
            try:
                catalog_service._guard_dangerous_goods(
                    {
                        "dg_flag": dg_flag,
                        "un_number": un_number,
                        "dg_class": dg_class,
                        "packing_group": packing_group,
                        "flash_point_c": flash_point_c,
                        "alcohol_content_pct": alcohol_content_pct,
                        "is_aerosol": is_aerosol,
                        "is_limited_quantity": is_limited_quantity,
                    },
                    kind,
                )
            except AppError as exc:
                problems.append(detail_text(exc))

        if problems:
            return None, " / ".join(problems)
        return {
            "sku_code": sku_code,
            "name_ko": name_ko,
            "name_en": name_en,
            "kind": kind,
            "product_code": product_code,
            "product_id": product_id,
            "status": status,
            "barcode": barcode,
            "unit_weight_g": unit_weight_g,
            "box_qty": box_qty,
            "shelf_life_months": shelf_life_months,
            "manufacturer_code": manufacturer_code,
            "manufacturer_partner_id": manufacturer_id,
            "dg_flag": dg_flag,
            "un_number": un_number,
            "dg_class": dg_class,
            "packing_group": packing_group,
            "flash_point_c": flash_point_c,
            "alcohol_content_pct": alcohol_content_pct,
            "is_aerosol": is_aerosol,
            "is_limited_quantity": is_limited_quantity,
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
            select(Sku, Product.product_code, Partner.partner_code)
            .outerjoin(Product, Sku.product_id == Product.id)
            .outerjoin(Partner, Sku.manufacturer_partner_id == Partner.id)
            .where(Sku.id.in_(ids), Sku.deleted_at.is_(None))
        ).all()
        return {
            sku.id: TargetSnapshot(
                id=sku.id,
                version=sku.version,
                payload={
                    "sku_code": sku.sku_code,
                    "name_ko": sku.name_ko,
                    "name_en": sku.name_en,
                    "kind": sku.kind,
                    "product_code": product_code,
                    "product_id": sku.product_id,
                    "status": sku.status,
                    "barcode": sku.barcode,
                    "unit_weight_g": _decimal_text(sku.unit_weight_g),
                    "box_qty": sku.box_qty,
                    "shelf_life_months": sku.shelf_life_months,
                    "manufacturer_code": manufacturer_code,
                    "manufacturer_partner_id": sku.manufacturer_partner_id,
                    "dg_flag": sku.dg_flag,
                    "un_number": sku.un_number,
                    "dg_class": sku.dg_class,
                    "packing_group": sku.packing_group,
                    "flash_point_c": _decimal_text(sku.flash_point_c),
                    "alcohol_content_pct": _decimal_text(sku.alcohol_content_pct),
                    "is_aerosol": sku.is_aerosol,
                    "is_limited_quantity": sku.is_limited_quantity,
                },
            )
            for sku, product_code, manufacturer_code in rows
        }

    def load_targets_for_update(self, session: Session, ids: list[int]) -> dict[int, Any]:
        if not ids:
            return {}
        rows = session.execute(
            select(Sku).where(Sku.id.in_(ids), Sku.deleted_at.is_(None)).with_for_update()
        ).scalars()
        return {row.id: row for row in rows}

    def existing_code_owners(self, session: Session, codes: list[str]) -> dict[str, int]:
        """활성 행이 이미 쓰는 품번 → 그 행의 id — 스테이징 단계에서 중복을 리포트한다."""
        if not codes:
            return {}
        rows = (
            session.execute(
                select(Sku.sku_code, Sku.id).where(
                    Sku.sku_code.in_(codes), Sku.deleted_at.is_(None)
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)

    def create(self, session: Session, actor_id: int, payload: dict[str, Any]) -> int:
        sku = Sku(
            sku_code=payload["sku_code"],
            name_ko=payload["name_ko"],
            name_en=payload["name_en"],
            status=payload["status"],
            kind=payload["kind"],
            product_id=payload["product_id"],
            barcode=payload["barcode"],
            unit_weight_g=_decimal_or_none(payload["unit_weight_g"]),
            box_qty=payload["box_qty"],
            shelf_life_months=payload["shelf_life_months"],
            manufacturer_partner_id=payload["manufacturer_partner_id"],
            dg_flag=payload["dg_flag"],
            un_number=payload["un_number"],
            dg_class=payload["dg_class"],
            packing_group=payload["packing_group"],
            flash_point_c=_decimal_or_none(payload["flash_point_c"]),
            alcohol_content_pct=_decimal_or_none(payload["alcohol_content_pct"]),
            is_aerosol=payload["is_aerosol"],
            is_limited_quantity=payload["is_limited_quantity"],
            created_by_id=actor_id,
        )
        session.add(sku)
        session.flush()
        return sku.id

    def apply_changes(
        self,
        session: Session,
        actor_id: int,
        target: Any,
        payload: dict[str, Any],
        changed_fields: set[str],
    ) -> None:
        # 종류·제품코드는 스테이징 단계에서 오류 행이 된다(immutable_fields) —
        # 여기 도달하면 계약 위반이라 조용히 넘기지 않는다.
        assert not (changed_fields & self.immutable_fields)
        for field in ("sku_code", "name_ko", "name_en", "status", "barcode"):
            if field in changed_fields:
                setattr(target, field, payload[field])
        for field in ("unit_weight_g", "flash_point_c", "alcohol_content_pct"):
            if field in changed_fields:
                setattr(target, field, _decimal_or_none(payload[field]))
        for field in ("box_qty", "shelf_life_months"):
            if field in changed_fields:
                setattr(target, field, payload[field])
        if "manufacturer_code" in changed_fields:
            target.manufacturer_partner_id = payload["manufacturer_partner_id"]
        for field in (
            "dg_flag",
            "un_number",
            "dg_class",
            "packing_group",
            "is_aerosol",
            "is_limited_quantity",
        ):
            if field in changed_fields:
                setattr(target, field, payload[field])
        target.updated_by_id = actor_id
        session.flush()

    def verify_references(
        self, session: Session, rows: list[tuple[int, dict[str, Any]]]
    ) -> list[str]:
        """확정 시점의 제품·제조사 재검증 — materials 기본공급사 재검증과 같은 계약.

        스테이징이 굳힌 id는 그 시점의 검증 결과다. PENDING이 대기하는 사이
        제품이 삭제되거나 거래처 왕복이 OEM 유형을 해제할 수 있다 — 재검증
        없이 반영하면 확정 경로만 [M1] 보강(S1-3) ⑤(제조사=OEM)를 우회한다.
        """
        product_ids = {
            payload["product_id"] for _, payload in rows if payload.get("product_id") is not None
        }
        manufacturer_ids = {
            payload["manufacturer_partner_id"]
            for _, payload in rows
            if payload.get("manufacturer_partner_id") is not None
        }
        live_products: set[int] = set()
        if product_ids:
            live_products = set(
                session.execute(
                    select(Product.id).where(
                        Product.id.in_(product_ids), Product.deleted_at.is_(None)
                    )
                ).scalars()
            )
        live_partners: set[int] = set()
        oem_ids: set[int] = set()
        if manufacturer_ids:
            live_partners = set(
                session.execute(
                    select(Partner.id).where(
                        Partner.id.in_(manufacturer_ids), Partner.deleted_at.is_(None)
                    )
                ).scalars()
            )
            oem_ids = set(
                session.execute(
                    select(PartnerTypeLink.partner_id).where(
                        PartnerTypeLink.partner_id.in_(manufacturer_ids),
                        PartnerTypeLink.type_code == "OEM",
                        PartnerTypeLink.deleted_at.is_(None),
                    )
                ).scalars()
            )
        problems: list[str] = []
        for row_no, payload in rows:
            product_id = payload.get("product_id")
            if product_id is not None and product_id not in live_products:
                problems.append(f"{row_no}행(제품이 삭제됨)")
            manufacturer_id = payload.get("manufacturer_partner_id")
            if manufacturer_id is None:
                continue
            if manufacturer_id not in live_partners:
                problems.append(f"{row_no}행(제조사 거래처가 삭제됨)")
            elif manufacturer_id not in oem_ids:
                problems.append(f"{row_no}행(제조사가 더 이상 OEM 유형이 아님)")
        return problems


def _decimal_or_none(text: str | None) -> Decimal | None:
    """payload 정규 문자열 → DB Decimal. float은 거치지 않는다(§2 ADR-02)."""
    return None if text is None else Decimal(text)


ImportTarget = PartnersImportTarget | MaterialsImportTarget | SkusImportTarget

#: 레지스트리 본체 — models.IMPORT_REGISTRY_CODES와 1:1(테스트가 대조).
IMPORT_TARGETS: dict[str, ImportTarget] = {
    target.code: target
    for target in (MaterialsImportTarget(), PartnersImportTarget(), SkusImportTarget())
}

#: 내보내기 라우터가 쓰는 헤더 상수 — 양식과 내보내기가 한 상수를 공유해야
#: 왕복(내려받아 그대로 올리면 변화 0)이 구조적으로 성립한다.
PARTNERS_CSV_HEADER = IMPORT_TARGETS["partners"].header
MATERIALS_CSV_HEADER = IMPORT_TARGETS["materials"].header
#: SKU만 왕복 내보내기가 표시용 목록 CSV와 분리다(판정 ① — 파생 표시 2칸은
#: 구조적 부재, 표시 수요는 목록 CSV 몫. ADR-0030 부기).
SKUS_ROUNDTRIP_CSV_HEADER = IMPORT_TARGETS["skus"].header
