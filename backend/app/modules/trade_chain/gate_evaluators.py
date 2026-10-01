"""게이트 7종 평가기 — SO 대상 어댑터와 등록 (S3-1 PR-11a / design-D D3·D4 / design-integrated X-26·X-27 / ADR-0069).

`gates`(평가 틀·`clearance`)는 도메인을 모른다 — 이 파일(L2, trade_chain)이 SO를 `GateSubject`로 어댑트하고 평가기 7개를 **등록**한다(`gates.registry`). 평가기는 **쓰기 없이 읽기만** 하며
마스터 규칙을 재구현하지 않는다(정의 이중화 방지): 준비도=`readiness.cells_for`·여신=`credit.evaluation`(통과 판정 복제 금지)·입금=`payments.pi_gate`·정책=`policies.get_policy`·품번=`partners.resolve_buyer_items`.

■ 결과는 전부 `gates.policy.outcome(...)` 팩토리로 만든다 — `GATE_SPECS`에 없는 (결과, 해소) 조합은 만들 수 없고(ValueError → 평가 틀이 UNKNOWN으로) 통과 판정은 `gates.service.clearance`만 한다.
  평가기 안에는 **결과값 비교로 통과를 판정하는 코드가 없다**(결과를 만들 뿐).
■ 한 게이트가 한 평가에서 (게이트, 라인)당 최대 1건을 낸다. 라인 단위 게이트(품번·단가·준비도·MOQ)는 **비PASS 라인만** 결과로 내고, 전 라인이 통과면 게이트 단위 PASS 1건을 낸다.
■ `basis`에는 **판매 단가·기준가·허용치·수량·준비 상태 요약만** 싣는다 — **원가·마진·매입가(PURCHASE 유형)는 어떤 경로에서도 조회하지 않는다**(소스 스캔 테스트).
■ MARKET_READINESS 결과는 계산값 표시이며 **법적 판정이 아니다** — 메시지에 '판매 가능·적합·승인·허가' 워딩을 쓰지 않는다(문구 스캔).
■ CREDIT: `authoritative=True`(확정 통로 — 호출자가 거래처를 이미 잠갔다)면 `lock_buyer_for_credit`(같은 트랜잭션의 같은 행 재획득 — 대기·순서 위반 없음)+`evaluate_credit`, 조회(`GET /gates`)는
  잠금 없는 `evaluate_credit_unlocked`(`advisory=true` 참고값)다. 해소는 승인뿐(override 불가 — 명세·DB CHECK).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.exceptions import AppError
from app.modules.catalog.models import SKU_STATUS_DISCONTINUED, Sku
from app.modules.credit import evaluation as credit_evaluation
from app.modules.credit.locking import lock_buyer_for_credit
from app.modules.gates import registry as gate_registry
from app.modules.gates.models import SUBJECT_SALES_ORDER
from app.modules.gates.policy import outcome
from app.modules.gates.registry import EvaluatorRegistry
from app.modules.gates.types import (
    SUBJECT_INTAKE,
    BasisValue,
    GateCode,
    GateLevel,
    GateLine,
    GateOutcome,
    GatePhase,
    GateResolution,
    GateSubject,
)
from app.modules.order_intake import service as intake_service
from app.modules.partners.models import PARTNER_TYPE_BUYER, Partner
from app.modules.partners.service import partner_type_codes, resolve_buyer_items
from app.modules.payments.pi_gate import (
    MODE_WARN,
    PiAdvanceState,
    SoTerms,
    evaluate_pi_advance,
)
from app.modules.policies.service import get_policy
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.readiness import rules as readiness_rules
from app.modules.readiness import service as readiness_service
from app.modules.sales_orders.models import SalesOrder, SalesOrderLine
from app.modules.trade_docs.machine import SalesOrderStatus

#: 정책 키 — 게이트 평가·증적이 쓰는 두 값(`policies.registry`와 1:1).
POLICY_PI_MODE = "pi_advance_gate_mode"
POLICY_PRICE_TOLERANCE = "price_deviation_tolerance_bp"

NOTE_READINESS = "준비 상태 안내(법적 판정 아님)"

Basis = dict[str, BasisValue]


# ── 대상 어댑터 ──────────────────────────────────────────────────────────────


def subject_from_sales_order(session: Session, so_id: int, *, authoritative: bool) -> GateSubject:
    """SO(헤더+살아 있는 라인)를 평가 대상으로 어댑트한다 — 호출자가 잠근 SO를 읽는다(`populate_existing`: 세션 캐시의 옛 값 방지)."""
    order = session.execute(
        select(SalesOrder).where(SalesOrder.id == so_id).execution_options(populate_existing=True)
    ).scalar_one()
    rows = session.execute(
        select(SalesOrderLine)
        .where(SalesOrderLine.so_id == so_id, SalesOrderLine.deleted_at.is_(None))
        .order_by(SalesOrderLine.line_no, SalesOrderLine.id)
        .execution_options(populate_existing=True)
    ).scalars()
    return GateSubject(
        kind=SUBJECT_SALES_ORDER,
        id=order.id,
        buyer_partner_id=order.buyer_partner_id,
        currency=order.currency,
        dest_market_code=order.dest_market_code,
        lines=tuple(
            GateLine(
                line_id=line.id,
                line_no=line.line_no,
                sku_id=line.sku_id,
                buyer_item_code=line.buyer_item_code,
                quantity=line.quantity,
                unit_price_amount=line.unit_price_amount,
                list_price_amount=line.list_price_amount,
                is_free=line.is_free,
            )
            for line in rows
        ),
        total_amount=order.total_amount,
        po_no_key=order.buyer_po_no_key,
        payment_type=order.payment_type,
        advance_pct_bp=order.advance_pct_bp,
        pi_id=order.pi_id,
        authoritative=authoritative,
    )


def _require_so(subject: GateSubject) -> None:
    if subject.kind != SUBJECT_SALES_ORDER:
        raise ValueError(f"SO 평가기가 받을 수 없는 대상입니다: {subject.kind}")


def _require_so_or_intake(subject: GateSubject) -> None:
    """INTAKE phase 어댑터(PR-13a) — 품번·중복 PO·가격·준비도·MOQ 5종은 인테이크(검토 시점)와 SO(확정 시점)가 **같은 평가기**를 쓴다. 여신·PI 입금은 SO 확정 전용이다."""
    if subject.kind not in (SUBJECT_SALES_ORDER, SUBJECT_INTAKE):
        raise ValueError(f"평가기가 받을 수 없는 대상입니다: {subject.kind}")


def _passed(
    code: GateCode, reason_code: str, message_ko: str, basis: Basis | None = None
) -> GateOutcome:
    return outcome(code, GateLevel.PASS, GateResolution.NONE, reason_code, message_ko, basis or {})


# ── ITEM_MAPPING ─────────────────────────────────────────────────────────────


def evaluate_item_mapping(
    session: Session, subject: GateSubject, phase: GatePhase
) -> list[GateOutcome]:
    """바이어 품번 → SKU. 미매핑·거래처 BUYER 상실·SKU 삭제=BLOCK/NONE / 단종 SKU = 인테이크 WARN·확정 BLOCK/NONE(A11-2). 마스터를 고쳐야 한다(override 불가)."""
    _require_so_or_intake(subject)
    code = GateCode.ITEM_MAPPING
    found: list[GateOutcome] = []

    buyer_alive = (
        session.execute(
            select(Partner.id).where(
                Partner.id == subject.buyer_partner_id, Partner.deleted_at.is_(None)
            )
        ).first()
        is not None
    )
    if not buyer_alive or PARTNER_TYPE_BUYER not in partner_type_codes(
        session, subject.buyer_partner_id
    ):
        found.append(
            outcome(
                code,
                GateLevel.BLOCK,
                GateResolution.NONE,
                "BUYER_LOST",
                "거래처가 바이어 유형이 아니어서 품번 매핑을 확인할 수 없습니다. 거래처 유형을 확인해 주세요.",
                {"buyer_partner_id": subject.buyer_partner_id},
            )
        )

    codes = [line.buyer_item_code.strip() for line in subject.lines if line.buyer_item_code]
    resolved = resolve_buyer_items(session, subject.buyer_partner_id, codes) if codes else {}
    sku_ids = {line.sku_id for line in subject.lines if line.sku_id is not None}
    sku_ids |= {item.sku_id for item in resolved.values()}
    skus = (
        {s.id: s for s in session.execute(select(Sku).where(Sku.id.in_(sku_ids))).scalars()}
        if sku_ids
        else {}
    )

    for line in subject.lines:
        buyer_code = line.buyer_item_code.strip() if line.buyer_item_code else None
        mapped = resolved.get(buyer_code) if buyer_code else None
        sku_id = line.sku_id if line.sku_id is not None else (mapped.sku_id if mapped else None)
        sku = skus.get(sku_id) if sku_id is not None else None
        basis: Basis = {"sku_id": sku_id, "buyer_item_code": buyer_code}
        intake_drift = (
            subject.kind == SUBJECT_INTAKE
            and buyer_code is not None
            and (mapped is None or mapped.sku_id != line.sku_id)
        )
        if sku is None or sku.deleted_at is not None:
            reason, message = (
                (
                    "SKU_DELETED",
                    "삭제된 품목이 포함되어 있습니다. 품목을 교체하거나 수주에서 제거해 주세요.",
                )
                if sku_id is not None
                else (
                    "ITEM_UNMAPPED",
                    "바이어 품번이 SKU에 매핑되지 않았습니다. 거래처 품번 매핑을 등록해 주세요.",
                )
            )
            found.append(
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.NONE,
                    reason,
                    message,
                    basis,
                    line_id=line.line_id,
                )
            )
        elif intake_drift:
            # 인테이크(검토 시점) 전용 — 저장본 SKU는 **검토자가 본 값**이다. 지금 매핑이 저장본과 다르면(매핑 소멸·다른 SKU·검토 뒤 신규 매핑) 낡은 검토다.
            # 단종 분기보다 먼저 본다(단종 SKU가 낡은 매핑을 가리고 통과하지 않게). 소멸=ITEM_UNMAPPED(422), 변경=MAPPING_CHANGED(409 STALE_MAPPING).
            found.append(
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.NONE,
                    "ITEM_UNMAPPED" if mapped is None else "MAPPING_CHANGED",
                    "바이어 품번 매핑이 없습니다. 거래처 품번 매핑을 등록해 주세요."
                    if mapped is None
                    else "검토한 뒤 바이어 품번 매핑이 바뀌었습니다. 품번을 다시 해석해 확인해 주세요.",
                    basis if mapped is None else {**basis, "mapped_sku_id": mapped.sku_id},
                    line_id=line.line_id,
                )
            )
        elif sku.status == SKU_STATUS_DISCONTINUED:
            found.append(
                outcome(
                    code,
                    GateLevel.WARN if phase is GatePhase.INTAKE else GateLevel.BLOCK,
                    GateResolution.NONE,
                    "SKU_DISCONTINUED",
                    "단종된 품목입니다. 확정하려면 품목 상태를 먼저 확인해 주세요.",
                    basis,
                    line_id=line.line_id,
                )
            )
        elif buyer_code is not None and mapped is None:
            found.append(
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.NONE,
                    "ITEM_UNMAPPED",
                    "바이어 품번 매핑이 없습니다(삭제되었거나 바뀌었을 수 있습니다). 거래처 품번 매핑을 확인해 주세요.",
                    basis,
                    line_id=line.line_id,
                )
            )
        elif buyer_code is not None and mapped is not None and mapped.sku_id != line.sku_id:
            found.append(
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.NONE,
                    "MAPPING_CHANGED",
                    "바이어 품번 매핑이 다른 품목으로 바뀌었습니다. 수주 품목과 품번 매핑을 확인해 주세요.",
                    {**basis, "mapped_sku_id": mapped.sku_id},
                    line_id=line.line_id,
                )
            )
    return found or [_passed(code, "MAPPED", "모든 품목이 확인되었습니다.")]


# ── DUPLICATE_PO ─────────────────────────────────────────────────────────────


def evaluate_duplicate_po(
    session: Session, subject: GateSubject, phase: GatePhase
) -> list[GateOutcome]:
    """같은 (거래처, 바이어 PO 키)의 다른 비취소 SO = BLOCK/NONE(데이터 무결성 — override 불가). PO번호 없음 = WARN(중복 확인 불가를 기록으로 남긴다)."""
    _require_so_or_intake(subject)
    code = GateCode.DUPLICATE_PO
    key = subject.po_no_key
    if subject.kind == SUBJECT_INTAKE and key is None:
        # 인테이크는 PO 키가 NOT NULL이다 — 없으면 어댑터 결함이라 통과(WARN)가 아니라 평가 불능(UNKNOWN)이어야 한다(fail-closed).
        raise ValueError("인테이크 평가 대상에 PO 비교 키가 없습니다.")
    if key is None:
        return [
            outcome(
                code,
                GateLevel.WARN,
                GateResolution.NONE,
                "PO_NO_NOT_GIVEN",
                "바이어 PO번호가 없어 중복 여부를 확인할 수 없습니다(견적·PI 유래 수주는 정상입니다).",
            )
        ]
    if subject.kind == SUBJECT_INTAKE:
        # 인테이크(검토 시점): 다른 **PENDING 인테이크**·살아 있는 비취소 SO가 같은 (거래처, 키)를 점유하면 BLOCK(데이터 무결성 — override 불가, 착지·확정 모두 거부).
        occupied = intake_service.occupant(
            session, subject.buyer_partner_id, key, exclude_intake_id=subject.id
        )
        if occupied is not None:
            return [
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.NONE,
                    "DUPLICATE_PO_NO",
                    "같은 거래처·같은 바이어 PO번호의 다른 오더가 이미 있습니다. 기존 인테이크를 거부하거나 수주를 취소하거나 PO번호를 확인해 주세요.",
                    {"buyer_po_no_key": key},
                    detail={
                        "other_doc_number": occupied.get("doc_number"),
                        "other_intake_id": occupied.get("intake_id"),
                        "other_status": occupied["status"],
                    },
                )
            ]
        return [
            _passed(
                code,
                "NO_DUPLICATE",
                "같은 PO번호의 다른 오더가 없습니다.",
                {"buyer_po_no_key": key},
            )
        ]
    other = session.execute(
        select(SalesOrder.doc_number, SalesOrder.status)
        .where(
            SalesOrder.buyer_partner_id == subject.buyer_partner_id,
            SalesOrder.buyer_po_no_key == key,
            SalesOrder.id != subject.id,
            SalesOrder.deleted_at.is_(None),
            SalesOrder.status != SalesOrderStatus.CANCELLED.value,
        )
        .order_by(SalesOrder.id)
        .limit(1)
    ).first()
    if other is not None:
        return [
            outcome(
                code,
                GateLevel.BLOCK,
                GateResolution.NONE,
                "DUPLICATE_PO_NO",
                "같은 거래처·같은 바이어 PO번호의 다른 수주가 있습니다. 기존 수주를 취소하거나 PO번호를 확인해 주세요.",
                {"buyer_po_no_key": key},
                # 다른 문서번호·상태는 표시 전용 상세(해시·증거 불포함) — 무역·관리자 응답에만 실린다
                detail={"other_doc_number": other[0], "other_status": other[1]},
            )
        ]
    return [
        _passed(
            code, "NO_DUPLICATE", "같은 PO번호의 다른 수주가 없습니다.", {"buyer_po_no_key": key}
        )
    ]


# ── PRICE_DEVIATION ──────────────────────────────────────────────────────────


def _pct_text(bps: int) -> str:
    """bp(정수) → 퍼센트 문자열(소수 2자리) — 화면은 산술 없이 그대로 표시한다."""
    return f"{bps // 100}.{bps % 100:02d}"


def evaluate_price_deviation(
    session: Session, subject: GateSubject, phase: GatePhase
) -> list[GateOutcome]:
    """라인별 단가 편차 — **정수 교차곱셈** `abs(unit−ref)×10000 > tolerance_bp×ref`면 초과(정확히 허용치는 통과, Float 없음). 양방향.

    기준가 = 라인의 `list_price_amount`(SO: 라인 생성 시점 판가 스냅샷 — 확정 시 mutable한 `sku_prices`를 다시 읽지 않는다). 없음·≤0·통화 불일치는 환산하지 않고 UNKNOWN(override 가능).
    무상(`is_free`) 라인은 WARN. 허용치는 정책 `price_deviation_tolerance_bp`(미설정 = 0bp, `policy_source=UNSET_DEFAULT`로 드러난다). **PURCHASE 유형·매입가는 조회하지 않는다.**
    """
    _require_so_or_intake(subject)
    code = GateCode.PRICE_DEVIATION
    policy = get_policy(session, POLICY_PRICE_TOLERANCE)
    tolerance = int(policy.value)
    found: list[GateOutcome] = []
    for line in subject.lines:
        base: Basis = {
            "sku_id": line.sku_id,
            "unit_price_amount": line.unit_price_amount,
            "currency": subject.currency,
            "tolerance_bp": tolerance,
            "policy_source": policy.source,
        }
        if line.is_free:
            found.append(
                outcome(
                    code,
                    GateLevel.WARN,
                    GateResolution.NONE,
                    "FREE_LINE",
                    "무상 라인입니다. 단가 편차를 판정하지 않고 기록으로 남깁니다.",
                    base,
                    line_id=line.line_id,
                )
            )
            continue
        ref = line.list_price_amount
        if ref is None:
            found.append(
                outcome(
                    code,
                    GateLevel.UNKNOWN,
                    GateResolution.OVERRIDE,
                    "NO_REFERENCE_PRICE",
                    "기준 단가가 없어 편차를 판정할 수 없습니다. 해당 통화의 판매 단가를 등록하거나 사유를 남겨 예외 통과해 주세요.",
                    base,
                    line_id=line.line_id,
                )
            )
        elif ref <= 0:
            found.append(
                outcome(
                    code,
                    GateLevel.UNKNOWN,
                    GateResolution.OVERRIDE,
                    "REFERENCE_INVALID",
                    "기준 단가가 올바르지 않아 편차를 판정할 수 없습니다. 판매 단가 마스터를 확인해 주세요.",
                    {**base, "reference_price_amount": ref},
                    line_id=line.line_id,
                )
            )
        else:
            diff = abs(line.unit_price_amount - ref)
            # 표시용 편차는 **올림**(bp·퍼센트) — 허용치를 1단위라도 넘긴 BLOCK이 "허용치와 같은 값"으로 보이는 오독을 막는다(판정은 교차곱)
            deviation_bps = -(-diff * 10000 // ref)
            detail: Basis = {
                **base,
                "reference_price_amount": ref,
                "deviation_bp": deviation_bps,
                "deviation_pct": _pct_text(deviation_bps),
                "direction": (
                    "ABOVE"
                    if line.unit_price_amount > ref
                    else "BELOW"
                    if line.unit_price_amount < ref
                    else "EQUAL"
                ),
            }
            if diff * 10000 > tolerance * ref:
                found.append(
                    outcome(
                        code,
                        GateLevel.BLOCK,
                        GateResolution.OVERRIDE,
                        "TOLERANCE_EXCEEDED",
                        "단가가 기준 단가의 허용 편차를 벗어났습니다. 단가를 확인하거나 사유를 남겨 예외 통과해 주세요.",
                        detail,
                        line_id=line.line_id,
                    )
                )
    return found or [
        _passed(code, "WITHIN_TOLERANCE", "모든 라인의 단가가 허용 편차 안에 있습니다.")
    ]


# ── CREDIT (E의 평가를 감싸는 어댑터 — 통과 판정 복제 금지) ───────────────────────


def evaluate_credit_gate(
    session: Session, subject: GateSubject, phase: GatePhase
) -> list[GateOutcome]:
    """여신 — 한도 초과=BLOCK/APPROVAL(승인 소비로만 해소, override 불가) / 평가 불능=UNKNOWN/NONE(승인 경로 없음 — X-27) / 한도 이내·한도 NULL=PASS."""
    _require_so(subject)
    code = GateCode.CREDIT
    order = session.execute(
        select(SalesOrder)
        .where(SalesOrder.id == subject.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    if subject.authoritative:
        locked = lock_buyer_for_credit(session, order.buyer_partner_id)
        evaluated = credit_evaluation.evaluate_credit(session, locked, order)
    else:
        evaluated = credit_evaluation.evaluate_credit_unlocked(session, order)
    snapshot = evaluated.to_snapshot()
    snapshot.pop("evaluated_at", None)  # 시각은 판정 입력이 아니다(해시 안정)
    basis: Basis = {**snapshot, "advisory": not subject.authoritative}
    verdict = evaluated.verdict
    if verdict is credit_evaluation.CreditVerdict.EXCEEDED:
        return [
            outcome(
                code,
                GateLevel.BLOCK,
                GateResolution.APPROVAL,
                "LIMIT_EXCEEDED",
                "여신 한도를 초과합니다. 승인을 요청해 승인된 뒤에 확정할 수 있습니다.",
                basis,
            )
        ]
    if verdict is credit_evaluation.CreditVerdict.UNEVALUABLE:
        return [
            outcome(
                code,
                GateLevel.UNKNOWN,
                GateResolution.NONE,
                evaluated.reason_codes[0] if evaluated.reason_codes else "UNEVALUABLE",
                "여신을 평가할 수 없습니다(통화 비교 불가 등). 승인으로 우회할 수 없으니 거래처 여신 통화·전표 환율을 확인해 주세요.",
                basis,
            )
        ]
    if verdict in (
        credit_evaluation.CreditVerdict.WITHIN_LIMIT,
        credit_evaluation.CreditVerdict.NOT_MANAGED,
    ):
        return [
            _passed(
                code,
                verdict.value,
                "여신 한도 이내이거나 여신을 관리하지 않는 거래처입니다.",
                basis,
            )
        ]
    raise ValueError(f"알 수 없는 여신 판정입니다: {verdict!r}")


# ── MARKET_READINESS ─────────────────────────────────────────────────────────


def evaluate_market_readiness(
    session: Session, subject: GateSubject, phase: GatePhase
) -> list[GateOutcome]:
    """시장 준비 상태 — `readiness.cells_for`(매트릭스와 같은 규칙) 재사용. RED=BLOCK/OVERRIDE(ADMIN) · **GRAY=UNKNOWN/OVERRIDE(ADMIN)**(통과로 읽지 않는다) · YELLOW=WARN · GREEN=PASS ·
    시장·SKU 소멸=UNKNOWN. 결과는 계산값 표시이며 법적 판정이 아니다(메시지에 판정 워딩 금지)."""
    _require_so_or_intake(subject)
    code = GateCode.MARKET_READINESS
    market = subject.dest_market_code
    sku_ids = sorted({line.sku_id for line in subject.lines if line.sku_id is not None})
    if market is None or not sku_ids:
        return [
            outcome(
                code,
                GateLevel.UNKNOWN,
                GateResolution.OVERRIDE,
                "MARKET_NOT_FOUND",
                f"{NOTE_READINESS}: 목적지 시장이 없어 준비 상태를 확인할 수 없습니다.",
                {"market_code": market},
            )
        ]
    try:
        cells = readiness_service.cells_for(sku_ids=sku_ids, market_code=market)
    except AppError:
        return [
            outcome(
                code,
                GateLevel.UNKNOWN,
                GateResolution.OVERRIDE,
                "MARKET_NOT_FOUND",
                f"{NOTE_READINESS}: 등록되지 않은 시장이라 준비 상태를 확인할 수 없습니다.",
                {"market_code": market},
            )
        ]
    found: list[GateOutcome] = []
    for line in subject.lines:
        cell = cells.get(line.sku_id) if line.sku_id is not None else None
        if cell is None:
            found.append(
                outcome(
                    code,
                    GateLevel.UNKNOWN,
                    GateResolution.OVERRIDE,
                    "SKU_NOT_FOUND",
                    f"{NOTE_READINESS}: 품목을 찾을 수 없어 준비 상태를 확인할 수 없습니다.",
                    {"market_code": market, "sku_id": line.sku_id},
                    line_id=line.line_id,
                )
            )
            continue
        color = cell.summary.color
        basis: Basis = {
            "prep_state": color,
            "unmet_count": cell.summary.unmet,
            "market_code": market,
            "sku_id": line.sku_id,
        }
        if color == readiness_rules.RED:
            found.append(
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.OVERRIDE,
                    "REQUIREMENTS_UNMET",
                    f"{NOTE_READINESS}: 충족되지 않은 요건이 있습니다. 요건 상태를 확인하거나 관리자가 사유를 남겨 예외 통과해 주세요.",
                    basis,
                    line_id=line.line_id,
                )
            )
        elif color == readiness_rules.GRAY:
            found.append(
                outcome(
                    code,
                    GateLevel.UNKNOWN,
                    GateResolution.OVERRIDE,
                    "NO_REQUIREMENTS",
                    f"{NOTE_READINESS}: 이 품목·시장에 필수 요건이 등록되어 있지 않아 상태를 알 수 없습니다.",
                    basis,
                    line_id=line.line_id,
                )
            )
        elif color == readiness_rules.YELLOW:
            found.append(
                outcome(
                    code,
                    GateLevel.WARN,
                    GateResolution.NONE,
                    "REQUIREMENTS_IN_PROGRESS",
                    f"{NOTE_READINESS}: 진행 중인 요건이 있습니다.",
                    basis,
                    line_id=line.line_id,
                )
            )
        elif color != readiness_rules.GREEN:
            raise ValueError(f"알 수 없는 준비 상태 값입니다: {color!r}")
    return found or [
        _passed(code, "REQUIREMENTS_OK", f"{NOTE_READINESS}: 표시된 요건이 모두 완료 상태입니다.")
    ]


# ── MOQ ──────────────────────────────────────────────────────────────────────


def evaluate_moq(session: Session, subject: GateSubject, phase: GatePhase) -> list[GateOutcome]:
    """같은 SKU **유상 라인 수량 합** < `skus.moq`(EA)면 BLOCK/OVERRIDE(무역·관리자). `moq` NULL = PASS('정책 없음'은 평가 실패가 아니다 — `moq_unset=true` 안내). 무상 라인은 대상이 아니다.
    결과는 SKU 묶음의 첫 라인(line_no 최소)에 붙는다. SKU를 읽을 수 없으면 UNKNOWN/NONE(틀 결과) — 품번 게이트가 먼저 막는다."""
    _require_so_or_intake(subject)
    code = GateCode.MOQ
    groups: dict[int, list[GateLine]] = defaultdict(list)
    for line in subject.lines:
        if not line.is_free and line.sku_id is not None:
            groups[line.sku_id].append(line)
    if not groups:
        return [_passed(code, "NO_PAID_LINES", "최소 주문 수량을 확인할 유상 라인이 없습니다.")]
    skus = {s.id: s for s in session.execute(select(Sku).where(Sku.id.in_(set(groups)))).scalars()}
    found: list[GateOutcome] = []
    unset = 0
    for sku_id, lines in groups.items():
        sku = skus.get(sku_id)
        first = min(lines, key=lambda x: (x.line_no, x.line_id))
        if sku is None or sku.deleted_at is not None:
            found.append(
                outcome(
                    code,
                    GateLevel.UNKNOWN,
                    GateResolution.NONE,
                    "SKU_NOT_FOUND",
                    "품목을 읽을 수 없어 최소 주문 수량을 확인할 수 없습니다. 품목을 확인해 주세요.",
                    {"sku_id": sku_id},
                    line_id=first.line_id,
                )
            )
            continue
        quantity = sum(line.quantity for line in lines)
        if sku.moq is None:
            unset += 1
        elif quantity < sku.moq:
            found.append(
                outcome(
                    code,
                    GateLevel.BLOCK,
                    GateResolution.OVERRIDE,
                    "BELOW_MOQ",
                    "주문 수량이 최소 주문 수량에 못 미칩니다. 수량을 늘리거나 사유를 남겨 예외 통과해 주세요.",
                    {"sku_id": sku_id, "quantity": quantity, "moq": sku.moq, "unit": "EA"},
                    line_id=first.line_id,
                )
            )
    # `moq` NULL = "정책 없음"은 평가 실패가 아니라 PASS다 — 안내용 증거(`moq_unset`)만 남긴다.
    return found or [
        _passed(
            code,
            "MOQ_OK",
            "모든 품목이 최소 주문 수량을 충족하거나 설정되어 있지 않습니다.",
            {"moq_unset": unset > 0, "moq_unset_count": unset},
        )
    ]


# ── PI_DEPOSIT ───────────────────────────────────────────────────────────────

_PI_MESSAGES = {
    "SHORT": "선수금 입금이 청구액에 못 미칩니다.",
    "PI_MISSING": "선수금 T/T 수주인데 연결된 PI가 없어 입금을 확인할 수 없습니다.",
    "PI_NOT_USABLE": "연결된 PI가 입금을 받을 수 있는 상태가 아니어서 입금을 확인할 수 없습니다.",
}


def evaluate_pi_deposit(
    session: Session, subject: GateSubject, phase: GatePhase
) -> list[GateOutcome]:
    """PI 선수금 입금 게이트 — 판정은 `payments.pi_gate.evaluate_pi_advance`(순수 함수), 모드는 정책 `pi_advance_gate_mode`(미설정=BLOCK·`policy_source=UNSET_DEFAULT`).

    모드 변환: OFF→PASS(`SKIPPED_OFF`, 스킵 사실 기록) / WARN→미충족은 `WARN`(진행·기록) / BLOCK→입금 부족=`BLOCK/OVERRIDE(ADMIN)`, PI 미연결·사용 불가=`UNKNOWN/OVERRIDE(ADMIN)`.
    비선수금(L/C·후불)은 모드와 무관하게 PASS(`INACTIVE`). 결제조건 부재 등 판정 불능은 `UNKNOWN/NONE`(결제조건이 정해져야 한다).
    """
    _require_so(subject)
    code = GateCode.PI_DEPOSIT
    mode = get_policy(session, POLICY_PI_MODE)
    pi = (
        session.execute(
            select(ProformaInvoice)
            .where(ProformaInvoice.id == subject.pi_id, ProformaInvoice.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if subject.pi_id is not None
        else None
    )
    evaluated = evaluate_pi_advance(
        session,
        so_terms=SoTerms(subject.payment_type, subject.advance_pct_bp),
        pi=pi,
        pi_referenced=subject.pi_id is not None,
        mode=str(mode.value),
    )
    basis: Basis = {
        "mode": evaluated.mode,
        "policy_source": mode.source,
        "terms_basis": evaluated.terms_basis,
        "terms_diverge": evaluated.terms_diverge,
        "payment_type": evaluated.payment_type,
        "required_amount": evaluated.required_amount,
        "received_amount": evaluated.received_amount,
        "currency": evaluated.currency,
    }
    state = evaluated.state
    if state is PiAdvanceState.INDETERMINATE:
        return [
            outcome(
                code,
                GateLevel.UNKNOWN,
                GateResolution.NONE,
                evaluated.reason,
                "결제조건이 정해지지 않아 선수금 입금을 판정할 수 없습니다. 결제조건을 입력해 주세요.",
                basis,
            )
        ]
    if state is PiAdvanceState.UNMET:
        message = _PI_MESSAGES.get(evaluated.reason, "선수금 입금을 확인할 수 없습니다.")
        if evaluated.mode == MODE_WARN:
            return [
                outcome(
                    code,
                    GateLevel.WARN,
                    GateResolution.NONE,
                    evaluated.reason,
                    f"{message} 경고 모드라 진행하되 확정 기록에 남깁니다.",
                    basis,
                )
            ]
        level = GateLevel.BLOCK if evaluated.reason == "SHORT" else GateLevel.UNKNOWN
        return [
            outcome(
                code,
                level,
                GateResolution.OVERRIDE,
                evaluated.reason,
                f"{message} 입금을 확인하거나 관리자가 사유를 남겨 예외 통과해 주세요.",
                basis,
            )
        ]
    return [_passed(code, evaluated.reason, "선수금 입금 조건을 통과했습니다.", basis)]


# ── 등록 ─────────────────────────────────────────────────────────────────────

EVALUATORS = {
    GateCode.ITEM_MAPPING: evaluate_item_mapping,
    GateCode.DUPLICATE_PO: evaluate_duplicate_po,
    GateCode.PRICE_DEVIATION: evaluate_price_deviation,
    GateCode.CREDIT: evaluate_credit_gate,
    GateCode.MARKET_READINESS: evaluate_market_readiness,
    GateCode.MOQ: evaluate_moq,
    GateCode.PI_DEPOSIT: evaluate_pi_deposit,
}


def register_evaluators(registry: EvaluatorRegistry | None = None) -> None:
    """7 평가기를 등록한다 — 전역 등록부는 앱 조립 시점에 1회(이미 등록돼 있으면 건너뛴다, 테스트 복원용 재호출 허용)."""
    target = registry if registry is not None else gate_registry.DEFAULT_REGISTRY
    for gate, evaluator in EVALUATORS.items():
        if gate.value not in target.codes():
            target.register(gate, evaluator)


register_evaluators()
