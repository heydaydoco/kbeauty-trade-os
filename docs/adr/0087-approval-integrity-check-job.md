# ADR-0087: 승인 무결성 대사 잡 `approval-integrity-check` daily@05:40 — PR-9a 부채 ① 소비(PR-1b로 앞당김)·문제별 dedup 관리자 알림·잡 FAILED는 실행 예외만·첫 커밋 전건 실측

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §15(S3-2 [M4] 보강) / ADR-0060 ④·0058 / PROGRESS PR-9a 부채 ① / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — N-02, R-17 / 구현 PR-1b(JOB 12→13)

**맥락** — ADR-0060 ④는 트리거 없는 위조 탐지층으로 승인 이벤트 대사 `approvals/integrity.check_integrity`(읽기 전용)를 두되 배선을 부채로 남겼고(PR-9a 부채 ① — 오너=영준, 트리거 "감사·운영 투입 전(S3-2 이전)"), 트리거가 이미 도달했는데 S3-2 부록 어디에도 없었다(조용한 누락 — N-02). 불일치 1건마다 매일 잡 FAILED로 두면 정정 수단이 없는 읽기 전용 대사라 알림 피로와 실행 실패를 구분할 수 없다.

**결정** — ① 잡 1행 `approval-integrity-check` daily@05:40 KST(05:30 검산 뒤·06:00 앞), `check_integrity`를 `after_id` 페이지로 전건 순회, CLI 수동 실행 겸용, **S3-2 PR-1b**(PR-1 직후 — JOB 12→13, 스캔 잡은 PR-6에서 13→14). ② **불일치 = 관리자 인앱 알림, dedup 키 `approval-integrity:{approval_id}:{problem}`**(일자 제외 — 같은 문제는 1회, 문제가 바뀌면 새 알림, 미해소 알림은 받은편지함에 남음). ③ **잡 FAILED = 실행 예외만**(불일치 자체는 SUCCESS+알림). ④ **PR-1b 첫 커밋에서 기존 DB 전건 대사 실측**("불일치 0" 기록 — 1건↑이면 알림 형식 확정 전 원인을 PROGRESS에 기록). ⑤ 4금: 승인 행 무수정(읽기 전용 — J 테스트)·알림만. deferred 트리거(DB 트리거 방식)는 미채택 유지(ADR-0060).

**근거** — 트리거 도과 부채를 잡 의존 0인 독립 PR로 먼저 소비한다. 문제별 1회 dedup은 매일 반복 알림보다 피로가 적으면서 문제별로는 동일하게 fail-visible이다.

**기각한 대안** — PR-6에 동석(트리거보다 늦음), 불일치 = 잡 FAILED(정정 수단 없는 매일 실패 — 실행 오류와 구분 불가), dedup에 일자 포함(매일 재알림 — 피로), DB 트리거 실시간 탐지(ADR-0060 기각 유지), 배선 보류(조용한 누락).

**되돌리기 비용** — 낮음(`enabled=false`, 키 형식 변경은 신규 알림부터). 오너=영준(보안 판정) 항목을 자율 확정한 것이라 **오너 확인 권장 2순위**.

**부기(2026-10-04 — S3-2 PR-1b 이행)** — (위 원문 결정은 고치지 않는다.) ①~⑤ 이행: `approvals/integrity.py`에 `scan_all`(기존 `check_integrity` 로직을 `after_id` 페이지로 전건 순회 — 대사 로직 재사용, 판정 문제 8종 무변경)·`run_integrity_check`(잡 본체·CLI 겸용)를 더했다. **읽기 전용을 DB가 강제**: 대사는 첫 문장이 `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`인 독립 트랜잭션에서 돌고(쓰기 시도는 PG가 거부 — J 테스트), 열린 트랜잭션 합류는 거부한다. 알림은 그 뒤 별도 트랜잭션 1개에서 `approval-integrity:{approval_id}:{problem}`(수신자별 `:{user_id}` 접미는 `notify` 공통) dedup·CRITICAL·ADMIN 라우팅·`entity_type=approvals`로 만든다. 잡 `approval-integrity-check` daily@05:40 등록(JOB 12→13), 불일치는 잡 OK·FAILED는 실행 예외뿐. CLI `python -m app.cli approval-integrity-check`(불일치가 있으면 종료 코드 1 — 합계 검산 CLI 관례). **첫 커밋 실측**: dev DB 승인 0건·불일치 0 / 앱 경로 흐름 16건(6상태) 투입 후 불일치 0(PROGRESS 'S3-2 PR-1b'). 남은 한계(의도): dedup이 수신자 단위라 **관리자가 새로 생기면 미해소 문제를 그 관리자에게 1회** 보낸다(받은편지함 기준).
