"""K. 게이트 증적 `gate_evaluations`·`gate_overrides`의 불변식을 DB가 강제한다 — IMMUTABLE(kbos_app 42501 실측)·CHECK·유니크·FK·제약명 63자·번역표 완결성 (S3-1 M09 / ADR-0069 / design-D D3).

서비스를 거치지 않고 raw INSERT로 위반시킨다(alembic check는 CHECK·GRANT를 못 본다 — 함정 ①). 모든 위반 케이스는 **양성 대조 INSERT가 성공**함을 함께 확인한다. 앱 계정(`kbos_app`)이
UPDATE·DELETE·TRUNCATE를 못 한다는 것이 "증적은 고칠 수 없고 정정은 새 행(REVOKE·새 시도)"의 DB 층이다. override 불가 게이트(품번·중복 PO·여신)의 INSERT는 CHECK 위반이다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db.session import engine, owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES
from app.modules.gates import models as gate_models
from app.modules.gates.policy import OVERRIDABLE_GATES
from app.modules.gates.service import CONSTRAINT_ERRORS
from app.modules.identity.models import RoleCode
from tests.factories.trade import unique
from tests.support.factories import create_user

pytestmark = pytest.mark.group_k

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FK_VIOLATION = "23503"
PERMISSION_DENIED = "42501"

HASH = "a" * 64


def _state(exc: Exception) -> tuple[str, str | None]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", ""), getattr(
        getattr(orig, "diag", None), "constraint_name", None
    )


@pytest.fixture
def user_id() -> int:
    return create_user(f"{unique('gate-k')}@example.com", roles=(RoleCode.ADMIN,))


def _override(user: int, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_type": "SALES_ORDER",
        "subject_id": 1,
        "line_id": 7,
        "gate_code": "PRICE_DEVIATION",
        "action": "GRANT",
        "result_at_grant": "BLOCK",
        "reason": "고객 요청으로 예외 처리",
        "basis": '{"unit_price_amount": 1051}',
        "basis_hash": HASH,
        "granted_by_id": user,
        "authorized_role": "TRADE",
        **overrides,
    }


def _evaluation(user: int, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_type": "SALES_ORDER",
        "subject_id": 1,
        "outcome": "BLOCKED",
        "results": '{"gates": []}',
        "input_digest": HASH,
        "evaluated_by_id": user,
        **overrides,
    }


def _insert(connection: Connection, table: str, values: dict[str, Any]) -> int:
    names = list(values)
    json_columns = {"basis", "results"}
    placeholders = ", ".join(
        f"CAST(:{n} AS jsonb)" if n in json_columns else f":{n}" for n in names
    )
    return int(
        connection.execute(
            text(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders}) RETURNING id"),
            values,
        ).scalar_one()
    )


def _violates(table: str, values: dict[str, Any], code: str, constraint: str) -> None:
    with pytest.raises(IntegrityError) as caught, owner_engine.begin() as connection:
        _insert(connection, table, values)
    assert _state(caught.value) == (code, constraint)


def _app_denied(sql: str, **params: Any) -> None:
    with pytest.raises(ProgrammingError) as caught, engine.begin() as connection:
        connection.execute(text(sql), params)
    assert _state(caught.value)[0] == PERMISSION_DENIED


# ── IMMUTABLE ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("table", "values_of", "update"),
    [
        ("gate_overrides", _override, "reason = 'x'"),
        ("gate_evaluations", _evaluation, "outcome = 'CONFIRMED'"),
    ],
)
def test_the_gate_tables_are_immutable_for_the_app_account(
    user_id: int, table: str, values_of: Any, update: str
) -> None:
    """kbos_app의 UPDATE·DELETE·TRUNCATE는 SQLSTATE 42501(실측)이고 INSERT·SELECT는 허용 — 분류표(IMMUTABLE_TABLES)에도 등재. 열 단위 UPDATE 권한도 0"""
    assert table in IMMUTABLE_TABLES
    with engine.begin() as connection:  # 양성 대조 — 앱 계정이 INSERT·SELECT는 한다
        row_id = _insert(connection, table, values_of(user_id))
        assert (
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE id = :i"), {"i": row_id}
            ).scalar_one()
            == 1
        )
    _app_denied(f"UPDATE {table} SET {update} WHERE id = :i", i=row_id)
    _app_denied(f"DELETE FROM {table} WHERE id = :i", i=row_id)
    _app_denied(f"TRUNCATE {table}")
    with engine.connect() as connection:
        privileges = {
            p: connection.execute(
                text("SELECT has_table_privilege('kbos_app', :t, :p)"), {"t": table, "p": p}
            ).scalar_one()
            for p in ("INSERT", "SELECT", "UPDATE", "DELETE", "TRUNCATE")
        }
        column_update = connection.execute(
            text(
                "SELECT count(*) FROM pg_attribute a WHERE a.attrelid = CAST(:t AS regclass)"
                " AND a.attnum > 0 AND NOT a.attisdropped"
                " AND has_column_privilege('kbos_app', a.attrelid, a.attnum, 'UPDATE')"
            ),
            {"t": f"public.{table}"},
        ).scalar_one()
    assert privileges == {
        "INSERT": True,
        "SELECT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
    }
    assert column_update == 0


# ── gate_overrides CHECK ────────────────────────────────────────────────────────


@pytest.mark.parametrize("gate", sorted(OVERRIDABLE_GATES))
def test_an_overridable_gate_code_inserts(user_id: int, gate: str) -> None:
    """[양성] override 가능한 4종(가격·MOQ·준비도·PI)은 INSERT된다 — 아래 우회 불가 3종의 위반 테스트가 공회전이 아님을 보장"""
    with owner_engine.begin() as connection:
        assert _insert(connection, "gate_overrides", _override(user_id, gate_code=gate)) > 0


@pytest.mark.parametrize("gate", ["ITEM_MAPPING", "DUPLICATE_PO", "CREDIT", "BOGUS"])
def test_the_database_refuses_an_override_for_a_non_overridable_gate(
    user_id: int, gate: str
) -> None:
    """품번 매핑·중복 PO·여신(그리고 모르는 코드)의 override 행은 서비스를 우회해 raw INSERT해도 CHECK 위반 — 우회 불가를 DB가 강제한다(ADMIN 포함)"""
    _violates(
        "gate_overrides",
        _override(user_id, gate_code=gate),
        CHECK_VIOLATION,
        "ck_gate_overrides_gate_code_overridable",
    )


@pytest.mark.parametrize(
    ("column", "bad", "constraint"),
    [
        ("action", "DELETE", "ck_gate_overrides_action_valid"),
        ("result_at_grant", "PASS", "ck_gate_overrides_result_at_grant_valid"),
        ("result_at_grant", "WARN", "ck_gate_overrides_result_at_grant_valid"),
        ("subject_type", "CHANNEL_LISTING", "ck_gate_overrides_subject_type_valid"),
        ("authorized_role", "SUPERUSER", "ck_gate_overrides_authorized_role_valid"),
        ("basis_hash", "a" * 63, "ck_gate_overrides_basis_hash_format"),
        ("basis_hash", "A" * 64, "ck_gate_overrides_basis_hash_format"),
        ("basis_hash", "g" * 64, "ck_gate_overrides_basis_hash_format"),
        ("line_id", 0, "ck_gate_overrides_line_id_positive"),
        ("basis", "[1]", "ck_gate_overrides_basis_is_object"),
    ],
    ids=[
        "action",
        "result-pass",
        "result-warn",
        "subject-type",
        "role",
        "hash-63",
        "hash-upper",
        "hash-nonhex",
        "line-0",
        "basis-array",
    ],
)
def test_override_row_checks(user_id: int, column: str, bad: Any, constraint: str) -> None:
    """override 행의 열거·해시 형식·역할·라인·근거 형 CHECK — 각각 raw INSERT로 위반시킨다(양성 대조는 위 INSERT 테스트와 같은 기본 행)"""
    _violates("gate_overrides", _override(user_id, **{column: bad}), CHECK_VIOLATION, constraint)


@pytest.mark.parametrize(
    "reason",
    ["12345", "가" * 5, "가" * 500, "  가나다라마  "],
    ids=["ascii-5", "hangul-5", "hangul-500", "padded-5"],
)
def test_a_reason_of_five_to_five_hundred_characters_inserts(user_id: int, reason: str) -> None:
    """[양성] 사유 5자·500자(앞뒤 공백을 뺀 길이 기준)는 INSERT된다 — 경계"""
    with owner_engine.begin() as connection:
        assert _insert(connection, "gate_overrides", _override(user_id, reason=reason)) > 0


@pytest.mark.parametrize(
    ("reason", "constraint"),
    [
        ("1234", "ck_gate_overrides_reason_length"),
        ("    abcd    ", "ck_gate_overrides_reason_length"),
        ("", "ck_gate_overrides_reason_length"),
        ("가" * 501, "ck_gate_overrides_reason_length"),
        ("줄\n바꿈 사유", "ck_gate_overrides_reason_clean"),
        ("탭\t사유입니다", "ck_gate_overrides_reason_clean"),
        ("널\x01문자사유", "ck_gate_overrides_reason_clean"),
    ],
    ids=["4", "padded-4", "empty", "501", "newline", "tab", "soh"],
)
def test_a_reason_outside_the_bounds_or_with_control_characters_is_refused(
    user_id: int, reason: str, constraint: str
) -> None:
    """사유 4자·공백 패딩 4자·빈 값·501자는 길이 CHECK, 제어문자(개행·탭·SOH)는 clean CHECK 위반 — 5~500자 규칙이 DB에서도 성립한다"""
    _violates("gate_overrides", _override(user_id, reason=reason), CHECK_VIOLATION, constraint)


def test_a_missing_actor_user_is_refused_by_the_foreign_key(user_id: int) -> None:
    """존재하지 않는 사용자를 부여자·평가자로 적을 수 없다(FK RESTRICT)"""
    _violates(
        "gate_overrides",
        _override(user_id, granted_by_id=9_999_999),
        FK_VIOLATION,
        "fk_gate_overrides_granted_by_id_users",
    )
    _violates(
        "gate_evaluations",
        _evaluation(user_id, evaluated_by_id=9_999_999),
        FK_VIOLATION,
        "fk_gate_evaluations_evaluated_by_id_users",
    )


# ── gate_evaluations CHECK·유니크 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("column", "bad", "constraint"),
    [
        ("outcome", "PASSED", "ck_gate_evaluations_outcome_valid"),
        ("subject_type", "CHANNEL_LISTING", "ck_gate_evaluations_subject_type_valid"),
        ("input_digest", "z" * 64, "ck_gate_evaluations_input_digest_format"),
        ("input_digest", "a" * 63, "ck_gate_evaluations_input_digest_format"),
        ("results", "[]", "ck_gate_evaluations_results_is_object"),
    ],
    ids=["outcome", "subject-type", "digest-nonhex", "digest-63", "results-array"],
)
def test_evaluation_row_checks(user_id: int, column: str, bad: Any, constraint: str) -> None:
    """증거 스냅샷의 결과 열거·digest 형식·results 형 CHECK"""
    with owner_engine.begin() as connection:  # 양성 대조
        assert _insert(connection, "gate_evaluations", _evaluation(user_id)) > 0
    _violates(
        "gate_evaluations", _evaluation(user_id, **{column: bad}), CHECK_VIOLATION, constraint
    )


def test_a_sales_order_has_at_most_one_confirmed_evidence_row_but_many_blocked_attempts(
    user_id: int,
) -> None:
    """SO당 CONFIRMED 증거는 정확히 1행(부분 유니크) — BLOCKED 시도는 여러 번 남을 수 있고 다른 SO의 CONFIRMED와는 무관"""
    with owner_engine.begin() as connection:
        for _ in range(3):
            _insert(connection, "gate_evaluations", _evaluation(user_id, outcome="BLOCKED"))
        _insert(connection, "gate_evaluations", _evaluation(user_id, outcome="CONFIRMED"))
        _insert(
            connection, "gate_evaluations", _evaluation(user_id, outcome="CONFIRMED", subject_id=2)
        )
    _violates(
        "gate_evaluations",
        _evaluation(user_id, outcome="CONFIRMED"),
        UNIQUE_VIOLATION,
        "uq_gate_evaluations_confirmed_once",
    )


# ── 정의·명명 ──────────────────────────────────────────────────────────────────


def _constraint_defs(table: str) -> dict[str, str]:
    with owner_engine.connect() as connection:
        return {
            r[0]: r[1]
            for r in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = CAST(:t AS regclass)"
                ),
                {"t": f"public.{table}"},
            )
        }


def test_the_overridable_gate_check_lists_exactly_the_code_table_and_the_role_check_lists_the_five_roles() -> (
    None
):
    """DB의 override 가능 게이트 CHECK 정의가 코드의 `OVERRIDE_ROLES` 키 집합(4종)과 같고, 역할 CHECK가 `RoleCode` 5종과 같다 — 코드와 DB가 따로 늘어나지 않는다"""
    defs = _constraint_defs("gate_overrides")
    gate_def = defs["ck_gate_overrides_gate_code_overridable"]
    listed = {part.split("'")[1] for part in gate_def.split("ARRAY[")[1].split("]")[0].split(",")}
    assert listed == set(OVERRIDABLE_GATES) == set(gate_models.OVERRIDABLE_GATE_CODES)
    role_def = defs["ck_gate_overrides_authorized_role_valid"]
    roles = {part.split("'")[1] for part in role_def.split("ARRAY[")[1].split("]")[0].split(",")}
    assert roles == {r.value for r in RoleCode} == set(gate_models.ROLE_CODES)


def test_constraint_names_fit_in_63_characters_and_the_translation_table_is_complete() -> None:
    """제약·인덱스 이름이 63자 이내이고, 두 테이블의 모든 DB 제약·유니크 인덱스가 번역표(`CONSTRAINT_ERRORS`)에 있다(역도 성립) — 신규 제약이 번역 없이 500으로 새지 않는다"""
    names: set[str] = set()
    for table in ("gate_evaluations", "gate_overrides"):
        names |= set(_constraint_defs(table))
    with owner_engine.connect() as connection:
        names |= {
            r[0]
            for r in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename IN"
                    " ('gate_evaluations', 'gate_overrides') AND indexname LIKE 'uq\\_%'"
                )
            )
        }
    assert all(len(n) <= 63 for n in names)
    assert names == set(CONSTRAINT_ERRORS), names ^ set(CONSTRAINT_ERRORS)
