"""G. AI·보안 — 원가가 로그에 남지 않는다 (DESIGN.md §18.1 / §20 G / ADR-0018).

§18.1: "화면에서 못 보는 값이 로그에 평문이면 통제가 우회된다."
§20 G: "로그에 원가 문자열 0건."

★ 응답 마스킹만으로는 절반이다. 로그에는 별개의 경로가 세 개 있다:
    ① 구조화 로그의 키(purchase_price 등)
    ② 느린 쿼리 로그의 SQL
    ③ **예외 문자열·트레이스백의 바인드 파라미터**  ← 실질적 구멍이었다

  이 파일은 세 경로를 **실제 로그 출력으로** 확인한다. 키 목록만 검사하면
  "그 키로 로그를 남기지 않는다"는 사실에 기대게 되고, 그건 다음 사람이 다른
  키 이름으로 같은 값을 찍는 순간 무너진다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.logging import get_logger
from app.main import app
from app.modules.catalog.models import SkuPrice
from app.modules.identity.models import RoleCode
from tests.support.factories import DEFAULT_PASSWORD, create_sku, create_user

pytestmark = pytest.mark.group_g

SKUS = "/api/v1/skus"

#: 로그 어디에도 나오면 안 되는 매입가. 다른 숫자와 겹치지 않게 특이한 값으로.
COST = "7654321"


@pytest.fixture
def trader() -> Iterator[TestClient]:
    create_user("trade@example.com", roles=(RoleCode.TRADE,))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "trade@example.com", "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 200, response.text
        yield client


def _post_cost(client: TestClient, sku_id: int, *, key: str) -> Any:
    return client.post(
        f"{SKUS}/{sku_id}/prices",
        json={
            "price_type": "PURCHASE",
            "currency": "KRW",
            "amount": COST,
            "effective_from": "2026-01-01",
        },
        headers={"Idempotency-Key": key},
    )


def test_the_cost_never_appears_in_logs_on_the_happy_path(
    trader: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """매입가를 등록해도 그 금액이 로그에 남지 않는다"""
    sku_id = create_sku()

    with caplog.at_level(logging.DEBUG):
        response = _post_cost(trader, sku_id, key="k1")

    assert response.status_code == 201, response.text
    assert response.json()["amount"] == int(COST)  # 응답에는 있다(무역 역할)
    assert COST not in caplog.text


def test_the_cost_never_appears_in_logs_on_the_failure_path(
    trader: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """★ 등록이 실패해도 그 금액이 로그에 남지 않는다

    실패 경로가 위험하다 — 예외가 SQL과 함께 트레이스백으로 남고, 거기에는
    바인드 파라미터가 그대로 실린다(엔진의 hide_parameters=True가 이걸 막는다).
    """
    sku_id = create_sku()
    assert _post_cost(trader, sku_id, key="k1").status_code == 201

    with caplog.at_level(logging.DEBUG):
        duplicate = _post_cost(trader, sku_id, key="k2")

    assert duplicate.status_code == 422
    assert COST not in caplog.text


def test_sql_errors_do_not_carry_bind_parameters(caplog: pytest.LogCaptureFixture) -> None:
    """★ SQL 오류에 바인드 파라미터가 실리지 않는다 (hide_parameters=True)

    임의의 숫자·해시는 마스킹 규칙으로 잡을 수 없다 — 애초에 만들지 않는 것이
    유일한 방법이다. 대신 문장과 PostgreSQL의 DETAIL은 남아야 진단이 된다.
    """
    sku_id = create_sku()

    def _insert() -> None:
        with unit_of_work() as uow:
            uow.session.add(
                SkuPrice(
                    sku_id=sku_id,
                    price_type="PURCHASE",
                    currency="KRW",
                    amount=int(COST),
                    effective_from="2026-01-01",
                )
            )
            uow.session.flush()

    _insert()
    with pytest.raises(IntegrityError) as exc:
        _insert()

    message = str(exc.value)
    assert COST not in message
    # 진단은 여전히 가능해야 한다 — 어떤 제약이 왜 걸렸는지가 남는다.
    assert "uq_sku_prices" in message
    assert "INSERT INTO sku_prices" in message


def test_the_detector_would_notice_a_leak(
    trader: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """검사기가 실제로 유출을 잡는다 (자기검사)

    ★ 이 검사가 없으면, caplog가 아무것도 못 잡는 상태(로그 설정이 바뀌어
      전파가 끊긴 경우 등)에서도 위 테스트들이 영원히 초록이다.
    """
    with caplog.at_level(logging.DEBUG):
        trader.get(f"{SKUS}/999999/prices")

    # 요청 자체는 로그에 남는다 — 즉 캡처가 살아 있다.
    assert "999999" in caplog.text


def test_a_constraint_violation_does_not_leak_the_failing_row_into_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """★ PostgreSQL의 "Failing row contains (…)"가 로그로 나가지 않는다

    hide_parameters는 **우리가 보낸** 바인드 값만 가린다. 제약 위반 시 서버가
    DETAIL에 실어 보내는 "Failing row contains (…)"는 **행 전체 값**이고, 그건
    서버가 만든 문자열이라 엔진 설정으로 막을 수 없다. 실측으로 확인한 경로다:

        DETAIL:  Failing row contains (1, 1, BOGUS, KRW, 7654321, 2026-01-01, …)

    마지막 관문(마스킹 프로세서)이 지운다.
    """
    sku_id = create_sku()

    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            SkuPrice(
                sku_id=sku_id,
                price_type="BOGUS",  # CHECK 위반 — 앱 검증을 우회해 직접 넣는다
                currency="KRW",
                amount=int(COST),
                effective_from="2026-01-01",
            )
        )
        uow.session.flush()

    # 예외 문자열 자체에는 남아 있다 — PostgreSQL이 만든 문자열이기 때문이다.
    assert COST in str(exc.value)

    with caplog.at_level(logging.ERROR):
        get_logger("test.leak").error("unhandled_exception", exc_info=exc.value)

    assert COST not in caplog.text
    # 진단은 유지된다 — 어떤 제약이 걸렸는지는 남는다.
    assert "ck_sku_prices_price_type_valid" in caplog.text


def test_a_newline_inside_a_value_does_not_defeat_the_scrub(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """★ 값에 개행이 있어도 행 전체가 지워진다

    PostgreSQL은 값 안의 개행을 **그대로 싣는다**(실측). 줄 끝으로 자르면 개행
    앞만 지워지고 뒤가 남는다 — 지금 sku_prices는 금액이 메모보다 앞이라 당장
    새지 않지만, 이 프로세서는 전역 관문이고 다른 테이블의 컬럼 순서는 보장되지
    않는다(S3-1 비용·S6-2 원가가 재발 지점이다).
    """
    sku_id = create_sku()
    tail = "TAILSECRET9999999"

    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            SkuPrice(
                sku_id=sku_id,
                price_type="BOGUS",
                currency="KRW",
                amount=int(COST),
                effective_from="2026-01-01",
                note=f"메모\n{tail}",
            )
        )
        uow.session.flush()

    # 서버가 보낸 원문에는 개행 뒤 값까지 들어 있다.
    assert tail in str(exc.value)

    with caplog.at_level(logging.ERROR):
        get_logger("test.leak").error("unhandled_exception", exc_info=exc.value)

    assert COST not in caplog.text
    assert tail not in caplog.text
    # 진단은 유지된다 — 제약 이름과 실패한 문장이 남는다.
    assert "ck_sku_prices_price_type_valid" in caplog.text
    assert "INSERT INTO sku_prices" in caplog.text
