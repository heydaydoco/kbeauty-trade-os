# ADR-0075: SO CONFIRMED↔IN_SHIPMENT 자동 수렴 2엣지 — §15 "SO 자동 엣지 0" 개정·4금 논증·SO 취소는 후속 생존 검사 선행

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.2·§15(S3-2 [M4] 보강 — 문면 변경) / WBS S3-2 "RESERVED 엣지(SO IN_SHIPMENT) 추가" / ADR-0051·0058 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sA §A7-2, sC §C12, R-02·R-23 / 구현 PR-3a

**맥락** — WBS S3-2는 SO RESERVED 엣지 IN_SHIPMENT를 열라고 하지만 DESIGN §15 S3-1 부기 ②와 `test_po_and_so_have_no_automatic_edges`는 SO 자동 엣지를 0으로 고정했다. 사람 엣지로 열면 선적 없이 IN_SHIPMENT가 되는 거짓 상태가 생긴다. 또 기존 `_cancel_sales_order`는 상태 검사가 후속 생존 검사보다 먼저라 IN_SHIPMENT SO 취소가 `TRANSITION.NOT_ALLOWED`로 나와 커널 원칙(후속 생존 = `SUCCESSOR_ALIVE`)과 어긋난다.

**결정** — ① `AUTO_TRANSITIONS[SO]`에 (CONFIRMED→IN_SHIPMENT)(첫 살아 있는 선적 생성과 같은 TX)·(IN_SHIPMENT→CONFIRMED)(마지막 선적 취소와 같은 TX) 2개를 더하고 `RESERVED[SO]`에서 IN_SHIPMENT를 뺀다. 행위자 = 유발자, `automatic=True`, 이벤트 payload에 `cause_shipment_id`. 불변식: **확정 SO의 IN_SHIPMENT ⇔ 살아 있는 선적 ≥ 1**(통합 테스트). ② IN_SHIPMENT의 ON_HOLD·CANCELLED 엣지는 열지 않는다(선적 선취소 — 재개 판정이 `confirmed_at`만 봐 오도착). ③ **`_cancel_sales_order`는 후속 생존 검사를 상태 검사보다 먼저** 한다 — IN_SHIPMENT SO 취소 = 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`+`detail.successors=[SH-…]`, IN_SHIPMENT 보류 요청은 `TRANSITION.NOT_ALLOWED` 유지. ④ DESIGN §15 부기 개정(문면 변경 — 자율 확정)·테스트 개명("PO 자동 엣지 0, SO 자동 엣지는 IN_SHIPMENT 수렴 2개뿐")·`no_auto_confirm` 엔트리 확장을 세트로 한다. SO 범용 전이 Literal은 사람 엣지만 세므로 **무변경**(신설은 `ShipmentTarget=Literal["CANCELLED"]`).

**근거** — 도착 상태 IN_SHIPMENT·CONFIRMED는 약정 진입(확정)이 아니라 이행 진행의 반영이라 §15 L3 4금(지출·발주 확정/법적 판정/대외 발송/장부 확정)에 해당하지 않는다. QT CONVERTED↔ISSUED 수렴(§7.2 S3-1 부기 ④)과 같은 형태이며 같은 TX라 거짓 상태 창이 없다. 검사 순서는 `record_transition` 원칙(`trade_docs/transition.py`)과 GC-A14 기대 코드를 맞춘다.

**기각한 대안** — 사람 엣지 CONFIRMED→IN_SHIPMENT(선적 없는 IN_SHIPMENT), 진입 트리거를 출고·ETD 실적으로(출고는 RESERVED — S3-2에서 도달 불가), 수렴을 별도 TX/잡으로(§17.1 1동작=1TX 위반·거짓 창), SO Literal에 IN_SHIPMENT 추가(사람 엣지가 아니라 값 공간 불변).

**되돌리기 비용** — **중간**. 엣지 제거 시 IN_SHIPMENT 행을 CONFIRMED로 되돌리는 데이터 정정 마이그레이션 1건 + 보드 '선적중' 열·SO 취소 안내 원복. DESIGN 문면 변경이라 **오너 확인 권장 3순위**(번복 시 이 ADR을 "대체" 표기). 검사 순서 변경은 낮음.

**부기(2026-10-04 — S3-2 PR-3a 이행: 수렴 2엣지·검사 순서)** — ①~④를 구현했다. 판정 함수는 하나다 — `trade_chain.chain_ops.converge_sales_order_shipping`(SO `FOR UPDATE` → `has_live_children(SO, child_table="shipments")` → `record_transition(automatic=True, cause_shipment_id=…)`), 선적 생성·라인 삭제(항상 no-op)·선적 취소가 `converge_parent(SHIPMENT)`로 같은 함수를 부른다(X-13). `cause_shipment_id`는 커널 `PAYLOAD_KEYS`에 더했고 자동 전이에서만 받는다(사람 전이에 넘기면 TypeError). 불변식은 실제 동시 시험(J-04 생성 vs SO 취소·J-05 마지막 선적 취소 vs 새 선적·동시 취소 2건 → 복귀 이력 정확히 1행)이 고정한다. ③은 별도 커밋(`_cancel_sales_order` — 종결 아닌 SO는 후속 생존 검사가 먼저, GC-A14 golden). `no_auto_confirm` 엔트리에 생성·출고지시·취소·수렴 함수를 더했고, SO 범용 전이 Literal은 무변경·`ShipmentTarget = Literal["CANCELLED"]`가 `public_transition_targets(SHIPMENT)`와 같음을 라우터 import 시점에 단언한다.
