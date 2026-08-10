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
    #: 선행요건이 순환한다 — 자기참조(깊이 1)는 DB CHECK, 깊이 2+는 이 검증이
    #: 막는다(조건 1). 순환 그래프는 태스크 순서(S2-2)를 성립 불가로 만든다.
    REQUIREMENTS_PREREQUISITE_CYCLE = "REQUIREMENTS.PREREQUISITE.CYCLE"

    # 동시성·멱등 (§17.2 / §17.4)
    CONCURRENCY_VERSION_CONFLICT = "COMMON.CONCURRENCY.VERSION_CONFLICT"
    IDEMPOTENCY_KEY_CONFLICT = "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    IDEMPOTENCY_KEY_REQUIRED = "COMMON.IDEMPOTENCY.KEY_REQUIRED"

    # 트랜잭션 경계 (§17.1)
    TRANSACTION_BOUNDARY_VIOLATION = "COMMON.TRANSACTION.BOUNDARY_VIOLATION"

    # 외부 연동 (§17.6)
    EXTERNAL_TIMEOUT = "COMMON.EXTERNAL.TIMEOUT"
    EXTERNAL_UNAVAILABLE = "COMMON.EXTERNAL.UNAVAILABLE"

    # 최후
    INTERNAL_UNEXPECTED = "COMMON.INTERNAL.UNEXPECTED"
