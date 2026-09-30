# S3-1 계획 설계 — 묶음 E 판정서: 여신 게이트·PI 입금 게이트·직렬화·정책 설정

- 작성: 묶음 E 심판(최종 설계자). 오너 지시(2026-09-29)에 따른 **자율 확정** — 아래 결정은 전부 "확정"이며 사후 번복 가능하다(안건마다 되돌리기 비용 병기). "미정" 결론 없음.
- 방법: 3개 관점(strict/risk/fit) 제안의 사실 주장을 코드·DESIGN·이미 작성된 A·B·C 판정서(`d-A.md`·`d-B.md`·`d-C.md`)와 직접 대조했다. **정적 독해만 했다 — pytest·DB·서버 미실행, 실행 검증 못 했음**(PostgreSQL 잠금 호환·SQLAlchemy 렌더링·CHECK 식은 구현 첫 커밋에서 실행 확인 필요 — 해당 지점마다 표기).
- 안건 번호는 통합 검토 편의를 위해 새로 매겼다(E1~E11). 제안 qid 대응은 각 안건 머리에 적는다.

---

## 0. 심판 노트

### 0-1. 제안 사실 주장 검증 (틀린 것은 걸러냈다)

| # | 주장(출처) | 확인 | 판정에 미친 영향 |
|---|---|---|---|
| 1 | `kbos_app` `lock_timeout=5s`(55P03), 핸들러에 55P03·40P01 매핑 없음 — 3안 공통 | **맞음**. `infra/postgres/init/00-roles.sql:37-40`(statement 30s·lock 5s·idle_in_tx 60s), `core/errors/handlers.py:128-133`(등록 5개: AppError·검증·HTTP·StaleData·Exception뿐). 결과: 잠금 경합이 `INTERNAL.UNEXPECTED` 500 | 공통 핸들러 신설(E3). 단 **A13·B8·C X3 세 판정서가 같은 핸들러를 각자 "이번 PR 포함"으로 적었다 — 구현 소유는 1곳이어야 한다**(E3 §충돌) |
| 2 | 제안 2: "멱등 지문이 없을 수 있다 [실행 검증 못 했음]", 새 코드 `COMMON.IDEMPOTENCY.KEY_REUSED`(422) 신설(X3) | **틀림**. `idempotency/service.py:63-118`에 `fingerprint()`(정규화 JSON sha256)와 지문 불일치 시 `ErrorCode.IDEMPOTENCY_KEY_CONFLICT`(409, catalog 기존) 이미 존재 | 신규 코드 기각. 기존 코드 재사용(E10) |
| 3 | `FOR UPDATE`는 자식 INSERT의 `FOR KEY SHARE`와 충돌 → 무관한 견적·SO 작성까지 막음, `FOR NO KEY UPDATE`는 비충돌(제안 2) | **참**(PostgreSQL 행 잠금 호환표 — `FOR KEY SHARE`는 `FOR UPDATE`와만 충돌). SQLAlchemy `with_for_update(key_share=True)` = `FOR NO KEY UPDATE`. **실행 검증 못 했음** → 구현 첫 커밋에서 렌더 SQL 확인+비블록 증명 테스트로 고정 | 잠금 단위 = 거래처 행 `FOR NO KEY UPDATE`(E3). 제안 1·3의 `FOR UPDATE`는 기각 |
| 4 | "기존 require_* for_update 관용"(제안 1) | **부분 맞음**. `for_update=True` 인자 관용은 requirements·certifications·collaboration에 있고, partners에는 없다(`partners/service.py` 어디에도 잠금 조회 없음). `imports/registry.py:274-280`이 partners를 `FOR UPDATE`로 잠근다(임포트 확정) | 잠금 헬퍼를 신설. 임포트의 `FOR UPDATE`와도 충돌하므로 한도 변경×확정이 직렬화된다(E3·E9) |
| 5 | "단건 PATCH·CLI로도 여신한도를 바꾼다"(제안 1·2 X1) | **틀림**. `partners/router.py`에 PATCH 없음(POST 등록·GET·item-codes만, `CAN_REGISTER=(TRADE,)`), CLI에 여신 경로 0건(`grep credit app/cli.py` 0). **한도 값이 바뀌는 쓰기 경로 = ① `create_partner`(등록 시 초기 한도) ② 임포트 `confirm_staging`→`PartnersTarget.create/apply_changes`(`imports/registry.py:299-333`) 둘뿐** | X1 통제 지점을 2곳으로 정확히 특정(E9). A의 부채("거래처 수정 화면 부재 — CSV만")와 정합 |
| 6 | 제안 2: PI `advance_amount` 발행 시 스냅샷 저장(ROUND_HALF_UP), 제안 1: `ceil` 올림, 제안 3: `total×pct/100` HALF_UP | **A5와 충돌**. A5는 "선수금 계산은 **저장하지 않고 함수** `split_advance(total, advance_pct_bp)`, 선수금 = `(total×bp+5000)//10000`(HALF_UP), 잔금 = 총액−선수금"으로 확정(`d-A.md` A5). `advance_pct_bp`는 bp 정수 | 제안 1의 올림·제안 2의 저장 열 기각 — **`split_advance` 단일 출처**(청구서에 찍힌 금액과 게이트 요구액이 어긋나는 결함 원천 제거). 제안 3의 `advance_pct/100`도 bp 정수와 어긋나 기각 |
| 7 | 제안 1: PI 상태 PARTIALLY_PAID = `0<순입금<PI 총액`, PAID = `≥ PI 총액` | **B1과 충돌**. B1은 `derive_pi_status(received_total, due_amount)`에서 `due_amount` = **선수금 청구액**(0→ISSUED, 0<x<due→PARTIALLY_PAID, x≥due>0→PAID)이고 `converge_payment_status(session, pi_id, received_total_amount, actor_user_id)` 단일 진입점 확정 | B 채택. `due_amount`는 컬럼이 아니라 `split_advance(PI.total, bp).advance` — B가 "가정명 `advance_due_amount`"로 둔 컬럼은 **만들지 않는다**(A5 파생값 이중 저장 금지) |
| 8 | 제안 2·3: 초과 시 확정 요청이 승인 요청을 자동 생성(202 또는 422 후 커밋) | **C X1과 충돌**. C X1 계약 1: "승인 요청은 SO 확정 시도의 부작용이 아니다 — 확정 시도는 거부+안내만, 승인 요청은 명시 버튼(두 번째 동작)". 제안 1이 이와 일치 | 제안 1 채택(E4·E5). 자동 생성 기각 |
| 9 | 제안 2·3: 통화 불일치·불능은 "승인 요구"로 취급 | **C6과 충돌**. C6: `TargetSnapshot.amount`는 "현재 승인이 필요한 금액", **평가 불능이면 spec이 자기 AppError를 던져 fail-closed**, approvals `basis_amount>0` CHECK — 금액을 모르면 승인 상한을 정할 수 없다 | 통화·산정 불능(UNEVALUABLE)은 **승인 경로 없이 확정 거부**(E1·E4). 제안 2·3 기각 |
| 10 | 제안 1·2·3: 한도 통화와 다른 통화가 하나라도 있으면 무조건 UNEVALUABLE | **A7이 이미 좁혔다**. A7 `comparable_amount(amount, doc_currency, fx_rate, target_currency)`: 같은 통화=원액, `target='KRW'`+전표 환율 있으면 `to_krw`, 그 외만 `None`. A는 "E는 여신 노출에 이 함수를 쓰고 `None`을 통과로 취급하지 않는다"를 E에 요구. 수출 주문 대부분이 USD라 전부 UNEVALUABLE이면 게이트가 무용 | A7 `comparable_amount` 채택(E1). 제안 3개의 "무조건 UNEVALUABLE" 기각 |
| 11 | 제안 3: 입금 `amount>0`+`kind`로 부호 결정(역기록도 양수 행) | ADR-05 문면("역기록 = 원본 불변 + **반대 부호**의 신규 기록")과 어긋남 | 제안 1의 **부호 있는 금액**(RECEIPT>0·REVERSAL<0, CHECK로 kind와 결속) 채택. 순입금 = `SUM(received_amount)` 하나 — kind 분기 합산 결함 소지 제거(E7) |
| 12 | 제안 2: `payments`+`payment_allocations` 2테이블 | 소비자 없는 테이블(ADR-0041 "죽은 문"). S3-1의 입금 대상은 PI 1종뿐 | 기각. `payments` 1테이블(E7) |
| 13 | 제안 2: `bank_reference` 필수+`UNIQUE(partner, ref) WHERE kind='RECEIPT'` | 부분 정정(역기록+재입금)이 같은 은행 참조로 재입력되면 유니크에 걸려 정상 정정 경로가 막힌다(유니크는 "역기록되지 않은" 행만으로 술어를 못 건다) | 유니크 기각. 참조는 필수 텍스트로 두되 유니크 없음(E7), 이중 입력 탐지는 부채(S3-3/P7 은행 CSV 매칭) |
| 14 | 제안 2: `policy_key`에 CHECK를 두지 않는다 | C1이 "CHECK == 열거 자동 대사" 관용을 확정. 폐쇄 열거+CHECK가 fail-closed(원시 INSERT 방어) | 제안 1 채택: 키·값 형 CHECK+코드 레지스트리 1:1 대사 테스트(E8) |
| 15 | 제안 2: CLI `seed-policies`로 `price_deviation.tolerance_bp=500`(±5%) 시드 | 500은 근거 없는 추측값(제안 자신도 "추측 명기"). 마이그레이션·CLI 시드는 함정 ⑩(ActorMixin users FK)·오너 판단 영역 | 기각. 첫 저장 = 관리자가 화면에서(E8) |
| 16 | 제안 3: 정책 조회 GET을 ADMIN·TRADE에 개방 | 게이트 응답이 이미 실효 모드·출처를 싣는다(E6). 정책 원장 열람은 설정 화면(ADMIN)에만 필요 | GET·PUT 모두 ADMIN(E8) |
| 17 | 제안 1·2·3: 에러 코드 도메인 `SALES.*`·`SALES_ORDERS.*`·`FINANCE.*` | A는 `TRADE.*`, B는 `TRADE_DOCS.*`, C는 `APPROVALS.*` 확정. 3세그먼트 관용은 동일 | E 코드는 소비 지점 도메인에 맞춰 `TRADE_DOCS.GATE.*`(게이트)·`PAYMENTS.PAYMENT.*`·`POLICIES.POLICY.*`·`PARTNERS.CREDIT_LIMIT.*`로 재명명 |
| 18 | 제안 2·3: SO 컬럼 `credit_gate_outcome`/`credit_gate_result`로 "확정 이후 상태군 ⇒ NOT NULL" CHECK | B가 이미 SO SYSTEM 컬럼 `confirmed_at`과 "확정 후 RECEIVED 회귀 금지" CHECK를 정의(`d-B.md` B1·B2). 상태군 열거를 CHECK에 또 쓰면 이중 정의 | `confirmed_at` 기준 결속 CHECK로 대체(E4). 상태 열거를 CHECK에 복제하지 않는다 |
| 19 | 제안 2: "보류 해제도 확정과 동일 재평가" | B1 전이 7번: `ON_HOLD→CONFIRMED` 재개는 **게이트 재평가 없음**. 확정 이력 보유 ON_HOLD SO는 노출에 계속 산입되므로 재개가 노출을 늘리지 않는다 | 재평가 강제 기각. 대신 노출 술어에 ON_HOLD(확정 이력 있음) 포함(E1) — B와 정합 |
| 20 | 제안 1·2: 노출 술어에 자기 자신 제외 | 필수(재확정·재평가 이중 계산 방지) | 채택 |
| 21 | 제안 2: `_envelope`에 `Retry-After` 헤더 인자 추가 | `_envelope`는 `JSONResponse`에 headers를 안 넣는다(handlers.py:56-70). 헤더가 필요한 소비자가 프런트 어디에도 없다(`lib/api.ts`는 봉투의 `message`를 그대로 표시, 상태별 분기 없음) | 헤더 도입 기각 — 409+안내 문구로 충분 |
| 22 | "409는 프런트가 '먼저 수정했습니다'로 오도"(제안 1·2·3의 503 근거) | **근거 약함**. 프런트 `ApiError`는 서버의 `message`를 그대로 보이고(`lib/api.ts:16-30`), `VERSION_CONFLICT` 전용 분기가 어디에도 없다(`grep VERSION_CONFLICT frontend/src` 0건) | **A·B·C 3판정서가 모두 409로 일치** — 상태 코드는 와이어 취향이므로 3:1 정합을 따르고 코드값으로 구분(E3) |

### 0-2. 충돌 지점별 판정 요약

| 충돌 | 판정 | 이유 |
|---|---|---|
| 잠금 순서: C6(SO→직렬화 잠금→approvals) vs A13·B3(거래처→QT→PI→SO→…) vs 제안 1·3(SO→거래처) vs 제안 2(거래처→SO) | **거래처 → QT → PI → SO → approvals → 라인 → 채번** (A·B·제안 2) — **C6의 순서 문구 수정 요구** | 두 경로가 반대 순서로 같은 두 행을 잡으면 교착이다(예: 승인 결정=SO→거래처, 확정=거래처→SO). 여신 직렬화는 "거래처 단위 조율"이고 거래처가 SO의 상위(1:N)이므로 coarse→fine 순서가 자연스럽고 A·B 두 판정서가 이미 채택. C6의 `TargetSpec.snapshot(lock=True)`는 "직렬화 잠금을 확정 통로와 같은 순서로"라고 추상화돼 있어 스펙 내부 순서만 바꾸면 된다 |
| 잠금 대기 초과 응답: 503(제안 전부) vs 409(A·B·C) | **409, 새 코드 `COMMON.CONCURRENCY.LOCK_BUSY`**(A13 명명) | 0-1 #22. 코드 이름은 A13이 완결 명시(`COMMON.CONCURRENCY.LOCK_BUSY`), B의 `CONCURRENCY.LOCK.BUSY`·C의 `LOCK_TIMEOUT` 후보는 폐기 |
| `statement_timeout` 57014 매핑: A13 서문·C X3("QueryCanceled") 포함, 제안 1·3 제외, 제안 2 504 | **매핑하지 않는다(500 유지)** — 통합 시 A13·C X3에서 57014 문구를 삭제 | "다른 사용자가 처리 중입니다" 문구가 30초 초과 쿼리에는 거짓이다. 30초 초과는 진짜 결함 신호라 fail-visible이 옳다 |
| 여신 마스킹: B 판정서가 "원가·마진·**여신**·PO 금액은 응답 레벨 마스킹(E·F 묶음 규약)"으로 전제 | **여신은 마스킹 비대상 유지**(E2) — B의 문구에서 "여신" 삭제 요구 | PROGRESS 마스킹 원장 #2(2026-08-05 판정)와 ADR-0026이 비대상이고 재판정 결과도 유지 |
| 승인 요청 방식: 제안 1(명시) vs 2·3(자동) | 명시 (C X1 정합) | 0-1 #8 |
| 승인 상한 개념: 제안 1·2 `exposure_after` ceiling vs C `basis_amount`=초과분 | **C: `basis_amount` = 초과분(한도 통화)**. 동치 — `exposure_after ≤ limit + basis` | C6의 `amount > basis_amount → STALE(CAP_EXCEEDED)`이 이미 구현 계약 |
| 입금 정본 시점: WBS S3-1 "PI 입금 게이트" ↔ S3-3 "receivables/payments(부분)" | **S3-1이 `payments` 최소형을 신설, S3-3은 확장** | B가 "E가 해소"로 위임(`d-B.md` D-B3·WBS 충돌 항목). 입금 사실 없이는 A 그룹 "PI 일부입금 SO 게이트" 테스트가 픽스처 조작이 된다. S3-3 재정의(ADR-05 위반) 방지 위해 **처음부터 S3-3이 쓸 이름·부호·불변 규율로** |

---

## E1. 여신 노출 산식 · 통화 · NULL/0 · 경계 · 미수 항 (제안 E1 통합)

**(a) 목적·경계** — 여신 게이트의 판정 값(`CreditEvaluation`)을 계산하는 순수 함수와 그 입력 규약을 정한다. 잠금은 하지 않는다(E3), 승인 절차는 C·E4·E5. 산식 각 항은 fail-visible이어야 한다("못 셈"을 "없음"과 같게 만들지 않는다).

**(b) DESIGN 근거** §7.10 "여신 초과 수주 승인 게이트", §7.4 게이트 "여신", §4.6 "여신한도", §2 승인 대상 "여신 초과 수주", §17.2 "여신 체크만 행 잠금", ADR-05, ADR-0048 근거 ⑥("환율 축이 없어 환산은 추측"), A7 환율 규약, PROGRESS 마스킹 원장 #2. **산식 문면은 설계서에 없다**(r1 B6) — 아래는 "가장 좁은 안전한 결정"이며 **DESIGN §7.10 보강 필요**(E11).

**(c) 데이터 변경** — **테이블·컬럼 없음**(계산값 — 저장은 확정 시점 증적 E4뿐). 코드:
- 모듈 `app/modules/credit/`: `evaluation.py`(`evaluate_credit`, `CreditEvaluation`), `providers.py`(`ReceivableExposureProvider`), `exposure.py`(미결 SO 질의·`open_order_amount`), `locking.py`(E3), `spec.py`(C `TargetSpec` 등록, E4).
- 부분 인덱스(마이그레이션 E4에 동봉): `ix_sales_orders_open_exposure ON sales_orders(buyer_partner_id) WHERE confirmed_at IS NOT NULL AND deleted_at IS NULL AND status NOT IN ('COMPLETED','CANCELLED')`.

**산식 (전부 BIGINT 최소단위, 한도 통화 `partners.credit_limit_currency` 한 통화로만 합산):**

`exposure_after = 미결 SO 합 + 이번 SO 금액 + 미수채권(provider)` — 각 항은 `comparable_amount()`로 한도 통화에 맞춘다.

1. **미결 SO 술어 (단일 정의, `exposure.open_orders_stmt()` 한 곳)**: `buyer_partner_id = :p AND confirmed_at IS NOT NULL AND deleted_at IS NULL AND status NOT IN ('COMPLETED','CANCELLED') AND id <> :self`.
   - 뜻: "한 번이라도 게이트를 통과해 확정된 적 있고 종결(완료·취소)되지 않은 SO". 포함 상태 = CONFIRMED·PARTIALLY_ALLOCATED·ALLOCATED·IN_SHIPMENT, 그리고 **확정 이력이 있는 ON_HOLD**. 제외 = RECEIVED(접수·게이트 전 — 확정 전 약정 아님), 접수 단계 ON_HOLD(`confirmed_at IS NULL`), COMPLETED(선적 완료 — 채권 영역, S3-3 provider), CANCELLED, soft delete, 자기 자신.
   - **음수 집합(제외 열거) 형태**라 후속 세션이 새 상태를 추가해도 조용히 0으로 빠지지 않고 기본 산입된다(B1의 RESERVED 4상태가 이미 CHECK에 있어 지금도 술어가 그 값들을 포함한다).
   - `confirmed_at`은 B가 정의한 SO SYSTEM 컬럼(확정 트랜잭션에서만 채워짐). 제안 2·3의 별도 `credit_gate_*` 표지 대신 이를 쓴다(0-1 #18).
2. **`open_order_amount(so) -> int`**: S3-1은 `so.total_amount` 그대로(선적 0). **이 함수 한 곳이 잔액 식의 유일 정의**다. S3-2는 선적분 차감을 이 함수에 넣되 **선적분 차감은 S3-3 채권 provider가 `reflected=True`로 등록되는 릴리스와 같은 PR에서만**(그 사이 선적 후~미수 발생 전 노출 공백 방지 — E11 인계 계약).
3. **이번 SO 금액** = 평가 대상 SO `total_amount`(자기 자신은 술어에서 제외 → 재확정·재평가에도 이중 계산 없음). 같은 거래처의 **다른 RECEIVED SO는 세지 않는다**(확정 직렬화가 흡수 — 두 SO를 순차 확정하면 두 번째가 첫 번째를 본다).
4. **미수채권**: `ReceivableExposureProvider` 프로토콜 `outstanding(session, partner_id, limit_currency) -> ReceivableTerm(reflected: bool, amount: int | None)`. S3-1 기본 구현 `UnreflectedReceivables`는 **`(reflected=False, amount=None)`만 반환 — 0 반환 금지**(타입상 `None`). 평가 결과에 `receivables_reflected=false`·`exposure_is_partial=true`가 실리고 SO 상세·확정 화면·승인 화면·보드에 **'미수 미반영' 배지를 항상**(색+글자) 표시한다. `amount=None`을 0으로 더하지 않는다. **판정을 막지는 않는다**(막으면 S3-3 전 모든 확정이 영구 정지) — 대신 표시 의무. provider 등록은 모듈 레지스트리 `register_receivable_provider()`(1회만 — 두 번째 등록은 예외), provider가 예외를 던지면 통과가 아니라 **UNEVALUABLE(`RECEIVABLE_PROVIDER_ERROR`)** 로 확정 거부(fail-closed). S3-3이 등록 시 `test_receivable_provider_is_not_default_after_s33`(아키텍처)가 기본 구현 잔존을 실패시키도록 S3-3 DoD에 편입(E11).
5. **선수금 입금분은 노출에서 차감하지 않는다**. 근거: DESIGN에 차감 문면 없음, 차감하려면 입금↔SO 배분이 필요하고 그것은 S3-3 채권 적용 몫, 미차감은 노출 **과대** 방향(초과 → 사람 승인으로 흐를 뿐 막히지 않음)이라 fail-safe, 입금 오기재·역기록이 여신 과소 산정(조용한 통과)으로 이어지는 결합을 S3-1에 들여오지 않는다. 재판정 트리거 = S3-3 채권 적용 도입(E11 등재).
6. **통화 (A7 채택 — 0-1 #10)**: 각 금액을 `comparable_amount(amount, doc_currency, doc_fx_rate, limit_currency)`로 한도 통화에 맞춘다 — 같은 통화=원액, 한도 통화가 KRW이고 전표에 환율이 있으면 `to_krw`(전표 통화 1단위=x KRW, HALF_UP 단일), **그 외는 `None`**. 하나라도 `None`이면 `verdict=UNEVALUABLE`, `reason_codes=['CURRENCY_NOT_CONVERTIBLE']`, `unconverted=[{doc_number(최대 5), currency}]`. **각 미결 SO는 자기 확정 시점 환율 스냅샷(`fx_rate`)으로 환산**한다(확정 후 환율 불변 — A3 동결 열). 환산에 쓴 값은 증적에 남긴다(A7 "E·C에 요구하는 증빙 계약").
   - UNEVALUABLE은 **결과 값으로는 존재**(advisory 조회가 사유를 보여줘야 하므로)하지만 **확정·승인 요청 경로에서는 예외**다(`TRADE_DOCS.GATE.CREDIT_UNEVALUABLE` 422, 승인으로 우회 불가 — E4). 안내: "한도 통화(KRW)와 전표 통화(EUR)를 비교할 수 없습니다. 관리자가 거래처 여신 통화를 KRW로 맞춘 뒤 다시 확정해 주세요."
7. **NULL vs 0 vs 경계**: 한도 NULL(금액·통화 모두 NULL — DB CHECK `credit_limit_pair`가 쌍 불일치 차단) = **여신 관리 안 함 → `NOT_MANAGED`**(통과, 확정 증적에 기록, **환산·노출 계산 자체를 건너뛴다** — 관리 안 하는 거래처가 통화 불일치로 막히지 않게). 한도 **0 = 신용 거래 불가** → 이번 SO 금액>0이면 항상 초과. 초과 판정 = **`exposure_after > limit`(strict)** — 같으면 통과. `excess_amount = exposure_after − limit`(초과일 때만). **이번 SO 금액이 0(전 라인 무상)이면 `WITHIN_LIMIT`(사유 `NO_INCREMENT`)** — 노출을 늘리지 않는 전표가 기존 초과 상태 때문에 막히지 않게(기존 초과 상태의 해소는 별도 사안).
8. **결과 값 객체** `CreditEvaluation`(frozen dataclass): `partner_id, sales_order_id, verdict(NOT_MANAGED|WITHIN_LIMIT|EXCEEDED|UNEVALUABLE), limit_amount|None, limit_currency|None, open_orders_amount|None, this_order_amount|None, receivable{reflected:false, amount:null}, exposure_after_amount|None, excess_amount, exposure_is_partial, reason_codes[], unconverted[], fx_used{doc_currency, fx_rate, fx_rate_date, converted_open_orders:int}, evaluated_at(UTC)` + `to_snapshot() -> dict`(스칼라만·키 ≤20 — C `snapshot` 제약). **원가·마진·PURCHASE 계열 필드는 존재하지 않는다**(E2 아키텍처 테스트).
9. **열거**(전부 소비 코드 있음): `CreditVerdict` StrEnum 4값, `ReasonCode` StrEnum 3값(`CURRENCY_NOT_CONVERTIBLE`·`RECEIVABLE_PROVIDER_ERROR`·`NO_INCREMENT`) — DB CHECK 없음(계산값·저장 안 함; 저장되는 `credit_verdict`는 E4의 3값).

**(d) 4금** — 저촉 없음. 계산값 표시·게이트 판정이며 확정은 사람 1클릭(E4). 법적 판정 문구 없음("초과입니다"는 산술 사실).

**(e) 상태·불변** — 상태 없음. 불변식: ① 미수 항은 절대 0으로 합산되지 않는다 ② `exposure_after`는 provider 미반영이면 `exposure_is_partial=true` ③ UNEVALUABLE은 통과값이 아니다 ④ 경계 strict.

**(f) 테스트 배분**
- **A(서비스 전수)**: 경계 3점(한도 100: 노출 99·100·101 → WITHIN·WITHIN·EXCEEDED, 양방향 자기검사) / 한도 0+금액>0 → EXCEEDED, 금액 0 → WITHIN(NO_INCREMENT) / 한도 NULL → NOT_MANAGED이고 불능 통화 미결 SO가 있어도 통과 / 통화: 동일 통화·KRW 한도+USD 전표(환율 환산 HALF_UP 경계)·한도 USD+전표 KRW → UNEVALUABLE·한도 USD+미결 EUR → UNEVALUABLE(승인 우회 불가는 E4) / **미결 술어 전수**(CONFIRMED·PARTIALLY_ALLOCATED·ALLOCATED·IN_SHIPMENT 포함, 확정 이력 있는 ON_HOLD 포함, 접수 단계 ON_HOLD·RECEIVED·COMPLETED·CANCELLED·soft delete·자기 자신 제외, 타 거래처 제외) / **SO 쪼개기**(한도 100, 60+60 순차 확정 → 두 번째 EXCEEDED) / 미수 provider 기본 → `reflected=false`·`amount is None`이고 노출에 0이 더해지지 않음(`exposure_is_partial=true`) / provider 예외 → UNEVALUABLE(통과 아님) / provider 이중 등록 예외 / 선수금 입금이 있어도 노출 미차감 고정.
- **K(아키텍처)**: `CreditEvaluation`·응답 스키마에 cost·margin·purchase 계열 필드 부재(재귀 키 스캔 — E2) / `open_orders_stmt`·`open_order_amount` 정의가 소스 전체에서 1곳(재구현 스캔) / 금액 컬럼 규약 자동 편입.
- **변이 점검 대상**: `>`→`>=`, provider `None`→`0` 치환, 술어에서 `confirmed_at IS NOT NULL` 제거·ON_HOLD 제외·자기 제외 제거, `comparable_amount`의 `None`을 0/원액으로 대체, `NOT_MANAGED`에서도 환산 수행, `this==0` 분기 제거 → 각각 위 테스트가 실패해야 한다.
- vitest: '미수 미반영' 배지 렌더(누락 시 실패)·UNEVALUABLE 사유 문구.

**(g) 소비·등재** — 소비: E4 확정 통로·E5 credit-check·C `SO_CREDIT_EXCEEDED` TargetSpec·D 보드 카드·S3-2(`open_order_amount` 교체)·S3-3(provider 등록). 등재: 미수 provider 미구현·선수금 미차감·환율 수동 입력 신뢰 한계(TRADE가 낮은 환율을 넣어 노출을 낮출 수 있음 — 증적·화면에 사용 환율·기준일·`fx_rate_age_days` 표시로 fail-visible, 재판정 트리거=A7의 `fx_rates` 마스터 도입) 부채 3건. **DESIGN §7.10 보강 필요**.

**(h) 되돌리기 비용 — 낮음.** 산식은 `credit/` 한 모듈의 순수 함수·provider·상수 단위이고 저장 열이 없어 이관 없음. 선수금 차감 도입·통화 처리 교체는 서비스+테스트 수정. strict→≥ 등 경계 변경은 응답 계약(중간 미만).

---

## E2. 마스킹 재판정 — 여신한도·노출은 마스킹 비대상 유지 (제안 E1 [마스킹 재판정] 통합)

**(a) 목적·경계** — PROGRESS 마스킹 원장 #2·ADR-0026의 **재판정 트리거("여신 게이트 도입 세션(S3-1)")** 를 소진한다. 결론과 조건을 계약으로 고정한다.

**(b) DESIGN 근거** §2 "조회는 원가·마진을 API 응답 레벨에서 마스킹", ADR-0018·0024(보호 대상=원가·마진), ADR-0026 ②, 마스킹 원장 #2·#3(예상 비용 — 같은 계보), `core/logging/redaction.py`(금액 컬럼은 마스킹 키가 아니다 — 원가 키 `purchase_price`·`purchase_amount`·접미 `_cost`·`_margin`만).

**(c) 결정: 비대상 유지.** 근거 3점: ① 한도·노출·SO 총액은 **판매 측 금액**이며 원가·마진 산식에 입력되지 않는다 — 노출에서 마진을 역산할 수 없고(원가 성분 0), SO 판매가는 이미 VIEWER가 열람하는 범위다(ADR-0018 '값에 대한 지식' 논리) ② 승인·확정 화면에 필요한 운영 데이터다(마스킹하면 결재자가 판단 불능) ③ 노출 계산에 PURCHASE 가격이 관여하지 않는다. **조건(계약으로 고정)**: (i) `CreditEvaluation`·credit-check 응답·`confirm_gate_snapshot`·approvals 스냅샷(`detail`)·보드 셀 스키마에 cost/margin/원가 계열 필드가 **구조적으로 없다**(K 아키텍처 스캔) (ii) **재확정 트리거 = 역할 세분화 또는 외부(바이어) 공유 뷰 도입**(제안 2의 트리거 추가 채택 — 외부 공유 링크 §18.1이 있으므로 한도가 바이어에게 노출될 가능성 방지) (iii) 한도 **변경**권은 별개 축으로 ADMIN 전용(E9). 노출 재계산 엔드포인트(credit-check)만 TRADE+ADMIN(E5) — 열람 자체는 SO 상세에 실린 확정 증적으로 전 역할이 본다(마스킹 비대상).

**(d) 4금** — 없음. **(e)** 없음.

**(f) 테스트** — K: ① 여신 응답 스키마(`CreditEvaluationOut`·`ConfirmGatesOut`·`ApprovalCreditDetail`)의 필드명 재귀 스캔에 `cost|margin|purchase|원가` 계열 0건(공회전 방지 자기검사: 스캔이 비어 있지 않음) ② VIEWER가 SO 상세를 열면 `credit_verdict`·`confirm_gate_snapshot`이 보임(의도된 동작으로 고정) ③ approvals 스냅샷 detail에 `is_sensitive_key` 키 부재(C 검증 재사용).

**(g) 소비·등재** — ADR-0026 **부기 문구(5줄)**: "**② 재판정(S3-1 계획, 2026-09-29 자율 확정): 유지.** 여신 게이트·확정 증적·승인 화면·보드에 한도/노출이 실려도 마스킹 비대상 — 노출은 판매금액 합이며 원가·마진을 포함하지 않는다. 고정 장치: 여신 응답 스키마 원가 필드 부재 아키텍처 테스트. **재판정 트리거는 역할 세분화 또는 외부 공유 뷰 도입으로 좁혀 유지**(S3-1 트리거 소진). 한도 변경은 ADMIN 전용(E9 — 열람과 별개 축)." PROGRESS 마스킹 원장 #2를 "재판정 완료(유지)"로 갱신. **B 판정서의 "원가·마진·여신·PO 금액 마스킹" 문구에서 '여신' 제거 요구**.

**(h) 되돌리기 비용 — 중간.** 마스킹 전환 시 ADR-0024 방식(응답 스키마 2종·`response_model=None`)이 필요하나 컬럼·데이터 변경은 없다.

---

## E3. 직렬화 — 잠금 단위·전역 잠금 순서·lock_timeout 계약·동시성 테스트 (제안 E2 통합)

**(a) 목적·경계** — 여신 체크의 "확인→기록" 창을 닫고(§17.2), 신규 핫패스(확정·입금)가 만드는 잠금 경합이 500으로 새지 않게 하고, 전역 잠금 순서를 계약화한다. 재고·할당·잔량 직렬화(S4-1 ADR)는 선점하지 않는다.

**(b) DESIGN 근거** §17.2("잔량을 깨뜨리는 지점(…여신 체크)만 행 잠금 또는 advisory lock으로 확인→기록 직렬화 — **방식은 Phase 4 ADR**, 전면 SERIALIZABLE 금지"), §17.1, §20 J·H("동시 20명 벌크 경합"), GC-F1 문면("실제 동시 실행"), A13·B3·C6 잠금 순서, PROGRESS 함정 ⑨.

**(c) 결정 (S3-1 로컬 결정 — P4 ADR이 대체·통합 가능)**

1. **잠금 단위 = 거래처(partners) 행 `SELECT ... FOR NO KEY UPDATE`**(SQLAlchemy `with_for_update(key_share=True)`), advisory lock 미채택. 이유: ① `FOR UPDATE`는 자식 행(QT·PI·SO·payments) INSERT의 FK 검사 잠금(`FOR KEY SHARE`)과 충돌해 **무관한 신규 작성까지 막는다** — `NO KEY UPDATE`는 비충돌이면서 같은 거래처의 여신 체크끼리·임포트의 partners `FOR UPDATE`(`imports/registry.py:280`)·거래처 UPDATE와는 직렬화된다(한도 변경×확정 교차 불가) ② 트랜잭션 수명 잠금이라 `idle_in_transaction 60s` 함정과 무관 ③ advisory는 정수 키 중앙 레지스트리가 없다(scheduler 8_231_057·seeds 4_900_001 산재) ④ 채번·사용자·디스패처가 이미 행 잠금을 쓴다.
2. **단일 진입점** `credit/locking.py::lock_buyer_for_credit(session, partner_id) -> LockedBuyer`(토큰 dataclass — 잠긴 `Partner` 보유). 조회에 `populate_existing`을 걸어 **잠금 획득 후 한도를 새로 읽는다**(ORM 아이덴티티 캐시 방지). `evaluate_credit`의 첫 인자는 `LockedBuyer`이고 아키텍처 테스트가 (a) `Partner`에 대한 `with_for_update`가 `locking.py` 밖에 없음 (b) `evaluate_credit` 호출부가 모두 `LockedBuyer`를 만들거나 받는 함수 안임을 스캔한다. **한도가 NULL이어도 먼저 잠근다**(NULL→값 변경 경합 방지). advisory `credit-check` 조회(E5)만 예외로 `evaluate_credit_unlocked`(이름·응답에 `advisory=true`)를 쓴다.
3. **전역 잠금 순서 (전 세션 공통 계약 — 변경은 ADR)**:
   `(0) 멱등 claim 행 → (1) partners(바이어) → (2) QT → (3) PI → (4) SO 헤더 → (5) approvals 행 → (6) 라인(id 오름차순) → (7) doc_number_seq(항상 마지막)`.
   - 잠금 전에 거래처·상위 전표 id를 **무잠금 조회**로 얻고, 잠근 뒤 **재확인**(SO의 `buyer_partner_id`·상태 불변) — 달라졌으면 409 `COMMON.CONCURRENCY.VERSION_CONFLICT`(새로고침 안내).
   - **한 트랜잭션은 거래처 행을 최대 1개만 잠근다**(벌크는 건별 독립 트랜잭션 §17.6). 불가피해지면 partner_id 오름차순.
   - **모든 경로가 이 순서를 따른다**: 확정(0→1→2→3→4→5), 승인 결정·소비·무효(`decide_approval`: 무잠금으로 SO·거래처 id 조회 → **1→4→5**), 입금·역기록(B의 `lock_chain(PI)`로 **1→2→3**만 — SO 잠금 없음, 그 뒤 PI 상태 수렴), SO 편집·취소·`void_for_target`(**4→5**), 참조 생성(**1→상위 전표 잠금→채번 마지막**, A13). 승인 결정이 "SO→거래처"였다면 확정("거래처→SO")과 **교착**이므로 C6의 `TargetSpec.snapshot(lock=True)` 내부를 "직렬화 잠금(거래처) → 대상(SO) 행" 순으로 정정한다(**C6·C X1-3의 순서 문구 수정 요구**).
   - 순서는 상수 `LOCK_ORDER=('idempotency','partners','quotations','proforma_invoices','sales_orders','approvals','lines','doc_number_seq')`로 `trade_docs/locking.py`(B의 `lock_chain`과 같은 자리)에 두고, 아키텍처 테스트가 확정·결정·입금 서비스 소스의 `with_for_update`/헬퍼 호출 순서를 이 상수와 대조한다(위반 주석 관용은 A13 방식).
4. **lock_timeout 계약**: 앱 역할 `lock_timeout=5s`(55P03)·교착 검출(40P01)을 `core/errors/handlers.py`에 **`sqlalchemy.exc.OperationalError` 핸들러**로 매핑 — `getattr(exc.orig, "sqlstate", None) in {"55P03","40P01"}`(psycopg 3.3.4 — `requirements.txt:7`)이면 **HTTP 409, 코드 `COMMON.CONCURRENCY.LOCK_BUSY`**, 문구 "다른 사용자가 같은 거래처(전표)를 처리 중입니다. 잠시 후 같은 동작을 다시 시도해 주세요. 입력한 내용은 저장되지 않았습니다.", detail `{}`, 로그에는 `sqlstate`·경로만(SQL·바인딩 파라미터·금액·거래처명 금지 — D13). 그 외 sqlstate·**57014(statement_timeout)** 는 `handle_unexpected` 500 유지(0-2). 핸들러는 UoW가 롤백한 뒤에 응답한다(`core/db/uow.py:70` — 예외 통과 시 `_rollback`); **멱등 claim 행도 같은 트랜잭션이라 롤백되어 같은 키 재시도가 안전**(구현 요건으로 고정). 벌크 확정 결과 표는 이 코드를 행 상태 "재시도 가능"으로 표시한다(D 소비). 구현 소유: **핸들러+코드 1건은 A13이 "이번 PR"에 포함한다고 확정했으므로 A13 PR에서 구현하고 B8·C X3의 중복 항목은 그 1건을 참조한다**(통합 검토가 정리). E는 이 핸들러에 의존하는 테스트(아래)만 추가.
5. **ADR 관계**: 신규 ADR "여신·입금 게이트 계약 — 산식·직렬화·잠금 순서"에 "**§17.2의 방식 결정(Phase 4 ADR)은 여신 체크에 한해 S3-1이 행 잠금으로 선행한다. S4-1이 재고·할당·잔량 직렬화와 통합해 advisory 등으로 바꿀 수 있다. 바꿀 때는 `lock_buyer_for_credit` 내부만 교체하고 호출 계약(`LockedBuyer` 토큰)·전역 잠금 순서표·409 계약은 승계한다**"를 명기.

**(d) 4금** — 없음.

**(e) 상태·불변** — 불변식: 한 거래처의 확정 트랜잭션은 동시에 1개만 노출을 계산·기록한다(커밋 후에야 다음이 본다). 잠금은 트랜잭션 종료 시 자동 해제.

**(f) 테스트 배분**
- **J(동시성·`concurrency` 마커, 실커밋, `run_concurrently`, 스레드별 독립 세션)**:
  ① **결정적 경합**: 한도 1,000·기존 노출 0·같은 거래처 SO 2건(각 600). 평가 직후 호출되는 테스트 이음새 `credit.evaluation._after_evaluate_hook()`(기본 no-op, 아키텍처 테스트가 "프로덕션 기본은 no-op·다른 모듈이 대입하지 않음" 고정)을 패치해 `Barrier(2, timeout=1.5)`에 대기시킨다 — 잠금이 있으면 뒤 스레드가 평가 전에 막혀 앞 스레드 배리어가 타임아웃으로 풀리고 직렬화됨. 기대: 정확히 1건 200(확정·`credit_verdict=WITHIN_LIMIT`)·1건 `APPROVALS.APPROVAL.REQUIRED` 422, `confirmed_at` 있는 SO 정확히 1건. **잠금을 제거하면 둘 다 통과해 이 테스트가 실패해야 한다(변이 확인 1회 기록 — GC-F1 유형)**.
  ② 자연 경합 10회 반복(매회 새 거래처 — 플래키 방지, 1과 같은 기대) ③ 3건 동시(각 400, 한도 1,000) → 정확히 2건 통과 ④ **다른 거래처 2건 동시 확정 → 서로 대기 없이 둘 다 통과**(잠금 범위 증명) ⑤ **비블록 증명**: 확정 트랜잭션이 거래처 잠금 보유 중 같은 거래처의 신규 SO/QT/PI 생성이 `lock_timeout` 내 성공(`FOR NO KEY UPDATE`가 `KEY SHARE`와 비충돌 — 변이: `key_share=True` 제거 시 실패) ⑥ 확정 vs 한도 변경(임포트 확정) 동시 → 결과가 직렬 순서 중 하나와 일치 ⑦ **확정×승인 결정×입금 기록 교차 반복 30회 — 데드락(40P01) 0·500 0** ⑧ **55P03 실접촉**: 별 연결이 거래처 행을 잠근 채 대기, 테스트 전용으로 세션에 `SET lock_timeout='300ms'`(연결 이벤트 fixture)한 확정 요청 → 409+`COMMON.CONCURRENCY.LOCK_BUSY`(500 아님, 로그에 금액·SQL 없음), 잠금 해제 후 **같은 Idempotency-Key 재시도 성공**(claim 롤백 증명) ⑨ 확정 더블클릭(같은 키) → 전표 1건.
- **핸들러 단위**: 가짜 `OperationalError`(orig.sqlstate=55P03·40P01 → 409 LOCK_BUSY / 57014·23505 → 500 유지·의도된 미매핑 회귀 고정 / sqlstate 속성 없음 → 500).
- **K(아키텍처)**: `Partner` `with_for_update`는 `locking.py` 한정 / `evaluate_credit`의 `LockedBuyer` 결속 / `LOCK_ORDER` 대조 스캔(확정·결정·입금) / `_after_evaluate_hook` 기본 no-op.
- **변이 점검 대상**: `with_for_update` 제거, `key_share=True` 제거, `populate_existing` 제거(캐시된 옛 한도 사용), 핸들러 매핑 제거, 잠금 순서 반전(교차 테스트 데드락 검출), 거래처 잠금을 평가 뒤로 이동.

**(g) 소비·등재** — 소비: E4 확정·C `decide_approval`·`consume_approval`(순서 준수)·B `lock_chain`·입금 서비스·D 벌크. 등재: S4-1 ADR 착수 체크리스트에 "여신 잠금 헬퍼 교체 여부·잠금 순서표 승계" / statement_timeout 500 누수 관찰(벌크 경로 도입 세션 재판정) / **DESIGN §17.2 보강 필요**(여신 체크 방식의 S3-1 선행 결정 문구).

**(h) 되돌리기 비용 — 중간.** 잠금 단위를 advisory로 바꾸는 것은 헬퍼 1곳+동시성 테스트 재실행(낮음). 409 계약은 와이어 계약이라 프런트 매핑 동반(낮음~중간). **잠금 순서표 변경은 전 확정·결정·입금 경로 재검증(높음) — 그래서 지금 고정**한다.

---

## E4. 확정 통로의 게이트 시퀀스 · 승인 소비 · 우회 차단 · 증적 저장 (제안 E3 통합)

**(a) 목적·경계** — SO 확정(RECEIVED→CONFIRMED)의 유일 통로 안에서 **PI 입금 게이트(E6)→여신 게이트(E1)→승인 소비(C)→전이** 순서와 실패·성공 처리, 게이트 증적의 DB 저장, 우회 경로 전수 차단을 정한다. 확정 서비스 자체(사전 조건 재검증·D 게이트 4종·할당 포트·QT 수주전환)는 B·D 소유이며 E는 자기 게이트 구간을 계약으로 제공한다.

**(b) DESIGN 근거** §7.4·§7.10, §2 "승인 후 불변·여신 초과 수주", §17.1(확정+승인 소비 한 트랜잭션), §20 A("PI 일부입금 SO 게이트")·H("승인 우회 차단·승인 후 불변"), WBS S3-1 DoD("여신 초과 → 승인 게이트"), C6·X1 계약, B1 전이 1·7.

**(c) 결정**

**확정 트랜잭션 순서(E가 소유하는 구간 = ⑤~⑦)**
```
① 멱등 claim(재수신 → 최초 결과 재생)
② 무잠금 조회로 SO의 buyer_partner_id·pi_id 획득
③ 거래처 잠금(E3)  → ④ 사슬 잠금 QT→PI(lock_chain) → SO FOR UPDATE + version 대조(409)
   + buyer·상태(RECEIVED) 재확인            [A·B 소유: 사전 조건·동결 완전성·원천 재검증·D 게이트 4종]
⑤ PI 입금 게이트 평가(E6) — 부작용 없는 하드 실패를 먼저. BLOCK 미충족·PI_MISSING → 예외(롤백)
⑥ 여신 평가(E1, LockedBuyer 필수) — UNEVALUABLE → 예외(롤백, 승인 경로 없음)
⑦ 승인 소비: spec.snapshot으로 excess를 재계산 → C `consume_approval(...)`을 **항상** 호출
     · NOT_REQUIRED(excess≤0 — 한도 이내·NOT_MANAGED·한도 상향으로 범위 내) → credit_verdict = WITHIN_LIMIT|NOT_MANAGED
     · CONSUMED → credit_verdict = 'APPROVED', credit_approval_id 기록
     · BLOCKED → UoW 정상 종료(커밋: C가 남긴 VOID·audit 보존) 후 `raise result.error`
⑧ record_transition(RECEIVED→CONFIRMED, approval_id=…) — confirmed_at·게이트 5열·snapshot을 같은 flush에 기록
   + QT 수주전환·할당 포트·outbox·audit(B)
⑨ idempotency.complete(200)  → 커밋
```
- **소비를 게이트의 마지막에** 둔다(부작용 있는 단계는 하드 실패가 모두 통과한 뒤). ⑤⑥의 예외는 롤백(쓰기 0)이고, ⑦ BLOCKED만 "실패도 커밋"(C6 호출 규약 — 함정 ⑨).
- **거부 응답은 멱등 결과로 기록하지 않는다 — 성공 확정(200)만 `complete`**. 이유: 거부 뒤 사용자가 승인을 받고 **같은 키로** 재시도할 수 있어야 한다(A13 5번은 "키를 화면 진입 시 1회 생성해 재사용"을 요구 — 거부를 재생하면 승인 후 재확정이 영원히 옛 거부를 받는다. 제안 2의 202 재생 결함). ⑦ 커밋 경로에서 claim 행이 미완료로 남아도 `claim()`이 "결과 없는 선점 = 이어받아 수행"으로 처리한다(`idempotency/service.py:120-125`).

**거부 응답 4종(예외)**
| 코드 | HTTP | 발생 | detail(사용자용 — 금액은 detail에만, 로그 미기재) |
|---|---|---|---|
| `TRADE_DOCS.GATE.PI_ADVANCE_BLOCKED` | 422 | E6 미충족 | `reason(SHORT\|PI_MISSING\|PI_NOT_USABLE), required_amount, received_amount, currency, mode, mode_source, overridable` |
| `TRADE_DOCS.GATE.CREDIT_UNEVALUABLE` | 422 | 통화 불가·provider 오류 | `reason_codes, limit_currency, unconverted` |
| `APPROVALS.APPROVAL.REQUIRED`(C) | 422 | 초과·유효 승인 없음 | E가 spec `detail`로 `limit_amount, limit_currency, exposure_after_amount, excess_amount, receivables_reflected, pending_approval_id\|null`을 공급(C 에러가 `snapshot.detail`을 싣도록 C에 요구) |
| `APPROVALS.APPROVAL.STALE`(C) | 409 | 승인 후 SO 변경·초과분이 승인 상한 초과 | C 정의 |

**증적 저장 — `sales_orders`에 5열 추가(마이그레이션 E-3, additive, 표가 비어 있어 백필 없음)** — SO가 게이트 통과 사실을 **행 자체**가 증명하고, DB가 게이트 없는 확정을 구조적으로 거부한다(§17.5 "CHECK 가능한 불변식은 CHECK", C X1-5 권고 채택).

| 열 | 타입 | 규칙 |
|---|---|---|
| `credit_verdict` | VARCHAR(12) NULL | CHECK IN ('WITHIN_LIMIT','NOT_MANAGED','APPROVED') — 저장값 3종(EXCEEDED·UNEVALUABLE은 확정에 도달할 수 없어 저장 불가) |
| `credit_approval_id` | BIGINT NULL FK approvals(id) RESTRICT | UNIQUE 부분 인덱스 `uq_sales_orders_credit_approval_id (credit_approval_id) WHERE credit_approval_id IS NOT NULL` — **1승인=1SO를 DB가 보장**(C의 CONSUMED 종결과 이중 방어) |
| `pi_gate_verdict` | VARCHAR(16) NULL | CHECK IN ('PASS','NOT_APPLICABLE','WARN_OVERRIDDEN','SKIPPED_OFF') |
| `pi_gate_override_reason` | VARCHAR(500) NULL | `WARN_OVERRIDDEN`일 때만 NOT NULL, 공백 제외 5자 이상 |
| `confirm_gate_snapshot` | JSONB NULL | `jsonb_typeof='object'`. 스칼라만: `{credit:{…CreditEvaluation.to_snapshot…}, pi_advance:{mode, mode_source, basis('PI'\|'SO'), pi_id, required_amount, received_amount, currency, terms_diverge}}` — **D가 자기 4게이트 키를 같은 객체에 추가**(권고; D가 별도 저장을 택하면 통합 검토가 조정) |

CHECK(이름 63자 이내 — A가 실측한 식별자 한도 함정): ① `(confirmed_at IS NULL) = (credit_verdict IS NULL)` ② `(confirmed_at IS NULL) = (pi_gate_verdict IS NULL)` ③ `(confirmed_at IS NULL) = (confirm_gate_snapshot IS NULL)` ④ `((credit_verdict = 'APPROVED') IS TRUE) = (credit_approval_id IS NOT NULL)` ⑤ `((pi_gate_verdict = 'WARN_OVERRIDDEN') IS TRUE) = (pi_gate_override_reason IS NOT NULL)` 및 `pi_gate_override_reason IS NULL OR char_length(btrim(pi_gate_override_reason)) >= 5`. (`= ` 비교가 NULL을 통과시키는 함정을 `IS TRUE`·`IS NULL` 형태로 회피 — 구현 시 원시 INSERT 거부 테스트로 실측.) 5열은 B2 열 분류 레지스트리에 **SYSTEM**으로 등재(확정 트랜잭션에서만 채워지고 이후 불변 — 미등재 시 CI 실패). 확정은 1회(재개는 재평가 없음 — B1)라 재기록 경로가 없다.

**우회 경로 전수 차단 (ADMIN 예외 없음 — 게이트 코드는 역할을 참조하지 않는다)**
1. SO가 CONFIRMED가 되는 **유일 통로 = `record_transition`(B) 호출이 있는 `confirm_sales_order`** — 시그니처·API 스키마에 `force`·`skip`·`bypass` 인자 부재(`extra=forbid`), PATCH/생성 스키마에 `status`·`confirmed_at`·`credit_*`·`pi_gate_*`·`confirm_gate_snapshot` 필드 부재.
2. 벌크(오더 보드)·인테이크 확정·엑셀·CLI·(S5 자동 경로)는 같은 함수를 건별 독립 트랜잭션으로 호출한다. **인테이크·임포트가 만드는 SO는 접수(RECEIVED)로만 생성**(확정 상태·게이트 열을 입력 스키마·임포트 매핑 어디서도 세팅 불가). WARN 모드의 사유 override는 **개별 확정에서만** 가능하고 벌크는 override 없이 호출 → 미충족 행은 "사유 입력 필요 — 개별 확정에서 진행"으로 표시(D 소비).
3. **DB 백스톱**: 위 CHECK ①~③ — 게이트 통과 기록 없이 `confirmed_at`만 채우는 원시 UPDATE·잘못된 신규 경로는 `IntegrityError`(정상 코드가 게이트 결과를 **위조**하는 것까지는 막지 못한다 — 트리거는 ADR-0028·0040 선례대로 쓰지 않는다).
4. 여신한도 조작 우회 = E9. 승인 행을 직접 APPROVED로 만드는 경로 = C의 `decide_approval` 단일 통로·SoD(요청자≠결정자, ADMIN 포함)·`approval_events.actor_user_id NOT NULL`(시스템 행위자 없음).
5. 승인 후 SO 변경 = C6 digest 대조(STALE)·`void_for_target`. 승인 후 다른 SO 확정으로 노출이 늘어 초과분이 승인 상한을 넘으면 STALE(`CAP_EXCEEDED`).
6. 확정 후 SO 편집·soft delete 금지·복원 액션 부재는 B2·B9 소유.
7. `test_no_auto_confirm_code_path_exists` 계보를 SO 확정 전역으로 확장(K).

**(d) 4금** — 저촉 없음. ① 발주 확정 아님(수주 확정 — 그리고 사람 1클릭) ② 승인 결정은 사람(C, 자동 승인 부재 4중 강제) ③ 대외 발송·법적 판정·장부 확정 코드 0. 승인 요청도 사람의 명시 동작(E5).

**(e) 상태·전이·불변** — 전이 RECEIVED→CONFIRMED는 B1 1번. 게이트 5열은 확정 트랜잭션에서만 기록·이후 불변(열 분류+CHECK). 승인 소비는 CONSUMED 종결(C2).

**(f) 테스트 배분**
- **H(승인 우회 차단·승인 후 불변 — 양방향 자기검사)**: ① 한도 내 SO 확정 성공(`WITHIN_LIMIT`) ② 초과 → 422 REQUIRED, SO 상태·`confirmed_at` 불변, **approvals 행 0·events 0**(확정 시도만으로 요청 안 생김) ③ **ADMIN 토큰도 동일 422**(예외 없음) ④ 무역 요청→(다른 사용자·역할 보유) 승인→같은 SO 재확정 200(`APPROVED`·승인 CONSUMED) ⑤ 승인 재사용(같은 승인으로 다른 SO) 거부(REQUIRED) ⑥ 승인 후 SO 라인 수량 변경(서비스 경유) → STALE+VOIDED ⑦ **version을 올리지 않는 raw UPDATE로 단가 변경 → digest 불일치로 소비 거부**(C lazy 백스톱 실증 — 확정 통로에서) ⑧ 승인 후 같은 거래처 다른 SO 확정으로 노출 증가 → 초과분>상한 → STALE ⑨ 승인 후 한도 상향으로 범위 내 → 승인 미소비 정상 확정(NOT_REQUIRED·`WITHIN_LIMIT`)+승인 VOID ⑩ 반려 승인 소비 불가 ⑪ **원시 SQL로 `confirmed_at`만 채우면 CHECK 위반**(마이그레이션 CHECK 존재 증명)·`APPROVED`인데 `credit_approval_id` NULL → 위반·같은 승인 FK 두 SO → UNIQUE 위반 ⑫ BLOCKED 경로가 커밋 후 raise되어 VOID·audit가 남음(C6 ⑦ — 확정 통로에서 재확인).
- **A(서비스 전수)**: 게이트 순서(PI 게이트 실패가 여신 평가·승인 소비보다 먼저 — 승인이 소비되지 않은 채 PI 예외 → 롤백 후 승인 여전히 APPROVED) / UNEVALUABLE에서 승인이 있어도 통과 불가 / 결과 스냅샷 값 정확성(한도·노출·환율).
- **J**: 더블클릭 confirm 같은 키 → 전표 1건·승인 소비 1회 / 확정 중간 실패(소비 후 예외 주입) → 승인 여전히 APPROVED(롤백 실측) / 동시 확정 2요청+승인 1건 → 정확히 1건 CONSUMED(E3 시나리오 재사용).
- **K(아키텍처)**: SO 상태 대입 CONFIRMED 경로 1곳(B 스캔 재사용) / PATCH·생성 스키마 게이트 열 부재 / `force|skip|bypass` 인자 부재 / 인테이크·임포트·보드 서비스 소스에 CONFIRMED 대입·`confirm_sales_order` 외 확정 호출 부재 / `CreditEvaluationOut` 원가 필드 부재(E2).
- **GC v1.4 신설 후보(등재 요청)**: "여신 초과 승인 게이트"(성공 방향=한도 내 확정·동시 경합 포함, 실패 방향=초과 무승인 거부) / "승인 우회 차단(ADMIN 포함)+승인 후 통과" 양방향.
- **변이 점검 대상**: 게이트 순서(승인 소비를 PI 게이트 앞으로), `consume_approval` 호출 제거·결과 무시, `IntegrityError`가 아니라 CHECK 제거, CHECK ① 제거, ADMIN 분기 삽입, BLOCKED를 롤백 raise로 변경(VOID 소멸), 거부 응답을 `complete` 기록(승인 후 재확정 실패) → 각각 실패해야 한다.

**(g) 소비·등재** — 소비: B `record_transition(approval_id=)`·D 보드·프런트 확정 패널. 요청: **C** — `TargetSpec` 등록(E5), 에러 detail에 `snapshot.detail` 탑재, 결재선 임계는 한도 통화와 동일 통화 행만(C3) — 한도 통화의 결재선이 없으면 `APPROVALS.LINE.NOT_CONFIGURED`(422, fail-closed)로 요청 단계에서 안내됨을 화면 문구에 반영. **B** — 열 분류 SYSTEM 등재 5열, `confirmed_at` 결속 CHECK와의 정합. **D** — `confirm_gate_snapshot` 공용 객체. 부채: 확정 후 게이트 재평가 없음(ON_HOLD 재개 시 한도 하향 미반영 — 노출 불변이라 의도) 관찰 등재.

**(h) 되돌리기 비용 — 중간.** 게이트 순서·소비 규칙은 서비스 1곳(낮음). SO 5열+CHECK는 마이그레이션 1건 — **S3-1에 넣는 것이 가장 싸다**(나중 추가는 확정 행 백필 필요, 높음). 확정 응답이 "거부 예외" 방식이라 자동 요청 생성(202)으로 바꾸려면 프런트·벌크 UI 재작업(중간).

---

## E5. 여신 승인 요청·advisory 조회·`SO_CREDIT_EXCEEDED` TargetSpec (제안 E3 일부·C X1 계약 이행)

**(a) 목적·경계** — C X1이 SO 묶음에 위임한 "명시 승인 요청" 엔드포인트와 C의 `TargetSpec` 등록, 재계산 참고 조회를 정한다. 승인의 저장·결정·소비 자체는 C.

**(b) DESIGN 근거** §2 "기안→승인 2단"(기안=사람 행위), §7.10, C1·C6·X1.

**(c) 결정**
1. **`POST /api/v1/sales-orders/{id}/approval-requests`** — TRADE+ADMIN, `Idempotency-Key` 필수, body `{version:int}`(`extra=forbid` — 사용자가 본 SO 버전; 불일치 409). 흐름: 거래처 잠금→SO 잠금→version 대조→상태 RECEIVED 확인→`spec.snapshot(lock=True)`→`approvals.request_approval(approval_type='SO_CREDIT_EXCEEDED', target_id, requested_by)`(호출 트랜잭션에 합류; 같은 스냅샷이면 기존 행 반환 `created=false`, 다르면 기존 VOID 후 신규 — C6). **excess≤0이면 422**(불필요한 승인 금지 — C X1-1), **UNEVALUABLE이면 422 `CREDIT_UNEVALUABLE`**(승인 요청 불가). 응답: 승인 뷰+`created`.
2. **`GET /api/v1/sales-orders/{id}/credit-check`** — TRADE+ADMIN, **잠금 없이** 같은 `evaluate_credit`(unlocked 변형)을 호출해 `CreditEvaluation`+`advisory:true`("참고용 — 확정 시 재평가")+`pending_approval`(있으면 id·상태)를 반환. RECEIVED가 아니면 409(B의 `TRADE_DOCS.TRANSITION.NOT_ALLOWED`). 프런트 훅은 `staleTime 0`(계산값 화면 — 마운트마다 재조회, S2-3 FRESH_EVERY_TIME 선례).
3. **`SO_CREDIT_EXCEEDED` `TargetSpec`**(`credit/spec.py`, 소비 모듈 등록 — C1 (g)): `snapshot(session, so_id, *, lock)` → `TargetSnapshot(label=doc_number, digest=gate_input_digest(so), amount=excess_amount, currency=limit_currency, detail=CreditEvaluation.to_snapshot()의 스칼라(≤20키·4KB), gate_open=(status=='RECEIVED' and deleted_at IS NULL))`. `lock=True`는 **거래처→SO 순서**(E3)로 잠근다. `excess≤0`이면 `amount=0`(C의 NOT_REQUIRED 분기), UNEVALUABLE·NOT 평가 가능이면 **`AppError` 던짐**(C6 fail-closed). `gate_input_digest`는 **A·B(SO 묶음) 소유** — 입력 집합은 C X2 계약대로 "바이어·통화·환율 스냅샷·라인별 SKU/수량/단가/무상 표지; 메모·담당자·version 제외"이며, A·B가 제공하지 않는 경우에 대비해 E 기본안을 명시한다: `sha256(canonical_json({buyer_partner_id, currency, fx_rate(str), fx_rate_date, lines:[[sku_id, quantity, unit_price_amount, is_free]…line_no순]}))`(정수 문자열·NFC 정규화, 골든 벡터 테스트).
4. 소비 접점 스캔: C X2 "소비 접점 스캔은 배선 PR과 같은 PR" — `SO_CREDIT_EXCEEDED` spec 등록과 `consume_approval(` 호출(E4)을 **같은 PR**에 둔다.
5. 화면(프런트): SO 상세의 확정 패널 — 확정 버튼 → 422 REQUIRED를 받으면 **'승인 요청 올리기' 버튼**(→ 위 POST)과 한도·노출·초과분·'미수 미반영' 배지 표시, `pending_approval_id`가 있으면 '승인 대기 중(요청 #n)' 링크(C의 ApprovalPanel), 승인 후 돌아와 재확정.

**(d) 4금** — 요청은 사람의 명시 동작, 자동 요청 생성 경로 0(K 스캔: `request_approval` 호출부가 라우터 함수 1곳뿐이고 확정 서비스·잡·이벤트 핸들러에서 호출되지 않음).

**(e) 상태·불변** — 요청 중 SO 편집은 허용(C: digest 가드가 대신), 요청·승인 상태 전이는 C.

**(f) 테스트** — H/A: 확정 시도만으로 approvals 0 / 명시 요청 후 1건 / 같은 SO 재요청은 기존 반환(`created=false`)·알림 재발생 0 / excess≤0 요청 422 / UNEVALUABLE 요청 422 / VIEWER·LOGISTICS·CERT 요청 403 / 다른 version → 409 / advisory 조회가 잠금을 잡지 않음(다른 세션이 거래처를 잠근 채여도 즉시 응답 — 200ms 이내)·`advisory=true`. K: `request_approval` 호출부 스캔·spec 등록 공회전 방지(len≥1)·digest 골든 벡터. vitest: 422 수신 시 버튼 전환·성공 토스트 없음·배지.

**(g) 소비·등재** — C에 요구: `request_approval`이 호출 트랜잭션에 합류하고 대상 잠금을 재획득하지 않음(E3 순서), 결재선 미설정 422 문구, 알림 라우팅(C7). **(h) 되돌리기 비용 — 낮음**(엔드포인트·스펙 함수 수준).

---

## E6. PI 입금 게이트 — 조건·활성·모드·PI 없음·WARN 사유 (제안 E4 통합)

**(a) 목적·경계** — SO 확정 시 "선수금이 들어왔는가"를 금액으로 판정한다. 입금 사실의 기록·역기록은 E7. 정책 모드 저장은 E8.

**(b) DESIGN 근거** §7.3("입금 매칭 시 상태 자동 전환. **선수금 미입금 SO 확정 게이트(경고/차단 설정)는 결제유형이 선수금 T/T일 때만 활성**(L/C 등 타 결제는 비활성)"), §20 A("PI 일부입금 SO 게이트(선수금 T/T만 활성)"), WBS S3-1 DoD, A5(`TT_ADVANCE`·`split_advance`), B1(PI 수렴).

**(c) 결정**

1. **조건 = 금액 대조**: `net_received ≥ required`이면 통과(**등호 통과**, 부족 = strict 미만). `required = split_advance(PI.total, PI.advance_pct_bp).advance`(A5 — **HALF_UP, 청구서 금액과 동일**), `net_received = net_received_for_pi(session, pi_id)` = 그 PI의 `SUM(payments.received_amount)`(부호 있음). **PI 상태값은 조건에 쓰지 않는다**(선수금 30% 입금 시 PARTIALLY_PAID도 통과해야 하고, 상태 조작으로 우회하는 경로를 차단). `required=0`(bp가 작아 반올림 0)이면 통과(사유 `ZERO_REQUIRED`, 증적에 기록).
2. **기준 조건의 출처 — 참조 SO는 PI 기준**: `so.pi_id IS NOT NULL`이면 결제유형·선수금 %·총액·통화를 **PI의 것**으로 판정하고(`basis='PI'`), PI 없는 SO는 SO 자신의 조건(`basis='SO'`). 이유: A4가 SO 접수 단계에서 결제 조건 편집을 허용하므로 **확정 직전 `TT_ADVANCE`→`TT_DEFERRED`/`LC`로 바꿔 게이트를 끄는 우회**가 가능하다 — PI가 바이어에게 청구한 조건이 정본이다. SO 조건이 PI와 다르면 증적에 `terms_diverge=true`(화면에 "PI 조건 기준으로 판정" 안내). 바이어가 실제로 조건을 바꾼 정당한 경우는 SO 취소 후 QT에서 새 SO를 만든다(A4: QT→SO 직접 1:N 허용) 또는 WARN 모드의 사유 override.
3. **활성 조건**: 판정 기준 결제유형이 **`TT_ADVANCE`** 일 때만. 그 외(`TT_DEFERRED`·`LC`)는 `pi_gate_verdict='NOT_APPLICABLE'`(모드가 BLOCK이어도 통과 — **양방향 테스트 필수**). `payment_type NULL`은 A의 `frozen_complete`가 확정 전에 거부하므로 게이트에 도달하지 않는다(방어: 도달하면 예외 — 결제유형 부재를 비활성으로 취급하지 않는다).
4. **모드** — 정책 키 `pi_advance_gate_mode ∈ OFF|WARN|BLOCK`(E8), **미설정 = BLOCK(fail-closed)**, 응답·증적에 `mode_source ∈ SET|UNSET_DEFAULT`를 싣는다:
   - **OFF**: 평가·차단 생략, `pi_gate_verdict='SKIPPED_OFF'`로 **스킵 사실을 SO에 기록**(평가 불능·생략이 조용히 통과가 되지 않게 — 제안 1·3의 "기록 없이 통과" 기각).
   - **WARN**: 미충족(아래 비통과 3종)이면 422 `TRADE_DOCS.GATE.PI_ADVANCE_BLOCKED`(`overridable=true`). 확정 body에 `pi_gate_override_reason`(공백 제외 5~500자)이 있으면 통과, `pi_gate_verdict='WARN_OVERRIDDEN'`+`pi_gate_override_reason` 기록+audit 1건+outbox 이벤트 `trade_docs.sales_order.gate_overridden`(ADMIN이 alert_rules 데이터로 알림 규칙을 걸 수 있다 — 코드 알림 신설 없음).
   - **BLOCK**: 같은 코드, `overridable=false`. **사유를 보내도 통과하지 않는다**(ADMIN 포함) — 사유는 무시되고 거부 그대로.
5. **비통과 3종(`reason`)**: `SHORT`(순입금<required) / `PI_MISSING`(판정 기준이 SO 자신인데 `TT_ADVANCE` — PI 없는 선수금 SO. 평가 불능을 통과로 취급하지 않는다: BLOCK이면 확정 불가, WARN이면 사유 override로 확정 가능(snapshot `pi_id=null`), OFF면 SKIPPED_OFF) / `PI_NOT_USABLE`(참조 PI가 ISSUED·PARTIALLY_PAID·PAID가 아님 — B3상 살아 있는 SO가 있으면 취소·만료가 막혀 도달 불가한 방어 분기, fail-closed).
6. **평가 예외**(집계 실패 등)는 통과 금지 — 예외 전파(롤백)+500이 아니라 `TRADE_DOCS.GATE.PI_ADVANCE_BLOCKED reason=SHORT`로 위장하지 않는다(그대로 500 — 데이터 결함은 fail-visible).
7. **직렬화**: PI 행은 E4 순서 ③에서 잠기므로 입금·역기록(E7)과 확정이 직렬화된다. 게이트는 `(SO, PI, mode)`를 입력으로 하는 순수 함수 `payments/pi_gate.py::evaluate_pi_advance(...) -> PiAdvanceEvaluation(applicable, basis, reason|None, required_amount, received_amount, currency, mode, mode_source, verdict, overridable)`.

**(d) 4금** — 없음(게이트=사람 확정 전 검사, 결과는 표시·차단). 모드를 OFF/WARN으로 바꾸는 것은 ADMIN의 정책 결정(감사·사유 필수 — E8)이며 SO에 `mode`·`mode_source` 스냅샷이 남아 사후 추적된다.

**(e) 상태·불변** — 게이트 판정은 확정 시점 1회(재평가 없음 — B1 재개 규칙). 증적은 E4의 SO 5열.

**(f) 테스트 배분**
- **A(서비스 전수 — WBS 검증 "A(PI 게이트)")**: ① 선수금 T/T+입금=required 통과·required−1 차단(BLOCK) ② **같은 조건을 LC·TT_DEFERRED로 → 통과(NOT_APPLICABLE), BLOCK 모드에서도(활성/비활성 양방향 자기검사)** ③ PARTIALLY_PAID 30% 입금 PI로 30% 조건 통과 / PAID 상태여도 금액이 required 미만이 될 수 있는 시나리오는 자동 수렴으로 불가함을 확인 ④ PI 없는 선수금 SO: BLOCK→PI_MISSING·WARN→사유로 통과+증적·OFF→SKIPPED_OFF ⑤ WARN 사유 4자 거부·5자 통과·BLOCK에서 사유 무시 ⑥ 모드 미설정→BLOCK+`mode_source=UNSET_DEFAULT`·정책 값 손상 → BLOCK ⑦ **HALF_UP 경계**(`split_advance`와 동일 값 — 청구서 금액과 게이트 요구액 일치 대조, bp 3333·홀수 금액·JPY 0자리·USD 2자리) ⑧ `required=0` 통과 ⑨ **우회 방지**: SO 결제유형을 `TT_ADVANCE`→`LC`로 편집한 뒤 확정(PI 참조) → 여전히 PI 기준 판정(`terms_diverge=true`)·PI 없는 SO는 SO 기준 ⑩ 순입금 = 입금 합−역기록(역기록 후 재확정 시도 → 다시 SHORT).
- **H/J**: WARN override가 벌크 호출에서 불가 / 입금 역기록과 확정 동시(PI 잠금 직렬화) — 결과가 직렬 순서 중 하나(확정 선행이면 확정 유지+E7 경고, 역기록 선행이면 확정 SHORT 차단).
- **K**: `evaluate_pi_advance`가 `pi.status`를 읽지 않음(소스 스캔) / 응답에 원가 필드 부재.
- **변이 점검 대상**: `>=`→`>`, `TT_ADVANCE` 조건 제거(항상 활성), 순입금에서 REVERSAL 부호 제거, PI 잠금 제거, 기본 모드를 WARN으로 변경, `basis` 분기를 항상 SO로, BLOCK에서 사유 수용, OFF에서 기록 생략, `HALF_UP`→`ceil`.

**(g) 소비·등재** — 소비: E4 확정 통로·D 보드 카드(미충족·모드 배지)·S3-3(입금 화면). **DESIGN §7.3 보강 필요**(금액 대조·PI 기준·모드·미설정 BLOCK·PI 없음 처리 명문). 관찰: 모드 OFF/WARN 완화는 ADMIN 통제 수용.

**(h) 되돌리기 비용 — 낮음.** 게이트 로직·모드 기본값·PI 없음 처리는 서비스 1곳+레지스트리 상수. SO 5열은 E4 소관.

---

## E7. 입금 원장 `payments` — S3-3이 그대로 쓰는 정본 · 입금·역기록 · PI 상태 수렴 · 권한 (제안 E4 통합)

**(a) 목적·경계** — 입금 **사실**의 정본 테이블을 S3-1이 처음부터 S3-3이 쓸 이름·부호 규약·불변 규율로 만들고, PI 선수금 입금과 그 정정(역기록), PI 상태 자동 수렴 호출을 정한다. 채권(receivables)·분개·은행 CSV 매칭·잔금 입금은 범위 밖(S3-3·Phase 7).

**(b) DESIGN 근거** §3 표 맵 `payments`(부분 허용), §7.3(입금 매칭 시 상태 자동 전환), §7.10, ADR-05(**역기록 = 원본 불변 + 반대 부호의 신규 기록** — 원장 공통), §17.5 확장 명문(불변 테이블 IMMUTABLE 등재), §2 날짜 3종·KST, ADR-0003 금액 규약, A5·A10, B1(`converge_payment_status`).

**(c) 데이터 변경 — 마이그레이션 E-2(신규 테이블 1개, additive, 시드 0)**

`payments`(PkMixin + 명시 컬럼; **Version·SoftDelete·Actor 믹스인 없음 — 원장형 INSERT-only**)

| 열 | 타입 | 비고 |
|---|---|---|
| `id` | BIGINT identity PK | |
| `partner_id` | BIGINT FK partners RESTRICT NOT NULL | PI의 바이어(서비스가 복사·일치 검증 — S3-3 거래처 단위 입금이 같은 열을 쓴다) |
| `pi_id` | BIGINT FK proforma_invoices RESTRICT **NOT NULL** | S3-1 서비스는 PI 입금만 만든다. S3-3이 채권 입금을 넣을 때 `DROP NOT NULL`+`receivable_id` 추가+"둘 중 정확히 하나" CHECK(가산·완화만, 기존 행 무영향 — 죽은 nullable 컬럼을 지금 만들지 않는다) |
| `kind` | VARCHAR(10) NOT NULL | CHECK IN ('RECEIPT','REVERSAL') — S3-3에서도 값이 늘지 않음 |
| `received_amount`, `received_currency` | BIGINT, CHAR(3) NOT NULL | `money_columns("received")`. **부호 있음**(RECEIPT>0, REVERSAL<0). 통화 대문자 CHECK |
| `received_on` | DATE NOT NULL | 증빙일(KST 실입금일 — A10 날짜 3종 중 증빙일). 미래 불가(서비스), 소급 허용. 입력일=`created_at`(UTC) |
| `reference` | VARCHAR(100) NOT NULL | 입금 확인 근거(은행 거래 참조·확인 메모). `btrim<>''`. **유니크 없음**(0-1 #13) |
| `reverses_payment_id` | BIGINT FK payments NULL | |
| `reason` | VARCHAR(300) NULL | 역기록 사유(필수 — CHECK) |
| `recorded_by_id` | BIGINT FK users RESTRICT **NOT NULL** | 시스템 행위자 없음(사람 입력만) — Actor 믹스인 대신 명시 |
| `created_at` | TIMESTAMPTZ NOT NULL server_default now() | |

CHECK: `payments_kind_sign`: `(kind='RECEIPT' AND received_amount>0 AND reverses_payment_id IS NULL AND reason IS NULL) OR (kind='REVERSAL' AND received_amount<0 AND reverses_payment_id IS NOT NULL AND char_length(btrim(reason))>=2)` / 통화 대문자 / `reference` 비공백. 제약·인덱스: `UNIQUE(id, pi_id, received_currency)` + **복합 FK** `(reverses_payment_id, pi_id, received_currency) → payments(id, pi_id, received_currency)`(역기록은 **같은 PI·같은 통화**의 원 입금만 — DB 강제; MATCH SIMPLE 공백은 CHECK가 REVERSAL 시 reverses NOT NULL을 요구해 보강), 부분 유니크 `uq_payments_reverses_payment_id (reverses_payment_id) WHERE reverses_payment_id IS NOT NULL`(**한 입금은 한 번만 역기록** — 삭제 없음이라 deleted_at 술어 불필요), 인덱스 `ix_payments_pi (pi_id, id)`. 역기록 금액 = −원금(전액만)·`partner_id`=PI 바이어는 **서비스 검증+통합 테스트**(교차 행 CHECK 불가). 제약명 63자 이내(구현 시 실측).

**불변**: `IMMUTABLE_TABLES += payments`+`revoke_mutations`(앱 계정 INSERT·SELECT만 — 마이그레이션 수기 호출, `table_policy` 분류 테스트 갱신). **정정 = 역기록 후 재입금**(부분 정정도 동일). `_NEVER_SEEDED += payments`.

**서비스(모듈 `app/modules/payments/`)**
- `record_receipt(actor, idempotency_key, pi_id, body{received_amount(문자열 12.34), received_currency, received_on, reference})`: claim → `lock_chain(PI)`(거래처→QT→PI, E3) → 검증 순서: ① PI 상태가 ISSUED·PARTIALLY_PAID·PAID(그 외 EXPIRED·CANCELLED → **B의 409 `TRADE_DOCS.PAYMENT.PI_NOT_OPEN`**) ② **PI 결제유형이 `TT_ADVANCE`**(아니면 422 `PAYMENTS.PAYMENT.PI_NOT_ADVANCE` — S3-1의 입금은 선수금 전용, 잔금 입금은 S3-3 채권 몫) ③ `received_currency == PI.currency`(422 `PAYMENTS.PAYMENT.CURRENCY_MISMATCH` — **환산 없음**, 요청에 통화를 명시하게 해 오입력을 잡는다) ④ 금액 파싱(통화 자릿수 초과 422, 양수) ⑤ `received_on ≤ 오늘(KST)` ⑥ **순입금+금액 ≤ `due`(= `split_advance` 선수금)** 아니면 422 `PAYMENTS.PAYMENT.EXCEEDS_DUE`(선수금 청구액을 넘는 입금은 잔금이며 S3-3 채권 입금으로 기록 — 이중 계상 방지) → INSERT → `converge_payment_status(session, pi_id, received_total_amount=순입금, actor)`(B — PI ISSUED/PARTIALLY_PAID/PAID 6방향 수렴, 상태 이력에 `automatic` 표식·사유 "입금 #id 기록") → `audit.record(action='payments.receipt.recorded', entity_type='payments', detail={pi_id, amount, currency, received_on, net_after})`(참조 텍스트는 audit·로그에 넣지 않음) → outbox `payments.payment.recorded`(id만) → `complete(201)`.
- `reverse_payment(actor, idempotency_key, payment_id, body{reason})`: 무잠금으로 원 입금→PI id → `lock_chain(PI)` → 원 입금이 RECEIPT이고 미역기록(경합은 유니크가 잡아 409 `PAYMENTS.PAYMENT.ALREADY_REVERSED`)·REVERSAL 행 역기록은 409 `PAYMENTS.PAYMENT.NOT_REVERSIBLE` → INSERT(−원금, 같은 PI·통화·partner) → `converge_payment_status`(역방향 수렴) → audit·outbox. **확정된 SO가 이 PI를 근거로 이미 있고 역기록 결과 순입금이 `required` 미만이 되면** 응답에 `warnings:[{code:'CONFIRMED_SO_ADVANCE_UNMET', sales_order_ids:[…]}]`를 싣고 outbox `payments.payment.reversed_after_confirm`(ID만) 발행 — **역기록 자체는 막지 않는다(현금 사실 정정은 막지 않음)·확정 SO를 자동 취소하지 않는다(4금)**. 사람이 후속 조치(취소·재확인)를 한다.
- `net_received_for_pi(session, pi_id) -> int`: 유일한 순입금 정의(`SELECT COALESCE(SUM(received_amount),0)`). PI 상태·다른 집계로 대체 금지(스캔).
- API(전부 `extra=forbid`, 쓰기는 `Idempotency-Key` 필수): `POST /api/v1/proforma-invoices/{pi_id}/payments`, `POST /api/v1/payments/{payment_id}/reversal`, `GET /api/v1/proforma-invoices/{pi_id}/payments`(페이지네이션 기본 50, 순입금·due·PI 상태 요약 동봉).
- **권한**: 기록·역기록 = TRADE+ADMIN(서버 `require_roles`), 열람 = 전 역할(금액은 원가·마진이 아님·마스킹 비대상). LOGISTICS·CERT·VIEWER의 쓰기는 403. 소유권은 역할 기준(B의 전표 관례 — 개인 필터 없음).
- 오류 매핑(D8): 유니크·복합 FK·CHECK 위반은 서비스 선검증이 1차, DB가 최후 — `uq_payments_reverses_payment_id` 23505 → 409 ALREADY_REVERSED, 그 외 IntegrityError는 A13의 `map_integrity_error()` 제약명 사전에 등재(사전 완결성 스캔이 신규 제약 누락 시 실패).

**(d) 4금** — 없음. 입금 기록은 사람이 은행 확인 후 입력하는 운영 사실이며 **회계 기표(분개)가 아니다**(§1 비범위 — 입금 분개 초안은 S3-3 이후 별도). PI 상태 자동 전환은 사람이 입력한 사실의 파생이다(§7.3 문면). 대외 발송 없음.

**(e) 상태·전이·불변** — `payments`는 상태 없음·INSERT-only. PI 6방향 자동 수렴은 B1 전이표(자동 10 중 6)에 이미 있다. 불변식: 순입금 ≥ 0(역기록이 원금을 넘지 못함 — 복합 FK+전액 역기록), 순입금 ≤ due(서비스).

**(f) 테스트 배분**
- **A(서비스 전수)**: 일부 입금→PARTIALLY_PAID·완납→PAID·역기록→역방향 왕복(ISSUED→PARTIALLY_PAID→PAID→PARTIALLY_PAID→ISSUED, 상태 이력에 automatic·사유) / 통화 불일치 422 / 초과 입금 422(due 경계 ±1) / 미래 날짜 422·소급 허용(KST 00:30 경계) / 자릿수 초과 422 / EXPIRED·CANCELLED PI 입금 409 / `TT_DEFERRED`·`LC` PI 입금 422 / 역기록: 반대 부호 신규 행·원본 불변·재역기록 409·REVERSAL 재역기록 409·순입금 복원·PI 상태 복원 / **확정 SO 존재 시 역기록 → 경고 응답+outbox·SO 불변** / partner_id 일치.
- **K(보안·품질·제약)**: `payments`가 IMMUTABLE로 분류되고 **`kbos_app`의 UPDATE·DELETE·TRUNCATE가 SQLSTATE 42501로 거부**(실권한 — table_policy 분류 테스트+실측) / CHECK 위반 원시 INSERT(부호 반전·REVERSAL에 사유 없음·다른 PI로의 역기록·통화 다른 역기록) 거부 / LOGISTICS·CERT·VIEWER 쓰기 403·열람 200 / 목록 페이지네이션 스캔 / 금액 Float 부재·유니크 키에 금액 컬럼 부재(기존 스캔 자동 편입).
- **J**: 입금 더블클릭(같은 키) → 1건 / 같은 PI에 두 입금 동시(합계가 due 초과) → 1건만 성공(PI 잠금) / **확정 vs 역기록 동시**(E6 시나리오) / 입금 수렴 중 실패 → 완전 롤백(원장 행·PI 상태 함께).
- **변이 점검 대상**: 순입금에서 REVERSAL 부호 제거·kind별 분기 합산, PI 잠금 제거, `EXCEEDS_DUE` 비교 제거, 복합 FK 제거, `revoke_mutations` 제거, `converge_payment_status` 호출 제거, 통화 비교 제거.
- vitest(프런트 `PiPaymentsPanel`): 순입금·due·PI 상태 표시, 입금 폼(통화 표시·근거 필수), 역기록(사유 필수) 확인 대화상자, 확정 SO 경고 배너, 403 시 폼 숨김.

**(g) 소비·등재** — 소비: E6 게이트·B `converge_payment_status`(호출자 확정 — B의 "S3-3 소비 계약"을 S3-1이 선점 소비)·S3-3(nullable 완화·`receivable_id`·입금 분개 초안). **WBS v1.5 수정 요청**: S3-3 산출물의 "payments(부분)"를 "**S3-1 신설 `payments` 확장(채권 연결·잔금 입금)**"으로. **DESIGN §3 표 맵**(payments 신설 세션 S3-1 표기)·**§7.10**(선수금 입금=S3-1·채권 입금=S3-3 분담) 보강. 부채: 이중 입력 탐지(은행 참조 유니크 불가 사유) → S3-3/P7 은행 CSV 매칭 / 통화 불일치 입금 불가(환율 원천 도입 시 재판정) / 선수금 초과분 입금 불가(S3-3 채권 입금으로) / 확정 SO 사후 경고의 후속 조치는 사람.

**(h) 되돌리기 비용 — 중간~높음.** 테이블명·부호 규약·불변 등록·`pi_id NOT NULL`은 S3-3이 소비하므로 **배포 뒤 변경은 데이터 이관**(그래서 지금 확정). 입금 권한 축소(TRADE→ADMIN)·`EXCEEDS_DUE` 완화는 서비스 1곳(낮음).

---

## E8. 정책 설정 `policy_settings` — 저장 위치·키·미설정 동작·변경 통제·관리 화면 (제안 E5 통합)

**(a) 목적·경계** — ADR-11("설정화 가능분=데이터, 관리 화면")이 요구하는 값 있는 정책의 저장소·API·화면을 세운다. feature_flags(bool 전용)와 alert_rules.config(JSONB 알림 문턱)와 섞지 않는다.

**(b) DESIGN 근거** ADR-11, §7.3("경고/차단 설정"), §7.4("단가 편차(±허용치)"), §15 폐쇄 레지스트리 선례, §2 관리자 통제, ADR-0041(소비 없는 열거 금지), 함정 ⑩(ActorMixin users FK 테이블은 마이그레이션 시드 불가).

**(c) 결정 — 마이그레이션 E-1(신규 테이블 1개, additive, 시드 0)**

`policy_settings`(Pk+Timestamp+SoftDelete+Version+Actor 믹스인 — ADR-02 관용 준수)

| 열 | 타입 | 규칙 |
|---|---|---|
| `policy_key` | VARCHAR(60) NOT NULL | **CHECK IN ('pi_advance_gate_mode','price_deviation_tolerance_bp')** — 폐쇄 열거(키는 코드 동작과 1:1이라 행 추가만으로 새 동작이 생기지 않는다 — §15 선례). 새 키 = 소비 코드+CHECK 재정의 마이그레이션+ADR 부기(의도된 마찰, C1 유형 추가 절차와 같은 규약) |
| `value_text` | VARCHAR(20) NULL | |
| `value_int` | INTEGER NULL | |
| 부분 유니크 | `unique_active('policy_settings','policy_key')` | `WHERE deleted_at IS NULL` |

값 형 CHECK: `(policy_key='pi_advance_gate_mode' AND value_text IN ('OFF','WARN','BLOCK') AND value_int IS NULL) OR (policy_key='price_deviation_tolerance_bp' AND value_int BETWEEN 0 AND 10000 AND value_text IS NULL)`. 키 2개는 **둘 다 소비 코드가 S3-1 안에 있다**(E6 게이트, D의 단가 편차 게이트 — 허용치 저장을 D가 다른 곳에 두기로 하면 통합 검토에서 이 키를 제거하고 CHECK를 1키로 줄인다: 가정 명시).

**코드 레지스트리** `policies/registry.py::POLICY_REGISTRY: dict[str, PolicySpec(kind, allowed/range, unset_behavior, label_ko, description_ko)]` — 키집합이 DB CHECK와 1:1(아키텍처 테스트가 `pg_get_constraintdef` 파싱으로 대사 — C1 관용). 여신은 **항상 켜진 승인 게이트**(끔 스위치 키 부재 — §2 승인 통제 우회 통로 금지; 아키텍처 테스트가 `credit*enabled|off|skip` 류 키 등재를 금지). MOQ·중복 PO·품번 매핑·준비도 게이트는 DESIGN에 설정 문면이 없고 MOQ 출처가 미정이라 키를 만들지 않는다(D가 게이트별 고정 동작을 정하고, 필요해지면 위 추가 절차로).

**미설정 동작(fail-closed·fail-visible)**: 행이 없으면 `pi_advance_gate_mode=BLOCK`(가장 엄격), `price_deviation_tolerance_bp=0`(어떤 편차도 통과하지 않음 — 처리 강도는 D)로 동작한다. `get_policy(session, key) -> ResolvedPolicy(value, source: 'SET'|'UNSET_DEFAULT')`가 **유일 조회 통로**이며 게이트 응답·증적 스냅샷·관리 화면이 `source`를 싣는다("정책 미설정 — 기본 동작(차단)으로 처리 중"). **조용한 기본값 금지.** 값 손상(CHECK 우회·수기 DB 수정)은 미설정과 동일 취급+경고 로그+`source='UNSET_DEFAULT'`(CHECK가 있어 정상 경로에서는 발생 불가한 방어 분기). 캐시하지 않고 게이트 평가 트랜잭션 안에서 읽는다(정책 변경×확정은 READ COMMITTED에서 변경 전/후 하나로 수렴하며 SO에 사용 값 스냅샷이 남는다).

**초기 행 공급**: **마이그레이션 시드 금지·CLI 시드 없음**(함정 ⑩·`PRESERVED_TABLES` 미등재·`_NEVER_SEEDED += policy_settings`). 관리자가 화면에서 저장할 때 upsert(없으면 `INSERT ... ON CONFLICT DO NOTHING`, 충돌이면 409 `VERSION_CONFLICT`로 새로고침 안내 — 동시 최초 생성 500 금지; 있으면 `version` 확인 UPDATE). 수치 근거 없는 권장값(예: 허용치 5%)을 시스템이 박아 넣지 않는다 — 오너가 저장할 때까지 UNSET 기본 동작.

**API(ADMIN 전용 — 서버측 역할 검증, `extra=forbid`)**: `GET /api/v1/policies`(레지스트리 전 키를 행으로 — 현재값·`source`·라벨·설명·`version`·수정자·시각[KST 표시]; 2건 고정 목록이지만 규약상 페이지 응답 형태 유지), `PUT /api/v1/policies/{policy_key}`(body `{value, version|null, reason(2~300자, 필수)}` + `Idempotency-Key`). 알 수 없는 키 404 `POLICIES.POLICY.UNKNOWN_KEY`, 값 위반 422 `POLICIES.POLICY.INVALID_VALUE`, version 충돌 409(기존 `COMMON.CONCURRENCY.VERSION_CONFLICT`). **모든 변경은 같은 트랜잭션에서 `audit.record(action='policy.update', entity_type='policy_settings', detail={key, old, new, old_source, reason})`+outbox `policies.policy.changed`**(ADMIN이 alert_rules로 알림 규칙 가능). 삭제·"기본값으로 되돌리기" API는 만들지 않는다(기본값과 같은 값을 명시 저장하면 되고 이력이 명료). 게이트 응답은 모든 역할에 **실효 모드·출처만** 읽기로 노출(설정 편집권과 분리).

**화면**: `/settings/policies`(ADMIN 전용 — 사이드메뉴 ADMIN 항목·서버가 재검증). 표: 라벨·설명·현재값·출처 배지(설정됨/**미설정 — 기본 동작(차단)**, 적색+글자)·마지막 변경자·시각. 행 편집: 모드=셀렉트, 허용치=% 입력→bp 변환 표시(상한 문구를 레지스트리 값과 1:1 테스트)+**변경 사유 입력 필수**. 한국어 UI 규약 준수(break-keep·헤더 nowrap·숫자 가운데). 기존 `alerts.tsx`의 관리자 패널 패턴 재사용. D9 4요소(API·화면·권한·테스트) 충족.

**(d) 4금** — 없음. 정책 완화(BLOCK→WARN/OFF)는 ADMIN의 통제 결정이며 감사·사유·스냅샷으로 추적된다(자동 변경 경로 0).

**(e) 상태·불변** — 값은 낙관 잠금(version)으로만 변경, 변경 이력은 audit_log(IMMUTABLE)가 정본. `MUTABLE_TABLES += policy_settings`(사유 주석).

**(f) 테스트 배분**
- **A/H**: 행 없음 → BLOCK·`UNSET_DEFAULT`/저장 후 `SET`·다음 확정 평가에 즉시 반영·SO 스냅샷에 mode·source / CHECK 위반(모드 'ALLOW'·허용치 10001·음수·값 형 교차) 원시 INSERT 거부 / 알 수 없는 키 404 / ADMIN PUT 200+audit 1행(old·new·reason)+outbox 1건 / TRADE·LOGISTICS·CERT·VIEWER PUT·GET 403.
- **J**: version 충돌 409 / 같은 Idempotency-Key 재요청 동일 결과 / **동시 최초 생성 두 요청 → 하나 성공·하나 409(500 아님)**.
- **K**: `POLICY_REGISTRY` 키집합 == CHECK 키집합(불일치 실패·공회전 방지 len≥1) / 마이그레이션에 `policy_settings` INSERT 부재 스캔·`_NEVER_SEEDED` 등재 / `credit` 끔 스위치 키 등재 금지 / `get_policy` 리터럴 키가 전부 레지스트리에 있음(스캔).
- vitest: 미설정 적색 배지·%↔bp 변환·상한 문구·사유 필수·저장 후 재조회·비관리자 화면 숨김.
- **변이 점검 대상**: 기본값 BLOCK→WARN, 미설정을 통과로 폴백, ADMIN 검증 제거, audit 제거, CHECK 제거, 사유 필수 제거.

**(g) 소비·등재** — 소비: E6(`pi_advance_gate_mode`), D(`price_deviation_tolerance_bp`). ADR 필요(E11): "값=데이터, 키·타입·기본값=코드, 폐쇄 키 CHECK, 시드 없음, feature_flags와 혼용 금지(bool 전용 유지)". 프런트 사이드메뉴 항목 추가.

**(h) 되돌리기 비용 — 낮음.** 독립 신설 테이블·행 2개 수준. 키 추가·값 범위 변경은 CHECK 마이그레이션 1건, 기본값 변경은 레지스트리 상수 1곳.

---

## E9. 여신한도 변경 통제 — ADMIN 전용 (제안 X1 통합)

**(a) 목적·경계** — 게이트 도입과 동시에 생기는 가장 값싼 우회(무역 담당이 한도를 올린 뒤 확정)를 닫는다. **열람과 별개 축**(E2는 열람 마스킹 결정).

**(b) DESIGN 근거** §2 승인 통제(여신 초과 수주)·§20 H "승인 우회 차단", §4.6 여신한도, ADR-0026.

**(c) 결정 — 한도 값이 바뀌는 쓰기 경로 2곳에서 ADMIN 요구 (0-1 #5: 실제 경로 특정)**
1. **`create_partner`**(`partners/service.py:288-` — 등록 시 초기 한도): 요청에 `credit_limit`·`credit_limit_currency`가 값으로 실려 있고 행위자가 ADMIN이 아니면 403 `PARTNERS.CREDIT_LIMIT.ADMIN_ONLY`. 한도 없이(빈 값) 등록은 종전대로 TRADE 가능(등록 후 ADMIN이 임포트로 설정).
2. **임포트 확정 `confirm_staging`**(`imports/service.py:543-`, 대상 `partners`): `ImportTarget`에 선택 속성 `admin_only_fields`(partners = `{credit_limit_amount, credit_limit_currency}`)를 도입하고, 확정 행위자가 ADMIN이 아니면서 (a) `CHANGED` 행의 `changed_fields`가 이 집합과 교집합이 있거나 (b) `NEW` 행 payload에 해당 필드가 non-None이면 **배치 전체를 거부**(부분 반영 없음 — 기존 충돌 전수 검사 원칙)하고 403 `PARTNERS.CREDIT_LIMIT.ADMIN_ONLY`+행 번호 목록(사용자용 detail). 판정 시점은 **확정 시점 행위자**(스테이징은 TRADE가 올리고 ADMIN이 확정해도 통과 — 승인 유사 분리 허용). **"변경"의 정의**: 값이 실제로 달라지는 모든 전이(값↑·값↓·통화 변경·NULL→값·값→NULL='관리 해제') — 값이 불변인 재전송은 제외(기존 CSV 왕복 흐름 보존, `changed_fields`가 이미 그렇게 계산됨).
3. 변경은 `audit.record(action='partners.credit_limit.changed', detail={partner_id, old_amount, old_currency, new_amount, new_currency})` 필수(등록 시 초기 설정도 동일 action `partners.credit_limit.set`). 임포트 경로의 기존 audit이 있으면 그 detail에 4필드를 포함.
4. 확정 시점 평가는 잠금 하 최신 한도를 읽는다(E3 `populate_existing`). 임포트의 partners `FOR UPDATE`와 확정의 `FOR NO KEY UPDATE`가 충돌하므로 **한도 변경×확정은 직렬화**.
5. **프런트**: 거래처 등록 폼(`partners.tsx`)의 여신 입력 2칸을 비관리자에게 비활성+"여신 한도는 관리자만 설정할 수 있습니다" 안내(서버가 정본, 화면은 편의).
6. 신규 권한 개념 없음(기존 ADMIN만). 무역 담당의 한도 조정 요청은 관리자 대행(운영 마찰은 오너 판단으로 후속 완화 — 승인 유형화는 C·후속 세션 몫).

**(d) 4금** — 없음. **(e)** 스키마 변경 없음.

**(f) 테스트** — H/K: 무역이 한도 포함 등록 → 403(한도 없이 등록은 201) / ADMIN 한도 포함 등록 201+audit / 임포트: 무역 확정 시 한도 컬럼 CHANGED·NEW → 배치 거부·DB 불변(부분 반영 0) / 값 불변 재전송 임포트 → 무역도 통과 / NULL→값·값→NULL·통화만 변경도 변경으로 취급 / ADMIN 확정 성공+audit / 무역이 올린 스테이징을 ADMIN이 확정 성공 / **통합 시나리오**: 한도 상향 후 승인 없이 확정 통과는 ADMIN 변경에서만 가능 / K: `ImportTarget.admin_only_fields`가 partners 레지스트리에 설정됨(공회전 방지)·기존 partners 테스트 중 TRADE가 한도를 바꾸는 케이스가 있으면 ADMIN으로 갱신(구현 착수 시 `grep`, PR 본문에 "기존 동작 변경" 명시). 변이: 검사 제거·행위자 역할 판정을 스테이징 생성자로 변경 → 실패.

**(g) 소비·등재** — 임포트 레지스트리 계약 확장(다른 임포트 대상이 admin-only 필드를 갖게 될 때 같은 속성 사용). 부채: 거래처 수정 화면 부재(CSV만 — A 부채와 동일)·한도 조정 요청 워크플로 없음. **DESIGN §4.6 보강 필요**(여신한도 등록·변경 = 관리자).

**(h) 되돌리기 비용 — 낮음.** 가드 함수 1곳+속성 1개. 운영 마찰(관리자 의존)은 오너 판단으로 완화 가능.

---

## E10. 확정·입금·정책 API 계약 · 멱등 · 프런트 규약 (제안 X3·E3 통합)

**(a) 목적·경계** — E가 제공하는 쓰기 API의 공통 계약(멱등 키·지문·재시도·화면 반응)과 프런트 산출물을 고정한다.

**(b) DESIGN 근거** §17.4(쓰기 API 멱등 키), §18.4(에러 이원화·표준 컴포넌트·페이지네이션), S1-3 PR-3 프런트 결함(확정 키 재생성), `lib/api.ts`(자동 키 부착).

**(c) 결정**
1. **확정 API** `POST /api/v1/sales-orders/{id}/confirm`(B 소유 엔드포인트 — E 구간 계약): body `{version:int, pi_gate_override_reason?:str(5..500)}`(`extra=forbid`), 권한 TRADE+ADMIN, `Idempotency-Key` 필수(없으면 400 기존 `IDEMPOTENCY_KEY_REQUIRED`). **응답 200만 성공**(거부는 전부 4xx 예외 — 202 판별 유니온을 만들지 않는다: 성공 토스트 오표시 위험 제거). 200 본문: SO 요약+`gates:{credit:{verdict, limit_amount, limit_currency, exposure_after_amount, excess_amount, receivables_reflected:false, exposure_is_partial:true}, pi_advance:{verdict, mode, mode_source, required_amount, received_amount}}`(전부 마스킹 비대상 필드, 원가 계열 없음)+B의 `allocation`.
2. **지문**: `claim(request_body={so_id, version, pi_gate_override_reason})` — 같은 키+다른 지문은 **기존 `IDEMPOTENCY_KEY_CONFLICT` 409**(0-1 #2).
3. **재시도 의미**: 네트워크 재시도=같은 키·같은 본문(성공했다면 최초 200 재생). **거부(4xx) 뒤 재시도는 새 판정을 받는다**(E4 — 거부는 기록 안 함).
4. **프런트 키 수명**: 확정 패널은 **(SO id, version, override 사유) 조합**이 바뀔 때만 키를 새로 만들고 그 조합 안에서는 재클릭·재시도에 같은 키를 쓴다(A13 5번 "화면 진입 시 1회"를 정확화 — 본문이 바뀌었는데 키를 재사용하면 409 KEY_CONFLICT). 요청 중 버튼 비활성, 응답이 200이 아니면 성공 토스트 금지, `422 APPROVALS.APPROVAL.REQUIRED`는 '승인 요청' 버튼 노출, `422 PI_ADVANCE_BLOCKED`는 `overridable`에 따라 사유 입력란(WARN) 또는 안내(BLOCK), `409 COMMON.CONCURRENCY.LOCK_BUSY`는 "다시 시도" 버튼(같은 키).
5. **프런트 산출물(E 소유)**: ① `CreditEvaluationCard`(한도·노출·초과분·'미수 미반영' 배지·환율 사용 표시 — SO 상세·승인 패널·보드가 공유, 원가 필드 없음) ② `ConfirmPanel` 반응 규칙(위 4) ③ `PiPaymentsPanel`(E7) ④ `/settings/policies`(E8) ⑤ 거래처 등록 폼 여신 입력 비활성(E9). 모든 금액 표기는 서버 문자열(프런트 산술 0 — 검증 #9 가드), 한국어 규약(break-keep·nowrap 헤더·숫자 가운데).
6. **에러 코드 등재 요약(3세그먼트, catalog 한국어 문구 10자 이상+조치 힌트)**: `TRADE_DOCS.GATE.PI_ADVANCE_BLOCKED`(422)·`TRADE_DOCS.GATE.CREDIT_UNEVALUABLE`(422)·`PAYMENTS.PAYMENT.CURRENCY_MISMATCH`(422)·`PAYMENTS.PAYMENT.EXCEEDS_DUE`(422)·`PAYMENTS.PAYMENT.PI_NOT_ADVANCE`(422)·`PAYMENTS.PAYMENT.ALREADY_REVERSED`(409)·`PAYMENTS.PAYMENT.NOT_REVERSIBLE`(409)·`POLICIES.POLICY.UNKNOWN_KEY`(404)·`POLICIES.POLICY.INVALID_VALUE`(422)·`PARTNERS.CREDIT_LIMIT.ADMIN_ONLY`(403) + 공용 `COMMON.CONCURRENCY.LOCK_BUSY`(409, A13). C 소유 `APPROVALS.APPROVAL.REQUIRED/STALE`·B 소유 `TRADE_DOCS.PAYMENT.PI_NOT_OPEN`·`TRADE_DOCS.TRANSITION.NOT_ALLOWED` 재사용. 응답 detail의 금액은 사용자용이며 **로그 컨텍스트에는 금액·거래처명·참조 텍스트를 싣지 않는다**(`log_context`와 `detail` 분리 — D13, 로그 캡처 단언 테스트).

**(d) 4금** — 없음. **(e)** 없음.

**(f) 테스트** — J/A: 같은 키 다른 SO·다른 version → 409 KEY_CONFLICT / 거부 뒤 같은 키 재확정이 새 판정 받음(승인 후 200) / 성공 후 같은 키 재전송 → 최초 200 재생·승인 소비 1회 / e2e 대표 쌍: 한도 내 확정 200·초과 422 승인 요청→(타 사용자 승인)→재확정 200(GC 후보 관통) / 프런트 vitest: 키 재발급 조건·요청 중 비활성·200 외 성공 토스트 없음·422/409 분기 버튼·LOCK_BUSY 재시도(같은 키)·응답 금액 산술 0. K: 신규 쓰기 스키마 `extra=forbid` 전수·목록 API 페이지네이션 스캔·신규 에러 코드가 catalog 등재(열거 완전성). 변이: 지문 비교 제거·거부를 `complete` 기록·본문 변경 시 키 재사용 → 실패.

**(g) 소비·등재** — 소비: B 확정 엔드포인트가 E 구간을 호출·D 보드 벌크. **(h) 되돌리기 비용 — 낮음~중간**(200/예외 계약은 프런트가 즉시 의존).

---

## E11. 문서·ADR·WBS·후속 세션 인계·PR 구성 (제안 X2 통합)

**(a) 목적·경계** — 설계 변경=DESIGN 갱신+ADR 5줄 세트(CLAUDE.md), E의 임시·로컬 결정이 후속 세션에서 조용히 굳지 않게 소유 세션과 검증 테스트를 인계한다.

**(c) 결정**

**DESIGN 보강 필요(문면)**: **§7.3**(게이트 판정=금액 대조·`split_advance`·PI 참조 SO는 PI 조건 기준·모드 3값과 미설정=BLOCK·PI 없는 선수금 SO 처리·OFF 스킵 기록) / **§7.10**(여신 노출 산식·통화 규약[A7 `comparable_amount`]·NULL=관리 안 함·0=신용불가·strict·미수 미반영 배지·선수금 미차감·UNEVALUABLE=승인 불가 확정 거부·`payments` 신설=S3-1[선수금]/S3-3[채권] 분담) / **§3 표 맵**(payments 신설 세션·`policy_settings` 추가) / **§4.6**(여신한도 등록·변경=관리자) / **§17.2**(여신 체크=거래처 행 `FOR NO KEY UPDATE` S3-1 선행 결정·전역 잠금 순서·409 LOCK_BUSY) / **§17.5**(`payments` IMMUTABLE 확장 — ADR-0040 서식 재사용) / **§2**(정책 관리자 통제).

**ADR 신설(번호는 통합 검토가 0051~ 배정)**: ① **여신·입금 게이트 계약** — 산식·통화·미수 provider·직렬화(행 잠금 `NO KEY UPDATE`)·전역 잠금 순서·409 계약(§17.2 P4 ADR을 S4-1이 대체 가능하며 대체 시 순서표 승계)·확정 증적 5열·거부는 멱등 미기록 ② **`payments` 입금 원장 정본** — 이름·부호 있는 금액·IMMUTABLE·`pi_id NOT NULL`→S3-3 완화·선수금 전용·역기록 규칙 ③ **정책 저장소** — ADR-11 해석(값=데이터·키/타입/기본값=코드)·폐쇄 키 CHECK·시드 없음·feature_flags 혼용 금지. **부기**: ADR-0026 ②(E2 문구)·ADR-0018 ㉠ 영향 없음. 각 ADR 5줄(맥락·결정·근거·기각한 대안·되돌리기 비용).

**WBS v1.5 수정 요청**: S3-1 산출물에 `payments`(선수금 입금 최소형)·`policy_settings` 명시 / S3-3 산출물의 payments를 "S3-1 신설 테이블 확장(채권 연결)"으로 / S3-2·S3-3 인계 조건(아래).

**GC v1.4 신설 후보(등재 요청)**: 여신 초과 승인 게이트(양방향+동시 경합) / 승인 우회 차단(ADMIN 포함·승인 후 통과) / PI 게이트 선수금 T/T 전용 활성·비활성 양방향 / 확정 후 입금 역기록 경고(자동 취소 없음).

**후속 세션 인계 계약(PROGRESS 부채·각 세션 계획서 DoD에 편입)**
1. **S3-2**: `open_order_amount(so)`(E1)의 선적분 차감은 **S3-3 미수 provider `reflected=True` 등록 릴리스와 같은 PR에서만** 도입(노출 공백 방지). 부분선적 잔량 테스트에 여신 노출 케이스 1건 포함. `IN_SHIPMENT`→`COMPLETED` 전이 도입 시 COMPLETED 제외 정의의 간극(선적 후~채권 발생 전)을 '미수 미반영' 배지가 가시화함을 확인.
2. **S3-3**: `register_receivable_provider()` 등록(기본 구현 잔존 금지 아키텍처 테스트·DoD에 `receivables_reflected=true`와 "선적 확정~미수 발생 노출 공백·이중 계산 0" 테스트), `payments` 확장은 `pi_id` `DROP NOT NULL`+`receivable_id` 추가+"둘 중 정확히 하나" CHECK만(기존 컬럼·CHECK·kind 값 변경 금지), 선수금 미차감 재판정, 통화 불일치 입금(환율 원천) 재판정, 잔금·선수금 초과분 입금 경로, 은행 CSV 이중 입력 탐지(P7).
3. **S4-1**: P4 ADR이 `lock_buyer_for_credit` 교체 여부와 잠금 순서표 승계를 결정(체크리스트 등재).
4. **S5(자동 경로)**: `confirm_sales_order`를 호출해도 게이트를 우회하는 자동 확정 경로를 만들 수 없다(K 스캔이 S5에도 적용 — 4금).
5. **A**: `sales_orders` 5열 수용 위치·`buyer_partner_id`·`confirmed_at`·`fx_rate`·`gate_input_digest`, PI 1:1·`split_advance`. **B**: 열 분류 SYSTEM 5열, `lock_chain`에 payments 진입 허용, B의 "여신 마스킹" 문구 정정. **C**: 잠금 순서 문구 수정(E3)·`TargetSpec` 소비 규약·에러 detail 탑재. **D**: `confirm_gate_snapshot` 공용·벌크 결과 표(LOCK_BUSY 재시도 가능·WARN 사유 필요 행)·`price_deviation_tolerance_bp` 소비·게이트 순서(D 게이트 → E 게이트). **F**: 역할 매트릭스(입금 기록·역기록=TRADE+ADMIN, 정책=ADMIN, 한도 변경=ADMIN)·ADR 번호·PROGRESS 부채·마스킹 원장 #2.

**PR 구성 권고(작은 단위·즉시 테스트 — CLAUDE.md)**: ① PR-E1: `policy_settings`+레지스트리·API·화면+E9(`ADMIN_ONLY`)+LOCK_BUSY 핸들러 소비 확인(A13 PR 선행) ② PR-E2: `payments`+입금/역기록 API+`PiPaymentsPanel`+`converge_payment_status` 배선(B 선행) ③ PR-E3: 여신 평가·미결 술어·provider·잠금 헬퍼·SO 5열 마이그레이션·`TargetSpec`·확정 통로 배선(C 승인 코어·B 확정 서비스 선행 — C X2 "소비 접점 스캔은 배선 PR과 같은 PR")·approval-requests·credit-check·확정 패널·GC. 마이그레이션 3건(E-1 policy_settings / E-2 payments / E-3 SO 5열+인덱스+UNIQUE — 전부 additive·신규 테이블 또는 빈 표 컬럼 추가·downgrade 완결·`alembic check` 드리프트 0·왕복 up-down-up 검증).

**부채·관찰 등재(PROGRESS)**: ① 미수 provider 미구현(S3-3) ② 선수금 노출 미차감(S3-3 재판정) ③ 환율 수동 입력 신뢰 한계(`fx_rates` 마스터 재판정 트리거) ④ statement_timeout(57014) 500 누수(벌크 경로 재판정) ⑤ 입금 이중 입력 탐지 없음(S3-3/P7) ⑥ 확정 후 역기록 후속 조치는 사람(경고만) ⑦ 혼합 통화 거래처는 한도 통화를 KRW로 두어야 함(UNEVALUABLE 하드 블록의 운영 함의) ⑧ ON_HOLD 재개 시 게이트 재평가 없음(B 결정 — 한도 하향 미반영 관찰) ⑨ 정책 키 `price_deviation_tolerance_bp`의 소비 확정은 D 의존 ⑩ COMPLETED 제외와 S3-2~S3-3 간극.

**(d) 4금** — 해당 없음. **(e)** 문서. **(f) 테스트** — 계획 등재 자체는 PR 리뷰 항목(인계 항목이 S3-2·S3-3 계획서 DoD에 들어갔는지). **(h) 되돌리기 비용 — 낮음**(문서·부채 등재; 실제 위반은 아키텍처 테스트가 잡는다).

---

## 자율 확정 판정표

| 번호 | 결정 요지 | 근거 한 줄 | 되돌리기 |
|---|---|---|---|
| E1 | 노출=미결 SO(확정 이력 있고 완료·취소 아님, ON_HOLD 포함, 자기 제외)+이번 SO+미수(provider, 기본 `reflected=false`·0 반환 금지·'미수 미반영' 배지), 선수금 미차감, 통화=A7 `comparable_amount`(불가=UNEVALUABLE=승인 불가 확정 거부), NULL=관리 안 함·0=신용불가·strict `>`·이번 SO 0원=통과 | DESIGN 산식 침묵→가장 좁은 안전(과대 산정·fail-visible), A7·C6와 정합 | 낮음 |
| E2 | 여신한도·노출 마스킹 비대상 유지(트리거 소진), 응답 스키마 원가 필드 부재 아키텍처 테스트, 재판정 트리거=역할 세분화·외부 공유 뷰 | 노출=판매금액 합(ADR-0018 값 지식 논리), 원장 #2 기판정과 일치 | 중간 |
| E3 | 잠금=거래처 행 `FOR NO KEY UPDATE`+`LockedBuyer` 토큰, 전역 순서 거래처→QT→PI→SO→approvals→라인→채번(C6 문구 수정 요구), 55P03·40P01→409 `COMMON.CONCURRENCY.LOCK_BUSY`(57014 미매핑), P4 ADR 대체 가능 명기, 결정적 경합 테스트+변이 확인 | §17.2 방식 P4 ADR 선점 회피·자식 INSERT 비블록·교착 방지·500 누수 방지 | 중간(순서표는 높음) |
| E4 | 확정 순서 PI게이트→여신→승인 소비→전이, 거부는 예외·멱등 미기록(성공만 complete), BLOCKED만 커밋 후 raise, SO 5열(`credit_verdict`·`credit_approval_id`·`pi_gate_verdict`·`pi_gate_override_reason`·`confirm_gate_snapshot`)+`confirmed_at` 결속 CHECK+승인 UNIQUE, 우회 7경로 차단(ADMIN 예외 없음) | §17.1·§20 H·C6/X1 계약, CHECK 가능한 불변식은 CHECK | 중간(SO 열은 지금이 최저가) |
| E5 | 승인 요청=명시 `POST …/approval-requests`(확정이 자동 생성 안 함), advisory `credit-check`(잠금 없음), `SO_CREDIT_EXCEEDED` TargetSpec(excess=basis, UNEVALUABLE=AppError) | C X1 계약·§2 기안=사람 행위 | 낮음 |
| E6 | PI 게이트=금액 대조(`split_advance` HALF_UP, 등호 통과), `TT_ADVANCE`만 활성, PI 참조 SO는 PI 조건 기준(결제유형 편집 우회 차단), 모드 OFF(스킵 기록)/WARN(사유 5자+audit)/BLOCK, 미설정=BLOCK, PI 없는 선수금 SO=PI_MISSING | §7.3 문면+A5·B1 정합+평가 불능≠통과 | 낮음 |
| E7 | `payments` 1테이블 S3-1 신설(부호 있는 금액·IMMUTABLE·`pi_id NOT NULL`→S3-3 완화·복합 FK로 역기록 같은 PI·통화·유니크 1회), 입금=TRADE+ADMIN·선수금 전용·due 초과 422·통화 불일치 422, 역기록 후 확정 SO 경고(자동 취소 없음), `converge_payment_status` 호출 | ADR-05 반대 부호·S3-3 재정의 방지(WBS 충돌 해소) | 중간~높음 |
| E8 | `policy_settings` 단일 테이블(키 CHECK 폐쇄 2개+값 형 CHECK)+코드 레지스트리, 미설정=BLOCK/0bp·source 표시, 시드·CLI 없음, ADMIN 전용 GET/PUT+사유 필수+audit, `/settings/policies` 화면 | ADR-11·§15 폐쇄 레지스트리·함정 ⑩·fail-visible | 낮음 |
| E9 | 여신한도 값 변경은 ADMIN 전용(통제 지점=`create_partner`·임포트 `confirm_staging`, `admin_only_fields` 속성, 배치 전체 거부), audit 필수, 프런트 입력 비활성 | 게이트 우회 최저가 차단·실제 쓰기 경로 2곳 실측(PATCH 없음) | 낮음 |
| E10 | 확정 응답은 200만 성공(거부=4xx, 202 없음), 기존 `IDEMPOTENCY_KEY_CONFLICT` 지문 재사용, 프런트 키=(SO,version,사유) 조합 수명, 신규 에러코드 10종+공용 LOCK_BUSY | 성공 토스트 오표시 방지·A13 키 규약 정확화 | 낮음~중간 |
| E11 | DESIGN §7.3·§7.10·§3·§4.6·§17.2·§17.5·§2 보강+ADR 3건+ADR-0026 부기+WBS v1.5+GC v1.4 후보+S3-2/3·S4-1·S5 인계+PR 3분할·마이그레이션 3건 | 설계 변경=DESIGN+ADR 세트(CLAUDE.md), 임시 결정의 조용한 고착 방지 | 낮음 |
