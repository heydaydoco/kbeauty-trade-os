"""로그 마스킹 (DESIGN.md §18.1, §20 K "로그 마스킹").

마스킹을 "호출부가 조심해서" 하면 반드시 새어 나간다. 여기 있는 프로세서가
**렌더링 직전에 전 이벤트를 훑는 마지막 관문**이고, 그 위치는 메타 테스트가 고정한다.

세 갈래로 막는다.
  ① 키 이름 — password, api_key, cost, margin ... (camelCase도 정규화해서 잡는다)
  ② 값 패턴 — 접속 문자열의 비밀번호, sk-ant-*, ghp_*, JWT, Bearer 토큰
  ③ 실제 비밀 값 — 기동 시 설정에서 읽은 진짜 시크릿 문자열을 등록해 두고,
     어떤 문자열 안에 들어 있든 치환한다. 예외 트레이스백처럼 우리가 형식을
     모르는 문자열까지 덮는 유일한 방법이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REPLACEMENT = "***"

#: 너무 깊은 구조는 로그를 못 읽게 만들고 마스킹 비용도 커진다.
MAX_DEPTH = 6
MAX_STRING_LENGTH = 2048
MAX_SEQUENCE_ITEMS = 200

#: 이름이 정확히 일치하면 값을 통째로 가린다.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "secret_key",
        "token",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "private_key",
        "credential",
        "credentials",
        "session_id",
        # 해시라고 안전한 게 아니다. 유출되면 오프라인 대입의 출발점이 되고,
        # 세션 토큰 해시는 그 자체가 DB의 세션 조회 키다(ADR-0013).
        "password_hash",
        "token_hash",
        "session_token",
        "otp",
        "pin",
        "cvv",
        "card_number",
        "account_no",
        "account_number",
        "resident_registration_number",
        "dsn",
        "database_url",
        "migration_database_url",
        # 원가·마진 — 조회 역할이 화면에서 볼 수 없는 값이 로그에는 평문으로
        # 남으면 §18.1의 마스킹 통제가 그대로 우회된다.
        "cost",
        "margin",
        "unit_cost",
        "landed_cost",
    }
)

#: 이름이 이걸로 끝나면 가린다. `_key`는 넣지 않는다 —
#: idempotency_key·natural_key처럼 비밀이 아닌데 디버깅에 꼭 필요한 값이 많다.
SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "_password",
    "_passwd",
    "_secret",
    "_secret_key",
    "_api_key",
    "_access_key",
    "_private_key",
    "_token",
    "_credential",
    "_dsn",
    "_cost",
    "_margin",
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# 접속 문자열의 비밀번호만 정확히 도려낸다(호스트·DB명은 남겨야 진단이 된다).
_DSN_PASSWORD = re.compile(
    r"(?P<head>[A-Za-z][A-Za-z0-9+.\-]*://[^:/@\s]+:)(?P<pw>[^@\s]+)(?P<tail>@)"
)

_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}"),
)

#: 기동 시 등록되는 "진짜" 비밀 문자열.
_known_secrets: set[str] = set()

#: 짧은 값을 등록하면 무관한 로그까지 별표로 뒤덮인다.
MIN_REGISTERED_SECRET_LENGTH = 8


def register_secret_values(values: str | Iterable[object]) -> None:
    """설정에서 읽은 실제 비밀 값을 마스킹 대상으로 등록한다."""
    items: Iterable[object] = [values] if isinstance(values, str) else values
    for value in items:
        text = str(value)
        if len(text) >= MIN_REGISTERED_SECRET_LENGTH:
            _known_secrets.add(text)
            # DSN을 통째로 등록했다면 그 안의 비밀번호 조각도 따로 등록한다.
            match = _DSN_PASSWORD.search(text)
            if match and len(match.group("pw")) >= MIN_REGISTERED_SECRET_LENGTH:
                _known_secrets.add(match.group("pw"))


def clear_registered_secrets() -> None:
    """테스트에서 등록 상태를 되돌린다."""
    _known_secrets.clear()


def normalize_key(key: object) -> str:
    """apiKey / API-KEY / _apiKey → api_key"""
    text = _CAMEL_BOUNDARY.sub("_", str(key))
    return text.replace("-", "_").lower().strip("_")


def is_sensitive_key(key: object) -> bool:
    normalized = normalize_key(key)
    return normalized in SENSITIVE_KEYS or normalized.endswith(SENSITIVE_SUFFIXES)


def scrub_text(text: str) -> str:
    """문자열 안의 비밀을 지운다 (예외 트레이스백·SQL·URL 포함)."""
    cleaned = _DSN_PASSWORD.sub(rf"\g<head>{REPLACEMENT}\g<tail>", text)
    for pattern in _VALUE_PATTERNS:
        cleaned = pattern.sub(REPLACEMENT, cleaned)
    for secret in _known_secrets:
        if secret in cleaned:
            cleaned = cleaned.replace(secret, REPLACEMENT)
    if len(cleaned) > MAX_STRING_LENGTH:
        cleaned = cleaned[:MAX_STRING_LENGTH] + f"…<{len(cleaned) - MAX_STRING_LENGTH}자 생략>"
    return cleaned


def scrub(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "<생략: 중첩 깊이 초과>"
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            key: (REPLACEMENT if is_sensitive_key(key) else scrub(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        items = list(value)
        trimmed = [scrub(item, depth + 1) for item in items[:MAX_SEQUENCE_ITEMS]]
        if len(items) > MAX_SEQUENCE_ITEMS:
            trimmed.append(f"<생략: {len(items) - MAX_SEQUENCE_ITEMS}개 더>")
        return trimmed
    if isinstance(value, bytes):
        return f"<bytes {len(value)}B>"
    if isinstance(value, int | float | bool | type(None)):
        return value
    return scrub_text(repr(value))


def redact_processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog 프로세서 — 렌더링 직전 마지막 관문.

    ★ 이 프로세서는 반드시 `format_exc_info` **뒤에** 있어야 한다.
      예외 정보가 아직 예외 객체 상태면 안을 들여다볼 수 없고, 트레이스백이
      문자열로 펼쳐진 뒤라야 그 안의 접속 문자열 비밀번호를 지울 수 있다.
      순서는 processors.py의 메타 테스트가 고정한다.
    """
    return {
        key: (REPLACEMENT if is_sensitive_key(key) else scrub(value))
        for key, value in event_dict.items()
    }
