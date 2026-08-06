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
    }
)


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
