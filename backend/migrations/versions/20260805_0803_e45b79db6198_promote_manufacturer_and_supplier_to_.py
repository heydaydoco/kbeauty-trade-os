"""promote manufacturer and supplier to partners

리비전 ID: e45b79db6198
직전 리비전: 81fe6e5c01cc
생성 시각(UTC): 2026-08-05 08:03:17.740409+00:00

임시 문자열 2건의 partners FK 승격 (ADR-0020 / [M1] 보강(S1-3) ⑤ / 부채 #14):
`skus.manufacturer_name` → `skus.manufacturer_partner_id`(유형 OEM),
`materials.default_supplier_name` → `materials.default_supplier_partner_id`(유형 SUPPLIER).

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ **rename이 아니라 승격**이다 — drop 전에 backfill이 값을 partners 행으로
    옮긴다(웹 세션 승인 조건 3: 트림·연속 공백 정규화 후 distinct).
  ■ backfill 규칙: ① 정규화 이름이 활성 partners.name_ko에 이미 있으면 재사용
    (upgrade 재실행에 안전) ② 없으면 MIG-#### 코드로 생성(번호는 기존 MIG 최대
    +1부터, 이름순 — 결정적) ③ 유형 링크는 없을 때만 추가 ④ FK는 이름 일치
    최소 id로 연결(동명 중복 대비 결정적).
  ■ 기존 테이블 ALTER지만 **CHECK 변경 없음** — set_has_no_dg는 msds_url만
    포함하고(그건 다음 PR), manufacturer_name은 어떤 CHECK에도 없다(함정 ①
    해당 없음을 실측 확인).
  ■ downgrade는 역복사다(승인 문면) — 문자열 컬럼을 되살려 파트너명을 복사한
    뒤 FK 컬럼을 지운다. 생성된 MIG- 파트너 행은 지우지 않는다(데이터 삭제는
    downgrade의 일이 아니고, 재-upgrade 시 이름 재사용으로 중복도 안 생긴다).
  ■ 백필 실측 테스트: tests/integration/test_migrations.py (직전 리비전까지
    올려 행을 심고 head로 — S1-1 백필과 같은 하네스).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e45b79db6198"
down_revision: str | None = "81fe6e5c01cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 정규화 — 트림 + 연속 공백 1칸(승인 조건 3). 두 소스가 같은 식을 써야
#: "한국  콜마"와 "한국 콜마 "가 한 거래처로 합쳐진다.
_NORM_SKU = r"regexp_replace(btrim(manufacturer_name), '\s+', ' ', 'g')"
_NORM_MAT = r"regexp_replace(btrim(default_supplier_name), '\s+', ' ', 'g')"

_SOURCES = f"""
    SELECT {_NORM_SKU} AS name FROM skus
    WHERE manufacturer_name IS NOT NULL AND btrim(manufacturer_name) <> ''
    UNION
    SELECT {_NORM_MAT} FROM materials
    WHERE default_supplier_name IS NOT NULL AND btrim(default_supplier_name) <> ''
"""


def upgrade() -> None:
    # 1) 새 FK 컬럼 (autogenerate 초안 그대로)
    op.add_column(
        "materials", sa.Column("default_supplier_partner_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_materials_default_supplier_partner_id_partners"),
        "materials",
        "partners",
        ["default_supplier_partner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("skus", sa.Column("manufacturer_partner_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_skus_manufacturer_partner_id_partners"),
        "skus",
        "partners",
        ["manufacturer_partner_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 2) backfill — 이름당 거래처 1행. 이미 같은 이름의 활성 거래처가 있으면
    #    만들지 않고 재사용한다(재실행·부분 적용에 안전).
    op.execute(
        f"""
        WITH sources AS ({_SOURCES}),
        missing AS (
            SELECT DISTINCT s.name FROM sources s
            WHERE NOT EXISTS (
                SELECT 1 FROM partners p
                WHERE p.deleted_at IS NULL AND p.name_ko = s.name
            )
        ),
        numbered AS (
            SELECT name, row_number() OVER (ORDER BY name) AS rn FROM missing
        ),
        base AS (
            SELECT COALESCE(MAX((substring(partner_code FROM '^MIG-(\\d+)$'))::int), 0) AS max_n
            FROM partners WHERE partner_code ~ '^MIG-\\d+$'
        )
        INSERT INTO partners (partner_code, name_ko)
        SELECT 'MIG-' || lpad((base.max_n + numbered.rn)::text, 4, '0'), numbered.name
        FROM numbered CROSS JOIN base
        """
    )
    # 3) 유형 링크 — 제조사 출처는 OEM, 공급사 출처는 SUPPLIER. 이미 있으면 통과.
    op.execute(
        f"""
        INSERT INTO partner_type_links (partner_id, type_code)
        SELECT DISTINCT p.id, 'OEM'
        FROM (SELECT DISTINCT {_NORM_SKU} AS name FROM skus
              WHERE manufacturer_name IS NOT NULL AND btrim(manufacturer_name) <> '') s
        JOIN partners p ON p.deleted_at IS NULL AND p.name_ko = s.name
        WHERE NOT EXISTS (
            SELECT 1 FROM partner_type_links l
            WHERE l.partner_id = p.id AND l.type_code = 'OEM' AND l.deleted_at IS NULL
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO partner_type_links (partner_id, type_code)
        SELECT DISTINCT p.id, 'SUPPLIER'
        FROM (SELECT DISTINCT {_NORM_MAT} AS name FROM materials
              WHERE default_supplier_name IS NOT NULL AND btrim(default_supplier_name) <> '') s
        JOIN partners p ON p.deleted_at IS NULL AND p.name_ko = s.name
        WHERE NOT EXISTS (
            SELECT 1 FROM partner_type_links l
            WHERE l.partner_id = p.id AND l.type_code = 'SUPPLIER' AND l.deleted_at IS NULL
        )
        """
    )
    # 4) FK 연결 — 동명 중복이 있어도 최소 id로 결정적이게.
    op.execute(
        f"""
        UPDATE skus SET manufacturer_partner_id = (
            SELECT MIN(p.id) FROM partners p
            WHERE p.deleted_at IS NULL AND p.name_ko = {_NORM_SKU}
        )
        WHERE manufacturer_name IS NOT NULL AND btrim(manufacturer_name) <> ''
        """
    )
    op.execute(
        f"""
        UPDATE materials SET default_supplier_partner_id = (
            SELECT MIN(p.id) FROM partners p
            WHERE p.deleted_at IS NULL AND p.name_ko = {_NORM_MAT}
        )
        WHERE default_supplier_name IS NOT NULL AND btrim(default_supplier_name) <> ''
        """
    )

    # 5) 문자열 컬럼 제거 — 값은 위에서 전부 partners로 옮겨졌다(ADR-0020
    #    "'이미 값이 있으니까'로 넘어가는" 영구 잔류 경로의 차단).
    op.drop_column("materials", "default_supplier_name")
    op.drop_column("skus", "manufacturer_name")


def downgrade() -> None:
    # 역복사(웹 세션 승인 문면) — 문자열 컬럼을 되살려 파트너명을 복사한 뒤
    # FK를 지운다. soft delete된 파트너의 이름도 복사한다(이름을 잃지 않는다).
    op.add_column(
        "skus",
        sa.Column("manufacturer_name", sa.VARCHAR(length=100), autoincrement=False, nullable=True),
    )
    op.execute(
        """
        UPDATE skus s SET manufacturer_name = left(p.name_ko, 100)
        FROM partners p WHERE s.manufacturer_partner_id = p.id
        """
    )
    op.drop_constraint(op.f("fk_skus_manufacturer_partner_id_partners"), "skus", type_="foreignkey")
    op.drop_column("skus", "manufacturer_partner_id")

    op.add_column(
        "materials",
        sa.Column(
            "default_supplier_name", sa.VARCHAR(length=100), autoincrement=False, nullable=True
        ),
    )
    op.execute(
        """
        UPDATE materials m SET default_supplier_name = left(p.name_ko, 100)
        FROM partners p WHERE m.default_supplier_partner_id = p.id
        """
    )
    op.drop_constraint(
        op.f("fk_materials_default_supplier_partner_id_partners"), "materials", type_="foreignkey"
    )
    op.drop_column("materials", "default_supplier_partner_id")
