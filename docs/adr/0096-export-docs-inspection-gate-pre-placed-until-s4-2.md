# ADR-0096: CI·PL·S/I 검수 게이트 선배치(INSPECTED·RELEASED·SHIPPED) — 운영 발행 경로는 S4-2까지 닫힘·DoD = 서비스 층 시험(시험 전용 팩토리)·미리보기 RELEASE_ORDERED부터·runbook 외부 작성 안내(A-05)·S/I 폐쇄 = 설계 선택(원천 = CI)

- **상태**: 자율 확정 — 사후 번복 가능 (S3-3 계획 2026-10-05 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-05
- **관련**: DESIGN.md §7.2(선적 줄 'CI/PL 생성은 검수완료 이후만')·§20 B '검수 미완료 선적의 CI·PL 생성 차단'(S3-3 [M4] 보강 §7.2 ②·§20 ①) / WBS S3-3 산출물·DoD(v1.7 주석 ①-a·①-b·④)·S4-2 행(v1.7 ⑦) / ADR-0074·0081(시험 구조 선례)·0095 / docs/plans/s3-3-plan.md · docs/plans/s3-3/design-integrated.md(§9 적대 검토 정정 R-01~R-40 우선) — sA §A2·§A8, N-11, R-10 / 구현 PR-5a(게이트 상수·결속 시험)·PR-5b·PR-7(runbook A-05)
- **번복 비용 큰 결정 — 오너 확인 권장 3순위**: WBS S3-3 산출물 'CI·PL·S/I 템플릿 렌더링'이 운영에서 S4-2까지 쓰이지 않는다(운영 사용자 기대와 어긋남). 번복은 상수 1개 + 시험 2건 + runbook 문장(낮음)이지만, 번복 뒤 발행된 CI는 소급 회수할 수 없어 **비대칭**이다. 오너 상시 지시(2026-09-29 "결정·개입 없이 끝까지")에 따라 자율 확정해 진행한다. 오너가 번복하면 이 ADR을 '대체' 표기로 갱신하고 새 ADR을 쓴다(PROGRESS 'S3-3 계획 확정·PR-1' 절 '오너 확인 권장 4건').

**맥락** — DESIGN §7.2·§20 B는 검수 전 CI·PL 생성을 금지한다. 선적 RESERVED 5상태(PICKING~CLOSED)는 S4-2에서 열린다(ADR-0074). 따라서 S3-3이 CI 발행 경로를 만들어도 운영에서 발행 가능한 선적이 없다. WBS DoD '교차 불일치 거부·무상 생성'은 S3-3에서 충족해야 한다.

**결정** — ① **게이트를 지금 세운다**: `CI_ISSUABLE_SHIPMENT_STATES = {INSPECTED, RELEASED, SHIPPED}` — 그 밖 CI(+PL) 발행 409 `EXPORT_DOCS.SHIPMENT.NOT_INSPECTED`. 결속 시험 2(실제 경로로 만든 RELEASE_ORDERED 선적 409·술어 확장 변이 kill — GC-A25). ② **①-a CI·PL** = `D:187`·`D:444` 근거. **①-b S/I** = DESIGN 근거가 아니라 **설계 선택 '원천 = 살아 있는 CI'**에 따른 폐쇄 — 선적 원천 S/I 개방안은 **기각**(R-10). ③ **S3-3~S4-2 운영 CI·PL·S/I 발행 0** — 미리보기(JSON·저장 0)는 RELEASE_ORDERED부터, runbook이 시스템 밖 작성(A-05)과 채권 `invoice_ref`에 외부 인보이스 번호 입력을 안내. ④ DoD = **서비스 층 시험**(시험 전용 상태 팩토리 `force_shipment_status_for_test` — ADR-0081 구조, 앱 코드 호출 0 AST 스캔·raw `UPDATE shipments SET status` 리터럴 0 — N-11). ⑤ S4-2 INSPECTED 개방 PR이 게이트 실발효·runbook 외부 작성 문장 삭제·운영 관통 1회·채권 선행 선적 CI 재판정·CI 목록 메뉴를 함께 한다(WBS v1.7 ⑦).

**근거** — 게이트를 S4-2에 미루면 S3-3이 만든 발행 경로가 검수 없이 열린 채로 S4-2를 기다린다(사양 위반 창). 지금 세우면 위반 창이 0이고, DoD는 서비스 층에서 그대로 증명된다(S3-2 L/C 순수 함수 선례).

**기각한 대안** — 게이트 없이 CI 운영 개방(§7.2·§20 B 위반), RELEASE_ORDERED 허용(검수 미완료), 선적 원천 S/I 운영 개방(CI 발행 뒤 수하인 불일치를 막을 기준값 없음·B/L 대조 기준 이원화 — R-10), DoD를 S4-2로 이관(WBS 문면 미이행), 시험 전용 엔드포인트(운영 우회 표면).

**되돌리기 비용** — **낮음**(상수 1개 + 시험 2건 + runbook 문장) — 단 **비대칭**(번복 뒤 발행된 CI는 회수 불가). S/I 원천 번복은 중간(`ci_id` NULL 허용 + 원천 2종 CHECK + B/L 대조 기준 재정의).
