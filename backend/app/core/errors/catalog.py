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
    ErrorCode.DOCUMENTS_LINKED_TO_TASK: ErrorSpec(
        409,
        "인증 체크리스트가 서류로 연결 중인 문서는 삭제할 수 없습니다. 해당 인증의 태스크에서 서류 연결을 먼저 해제한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.COLLABORATION_COMM_LOG_HAS_ATTACHMENTS: ErrorSpec(
        409,
        "첨부 문서가 남은 통신 기록은 삭제할 수 없습니다. 첨부를 먼저 삭제한 뒤 다시 시도해 주세요.",
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
    ErrorCode.CERTIFICATIONS_PACKAGE_TOO_LARGE: ErrorSpec(
        422,
        "전달 서류가 너무 많거나 커서 한 번에 묶을 수 없습니다(파일 100건·합계 200MB 이하). "
        "서류를 나누어 내려받아 주세요.",
    ),
    ErrorCode.CERTIFICATIONS_PACKAGE_FILES_UNAVAILABLE: ErrorSpec(
        409,
        "일부 서류 파일을 읽을 수 없어 전달 묶음을 만들지 않았습니다(빠진 채 나가면 더 위험합니다). "
        "아래 서류를 문서 보관소에서 다시 올린 뒤 시도해 주세요.",
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
    ErrorCode.TRADE_DOCS_DOCUMENT_INCOMPLETE: ErrorSpec(
        422,
        "발행·확정에 필요한 항목이 비어 있습니다. 표시된 항목을 입력한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_DOCUMENT_FROZEN: ErrorSpec(
        409,
        "이미 발행·확정되어 수정할 수 없는 항목입니다. 내용을 바꿔야 하면 취소 후 새 문서로 작성해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_LINE_SKU_DISCONTINUED: ErrorSpec(
        422,
        "단종된 SKU는 새 라인으로 추가할 수 없습니다. 마스터에서 SKU 상태를 확인하거나 다른 SKU를 선택해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_LINE_SKU_DUPLICATE: ErrorSpec(
        409,
        "같은 SKU의 유상 또는 무상 라인이 이미 있습니다. 기존 라인의 수량을 수정해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_LINE_AMOUNT_OUT_OF_RANGE: ErrorSpec(
        422,
        "금액 또는 라인 수가 허용 범위를 넘었습니다. 수량·단가를 확인하거나 문서를 나누어 작성해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_PAYMENT_LC_DISABLED: ErrorSpec(
        422,
        "L/C 결제조건은 현재 사용할 수 없습니다. 선수금 T/T 또는 후불 T/T를 선택하시거나 관리자에게 문의해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_TRANSITION_NOT_ALLOWED: ErrorSpec(
        409,
        "현재 상태에서 허용되지 않는 전이입니다. 화면을 새로 고쳐 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_TRANSITION_REASON_REQUIRED: ErrorSpec(
        422,
        "이 전이에는 사유가 필요합니다. 사유를 입력해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE: ErrorSpec(
        409,
        "복제할 수 없는 원본 문서입니다. 취소 또는 만료된 동일 거래처의 문서인지 확인해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_CANCEL_SUCCESSOR_ALIVE: ErrorSpec(
        409,
        "이 문서에서 파생된 후속 문서가 아직 살아 있습니다. 후속 문서를 먼저 취소한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_QUANTITY_EXCEEDS_OPEN: ErrorSpec(
        409,
        "요청 수량이 남은 수량을 넘었습니다. 남은 수량을 확인한 뒤 수량을 줄여 다시 시도해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_QUANTITY_DOCUMENT_NOT_CONSUMABLE: ErrorSpec(
        409,
        "이 문서의 현재 상태에서는 수량을 사용할 수 없습니다. 문서 상태를 확인한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_PARENT_NOT_USABLE: ErrorSpec(
        409,
        "원천 문서의 현재 상태에서는 후속 문서를 만들 수 없습니다. 발행된 견적인지 확인하거나 새 문서를 작성해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_VALIDITY_EXPIRED: ErrorSpec(
        422,
        "유효기간이 지난 문서로는 진행할 수 없습니다. 새 견적 또는 PI를 발행한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_PAYMENT_PI_NOT_OPEN: ErrorSpec(
        409,
        "취소되었거나 만료된 PI에는 입금을 반영할 수 없습니다. 새 PI를 발행한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO: ErrorSpec(
        409,
        "같은 바이어의 같은 PO번호가 이미 다른 수주에 등록되어 있습니다. 기존 수주를 확인하거나, 정정이라면 기존 수주를 취소한 뒤 다시 등록해 주세요.",
    ),
    ErrorCode.TRADE_DOCS_REFERENCE_ALREADY_CONVERTED: ErrorSpec(
        409,
        "이 문서에서 이미 수주가 만들어졌습니다. 기존 수주를 확인하거나, 정정이라면 기존 수주를 취소한 뒤 다시 만들어 주세요.",
    ),
    ErrorCode.TRADE_DOCS_RESUME_TARGET_MISMATCH: ErrorSpec(
        409,
        "보류 직전 상태와 다른 상태로는 재개할 수 없습니다. 화면을 새로 고쳐 재개 가능한 상태를 확인한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.CONCURRENCY_VERSION_CONFLICT: ErrorSpec(
        409,
        # §17.2가 지정한 문구. 임의로 바꾸지 말 것.
        "다른 사용자가 먼저 수정했습니다. 화면을 새로 고쳐 최신 내용을 확인한 뒤 다시 저장해 주세요.",
    ),
    ErrorCode.PARTNERS_CREDIT_LIMIT_ADMIN_ONLY: ErrorSpec(
        403,
        "여신한도는 관리자만 등록·변경할 수 있습니다. 관리자에게 설정을 요청해 주세요.",
    ),
    ErrorCode.PAYMENTS_PAYMENT_CURRENCY_MISMATCH: ErrorSpec(
        422,
        "입금 통화가 PI 통화와 다릅니다. PI와 같은 통화로 입금액을 다시 입력해 주세요(환산 입금은 지원하지 않습니다).",
    ),
    ErrorCode.PAYMENTS_PAYMENT_EXCEEDS_DUE: ErrorSpec(
        422,
        "입금액이 PI 선수금 청구액을 넘습니다. 남은 선수금 이하로 입력해 주세요(선수금을 넘는 금액은 잔금 입금으로 따로 기록합니다).",
    ),
    ErrorCode.PAYMENTS_PAYMENT_PI_NOT_ADVANCE: ErrorSpec(
        422,
        "선수금 T/T가 아닌 PI에는 입금을 기록할 수 없습니다. 결제유형을 확인해 주세요(잔금 입금은 채권 입금으로 기록합니다).",
    ),
    ErrorCode.PAYMENTS_PAYMENT_ALREADY_REVERSED: ErrorSpec(
        409,
        "이미 역기록된 입금입니다. 입금 목록을 새로 고쳐 확인해 주세요(정정이 필요하면 새 입금을 기록해 주세요).",
    ),
    ErrorCode.PAYMENTS_PAYMENT_NOT_REVERSIBLE: ErrorSpec(
        409,
        "역기록 행은 다시 역기록할 수 없습니다. 정정이 필요하면 새 입금을 기록해 주세요.",
    ),
    ErrorCode.POLICIES_POLICY_UNKNOWN_KEY: ErrorSpec(
        404,
        "알 수 없는 정책 항목입니다. 정책 목록을 새로 고쳐 확인해 주세요.",
    ),
    ErrorCode.POLICIES_POLICY_INVALID_VALUE: ErrorSpec(
        422,
        "정책 값이 허용 범위를 벗어났습니다. 입력 가능한 값을 확인해 다시 입력해 주세요.",
    ),
    ErrorCode.CONCURRENCY_LOCK_BUSY: ErrorSpec(
        409,
        "같은 건을 다른 사용자가 처리 중입니다. 잠시 후 화면을 새로 고쳐 다시 시도해 주세요.",
    ),
    ErrorCode.APPROVALS_LINE_NOT_CONFIGURED: ErrorSpec(
        422,
        "결재선이 없어 승인 요청을 만들 수 없습니다. 관리자에게 결재선 등록을 요청해 주세요.",
    ),
    ErrorCode.APPROVALS_LINE_DUPLICATE: ErrorSpec(
        409,
        "같은 유형·통화·임계 금액의 결재선이 이미 있습니다. 목록에서 기존 결재선을 확인해 주세요.",
    ),
    ErrorCode.APPROVALS_APPROVAL_NO_ELIGIBLE_APPROVER: ErrorSpec(
        422,
        "결재할 수 있는 사람이 없어 승인 요청을 만들 수 없습니다. 관리자에게 결재 역할 보유자 지정을 요청해 주세요.",
    ),
    ErrorCode.APPROVALS_APPROVAL_ALREADY_ACTIVE: ErrorSpec(
        409,
        "이 건에는 이미 진행 중인 승인이 있습니다. 승인 목록에서 진행 상태를 확인해 주세요.",
    ),
    ErrorCode.APPROVALS_APPROVAL_REQUIRED: ErrorSpec(
        422,
        "승인이 필요한 건이라 승인 없이는 확정할 수 없습니다. 승인을 요청하고 결재가 끝난 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.APPROVALS_APPROVAL_STALE: ErrorSpec(
        409,
        "승인 이후 대상이 바뀌어 승인이 무효가 되었습니다. 변경 내용을 확인하고 승인을 다시 요청해 주세요.",
    ),
    ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED: ErrorSpec(
        409,
        "현재 승인 상태에서는 할 수 없는 처리입니다. 화면을 새로 고쳐 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.APPROVALS_TRANSITION_REASON_REQUIRED: ErrorSpec(
        422,
        "반려·회수에는 사유가 필요합니다. 사유를 입력해 주세요.",
    ),
    ErrorCode.APPROVALS_DECISION_NOT_APPROVER: ErrorSpec(
        403,
        "이 승인을 결재할 권한이 없습니다. 결재 역할이나 유효한 대결 지정이 있는지 확인해 주세요.",
    ),
    ErrorCode.APPROVALS_DECISION_SELF_APPROVAL: ErrorSpec(
        403,
        "본인이 올린 승인은 직접 결재할 수 없습니다. 다른 결재자에게 결재를 요청해 주세요.",
    ),
    ErrorCode.APPROVALS_DELEGATION_OVERLAP: ErrorSpec(
        409,
        "같은 위임자·유형·역할의 대결 기간이 겹칩니다. 기존 대결을 종료하거나 기간을 조정해 주세요.",
    ),
    ErrorCode.APPROVALS_DELEGATION_NOT_ACTIVE: ErrorSpec(
        409,
        "이미 종료되었거나 기간이 지난 대결입니다. 대결 목록에서 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.GATES_OVERRIDE_NOT_ALLOWED: ErrorSpec(
        403,
        "이 게이트의 예외 통과(override)는 현재 역할로 할 수 없습니다. 권한이 있는 담당자(관리자 등)에게 요청해 주세요.",
    ),
    ErrorCode.GATES_OVERRIDE_NOT_APPLICABLE: ErrorSpec(
        422,
        "예외 통과(override) 대상이 아닌 항목입니다. 품번 매핑·중복 PO·여신 한도는 데이터를 고치거나 승인으로만 해소됩니다. 판정을 새로 확인해 주세요.",
    ),
    ErrorCode.GATES_OVERRIDE_STALE: ErrorSpec(
        409,
        "확인하신 판정이 이미 바뀌었습니다. 수주의 게이트 판정을 새로 불러와 다시 확인해 주세요.",
    ),
    ErrorCode.GATES_OVERRIDE_ALREADY_GRANTED: ErrorSpec(
        409,
        "이미 같은 판정에 유효한 예외 통과가 있습니다. 수주의 게이트 판정을 새로 불러와 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.GATES_OVERRIDE_NOT_GRANTED: ErrorSpec(
        422,
        "철회할 유효한 예외 통과가 없습니다(없거나 이미 철회되었습니다). 게이트 판정을 새로 불러와 확인해 주세요.",
    ),
    ErrorCode.GATES_OVERRIDE_ORDER_NOT_OPEN: ErrorSpec(
        409,
        "접수 상태가 아닌 수주는 예외 통과를 부여하거나 철회할 수 없습니다. 수주의 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED: ErrorSpec(
        409,
        "해소되지 않은 게이트가 있어 수주를 확정할 수 없습니다. 아래 항목(승인 필요·입금 확인·가격·최소수량·준비도 등)을 해소한 뒤 다시 확정해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_STATE_NOT_PENDING: ErrorSpec(
        409,
        "이미 확정되었거나 거부된 오더 인테이크라 더 처리할 수 없습니다. 목록을 새로 불러와 현재 상태를 확인해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_LINE_UNMAPPED_ITEMS: ErrorSpec(
        422,
        "바이어 품번이 SKU에 매핑되지 않았거나 삭제된 품목이 있어 접수할 수 없습니다. 거래처 품번 매핑을 등록한 뒤 다시 확인해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_LINE_STALE_MAPPING: ErrorSpec(
        409,
        "검토한 뒤 바이어 품번 매핑이 바뀌었습니다. 품번 해석을 다시 확인(재해석)하고 내용을 검토한 뒤 확정해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_LINE_DUPLICATE_SKU: ErrorSpec(
        422,
        "같은 SKU로 매핑된 라인이 둘 이상입니다. 수주는 SKU마다 유상 라인 1줄이므로 라인을 합치거나 수정해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_LINE_LIMIT_EXCEEDED: ErrorSpec(
        422,
        "오더 인테이크의 라인은 1개 이상 200개 이하여야 합니다. 라인 수를 확인해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_GATE_UNRESOLVED: ErrorSpec(
        409,
        "접수 확정에 필요한 확인 항목을 평가하지 못했습니다. 잠시 후 다시 시도하시고, 계속되면 오류 번호와 함께 관리자에게 문의해 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_FILE_DUPLICATE: ErrorSpec(
        409,
        "같은 파일이 이미 검토 대기 중인 오더 인테이크로 올라와 있습니다. 기존 인테이크를 확정하거나 거부한 뒤 필요하면 다시 올려 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_FILE_INVALID_ROWS: ErrorSpec(
        422,
        "파일에 고쳐야 할 행이 있어 아무것도 등록하지 않았습니다. 아래 행·열별 사유를 모두 고친 뒤 파일 전체를 다시 올려 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_FILE_TOO_MANY_GROUPS: ErrorSpec(
        422,
        "한 파일에 담을 수 있는 바이어 PO 수를 넘었습니다(파일의 PO 수·상한은 안내 정보 참조). 파일을 나누어 다시 올려 주세요.",
    ),
    ErrorCode.ORDER_INTAKE_FILE_UNSUPPORTED_FORMAT: ErrorSpec(
        422,
        "엑셀 파일(.xlsx·.xls)은 올릴 수 없습니다. 엑셀에서 '다른 이름으로 저장 > CSV UTF-8(쉼표로 분리)'로 저장한 뒤 올려 주세요.",
    ),
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: ErrorSpec(
        409,
        "같은 요청 키로 다른 내용이 이미 처리되었습니다. 화면을 새로 고쳐 처리 결과를 확인해 주세요.",
    ),
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: ErrorSpec(
        400,
        "요청 식별 키가 없어 처리하지 못했습니다. 화면을 새로 고친 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.IDEMPOTENCY_KEY_INVALID: ErrorSpec(
        422,
        "요청 식별 키의 형식이 올바르지 않아 처리하지 못했습니다(1~128자, 보이지 않는 글자 불가). 화면을 새로 고친 뒤 다시 시도해 주세요.",
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
    ErrorCode.ORDER_BOARD_BULK_TOO_MANY: ErrorSpec(
        422,
        "한 번에 처리할 수 있는 건수(50건)를 넘었습니다. 대상을 나눠서 다시 처리해 주세요.",
    ),
    ErrorCode.ORDER_BOARD_FILTER_LIMIT_REACHED: ErrorSpec(
        422,
        "저장 필터는 한 사람당 20개까지 만들 수 있습니다. 쓰지 않는 필터를 지운 뒤 다시 저장해 주세요.",
    ),
    ErrorCode.ORDER_BOARD_FILTER_DUPLICATE_NAME: ErrorSpec(
        409,
        "같은 이름의 저장 필터가 이미 있습니다. 다른 이름으로 저장하거나 기존 필터를 수정해 주세요.",
    ),
    ErrorCode.HOLIDAYS_CALENDAR_SOURCE_REQUIRED: ErrorSpec(
        422,
        "휴일 캘린더에는 근거 링크(http:// 또는 https://)와 확인일(오늘 또는 그 이전)이 필요합니다. 관보·정부 공고 링크와 확인한 날짜를 입력해 주세요.",
    ),
    ErrorCode.HOLIDAYS_CALENDAR_YEAR_MISMATCH: ErrorSpec(
        422,
        "휴일 날짜의 연도가 선언 연도와 다릅니다. 해당 연도의 날짜만 입력하거나, 다른 연도는 그 연도 캘린더에서 등록해 주세요.",
    ),
    ErrorCode.HOLIDAYS_CALENDAR_DUPLICATE_DATE: ErrorSpec(
        422,
        "같은 날짜가 두 번 입력됐습니다. 날짜마다 휴일 이름을 하나만 남기고 다시 저장해 주세요.",
    ),
    ErrorCode.HOLIDAYS_CALENDAR_YEAR_DUPLICATE: ErrorSpec(
        409,
        "다른 사용자가 같은 국가·연도의 휴일 캘린더를 먼저 등록했습니다. 화면을 다시 불러온 뒤 내용을 확인하고 저장해 주세요.",
    ),
    ErrorCode.HOLIDAYS_COUNTRY_INVALID: ErrorSpec(
        422,
        "국가 코드는 ISO 영문 대문자 두 글자(예: KR, CN, US)여야 합니다. 국가 코드를 확인해 주세요.",
    ),
    ErrorCode.HOLIDAYS_CSV_INVALID_FORMAT: ErrorSpec(
        422,
        "휴일 CSV 형식을 확인해 주세요(UTF-8, 머리글 holiday_on,name).",
    ),
    ErrorCode.SHIPMENTS_SOURCE_LINE_MISMATCH: ErrorSpec(
        422,
        "선택한 라인이 이 선적의 원천 전표(수주·발주)의 라인이 아닙니다. 원천 전표 상세에서 라인을 다시 선택해 주세요.",
    ),
    ErrorCode.SHIPMENTS_SHIPMENT_NOT_ACTIVE: ErrorSpec(
        409,
        "취소된 선적은 수정할 수 없습니다. 필요하면 원천 전표(수주·발주)에서 새 선적을 만들어 주세요.",
    ),
    ErrorCode.SHIPMENTS_LINE_DUPLICATE_SOURCE: ErrorSpec(
        409,
        "같은 원천 라인(수주·발주 라인)이 이 선적에 이미 있습니다. 기존 라인의 수량을 수정해 주세요.",
    ),
    ErrorCode.SHIPMENTS_LINE_LAST_LINE: ErrorSpec(
        409,
        "선적에는 라인이 1개 이상 있어야 합니다. 선적 전체를 없애려면 선적을 취소해 주세요.",
    ),
    ErrorCode.SHIPMENTS_PARTY_ROLE_DUPLICATE: ErrorSpec(
        409,
        "이 역할의 당사자가 이미 있습니다. 기존 당사자를 삭제한 뒤 다시 지정해 주세요.",
    ),
    ErrorCode.SHIPMENTS_PARTY_ROLE_NOT_ALLOWED: ErrorSpec(
        422,
        "이 역할의 당사자는 직접 지정하거나 삭제할 수 없습니다(수출: 수하인 = 수주 바이어 자동·송하인 = 자사 / 수입: 송하인 = 발주 공급사 자동·수하인 = 자사). 통지처·포워더·관세사 역할로 지정해 주세요.",
    ),
    ErrorCode.SHIPMENTS_PARTY_ENGLISH_NAME_MISSING: ErrorSpec(
        422,
        "거래처의 영문명이 비어 있어 선적 서류에 쓸 수 없습니다. 거래처 화면에서 영문명을 입력한 뒤 다시 시도해 주세요.",
    ),
    ErrorCode.SHIPMENTS_QUANTITY_EXCEEDS_ASSIGNABLE: ErrorSpec(
        409,
        "요청 수량이 발주 라인의 배정 가능량(발주 수량 − 다른 수입선적에 배정된 수량)을 넘었습니다. 배정 가능량을 확인한 뒤 수량을 줄여 다시 시도해 주세요.",
    ),
    ErrorCode.SHIPMENTS_SHIPMENT_CUSTOMS_RECORD_ALIVE: ErrorSpec(
        409,
        "통관 기록이 남아 있는 선적은 취소할 수 없습니다. 통관 기록을 사유와 함께 먼저 삭제해 주세요.",
    ),
    ErrorCode.SHIPMENTS_SHIPMENT_ACTUAL_RECORDED: ErrorSpec(
        409,
        "ETD·B/L 발행·ETA 실적이 기록된 선적은 취소할 수 없습니다. 잘못 입력한 실적이면 사유와 함께 실적을 정정(삭제)한 뒤 취소해 주세요.",
    ),
    ErrorCode.SHIPMENTS_CUSTOMS_DECLARATION_DUPLICATE: ErrorSpec(
        409,
        "같은 구분·신고번호의 통관 기록이 이미 있습니다. 기존 통관 기록을 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_CUSTOMS_KIND_MISMATCH: ErrorSpec(
        422,
        "신고 구분이 선적 구분과 다릅니다. 수출선적에는 수출신고, 수입선적에는 수입신고를 기록해 주세요.",
    ),
    ErrorCode.SHIPMENTS_CUSTOMS_REASON_REQUIRED: ErrorSpec(
        422,
        "통관 기록을 정정하거나 삭제하려면 사유가 필요합니다. 사유를 입력해 주세요.",
    ),
    ErrorCode.SHIPMENTS_CUSTOMS_DATE_IN_FUTURE: ErrorSpec(
        422,
        "신고일·수리일은 오늘(한국 날짜) 이후일 수 없습니다. 실제 신고·수리된 날짜를 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_CUSTOMS_ACCEPT_BEFORE_DECLARE: ErrorSpec(
        422,
        "수리일이 신고일보다 앞설 수 없습니다. 신고일과 수리일을 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_DUPLICATE_TYPE: ErrorSpec(
        409,
        "같은 종류가 이미 있습니다(선적·발주 일정 또는 품목군 마일스톤 세트). 화면을 다시 불러와 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_DERIVED_NOT_EDITABLE: ErrorSpec(
        422,
        "자동 계산 마일스톤(적재기한·대금만기·제시기한)은 직접 수정할 수 없습니다. 계산의 근거가 되는 실적·계획·통관 기록을 고쳐 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_IN_FUTURE: ErrorSpec(
        422,
        "실적은 아직 오지 않은 날짜·시각으로 기록할 수 없습니다. 실제로 일어난 날짜·시각을 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_REASON_REQUIRED: ErrorSpec(
        422,
        "계획 변경(롤오버)이나 실적 정정에는 사유가 필요합니다. 사유를 입력해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_TYPE_NOT_APPLICABLE: ErrorSpec(
        422,
        "이 대상(선적 구분·OEM 생산 발주·품목군 세트)에는 쓰지 않는 마일스톤 종류입니다. 마일스톤 목록에서 종류를 다시 선택해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_OWNER_NOT_ACTIVE: ErrorSpec(
        409,
        "취소된 전표의 일정은 수정할 수 없습니다. 화면을 다시 불러와 전표 상태를 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_FROM_CUSTOMS_RECORD: ErrorSpec(
        422,
        "신고수리 실적은 통관 기록의 수리일에서 자동으로 반영됩니다. 통관 기록에서 수리일을 입력해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_TIMEZONE_INVALID: ErrorSpec(
        422,
        "시간대가 올바르지 않습니다. 목록에서 시간대(예: Asia/Seoul)를 선택해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_VALUE_SHAPE_MISMATCH: ErrorSpec(
        422,
        "이 마일스톤 종류에 맞지 않는 값 형식입니다. 서류마감·Cargo Closing은 시각과 시간대, 나머지는 날짜로 입력해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_BEFORE_RELEASE: ErrorSpec(
        422,
        "ETD·B/L 발행·ETA 실적은 출고지시 뒤에만 기록할 수 있습니다. 출고지시를 먼저 진행해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_NOTICE_LIMIT_REACHED: ErrorSpec(
        422,
        "변경 1건에는 통보 기록을 20건까지 남길 수 있습니다. 기존 통보 기록을 확인해 주세요.",
    ),
    ErrorCode.SHIPMENTS_MILESTONE_OWNER_NOT_OEM: ErrorSpec(
        422,
        "OEM 생산 일정(원료수급·충진·포장·출하검사)은 OEM 생산 발주에만 기록할 수 있습니다. 발주 구분을 확인해 주세요.",
    ),
}


def spec_for(code: ErrorCode) -> ErrorSpec:
    return ERROR_CATALOG[code]
