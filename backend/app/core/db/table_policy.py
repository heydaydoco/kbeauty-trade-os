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
