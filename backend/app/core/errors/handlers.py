"""예외 → HTTP 응답 봉투.

모든 오류 응답의 모양은 하나다.

    {"error": {"code": "...", "message": "한국어", "detail": {...}, "request_id": "..."}}

`request_id`가 응답에 실리는 이유: 영준이가 "이 화면에서 오류가 났다"고 할 때
그 번호 하나로 로그를 정확히 집어낼 수 있어야 한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors.catalog import spec_for
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.logging import get_logger
from app.core.logging.context import request_id_var

logger = get_logger(__name__)

#: 상태 코드만 알고 있을 때 쓸 기본 코드.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.AUTH_UNAUTHENTICATED,
    403: ErrorCode.AUTH_FORBIDDEN,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    422: ErrorCode.VALIDATION_INVALID_FIELD,
}


def _request_id(request: Request) -> str | None:
    # scope state가 1순위 — 500 핸들러는 contextvar 스코프 밖에서 돈다.
    state_id = getattr(request.state, "request_id", None)
    return state_id or request_id_var.get()


def _envelope(
    code: ErrorCode,
    message: str,
    detail: dict[str, Any],
    status_code: int,
    request: Request,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": str(code),
                "message": message,
                "detail": detail,
                "request_id": _request_id(request),
            }
        },
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    log = logger.bind(error_code=str(exc.code), path=request.url.path, **exc.log_context)
    if exc.status_code >= 500:
        log.error("app_error", exc_info=exc)
    else:
        log.info("app_error")
    return _envelope(exc.code, exc.message, exc.detail, exc.status_code, request)


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # ★ Pydantic 오류의 `input`·`ctx`를 그대로 실으면 안 된다.
    #   키 이름이 password가 아니라 input이라서 어떤 키 목록으로도 못 막는
    #   유출 경로가 된다(사용자가 보낸 값이 그대로 에코된다).
    fields = [
        {
            "위치": ".".join(str(part) for part in err.get("loc", ())[1:]) or "(본문)",
            "사유": str(err.get("msg", "")),
        }
        for err in exc.errors()
    ]
    spec = spec_for(ErrorCode.VALIDATION_INVALID_FIELD)
    logger.info("validation_error", path=request.url.path, field_count=len(fields))
    return _envelope(
        ErrorCode.VALIDATION_INVALID_FIELD,
        spec.message_ko,
        {"항목": fields},
        spec.status_code,
        request,
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_UNEXPECTED)
    spec = spec_for(code)
    message = spec.message_ko
    logger.info("http_exception", path=request.url.path, status_code=exc.status_code)
    return _envelope(code, message, {}, exc.status_code, request)


async def handle_stale_data(request: Request, exc: Exception) -> JSONResponse:
    """낙관적 잠금 충돌 → 409 (§17.2)."""
    spec = spec_for(ErrorCode.CONCURRENCY_VERSION_CONFLICT)
    logger.info("version_conflict", path=request.url.path)
    return _envelope(
        ErrorCode.CONCURRENCY_VERSION_CONFLICT, spec.message_ko, {}, spec.status_code, request
    )


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """마지막 그물 — 스택트레이스는 로그에만, 응답에는 오류 번호만."""
    spec = spec_for(ErrorCode.INTERNAL_UNEXPECTED)
    logger.error("unhandled_exception", path=request.url.path, exc_info=exc)
    return _envelope(ErrorCode.INTERNAL_UNEXPECTED, spec.message_ko, {}, spec.status_code, request)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(StaleDataError, handle_stale_data)
    app.add_exception_handler(Exception, handle_unexpected)
