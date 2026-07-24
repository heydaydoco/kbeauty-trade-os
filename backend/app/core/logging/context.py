"""요청 단위 컨텍스트 — 로그 한 줄만 봐도 어느 요청인지 알 수 있게 한다.

user_id·role은 S0-2(로그인)에서 값이 채워진다. 지금 자리를 잡아 두는 이유는
그때 접근 로그 스키마가 바뀌지 않게 하기 위해서다.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)
user_role_var: ContextVar[str | None] = ContextVar("user_role", default=None)

# ★ 쿼리 카운터는 ContextVar에 정수를 "다시 넣는" 방식으로 만들면 안 된다.
#   동기 def 엔드포인트는 Starlette가 스레드풀에서 돌리는데, 그 스레드는
#   컨텍스트의 *복사본*을 받는다. 복사본에 새 값을 set 해도 바깥에서는 보이지
#   않아 접근 로그에 항상 0이 찍힌다. 가변 dict를 넣고 제자리에서 증가시키면
#   같은 객체를 공유하므로 바깥에서도 보인다.
_query_state: ContextVar[dict[str, int] | None] = ContextVar("query_state", default=None)


def start_query_counter() -> dict[str, int]:
    state = {"count": 0}
    _query_state.set(state)
    return state


def bump_query_count() -> None:
    state = _query_state.get()
    if state is not None:
        state["count"] += 1


def current_query_count() -> int:
    state = _query_state.get()
    return state["count"] if state else 0


def bind_request(request_id: str) -> None:
    request_id_var.set(request_id)


def add_request_context(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog 프로세서 — 요청 식별자를 모든 로그 줄에 붙인다."""
    request_id = request_id_var.get()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    user_id = user_id_var.get()
    if user_id is not None:
        event_dict.setdefault("user_id", user_id)
    role = user_role_var.get()
    if role is not None:
        event_dict.setdefault("role", role)
    return event_dict
