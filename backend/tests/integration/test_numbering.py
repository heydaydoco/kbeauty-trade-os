"""J. 안전 계약 — 채번 (DESIGN.md §17.3 / S0-2 DoD "동시 100건 중복 0").

★ 순차 실행은 이 케이스의 증거가 아니다. 채번의 위험은 "동시에 읽고 동시에 쓰는"
  순간에만 나타나므로, 실제 스레드를 동시에 출발시켜야 시험이 성립한다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.modules.numbering.service import next_document_number
from tests.support.concurrency import run_concurrently

pytestmark = pytest.mark.group_j

CONCURRENT_REQUESTS = 100


def _issue(prefix: str = "SO") -> str:
    with unit_of_work() as uow:
        return next_document_number(uow.session, prefix)


def test_number_format_is_prefix_year_sequence() -> None:
    """발급 형식은 SO-2026-0001이다 (§3)"""
    number = _issue()
    prefix, year, sequence = number.split("-")
    assert prefix == "SO"
    assert year.isdigit() and len(year) == 4
    assert sequence == "0001"


def test_numbers_increase_by_one() -> None:
    """연속 발급은 1씩 증가한다"""
    assert [_issue().rsplit("-", 1)[1] for _ in range(3)] == ["0001", "0002", "0003"]


def test_prefixes_have_independent_counters() -> None:
    """접두어가 다르면 카운터도 다르다 (SO와 PO가 서로를 밀지 않는다)"""
    assert _issue("SO").endswith("0001")
    assert _issue("PO").endswith("0001")
    assert _issue("SO").endswith("0002")


def test_rollback_returns_the_number() -> None:
    """업무가 롤백되면 번호도 함께 돌아간다 (결번이 생기지 않는다)

    채번만 별도 트랜잭션으로 먼저 따 두면 롤백된 업무의 번호가 소비된 채 남는다.
    """
    assert _issue().endswith("0001")

    with pytest.raises(RuntimeError), unit_of_work() as uow:
        next_document_number(uow.session, "SO")
        raise RuntimeError("업무 실패")

    assert _issue().endswith("0002")


@pytest.mark.concurrency
@pytest.mark.slow
def test_one_hundred_concurrent_requests_produce_no_duplicate() -> None:
    """동시 100건 발급에 중복이 0이다 (S0-2 DoD)"""
    outcomes = run_concurrently(lambda _: _issue(), workers=CONCURRENT_REQUESTS)

    failures = [outcome.error for outcome in outcomes if not outcome.ok]
    assert not failures, f"발급 실패 {len(failures)}건: {failures[:3]}"

    numbers = [outcome.value for outcome in outcomes]
    assert len(set(numbers)) == CONCURRENT_REQUESTS, (
        f"중복 발급: {len(numbers) - len(set(numbers))}건"
    )

    # 결번도 없어야 한다 — 1..100이 정확히 한 번씩.
    sequences = sorted(int(number.rsplit("-", 1)[1]) for number in numbers)
    assert sequences == list(range(1, CONCURRENT_REQUESTS + 1))

    with engine.connect() as connection:
        last = connection.execute(
            text("SELECT last_number FROM doc_number_seq WHERE prefix = 'SO'")
        ).scalar_one()
    assert last == CONCURRENT_REQUESTS
