"""K. 보안·품질 — 시장 마스터의 불변식을 DB가 강제한다 (§5.1 / S2-1 판정 조건 3).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — 모델이 선언한 CHECK·유니크가
  DB에 실재하는지를 실측한다(test_partner_constraints와 같은 방식).

★ 이 파일의 핵심은 **전역 UNIQUE의 실측**이다. markets.code는 unique_active
  (부분 인덱스)가 아니라 전역 UNIQUE 제약이어야 한다 — country_code FK의
  참조 대상이라 부분 유니크로는 FK가 성립하지 않는다(판정 §0-1 ②). soft
  delete된 행이 코드를 계속 점유하는 거동까지가 그 계약이다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.markets.models import Market

pytestmark = pytest.mark.group_k


def _insert_market(**overrides: Any) -> None:
    columns: dict[str, Any] = {"code": "US", "name_ko": "미국"}
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(Market(**columns))
        uow.session.flush()


# ── 코드 형식 CHECK ─────────────────────────────────────────────────────────


def test_lowercase_code_is_rejected_by_the_database() -> None:
    """소문자 코드는 DB가 거부한다 — 정규화는 서비스, 강제는 CHECK"""
    with pytest.raises(IntegrityError) as exc:
        _insert_market(code="us")
    assert "code_format" in str(exc.value)


def test_digit_code_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_market(code="U1")
    assert "code_format" in str(exc.value)


def test_check_definition_matches_the_model() -> None:
    """CHECK 정의문이 모델 선언 그대로 DB에 실재한다 (pg_get_constraintdef — 조건 5 양식)"""
    with unit_of_work() as uow:
        definition = uow.session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_markets_code_format'"
            )
        ).scalar_one()
    assert "'^[A-Z]{2}$'" in definition


# ── 전역 UNIQUE (판정 조건 3 — unique_active가 아니다) ──────────────────────


def test_duplicate_code_is_rejected_by_the_database() -> None:
    _insert_market(code="US")
    with pytest.raises(IntegrityError) as exc:
        _insert_market(code="US", name_ko="미국 중복")
    assert "uq_markets_code" in str(exc.value)


def test_soft_deleted_code_still_occupies_the_key() -> None:
    """soft delete된 시장의 코드도 점유가 유지된다 — 재사용은 신규가 아니라 복원

    §17.4의 부분 유니크 규율(삭제 행은 재유입을 막지 않는다)과 **다른 거동**이
    이 테이블의 계약이다: 코드는 FK가 값으로 참조하는 자연키라, 같은 코드의
    신·구 행이 공존하면 참조 대상이 모호해진다(판정 §0-1 ②).
    """
    _insert_market(code="EU", name_ko="유럽연합", deleted_at=utcnow())
    with pytest.raises(IntegrityError) as exc:
        _insert_market(code="EU", name_ko="유럽연합 신규")
    assert "uq_markets_code" in str(exc.value)


def test_unique_is_a_constraint_not_a_partial_index() -> None:
    """uq_markets_code가 부분 인덱스가 아닌 전면 UNIQUE 제약으로 실재한다

    unique_active로 바꾸는 리팩터링이 들어오면 여기서 잡힌다 — 그 순간
    country_code FK의 성립 근거가 사라진다.
    """
    with unit_of_work() as uow:
        contype = uow.session.execute(
            text("SELECT contype FROM pg_constraint WHERE conname = 'uq_markets_code'")
        ).scalar_one_or_none()
        assert contype == "u", "uq_markets_code가 UNIQUE 제약이 아닙니다."
        partial = uow.session.execute(
            text(
                "SELECT count(*) FROM pg_indexes WHERE tablename = 'markets'"
                " AND indexname = 'uq_markets_code' AND indexdef ILIKE '%WHERE%'"
            )
        ).scalar_one()
        assert partial == 0, "uq_markets_code가 부분 인덱스입니다 — FK가 성립하지 않습니다."
