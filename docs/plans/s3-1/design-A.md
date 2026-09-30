# S3-1 계획 설계 — 묶음 A 판정서: 전표 데이터 모델·참조·스냅샷·조건 구조

- 작성: 묶음 A 심판(최종 설계자). 자율 확정(오너 지시 2026-09-29) — 아래 결정은 전부 "확정"이며 사후 번복 가능하다(안건마다 되돌리기 비용 병기).
- 방법: 3개 관점 제안(strict/risk/fit)의 사실 주장을 코드·DESIGN에서 직접 확인했고, 충돌 지점은 이유를 들어 하나를 골랐다. **정적 독해만 했다 — 실행 검증 못 했음**(pytest·alembic·DB 미실행. 식별자 길이는 파이썬으로 문자 수만 셌다).
- 안건 번호는 통합 검토 편의를 위해 새로 매겼다(A1~A14). 제안의 qid 대응은 각 안건 머리에 적는다.

---

## 0. 전제·검증 결과·타 묶음에 둔 가정

### 0-1. 제안 사실 주장 검증 결과 (틀린 것은 걸러냈다)

| # | 주장(출처) | 확인 | 판정에 미친 영향 |
|---|---|---|---|
| 1 | `numbering/service.py:36`이 UTC 연도(`(at or utcnow()).year`) — 3안 공통 | **맞음**. 호출부는 테스트뿐(`tests/integration/test_numbering.py`, `at` 사용 0건), 앱 안 호출 0곳 | 채번 연도를 KST로 고침(A10). risk의 "doc_date 연도를 `at`으로 넘겨 우회"는 기각(소급 입력 시 번호 연도가 증빙일을 따라가 발급 순서와 어긋남 — 이유 A10) |
| 2 | handover가 필터 없이 `UPDATE ... SET assignee_id` 일괄 수행(strict) | **맞음**(`handover/service.py:56-68`, Core `update()`, version·updated_at 미증가, 취소·완료 전표도 포함) | 동결 제외 열에 `assignee_id`를 못박음(A3) |
| 3 | `fx_rates`가 DESIGN §3 [공통] 맵에 있으나 WBS 배정·코드 0건 | **맞음**(WBS grep 0, 마이그레이션 27건 중 0) | 3안 중 2안(strict·fit)이 "미신설", risk만 "신설". 판정은 A7 |
| 4 | fit이 쓴 FK 열 이름 `proforma_invoice_id`로 `unique_active(lines, proforma_invoice_id, sku_id, is_free)`를 만들면 식별자 63자 초과 | **맞음** — `uq_proforma_invoice_lines_proforma_invoice_id_sku_id_is_free_active` = 67자, `_guard_name`이 `ValueError`로 임포트 시점에 실패(`core/db/constraints.py`) | strict의 짧은 FK 이름(`qt_id/pi_id/so_id/po_id`)을 채택. 이 이름이면 최장 53자 |
| 5 | risk: "PO 금액 컬럼을 `purchase_` 접두로 지으면 redaction 키에 자동 편입" | **틀림**. `SENSITIVE_KEYS`는 정확히 `purchase_price`·`purchase_amount`뿐이고 접미사는 `_cost`·`_margin`뿐이라 `purchase_total_amount`는 마스킹되지 않는다(`core/logging/redaction.py:28-92`, `normalize_key`는 카멜→스네이크만 함). 접두 규칙을 새로 짜야 함 | 이미 마스킹·금액 판정 양쪽에 걸리는 **`unit_cost`/`line_cost`/`total_cost` 이름**을 채택(BOM `unit_cost` 선례). redaction 수정 없이 자동 편입(A12) |
| 6 | strict·fit·risk: 은행 스냅샷 컬럼 `bank_account_no`/`bank_account_number`가 로그에서 자동 마스킹 | **틀림**. 키 목록은 정확 일치 `account_no`·`account_number`뿐이고 접미 규칙에 없다 | 접미사 `_account_no`·`_account_number` 2개를 `SENSITIVE_SUFFIXES`에 추가(A8) |
| 7 | risk: 라인 CHECK `line_amount = qty * unit_price`가 bigint 곱 오버플로로 500을 낼 수 있다 | **맞음**(PG는 CHECK 평가 중 `bigint out of range` 예외) | `quantity::numeric * unit_price_amount` 형태로 numeric 곱 + 상한 CHECK 분리(A2) |
| 8 | strict: 복합 FK `(pi_id, qt_id) → proforma_invoices(id, qt_id)`가 SO의 qt_id 일치를 DB가 강제 | **절반만 맞음** — PG 기본 MATCH SIMPLE은 한 열이라도 NULL이면 검사를 건너뜀. `pi_id`만 있고 `qt_id`가 NULL이면 통과 | 보강 CHECK `pi_id IS NULL OR qt_id IS NOT NULL` 추가(A2) |
| 9 | 선수금 %를 프런트가 `/100`·`*100`으로 다루는 것이 막혀 있음(fit) | **맞음** — `frontend/src/lib/money.test.ts:62`의 `\w\s*[/*]\s*100\b` 가드가 프로덕션 소스를 훑는다 | bp 정수 저장 + **서버가 퍼센트 문자열을 주고받음**(프런트 산술 0, A5) |
| 10 | `money_columns()` 헬퍼 미사용(r3) | **맞음**(정의만 있고 호출 0) | 금액 쌍은 선례대로 손으로 선언(A1) |
| 11 | risk: kbos_app `lock_timeout=5s`(55P03) 처리 코드 0건 | **맞음**(`grep 55P03\|LockNotAvailable\|lock_timeout app/` = 세션 주석 1건뿐) | 전표 직렬화가 처음으로 잠금을 본격 사용하므로 변환 핸들러를 S3-1에 포함(A13) |
| 12 | `feature_flags`로 'lc' 플래그 조회 가능 | **절반**: 모델(`platform/models.py`)만 있고 **읽는 코드도, 행을 만드는 경로도 없음**(grep) | "행 없음=꺼짐"이면 S3-1 프로덕션에서 L/C 선택 경로가 사실상 닫힘 — 이를 인정하고 S3-3 인계로 명시(A5) |
| 13 | `readiness` 계약 테스트가 `readiness/matrix/traffic/signal/cell_color/market_ready/sellable` 이름의 테이블·컬럼을 금지 | **맞음**(`test_readiness_contract.py:31-33`) | `dest_market_code` 등 제안 컬럼은 통과. 게이트 결과 저장 이름은 D 묶음이 주의 |
| 14 | 시장 참조는 `markets.code`를 값으로 FK(`String(2)`, 전역 UNIQUE) | **맞음**(`catalog/models.py:238` 선례) | `dest_market_code VARCHAR(2) FK markets.code` |

### 0-2. 타 묶음에 둔 가정 (통합 검토가 맞춘다)

- **B**: ① 상태 값은 `CANCELLED`(취소)·`EXPIRED`(만료)라는 영문 코드를 쓴다(내 부분 유니크·소비 술어가 이 두 리터럴에 의존). ② QT `CONVERTED`(수주전환)는 "그 QT에서 파생된 SO가 처음 확정되는 트랜잭션"에서 전이한다. ③ 취소 사유에 `REVISED`가 있다. ④ 동결의 DB/서비스 강제 수단은 B가 정하되 **동결 여부의 단일 표식은 A가 준 `frozen_at`** 이고 동결 열 집합은 A3의 목록을 쓴다. ⑤ 만료 전이가 스윕 잡이든 계산값이든, 참조 자격 검사는 상태만 믿지 않고 `valid_until`도 직접 본다(A4).
- **C**: 승인 대상 참조는 `trade_docs.DOC_PREFIXES`(QT/PI/SO/PO)를 폴리모픽 코드로 쓴다.
- **D**: 인테이크가 SO 라인을 만들 때 A의 `LineIn`·`parse_minor_amount`·`require_sellable_sku`·`snapshot_line_from_master`를 재사용한다. `buyer_po_no_key` 정규화 함수는 A가 제공한다.
- **E**: 여신·결재 임계 비교는 A7의 `comparable_amount()`를 쓰고 `None`을 통과로 취급하지 않는다. 선수금 계산은 A5의 `split_advance()`.
- **F**: PO 라인 품목은 **SKU 전용**으로 두고(A12), 조회(VIEWER) 마스킹은 BOM 선례의 "필드 부재 스키마 분기"를 쓴다. 공급사 유형은 SUPPLIER 또는 OEM.

---

## A1. 테이블 구성·모듈 계층·명명 (제안 A1 통합)

**(a) 목적·경계** — 4개 전표(QT·PI·SO·PO)를 헤더+라인 8테이블로, PI의 은행정보 원천을 마스터 1테이블로 신설한다. 상태 이력 테이블·승인·인테이크·게이트 저장은 B·C·D·E 소관이라 여기서 만들지 않는다.

**(b) DESIGN 근거** — §3 표 맵(`quotations(+lines), proforma_invoices, sales_orders(+lines), purchase_orders(+lines)`), §7.1~7.3, WBS S3-1 산출물 "(+lines·잔량·스냅샷)". **PI 라인은 §3 표 맵에 `(+lines)`가 없으나 표기 누락으로 확정**한다 — 근거: ① WBS는 4종 전체에 "+lines·잔량"을 붙였고 ② §7.6은 QT·PI를 같은 원천에서 렌더링하며 "수량×단가=금액·라인합=총액"을 저장 전 검증으로 요구하고 ③ PI가 QT를 부분 수량으로 참조하는 경로(A4)와 ADR-05 "선행 복사"가 PI 자체 라인 없이는 성립하지 않으며 ④ 3안 모두 동일 결론.

**(c) 데이터 변경**
- 신설 테이블 9개: `bank_accounts`, `quotations`, `quotation_lines`, `proforma_invoices`, `proforma_invoice_lines`, `sales_orders`, `sales_order_lines`, `purchase_orders`, `purchase_order_lines`. 전부 신규 테이블(백필 없음) → `table_policy.MUTABLE_TABLES` 9종 등재(초안 편집·상태 전이 UPDATE가 필요하므로 IMMUTABLE 불가. 불변 이력 테이블은 B가 IMMUTABLE로 등재).
- 믹스인: **헤더·bank_accounts** = `PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base`. **라인** = `PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base`(Version 없음 — 라인 편집은 헤더 행 잠금+헤더 version 검사로 직렬화하고 헤더 version을 올린다, A13).
- 전 FK `ondelete=RESTRICT`. ORM `relationship`은 두지 않는다(복합 FK 다중 경로 모호성·N+1 방지 — 명시 쿼리).
- **FK 열 이름은 짧게 고정**: 헤더 참조 `qt_id`·`pi_id`·`so_id`·`po_id`. (검증 #4)
- 금액은 **손으로 선언**(`money_columns()` 미사용, 검증 #10): 판매 체인 `*_amount`(BIGINT) + 통화 `currency`(CHAR(3)). 헤더 단일 통화가 헤더 금액 열을 모두 커버하고, **라인도 자기 `currency` 열을 갖되 헤더와 복합 FK로 묶는다**(A2). 이유: `money_columns` 독스트링이 밝힌 프로젝트 철학("금액만 있고 통화가 없는 컬럼을 만들 수 없게")과 ADR-0003 ④를 행 단위로 지키면서, 혼합 통화가 DB에서 불가능해진다(strict 안 채택, risk·fit의 "라인 통화 열 없음"은 기각 — 라인 행 단독으로 금액이 자립하지 않아 집계·내보내기·인테이크에서 조인이 강제되고 ADR-0003 ④ 문면과 어긋남).
- 모듈 계층(단방향, 순환 금지 — 아키텍처 테스트로 강제):
  - **L0 `app/modules/trade_docs/`** (커널): `constants`(열거 상수·`DOC_PREFIXES`), `mixins`(헤더·라인 공통 컬럼), `payment_terms`, `incoterms`, `fx`, `money_input`, `snapshot`, `lines`(`require_sellable_sku`), `freeze`(동결 열 집합), `numbering`(`issue_doc_number`), `verify`(합계 검산). 임포트 허용: core, catalog, partners, markets.
  - **L1 `quotations` · `proforma_invoices` · `sales_orders` · `purchase_orders` · `bank_accounts`**: 각자 모델·스키마·서비스·라우터. L0만 임포트(PI는 quotations 모델 참조 없이 FK를 문자열 테이블명으로 선언).
  - **L2 `trade_chain`**: 참조 생성 오케스트레이션(PI←QT, SO←QT/PI), 소비량 집계, `document_flow`, `active_descendants`(역순 취소 가드 질의), 개정. L1 셋을 모두 임포트한다. 이 계층이 따로 필요한 이유: "QT 라인 소비량"은 PI 라인과 SO 라인을 함께 세야 해서 L1 어느 한 곳에 두면 순환이 생긴다.
- `handover/targets.py`에 **4행 등록**(`quotations/proforma_invoices/sales_orders/purchase_orders` × `assignee_id`) — 미등록이면 `test_assignment_coverage`가 실패한다(확인함). 순환 임포트 방향은 handover → 도메인.
- `app/registry.py`에 모델 모듈 등록, `api/router.py`에 `include_router` 추가(`test_every_model_module_is_registered` 존재; 라우터 누락은 조용한 미노출이라 라우트 존재 테스트 추가).

**(d) 4금 저촉** — 없음. 이 안건은 구조만이다. 자동 확정 경로 부재는 B·C 소관 아키텍처 테스트가 고정하고 여기서는 "trade 모듈 안에서 상태 전이 함수 호출 지점 목록"을 그 테스트에 넘긴다.

**(e) 상태·불변** — 상태 열은 `status VARCHAR(20) NOT NULL` + `value_in`(값 목록은 B의 상수 튜플에서 파생). 내가 의존하는 리터럴은 `CANCELLED`·`EXPIRED` 둘뿐(§0-2).

**(f) 테스트**
- K(아키텍처, 자동): 신규 9테이블 `table_policy` 분류 / Float 컬럼 0 / 유니크 키에 금액·민감 이름 0(`test_secret_boundaries` 자동 확장) / 전 FK RESTRICT / FK 열 인덱스 존재 / `assignee_id` handover 등록 / 모델 등록소·라우터 include / **모듈 계층 임포트 방향**(L0는 L1·L2를 임포트하지 않음, L1은 L2를 임포트하지 않음, L1끼리 임포트하지 않음) / **`trade_docs.snapshot`·`lines` 밖에서 `catalog.models.Sku`·`SkuPrice` 임포트 0**(마스터 값 복사 단일 통로) / **`price_type="PURCHASE"` 문자열이 `purchase_orders` 모듈 밖에 없음**(판매 체인이 원가를 읽는 경로 봉쇄).
- 식별자 길이: 제약·인덱스 이름 전수가 63자 이내(수기 산정 최장 58자 — `fk_proforma_invoice_lines_pi_id_currency_proforma_invoices`). **첫 커밋에서 실제 임포트·`alembic check`로 확인 필요(실행 검증 못 했음)**.
- 마이그레이션: 빈 DB `upgrade head` → `downgrade base` → `upgrade head` 왕복, `alembic check` 드리프트 0(복합 FK·부분 유니크 인덱스가 감지 대상 — CHECK는 감지 대상 아님이라 `create_table` 안에 `op.f()` 이름으로 넣고 정의문 테스트로 고정: 함정 ①·⑪).
- 변이 점검: 복합 FK 제거·`RESTRICT`를 `CASCADE`로 바꾸기·handover 등록 1행 제거 → 각각 실패해야 함.

**(g) 소비·등재** — S3-2 shipments가 SO 헤더·라인을 복사 원천으로 소비, S3-3 렌더러가 QT·PI를 소비, S3-4 백오더가 SO 라인 `requested_delivery_date`·PO 라인 수량을 소비. PR 분할 제안(계획 세션이 확정): PR-A(커널+bank_accounts+QT) → PR-B(PI) → PR-C(SO) → PR-D(PO), FK 순서 QT→PI→SO. 각 PR의 마이그레이션은 신규 `create_table`만이고 기존 테이블 변경은 A8·A11의 마스터 마이그레이션 1건으로 분리.

**(h) 되돌리기 비용 — 중간(데이터 0건인 착수 시점은 낮음)**. 컬럼 추가는 nullable `ADD COLUMN` 1건으로 저렴. 라인 `currency` 복합 FK 제거나 PI 라인 신설/제거는 S3-2 소비 시작 후 높아짐. 그래서 첫 PR 전에 확정.

---

## A2. 헤더·라인 컬럼·CHECK·인덱스 전문 (DDL 사양) (제안 A1·A7·X1 통합)

**(a) 목적·경계** — 후속 세션(S3-2/3/4·P4)이 재입력·CHECK 재정의 없이 소비할 열을 지금 완결한다. 소비자가 없는 열은 만들지 않는다.

**(b) DESIGN 근거** — §2 ADR-02, §3 스냅샷, §7.1~7.6, §17.4·17.5(CHECK 가능한 불변식은 CHECK), ADR-0003 ①②④.

**(c) 데이터 변경** — 컬럼 폭은 기존 마스터 폭에 맞춘다(`skus.sku_code` 40·`name_ko/en` 200, `customer_item_codes.buyer_item_code` 100, `partners.name_ko` 200 — 초과는 422).

### 헤더 공통 열 (QT·PI·SO·PO)
| 열 | 타입 | 비고 |
|---|---|---|
| `id` | BIGINT identity PK | |
| `doc_number` | VARCHAR(20) NOT NULL | `unique_active(doc_number)` |
| `doc_date` | DATE NOT NULL | 증빙일(KST 업무일). 날짜 3종 중 증빙일 |
| `status` | VARCHAR(20) NOT NULL | `value_in`(B 확정 열거) |
| `currency` | CHAR(3) NOT NULL | `currency = upper(currency)`, 서비스가 `CURRENCY_MINOR_UNITS` 소속 선검사(미등록 통화가 `price_at`에서 `UnknownCurrencyError`→500이 되지 않게 422) |
| `total_amount` | BIGINT NOT NULL DEFAULT 0 | **PO는 `total_cost`** (A12). 서버 계산값, 요청 스키마에 없음 |
| `fx_rate` | NUMERIC(18,8) NULL | A7 |
| `fx_rate_date` | DATE NULL | A7 |
| `payment_type` | VARCHAR(12) NULL | A5 |
| `advance_pct_bp` | INT NULL | A5 (1%=100bp) |
| `balance_anchor` | VARCHAR(16) NULL | A5 |
| `balance_days` | INT NULL | A5 (−90..365) |
| `incoterm_code` | VARCHAR(3) NULL | A6 |
| `incoterm_place` | VARCHAR(100) NULL | A6 |
| `incoterm_year` | SMALLINT NULL | A6 |
| `assignee_id` | BIGINT FK users RESTRICT NOT NULL | 기본=생성 actor. handover 등록 대상 |
| `note` | VARCHAR(1000) NULL | **내부 메모**(서류에 출력하지 않음 — 동결 제외 열, A3). 서류 비고가 필요하면 S3-3이 `remarks` 열을 별도 추가 |
| `last_line_no` | INT NOT NULL DEFAULT 0 | 라인 번호 카운터(아래) |
| `frozen_at` | TIMESTAMPTZ NULL | A3. **PI·PO는 `NOT NULL DEFAULT now()`**(생성 즉시 발행 — §7.2에 초안 상태 없음) |
| 믹스인 열 | | created_at/updated_at, deleted_at, version, created_by_id/updated_by_id |

### 판매 체인 3종(QT·PI·SO) 추가 열
| 열 | 타입 | 비고 |
|---|---|---|
| `buyer_partner_id` | BIGINT FK partners RESTRICT NOT NULL | 서비스가 `partners.require_partner_of_type(..., "BUYER", field="buyer_partner_id", type_label="바이어")`를 **생성·거래처 변경·동결 전이 3시점**에 호출(사용 시점 재검증 — 유형 해제 후에도 fail-closed) |
| `buyer_name` | VARCHAR(200) NOT NULL | 스냅샷(A8) |
| `dest_market_code` | VARCHAR(2) NOT NULL FK `markets.code` RESTRICT | 목적지 시장. 준비도 게이트(D)·서류가 소비. `markets.service.require_active_market_code`로 검증 |
| `buyer_address` | VARCHAR(500) | **QT·PI만**. QT: NULL 허용(동결 시 필수), PI: NOT NULL. SO는 열 없음 |
| `valid_until` | DATE | **QT·PI만**(A10). SO·PO 없음 |

- **QT 전용**: `revises_qt_id BIGINT FK quotations(id) RESTRICT NULL`(A10).
- **PI 전용**: `qt_id BIGINT NOT NULL FK quotations(id)`(§7.3 "견적 참조" 문면. 완화는 `DROP NOT NULL` 1건이라 저렴하고, 조이는 쪽은 데이터가 쌓이면 어렵기 때문에 좁게 시작), `bank_account_id BIGINT NOT NULL FK bank_accounts(id) RESTRICT`, **은행 스냅샷 6열 전부 NOT NULL**: `bank_beneficiary_name VARCHAR(200)`, `bank_beneficiary_address VARCHAR(300)`, `bank_name VARCHAR(200)`, `bank_address VARCHAR(300)`, `bank_account_no VARCHAR(40)`, `bank_swift_code VARCHAR(11)`. 추가 UNIQUE(id, qt_id)(SO의 복합 FK 대상).
- **SO 전용**: `qt_id BIGINT NULL FK quotations`, `pi_id BIGINT NULL FK proforma_invoices`, 복합 FK `(pi_id, qt_id) → proforma_invoices(id, qt_id)` + CHECK `pi_id IS NULL OR qt_id IS NOT NULL`(MATCH SIMPLE 공백 보강, 검증 #8), `buyer_po_no VARCHAR(60) NULL`, `buyer_po_no_key VARCHAR(60) NULL`(정규화 키, A11 끝의 함수가 채움), CHECK `(buyer_po_no IS NULL) = (buyer_po_no_key IS NULL)`. **직접 인테이크 SO는 `qt_id`·`pi_id` 모두 NULL.** 인테이크 연결 FK·게이트·여신·승인 열은 D·E·C가 additive로 추가.
- **PO 전용**: `supplier_partner_id BIGINT FK partners NOT NULL`(SUPPLIER 또는 OEM 유형 — A12), `supplier_name VARCHAR(200) NOT NULL`. 구매는 목적지 시장·buyer_*·valid_until·revises 없음.
- 4헤더 모두 `UNIQUE(id, currency)`(라인의 복합 FK 대상).

### 라인 공통 열 (QT·PI·SO·PO 라인)
| 열 | 타입 | 비고 |
|---|---|---|
| `id` | BIGINT PK | 안정 id — 초안 편집은 **제자리 UPDATE**, 삭제+재삽입 금지(참조 FK 보존) |
| `qt_id`/`pi_id`/`so_id`/`po_id` | BIGINT NOT NULL | 복합 FK `(hdr_id, currency) → header(id, currency)` **하나로** 헤더 참조+통화 일치 강제. 헤더 통화 변경은 라인이 있으면 FK가 막고 서비스가 "라인을 먼저 모두 삭제하세요"로 번역(가격이 통화별이라 오히려 의도된 동작) |
| `line_no` | INT NOT NULL CHECK ≥1 | 헤더 `last_line_no`를 **헤더 행 잠금 하에서** +1해 부여. 결번 허용·재사용 금지. `unique_active(hdr_id, line_no)` |
| `currency` | CHAR(3) NOT NULL | 위 복합 FK의 일부 + 대문자 CHECK |
| `sku_id` | BIGINT FK skus RESTRICT NOT NULL | 식별용. **서류·표시는 아래 스냅샷 열만 읽는다** |
| `sku_code` / `sku_name_ko` / `sku_name_en` | VARCHAR(40)/(200)/(200 NULL) | 스냅샷 |
| `sku_kind` | VARCHAR(6) NOT NULL | `value_in(SKU_KINDS)` — 세트 여부 스냅샷 |
| `quantity` | INT NOT NULL CHECK BETWEEN 1 AND 99999999 | EA 정수 |
| `requested_delivery_date` | DATE NULL | **SO·PO 라인만** |
| `note` | VARCHAR(500) NULL | 내부 메모(동결 제외) |

- **판매 라인(QT·PI·SO) 추가**: `buyer_item_code VARCHAR(100) NULL`(스냅샷), `unit_price_amount BIGINT NOT NULL`, `list_price_amount BIGINT NULL`(라인 생성 시점의 마스터 표준 판가 스냅샷 — 단가 편차 게이트(D)가 확정 시점에 mutable한 `sku_prices`를 다시 읽지 않고도 "당시 기준가 대비"를 재현하게 하는 열. 마스터에 판가가 없으면 NULL. 나중에 추가하면 백필이 불가능해 지금 둔다), `line_amount BIGINT NOT NULL`, `price_basis VARCHAR(10) NOT NULL`, `is_free BOOLEAN NOT NULL DEFAULT false`, `price_reason VARCHAR(200) NULL`.
- **PO 라인 추가**: `unit_cost BIGINT NOT NULL`, `line_cost BIGINT NOT NULL`, `price_basis VARCHAR(10) NOT NULL`. (`is_free`·`price_reason`·`list_price_amount` 없음 — PO 단가는 양수 강제, A12)
- **출처 참조(판매 라인)**: PI 라인 `qt_line_id BIGINT NOT NULL FK quotation_lines`. SO 라인 `qt_line_id BIGINT NULL FK quotation_lines`, `pi_line_id BIGINT NULL FK proforma_invoice_lines`, CHECK `num_nonnulls(qt_line_id, pi_line_id) <= 1`(**한 줄은 한 원천만 가리킨다** — PI 경유 SO 라인은 `pi_line_id`만 채우고 QT 조상은 PI 라인에서 유도. 조상 이중 기록·불일치 원천 차단, risk 안 채택. 라인 소속(그 PI 라인이 그 SO의 PI에 속함)은 크로스 행이라 서비스 검증+테스트). QT 라인은 출처 열 없음.
- **부호·범위 CHECK**(이름은 `ck_<table>_<name>` 63자 한도 안에서 — `proforma_invoice_lines`는 접두가 26자라 CHECK 이름 ≤37자):
  - `unit_price_amount` ≥ 0 및 ≤ 9007199254740991(JS 안전 정수 — 프런트 `number` 보호), `line_amount`·`total_amount`도 0..2^53−1, `list_price_amount` ≥ 0.
  - **`quantity::numeric * unit_price_amount = line_amount`**(numeric 곱 — 오버플로 500 방지, 검증 #7). PO는 `quantity::numeric * unit_cost = line_cost` 및 `unit_cost > 0`.
  - `is_free = (unit_price_amount = 0)`, `NOT is_free OR (price_reason IS NOT NULL AND btrim(price_reason) <> '')`, `NOT is_free OR price_basis IN ('MANUAL','BUYER_PO')`(마스터 판가 0을 자동 무상으로 받지 않음).
  - `price_basis` = `value_in('MASTER','MANUAL','BUYER_PO')`(PO는 `('MASTER','MANUAL')`).
- **헤더 CHECK**: `currency = upper(currency)` / `total_amount BETWEEN 0 AND 9007199254740991` / `last_line_no >= 0` / `fx_rate > 0 AND fx_rate <= 1000000` / `(fx_rate IS NULL) = (fx_rate_date IS NULL)` / `currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1` / `fx_rate_date <= doc_date` / `valid_until >= doc_date`(QT·PI) / 결제조건 형태 CHECK(A5) / Incoterms 짝·연도 CHECK(A6) / **동결 완결성 `ck_<t>_frozen_complete`**:
  `frozen_at IS NULL OR (payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL AND <판매: btrim(buyer_name)<>''> AND <QT·PI: valid_until IS NOT NULL AND buyer_address IS NOT NULL AND btrim(buyer_address)<>''> AND <PO: btrim(supplier_name)<>''>)`. **상태 리터럴에 의존하지 않고 `frozen_at`에 의존**하므로 B의 열거 확정과 독립이다.
- **인덱스·유니크**(전부 부분 유니크는 `WHERE deleted_at IS NULL`):
  - 헤더: `unique_active(doc_number)`; 일반 `(buyer_partner_id|supplier_partner_id, doc_date)`, `(status, doc_date)`(보드용), `(assignee_id)`, `(qt_id)`(PI·SO), `(pi_id)`(SO), `(revises_qt_id)`.
  - SO: **중복 바이어 PO** — 부분 유니크 `(buyer_partner_id, buyer_po_no_key) WHERE deleted_at IS NULL AND buyer_po_no_key IS NOT NULL AND status <> 'CANCELLED'`(`unique_active` 헬퍼는 술어가 `deleted_at`뿐이라 **직접 `Index` 선언**, 이름 ≤63자). **취소 SO는 번호를 점유하지 않는다** — ADR-05 "정정 = 취소+신규"에 따라 확정된 SO를 바로잡을 때 새 SO가 같은 바이어 PO번호를 달아야 하기 때문(strict의 "취소 SO 점유 유지"는 이 정정 경로를 막아 기각). §17.4 문면의 부분 조건에 `status <> 'CANCELLED'`를 더하므로 **DESIGN §17.4 보강 문단+ADR 필요**.
  - SO: **PI→SO 활성 1:1** `(pi_id) WHERE deleted_at IS NULL AND pi_id IS NOT NULL AND status <> 'CANCELLED'`.
  - QT: **활성 개정본 1개** `(revises_qt_id) WHERE deleted_at IS NULL AND revises_qt_id IS NOT NULL AND status <> 'CANCELLED'`.
  - 라인: `unique_active(hdr_id, line_no)`, `unique_active(hdr_id, sku_id, is_free)`(같은 SKU는 **유상 1줄+무상 1줄까지** — A11), 일반 `(hdr_id)`, `(sku_id)`, `(qt_line_id)`, `(pi_line_id)`.
  - **금액·민감 이름 컬럼은 어떤 유니크 키에도 넣지 않는다**(`test_secret_boundaries`). 은행 계좌번호도 유니크 키 금지(A8).
- **마이그레이션 성격**: 신규 9테이블 `create_table`(CHECK는 `sa.CheckConstraint(..., name=op.f("ck_<t>_<n>"))`로 테이블 정의 안에), 복합 FK는 `ForeignKeyConstraint`. 데이터·시드 없음. **부분 유니크 인덱스의 술어 정의문 테스트**(`pg_get_indexdef`)로 `status <> 'CANCELLED'` 술어를 고정.

**(d) 4금 저촉** — 없음(구조).

**(e) 상태·불변** — CHECK 가능한 것(라인금액=수량×단가, 무상 양방향, 통화 일치, 동결 완결성, 상한)은 전부 DB 이중망. 헤더 합계=Σ라인은 크로스 행이라 CHECK 불가 → A14.

**(f) 테스트**
- A(전표·정합): 모든 CHECK를 **원시 INSERT/UPDATE로 위반시켜 거부 확인 + 같은 조건의 양성 대조 INSERT 1건 성공**(TRUNCATE 하네스 공회전 방지): 라인금액 불일치, 무상 양방향, `is_free`+`MASTER`, 상한 초과(`2^53`), 통화 소문자, KRW에 rate≠1, `fx_rate` 0/음수/1e6 초과, 동결 완결성 열별 결측, 라인 통화≠헤더 통화(복합 FK), 헤더 통화 변경 시도(라인 존재), `(pi_id, qt_id)` 불일치·`pi_id`만 있는 SO, `num_nonnulls` 2, 활성 `doc_number`·PO키·PI→SO·개정본 중복 거부 + **soft delete 후·취소 후 재유입 허용**.
- K: 정의문 테스트(`pg_get_constraintdef`/`pg_get_indexdef` — `test_certification_constraints` 패턴)로 CHECK·부분 유니크 술어 고정. 컬럼 폭 초과(`buyer_po_no` 61자·품번 41자·`doc_number`) 서비스 422(500 0건).
- 변이 점검(생존 0 요구): `line_amount` CHECK 제거 / `numeric` 캐스트를 `bigint`로 되돌리기 / 복합 FK를 단순 FK로 / `frozen_complete` 항 1개 제거 / PI→SO 부분 유니크에서 `status` 조건 제거 / PO키 술어에서 `CANCELLED` 제외 조건 제거.

**(g) 소비·등재** — 동결 열 집합(A3)·소비량 술어(A4)가 이 열 이름을 그대로 참조. 추가 부채: 분할 납기(같은 SKU 다른 날짜 다중 유상 라인) 미지원(A11).

**(h) 되돌리기 비용 — 중간**. CHECK 조정·컬럼 추가는 마이그레이션 1건. 라인 통화 열/복합 FK 철거, `list_price_amount` 사후 추가(백필 불가)가 비싼 쪽 — 지금 확정.

---

## A3. 동결 계약 — `frozen_at`·동결 열 집합·동결 시점 (제안 X2 통합, 강제 수단은 B)

**(a) 목적·경계** — WBS DoD "확정 후 단가·환율 불변"이 검증 가능하려면 "무엇이 언제 불변인가"가 열 단위로 있어야 한다. **A는 표식과 목록을 주고, 막는 수단(서비스 가드·트리거·다이제스트)은 B가 정한다.**

**(b) DESIGN 근거** — WBS S3-1 DoD, §20 A "확정 건 환율·단가 불변", §17.5 "확정 전표 불변"(전표 헤더는 상태 전이 UPDATE가 필요해 DB 권한 차단 불가 — ADR-0028·0040이 트리거를 반복 기각한 선례 확인), ADR-05.

**(c) 데이터 변경** — `frozen_at`(A2). **동결 시점**: QT = 발행(ISSUED 진입) / PI = 생성 즉시 / SO = 확정 / PO = 발행(생성 즉시). `frozen_at`은 동결 전이 트랜잭션에서 한 번 채우고 **지우지 않는다**(취소는 `status`로 표현). 초안 편집 창(QT 작성·SO 접수) 동안은 NULL. **다이제스트(risk의 `frozen_digest`)는 A에서 채택하지 않는다** — 탐지형 보강은 B가 4층 방어 안에서 판단할 사안이고, 열 추가는 additive라 저렴하다.

- `FROZEN_HEADER_COLUMNS`(단일 출처 frozenset, 서비스 가드·아키텍처 테스트·B의 트리거가 같은 집합 공유): `doc_number, doc_date, currency, total_amount|total_cost, fx_rate, fx_rate_date, payment_type, advance_pct_bp, balance_anchor, balance_days, incoterm_code, incoterm_place, incoterm_year, frozen_at` + 판매 `buyer_partner_id, buyer_name, buyer_address, dest_market_code, valid_until, qt_id, pi_id, revises_qt_id, buyer_po_no, buyer_po_no_key` + PI `bank_account_id`·은행 6열 + PO `supplier_partner_id, supplier_name`.
- `FROZEN_LINE_COLUMNS`: 라인의 모든 업무 열(`sku_id`·스냅샷 4열·`buyer_item_code`·`quantity`·`currency`·`unit_price_amount|unit_cost`·`list_price_amount`·`line_amount|line_cost`·`price_basis`·`is_free`·`price_reason`·`requested_delivery_date`·출처 FK·`line_no`) + **라인 추가·삭제 자체**.
- **동결 제외(동결 후에도 변경 가능)**: `status`(전이 서비스), **`assignee_id`**(handover가 필터 없는 일괄 UPDATE를 하므로 트리거를 채택해도 반드시 통과 — 검증 #2), 헤더·라인 `note`(내부 메모), `version`, `updated_at`, `updated_by_id`, `last_line_no`. `deleted_at`은 동결 후 세팅 금지(서비스: 동결 전표는 삭제 불가·취소만).
- **아키텍처 테스트**: 모델의 모든 열은 `FROZEN_*` 또는 `MUTABLE_AFTER_FREEZE` 중 정확히 한 곳에 속한다(신규 업무 열 추가 시 분류 누락 = 실패 — 조용한 미분류 방지).

**(d) 4금 저촉** — 없음. `frozen_at` 설정은 사람이 누른 발행/확정 액션의 결과이며 자동 경로가 없다(SO 확정·PO 발행·QT 발행의 호출 지점이 라우터 밖에 없음을 B가 스캔으로 고정).

**(e) 상태·전이·불변** — 동결 전 편집 자유, 동결 후 위 목록 불변. 되돌림 경로는 "취소+신규"뿐(ADR-05). 재확정(초안 복귀) 경로는 정의하지 않는다 — B가 필요하면 ADR로 `frozen_at` 해제 규칙을 명시해야 한다.

**(f) 테스트**
- A/H: 동결 전표에서 `FROZEN_*` 열 각각 UPDATE 시도 → 서비스 409(B) + (B가 트리거 채택 시) 직접 SQL도 거부 / 동결 제외 열 변경 허용 / **handover 일괄 이관이 동결 전표에서도 성공** / 분류 완전성 테스트 / 양성 대조: 동결 전에는 같은 편집 성공.
- 변이 점검: `FROZEN_*`에서 `total_amount`·`fx_rate` 제외 / `assignee_id`를 동결 집합에 넣기(handover 테스트 실패해야 함) / `frozen_complete` CHECK 제거.

**(g) 소비** — B(가드·트리거), C(승인 후 불변 검증이 같은 집합), S3-2(선적은 SO 확정 후만 복사). 부채: GC-A4 문면("DB 권한 레벨 차단")과의 관계는 B ADR이 명시(판단 이월).

**(h) 되돌리기 비용 — 낮음**(목록 편집=상수+테스트). `frozen_at` 열을 걷어내고 status 기반으로 바꾸는 것은 CHECK 재정의 1건.

---

## A4. 참조 카디널리티·소비 수량·참조 생성 API·복사 후 편집 (제안 A2 통합)

**(a) 목적·경계** — DoD "참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음)"을 **요청 스키마 구조로 보증**한다. 전이 자체(QT→CONVERTED, 취소 가드)는 B, 승인·게이트는 C·D·E.

**(b) DESIGN 근거** — §7.1(잔량이 부분·초과 방지 기준), §7.2, §7.3, ADR-05, §17.2(수주 잔량은 행 잠금 대상), WBS DoD.

**(c) 데이터 변경** — 저장 열은 A2의 참조 FK뿐이다. **잔량·소비량은 열로 저장하지 않고 파생**(SUM) — 유지형 카운터의 불일치 결함 원천 제거.

- **카디널리티**: QT→PI **1:N** / PI→SO **활성 1:1**(A2 부분 유니크, 취소 후 재생성 허용) / QT→SO 직접 **1:N**(PI 없는 후불 거래) / SO의 `qt_id`·`pi_id` NULL 허용(직접 인테이크) / **PI의 `qt_id`는 NOT NULL**(직접 PI 발행 경로 없음 — PI는 QT에서만 만든다. 견적 없이 PI가 필요하면 QT 작성·발행 후 참조 — QT는 외부 송부 없이도 발행 가능). SO가 PI를 참조하면 `SO.qt_id = PI.qt_id`(서비스가 복사, 요청 스키마에 없음, DB가 복합 FK로 강제).
- **소비량 산식(단일 함수 `consumed_qty(session, line_ids)`, 다른 곳에서 재구현 금지 — 스캔)**: 원천 라인 소비량 = Σ 활성 후속 라인 수량. QT 라인 = Σ(활성 PI 라인 중 `qt_line_id`=그 라인) + Σ(활성 SO 라인 중 `qt_line_id`=그 라인, 즉 직접 경로). PI 라인 = Σ(활성 SO 라인 중 `pi_line_id`=그 라인). 조건: 후속 수량 합 ≤ 원천 수량. **"활성" = `deleted_at IS NULL` 이고 상태가 `CANCELLED`·`EXPIRED`가 아닌 전표의 라인** — 초안(QT 작성 아님, SO 접수 포함)도 소비로 센다(동시 다건 생성의 초과 방지). **만료·취소 PI의 수량은 QT로 환원**된다(3안이 놓친 점 — 만료 PI가 QT 수량을 영구히 잡고 있으면 QT가 죽은 재고가 됨).
- **참조 생성 API** (본문은 `extra=forbid`, 원천에 있는 값을 다시 받는 필드는 스키마에 **존재하지 않는다** — SKU·단가·통화·환율·거래처·시장·스냅샷 전부 없음):
  - `POST /v1/quotations/{qt_id}/proforma-invoices/preview` · `POST /v1/quotations/{qt_id}/proforma-invoices`
    본문 `{expected_version(원천 QT 헤더), doc_date?(기본 오늘 KST), valid_until(필수), bank_account_id(필수), lines?:[{source_line_id, quantity}](생략=전 라인 잔량 전부), overrides?:{payment_terms?, incoterms?, note?, assignee_id?}}`.
    **PI는 §7.2에 초안 상태가 없어 생성 즉시 발행·동결**이므로 **비저장 미리보기**(`/preview` — 채번·감사·이벤트·멱등키 소비 없음)로 편집 요구를 충족한다. 재정의 화이트리스트에 **환율·통화·단가·거래처·시장은 없다**(협상은 QT에서, 가격 변경은 새 QT).
  - `POST /v1/proforma-invoices/{pi_id}/sales-orders` · `POST /v1/quotations/{qt_id}/sales-orders`
    본문 `{expected_version, doc_date?, buyer_po_no?, lines?:[{source_line_id, quantity, requested_delivery_date?}], assignee_id?, note?}`. SO는 접수(INTAKE) 상태가 편집 가능 초안이라 미리보기가 없고, 조건·환율·단가 조정은 생성 후 **SO 편집 API**(접수 상태 한정)로 한다.
  - 모두 **`Idempotency-Key` 필수**(`idempotency.claim/complete`, 재수신=최초 결과). 미리보기는 키 불필요.
- **생성 알고리즘(한 트랜잭션, 서비스 `trade_chain.create_from_reference`)**: ① 원천 헤더 `FOR UPDATE` + `expected_version` 검사(409) ② **원천 자격 검사** ③ 원천 라인을 id 오름차순 `FOR UPDATE` → `consumed_qty` 집계 → 요청 수량 ≤ 잔량(초과 409 `TRADE.REFERENCE.QTY_EXCEEDS_SOURCE`, detail에 원천 수량·기소비·요청) ④ **선행 전표 라인에서 값 복사(마스터 재조회 금지)** — `price_basis`·`list_price_amount`·`is_free`·`price_reason`도 복사, **`line_amount`는 복사가 아니라 새 수량×복사 단가로 재계산** ⑤ SKU 재검사(A11) ⑥ 마지막에 채번(카운터 행 잠금 시간 최소화, A13) → 헤더·라인 INSERT → `total` 재계산·상한 검증 → audit+outbox(ID만) ⑦ 멱등 완료.
- **원천 자격**: QT = 참조 허용 상태(발행 또는 수주전환됨 — B 열거) **그리고 `valid_until >= today_kst()`**(상태만 믿지 않음: 만료 잡이 아직 안 돌았어도 막는다). PI = 참조 허용 상태(발행·일부입금·입금완료) **그리고 (상태가 미입금 발행이면 `valid_until >= today_kst()`, 일부입금·입금완료면 유효기간 면제** — 이미 입금한 바이어의 SO 생성을 유효기간이 막아 선수금이 갇히는 사고 방지). 초안 원천은 참조 불가. 위반 409 `TRADE.REFERENCE.SOURCE_NOT_ELIGIBLE`(사유는 한국어 detail).
- **복사 후 편집 범위**: PI(동결 생성)는 미리보기 폼에서만 위 화이트리스트를 조정한다. SO(접수)는 수량(≤잔량)·단가·조건·환율·요청납기·비고·담당자·거래처 외 편집 가능 — 단, **`sku_id`는 변경 불가**(라인 삭제 후 신규 추가만), **거래처·통화·목적지 시장 변경은 참조 SO에서 금지**(원천과 어긋나면 참조가 무의미), 참조 SO에 원천에 없는 SKU 라인 추가 금지(추가 품목은 새 QT 개정). 단가를 바꾸면 `price_basis='MANUAL'`(원출처가 `BUYER_PO`이고 값 불변이면 유지). 라인 삭제(제외)는 가능하며 소비량에서 즉시 빠진다.
- **원천 대비 차이는 저장하지 않고 조회 시 계산**한다: 상세 응답 라인에 `source: {quantity, unit_price_amount}`와 파생 `quantity_delta`·`price_changed`(배치 조회 1회 — N+1 금지). 원천 라인은 동결 후 불변이라 재현 가능.
- **초안 편집·동결 재검증**: SO 접수 편집에서 수량을 바꿀 때와 **SO 확정(동결) 전이 시** 원천을 다시 잠그고 자격(취소·개정·만료 발생 여부)과 잔량을 재검증한다(초안 생성 후 원천이 바뀌거나 다른 초안이 소비한 경우 방지 — S1-3 PR-3 "확정 시점 재검증 부재" 재발 방지).
- **QT 수주전환(CONVERTED) 진입 시점 = 그 QT를 원천으로 하는(직접 또는 PI 경유) SO가 처음 확정되는 트랜잭션** — PI 생성·SO 접수만으로는 전환하지 않는다(PI는 오퍼 회신, SO 접수는 게이트 전이라 수주 성립 아님). 전이 실행은 B. CONVERTED에서 역전이는 없으며(§7.2에 CONVERTED→CANCELLED 없음), 잔량과 유효기간이 남아 있으면 추가 PI/SO 생성을 허용한다.
- **역순 취소 가드 질의**: `active_descendants(doc)`(QT→PI·SO·개정본, PI→SO)를 L2에 제공 — B가 취소 전이에서 호출(DRAFT/INTAKE 후속 포함, 삭제·취소·만료 후속 제외).
- **문서 흐름 조회**: `document_flow(doc_type, id)` — FK로 QT→PI→SO 상·하 양방향 탐색(§14 ⑦ 계약, 보드·상세 소비).

**(d) 4금 저촉** — 참조 생성은 항상 사람 요청이며 새로 만든 전표는 PI(발행=대외 송부 아님, 파일 생성일 뿐 발송은 S3-3 이후 사람)·SO 접수 상태다. 어떤 경로도 SO 확정·PO 발행을 호출하지 않는다.

**(e) 상태·불변** — 원천은 동결 상태여야 참조 가능(초안 참조 금지), 참조 생성은 원천 행을 **수정하지 않는다**(잠금+읽기) → 원천 version 불변.

**(f) 테스트**
- A: **관통 e2e** — 요청 본문에 `sku_id/unit_price_amount/currency/fx_rate/buyer_partner_id/payment_*/incoterm_*` 키가 0개이고 QT→PI→SO를 `원천 id + valid_until + bank_account_id`만으로 관통, SO 라인이 QT 스냅샷과 값 일치 / **요청 스키마 필드 집합 스냅샷 테스트**(허용 목록과 정확히 일치) / 수량 경계(잔량 ==, +1, 무상 라인 포함) / QT 100라인=PI 60+SO 직접 40 허용·1 초과 422→409 / 만료·취소 PI의 수량 환원 / PI→SO 활성 2건 거부·취소 후 재생성 허용 / 원천 상태·유효기간 경계(당일 허용·다음 날 거부, 잡 미실행 상태에서도) / 일부입금 PI는 유효기간 경과 후에도 SO 생성 허용 / PI 미리보기가 번호·감사·이벤트·멱등 레코드를 만들지 않음 / 원천 값이 마스터 변경 후에도 유지(마스터 판가 바꾼 뒤 PI 생성해도 QT 단가) / 단가 재정의 필드 존재 시 422(`extra=forbid`) / 참조 SO에 원천 외 SKU 추가·거래처 변경 거부.
- J(안전): 같은 키 재전송=동일 결과·전표 1건 / **동시성**(`concurrency` 마커, 실커밋): 실스레드 2개가 같은 QT에서 잔량을 넘겨 PI 2건 생성 → 정확히 1건 성공 / 동시 PI→SO 2건(다른 키) → 1건만 / SO 접수 수량 편집과 다른 참조 생성 동시 → 총 소비 ≤ 원천 / 교착 0(A13 잠금 순서).
- H: 확정(동결) 시 원천 재검증(초안 생성 후 원천 취소 → 확정 거부).
- 변이 점검: 소비 술어에서 초안 제외·`EXPIRED` 환원 제거·원천 라인 `FOR UPDATE` 제거·PI 경유 SO 라인 이중 계산·`valid_until` 직접 검사를 상태만 보는 검사로 변경·미리보기에서 INSERT 남기기 → 각각 실패.

**(g) 소비·등재** — B: 취소 가드·CONVERTED 전이·상태 리터럴. D: 인테이크가 같은 소비 함수를 재사용. 관찰: 미리보기 API는 PI·PO의 "초안 상태 부재"를 메우는 A의 설계 결정이므로 DESIGN §7.2 보강 문단에 근거 1줄.

**(h) 되돌리기 비용 — 중간~높음**. 요청 계약과 수주전환 시점은 프런트·테스트가 즉시 의존한다. PI→SO를 1:N으로 푸는 것은 부분 유니크 1개 제거+입금 귀속 재정의(중간). 수주전환 시점을 SO 접수로 앞당기는 것은 서비스 1곳.

---

## A5. 결제조건 — 구조화 4열 (제안 A3 통합)

**(a) 목적·경계** — S3-2 대금만기 계산이 소비할 결제유형·기산점·일수를 지금 열거로 완결한다(S3-2가 CHECK를 재정의하지 않도록). L/C 기일(네고·인수)은 S3-3 `lc_terms` 소관이라 전표에 복사하지 않는다.

**(b) DESIGN 근거** — §7.1 "결제조건은 구조화(기산점+일수) — 자유 텍스트 금지", §7.3 "선수금%·잔금 기산점", §7.5 결제유형 분기(T/T=약정 기산점 / L/C=네고·인수), §7.10, §3 feature flag(`lc_terms`).

**(c) 데이터 변경** — A2의 4열(QT·PI·SO·PO 동일 형태, 자유 텍스트 열 없음, 거래처 기본값 열 없음 — 마스터 확장은 DESIGN+ADR 세트라 근거 없이 하지 않고 UI 미리채움은 프런트 편의로만).
- `payment_type` = `value_in('TT_ADVANCE','TT_DEFERRED','LC')`: 선수금 T/T(선수금 후 잔금·100% 선수금 포함) / 후불 T/T / L/C. **'기타(OTHER)'는 만들지 않는다** — 계산 의미가 없는 죽은 값이고 자유 텍스트 우회 입구가 됨. 신규 유형은 CHECK 마이그레이션+ADR로만(fail-closed).
- `advance_pct_bp` INT — **basis point**(1%=100, 30%=3000, 100%=10000). API는 **퍼센트 문자열**(`"30"`, `"33.33"`, 소수 2자리까지, 초과 422 — 반올림 안 함)로 받고 서버가 bp로 변환, 응답에 `advance_pct`(문자열)와 `advance_pct_bp`(정수) 병기. **프런트는 산술 0**(검증 #9 가드 통과).
- `balance_anchor` = `value_in('ORDER_DATE','INVOICE_DATE','ETD_DATE','BL_DATE','ARRIVAL_DATE','RECEIPT_DATE')`: 주문일(SO 확정일 KST; PI·QT 단계에서는 발행일 — S3-2가 SO 확정일 기준으로 계산) / 인보이스일(CI 발행일) / **ETD(출항 예정→실적)** / B/L(또는 AWB)일 / 도착일(ETA→실적) / **입고 확정일(PO 전용, 판매 체인에서 선택하면 422)**. ETD를 별도 앵커로 둔 이유: "선적 전 잔금(before shipment)"이 T/T 실무에서 가장 흔한 조건이며 §7.5가 실적 입력 시 후속 재계산을 요구한다. S3-2는 각 앵커를 마일스톤에 매핑만 하고 열거를 재정의하지 않는다.
- `balance_days` INT CHECK BETWEEN −90 AND 365. **음수는 `ETD_DATE`에서만**("선적 N일 전 잔금"). 대금만기 = 앵커 일자 + `balance_days`(달력일 — 휴일 보정은 S3-2). 앵커 일자가 아직 없으면 S3-2가 "계산 불능(앵커 미확정)"으로 표시한다.
- **형태 CHECK `payment_terms_shape`(행 단위, 한 식)**: 4열 전부 NULL(동결 전 초안 허용) OR (`TT_ADVANCE` AND `advance_pct_bp BETWEEN 1 AND 10000` AND ((`advance_pct_bp=10000` AND anchor·days NULL) OR (`advance_pct_bp<10000` AND anchor·days NOT NULL))) OR (`TT_DEFERRED` AND advance NULL AND anchor·days NOT NULL) OR (`LC` AND advance·anchor·days NULL). 음수 days ⇒ anchor=`ETD_DATE` CHECK 별도(`ck_<t>_neg_days_etd_only`). 동결 시 `payment_type NOT NULL`은 `frozen_complete`가 강제(A2). 서비스: `RECEIPT_DATE`는 PO에서만.
- **선수금 계산은 저장하지 않고 함수**(파생값 이중 저장 금지): `split_advance(total: Money, advance_pct_bp) -> (advance: Money, balance: Money)`, 선수금 = `(total.amount * bp + 5000) // 10000`(파이썬 정수, ROUND_HALF_UP과 동치), **잔금 = 총액 − 선수금**(합 일치 보장). SQL에서 곱하지 않는다(`total`(2^53) × 10^4가 BIGINT를 넘김).
- **L/C와 feature flag `lc`**: DB CHECK는 항상 LC를 허용(스키마가 플래그에 종속되지 않게). **서비스가 `payment_type`에 'LC'를 새로 *입력*(생성·편집)할 때** `feature_flags`의 `lc` 행 조회 — **행 없음·꺼짐·조회 실패는 모두 꺼짐(fail-closed)** → 422 `TRADE.PAYMENT.LC_DISABLED`. **참조 복사로 상속되는 LC는 검사하지 않고 동결·조회·진행도 검사하지 않는다**(플래그를 끄더라도 진행 중 거래가 갇히지 않게 — strict의 "동결 시 재검사"·risk의 "복사 시 차단"보다 좁은 규칙). 읽기 헬퍼 `platform.service.is_feature_enabled(session, code) -> bool`(fail-closed)을 신설한다.
  - **인정하는 결과**: 현재 코드에는 플래그 행을 만드는 경로(API·CLI)가 없다(검증 #12). 따라서 **S3-1 프로덕션에서는 L/C 선택이 닫혀 있고**, 테스트는 픽스처로 행을 넣어 양방향을 검증한다. 플래그 토글 경로는 S3-3(`lc_terms`와 함께 열림)에 인계 — 부채로 등재. (오너가 S3-1 시점에 L/C를 필요로 하면 CLI `set-feature-flag` 소단위 1건 추가로 해소 — 되돌리기 비용 낮음.)

**(d) 4금 저촉** — 없음.

**(e) 상태·불변** — 동결 열(A3). 초안(QT 작성·SO 접수)에서 편집 가능, 원천→후속은 복사, PI는 미리보기 화이트리스트에서만 조정(A4).

**(f) 테스트**
- A: 유효 조합(30%+기산점, 100%+NULL, 후불, LC) 저장 성공 / 무효 조합 각각 **DB CHECK 원시 INSERT 거부 + 서비스 422 둘 다**(bp 0·10001, 100%인데 앵커 있음, <100%인데 앵커 없음, 후불에 선수금, LC에 일수, 음수 일수+ETD 외 앵커, 판매 체인 `RECEIPT_DATE`) / `split_advance` 합=총액(홀수 금액·bp 3333·JPY 0자리·USD 2자리·GC-G1 계열, `.5` 경계) / 원천→후속 복사 값 동일 / 동결 후 변경 거부.
- H: LC 플래그 없음/꺼짐/켜짐 3경우(생성 422·켜짐 통과·꺼진 뒤 기존 LC 전표 조회·진행 유지·상속 복사 통과) / `is_feature_enabled` 조회 실패(예외) → 꺼짐.
- K: 신규 모델·스키마에 자유 텍스트 결제조건 필드 부재 스캔 / 프런트 산술 가드 통과(퍼센트 문자열 왕복).
- 변이 점검: `payment_terms_shape` 제거 / 플래그 조회 기본값을 True로 뒤집기 / `split_advance`를 잔금=반올림 방식으로 변경 / bp 변환에서 반올림 허용.

**(g) 소비** — S3-2 대금만기(앵커·일수), E PI 입금 게이트(`TT_ADVANCE`일 때만 활성 — "선수금 T/T일 때만" §7.3, `split_advance` 사용, `total=0`인 TT_ADVANCE는 게이트 통과가 아니라 "선수금 0" 명시 처리 E가 결정). 부채: 3분할 이상 분할결제·D/P·D/A 미지원(필요 시 신규 테이블/CHECK 확장).

**(h) 되돌리기 비용 — 중간**. 열거 추가는 CHECK 재정의 1건(선례 있음). 삭제·의미 변경은 S3-2 소비 후 비쌈 → 지금 6앵커 확정. bp→소수 %는 이미 bp라 불필요.

---

## A6. Incoterms (제안 A4 통합)

**(a)** QT·PI·SO·PO 4헤더에 3열. 선적(S3-2)은 SO에서 복사만 하고 재입력하지 않는다. 완전성 검증 본체는 S3-3.
**(b)** §7.5 shipments "(Incoterms+장소·연도)", §7.6 "Incoterms 완전성", §10.1 부담자 기본값, ADR-05.
**(c)** `incoterm_code` = `value_in('EXW','FCA','FAS','FOB','CFR','CIF','CPT','CIP','DAP','DPU','DDP','DAT')`(2020판 11종 + 2010판 DAT), `incoterm_year` = `value_in(2010, 2020)`(CHECK `IN (2010, 2020)`), 교차 CHECK: `DAT ⇒ year=2010`, `DPU ⇒ year=2020`. **연도 열을 2020 단일값으로 고정하는 risk 안은 기각** — §7.5 문면이 연도를 데이터로 요구하므로 상수열이면 죽은 열이다. `incoterm_place` VARCHAR(100), CHECK `btrim(place) <> ''`(제어문자·개행은 서비스 422). **셋 다 NULL이거나 셋 다 NOT NULL**(`incoterm_all_or_none`), 동결 시 `code` NOT NULL(A2 `frozen_complete`) — QT는 작성 중 미정 허용. API 입력 `IncotermIn{code, place, year=2020 기본}`. 자리표시자('TBD'·'N/A') 거부는 임의 정책이라 하지 않는다(완전성은 S3-3). PO는 판매 체인과 같은 3열(수입 선적이 복사 원천으로 소비).
**(d)** 없음(법적 판정 아님 — 값 입력·형식 검증만, 판정 문구 금지).
**(e)** 동결 열(A3).
**(f)** A: 12코드 저장·미지 코드·소문자·DAT+2020·DPU+2010·place 공백·일부만 입력 거부(DB+서비스) / 복사 사슬 동일 / 동결 후 변경 거부 / 필드 집합 스캔. 변이: 열거 1종 제거·교차 CHECK 제거.
**(g)** S3-2 shipments 초안이 SO 3열 복사, S3-3 완전성 검증이 읽음, S3-4 부담자 기본값.
**(h)** 낮음(코드 추가=CHECK 1건, 판 추가 동일). 열 위치(4헤더)만 비쌈 — 확정.

---

## A7. 통화·환율 — 헤더 스냅샷만, `fx_rates` 마스터는 S3-1에서 신설하지 않는다 (제안 A5 통합; 3안 중 risk 안 기각)

**(a) 목적·경계** — DoD "확정 후 환율 불변"의 대상(저장 위치·방향·정밀도·검증)과 통화 불일치 비교 최소 규약을 정한다. 환율 수집(§15 L3·ADR-06 2단계)·환노출(S6-2)은 범위 밖.

**(b) DESIGN 근거** — ADR-02 "금액=정수+통화+환율+기준일", §3 [공통] 표 맵의 `fx_rates`(배정 세션 없음), §7.5 "환율 고정"(S3-2 — SO 스냅샷을 복사하면 충족), §12 ADR-06, ADR-0048("환율 축이 없어 환산은 추측이다" — 프로젝트가 축 부재를 공식 인정).

**(c) 데이터 변경** — 전표 통화는 **헤더 단일**(라인은 복합 FK로 강제 일치, A2). 헤더 `fx_rate NUMERIC(18,8)`, `fx_rate_date DATE`(A2 CHECK).
- **방향·의미**: "**전표 통화 1단위 = x KRW**"(KRW per 1 unit, 한 방향만 저장 — 역수 혼동 원천 차단). 8자리 소수는 VND·IDR 등 저가 통화의 유효숫자 손실(6자리 시 상대오차 ~2e-5)을 줄인다. 상한 1,000,000(서비스).
- **입력**: **수동 입력**(비KRW 동결 시 필수, `Decimal` 문자열 입출력 — float 파싱 금지, pydantic `max_digits=18, decimal_places=8`), `currency='KRW'`이면 서버가 `fx_rate=1`, `fx_rate_date=doc_date`를 채움(CHECK와 일치). `fx_rate_date <= doc_date`(CHECK). 출처 열은 만들지 않는다(누가·언제는 ActorMixin+audit_log, 단일값 열거는 죽은 값 — 마스터 도입 시 additive로 `fx_rate_source` 추가). **신선도(stale) 서버 규칙은 두지 않는다** — 참조 생성은 원천의 환율·기준일을 복사하므로(PI는 QT 환율, SO는 PI/QT 환율) 수 주 지난 값이 정상이다. 대신 **SO 접수 편집에서 환율을 갱신할 수 있고(확정 시 동결)**, 화면이 "기준일이 N일 지남"을 **파생 표시**한다(서버 계산 필드가 아니라 프런트 표시, 산술 가드 준수를 위해 서버가 `fx_rate_age_days` 정수 제공).
- **`fx_rates` 마스터를 신설하지 않는 이유(risk 안 기각)**: ① 소비자가 없다 — 환율의 사용처는 (i) 확정 후 불변(스냅샷으로 충족) (ii) 승인 임계·여신·보드의 KRW 환산뿐이고 (iii) 환율 수집·이력·출처 정책은 §15 L3·ADR-06 2단계·S6-2 소관이라 마스터를 지금 세우면 그 정책을 조기 확정하며 S6-2와 충돌할 수 있다. ② risk가 든 "임의 환율로 여신·결재 임계 우회" 우려는 실재하지만 **risk 안(마스터 등록 권한 ADMIN·TRADE)도 TRADE가 환율을 등록할 수 있어 우회를 닫지 못한다** — 우회를 진짜 닫으려면 ADMIN 전용 등록+주기적 갱신 운영 부담(소규모 팀의 주 1회 이상 환율 등록)이 생긴다. 이 우려는 (i) 환율 입력·변경이 audit_log에 남고 (ii) 승인·여신 증빙에 **사용한 환율·기준일을 값으로 남기게 하는 계약**(E·C에 요구, 아래)으로 fail-visible하게 다루고, 마스터 도입은 재판정 트리거를 걸어 이월한다.
- **환산·비교 규약(함수, 저장 없음 — 파생값 이중 저장 금지)**: `trade_docs.fx.to_krw(amount_minor: int, currency: str, fx_rate: Decimal) -> Money` = `Money.from_decimal(Decimal(amount).scaleb(-minor_units(currency)) * fx_rate, 'KRW')`(ROUND_HALF_UP 단일 — `Money.from_decimal`의 규칙 재사용). `comparable_amount(amount_minor, doc_currency, fx_rate, target_currency) -> int | None`: 같은 통화면 원액, `target='KRW'`이고 `fx_rate`가 있으면 `to_krw`, **그 외(예: 한도 USD·전표 EUR, 전표 KRW·한도 USD)는 `None` = 비교 불가**. **`None`은 통과가 아니다** — 소비자(여신 게이트 E·결재 임계 C)는 `None`이면 승인 게이트로 보내고 사유 코드를 남긴다(fail-closed·fail-visible). 임의 역환산·제3통화 환산을 하지 않는다.
- 통화 검증: 요청 통화는 `CURRENCY_MINOR_UNITS`에 등재된 코드만(선검사 → 422, `UnknownCurrencyError`→500 방지).
- **E·C에 요구하는 증빙 계약**: 여신·승인 판정 결과에 `(doc_id, currency, total, fx_rate, fx_rate_date, 비교 결과/None 사유)`를 값으로 남긴다.

**(d) 4금 저촉** — 없음(계산 함수, 자동 확정 없음).

**(e) 상태·불변** — `currency·fx_rate·fx_rate_date`는 동결 열(A3). QT 발행·SO 확정·PO 발행 이후 변경 불가, 변경은 취소+신규.

**(f) 테스트**
- A/K: KRW=1 자동·KRW에 rate≠1 CHECK 거부 / 비KRW 동결 시 rate 없음 422·DB `frozen_complete` 거부 / rate 0·음수·1e6 초과·소수 9자리 거부 / **Decimal 문자열 왕복 무손실**(NUMERIC 정밀) / 미등록 통화 422(500 아님) / 미래 `fx_rate_date` 거부(`fx_rate_date > doc_date`) / `to_krw` HALF_UP 경계(USD→KRW, JPY 0자리, 큰 금액 2^53 근처) / `comparable_amount` 3분기 전건(동일 통화·KRW 한도·`None`) / 동결 후 환율 변경 거부 / Float 컬럼 0.
- 시각: KST 00:30(UTC 전일 15:30) 고정 시계에서 `doc_date=today_kst` 허용.
- 변이 점검: 방향을 나누기로 뒤집기 / `None`을 0으로 대체 / KRW 분기 제거 / `to_krw` 반올림 방향 변경.

**(g) 소비·등재** — E 여신 노출·C 임계 비교·S3-2 shipments 환율 고정(SO 스냅샷 복사)·S6-2 환노출. **WBS v1.5에 "fx_rates 소유 세션 미배정 — 재판정 트리거: 환율 자동 수집 도입(§15 L3) 또는 S6-2 착수 또는 환율 입력 오류 사고 발생"으로 등재.** 신규 ADR(환율 규약) 필요. DESIGN §3 표 맵 `fx_rates` 항에 "S3-1은 전표 헤더 스냅샷까지, 마스터는 미신설" 주석.

**(h) 되돌리기 비용 — 중간**. 마스터 신설·출처 열은 additive(저렴)라 전표 컬럼은 불변. **방향·정밀도를 바꾸는 것은 전 전표 값 변환+소비 코드 수정으로 비싸다 — 지금 확정**.

---

## A8. 스냅샷 규약·마스터 보강 (partners 2열 + `bank_accounts`) (제안 A6 통합)

**(a) 목적·경계** — "거래 시점 값 스냅샷 복사"의 범위·시점·원천을 확정하고, 원천 공백(거래처 영문명·주소, 자사 은행)을 최소 마스터 변경으로 메운다. **자사 레터헤드(상호·주소·로고·사업자번호)는 S3-1 스냅샷 대상이 아니다** — 렌더링 자산이며 S3-3이 필요 시 additive로 정의(인계·부채).

**(b) DESIGN 근거** — §3 "거래 시점 값 스냅샷 복사", §7.3 "은행정보 스냅샷", ADR-0017("이력과 스냅샷은 값 복사" — `sku_prices`는 MUTABLE이라 과거 `price_at`이 재현되지 않으므로 전표 라인이 진실), §4.6, ADR-05, CLAUDE.md "마스터 변경 = DESIGN 갱신+ADR 세트".

**(c) 데이터 변경**
- **스냅샷 원칙**: 라인·헤더 모두 **값 복사 열**이다(문서에 원천 마스터 FK는 식별·추적용으로만 남기고 조회·렌더링은 스냅샷 열만 읽는다). **시점**: 마스터에서 직접 만드는 경로(QT 라인 추가, 직접 SO/PO 라인, PI 은행)는 **생성(추가) 시점에 1회 복사**, 초안 편집 창에서는 사용자의 명시 편집·거래처 변경으로만 값이 바뀌고(자동 갱신 없음, 거래처 변경 시 `buyer_name`·`buyer_address` 재복사), **참조 생성(PI·SO)은 선행 전표 값을 그대로 복사하고 마스터를 다시 읽지 않는다**, 동결 시점부터 불변. "마스터 대비 변경됨" 배지·수동 새로고침 액션은 만들지 않는다(risk 안 미채택 — 복잡도 대비 근거 없음, 관찰 등재).
- **라인 스냅샷**: `sku_code·sku_name_ko·sku_name_en·sku_kind·buyer_item_code·unit_price_amount·list_price_amount·currency·price_basis`. 두 함수만이 라인 스냅샷을 만든다: `snapshot_line_from_master(session, buyer_partner_id, sku_id, currency, doc_date) -> LineSnapshot` / `snapshot_line_from_source(source_line) -> LineSnapshot`.
- **`buyer_item_code` 결정 규칙**: 라인 추가 시 `(buyer_partner_id, sku_id)`의 활성 `customer_item_codes`가 **정확히 1건이면 그 값, 0건이면 NULL, 2건 이상이면 사용자가 그 바이어의 활성 매핑 집합에서 직접 지정(미지정이면 NULL — 추측 선택 금지, "가장 작은 id" 같은 임의 규칙 기각)**. 인테이크 경로는 매칭에 쓰인 바이어 품번을 그대로 스냅샷(D). 세트 구성품은 스냅샷하지 않는다(A11).
- **헤더 스냅샷**: `buyer_name`(기본 = `COALESCE(partners.name_en, partners.name_ko)`, 편집 가능), `buyer_address`(QT·PI), `dest_market_code`, `currency`, 환율·결제조건·Incoterms(A5~A7), PO `supplier_name`.
- **partners 확장(마스터 변경 ①)** — 신규 nullable 2열: **`name_en VARCHAR(200)`, `address_en VARCHAR(500)`**(country 열은 만들지 않음 — 주소 문자열에 포함, 목적지는 `dest_market_code`가 소유; 선적 당사자(consignee/notify)는 S3-2 소유). 이유: QT·PI 서류가 필요로 하는 값이 마스터에 없고, 전표마다 손으로 입력하면 같은 바이어에 대해 재입력이 반복된다(ADR-05 정신 위반). 전표 컬럼은 어차피 스냅샷 열이라 **마스터 확장은 편의만 추가하는 additive**이지만, 지금 하지 않으면 첫 사용자가 "매번 주소를 쳐야 하나"로 되묻는다. 세트: **DESIGN §4.6 보강 + ADR 5줄 + 마이그레이션(nullable 2열, `op.f()` CHECK 없음) + `PartnerCreate/Update` 스키마(`extra=forbid` 유지) + `PartnerView` + CSV 왕복 임포트 어댑터(`imports/registry.py` `PartnersImportTarget` 열 추가·왕복 diff 테스트 갱신) + 거래처 화면 필드**. 거래처 수정 API는 신설하지 않는다(CSV 왕복이 지정 경로 — PROGRESS:119 갭의 재판정: "화면 신설 안 함, 부채 유지").
- **은행 원천 = 신설 마스터 `bank_accounts`**(§7.3이 "은행정보 스냅샷"을 명시하는데 원천 마스터가 0건): `id`, `label VARCHAR(80) NOT NULL`(`unique_active`), `currency CHAR(3) NOT NULL`(대문자 CHECK), `beneficiary_name VARCHAR(200)`, `beneficiary_address VARCHAR(300)`, `bank_name VARCHAR(200)`, `bank_address VARCHAR(300)`, `account_no VARCHAR(40)`, `swift_code VARCHAR(11) CHECK ~ '^[A-Z0-9]{8}([A-Z0-9]{3})?$'` 전부 NOT NULL + 전 믹스인. **`account_no`는 어떤 유니크 키에도 넣지 않는다**(`test_secret_boundaries`가 실패시킴 — 중복 계좌는 서비스가 (정규화 계좌번호, SWIFT)로 검사해 409, 경합 잔여 위험은 ADMIN 전용 저빈도라 관찰 등재). CRUD 4개(등록·목록·수정 `expected_version`·soft delete=비활성), **쓰기 ADMIN 전용, 조회 ADMIN·TRADE**(PI 발행 선택용). 초기 행은 앱 경로(화면/API)로만 — 마이그레이션 시드 금지(함정 ⑩).
- **PI 생성 시**: `bank_account_id` 필수 지정 + **계좌 통화 = PI 통화일 때만 허용**(불일치·미존재·비활성 → `VALIDATION_INVALID_FIELD` detail `{bank_account_id: ...}` — 잘못된 통화 입금 안내 방지, 해당 통화 계좌가 없으면 "계좌를 먼저 등록하세요" 안내) + 은행 6열 값 복사. 이후 마스터를 수정·비활성해도 발행 PI 불변.
- **로그·이벤트 마스킹**: `redaction.SENSITIVE_SUFFIXES`에 `_account_no`, `_account_number` 추가(검증 #6 — `bank_account_no` 자동 마스킹), audit·events·알림 본문에는 계좌번호를 싣지 않는다(ID만). 은행정보는 원가·마진이 아니므로 역할 마스킹 비대상(전표의 일부이며 바이어에게 발송되는 값).
- 동결 요건(S3-3 소비 최소): QT·PI 동결 = `buyer_name`·`buyer_address` 비공백 필수(부재=차단, 마스터 입력 안내 — 평가 불능을 통과로 두지 않음), PI 추가로 은행 6열 전부, SO 확정 = `buyer_name`만.

**(d) 4금 저촉** — 없음.

**(e) 상태·불변** — 스냅샷 열은 동결 열 집합(A3). 마스터 변경이 과거 전표에 영향을 주지 않음이 핵심 불변.

**(f) 테스트**
- A/H: **마스터(SKU명·판가·바이어 품번·거래처 영문명·주소·은행 계좌)를 수정한 뒤에도 발행된 전표 값 불변** / 참조 생성이 마스터 변경 후에도 선행 값 복사(재조회 없음 — 마스터 판가를 바꾼 뒤 PI 생성해도 QT 단가) / `buyer_item_code` 0·1·2건 분기(2건 미지정 NULL, 지정값이 집합 밖이면 422) / 계좌 통화 불일치·비활성·타 통화 계좌 거부 / QT·PI 동결 시 `buyer_address`·은행 열 결측 거부(서비스+DB) / 거래처 변경 시 `buyer_name` 재복사.
- K: `bank_accounts` CRUD 권한(ADMIN 외 쓰기 403, LOGISTICS·CERT·VIEWER 조회 403, 낙관 잠금 409) / `account_no`·`bank_account_no` 로그·audit·events 미기재 스캔(`_account_no` 접미 규칙) / 유니크 키 금지 테스트 통과 / partners CSV 왕복(`name_en`·`address_en` 보존) / 페이지네이션 50.
- 변이 점검: 동결 시 재복사 추가 / `frozen_complete`에서 `buyer_address` 항 제거 / redaction 접미 제거 / 참조 생성이 마스터를 다시 읽게 변경.

**(g) 소비·등재** — S3-3 렌더링(레터헤드 = 부채·인계: 자사 프로필 마스터는 S3-3이 additive로 정의), S3-2 parties. 부채: 거래처 수정 화면 부재(CSV만), 스냅샷 새로고침 액션 부재, 중복 계좌 경합. ADR 필요: 스냅샷 규약, partners 확장, bank_accounts, 마스킹 접미 추가.

**(h) 되돌리기 비용 — 중간**. partners 2열은 nullable additive(삭제 저렴), `bank_accounts` 대체는 마이그레이션+PI FK 이관 필요. 전표 스냅샷 열은 소비 시작 후 삭제 비쌈.

---

## A9. 단가·금액 규칙 (제안 A7 통합)

**(a)** QT 단가 기본값·수동 입력·부재·자릿수·0원·라인금액·합계·상한 규칙 확정.
**(b)** ADR-0017("단가 없음=오류"), §4.1, §17.5(라인합=헤더합 저장 시점 검증), §7.6, §20 B 무상(금액 0), `partners.parse_credit_limit`(거부 선례).
**(c)**
1. **기본 단가** = `price_at(sku_id=, price_type="SALES", currency=헤더통화, on=doc_date)`(`price_at`은 세션 인자 없이 호출자 트랜잭션에 합류) → `unit_price_amount`, 같은 값을 `list_price_amount`에 스냅샷, `price_basis='MASTER'`. **다수 라인용 벌크 조회 `catalog.pricing.prices_at(session, sku_ids, currency, on) -> dict[sku_id, Money]`를 신설**(읽기 전용, `price_at`과 동일 규칙·동일 기준일 의미, 부재는 키 없음; `price_at` 대비 동등성 테스트) — 인테이크·QT 다중 라인이 N+1(§18.4)이 되지 않게. 단가 이력 행이 없으면 **`CATALOG.PRICE.NOT_EFFECTIVE`(기존 422)를 그대로 전파**(0·NULL 대체 금지, 라인 미생성). 사용자는 마스터 판가를 등록하거나 수동 단가를 넣는다.
2. **수동 입력 허용, 사유는 요구하지 않는다** — 견적은 협상 문서라 필수 사유는 정상 업무를 막는다(risk의 사유 강제 기각). 수동 입력 라인은 `price_basis='MANUAL'`(화면 배지), `list_price_amount`는 그 시점 마스터 판가(없으면 NULL — 비교 불능은 D 게이트가 "기준가 없음"으로 표시)로 남겨 편차가 항상 값으로 보인다. 편차 통제는 SO 단가 편차 게이트(D)가 한다. 인테이크 추출 단가는 `BUYER_PO`(D). 참조 생성 라인은 선행 `price_basis` 복사·재정의 불가(A4).
3. **자릿수 초과는 거부(반올림 금지)** — 바이어 PO에서 추출·수동 입력한 사람 표기 Decimal을 통화 최소단위 자릿수로 검사, 초과 시 422 "USD는 소수점 2자리까지". 공용 헬퍼 `trade_docs.money_input.parse_minor_amount(value, currency, *, max_digits=15) -> int`(`partners.parse_credit_limit`·`materials._minor_amount`와 같은 규칙의 4번째 사본 — **통합 리팩터링은 범위 밖, 부채 등재**). `pricing._to_money`의 조용한 반올림(`12.345→12.35`, 테스트 고정)은 전표에 쓰지 않는다. **서브 미니멈 단가(USD 0.125 등) 미지원이 한계이며 ADR에 명기**, 재판정 트리거 = 실데이터에서 소수 단가 발생.
4. **0원**: 마스터 판가가 0이면 자동 수용하지 않고 422("무상은 명시로 입력"). `is_free=true` 라인만 `unit_price_amount=0` 허용, **`price_reason` 필수**, `price_basis ∈ {MANUAL, BUYER_PO}`(A2 CHECK). QT·PI·SO 라인에서 허용, **PO 라인은 단가>0 강제**. 전 라인이 무상이면 `total_amount=0` 허용(§20 B "무상 0" — S3-3이 0원 서류를 막지 못하게). **PI는 서비스가 `total_amount > 0`을 요구**(선수금 요청서에 0원은 무의미, 422).
5. **라인금액 = `quantity × unit_price_amount`**(정수 곱, 반올림 없음) — DB CHECK+서비스 계산. 클라이언트가 보내는 `line_amount`·`total_amount` 필드는 스키마에 없다(`extra=forbid`).
6. **헤더 `total_amount` = Σ 활성 라인 `line_amount`**: 라인 변경 트랜잭션마다 서비스가 헤더 행 잠금 후 재계산·저장(**`total_amount` 직접 대입은 `recompute_total()` 안에서만 허용 — AST 스캔**), 합은 Python 정수(또는 SQL `SUM(...::numeric)`)로 계산하고 2^53−1 초과·라인 수 500 초과는 422 `TRADE.LINE.AMOUNT_OUT_OF_RANGE`. 통화 동일은 복합 FK가 보증. 야간 검산은 A14.
7. QT/PI/SO에는 **원가·마진 컬럼·필드·이벤트·로그를 만들지 않는다**(§7.1~7.4에 없음 — 발명 금지; A1의 스캔이 `PURCHASE` 호출을 봉쇄).
8. 상한: quantity 1..99,999,999, 금액 ≤ 2^53−1(A2).

**(d)** 4금 없음(수동 단가는 사람 입력).
**(e)** 라인 단가 열 동결(A3). 편집(초안)에서 단가를 바꾸면 `MANUAL`.
**(f)** A: `price_at` 성공(`MASTER`, `list=unit`)/부재 422·라인 0건/마스터 판가 0 → 422/부재 후 수동 입력 저장(`MANUAL`, `list NULL`)/수동 입력이 list와 달라도 list 유지 / 자릿수 경계(KRW 정수·소수 거부, USD 12.34 통과·12.345 거부 — 반올림 흔적 0) / 무상 양방향·사유 필수·전량 무상 total 0·PI total 0 거부 / `line_amount`=수량×단가·헤더=Σ라인(홀수 케이스, 라인 추가·수정·삭제 후 재계산, **랜덤 조작 100회 후 매번 불변식 검증**) / 클라이언트가 `total_amount` 보내면 422 / 2^53 초과·라인 501개 422 / 미등록 통화 422 / `prices_at`=`price_at` 동등성·N+1 없음(쿼리 수 고정). K: `total_amount` 대입 위치 스캔, 응답 스키마에 cost/margin 부재.
변이: 정밀도 검사 제거·`recompute_total` 누락·CHECK 곱셈 제거·무상 사유 필수 제거·`prices_at`을 라인별 호출로 변경.
**(g)** D(인테이크가 `LineIn`·`parse_minor_amount` 재사용, 초과 자릿수 행은 ERROR로 남기고 사람이 정정), E(0원 선수금 처리). 관찰: 서브 미니멈 단가 미지원, 금액 파서 4중 사본.
**(h) 되돌리기 비용 — 낮음~중간**. 반올림 허용 전환은 서비스 1곳. `list_price_amount` 사후 추가는 백필 불가라 지금 확정. 서브 미니멈 단가는 전 금액 의미 변경이라 매우 비쌈 → ADR에 한계 명시.

---

## A10. 날짜 3종·유효기간·QT 개정·채번 (제안 A8·X1 통합)

**(a)** 4종 전표의 날짜 적용 범위, QT·PI 유효기간, QT 개정(재발행) 표현, 채번 연도·시점.
**(b)** §3 날짜 3종(증빙/전기/입력), §7.2 QT "만료"·PI 유효기간(§7.3), §12.4(분개 이벤트), §21 소급 입력 runbook(증빙일=실제 발생일·입력일=복구일), §17.3, ADR-05 "정정=취소+신규(취소 전표가 원본 참조)".
**(c)**
- **날짜**: 4종은 **증빙일 `doc_date`(NOT NULL) + 입력일 `created_at`(기존 TimestampMixin)만** 가진다. **전기일 열은 두지 않는다** — 분개를 만드는 전표(선적확정·입고·비용·입금)가 소유하며 이 4종은 분개를 만들지 않는다(§1 비범위). 검증: `doc_date <= today_kst()`(미래 거부 422 `VALIDATION_INVALID_FIELD`), **과거(소급)는 상한 없이 허용**(§21), 후속 전표 `doc_date >= 원천 doc_date`, `valid_until >= doc_date`(CHECK), `fx_rate_date <= doc_date`(CHECK). 마감 잠금은 S4-4 몫. 요청납기·유효기간은 미래 허용.
- **유효기간**: **QT·PI 둘 다 `valid_until DATE`**(QT "만료" 상태가 §7.2에 있으므로 QT에도 필수), SO·PO 없음. 동결 시 NOT NULL(A2). **서버 기본값 없음(명시 필수)** — UI만 발행일+30일을 미리 채운다. 서비스 검증: `valid_until <= doc_date + 365일`(오타 방어, 422). 의미: `valid_until` **당일 KST 24:00까지 유효**, `today_kst() > valid_until`이면 만료 대상(포함 경계). 만료 전이(스윕 잡 vs 계산값)는 B 소관이나 **참조 자격은 상태와 무관하게 `valid_until`을 직접 검사**(A4).
- **QT 개정**: **신규 QT + `revises_qt_id`(자기 참조)**, 개정번호 열 없음(체인은 FK, "Rev N"은 조회 시 계산). `POST /v1/quotations/{qt_id}/revisions`(Idempotency-Key)가 원본(ISSUED)을 값 복사한 **작성(초안) QT**를 만든다(QT는 작성 상태가 있어 정상 편집 흐름). **새 QT를 발행하는 같은 트랜잭션에서 원본 QT를 취소(사유 `REVISED`)로 전이**한다(ADR-05 정정=취소+신규). 원본에 활성 후속(PI·SO)이 있으면 개정 발행 거부(`TRADE.REFERENCE.HAS_ACTIVE_DESCENDANTS` 409 — 역순 취소 원칙), 원본이 수주전환됨이면 개정 불가(CONVERTED→CANCELLED 전이 없음 — 이 경우 사용자는 개정 링크 없는 **새 QT**를 작성). **원본이 만료(EXPIRED)이면** 취소 없이 개정본만 만들 수 있다(만료 견적 갱신). 한 원본에 활성 개정본은 하나(A2 부분 유니크). (risk의 "원본 자동 취소 없음+SUPERSEDED 규칙"은 새 개념을 만들어 기각.)
- **채번**: 접두어 `QT/PI/SO/PO`를 `trade_docs.DOC_PREFIXES` 단일 출처(C의 폴리모픽 코드와 공유)에 두고 **전표 서비스가 접두어 문자열을 직접 쓰지 않는다**(`issue_doc_number(session, doc_kind)` 경유 — 스캔). 번호는 **최초 저장 트랜잭션 안**에서 발급: QT=작성 생성 시, SO=접수 생성 시, PI·PO=생성(발행) 시(롤백 시 번호도 롤백, 취소는 결번 허용·재사용 금지 — §17.3). **채번 연도를 KST로 수정**(`numbering/service.py:36` `year = to_kst(at or utcnow()).year` — 미지정 시 발급 시각의 KST 연도; `at`을 넘기면 aware 값만 허용, naive는 `to_kst`가 예외 → fail-closed). 번호 연도는 **발급(입력) 시점 연도**이며 소급 입력 시 증빙일 연도와 다를 수 있음(정상, ADR 명기) — risk의 "증빙일 연도로 채번+연도 변경 편집 금지" 안은 카운터 연도가 증빙일을 따라가 발급 순서와 번호가 어긋나고 별도 잠금 규칙이 필요해 기각. 호출부가 앱 안 0곳이라 영향은 기존 `test_numbering` 케이스 갱신뿐.

**(d)** 4금 없음(개정 발행 시 원본 자동 취소는 사람이 누른 발행 액션의 부수 전이).
**(e)** 개정 발행 전이=B 전이표에 "QT 발행+원본 REVISED 취소" 합성 전이 등재 요청.
**(f)** A: `valid_until` 경계(당일 유효·다음 날 만료·365일 초과 422·`<doc_date` CHECK 거부) / `doc_date` 미래 거부·과거 허용·후속<원천 거부 / **KST 00:30(UTC 전일) 고정 시계**에서 `today_kst` 기준 검증(UTC 날짜 오판 방지) / 개정: 발행 시 원본 자동 취소+**동일 트랜잭션 롤백 원자성**(개정 발행 실패 시 원본 취소도 롤백) / 활성 후속 있는 QT 개정 거부·CONVERTED 거부·EXPIRED 원본 취소 없이 개정 / 한 원본에 활성 개정본 2개 거부(부분 유니크) / 전기일 열 부재 스캔(모델 컬럼 집합에 `posting_date`류 없음) / 채번: **KST 2027-01-01 00:00~08:59(UTC 12/31)에 발급하면 2027 시퀀스**, 14:59:59Z는 2026, 접두어 4종 중복 없음 메타 테스트, 상수 밖 접두어 호출 스캔, 롤백 시 미소비, 동시 100건 중복·결번 0 유지(기존 J 테스트 유지, 연도 하드코딩 금지).
변이: `valid_until` `<`↔`<=`·`today_kst`를 UTC 날짜로 교체·개정 시 원본 취소 누락·`at` naive 허용.
**(g)** B: 개정 합성 전이·만료 잡. 부채: 소급 입력 runbook 양식(`docs/runbook/forms/manual-record-form.md`)의 전표 계열 대응표는 "전표 계열은 도래 시 갱신"이라 명시돼 있음 — **S3-1이 갱신 의무**(QT·PI·SO·PO 증빙일 대응) — F 묶음이 인계 수령.
**(h) 되돌리기 비용 — 낮음**. 개정번호 열·전기일 열 추가는 additive. 채번 연도 규칙은 함수 1줄(이미 발급된 번호는 소급 변경하지 않음).

---

## A11. 라인 품목 규칙 — SET·DISCONTINUED·MOQ·수량 단위·중복 SKU (제안 A9 통합)

**(a)** SET SKU 처리, 단종 SKU 차단 범위, MOQ 원천, 수량 단위, 같은 SKU 다중 라인 규칙.
**(b)** §4.1·§4.2(세트 전개는 원장·인증 롤업 시점), §7.4 MOQ 게이트, §8.2(EA 원장·박스 화면 환산), `catalog/models.py` "과거 전표 참조를 위해 SKU는 삭제하지 않는다".
**(c)**
1. **SET SKU는 세트 그대로 1라인**(`sku_kind='SET'` 스냅샷, 구성품 전개·구성 스냅샷 테이블 없음). 세트는 SKU 하나로 판매·가격·MOQ·바이어 품번을 가진다. 전개는 할당·DG 시점(S4-2 `list_set_components`). **위험(확정 후 구성 변경 드리프트)은 부채·관찰로 등재**, 재판정 트리거 = S4-2 착수(그때 "열린 SO/PO 라인 있는 세트의 구성 변경 가드" 또는 "확정 시점 구성 스냅샷" ADR).
2. **DISCONTINUED·삭제 SKU**(DESIGN 침묵 구간 — "가장 좁은 안전한 결정"): 공용 단일 통로 `trade_docs.lines.require_sellable_sku(session, sku_id, doc_kind) -> Sku`가 모든 라인 경로(수기·참조·인테이크·임포트)의 상태 검사를 소유(현 `catalog.require_sku`는 상태를 안 봄).
   - **QT·PO**: 신규 라인 추가·SKU 변경에서 DISCONTINUED → 422 `TRADE.LINE.SKU_DISCONTINUED`(즉시 차단). 삭제된 SKU는 404/422.
   - **PI 생성(동결 생성)**: 원천 라인 SKU가 단종·삭제로 바뀌었으면 **전체 거부가 아니라 422로 해당 SKU 목록을 열거**하고 사용자가 `lines`에서 제외하도록 한다(침묵 제외 금지).
   - **SO 접수(INTAKE)**: 바이어가 실제 보낸 PO를 시스템이 거부하면 접수 자체가 안 되므로 **저장은 허용하되 라인에 `sku_status` 표시(응답 파생값)**, **SO 확정(동결) 전이에서 재검사해 차단**(단종 SKU를 판매하려면 마스터 상태를 되돌리는 가시적 조치가 선행). 게이트 6종(D)과 별도로 A의 라인 규칙이 확정 사전조건이다.
   - 이미 동결된 전표는 SKU가 나중에 단종돼도 영향 없음(과거 사실).
3. **수량**: EA 정수만(`INT`, 1..99,999,999). BOX 입력·환산은 화면(P4, `skus.box_qty`), 서버는 EA만 수용.
4. **MOQ 원천 — `skus.moq INT NULL CHECK (moq IS NULL OR moq > 0)`**(단위 EA, SET은 세트 단위, NULL = MOQ 미정의). 바이어별·구간별 MOQ는 만들지 않는다(문면 근거 없음, 필요 시 별도 테이블 additive). **마스터 변경 ②** — 세트: DESIGN §4.1 보강 문단 + ADR 5줄 + 마이그레이션(기존 테이블 CHECK는 `op.create_check_constraint(op.f("ck_skus_moq_positive"), ...)` 수기 — 함정 ①·⑪) + SKU 등록·수정 스키마(`extra=forbid` 유지)·`SkuView`·**CSV 왕복 임포트 어댑터 열 추가(`imports/registry.py` SKU 어댑터, 왕복 diff 테스트 갱신)** + SKU 화면 필드. 게이트 판정(미만 → 경고/차단, 미정의 → 가시 표시)은 D. **판정 단위 권고 = 한 전표 안 동일 SKU 유상 라인 수량 합**(A11-5로 SKU당 유상 라인은 1개라 곧 라인 수량).
5. **중복 SKU**: 한 전표에서 `(sku_id, is_free)` 유일(A2) — **같은 SKU의 유상 1줄 + 무상 1줄까지 허용**(FOC 병행은 정상 패턴). 동일 조합 2줄은 409 `TRADE.LINE.SKU_DUPLICATE`(부분 유니크 위반은 제약명으로 판별해 번역 — IntegrityError 500 금지). 수량 합치기는 사용자가 라인 수정으로(자동 병합 금지). **분할 납기(같은 SKU 다른 납기일 다중 유상 라인)는 지원하지 않는다** — 부분선적(S3-2 1:N)으로 납기를 나누고 라인 `requested_delivery_date`는 하나. 필요 시 유니크 인덱스에 납기일 추가(`NULLS NOT DISTINCT`)로 완화(마이그레이션 1건·데이터 변환 없음 — 조이는 쪽이 어려워 좁게 시작). 인테이크 중복 줄은 D의 스테이징 검토가 합산·거부를 결정.

**(d)** 4금 없음.
**(e)** SO 접수 단종 SKU 허용 → 확정 차단(A3 동결 전이 사전조건).
**(f)** A: SET 라인 저장·전개 없음 / DISCONTINUED: QT·PO 추가·수정 거부, PI 생성 거부(SKU 목록 열거)·제외 후 성공, SO 접수 저장+표시·확정 시 재검사 차단, 동결 전표는 단종 후에도 무영향 / 삭제 SKU 거부 / EA 정수 외(0·음수·소수) 거부 / 유상 2줄 거부·유상+무상 각 1줄 허용·soft delete 후 재추가 허용 / `moq` CHECK(0 거부·NULL 허용)·SKU 등록/수정·CSV 왕복 보존. K: 라인 생성 경로 4곳(수기·참조·인테이크·임포트)이 `require_sellable_sku`를 호출하는지 AST 스캔(양·음성 코퍼스). 변이: 상태 검사 제거·유니크 술어에서 `is_free` 제거·SO 확정 재검사 제거.
**(g)** D: 게이트가 MOQ·단종 표시를 소비, 인테이크 라인 경로가 `require_sellable_sku` 재사용. 관찰: SET 구성 변경 드리프트(P4 트리거), 분할 납기 미지원.
**(h) 되돌리기 비용 — 낮음~중간**. `moq`는 nullable 컬럼(삭제 시 임포트 어댑터·화면 되돌림 필요). 단종 정책·중복 정책 완화는 서비스/인덱스 1건.

---

## A12. PO 골격 — 공급사 유형·SKU 전용 라인·원가 네이밍·마스킹 전제 (제안 A1·X2·X3 통합, F와 접점)

**(a)** PO 헤더·라인의 데이터 골격과 원가(매입가) 컬럼 명명. 마스킹 구현·역할 매트릭스는 F, 발행=발주 확정(4금)의 1클릭 강제·상태는 B·C.

**(b)** §7.1~7.2(PO: 발행→공급사확인→…), §4.4(OEM 생산 발주), §8.1·8.5(SKU 또는 자재), ADR-0018·0024(매입가=원가), §18.1 조회 역할 원가 마스킹, `redaction.py`.

**(c)**
- **공급사 유형**: `supplier_partner_id`는 활성 유형이 **SUPPLIER 또는 OEM**(교집합 검사 래퍼 `partners.require_partner_of_any_type(session, id, ("SUPPLIER","OEM"), field=..., type_label="공급사")` — 기존 `require_partner_of_type`은 단일 유형만 받음; 생성·동결(발행)·거래처 변경 시 재검증).
- **PO 라인 품목 = SKU 전용**(F 묶음에 둔 가정): `sku_id NOT NULL`. **자재(materials) PO 라인은 S3-1에서 만들지 않는다** — 자재 PO는 재고 편입(§8.1)·사급(§8.5)과 한 덩어리로 P4 소비이고 단위(uom)·수량 타입(`NUMERIC(14,4)`) 결정이 따라온다. 추가 경로는 additive: `material_id BIGINT NULL` 추가 + `sku_id` NULL 허용 + `num_nonnulls(sku_id, material_id) = 1` CHECK + 수량 `INT→NUMERIC(14,4)` 확장(무손실 확폭)+`uom`. 재판정 트리거 = P4 착수 ADR(사급) 또는 오너의 자재 발주 요구. (3안 모두 F 위임이라 판정 필요했음 — 가장 좁은 안전 결정으로 SKU 전용 채택.)
- **원가 네이밍(중요)**: PO 금액 열은 **`unit_cost`(라인 단가)·`line_cost`(라인 금액)·`total_cost`(헤더 합계)** 로 짓는다 — 이 이름은 **로그 마스킹 키(`unit_cost`) 또는 접미사(`_cost`)에 이미 걸리고, `is_money_column_name`(`_cost` 접미)에도 걸려** 유니크 키 금지·로그 마스킹이 코드 변경 없이 자동 편입된다(BOM `unit_cost` 선례, 검증 #5). risk의 `purchase_*_amount` 접두 안은 redaction 규칙을 새로 짜야 해 기각. 통화는 `currency`. 판매 체인 금액(`*_amount`)은 판가라 마스킹 비대상.
- PO 단가 기본값 = `price_at(price_type="PURCHASE")`(권한 검사 없음 — 호출 가능 역할은 서비스가 `may_see_cost`로 제한), `price_basis IN ('MASTER','MANUAL')`, `unit_cost > 0`(무상 매입 미지원, 필요 시 재판정), `line_cost = quantity × unit_cost`(A2 CHECK), `total_cost = Σ line_cost`(A9-6 규칙).
- **F에 넘기는 마스킹 전제**: VIEWER 응답 스키마에서 `unit_cost·line_cost·total_cost`(및 `currency`)는 **필드 부재**(null이 아님 — ADR-0024 "필드 부재 스키마 분기", `response_model=None`+명시 스키마 선택, 직렬화 후 dict 키 삭제 금지)이고 행·건수는 보인다. 목록·검색·정렬·필터·CSV에서 VIEWER의 `*_cost` 정렬·범위 필터는 422(순서 오라클 차단). K 테스트는 "원가 없는 PO 스키마에 금액·민감 이름 필드가 하나도 없음"을 `test_bom_cost_masking` 형식으로 신설. 이벤트·알림·audit에는 ID·번호·상태만(금액 미기재).
- 헤더 나머지: 결제조건·Incoterms·환율 스냅샷은 판매 체인과 같은 믹스인(수입 선적 소비), 목적지 시장·`buyer_*`·`valid_until` 없음.

**(d)** **PO 발행 = 발주 확정(4금)** — 생성 API가 곧 발행이므로 **사람이 누른 1클릭이 유일 경로**여야 한다. 자동 규칙·스케줄러·인테이크가 PO 생성 서비스를 호출하는 곳 0건을 아키텍처 테스트로 고정(B·C 소관 스캔에 이 함수 목록을 제공). 발주점 권고(§8.8)도 PO를 만들지 않는다.
**(e)** 동결=생성 즉시(`frozen_at` NOT NULL DEFAULT now()). 상태 전이는 B.
**(f)** K/G: VIEWER PO 상세·목록·검색·CSV에서 cost 필드 부재, 정렬·필터 422, ADMIN·TRADE·LOGISTICS·CERT 값 표시 / 원가 없는 스키마 필드 스캔 / 로그 캡처에 `unit_cost` 등 값 미노출(자동 마스킹 확인) / 공급사 유형 SUPPLIER·OEM 허용·타 유형 422·유형 해제 후 발행 422 / `unit_cost 0` 거부 / SET SKU 라인 / DISCONTINUED PO 라인 차단 / PO 생성 호출 지점 스캔. 변이: 응답 조립에서 마스킹 분기 제거·`_cost` 이름을 `_amount`로 바꾸기(로그 마스킹 테스트가 잡아야 함).
**(g)** F: 응답 스키마 분기·역할 매트릭스·**거래처 유형 해제 갭(PROGRESS:119)**의 구현 위치 — A는 `partner_has_open_documents(session, partner_id, kind)`(L2, 미결 전표 존재 조회)만 제공하고 차단 정책은 F/E. 관찰: 자재 PO(P4), 무상 매입.
**(h) 되돌리기 비용 — 낮음~중간**. 컬럼 rename은 신규 테이블이라 지금 무료, 발행 데이터 후엔 마이그레이션+전 코드 변경. 자재 라인 추가는 additive+확폭.

---

## A13. 잠금·경합·멱등·오류 변환 계약 (risk X2·X5 중 채택분)

**(a)** 전표 쓰기 경로 공통 직렬화·낙관 잠금 상호작용·멱등·제약 위반 번역. 승인·여신 잠금은 C·E이며 순서만 합의한다.
**(b)** §17.1~17.4, §20 J, S1-3 PR-3 리뷰 ①②(부모 version 미증가 우회·확정 시점 재검증 부재), PROGRESS 함정 ②(IntegrityError 후 세션 재사용).
**(c)**
1. **모든 전표 쓰기(라인 포함)는 헤더 행 `SELECT FOR UPDATE` → 요청 `expected_version` 대조(불일치 409 "다른 사용자가 먼저 수정했습니다") → 변경 → 헤더 version 증가** 순서. 라인만 바뀌어도 헤더 version이 올라야 겹친 편집·승인 요청이 409를 우회하지 못한다 — 구현은 `bump_header_version()` 1곳에서 **`updated_at` 명시 대입으로 부모를 dirty로 만드는 S1-3 PR-3 선례**를 재사용(SQLAlchemy `version_id_col`이 flush 시 1회만 증가하는지 첫 테스트에서 확인 필요 — 실행 검증 못 했음). 라인 편집 API는 `/{doc}/{id}/lines/{line_id}` 형태만(라인 단독 라우트 금지, 서비스가 `line.hdr_id == id` 단언 — IDOR).
2. **전역 잠금 순서(고정)**: ① 거래처(partners — 여신 직렬화, E) → ② QT → ③ PI → ④ SO → ⑤ PO → ⑥ 같은 종류 안에서는 id 오름차순, 원천 라인은 헤더 다음 id 오름차순 → ⑦ **`doc_number_seq`는 항상 마지막**(카운터 행 잠금이 트랜잭션 끝까지 유지되므로 채번은 INSERT 직전으로 미룸). 교착 방지.
3. **확인→기록 창 방어**: 참조 생성·동결 전이·편집은 "잠금→재조회→재검증(원천 상태·잔량·마스터 유효성)→기록→커밋 뒤 외부효과(outbox)". 트랜잭션 안 외부 호출 없음.
4. **잠금 대기 초과 변환**: kbos_app `lock_timeout=5s`(55P03)·`statement_timeout=30s`(57014)는 현재 처리 코드가 없어 500이 된다 → `errors/handlers`에 공통 변환 추가, 409 `COMMON.CONCURRENCY.LOCK_BUSY`("다른 사용자가 처리 중입니다. 잠시 후 다시 시도해 주세요"). **플랫폼 변경이며 S3-1이 첫 사용처라 이 PR에 포함**.
5. **멱등**: 생성·참조 생성·개정·동결 전이(확정·발행)·취소 액션 모두 `Idempotency-Key` 필수(스코프 액터×엔드포인트×키, 재수신=최초 결과, 같은 키 다른 본문 422). **프런트는 확정·생성 버튼의 키를 화면 진입 시 1회 생성해 재클릭·재시도에 재사용**(키 재생성 금지 — S1-3 PR-3 프런트 결함 재발 방지). DB UNIQUE가 마지막 방어선: `doc_number`·PI→SO·중복 바이어 PO·개정본·라인 유니크.
6. **IntegrityError·CheckViolation 번역**: `map_integrity_error()` 1곳에서 **제약명 접두**로 분기 — 유니크 위반은 사용자 문구(409: `DUPLICATE_BUYER_PO`(기존 전표 번호만 노출, 금액·민감값 미노출)·`ALREADY_CONVERTED`·`SKU_DUPLICATE`), **CHECK 위반은 "서비스 선검증 누락 버그"로 취급해 ERROR 로그(값은 `log_context`에만)+일반 422/409** — 500 금지, 응답·`last_error`에 DB 예외 원문 저장 금지. IntegrityError 후 세션 재사용 금지(`PendingRollbackError`).
7. **에러코드**(`core/errors/codes.py` 열거+`catalog.py` 스펙 동시 추가, 3세그먼트): `TRADE.DOCUMENT.INCOMPLETE`(422, detail={field: 한국어} 다건) / `TRADE.DOCUMENT.TOTAL_MISMATCH`(409+ERROR 로그) / `TRADE.DOCUMENT.DUPLICATE_BUYER_PO`(409) / `TRADE.REFERENCE.SOURCE_NOT_ELIGIBLE`(409) / `TRADE.REFERENCE.QTY_EXCEEDS_SOURCE`(409) / `TRADE.REFERENCE.ALREADY_CONVERTED`(409) / `TRADE.REFERENCE.HAS_ACTIVE_DESCENDANTS`(409) / `TRADE.LINE.SKU_DISCONTINUED`(422) / `TRADE.LINE.SKU_DUPLICATE`(409) / `TRADE.LINE.AMOUNT_OUT_OF_RANGE`(422) / `TRADE.PAYMENT.LC_DISABLED`(422) / `COMMON.CONCURRENCY.LOCK_BUSY`(409). 입력 형식·자릿수·미등록 통화·날짜는 기존 `VALIDATION_INVALID_FIELD`+detail로(열거 폭증 방지). 동결 위반 `TRADE.DOCUMENT.FROZEN`(409)은 B가 정의. 값 자체가 detail·로그에 실리지 않도록(금액·계좌 미포함).

**(d)** 4금 없음.
**(e)** 잠금 순서·`bump_header_version`이 불변식.
**(f)** J/H(`concurrency` 마커, 실커밋, 사용자 자원 공유 금지): (a) 같은 헤더 라인 추가와 헤더 PATCH 동시 → 한쪽 409·합계 정합 (b) 두 참조 생성 교차 잠금 시나리오 교착 0 (c) 라인 변경 뒤 옛 version으로 동결·승인 요청 → 409 (d) 다른 세션이 행을 잠근 상태에서 lock_timeout 초과 → 409 LOCK_BUSY(500 0건) (e) 확정 더블클릭(같은 키) → 전표 1건 (f) 채번 동시 100건 중복·결번 0 (g) 제약명→코드 매핑 표 **전수** 테스트(IDOR: 다른 전표 `line_id` 섞은 PATCH/DELETE → 404). 변이: `FOR UPDATE` 제거·version bump 제거·잠금 순서 반전·55P03 핸들러 제거.
**(g)** C·E: 잠금 순서 ①에 거래처가 먼저 온다는 합의, 승인 요청이 `(doc_id, 요청 시점 header version)`을 저장·결정 시 재대조. 관찰: 동시성 테스트는 병렬 pytest 금지(함정 ③) — 로컬 검증 불가 시 CI 의존.
**(h) 되돌리기 비용 — 낮음**(서비스 내부 규율·스키마 영향 없음). 단 잠금 순서를 후속 세션이 어기면 교착이 조용히 생기므로 K 스캔(순서 위반 주석 관용)으로 고정 검토.

---

## A14. 합계 야간 검산 — `verify_document_totals()`와 잡 (제안 X3 통합)

**(a)** §17.5 "라인합=헤더합은 저장 시점 검증+야간 검산 이중망"의 후자를 S3-1이 전표를 만드는 시점에 함께 세운다.
**(b)** §17.5, §21 "관리 메뉴 검산·헬스체크", ADR-0044(스케줄러 계약), §15 4금.
**(c)** `trade_docs.verify.verify_document_totals(session) -> list[TotalMismatch(doc_kind, id, doc_number, header_total, lines_total)]`(읽기 전용·잠금 없음, 4헤더 대상, `total <> COALESCE(SUM(활성 라인 금액::numeric), 0)`, 삭제·취소 전표 포함 여부: 삭제만 제외). **잡 `trade-docs-totals-verify`**(daily@05:30 KST)를 `JOB_REGISTRY`에 등록하고 불일치 시 **ADMIN 알림**(`notify()` 단일 통로, dedup `trade.totals_mismatch:{kind}:{id}:{KST일자}`, 본문에 doc_number만·금액 미기재), 검사 실패(예외)는 잡 FAILED(`_fail_if_any_failed`). **불일치 자체는 알림이며 자동 보정하지 않는다**(조용한 보정 금지). 등록은 앱 경로(worker 기동 자동 등록·`register-jobs`), **마이그레이션 시드 금지**(함정 ⑩), `test_registered_jobs_stay_clear_of_the_four_bans`의 등록 코드 집합·4금 비저촉 서술 갱신(읽기+알림만, 상태 변경·대외 발송·장부 확정 없음).
**(d)** 4금 비저촉(읽기 전용, IN_APP 알림, 자동 확정·발송 없음).
**(e)** 검산은 불변식의 감시자일 뿐 상태를 바꾸지 않는다.
**(f)** A/H: 정상 데이터 0건 / 헤더 미갱신을 SQL로 강제(테스트 전용)해 검출 / 삭제 라인 제외 / 4헤더 전부 대상 / 잡 실행이 알림 1건(dedup으로 같은 날 재실행 0건) / 잡 실패 시 FAILED. K: 스케줄러 레지스트리 테스트 집합 갱신·4금 테스트 통과. 변이: 검산에서 삭제 라인 포함·헤더 하나 누락.
**(g)** 잡이 늘어 레지스트리 8행(현 7). 소비: 운영 검산 버튼(§21)은 이 함수를 재사용. 관찰: 잡 연결을 별도 소단위로 미루면 §22 부채 — **판정: S3-1 범위에 포함**(전표가 처음 생기는 세션이 이중망을 완성).
**(h) 되돌리기 비용 — 낮음**(순수 읽기+잡 1행).

---

## 자율 확정 판정표

| 번호 | 결정 요지 | 근거 한 줄 | 되돌리기 |
|---|---|---|---|
| A1 | 8테이블+`bank_accounts`, PI 라인 보유, 라인도 자기 `currency`+헤더 복합 FK, 모듈 L0 `trade_docs`/L1 전표별/L2 `trade_chain`, FK 열 `qt_id/pi_id/so_id/po_id`(63자 한도) | §3·WBS "(+lines)", ADR-0003 ④ 행 단위, 순환 방지, 이름 길이 실측 | 중간 |
| A2 | 열·CHECK·인덱스 전문: 라인금액=수량×단가(numeric 곱)·무상 양방향·상한 2^53·`frozen_complete`(frozen_at 기반)·SO/PI/QT 부분 유니크(취소 제외) | §17.5 CHECK 우선, 오버플로 500 방지, 정정=취소+신규 경로 보장 | 중간 |
| A3 | `frozen_at` 표식+`FROZEN_*` 열 집합(assignee_id·status·note 제외), 강제 수단은 B, 다이제스트 미채택 | handover 일괄 UPDATE 충돌 방지, B 열거와 독립 | 낮음 |
| A4 | QT→PI 1:N·PI→SO 활성 1:1·QT→SO 1:N, PI.qt_id NOT NULL, 소비=파생 SUM(취소·만료 제외), 참조 생성 API(원천 값 입력 필드 없음)+PI 미리보기, 수주전환=SO 확정, 원천 자격에 `valid_until` 직접 검사(입금 PI 면제) | DoD를 스키마 구조로 보증, 파생값 불일치 원천 제거 | 중간~높음 |
| A5 | 결제조건 4열: `TT_ADVANCE/TT_DEFERRED/LC`, bp 정수, 앵커 6종(ETD·RECEIPT 포함, 음수 일수는 ETD 한정), 형태 CHECK, `split_advance()`, LC는 플래그 fail-closed(상속 복사·진행은 검사 안 함) | 문면 열거 부재를 S3-2 이전에 완결, 프런트 산술 가드 | 중간 |
| A6 | Incoterms 3열: 12코드+연도 {2010,2020}+교차 CHECK, all-or-none, 완전성은 S3-3 | §7.5 연도 열 문면, 재입력 금지 | 낮음 |
| A7 | 헤더 환율 스냅샷(1통화=x KRW, NUMERIC(18,8), 수동), **`fx_rates` 미신설**, `to_krw`·`comparable_amount`(불가=None=통과 아님), WBS v1.5 등재 | 소비자 없는 마스터 금지, risk 안은 우회를 못 닫음 | 중간 |
| A8 | 스냅샷=값 복사(생성 1회·참조는 선행 복사), partners `name_en`·`address_en` 2열, `bank_accounts`(ADMIN 쓰기), PI 통화=계좌 통화, 레터헤드 S3-3 이월, redaction 접미 `_account_no/_account_number` | ADR-0017·ADR-05, 마스킹 키 공백 실측 | 중간 |
| A9 | 단가=`price_at`(부재 422 전파, 판가 0 거부), 수동 입력 사유 불요, 자릿수 초과 거부, 무상은 명시+사유, PI total>0, `list_price_amount` 스냅샷, `prices_at` 벌크 신설, 서브 미니멈 단가 미지원 | ADR-0017·partners 거부 선례, 사후 백필 불가 열 | 낮음~중간 |
| A10 | 날짜 3종 중 증빙·입력만(전기일 없음), 미래 거부·소급 허용, QT·PI `valid_until`(명시 필수·≤365일), QT 개정=신규 QT+`revises_qt_id`(발행 시 원본 REVISED 취소), 채번 KST 연도(발급 시점) | §21 소급 runbook, ADR-05, 렌즈 6 | 낮음 |
| A11 | SET 그대로 1라인, 단종 SKU: QT·PO 즉시 차단/PI 열거 후 제외/SO 접수 허용·확정 차단, EA 정수, `skus.moq`(NULL=미정의), 동일 SKU 유상 1+무상 1(분할 납기 미지원) | §4.1·§7.4, fail-closed 최소 결정 | 낮음~중간 |
| A12 | PO: 공급사 SUPPLIER/OEM, 라인 SKU 전용, 원가 열 `unit_cost/line_cost/total_cost`(자동 마스킹·금액 판정 편입), 단가>0, 마스킹은 필드 부재 스키마 분기 전제, PO 생성=사람 1클릭 | ADR-0018·0024, 검증 #5 | 낮음~중간 |
| A13 | 헤더 잠금→version 검사→bump(라인 변경도), 전역 잠금 순서(거래처→QT→PI→SO→PO→채번 마지막), 55P03→409 변환, 멱등키 필수, 제약명 기반 오류 번역, 에러코드 12종 | S1-3 PR-3 결함·lock_timeout 처리 부재 실측 | 낮음 |
| A14 | `verify_document_totals()`+잡 `trade-docs-totals-verify`(ADMIN 알림, 자동 보정 없음) | §17.5 이중망 문면 | 낮음 |

---

## 설계 보강·ADR 필요 목록 (설계/WBS 충돌·침묵 보충)

- **DESIGN §3 표 맵**: PI에 `(+lines)` 추가(표기 정정), `bank_accounts` 추가, `fx_rates` 항에 "S3-1은 전표 헤더 스냅샷, 마스터 미신설(재판정 트리거 명시)" 주석. **§4.6 보강**: partners `name_en`·`address_en`. **§4.1 보강**: `skus.moq`. **§7 보강 문단**: 참조 카디널리티·수주전환=SO 확정·PI 미리보기(초안 상태 부재 보충)·결제조건/Incoterms 열거·`frozen_at` 동결 표식·QT 개정=신규+원본 REVISED 취소. **§17.3**: 채번 연도=발급 시각 KST 명문화. **§17.4**: 중복 바이어 PO·PI→SO·개정본 부분 유니크의 술어에 `status <> 'CANCELLED'` 추가(정정=취소+신규 경로) — 문면 보강.
- **WBS v1.5**: `fx_rates` 소유 세션 미배정 등재(재판정 트리거 3종), L/C 플래그 토글 경로 → S3-3, 자사 레터헤드 마스터 → S3-3.
- **ADR 신설(번호는 통합 검토가 0051~ 배정)**: ① 전표 구조·참조 카디널리티·소비 규칙 ② 결제조건·Incoterms 열거 ③ 환율 규약(방향·NUMERIC(18,8)·수동·`fx_rates` 미신설·비교 불가=None) ④ 스냅샷 규약+동결 열 집합(`frozen_at`)+마스터 보강(partners 2열·bank_accounts) ⑤ 단가 자릿수 거부·무상 명시·서브 미니멈 한계 ⑥ MOQ 마스터 열 ⑦ 채번 연도 KST(ADR-0003 채번 계보 부기 가능) ⑧ 원가 네이밍(`*_cost`)과 PO 마스킹 전제(ADR-0024 부기 가능).
- **GC 등재 후보**: v1.4 안건(r1): 참조 관통·중복 PO·여신 게이트·확정 후 환율/단가 불변·PI 게이트 — A 묶음 기여분은 "확정 후 단가·환율 불변(성공 방향=초안 편집 허용)"과 "중복 바이어 PO(성공 방향=취소 후 정정 SO 허용)".

## 타 묶음 의존(요약)

- B: `CANCELLED`/`EXPIRED` 리터럴, QT CONVERTED 전이(SO 확정 트랜잭션), `REVISED` 취소 사유, 개정 합성 전이, 만료 잡/계산값, 동결 강제 수단(A3 목록 사용), 취소 시 `active_descendants` 호출, 상태 이력 IMMUTABLE 등재.
- C: `DOC_PREFIXES`, 승인 후 불변에 A3 목록, `comparable_amount` None → 승인 게이트, 승인 요청에 `(doc_id, header version)` 저장.
- D: `LineIn`·`parse_minor_amount`·`require_sellable_sku`·`snapshot_line_from_master`·`prices_at`·`normalize_buyer_po_no`(A 제공: NFKC → 대문자 → 모든 공백 제거, 구두점 보존 — D가 다른 규칙을 원하면 인덱스 키 백필 동반 ADR), `skus.moq` 소비, 게이트 결과 저장 이름 금지어 주의, SO 접수 단종 SKU 표시.
- E: 여신 노출에 SO `total_amount`+`fx_rate`·`comparable_amount` 사용, `split_advance`, `total=0` 선수금 처리, L/C 플래그 행 공급 경로(S3-3), 거래처 행 잠금 최선행.
- F: PO 마스킹 스키마 분기·역할 매트릭스(`bank_accounts` ADMIN 쓰기), 공급사 유형 집합 수용, 자재 PO 이월, 거래처 유형 해제 갭 구현, 소급 runbook 양식 갱신, handover 등록 4행.

## 부채·관찰 등재(PROGRESS)

1. `fx_rates` 마스터 미신설(재판정 트리거: 환율 자동 수집 §15 L3 / S6-2 / 환율 입력 오류 사고). 환율 신선도 서버 규칙 없음(화면 파생 표시만).
2. 서브 미니멈 단가 미지원(트리거: 실데이터 소수 단가). 금액 파서 4중 사본(통합 리팩터링).
3. SET 구성 변경 드리프트(트리거: S4-2 착수). 분할 납기(동일 SKU 다중 유상 라인) 미지원. PO 자재 라인 미지원(트리거: P4 사급/자재 발주 요구).
4. 자사 레터헤드 마스터(S3-3 인계), L/C 플래그 토글 경로(S3-3 인계 — S3-1 프로덕션에서 L/C 선택은 닫혀 있음), 거래처 수정 화면 부재(CSV만), 스냅샷 새로고침 액션 부재, 계좌 중복 등록 경합(ADMIN 저빈도).
5. `frozen_at`의 DB 강제 수단·GC-A4 문면과의 관계는 B ADR. 다이제스트(탐지형) 도입 여부 B 판단.
6. 검증 공백(정적 독해 한계): 복합 FK·부분 유니크 인덱스의 `alembic check` 드리프트 0, `version_id_col` 부모 dirty 1회 증가, 제약 이름 길이 실측, `Idempotency-Key`·`ErrorCode` 열거 완전성 테스트 — 전부 PR-A 첫 커밋에서 실행 확인 필요(실행 검증 못 했음).
