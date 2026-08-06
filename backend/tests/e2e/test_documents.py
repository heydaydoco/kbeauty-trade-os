"""B. 서류 — 문서 보관소 (§4.7·§4.8 / ADR-0028·0029 / WBS S1-3 PR-2).

■ 이 파일이 고정하는 경계

  파일 보안 4항(웹 세션 승인 조건 4): ① 저장 파일명=서버 생성(사용자 파일명
  불사용) ② 저장 루트 밖 접근 불가(통로는 다운로드 엔드포인트 하나) ③ 다운로드
  attachment 고정·inline 렌더 금지 ④ 크기 상한 수치·거부. 그리고 파기 잠금
  (보존기한 내 soft delete 거부), SET SKU MSDS 서비스 가드(웹 세션 승인 —
  DB CHECK 대체분), 해시 멱등, 품목군 서류 세트.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from app.core.config import settings
from app.core.time import today_kst
from app.main import app
from app.modules.documents import service as documents_service
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_item_profile,
    create_sku,
    create_user,
)

pytestmark = pytest.mark.group_b

LOGIN = "/api/v1/auth/login"
DOCUMENTS = "/api/v1/documents"
DOCUMENT_TYPES = "/api/v1/document-types"
SKUS = "/api/v1/skus"
PROFILES = "/api/v1/item-profiles"

Upload = Callable[..., Response]
Link = Callable[..., Response]


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """저장 루트를 테스트 임시 폴더로 — 실 저장소를 오염시키지 않는다."""
    monkeypatch.setattr(settings, "file_storage_root", str(tmp_path / "files"))
    return tmp_path / "files"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade@example.com", RoleCode.TRADE)


@pytest.fixture
def certifier() -> Iterator[TestClient]:
    yield from _client("cert@example.com", RoleCode.CERT)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer@example.com", RoleCode.VIEWER)


@pytest.fixture
def sku_id() -> int:
    return create_sku()


@pytest.fixture
def upload(trader: TestClient, sku_id: int) -> Upload:
    def _upload(
        *,
        key: str = "doc1",
        filename: str = "msds.pdf",
        content: bytes = b"%PDF-1.7 test",
        content_type: str = "application/pdf",
        **form_overrides: Any,
    ) -> Response:
        form: dict[str, Any] = {
            "owner_type": "SKU",
            "owner_id": sku_id,
            "document_type": "MSDS",
        }
        form.update(form_overrides)
        return trader.post(
            f"{DOCUMENTS}/files",
            data={k: str(v) for k, v in form.items() if v is not None},
            files={"file": (filename, content, content_type)},
            headers={"Idempotency-Key": key},
        )

    return _upload


@pytest.fixture
def link(trader: TestClient, sku_id: int) -> Link:
    def _link(*, key: str = "lnk1", **overrides: Any) -> Response:
        body: dict[str, Any] = {
            "owner_type": "SKU",
            "owner_id": sku_id,
            "document_type": "MSDS",
            "url": "https://example.com/msds.pdf",
        }
        body.update(overrides)
        return trader.post(f"{DOCUMENTS}/links", json=body, headers={"Idempotency-Key": key})

    return _link


# ── 종류 목록 (§4.7 시드) ──────────────────────────────────────────────────


def test_document_types_list_serves_the_seed(trader: TestClient) -> None:
    """종류 목록이 시드를 그대로 낸다 — 원산지 증빙에는 보존 연수 5가 실려 있다"""
    listed = trader.get(DOCUMENT_TYPES, params={"size": 200})
    assert listed.status_code == 200, listed.text
    by_code = {row["code"]: row for row in listed.json()["items"]}
    assert "MSDS" in by_code and "LABEL_ARTWORK" in by_code
    assert by_code["ORIGIN_EVIDENCE"]["retention_years"] == 5


# ── 업로드·다운로드 왕복 (§22 렌즈 11) ─────────────────────────────────────


def test_upload_then_list_then_download(trader: TestClient, upload: Upload, sku_id: int) -> None:
    """한글 파일명 업로드 → 목록 → 다운로드가 바이트 그대로 왕복한다"""
    content = "한글 MSDS 내용 — 왕복 검증".encode()
    created = upload(filename="수분세럼 MSDS.pdf", content=content)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["storage_kind"] == "FILE"
    assert body["original_filename"] == "수분세럼 MSDS.pdf"
    assert body["size_bytes"] == len(content)
    assert len(body["sha256"]) == 64
    assert body["owner_display"] == "SKU-001"

    listed = trader.get(DOCUMENTS, params={"owner_type": "SKU", "owner_id": sku_id})
    assert listed.json()["total"] == 1

    downloaded = trader.get(f"{DOCUMENTS}/{body['id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == content


def test_stored_name_is_server_generated_and_inside_the_root(
    upload: Upload, _isolated_storage: Path
) -> None:
    """① 저장 파일명은 서버 생성 hex다 — 사용자 파일명은 경로에 쓰이지 않는다"""
    assert upload(filename="원본이름.pdf").status_code == 201
    stored = list(_isolated_storage.iterdir())
    assert len(stored) == 1
    name = stored[0].name
    assert name != "원본이름.pdf"
    assert len(name) == 32 and all(ch in "0123456789abcdef" for ch in name)


@pytest.mark.group_k
def test_a_traversal_filename_cannot_escape_the_root(
    upload: Upload, _isolated_storage: Path, tmp_path: Path
) -> None:
    """경로 탈출 차단 (§20 K) — 조작된 파일명도 루트 밖에 아무것도 만들지 않는다"""
    created = upload(filename="..\\..\\evil.pdf")
    assert created.status_code == 201, created.text
    # 저장 루트 밖(tmp_path 바로 아래)에는 아무것도 생기지 않았다.
    outside = [p for p in tmp_path.iterdir() if p.name != "files"]
    assert outside == []
    # 원본 파일명은 마지막 조각으로 정규화되어 표시에만 쓰인다.
    assert created.json()["original_filename"] == "evil.pdf"


@pytest.mark.group_k
def test_the_download_is_attachment_only(trader: TestClient, upload: Upload) -> None:
    """③ 다운로드는 attachment 고정 + octet-stream + nosniff — inline 렌더 금지"""
    document_id = upload(filename="라벨 시안.pdf").json()["id"]
    downloaded = trader.get(f"{DOCUMENTS}/{document_id}/download")
    disposition = downloaded.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "%EB%9D%BC%EB%B2%A8" in disposition  # RFC 5987 — 한글 파일명 인코딩
    assert downloaded.headers["content-type"].startswith("application/octet-stream")
    assert downloaded.headers["x-content-type-options"] == "nosniff"


@pytest.mark.group_k
def test_the_size_limit_is_pinned_and_enforced(upload: Upload) -> None:
    """④ 크기 상한 = 20MiB 명시 수치. 초과는 413이고 실물은 남지 않는다"""
    assert documents_service.MAX_UPLOAD_BYTES == 20 * 1024 * 1024
    too_big = upload(content=b"0" * (documents_service.MAX_UPLOAD_BYTES + 1))
    assert too_big.status_code == 413, too_big.text
    assert too_big.json()["error"]["code"] == "DOCUMENTS.FILE.TOO_LARGE"


@pytest.mark.group_k
def test_oversize_upload_leaves_no_file_behind(upload: Upload, _isolated_storage: Path) -> None:
    """거부된 업로드는 쓰다 만 실물을 지운다 — 행 없는 파일은 유령이다"""
    upload(content=b"0" * (documents_service.MAX_UPLOAD_BYTES + 1))
    leftovers = list(_isolated_storage.iterdir()) if _isolated_storage.exists() else []
    assert leftovers == []


@pytest.mark.group_k
def test_an_executable_extension_is_rejected(upload: Upload) -> None:
    """업로드 실행 불가 (§20 K·§18.1) — 허용 목록 밖 확장자는 422다"""
    rejected = upload(filename="evil.exe", content=b"MZ....")
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "DOCUMENTS.FILE.TYPE_NOT_ALLOWED"


@pytest.mark.group_k
def test_an_inline_renderable_extension_is_rejected(upload: Upload) -> None:
    """.html도 목록 밖이다 — attachment 고정에 더한 이중 안전(ADR-0029)"""
    rejected = upload(filename="page.html", content=b"<script>alert(1)</script>")
    assert rejected.status_code == 422, rejected.text


def test_an_empty_file_is_rejected(upload: Upload) -> None:
    rejected = upload(content=b"")
    assert rejected.status_code == 422, rejected.text


def test_downloading_a_link_document_is_refused(trader: TestClient, link: Link) -> None:
    """LINK형은 내려받을 실물이 없다 — 409로 알려 준다"""
    document_id = link().json()["id"]
    refused = trader.get(f"{DOCUMENTS}/{document_id}/download")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "DOCUMENTS.DOWNLOAD.NOT_A_FILE"


# ── LINK 등록 ──────────────────────────────────────────────────────────────


def test_a_link_document_records_the_url(trader: TestClient, link: Link, sku_id: int) -> None:
    created = link()
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["storage_kind"] == "LINK"
    assert body["url"] == "https://example.com/msds.pdf"
    assert body["sha256"] is None


def test_a_non_http_url_is_rejected(link: Link) -> None:
    """링크가 아니면 근거가 아니다 — javascript: 류는 형식에서 거른다"""
    rejected = link(url="javascript:alert(1)")
    assert rejected.status_code == 422, rejected.text


# ── SET SKU MSDS 가드 (웹 세션 승인 — DB CHECK 대체분) ─────────────────────


@pytest.fixture
def set_sku_id() -> int:
    from app.core.db.uow import unit_of_work
    from app.modules.catalog.models import Sku

    with unit_of_work() as uow:
        row = Sku(sku_code="SET-DOC", name_ko="문서 세트", kind="SET", product_id=None)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def test_msds_cannot_attach_to_a_set_sku(link: Link, set_sku_id: int) -> None:
    """세트 SKU에는 MSDS를 연결할 수 없다 — 구 set_has_no_dg의 승계(서비스 가드)"""
    refused = link(owner_id=set_sku_id)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "DOCUMENTS.MSDS.SET_SKU_FORBIDDEN"


def test_msds_upload_to_a_set_sku_is_also_refused(upload: Upload, set_sku_id: int) -> None:
    """FILE 경로도 같은 가드를 지난다 — 통로가 갈려도 규칙은 하나다"""
    refused = upload(owner_id=set_sku_id)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "DOCUMENTS.MSDS.SET_SKU_FORBIDDEN"


def test_other_document_types_may_attach_to_a_set_sku(link: Link, set_sku_id: int) -> None:
    """가드는 MSDS 한정이다 — 세트의 인증서 문서는 허용된다(자기검사)"""
    allowed = link(owner_id=set_sku_id, document_type="CERTIFICATE")
    assert allowed.status_code == 201, allowed.text


# ── 보존기한·파기 잠금 (§4.7) ──────────────────────────────────────────────


def test_origin_evidence_requires_an_issue_date(link: Link) -> None:
    """원산지 증빙은 발급일 없이 등록되지 않는다 — 보존기한(발급일+5년)을 셀 수 없다"""
    refused = link(document_type="ORIGIN_EVIDENCE")
    assert refused.status_code == 422, refused.text
    assert "발급일" in refused.json()["error"]["detail"]["issued_on"]


def test_origin_evidence_retention_defaults_to_issue_plus_five_years(link: Link) -> None:
    """보존기한 기본값 = 발급일+5년 (§4.7 / 웹 세션 승인 문면)"""
    created = link(document_type="ORIGIN_EVIDENCE", issued_on="2026-03-15")
    assert created.status_code == 201, created.text
    assert created.json()["retention_until"] == "2031-03-15"


def test_an_explicit_retention_date_wins(link: Link) -> None:
    """명시한 보존기한이 기본 계산보다 우선한다 — §4.7의 '기본'은 대체 가능이다"""
    created = link(
        document_type="ORIGIN_EVIDENCE", issued_on="2026-03-15", retention_until="2040-01-01"
    )
    assert created.status_code == 201, created.text
    assert created.json()["retention_until"] == "2040-01-01"


def test_deleting_inside_the_retention_period_is_refused(trader: TestClient, link: Link) -> None:
    """파기 잠금 — 보존기한 내 soft delete는 409로 거부된다 (§4.7)"""
    created = link(document_type="ORIGIN_EVIDENCE", issued_on=today_kst().isoformat())
    document_id = created.json()["id"]

    refused = trader.delete(f"{DOCUMENTS}/{document_id}")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "DOCUMENTS.DOCUMENT.RETENTION_LOCKED"
    # 잠긴 문서는 그대로 남아 있다.
    assert trader.get(f"{DOCUMENTS}/{document_id}").status_code == 200


def test_deleting_after_the_retention_period_succeeds(
    trader: TestClient, link: Link, _isolated_storage: Path
) -> None:
    """보존기한이 지난 문서는 삭제된다 — 잠금은 기한의 함수이지 종류의 낙인이 아니다"""
    long_ago = (today_kst() - timedelta(days=365 * 6)).isoformat()
    created = link(document_type="ORIGIN_EVIDENCE", issued_on=long_ago)
    assert created.json()["retention_until"] < today_kst().isoformat()

    deleted = trader.delete(f"{DOCUMENTS}/{created.json()['id']}")
    assert deleted.status_code == 204, deleted.text
    assert trader.get(f"{DOCUMENTS}/{created.json()['id']}").status_code == 404


def test_a_document_without_retention_deletes_freely(
    trader: TestClient, upload: Upload, _isolated_storage: Path
) -> None:
    """보존 규율 없는 종류는 바로 삭제된다. 실물은 남는다(soft delete — 복원 가능)"""
    document_id = upload().json()["id"]
    assert trader.delete(f"{DOCUMENTS}/{document_id}").status_code == 204
    listed = trader.get(DOCUMENTS)
    assert listed.json()["total"] == 0
    assert len(list(_isolated_storage.iterdir())) == 1  # 실물 정리는 S2-4 정책의 일


# ── 멱등·중복 (§17.4) ──────────────────────────────────────────────────────


@pytest.mark.group_j
def test_a_double_click_upload_stores_one_document_and_one_file(
    trader: TestClient, upload: Upload, _isolated_storage: Path
) -> None:
    """같은 키 재수신은 최초 결과 반환 — 행도 실물도 하나다 (§17.4)"""
    first = upload(key="twice")
    replay = upload(key="twice")
    assert first.status_code == 201 and replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert trader.get(DOCUMENTS).json()["total"] == 1
    assert len(list(_isolated_storage.iterdir())) == 1  # 재생분 실물은 버려졌다


def test_the_same_content_twice_on_one_owner_is_refused(upload: Upload) -> None:
    """다른 키로 같은 파일을 또 올리면 해시 중복으로 거절된다 (§12.1 중복 감지)"""
    assert upload(key="h1").status_code == 201
    duplicate = upload(key="h2", filename="다른이름.pdf")
    assert duplicate.status_code == 422, duplicate.text
    assert "이미 등록" in duplicate.json()["error"]["detail"]["file"]


def test_the_same_content_on_another_sku_is_allowed(upload: Upload) -> None:
    """같은 파일도 소유자가 다르면 등록된다 (용량 변형이 MSDS를 공유하는 실무)"""
    assert upload(key="o1").status_code == 201
    other_sku = create_sku("SKU-OTHER")
    assert upload(key="o2", owner_id=other_sku).status_code == 201


# ── 라벨 소유 문서 (승격분 소비 경로) ──────────────────────────────────────


def test_a_label_file_rides_on_the_label_listing(
    trader: TestClient, upload: Upload, sku_id: int
) -> None:
    """라벨에 올린 파일이 SKU 상세 라벨 목록의 documents로 나온다 (구 file_url 자리)"""
    label = trader.post(
        f"{SKUS}/{sku_id}/labels",
        json={"country_code": "US", "label_version": 1, "language": "en"},
        headers={"Idempotency-Key": "lbl"},
    )
    assert label.status_code == 201, label.text
    label_id = label.json()["id"]

    created = upload(
        owner_type="LABEL",
        owner_id=label_id,
        document_type="LABEL_ARTWORK",
        filename="US v1 아트웍.pdf",
    )
    assert created.status_code == 201, created.text
    assert created.json()["owner_display"] == "SKU-001 · US v1(en)"

    listed = trader.get(f"{SKUS}/{sku_id}/labels").json()
    docs = listed["items"][0]["documents"]
    assert len(docs) == 1
    assert docs[0]["document_id"] == created.json()["id"]
    assert docs[0]["original_filename"] == "US v1 아트웍.pdf"
    assert docs[0]["storage_kind"] == "FILE"


def test_an_unknown_label_owner_is_rejected(upload: Upload) -> None:
    refused = upload(owner_type="LABEL", owner_id=999_999, document_type="LABEL_ARTWORK")
    assert refused.status_code == 422, refused.text


def test_an_unknown_owner_type_is_rejected(link: Link) -> None:
    refused = link(owner_type="SHIPMENT")
    assert refused.status_code == 422, refused.text


def test_an_unknown_document_type_is_rejected(link: Link) -> None:
    refused = link(document_type="MYSTERY")
    assert refused.status_code == 422, refused.text
    assert "등록되지 않은 문서 종류" in refused.json()["error"]["detail"]["document_type"]


# ── 목록 필터·CSV ──────────────────────────────────────────────────────────


def test_the_list_filters_by_type_and_owner(
    trader: TestClient, upload: Upload, link: Link, sku_id: int
) -> None:
    upload(key="f1")
    link(key="f2", document_type="CERTIFICATE", url="https://example.com/cert.pdf")
    by_type = trader.get(DOCUMENTS, params={"document_type": "MSDS"})
    assert by_type.json()["total"] == 1
    by_owner = trader.get(DOCUMENTS, params={"owner_type": "SKU", "owner_id": sku_id})
    assert by_owner.json()["total"] == 2


def test_a_bogus_owner_type_filter_is_rejected(trader: TestClient) -> None:
    refused = trader.get(DOCUMENTS, params={"owner_type": "BOGUS"})
    assert refused.status_code == 422, refused.text


def test_csv_export_has_bom_and_korean_header(trader: TestClient, upload: Upload) -> None:
    """문서 목록 CSV — BOM·CRLF·한글 무손실 (§12.2)"""
    upload()
    exported = trader.get(f"{DOCUMENTS}/export.csv")
    assert exported.status_code == 200, exported.text
    text = exported.content.decode("utf-8")
    assert text.startswith("﻿")
    assert "\r\n" in text
    assert "종류" in text and "보존기한" in text


# ── 품목군 서류 세트 (§4.8) ────────────────────────────────────────────────


def test_profile_document_set_round_trip(trader: TestClient) -> None:
    """세트에 종류를 넣고·보고·빼고·다시 넣는다 (§4.8 — 재추가는 신규다)"""
    profile_id = create_item_profile("PRF-SET")
    added = trader.post(
        f"{PROFILES}/{profile_id}/document-types",
        json={"document_type": "CFS"},
        headers={"Idempotency-Key": "ps1"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["document_type_name"] == "자유판매증명서(CFS)"

    listed = trader.get(f"{PROFILES}/{profile_id}/document-types")
    assert listed.json()["total"] == 1

    duplicate = trader.post(
        f"{PROFILES}/{profile_id}/document-types",
        json={"document_type": "CFS"},
        headers={"Idempotency-Key": "ps2"},
    )
    assert duplicate.status_code == 422, duplicate.text

    removed = trader.delete(f"{PROFILES}/{profile_id}/document-types/{added.json()['id']}")
    assert removed.status_code == 204, removed.text
    assert trader.get(f"{PROFILES}/{profile_id}/document-types").json()["total"] == 0

    re_added = trader.post(
        f"{PROFILES}/{profile_id}/document-types",
        json={"document_type": "CFS"},
        headers={"Idempotency-Key": "ps3"},
    )
    assert re_added.status_code == 201, re_added.text  # soft delete 후 재유입 = 신규


def test_a_missing_profile_is_rejected(trader: TestClient) -> None:
    """없는 품목군의 세트 조회는 거부된다 (require_profile — 422 한국어 안내)"""
    refused = trader.get(f"{PROFILES}/999999/document-types")
    assert refused.status_code == 422, refused.text


# ── 권한 (§18.1) ───────────────────────────────────────────────────────────


@pytest.mark.group_k
def test_a_certifier_may_register_documents(certifier: TestClient, sku_id: int) -> None:
    """CERT 역할도 문서를 등록한다 — 역할 시드 문면("…문서")이 근거다"""
    created = certifier.post(
        f"{DOCUMENTS}/links",
        json={
            "owner_type": "SKU",
            "owner_id": sku_id,
            "document_type": "CERTIFICATE",
            "url": "https://example.com/cpnp.pdf",
        },
        headers={"Idempotency-Key": "cert1"},
    )
    assert created.status_code == 201, created.text


@pytest.mark.group_k
def test_a_viewer_reads_but_cannot_write(
    viewer: TestClient, trader: TestClient, upload: Upload
) -> None:
    """조회 역할은 목록·다운로드만 — 등록·삭제는 403이다"""
    document_id = upload().json()["id"]

    assert viewer.get(DOCUMENTS).status_code == 200
    assert viewer.get(f"{DOCUMENTS}/{document_id}/download").status_code == 200

    denied_link = viewer.post(
        f"{DOCUMENTS}/links",
        json={
            "owner_type": "SKU",
            "owner_id": 1,
            "document_type": "MSDS",
            "url": "https://example.com/x.pdf",
        },
        headers={"Idempotency-Key": "v1"},
    )
    assert denied_link.status_code == 403, denied_link.text
    assert viewer.delete(f"{DOCUMENTS}/{document_id}").status_code == 403


@pytest.mark.group_k
def test_unauthenticated_requests_are_401(upload: Upload, trader: TestClient) -> None:
    """비인증은 다운로드 통로에서도 401 — 파일 접근 단일 통로의 전제(②)"""
    document_id = upload().json()["id"]
    with TestClient(app) as anonymous:
        assert anonymous.get(DOCUMENTS).status_code == 401
        assert anonymous.get(f"{DOCUMENTS}/{document_id}/download").status_code == 401
