"""인증 상태머신 — 코드 고정 (DESIGN.md §5.2 / ADR-11 "상태는 고정" / s2-2-plan.md §3).

이 파일이 상태·전이의 유일한 정의다. 여기 없는 전이는 존재하지 않고(전이 외
변경 거부 — §5.2), 상태를 대입하는 코드는 service.py의 전이 통로 하나뿐이다
(층2 — 아키텍처 테스트가 고정).

■ 설계 등뼈 둘 (판정 안건 ③ — s2-2-plan.md §0-1)

  ① **만료는 종결이 아니라 달력 파생 상태다.** 만료를 종결로 두면 만료일
    오입력 → 자동 만료 → 탈출 전무의 불가역 고착이 생긴다. {승인, 만료임박,
    만료} 3태는 만료일·리드타임의 순수 함수로 스윕이 양방향 수렴시킨다.
  ② **갱신은 재진입이 아니라 직행 왕복이다.** 갱신중→신청제출로 기존 사이클에
    재진입시키면 갱신 신청의 반려가 아직 유효한 인증을 REJECTED로 종결시키는
    축 혼동이 생긴다 — 상태 축은 기존 인증의 유효성을 표현하고, 갱신 절차의
    세부는 태스크(자유)·comm_logs(S2-4)가 담당한다.

■ 총수 고정 (판정 조건 E)

  허용 = 사람 21방향 + 자동 6방향 = 27, 미허용 = 11P2(110) − 27 = 83.
  이 수는 tests/architecture/test_certification_machine.py가 집계로 고정한다 —
  전이를 더하거나 빼면 그 테스트와 이 독스트링을 함께 고친다(선례 #11 대사).
"""

from __future__ import annotations

#: 상태 11값 (§5.2 문면의 코드값 — DESIGN [M2] 보강(S2-2)이 명문화한다).
CERTIFICATION_STATUSES: tuple[str, ...] = (
    "NOT_STARTED",  # 미착수
    "PREPARING",  # 서류준비
    "SUBMITTED",  # 신청제출
    "IN_REVIEW",  # 심사중
    "SUPPLEMENTING",  # 보완요청
    "APPROVED",  # 승인(유효)
    "EXPIRING",  # 만료임박
    "RENEWING",  # 갱신중
    "REJECTED",  # 반려 (종결)
    "EXPIRED",  # 만료 (비종결 — 달력 파생)
    "SUSPENDED",  # 중단 (종결)
)

#: 종결 2태 — 탈출 전이 0. 재도전은 신규 인스턴스다(활성 한정 유니크가 구조로
#: 성립 — 템플릿 RETIRED 재활성 선례와 다른 이유: 템플릿은 유일 정의 행,
#: 인스턴스는 사건 기록 복수). 만료는 여기 없다 — 비종결 달력 파생 상태다.
TERMINAL_STATUSES: frozenset[str] = frozenset({"REJECTED", "SUSPENDED"})

#: 달력 파생 3태 — 날짜 스윕의 출발·도달이 전부 이 안이다(층3이 고정).
#: 갱신중(RENEWING)은 스윕 비대상 — 갱신 절차가 살아 있는 인스턴스를 시스템이
#: 닫지 않는다(도과 표시·알림은 S2-3 매트릭스·기일 엔진 몫 — §5.3 계산값).
DATE_DERIVED_STATUSES: tuple[str, ...] = ("APPROVED", "EXPIRING", "EXPIRED")

#: 중단(SUSPENDED)의 원천 — 종결 2태를 뺀 전 9상태. 미착수→중단 포함은 판정
#: ③ 문면("§4.8 자동 생성분의 처분 출구로 필수").
_SUSPENDABLE: tuple[str, ...] = tuple(
    s for s in CERTIFICATION_STATUSES if s not in TERMINAL_STATUSES
)

#: 사람 전이 21방향 — 전건 인증+관리자, 전이 전용 엔드포인트 하나로 수행한다.
HUMAN_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("NOT_STARTED", "PREPARING"),  # 착수 기록
        ("PREPARING", "SUBMITTED"),  # 제출 기록 (부속: 신청일)
        ("SUBMITTED", "IN_REVIEW"),  # 접수 확인 기록
        ("IN_REVIEW", "SUPPLEMENTING"),  # 보완 요청 수신 기록
        ("SUPPLEMENTING", "IN_REVIEW"),  # 보완 제출 기록 (§5.2 ⇄ 쌍)
        ("IN_REVIEW", "APPROVED"),  # 기관 승인 결과 기록 (부속: 승인일·유효시작·만료일·인증번호)
        ("APPROVED", "RENEWING"),  # 조기 갱신 착수 (리드타임 부재 인증의 갱신 진입 구제)
        ("EXPIRING", "RENEWING"),  # 갱신 착수 (§5.2 명문 경로)
        ("EXPIRED", "RENEWING"),  # 늦은 갱신·재취득 착수 (만료 후 재개의 단일 경로)
        ("RENEWING", "APPROVED"),  # 갱신 결과 기록 (부속 생략=갱신 취소 복귀)
        ("IN_REVIEW", "REJECTED"),  # 기관 반려 기록 (사유 필수)
        ("SUPPLEMENTING", "REJECTED"),  # 보완 기한 도과·포기 (사유 필수)
    }
    | {(s, "SUSPENDED") for s in _SUSPENDABLE}  # 사업 보류·대상 제외 (사유 필수)
)

#: 자동 전이 6방향 — 달력 파생 3태 상호 전부. 날짜 스윕(전이 서비스 경유·
#: actor NULL·사유 자동)만 수행한다. 승인 "부여"는 여기 포함되지 않는다 —
#: 25~27은 만료일 정정으로 날짜 조건이 해소된 행의 복귀다(#16 비대칭 결손
#: 금지 계보: 부여만 있고 해소가 없으면 정정 후 상태가 거짓이 된다).
AUTO_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("APPROVED", "EXPIRING"),  # 만료일−리드타임 도달 (§5.2 명문의 자동 부여)
        ("EXPIRING", "EXPIRED"),  # 만료일 도과
        ("APPROVED", "EXPIRED"),  # 리드 부재·스윕 공백의 도과 직행
        ("EXPIRING", "APPROVED"),  # 만료일 정정·연장으로 임박 조건 해소
        ("EXPIRED", "EXPIRING"),  # 만료일 정정으로 도과 해소·임박 구간 진입
        ("EXPIRED", "APPROVED"),  # 만료일 정정으로 도과·임박 조건 전부 해소
    }
)

#: 허용 전이 전체 = 27 (조건 E).
ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = HUMAN_TRANSITIONS | AUTO_TRANSITIONS

#: 사람 전이에서 사유가 필수인 도달 상태 (§5.2 "반려·만료·중단(사유 필수)").
#: 만료(EXPIRED)는 사람 전이 도달이 없다 — 자동 전이가 사유를 자동 기록하고,
#: DB CHECK(reason_required — 조건 B)가 3종 전부를 마지막 층에서 강제한다.
REASON_REQUIRED_TO: frozenset[str] = frozenset({"REJECTED", "SUSPENDED"})

#: 전이 부속 데이터 — 같은 요청·같은 트랜잭션에 싣는다(§17.1: 전이 따로 PATCH
#: 따로면 한 업무 동작이 두 트랜잭션으로 갈라진다).
#:   (서류준비→신청제출)  신청일 필수.
#:   (심사중→승인)        승인일·유효시작 필수, 만료일(무기한 허용)·인증번호 선택.
#:   (갱신중→승인)        전부 선택 — 값이 오면 갱신 반영, 전부 생략이면 기존 값
#:                        유지(갱신 취소 복귀 — 사유·입력값이 완료와 취소를 구분).
TRANSITION_REQUIRED_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("PREPARING", "SUBMITTED"): frozenset({"applied_on"}),
    ("IN_REVIEW", "APPROVED"): frozenset({"approved_on", "valid_from"}),
}
TRANSITION_OPTIONAL_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("IN_REVIEW", "APPROVED"): frozenset({"expires_on", "cert_number"}),
    ("RENEWING", "APPROVED"): frozenset({"approved_on", "valid_from", "expires_on", "cert_number"}),
}
