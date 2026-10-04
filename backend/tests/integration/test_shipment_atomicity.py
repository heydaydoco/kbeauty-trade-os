"""J. 선적 생성·취소의 원자성과 제약 번역 완결성 (S3-2 PR-3a / design-C C1·C5·C14 J-08·J-09).

■ J-08 — 업무 동작 1개 = TX 1개: 생성 TX 중간(채번·INSERT 뒤, SO 수렴 직전)에 실패를 주입하면 선적·라인·당사자·선적 이력·SO 상태·SO 이력·
  아웃박스·멱등 키·채번 카운터가 **전부** 롤백된다(번호 구멍은 허용 — 카운터 행도 같은 TX라 되돌아간다).
■ J-09 — DB 유니크·부분 유니크 위반이 500으로 새지 않는다: 번역표(`CONSTRAINT_ERRORS`)가 선적 계열 표의 실제 유니크 인덱스를 **전부** 덮는다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.errors.exceptions import AppError
from app.modules.identity.models import RoleCode
from app.modules.shipments.service import CONSTRAINT_ERRORS
from app.modules.trade_chain import chain_ops, shipment_flow
from tests.factories.approvals import make_user
from tests.factories.shipments import confirmed_so, create_body, scalar, so_status
from tests.factories.trade import unique

pytestmark = pytest.mark.group_j


class _Boom(RuntimeError):
    pass


def _counts(so_id: int) -> dict[str, Any]:
    return {
        "shipments": scalar("SELECT count(*) FROM shipments"),
        "lines": scalar("SELECT count(*) FROM shipment_lines"),
        "parties": scalar("SELECT count(*) FROM shipment_parties"),
        "log": scalar("SELECT count(*) FROM shipment_status_log"),
        "so_log": scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so_id
        ),
        "events": scalar("SELECT count(*) FROM events"),
        "keys": scalar("SELECT count(*) FROM idempotency_keys"),
        "seq": scalar("SELECT COALESCE(SUM(last_number), 0) FROM doc_number_seq"),
        "so_status": so_status(so_id),
    }


def test_a_failure_after_insert_rolls_back_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """J-08 — 채번·INSERT·탄생 이력·created 이벤트 뒤 SO 수렴 직전 예외 → 선적·라인·당사자·이력·SO 상태·이벤트·멱등 키·채번 카운터 전부 원상"""
    so = confirmed_so((10,))
    actor = make_user(RoleCode.TRADE)
    before = _counts(so["id"])

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise _Boom("주입 실패")

    monkeypatch.setattr(shipment_flow, "converge_parent", boom)
    with pytest.raises(_Boom):
        shipment_flow.create_shipment_from_sales_order(
            actor=actor,
            idempotency_key=unique("atom"),
            so_id=so["id"],
            payload=create_body([(so["line_ids"][0], 4)]),
        )
    assert _counts(so["id"]) == before
    assert before["so_status"] == "CONFIRMED"


def test_a_failure_after_cancel_keeps_the_shipment_and_the_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J-08 — 취소 전이 뒤 SO 복귀 수렴에서 예외 → 선적은 계획 그대로·SO는 선적중 그대로(거짓 상태 0)"""
    so = confirmed_so((10,))
    actor = make_user(RoleCode.TRADE)
    _, body = shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=unique("atom"),
        so_id=so["id"],
        payload=create_body([(so["line_ids"][0], 4)]),
    )
    original = chain_ops.converge_sales_order_shipping

    def boom(*args: Any, **kwargs: Any) -> None:
        raise _Boom("주입 실패")

    monkeypatch.setattr(chain_ops, "converge_sales_order_shipping", boom)
    with pytest.raises(_Boom):
        shipment_flow.transition_shipment(
            actor=actor,
            idempotency_key=unique("atom"),
            shipment_id=body["id"],
            to="CANCELLED",
            version=body["version"],
            reason="취소",
        )
    assert scalar("SELECT status FROM shipments WHERE id = :i", i=body["id"]) == "PLANNED"
    assert so_status(so["id"]) == "IN_SHIPMENT"
    assert original is not boom


def test_the_translation_table_covers_every_unique_index_of_the_shipment_tables() -> None:
    """J-09 — 선적 계열 표의 유니크 인덱스(부분 유니크 포함, PK 제외)는 전부 번역표에 있고, 번역표에 죽은 이름이 없다(500 누수 0)"""
    with owner_engine.connect() as connection:
        names = {
            r[0]
            for r in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename IN"
                    " ('shipments','shipment_lines','shipment_parties')"
                    " AND indexdef LIKE 'CREATE UNIQUE%' AND indexname NOT LIKE 'pk_%'"
                    " AND indexname <> 'uq_shipments_id_currency'"
                )
            )
        }
    assert names == set(CONSTRAINT_ERRORS), names ^ set(CONSTRAINT_ERRORS)


def test_a_raced_duplicate_party_is_409_not_500() -> None:
    """J-09 — 선검사를 빠져나간 (선적, 역할) 중복(원시 INSERT로 경합 재현)이 서비스 flush에서 409 ROLE_DUPLICATE로 번역된다"""
    from tests.factories.trade import create_buyer

    so = confirmed_so((10,))
    actor = make_user(RoleCode.TRADE)
    _, body = shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=unique("atom"),
        so_id=so["id"],
        payload=create_body([(so["line_ids"][0], 4)]),
    )
    notify = create_buyer(name_en="Notify Co.")
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO shipment_parties (shipment_id, role, partner_id, name_en, is_auto)"
                " VALUES (:s, 'NOTIFY', :p, 'Raced', false)"
            ),
            {"s": body["id"], "p": notify},
        )
    with pytest.raises(AppError) as caught:
        shipment_flow.add_party(
            actor=actor,
            idempotency_key=unique("party"),
            shipment_id=body["id"],
            payload={"role": "NOTIFY", "partner_id": notify},
        )
    assert caught.value.code == "SHIPMENTS.PARTY.ROLE_DUPLICATE" and caught.value.status_code == 409
