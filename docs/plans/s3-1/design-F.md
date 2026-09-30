# S3-1 계획서 안건 전문 — 묶음 F: PO 범위 · 역할/소유권 · 마스터 정합 · 이월 소비

- 작성: 심판(최종 설계자) · 정본: DESIGN.md §2·§3·§7·§17·§18·§20·§22, WBS.md S3-1, 코드 실측(2026-09-30 리포) + 형제 묶음 결정문(d-A·d-B·d-C·d-D)
- 성격: 설계 결정(구현 아님). 전 안건 **자율 확정**(오너 지시 2026-09-29 — 판정 후보를 권장안으로 확정, 사후 번복 가능). "미정" 결론 없음.
- 안건: F1~F20. 3개 제안의 X/F 번호는 아래 §0.3 대응표로 흡수했다.

---

## 0. 심판 노트

### 0.1 직접 검증한 사실 (제안 3종의 주장 → 판정)

| 제안의 주장 | 검증 결과 |
|---|---|
| (P2) MATERIAL 라인 단가 = `materials.unit_cost`+currency 복사 | **틀림.** `materials` 테이블(`materials/models.py` Material)에는 단가·통화·단위 컬럼이 없다. `unit_cost`·`currency`·`quantity_unit`은 **`product_boms`**(BOM 라인) 컬럼이다. 자재 라인의 가격 원천·단위는 DESIGN에 없다 → 자재 PO는 추측 구현이 된다(F1). |
| (P3) materials에 단위 컬럼이 실측상 없음 | 사실. (P1이 제안한 `qty_unit` 자유 문자열은 DESIGN에 없는 발명 — §8.2는 원장 "기본 단위(EA)로만·수량=정수", 사급 수율·단위는 P4 착수 ADR 항목.) |
| (전 제안) PO 라인 품목 = SKU\|MATERIAL 폴리모픽이 ADR-05(재입력 금지)상 필요 | **과잉.** WBS S3-1 산출물에 자재 발주가 없고 IN_PO 자재 입고·사급은 S4-1(P4 ADR)이다. SKU 전용→자재 확장은 nullable 컬럼 추가+CHECK+수량 확폭의 **가산 마이그레이션**이고 기존 SKU 라인 데이터는 재입력이 필요 없다. A 묶음(A12)도 "F 위임 가정 — SKU 전용"으로 설계했다. |
| (P1·P2·P3) PO 상태에 DRAFT를 둔다 | **B 묶음 결정과 충돌·기각.** DESIGN §7.2 PO = "**발행**→공급사확인(OC)→…"(초안 없음). B는 PO 6값·전이 3방향·생성=발행으로 확정했고 A는 `frozen_at NOT NULL DEFAULT now()`로 설계했다. 초안이 없으므로 "발행 시 재검증·초안 편집 후 동결" 논리는 전부 "생성 시 1회"로 수렴한다. |
| (P1·P2) PO 라인 `expected_receipt_on`(발행 후 변경 가능 유일 컬럼) 신설 | **기각.** 소비자가 S3-1에 없다: WBS "백오더 보드(가용일 추종은 **P4 연결**)". B는 FREE 컬럼을 정확히 4개(내부메모·assignee_id·oc_reference·oc_received_on)로 고정하고 `expected_receipt_date`를 "문면 침묵"으로 만들지 않았다. 발행 후 변경 가능한 신규 컬럼은 불변 트리거 예외+전용 엔드포인트+이력이라는 별도 기능이다 → 소비 세션이 가산(F6). |
| (P1) 금액 컬럼명 `unit_price_amount`/`line_amount`/`total_amount` | **로그 마스킹 회피.** `core/logging/redaction.py`: SENSITIVE_KEYS에 `unit_cost`·`cost`·`landed_cost`·`purchase_price`·`purchase_amount`, SENSITIVE_SUFFIXES에 `_cost`·`_margin`. **`amount`·`price`는 의도적으로 통째 제외**(판가·전표금액은 원가 아님). → PO 원가 컬럼은 A12의 **`unit_cost`·`line_cost`·`total_cost`**여야 로그 마스킹이 코드 변경 없이 걸린다(P2의 관찰이 맞았고 A12 명명이 정답). |
| (P2) VIEWER는 물론 CERT의 PO 접근도 403(최소권한) | **기각.** `catalog/pricing.py COST_VISIBLE_ROLES = (ADMIN, TRADE, LOGISTICS, CERT)` — 주석: "§2는 '조회'만 마스킹, 문언 그대로 나머지 네 역할은 볼 수 있다". PO 금액도 같은 단일 출처(`may_see_cost`)를 재사용한다(ADR-0024 ③). 새 역할 제한을 만들면 두 곳이 갈린다. |
| (P1) `price_at(session,…)` 호출 | 시그니처는 `price_at(*, sku_id, price_type, currency, on=None)` — session 인자 없음, 내부에서 `unit_of_work()`(바깥 UoW에 **합류**), 권한 검사 없음, 부재 시 `CATALOG.PRICE.NOT_EFFECTIVE`(0/null 반환 없음). 사실 확인. 호출 경로를 PO 쓰기 서비스(ADMIN·TRADE ⊂ 원가 가시 역할)로 한정하는 것은 `pricing.py` 주석("권한은 호출자 몫")과 일치. |
| (P1·P2·P3) 폴리모픽 documents.owner_type 확장·확폭 | 사실 확인: `documents/models.py:68` `("SKU","LABEL","CERTIFICATION","COMM_LOG")`, `String(13)`. `ORDER_INTAKE`=12자(확폭 불필요), `PURCHASE_ORDER`=14자·`PROFORMA_INVOICE`=16자(확폭 필요). 그러나 **S3-1에 소비자가 없다**: D 묶음이 "원본 파일 보관 없음(S6-1 예약)"으로 확정. → F13은 documents 무변경. |
| (P1·P2·P3) ADR-0013/0014 청소 배치가 JOB_REGISTRY에 없음 | 사실. `scheduler.py JOB_REGISTRY` 7행(certification-sweep·outbox-dispatch·deadline-scan·daily-briefing·stagnation-scan·storage-monitor·backup-freshness). ADR-0014 결정문: "만료분 청소는 `scheduled_jobs`의 배치로 등록한다(실행기는 S2-3…)". `idempotency/service._purge_expired`·`identity/service._purge_expired_sessions`는 **해당 사용자분만** 치운다(독스트링이 "전역 청소는 배치의 일"). 약속 미이행 확인(F18). |
| (P1·P2·P3) 쓰기 스키마 forbid 미설정 32/45 | AST 실측 재현: `app/modules/*/schemas.py`의 `…Request` 45개 중 forbid 없음 **32개**(catalog 7·requirements 7·partners 3·materials 3·ingredients 3·identity 3·documents 2·markets 2·handover 1·worklist 1). 이름 목록은 F17에 고정. |
| (P3) 자재 확정 쪽은 verify_references로 이미 닫힘 | 사실(`PROGRESS` 유형 해제 항목 원문). 반대 방향의 훅 자리도 실재: `imports/registry.py PartnersImportTarget.verify_references`가 지금 `return []`이고, `imports/service.py:600`이 그 반환을 "전체 409(`IMPORTS.CONFIRM.VERSION_CONFLICT`)"의 문제 행으로 모은다 → 신규 에러코드 없이 구현할 자리가 이미 있다(F11). |
| (P1·P3) handover 등록은 `AssignmentTarget('테이블명', Model, Model.assignee_id)` | 사실(`tests/architecture/test_assignment_coverage.py`가 `(target.label, column.key)`를 `(테이블명, 컬럼명)`과 대조 — label=테이블명이어야 함). |
| (P2·P3) 자기 승인·ADMIN 미통과 세부 | **C 묶음이 이미 확정**(C4: ADMIN은 자격자이나 자기 기안 승인 불가·승인 기록 없이 확정 불가). F는 C를 단일 출처로 받고 매트릭스에 그대로 옮긴다(F8). P2/P3의 "ADMIN 상시 통과 미적용"은 C4와 다르므로 채택하지 않는다. |
| (P2) 소유권 = 담당자 한정 쓰기(`NOT_ASSIGNEE` 403) | **B·D 묶음과 충돌·기각**(F9). B8: "소유권: 역할 기준(개인 소유 필터 없음 — 협업 관용, F가 §20 K 해석 확정)". D7: "담당자는 접근 제한이 아니라 이관 단위". D6 벌크 ASSIGN은 비담당자 TRADE도 수행. |

### 0.2 형제 묶음과의 정합 요약(F가 전제하는 사실)

- A: 모듈 배치 L0 `trade_docs` + L1 `quotations/proforma_invoices/sales_orders/purchase_orders`. PO 헤더 `supplier_partner_id`·`supplier_name`(스냅샷), 라인 `sku_id NOT NULL`·`quantity INT(EA)`·`unit_cost/line_cost BIGINT`·`price_basis IN ('MASTER','MANUAL')`·`requested_delivery_date`, 헤더 `total_cost`·`currency`, `frozen_at NOT NULL DEFAULT now()`(PO·PI). 채번 접두 QT/PI/SO/PO, `trade_docs.DOC_PREFIXES`. partners에 `name_en`·`address_en` 가산. 라인 통화는 헤더와 복합 FK로 일치 강제.
- B: 상태 PO 6값(ISSUED·SUPPLIER_CONFIRMED·PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED·CANCELLED), 전이 3방향, RESERVED 3, OC 부속 `oc_received_on`(필수)·`oc_reference`, FREE 4개, 전이 통로 `record_birth`/`record_transition`, 전표 삭제 없음, 잠금 순서(거래처→사슬→라인→시퀀스), 55P03/40P01→409.
- C: `approvals`(SO_CREDIT_EXCEEDED 1유형, PO 승인 없음), 자격=역할 기반·ADMIN 자격자·자기 승인 금지, `GET /approvals/delegation-candidates`.
- D: `order_intakes` 별도 테이블·원본 파일 미보관, 보드 벌크 3종(CONFIRM_INTAKE·CONFIRM_SO·ASSIGN), `GET /users/lookup`(F 확인 요청), 검색형 콤보박스·`/partners?q&type`·`/skus?q`.

### 0.3 제안 ID → 이 문서 안건 대응

| 제안 | → |
|---|---|
| P1 F1-a / P2 F1(1) / P3 F1(1) | F1 |
| P1 F1-c·F1-e·F1-f·F1-g / P2 F1(4)(6)(7)(8) / P3 F1(3)(5)(6)(7) | F2 (F1-f 기각, F6 이월) |
| P1 F1-b·F1-j / P2 F1(3) / P3 F1(2) | F3 |
| P1 F1-l / P2 F1(2) / P3 F1(8) | F4 |
| P1 F1-h·F1-i / P2 F1(9)·X4 / P3 F1(9)(10)(11)·X2 | F5 |
| P1 F1-d / P2 F1(5) / P3 F1(4) | F6 |
| P1 F1-k / P2 F1(10) / P3 F1(12) | F7 |
| P1·P2·P3 F2-a | F8 |
| P1·P2·P3 F2-b·F2-e(사용자 디렉터리) | F9 |
| P1·P2·P3 F2-c·F2-d | F10 |
| P1·P2·P3 F3(+X1) · F3-b | F11 |
| P1·P2·P3 F3-c | F12 |
| F4-a·F4-b / P2 F4(1)(2)(3)·X5 | F13 |
| F4-c | F14 |
| F4-d | F15 |
| F4-e | F16 |
| F4-f | F17 |
| F4-g | F18 |
| F4-h | F19 |
| X1(P1 문서 세트)·P2 X1~X3(통로·삭제·잠금) | F20(문서 세트). 통로·삭제·잠금은 B8·B9가 확정 — F는 F2·F5에서 PO 몫만 소비 |

---

## F1. PO 라인 품목 = **SKU 전용** (자재 PO는 P4 이월, 가산 확장 경로 고정)

### (a) 목적·경계
PO 라인이 무엇을 발주할 수 있는가. 경계: 라인 컬럼 전체 골격은 A12, 여기서는 "품목 종류"만 판정한다.

### (b) DESIGN 근거
§7.1(PO→수입선적→통관→입고, IN_PO 원장 ref는 입고 문서), §8.1(원장 주체=SKU 또는 재고관리 자재), §8.2(원장 기본 단위 EA·수량=정수), §8.5(사급: 자재 구매 입고 IN_PO — Phase 4 원장 ADR에 수율 세부), §4.4(materials 재고관리 플래그 — "원장 편입은 P4"), WBS S3-1 산출물(자재 발주 문구 없음), S4-1(착수 ADR 5항목에 사급 수율), ADR-0041(소비 코드 없는 열거·문 금지), 코드: `materials` 테이블에 단가·단위 컬럼 없음.

### (c) 데이터 변경
- 변경 없음(A12 골격 확정): `purchase_order_lines.sku_id BIGINT NOT NULL FK skus RESTRICT`, `quantity INT CHECK 1..99,999,999`(EA), `unit_cost/line_cost BIGINT`.
- 서비스 규칙: 라인 SKU는 A의 단일 통로 `trade_docs.lines.require_sellable_sku(session, sku_id, doc_kind='PURCHASE_ORDER'…)`(삭제·DISCONTINUED 차단 — A 계약)를 재사용. 세트 SKU는 1라인 그대로(A11).
- 요청 스키마(`extra=forbid`)에 `material_id`·`item_type` 필드가 **없다**(밀반입 시 422).
- **가산 확장 경로(P4 소비 세션 규약 — ADR-F1에 명문)**: ① `sku_id DROP NOT NULL` + `material_id BIGINT NULL FK materials RESTRICT` + `CHECK num_nonnulls(sku_id, material_id)=1` ② `quantity INT → NUMERIC(14,4)`(무손실 확폭) + `uom VARCHAR(10) NOT NULL DEFAULT 'EA'` ③ 자재 라인 단가는 가격 마스터가 없으므로 `price_basis='MANUAL'` 강제 ④ 재고관리(`inventory_managed=true`) 자재만 허용. 마이그레이션 성격: 기존 데이터 무손실 ALTER(기존 SKU 라인 재입력 불필요).
- 마이그레이션 성격: **없음**(A의 신규 테이블 생성 리비전에 포함).

### (d) 4금
자재 라인이 없으므로 무관. PO 생성=발주 확정의 4금은 F2.

### (e) 상태·전이·불변
라인은 생성 시 동결(PO는 편집 구간 없음 — B2 `EDITABLE_STATES['PO']=∅`). 품목·수량·단가 변경 = 취소+신규.

### (f) 테스트 배분
- A(서비스 전수): 삭제·DISCONTINUED SKU 라인 422 / 세트 SKU 1라인 / 요청 본문에 `material_id`·`item_type` 422.
- K(아키텍처): `purchase_order_lines`에 `material_id` 컬럼 부재를 명시 단언하지 **않는다**(가산 시 테스트 수정이 헛수고) — 대신 요청 스키마 필드 집합 스냅샷 테스트가 밀반입을 잡는다.
- 변이 점검: `require_sellable_sku` 호출 삭제 → 실패.

### (g) 소비·등재
소비: P4(S4-1 사급 ADR) — 위 확장 경로. **부채·관찰 등재**: "자재 PO 미지원(트리거: S4-1 사급 ADR 또는 오너의 자재 발주 요구)". DESIGN §7.1 보강 1줄(PO 라인=SKU, 자재 라인은 P4).

### (h) 되돌리기 비용 — **낮음~중간**
SKU 전용→폴리모픽은 가산 ALTER(무손실). 반대(폴리모픽→전용)는 자재 라인이 쌓인 뒤라 비싸다 → 좁게 시작하는 쪽이 안전. 근거: 자재 라인은 단위·수량 타입·가격 원천 세 가지가 DESIGN에 없어 지금 정하면 추측이다.

---

## F2. PO 생성=발행(사람 1클릭 단일 통로)·상태·OC — **B 결정 채택 + F 보강**

### (a) 목적·경계
PO의 상태·전이·발주 확정 통로를 B와 하나로 맞춘다. 경계: 전이표·상태 이력·불변 분류는 B(B1·B2), F는 PO 고유 API·4금 강제·OC 경계를 소유.

### (b) DESIGN 근거
§7.2 "PO: 발행→공급사확인(OC)→(수입선적 연결)→부분입고→입고완료→종결 / 취소"(초안 상태 없음, "(수입선적 연결)"은 괄호 = 선택 단계), §15 L3 4금 ①(발주 확정 자동화 금지)·ADR-09(자동 확정 예외는 인테이크 전표까지, 지출·발주 확정 불가), §2 승인 대상 6종(PO 발행 없음), §17.1·17.4, ADR-0038·0041.

### (c) 데이터 변경
스키마 신규 없음. B/A 확정본을 그대로 사용:
- `purchase_orders.status VARCHAR(20)` CHECK 6값 `ISSUED, SUPPLIER_CONFIRMED, PARTIALLY_RECEIVED, FULLY_RECEIVED, CLOSED, CANCELLED`(RESERVED 3 = 입고 후반).
- `oc_received_on DATE NULL`, `oc_reference VARCHAR(100) NULL`(B2 CHECK). **F가 추가하는 서비스 경계**: `oc_received_on`은 `doc_date ≤ oc_received_on ≤ today_kst()`(미래·발행일 이전 422 `VALIDATION_INVALID_FIELD`). 근거: OC는 공급사 회신의 **사람 기록**이고 발행 전 회신은 불가능. (B 확인 요청: B2 PO 부속 필드 검증 목록에 이 경계 추가.)
- 헤더 `internal_note`·`assignee_id`·`oc_reference`·`oc_received_on`만 발행 후 편집(B2 FREE 4개 — **5번째 추가 요구 없음**: F1 제안의 `expected_receipt_on`은 기각).

### (d) 자동화 4금
**발주 확정 = PO 생성 요청 자체**다. 사람이 세션 로그인 상태에서 1클릭(확정 버튼)으로 `POST /api/v1/purchase-orders`를 호출하는 경로가 유일하다.
- 경로: ① 라우터 1곳 ② `Idempotency-Key` 필수 ③ 스케줄러·CLI·outbox 핸들러·imports·order_intake·order_board·approvals·gates·seeds 어디서도 `create_purchase_order`를 import하지 않는다(AST) ④ 오더 보드 벌크 액션 열거에 PO 없음(D6: CONFIRM_INTAKE·CONFIRM_SO·ASSIGN) ⑤ §8.8 발주점 권고(P4)도 PO를 생성하지 않는다(A12 (d)) ⑥ `copied_from_id` 복제 = "GET 상세로 프리필 후 사람이 확정 클릭"(별도 copy 엔드포인트 없음 — B8).
- **화면 계약**: 발주 화면은 2단이다 — ① 입력+`POST /purchase-orders/preview`(비저장·채번/감사/이벤트/멱등 소비 없음, F4의 단가 계산 결과를 보여 줌) ② 확정 버튼 → 생성. 확정 버튼의 Idempotency-Key는 폼 인스턴스당 1개(더블클릭·재전송은 최초 결과 재생 — D5의 SO 확정 키 규칙과 동형).
- 승인 게이트: **없음**(§2 열거에 PO 발행 없음). 임계 초과 PO 승인이 요구되면 C의 `APPROVAL_TYPES` 가산 절차(C 안건 ⑤)로 수용 — S3-1 비포함.
저촉 없음. 자동 경로 부재를 아래 테스트가 기계 고정.

### (e) 상태·전이·불변
B 정본: 생성=ISSUED(`record_birth`) / ISSUED→SUPPLIER_CONFIRMED(OC) / ISSUED→CANCELLED / SUPPLIER_CONFIRMED→CANCELLED(사유 필수·후속 생존 시 409 — B3 `chain.has_live_children`, PO_LINE 소비자 레지스트리는 S3-1 등록 0건). RESERVED 3값은 in/out 엣지 0. 전이 요청 = `POST /purchase-orders/{id}/transitions`(B8 `TransitionRequest`, `to` Literal에서 RESERVED·자동 제외) + Idempotency-Key. **전이 시 거래처 재검증 없음**(OC·취소는 신규 약정이 아니다 — 재검증은 생성 1회, F3).
불변: 발행(생성) 후 품목·수량·단가·통화·거래처·po_kind 변경 불가(B2 CONTENT/ORIGIN 동결).

### (f) 테스트 배분
- K(아키텍처): `test_no_auto_confirm_code_path_exists`에 PO 항목 — (1) `create_purchase_order` 호출처 집합 = {`purchase_orders/router.py`} (2) `scheduler`·`cli`·`outbox` 핸들러·`imports`·`order_intake`·`order_board`·`approvals`·`gates` 모듈이 `purchase_orders.service`를 import하지 않음(양방향 자기검사: 위반 픽스처 주입 시 실패) (3) `JOB_REGISTRY` 잡 전건이 PO 서비스 미참조 (4) 오더 보드 벌크 액션 열거에 PO 값 부재 (5) `purchase_orders` 요청 스키마에 `status`·`doc_number`·`frozen_at`·`version` 부재+`extra=forbid`.
- A(서비스 전수): 전이 3성공+나머지 전 쌍 409(B의 126쌍 파라미터에 PO 30쌍 포함) / OC 날짜 경계(발행일 전·미래·당일) / OC 후 `oc_reference` 정정(FREE) 성공 / OC 없이 CLOSED 진입 시도 409.
- J: 생성 더블클릭 → PO 1건·번호 1개·이벤트 1건(멱등 키) / 같은 키 다른 본문 409 / 채번 실패 시 전 롤백.
- H: 동시 두 취소 → 1건 성공·1건 409.
- 변이 점검: 생성 통로에서 `Idempotency-Key` 검사 삭제, `record_birth` 이벤트 발행 삭제, PO 호출처 스캔의 허용 집합 확대 — 각각 실패.

### (g) 소비·등재
소비: B(전이 통로·이력·이벤트), S3-2(수입선적 연결 엣지 추가 시 RESERVED 감소), S4-1(입고 후반 엣지). DESIGN §7.2에 "PO는 초안 없음·생성=발행=발주 확정, OC 부속 필드"를 B가 명문화(F는 문구 1줄 동의). 관찰 등재: **PO 라인 ETA 컬럼 미도입**(F6).

### (h) 되돌리기 비용 — **중간**
초안 도입은 CHECK 재정의+동결 시점 재정의(B 기술). 그러나 §7.2 문면 그대로라 번복 사유가 약하다. 생성 통로 단일성은 처음부터 지키는 것이 가장 싸다.

---

## F3. PO 공급사 유형 검증·`po_kind` 슬롯

### (a) 목적·경계
PO 상대가 공급사/OEM인지 검증하는 시점·방법, OEM 생산 발주 구분 컬럼(§4.4).

### (b) DESIGN 근거
§7.1·§4.6(partner_type_links — 다중 유형, ADR-0026), §4.4 "OEM 생산 발주(PO)에는 생산 마일스톤 프로파일 적용 가능", §7.7 "차단 지점은 등록이 아니라 게이트", PROGRESS "[S1-2] §4.4 OEM 생산 발주 마일스톤 프로파일 — 재판정 트리거 S3-1/S3-2 계획 보고", 코드 `partners.service.require_partner_of_type/partner_has_type`(단일 유형만).

### (c) 데이터 변경
- `purchase_orders.po_kind VARCHAR(16) NOT NULL DEFAULT 'PURCHASE'` + `CHECK po_kind IN ('PURCHASE','OEM_PRODUCTION')`(`value_in`, 네이티브 ENUM 금지) — **A의 신규 테이블 생성 리비전에 포함**(신규 테이블이라 별도 ALTER 없음). B FIELD_POLICY 분류: CONTENT(생성 시 동결), A `FROZEN_HEADER_COLUMNS`에 `po_kind` 추가.
- StrEnum `PoKind`. 마일스톤 프로파일 FK·컬럼은 만들지 않는다(F7).
- 규칙(서비스, 검증은 트랜잭션 안 거래처 행 `FOR SHARE` 후):
  - `PURCHASE`: 거래처 활성(soft delete 아님)+활성 유형 링크 SUPPLIER 또는 OEM 중 하나 이상.
  - `OEM_PRODUCTION`: 활성 유형 OEM 필수.
  - 위반은 기존 코드 `COMMON.VALIDATION.INVALID_FIELD`(detail=`{"supplier_partner_id": "…'공급사' 유형이 아닌 거래처입니다. 거래처 화면에서…"}` — `require_partner_of_type`의 문구 패턴 재사용). **신규 에러코드를 만들지 않는다**(D의 "열거 폭증 방지" 원칙).
- 신규 서비스 함수 `partners.service.require_partner_of_any_type(session, partner_id, type_codes, *, field, type_label, lock=False)`(A12가 요구한 래퍼) — `lock=True`면 거래처 행 `FOR SHARE`. 인테이크·QT·PI·SO의 BUYER 검증도 같은 함수(단일 코드 `("BUYER",)`)로 통일(호출자 = A/D).

### (d) 4금
없음(검증은 게이트).

### (e) 상태·전이·불변
`po_kind`는 생성 후 불변. PO는 생성 1회만 검증(초안 없음). 이후 거래처 유형 해제는 기존 PO를 무효화하지 않는다(역사 전표) — F11.

### (f) 테스트 배분
- A(서비스): 유형 없는 거래처 422 / BUYER 전용 거래처로 PO 422 / SUPPLIER만 있는 거래처로 `OEM_PRODUCTION` 422 / SUPPLIER+OEM 겸유 거래처 두 kind 모두 통과 / soft delete 거래처 422(평가 불능=통과 아님) / `po_kind` CHECK 외 값 DB 거부(IntegrityError 직접 삽입).
- J(동시성 실스레드): PO 생성 × 유형 해제 임포트 확정 동시 → 어느 순서든 "유형 없는 채로 PO 생성 성공" 상태 불가(거래처 `FOR SHARE` ↔ 해제 경로 `FOR UPDATE`).
- K: `po_kind` 값 ↔ StrEnum ↔ CHECK 정의문 1:1(`pg_get_constraintdef`), `po_kind`가 `FROZEN_HEADER_COLUMNS` 소속.
- 변이 점검: 유형 검증 호출 삭제·`lock=True` 제거·OEM 분기 삭제.

### (g) 소비·등재
소비: S3-2(마일스톤 프로파일이 `po_kind='OEM_PRODUCTION'`을 키로 소비). PROGRESS "OEM 생산 발주 마일스톤" 항목 종결 부기: "S3-1 = 구분 슬롯(po_kind) 완결·검증 소비, 프로파일 적용 = S3-2".

### (h) 되돌리기 비용 — **낮음**
컬럼 1개+CHECK. S3-2가 다른 구분 방식을 택하면 CHECK 재정의 1건. 추가 이유: 다중 유형(SUPPLIER∧OEM) 거래처에서 유형만으로는 생산 발주 여부를 알 수 없고, 후속 세션이 나중에 구분을 붙이면 S3-1~S3-2 사이 발행된 PO를 재분류해야 한다(ADR-05 재입력 금지).

---

## F4. PO 단가 원천 — `price_at(PURCHASE)` 기본 + 수동 입력, 명명 `_cost`

### (a) 목적·경계
PO 라인 단가를 어디서 오고 어떻게 남기는가. 경계: 컬럼·CHECK 골격은 A12, F는 원천·분기·오류·호출 규율.

### (b) DESIGN 근거
§4.1(가격 이력 — 기준일 단가, "없으면 0·null이 아니라 오류 — S3-1 전표 스냅샷이 이 함수를 소비"), ADR-0017(발효일 이력·종료일 없음), ADR-0018(PURCHASE=원가), §2 ADR-02(금액=정수 최소단위), 코드 `catalog/pricing.py price_at`.

### (c) 데이터 변경
스키마 신규 없음(A12): `unit_cost BIGINT NOT NULL CHECK > 0`, `line_cost BIGINT NOT NULL`(= `quantity × unit_cost`, DB CHECK), `price_basis VARCHAR(10) CHECK IN ('MASTER','MANUAL')`, 헤더 `total_cost = Σ line_cost`(A9-6), `currency CHAR(3)`(헤더·라인 복합 FK로 일치).
서비스 규칙:
1. 요청 라인에 `unit_cost` 미입력 → 서버가 `price_at(sku_id=…, price_type='PURCHASE', currency=헤더 통화, on=doc_date)` 호출, `price_basis='MASTER'`. **이력 없음·미발효는 `CATALOG.PRICE.NOT_EFFECTIVE`를 그대로 전파해 422**(0·null 대체 금지, fail-visible). 사용자는 그 응답에서 "단가를 직접 입력"으로 진행할 수 있다.
2. 요청 라인에 `unit_cost` 입력(사람 표기 Decimal — `SkuPriceCreateRequest.amount`와 같은 규약, 서버가 `money.minor_units`로 최소단위 변환, float 금지) → `price_basis='MANUAL'`, `price_at` **미호출**. 마스터 값과 같아도 MANUAL(입력 경로가 기준).
3. `unit_cost > 0`(무상 매입 미지원 — A12) · 상한 2^53−1(A) · 자릿수 초과 422.
4. `price_at`은 **PO 생성·preview 서비스 안에서만** 호출(권한 검사는 호출자 몫 — 생성 권한이 ADMIN·TRADE ⊂ 원가 가시 역할). `price_type="PURCHASE"` 리터럴은 `purchase_orders` 모듈 밖 0건(A의 K 테스트 재확인).
5. 스냅샷은 생성 시 1회. 이후 마스터 단가 변경은 전표 불변.
6. `price_at`은 자체 `unit_of_work()`를 열지만 바깥 UoW에 합류하므로 "한 동작=한 트랜잭션"이 유지된다(코드 확인).

### (d) 4금
없음(단가는 사람이 확정 클릭하는 PO의 구성 값).

### (e) 상태·전이·불변
생성 후 불변(F2).

### (f) 테스트 배분
- A: 단가 이력 3건 중 `doc_date` 기준 정확 선택(경계: 발효일 당일·전날) / 기준일 단가 없음→422 `CATALOG.PRICE.NOT_EFFECTIVE`(라인 0건 저장) / 수동 입력 시 `price_at` 미호출(호출 스파이) / 발행 후 마스터 단가 변경해도 라인 불변 / 통화 다른 이력만 있으면 NOT_EFFECTIVE / `unit_cost=0`·음수·과다 자릿수 422 / 소급 `doc_date`에서 그 시점 단가.
- G: 오류 응답·로그에 단가 값 부재(F5 채널 테스트와 통합).
- K: `price_type` 리터럴 위치 스캔, `unit_cost`·`line_cost`·`total_cost` 컬럼명이 `_cost` 접미(로그 마스킹 자동 편입) — 이름을 `_amount`로 바꾸면 `test_cost_never_reaches_logs`가 실패하는지 양방향 확인(변이 점검).
- 변이 점검: `price_at` 분기 제거, MANUAL 시 기본값 폴백(0 저장) 추가 → 실패.

### (g) 소비·등재
소비: S3-4 원가·비용 코어(PO 매입가=원가 원천 — 마진은 S3-4/S6-2). 마스킹 원장 행 갱신(F5).

### (h) 되돌리기 비용 — **낮음**
MANUAL 허용 제거는 서비스 가드 1줄(저장된 MANUAL 행은 남음). 컬럼 rename은 신규 테이블이라 지금 무료, 데이터 이후엔 마이그레이션+전 코드 변경 → 지금 확정.

---

## F5. PO 원가 마스킹 — 혼합 목적 행 = **필드 부재**(ADR-0024) + 부수 채널 전수 봉쇄

### (a) 목적·경계
조회(VIEWER)에게 PO의 원가가 API·CSV·이벤트·로그·감사·에러·멱등 저장 어디로도 새지 않게 한다. 경계: 역할 매트릭스는 F8, 스키마 골격은 A12.

### (b) DESIGN 근거
§2 "조회는 원가·마진을 API 응답 레벨에서 마스킹", §18.1(로그 마스킹 "화면에서 못 보는 값이 로그에 평문이면 통제가 우회된다"), ADR-0018(행 단위 — 행의 존재 목적이 원가일 때)·ADR-0024(필드 부재 — 혼합 목적 행)·ADR-0008(로그 마스킹).

### (c) 데이터 변경
스키마 없음. 판정: **PO 행은 혼합 목적 행**이다 — 상태·공급사·품목·수량·요청납기·OC 일자는 물류·조회 업무 데이터이고 원가는 3~4칸(`unit_cost`·`line_cost`·`total_cost`·`currency`)뿐. 행 수(PO 건수)는 원가 정보가 아니다(ADR-0024 논지). → **행 단위 숨김(404) 기각, 필드 부재 채택**.
- 응답 스키마 2종(목록·상세·라인 각각): `PurchaseOrderDetail` / `PurchaseOrderCostHiddenDetail`, `PurchaseOrderSummary` / `PurchaseOrderCostHiddenSummary`, 라인 `…Line` / `…LineCostHidden`(명명은 `BomLineCostHiddenSummary` 선례). CostHidden에서 **키 자체 부재**: 헤더 `total_cost`·`currency`, 라인 `unit_cost`·`line_cost`·`price_basis`. (`price_basis`는 협상가 여부를 드러내는 가격 정보라 함께 숨김.)
- 갈림은 라우터 경계 1곳: `response_model=None` + `may_see_cost(actor)`로 스키마 선택(FastAPI Union 자동 분기는 실측 실패 — ADR-0024). 직렬화 후 dict 키 삭제 금지. CostHidden 클래스는 Full을 상속하지 않고 금액·`currency`·`price_*` 필드를 가지면 아키텍처 테스트가 실패.
- 채널별 규칙(전부 S3-1 PO 몫):
  1. **정렬·필터·검색**: PO 목록의 `sort`는 `Literal` 화이트리스트(발행일·번호·상태·공급사명 등 — **원가 값 없음, 전 역할 공통**), 원가 범위 필터 파라미터는 아예 만들지 않는다. 화이트리스트 밖 값은 422(FastAPI는 미지정 쿼리 키를 무시하므로 `Literal`이 유일한 방어). 검색 `q`는 번호·공급사명·SKU 코드 부분 일치(원가 무관).
  2. **CSV**: PO CSV 헤더 상수 2종(원가 열 포함/미포함), 역할별 빌더. F15.
  3. **에러 detail·검증 메시지**: 단가·합계 값을 문자열에 넣지 않는다(`price_at` 실패 detail은 기준일·통화·SKU만 — 기존 코드 확인: `effective_from`/currency/on). Pydantic 오류의 `input` 에코는 핸들러가 이미 차단(`handlers.py`) — 커스텀 `ValueError` 메시지에 입력값 interpolate 금지.
  4. **로그**: `unit_cost`(SENSITIVE_KEYS)·`_cost` 접미(SENSITIVE_SUFFIXES)로 자동 마스킹 — 신규 로그 키·`log_context`에 원가 값 기재 자체를 금지(마스킹은 이름 기반이라 `currency`·수량 조합 등 값 추론은 막지 못한다). f-string으로 원가를 예외·로그 메시지에 넣는 코드를 AST로 금지.
  5. **audit_log.detail**: `{doc_number, supplier_partner_id, line_count, po_kind}`만. 원가 전후 값 기재 금지(PO는 생성 후 불변이라 변경 이력도 없음).
  6. **outbox 이벤트·알림·브리핑**: payload 화이트리스트 `{doc_id, doc_number, status, supplier_partner_id, assignee_id, automatic}` — B6 화이트리스트와 동일 키. 원가·통화·`price_*` 금지. `notify()` title/body에 원가 금지.
  7. **idempotency_keys.response_body**: 생성 응답(Full 스키마 — 생성자=ADMIN·TRADE는 원가 가시 역할)이 (actor,endpoint,key) 스코프로 24h 저장된다. 타 사용자 노출 없음, 원가가 at-rest로 남는 점은 F18 청소 잡으로 보존 최소화 — ADR-F1에 "수용 사실"로 명시.
  8. **documents 첨부**: S3-1에 PO 첨부 없음(F13). 후속에서 PO 첨부(owner_type 확장)를 추가하는 세션은 **원가 가시성 검증(VIEWER 403)을 소유 전표 R 권한과 함께 검사**해야 한다 — 마스킹 원장에 사전 등재.
  9. **집계·보드·대시보드**: SO·QT·PI·보드·인테이크 응답에는 cost·margin·purchase 계열 필드를 **어느 역할에도** 도입하지 않는다(마진은 S3-4·S6-2 몫). 응답 모델 필드명 스캔이 강제.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음(계약).

### (f) 테스트 배분
- G(조회 역할 원가 마스킹): 5역할 × (상세·목록·라인·CSV) 응답 JSON **재귀 키 스캔** — VIEWER 응답에 `*_cost`·`currency`·`price_*` 키 0, ADMIN·TRADE·LOGISTICS·CERT는 존재 / VIEWER 상세·목록 **200**(404·403 아님)·행 수·`total` 건수 4역할과 동일 / `sort=total_cost`·`sort=unit_cost` 422 / 센티널 원가(예 7654321987)를 생성 후 VIEWER로 목록·상세·CSV·정렬·에러 유도·알림함을 순회해 응답·`caplog`·`events.payload`·`alerts.body`·`audit_log.detail` 전부에서 0회(로그 캡처는 `test_cost_never_reaches_logs` 패턴 재사용).
- K(아키텍처): `test_po_cost_masking.py`(BOM 선례 `test_bom_cost_masking.py` 계승): CostHidden 스키마 필드명이 정규식 `(_cost$|^cost$|^currency$|^price_)`에 걸리지 않음 / CostHidden이 Full 미상속 / PO 엔드포인트 전부 `response_model=None` / `_cost` 접미 컬럼이 로그 마스킹에 걸림 / `purchase_orders` 모듈의 f-string raise·log에 `cost` 이름 부재(AST) / 이벤트 발행 호출 payload 키 ⊂ 화이트리스트(AST) / SO·QT·PI·보드·인테이크 응답 모델 필드명 `cost|margin|purchase|landed` 부재(위반 픽스처 양방향 자기검사).
- J: 멱등 재생 응답에 VIEWER 접근 경로 없음(다른 actor 키 재사용 시 재생 안 됨).
- 변이 점검: VIEWER 스키마 분기 삭제, 정렬 화이트리스트에 원가 값 추가, audit에 원가 기재, 이벤트 payload에 `total_cost` 추가 — 각각 실패.

### (g) 소비·등재
마스킹 원장(PROGRESS) 신규 행 "S3-1 PO 원가 9채널"(자율 확정 표기). 후속: PO 첨부(채널 8), S3-4 원가·마진 조회, S6-2. ADR-F1에 "혼합 목적 행 판정·idempotency at-rest 수용" 기재.

### (h) 되돌리기 비용 — **중간**
필드 부재→행 단위로 바꾸면 API 계약(200→404)이 프런트·테스트 전반에 퍼진다. Full 노출 확대는 스키마 통합만으로 되나 원가 유출은 회수 불가 → 안전한 쪽을 지금 채택.

---

## F6. PO 후반 소유 공백 해소 — WBS v1.5 배정 + ETA 슬롯 이월

### (a) 목적·경계
입고 문서·PO 잔량 차감·PO 후반 전이·customs_records의 소유 세션이 WBS에 없다(r1 C12, B의 D-B1). 문서 배정 안건이며 S3-1은 코드를 만들지 않는다.

### (b) DESIGN 근거
§7.1(입고 라인은 수입 선적 라인을 참조 생성, PO 잔량은 입고 확정 시 차감, IN_PO 원장 ref는 입고 문서), §3 표 맵(customs_records ∈ M4), §17.1(원장 기록과 한 트랜잭션), WBS S3-2·S4-1 산출물, ADR-0036(⑭ 인증 보드 배정 — WBS 문서 갱신으로 배정한 선례).

### (c) 데이터 변경
없음. **WBS v1.5 변경 이력**(PR-1 문서 커밋):
- **S4-1**: 입고 문서(goods receipt: 헤더·라인)·PO 잔량 차감(B 잔량 계약의 `LINE_CONSUMERS['PO_LINE']` 소비자 등록)·PO 후반 전이(PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED 엣지 추가, RESERVED 감소)·IN_PO 원장 ref = 입고 문서. 이유: 입고 확정이 IN_PO 원장 이동과 **한 트랜잭션**이어야 하므로 원장 세션이 최초 소비자.
- **S3-2**: 수입 선적의 PO 참조(`shipment_lines.po_line_id` FK — `LINE_CONSUMERS['PO_LINE']`에 수입선적 소비자 등록)·`customs_records`(통관, 수입 세금 납부기한 마일스톤과 동석)·PO 라인 ETA(수입 선적 ETA에서 파생 또는 컬럼 가산 — 아래).
- **ETA**: PO 라인 예정일 컬럼은 **S3-1에 만들지 않는다**(§0.1 표). §11 백오더 "예상 가용일이 PO 입고예정에 추종"의 소비는 WBS상 P4 연결이다. 소비 세션(S3-2 수입 선적 ETA 또는 P4 백오더)이 (a) 선적 ETA에서 파생하거나 (b) `purchase_order_lines.expected_receipt_on DATE NULL` 가산 + B의 FREE 목록 확장(+전용 엔드포인트·이력)을 ADR로 판정한다.
- 문서 갱신: DESIGN §7.1에 소유 세션 주석은 넣지 않는다(WBS 소관). PROGRESS 부채 등재.

### (d) 4금
해당 없음.

### (e) 상태·전이·불변
PO RESERVED 3값(B)은 엣지 0 유지·`machine.RESERVED['PO']`에 소유 세션 주석(S4-1) 병기.

### (f) 테스트 배분
- 문서 린트 1건(K): WBS 본문에 "입고 문서"·"customs_records" 소유 문구가 S4-1/S3-2 절에 존재.
- 코드 테스트는 B의 RESERVED in/out 0 단언으로 대체.

### (g) 소비·등재
부채 신규 3건: ① "PO 후반 소유 = S4-1(트리거: S4-1 계획 보고에서 입고 문서 스키마 판정)" ② "customs_records·수입선적 PO 참조 = S3-2(트리거: S3-2 계획 보고)" ③ "PO 라인 ETA 슬롯 미도입(트리거: S3-2 계획 또는 P4 백오더 계획 중 먼저)". 각 세션 DoD에 1줄씩 추가 요구.

### (h) 되돌리기 비용 — **매우 낮음**(문서 한 줄 수정)

---

## F7. OEM 마일스톤 프로파일 슬롯·facilities 재판정 종결

### (a) 목적·경계
PROGRESS의 두 재판정 트리거("OEM 생산 발주 마일스톤 — S3-1/S3-2 계획 보고", "FACILITY→partners 잠정 매핑 — S3-1 OEM 계획 보고")를 소진·이월한다.

### (b) DESIGN 근거
§4.4, §5.1(FACILITY 적용단위), ADR-0037, S2-2 판정 조건 D, ADR-0041(죽은 문 금지).

### (c) 데이터 변경
**없음.** `po_kind`(F3)가 슬롯의 전부다. 마일스톤 프로파일 FK·컬럼·`facility_id`는 만들지 않는다. facilities 마스터 **신설 안 함**(유지): PO는 `supplier_partner_id`만 가진다 — 발주 대상이 파트너 단위로 성립하고 복수 시설 인스턴스 실수요가 미관측이다.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
문서 린트(선택): PROGRESS 해당 두 항목에 "S3-2" 문구 포함(재이월 확인).

### (g) 소비·등재
PROGRESS 문면 교체안(원문 그대로 사용):
- "[S1-2] §4.4 OEM 생산 발주 마일스톤 프로파일" → **"[S3-1 계획 판정 2026-09-30 자율 확정] 구분 슬롯(`purchase_orders.po_kind`) 완결·유형 검증에서 소비. 프로파일 적용 = S3-2(재판정 트리거: S3-2 계획 보고 — `purchase_orders.profile_id` nullable 가산은 S3-2 몫)."**
- "FACILITY→partners 잠정 매핑 한계" → **"[S3-1 계획 판정 2026-09-30 자율 확정] S3-1 PO는 시설을 참조하지 않는다(partner_id+po_kind) — facilities 신설 수요 없음, 잠정 매핑 유지. 재판정 트리거(먼저 도달): (a) S3-2 OEM 마일스톤 프로파일 계획 보고에서 시설 단위 일정이 필요하다고 판정될 때 (b) 한 거래처의 두 번째 시설 인스턴스 등록 시도 (c) 시설 인증 인스턴스 대량 등록 착수 전 — 그 이후는 재매핑 비용>0."**
ADR-F1에 1줄 부기(ADR-0037 관련).

### (h) 되돌리기 비용 — **낮음**
facilities 도입은 신설+`purchase_orders.facility_id` nullable 가산+certifications 재매핑(반입 전 0). 지금 결정이 후속 재입력을 유발하지 않는다.

---

## F8. 역할 매트릭스 — PO 주체 = TRADE, 기계 정본(`AUTHZ_MATRIX`)

### (a) 목적·경계
S3-1 전 엔드포인트의 역할 권한을 한 표로 고정하고 테스트가 그 표를 강제한다. 경계: 승인 결정 세부는 C4, 게이트·인테이크·보드 세부는 D7이 정본이고 F는 표에 옮겨 **일치를 검증**한다.

### (b) DESIGN 근거
§2(역할 5종 — 조회는 원가·마진 마스킹, 문서보관소=무역+인증, 시장·요건=인증, 승인 워크플로우), §18.1(모든 엔드포인트 역할+소유권), §20 H·K, ROLE_SEED(`TRADE`="견적·수주·선적·채권" — PO 주체가 문면에 없음), 선례: §2에 "판정 부기"로 역할 배정을 명문화(문서보관소·시장).

### (c) 데이터 변경
없음. **ROLE_SEED·역할 5종 불변**(추가·변경은 마이그레이션 사안, §2 5종 고정) — 설명 문구 갱신도 마이그레이션 시드 변경이라 하지 않고 DESIGN §2 표가 정본임을 명기.

**매트릭스(정본)** — R 조회, W 작성·수정, C 확정·발행·전이, X 취소. ADMIN은 라우터 `require_roles`에서 자동 통과하되 **업무 게이트(승인·게이트)는 통과가 아니다**(C4·D):

| 대상 | ADMIN | TRADE | LOGISTICS | CERT | VIEWER |
|---|---|---|---|---|---|
| QT·PI·SO 조회(목록·상세·CSV) | R | R | R | R | R |
| QT·PI·SO 생성·편집·발행/확정·전이·취소·FREE 메타 | W C X | W C X | 403 | 403 | 403 |
| **PO 조회** | R(Full) | R(Full) | R(Full) | R(Full) | **R(CostHidden, 200)** |
| PO 생성·preview·전이(OC·취소)·FREE 메타 | W C X | W C X | 403 | 403 | 403 |
| 승인함·상세·결정·회수·`inbox-count`·`delegation-candidates` | C4 표 그대로(VIEWER 403, 결정=서비스 자격 판정) | | | | |
| 대결 등록·해지·조회 | C5 그대로(위임자 본인+ADMIN) | | | | |
| 결재선(approval-lines) 조회/편집 | R·편집 | R | R | R | 403 |
| 인테이크 등록·수정·거부·확정·resolve·담당 변경 | 허용 | 허용 | 403 | 403 | 403 |
| 인테이크 목록·상세·게이트 조회 | R | R | R | R | R (D7: 전 역할 — 원본 파일 미보관·원가 없음) |
| 게이트 정책 PUT | 허용 | 403 | 403 | 403 | 403 |
| 오더 보드 조회·CSV·저장 필터(본인 것만) | 전 역할 | | | | |
| 오더 보드 벌크(CONFIRM_INTAKE·CONFIRM_SO·ASSIGN) | 허용 | 허용 | 403 | 403 | 403 |
| 바이어 품번 매핑 등록·삭제(F12) | 허용 | 허용 | 403 | 403 | 403 |
| `GET /users/lookup`(F9) | 허용 | 허용 | 403 | 403 | 403 |

- **PO 주체 = TRADE 판정**: 발주·소싱은 무역 실무이고 LOGISTICS는 입고·검수, CERT는 인증이라 발주를 그쪽에 둘 근거가 더 약하다. DESIGN §2 문단에 판정 부기 1문장 + ADR-F2.
- 인테이크 조회를 전 역할로 둔 것은 D7 그대로(제안 P2의 "LOGISTICS·CERT·VIEWER 인테이크 조회 403"은 기각 — D는 인테이크·게이트 응답에 원가·마진을 어느 역할에도 싣지 않고 원본 파일을 보관하지 않으며 여신 수치만 TRADE·ADMIN 응답에 둔다. 매트릭스는 D7과 일치시킨다).
- **서비스 자격 판정 라우트**(승인 결정·대결·delegation-candidates 등)는 라우트 게이트가 "허용 역할의 상한"이고 세부 자격은 C의 `decision_authority`가 판정한다 — 매트릭스 표에 `SERVICE_GATED` 표식.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
- K(신규 `tests/architecture/authz_matrix.py` + `test_authz_matrix.py`): ① **완비성** — OpenAPI의 S3-1 신규 모듈 경로(`quotations`·`proforma-invoices`·`sales-orders`·`purchase-orders`·`approvals`·`approval-lines`·`delegations`·`order-intakes`·`gate-*`·`order-board`·`users/lookup`)의 (메서드, 경로) 전건이 `EXPECTED` 표에 있고 표의 stale 행이 없음(공회전 방지 단언 포함) ② **프로브** — 각 (메서드, 경로) × 5역할 실제 요청(빈/무효 본문): 표에서 금지 역할은 **정확히 403**, 허용 역할은 401·403이 **아님**(404·409·422는 정상 — FastAPI는 의존성을 본문 검증보다 먼저 해소하므로 403이 먼저 나온다는 `test_auth_coverage`의 전제와 동일) ③ 무세션 401은 기존 `test_auth_coverage`가 신규 라우트를 자동 포괄함을 PR-1에서 확인.
- G/K: PO VIEWER 200+CostHidden(F5), CERT·LOGISTICS PO 200+Full.
- 변이 점검: 라우터 `require_roles` 한 곳 삭제, 표의 한 행 허용 역할 확장 — 각각 프로브가 실패.

### (g) 소비·등재
DESIGN §2 문단에 표+판정 부기, ADR-F2. 후속 세션은 신규 라우터 추가 시 `EXPECTED` 행을 같이 추가(없으면 완비성 실패).

### (h) 되돌리기 비용 — **낮음~중간**
매트릭스는 상수+테스트 파라미터. 완화(403→200)는 무해, 강화는 프런트 영향. PO 주체를 신규 역할로 옮기는 것은 역할 마이그레이션이라 중간.

---

## F9. 소유권 정의(IDOR)·확인 순서·표시명·사용자 디렉터리

### (a) 목적·경계
§18.1 "역할+소유권 검증(IDOR 차단)"·§20 K "타 사용자 전표 URL 403"의 소유권을 정의한다.

### (b) DESIGN 근거
§18.1, §20 K, §2(담당 이관: 퇴사·휴가·담당 변경 시 일괄 재배정), §7.4(오더 보드 전체 열람), B8·D7의 확정 전제. **DESIGN은 소유권을 정의하지 않는다 → 침묵 메움(가장 좁은 안전한 결정)**.

### (c) 데이터 변경
없음. 정의 — 소유권은 3축이다:
1. **역할 스코프**: 표(F8)의 역할 밖 접근 = 403.
2. **부모-자식 소속**: 경로의 자식 id가 부모 소속이 아니면 **404**(예 `/purchase-orders/{a}/…/{b의 line}`), 수정 부작용 0. 공통 헬퍼 `get_child_or_404`.
3. **당사자성**(당사자 스코프 자원 한정): 승인·위임 상세는 C4(비당사자 403 — 승인 존재는 팀 공개 정보라 403), 저장 필터·본인 설정은 D6(타인 id **404** — 개인 자원은 존재 오라클을 만들지 않음). 두 규칙의 구분은 "존재가 공개 정보인가"다.
- **전표(QT·PI·SO·PO)는 회사 공유 자산**: TRADE 사용자는 다른 TRADE 사용자가 만든 전표를 조회·수정·전이할 수 있다. `assignee_id`는 라우팅·알림·이관 단위이지 접근 제어가 아니다(B8·D7·D6 벌크 ASSIGN과 정합). 근거: 소수 인원 공유 업무·휴가 대행·오더 보드 전체 열람(§7.4)과 양립하는 유일한 해석이며, 작성자·담당자 한정 쓰기는 이관 도구·벌크·B의 FREE 편집과 모순한다. **§20 K "타 사용자 전표 URL 403"은 "역할 밖 사용자의 전표 URL 접근 403"으로 해석한다** — DESIGN 침묵 메움이므로 DESIGN §18.1·§20 K에 해석 보강 문장+ADR-F2.
- **번복 여지를 싸게 만드는 장치(seam)**: 모든 전표 쓰기 서비스는 첫 줄에서 `trade_docs.authz.assert_may_write(actor, doc)`를 호출한다(현재 구현 = 쓰기 역할 재검증: `actor.has_any_role(TRADE)`+ADMIN — D7 "require_roles+서비스 재검증"과 동일 지점). 소유권을 담당자 한정으로 좁히려면 이 함수 1개만 바꾼다.
- **확인 순서 고정**: 401(무세션) → 403(역할) → 404(부재·부모 불일치) → 409(상태·version) → 422(검증). 역할 검사는 존재 검사보다 앞(존재 오라클 방지).
- **담당자 유효성**: `assignee_id`는 활성 사용자이고 TRADE 또는 ADMIN 역할 보유(전표를 편집할 수 있어야 담당이 의미 있음), 아니면 422. 기본값=생성 actor. 대상: 4전표 생성·인테이크 등록·벌크 ASSIGN·B의 FREE 메타 변경. (기존 handover 일괄 이관 도구는 역할을 검증하지 않는다 — 기존 동작 유지, 관찰 등재.)
- **표시명**: 응답에 `assignee_name`·`requested_by_name` 등 **표시명 문자열만** 서버 조인(이메일·로그인ID·역할 미노출, 비활성·삭제 사용자도 이름 유지) — `certifications.assignee_name` 선례.
- **사용자 디렉터리(D의 "F 확인" 회신)**: `GET /api/v1/users/lookup?q=&page&size`(기본 50, `list_user_lookup`) — 응답 `{id, display_name}` 두 키뿐, 활성·비삭제 계정, **역할 ADMIN·TRADE**(담당자 선택기), 기본 `assignee_target=true` 필터로 TRADE·ADMIN 보유자만. C의 `GET /approvals/delegation-candidates`는 C 소관 그대로(비조회 4역할·본인 제외) — 두 엔드포인트는 **하나의 조회 함수(`identity.service.list_active_user_names`)와 하나의 응답 스키마(`UserLookupItem`)를 공유**한다. 기존 `/users`(관리)는 ADMIN 전용 유지.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
- K(IDOR): (a) LOGISTICS·CERT·VIEWER가 쓰기 엔드포인트 403 (b) 타 PO의 line_id를 다른 PO 경로에 넣으면 404·부작용 0(4전표 + 인테이크 라인) (c) **다른 TRADE 사용자의 전표 수정·전이 200**(공유 모델 고정 — 회귀 방지) (d) 존재하지 않는 id와 역할 밖 id의 응답이 역할 밖에서 동일 403 (e) 승인·위임 비당사자 403·저장 필터 타인 404(자원별 규칙 대조표 테스트) (f) `assert_may_write`를 부르지 않는 쓰기 서비스가 없음(AST — 전표 서비스 공개 쓰기 함수 첫 문장 검사).
- K: `users/lookup` 응답 키 집합 == {id, display_name}·이메일 문자열 0회·VIEWER/LOGISTICS/CERT 403·비활성·삭제 계정 미노출·`assignee_target` 필터 정확성·`size>100` 422.
- A: 비활성·역할 없는 사용자를 담당자로 지정 422.
- 변이 점검: `assert_may_write` 호출 삭제, 부모-자식 소속 검사 삭제, lookup에 email 추가 — 각각 실패.

### (g) 소비·등재
D(벌크 ASSIGN 검증 규칙·`users/lookup` 소비), C(`delegation-candidates` 공유 함수), 프런트(담당자 선택기 = F16 컴포넌트). **설계 보강 필요: DESIGN §18.1·§20 K 소유권 해석 문장** + ADR-F2. 오너 사후 번복 시 `assert_may_write` 한 함수 + 응답 코드 403 `…NOT_ASSIGNEE` 신설.

### (h) 되돌리기 비용 — **낮음~중간**
좁히는 방향(담당자 한정)은 함수 1개+에러코드+프런트 문구지만 이관·벌크·B의 FREE와 동반 재설계가 필요하다. 넓히는 방향은 쉽다 → 공유 모델이 안전한 시작점.

---

## F10. 담당자 컬럼·handover 등록·사용자 FK 분류 테스트

### (a) 목적·경계
§2 "담당 이관"(전표) 소비와 이관·이력·위임 사용자 FK의 조용한 누락 차단.

### (b) DESIGN 근거
§2 담당 이관·계정 비활성 절차, ADR-0012·0015(담당 vs 이력 구분), PROGRESS "Phase 3 인계: 전표에 담당자 컬럼을 추가하면 targets.py에 한 줄 추가(아키텍처 테스트가 강제)", 코드 `handover/targets.py ASSIGNMENT_COLUMN_NAMES`·`test_assignment_coverage`.

### (c) 데이터 변경
- 4전표 헤더 `assignee_id BIGINT NOT NULL FK users RESTRICT`(A: 컬럼·인덱스 소유). `handover/targets.py ASSIGNMENT_TARGETS`에 4행: `AssignmentTarget("quotations", Quotation, Quotation.assignee_id)`·`("proforma_invoices",…)`·`("sales_orders",…)`·`("purchase_orders",…)` — **label=테이블명**(`test_every_assignment_column_is_a_handover_target` 대조 규약). D의 `order_intakes` 1행은 D 소유(총 5행 추가).
- `ASSIGNMENT_COLUMN_NAMES`(이름 4종)는 **확장하지 않는다**: 이력(`requested_by_id`·`decided_by_id`·`consumed_by_id`·`decided_on_behalf_of_id`·`revoked_by_id`)을 스캔에 넣으면 이관이 과거 결정 기록을 바꾸는 오탐이 생긴다(ADR-0015).
- **신규 아키텍처 테스트 `test_user_fk_classification`**(런타임 변경 없음): `Base.metadata`의 `users.id`를 가리키는 **모든 FK 컬럼**(ActorMixin의 `created_by_id`·`updated_by_id`는 이름으로 AUDIT 자동 분류)을 `handover/targets.py::USER_FK_CLASSIFICATION`(`"테이블.컬럼" → (분류, 사유)`)에 강제:
  - `ASSIGNMENT`: `ASSIGNMENT_TARGETS` 등록 필수(교차 단언).
  - `HISTORY`: 행위자 이력 — 이관·변경 금지(`audit_log.actor_user_id`, `approvals.requested_by_id`·`decided_by_id` 등, 상태 이력 `actor_user_id`).
  - `AUTH`: 계정 종속(`user_roles.user_id`·`user_sessions.user_id`·`idempotency_keys.actor_user_id`).
  - `PERSONAL`: 개인 설정(`board_saved_filters.user_id`).
  - `DELEGATION`: `approval_delegations.delegator_user_id`·`delegate_user_id` — 계정 비활성 시 처리 규칙은 C의 유효성 판정(사용 시점 활성 확인)이 소유, 분류 사유에 그 위치를 명기.
  - 분류 없는 신규 users FK가 있으면 CI 실패. 기존 테이블(현 10개 비-믹스인 users FK — `user_roles`·`user_sessions`·`audit_log`·`tasks`·`alerts`·`alert_rules` 계열·`certifications`·`import_staging`·`idempotency_keys`)은 PR-1에서 **1회 소급 분류**한다(범위가 작고 이 테스트의 전제).
  - 양방향 자기검사: 임시 모델(분류 없는 users FK) 주입 시 실패, 분류만 있고 컬럼이 사라진 stale 항목 실패, ASSIGNMENT 분류인데 targets 미등록이면 실패.

### (d) 4금
없음.

### (e) 상태·전이·불변
이관 도구는 이력 컬럼(`created_by_id` 등)을 건드리지 않는다(기존 테스트).

### (f) 테스트 배분
- K: 위 분류 테스트 + `test_assignment_coverage`가 신규 4모델을 자동 감지·등록 강제.
- H(§20 H "담당 일괄 이관 후 라우팅 즉시 반영"): 이관 후 4전표 `assignee_id`가 신담당으로 이동·이관 후 신규 알림 수신자가 신담당·이관이 approvals 이력 컬럼 불변·이관 후 전표 `version` 불변(D 명시: handover 일괄 UPDATE는 version을 올리지 않음).
- 변이 점검: 분류 사전에서 항목 삭제, ASSIGNMENT 분류에서 targets 교차 단언 제거.

### (g) 소비·등재
소비: C(위임 컬럼)·D(`board_saved_filters.user_id`는 PERSONAL). 알림 수신자 해석: `assignee_id`→(비활성이면) ADMIN 폴백(`notify()` 기존 규칙).

### (h) 되돌리기 비용 — **낮음**(테스트 상수·targets 한 줄). `assignee_id` 컬럼명은 감지 이름과 묶여 사실상 고정.

---

## F11. 거래처 유형 해제 갭 종결 — 참조 마스터 409 + 게이트 재검증 + 잠금 (+partners 컬럼 경계)

### (a) 목적·경계
PROGRESS "거래처 유형 해제가 참조 마스터를 확인하지 않음"(트리거 S3-1)·s1-5-plan "구현은 S3-1 이월 확정"의 종결. 경계: 유형 검증 호출자(전표 게이트)는 A·B·D·F3.

### (b) DESIGN 근거
§7.7 "차단 지점은 등록이 아니라 게이트", §4.6(다중 유형), §12.2 ③(확정은 원자적 — 어긋나면 전체 409), §17.2, 코드 `PartnersImportTarget.apply_changes`(유형 해제=soft delete)·`verify_references` 훅.

### (c) 데이터 변경
스키마 없음(서비스·임포트 훅만).
**정합 판정(§7.7)**: §7.7은 *미완성 값을 등록 단계에서 막지 말라*는 사상이다. 이미 **성립한 의존 관계**(자재의 기본공급사·SKU의 제조사)를 조용히 깨는 변경을 막는 것은 사상과 충돌하지 않는다. 반대로 **전표 참조 409**(활성 전표가 있으면 유형 해제 금지)는 기각한다 — 종결·역사 전표(PAID PI, 완료 SO 등)가 유형 정리를 영구 봉쇄하는 "열린 상태" 정의 위험이 있고, 진행 중 전표는 아래 게이트가 fail-closed·fail-visible로 잡는다.
결정:
1. **마스터 축 409**: `partners.service.find_type_release_blockers(session, partner_id, released_type_codes) -> list[Blocker]` — SUPPLIER 해제 시 `materials.default_supplier_partner_id`가 이 거래처인 활성(`deleted_at IS NULL`) 자재 수, OEM 해제 시 `skus.manufacturer_partner_id`가 이 거래처인 활성 SKU 수(삭제되지 않은 전부 — DISCONTINUED 포함). 호출 위치: `PartnersImportTarget.verify_references`(현재 `return []` — 훅 시그니처에 행의 `target_id`를 추가로 넘긴다; 기존 두 타깃은 무시) → 문제 행을 `"{행번호}행(유형 해제 불가 — SUPPLIER: 기본공급사로 쓰는 자재 N건 …)"` 문자열로 모아 기존 `IMPORTS.CONFIRM.VERSION_CONFLICT` 전체 409(§12.2 ③, 부분 반영 없음). **신규 에러코드 없음.** 해제 대상 판정은 (현재 활성 유형 집합 − 페이로드 유형 집합).
2. **전표 축 = 게이트 재검증**: 전표 확정성 진입(QT 발행·PI 생성·SO 확정·PO 생성·인테이크 확정·선적 계획 후속)은 상대방 유형을 **그 시점**에 재검증(fail-closed — 유형 없음 422, 평가 불능[soft delete 거래처·조회 실패]도 통과가 아님). 이미 발행·종결된 전표는 유형 해제로 무효화되지 않는다(역사). 유형이 해제된 거래처의 전표는 다음 진행 시 422로 막히고 메시지가 재부여 경로를 안내(fail-visible). A의 `partner_has_open_documents`는 F가 쓰지 않는다 → **통합 검토가 삭제하거나(권장, ADR-0041 죽은 코드) 소비처(거래처 상세 화면)를 지정**.
3. **잠금(TOCTOU)**: 유형 해제 경로는 거래처 행 `FOR UPDATE`, 유형을 소비하는 쓰기(전표 게이트·자재·SKU 참조 검증 `_live_supplier`·`_live_manufacturer`)는 거래처 행 `FOR SHARE`(`require_partner_of_type(…, lock=True)`). B의 잠금 순서(거래처 최상위)와 일치. 임포트 확정이 거래처 행을 이미 `FOR UPDATE`로 잡는지는 구현 시 확인·미잠금이면 추가.
4. **수정 화면은 만들지 않는다**: 거래처 유형 편집 경로 = 왕복 임포트 단일(확정 우회 경로 증가 방지). 향후 편집 API가 생기면 `find_type_release_blockers`를 반드시 호출 — 아키텍처 테스트가 `PartnerTypeLink.deleted_at` 대입 지점을 허용 목록 {`imports/registry.py PartnersImportTarget.apply_changes`}로 고정(새 작성자는 목록 갱신을 강제당함).
5. **partners 컬럼 경계**: S3-1이 partners에 추가하는 컬럼은 A6·A8의 `name_en`·`address_en`(nullable, 데이터 전용)뿐. F의 요구 두 가지: ① 그 컬럼은 왕복 임포트 **한 헤더 상수**(§12.2 ②)·diff에 편입(열 끝 추가·열 부재 허용으로 기존 CSV 호환) ② 전표는 확정 시점에 값 복사 스냅샷을 가진다(A8 — 마스터 변경이 확정 전표를 바꾸지 않음). 전표 응답의 거래처 표시는 스냅샷 컬럼(`buyer_name`·`supplier_name`)을 읽는다.

### (d) 4금
없음.

### (e) 상태·전이·불변
확정 전표는 유형 해제와 무관하게 불변.

### (f) 테스트 배분
- A/J: SUPPLIER 해제(자재 참조 있음) → 임포트 확정 전체 409·문제 행 문구에 건수·부분 반영 0 / 참조 없으면 성공 / OEM 해제(SKU 제조사 참조) 동일 / 참조 자재 soft delete 후 재시도 성공 / 유형 재부여(신규 링크 행) 후 전표 진행 통과.
- A: 유형 해제된 거래처로 QT 발행·PI 생성·SO 확정·PO 생성 → 422(재부여 안내 문구) / 이미 발행된 PO·SO 상세·조회는 정상.
- J(동시성 실스레드): 유형 해제 임포트 확정 × 그 거래처를 기본공급사로 지정하는 자재 등록 동시 → 어느 쪽이든 "SUPPLIER 없는 거래처가 활성 자재의 기본공급사" 상태 불가 / 유형 해제 × PO 생성 동시 → "유형 없는 PO 생성 성공" 불가.
- K: `PartnerTypeLink.deleted_at` 대입 지점 허용 목록 스캔(양방향 자기검사) / partners 라우터에 PATCH·PUT 부재 / 왕복 CSV 신규 컬럼 헤더 상수 편입.
- 변이 점검: `verify_references`의 blocker 조회 삭제, 참조 술어의 `deleted_at IS NULL` 삭제, 게이트 재검증 삭제, `FOR SHARE` 제거.

### (g) 소비·등재
PROGRESS 항목 종결 부기(자율 확정), ADR-F3(또는 ADR-F1 부기): 판정 논거(§7.7 정합)·전표 축 미차단 사유·A 함수 처분. 후속(S3-2 선적·S4 할당)은 자기 확정 시점에 `require_partner_of_any_type(lock=True)`를 호출(각 세션 DoD 1줄).

### (h) 되돌리기 비용 — **낮음**
차단 축소·강화 모두 서비스 함수 1개 수정. 스키마 변경 없음. 전표 축 409를 추가하려면 `find_type_release_blockers`에 프로브 추가.

---

## F12. 바이어 품번 매핑 삭제 경로 신설(수정 API는 만들지 않음)

### (a) 목적·경계
`partners/service.py`의 유일키 위반 문구가 "기존 매핑을 정리해 주세요"라고 안내하나 정리 경로가 없다(등록·목록만 존재 — 라우터 실측). 인테이크의 "미매핑 시 등록 유도(§7.4)"가 잘못 등록된 매핑에 영구히 막히는 교착을 막는다.

### (b) DESIGN 근거
§7.4(품번 매핑 게이트), §17.4(활성 부분 유니크 — 삭제 후 재유입=신규 행), 코드 `CustomerItemCode`(SoftDeleteMixin·VersionMixin), `table_policy` soft delete UPDATE 허용.

### (c) 데이터 변경
스키마 없음. `DELETE /api/v1/partners/{partner_id}/item-codes/{item_code_id}` → 204, `dependencies=[require_roles(*CAN_REGISTER)]`(TRADE+ADMIN). 저장소 관용(문서·계약·체크리스트의 DELETE 라우트)대로 **본문·version 파라미터 없음**, 행을 `FOR UPDATE`로 잡고 soft delete(`deleted_at`·`updated_by_id`), 이미 삭제됐으면 404, 다른 거래처의 `item_code_id`를 넣으면 404(부모-자식 소속 검사), audit 기록·outbox 이벤트(id만). **수정(PATCH) 미제공**(정정=삭제 후 재등록 — 활성 부분 유니크라 재사용 허용).
- 기확정 전표는 영향 없음: 라인이 `buyer_item_code`·`sku_id` 스냅샷을 저장한다(A).
- 인테이크 게이트가 쓰는 일괄 해석 함수(`resolve_buyer_items` — D)는 이 삭제와 **같은 활성 필터(`deleted_at IS NULL`)**를 쓴다. 삭제된 매핑에 의존하는 대기 중 인테이크는 D의 확정 시 재해석(`LINE.STALE_MAPPING`)이 잡는다.

### (d) 4금
없음.

### (e) 상태·전이·불변
soft delete 1방향. 복원 액션은 만들지 않는다(재등록으로 대체).

### (f) 테스트 배분
- A/K: 삭제 후 같은 (거래처, 품번) 재등록 성공(신규 행) / 삭제 후 인테이크 게이트가 미매핑 판정 / 삭제 후 확정 대기 인테이크의 확정이 `STALE_MAPPING` / 타 거래처 id 404 / 이미 삭제 404 / LOGISTICS·CERT·VIEWER 403 / 기확정 SO 라인 `sku_id`·`buyer_item_code` 불변 / audit 1행.
- J: 삭제 × 인테이크 확정 동시 → 확정이 삭제 전 매핑으로 성공하는 경우 그 결과가 스냅샷으로 정합(삭제는 후속 전표에 무영향) 또는 STALE 409 — 두 결과 모두 "삭제된 매핑으로 확정" 불가는 아님을 명시적으로 단언하지 않고 **확정 후 SO 라인이 결정 시점의 (품번,SKU)를 유지**만 단언.

### (g) 소비·등재
D(등록 유도 UX가 삭제 안내를 포함), 프런트(거래처 상세 품번 목록에 삭제 버튼 — TRADE·ADMIN).

### (h) 되돌리기 비용 — **낮음**(엔드포인트 1개 제거는 안전)

---

## F13. 전표 참조 폴리모픽 코드 단일 출처 · documents 무변경

### (a) 목적·경계
r1: "expenses·documents 소유·tasks.entity_type가 공유하는 참조 전표 코드 상수 단일 출처는 S3-1이 완결". A는 `trade_docs.DOC_PREFIXES`(QT/PI/SO/PO), C·D는 저장값으로 `SALES_ORDER`를 쓴다 — **어휘가 둘**이므로 통일이 필요하다.

### (b) DESIGN 근거
§10.1(expenses "참조 전표"), §4.7(documents 폴리모픽 소유), §7.4(서류 태스크 체인), ADR-05, ADR-0041, 코드 관용: `documents.owner_type`·`certifications.target_type`·`approvals.target_type`은 **대문자 풀네임**, `tasks/alerts/audit.entity_type`은 **테이블명**.

### (c) 데이터 변경
- 단일 출처 `trade_docs/constants.py`: `class DocKind(StrEnum)` = `QUOTATION, PROFORMA_INVOICE, SALES_ORDER, PURCHASE_ORDER`(**DB에 저장되는 폴리모픽 전표 참조값의 유일한 어휘** — approvals.target_type·gate 계열 subject_type·후속 expenses 참조·후속 documents.owner_type), `DOC_PREFIXES: dict[DocKind, str]`(QT/PI/SO/PO — 채번 접두어), `DOC_TABLES: dict[DocKind, str]`(테이블명). 접두어 문자열은 **채번·표시에만** 쓰고 DB 폴리모픽 컬럼에는 저장하지 않는다. B의 내부 딕셔너리 키('QT' 등)는 메모리 상수라 유지 가능(변환은 `DOC_PREFIXES` 역조회) — 통합 검토에서 B가 `DocKind`로 키를 통일하면 더 좋다.
- `tasks.entity_type`·`alerts.entity_type`·`audit_log.entity_type`은 **기존 관용(테이블명)** 유지: 알림·감사는 `DOC_TABLES[kind]` 값을 쓴다(`entity_type='approvals'`(C)도 같은 관용). `tasks.entity_type`에 CHECK를 추가하지 않는다(기존 폴리모픽 값 공존).
- **documents는 S3-1에서 변경하지 않는다**(owner_type 열거·폭 모두). D가 원본 파일 미보관을 확정했고 QT/PI/SO/PO 첨부 소비자가 없다(ADR-0041). **폭 함정 사전 등재**(과거 owner_type 폭 정찰 누락 재발 방지): 후속 소비 세션이 `owner_type`을 확장할 때 `String(13)`→여유 폭(예 30) 확폭을 **같은 CHECK 재정의 마이그레이션에서** 해야 한다 — 값 길이: `QUOTATION` 9·`ORDER_INTAKE` 12·`SALES_ORDER` 11·`PURCHASE_ORDER` 14·`PROFORMA_INVOICE` 16. PO 첨부는 F5 채널 8(원가 가시성) 적용.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
- K: `DocKind` 값 ↔ `DOC_PREFIXES` 키 ↔ `DOC_TABLES` 키 1:1 / `DOC_TABLES` 값이 실제 모델 `__tablename__`에 존재 / `DOC_PREFIXES` 값이 채번 시퀀스 접두어(B4)와 일치 / S3-1 모듈에 `'SALES_ORDER'` 등 전표 유형 리터럴 직접 기재 금지(AST — 예외: `DocKind` 정의 파일·CHECK 정의) / C·D의 `target_type`·`subject_type` CHECK 값 ⊂ `DocKind` 값(pg_get_constraintdef 대조).
- 변이 점검: `DOC_TABLES` 값 오타, CHECK에서 값 삭제.

### (g) 소비·등재
소비: C(`target_type`)·D(`subject_type`)·후속 expenses(S3-4)·documents 소비 세션. 부채 등재: "documents 전표 첨부 미지원 + 확폭 경고(트리거: S3-3 QT/PI 파일 첨부 소비 또는 S6-1 AI 원본 보관)". C·D 문서의 `target_type`/`subject_type` 값이 이미 풀네임(`SALES_ORDER`)이라 충돌 없음 — A의 "DOC_PREFIXES를 폴리모픽 코드로" 문구만 이 안건으로 교정.

### (h) 되돌리기 비용 — **낮음**
상수 파일 1개. 값 변경 시 소비자 테스트가 잡는다.

---

## F14. runbook 소급 양식 갱신 소유 = S3-1

### (a) 목적·경계
DESIGN §21 부기: 소급 입력 대응표에 "전표 계열은 도래 시 갱신" — S3-1이 갱신 의무(A의 (g)에서도 확인).

### (b) DESIGN 근거
§21 소급 입력 runbook(증빙일=실제 발생일·입력일=복구일), §3 날짜 3종, `docs/runbook/forms/manual-record-form.md`·`incident-sop.md`(실재 확인).

### (c) 데이터 변경
문서만. 전표 생성 API가 서는 마지막 관통 PR의 문서 커밋에서: `manual-record-form.md` 소급 입력 대응표에 QT·PI·SO·PO 4행 추가(수기 임시번호·증빙일(`doc_date`)·입력일(`created_at` 자동)·상대방·품목·수량·단가·비고), `incident-sop.md`에 전표 소급 순서(QT→PI→SO, PO 독립)와 검산 1회 항목. API 계약(A10): `doc_date`는 사용자 입력·**미래 422·과거 하한 없음**·입력일은 서버 시각(사용자 입력 불가 — 스키마에 필드 부재+forbid). 수기 임시번호는 `note`에 병기(공식 번호는 신규 채번).

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
- 문서 린트(K) 1건: 양식 파일에 QT·PI·SO·PO 4접두 존재. A: 과거 `doc_date`로 생성 시 `created_at`은 실제 시각·`doc_date`는 입력값 유지·미래 422·`created_at`을 요청 본문에 넣으면 422.

### (g) 소비·등재
DoD 문서 항목. 부채 없음.

### (h) 되돌리기 비용 — **매우 낮음**

---

## F15. 목록 CSV 내보내기 범위 — QT·PI·SO·PO 4종 포함

### (a) 목적·경계
§12.2 "전 목록 UTF-8 BOM 내보내기"의 S3-1 포함 범위(PROGRESS: S2-1 요건 템플릿 CSV 누락 선례 — 계획서가 명시해야 관찰이 안 남음).

### (b) DESIGN 근거
§12.2, ADR-0027(수식 이스케이프)·`core/csv_export.render_csv/escape_formula_cell`, `EXPORT_MAX_ROWS=50,000`(조용한 잘림 금지·초과 422 조건 좁히기 선례), 마스킹(F5).

### (c) 데이터 변경
없음. 엔드포인트: `GET /quotations/export.csv`·`/proforma-invoices/export.csv`·`/sales-orders/export.csv`·`/purchase-orders/export.csv`(목록과 **동일 필터·권한**, 페이지네이션 우회 없이 서버 청크 스트리밍이되 총건수>50,000이면 422), 전 셀 `render_csv` 통로·UTF-8 BOM·쓰기 없는 GET이라 Idempotency-Key 불필요. 열 집합은 헤더 상수(핀 테스트). **PO CSV는 헤더 상수 2종**(원가 열 포함=ADMIN·TRADE·LOGISTICS·CERT / 미포함=VIEWER). 오더 보드 CSV는 D6(별도 열 집합 — 인테이크+SO 혼합 뷰이므로 SO 목록 CSV와 병존).
**포함하지 않는 것(부채 등재, 조용한 누락 아님)**: 승인함·대결·결재선 매핑·인테이크 스테이징 목록 CSV — 운영 큐·설정이지 "대장"이 아니다. 트리거: 운영 요청 또는 S6 대장 정비.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
K/B: 4종 각각 BOM·헤더 핀·수식 이스케이프(`=`,`+`,`-`,`@` 선두 셀)·금액 셀 숫자 정합·50건 초과 전건 출력·50,001건 422 / PO CSV 역할별 열 집합 고정(VIEWER 원가·통화 열 부재, F5 센티널 0회) / 권한 매트릭스에 export 행 포함 / N+1 없음(쿼리 수 상수). 변이 점검: 이스케이프 통로 우회, VIEWER 헤더에 원가 열 추가.

### (g) 소비·등재
부채 1건(위 제외 4목록). PROGRESS "S2-1 요건 템플릿 CSV" 계보에 부기.

### (h) 되돌리기 비용 — **낮음**(엔드포인트 추가는 가산)

---

## F16. 200건 드롭다운 상한 — 검색형 선택(D6 컴포넌트) S3-1 전 화면 적용

### (a) 목적·경계
PROGRESS의 `?size=200` 재발 트리거(S1.5·S2-2·S2-4 항목, §1 "SKU 수백") 도달. D6가 컴포넌트·서버 `q`를 정의했으므로 F는 **적용 범위·금지·소급 경계**를 확정한다.

### (b) DESIGN 근거
§1(규모), PROGRESS 드롭다운 4곳·인증 인스턴스 대상 등재 항목, D6.

### (c) 데이터 변경
없음(읽기 전용 서버 확장은 D 소유: `GET /partners?q=&type=`·`GET /skus?q=`·`GET /users/lookup?q=`). 서버 `q`는 LIKE 와일드카드(`%`·`_`·`\`)를 이스케이프한 부분 일치, 기본 `size` 50(Page 봉투 유지). F 확정: ① **QT·PI·SO·PO·인테이크·보드·승인/대결의 모든 거래처·SKU·담당자·수임자 선택은 이 컴포넌트 하나**로(화면별 자체 구현 금지) ② S3-1 신규 프런트 파일에서 `size=200`·`size: 200` 우회 금지(소스 스캔) ③ 결과가 `total`보다 적으면 "더 있음·검색어를 더 입력" 고지(TruncationNotice 계열, fail-visible) ④ **기존 화면(products·skus·certifications 대상·TasksPanel 문서 연결)은 소급하지 않는다** — 회귀 위험 대비 이득이 작고 PROGRESS 항목의 트리거(200건 초과 실측)를 유지한다.

### (d)~(e) 해당 없음

### (f) 테스트 배분
프런트 vitest: 300건 목록에서 검색어로 201번째 항목 선택 가능·디바운스·q 2자 미만 미검색·"더 있음" 표시·선택 후 필터 변경 시 stale 값 초기화·키보드 선택 / 소스 스캔(S3-1 신규 파일에 `size=200` 부재). 백엔드(D): `q`에 `%`·`_`·`\` 리터럴 매칭·`q` 미지정 기존 응답 불변·`type` 필터 정확성.

### (g) 소비·등재
PROGRESS 4개 항목에 "S3-1 신규 화면은 컴포넌트 적용 — 기존 화면 전환 트리거 유지" 부기.

### (h) 되돌리기 비용 — **낮음**

---

## F17. 쓰기 스키마 `extra=forbid` 전역 스캔 — **래칫**(기존 32개 동결·신규 전건 강제)

### (a) 목적·경계
PROGRESS "쓰기 스키마 forbid 스캔이 certifications 모듈 한정 — 다음 신규 모듈 추가 세션에서 app 전역으로 넓힐 것"의 트리거가 S3-1에서 발동(C 묶음도 V18로 확인). 소급 변환은 범위 밖.

### (b) DESIGN 근거
§17.4·ADR-0033(상태 밀반입 축 — status 필드 부재+forbid), §18.1 입력 검증, `test_certification_machine.py::test_every_write_schema_forbids_unknown_fields`(선례).

### (c) 데이터 변경
스키마 없음. `tests/architecture/test_write_schemas_forbid.py`(기존 certifications 스캔을 일반화·이전):
1. 대상: `app/modules/*/schemas.py`의 클래스 중 이름이 `Request`·`Create`·`Update`·`Patch`·`Payload`·`Body`·`Input`로 끝나는 pydantic 모델(현재 실측: `…Request` 45개, forbid 미설정 **32개** — `model_config['extra']` 런타임 판정).
2. 규칙: (R1) 동결 목록 `LEGACY_FORBID_EXEMPT` 밖의 클래스는 `extra='forbid'` 필수. (R2) 목록 항목이 삭제·개명됐거나 이미 forbid인데 목록에 남아 있으면 실패(**단조 감소 강제**). (R3) 공회전 방지: 스캔 대상 수 ≥ 45(감소 방지, 상한은 신규로 자연 증가).
3. 동결 목록(2026-09-30 AST 실측, 구현 시 런타임 재측정): `catalog.{BrandCreateRequest, ProductCreateRequest, SkuCreateRequest, SkuHsCodeCreateRequest, SetComponentCreateRequest, SkuPriceCreateRequest, ItemProfileCreateRequest}`, `documents.{LinkDocumentCreateRequest, ProfileDocumentTypeAddRequest}`, `handover.HandoverRequest`, `identity.{LoginRequest, AccountActiveRequest, RoleAssignmentRequest}`, `ingredients.{IngredientCreateRequest, IngredientRuleCreateRequest, ProductIngredientCreateRequest}`, `markets.{MarketCreateRequest, MarketUpdateRequest}`, `materials.{MaterialCreateRequest, BomLineCreateRequest, LabelCreateRequest}`, `partners.{PartnerCreateRequest, ItemCodeCreateRequest, SignatoryCreateRequest}`, `requirements.{RequirementTemplateCreateRequest, RequirementTemplateUpdateRequest, TemplateActionRequest, ChecklistItemAddRequest, ChecklistItemUpdateRequest, PrerequisiteAddRequest, ProfileRequirementTemplateAddRequest}`, `worklist.TaskCreateRequest`.
4. **S3-1 신규 스키마는 전건 forbid**(`model_config = ConfigDict(extra="forbid")` 명시 — 기저 클래스 도입 없이 저장소 관용 유지). 상태 밀반입 축(`status`·`doc_number`·`frozen_at`·`version` 이외 상태 필드 부재)은 B의 `test_doc_machines`가 계속 소유.
5. 소급은 "그 스키마를 수정하는 세션이 forbid로 바꾸고 동결 목록에서 지운다"(부채 등재). 공개 API 계약 변경(여분 필드 조용한 무시→422)이라 프런트 회귀 실측이 필요해 S3-1 범위 밖.

### (d) 4금
없음.

### (e) 상태·전이·불변
해당 없음.

### (f) 테스트 배분
K: 위 R1~R3 + 양방향 자기검사(임시 모듈 AST 픽스처 — forbid 없는 신규 클래스 주입 시 실패, stale 항목 실패) / B·C·D의 모듈별 forbid 스캔은 이 전역 스캔에 흡수(중복 제거는 통합 단계). 변이 점검: 동결 목록 비교 반전, R2 삭제.

### (g) 소비·등재
PROGRESS 해당 항목 종결 부기(래칫으로 전환) + 부채 신규 "기존 32개 소급(트리거: 해당 모듈 수정 세션)".

### (h) 되돌리기 비용 — **낮음**(테스트+상수. 소급은 스키마 단위로 점진).

---

## F18. ADR-0013/0014 예약 청소 잡 이행 — `idempotency-purge`·`session-purge`

### (a) 목적·경계
ADR-0014 "만료분 청소는 `scheduled_jobs`의 배치로 등록한다(실행기는 S2-3)"·ADR-0013 "전역 청소는 배치의 일"이 약속됐으나 JOB_REGISTRY 7행에 없다(PROGRESS 미등재 누락). S3-1이 확정·생성 API를 대량 추가하고 `idempotency_keys.response_body`에 PO 원가(Full 응답)가 24h 저장되므로 **이번에 이행**한다. 작은 별도 커밋(마지막 PR의 운영 정비 커밋).

### (b) DESIGN 근거
ADR-0013·0014, §15 L3(잡은 4금 밖), §2(멱등 키 TTL 24h), ADR-0044(스케줄러 계약), 코드 `idempotency/service.py KEY_TTL=24h·_purge_expired`, `identity/service._purge_expired_sessions`, `table_policy`(idempotency_keys "TTL 청소(DELETE)"·user_sessions MUTABLE).

### (c) 데이터 변경
스키마·마이그레이션 없음(잡 행은 실행기가 `JOB_REGISTRY`에서 동기화 — `scheduler.py` 실측).
- `JOB_REGISTRY` += `JobSpec(code='idempotency-purge', name_ko='멱등 키 만료분 청소', schedule='daily@04:20', run=_run_idempotency_purge)`, `JobSpec(code='session-purge', name_ko='로그인 세션 만료분 청소', schedule='daily@04:25', run=_run_session_purge)`. 시각: 일일 백업 03:00 KST(`infra/backup`)·일요일 04:00 복원 리허설과 겹치지 않게 그 뒤·기존 05:00 storage-monitor 앞.
- 삭제 규칙은 **기존 사용자별 청소와 동일 술어**: `idempotency_keys.expires_at <= now`, `user_sessions.expires_at <= now`(만료 기준 즉시 삭제 — 기존 로그인 시 청소와 같은 규칙, 별도 유예 기간을 새로 발명하지 않음). 1,000행 청크 반복 커밋(건별 독립 트랜잭션), 실행 결과에 삭제 건수 기록(`scheduled_job_runs` 결과 필드 관용 확인), 실패는 기존 실행기의 ADMIN 운영 알림(fail-visible).
- 함수: `idempotency/service.purge_expired_all(*, batch=1000) -> int`, `identity/service.purge_expired_sessions_all(*, batch=1000) -> int`(각 서비스가 자기 테이블 소유).
- **재생 창 명시**: 24h 이후 재시도는 키가 아니라 상태 검사(PO=이미 생성된 번호·SO=상태 409 등 B 전이 검사)가 이중 확정을 막는다(기존 계약 — 문서에 명기).

### (d) 4금
기술 청소(삭제) 잡 — 원장·전표·감사·발주·법적 판정·대외 발송과 무관. `test_registered_jobs_stay_clear_of_the_four_bans`의 코드 집합에 두 코드를 추가하며 사유를 주석으로 남긴다("기술 행 TTL 청소").

### (e) 상태·전이·불변
`audit_log`·전표·이력 테이블은 건드리지 않는다(잡이 임포트하는 모델 = IdempotencyKey·UserSession뿐, 스캔).

### (f) 테스트 배분
- J: TTL 만료 키만 삭제·미만료 보존 / 1,000행 초과 청크 반복 후 잔여 0 / 삭제 후 같은 키 재요청은 신규 처리 / 만료 세션만 삭제·유효·revoked 미만료 세션 보존(기존 로그인 청소와 동일 결과) / 실행 이력에 건수 / 잡 실패 시 운영 알림.
- K: `test_scheduler_registry` 등록 코드 집합·개수 갱신(B의 document-expiry-sweep·C의 approval-stagnation-scan과 합쳐 **11행** — 통합 단계에서 총수 정합) / 잡 모듈이 전표·원장·감사 모델을 import하지 않음 / 4금 테스트 집합 갱신.
- 변이 점검: 만료 술어를 `>`로 반전(미만료 삭제 → 실패), 청크 루프 1회로 축소.

### (g) 소비·등재
PROGRESS "ADR-0013·0014 예약 소비 종결(미등재 누락이었음 부기)". runbook 잡 표 갱신. 등록 총수(7→11)는 B·C 잡과 함께 통합 검토가 확정.

### (h) 되돌리기 비용 — **낮음**(레지스트리 2행 삭제·비활성). 삭제 대상이 TTL 만료분뿐이라 데이터 손실 위험 없음.

---

## F19. SKU 용량 컬럼 — S3-1 미도입, 품명 스냅샷으로 식별

### (a) 목적·경계
PROGRESS 관찰 "SKU 용량(volume) 컬럼 부재"의 재판정 트리거(S3-3·S5-1·§7.7 중 먼저) — S3-1은 트리거 미도달, 전표 라인이 이를 잘못 굳히지 않게 한다.

### (b) DESIGN 근거
§4.1(SKU 컬럼 목록에 용량 없음 — 추측 구현 금지), §7.6(서류 생성기 = S3-3), A의 라인 스냅샷(`sku_code`·`sku_name_ko`·`sku_name_en`).

### (c) 데이터 변경
없음. QT·PI·SO·PO 라인은 품목을 **`sku_code`·`sku_name_ko`·`sku_name_en`(마스터 값 복사)** 로 식별한다(A). 이름에서 용량을 파싱·추정하지 않는다. 후속 `skus.volume_*` 가산 시 확정 전표는 스냅샷 텍스트를 유지하므로 재입력·소급 변경이 없다. `sku_name_en`이 NULL인 SKU도 저장은 허용(서류 생성 시 fail-visible 경고는 S3-3).

### (d)~(e) 해당 없음

### (f) 테스트 배분
A(A 묶음 테스트에 이미 포함): 마스터 개명 후 발행된 전표 스냅샷 불변·`name_en` NULL 저장 성공.

### (g) 소비·등재
PROGRESS 관찰 항목에 "S3-1 = 전표 라인은 품명 문자열 스냅샷만(용량 구분의 유일 수단), 서류(S3-3) 렌더에서 재판정" 부기.

### (h) 되돌리기 비용 — **낮음**

---

## F20. 문서·ADR·부채 세트(설계 변경 = DESIGN 갱신 + ADR 5줄)

### (a) 목적·경계
CLAUDE.md "설계 변경은 DESIGN.md 갱신+`docs/adr/` ADR 5줄이 세트". F의 결정 중 DESIGN 침묵을 메우거나 문면을 보강하는 것을 문서로 고정한다. ADR 번호는 통합 검토가 배정(형제 묶음이 0051~을 사용 — 충돌 방지를 위해 여기서는 ADR-F1~F3로 지칭).

### (c) 문서 변경 목록 (전부 "자율 확정(2026-09-30)" 표기)
**ADR (각 5줄: 결정·근거·대안·되돌리기 비용·영향 세션)**
- **ADR-F1 PO 결정 묶음**: SKU 전용 라인(자재=P4 가산 경로)·PO 초안 없음(B와 동일 인용)·`po_kind`·단가 원천(MASTER/MANUAL·`_cost` 명명)·원가 마스킹=혼합 목적 행 필드 부재+9채널·idempotency at-rest 수용·후반 소유 배정(WBS v1.5)·ETA 슬롯 이월·facilities 유지·OEM 슬롯 종결.
- **ADR-F2 역할·소유권**: 역할 매트릭스(PO 주체=TRADE)·소유권=역할 스코프+부모-자식 소속+당사자성(전표=공유 자산, §20 K 해석)·확인 순서·담당자 유효성·`assert_may_write` seam·사용자 FK 분류 테스트·users/lookup.
- **ADR-F3 마스터 정합**: 유형 해제 갭 종결(마스터 참조 409·전표 게이트 재검증·잠금·§7.7 정합 논거)·품번 매핑 삭제 경로·전표 참조 코드 단일 출처(DocKind)·documents 무변경+확폭 경고·forbid 래칫·청소 잡 이행(ADR-0013·0014 부기)·CSV 범위.
**DESIGN 갱신(문면 보강)**: §2 권한·통제 문단(역할 매트릭스·PO 주체 판정 부기·소유권 정의) · §18.1(IDOR 정의 1문장)·§20 K(문면 해석 주) · §7.1/7.2(PO 라인=SKU·초안 없음은 B와 공동 문단) · §12.2 부기(전표 CSV 범위·PO CSV 역할별 열) · §3 표 맵(신규 컬럼 `po_kind` 반영). 설계 변경이 아니라 침묵 메움 — 다만 §20 K 해석은 **설계 보강 필요(DESIGN §18.1·§20 K)**.
**WBS v1.5 변경 이력**: S4-1(입고 문서·PO 잔량 차감·후반 전이·IN_PO ref)·S3-2(수입선적 PO 참조·customs_records)·PO 라인 ETA 슬롯 판정 소유. (WBS 침묵 메움.)
**PROGRESS**: 종결 부기 — 유형 해제 갭·OEM 슬롯·facilities 문면 교체(F7)·forbid 래칫 전환·ADR-0013/0014 소비·SKU 용량 부기·200건 드롭다운 부기·CSV 누락 판정. **신규 부채**: ① PO 후반 소유 → S4-1(트리거: S4-1 계획) ② customs_records·수입선적 PO 참조 → S3-2 ③ PO 라인 ETA 슬롯(트리거: S3-2/P4) ④ 자재 PO 미지원(트리거: S4-1 사급 ADR) ⑤ documents 전표 첨부·확폭(트리거: S3-3/S6-1) ⑥ 목록 CSV 미포함 4종 ⑦ 기존 forbid 32개 소급 ⑧ handover의 담당자 역할 미검증(기존 동작) ⑨ 마스킹 원장 신규 행(PO 9채널).
**runbook**: `manual-record-form.md`·`incident-sop.md`(F14), 운영 잡 표(F18).

### (d)~(e) 해당 없음

### (f) 테스트 배분
문서 린트(K): ADR 5줄 형식 검사(기존 테스트 있으면 통과)·WBS v1.5 변경 이력 행 존재·소급 양식 4접두·PROGRESS 교체 문면 존재.

### (g) 소비·등재
PR-1(계획 등재 커밋)에 DESIGN·WBS·ADR·PROGRESS 문서 커밋 포함, runbook은 관통 PR. 웹 세션 판정 절차 생략에 따라 각 항목 "자율 확정" 표기(ADR-0011 부기 2026-09-29).

### (h) 되돌리기 비용 — **매우 낮음**(문서)

---

## 자율 확정 판정표

| # | 결정 요지 | 근거 한 줄 | 되돌리기 |
|---|---|---|---|
| F1 | PO 라인 품목 = SKU 전용, 자재 PO는 P4 가산(`material_id`+num_nonnulls+NUMERIC 확폭) | materials에 단위·가격 컬럼 없음(P2 주장 틀림)·§8.2 EA 정수·사급 수율=P4 ADR·ADR-0041 | 낮음~중간 |
| F2 | PO 초안 없음·생성=발행=사람 1클릭(B 채택), 6값·전이 3, OC 일자 경계(발행일≤OC≤오늘), 라인 ETA 기각 | §7.2 "발행→"·§15 4금·B 확정·ETA 소비자는 P4 | 중간 |
| F3 | 공급사 유형: PURCHASE=SUPPLIER∨OEM·OEM_PRODUCTION=OEM, 생성 시 1회+거래처 FOR SHARE, `po_kind` 컬럼 | 다중 유형이라 구분 필요·S3-2가 나중에 붙이면 기발행 PO 재분류 | 낮음 |
| F4 | 단가: 미입력=price_at(PURCHASE, doc_date)·MASTER / 입력=MANUAL, 부재 시 NOT_EFFECTIVE 422, 컬럼 `unit_cost/line_cost/total_cost` | §4.1 fail-visible·`_cost` 접미가 로그 마스킹에 자동 편입(`amount`는 제외) | 낮음 |
| F5 | PO 원가 = 혼합 목적 행 → 필드 부재 스키마 2종+9채널 봉쇄(정렬·CSV·에러·로그·감사·이벤트·멱등·첨부·집계) | ADR-0024 판정 기준·§18.1 로그 마스킹·COST_VISIBLE_ROLES 단일 출처 | 중간 |
| F6 | PO 후반=S4-1·수입선적 PO 참조+customs_records=S3-2로 WBS v1.5 배정, ETA 슬롯은 소비 세션 가산 | IN_PO는 원장과 한 트랜잭션·WBS "가용일 추종은 P4" | 매우 낮음 |
| F7 | OEM 슬롯=`po_kind`로 종결·프로파일=S3-2, facilities 신설 안 함(트리거 재설정 문면 확정) | 시설 참조 수요 없음(추측 구현 금지) | 낮음 |
| F8 | 역할 매트릭스 확정(PO 주체=TRADE, 전표 조회 전 역할, PO VIEWER=CostHidden 200), 기계 정본 `AUTHZ_MATRIX`+프로브 테스트 | §2·COST_VISIBLE_ROLES·B/C/D와 정합 | 낮음~중간 |
| F9 | 소유권=역할 스코프+부모-자식 404+당사자성, 전표는 공유 자산(§20 K "역할 밖 403" 해석), `assert_may_write` seam, users/lookup(ADMIN·TRADE) | B8·D7·§7.4·이관 도구와 양립하는 유일한 해석 | 낮음~중간 |
| F10 | `assignee_id` 4전표+handover 4행(label=테이블명), 사용자 FK 전수 분류 테스트(이름 스캔 미확장) | 이력 컬럼 이관 오탐 방지·`board_saved_filters.user_id` 등 조용한 누락 차단 | 낮음 |
| F11 | 유형 해제: 참조 마스터(자재 기본공급사·SKU 제조사) 임포트 확정 409 + 전표 게이트 재검증 fail-closed + FOR UPDATE/SHARE, 전표 참조 409는 기각 | §7.7은 미완성 등록 사상·성립한 의존 파괴는 별개, 역사 전표 봉쇄 방지 | 낮음 |
| F12 | 품번 매핑 DELETE 신설(수정 없음, 저장소 DELETE 관용) | "정리해 주세요" 문구의 정리 경로 부재·인테이크 교착 방지 | 낮음 |
| F13 | 폴리모픽 전표 코드 `DocKind`(풀네임 4값) 단일 출처+접두어 표시 전용, tasks/alerts는 테이블명 관용 유지, documents 무변경(확폭 경고 등재) | C·D가 이미 `SALES_ORDER` 사용·저장소 관용·소비자 없는 owner_type 확장 금지 | 낮음 |
| F14 | runbook 소급 양식 QT·PI·SO·PO 4행 갱신 소유=S3-1 | §21 부기 의무·A10 doc_date 계약 | 매우 낮음 |
| F15 | 목록 CSV 4종 포함(PO는 역할별 열), 승인·인테이크·결재선 CSV는 부채 | §12.2·S2-1 누락 선례 | 낮음 |
| F16 | 검색형 선택 컴포넌트를 S3-1 전 신규 화면에 적용·`size=200` 금지, 기존 화면 소급 안 함 | 재발 트리거 도달·소급은 회귀 대비 이득 작음 | 낮음 |
| F17 | forbid 전역 래칫: 기존 32개 동결·신규 전건 강제·단조 감소 | 소급은 계약 변경+프런트 회귀 검증 필요 | 낮음 |
| F18 | 청소 잡 2종(`idempotency-purge` 04:20·`session-purge` 04:25) 이번에 이행 | ADR-0013·0014 약속·PO 원가 응답 at-rest 보존 최소화 | 낮음 |
| F19 | SKU 용량 컬럼 미도입, 라인은 품명 스냅샷만 | §4.1 침묵·트리거 미도달 | 낮음 |
| F20 | ADR-F1~F3+DESIGN §2·§18.1·§20 K·§12.2·§3 보강+WBS v1.5+PROGRESS 종결·부채 9건 | 설계 변경=DESIGN+ADR 세트(CLAUDE.md) | 매우 낮음 |

---

## 부록 A. 다른 묶음에 요구·확인할 항목(통합 검토용)

| 대상 | 요구 |
|---|---|
| A | (1) PO 헤더에 `po_kind VARCHAR(16) NOT NULL DEFAULT 'PURCHASE'`+CHECK(F3), `FROZEN_HEADER_COLUMNS`에 추가 (2) 자재 PO=P4 가산 확장 경로 문구 수용(A12 (c) 이미 일치) (3) `partner_has_open_documents`는 F가 소비하지 않음 — 삭제 또는 소비처 지정 (4) "DOC_PREFIXES를 폴리모픽 코드로" → `DocKind`(풀네임) 저장·접두어 표시 전용으로 교정(F13) (5) 라인 SKU 중복 금지 규칙(`TRADE.LINE.SKU_DUPLICATE`)이 PO에도 적용되는지 확인(PO 라인 분할 납기 미지원과 동일 취급 가정) (6) `require_partner_of_any_type(lock=True)` 호출 |
| B | (1) PO OC 부속 `oc_received_on` 경계(발행일≤OC≤오늘) 검증 목록 추가 (2) `RESERVED['PO']` 주석에 소유 세션(S4-1) 병기 (3) `DocKind`로 내부 키 통일 권장 (4) 이벤트 payload 화이트리스트 = F5 채널 6과 동일 확인 (5) FREE 4개 유지(F가 5번째 요구 안 함) |
| C | (1) 승인 결정·자격은 C4 그대로 채택(F8 매트릭스가 `SERVICE_GATED`로 참조) (2) `delegation-candidates`와 F9 `users/lookup`이 조회 함수·스키마 공유 (3) `approval_delegations` 사용자 FK 분류(DELEGATION) 사유 문구 제공 (4) PO 발행에는 승인 게이트 없음 확인 |
| D | (1) `users/lookup` 권한 = ADMIN·TRADE, 응답 2키, `assignee_target` 필터 — D6 확인 회신 (2) 벌크 ASSIGN·인테이크 담당 변경은 담당자 유효성(활성+TRADE/ADMIN 보유) 검증 추가 (3) 컴포넌트를 S3-1 전 화면(A·C 포함)에 적용 (4) 전표 참조 저장값 `subject_type='SALES_ORDER'` 유지(DocKind 값과 일치) |
| E | 여신 수치 마스킹 결론(E2)이 D의 "TRADE·ADMIN 응답에만"과 다르면 D7 상수 1곳 — F는 전표에 원가·마진 필드 불도입만 확정 |
| 통합 | JOB_REGISTRY 총수(B+1·C+1·F+2 = 11)·ADR 번호 배정(0051~ 충돌)·에러 코드 도메인 접두 정렬(PO 코드는 기존 코드 재사용 위주라 F 신규 에러코드 0건) |

## 부록 B. F의 신규 에러코드·엔드포인트·상수 총람

- 신규 에러코드: **0건**(유형 오류=`COMMON.VALIDATION.INVALID_FIELD`+필드 detail, 단가 부재=`CATALOG.PRICE.NOT_EFFECTIVE`, 유형 해제 차단=`IMPORTS.CONFIRM.VERSION_CONFLICT` 문제 행, 전이 오류=B의 `TRADE_DOCS.*`).
- 엔드포인트: `POST /purchase-orders`·`POST /purchase-orders/preview`·`GET /purchase-orders`·`GET /purchase-orders/{id}`·`POST /purchase-orders/{id}/transitions`·`PATCH /purchase-orders/{id}/meta`(B8 FREE)·`GET /*/export.csv` 4종·`DELETE /partners/{id}/item-codes/{item_code_id}`·`GET /users/lookup`. (QT·PI·SO 라우트는 A/B/D 소유.)
- 코드 상수: `PoKind`, `DocKind`/`DOC_PREFIXES`/`DOC_TABLES`, `USER_FK_CLASSIFICATION`, `LEGACY_FORBID_EXEMPT`, `AUTHZ_MATRIX`(테스트), PO CSV 헤더 2종, 잡 2종.
- 신규 서비스 함수: `partners.service.require_partner_of_any_type(…, lock)`, `partners.service.find_type_release_blockers`, `trade_docs.authz.assert_may_write`, `identity.service.list_active_user_names`, `idempotency.service.purge_expired_all`, `identity.service.purge_expired_sessions_all`, `purchase_orders.service.create_purchase_order/preview_purchase_order`.
- 신규 테이블: **0건**(A 신설 `purchase_orders`에 `po_kind` 1열 요구). 기존 테이블 변경: 0건.
- 신규 테스트 파일: `test_authz_matrix.py`(+`authz_matrix.py`), `test_user_fk_classification.py`, `test_write_schemas_forbid.py`, `test_po_cost_masking.py`, PO 4금 스캔(`test_no_auto_confirm_code_path_exists` 확장), 문서 린트, 프런트 vitest(선택 컴포넌트·`size=200` 스캔).
