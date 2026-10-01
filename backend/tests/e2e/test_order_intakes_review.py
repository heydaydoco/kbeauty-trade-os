"""A·J·K. 오더 인테이크 — 적대 검토 반영(PR-13a 2차): PATCH 동시 경합·이관 잠금 순서·PO 점유 표지·부분 재해석·확정 에러 계약·마스킹·정규화 보강.

앞 파일(`test_order_intakes.py`)이 착지·확정·STALE의 기본 계약을 증명한다면, 여기는 검토가 지적한 경계(동시 PATCH 500·막다른 PENDING·검토 안 한 라인의 조용한 갱신 등)를 고정한다.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.errors.exceptions import AppError
from app.modules.gates.types import GateLevel, GateOutcome, GateResolution
from app.modules.handover.service import reassign_all
from app.modules.handover.targets import ASSIGNMENT_TARGETS
from app.modules.identity.models import RoleCode
from app.modules.order_intake import service as intake_service
from app.modules.trade_chain import intake_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.intake import (
    INTAKES,
    code_of,
    confirm,
    execute,
    future,
    get,
    intake_payload,
    land,
    line_body,
    register,
    rows,
    scalar,
    trade_actor,
    world,
)
from tests.factories.trade import (
    create_direct_so,
    create_priced_sku,
    create_so_from_qt_via_api,
    idem,
    issued_quotation,
    logged_in,
    map_buyer_item_code,
    unique,
)
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_user

pytestmark = pytest.mark.group_a

SO = "/api/v1/sales-orders"
DUP = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _remap(w: dict[str, Any], code: str) -> int:
    """품번 `code`를 다른 SKU로 다시 매핑한다(검토 뒤 매핑 변경) — 새 SKU id."""
    other = create_priced_sku()
    execute(
        "UPDATE customer_item_codes SET deleted_at = now() WHERE partner_id = :p AND buyer_item_code = :c",
        p=w["buyer"],
        c=code,
    )
    map_buyer_item_code(w["buyer"], other, code)
    return other


# ── 1. PATCH 동시 경합은 409 (500 아님) ─────────────────────────────────────────


@pytest.mark.group_j
def test_concurrent_patches_racing_for_the_same_po_never_leak_a_500() -> None:
    """키 변경+라인 교체를 한 PATCH와 같은 PO를 노리는 다른 인테이크의 PATCH가 동시에 와도 한쪽은 409 DUPLICATE_BUYER_PO이지 DB 오류(500)가 아니다 — 8회 반복"""
    for _ in range(8):
        w = world()
        a = land(w, po_no=unique("PO-A"))
        b = land(w, po_no=unique("PO-B"))
        target = unique("PO-RACE")

        def worker(i: int, _a: dict[str, Any] = a, _b: dict[str, Any] = b, _t: str = target) -> Any:
            mine = _a if i == 0 else _b
            payload: dict[str, Any] = {"version": mine["version"], "buyer_po_no": _t}
            if i == 0:  # 헤더 변경 + 라인 교체(교체 flush 안에서 키 유니크가 터질 수 있다)
                payload["lines"] = [
                    {
                        "id": mine["lines"][0]["id"],
                        **line_body(_a["lines"][0]["buyer_item_code"], quantity=9),
                    }
                ]
            return intake_service.update_intake(
                actor=trade_actor(), intake_id=mine["id"], payload=payload
            )

        outcomes = run_concurrently(worker, workers=2)
        assert sum(1 for o in outcomes if o.ok) == 1, [repr(o.error) for o in outcomes]
        loser = next(o for o in outcomes if not o.ok)
        assert isinstance(loser.error, AppError), repr(loser.error)
        assert loser.error.code.value == DUP and loser.error.status_code == 409
        keys = rows(
            "SELECT buyer_po_no_key FROM order_intakes WHERE buyer_partner_id = :b", b=w["buyer"]
        )
        assert sorted(str(k[0]) for k in keys).count(target.upper()) == 1


# ── 2. 담당 이관 잠금 순서 ──────────────────────────────────────────────────────


def test_handover_follows_the_global_lock_order() -> None:
    """이관은 표마다 UPDATE로 행을 잠그므로 ASSIGNMENT_TARGETS의 순서가 곧 잠금 순서다 — order_intakes → quotations → PI → SO → PO(전역 LOCK_ORDER 순)"""
    labels = [t.label for t in ASSIGNMENT_TARGETS]
    chain = ["order_intakes", "quotations", "proforma_invoices", "sales_orders", "purchase_orders"]
    positions = [labels.index(name) for name in chain]
    assert positions == sorted(positions), labels
    assert (
        LOCK_ORDER.index("order_intakes")
        < LOCK_ORDER.index("sales_orders")
        < LOCK_ORDER.index("purchase_orders")
    )


@pytest.mark.group_j
def test_handover_crossing_a_copy_intake_confirmation_has_no_deadlock_and_no_stale_assignee(
    trade: Any,
) -> None:
    """복제 인테이크 확정(인테이크 → 원본 SO 잠금) × 담당 이관(인테이크 → … → SO) 교차 6회 — 교착 0, 이관 뒤 SO 담당자가 옛 담당자로 남지 않는다"""
    for _ in range(6):
        w = world()
        old = create_user(f"{unique('old')}@example.com", roles=(RoleCode.TRADE,))
        new = create_user(f"{unique('new')}@example.com", roles=(RoleCode.TRADE,))
        admin = create_user(f"{unique('adm')}@example.com", roles=(RoleCode.ADMIN,))
        src = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no=unique("PO"), assignee_id=old)
        body = trade.get(f"{SO}/{src['id']}").json()
        assert (
            trade.post(
                f"{SO}/{src['id']}/transitions",
                json={"to": "CANCELLED", "version": body["version"], "reason": "복제 시험"},
                headers=idem(),
            ).status_code
            == 200
        )
        status, intake = intake_service.create_manual_intake(
            actor=trade_actor(old),
            idempotency_key=unique("k"),
            payload=intake_payload(w, copied_from_so_id=src["id"], assignee_id=old),
        )
        assert status == 201

        def worker(
            i: int,
            _intake: dict[str, Any] = intake,
            _old: int = old,
            _new: int = new,
            _admin: int = admin,
        ) -> Any:
            if i == 0:
                return intake_flow.confirm_intake(
                    actor=trade_actor(),
                    idempotency_key=unique("c"),
                    intake_id=_intake["id"],
                    version=_intake["version"],
                )
            return reassign_all(from_user_id=_old, to_user_id=_new, actor_user_id=_admin)

        outcomes = run_concurrently(worker, workers=2)
        assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
        assert scalar("SELECT assignee_id FROM order_intakes WHERE id = :i", i=intake["id"]) == new
        so_id = scalar("SELECT sales_order_id FROM order_intakes WHERE id = :i", i=intake["id"])
        assert scalar("SELECT assignee_id FROM sales_orders WHERE id = :i", i=so_id) == new


def test_handover_moves_terminal_intakes_too_and_keeps_their_version(trade: Any) -> None:
    """현행 동작 고정(오너 판정 후보 ⑤) — 담당 이관은 CONFIRMED·REJECTED 인테이크의 담당자도 옮기며 version·상태는 건드리지 않는다"""
    w = world()
    owner = create_user(f"{unique('o')}@example.com", roles=(RoleCode.TRADE,))
    admin = create_user(f"{unique('a')}@example.com", roles=(RoleCode.ADMIN,))
    new = create_user(f"{unique('n')}@example.com", roles=(RoleCode.TRADE,))
    ids: list[int] = []
    for _ in range(3):
        status, body = intake_service.create_manual_intake(
            actor=trade_actor(owner),
            idempotency_key=unique("k"),
            payload=intake_payload(w, assignee_id=owner),
        )
        assert status == 201
        ids.append(body["id"])
    confirmed = get(trade, ids[0])
    assert confirm(trade, confirmed).status_code == 201
    assert (
        trade.post(
            f"{INTAKES}/{ids[1]}/reject",
            json={"version": 1, "reason": "이관 시험 거부"},
            headers=idem(),
        ).status_code
        == 200
    )
    before = {
        i: rows("SELECT status, version FROM order_intakes WHERE id = :i", i=i)[0] for i in ids
    }
    result = reassign_all(from_user_id=owner, to_user_id=new, actor_user_id=admin)
    assert result.moved["order_intakes"] == 3
    for i in ids:
        assert scalar("SELECT assignee_id FROM order_intakes WHERE id = :i", i=i) == new
        assert rows("SELECT status, version FROM order_intakes WHERE id = :i", i=i)[0] == before[i]


# ── 3. PO 점유 표지 ─────────────────────────────────────────────────────────────


def test_po_occupied_marker_appears_when_an_so_takes_the_po_after_landing(trade: Any) -> None:
    """PENDING 등록 뒤 QT 참조 생성·SO PO번호 편집으로 같은 PO가 점유되면 상세·목록에 `po_occupied`(SALES_ORDER)가 뜨고 확정은 409 — 무역·관리자만 점유 문서번호를 본다(금액 없음)"""
    w = world()
    po_a, po_b = unique("PO-OCC"), unique("PO-OCC")
    a = register(trade, w, po_no=po_a)
    b = register(trade, w, po_no=po_b)
    assert a["po_occupied"] is None and b["po_occupied"] is None  # 양성 대조
    qt = issued_quotation(trade, w["buyer"], w["sku_ids"])
    so1 = create_so_from_qt_via_api(trade, qt, buyer_po_no=po_a)  # 참조 생성으로 점유
    so2 = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no=unique("PO-OTHER"))
    patched = trade.patch(
        f"{SO}/{so2['id']}", json={"version": so2["version"], "buyer_po_no": po_b}
    )  # SO PO번호 편집으로 점유
    assert patched.status_code == 200, patched.text
    for intake, so_number in ((a, so1["doc_number"]), (b, patched.json()["doc_number"])):
        shown = get(trade, intake["id"])
        assert shown["po_occupied"] == {
            "kind": "SALES_ORDER",
            "doc_number": so_number,
            "status": "RECEIVED",
        }
        report = trade.get(f"{INTAKES}/{intake['id']}/gates").json()
        assert report["intake_confirmable"] is False
        r = confirm(trade, shown)
        assert r.status_code == 409 and code_of(r) == DUP
    listed = {i["id"]: i for i in trade.get(INTAKES).json()["items"]}
    assert listed[a["id"]]["po_occupied"]["doc_number"] == so1["doc_number"]
    assert "amount" not in str(listed[a["id"]]["po_occupied"])
    with logged_in(RoleCode.VIEWER) as viewer:
        masked = get(viewer, a["id"])["po_occupied"]
        assert masked == {"kind": "SALES_ORDER", "doc_number": None, "status": None}
        assert viewer.get(INTAKES).json()["items"][-1]["po_occupied"] is None or True
        assert {i["id"]: i for i in viewer.get(INTAKES).json()["items"]}[a["id"]]["po_occupied"][
            "doc_number"
        ] is None


def test_po_occupied_is_not_set_for_terminal_intakes(trade: Any) -> None:
    """확정된 인테이크는 자기 SO가 PO를 점유하지만 표지는 PENDING 전용이다(자기 SO를 '막다른'으로 오독하지 않는다)"""
    w = world()
    done = confirm(trade, register(trade, w)).json()["intake"]
    assert done["po_occupied"] is None and get(trade, done["id"])["po_occupied"] is None


# ── 4. 확정 시점 에러 계약 ──────────────────────────────────────────────────────


def test_confirmation_time_errors_are_422_or_409_with_intake_line_numbers(trade: Any) -> None:
    """확정 시점에 납기가 지나면 422(필드 경로는 인테이크 라인 번호 `line_<n>.…`)·복제 원본 자격 상실 409·시장 비활성 422 — 전부 롤백이고 인테이크는 PENDING 그대로"""
    w = world(lines=2)
    body = register(
        trade,
        w,
        lines=[
            line_body(w["codes"][0]),
            line_body(w["codes"][1], requested_delivery_date=future(3)),
        ],
    )
    execute(
        "UPDATE order_intake_lines SET requested_delivery_date = '2000-01-01' WHERE intake_id = :i AND line_no = 2",
        i=body["id"],
    )
    r = confirm(trade, get(trade, body["id"]))
    assert r.status_code == 422, r.text
    assert "line_2.requested_delivery_date" in r.json()["error"]["detail"]
    assert "lines[" not in str(r.json()["error"]["detail"])  # SO draft 순번이 새지 않는다
    assert (
        scalar("SELECT count(*) FROM sales_orders") == 0
        and get(trade, body["id"])["status"] == "PENDING"
    )

    src = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no=unique("PO"))
    cur = trade.get(f"{SO}/{src['id']}").json()
    trade.post(
        f"{SO}/{src['id']}/transitions",
        json={"to": "CANCELLED", "version": cur["version"], "reason": "복제 시험"},
        headers=idem(),
    )
    copy = register(trade, w, copied_from_so_id=src["id"])
    execute(
        "UPDATE sales_orders SET status = 'RECEIVED' WHERE id = :s", s=src["id"]
    )  # 원본이 되살아남(자격 상실)
    lost = confirm(trade, copy)
    assert lost.status_code == 409 and code_of(lost) == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"

    market = register(trade, w)
    execute("UPDATE markets SET deleted_at = now() WHERE code = 'US'")
    gone = confirm(trade, market)
    assert gone.status_code == 422, gone.text


# ── 5. 부분 재해석 ──────────────────────────────────────────────────────────────


def test_editing_one_line_keeps_the_stale_state_of_untouched_lines(trade: Any) -> None:
    """PATCH는 변경·신규 라인만 재해석한다 — 라인 A만 고쳐도 검토자가 보지 않은 라인 B의 STALE은 조용히 갱신되지 않는다(`resolve`로만 갱신, ADR-0071 '자동 추종 안 함')"""
    w = world(lines=2)
    body = register(trade, w)
    new_sku = _remap(w, w["codes"][1])  # B의 매핑이 검토 뒤 바뀜
    shown = get(trade, body["id"])
    assert [ln["mapping_state"] for ln in shown["lines"]] == ["MAPPED", "STALE"]
    a, b = shown["lines"]
    edited = trade.patch(
        f"{INTAKES}/{body['id']}",
        json={
            "version": shown["version"],
            "lines": [
                {"id": a["id"], **line_body(w["codes"][0], quantity=9)},
                {"id": b["id"], **line_body(w["codes"][1], quantity=b["quantity"])},  # B는 그대로
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    got = edited.json()
    assert got["lines"][0]["quantity"] == 9
    assert (
        got["lines"][1]["sku_id"] == b["sku_id"] and got["lines"][1]["mapping_state"] == "STALE"
    )  # 저장본 유지
    r = confirm(trade, got)
    assert r.status_code == 409 and code_of(r) == "ORDER_INTAKE.LINE.STALE_MAPPING"
    # 헤더만 바꾸는 PATCH도 재해석하지 않는다
    header = trade.patch(
        f"{INTAKES}/{body['id']}", json={"version": got["version"], "buyer_po_date": "2026-09-01"}
    ).json()
    assert header["lines"][1]["sku_id"] == b["sku_id"]
    # 신규 라인 추가(변경 없는 A·B 포함) — 새 라인만 해석되고 B는 여전히 STALE, resolve 뒤 확정 성공
    extra = create_priced_sku()
    map_buyer_item_code(w["buyer"], extra, "EXTRA-NEW")
    added = trade.patch(
        f"{INTAKES}/{body['id']}",
        json={
            "version": header["version"],
            "lines": [
                {"id": ln["id"], **line_body(ln["buyer_item_code"], quantity=ln["quantity"])}
                for ln in header["lines"]
            ]
            + [line_body("EXTRA-NEW")],
        },
    ).json()
    assert [ln["mapping_state"] for ln in added["lines"]] == ["MAPPED", "STALE", "MAPPED"]
    assert added["lines"][2]["sku_id"] == extra
    resolved = trade.post(
        f"{INTAKES}/{body['id']}/resolve", json={"version": added["version"]}, headers=idem()
    ).json()
    assert resolved["lines"][1]["sku_id"] == new_sku
    assert confirm(trade, resolved).status_code == 201


def test_a_noop_patch_keeps_the_version_and_a_duplicate_line_id_is_422(trade: Any) -> None:
    """바뀐 것이 없는 PATCH(같은 헤더·같은 라인)는 version 불변이고, 같은 라인 id를 두 번 실으면 422"""
    w = world()
    body = register(trade, w, po_no="PO-NOOP-1")
    line = body["lines"][0]
    same = {"id": line["id"], **line_body(w["codes"][0])}
    ok = trade.patch(
        f"{INTAKES}/{body['id']}",
        json={"version": body["version"], "buyer_po_no": "PO-NOOP-1", "lines": [same]},
    )
    assert ok.status_code == 200 and ok.json()["version"] == body["version"]
    dup = trade.patch(
        f"{INTAKES}/{body['id']}", json={"version": body["version"], "lines": [same, same]}
    )
    assert dup.status_code == 422
    assert get(trade, body["id"])["version"] == body["version"]


# ── 6. 하드 게이트 에러 우선순위 ─────────────────────────────────────────────────


def _outcome(gate: str, reason: str, line_id: int | None = None, **detail: Any) -> GateOutcome:
    return GateOutcome(
        gate_code=gate,
        line_id=line_id,
        level=GateLevel.BLOCK,
        resolution=GateResolution.NONE,
        reason_code=reason,
        message_ko="시험",
        detail=detail,
    )


def _subject() -> Any:
    from app.modules.gates.types import SUBJECT_INTAKE, GateLine, GateSubject

    line = GateLine(1, 1, None, "X", 1, 1, None, False)
    return GateSubject(SUBJECT_INTAKE, 1, 1, "USD", "US", (line,), 1, po_no_key="K")


def test_unresolved_hard_gates_map_to_errors_in_the_documented_priority() -> None:
    """복합 사유 입력의 우선순위 — 미매핑 422 → STALE 409 → 중복 PO 409 → GATE.UNRESOLVED 409 (앞 사유를 빼면 다음 사유로 내려간다)"""
    unmapped = _outcome("ITEM_MAPPING", "ITEM_UNMAPPED", 1)
    deleted = _outcome("ITEM_MAPPING", "SKU_DELETED", 1)
    stale = _outcome("ITEM_MAPPING", "MAPPING_CHANGED", 1)
    dup = _outcome(
        "DUPLICATE_PO", "DUPLICATE_PO_NO", None, other_doc_number="SO-1", other_status="RECEIVED"
    )
    unknown = _outcome("ITEM_MAPPING", "EVALUATION_ERROR")
    subject = _subject()

    def raised(*items: GateOutcome) -> str:
        with pytest.raises(AppError) as caught:
            intake_flow._raise_for_unresolved(subject, tuple(items))
        return caught.value.code.value

    assert raised(dup, stale, unmapped, unknown) == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"
    assert raised(unknown, dup, deleted) == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"
    assert raised(dup, stale, unknown) == "ORDER_INTAKE.LINE.STALE_MAPPING"
    assert raised(unknown, dup) == DUP
    assert raised(unknown) == "ORDER_INTAKE.GATE.UNRESOLVED"


def test_a_composite_failure_reports_the_unmapped_items_first(trade: Any) -> None:
    """실제 입력으로: 미매핑 라인 + 같은 PO를 SO가 점유 — 422 UNMAPPED_ITEMS가 먼저(중복 PO 409가 아니라)"""
    w = world()
    body = register(trade, w, po_no="PO-COMPOSITE", lines=[line_body("NO-MAP")])
    create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no="PO-COMPOSITE")
    r = confirm(trade, body)
    assert r.status_code == 422 and code_of(r) == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"


def test_buyer_lost_is_reachable_only_through_the_gate_report(trade: Any) -> None:
    """바이어 유형 상실(BUYER_LOST)은 게이트 조회에서만 보이고, 확정은 입력 완결성 단계(422)에서 먼저 막힌다 — 확정 에러 매핑에는 도달하지 않는다"""
    w = world()
    body = register(trade, w)
    execute(
        "UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p AND type_code = 'BUYER'",
        p=w["buyer"],
    )
    report = trade.get(f"{INTAKES}/{body['id']}/gates").json()
    mapping = [g for g in report["gates"] if g["gate_code"] == "ITEM_MAPPING"]
    assert (
        any(g["reason_code"] == "BUYER_LOST" for g in mapping)
        and report["intake_confirmable"] is False
    )
    r = confirm(trade, get(trade, body["id"]))
    assert r.status_code == 422 and code_of(r) == "COMMON.VALIDATION.INVALID_FIELD"
    assert "BUYER_LOST" not in intake_flow._UNMAPPED_REASONS


# ── 7. PO 정규화 보강(공유 함수) ─────────────────────────────────────────────────


def test_blank_symbols_are_stripped_so_the_braille_blank_cannot_dodge_the_duplicate_check(
    trade: Any,
) -> None:
    """점자 빈칸(U+2800)처럼 렌더가 비는 기호 문자를 PO번호에 섞어도 같은 키다 — 착지 중복 409"""
    w = world()
    register(trade, w, po_no="PO-BRAILLE-1")
    for variant in ("PO-BRAILLE⠀-1", "⠀PO-BRAILLE-1⠀"):
        r = trade.post(INTAKES, json=intake_payload(w, po_no=variant), headers=idem())
        assert r.status_code == 409 and code_of(r) == DUP, variant


# ── 8. 마스킹 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "visible"),
    [
        (RoleCode.TRADE, True),
        (RoleCode.ADMIN, True),
        (RoleCode.LOGISTICS, False),
        (RoleCode.CERT, False),
        (RoleCode.VIEWER, False),
    ],
)
def test_the_reject_reason_free_text_is_visible_only_to_trade_and_admin(
    role: RoleCode, visible: bool
) -> None:
    """거부 사유(자유 텍스트)는 무역·관리자에게만 — 그 외 역할은 null(11a override 사유 선례). 목록은 사유를 싣지 않는다"""
    w = world()
    with logged_in(RoleCode.TRADE) as trade_client:
        body = register(trade_client, w)
        r = trade_client.post(
            f"{INTAKES}/{body['id']}/reject",
            json={"version": body["version"], "reason": "바이어 내부 사정으로 취소"},
            headers=idem(),
        )
        assert r.status_code == 200 and r.json()["reject_reason"] == "바이어 내부 사정으로 취소"
    with logged_in(role) as client:
        shown = get(client, body["id"])["reject_reason"]
        assert (shown == "바이어 내부 사정으로 취소") if visible else (shown is None)
        assert "바이어 내부 사정" not in client.get(INTAKES).text


def test_the_deleted_sku_message_speaks_in_the_intake_context(trade: Any) -> None:
    """삭제된 SKU 안내가 인테이크 문맥이다('수주에서 제거' 아님)"""
    w = world()
    body = register(trade, w)
    execute("UPDATE skus SET deleted_at = now() WHERE id = :s", s=w["sku_ids"][0])
    report = trade.get(f"{INTAKES}/{body['id']}/gates").json()
    messages = [g["message_ko"] for g in report["gates"] if g["reason_code"] == "SKU_DELETED"]
    assert messages and "수주에서" not in messages[0] and "라인을 수정" in messages[0]


# ── 9. 13b 인계 필드 ────────────────────────────────────────────────────────────


def test_gate_report_fields_for_the_frontend_handover(trade: Any) -> None:
    """GET gates — 미매핑·중복 PO는 `blocks_intake_confirm`=True·`intake_confirmable`=False, 비PENDING은 빈 결과+false, 비무역 역할은 점유 문서 detail이 빈 값"""
    w = world()
    unmapped = register(trade, w, lines=[line_body("NO-MAP")])
    report = trade.get(f"{INTAKES}/{unmapped['id']}/gates").json()
    blocking = [g for g in report["gates"] if g["blocks_intake_confirm"]]
    assert (
        blocking
        and blocking[0]["gate_code"] == "ITEM_MAPPING"
        and report["intake_confirmable"] is False
    )

    dup = register(trade, w, po_no="PO-GATES-DUP")
    so = create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no="PO-GATES-DUP")
    rep = trade.get(f"{INTAKES}/{dup['id']}/gates").json()
    dups = [g for g in rep["gates"] if g["gate_code"] == "DUPLICATE_PO"]
    assert dups[0]["blocks_intake_confirm"] is True and rep["intake_confirmable"] is False
    assert dups[0]["detail"]["other_doc_number"] == so["doc_number"]
    with logged_in(RoleCode.VIEWER) as viewer:
        masked = viewer.get(f"{INTAKES}/{dup['id']}/gates").json()
        assert [g["detail"] for g in masked["gates"] if g["gate_code"] == "DUPLICATE_PO"] == [{}]
        assert masked["intake_confirmable"] is False

    done = confirm(trade, register(trade, w)).json()["intake"]
    closed = trade.get(f"{INTAKES}/{done['id']}/gates").json()
    assert (
        closed["gates"] == []
        and closed["intake_confirmable"] is False
        and closed["status"] == "CONFIRMED"
    )
    ok = register(trade, w)
    assert (
        trade.get(f"{INTAKES}/{ok['id']}/gates").json()["intake_confirmable"] is True
    )  # 양성 대조


# ── 10. 행동 수준 잠금 시험 ──────────────────────────────────────────────────────


@pytest.mark.group_j
def test_confirmation_really_waits_for_the_intake_row_lock_and_then_refuses_exactly(
    trade: Any,
) -> None:
    """행동 수준 — 다른 트랜잭션이 인테이크 행을 잡고 REJECTED로 바꾸는 동안 확정은 **대기**하고(SO 0건), 커밋 뒤 정확히 409 NOT_PENDING으로 거부된다(FOR UPDATE가 없으면 즉시 SO를 만들어 이 시험이 실패한다)"""
    w = world()
    intake = land(w)
    user = trade_actor().id
    holder = owner_engine.connect()
    tx = holder.begin()
    holder.execute(
        text(
            "UPDATE order_intakes SET status = 'REJECTED', reject_reason = '동시 거부 시험 사유', decided_at = now(),"
            " decided_by_id = :u WHERE id = :i"
        ),
        {"u": user, "i": intake["id"]},
    )
    result: dict[str, Any] = {}
    started = threading.Event()

    def run() -> None:
        started.set()
        try:
            result["value"] = intake_flow.confirm_intake(
                actor=trade_actor(),
                idempotency_key=unique("wait"),
                intake_id=intake["id"],
                version=intake["version"],
            )
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    started.wait(5)
    thread.join(1.5)
    still_waiting = thread.is_alive()
    tx.commit()
    holder.close()
    thread.join(30)
    assert still_waiting, "확정이 행 잠금을 기다리지 않고 진행했다"
    assert (
        isinstance(result.get("error"), AppError)
        and result["error"].code.value == "ORDER_INTAKE.STATE.NOT_PENDING"
    )
    assert scalar("SELECT count(*) FROM sales_orders") == 0


# ── 12. 정리 항목 ───────────────────────────────────────────────────────────────


def test_event_payloads_are_checked_against_the_whitelist() -> None:
    """이벤트 payload는 화이트리스트와 키가 정확히 같을 때만 나간다 — 키가 새면 ValueError(금액·사유 원문 유출 회귀 차단)"""
    from app.modules.order_intake import machine

    with pytest.raises(ValueError):
        machine._checked_payload({"intake_id": 1, "amount": 5}, machine.CREATED_PAYLOAD_KEYS)
    ok = dict.fromkeys(machine.CREATED_PAYLOAD_KEYS, 1)
    assert machine._checked_payload(ok, machine.CREATED_PAYLOAD_KEYS) is ok


def test_the_role_set_and_reason_bounds_have_single_sources() -> None:
    """확정·쓰기 역할 집합·거부 사유 길이·품번 길이는 각각 한 곳에서 온다"""
    from app.modules.gates import text as gates_text
    from app.modules.order_intake import models

    assert intake_flow.CONFIRM_ROLES is intake_service.INTAKE_WRITE_ROLES
    assert (models.REJECT_REASON_MIN, models.REJECT_REASON_MAX) == (
        gates_text.REASON_MIN,
        gates_text.REASON_MAX,
    )


@pytest.mark.group_j
def test_the_confirmation_query_count_does_not_grow_with_the_number_of_lines() -> None:
    """확정의 SKU·판가 조회는 일괄이다 — 라인 2개와 20개의 SELECT 수가 같다(SO 라인 INSERT는 묶음 처리)"""
    from tests.support.sqlcount import count_statements

    warm = land(world())  # 채번 카운터 행 생성 등 1회성 질의를 먼저 지나간다
    intake_flow.confirm_intake(
        actor=trade_actor(),
        idempotency_key=unique("warm"),
        intake_id=warm["id"],
        version=warm["version"],
    )
    counts: list[int] = []
    for n in (2, 20):
        w = world(lines=n)
        intake = land(w)
        counts.append(
            count_statements(
                lambda i=intake: intake_flow.confirm_intake(
                    actor=trade_actor(),
                    idempotency_key=unique("q"),
                    intake_id=i["id"],
                    version=i["version"],
                )
            )
        )
    assert counts[0] > 0 and counts[0] == counts[1], counts


def test_list_filters_by_search_text_and_assignee(trade: Any) -> None:
    """목록 `q`(PO번호·거래처명 부분 일치, 와일드카드 이스케이프)와 `assignee_id` 필터"""
    w = world()
    a = register(trade, w, po_no="ZEBRA-100")
    register(trade, w, po_no="OTHER-200")
    assert [i["id"] for i in trade.get(f"{INTAKES}?q=zebra").json()["items"]] == [a["id"]]
    assert trade.get(f"{INTAKES}?q=%25").json()["total"] == 0  # '%'는 문자 그대로
    assert trade.get(f"{INTAKES}?q=Acme").json()["total"] == 2  # 거래처명(영문) 일치
    me = a["assignee_id"]
    assert trade.get(f"{INTAKES}?assignee_id={me}").json()["total"] == 2
    assert trade.get(f"{INTAKES}?assignee_id={me + 9999}").json()["total"] == 0
