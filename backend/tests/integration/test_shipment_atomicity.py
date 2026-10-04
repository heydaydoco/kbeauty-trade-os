"""J. 선적 생성·취소의 원자성과 제약 번역 완결성 (S3-2 PR-3a / design-C C1·C5·C14 J-08·J-09).

■ J-08 — 업무 동작 1개 = TX 1개: 생성 TX 중간(채번·INSERT 뒤, SO 수렴 직전)에 실패를 주입하면 선적·라인·당사자·선적 이력·SO 상태·SO 이력·
  아웃박스·멱등 키·채번 카운터가 **전부** 롤백된다(번호 구멍은 허용 — 카운터 행도 같은 TX라 되돌아간다).
■ J-09 — DB 유니크·부분 유니크 위반이 500으로 새지 않는다: 번역표(`CONSTRAINT_ERRORS`)가 선적 계열 표의 실제 유니크 인덱스와 입력 텍스트 열
  CHECK를 **전부** 덮는다. 입력 텍스트(거래처 영문명·주소)의 보이지 않는 글자는 미리보기·생성·당사자 추가 모두 같은 422(PR-3a 적대 검토 반영).
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


@pytest.mark.group_k
def test_the_nightly_totals_verify_covers_shipments() -> None:
    """§17.5 이중망 — 야간 합계 검산(`trade-docs-totals-verify`)이 선적을 DocKind 루프로 자동 편입한다: 생성·라인 수정 뒤 불일치 0,
    헤더 합계를 원시로 어긋나게 하면 그 선적 번호로 잡힌다(자동 보정 없음)"""
    from app.core.db.uow import unit_of_work
    from app.modules.trade_docs import verify
    from app.modules.trade_docs.constants import DocKind

    so = confirmed_so((10, 5))
    actor = make_user(RoleCode.TRADE)
    _, body = shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=unique("tot"),
        so_id=so["id"],
        payload=create_body([(so["line_ids"][0], 4), (so["line_ids"][1], 5)]),
    )
    shipment_flow.update_line(
        actor=actor,
        shipment_id=body["id"],
        line_id=body["lines"][0]["id"],
        payload={"version": body["version"], "quantity": 2},
    )

    def shipment_mismatches() -> list[tuple[str, int]]:
        with unit_of_work() as uow:
            return [
                (m.doc_number, m.header_total - m.lines_total)
                for m in verify.verify_document_totals(uow.session)
                if m.doc_kind is DocKind.SHIPMENT
            ]

    assert shipment_mismatches() == []
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE shipments SET total_amount = total_amount + 1 WHERE id = :i"),
            {"i": body["id"]},
        )
    assert shipment_mismatches() == [(body["doc_number"], 1)]


#: 입력(거래처 마스터의 자유 텍스트)에서 값이 오는 열 — 이 열을 검사하는 CHECK는 서비스 검사를 빠져나간 값이 닿을 수 있으므로 번역표 대상이다.
INPUT_TEXT_COLUMNS = ("name_en", "address_en")


def test_the_translation_table_covers_every_unique_index_and_input_check_of_the_shipment_tables() -> (
    None
):
    """J-09 — 번역표 = 선적 계열 표의 유니크 인덱스(부분 유니크 포함, PK·복합 FK 대상 제외) ∪ 입력 텍스트 열(`name_en`·`address_en`)을 검사하는
    CHECK. 빠진 이름도 죽은 이름도 없다(500 누수 0 — PR-3a 적대 검토: C1 제어문자가 `name_en_clean`에 걸려 500이던 경로)"""
    with owner_engine.connect() as connection:
        uniques = {
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
        input_checks = {
            r[0]
            for r in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE contype = 'c'"
                    " AND conrelid::regclass::text IN ('shipments','shipment_lines','shipment_parties')"
                    " AND pg_get_constraintdef(oid) ~ :cols"
                ),
                {"cols": r"\m(" + "|".join(INPUT_TEXT_COLUMNS) + r")\M"},
            )
        }
    assert input_checks == {
        "ck_shipment_parties_name_en_clean",
        "ck_shipment_parties_address_en_clean",
        "ck_shipment_parties_address_en_not_blank",
    }  # 공회전 방지 — 대상 CHECK를 실제로 찾는다
    names = uniques | input_checks
    assert names == set(CONSTRAINT_ERRORS), names ^ set(CONSTRAINT_ERRORS)


# ── 입력 텍스트 위생(PR-3a 적대 검토 반영 — C1 제어문자 500·주소 제어문자) ──────────────────────

C1_CONTROLS = (
    "\u0085",
    "\u0096",
)  # NEL·SPA — C0·DEL만 보던 검사를 빠져나가 DB CHECK에서 500이던 값


def _stored() -> dict[str, Any]:
    return {
        "shipments": scalar("SELECT count(*) FROM shipments"),
        "parties": scalar("SELECT count(*) FROM shipment_parties"),
        "keys": scalar("SELECT count(*) FROM idempotency_keys"),
        "events": scalar("SELECT count(*) FROM events WHERE event_type LIKE 'shipments.%'"),
    }


@pytest.mark.parametrize("control", C1_CONTROLS)
def test_c1_controls_in_the_buyer_english_name_are_422_on_preview_and_create(control: str) -> None:
    """바이어 영문명에 C1 제어문자(U+0085·U+0096) — 미리보기·생성 모두 422 ENGLISH_NAME_MISSING(500 아님)·저장 0(멱등 키·이벤트 포함)"""
    from tests.factories.trade import create_buyer

    buyer = create_buyer(name_en=f"Acme{control}Trading")
    so = confirmed_so((10,), buyer=buyer)
    actor = make_user(RoleCode.TRADE)
    before = _stored()
    body = create_body([(so["line_ids"][0], 2)])
    for call in (
        lambda: shipment_flow.preview_shipment_from_sales_order(
            actor=actor, so_id=so["id"], payload=body
        ),
        lambda: shipment_flow.create_shipment_from_sales_order(
            actor=actor, idempotency_key=unique("c1"), so_id=so["id"], payload=body
        ),
    ):
        with pytest.raises(AppError) as caught:
            call()
        assert caught.value.code == "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING"
        assert caught.value.status_code == 422 and caught.value.detail == {
            "so_id": "거래처 영문명을 확인해 주세요."
        }
    assert _stored() == before and so_status(so["id"]) == "CONFIRMED"


@pytest.mark.parametrize("control", C1_CONTROLS)
def test_c1_controls_in_a_party_english_name_are_422_on_add_and_in_the_create_body(
    control: str,
) -> None:
    """FORWARDER 거래처 영문명에 C1 제어문자 — 당사자 추가·생성 본문 당사자·미리보기 모두 422 ENGLISH_NAME_MISSING·당사자 행 0"""
    from tests.factories.trade import create_supplier

    forwarder = create_supplier(types=("FORWARDER",), name_en=f"Fast{control}Freight")
    so = confirmed_so((10,))
    actor = make_user(RoleCode.TRADE)
    _, shipment = shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=unique("c1"),
        so_id=so["id"],
        payload=create_body([(so["line_ids"][0], 2)]),
    )
    before = _stored()
    parties = [{"role": "FORWARDER", "partner_id": forwarder}]
    body = create_body([(so["line_ids"][0], 1)], parties=parties)
    for call, field in (
        (
            lambda: shipment_flow.add_party(
                actor=actor,
                idempotency_key=unique("c1"),
                shipment_id=shipment["id"],
                payload=parties[0],
            ),
            "partner_id",
        ),
        (
            lambda: shipment_flow.create_shipment_from_sales_order(
                actor=actor, idempotency_key=unique("c1"), so_id=so["id"], payload=body
            ),
            "parties[0].partner_id",
        ),
        (
            lambda: shipment_flow.preview_shipment_from_sales_order(
                actor=actor, so_id=so["id"], payload=body
            ),
            "parties[0].partner_id",
        ),
    ):
        with pytest.raises(AppError) as caught:
            call()
        assert caught.value.code == "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING", field
        assert caught.value.status_code == 422 and set(caught.value.detail or {}) == {field}
    assert _stored() == before


def test_the_db_check_is_translated_when_the_service_check_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2차 방어선 — 서비스 검사를 우회(변이 흉내)해도 DB CHECK `name_en_clean` 위반이 생성·당사자 추가 모두 422 ENGLISH_NAME_MISSING으로 번역된다(500 아님)"""
    from app.modules.shipments import service as shipments_service
    from tests.factories.trade import create_buyer, create_supplier

    monkeypatch.setattr(
        shipments_service, "require_english_name", lambda name_en, *, field: (name_en or "").strip()
    )
    buyer = create_buyer(name_en="Acme\u0085Trading")
    so = confirmed_so((10,), buyer=buyer)
    actor = make_user(RoleCode.TRADE)
    with pytest.raises(AppError) as created:
        shipment_flow.create_shipment_from_sales_order(
            actor=actor,
            idempotency_key=unique("c1"),
            so_id=so["id"],
            payload=create_body([(so["line_ids"][0], 2)]),
        )
    assert created.value.code == "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING"
    clean = confirmed_so((10,))
    _, shipment = shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=unique("c1"),
        so_id=clean["id"],
        payload=create_body([(clean["line_ids"][0], 2)]),
    )
    forwarder = create_supplier(types=("FORWARDER",), name_en="Fast\u0096Freight")
    with pytest.raises(AppError) as added:
        shipment_flow.add_party(
            actor=actor,
            idempotency_key=unique("c1"),
            shipment_id=shipment["id"],
            payload={"role": "FORWARDER", "partner_id": forwarder},
        )
    assert added.value.code == "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING"
    assert added.value.status_code == 422
    assert scalar("SELECT count(*) FROM shipments") == 1


@pytest.mark.parametrize("bad", ["1 Main St\x0bLA", "1 Main St\u0085LA", "1 Main​St"])
def test_address_controls_other_than_line_breaks_are_422(bad: str) -> None:
    """당사자 영문 주소 — 탭·LF·CR만 허용, 그 밖의 제어(VT·C1)·서식(제로폭) 문자는 생성 422 INVALID_FIELD(`detail.so_id`)·저장 0"""
    from tests.factories.trade import create_buyer

    so = confirmed_so((10,), buyer=create_buyer(address_en=bad))
    actor = make_user(RoleCode.TRADE)
    before = _stored()
    with pytest.raises(AppError) as caught:
        shipment_flow.create_shipment_from_sales_order(
            actor=actor,
            idempotency_key=unique("addr"),
            so_id=so["id"],
            payload=create_body([(so["line_ids"][0], 2)]),
        )
    assert caught.value.code == "COMMON.VALIDATION.INVALID_FIELD" and set(
        caught.value.detail or {}
    ) == {"so_id"}
    assert _stored() == before


def test_multi_line_addresses_with_tabs_and_line_breaks_are_kept() -> None:
    """여러 줄 주소(탭·LF·CR)는 그대로 스냅샷된다(앞뒤 공백만 정리)"""
    from tests.factories.trade import create_buyer

    address = "1 Main St\r\nSuite\t5\nLos Angeles"
    so = confirmed_so((10,), buyer=create_buyer(address_en=f"  {address}  "))
    _, shipment = shipment_flow.create_shipment_from_sales_order(
        actor=make_user(RoleCode.TRADE),
        idempotency_key=unique("addr"),
        so_id=so["id"],
        payload=create_body([(so["line_ids"][0], 2)]),
    )
    consignee = next(p for p in shipment["parties"] if p["role"] == "CONSIGNEE")
    assert consignee["address_en"] == address


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
