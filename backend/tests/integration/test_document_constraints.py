"""K. 보안·품질 — 문서 보관소의 불변식을 DB가 강제한다 (§4.7·§17.4·§17.5).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — 모델이 선언한 CHECK·부분
  유니크가 DB에 실재하는지를 실측한다(test_partner_constraints와 같은 방식).
  특히 CHECK는 autogenerate가 마이그레이션 초안에 넣어 주지 않으므로(함정 ①)
  이 실측이 수기 반영 누락의 최종 검출이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.documents.models import Document, DocumentType, ItemProfileDocumentType

pytestmark = pytest.mark.group_k

_SHA = "a" * 64
_STORED = "b" * 32


def _seed_type_id(code: str = "MSDS") -> int:
    with unit_of_work() as uow:
        return (
            uow.session.execute(
                select(DocumentType.id).where(
                    DocumentType.code == code, DocumentType.deleted_at.is_(None)
                )
            ).scalar_one()
        )


def _insert_document(**overrides: Any) -> int:
    columns: dict[str, Any] = {
        "owner_type": "SKU",
        "owner_id": 1,  # 폴리모픽 — FK가 없으므로 실재 검증은 서비스 몫이다
        "document_type_id": _seed_type_id(),
        "storage_kind": "LINK",
        "url": "https://example.com/msds.pdf",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = Document(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _file_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "storage_kind": "FILE",
        "url": None,
        "stored_name": _STORED,
        "original_filename": "msds.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "sha256": _SHA,
    }
    fields.update(overrides)
    return fields


# ── 시드 실재 (웹 세션 승인 조건 6) ────────────────────────────────────────


def test_the_seed_covers_the_design_enumeration() -> None:
    """document_types 시드가 §4.7 열거 9종 + LABEL_ARTWORK(추론분)로 실재한다"""
    with unit_of_work() as uow:
        codes = set(
            uow.session.execute(
                select(DocumentType.code).where(DocumentType.deleted_at.is_(None))
            ).scalars()
        )
    assert codes == {
        "CFS",
        "GMP",
        "COA",
        "MSDS",
        "CERTIFICATE",
        "TRADE_DOC",
        "PURCHASE_CONFIRMATION",
        "LC_ADVICE",
        "ORIGIN_EVIDENCE",
        "LABEL_ARTWORK",
    }


def test_origin_evidence_carries_the_five_year_retention() -> None:
    """원산지 증빙만 retention_years=5다 (§4.7 "보존기한(발급일+5년)")"""
    with unit_of_work() as uow:
        rows = uow.session.execute(
            select(DocumentType.code, DocumentType.retention_years).where(
                DocumentType.deleted_at.is_(None)
            )
        ).all()
    years = {code: retention for code, retention in rows}
    assert years.pop("ORIGIN_EVIDENCE") == 5
    assert all(value is None for value in years.values())


# ── FILE/LINK 상호 배타 (ADR-0028) ─────────────────────────────────────────


def test_a_file_row_without_a_hash_is_rejected_by_the_database() -> None:
    """FILE인데 sha256이 없으면 DB가 거부한다 — 반쪽 파일 행 금지"""
    with pytest.raises(IntegrityError) as exc:
        _insert_document(**_file_fields(sha256=None))
    assert "storage_kind_fields" in str(exc.value)


def test_a_link_row_with_file_fields_is_rejected_by_the_database() -> None:
    """LINK인데 저장 파일명이 있으면 DB가 거부한다 — 어느 쪽이 진본인지 모호해진다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_document(stored_name=_STORED)
    assert "storage_kind_fields" in str(exc.value)


def test_a_link_row_without_a_url_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_document(url=None)
    assert "storage_kind_fields" in str(exc.value)


# ── 열거·형식 CHECK ────────────────────────────────────────────────────────


def test_an_unknown_owner_type_is_rejected_by_the_database() -> None:
    """소유 열거는 소비분 2종(SKU·LABEL)뿐이다 (ADR-0028 — 확장은 후속 마이그레이션)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_document(owner_type="SHIPMENT")
    assert "owner_type_valid" in str(exc.value)


def test_an_unknown_storage_kind_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_document(storage_kind="EMBED")
    assert "storage_kind" in str(exc.value)


def test_a_path_like_stored_name_is_rejected_by_the_database() -> None:
    """저장 파일명은 서버 생성 hex 32자뿐이다 — 경로 문자열이 끼면 DB가 거부한다(①)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_document(**_file_fields(stored_name="../../etc/passwd"))
    assert "stored_name_format" in str(exc.value)


def test_a_malformed_hash_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_document(**_file_fields(sha256="ZZ" * 32))
    assert "sha256_format" in str(exc.value)


def test_a_zero_byte_size_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_document(**_file_fields(size_bytes=0))
    assert "size_bytes_positive" in str(exc.value)


# ── 기간 순서 (§17.5) ──────────────────────────────────────────────────────


def test_validity_before_issue_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_document(issued_on=date(2026, 8, 1), valid_until=date(2026, 7, 31))
    assert "validity_after_issue" in str(exc.value)


def test_retention_before_issue_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_document(issued_on=date(2026, 8, 1), retention_until=date(2026, 7, 31))
    assert "retention_after_issue" in str(exc.value)


# ── 해시 멱등 (§17.4 / §12.1 해시 중복 감지) ───────────────────────────────


def test_the_same_file_twice_on_one_owner_is_rejected_by_the_database() -> None:
    """같은 소유자에 같은 해시는 한 번만 — 부분 유니크가 최종 판정자다"""
    _insert_document(**_file_fields())
    with pytest.raises(IntegrityError) as exc:
        _insert_document(**_file_fields(stored_name="c" * 32))
    assert "uq_documents_owner_type_owner_id_sha256_active" in str(exc.value)


def test_the_same_file_on_another_owner_is_allowed() -> None:
    """다른 소유자는 같은 파일을 가질 수 있다 (같은 MSDS를 쓰는 용량 변형 실무)"""
    _insert_document(**_file_fields())
    _insert_document(owner_id=2, **_file_fields(stored_name="c" * 32))


def test_soft_deleted_hash_can_be_reuploaded() -> None:
    """soft delete 후 같은 해시 재유입은 신규다 (§17.4 부분 유니크)"""
    first = _insert_document(**_file_fields())
    with unit_of_work() as uow:
        row = uow.session.get(Document, first)
        assert row is not None
        row.deleted_at = utcnow()
    _insert_document(**_file_fields(stored_name="c" * 32))


def test_multiple_links_per_owner_are_allowed() -> None:
    """LINK형은 해시가 없다(NULL) — 한 소유자에 여러 링크가 공존한다(자기검사)"""
    _insert_document(url="https://example.com/a.pdf")
    _insert_document(url="https://example.com/b.pdf")


# ── 품목군 서류 세트 (§4.8) ────────────────────────────────────────────────


def _insert_profile_link(profile_id: int, type_id: int) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            ItemProfileDocumentType(item_profile_id=profile_id, document_type_id=type_id)
        )
        uow.session.flush()


def test_duplicate_profile_document_type_is_rejected_by_the_database() -> None:
    """한 품목군 세트에 같은 종류는 한 번만 (부분 유니크 — 이름은 손으로 줄인 것)"""
    from tests.support.factories import create_item_profile

    profile_id = create_item_profile("PRF-DOC")
    type_id = _seed_type_id()
    _insert_profile_link(profile_id, type_id)
    with pytest.raises(IntegrityError) as exc:
        _insert_profile_link(profile_id, type_id)
    assert "uq_item_profile_document_types_profile_type_active" in str(exc.value)


# ── 선언 CHECK 전건 실재 (함정 ① — 수기 반영 누락 검출) ────────────────────

_DECLARED_DOCUMENT_CHECKS = frozenset(
    {
        "ck_documents_owner_type_valid",
        "ck_documents_storage_kind_valid",
        "ck_documents_storage_kind_fields",
        "ck_documents_size_bytes_positive",
        "ck_documents_sha256_format",
        "ck_documents_stored_name_format",
        "ck_documents_validity_after_issue",
        "ck_documents_retention_after_issue",
    }
)


def test_every_declared_check_constraint_exists() -> None:
    """모델이 선언한 documents CHECK가 전부 DB에 실재한다"""
    with unit_of_work() as uow:
        names = set(
            uow.session.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'public.documents'::regclass AND contype = 'c'
                    """
                )
            )
            .scalars()
            .all()
        )
    missing = _DECLARED_DOCUMENT_CHECKS - names
    assert not missing, (
        f"모델에는 있는데 DB에 없는 CHECK: {sorted(missing)} — "
        "autogenerate는 CHECK를 감지하지 못합니다. 마이그레이션에 손으로 추가하세요."
    )
