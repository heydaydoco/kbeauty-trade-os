"""K. 보안·품질 — 인증 커버리지 (DESIGN.md §18.1 "인가는 API에서 강제").

§18.1은 **모든** 엔드포인트에 역할·소유권 검증을 요구한다. 사람이 라우터를
만들 때마다 의존성을 기억해서 붙이는 방식은 반드시 새고, 새어 나간 경로는
아무 증상 없이 공개된다 — 누가 URL을 알아내기 전까지는.

★ 의존성 트리를 들여다보지 않고 **실제로 요청을 보내서** 확인한다.
  FastAPI가 include_router 결과를 내부 래퍼로 감싸기 시작하면서 라우트 목록을
  구조로 훑는 방법이 버전마다 달라졌다. 그런 검사는 업그레이드 때 "보안 규칙이
  깨진 것처럼" 실패하고, 그러면 사람들은 규칙이 아니라 검사를 지운다.
  비인증 요청에 401이 오는지는 프레임워크 버전과 무관한 사실이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.public import PUBLIC_PATHS, is_public
from app.main import app

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

_PATH_PARAM = re.compile(r"\{[^}]+\}")

#: 인증 검사에서 제외하는 메서드 — 본문 스키마와 무관하게 프레임워크가 처리한다.
_PROBE_METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _declared_operations(target: FastAPI = app) -> list[tuple[str, str]]:
    """OpenAPI 스키마에서 (메서드, 경로) 전건. 앱이 실제로 노출하는 목록이다."""
    schema = target.openapi()
    return [
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in _PROBE_METHODS
    ]


def _probe_path(path: str) -> str:
    """경로 파라미터를 아무 값으로 채운다 — 인증은 자원 존재 여부보다 먼저다."""
    return _PATH_PARAM.sub("1", path)


def test_scan_is_not_vacuous() -> None:
    """검사할 엔드포인트가 실제로 존재한다 (빈 목록으로 초록을 사지 않는다)"""
    assert len(_declared_operations()) >= 5


def _leaked_operations(probe_client: TestClient, operations: list[tuple[str, str]]) -> list[str]:
    """비인증 요청에 401을 주지 않는 비공개 경로들."""
    leaked: list[str] = []
    for method, path in operations:
        if is_public(path):
            continue
        response = probe_client.request(method, _probe_path(path), json={})
        if response.status_code != 401:
            leaked.append(f"{method} {path} → {response.status_code}")
    return leaked


def test_detector_catches_an_unprotected_route() -> None:
    """검사기가 실제로 구멍을 잡는다

    ★ 이 자기검사가 없으면, 검사기가 조용히 고장 났을 때(예: 라우트를 하나도
      못 찾을 때) 아래 커버리지 테스트가 영원히 초록이 된다. 그건 보안 검사가
      없는 것보다 나쁘다 — 있다고 믿게 만들기 때문이다.
    """
    leaky_app = FastAPI()

    @leaky_app.get("/api/v1/leaky")
    def leaky() -> dict[str, str]:
        return {"exposed": "yes"}

    with TestClient(leaky_app) as leaky_client:
        leaked = _leaked_operations(leaky_client, _declared_operations(leaky_app))

    assert leaked == ["GET /api/v1/leaky → 200"]


def test_every_endpoint_is_authenticated_or_explicitly_public(client: TestClient) -> None:
    """공개 목록에 없는 모든 엔드포인트는 비인증 요청에 401을 준다"""
    leaked = _leaked_operations(client, _declared_operations())

    assert not leaked, (
        "인증 없이 응답하는 경로: "
        + ", ".join(sorted(leaked))
        + "\nCurrentUser(또는 require_roles/AdminUser) 의존성을 붙이거나, 의도한 "
        "공개라면 app/api/public.py의 PUBLIC_PATHS에 사유와 함께 추가하세요."
    )


def test_public_endpoints_really_answer_without_a_session(client: TestClient) -> None:
    """공개로 선언한 경로는 실제로 로그인 없이 응답한다 (목록이 사문화되지 않는다)"""
    unreachable = [
        f"{method} {path} → {client.request(method, _probe_path(path), json={}).status_code}"
        for method, path in _declared_operations()
        if is_public(path) and client.request(method, _probe_path(path), json={}).status_code == 401
    ]
    assert not unreachable, "공개 목록에 있는데 401을 주는 경로: " + ", ".join(unreachable)


def test_public_list_has_no_dead_entries() -> None:
    """공개 목록에 실재하지 않는 경로가 남아 있지 않다

    사라진 경로가 목록에 남으면, 나중에 같은 경로가 다른 의미로 생겼을 때
    조용히 공개된다.
    """
    live = {path for _, path in _declared_operations()}
    stale = PUBLIC_PATHS - live
    assert not stale, f"존재하지 않는 경로가 공개 목록에 있다: {sorted(stale)}"


def test_health_endpoints_stay_public() -> None:
    """헬스체크는 공개다 — 401을 주면 컨테이너가 무한 재시작한다"""
    assert is_public("/api/v1/system/healthz")
    assert is_public("/api/v1/system/readyz")


def test_no_endpoint_returns_a_bare_list() -> None:
    """어떤 엔드포인트도 배열을 그대로 돌려주지 않는다 (§18.4 무페이지네이션 금지)

    맨 배열을 한 번 허용하면 그 화면은 데이터가 쌓인 뒤에야 문제가 되고,
    그때는 이미 클라이언트가 그 모양에 의존하고 있다.
    """
    schema = app.openapi()
    offenders: list[str] = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in _PROBE_METHODS:
                continue
            content = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
            )
            if content.get("schema", {}).get("type") == "array":
                offenders.append(f"{method.upper()} {path}")
    assert not offenders, (
        "배열을 그대로 반환하는 엔드포인트: "
        + ", ".join(offenders)
        + " — app.core.pagination.Page.of()로 감싸세요(기본 50건)."
    )


def test_list_endpoints_use_the_page_envelope() -> None:
    """목록(operationId가 list_로 시작) 응답은 Page 봉투다"""
    schema = app.openapi()
    offenders: list[str] = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in _PROBE_METHODS:
                continue
            if not str(operation.get("operationId", "")).startswith("list_"):
                continue
            ref = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
                .get("$ref", "")
            )
            if "Page" not in ref:
                offenders.append(f"{method.upper()} {path} → {ref or '(스키마 없음)'}")
    assert not offenders, "목록 엔드포인트가 Page 봉투를 쓰지 않는다: " + ", ".join(offenders)
