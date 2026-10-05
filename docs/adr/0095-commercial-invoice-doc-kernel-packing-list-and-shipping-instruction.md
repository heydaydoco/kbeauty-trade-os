# ADR-0095: CI 커널 편입(DocKind `COMMERCIAL_INVOICE`·접두어 CI)·상태 2·사람 엣지 1·PL 동반(번호 공유, Q-05)·S/I 비커널 불변(채번 SI, Q-06)·재발행 = 새 번호 1TX·발행 A·T·계좌번호 마스킹·당사자 version+1(R-3a-4)·STALE 값 비교·총수 32/152/184

- **상태**: 자율 확정 — 사후 번복 가능 (S3-3 계획 2026-10-05 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-05
- **관련**: DESIGN.md §7.2·§7.6·§3·§17.4·§17.5(S3-3 [M4] 보강) / WBS S3-3 산출물 'CI·PL·S/I'·DoD '교차 불일치 서류 저장 거부·무상 생성' / ADR-0051·0052·0053·0054·0074 / PROGRESS Q-05·Q-06·R-3a-4 / docs/plans/s3-3-plan.md · docs/plans/s3-3/design-integrated.md(§9 적대 검토 정정 R-01~R-40 우선) — sA §A4~§A11, X-03·X-09·X-12·X-13·X-21·X-22·X-27, N-12, R-16·R-17·R-26 / 구현 PR-5a(M21)·PR-5b(M22)

**맥락** — CI·PL·S/I는 선적에서 나오는 대외 서류다. 커널 밖 별도 표로 두면 역순 가드·검산·상태이력·채번을 다시 만들어야 한다. 중량·CBM·박스(Q-05)와 'TO ORDER' 수하인(Q-06)은 S3-2가 S3-3 서류·L/C로 넘겼고, 당사자 변경이 선적 version을 올리지 않는 결함(R-3a-4)은 서류 스냅샷이 생기면서 재트리거됐다.

**결정** — ① **CI = 전표 커널 편입** — DocKind `COMMERCIAL_INVOICE`·접두어 `CI`·상태 ISSUED·CANCELLED·사람 엣지 1(ISSUED→CANCELLED, 사유 필수)·생성 = 발행 = 동결, 커널 dict 11행·`FIELD_POLICY`(FREE = `internal_note`·`assignee_id`)·사슬 `ChildLink(SHIPMENT→commercial_invoices)`·`ChildLink(CI→receivables)`·총수 핀을 **한 커밋**(S3-2 R-23 선례). 교차 표 DocKind CHECK 수정 0(R-26 — 5a 첫 커밋에서 0건 확인). ② 선적당 살아 있는 CI 1(`uq_commercial_invoices_shipment_id_live` — 409 `ALREADY_ISSUED`), 헤더 열 단위 UPDATE(상태·내부 메모·담당자만), 라인·상태이력 IMMUTABLE, 담당 이관 대상 등재(N-12). ③ **재발행 = 원본 CANCELLED + 새 번호 + `revision_no`·`supersedes_ci_id` 1TX**(중간 실패 시 둘 다 롤백, CLOSED 선적 재발행 = 부채 A-04). ④ **PL = CI 구성 요소**(같은 TX·번호 공유) — `packing_list_packages`·`packing_list_package_items`, 그램·mm 정수, CBM 파생, G.W. ≥ N.W. DB CHECK(같으면 통과), 포장 번호 연속(Q-05 종결). ⑤ **S/I = 비커널 불변**(`shipping_instructions`, 채번 `SI`) — 원천 = 살아 있는 CI(`ci_id` NOT NULL), 운임 조건은 Incoterms에서 도출, 포워더 필수, DG UN·Class 필수, 지시식(`TO_ORDER`·`TO_ORDER_OF`)은 L/C 술어 True일 때만(Q-06 종결), **CI당 첫 S/I 부분 유니크 + 409 `SI.ALREADY_ISSUED`·T6 선적 `FOR UPDATE`**(R-16). ⑥ **R-3a-4 해소**: 당사자 추가·삭제가 선적 version+1, CI STALE = 수하인·통지처 **값 비교**(파생 배지 — 내부 메모·담당자 변경의 거짓 STALE 0), STALE CI로 S/I 발행 409 `EXPORT_DOCS.CI.SOURCE_CHANGED`, 포워더 교체는 STALE 아님. ⑦ **발행·재발행·취소 = A·T**(미리보기·렌더 재시도·FREE PATCH = A·T·L), CI 발행 본문에 금액·통화·환율·단가 필드 구조적 부재. ⑧ CI 상세 `bank.account_no` = A·T·L만(C·V 마스킹), 미리보기 계좌 후보·요약 = A·T만, 목록·CSV 계좌번호 열 0(R-17). ⑨ 총수 31/151/182 → **32(19·13)/152/184**. ⑩ 비공백 CHECK = `BLANK_CHAR_CLASS`, 검산 잡은 CI를 자동 편입(코드 변경 0 — 5a에서 확인).

**근거** — 커널 편입이 역순 가드·검산·상태이력·채번을 재구현 없이 승계한다(sA §A4). 포장은 피킹·검수 뒤 확정되므로 동결된 선적 라인 열이 아니라 PL 표가 맞다. S/I가 CI를 원천으로 삼아야 S/I·CI·B/L 대조(S5-3)의 기준값이 하나다.

**기각한 대안** — CI 비커널 별도 표(`trade_documents` — 커널 기능 재구현, X-09), 선적 라인에 중량·CBM 열(혼합 포장 표현 불가 — X-12), 선적 당사자에 비거래처 TO ORDER 행(`partner_id NOT NULL` 계약 파괴 — X-13), CI·PL version 교차 규칙(같은 TX라 불필요 — X-21), version 비교 STALE(거짓 STALE), CI 발행 A·T·L(청구 서류 — X-03).

**되돌리기 비용** — DocKind 저장값 — 운영 데이터 전 **중간**, 후 **높음**. 단 검수 게이트(ADR-0096)로 S4-2까지 운영 CI 행 0이 기대되어 되돌리기 창이 S4-2까지 열린다. 발행 역할 넓히기는 낮음.
