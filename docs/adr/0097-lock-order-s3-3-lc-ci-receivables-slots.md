# ADR-0097: LOCK_ORDER S3-3 개정 — `lc_terms`·`commercial_invoices`·`receivables` 슬롯·PI SHARE 뒤 `lock_chain` 금지·채권 발생 PI 무잠금·노출 구성 변경 쓰기(PI 입금 포함) 거래처 잠금 선행·역기록 peek → 거래처 → 대상

- **상태**: 자율 확정 — 사후 번복 가능 (S3-3 계획 2026-10-05 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-05
- **관련**: DESIGN.md §17.2(S3-3 [M4] 보강) / ADR-0059·0068·0078(승계) / docs/plans/s3-3-plan.md · docs/plans/s3-3/design-integrated.md(§9 적대 검토 정정 R-01~R-40 우선) — sC C2·C3, sB B9, X-17·X-18·X-19·X-20·X-22, R-16·R-19·R-20·R-25·R-31 / 구현 PR-2a(`receivables`)·PR-2d(T11~T13)·PR-4a(`lc_terms`)·PR-5a(`commercial_invoices`)

**맥락** — S3-3은 채권·L/C·CI 세 표를 잠금 경로에 넣는다. 채권 발생이 PI를 `FOR SHARE`로 잡은 뒤 `lock_chain(SO)`를 부르면 `ANCESTORS[SO]=(QT, PI)` 때문에 QT `FOR UPDATE`가 PI 뒤에 오는 역순과 SHARE→UPDATE 승격 교착이 생긴다(X-18). 여신 평가는 SO 루프 뒤 provider를 다른 문장 스냅샷으로 읽어 중간 쓰기가 끼면 공백·이중이 생긴다(X-20).

**결정** — ① 순서: `idempotency_keys → order_intakes → partners → quotations → proforma_invoices → sales_orders → lc_terms → purchase_orders → shipments → shipment_children(+lc_presentations·lc_checklist_marks) → commercial_invoices → receivables → approvals → lines → doc_number_seq`. 슬롯은 표가 생기는 PR에서만. ② **노출 구성 변경 쓰기 = `lock_buyer_for_credit`(`FOR NO KEY UPDATE`) 선행** — 채권 발생(T8)·OPENING(T9)·채권 취소(T10)·채권 입금(T11)·역기록 양 분기(T12)·**PI 입금(T13 — R-20, X-20의 'T13 무변경' 철회)**·short-close(T14). ③ **PI를 `FOR SHARE`로 잡은 TX는 SO를 `lock_chain`으로 잡지 않는다 — `lock_document(SO)` 직접**(`lock_document(..., read=)` 키워드 확장 — R-25). 채권 발생은 PI 무잠금. ④ 역기록 = peek → 대상 판별 → 거래처 → 대상별(PI: `lock_chain(PI)` / 채권: T11과 같음). ⑤ CI 발행(T5) = 선적 `FOR UPDATE` → 당사자 `FOR SHARE` → CI → receivables 존재 읽기 → lines → seq(SO·거래처 잠금 없음), **S/I 발행(T6) = 선적 `FOR UPDATE`**(R-16). ⑥ L/C 입력 3종 = 플래그 행 `FOR SHARE`를 멱등 직후·다른 잠금보다 먼저(R-31), 레터헤드 = advisory 고정 키(ADR-0092). ⑦ 순서표 밖: company_profiles·feature_flags·payments·alerts·renditions. ⑧ 계측 CJ-09: T5~T17 첫 접촉 순서 = 색인 오름차순(부분수열), PI SHARE 이후 QT·PI UPDATE 0.

**근거** — `D:362` ②(부분수열·변경은 ADR). 노출 구성 쓰기를 거래처 잠금으로 직렬화해야 평가가 보는 노출이 {전, 후} 중 하나다(GC-F5). PI 입금까지 거래처 잠금을 잡아야 같은 송금이 채권·PI 양 경로로 동시에 들어올 때 이중 입력 판정 창이 0이다.

**기각한 대안** — 채권 발생이 PI `FOR SHARE` + `lock_chain(SO)`(40P01 구조), 노출 쓰기 무잠금 + 평가 재시도(공백·이중 창), PI 입금 무변경(교차 경합으로 이중 입력 검사 우회 — R-20), 빈 슬롯 선등록(계측 공회전).

**되돌리기 비용** — **중간** — 교착 재검증이 필요하다(계측 CJ-09·CJ-10이 비용을 줄인다).
