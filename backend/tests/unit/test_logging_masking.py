"""K. 보안·품질 — 로그 마스킹 (DESIGN.md §18.1, §20 K).

★ 검증은 반드시 **최종 렌더 문자열** 기준으로 한다.
  structlog.testing.capture_logs()를 쓰면 프로세서 체인이 통째로 교체돼
  redact_processor가 아예 실행되지 않는다 — "초록인데 평문이 나간다"는
  가장 위험한 거짓 안심이다. 그래서 실제 렌더러를 붙인 로거로 문자열을 뽑는다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
import structlog

from app.core.logging.processors import shared_processors
from app.core.logging.redaction import (
    clear_registered_secrets,
    is_sensitive_key,
    normalize_key,
    register_secret_values,
    scrub_text,
)

pytestmark = pytest.mark.group_k


@pytest.fixture
def rendered() -> Iterator[list[str]]:
    """등록한 시크릿을 테스트마다 초기화하기 위한 픽스처.

    실제 렌더는 _emit()이 프로세서 체인을 직접 돌려서 만든다 — structlog 전역
    설정을 건드리지 않아 다른 테스트에 영향을 주지 않는다.
    """
    lines: list[str] = []
    yield lines
    clear_registered_secrets()


def _emit(rendered: list[str], **kw: object) -> str:
    """운영과 동일한 프로세서 체인 + JSON 렌더러로 로그 한 줄을 만든다.

    ★ capture_logs()를 쓰지 않는 이유가 여기 있다 — 그 헬퍼는 체인을 갈아치워
    마스킹을 건너뛴다. 여기서는 shared_processors()를 그대로 태운다.
    """
    # add_logger_name이 logger.name을 읽으므로 실제 stdlib 로거를 넘긴다.
    logger = logging.getLogger("test.masking")
    chain = [*shared_processors(), structlog.processors.JSONRenderer(ensure_ascii=False)]
    result: object = {"event": "t", **kw}
    for proc in chain:
        result = proc(logger, "info", result)  # type: ignore[assignment]
    rendered.append(result)  # type: ignore[arg-type]
    return result  # type: ignore[return-value]


# ── 값 기준 ────────────────────────────────────────────────────────────────


def test_actual_secret_values_never_appear(rendered: list[str]) -> None:
    """기동 시 등록한 진짜 비밀 값은 어떤 문자열에 섞여 있어도 사라진다"""
    register_secret_values(["supersecretpassword123", "prod_secret_key_abcdefgh"])
    line = _emit(
        rendered,
        note="접속: postgresql+psycopg://kbos_app:supersecretpassword123@db:5432/kbos",
        memo="키는 prod_secret_key_abcdefgh 입니다",
    )
    assert "supersecretpassword123" not in line
    assert "prod_secret_key_abcdefgh" not in line


def test_dsn_password_is_masked_but_host_kept(rendered: list[str]) -> None:
    """접속 문자열은 비밀번호만 가리고 호스트·DB명은 남긴다(진단 가능성)"""
    line = _emit(rendered, dsn_note="postgresql+psycopg://kbos_app:rawpassword@db:5432/kbos_dev")
    assert "rawpassword" not in line
    assert "db:5432/kbos_dev" in line


def test_token_patterns_are_masked(rendered: list[str]) -> None:
    """API 키·JWT·Bearer 토큰은 값 패턴만으로도 가려진다"""
    line = _emit(
        rendered,
        msg="사용 키 sk-ant-abc123456789xyz 와 gh 토큰 ghp_ABCDEFGHIJKLMNOP1234",
        jwt="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturepart",
        auth="Bearer abcdef0123456789token",
    )
    for leaked in ("sk-ant-abc", "ghp_ABCDEF", "eyJhbGci", "abcdef0123456789token"):
        assert leaked not in line, f"{leaked!r}가 마스킹되지 않았다"


# ── 키 기준 ────────────────────────────────────────────────────────────────


def test_sensitive_keys_masked_including_camel_and_nested(rendered: list[str]) -> None:
    """민감 키는 camelCase·중첩·리스트 안에서도 값이 가려진다"""
    line = _emit(
        rendered,
        password="p@ss",
        apiKey="secretkey",
        accessToken="tok",
        cost=12345,
        nested={"inner": [{"account_no": "110-222-333", "token": "deeptoken"}]},
    )
    for leaked in ("p@ss", "secretkey", '"tok"', "12345", "110-222-333", "deeptoken"):
        assert leaked not in line, f"{leaked!r}가 노출됐다"


def test_non_sensitive_keys_are_preserved(rendered: list[str]) -> None:
    """비밀이 아닌 키는 가리지 않는다 (과잉 마스킹은 진단을 막는다)"""
    line = _emit(
        rendered,
        idempotency_key="idem-42",
        cost_center="CC-01",
        natural_key="SKU-001",
        request_id="abc123",
    )
    assert "idem-42" in line
    assert "CC-01" in line
    assert "SKU-001" in line


# ── 키 정규화 단위 검증 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("apiKey", "api_key"),
        ("API-KEY", "api_key"),
        ("accessToken", "access_token"),
        ("_password", "password"),
        ("postgresPassword", "postgres_password"),
    ],
)
def test_key_normalization(raw: str, expected: str) -> None:
    """카멜·하이픈·대문자 키가 스네이크로 정규화된다"""
    assert normalize_key(raw) == expected


@pytest.mark.parametrize(
    "key", ["password", "apiKey", "secret_key", "user_password", "unit_cost", "authorization"]
)
def test_recognized_as_sensitive(key: str) -> None:
    """대표적인 민감 키들이 민감으로 인식된다"""
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["idempotency_key", "natural_key", "request_id", "sku_code"])
def test_recognized_as_safe(key: str) -> None:
    """디버깅에 필요한 키들은 민감으로 잡히지 않는다"""
    assert not is_sensitive_key(key)


# ── 프로세서 순서 (메타) ─────────────────────────────────────────────────────


@pytest.mark.meta
def test_redact_runs_after_exc_info_and_last() -> None:
    """마스킹은 예외 펼침 뒤·렌더러 직전에 위치한다 (순서가 뒤집히면 트레이스백이 샌다)"""
    from app.core.logging import processors as p
    from app.core.logging.context import add_request_context
    from app.core.logging.redaction import redact_processor

    chain = shared_processors()
    exc_idx = chain.index(structlog.processors.format_exc_info)
    redact_idx = chain.index(redact_processor)

    assert redact_idx > exc_idx, "redact가 format_exc_info보다 앞이면 트레이스백 속 비밀이 샌다"
    assert redact_idx == len(chain) - 1, "redact는 체인의 마지막(렌더러 직전)이어야 한다"
    assert chain.index(add_request_context) < redact_idx
    assert p  # 임포트 유지


@pytest.mark.meta
def test_exception_traceback_password_is_masked(rendered: list[str]) -> None:
    """예외 트레이스백에 섞인 접속 문자열 비밀번호가 마스킹된다"""
    try:
        raise RuntimeError("fail at postgresql+psycopg://kbos_owner:tracebackpw999@db:5432/kbos")
    except RuntimeError:
        import sys

        line = _emit(rendered, event="boom", exc_info=sys.exc_info())
    assert "tracebackpw999" not in line
    assert "Traceback" in line


def test_purchase_price_keys_are_masked() -> None:
    """매입가(원가) 키는 로그에서도 가려진다 (ADR-0018 / §18.1)

    화면에서 못 보는 값이 로그에 평문이면 §18.1의 마스킹 통제가 그대로 우회된다.
    ★ `amount`·`price`를 통째로 가리지는 않는다 — 판가·전표 금액은 원가가
      아니고, 그것까지 가리면 진단이 불가능해진다.
    """
    assert is_sensitive_key("purchase_price")
    assert is_sensitive_key("purchase_amount")
    assert is_sensitive_key("purchaseAmount")  # camelCase도 정규화해서 잡는다
    assert not is_sensitive_key("amount")
    assert not is_sensitive_key("sales_price")


def test_postgres_failing_row_detail_is_scrubbed() -> None:
    """PostgreSQL의 "Failing row contains (…)"는 행 전체 값이라 지운다

    hide_parameters로는 막히지 않는 경로다(서버가 만든 문자열). 제약 이름은
    남아야 진단이 되므로 그 줄만 자른다.
    """
    raw = (
        'new row for relation "sku_prices" violates check constraint '
        '"ck_sku_prices_price_type_valid"\n'
        "DETAIL:  Failing row contains (1, 1, BOGUS, KRW, 7654321, 2026-01-01, null).\n"
        "[SQL: INSERT INTO sku_prices ...]"
    )

    cleaned = scrub_text(raw)

    assert "7654321" not in cleaned
    assert "BOGUS" not in cleaned
    assert "ck_sku_prices_price_type_valid" in cleaned
    assert "INSERT INTO sku_prices" in cleaned
