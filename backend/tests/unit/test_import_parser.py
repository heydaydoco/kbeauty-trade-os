"""F. 왕복 CSV 파서 — 역변환 대칭·인코딩·구조 검증 (§12.2 보강 / ADR-0027)."""

from __future__ import annotations

import io

import pytest

from app.core.csv_export import escape_formula_cell, unescape_formula_cell
from app.core.errors.exceptions import AppError
from app.modules.imports import parser
from app.modules.imports.service import MAX_UPLOAD_BYTES, _read_limited

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
        _read_limited(io.BytesIO(b"0" * (MAX_UPLOAD_BYTES + 1)))
    assert caught.value.code == "IMPORTS.FILE.TOO_LARGE"
    assert caught.value.status_code == 413
