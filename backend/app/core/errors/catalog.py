"""에러 코드 → HTTP 상태 + 한국어 문구.

문구는 여기에만 존재한다. 클라이언트는 서버가 준 message를 그대로 보여주기만
한다 — 같은 상황에 서버와 화면이 다른 말을 하는 일을 없앤다.

문구 규칙 (§18.4 "사용자=한국어+원인+조치"):
    1문장 = 무슨 일이 일어났는가(원인)
    2문장 = 무엇을 하면 되는가(조치)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors.codes import ErrorCode


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status_code: int
    message_ko: str


ERROR_CATALOG: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.VALIDATION_INVALID_FIELD: ErrorSpec(
        422,
        "입력값이 올바르지 않습니다. 표시된 항목을 확인한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.REQUEST_MALFORMED: ErrorSpec(
        400,
        "요청 내용을 읽지 못했습니다. 화면을 새로 고친 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorSpec(
        404,
        "요청하신 자료를 찾을 수 없습니다. 이미 삭제되었거나 주소가 잘못되었을 수 있으니 목록에서 다시 선택해 주세요.",
    ),
    ErrorCode.AUTH_UNAUTHENTICATED: ErrorSpec(
        401,
        "로그인이 필요합니다. 로그인 화면에서 다시 로그인해 주세요.",
    ),
    ErrorCode.AUTH_FORBIDDEN: ErrorSpec(
        403,
        "이 자료에 접근할 권한이 없습니다. 필요하시면 관리자에게 권한을 요청해 주세요.",
    ),
    ErrorCode.AUTH_INVALID_CREDENTIALS: ErrorSpec(
        401,
        # 어느 쪽이 틀렸는지 밝히지 않는다 — 이메일 존재 여부를 알려 주면
        # 계정 목록을 긁어모으는 통로가 된다(§18.1).
        "이메일 또는 비밀번호가 올바르지 않습니다. 다시 확인해 주세요.",
    ),
    ErrorCode.AUTH_ACCOUNT_LOCKED: ErrorSpec(
        423,
        "비밀번호를 5회 연속 틀려 계정이 잠겼습니다. 잠시 후 다시 시도하시거나 관리자에게 잠금 해제를 요청해 주세요.",
    ),
    ErrorCode.AUTH_ACCOUNT_INACTIVE: ErrorSpec(
        403,
        "비활성 처리된 계정입니다. 관리자에게 계정 활성화를 요청해 주세요.",
    ),
    ErrorCode.IDENTITY_LAST_ADMIN_PROTECTED: ErrorSpec(
        409,
        "마지막 남은 관리자입니다. 이 계정의 관리자 권한을 회수하거나 비활성화하면 아무도 시스템을 관리할 수 없게 됩니다. 다른 사용자에게 관리자 권한을 먼저 부여해 주세요.",
    ),
    ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE: ErrorSpec(
        422,
        "해당 기준일에 적용되는 단가가 없습니다. 그 날짜 이전에 발효되는 단가를 먼저 등록해 주세요.",
    ),
    ErrorCode.INGREDIENTS_FORMULA_EMPTY: ErrorSpec(
        422,
        "이 제품에는 등록된 전성분이 없습니다. 전성분을 먼저 등록한 뒤 스크리닝해 주세요.",
    ),
    ErrorCode.DOCUMENTS_FILE_TOO_LARGE: ErrorSpec(
        413,
        "파일이 너무 큽니다(최대 20MB). 파일 크기를 줄인 뒤 다시 업로드해 주세요.",
    ),
    ErrorCode.DOCUMENTS_FILE_TYPE_NOT_ALLOWED: ErrorSpec(
        422,
        "허용되지 않는 파일 형식입니다. PDF·이미지·엑셀·워드·텍스트·ZIP·AI 형식의 파일로 다시 올려 주세요.",
    ),
    ErrorCode.DOCUMENTS_RETENTION_LOCKED: ErrorSpec(
        409,
        "보존기한이 지나지 않은 문서는 삭제할 수 없습니다(파기 잠금). 보존기한이 지난 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.DOCUMENTS_SET_SKU_MSDS_FORBIDDEN: ErrorSpec(
        422,
        "세트 SKU에는 MSDS 문서를 연결할 수 없습니다. 위험물 판정은 구성품 단위로 하니 구성품 SKU에 등록해 주세요.",
    ),
    ErrorCode.DOCUMENTS_DOWNLOAD_NOT_A_FILE: ErrorSpec(
        409,
        "링크형 문서에는 내려받을 파일이 없습니다. 문서의 링크 주소로 이동해 확인해 주세요.",
    ),
    ErrorCode.CONCURRENCY_VERSION_CONFLICT: ErrorSpec(
        409,
        # §17.2가 지정한 문구. 임의로 바꾸지 말 것.
        "다른 사용자가 먼저 수정했습니다. 화면을 새로 고쳐 최신 내용을 확인한 뒤 다시 저장해 주세요.",
    ),
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: ErrorSpec(
        409,
        "같은 요청 키로 다른 내용이 이미 처리되었습니다. 화면을 새로 고쳐 처리 결과를 확인해 주세요.",
    ),
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: ErrorSpec(
        400,
        "요청 식별 키가 없어 처리하지 못했습니다. 화면을 새로 고친 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.TRANSACTION_BOUNDARY_VIOLATION: ErrorSpec(
        500,
        "요청을 처리하는 중 내부 오류가 발생했습니다. 잠시 후 다시 시도하시고, 계속되면 오류 번호와 함께 관리자에게 알려 주세요.",
    ),
    ErrorCode.EXTERNAL_TIMEOUT: ErrorSpec(
        504,
        "외부 시스템 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.",
    ),
    ErrorCode.EXTERNAL_UNAVAILABLE: ErrorSpec(
        503,
        "외부 시스템에 연결할 수 없습니다. 잠시 후 다시 시도하시고, 계속되면 관리자에게 알려 주세요.",
    ),
    ErrorCode.INTERNAL_UNEXPECTED: ErrorSpec(
        500,
        "요청을 처리하는 중 오류가 발생했습니다. 잠시 후 다시 시도하시고, 계속되면 오류 번호와 함께 관리자에게 알려 주세요.",
    ),
}


def spec_for(code: ErrorCode) -> ErrorSpec:
    return ERROR_CATALOG[code]
