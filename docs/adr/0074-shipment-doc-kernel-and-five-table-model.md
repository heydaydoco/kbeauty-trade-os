# ADR-0074: 선적을 전표 커널에 편입한다(DocKind `SHIPMENT`·접두어 `SH`) — 5표 모델·원천 FK 정확히 하나·수입 원가 비복사·상태 8값 활성 3엣지·실적/통관 생존 시 취소 차단

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.1·§7.2·§7.5·§17.3·§17.5(S3-2 [M4] 보강) / WBS S3-2 / ADR-0051·0052·0053·0054·0024 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sA §A1~A9, X-03·X-05, R-01·R-04·R-15·R-16 / 구현 PR-3a(M14)·PR-4a(M15 통관)

**맥락** — §7.5는 선적(Incoterms·환율 고정·구분 4종)과 부분 1:N을, §7.2는 선적 상태 열거(계획→출고지시→피킹→검수완료→출고→선적→종결/취소)를 정한다. 선적은 SO·PO의 후속이라 역순 취소·잔량·이력 불변·아웃박스 계약을 받아야 하는데, 커널 밖에 두면 이 계약을 다시 만들어야 하고 그 재구현이 곧 우회 표면이다. 수입선적은 원가를 가진 PO에서 온다(ADR-0024 PO 원가 9채널).

**결정** — ① `DocKind.SHIPMENT`(접두어 `SH`, 수출·수입 한 kind + `shipment_kind` 4값 EXPORT·IMPORT·CHANNEL_INBOUND·SAMPLE_FREE)로 커널에 편입하고 상수 dict 11종·FIELD_POLICY·사슬 레지스트리를 함께 등록한다. ② 테이블 5개: `shipments`·`shipment_lines`·`shipment_parties`·`shipment_status_log`(IMMUTABLE)·`customs_records`(신고 1:N 사실 기록 — 세율·과세가격·세액·HS 열 없음, **M15/PR-4a**). ③ **헤더 원천 FK 정확히 하나**(EXPORT ⇔ `so_id`, IMPORT ⇔ `po_id` — CHECK), 채널입고·샘플무상 행은 DB가 거부(값만 싣고 생성 경로 미개방), 다중 SO 합적 없음. ④ 통화·고정 환율·결제조건 4열·Incoterms 3열·거래 상대·SKU 품명은 원천 ORIGIN 사본(생성 본문에 필드 없음 — `extra="forbid"`), **`doc_date`는 생성 시 `today_kst()` 1회·이후 불변**. 수출 라인 금액 CHECK는 `quantity::numeric * unit_price_amount = line_amount`+무상 규약 승계, **수입 라인은 단가 NULL·금액 0**(원가 비복사). ⑤ 상태 8값, 활성 사람 엣지 3(PLANNED→RELEASE_ORDERED 전용 `release-order`·PLANNED/RELEASE_ORDERED→CANCELLED 사유 필수), PICKING~CLOSED 5값 RESERVED(S4-2), 라인 편집은 PLANNED만(`TRADE_DOCS.DOCUMENT.FROZEN`). ⑥ **ETD·BL_ISSUED·ETA 실적은 RELEASE_ORDERED에서만**(PLANNED = 422 `SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE`), 그 실적이나 살아 있는 통관 기록이 있으면 선적 취소 409(`SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`·`CUSTOMS_RECORD_ALIVE`). ⑦ 총수 핀 30/152/182(ADR-0075와 합산).

**근거** — 커널 편입이면 단일 전이 통로·이력 IMMUTABLE·총수 핀·역순 취소·채번(ADR-0054 "접두어는 멤버 추가만")을 그대로 받는다. 원천 FK를 하나로 조이면 사슬 판정·잔량·SO 수렴이 단순해지고, 열면 링크 표 이전이 필요하다. 실적 생존 취소 차단은 §20 A "후속 생존 시 선행 취소 차단"의 선적판이다(적대 R-01 — 차단 없으면 ETD 실적이 있는 선적을 취소해 SO 잔량이 복원되는 fail-open).

**기각한 대안** — 커널 밖 독립 상태 기계(계약 재구현·`record_transition` 보호 밖), 수출·수입 별도 kind(총수·dict 2배), 수입 접두어 `SI`(S3-3 Shipping Instruction과 충돌), 다중 SO 합적 링크 표(실수요 없음 — 부채), 통관 표를 M14에 선생성(쓰기 경로 없는 표 구간 — R-16), 실적 생존과 무관한 자유 취소.

**되돌리기 비용** — **높음**(DocKind 값이 상태이력·아웃박스에 저장 — 운영 데이터 이후 분리 불가에 가깝다. 3a·4a는 운영 실데이터 투입 전 병합 권장). 원천 FK 완화(합적 허용)도 높음. 실적 취소 가드 제거·채널입고 경로 개방은 낮음(가드·CHECK 1개).
