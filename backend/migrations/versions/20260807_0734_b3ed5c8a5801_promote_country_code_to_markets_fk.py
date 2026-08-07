"""promote country code to markets fk

리비전 ID: b3ed5c8a5801
직전 리비전: 5df0df5da551
생성 시각(UTC): 2026-08-07 07:34:04.714007+00:00

country_code → markets FK 승격 3건 (ADR-0023 종착 / [M1] 보강(S1-2) ④ /
S2-1 판정 §0-1 ② — 조건 3~5. 조건 2: 완료 보고에 이 DDL 전문 포함).

방식(조건 3 확정): **문자열 코드 컬럼 유지 + markets.code 참조 FK** —
컬럼·유일키(라벨 [sku,국가,언어,판번] 포함)·CSV 칸이 전부 불변이고 FK만
얹는다. market_id 대리키 교체안은 판정에서 부결됐다.

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ rename·컬럼 변경 없음 — FK 3건 추가뿐. CHECK 변경도 없음(country_code_format
    3건은 그대로 — 함정 ① 해당 없음을 실측 확인, FK는 autogenerate가 감지했다).
  ■ **backfill이 FK보다 먼저다** — FK는 소프트 삭제 행 포함 **전 행**을
    검증하므로, 세 테이블의 distinct 코드 전부(deleted_at 무관)를 markets에
    선등록해야 제약 생성이 성공한다. 이미 있는 코드는 건너뛴다(재실행 안전 —
    e45b79db6198 선례).
  ■ backfill 행 = MIG 계보(조건 4): name_ko=코드 그대로, note에 MIG 표식.
    정식화(이름 입력)는 시장 화면의 PATCH, 검수는 반입 재개 시 MIG 큐 합류.
    전량 평문 보고는 완료 보고 몫.
  ■ downgrade는 FK 3건 drop(op.f — 함정 ⑪). markets 행은 지우지 않는다
    (데이터 삭제는 downgrade의 일이 아니다 — e45b79db6198 선례 문면).
    코드 컬럼이 불변이라 역복사도 필요 없다.
  ■ FK 정의문 실재 테스트: tests/integration/test_market_constraints.py
    (pg_get_constraintdef — 조건 5 양식).
  ■ 백필 실측 테스트: tests/integration/test_migrations.py (직전 리비전까지
    올려 행을 심고 head로 — e45b79db6198과 같은 하네스).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "b3ed5c8a5801"
down_revision: str | None = "5df0df5da551"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 세 테이블의 코드 전집합 — deleted_at을 **보지 않는다**(FK는 전 행을 검증한다).
_SOURCES = """
    SELECT country_code AS code FROM labels
    UNION
    SELECT country_code FROM ingredient_rules
    UNION
    SELECT country_code FROM sku_hs_codes
"""

#: MIG 표식(조건 4) — 시장 코드는 값 자체가 자연키라 partners의 MIG-#### 채번
#: 대신 note가 계보를 진다. 이 문구는 완료 보고·검수 큐가 그대로 조회한다.
_MIG_NOTE = "MIG: country_code FK 승격 백필(S2-1) — 이름 정식화·검수 전"


def upgrade() -> None:
    # 1) backfill — 참조될 코드를 먼저 markets에 채운다. 전역 UNIQUE(소프트
    #    삭제 행 포함 점유)라 존재 확인도 deleted_at을 보지 않는다.
    op.execute(
        f"""
        INSERT INTO markets (code, name_ko, note)
        SELECT s.code, s.code, '{_MIG_NOTE}'
        FROM ({_SOURCES}) s
        WHERE NOT EXISTS (SELECT 1 FROM markets m WHERE m.code = s.code)
        ORDER BY s.code
        """
    )

    # 2) FK 3건 (autogenerate 초안 그대로 — 이름은 규약, op.f는 "최종 이름" 표식)
    op.create_foreign_key(
        op.f("fk_ingredient_rules_country_code_markets"),
        "ingredient_rules",
        "markets",
        ["country_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_labels_country_code_markets"),
        "labels",
        "markets",
        ["country_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_sku_hs_codes_country_code_markets"),
        "sku_hs_codes",
        "markets",
        ["country_code"],
        ["code"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # FK만 되돌린다 — 컬럼이 불변이라 역복사가 없고, backfill로 생긴 markets
    # 행은 지우지 않는다(위 체크리스트).
    op.drop_constraint(
        op.f("fk_sku_hs_codes_country_code_markets"), "sku_hs_codes", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_labels_country_code_markets"), "labels", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_ingredient_rules_country_code_markets"), "ingredient_rules", type_="foreignkey"
    )
