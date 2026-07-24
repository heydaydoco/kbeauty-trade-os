"""도메인 예외 — 응답에 나갈 것과 로그에만 남을 것을 타입으로 분리한다.

§18.4는 에러 메시지를 사용자용(한국어+원인+조치)과 로그용(코드+상세)으로
나누라고 한다. "조심해서 나누자"로는 지켜지지 않으므로, 응답 직렬화 함수가
`log_context`에 **접근할 경로 자체를 갖지 않게** 만든다.
"""

from __future__ import annotations

from typing import Any

from app.core.errors.catalog import spec_for
from app.core.errors.codes import ErrorCode


class AppError(Exception):
    """업무 규칙 위반·처리 실패를 나타내는 예외."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        detail: dict[str, Any] | None = None,
        log_context: dict[str, Any] | None = None,
        message_override: str | None = None,
    ) -> None:
        """
        Args:
            code: 에러 코드(카탈로그의 키).
            detail: **응답에 나가는** 부가 정보. 사용자가 봐도 되는 것만 담는다.
                    (예: 어느 항목이 잘못됐는지, 부족한 수량이 몇인지)
            log_context: **로그에만 남는** 진단 정보. 응답에는 절대 실리지 않는다.
                    (예: 내부 ID, 쿼리 조건, 외부 응답 원문)
            message_override: 카탈로그 문구 대신 쓸 한국어 문구.
        """
        self.code = code
        self.detail = detail or {}
        self.log_context = log_context or {}
        spec = spec_for(code)
        self.status_code = spec.status_code
        self.message = message_override or spec.message_ko
        super().__init__(f"{code}: {self.message}")


class NotFoundError(AppError):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(ErrorCode.RESOURCE_NOT_FOUND, **kwargs)


class ForbiddenError(AppError):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(ErrorCode.AUTH_FORBIDDEN, **kwargs)


class UnauthenticatedError(AppError):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(ErrorCode.AUTH_UNAUTHENTICATED, **kwargs)


class VersionConflictError(AppError):
    """낙관적 잠금 충돌 (§17.2)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(ErrorCode.CONCURRENCY_VERSION_CONFLICT, **kwargs)
