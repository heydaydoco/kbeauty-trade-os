"""전달 서류 zip — 한 인증의 서류를 한 묶음으로 (§5.4 / S2-4 PR-2 안건 ④ — ADR-0049).

구성: `manifest.csv`(UTF-8 BOM) · `README.txt` · `links.txt`(LINK형이 있을 때) · `documents/NN_종류_원본명`.
포함 범위: 인증 소유 문서 + 이 인증의 태스크가 서류로 연결한 문서(다른 소유 포함 — §5.6 공용 참조,
GMP류 소유 공백은 이 경로로 흡수). **통신 기록 첨부는 포함하지 않는다**(통신 기록이지 전달 서류가 아니다).

★ 생성까지 시스템, **발송은 사람** — README가 그렇게 고지한다. 생성 이력은 남기지 않는다(관찰 등재).
★ DB 조회(메타)는 트랜잭션 안에서 끝내고, **파일 IO는 트랜잭션 밖**에서 한다(§17.1 — 앱 계정
  idle_in_tx 60s 안에서 대용량 IO 금지). 메모리는 `SpooledTemporaryFile` 8MiB까지만 쓰고 넘으면
  디스크로 내린다.
★ 실물이 없거나 해시가 다르면 **조용히 빼지 않고 409로 목록과 함께 거부**한다 — 빠진 채 나간
  전달본이 가장 위험하다. 저장명(`stored_name`)은 zip에 노출하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import IO

from sqlalchemy import or_, select

from app.core.csv_export import render_csv
from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import to_kst, utcnow
from app.modules.certifications.models import Certification, CertificationTask
from app.modules.certifications.service import require_certification
from app.modules.documents.models import Document, DocumentType
from app.modules.documents.service import storage_root

MAX_FILES = 100
MAX_TOTAL_BYTES = 200 * 1024 * 1024
SPOOL_BYTES = 8 * 1024 * 1024
_CHUNK = 64 * 1024
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
#: Windows 예약 장치명 — 압축을 풀 때 실패하므로 밑줄을 접두한다.
_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

MANIFEST_HEADER = (
    "순번",
    "서류종류",
    "zip 내 경로",
    "저장형식",
    "발급일",
    "유효기간",
    "sha256",
    "링크",
    "출처",
)


@dataclass(frozen=True, slots=True)
class PackageItem:
    seq: int
    document_id: int
    type_name: str
    kind: str
    original_filename: str | None
    stored_name: str | None
    size_bytes: int | None
    sha256: str | None
    url: str | None
    issued_on: date | None
    valid_until: date | None
    source: str

    @property
    def zip_path(self) -> str | None:
        if self.kind != "FILE" or self.original_filename is None:
            return None
        return f"documents/{self.seq:02d}_{_component(self.type_name)}_{_component(self.original_filename)}"


@dataclass(frozen=True, slots=True)
class PackageSource:
    certification_id: int
    template_name: str
    status: str
    cert_number: str | None
    items: list[PackageItem]


def _component(raw: str, limit: int = 120) -> str:
    """zip 경로 조각 — 경로 구분자·제어·예약 문자를 걷어 zip-slip과 OS별 금지 문자를 막는다.

    길이 제한은 **확장자를 보존**한 채 본문만 자른다(확장자가 잘리면 받는 쪽이 파일을 못 연다)."""
    cleaned = _UNSAFE.sub("_", raw).strip().strip(".") or "file"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    if len(cleaned) > limit:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and stem and 0 < len(ext) <= 10:
            cleaned = stem[: limit - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:limit]
    return cleaned


def load_source(certification_id: int) -> PackageSource:
    """메타 조회(트랜잭션 안) — 인증 소유 + 태스크 연결 문서, 활성만."""
    with unit_of_work() as uow:
        session = uow.session
        cert: Certification = require_certification(session, certification_id)
        linked_ids = set(
            session.execute(
                select(CertificationTask.document_id).where(
                    CertificationTask.certification_id == certification_id,
                    CertificationTask.deleted_at.is_(None),
                    CertificationTask.document_id.is_not(None),
                )
            ).scalars()
        )
        owned = (Document.owner_type == "CERTIFICATION") & (Document.owner_id == certification_id)
        scope = or_(owned, Document.id.in_(linked_ids)) if linked_ids else owned
        rows = session.execute(
            select(Document, DocumentType.name_ko)
            .join(DocumentType, DocumentType.id == Document.document_type_id)
            .where(Document.deleted_at.is_(None), scope)
            .order_by(DocumentType.name_ko, Document.id)
        ).all()
        items: list[PackageItem] = []
        for seq, (doc, type_name) in enumerate(rows, start=1):
            owned = doc.owner_type == "CERTIFICATION" and doc.owner_id == certification_id
            linked = doc.id in linked_ids
            source = (
                "인증 소유·태스크 연결"
                if owned and linked
                else "인증 소유"
                if owned
                else "태스크 연결"
            )
            items.append(
                PackageItem(
                    seq=seq,
                    document_id=doc.id,
                    type_name=type_name,
                    kind=doc.storage_kind,
                    original_filename=doc.original_filename,
                    stored_name=doc.stored_name,
                    size_bytes=doc.size_bytes,
                    sha256=doc.sha256,
                    url=doc.url,
                    issued_on=doc.issued_on,
                    valid_until=doc.valid_until,
                    source=source,
                )
            )
        return PackageSource(
            certification_id=cert.id,
            template_name=cert.template_name,
            status=cert.status,
            cert_number=cert.cert_number,
            items=items,
        )


def _enforce_limits(source: PackageSource) -> None:
    files = [item for item in source.items if item.kind == "FILE"]
    total = sum(item.size_bytes or 0 for item in files)
    if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
        raise AppError(
            ErrorCode.CERTIFICATIONS_PACKAGE_TOO_LARGE,
            detail={"files": len(files), "total_bytes": total},
            log_context={
                "certification_id": source.certification_id,
                "files": len(files),
                "total_bytes": total,
            },
        )


def _manifest(items: list[PackageItem]) -> str:
    return render_csv(
        MANIFEST_HEADER,
        [
            (
                item.seq,
                item.type_name,
                item.zip_path or "",
                "파일" if item.kind == "FILE" else "링크",
                item.issued_on.isoformat() if item.issued_on else "",
                item.valid_until.isoformat() if item.valid_until else "",
                item.sha256 or "",
                item.url or "",
                item.source,
            )
            for item in items
        ],
    )


def _readme(source: PackageSource) -> str:
    now = to_kst(utcnow()).strftime("%Y-%m-%d %H:%M")
    files = sum(1 for item in source.items if item.kind == "FILE")
    links = len(source.items) - files
    lines = [
        "전달 서류 묶음",
        "=" * 20,
        f"인증: {source.template_name} (#{source.certification_id})",
        f"상태: {source.status}"
        + (f" · 인증번호 {source.cert_number}" if source.cert_number else ""),
        f"생성 시각(KST): {now}",
        f"서류: 파일 {files}건 · 링크 {links}건",
        "",
        "이 묶음은 시스템이 만들었습니다. 상대(대행사·기관·바이어)에게 보내는 일은 사람이 합니다 —",
        "보내기 전에 서류 종류·유효기간(manifest.csv)을 한 번 더 확인해 주세요.",
        "링크형 서류는 파일이 아니라 주소만 기록돼 있습니다(links.txt).",
    ]
    return "\r\n".join(lines) + "\r\n"


def _links(items: list[PackageItem]) -> str:
    lines = [
        f"{item.seq:02d} {item.type_name}: {item.url}" for item in items if item.kind == "LINK"
    ]
    return "\r\n".join(lines) + "\r\n"


def build_package(source: PackageSource) -> IO[bytes]:
    """zip을 임시 파일(8MiB 초과 시 디스크)에 만들고 처음 위치로 되감아 돌려준다.

    호출자가 닫는다. 실물 유실·해시 불일치가 하나라도 있으면 만든 것을 버리고 409를 올린다.
    """
    _enforce_limits(source)
    root = storage_root()
    problems: list[dict[str, object]] = []
    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115
    try:
        with zipfile.ZipFile(spool, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in source.items:
                if item.kind != "FILE":
                    continue
                assert item.stored_name is not None and item.zip_path is not None
                path = root / item.stored_name
                if not path.is_file():
                    problems.append({"document_id": item.document_id, "reason": "MISSING"})
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as src, archive.open(item.zip_path, mode="w") as dst:
                    while chunk := src.read(_CHUNK):
                        digest.update(chunk)
                        dst.write(chunk)
                if item.sha256 is not None and digest.hexdigest() != item.sha256:
                    problems.append({"document_id": item.document_id, "reason": "HASH_MISMATCH"})
            if problems:
                raise AppError(
                    ErrorCode.CERTIFICATIONS_PACKAGE_FILES_UNAVAILABLE,
                    detail={"documents": problems},
                    log_context={"certification_id": source.certification_id, "problems": problems},
                )
            archive.writestr("manifest.csv", _manifest(source.items).encode("utf-8"))
            archive.writestr("README.txt", _readme(source).encode("utf-8"))
            if any(item.kind == "LINK" for item in source.items):
                archive.writestr("links.txt", _links(source.items).encode("utf-8"))
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool
