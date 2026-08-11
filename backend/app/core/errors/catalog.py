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
    ErrorCode.IMPORTS_FILE_TOO_LARGE: ErrorSpec(
        413,
        "파일이 너무 큽니다(최대 20MB). 파일을 나누어 다시 업로드해 주세요.",
    ),
    ErrorCode.IMPORTS_FILE_TYPE_NOT_ALLOWED: ErrorSpec(
        422,
        "CSV 파일만 업로드할 수 있습니다. 표준 양식을 내려받아 CSV 형식 그대로 저장한 뒤 다시 올려 주세요.",
    ),
    ErrorCode.IMPORTS_FILE_ENCODING_INVALID: ErrorSpec(
        422,
        "파일의 문자 인코딩을 읽지 못했습니다. 엑셀에서 'CSV UTF-8' 형식으로 저장한 뒤 다시 올려 주세요.",
    ),
    ErrorCode.IMPORTS_FILE_HEADER_MISMATCH: ErrorSpec(
        422,
        "파일 첫 행(컬럼 제목)이 표준 양식과 다릅니다. 표준 양식을 내려받아 컬럼을 그대로 두고 작성해 주세요.",
    ),
    ErrorCode.IMPORTS_FILE_EMPTY: ErrorSpec(
        422,
        "파일에 데이터 행이 없습니다. 내용을 채운 뒤 다시 올려 주세요.",
    ),
    ErrorCode.IMPORTS_STAGING_DUPLICATE_PENDING: ErrorSpec(
        422,
        "같은 내용의 파일이 이미 검토 대기 중입니다. 기존 검토 건을 확정하거나 삭제한 뒤 다시 올려 주세요.",
    ),
    ErrorCode.IMPORTS_STAGING_NOT_PENDING: ErrorSpec(
        409,
        "검토 대기 상태가 아닌 건입니다. 목록을 새로 고쳐 처리 상태를 확인해 주세요.",
    ),
    ErrorCode.IMPORTS_CONFIRM_VERSION_CONFLICT: ErrorSpec(
        409,
        "검토 등록 후 다른 사용자가 대상 행을 먼저 수정하거나 삭제했습니다. 목록을 다시 내려받아 변경분을 새로 올려 주세요.",
    ),
    ErrorCode.MARKETS_MARKET_NOT_REGISTERED: ErrorSpec(
        422,
        # 조건 6(S2-1 판정) — 조치가 "시장 선등록"임을 문구가 직접 안내한다.
        "등록되지 않은 시장(국가 코드)입니다. 시장 관리 화면에서 해당 시장을 먼저 등록한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.REQUIREMENTS_TEMPLATE_EVIDENCE_REQUIRED: ErrorSpec(
        422,
        # §5.5 확정 차단(GC-C8) — 차단 사유와 조치(근거 입력)를 직접 안내한다.
        "근거링크와 최종확인일이 없는 템플릿은 확정할 수 없습니다. 두 항목을 입력한 뒤 다시 확정해 주세요.",
    ),
    ErrorCode.REQUIREMENTS_TEMPLATE_CONFIRMED_LOCKED: ErrorSpec(
        409,
        # 판정 조건 11 — 조치(초안 전환→수정→재확정)를 직접 안내한다.
        "확정된 템플릿은 편집할 수 없습니다. '초안 전환'으로 되돌린 뒤 수정하고 다시 확정해 주세요.",
    ),
    ErrorCode.REQUIREMENTS_TEMPLATE_ALREADY_CONFIRMED: ErrorSpec(
        409,
        "이미 확정된 템플릿입니다. 화면을 새로 고쳐 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.REQUIREMENTS_TEMPLATE_NOT_CONFIRMED: ErrorSpec(
        422,
        "확정 상태의 템플릿이 아니라 초안으로 전환할 수 없습니다. 화면을 새로 고쳐 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.REQUIREMENTS_TEMPLATE_NOT_DRAFT: ErrorSpec(
        409,
        # #16 보완 판정 — 허용 경로(초안 복귀→근거 재검토→확정)를 직접 안내한다.
        "폐기된 템플릿은 바로 확정할 수 없습니다. 편집에서 초안으로 되돌려 근거를 재검토한 뒤 다시 확정해 주세요.",
    ),
    ErrorCode.REQUIREMENTS_PREREQUISITE_CYCLE: ErrorSpec(
        422,
        "선행요건이 서로를 순환 참조하게 됩니다. 선행 관계의 방향을 확인해 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_TEMPLATE_NOT_CONFIRMED: ErrorSpec(
        422,
        # 3층 확정 게이트의 소비자(안건 ①) — 조치(템플릿 확정)를 직접 안내한다.
        "확정된 템플릿에서만 인증을 등록할 수 있습니다. 요건 템플릿을 먼저 확정해 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_TARGET_APPLIES_TO_MISMATCH: ErrorSpec(
        422,
        "대상 유형이 템플릿의 적용단위와 다릅니다. 템플릿의 적용단위를 확인해 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_TARGET_NOT_FOUND: ErrorSpec(
        422,
        "인증 대상을 찾을 수 없습니다. 대상이 등록돼 있는지 확인해 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_TRANSITION_NOT_ALLOWED: ErrorSpec(
        409,
        # §5.2 전이 외 변경 거부 — detail에 현재 상태·시도 상태가 실린다.
        "현재 상태에서 허용되지 않는 전이입니다. 화면을 새로 고쳐 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_TRANSITION_REASON_REQUIRED: ErrorSpec(
        422,
        "이 전이에는 사유가 필요합니다. 사유를 입력해 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_EXPIRES_ON_STATE_LOCKED: ErrorSpec(
        409,
        "만료일 정정은 승인·만료임박·만료 상태에서만 할 수 있습니다. 승인 전 만료일은 승인 기록에 함께 입력해 주세요.",
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
