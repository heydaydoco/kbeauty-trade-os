"""K·J. 오더 보드 저장 필터 — 본인 것만(타인 404)·사용자당 20개·이름 유일·스키마 재검증·낙관 잠금·멱등 (S3-1 PR-15a / design-D D6 / ADR-0066·0067 / DESIGN §18.1 부기 ③)."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from app.modules.order_board import saved_filters as saved_service
from app.modules.order_board.constants import SAVED_FILTER_LIMIT
from app.modules.order_board.schemas import BoardFilter
from tests.factories.approvals import client_for, make_user
from tests.factories.board import SAVED, code_of
from tests.factories.trade import idem, logged_in
from tests.support.concurrency import run_concurrently

pytestmark = pytest.mark.group_k


def _create(client: TestClient, name: str, config: dict[str, Any] | None = None, **kw: Any) -> Any:
    return client.post(
        SAVED,
        json={"name": name, "filter_config": config if config is not None else {"q": "PO"}},
        headers=kw.get("headers") or idem(),
    )


def _created(client: TestClient, name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    response = _create(client, name, config)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _mine(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(SAVED, params={"size": 200})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


@pytest.mark.parametrize(
    "role", [RoleCode.VIEWER, RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.TRADE, RoleCode.ADMIN]
)
def test_every_role_can_save_list_update_and_delete_its_own_filters(role: RoleCode) -> None:
    """전 역할이 자기 필터를 만들고·보고·고치고·지운다 — 응답은 재검증된 조건·needs_resave=false·version"""
    with logged_in(role) as client:
        created = _created(
            client,
            "  내 대기 건  ",
            {"q": "ACME", "currency": "USD", "created_from": "2026-09-01"},
        )
        assert created["name"] == "내 대기 건"  # 앞뒤 공백 제거
        assert created["filter_config"] == {
            "q": "ACME",
            "currency": "USD",
            "created_from": "2026-09-01",
            "buyer_partner_id": None,
            "assignee_id": None,
            "dest_market_code": None,
            "created_to": None,
        }
        assert created["needs_resave"] is False and created["version"] == 1
        assert [f["id"] for f in _mine(client)] == [created["id"]]
        updated = client.patch(
            f"{SAVED}/{created['id']}",
            json={"version": created["version"], "name": "새 이름", "filter_config": {"q": "Z"}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "새 이름" and updated.json()["version"] == 2
        assert updated.json()["filter_config"]["q"] == "Z"
        deleted = client.delete(f"{SAVED}/{created['id']}", params={"version": 2})
        assert deleted.status_code == 204
        assert _mine(client) == []


def test_another_users_filter_is_invisible_and_404_for_update_and_delete() -> None:
    """당사자성 — 남의 필터는 목록에 없고, 수정·삭제는 404(존재 여부를 알리지 않는다 — 403 아님)·원본 무변"""
    owner = make_user(RoleCode.TRADE)
    intruder = make_user(RoleCode.ADMIN)
    with client_for(owner) as c1:
        mine = _created(c1, "주인 필터")
    with client_for(intruder) as c2:
        assert _mine(c2) == []
        patch = c2.patch(f"{SAVED}/{mine['id']}", json={"version": 1, "name": "탈취"})
        delete = c2.delete(f"{SAVED}/{mine['id']}", params={"version": 1})
        missing = c2.patch(f"{SAVED}/999999999", json={"version": 1, "name": "x"})
    assert patch.status_code == delete.status_code == missing.status_code == 404
    assert code_of(patch) == code_of(missing) == "COMMON.RESOURCE.NOT_FOUND"
    with client_for(owner) as c1:
        (only,) = _mine(c1)
    assert only["name"] == "주인 필터" and only["version"] == 1


def test_the_21st_filter_is_refused_and_deleting_one_frees_a_slot() -> None:
    """사용자당 활성 20개 — 21번째는 422 `ORDER_BOARD.FILTER.LIMIT_REACHED`, 하나를 지우면 다시 저장된다(다른 사용자의 개수와 무관)"""
    with logged_in(RoleCode.TRADE) as client:
        made = [_created(client, f"필터 {n:02d}") for n in range(SAVED_FILTER_LIMIT)]
        over = _create(client, "스물한 번째")
        assert over.status_code == 422 and code_of(over) == "ORDER_BOARD.FILTER.LIMIT_REACHED"
        assert client.delete(f"{SAVED}/{made[0]['id']}", params={"version": 1}).status_code == 204
        assert _create(client, "스물한 번째").status_code == 201
        assert len(_mine(client)) == SAVED_FILTER_LIMIT
    with logged_in(RoleCode.VIEWER) as other:
        assert _create(other, "남의 첫 필터").status_code == 201


def test_names_are_unique_per_user_and_a_deleted_name_is_free_again() -> None:
    """이름은 사용자 안에서 유일(409 `DUPLICATE_NAME` — 앞뒤 공백 무시)·수정으로 겹쳐도 409, 지운 이름은 다시 쓸 수 있고 다른 사용자는 같은 이름을 쓸 수 있다"""
    with logged_in(RoleCode.TRADE) as client:
        first = _created(client, "주간 확인")
        dup = _create(client, " 주간 확인 ")
        assert dup.status_code == 409 and code_of(dup) == "ORDER_BOARD.FILTER.DUPLICATE_NAME"
        second = _created(client, "월간 확인")
        rename = client.patch(f"{SAVED}/{second['id']}", json={"version": 1, "name": "주간 확인"})
        assert rename.status_code == 409 and code_of(rename) == "ORDER_BOARD.FILTER.DUPLICATE_NAME"
        assert client.delete(f"{SAVED}/{first['id']}", params={"version": 1}).status_code == 204
        assert _create(client, "주간 확인").status_code == 201
    with logged_in(RoleCode.TRADE) as other:
        assert _create(other, "주간 확인").status_code == 201


@pytest.mark.parametrize(
    "config",
    [
        {"unit_cost": 1},
        {"margin_min": 5},
        {"credit_limit": 1},
        {"stage": "SO_RECEIVED"},
        {"currency": "usd"},
        {"created_from": "2026-09-10", "created_to": "2026-09-01"},
        {"q": "x" * 101},
    ],
)
def test_unknown_or_invalid_filter_keys_are_refused_on_save(config: dict[str, Any]) -> None:
    """저장 시 BoardFilter(extra=forbid)로 검증 — 모르는 키(원가·여신 이름 포함)·형식 오류는 422, 아무것도 저장되지 않는다"""
    with logged_in(RoleCode.TRADE) as client:
        response = _create(client, "잘못된 필터", config)
        assert response.status_code == 422, response.text
        patch_target = _created(client, "정상")
        patch = client.patch(
            f"{SAVED}/{patch_target['id']}", json={"version": 1, "filter_config": config}
        )
        assert patch.status_code == 422
        assert [f["name"] for f in _mine(client)] == ["정상"]


BAD_NAMES = {
    "빈": "",
    "공백": "   ",
    "줄바꿈": "줄\n바꿈",
    "탭": "탭\t이름",
    "61자": "가" * 61,
    "NUL": "a\x00b",
    "C1-NEL": "주간\x85필터",
    "C1-APC": "a\x9fb",
    "줄구분-Zl": "a\u2028b",
    "문단구분-Zp": "a\u2029b",
    "제로폭-Cf": "주간\u200b",
    "한글채움": "\u3164",
    "방향제어-Cf": "\u202e주간",
    "끝-제로폭": "주간 필터\u200b ",
}


@pytest.mark.parametrize("name", list(BAD_NAMES.values()), ids=list(BAD_NAMES))
def test_bad_names_are_422_on_create_and_update(name: str) -> None:
    """이름 — 빈 값·공백뿐·61자·보이지 않는 글자(C0·C1 제어·NUL·줄/문단 구분·제로폭·방향 제어·한글 채움 — strip 전 원문 검사)는 등록·수정 모두 422
    `INVALID_FIELD`(DB에 닿기 전 — DB CHECK는 C0·C1 제어만 잡는다), 아무것도 바뀌지 않는다"""
    with logged_in(RoleCode.TRADE) as client:
        created = _create(client, name)
        assert created.status_code == 422, created.text
        assert code_of(created) == "COMMON.VALIDATION.INVALID_FIELD"
        kept = _created(client, "정상 이름")
        patched = client.patch(f"{SAVED}/{kept['id']}", json={"version": 1, "name": name})
        assert patched.status_code == 422, patched.text
        assert code_of(patched) == "COMMON.VALIDATION.INVALID_FIELD"
        (only,) = _mine(client)
        assert only["name"] == "정상 이름" and only["version"] == 1


@pytest.mark.parametrize(
    "config",
    [{"q": "a\x00b"}, {"q": "주간\u200b"}, {"created_from": "9999-12-31"}],
    ids=["q-NUL", "q-제로폭", "날짜-범위밖"],
)
def test_a_filter_config_with_nul_or_out_of_range_dates_is_422_not_500(
    config: dict[str, Any],
) -> None:
    """저장 필터 JSON의 `\\u0000`(JSONB가 거부 — 그대로 닿으면 500)·보이지 않는 글자·범위 밖 날짜는 등록·수정 모두 422, 저장 값 무변"""
    with logged_in(RoleCode.TRADE) as client:
        created = _create(client, "필터", config)
        assert created.status_code == 422, created.text
        kept = _created(client, "정상", {"q": "A"})
        patched = client.patch(
            f"{SAVED}/{kept['id']}", json={"version": 1, "filter_config": config}
        )
        assert patched.status_code == 422, patched.text
        (only,) = _mine(client)
        assert only["filter_config"]["q"] == "A" and only["version"] == 1


def test_the_db_name_checks_are_translated_to_422_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """두 번째 방어선 — 앱 검사를 건너뛰어 DB CHECK(`name_clean`·`name_nonblank`)에 닿아도 500이 아니라 422 `INVALID_FIELD`(제약명 번역)"""
    monkeypatch.setattr(saved_service, "_clean_name", lambda raw: raw)
    with logged_in(RoleCode.TRADE) as client:
        for name in ("a\x01b", "   "):
            response = _create(client, name)
            assert response.status_code == 422, response.text
            assert code_of(response) == "COMMON.VALIDATION.INVALID_FIELD"
        assert _mine(client) == []


def test_a_stale_stored_config_is_not_silently_ignored_but_flagged_needs_resave() -> None:
    """스키마가 바뀌어 지금의 BoardFilter로 검증되지 않는 옛 저장 값은 조용히 무시·변환하지 않는다 — filter_config=null·needs_resave=true, 다시 저장하면 정상"""
    with logged_in(RoleCode.TRADE) as client:
        saved = _created(client, "옛 필터")
        with owner_engine.begin() as connection:
            connection.execute(
                text(
                    'UPDATE board_saved_filters SET filter_config = \'{"q": "A", "legacy_status": "X"}\'::jsonb'
                    " WHERE id = :i"
                ),
                {"i": saved["id"]},
            )
        (stale,) = _mine(client)
        assert stale["needs_resave"] is True and stale["filter_config"] is None
        fixed = client.patch(
            f"{SAVED}/{saved['id']}",
            json={"version": stale["version"], "filter_config": {"q": "A"}},
        )
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["needs_resave"] is False and fixed.json()["filter_config"]["q"] == "A"


def test_version_is_required_and_checked_and_a_noop_patch_does_not_bump_it() -> None:
    """수정·삭제는 version 필수(없으면 422)·낡으면 409 · 바뀐 것이 없는 수정은 version을 올리지 않는다"""
    with logged_in(RoleCode.TRADE) as client:
        saved = _created(client, "버전 필터", {"q": "A"})
        assert client.patch(f"{SAVED}/{saved['id']}", json={"name": "x"}).status_code == 422
        assert client.delete(f"{SAVED}/{saved['id']}").status_code == 422
        same = client.patch(
            f"{SAVED}/{saved['id']}",
            json={"version": 1, "name": "버전 필터", "filter_config": {"q": "A"}},
        )
        assert same.status_code == 200 and same.json()["version"] == 1
        bumped = client.patch(f"{SAVED}/{saved['id']}", json={"version": 1, "name": "바뀜"})
        assert bumped.json()["version"] == 2
        stale = client.patch(f"{SAVED}/{saved['id']}", json={"version": 1, "name": "낡음"})
        assert stale.status_code == 409
        assert client.delete(f"{SAVED}/{saved['id']}", params={"version": 1}).status_code == 409
        assert client.post(SAVED, json={"name": "x", "filter_config": {}}).status_code == 400


@pytest.mark.group_j
def test_create_is_idempotent_by_key_and_a_changed_body_with_the_same_key_conflicts() -> None:
    """같은 Idempotency-Key 재전송 = 같은 필터(201 재생·행 1개), 같은 키로 다른 본문 = 409 KEY_CONFLICT"""
    headers = idem()
    with logged_in(RoleCode.TRADE) as client:
        first = _create(client, "더블클릭", headers=headers)
        again = _create(client, "더블클릭", headers=headers)
        changed = _create(client, "다른 이름", headers=headers)
        assert first.status_code == again.status_code == 201
        assert first.json() == again.json()
        assert changed.status_code == 409 and code_of(changed) == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
        assert len(_mine(client)) == 1


@pytest.mark.group_j
def test_concurrent_creates_at_the_boundary_never_exceed_the_limit() -> None:
    """경계 경합 — 19개 보유자가 서로 다른 이름으로 동시에 4건 저장해도 활성 20개를 넘지 않는다(1건 성공·3건 LIMIT_REACHED)"""
    user = make_user(RoleCode.TRADE)
    for n in range(SAVED_FILTER_LIMIT - 1):
        saved_service.create_saved_filter(
            actor=user, idempotency_key=f"pre-{n}", name=f"기존 {n}", filter_config=BoardFilter()
        )

    def worker(index: int) -> Any:
        return saved_service.create_saved_filter(
            actor=user,
            idempotency_key=f"race-{index}",
            name=f"경합 {index}",
            filter_config=BoardFilter(),
        )

    outcomes = run_concurrently(worker, workers=4)
    ok = [o for o in outcomes if o.ok]
    refused = [o for o in outcomes if not o.ok]
    assert len(ok) == 1 and len(refused) == 3
    assert all(
        getattr(o.error, "code", None) is not None
        and str(o.error.code) == "ORDER_BOARD.FILTER.LIMIT_REACHED"  # type: ignore[union-attr]
        for o in refused
    )
    with owner_engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT count(*) FROM board_saved_filters WHERE user_id = :u AND deleted_at IS NULL"
            ),
            {"u": user.id},
        ).scalar_one()
    assert active == SAVED_FILTER_LIMIT


@pytest.mark.group_j
def test_a_create_that_waited_on_the_boundary_lock_recounts_and_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """결정적 경계 경합 — A가 본인 행을 잠근 채 멈춘 동안 B가 잠금을 **실제로 기다리게**(pg_locks 미부여 확인) 한 뒤 A를 커밋시키면, B는 잠금 뒤 다시 세어
    20개를 보고 422로 거부된다(잠금 문의 결과 행 수로 세면 대기 중 커밋된 A의 행이 빠져 21개가 된다 — READ COMMITTED 함정 회귀 고정)"""
    user = make_user(RoleCode.TRADE)
    for n in range(SAVED_FILTER_LIMIT - 1):
        saved_service.create_saved_filter(
            actor=user, idempotency_key=f"pre-{n}", name=f"기존 {n}", filter_config=BoardFilter()
        )
    a_locked = threading.Event()
    original = saved_service._name_taken

    def pausing(session: Any, user_id: int, name: str, *, exclude_id: int | None) -> bool:
        if (
            name == "A 필터"
        ):  # A는 잠금·개수 확인을 마친 뒤 여기서 B가 잠금 대기에 들어갈 때까지 멈춘다
            a_locked.set()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with owner_engine.connect() as connection:
                    waiting = connection.execute(
                        text(
                            "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
                            " WHERE NOT l.granted AND a.datname = current_database()"
                        )
                    ).scalar_one()
                if waiting:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("B가 잠금 대기에 들어가지 않았다")
        return original(session, user_id, name, exclude_id=exclude_id)

    monkeypatch.setattr(saved_service, "_name_taken", pausing)
    results: dict[str, Any] = {}

    def run(label: str) -> None:
        if label == "B":
            assert a_locked.wait(15)
        try:
            results[label] = saved_service.create_saved_filter(
                actor=user,
                idempotency_key=f"det-{label}",
                name=f"{label} 필터",
                filter_config=BoardFilter(),
            )
        except Exception as exc:  # 결과로 판정한다
            results[label] = exc

    threads = [threading.Thread(target=run, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert results["A"][0] == 201
    assert str(getattr(results["B"], "code", "")) == "ORDER_BOARD.FILTER.LIMIT_REACHED", results[
        "B"
    ]
    with owner_engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT count(*) FROM board_saved_filters WHERE user_id = :u AND deleted_at IS NULL"
            ),
            {"u": user.id},
        ).scalar_one()
    assert active == SAVED_FILTER_LIMIT


@pytest.mark.group_j
def test_concurrent_creates_with_the_same_name_land_one_and_translate_the_race_to_409() -> None:
    """같은 이름 동시 저장 4건 — 1건만 저장되고 나머지는 500이 아니라 409 DUPLICATE_NAME(선조회를 빠져나간 경합은 제약명 번역)"""
    user = make_user(RoleCode.TRADE)

    def worker(index: int) -> Any:
        return saved_service.create_saved_filter(
            actor=user,
            idempotency_key=f"same-{index}",
            name="같은 이름",
            filter_config=BoardFilter(),
        )

    outcomes = run_concurrently(worker, workers=4)
    assert sum(1 for o in outcomes if o.ok) == 1
    for o in outcomes:
        if not o.ok:
            assert str(getattr(o.error, "code", "")) == "ORDER_BOARD.FILTER.DUPLICATE_NAME", o.error
