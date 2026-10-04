# ADR-0077: 수입선적 소비 kind `IN_TRANSIT`·`open_quantity` kind 필터·배정 가능량 — PO 잔량은 입고에서만 줄고 S4-1 입고 FULFILL과 겹쳐 세지 않는다

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.1·§8.3(S3-2 [M4] 보강) / WBS S3-2·S4-1(v1.6) / ADR-0052 / PROGRESS P-04·P-07 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sA §A4 / 구현 PR-2a(필터)·PR-3a(등록)·PR-5a(생성)

**맥락** — §7.1은 "PO 잔량은 입고 확정 시 차감"이라 하는데 수입선적은 PO 라인을 참조 생성한다. 현행 `open_quantity`는 소비자 kind를 무시하고 합산(`quantities.py`)하므로 수입선적 소비자를 등록하는 순간 PO 잔량이 선적 시점에 줄어드는 결함이 생긴다. 동시에 PO 수량을 넘는 수입선적은 S4-1 입고 참조를 오염시킨다.

**결정** — ① `LINE_CONSUMERS["SO_LINE"]` += 선적 라인 FULFILL 소비자(수출 잔량 = SO 라인 − 살아 있는 선적 라인 합, 초과 409 `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`), `CONSUMABLE_STATUSES[SO]` = {CONFIRMED, IN_SHIPMENT}(ON_HOLD 제외). ② `LINE_CONSUMERS["PO_LINE"]` += 선적 라인 **`IN_TRANSIT`** 소비자. ③ **`open_quantity(..., *, kinds=frozenset({"FULFILL"}))`** — 기본은 FULFILL만 합산하므로 PO 잔량은 수입선적으로 줄지 않는다(시그니처 하나 유지 — §8.3 부기). ④ 수입선적 초과 기준 = **배정 가능량** = PO 라인 수량 − 살아 있는 IN_TRANSIT 합(`kinds={"IN_TRANSIT"}`로 같은 함수에서 파생), 초과 409 `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE`(PO 잔량과 의미가 달라 코드 분리). PO 상태·`CONSUMABLE_STATUSES[PO]` 불변. ⑤ **S4-1 승계 계약**: 입고 라인은 수입선적 라인을 참조 생성하며 입고 FULFILL과 IN_TRANSIT은 겹쳐 세지 않는다(S4-1 착수 ADR이 재판정).

**근거** — kind 필터를 커널에 먼저 넣어야(PR-2a) 등록(PR-3a)이 PO 잔량을 조용히 바꾸지 않는다. 잔량은 파생이라 선적 취소 시 자동 복원된다(저장 잔량 없음 — ADR-0052).

**기각한 대안** — 수입선적을 FULFILL로 등록(PO 잔량이 선적 시점에 줄어 §7.1과 정면 충돌), 수입선적 초과 방지 없음(PO 초과 선적), 별도 함수 `assignable_quantity`(§8.3 "시그니처 하나" 위반), 같은 409 코드 공유(잔량·배정 가능량 오독).

**되돌리기 비용** — **중간**. S4-1이 입고 소비 의미를 재정의하면 IN_TRANSIT·필터를 함께 재판정(소비자 등록·기대값 테스트 갱신). 필터 기본값 변경은 기존 4종 전표 기대값 회귀 테스트가 지킨다.

**부기(2026-10-04 — S3-2 PR-2a 이행: kind 필터 선행)** — ③을 구현했다: `open_quantity(session, line_kind, line_ids, *, kinds=DEFAULT_OPEN_KINDS)`(기본 `frozenset({"FULFILL"})`), 소비 성격 폐쇄 집합 `CONSUMER_KINDS = {FULFILL, IN_TRANSIT}`(`trade_docs/quantities.py`). 필터 밖 kind의 소비자는 합산하지 않고, **빈 집합·모르는 kind는 `ValueError`**(조용히 소비 0으로 읽혀 초과 소비가 통과하는 길을 닫는 더 엄격한 쪽 — 자율 확정). 현재 등록 소비자 3건은 전부 FULFILL이라 기존 4종 전표 잔량 동작 불변(회귀 시험 `test_open_quantity_default_is_unchanged_for_registered_consumers`·QT/PI/SO e2e 무변경 통과). IN_TRANSIT 임시 소비자로 "기본 = FULFILL만 / `kinds={IN_TRANSIT}` = 배정 가능량 / 둘 다 = 합"을 실측 고정했다(`test_open_quantity_kind_filter_defaults_to_fulfill_only` — 변이 "kind 필터 제거"·"kinds 검증 제거" kill). ①·②·④(소비자 등록·배정 가능량 409)는 PR-3a·PR-5a 몫 그대로. **되돌리기 비용**: 낮음(필터 1줄 — FULFILL만 등록된 동안 무해).
