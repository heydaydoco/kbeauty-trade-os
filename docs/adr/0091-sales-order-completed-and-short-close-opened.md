# ADR-0091: SO COMPLETED 자동 엣지 1(IN_SHIPMENT→COMPLETED)·short-close(사람 결정 3열·사유)·완결 판정 함수 1개(호출처 2)·오더 보드 제외·총수 31/151/182 — ADR-0076 대체

- **상태**: 자율 확정 — 사후 번복 가능 (S3-3 계획 2026-10-05 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-05
- **관련**: DESIGN.md §7.2·§15(S3-3 [M4] 보강 — §15 'SO 자동 엣지 2 → 3' 문면 변경) / WBS S3-3 v1.6 주석 'COMPLETED 엣지·short-close = provider `reflected=True`와 같은 PR' / **ADR-0076 대체** / ADR-0051·0066·0075 / PROGRESS Q-04 = P-02 / docs/plans/s3-3-plan.md · docs/plans/s3-3/design-integrated.md(§9 적대 검토 정정 R-01~R-40 우선) — sB B7, sC C5·C11, X-14·X-35, N-02, R-12 / 구현 PR-2b(커밋 ③ — 상태 기계·M17·보드 제외·총수 핀 같은 커밋)

**맥락** — S3-2는 COMPLETED 엣지·short-close를 미수 provider 등록과 같은 PR로 미뤘다(ADR-0076 — 기본 provider 동안 COMPLETED ∈ RESERVED[SO]). 부분 출하 뒤 잔량을 접는 수단이 없어 SO가 영원히 IN_SHIPMENT에 남는다.

**결정** — ① **IN_SHIPMENT→COMPLETED 자동 엣지 1**: 완결 판정 함수 `converge_sales_order_completion` 1개(호출처 = 채권 발생 flow·short-close flow **2파일**), 불변식 = 살아 있는 선적 전부 살아 있는 채권 보유 + 살아 있는 채권 ≥ 1 + (전 라인 잔량 0 또는 `short_closed_at` NOT NULL), 사람 1클릭과 **같은 TX**에서만(커널 `PAYLOAD_KEYS` += `cause_receivable_id`). ② **short-close** `POST /sales-orders/{id}/short-close`(A·T) — 사유 필수(≥ 2자, `BLANK_CHAR_CLASS`), 3열(`short_closed_at`·`_by_id`·`_reason`) 기록, 채권 없는 살아 있는 선적이 있으면 409 `SHORT_CLOSE.SHIPMENT_PENDING`, IN_SHIPMENT 아님 409·접을 잔량 없음 409. 불변 = 일관성 CHECK(셋 다 NULL 또는 셋 다 NOT NULL) + COMPLETED 종결(출구 엣지 0) + 3열 대입 앱 코드 1곳(아키텍처 스캔) — 트리거 미채택. ③ COMPLETED = TERMINAL·`REASON_REQUIRED_TO`, RESERVED에서 제외, 되돌림 0, COMPLETED SO의 새 선적 409·채권 취소 409 `SO_COMPLETED`. ④ M17: short-close 3열 + 일관성 CHECK + SO 상태이력 `reason_required` 재정의 + **`reason_not_blank`를 `BLANK_CHAR_CLASS`로 재정의**(R-12 — 위반 기존 행 사전 계수, 1건이라도 있으면 RAISE). ⑤ 오더 보드 `EXCLUDED_SO_STATUSES` += COMPLETED(N-02). ⑥ 총수 30/152/182 → **31(18·13)/151/182**(5a 후 32/152/184 — ADR-0095). ⑦ ADR-0076의 결속 시험을 ADR-0090 ⑤의 강화형으로 대체.

**근거** — WBS v1.6 주석이 이 엣지를 provider·노출 차감과 '같은 PR'로 결속했다 — 노출 술어가 COMPLETED를 제외하는 순간 그 SO 금액은 채권 항으로 옮겨 가 있어야 공백이 0이다. 트리거를 사람 1클릭 TX로 한정하면 §15 4금(자동 확정 부재)이 유지된다.

**기각한 대안** — COMPLETED 사람 엣지(채권화와 상태가 어긋날 창), 야간 잡으로 수렴(스케줄러 자동 전이 — 4금 위반 표면), short-close 사유 선택형(자유 기록이 감사 근거), 불변 트리거(ADR-0028·0040 계보 — 앱 단일 통로 + 스캔), COMPLETED 되돌림 엣지(채권 취소와 순환).

**되돌리기 비용** — **중간** — 상태이력 CHECK·총수 핀·SO 3열, COMPLETED 행이 생기면 M17 downgrade RAISE. 엣지 자체 제거는 낮음.
