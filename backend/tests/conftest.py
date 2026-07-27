"""테스트 공통 설정.

■ 격리 방식: 실제 커밋 + 테스트 후 TRUNCATE

  트랜잭션 롤백(또는 SAVEPOINT) 픽스처를 쓰지 않는다. 빠르지만 이 프로젝트와
  원리적으로 맞지 않는다.

    - §17.1이 서비스 레이어가 **스스로 커밋**하도록 규정한다. 바깥에서
      트랜잭션을 열어 두면 서비스의 commit이 SAVEPOINT 해킹 없이는 동작하지 않고,
      그 해킹은 "테스트에서만 통하는 코드 경로"를 만든다.
    - GC-F1은 "실제 동시 실행"을 요구한다(순차 실행은 통과 증거로 인정 안 됨).
      스레드 두 개가 서로의 커밋을 봐야 하는데 하나의 열린 트랜잭션 안에서는
      원리적으로 불가능하다.
    - 아웃박스(§17.1)는 "커밋된 뒤에 발송"이 핵심이라 커밋이 없으면 검증할 게 없다.

  나중에 "테스트가 느리다"는 이유로 롤백 픽스처로 갈아타면 위 세 가지가
  Phase 4에서 한꺼번에 무너진다. 바꾸려면 ADR을 남길 것.
"""

from __future__ import annotations

import os

# ★ app 모듈을 임포트하기 **전에** 실행 환경을 test로 고정한다.
#   이 한 줄 덕분에 app.core.db.session이 접속 대상을 kbos_test로 고른다.
os.environ["APP_ENV"] = "test"

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from app.core.config import settings
from app.core.db.session import owner_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

#: DESIGN.md §20의 테스트 그룹.
GROUP_MARKERS = frozenset(f"group_{letter}" for letter in "abcdefghijk")

#: TRUNCATE 대상에서 제외하는 테이블.
#:
#: ★ 마이그레이션이 채워 넣는 시드(참조 데이터)가 여기 들어간다. 매 테스트 후
#:   비워 버리면 스키마를 만든 마이그레이션은 세션당 한 번만 도는 탓에 두 번째
#:   테스트부터 시드가 사라진다 — "역할이 없어서" 실패하는 인가 테스트가 줄줄이
#:   나오는데 원인은 인가 코드가 아니라 이 목록이다.
#:
#: ★ 대신 여기 넣은 테이블을 **테스트가 고치면 그 변경이 다음 테스트로 샌다**.
#:   시드 테이블은 마이그레이션으로만 바뀌는 것들이어야 한다(roles가 그렇다).
PRESERVED_TABLES = frozenset(
    {
        "alembic_version",  # alembic이 소유·관리
        "roles",  # 역할 5종 시드 — 마이그레이션 소유, 앱·테스트는 읽기만 (S0-2)
    }
)


def pytest_configure(config: pytest.Config) -> None:
    """오폭 가드 — 테스트가 개발 DB를 지우는 사고를 막는다."""
    if settings.app_env != "test":
        raise pytest.UsageError(
            f"APP_ENV가 {settings.app_env!r}입니다. 테스트는 APP_ENV=test에서만 돌아야 합니다."
        )
    if settings.test_database_url is None:
        raise pytest.UsageError(
            "TEST_DATABASE_URL이 없습니다. docker-compose.yml의 api 서비스가 주입합니다 — "
            "컨테이너 밖에서 돌리는 중이라면 `docker compose run --rm api pytest`를 쓰세요."
        )
    database_name = owner_engine.url.database or ""
    if "test" not in database_name:
        raise pytest.UsageError(
            f"테스트 대상 DB 이름이 {database_name!r}입니다. 테스트는 매 케이스 후 "
            "TRUNCATE로 정리하므로, 이름에 'test'가 없는 DB에는 절대 붙지 않습니다."
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """그룹 마커(A~K)가 없는 테스트는 수집 단계에서 실패시킨다.

    §22 렌즈 8이 "해당 그룹(A~K)에 케이스 추가"를 요구한다. 사람이 기억해서
    붙이는 규칙은 반드시 새므로 기계가 강제한다.
    """
    unmarked = [
        item.nodeid
        for item in items
        if not (GROUP_MARKERS & {mark.name for mark in item.iter_markers()})
    ]
    if unmarked:
        listed = "\n  - ".join(unmarked)
        raise pytest.UsageError(
            "그룹 마커(group_a ~ group_k)가 없는 테스트가 있습니다.\n"
            f"  - {listed}\n"
            "DESIGN.md §20에서 이 케이스가 속할 그룹을 고르고 "
            "@pytest.mark.group_x 를 붙이세요."
        )


def pytest_itemcollected(item: pytest.Item) -> None:
    """테스트 이름 옆에 한국어 설명(독스트링 첫 줄)을 붙인다 — 실행 로그가 곧 보고서."""
    doc = getattr(item, "obj", None) and item.obj.__doc__
    if doc:
        first_line = doc.strip().splitlines()[0].strip()
        if first_line:
            item._nodeid = f"{item._nodeid} :: {first_line}"


@pytest.fixture(scope="session", autouse=True)
def _prepare_schema() -> None:
    """테스트 스키마는 마이그레이션으로만, 매 세션 **바닥부터** 만든다.

    Base.metadata.create_all()을 쓰면 "테스트가 통과하는 스키마"와
    "마이그레이션이 만드는 스키마"가 갈라져서 §18.3의 드라이런이 형식만 남는다.

    ★ upgrade만 하지 않고 downgrade base를 먼저 부르는 이유:
      마이그레이션이 넣는 시드(roles 등)는 리비전이 적용될 때 딱 한 번 들어간다.
      이미 head인 DB에 upgrade head는 아무것도 하지 않으므로, 과거에 어떤 사고로
      시드가 지워졌다면 그 DB는 **영원히 시드 없는 상태로 고정**된다. 그 상태는
      "인가 코드가 잘못된 것처럼 보이는" 실패로 나타나 원인 추적을 크게 낭비시킨다.
      매번 바닥부터 올리면 그 상태가 존재할 수 없고, downgrade 경로도 덤으로
      매 실행 검증된다.
    """
    config = AlembicConfig(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def _truncatable_tables(_prepare_schema: None) -> list[str]:
    """정리 대상 테이블 목록. 스키마는 세션 중 변하지 않으므로 한 번만 읽는다.

    목록을 하드코딩하지 않고 pg_tables에서 읽는다 — 새 테이블이 생겼는데
    목록에 추가하지 않아 데이터가 남는 사고를 없앤다.

    ★ `_prepare_schema`를 인자로 받는 것이 이 픽스처의 핵심이다.
      autouse라는 이유로 스키마 생성이 먼저 돌 것이라 가정하면 안 된다 —
      pytest는 같은 스코프에서 autouse를 먼저 두지만, 이 픽스처가 스키마보다
      앞서 평가되면 목록이 **빈 채로 고정**되고 이후 모든 정리가 조용히
      no-op이 된다. 그러면 테이블이 이미 남아 있는 개발 PC에서는 통과하고
      DB가 새것인 CI에서만 무너진다(실제로 그렇게 터졌다).

    ★ 빈 목록을 그대로 돌려주지 않는다. 빈 목록은 "지울 게 없다"가 아니라
      "스키마를 못 봤다"는 신호다.
    """
    with owner_engine.connect() as connection:
        names = (
            connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            .scalars()
            .all()
        )
    targets = [name for name in names if name not in PRESERVED_TABLES]
    if not targets:
        raise pytest.UsageError(
            "정리 대상 테이블이 하나도 없습니다. 마이그레이션이 적용되기 전에 목록을 "
            "읽었을 가능성이 높습니다 — 이 상태로는 테스트 간 데이터가 그대로 새어 "
            "다음 테스트를 오염시킵니다."
        )
    return targets


@pytest.fixture(autouse=True)
def _clean_tables(_truncatable_tables: list[str]) -> Iterator[None]:
    """각 테스트 후 **데이터가 들어간 테이블만** 비운다(소유자 계정의 별도 커넥션).

    ★ 전 테이블 무조건 TRUNCATE는 테이블 수에 비례해 느려진다. Docker Desktop
      (WSL2) 환경에서 실측 테스트당 ~2.8초였고, 테이블이 12개로 늘어나는 S0-2
      기준으로는 전체 실행이 분 단위로 밀린다 — 느린 테스트는 안 돌리는 테스트가
      되고, 그러면 §18.3 "회귀를 기계가 잡는다"가 무너진다.

      대부분의 테스트는 DB에 아무것도 쓰지 않는다. 비어 있는지 확인하는 EXISTS는
      빈 테이블에서 사실상 공짜라, 실제로 더럽힌 테스트만 값을 치른다.

    CASCADE가 참조 테이블까지 함께 비우므로 부분 목록이어도 고아 행은 남지 않는다.
    """
    yield
    with owner_engine.begin() as connection:
        dirty = [
            name
            for name in _truncatable_tables
            if connection.execute(
                text(f'SELECT EXISTS (SELECT 1 FROM public."{name}")')
            ).scalar_one()
        ]
        if dirty:
            quoted = ", ".join(f'public."{name}"' for name in dirty)
            connection.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
