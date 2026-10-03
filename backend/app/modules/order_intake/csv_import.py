"""오더 인테이크 CSV 입구 — `POST /order-intakes/import-csv` (S3-1 PR-14a / design-D D2 / ADR-0072).

■ **파일 전체 원자**: 한 행이라도 오류면 **아무것도 착지하지 않고** 모든 오류를 한 번에 리포트한다(부분 성공 없음 — 부분 착지 뒤 수정본 재업로드는 sha256이 달라 이미 착지된 PO가 중복으로 충돌한다).
  통과하면 PO 그룹마다 `register_intake`(착지 단일 통로)를 **한 트랜잭션**에서 부른다 — 이 모듈은 `OrderIntake`를 직접 만들지 않는다(AST 스캔). 인테이크는 항상 PENDING이다.
■ 한 트랜잭션의 순서: 파일 해시 advisory lock → 멱등 claim(재생이면 최초 결과) → 같은 파일의 PENDING 인테이크 선조회(409 `FILE.DUPLICATE`) → 바이어·시장·중복 PO·중복 SKU 검증(서버 마스터) →
  오류가 있으면 422 `FILE.INVALID_ROWS`(중복 PO만 있으면 13a와 같은 409 `DUPLICATE_BUYER_PO`) → 그룹별 착지(`(바이어, PO키)` 정렬 순 — 동시 업로드 교착 방지) → 멱등 완료.
■ 동시 같은 파일: sha256 advisory lock(LOCK_ORDER (−1) — 트랜잭션 첫 문장)이 직렬화한다 — 먼저 온 쪽이 **lock_timeout(5s, `kbos_app` 역할 설정) 이내에** 커밋하면
  뒤에 온 쪽은 같은 키면 최초 결과 재생, 다른 키면 `FILE.DUPLICATE` 409(결정적). 5초를 넘기면 뒤에 온 쪽은 409 `COMMON.CONCURRENCY.LOCK_BUSY`(같은 키로 재시도).
  DB 부분 유니크(`…source_sha256_source_group_key_pending`)가 최종 방어다. 교착 방지 범위: 이 입구의 행 잠금은 `(거래처, PO키)` 정렬 착지뿐이고(인테이크 INSERT의
  유니크 대기·거래처 KEY SHARE), 임포트 확정의 다중 행 FOR UPDATE는 id 오름차순이다(`imports.registry`) — 그 밖의 경로와의 교착은 전역 LOCK_ORDER가 맡는다.
■ **원본 바이트는 보관하지 않는다**(S3-1 — 설계 D2): 출처는 `extracted_snapshot`(행마다 9열 원본 셀 무손실)·`source_sha256`·`original_filename`으로 특정한다(원본 보관은 S6-1).
■ 파싱·셀 검증은 `csv_parse`(순수 함수)이고, 파일 읽기·디코딩·헤더·수식 이스케이프는 `imports` 모듈의 공개 통로를 그대로 쓴다(`parse_csv(strict=True)`).
  그 앞에 **저장 없는 계수 패스**(헤더 진단·50,000행 초과 즉시 중단·CSV 문법 오류의 물리 줄 번호)를 둔다 — 행 객체를 만들기 전에 거부해 메모리를 상수로 묶는다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any, BinaryIO

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.documents.service import sanitize_filename
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.imports import parser
from app.modules.imports import service as imports_service
from app.modules.markets.models import Market
from app.modules.order_intake import csv_parse as cp
from app.modules.order_intake import csv_template as tpl
from app.modules.order_intake import service as intake_service
from app.modules.order_intake.models import IntakeSourceKind, OrderIntake, OrderIntakeLine
from app.modules.order_intake.schemas import IntakeHeaderIn, IntakeLineIn
from app.modules.partners.models import Partner, PartnerTypeLink
from app.modules.trade_docs.views import money_text

IMPORT_ENDPOINT = "POST /api/v1/order-intakes/import-csv"
#: 헤더 불일치 진단에 싣는 열 차이의 최대 개수.
HEADER_DIFF_LIMIT = 20

#: 엑셀 확장자 — 전용 메시지(그 밖의 비-CSV는 `IMPORTS.FILE.TYPE_NOT_ALLOWED`).
_EXCEL_SUFFIXES = (".xlsx", ".xls", ".xlsm", ".xlsb")
#: 엑셀 바이트 시그니처 — xlsx(zip) `PK\x03\x04`, 구 xls(OLE) `D0CF11E0` — 확장자를 `.csv`로 위장해도 거부한다.
_EXCEL_SIGNATURES = (b"PK\x03\x04", bytes.fromhex("D0CF11E0"))


def template_header() -> tuple[str, ...]:
    """다운로드 양식 헤더 — 업로드 검증이 쓰는 상수와 **같은 객체**다(양식 왕복)."""
    return tpl.CSV_HEADER


# ── 파일 읽기·파싱 (DB 없음) ───────────────────────────────────────────────────


def _read_upload(stream: BinaryIO, filename: str) -> tuple[bytes, str, str]:
    """(원문 바이트, 정리한 파일명, sha256) — 확장자·크기·엑셀 시그니처·빈 파일 검사."""
    original = sanitize_filename(filename)
    if original.lower().endswith(_EXCEL_SUFFIXES):
        raise AppError(ErrorCode.ORDER_INTAKE_FILE_UNSUPPORTED_FORMAT)
    imports_service.validate_extension(original)
    raw = imports_service.read_limited(stream)
    if not raw:
        raise AppError(ErrorCode.IMPORTS_FILE_EMPTY)
    if raw.startswith(_EXCEL_SIGNATURES):
        raise AppError(ErrorCode.ORDER_INTAKE_FILE_UNSUPPORTED_FORMAT)
    return raw, original, hashlib.sha256(raw).hexdigest()


def _utf8_error_line(raw: bytes) -> int | None:
    """UTF-8(BOM 허용)로 읽히면 None, 아니면 처음 읽지 못한 바이트가 있는 물리 줄 번호(1부터)."""
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return raw[: exc.start].count(b"\n") + 1
    return None


def _decode(raw: bytes) -> tuple[str, int | None]:
    """(본문, UTF-8 실패 줄) — 디코딩은 공유 통로(`decode_upload`: UTF-8 BOM → CP949). 둘 다 실패하면 줄 번호를 실어 422."""
    if b"\x00" in raw:  # NUL 바이트 — 텍스트 파일이 아니다(csv 모듈 동작도 버전마다 다르다)
        raise AppError(ErrorCode.IMPORTS_FILE_ENCODING_INVALID)
    utf8_line = _utf8_error_line(raw)
    try:
        return parser.decode_upload(raw), utf8_line
    except AppError as exc:
        raise AppError(
            exc.code,
            detail={
                "file": f"{utf8_line}번째 줄에서 UTF-8로도 CP949로도 읽을 수 없는 글자를 만났습니다. "
                "엑셀에서 'CSV UTF-8(쉼표로 분리)'로 다시 저장해 올려 주세요.",
                "line_no": utf8_line,
            },
        ) from None


def _cut(value: str | None) -> str:
    if value is None:
        return "(없음)"
    return repr(value if len(value) <= cp.QUOTE_LIMIT else value[: cp.QUOTE_LIMIT] + "…")


def _check_header(actual: list[str], utf8_line: int | None) -> None:
    """헤더 진단 — 끝에 빈 열만 붙은 경우는 전용 문구(자동으로 잘라 내지 않고 거부), 그 밖은 열 단위 차이를 repr로(최대 20열·열당 40자)."""
    expected = tpl.CSV_HEADER
    if tuple(actual) == expected:
        return
    note = (
        ""
        if utf8_line is None
        else f" (이 파일은 {utf8_line}번째 줄에서 UTF-8로 읽지 못해 CP949로 읽었습니다 — 한글이 깨졌다면 'CSV UTF-8'로 다시 저장해 주세요.)"
    )
    trimmed = list(actual)
    while trimmed and trimmed[-1].strip() == "":
        trimmed.pop()
    if tuple(trimmed) == expected:
        extra = len(actual) - len(trimmed)
        raise AppError(
            ErrorCode.IMPORTS_FILE_HEADER_MISMATCH,
            detail={
                "header": f"첫 행 오른쪽 끝에 빈 열이 {extra}개 붙어 있습니다(엑셀 표 오른쪽에 공백·서식만 남은 열). "
                "빈 열을 삭제한 뒤 다시 저장해 올려 주세요 — 자동으로 잘라 내지 않습니다." + note,
                "trailing_empty_columns": extra,
            },
        )
    differences = [
        (
            index + 1,
            expected[index] if index < len(expected) else None,
            actual[index] if index < len(actual) else None,
        )
        for index in range(max(len(expected), len(actual)))
        if (expected[index] if index < len(expected) else None)
        != (actual[index] if index < len(actual) else None)
    ]
    shown = differences[:HEADER_DIFF_LIMIT]
    parts = "; ".join(f"{no}열: 기대 {_cut(exp)} / 파일 {_cut(act)}" for no, exp, act in shown)
    more = f" 외 {len(differences) - len(shown)}열" if len(differences) > len(shown) else ""
    raise AppError(
        ErrorCode.IMPORTS_FILE_HEADER_MISMATCH,
        detail={
            "header": f"첫 행(컬럼 제목)이 표준 양식과 다릅니다 — {parts}{more}." + note,
            "differences": [
                {"column_no": no, "expected": exp, "actual": _cut(act)} for no, exp, act in shown
            ],
        },
    )


def _syntax_error(line_no: int, reason: str) -> AppError:
    """CSV 문법 오류 → 한국어 리포트(물리 줄 번호). 영문 원문은 로그 컨텍스트에만 남긴다."""
    if "field larger than field limit" in reason:
        what = "셀 하나가 너무 깁니다"
    else:
        what = "따옴표가 닫히지 않았거나 따옴표 뒤에 글자가 있습니다"
    return _report(
        [
            cp.RowError(
                None,
                None,
                cp.E_CSV_SYNTAX,
                f'{line_no}번째 줄 근처에서 CSV를 읽지 못했습니다: {what}. 해당 셀의 따옴표(")와 줄바꿈을 확인해 주세요.',
            )
        ],
        log_context={"csv_error": reason[:200], "line_no": line_no},
    )


def _too_many_rows() -> AppError:
    limit = imports_service.IMPORT_MAX_ROWS
    return AppError(
        ErrorCode.VALIDATION_INVALID_FIELD,
        detail={
            "file": f"행이 너무 많습니다({limit:,}행 초과). {limit:,}행 이하로 나누어 올려 주세요."
        },
    )


def _precount(text_body: str, utf8_line: int | None) -> None:
    """**저장 없는 계수 패스** — 헤더 진단 후 비지 않은 레코드만 세다가 상한을 넘는 즉시 중단한다(행 객체를 만들기 전에 거부).

    `parse_csv`와 같은 판정(모든 셀이 빈 문자열인 레코드는 데이터가 아니다)·같은 strict 리더라 두 패스의 결론이 갈리지 않는다.
    """
    reader = csv.reader(io.StringIO(text_body, newline=""), strict=True)
    count = 0
    try:
        header = next(reader, None)
        if header is None:
            raise AppError(ErrorCode.IMPORTS_FILE_EMPTY)
        _check_header(header, utf8_line)
        for record in reader:
            if any(record):
                count += 1
                if count > imports_service.IMPORT_MAX_ROWS:
                    raise _too_many_rows()
    except csv.Error as exc:
        raise _syntax_error(reader.line_num, str(exc)) from None
    if count == 0:
        raise AppError(ErrorCode.IMPORTS_FILE_EMPTY)


def _parse_upload(raw: bytes) -> cp.ParsedCsv:
    text_body, utf8_line = _decode(raw)
    _precount(text_body, utf8_line)
    try:
        parsed = parser.parse_csv(
            text_body, header=tpl.CSV_HEADER, string_columns=tpl.STRING_COLUMNS, strict=True
        )
    except parser.CsvSyntaxError as exc:  # 계수 패스와 같은 리더라 도달하지 않지만 방어
        raise _syntax_error(exc.line_no, exc.reason) from None
    return cp.parse_rows(parsed, today=today_kst(), cp949=utf8_line is not None)


# ── 오류 리포트 ────────────────────────────────────────────────────────────────


#: 리포트 상한(200)을 채울 때 먼저 싣는 오류 — 형식 오류 수천 건에 가려지면 안 되는 것(중복 PO·마스터 미등록).
PRIORITY_CODES: tuple[str, ...] = (
    cp.E_DUPLICATE_PO,
    cp.E_BUYER_UNKNOWN,
    cp.E_BUYER_NOT_BUYER,
    cp.E_MARKET_UNKNOWN,
)


def _report(
    errors: Sequence[cp.RowError], *, log_context: dict[str, Any] | None = None
) -> AppError:
    """오류 전수 → 응답. 최대 N건을 싣되 **중복 PO·마스터 오류를 먼저** 채우고(행·열 순으로 정렬해 표시), 나머지는 개수만 알린다.

    detail = `{errors, total_errors, omitted_errors, counts_by_code}`. **중복 PO만** 있으면 13a와 같은 409, 그 밖은 422 `FILE.INVALID_ROWS`.
    """
    column_order = {name: index for index, name in enumerate(tpl.CSV_HEADER)}

    def position(e: cp.RowError) -> tuple[int, int, str]:
        return (e.row_no or 0, column_order.get(e.column or "", -1), e.code)

    ordered = sorted(errors, key=position)
    rank = {code: index for index, code in enumerate(PRIORITY_CODES)}
    first = sorted(
        (e for e in ordered if e.code in rank), key=lambda e: (rank[e.code], *position(e))
    )
    rest = [e for e in ordered if e.code not in rank]
    shown = sorted((first + rest)[: tpl.MAX_REPORTED_ERRORS], key=position)
    only_duplicate_po = bool(ordered) and all(e.code == cp.E_DUPLICATE_PO for e in ordered)
    code = (
        ErrorCode.TRADE_DOCS_DOCUMENT_DUPLICATE_BUYER_PO
        if only_duplicate_po
        else ErrorCode.ORDER_INTAKE_FILE_INVALID_ROWS
    )
    return AppError(
        code,
        detail={
            "errors": [e.as_dict() for e in shown],
            "total_errors": len(ordered),
            "omitted_errors": len(ordered) - len(shown),
            "counts_by_code": dict(sorted(Counter(e.code for e in ordered).items())),
        },
        log_context=log_context,
    )


# ── 서버 마스터 검증 (읽기 전용) ────────────────────────────────────────────────


def _master_errors(
    session: Session, parsed: cp.ParsedCsv
) -> tuple[list[cp.RowError], dict[str, int]]:
    """바이어·시장 등록 여부 → (행 오류, 바이어코드→거래처 id). 형식을 통과한 **모든 행**(다른 열이 틀린 행 포함)을 본다.

    바이어코드는 **정확 일치**(대소문자 다르면 오류+힌트, 자동 매칭 없음)다.
    """
    errors: list[cp.RowError] = []
    codes = set(parsed.buyer_rows)
    found = {
        str(code): int(pid)
        for pid, code in session.execute(
            select(Partner.id, Partner.partner_code).where(
                Partner.partner_code.in_(codes), Partner.deleted_at.is_(None)
            )
        ).all()
    }
    buyer_ids = set(
        session.execute(
            select(PartnerTypeLink.partner_id).where(
                PartnerTypeLink.partner_id.in_(set(found.values())),
                PartnerTypeLink.type_code == "BUYER",
                PartnerTypeLink.deleted_at.is_(None),
            )
        ).scalars()
    )
    missing = {code for code in codes if code not in found}
    hints: dict[str, str] = {}
    if missing:
        lowered = {code.lower() for code in missing}
        for (code,) in session.execute(
            select(Partner.partner_code).where(
                func.lower(Partner.partner_code).in_(lowered), Partner.deleted_at.is_(None)
            )
        ).all():
            hints.setdefault(str(code).lower(), str(code))
    buyers: dict[str, int] = {}
    for code in sorted(codes):
        rows = parsed.buyer_rows[code]
        if code not in found:
            hint = hints.get(code.lower())
            suffix = (
                f" 혹시 {cp.quote(hint)}인가요? 코드의 대소문자까지 같아야 합니다." if hint else ""
            )
            message = f"등록되지 않은 바이어코드입니다: {cp.quote(code)}.{suffix} 거래처 관리에서 확인해 주세요."
            errors += [
                cp.RowError(r, tpl.COL_BUYER_CODE, cp.E_BUYER_UNKNOWN, message) for r in rows
            ]
        elif found[code] not in buyer_ids:
            message = (
                f"바이어 유형의 거래처가 아닙니다: {cp.quote(code)}. 거래처 유형을 확인해 주세요."
            )
            errors += [
                cp.RowError(r, tpl.COL_BUYER_CODE, cp.E_BUYER_NOT_BUYER, message) for r in rows
            ]
        else:
            buyers[code] = found[code]

    markets = set(parsed.market_rows)
    known = set(
        session.execute(
            select(Market.code).where(Market.code.in_(markets), Market.deleted_at.is_(None))
        ).scalars()
    )
    for market in sorted(markets - known):
        message = f"등록되지 않은 시장입니다: {cp.quote(market)}. 시장 관리에서 먼저 등록해 주세요."
        errors += [
            cp.RowError(r, tpl.COL_MARKET, cp.E_MARKET_UNKNOWN, message)
            for r in parsed.market_rows[market]
        ]
    return errors, buyers


def _po_and_sku_errors(
    session: Session, groups: Sequence[cp.CsvGroup], buyers: dict[str, int]
) -> list[cp.RowError]:
    """중복 PO(PENDING 인테이크·비취소 SO 점유 — 거래처마다 **일괄 2쿼리**)와 **같은 SKU로 매핑되는 서로 다른 품번**(한 PO 안, 품번 형식을 통과한 전 행).

    품번 미매핑은 오류가 아니다(`sku_id NULL`로 착지). 질의 수는 거래처 수에 비례하고 PO 수와 무관하다.
    """
    errors: list[cp.RowError] = []
    by_buyer: dict[int, list[cp.CsvGroup]] = defaultdict(list)
    for group in groups:
        if group.buyer_code in buyers:
            by_buyer[buyers[group.buyer_code]].append(group)
    for partner_id, owned in by_buyer.items():
        resolved = intake_service.resolve_codes(
            session, partner_id, [code for g in owned for _, code in g.items]
        )
        held = intake_service.occupants(session, partner_id, [g.po_key for g in owned])
        for group in owned:
            found = held.get(group.po_key)
            if found is not None:
                if "intake_id" in found:
                    who = f"검토 대기 중인 오더 인테이크(#{found['intake_id']})"
                else:
                    who = f"수주 {found.get('doc_number')}({found.get('status')})"
                errors.append(
                    cp.RowError(
                        group.first_row_no,
                        tpl.COL_PO_NO,
                        cp.E_DUPLICATE_PO,
                        f"이 바이어 PO번호는 이미 {who}가 사용 중입니다. "
                        "기존 건을 거부·취소한 뒤 다시 올리거나 PO번호를 확인해 주세요.",
                    )
                )
            seen_sku: dict[int, tuple[int, str]] = {}
            for row_no, code in group.items:
                hit = resolved.get(code)
                if hit is None:
                    continue
                first_seen = seen_sku.get(hit.sku_id)
                if first_seen is not None and first_seen[1] == code:
                    continue  # 같은 품번 두 줄은 파서가 이미 DUPLICATE_ITEM으로 보고했다(같은 칸 오류 2건 방지)
                if first_seen is not None:
                    first = first_seen[0]
                    errors.append(
                        cp.RowError(
                            row_no,
                            tpl.COL_ITEM_CODE,
                            cp.E_DUPLICATE_SKU,
                            f"{first}행의 품번과 같은 SKU로 매핑됩니다. 수주는 SKU마다 1줄이므로 한 줄로 합쳐 주세요.",
                        )
                    )
                else:
                    seen_sku[hit.sku_id] = (row_no, code)
    return errors


#: 착지 통로(`register_intake`) 재검사의 필드 경로 → CSV 열(KST 자정 경계 등 — B8).
_HEADER_FIELD_COLUMNS = {
    "buyer_partner_id": tpl.COL_BUYER_CODE,
    "buyer_po_no": tpl.COL_PO_NO,
    "buyer_po_date": tpl.COL_PO_DATE,
    "currency": tpl.COL_CURRENCY,
    "dest_market_code": tpl.COL_MARKET,
}
_LINE_FIELD_COLUMNS = {
    "buyer_item_code": tpl.COL_ITEM_CODE,
    "quantity": tpl.COL_QUANTITY,
    "unit_price": tpl.COL_UNIT_PRICE,
    "requested_delivery_date": tpl.COL_DELIVERY,
}
_LINE_FIELD = re.compile(r"^lines\[(\d+)\]\.(\w+)$")


def _landing_errors(group: cp.CsvGroup, exc: AppError) -> list[cp.RowError]:
    """착지 통로의 입력 검증 422(파서 검사 뒤 자정을 넘긴 납기·PO일자 등)를 그 행·열의 리포트로 번역한다."""
    out: list[cp.RowError] = []
    for field_path, message in exc.detail.items():
        match = _LINE_FIELD.match(str(field_path))
        if match and int(match.group(1)) < len(group.lines):
            row_no = group.lines[int(match.group(1))].row_no
            column = _LINE_FIELD_COLUMNS.get(match.group(2))
        else:
            row_no = group.first_row_no
            column = _HEADER_FIELD_COLUMNS.get(str(field_path))
        out.append(cp.RowError(row_no, column, cp.E_OUT_OF_RANGE, str(message)))
    return out or [
        cp.RowError(
            group.first_row_no,
            None,
            cp.E_OUT_OF_RANGE,
            "등록 직전 재검사에서 거부됐습니다. 내용을 확인해 다시 올려 주세요.",
        )
    ]


# ── 스냅샷·응답 ────────────────────────────────────────────────────────────────


def _snapshot(group: cp.CsvGroup, *, filename: str, sha256: str) -> dict[str, Any]:
    """CSV 불변 원본 — MANUAL 스냅샷과 같은 렌더 키(`kind·header·lines[].buyer_item_code…`)에 파일 출처·엑셀 행번호를 더한다.

    값은 **원본 셀 문자열 그대로**(서버 해석값은 싣지 않는다). `header`는 PO 첫 행의 5셀이고, 행마다 `header_cells`(그 행의 헤더 5셀 원문)를 함께 실어
    9열 전부가 행 단위로 무손실이다(같은 PO 안에서 대소문자만 다른 통화·시장 코드도 그 행 원문대로 남는다).
    """
    return {
        "kind": IntakeSourceKind.CSV.value,
        "parser_version": tpl.PARSER_VERSION,
        "file": {"original_filename": filename, "sha256": sha256},
        "header": {
            "buyer_code": group.header_raw[tpl.COL_BUYER_CODE],
            "buyer_po_no": group.header_raw[tpl.COL_PO_NO],
            "buyer_po_date": group.header_raw[tpl.COL_PO_DATE],
            "currency": group.header_raw[tpl.COL_CURRENCY],
            "dest_market_code": group.header_raw[tpl.COL_MARKET],
        },
        "lines": [
            {
                "row_no": line.row_no,
                "buyer_item_code": line.raw[tpl.COL_ITEM_CODE],
                "quantity": line.raw[tpl.COL_QUANTITY],
                "unit_price": line.raw[tpl.COL_UNIT_PRICE],
                "requested_delivery_date": line.raw[tpl.COL_DELIVERY],
                "header_cells": {
                    "buyer_code": line.raw[tpl.COL_BUYER_CODE],
                    "buyer_po_no": line.raw[tpl.COL_PO_NO],
                    "buyer_po_date": line.raw[tpl.COL_PO_DATE],
                    "currency": line.raw[tpl.COL_CURRENCY],
                    "dest_market_code": line.raw[tpl.COL_MARKET],
                },
            }
            for line in group.lines
        ],
    }


def _summaries(
    session: Session, landed: Sequence[tuple[cp.CsvGroup, OrderIntake]]
) -> list[dict[str, Any]]:
    """착지 결과 요약(파일 순서) — 라인 수·미매핑 수·합계 집계 1쿼리 + 바이어명 1쿼리 = **2쿼리**(라인·PO 수와 무관)."""
    ids = [row.id for _, row in landed]
    stats = {
        int(i): (int(n), int(unmapped), int(total))
        for i, n, unmapped, total in session.execute(
            select(
                OrderIntakeLine.intake_id,
                func.count(),
                func.count().filter(OrderIntakeLine.sku_id.is_(None)),
                func.sum(OrderIntakeLine.quantity * OrderIntakeLine.unit_price_amount),
            )
            .where(OrderIntakeLine.intake_id.in_(ids), OrderIntakeLine.deleted_at.is_(None))
            .group_by(OrderIntakeLine.intake_id)
        ).all()
    }
    names = {
        int(pid): str(name)
        for pid, name in session.execute(
            select(Partner.id, Partner.name_ko).where(
                Partner.id.in_({row.buyer_partner_id for _, row in landed})
            )
        ).all()
    }
    out: list[dict[str, Any]] = []
    for group, row in sorted(landed, key=lambda pair: pair[0].first_row_no):
        count, unmapped, total = stats.get(row.id, (0, 0, 0))
        out.append(
            {
                "id": row.id,
                "version": row.version,
                "buyer_partner_id": row.buyer_partner_id,
                "buyer_name": names.get(row.buyer_partner_id),
                "buyer_po_no": row.buyer_po_no,
                "currency": row.currency,
                "dest_market_code": row.dest_market_code,
                "line_count": count,
                "unmapped_line_count": unmapped,
                "total_amount": total,
                "total_text": money_text(total, row.currency),
                "first_row_no": group.first_row_no,
            }
        )
    return out


# ── 엔드포인트 서비스 ───────────────────────────────────────────────────────────


def import_csv(
    *, actor: AuthenticatedUser, idempotency_key: str, stream: BinaryIO, filename: str
) -> tuple[int, dict[str, Any]]:
    """CSV 업로드 → 인테이크 N건 일괄 착지(전부 또는 전무). 201 본문 = `CsvImportResult`."""
    intake_service.require_intake_writer(actor)
    raw, original, sha256 = _read_upload(stream, filename)
    # 파싱·셀 검증은 CPU 작업이라 트랜잭션 밖이다(실패가 흔한 검증을 DB 잠금 없이 끝낸다).
    parsed = _parse_upload(raw)
    if len(parsed.groups) > tpl.MAX_GROUPS:
        raise AppError(
            ErrorCode.ORDER_INTAKE_FILE_TOO_MANY_GROUPS,
            detail={"groups": len(parsed.groups), "max_groups": tpl.MAX_GROUPS},
        )

    with unit_of_work() as uow:
        session = uow.session
        # LOCK_ORDER (−1) — 같은 파일의 동시 업로드를 직렬화한다. 앞 트랜잭션이 lock_timeout(5s) 이내에 끝나면 뒤에 온 쪽은 재생/409를
        # 결정적으로 보고, 넘기면 409 LOCK_BUSY(같은 키로 재시도)다.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"order_intake_csv:{sha256}"},
        )
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=IMPORT_ENDPOINT,
            key=idempotency_key,
            request_body={"sha256": sha256, "filename": original},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        pending = list(
            session.execute(
                select(OrderIntake.id)
                .where(
                    OrderIntake.source_sha256 == sha256,
                    OrderIntake.status == "PENDING",
                    OrderIntake.deleted_at.is_(None),
                )
                .order_by(OrderIntake.id)
            ).scalars()
        )
        if pending:
            raise AppError(
                ErrorCode.ORDER_INTAKE_FILE_DUPLICATE,
                detail={"intake_ids": [int(i) for i in pending]},
            )

        errors = list(parsed.errors)
        master_errors, buyers = _master_errors(session, parsed)
        errors += master_errors
        errors += _po_and_sku_errors(session, parsed.groups, buyers)
        if errors:
            raise _report(errors)

        landed: list[tuple[cp.CsvGroup, OrderIntake]] = []
        for group in sorted(parsed.groups, key=lambda g: (buyers[g.buyer_code], g.po_key)):
            partner_id = buyers[group.buyer_code]
            try:
                row = _land(session, actor, group, partner_id, filename=original, sha256=sha256)
            except AppError as exc:
                if exc.code is not ErrorCode.VALIDATION_INVALID_FIELD:
                    raise
                raise _report(
                    _landing_errors(group, exc), log_context={"phase": "landing"}
                ) from None
            landed.append((group, row))

        intakes = _summaries(session, landed)
        body: dict[str, Any] = {
            "original_filename": original,
            "source_sha256": sha256,
            "row_count": parsed.total_rows,
            "group_count": len(intakes),
            "line_count": sum(i["line_count"] for i in intakes),
            "unmapped_line_count": sum(i["unmapped_line_count"] for i in intakes),
            "intakes": intakes,
        }
        body = intake_service.jsonable(body)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def _land(
    session: Session,
    actor: AuthenticatedUser,
    group: cp.CsvGroup,
    partner_id: int,
    *,
    filename: str,
    sha256: str,
) -> OrderIntake:
    """PO 그룹 1건 착지 — 착지 단일 통로 `register_intake`(source_kind=CSV, 항상 PENDING)만 부른다."""
    return intake_service.register_intake(
        session,
        actor=actor,
        source_kind=IntakeSourceKind.CSV,
        buyer_partner_id=partner_id,
        header=IntakeHeaderIn(
            buyer_po_no=group.po_no,
            currency=group.currency,
            dest_market_code=group.market,
            buyer_po_date=group.po_date,
        ),
        lines=[
            IntakeLineIn(
                buyer_item_code=line.buyer_item_code,
                quantity=line.quantity,
                unit_price=line.unit_price,
                requested_delivery_date=line.requested_delivery_date,
                source_row_no=line.row_no,
            )
            for line in group.lines
        ],
        extracted_snapshot=_snapshot(group, filename=filename, sha256=sha256),
        source_sha256=sha256,
        source_group_key=f"{partner_id}|{group.po_key}",
        original_filename=filename,
    )
