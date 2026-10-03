"""F·G·A·K. 오더 인테이크 CSV 입구 — 양식 왕복·파싱 경계·파일 전체 원자·파일 해시 멱등·중복 PO (S3-1 PR-14a / ADR-0072 / design-D D2).

핵심 계약: **파일 전체 원자**(한 행이라도 오류면 인테이크 0건 — 모든 오류를 행·열·사유로 한 번에 보고), **추측 변환 금지**(모호한 날짜·소수 단가·지수 표기·전각·불가시 문자는 전부 행 오류),
**파일 해시 멱등**(같은 파일은 PENDING 인테이크를 두 번 만들지 않는다), 품번 미매핑은 오류가 아니다(`sku_id NULL`로 착지). 테스트 데이터는 전부 익명 합성이다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.money import CURRENCY_MINOR_UNITS
from app.modules.identity.models import RoleCode
from app.modules.imports import service as imports_service
from app.modules.order_intake import csv_template
from tests.factories.intake import (
    IMPORT_CSV,
    INTAKES,
    TEMPLATE_CSV,
    code_of,
    confirm,
    csv_bytes,
    csv_header,
    csv_row,
    error_rows,
    future,
    get,
    land,
    past,
    rows,
    scalar,
    upload_csv,
    world,
)
from tests.factories.trade import (
    idem,
    logged_in,
    map_buyer_item_code,
    unique,
)
from tests.support.factories import create_market, create_partner

pytestmark = pytest.mark.group_f

INVALID_ROWS = "ORDER_INTAKE.FILE.INVALID_ROWS"
FILE_DUP = "ORDER_INTAKE.FILE.DUPLICATE"
DUP_PO = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _count(table: str = "order_intakes") -> int:
    return int(scalar(f"SELECT count(*) FROM {table}"))


def _errors_of(response: Any, *, column: str | None = None) -> list[dict[str, Any]]:
    found = error_rows(response)
    return [e for e in found if column is None or e["column"] == column]


# ── 양식 ─────────────────────────────────────────────────────────────────────


def test_template_is_the_nine_column_header_only_with_a_bom_for_every_role() -> None:
    """양식 다운로드 — UTF-8 BOM·헤더 9열 완전 일치·데이터/안내 행 없음·CRLF. 전 역할이 받는다(헤더뿐이라 민감 값 없음). 비인증은 401"""
    for role in RoleCode:
        with logged_in(role) as client:
            response = client.get(TEMPLATE_CSV)
            assert response.status_code == 200, (role, response.text)
            assert response.headers["content-type"].startswith("text/csv")
            assert "attachment" in response.headers["content-disposition"]
            body = response.content
            assert body.startswith(b"\xef\xbb\xbf")  # BOM — 엑셀이 한글을 읽는다
            lines = body.decode("utf-8-sig").split("\r\n")
            assert lines[0] == ",".join(csv_template.CSV_HEADER) and lines[1:] == [""]
    assert len(csv_template.CSV_HEADER) == 9
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get(TEMPLATE_CSV).status_code == 401


def test_the_downloaded_template_round_trips_through_the_upload(trade: Any) -> None:
    """양식 왕복 — 내려받은 양식 바이트에 행만 붙여 그대로 올리면 접수된다(다운로드 헤더 상수 = 업로드 헤더 상수)"""
    w = world()
    template = trade.get(TEMPLATE_CSV).content
    row = ",".join(csv_row(w, po=unique("PO"))) + "\r\n"
    response = upload_csv(trade, template + row.encode("utf-8"))
    assert response.status_code == 201, response.text
    assert response.json()["group_count"] == 1


# ── 착지 ─────────────────────────────────────────────────────────────────────


def test_rows_group_by_po_into_one_intake_each_even_when_not_contiguous(trade: Any) -> None:
    """3 PO × 여러 라인 → 인테이크 3건(비연속 행도 한 그룹) — 전부 PENDING·CSV 출처·sha256·그룹 키·파일명·엑셀 행번호·불변 원본(원본 셀 문자열)"""
    w = world(lines=3)
    c = w["codes"]
    file_rows = [
        csv_row(w, po="PO-A", code=c[0], qty="1,000"),
        csv_row(w, po="PO-B", code=c[0]),
        csv_row(w, po="PO-A", code=c[1], price="12.5"),
        csv_row(w, po="PO-C", code=c[2]),
        csv_row(w, po="PO-B", code=c[1]),
        csv_row(w, po="PO-A", code=c[2]),
    ]
    response = upload_csv(trade, csv_bytes(file_rows), filename="바이어 발주.csv")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["row_count"], body["group_count"], body["line_count"]) == (6, 3, 6)
    assert body["original_filename"] == "바이어 발주.csv" and len(body["source_sha256"]) == 64
    by_po = {i["buyer_po_no"]: i for i in body["intakes"]}
    assert {k: v["line_count"] for k, v in by_po.items()} == {"PO-A": 3, "PO-B": 2, "PO-C": 1}
    assert [i["first_row_no"] for i in body["intakes"]] == [2, 3, 5]  # 파일 순서

    detail = get(trade, by_po["PO-A"]["id"])
    assert detail["status"] == "PENDING" and detail["source_kind"] == "CSV"
    assert [ln["source_row_no"] for ln in detail["lines"]] == [2, 4, 7]
    assert [ln["quantity"] for ln in detail["lines"]] == [1000, 5, 5]
    assert [ln["unit_price_amount"] for ln in detail["lines"]] == [1000, 1250, 1000]
    assert all(ln["mapping_state"] == "MAPPED" for ln in detail["lines"])
    original = detail["original"]
    assert original["kind"] == "CSV" and original["parser_version"] == csv_template.PARSER_VERSION
    assert original["file"] == {
        "original_filename": "바이어 발주.csv",
        "sha256": body["source_sha256"],
    }
    assert original["header"]["buyer_code"] == w["buyer_code"]
    assert [ln["quantity"] for ln in original["lines"]] == ["1,000", "5", "5"]  # 원본 셀 그대로
    assert [ln["row_no"] for ln in original["lines"]] == [2, 4, 7]
    stored = rows(
        "SELECT source_sha256, source_group_key, original_filename, source_kind FROM order_intakes WHERE id = :i",
        i=by_po["PO-A"]["id"],
    )[0]
    assert stored[0] == body["source_sha256"] and stored[3] == "CSV"
    assert stored[1] == f"{w['buyer']}|PO-A" and stored[2] == "바이어 발주.csv"
    events = scalar(
        "SELECT count(*) FROM events WHERE event_type = 'order_intakes.order_intake.created'"
    )
    assert events == 3  # 13a 통로 재사용 — 인테이크당 created 1건


def test_unmapped_item_codes_land_with_null_sku_and_are_counted(trade: Any) -> None:
    """품번 미매핑은 오류가 아니다 — sku_id NULL로 착지하고 응답이 미매핑 라인 수를 알린다(검토 화면이 등록을 유도). 대소문자 다른 품번도 자동 매칭하지 않는다"""
    w = world(lines=1)
    file_rows = [
        csv_row(w, po="PO-U", code=w["codes"][0]),
        csv_row(w, po="PO-U", code="NO-SUCH-CODE"),
        csv_row(w, po="PO-U", code=w["codes"][0].lower()),
    ]
    response = upload_csv(trade, csv_bytes(file_rows))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["unmapped_line_count"] == 2 and body["intakes"][0]["unmapped_line_count"] == 2
    detail = get(trade, body["intakes"][0]["id"])
    assert [ln["mapping_state"] for ln in detail["lines"]] == ["MAPPED", "UNMAPPED", "UNMAPPED"]
    assert [ln["sku_id"] is None for ln in detail["lines"]] == [False, True, True]


def test_two_buyers_may_use_the_same_po_number(trade: Any) -> None:
    """같은 PO번호라도 바이어가 다르면 별개 그룹·별개 인테이크다(그룹 키에 바이어 포함)"""
    w1, w2 = world(), world()
    file_rows = [csv_row(w1, po="PO-X"), csv_row(w2, po="PO-X", code=w2["codes"][0])]
    response = upload_csv(trade, csv_bytes(file_rows))
    assert response.status_code == 201, response.text
    assert response.json()["group_count"] == 2
    assert scalar("SELECT count(DISTINCT buyer_partner_id) FROM order_intakes") == 2


def test_optional_dates_may_be_blank_and_currency_and_market_are_uppercased(trade: Any) -> None:
    """PO일자·요청납기일은 빈 칸 허용(없음) / 통화·시장 코드는 소문자 입력을 대문자화한다 — 코드의 대소문자는 의미 없다"""
    w = world()
    row = csv_row(w, po="PO-D", po_date="", delivery="", currency="usd", market="us")
    response = upload_csv(trade, csv_bytes([row]))
    assert response.status_code == 201, response.text
    detail = get(trade, response.json()["intakes"][0]["id"])
    assert detail["currency"] == "USD" and detail["dest_market_code"] == "US"
    assert detail["buyer_po_date"] is None and detail["lines"][0]["requested_delivery_date"] is None


def test_excel_formula_escape_is_reversed_only_through_the_shared_channel(trade: Any) -> None:
    """수식 이스케이프('=CMD() → =CMD())는 내보내기 통로의 역변환 그대로 — 품번·PO번호(문자열 열)에만 적용. 값은 수식으로 해석되지 않고 문자 그대로 저장된다"""
    w = world()
    row = csv_row(w, po="'=PO-F", code="'=CMD()")
    response = upload_csv(trade, csv_bytes([row]))
    assert response.status_code == 201, response.text
    detail = get(trade, response.json()["intakes"][0]["id"])
    assert detail["buyer_po_no"] == "=PO-F"
    assert detail["lines"][0]["buyer_item_code"] == "=CMD()"
    assert (
        detail["lines"][0]["mapping_state"] == "UNMAPPED"
    )  # 수식 문자열은 어떤 매핑과도 일치하지 않는다
    # 숫자 열(수량·단가)은 역변환 비대상 — 음수는 '텍스트'가 아니라 거부 대상이다
    bad = csv_row(w, po="PO-G", price="'-5")
    assert upload_csv(trade, csv_bytes([bad])).status_code == 422


# ── 인코딩·헤더·구조 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-8", "cp949"])
def test_utf8_bom_plain_utf8_and_cp949_are_all_read(trade: Any, encoding: str) -> None:
    """한글 CSV 인코딩 — UTF-8 BOM·BOM 없는 UTF-8·CP949(한국어 엑셀 'CSV 쉼표로 분리' 저장본)를 모두 읽는다. 한글 헤더·한글 품번 포함"""
    w = world()
    row = csv_row(w, po="PO-ENC", code="한글품번-가")
    response = upload_csv(trade, csv_bytes([row], encoding=encoding))
    assert response.status_code == 201, response.text
    detail = get(trade, response.json()["intakes"][0]["id"])
    assert detail["lines"][0]["buyer_item_code"] == "한글품번-가"


def test_header_must_match_exactly(trade: Any) -> None:
    """헤더 불일치 422 — 열 이름 변경·순서 바뀜·열 추가·열 누락·앞뒤 공백. 아무것도 등록되지 않는다"""
    w = world()
    row = csv_row(w, po="PO-H")
    header = csv_header()
    swapped = [header[1], header[0], *header[2:]]
    variants = {
        "renamed": [*header[:8], "납기"],
        "swapped": swapped,
        "extra": [*header, "비고"],
        "missing": header[:8],
        "padded": [" " + header[0], *header[1:]],
    }
    for name, bad_header in variants.items():
        response = upload_csv(trade, csv_bytes([row], header=bad_header))
        assert response.status_code == 422, (name, response.text)
        assert code_of(response) == "IMPORTS.FILE.HEADER_MISMATCH", name
    assert _count() == 0


def test_empty_header_only_and_garbage_files_are_refused(trade: Any) -> None:
    """빈 파일·헤더만 있는 파일·NUL 바이트·UTF-8도 CP949도 아닌 바이트는 422 — 깨진 글자를 읽지 않는다(fail-closed)"""
    assert code_of(upload_csv(trade, b"")) == "IMPORTS.FILE.EMPTY"
    assert code_of(upload_csv(trade, csv_bytes([]))) == "IMPORTS.FILE.EMPTY"
    assert (
        code_of(upload_csv(trade, b"\xef\xbb\xbf" + b"a\x00b")) == "IMPORTS.FILE.ENCODING_INVALID"
    )
    assert code_of(upload_csv(trade, b"\xff\xff\xff\xff,\xff")) == "IMPORTS.FILE.ENCODING_INVALID"
    assert _count() == 0


def test_blank_rows_are_skipped_but_row_numbers_stay_equal_to_excel(trade: Any) -> None:
    """빈 줄은 건너뛰되 오류 리포트의 행번호는 엑셀 행번호와 일치한다(헤더=1)"""
    w = world()
    good = csv_row(w, po="PO-B1")
    bad = csv_row(w, po="PO-B2", qty="abc")
    content = csv_bytes([good, [""] * 9, [""] * 9, bad])
    response = upload_csv(trade, content)
    assert response.status_code == 422
    assert [(e["row_no"], e["column"]) for e in error_rows(response)] == [(5, "수량")]


def test_a_row_with_the_wrong_number_of_cells_is_reported(trade: Any) -> None:
    """열 개수가 다른 행은 행 구조 오류로 보고된다(다른 행의 오류와 함께)"""
    w = world()
    content = csv_bytes([csv_row(w, po="PO-S1"), csv_row(w, po="PO-S2", qty="x")[:-2]])
    response = upload_csv(trade, content)
    assert response.status_code == 422
    codes = {(e["row_no"], e["code"]) for e in error_rows(response)}
    assert (3, "ROW_STRUCTURE") in codes


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("po.xlsx", b"PK\x03\x04" + b"0" * 50, "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT"),
        (
            "po.xls",
            bytes.fromhex("D0CF11E0A1B11AE1") + b"0" * 50,
            "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT",
        ),
        (
            "po.csv",
            b"PK\x03\x04" + b"0" * 50,
            "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT",
        ),  # 확장자 위장
        (
            "po.csv",
            bytes.fromhex("D0CF11E0A1B11AE1") + b"0" * 50,
            "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT",
        ),
        ("po.XLSX", b"x,y\n1,2\n", "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT"),
        ("po.txt", b"x,y\n1,2\n", "IMPORTS.FILE.TYPE_NOT_ALLOWED"),
        ("po", b"x,y\n1,2\n", "IMPORTS.FILE.TYPE_NOT_ALLOWED"),
    ],
)
def test_excel_and_non_csv_files_are_refused(
    trade: Any, filename: str, content: bytes, code: str
) -> None:
    """.xlsx·.xls 미수용(신규 의존 0) — 확장자가 .csv여도 엑셀 바이트 시그니처면 전용 422, 그 밖의 비-CSV는 형식 불허 422"""
    response = upload_csv(trade, content, filename=filename)
    assert response.status_code == 422, response.text
    assert code_of(response) == code
    assert _count() == 0


# ── 셀 검증 경계 (F) ───────────────────────────────────────────────────────────


def _one_row_error(trade: Any, w: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    response = upload_csv(trade, csv_bytes([csv_row(w, po=unique("PO"), **kwargs)]))
    assert response.status_code == 422, (kwargs, response.status_code, response.text)
    assert code_of(response) == INVALID_ROWS
    return error_rows(response)


@pytest.mark.parametrize(
    "value",
    [
        "10/5/2026",
        "46000",
        "2026-13-01",
        "2026/10/05",
        "26-10-05",
        "2026-1-5",
        "2026-02-30",
        "２０２６-０１-０１",
        "2026-01-01 00:00",
        "1999-12-31",
    ],
)
def test_po_date_accepts_only_iso_and_never_guesses(trade: Any, value: str) -> None:
    """PO일자 — ISO YYYY-MM-DD만. 엑셀 일련번호·`10/5/2026`·`2026/10/05`·2자리 연도·존재하지 않는 날짜·전각 숫자·2000년 이전은 전부 행 오류(메시지에 올바른 형식 예시)"""
    errors = _one_row_error(trade, world(), po_date=value)
    assert [(e["row_no"], e["column"]) for e in errors] == [(2, "PO일자")]
    assert errors[0]["code"] in {
        "INVALID_DATE",
        "INVALID_FORMAT",
        "OUT_OF_RANGE",
        "INVISIBLE_CHAR",
        "TOO_LONG",
    }
    assert _count() == 0


def test_date_semantics_future_po_date_and_past_delivery_are_row_errors(trade: Any) -> None:
    """PO일자는 미래일 수 없고 요청납기일은 오늘 이전일 수 없다(KST) — 행 오류로 보고되고(착지 중 500·전체 422가 아님) 오늘 납기는 통과한다"""
    w = world()
    assert [e["column"] for e in _one_row_error(trade, w, po_date=future(3))] == ["PO일자"]
    assert [e["column"] for e in _one_row_error(trade, w, delivery=past(1))] == ["요청납기일"]
    assert [e["column"] for e in _one_row_error(trade, w, delivery="2026/10/05")] == ["요청납기일"]
    from app.core.time import today_kst

    ok = csv_row(w, po="PO-T", delivery=today_kst().isoformat(), po_date=today_kst().isoformat())
    assert upload_csv(trade, csv_bytes([ok])).status_code == 201


@pytest.mark.parametrize(
    "value",
    [
        "1.5",
        "1,00",
        "1e3",
        "１０",
        "0",
        "-1",
        "100000000",
        "1,0000",
        "1 000",
        "abc",
        "10.01",
        "+5",
        "",
    ],
)
def test_quantity_rejects_everything_but_whole_numbers(trade: Any, value: str) -> None:
    """수량 — 소수·콤마 위치 오류·지수 표기·전각·0·음수·1억 이상·공백 구분·빈 칸은 행 오류"""
    errors = _one_row_error(trade, world(), qty=value)
    assert [(e["row_no"], e["column"]) for e in errors] == [(2, "수량")]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10", 10),
        ("10.0", 10),
        ("10.00", 10),
        ("1,000", 1000),
        ("1,000.0", 1000),
        ("007", 7),
        (" 5 ", 5),
        ("99,999,999", 99999999),
    ],
)
def test_quantity_accepts_whole_numbers_with_thousands_commas(
    trade: Any, value: str, expected: int
) -> None:
    """수량 허용 — `10`·`10.0`·`1,000`(천단위 위치일 때만)·앞뒤 공백·상한 경계 99,999,999"""
    w = world()
    response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-Q", qty=value)]))
    assert response.status_code == 201, response.text
    assert get(trade, response.json()["intakes"][0]["id"])["lines"][0]["quantity"] == expected


@pytest.mark.parametrize(
    ("currency", "value"),
    [
        ("KRW", "1234.5"),
        ("USD", "12.345"),
        ("USD", "0"),
        ("USD", "0.00"),
        ("USD", "-1"),
        ("USD", "$5"),
        ("USD", "1e2"),
        ("USD", "１２"),
        ("USD", "12,34"),
        ("USD", "1,00.00"),
        ("USD", ""),
        ("KRW", "1.0.0"),
        ("USD", "10 00"),
    ],
)
def test_unit_price_is_never_rounded_and_never_guessed(
    trade: Any, currency: str, value: str
) -> None:
    """단가 — 통화 최소단위 자릿수 초과(KRW 1234.5·USD 12.345)·0·음수·통화기호·지수·전각·유럽식 콤마·잘못된 천단위는 행 오류. 반올림 없음"""
    w = world(currency=currency)
    errors = _one_row_error(trade, w, price=value)
    assert [(e["row_no"], e["column"]) for e in errors] == [(2, "단가")]


@pytest.mark.parametrize(
    ("currency", "value", "minor"),
    [
        ("USD", "12.30", 1230),
        ("USD", "1,234.50", 123450),
        ("USD", "5", 500),
        ("USD", "0.01", 1),
        ("KRW", "1234", 1234),
        ("KRW", "1,234.0", 1234),
        ("USD", "12.3", 1230),
    ],
)
def test_unit_price_converts_to_integer_minor_units(
    trade: Any, currency: str, value: str, minor: int
) -> None:
    """단가 허용 — 소수 표기를 통화 최소단위 이내에서만 정수 최소단위로(USD 12.30=1230, KRW 1234=1234, 천단위 콤마 허용)"""
    w = world(currency=currency)
    response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-P", price=value)]))
    assert response.status_code == 201, response.text
    line = get(trade, response.json()["intakes"][0]["id"])["lines"][0]
    assert line["unit_price_amount"] == minor


def test_line_amount_over_the_safe_range_is_a_row_error(trade: Any) -> None:
    """수량 × 단가가 정수 상한(2^53−1)을 넘으면 착지 중 예외가 아니라 그 행의 오류다"""
    errors = _one_row_error(
        trade,
        world(currency="KRW"),
        qty="99,999,999",
        price="999,999,999,999,999",
        delivery=future(),
    )
    assert [(e["row_no"], e["column"]) for e in errors] == [(2, "단가")]


def test_a_po_total_over_the_safe_range_is_reported_on_the_po_first_row(trade: Any) -> None:
    """라인마다는 범위 안이어도 PO 합계가 정수 상한(2^53−1)을 넘으면 착지 중 전체 422가 아니라 그 PO 첫 행의 오류로 보고된다(다른 PO 오류와 함께)"""
    w = world(lines=2, currency="KRW")
    c = w["codes"]
    content = csv_bytes(
        [
            csv_row(w, po="PO-BIG", code=c[0], qty="99,999,999", price="50,000,000"),
            csv_row(w, po="PO-BIG", code=c[1], qty="99,999,999", price="50,000,000"),
            csv_row(w, po="PO-OTHER", code=c[0], qty="x"),
        ]
    )
    response = upload_csv(trade, content)
    assert response.status_code == 422 and code_of(response) == INVALID_ROWS
    assert [(e["row_no"], e["column"], e["code"]) for e in error_rows(response)] == [
        (2, "단가", "OUT_OF_RANGE"),
        (4, "수량", "INVALID_FORMAT"),
    ]
    assert _count() == 0
    one_line = csv_bytes([csv_row(w, po="PO-ONE", code=c[0], qty="99,999,999", price="50,000,000")])
    assert upload_csv(trade, one_line).status_code == 201  # 경계 안쪽은 통과


@pytest.mark.parametrize("column", ["buyer", "po", "item"])
@pytest.mark.parametrize("value", ["1.23E+10", "1E5", "9.99e-3", "12345678901234E+2"])
def test_excel_exponent_pollution_in_code_columns_is_a_row_error(
    trade: Any, column: str, value: str
) -> None:
    """코드 열(바이어코드·PO번호·품번)의 지수 표기 — 엑셀이 숫자로 오인해 원본이 손실된 값이라 행 오류(코드 열은 텍스트 서식 안내)"""
    w = world()
    kwargs: dict[str, Any] = {
        "buyer": {"buyer_code": value},
        "po": {"po": value},
        "item": {"code": value},
    }[column]
    kwargs.setdefault("po", unique("PO"))
    response = upload_csv(trade, csv_bytes([csv_row(w, **kwargs)]))
    assert response.status_code == 422
    expected_column = {"buyer": "바이어코드", "po": "바이어PO번호", "item": "바이어품번"}[column]
    found = [e for e in error_rows(response) if e["column"] == expected_column]
    assert found and found[0]["code"] == "EXCEL_EXPONENT"


@pytest.mark.parametrize(
    "bad",
    [
        "A\u200bB",
        "A\ufeffB",
        "PO\u00a0X",
        "A\u3164B",
        "A\u2800B",
        "A\x07B",
        "LINE1\nLINE2",
        "A\u2028B",
        "A\x1fB",
        "A\ufe0fB",
        "A\u034fB",
        "A\u3000B",
    ],
)
def test_invisible_and_control_characters_inside_a_value_are_row_errors(
    trade: Any, bad: str
) -> None:
    """값 안의 제로폭·BOM·NBSP·한글 채움·점자 빈칸·제어 문자·줄바꿈은 행 오류(눈에 같은 코드가 다른 값이 되는 우회 차단)"""
    w = world()
    for kwargs in ({"code": bad}, {"po": bad}):
        kwargs.setdefault("po", unique("PO"))
        response = upload_csv(trade, csv_bytes([csv_row(w, **kwargs)]))
        assert response.status_code == 422, (kwargs, response.text)
        assert any(e["code"] == "INVISIBLE_CHAR" for e in error_rows(response)), kwargs


def test_codes_are_kept_verbatim_only_the_ends_are_trimmed(trade: Any) -> None:
    """바이어코드·품번·PO번호는 원문 유지(대소문자 변환 없음) — 앞뒤 공백만 걷는다. PO 키는 공유 정규화 통로가 만든다"""
    w = world()
    row = csv_row(w, po="  po-1/ab  ", code=f" {w['codes'][0]} ", buyer_code=f" {w['buyer_code']} ")
    response = upload_csv(trade, csv_bytes([row]))
    assert response.status_code == 201, response.text
    detail = get(trade, response.json()["intakes"][0]["id"])
    assert detail["buyer_po_no"] == "po-1/ab"
    assert detail["lines"][0]["mapping_state"] == "MAPPED"
    assert scalar("SELECT buyer_po_no_key FROM order_intakes") == "PO-1/AB"


def test_unregistered_currency_and_market_are_row_errors(trade: Any) -> None:
    """통화·시장 미등록은 행 오류 — 통화는 최소단위 표에 있어야 하고 시장은 마스터에 등록돼 있어야 한다"""
    w = world()
    assert [e["code"] for e in _one_row_error(trade, w, currency="ZZZ")] == ["UNKNOWN_CURRENCY"]
    assert [e["code"] for e in _one_row_error(trade, w, currency="US")] == ["UNKNOWN_CURRENCY"]
    market_errors = _one_row_error(trade, w, market="ZZ")
    assert [(e["column"], e["code"]) for e in market_errors] == [
        ("목적지시장코드", "MARKET_NOT_REGISTERED")
    ]
    assert [e["column"] for e in _one_row_error(trade, w, market="USA")] == ["목적지시장코드"]
    assert "USD" in CURRENCY_MINOR_UNITS and _count() == 0


# ── 바이어 ───────────────────────────────────────────────────────────────────


def test_buyer_code_must_match_exactly_with_a_case_hint_and_never_auto_matches(trade: Any) -> None:
    """바이어코드는 정확 일치 — 대소문자만 다르면 오류+'혹시 …?' 힌트(자동 매칭 없음), 미등록 코드는 오류, 바이어 유형이 아닌 거래처도 오류"""
    w = world()
    lowered = w["buyer_code"].lower()
    errors = _one_row_error(trade, w, buyer_code=lowered)
    assert (
        errors[0]["code"] == "BUYER_NOT_REGISTERED" and w["buyer_code"] in errors[0]["message_ko"]
    )
    assert _one_row_error(trade, w, buyer_code="NO-SUCH-BUYER")[0]["code"] == "BUYER_NOT_REGISTERED"
    supplier = unique("SUP")
    create_partner(supplier, name_ko="합성 공급사", types=("SUPPLIER",))
    assert _one_row_error(trade, w, buyer_code=supplier)[0]["code"] == "NOT_A_BUYER"
    assert _count() == 0


# ── 그룹 ─────────────────────────────────────────────────────────────────────


def test_a_header_value_that_differs_inside_one_po_is_that_rows_error(trade: Any) -> None:
    """한 PO 안에서 앞 5열(헤더 값)이 다르면 그 행·그 열의 오류(첫 행 기준 행번호 안내) — PO번호 표기·통화·시장·PO일자가 각각 잡힌다. 대소문자만 다른 통화·시장은 같은 값"""
    w = world(lines=4)
    c = w["codes"]
    create_market("KR")
    content = csv_bytes(
        [
            csv_row(w, po="PO-M", code=c[0]),
            csv_row(w, po="PO-M", code=c[1], po_date=past(5)),
            csv_row(w, po="PO-M", code=c[2], market="KR"),
            csv_row(w, po="PO-M", code=c[3], currency="usd", market="us"),  # 같은 값 — 통과
        ]
    )
    response = upload_csv(trade, content)
    assert response.status_code == 422
    assert [(e["row_no"], e["column"], e["code"]) for e in error_rows(response)] == [
        (3, "PO일자", "GROUP_HEADER_MISMATCH"),
        (4, "목적지시장코드", "GROUP_HEADER_MISMATCH"),
    ]
    assert "2행" in error_rows(response)[0]["message_ko"]
    # PO 표기만 달라도(키는 같음) 같은 PO의 서로 다른 헤더 값이다 — 추측으로 한쪽을 고르지 않는다
    for other in ("po-m", " PO- M"):
        mixed = csv_bytes([csv_row(w, po="PO-M"), csv_row(w, po=other, code=c[1])])
        got = upload_csv(trade, mixed)
        assert got.status_code == 422 and error_rows(got)[0]["column"] == "바이어PO번호", other
    assert _count() == 0


def test_the_same_item_code_twice_in_one_po_is_refused_and_so_is_the_same_sku_via_two_codes(
    trade: Any,
) -> None:
    """한 PO 안의 같은 품번 두 줄은 행 오류(수량을 합치게 한다). 서로 다른 품번이 같은 SKU로 매핑돼도 행 오류(수주는 SKU마다 1줄) — 다른 PO에서는 같은 품번이 허용된다"""
    w = world(lines=1)
    alias = unique("ALIAS")
    map_buyer_item_code(w["buyer"], w["sku_ids"][0], alias)
    c = w["codes"][0]
    dup = csv_bytes([csv_row(w, po="PO-DUP", code=c), csv_row(w, po="PO-DUP", code=c)])
    response = upload_csv(trade, dup)
    assert response.status_code == 422
    assert [(e["row_no"], e["code"]) for e in error_rows(response)] == [(3, "DUPLICATE_ITEM")]
    same_sku = csv_bytes([csv_row(w, po="PO-DUP", code=c), csv_row(w, po="PO-DUP", code=alias)])
    response = upload_csv(trade, same_sku)
    assert response.status_code == 422
    assert [(e["row_no"], e["code"]) for e in error_rows(response)] == [(3, "DUPLICATE_SKU")]
    ok = csv_bytes([csv_row(w, po="PO-1", code=c), csv_row(w, po="PO-2", code=c)])
    assert upload_csv(trade, ok).status_code == 201


def test_group_and_line_limits_pass_at_the_boundary_and_fail_one_over(
    trade: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """파일당 그룹 상한·그룹당 라인 상한 — 경계값 통과, +1 거부(그룹 초과는 TOO_MANY_GROUPS 422, 라인 초과는 그 행의 오류). 상수는 단일 출처 `csv_template`"""
    monkeypatch.setattr(csv_template, "MAX_GROUPS", 3)
    monkeypatch.setattr(csv_template, "MAX_GROUP_LINES", 2)
    w = world(lines=3)
    c = w["codes"]
    three_groups = csv_bytes([csv_row(w, po=f"PO-G{i}", code=c[0]) for i in range(3)])
    assert upload_csv(trade, three_groups).status_code == 201
    four_groups = csv_bytes([csv_row(w, po=f"PO-H{i}", code=c[0]) for i in range(4)])
    over = upload_csv(trade, four_groups)
    assert over.status_code == 422 and code_of(over) == "ORDER_INTAKE.FILE.TOO_MANY_GROUPS"
    assert over.json()["error"]["detail"] == {"groups": 4, "max_groups": 3}
    two_lines = csv_bytes([csv_row(w, po="PO-L", code=c[0]), csv_row(w, po="PO-L", code=c[1])])
    assert upload_csv(trade, two_lines).status_code == 201
    three_lines = csv_bytes([csv_row(w, po="PO-L2", code=code) for code in c])
    over_lines = upload_csv(trade, three_lines)
    assert over_lines.status_code == 422
    assert [(e["row_no"], e["code"]) for e in error_rows(over_lines)] == [(4, "GROUP_LINE_LIMIT")]
    assert _count() == 4  # 통과한 두 파일(3+1)만 — 거부된 파일은 0건


def test_the_real_group_limit_is_200_and_the_row_and_size_limits_are_enforced(trade: Any) -> None:
    """실제 상한 — 200 PO 통과(착지 200건)·201 PO는 422 TOO_MANY_GROUPS(착지 0). 행 50,001개는 422(검증 이전에 거부), 업로드 20MiB 초과는 413"""
    w = world(lines=1)
    c = w["codes"][0]
    ok = upload_csv(trade, csv_bytes([csv_row(w, po=f"PO-{i:04d}", code=c) for i in range(200)]))
    assert ok.status_code == 201 and ok.json()["group_count"] == 200, ok.text
    assert _count() == 200
    over = upload_csv(trade, csv_bytes([csv_row(w, po=f"PX-{i:04d}", code=c) for i in range(201)]))
    assert over.status_code == 422 and code_of(over) == "ORDER_INTAKE.FILE.TOO_MANY_GROUPS"
    assert _count() == 200
    assert imports_service.IMPORT_MAX_ROWS == 50_000
    too_many = upload_csv(trade, csv_bytes([csv_row(w, po="P", code=c)] * 50_001))
    assert too_many.status_code == 422 and "50,000" in too_many.text
    huge = upload_csv(trade, b"a" * (imports_service.MAX_UPLOAD_BYTES + 1))
    assert huge.status_code == 413 and code_of(huge) == "IMPORTS.FILE.TOO_LARGE"
    assert _count() == 200


# ── 파일 전체 원자 (A) ─────────────────────────────────────────────────────────


@pytest.mark.group_a
def test_one_bad_row_rejects_the_whole_file_and_reports_every_error_at_once(trade: Any) -> None:
    """파일 전체 원자 — 100 PO 중 마지막 PO의 오류 1행이면 착지 0건(인테이크·라인·이벤트 0, 멱등 키 미소비). 오류는 첫 오류에서 멈추지 않고 전부 보고된다"""
    w = world(lines=1)
    c = w["codes"][0]
    good = [csv_row(w, po=f"PO-{i:03d}", code=c) for i in range(99)]
    bad_last = csv_row(w, po="PO-099", code=c, qty="1.5")
    key = unique("atomic")
    response = upload_csv(trade, csv_bytes([*good, bad_last]), key=key)
    assert response.status_code == 422 and code_of(response) == INVALID_ROWS
    assert [(e["row_no"], e["column"]) for e in error_rows(response)] == [(101, "수량")]
    for table in ("order_intakes", "order_intake_lines"):
        assert _count(table) == 0, table
    assert scalar("SELECT count(*) FROM events WHERE event_type LIKE 'order_intakes.%'") == 0
    # 멱등 키는 소비되지 않았다 — 같은 키로 고친 파일을 올리면 착지한다
    fixed = upload_csv(trade, csv_bytes([*good, csv_row(w, po="PO-099", code=c)]), key=key)
    assert fixed.status_code == 201 and fixed.json()["group_count"] == 100


@pytest.mark.group_a
def test_all_kinds_of_errors_are_reported_together_sorted_by_row_and_column(trade: Any) -> None:
    """한 파일의 서로 다른 오류(날짜·수량·단가·시장·바이어·품번 지수·구조)가 한 번에 행·열 순으로 나온다 — 응답에 total_errors·omitted_errors 동봉"""
    w = world(lines=2)
    c = w["codes"]
    content = csv_bytes(
        [
            csv_row(w, po="P1", po_date="10/5/2026", qty="1.5"),
            csv_row(w, po="P2", price="12.345", market="ZZ"),
            csv_row(w, po="P3", buyer_code="NOPE"),
            csv_row(w, po="P4", code="1.5E+3", delivery=past(3)),
            csv_row(w, po="P5", code=c[1])[:-3],
        ]
    )
    response = upload_csv(trade, content)
    assert response.status_code == 422
    detail = response.json()["error"]["detail"]
    got = [(e["row_no"], e["column"], e["code"]) for e in detail["errors"]]
    assert got == [
        (2, "PO일자", "INVALID_DATE"),
        (2, "수량", "INVALID_FORMAT"),
        (3, "목적지시장코드", "MARKET_NOT_REGISTERED"),
        (3, "단가", "INVALID_FORMAT"),
        (4, "바이어코드", "BUYER_NOT_REGISTERED"),
        (5, "바이어품번", "EXCEL_EXPONENT"),
        (5, "요청납기일", "OUT_OF_RANGE"),
        (6, None, "ROW_STRUCTURE"),
    ]
    assert detail["total_errors"] == 8 and detail["omitted_errors"] == 0
    assert all(e["message_ko"] for e in detail["errors"]) and _count() == 0


@pytest.mark.group_a
def test_the_error_report_is_capped_and_counts_the_omitted(trade: Any) -> None:
    """오류가 많으면 최대 200건만 싣고 나머지는 개수로 알린다(응답 크기 상한)"""
    w = world()
    content = csv_bytes(
        [csv_row(w, po=f"PO-{i % 100}", code=f"C-{i}", qty="x") for i in range(250)]
    )
    response = upload_csv(trade, content)
    detail = response.json()["error"]["detail"]
    assert response.status_code == 422
    assert len(detail["errors"]) == 200 and detail["total_errors"] == 250
    assert detail["omitted_errors"] == 50 and detail["errors"][0]["row_no"] == 2


@pytest.mark.group_a
def test_a_failure_while_landing_a_later_group_rolls_back_the_earlier_groups(
    trade: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """착지 도중(세 번째 PO) 예외 — 앞서 착지한 두 PO와 이벤트·멱등 키까지 전부 롤백된다(그룹별 커밋이 아니다)"""
    from app.modules.order_intake import csv_import
    from app.modules.order_intake import service as intake_service

    w = world(lines=1)
    real = intake_service.register_intake
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(csv_import.intake_service, "register_intake", flaky)
    content = csv_bytes([csv_row(w, po=f"PO-{i}") for i in range(4)])
    key = unique("rollback")
    with pytest.raises(RuntimeError):  # TestClient가 서버 예외를 그대로 올린다(실서버에서는 500)
        upload_csv(trade, content, key=key)
    assert calls["n"] == 3
    assert _count() == 0 and _count("order_intake_lines") == 0
    assert scalar("SELECT count(*) FROM events WHERE event_type LIKE 'order_intakes.%'") == 0
    monkeypatch.setattr(csv_import.intake_service, "register_intake", real)
    again = upload_csv(trade, content, key=key)  # 키도 소비되지 않았다
    assert again.status_code == 201 and _count() == 4


# ── 중복 PO (A) ───────────────────────────────────────────────────────────────


@pytest.mark.group_a
def test_a_po_held_by_a_pending_intake_rejects_the_whole_file_with_the_13a_conflict(
    trade: Any,
) -> None:
    """DB 점유 중복 PO(검토 대기 인테이크) — 13a와 같은 409 DUPLICATE_BUYER_PO로 **파일 전체 거부**(점유 PO가 한 줄이어도 다른 PO도 0건). 오류 행·점유 문서가 detail에 있다"""
    w = world(lines=2)
    manual = land(w, po_no="PO-TAKEN")
    before = _count()
    content = csv_bytes(
        [
            csv_row(w, po="PO-FREE", code=w["codes"][0]),
            csv_row(w, po="po-taken", code=w["codes"][1]),  # 정규화 키가 같은 점유 PO(표기만 다름)
        ]
    )
    response = upload_csv(trade, content)
    assert response.status_code == 409 and code_of(response) == DUP_PO
    errors = error_rows(response)
    assert [(e["row_no"], e["column"], e["code"]) for e in errors] == [
        (3, "바이어PO번호", "DUPLICATE_BUYER_PO")
    ]
    assert str(manual["id"]) in errors[0]["message_ko"]
    assert _count() == before  # PO-FREE도 착지하지 않았다


@pytest.mark.group_a
def test_a_po_held_by_a_live_so_rejects_and_a_rejected_intake_frees_it(trade: Any) -> None:
    """비취소 SO가 점유한 PO는 409(SO 문서번호·상태 안내), 거부(REJECTED)된 인테이크의 PO는 CSV로 다시 올릴 수 있다 — REJECTED는 PO를 점유하지 않는다"""
    w = world(lines=1)
    first = land(w, po_no="PO-SO")
    assert confirm(trade, first).status_code == 201
    response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-SO")]))
    assert response.status_code == 409 and code_of(response) == DUP_PO
    assert "수주" in error_rows(response)[0]["message_ko"]

    second = land(w, po_no="PO-REJ")
    rejected = trade.post(
        f"{INTAKES}/{second['id']}/reject",
        json={"version": second["version"], "reason": "테스트 거부 사유"},
        headers=idem(),
    )
    assert rejected.status_code == 200, rejected.text
    again = upload_csv(trade, csv_bytes([csv_row(w, po="PO-REJ")]))
    assert again.status_code == 201, again.text


@pytest.mark.group_a
def test_a_duplicate_po_mixed_with_format_errors_is_one_422_report(trade: Any) -> None:
    """형식 오류와 중복 PO가 함께 있으면 422 한 번의 리포트에 둘 다 나온다(중복 PO만 있을 때만 409) — 사용자가 한 번에 고친다"""
    w = world(lines=2)
    land(w, po_no="PO-HELD")
    content = csv_bytes(
        [
            csv_row(w, po="PO-HELD", code=w["codes"][0]),
            csv_row(w, po="PO-NEW", code=w["codes"][1], qty="x"),
        ]
    )
    response = upload_csv(trade, content)
    assert response.status_code == 422 and code_of(response) == INVALID_ROWS
    assert {(e["row_no"], e["code"]) for e in error_rows(response)} == {
        (2, "DUPLICATE_BUYER_PO"),
        (3, "INVALID_FORMAT"),
    }


# ── 파일 해시 멱등 (G) ─────────────────────────────────────────────────────────


@pytest.mark.group_g
def test_the_same_file_twice_creates_one_set_and_the_second_is_a_409(trade: Any) -> None:
    """파일 해시 멱등 — 같은 파일(sha256)을 다른 키로 다시 올리면 409 FILE.DUPLICATE(기존 인테이크 id 목록)이고 인테이크 수는 그대로다. 내용이 같으면 파일명이 달라도 같은 파일이다"""
    w = world(lines=2)
    content = csv_bytes(
        [csv_row(w, po="PO-1", code=w["codes"][0]), csv_row(w, po="PO-2", code=w["codes"][1])]
    )
    first = upload_csv(trade, content, filename="a.csv")
    assert first.status_code == 201
    ids = sorted(i["id"] for i in first.json()["intakes"])
    for filename in ("a.csv", "다른 이름.csv"):
        second = upload_csv(trade, content, filename=filename)
        assert second.status_code == 409 and code_of(second) == FILE_DUP, second.text
        assert second.json()["error"]["detail"]["intake_ids"] == ids
    assert _count() == 2 and _count("order_intake_lines") == 2
    # 한 글자만 달라도 다른 파일 — 이번에는 PO 중복이 막는다(해시 멱등은 PO 중복의 대체물이 아니다)
    changed = upload_csv(trade, content + b"\r\n")
    assert changed.status_code in (409, 422), changed.text
    assert _count() == 2


@pytest.mark.group_g
def test_the_same_key_replays_the_first_result_and_a_different_file_under_that_key_conflicts(
    trade: Any,
) -> None:
    """멱등 키 — 같은 키·같은 파일은 최초 201 결과를 그대로 재생(인테이크 불변), 같은 키·다른 파일은 409 KEY_CONFLICT"""
    w = world(lines=2)
    content = csv_bytes([csv_row(w, po="PO-K", code=w["codes"][0])])
    key = unique("replay")
    first = upload_csv(trade, content, key=key)
    again = upload_csv(trade, content, key=key)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json() and _count() == 1
    other = csv_bytes([csv_row(w, po="PO-K2", code=w["codes"][1])])
    conflict = upload_csv(trade, other, key=key)
    assert conflict.status_code == 409 and code_of(conflict) == "COMMON.IDEMPOTENCY.KEY_CONFLICT"
    assert _count() == 1


@pytest.mark.group_g
def test_a_resolved_file_can_be_uploaded_again_and_converges_on_the_po_check(trade: Any) -> None:
    """확정·거부 후 재업로드는 허용되고 PO 중복 검사가 수렴시킨다 — 전부 거부하면 다시 올릴 수 있고, 확정(SO 점유)된 PO가 남아 있으면 그 PO의 409가 막는다"""
    w = world(lines=2)
    content = csv_bytes(
        [csv_row(w, po="PO-C1", code=w["codes"][0]), csv_row(w, po="PO-C2", code=w["codes"][1])]
    )
    first = upload_csv(trade, content)
    ids = [i["id"] for i in first.json()["intakes"]]
    assert upload_csv(trade, content).status_code == 409  # 아직 PENDING — 파일 중복
    one = get(trade, ids[0])
    assert confirm(trade, one).status_code == 201
    other = get(trade, ids[1])
    rejected = trade.post(
        f"{INTAKES}/{ids[1]}/reject",
        json={"version": other["version"], "reason": "테스트 거부 사유"},
        headers=idem(),
    )
    assert rejected.status_code == 200
    # 이제 PENDING이 없어 파일 중복은 아니고, 확정 건의 SO가 PO-C1을 점유 → PO 중복 409
    again = upload_csv(trade, content)
    assert again.status_code == 409 and code_of(again) == DUP_PO
    assert [e["row_no"] for e in error_rows(again)] == [2]


@pytest.mark.group_g
def test_csv_landing_is_pending_only_and_the_entrypoint_has_no_status_or_direct_construction() -> (
    None
):
    """DB 직행 불가 — CSV 입구 함수에 status 인자가 없고, 입구 모듈이 `OrderIntake`를 직접 만들지 않으며 착지는 `register_intake`(source_kind=CSV) 호출뿐이다"""
    import ast
    import inspect

    from app.modules.order_intake import csv_import, csv_parse

    params = inspect.signature(csv_import.import_csv).parameters
    assert not {"status", "sales_order_id", "source_kind"} & set(params)
    for module in (csv_import, csv_parse):
        tree = ast.parse(inspect.getsource(module))
        constructed = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "OrderIntake" not in constructed, module.__name__
    source = inspect.getsource(csv_import)
    assert "register_intake(" in source and "IntakeSourceKind.CSV" in source
    assert "apply_intake_transition" not in source and "confirm_intake" not in source


# ── 권한·입력 계약 (K) ─────────────────────────────────────────────────────────


@pytest.mark.group_k
def test_only_trade_and_admin_may_upload_and_the_key_is_required(trade: Any) -> None:
    """업로드는 무역·관리자만(나머지 3역할 403 — 파일 처리 이전), 멱등 키 없으면 400, 비인증 401. 거부된 요청은 아무것도 만들지 않는다"""
    w = world()
    content = csv_bytes([csv_row(w, po="PO-AUTH")])
    for role in (RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER):
        with logged_in(role) as client:
            assert upload_csv(client, content).status_code == 403, role
    with logged_in(RoleCode.ADMIN) as admin:
        assert upload_csv(admin, content).status_code == 201
    no_key = trade.post(IMPORT_CSV, files={"file": ("po.csv", csv_bytes([]), "text/csv")})
    assert no_key.status_code == 400 and code_of(no_key) == "COMMON.IDEMPOTENCY.KEY_REQUIRED"
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        assert upload_csv(anonymous, content).status_code == 401
    assert _count() == 1


@pytest.mark.group_k
def test_the_service_layer_rechecks_the_role_independently_of_the_route_gate() -> None:
    """서비스 층 이중 방어 — 라우트 게이트를 거치지 않고 무역·관리자가 아닌 행위자로 `import_csv`를 직접 부르면 파일을 읽기 전에 403"""
    import io

    from app.core.errors.exceptions import ForbiddenError
    from app.modules.identity.service import AuthenticatedUser
    from app.modules.order_intake import csv_import

    actor = AuthenticatedUser(
        id=1,
        email="x@example.com",
        display_name="x",
        roles=frozenset({RoleCode.VIEWER}),
        session_id=0,
    )
    stream = io.BytesIO(b"never read")
    with pytest.raises(ForbiddenError):
        csv_import.import_csv(actor=actor, idempotency_key="k", stream=stream, filename="a.csv")
    assert stream.tell() == 0


@pytest.mark.group_k
def test_new_error_codes_have_the_designed_statuses_and_messages() -> None:
    """FILE.* 코드 4종 — 3세그먼트·상태 코드(409/422/422/422)·한국어 문구(카탈로그 계약 테스트가 형식·조치 문구를 전수 검사한다)"""
    from app.core.errors.catalog import spec_for
    from app.core.errors.codes import ErrorCode

    expected = {
        ErrorCode.ORDER_INTAKE_FILE_DUPLICATE: ("ORDER_INTAKE.FILE.DUPLICATE", 409),
        ErrorCode.ORDER_INTAKE_FILE_INVALID_ROWS: ("ORDER_INTAKE.FILE.INVALID_ROWS", 422),
        ErrorCode.ORDER_INTAKE_FILE_TOO_MANY_GROUPS: ("ORDER_INTAKE.FILE.TOO_MANY_GROUPS", 422),
        ErrorCode.ORDER_INTAKE_FILE_UNSUPPORTED_FORMAT: (
            "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT",
            422,
        ),
    }
    for code, (text, status) in expected.items():
        assert str(code) == text and spec_for(code).status_code == status


@pytest.mark.group_k
def test_the_error_report_never_echoes_amounts_or_internal_values(trade: Any) -> None:
    """오류 리포트(응답 detail)는 행번호·열·코드·한국어 사유뿐이다 — 거래처 내부 id·SKU 코드·스택·SQL이 실리지 않는다"""
    w = world(lines=1)
    response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-E", qty="x", price="9.999")]))
    detail = response.json()["error"]["detail"]
    assert set(detail) == {"errors", "total_errors", "omitted_errors"}
    for item in detail["errors"]:
        assert set(item) == {"row_no", "column", "code", "message_ko"}
    text = response.text
    assert "Traceback" not in text and "SELECT" not in text and "sku_id" not in text


@pytest.mark.group_k
def test_template_and_a_numeric_id_route_do_not_collide(trade: Any) -> None:
    """`GET /order-intakes/template.csv`는 `/{intake_id}`보다 먼저 선언돼 id 422가 아니라 양식을 준다 / 정수 id 상세는 그대로 동작"""
    assert trade.get(TEMPLATE_CSV).status_code == 200
    assert trade.get(f"{INTAKES}/999999").status_code == 404
    assert trade.get(f"{INTAKES}/not-a-number").status_code == 422
