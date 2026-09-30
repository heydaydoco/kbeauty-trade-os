"""G. 보안 — PO 원가 마스킹 **9채널 전수 대사** (S3-1 PR-8a / ADR-0057 ⑤ · ADR-0024 / design-F F5 / 통합 §3.1 PR-8 · GC-G2).

원가(`unit_cost`·`line_cost`·`total_cost`·`currency`·`price_basis`)는 원가를 볼 수 없는 역할(VIEWER·역할 없음)에게 **응답·CSV·로그·감사·이벤트·에러 메시지·문서 흐름·알림
어디에도** 나가지 않는다. PO는 행이 아니라 **필드**를 감추는 혼합 목적 행이라(ADR-0024) VIEWER도 200으로 행·건수·상태를 본다.

ADR-0057 ⑤가 열거한 9채널을 채널마다 테스트로 세운다(센티널 원가를 만들어 모든 채널을 순회 — 0회여야 한다):
  ① 정렬·필터·검색   ② CSV   ③ 에러 detail·검증 메시지   ④ 로그(+PG "Failing row" 경로)   ⑤ audit_log.detail   ⑥ outbox 이벤트·알림·브리핑
  ⑦ idempotency_keys.response_body(at-rest 수용 사실 — 스코프 고정)   ⑧ documents 첨부(소비자 없음 — 붙일 수 없다)   ⑨ 집계·보드·문서 흐름(다른 전표에 원가 계열 필드 부재)
센티널은 원가·라인원가·합계·파생값이 서로 다른 숫자라 어느 하나가 새도 잡힌다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.logging import get_logger
from app.modules.identity.models import RoleCode
from app.modules.notifications import dispatcher
from app.modules.purchase_orders.models import PurchaseOrderLine
from app.modules.worklist.models import Alert, AlertRule
from tests.factories.trade import (
    SENTINEL_UNIT_COST,
    SENTINEL_UNIT_COST_TEXT,
    create_po_via_api,
    create_purchase_priced_sku,
    create_supplier,
    idem,
    issued_pi_chain,
    logged_in,
    po_payload,
    raw_po,
    unique,
)
from tests.support.factories import DEFAULT_PASSWORD, create_sku, create_user

pytestmark = pytest.mark.group_g

PO = "/api/v1/purchase-orders"
COST_KEY = re.compile(r"(_cost$|^cost$|^currency$|^price_)")
COST_VISIBLE = (RoleCode.ADMIN, RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT)
#: 센티널에서 파생되는 값 — 단가(최소단위·표시)·수량 3줄 라인원가·합계(1줄 + 3줄 + 6줄).
UNIT = SENTINEL_UNIT_COST
SENTINELS = (
    str(UNIT),  # 7654321
    SENTINEL_UNIT_COST_TEXT,  # 76543.21
    str(UNIT * 3),  # 22962963
    f"{UNIT * 3 / 100:.2f}",  # 229629.63
    str(UNIT * 10),  # 76543210
    f"{UNIT * 10 / 100:.2f}",  # 765432.10
)


def _assert_clean(blob: str, *, where: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in blob, f"{where}: 원가 센티널 {sentinel}이(가) 나갔다"


def _scan_keys(node: Any) -> set[str]:
    """JSON을 재귀로 훑어 모든 키를 모은다."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= _scan_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _scan_keys(item)
    return keys


def _rows(sql: str, **params: Any) -> list[Any]:
    with owner_engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params).all()]


@pytest.fixture
def seeded() -> dict[str, Any]:
    """센티널 원가를 가진 PO 2건(1줄 수량 1 / 2줄 수량 3·6) — 생성자는 TRADE. 이후 VIEWER 채널 순회의 대상이다."""
    supplier = create_supplier(name_en="Sentinel Supply Ltd")
    sku_a = create_sku(unique("SA"))
    sku_b = create_sku(unique("SB"))
    sku_c = create_sku(unique("SC"))
    with logged_in(RoleCode.TRADE) as trade:
        first = trade.post(
            PO,
            json=po_payload(
                supplier,
                [],
                lines=[{"sku_id": sku_a, "quantity": 1, "unit_cost": SENTINEL_UNIT_COST_TEXT}],
            ),
            headers=idem(),
        )
        second = trade.post(
            PO,
            json=po_payload(
                supplier,
                [],
                lines=[
                    {"sku_id": sku_b, "quantity": 3, "unit_cost": SENTINEL_UNIT_COST_TEXT},
                    {"sku_id": sku_c, "quantity": 6, "unit_cost": SENTINEL_UNIT_COST_TEXT},
                ],
            ),
            headers=idem(),
        )
        assert first.status_code == second.status_code == 201, (first.text, second.text)
        # 센티널이 실제로 저장됐다(이후 "0회"가 공회전이 아님을 보증)
        assert first.json()["lines"][0]["unit_cost"] == UNIT
        assert second.json()["total_cost"] == UNIT * 9
    return {
        "supplier": supplier,
        "pos": [first.json(), second.json()],
        "skus": [sku_a, sku_b, sku_c],
    }


# ── 채널 0: 응답 본문 — 필드 부재 · 200 · 건수 동일 ────────────────────────────────


@pytest.mark.parametrize("role", list(RoleCode), ids=[r.value for r in RoleCode])
def test_detail_list_and_lines_carry_cost_keys_only_for_cost_visible_roles(
    seeded: dict[str, Any], role: RoleCode
) -> None:
    """5역할 × (상세·목록·라인) 재귀 키 스캔 — VIEWER 응답에는 `*_cost`·`currency`·`price_*` 키가 **0개**(값 null이 아니라 키 부재), ADMIN·TRADE·LOGISTICS·CERT는 존재한다.
    VIEWER도 상세·목록은 **200**(404·403 아님)이고 행 수·total·상태는 원가 역할과 같다"""
    with logged_in(role) as client:
        detail = client.get(f"{PO}/{seeded['pos'][1]['id']}")
        listing = client.get(PO)
        assert detail.status_code == listing.status_code == 200
        keys = _scan_keys(detail.json()) | _scan_keys(listing.json())
        leaked = {k for k in keys if COST_KEY.search(k)}
        extra_hidden = {
            "total_text",
            "unit_cost_text",
            "line_cost_text",
            "minor_units",
            "fx_rate",
            "fx_rate_date",
        }
        if role in COST_VISIBLE:
            assert {"total_cost", "currency", "unit_cost", "line_cost", "price_basis"} <= keys
            assert extra_hidden <= keys
        else:
            assert leaked == set() and not keys & extra_hidden, (role, leaked)
        assert listing.json()["total"] == 2 and len(listing.json()["items"]) == 2
        assert [line["quantity"] for line in detail.json()["lines"]] == [3, 6]
        assert (
            detail.json()["status"] == "ISSUED"
            and detail.json()["supplier_name"] == "Sentinel Supply Ltd"
        )


def test_a_user_without_any_role_also_gets_the_cost_hidden_shape(seeded: dict[str, Any]) -> None:
    """역할이 하나도 없는 계정은 기본값이 안전한 쪽 — 원가 없는 응답(200)이다"""
    email = f"{unique('norole')}@example.com"
    create_user(email, roles=())
    from app.main import app

    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        ).is_success
        body = client.get(f"{PO}/{seeded['pos'][0]['id']}")
        assert body.status_code == 200
        assert not {k for k in _scan_keys(body.json()) if COST_KEY.search(k)}
        _assert_clean(body.text, where="역할 없음 상세")


def test_the_service_layer_omits_cost_keys_before_the_schema_even_applies(
    seeded: dict[str, Any],
) -> None:
    """방어 1층 — 서비스가 `include_cost=False`로 만든 dict(상세·목록·CSV 행)에는 원가 키·값이 **처음부터 없다**(만든 뒤 지우지 않는다 — ADR-0024). 라우터의 스키마 분기(2층)가 뚫려도 값이 없다"""
    from app.modules.purchase_orders import service

    po_id = seeded["pos"][1]["id"]
    detail = service.get_purchase_order(po_id, include_cost=False)
    items, total = service.list_purchase_orders(offset=0, limit=50, include_cost=False)
    rows = service.export_rows(include_cost=False)
    assert total == 2 and len(items) == 2 and len(rows) == 2
    for label, payload in (("상세", detail), ("목록", items)):
        assert not {k for k in _scan_keys(payload) if COST_KEY.search(k)}, label
        assert not _scan_keys(payload) & {
            "total_text",
            "unit_cost_text",
            "line_cost_text",
            "minor_units",
            "fx_rate",
        }, label
        _assert_clean(json.dumps(payload, default=str), where=f"서비스 {label}")
    _assert_clean(json.dumps(rows, default=str), where="서비스 CSV 행")
    assert len(rows[0]) == len(service.EXPORT_HEADER_NO_COST)
    full = service.get_purchase_order(
        po_id, include_cost=True
    )  # 공회전 방지 — 같은 함수가 True면 원가가 있다
    assert full["total_cost"] == UNIT * 9 and full["lines"][0]["unit_cost"] == UNIT


# ── 채널 1: 정렬·필터·검색 ──────────────────────────────────────────────────────


def test_channel_1_sort_filter_and_search_cannot_reach_cost_values(seeded: dict[str, Any]) -> None:
    """① VIEWER: 원가·통화·가격 기준으로 정렬하면 422, 원가 범위 필터는 존재하지 않아 붙여도 무시(결과 동일), 원가 값(센티널)으로 검색하면 0건 — 정렬·필터·검색으로 원가를 추정할 수 없다"""
    with logged_in(RoleCode.VIEWER) as viewer:
        plain = viewer.get(PO).json()
        for key in (
            "total_cost",
            "unit_cost",
            "line_cost",
            "currency",
            "price_basis",
            "total_text",
        ):
            assert viewer.get(PO, params={"sort": key}).status_code == 422, key
            assert viewer.get(PO, params={"sort": key, "order": "asc"}).status_code == 422, key
        for extra in (
            {"total_cost_min": 1},
            {"total_cost_max": 1},
            {"unit_cost": UNIT},
            {"min_total_cost": UNIT * 100},
            {"currency": "KRW"},
            {"total_cost": UNIT},
        ):
            assert viewer.get(PO, params=extra).json() == plain, extra
        for needle in (*SENTINELS, "USD", "MASTER", "MANUAL"):
            found = viewer.get(PO, params={"q": needle}).json()
            assert found["total"] == 0 and found["items"] == [], needle
        _assert_clean(json.dumps(plain), where="VIEWER 목록")


def test_channel_1_sorting_by_allowed_keys_never_orders_by_cost(seeded: dict[str, Any]) -> None:
    """허용 정렬 키(발행일·번호·상태·공급사명·생성일)는 원가와 무관하다 — 같은 정렬 결과가 원가 역할과 VIEWER에서 동일(원가 순서가 새지 않는다)"""
    with logged_in(RoleCode.TRADE) as trade, logged_in(RoleCode.VIEWER) as viewer:
        for key in ("doc_date", "doc_number", "status", "supplier_name", "created_at"):
            for order in ("asc", "desc"):
                a = [
                    i["id"]
                    for i in trade.get(PO, params={"sort": key, "order": order}).json()["items"]
                ]
                b = [
                    i["id"]
                    for i in viewer.get(PO, params={"sort": key, "order": order}).json()["items"]
                ]
                assert a == b, (key, order)


# ── 채널 2: CSV ────────────────────────────────────────────────────────────────


def test_channel_2_csv_header_and_cells_have_no_cost_columns_for_the_viewer(
    seeded: dict[str, Any],
) -> None:
    """② CSV: VIEWER는 **원가·통화 열이 없는 헤더**(역할별 헤더 2종)이고 어느 셀에도 센티널이 없다 — 원가 역할은 합계·통화 열이 있다"""
    from app.modules.purchase_orders.service import EXPORT_HEADER_NO_COST, EXPORT_HEADER_WITH_COST

    assert not {"통화", "합계"} & set(EXPORT_HEADER_NO_COST) and {"통화", "합계"} <= set(
        EXPORT_HEADER_WITH_COST
    )
    with logged_in(RoleCode.VIEWER) as viewer:
        response = viewer.get(f"{PO}/export.csv")
        assert response.status_code == 200
        lines = response.content.decode("utf-8-sig").splitlines()
        assert lines[0] == ",".join(EXPORT_HEADER_NO_COST) and len(lines) == 3
        assert "USD" not in response.text
        _assert_clean(response.text, where="VIEWER CSV")
        filtered = viewer.get(f"{PO}/export.csv", params={"q": seeded["pos"][0]["doc_number"]})
        _assert_clean(filtered.text, where="VIEWER CSV(필터)")
    for role in COST_VISIBLE:
        with logged_in(role) as client:
            header = client.get(f"{PO}/export.csv").content.decode("utf-8-sig").splitlines()[0]
            assert header == ",".join(EXPORT_HEADER_WITH_COST), role


# ── 채널 3: 에러 detail·검증 메시지 ──────────────────────────────────────────────


def test_channel_3_error_bodies_never_echo_cost_values(seeded: dict[str, Any]) -> None:
    """③ 작성자(TRADE)가 센티널 원가를 실은 요청이 **실패**하는 모든 경로 — 중복 SKU·수량 오류·자릿수 초과·타입 오류·금지 필드·없는 SKU·상한 초과·완결성 누락·권한 오류 — 의 응답 본문에 입력한 원가가 되돌아오지 않는다"""
    supplier = seeded["supplier"]
    sku = create_purchase_priced_sku()
    cost = SENTINEL_UNIT_COST_TEXT
    bad_bodies: list[dict[str, Any]] = [
        po_payload(
            supplier,
            [],
            lines=[
                {"sku_id": sku, "quantity": 1, "unit_cost": cost},
                {"sku_id": sku, "quantity": 2, "unit_cost": cost},
            ],
        ),
        po_payload(supplier, [], lines=[{"sku_id": sku, "quantity": 0, "unit_cost": cost}]),
        po_payload(supplier, [], lines=[{"sku_id": sku, "quantity": "x", "unit_cost": cost}]),
        po_payload(
            supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": f"{cost}1"}]
        ),  # 자릿수 초과
        po_payload(
            supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": UNIT}]
        ),  # 타입 오류(문자열이어야)
        po_payload(
            supplier,
            [],
            lines=[{"sku_id": sku, "quantity": 1, "unit_cost": cost, "line_cost": UNIT}],
        ),  # 금지 필드
        {
            **po_payload(supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": cost}]),
            "total_cost": UNIT,
        },
        po_payload(supplier, [], lines=[{"sku_id": 9_999_999, "quantity": 1, "unit_cost": cost}]),
        po_payload(
            supplier,
            [],
            lines=[{"sku_id": sku, "quantity": 99_999_999, "unit_cost": "90000000000000.00"}],
        ),
        po_payload(
            supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": cost}], currency="XXX"
        ),
        po_payload(
            supplier,
            [],
            lines=[{"sku_id": sku, "quantity": 1, "unit_cost": cost}],
            po_kind="OEM_PRODUCTION",
        ),
        {
            "supplier_partner_id": supplier,
            "currency": "USD",
            "lines": [{"sku_id": sku, "quantity": 1, "unit_cost": cost}],
        },
    ]
    with logged_in(RoleCode.TRADE) as trade:
        for body in bad_bodies:
            response = trade.post(PO, json=body, headers=idem())
            assert response.status_code in (409, 422), response.text
            _assert_clean(response.text, where=f"TRADE 오류 본문 {response.status_code}")
            preview = trade.post(f"{PO}/preview", json=body)
            assert preview.status_code in (409, 422)
            _assert_clean(preview.text, where="TRADE 미리보기 오류 본문")
        # 매입가 없는 SKU — NOT_EFFECTIVE 오류는 기준일·통화·SKU만 말한다(값 없음)
        no_price = create_sku(unique("NP"))
        missing = trade.post(
            PO,
            json=po_payload(supplier, [], lines=[{"sku_id": no_price, "quantity": 1}]),
            headers=idem(),
        )
        assert (
            missing.status_code == 422
            and missing.json()["error"]["code"] == "CATALOG.PRICE.NOT_EFFECTIVE"
        )
        _assert_clean(missing.text, where="NOT_EFFECTIVE")
    with logged_in(RoleCode.VIEWER) as viewer:
        denied = viewer.post(PO, json=bad_bodies[0], headers=idem())
        assert denied.status_code == 403
        _assert_clean(denied.text, where="VIEWER 403")
        missing_po = viewer.get(f"{PO}/999999")
        assert missing_po.status_code == 404
        _assert_clean(missing_po.text, where="VIEWER 404")
    assert (
        _rows("SELECT count(*) FROM purchase_orders")[0][0] == 2
    )  # 실패 요청은 전표를 만들지 않았다


def test_channel_3_transition_and_meta_errors_do_not_leak_either(seeded: dict[str, Any]) -> None:
    """③ 전이·메타의 오류(낡은 version·종결 후 재취소·OC 경계·금지 필드)도 원가를 싣지 않는다 — 동결 PO의 본문은 409/422 어디에도 금액이 없다"""
    po = seeded["pos"][0]
    with logged_in(RoleCode.TRADE) as trade:
        url = f"{PO}/{po['id']}"
        responses = [
            trade.post(
                f"{url}/transitions",
                json={"to": "CANCELLED", "version": 99, "reason": "r"},
                headers=idem(),
            ),
            trade.post(
                f"{url}/transitions",
                json={
                    "to": "SUPPLIER_CONFIRMED",
                    "version": po["version"],
                    "oc_received_on": "2000-01-01",
                },
                headers=idem(),
            ),
            trade.post(
                f"{url}/transitions",
                json={"to": "CLOSED", "version": po["version"]},
                headers=idem(),
            ),
            trade.patch(f"{url}/meta", json={"version": po["version"], "total_cost": UNIT}),
            trade.patch(f"{url}/meta", json={"version": po["version"], "oc_reference": "x"}),
        ]
        assert all(r.status_code in (409, 422) for r in responses), [
            r.status_code for r in responses
        ]
        for r in responses:
            _assert_clean(r.text, where="전이·메타 오류")


# ── 채널 4: 로그 ───────────────────────────────────────────────────────────────


def test_channel_4_no_log_line_carries_the_cost_on_any_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """④ DEBUG까지 캡처한 상태에서 생성(성공·실패·중복)·미리보기·전이·메타·VIEWER 조회·CSV를 전부 돌려도 **로그 어디에도 원가 센티널이 없다**(키 마스킹은 이름 기반이라 값을 로그에 넣지 않는 것이 유일한 방어다)"""
    supplier = create_supplier()
    sku_a, sku_b = create_sku(unique("LA")), create_sku(unique("LB"))
    body = po_payload(
        supplier,
        [],
        lines=[
            {"sku_id": sku_a, "quantity": 1, "unit_cost": SENTINEL_UNIT_COST_TEXT},
            {"sku_id": sku_b, "quantity": 3, "unit_cost": SENTINEL_UNIT_COST_TEXT},
        ],
    )
    with caplog.at_level(logging.DEBUG):
        with logged_in(RoleCode.TRADE) as trade:
            assert trade.post(f"{PO}/preview", json=body).status_code == 200
            created = trade.post(PO, json=body, headers=idem())
            assert created.status_code == 201
            po = created.json()
            assert (
                trade.post(PO, json={**body, "currency": "XXX"}, headers=idem()).status_code == 422
            )  # 실패
            dup_lines = {**body, "lines": [body["lines"][0], body["lines"][0]]}
            assert trade.post(PO, json=dup_lines, headers=idem()).status_code == 409  # 중복 SKU
            assert (
                trade.patch(
                    f"{PO}/{po['id']}/meta", json={"version": po["version"], "internal_note": "n"}
                ).status_code
                == 200
            )
            stale = trade.post(
                f"{PO}/{po['id']}/transitions",
                json={"to": "CANCELLED", "version": 1, "reason": "r"},
                headers=idem(),
            )
            assert stale.status_code == 409
            assert (
                trade.post(
                    f"{PO}/{po['id']}/transitions",
                    json={"to": "CANCELLED", "version": po["version"] + 1, "reason": "r"},
                    headers=idem(),
                ).status_code
                == 200
            )
        with logged_in(RoleCode.VIEWER) as viewer:
            assert viewer.get(f"{PO}/{po['id']}").status_code == 200
            assert viewer.get(PO).status_code == 200
            assert viewer.get(f"{PO}/export.csv").status_code == 200
            assert viewer.get(PO, params={"sort": "total_cost"}).status_code == 422
    _assert_clean(caplog.text, where="로그")


def test_channel_4_a_po_line_constraint_violation_does_not_leak_the_failing_row(
    seeded: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """④ PostgreSQL의 "Failing row contains (…)"는 서버가 만든 행 전체 값이다 — PO 라인 CHECK 위반 시 센티널 원가가 예외 문자열에는 있어도 **로그에는 지워지고** 제약 이름은 남는다(ADR-0018 ㉠ 경로의 PO 재현)"""
    po_id = raw_po("ISSUED")
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            PurchaseOrderLine(
                po_id=po_id,
                currency="USD",
                line_no=1,
                sku_id=create_sku(unique("FR")),
                sku_code="SKU-FR",
                sku_name_ko="실패 행",
                sku_kind="SINGLE",
                quantity=3,
                unit_cost=UNIT,
                line_cost=UNIT + 1,  # 수량×단가와 불일치 — CHECK line_cost_matches 위반
                price_basis="MASTER",
            )
        )
        uow.session.flush()
    assert str(UNIT) in str(exc.value)  # 서버 원문에는 있다(이 검사가 공회전이 아니라는 증거)
    with caplog.at_level(logging.ERROR):
        get_logger("test.leak").error("unhandled_exception", exc_info=exc.value)
    _assert_clean(caplog.text, where="제약 위반 로그")
    assert "ck_purchase_order_lines_line_cost_matches" in caplog.text


def test_channel_4_the_log_detector_would_notice_a_leak(caplog: pytest.LogCaptureFixture) -> None:
    """자기검사 — 같은 요청 흐름에서 요청 로그는 실제로 캡처된다(캡처가 죽어 있으면 위 '0회' 검사가 영원히 초록이다)"""
    with caplog.at_level(logging.DEBUG), logged_in(RoleCode.VIEWER) as viewer:
        viewer.get(f"{PO}/424242")
    assert "424242" in caplog.text
    with pytest.raises(AssertionError):
        _assert_clean(f"x {SENTINELS[0]} y", where="합성 유출")


# ── 채널 5: audit_log ───────────────────────────────────────────────────────────


def test_channel_5_the_audit_log_holds_no_po_rows_and_no_cost(seeded: dict[str, Any]) -> None:
    """⑤ 전표 생성·전이·편집은 audit_log에 기록하지 않는다(상태이력+아웃박스가 정본 — X-24) — 생성·전이·메타 뒤에도 PO 관련 audit 행 0건, 전체 audit detail에 센티널 0회"""
    po = seeded["pos"][1]
    with logged_in(RoleCode.TRADE) as trade:
        assert (
            trade.patch(
                f"{PO}/{po['id']}/meta", json={"version": po["version"], "internal_note": "메모"}
            ).status_code
            == 200
        )
        version = po["version"] + 1
        assert (
            trade.post(
                f"{PO}/{po['id']}/transitions",
                json={
                    "to": "SUPPLIER_CONFIRMED",
                    "version": version,
                    "oc_received_on": po["doc_date"],
                },
                headers=idem(),
            ).status_code
            == 200
        )
    assert (
        _rows("SELECT count(*) FROM audit_log WHERE entity_type LIKE 'purchase_order%'")[0][0] == 0
    )
    blob = json.dumps([str(r) for r in _rows("SELECT detail::text FROM audit_log")])
    _assert_clean(blob, where="audit_log.detail")


# ── 채널 6: outbox 이벤트·알림·브리핑 ─────────────────────────────────────────────


def test_channel_6_events_alerts_and_briefings_carry_no_cost(seeded: dict[str, Any]) -> None:
    """⑥ outbox payload는 화이트리스트 키뿐이라 원가·통화가 없고, 이 이벤트를 구독하는 규칙으로 디스패치한 **알림 제목·본문**에도 센티널이 없다 — 데일리 브리핑도 마찬가지"""
    viewer_id = create_user(f"{unique('alertv')}@example.com", roles=(RoleCode.VIEWER,))
    with unit_of_work() as uow:
        for event_type, code in (
            ("purchase_orders.purchase_order.created", "PO-CREATED"),
            ("purchase_orders.purchase_order.status_changed", "PO-CHANGED"),
        ):
            uow.session.add(
                AlertRule(
                    code=code,
                    name_ko=f"발주서 사건 {code}",
                    event_type=event_type,
                    recipient_user_id=viewer_id,
                )
            )
    po = seeded["pos"][0]
    with logged_in(RoleCode.TRADE) as trade:
        assert (
            trade.post(
                f"{PO}/{po['id']}/transitions",
                json={"to": "CANCELLED", "version": po["version"], "reason": "알림 시험"},
                headers=idem(),
            ).status_code
            == 200
        )
    events = _rows(
        "SELECT event_type, payload::text FROM events WHERE event_type LIKE 'purchase_orders.%' ORDER BY id"
    )
    assert len(events) == 3  # 생성 2 + 전이 1
    for _type, payload in events:
        _assert_clean(payload, where="outbox payload")
        assert not {k for k in json.loads(payload) if COST_KEY.search(k)}
        assert "currency" not in payload and "total" not in payload and "알림 시험" not in payload
    counts = dispatcher.dispatch_pending()
    assert counts["published"] >= 3 and counts["alerts"] >= 3
    with unit_of_work() as uow:
        # 담당자가 있는 사건은 담당자에게만 간다(배타 라우팅) — 수신자와 무관하게 이 사건으로 생긴 알림 전부를 본다.
        alerts = uow.session.query(Alert).filter(Alert.entity_type == "purchase_orders").all()
        texts = [f"{a.title}\n{a.body}" for a in alerts]
    assert len(texts) >= 3
    _assert_clean("\n".join(texts), where="알림 제목·본문")
    from app.modules.deadlines.service import send_daily_briefing

    send_daily_briefing()
    blob = json.dumps([str(r) for r in _rows("SELECT title, body FROM alerts")])
    _assert_clean(blob, where="알림함(브리핑 포함)")


# ── 채널 7: idempotency_keys.response_body ──────────────────────────────────────


def test_channel_7_the_idempotency_store_is_actor_scoped_and_holds_cost_only_for_the_creator() -> (
    None
):
    """⑦ 생성 응답(Full)은 생성자 스코프(actor×endpoint×key)로 24시간 저장된다 — **원가가 at-rest로 남는 것은 ADR-0057의 수용 사실**이다. 스코프를 고정한다: 저장 행의 주체는 생성자뿐이고,
    다른 사용자가 같은 키·본문으로 요청해도 재생(저장 응답 열람)이 아니라 별개 요청이며, 요청 지문은 해시라 본문 원문이 없다. VIEWER는 생성도 재생도 못 한다"""
    supplier = create_supplier()
    sku = create_sku(unique("ID"))
    body = po_payload(
        supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": SENTINEL_UNIT_COST_TEXT}]
    )
    key = idem()
    with logged_in(RoleCode.TRADE) as trade:
        created = trade.post(PO, json=body, headers=key)
        assert created.status_code == 201
        trade_id = _rows("SELECT id FROM users ORDER BY id DESC LIMIT 1")[0][0]
    stored = _rows(
        "SELECT actor_user_id, endpoint, request_fingerprint, response_body::text FROM idempotency_keys WHERE endpoint LIKE '%purchase-orders%'"
    )
    assert len(stored) == 1
    actor_id, endpoint, fingerprint, response_body = stored[0]
    assert actor_id == trade_id and endpoint == "POST /api/v1/purchase-orders"
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)  # 지문은 sha256 — 요청 원문(원가)이 없다
    _assert_clean(fingerprint, where="request_fingerprint")
    assert (
        str(UNIT) in response_body
    )  # 수용 사실 고정 — 생성자 스코프의 Full 응답은 저장된다(청소 잡은 PR-16)
    with logged_in(RoleCode.ADMIN) as admin:
        other = admin.post(PO, json=body, headers=key)
        assert other.status_code == 201  # 재생이 아니라 새 요청(새 PO)
        assert other.json()["id"] != created.json()["id"]
    with logged_in(RoleCode.VIEWER) as viewer:
        assert viewer.post(PO, json=body, headers=key).status_code == 403
        assert viewer.get(PO).status_code == 200
    by_actor = _rows(
        "SELECT actor_user_id FROM idempotency_keys WHERE endpoint LIKE '%purchase-orders%' ORDER BY actor_user_id"
    )
    assert len({r[0] for r in by_actor}) == 2 and all(r[0] != 0 for r in by_actor)
    viewer_rows = _rows(
        "SELECT count(*) FROM idempotency_keys k JOIN user_roles ur ON ur.user_id = k.actor_user_id"
        " JOIN roles r ON r.id = ur.role_id WHERE r.code = 'VIEWER'"
    )
    assert viewer_rows[0][0] == 0  # 원가를 못 보는 역할의 키 행은 없다


def test_channel_7_replays_return_the_stored_body_only_to_the_same_actor(
    seeded: dict[str, Any],
) -> None:
    """⑦ 같은 actor·같은 키의 재전송만 저장 응답을 받는다 — 본문이 다르면 409(지문 불일치)이고 그 오류 본문에는 저장 응답이 섞이지 않는다"""
    supplier = create_supplier()
    sku = create_sku(unique("RP"))
    body = po_payload(
        supplier, [], lines=[{"sku_id": sku, "quantity": 1, "unit_cost": SENTINEL_UNIT_COST_TEXT}]
    )
    key = idem()
    with logged_in(RoleCode.TRADE) as trade:
        assert trade.post(PO, json=body, headers=key).status_code == 201
        conflict = trade.post(PO, json={**body, "internal_note": "다름"}, headers=key)
        assert conflict.status_code == 409
        _assert_clean(conflict.text, where="멱등 충돌 본문")


# ── 채널 8: documents 첨부 ──────────────────────────────────────────────────────


def test_channel_8_a_po_cannot_carry_attachments_so_there_is_no_side_door(
    seeded: dict[str, Any],
) -> None:
    """⑧ S3-1에는 PO 첨부가 없다(소비자 없는 owner_type 확장 금지) — 소유 유형 열거에 PO가 없고 API로 PO 소유 문서를 올리려 해도 거절된다. 후속이 PO 첨부를 더하면
    원가 가시성(VIEWER 403)을 소유 전표 권한과 함께 검사해야 한다(마스킹 원장 사전 등재)"""
    from app.modules.documents.models import DOCUMENT_OWNER_TYPES

    assert not {
        t for t in DOCUMENT_OWNER_TYPES if "PURCHASE" in t or t in ("PO", "PURCHASE_ORDERS")
    }
    before = _rows("SELECT count(*) FROM documents")[0][0]
    for owner_type in ("PURCHASE_ORDER", "PO", "purchase_orders"):
        with logged_in(RoleCode.TRADE) as trade:
            response = trade.post(
                "/api/v1/documents/files",
                files={"file": ("po.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
                data={
                    "owner_type": owner_type,
                    "owner_id": str(seeded["pos"][0]["id"]),
                    "document_type": "MSDS",
                },
                headers=idem(),
            )
        assert response.status_code in (400, 422), (owner_type, response.status_code, response.text)
    assert _rows("SELECT count(*) FROM documents")[0][0] == before


# ── 채널 9: 집계·보드·문서 흐름·다른 전표 ──────────────────────────────────────────


def test_channel_9_no_other_document_response_carries_cost_margin_or_purchase_keys() -> None:
    """⑨ QT·PI·SO·문서 흐름 응답의 **모든 키**(재귀)에 cost·margin·purchase·landed 계열이 없다 — 어느 역할에게도(마진은 S3-4·S6-2 몫). PO는 문서 흐름(판매 사슬) 밖이라 422"""
    banned = re.compile(r"cost|margin|purchase|landed", re.IGNORECASE)
    with logged_in(RoleCode.TRADE) as trade:
        issued_qt, issued_pi = issued_pi_chain(trade)
        qt = trade.get(f"/api/v1/quotations/{issued_qt['id']}").json()
        pi = trade.get(f"/api/v1/proforma-invoices/{issued_pi['id']}").json()
        flow = trade.get(f"/api/v1/document-flow/PROFORMA_INVOICE/{issued_pi['id']}").json()
        listings = [
            trade.get(p).json()
            for p in ("/api/v1/quotations", "/api/v1/proforma-invoices", "/api/v1/sales-orders")
        ]
        po = create_po_via_api(trade)
        assert trade.get(f"/api/v1/document-flow/PURCHASE_ORDER/{po['id']}").status_code == 422
    for label, payload in (("QT", qt), ("PI", pi), ("흐름", flow), ("목록", listings)):
        offenders = {k for k in _scan_keys(payload) if banned.search(k)}
        assert offenders == set(), (label, offenders)


# ── 채널 합류: 모든 채널을 센티널 하나로 한 번에 순회(GC-G2) ─────────────────────────────


def test_gc_g2_one_sweep_over_every_viewer_visible_surface(seeded: dict[str, Any]) -> None:
    """GC-G2 — VIEWER가 볼 수 있는 모든 표면(상세·목록·라인·상태이력·CSV·에러)을 순회한 응답 전체 문자열에 센티널 원가가 **0회**다"""
    with logged_in(RoleCode.VIEWER) as viewer:
        surfaces = [viewer.get(PO), viewer.get(f"{PO}/export.csv")]
        for po in seeded["pos"]:
            surfaces += [viewer.get(f"{PO}/{po['id']}"), viewer.get(f"{PO}/{po['id']}/status-log")]
        surfaces += [
            viewer.get(f"{PO}/999999"),
            viewer.get(PO, params={"sort": "unit_cost"}),
            viewer.get(PO, params={"q": SENTINELS[0]}),
        ]
    assert all(r.status_code in (200, 404, 422) for r in surfaces)
    for response in surfaces:
        _assert_clean(response.text, where=f"{response.request.url}")
    # 공회전 방지 — 같은 표면을 원가 역할이 보면 센티널이 실제로 나온다
    with logged_in(RoleCode.LOGISTICS) as logistics:
        assert str(UNIT * 3) in logistics.get(f"{PO}/{seeded['pos'][1]['id']}").text
        assert SENTINEL_UNIT_COST_TEXT in logistics.get(f"{PO}/{seeded['pos'][1]['id']}").text


def test_the_sentinels_are_all_distinct() -> None:
    """(공회전 방지) 센티널이 서로 다른 값이다 — 같은 값이면 어느 하나가 새도 구분하지 못한다"""
    assert len(set(SENTINELS)) == len(SENTINELS)
