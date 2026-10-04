# ADR-0083: 롤오버 이력 `milestone_changes`·통보 `milestone_change_notices`는 IMMUTABLE — 통보 = comm_logs SHIPMENT 주제(선적 전용 통로만, 범용 경로 쓰기·읽기·첨부 차단)·발송 0·M2·M3 응답 `{board, change}`

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §5.4·§7.5·§7.9·§17.5(S3-2 [M4] 보강) / ADR-0040·0051 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B9, sC §C9, N-03, R-05·R-19 / 구현 PR-4a(M15)

**맥락** — §7.5는 "스케줄 변경(롤오버) 이력+통보 기록"을, §5.4 S2-4 부기는 포워더·관세사 소통을 comm_logs 주제 확장으로 붙이라고 한다. 이력 행이 수정 가능하면 롤오버 사실이 사라지고, 불변 행에는 통보를 사후에 적을 수 없다. comm_logs에 SHIPMENT를 넣으면 범용 `/comm-logs`가 SHIPMENT 행을 만들거나 노출·첨부할 수 있다(R-05).

**결정** — ① `milestone_changes`(IMMUTABLE+`revoke_mutations`): 마일스톤 값 변경마다 1행 — `change_kind` PLAN_SET·PLAN_CHANGED(= 롤오버)·ACTUAL_RECORDED·ACTUAL_CORRECTED, 전후 값(날짜·시각·tz 분리), PLAN_CHANGED·ACTUAL_CORRECTED는 사유 필수(CHECK). 계획 삭제 경로 없음. 멱등 정본은 `idempotency_keys`(이력 표에 키 열 없음). ② `milestone_change_notices`(IMMUTABLE): (change_id, comm_log_id) UNIQUE — 이력과 통보를 사후 연결. 미연결 롤오버는 '통보 기록 없음' 배지. ③ comm_logs 주제 CHECK += SHIPMENT(M15 — downgrade는 SHIPMENT 행이 있으면 실패). **범용 경로**: `SubjectType` Literal `{CERTIFICATION}` 유지(POST·목록 필터 SHIPMENT = 422), 목록 기본 조건 = 범용 허용 주제(SHIPMENT 미노출), id 접근(상세·PATCH·DELETE) SHIPMENT 행 = 404, documents `COMM_LOG` 첨부는 범용 주제만. ④ **발송 코드 0** — 통보는 일어난 일의 기록. ⑤ 마일스톤 쓰기 응답 = `{board: MilestoneBoard, change: {id, change_kind} | null}`(no-op이면 null), 같은 Idempotency-Key 재요청 = 같은 `change.id`. ⑥ 1TX: 마일스톤 UPDATE + 이력 INSERT + outbox `shipments.milestone.changed`(금액·원가 0).

**근거** — 상태 변경 이력 성격의 표는 신설 세션이 IMMUTABLE로 등재한다(§17.5). 연결 표로 두면 이력 불변과 사후 통보 기록이 양립한다. 범용 경로 차단은 선적 통보가 선적 권한·화면 밖으로 새는 것을 막는다.

**기각한 대안** — 통보를 이력 행 필드로(불변 행이라 사후 기록 불가·§7.9 허브와 이중 구조), 마일스톤 행 수정 가능 이력(정정=새 행 원칙 위반), 범용 `/comm-logs`로 SHIPMENT 허용(권한·노출 경계 붕괴), 응답에서 `change_id` 생략(프런트가 재조회로 추정 — R-19), 통보 시 실제 메일 발송(§15 대외 최초 발송 금지).

**되돌리기 비용** — **중간~높음**(IMMUTABLE 표 형태 변경 = 권한 해제 마이그레이션+데이터 이전). 범용 경로 차단 완화·응답 모양 변경은 낮음.

**부기(2026-10-04 — S3-2 PR-4a 이행: M15 이력·통보·comm_logs SHIPMENT)** — ①~⑥을 구현했다. `milestone_changes`(IMMUTABLE·`revoke_mutations`, 전후 값 날짜·시각·tz 분리, CHECK `reason_required`·`reason_clean`[1~500·제어문자 0]·`value_pairs`·`kind_values`[설정·기록 ⇔ 이전 값 없음, 새 값 비움은 실적 정정만]·`changed`[전후 동일 행 거부])·`milestone_change_notices`(IMMUTABLE, UNIQUE(change_id, comm_log_id)) — table_policy IMMUTABLE 등재·42501 시험. comm_logs 주제 CHECK = (CERTIFICATION, SHIPMENT), 범용 경로 차단 4중(생성 Literal·목록 기본 조건·id 접근 404·documents 첨부 422)을 e2e로 고정. 통보 = `POST /shipments/{id}/milestone-changes/{change_id}/notices` `{occurred_on(≤ KST 오늘), counterpart_partner_id?, summary}` → 201 (SHIPMENT 통신 기록 1행 + 연결 1행, **발송·아웃박스 0** — 아키텍처 스캔). 자율 확정: 통보는 모든 변경 종류에 허용(롤오버 미연결 배지는 PLAN_CHANGED만 센다)·취소된 선적은 409 `OWNER_NOT_ACTIVE`·상대 거래처 유형 = FORWARDER·CUSTOMS_BROKER·THREE_PL·BUYER·SUPPLIER·OEM. ⑤ 응답 `{board, change}`(no-op = null, 같은 키 = 같은 `change.id` — 6스레드 동시 시험), ⑥ outbox `shipments.milestone.changed` payload = 소유자·종류·`change_id`·`change_kind`·전후 값(금액·원가 0).
