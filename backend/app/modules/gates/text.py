"""override 사유 위생 — 서비스(`service.clean_reason`)와 요청 스키마가 같은 규칙을 쓴다 (S3-1 PR-11a 적대 검토 반영 / ADR-0069).

규칙(코드포인트 기준): ① 유니코드 범주 Cc(제어)·Cf(서식)·Zl·Zp와 한글 채움 문자(U+115F·U+1160·U+3164·U+FFA0) 거부 — 눈에 안 보이는 글자로 길이를 채우는 우회 차단 ② 앞뒤 공백(`str.strip` — 전각·NBSP 포함)을
자른 뒤 길이 5~500 ③ 내부 공백을 제외한 실질 글자가 5자 이상(공백성 사유 거부). DB CHECK(`reason !~ '[[:cntrl:]]'`·`btrim`)는 **로캘·ASCII 공백에 의존하는 최후 방어선**이라 이 규칙보다 느슨하다 — 사유의 정본 검증은 여기다.
"""

from __future__ import annotations

from app.core.text import invisible_char_problem

REASON_MIN = 5
REASON_MAX = 500


def reason_problem(value: str) -> str | None:
    """사유가 규칙을 어기면 사용자용 한국어 문구, 통과면 None. 호출자는 이 문구를 422 detail에 싣는다.

    ① 보이지 않는 글자 판정은 공용 헬퍼 `app.core.text.invisible_char_problem`(Cc·Cf·Zl·Zp·한글 채움 — PR-15a에서 추출, 동작 동일)이다.
    """
    problem = invisible_char_problem(value, label="사유")
    if problem is not None:
        return problem
    cleaned = value.strip()
    if not REASON_MIN <= len(cleaned) <= REASON_MAX:
        return f"사유는 {REASON_MIN}~{REASON_MAX}자로 입력해 주세요."
    if len("".join(cleaned.split())) < REASON_MIN:
        return f"사유는 공백을 뺀 실제 글자가 {REASON_MIN}자 이상이어야 합니다."
    return None
