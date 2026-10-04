"""K(아키텍처) — 배치 레지스트리의 구조 계약 (§15 / 자동화 4금 / 부채 #12).

★ 이 파일이 고정하는 것:
  ① **코드→함수 매핑이 폐쇄 열거다.** 행을 넣는 것만으로 새 동작이 생기면
    scheduled_jobs에 한 줄 적는 행위가 곧 코드 배포이고, 자동화 4금(§15 L3)을
    데이터로 우회하는 통로가 열린다. 잡을 추가하려면 코드가 늘어야 한다.
  ② **scheduled_jobs에 행을 쓰는 코드는 실행기·서비스뿐이다.** 마이그레이션
    시드는 항구 금지다(함정 ⑩ — users FK가 TRUNCATE CASCADE로 PRESERVED_TABLES를
    무력화한다).
  ③ **등록 잡 전건이 4금 비저촉 동작이다** — 지출·법적 판정·대외 발신·장부
    확정을 하는 잡이 레지스트리에 없다는 사실을 이름과 대상으로 확인한다.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.modules.platform import models as platform_models
from app.modules.platform import scheduler

pytestmark = pytest.mark.group_k

_APP_DIR = Path(platform_models.__file__).resolve().parent.parent.parent
_MIGRATIONS_DIR = _APP_DIR.parent / "migrations" / "versions"

_JOB_INSERT = re.compile(r"ScheduledJob\s*\(")


def test_the_registry_is_a_closed_enumeration() -> None:
    """JOBS_BY_CODE는 JOB_REGISTRY에서만 만들어진다 — 런타임 등록 통로 0"""
    assert set(scheduler.JOBS_BY_CODE) == {spec.code for spec in scheduler.JOB_REGISTRY}
    assert len(scheduler.JOB_REGISTRY) == len(scheduler.JOBS_BY_CODE)


def test_every_registered_job_has_a_callable() -> None:
    for spec in scheduler.JOB_REGISTRY:
        assert callable(spec.run), spec.code


def test_registry_codes_are_stable_identifiers() -> None:
    """코드는 소문자 하이픈 — DB 행과 코드가 이 문자열로 만난다"""
    for spec in scheduler.JOB_REGISTRY:
        assert re.fullmatch(r"[a-z][a-z0-9-]{2,59}", spec.code), spec.code


#: 이 리포의 시드 관용구는 `op.execute("INSERT INTO …")`다(roles·document_types
#: 선례). `op.bulk_insert`만 찾으면 실제 시드를 하나도 못 잡는다.
_SEEDS = re.compile(r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE)

#: 앱 경로로만 채워야 하는 테이블 — users FK(ActorMixin)를 달아 시드하면
#: 테스트 정리의 TRUNCATE CASCADE가 PRESERVED_TABLES를 무력화한다(함정 ⑩).
_NEVER_SEEDED = (
    "scheduled_jobs",
    "notification_channels",
    "webhook_subscriptions",
    "policy_settings",  # S3-1 — 행이 없는 것이 정상 초기 상태(미설정=fail-closed)
    # S3-1 PR-9a — 승인 4표: 결재선은 업무 정책이라 코드·시드가 지어낼 수 없다(ADMIN 화면/API만 공급 — 등록 전 fail-closed),
    # 나머지 3표는 업무 행위의 기록이다.
    "approval_lines",
    "approvals",
    "approval_events",
    "delegations",
    "payments",  # S3-1 PR-10a — 입금 원장: 업무 행위의 기록(시드 불가·users FK 보유)
    "gate_evaluations",  # S3-1 PR-11a — 확정 시도 증적: 업무 행위의 기록(시드 불가·users FK 보유)
    "gate_overrides",  # S3-1 PR-11a — 통제된 예외 증적: 업무 행위의 기록(시드 불가·users FK 보유)
    "order_intakes",  # S3-1 PR-13a — 바이어 PO 스테이징: 업무 행위의 기록(시드 불가·users FK 보유)
    "order_intake_lines",
    "board_saved_filters",  # S3-1 PR-15a — 개인 설정(사용자 FK 소유 — 시드 불가)
    # S3-2 PR-2a — 휴일 캘린더: 근거 링크·확인일을 사람이 확인한 선언만(시드 0 — ADR-0082, 함정 ⑩·외부 자동 수집 0)
    "holiday_calendar_years",
    "holidays",
)


def test_no_migration_seeds_the_app_owned_tables() -> None:
    """마이그레이션 시드 항구 금지(함정 ⑩) — 등록 경로는 앱(CLI·API)뿐이다"""
    offenders: list[str] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        seeded = {match.lower() for match in _SEEDS.findall(path.read_text(encoding="utf-8"))}
        for table in _NEVER_SEEDED:
            if table in seeded:
                offenders.append(f"{path.name}:{table}")
    assert offenders == [], f"마이그레이션이 앱 소유 테이블을 시드합니다: {offenders}"


def test_the_seed_scan_recognises_this_repos_idiom() -> None:
    """공회전 방지 — 스캔이 리포의 실제 시드 관용구를 알아본다.

    `op.bulk_insert`만 찾던 종전 스캔은 이 리포에서 단 한 건도 잡지 못했다
    (roles·document_types·partners 시드가 전부 op.execute + INSERT INTO다).
    """
    assert _SEEDS.findall("op.execute(\"INSERT INTO roles (code) VALUES ('X')\")") == ["roles"]
    seeded_somewhere = any(
        _SEEDS.search(path.read_text(encoding="utf-8")) for path in _MIGRATIONS_DIR.glob("*.py")
    )
    assert seeded_somewhere, "시드하는 마이그레이션이 하나도 안 잡혔습니다 — 스캔이 헛돕니다"


def test_no_app_code_writes_notification_channel_rows() -> None:
    """채널·구독의 '0행 유지'(판정 요청 5)를 지키는 진짜 장치.

    행수를 세는 통합 테스트는 TRUNCATE 하네스 때문에 늘 0이라 아무것도 보증하지
    못한다 — 보증은 **쓰기 경로의 부재**다. S6-3에서 공급 경로를 열 때 이
    테스트가 먼저 빨개지고, 그 순간이 판정 지점이다.
    """
    offenders: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "models.py":
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?<!class )\b(NotificationChannel|WebhookSubscription)\s*\(", source):
            offenders.append(str(path.relative_to(_APP_DIR)))
    assert offenders == [], f"채널·구독 쓰기 경로가 생겼습니다: {offenders}"


def test_scheduled_jobs_rows_are_written_only_by_the_platform_module() -> None:
    """app 안에서 ScheduledJob 행을 만드는 코드는 실행기 하나뿐"""
    offenders: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name in {"scheduler.py", "models.py"}:
            continue
        if _JOB_INSERT.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_APP_DIR)))
    assert offenders == [], f"배치 등록이 실행기 밖에 있습니다: {offenders}"


def test_the_scan_is_not_idle() -> None:
    source = (_APP_DIR / "modules" / "platform" / "scheduler.py").read_text(encoding="utf-8")
    assert _JOB_INSERT.search(source) is not None


def test_registered_jobs_stay_clear_of_the_four_bans() -> None:
    """등록 잡 전건이 §15 L3 금지 4영역 밖이다.

    지금 도는 것은 ⓐ 달력 파생 상태 수렴(기판정 승인 — ADR-0038·0039)과
    ⓑ 내부 알림 생성(디스패치·기일 스캔·브리핑·정체 스캔 — 읽기+alerts INSERT)뿐이다.
    지출 확정·법적 판정·대외 발송·장부 확정을 하는 잡이 들어오면 이 목록이
    먼저 바뀌므로, 그때 판정을 거치게 된다.
    """
    assert (
        {spec.code for spec in scheduler.JOB_REGISTRY}
        == {
            "certification-sweep",
            "outbox-dispatch",
            "deadline-scan",
            "daily-briefing",
            "stagnation-scan",  # S2-4 PR-1 — 정체 N일·다음 액션 독촉(읽기+alerts INSERT — 발송 없음)
            "storage-monitor",  # S2-4 PR-3 — 용량·고아/유실 점검(+설정 시 물리 정리 — 기본 OFF, 알림만)
            "backup-freshness",  # S2-4 PR-3 — 백업 신선도 감시(백업 볼륨 읽기 전용+alerts INSERT — 발송 없음)
            # S3-1 PR-5a — 전표 합계 검산(읽기 전용+ADMIN 인앱 알림 — 자동 보정·상태 변경·대외 발송·장부 확정 없음)
            "trade-docs-totals-verify",
            # S3-1 PR-6a — 견적·PI 만료 스윕(전이 두 엣지 QT/PI ISSUED→EXPIRED뿐 — 발주·SO 무접촉·대외 발송 없음·후속 보유 제외, ADR-0056)
            "document-expiry-sweep",
            # S3-1 PR-9a — 결재 대기 정체 독촉(읽기+alerts INSERT뿐 — 승인 상태 불변·자동 결정 없음·대외 발송 없음, ADR-0061 4금 논증)
            "approval-stagnation-scan",
            # S3-1 PR-16 — 기술 행 TTL 청소(ADR-0013·0014 예약 이행, ADR-0058 ⑤): idempotency_keys·user_sessions의 만료분 DELETE뿐 —
            # 원장·전표·감사·발주 무접촉·판정 없음·대외 발송 없음
            "idempotency-purge",
            "session-purge",
            # S3-2 PR-1b — 승인 무결성 대사(READ ONLY 트랜잭션 대사 + ADMIN 인앱 알림뿐 — 승인 상태 무수정·자동 정정 없음·대외 발송 없음, ADR-0087 ⑤)
            "approval-integrity-check",
        }
    )


def test_the_registry_has_exactly_thirteen_jobs_with_the_s3_2_pr1b_schedules() -> None:
    """총수 대사 — S3-1 종결 12행(X-45·ADR-0058 ①) + S3-2 PR-1b `approval-integrity-check` 1행 = **13행**(R-17 중간값 — PR-6이 14로 올린다).
    청소 잡 2종은 백업(03:00)·복원 리허설(04:00) 뒤·저장소 점검(05:00) 앞. 무결성 대사는 합계 검산(05:30) 뒤·인증 스윕(06:00) 앞."""
    schedules = {spec.code: spec.schedule for spec in scheduler.JOB_REGISTRY}
    assert len(scheduler.JOB_REGISTRY) == 13
    assert schedules["approval-integrity-check"] == "daily@05:40"
    assert schedules["idempotency-purge"] == "daily@04:20"
    assert schedules["session-purge"] == "daily@04:25"
    daily = [s for s in schedules.values() if s.startswith("daily@")]
    assert len(daily) == len(set(daily)), "같은 시각에 겹친 daily 잡이 있다"


def test_purge_jobs_touch_only_their_own_technical_table() -> None:
    """청소 함수가 다루는 모델은 자기 기술 표 하나뿐 — 전표·원장·감사 모델 무접촉(ADR-0058 ⑤ / design-F F18 (e))."""
    from app.core.db import purge
    from app.modules.idempotency import service as idempotency
    from app.modules.identity import service as identity

    idem = inspect.getsource(idempotency.purge_expired_all)
    sess = inspect.getsource(identity.purge_expired_sessions_all)
    assert "IdempotencyKey" in idem and "UserSession" not in idem
    assert "UserSession" in sess and "IdempotencyKey" not in sess
    for body in (idem, sess):
        assert "audit." not in body and "AuditAction" not in body
    helper = inspect.getsource(purge)
    assert "app.modules" not in helper, "청크 헬퍼는 어떤 업무 모듈도 임포트하지 않는다"
