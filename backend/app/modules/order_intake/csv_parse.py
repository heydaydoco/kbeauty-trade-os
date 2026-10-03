"""오더 인테이크 CSV 파서·행 검증 — **DB 없는 순수 함수** (S3-1 PR-14a / design-D D2).

입력은 `imports.parser.parse_csv`가 만든 행(수식 이스케이프 역변환·헤더 완전 일치·행 구조 오류 리포트는 그쪽 통로 그대로)이고, 여기서는 **셀 값 검증과 PO 그룹핑**만 한다.
원칙은 fail-visible이다 — **추측 변환 금지**: 모호한 날짜(`10/5/2026`·엑셀 일련번호·2자리 연도)·콤마 위치가 틀린 수량(`1,00`)·통화 최소단위를 넘는 소수 단가·지수 표기·전각 숫자·불가시 문자는
전부 **행 오류**로 보고하고 사용자가 고치게 한다(반올림·자동 보정 없음). 바이어코드·품번·PO번호는 **원문 유지**(대소문자 변환 없음 — 통화·시장 코드만 대문자화).

■ 그룹 키 = `(바이어코드 strip, buyer_po_no_key)`(비연속 행도 한 그룹). 같은 그룹 안에서 앞 5열(헤더 값)이 다르면 **그 행의 오류**다(어느 행이 다른지 행번호).
■ 모든 오류를 **한 번에** 모은다(첫 오류에서 멈추지 않음) — 부분 성공이 없으므로 운영자가 한 번에 고칠 수 있어야 한다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.core.errors.exceptions import AppError
from app.core.money import CURRENCY_MINOR_UNITS, AmountFormatError, parse_minor_amount
from app.modules.imports.parser import ParsedFile
from app.modules.order_intake import csv_template as tpl
from app.modules.order_intake.models import MAX_BUYER_ITEM_CODE
from app.modules.trade_docs.buyer_po import po_columns
from app.modules.trade_docs.constants import MAX_SAFE_INTEGER
from app.modules.trade_docs.snapshot import line_amount, validate_quantity

#: 셀 하나의 최대 길이(방어 — 어떤 열도 이보다 길 수 없다).
MAX_CELL_LENGTH = 200
MAX_BUYER_CODE_LENGTH = 40

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
_QTY_PLAIN = re.compile(r"^\d+(\.0+)?$", re.ASCII)
_QTY_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.0+)?$", re.ASCII)
#: 엑셀이 숫자로 오인해 바꾼 지수 표기(`1.23E+10`) — 코드 열에서는 원본 코드가 이미 손실된 값이다.
_EXPONENT = re.compile(r"^\d+(\.\d+)?[eE][+-]?\d+$", re.ASCII)
_MARKET = re.compile(r"^[A-Z]{2}$", re.ASCII)

#: 모양이 없는 채움 문자(한글 채움·점자 빈칸) — 카테고리 필터에 안 걸린다.
_BLANKS = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"})
#: 기본 무시 문자(Default_Ignorable) 중 범주가 Mn이라 카테고리 필터에 안 걸리는 것 — 결합 문자 잇기(CGJ)·변형 선택자·몽골 자유 변형 선택자·크메르 모음 고유음.
_IGNORABLE_MARKS = frozenset(
    {
        "\u034f",
        "\u17b4",
        "\u17b5",
        *map(chr, range(0x180B, 0x1810)),
        *map(chr, range(0xFE00, 0xFE10)),
        *map(chr, range(0xE0100, 0xE01F0)),
    }
)
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})
#: 오늘 이전 PO일자는 이 날짜 이후여야 한다(엑셀 서식 오염 방어 — 1900·0001 류).
_MIN_PO_DATE = date(2000, 1, 1)

# 오류 코드(행 리포트 `code`) — 프런트가 문구 대신 분기에 쓸 수 있는 안정 값.
E_REQUIRED = "REQUIRED"
E_TOO_LONG = "TOO_LONG"
E_INVISIBLE = "INVISIBLE_CHAR"
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
    """검증을 통과한 라인 1행 — `raw`는 정리 전 원본 셀(스냅샷용, 수식 이스케이프만 역변환)."""

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


@dataclass(slots=True)
class ParsedCsv:
    groups: list[CsvGroup]
    errors: list[RowError]
    total_rows: int


def _problem_of_invisible(text: str) -> str | None:
    """사람 눈에 안 보이거나 줄바꿈·탭 같은 제어 문자가 **값 안에** 있으면 그 설명을 돌려준다(앞뒤 공백은 호출자가 strip한 뒤다)."""
    for ch in text:
        category = unicodedata.category(ch)
        if (
            category in _INVISIBLE_CATEGORIES
            or ch in _BLANKS
            or ch in _IGNORABLE_MARKS
            or (category == "Zs" and ch != " ")
            or (ch.isspace() and ch != " ")
        ):
            return "눈에 보이지 않는 문자(줄바꿈·탭·제로폭 공백 등)"
    return None


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
    hidden = _problem_of_invisible(value)
    if hidden is not None:
        errors.append(
            RowError(
                row_no,
                column,
                E_INVISIBLE,
                f"{column}에 {hidden}가 들어 있습니다. 셀 내용을 지우고 다시 입력해 주세요.",
            )
        )
        return False
    return True


def _check_code(
    errors: list[RowError], row_no: int, column: str, value: str, *, max_length: int
) -> bool:
    """코드 열(바이어코드·PO번호·품번) — 텍스트 검사 + 지수 표기 오염 거부."""
    if not _check_text(errors, row_no, column, value, required=True, max_length=max_length):
        return False
    if _EXPONENT.match(value):
        errors.append(
            RowError(
                row_no,
                column,
                E_EXPONENT,
                f"{column} '{value}'은(는) 엑셀이 숫자로 오인해 지수 표기로 바꾼 값입니다(원래 값을 알 수 없음). "
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
    errors: list[RowError], row_no: int, column: str, value: str
) -> tuple[bool, date | None]:
    """날짜 열(선택) — 빈 칸은 '없음'. ISO 외 형식은 오류(엑셀 일련번호·`10/5/2026`·`2026/10/05`·2자리 연도 전부)."""
    if not value:
        return True, None
    if not _check_text(errors, row_no, column, value, required=False, max_length=10):
        return False, None
    parsed = parse_iso_date(value)
    if parsed is None:
        errors.append(
            RowError(
                row_no,
                column,
                E_DATE,
                f"{column} '{value}'은(는) 날짜 형식이 아닙니다. 2026-10-05 형식(연-월-일)으로 입력해 주세요"
                "(엑셀 셀 서식을 yyyy-mm-dd로 지정).",
            )
        )
        return False, None
    return True, parsed


def _quantity(errors: list[RowError], row_no: int, value: str) -> int | None:
    column = tpl.COL_QUANTITY
    if not _check_text(errors, row_no, column, value, required=True, max_length=40):
        return None
    if _QTY_PLAIN.match(value):
        digits = value.split(".", 1)[0]
    elif _QTY_GROUPED.match(value):
        digits = value.split(".", 1)[0].replace(",", "")
    else:
        errors.append(
            RowError(
                row_no,
                column,
                E_FORMAT,
                f"{column} '{value}'은(는) 정수가 아닙니다. 1000 또는 1,000처럼 정수(EA)로 입력해 주세요"
                "(소수·지수 표기·전각 숫자 불가).",
            )
        )
        return None
    try:
        return validate_quantity(int(digits), field=column)
    except AppError:
        errors.append(
            RowError(
                row_no,
                column,
                E_OUT_OF_RANGE,
                f"{column} '{value}'은(는) 범위를 벗어났습니다. 1 이상 99,999,999 이하 정수로 입력해 주세요.",
            )
        )
        return None


def _unit_price(
    errors: list[RowError], row_no: int, value: str, currency: str | None
) -> tuple[str, int] | None:
    column = tpl.COL_UNIT_PRICE
    if not _check_text(errors, row_no, column, value, required=True, max_length=40):
        return None
    if currency is None:
        return None  # 통화 오류가 먼저 보고된다 — 최소단위 자릿수를 알 수 없다
    try:
        amount = parse_minor_amount(value, currency, field=column, max_digits=15)
    except AmountFormatError as exc:
        errors.append(RowError(row_no, column, E_FORMAT, f"{column} '{value}': {exc.reason}"))
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


def parse_rows(parsed: ParsedFile, *, today: date) -> ParsedCsv:
    """검증된 행(`ParsedFile`) → 그룹+오류 전수. 구조 오류(열 개수)도 같은 리포트로 합친다."""
    errors: list[RowError] = [
        RowError(p.row_no, None, E_ROW_STRUCTURE, p.reason) for p in parsed.problems
    ]
    groups: dict[tuple[str, str], CsvGroup] = {}
    seen_items: dict[tuple[str, str], dict[str, int]] = {}

    for row in parsed.rows:
        row_no = row.row_no
        cells = {name: row.cells[name].strip() for name in tpl.CSV_HEADER}
        before = len(errors)

        buyer_code = cells[tpl.COL_BUYER_CODE]
        po_raw = cells[tpl.COL_PO_NO]
        item_code = cells[tpl.COL_ITEM_CODE]
        ok_buyer = _check_code(
            errors, row_no, tpl.COL_BUYER_CODE, buyer_code, max_length=MAX_BUYER_CODE_LENGTH
        )
        ok_po = _check_code(errors, row_no, tpl.COL_PO_NO, po_raw, max_length=60)
        ok_item = _check_code(
            errors, row_no, tpl.COL_ITEM_CODE, item_code, max_length=MAX_BUYER_ITEM_CODE
        )

        po_key: str | None = None
        if ok_po:
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
        if po_date is not None and po_date < _MIN_PO_DATE:
            errors.append(
                RowError(
                    row_no,
                    tpl.COL_PO_DATE,
                    E_OUT_OF_RANGE,
                    "PO일자가 2000-01-01보다 앞서 있습니다. 날짜를 확인해 주세요.",
                )
            )
            ok_po_date = False

        currency: str | None = None
        raw_currency = cells[tpl.COL_CURRENCY]
        if _check_text(errors, row_no, tpl.COL_CURRENCY, raw_currency, required=True, max_length=3):
            code = raw_currency.upper()
            if code in CURRENCY_MINOR_UNITS:
                currency = code
            else:
                errors.append(
                    RowError(
                        row_no,
                        tpl.COL_CURRENCY,
                        E_UNKNOWN_CURRENCY,
                        f"지원하지 않는 통화입니다: {raw_currency}. USD·KRW 같은 3자리 통화 코드를 입력해 주세요.",
                    )
                )

        market: str | None = None
        raw_market = cells[tpl.COL_MARKET]
        if _check_text(errors, row_no, tpl.COL_MARKET, raw_market, required=True, max_length=2):
            if _MARKET.match(raw_market.upper()):
                market = raw_market.upper()
            else:
                errors.append(
                    RowError(
                        row_no,
                        tpl.COL_MARKET,
                        E_FORMAT,
                        f"{tpl.COL_MARKET} '{raw_market}'은(는) 2자리 국가 코드(예: US)가 아닙니다.",
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
        if ok_buyer and po_key is not None:
            key = (buyer_code, po_key)
            group = groups.get(key)
            if group is None:
                group = CsvGroup(
                    buyer_code=buyer_code,
                    po_no=po_raw,
                    po_key=po_key,
                    po_date=po_date,
                    currency=currency or raw_currency.upper(),
                    market=market or raw_market.upper(),
                    first_row_no=row_no,
                    header_raw={name: row.cells[name] for name in tpl.HEADER_COLUMNS},
                )
                groups[key] = group
                seen_items[key] = {}
            else:
                _check_group_header(errors, row_no, group, cells)
            group.row_nos.append(row_no)
            if ok_item:
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
                    f"PO({group.po_no})의 합계 금액이 허용 범위를 넘었습니다. PO를 나누거나 수량·단가를 확인해 주세요.",
                )
            )
    return ParsedCsv(
        groups=list(groups.values()),
        errors=errors,
        total_rows=len(parsed.rows) + len(parsed.problems),
    )


def _check_group_header(
    errors: list[RowError], row_no: int, group: CsvGroup, cells: dict[str, str]
) -> None:
    """같은 PO 그룹의 헤더 값(앞 5열) 불일치 — **그 행의 그 열**에 오류(첫 행 값 기준). 대소문자만 다른 통화·시장 코드는 같은 값이다."""
    first = {name: group.header_raw[name].strip() for name in tpl.HEADER_COLUMNS}
    for column in tpl.HEADER_COLUMNS:
        mine, base = cells[column], first[column]
        if column in (tpl.COL_CURRENCY, tpl.COL_MARKET):
            mine, base = mine.upper(), base.upper()
        if mine != base:
            errors.append(
                RowError(
                    row_no,
                    column,
                    E_GROUP_MISMATCH,
                    f"같은 PO({group.po_no})인데 {column}이(가) {group.first_row_no}행과 다릅니다. "
                    "한 PO의 앞 5개 열(바이어코드~목적지시장코드)은 모든 줄이 같아야 합니다.",
                )
            )
