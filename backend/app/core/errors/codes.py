"""에러 코드 (DESIGN.md §18.4 — 사용자용 한국어 / 로그용 코드+상세).

형식: `<도메인>.<대상>.<사유>` 3세그먼트, 대문자 스네이크.
    COMMON.RESOURCE.NOT_FOUND
    INVENTORY.STOCK.INSUFFICIENT     ← S4 이후 이런 식으로 늘어난다

이건 화면 몇 개가 아니라 **모든 엔드포인트와 클라이언트가 공유하는 와이어 계약**이다.
배포 뒤에 바꾸면 서버 전건과 클라이언트 전건이 같이 움직인다.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    # 입력
    VALIDATION_INVALID_FIELD = "COMMON.VALIDATION.INVALID_FIELD"

    #: 본문이 JSON이 아니거나 인코딩이 깨진 등 요청 자체가 읽히지 않는 경우.
    REQUEST_MALFORMED = "COMMON.REQUEST.MALFORMED"

    # 자원
    RESOURCE_NOT_FOUND = "COMMON.RESOURCE.NOT_FOUND"

    # 인증·인가 (S0-2에서 실제로 쓰인다)
    AUTH_UNAUTHENTICATED = "COMMON.AUTH.UNAUTHENTICATED"
    AUTH_FORBIDDEN = "COMMON.AUTH.FORBIDDEN"
    AUTH_INVALID_CREDENTIALS = "COMMON.AUTH.INVALID_CREDENTIALS"
    AUTH_ACCOUNT_LOCKED = "COMMON.AUTH.ACCOUNT_LOCKED"
    AUTH_ACCOUNT_INACTIVE = "COMMON.AUTH.ACCOUNT_INACTIVE"

    # 신원·계정 (S0-2)
    IDENTITY_LAST_ADMIN_PROTECTED = "IDENTITY.ADMIN.LAST_ONE"

    # 마스터 — 단가 (S1-1 / ADR-0017)
    #: 기준일에 적용되는 단가가 없다. **0이나 null로 대신하지 않는다** —
    #: 0을 돌려주면 금액 0원 전표가 조용히 확정된다(S3-1이 이 조회를 쓴다).
    CATALOG_PRICE_NOT_EFFECTIVE = "CATALOG.PRICE.NOT_EFFECTIVE"

    # 성분 — 스크리닝 (S1-2 / §4.3)
    #: 전성분이 비어 있다. **빈 리포트로 대신하지 않는다** — "검출 0건"과
    #: "검사할 것이 없었다"가 같은 모양이면 후자가 전자로 읽힌다(웹 세션 판정 D).
    INGREDIENTS_FORMULA_EMPTY = "INGREDIENTS.FORMULA.EMPTY"

    # 문서 보관소 (S1-3 / §4.7 / ADR-0028·0029)
    #: 업로드 크기 상한 초과 — 상한 수치는 documents.service.MAX_UPLOAD_BYTES가
    #: 유일 출처이고 테스트가 고정한다(웹 세션 승인 조건 4-④).
    DOCUMENTS_FILE_TOO_LARGE = "DOCUMENTS.FILE.TOO_LARGE"
    #: 허용 목록 밖 확장자 — 실행물(§18.1 "실행 불가")과 인라인 렌더 가능물을 막는다.
    DOCUMENTS_FILE_TYPE_NOT_ALLOWED = "DOCUMENTS.FILE.TYPE_NOT_ALLOWED"
    #: 보존기한 내 삭제 시도 — 파기 잠금(§4.7). 강제 삭제 액션은 없다(승인 문면).
    DOCUMENTS_RETENTION_LOCKED = "DOCUMENTS.DOCUMENT.RETENTION_LOCKED"
    #: 세트 SKU에 MSDS 연결 시도 — DB CHECK 대신 서비스 가드다(웹 세션 승인).
    DOCUMENTS_SET_SKU_MSDS_FORBIDDEN = "DOCUMENTS.MSDS.SET_SKU_FORBIDDEN"
    #: LINK형 문서의 다운로드 시도 — 내려받을 실물이 없다.
    DOCUMENTS_DOWNLOAD_NOT_A_FILE = "DOCUMENTS.DOWNLOAD.NOT_A_FILE"
    #: 인증 태스크가 서류 링크로 참조 중인 문서의 삭제 시도 — 선해제 후 삭제
    #: (S2-3 판정 요청 19 (나)). 기일 스캔이 활성 문서만 돌므로, 링크된 채 삭제되면
    #: 그 문서의 만료 알림이 조용히 사라진다.
    DOCUMENTS_LINKED_TO_TASK = "DOCUMENTS.DOCUMENT.LINKED_TO_TASK"
    #: 첨부(활성 문서)가 남은 통신 기록의 삭제 시도 — 첨부를 먼저 지운 뒤 삭제(S2-4 PR-1 리뷰).
    #: 주인 없는 활성 문서가 남으면 그 문서의 유효기간 알림이 붙일 곳 없이 계속 나간다.
    COLLABORATION_COMM_LOG_HAS_ATTACHMENTS = "COLLABORATION.COMM_LOG.HAS_ATTACHMENTS"

    # 엑셀 임포트 (S1-3 / §12.2 / ADR-09·0027)
    #: 업로드 크기 상한 초과 — 수치는 imports.service.MAX_UPLOAD_BYTES가 유일
    #: 출처(documents와 같은 20MiB — ADR-0029의 nginx 25m 안쪽).
    IMPORTS_FILE_TOO_LARGE = "IMPORTS.FILE.TOO_LARGE"
    #: 왕복 임포트는 CSV만 받는다 — 파서가 곧 왕복 보증 범위다(ADR-0027).
    IMPORTS_FILE_TYPE_NOT_ALLOWED = "IMPORTS.FILE.TYPE_NOT_ALLOWED"
    #: UTF-8(BOM)도 CP949도 아니어서 본문을 읽지 못했다.
    IMPORTS_FILE_ENCODING_INVALID = "IMPORTS.FILE.ENCODING_INVALID"
    #: 첫 행(컬럼 제목)이 표준 양식과 다르다 — 열 어긋난 채 파싱하지 않는다.
    IMPORTS_FILE_HEADER_MISMATCH = "IMPORTS.FILE.HEADER_MISMATCH"
    #: 데이터 행이 0건 — "올릴 것이 없었다"를 성공으로 착각하게 두지 않는다.
    IMPORTS_FILE_EMPTY = "IMPORTS.FILE.EMPTY"
    #: 같은 파일(해시 일치)이 이미 검토 대기 중이다 — ADR-09 파일 해시 멱등.
    IMPORTS_STAGING_DUPLICATE_PENDING = "IMPORTS.STAGING.DUPLICATE_PENDING"
    #: 확정·삭제는 검토 대기(PENDING) 상태에서만 가능하다.
    IMPORTS_STAGING_NOT_PENDING = "IMPORTS.STAGING.NOT_PENDING"
    #: 스테이징 후 확정 전에 대상 행이 먼저 수정·삭제됐다 — 부분 반영 없이
    #: 전체를 거부한다(사람이 검토한 diff와 다른 결과를 만들지 않는다).
    IMPORTS_CONFIRM_VERSION_CONFLICT = "IMPORTS.CONFIRM.VERSION_CONFLICT"
    #: 여신한도 등록·변경은 관리자만 — 무역 담당이 한도를 올린 뒤 확정하는 우회를 닫는다(S3-1 E9).
    PARTNERS_CREDIT_LIMIT_ADMIN_ONLY = "PARTNERS.CREDIT_LIMIT.ADMIN_ONLY"

    # 시장 (S2-1 / §5.1 / 판정 조건 6)
    #: 라벨·성분 규칙·HS 세번이 참조하는 시장이 등록돼 있지 않다 — FK가
    #: 마지막 안전망이고, 사용자 안내(선등록)는 이 코드로 나간다.
    MARKETS_MARKET_NOT_REGISTERED = "MARKETS.MARKET.NOT_REGISTERED"

    # 요건 템플릿 (S2-1 / §5.1·§5.5 / ADR-0033·0034 / GC-C8)
    #: 근거 2필드(근거링크·최종확인일) 없이 확정을 시도했다 — §5.5 확정 차단.
    #: DB CHECK(confirmed_requires_evidence)가 마지막 층이고 안내는 이 코드다.
    REQUIREMENTS_TEMPLATE_EVIDENCE_REQUIRED = "REQUIREMENTS.TEMPLATE.EVIDENCE_REQUIRED"
    #: 확정 상태 템플릿의 내용 편집 시도 — 편집은 초안 전환(명시 액션) 후에만
    #: 가능하고, 수정분은 재확정을 거친다(판정 조건 11 — 게이트 우회 차단).
    REQUIREMENTS_TEMPLATE_CONFIRMED_LOCKED = "REQUIREMENTS.TEMPLATE.CONFIRMED_LOCKED"
    #: 이미 확정된 템플릿의 재확정 시도(다른 멱등 키) — 같은 키는 최초 결과 재생.
    REQUIREMENTS_TEMPLATE_ALREADY_CONFIRMED = "REQUIREMENTS.TEMPLATE.ALREADY_CONFIRMED"
    #: 확정 상태가 아닌 템플릿의 초안 전환 시도 — 전환할 확정이 없다.
    REQUIREMENTS_TEMPLATE_NOT_CONFIRMED = "REQUIREMENTS.TEMPLATE.NOT_CONFIRMED"
    #: 초안이 아닌 템플릿의 확정 시도 — 확정은 DRAFT에서만 한다(#16 보완 판정).
    #: 폐기(RETIRED) 재활성은 초안 전환(근거 최신성 재검토) 경유가 유일 경로다.
    REQUIREMENTS_TEMPLATE_NOT_DRAFT = "REQUIREMENTS.TEMPLATE.NOT_DRAFT"
    #: 선행요건이 순환한다 — 자기참조(깊이 1)는 DB CHECK, 깊이 2+는 이 검증이
    #: 막는다(조건 1). 순환 그래프는 태스크 순서(S2-2)를 성립 불가로 만든다.
    REQUIREMENTS_PREREQUISITE_CYCLE = "REQUIREMENTS.PREREQUISITE.CYCLE"

    # 인증 인스턴스 (S2-2 / §5.1·§5.2 / s2-2-plan.md §0-1)
    #: 확정(CONFIRMED) 아닌 템플릿으로 인스턴스 생성 시도 — 3층 확정 게이트의
    #: 소비자다(안건 ①). RETIRED 템플릿도 신규 생성 불가(기존 인스턴스는 유지).
    CERTIFICATIONS_TEMPLATE_NOT_CONFIRMED = "CERTIFICATIONS.TEMPLATE.NOT_CONFIRMED"
    #: 대상 유형이 템플릿 적용단위와 다르다 — 폴리모픽 대상의 정합 강제(안건 ②).
    CERTIFICATIONS_TARGET_APPLIES_TO_MISMATCH = "CERTIFICATIONS.TARGET.APPLIES_TO_MISMATCH"
    #: 대상 실체(제품·SKU·성분·파트너)가 없다 — 폴리모픽이라 FK 대신 서비스 검증.
    CERTIFICATIONS_TARGET_NOT_FOUND = "CERTIFICATIONS.TARGET.NOT_FOUND"
    #: 전달 서류 묶음이 상한(파일 100건·합계 200MiB)을 넘는다 — 사용자가 나누어 받는다(S2-4 PR-2).
    CERTIFICATIONS_PACKAGE_TOO_LARGE = "CERTIFICATIONS.PACKAGE.TOO_LARGE"
    #: 묶음에 넣을 실물이 유실·손상됐다 — 빠진 채 나간 전달본이 가장 위험하므로 조용히 빼지 않고
    #: 목록과 함께 거부한다(S2-4 PR-2 안건 ④ (b)).
    CERTIFICATIONS_PACKAGE_FILES_UNAVAILABLE = "CERTIFICATIONS.PACKAGE.FILES_UNAVAILABLE"
    #: 전이 표 밖 상태 전이 시도 — §5.2 "전이 외 변경 거부"(허용 27방향뿐).
    CERTIFICATIONS_TRANSITION_NOT_ALLOWED = "CERTIFICATIONS.TRANSITION.NOT_ALLOWED"
    #: 반려·중단 전이에 사유가 없다 — §5.2 "사유 필수"(DB CHECK가 마지막 층).
    CERTIFICATIONS_TRANSITION_REASON_REQUIRED = "CERTIFICATIONS.TRANSITION.REASON_REQUIRED"
    #: 만료일 정정은 달력 파생 3태(승인·만료임박·만료)에서만 — 그 밖 상태의
    #: 만료일은 승인·갱신 전이의 부속 데이터로만 기록된다(조건 A 수렴과 세트).
    CERTIFICATIONS_EXPIRES_ON_STATE_LOCKED = "CERTIFICATIONS.EXPIRES_ON.STATE_LOCKED"

    # 전표 커널·견적 (S3-1 ADR-0051~0053 / design-integrated §2.4 — 도메인=소유 모듈명 대문자)
    #: 동결(발행·확정)에 필요한 값이 비어 있다 — detail에 항목별 한국어 안내(값·금액은 싣지 않는다).
    TRADE_DOCS_DOCUMENT_INCOMPLETE = "TRADE_DOCS.DOCUMENT.INCOMPLETE"
    #: 동결된 전표의 CONTENT·ORIGIN 열을 바꾸려 했다 — detail은 필드명 목록뿐(값·금액 미기재).
    TRADE_DOCS_DOCUMENT_FROZEN = "TRADE_DOCS.DOCUMENT.FROZEN"
    TRADE_DOCS_LINE_SKU_DISCONTINUED = "TRADE_DOCS.LINE.SKU_DISCONTINUED"
    TRADE_DOCS_LINE_SKU_DUPLICATE = "TRADE_DOCS.LINE.SKU_DUPLICATE"
    TRADE_DOCS_LINE_AMOUNT_OUT_OF_RANGE = "TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE"
    #: L/C 입력은 기능 플래그 `lc`가 켜졌을 때만(행 없음=꺼짐, fail-closed).
    TRADE_DOCS_PAYMENT_LC_DISABLED = "TRADE_DOCS.PAYMENT.LC_DISABLED"
    TRADE_DOCS_TRANSITION_NOT_ALLOWED = "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    TRADE_DOCS_TRANSITION_REASON_REQUIRED = "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    #: 복제 원본이 취소·만료 상태가 아니거나(개정은 발행 상태 QT만) 다른 거래처·유형의 전표다.
    TRADE_DOCS_COPY_SOURCE_NOT_ELIGIBLE = "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    #: 살아 있는 후속 전표가 있어 선행 전표를 취소할 수 없다(역순 취소만 — ADR-05). 개정 발행 거부 포함.
    TRADE_DOCS_CANCEL_SUCCESSOR_ALIVE = "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
    #: 잔량 초과(참조 생성·선적·입고 공용).
    TRADE_DOCS_QUANTITY_EXCEEDS_OPEN = "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    TRADE_DOCS_QUANTITY_DOCUMENT_NOT_CONSUMABLE = "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
    # PI·참조 생성 (S3-1 PR-6a / design-B B3·B7)
    #: 참조 원천(부모) 전표가 후속 생성에 쓸 수 없는 상태다(초안·취소·만료·삭제) — 원천 자격 검사.
    TRADE_DOCS_PARENT_NOT_USABLE = "TRADE_DOCS.PARENT.NOT_USABLE"
    #: 유효기간이 지난 견적·PI로는 후속 전표를 만들 수 없다 — 만료 스윕이 아직 안 돌았어도 `valid_until`을 직접 본다.
    TRADE_DOCS_VALIDITY_EXPIRED = "TRADE_DOCS.VALIDITY.EXPIRED"
    #: 취소·만료된 PI에는 입금 수렴을 할 수 없다(새 PI를 발행해야 한다) — fail-closed.
    TRADE_DOCS_PAYMENT_PI_NOT_OPEN = "TRADE_DOCS.PAYMENT.PI_NOT_OPEN"
    # SO 접수·참조 생성 2단 (S3-1 PR-7a / design-A A4·A13 / design-B B1·B3)
    #: 같은 바이어의 같은 PO번호(정규화 키)를 이미 비취소 SO가 점유하고 있다 — detail은 점유 문서번호·상태뿐(금액 미기재).
    TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    #: 이 PI(또는 원천)에서 이미 살아 있는 수주(SO)가 만들어졌다 — PI→SO는 활성 1:1이다(취소 후 재생성만 허용).
    TRADE_DOCS_REFERENCE_ALREADY_CONVERTED = "TRADE_DOCS.REFERENCE.ALREADY_CONVERTED"
    #: 보류 재개의 목표 상태가 보류 직전 상태와 다르다(RECEIVED↔CONFIRMED 뒤바꿈 금지) — `confirmed_at`이 원천.
    TRADE_DOCS_RESUME_TARGET_MISMATCH = "TRADE_DOCS.RESUME.TARGET_MISMATCH"

    # 동시성·멱등 (§17.2 / §17.4)
    CONCURRENCY_VERSION_CONFLICT = "COMMON.CONCURRENCY.VERSION_CONFLICT"
    #: 행 잠금 대기 초과(55P03)·교착(40P01) — 같은 전표·거래처를 다른 요청이
    #: 처리 중이다. 30초 초과 쿼리(57014)는 여기 매핑하지 않는다(문구가 거짓이 된다).
    CONCURRENCY_LOCK_BUSY = "COMMON.CONCURRENCY.LOCK_BUSY"

    # 입금 원장 (S3-1 PR-10a / ADR-0068)
    #: 입금 통화가 PI 통화와 다르다 — 환산은 하지 않는다(환율 원천 도입 시 재판정).
    PAYMENTS_PAYMENT_CURRENCY_MISMATCH = "PAYMENTS.PAYMENT.CURRENCY_MISMATCH"
    #: 순입금이 선수금 청구액(`split_advance`)을 넘는다 — 초과분은 잔금이며 S3-3 채권 입금으로 기록한다.
    PAYMENTS_PAYMENT_EXCEEDS_DUE = "PAYMENTS.PAYMENT.EXCEEDS_DUE"
    #: 선수금 T/T가 아닌 PI에는 S3-1 입금을 기록할 수 없다(잔금 입금은 S3-3 채권 몫).
    PAYMENTS_PAYMENT_PI_NOT_ADVANCE = "PAYMENTS.PAYMENT.PI_NOT_ADVANCE"
    #: 이미 역기록된 입금이다(한 입금은 한 번만 역기록).
    PAYMENTS_PAYMENT_ALREADY_REVERSED = "PAYMENTS.PAYMENT.ALREADY_REVERSED"
    #: 역기록 행 자체는 역기록할 수 없다(정정 = 역기록 후 재입금).
    PAYMENTS_PAYMENT_NOT_REVERSIBLE = "PAYMENTS.PAYMENT.NOT_REVERSIBLE"

    # 정책 설정 (S3-1 ADR-0065)
    POLICIES_POLICY_UNKNOWN_KEY = "POLICIES.POLICY.UNKNOWN_KEY"
    POLICIES_POLICY_INVALID_VALUE = "POLICIES.POLICY.INVALID_VALUE"

    # 승인 코어 (S3-1 ADR-0060·0061 / design-C C8) — 12종
    #: 결재선이 없어 승인 요청을 만들 수 없다(fail-closed) — 관리자가 결재선을 등록해야 한다.
    APPROVALS_LINE_NOT_CONFIGURED = "APPROVALS.LINE.NOT_CONFIGURED"
    #: 같은 (유형·통화)에 같은 임계의 결재선이 이미 있다.
    APPROVALS_LINE_DUPLICATE = "APPROVALS.LINE.DUPLICATE"
    #: 결재 자격자가 공집합이다(기안자 제외) — 조용한 정체 금지.
    APPROVALS_APPROVAL_NO_ELIGIBLE_APPROVER = "APPROVALS.APPROVAL.NO_ELIGIBLE_APPROVER"
    #: 대상에 이미 활성 승인이 있다(안전망 — 정상 경로는 대상 잠금이 직렬화한다).
    APPROVALS_APPROVAL_ALREADY_ACTIVE = "APPROVALS.APPROVAL.ALREADY_ACTIVE"
    #: 승인 없이 확정 시도(우회 차단) — ADMIN 포함.
    APPROVALS_APPROVAL_REQUIRED = "APPROVALS.APPROVAL.REQUIRED"
    #: 승인 후 대상이 바뀌었다(digest·통화·상한 불일치·더는 불필요) — 무효 처리 후 재승인이 필요하다.
    APPROVALS_APPROVAL_STALE = "APPROVALS.APPROVAL.STALE"
    APPROVALS_TRANSITION_NOT_ALLOWED = "APPROVALS.TRANSITION.NOT_ALLOWED"
    APPROVALS_TRANSITION_REASON_REQUIRED = "APPROVALS.TRANSITION.REASON_REQUIRED"
    APPROVALS_DECISION_NOT_APPROVER = "APPROVALS.DECISION.NOT_APPROVER"
    #: 기안자 본인은 결정할 수 없다(직무분리 — ADMIN 포함).
    APPROVALS_DECISION_SELF_APPROVAL = "APPROVALS.DECISION.SELF_APPROVAL"
    APPROVALS_DELEGATION_OVERLAP = "APPROVALS.DELEGATION.OVERLAP"
    APPROVALS_DELEGATION_NOT_ACTIVE = "APPROVALS.DELEGATION.NOT_ACTIVE"

    # 게이트 override (S3-1 PR-11a / ADR-0069)
    #: 이 역할로는 해당 게이트의 override를 부여·철회할 수 없다(가격·MOQ=무역·관리자, 준비도·PI=관리자).
    GATES_OVERRIDE_NOT_ALLOWED = "GATES.OVERRIDE.NOT_ALLOWED"
    #: override 대상이 아니다 — 해소 수단이 없는 결과(품번 매핑·중복 PO·여신)·이미 통과·경고뿐인 결과·철회할 부여 없음.
    GATES_OVERRIDE_NOT_APPLICABLE = "GATES.OVERRIDE.NOT_APPLICABLE"
    #: 사람이 본 판정이 낡았다(판정 해시 불일치) — 입력이 바뀌었으니 다시 확인해야 한다.
    GATES_OVERRIDE_STALE = "GATES.OVERRIDE.STALE"
    #: 이미 유효한 부여가 있다(같은 판정 해시) — 중복 부여 금지.
    GATES_OVERRIDE_ALREADY_GRANTED = "GATES.OVERRIDE.ALREADY_GRANTED"
    #: 철회할 유효한 부여가 없다(없거나 이미 철회됨).
    GATES_OVERRIDE_NOT_GRANTED = "GATES.OVERRIDE.NOT_GRANTED"
    #: 접수(RECEIVED) 상태가 아닌 수주에는 부여·철회할 수 없다.
    GATES_OVERRIDE_ORDER_NOT_OPEN = "GATES.OVERRIDE.ORDER_NOT_OPEN"

    # 수주 확정 (S3-1 PR-12a / ADR-0070)
    #: 미해소 게이트가 있어 확정할 수 없다 — 응답 detail의 `blocked_gates[]`가 게이트별 결과·해소 방식·사유 코드를 싣는다(승인 필요·평가 불능·PI 입금 부족·가격 편차 등 전부 이 코드 하나).
    TRADE_CHAIN_CONFIRM_GATE_BLOCKED = "TRADE_CHAIN.CONFIRM.GATE_BLOCKED"

    IDEMPOTENCY_KEY_CONFLICT = "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    IDEMPOTENCY_KEY_REQUIRED = "COMMON.IDEMPOTENCY.KEY_REQUIRED"

    # 트랜잭션 경계 (§17.1)
    TRANSACTION_BOUNDARY_VIOLATION = "COMMON.TRANSACTION.BOUNDARY_VIOLATION"

    # 외부 연동 (§17.6)
    EXTERNAL_TIMEOUT = "COMMON.EXTERNAL.TIMEOUT"
    EXTERNAL_UNAVAILABLE = "COMMON.EXTERNAL.UNAVAILABLE"

    # 최후
    INTERNAL_UNEXPECTED = "COMMON.INTERNAL.UNEXPECTED"
