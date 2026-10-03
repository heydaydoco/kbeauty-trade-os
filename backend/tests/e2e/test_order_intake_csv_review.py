"""F·A·J·K. 오더 인테이크 CSV 입구 — 적대 검토 반영분(S3-1 PR-14a 2차 / ADR-0072).

계수 패스(행 객체 전 50,000행 거부)·strict 리더(깨진 따옴표의 물리 줄 번호)·헤더 진단(끝 빈 열·열 단위 차이)·셀 원문 길이와 가장자리 제어 문자·
엑셀 오류 값·CP949 '?' 손실·NFC·ASCII 코드·'0,500'·형식이 틀린 행의 마스터 오류·KST 자정 경계 번역·리포트 우선순위와 `counts_by_code`·
중복 PO 응답 두 모양·행별 헤더 원문 스냅샷·`first_row_no` 파일 순서·열별 최대 길이 경계·잠금 대기 409 LOCK_BUSY. 테스트 데이터는 전부 익명 합성이다.
"""

from __future__ import annotations

import hashlib
import io
import tracemalloc
import unicodedata
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.order_intake import csv_import, csv_template
from app.modules.order_intake import service as intake_service
from tests.factories.intake import (
    code_of,
    csv_bytes,
    csv_header,
    csv_row,
    error_rows,
    get,
    land,
    scalar,
    upload_csv,
    world,
)
from tests.factories.trade import create_buyer, logged_in, map_buyer_item_code, unique

pytestmark = pytest.mark.group_f

INVALID_ROWS = "ORDER_INTAKE.FILE.INVALID_ROWS"
DUP_PO = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"


@pytest.fixture
def trade():  # type: ignore[no-untyped-def]
    with logged_in(RoleCode.TRADE) as client:
        yield client


def _count() -> int:
    return int(scalar("SELECT count(*) FROM order_intakes"))


def _codes(response: Any) -> list[tuple[int | None, str | None, str]]:
    return [(e["row_no"], e["column"], e["code"]) for e in error_rows(response)]


def _header_line() -> bytes:
    return (",".join(csv_header()) + "\r\n").encode("utf-8")


# ── A1 — 계수 패스 ──────────────────────────────────────────────────────────────


@pytest.mark.group_k
def test_millions_of_short_rows_are_refused_before_row_objects_are_built() -> None:
    """헤더+'a' 300만 줄 — 저장 없는 계수 패스가 50,000행을 넘는 즉시 같은 422로 거부한다(행 객체·구조 오류 300만 개를 만들지 않음). tracemalloc 피크 64MiB 이내"""
    content = b"\xef\xbb\xbf" + _header_line() + b"a\n" * 3_000_000
    actor = AuthenticatedUser(
        id=1,
        email="x@example.com",
        display_name="x",
        roles=frozenset({RoleCode.TRADE}),
        session_id=0,
    )
    tracemalloc.start()
    try:
        with pytest.raises(AppError) as caught:
            csv_import.import_csv(
                actor=actor, idempotency_key="k", stream=io.BytesIO(content), filename="big.csv"
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert caught.value.code is ErrorCode.VALIDATION_INVALID_FIELD
    assert "50,000" in caught.value.detail["file"]
    assert peak < 64 * 1024 * 1024, f"피크 {peak / 1024 / 1024:.1f}MiB"


# ── B4 — strict 리더 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tail", "line_no", "phrase", "english"),
    [
        (b'BUY,"PO-1,2026-01-01,USD,US,C,5,1.00,2026-12-31\r\n', 2, "따옴표", "unexpected end"),
        (b'BUY,"PO"1,2026-01-01,USD,US,C,5,1.00,2026-12-31\r\n', 2, "따옴표", "expected after"),
        (
            b"BUY," + b"x" * 140_000 + b",a,b,c,d,e,f,g\r\n",
            2,
            "셀 하나가 너무 깁니다",
            "field limit",
        ),
    ],
)
def test_broken_csv_syntax_is_a_korean_report_with_the_physical_line(
    trade: Any, tail: bytes, line_no: int, phrase: str, english: str
) -> None:
    """닫히지 않은 따옴표·따옴표 뒤 글자·필드 한도 초과는 추측해 이어 붙이지 않고 CSV_SYNTAX — 물리 줄 번호와 한국어 사유만(영문 원문은 로그 전용)"""
    response = upload_csv(trade, _header_line() + tail)
    assert response.status_code == 422 and code_of(response) == INVALID_ROWS
    [item] = error_rows(response)
    assert item["code"] == "CSV_SYNTAX" and item["row_no"] is None
    assert f"{line_no}번째 줄" in item["message_ko"] and phrase in item["message_ko"]
    assert english not in response.text
    assert _count() == 0


# ── B5 — 헤더 진단 ───────────────────────────────────────────────────────────────


def test_trailing_empty_header_columns_are_named_and_refused_not_trimmed(trade: Any) -> None:
    """헤더 끝에 빈 열이 붙은 파일 — 자동으로 잘라 내지 않고 '빈 열 N개' 전용 문구로 거부한다"""
    w = world()
    header = [*csv_header(), "", " "]
    response = upload_csv(trade, csv_bytes([[*csv_row(w, po="PO-1"), "", ""]], header=header))
    assert response.status_code == 422 and code_of(response) == "IMPORTS.FILE.HEADER_MISMATCH"
    detail = response.json()["error"]["detail"]
    assert "빈 열이 2개" in detail["header"] and detail["trailing_empty_columns"] == 2
    assert _count() == 0


def test_header_differences_are_listed_per_column_with_repr_and_cut(trade: Any) -> None:
    """그 밖의 헤더 불일치는 열 단위 차이를 repr로(보이지 않는 문자도 드러남) — 열당 40자·최대 20열로 자른다"""
    w = world()
    header = csv_header()
    header[2] = "PO 일자"
    header[5] = "바이어​품번"
    response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-1")], header=header))
    detail = response.json()["error"]["detail"]
    assert "3열: 기대 'PO일자' / 파일 'PO 일자'" in detail["header"]
    assert "\\u200b" in detail["header"]
    assert [d["column_no"] for d in detail["differences"]] == [3, 6]
    wide = ["X" * 100] * 30
    response = upload_csv(trade, csv_bytes([["1"] * 30], header=wide))
    detail = response.json()["error"]["detail"]
    assert len(detail["differences"]) == 20 and "외 10열" in detail["header"]
    assert "X" * 41 not in detail["header"]


# ── B3 — 셀 원문 길이·가장자리 제어 문자 ─────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["\tPO-1", "PO-1\n", "PO-1\r", "\x0bPO-1"])
def test_control_characters_stripped_from_the_edges_are_still_rejected(
    trade: Any, bad: str
) -> None:
    """strip이 걷어 내는 가장자리라도 줄바꿈·탭 같은 제어 문자(Cc·Cf)면 INVISIBLE_CHAR — 여백(공백·NBSP)만 조용히 걷는다"""
    w = world()
    response = upload_csv(trade, csv_bytes([csv_row(w, po=bad)]))
    assert response.status_code == 422
    assert _codes(response) == [(2, "바이어PO번호", "INVISIBLE_CHAR")]
    ok = upload_csv(trade, csv_bytes([csv_row(w, po="  PO-NBSP  ")]))
    assert ok.status_code == 201, ok.text


def test_the_raw_cell_length_is_capped_before_strip(trade: Any) -> None:
    """셀 원문 길이 상한은 strip 전 기준 — 공백 포함 200자는 통과, 201자는 TOO_LONG"""
    w = world()
    padded = " " * (200 - len("PO-W")) + "PO-W"
    assert upload_csv(trade, csv_bytes([csv_row(w, po=padded)])).status_code == 201
    response = upload_csv(trade, csv_bytes([csv_row(w, po=" " + padded + "")]))
    assert response.status_code == 422
    assert _codes(response) == [(2, "바이어PO번호", "TOO_LONG")]


# ── B13(b) — 열별 최대·최대+1 ──────────────────────────────────────────────────────


def _code_of_len(prefix: str, length: int) -> str:
    return (prefix + "X" * length)[:length]


@pytest.mark.parametrize(
    ("column", "ok", "over"),
    [
        ("바이어PO번호", {"po": "P" * 60}, {"po": "P" * 61}),
        ("바이어품번", {"code": "I" * 100}, {"code": "I" * 101}),
        ("통화", {"currency": "USD"}, {"currency": "USDX"}),
        ("목적지시장코드", {"market": "US"}, {"market": "USA"}),
        ("수량", {"qty": "0" * 39 + "5"}, {"qty": "0" * 40 + "5"}),
        ("단가", {"price": "0" * 35 + "10.00"}, {"price": "0" * 36 + "10.00"}),
    ],
)
def test_each_column_accepts_its_max_length_and_refuses_one_more(
    trade: Any, column: str, ok: dict[str, str], over: dict[str, str]
) -> None:
    """열마다 최대 길이 경계 — max는 통과, max+1은 그 열의 TOO_LONG"""
    w = world()
    ok_kwargs = {"po": unique("PO"), **ok}
    assert upload_csv(trade, csv_bytes([csv_row(w, **ok_kwargs)])).status_code == 201
    over_kwargs = {"po": unique("PO"), **over}
    response = upload_csv(trade, csv_bytes([csv_row(w, **over_kwargs)]))
    assert response.status_code == 422
    assert _codes(response) == [(2, column, "TOO_LONG")]


def test_buyer_code_max_length_is_the_partner_code_width(trade: Any) -> None:
    """바이어코드 경계 — 거래처 코드 폭 40자는 통과(등록된 40자 코드), 41자는 TOO_LONG(조회 전에)"""
    w = world()
    code40 = _code_of_len(unique("B40"), 40)
    create_buyer(code=code40)
    assert (
        upload_csv(trade, csv_bytes([csv_row(w, po="PO-40", buyer_code=code40)])).status_code == 201
    )
    response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-41", buyer_code=code40 + "Z")]))
    assert _codes(response) == [(2, "바이어코드", "TOO_LONG")]


# ── B6 — 엑셀 오염 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value", ["#N/A", "#NAME?", "#VALUE!", "#REF!", "#DIV/0!", "#NUM!", "#NULL!", "#", "#####"]
)
@pytest.mark.parametrize(
    ("column", "key"),
    [("바이어코드", "buyer_code"), ("바이어PO번호", "po"), ("바이어품번", "code")],
)
def test_excel_error_literals_in_code_columns_are_row_errors(
    trade: Any, value: str, column: str, key: str
) -> None:
    """코드 열의 엑셀 오류 값(#N/A·#NAME?…)과 '#'만으로 된 값(열 너비 부족 표시)은 원래 값이 손실된 것 — EXCEL_ERROR"""
    w = world()
    kwargs: dict[str, Any] = {"po": unique("PO"), key: value}
    response = upload_csv(trade, csv_bytes([csv_row(w, **kwargs)]))
    assert response.status_code == 422
    assert (2, column, "EXCEL_ERROR") in _codes(response)


def test_a_question_mark_in_a_cp949_file_code_is_treated_as_encoding_loss(trade: Any) -> None:
    """CP949로 읽은 파일의 코드 열 '?'는 표현 불가 글자 손실일 수 있다 — ENCODING_LOSS. 같은 값도 UTF-8 파일이면 통과(실제 '?'가 들어간 코드)"""
    w = world()
    row = csv_row(w, po="PO-Q", code="품번?가")
    response = upload_csv(trade, csv_bytes([row], encoding="cp949"))
    assert response.status_code == 422
    assert _codes(response) == [(2, "바이어품번", "ENCODING_LOSS")]
    assert upload_csv(trade, csv_bytes([row], encoding="utf-8-sig")).status_code == 201


# ── B10 — ASCII·NFC·'0,500' ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "column"),
    [
        ({"currency": "ＵＳＤ"}, "통화"),
        ({"currency": "ｕｓｄ"}, "통화"),
        ({"market": "ＵＳ"}, "목적지시장코드"),
    ],
)
def test_non_ascii_currency_and_market_codes_are_format_errors(
    trade: Any, kwargs: dict[str, str], column: str
) -> None:
    """통화·시장 코드는 ASCII를 먼저 본다 — 전각 코드는 대문자화로 바꾸지 않고 INVALID_FORMAT"""
    response = upload_csv(trade, csv_bytes([csv_row(world(), po=unique("PO"), **kwargs)]))
    assert _codes(response) == [(2, column, "INVALID_FORMAT")]


@pytest.mark.parametrize(("key", "column"), [("code", "바이어품번"), ("po", "바이어PO번호")])
def test_decomposed_hangul_in_code_columns_is_refused_not_normalized(
    trade: Any, key: str, column: str
) -> None:
    """코드 열이 NFC가 아니면(맥 복붙 조합형 등) 행 오류 NOT_NFC — 자동 정규화하지 않는다"""
    nfd = unicodedata.normalize("NFD", "가나품번")
    kwargs: dict[str, Any] = {"po": unique("PO"), key: nfd}
    response = upload_csv(trade, csv_bytes([csv_row(world(), **kwargs)]))
    assert (2, column, "NOT_NFC") in _codes(response)


@pytest.mark.parametrize("value", ["0,500", "0,000", "00,500"])
def test_a_grouped_quantity_never_starts_with_zero(trade: Any, value: str) -> None:
    """수량 천단위 표기의 첫 그룹은 0으로 시작하지 않는다 — '0,500'은 500이 아니라 INVALID_FORMAT(유럽식 소수일 수 있다)"""
    response = upload_csv(trade, csv_bytes([csv_row(world(), po=unique("PO"), qty=value)]))
    assert _codes(response) == [(2, "수량", "INVALID_FORMAT")]


# ── B9 — 오도 문구 ───────────────────────────────────────────────────────────────


def test_misleading_messages_are_specific(trade: Any) -> None:
    """시간이 붙은 날짜는 '시간을 빼고', 음수 수량은 범위 문구(OUT_OF_RANGE), 읽지 못한 바이트는 줄 번호를 detail에"""
    w = world()
    past_iso = (today_kst() - timedelta(days=3)).isoformat()
    timed = upload_csv(trade, csv_bytes([csv_row(w, po="PO-T", po_date=f"{past_iso} 00:00")]))
    [item] = error_rows(timed)
    assert item["code"] == "INVALID_DATE" and "시간을 빼고" in item["message_ko"]
    for negative in ("-5", "-1,000"):
        response = upload_csv(trade, csv_bytes([csv_row(w, po="PO-N", qty=negative)]))
        assert _codes(response) == [(2, "수량", "OUT_OF_RANGE")], negative
        assert "범위" in error_rows(response)[0]["message_ko"]
    good = ",".join(csv_row(w, po="PO-G")).encode("utf-8") + b"\r\n"
    broken = _header_line() + good + b"BUY,PO-X,\xff\xff,USD,US,C,1,1.00,2030-01-01\r\n"
    response = upload_csv(trade, broken)
    assert response.status_code == 422 and code_of(response) == "IMPORTS.FILE.ENCODING_INVALID"
    detail = response.json()["error"]["detail"]
    assert detail["line_no"] == 3 and "3번째 줄" in detail["file"]


# ── B7 — 형식이 틀린 행도 마스터 오류를 함께 받는다 ────────────────────────────────────


def test_master_errors_cover_rows_whose_other_columns_are_wrong(trade: Any) -> None:
    """PO번호가 틀린(그룹에 못 들어간) 행도 바이어·시장 미등록을 함께 보고하고, 수량이 틀린 행도 같은 SKU 판정에 들어간다 — 한 번에 다 고친다"""
    w = world(lines=1)
    alias = unique("ALIAS")
    map_buyer_item_code(w["buyer"], w["sku_ids"][0], alias)
    content = csv_bytes(
        [
            csv_row(w, po="1.5E+3", buyer_code="NO-SUCH-BUYER", market="ZZ"),
            csv_row(w, po="PO-S", code=w["codes"][0]),
            csv_row(w, po="PO-S", code=alias, qty="x"),
        ]
    )
    response = upload_csv(trade, content)
    got = set(_codes(response))
    assert {
        (2, "바이어코드", "BUYER_NOT_REGISTERED"),
        (2, "바이어PO번호", "EXCEL_EXPONENT"),
        (2, "목적지시장코드", "MARKET_NOT_REGISTERED"),
        (4, "바이어품번", "DUPLICATE_SKU"),
        (4, "수량", "INVALID_FORMAT"),
    } <= got, got


# ── B8 — KST 자정 경계 ───────────────────────────────────────────────────────────


def test_a_landing_time_recheck_failure_becomes_that_rows_report(
    trade: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """파서 검사 뒤 자정을 넘겨 착지 통로 재검사가 납기를 거부해도 500·전체 422가 아니라 그 행·열의 OUT_OF_RANGE 리포트다(착지 0건)"""
    w = world()
    today = today_kst()
    monkeypatch.setattr(intake_service, "today_kst", lambda: today + timedelta(days=1))
    row = csv_row(w, po="PO-MID", delivery=today.isoformat())
    response = upload_csv(trade, csv_bytes([row]))
    assert response.status_code == 422 and code_of(response) == INVALID_ROWS
    assert _codes(response) == [(2, "요청납기일", "OUT_OF_RANGE")]
    assert _count() == 0


# ── B11 — 중복 PO 응답 두 모양 ────────────────────────────────────────────────────


@pytest.mark.group_a
def test_the_duplicate_po_409_has_two_shapes_and_errors_tells_them_apart(
    trade: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 409 DUPLICATE_BUYER_PO가 두 모양이다 — 파일 검증(`detail.errors[…]`)과 착지 통로 경합(`detail {intake_id|doc_number, status}`, errors 키 없음). 14b는 errors 키 유무로 먼저 분기한다"""
    w = world()
    held = land(w, po_no="PO-HELD")
    report = upload_csv(trade, csv_bytes([csv_row(w, po="PO-HELD")]))
    assert report.status_code == 409 and code_of(report) == DUP_PO
    assert "errors" in report.json()["error"]["detail"]
    monkeypatch.setattr(csv_import.intake_service, "occupants", lambda *a, **k: {})
    race = upload_csv(trade, csv_bytes([csv_row(w, po="PO-HELD", qty="6")]))
    assert race.status_code == 409 and code_of(race) == DUP_PO
    detail = race.json()["error"]["detail"]
    assert "errors" not in detail and detail == {"intake_id": held["id"], "status": "PENDING"}


# ── B12 — 행별 헤더 원문 ───────────────────────────────────────────────────────────


def test_the_snapshot_keeps_every_rows_header_cells_verbatim(trade: Any) -> None:
    """불변 원본은 행마다 헤더 5셀 원문(`header_cells`)을 싣는다 — 같은 PO 안에서 대소문자만 다른 통화·시장도 그 행 원문대로 남는다(13b 렌더 키 유지)"""
    w = world(lines=2)
    content = csv_bytes(
        [
            csv_row(w, po="PO-H", code=w["codes"][0], currency="usd", market="us"),
            csv_row(w, po="PO-H", code=w["codes"][1], currency="USD", market="US"),
        ]
    )
    response = upload_csv(trade, content)
    assert response.status_code == 201, response.text
    original = get(trade, response.json()["intakes"][0]["id"])["original"]
    assert [ln["header_cells"]["currency"] for ln in original["lines"]] == ["usd", "USD"]
    assert [ln["header_cells"]["dest_market_code"] for ln in original["lines"]] == ["us", "US"]
    assert {
        "buyer_item_code",
        "quantity",
        "unit_price",
        "requested_delivery_date",
        "row_no",
    } <= set(original["lines"][0])
    assert original["header"]["currency"] == "usd"


# ── B13(a) — 파일 순서 ────────────────────────────────────────────────────────────


def test_the_response_follows_file_order_even_though_landing_is_key_ordered(trade: Any) -> None:
    """착지는 `(거래처, PO키)` 정렬 순이지만 응답은 파일 순서(first_row_no) — 파일 순서와 키 순서가 반대인 파일로 확인"""
    w = world()
    content = csv_bytes([csv_row(w, po="PO-Z"), csv_row(w, po="PO-A")])
    response = upload_csv(trade, content)
    intakes = response.json()["intakes"]
    assert [i["buyer_po_no"] for i in intakes] == ["PO-Z", "PO-A"]
    assert [i["first_row_no"] for i in intakes] == [2, 3]
    assert intakes[0]["id"] > intakes[1]["id"]  # PO-A가 먼저 착지했다


# ── B15 — 리포트 우선순위·counts_by_code ───────────────────────────────────────────


@pytest.mark.group_a
def test_the_capped_report_keeps_duplicate_po_and_master_errors_and_counts_by_code(
    trade: Any,
) -> None:
    """형식 오류 250건 뒤의 중복 PO·바이어 미등록이 상한 200에 가려지지 않는다(우선 포함) — counts_by_code는 전수"""
    w = world()
    land(w, po_no="PO-HELD")
    rows_ = [csv_row(w, po=f"PO-{i % 2}", code=f"C-{i}", qty="x") for i in range(250)]
    rows_.append(csv_row(w, po="PO-HELD"))
    rows_.append(csv_row(w, po="PO-NB", buyer_code="NO-SUCH-BUYER"))
    response = upload_csv(trade, csv_bytes(rows_))
    detail = response.json()["error"]["detail"]
    assert response.status_code == 422
    assert len(detail["errors"]) == 200 and detail["total_errors"] == 252
    assert detail["omitted_errors"] == 52
    assert detail["counts_by_code"] == {
        "BUYER_NOT_REGISTERED": 1,
        "DUPLICATE_BUYER_PO": 1,
        "INVALID_FORMAT": 250,
    }
    shown = {(e["row_no"], e["code"]) for e in detail["errors"]}
    assert (252, "DUPLICATE_BUYER_PO") in shown and (253, "BUYER_NOT_REGISTERED") in shown
    rows_no = [e["row_no"] for e in detail["errors"]]
    assert rows_no == sorted(rows_no)  # 표시는 행 순서


# ── A2 ③ — 잠금 대기 초과 ─────────────────────────────────────────────────────────


@pytest.mark.group_j
def test_a_file_lock_held_past_lock_timeout_is_409_lock_busy_and_the_same_key_retries(
    trade: Any,
) -> None:
    """같은 파일의 앞 트랜잭션이 lock_timeout(5s)을 넘겨 파일 해시 잠금을 쥐고 있으면 뒤 업로드는 409 LOCK_BUSY — 키를 소비하지 않아 같은 키 재시도가 착지한다"""
    w = world()
    content = csv_bytes([csv_row(w, po="PO-LOCK")])
    sha = hashlib.sha256(content).hexdigest()
    key = unique("lock")
    with owner_engine.connect() as holder:
        tx = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"order_intake_csv:{sha}"},
        )
        busy = upload_csv(trade, content, key=key)
        tx.rollback()
    assert busy.status_code == 409 and code_of(busy) == "COMMON.CONCURRENCY.LOCK_BUSY"
    assert _count() == 0
    retried = upload_csv(trade, content, key=key)
    assert retried.status_code == 201, retried.text


def test_the_catalog_message_does_not_hardcode_the_group_limit() -> None:
    """FILE.TOO_MANY_GROUPS 문구는 상한 숫자를 하드코딩하지 않는다(상한은 detail.max_groups — 단일 출처 csv_template)"""
    from app.core.errors.catalog import spec_for

    message = spec_for(ErrorCode.ORDER_INTAKE_FILE_TOO_MANY_GROUPS).message_ko
    assert str(csv_template.MAX_GROUPS) not in message
