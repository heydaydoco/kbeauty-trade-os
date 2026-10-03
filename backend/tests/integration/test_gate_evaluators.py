"""A. 게이트 7종 평가기 — 게이트 × 도달 가능한 결과값 전수, 경계, PI 게이트 활성/비활성 양방향 (S3-1 PR-11a / design-D D3·D4 / design-E E6).

각 테스트는 `@covers(게이트, 결과, 해소[, 시점])`로 자기가 검증하는 명세 조합을 정적으로 장부에 올린다 — 메타 테스트(`test_gate_specs_meta`)가 `GATE_SPECS`의 모든 조합에 테스트가 있음을
대사한다(공회전 방지). 평가기는 읽기 전용이고 결과 조합은 `gates.policy.outcome` 팩토리가 명세로 검증한다. 테스트 데이터는 전부 익명 합성이다.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.gates import service as gates_service
from app.modules.gates.types import GateCode, GateLevel, GatePhase, GateResolution
from app.modules.identity.models import RoleCode
from app.modules.readiness import service as readiness_service
from app.modules.trade_chain import gate_evaluators
from tests.factories.approvals import credit_so, set_credit_limit
from tests.factories.gates import (
    evaluate,
    line_ids,
    make_free,
    one,
    passing_so,
    scalar,
    set_line,
    set_policy,
    set_terms,
)
from tests.factories.payments import advance_pi, set_payment_terms
from tests.factories.trade import (
    create_buyer,
    create_direct_so,
    raw_so,
    unique,
)
from tests.support.factories import create_sku, create_user
from tests.support.gate_coverage import covers

pytestmark = pytest.mark.group_a

L = GateLevel
R = GateResolution
G = GateCode
INTAKE = GatePhase.INTAKE


def _sql(sql: str, **params: Any) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


def _eval_subject(subject: Any, gate: GateCode, phase: GatePhase = GatePhase.CONFIRM) -> Any:
    """평가 대상(변형된 GateSubject)을 직접 평가 — 마스터가 소멸한 상황(시장·SKU)을 재현한다."""
    with unit_of_work() as uow:
        return gates_service.evaluate_all(uow.session, subject, phase, only=[gate])


def _subject(so_id: int) -> Any:
    with unit_of_work() as uow:
        return gate_evaluators.subject_from_sales_order(uow.session, so_id, authoritative=False)


# ══ 기준 SO — 7종 전부 PASS ═════════════════════════════════════════════════════


def test_the_baseline_sales_order_passes_every_gate() -> None:
    """기준 SO(직접 수주·준비도 GREEN·LC·여신 미관리·PO번호 있음)는 7종 모두 PASS이고 확정 가능 판정이다 — 이후 테스트의 대조군"""
    so = passing_so()
    outcomes = evaluate(so["id"])
    assert [o.gate_code for o in outcomes] == [g.value for g in GateCode]  # 표시 순서 = 명단 순서
    assert {o.level for o in outcomes} == {L.PASS}
    assert gates_service.clearance(outcomes, {}, False).cleared is True


def test_the_intake_phase_skips_the_confirm_only_gates() -> None:
    """CREDIT·PI_DEPOSIT는 확정 시점 전용 — 인테이크 시점 평가에는 나오지 않는다(명세 phases)"""
    so = passing_so()
    codes = {o.gate_code for o in evaluate(so["id"], phase=INTAKE)}
    assert codes == {"ITEM_MAPPING", "DUPLICATE_PO", "PRICE_DEVIATION", "MARKET_READINESS", "MOQ"}


# ══ ITEM_MAPPING ═══════════════════════════════════════════════════════════════


@covers(G.ITEM_MAPPING, L.PASS, R.NONE)
def test_item_mapping_passes_with_a_matching_mapping_and_without_a_buyer_code() -> None:
    """바이어 품번이 같은 SKU로 매핑돼 있거나 품번이 없는 라인은 PASS(MAPPED) — 품번 없는 SO(QT·PI 유래)를 막지 않는다"""
    mapped = passing_so(map_code=True)
    result = one(evaluate(mapped["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING)
    assert (result.level, result.resolution, result.reason_code) == (L.PASS, R.NONE, "MAPPED")
    plain = passing_so()
    assert one(evaluate(plain["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING).level is L.PASS


@covers(G.ITEM_MAPPING, L.BLOCK, R.NONE)
def test_item_mapping_blocks_an_unmapped_buyer_code_without_any_override_path() -> None:
    """매핑이 사라진(삭제된) 바이어 품번은 BLOCK/NONE(ITEM_UNMAPPED) — 해소 수단도 override 역할도 없다(마스터를 고쳐야 한다)"""
    so = passing_so(map_code=True)
    _sql("UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p", p=so["buyer"])
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING, line)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.NONE,
        "ITEM_UNMAPPED",
    )
    assert result.override_roles == ()


def test_item_mapping_blocks_a_mapping_that_now_points_to_another_sku() -> None:
    """품번 매핑이 다른 SKU로 바뀌었으면 BLOCK(MAPPING_CHANGED) — 수주 품목과 매핑이 어긋난 채 확정되지 않는다"""
    so = passing_so(map_code=True)
    other = create_sku(unique("OTHER"))
    _sql(
        "UPDATE customer_item_codes SET sku_id = :s WHERE partner_id = :p",
        s=other,
        p=so["buyer"],
    )
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING, line)
    assert (result.level, result.reason_code) == (L.BLOCK, "MAPPING_CHANGED")
    assert result.basis["mapped_sku_id"] == other


def test_item_mapping_blocks_a_deleted_sku() -> None:
    """삭제된 SKU가 담긴 라인은 BLOCK(SKU_DELETED)"""
    so = passing_so()
    _sql("UPDATE skus SET deleted_at = now() WHERE id = :s", s=so["sku_ids"][0])
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING, line)
    assert (result.level, result.reason_code) == (L.BLOCK, "SKU_DELETED")


def test_item_mapping_blocks_when_the_buyer_lost_the_buyer_type() -> None:
    """거래처의 BUYER 유형이 해제됐으면 게이트 단위 BLOCK(BUYER_LOST) — 품번 매핑을 믿을 수 없다"""
    so = passing_so()
    _sql(
        "UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p AND type_code = 'BUYER'",
        p=so["buyer"],
    )
    result = one(evaluate(so["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING)
    assert (result.level, result.resolution, result.reason_code) == (L.BLOCK, R.NONE, "BUYER_LOST")


@covers(G.ITEM_MAPPING, L.BLOCK, R.NONE)
def test_a_discontinued_sku_blocks_at_confirm() -> None:
    """단종(DISCONTINUED) SKU는 확정 시점에 BLOCK/NONE — 마스터 상태를 되돌리는 가시적 조치가 선행돼야 한다(A11-2)"""
    so = passing_so()
    _sql("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :s", s=so["sku_ids"][0])
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.ITEM_MAPPING]), G.ITEM_MAPPING, line)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.NONE,
        "SKU_DISCONTINUED",
    )


@covers(G.ITEM_MAPPING, L.WARN, R.NONE, INTAKE)
def test_a_discontinued_sku_only_warns_at_intake() -> None:
    """같은 단종 SKU가 인테이크(접수) 시점에는 WARN — 접수는 허용하고 확정에서 막는다"""
    so = passing_so()
    _sql("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :s", s=so["sku_ids"][0])
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.ITEM_MAPPING], phase=INTAKE), G.ITEM_MAPPING, line)
    assert (result.level, result.resolution) == (L.WARN, R.NONE)


# ══ DUPLICATE_PO ═══════════════════════════════════════════════════════════════


@covers(G.DUPLICATE_PO, L.PASS, R.NONE)
def test_duplicate_po_passes_when_no_other_live_order_holds_the_key() -> None:
    """같은 (거래처, PO 키)의 다른 비취소 SO가 없으면 PASS — 취소된 SO가 같은 번호를 쥐고 있어도 재사용은 허용(정정=취소+신규)"""
    buyer = create_buyer()
    first = create_direct_so(buyer, buyer_po_no="PO-REUSE-1")
    _sql("UPDATE sales_orders SET status = 'CANCELLED' WHERE id = :i", i=first["id"])
    second = create_direct_so(buyer, buyer_po_no="PO-REUSE-1")
    result = one(evaluate(second["id"], only=[G.DUPLICATE_PO]), G.DUPLICATE_PO)
    assert (result.level, result.reason_code) == (L.PASS, "NO_DUPLICATE")


@covers(G.DUPLICATE_PO, L.WARN, R.NONE)
def test_duplicate_po_warns_when_the_order_has_no_po_number() -> None:
    """PO번호가 없는 SO(QT·PI 유래는 정상)는 WARN(PO_NO_NOT_GIVEN) — 중복 확인 불가를 기록으로 남기되 막지 않는다"""
    so = passing_so(po_no=None)
    result = one(evaluate(so["id"], only=[G.DUPLICATE_PO]), G.DUPLICATE_PO)
    assert (result.level, result.resolution, result.reason_code) == (
        L.WARN,
        R.NONE,
        "PO_NO_NOT_GIVEN",
    )


@covers(G.DUPLICATE_PO, L.BLOCK, R.NONE)
def test_duplicate_po_blocks_a_live_duplicate_and_it_cannot_be_overridden() -> None:
    """같은 거래처·같은 PO 키의 다른 비취소 SO가 있으면 BLOCK/NONE(override 불가). SO 부분 유니크가 정상 경로의 중복을 막으므로 합성 대상(po_no_key = 다른 SO의 키)으로 평가기를 직접 부른다 — DDL 조작 없음.
    다른 문서번호·상태는 해시에 안 들어가는 표시 전용 detail에만 있다"""
    buyer = create_buyer()
    first = create_direct_so(buyer, buyer_po_no="PO-DUP-1")
    second = create_direct_so(buyer, buyer_po_no="PO-OTHER-2")
    first_key = scalar("SELECT buyer_po_no_key FROM sales_orders WHERE id = :i", i=first["id"])
    subject = dataclasses.replace(_subject(second["id"]), po_no_key=first_key)
    (result,) = _eval_subject(subject, G.DUPLICATE_PO)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.NONE,
        "DUPLICATE_PO_NO",
    )
    assert result.detail["other_doc_number"] == first["doc_number"]
    assert "other_doc_number" not in result.basis and result.override_roles == ()
    twin = dataclasses.replace(result, detail={"other_doc_number": "SO-X"})
    assert twin.basis_hash == result.basis_hash  # 표시 상세는 해시에 영향이 없다
    # 취소된 SO가 쥔 키는 점유가 아니다(반대 방향 대조)
    _sql("UPDATE sales_orders SET status = 'CANCELLED' WHERE id = :i", i=first["id"])
    (free,) = _eval_subject(subject, G.DUPLICATE_PO)
    assert free.level is L.PASS


# ══ PRICE_DEVIATION ═════════════════════════════════════════════════════════════


def _price_outcome(unit: int, tolerance: int | None, *, reference: int | None = 1000) -> Any:
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", tolerance)
    set_line(so["id"], 1, unit_price=unit, list_price=reference)
    (line,) = line_ids(so["id"])
    return one(evaluate(so["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION, line)


@covers(G.PRICE_DEVIATION, L.PASS, R.NONE)
@pytest.mark.parametrize(
    ("unit", "tolerance"),
    [(1000, 500), (1050, 500), (950, 500), (1000, 0)],
    ids=["same", "upper-edge", "lower-edge", "zero-tolerance-exact"],
)
def test_a_price_exactly_at_the_tolerance_passes(unit: int, tolerance: int) -> None:
    """허용치에 정확히 걸린 단가는 통과(정수 교차곱셈 `>` — `>=`가 아니다)·양방향. 모든 라인이 통과면 게이트 단위 PASS 1건"""
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", tolerance)
    set_line(so["id"], 1, unit_price=unit, list_price=1000)
    result = one(evaluate(so["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION)
    assert (result.level, result.reason_code) == (L.PASS, "WITHIN_TOLERANCE")


@covers(G.PRICE_DEVIATION, L.BLOCK, R.OVERRIDE)
@pytest.mark.parametrize(
    ("unit", "tolerance", "direction"),
    [(1051, 500, "ABOVE"), (949, 500, "BELOW"), (1001, 0, "ABOVE"), (999, 0, "BELOW")],
    ids=["1-above-edge", "1-below-edge", "zero-above", "zero-below"],
)
def test_a_price_one_unit_past_the_tolerance_blocks_with_an_override_path(
    unit: int, tolerance: int, direction: str
) -> None:
    """허용치를 1단위라도 넘으면(고가·저가 양방향) BLOCK/OVERRIDE(무역·관리자) — 서버가 편차(bp·퍼센트 문자열)를 계산해 싣는다"""
    result = _price_outcome(unit, tolerance)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.OVERRIDE,
        "TOLERANCE_EXCEEDED",
    )
    assert result.override_roles == (RoleCode.TRADE, RoleCode.ADMIN)
    assert result.basis["direction"] == direction
    assert result.basis["tolerance_bp"] == tolerance
    assert result.basis["policy_source"] == "SET"


def test_the_price_check_is_an_integer_cross_multiplication_not_a_floored_division() -> None:
    """기준 3·단가 4·허용 3333bp: 편차 33.333…%는 허용 33.33%를 넘는다(교차곱 10000 > 9999) — 내림 나눗셈(10000//3 = 3333)으로 비교하면 통과로 오판한다"""
    so = passing_so(price=3)
    set_policy("price_deviation_tolerance_bp", 3333)
    set_line(so["id"], 1, unit_price=4, list_price=3)
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION, line)
    assert result.level is L.BLOCK
    # 표시 편차는 올림 — 허용치(33.33%)를 넘긴 BLOCK이 "33.33"으로 보이는 오독이 없다(판정은 교차곱)
    assert result.basis["deviation_bp"] == 3334
    assert result.basis["deviation_pct"] == "33.34"


def test_the_price_basis_carries_the_sku_so_the_hash_binds_the_product() -> None:
    """가격 근거에 sku_id가 실린다 — 같은 가격 조건에서 라인의 SKU가 바뀌면 판정 해시가 달라져 기존 override가 무효가 된다(결속 강화)"""
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=1100, list_price=1000)
    (line,) = line_ids(so["id"])
    before = one(evaluate(so["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION, line)
    assert before.basis["sku_id"] == so["sku_ids"][0]
    other = create_sku(unique("SWAP"))
    set_line(so["id"], 1, sku_id=other)
    after = one(evaluate(so["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION, line)
    assert after.basis["sku_id"] == other and after.basis_hash != before.basis_hash


def test_an_unset_tolerance_means_zero_basis_points_and_says_so() -> None:
    """정책 미설정 = 0bp(어떤 편차도 통과하지 않음)이고 `policy_source=UNSET_DEFAULT`로 드러난다 — 정확히 기준가인 라인은 통과"""
    exact = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", None)
    assert one(evaluate(exact["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION).level is L.PASS
    off = _price_outcome(1001, None)
    assert off.level is L.BLOCK
    assert off.basis["policy_source"] == "UNSET_DEFAULT"
    assert off.basis["tolerance_bp"] == 0


@covers(G.PRICE_DEVIATION, L.WARN, R.NONE)
def test_a_free_line_warns_instead_of_being_judged() -> None:
    """무상(is_free) 라인은 WARN(FREE_LINE) — 단가 0을 편차로 오판하지 않고 기록으로 남긴다"""
    so = passing_so()
    make_free(so["id"])
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.PRICE_DEVIATION]), G.PRICE_DEVIATION, line)
    assert (result.level, result.resolution, result.reason_code) == (L.WARN, R.NONE, "FREE_LINE")


@covers(G.PRICE_DEVIATION, L.UNKNOWN, R.OVERRIDE)
def test_a_missing_reference_price_is_unknown_not_a_pass() -> None:
    """기준가가 없으면 UNKNOWN(NO_REFERENCE_PRICE)/OVERRIDE — 통화 불일치를 환산해 맞추지 않고 평가 불능으로 둔다(통과 아님)"""
    result = _price_outcome(1000, 500, reference=None)
    assert (result.level, result.resolution, result.reason_code) == (
        L.UNKNOWN,
        R.OVERRIDE,
        "NO_REFERENCE_PRICE",
    )
    clearance = gates_service.clearance([result], {}, False)
    assert clearance.cleared is False  # UNKNOWN은 BLOCK과 같은 효과


def test_a_zero_reference_price_is_unknown_reference_invalid() -> None:
    """기준가 0은 UNKNOWN(REFERENCE_INVALID) — 0으로 나누지도, 0을 통과 기준으로 쓰지도 않는다"""
    result = _price_outcome(1000, 500, reference=0)
    assert (result.level, result.reason_code) == (L.UNKNOWN, "REFERENCE_INVALID")


# ══ CREDIT (E 평가를 감싸는 어댑터 — 통과 판정 복제 없음) ═══════════════════════════


@covers(G.CREDIT, L.PASS, R.NONE)
def test_credit_passes_when_the_limit_is_unmanaged_or_within_the_limit() -> None:
    """한도 NULL(여신 관리 안 함)과 한도 이내는 PASS — 이유 코드가 둘을 구분한다"""
    unmanaged = passing_so()
    assert one(evaluate(unmanaged["id"], only=[G.CREDIT]), G.CREDIT).reason_code == "NOT_MANAGED"
    within = credit_so(limit=10_000_000, unit_price=1000, quantity=5)
    result = one(evaluate(within["id"], only=[G.CREDIT]), G.CREDIT)
    assert (result.level, result.reason_code) == (L.PASS, "WITHIN_LIMIT")


@covers(G.CREDIT, L.BLOCK, R.APPROVAL)
def test_credit_over_the_limit_blocks_and_only_an_approval_resolves_it() -> None:
    """한도 초과는 BLOCK/APPROVAL — override 역할이 없다(승인 소비로만 해소). 근거에 한도·노출이 실리고 참고값 표시(advisory)가 붙는다"""
    so = credit_so(limit=100_000, unit_price=40_000, quantity=5)
    result = one(evaluate(so["id"], only=[G.CREDIT]), G.CREDIT)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.APPROVAL,
        "LIMIT_EXCEEDED",
    )
    assert result.override_roles == ()
    assert result.basis["advisory"] is True and result.basis["excess_amount"] == 100_000
    assert "evaluated_at" not in result.basis  # 시각은 판정 입력이 아니다(해시 안정)


@covers(G.CREDIT, L.UNKNOWN, R.NONE)
def test_credit_that_cannot_be_evaluated_is_unknown_with_no_resolution() -> None:
    """한도 통화(KRW)와 전표 통화(USD)를 비교할 수 없으면 UNKNOWN/NONE — 승인 경로가 없다(금액을 모르면 승인 상한을 정할 수 없다, X-27)"""
    so = passing_so()
    set_credit_limit(so["buyer"], 1_000_000, "KRW")
    result = one(evaluate(so["id"], only=[G.CREDIT]), G.CREDIT)
    assert (result.level, result.resolution) == (L.UNKNOWN, R.NONE)
    assert result.reason_code == "CURRENCY_NOT_CONVERTIBLE"


def test_the_authoritative_credit_evaluation_runs_under_the_buyer_lock_and_is_not_advisory() -> (
    None
):
    """확정 통로 평가(authoritative=True)는 거래처 잠금 하 평가이고 참고값 표시가 없다 — 조회는 잠금 없는 참고값(advisory=True). 해시는 두 경로가 달라도 결과 의미를 구분한다"""
    so = credit_so(limit=100_000, unit_price=40_000, quantity=5)
    advisory = one(evaluate(so["id"], only=[G.CREDIT], authoritative=False), G.CREDIT)
    authoritative = one(evaluate(so["id"], only=[G.CREDIT], authoritative=True), G.CREDIT)
    assert advisory.basis["advisory"] is True and authoritative.basis["advisory"] is False
    assert authoritative.level is L.BLOCK


# ══ MARKET_READINESS ═══════════════════════════════════════════════════════════


def _market(color: str) -> Any:
    so = passing_so(market_color=color)
    (line,) = line_ids(so["id"])
    return so, line, one(evaluate(so["id"], only=[G.MARKET_READINESS]), G.MARKET_READINESS, line)


@covers(G.MARKET_READINESS, L.PASS, R.NONE)
def test_market_readiness_green_passes() -> None:
    """준비 상태 GREEN은 PASS — 모든 라인이 통과면 게이트 단위 PASS 1건"""
    so = passing_so(market_color="GREEN")
    result = one(evaluate(so["id"], only=[G.MARKET_READINESS]), G.MARKET_READINESS)
    assert (result.level, result.reason_code) == (L.PASS, "REQUIREMENTS_OK")


@covers(G.MARKET_READINESS, L.WARN, R.NONE)
def test_market_readiness_yellow_warns() -> None:
    """YELLOW(진행 중 요건)는 WARN — 진행하되 확정 증거에 남긴다"""
    _, _, result = _market("YELLOW")
    assert (result.level, result.resolution) == (L.WARN, R.NONE)
    assert result.basis["prep_state"] == "YELLOW"


@covers(G.MARKET_READINESS, L.BLOCK, R.OVERRIDE)
def test_market_readiness_red_blocks_and_only_an_admin_may_override() -> None:
    """RED(미충족 요건)는 BLOCK/OVERRIDE — override 허용 역할은 ADMIN뿐(무역도 불가)"""
    _, _, result = _market("RED")
    assert (result.level, result.resolution) == (L.BLOCK, R.OVERRIDE)
    assert result.override_roles == (RoleCode.ADMIN,)
    assert result.basis["unmet_count"] >= 1


@covers(G.MARKET_READINESS, L.UNKNOWN, R.OVERRIDE)
def test_market_readiness_gray_is_unknown_never_a_pass() -> None:
    """GRAY(필수 요건 0건)는 UNKNOWN/OVERRIDE(ADMIN) — 통과로 읽지 않는다(요건이 없다는 사실이 '준비됨'이 아니다). 확정 가능 판정도 아니다"""
    _, _, result = _market("GRAY")
    assert (result.level, result.resolution, result.reason_code) == (
        L.UNKNOWN,
        R.OVERRIDE,
        "NO_REQUIREMENTS",
    )
    assert gates_service.clearance([result], {}, False).cleared is False


def test_a_vanished_market_or_sku_is_unknown_for_the_admin_to_decide() -> None:
    """시장이 등록돼 있지 않거나 SKU를 찾을 수 없으면 UNKNOWN/OVERRIDE(ADMIN) — 500도 통과도 아니다"""
    so = passing_so()
    subject = _subject(so["id"])
    gone_market = _eval_subject(
        dataclasses.replace(subject, dest_market_code="ZZ"), G.MARKET_READINESS
    )
    assert [(o.level, o.resolution, o.reason_code) for o in gone_market] == [
        (L.UNKNOWN, R.OVERRIDE, "MARKET_NOT_FOUND")
    ]
    ghost = dataclasses.replace(subject.lines[0], sku_id=9_999_999)
    gone_sku = _eval_subject(dataclasses.replace(subject, lines=(ghost,)), G.MARKET_READINESS)
    assert [(o.level, o.reason_code) for o in gone_sku] == [(L.UNKNOWN, "SKU_NOT_FOUND")]


def test_the_market_gate_uses_the_matrix_cell_and_states_it_is_not_a_legal_judgement() -> None:
    """게이트 판정의 색이 매트릭스(`get_matrix`)의 같은 SKU·시장 셀과 같다(규칙 1곳) — 모든 메시지에 '법적 판정 아님' 고정 문구가 있고 판정·허가 워딩이 없다"""
    banned = ("판매 가능", "판매가능", "적합", "승인", "허가")
    for color in ("GREEN", "YELLOW", "RED", "GRAY"):
        so = passing_so(market_color=color)
        sku = so["sku_ids"][0]
        matrix = readiness_service.get_matrix(offset=0, limit=500)
        row = next(r for r in matrix.rows if r.sku_id == sku)
        cell = next(c for c in row.cells if c.market_code == "US")
        outcomes = evaluate(so["id"], only=[G.MARKET_READINESS])
        for item in outcomes:
            assert "법적 판정 아님" in item.message_ko
            assert not any(word in item.message_ko for word in banned), item.message_ko
        states = {o.basis.get("prep_state") for o in outcomes}
        assert states <= {cell.summary.color, None}


# ══ MOQ ════════════════════════════════════════════════════════════════════════


@covers(G.MOQ, L.PASS, R.NONE)
def test_moq_passes_at_exactly_the_minimum_and_when_unset() -> None:
    """수량 = MOQ는 PASS(미만만 BLOCK)이고 MOQ NULL은 PASS('정책 없음'은 평가 실패가 아니다 — `moq_unset=true` 안내를 남긴다)"""
    exact = passing_so(quantity=10, moq=10)
    assert one(evaluate(exact["id"], only=[G.MOQ]), G.MOQ).level is L.PASS
    unset = passing_so(quantity=1, moq=None)
    result = one(evaluate(unset["id"], only=[G.MOQ]), G.MOQ)
    assert (result.level, result.reason_code) == (L.PASS, "MOQ_OK")
    assert result.basis["moq_unset"] is True and result.basis["moq_unset_count"] == 1


@covers(G.MOQ, L.BLOCK, R.OVERRIDE)
def test_moq_one_below_the_minimum_blocks_with_a_trade_or_admin_override() -> None:
    """수량 = MOQ−1은 BLOCK/OVERRIDE(무역·관리자) — 근거에 수량·MOQ·단위(EA)가 실린다"""
    so = passing_so(quantity=9, moq=10)
    (line,) = line_ids(so["id"])
    result = one(evaluate(so["id"], only=[G.MOQ]), G.MOQ, line)
    assert (result.level, result.resolution, result.reason_code) == (
        L.BLOCK,
        R.OVERRIDE,
        "BELOW_MOQ",
    )
    assert result.override_roles == (RoleCode.TRADE, RoleCode.ADMIN)
    assert (result.basis["quantity"], result.basis["moq"], result.basis["unit"]) == (9, 10, "EA")


def test_free_lines_are_not_subject_to_the_moq() -> None:
    """무상 라인은 MOQ 대상이 아니다 — 유일한 라인이 무상이면 확인할 유상 라인이 없어 PASS(NO_PAID_LINES)"""
    so = passing_so(quantity=1, moq=10)
    make_free(so["id"])
    result = one(evaluate(so["id"], only=[G.MOQ]), G.MOQ)
    assert (result.level, result.reason_code) == (L.PASS, "NO_PAID_LINES")


@covers(G.MOQ, L.UNKNOWN, R.NONE)
def test_an_unreadable_sku_is_unknown_for_the_moq() -> None:
    """SKU를 읽을 수 없으면 UNKNOWN/NONE(SKU_NOT_FOUND) — MOQ를 없는 것으로 보고 통과시키지 않는다(품번 게이트가 먼저 막는다)"""
    so = passing_so(moq=5)
    subject = _subject(so["id"])
    ghost = dataclasses.replace(subject.lines[0], sku_id=9_999_999)
    result = _eval_subject(dataclasses.replace(subject, lines=(ghost,)), G.MOQ)
    assert [(o.level, o.resolution, o.reason_code) for o in result] == [
        (L.UNKNOWN, R.NONE, "SKU_NOT_FOUND")
    ]


# ══ PI_DEPOSIT — 활성/비활성 양방향 ═════════════════════════════════════════════


def _receive(pi_id: int, amount: int) -> int:
    """입금 원장에 입금 1행(DB 직행)."""
    user = create_user(f"{unique('rcv')}@example.com", roles=(RoleCode.TRADE,))
    with owner_engine.begin() as connection:
        partner = connection.execute(
            text("SELECT buyer_partner_id FROM proforma_invoices WHERE id = :i"), {"i": pi_id}
        ).scalar_one()
        return int(
            connection.execute(
                text(
                    "INSERT INTO payments (partner_id, pi_id, kind, received_amount, received_currency,"
                    " received_on, reference, recorded_by_id) VALUES (:p, :pi, 'RECEIPT', :a, 'USD',"
                    " '2026-09-20', :r, :u) RETURNING id"
                ),
                {"p": partner, "pi": pi_id, "a": amount, "r": unique("REF"), "u": user},
            ).scalar_one()
        )


def _reverse(pi_id: int, payment_id: int, amount: int) -> None:
    user = create_user(f"{unique('rev')}@example.com", roles=(RoleCode.TRADE,))
    with owner_engine.begin() as connection:
        partner = connection.execute(
            text("SELECT buyer_partner_id FROM proforma_invoices WHERE id = :i"), {"i": pi_id}
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO payments (partner_id, pi_id, kind, received_amount, received_currency,"
                " received_on, reference, reverses_payment_id, reason, recorded_by_id)"
                " VALUES (:p, :pi, 'REVERSAL', :a, 'USD', '2026-09-21', :r, :o, '정정', :u)"
            ),
            {
                "p": partner,
                "pi": pi_id,
                "a": -amount,
                "r": unique("REF"),
                "o": payment_id,
                "u": user,
            },
        )


def _pi_outcome(so_id: int) -> Any:
    return one(evaluate(so_id, only=[G.PI_DEPOSIT]), G.PI_DEPOSIT)


def _pi_so(mode: str | None, net: int = 0) -> tuple[int, int]:
    """선수금 T/T PI(청구액 30,000)와 그 PI를 참조하는 RECEIVED SO. 모드·순입금을 심는다 → (so_id, pi_id)."""
    pi = advance_pi()
    so = raw_so("RECEIVED", pi_id=pi)
    set_policy("pi_advance_gate_mode", mode)
    if net:
        _receive(pi, net)
    return so, pi


@covers(G.PI_DEPOSIT, L.BLOCK, R.OVERRIDE)
@pytest.mark.golden
def test_block_mode_blocks_a_short_deposit_and_passes_exactly_the_due_amount() -> None:
    """GC-A10 — 모드 BLOCK: 순입금 = 청구액−1은 BLOCK/OVERRIDE(관리자·SHORT), = 청구액(등호)은 PASS — 상태값이 아니라 금액 대조"""
    short, _ = _pi_so("BLOCK", net=29_999)
    result = _pi_outcome(short)
    assert (result.level, result.resolution, result.reason_code) == (L.BLOCK, R.OVERRIDE, "SHORT")
    assert result.override_roles == (RoleCode.ADMIN,)
    assert (result.basis["required_amount"], result.basis["received_amount"]) == (30_000, 29_999)
    enough, _ = _pi_so("BLOCK", net=30_000)
    assert (_pi_outcome(enough).level, _pi_outcome(enough).reason_code) == (L.PASS, "SUFFICIENT")
    over, _ = _pi_so("BLOCK", net=30_001)
    assert _pi_outcome(over).level is L.PASS


@covers(G.PI_DEPOSIT, L.WARN, R.NONE)
def test_warn_mode_turns_a_shortfall_into_a_warning_that_proceeds() -> None:
    """모드 WARN: 같은 미입금이 WARN(진행·기록)이다 — 충족이면 PASS"""
    short, _ = _pi_so("WARN", net=0)
    result = _pi_outcome(short)
    assert (result.level, result.resolution, result.reason_code) == (L.WARN, R.NONE, "SHORT")
    assert gates_service.clearance([result], {}, False).cleared is True  # WARN은 통과(증적에 기록)
    met, _ = _pi_so("WARN", net=30_000)
    assert _pi_outcome(met).level is L.PASS


@covers(G.PI_DEPOSIT, L.PASS, R.NONE)
def test_off_mode_skips_the_check_and_records_that_it_was_skipped() -> None:
    """모드 OFF: 미입금이어도 PASS이되 이유 코드가 SKIPPED_OFF — 확인을 생략한 사실이 조용히 사라지지 않는다"""
    so, _ = _pi_so("OFF", net=0)
    result = _pi_outcome(so)
    assert (result.level, result.reason_code) == (L.PASS, "SKIPPED_OFF")
    assert result.basis["mode"] == "OFF"


def test_an_unset_mode_behaves_as_block_and_reports_its_source() -> None:
    """모드 미설정은 BLOCK으로 동작(fail-closed)하고 `policy_source=UNSET_DEFAULT`가 근거에 실린다 — 저장하면 SET"""
    so, _ = _pi_so(None, net=0)
    result = _pi_outcome(so)
    assert (result.level, result.basis["mode"], result.basis["policy_source"]) == (
        L.BLOCK,
        "BLOCK",
        "UNSET_DEFAULT",
    )
    stored, _ = _pi_so("BLOCK", net=0)
    assert _pi_outcome(stored).basis["policy_source"] == "SET"


@pytest.mark.parametrize("payment_type", ["LC", "TT_DEFERRED"])
@pytest.mark.golden
def test_the_gate_is_inactive_for_non_advance_payment_even_in_block_mode(payment_type: str) -> None:
    """GC-A10 — [활성/비활성 양방향 ①] 같은 PI·같은 모드(BLOCK)·같은 미입금에서 결제유형이 L/C·후불이면 PASS(INACTIVE) — 선수금 T/T일 때만 게이트가 켜진다"""
    pi = advance_pi()
    set_payment_terms(pi, payment_type)
    so = raw_so("RECEIVED", pi_id=pi)
    set_policy("pi_advance_gate_mode", "BLOCK")
    result = _pi_outcome(so)
    assert (result.level, result.reason_code) == (L.PASS, "INACTIVE")


def test_the_same_setup_is_active_for_advance_payment() -> None:
    """[활성/비활성 양방향 ②] 위 비활성 테스트와 같은 구성에서 결제유형만 선수금 T/T면 BLOCK — 비활성이 '게이트 고장'이 아님을 대조로 확인(공회전 방지)"""
    pi = advance_pi()
    so = raw_so("RECEIVED", pi_id=pi)
    set_policy("pi_advance_gate_mode", "BLOCK")
    assert _pi_outcome(so).level is L.BLOCK


@covers(G.PI_DEPOSIT, L.UNKNOWN, R.OVERRIDE)
def test_an_advance_order_without_a_pi_cannot_be_judged_and_follows_the_mode() -> None:
    """PI 없는 선수금 SO: BLOCK 모드=UNKNOWN/OVERRIDE(PI_MISSING — 입금 원장은 PI 단위라 판정 불가, 통과 아님), WARN=WARN, OFF=SKIPPED_OFF. 비선수금 SO는 PASS(INACTIVE)"""
    so = raw_so("RECEIVED")  # 기본 결제조건 = 선수금 T/T 30%, PI 참조 없음
    set_policy("pi_advance_gate_mode", "BLOCK")
    blocked = _pi_outcome(so)
    assert (blocked.level, blocked.resolution, blocked.reason_code) == (
        L.UNKNOWN,
        R.OVERRIDE,
        "PI_MISSING",
    )
    set_policy("pi_advance_gate_mode", "WARN")
    assert (_pi_outcome(so).level, _pi_outcome(so).reason_code) == (L.WARN, "PI_MISSING")
    set_policy("pi_advance_gate_mode", "OFF")
    assert _pi_outcome(so).reason_code == "SKIPPED_OFF"
    set_policy("pi_advance_gate_mode", "BLOCK")
    set_terms(so, "LC")
    assert _pi_outcome(so).reason_code == "INACTIVE"


@covers(G.PI_DEPOSIT, L.UNKNOWN, R.NONE)
def test_a_missing_payment_type_is_unknown_not_inactive() -> None:
    """결제유형이 비어 있으면 비활성이 아니라 UNKNOWN/NONE(PAYMENT_TYPE_UNSET) — 결제조건이 정해져야 판정한다"""
    so = raw_so("RECEIVED")
    set_terms(so, None)
    result = _pi_outcome(so)
    assert (result.level, result.resolution, result.reason_code) == (
        L.UNKNOWN,
        R.NONE,
        "PAYMENT_TYPE_UNSET",
    )


def test_editing_the_order_terms_cannot_switch_the_gate_off_for_a_pi_order() -> None:
    """우회 방지: PI를 참조하는 SO의 결제유형을 선수금→L/C로 바꿔도 PI 조건 기준으로 판정한다(terms_basis=PI, terms_diverge=true) — 여전히 BLOCK"""
    so, _ = _pi_so("BLOCK", net=0)
    set_terms(so, "LC")
    result = _pi_outcome(so)
    assert result.level is L.BLOCK
    assert (result.basis["terms_basis"], result.basis["terms_diverge"]) == ("PI", True)


def test_a_referenced_pi_that_cannot_take_payments_is_not_usable() -> None:
    """참조 PI가 취소·만료 상태면 PI_NOT_USABLE — BLOCK 모드는 UNKNOWN/OVERRIDE, WARN 모드는 WARN(통과로 취급하지 않는다)"""
    so, pi = _pi_so("BLOCK", net=30_000)
    _sql("UPDATE proforma_invoices SET status = 'CANCELLED' WHERE id = :i", i=pi)
    blocked = _pi_outcome(so)
    assert (blocked.level, blocked.reason_code) == (L.UNKNOWN, "PI_NOT_USABLE")
    set_policy("pi_advance_gate_mode", "WARN")
    assert _pi_outcome(so).level is L.WARN


def test_a_referenced_pi_that_cannot_be_read_is_not_usable_whatever_the_order_terms_say() -> None:
    """PI를 참조하는데 PI 행을 읽을 수 없으면(삭제) SO 결제유형을 L/C로 바꿔도 PASS가 아니다 — BLOCK 모드는 UNKNOWN/OVERRIDE(PI_NOT_USABLE), WARN 모드는 WARN. 결제유형 편집으로 게이트를 끄는 우회 차단"""
    so, pi = _pi_so("BLOCK", net=30_000)
    set_terms(so, "LC")
    _sql("UPDATE proforma_invoices SET deleted_at = now() WHERE id = :i", i=pi)
    blocked = _pi_outcome(so)
    assert (blocked.level, blocked.resolution, blocked.reason_code) == (
        L.UNKNOWN,
        R.OVERRIDE,
        "PI_NOT_USABLE",
    )
    set_policy("pi_advance_gate_mode", "WARN")
    assert (_pi_outcome(so).level, _pi_outcome(so).reason_code) == (L.WARN, "PI_NOT_USABLE")


def test_the_amount_not_the_pi_status_decides() -> None:
    """PI 상태값은 판정에 쓰지 않는다 — PARTIALLY_PAID PI가 30% 입금으로 통과하고, 같은 상태에서 입금이 모자라면 차단"""
    so, pi = _pi_so("BLOCK", net=30_000)
    _sql("UPDATE proforma_invoices SET status = 'PARTIALLY_PAID' WHERE id = :i", i=pi)
    assert _pi_outcome(so).level is L.PASS
    short, spi = _pi_so("BLOCK", net=100)
    _sql("UPDATE proforma_invoices SET status = 'PARTIALLY_PAID' WHERE id = :i", i=spi)
    assert _pi_outcome(short).level is L.BLOCK


def test_net_received_subtracts_reversals() -> None:
    """순입금 = 입금 − 역기록 — 입금 후 역기록하면 다시 부족(SHORT)으로 돌아간다"""
    so, pi = _pi_so("BLOCK")
    payment = _receive(pi, 30_000)
    assert _pi_outcome(so).level is L.PASS
    _reverse(pi, payment, 30_000)
    result = _pi_outcome(so)
    assert (result.level, result.basis["received_amount"]) == (L.BLOCK, 0)


def test_the_required_amount_rounds_half_up_like_the_invoice() -> None:
    """요구액은 청구서와 같은 HALF_UP(`split_advance`): 총액 3·50% → 2(1.5 반올림). 순입금 1은 SHORT, 2는 통과. 반올림 0이면 ZERO_REQUIRED로 통과"""
    pi = advance_pi(total_amount=3)
    _sql("UPDATE proforma_invoices SET advance_pct_bp = 5000 WHERE id = :i", i=pi)
    so = raw_so("RECEIVED", pi_id=pi)
    set_policy("pi_advance_gate_mode", "BLOCK")
    _receive(pi, 1)
    assert (_pi_outcome(so).level, _pi_outcome(so).basis["required_amount"]) == (L.BLOCK, 2)
    _receive(pi, 1)
    assert _pi_outcome(so).level is L.PASS
    tiny = advance_pi(total_amount=100)
    _sql("UPDATE proforma_invoices SET advance_pct_bp = 1 WHERE id = :i", i=tiny)
    tiny_so = raw_so("RECEIVED", pi_id=tiny)
    zero = _pi_outcome(tiny_so)
    assert (zero.level, zero.reason_code, zero.basis["required_amount"]) == (
        L.PASS,
        "ZERO_REQUIRED",
        0,
    )


# ══ 불변 ════════════════════════════════════════════════════════════════════


def test_every_outcome_hash_is_stable_across_two_evaluations() -> None:
    """같은 입력이면 7종 모든 결과의 basis_hash가 같고 입력이 바뀌면 달라진다 — override 결속의 전제(시각·난수가 해시에 섞이지 않는다)"""
    so = passing_so(map_code=True)
    first = {(o.gate_code, o.line_id): o.basis_hash for o in evaluate(so["id"])}
    second = {(o.gate_code, o.line_id): o.basis_hash for o in evaluate(so["id"])}
    assert first == second
    set_line(so["id"], 1, unit_price=1051)
    changed = {(o.gate_code, o.line_id): o.basis_hash for o in evaluate(so["id"])}
    assert changed != first
    assert scalar("SELECT count(*) FROM gate_evaluations") == 0  # 평가는 아무것도 저장하지 않는다


def test_a_partial_evaluation_is_never_a_clearance() -> None:
    """`only`로 일부 게이트만 평가한 묶음은 모든 결과가 PASS여도 cleared=False·partial=True — 부분 평가로 확정 가능을 판정할 수 없다(확정 통로가 only를 실수로 넘겨도 우회 불가). 전건 평가는 cleared=True"""
    from app.modules.trade_chain import gate_flow

    so = passing_so()
    with unit_of_work() as uow:
        full = gate_flow.evaluate_sales_order(uow.session, so["id"])
        partial = gate_flow.evaluate_sales_order(uow.session, so["id"], only=[G.MOQ])
    assert (full.clearance.cleared, full.partial) == (True, False)
    assert (partial.clearance.cleared, partial.partial) == (False, True)
    assert {o.level for o in partial.outcomes} == {L.PASS}
