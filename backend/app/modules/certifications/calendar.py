"""인증 만료일의 달력 계산 — 순수 함수 (DB·세션 없음).

네 곳이 같은 계산을 공유한다: 날짜 스윕(`service._converge` — 상태를 저장된
값으로 맞춘다)·도과 표시(`service.overdue_days_of`)·시장 준비도 매트릭스
(`readiness` — 저장하지 않고 그 자리에서 계산한다)·기일 캘린더
(`deadlines/board.py` — 도과 표시). "만료임박이 언제부터인가"를
각자 구현하면 화면마다 상태가 갈리므로 정의는 이 파일 하나다.

★ 실효 상태(effective_status)가 매트릭스가 스윕을 기다리지 않는 근거다. 스윕은
  하루 한 번(06:00) 돌고, 만료일이 지난 날 06:00 전까지 저장 상태는 아직
  승인이다. 매트릭스가 저장 상태만 읽으면 그 창 동안 🟢을 보여 준다(과신) — 달력
  파생 3태(승인·만료임박·만료)는 만료일·리드타임의 순수 함수이므로 그 자리에서
  다시 계산해 스윕이 곧 도달할 상태를 미리 본다.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.modules.certifications.machine import DATE_DERIVED_STATUSES, TERMINAL_STATUSES


def derived_date_status(
    expires_on: date | None, renewal_lead_days: int | None, base_date: date
) -> str:
    """달력 파생 3태의 정답 — 만료일·리드타임의 순수 함수 (§5.2 자동 부여).

    만료일이 없으면 무기한이라 APPROVED가 정답이고, 리드가 없으면 임박 단계가
    없다(임의 기본값 발명 금지 — 필요하면 템플릿에 리드타임을 입력하는 것이
    정공법).
    """
    if expires_on is None:
        return "APPROVED"
    if base_date > expires_on:
        return "EXPIRED"
    if renewal_lead_days is not None and base_date >= expires_on - timedelta(
        days=renewal_lead_days
    ):
        return "EXPIRING"
    return "APPROVED"


def effective_status(
    status: str, expires_on: date | None, renewal_lead_days: int | None, base_date: date
) -> str:
    """오늘 기준의 실효 상태 — 달력 파생 3태만 다시 계산하고 나머지는 저장값 그대로.

    진행 상태(서류준비~보완요청)·갱신중·종결 2태는 사람이 기록한 전이의 결과라
    날짜로 바꾸지 않는다(층3 — `machine.DATE_DERIVED_STATUSES`).
    """
    if status in DATE_DERIVED_STATUSES:
        return derived_date_status(expires_on, renewal_lead_days, base_date)
    return status


def overdue_days(status: str, expires_on: date | None, base_date: date) -> int | None:
    """만료일 도과 일수 — 도과가 아니면(무기한·미도래·종결) None (안건 ⑦ 계산값).

    종결 2태(반려·중단)는 만료일이 남아 있어도 도과가 아니다 — 이미 닫힌 건이다.
    """
    if expires_on is None or status in TERMINAL_STATUSES:
        return None
    elapsed = (base_date - expires_on).days
    return elapsed if elapsed > 0 else None
