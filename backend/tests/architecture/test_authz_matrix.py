"""K. 역할 권한 매트릭스 — 표(authz_matrix.EXPECTED)와 실제 라우트가 일치한다 (ADR-0067).

① 완비성: 통제 대상 접두어 아래 모든 (메서드, 경로)가 표에 있고 표에 죽은 행이 없다.
② 프로브: 표의 각 행을 5개 역할로 실제 호출해 403/통과가 표와 같은지 확인한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.models import RoleCode
from tests.architecture.authz_matrix import ALLOW, DENY, EXPECTED, GOVERNED_PREFIXES
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_k


def _operations() -> set[tuple[str, str]]:
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in ("get", "post", "put", "patch", "delete")
    }


def test_matrix_is_not_vacuous() -> None:
    """표가 비어 있지 않고 모든 행이 5역할을 다 갖는다"""
    assert EXPECTED
    for row, roles in EXPECTED.items():
        assert set(roles) == set(RoleCode), f"{row}: 5역할 전부의 기대가 있어야 한다"
        assert set(roles.values()) <= {ALLOW, DENY}


def test_every_governed_operation_is_in_the_matrix_and_no_row_is_dead() -> None:
    """통제 접두어 아래 라우트는 표에 있고, 표의 행은 실제 라우트다"""
    live = _operations()
    governed = {op for op in live if op[1].startswith(GOVERNED_PREFIXES)}
    assert governed - set(EXPECTED) == set(), (
        f"표에 없는 라우트: {sorted(governed - set(EXPECTED))}"
    )
    assert set(EXPECTED) <= live, f"실제 라우트가 아닌 행: {sorted(set(EXPECTED) - live)}"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED))
def test_probe_each_role(client: TestClient, method: str, path: str) -> None:
    """표의 (메서드, 경로) × 5역할 — 라우트 게이트가 표대로 동작한다"""
    for role, expected in EXPECTED[(method, path)].items():
        email = f"authz-{role.value.lower()}@x.kbos"
        create_user(email, roles=(role,))
        client.cookies.clear()
        assert client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        ).is_success
        status = client.request(method, path).status_code
        if expected == DENY:
            assert status == 403, f"{role} {method} {path}: 403이어야 하는데 {status}"
        else:
            assert status not in (401, 403), f"{role} {method} {path}: 통과해야 하는데 {status}"
