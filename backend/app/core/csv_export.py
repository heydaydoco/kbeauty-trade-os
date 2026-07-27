"""CSV 내보내기 (DESIGN.md §12.2 "전 목록 UTF-8 BOM 내보내기").

★ BOM이 이 모듈의 존재 이유다.
  BOM 없는 UTF-8 CSV를 Windows Excel이 열면 한글이 전부 깨져 보인다. 사용자는
  "시스템이 한글을 못 쓴다"고 판단하고 다시는 이 기능을 안 쓴다 — 실제로 데이터는
  멀쩡한데도. 내보내기 지점마다 기억해서 붙이는 방식으로는 반드시 새므로
  통로를 하나로 둔다.

★ 줄바꿈은 CRLF다(csv 모듈 기본값). RFC 4180이고 Excel이 기대하는 형식이다.
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


def render_csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """BOM으로 시작하는 CSV 문자열."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
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
