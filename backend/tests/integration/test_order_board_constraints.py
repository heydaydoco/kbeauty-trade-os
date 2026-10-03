"""K. 저장 필터 표(M12 `board_saved_filters`)의 불변식을 DB가 강제한다 (S3-1 PR-15a / ADR-0066).

★ 서비스를 거치지 않고 raw INSERT로 위반시킨다(alembic check는 CHECK를 못 본다 — 함정 ①). 위반마다 같은 조건의 **양성 대조 INSERT가 성공**함을 함께 확인한다.
핵심: 이름 비공백·제어문자 불가·조건은 JSON 객체·활성 이름 유일(soft delete가 이름을 해방)·사용자 FK RESTRICT.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES
from app.modules.handover.targets import USER_FK_CLASSIFICATION
from app.modules.order_board.models import SAVED_FILTER_NAME_UNIQUE
from tests.factories.trade import unique
from tests.support.factories import create_user

pytestmark = pytest.mark.group_k

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


def _insert(user_id: int, name: str, config: str = "{}", *, deleted: bool = False) -> int:
    with owner_engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO board_saved_filters (user_id, name, filter_config, deleted_at)"
                    " VALUES (:u, :n, CAST(:c AS jsonb), CASE WHEN :d THEN now() END) RETURNING id"
                ),
                {"u": user_id, "n": name, "c": config, "d": deleted},
            ).scalar_one()
        )


@pytest.fixture
def user() -> int:
    return create_user(f"{unique('bsf')}@example.com")


@pytest.mark.parametrize(
    ("name", "config", "constraint"),
    [
        ("   ", "{}", "ck_board_saved_filters_name_nonblank"),
        ("탭\t이름", "{}", "ck_board_saved_filters_name_clean"),
        ("줄\n바꿈", "{}", "ck_board_saved_filters_name_clean"),
        ("배열 조건", "[]", "ck_board_saved_filters_filter_config_is_object"),
        ("문자 조건", '"q"', "ck_board_saved_filters_filter_config_is_object"),
    ],
)
def test_check_constraints_refuse_bad_rows(
    user: int, name: str, config: str, constraint: str
) -> None:
    """CHECK — 공백뿐인 이름·제어 문자 이름·객체가 아닌 조건은 DB가 거부한다(양성 대조는 통과)"""
    with pytest.raises(IntegrityError) as caught:
        _insert(user, name, config)
    assert _state(caught.value) == (CHECK_VIOLATION, constraint)
    assert _insert(user, f"정상 {unique('n')}", '{"q": "A"}') > 0


def test_active_names_are_unique_per_user_and_soft_delete_frees_the_name(user: int) -> None:
    """활성 이름 유일(부분 유니크) — 같은 사용자 같은 이름은 23505, 지운 행은 이름을 점유하지 않고, 다른 사용자는 같은 이름을 쓴다"""
    _insert(user, "주간")
    with pytest.raises(IntegrityError) as caught:
        _insert(user, "주간")
    assert _state(caught.value) == (UNIQUE_VIOLATION, SAVED_FILTER_NAME_UNIQUE)
    _insert(user, "삭제됨", deleted=True)
    assert _insert(user, "삭제됨") > 0
    other = create_user(f"{unique('bsf')}@example.com")
    assert _insert(other, "주간") > 0


def test_the_owner_fk_is_enforced(user: int) -> None:
    """사용자 FK — 없는 사용자 id는 23503"""
    with pytest.raises(IntegrityError) as caught:
        _insert(987_654_321, "고아 필터")
    assert _state(caught.value)[0] == FK_VIOLATION


def test_definitions_are_registered_in_the_kernel_tables() -> None:
    """등재 — MUTABLE(앱 계정 UPDATE 정상)·users FK 분류 IDENTITY_LINK(이관 대상 아님)·제약 정의문이 DB에 있다(이름 63자 이내)"""
    assert "board_saved_filters" in MUTABLE_TABLES and "board_saved_filters" not in IMMUTABLE_TABLES
    assert USER_FK_CLASSIFICATION[("board_saved_filters", "user_id")] == "IDENTITY_LINK"
    with owner_engine.connect() as connection:
        found: dict[str, Any] = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'board_saved_filters'::regclass AND contype = 'c'"
                )
            )
        }
        index = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
            {"n": SAVED_FILTER_NAME_UNIQUE},
        ).scalar_one()
    assert set(found) == {
        "ck_board_saved_filters_name_nonblank",
        "ck_board_saved_filters_name_clean",
        "ck_board_saved_filters_filter_config_is_object",
    }
    assert "jsonb_typeof(filter_config)" in found["ck_board_saved_filters_filter_config_is_object"]
    assert "UNIQUE" in index and "(user_id, name)" in index and "deleted_at IS NULL" in index
    assert all(len(name) <= 63 for name in [*found, SAVED_FILTER_NAME_UNIQUE])
