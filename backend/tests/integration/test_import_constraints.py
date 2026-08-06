"""K. 보안·품질 — 임포트 스테이징의 불변식을 DB가 강제한다 (§12.2·§17.4·§17.5).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — 모델이 선언한 CHECK·부분
  유니크가 DB에 실재하는지를 실측한다(test_partner_constraints와 같은 방식).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.imports.models import ImportStaging, ImportStagingRow
from tests.support.factories import create_user

SHA_A = "a" * 64
SHA_B = "b" * 64


def _insert_staging(**overrides: Any) -> int:
    columns: dict[str, Any] = {
        "registry_code": "partners",
        "original_filename": "목록.csv",
        "sha256": SHA_A,
        "status": "PENDING",
        "total_data_rows": 1,
        "unchanged_rows": 0,
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = ImportStaging(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _insert_row(staging_id: int, **overrides: Any) -> None:
    columns: dict[str, Any] = {
        "staging_id": staging_id,
        "row_no": 2,
        "kind": "NEW",
        "payload": {},
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(ImportStagingRow(**columns))
        uow.session.flush()


pytestmark = pytest.mark.group_k


# ── 헤더 — 상태·해시·확정 흔적 쌍 ──────────────────────────────────────────


def test_unknown_registry_code_is_rejected_by_the_database() -> None:
    """레지스트리 열거 밖 코드는 DB가 거부한다 — 어댑터 없는 스테이징은 유령이다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_staging(registry_code="mystery")
    assert "registry_code_valid" in str(exc.value)


def test_malformed_sha256_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_staging(sha256="NOT-A-HASH")
    assert "sha256_format" in str(exc.value)


def test_confirmed_status_requires_the_confirmation_trail() -> None:
    """CONFIRMED인데 확정 시각이 없으면 거부 — 확정 상태와 흔적은 한 쌍이다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_staging(status="CONFIRMED", confirmed_at=None)
    assert "confirmed_at_pair" in str(exc.value)


def test_pending_status_rejects_a_confirmation_timestamp() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_staging(status="PENDING", confirmed_at=utcnow())
    assert "confirmed_at_pair" in str(exc.value)


def test_unchanged_rows_cannot_exceed_total_rows() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_staging(total_data_rows=1, unchanged_rows=2)
    assert "unchanged_within_total" in str(exc.value)


# ── 같은 파일은 검토 대기 중 한 번만 (ADR-09 파일 해시 멱등) ────────────────


def test_duplicate_pending_file_is_rejected_by_the_database() -> None:
    _insert_staging(sha256=SHA_A)
    with pytest.raises(IntegrityError) as exc:
        _insert_staging(sha256=SHA_A)
    assert "uq_import_staging_registry_code_sha256_pending" in str(exc.value)


def test_the_same_file_may_be_staged_again_after_confirmation() -> None:
    """확정된 파일의 재업로드는 허용 — 그때는 diff가 0이 되어 재실행 변화 0이 선다"""
    actor_id = create_user("importer@example.com")
    _insert_staging(
        sha256=SHA_A, status="CONFIRMED", confirmed_at=utcnow(), confirmed_by_id=actor_id
    )
    _insert_staging(sha256=SHA_A)  # PENDING — 부분 유니크의 사정권 밖


def test_different_registries_may_stage_the_same_file() -> None:
    _insert_staging(registry_code="partners", sha256=SHA_B)
    _insert_staging(registry_code="materials", sha256=SHA_B)


# ── 행 — 분류별 필수 필드 ──────────────────────────────────────────────────


def test_new_rows_cannot_carry_a_target() -> None:
    staging_id = _insert_staging()
    with pytest.raises(IntegrityError) as exc:
        _insert_row(staging_id, kind="NEW", target_id=1, expected_version=1)
    assert "new_has_no_target" in str(exc.value)


def test_changed_rows_require_target_and_version() -> None:
    """CHANGED에 대상·기대 version이 없으면 확정이 충돌 검사를 할 수 없다 (§17.2)"""
    staging_id = _insert_staging()
    with pytest.raises(IntegrityError) as exc:
        _insert_row(staging_id, kind="CHANGED", target_id=1, expected_version=None)
    assert "changed_has_target" in str(exc.value)


def test_error_rows_require_a_reason() -> None:
    """사유 없는 오류 행은 리포트가 아니다 (§12.2 "행번호+사유")"""
    staging_id = _insert_staging()
    with pytest.raises(IntegrityError) as exc:
        _insert_row(staging_id, kind="ERROR", error_reason=None)
    assert "error_has_reason" in str(exc.value)


def test_row_numbers_start_after_the_header() -> None:
    staging_id = _insert_staging()
    with pytest.raises(IntegrityError) as exc:
        _insert_row(staging_id, row_no=1)
    assert "row_no_after_header" in str(exc.value)


def test_duplicate_row_numbers_are_rejected_within_a_staging() -> None:
    staging_id = _insert_staging()
    _insert_row(staging_id, row_no=2)
    with pytest.raises(IntegrityError) as exc:
        _insert_row(staging_id, row_no=2)
    assert "uq_import_staging_rows_staging_id_row_no_active" in str(exc.value)


# ── CHECK 재정의 실재 (S1.5 PR-1 — 함정 ①·⑪) ─────────────────────────────


def test_registry_code_check_definition_includes_skus() -> None:
    """registry_code CHECK의 **정의문**에 skus가 실재한다 (마이그레이션 a7c3e9f2d4b8)

    기존 테이블 CHECK 변경은 autogenerate가 감지하지 못해(함정 ①) 수기
    재정의였다 — 이름만 있고 구 정의가 남는 실패 모양을 정의문 실측으로
    잡는다(PR-2 선례: ck_skus_set_has_no_dg 정의문 테스트와 같은 양식).
    """
    from sqlalchemy import text

    from app.modules.imports.models import IMPORT_REGISTRY_CODES

    with unit_of_work() as uow:
        definition = uow.session.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'public.import_staging'::regclass
                  AND conname = 'ck_import_staging_registry_code_valid'
                """
            )
        ).scalar_one()
    for code in IMPORT_REGISTRY_CODES:
        assert f"'{code}'" in definition, definition
