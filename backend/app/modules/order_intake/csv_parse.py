"""오더 인테이크 CSV 파서·행 검증 — **DB 없는 순수 함수** (S3-1 PR-14a / design-D D2 / ADR-0072).

입력은 `imports.parser.parse_csv`가 만든 행(수식 이스케이프 역변환·헤더 완전 일치·행 구조 오류 리포트는 그쪽 통로 그대로)이고, 여기서는 **셀 값 검증과 PO 그룹핑**만 한다.
원칙은 fail-visible이다 — **추측 변환 금지**: 모호한 날짜(`10/5/2026`·엑셀 일련번호·2자리 연도·시간 붙은 날짜)·콤마 위치가 틀린 수량(`1,00`·`0,500`)·통화 최소단위를 넘는
소수 단가·지수 표기·엑셀 오류 값(`#N/A`)·전각 숫자·불가시 문자·조합형(NFD) 글자·CP949 저장이 '?'로 바꾼 글자는 전부 **행 오류**로 보고하고 사용자가 고치게 한다(반올림·자동 보정·자동 정규화 없음).
바이어코드·품번·PO번호는 **원문 유지**(대소문자 변환 없음 — 통화·시장 코드만 ASCII 확인 후 대문자화).

■ 셀 공통: **strip 전 원문 길이** 상한(`MAX_CELL_LENGTH`) → strip이 걷어 내는 가장자리에 제어 문자(Cc·Cf)가 있으면 오류(줄바꿈·탭은 '공백'이 아니다) → 열별 길이·형식.
■ 그룹 키 = `(바이어코드 strip, buyer_po_no_key)`(비연속 행도 한 그룹). 같은 그룹 안에서 앞 5열(헤더 값)이 다르면 **그 행의 오류**다(어느 행이 다른지 행번호).
■ 모든 오류를 **한 번에** 모은다(첫 오류에서 멈추지 않음) — 부분 성공이 없으므로 운영자가 한 번에 고칠 수 있어야 한다. 서버 마스터 검사(바이어·시장·같은 SKU)가
  형식을 통과한 **모든 행**을 보도록 `buyer_rows`·`market_rows`·그룹의 `items`(품번 형식 통과 행 전부)를 따로 넘긴다(다른 열이 틀린 행도 마스터 오류를 함께 받는다).
■ 오류 메시지에 **수량·단가 셀 원문을 싣지 않는다**(design-D D7 금액 미기재 — 열 이름과 사유만). 코드 값 인용은 40자로 자른다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.core.errors.exceptions import AppError
from app.core.money import CURRENCY_MINOR_UNITS, AmountFormatError, parse_minor_amount
from app.modules.imports.parser import ParsedFile
from app.modules.order_intake import csv_template as tpl
from app.modules.order_intake.models import MAX_BUYER_ITEM_CODE
from app.modules.trade_docs.buyer_po import BUYER_PO_MAX_LENGTH, po_columns
from app.modules.trade_docs.constants import MAX_QUANTITY, MAX_SAFE_INTEGER
from app.modules.trade_docs.snapshot import line_amount, validate_quantity

#: 셀 하나의 **strip 전** 원문 최대 길이(방어 — 어떤 열도 이보다 길 수 없다).
MAX_CELL_LENGTH = 200
#: 바이어코드 최대 길이(= `partners.partner_code` 열 폭).
MAX_BUYER_CODE_LENGTH = 40
#: 바이어PO번호 원문 최대 길이(= `trade_docs.buyer_po`의 원문·키 상한).
MAX_PO_LENGTH = BUYER_PO_MAX_LENGTH
#: 수량·단가·날짜 셀 최대 길이.
MAX_VALUE_LENGTH = 40
#: 오류 메시지에 인용하는 코드 값의 최대 길이.
QUOTE_LIMIT = 40

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
#: 엑셀이 날짜 셀을 '날짜 시간'으로 내보낸 값(`2026-10-05 00:00`) — 시간을 빼라고 안내한다(잘라 내지 않는다).
_DATE_WITH_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}", re.ASCII)
_QTY_PLAIN = re.compile(r"^\d+(\.0+)?$", re.ASCII)
#: 천단위 콤마 — 첫 그룹은 0으로 시작하지 않는다('0,500'은 유럽식 소수일 수 있다).
_QTY_GROUPED = re.compile(r"^[1-9]\d{0,2}(,\d{3})+(\.0+)?$", re.ASCII)
_QTY_NEGATIVE = re.compile(r"^-\s*[\d,]+(\.\d+)?$", re.ASCII)
#: 엑셀이 숫자로 오인해 바꾼 지수 표기(`1.23E+10`) — 코드 열에서는 원본 코드가 이미 손실된 값이다.
_EXPONENT = re.compile(r"^\d+(\.\d+)?[eE][+-]?\d+$", re.ASCII)
_MARKET = re.compile(r"^[A-Z]{2}$", re.ASCII)
#: 엑셀 오류 리터럴 — 수식이 깨진 셀을 CSV로 저장하면 이 문자열이 값이 된다(원래 코드는 손실).
EXCEL_ERROR_LITERALS = frozenset(
    {"#N/A", "#NAME?", "#VALUE!", "#REF!", "#DIV/0!", "#NUM!", "#NULL!"}
)

#: 모양이 없는 채움 문자(한글 채움·점자 빈칸) — 카테고리 필터에 안 걸린다.
_BLANKS = frozenset({"ᅟ", "ᅠ", "ㅤ", "ﾠ", "⠀"})
#: 기본 무시 문자(Default_Ignorable) 중 범주가 Mn이라 카테고리 필터에 안 걸리는 것 — 결합 문자 잇기(CGJ)·변형 선택자·몽골 자유 변형 선택자·크메르 모음 고유음.
_IGNORABLE_MARKS = frozenset(
    {
        "͏",
        "឴",
        "឵",
        *map(chr, range(0x180B, 0x1810)),
        *map(chr, range(0xFE00, 0xFE10)),
        *map(chr, range(0xE0100, 0xE01F0)),
    }
)
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})
#: strip이 걷어 내는 가장자리에 있으면 안 되는 범주 — 줄바꿈·탭(Cc)·서식 문자(Cf)는 '여백'이 아니다.
_EDGE_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf"})
#: PO일자 하한(엑셀 서식 오염 방어 — 1900·0001 류).
MIN_PO_DATE = date(2000, 1, 1)

# 오류 코드(행 리포트 `code`) — 프런트가 문구 대신 분기에 쓸 수 있는 안정 값.
E_REQUIRED = "REQUIRED"
E_TOO_LONG = "TOO_LONG"
E_INVISIBLE = "INVISIBLE_CHAR"
E_NOT_NFC = "NOT_NFC"
E_EXCEL_ERROR = "EXCEL_ERROR"
E_ENCODING_LOSS = "ENCODING_LOSS"
E_EXPONENT = "EXCEL_EXPONENT"
E_FORMAT = "INVALID_FORMAT"
E_DATE = "INVALID_DATE"
E_OUT_OF_RANGE = "OUT_OF_RANGE"
E_UNKNOWN_CURRENCY = "UNKNOWN_CURRENCY"
E_GROUP_MISMATCH = "GROUP_HEADER_MISMATCH"
E_DUPLICATE_ITEM = "DUPLICATE_ITEM"
E_GROUP_LINE_LIMIT = "GROUP_LINE_LIMIT"
E_ROW_STRUCTURE = "ROW_STRUCTURE"
E_CSV_SYNTAX = "CSV_SYNTAX"
E_BUYER_UNKNOWN = "BUYER_NOT_REGISTERED"
E_BUYER_NOT_BUYER = "NOT_A_BUYER"
E_MARKET_UNKNOWN = "MARKET_NOT_REGISTERED"
E_DUPLICATE_SKU = "DUPLICATE_SKU"
E_DUPLICATE_PO = "DUPLICATE_BUYER_PO"


@dataclass(frozen=True, slots=True)
class RowError:
    """행별 오류 1건 — `row_no`는 엑셀 행번호(헤더=1, 데이터는 2부터)이고 파일 수준 오류는 None."""

    row_no: int | None
    column: str | None
    code: str
    message_ko: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row_no": self.row_no,
            "column": self.column,
            "code": self.code,
            "message_ko": self.message_ko,
        }


@dataclass(frozen=True, slots=True)
class CsvLine:
    """검증을 통과한 라인 1행 — `raw`는 그 행의 9열 원본 셀 전부(스냅샷용, 수식 이스케이프만 역변환)."""

    row_no: int
    buyer_item_code: str
    quantity: int
    unit_price: str
    requested_delivery_date: date | None
    raw: dict[str, str]
    #: 라인 금액(수량 × 단가 최소단위) — 그룹 합계 상한 검사용.
    amount: int = 0


@dataclass(slots=True)
class CsvGroup:
    """한 바이어 PO = 인테이크 1건. 헤더 값은 첫 행 기준이다."""

    buyer_code: str
    po_no: str
    po_key: str
    po_date: date | None
    currency: str
    market: str
    first_row_no: int
    header_raw: dict[str, str]
    lines: list[CsvLine] = field(default_factory=list)
    row_nos: list[int] = field(default_factory=list)
    #: 품번 형식을 통과한 **모든** 행의 (행번호, 품번) — 같은 SKU 판정은 다른 열이 틀린 행도 본다.
    items: list[tuple[int, str]] = field(default_factory=list)


@dataclass(slots=True)
class ParsedCsv:
    groups: list[CsvGroup]
    errors: list[RowError]
    total_rows: int
    #: 형식을 통과한 바이어코드 → 그 행번호들(PO번호가 틀린 행 포함 — 마스터 검사 대상).
    buyer_rows: dict[str, list[int]] = field(default_factory=dict)
    #: 형식을 통과한 시장 코드 → 그 행번호들.
    market_rows: dict[str, list[int]] = field(default_factory=dict)


def quote(value: str) -> str:
    """메시지 인용용 — 40자로 자르고 따옴표로 감싼다."""
    cut = value if len(value) <= QUOTE_LIMIT else value[:QUOTE_LIMIT] + "…"
    return f"'{cut}'"


def _has_invisible(text: str) -> bool:
    """사람 눈에 안 보이거나 줄바꿈·탭 같은 제어 문자가 **값 안에** 있는가(앞뒤 공백은 호출자가 strip한 뒤다)."""
    for ch in text:
        category = unicodedata.category(ch)
        if (
            category in _INVISIBLE_CATEGORIES
            or ch in _BLANKS
            or ch in _IGNORABLE_MARKS
            or (category == "Zs" and ch != " ")
            or (ch.isspace() and ch != " ")
        ):
            return True
    return False


def _invisible(row_no: int, column: str) -> RowError:
    return RowError(
        row_no,
        column,
        E_INVISIBLE,
        f"{column}에 눈에 보이지 않는 문자(줄바꿈·탭·제로폭 공백 등)가 들어 있습니다. 셀 내용을 지우고 다시 입력해 주세요.",
    )


def _cell(errors: list[RowError], row_no: int, column: str, raw: str) -> str | None:
    """셀 공통 관문 — strip 전 원문 길이 상한, 가장자리 제어 문자 검사 후 strip 값을 돌려준다(오류면 None)."""
    if len(raw) > MAX_CELL_LENGTH:
        errors.append(
            RowError(
                row_no,
                column,
                E_TOO_LONG,
                f"{column} 셀이 너무 깁니다(공백 포함 {MAX_CELL_LENGTH}자 이내).",
            )
        )
        return None
    value = raw.strip()
    edges = raw[: len(raw) - len(raw.lstrip())] + raw[len(raw.rstrip()) :]
    if any(unicodedata.category(ch) in _EDGE_FORBIDDEN_CATEGORIES for ch in edges):
        errors.append(_invisible(row_no, column))
        return None
    return value


def _check_text(
    errors: list[RowError], row_no: int, column: str, value: str, *, required: bool, max_length: int
) -> bool:
    """공통 텍스트 검사 — 통과하면 True. 오류는 `errors`에 쌓는다."""
    if not value:
        if required:
            errors.append(RowError(row_no, column, E_REQUIRED, f"{column}을(를) 입력해 주세요."))
            return False
        return True
    if len(value) > max_length:
        errors.append(
            RowError(
                row_no, column, E_TOO_LONG, f"{column}은(는) {max_length}자 이내로 입력해 주세요."
            )
        )
        return False
    if _has_invisible(value):
        errors.append(_invisible(row_no, column))
        return False
    return True


def _check_code(
    errors: list[RowError],
    row_no: int,
    column: str,
    value: str | None,
    *,
    max_length: int,
    cp949: bool,
) -> bool:
    """코드 열(바이어코드·PO번호·품번) — 텍스트 검사 + NFC + 엑셀 오류 값 + CP949 '?' 손실 + 지수 표기 오염 거부."""
    if value is None:
        return False
    if not _check_text(errors, row_no, column, value, required=True, max_length=max_length):
        return False
    if not unicodedata.is_normalized("NFC", value):
        errors.append(
            RowError(
                row_no,
                column,
                E_NOT_NFC,
                f"{column}에 조합형(NFD) 글자가 들어 있습니다(보기에는 같아도 다른 값). "
                "셀을 지우고 직접 다시 입력해 주세요.",
            )
        )
        return False
    if value in EXCEL_ERROR_LITERALS or set(value) == {"#"}:
        errors.append(
            RowError(
                row_no,
                column,
                E_EXCEL_ERROR,
                f"{column} {quote(value)}은(는) 엑셀 오류 표시입니다(원래 값을 알 수 없음). 원래 코드를 다시 입력해 주세요.",
            )
        )
        return False
    if cp949 and "?" in value:
        errors.append(
            RowError(
                row_no,
                column,
                E_ENCODING_LOSS,
                f"{column}의 '?'는 CP949(엑셀 'CSV 쉼표로 분리')로 저장하면서 표현할 수 없는 글자가 바뀐 것일 수 있습니다. "
                "엑셀에서 'CSV UTF-8(쉼표로 분리)'로 다시 저장해 올려 주세요.",
            )
        )
        return False
    if _EXPONENT.match(value):
        errors.append(
            RowError(
                row_no,
                column,
                E_EXPONENT,
                f"{column} {quote(value)}은(는) 엑셀이 숫자로 오인해 지수 표기로 바꾼 값입니다(원래 값을 알 수 없음). "
                "코드 열은 '텍스트' 서식으로 지정한 뒤 원래 값을 다시 입력해 주세요.",
            )
        )
        return False
    return True


def parse_iso_date(value: str) -> date | None:
    """`YYYY-MM-DD`만 받는다 — 그 밖의 형식·존재하지 않는 날짜는 None."""
    if not _ISO_DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _date_cell(
    errors: list[RowError], row_no: int, column: str, value: str | None
) -> tuple[bool, date | None]:
    """날짜 열(필수) — ISO 외 형식은 오류(엑셀 일련번호·`10/5/2026`·`2026/10/05`·2자리 연도·시간 붙은 값 전부)."""
    if value is None:
        return False, None
    if not _check_text(errors, row_no, column, value, required=True, max_length=MAX_VALUE_LENGTH):
        return False, None
    parsed = parse_iso_date(value)
    if parsed is not None:
        return True, parsed
    if _DATE_WITH_TIME.match(value):
        message = f"{column} {quote(value)}에 시간이 붙어 있습니다. 시간을 빼고 yyyy-mm-dd(예: 2026-10-05)로 입력해 주세요."
    else:
        message = (
            f"{column} {quote(value)}은(는) 날짜 형식이 아닙니다. 2026-10-05 형식(연-월-일)으로 입력해 주세요"
            "(엑셀 셀 서식을 yyyy-mm-dd로 지정)."
        )
    errors.append(RowError(row_no, column, E_DATE, message))
    return False, None


def _quantity(errors: list[RowError], row_no: int, value: str | None) -> int | None:
    column = tpl.COL_QUANTITY
    if value is None:
        return None
    if not _check_text(errors, row_no, column, value, required=True, max_length=MAX_VALUE_LENGTH):
        return None
    out_of_range = RowError(
        row_no,
        column,
        E_OUT_OF_RANGE,
        f"{column}이(가) 범위를 벗어났습니다. 1 이상 {MAX_QUANTITY:,} 이하 정수(EA)로 입력해 주세요.",
    )
    if _QTY_PLAIN.match(value):
        digits = value.split(".", 1)[0]
    elif _QTY_GROUPED.match(value):
        digits = value.split(".", 1)[0].replace(",", "")
    elif _QTY_NEGATIVE.match(value):
        errors.append(out_of_range)
        return None
    else:
        errors.append(
            RowError(
                row_no,
                column,
                E_FORMAT,
                f"{column}이(가) 정수가 아닙니다. 1000 또는 1,000처럼 정수(EA)로 입력해 주세요"
                "(소수·지수 표기·전각 숫자 불가, 천단위 콤마는 세 자리마다).",
            )
        )
        return None
    try:
        return validate_quantity(int(digits), field=column)
    except AppError:
        errors.append(out_of_range)
        return None


def _unit_price(
    errors: list[RowError], row_no: int, value: str | None, currency: str | None
) -> tuple[str, int] | None:
    column = tpl.COL_UNIT_PRICE
    if value is None:
        return None
    if not _check_text(errors, row_no, column, value, required=True, max_length=MAX_VALUE_LENGTH):
        return None
    if currency is None:
        return None  # 통화 오류가 먼저 보고된다 — 최소단위 자릿수를 알 수 없다
    try:
        amount = parse_minor_amount(value, currency, field=column, max_digits=15)
    except AmountFormatError as exc:
        errors.append(RowError(row_no, column, E_FORMAT, f"{column}: {exc.reason}"))
        return None
    if amount < 1:
        errors.append(
            RowError(
                row_no,
                column,
                E_OUT_OF_RANGE,
                f"{column}은(는) 0보다 커야 합니다(무상 라인은 수주 접수 후 수주 편집에서 추가합니다).",
            )
        )
        return None
    return value, amount


def _ascii_code(
    errors: list[RowError], row_no: int, column: str, value: str | None, *, length: int
) -> str | None:
    """통화·시장 코드 공통 — 텍스트 검사 → **ASCII 먼저**(비ASCII의 `upper()`는 길이·글자가 바뀐다) → 대문자화."""
    if value is None:
        return None
    if not _check_text(errors, row_no, column, value, required=True, max_length=length):
        return None
    if not value.isascii():
        errors.append(
            RowError(
                row_no,
                column,
                E_FORMAT,
                f"{column}에 영문·숫자가 아닌 글자(전각 등)가 있습니다. 반각 영문으로 입력해 주세요.",
            )
        )
        return None
    return value.upper()


def parse_rows(parsed: ParsedFile, *, today: date, cp949: bool = False) -> ParsedCsv:
    """검증된 행(`ParsedFile`) → 그룹+오류 전수. 구조 오류(열 개수)도 같은 리포트로 합친다.

    `cp949=True`(UTF-8로 읽지 못해 CP949로 디코딩한 파일)면 코드 열의 '?'를 표현 불가 글자 손실로 보고 거부한다.
    """
    errors: list[RowError] = [
        RowError(p.row_no, None, E_ROW_STRUCTURE, p.reason) for p in parsed.problems
    ]
    groups: dict[tuple[str, str], CsvGroup] = {}
    seen_items: dict[tuple[str, str], dict[str, int]] = {}
    buyer_rows: dict[str, list[int]] = defaultdict(list)
    market_rows: dict[str, list[int]] = defaultdict(list)

    for row in parsed.rows:
        row_no = row.row_no
        before = len(errors)
        cells = {name: _cell(errors, row_no, name, row.cells[name]) for name in tpl.CSV_HEADER}

        buyer_code = cells[tpl.COL_BUYER_CODE]
        po_raw = cells[tpl.COL_PO_NO]
        item_code = cells[tpl.COL_ITEM_CODE]
        ok_buyer = _check_code(
            errors,
            row_no,
            tpl.COL_BUYER_CODE,
            buyer_code,
            max_length=MAX_BUYER_CODE_LENGTH,
            cp949=cp949,
        )
        ok_po = _check_code(
            errors, row_no, tpl.COL_PO_NO, po_raw, max_length=MAX_PO_LENGTH, cp949=cp949
        )
        ok_item = _check_code(
            errors,
            row_no,
            tpl.COL_ITEM_CODE,
            item_code,
            max_length=MAX_BUYER_ITEM_CODE,
            cp949=cp949,
        )
        if ok_buyer and buyer_code is not None:
            buyer_rows[buyer_code].append(row_no)

        po_key: str | None = None
        if ok_po and po_raw is not None:
            try:
                _, po_key = po_columns(po_raw)
            except AppError as exc:
                reason = next(iter(exc.detail.values()), "바이어 PO번호를 확인해 주세요.")
                errors.append(RowError(row_no, tpl.COL_PO_NO, E_FORMAT, str(reason)))

        ok_po_date, po_date = _date_cell(errors, row_no, tpl.COL_PO_DATE, cells[tpl.COL_PO_DATE])
        if po_date is not None and po_date > today:
            errors.append(
                RowError(
                    row_no,
                    tpl.COL_PO_DATE,
                    E_OUT_OF_RANGE,
                    "PO일자는 오늘(KST)보다 미래일 수 없습니다. 날짜를 확인해 주세요.",
                )
            )
            ok_po_date = False
        if po_date is not None and po_date < MIN_PO_DATE:
            errors.append(
                RowError(
                    row_no,
                    tpl.COL_PO_DATE,
                    E_OUT_OF_RANGE,
                    f"PO일자가 {MIN_PO_DATE.isoformat()}보다 앞서 있습니다. 날짜를 확인해 주세요.",
                )
            )
            ok_po_date = False

        currency: str | None = None
        code = _ascii_code(errors, row_no, tpl.COL_CURRENCY, cells[tpl.COL_CURRENCY], length=3)
        if code is not None:
            if code in CURRENCY_MINOR_UNITS:
                currency = code
            else:
                errors.append(
                    RowError(
                        row_no,
                        tpl.COL_CURRENCY,
                        E_UNKNOWN_CURRENCY,
                        f"지원하지 않는 통화입니다: {quote(code)}. USD·KRW 같은 3자리 통화 코드를 입력해 주세요.",
                    )
                )

        market: str | None = None
        upper_market = _ascii_code(errors, row_no, tpl.COL_MARKET, cells[tpl.COL_MARKET], length=2)
        if upper_market is not None:
            if _MARKET.match(upper_market):
                market = upper_market
                market_rows[market].append(row_no)
            else:
                errors.append(
                    RowError(
                        row_no,
                        tpl.COL_MARKET,
                        E_FORMAT,
                        f"{tpl.COL_MARKET} {quote(upper_market)}은(는) 2자리 국가 코드(예: US)가 아닙니다.",
                    )
                )

        quantity = _quantity(errors, row_no, cells[tpl.COL_QUANTITY])
        price = _unit_price(errors, row_no, cells[tpl.COL_UNIT_PRICE], currency)
        ok_delivery, delivery = _date_cell(
            errors, row_no, tpl.COL_DELIVERY, cells[tpl.COL_DELIVERY]
        )
        if delivery is not None and delivery < today:
            errors.append(
                RowError(
                    row_no,
                    tpl.COL_DELIVERY,
                    E_OUT_OF_RANGE,
                    "요청납기일은 오늘(KST)보다 앞설 수 없습니다. 날짜를 확인해 주세요.",
                )
            )
            ok_delivery = False

        if quantity is not None and price is not None:
            try:
                line_amount(quantity, price[1])
            except AppError:
                errors.append(
                    RowError(
                        row_no,
                        tpl.COL_UNIT_PRICE,
                        E_OUT_OF_RANGE,
                        "수량 × 단가(라인 금액)가 허용 범위를 넘었습니다. 수량·단가를 확인해 주세요.",
                    )
                )
                price = None

        # ── 그룹핑 — 바이어코드·PO키가 정상인 행만 묶는다(키를 못 만든 행은 위 오류로 이미 보고됐다).
        if ok_buyer and buyer_code is not None and po_raw is not None and po_key is not None:
            stripped = {name: cells[name] for name in tpl.HEADER_COLUMNS}
            key = (buyer_code, po_key)
            group = groups.get(key)
            if group is None:
                group = CsvGroup(
                    buyer_code=buyer_code,
                    po_no=po_raw,
                    po_key=po_key,
                    po_date=po_date,
                    currency=currency or (stripped[tpl.COL_CURRENCY] or "").upper(),
                    market=market or (stripped[tpl.COL_MARKET] or "").upper(),
                    first_row_no=row_no,
                    header_raw={name: row.cells[name] for name in tpl.HEADER_COLUMNS},
                )
                groups[key] = group
                seen_items[key] = {}
            else:
                _check_group_header(errors, row_no, group, stripped)
            group.row_nos.append(row_no)
            if ok_item and item_code is not None:
                group.items.append((row_no, item_code))
                first = seen_items[key].get(item_code)
                if first is not None:
                    errors.append(
                        RowError(
                            row_no,
                            tpl.COL_ITEM_CODE,
                            E_DUPLICATE_ITEM,
                            f"같은 PO 안에 같은 바이어품번이 {first}행에도 있습니다. 한 줄로 합쳐 수량을 더해 주세요.",
                        )
                    )
                else:
                    seen_items[key][item_code] = row_no
            if len(group.row_nos) > tpl.MAX_GROUP_LINES:
                errors.append(
                    RowError(
                        row_no,
                        None,
                        E_GROUP_LINE_LIMIT,
                        f"한 PO의 라인은 {tpl.MAX_GROUP_LINES}개까지입니다. 이 PO를 나누어 올려 주세요.",
                    )
                )
            if (
                len(errors) == before
                and quantity is not None
                and price is not None
                and ok_item
                and item_code is not None
                and ok_po_date
                and ok_delivery
            ):
                group.lines.append(
                    CsvLine(
                        row_no=row_no,
                        buyer_item_code=item_code,
                        quantity=quantity,
                        unit_price=price[0],
                        requested_delivery_date=delivery,
                        raw={name: row.cells[name] for name in tpl.CSV_HEADER},
                        amount=quantity * price[1],
                    )
                )
    for group in groups.values():
        total = sum(line.amount for line in group.lines)
        if total > MAX_SAFE_INTEGER:
            errors.append(
                RowError(
                    group.first_row_no,
                    tpl.COL_UNIT_PRICE,
                    E_OUT_OF_RANGE,
                    f"PO {quote(group.po_no)}의 합계 금액이 허용 범위를 넘었습니다. PO를 나누거나 수량·단가를 확인해 주세요.",
                )
            )
    return ParsedCsv(
        groups=list(groups.values()),
        errors=errors,
        total_rows=len(parsed.rows) + len(parsed.problems),
        buyer_rows=dict(buyer_rows),
        market_rows=dict(market_rows),
    )


def _check_group_header(
    errors: list[RowError], row_no: int, group: CsvGroup, cells: dict[str, str | None]
) -> None:
    """같은 PO 그룹의 헤더 값(앞 5열) 불일치 — **그 행의 그 열**에 오류(첫 행 값 기준). 대소문자만 다른 통화·시장 코드는 같은 값이다.

    이 행에서 셀 관문(`_cell`)을 이미 통과하지 못한 열(None)은 그 오류로 충분해 비교하지 않는다(같은 칸 오류 2건 방지).
    """
    first = {name: group.header_raw[name].strip() for name in tpl.HEADER_COLUMNS}
    for column in tpl.HEADER_COLUMNS:
        mine, base = cells[column], first[column]
        if mine is None:
            continue
        if column in (tpl.COL_CURRENCY, tpl.COL_MARKET):
            mine, base = mine.upper(), base.upper()
        if mine != base:
            errors.append(
                RowError(
                    row_no,
                    column,
                    E_GROUP_MISMATCH,
                    f"같은 PO({quote(group.po_no)})인데 {column}이(가) {group.first_row_no}행과 다릅니다. "
                    "한 PO의 앞 5개 열(바이어코드~목적지시장코드)은 모든 줄이 같아야 합니다.",
                )
            )
