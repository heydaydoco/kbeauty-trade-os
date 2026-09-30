"""A. 견적 상태 전이 — QT 20쌍 전수·통로 규칙·역순 취소·수렴·개정 원자성 (S3-1 PR-5a / ADR-0051·0052 / design-B B1·B3).

QT 5상태의 순서쌍 20개: 허용 6(사람 3·자동 3)은 성공, 미허용 14는 409 전건(ADR-0038의 전수 선례). PI·SO·PO의 전수는
각 전표 PR이 같은 방식으로 더한다(machine 총수 126은 K의 test_doc_machines가 이미 고정). 성공 방향(허용 쌍)과 거부 방향
(미허용·잘못된 통로·사유 결여)을 함께 검증한다.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import permutations
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.quotations.models import Quotation
from app.modules.trade_chain import chain_ops, lifecycle
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import AUTO_TRANSITIONS, HUMAN_TRANSITIONS, STATUSES
from app.modules.trade_docs.transition import record_transition
from tests.factories.trade import (
    create_buyer,
    create_priced_sku,
    create_quotation_via_api,
    fake_successors,
    idem,
    issue_via_api,
    logged_in,
    raw_quotation,
)
from tests.support.factories import create_user

pytestmark = pytest.mark.group_a

QT = "/api/v1/quotations"
KIND = DocKind.QUOTATION
STATES = STATUSES[KIND]
HUMAN = HUMAN_TRANSITIONS[KIND]
AUTO = AUTO_TRANSITIONS[KIND]
FREEZE_EDGE = ("DRAFT", "ISSUED")


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _log_rows(qt_id: int) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [
            tuple(r)
            for r in connection.execute(
                text(
                    "SELECT from_status, to_status, automatic, reason FROM quotation_status_log"
                    " WHERE quotation_id = :q ORDER BY id"
                ),
                {"q": qt_id},
            )
        ]


def _events(qt_id: int) -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM events WHERE aggregate_type = 'quotations' AND aggregate_id = :i"
            " AND event_type = 'quotations.quotation.status_changed'",
            i=qt_id,
        )
    )


def _transition(qt_id: int, to: str, **kwargs: Any) -> str:
    with unit_of_work() as uow:
        row = uow.session.get(Quotation, qt_id)
        assert row is not None
        return record_transition(uow.session, row, to, **kwargs)


_ALL_PAIRS = list(permutations(STATES, 2))


def _kwargs_for(pair: tuple[str, str], actor: int) -> dict[str, Any]:
    to = pair[1]
    auto = pair in AUTO
    return {
        "actor_user_id": None if auto and to == "EXPIRED" else actor,
        "reason": "테스트 사유" if to in ("CANCELLED", "EXPIRED") else None,
        "automatic": auto,
        "via_freeze_action": pair == FREEZE_EDGE,
    }


@pytest.mark.parametrize("pair", _ALL_PAIRS, ids=[f"{a}->{b}" for a, b in _ALL_PAIRS])
def test_all_twenty_qt_pairs(pair: tuple[str, str]) -> None:
    """QT 20쌍 — 허용 6쌍은 성공(이력 1행·automatic 표식·이벤트 1건), 미허용 14쌍은 409 TRANSITION.NOT_ALLOWED이고 무변"""
    frm, to = pair
    actor = create_user(f"actor-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    qt_id = raw_quotation(frm)
    before_log, before_events = len(_log_rows(qt_id)), _events(qt_id)
    if pair in HUMAN | AUTO:
        assert _transition(qt_id, to, **_kwargs_for(pair, actor)) == frm
        assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == to
        rows = _log_rows(qt_id)
        assert len(rows) == before_log + 1 and _events(qt_id) == before_events + 1
        assert rows[-1][:3] == (frm, to, pair in AUTO)
        if to in ("CANCELLED", "EXPIRED"):
            assert rows[-1][3] == "테스트 사유"
        if pair == FREEZE_EDGE:
            assert _scalar("SELECT frozen_at IS NOT NULL FROM quotations WHERE id = :i", i=qt_id)
    else:
        with pytest.raises(AppError) as caught:
            _transition(qt_id, to, **_kwargs_for(pair, actor))
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
        assert caught.value.status_code == 409
        assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == frm
        assert len(_log_rows(qt_id)) == before_log and _events(qt_id) == before_events


def test_the_pair_table_itself_is_not_vacuous() -> None:
    """공회전 방지 — 20쌍 중 허용 6·미허용 14가 실제로 갈린다"""
    assert len(_ALL_PAIRS) == 20
    assert len([p for p in _ALL_PAIRS if p in HUMAN | AUTO]) == 6


@pytest.mark.parametrize("pair", sorted(HUMAN | AUTO))
def test_wrong_channel_is_rejected_even_for_an_allowed_pair(pair: tuple[str, str]) -> None:
    """허용 쌍도 통로가 틀리면 거부 — 사람 쌍에 automatic=True·자동 쌍에 automatic=False, 동결 엣지는 동결 액션으로만"""
    frm, to = pair
    actor = create_user(f"chan-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    qt_id = raw_quotation(frm)
    good = _kwargs_for(pair, actor)
    flipped = {**good, "automatic": not good["automatic"]}
    if flipped["automatic"] is False:  # 자동 → 사람으로 뒤집으면 행위자가 필요하다
        flipped["actor_user_id"] = actor
    with pytest.raises(AppError) as caught:
        _transition(qt_id, to, **flipped)
    assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    if pair == FREEZE_EDGE:  # 동결 액션 없이 사람 통로로 발행 시도
        with pytest.raises(AppError):
            _transition(qt_id, to, **{**good, "via_freeze_action": False})
    else:  # 동결 액션 통로로 일반 전이를 넘기려는 시도
        with pytest.raises(AppError):
            _transition(qt_id, to, **{**good, "via_freeze_action": True})
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == frm


@pytest.mark.parametrize(
    ("frm", "to", "auto"),
    [("DRAFT", "CANCELLED", False), ("ISSUED", "CANCELLED", False), ("ISSUED", "EXPIRED", True)],
)
@pytest.mark.parametrize("reason", [None, "", "   ", "\n\t"])
def test_cancel_and_expire_need_a_non_blank_reason(
    frm: str, to: str, auto: bool, reason: str | None
) -> None:
    """취소·만료는 사유 필수 — 결여·공백뿐이면 422 REASON_REQUIRED이고 상태는 무변"""
    actor = create_user(f"rsn-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    qt_id = raw_quotation(frm)
    with pytest.raises(AppError) as caught:
        _transition(qt_id, to, actor_user_id=None if auto else actor, reason=reason, automatic=auto)
    assert caught.value.code == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    assert caught.value.status_code == 422
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == frm


def test_reason_length_limit_and_trim() -> None:
    """사유는 앞뒤 공백을 자르고 500자를 넘으면 422 — 이력에는 잘린 값이 남는다"""
    actor = create_user("rsn-len@example.com", roles=(RoleCode.TRADE,))
    qt_id = raw_quotation("DRAFT")
    with pytest.raises(AppError) as caught:
        _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="가" * 501, automatic=False)
    assert caught.value.code == "COMMON.VALIDATION.INVALID_FIELD"
    _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="  폐기  ", automatic=False)
    assert _log_rows(qt_id)[-1][3] == "폐기"


# ── 역순 취소 ───────────────────────────────────────────────────────────────


def test_cancel_is_blocked_by_a_live_successor_and_freed_by_dead_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """살아 있는 후속이 있으면 취소 409(문서번호만 안내) — 취소·만료·삭제된 후속은 막지 않고 ON_HOLD 등은 살아 있다"""
    actor = create_user("rev-cancel@example.com", roles=(RoleCode.TRADE,))
    with fake_successors(monkeypatch) as fake:
        qt_id = raw_quotation("ISSUED")
        live = fake.add(qt_id, status="ISSUED", number="SC-LIVE-1")
        held = fake.add(qt_id, status="ON_HOLD", number="SC-HOLD-1")
        with pytest.raises(AppError) as caught:
            _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="취소", automatic=False)
        assert caught.value.code == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        assert caught.value.detail == {"successors": ["SC-LIVE-1", "SC-HOLD-1"]}
        assert "amount" not in str(caught.value.detail)
        fake.set_status(live, "CANCELLED")  # 하나만 죽어도 나머지가 붙잡는다
        with pytest.raises(AppError):
            _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="취소", automatic=False)
        fake.set_status(held, "EXPIRED")
        _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="취소", automatic=False)
        assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == "CANCELLED"
        # 삭제(soft delete)된 후속도 세지 않는다
        other = raw_quotation("ISSUED")
        ghost = fake.add(other, status="ISSUED")
        with owner_engine.begin() as connection:
            connection.execute(
                text(f'UPDATE public."{fake.table_name}" SET deleted_at = now() WHERE id = :i'),
                {"i": ghost},
            )
        _transition(other, "CANCELLED", actor_user_id=actor, reason="취소", automatic=False)


def test_a_converted_quotation_reports_the_blocking_successor_not_a_bare_transition_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """수주전환(CONVERTED) QT 취소 시도는 '전이 불가'가 아니라 '먼저 취소할 후속'(SUCCESSOR_ALIVE)으로 안내한다"""
    actor = create_user("rev-conv@example.com", roles=(RoleCode.TRADE,))
    with fake_successors(monkeypatch) as fake:
        qt_id = raw_quotation("CONVERTED")
        fake.add(qt_id, status="CONFIRMED", confirmed=True, number="SC-CONF-1")
        with pytest.raises(AppError) as caught:
            _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="취소", automatic=False)
        assert caught.value.code == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    # 후속이 없는 CONVERTED(불일치 상태)는 후속 검사를 통과하고 엣지 검사에서 막힌다 — CONVERTED→CANCELLED는 없다
    qt_id = raw_quotation("CONVERTED")
    with pytest.raises(AppError) as caught2:
        _transition(qt_id, "CANCELLED", actor_user_id=actor, reason="취소", automatic=False)
    assert caught2.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"


# ── 수렴(converge_quotation) ────────────────────────────────────────────────


def _converge(qt_id: int, actor: int | None) -> str | None:
    with unit_of_work() as uow:
        return chain_ops.converge_quotation(uow.session, qt_id, actor_user_id=actor)


def test_quotation_converts_when_a_live_confirmed_successor_exists_and_returns_when_it_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """살아 있는 확정 후속 ≥1 → CONVERTED, 마지막 확정 후속이 죽으면 ISSUED로 복귀(자동 전이 — 행위자=유발자·이력 automatic)"""
    actor = create_user("conv@example.com", roles=(RoleCode.TRADE,))
    with fake_successors(monkeypatch) as fake:
        qt_id = raw_quotation("ISSUED")
        unconfirmed = fake.add(
            qt_id, status="ISSUED", confirmed=False
        )  # PI 대역 — 전환시키지 않는다
        assert _converge(qt_id, actor) is None
        assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == "ISSUED"
        confirmed = fake.add(qt_id, status="CONFIRMED", confirmed=True)
        assert _converge(qt_id, actor) == "CONVERTED"
        assert _converge(qt_id, actor) is None  # 멱등
        row = _log_rows(qt_id)[-1]
        assert row[:3] == ("ISSUED", "CONVERTED", True)
        fake.set_status(confirmed, "CANCELLED")
        assert _converge(qt_id, actor) == "ISSUED"
        assert _log_rows(qt_id)[-1][:3] == ("CONVERTED", "ISSUED", True)
        assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt_id) == "ISSUED"
        fake.set_status(unconfirmed, "CANCELLED")
        assert _converge(qt_id, actor) is None


def test_lapsed_quotation_expires_only_when_nothing_holds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """복귀 직후 유효기간이 지났고 붙잡는 후속이 없으면 같은 트랜잭션에서 EXPIRED(이력 2행) · 미확정 후속이 있으면 유지"""
    actor = create_user("lapse@example.com", roles=(RoleCode.TRADE,))
    yesterday = today_kst() - timedelta(days=1)
    with fake_successors(monkeypatch) as fake:
        held = raw_quotation("CONVERTED", valid_until=yesterday)
        pending_pi = fake.add(
            held, status="ISSUED", confirmed=False
        )  # 확정 SO는 없고 PI만 살아 있다
        assert _converge(held, actor) == "ISSUED"  # 복귀만 하고 PI가 붙잡아 EXPIRED로 가지 않는다
        assert _scalar("SELECT status FROM quotations WHERE id = :i", i=held) == "ISSUED"
        fake.set_status(pending_pi, "CANCELLED")
        assert _converge(held, actor) == "EXPIRED"
        assert [r[:3] for r in _log_rows(held)[-1:]] == [("ISSUED", "EXPIRED", True)]
        assert "자동: 유효기간 경과" in _log_rows(held)[-1][3]
        free = raw_quotation("CONVERTED", valid_until=yesterday)  # 후속이 아예 없다
        assert _converge(free, actor) == "EXPIRED"
        tail = _log_rows(free)[-2:]
        assert [t[:3] for t in tail] == [("CONVERTED", "ISSUED", True), ("ISSUED", "EXPIRED", True)]


def test_convergence_does_not_touch_other_states() -> None:
    """DRAFT·CANCELLED·EXPIRED는 수렴 대상이 아니다(None) — 만료 당일까지는 유효(포함 경계)"""
    for state in ("DRAFT", "CANCELLED", "EXPIRED"):
        assert _converge(raw_quotation(state), None) is None
    today_valid = raw_quotation("ISSUED", valid_until=today_kst())
    assert _converge(today_valid, None) is None  # valid_until = 오늘 → 아직 유효


def test_lock_chain_locks_the_quotation_and_returns_it() -> None:
    """lock_chain(QT)은 자기 행을 FOR UPDATE로 잠근다(조상 없음) — 삭제된 행은 404"""
    from app.core.errors.exceptions import NotFoundError

    qt_id = raw_quotation("DRAFT")
    with unit_of_work() as uow:
        locked = chain_ops.lock_chain(uow.session, KIND, qt_id)
        assert locked[KIND].id == qt_id and set(locked) == {KIND}
    with pytest.raises(NotFoundError), unit_of_work() as uow:
        chain_ops.lock_chain(uow.session, KIND, 999_999)


# ── API 흐름: 발행·취소 ─────────────────────────────────────────────────────


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def test_issue_requires_a_complete_document_and_leaves_no_trace_on_failure(
    trade: TestClient,
) -> None:
    """발행 완결성: 결제조건·Incoterms·환율·유효기간·라인이 없으면 422(항목별 안내) — 이력·이벤트·상태 무변"""
    buyer = create_buyer(address_en=None)
    qt = create_quotation_via_api(
        trade, buyer, payment_terms=None, incoterm=None, fx_rate=None, valid_until=None
    )
    response = trade.post(f"{QT}/{qt['id']}/issue", json={"version": qt["version"]}, headers=idem())
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "TRADE_DOCS.DOCUMENT.INCOMPLETE"
    assert set(error["detail"]) == {
        "payment_terms", "incoterm", "fx_rate", "buyer_address", "valid_until", "lines",
    }  # fmt: skip
    assert len(_log_rows(qt["id"])) == 1 and _events(qt["id"]) == 0
    assert trade.get(f"{QT}/{qt['id']}").json()["status"] == "DRAFT"


def test_issue_success_freezes_records_history_and_replays_on_double_click(
    trade: TestClient,
) -> None:
    """발행: DRAFT→ISSUED·frozen_at·이력 1행(사람)·이벤트 1건 · 같은 키 재전송은 최초 결과 그대로(이력 1행 유지)"""
    buyer = create_buyer()
    qt = create_quotation_via_api(trade, buyer, [create_priced_sku()])
    key = idem()
    first = trade.post(f"{QT}/{qt['id']}/issue", json={"version": qt["version"]}, headers=key)
    second = trade.post(f"{QT}/{qt['id']}/issue", json={"version": qt["version"]}, headers=key)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["status"] == "ISSUED" and body["frozen_at"] and body["version"] == qt["version"] + 1
    assert [r[:3] for r in _log_rows(qt["id"])] == [
        (None, "DRAFT", False),
        ("DRAFT", "ISSUED", False),
    ]
    assert _events(qt["id"]) == 1
    again = trade.post(f"{QT}/{qt['id']}/issue", json={"version": body["version"]}, headers=idem())
    assert again.status_code == 409  # 이미 발행됨 — 새 키로 다시 눌러도 전이 불가
    assert again.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    stale = trade.post(f"{QT}/{qt['id']}/issue", json={"version": qt["version"]}, headers=idem())
    assert stale.status_code == 409 and "VERSION_CONFLICT" in stale.json()["error"]["code"]
    assert (
        trade.post(f"{QT}/{qt['id']}/issue", json={"version": 1}).status_code == 400
    )  # 멱등 키 필수


def test_issue_rejects_an_already_lapsed_validity_and_a_released_buyer_type(
    trade: TestClient,
) -> None:
    """발행 시점 재검증: 유효기간이 이미 지났으면 422 · 바이어 유형이 해제됐으면 422(fail-closed) — 상태 무변"""
    buyer = create_buyer()
    old = today_kst() - timedelta(days=40)
    lapsed = create_quotation_via_api(
        trade,
        buyer,
        [create_priced_sku()],
        doc_date=old.isoformat(),
        fx_rate_date=old.isoformat(),
        valid_until=(today_kst() - timedelta(days=10)).isoformat(),
    )
    response = trade.post(
        f"{QT}/{lapsed['id']}/issue", json={"version": lapsed["version"]}, headers=idem()
    )
    assert response.status_code == 422 and "valid_until" in response.json()["error"]["detail"]
    fine = create_quotation_via_api(trade, buyer, [create_priced_sku()])
    with unit_of_work() as uow:
        uow.session.execute(
            text("UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p"),
            {"p": buyer},
        )
    denied = trade.post(
        f"{QT}/{fine['id']}/issue", json={"version": fine["version"]}, headers=idem()
    )
    assert denied.status_code == 422 and "buyer_partner_id" in denied.json()["error"]["detail"]
    assert trade.get(f"{QT}/{fine['id']}").json()["status"] == "DRAFT"


def test_cancel_flow_needs_a_reason_and_is_final(trade: TestClient) -> None:
    """취소: 초안 폐기·발행본 취소 모두 사유 필수(공백 불가) · 취소 후 재취소·발행·편집은 409 — 번호는 남는다"""
    buyer = create_buyer()
    draft = create_quotation_via_api(trade, buyer, [create_priced_sku()])
    issued = issue_via_api(trade, create_quotation_via_api(trade, buyer, [create_priced_sku()]))
    for qt in (draft, issued):
        url = f"{QT}/{qt['id']}/transitions"
        for reason in (None, "", "   "):
            body = {"to": "CANCELLED", "version": qt["version"], "reason": reason}
            response = trade.post(url, json=body, headers=idem())
            assert response.status_code == 422, reason
            assert response.json()["error"]["code"] == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
        done = trade.post(
            url,
            json={"to": "CANCELLED", "version": qt["version"], "reason": " 바이어 철회 "},
            headers=idem(),
        )
        assert done.status_code == 200 and done.json()["status"] == "CANCELLED"
        assert done.json()["doc_number"] == qt["doc_number"]  # 결번·재사용 없음
        assert _log_rows(qt["id"])[-1][3] == "바이어 철회"
        version = done.json()["version"]
        again = trade.post(
            url, json={"to": "CANCELLED", "version": version, "reason": "또"}, headers=idem()
        )
        assert again.status_code == 409
        assert (
            trade.post(
                f"{QT}/{qt['id']}/issue", json={"version": version}, headers=idem()
            ).status_code
            == 409
        )
        frozen_edit = trade.patch(
            f"{QT}/{qt['id']}", json={"version": version, "buyer_name": "변경"}
        )
        assert frozen_edit.status_code == 409
        assert trade.get(f"{QT}/{qt['id']}").json()["doc_number"] == qt["doc_number"]


def test_cancelling_with_a_live_successor_via_the_api_is_409_with_document_numbers(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API 취소도 역순 규칙 — 후속이 살아 있으면 409 + 문서번호 안내, 후속이 죽으면 취소 성공"""
    buyer = create_buyer()
    qt = issue_via_api(trade, create_quotation_via_api(trade, buyer, [create_priced_sku()]))
    with fake_successors(monkeypatch) as fake:
        child = fake.add(qt["id"], status="ISSUED", number="SC-2026-0007")
        url = f"{QT}/{qt['id']}/transitions"
        body = {"to": "CANCELLED", "version": qt["version"], "reason": "취소"}
        blocked = trade.post(url, json=body, headers=idem())
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        assert blocked.json()["error"]["detail"]["successors"] == ["SC-2026-0007"]
        assert trade.get(f"{QT}/{qt['id']}").json()["status"] == "ISSUED"
        fake.set_status(child, "CANCELLED")
        assert trade.post(url, json=body, headers=idem()).status_code == 200


def test_viewer_logistics_cert_cannot_write_or_transition(trade: TestClient) -> None:
    """쓰기·전이 엔드포인트는 무역(관리자)만 — 나머지 3역할은 403이고 상태는 무변"""
    buyer = create_buyer()
    qt = create_quotation_via_api(trade, buyer, [create_priced_sku()])
    for role in (RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT):
        with logged_in(role) as other:
            assert (
                other.post(
                    f"{QT}/{qt['id']}/issue", json={"version": qt["version"]}, headers=idem()
                ).status_code
                == 403
            )
            assert (
                other.patch(f"{QT}/{qt['id']}", json={"version": qt["version"]}).status_code == 403
            )
            assert other.post(QT, json={}, headers=idem()).status_code == 403
    assert trade.get(f"{QT}/{qt['id']}").json()["status"] == "DRAFT"
    with logged_in(RoleCode.ADMIN) as admin:  # 관리자는 상시 통과
        assert (
            admin.post(
                f"{QT}/{qt['id']}/issue", json={"version": qt["version"]}, headers=idem()
            ).status_code
            == 200
        )


# ── 개정 ────────────────────────────────────────────────────────────────────


def _revise(client: TestClient, qt: dict[str, Any]) -> Any:
    return client.post(
        f"{QT}/{qt['id']}/revisions", json={"version": qt["version"]}, headers=idem()
    )


def _finish_revision(client: TestClient, draft: dict[str, Any]) -> dict[str, Any]:
    """개정 초안에 새 유효기간을 넣고 발행한다."""
    patched = client.patch(
        f"{QT}/{draft['id']}",
        json={
            "version": draft["version"],
            "valid_until": (today_kst() + timedelta(days=45)).isoformat(),
        },
    )
    assert patched.status_code == 200, patched.text
    return issue_via_api(client, patched.json())


def test_revision_copies_the_source_and_publishing_it_cancels_the_original_atomically(
    trade: TestClient,
) -> None:
    """개정: 원본(ISSUED) 값 복사 초안 생성 → 초안 발행이 같은 트랜잭션에서 원본을 취소(사람 엣지·행위자=발행자·자동 문구)"""
    from tests.factories.trade import set_price

    buyer = create_buyer()
    sku = create_priced_sku(amount=1000)
    original = issue_via_api(trade, create_quotation_via_api(trade, buyer, [sku]))
    set_price(
        sku, 5000, effective_from=today_kst()
    )  # 마스터가 바뀌어도 개정본은 원본 값을 복사한다
    response = _revise(trade, original)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "DRAFT" and draft["copied_from_id"] == original["id"]
    assert draft["valid_until"] is None and draft["doc_number"] != original["doc_number"]
    assert [ln["unit_price_amount"] for ln in draft["lines"]] == [1000]
    assert draft["total_amount"] == original["total_amount"]
    assert (draft["buyer_name"], draft["payment_terms"], draft["incoterm"]) == (
        original["buyer_name"], original["payment_terms"], original["incoterm"],
    )  # fmt: skip
    assert (
        trade.get(f"{QT}/{original['id']}").json()["status"] == "ISSUED"
    )  # 초안 단계에선 원본 무변
    incomplete = trade.post(
        f"{QT}/{draft['id']}/issue", json={"version": draft["version"]}, headers=idem()
    )
    assert incomplete.status_code == 422 and "valid_until" in incomplete.json()["error"]["detail"]
    assert (
        trade.get(f"{QT}/{original['id']}").json()["status"] == "ISSUED"
    )  # 발행 실패 → 원본 취소도 없다
    issued = _finish_revision(trade, draft)
    assert issued["status"] == "ISSUED"
    cancelled = trade.get(f"{QT}/{original['id']}").json()
    assert cancelled["status"] == "CANCELLED"
    row = _log_rows(original["id"])[-1]
    assert row[:3] == ("ISSUED", "CANCELLED", False)  # 사람 엣지 — 자동이 아니다
    assert issued["doc_number"] in row[3] and "개정" in row[3]
    actor = _scalar(
        "SELECT actor_user_id FROM quotation_status_log WHERE quotation_id = :q ORDER BY id DESC LIMIT 1",
        q=original["id"],
    )
    assert actor == _scalar(
        "SELECT actor_user_id FROM quotation_status_log WHERE quotation_id = :q ORDER BY id DESC LIMIT 1",
        q=issued["id"],
    )


def test_revision_source_and_uniqueness_rules(trade: TestClient) -> None:
    """개정은 발행 상태 원본만 · 원본당 살아 있는 개정본 1개 · 개정본이 취소되면 다시 가능"""
    buyer = create_buyer()
    sku = create_priced_sku()
    draft_source = create_quotation_via_api(trade, buyer, [sku])
    denied = _revise(trade, draft_source)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    original = issue_via_api(trade, create_quotation_via_api(trade, buyer, [sku]))
    first = _revise(trade, original)
    assert first.status_code == 201
    second = _revise(trade, original)
    assert (
        second.status_code == 409
        and second.json()["error"]["code"] == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    )
    stale = trade.post(f"{QT}/{original['id']}/revisions", json={"version": 1}, headers=idem())
    assert stale.status_code == 409
    cancel = trade.post(
        f"{QT}/{first.json()['id']}/transitions",
        json={"to": "CANCELLED", "version": first.json()["version"], "reason": "개정 포기"},
        headers=idem(),
    )
    assert cancel.status_code == 200
    third = _revise(trade, original)
    assert third.status_code == 201  # 살아 있는 개정본이 없어졌다
    assert trade.get(f"{QT}/{original['id']}").json()["status"] == "ISSUED"


def test_revision_is_refused_while_a_live_successor_holds_the_original(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원본에 살아 있는 후속(PI·SO)이 있으면 개정 초안 생성 409 — 이미 만든 초안의 발행도 409이고 둘 다 무변"""
    buyer = create_buyer()
    sku = create_priced_sku()
    with fake_successors(monkeypatch) as fake:
        held = issue_via_api(trade, create_quotation_via_api(trade, buyer, [sku]))
        blocker = fake.add(held["id"], status="ISSUED", number="SC-2026-0001")
        refused = _revise(trade, held)
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        assert refused.json()["error"]["detail"]["successors"] == ["SC-2026-0001"]
        # 후속이 없을 때 만든 개정 초안이, 발행 전에 후속이 생기면 발행에서 막힌다
        original = issue_via_api(trade, create_quotation_via_api(trade, buyer, [sku]))
        draft = _revise(trade, original).json()
        late = fake.add(original["id"], status="ISSUED", number="SC-2026-0002")
        patched = trade.patch(
            f"{QT}/{draft['id']}",
            json={
                "version": draft["version"],
                "valid_until": (today_kst() + timedelta(days=30)).isoformat(),
            },
        ).json()
        blocked = trade.post(
            f"{QT}/{draft['id']}/issue", json={"version": patched["version"]}, headers=idem()
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        assert trade.get(f"{QT}/{original['id']}").json()["status"] == "ISSUED"
        assert trade.get(f"{QT}/{draft['id']}").json()["status"] == "DRAFT"
        assert len(_log_rows(original["id"])) == 2 and len(_log_rows(draft["id"])) == 1
        fake.set_status(late, "CANCELLED")
        fake.set_status(blocker, "CANCELLED")
        assert (
            trade.post(
                f"{QT}/{draft['id']}/issue", json={"version": patched["version"]}, headers=idem()
            ).status_code
            == 200
        )
        assert trade.get(f"{QT}/{original['id']}").json()["status"] == "CANCELLED"


def test_revision_publish_is_atomic_when_the_second_step_fails(
    trade: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """개정 발행 원자성 — 원본 취소는 성공했는데 새 QT 발행이 실패하면 원본 취소·이력·이벤트가 전부 롤백된다"""
    buyer = create_buyer()
    sku = create_priced_sku()
    original = issue_via_api(trade, create_quotation_via_api(trade, buyer, [sku]))
    draft = _revise(trade, original).json()
    patched = trade.patch(
        f"{QT}/{draft['id']}",
        json={
            "version": draft["version"],
            "valid_until": (today_kst() + timedelta(days=30)).isoformat(),
        },
    ).json()
    real = lifecycle.record_transition
    calls: list[str] = []

    def flaky(session: Any, doc: Any, to: str, **kwargs: Any) -> str:
        calls.append(to)
        if to == "ISSUED":
            raise RuntimeError("발행 단계 실패 주입")
        return real(session, doc, to, **kwargs)

    monkeypatch.setattr(lifecycle, "record_transition", flaky)
    actor = _scalar("SELECT id FROM users LIMIT 1")
    from app.modules.identity.service import AuthenticatedUser

    user = AuthenticatedUser(
        id=actor, email="x@x", display_name="x", roles=frozenset({RoleCode.TRADE}), session_id=0
    )
    with pytest.raises(RuntimeError):
        lifecycle.issue_quotation(
            actor=user, idempotency_key="k-atomic", qt_id=draft["id"], version=patched["version"]
        )
    assert calls == ["CANCELLED", "ISSUED"]  # 원본 취소가 먼저 실행됐다
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=original["id"]) == "ISSUED"
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=draft["id"]) == "DRAFT"
    assert len(_log_rows(original["id"])) == 2 and _events(original["id"]) == 1
    assert _scalar("SELECT count(*) FROM idempotency_keys WHERE idempotency_key = 'k-atomic'") == 0
    monkeypatch.setattr(lifecycle, "record_transition", real)  # 정상 경로 재시도는 성공
    ok = trade.post(
        f"{QT}/{draft['id']}/issue", json={"version": patched["version"]}, headers=idem()
    )
    assert (
        ok.status_code == 200
        and _scalar("SELECT status FROM quotations WHERE id = :i", i=original["id"]) == "CANCELLED"
    )


def test_publishing_a_copy_of_a_dead_source_does_not_touch_the_source(trade: TestClient) -> None:
    """만료·취소 원본의 복제(재발행)를 발행해도 원본은 손대지 않는다(이미 죽은 전표에 이력 추가 없음)"""
    buyer = create_buyer()
    sku = create_priced_sku()
    source = create_quotation_via_api(trade, buyer, [sku])
    cancelled = trade.post(
        f"{QT}/{source['id']}/transitions",
        json={"to": "CANCELLED", "version": source["version"], "reason": "재작성"},
        headers=idem(),
    ).json()
    copy = create_quotation_via_api(trade, buyer, [sku], copied_from_id=cancelled["id"])
    issue_via_api(trade, copy)
    assert len(_log_rows(source["id"])) == 2 and _events(source["id"]) == 1
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=source["id"]) == "CANCELLED"
