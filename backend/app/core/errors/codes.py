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

    # 오더 인테이크 (S3-1 PR-13a / ADR-0071) — 중복 바이어 PO는 `TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO`(PENDING 인테이크·SO 공용, 통합 X-40)를 쓴다.
    #: 대기(PENDING) 상태가 아닌 인테이크는 수정·거부·확정할 수 없다(확정·거부는 종결 — 탈출 없음).
    ORDER_INTAKE_STATE_NOT_PENDING = "ORDER_INTAKE.STATE.NOT_PENDING"
    #: 접수 확정에 쓸 수 없는 라인이 있다 — 바이어 품번 미매핑·삭제된 SKU·바이어 유형 상실(품번 매핑을 등록·수정한 뒤 다시 확인).
    ORDER_INTAKE_LINE_UNMAPPED_ITEMS = "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"
    #: 검토한 뒤 품번 매핑이 바뀌었다 — 다시 해석(resolve)해 검토한 뒤 확정해야 한다.
    ORDER_INTAKE_LINE_STALE_MAPPING = "ORDER_INTAKE.LINE.STALE_MAPPING"
    #: 같은 SKU의 유상 라인이 둘 이상이다(수주 라인은 SKU당 유상 1줄) — 라인 번호 목록을 detail로 준다.
    ORDER_INTAKE_LINE_DUPLICATE_SKU = "ORDER_INTAKE.LINE.DUPLICATE_SKU"
    #: 인테이크 라인 수 상한(200) 초과 또는 라인 없음.
    ORDER_INTAKE_LINE_LIMIT_EXCEEDED = "ORDER_INTAKE.LINE.LIMIT_EXCEEDED"
    #: 접수 확정의 하드 게이트를 평가하지 못했다(평가 불능은 통과가 아니다 — fail-closed).
    ORDER_INTAKE_GATE_UNRESOLVED = "ORDER_INTAKE.GATE.UNRESOLVED"

    # 오더 인테이크 CSV 입구 (S3-1 PR-14a / design-D D2) — 파일 전체 원자(한 행이라도 오류면 전부 거부).
    #: 같은 파일(sha256)이 이미 검토 대기(PENDING) 인테이크로 올라와 있다 — detail `intake_ids`(기존 인테이크 id 목록)로 그 건을 처리한 뒤 다시 올린다.
    ORDER_INTAKE_FILE_DUPLICATE = "ORDER_INTAKE.FILE.DUPLICATE"
    #: 파일의 행·셀 오류가 있어 **아무것도 등록하지 않았다** — detail `errors[{row_no, column, code, message_ko}]`(상한까지, 중복 PO·마스터 오류 우선)+`total_errors`·`omitted_errors`·`counts_by_code`.
    ORDER_INTAKE_FILE_INVALID_ROWS = "ORDER_INTAKE.FILE.INVALID_ROWS"
    #: 한 파일이 만들 수 있는 인테이크(바이어 PO) 수 상한을 넘었다 — detail `{groups, max_groups}`(상한 값은 `csv_template.MAX_GROUPS` 단일 출처).
    ORDER_INTAKE_FILE_TOO_MANY_GROUPS = "ORDER_INTAKE.FILE.TOO_MANY_GROUPS"
    #: 엑셀(.xlsx·.xls) 파일은 받지 않는다 — 엑셀에서 'CSV UTF-8'로 저장해 올린다.
    ORDER_INTAKE_FILE_UNSUPPORTED_FORMAT = "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT"

    IDEMPOTENCY_KEY_CONFLICT = "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    IDEMPOTENCY_KEY_REQUIRED = "COMMON.IDEMPOTENCY.KEY_REQUIRED"

    # 트랜잭션 경계 (§17.1)
    TRANSACTION_BOUNDARY_VIOLATION = "COMMON.TRANSACTION.BOUNDARY_VIOLATION"

    # 외부 연동 (§17.6)
    EXTERNAL_TIMEOUT = "COMMON.EXTERNAL.TIMEOUT"
    EXTERNAL_UNAVAILABLE = "COMMON.EXTERNAL.UNAVAILABLE"

    # 오더 보드 (S3-1 PR-15a / ADR-0066) — 벌크 건별 결과는 단일 통로의 코드를 그대로 싣고, 보드 자체의 거부만 여기 둔다.
    #: 벌크 대상이 상한(50건)을 넘는다 — 나눠서 처리해야 한다.
    ORDER_BOARD_BULK_TOO_MANY = "ORDER_BOARD.BULK.TOO_MANY"
    #: 저장 필터가 사용자당 상한(20개)에 닿았다 — 안 쓰는 필터를 지운 뒤 저장한다.
    ORDER_BOARD_FILTER_LIMIT_REACHED = "ORDER_BOARD.FILTER.LIMIT_REACHED"
    #: 같은 이름의 저장 필터가 이미 있다(본인 것 안에서 이름 유일).
    ORDER_BOARD_FILTER_DUPLICATE_NAME = "ORDER_BOARD.FILTER.DUPLICATE_NAME"

    # 휴일 캘린더 (S3-2 PR-2a / ADR-0082 / design-integrated §2.6·X-22) — 쓰기 통로는 PUT /holidays/{c}/{y} 하나(ADMIN).
    #: 연도 선언의 근거 링크(http/https)·확인일(오늘 이전)이 없다 — 근거 없는 휴일 데이터는 받지 않는다(ADR-03).
    HOLIDAYS_CALENDAR_SOURCE_REQUIRED = "HOLIDAYS.CALENDAR.SOURCE_REQUIRED"
    #: 휴일 날짜의 연도가 선언 연도와 다르다(서비스 선검증 + DB 복합 FK·CHECK 번역 — R-24).
    HOLIDAYS_CALENDAR_YEAR_MISMATCH = "HOLIDAYS.CALENDAR.YEAR_MISMATCH"
    #: 한 요청 본문(또는 CSV)에 같은 날짜가 두 번 있다 — 입력 오류(422).
    HOLIDAYS_CALENDAR_DUPLICATE_DATE = "HOLIDAYS.CALENDAR.DUPLICATE_DATE"
    #: 같은 국가·연도를 동시에 처음 선언했다(부분 유니크 경합) — 다시 불러온 뒤 시도한다(409).
    HOLIDAYS_CALENDAR_YEAR_DUPLICATE = "HOLIDAYS.CALENDAR.YEAR_DUPLICATE"
    #: 국가 코드가 ISO 3166-1 alpha-2 대문자 두 글자가 아니다.
    HOLIDAYS_COUNTRY_INVALID = "HOLIDAYS.COUNTRY.INVALID"
    #: 휴일 CSV의 인코딩·머리글·크기·문법이 양식과 다르다(파일 단위 거부 — 행 문제는 미리보기 problems로).
    HOLIDAYS_CSV_INVALID_FORMAT = "HOLIDAYS.CSV.INVALID_FORMAT"

    # 선적 (S3-2 PR-3a / ADR-0074 / design-integrated §2.6 — 이 PR이 쓰는 7종. 나머지 19종은 소비 PR[4a·5a]이 쓰면서 더한다 — 죽은 코드 금지)
    #: 본문의 원천 라인이 경로의 원천 전표 소속이 아니다(없는·삭제된 라인 포함 — 존재 여부를 알려 주지 않는다, X-12).
    SHIPMENTS_SOURCE_LINE_MISMATCH = "SHIPMENTS.SOURCE.LINE_MISMATCH"
    #: 취소된 선적에는 당사자를 더하거나 지울 수 없다.
    SHIPMENTS_SHIPMENT_NOT_ACTIVE = "SHIPMENTS.SHIPMENT.NOT_ACTIVE"
    #: 한 선적에 같은 원천 라인이 이미 있다(부분 유니크 번역 — 수량을 고친다).
    SHIPMENTS_LINE_DUPLICATE_SOURCE = "SHIPMENTS.LINE.DUPLICATE_SOURCE"
    #: 선적의 마지막 라인은 지울 수 없다(라인 0건 선적 = SO 수렴 불변식 위반 — 선적 취소로 안내).
    SHIPMENTS_LINE_LAST_LINE = "SHIPMENTS.LINE.LAST_LINE"
    #: (선적, 역할) 살아 있는 당사자가 이미 있다(부분 유니크 번역).
    SHIPMENTS_PARTY_ROLE_DUPLICATE = "SHIPMENTS.PARTY.ROLE_DUPLICATE"
    #: 수출 SHIPPER·CONSIGNEE(자사·자동 스냅샷)는 직접 추가·삭제할 수 없다.
    SHIPMENTS_PARTY_ROLE_NOT_ALLOWED = "SHIPMENTS.PARTY.ROLE_NOT_ALLOWED"
    #: 당사자 거래처의 영문명이 비어 있다(서류 영문 원천 결측 — 거래처 화면에서 보완, fail-visible).
    SHIPMENTS_PARTY_ENGLISH_NAME_MISSING = "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING"

    # 선적 마일스톤·통관 (S3-2 PR-4a / ADR-0080·0083 / design-integrated §2.6·§9 R-01·R-18·R-26 — 이 PR이 쓰는 17종.
    # 남은 2종[MILESTONE.OWNER_NOT_OEM·QUANTITY.EXCEEDS_ASSIGNABLE]은 소비 PR[4c·5a]이 더한다 — 죽은 코드 금지.
    # PR-4c가 OWNER_NOT_OEM을 더했다 — 아래 마일스톤 묶음 끝)
    #: 살아 있는 통관 기록이 있는 선적은 취소할 수 없다(역순 원칙의 사실 기록판 — 통관 기록을 사유와 함께 먼저 삭제).
    SHIPMENTS_SHIPMENT_CUSTOMS_RECORD_ALIVE = "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE"
    #: ETD·B/L 발행·ETA 실적이 살아 있는 선적은 취소할 수 없다(R-01 — 실적을 사유와 함께 정정·삭제한 뒤 취소).
    SHIPMENTS_SHIPMENT_ACTUAL_RECORDED = "SHIPMENTS.SHIPMENT.ACTUAL_RECORDED"
    #: (신고 구분, 신고번호) 살아 있는 통관 기록이 이미 있다(부분 유니크 번역).
    SHIPMENTS_CUSTOMS_DECLARATION_DUPLICATE = "SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE"
    #: 신고 구분이 선적 구분과 다르다(수출선적 = 수출신고, 수입선적 = 수입신고).
    SHIPMENTS_CUSTOMS_KIND_MISMATCH = "SHIPMENTS.CUSTOMS.KIND_MISMATCH"
    #: 통관 기록의 신고번호·신고일·수리일 정정이나 삭제에 사유가 없다.
    SHIPMENTS_CUSTOMS_REASON_REQUIRED = "SHIPMENTS.CUSTOMS.REASON_REQUIRED"
    #: 신고일·수리일이 오늘(KST)보다 뒤다(R-18 — 여유 0).
    SHIPMENTS_CUSTOMS_DATE_IN_FUTURE = "SHIPMENTS.CUSTOMS.DATE_IN_FUTURE"
    #: 수리일이 신고일보다 앞이다(서비스 선검증 + CHECK 번역 — R-26).
    SHIPMENTS_CUSTOMS_ACCEPT_BEFORE_DECLARE = "SHIPMENTS.CUSTOMS.ACCEPT_BEFORE_DECLARE"
    #: (소유자, 종류) 살아 있는 마일스톤 행·(품목군, 종류) 세트 행이 이미 있다(부분 유니크 번역 — 동시 최초 입력 경합, 세트 중복 — R-26).
    SHIPMENTS_MILESTONE_DUPLICATE_TYPE = "SHIPMENTS.MILESTONE.DUPLICATE_TYPE"
    #: 파생 마일스톤(적재기한·대금만기·제시기한)은 직접 쓸 수 없다(덮어쓰기 금지 — 입력 값을 고친다).
    SHIPMENTS_MILESTONE_DERIVED_NOT_EDITABLE = "SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE"
    #: 실적이 미래다(날짜형 > KST 오늘+1일, 시각형 > 현재 UTC 시각 — R-18).
    SHIPMENTS_MILESTONE_ACTUAL_IN_FUTURE = "SHIPMENTS.MILESTONE.ACTUAL_IN_FUTURE"
    #: 롤오버(계획 변경)·실적 정정에 사유가 없다.
    SHIPMENTS_MILESTONE_REASON_REQUIRED = "SHIPMENTS.MILESTONE.REASON_REQUIRED"
    #: 선적 구분(수출·수입)에 적용되지 않는 종류다(예: 수입선적의 수출 전 검사, 선적의 OEM 생산 종류).
    #: PR-4c 재사용(R-26): OEM 발주에 선적 종류, 품목군 세트에 파생·OEM 종류.
    SHIPMENTS_MILESTONE_TYPE_NOT_APPLICABLE = "SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE"
    #: 취소된 선적·PO(소유자)의 마일스톤은 쓸 수 없다(N-05 — 선적·OEM 발주 공통 1코드). 카탈로그 문구는 소유자 중립이고, 경로가
    #: 소유자별 조치(선적 = 수주에서 새 선적 / 발주 = 새 발주)를 문구로 덮고 detail.owner_type을 싣는다(PR-4c 적대 검토 반영 ③).
    SHIPMENTS_MILESTONE_OWNER_NOT_ACTIVE = "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
    #: 신고수리 실적은 마일스톤에 직접 쓰지 않는다 — 통관 기록의 수리일이 유일 원천이다(X-02).
    SHIPMENTS_MILESTONE_ACTUAL_FROM_CUSTOMS_RECORD = (
        "SHIPMENTS.MILESTONE.ACTUAL_FROM_CUSTOMS_RECORD"
    )
    #: IANA 시간대 이름이 아니다(시각형 마일스톤 — N-04).
    SHIPMENTS_MILESTONE_TIMEZONE_INVALID = "SHIPMENTS.MILESTONE.TIMEZONE_INVALID"
    #: 날짜형 종류에 시각 값(또는 그 반대)을 보냈다(N-04).
    SHIPMENTS_MILESTONE_VALUE_SHAPE_MISMATCH = "SHIPMENTS.MILESTONE.VALUE_SHAPE_MISMATCH"
    #: ETD·B/L 발행·ETA 실적은 출고지시 뒤에만 기록한다(R-01 — 계획 단계 선적에 실적 422).
    SHIPMENTS_MILESTONE_ACTUAL_BEFORE_RELEASE = "SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE"
    #: 변경 1건에 연결할 수 있는 통보 기록 수를 넘었다(상한 — ORDER_BOARD.FILTER.LIMIT_REACHED 선례, PR-4a 적대 검토 반영 ⑧).
    SHIPMENTS_MILESTONE_NOTICE_LIMIT_REACHED = "SHIPMENTS.MILESTONE.NOTICE_LIMIT_REACHED"
    #: OEM 생산 마일스톤(원료수급·충진·포장·출하검사)은 OEM 생산 발주(`po_kind=OEM_PRODUCTION`)에만 있다(S3-2 PR-4c / design-B B15).
    SHIPMENTS_MILESTONE_OWNER_NOT_OEM = "SHIPMENTS.MILESTONE.OWNER_NOT_OEM"

    # 최후
    INTERNAL_UNEXPECTED = "COMMON.INTERNAL.UNEXPECTED"
