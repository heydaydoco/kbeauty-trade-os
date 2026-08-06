"""K. 보안·품질 — 마이그레이션 드라이런 (DESIGN.md §18.3).

"회귀를 기계가 잡는다"를 실제로 달성하는 4종. 앱 검사와 **다른 DB**(kbos_migr)에서
왕복해서 pytest 스키마를 건드리지 않는다.
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.db.session import build_engine
from app.registry import Base
from tests.conftest import ALEMBIC_INI

pytestmark = pytest.mark.group_k


def _migration_check_url() -> str:
    assert settings.migration_check_database_url is not None
    return settings.migration_check_database_url.get_secret_value()


def _config() -> AlembicConfig:
    return AlembicConfig(str(ALEMBIC_INI))


@pytest.fixture(autouse=True)
def _isolate_migration_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 파일의 alembic 명령을 kbos_migr(왕복 검사 전용 DB)로 향하게 한다.

    env.py가 ALEMBIC_DATABASE_URL 환경변수를 우선 본다. 시작 상태를 빈 DB로
    맞춰서 각 테스트가 독립적으로 upgrade부터 시작한다.
    """
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _migration_check_url())
    command.downgrade(_config(), "base")


def test_single_head() -> None:
    """마이그레이션 head는 정확히 하나다 (병렬 브랜치 충돌 방지)"""
    script = ScriptDirectory.from_config(_config())
    assert len(script.get_heads()) == 1


def test_upgrade_from_empty() -> None:
    """빈 DB에서 head까지 올라간다"""
    command.upgrade(_config(), "head")
    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    engine.dispose()
    script = ScriptDirectory.from_config(_config())
    assert revision == script.get_current_head()


def test_downgrade_then_upgrade_roundtrip() -> None:
    """올렸다 내렸다 다시 올려도 성공한다 (downgrade가 실제로 되돌린다)"""
    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")
    command.upgrade(_config(), "head")  # 예외 없이 끝나면 통과


def test_no_model_migration_drift() -> None:
    """모델과 마이그레이션이 일치한다 (모델만 고치고 마이그레이션을 잊은 경우 검출)"""
    command.upgrade(_config(), "head")
    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": False}
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], f"모델↔마이그레이션 드리프트: {diff}"


def test_migration_runs_as_owner_role() -> None:
    """마이그레이션은 kbos_owner로 접속한다"""
    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == "kbos_owner"
    engine.dispose()


# ── S1-1 백필 (ADR-0016 A2) ────────────────────────────────────────────────
#
# ★ 백필 코드는 "기존 행이 있을 때만" 도는데, 테스트 DB는 항상 비어 있어서
#   평범하게 head까지 올리는 검사로는 **한 번도 실행되지 않는다**. 실행된 적
#   없는 마이그레이션 코드는 실행된 적 없는 코드일 뿐이다(S0-1의 mako 필터가
#   정확히 그렇게 새 나갔다 — PROGRESS 주의 인계). 그래서 직전 리비전까지만
#   올려 행을 심고 나머지를 올린다.

#: S0-2의 skus(최소 컬럼) 리비전.
_BEFORE_PRODUCT_HIERARCHY = "65f7c2c5b6fa"


def test_backfill_links_existing_skus_to_generated_products() -> None:
    """S0-2에서 등록된 SKU가 있어도 S1-1 마이그레이션이 처방을 만들어 연결한다"""
    command.upgrade(_config(), _BEFORE_PRODUCT_HIERARCHY)
    engine = build_engine(_migration_check_url())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO skus (sku_code, name_ko) VALUES ('LEG-001', '이월 세럼')")
        )

    command.upgrade(_config(), "head")

    with engine.connect() as connection:
        kind, product_name, description, brand_code = connection.execute(
            text(
                """
                SELECT s.kind, p.name_ko, p.description, b.brand_code
                FROM skus s
                JOIN products p ON p.id = s.product_id
                JOIN brands b ON b.id = p.brand_id
                WHERE s.sku_code = 'LEG-001'
                """
            )
        ).one()
    engine.dispose()

    assert kind == "SINGLE"
    assert product_name == "이월 세럼"
    assert brand_code == "UNCLASSIFIED"
    # 생성 출처가 남아야 사람이 만든 마스터와 구분되고, 구분돼야 정리된다.
    assert "자동 생성" in description
    assert "LEG-001" in description


def test_backfill_creates_nothing_on_an_empty_database() -> None:
    """SKU가 없으면 백필은 아무 행도 만들지 않는다 (새 설치에 유령 마스터 금지)"""
    command.upgrade(_config(), "head")

    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        brands = connection.execute(text("SELECT count(*) FROM brands")).scalar_one()
        products = connection.execute(text("SELECT count(*) FROM products")).scalar_one()
        partners = connection.execute(text("SELECT count(*) FROM partners")).scalar_one()
        documents = connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
        # 시드는 예외다 — document_types는 마이그레이션이 채우는 참조 데이터다.
        doc_types = connection.execute(text("SELECT count(*) FROM document_types")).scalar_one()
    engine.dispose()

    # partners·documents까지 0 — 승격 백필들이 빈 DB에 유령 행을 만들지 않는다.
    assert (brands, products, partners, documents) == (0, 0, 0, 0)
    assert doc_types == 10  # §4.7 열거 9종 + LABEL_ARTWORK(추론분)


# ── S1-3 승격 백필 (ADR-0020 / [M1] 보강(S1-3) ⑤) ──────────────────────────
#
# ★ S1-1 백필과 같은 이유·같은 하네스 — 직전 리비전까지 올려 문자열 행을
#   심은 뒤 head로 올려, 실제로 실행된 백필을 검증한다.

#: 승격 직전 리비전(partners 테이블 생성까지).
_BEFORE_PARTNER_PROMOTION = "81fe6e5c01cc"


def test_promotion_backfill_merges_normalized_names_into_one_partner() -> None:
    """트림·공백 정규화 후 같은 이름은 한 거래처가 되고 유형이 합쳐진다 (승인 조건 3)"""
    command.upgrade(_config(), _BEFORE_PARTNER_PROMOTION)
    engine = build_engine(_migration_check_url())
    with engine.begin() as connection:
        # SET SKU는 처방 없이 존재한다(ADR-0016) — 준비가 가장 가볍다.
        connection.execute(
            text(
                "INSERT INTO skus (sku_code, name_ko, kind, manufacturer_name) "
                "VALUES ('SET-MIG', '이월 세트', 'SET', '  한국  콜마 ')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO materials (material_code, name_ko, material_type, "
                "default_supplier_name) "
                "VALUES ('MAT-MIG', '이월 자재', 'RAW_MATERIAL', '한국 콜마')"
            )
        )

    command.upgrade(_config(), "head")

    with engine.connect() as connection:
        partners = connection.execute(text("SELECT partner_code, name_ko FROM partners")).all()
        assert len(partners) == 1, partners  # "한국  콜마"와 "한국 콜마"는 한 거래처다
        assert partners[0] == ("MIG-0001", "한국 콜마")
        types = {
            row[0] for row in connection.execute(text("SELECT type_code FROM partner_type_links"))
        }
        assert types == {"OEM", "SUPPLIER"}  # 제조사 출처 + 공급사 출처
        sku_partner = connection.execute(
            text(
                "SELECT p.name_ko FROM skus s JOIN partners p "
                "ON p.id = s.manufacturer_partner_id WHERE s.sku_code = 'SET-MIG'"
            )
        ).scalar_one()
        material_partner = connection.execute(
            text(
                "SELECT p.name_ko FROM materials m JOIN partners p "
                "ON p.id = m.default_supplier_partner_id WHERE m.material_code = 'MAT-MIG'"
            )
        ).scalar_one()
    engine.dispose()

    assert sku_partner == "한국 콜마"
    assert material_partner == "한국 콜마"


def test_promotion_downgrade_restores_names_by_reverse_copy() -> None:
    """downgrade는 역복사다 — 문자열 컬럼이 파트너명으로 복원된다 (승인 문면)"""
    command.upgrade(_config(), _BEFORE_PARTNER_PROMOTION)
    engine = build_engine(_migration_check_url())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO skus (sku_code, name_ko, kind, manufacturer_name) "
                "VALUES ('SET-DWN', '이월 세트', 'SET', '한국콜마')"
            )
        )

    command.upgrade(_config(), "head")
    command.downgrade(_config(), _BEFORE_PARTNER_PROMOTION)

    with engine.connect() as connection:
        restored = connection.execute(
            text("SELECT manufacturer_name FROM skus WHERE sku_code = 'SET-DWN'")
        ).scalar_one()
    engine.dispose()

    assert restored == "한국콜마"


# ── S1-3 PR-2 문서 승격 백필 (ADR-0020 잔여 2건 / [M1] 보강(S1-3) ⑤ 말미) ──

#: 문서 3테이블+시드 생성 리비전 — 승격(d5e8f2a6c4b9) 직전이다.
_BEFORE_DOCUMENT_PROMOTION = "c9a2e4f7b1d3"


def _plant_url_rows(engine: Engine) -> None:
    """msds_url·file_url 값이 있는 행을 심는다 — SINGLE SKU는 처방이 필요하다."""
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO brands (brand_code, name_ko) VALUES ('B-DOC', '문서 브랜드')")
        )
        connection.execute(
            text(
                "INSERT INTO products (brand_id, product_code, name_ko) "
                "SELECT id, 'P-DOC', '문서 처방' FROM brands WHERE brand_code = 'B-DOC'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO skus (sku_code, name_ko, kind, product_id, msds_url) "
                "SELECT 'SKU-DOC', '문서 세럼', 'SINGLE', id, ' https://example.com/msds.pdf ' "
                "FROM products WHERE product_code = 'P-DOC'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO labels (sku_id, country_code, label_version, language, "
                "approval_status, inci_local_verified, origin_mark_verified, file_url) "
                "SELECT id, 'US', 1, 'en', 'DRAFT', false, false, "
                "'https://example.com/artwork.pdf' FROM skus WHERE sku_code = 'SKU-DOC'"
            )
        )


def test_document_promotion_backfill_moves_urls_into_link_documents() -> None:
    """msds_url·file_url 값이 LINK형 documents로 이관되고 문자열 컬럼은 사라진다"""
    command.upgrade(_config(), _BEFORE_DOCUMENT_PROMOTION)
    engine = build_engine(_migration_check_url())
    _plant_url_rows(engine)

    command.upgrade(_config(), "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT d.owner_type, t.code, d.storage_kind, d.url, d.note
                FROM documents d JOIN document_types t ON t.id = d.document_type_id
                ORDER BY d.owner_type
                """
            )
        ).all()
        sku_columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'skus'"
                )
            )
        }
        label_columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'labels'"
                )
            )
        }
    engine.dispose()

    assert len(rows) == 2, rows
    label_row, sku_row = rows
    # 라벨 파일 → LABEL×LABEL_ARTWORK. 출처 표식(MIG:)이 dev 전량 보고의 근거다.
    assert label_row[0:3] == ("LABEL", "LABEL_ARTWORK", "LINK")
    assert label_row[3] == "https://example.com/artwork.pdf"
    assert label_row[4].startswith("MIG:")
    # MSDS → SKU×MSDS. 트림되어 이관된다(승인 조건 3의 정규화 규율).
    assert sku_row[0:3] == ("SKU", "MSDS", "LINK")
    assert sku_row[3] == "https://example.com/msds.pdf"
    assert sku_row[4].startswith("MIG:")
    # 문자열 컬럼은 제거됐다 — 영구 잔류 경로의 차단(ADR-0020).
    assert "msds_url" not in sku_columns
    assert "file_url" not in label_columns


def test_document_promotion_downgrade_restores_urls_by_reverse_copy() -> None:
    """downgrade는 역복사다 — 두 문자열 컬럼이 LINK 문서의 URL로 복원된다 (승인 문면)"""
    command.upgrade(_config(), _BEFORE_DOCUMENT_PROMOTION)
    engine = build_engine(_migration_check_url())
    _plant_url_rows(engine)

    command.upgrade(_config(), "head")
    command.downgrade(_config(), _BEFORE_DOCUMENT_PROMOTION)

    with engine.connect() as connection:
        msds = connection.execute(
            text("SELECT msds_url FROM skus WHERE sku_code = 'SKU-DOC'")
        ).scalar_one()
        artwork = connection.execute(text("SELECT file_url FROM labels")).scalar_one()
        set_check = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.skus'::regclass "
                "AND conname = 'ck_skus_set_has_no_dg'"
            )
        ).scalar_one()
    engine.dispose()

    assert msds == "https://example.com/msds.pdf"
    assert artwork == "https://example.com/artwork.pdf"
    # 구 CHECK(msds_url 포함)도 복원됐다 — 함정 ①의 왕복 대상.
    assert "msds_url" in set_check
