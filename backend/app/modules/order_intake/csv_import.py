"""오더 인테이크 CSV 입구 — `POST /order-intakes/import-csv` (S3-1 PR-14a / design-D D2 / ADR-0072).

■ **파일 전체 원자**: 한 행이라도 오류면 **아무것도 착지하지 않고** 모든 오류를 한 번에 리포트한다(부분 성공 없음 — 부분 착지 뒤 수정본 재업로드는 sha256이 달라 이미 착지된 PO가 중복으로 충돌한다).
  통과하면 PO 그룹마다 `register_intake`(착지 단일 통로)를 **한 트랜잭션**에서 부른다 — 이 모듈은 `OrderIntake`를 직접 만들지 않는다(AST 스캔). 인테이크는 항상 PENDING이다.
■ 한 트랜잭션의 순서: 파일 해시 advisory lock → 멱등 claim(재생이면 최초 결과) → 같은 파일의 PENDING 인테이크 선조회(409 `FILE.DUPLICATE`) → 바이어·시장·중복 PO·중복 SKU 검증(서버 마스터) →
  오류가 있으면 422 `FILE.INVALID_ROWS`(중복 PO만 있으면 13a와 같은 409 `DUPLICATE_BUYER_PO`) → 그룹별 착지(`(바이어, PO키)` 정렬 순 — 동시 업로드 교착 방지) → 멱등 완료.
■ 동시 같은 파일: sha256 advisory lock이 직렬화한다 — 먼저 온 쪽이 커밋하면 뒤에 온 쪽은 같은 키면 최초 결과 재생, 다른 키면 `FILE.DUPLICATE` 409(결정적). DB 부분 유니크(`…source_sha256_source_group_key_pending`)가 최종 방어다.
■ **원본 바이트는 보관하지 않는다**(S3-1 — 설계 D2): 출처는 `extracted_snapshot`(원본 행 무손실)·`source_sha256`·`original_filename`으로 특정한다(원본 보관은 S6-1).
■ 파싱·셀 검증은 `csv_parse`(순수 함수)이고, 파일 읽기·인코딩·헤더·수식 이스케이프는 `imports` 모듈의 공개 통로를 그대로 쓴다.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
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

IMPORT_ENDPOINT = "POST /api/v1/order-intakes/import-csv"

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


def _parse_upload(raw: bytes) -> cp.ParsedCsv:
    if b"\x00" in raw:  # NUL 바이트 — 텍스트 파일이 아니다(csv 모듈 동작도 버전마다 다르다)
        raise AppError(ErrorCode.IMPORTS_FILE_ENCODING_INVALID)
    text_body = parser.decode_upload(raw)
    try:
        parsed = parser.parse_csv(
            text_body, header=tpl.CSV_HEADER, string_columns=tpl.STRING_COLUMNS
        )
    except csv.Error as exc:
        raise _report(
            [
                cp.RowError(
                    None,
                    None,
                    cp.E_CSV_SYNTAX,
                    f"CSV 형식을 읽지 못했습니다({str(exc)[:80]}). 따옴표·줄바꿈이 깨진 셀이 없는지 확인해 주세요.",
                )
            ]
        ) from None
    total = len(parsed.rows) + len(parsed.problems)
    if total == 0:
        raise AppError(ErrorCode.IMPORTS_FILE_EMPTY)
    if total > imports_service.IMPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "file": f"행이 너무 많습니다({total:,}행). "
                f"{imports_service.IMPORT_MAX_ROWS:,}행 이하로 나누어 올려 주세요."
            },
        )
    return cp.parse_rows(parsed, today=today_kst())


# ── 오류 리포트 ────────────────────────────────────────────────────────────────


def _report(errors: Sequence[cp.RowError]) -> AppError:
    """오류 전수 → 응답. 행번호·열 순으로 정렬해 최대 N건만 싣고 나머지는 개수만 알린다. **중복 PO만** 있으면 13a와 같은 409다."""
    column_order = {name: index for index, name in enumerate(tpl.CSV_HEADER)}
    ordered = sorted(
        errors,
        key=lambda e: (e.row_no or 0, column_order.get(e.column or "", -1), e.code),
    )
    shown = ordered[: tpl.MAX_REPORTED_ERRORS]
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
        },
    )


# ── 서버 마스터 검증 (읽기 전용) ────────────────────────────────────────────────


def _master_errors(
    session: Session, groups: Sequence[cp.CsvGroup]
) -> tuple[list[cp.RowError], dict[str, int]]:
    """바이어·시장 등록 여부 → (행 오류, 바이어코드→거래처 id). 바이어코드는 **정확 일치**(대소문자 다르면 오류+힌트, 자동 매칭 없음)다."""
    errors: list[cp.RowError] = []
    codes = {g.buyer_code for g in groups}
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
        rows = [r for g in groups if g.buyer_code == code for r in g.row_nos]
        if code not in found:
            hint = hints.get(code.lower())
            suffix = f" 혹시 '{hint}'인가요? 코드의 대소문자까지 같아야 합니다." if hint else ""
            message = (
                f"등록되지 않은 바이어코드입니다: {code}.{suffix} 거래처 관리에서 확인해 주세요."
            )
            errors += [
                cp.RowError(r, tpl.COL_BUYER_CODE, cp.E_BUYER_UNKNOWN, message) for r in rows
            ]
        elif found[code] not in buyer_ids:
            message = f"바이어 유형의 거래처가 아닙니다: {code}. 거래처 유형을 확인해 주세요."
            errors += [
                cp.RowError(r, tpl.COL_BUYER_CODE, cp.E_BUYER_NOT_BUYER, message) for r in rows
            ]
        else:
            buyers[code] = found[code]

    #: 형식이 틀린 시장 코드(2자리 영문 아님)는 파서가 이미 그 행의 오류로 보고했다 — 마스터 조회 대상에서 뺀다(같은 행에 오류 2건을 만들지 않는다).
    markets = {g.market for g in groups if len(g.market) == 2 and g.market.isalpha()}
    known = set(
        session.execute(
            select(Market.code).where(Market.code.in_(markets), Market.deleted_at.is_(None))
        ).scalars()
    )
    for market in sorted(markets - known):
        message = f"등록되지 않은 시장입니다: {market}. 시장 관리에서 먼저 등록해 주세요."
        errors += [
            cp.RowError(r, tpl.COL_MARKET, cp.E_MARKET_UNKNOWN, message)
            for g in groups
            if g.market == market
            for r in g.row_nos
        ]
    return errors, buyers


def _po_and_sku_errors(
    session: Session, groups: Sequence[cp.CsvGroup], buyers: dict[str, int]
) -> list[cp.RowError]:
    """중복 PO(PENDING 인테이크·비취소 SO 점유)와 **같은 SKU로 매핑되는 서로 다른 품번**(한 PO 안) — 품번 미매핑은 오류가 아니다(`sku_id NULL`로 착지)."""
    errors: list[cp.RowError] = []
    by_buyer: dict[int, list[cp.CsvGroup]] = defaultdict(list)
    for group in groups:
        if group.buyer_code in buyers:
            by_buyer[buyers[group.buyer_code]].append(group)
    for partner_id, owned in by_buyer.items():
        resolved = intake_service.resolve_codes(
            session, partner_id, [line.buyer_item_code for g in owned for line in g.lines]
        )
        for group in owned:
            found = intake_service.occupant(session, partner_id, group.po_key)
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
            seen_sku: dict[int, int] = {}
            for line in group.lines:
                hit = resolved.get(line.buyer_item_code)
                if hit is None:
                    continue
                first = seen_sku.get(hit.sku_id)
                if first is not None:
                    errors.append(
                        cp.RowError(
                            line.row_no,
                            tpl.COL_ITEM_CODE,
                            cp.E_DUPLICATE_SKU,
                            f"{first}행의 품번과 같은 SKU로 매핑됩니다. 수주는 SKU마다 1줄이므로 한 줄로 합쳐 주세요.",
                        )
                    )
                else:
                    seen_sku[hit.sku_id] = line.row_no
    return errors


# ── 스냅샷·응답 ────────────────────────────────────────────────────────────────


def _snapshot(group: cp.CsvGroup, *, filename: str, sha256: str) -> dict[str, Any]:
    """CSV 불변 원본 — MANUAL 스냅샷과 같은 모양(`kind·header·lines`)에 파일 출처·엑셀 행번호를 더한다. 값은 **원본 셀 문자열 그대로**(서버 해석값은 싣지 않는다)."""
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
            }
            for line in group.lines
        ],
    }


def _summaries(
    session: Session, landed: Sequence[tuple[cp.CsvGroup, OrderIntake]]
) -> list[dict[str, Any]]:
    """착지 결과 요약(파일 순서) — 라인 수·미매핑 수·합계는 한 번의 집계 쿼리다(라인 수와 무관한 질의 수)."""
    from app.modules.trade_docs.views import money_text

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
        # 같은 파일의 동시 업로드를 직렬화한다 — 뒤에 온 쪽은 앞 트랜잭션 종료 뒤 재생/409를 결정적으로 본다.
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
        master_errors, buyers = _master_errors(session, parsed.groups)
        errors += master_errors
        errors += _po_and_sku_errors(session, parsed.groups, buyers)
        if errors:
            raise _report(errors)

        landed: list[tuple[cp.CsvGroup, OrderIntake]] = []
        for group in sorted(parsed.groups, key=lambda g: (buyers[g.buyer_code], g.po_key)):
            partner_id = buyers[group.buyer_code]
            row = intake_service.register_intake(
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
                extracted_snapshot=_snapshot(group, filename=original, sha256=sha256),
                source_sha256=sha256,
                source_group_key=f"{partner_id}|{group.po_key}",
                original_filename=original,
            )
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
