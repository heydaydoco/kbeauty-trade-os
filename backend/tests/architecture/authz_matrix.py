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
    "/api/v1/purchase-orders",
    # S3-1 PR-9a — 승인 코어(결재선·승인·대결). 승인 요청 생성(`POST /approvals`)은 의도적으로 존재하지 않는다(PR-12가 SO 엔드포인트로 노출).
    "/api/v1/approval-lines",
    "/api/v1/approvals",
    "/api/v1/delegations",
    # S3-1 PR-10a — 입금 원장(`/proforma-invoices/{id}/payments`는 위 PI 접두어가 이미 통제한다).
    "/api/v1/payments",
    # S3-1 PR-13a — 오더 인테이크(등록·편집·재해석·거부=order_intake 라우터, 확정·게이트 조회=trade_chain 라우터). CSV 입구(import-csv·template.csv)는 PR-14a가 행을 더했다.
    "/api/v1/order-intakes",
    # S3-1 PR-15a — 오더 보드(보드·드릴다운·CSV=전 역할, 벌크=무역·관리자, 저장 필터=전 역할·본인 것만[당사자성은 서비스 404]).
    "/api/v1/order-board",
    # S3-2 PR-2a — 휴일 캘린더(조회 = 전 역할, 원자 교체·CSV 미리보기 = 관리자 전용 — ADR-0079·0082).
    "/api/v1/holidays",
    # S3-2 PR-3a — 선적(조회 = 전 역할, 라인·취소 = 무역, 헤더·출고지시·당사자 = 무역 + **물류**[첫 전표 쓰기 — ADR-0079]).
    # PR-3c의 CSV(`/shipments/export.csv` — 전 역할)도 이 접두어가 통제한다(행만 추가).
    # SO 하위 참조 생성(`/sales-orders/{so_id}/shipments[/preview]`)은 위 SO 접두어가 통제한다(행만 추가).
    "/api/v1/shipments",
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
    # S3-1 PR-10a — 입금 원장: 열람은 전 역할(원가·마진 아님), 기록·역기록은 무역+관리자(E7).
    ("GET", "/api/v1/proforma-invoices/{pi_id}/payments"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("POST", "/api/v1/proforma-invoices/{pi_id}/payments"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/payments/{payment_id}/reversal"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
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
    # S3-1 PR-11a — 게이트: 판정 조회는 전 역할(여신 수치는 응답 본문에서 무역·관리자만 — test_gate_api), override 부여·철회는 라우트 상한이 무역(관리자 상시 통과)이고
    # 게이트별 역할(가격·MOQ=무역·관리자, 준비도·PI=관리자)은 **서비스가 판정**한다(SERVICE_GATED — 라우트 프로브는 상한만 본다, 서비스 판정은 test_gate_override_api).
    ("GET", "/api/v1/sales-orders/{so_id}/gates"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    # S3-1 PR-12a — 확정·여신 초과 승인 요청(무역·관리자 — 게이트·승인 통과는 서비스가 증거로 판정하고 관리자도 우회 못 한다)
    ("POST", "/api/v1/sales-orders/{so_id}/confirm"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/sales-orders/{so_id}/approval-requests"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/sales-orders/{so_id}/gate-overrides"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/sales-orders/{so_id}/gate-overrides/revoke"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # S3-1 PR-13a — 오더 인테이크. 조회·게이트 평가는 전 역할(원가·마진 필드 없음), 쓰기(등록·편집·재해석·거부·확정)는 무역(관리자 상시 통과) — 서비스가 역할을 한 번 더 확인한다.
    ("GET", "/api/v1/order-intakes"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/order-intakes/{intake_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("GET", "/api/v1/order-intakes/{intake_id}/gates"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("POST", "/api/v1/order-intakes"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    # S3-1 PR-14a — CSV 입구. 양식 다운로드·업로드 모두 무역(관리자 상시 통과) — 양식은 업로드할 수 있는 역할만(적대 검토 판정: 더 엄격한 쪽 자율 확정,
    # design-D D7 "CSV=전 역할"보다 좁힘). 업로드는 서비스가 역할을 한 번 더 확인한다.
    ("GET", "/api/v1/order-intakes/template.csv"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/order-intakes/import-csv"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/order-intakes/{intake_id}"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/order-intakes/{intake_id}/resolve"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/order-intakes/{intake_id}/reject"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/order-intakes/{intake_id}/confirm"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # S3-1 PR-8a — PO. **조회는 전 역할**이되 VIEWER는 원가·통화 필드가 없는 응답(200, ADR-0024 필드 부재 — 본문 분기는 test_po_cost_masking·G 그룹),
    # 생성(=발행=발주 확정)·미리보기·전이(OC·취소)·메타는 무역(관리자 상시 통과) — PO 주체=TRADE(설계 F8). 나머지 역할은 전부 403.
    ("GET", "/api/v1/purchase-orders"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/purchase-orders/export.csv"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("GET", "/api/v1/purchase-orders/{po_id}"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/purchase-orders/{po_id}/status-log"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("POST", "/api/v1/purchase-orders"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/purchase-orders/preview"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/purchase-orders/{po_id}/meta"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/purchase-orders/{po_id}/transitions"): {
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
    # S3-1 PR-9a — 승인 코어. VIEWER는 전면 403(여신 초과액 노출 재판정을 열지 않는 가장 좁은 결정 — C4). 결재 **자격**(역할·SoD·대결)은 서비스가
    # 판정하므로 이 표는 라우트 게이트만 다룬다: ADMIN은 `require_roles`를 통과하되 자기 기안은 서비스가 막는다(test_approval_core 서비스 층).
    ("GET", "/api/v1/approval-lines"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("GET", "/api/v1/approval-lines/coverage"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("POST", "/api/v1/approval-lines"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("PATCH", "/api/v1/approval-lines/{line_id}"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("DELETE", "/api/v1/approval-lines/{line_id}"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("GET", "/api/v1/approvals"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("GET", "/api/v1/approvals/inbox-count"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("GET", "/api/v1/approvals/delegation-candidates"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: DENY,
    },
    ("GET", "/api/v1/approvals/{approval_id}"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("GET", "/api/v1/approvals/{approval_id}/events"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: DENY,
    },
    ("POST", "/api/v1/approvals/{approval_id}/decisions"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: DENY,
    },
    ("GET", "/api/v1/delegations"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("POST", "/api/v1/delegations"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: DENY},
    ("POST", "/api/v1/delegations/{delegation_id}/revoke"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: DENY,
    },
    # S3-1 PR-15a — 오더 보드(ADR-0066·0067). 조회·CSV는 전 역할(카드에 원가·마진·여신·게이트 필드 없음), 벌크는 무역(관리자 상시 통과 —
    # 각 건은 단일 통로가 역할·게이트·승인을 다시 판정), 저장 필터는 전 역할(개인 설정 — 타인 id는 서비스가 404).
    ("GET", "/api/v1/order-board"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/order-board/items"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/order-board/export.csv"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("POST", "/api/v1/order-board/bulk"): {A: ALLOW, T: ALLOW, L: DENY, C: DENY, V: DENY},
    ("GET", "/api/v1/order-board/saved-filters"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("POST", "/api/v1/order-board/saved-filters"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("PATCH", "/api/v1/order-board/saved-filters/{filter_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("DELETE", "/api/v1/order-board/saved-filters/{filter_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    # S3-2 PR-2a — 휴일 캘린더. 조회(선언 목록·휴일 목록·CSV)는 전 역할, 쓰기(원자 교체)·CSV 미리보기는 관리자 전용(ADR-0079 — 기한 데이터 관리 주체 확대 금지).
    ("GET", "/api/v1/holidays/calendars"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/holidays"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/holidays/{country}/{year}/export.csv"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("PUT", "/api/v1/holidays/{country}/{year}"): {A: ALLOW, T: DENY, L: DENY, C: DENY, V: DENY},
    ("POST", "/api/v1/holidays/{country}/{year}/import-csv/preview"): {
        A: ALLOW,
        T: DENY,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    # S3-2 PR-3a — 선적(design-integrated §2.9 / ADR-0079). 생성·미리보기·라인·취소 = 무역(SO 잔량 소비·SO 수렴 = 상업 사실),
    # 헤더(메모·담당·국가)·출고지시·당사자 = 무역 + 물류(물류 첫 전표 쓰기), 조회 = 전 역할(원가 필드 없음). 인증·조회 전용은 쓰기 0.
    ("POST", "/api/v1/sales-orders/{so_id}/shipments/preview"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/sales-orders/{so_id}/shipments"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("GET", "/api/v1/shipments"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    # S3-2 PR-3c — 선적 목록 CSV(S20): 전 역할 같은 헤더(원가·단가 열 0 — 역할별 분기 없음, design-C C8).
    ("GET", "/api/v1/shipments/export.csv"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/shipments/{shipment_id}"): {A: ALLOW, T: ALLOW, L: ALLOW, C: ALLOW, V: ALLOW},
    ("GET", "/api/v1/shipments/{shipment_id}/status-log"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: ALLOW,
        V: ALLOW,
    },
    ("PATCH", "/api/v1/shipments/{shipment_id}"): {A: ALLOW, T: ALLOW, L: ALLOW, C: DENY, V: DENY},
    ("POST", "/api/v1/shipments/{shipment_id}/lines"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("PATCH", "/api/v1/shipments/{shipment_id}/lines/{line_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("DELETE", "/api/v1/shipments/{shipment_id}/lines/{line_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/shipments/{shipment_id}/release-order"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/shipments/{shipment_id}/transitions"): {
        A: ALLOW,
        T: ALLOW,
        L: DENY,
        C: DENY,
        V: DENY,
    },
    ("POST", "/api/v1/shipments/{shipment_id}/parties"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: DENY,
        V: DENY,
    },
    ("DELETE", "/api/v1/shipments/{shipment_id}/parties/{party_id}"): {
        A: ALLOW,
        T: ALLOW,
        L: ALLOW,
        C: DENY,
        V: DENY,
    },
}
