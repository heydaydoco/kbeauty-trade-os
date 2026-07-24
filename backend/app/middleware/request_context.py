"""요청 식별자 미들웨어.

BaseHTTPMiddleware가 아니라 순수 ASGI로 짠다. BaseHTTPMiddleware는 내부적으로
별도 태스크에서 앱을 실행해 ContextVar 전파가 어긋나는 경우가 있는데, 그러면
로그의 request_id가 조용히 비고 원인 추적이 불가능해진다.
"""

from __future__ import annotations

from secrets import token_hex

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging.context import request_id_var, start_query_counter

REQUEST_ID_HEADER = "X-Request-ID"

# 32자리 hex — 나중에 분산 추적(trace_id)을 붙이더라도 형식이 호환된다.
_ID_BYTES = 16


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, trust_incoming: bool = False) -> None:
        self.app = app
        self.trust_incoming = trust_incoming

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._resolve_request_id(scope)
        token = request_id_var.set(request_id)
        start_query_counter()

        # scope state에도 심는다 — 500 응답을 만드는 Starlette ServerErrorMiddleware는
        # 이 미들웨어보다 바깥이라 그때는 contextvar가 이미 reset돼 있다. 핸들러는
        # request.state.request_id로 항상 읽을 수 있어야 한다.
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)

    def _resolve_request_id(self, scope: Scope) -> str:
        if self.trust_incoming:
            # 신뢰할 수 있는 프록시 뒤에서만 켠다. 그렇지 않으면 외부가
            # 임의의 값을 보내 로그를 오염시킬 수 있다.
            headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
            for key, value in headers:
                if key.decode("latin-1").lower() == REQUEST_ID_HEADER.lower():
                    candidate: str = value.decode("latin-1").strip()[:64]
                    if candidate:
                        return candidate
        return token_hex(_ID_BYTES)
