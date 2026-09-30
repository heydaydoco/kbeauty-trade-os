"""A·H·J·K. 정책 저장소 — 미설정=fail-closed, 사유 필수·audit·outbox, 낙관 잠금, 동시 최초 생성 (ADR-0065)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.identity.models import RoleCode
from app.modules.policies import service
from app.modules.policies.service import get_policy
from tests.support.concurrency import run_concurrently
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_h

POLICIES = "/api/v1/policies"
MODE = "pi_advance_gate_mode"
TOL = "price_deviation_tolerance_bp"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        ).is_success
        yield client


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from _client("pol-admin@example.com", RoleCode.ADMIN)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("pol-trade@example.com", RoleCode.TRADE)


def _put(
    client: TestClient,
    key: str,
    value: Any,
    *,
    version: int | None,
    reason: str = "운영 개시 설정",
    idem: str,
) -> Any:
    return client.put(
        f"{POLICIES}/{key}",
        json={"value": value, "version": version, "reason": reason},
        headers={"Idempotency-Key": idem},
    )


def _by_key(client: TestClient) -> dict[str, dict[str, Any]]:
    body = client.get(POLICIES).json()
    return {item["key"]: item for item in body["items"]}


def test_unset_policies_are_strict_and_visible(admin: TestClient) -> None:
    """행이 없으면 가장 엄격한 동작(모드=BLOCK·허용치=0)이고 출처가 UNSET_DEFAULT로 드러난다"""
    items = _by_key(admin)
    assert items[MODE]["value"] == "BLOCK" and items[MODE]["source"] == "UNSET_DEFAULT"
    assert items[TOL]["value"] == 0 and items[TOL]["source"] == "UNSET_DEFAULT"
    assert items[MODE]["version"] is None
    with unit_of_work() as uow:
        assert (get_policy(uow.session, MODE).value, get_policy(uow.session, MODE).source) == (
            "BLOCK",
            "UNSET_DEFAULT",
        )


def test_saving_makes_the_value_effective_immediately_with_audit_and_outbox(
    admin: TestClient,
) -> None:
    """저장 → 즉시 SET·이후 조회 반영, audit 1행(old·new·reason)·outbox 1건"""
    saved = _put(admin, MODE, "WARN", version=None, reason="시범 운영은 경고로", idem="p1")
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"policy_key": MODE, "value": "WARN", "source": "SET", "version": 1}
    assert _by_key(admin)[MODE]["source"] == "SET"
    with unit_of_work() as uow:
        assert get_policy(uow.session, MODE).value == "WARN"
    with engine.connect() as conn:
        audit = (
            conn.execute(text("SELECT detail FROM audit_log WHERE action='policy.update'"))
            .scalars()
            .all()
        )
        events = conn.execute(
            text("SELECT count(*) FROM events WHERE event_type='policies.policy.changed'")
        ).scalar_one()
    assert len(audit) == 1 and audit[0] == {
        "key": MODE,
        "old": "BLOCK",
        "new": "WARN",
        "old_source": "UNSET_DEFAULT",
        "reason": "시범 운영은 경고로",
    }
    assert events == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [(MODE, "ALLOW"), (MODE, 5), (TOL, 10001), (TOL, -1), (TOL, "5"), (TOL, True)],
)
def test_invalid_values_are_422(admin: TestClient, key: str, value: Any) -> None:
    """허용되지 않는 값(모드 밖 문자열·형 교차·범위 밖·불리언)은 422 INVALID_VALUE"""
    response = _put(admin, key, value, version=None, idem=f"bad-{key}-{value}")
    assert response.status_code == 422, response.text
    # 불리언·형 교차는 요청 스키마(엄격 형)가, 범위·허용 밖 값은 서비스가 거부한다 — 둘 다 422.
    assert response.json()["error"]["code"] in (
        "POLICIES.POLICY.INVALID_VALUE",
        "COMMON.VALIDATION.INVALID_FIELD",
    )


def test_unknown_key_and_missing_reason(admin: TestClient) -> None:
    """알 수 없는 키 404, 사유가 없거나 1자면 422"""
    assert _put(admin, "nope", "BLOCK", version=None, idem="u1").status_code == 404
    assert _put(admin, MODE, "WARN", version=None, reason="", idem="u2").status_code == 422
    assert _put(admin, MODE, "WARN", version=None, reason="x", idem="u3").status_code == 422
    assert _put(admin, MODE, "WARN", version=None, reason="x" * 301, idem="u4").status_code == 422


def test_only_admin_can_read_or_write(trader: TestClient) -> None:
    """무역 등 비관리자는 조회·저장 모두 403"""
    assert trader.get(POLICIES).status_code == 403
    assert _put(trader, MODE, "OFF", version=None, idem="t1").status_code == 403


def test_optimistic_lock_and_idempotent_replay(admin: TestClient) -> None:
    """version 불일치 409, 같은 키 재요청은 최초 결과 그대로, 행이 있는데 version=null도 409"""
    first = _put(admin, TOL, 500, version=None, idem="o1")
    assert first.status_code == 200 and first.json()["version"] == 1
    assert _put(admin, TOL, 500, version=None, idem="o1").json() == first.json()  # 재생
    assert _put(admin, TOL, 700, version=None, idem="o2").status_code == 409  # 이미 있는 행
    assert _put(admin, TOL, 700, version=99, idem="o3").status_code == 409
    ok = _put(admin, TOL, 700, version=1, idem="o4")
    assert ok.status_code == 200 and ok.json()["version"] == 2
    assert _by_key(admin)[TOL]["value"] == 700


def test_first_creation_race_yields_one_success_and_one_409() -> None:
    """동시 최초 생성 두 요청 → 하나 성공, 하나 409(500 아님)"""
    admin_id = create_user("pol-race@example.com", roles=(RoleCode.ADMIN,))
    from app.modules.identity.service import AuthenticatedUser

    actor = AuthenticatedUser(
        id=admin_id,
        email="pol-race@example.com",
        display_name="r",
        roles=frozenset({RoleCode.ADMIN}),
        session_id=0,
    )

    def worker(i: int) -> int:
        status, _ = service.update_policy(
            actor=actor,
            idempotency_key=f"race-{i}",
            policy_key=MODE,
            payload={"value": "OFF", "version": None, "reason": "동시 생성"},
        )
        return status

    outcomes = run_concurrently(worker, workers=2)
    ok = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]
    assert len(ok) == 1 and len(failed) == 1
    assert type(failed[0].error).__name__ == "VersionConflictError"
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM policy_settings WHERE policy_key=:k"), {"k": MODE}
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    ("key", "text_value", "int_value"),
    [
        (MODE, "ALLOW", None),
        (MODE, None, 5),
        (MODE, "WARN", 5),
        (TOL, None, 10001),
        (TOL, None, -1),
        (TOL, "OFF", 5),
        ("other", "WARN", None),
    ],
)
def test_check_constraints_reject_raw_inserts(
    key: str, text_value: str | None, int_value: int | None
) -> None:
    """서비스를 우회한 원시 INSERT도 DB CHECK가 거부한다(키 폐쇄·값 형 교차·범위)"""
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO policy_settings (policy_key, value_text, value_int) VALUES (:k, :t, :i)"
            ),
            {"k": key, "t": text_value, "i": int_value},
        )
