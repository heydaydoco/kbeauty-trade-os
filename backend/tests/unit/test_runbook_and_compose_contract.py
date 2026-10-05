"""K·H. 운영 문서·compose 계약 (§21 / S2-4 PR-3 안건 ⑨ — ADR-0050).

★ 문서가 조용히 낡는 것을 막는 최소 계약이다: ① 수기 양식의 필수 항목·시스템 입력 대응표가 **실제 필드**를
  가리키는가(필드가 바뀌면 문서가 깨져 보인다) ② runbook 상호 링크가 실재하는가 ③ 백업 서비스의 안전 규약
  (패스프레이즈 필수·dev 기본 미기동·prod 읽기 전용 마운트·볼륨 복제 경고)이 compose·문서에 있는가.
파일이 없는 환경(리포 루트가 보이지 않는 컨테이너)에서는 skip한다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.group_h, pytest.mark.group_k]

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = REPO / "docs" / "runbook"
FORM = RUNBOOK / "forms" / "manual-record-form.md"
SOP = RUNBOOK / "incident-sop.md"
BACKUP_DOC = RUNBOOK / "backup-restore.md"

#: CI는 KBOS_REQUIRE_SCRIPT_TESTS=1을 주어 skip 대신 **실패**하게 한다 — 러너 이미지가 바뀌어 검증이 조용히 사라지는 것을 막는다.
REQUIRED = os.environ.get("KBOS_REQUIRE_SCRIPT_TESTS") == "1"
needs_repo = pytest.mark.skipif(
    not (REPO / "docs").is_dir() and not REQUIRED, reason="리포 루트가 보이지 않는다"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@needs_repo
@pytest.mark.parametrize(
    "required",
    [
        "실제 발생일",
        "대상",
        "종류",
        "상대방",
        "요지",
        "다음에 해야 할 일과 기한",
        "발급일",
        "유효기간",
        "파일명",
        "신청일",
        "승인일",
        "만료일",
        "사유",
        "작성자",
        "관리자 검산",
        "시스템에 입력함",
    ],
)
def test_the_manual_form_has_every_required_item(required: str) -> None:
    assert required in _text(FORM)


@needs_repo
def test_the_input_mapping_points_at_fields_that_really_exist() -> None:
    """양식 → 시스템 필드 대응표의 각 필드가 모델·스키마에 실제로 있다(이름이 바뀌면 이 테스트가 먼저 깨진다)"""
    from app.modules.certifications.schemas import CertificationTransitionRequest
    from app.modules.collaboration.models import CommLog
    from app.modules.documents.models import Document

    form = _text(FORM)
    for korean in (
        "발급일",
        "유효기간",
        "신청일",
        "승인일",
        "시작일",
        "만료일",
        "인증번호",
        "사유",
        "오간 날",
        "다음 액션",
        "기한",
    ):
        assert korean in form, korean
    assert {"issued_on", "valid_until"} <= set(Document.__table__.columns.keys())
    assert {
        "applied_on",
        "approved_on",
        "valid_from",
        "expires_on",
        "cert_number",
        "reason",
    } <= set(CertificationTransitionRequest.model_fields)
    assert {"occurred_on", "summary", "next_action", "next_action_due"} <= set(
        CommLog.__table__.columns.keys()
    )


@needs_repo
def test_the_shipment_rows_of_the_form_point_at_fields_that_really_exist() -> None:
    """수기 양식 4-2(선적 — S3-2 PR-8)의 대응표가 가리키는 필드가 실제 요청 스키마에 있다(이름이 바뀌면 양식이 먼저 깨진다)"""
    from app.modules.shipments.schemas import (
        CustomsRecordCreateRequest,
        MilestoneActualRequest,
        MilestoneNoticeRequest,
        MilestonePlanRequest,
    )

    form = _text(FORM)
    for korean in (
        "4-2. 선적인 경우",
        "선적 만들기",
        "수입선적 만들기",
        "계획 변경",
        "실적 입력",
        "통관 기록 추가",
    ):
        assert korean in form, korean
    for field in ("actual_on", "actual_at", "declaration_no", "declared_on", "accepted_on"):
        assert f"`{field}`" in form, field
    assert {"actual_on", "actual_at", "reason"} <= set(MilestoneActualRequest.model_fields)
    assert {"planned_on", "planned_at", "reason"} <= set(MilestonePlanRequest.model_fields)
    assert {"declaration_no", "declared_on", "accepted_on", "note"} <= set(
        CustomsRecordCreateRequest.model_fields
    )
    assert {"occurred_on", "summary"} <= set(MilestoneNoticeRequest.model_fields)


@needs_repo
def test_the_s3_2_opening_section_carries_the_operating_rules() -> None:
    """runbook S3-2 운영 개시(PR-8)가 계획서 PR-8 행의 항목을 전부 담는다 — 휴일 선언·물류 계정 경로·잡 14행·DG 경고·통관 이슈 임시 규칙·수기 양식"""
    prod = _text(RUNBOOK / "prod.md")
    section = prod[prod.index("## S3-2 운영 개시") :]
    for needle in (
        "create-admin",
        "사용자·역할",
        "회수",
        "휴일 캘린더 등록",
        "근거 링크",
        "확인 불가",
        "위험물(DG)",
        "통관 이슈 임시 규칙",
        "forms/manual-record-form.md",
        "trade-deadline-scan",
    ):
        assert needle in section, needle
    assert "### 잡 표 — 레지스트리 14행" in prod and "### ⑧ 배치 14개" in prod
    assert "사용자 역할을 바꾸는 화면은 아직 없다" not in prod  # PR-7 이후 낡은 문장 잔존 금지


@needs_repo
def test_the_sop_states_the_date_rule_the_steps_and_the_one_time_reconciliation() -> None:
    sop = _text(SOP)
    assert "증빙일 = 실제 발생일" in sop and "입력일 = 복구일" in sop
    for heading in ("수기 기록", "소급 입력", "검산 1회"):
        assert heading in sop
    assert "미래 날짜를 막는 필드는" in sop and "통신 기록의 오간 날" in sop
    assert "forms/manual-record-form.md" in sop and "backup-restore.md" in sop


@needs_repo
def test_runbook_relative_links_resolve() -> None:
    link = re.compile(r"\]\((?!https?://|#)([^)#]+)(#[^)]*)?\)")
    checked = 0
    for path in RUNBOOK.rglob("*.md"):
        for match in link.finditer(_text(path)):
            target = (path.parent / match.group(1)).resolve()
            assert target.exists(), f"{path.relative_to(REPO)} → {match.group(1)}"
            checked += 1
    assert checked >= 8  # 검사가 공회전하지 않는다


@needs_repo
def test_the_backup_doc_carries_the_safety_warnings() -> None:
    doc = _text(BACKUP_DOC)
    for phrase in (
        "KBOS_BACKUP_PASSPHRASE",
        "잃으면 백업을 풀 수 없다",
        "호스트 밖",
        "리허설",
        "GET /api/v1/system/backups",
        "관리자 승인",
    ):
        assert phrase in doc, phrase
    prod = _text(RUNBOOK / "prod.md")
    assert (
        "backup-restore.md" in prod
        and "incident-sop.md" in prod
        and "KBOS_FILE_PURGE_ENABLED" in prod
    )


@needs_repo
def test_the_compose_backup_service_is_fail_closed_and_isolated() -> None:
    prod = yaml.safe_load(_text(REPO / "docker-compose.prod.yml"))
    dev = yaml.safe_load(_text(REPO / "docker-compose.yml"))
    backup = prod["services"]["backup"]
    assert backup["image"] == "postgres:16.14"
    assert (
        "${KBOS_BACKUP_PASSPHRASE:?" in backup["environment"]["KBOS_BACKUP_PASSPHRASE"]
    )  # 없으면 기동 거부
    assert "files_data:/data/files:ro" in backup["volumes"]  # 원본 파일 저장소는 읽기 전용
    assert "backups" in prod["volumes"]
    for name in ("api", "worker"):  # 앱은 백업 볼륨을 읽기 전용으로만 본다
        assert "backups:/backups:ro" in prod["services"][name]["volumes"]
        assert prod["services"][name]["environment"]["KBOS_BACKUP_DIR"] == "/backups"
    dev_backup = dev["services"]["backup"]
    assert dev_backup["profiles"] == ["backup"]  # dev 기본 기동에서 제외
    for name in (
        "api",
        "worker",
    ):  # dev는 백업 볼륨을 앱에 연결하지 않는다 → 신선도 감시 건너뜀(소음 없음)
        assert "KBOS_BACKUP_DIR" not in dev["services"][name]["environment"]
    example = _text(REPO / ".env.prod.example")
    assert "KBOS_BACKUP_PASSPHRASE=" in example


@needs_repo
def test_the_backup_scripts_exist_and_the_compose_command_runs_the_scheduler() -> None:
    for name in ("backup.sh", "restore-rehearsal.sh", "scheduler.sh", "common.sh"):
        assert (REPO / "infra" / "backup" / name).is_file(), name
    prod = yaml.safe_load(_text(REPO / "docker-compose.prod.yml"))
    assert prod["services"]["backup"]["command"] == ["bash", "/scripts/scheduler.sh"]
    assert "./infra/backup:/scripts:ro" in prod["services"]["backup"]["volumes"]
    scripts = _text(REPO / "infra" / "backup" / "backup.sh")
    assert (
        "require_passphrase" in scripts
        and "--snapshot" in scripts
        and "openssl" in _text(REPO / "infra" / "backup" / "common.sh")
    )


@needs_repo
def test_ui_labels_quoted_in_the_form_exist_in_the_screens() -> None:
    """양식이 쓰는 화면 라벨(유효 시작일·유효기간 만료일)이 실제 화면 소스에 있다 — 화면 문구가 바뀌면 양식이 어긋난다"""
    src = REPO / "frontend" / "src" / "routes"
    if not src.is_dir():
        pytest.skip("프런트 소스가 보이지 않는다")
    form = _text(FORM)
    assert "유효 시작일" in form and "유효 시작일" in _text(src / "certifications.tsx")
    assert "유효기간 만료일" in form and "유효기간 만료일" in _text(src / "documents.tsx")


@needs_repo
def test_runbook_anchors_resolve_to_real_headings() -> None:
    def slug(heading: str) -> str:
        return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", heading.strip().lower()))

    checked = 0
    for path in RUNBOOK.rglob("*.md"):
        for match in re.finditer(r"\]\(([^)#]+)#([^)]+)\)", _text(path)):
            target = (path.parent / match.group(1)).resolve()
            headings = {
                slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", _text(target), re.M)
            }
            assert match.group(2) in headings, f"{path.name} → {match.group(1)}#{match.group(2)}"
            checked += 1
    assert checked >= 1


@needs_repo
def test_the_restore_procedure_uses_the_real_script_and_the_compose_restore_roles() -> None:
    doc = _text(BACKUP_DOC)
    assert "/scripts/restore.sh" in doc and "--target-db" in doc and "--allow-nonempty" in doc
    assert (
        "20-grants.sql" not in doc
    )  # 권한은 ACL 복원이 맡는다 — 잘못된 '재실행으로 복구' 안내가 없다
    assert (REPO / "infra" / "backup" / "restore.sh").is_file()
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        env = yaml.safe_load(_text(REPO / name))["services"]["backup"]["environment"]
        assert env["RESTORE_ROLE"] == "kbos_owner" and env["RESTORE_APP_ROLE"] == "kbos_app"
