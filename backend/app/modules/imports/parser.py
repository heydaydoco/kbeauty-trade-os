"""왕복 CSV 파서 (DESIGN.md §12.2 보강 / ADR-0027 — 부채 #17의 나머지 반쪽).

■ 수식 이스케이프의 역변환은 여기서만 일어난다

  내보내기(core/csv_export)가 **문자열 타입 셀**에 `'`를 접두했으니, 임포트는
  같은 집합의 셀에서 정확한 역변환(unescape_formula_cell)을 적용한다. 어느
  컬럼이 문자열 타입인지는 레지스트리가 안다 — 숫자·금액·불리언 컬럼에
  역변환을 적용하면 전단사가 깨진다(내보내기가 그 셀을 이스케이프하지 않았다).

  보증 범위는 본 시스템 export→import 쌍 기준이다(ADR-0027 문면). 중간에
  엑셀 편집이 끼는 일반 케이스는 `'` 접두가 엑셀의 텍스트 접두 관례와 같아
  보존되고, 그 이상의 방어는 더하지 않는다(과잉 방어 금지 — 웹 세션 판정).

■ 인코딩: UTF-8(BOM) 우선, CP949 폴백

  우리 내보내기는 UTF-8 BOM이다. 다만 한국어 엑셀이 "CSV(쉼표로 분리)"로
  저장하면 CP949로 재인코딩되는 실무가 있어 한 번만 폴백한다. 둘 다 아니면
  읽지 않고 거부한다(fail-closed) — 깨진 글자를 diff에 올리지 않는다.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from app.core.csv_export import unescape_formula_cell
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError


@dataclass(frozen=True, slots=True)
class RawRow:
    """파일의 데이터 한 행 — 역변환까지 끝난 셀 값(헤더 → 셀)."""

    row_no: int
    cells: dict[str, str]


@dataclass(frozen=True, slots=True)
class RowProblem:
    """행 구조 오류(열 개수 불일치 등) — 오류 행 리포트로 나간다(§12.2)."""

    row_no: int
    reason: str


@dataclass(frozen=True, slots=True)
class ParsedFile:
    rows: list[RawRow]
    problems: list[RowProblem]
    #: 전부 빈 셀이라 건너뛴 행 수 — 엑셀이 꼬리에 남기는 빈 행은 데이터가 아니다.
    skipped_blank_rows: int


def decode_upload(raw: bytes) -> str:
    """업로드 바이트 → 본문 문자열. UTF-8(BOM 흡수) → CP949 순서로 한 번씩만."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp949")
    except UnicodeDecodeError as exc:
        raise AppError(ErrorCode.IMPORTS_FILE_ENCODING_INVALID) from exc


def parse_csv(text: str, *, header: tuple[str, ...], string_columns: frozenset[str]) -> ParsedFile:
    """CSV 본문을 행 목록으로. 헤더가 표준 양식과 다르면 통째로 거부한다.

    열이 어긋난 채 파싱을 계속하면 "이름이 코드 칸에 들어간" diff가 만들어지고,
    그 diff는 검토자가 잡아내기 가장 어려운 종류의 오염이다 — 첫 행에서 멈춘다.
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        actual_header = next(reader)
    except StopIteration:
        raise AppError(ErrorCode.IMPORTS_FILE_EMPTY) from None
    if tuple(actual_header) != header:
        raise AppError(
            ErrorCode.IMPORTS_FILE_HEADER_MISMATCH,
            detail={
                "header": (
                    f"기대한 컬럼: {', '.join(header)} / 파일의 컬럼: "
                    f"{', '.join(actual_header) or '(없음)'}"
                )
            },
        )

    rows: list[RawRow] = []
    problems: list[RowProblem] = []
    skipped_blank = 0
    # 헤더가 1행이므로 데이터는 2행부터 — 오류 리포트의 행번호가 엑셀과 일치한다.
    for row_no, record in enumerate(reader, start=2):
        if all(cell == "" for cell in record):
            skipped_blank += 1
            continue
        if len(record) != len(header):
            problems.append(
                RowProblem(
                    row_no=row_no,
                    reason=f"열 개수가 다릅니다(기대 {len(header)}칸, 실제 {len(record)}칸). "
                    "표준 양식의 컬럼을 그대로 두고 값만 고쳐 주세요.",
                )
            )
            continue
        cells = {
            name: (unescape_formula_cell(cell) if name in string_columns else cell)
            for name, cell in zip(header, record, strict=True)
        }
        rows.append(RawRow(row_no=row_no, cells=cells))
    return ParsedFile(rows=rows, problems=problems, skipped_blank_rows=skipped_blank)
