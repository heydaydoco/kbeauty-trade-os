"""C·K. 전달 서류 zip — GET /certifications/{id}/package (§5.4 / S2-4 PR-2 안건 ④).

★ 실제 바이트를 올리고 내려받아 zip 안의 바이트·sha256·manifest를 대조한다.
  실물 유실·해시 불일치는 조용히 빼지 않고 409(빠진 채 나간 전달본이 가장 위험하다).
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.db.uow import unit_of_work
from app.main import app
from app.modules.certifications import package
from app.modules.documents.models import Document
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_certification_instance,
    create_link_document,
    create_requirement_template,
    create_sku,
    create_user,
)

pytestmark = [pytest.mark.group_c, pytest.mark.group_k]

LOGIN = "/api/v1/auth/login"
DOCUMENTS = "/api/v1/documents"
CERTIFICATIONS = "/api/v1/certifications"
_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "file_storage_root", str(tmp_path / "files"))
    return tmp_path / "files"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        assert (
            client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD}).status_code
            == 200
        )
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("pkg-cert@example.com", RoleCode.CERT)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("pkg-viewer@example.com", RoleCode.VIEWER)


def _certification() -> int:
    number = next(_SEQ)
    template_id = create_requirement_template("US", name=f"패키지 요건 {number}")
    return create_certification_instance(
        template_id, "SKU", create_sku(f"SKU-PKG-{number}"), status="PREPARING"
    )


def _upload(
    client: TestClient,
    owner_id: int,
    *,
    filename: str,
    content: bytes,
    owner_type: str = "CERTIFICATION",
    document_type: str = "CERTIFICATE",
) -> dict[str, Any]:
    response = client.post(
        f"{DOCUMENTS}/files",
        data={"owner_type": owner_type, "owner_id": str(owner_id), "document_type": document_type},
        files={"file": (filename, content, "application/pdf")},
        headers={"Idempotency-Key": f"pkg-up-{next(_SEQ)}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _zip(response: Any) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def _manifest(archive: zipfile.ZipFile) -> list[list[str]]:
    raw = archive.read("manifest.csv")
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


def _package(client: TestClient, certification_id: int) -> Any:
    return client.get(f"{CERTIFICATIONS}/{certification_id}/package")


def test_the_package_holds_owned_files_with_their_exact_bytes_and_a_matching_manifest(
    cert: TestClient,
) -> None:
    """인증 소유 FILE — 바이트 왕복(sha256 대조)·manifest 정합·한글 파일명·저장명 비노출"""
    certification_id = _certification()
    body = "한글 본문 ".encode() * 100
    doc = _upload(cert, certification_id, filename="인증서 원본.pdf", content=body)
    response = _package(cert, certification_id)
    assert response.headers["content-type"] == "application/zip"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    archive = _zip(response)
    names = archive.namelist()
    file_entries = [name for name in names if name.startswith("documents/")]
    assert len(file_entries) == 1 and file_entries[0].endswith("인증서 원본.pdf")
    assert file_entries[0].startswith("documents/01_")
    payload = archive.read(file_entries[0])
    assert payload == body and hashlib.sha256(payload).hexdigest() == doc["sha256"]
    header, *rows = _manifest(archive)
    assert header[:3] == ["순번", "서류종류", "zip 내 경로"]
    assert len(rows) == 1 and rows[0][2] == file_entries[0] and rows[0][6] == doc["sha256"]
    assert rows[0][3] == "파일" and rows[0][8] == "인증 소유"
    assert doc.get("stored_name") is None
    with unit_of_work() as uow:
        stored = uow.session.execute(select(Document.stored_name)).scalars().all()
    assert not any(str(name) in entry for name in stored for entry in names)  # 저장명 비노출
    readme = archive.read("README.txt").decode("utf-8")
    assert "발송은 사람이" in readme or "사람이 합니다" in readme
    assert "links.txt" not in names  # LINK가 없으면 만들지 않는다


def test_task_linked_documents_of_other_owners_are_included_and_links_go_to_the_list(
    cert: TestClient,
) -> None:
    """태스크가 서류로 연결한 문서(SKU 소유 파일·링크)도 들어간다 — 통신 기록 첨부와 삭제된 문서는 아니다"""
    certification_id = _certification()
    sku_id = _sku_of(certification_id)
    linked_file = _upload(
        cert,
        sku_id,
        filename="cfs.pdf",
        content=b"CFS-BYTES",
        owner_type="SKU",
        document_type="CFS",
    )
    link_id = create_link_document(sku_id, valid_until=None, owner_type="SKU", document_type="GMP")
    _link_task(cert, certification_id, linked_file["id"], seq=1)
    _link_task(cert, certification_id, link_id, seq=2)
    _upload(cert, certification_id, filename="own.pdf", content=b"OWN")
    # 제외 대상: 통신 기록 첨부, 삭제된 문서, 다른 인증의 문서
    other = _certification()
    _upload(cert, other, filename="other.pdf", content=b"OTHER")
    log_doc = create_link_document(
        999, valid_until=None, owner_type="COMM_LOG", document_type="CFS", tag="log"
    )
    deleted = _upload(cert, certification_id, filename="deleted.pdf", content=b"DEL")
    assert cert.delete(f"{DOCUMENTS}/{deleted['id']}").status_code == 204
    assert log_doc

    archive = _zip(_package(cert, certification_id))
    names = archive.namelist()
    assert {"manifest.csv", "README.txt", "links.txt"} <= set(names)
    files = sorted(name for name in names if name.startswith("documents/"))
    assert len(files) == 2
    assert any(name.endswith("cfs.pdf") for name in files) and any(
        name.endswith("own.pdf") for name in files
    )
    assert not any("other.pdf" in name or "deleted.pdf" in name for name in names)
    rows = _manifest(archive)[1:]
    assert len(rows) == 3  # 소유 1 + 태스크 연결 2(파일 1·링크 1)
    sources = sorted(row[8] for row in rows)
    assert sources == ["인증 소유", "태스크 연결", "태스크 연결"]
    links = archive.read("links.txt").decode("utf-8")
    assert links.startswith("0") and ": https://example.com/" in links
    link_row = next(row for row in rows if row[3] == "링크")
    assert link_row[2] == "" and link_row[7].startswith("https://")


def _sku_of(certification_id: int) -> int:
    from app.modules.certifications.models import Certification

    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None and row.target_id is not None
        return row.target_id


def _link_task(client: TestClient, certification_id: int, document_id: int, *, seq: int) -> None:
    task = client.post(
        f"{CERTIFICATIONS}/{certification_id}/tasks",
        json={"seq": seq, "item_name": f"서류 {seq}", "is_required": True},
        headers={"Idempotency-Key": f"pkg-task-{next(_SEQ)}"},
    )
    assert task.status_code == 201, task.text
    linked = client.patch(
        f"{CERTIFICATIONS}/{certification_id}/tasks/{task.json()['id']}",
        json={"version": task.json()["version"], "document_id": document_id},
    )
    assert linked.status_code == 200, linked.text


def test_a_document_that_is_both_owned_and_linked_appears_once(cert: TestClient) -> None:
    certification_id = _certification()
    doc = _upload(cert, certification_id, filename="both.pdf", content=b"BOTH")
    _link_task(cert, certification_id, doc["id"], seq=1)
    rows = _manifest(_zip(_package(cert, certification_id)))[1:]
    assert len(rows) == 1 and rows[0][8] == "인증 소유·태스크 연결"


def test_same_filenames_do_not_collide_and_path_pieces_are_stripped(cert: TestClient) -> None:
    """이름 충돌은 순번 접두로 해소, 경로 조각·금지 문자는 제거(zip-slip 방지)"""
    certification_id = _certification()
    _upload(cert, certification_id, filename="same.pdf", content=b"ONE")
    _upload(cert, certification_id, filename="same.pdf", content=b"TWO")
    _upload(cert, certification_id, filename="../../evil:name?.pdf", content=b"EVIL")
    archive = _zip(_package(cert, certification_id))
    files = [name for name in archive.namelist() if name.startswith("documents/")]
    assert len(files) == 3 and len(set(files)) == 3
    for name in archive.namelist():
        assert ".." not in name and not name.startswith("/") and "\\" not in name
        assert name.count("/") <= 1
    assert sorted(archive.read(name) for name in files) == [b"EVIL", b"ONE", b"TWO"]


def test_a_missing_file_blocks_the_package_with_a_409_listing_it(
    cert: TestClient, _isolated_storage: Path
) -> None:
    """실물 유실 — 조용히 빼지 않고 409 + 문서 목록(빠진 채 나가면 더 위험하다)"""
    certification_id = _certification()
    good = _upload(cert, certification_id, filename="good.pdf", content=b"GOOD")
    lost = _upload(cert, certification_id, filename="lost.pdf", content=b"LOST")
    with unit_of_work() as uow:
        stored = uow.session.execute(
            select(Document.stored_name).where(Document.id == lost["id"])
        ).scalar_one()
    (_isolated_storage / stored).unlink()
    response = _package(cert, certification_id)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CERTIFICATIONS.PACKAGE.FILES_UNAVAILABLE"
    assert error["detail"]["documents"] == [{"document_id": lost["id"], "reason": "MISSING"}]
    assert good["id"] not in [item["document_id"] for item in error["detail"]["documents"]]


def test_a_tampered_file_is_refused_by_its_hash(cert: TestClient, _isolated_storage: Path) -> None:
    """저장된 바이트가 등록 해시와 다르면 409 HASH_MISMATCH — 손상본을 내보내지 않는다"""
    certification_id = _certification()
    doc = _upload(cert, certification_id, filename="tamper.pdf", content=b"ORIGINAL")
    with unit_of_work() as uow:
        stored = uow.session.execute(
            select(Document.stored_name).where(Document.id == doc["id"])
        ).scalar_one()
    (_isolated_storage / stored).write_bytes(b"CORRUPTED")
    response = _package(cert, certification_id)
    assert response.status_code == 409
    assert response.json()["error"]["detail"]["documents"] == [
        {"document_id": doc["id"], "reason": "HASH_MISMATCH"}
    ]


def test_the_file_count_and_total_size_limits_are_422(
    cert: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상한 — 파일 수·합계 크기 초과는 422(사용자 안내), 경계값 그대로는 통과"""
    certification_id = _certification()
    _upload(cert, certification_id, filename="a.pdf", content=b"AAAA")
    _upload(cert, certification_id, filename="b.pdf", content=b"BBBB")
    monkeypatch.setattr(package, "MAX_FILES", 2)
    assert _package(cert, certification_id).status_code == 200  # 정확히 상한
    monkeypatch.setattr(package, "MAX_FILES", 1)
    over = _package(cert, certification_id)
    assert (
        over.status_code == 422
        and over.json()["error"]["code"] == "CERTIFICATIONS.PACKAGE.TOO_LARGE"
    )
    monkeypatch.setattr(package, "MAX_FILES", 100)
    monkeypatch.setattr(package, "MAX_TOTAL_BYTES", 8)
    assert _package(cert, certification_id).status_code == 200  # 합계 8바이트 = 상한
    monkeypatch.setattr(package, "MAX_TOTAL_BYTES", 7)
    assert _package(cert, certification_id).status_code == 422


def test_a_large_package_spills_to_disk_and_still_round_trips(
    cert: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """메모리 한도(8MiB)를 넘어 디스크로 내려도 zip이 온전하다(한도를 1바이트로 낮춰 강제)"""
    monkeypatch.setattr(package, "SPOOL_BYTES", 1)
    certification_id = _certification()
    body = bytes(range(256)) * 400
    _upload(cert, certification_id, filename="big.pdf", content=body)
    archive = _zip(_package(cert, certification_id))
    (name,) = [n for n in archive.namelist() if n.startswith("documents/")]
    assert archive.read(name) == body
    assert archive.testzip() is None


def test_an_empty_package_still_carries_the_manifest_and_readme(cert: TestClient) -> None:
    archive = _zip(_package(cert, _certification()))
    assert sorted(archive.namelist()) == ["README.txt", "manifest.csv"]
    assert len(_manifest(archive)) == 1  # 머리글만


def test_every_role_may_download_and_unknown_or_deleted_certifications_are_404(
    viewer: TestClient, cert: TestClient
) -> None:
    from app.core.time import utcnow
    from app.modules.certifications.models import Certification

    certification_id = _certification()
    assert _package(viewer, certification_id).status_code == 200
    assert _package(viewer, 999999).status_code == 404
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.deleted_at = utcnow()
    assert _package(cert, certification_id).status_code == 404


def test_the_readme_states_the_certification_and_the_no_dispatch_notice(cert: TestClient) -> None:
    certification_id = _certification()
    readme = _zip(_package(cert, certification_id)).read("README.txt").decode("utf-8")
    name = cert.get(f"{CERTIFICATIONS}/{certification_id}").json()["template_name"]
    assert f"{name} (#{certification_id})" in readme
    assert "KST" in readme and "사람이" in readme


def test_a_document_of_a_deleted_task_link_is_no_longer_included(cert: TestClient) -> None:
    """태스크를 지우면 그 태스크가 연결했던(다른 소유) 문서는 묶음에서 빠진다"""
    certification_id = _certification()
    sku_id = _sku_of(certification_id)
    doc = create_link_document(sku_id, valid_until=None, owner_type="SKU", document_type="CFS")
    _link_task(cert, certification_id, doc, seq=1)
    assert len(_manifest(_zip(_package(cert, certification_id)))) == 2
    tasks = cert.get(f"{CERTIFICATIONS}/{certification_id}/tasks").json()["items"]
    assert (
        cert.delete(f"{CERTIFICATIONS}/{certification_id}/tasks/{tasks[0]['id']}").status_code
        == 204
    )
    assert len(_manifest(_zip(_package(cert, certification_id)))) == 1


def test_path_components_drop_separators_reserved_characters_and_dots() -> None:
    """경로 조각 정화 — 구분자·예약 문자·제어 문자·선행/후행 점 제거, 빈 값은 file"""
    assert package._component('a/b\\c:d*e?"f<g>h|i') == "a_b_c_d_e__f_g_h_i"
    assert package._component("..") == "file" and package._component("") == "file"
    assert package._component("x\x00y\n") == "x_y_"
    assert len(package._component("가" * 500)) == 120
