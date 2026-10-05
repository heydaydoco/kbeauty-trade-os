# S3-3 계획 설계 — 부록 B: 채권·입금·여신 provider·대금만기

- 기준: main `2092406`(S3-2 종결 — PR-8 #67). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-3 행(W:127-132)과 v1.6 주석(W:132·W:251·W:258)을 따른다. 부채 정본은 PROGRESS 'S3-2 부채 최종 목록'(P:50-166)과 S3-1 계획 등재 P-01~P-60(P:1614-1675)이다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `code:경로:줄` = `backend/app/` 아래 경로, `test:경로:줄` = `backend/tests/` 아래 경로, `ADR-nnnn` = `docs/adr/`. 줄 번호는 `2092406`에서 읽은 값이다.
- 판정 방식: 오너 상시 지시(2026-09-29, CLAUDE.md "결정·개입 없이 끝까지")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. 결정마다 근거와 되돌리기 비용을 적었다. PROGRESS·ADR 등재 시 "자율 확정 — 사후 번복 가능"으로 표기한다.
- 형식: 안건 B1~B21. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 / 되돌리기 비용** 순이다(S3-2 부록 B 선례).
- **실행 검증 못 했음.** 정적 독해만 했다. pytest·alembic·DB·서버는 돌리지 않았다. 금액·날짜 예시는 손계산이며 PR에서 단위·통합 테스트로 실측해 확정한다. "깨질 테스트"는 소스 단언문을 읽고 추론한 것이다.
- 신규 ADR 번호는 **0088부터**(통합에서 부여 — 이 부록은 `ADR-B①…` 가칭), 마이그레이션 번호도 통합에서 부여한다(가칭 `M-B1`·`M-B2`).

---

## 0. 범위와 다른 부록과의 경계

### 0-1. 이 부록이 확정하는 것
- **채권(receivables) 데이터 모델**: 발생 단위·발생 동작(사람 1클릭)·불변·취소(역순)·사슬 등록
- **`payments` 확장(P-09)**: `pi_id` 완화·`receivable_id`·"정확히 하나" CHECK·역기록 복합 FK 2벌
- **미수(outstanding)의 단일 정의**: 채권 총액 − 채권 입금 − 선수금 충당(파생, 저장 안 함 — P-15 재판정)
- **채권 입금·역기록·이중 입력 의심(P-14)·통화 불일치·초과·만료/취소 PI 입금(P-12) 재판정**
- **여신 노출**: 미수 provider 실구현 등록(P-08 — 기본 구현 잔존 금지 테스트), `open_order_amount`의 **채권 전환분 차감**(P-01), 환산 단위
- **SO COMPLETED·short-close(Q-04=P-02)**: provider `reflected=True`와 **같은 PR**(ADR-0076 대체)
- **DoD "선적 확정~미수 발생 구간 노출 공백 0·이중 계산 0"** 증명표와 시험
- **잠금 순서·동시성**: 노출 구성 변경 쓰기의 거래처 잠금 규칙
- **대금만기 배선**: 인보이스일(INVOICE_DATE 앵커) 공급, `lc_terms` → S3-2 순수 함수(`schedule.payment_due`·`presentation_deadline`·`tolerance_bounds`) 배선 — **산식 재정의 금지**(ADR-0081)
- **aging 30/60/90**
- **대금만기·제시기한 알림(Q-08 = R-6-2)**: `trade-deadline-scan` 확장
- **이월 채권 반입(P-51 재판정)**, **L/C 플래그 토글 경로(P-10)**
- 서류 쪽 부채(Q-05·Q-06·R-3a-5·P-11)는 **처리안과 소유 부록만** 적는다(B16)

### 0-2. 경계만 적고 넘기는 것 (다른 부록 소관 — 부록 문자는 통합에서 확정, 아래는 가정)

| 주제 | 소관(가정) | 이 부록이 가정·요구하는 인터페이스 |
|---|---|---|
| CI·PL·S/I 템플릿 렌더링·저장 전 검증·언어 변형·자사 레터헤드(P-11)·SHIPPER 자리(R-3a-5)·PL 중량/CBM(Q-05) | **서류 생성기 부록(A)** | CI가 "발행(동결)" 사건을 갖는 영속 전표면 **CI 발행 TX가 B4의 `open_receivable_for_shipment()`를 같은 TX로 호출**하고 `invoice_on`·`invoice_ref`를 CI에서 복사한다(B1 ③). CI 총액 = 선적 `total_amount`(A의 CI↔선적 교차 검증)를 가정한다. A가 CI를 영속 발행 전표로 두지 않으면 B의 전용 엔드포인트가 유일 경로다. |
| `lc_terms` 본체(MT700 인테이크 → 초안·등록·개정), 하자 체크리스트 화면, tolerance 화면 표시, `lc` 플래그 ON 시 입력 화면 | **L/C 부록(C)** | B10이 요구하는 **최소 열**(유효기일·제시기간·tenor·usance 일수·L/C 금액·통화·tolerance ±bp)과 **선적 단위 제시 기록**(제시일·네고일·인수일)을 C가 제공한다. 배선 어댑터 `lc_inputs_for(session, shipment)`는 B가 소유한다(산식 호출부가 B라서). C가 없으면 B가 최소 열만 직접 소유한다(B10 자율 확정). |
| `LOCK_ORDER`에 `receivables` 자리 삽입·권한 매트릭스 행·임포트 계층 등재·에러코드 카탈로그 1:1 | **인가·잠금 부록(D)** | B9·B17이 요구사항만 적는다(자리: `shipment_children` 뒤·`approvals` 앞). |
| 채권·aging 화면, SO/선적 상세의 채권 패널, 입금 다이얼로그 | **화면 부록(E)** | B18 API 계약을 소비한다. 한국어 UI 규칙(break-keep·nowrap·숫자 가운데)은 E가 고정한다. |
| 선적 RESERVED 5상태 엣지·출고 원장·검수 미완료 CI/PL 차단 본체 | S4-2 | 채권 발생 허용 상태를 "동결 이후 살아 있는 선적"(RELEASE_ORDERED + 장래 RESERVED 5값 중 CANCELLED 제외)으로 정의해 S4-2 개방 시 재작업 0. |
| 은행 CSV 매칭·입금 자동 대사 | P7(D:225 문면) | 이중 입력 의심(P-14)은 사람 확인 409까지만(B5). |
| 대손·환차손익·분개 | 회계(P6·§12) | 채권은 **회계 원장이 아니다**(B2 근거). 대손 처리 경로 없음 = 부채(B21). |
| 바이어 대상 독촉 발송 | S5-4 outbound_policies(D:335) | 알림은 인앱·내부 수신자뿐(B13 4금). |

---

## B1. 채권 발생 단위와 발생 동작

**결정**
- ① **발생 단위 = 수출 선적 1건당 살아 있는 채권 최대 1건**(DB 부분 유니크 `uq_receivables_shipment_live` `WHERE status <> 'CANCELLED'`). 금액 = 선적 `total_amount`(동결 후 불변 — code:modules/shipments/models.py:104-106, 라인 편집은 PLANNED만 — D:192 ①).
- ② **발생은 사람 1클릭(L2)**이다 — 자동 발생 0. 마일스톤 실적·스캔·선적 상태 변화가 채권을 만들지 않는다.
- ③ 발생 동작 = 전용 엔드포인트 `POST /api/v1/shipments/{id}/receivable`(본문 `invoice_on`·`invoice_ref?`) — **CI 발행 전표가 생기면(부록 A) CI 발행 TX가 같은 내부 함수를 호출하고 이 엔드포인트는 "CI 없이 채권만" 경로로 남지 않고 닫는다**(자율 확정 — 아래). 
- ④ 허용 선행 상태: 선적 = **수출(EXPORT) · 동결 이후 살아 있음**(RELEASE_ORDERED, S4-2가 열 PICKING~CLOSED 포함, CANCELLED 제외). PLANNED = 409 `RECEIVABLES.RECEIVABLE.SHIPMENT_NOT_FROZEN`. 수입선적 = 422 `RECEIVABLES.RECEIVABLE.NOT_EXPORT`(매입 채무는 범위 밖).
- ⑤ **사슬 등록**: `ChildLink(DocKind.SHIPMENT, "receivables", "shipment_id")`를 `CHILD_LINKS`(code:modules/trade_docs/chain.py:40-48)에 더한다 → 살아 있는 채권이 있는 선적은 취소 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(역순 취소 — 채권 먼저 취소). 생존 술어 `deleted_at IS NULL AND status NOT IN DEAD_STATUSES`(code:modules/trade_docs/chain.py:133-134)에 맞도록 `receivables`는 `status`(OPEN·CANCELLED)와 `deleted_at`(항상 NULL — CHECK)을 가진다.

**근거**
- ADR-05 사슬 "선적→CI/PL→C/O→**채권**"(D:29, D:177) — 채권은 선적의 후속이다. 후속 생존 시 선행 취소 차단은 §20 A(D:446) 규율 그대로.
- L3 금지 "장부 확정(분개·마감·전표·**원장**)"(D:329). 채권은 회계 원장은 아니지만 **거래처에 대한 청구 사실의 기록**이라 자동 생성은 4금의 취지(장부성 기록의 무인 생성)에 걸린다 → 사람 1클릭이 더 엄격하다.
- 발생 시점을 "적재 실적(ETD·B/L 실적)"으로 묶는 안을 먼저 검토했으나 **기각**: ① 실적은 null+사유로 지울 수 있어(code:modules/trade_chain/milestone_flow.py:429-437) 채권 근거가 사라질 수 있다 ② ETD 앵커 음수 일수(GC-02, 잔금 = ETD−7)처럼 **선적 전 잔금 입금**을 받을 자리가 없어진다(PI 입금은 선수금 청구액까지만 — code:modules/payments/service.py:163 `EXCEEDS_DUE`). 동결(출고지시) 이후면 금액이 확정되고 취소는 사슬이 막는다.
- 선적당 1건: 부분 청구(선적 1건을 인보이스 2장)는 실무상 드물고, 허용하면 "선적 금액 − 채권 합" 잔여를 또 추적해야 한다(노출 공백의 새 축). 필요 시 부채(B21).

**대안**
- (a) SO 단위 채권(전량 출하 후 1건). 기각 — 부분선적의 대금만기가 선적마다 다르다(B/L·ETD 앵커).
- (b) 채권 자동 생성(출고지시 TX에서). 기각 — 4금 취지·사람 확인 없는 청구 기록.
- (c) CI 발행만을 유일 경로로. **조건부 채택** — A가 CI를 영속 발행 전표로 두면 ③대로 CI 발행 = 채권 발생(1클릭 2기록, 이중 버튼 제거). A가 렌더링만 하면 B 엔드포인트가 유일 경로.

**자율 확정**: 확정. ③의 "CI 발행 전표가 있으면 B 엔드포인트를 닫는다"는 **더 엄격한 쪽**(청구 기록과 청구 서류의 불일치 경로 0)이다 — 통합이 A의 CI 모델을 보고 둘 중 하나로 고정한다(멈춰서 보고 ①).

**되돌리기 비용**: 낮음~중간. 발생 경로 추가·통합은 서비스 함수 1개 호출부 변경. 선적당 N건으로 넓히는 것은 부분 유니크 제거 + 잔여 추적(중간).

---

## B2. `receivables` 테이블 (M-B1)

**결정** — 헤더형(라인 없음), 금액 열 불변, 상태 2값.

| 열 | 형 | 규칙 |
|---|---|---|
| id | BIGINT PK | |
| source_kind | VARCHAR(10) | `SHIPMENT` · `OPENING`(이월 — B14) CHECK |
| partner_id | BIGINT FK partners RESTRICT | 바이어. SHIPMENT면 SO `buyer_partner_id` 복사 — 복합 FK `(so_id, partner_id)` → `sales_orders(id, buyer_partner_id)`(신규 UNIQUE) |
| so_id | BIGINT NULL | SHIPMENT 필수·OPENING NULL. 복합 FK `(shipment_id, so_id)` → `shipments(id, so_id)`(신규 UNIQUE) — **수입선적(so_id NULL)은 구조적으로 불가** |
| shipment_id | BIGINT NULL | SHIPMENT 필수·OPENING NULL. 복합 FK `(shipment_id, currency)` → `shipments(id, currency)`(기존 `uq_shipments_id_currency` — code:modules/shipments/models.py:142) — **통화 불일치 DB 거부** |
| currency | CHAR(3) | `^[A-Z]{3}$` |
| gross_amount | BIGINT | 0 ≤ x ≤ 2^53−1. SHIPMENT = 선적 `total_amount` 복사(무상 선적 0 허용 — B7 완결 조건이 균일해진다) |
| fx_rate·fx_rate_date | NUMERIC(18,8)·DATE NULL | SHIPMENT = **SO 헤더 환율 복사**(선적 헤더도 SO 사본 — code:modules/trade_chain/shipment_flow.py:274-276), OPENING = 입력(통화 ≠ KRW면 필수). 기존 `fx_pair`·`krw_fx_is_one` 규약 승계(code:modules/trade_docs/mixins.py:185-191) |
| invoice_on | DATE | KST 인보이스일. ≤ 오늘(KST)·≥ SO 확정 KST일(SHIPMENT) — INVOICE_DATE 앵커의 **단일 원천**(B10) |
| invoice_ref | VARCHAR(60) NULL | CI 번호 등 외부 참조(비유니크·제어문자 금지 — payments `reference` 규약 승계) |
| due_on | DATE NULL | **OPENING만 필수, SHIPMENT는 NULL**(CHECK — 선적 채권의 만기는 파생, 저장 금지 — D:213 ③ "자동 계산은 순수 함수 한 곳") |
| status | VARCHAR(10) | `OPEN` · `CANCELLED` |
| cancel_reason·cancelled_at·cancelled_by_id | | CANCELLED ⇔ 3열 NOT NULL(사유 ≥ 2자) CHECK |
| deleted_at | TIMESTAMPTZ NULL | CHECK `deleted_at IS NULL`(soft delete 경로 없음 — ChildLink 생존 술어 호환용) |
| created_by_id·created_at·version | | |

- **불변**: 앱 계정 UPDATE는 **열 단위 GRANT**(S3-1 PR-2 column grant — `status`·`cancel_*`·`version`만). `gross_amount`·`currency`·`fx_*`·`invoice_on`·`partner_id`·`so_id`·`shipment_id`·`due_on` UPDATE = 42501. DELETE·TRUNCATE revoke.
- **채번 없음**: 화면 식별은 선적 번호(SH-…)+`invoice_ref`. `DocKind` 편입 안 함(상태 기계·채번 접두어·상태이력 표 신설을 피한다 — 상태 2값·엣지 1개).
- 인덱스: `(partner_id) WHERE status='OPEN'`(provider), `(so_id) WHERE status='OPEN'`(차감), `uq_receivables_shipment_live`, `(created_by_id)`.
- 감사: 생성·취소 audit 1행씩(금액·통화·id만, `invoice_ref`·사유 텍스트 미기록 — payments 선례 code:modules/payments/service.py:1-13). outbox `receivables.receivable.opened`·`.cancelled`(id만).

**근거**: §17.5 불변의 DB 강제(D:374), 금액=정수 최소단위+통화(CLAUDE.md), 파생값 비저장(D:213 ③·ADR-0080), payments의 복합 FK 통화 강제 선례(code:modules/payments/models.py:97-105).

**대안**: (a) `DocKind.RECEIVABLE` 편입(상태이력·채번·범용 전이) — 기각, 상태 2값에 커널 비용 과다. (b) 만기 저장 — 기각, 마일스톤 정정 시 이중 정의. (c) INSERT-only 원장 + 취소 역행 — 기각, 채권은 "건" 단위 생존 판정(ChildLink)이 필요하다.

**자율 확정**: 확정. **되돌리기 비용**: 중간(테이블 신설 — 열 추가는 낮음, DocKind 편입 전환은 중간).

---

## B3. `payments` 확장 (P-09, M-B2)

**결정** — 기존 열·CHECK·kind 값 **무변경**(W:129 "기존 컬럼·CHECK·kind 값 변경 금지"), 가산만.
- ① `pi_id` NOT NULL 해제 + `receivable_id BIGINT NULL FK receivables RESTRICT` 추가 + CHECK `num_nonnulls(pi_id, receivable_id) = 1`(`target_exactly_one`).
- ② 역기록 복합 FK **2벌**: 기존 `(reverses_payment_id, pi_id, received_currency)`(code:modules/payments/models.py:100-105)에 더해 `(reverses_payment_id, receivable_id, received_currency)` → `UNIQUE(id, receivable_id, received_currency)`. **MATCH SIMPLE 함정**: PI 입금의 역기록은 `receivable_id` NULL이라 2번째 FK가 검사되지 않고, 채권 입금의 역기록은 `pi_id` NULL이라 1번째 FK가 검사되지 않는다 — 각자 자기 FK만 검사되며, `target_exactly_one`+`kind_sign`(REVERSAL ⇒ `reverses_payment_id NOT NULL` — code:modules/payments/models.py:80-86)이 "대상 열 NULL로 FK 회피"를 막는다. 시험으로 고정(PI 입금을 채권 입금으로 역기록 = 23503).
- ③ 채권 입금의 통화·거래처 DB 강제: 복합 FK `(receivable_id, partner_id, received_currency)` → `receivables(id, partner_id, currency)`(신규 UNIQUE). PI 쪽은 기존 서비스 검증 유지.
- ④ 인덱스 `ix_payments_receivable (receivable_id, id) WHERE receivable_id IS NOT NULL`.
- ⑤ `CONSTRAINT_ERRORS` 번역표(code:modules/payments/service.py:80-) 확장 — "모델·마이그레이션 제약 집합 ⊆ 번역표" 대사 시험이 신규 제약 누락을 잡는다.
- ⑥ `NON_CHILD_FK_ALLOWLIST`에 `("payments", "receivable_id")` 사유 등재(입금은 채권의 사건 기록 — code:modules/trade_docs/chain.py:61-64 선례).

**근거**: ADR-0064·0068, DESIGN §7.10 PR-10a 부기 ①(D:229 — "S3-3은 `pi_id` NOT NULL 완화+`receivable_id`+'둘 중 정확히 하나' CHECK로만 확장").

**대안**: 채권 입금 별도 테이블 — 기각(DESIGN 부기가 같은 원장 확장으로 확정).

**자율 확정**: 확정(문면 그대로). **되돌리기 비용**: 높음(INSERT-only 원장 스키마 — 운영 데이터가 쌓이면 되돌릴 수 없다). 그래서 ②·③ 시험을 PR 첫 커밋에 둔다.

---

## B4. 미수(outstanding)의 단일 정의와 선수금 충당 (P-15 재판정)

**결정**
- ① **파생·비저장.** 단일 정의 `receivables/service.py::outstanding_for(session, receivable_ids | partner_id)` 한 곳.
- ② 채권 i의 미수 = `gross_i − paid_i − advance_alloc_i`.
  - `paid_i` = `SUM(payments.received_amount WHERE receivable_id = i)`(부호 있는 합 — 역기록 포함, `net_received_for_pi` 선례 code:modules/payments/service.py:53-58).
  - `advance_alloc_i` = **선수금 충당액(파생)**: 같은 SO의 PI 순입금 `A = net_received_for_pi(SO.pi_id)`(PI 없으면 0)을 그 SO의 살아 있는 채권에 **id 오름차순 FIFO**로 나눈다 — `cap_i = max(gross_i − paid_i, 0)`, `alloc_i = min(남은 A, cap_i)`. 남은 A는 **미충당 선수금**(SO 단위 표시, 노출 미차감).
- ③ 미수는 항상 ≥ 0(구성상). 채권 입금 상한(B5 ⑤)이 `paid_i ≤ gross_i − alloc_i`를 지키고, 이후 PI 선수금이 늘면 FIFO가 clamp해 초과분은 미충당 선수금으로 남는다(음수 미수 0).
- ④ 채권 상태 표시값(저장 안 함): `UNPAID`(paid+alloc = 0) · `PARTIALLY_PAID` · `PAID`(미수 0) · `CANCELLED`. — WBS 검증 "A(일부입금 전환)"(W:131)은 이 파생 상태의 전환 시험으로 충족한다.
- ⑤ **P-15 재판정 결과**: 노출의 **SO 항**은 종전대로 선수금 미차감(과대 방향 — code:modules/credit/evaluation.py:13), **채권 항**은 충당 후 미수(②)를 쓴다. 근거: 채권 항에서까지 미차감하면 aging이 이미 받은 선수금을 '연체'로 보이게 해 DoD "aging 정확"(W:130)을 깬다. SO 항 차감은 입금↔SO 배분이 PI→SO 활성 1:1(D:179 ①)로 결정적이라 가능하지만, **노출을 줄이는 방향의 신규 산식**이라 이번엔 열지 않는다(더 엄격).

**근거**: X-17 "파생값 이중 저장 금지"(code:modules/trade_chain/payment_status.py:5-6), PI→SO 활성 1:1(D:179 ①), split_advance HALF_UP(code:modules/trade_docs/payment_terms.py:155-164).

**대안**: (a) 충당 행 저장(입금 원장에 '충당' kind) — 기각, kind 값 변경 금지(W:129)·역기록 연쇄. (b) 채권 총액에서 선수금을 미리 뺀 '잔금 채권'으로 생성 — 기각, 선수금 역기록 시 채권 금액이 틀린 채로 굳는다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(파생 함수 1곳 — 저장값 없음).

---

## B5. 채권 입금·역기록·이중 입력·통화·초과·만료 PI (P-12·P-14 재판정)

**결정**
- ① API: `POST /api/v1/receivables/{id}/payments`(TRADE·ADMIN, `Idempotency-Key` 필수, 201). 역기록은 기존 `POST /api/v1/payments/{id}/reversal`을 **대상 열로 분기**(채권 입금이면 채권 경로 — 같은 채권·같은 통화만, 사유 필수). 조회 `GET /api/v1/receivables/{id}/payments`(전 역할·페이지 50).
- ② 오케스트레이터 = `trade_chain/receivable_flow.py`(trade_chain→receivables·payments 정방향 — `payments`는 trade_chain을 임포트하지 않는 규약 유지, code:modules/payments/service.py:3-6). 원장 INSERT·검증·audit·outbox는 `payments.service`의 신규 순수 함수 `record_receivable_receipt`.
- ③ **검증 순서**(먼저 걸린 것이 응답 — PI 입금 E7 순서 승계): 채권 404 → CANCELLED 409 `RECEIVABLES.RECEIVABLE.NOT_OPEN` → 통화 일치 422 `PAYMENTS.PAYMENT.CURRENCY_MISMATCH`(환산 없음) → 금액 파싱 422 → 입금일 ≤ 오늘(KST) 422 → 금액 ≤ 미수 422 `PAYMENTS.PAYMENT.EXCEEDS_DUE` → 이중 입력 의심 409(④).
- ④ **이중 입력 의심(P-14)**: 같은 거래처·같은 통화·같은 금액·입금일 ±3일 안의 살아 있는(역기록되지 않은) 입금이 있으면 409 `PAYMENTS.PAYMENT.POSSIBLE_DUPLICATE`(`detail.payment_ids`). 본문 `acknowledge_duplicates: [id…]`가 그 id 집합을 **정확히** 덮으면 통과(사람 확인 기록 — audit에 확인 id 기록). PI 입금 경로에도 같은 규칙을 넣는다. 은행 참조는 여전히 비유니크(code:modules/payments/models.py:11).
- ⑤ **초과 입금**: 미수 초과 = 422(③). **거래처 단위 미배정 입금(pi·receivable 둘 다 NULL)은 열지 않는다** — `target_exactly_one`이 DB에서 막는다. 초과분 처리(환불·다음 채권 충당)는 부채(B21).
- ⑥ **통화 불일치 입금**(W:129 "재판정"): **422 유지**(재판정 결과 — 환산 입금 기록은 환율 원천 P-22 부재로 금액 근거가 없다). runbook: 은행 원화 환전 입금은 은행 통지서의 원통화 금액으로 기록.
- ⑦ **EXPIRED/CANCELLED PI 입금(P-12)**: **409 유지**(재판정 결과 — code:modules/trade_chain/payment_status.py:61-66). 선적 후 잔금은 PI가 아니라 채권으로 받으므로 실해가 줄었다. runbook: 선수금이 만료 PI로 오면 새 PI 발행.
- ⑧ 채권 입금·역기록은 **거래처 잠금**(B9)을 먼저 잡는다 — 역기록은 노출을 늘리는 쓰기다.
- ⑨ PI 선수금 **역기록**도 채권이 있는 SO에서는 충당액을 줄여 노출을 늘린다 → 기존 `reverse_payment`(code:modules/trade_chain/payment_flow.py:105-151)에 **거래처 잠금 선행**을 추가한다(LOCK_ORDER 거래처 → QT → PI 그대로 — ADR-0078 ①).

**근거**: ADR-0068(방향 반전), D:229 ②(검증 순서), §17.4 멱등(D:370).

**대안**: ④ 경고만(warnings) — 기각, 경고는 무시된다(fail-open). ④ 차단(확인 경로 없음) — 기각, 같은 금액 분할 입금은 실재한다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(서비스 검증·번역표). ④ 창(±3일)은 상수 — 정책 저장소(ADR-0065)로 옮기는 것은 트리거 '오탐 보고 3건'.

---

## B6. 여신 노출 — provider 실구현·채권 전환분 차감·환산 단위 (P-01·P-08)

**결정**
- ① **provider 실구현** `receivables/exposure.py::ReceivableExposure`를 `register_receivable_provider`(code:modules/credit/providers.py:55-60)로 등록한다. 등록 지점 = 공용 부트스트랩 `app/bootstrap.py::register_runtime_providers()`(멱등 — `is_default_provider()`일 때만 등록)를 **API(`main.create_app` — code:main.py:16)·worker·CLI(`cli.main` — code:cli.py:142) 3 입구**가 부른다. 시험 `test_receivable_provider_is_not_default_after_s33`(provider 독스트링이 예고 — code:modules/credit/providers.py:8-9)를 신설해 **3 입구 각각** 부트스트랩 후 `not is_default_provider()`를 단언한다.
- ② **선적분 차감의 정의 = "채권 전환분 차감"**: `open_order_amount(order, invoiced)` = `order.total_amount − invoiced.get(order.id, 0)` — `invoiced` = 그 SO의 **살아 있는 채권 `gross_amount` 합**(SO 통화). 선적 생성·출고지시만으로는 차감하지 않는다(채권이 생기기 전까지 SO 항에 남는다 → 공백 0). `CLOSED_STATUSES`(code:modules/credit/exposure.py:19)는 **무변경**(COMPLETED SO는 B7 완결 조건상 전량이 채권으로 넘어갔거나 잔량이 종결됐다).
- ③ **차감 데이터의 출처 = provider**: Protocol에 `invoiced_by_sales_order(session, so_ids) -> dict[int, int]`를 더한다. 기본 구현은 `{}` — **차감은 provider가 실구현일 때만 구조적으로 존재**한다(P-01 "provider `reflected=True`와 같은 PR에서만"을 코드 구조로 결속). `credit`은 `receivables`를 임포트하지 않는다(의존 방향 유지).
- ④ **환산 단위 = SO 묶음**: 한도 통화 환산(`comparable_amount` — code:modules/trade_docs/fx.py)은 **SO마다 (SO 잔여 + 그 SO 채권들의 미수)를 SO 통화로 먼저 합한 뒤 1회 HALF_UP**한다(환율 = SO 확정 환율 — 채권 `fx_rate`는 SO 사본이라 같다). OPENING 채권은 행마다 자기 환율로 1회. → 채권 발생 전후 환산 노출이 **1 최소단위도 어긋나지 않는다**(항별 환산이면 HALF_UP 반올림이 ±1씩 갈린다 — DoD "이중 계산 0" 위반). 응답 분해값은 `receivable_amount = exposure_after − this_order_amount − open_orders_amount`(차로 정의 — 합 정확).
  - 이를 위해 `credit/evaluation.py::_evaluate`(code:modules/credit/evaluation.py:164-216)의 미결 SO 루프와 provider 호출을 **provider 먼저 1회 호출(SAVEPOINT 안)·SO 루프에서 묶음 환산**으로 재배치한다. provider 예외 처리(55P03·40P01 전파·그 외 UNEVALUABLE — code:modules/credit/evaluation.py:183-193)는 그대로.
- ⑤ **환산 불가 미수**: provider가 `ReceivableNotConvertible(ValueError)`를 던지면 `CURRENCY_NOT_CONVERTIBLE`(UNEVALUABLE)로 번역한다(종전 `RECEIVABLE_PROVIDER_ERROR`와 구분 — 사용자 조치가 다르다: 한도 통화 KRW 설정·환율 입력). 
- ⑥ **불변식 위반(차감 > SO 총액)**: provider가 `ValueError` → UNEVALUABLE(`RECEIVABLE_PROVIDER_ERROR`) — 0으로 깎아 통과시키지 않는다.
- ⑦ `ReceivableTerm`(code:modules/credit/providers.py:21-35)은 유지(`reflected=True, amount=미수 합(한도 통화)`), `exposure_is_partial=false`가 된다 → 프런트 '미수 미반영' 배지가 사라진다(E 소관 — 배지 조건은 `receivables_reflected` 그대로라 코드 변경 0 예상, PR에서 실측).

**근거**: ADR-0064·0076, P-01·P-08(P:1615·1622), DESIGN §7.10 S3-1 부기 ①·④(D:227), exposure 독스트링(code:modules/credit/exposure.py:8-9 "선적분 차감은 … 같은 PR에서만").

**대안**: (a) 선적 생성 시점 차감(선적 `total_amount`) — 기각, 선적~채권 사이 공백(WBS DoD 정면 위반). (b) `CLOSED_STATUSES`를 `("CANCELLED",)`로 좁혀 COMPLETED도 산입 — 기각, COMPLETED의 채권 분과 SO 항이 이중 계산된다. (c) 항별 환산 유지 + 허용 오차 ±1 — 기각, "0"이 DoD 문면.

**자율 확정**: 확정. ④의 평가 함수 재배치는 S3-1 평가 테스트 다수의 기대값을 바꾸지 않아야 한다(같은 통화·KRW 환산 시험은 SO 단위 1회 환산과 결과 동일 — 채권 0일 때 묶음 = SO 항뿐). **되돌리기 비용**: 중간(평가 함수·Protocol 확장 — 시험이 비용을 줄인다).

---

## B7. SO COMPLETED·short-close (Q-04 = P-02, ADR-0076 대체)

**결정**
- ① SO 엣지 **(IN_SHIPMENT → COMPLETED) 1개를 AUTO_TRANSITIONS에 추가**하고 `RESERVED[SO]`에서 COMPLETED를 뺀다(code:modules/trade_docs/machine.py:113-120), `TERMINAL_STATUSES[SO]`에 COMPLETED 추가(code:modules/trade_docs/machine.py:144), `REASON_REQUIRED_TO[SO]`에 COMPLETED 추가(상태이력 CHECK `reason_required` 동반 마이그레이션 — code:modules/trade_docs/machine.py:240).
- ② **완결 판정 함수 하나** `chain_ops.converge_sales_order_completion(session, so_id, *, actor_user_id, cause)`: SO가 IN_SHIPMENT이고 **(가) 살아 있는 선적 전부에 살아 있는 채권이 있고 (나) 살아 있는 채권 ≥ 1이며 (다) SO 전 라인 잔량(`open_quantity` — code:modules/trade_docs/quantities.py:153) = 0 이거나 `short_closed_at IS NOT NULL`**이면 COMPLETED. 호출처 = 채권 발생 TX(B1)와 short-close TX 2곳뿐(핀 시험).
- ③ **short-close** = 사람 동작 `POST /api/v1/sales-orders/{id}/short-close`(TRADE·ADMIN, `Idempotency-Key`, 사유 필수 ≥ 2자). SO에 **사람 결정 사실 3열**(`short_closed_at`·`short_closed_by_id`·`short_close_reason` — SYSTEM 열, 한 번 쓰면 불변 CHECK)을 기록하고 **같은 TX에서 ②를 호출**한다. 엣지 자체는 AUTO 1개뿐(같은 쌍을 사람·자동 양쪽에 두지 않는다 — 총수 단일 계수), 사람 결정은 3열+audit에 남는다. 거부: SO가 IN_SHIPMENT 아님 409 `SALES_ORDERS.SHORT_CLOSE.NOT_IN_SHIPMENT`(접수·확정 단계는 취소 경로) / 잔량 0 409 `NOTHING_TO_CLOSE` / **채권 없는 살아 있는 선적 존재** 409 `SHIPMENT_PENDING`(`detail.shipments`) — 그 선적을 채권화하거나 취소한 뒤 다시.
- ④ **COMPLETED의 안정성**(되돌림 엣지 0): COMPLETED SO의 살아 있는 선적은 전부 살아 있는 채권을 가지므로 선적 취소는 사슬이 409로 막고(B1 ⑤), 채권 취소는 SO가 COMPLETED면 409 `RECEIVABLES.RECEIVABLE.SO_COMPLETED`로 막는다. COMPLETED SO에 새 선적 = 409(수렴 상태 `SO_SHIPPING_STATES` 밖 — code:modules/trade_chain/chain_ops.py:129). 선적 수렴 함수는 COMPLETED를 건드리지 않는다(code:modules/trade_chain/chain_ops.py:140-141).
- ⑤ **총수 갱신**: 허용 **31방향(사람 18·자동 13) / 미허용 151 / 총 182쌍**, SO **11·45**(code:modules/trade_docs/machine.py:7-8 독스트링·핀 시험 갱신 — 수치는 PR에서 실측 확정).
- ⑥ **아키텍처 시험 개정**: `test_so_completed_stays_reserved_while_the_receivable_provider_is_the_default`(test:architecture/test_doc_machines.py:141-153)를 **대체** — 새 단언 "COMPLETED로 들어가는 엣지가 있으면 ⇒ 부트스트랩 후 provider ≠ 기본값" + "COMPLETED ∈ CLOSED_STATUSES" + "SO 자동 엣지 정확히 3".
- ⑦ 4금 논증(§15 부기 갱신): 도착 상태 COMPLETED는 **이행 완료의 반영**이고 약정 진입이 아니다. 트리거는 사람 1클릭(채권 발생·short-close)과 같은 TX다. 자동 확정 부재 시험(`test_no_auto_confirm_code_path_exists`)의 엔트리에 두 동작(actor 필수)을 더한다.
- ⑧ 잔량 종결의 수량 효과: COMPLETED SO는 `SO_SHIPPING_STATES` 밖이라 새 선적이 막히므로 `open_quantity` 산식은 **무변경**(잔량은 계산상 남지만 소비 경로가 닫힌다). 화면은 "잔량 종결 n개"로 표시(E).

**근거**: WBS v1.6 주석(W:132 "SO COMPLETED 엣지·short-close는 미수 provider `reflected=True` 등록·선적분 노출 차감과 같은 PR"), ADR-0076 되돌리기 비용란("S3-3 provider PR에서 반드시 연다 — 그때 이 ADR을 '대체' 표기"), D:192 ④.

**대안**: (a) short-close를 HUMAN 엣지로(전용 액션) + 전량 완결은 AUTO — 기각, 같은 쌍 이중 분류(총수·Literal 원천 혼선). (b) 채권 없는 선적이 있어도 short-close 허용 — 기각, 그 선적 금액이 COMPLETED 제외로 노출에서 빠진다(공백). (c) COMPLETED → IN_SHIPMENT 복귀 엣지 — 기각, 종결의 의미가 흐려지고 노출 재산입 경로가 늘어난다.

**자율 확정**: 확정. **되돌리기 비용**: 중간(상태이력 CHECK·총수 핀·SO 3열 마이그레이션). 반대로 공백이 실재하면 높음(과소 노출 확정은 소급 불가 — ADR-0076 근거 승계).

---

## B8. DoD — "선적 확정~미수 발생 구간 노출 공백 0·이중 계산 0" 증명표

예: 거래처 한도 USD, SO 1건 USD 1,000,000(최소단위 — 1라인 1,000개 × 1,000), 결제 TT_ADVANCE 30%(선수금 300,000 입금 완료), SO 확정 후.

| 시점(사건) | SO 항(잔여) | 채권 항(미수) | 노출 | 비고 |
|---|---|---|---|---|
| t0 확정 | 1,000,000 | 0 | 1,000,000 | 선수금 미차감(P-15 SO 항) |
| t1 선적1(600개) 생성·출고지시 | 1,000,000 | 0 | 1,000,000 | 선적은 차감 근거가 아니다(B6 ②) — **공백 0** |
| t2 선적1 채권 R1 발생(600,000) | 400,000 | 600,000 − 충당 300,000 = 300,000 | 700,000 | 감소분 = 충당 선수금 300,000(현금 사실) — **이중 0**(600,000은 한 항에만) |
| t3 R1 입금 100,000 | 400,000 | 200,000 | 600,000 | |
| t4 선적2(400개) 출고지시 | 400,000 | 200,000 | 600,000 | 공백 0 |
| t5 R2 발생(400,000) → 잔량 0 → **COMPLETED** | (제외) | 200,000 + 400,000 = 600,000 | 600,000 | 같은 TX — 술어 제외와 채권 전환이 원자적 |
| t5' (대안) t4 대신 short-close | (제외) | 200,000 | 200,000 | 잔량 400,000은 약정 소멸(사람 결정·사유) |
| t6 R1 입금 역기록 100,000 | (제외) | 700,000 | 700,000 | 거래처 잠금 선행(B9) |

- 선수금 0(TT_DEFERRED)이면 t1→t2에서 노출이 **정확히 불변**(1,000,000 = 400,000 + 600,000)이다 — 이것이 DoD 시험의 주 단언이다.
- 한도 KRW·SO 환율 1,350.5(가정)이면 t1·t2 모두 `HALF_UP((400,000+600,000) × 1350.5 / 100)` 1회 환산(B6 ④) — 항별 환산 금지로 반올림 차 0.
- **시험(DoD·K)**: ① 위 표를 통합 시험 1벌(TT_ADVANCE)+1벌(TT_DEFERRED, 노출 불변 단언)+1벌(KRW 환산 불변 단언) ② **속성 시험**: 무작위 사건열(선적 생성·출고지시·채권 발생·취소·입금·역기록·short-close)에서 매 단계 `노출 = Σ_open SO(총액 − 채권 총액) + Σ 미수`이고 "채권 총액 ≤ SO 총액"·"미수 ≥ 0" ③ 동시성: 채권 발생 TX와 여신 평가(잠금 평가)가 경합해도 평가가 보는 노출 ∈ {전, 후}(중간 상태 0 — B9).

**자율 확정**: 확정. **되돌리기 비용**: 해당 없음(시험).

---

## B9. 잠금 순서·동시성

**결정**
- ① **노출 구성 변경 쓰기 규칙**: 노출의 항을 옮기거나 늘리는 쓰기 — 채권 발생·채권 취소·채권 입금·채권/PI 입금 역기록·short-close — 는 **거래처 행 `FOR NO KEY UPDATE`를 `lock_buyer_for_credit`로 먼저**(code:modules/credit/locking.py:32-42 — 단일 진입점 규약 유지) 잡는다. 여신 평가(확정·승인 경로)와 직렬화되어 평가가 반쯤 옮겨진 상태를 보지 않는다. 노출을 줄이기만 하는 PI 선수금 입금은 종전대로(잠금 없음 — 낡게 읽으면 과대 방향).
- ② `LOCK_ORDER`(code:modules/trade_docs/locking.py:41-54)에 **`receivables`를 `shipment_children` 뒤·`approvals` 앞**에 삽입(인가·잠금 부록 D가 ADR-0078 부기로 확정).
  - 채권 발생: 멱등 → 거래처(NO KEY UPDATE) → (PI 있으면) PI `FOR SHARE`(충당액 읽기) → SO `FOR UPDATE`(완결 수렴) → 선적 `FOR UPDATE`(상태 재검사) → receivables INSERT. **CI 발행 TX가 호출하면 A의 CI 잠금은 receivables 앞(선적 자식)으로 D가 배치**.
  - 채권 입금·역기록: 멱등 → 거래처 → PI `FOR SHARE` → SO `FOR SHARE` → receivables 행 `FOR UPDATE` → payments INSERT.
  - short-close: 멱등 → 거래처 → SO `FOR UPDATE` → 선적(id 순 `FOR SHARE` — 채권화 여부 판정) → receivables(읽기).
  - 채권 취소(ADMIN): 멱등 → 거래처 → SO `FOR UPDATE`(COMPLETED 재검사) → 선적 `FOR SHARE` → receivables 행 `FOR UPDATE`.
- ③ 한 TX 거래처 1행 원칙 유지(code:modules/credit/locking.py:11).
- ④ J-07 계측 시험에 위 4경로의 첫 접촉 순서를 `LOCK_ORDER` 색인 오름차순으로 단언(ADR-0078 선례). 경합 시험: 같은 선적 채권 발생 2건 동시 → 1건 성공·1건 409(부분 유니크 번역) / 채권 입금 2건이 합쳐 미수 초과 → 1건 422 / 채권 발생 vs SO 확정(같은 거래처) → 교착 0.

**근거**: §17.2 S3-1 부기 ①(D:362 — 여신 체크 직렬화 = 거래처 행), ADR-0059·0078.

**대안**: 거래처 잠금 없이 "SO 항 먼저 읽고 provider 나중" 순서에 기대기 — 기각, 순서 의존은 B6 ④ 재배치(provider 먼저)와 충돌하고 역기록(증가 방향)을 막지 못한다.

**자율 확정**: 확정. **되돌리기 비용**: 중간(교착 재검증 — 계측 시험이 줄인다).

---

## B10. 대금만기 배선 — 인보이스일·`lc_terms` → S3-2 순수 함수 (WBS v1.6 W:119 ②·W:132)

**결정**
- ① **산식 재정의 금지**: `schedule.payment_due`·`lc_payment_due`·`presentation_deadline`·`tolerance_bounds`(code:modules/trade_docs/schedule.py:266-364)는 **본문 무변경**. 바꾸는 것은 입력 공급뿐.
- ② **INVOICE_DATE 앵커**: `AnchorContext`(code:modules/trade_docs/schedule.py:146-158)에 `invoice: DateValue | None`을 **가산**하고 `resolve_anchor`의 INVOICE_DATE 분기(code:modules/trade_docs/schedule.py:254-255)를 "있으면 그 값(ACTUAL), 없으면 UNKNOWN `INVOICE_NOT_ISSUED`"로 바꾼다 — ETD 대체 금지(GC-05·GC-A13) 유지. 원천 = 그 선적의 살아 있는 채권 `invoice_on`(B2 — 단일 원천). 이것은 "원천 공급"이지 산식 변경이 아니다(S3-2 독스트링이 "원천이 아직 없어 필드가 없다"고 예고 — code:modules/trade_docs/schedule.py:151).
- ③ **L/C 어댑터** `trade_chain/lc_inputs.py::lc_inputs_for(session, shipment) -> LcInputs | None`: 선적의 SO에 등록된(살아 있는) `lc_terms`와 그 선적의 제시 기록에서 `LcInputs(tenor, negotiated_on, accepted_on, usance_days)`(code:modules/trade_docs/schedule.py:161-168)를 만든다. `lc_terms` 없음 = None → 종전 UNKNOWN `LC_TERMS_NOT_REGISTERED`(무변경 — GC-08 유지).
- ④ **배선 지점** = `milestone_view._derived_row`(code:modules/trade_chain/milestone_view.py:377-393) 한 곳: PRESENTATION_DEADLINE 분기의 고정 UNKNOWN(code:modules/trade_chain/milestone_view.py:378-383)을 `presentation_deadline(bl, lc.expiry_on, lc.presentation_days)`로, PAYMENT_DUE 분기의 `payment_due(row, context, None)`(code:modules/trade_chain/milestone_view.py:389)을 `payment_due(row, context(+invoice), lc_inputs_for(...))`로. 스캔·aging·화면은 **이 조립 결과만** 읽는다(정의 이원화 금지 — code:modules/trade_chain/deadline_scan.py:10-11).
- ⑤ **결제조건 원천 = 선적 헤더 사본**(X-01 — code:modules/trade_chain/milestone_view.py:388) 그대로. OPENING 채권은 저장 `due_on`.
- ⑥ **`lc_terms` 최소 열 요구**(C 소관 가정): SO 1:N(개정은 신규 행·이전 행 SUPERSEDED, 살아 있는 행 SO당 1 — 부분 유니크), `expiry_on`·`presentation_days`(1~365, 기본 21 — code:modules/trade_docs/schedule.py:32-33)·`tenor`(SIGHT·USANCE)·`usance_days`·`lc_amount`·`currency`(= SO 통화 — 복합 FK)·`tolerance_plus_bp`·`tolerance_minus_bp`(0~10000)·`latest_shipment_on`. **제시 기록**(선적 1:0..1): `presented_on`·`negotiated_on`·`accepted_on`(KST 날짜, ≤ 오늘). C가 이 표를 두지 않으면 **B가 최소 열만 소유**(자율 확정 — 배선이 입력 없이 열리지 않게).
- ⑦ **tolerance**: 채권 발생 시 L/C SO면 `Σ(살아 있는 채권 gross) ≤ tolerance_bounds(lc_amount, +, −).upper`가 아니면 **422 `RECEIVABLES.RECEIVABLE.LC_AMOUNT_EXCEEDED`**(차단 — 초과 청구 = 하자 확정). 하한 미달은 경고(부분선적이 정상이라 판정 불가). 화면 표시는 C.
- ⑧ **플래그와 읽기**: `lc` 플래그 OFF는 **새 입력만 닫는다**(code:modules/trade_docs/payment_terms.py:140-152 승계). 이미 등록된 `lc_terms`의 만기·제시기한 계산·알림은 OFF여도 계속한다 — 끄면 기존 L/C 건의 기한이 화면·알림에서 사라지는 fail-open을 막는다(§20 H "완전 비활성"은 **기능 진입**의 비활성으로 읽는다 — 멈춰서 보고 ③).

**근거**: ADR-0081(순수 함수·운영 UNKNOWN·S3-3 배선), D:213 ③, W:119 ②.

**대안**: (a) `payment_due`에 인보이스 인자 추가 — 기각, 시그니처 변경은 "산식 재정의"로 읽힐 소지(컨텍스트 가산이 최소). (b) tolerance 초과 경고만 — 기각, 은행 매입 거절 사유를 시스템이 알면서 통과.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(어댑터·조립 1곳). ⑦ 차단 완화는 상수 1개.

---

## B11. L/C 플래그 토글 경로 (P-10)

**결정**
- ① `PUT /api/v1/feature-flags/{code}`(ADMIN 전용, `Idempotency-Key`, 본문 `is_enabled`·`version`)와 설정 화면 토글(E). `code`는 **코드 고정 폐쇄 레지스트리** `FEATURE_FLAG_REGISTRY = {"lc": "L/C 결제"}`만(그 밖 404) — 행 등록만으로 새 기능이 생기는 통로 차단(§15 스케줄 레지스트리 선례 D:333). 행이 없으면 첫 PUT이 INSERT(마이그레이션 시드 금지 — code:modules/platform/service.py:93-104 "함정 ⑩").
- ② audit(이전·이후 값). `GET /api/v1/feature-flags`(ADMIN — 페이지 50).
- ③ **ON 전제 조건 없음**(lc_terms 경로가 같은 릴리스에 있으므로). OFF는 B10 ⑧대로 입력만 닫는다.
- ④ S3-1 G-09(L/C CLI 소단위 — P:1087)는 **구현하지 않고 종결**(화면 경로로 대체 — 영준 클릭 단위 안내 가능).

**근거**: P-10(P:1624), D:179 ④("플래그 행 공급 경로가 없어 S3-1 프로덕션에서 L/C 선택은 닫혀 있다").

**대안**: CLI만 — 기각, 비개발자 운영(CLAUDE.md). 범용 플래그 CRUD — 기각, 데이터로 기능 우회.

**자율 확정**: 확정. 소유는 L/C 부록(C)과 겹친다 — **C가 같은 경로를 정하면 C 우선, 없으면 B**(통합 확정). **되돌리기 비용**: 낮음.

---

## B12. aging 30/60/90

**결정**
- ① 기준일 = **KST 오늘**(인자 주입). 연체일 = 오늘 − 만기. 구간: `NOT_DUE`(≤ 0)·`D1_30`·`D31_60`·`D61_90`·`D91_PLUS`·**`DUE_UNKNOWN`**(만기 UNKNOWN 또는 100% 선수금인데 미수 > 0 등 NOT_APPLICABLE+미수). 미수 0 채권은 제외.
- ② **만기 미상은 '미도래'가 아니다** — 별도 구간·적색 표시(fail-visible). 사유 코드(`INVOICE_NOT_ISSUED`·`LC_TERMS_NOT_REGISTERED`·`LC_INPUT_MISSING`·`ANCHOR_PENDING`)를 그대로 싣는다.
- ③ 만기 = B10 조립 결과(선적 채권)·`due_on`(OPENING). 만기 `basis`(PLANNED·ACTUAL)를 함께 싣는다 — 계획 기준 만기는 '예정' 표기.
- ④ 합계는 **거래처 × 통화** 단위로만 더한다(통화 간 합산 금지). KRW 환산 총계는 전 항이 환산 가능할 때만(채권 `fx_rate` — B6 ④와 같은 규칙), 하나라도 불가면 "환산 불가 n건"(None ≠ 0).
- ⑤ API: `GET /api/v1/receivables`(목록 — 필터 거래처·구간·상태, 페이지 50, CSV UTF-8 BOM), `GET /api/v1/receivables/aging`(거래처×통화×구간 요약, 페이지 50). **쿼리 수 고정**(채권 페이지 → 선적·마일스톤·통관·lc 묶음 로딩 — N+1 0, S3-2 `etd_eta_by_shipment` 선례 code:modules/trade_chain/milestone_view.py:397-420).
- ⑥ 읽기 = 전 역할(전표 회사 공유 자산 — ADR-0067 ①, 판매금액이라 마스킹 비대상 — D:227 ⑥).

**근거**: D:225 "aging 30/60/90", W:130 DoD "aging 정확".

**대안**: 인보이스일 기준 aging — 기각, 문면 "만기 자동"과 결제유형 분기의 의미가 사라진다. 만기 미상을 NOT_DUE로 — 기각(fail-open).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(읽기 계산).

---

## B13. 대금만기·제시기한 알림 (Q-08 = R-6-2)

**결정**
- ① `trade-deadline-scan`(ADR-0084)의 **같은 잡·같은 판정 함수**에 대상 추가 — 새 잡 없음(레지스트리 14행 유지).
- ② **PAYMENT_DUE**(수출 선적 단위): 대상 = 살아 있는 수출 선적 중 **동결 이후** + 대금만기 OK. **충족 신호 = 그 선적의 살아 있는 채권 미수 = 0**. 채권이 아직 없는 선적은 **미충족**(알림이 나간다 — 채권 등록을 잊으면 만기가 조용히 지나가는 fail-open 차단). NOT_APPLICABLE(100% 선수금)이면 대상 아님 — 단 B12 ①의 미수>0 예외는 aging에서만 드러낸다.
- ③ **PRESENTATION_DEADLINE**(L/C 선적): 대상 = 제시기한 OK. **충족 신호 = 제시 기록 `presented_on` 존재**(B10 ⑥). 같은 축으로 `lc_terms.expiry_on`·`latest_shipment_on` 임박도 알린다(D:225 "유효·선적기일 임박 적색") — 선적기일 충족 = ETD·B/L 실적 존재(적재 이행일 — D:213 ④).
- ④ **판정 불가(UNRESOLVED) 알림**: 동결 이후 수출 선적의 대금만기가 UNKNOWN이고 **적재 실적(ETD·B/L)이 있으면** 1회(`deadline-unresolved:` 키 종류 — 기존 분리 규약 code:modules/trade_chain/deadline_scan.py:16-18). L/C 선적에 `lc_terms`가 없고 B/L 실적이 있으면 제시기한도 1회. 적재 전 UNKNOWN은 정상 대기라 알리지 않는다(소음 방지).
- ⑤ 도과 판정: 날짜형 `오늘(KST) > 만기`(기존). milestone_view의 PAYMENT_DUE `is_overdue`(현재 null — code:modules/trade_chain/milestone_view.py:390-393)를 **미충족일 때만** 채운다.
- ⑥ 수신자: **SO 담당자(무역)** → 규칙 → ADMIN(`Routing.DEADLINE`). 선적 담당자(물류일 수 있음)가 아니다 — 대금·L/C 서류는 무역 업무. dedup 키 = `deadline:shipments:{선적 id}:{PAYMENT_DUE|PRESENTATION_DEADLINE|LC_EXPIRY|LC_LATEST_SHIPMENT}/{문턱}@{기일}:{수신자}`(기존 규약 — code:modules/trade_chain/deadline_scan.py:21-26). 발송 직전 재확인(미수·제시 기록 재조회)으로 같은 TX 경합 입금은 `deferred`.
- ⑦ OPENING 채권: `due_on` 기준 PAYMENT_DUE를 `deadline:receivables:{id}:PAYMENT_DUE/…` 키로(entity_type `receivables` — 프런트 `alert-routes.ts` 이동 표에 `/receivables/{id}` 추가, K 출구 계약 시험 — P:28-29 선례).
- ⑧ `SCAN_TYPES` 고정 시험(test:integration/test_trade_deadline_scan.py:760)을 갱신하고 "OEM 4종 ∉ 대상"은 유지. 4금: 상태 전이 0·대외 발송 0·원장 무접촉(스캔은 receivables·payments를 **읽기만** — 임포트 금지 목록에 쓰기 함수 추가).

**근거**: D:213 ⑦("대금만기·제시기한 알림은 충족 신호(입금·제시)가 S3-3에 있어 S3-2에서 내지 않는다"), W:132, Q-08(P:63), deadline_scan 독스트링(code:modules/trade_chain/deadline_scan.py:8-10).

**대안**: (a) 채권이 생긴 선적만 대상 — 기각, 등록 누락 시 무알림(fail-open). (b) 별도 잡 — 기각, 판정 함수 이원화.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(대상 집합 상수·규칙 문턱).

---

## B14. 이월 채권 반입 (P-51 재판정)

**결정**
- ① **같은 릴리스에서** OPENING 채권 수기 등록을 연다: `POST /api/v1/receivables/opening`(**ADMIN 전용**, `Idempotency-Key`) — 거래처·통화·금액·환율(통화 ≠ KRW 필수)·인보이스일·만기(필수)·`invoice_ref`(필수)·근거 메모. 입금은 B5 경로 그대로. 취소 = ADMIN·사유(입금 0일 때만).
- ② **provider는 OPENING 미수도 합산**한다(환산은 행별 1회 — B6 ④).
- ③ **runbook 전환 순서**(영준 클릭 단위 — runbook 소유): (가) 거래처별 이월 미수를 OPENING으로 등록 → (나) 여신 화면에서 '미수 반영'·노출을 확인 → (다) **그 뒤에** S3-1 runbook이 깎아 둔 한도(잔여 한도 — D:227 ④)를 원래 한도로 복원. 순서를 거꾸로 하면 낙관 노출 — runbook에 경고 문구. (가)만 하고 (다)를 안 하면 이중 보수(안전측).
- ④ CSV 일괄 반입은 **열지 않는다**(건수 소규모 가정·검증 경로 증가) — 트리거 '이월 채권 30건 초과'로 부채.

**근거**: P-51(P:1665 — "runbook 완화+S3-3 이월 채권 반입 시 재판정"), provider 독스트링(code:modules/credit/providers.py:6 "실질 위험은 시스템 도입 전 이월 미수뿐").

**대안**: 이월 미수 미지원 유지 — 기각, provider `reflected=True`가 되면 '미수 미반영' 배지가 사라져 **이월 미수 누락이 보이지 않게 된다**(가시성 후퇴 — 지금보다 나빠진다).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(경로 1개·source_kind 1값).

---

## B15. 채권 취소(정정)

**결정** — `POST /api/v1/receivables/{id}/cancel`(**ADMIN 전용**, 사유 필수, `Idempotency-Key`). 거부: 이미 취소 409 / **순입금(paid_i) ≠ 0** 409 `RECEIVABLES.RECEIVABLE.PAYMENTS_EXIST`(입금 역기록 먼저 — 역순) / SO COMPLETED 409 `SO_COMPLETED`(B7 ④). 효과: 상태 CANCELLED → SO 항 복귀(파생)·선적 취소 가능해짐(사슬). 정정 = 취소 + 재발생(D:179 ① "정정=취소+신규" 승계). 선수금 충당은 파생이라 자동 재배분.

**근거**: ADR-0053(동결·취소·정정), §20 A 역순 취소.

**대안**: TRADE도 취소 — 기각(청구 기록 삭제에 준함 — 더 좁은 쪽). **자율 확정**: 확정. **되돌리기 비용**: 낮음(역할 상수).

---

## B16. 서류 쪽 부채 처리안 (Q-05·Q-06·R-3a-5·P-11 — 소유 = 서류 생성기 부록 A·L/C 부록 C)

| ID | 처리안(권고) | 소유 | B와의 접점 |
|---|---|---|---|
| Q-05 중량·CBM·박스 열 | 선적 **라인**에 `net_weight_g`·`gross_weight_g`(정수 그램)·`package_count`·`cbm_milli`(정수 0.001㎥) — PLANNED 편집, 출고지시 동결. G.W. ≥ N.W. CHECK(D:215). 헤더 합은 파생 | A | 없음(금액 무관) |
| Q-06 비거래처 수하인 'TO ORDER' | `shipment_parties`에 CONSIGNEE 자유 문구 행(`partner_id` NULL + `order_text` — 'TO ORDER' / 'TO ORDER OF {은행}')을 **결제유형 LC일 때만** 허용(CHECK + 서비스). 통지처(NOTIFY)는 거래처 유지 | A(·C) | L/C 판정은 B10 `lc_inputs_for`와 같은 원천(선적 헤더 결제유형 사본) |
| R-3a-5 SHIPPER(자사) 자리 | 자사 정보는 거래처가 아니라 **자사 레터헤드 마스터(P-11)**에서 렌더링 시 스냅샷 — `shipment_parties`에 SHIPPER 행을 만들지 않는다(자사 = 거래처 오염 방지) | A | 없음 |
| P-11 자사 레터헤드 마스터 | 단일 행(ADMIN) — 영문 상호·주소·연락처·사업자번호(검증)·로고 파일(`documents`)·서명 이미지, 변경 이력 audit. 서류는 발행 시 스냅샷 | A | 없음 |

**자율 확정**: 소유 배정만 확정(세부는 A·C가 확정 — 이 표는 권고). **되돌리기 비용**: 해당 부록 판단.

---

## B17. 마이그레이션 (가칭 — 번호는 통합)

| 가칭 | 내용 | 역방향 |
|---|---|---|
| M-B1 | `receivables` 신설(B2)·`sales_orders`에 `UNIQUE(id, buyer_partner_id)`·`shipments`에 `UNIQUE(id, so_id)`·SO short-close 3열(+불변 CHECK)·SO 상태이력 `reason_required`에 COMPLETED·열 GRANT·revoke | drop(데이터 없을 때만 — 드라이런) |
| M-B2 | `payments` 확장(B3) — NOT NULL 해제·`receivable_id`·CHECK·UNIQUE 2·복합 FK 2·인덱스 | 채권 입금 행 0일 때만 |
| (C 또는 B) | `lc_terms`·제시 기록(B10 ⑥) | |

- 시드 0(플래그 행 포함). CI 마이그레이션 드라이런·스키마 대사(모델 ↔ DB 제약 집합) 시험 통과 필수.
- **순서 결속**: M-B1·M-B2는 B19 PR-B1a에서, 상태 기계 변경(B7 ①)은 코드 상수라 PR-B1b에서(CHECK `reason_required`만 M-B1에 미리 — 미사용 값 허용은 무해).

---

## B18. API·권한·에러코드 (인가 부록 D가 매트릭스로 확정)

| 엔드포인트 | 역할 | 멱등 | 비고 |
|---|---|---|---|
| `POST /shipments/{id}/receivable` | TRADE·ADMIN | 필수 | B1 — CI 발행 경로가 생기면 닫힘 |
| `POST /receivables/opening` | ADMIN | 필수 | B14 |
| `POST /receivables/{id}/cancel` | ADMIN | 필수 | B15 |
| `POST /receivables/{id}/payments` | TRADE·ADMIN | 필수 | B5 |
| `POST /payments/{id}/reversal` | TRADE·ADMIN | 필수 | 기존 — 대상 분기 |
| `GET /receivables` · `/receivables/{id}` · `/receivables/aging` · `/receivables/{id}/payments` | 전 역할 | — | 페이지 50, CSV |
| `POST /sales-orders/{id}/short-close` | TRADE·ADMIN | 필수 | B7 |
| `PUT /feature-flags/{code}` · `GET /feature-flags` | ADMIN | PUT 필수 | B11 |

- 소유권: 전표 회사 공유 자산(ADR-0067 ①) — 담당자 한정 없음. 부모-자식 불일치(다른 채권의 입금 id) = 404. IDOR 시험(K).
- 신규 에러코드(`<도메인>.<대상>.<사유>`, 문구에 조치 힌트, 카탈로그 1:1): `RECEIVABLES.RECEIVABLE.{SHIPMENT_NOT_FROZEN, NOT_EXPORT, ALREADY_OPEN, NOT_OPEN, PAYMENTS_EXIST, SO_COMPLETED, LC_AMOUNT_EXCEEDED, INVOICE_DATE_INVALID}`, `PAYMENTS.PAYMENT.POSSIBLE_DUPLICATE`, `SALES_ORDERS.SHORT_CLOSE.{NOT_IN_SHIPMENT, NOTHING_TO_CLOSE, SHIPMENT_PENDING}`, `PLATFORM.FEATURE_FLAG.UNKNOWN_CODE`. 오류 우선순위 401→403→404→409→422(ADR-0079 ⑧).
- 쓰기 스키마 `extra=forbid`(P-50 래칫 — 신규 스키마는 처음부터).

---

## B19. PR 분할 제안 (이 부록 몫 — 통합이 전체 순서에 끼움)

| PR | 내용 | 노출 영향 | 결속 |
|---|---|---|---|
| **PR-B1a** | M-B1·M-B2, 채권 발생(B 엔드포인트)·취소·OPENING·채권 입금·역기록 분기·이중 입력 의심(PI 포함)·미수 파생·aging 읽기 API | **0**(provider 기본 — 채권은 노출에 안 보이고 SO 항은 전액 → 과대 방향만) | 이 상태로 운영 개시하지 않는다(채권 화면만 미리) |
| **PR-B1b** | provider 실구현 등록(3 입구)·`invoiced_by_sales_order` 차감·평가 묶음 환산·거래처 잠금 규칙(PI 역기록 포함)·SO COMPLETED 엣지·short-close·ADR-0076 시험 대체·DoD 공백/이중 0 시험·총수 갱신 | **원자 전환** | **WBS v1.6 "같은 PR"** — 쪼개지 않는다 |
| PR-B2 | L/C 어댑터·INVOICE_DATE 앵커·milestone_view 배선·tolerance 차단·플래그 토글(B11) | 없음 | C의 `lc_terms` 병합 뒤 |
| PR-B3 | 스캔 확장(B13)·alert-routes 출구 계약 | 없음 | B1b·B2 뒤 |
| (E) | 채권·aging·short-close·플래그 화면 | — | B1b 뒤 |

- PR-B1a를 B1b와 합칠지는 크기로 통합이 판단(합쳐도 무방 — 결속 조건은 B1b 항목들끼리).

---

## B20. 공통 계약 적용(§17·§18·§22 — 이 부록 범위)

| 계약 | 요구 |
|---|---|
| 1TX(D:358) | 채권 발생 = receivables INSERT + (완결 시) SO 전이·상태이력 + audit + outbox 1TX. CI 발행과 합류 시 같은 TX. 외부 호출 0 |
| 동시성(D:360-364) | B9. version: 채권 취소·SO short-close는 `version` 대조 409 |
| 채번(D:366) | 채권 채번 없음(B2) — 신규 접두어 0 |
| 멱등(D:370) | 쓰기 6종 전부 `Idempotency-Key`. 선적당 살아 있는 채권 1 = 부분 유니크(`WHERE status <> 'CANCELLED'`), 번역 409 `ALREADY_OPEN` |
| 불변(D:374) | payments INSERT-only 유지·receivables 열 GRANT. 앱 계정 UPDATE 금액 열 = 42501 시험(J) |
| 실패(D:382) | 스캔 건별 독립 TX(기존) |
| 인가(§18.1) | B18. 서버측 역할 검사가 잠금 이전(`require_roles`) |
| 시간(렌즈 6) | `invoice_on`·`received_on` = KST 증빙일, 미래 불가·소급 허용. aging 기준일 KST 인자 주입. 만기 basis 병기 |
| 성능(렌즈 7·D:376) | 목록·aging 페이지 50·쿼리 수 고정. provider는 거래처 1명 OPEN 채권만(부분 인덱스) |
| 운영(렌즈 9) | runbook: OPENING 반입 순서(B14 ③)·채권 발생 버튼 위치·short-close 판단 기준·이중 입력 확인 절차·통화 불일치 입금 기록법·`lc` 토글 클릭 단위. 수기 양식에 채권·입금 행(증빙일=실제일·입력일=복구일) |
| 문서(렌즈 10) | DESIGN §7.10 부기(채권 모델·미수 정의·노출 재정의)·§7.2 부기(COMPLETED·short-close)·§15 부기(SO 자동 엣지 3·4금)·§17.2 부기(LOCK_ORDER receivables·거래처 잠금 규칙)·§3 표 맵(receivables·payments 확장). ADR: 채권 발생 단위·노출 전환·COMPLETED(ADR-0076 대체)·payments 확장·L/C 배선·이월 반입 |
| 워크스루(렌즈 11) | 실 HTTP e2e 1쌍: 확정 → 부분선적 2건 → 채권 1 → 입금 → 채권 2 → COMPLETED → aging·노출 확인 / short-close 변형. L/C 변형(플래그 ON → lc_terms → B/L → 제시기한 알림 → 제시 기록 → 알림 소멸) |
| 한국어 UI | 금액·D-N·구간 가운데 정렬, 거래처명·통화 nowrap, 사유 break-keep(E 고정) |

---

## B21. 테스트 배분과 GC 경계 케이스 (GC 신설 후보 — `golden` 마커, 번호는 통합)

| # | 그룹 | 입력 | 기대 | 지키는 것 |
|---|---|---|---|---|
| GB-01 | A·K | B8 표 TT_DEFERRED: 채권 발생 전후 | 노출 정확히 불변 | DoD 이중 0·공백 0 |
| GB-02 | A | B8 표 TT_ADVANCE 30% | t2 노출 = t1 − 300,000(충당) | 충당 정의 |
| GB-03 | A | 한도 KRW·SO USD 환율 1350.5, SO 1,000,001 / 채권 600,001 | 전후 환산 노출 동일(SO 묶음 1회 HALF_UP) | 환산 단위 |
| GB-04 | A | 선적 PLANNED에서 채권 발생 | 409 `SHIPMENT_NOT_FROZEN` | 금액 동결 전제 |
| GB-05 | A | 수입선적 채권 | 422 `NOT_EXPORT`(DB 복합 FK도 거부) | 구조 차단 |
| GB-06 | A | 같은 선적 채권 2회(다른 키) / 같은 키 재요청 | 409 `ALREADY_OPEN` / 같은 응답 1건 | 부분 유니크·멱등 |
| GB-07 | A | 채권 있는 선적 취소 | 409 `SUCCESSOR_ALIVE`(+successors) | 역순 취소 |
| GB-08 | A | 2선적 전량 채권화 | SO COMPLETED(자동·사유 문구·행위자 = 클릭자) | 완결 판정 |
| GB-09 | A | 1선적 채권 + 잔량 400 short-close(사유) / 사유 없음 / 채권 없는 출고지시 선적 존재 | COMPLETED / 422 / 409 `SHIPMENT_PENDING` | short-close 가드 |
| GB-10 | A | COMPLETED SO에 선적 생성 / 채권 취소 | 409 / 409 `SO_COMPLETED` | 종결 안정 |
| GB-11 | A | 채권 600,000·선수금 300,000·입금 300,000 / 추가 1 | PAID / 422 `EXCEEDS_DUE` | 상한 |
| GB-12 | A | 채권 입금 후 선수금 300,000 추가 입금(PI) | 미수 0 유지·미충당 선수금 300,000(음수 미수 0) | FIFO clamp |
| GB-13 | A | 일부입금 → 부분 → 완납 → 역기록 | UNPAID→PARTIALLY_PAID→PAID→PARTIALLY_PAID | WBS 검증 A |
| GB-14 | J | PI 입금을 채권 입금 id로 역기록(직접 INSERT) | 23503 | MATCH SIMPLE 함정 |
| GB-15 | J | 채권 입금 통화 ≠ 채권 통화(직접 INSERT) | FK 위반 | DB 통화 강제 |
| GB-16 | A | 같은 거래처·금액·입금일 +2일 입금 / ack 포함 재요청 / ack 집합 불일치 | 409 `POSSIBLE_DUPLICATE` / 201 / 409 | P-14 |
| GB-17 | A | INVOICE_DATE 앵커 +60, 채권 `invoice_on` 2026-11-02 / 채권 없음 | 2027-01-01 ACTUAL / UNKNOWN `INVOICE_NOT_ISSUED` | 배선·대체 금지 |
| GB-18 | K | L/C SIGHT, lc_terms 유효 2027-03-31·제시 21, B/L 실적 2027-03-01, 네고 2027-03-10 | 제시기한 2027-03-22 / 대금만기 2027-03-10 | S3-2 산식 그대로 배선 |
| GB-19 | K | L/C SO, lc_terms 없음 | 대금만기·제시기한 UNKNOWN `LC_TERMS_NOT_REGISTERED`(GC-08 유지) | 미등록 fail-visible |
| GB-20 | B | L/C 금액 1,000,000 +5%, 채권 합 1,050,001 / 1,050,000 | 422 `LC_AMOUNT_EXCEEDED` / 성공 | tolerance 경계 포함 |
| GB-21 | H | 출고지시 선적, 대금만기 D-3, 채권 없음 | 알림 1(SO 담당자) | 미등록 fail-closed |
| GB-22 | H | 같은 건 미수 0 후 재스캔 / 미수 > 0 만기+1 | 알림 0 / 도과 1 | 충족 신호 |
| GB-23 | H | 대금만기 UNKNOWN + ETD 실적 있음 / ETD 실적 없음 | UNRESOLVED 1회 / 0 | 판정 불가 알림 범위 |
| GB-24 | H | 제시기한 D-1 후 `presented_on` 기록 → 재스캔 | 0 | 제시 충족 |
| GB-25 | A | aging 기준일 2027-01-31, 만기 2027-01-31 / 2027-01-01 / 2026-12-31 / 2026-12-02 / 2026-12-01 / 2026-11-02 / 2026-11-01 / UNKNOWN | NOT_DUE(0) / D1_30(30) / D31_60(31) / D31_60(60) / D61_90(61) / D61_90(90) / D91_PLUS(91) / DUE_UNKNOWN | 구간 경계(손계산 — 실측 확정) |
| GB-26 | H | `lc` 플래그 OFF 후 기존 L/C 선적 제시기한 | 계산·알림 계속 / 신규 L/C 입력 422 | 플래그 = 입력만 |
| GB-27 | K | 부트스트랩 3 입구 | `not is_default_provider()` | P-08 DoD |
| GB-28 | J | 채권 발생 vs 같은 거래처 SO 확정 20회 경합 | 교착 0·평가 노출 ∈ {전, 후} | 잠금 규칙 |

- 추가 아키텍처 시험(GC 외): `converge_sales_order_completion` 호출처 핀(2곳) · `credit`→`receivables` 임포트 0 · `payments`→`trade_chain` 임포트 0(기존 유지) · 스캔이 receivables·payments 쓰기 함수 임포트 0 · `FEATURE_FLAG_REGISTRY` 폐쇄 · 제약 집합 ⊆ 번역표(payments·receivables) · SO 자동 엣지 정확히 3.
- **깨질 기존 시험(예상)**: test:architecture/test_doc_machines.py:141-153(대체), 상태 총수 핀(30→31), test:integration/test_trade_deadline_scan.py:760(SCAN_TYPES), test:integration/test_credit_evaluation.py:192-202(기본 provider 단언은 픽스처 `reset_receivable_provider_for_tests`로 격리 유지 — 실 provider 시험 추가), S3-2 milestone_view PRESENTATION_DEADLINE 고정 UNKNOWN 단언(배선 후 lc_terms 없음일 때만 UNKNOWN).

---

## 부록 요약 — 멈춰서 보고할 항목(DESIGN·WBS 부기와 ADR로 해소 전제)

1. **채권 발생 동작의 소유(B1 ③)**: DESIGN 사슬(D:29)상 채권은 CI 뒤다. 서류 생성기 부록이 CI를 영속 발행 전표로 두면 "CI 발행 = 채권 발생" 단일 경로, 아니면 B 엔드포인트. 통합이 하나로 고정(자율 확정 기본 = 더 엄격한 단일 경로).
2. **노출 산식 문면 변경(B6)**: D:227 ①의 "미결 SO 합"을 **"미결 SO 잔여(총액 − 채권 전환분) 합"**으로, 환산을 **SO 묶음 1회**로 부기. ADR-0064 부기·ADR-0076 **대체** 표기.
3. **`lc` 플래그 OFF의 의미(B10 ⑧)**: §20 H "기능 플래그 오프 완전 비활성"을 '입력 진입 비활성 — 기존 L/C 기한 계산·알림은 계속'으로 해석 부기(끄면 기한이 사라지는 fail-open 방지).
4. **WBS S3-3 문면 대비**: "payments(부분)"은 채권 입금 부분 허용으로 충족, "receivables(만기 자동)"은 **만기 저장 없이 파생**(OPENING만 저장) — v1.7 주석 후보.
5. **SO 상태 총수 30→31**(사람 18·자동 13) — D:192 ⑤·§15 부기 갱신.

## 부채 등재 후보(이 부록)
- 선적 1건 다중 인보이스(부분 청구) — 트리거: 실수요 1건(B1).
- 초과 입금·미배정 입금·환불 경로 — 트리거: 초과 입금 1건(B5 ⑤).
- 대손(write-off)·환차손익 — 소유 회계(P6) — 트리거: 장기 미수 정리 요구. 그 전까지 미수는 노출에 영구 산입(안전측).
- SO 항 선수금 차감(P-15 SO 측) — 트리거: 노출 과대로 정당 수주 승인 반복 보고.
- 이중 입력 의심 창(±3일) 정책화 — 트리거: 오탐 3건.
- OPENING CSV 일괄 반입 — 트리거: 30건 초과.
- L/C 하한 tolerance 미달 판정(부분선적 종결 시) — 트리거: L/C 잔액 미청구 사고.
- 채권 통화 ≠ L/C 통화(이중 통화 L/C) 미지원 — 트리거: 실수요.
