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

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.certifications.service import sweep_date_transitions
from app.modules.identity.models import Role, RoleCode, User, UserRole
from app.modules.identity.passwords import hash_password
from app.modules.identity.service import normalize_email
from app.modules.platform import scheduler

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

    # 배치 레지스트리 등록 (§15 / 부채 #12 — 판정 요청 15의 앱 경로 등록).
    # 마이그레이션 시드는 항구 금지다(함정 ⑩ — scheduled_jobs가 users FK를 단다).
    commands.add_parser(
        "register-jobs",
        help="레지스트리의 배치를 scheduled_jobs에 등록한다(멱등 — 있는 건 건드리지 않음)",
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
    if args.command == "register-jobs":
        created = scheduler.register_jobs()
        if created:
            print(f"등록 완료 {len(created)}건: {', '.join(created)}")
        else:
            print("새로 등록할 배치가 없습니다(전건 등록 상태).")
        return 0
    if args.command == "run-scheduler":
        return scheduler.run_forever()
    return 1  # pragma: no cover — argparse가 먼저 막는다


if __name__ == "__main__":
    sys.exit(main())
