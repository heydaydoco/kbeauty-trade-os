# S3-1 계획서 안건 전문 — 묶음 D: 오더 인테이크·게이트 6종(+PI 입금)·오더 보드

- 작성: 묶음 D 심판(최종 설계자). 전 안건 **자율 확정**(오너 지시 2026-09-29 — 판정 후보를 권장안으로 확정, 사후 번복 가능). "미정" 결론 없음. 안건마다 되돌리기 비용을 병기한다.
- 방법: 3개 관점 제안(strict/risk/fit)의 사실 주장을 코드·DESIGN에서 직접 grep/독해로 검증했고, 충돌 지점은 이유를 들어 하나를 골랐다. **정적 독해만 했다 — 실행 검증 못 했음**(pytest·alembic·DB·dev 서버 미실행. 식별자 길이는 파이썬으로 문자 수만 셌다).
- 정합 기준: 이미 확정된 **A 판정서(`d-A.md`)·B 판정서(`d-B.md`)** 를 정본으로 삼는다(SO 컬럼명·상태 열거·채번 시점·이벤트 규약·동결 분류). C(승인)·E(여신·입금)·F(역할·이월) 판정서는 아직 없어 **가장 그럴듯한 가정**을 두고 §0-3에 적었다(통합 검토가 맞춘다).
- 안건 번호는 D1~D8. 제안의 qid 대응: D1=제안 D1 / D2=제안 D2 / D3=제안 D3 / D4=제안 D4 / D5=제안 D5 / D6=제안 D6 / D7=제안 X1~X5(횡단) / D8=실행 계획(신설).

---

## 0. 심판 노트

### 0-1. 제안 사실 주장 검증 결과 (틀린 것은 걸러냈다)

| # | 주장(출처) | 확인 | 판정에 미친 영향 |
|---|---|---|---|
| 1 | `ASSIGNMENT_TARGETS`에 `AssignmentTarget('오더 인테이크', OrderIntake, OrderIntake.assignee_id)` 등록(strict·risk) | **틀림**. `test_every_assignment_column_is_a_handover_target`은 `(target.label, target.column.key)`를 **`(테이블명, 컬럼명)`** 과 대조한다(`tests/architecture/test_assignment_coverage.py`, 선례 라벨=`"certifications"`). 한글 라벨을 쓰면 실제로는 등록했는데도 "누락"으로 실패한다 | 라벨은 **테이블명 `"order_intakes"`**(D1) |
| 2 | 오더 보드 비-Page 응답을 위해 "K 페이지네이션 스캔의 허용 목록에 order-board 등록"(strict) | **그런 허용 목록은 없다**. 실재 스캔은 ① `test_no_endpoint_returns_a_bare_list`(200 응답이 **최상위 배열**인 경우만) ② `test_list_endpoints_use_the_page_envelope`(operationId가 `list_`로 시작하면 Page 봉투 요구) 둘뿐(`test_auth_coverage.py:131,158`) | 비-Page 응답은 **객체 최상위 + 함수명 `list_` 접두 금지**로 충돌 없이 통과(D6). 허용 목록을 만들지 않는다. 열 상한·건수는 별도 테스트로 고정 |
| 3 | 페이지 파라미터 `page_size`(strict·risk) | **틀림**. `PageParams`는 `page`·`size`(기본 50·최대 200)다(`core/pagination.py`) | 전 목록은 `size` |
| 4 | 매핑 등록 서비스가 `catalog.add_item_code`(fit) | **틀림**. `partners/service.py:394 add_item_code`(라우터 `POST /partners/{id}/item-codes`, 권한 `CAN_REGISTER=(TRADE,)`+ADMIN 통과). 저장 정규화는 **`strip()`뿐**, 유일키는 (거래처, 품번) **대소문자 구분 정확 일치**(`unique_active("customer_item_codes","partner_id","buyer_item_code")`) | 품번 해석은 **strip 후 정확 일치**(D4-①). strict의 "NFKC→대문자" 정규화는 등록 규칙과 어긋나 기각(등록 `ab1`·`AB1` 공존을 합쳐 모호해진다) |
| 5 | `extraction_snapshot` 불변을 BEFORE UPDATE 트리거로(strict) | 리포 전체 `CREATE TRIGGER` **0건**(migrations·app grep). B 판정서도 트리거 미채택(ADR-0028·0040 반복 기각) | 트리거 없이 **ORM `before_update` 가드 + Core `update()` AST 스캔 + CHECK**(D1) |
| 6 | 게이트 정책을 마이그레이션 시드(`PRICE_DEVIATION 500bps`, `PI_DEPOSIT WARN`)로(strict) | **함정 ⑩ 위반**. Version·Actor 믹스인을 단 테이블은 users FK 때문에 마이그레이션 시드 금지(PROGRESS 함정 ⑩, r2 §2-1). `roles`·`document_types`처럼 믹스인 없는 시드 테이블만 가능 | 시드 없음: **0행=평가 불능(UNKNOWN, fail-visible)** + 앱 경로 CLI `seed-gate-policies`(멱등)+ADMIN PUT(D3) |
| 7 | MOQ 컬럼 `skus.moq_qty`(strict·risk) / `skus.moq`(fit) | A 판정서 A11-4가 **`skus.moq INT NULL CHECK (moq IS NULL OR moq>0)`** 로 이미 확정(마스터 변경·CSV 왕복 포함) | D는 A의 `skus.moq`를 **소비**만 한다 |
| 8 | 배치 단가 함수 `prices_at_many`(strict)·`prices_at`(fit)를 D가 신설 | A A9-1이 `catalog.pricing.prices_at(session, sku_ids, currency, on) -> dict[sku_id, Money]`를 이미 신설(현 `price_at(*, sku_id, price_type, currency, on)`은 세션 인자 없이 자체 UoW 합류 — 실측) | D는 소비만 한다 |
| 9 | 확정 이벤트 `orders.sales_order.confirmed`·`orders.sales_order.received`(3안 공통) | B6이 **전이별 개별 이벤트를 만들지 않고 `sales_orders.sales_order.created` + `.status_changed`(payload 화이트리스트)** 로 확정. `record_birth/record_transition` 안에서만 발행 | D는 SO 이벤트를 **직접 발행하지 않는다**(B 통로가 발행). 후속 소비 진입점 = `status_changed(to_status=CONFIRMED)`(D5) |
| 10 | 인테이크 헤더에 `po_date`·`payment_type`·`incoterm`을 두고 SO로 1:1 복사(strict·fit) | A의 SO 헤더에는 `buyer_po_date`가 **없고**(A2), 결제조건 4열·Incoterms 3열은 **행 단위 형태 CHECK**(A5 `payment_terms_shape`·A6 `incoterm_all_or_none`)로 묶여 있어 **부분 복사는 SO INSERT를 CHECK 위반으로 만든다** | 결제조건·인코텀즈·환율은 **인테이크에 싣지 않는다**(SO 접수 후 SO 편집에서 A 규칙으로 입력·확정 시 `frozen_complete`가 강제). `buyer_po_date`만 A에 nullable 1열 추가 요구(§0-3) |
| 11 | PO 정규화 키를 영숫자만 남기는 방식(fit) | A가 `normalize_buyer_po_no`(NFKC→대문자→모든 공백 제거, 구두점 보존)를 이미 확정하고 `buyer_po_no_key VARCHAR(60)`을 SO에 두었다. 과병합('PO-1'='PO1')은 HARD 차단이라 오차단 위험 | A 규칙 채택. 단 **제로폭(유니코드 Cf) 문자 제거**를 A 함수에 추가 요구(엑셀·PDF 복붙 오염이 키 불일치의 주원인) |
| 12 | 보드 CSV 최대 5,000행(strict·risk) | 기존 내보내기 6모듈이 전부 **`EXPORT_MAX_ROWS = 50_000`**(catalog·materials·markets·partners·ingredients·documents, 초과 시 `VALIDATION_INVALID_FIELD` 422) | 선례 그대로 50,000(D6) |
| 13 | 내보내기 audit 기록(strict·risk) | `audit.record` 호출자는 identity·handover·platform.storage·system(backups)·cli뿐이고 목록 CSV 내보내기는 audit를 쓰지 않는다(r4 ⑤, 선례) | audit 기록 안 함(문면 근거 없음, 선례 준수) — 관찰 원장 |
| 14 | 벌크 건별 키 `'{bulk_key}:{action}:{id}'`(strict·risk) | `idempotency_keys.idempotency_key`는 **VARCHAR(128)**, 헤더 길이 검증 없음 | 건별 키는 `sha256(...)` hex 64자로 파생(D6). 헤더 키 길이 상한 미검증은 기존 결함 → 관찰 |
| 15 | claim을 "완료 저장하지 않고 해제"해야 같은 키로 재시도 가능(strict) | `idempotency.claim`은 **선점 행이 있고 결과가 없으면 이어받아 수행**한다(`service.py` — "영원히 막힌 키를 만들지 않는다"). 단 **같은 키+다른 본문은 409 KEY_CONFLICT** | 별도 해제 불필요: BLOCKED는 `complete`를 호출하지 않고 커밋. 프런트 규칙: 확정 버튼 키는 **(SO id, version) 조합당 1개**, 본문이 바뀌면 새 키(D5) |
| 16 | 저장 필터 테이블 컬럼명 `user_id`가 handover 감지와 충돌(risk가 회피 언급) | `ASSIGNMENT_COLUMN_NAMES = {assignee_id, recipient_user_id, owner_user_id, in_charge_user_id}` — **`user_id`는 감지 대상이 아니다**. `owner_user_id`를 쓰면 이관 대상 등록을 강제당한다 | 컬럼명 `user_id` 채택(D6) |
| 17 | 마스터 목록 API에 `q`·유형 필터가 이미 있다(fit·strict 암묵 전제) | **없다**. `list_partners`·`list_skus`는 `PageParams`뿐이고 사용자 목록은 **ADMIN 전용**(`identity/router.py:89`). 프런트는 `?size=200` 드롭다운(200 초과 시 조용한 잘림 — 관찰 원장 다수, r6) | `q`(+거래처 `type`) 읽기 전용 확장 + 활성 사용자 조회(id·표시명) 최소 엔드포인트 필요(D6·D7) |
| 18 | QT 수주전환을 "사전 채움 MANUAL 인테이크 생성"으로만 허용(risk X1-4) | A4가 QT/PI→SO **참조 생성을 SO(RECEIVED) 직접 생성**으로 이미 확정. 게이트는 **SO 확정 시 전건 재평가**라 출처와 무관하게 적용된다 | risk 안 기각. 통로가 둘이어도 게이트는 SO 확정 한 곳(D5)이 지킨다 |
| 19 | 인테이크 라인 무상(0원) 허용 | A의 SO 라인 CHECK: 0원은 `is_free`+`price_reason` 필수 | 인테이크 단가는 **≥1**. 무상 라인은 SO 접수 후 SO 편집에서 A 규칙으로 추가(D2) |
| 20 | 인테이크 라인의 같은 SKU 중복 | A의 SO 라인 `unique_active(so_id, sku_id, is_free)` — 서로 다른 바이어 품번이 같은 SKU를 가리키면 SO INSERT가 유니크 위반 | `confirm_intake`가 **DUPLICATE_SKU 422**로 선차단(D5) — A11-5 "인테이크 중복 줄은 D의 스테이징 검토가 결정" 이행 |

### 0-2. 충돌 지점 판정 요약

| 충돌 | strict | risk | fit | **판정** | 이유 |
|---|---|---|---|---|---|
| 스테이징 구조 | 별도 모듈·2테이블 | 별도 모듈·3+1테이블(edit_log) | 별도 모듈·2테이블 | **별도 2테이블, edit_log 없음** | import_staging 불변식(행 불변·원자 확정·파일 미보관·ID 키 diff)과 충돌해 A(확장)·B-2(SO 겸용) 기각은 3안 일치. 수정 이력은 §12.1 목적("파서·프롬프트 개선 데이터")상 **원본 스냅샷(불변) 대 현재 값** 순 diff로 충족되고, 편집 단위 로그는 문면 근거 없음 |
| 소스 열거 | MANUAL·CSV | MANUAL·CSV | MANUAL·CSV_TEMPLATE | **MANUAL·CSV** | 소비분만(ADR-0041), 값 이름은 짧게 |
| CSV 원자성 | 그룹별 부분 성공 | 파일 전체 원자 | 형식 오류만 전체 거부, 업무 미해결은 착지 | **파일 전체 원자(모든 오류 리포트)** | 부분 착지 후 수정본 재업로드는 sha가 달라 이미 착지된 오더와 충돌한다. 업무 미해결 중 "거래처 미등록"은 착지시키지 않는다(NOT NULL 유지, AI 경로 소비 시 `DROP NOT NULL` 1건), **품번 미매핑만 착지**(검토 화면의 등록 유도가 그 용도) |
| 게이트 결과 저장 | 불변 테이블+override 테이블 | 캐시 컬럼+불변 테이블 | 저장 안 함+SO 컬럼 스냅샷 | **불변 `gate_evaluations`(확정 시도 전건)+불변 `gate_overrides`, 캐시 컬럼 없음** | 스테이징 검토는 저장 없는 실시간 계산(낡은 판정 오독 차단), 확정 시도만 저장(실패도 커밋). SO 컬럼 스냅샷(fit)은 B의 열 분류 레지스트리·A 스키마를 건드려 결합이 커진다 |
| WARN 의미 | ACK 필요 | override 필요 | 비차단 | **WARN=비차단(기록만), BLOCK/UNKNOWN=사유 있는 사람 결정 또는 데이터 정정** | §7.3 "경고/차단 설정"의 경고는 진행 가능이라는 통상 의미. 진행을 막아야 할 사안은 BLOCK으로 올린다 |
| override 부여 시점 | 확정 요청에 동봉 | 별도 액션(해시 결속) | 별도 액션(해시 결속) | **별도 액션 + 서버 계산 해시 + 낡은 판정 409** | override 권한(ADMIN)과 확정 권한(TRADE)이 다를 수 있어 동봉 방식은 역할 분리를 깬다 |
| 저장 필터 | DB | DB | 브라우저 | **DB** | §7.4가 "저장 필터"를 보드 기능으로 명시, 기기 간 유실 방지 |
| 벌크 액션 | CONFIRM_INTAKE·CONFIRM_SO·ASSIGN | ASSIGN·HOLD·CONFIRM_SO | CONFIRM_INTAKE·CONFIRM_SO·REASSIGN | **CONFIRM_INTAKE·CONFIRM_SO·ASSIGN** | 사유가 건별로 필요한 행위(보류·거부·취소·override)는 벌크에서 제외 |
| MARKET 게이트 override 권한 | ADMIN | ADMIN·CERT | ADMIN | **ADMIN** | GC-C2 "권한 있는 사용자"의 가장 좁은 해석, 확장은 상수 1곳(F 확정 사항) |
| 가격·MOQ 이탈 처리 | WARN+ACK | WARN+override | BLOCK+OVERRIDE | **BLOCK+OVERRIDE(TRADE·ADMIN, 사유 필수)** | 상업 정책 이탈은 사유 기록이 있는 사람 결정이어야 하고, WARN은 아무도 안 보고 통과하는 경로가 된다 |

### 0-3. 타 묶음에 둔 가정 (통합 검토가 맞춘다)

- **A**(확정 문서 기준): SO 컬럼명 `buyer_partner_id`·`buyer_po_no`·`buyer_po_no_key`(VARCHAR 60)·`dest_market_code`(VARCHAR(2) FK markets.code)·`currency`·`assignee_id`·`doc_date`, 라인 `sku_id`·`buyer_item_code`·`quantity`·`unit_price_amount`·`list_price_amount`·`price_basis('BUYER_PO')`·`requested_delivery_date`. **D가 A에 추가로 요구**: ① SO 헤더 `buyer_po_date DATE NULL`(CONTENT 분류) ② `normalize_buyer_po_no`가 제로폭(Cf) 문자를 제거 ③ SO 생성 서비스 `sales_orders.service.create_received_sales_order(session, *, actor, draft)`(D5-①에서 계약 명시) ④ 인테이크 잠금 순서를 A13 규약의 **맨 앞(⓪)** 으로 추가.
- **B**: `record_birth`/`record_transition`, `issue_document_number` 채번 최후 단계, `confirmed_at`·`content_rev`, `TRADE_DOCS.*` 에러 계열, 락 순서(거래처→QT→PI→SO→라인→채번), `lock_document`, `CONCURRENCY.LOCK.BUSY` 핸들러, SO 확정 응답 형태(B8: `/sales-orders/{id}/confirm`), 승인은 서버가 `(SO id, content_rev)` 결속 APPROVED를 조회해 소비.
- **C**: `approvals.find_consumable(session, *, subject_type, subject_id, content_rev) -> ApprovalRef | None`(읽기)와 `approvals.consume(session, approval, *, actor)`(원자 소비, `WHERE consumed_at IS NULL` rowcount==1) **2단 계약** — 게이트 미해소 시 승인을 소비하지 않은 채 BLOCKED 기록을 커밋해야 하므로 "조회"와 "소비"가 분리돼야 한다.
- **E**: `evaluate_credit(session, *, buyer_partner_id, currency, additional_exposure_amount, exclude_sales_order_id) -> GateOutcome`(gate_code='CREDIT', resolution=APPROVAL), 잠금 없는 호출은 참고값, PI 입금 판독 `evaluate_pi_deposit(session, *, sales_order_id, mode) -> GateOutcome`, PI 입금 원천은 S3-1에서 PI.status만 판독(payments는 S3-3).
- **F**: 역할표(D7), 활성 사용자 조회 엔드포인트 소유, ADR 번호 배정, DESIGN·WBS·PROGRESS 갱신, GC v1.4 매핑.

---

## D1. 오더 인테이크 구조 — 별도 모듈·2테이블·상태 3값·불변 스냅샷 (제안 D1 통합)

### (a) 목적·경계
바이어 PO(수동 입력·CSV)를 **스테이징 → 사람 검토 → 사람 1클릭 확정 → SO(접수) 생성**으로 받는 전용 저장소를 세운다. `import_staging`은 손대지 않는다(마스터 왕복 diff 전용 유지). 게이트 평가는 D3·D4, 확정 TX는 D5, 입구(수동·CSV)는 D2. 경계 밖: 결제조건·Incoterms·환율(SO에서 입력 — 근거 §0-1 #10), 원본 파일 보관(D2), 할당·선적(후속).

### (b) DESIGN 근거
§7.2(SO 첫 상태 "접수(스테이징)"), §7.4("스테이징 한 입구"·게이트), §12.1(스테이징→검토→확정, **거부·수정 이력 보존**), §12.2 ⑤·ADR-09(파일 해시+문서번호 멱등, 자동 확정은 "인테이크 전표까지"), §17.4(멱등 키=DB UNIQUE 부분 인덱스, (partner_id, 바이어 PO번호)), §17.5(CHECK 가능한 불변식), ADR-0038(총수 고정·3층 차단)·ADR-0041(죽은 열거 금지), CLAUDE.md "설계와 충돌하면 DESIGN 갱신+ADR 세트".

**해석(설계 보강 필요 — DESIGN §7.4·§12.2·§3 M9)**: "스테이징 한 입구"는 **원칙**(파서·AI·엑셀·채널 → 사람 검토 → 확정, 파일 해시+문서번호 멱등)이며, 오더 도메인의 물리 입구는 `order_intakes`, `import_staging`은 마스터 왕복 전용이다. SO '접수'는 **인테이크 확정의 산출물**(2단 흐름: 인테이크 PENDING → [사람 1클릭] → SO RECEIVED → [게이트·승인 후 사람 1클릭] → SO CONFIRMED). ADR-09 원문·§12.2 원문은 고치지 않고 **부기**를 덧붙인다(D8).

### (c) 데이터 변경 — 신규 모듈 `app/modules/order_intake/`, 테이블 2개

**`order_intakes`** (믹스인 Pk·Timestamp·SoftDelete·Version·Actor)

| 열 | 타입 | 규칙 |
|---|---|---|
| `source_kind` | VARCHAR(10) NOT NULL | `value_in('MANUAL','CSV')` — 소비분만(AI·이메일·채널은 소비 세션이 CHECK 재정의 마이그레이션 1건으로 추가) |
| `source_sha256` | CHAR(64) NULL | `~ '^[0-9a-f]{64}$'`. 업로드 **원문 바이트**의 sha256 |
| `source_group_key` | VARCHAR(100) NULL | `'{buyer_partner_id}|{buyer_po_no_key}'`(한 파일이 여러 인테이크를 만들므로 파일 단독 유니크는 두 번째 그룹부터 자체 위반) |
| `original_filename` | VARCHAR(255) NULL | 표시 전용 |
| `extracted_snapshot` | JSONB NOT NULL | **불변 원본**. MANUAL=최초 제출 본문 `{kind,header,lines}`, CSV=`{parser_version, header_cells, rows:[{row_no, cells}]}`(그룹의 원본 행, 수식 이스케이프 역변환 후 원문). §12.1 "원본 나란히 검토"의 원천 |
| `buyer_partner_id` | BIGINT NOT NULL FK partners RESTRICT | **등록 후 변경 불가**(잘못 고르면 거부 후 재등록). 서비스가 BUYER 유형·활성 재확인 |
| `buyer_po_no` | VARCHAR(60) NOT NULL | 원문(표시용), `btrim<>''` |
| `buyer_po_no_key` | VARCHAR(60) NOT NULL | 서버 산출 전용(A의 `normalize_buyer_po_no`) — 요청 스키마에 필드 없음. 정규화 후 길이>60·빈 값은 422 |
| `buyer_po_date` | DATE NULL | 바이어 PO 일자(서류용 — A에 SO 열 요청) |
| `currency` | CHAR(3) NOT NULL | 대문자 CHECK. 서비스가 `CURRENCY_MINOR_UNITS` 소속 선검사(422, `UnknownCurrencyError`→500 방지). **등록 후 변경 불가** |
| `dest_market_code` | VARCHAR(2) NOT NULL FK markets.code RESTRICT | `require_active_market_code`로 선검사. 컬럼명에 금지어(readiness 등) 없음 |
| `status` | VARCHAR(9) NOT NULL DEFAULT 'PENDING' | `value_in('PENDING','CONFIRMED','REJECTED')` |
| `assignee_id` | BIGINT NOT NULL FK users RESTRICT | 기본=등록자. **감지 이름 그대로** → `ASSIGNMENT_TARGETS`에 `AssignmentTarget("order_intakes", OrderIntake, OrderIntake.assignee_id)` 1줄(라벨=테이블명, §0-1 #1) |
| `last_line_no` | INT NOT NULL DEFAULT 0 | 라인 번호 카운터(A13 규약 — 헤더 잠금 하 +1, 결번 허용·재사용 금지). `>=0` |
| `reject_reason` | TEXT NULL | 거부 사유 |
| `decided_at` / `decided_by_id` | TIMESTAMPTZ NULL / BIGINT NULL FK users | 확정·거부 공통 결정 시각·행위자 |
| `sales_order_id` | BIGINT NULL FK sales_orders RESTRICT | **백링크는 이 한 방향만**(SO 쪽 역FK 없음 — 순환 FK·이중 진실 방지, SO의 출처는 조인) |

- CHECK: `ck_order_intakes_source_pair` = `(source_kind='CSV') = (source_sha256 IS NOT NULL)` AND `(source_sha256 IS NULL) = (source_group_key IS NULL)` / `ck_order_intakes_decision_consistent` = `(status='PENDING') = (decided_at IS NULL)` AND `(decided_at IS NULL) = (decided_by_id IS NULL)` / `(status='CONFIRMED') = (sales_order_id IS NOT NULL)` / `(status='REJECTED') = (reject_reason IS NOT NULL)` AND `reject_reason IS NULL OR length(btrim(reject_reason)) BETWEEN 5 AND 500` / `ck_order_intakes_po_key_nonblank` / `currency = upper(currency)`.
- UNIQUE(전부 `WHERE deleted_at IS NULL` 부분 인덱스, 직접 `Index` 선언 — `unique_active` 헬퍼는 술어가 `deleted_at`뿐):
  (a) `uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending` `(buyer_partner_id, buyer_po_no_key) AND status='PENDING'` — 진행 중 중복 차단(CONFIRMED는 SO 유니크가 이어받고 REJECTED는 키 해방);
  (b) `uq_order_intakes_source_sha256_source_group_key_pending` `(source_sha256, source_group_key) AND status='PENDING' AND source_sha256 IS NOT NULL` — 파일 해시 멱등(ADR-09, `import_staging`의 PENDING 한정 유니크와 같은 꼴 — 확정 후 재업로드는 (a)·SO 유니크로 수렴);
  (c) `uq_order_intakes_sales_order_id_active` `(sales_order_id)` — 인테이크 1건=SO 1건.
  `UNIQUE(id, currency)`(라인 복합 FK 대상). 식별자 전수 63자 이내(최장 57자, 실측 계산).
- 인덱스: `(status, assignee_id)`, `(buyer_partner_id)`, `(created_at)`.

**`order_intake_lines`** (믹스인 Pk·Timestamp·SoftDelete·Actor — Version 없음: 라인 편집은 헤더 행 잠금+헤더 version 증가, A13 규약)

| 열 | 타입 | 규칙 |
|---|---|---|
| `intake_id` | BIGINT NOT NULL | 복합 FK `(intake_id, currency) → order_intakes(id, currency)` RESTRICT — 라인 통화=헤더 통화를 DB가 강제(헤더 통화는 불변이라 ON UPDATE 처리 불요) |
| `currency` | CHAR(3) NOT NULL | 대문자 CHECK(행 단위 금액 쌍, ADR-0003 ④) |
| `line_no` | INT NOT NULL CHECK ≥1 | `unique_active(order_intake_lines, intake_id, line_no)` |
| `buyer_item_code` | VARCHAR(100) NOT NULL | **strip 원문**(`btrim<>''`). 초과 422 |
| `sku_id` | BIGINT NULL FK skus RESTRICT | **검토자가 본 해석 결과의 저장본**(NULL=미매핑). 요청 스키마에 없음 — 서버가 등록·수정·`resolve`마다 재해석해 대입 |
| `quantity` | INT NOT NULL | `BETWEEN 1 AND 99999999`(A 규약) |
| `unit_price_amount` | BIGINT NOT NULL | `BETWEEN 1 AND 9007199254740991`. **무상(0원) 라인은 인테이크에서 받지 않는다**(SO 접수 후 A 규칙으로 추가) |
| `requested_delivery_date` | DATE NULL | 라인 단위(A의 SO는 납기가 라인 열) |
| `source_row_no` | INT NULL CHECK ≥2 | CSV 행 출처(엑셀 행번호와 일치 — 헤더 1행) |

- 서비스 선검증: `quantity × unit_price_amount ≤ 2^53−1`, 라인 합계 ≤ 2^53−1(A의 SO CHECK를 인테이크 단계에서 422로 선번역 — `TRADE.LINE.AMOUNT_OUT_OF_RANGE` 재사용). 인테이크당 라인 상한 **200**(초과 422).
- `table_policy.MUTABLE_TABLES`에 둘 다 등재. `registry.py`에 모델 모듈 등록(모델 파일명 `models.py` 한 이름 — 글롭 규약).
- **마이그레이션 성격**: 신규 `create_table` 2건(A의 `sales_orders` 이후 리비전), CHECK는 `sa.CheckConstraint(..., name=op.f(...))`로 테이블 정의 안에(`alembic check` 감지 대상 아님 → 정의문 테스트로 고정, 함정 ①·⑪), 시드·백필 없음, downgrade는 `drop_table`.

### (d) 자동화 4금 저촉 여부
저촉 없음. 인테이크는 발주·법적 판정·대외 발송·장부 확정 어느 것도 아니며, **어떤 입구(수동·CSV·후속 AI)도 PENDING으로만 착지**하고 CONFIRMED로 가는 유일 경로는 사람 1클릭 `confirm_intake`(D5). ADR-09의 "결정적 파서 조건부 자동 확정 예외"는 S5-4 소비이며 S3-1은 **구현하지 않는다**(아키텍처 테스트가 부재를 고정, S5-4는 ADR로 테스트를 개정).

### (e) 상태·전이·불변
- 상태 3값, 방향 쌍 6(자기전이 제외): **허용 2 = PENDING→CONFIRMED(사람 1클릭 `confirm_intake`) · PENDING→REJECTED(사유 ≥5자, 사람)**, 미허용 4. 종결 2태(CONFIRMED·REJECTED) 탈출 0. 자동 전이 0.
- **상태 대입 단일 통로** `order_intake/machine.py::apply_intake_transition(session, intake, to, *, actor_id, reason=None)` — 전이 표는 단일 상수(총수 EXPECTED=(2,4)), 이 함수 밖의 `.status =`·`update().values(status=…)`·`OrderIntake(status=…)` 0건(아키텍처 스캔+자기검사). 생성 스키마·PATCH 스키마에 `status`·`sales_order_id`·`decided_*`·`source_*`·`buyer_po_no_key` 필드 **구조적 부재**+`extra='forbid'`.
- **불변 필드**(등록 후 변경 불가): `extracted_snapshot`·`source_kind`·`source_sha256`·`source_group_key`·`original_filename`·`buyer_partner_id`·`currency`. 강제 3층: ① DB CHECK(쌍 규칙) ② **ORM `before_update` 가드**(위 열의 속성 히스토리에 순변경이 있으면 예외) ③ AST 스캔(Core `update(OrderIntake)`가 위 열을 건드리는지 — handover의 `assignee_id` 일괄 UPDATE만 허용).
- **PENDING에서만 편집 가능**: 헤더(`buyer_po_no`·`buyer_po_date`·`dest_market_code`·`assignee_id`)·라인 전체(D2). CONFIRMED·REJECTED는 편집 409 `NOT_PENDING`. **삭제 엔드포인트 없음**(폐기=REJECT, 행·스냅샷·사유 영구 보존 — §12.1 "거부 이력 보존"; B9의 전표 삭제 금지와 같은 결). soft delete 쓰기 경로 0(스캔).
- **수정 이력**: 별도 이력 테이블을 두지 않는다. 원본=`extracted_snapshot`, 최종=현재 컬럼(확정 후 동결) → 상세 응답이 `original`(스냅샷)과 현재 값을 함께 준다("원본 나란히 검토"). 편집 단위 diff 조회는 문면 근거가 없어 관찰 원장(트리거: 파서 개선 데이터로 편집 순서가 필요해질 때).
- 이벤트(B6 규약 준용): `order_intakes.order_intake.created`(착지) · `order_intakes.order_intake.status_changed`(확정·거부). payload 화이트리스트 `{intake_id, from_status, to_status, buyer_partner_id, assignee_id, source_kind, sales_order_id}` — **금액·사유 원문·메모 금지**. 편집 이벤트는 만들지 않는다(소비자 부재).

### (f) 테스트 배분
- **A**: 중복 PO 0건(동일 (partner,key) PENDING 2건째 거부·REJECTED 후 재등록 성공·soft delete 후 재유입은 신규·**양성 대조 INSERT 동반**) / 전이 표 총수 (2,4) 전건 파라미터화(허용 2 성공·미허용 4 거부·종결 탈출 0) / CHECK 위반 원시 INSERT(CONFIRMED인데 SO 없음·REJECTED 사유 공백·PENDING인데 decided_at·CSV인데 sha 없음·통화 소문자) 각 IntegrityError+양성 대조 / 라인 복합 FK 통화 불일치 거부 / 원본 스냅샷 불변(ORM 가드 예외)+수정 후에도 `original` 유지 / 거부 후 스냅샷·사유·행위자 보존.
- **K**: `table_policy` 분류·registry 등록·`ASSIGNMENT_TARGETS` 커버리지(라벨=테이블명)·금액 컬럼이 유니크 키에 없음·상태 대입 스캔(+자기검사)·불변 열 대입 AST 스캔·모든 쓰기 스키마 `extra='forbid'`·`status`/`buyer_po_no_key` 요청 필드 부재(신규 모듈용 스캔 복제 — `test_certification_machine.py:168-192`는 certifications만 스캔)·DELETE 메서드 부재·`pg_get_constraintdef`/`pg_get_indexdef` 정의문 대조.
- **J**: 같은 PO로 서로 다른 두 요청 동시 등록 → 정확히 1건(나머지 409, 500 아님).
- **변이 점검 대상**: 부분 유니크 술어의 `status='PENDING'` 제거 / `ck_..._decision_consistent`의 `sales_order_id` 항 삭제 / ORM 가드 제거 / 전이 표 한 행 추가.
- 기존 테스트 충돌 없음: `test_import_registry`·`test_requirements_contract`의 `auto_confirm` 문자열 스캔은 각각 `modules/imports/*.py`·`modules/requirements/*.py`만 본다(실측) — 신규 모듈은 스캔 밖. imports 소스는 **공개 승격 alias 외 무변경**(D2).

### (g) 소비·등재
소비: D2(착지)·D5(확정)·D6(보드). S6-1(AI PDF)·S5-4(자동 확정)는 `source_kind` CHECK 재정의+원본 문서 연결(`documents.owner_type`에 `'ORDER_INTAKE'` — 12자, 현 `String(13)` 이내라 확폭 불필요, `order_intakes.document_id` 추가형 nullable 컬럼)을 소비 세션 몫으로 **예약 값만 ADR에 명기**. 관찰 등재: 편집 단위 이력, 거래처 NULL 착지(AI 경로 소비 시 `DROP NOT NULL`).

### (h) 되돌리기 비용 — **중간**
마이그레이션 전(코드·문서 단계)은 낮음. 테이블 생성 후 시안 A로 전환하려면 데이터 이관+CHECK 3종 재정의가 필요해 그 방향이 더 비싸다. 소스·상태 값 추가는 CHECK 재정의 1건, 백링크 방향 변경은 컬럼 이동 1건.

---

## D2. 입구 2종·`register_intake` 단일 착지·CSV 표준 양식·원본 보관 (제안 D2 통합)

### (a) 목적·경계
S3-1의 입구는 **수동 폼(MANUAL)** 과 **CSV 표준 양식(CSV)** 두 개다. 두 입구가 같은 착지 함수만 부르게 해 후속 입구(AI·이메일·채널)가 그 함수만 호출하도록 만든다. AI 경로·원본 파일 보관은 S3-1 밖.

### (b) DESIGN 근거
§7.4(엑셀 대량 스테이징), §12.1(파일 파이프라인·해시 중복 감지), §12.2(엑셀 = 매핑 템플릿 재사용+BOM 처리, 수식 이스케이프 ADR-0027), ADR-08(AI 무예외 사람 확정)·GC-H1(AI 추출 자동 확정 불가), ADR-0041, §17.4(파일 해시 멱등).

### (c) 데이터 변경 — 테이블 신설 없음(D1 사용), 코드 계약

**착지 단일 통로**: `order_intake/service.py::register_intake(session, *, actor: AuthenticatedUser, source_kind: IntakeSourceKind, buyer_partner_id: int, header: IntakeHeaderIn, lines: Sequence[IntakeLineIn], extracted_snapshot: dict, source_sha256: str | None = None, source_group_key: str | None = None, original_filename: str | None = None) -> OrderIntake`. **status 인자 없음**(항상 PENDING). 라우터·파서가 `OrderIntake`를 직접 생성하지 않는다(AST 스캔). 내부 순서: 거래처 BUYER 재확인 → 통화·시장 선검사 → PO 키 산출·길이 검사 → 중복 사전 조회(PENDING 인테이크·**비취소 SO**) → 라인 해석(`resolve_buyer_items`, D4-①) → 금액·수량 상한 → INSERT(유니크 위반은 제약명으로 판별해 번역) → `order_intake.created` 이벤트. 트랜잭션·멱등 claim은 호출자(엔드포인트 서비스) 몫.

**수동 폼** `POST /api/v1/order-intakes`(JSON, `extra='forbid'`, Idempotency-Key 필수). 본문: 헤더(`buyer_partner_id`·`buyer_po_no`·`buyer_po_date`·`currency`·`dest_market_code`·`assignee_id?`)+라인(`buyer_item_code`·`quantity`·`unit_price`(사람 표기 문자열)·`requested_delivery_date`). **라인에 `sku_id` 입력 필드가 없다**(매핑 우회 금지 — 서버 해석만). 단가는 A의 `parse_minor_amount(value, currency, max_digits=15)`로 정수 최소단위 변환(자릿수 초과 거부·반올림 금지).

**CSV** `POST /api/v1/order-intakes/import-csv`(multipart, Idempotency-Key) + `GET /api/v1/order-intakes/template.csv`(헤더만 든 양식, 안내 행 없음 — 헤더 완전일치 파서라 안내 행이 오류가 되므로 안내는 화면 문구).
- 표준 양식(UTF-8-SIG, 헤더 1행 **완전 일치·순서 고정**): `바이어코드 | 바이어PO번호 | PO일자 | 통화 | 목적지시장코드 | 바이어품번 | 수량 | 단가 | 요청납기일`. **1행=1라인**, 앞 5열은 헤더 값(그룹 내 전 행 동일), 뒤 4열은 라인 값. 그룹 키 = `(바이어코드 strip, buyer_po_no_key)`(비연속 행도 한 그룹). 한 그룹 안 헤더 값 불일치는 **그 그룹 오류**(어느 행이 다른지 행번호). 배관 재사용: `decode_upload`(UTF-8-SIG→CP949)·`parse_csv`(헤더 완전일치·행 구조 오류 리포트·수식 이스케이프 역변환 `string_columns` 지정)는 이미 공개 — 그대로 호출. 업로드 크기·확장자 검사는 `imports/service.py`의 비공개 `_read_limited`·`_validate_extension`을 **동작 불변으로 공개 승격(alias 유지)** — 기존 F 그룹 테스트 무수정 통과가 조건. 상한: **20 MiB·50,000행(기존 `MAX_UPLOAD_BYTES`·`IMPORT_MAX_ROWS` 재사용)·파일당 그룹 200·그룹당 라인 200**(초과 422).
- 실무 무결성(추측 변환 금지 — fail-visible):
  ① 날짜 **ISO `YYYY-MM-DD`만**(엑셀 일련번호·`10/5/2026`·2자리 연도·`2026/10/05` 전부 행 오류, 메시지에 올바른 형식 예시·"셀 서식을 yyyy-mm-dd로") ② 수량 `^\d+(\.0+)?$`(콤마는 `^\d{1,3}(,\d{3})+$` 위치일 때만 제거 — 엑셀 CSV 저장이 표시 서식의 천단위 콤마를 내보낸다) 후 정수 ≥1 ③ 단가 소수 표기를 **통화 최소단위 자릿수 이내**에서만 정수 최소단위로(초과 자릿수 반올림 없이 오류, 통화기호·음수·0 거부, 천단위 규칙 ②와 동일) ④ 통화·시장 코드는 strip+**대문자화** 후 등록 여부 검사(코드 대소문자는 의미 없음) ⑤ **바이어코드·품번·PO번호는 원문 유지**(대소문자 변환 없음 — 없는 코드는 "혹시 `ABC`?" 힌트만, 자동 보정 금지) ⑥ 엑셀 지수표기 오염(`^\d+(\.\d+)?[eE][+-]?\d+$`)은 바이어코드·품번·PO번호 셀에서 행 오류(선행 0 손실은 검출 불가 — 화면 안내문에 "코드 열은 텍스트 서식") ⑦ `.xlsx`/`.xls` 미수용(요구사항에 xlsx 라이브러리 0건, 신규 의존 0): 확장자 `.csv`만 + 바이트 시그니처 `PK\x03\x04`·`D0CF11E0`이면 전용 메시지 "엑셀에서 'CSV UTF-8'로 저장" 422 ⑧ 수식 인젝션은 기존 역변환 통로만.
- **원자성 = 파일 전체 원자(모든 오류를 한 번에 리포트)**: 형식 오류·바이어 미등록/비BUYER·통화·시장 미등록·**중복 PO(PENDING 인테이크·비취소 SO)**·파일 중복이 하나라도 있으면 **아무것도 착지하지 않고** 422 `ORDER_INTAKE.FILE.INVALID_ROWS`(detail: `[{row_no, column, code, message_ko}]` 최대 200건+초과 건수). 통과 시 그룹별 `register_intake`를 **한 트랜잭션**에서 실행. **품번 미매핑은 오류가 아니다** — `sku_id NULL`로 착지하고 검토 화면이 등록을 유도한다(D4-①). 이유: 부분 착지 후 수정본 재업로드는 sha256이 달라 이미 착지된 오더가 PO 중복으로 충돌하고, 착지 안 된 것이 어느 것인지 운영자가 추적해야 한다.
- 파일 해시 멱등: sha256=업로드 원문 바이트. 사전 조회(PENDING 인테이크에 같은 sha 존재)로 친절한 오류(`ORDER_INTAKE.FILE.DUPLICATE` 409, 기존 인테이크 id 목록 detail) + DB 부분 유니크 (D1-b)가 최종 방어(IntegrityError→같은 코드). 확정·거부 후 재업로드는 허용되며 PO 중복 검사가 수렴시킨다("동일 임포트 재실행 변화 0" 계열).
- **원본 파일 보관 없음**(S3-1): CSV는 `extracted_snapshot`(원본 행 무손실)+`source_sha256`+`original_filename`으로 출처를 특정한다. `documents.owner_type` 확장·`document_id` 컬럼은 **소비자(AI PDF 경로, S6-1)가 없어 만들지 않는다**(ADR-0041 — 소비분 1종까지 확장 규율). 근거: 파일 1개=오더 N건이라 `unique_active(owner_type, owner_id, sha256)`에 맞추려면 인테이크마다 같은 파일을 복제 보관해야 한다. 예약 값(`'ORDER_INTAKE'` 12자 ≤ 13, `document_id` nullable 추가형)만 ADR에 명기.
- 표준 양식 개정 정책: 헤더 상수 1곳(`order_intake/csv_template.py`)+`parser_version`을 스냅샷에 기록. 열 추가 시 **직전 1세대 헤더 병행 수용**은 개정 세션 몫(관찰).

### (d) 자동화 4금 저촉 여부
저촉 없음. 파서는 결정적(AI·추측 없음)이고 착지는 PENDING까지, 확정은 사람(D5). AI 유래 사람 확정 필수는 **구조로 보장**: `confirm_intake`가 `actor: AuthenticatedUser`(사람 세션)·기본값 없는 키워드 전용 `idempotency_key`를 요구하고, 착지 함수 어디에도 CONFIRMED 대입·`confirm_*` 호출이 없다(GC-H1 — "신뢰도 임계값" 류 옵션이 들어갈 자리가 시그니처에 없다).

### (e) 상태·전이·불변
착지 직후 항상 PENDING. CSV 업로드 1회의 결과는 전부 착지 또는 전무. `extracted_snapshot`은 착지 시 1회 기록(D1 불변).

### (f) 테스트 배분
- **G**(AI·보안 — 파일 해시 멱등·DB 직행 불가): 동일 파일 재업로드 409+인테이크 수 불변 / 확정·거부 후 재업로드는 PO 중복으로 수렴 / 착지 함수가 status를 받지 않음(`inspect.signature`)·CONFIRMED로 직접 생성 불가 / 라우터·파서의 `OrderIntake` 직접 생성 0(AST, +자기검사).
- **F**(엑셀·연동): 양식 왕복(다운로드 헤더 상수=업로드 헤더 상수) / 헤더 불일치 422 / 그룹핑(3 PO×여러 라인→3건, 비연속 행) / 그룹 내 헤더 불일치 / 날짜 `10/5/2026`·일련번호 `46000`·`2026-13-01`·`2026/10/05` 거부 / 수량 `1.5` 거부·`10.0`·`1,000` 허용·`1,00` 거부 / 단가 KRW `1234.5` 거부·USD `12.345` 거부·`12.30`=1230·`1,234.50` 허용·`0`·`-1`·`$5` 거부 / 코드 지수표기 거부 / 통화 소문자 허용(대문자화) / 시장 미등록 / **바이어코드 대소문자 다르면 오류+힌트**(자동 매칭 없음) / 수식 셀 `=CMD()` 역변환 / CP949 저장본 / `.xlsx` 바이트(확장자 위장 포함) 422 / 51,000행·201그룹·그룹 201라인 경계(경계값 통과·+1 거부) / **전체 원자성**(오류 1행이면 착지 0건, 100그룹 중 마지막 그룹 오류) / 품번 미매핑은 착지 / 익명 합성 픽스처만.
- **J**: 같은 키 재전송=최초 결과 재생·같은 키+다른 파일 409 / 같은 파일 동시 2업로드 → 1건 성공·1건 409.
- **K**: 신규 쓰기 스키마 `extra='forbid'`, `sku_id`·`buyer_po_no_key` 요청 필드 부재(본문에 실으면 422), Idempotency-Key 결여 400(수제 테스트), 엔드포인트×역할 행렬.
- **변이 점검 대상**: 날짜 모호 형식 허용 추가 / 단가 반올림 추가 / 그룹 키에서 partner 제거 / 원자성을 그룹별 커밋으로 변경 / 지수표기 검사 제거.

### (g) 소비·등재
D4-①(해석), D5(확정). A의 `parse_minor_amount`·`LineIn` 재사용. 재판정 트리거 등재: `.xlsx` 수용 요구 / 무상 라인 인테이크 수용 / 양식 개정.

### (h) 되돌리기 비용 — **낮음**
입구는 착지 함수 위 어댑터. 양식 열 추가=헤더 상수+파서 1곳(공지 필요). `.xlsx` 수용은 어댑터 추가(가산). 원자성→부분 성공 전환은 서비스 1곳(단, sha 충돌 문제를 그때 재판정).

---

## D3. 게이트 공통 모듈 `gates` — 결과값·해소 방식·평가 시점·저장·override·정책 (제안 D3 통합)

### (a) 목적·경계
게이트 6종+PI 입금을 **한 모델**로 표현하고 S5-1 채널 리스팅 게이트(GC-C2)가 그대로 재사용하게 한다. 원천 데이터·평가 규칙은 D4, 확정 TX 결합은 D5. 여신 로직·승인 소비는 E·C.

### (b) DESIGN 근거
§7.4(게이트 6종·"미매핑 시 등록 유도"), §7.3(PI 게이트 경고/차단 설정, 선수금 T/T만), §2 승인(여신 초과 수주만 승인 대상), GC-C2(차단 기본+**통제된 override**: 사유·권한), §17.2(확정 시 잠금 하 재평가), §17.5(불변 테이블 확장), fail-closed·fail-visible 계약(평가 불능≠통과), ADR-11(허용치는 데이터).

### (c) 데이터 변경 — 신규 모듈 `app/modules/gates/`(도메인 무임포트), 테이블 3개

**타입**(`gates/types.py`, StrEnum·frozen dataclass):
- `GateLevel`: `PASS`·`WARN`·`BLOCK`·`UNKNOWN`. **UNKNOWN(평가 불능: 원천 부재·통화 불일치·설정 0행·예외)은 통과와 분리 표기, 효과는 BLOCK과 동일(fail-closed)**.
- `GateResolution`: `NONE`(해소 수단 없음 — 데이터를 고쳐야 함) · `OVERRIDE`(권한자+사유로 통과) · `APPROVAL`(승인 소비로 해소). PASS·WARN에는 해소 불요.
- `GateOutcome`: `gate_code, line_id | None, level, resolution, reason_code, message_ko, basis: dict[str, int|str|None]`(판정 입력값 — 판매 단가·기준가·허용치·수량·MOQ·준비 상태 요약만, **원가·마진·매입가·내부 예외문자열 금지**), `override_roles: tuple[RoleCode, ...]`. `basis_hash` = `sha256(canonical JSON{gate_code, line_id, level, reason_code, basis})`.
- `GateSubject`(도메인 무의존 dataclass): `kind('INTAKE'|'SALES_ORDER'), id, buyer_partner_id, currency, dest_market_code, buyer_po_no_key | None, payment_type | None, pi_id | None, lines: tuple[GateLine(line_id, line_no, sku_id | None, buyer_item_code | None, quantity, unit_price_amount, list_price_amount | None, is_free)], total_amount`. 인테이크·SO가 자기 데이터를 어댑트해 **같은 평가기**를 부른다.
- **게이트 명세 단일 출처** `gates/policy.py::GATE_SPECS`(게이트별 평가 시점·결과→해소·override 허용 역할) — §D4 표가 정본, 코드는 이 상수 하나.

**평가기 등록 구조**(순환 방지): `gates`는 `order_intake`·`sales_orders`·`trade_chain`·`channels`를 **임포트하지 않는다**(방향 테스트). `gates.registry.register(gate_code, evaluator)`(`evaluator(session, subject, phase) -> list[GateOutcome]`), `evaluate_all(session, subject, phase, *, registry)`가 등록부를 순회. **미등록 평가기·평가 중 예외는 UNKNOWN**(`EVALUATOR_NOT_REGISTERED`/`EVALUATION_ERROR`, resolution NONE; 예외는 SAVEPOINT 안에서 잡아 세션 오염 방지, **`lock_timeout`·교착(OperationalError 55P03/40P01)은 삼키지 않고 전파**(B8 `LOCK.BUSY` 409), 로그·응답에는 예외 클래스명만). 구체 평가기 위치: `trade_chain/gate_evaluators.py`(L2 — 매핑·단가·MOQ·준비도·SO측 중복 PO·PI 래퍼)와 `order_intake/gates.py`(인테이크측 중복 PO — PENDING 인테이크 조회가 필요). S5-1은 자체 `GateSubject`+평가기를 등록하고 override·평가 테이블만 재사용.

**확정 가능 판정 단일 함수** `gates.service.clearance(outcomes, effective_overrides, approval_available) -> Clearance{cleared, unresolved, used_overrides, needs_approval}`: PASS·WARN=통과 / BLOCK·UNKNOWN + `NONE`=미해소 / + `OVERRIDE`=`(gate_code, line_id, basis_hash)`가 유효 GRANT일 때만 해소 / + `APPROVAL`=`approval_available`일 때만 해소. **"확정 가능" 판정 로직의 복제 금지**(스캔 — 문자열 `'PASS'` 직접 비교로 통과를 판정하는 코드 0건).

**`gate_policies`** (설정 데이터 — Pk·Timestamp·SoftDelete·Version·Actor): `gate_code VARCHAR(20) NOT NULL CHECK IN ('PRICE_DEVIATION','PI_DEPOSIT')`, `config JSONB NOT NULL`. `unique_active(gate_policies, gate_code)`. 게이트별 pydantic 스키마(`extra='forbid'`): `PRICE_DEVIATION {tolerance_bps: int 0..100000}` / `PI_DEPOSIT {mode: 'WARN'|'BLOCK'}`. 형이 흐린 행은 **기본값 폴백이 아니라 UNKNOWN**('POLICY_INVALID')+ PUT 422. API는 퍼센트 문자열(`"5.00"`, 소수 2자리, A5 `advance_pct` 선례)↔저장 bps 정수(Float 금지). **0행 = UNKNOWN 'POLICY_NOT_SET'**(fail-visible). 시드: **마이그레이션 시드 금지(함정 ⑩)** → 앱 경로 CLI `seed-gate-policies`(멱등·기존 행 무덮어쓰기, 기본 `PRICE_DEVIATION 500bps`·`PI_DEPOSIT WARN`)+ADMIN `PUT /api/v1/gate-policies/{gate_code}`(`expected_version`, 없으면 생성). PI 기본 WARN의 이유: S3-1에는 입금 기록 경로가 없어(S3-3 payments) BLOCK 기본이면 선수금 T/T SO가 전건 ADMIN override를 요구한다 — 오너가 S3-3 이후 BLOCK으로 전환. 거래처·품목군별 허용치는 문면 근거가 없어 만들지 않는다(전역 1값, 관찰).

**`gate_evaluations`** (§17.5 확장 **불변** — Pk+Base만, `IMMUTABLE_TABLES`+`revoke_mutations`): `subject_type VARCHAR(20) CHECK IN ('SALES_ORDER')`(S5-1이 값 추가) / `subject_id BIGINT` / `outcome VARCHAR(9) CHECK IN ('CONFIRMED','BLOCKED')` / `results JSONB NOT NULL`(비PASS 결과 목록 `[{gate_code, line_id, level, resolution, reason_code, basis_hash, basis}]` + `passed_gates:[gate_code]` + 사용한 override id·승인 ref) / `content_rev INT NOT NULL`(평가 시점 SO `content_rev` — 승인 결속 토큰과 맞물림) / `evaluated_by_id BIGINT NOT NULL FK users` / `evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()`. **`UNIQUE(subject_type, subject_id) WHERE outcome='CONFIRMED'`** — SO당 확정 증거 스냅샷 정확히 1행("확정 시점 값" 재현, SO 열 추가 없이 A·B 분류표 무접촉). 인덱스 `(subject_type, subject_id)`. 기록 시점은 **확정 시도(성공·차단)뿐**, GET 조회는 저장하지 않는다(조회 부작용 금지). 컬럼·테이블명에 금지어 없음(`results` JSON 키도 `prep_state` 등으로 — `cell_color` 회피).

**`gate_overrides`** (불변 — Pk+Base만): `subject_type CHECK IN ('SALES_ORDER')`, `subject_id BIGINT`, `line_id BIGINT NULL`(SO 라인, 폴리모픽 FK 없음), `gate_code VARCHAR(20) CHECK IN ('PRICE_DEVIATION','MOQ','MARKET_READINESS','PI_DEPOSIT')`(**override 가능 게이트만 열거 — ITEM_MAPPING·DUPLICATE_PO·CREDIT은 DB가 우회 불가를 강제**), `action VARCHAR(6) CHECK IN ('GRANT','REVOKE')`, `result_at_grant VARCHAR(7) CHECK IN ('BLOCK','UNKNOWN')`, `reason TEXT NOT NULL CHECK length(btrim(reason)) BETWEEN 5 AND 500`, `basis JSONB NOT NULL`, `basis_hash CHAR(64) CHECK ~ '^[0-9a-f]{64}$'`, `granted_by_id BIGINT NOT NULL FK users`, `authorized_role VARCHAR(10) CHECK IN (5역할)`(행위자가 보유한 것 중 게이트 허용 역할), `created_at`. 인덱스 `(subject_type, subject_id, gate_code)`. **유효성 = `(subject, gate_code, line_id, basis_hash)`별 최신 행(max id)이 GRANT** — REVOKE로 철회(행 삭제 없음). 판정 입력이 바뀌면 해시가 달라져 기존 override는 **자동 무효**(재승인 필요).

**마이그레이션 성격**: 신규 `create_table` 3건+`revoke_mutations` 2회(다운그레이드는 `drop_table`). `gate_policies`는 시드 없음.

### (d) 자동화 4금 저촉 여부
- **MARKET_READINESS 결과 = 계산값 표시이며 법적 판정이 아니다**: 응답·UI·메시지 어디에도 '판매 가능·적합·승인·허가' 워딩 금지, `readiness.rules.SCOPE_NOTE`+고정 문구 "준비 상태 안내(법적 판정 아님)"를 응답 `note`에 동봉(문구 스캔 테스트). override는 사람 결정(ADMIN+사유).
- **게이트 통과는 어떤 경우에도 사람의 확정 클릭을 대신하지 않는다**(자동 확정 경로 부재 — D5).
- override 부여는 사람 행위만(자동 부여 경로 0, 벌크도 부여하지 않음).

### (e) 상태·전이·불변
게이트 결과는 상태가 아니라 **계산값**(저장 캐시 없음). 불변: `gate_evaluations`·`gate_overrides`는 INSERT/SELECT만(REVOKE), override 철회는 REVOKE 행 추가. 평가 결과는 입력이 바뀌면 달라지는 순수 함수라 낡은 판정이 통과로 오독될 저장 경로가 없다.

**해소 권한(서버측 `gates.policy.OVERRIDE_ROLES`, 라우터가 아닌 서비스가 검증 — 이중 방어)**: `PRICE_DEVIATION`·`MOQ` = TRADE·ADMIN / `MARKET_READINESS`·`PI_DEPOSIT` = **ADMIN** / 그 외 게이트는 override 자체가 불가. LOGISTICS·CERT·VIEWER는 어느 override도 403. SoD는 C의 승인 몫이며 override에 별도 SoD를 두지 않는다(문면 근거 없음).

**override 흐름(별도 액션)**: `POST /api/v1/sales-orders/{id}/gate-overrides`(Idempotency-Key) 본문 `{gate_code, line_id?, basis_hash, reason}`. 서비스: SO 행 잠금 → **서버가 지금 평가** → 해당 (gate, line) 결과가 BLOCK·UNKNOWN+`OVERRIDE` 해소 대상이 아니면 422 `GATES.OVERRIDE.NOT_APPLICABLE`(PASS·WARN·NONE·APPROVAL 대상에 부여 시도 — 조용한 통과 방지) / 서버 해시≠요청 `basis_hash`면 409 `GATES.OVERRIDE.STALE`(사람이 본 판정이 낡음 — 다시 확인) / 역할 불허 403 `GATES.OVERRIDE.NOT_ALLOWED` / 사유 5자 미만 422. 성공 시 GRANT 행 INSERT. 철회 `POST .../gate-overrides/revoke`(같은 본문, 행위자 본인 또는 ADMIN). **override 없이 확정 요청에 첨부하는 방식은 없다**(TRADE가 확정, ADMIN이 override 하는 역할 분리 유지).

### (f) 테스트 배분
- **A/H**: 게이트 7종 × 도달 가능한 결과값 전수 파라미터화 + **메타 테스트**(`GATE_SPECS`가 선언한 (gate, level, resolution) 조합마다 최소 1개 테스트가 있는지 — 공회전 방지) / UNKNOWN이 PASS와 다른 표기·확정 차단 / 미등록 평가기→UNKNOWN / 평가기 예외 주입(monkeypatch RuntimeError)→UNKNOWN(500·PASS 아님)+세션 사용 가능 / `lock_timeout` 유도→409(UNKNOWN으로 삼키지 않음).
- **H**(승인 우회 차단): override 사유 4자 422·5자 통과 / 비허용 역할 403(TRADE의 MARKET_READINESS override, LOGISTICS·CERT·VIEWER 전부) / ITEM_MAPPING·DUPLICATE_PO·CREDIT override 시도 422+**DB 원시 INSERT는 CHECK 위반** / `basis_hash` 낡음 409 / GRANT 후 단가 1원 변경→해시 불일치로 다시 차단 / REVOKE 후 차단 / ADMIN도 CREDIT을 override로 통과 불가.
- **J**: 실패도 커밋(BLOCKED 행 존재·SO 상태 불변) / 동시 override 2건.
- **K**: `gate_evaluations`·`gate_overrides` `IMMUTABLE_TABLES` 등재+UPDATE/DELETE/TRUNCATE 42501 / 테이블·컬럼 금지어 스캔(`test_no_table_or_column_stores_an_aggregate`) 통과 / **`gates`가 intake·sales_orders·trade_chain·channels를 임포트하지 않음** / `clearance` 밖의 통과 판정 복제 0 / `gate_policies` 형 오류 422·0행 UNKNOWN / MARKET_READINESS 워딩 금지·SCOPE_NOTE 동봉 스캔 / 평가 코드에 `PURCHASE`·`purchase_price` 참조 0.
- **변이 점검 대상**: UNKNOWN→PASS 매핑 한 줄 / `clearance`의 해시 비교 제거 / override 역할 표에 TRADE를 MARKET_READINESS에 추가 / BLOCKED 기록을 롤백되는 위치로 이동 / GRAY 분기 삭제.

### (g) 소비·등재
소비: D4(평가기)·D5(확정)·D6(벌크 결과). S5-1: `subject_type` CHECK에 `'CHANNEL_LISTING'`, `gate_code` CHECK 확장(소비분 한정 재정의 — ADR-11·0041 선례). 재판정 트리거 등재: MARKET_READINESS override 권한(CERT 추가 여부)·PI 모드 기본값(S3-3 이후 BLOCK)·거래처별 허용치.

### (h) 되돌리기 비용 — **중간**
`resolution`/역할 배정 변경은 상수 1곳+테스트(낮음). 테이블은 S3-2 이후 소비가 시작되면 이관이 필요해 **첫 PR 전 확정**. 결과 저장 방식을 SO 열로 옮기는 것은 A·B 분류표 재작업이라 비싸다(그래서 채택하지 않음).

---

## D4. 게이트 원천·평가 규칙 — 품번·단가·준비도·MOQ·중복 PO·여신·PI (제안 D4 통합)

### (a) 목적·경계
각 게이트의 **데이터 원천·판정식·결과 배정**을 확정한다. 마스터 규칙을 게이트가 재구현하지 않는다(정의 이중화 방지 — s2-3 교훈).

### (b) DESIGN 근거
§7.4 게이트 명단, §4.6 보강(매핑 유일키 (거래처, 품번)), ADR-0017("그때 단가"·단가 부재는 오류), §5.3(준비도 계산값), GC-C2, ADR-0018·0024(원가 마스킹), §7.3.

### (c) 데이터 변경 — 원천별 (스키마 신규는 D3 3테이블뿐, 마스터 변경은 A 소유)

**게이트 명세표(정본 — `GATE_SPECS`)** — 시점: I=인테이크 검토(실시간 계산·**정보**), C=SO 확정(잠금 하 재평가·**권위**). 인테이크 확정의 하드 조건은 ITEM_MAPPING·DUPLICATE_PO·입력 완결성뿐이다(D5).

| 게이트 | 시점 | 결과 → (level / resolution) |
|---|---|---|
| ITEM_MAPPING | I·C | 미매핑·거래처 BUYER 상실·SKU 삭제=`BLOCK/NONE` / SKU DISCONTINUED = **I: `WARN`**(접수는 허용 — A11-2), **C: `BLOCK/NONE`**(마스터 상태를 되돌리는 가시적 조치가 선행, A11-2) / 해석 예외=`UNKNOWN/NONE` |
| DUPLICATE_PO | I·C | 다른 PENDING 인테이크·비취소 SO가 같은 (거래처, 키)=`BLOCK/NONE`(override 불가 — 데이터 무결성) / SO의 `buyer_po_no_key` NULL(C만)=`WARN` 'PO_NO_NOT_GIVEN'(QT·PI 유래 SO 정상 — 중복 확인 불가를 기록으로 남김) / 예외=`UNKNOWN/NONE` |
| PRICE_DEVIATION | I·C | 라인별. 허용치 초과=`BLOCK/OVERRIDE(TRADE,ADMIN)` / 기준가 없음·통화 불일치·허용치 0행·형 오류·기준가≤0=`UNKNOWN/OVERRIDE(TRADE,ADMIN)` / 무상(`is_free`) 라인=`WARN` 'FREE_LINE' / 이내=PASS |
| CREDIT | C | 한도 초과=`BLOCK/APPROVAL` / 평가 불능(환율 부재 통화 상이 등)=`UNKNOWN/APPROVAL` / 한도 NULL(관리 안 함)=PASS(E) |
| MARKET_READINESS | I·C | 셀 RED=`BLOCK/OVERRIDE(ADMIN)` / **GRAY(필수 요건 0건)=`UNKNOWN/OVERRIDE(ADMIN)`(통과로 읽지 않음)** / YELLOW=`WARN` / GREEN=PASS / 시장·SKU 소멸·계산 예외=`UNKNOWN/OVERRIDE(ADMIN)` |
| MOQ | I·C | 같은 SKU 수량 합 < `skus.moq`=`BLOCK/OVERRIDE(TRADE,ADMIN)` / `moq` NULL=**PASS**(`evidence moq_unset=true` 안내 — "정책 없음"은 평가 실패가 아니다) / 이내=PASS |
| PI_DEPOSIT | C | `payment_type ≠ 'TT_ADVANCE'`=PASS('INACTIVE') / 모드 WARN·미입금=`WARN` / 모드 BLOCK·미입금=`BLOCK/OVERRIDE(ADMIN)` / PI 미연결·상태 판독 불가·모드 0행=`UNKNOWN/OVERRIDE(ADMIN)` |

원칙: **BLOCK/UNKNOWN은 "사유를 남긴 사람 결정 또는 데이터 정정"이 있어야 진행, WARN은 진행하되 확정 증거 스냅샷에 기록**.

**① 바이어 품번 → SKU** (`partners.service.resolve_buyer_items(session, *, partner_id: int, codes: Sequence[str]) -> dict[str, ItemResolution]` 신설, 읽기 전용):
- 정규화 = **`strip()` 후 정확 일치**(등록 규칙과 동일 — §0-1 #4). 쿼리 **최대 2개(라인 수와 무관, N+1 없음)**: Q1 `customer_item_codes ⋈ skus`(활성 매핑, `buyer_item_code IN (:codes)`, 삭제된 SKU 포함해 상태 판독) / Q2 힌트(Q1에서 못 찾은 코드만, `lower(buyer_item_code) IN (:lowered)` — **힌트 표시 전용, 자동 매칭 금지**).
- `ItemResolution{state: MAPPED | UNMAPPED | SKU_DELETED | SKU_DISCONTINUED, sku_id, sku_code, sku_name_ko, hint_code}`. 다른 바이어의 같은 품번은 쿼리 조건(`partner_id`)으로 격리(IDOR형 교차 매칭 불가). 함수 진입에서 거래처 BUYER 유형 재확인(`require_partner_of_type`, 생성 후 유형 해제 창 방어).
- **등록 유도 UX**: 미매핑 라인 옆 '품번 등록' → SKU 검색형 선택(D6-⑤) → **기존** `POST /partners/{id}/item-codes` 호출(신규 엔드포인트·권한 신설 없음, `CAN_REGISTER=TRADE`+ADMIN — 권한 없으면 "거래처·품번 마스터 담당자에게 요청" 안내만) → 성공 후 `POST /order-intakes/{id}/resolve`로 **전 라인 재해석**(변경 있으면 헤더 version+1). SKU 코드·바코드 대체 자동 매칭 없음(추측 금지). "매핑 없이 SKU만 수동 지정"(fit의 `sku_source='MANUAL'`)은 매핑 게이트 우회라 **채택하지 않는다**(잘못 등록된 매핑의 정정 API 부재는 이월 부채로 유지).
- 저장본 `sku_id`는 검토자가 본 값이고 `confirm_intake`가 전 라인 재해석 후 **다르면 409 `STALE_MAPPING`**(검토 후 매핑이 바뀜 — 재검토 요구, §17.2 "확인→기록").

**② 단가 편차**:
- 허용치 `gate_policies(PRICE_DEVIATION).config.tolerance_bps`(전역 1값, 대칭 ±).
- **기준가**: 인테이크 단계 = `catalog.pricing.prices_at(session, sku_ids, currency, on=today_kst())`(A 신설, `price_type='SALES'`) / **SO 확정 단계 = SO 라인 `list_price_amount`**(A9-1: 라인 생성 시점 마스터 판가 스냅샷 — 확정 시 mutable한 `sku_prices`를 다시 읽지 않고 "당시 기준가 대비"를 재현). NULL·미존재 = UNKNOWN('NO_REFERENCE_PRICE').
- **통화 불일치는 환산하지 않는다**(환율 축 부재 — ADR-0048 "환산은 추측이다"): 해당 통화 판매가가 없으면 UNKNOWN. 사용자는 그 통화의 마스터 판가를 등록하거나 사유 있는 override.
- **판정식은 정수 교차곱셈**: `abs(unit − ref) × 10000 > tolerance_bps × ref` 이면 초과(**정확히 허용치는 통과**, 나눗셈 절사 오차 없음, Float 없음). 표시용 `deviation_bps = abs(unit − ref) × 10000 // ref`(정수)와 서버 제공 `deviation_pct` 문자열(프런트 산술 0 — 화면이 계산하지 않는다). 방향(고가·저가) 모두 대상. `ref ≤ 0`=UNKNOWN 'REFERENCE_INVALID'.
- **`PURCHASE` 유형·매입가는 어떤 경로에서도 조회하지 않는다**(원가 마스킹 계약 — 소스 스캔 테스트).

**③ 시장 준비도**: `readiness/service.py`에 **읽기 전용 공개 함수** `cells_for(session, sku_ids: Sequence[int], market_code: str, *, base_date: date | None = None) -> dict[int, MatrixCellView]` 신설 — 활성 SKU `select` → `_load_facts` → `_build_row(markets=[해당 시장 1개])` 재사용(세트 롤업·제조사 미지정 RED·초안 규칙 재구현 0, 계산 규칙 1곳 — §5.3). 시장 원천 = 인테이크 `dest_market_code` → SO 헤더 `dest_market_code`(A). 매핑: GREEN→PASS / YELLOW→WARN / RED→BLOCK / GRAY→**UNKNOWN**. 소멸한 시장·SKU는 UNKNOWN. readiness 모듈 읽기 전용 계약 유지(쓰기 호출·outbox·audit·notifications 임포트 0 — `test_readiness_contract` 무변경 통과). 게이트 `basis`는 `prep_state`(색 값)·`unmet_count`·`market_code`·`sku_id`만(금지어 회피), 응답에 `SCOPE_NOTE` 동봉.

**④ MOQ**: A의 `skus.moq`(EA, 세트는 세트 단위 — 구성품 롤업 없음) 소비. 한 전표 안 같은 SKU **유상 라인 수량 합**과 비교(A11-5로 SKU당 유상 1줄이지만 인테이크 단계는 합산). 단위 혼동 방지로 `basis.unit='EA'` 기록.

**⑤ 중복 PO**: 키 = A의 `normalize_buyer_po_no`(NFKC→대문자→모든 공백 제거, 구두점 보존 — 'PO-1'≠'PO1'; **제로폭(Cf) 제거는 A에 요구**). 저장소: 인테이크 (D1-a, PENDING 한정)·SO (A2: `(buyer_partner_id, buyer_po_no_key) WHERE deleted_at IS NULL AND buyer_po_no_key IS NOT NULL AND status <> 'CANCELLED'` — 취소된 SO의 PO번호 재사용 허용, ADR-05 "정정=취소+신규"). REJECTED 인테이크도 점유하지 않는다. **바이어 PO 개정판(Rev.2) 전용 개념·컬럼 없음** — 유일 경로는 "기존 SO 취소(또는 인테이크 거부) 후 재등록"(화면 안내에 명시, 개정 개념 신설은 관찰 원장). **등록(착지) 시점에 이미 비취소 SO·PENDING 인테이크가 키를 점유하면 409 `ORDER_INTAKE.PO.DUPLICATE`**(detail: 점유 문서 번호·상태, 금액 미기재)이고 CSV는 파일 전체 거부(D2). SO 유니크 위반은 `IntegrityError`를 **제약명으로 판별**해 409 번역(500 금지). SO 확정 시점의 평가는 방어적 재조회(A의 부분 유니크가 실제 보증)+키 NULL 경고.

**⑥ 여신**: E가 `evaluate_credit(session, *, buyer_partner_id, currency, additional_exposure_amount, exclude_sales_order_id) -> GateOutcome(gate_code='CREDIT', resolution=APPROVAL)`를 제공(E 산출 필수 — DoD "여신 초과 → 승인 게이트"). D는 ① SO 확정 TX에서 **거래처 행 잠금 이후** 호출 ② 결과 resolution이 APPROVAL이면 승인 경로로만 해소 ③ 함수 미등록·예외 = UNKNOWN(D3 등록부 규칙) — **fail-closed 대체 구현은 두지 않는다**(E 산출 부재는 배포 결함이며 UNKNOWN이 SO 확정을 막아 드러낸다). 사전 조회(`GET .../gates`)의 여신은 **잠금 없는 참고값**으로 표기(`authoritative=false`).

**⑦ PI 입금**: `payment_type='TT_ADVANCE'`일 때만 활성(§7.3) — 판정은 D 래퍼가 하고 E의 `evaluate_pi_deposit`는 활성일 때만 호출. 입금 원천은 S3-1 시점 PI.status(B의 `converge_payment_status` 수렴 결과) 판독뿐(E 가정).

### (d) 자동화 4금 저촉 여부
저촉 없음. 게이트는 계산·표시이며 판정 주체는 사람(override·승인). 준비도 게이트는 D3-(d)의 워딩 규칙 적용.

### (e) 상태·전이·불변
게이트 값 저장 없음(계산). 확정 시도 스냅샷만 D3.

### (f) 테스트 배분
- **A**: `resolve_buyer_items` — strip 정확 일치·대소문자 다름→UNMAPPED+힌트(자동 매칭 없음)·삭제 SKU→SKU_DELETED·DISCONTINUED·BUYER 유형 해제·다른 바이어의 같은 품번 격리·**라인 100개 쿼리 수가 라인 1개와 동일**(SQLAlchemy 이벤트 카운터) / 매핑 변경 후 `STALE_MAPPING` / 단가: **허용치 정확히=통과·1bp 초과=BLOCK** 양수·음수 방향·기준가 없음·통화 불일치·`ref=0`·허용치 0행·형 오류→UNKNOWN(500 없음)·정수만(Float 스캔)·`PURCHASE` 조회 0(스캔)·`prices_at`≡`price_at`(A 소유 동치 테스트 재확인) / 준비도: **`cells_for` 셀 == `get_matrix` 같은 SKU·시장 셀**(세트 롤업 포함 동치)·GRAY→UNKNOWN·`test_readiness_contract` 무변경 / MOQ 경계(=MOQ는 PASS)·NULL PASS+안내·미만 BLOCK / 중복 PO: 공백·전각·대소문자 변형 동일 키·구두점 다르면 별개·취소 SO 후 재사용·REJECTED 후 재사용·SO 점유 시 착지 409 / PI: 비선수금 PASS·모드별 결과.
- **H**: 여신 함수 부재·예외 → UNKNOWN 차단(모의) / 승인 없이 여신 초과 SO 확정 불가(ADMIN 포함).
- **K**: 게이트 평가 모듈 소스에 원가 유형 참조 0 / 게이트 응답·`results` JSON에 `is_sensitive_key` 0.
- **변이 점검 대상**: 교차곱 `>`를 `>=`로 / 준비도 GRAY를 PASS로 / MOQ NULL을 UNKNOWN으로 / 중복 조회의 취소 제외 조건 반전 / 매핑 정확 일치를 대소문자 무시로.

### (g) 소비·등재
A: `skus.moq`·`prices_at`·`list_price_amount`·`normalize_buyer_po_no`·`buyer_po_date`. E: `evaluate_credit`·`evaluate_pi_deposit`. C: 승인. 관찰 등재: 바이어별 단가표·바이어별 MOQ·품번 매핑 정정 API·**마스터 CSV 왕복 어댑터 skus의 `moq` 열(A 소유)**·분할 납기.

### (h) 되돌리기 비용 — **낮음~중간**
평가 규칙·결과 배정은 명세표 상수+테스트. 정규화 규칙 변경은 `buyer_po_no_key` 재계산 마이그레이션(데이터 초기라 저렴). 기준가를 QT 참조로 바꾸려면 인테이크에 QT 링크 열이 필요(중간).

---

## D5. 확정 흐름 — `confirm_intake`·`confirm_sales_order` TX 계약·실패도 커밋·후속 체인·자동 확정 부재 (제안 D5 통합)

### (a) 목적·경계
사람 1클릭 두 단계의 트랜잭션 계약을 고정한다. 인테이크 확정=**SO(접수) 생성**(D 소유), SO 확정=**게이트 재평가+승인·override 소비+동결 전이**(오케스트레이션 D, 전이·동결·번호는 B, 승인 C, 여신·입금 E). §7.4 후속 체인(할당/백오더 분기→선적 계획 초안→서류 태스크)의 S3-1 완결 몫은 **SO 확정 이벤트 발행까지**.

### (b) DESIGN 근거
§7.2·§7.4·ADR-09, §17.1(1 업무 동작=1 TX·외부 호출은 아웃박스), §17.2(확인→기록 잠금·확정 시 재평가), §17.3(채번 행 잠금), §17.4(확정 요청 idempotency key), ADR-05, §15 4금, GC-H1, WBS DoD("여신 초과 → 승인 게이트"·"확정 후 단가·환율 불변"), B8·B9(우회 경로 차단표).

### (c) 데이터 변경 — 스키마 없음(D1·D3 테이블 사용), 코드 계약

**① `confirm_intake`**(`order_intake/service.py`): `confirm_intake(*, actor: AuthenticatedUser, idempotency_key: str, intake_id: int, expected_version: int) -> tuple[int, dict]` — **`idempotency_key`는 기본값 없는 키워드 전용**. 하나의 TX(자체 `unit_of_work`):
1. `idempotency.claim(endpoint="POST /api/v1/order-intakes/confirm", request_body={intake_id, expected_version})` — 재생이면 최초 결과 반환.
2. 인테이크 `SELECT … FOR UPDATE`(잠금 순서 ⓪ — 인테이크를 먼저 잠그고 이후 A13 규약 순서 유지, SO 쪽에서 인테이크를 잠그는 경로는 없다). 미존재·삭제 404 / `status ≠ PENDING` 409 `NOT_PENDING` / version 불일치 409.
3. **입력 완결성 검증**(게이트 아님 — 422): 거래처 BUYER·활성 재확인, 시장 활성, 통화, 라인 ≥1, **같은 SKU 유상 라인 중복 → 422 `ORDER_INTAKE.LINE.DUPLICATE_SKU`(라인 번호 목록)**, 금액 상한.
4. **하드 게이트 재평가(잠금 하)**: 전 라인 재해석 → 미매핑·SKU 삭제 422 `ORDER_INTAKE.LINE.UNMAPPED_ITEMS` / 저장본과 해석 불일치 409 `STALE_MAPPING` / DUPLICATE_PO(비취소 SO·타 PENDING) 409. **PRICE·MOQ·MARKET·CREDIT·PI는 접수를 막지 않는다**(§7.2 접수는 미확정 — 확정 시점 판정, GC-C2 문면). DISCONTINUED는 접수 허용(WARN, A11-2).
5. `sales_orders.service.create_received_sales_order(session, *, actor, draft: SalesOrderDraft) -> CreatedDoc(id, doc_number)` 호출 — **A·B 소유 계약**(같은 세션·같은 TX, 헤더+라인 생성, 스냅샷 복사, `record_birth`, `issue_document_number`를 **TX 마지막**에). 복사 매핑: 헤더 `buyer_partner_id·buyer_po_no·buyer_po_date·currency·dest_market_code·assignee_id`(+`doc_date=today_kst()`, `qt_id=pi_id=NULL`, 결제조건·Incoterms·환율=NULL — SO 편집에서 입력), 라인 `sku_id·buyer_item_code·quantity·unit_price_amount·requested_delivery_date`(+`price_basis='BUYER_PO'`, `list_price_amount`=A의 `prices_at`으로 그 시점 마스터 판가 스냅샷, `is_free=false`). 복사 후 SO는 인테이크와 독립(ADR-05 참조 복사). SO 라인 생성은 A의 **`require_sellable_sku`(SO 접수 정책)** 단일 통로 경유.
6. 인테이크 `apply_intake_transition(CONFIRMED)`+`decided_at/by`+`sales_order_id`(version은 ORM이 +1).
7. 이벤트: `order_intake.status_changed`(D1). SO 생성 이벤트는 B의 `record_birth`가 발행. 트랜잭션 안 외부 호출 0(알림은 아웃박스→디스패처).
8. `idempotency.complete(201, {intake_id, sales_order_id, doc_number})`.
- `IntegrityError`는 **제약명으로 판별**: SO PO 유니크→409 `PO.DUPLICATE`(TX 전체 롤백 — 채번 카운터도 같은 TX라 되돌아감), 인테이크 유니크→해당 코드. **미매핑 제약 위반은 500 유지+로그(제약명만, 값 미포함)** — 조용한 삼킴 금지.
- 권한: TRADE·ADMIN(서버 `require_roles`+서비스 재검증). 담당자 불일치로 거부하지 않는다(B8 — 협업 관용, 역할 기준).

**② `confirm_sales_order`**(오케스트레이터 `trade_chain/confirm.py`, L2 — 게이트 평가기가 여러 L1을 읽으므로. 라우터도 L2에서 서빙 `POST /api/v1/sales-orders/{id}/confirm`, B8 경로 유지): `confirm_sales_order(*, actor, idempotency_key, sales_order_id, expected_version) -> ConfirmOutcome`. 하나의 TX, **순서 고정**(A13·B8 잠금 순서 준수):
1. `idempotency.claim`(request_body에 `sales_order_id`·`expected_version`).
2. SO의 `buyer_partner_id`를 잠금 없이 읽고(SO 거래처는 ORIGIN 불변 — B2) **거래처 행 `FOR UPDATE`**(E의 여신 직렬화 축) → **SO 행 `lock_document`**(FOR UPDATE+version 409) → 상태가 RECEIVED가 아니면 409(B의 `TRANSITION.NOT_ALLOWED`).
3. **동결 완결성 사전검사**(A의 `frozen_complete` — 결제조건·Incoterms·환율·`buyer_name` 결측이면 CHECK 위반 500이 아니라 422 `TRADE.DOCUMENT.INCOMPLETE` 다건 detail).
4. `GateSubject` 구성(잠금 이후 읽은 값) → `evaluate_all(phase=SO_CONFIRM)` 7종 전건 재평가. **화면·이전 평가 결과는 재사용하지 않는다.**
5. `effective_overrides` 조회 + `approvals.find_consumable(...)`(읽기) → `gates.clearance(...)`.
6. **미해소가 있으면**: `gate_evaluations(outcome='BLOCKED', results, content_rev)` INSERT → **예외를 던지지 않고 커밋**(idempotency는 `complete`하지 않음) → `ConfirmOutcome(confirmed=False, evaluation_id, report)` 반환 → 라우터가 UoW 종료 **후** 409 `TRADE_CHAIN.CONFIRM.GATE_BLOCKED`로 변환(본문에 게이트별 level·resolution·reason_code·해소 방법, 값은 판매가·수량뿐). 같은 키·같은 본문 재시도는 이어받아 재평가(override 부여 후 재시도 성공).
7. **해소됨**: `approvals.consume(session, approval, *, actor)`(필요 시 — 원자 UPDATE, 실패 시 409 `APPROVAL_REQUIRED`) → B의 `record_transition(RECEIVED→CONFIRMED, approval_id=…)`(`frozen_at`·`confirmed_at`·상태이력·`status_changed` 이벤트) → `gate_evaluations(outcome='CONFIRMED', results(WARN·사용한 override·승인 ref 포함), content_rev)` → `idempotency.complete(200, …)`.
- **승인은 미해소 확정 시도에서 절대 소비하지 않는다**(BLOCKED 커밋과 소비가 한 TX에 섞이면 승인이 사라진다 → C의 2단 계약, §0-3).
- 확정 후 단가·통화·환율 불변은 B2 동결 계약이 강제(D는 "확정 스냅샷=확정 직전 값"만 테스트).

**③ 후속 체인**: S3-1 완결 몫 = **SO CONFIRMED 전이 + `status_changed` 이벤트(payload에 doc_id·doc_number·partner_id·assignee_id — B6 화이트리스트) + 담당자 라우팅 이벤트 소비(디스패처) + 오더 보드 '확정' 열 표시**. 할당/백오더 분기=S4-2·S3-4, 선적 계획 초안=S3-2, 서류 태스크 원클릭=S3-3이 각자 **사람 1클릭 액션**으로 이 이벤트/상태를 소비한다. S3-1에 포트 인터페이스·빈 스텁·비활성 버튼을 두지 않는다(죽은 문 금지). (B5의 `AllocationPort` NOT_IMPLEMENTED 응답 노출이 B 판정으로 confirm 응답에 들어가면 D는 그 결과를 그대로 전달한다.)

**④ 자동 확정 경로 부재 — `test_no_auto_confirm_paths`(신규, 세 모듈 공통)**: (a) `order_intake`·`gates`·`trade_chain`·`order_board` 소스에 `auto_confirm|autoconfirm|auto_approve` 문자열 0건 (b) AST: `confirm_intake`·`confirm_sales_order`·벌크 확정 호출처가 **`order_intake/router.py`·`trade_chain/router.py`·`order_board/bulk.py` 뿐** (c) 두 함수 시그니처가 `actor: AuthenticatedUser`+무기본값 키워드 전용 `idempotency_key`(`inspect.signature`) (d) `scheduler`·outbox 디스패처·notifications·CLI 모듈이 위 함수를 임포트하지 않음 (e) `apply_intake_transition(CONFIRMED)` 호출이 `confirm_intake` 1곳, SO CONFIRMED 전이는 B의 통로 1곳(`confirm_sales_order` 호출) (f) `register_intake` 내부에 확정 호출 0. 각 스캔에 공회전 방지 자기검사. S5-4의 조건부 자동 확정은 별도 모듈·ADR로 (b)를 개정한다.

### (d) 자동화 4금 저촉 여부
저촉 없음 — 위 ④가 구조 증명. 인테이크·SO 확정은 사람 1클릭+idempotency key, 자동 경로 0. (SO 확정은 발주 확정(PO)이 아니라 수주 확정이며, 그래도 사람 1클릭 원칙을 동일 적용.)

### (e) 상태·전이·불변
인테이크 PENDING→CONFIRMED와 SO 생성이 **같은 TX 원자**(어느 한쪽만 성공 없음). SO RECEIVED→CONFIRMED는 B 전이표 엣지 1번. 확정 시도 1건당 `gate_evaluations` 1행(성공은 SO당 정확히 1행).

### (f) 테스트 배분
- **A**: QT 없이 인테이크→SO 접수 **스냅샷 값 일치**(헤더·라인 전 필드, `price_basis='BUYER_PO'`, `list_price_amount`)·SO 번호 형식·백링크 / 확정 후 마스터(품번 매핑·판가) 변경이 SO에 무영향 / 미완결·미매핑·중복 SKU·STALE 각 거부(+양성 대조) / SO 확정: 접수 후 SKU 삭제·DISCONTINUED→BLOCK(재평가가 확정 시점 값 반영) / 확정 스냅샷=확정 직전 값.
- **H**: 승인 없이 여신 초과 확정 불가(ADMIN 포함)·승인 후 확정 성공·**BLOCKED 시도가 승인을 소비하지 않음**·승인 후 금액 변경→재승인 / 상태 PATCH·벌크·인테이크 확정·CLI 각 경로로 승인 필요 SO 확정 시도 전부 거부 / 게이트 UNKNOWN에서 확정 거부.
- **J**: 확정 더블클릭(같은 키)→SO 1건·같은 키+다른 본문 409 / **`confirm_intake` 도중 예외 주입 시 SO·채번·이벤트 전부 롤백**(카운터 복귀) / **BLOCKED 후 `gate_evaluations` 행이 커밋돼 있음+SO 상태 불변** / 같은 키로 BLOCKED 후 override 부여 뒤 재시도 성공(키 소모되지 않음) / 같은 인테이크 동시 confirm 2스레드→SO 1건·나머지 409 / 같은 SO 동시 confirm 20스레드→성공 1·이벤트 1·평가(CONFIRMED) 1행 / TOCTOU: 평가 통과 직후 별 스레드가 SKU를 DISCONTINUED로 바꾸는 시나리오→잠금 하 재평가로 거부 / 트랜잭션 안 외부 호출 0(모의 감시).
- **I**(자동화·통합): 자동 확정 경로 부재 ④ 전건 / 착지 후 CONFIRMED 직행 불가.
- **K**: 위 스캔·시그니처·에러 매핑 표 전수(제약당 위반 유도 1건).
- **변이 점검 대상**: FOR UPDATE 제거 / 게이트 재평가 제거 / 승인 소비를 평가 앞으로 이동 / BLOCKED 기록을 롤백되는 위치로 이동 / 채번을 TX 앞으로 이동 / DUPLICATE_SKU 검사 제거.

### (g) 소비·등재
C(2단 승인 계약)·E(여신·PI)·A/B(생성 함수·전이). 부채·관찰: BLOCKED 시도 행 누적(벌크 재시도 스팸) — 시도 빈도 실측 후 dedup 재판정 / 담당자 알림은 디스패처 규칙(0행이면 미발송 — 승인 알림은 C).

### (h) 되돌리기 비용 — **낮음~중간**
확정 순서 재배치는 함수 1개 안. 이벤트 계약은 B 소유(추가만). `gate_evaluations` 없이 SO 열로 옮기는 것은 비쌈(D3-h).

---

## D6. 오더 보드 — 파이프라인 뷰·벌크 3종·저장 필터·CSV·검색형 선택·동시성 (제안 D6 통합)

### (a) 목적·경계
§7.4 "오더 보드(파이프라인 뷰+벌크 액션+저장 필터)". S3-1 범위 = **접수 이후~확정까지**의 인테이크·SO 뷰만. 할당·선적 열은 소비 세션이 붙인다(죽은 열 금지).

### (b) DESIGN 근거
§7.4·§3 ⑥(오더 보드), §18.4(페이지네이션·N+1), §17.6(건별 독립 TX), §20 H("동시 20명 벌크 경합 정합"), §2(조회 역할 원가 마스킹), S1-3 CSV 이스케이프 규약(ADR-0027), 프런트 선례(`certification-board.tsx` 칸반·`TruncationNotice`·"기타 상태" 컬럼).

### (c) 데이터 변경 — 신규 모듈 `app/modules/order_board/`, 테이블 1개

**`board_saved_filters`** (Pk·Timestamp·SoftDelete·Version·Actor): `user_id BIGINT NOT NULL FK users RESTRICT`(**`user_id`**: `owner_user_id` 등은 handover 감지 이름 — §0-1 #16), `name VARCHAR(60) NOT NULL`(`btrim<>''`), `filter_config JSONB NOT NULL`. `unique_active(board_saved_filters, user_id, name)`. 사용자당 활성 **20개** 상한(서비스 422). `MUTABLE_TABLES` 등재. `filter_config`는 `BoardFilter` 스키마(`extra='forbid'`)로 **저장 시·읽기 시 재검증** — 스키마 변경으로 낡은 저장 필터는 무시가 아니라 응답에 `needs_resave=true` 표시. 마이그레이션: 신규 `create_table` 1건.

**엔드포인트**(전부 `extra='forbid'`, 목록형 `page`·`size`):
- `GET /api/v1/order-board` — **비-Page 단일 객체**(의도된 예외): 고정 4열 `[{stage, label_ko, total, has_more, items[≤50]}]`+`generated_at`. `stage` ∈ `INTAKE_PENDING`(인테이크 대기)·`SO_RECEIVED`(SO 접수)·`SO_ON_HOLD`(SO 보류)·`SO_CONFIRMED`(SO 확정). 함수명 **`get_order_board`(`list_` 접두 금지)**, 최상위 배열 아님 → 기존 두 스캔 통과(§0-1 #2). 열별 정렬: 대기·접수·보류=접수 오래된 순(주의 필요), 확정=최근순. 취소·거부는 보드에서 제외(각 목록 화면의 상태 필터로).
- `GET /api/v1/order-board/items?stage=&page=&size=` — Page 봉투(`list_order_board_items`), '더 보기'.
- 카드 필드 `BoardCard`: `kind(INTAKE|SO), id, ref_label(SO 번호 또는 'IN-{id}'), buyer_partner_id, buyer_name, buyer_po_no, line_count, total_amount, currency, age_days(KST 계산값, 저장 열 없음), assignee_id, assignee_name, updated_at, version`. **원가·마진·매입가·여신·게이트 배지 필드 자체가 응답 모델에 없다**(게이트는 카드마다 평가하면 N+1·낡은 판정 오독 — 상세의 실시간 계산). 판매 금액은 마스킹 비대상.
- 쿼리: 열별 `COUNT`(GROUP BY 1쿼리)+열별 상위 50 조회 4쿼리, 이름·합계는 IN 프리로드/집계 서브쿼리(`sum(quantity::numeric * unit_price_amount)`), **카드 수와 무관한 상수 쿼리 수**.
- 공통 필터 `BoardFilter`: `q`(≤100자, 바이어명·PO번호 부분 일치, `autoescape`), `buyer_partner_id`, `assignee_id`, `currency`, `dest_market_code`, `created_from`·`created_to`(KST 날짜).
- 매핑 안전망: `BOARD_STAGE_STATUSES`(SO 상태→열) 상수 + 아키텍처 테스트 "모든 SO 상태 = 매핑됨 ∪ {CANCELLED} ∪ RESERVED"(B가 RESERVED를 줄이면 이 테스트가 실패해 **카드가 조용히 사라지는 일이 없다**).
- **벌크** `POST /api/v1/order-board/bulk` `{action: CONFIRM_INTAKE | CONFIRM_SO | ASSIGN, targets:[{kind, id, expected_version}] ≤ 50(초과 422 `ORDER_BOARD.BULK.TOO_MANY`), assignee_id(ASSIGN 전용)}`+Idempotency-Key. 보류·거부·취소·override·승인 요청은 **벌크 제외**(건별 사유·해시가 필요한 행위를 일괄로 흘리지 않는다). 원자성 = **건별 독립 TX(§17.6)+결과 리포트**(부분 성공이 정상, 200): `[{kind, id, outcome: OK|SKIPPED|BLOCKED|CONFLICT|FORBIDDEN|FAILED, code, message_ko, blocked_gates:[{gate_code, line_id, level, resolution, reason_code}]}]`+`ok_count`·`fail_count`(**모든 건 커밋·롤백이 끝난 뒤 합산** — 건 TX 안 집계 금지). 각 건은 **단일 통로 함수를 그대로 호출**(`confirm_intake`·`confirm_sales_order`·`assign_*`) — 벌크 전용 확정 코드 없음이므로 게이트·승인·소유권·권한이 개별과 동일. `CONFIRM_SO`는 override·승인을 **부여·기안하지 않고**, 이미 사람이 개별로 부여해 둔 유효 override·승인만 소비(BLOCKED는 `gate_evaluations`에 남음, 코드 `TRADE_CHAIN.CONFIRM.GATE_BLOCKED`, "개별 처리 필요"). `ASSIGN`은 대상 담당자가 활성 사용자인지 검증하고 이미 그 담당자면 `SKIPPED`. 처리 순서 **id 오름차순**(교착 방지), 건별 키 = `sha256(f"{bulk_key}|{action}|{kind}|{id}")` hex(≤128 제한 회피 — §0-1 #14)라 **같은 벌크 재요청은 건별 리플레이로 같은 리포트**(외부 claim 불요). 권한: 벌크는 TRADE·ADMIN, 행별 서버측 역할 검증(권한 없는 행은 `FORBIDDEN` 리포트).
- **저장 필터 API** `GET·POST·PATCH·DELETE /api/v1/order-board/saved-filters` — 본인 것만(타인 id는 404), 전 역할 사용 가능(원가 없음·개인 설정).
- **CSV** `GET /api/v1/order-board/export.csv`(같은 필터, `stage` 선택): 열=`구분·문서번호·거래처·바이어PO번호·통화·합계금액·라인수·담당자·접수일(KST)·납기요청 최소일·상태`, 원가·마진·게이트·여신 열 없음. 모든 문자열 셀 `core.csv_export.render_csv`(=`escape_formula_cell`) 통로, UTF-8 BOM, **최대 50,000행**(기존 `EXPORT_MAX_ROWS` 선례, 초과 422 `VALIDATION_INVALID_FIELD` 조건 좁히기 안내 — 조용한 잘라내기 금지). 전 역할 가능. audit 기록 없음(§0-1 #13).
- **200건 드롭다운 상한 해소(이월 관찰 원장 소비)**: 보드·인테이크·품번 등록의 바이어·SKU·담당자 선택은 **검색형 콤보박스 공용 컴포넌트 1개**(서버 `?q=` 부분 일치+300ms 디바운스+`size≤20`+"결과 없음·더 있음" 표기). 필요한 서버 읽기 전용 확장: `GET /partners`에 `q`·`type`(예 `BUYER`), `GET /skus`에 `q`, 담당자용 **활성 사용자 조회 `GET /api/v1/users/lookup?q=`(id·표시명만, TRADE·ADMIN — 사용자 목록이 ADMIN 전용이라 필요, F 확인)**.
- 프런트: 신규 화면 `/orders/board`(칸반 4열+상세 슬라이드, 잘림·더 보기 고지 — `KanbanBoard`·`TruncationNotice` 추출은 신규 화면부터·기존 화면 이관은 별도 커밋)·`/orders/intakes`(목록·등록·CSV 업로드·검토 상세)·SO 상세의 `GatePanel`. 한국어 UI 규약(`break-keep`, 헤더·협정명·국가명 `nowrap`, 숫자 가운데 정렬). 확정 버튼 키 규칙(D5: (SO id, version)당 1개).

### (d) 자동화 4금 저촉 여부
저촉 없음. 벌크는 사람이 선택·클릭한 건만 단일 통로로 처리하고 새 확정 경로를 만들지 않는다(우회 경로 차단표 B9 #2). 카드·CSV에 원가·마진이 없다(응답 모델 부재).

### (e) 상태·전이·불변
보드는 조회+벌크+개인 설정뿐, 도메인 상태를 직접 바꾸지 않는다(상태 대입 0). `ASSIGN`은 FREE 필드(B2)로 동결 후에도 허용.

### (f) 테스트 배분
- **K**: 보드 `get_order_board`가 최상위 객체·열 4개·열당 items ≤ 50·`has_more` 정합 / **120건 시딩에서 열당 50·total 120** / 응답 모델에 원가·마진·매입가·여신 필드 부재(`is_sensitive_key`+모델 필드 스캔, VIEWER 동일) / N+1: 카드 200건·열 3개에서 쿼리 수 상수 / 엔드포인트×역할 행렬·auth-coverage / 저장 필터 본인 격리(타인 404)·중복 이름 409·21번째 422·미허용 키 422·낡은 스키마 `needs_resave` / CSV: `'=1+1'` 이스케이프·원가 열 부재·BOM·행 상한·KST 서식 / `BOARD_STAGE_STATUSES` 완전성 / `list_` 접두 스캔 통과.
- **H**: **동시 20명 벌크 경합** — 서로 겹치는 인테이크·SO 집합을 20스레드가 서로 다른 순서로 벌크 확정 → 건당 SO/확정 정확히 1회·나머지 `CONFLICT`/`SKIPPED`·중복 이벤트 0·교착 0(id 오름차순)·**리포트 = 실제 DB 상태** / 벌크 부분 성공(50건 중 3건 BLOCKED→47 OK·3 BLOCKED+`blocked_gates`, 카운트=커밋 후 합산) / 벌크로 override·승인 없는 확정 불가(ADMIN 포함) / 51건 422·중복 target 제거 / 같은 벌크 키 재요청=같은 리포트 / 벌크 ASSIGN 후 version 증가(낡은 version PATCH 409)·이미 같은 담당자 SKIPPED. (§20 헤더는 H를 P2·P6에만 매핑 — **P3 매핑 보강 필요**, §설계·WBS 충돌.)
- **A**: 프런트 vitest — 콤보박스 200건 초과 서버 검색 시나리오·`q` 2자 미만 미검색·결과 잘림 고지·벌크 결과 모달·`localStorage` 접근 throw에도 정상 렌더·override 다이얼로그(사유 5자·역할 미보유 시 버튼 비노출이 아니라 서버 403 문구)·UNKNOWN이 PASS와 다른 색·문구·`SCOPE_NOTE` 표시·확정 키 유지/재발급 규칙.
- **변이 점검 대상**: 열 상한 제거 / 벌크 정렬 제거 / 카운트를 TX 안 집계로 / `escape` 통로 우회 / 필터 스키마 `extra='allow'` / 벌크가 전이 함수 우회.

### (g) 소비·등재
S3-2/S4-2: 열 추가(상수+테스트, 매핑 완전성 테스트가 강제). 재판정 트리거 등재: 벌크 HOLD/CANCEL(건별 사유 필요)·저장 필터 공유·게이트 배지의 보드 표시(배치 평가 비용 실측 후)·카드 정렬 사용자 지정.

### (h) 되돌리기 비용 — **낮음**
벌크 액션 추가=열거+호출 1줄, 열 추가=상수 1줄, 저장 필터 테이블 제거는 개인 설정 소실뿐, 상한은 상수.

---

## D7. 횡단 계약 — 권한·소유권·마스킹·낙관 잠금·에러 매핑·에러코드 (제안 X1~X5 통합)

### (a) 목적·경계
D1~D6 전 엔드포인트에 공통 적용되는 계약을 한곳에 고정한다.

### (b) DESIGN 근거
§2 권한·통제(역할 5종·조회 역할 원가 마스킹), §17.2·17.4, §18.1, B8(잠금·멱등·낙관 잠금), A13(제약명→코드 번역), ADR-0024(응답 스키마 분기).

### (c) 데이터 변경 — 스키마 없음
- **역할표**(서버 `require_roles`+서비스 재검증): 인테이크 등록·수정·거부·확정·매핑 해석(`resolve`)·담당 변경=**TRADE·ADMIN** / SO 확정=TRADE·ADMIN / override(D3-e)=게이트별 / 게이트 정책 PUT=**ADMIN**(조회 TRADE·ADMIN) / 보드·인테이크·게이트 조회·CSV=**전 역할** / 벌크=TRADE·ADMIN / 저장 필터=본인만(전 역할). LOGISTICS·CERT·VIEWER는 쓰기 403.
- **소유권**: 팀 공유 업무 데이터라 담당자(`assignee_id`)는 접근 제한이 아니라 **이관 단위**다(B8 — 역할 기준). 매 요청 검증: 인테이크·SO 존재·`deleted_at`·경로 id 일치, **라인 id가 다른 인테이크 소속이면 404**(IDOR). 저장 필터만 본인 소유 강제.
- **마스킹**: 인테이크·SO·보드·게이트 응답에 **원가·마진·매입가 필드를 어느 역할에도 싣지 않는다**(필드 부재). 게이트 `basis`·`results`의 금액은 판매가·수량뿐. **여신 수치(한도·노출)는 `CREDIT` 결과의 basis에서 TRADE·ADMIN에게만 응답**(그 외 역할 응답에서 필드 부재 — ADR-0024 방식, E의 재판정 결과가 다르면 상수 1곳; ADR-0026 재판정 트리거 소진 처리 안건은 E·F가 부기). 로그 컨텍스트·이벤트 payload·에러 detail에 금액·사유 원문 미기재.
- **낙관 잠금**: 모든 변경·확정·거부·매핑·벌크 대상은 `expected_version` 필수(없으면 422). 라인만 바뀌어도 **헤더 version +1**(`bump_header_version` — A13). handover 일괄 UPDATE는 version을 올리지 않으므로 담당 변경은 검토 내용을 무효화하지 않는다(독스트링 명시). 게이트 조회·`resolve`(변경 없음)는 version 불변.
- **IntegrityError→HTTP 번역 표**(`order_intake/service.py::map_integrity_error` 1곳, **제약명으로 분기**): 인테이크 PO 유니크→409 `ORDER_INTAKE.PO.DUPLICATE` / 파일 그룹 유니크→409 `ORDER_INTAKE.FILE.DUPLICATE` / SO PO 유니크(A)→409 `ORDER_INTAKE.PO.DUPLICATE` / SKU 중복(A)→409 `TRADE.LINE.SKU_DUPLICATE` / 그 외 미등록 제약 위반→**로그(제약명만, 값 미포함) 후 500 유지**(삼키면 fail-open). `UnknownCurrencyError`·날짜/정수 파싱 `ValueError`·`lock_timeout`(B의 핸들러)·`PendingRollbackError`는 서비스 경계에서 지정 코드로 변환. 신규 varchar 폭 초과는 스키마 `max_length`로 422(DB 에러 도달 전): `buyer_po_no` 60·`buyer_item_code` 100·`reject_reason` 500·override `reason` 500·정책 `gate_code` 20·필터 `name` 60·`q` 100, **정규화 후 키 길이 60 초과 422**.
- **에러코드**(`core/errors/codes.py`+`catalog.py` 동시 추가, 3세그먼트·문구 ≥10자+조치 힌트어·`test_error_catalog` 통과; 도메인 접두는 모듈명 — 통합 검토가 A/B의 `TRADE`·`TRADE_DOCS`와 정렬):
  `ORDER_INTAKE.STATE.NOT_PENDING`(409) · `ORDER_INTAKE.PO.DUPLICATE`(409) · `ORDER_INTAKE.LINE.UNMAPPED_ITEMS`(422) · `ORDER_INTAKE.LINE.STALE_MAPPING`(409) · `ORDER_INTAKE.LINE.DUPLICATE_SKU`(422) · `ORDER_INTAKE.LINE.LIMIT_EXCEEDED`(422) · `ORDER_INTAKE.FILE.DUPLICATE`(409) · `ORDER_INTAKE.FILE.INVALID_ROWS`(422) · `ORDER_INTAKE.FILE.TOO_MANY_GROUPS`(422) · `ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT`(422) · `GATES.OVERRIDE.NOT_ALLOWED`(403) · `GATES.OVERRIDE.NOT_APPLICABLE`(422) · `GATES.OVERRIDE.STALE`(409) · `GATES.POLICY.INVALID_CONFIG`(422) · `TRADE_CHAIN.CONFIRM.GATE_BLOCKED`(409) · `TRADE_CHAIN.CONFIRM.APPROVAL_REQUIRED`(409, C와 합의) · `ORDER_BOARD.BULK.TOO_MANY`(422) · `ORDER_BOARD.FILTER.LIMIT_REACHED`(422) · `ORDER_BOARD.FILTER.DUPLICATE_NAME`(409). 거래처 유형·통화·시장·날짜·수량·단가 형식은 기존 `VALIDATION_INVALID_FIELD`+detail 재사용(열거 폭증 방지). 재사용: `IMPORTS_FILE_TYPE_NOT_ALLOWED`·`ENCODING_INVALID`·`HEADER_MISMATCH`·`EMPTY`·`TOO_LARGE`(메시지가 "표준 양식·CSV"로 일반적임을 실측 — 20MB 표기가 실제 상한과 일치).
- **핵심 엔드포인트 요약**: `POST /order-intakes`·`POST /order-intakes/import-csv`·`GET /order-intakes/template.csv`·`GET /order-intakes`(`list_order_intakes`)·`GET /order-intakes/{id}`·`GET /order-intakes/{id}/gates`·`PATCH /order-intakes/{id}`·`POST /order-intakes/{id}/resolve`·`POST /order-intakes/{id}/confirm`·`POST /order-intakes/{id}/reject` / `GET /sales-orders/{id}/gates`·`POST /sales-orders/{id}/confirm`·`POST /sales-orders/{id}/gate-overrides`·`POST /sales-orders/{id}/gate-overrides/revoke` / `GET /gate-policies`·`PUT /gate-policies/{gate_code}` / 보드 6군(D6). 전 쓰기 POST는 Idempotency-Key(PATCH는 version만 — B8·certifications 선례), 전 목록 `page`/`size`(기본 50).

### (d) 자동화 4금 저촉 여부
없음.

### (e) 상태·전이·불변
해당 없음(계약).

### (f) 테스트 배분
- **K**: 엔드포인트×역할 행렬 전수(TRADE/ADMIN/LOGISTICS/CERT/VIEWER × 쓰기=허용/403)·auth-coverage(401)·**신규 쓰기 스키마 `extra='forbid'` 스캔(D 세 모듈)**·에러코드 카탈로그 전수 / 제약명→코드 번역 **전수**(제약당 위반 유도 1건)·미등록 제약은 500+로그에 값 없음 / 폭 초과 각 필드 경계(max/max+1)·NFKC 확장으로 60 초과 PO번호 422·미등록 통화 500 미발생 / 라인 id 교차→404·soft-deleted 인테이크 404 / VIEWER 응답 전 필드 `is_sensitive_key` 스캔·여신 수치 부재 / 로그 캡처에 금액 미포함.
- **J**: 라인만 수정한 뒤 이전 version으로 확정→409 / handover 실행 후 확정 성공(version 불변) / 두 사용자 동시 수정→하나 409.
- **변이 점검 대상**: 매핑 표에서 한 제약 삭제(500 재현 테스트가 실패해야 함) / `expected_version` 비교 제거 / 역할 검증 제거.

### (g) 소비·등재
F: 역할표·`users/lookup`·ADR 번호·마스킹 원장(게이트 basis의 판매 기준가·여신 수치, 보드의 비원가 확인 항목)·기존 관찰(헤더 멱등 키 길이 미검증).

### (h) 되돌리기 비용 — **낮음**
권한표·폭·번역 표는 코드 상수. 에러코드 도메인 접두 정렬은 기계적 개명.

---

## D8. 실행 계획 — 마이그레이션 순서·기존 코드 변경·문서 갱신·PR 분할 제안

### (a) 목적·경계
D의 변경 전량 목록(기존 테이블·코드 변경 포함)과 착수 순서를 고정해 통합 검토·계획 세션이 그대로 채택하게 한다. 구현은 계획 승인 이후.

### (c) 변경 전량
- **신규 테이블(D 소유 6)**: `order_intakes`·`order_intake_lines`(MUTABLE) / `gate_policies`(MUTABLE) / `gate_evaluations`·`gate_overrides`(**IMMUTABLE+REVOKE**) / `board_saved_filters`(MUTABLE).
- **기존 테이블 변경(D 요구 — 소유는 A/타)**: `sales_orders.buyer_po_date DATE NULL`(A, CONTENT 분류). D 자체의 기존 테이블 ALTER는 **0건**(`skus.moq`·`partners.name_en`은 A 소유).
- **마이그레이션 순서**: (A 리비전: 전표·`skus.moq`·`partners` 확장·`bank_accounts`) → (B: 상태이력 4표+REVOKE / C: approvals) → **M-D1** `order_intakes`+`order_intake_lines` → **M-D2** `gate_policies`·`gate_evaluations`·`gate_overrides`(+REVOKE 2) → **M-D3** `board_saved_filters`. 각 리비전 `upgrade→downgrade→upgrade` 왕복 테스트·`alembic check` 드리프트 0·식별자 63자 확인·CHECK는 `op.f()` 수기+정의문 테스트·시드 없음.
- **기존 코드 변경(전부 동작 불변 또는 읽기 전용 가산)**: `partners.service.resolve_buyer_items` 신설·`GET /partners`에 `q`·`type` / `GET /skus`에 `q` / `readiness.service.cells_for` 신설(쓰기 0) / `imports/service.py` `_read_limited`·`_validate_extension` 공개 승격(alias 유지, F 그룹 무수정 통과가 조건) / `handover/targets.py` +1행(`"order_intakes"`) / `core/db/table_policy.py`(IMMUTABLE +2·MUTABLE +4) / `app/registry.py` 모델 모듈 3개 / `api/router.py` include / `core/errors` 코드·카탈로그 / `app/cli.py` `seed-gate-policies` / `identity` 사용자 조회(F).
- **DESIGN 갱신(설계 보강 필요)**: §7.4 [M4] 보강 문단(게이트 명세표·4결과값·해소 방식·확정 시점 재평가·2단 흐름) / **§12.2 말미와 §3 M9에 부기 — "스테이징 한 입구"는 원칙이고 오더 도메인 물리 입구는 `order_intakes`, `import_staging`은 마스터 왕복 전용** / §17.5 확장 부기 1문장(`gate_evaluations`·`gate_overrides` 등재) / §3 표 맵에 6테이블 / §7.2 "접수(스테이징)" 해석 1줄. **ADR 4건**(번호는 통합 검토가 0051~ 배정, 각 5줄): ① 오더 인테이크 별도 테이블·2단 흐름·착지 단일 통로·원본 미보관(예약 값 명기) ② 게이트 공통 모듈(4결과값×3해소·평가 시점·불변 평가/override·정책 데이터·MARKET 워딩 규칙) ③ 게이트 원천(품번 정확 일치·정수 교차곱 편차·`cells_for`·MOQ·PO 키·CSV 무결성 규칙) ④ 오더 보드(벌크 3종·저장 필터 DB·CSV·검색형 선택). DESIGN·ADR은 한 세트(CLAUDE.md).
- **PR 분할 제안(계획 세션이 확정)**: D-PR1 `gates` 코어(타입·명세·`clearance`·3테이블·정책 CRUD·CLI·평가기 등록부) → D-PR2 인테이크(2테이블·수동 입구·`resolve_buyer_items`·검토용 게이트 평가기·`cells_for`·`confirm_intake`·reject) → D-PR3 CSV 입구 → D-PR4 SO 확정 오케스트레이션·override·게이트 패널 API(**A·B·C·E 산출 선행 필요**) → D-PR5 보드·벌크·저장 필터·CSV·검색형 콤보박스·프런트. 각 PR의 병합 조건은 CLAUDE.md 병합 게이트(head SHA 전 체크런 success+mergeable clean).
- **11렌즈 통과 예정 근거**: 기능(DoD 4항 중 중복 PO 0건·여신→승인·불변에 직접 기여) / 데이터(스냅샷·soft delete·불변 테이블) / 트랜잭션(1동작=1TX·아웃박스) / 동시성·멱등(잠금 순서·부분 유니크·키) / 보안·권한(서버측 역할·IDOR·마스킹) / 시간(KST `age_days`·`today_kst`) / 성능(N+1 상수 쿼리 테스트) / 테스트(A·G·H·I·J·K 배분) / 운영(정책 데이터·CLI·로그 마스킹) / 문서(DESIGN+ADR 4건) / 워크스루(CSV 업로드→검토→매핑 등록→확정→SO 편집→override→SO 확정을 입구부터 출구까지 1회 관통 — J·H e2e 대표 1쌍).

### (d) 자동화 4금 저촉 여부
없음(D5-④ 스캔이 PR-2부터 상시 가동).

### (h) 되돌리기 비용 — **낮음**(계획·순서는 문서), 마이그레이션 착수 후는 D1~D3 각각의 (h)를 따른다.

---

## 자율 확정 판정표

| 번호 | 결정 요지 | 근거 한 줄 | 되돌리기 비용 |
|---|---|---|---|
| D1 | 별도 모듈 `order_intake`·테이블 2개(`order_intakes`·`order_intake_lines`), 상태 PENDING/CONFIRMED/REJECTED(허용 2·미허용 4), 2단 흐름(인테이크 확정=SO 접수 생성), 백링크 intake→SO 단방향, 원본 스냅샷 불변(ORM 가드+AST+CHECK), 편집 이력 테이블 없음·거부 영구 보존·삭제 경로 없음, 거래처·통화 등록 후 불변, 결제조건·Incoterms·환율은 인테이크에 없음, `ASSIGNMENT_TARGETS` 라벨=테이블명 | import_staging 불변식 충돌(3안 일치)·§12.1 이력 목적은 원본 스냅샷 대 현재 diff로 충족·A의 SO CHECK가 부분 복사 불허 | 중간 |
| D2 | 입구 2종(MANUAL·CSV), `register_intake` 단일 착지(status 인자 없음), CSV 9열 표준 양식·ISO 날짜만·통화최소단위 초과 소수 거부·지수표기 거부·`.xlsx` 미수용·**파일 전체 원자(모든 오류 리포트)**·품번 미매핑만 착지·파일 해시 멱등(sha+그룹키 PENDING 부분 유니크)·20MiB/50,000행/그룹 200/라인 200·원본 파일 미보관(예약 값만) | 죽은 열거 금지(ADR-0041)·부분 착지 후 재업로드 sha 충돌·조용한 보정 금지 | 낮음 |
| D3 | 공통 `gates` 모듈: 결과 PASS/WARN/BLOCK/UNKNOWN×해소 NONE/OVERRIDE/APPROVAL, UNKNOWN=BLOCK 효과(fail-closed)·별도 표기, 스테이징=저장 없는 실시간 계산·SO 확정=잠금 하 전건 재평가, 불변 `gate_evaluations`(확정 시도, 성공은 SO당 1행)·불변 `gate_overrides`(가능 게이트만 열거·GRANT/REVOKE·해시 결속·5자 사유), `gate_policies`(0행=UNKNOWN·시드 금지·CLI+ADMIN PUT), override 권한 TRADE·ADMIN(가격·MOQ)/ADMIN(준비도·PI), 별도 액션+서버 해시+낡음 409, 도메인 무임포트(등록부 주입) | 6종+PI 한 모델·GC-C2 통제된 override·§17.5 확장 불변·함정 ⑩ | 중간 |
| D4 | 원천: 품번=strip 정확 일치·2쿼리(힌트만 대소문자)·수동 SKU 지정 금지 / 단가=정수 교차곱 허용치(정확히 허용치는 통과)·SO 단계 기준=라인 `list_price_amount`·통화 불일치=UNKNOWN(환산 없음)·PURCHASE 조회 0 / 준비도=`cells_for` 재사용·GRAY=UNKNOWN·ADMIN override / MOQ=A의 `skus.moq`·미만 BLOCK+override·NULL PASS / 중복 PO=A의 `buyer_po_no_key`(제로폭 제거 요구)·착지 시 409·취소 SO 재사용 허용·개정판 개념 없음 / 여신·PI=E 인터페이스·잠금 하 호출·미등록=UNKNOWN / DISCONTINUED=I단계 WARN·C단계 BLOCK/NONE | 마스터 규칙 재구현 금지·A11 확정과 정합·fail-closed | 낮음~중간 |
| D5 | `confirm_intake` 1TX(claim→잠금→완결성→하드 게이트→SO 접수 생성(채번 최후)→CONFIRMED→이벤트→complete)·`confirm_sales_order` 1TX(거래처→SO 잠금→동결 완결성→7종 재평가→승인 조회→미해소면 **BLOCKED 커밋 후 409**·해소면 승인 소비+B 전이+CONFIRMED 스냅샷)·승인은 미해소 시도에서 소비 안 함(C 2단 계약)·같은 키 재시도는 이어받기·후속 체인은 `status_changed` 이벤트까지(포트·스텁 없음)·자동 확정 부재 AST 스캔 6항 | §17.1·§17.2·GC-H1·B9·idempotency 이어받기 실측 | 낮음~중간 |
| D6 | 보드 `/order-board` 비-Page 객체·고정 4열(대기·접수·보류·확정)·열당 50+`has_more`+Page 드릴다운·카드에 원가·게이트 없음, 벌크 3종(확정 2+ASSIGN) 건별 독립 TX·id 순·해시 건별 키·결과 리포트·override 미부여, 저장 필터 DB(`user_id`, 20개, 본인만), CSV(원가 없음·이스케이프·50,000행), 검색형 콤보박스+`q`/`users/lookup` 확장, 동시 20명 벌크 H 편입, 상태 매핑 완전성 테스트 | §7.4 문면 최소 구현·기존 스캔 실측(허용 목록 없음)·EXPORT 선례 | 낮음 |
| D7 | 역할표·소유권=역할 기준·IDOR 404·여신 수치 TRADE·ADMIN만·`expected_version` 필수·헤더 version bump·제약명 기반 번역 표(미등록은 500 유지)·폭 표·에러코드 19종 | B8·A13 정합·조용한 삼킴 금지 | 낮음 |
| D8 | 신규 6테이블·기존 테이블 ALTER 0건(D 소유분)·마이그레이션 M-D1~D3 순서·기존 코드 변경 목록·DESIGN 보강 5곳·ADR 4건·PR 5분할 제안 | 착수 전 변경 전량 공개 | 낮음 |

---

## 설계·WBS와의 충돌·보강 필요 목록

1. **DESIGN §7.4·§12.2·§3 M9 보강 + ADR 필요** — ADR-09 "import_staging → 검토 → 확정" 문면과 §7.4 "스테이징 한 입구"의 물리 해석: 오더 도메인은 `order_intakes`, `import_staging`은 마스터 왕복 전용. 원문은 보존하고 부기(D1·D8). SO '접수' = 인테이크 확정 산출물(2단)이라는 §7.2 해석 1줄.
2. **DESIGN §17.5 확장 부기** — `gate_evaluations`·`gate_overrides` 불변 테이블 등재(B의 상태이력 4표 부기와 합쳐 1문장 가능).
3. **DESIGN §7.4 게이트 명세 보강 필요(설계 침묵 보충)** — 결과값 4종·해소 3종·평가 시점·MOQ 미설정=PASS·GRAY=UNKNOWN·DISCONTINUED 시점별 처리·PI 기본 WARN의 이유. "가장 좁은 안전한 결정"임을 근거와 함께 기록.
4. **WBS/DESIGN §20 헤더 충돌** — `P3=A·B·E` 매핑에 H(승인 우회 차단·동시 20명 벌크)·I(자동 확정 부재)·G(파일 해시 멱등)가 없다. 본 묶음은 H·I·G·J·K 케이스를 S3-1에 배정하므로 **§20 매핑 보강 문장 필요**(B9와 동일 지적 — F 소관).
5. **WBS S3-1 "PI 입금 게이트" ↔ S3-3 payments** — 입금 기록 경로가 없어 S3-1 실운영에서 PI 미입금이 상시 참(E가 해소). 그래서 PI 기본 모드를 WARN으로 두었고 BLOCK 전환은 S3-3 이후 오너 결정.
6. **A와의 접점 요구 4건**(§0-3): `buyer_po_date`·`normalize_buyer_po_no`의 제로폭 제거·`create_received_sales_order` 계약·잠금 순서 ⓪ 인테이크 — A 판정서에 반영 요청.
7. **CLAUDE.md 원칙 충돌 없음** — 단 "모든 엔드포인트 소유권 검증"은 B8 해석(역할 기준·협업 관용, 개인 소유 필터 없음)을 따른다(§20 K "타 사용자 전표 403"의 해석 확정은 F).
8. **기존 관찰 원장 소비**: "SKU·거래처 선택 200건 초과 시 검색형"(r6·다수 등재) — D6에서 종결, 재판정 트리거 소진 처리 필요(F). "여신한도 마스킹 재판정(ADR-0026 — 트리거 S3-1)" — 본 묶음은 TRADE·ADMIN 한정 응답을 기본으로 제안, 결론은 E·F가 마스킹 원장 #2·ADR-0026에 부기.

## 타 묶음 의존(요약)

- **A**: `buyer_po_date`(SO)·`normalize_buyer_po_no`(제로폭 제거)·`create_received_sales_order(session, *, actor, draft)`·`require_sellable_sku`(SO 접수 정책)·`snapshot_line_from_master` 변형(단가 오버라이드, `price_basis='BUYER_PO'`)·`prices_at`·`list_price_amount`·`skus.moq`·`parse_minor_amount`·`TRADE.LINE.*` 에러·잠금 순서 ⓪·SO PO 부분 유니크(이미 확정).
- **B**: `record_birth`·`record_transition(approval_id=…)`·`issue_document_number` 최후 호출·`lock_document`·`content_rev`·`confirmed_at`·`sales_orders.sales_order.status_changed` 이벤트·`CONCURRENCY.LOCK.BUSY` 핸들러·`assignee_id` FREE 편집 통로(벌크 ASSIGN)·RESERVED 상태 목록(보드 매핑 테스트).
- **C**: `approvals.find_consumable`/`consume` **2단 계약**, `(SO id, content_rev)` 결속, ADMIN 우회 불가, 승인 요청 알림(결재선 0행 fail-visible).
- **E**: `evaluate_credit`·`evaluate_pi_deposit`, 여신 직렬화=거래처 행 잠금(D가 잠근 뒤 호출), PI 입금 판독 원천, 여신 수치 마스킹 결론, `gate_policies('PI_DEPOSIT')` 모드 저장 수용.
- **F**: 역할표 확정(override 역할)·`users/lookup` 소유·ADR 번호 0051~ 배정(D는 4건)·§20 매핑 보강·GC v1.4(중복 PO 거부·인테이크 자동 확정 불가·여신→승인·확정 후 불변)·마스킹 원장·PROGRESS(SO 확정 이벤트 소비 계약을 S3-2·S4-2 이월로 등재).

## 부채·관찰 등재(PROGRESS 후보)

| ID | 내용 | 트리거/소유 |
|---|---|---|
| D-D1 | 인테이크 편집 단위 이력(현재는 원본 스냅샷 대 현재 diff만) | 파서 개선 데이터로 편집 순서 필요 / S6-1 |
| D-D2 | `.xlsx` 수용·CSV 양식 개정 시 직전 세대 헤더 병행 수용 | 사용자 요구 / 양식 개정 세션 |
| D-D3 | 원본 파일 보관(`documents.owner_type='ORDER_INTAKE'`(12자 ≤13)+`order_intakes.document_id` 추가형) | S6-1 AI PDF 경로 |
| D-D4 | 거래처 미등록 상태의 인테이크 착지(`buyer_partner_id` NULL 허용, `DROP NOT NULL` 1건) | AI 경로 소비 시 |
| D-D5 | 무상(0원) 라인 인테이크 수용 | 실사용 요구 |
| D-D6 | 바이어별 단가 허용치·바이어별 MOQ·품목군별 허용치 | 실사용 요구 |
| D-D7 | 바이어 PO 개정판(Rev) 전용 개념(현재 "취소 후 재접수") | 실사용 마찰 |
| D-D8 | 품번 매핑 정정 API 부재(기존 부채 유지) | F 판정 |
| D-D9 | BLOCKED 확정 시도 행 누적(벌크 재시도 스팸)·dedup | 시도 빈도 실측 |
| D-D10 | 벌크 HOLD·CANCEL·저장 필터 공유·게이트 배지의 보드 표시 | 실사용 요구 / 배치 평가 비용 실측 |
| D-D11 | MARKET_READINESS override 역할에 CERT 추가 여부 | F 역할 판정 / 실사용 |
| D-D12 | PI 기본 모드 WARN→BLOCK 전환 | S3-3 payments 이후 오너 결정 |
| D-D13 | `Idempotency-Key` 헤더 길이(>128) 미검증 → DB 오류 500 가능(기존 결함) | F/코어 소규모 수정 |
| D-D14 | 오더 보드 CSV·게이트 응답의 접근 audit 기록(현재 선례상 미기록) | 대량 반출 우려 발생 시 |
