"""J. 안전 계약 — Idempotency-Key 형식 검사(P-39 = PR-16 ⑨ 해소, S3-3 PR-1b · sC CJ-18).

키는 1~128자(DB `idempotency_keys.idempotency_key String(128)`과 같은 상한)이고 보이지 않는 글자(제어·서식·줄/문단 구분·한글
채움)를 담지 않는다. 어기면 **DB에 닿기 전에** 422 `COMMON.IDEMPOTENCY.KEY_INVALID` — 검사가 없던 때는 129자 키가 22001로 500이 됐다.
HTTP 표면(실제 키 필수 엔드포인트 `POST /tasks`)과 의존성 함수 단위 양쪽에서 단언한다. 이 파일은 KST '오늘'을 읽지 않는다
(시각 무관 — 키 형식만).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import String, text

from app.api.deps import get_idempotency_key
from app.core.db.session import engine
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.main import app
from app.modules.idempotency.models import IdempotencyKey
from app.modules.idempotency.service import IDEMPOTENCY_KEY_MAX_LENGTH
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.group_j

LOGIN = "/api/v1/auth/login"
TASKS = "/api/v1/tasks"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        create_user("trade@example.com", roles=(RoleCode.TRADE,))
        login = test_client.post(
            LOGIN, json={"email": "trade@example.com", "password": DEFAULT_PASSWORD}
        )
        assert login.status_code == 200, login.text
        yield test_client


def _create(client: TestClient, key: str) -> Response:
    """키를 latin-1 바이트로 싣는다 — 서버(Starlette)가 헤더를 latin-1로 푸는 것과 같은 왕복이라 C1(0x80~0x9F)도 그대로 닿는다."""
    return client.post(
        TASKS, json={"title": "키 형식"}, headers={b"Idempotency-Key": key.encode("latin-1")}
    )


def _count(sql: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(sql)).scalar_one())


def _code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def test_the_key_limit_matches_the_db_column() -> None:
    """입구 상한 = DB 열 길이(128) — 둘이 갈라지면 입구를 통과한 키가 다시 22001 500이 된다"""
    column = IdempotencyKey.__table__.c.idempotency_key
    assert isinstance(column.type, String)
    assert IDEMPOTENCY_KEY_MAX_LENGTH == column.type.length == 128


def test_a_128_char_key_is_accepted_and_stored(client: TestClient) -> None:
    """CJ-18 ② 128자 = 정상 — 201, 멱등 행이 그 키 그대로 1건, 같은 키 재요청은 재생(태스크 1건)"""
    key = "k" * 128
    first = _create(client, key)
    second = _create(client, key)
    assert first.status_code == second.status_code == 201, first.text
    assert first.json() == second.json()
    assert _count("SELECT count(*) FROM tasks") == 1
    assert _count("SELECT count(*) FROM idempotency_keys WHERE length(idempotency_key) = 128") == 1


def test_a_129_char_key_is_rejected_before_the_db(client: TestClient) -> None:
    """CJ-18 ① 129자 = 422 KEY_INVALID(500 아님) — 태스크·멱등 행 0(입구에서 막힘)"""
    response = _create(client, "k" * 129)
    assert response.status_code == 422, response.text
    assert (
        _code(response)
        == "COMMON.IDEMPOTENCY.KEY_INVALID"
        == str(ErrorCode.IDEMPOTENCY_KEY_INVALID)
    )
    assert _count("SELECT count(*) FROM tasks") == 0
    assert _count("SELECT count(*) FROM idempotency_keys") == 0


@pytest.mark.parametrize(
    "key",
    ["abc\tdef", "abc\x01def", "abc\x7fdef", "abc\x85def"],
    ids=["tab", "c0-soh", "del", "c1-nel"],
)
def test_a_key_with_a_control_char_is_rejected_over_http(client: TestClient, key: str) -> None:
    """CJ-18 ③ 제어문자 = 422 KEY_INVALID — HTTP 헤더로 실제로 실려 오는 제어문자(탭·C0·DEL·C1[latin-1 디코딩])"""
    response = _create(client, key)
    assert response.status_code == 422, response.text
    assert _code(response) == "COMMON.IDEMPOTENCY.KEY_INVALID"
    assert _count("SELECT count(*) FROM tasks") == 0


def test_a_missing_or_empty_key_stays_required_400(client: TestClient) -> None:
    """회귀 — 키 없음·빈 값은 종전대로 400 KEY_REQUIRED(형식 검사가 '없음'을 422로 바꾸지 않는다)"""
    missing = client.post(TASKS, json={"title": "키 없음"})
    empty = _create(client, "")
    assert missing.status_code == empty.status_code == 400
    assert _code(missing) == _code(empty) == "COMMON.IDEMPOTENCY.KEY_REQUIRED"


@pytest.mark.parametrize(
    "key",
    [
        "a\x00b",  # NUL — DB에 닿으면 500
        "a\nb",
        "a\rb",
        "a​b",  # 제로폭 공백(Cf)
        "a‮b",  # 방향 뒤집기(Cf)
        "a b",  # 줄 구분(Zl)
        "a b",  # 문단 구분(Zp)
        "aㅤb",  # 한글 채움
        "x" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1),
        "가" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1),
    ],
    ids=["nul", "lf", "cr", "zwsp", "rlo", "zl", "zp", "hangul-filler", "ascii-129", "hangul-129"],
)
def test_the_dependency_rejects_every_invalid_key(key: str) -> None:
    """의존성 단위 — HTTP 클라이언트가 실어 보내지 못하는 글자(NUL·줄바꿈)와 서식·구분·채움 문자도 422 KEY_INVALID"""
    with pytest.raises(AppError) as caught:
        get_idempotency_key(key)
    assert caught.value.code is ErrorCode.IDEMPOTENCY_KEY_INVALID


@pytest.mark.parametrize(
    "key",
    [
        "1",
        "0b6f3c1e-7a8d-4c2b-9e1f-2a3b4c5d6e7f",
        "a b",
        "가" * IDEMPOTENCY_KEY_MAX_LENGTH,
        "k" * 128,
    ],
    ids=["one-char", "uuid", "inner-space", "hangul-128", "ascii-128"],
)
def test_the_dependency_accepts_valid_keys(key: str) -> None:
    """의존성 단위 — 1자·UUID·가운데 공백·128자(글자 수 기준)는 통과하고 원문 그대로 돌려준다"""
    assert get_idempotency_key(key) == key
