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
        # 매입가는 원가다(ADR-0018). 조회 역할이 화면에서 볼 수 없는 값이라
        # 로그에도 남으면 안 된다. ★ `amount`·`price`를 통째로 넣지는 않는다 —
        # 판가·전표 금액은 원가가 아니고, 그것까지 가리면 진단이 불가능해진다.
        "purchase_price",
        "purchase_amount",
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

# PostgreSQL이 CHECK·NOT NULL 위반 시 DETAIL에 실어 보내는 "Failing row contains (…)".
#
# ★ **행 전체 값**이다 — 매입가도 비밀번호 해시도 그대로 들어 있다. 이건 서버가
#   만든 문자열이라 SQLAlchemy의 hide_parameters로는 막히지 않는다(그건 우리가
#   보낸 바인드 값만 가린다). 실측으로 확인한 유출 경로다:
#       DETAIL:  Failing row contains (1, 1, BOGUS, KRW, 7654321, 2026-01-01, …)
_PG_FAILING_ROW_START = "Failing row contains ("

# SQLAlchemy가 그 뒤에 붙이는 꼬리표들. 여기까지가 지울 구간이고, 이 뒤(문장·
# 배경 링크)는 진단에 필요해서 남긴다.
_SQLALCHEMY_TAIL_MARKERS = ("\n[SQL:", "\n[SQL parameters", "\n(Background on this error")

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


def _scrub_failing_row(text: str) -> str:
    """PostgreSQL의 "Failing row contains (…)" 구간을 통째로 지운다.

    ★ 줄 끝으로 자르면 안 된다. PostgreSQL은 값 안의 **개행을 그대로 싣는다**
      — 실측: `Failing row contains (…, 1234, …, 메모\\nTAILSECRET…, …)`.
      줄 끝 절단은 개행 앞만 지우고 뒤를 남겼다. 괄호 짝으로 자르는 것도 같은
      이유(값 안의 괄호)로 안 된다.

      그래서 **여는 괄호부터 SQLAlchemy 꼬리표 앞까지**를 통째로 지운다.
      제약 이름은 앞 줄에, 실패한 문장은 `[SQL: …]`에 남으므로 진단은 유지된다.

    ★ 꼬리표는 **위조될 수 있다**. 사용자가 메모에 `"\\n[SQL:"`을 그대로 적으면
      절단이 거기서 멈추고 뒤가 남는다 — 실측으로 확인했다. 그래서 두 겹으로 막는다.

        ① 꼬리표는 **마지막** 등장 위치로 찾는다. 값은 DETAIL 안에 있고 진짜
           꼬리표는 그 뒤에 오므로, 마지막 것이 진짜다.
        ② 그렇게 정한 삭제 구간 안에 **또 꼬리표가 보이면** 위조가 섞인
           것이므로 **메시지 끝까지** 지운다. 문장을 잃더라도 값을 남기지 않는다
           — 과잉 삭제가 유출보다 낫다(fail-closed).
    """
    start = text.find(_PG_FAILING_ROW_START)
    if start < 0:
        return text

    tail_at = len(text)
    for marker in _SQLALCHEMY_TAIL_MARKERS:
        found = text.rfind(marker)
        if found > start:
            tail_at = min(tail_at, found)

    removed = text[start:tail_at]
    if any(marker in removed for marker in _SQLALCHEMY_TAIL_MARKERS):
        tail_at = len(text)

    return f"{text[:start]}Failing row contains {REPLACEMENT}{text[tail_at:]}"


def scrub_text(text: str) -> str:
    """문자열 안의 비밀을 지운다 (예외 트레이스백·SQL·URL 포함)."""
    cleaned = _DSN_PASSWORD.sub(rf"\g<head>{REPLACEMENT}\g<tail>", text)
    cleaned = _scrub_failing_row(cleaned)
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
