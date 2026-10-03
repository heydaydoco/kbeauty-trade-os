"""오더 인테이크 CSV 표준 양식 — 헤더·열 정의·상한의 **단일 출처** (S3-1 PR-14a / design-D D2).

■ 양식은 9열 고정(헤더 1행 **완전 일치·순서 고정**, 안내 행 없음 — 헤더 완전일치 파서라 안내 행이 오류가 되므로 안내는 화면 문구다).
  1행 = 1라인. 앞 5열은 **헤더 값**(같은 PO 안의 전 행이 같아야 한다), 뒤 4열은 라인 값이다.
■ 다운로드(`GET /order-intakes/template.csv`)와 업로드 검증이 **같은 상수**를 쓴다(양식 왕복 — 한쪽만 바뀔 수 없다).
■ 열을 추가·개정하면 `PARSER_VERSION`을 올린다(스냅샷에 기록 — 직전 세대 헤더 병행 수용은 개정 세션 몫, design-D D2 관찰).
"""

from __future__ import annotations

from typing import Final

COL_BUYER_CODE: Final = "바이어코드"
COL_PO_NO: Final = "바이어PO번호"
COL_PO_DATE: Final = "PO일자"
COL_CURRENCY: Final = "통화"
COL_MARKET: Final = "목적지시장코드"
COL_ITEM_CODE: Final = "바이어품번"
COL_QUANTITY: Final = "수량"
COL_UNIT_PRICE: Final = "단가"
COL_DELIVERY: Final = "요청납기일"

CSV_HEADER: Final[tuple[str, ...]] = (
    COL_BUYER_CODE,
    COL_PO_NO,
    COL_PO_DATE,
    COL_CURRENCY,
    COL_MARKET,
    COL_ITEM_CODE,
    COL_QUANTITY,
    COL_UNIT_PRICE,
    COL_DELIVERY,
)
#: 앞 5열 — 같은 PO 그룹 안에서 전 행이 같아야 하는 헤더 값.
HEADER_COLUMNS: Final[tuple[str, ...]] = CSV_HEADER[:5]
#: 뒤 4열 — 라인 값.
LINE_COLUMNS: Final[tuple[str, ...]] = CSV_HEADER[5:]

#: 문자열 타입 열 — 수식 이스케이프 역변환(`unescape_formula_cell`)을 적용한다. 수량·단가·날짜 열은 비대상(음수가 텍스트로 변하면 안 된다 — 거부 대상이다).
STRING_COLUMNS: Final[frozenset[str]] = frozenset(
    {COL_BUYER_CODE, COL_PO_NO, COL_CURRENCY, COL_MARKET, COL_ITEM_CODE}
)

#: 파서 세대 — 스냅샷에 기록한다.
PARSER_VERSION: Final = 1

#: 한 파일이 만드는 인테이크(PO 그룹) 수 상한 / 한 그룹의 라인 수 상한(= `MAX_INTAKE_LINES`와 같은 값).
MAX_GROUPS: Final = 200
MAX_GROUP_LINES: Final = 200
#: 오류 리포트 최대 건수(초과분은 개수만 알린다).
MAX_REPORTED_ERRORS: Final = 200

DOWNLOAD_FILENAME: Final = "오더인테이크_표준양식.csv"
