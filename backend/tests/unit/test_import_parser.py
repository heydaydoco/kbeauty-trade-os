"""F. 왕복 CSV 파서 — 역변환 대칭·인코딩·구조 검증 (§12.2 보강 / ADR-0027)."""

from __future__ import annotations

import io

import pytest

from app.core.csv_export import escape_formula_cell, unescape_formula_cell
from app.core.errors.exceptions import AppError
from app.modules.imports import parser
from app.modules.imports.service import MAX_UPLOAD_BYTES, read_limited

pytestmark = pytest.mark.group_f

HEADER = ("ID", "이름", "메모")
STRING_COLUMNS = frozenset({"이름", "메모"})


def _parse(text: str) -> parser.ParsedFile:
    return parser.parse_csv(text, header=HEADER, string_columns=STRING_COLUMNS)


# ── 전단사 — escape ∘ unescape = 항등 (ADR-0027) ───────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        "+82-10",
        "-5",
        "@멘션",
        "\t탭 시작",
        "\r캐리지 리턴",
        "'이미 홑따옴표",
        "'=수식이었던 원값",
        "''이중 홑따옴표",
        "일반 텍스트",
        "",
        "중간=수식은 비대상",
    ],
)
def test_escape_then_unescape_is_identity(value: str) -> None:
    """어떤 원값이든 내보내기→임포트 왕복이 원값이다 — 전단사의 단위 증명"""
    assert unescape_formula_cell(escape_formula_cell(value)) == value


def test_unescape_applies_only_to_string_columns() -> None:
    """역변환은 문자열 타입 셀에만 — 비대상 셀의 `'`는 값의 일부다 (§12.2 보강)"""
    parsed = _parse("ID,이름,메모\r\n1,'=이름,'=메모\r\n")
    assert parsed.rows[0].cells["이름"] == "=이름"
    assert parsed.rows[0].cells["메모"] == "=메모"
    # ID는 문자열 타입 셀이 아니다 — 역변환하지 않는다(내보내기도 이스케이프 안 함).
    parsed_id = _parse("ID,이름,메모\r\n'=1,a,b\r\n")
    assert parsed_id.rows[0].cells["ID"] == "'=1"


def test_plain_apostrophe_text_is_left_alone() -> None:
    """트리거가 아닌 `'일반텍스트`는 사용자가 친 값 그대로다"""
    parsed = _parse("ID,이름,메모\r\n,'일반,x\r\n")
    assert parsed.rows[0].cells["이름"] == "'일반"


# ── 구조 검증 ──────────────────────────────────────────────────────────────


def test_header_mismatch_is_rejected() -> None:
    with pytest.raises(AppError) as caught:
        _parse("ID,이름\r\n1,a\r\n")
    assert caught.value.code == "IMPORTS.FILE.HEADER_MISMATCH"


def test_empty_text_is_rejected() -> None:
    with pytest.raises(AppError) as caught:
        _parse("")
    assert caught.value.code == "IMPORTS.FILE.EMPTY"


def test_row_numbers_match_excel_rows() -> None:
    """행번호는 엑셀에서 사람이 보는 번호다 — 헤더가 1행, 데이터는 2행부터"""
    parsed = _parse("ID,이름,메모\r\n1,a,b\r\n2,c,d\r\n")
    assert [row.row_no for row in parsed.rows] == [2, 3]


def test_blank_rows_are_skipped_not_errors() -> None:
    """엑셀이 꼬리에 남기는 빈 행은 데이터가 아니다 — 세지도 오류도 아니다"""
    parsed = _parse("ID,이름,메모\r\n1,a,b\r\n,,\r\n\r\n")
    assert len(parsed.rows) == 1
    assert parsed.problems == []
    assert parsed.skipped_blank_rows == 2


def test_column_count_mismatch_is_a_row_problem() -> None:
    parsed = _parse("ID,이름,메모\r\n1,a\r\n1,a,b,c\r\n")
    assert [problem.row_no for problem in parsed.problems] == [2, 3]
    assert "열 개수" in parsed.problems[0].reason


# ── 인코딩 (UTF-8 BOM 우선, CP949 폴백 — fail-closed) ──────────────────────


def test_utf8_bom_is_absorbed() -> None:
    text = parser.decode_upload("이름".encode("utf-8-sig"))
    assert text == "이름"


def test_cp949_fallback_decodes_excel_saved_files() -> None:
    text = parser.decode_upload("한글값".encode("cp949"))
    assert text == "한글값"


def test_undecodable_bytes_are_rejected() -> None:
    with pytest.raises(AppError) as caught:
        parser.decode_upload(b"\xff\xfe\x00\x01\x81\x82")
    assert caught.value.code == "IMPORTS.FILE.ENCODING_INVALID"


# ── 크기 상한 — documents와 같은 수치(ADR-0029 사정권) ─────────────────────


def test_the_size_limit_is_pinned_and_enforced() -> None:
    """상한은 20MiB 명시 수치다 — documents.MAX_UPLOAD_BYTES와 같은 값·같은 근거"""
    from app.modules.documents.service import MAX_UPLOAD_BYTES as DOCUMENTS_LIMIT

    assert MAX_UPLOAD_BYTES == 20 * 1024 * 1024
    assert MAX_UPLOAD_BYTES == DOCUMENTS_LIMIT
    with pytest.raises(AppError) as caught:
        read_limited(io.BytesIO(b"0" * (MAX_UPLOAD_BYTES + 1)))
    assert caught.value.code == "IMPORTS.FILE.TOO_LARGE"
    assert caught.value.status_code == 413


# ── strict 모드 (PR-14a B4 — 오더 인테이크 CSV 입구 전용, 기본값은 기존 동작) ────────


@pytest.mark.parametrize(
    ("text", "line_no"),
    [
        ('ID,이름,메모\r\n1,"닫히지 않은 따옴표,x\r\n', 2),
        ('ID,이름,메모\r\n1,"ab"c,x\r\n', 2),
    ],
)
def test_strict_mode_stops_at_broken_quotes_with_the_physical_line_number(
    text: str, line_no: int
) -> None:
    """strict=True는 닫히지 않은 따옴표·따옴표 뒤 글자를 추측해 이어 붙이지 않고 물리 줄 번호와 함께 멈춘다(`csv.Error` 하위 타입)"""
    import csv

    with pytest.raises(parser.CsvSyntaxError) as caught:
        parser.parse_csv(text, header=HEADER, string_columns=STRING_COLUMNS, strict=True)
    assert caught.value.line_no == line_no and caught.value.reason
    assert isinstance(caught.value, csv.Error)


def test_default_mode_keeps_the_previous_lenient_behaviour() -> None:
    """기본(strict=False) — 기존 임포트 동작 불변: 따옴표 뒤 글자는 이어 붙여 읽는다"""
    parsed = _parse('ID,이름,메모\r\n1,"ab"c,x\r\n')
    assert parsed.rows[0].cells["이름"] == "abc"


def test_the_column_count_reason_is_one_shared_constant() -> None:
    """열 개수 불일치 사유는 공유 상수 하나(`COLUMN_COUNT_MISMATCH`) — 문구는 이전과 같다"""
    parsed = _parse("ID,이름,메모\r\n1,2\r\n")
    assert parsed.problems[0].reason == parser.COLUMN_COUNT_MISMATCH.format(expected=3, actual=2)
    assert parsed.problems[0].reason.startswith("열 개수가 다릅니다(기대 3칸, 실제 2칸).")
