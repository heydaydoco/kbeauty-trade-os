"""테이블 변경 가능성 분류 (DESIGN.md §17.5).

§17.5는 stock_movements·확정 분개·audit_log에 대해 앱 계정의 UPDATE/DELETE
권한을 제거하라고 한다. 문제는 **누락이 조용하다는 것**이다 — 권한을 안 뺏은
테이블은 그냥 잘 동작하고, 사고가 난 뒤에야 드러난다.

그래서 모든 테이블을 둘 중 하나로 분류하도록 강제하고, 분류되지 않은 테이블이
하나라도 있으면 테스트가 실패한다. 새 테이블을 만든 사람이 "이건 불변인가?"를
반드시 한 번 생각하게 만드는 장치다.

WBS 배정: audit_log → S0-2 / stock_movements·확정 분개 → S4-1.
"""

from __future__ import annotations

from typing import Any

#: 앱 계정이 UPDATE/DELETE 할 수 없는 테이블 (INSERT/SELECT만).
#: 정정은 원본 수정이 아니라 반대 부호의 역기록으로 한다(ADR-05).
IMMUTABLE_TABLES: frozenset[str] = frozenset(
    {
        "audit_log",  # S0-2
        # S2-2 — 인증 상태 변경 이력 (§5.2 "이력 자동" / 판정 안건 ④).
        # §17.5 명시 3종 밖의 확장이다 — DESIGN §17.5 확장 명문+ADR-0040 세트.
        # 정정은 원본 수정이 아니라 새 전이 기록이다.
        "certification_status_log",
        # S3-1 — 전표 상태 변경 이력(ADR-0051·§17.5 확장). 전표별 1표이고 각 전표 PR이 자기 표를 더한다.
        # 정정은 원본 수정이 아니라 새 전이 기록이다.
        "quotation_status_log",
        "proforma_invoice_status_log",  # PR-6a — PI 상태 이력(같은 이유)
        "sales_order_status_log",  # PR-7a — SO 상태 이력(같은 이유)
        "purchase_order_status_log",  # PR-8a — PO 상태 이력(같은 이유)
        # S3-1 PR-9a — 승인 상태 변경 이력(ADR-0060·§17.5 확장). 시스템 행위자가 없고(actor NOT NULL) 정정은 새 전이 기록이다.
        "approval_events",
        # S3-1 PR-10a — 입금 원장(ADR-0068·§17.5 확장). 부호 있는 INSERT-only 원장이고 정정은 반대 부호의 신규 행(역기록, ADR-05)이다.
        "payments",
        # S3-1 PR-11a — 게이트 증적 2표(ADR-0069·§17.5 확장). 확정 시도 스냅샷과 통제된 예외(override)는 INSERT-only이고
        # 정정은 새 행이다(override 철회 = REVOKE 행 추가).
        "gate_evaluations",
        "gate_overrides",
    }
)

#: 일반 테이블. 여기 적는 것은 "불변이 아님을 확인했다"는 뜻이다.
MUTABLE_TABLES: frozenset[str] = frozenset(
    {
        "alembic_version",  # alembic이 소유·관리
        # S0-2 — 신원
        "users",  # 프로필·잠금 카운터·비활성 전환
        "roles",  # 마이그레이션으로만 바뀌지만 DDL 대상은 아니다
        "user_roles",  # 회수 = soft delete(UPDATE)
        "user_sessions",  # last_seen_at 갱신·폐기(UPDATE)
        # S0-2 — 공통
        "doc_number_seq",  # 카운터 증가(UPDATE)가 곧 채번이다
        "idempotency_keys",  # 최초 결과 기록(UPDATE) + TTL 청소(DELETE)
        "events",  # 발송 결과 기록(published_at·attempts)
        "tasks",
        "alert_rules",
        "alerts",  # ack 시각 기록
        "scheduled_jobs",  # 마지막 실행 결과 기록
        "feature_flags",
        "external_refs",
        "custom_field_defs",
        "custom_field_values",
        "skus",  # 마스터 — 상태 전환·정보 수정(UPDATE)
        # S1-1 — 제품 계층 (§4.1). 전부 마스터라 사람이 화면에서 고친다.
        "brands",
        "products",
        "sku_hs_codes",  # 세율 메모·근거링크·최종확인일 갱신(UPDATE)
        "set_components",  # 구성 수량 변경·구성품 제외(soft delete)
        # 마스터 이력이지 원장이 아니다 — 오타 정정은 수정/삭제로 한다.
        # (불변이 필요한 것은 §17.5의 stock_movements·확정 분개·audit_log다.)
        "sku_prices",
        "item_profiles",  # 제품군 분류 — 이름·설명 수정
        # S1-2 — 성분 (§4.3). 전부 마스터라 사람이 화면에서 고친다.
        "ingredients",
        "product_ingredients",  # 함량·표시순서 수정, 성분 제외(soft delete)
        # 규칙은 규제 개정을 따라 사람이 갱신한다(ADR-03 최종확인일 갱신 포함).
        # 판정 스냅샷의 불변(§6.2)은 origin_determinations(S3-4) 몫이다.
        "ingredient_rules",
        # S1-2 — 자재·BOM·라벨 (§4.4·§4.5). 전부 마스터다.
        "materials",
        # BOM 단가·소요량·원산지상태는 사람이 고친다. 판정 시점의 동결은
        # origin_determinations의 BOM 스냅샷(§6.2·S3-4)이 맡는다.
        "product_boms",
        "labels",  # 승인상태 전환·검증 체크·컷인일 입력
        # S1-3 — 거래처·서명권자 (§4.6·§4.7). 전부 마스터라 사람이 고친다.
        "partners",  # 여신·DG 취급·메모 수정
        "partner_type_links",  # 유형 해제 = soft delete(UPDATE)
        "customer_item_codes",  # 매핑 정리 = soft delete(UPDATE)
        "signatories",  # 최소 헤더 — 상세는 S4-4 재판정(ADR-0021 방식)
        # S1-3 — 문서 보관소 (§4.7·§4.8). 마스터다 — 문서의 불변이 필요한 지점은
        # 원본이 아니라 §6.2의 판정 스냅샷(S3-4)이고, 파기 잠금은 서비스가
        # soft delete를 거부하는 방식이라 UPDATE 권한이 있어야 성립한다.
        "document_types",  # 시드 + 마이그레이션·관리 화면(P6)으로만 확장
        "documents",  # soft delete(UPDATE)·메모 수정
        "item_profile_document_types",  # 세트 해제 = soft delete(UPDATE)
        # S1-3 — 엑셀 왕복 임포트 (§12.2·ADR-09). 확정이 상태를 바꾸고(UPDATE)
        # 파기가 soft delete다. 원장이 아니다 — 반영 결과는 대상 마스터에 남는다.
        "import_staging",
        "import_staging_rows",
        # S2-1 — 시장 (§5.1). 마스터다 — MIG 계보 백필 행의 정식화 편집(UPDATE).
        "markets",
        # S2-1 — 요건 템플릿 (§5.1·§4.8). 전부 편집 마스터다 — 규제 변경을 관리
        # 화면이 흡수한다(ADR-03). 확정 행의 편집 차단은 서비스+DB CHECK 몫이고
        # (ADR-0033), 불변이 필요한 "그때 요건"의 동결은 S2-2 인스턴스 스냅샷이다.
        "requirement_templates",
        "template_checklist",
        "template_prerequisites",  # 선행 해제 = soft delete(UPDATE)
        "item_profile_requirement_templates",  # 세트 해제 = soft delete(UPDATE)
        # S2-2 — 인증 인스턴스 (§5.1·§5.2). 상태 전이·실적 기록·담당 변경이
        # 전부 UPDATE다. 불변이 필요한 것은 이력(certification_status_log —
        # IMMUTABLE 등재)이지 본체가 아니다.
        "certifications",
        "certification_tasks",  # 체크·서류 링크·soft delete(UPDATE)
        # S2-3 — 알림 채널·구독 (ADR-07·§16). 설정 마스터라 관리 화면이 고친다.
        # 이번 세션에는 0행이고 쓰기 경로도 없지만(판정 요청 5), 분류는 테이블이
        # 서는 시점의 의무다 — "행이 없으니 나중에"가 곧 조용한 누락이다.
        "notification_channels",
        "webhook_subscriptions",
        # S2-4 — 대행 협업 (§5.4). 계약 대장·통신 기록은 사람이 화면에서 고치는
        # 편집 마스터다(다음 액션 완료 처리·정정 = UPDATE, 삭제 = soft delete).
        # 통신 기록의 "불변"은 요구되지 않는다 — 감사가 필요한 사실은 audit_log·
        # 낙관 잠금(version)·감사 컬럼이 맡고, 통신 요지는 오기 정정이 정상 업무다.
        "agency_contracts",
        "comm_logs",
        # S3-1 — 정책 설정(ADR-0065). 관리자가 화면에서 값을 고치는 설정 마스터다(낙관 잠금).
        # 변경 이력의 정본은 audit_log(IMMUTABLE)라 이 표 자체를 불변으로 두지 않는다.
        "policy_settings",
        # S3-1 — 견적(ADR-0052). 초안 편집·상태 전이·FREE 열(담당자·메모) 갱신이 앱 계정의 정상 UPDATE라
        # 권한 회수가 불가능하다(불변은 서비스 동결 가드+상태이력 IMMUTABLE — ADR-0053, 트리거 미채택).
        "quotations",
        "quotation_lines",  # 초안 라인 편집(제자리 UPDATE)·제외(soft delete)
        # S3-1 PR-6a — PI는 상태 전이(입금 수렴·만료·취소)·FREE 열(담당자·메모) 갱신이 앱 계정의 정상 UPDATE라
        # 권한 회수가 불가능하다(불변은 서비스 동결 가드+상태이력 IMMUTABLE — ADR-0053). 은행 계좌는 ADMIN이 화면에서 고치는 마스터.
        "proforma_invoices",
        "proforma_invoice_lines",  # 생성 시 INSERT만 하지만 헤더와 같은 분류(soft delete 컬럼 보유)
        "bank_accounts",
        # S3-1 PR-7a — SO는 편집(RECEIVED)·상태 전이·FREE 열 갱신이 앱 계정의 정상 UPDATE라 권한 회수가 불가능하다
        # (불변은 서비스 동결 가드+상태이력 IMMUTABLE — ADR-0053). 라인은 제자리 UPDATE·제외(soft delete)한다.
        "sales_orders",
        "sales_order_lines",
        # S3-1 PR-8a — PO는 상태 전이(OC·취소)·FREE 열(담당자·메모·OC 두 열) 갱신이 앱 계정의 정상 UPDATE라 권한 회수가 불가능하다
        # (불변은 서비스 동결 가드[편집 구간 없음]+상태이력 IMMUTABLE — ADR-0053). 라인은 생성 시 INSERT만 한다.
        "purchase_orders",
        "purchase_order_lines",
        # S3-1 PR-9a — 승인 3표(ADR-0060·0061). 앱 계정의 UPDATE가 정상 업무라 MUTABLE이되 **컬럼 단위로 좁혀** 불변을 DB가
        # 강제한다(아래 COLUMN_UPDATE_ALLOWLIST — 트리거 미채택, ADR-0028·0040 계보): approvals는 상태·결정·소비 컬럼만,
        # approval_lines는 역할·메모·soft delete만, delegations는 종료(revoke) 컬럼만. 허용 목록은 **호출하는 마이그레이션이 리터럴로 넘긴다**(앱 상수 임포트 금지). DELETE·TRUNCATE는 통째로 회수한다.
        # 변경 이력의 불변은 approval_events(IMMUTABLE)가 맡는다.
        "approvals",
        "approval_lines",
        "delegations",
        # S3-1 PR-13a — 오더 인테이크(ADR-0071). 편집(PENDING)·상태 전이·담당 이관이 앱 계정의 정상 UPDATE라 권한 회수가 불가능하다.
        # 불변 열(extracted_snapshot·거래처·통화 등)은 ORM before_update 가드+AST 스캔+CHECK가 지킨다(트리거 미채택, ADR-0028·0040). 라인은 제자리 UPDATE·제외(soft delete).
        "order_intakes",
        "order_intake_lines",
        # S3-1 PR-15a — 오더 보드 저장 필터(ADR-0066). 사용자가 화면에서 이름·조건을 고치고 지우는 개인 설정이다(soft delete·낙관 잠금).
        "board_saved_filters",
    }
)


#: 컬럼 단위 UPDATE만 허용하는 MUTABLE 테이블 (S3-1 ADR-0060).
#: 승인 결정은 상태·결정 컬럼만 바뀌어야 하고 금액·대상 같은 결속 컬럼은 앱 계정이
#: UPDATE 못 하게 DB가 막는다("승인 후 불변"의 마지막 층). 값은 UPDATE를 허용할 컬럼이며
#: `updated_at`·`version`처럼 낙관 잠금이 갱신하는 컬럼도 여기 적어야 한다.
#: 표는 그 테이블을 만드는 세션(S3-1 PR-9)이 채운다 — 등재 없는 선점은 하지 않는다.
COLUMN_UPDATE_ALLOWLIST: dict[str, frozenset[str]] = {
    # 스냅샷 컬럼(유형·대상·금액·digest·snapshot·required_role·approval_line_id·requested_by_id)은 목록에 없다 → INSERT 이후 불변.
    "approvals": frozenset(
        {
            "status",
            "decided_by_id",
            "decided_on_behalf_of_id",
            "decided_delegation_id",
            "decided_at",
            "consumed_at",
            "consumed_by_id",
            "version",
            "updated_at",
            "updated_by_id",
        }
    ),
    # 유형·통화·임계는 불변(변경 = 삭제+신규 — 매핑 계보의 정확성). 삭제는 soft delete(deleted_at).
    "approval_lines": frozenset(
        {"approver_role", "note", "deleted_at", "version", "updated_at", "updated_by_id"}
    ),
    # 당사자·기간·범위는 불변(이력 위변조 차단) — 종료 필드만.
    "delegations": frozenset(
        {"revoked_at", "revoked_by_id", "version", "updated_at", "updated_by_id"}
    ),
}


def classified_tables() -> frozenset[str]:
    return IMMUTABLE_TABLES | MUTABLE_TABLES


def revoke_mutations(op: Any, table: str) -> None:
    """마이그레이션에서 호출 — 앱 계정의 변경 권한을 회수한다.

        from app.core.db.table_policy import revoke_mutations
        def upgrade() -> None:
            op.create_table("audit_log", ...)
            revoke_mutations(op, "audit_log")

    GRANT/REVOKE는 autogenerate가 감지하지 못하므로 반드시 손으로 부른다.
    """
    if table not in IMMUTABLE_TABLES:
        raise ValueError(
            f"{table!r}이 IMMUTABLE_TABLES에 없습니다. "
            "app/core/db/table_policy.py에 먼저 등록하세요 — "
            "분류와 실제 권한이 어긋나면 §17.5의 강제가 무의미해집니다."
        )
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public."{table}" FROM kbos_app')


def restrict_update_columns(op: Any, table: str, allowed_columns: frozenset[str]) -> None:
    """마이그레이션에서 호출 — 앱 계정의 UPDATE를 **지정 컬럼만**으로 좁힌다.

        restrict_update_columns(op, "approvals", COLUMN_UPDATE_ALLOWLIST["approvals"])

    허용 목록은 **호출하는 마이그레이션이 리터럴로 넘긴다**(앱 상수 임포트 금지). DELETE·TRUNCATE는 통째로 회수한다(승인·결재선 행은 지우지 않는다 — 정정은 새 행).
    ★ 순서가 중요하다: 테이블 단위 UPDATE를 먼저 회수해야 컬럼 GRANT가 의미를 갖는다
      (테이블 단위 권한이 남아 있으면 컬럼 제한은 조용히 무효다). GRANT/REVOKE는
      autogenerate가 못 보므로 손으로 부르고, 실측 테스트가 권한 상태를 고정한다.
    """
    if not allowed_columns:
        raise ValueError("허용 컬럼이 비어 있습니다 — 불변이면 revoke_mutations를 쓰세요.")
    if table in IMMUTABLE_TABLES:
        raise ValueError(f"{table!r}은 IMMUTABLE입니다. 컬럼 허용 대상이 아닙니다.")
    # ★ 앱 상수(COLUMN_UPDATE_ALLOWLIST)와의 일치는 여기서 검사하지 않는다 — 마이그레이션은 **자기 시점의 컬럼 목록을 리터럴로 고정**해 부르고(상수를 나중에
    #   바꿔도 옛 마이그레이션 재생이 깨지지 않게), "현재 상수 == 최신 마이그레이션이 만든 DB 권한"은 아키텍처·통합 테스트가 검증한다(ADR-0060 부기).
    columns = ", ".join(f'"{name}"' for name in sorted(allowed_columns))
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public."{table}" FROM kbos_app')
    op.execute(f'GRANT UPDATE ({columns}) ON TABLE public."{table}" TO kbos_app')
