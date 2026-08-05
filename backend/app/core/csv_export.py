"""CSV 내보내기 (DESIGN.md §12.2 "전 목록 UTF-8 BOM 내보내기").

★ BOM이 이 모듈의 존재 이유다.
  BOM 없는 UTF-8 CSV를 Windows Excel이 열면 한글이 전부 깨져 보인다. 사용자는
  "시스템이 한글을 못 쓴다"고 판단하고 다시는 이 기능을 안 쓴다 — 실제로 데이터는
  멀쩡한데도. 내보내기 지점마다 기억해서 붙이는 방식으로는 반드시 새므로
  통로를 하나로 둔다.

★ 줄바꿈은 CRLF다(csv 모듈 기본값). RFC 4180이고 Excel이 기대하는 형식이다.

★ 수식 이스케이프는 전단사다 (§12.2 보강 / ADR-0027 — 부채 #17)
  Excel은 `=`·`+`·`-`·`@`(또는 탭·CR)로 시작하는 셀을 수식으로 해석할 수
  있다 — 자유 텍스트가 수식으로 실행되는 것이 CSV 인젝션이다. 여기서
  **문자열 셀에 한해** `'` 하나를 접두하고, 왕복 임포트 파서(S1-3 PR-3)는
  정확한 역변환(unescape_formula_cell)을 적용한다. 변환이 전단사라 왕복
  값이 보존되고 diff 오검출이 없다. 숫자·금액 타입 셀은 비대상이다 —
  음수(-5)가 텍스트로 변하는 부작용을 막는다. 보증 범위는 본 시스템
  export→import 쌍 기준(ADR-0027 문면).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from urllib.parse import quote

from fastapi.responses import StreamingResponse

#: Excel이 UTF-8임을 알아보게 하는 표식.
UTF8_BOM = "﻿"

MEDIA_TYPE = "text/csv; charset=utf-8"

#: 이 문자로 시작하는 문자열 셀은 이스케이프한다. `'` 자신이 포함되는 이유:
#: 접두가 `'`이므로, 원값이 `'`로 시작하면 한 번 더 접두해야(`''…`) 역변환이
#: 원값을 정확히 복원한다 — 이 포함이 전단사의 조건이다.
FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r", "'")


def escape_formula_cell(value: str) -> str:
    """수식으로 해석될 수 있는 문자열 셀에 `'`를 접두한다 (ADR-0027)."""
    if value.startswith(FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def unescape_formula_cell(value: str) -> str:
    """escape_formula_cell의 정확한 역변환 — 왕복 임포트 파서가 쓴다 (§12.2).

    선두 `'` 다음 문자가 트리거 집합에 속할 때만 벗긴다. 사용자가 Excel에서
    직접 친 `'일반텍스트`(트리거 아님)는 건드리지 않는다.
    """
    if value.startswith("'") and value[1:].startswith(FORMULA_TRIGGER_CHARS):
        return value[1:]
    return value


def _cell(value: object) -> object:
    if isinstance(value, str):
        return escape_formula_cell(value)
    return "" if value is None else value


def render_csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """BOM으로 시작하는 CSV 문자열. 문자열 셀은 수식 이스케이프를 거친다."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(cell) for cell in row])
    return UTF8_BOM + buffer.getvalue()


def csv_response(
    filename: str, header: Sequence[str], rows: Iterable[Sequence[object]]
) -> StreamingResponse:
    """다운로드 응답.

    파일명은 RFC 5987(`filename*`)로 함께 내린다 — 한글 파일명을 그냥 넣으면
    헤더가 latin-1이라 인코딩 에러로 응답 자체가 실패한다.
    """
    body = render_csv(header, rows)
    quoted = quote(filename)
    return StreamingResponse(
        iter([body]),
        media_type=MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            # 목록 내보내기는 캐시되면 안 된다(권한별로 내용이 다르다).
            "Cache-Control": "no-store",
        },
    )
