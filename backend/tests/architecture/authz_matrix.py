"""역할 권한 매트릭스 — S3-1 전 엔드포인트의 기계 정본 (ADR-0067 / 설계 F8).

행 = (메서드, 경로 템플릿). 값 = 역할별 기대: "allow"(라우트 게이트 통과 — 상태는 401·403이
아니면 된다) 또는 "deny"(403). 새 라우터를 추가하는 PR은 이 표에 행을 **같이** 추가한다 —
없으면 test_authz_matrix.py의 완비성 검사가 실패한다.

ADMIN은 `require_roles`에서 자동 통과하지만 업무 게이트(승인·게이트)는 통과가 아니다(C4) —
그 구분은 서비스 층 테스트의 몫이고 이 표는 **라우트 게이트**만 다룬다.
"""

from __future__ import annotations

from app.modules.identity.models import RoleCode

A = RoleCode.ADMIN
T = RoleCode.TRADE
L = RoleCode.LOGISTICS
C = RoleCode.CERT
V = RoleCode.VIEWER

ALLOW = "allow"
DENY = "deny"

#: 이 접두어 아래의 모든 (메서드, 경로)는 표에 있어야 한다. 소비 PR이 자기 접두어를 추가한다.
GOVERNED_PREFIXES: tuple[str, ...] = (
    "/api/v1/users/lookup",
    "/api/v1/policies",
    "/api/v1/quotations",
    "/api/v1/proforma-invoices",
    "/api/v1/bank-accounts",
    "/api/v1/sales-orders",
    "/api/v1/document-flow",
)

EXPECTED: dict[tuple[str, str], dict[RoleCode, str]] = {
    # F9 — 담당자·수임자 선택기(표시명만)
    ("GET", "/api/v1/users/lookup"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    # E8 — 정책 설정은 관리자 전용(조회·저장 모두). 게이트 응답이 실효값·출처를 전 역할에 읽기로 싣는다.
    ("GET", "/api/v1/policies"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("PUT", "/api/v1/policies/{policy_key}"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    # S3-1 PR-5a — 견적. 조회는 전 역할(원가·마진 필드 없음), 쓰기·전이는 무역(관리자 상시 통과).
    ("GET", "/api/v1/quotations"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/quotations/export.csv"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/quotations/{qt_id}"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/quotations/{qt_id}/status-log"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("POST", "/api/v1/quotations"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/quotations/{qt_id}"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/quotations/{qt_id}/meta"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/quotations/{qt_id}/lines"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/quotations/{qt_id}/lines/{line_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("DELETE", "/api/v1/quotations/{qt_id}/lines/{line_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/quotations/{qt_id}/issue"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/quotations/{qt_id}/transitions"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/quotations/{qt_id}/revisions"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # S3-1 PR-6a — QT→PI 참조 생성·미리보기는 무역(관리자 상시 통과) 쓰기.
    ("POST", "/api/v1/quotations/{qt_id}/proforma-invoices"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/quotations/{qt_id}/proforma-invoices/preview"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # PI — 조회는 전 역할(원가·마진 필드 없음), 쓰기·취소는 무역(관리자 상시 통과).
    ("GET", "/api/v1/proforma-invoices"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/proforma-invoices/export.csv"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("GET", "/api/v1/proforma-invoices/{pi_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("GET", "/api/v1/proforma-invoices/{pi_id}/status-log"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("PATCH", "/api/v1/proforma-invoices/{pi_id}/meta"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/proforma-invoices/{pi_id}/transitions"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # 은행 계좌 — 쓰기는 ADMIN 전용, 조회는 ADMIN·TRADE(PI 선택용), 나머지 403(설계 §2.7).
    ("GET", "/api/v1/bank-accounts"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("GET", "/api/v1/bank-accounts/export.csv"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("GET", "/api/v1/bank-accounts/{account_id}"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/bank-accounts"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/bank-accounts/{account_id}"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("DELETE", "/api/v1/bank-accounts/{account_id}"): {
        A: ALLOW,
        T: DENY,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # S3-1 PR-7a — QT/PI→SO 참조 생성은 무역(관리자 상시 통과) 쓰기. SO 조회는 전 역할(원가·마진 필드 없음).
    ("POST", "/api/v1/quotations/{qt_id}/sales-orders"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/proforma-invoices/{pi_id}/sales-orders"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("GET", "/api/v1/sales-orders"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/sales-orders/export.csv"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/sales-orders/{so_id}"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/sales-orders/{so_id}/status-log"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("PATCH", "/api/v1/sales-orders/{so_id}"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/sales-orders/{so_id}/meta"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/sales-orders/{so_id}/lines"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/sales-orders/{so_id}/lines/{line_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("DELETE", "/api/v1/sales-orders/{so_id}/lines/{line_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/sales-orders/{so_id}/transitions"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("GET", "/api/v1/document-flow/{doc_kind}/{doc_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
}
