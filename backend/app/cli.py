"""운영 명령줄 도구.

    docker compose run --rm api python -m app.cli create-admin --email you@example.com

★ 이게 없으면 시스템에 들어갈 방법이 없다.
  로그인은 계정을 요구하고 계정 생성은 관리자를 요구하므로, 첫 관리자는 앱
  바깥에서 만들어야 한다. 마이그레이션에 기본 관리자를 시드하는 방법은 쓰지
  않는다 — 기본 비밀번호가 리포에 박히고, 그 계정은 아무도 안 지운다.

★ 만들어진 계정도 audit_log에 남는다(행위자 NULL = 시스템 부트스트랩).
  "이 관리자는 언제 어디서 생겼나"에 답할 수 없는 계정을 만들지 않는다.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date
from uuid import uuid4

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.certifications.service import sweep_date_transitions
from app.modules.collaboration import stagnation
from app.modules.deadlines import service as deadlines
from app.modules.identity.models import Role, RoleCode, User, UserRole
from app.modules.identity.passwords import hash_password
from app.modules.identity.service import normalize_email
from app.modules.platform import scheduler
from app.modules.seeds import service as seeds

MIN_PASSWORD_LENGTH = 12


def create_admin(email: str, password: str, display_name: str) -> int:
    """관리자 계정을 만든다. 이미 있으면 ADMIN 역할만 보강한다."""
    normalized = normalize_email(email)
    with unit_of_work() as uow:
        session = uow.session
        user = session.execute(
            select(User).where(User.email == normalized, User.deleted_at.is_(None))
        ).scalar_one_or_none()

        if user is None:
            user = User(
                email=normalized,
                password_hash=hash_password(password),
                display_name=display_name,
            )
            session.add(user)
            session.flush()
            created = True
        else:
            created = False

        role_id = session.execute(
            select(Role.id).where(Role.code == RoleCode.ADMIN.value, Role.deleted_at.is_(None))
        ).scalar_one()
        already = session.execute(
            select(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role_id == role_id,
                UserRole.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if already is None:
            session.add(UserRole(user_id=user.id, role_id=role_id))
            audit.record(
                session,
                action=AuditAction.ROLE_GRANTED,
                entity_type="users",
                entity_id=user.id,
                detail={"role": RoleCode.ADMIN.value, "via": "cli.create-admin"},
            )
        if created:
            audit.record(
                session,
                action="identity.account.bootstrapped",
                entity_type="users",
                entity_id=user.id,
                detail={"via": "cli.create-admin"},
            )
        return user.id


def _read_password(supplied: str | None) -> str:
    password = supplied or getpass.getpass("비밀번호: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(
            f"비밀번호가 너무 짧습니다(최소 {MIN_PASSWORD_LENGTH}자). "
            "관리자 계정은 시스템 전체 권한을 가집니다."
        )
    return password


def _seed_t1(markets: list[str], actor_email: str) -> int:
    """등록된 계정을 투입자로 T1 초안을 투입한다 — 계정이 없으면 만들지 않고 실패한다."""
    from app.modules.identity.service import AuthenticatedUser

    normalized = normalize_email(actor_email)
    with unit_of_work() as uow:
        user = uow.session.execute(
            select(User).where(User.email == normalized, User.deleted_at.is_(None))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            print(f"투입자 계정을 찾을 수 없습니다(또는 비활성): {normalized}", file=sys.stderr)
            return 1
        actor = AuthenticatedUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            roles=frozenset(),
            session_id=0,
        )
    try:
        _, body = seeds.apply_t1(
            actor=actor,
            idempotency_key=f"cli-seed-t1-{uuid4().hex}",
            payload={"markets": markets},
        )
    except AppError as exc:
        print(f"투입 실패: {exc.detail}", file=sys.stderr)
        return 1
    for result in body["results"]:
        print(
            f"{result['code']}: 시장 {'신규 등록' if result['market_created'] else '기존'}"
            f" · 초안 {len(result['created'])}건 투입 · {len(result['skipped'])}건 건너뜀"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="kbeauty-trade-os 운영 명령")
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="첫 관리자 계정을 만든다")
    admin.add_argument("--email", required=True)
    admin.add_argument("--display-name", default="관리자")
    admin.add_argument(
        "--password",
        default=None,
        help="생략하면 화면에 입력받는다(셸 히스토리에 비밀번호를 남기지 않으려면 생략할 것)",
    )

    # 인증 만료임박·만료 날짜 스윕 (§5.2 자동 부여 / WBS S2-2 DoD "배치" — 판정 ⑦).
    # S2-3부터는 실행기가 같은 함수를 스케줄로도 돌린다 — 이 명령은 기준일을
    # 지정해 손으로 한 번 돌리는 통로로 남는다(과거 날짜 재수렴·관통 실측).
    sweep = commands.add_parser(
        "certification-sweep",
        help="인증 만료임박·만료 날짜 수렴 스윕을 1회 실행한다 (스케줄 등록은 S2-3)",
    )
    sweep.add_argument(
        "--base-date",
        default=None,
        help="기준일(YYYY-MM-DD). 생략하면 KST 오늘 — 업무 날짜는 KST다(§22 렌즈 6)",
    )

    # 기일 스캔·브리핑 수동 실행 (S2-3 PR-2 — 스윕과 같은 "스케줄 + CLI 겸용" 계보).
    # 기준일을 지정해 과거·미래 날짜로 관통 실측하는 통로다(안건 ② (e)).
    scan = commands.add_parser(
        "deadline-scan",
        help="인증 만료일·문서 유효기간 기일 스캔을 1회 실행한다(알림 생성 — 멱등)",
    )
    scan.add_argument(
        "--base-date",
        default=None,
        help=(
            "기준일(YYYY-MM-DD). 생략하면 KST 오늘. ★ 운영 DB에서 미래 날짜를 주지 말 것 — "
            "dedup 키가 만료일 기준이라 미래 기준일로 만든 알림이 실시간 알림의 자리를 선점한다"
        ),
    )
    stagnation_scan = commands.add_parser(
        "stagnation-scan",
        help="인증 정체 N일·다음 액션 기한 독촉 스캔을 1회 실행한다(알림 생성 — 멱등)",
    )
    stagnation_scan.add_argument(
        "--base-date",
        default=None,
        help="기준일(YYYY-MM-DD). 생략하면 KST 오늘. ★ 운영 DB에서 미래 날짜를 주지 말 것",
    )
    briefing = commands.add_parser(
        "daily-briefing", help="담당 건 보유 사용자에게 데일리 브리핑을 1회 보낸다(하루 1통 dedup)"
    )
    briefing.add_argument("--base-date", default=None, help="기준일(YYYY-MM-DD). 생략하면 KST 오늘")

    # 배치 레지스트리 등록 (§15 / 부채 #12 — 판정 요청 15의 앱 경로 등록).
    # 마이그레이션 시드는 항구 금지다(함정 ⑩ — scheduled_jobs가 users FK를 단다).
    commands.add_parser(
        "register-jobs",
        help="레지스트리의 배치를 scheduled_jobs에 등록한다(멱등 — 있는 건 건드리지 않음)",
    )

    # T1 요건 템플릿 초안 투입 (S2-4 PR-2 — 앱 경로 시드, 마이그레이션 시드 아님).
    seed = commands.add_parser(
        "seed-t1",
        help="선택한 시장의 T1 요건 템플릿 초안을 투입한다(멱등 — 있는 템플릿은 건너뜀, 확정은 사람이)",
    )
    seed.add_argument(
        "--market",
        action="append",
        required=True,
        help="투입할 시장 코드(예: US). 여러 시장은 --market을 반복한다",
    )
    seed.add_argument(
        "--actor-email", required=True, help="투입자(등록된 계정) 이메일 — 감사 컬럼에 남는다"
    )

    # 실행기 진입점 — compose의 worker 서비스가 이 명령으로 뜬다.
    commands.add_parser("run-scheduler", help="배치 실행기를 기동한다(무한 루프)")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        user_id = create_admin(args.email, _read_password(args.password), args.display_name)
        print(f"관리자 계정 준비 완료: {normalize_email(args.email)} (id={user_id})")
        return 0
    if args.command == "certification-sweep":
        base = date.fromisoformat(args.base_date) if args.base_date else None
        counts = sweep_date_transitions(base_date=base)
        print(f"스윕 완료: 대상 {counts['scanned']}건 중 수렴 {counts['converged']}건")
        for key in sorted(counts):
            if "->" in key:
                print(f"  {key}: {counts[key]}건")
        return 0
    if args.command == "deadline-scan":
        base = date.fromisoformat(args.base_date) if args.base_date else None
        counts = deadlines.scan_deadlines(base_date=base)
        print(
            f"스캔 완료: 인증 {counts['certifications']}건·문서 {counts['documents']}건 — "
            f"신규 알림 문턱 {counts['threshold']}·도과 {counts['overdue']}·"
            f"에스컬레이션 {counts['escalated']}·실패 {counts['failed']}건"
        )
        return 1 if counts["failed"] else 0
    if args.command == "stagnation-scan":
        base = date.fromisoformat(args.base_date) if args.base_date else None
        counts = stagnation.scan_stagnation(base_date=base)
        print(
            f"정체 스캔 완료: 인증 {counts['certifications']}건·통신 기록 {counts['follow_ups']}건 — "
            f"신규 알림 정체 {counts['stagnant']}·기한 당일 {counts['follow_up_due']}·"
            f"기한 도과 {counts['follow_up_overdue']}·실패 {counts['failed']}건"
        )
        return 1 if counts["failed"] else 0
    if args.command == "daily-briefing":
        base = date.fromisoformat(args.base_date) if args.base_date else None
        counts = deadlines.send_daily_briefing(base_date=base)
        print(
            f"브리핑 완료: 수신자 {counts['recipients']}명 중 신규 발송 {counts['sent']}통"
            f"·실패 {counts['failed']}건"
        )
        return 1 if counts["failed"] else 0
    if args.command == "register-jobs":
        created = scheduler.register_jobs()
        if created:
            print(f"등록 완료 {len(created)}건: {', '.join(created)}")
        else:
            print("새로 등록할 배치가 없습니다(전건 등록 상태).")
        return 0
    if args.command == "seed-t1":
        return _seed_t1(args.market, args.actor_email)
    if args.command == "run-scheduler":
        return scheduler.run_forever()
    return 1  # pragma: no cover — argparse가 먼저 막는다


if __name__ == "__main__":
    sys.exit(main())
