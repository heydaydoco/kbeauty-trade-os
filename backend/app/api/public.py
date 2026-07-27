"""인증 없이 부를 수 있는 경로 — **이 목록이 유일한 출처다** (DESIGN.md §18.1).

§18.1은 "모든 엔드포인트가 역할+소유권 검증"을 요구한다. 그런데 인증을
엔드포인트마다 붙이는 방식은 반드시 새어 나간다 — 새 라우터를 만든 사람이
의존성 한 줄을 안 붙이면 그 경로는 조용히 공개된다.

그래서 뒤집는다. **기본은 인증 필요**이고, 공개하려면 여기 적어야 한다.
tests/architecture/test_auth_coverage.py가 앱의 전 라우트를 훑어
"여기 없는데 인증 의존성도 없는 경로"가 있으면 실패시킨다.
"""

from __future__ import annotations

#: 인증 없이 접근 가능한 경로 (전체 경로, /api/v1 접두 포함).
#:
#: healthz/readyz — 컨테이너·로드밸런서가 부른다. 401을 받으면 컨테이너가
#:   계속 재시작한다(§14 각주의 인프라 헬스체크).
#: auth/login — 로그인하려면 로그인 없이 부를 수 있어야 한다.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/system/healthz",
        "/api/v1/system/readyz",
        "/api/v1/auth/login",
    }
)

#: FastAPI가 자동으로 만드는 문서·스키마 경로. 운영에서의 노출 여부는 별도
#: 결정이며(Phase 1), 인증 커버리지 검사 대상에서만 제외한다.
DOC_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path in DOC_PATHS
