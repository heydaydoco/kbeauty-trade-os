# S3-2 계획서 부록 A — 선적 데이터 모델 (shipments·lines·parties·customs_records·상태머신·사슬 등록)


> **통합 우선순위(2026-10-04)**: 이 부록과 `design-integrated.md`가 충돌하면 통합 문서가 이긴다. 통합 검토가 모순 해소에 필요한 최소 문면만 고쳤고, 고친 자리는 "[통합 X-nn]"·"[통합 N-nn]"으로 표시했다(목록: 통합 §1.6).
> **적대 검토 정정(2026-10-04)**: 통합 문서 §9(R-01~R-30)가 이 부록과 통합 §0~§8보다 우선한다. 이 부록에서 고친 자리는 "[적대 R-nn]"으로 표시했다(목록: 통합 §9 R-27).
- 성격: 설계 결정이다(구현 아님). 기준은 main `a4d91c0`이다. 근거 정본은 DESIGN.md §3·§7.1·§7.2·§7.5·§8.3·§15·§17·§18·§20·§22, WBS.md S3-2 행(`W:112-116`), PROGRESS.md 'S3-1 PR-16 / S3-1 종결'·'## 현재'다.
- 표기: `D:줄`=DESIGN.md, `W:줄`=WBS.md, `P:줄`=PROGRESS.md, `code:경로:줄`=`backend/app/` 기준 현행 코드다.
- 판정: 전 안건 **자율 확정**이다(오너 지시 2026-09-29 — 더 엄격한 fail-closed 권장안으로 확정, 사후 번복 가능, ADR-0011 부기). "미정" 결론은 두지 않는다.
- 안건 서식: 각 안건은 **결정 / 근거 / 대안(기각 사유) / 자율 확정 여부 / 되돌리기 비용**을 적는다.
- **실행 검증 못 했음.** 정적 독해만 했다(파일 열람·grep). 줄 번호는 `a4d91c0`에서 확인한 값이며, 각 PR 첫 커밋에서 실측 기록한다(P-60 선례).

---

## 0. 경계 — 이 부록이 정하는 것과 넘기는 것

| 이 부록(A)이 정한다 | 다른 부록으로 넘긴다(경계만 적음) |
|---|---|
| `shipments`·`shipment_lines`·`shipment_parties`·`shipment_status_log`·`customs_records`의 테이블·컬럼·CHECK·인덱스·FK·table_policy | **마일스톤·자동 계산·롤오버·통보 기록**: 마일스톤 10종(B/L일 포함 여부), 계획/실적 형태, 파생값(대금만기·적재의무·제시기한) 계산 함수, 롤오버 이력 표. 이 부록은 산식이 읽는 **입력 열**(선적 헤더의 결제조건 4열 사본, `customs_records.accepted_on`)만 제공한다 |
| 선적 상태 열거·전이표, SO `IN_SHIPMENT` 엣지, `COMPLETED` 보류 | **휴일**: `holidays` 표와 경고. 이 부록은 선적의 **출발국·도착국 열**(휴일 경고의 국가 축)만 둔다 |
| `CHILD_LINKS`·`LINE_CONSUMERS`·`CONSUMABLE_STATUSES`·`open_quantity` kind 필터 | **기일 알림·잡**: 선적 기일 스캔, `JOB_REGISTRY` 12→N, QT/PI D-N(P-03) |
| 부분선적 1:N 잔량 파생, 초과 거부, 수입선적 배정 가능량 | **§8.3 "자리만"**(AllocationPort 호출·표시)과 **화면**(선적 목록·상세·문서 흐름 노드·보드 열 UI·DG 배지) |
| 채번 접두어, 에러코드, 잠금 순서, 권한 행, 여신 노출 무접촉 | **OEM 프로파일**(P-06·P-57), **PO 라인 ETA**(P-05), **short-close**(P-02), PR-16 부채 ③⑦ |

겹치는 지점에서 이 부록이 내는 **계약 1줄**:
- 마일스톤 표는 `shipment_id` FK로 이 부록의 `shipments`를 가리킨다. 마일스톤 부록이 그 FK를 `NON_CHILD_FK_ALLOWLIST`에 사유와 함께 등재한다(§A6).
- 휴일 부록은 `shipments.origin_country_code`·`dest_country_code`(ISO alpha-2, markets FK 아님)를 읽는다(§A2).
- 선적 기일 알림 라우팅의 담당자 원천은 `shipments.assignee_id`다(§A11).

---

## A1. 선적을 전표 커널(`DocKind`)에 편입한다 — 접두어 `SH`

**결정**
- `DocKind.SHIPMENT = "SHIPMENT"`를 추가한다. `DOC_PREFIXES`에 `"SH"`를 넣고, `DOC_TABLES`·`LINE_TABLES`·`STATUS_LOG_TABLES`·`STATUS_LOG_FK`·`LINE_HEADER_FK`·`HEADER_TOTAL_COLUMN`·`LINE_AMOUNT_COLUMN`·`EVENT_PREFIX`·`PARTNER_COLUMN`·`FREEZE_COLUMN` 11개 dict에 행을 더한다(`code:modules/trade_docs/constants.py:12-93`).
  - 값: `shipments` / `shipment_lines` / `shipment_status_log` / `shipment_id` / `shipment_id` / `total_amount` / `line_amount` / `shipments.shipment` / `counterparty_partner_id` / `frozen_at`.
- 상태 대입은 `record_birth`/`record_transition` 단일 통로를 그대로 쓴다. 상태이력은 `status_log_checks` 믹스인을 쓰고 IMMUTABLE에 등재한다.
- **`approvals.target_type`·`gates.subject_type` CHECK는 넓히지 않는다.** 두 집합은 DocKind의 부분집합이면 되므로 확장 의무가 없다(`code:modules/trade_docs/constants.py:13`). S3-2는 선적 승인·게이트를 만들지 않는다.
- 수출·수입은 **한 kind**로 둔다. 구분은 `shipment_kind` 열이 맡는다(A2).
- 채번: `doc_number`는 `^SH-[0-9]{4}-[0-9]{4,}$` 형식 CHECK에 전역 UNIQUE다. 발급은 최초 저장 시점이고 TX의 마지막 단계이며 KST 연도를 쓴다(`D:348`). 수출·수입이 접두어를 공유한다. **통관 기록은 채번하지 않고** 외부 신고번호를 저장한다(A8).

**근거**
- §7.2는 선적 상태를 코드 고정 열거로 둔다(`D:181`, ADR-11 `D:35`).
- §17.5 "상태 변경 이력 성격의 테이블은 같은 지위로 신설 세션이 등재"(`D:356`).
- §17.3 채번 규약(`D:346`, `D:348`). ADR-0054 "채번은 `DocKind`/`DOC_PREFIXES` 멤버 추가만".
- 커널 밖에 별도 기계를 두면 단일 통로, 이력 IMMUTABLE, 총수 핀, 역순 취소, 아웃박스 계약을 다시 만들어야 한다. 그 재구현이 곧 우회 표면이다.

**대안(기각)**
- (a) 커널 밖 독립 상태 기계: 계약 재구현이 필요하고 `test_doc_status_channel`의 "status 대입은 record_transition만" 보호를 받지 못한다. 덜 엄격하다.
- (b) 수출(`SHIPMENT_EXPORT`)과 수입(`SHIPMENT_IMPORT`)을 별도 kind로: 상태 머신이 같은데 kind만 두 배가 되어 총수·dict 파급이 커진다.
- (c) 수입 접두어를 별도로(`SI` 등): 문면 근거가 없고, `SI`는 S3-3 Shipping Instruction 약어와 충돌한다.

**자율 확정 여부**: 자율 확정.

**되돌리기 비용: 높음.** DocKind 값은 DB에 저장되는 값이다(상태이력·아웃박스 aggregate). 편입 후 분리하려면 데이터 마이그레이션이 필요하다. 접두어 변경은 낮다(신규분부터 적용, 기존 번호는 재발급 금지라 유지).

**파급 테스트(갱신 의무)**
- `tests/architecture/test_doc_machines.py:46`(EXPECTED 키 = DocKind), `:169-171`(접두어 목록), `:174-191`(StrEnum↔machine↔DB CHECK 3자 대사)
- `test_doc_field_policy.py:42-62`, `test_doc_status_log_contract.py:46-75`, `test_doc_status_channel.py:367-378,496-536`
- `trade_docs/verify.py:43`(금액 열 규약 — A3에서 정함)
- `trade_chain/chain_ops.py:31-44`(`DOC_MODELS`·`ANCESTORS`)

---

## A2. `shipments` 헤더 — 구분 4종, 원천 FK 정확히 하나, 스냅샷 복사

**결정 — 컬럼**

`TradeHeaderMixin`을 재사용한다(`code:modules/trade_docs/mixins.py:51-96`). 여기서 doc_number, doc_date, status, currency, fx_rate, fx_rate_date, 결제조건 4열, Incoterms 3열, internal_note, last_line_no, assignee_id, copied_from_id가 온다. 그 위에 감사 컬럼, version, soft delete를 더하고 아래 열을 추가한다.

| 열 | 타입 | NULL | 분류(FIELD_POLICY) | 의미 |
|---|---|---|---|---|
| `shipment_kind` | VARCHAR(16) | NOT NULL | ORIGIN | `EXPORT`·`IMPORT`·`CHANNEL_INBOUND`·`SAMPLE_FREE` (§7.5 구분 4종) |
| `so_id` | BIGINT FK `sales_orders.id` RESTRICT | NULL | ORIGIN | 수출 원천 |
| `po_id` | BIGINT FK `purchase_orders.id` RESTRICT | NULL | ORIGIN | 수입 원천 |
| `counterparty_partner_id` | BIGINT FK `partners.id` RESTRICT | NOT NULL | ORIGIN | 수출=SO 바이어, 수입=PO 공급사(복사) |
| `counterparty_name` | VARCHAR(200) | NOT NULL | ORIGIN | 거래 상대명 스냅샷(SO `buyer_name` / PO 공급사명) |
| `origin_country_code` | CHAR(2) | NOT NULL | CONTENT | 출발국(휴일 부록이 소비) |
| `dest_country_code` | CHAR(2) | NOT NULL | CONTENT | 도착국(ETA 현지 휴일의 '현지') |
| `total_amount` | BIGINT | NOT NULL | CONTENT | 라인 `line_amount` 합(판매가 축, 수입은 0 — A3) |
| `frozen_at` | TIMESTAMPTZ | NULL | SYSTEM | 출고지시(RELEASE_ORDERED) 시각 = 동결 시각 |

**결정 — 기존 믹스인 열의 분류(선적 한정)**
- 통화, 환율 2열, 결제조건 4열, Incoterms 3열은 **ORIGIN**이다. 생성 시 원천에서 복사하고 이후 어느 상태에서도 불변이다. SO에서는 이 열들이 CONTENT(RECEIVED에서 편집 가능)였지만, 선적은 재입력 금지라 편집 구간 자체가 없다.
- `doc_date`는 ORIGIN(발급 KST 날짜)이다. **[적대 R-15]** 값 = 생성 시 `today_kst()` 1회 설정(원천 헤더에서 복사하지 않음), 이후 불변.
- `internal_note`·`assignee_id`는 FREE다. **`FREE_COLUMNS`는 정확히 4개 그대로**이고 확장 ADR이 없다.
- version, doc_number, status, frozen_at, last_line_no, 감사 컬럼은 SYSTEM이다.

**결정 — CHECK** (`create_table` 안에서 `op.f()` 이름으로 건다. 함정 ①·⑪)
- `header_common_checks(DocKind.SHIPMENT)` 전부: status_valid, doc_number_format, currency_uppercase, total_range, fx_rate_range, fx_pair, krw_fx_is_one, fx_date_not_future, copied_from_not_self, payment_type_valid, 결제·Incoterms 규약.
- `ck_shipments_kind_valid`: `shipment_kind IN ('EXPORT','IMPORT','CHANNEL_INBOUND','SAMPLE_FREE')`. 문면 4값을 싣는다.
- **`ck_shipments_kind_source`**: `(shipment_kind='EXPORT' AND so_id IS NOT NULL AND po_id IS NULL) OR (shipment_kind='IMPORT' AND po_id IS NOT NULL AND so_id IS NULL)`.
  - 결과적으로 **채널입고·샘플무상 행은 DB가 거부**한다. 값은 열거에 있되 생성 경로가 닫혀 있다.
  - 이 두 구분의 원천은 S4-3·S5-2 채널(채널입고)과 무상 라인 SO와의 구분 판정(샘플무상)이다. 여는 세션이 이 CHECK를 재정의한다.
- `ck_shipments_import_has_no_amount`: `shipment_kind <> 'IMPORT' OR total_amount = 0`. 원가 비복사를 보장한다(A3).
- `ck_shipments_country_format`: 두 국가 열 모두 `^[A-Z]{2}$`.
- 동결 정합 CHECK 2개. CANCELLED는 PLANNED에서 취소되면 frozen_at이 NULL, 출고지시 뒤 취소되면 NOT NULL이라 양방향 등식 하나로 쓸 수 없다.
  - `ck_shipments_planned_not_frozen`: `status <> 'PLANNED' OR frozen_at IS NULL`
  - `ck_shipments_released_frozen`: `status NOT IN ('RELEASE_ORDERED','PICKING','INSPECTED','RELEASED','SHIPPED','CLOSED') OR frozen_at IS NOT NULL`

**결정 — 인덱스**
- `uq_shipments_doc_number`: 전역 UNIQUE(`D:348`).
- `ix_shipments_so_id_live`: `(so_id) WHERE deleted_at IS NULL AND so_id IS NOT NULL`. CHILD_LINKS 후속 생존 판정과 SO 수렴 계수용이다.
- `ix_shipments_po_id_live`: 같은 형태로 `po_id`에 건다.
- `ix_shipments_list`: `(status, id DESC) WHERE deleted_at IS NULL`. 목록 페이지네이션 50용이다.
- `ix_shipments_assignee`: `(assignee_id) WHERE deleted_at IS NULL`. 담당 이관과 알림 라우팅용이다.

**결정 — 복사 규칙(참조 생성)**
- 생성 요청 본문에는 **SKU·단가·통화·환율·거래처·Incoterms·결제조건 필드가 없다**(`D:175` ①). 스키마는 `extra="forbid"`라서 이 필드를 보내면 422다.
- 본문에는 원천 id, 라인별 (원천 라인 id, 수량), 출발국·도착국, 선택적 당사자(A5), internal_note만 받는다.
- 환율 고정 = **원천 헤더의 `fx_rate`·`fx_rate_date` 스냅샷 복사**다. 선적 시점에 새 환율을 입력하는 경로는 없다. 변경은 취소+신규로만 한다.
- `assignee_id`는 원천(SO/PO)의 담당자를 복사하고, 이후 FREE다.

**근거**
- §7.5 "shipments(Incoterms+장소·연도, 환율 고정, 구분[수출/수입/채널입고/샘플·무상])"(`D:203`)
- §7.1 "후속은 선행에서 복사+참조"(`D:175` ①)
- ADR-0055:17 "환율 고정 = SO 스냅샷 복사, 선적이 Incoterms 3열 복사", ADR-0053:17 "선적은 SO 확정 후만 복사"
- 금액 규약 `D:26`, 스냅샷 `D:54`
- 국가 축: markets는 EU 같은 비국가 코드를 허용한다(`code:modules/markets/models.py:47-48`). 그래서 도착국을 SO `dest_market_code`로 근사할 수 없다.

**대안(기각)**
- (a) 원천 FK 1열 다형(`source_type`+`source_id`): FK 무결성을 잃는다. `ChildLink`는 헤더 FK 1열 equality만 지원한다(`code:modules/trade_docs/chain.py:27-37`).
- (b) 다중 SO 합적(N:M): 선적↔SO 링크 표가 필요하고 CHILD_LINKS로 표현할 수 없다. 지금 열고 나중에 조이는 것은 사실상 불가능하다. **부채 등재**(트리거: 합적 실수요).
- (c) 선적 시점 환율 신규 입력: 확정 후 환율 불변 계보(GC-A6, `W:109`)와 어긋나고 `fx_rates` 마스터도 없다(P-22).
- (d) 도착국을 `dest_market_code`에서 기본 복사: EU 등 비국가 시장에서 거짓 국가가 된다. **입력 필수**로 하고, 화면이 SO 시장이 ISO 국가일 때만 미리 채우는 것은 화면 부록 재량이다.

**자율 확정 여부**: 자율 확정. 단 **구분 4종 중 2종의 생성 경로를 닫는 것**은 §7.5 문면의 부분 이행이므로 부채로 등재한다.

**되돌리기 비용**
- 채널입고·샘플 개방은 **낮음**(CHECK 재정의, 가산).
- N:M 합적으로 바꾸는 것은 **높음**(헤더 FK를 링크 표로 이전하고 chain 판정을 재작성).
- ORIGIN 분류를 CONTENT로 푸는 것은 **낮음**(분류 1줄 변경과 편집 API 추가).

---

## A3. `shipment_lines` — 원천 라인 FK 정확히 하나, 단가는 수출만 복사

**결정 — 컬럼**

| 열 | 타입 | NULL | 분류 | 비고 |
|---|---|---|---|---|
| `shipment_id` | BIGINT FK `shipments.id` RESTRICT | NOT NULL | ORIGIN | |
| `line_no` | INTEGER | NOT NULL | SYSTEM | 헤더 `last_line_no` 카운터 |
| `so_line_id` | BIGINT FK `sales_order_lines.id` RESTRICT | NULL | ORIGIN | 수출 |
| `po_line_id` | BIGINT FK `purchase_order_lines.id` RESTRICT | NULL | ORIGIN | 수입(WBS `po_line_id`) |
| `sku_id`, `sku_code`, `sku_name_ko`, `sku_name_en`, `sku_kind` | (SO/PO 라인과 동형) | | ORIGIN | 원천 라인에서 복사 |
| `currency` | CHAR(3) | NOT NULL | ORIGIN | 헤더와 같아야 한다(서비스 검증 + 기존 라인 관용) |
| `quantity` | INTEGER | NOT NULL | CONTENT | 기본 단위 EA 정수(`D:225`) |
| `unit_price_amount` | BIGINT | NULL | ORIGIN | 수출=SO 라인 단가 사본, **수입=NULL** |
| `is_free` | BOOLEAN | NOT NULL | ORIGIN | SO 무상 라인 표식 사본(수입 false) |
| `line_amount` | BIGINT | NOT NULL | CONTENT | `quantity × unit_price_amount`(수입 0) |

- 감사 컬럼과 soft delete를 둔다. 라인 version은 두지 않는다. 라인 편집은 헤더 잠금→헤더 version 대조→헤더 version 상승으로 처리한다(`D:342`, `D:344` ④ 관용).
- 중량, CBM, 박스 수 열은 **두지 않는다**. S3-3 PL(G.W.≥N.W. 검증, `D:205`)이 착수할 때 가산한다.

**결정 — CHECK**
- `ck_shipment_lines_one_source`: `(so_line_id IS NULL) <> (po_line_id IS NULL)`
- `ck_shipment_lines_quantity_range`: `quantity BETWEEN 1 AND 99999999`(`MAX_QUANTITY`)
- `ck_shipment_lines_import_no_price`: `po_line_id IS NULL OR (unit_price_amount IS NULL AND line_amount = 0)`
- `ck_shipment_lines_export_priced`: `so_line_id IS NULL OR (unit_price_amount IS NOT NULL AND unit_price_amount >= 0 AND line_amount = quantity::bigint * unit_price_amount)`
- `ck_shipment_lines_amount_range`: `line_amount BETWEEN 0 AND 9007199254740991`
- `ck_shipment_lines_line_no_positive`: `line_no >= 1`

**결정 — 인덱스**
- `uq_shipment_lines_line_no_live`: `(shipment_id, line_no) WHERE deleted_at IS NULL`
- `uq_shipment_lines_so_line_live`: `(shipment_id, so_line_id) WHERE deleted_at IS NULL AND so_line_id IS NOT NULL`. 한 선적 안에서 같은 원천 라인은 1행이고, 선적 간에는 1:N이다.
- `uq_shipment_lines_po_line_live`: 같은 형태로 `po_line_id`에 건다.
- `ix_shipment_lines_so_line_id`, `ix_shipment_lines_po_line_id`: 잔량 SUM 소비 술어용이다.

**결정 — 소속 검증**
- 라인의 원천 라인이 헤더의 원천 전표에 속하는지(`so_line.so_id = shipments.so_id`)는 **서비스가 잠근 라인 집합과 대조**한다. 어긋나면 422 `SHIPMENTS.SOURCE.LINE_MISMATCH`다.
- `lock_lines_for_consumption`이 라인의 헤더 소속을 걸러 주는지는 PR 첫 커밋에서 실측한다. 걸러 주지 않으면 서비스에서 대조한다.

**결정 — 금액 축과 검산**
- 선적 `total_amount`/`line_amount`는 **판매가 축**이다. 마스킹 대상이 아니다(여신·판매 합계 선례 `D:116` ③).
- **수입선적은 PO 원가를 복사하지 않는다**(ADR-0024 9채널 봉쇄 `D:39` ③). 그래서 단가는 NULL, 금액은 0, 헤더 합계도 0이다. 이 0은 "금액 축 없음"이라는 뜻이며 CHECK로 고정한다.
- `trade-docs-totals-verify`(`code:modules/trade_docs/verify.py:43`)는 DocKind 루프로 선적을 자동 편입한다. 수입은 0=0으로 통과하고, 수출은 실제로 검산된다.

**근거**: "선적(부분 1:N)"(`D:173`), §7.1 보강 ①②(`D:175`), 원가 봉쇄(`D:39` ③), WBS "수입선적의 PO 참조(`po_line_id`)"(`W:114`).

**대안(기각)**
- (a) 수입 라인에 원가 복사: 9채널 봉쇄를 위반하고 LOGISTICS·VIEWER에게 원가가 노출될 표면이 생긴다.
- (b) 선적을 검산 루프에서 제외(`verify` 분기): "모든 DocKind 검산" 계약이 약해진다.
- (c) 원천 라인 소속을 복합 FK(`(so_line_id, so_id)` → `sales_order_lines(id, so_id)`)로 DB 강제: 더 엄격하지만 `sales_order_lines`·`purchase_order_lines`에 UNIQUE(id, 헤더FK) 추가 마이그레이션과 라인에 헤더 FK 비정규화가 필요하다. 서비스 대조와 K 테스트로 충분하다고 판단했다. **관찰 등재**(재판정 트리거: 소속 불일치 사고 1건).

**자율 확정 여부**: 자율 확정.

**되돌리기 비용**
- 중량·CBM 가산은 **낮음**.
- 수입 금액 축을 나중에 여는 것(예: 수입 신고가)은 **중간**(CHECK 재정의, 마스킹 설계 동반).

---

## A4. 잔량 — 부분선적 1:N 파생, 초과 409, 수입선적 "배정 가능량"

**결정 — 수출(SO_LINE 소비, FULFILL)**
- `LINE_CONSUMERS["SO_LINE"]`에 다음을 등록한다. SO 잔량 = SO 라인 수량 − 살아 있는 선적 라인 수량 합이다. 살아 있음 = 선적 헤더가 미삭제이고 `DEAD_STATUSES`가 아니며, 라인도 미삭제다.

  ```
  ConsumerSpec(name="SHIPMENT_LINE.so_line_id", child_line_table="shipment_lines", line_fk_col="so_line_id",
               qty_col="quantity", child_header_table="shipments", child_header_fk="shipment_id")  # kind=FULFILL
  ```

- 초과는 `require_within_open`이 409 `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`으로 처리한다(기존 코드 재사용, 카탈로그 문구 "참조 생성·선적·입고 공용", `code:core/errors/codes.py:148`).
- **`CONSUMABLE_STATUSES[SALES_ORDER]`에 `IN_SHIPMENT`를 추가**해 `{CONFIRMED, IN_SHIPMENT}`로 만든다. 추가하지 않으면 두 번째 부분선적이 `DOCUMENT_NOT_CONSUMABLE`로 막힌다. `ON_HOLD`는 넣지 않는다(보류 중 선적 생성 금지).
- 선적 취소 시 잔량은 **파생이므로 자동 복원**된다(저장 잔량 없음). 선적 라인 수량 감소, 라인 삭제(PLANNED 한정)도 같다.

**결정 — 수입(PO_LINE 소비, IN_TRANSIT)**
- `LINE_CONSUMERS["PO_LINE"]`에 `kind="IN_TRANSIT"` 소비자 `SHIPMENT_LINE.po_line_id`를 등록한다.
- **`open_quantity`에 kind 필터를 추가**한다. 시그니처는 `open_quantity(session, line_kind, line_ids, *, kinds=frozenset({"FULFILL"}))`이다.
  - 기본값에서는 FULFILL만 합산하므로 **PO 잔량은 수입선적으로 줄지 않는다**(§7.1 "PO 잔량은 입고 확정 시 차감", `D:173`).
  - 현재 루프는 kind를 무시한다(`code:modules/trade_docs/quantities.py:133`). 이 필터가 없으면 IN_TRANSIT 등록만으로 PO 잔량이 줄어드는 결함이 생긴다.
- 수입선적 초과 방지 기준은 **배정 가능량 = PO 라인 수량 − 살아 있는 IN_TRANSIT 합**이다. `open_quantity(..., kinds={"IN_TRANSIT"})`로 같은 함수에서 파생한다(시그니처 1개 유지, `D:229` ②). 초과는 409 `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE`이다. PO 잔량과 의미가 달라 같은 코드를 쓰면 오독하므로 코드를 분리한다.
- `CONSUMABLE_STATUSES[PURCHASE_ORDER]`는 `{ISSUED, SUPPLIER_CONFIRMED}` 그대로다.
- S4-1과의 경계: 입고 라인은 수입선적 라인을 참조 생성한다(`D:173`). 입고의 FULFILL 소비와 IN_TRANSIT은 **겹쳐 세지 않는다**는 계약을 ADR에 승계 항목으로 적는다.

**결정 — 잠금 순서(수량 직렬화)**
- 생성과 수량 증가는 다음 순서다: 멱등 claim → (당사자 검증 시) partners `FOR KEY SHARE`(id 순) → **원천 헤더 `FOR UPDATE`**(`lock_chain`) → `lock_lines_for_consumption`(헤더 `FOR SHARE`, 이미 같은 TX가 더 강한 잠금을 쥐어 즉시 통과 → 상태 검증 → 라인 `FOR UPDATE ORDER BY id`) → `open_quantity` 검사 → INSERT → `doc_number_seq`(마지막).
- 원천 헤더를 처음부터 `FOR UPDATE`로 잡는 이유는 같은 TX에서 SO 상태 수렴(CONFIRMED↔IN_SHIPMENT, A7)이 일어나기 때문이다. `FOR SHARE`를 쥔 두 TX가 `UPDATE`로 승격하려 하면 40P01 교착이 난다(SHARE→UPDATE 승격 금지).
  - 시그니처는 바뀌지 않으므로 §8.3 부기 ②의 문면("상위 헤더 FOR SHARE")은 **최소 요건으로 충족**된다. DESIGN §8.3 부기에 "S3-2는 상태 수렴 동반이라 원천 헤더 FOR UPDATE를 선행"을 1줄 추가한다.
- ~~수입은 PO 상태를 바꾸지 않지만, 같은 헬퍼 경로를 쓰도록 PO도 `FOR UPDATE`로 통일한다~~ **[통합 X-10]** 수입은 PO 상태를 바꾸지 않으므로 PO 헤더는 `FOR SHARE`(`D:229` ② 문면 그대로 — 승격 없음, PO 취소 `FOR UPDATE`와 직렬화).

**근거**: `D:173`, `D:175` ②, `D:229` ②, P-04, P-07(`P:562`, `P:565`), WBS DoD "부분선적 1:N 잔량 정확"·검증 A "잔량 0·초과 거부"(`W:115-116`, `D:419`).

**대안(기각)**
- (a) 수입선적을 FULFILL로 등록: PO 잔량이 선적 시점에 줄어 §7.1 문면과 정면으로 충돌한다.
- (b) 수입선적 초과 방지 없음: PO 수량을 넘는 선적이 생겨 S4-1 입고 참조가 오염된다.
- (c) 별도 함수 `assignable_quantity`: §8.3 부기 "시그니처 하나"와 어긋난다.
- (d) 원천 헤더 `FOR SHARE` 유지 + 수렴을 별도 TX로: 업무 동작 1개 = TX 1개(`D:340`) 위반이다.

**자율 확정 여부**: 자율 확정.

**되돌리기 비용**
- kind 필터와 IN_TRANSIT은 **중간**이다. S4-1이 소비 의미를 재정의하면 함께 재판정해야 한다.
- 잠금 강도를 낮추는 것은 **낮음**(헬퍼 1곳).

**파급 테스트**
- `test_pi_contract.py:28-48`(등록 3건, SO/PO 빈 튜플, 전부 FULFILL)는 등록 5건과 kind 허용 집합 `{FULFILL, IN_TRANSIT}`으로 갱신한다.
- `test_po_contract.py:239-251`, `test_trade_docs_kernel.py:75-203`(kind 필터 후 기대값 재확인).

---

## A5. `shipment_parties` — 역할 폐쇄 열거, 원천 당사자 자동 스냅샷

**결정 — 컬럼**: `shipment_id` FK RESTRICT NOT NULL, `role` VARCHAR(16) NOT NULL, `partner_id` FK `partners.id` RESTRICT NOT NULL, `name_en` VARCHAR(200) NOT NULL, `address_en` VARCHAR(500) NULL, 감사 컬럼, soft delete.

**결정 — 제약**
- `ck_shipment_parties_role_valid`: `role IN ('SHIPPER','CONSIGNEE','NOTIFY','FORWARDER','CUSTOMS_BROKER')`
- 제어문자 CHECK(`[[:cntrl:]]`)를 `name_en`·`address_en`에 건다.
- `uq_shipment_parties_role_live`: `(shipment_id, role) WHERE deleted_at IS NULL`. 위반은 제약명을 409 `SHIPMENTS.PARTY.ROLE_DUPLICATE`로 번역한다(500 금지, `D:352`).

**결정 — 규칙**
- **생성 시 자동 행**(재입력 금지): 수출은 `CONSIGNEE` = SO 바이어, 수입은 `SHIPPER` = PO 공급사다. 거래처의 `name_en`·`address_en`을 스냅샷한다. 원천을 수정해도 소급하지 않는다(`D:54`).
- 거래처 `name_en`이 비어 있으면 **422 `SHIPMENTS.PARTY.ENGLISH_NAME_MISSING`**이다. 서류 영문 원천(`D:116` ②)의 결측을 선적 시점에 드러낸다(fail-visible). 거래처 화면에서 보완하라는 조치 문구를 쓴다.
- 역할-거래처 유형 대응: `FORWARDER` 역할은 거래처 유형 FORWARDER, `CUSTOMS_BROKER`는 CUSTOMS_BROKER다. 검증은 `require_partner_of_any_type(lock=True)`(KEY SHARE, ADR-0067)이며 기존 코드를 재사용한다. NOTIFY는 유형 무관이다.
- **수출의 SHIPPER 행은 거부**한다(422 `SHIPMENTS.PARTY.ROLE_NOT_ALLOWED`). 수출 송하인은 자사이며 자사는 partners에 없다. CI 렌더링(S3-3)이 회사 정보에서 가져온다. 수입의 CONSIGNEE도 자사라 같은 규칙을 적용한다.
- 편집 구간: 자동 행(CONSIGNEE/SHIPPER)은 **불변**이다. NOTIFY·FORWARDER·CUSTOMS_BROKER는 선적이 살아 있는 동안(S3-2 활성 상태 PLANNED·RELEASE_ORDERED) 추가·교체·삭제할 수 있다. 헤더 version 대조와 audit_log를 남긴다. "TO ORDER" 같은 비거래처 수하인은 L/C 영역(S3-3)이라 열지 않는다.

**근거**: "shipments(+lines/parties)"(`D:70`), 거래처 유형 10종 중 포워더·관세사·3PL(`D:102`, `D:112` ①, `code:modules/partners/models.py:45-54`), 스냅샷 규율(`D:54`), 잠금 모드(`D:344` ②).

**대안(기각)**
- (a) 헤더에 `forwarder_partner_id` 등 고정 열: 역할을 늘릴 때마다 열이 늘고 FIELD_POLICY 분류도 같이 늘어난다.
- (b) partner_id NULL 허용(자유 텍스트 당사자): 거래처 마스터를 우회한다.
- (c) 3PL 역할을 지금 추가: 소비처(S4 창고)가 없는 죽은 열거다.

**자율 확정 여부**: 자율 확정.

**되돌리기 비용: 낮음.** 역할 확장은 CHECK 재정의다. 영문명 필수를 푸는 것은 서비스 1곳이다.

---

## A6. 사슬 등록 — `CHILD_LINKS`·`NON_CHILD_FK_ALLOWLIST`·역순 취소

**결정 — CHILD_LINKS 2건 추가**

```
ChildLink(DocKind.SALES_ORDER, "shipments", "so_id")
ChildLink(DocKind.PURCHASE_ORDER, "shipments", "po_id")
```

- 효과: 살아 있는 선적이 있는 SO·PO는 취소 시 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`다(`record_transition`이 엣지 검사보다 먼저 판정, `code:modules/trade_docs/transition.py:139-146`, SO 취소 `code:modules/trade_chain/lifecycle.py:355-359`). **[적대 R-02]** 단 현 SO 취소는 `SO_CANCELLABLE` 상태 검사(`:351-354`)가 먼저라 IN_SHIPMENT SO는 `TRANSITION.NOT_ALLOWED`가 난다 — PR-3a에서 후속 생존 검사를 상태 검사 앞으로 옮겨 `SUCCESSOR_ALIVE`+`detail.successors`를 보장한다.
- nullable FK여도 equality 술어라 반대편 구분의 행은 잡히지 않는다.
- 선적 **종결(CLOSED)은 LIVE**로 남아 이행된 사슬의 선행 취소를 계속 막는다. 의도된 동작이다(`DEAD_STATUSES`는 CANCELLED·EXPIRED만).
- 자식 테이블 필수 열 `{fk_column, deleted_at, status, doc_number}`를 shipments가 충족한다(`tests/architecture/test_doc_chain_contract.py:85-94`).
- `ANCESTORS[SHIPMENT] = ((SALES_ORDER, "so_id"), (PURCHASE_ORDER, "po_id"))`. `lock_chain`은 None 조상을 건너뛴다(`code:modules/trade_chain/chain_ops.py:58-63`). LOCK_ORDER상 SO→PO 순서와도 일치한다. **[적대 R-08]** `lock_chain`은 조상을 `FOR UPDATE`로 잡는다 — 선적 기점 경로(T4·T5)는 이를 그대로 쓰고(SO·PO 모두 UPDATE), PO `FOR SHARE`는 수입 생성(T2)에만 적용한다.

**결정 — 허용목록(사유 10자 이상)**
- `("shipment_lines","shipment_id")`: 라인은 자기 헤더의 구성 요소다.
- `("shipment_status_log","shipment_id")`: 상태이력은 사건 기록이다.
- `("shipment_parties","shipment_id")`: 당사자 스냅샷은 헤더의 구성 요소다.
- `("customs_records","shipment_id")`: 통관 기록은 사실 부속이다. 전표가 아니며 선적 취소 가드는 A8에서 별도로 둔다.
- `shipment_lines.so_line_id`·`po_line_id`: LINE_CONSUMERS 등록분이라 허용목록이 아니라 소비자로 대사된다.
- 마일스톤·롤오버 표의 `shipment_id`: **마일스톤 부록이 등재**한다.

**결정 — 선적 자신의 후속**: S3-2에서는 **0건**이다. CI/PL(S3-3)이 `ChildLink(SHIPMENT, ...)`를 더한다.

**근거**: "후속 전표가 살아 있으면 선행 취소 불가(역순 취소만)"(`D:29`, `D:39` ⑥), WBS `CHILD_LINKS` 등록(`W:114`), `NON_CHILD_FK_ALLOWLIST` 사유 "S3-2·S4-1이 PO_LINE 소비자를 등록"(`code:modules/trade_docs/chain.py:74-77`), §20 A "후속 생존 시 선행 취소 차단"(`D:419`).

**대안(기각)**: 선적 생존을 SO 상태(IN_SHIPMENT)만으로 막기. 상태와 사실이 어긋나면 가드가 뚫린다. CHILD_LINKS는 사실(살아 있는 행)을 본다.

**자율 확정 여부**: 자율 확정.

**되돌리기 비용: 낮음**(레지스트리 행).

**파급 테스트**
- `test_doc_chain_contract.py:85-108`: 부모 집합을 `{QT,PI,SO,PO}`로, `links_for(SO/PO)`를 갱신한다.
- `test_so_contract.py:30-45`, `test_po_contract.py:239-251`
- `test_purchase_order_lifecycle.py:425-436`: 대역 `fake_successors`를 실 테이블 테스트로 교체한다.

---

## A7. 상태 머신 — 선적 8값(활성 3엣지), SO IN_SHIPMENT 자동 수렴, COMPLETED 보류

### A7-1. 선적 상태 열거

| 코드 | §7.2 문면(`D:181`) | S3-2 지위 |
|---|---|---|
| `PLANNED` | 계획 | 활성(출생 상태, 편집 가능 = `EDITABLE_STATES`) |
| `RELEASE_ORDERED` | 출고지시 | 활성(동결 = `frozen_at` 기록) |
| `PICKING` | 피킹 | **RESERVED**(S4-2) |
| `INSPECTED` | 검수완료 | **RESERVED**(S4-2 — CI/PL 생성 허용 기준점) |
| `RELEASED` | 출고(원장 기록 시점) | **RESERVED**(S4-2 — OUT_SHIP 원장) |
| `SHIPPED` | 선적(ETD 실적) | **RESERVED**(S4-2 이후) |
| `CLOSED` | 종결 | **RESERVED** |
| `CANCELLED` | 취소 | 활성(종결, `TERMINAL_STATUSES`) |

- 최장값은 `RELEASE_ORDERED`(15자)이고, 상태 열은 VARCHAR(20)이다.

**선적 전이(활성)**

| 엣지 | 종류 | 통로 | 사유 |
|---|---|---|---|
| PLANNED → RELEASE_ORDERED | 사람 | **동결 액션 전용** `POST /shipments/{id}/release-order`(`FREEZE_ACTION_EDGES`) | 불요 |
| PLANNED → CANCELLED | 사람 | 범용 `/transitions` | **필수**(`REASON_REQUIRED_TO={CANCELLED}`) |
| RELEASE_ORDERED → CANCELLED | 사람 | 범용 `/transitions` | 필수 |

- 선적은 **자동 엣지 0**이다. `public_transition_targets(SHIPMENT) == {CANCELLED}`.
- ETD·B/L 등 실적 입력은 **상태와 독립된 마일스톤 데이터**다(마일스톤 부록). **[적대 R-01]** 단 ETD·BL_ISSUED·ETA 실적은 **RELEASE_ORDERED에서만** 받고(PLANNED면 422 `SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE`), 그 실적이 살아 있는 선적은 취소 409 `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`(실적을 사유와 함께 정정·삭제한 뒤 취소). "선적(ETD 실적)" 상태로의 자동 전이는 S3-2에서 하지 않는다. 출고(RESERVED)를 건너뛰는 우회 엣지가 되기 때문이다.
- 선적 취소의 부수 처리는 같은 TX에서 한다: 살아 있는 통관 기록 검사(A8) → **[적대 R-01] 살아 있는 ETD·BL_ISSUED·ETA 실적 검사(PR-4a)** → 전이 → SO 수렴(A7-2) → 아웃박스 `shipments.shipment.status_changed`.

**근거**: `D:181`. AMB-01 판단의 핵심은 §8.4 "검수 통과 후에만" 출고한다는 것과 출고가 원장 기록 시점(`D:231`)이라는 것이다. S4-2 "§7.2 선적 상태 연동"(`W:139-140`)이 이 엣지를 소유한다.

**대안(기각)**
- (a) 계획→선적 임시 직행 엣지: 무원장 "출고된" 선적이 생기고, P4 도입 시 데이터 정정이 필요하다.
- (b) RELEASE_ORDERED까지 RESERVED: 동결 지점이 없어 실무상 확정된 선적과 초안을 구분할 수 없다.
- (c) RELEASE_ORDERED→PLANNED 회수 엣지: 문면에 없다(추측 구현 금지). 필요하면 취소+신규로 한다.

### A7-2. SO 엣지 — IN_SHIPMENT만 연다(자동 수렴)

**결정**
- `AUTO_TRANSITIONS[SALES_ORDER]`에 다음 2개를 추가하고, `RESERVED[SALES_ORDER]`에서 `IN_SHIPMENT`를 뺀다.
  - `(CONFIRMED, IN_SHIPMENT)`: 살아 있는 선적이 1건 이상 생겼다(첫 선적 생성과 같은 TX).
  - `(IN_SHIPMENT, CONFIRMED)`: 마지막 살아 있는 선적이 취소됐다(복귀 대칭).
- 행위자는 유발자(선적 생성자·취소자)이고 `automatic=True`다. QT CONVERTED↔ISSUED 수렴과 같은 형태다(`D:184` ④, `code:modules/trade_chain/chain_ops.py:68-131`). `converge_parent`를 `child_kind == SHIPMENT`로 확장하고 `converge_sales_order_shipment`를 신설한다.
- 불변식: **확정 SO에 대해 `status = IN_SHIPMENT` ⇔ 살아 있는 선적 ≥ 1**. 통합 테스트로 고정한다.
- **IN_SHIPMENT에서는 ON_HOLD·CANCELLED 엣지를 열지 않는다.** 취소하려면 선적을 먼저 취소해야 하고(역순, CHILD_LINKS), 그러면 CONFIRMED로 자동 복귀한 뒤 취소한다. 재개 판정이 `confirmed_at`만 보는 구조(`code:modules/trade_chain/lifecycle.py:402-407`)라 IN_SHIPMENT→ON_HOLD를 열면 재개가 CONFIRMED로 오도착한다.
- **§15 L3 논증**: 도착 상태 IN_SHIPMENT는 이행 진행이지 약정 진입(확정)이 아니다. 4금(지출·발주 확정 / 법적 판정 / 대외 발송 / 장부 확정) 어디에도 해당하지 않는다. 다만 §15 부기 "SO 자동 엣지 0"(`D:324` ②)과 `test_po_and_so_have_no_automatic_edges`(`tests/architecture/test_doc_machines.py:123-126`)가 이를 금지하고 있으므로 **DESIGN §15 부기 개정, ADR, 테스트 개명**("PO 자동 엣지 0, SO 자동 엣지는 IN_SHIPMENT 수렴 2개뿐")을 세트로 한다. `no_auto_confirm` REGISTRY의 `record_birth`·`record_transition` 허용 호출처에 선적 오케스트레이터 파일을 추가한다.

**근거**: WBS "RESERVED 상태 엣지(SO IN_SHIPMENT·COMPLETED) 추가와 상태 총수 테스트 갱신"(`W:114`), `D:184` ⑤, 비대칭 결손 선례(`D:140` ②, ADR-0038).

**대안(기각)**
- (a) 사람 엣지 CONFIRMED→IN_SHIPMENT: 선적 없이도 IN_SHIPMENT가 되는 거짓 상태가 생긴다.
- (b) 진입 트리거를 출고·ETD 실적으로: 출고는 RESERVED라 S3-2에서는 영영 도달할 수 없다. S4-2가 의미를 좁히는 것은 가산 변경으로 가능하다.

### A7-3. SO COMPLETED — **S3-2에서 열지 않는다**(RESERVED 유지)

**결정**
- `COMPLETED`는 `RESERVED[SALES_ORDER]`에 남긴다. `CLOSED_STATUSES = ("COMPLETED","CANCELLED")`와 `open_order_amount`는 **무접촉**이다(`code:modules/credit/exposure.py:19-39`).
- **아키텍처 테스트 신설**: "receivable provider가 기본값(`is_default_provider()`)인 동안 `COMPLETED ∈ RESERVED[SALES_ORDER]`". COMPLETED 엣지는 S3-3 미수 provider `reflected=True` 등록과 같은 PR에서만 열리도록 기계적으로 고정한다(`code:modules/credit/providers.py:94-108`).
- 회귀: `IN_SHIPMENT` SO는 노출에 **전액 산입**된다. 술어가 음수 집합이라 자동 산입되며, 상태별 산입 표 테스트(`tests/integration/test_credit_evaluation.py:128-151`)에 행을 추가한다.

**근거**: 노출 술어가 COMPLETED를 통째로 제외한다(`code:modules/credit/exposure.py:19`). S3-3 provider 전에 열면 선적이 완료된 SO가 노출에서 빠지고 미수 반영은 없어 **노출 공백**이 생긴다. 이는 P-01(`P:559`)·ADR-0064와 S3-3 DoD "노출 공백 0"(`W:121`)이 막으려는 바로 그 공백이다. 게다가 COMPLETED의 자연 트리거(선적 전건 종결)는 A7-1에 따라 P4 전에는 도달할 수 없다.

**대안(기각)**
- (a) COMPLETED 엣지를 열고 `CLOSED_STATUSES`를 `("CANCELLED",)`로 좁히기: 부분 인덱스 `ix_sales_orders_open_exposure` 술어 재생성 마이그레이션이 필요하다(`code:modules/sales_orders/models.py:196-204`). 또 S3-3이 다시 되돌려야 해서 왕복 비용이 2배다.
- (b) 진입 조건만 걸고 열기: 도달 불가능한 죽은 엣지다.

**자율 확정 여부**: 자율 확정. **WBS 문면("COMPLETED 엣지 추가")과의 불일치이므로 §10 보고 항목**이다.

**되돌리기 비용: 낮음.** S3-3이 엣지 1개를 가산하고 RESERVED에서 1개를 빼며 총수를 갱신하면 된다. 반대로 지금 열었다가 공백이 실재하면, 과소 노출로 확정된 SO를 소급 재평가할 수 없어 **높음**이다.

### A7-4. 상태 총수(갱신 후 핀)

| 문서 | 상태 | 허용(사람·자동) | 미허용 | 쌍 |
|---|---|---|---|---|
| QT | 5 | 6 (3·3) | 14 | 20 |
| PI | 5 | 8 (1·7) | 12 | 20 |
| SO | 8 | **10 (8·2)** | **46** | 56 |
| PO | 6 | 3 (3·0) | 27 | 30 |
| **SHIPMENT** | **8** | **3 (3·0)** | **53** | **56** |
| **합** | | **30 (18·12)** | **152** | **182** |

- 기존은 25/101/126(`D:184` ①)이다.
- RESERVED: SO {PARTIALLY_ALLOCATED, ALLOCATED, COMPLETED}, PO 3 그대로, SHIPMENT {PICKING, INSPECTED, RELEASED, SHIPPED, CLOSED}.
- 갱신 대상 테스트:
  - `test_doc_machines.py:36-51,73-87,123-146,149-166`
  - `test_sales_order_lifecycle.py:132-141`
  - `machine.py:7-10` 독스트링
  - ~~`trade_chain/router.py:95-96,109-110`: 임포트 시 assert가 있으므로 엣지와 Literal을 같은 커밋에서 바꾼다.~~ **[적대 R-23]** SO Literal은 무변경(자동 엣지는 `public_transition_targets` 밖) — assert 통과만 확인. 신설 `ShipmentTarget`만 assert 추가.
  - 오더 보드 완전성 `test_order_board_contract.py:74-87`: IN_SHIPMENT가 RESERVED에서 빠지므로 매핑이 필수다.
- 보드 처리 권장: 보드 부록이 **`SO_IN_SHIPMENT`("선적중") 열을 1줄 가산**한다. 보드 상수 독스트링이 이 경로를 지정하고 있고(`code:modules/order_board/constants.py:3`), 카드가 조용히 사라지지 않는다(fail-visible). 열 UI는 화면 부록 경계다.

**되돌리기 비용(A7 전체): 중간.** 엣지 폐지는 가능하지만 IN_SHIPMENT 이력 행은 그대로 남는다(재계산하지 않음).

---

## A8. `customs_records` — 사람이 입력한 신고 사실만, 수리일이 적재의무의 원천

**결정 — 컬럼**

| 열 | 타입 | NULL | 비고 |
|---|---|---|---|
| `shipment_id` | BIGINT FK `shipments.id` RESTRICT | NOT NULL | |
| `declaration_kind` | VARCHAR(8) | NOT NULL | `EXPORT`(수출신고)·`IMPORT`(수입신고) |
| `declaration_no` | VARCHAR(40) | NOT NULL | 외부 신고번호(형식 CHECK 없음 — 비공백·대문자·제어문자 금지만) |
| `declared_on` | DATE | NOT NULL | 신고일(현지 날짜) |
| `accepted_on` | DATE | NULL | **수리일** — NULL = 미수리 |
| `customs_broker_partner_id` | BIGINT FK `partners.id` RESTRICT | NULL | 유형 CUSTOMS_BROKER 검증(KEY SHARE — **[적대 R-08] shipments 잠금보다 먼저**) |
| `note` | VARCHAR(1000) | NULL | |

- 이 밖에 version, 감사 컬럼, soft delete를 둔다.

**결정 — 제약·인덱스**
- `ck_customs_records_kind_valid`
- `ck_customs_records_accept_after_declare`: `accepted_on IS NULL OR accepted_on >= declared_on`(**[적대 R-26]** 서비스 선검증 422 `SHIPMENTS.CUSTOMS.ACCEPT_BEFORE_DECLARE`) · **[적대 R-18]** 신고일·수리일 ≤ `today_kst()`(422 `SHIPMENTS.CUSTOMS.DATE_IN_FUTURE`) · **[적대 R-16]** 표 생성은 M15(PR-4a)
- `ck_customs_records_declaration_no_shape`: 공백 금지와 `[[:cntrl:]]` 금지
- `uq_customs_records_declaration_live`: `(declaration_kind, declaration_no) WHERE deleted_at IS NULL`. 위반은 409 `SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE`.
- `ix_customs_records_shipment_live`: `(shipment_id) WHERE deleted_at IS NULL`

**결정 — 규칙**
- 선적:통관 = **1:N**이다(분할 신고 허용 — [통합 X-03] 유지). **[통합 X-02]** `accepted_on`이 수리일의 **유일 원천**이고 마일스톤 `CUSTOMS_CLEARED` 실적으로 복사하지 않는다. 유효 수리일 = 구분 일치·살아 있는 통관 기록의 `MIN(accepted_on)`(읽기 시 파생).
- 구분 대응: EXPORT 선적에는 EXPORT 신고만, IMPORT 선적에는 IMPORT 신고만 허용한다. 위반은 422 `SHIPMENTS.CUSTOMS.KIND_MISMATCH`다.
- 취소된 선적에는 기록할 수 없다(409 `SHIPMENTS.SHIPMENT.NOT_ACTIVE`).
- **선적 취소 가드**: 살아 있는 통관 기록이 있으면 선적 취소는 409 `SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE`다. 통관 기록을 먼저 사유와 함께 soft delete해야 한다(역순 원칙의 사실 기록판). 통관 기록에는 status·doc_number가 없어 CHILD_LINKS로 표현할 수 없으므로, 선적 취소 서비스의 명시 검사와 J 테스트로 고정한다.
- 수정: version 409와 audit_log를 남긴다. `accepted_on` 변경은 파생 기일(적재의무)을 바꾸므로 **사유 필수**다(422 `SHIPMENTS.CUSTOMS.REASON_REQUIRED`). 사유는 audit_log에 남긴다.
- **세율·과세가격·세액·HS 열은 두지 않는다.** 법적 판정 금지(`D:313`)와 문면 부재 열 비신설(signatories 선례 `D:112` ④)에 따른다. 수입 세금 납부기한은 마일스톤 부록 소관이다.
- PO 상태·잔량은 무접촉이다(S4-1, `D:184` ⑤).

**근거**: §3 표 맵 "customs_records"(`D:70`), 사슬 "→ 수출신고"·"→ 통관 → 입고"(`D:173`), WBS "`customs_records`(PO 후반 전이와 별개)"(`W:114`), 적재의무=수리일+30(`D:203`), 비범위 "관세율·요건·원산지의 자동 판정"(`D:19`).

**대안(기각)**
- (a) 선적 헤더에 신고번호·수리일 열: 분할 신고를 표현할 수 없고, FIELD_POLICY상 동결 후 편집이 FREE 확장을 요구한다.
- (b) 통관 기록 채번(`CC-` 등): 외부 번호가 정본이라 이중 식별자다.
- (c) 세액 열: 문면 부재이고 L3 경계를 침범한다.

**자율 확정 여부**: 자율 확정.

**되돌리기 비용: 낮음.** 열 가산이다. 선적 취소 가드를 완화하는 것은 서비스 1곳이다.

---

## A9. 테이블 정책·불변·이력

| 테이블 | table_policy | 사유 |
|---|---|---|
| `shipments` | MUTABLE | 상태·FREE 열 갱신, 정정은 취소+신규 |
| `shipment_lines` | MUTABLE | PLANNED 구간 수량 편집과 soft delete |
| `shipment_parties` | MUTABLE | 부속 당사자 교체(audit) |
| `customs_records` | MUTABLE | 수리일 기입·정정(audit+사유) |
| `shipment_status_log` | **IMMUTABLE** + `revoke_mutations` | §17.5 확장 "상태 변경 이력 성격"(`D:356`), S3-1 상태이력 4표 선례 |

- `_NEVER_SEEDED`(`tests/architecture/test_scheduler_registry.py:56-73`)에 위 5표를 등재한다. 마이그레이션 시드는 0이다.
- users FK: `assignee_id`는 `ASSIGNMENT_TARGETS`(A11)에 등록한다. `created_by_id`·`updated_by_id`·상태이력 `actor_user_id`는 `USER_FK_CLASSIFICATION`에 등록한다.
- stock_movements는 **무접촉**이다. S3-2는 원장 행을 만들지 않는다.
- 파급 테스트: `tests/integration/test_table_policy.py:35-64`.

**되돌리기 비용: 낮음**(분류 행). IMMUTABLE 해제는 ADR이 필요하므로 의도적으로 비싸다.

---

## A10. 잠금 순서(`LOCK_ORDER`) 개정 — ADR 필수

**결정**: `… → sales_orders → purchase_orders → **shipments** → **customs_records** → approvals → lines → doc_number_seq`. **[통합 X-09]** 최종 표기는 `shipments → shipment_children`(milestones·customs_records·shipment_parties, PO 소유 OEM 마일스톤 포함) — 통합 §2.11.

- 선적은 SO·PO의 후속이므로 조상→자기 순서다(`lock_chain` 관용).
- 통관 기록은 선적 행을 잠근 뒤에 잠근다.
- 선적 라인은 기존 `lines` 범주(id 오름차순)다.
- 거래처 KEY SHARE(당사자·관세사 유형 검증)는 기존 `partners` 위치(원천 헤더보다 앞)다.
- 튜플 중간 삽입이라 기존 상대 순서 단언은 유지된다. **선적 경로 계측 테스트를 신설**한다(`test_shipment_concurrency.py` — 실제 동시 실행, GC-F1 규칙).

**근거**: §17.2 보강 ② "전역 잠금 순서(LOCK_ORDER, 변경은 ADR)"(`D:344`), `code:modules/trade_docs/locking.py:32-43`.

**대안(기각)**: 선적을 `lines` 뒤에 두기. 라인 잠금 후 헤더 잠금은 다른 경로와 역순이 되어 교착 위험이 있다.

**자율 확정 여부**: 자율 확정.

**되돌리기 비용: 중간.** 순서 변경은 교착 재검증과 계측 테스트 갱신이 필요하다.

---

## A11. 권한·담당자·이관

**결정**
- 권한(`authz_matrix` `GOVERNED_PREFIXES`에 `/api/v1/shipments` 등재 — 등재하지 않으면 매트릭스가 공회전한다):
  - 조회(목록·상세·라인·당사자·통관·상태이력)는 **전 역할 ALLOW**다.
  - ~~쓰기 전부 ADMIN·TRADE·LOGISTICS ALLOW~~ **[통합 X-14]** 동작별: 생성·라인·취소 = A·T, 헤더(FREE·국가)·출고지시·당사자·통관·마일스톤 = A·T·L, CERT·VIEWER DENY(통합 §2.9).
  - LOGISTICS는 **최초의 전표 쓰기 허용**이다(현재 전 전표 쓰기 DENY, `tests/architecture/authz_matrix.py:198-263`). §2 역할 부기와 ADR을 함께 낸다.
- 401→403→404→409→422 순서를 따른다. 부모-자식 불일치(다른 선적의 라인·당사자·통관 id)는 404다(`D:370`).
- **수입선적 화면과 응답에 PO 원가가 없다**(A3에서 복사하지 않음). 그래서 LOGISTICS·VIEWER에게 원가 채널이 새로 열리지 않는다. 원천 PO 조회는 기존 원가 마스킹 규칙을 따른다.
- 담당자: `assignee_id`(믹스인, NOT NULL)는 원천 담당자를 복사한다. `handover/targets.py` `ASSIGNMENT_TARGETS`에 **purchase_orders 다음**(LOCK_ORDER와 같은 순서)으로 등록한다(`code:modules/handover/targets.py:49-74`, `tests/architecture/test_assignment_coverage.py:47-61`).
- 쓰기 스키마는 전부 `extra="forbid"`다(`test_write_schema_forbid.py:114-131`).
- 멱등: 생성 2종, 출고지시, 전이, 통관 기록 생성, 당사자 추가는 `Idempotency-Key` 필수다(`code:api/deps.py:94-109`). 수정(PATCH)은 version으로 처리한다.
- 목록 API(선적, 통관 기록, 상태이력)는 Page 봉투에 기본 50이다(`D:376`, `test_auth_coverage.py:131-170`).

**근거**: `D:368`, `D:370`, ADR-07 담당자 라우팅(`D:31`), 이관 도구(`D:37`), ADR-0067(AUTHZ 행), 원가 봉쇄(`D:39` ③).

**대안(기각)**
- (a) TRADE 단독 쓰기: 물류 담당자의 출고지시·통관 입력을 막아 실사용이 불가하다.
- (b) LOGISTICS 단독 쓰기: 무역의 SO→선적 참조 생성 흐름을 끊는다.
- (c) 조회도 A·T·L 한정: 다른 전표 조회 패턴(전 역할)과 어긋난다. 판매가는 마스킹 대상이 아니다.

**자율 확정 여부**: 자율 확정. PR-16 부채 ③(역할 변경 화면)은 LOGISTICS 첫 사용처가 생기는 S3-2에 배정하는 것을 권장하며, 배정 판정은 통합 부록 몫이다.

**되돌리기 비용: 중간.** 역할을 좁히면 이미 작업하던 물류 사용자가 막힌다. 넓히는 것은 낮다.

---

## A12. 모듈 배치·임포트 방향

**결정**
- `app/modules/shipments/`(L1)는 모델(shipments·lines·parties·status_log·customs_records), 상태 상수, 단건 조회를 둔다. **L1→다른 L1 금지**라 SO·PO 모델을 임포트하지 않는다.
- 참조 생성(SO→선적, PO→선적), SO 수렴, 취소 가드는 **`trade_chain`(L2)**의 `shipment_flow.py`가 맡는다. QT→PI 선례를 따른다.
- `tests/architecture/test_import_direction.py`의 L1 목록과 **`known_s3`(`:94-104`)에 `shipments`를 추가**한다. 누락하면 검사가 공회전한다.
- `document_flow.FLOW_KINDS`·프런트 `DocumentFlowPanel`의 선적 노드는 화면 부록 경계다. 쿼리 5회 이내 계약(`code:modules/trade_chain/document_flow.py:4`)은 유지한다.

**되돌리기 비용: 낮음.**

---

## A13. 에러코드 (신규 — `<도메인>.<대상>.<사유>`, 문구에 조치 힌트, 카탈로그 1:1)

| 코드 | HTTP | 상황 |
|---|---|---|
| `SHIPMENTS.SOURCE.LINE_MISMATCH` | 422 | 라인의 원천 라인이 헤더 원천 전표 소속이 아님 |
| ~~`SHIPMENTS.SOURCE.KIND_NOT_OPEN`~~ | — | **[통합 X-21] 철회** — 생성 경로에 구분 입력이 없어 도달 불가(DB CHECK가 닫음) |
| `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE` | 409 | 수입선적 수량 > PO 라인 배정 가능량 |
| ~~`SHIPMENTS.SHIPMENT.NOT_EDITABLE`~~ | — | **[통합 X-20] 철회** — 커널 `TRADE_DOCS.DOCUMENT.FROZEN` 재사용 |
| `SHIPMENTS.SHIPMENT.NOT_ACTIVE` | 409 | 취소된 선적에 당사자·통관 기록 |
| `SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE` | 409 | 살아 있는 통관 기록이 있는 선적 취소 |
| `SHIPMENTS.PARTY.ROLE_DUPLICATE` | 409 | (선적, 역할) 부분 유니크 위반 번역 |
| `SHIPMENTS.PARTY.ROLE_NOT_ALLOWED` | 422 | 수출 SHIPPER·수입 CONSIGNEE·자동 행 수정 |
| `SHIPMENTS.PARTY.ENGLISH_NAME_MISSING` | 422 | 당사자 거래처 `name_en` 결측 |
| `SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE` | 409 | 신고번호 부분 유니크 위반 번역 |
| `SHIPMENTS.CUSTOMS.KIND_MISMATCH` | 422 | 선적 구분과 신고 구분 불일치 |
| `SHIPMENTS.CUSTOMS.REASON_REQUIRED` | 422 | 수리일 변경 사유 없음 |

**재사용(신설 금지)**
- `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`(수출 잔량 초과), `TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE`(원천 SO가 CONFIRMED·IN_SHIPMENT 아님 / PO가 ISSUED·SUPPLIER_CONFIRMED 아님)
- `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(SO·PO 역순 취소), `TRADE_DOCS.TRANSITION.NOT_ALLOWED`·`REASON_REQUIRED`
- `COMMON.CONCURRENCY.VERSION_CONFLICT`·`LOCK_BUSY`(55P03·40P01, `D:344` ③)
- 거래처 유형 불일치는 `require_partner_of_any_type`의 기존 코드를 쓴다.

**규칙**
- detail에는 문서번호·라인별 잔량만 담고 **금액을 기재하지 않는다**(`require_within_open` 선례).
- 사용자 detail과 log_context를 분리한다(`D:374`).
- 파급 테스트: `tests/unit/test_error_catalog.py:19-49`, `tests/e2e/test_error_contract.py:56-85`.

**되돌리기 비용: 낮음**(enum+카탈로그 행).

---

## A14. 마이그레이션

- 1건으로 5표(shipments·shipment_lines·shipment_parties·shipment_status_log·customs_records)를 만든다. `down_revision`은 그 시점의 단일 head다(현재 `f2cb6020b2bb`. 마일스톤·휴일 부록 마이그레이션과의 순서는 PR 분할에서 정한다).
- downgrade를 완결한다. 시드는 0이다.
- CHECK는 `create_table` 안에서 `op.f()` 이름으로 걸고 정의문 테스트로 고정한다(alembic check가 CHECK를 못 본다 — 함정 ①·⑪). 식별자는 63자 이내다(`uq_shipment_lines_so_line_live` 등 실측).
- `IMMUTABLE` 표에 `revoke_mutations`를 적용한다.
- 기존 표 변경은 0이다: SO 상태 CHECK에 IN_SHIPMENT가 이미 있고, `confirmed_at_consistent`가 IN_SHIPMENT를 수용한다(`code:modules/sales_orders/models.py:118-124`). SO 상태이력 CHECK는 `STATUSES` 파생이라 엣지 추가만으로는 재생성이 필요 없다. 이것은 PR 첫 커밋에서 실측한다.
- 드라이런 4종(heads 단일, 빈 DB upgrade, base 왕복, check 드리프트 0)을 지킨다(`D:374`).

---

## A15. 여신 노출 — `open_order_amount` 선적분 차감 금지 (P-01)

**결정**
- S3-2는 `credit/exposure.py`를 **한 줄도 바꾸지 않는다**. 선적분 차감은 S3-3 미수 provider `reflected=True` 등록과 같은 PR에서만 한다.
- 선적 모듈에서 노출 산식을 재구현하는 것은 `test_approval_contract.py:604-612`가 막는다.
- 이 부록이 남기는 S3-3용 입력: 선적 라인 `line_amount`(판매가 축, 통화=SO 통화)와 헤더 `fx_rate` 사본. S3-3이 "선적분"을 이 합계로 정의할지는 S3-3이 판정한다.

**근거**: WBS `W:114`, P-01(`P:559`), ADR-0064, S3-3 DoD(`W:121`), `code:modules/credit/exposure.py:8-9`.

**되돌리기 비용**: 해당 없음(무변경).

---

## A16. 테스트 배분 (§20 그룹 — 데이터 모델 몫)

| 그룹 | 케이스 |
|---|---|
| **A** | 부분선적 1:N: 합 = SO 수량이면 잔량 0, +1이면 409 EXCEEDS_OPEN / 선적 취소 후 잔량 복원 / 살아 있는 선적이 있는 SO·PO 취소 409 SUCCESSOR_ALIVE, 선적 취소 후 SO 취소 통과 / 선적 환율·단가·Incoterms가 SO와 같고 PATCH 시도는 422(forbid)·409 / **수입선적 후 PO `open_quantity` 불변**, 배정 가능량 초과 409 |
| **B** | 선적 RESERVED 5상태 진입 0(전이 통로 거부) — S3-3·S4-2 "검수 미완료 CI/PL 차단"의 전제 |
| **I** | `no_auto_confirm` 엔트리 확장(선적 출고지시 = 사람 1클릭·actor 필수), SO 자동 엣지 2개의 4금 논증 테스트 |
| **J** | 동시 부분선적 2건 **실제 동시 실행** → 합이 잔량을 넘지 않음(GC-F1) / 생성 더블클릭(같은 Idempotency-Key) → 1건 / 생성 TX 중간 실패 시 선적·라인·SO 상태·이력·아웃박스 전부 롤백 / version 409 / soft delete 후 같은 (선적, 역할)·신고번호 재유입 = 신규 / 통관 기록 생존 시 선적 취소 409 |
| **K** | 상태 총수 182쌍 핀 / CHILD_LINKS·허용목록 대사 / LINE_CONSUMERS 5건·kind 집합 / FIELD_POLICY 완전성(선적 2표)·FREE 4 유지 / table_policy / authz 행(L 쓰기 허용·C·V 거부·타 역할 URL 403) / 목록 페이지네이션 / known_s3 / **COMPLETED∈RESERVED while default provider** / 수입선적 응답에 원가 키 0 / 오더 보드 완전성 |
| **E2E** | SO 확정 → 선적 2건(부분) → SO IN_SHIPMENT → 1건 취소(IN_SHIPMENT 유지) → 2건째 취소 → CONFIRMED 복귀 |

- GC v1.5 후보(통합 부록이 번호 확정): 부분선적 1:N 잔량 0·초과·동시성·복원(A), 수입선적 PO 잔량 불변(A).

---

## A17. DESIGN·ADR 부기 대상(이 부록 몫)

- **DESIGN 부기**
  - §7.2: 선적 코드값·전이표·RESERVED, SO 자동 수렴 2엣지, COMPLETED 보류, 총수 30/152/182
  - §7.1: 구분 4종 중 2종 생성 경로 미개방, 원천 FK 정확히 하나, 수입 원가 비복사
  - §8.3: kind 필터·IN_TRANSIT·배정 가능량, 원천 헤더 FOR UPDATE 선행
  - §17.2: LOCK_ORDER 개정
  - §17.5: shipment_status_log 등재
  - §15: SO 자동 엣지 0 개정
  - §2: LOGISTICS 첫 전표 쓰기
  - §3 표 맵: customs_records 형태
- **ADR 후보**(번호는 0074부터, 통합 부록이 배정)
  - ① 선적 커널 편입·상태 머신·RESERVED·SO IN_SHIPMENT 수렴
  - ② COMPLETED 보류(노출 공백 방지)와 provider 결속 테스트
  - ③ 수입선적 IN_TRANSIT 소비와 kind 필터(S4-1 승계 계약)
  - ④ LOCK_ORDER 개정
  - ⑤ LOGISTICS 쓰기 권한

---

## 10. 멈춰서 보고할 항목(이 부록 범위)

1. **WBS "RESERVED 엣지 COMPLETED 추가"(`W:114`) 미이행.** 노출 공백(A7-3) 때문에 S3-3 provider PR로 이월한다. WBS v1.6 정정 후보이고 부채로 등재한다.
2. **§7.5 구분 4종 중 채널입고·샘플무상의 생성 경로 미개방**(A2). 값만 싣고 DB CHECK로 닫는다. 부채로 등재한다.
3. **§15 부기 "SO 자동 엣지 0" 개정**(A7-2). IN_SHIPMENT 수렴 2엣지를 위해 문서·테스트 계약을 바꾼다.
4. **§8.3 부기 ② "선적 소비는 헤더 FOR SHARE"의 운용 보강**(A4) — **[적대 R-13] 해석이 아니라 문면 변경으로 보고**. FOR UPDATE를 선행하며 시그니처는 불변이다.
5. **선적 상태 중 PICKING 이후 5종은 S3-2에서 도달 불가**(A7-1). 따라서 "선적(ETD 실적)" 상태는 P4 이후다. ETD 실적은 마일스톤 데이터로만 입력한다.

## 부채 등재 후보(이 부록)

- 다중 SO 합적(N:M): 트리거는 합적 실수요.
- 원천 라인 소속 DB 강제(복합 FK): 트리거는 불일치 사고.
- 채널입고·샘플무상 경로: 트리거는 S4-3·S5-2와 무상 SO 판정.
- SO COMPLETED 엣지: 트리거는 S3-3 provider PR.
- 중량·CBM·박스 열: 트리거는 S3-3 PL.
- 비거래처 수하인("TO ORDER"): 트리거는 S3-3 L/C.
