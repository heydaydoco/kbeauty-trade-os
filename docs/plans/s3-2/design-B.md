# S3-2 계획 설계 — 부록 B: 기일 엔진·마일스톤·휴일


> **통합 우선순위(2026-10-04)**: 이 부록과 `design-integrated.md`가 충돌하면 통합 문서가 이긴다. 통합 검토가 모순 해소에 필요한 최소 문면만 고쳤고, 고친 자리는 "[통합 X-nn]"·"[통합 N-nn]"으로 표시했다(목록: 통합 §1.6).
> **적대 검토 정정(2026-10-04)**: 통합 문서 §9(R-01~R-30)가 이 부록과 통합 §0~§8보다 우선한다. 이 부록에서 고친 자리는 "[적대 R-nn]"으로 표시했다(목록: 통합 §9 R-27).
- 기준: main `a4d91c0`(S3-1 종결). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-2 행(W:112-116)을 따른다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `A:줄` = docs/plans/s3-1/design-A.md, `code:경로:줄` = `backend/app/` 아래 경로.
- 판정 방식: 오너 지시(2026-09-29, CLAUDE.md "웹 세션 판정 절차 생략")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. 결정마다 되돌리기 비용을 적었다. PROGRESS 등재 시에는 "자율 확정"으로 표기한다.
- **실행 검증 못 했음.** 정적 독해만 했다. pytest·alembic·DB는 돌리지 않았다. 날짜 산술 예시는 그레고리력으로 손계산한 값이며, PR에서는 단위 테스트로 실측해야 한다.
- 안건 번호는 B1~B20이다. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 / 되돌리기 비용** 순으로 적는다.

---

## 0. 범위와 다른 부록과의 경계

### 0-1. 이 부록이 확정하는 것
- **마일스톤 데이터 모델**: 계획값과 실적값, 날짜형과 시각형, 적용 표
- **자동 계산 순수 함수**
  - 대금만기: TT_ADVANCE / TT_DEFERRED / LC 분기, `balance_anchor` 6종 매핑
  - 적재의무: 수리일+30
  - L/C 제시기한: MIN(B/L+21, 유효기일)
  - tolerance 상·하한
- **실적 입력 뒤 재계산 규율**: 확정 필드 불변 계약과 어떻게 맞물리는지
- **롤오버 이력과 통보 기록**
- **holidays**: 국가 축, 출처, 연도 적재, 경고 의미론
- **KST와 현지일 기준**
- **판정 항목**
  - §8.3 산식 자리
  - OEM 생산 마일스톤 프로파일(P-06·P-57)
  - PO 라인 ETA 슬롯(P-05)
  - QT/PI 만료 임박 D-N 알림(P-03)
- **선적 기일 스캔 잡**과 GC 경계 케이스 표

### 0-2. 경계만 적고 넘기는 것 (다른 부록 소관)

| 주제 | 이 부록이 가정하는 인터페이스 |
|---|---|
| 선적 헤더·라인·당사자·상태 머신·DocKind 편입·채번·CHILD_LINKS·LINE_CONSUMERS·SO RESERVED 엣지 | **선적 헤더**에 다음이 있다고 가정한다: `shipment_kind`(EXPORT·IMPORT·CHANNEL_INBOUND·SAMPLE 계열 4값), `so_id`/`po_id` 중 정확히 하나, **`origin_country_code`·`dest_country_code`(ISO alpha-2, CONTENT)**. 상태 집합 중 "죽은 상태"(`DEAD_STATUSES` 파생)가 무엇인지는 그 부록이 정한다. 이 부록은 "죽은 선적은 스캔·경고 대상이 아니다"만 쓴다. |
| `customs_records` 열 명세 | 수리일의 **단일 원천**은 B7이 정한다. customs_records가 수리일 열을 가지려면 그 열이 마일스톤 실적과 이중 입력이 되지 않게 해야 한다(B7). |
| `lc_terms`(유효기일·제시기간·tolerance·usance), L/C 플래그 공급, tolerance 화면 표시 | S3-3 소관이다(D:71, D:213, P-10). 이 부록은 순수 함수와 K 테스트만 둔다(B5·B6). |
| 미수·aging·입금 충족 판정 | S3-3 소관이다. 대금만기는 **일자만 계산**하고 연체를 판정하지 않는다(B13). |
| 권한 매트릭스 행·LOCK_ORDER 삽입 위치·임포트 계층 등재 | 인가·계층 부록이 정한다. 이 부록은 요구사항만 적는다(B19). |
| 선적 캘린더 뷰 | S4-4(W:152, D:397)다. S3-2에는 포함하지 않는다. |
| comm_logs SHIPMENT 주제 확장의 스키마 | 협업 경계다. B9는 참조 관계만 정한다. |

---

## B1. 마일스톤 종류 열거와 적용 표

**결정**
- 마일스톤 종류는 코드 고정 폐쇄 열거 `MilestoneType`로 두고 DB CHECK와 1:1로 대사한다. 종류는 **저장형(사람 입력)**과 **파생형(계산값, 저장하지 않음)**으로 나눈다.

| 코드 | 한글 | 성격 | 값 형태 | 적용 구분 | 휴일 경고 국가 |
|---|---|---|---|---|---|
| DOC_CUTOFF | 서류마감 | 저장 | 시각(B3) | 수출·수입 | ~~출발국~~ — **[적대 R-09]** |
| CARGO_CLOSING | Cargo Closing | 저장 | 시각(B3) | 수출·수입 | ~~출발국~~ — **[적대 R-09]** |
| PSI | 수출 전 검사 | 저장 | 날짜 | 수출 | — |
| CUSTOMS_CLEARED | 신고수리 | 저장 | 날짜 | 수출·수입 | — |
| ETD | ETD | 저장 | 날짜 | 수출·수입 | ~~출발국~~ — **[적대 R-09]** |
| **BL_ISSUED** | B/L(AWB) 발행일 | 저장 | 날짜 | 수출·수입 | — |
| ETA | ETA | 저장 | 날짜 | 수출·수입 | **도착국(D:203 명시)** |
| IMPORT_TAX_DUE | 수입 세금 납부기한 | 저장(사람 입력) | 날짜 | 수입 | — |
| LOADING_DEADLINE | 적재기한 | **파생**(B7) | 날짜 | 수출 | — |
| PAYMENT_DUE | 대금만기 | **파생**(B4·B5) | 날짜 | 수출·수입 | — |
| PRESENTATION_DEADLINE | L/C 제시기한 | **파생**(B6) | 날짜 | ~~수출 + 결제유형 LC~~ **[적대 R-11] 수출·수입 + 결제유형 LC** | — |

- 채널입고와 샘플 계열의 생성 경로는 헤더 부록이 닫는다. 그 구분의 적용 집합은 경로가 열리는 세션이 이 표에 행을 더한다.

**근거**
- §7.5 9종(D:203). 이 가운데 "적재기한"과 "대금만기"는 같은 절에서 산식으로 정의돼 있다("자동 계산: 대금만기=…, 적재의무=수리일+30일"). 그래서 파생형으로 분류했다.
- 제시기한 산식은 B/L일을 입력으로 요구한다(D:203). 기산점 `BL_DATE`도 열거에 이미 있다(code:modules/trade_docs/constants.py `BalanceAnchor`). 그런데 9종 목록에는 B/L일이 없다. 그래서 **BL_ISSUED를 저장형으로 추가**한다.
- 수입 세금 납부기한의 산식은 DESIGN에 없다(D:203, D:251은 "기일 연동"만 말한다). 추측 구현 금지에 따라 법정 기한을 계산하지 않고 사람이 입력하게 한다.

**대안**
- (a) B/L일을 선적 헤더 열로 둔다. 기각한다. 계획·실적·롤오버 이력 규율이 둘로 갈린다.
- (b) 파생형도 행으로 저장한다. B10에서 기각한다.

**자율 확정**: 확정. 열거 확장(~~9→10~~ **[적대 R-03] 9→11종** — 저장 8[+BL_ISSUED]·파생 3[+PRESENTATION_DEADLINE 편입] 구분)은 DESIGN §7.5 부기와 ADR이 세트로 필요하다(멈춰서 보고할 항목 ①).

**되돌리기 비용**: 낮음. 열거 축소·확장은 CHECK 재정의 마이그레이션 1건과 대사 테스트다.

---

## B2. 마일스톤 저장 모델 — 계획/실적 이중값

**결정** — 테이블 `milestones`(MUTABLE)

- **소유자 FK 2개**: `shipment_id` FK NULL, `po_id` FK NULL. 정확히 하나가 NOT NULL이어야 한다(CHECK `ck_milestones_one_owner`). `po_id`는 OEM 생산 마일스톤용이다(B15). 폴리모픽 `owner_type+owner_id`는 쓰지 않는다. DB FK 무결성을 지키기 위해서다.
- **종류**: `milestone_type`(CHECK, 저장형만 허용 — 파생형 코드는 CHECK가 거부한다).
- **날짜형 값**: `planned_on DATE NULL`, `actual_on DATE NULL`
- **시각형 값**(B3): `planned_at timestamptz NULL`, `actual_at timestamptz NULL`, `tz VARCHAR(64) NULL`(IANA 시간대)
- **형태 CHECK**
  - 날짜형 종류는 `*_at`과 `tz`가 NULL이어야 한다.
  - 시각형 종류는 `*_on`이 NULL이어야 하고, (`planned_at` 또는 `actual_at`) NOT NULL ⇒ `tz` NOT NULL이다.
- **공통 열**: `version`(낙관 잠금 — §17.2), 감사 컬럼, soft delete
- **멱등 유니크**
  - `unique_active(shipment_id, milestone_type)`
  - `unique_active(po_id, milestone_type)`
  - 둘 다 부분 인덱스 `WHERE deleted_at IS NULL`이다.
- 실적은 사실이다. 계획이 없어도 실적만 입력할 수 있다.

**근거**
- "실적 입력 시 후속 재계산"(D:203)과 상태 "선적(ETD 실적)"(D:181)이 계획과 실적을 구분하라고 요구한다.
- 멱등 키는 DB UNIQUE 부분 인덱스로 둔다(D:350-352, CLAUDE.md 기술 규칙).
- 열 대신 행을 쓰는 이유가 있다. 선적 헤더를 커널 동결 대상에 편입하면 `FREE_COLUMNS`가 정확히 4개로 핀 고정돼 있어 실적 열을 둘 수 없다(code:modules/trade_docs/policy.py:33-35, `tests/architecture/test_doc_field_policy.py:64-72`). 실적을 별도 테이블 행에 두면 이 충돌이 사라진다.

**대안**
- (a) 선적 헤더에 `etd_planned/etd_actual…` 열 20개를 둔다. 기각한다. FREE 확장 ADR이 필요하고, 열마다 이력을 따로 만들어야 한다.
- (b) 계획과 실적을 각각 별도 행으로 둔다. 기각한다. 한 종류의 "현재 유효값"을 얻으려면 조인이 2배가 된다.

**자율 확정**: 확정.

**되돌리기 비용**: 중간. 저장 형태를 바꾸려면 데이터 이전 마이그레이션이 필요하다.

---

## B3. 시간 기준 — KST "오늘", 현지일, 시각형 마일스톤

**결정**

1. **날짜형 마일스톤**(ETD·ETA·B/L·수리·PSI·세금기한)은 **그 사건이 일어나는 곳의 현지 달력 날짜**를 DATE로 저장한다. 서류에 찍힌 날짜와 같다. 시간대 변환은 하지 않는다.
2. **파생 산식**(+N일, MIN)은 저장된 DATE끼리의 달력일 산술이다. 시간대가 개입하지 않는다.
3. **"오늘"**(D-N 판정, 도과, 실적 미래일 검증)은 `core/time.today_kst()` 하나로 판정한다. GC-H5 계보이고, UTC 날짜를 쓰면 안 된다(code:core/time.py:40-48 주석).
4. **시각형 2종**(DOC_CUTOFF·CARGO_CLOSING)
   - 시각은 UTC로 저장하고 `tz`(IANA)를 함께 저장한다.
   - 화면에는 **KST와 현지 시각을 병기**한다.
   - 기일 판정에 쓰는 날짜는 **`min(현지 날짜, KST 날짜)`**다. 둘 중 이른 쪽을 써서 더 일찍 경고한다. **[적대 R-20]** 이 날짜(`scan_date`)는 **D-N 문턱에만** 쓴다. **도과(`is_overdue`·OVERDUE 알림)는 시각형에서 `now_utc > effective_at`**으로 판정한다(날짜 비교면 기한 전 최대 ~16시간 '도과' 오표시). **[적대 R-25]** 응답 행에 `scan_date`·`local_date`를 명시 필드로 내리고 화면 문구가 어느 날짜 기준인지 구분한다.
   - `tz` 값은 서버가 `zoneinfo.ZoneInfo`로 검증한다. 모르는 값은 422다.
5. 시간대 원천은 사람 입력이다. 항구 마스터는 신설하지 않는다. 화면 기본값은 출발국 대표 시간대이며, 사람이 바꿀 수 있다(KR이면 `Asia/Seoul`).

**근거**
- "시각=UTC 저장·KST 표시(국제 일정은 현지 시간대 병기)"(D:26), 렌즈 6(D:452).
- `today_kst` 규약(code:core/time.py:40-48).
- 수입선적은 Cargo Closing이 해외 출발지에서 일어난다. KST는 대부분의 지역보다 시각이 앞서므로 KST 날짜만 쓰면 기한 날짜가 늦게 잡힌다(B20 GC-15).

**대안**
- (a) 모든 마일스톤을 timestamptz로 둔다. 기각한다. ETD·ETA는 실무상 날짜 단위이고, 가짜 시각(00:00)이 시간대 변환에서 날짜를 하루 밀어 버린다.
- (b) 시각형도 KST 날짜만으로 판정한다. 기각한다. 덜 엄격하다.

**자율 확정**: 확정.

**되돌리기 비용**: 중간. 형태를 바꾸면 데이터 변환이 필요하다. 판정 날짜 규칙(min)만 바꾸는 것은 낮음이다(함수 1개).

---

## B4. 대금만기 — 결제유형 분기와 `balance_anchor` 6종 매핑

**결정**

- **입력**: 동결된 결제조건 4열을 **읽기만** 한다. **[통합 X-01]** 읽는 곳은 **선적 헤더 사본**이다(부록 A §A2가 원천에서 ORIGIN으로 복사 — S3-1 결정 #5. 이중 저장 금지는 파생값[만기일]에 대한 규칙이다).
- **결과 타입**: `DueResult = (status ∈ {OK, UNKNOWN, NOT_APPLICABLE}, value: date|None, basis ∈ {ACTUAL, PLANNED}|None, reason_code)`
- **분기**
  - **TT_ADVANCE 100%**(anchor·days NULL): `NOT_APPLICABLE`("잔금 없음"). 선수금은 PI 입금 게이트(S3-1)의 영역이라 마일스톤으로 다루지 않는다.
  - **TT_ADVANCE <100%, TT_DEFERRED**: 잔금 만기 = 앵커일 + `balance_days`(달력일). 음수 일수는 ETD에서만 나온다(DB CHECK가 이미 보장 — code:modules/trade_docs/mixins.py `neg_days_etd_only`).
  - **LC**: B5로 위임한다.
  - **결제조건 4열이 전부 NULL**(동결 전 초안): `UNKNOWN`(`TERMS_MISSING`). 동결된 전표에서는 생길 수 없지만 방어한다.
- **앵커 매핑**(재정의 금지 — A:248 "S3-2는 각 앵커를 마일스톤에 매핑만 한다")

| balance_anchor | 원천 | 없을 때 |
|---|---|---|
| ORDER_DATE | 수출: SO `confirmed_at`의 **KST 날짜**(A:248). 수입: PO 발행일(KST 날짜) — **[적대 R-29] PO `frozen_at`(생성=발행 시각, `NOT NULL DEFAULT now()`)의 KST 날짜**(`doc_date`는 쓰지 않음) | UNKNOWN `ANCHOR_PENDING` |
| INVOICE_DATE | CI 발행일 — **S3-3 이전에는 원천이 없음** | 항상 UNKNOWN `INVOICE_NOT_ISSUED`. **ETD로 대체 금지** |
| ETD_DATE | 마일스톤 ETD | UNKNOWN `ANCHOR_PENDING` |
| BL_DATE | 마일스톤 BL_ISSUED | UNKNOWN |
| ARRIVAL_DATE | 마일스톤 ETA | UNKNOWN |
| RECEIPT_DATE | 입고 확정일(S4-1). 판매 체인에서는 원천에서 이미 422로 막힌다 | 항상 UNKNOWN `RECEIPT_NOT_RECORDED` |

- **유효값 규칙**: 실적이 있으면 실적, 없으면 계획을 쓴다. `basis`에 그 출처를 싣는다. 화면은 PLANNED이면 "예정 기준(실적 입력 시 재계산)"을 함께 보인다.
- **휴일**: 대금만기를 **자동으로 미루거나 당기지 않는다.** 휴일 경고 대상도 아니다(은행 소재국을 모델링하지 않았으므로 "휴일 미반영" 주기만 표시한다).

**근거**
- D:203 "대금만기=기산점+일수(결제유형 분기: T/T=약정 기산점 / L/C=네고·인수 기준)".
- 결제조건 구조화(D:173, D:175 ④), A:246-251, code:modules/trade_docs/payment_terms.py:117-136.
- "평가 불능은 통과가 아니다"(D:192 ②, GC-A13).

**대안**
- (a) INVOICE_DATE를 ETD로 근사한다. 기각한다. 추측 구현이고 GC-A13을 위반한다.
- (b) 계획값을 쓰지 않고 실적만 쓴다. 기각한다. 실적이 들어올 때까지 경고가 0건이 되어 덜 엄격하다.

**자율 확정**: 확정.

**되돌리기 비용**: 낮음. 순수 함수 하나와 테스트만 바뀐다(저장값이 없으므로 백필도 없다).

---

## B5. L/C 대금만기 — "네고·인수 기준"

**결정**
- 순수 함수 `lc_payment_due(tenor, negotiated_on, accepted_on, usance_days)`를 둔다.
  - **SIGHT**: 만기 = 네고일.
  - **USANCE**: 만기 = 인수일 + usance 일수.
  - 그 밖의 tenor 형태와 입력 결측은 전부 `UNKNOWN`이다.
- **S3-2 운영 경로에서는 L/C 선적의 PAYMENT_DUE를 항상 `UNKNOWN`(`LC_TERMS_NOT_REGISTERED`)으로 낸다.** 함수 입력을 선적에 임시 열로 받지 않는다. 입력은 S3-3 `lc_terms`가 공급한다.
- "B/L 후 N일"처럼 B/L 기준 usance는 DESIGN 문면에 없다. 그래서 열거하지 않는다(S3-3가 lc_terms 설계 시 가산).

**근거**
- D:203(L/C=네고·인수 기준)
- code:modules/trade_docs/payment_terms.py:134 주석("LC — 기산·선수금은 S3-3 lc_terms 소관")
- P-10(프로덕션 L/C 닫힘), W:115 DoD "T/T와 L/C 만기 계산 분기 테스트"

**대안**: 선적에 `lc_negotiated_on` 같은 임시 열을 둔다. 기각한다. S3-3에서 열을 폐기하고 데이터를 이전해야 한다.

**자율 확정**: 확정. 단 **WBS DoD "L/C 분기"를 단위 테스트로 충족한다는 해석**은 보고 대상이다(멈춰서 보고할 항목 ②).

**되돌리기 비용**: 낮음. S3-3 배선은 가산이다.

---

## B6. L/C 제시기한·tolerance — 순수 함수 + K 테스트

**결정**

- **`presentation_deadline(bl_on, expiry_on, presentation_days=21)`**
  - `bl_on`이나 `expiry_on` 중 하나라도 None이면 `UNKNOWN`이다. **B/L+21로 대체하지 않는다.**
  - 둘 다 있으면 `min(bl_on + presentation_days, expiry_on)`이다.
  - `presentation_days`는 1~365 정수만 받는다. 범위를 벗어나면 ValueError다.
  - `basis`는 `bl_on`의 basis를 승계한다.
- **휴일 연장은 하지 않는다.** UCP 600 제29조 a항(은행 휴무 시 다음 영업일 연장)은 DESIGN 문면에 없다. 연장하지 않는 쪽이 더 이르고 안전하다. 제시기한 마일스톤은 휴일 경고 대상도 아니다.
- **`tolerance_bounds(amount_minor, plus_bp, minus_bp) -> (low, high)`**
  - 정수 최소단위로 계산한다.
  - `low = ceil(amount × (10000 − minus_bp) / 10000)`, `high = floor(amount × (10000 + plus_bp) / 10000)`. 반올림 방향은 **허용폭을 좁히는 쪽**이다.
  - 파이썬 정수로만 계산한다. SQL에서 곱하지 않는다(A:251 BIGINT 선례).
  - bp는 0~10000이다.
  - 경계값은 통과다(`low ≤ x ≤ high`).
- 화면 표시와 lc_terms 결선은 S3-3에서 한다.

**근거**
- D:203(산식), D:429와 W:116 검증 K "L/C 제시기한 MIN·tolerance 상하한"
- D:71(lc_terms에 제시기간 필드 — 21은 기본값일 뿐)
- D:213(tolerance 표시는 §7.10, S3-3)

**대안**: tolerance를 S3-3으로 전부 넘긴다. 기각한다. W:116 검증 K가 S3-2에 배정돼 있다.

**자율 확정**: 확정. 단 "검증 K를 함수 테스트로 충족"한다는 해석은 보고 대상이다(멈춰서 보고할 항목 ②).

**되돌리기 비용**: 낮음. 반올림 방향을 바꾸면 테스트만 수정한다.

---

## B7. 적재의무 = 수리일 + 30 — 단일 원천

**결정**
- **적재기한** = CUSTOMS_CLEARED **실적**(`actual_on`) + 30 달력일이다. **수출 선적에만** 적용한다.
- 계획 수리일로는 계산하지 않는다. 수리 전이면 `UNKNOWN`(`NOT_CLEARED`)이다. 법정 기한을 가상의 수리일로 만들어 내지 않기 위해서다.
- **이행 판정**(계산값, 저장하지 않음)
  - ETD 실적이 있으면 `MET`(ETD ≤ 기한) 또는 `MET_LATE`다.
  - ETD 실적이 없고 오늘(KST) > 기한이면 도과다.
  - **[적대 R-10]** 이행일 = **ETD·BL_ISSUED 실적 중 존재하는 값의 MAX**(둘 다 있으면 늦은 쪽 — fail-closed). DESIGN은 적재 사실의 원천을 정하지 않았으므로(`D:203`) **가정**으로 ADR-0080에 적는다. 대안 "BL_ISSUED 단독"은 B/L 발행 지연 시 이행 판정이 결측되어 기각, "ETD 단독"은 본선적재일(B/L)과의 불일치를 놓쳐 기각. 번복 = 함수 1개(낮음).
  - **[적대 R-06]** 같은 선적·구분에 미수리(`accepted_on IS NULL`) 살아 있는 통관 기록이 1건 이상이면 신고수리 행 `customs_state = PARTIAL`, 화면 "일부 미수리 n건" 배지. 유효 실적 값·적재기한 산식은 MIN 그대로.
- **[통합 X-02 — 아래 원안 대체]** 수리일의 단일 원천은 **`customs_records.accepted_on`**이고 복사하지 않는다. `CUSTOMS_CLEARED` 행은 계획만 저장(`actual_on`은 CHECK로 NULL), 유효 실적 = 구분 일치·살아 있는 통관 기록 `MIN(accepted_on)`(읽기 시 파생). 원안: ~~수리일의 단일 원천은 마일스톤 CUSTOMS_CLEARED 실적~~이다. customs_records 부록이 수리일 열을 둔다면 다음 둘 중 하나를 골라야 한다.
  - (i) 그 열을 두지 않는다.
  - (ii) 그 열을 **유일 입력처**로 하고, 마일스톤 실적은 같은 트랜잭션에서 복사·갱신만 한다.
  - 사람이 두 곳에 입력하는 구조는 금지한다.

**근거**
- D:203 "적재의무=수리일+30일"
- 수출 사슬 "→ 수출신고 → C/O"(D:173)
- 도과는 계산값이다(D:144 ⑦)

**대안**: 계획 수리일로 예정 적재기한을 계산한다. 기각한다. 법정 기한처럼 보이는 가짜 날짜가 생긴다.

**자율 확정**: 확정.

**되돌리기 비용**: 낮음. 함수 1개만 바뀐다.

---

## B8. 실적 입력 뒤 재계산 규율 — 확정 필드 불변과의 관계

**결정**

1. **파생값은 저장하지 않는다.** 조회할 때마다 순수 함수가 다시 계산한다(B10). 그래서 "재계산"은 쓰기 연쇄(fan-out)가 아니라 **읽기 결과의 변화**다. 실적 입력 트랜잭션은 마일스톤 행 1개, 이력 1행, outbox 1건만 쓴다(D:340 1TX).
2. **동결된 전표의 CONTENT·ORIGIN 열은 건드리지 않는다.** SO·PO 결제조건, 선적 헤더 환율·Incoterms가 여기에 해당한다(D:184 ⑦). 재계산의 입력 중 **변하는 것은 마일스톤 값뿐**이고, 결제조건은 동결된 읽기 전용 입력이다. 그래서 "확정 후 단가·환율 불변"(W:109, GC-A6)과 충돌하지 않는다.
3. **파생 마일스톤을 사람이 덮어쓰는 것은 금지**한다. 파생형 코드는 `milestones.milestone_type` CHECK가 거부한다. 쓰기 API도 422 `SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE`을 낸다. 값을 바꾸는 유일한 통로는 입력(실적·계획)을 고치는 것이다.
4. **실적 규율**
   - **[적대 R-18]** 시각형 실적(`actual_at`)은 **`actual_at ≤ now_utc`**(여유 0)만 받는다(아래 +1일 여유는 날짜형 전용). **[적대 R-01]** ETD·BL_ISSUED·ETA 실적은 선적이 RELEASE_ORDERED일 때만(422 `ACTUAL_BEFORE_RELEASE`).
   - 실적 날짜는 **오늘(KST)+1일 이하**만 받는다. 현지 날짜가 KST보다 하루 앞설 수 있는 지역(UTC+10 이상)을 허용하기 위한 여유다. 그보다 미래면 422 `ACTUAL_IN_FUTURE`다.
   - 이미 입력된 실적을 바꾸거나 지우는 것은 **정정**이다. 사유가 필수이고(422 `REASON_REQUIRED`), 이력 행이 남는다.
5. **순서 검증은 경고만** 한다. 계획 ETA < 계획 ETD, 실적 ETA < 실적 ETD 같은 경우다. 항공편이 날짜변경선을 넘으면 현지 날짜상 ETA가 ETD보다 이를 수 있으므로 차단하지 않는다(B20 GC-19).
6. 쓰기 API(`POST …/milestones/{type}/plan|actual`)는 `Idempotency-Key`와 `version`을 요구한다. version이 불일치하면 409다(D:342, D:350).
7. 재계산 결과가 바뀌어도 SO·선적 **상태 전이는 0**이다. 마일스톤 쓰기 경로는 `record_transition`을 호출하지 않는다. `test_no_auto_confirm_code_path_exists`에 "마일스톤·재계산 모듈은 `record_transition`을 임포트하지 않는다"는 엔트리를 추가한다(D:324 ②).
   - ETD 실적과 선적 상태의 연결은 상태 머신 부록의 몫이다. 이 부록은 마일스톤 쓰기가 상태를 바꾸지 않는다는 쪽을 권장한다.

**근거**
- D:203("실적 입력 시 후속 재계산"), D:146 ③("저장 상태를 그대로 믿지 않는다 … 한 정의를 공유"), D:126("저장하지 않고 계산")
- D:340, D:184 ⑦

**대안**
- (a) 파생값을 저장하고 실적 입력 시 갱신한다. 기각한다. 이중 저장이고, 결제조건 정정·코드 수정 때마다 백필이 필요하다.
- (b) 파생값 덮어쓰기를 허용한다. 기각한다. 산식과 실제 값이 갈라져도 감사로 추적할 수 없다.

**자율 확정**: 확정.

**되돌리기 비용**: 중간. 저장형으로 바꾸면 백필이 필요하다. 덮어쓰기 허용은 가산이지만 이력·감사 설계를 추가해야 한다.

---

## B9. 롤오버 이력과 통보 기록

**결정**

- **`milestone_changes`**(IMMUTABLE + `revoke_mutations`, INSERT/SELECT만)
  - 마일스톤 값이 바뀔 때마다 1행을 남긴다.
  - 열: `milestone_id`, `change_kind ∈ {PLAN_SET, PLAN_CHANGED, ACTUAL_RECORDED, ACTUAL_CORRECTED}`, `old_value`/`new_value`(날짜형과 시각형을 각 열로 분리 — 타입 보존), `old_tz`/`new_tz`, `reason`, `actor_user_id`, `created_at`. **[통합 X-06]** `idempotency_key` 열은 두지 않는다(멱등 정본 = `idempotency_keys`).
  - CHECK: `PLAN_CHANGED`·`ACTUAL_CORRECTED`이면 `reason`이 NOT NULL이고 공백이 아니어야 한다(제어문자 검사는 `core/text.invisible_char_problem`).
  - **"롤오버" = `PLAN_CHANGED` 행**이다. 화면에서 ETD·ETA·CARGO_CLOSING의 PLAN_CHANGED를 "롤오버"로 표기하고 횟수를 계산값으로 보인다.
  - 계획을 지우는 경로는 없다(변경만 가능하다). 실적은 `ACTUAL_CORRECTED`로 지울 수 있다(사유 필수).
- **통보 기록 = `milestone_change_notices`**(IMMUTABLE)
  - 열: `change_id` FK, `comm_log_id` FK, `actor_user_id`(**[통합 X-06]**), `created_at`. (change_id, comm_log_id)는 유니크다.
  - 통보 내용은 comm_logs의 **SHIPMENT 주제 1종 확장**으로 기록한다(D:132 ③ "포워더·관세사는 comm_logs 주제 열거 확장으로 소비 세션이 붙는다").
  - 이력 행이 불변이라 통보는 사후 연결 테이블로 남긴다.
  - 통보가 연결되지 않은 롤오버에는 "통보 기록 없음" 배지를 단다(계산값, 차단 없음).
- **발송 코드 경로는 0**이다. 통보는 "일어난 일의 기록"이다(D:132 ④, L3 "대외 최초 발송" 금지 D:313).
- 트랜잭션: 마일스톤 UPDATE + 변경 이력 INSERT + outbox `shipments.milestone.changed`(payload: 선적 id, 종류, 변경 종류, 전후 날짜. 금액·원가 없음)를 1TX로 묶는다. 같은 Idempotency-Key로 재요청하면 최초 결과를 돌려주고 이력은 1행만 남는다.

**근거**
- D:203 "스케줄 변경(롤오버) 이력+통보 기록", D:19 "수동 롤오버 입력+이력이 정답"
- D:356 "상태 변경 이력 성격의 테이블은 같은 지위로 신설 세션이 등재"(IMMUTABLE)
- D:132 ③④

**대안**
- (a) 통보를 이력 행의 필드로 둔다. 기각한다. 불변 행이라 사후 기록이 불가능하고, §7.9 파트너 허브와 이중 구조가 된다.
- (b) 마일스톤 행을 수정 가능 이력으로 둔다. 기각한다. 정정은 새 행이라는 원칙(ADR-05 계보)에 어긋난다.

**자율 확정**: 확정. 다음 두 등재가 DESIGN §17.5 확장과 ADR 세트로 필요하다.
- comm_logs 주제 확장(`COMM_SUBJECT_TYPES`, code:modules/collaboration/models.py:55)
- IMMUTABLE 2표 등재

**되돌리기 비용**: 중간. 이력 형태를 바꾸려면 불변 표 데이터 이전이 필요하고, 그 이전에는 권한 해제 마이그레이션이 따라온다.

---

## B10. 계산 모듈 배치와 순수 함수 시그니처

**결정**
- 순수 계산은 **DB·세션·시계에 의존하지 않는** 모듈에 둔다. "오늘"은 인자로 받는다.
- 배치는 다음과 같다(계층 등재는 계층 부록이 확정).
  - `app/modules/trade_docs/schedule.py`(L0, 커널 상수만 임포트): 대금만기·L/C·제시기한·적재기한·tolerance
  - `app/modules/holidays/calc.py`(플랫폼, 도메인 무임포트): 휴일 판정

```python
# trade_docs/schedule.py — 전부 순수, 예외는 잘못된 인자(ValueError)뿐
class Basis(StrEnum): ACTUAL; PLANNED
class Status(StrEnum): OK; UNKNOWN; NOT_APPLICABLE
@dataclass(frozen=True)
class DateValue: value: date; basis: Basis            # effective() 결과
@dataclass(frozen=True)
class DueResult: status: Status; value: date | None; basis: Basis | None; reason: str | None

def effective(planned: date | None, actual: date | None) -> DateValue | None
def cutoff_scan_date(instant_utc: datetime, tz: str) -> date          # min(현지일, KST일) — B3④
def resolve_anchor(anchor: BalanceAnchor, ctx: AnchorContext) -> DateValue | DueResult  # B4 표
def payment_due(terms: PaymentTerms, ctx: AnchorContext, lc: LcInputs | None) -> DueResult
def lc_payment_due(tenor: str | None, negotiated_on, accepted_on, usance_days) -> DueResult
def presentation_deadline(bl: DateValue | None, expiry_on: date | None, presentation_days: int = 21) -> DueResult
def loading_deadline(cleared_actual_on: date | None) -> DueResult
def loading_fulfilment(deadline: DueResult, etd_actual_on: date | None, today: date) -> str  # MET/MET_LATE/OPEN/OVERDUE/UNKNOWN
def tolerance_bounds(amount_minor: int, plus_bp: int, minus_bp: int) -> tuple[int, int]

# holidays/calc.py
class HolidayFlag(StrEnum): HOLIDAY; CLEAR; UNVERIFIED
def holiday_flag(day: date, country: str, covered_years: frozenset[int], holidays: Mapping[date, str]) -> tuple[HolidayFlag, str | None]
```

- **조회 조립자**(L2, 선적 상세 API)는 다음 일을 한다. 쿼리 고정 횟수로 N+1을 금지한다(D:376).
  - 한 선적의 마일스톤 행
  - 원천 전표의 결제조건
  - 관련 국가·연도 휴일 일괄 로드
  - 위 순수 함수 호출
- 기일 스캔(B13)과 화면은 **같은 함수**를 쓴다. 정의를 이원화하지 않는다(D:146 ③).

**근거**
- §17·D:146 ③ 단일 정의
- `deadlines/service.py:83-112` 순수 함수 선례(code:modules/deadlines/service.py)

**대안**: 계산을 서비스 메서드 안에 둔다. 기각한다. 스캔과 화면이 갈라지고 GC를 단위 테스트로 고정할 수 없다.

**자율 확정**: 확정.

**되돌리기 비용**: 낮음.

---

## B11. holidays — 국가 축·출처·연도 적재

**결정**

- **`holiday_calendar_years`**(MUTABLE): 국가×연도 단위의 **적재 선언**이다.
  - 열: `country_code CHAR(2)`(CHECK `^[A-Z]{2}$`), `year`(2000~2999), `source_url NOT NULL`, `verified_on DATE NOT NULL`, 감사 컬럼, soft delete
  - `unique_active(country_code, year)`
- **`holidays`**(MUTABLE)
  - 열: `country_code`, `holiday_on DATE`, `name`(제어문자 금지), `calendar_year_id` FK NOT NULL
  - `unique_active(country_code, holiday_on)`
  - CHECK `extract(year from holiday_on)`와 선언 연도 일치는 서비스와 테스트가 맡는다(교차 테이블이라서).
- **국가 키는 ISO alpha-2 CHECK이고 `markets.code` FK가 아니다.**
  - markets는 시장 축이라 EU 같은 비국가 코드를 허용한다(code:modules/markets/models.py:47-48).
  - 출발국 KR이나 경유국은 시장이 아니다.
  - 근거 2필드(출처·확인일)는 **연도 선언 단위로 필수**다(ADR-03 "기한 데이터에 근거링크+최종확인일 필수" D:27).
- **적재 경로**
  - `PUT /api/v1/holidays/{country}/{year}`: 그 국가·연도의 휴일 집합 **전체를 원자 교체**한다. 연도 선언, 기존 행 soft delete, 새 행 INSERT를 1TX로 한다. Idempotency-Key를 받는다.
  - 화면 입력과 CSV 업로드(UTF-8 BOM·수식 이스케이프 §12.2 왕복, D:277-279)는 이 API의 클라이언트다.
  - **마이그레이션 시드와 CLI 시드는 0건이다**(함정 ⑩, D:322).
  - **외부 자동 수집은 0건이다**(근거 없음).
- 목록 API는 페이지네이션 기본 50이다(D:376).
- **편집 권한은 ADMIN 전용, 열람은 전 역할이다.** 행 확정은 인가 부록이 한다.

**근거**: D:59 "holidays(국가별)", D:203, D:393, D:27, D:322

**대안**
- (a) markets FK. 기각한다(위 이유).
- (b) 행 단위 출처. 기각한다. 연도 단위 일괄 출처(관보·정부 공고)가 실무 단위이고, 행마다 받으면 입력 마찰만 커진다.
- (c) ADMIN+LOGISTICS 편집. 보류한다. 더 좁은 쪽으로 확정한다.

**자율 확정**: 확정. markets 비FK 판단은 ADR로 고정한다.

**되돌리기 비용**
- 중간: markets FK로 바꾸려면 비시장 국가 행 정리가 필요하다.
- 낮음: 편집 역할 확대는 매트릭스 행만 바꾼다.
- 근거 필수 제약을 나중에 거는 것은 결측 백필이 필요해 높다. 그래서 지금 건다.

---

## B12. 휴일 경고 의미론 — 경고만, 자동 이동 금지

**결정**

1. **경고만 한다. 날짜를 자동으로 이동(순연·당김)하지 않는다.** 만기를 미루는 것은 계약 해석이므로 사람(L1)이 한다. 차단도 하지 않는다.
2. **판정은 3값**이다.
   - `HOLIDAY`(이름 동반): 해당 국가·연도가 선언돼 있고 그날이 휴일
   - `CLEAR`: 선언돼 있고 휴일이 아님
   - **`UNVERIFIED`**: 선언이 없음. 화면 배지 "휴일 캘린더 미등록 — 확인 불가". **경고 없음이 평일을 뜻하지 않는다.**
3. **적용 대상**(B1 표)
   - ETA → 도착국(D:203 명시)
   - ~~ETD·CARGO_CLOSING·DOC_CUTOFF → 출발국~~ **[적대 R-09]** S3-2는 **ETA(도착국)만** 판정한다(`D:203` 문면 "ETA 현지 연휴 경고"). 출발국 확장은 부채(트리거: 사용자 요구 또는 S4-4) — 수입선적 출발국 미선언 UNVERIFIED 배지 대량 발생도 피한다.
   - 판정 날짜는 유효값(실적 우선, 없으면 계획)이다. 시각형은 현지 날짜로 판정한다.
   - 대금만기·제시기한·적재기한·세금기한은 판정하지 않는다(B4·B6. 주기 "휴일 미반영").
4. 국가 원천은 선적 헤더의 `origin_country_code`·`dest_country_code`다. NULL이면 `UNVERIFIED`다.
5. 주말은 판정하지 않는다. 국가별 주말 요일 규칙(금·토 휴무국 등)은 DESIGN에 없다. 부채로 등재한다(트리거: 사용자 요구 또는 S4-4 캘린더 뷰).
6. 휴일 경고는 **조회 계산값**이다. 알림(기일 스캔)은 내지 않는다(B13 범위 밖).

**근거**
- D:203 "국가별 휴일 캘린더 반영(ETA 현지 연휴 경고)", W:115 DoD "ETA 현지 연휴 → 경고"
- D:192 ② "UNKNOWN은 통과가 아니며"
- A:249의 "휴일 보정은 S3-2" — DESIGN 정본은 "경고"이므로 보정은 **경고로 해석**한다

**대안**
- (a) 영업일 자동 순연. 기각한다. 문면에 없고, 만기가 조용히 변한다.
- (b) 미등록을 CLEAR로 처리. 기각한다(fail-open).

**자율 확정**: 확정. design-A:249의 "보정"을 "경고"로 해석한 것을 ADR에 기록한다.

**되돌리기 비용**
- 낮음: 경고 범위의 확대·축소는 함수와 화면만 바꾼다.
- 중간: 자동 순연을 나중에 넣으면 계산 결과가 바뀌므로 재계산 공지가 필요하다.

---

## B13. 선적 기일 스캔(알림) — 대상·문턱·잡

**결정**

- **대상**: 죽지 않은 선적의 마일스톤 가운데 다음 4종이다.

| 종류 | 대상 조건 | 충족 신호 |
|---|---|---|
| DOC_CUTOFF | 계획 있음 + **실적 없음** | 실적 |
| CARGO_CLOSING | 계획 있음 + **실적 없음** | 실적 |
| IMPORT_TAX_DUE | 계획 있음 + **실적 없음** | 실적 = 납부일 |
| LOADING_DEADLINE | 파생값 OK | ~~ETD 실적~~ **[적대 R-10] ETD 또는 BL_ISSUED 실적(이행일 = MAX)** |

  - **PAYMENT_DUE·PRESENTATION_DEADLINE은 S3-2 알림에서 제외**하고 표시만 한다. 충족 신호(입금·제시)가 S3-3 소관이라 지금 알리면 이미 입금된 건에 도과 알림이 나간다. 부채로 등재하고 트리거는 S3-3 receivables·lc_terms다.
- **문턱**: `alert_rules.config.thresholds` 데이터(event_type `shipments.milestone.approaching`)로 둔다. 규칙이 없으면 코드 기본 **D-7/3/1**과 도과를 쓴다. 기존 `DEFAULT_THRESHOLDS=(180,90,30)`(code:modules/deadlines/service.py:61)은 선적에 부적합하다.
  - `deadlines._policy`(code:modules/deadlines/service.py:142)가 기본값을 인자로 받도록 **공개 함수로 승격**한다. 기존 2축의 결과는 불변이어야 하고, 회귀 테스트를 둔다.
- **의미론**: S2-3을 그대로 승계한다(D:144 ①~⑤).
  - 문턱은 "지났다"로 판정한다.
  - 도과 건에는 지난 문턱을 소급 발송하지 않는다.
  - dedup 키는 ~~`deadline:milestones:{id}:{문턱}@{기일}:{수신자}`~~ **[통합 X-25]** `deadline:shipments:{shipment_id}:{MILESTONE_TYPE}/{문턱}@{기일}:{수신자}`다(파생 LOADING_DEADLINE은 행 id가 없고, 알림 대상은 소유 전표). 에스컬레이션 조회 prefix는 TYPE까지 포함한다. **롤오버로 기일이 바뀌면 새 알림**이 된다.
  - D-3 에스컬레이션을 쓴다.
  - 수신자는 선적 담당자 → 규칙 → ADMIN 폴백(Routing.DEADLINE)이다.
- **배치**
  - 스캔은 L2 신규 모듈이 소유한다. `deadlines`의 순수 함수와 `notifications.notify`만 임포트한다. 플랫폼 `deadlines`가 전표를 임포트하는 것은 금지다(`tests/architecture/test_import_direction.py:64-69`).
  - 건별 독립 트랜잭션으로 처리하고, 1건이라도 실패하면 잡은 FAILED다(D:144 ⑧, D:360).
- **잡**: `trade-deadline-scan` 1행, `daily@06:40` KST. 06:30 deadline-scan 뒤, 07:00 앞이다.
  - 선적 마일스톤과 B18 QT/PI 만료 임박을 한 잡에서 처리한다.
  - 이름에 `purchase`·`발주`·`-po-`를 쓰지 않는다(`tests/architecture/test_po_no_auto_path.py:352-361`).
  - 레지스트리 총수를 ~~12→13~~ **[적대 R-17] 13→14**(무결성 잡이 PR-1b에서 먼저 12→13)으로 갱신하고, 4금 집합 테스트(`test_scheduler_registry.py:143-174`), runbook 잡 표, DESIGN §15 잡 매핑 부기, ADR을 함께 고친다.
  - **4금 논증**: 전표 상태를 바꾸지 않고, 알림 생성만 한다. 대외 발송·지출·장부·법적 판정이 0이다.
- 도과·D-N은 계산값이다. 저장 컬럼은 없다(D:144 ⑦).

**근거**: D:11 ②(기일 사고), D:31(ADR-07 알림 정확성), D:144, D:320-324

**대안**
- (a) 기존 `deadline-scan`에 합친다. 기각한다. 임포트 방향을 위반한다.
- (b) 대금만기도 알린다. 기각한다. 충족 신호가 없어 오경보가 난다.
- (c) 문턱을 정책 키로 둔다. 기각한다. `policy_settings`에는 일수 목록 형이 없고 마찰 세트가 붙는다(code:modules/policies/registry.py:1-15).

**자율 확정**: 확정.

**되돌리기 비용**
- 낮음: 문턱은 데이터다.
- 중간: 대상 종류를 확대하면 dedup 키 계보와 맞물리므로 신규 종류를 가산만 한다.

---

## B14. §8.3 가용재고 산식 "자리만"

**결정**
- S3-2는 산식(현재고 − 유효 할당 − 격리분)을 **구현하지 않는다.**
- 선적 라인 조회 시 ~~기존 `AllocationPort`의 결과를 그대로 노출~~ **[통합 X-26]** 포트에는 읽기 메서드가 없으므로(포트 무변경) `AllocationStatus.NOT_IMPLEMENTED` 값을 싣는 조립 함수 1개가 응답 `availability.status`를 만든다. 화면은 **"가용재고 미산정"**으로 표시한다.
- **0이나 현재고를 가용으로 표시하지 않는다**(fail-visible).
- 이 부록의 기일 함수들은 재고를 입력으로 받지 않는다. 테스트로 "schedule.py는 재고 모듈을 임포트하지 않는다"를 고정한다.
- 포트 배선 위치는 선적 라인 부록과의 경계다.

**근거**: D:227, D:393, W:113 "(자리만)", D:229 ③, P-21

**대안**: 원장 합계로 임시 가용을 계산한다. 기각한다. 할당이 없는 가용은 과대 표시이고 S4-2 소관을 침범한다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## B15. OEM 생산 마일스톤 프로파일(P-06·P-57)

**결정**
- OEM 생산 마일스톤은 **`milestones`의 PO 소유 행**(B2 `po_id`)으로 둔다. 종류는 코드 고정 4종이다.
  - RAW_MATERIAL_READY(원료수급), FILLING(충진), PACKING(포장), OUTGOING_INSPECTION(출하검사)
- 적용 조건은 `purchase_orders.po_kind = 'OEM_PRODUCTION'`이다(code:modules/trade_docs/constants.py `PoKind`, "S3-2 마일스톤 프로파일의 키"). 서비스가 검증하고, 다른 PO에 만들면 422다. 교차 테이블 규칙이라 아키텍처 테스트로도 고정한다.
- **`profile_id`와 프로파일 마스터는 S3-2에서 신설하지 않는다.** DESIGN이 정한 프로파일은 하나(4단계)뿐이다(D:98). 두 번째 프로파일 요구가 생길 때 `purchase_orders.profile_id` nullable을 가산한다(트리거 등재).
- **facilities 마스터는 미신설을 유지**한다. P-57 트리거 (a) "시설 단위 일정 필요"에 대해 판정하면 **불필요**하다. 4단계 일정은 PO(=거래처 1곳) 단위로 충분하다. 트리거 (b)·(c)는 유지한다.
- 계획·실적·롤오버 이력 규율은 B2·B8·B9와 같다. PO는 생성 즉시 동결되지만, 마일스톤은 별도 행이라 FIELD_POLICY와 충돌하지 않는다.
- **알림은 하지 않고 표시만** 한다(D:98 "적용 가능". 부채: 트리거는 사용자 요구).
- `milestones.po_id` FK는 후속 전표가 아니다. 그래서 `NON_CHILD_FK_ALLOWLIST`에 사유와 함께 등재한다. 사슬 테스트 `test_every_fk_to_chain_docs_is_registered`가 이를 요구한다(code:modules/trade_docs/chain.py:47-).
- PO 원가는 노출하지 않는다(마일스톤에는 금액 열이 없다).

**근거**: D:98, W:114, P-06(P:564), P-57(P:615), ADR-0037

**대안**
- (a) 프로파일 마스터와 `profile_id`를 지금 둔다. 기각한다. 프로파일이 1개뿐이라 죽은 일반화이고, 마스터 화면·권한·시드가 딸려 온다.
- (b) facilities 신설. 기각한다(트리거 미발동).

**자율 확정**: 확정. WBS 문면 "`profile_id`"를 "판정 결과 미신설(트리거 등재)"로 처리하는 것은 PROGRESS에 자율 확정으로 표기한다.

**되돌리기 비용**: 낮음. nullable 열 가산이고, 기존 OEM PO는 NULL로 기본 프로파일로 해석된다.

---

## B16. 마일스톤 세트(§4.8 item_profiles)와 계획 초안

**결정**
- **선적 계획 초안**은 사람 1클릭으로 만든다(L2). SO 확정 통로는 건드리지 않는다.
  - 초안은 적용 종류 집합에 대해 **빈 계획 행**을 만든다.
  - 적용 종류 집합 = **(B1 구분별 적용 집합) ∩ (선적 라인 SKU들의 item_profile 마일스톤 세트 합집합)**이다.
  - SKU에 프로파일이 없거나 프로파일에 세트가 정의돼 있지 않으면 **구분별 적용 집합 전부**를 쓴다. 누락보다 과다가 안전하다.
- `item_profile_milestone_types`(품목군 × 저장형 종류, `unique_active`)를 신설해 §4.8 문면을 이행한다(부채 #15 마일스톤 몫 종결, ADR-0021 "각 세션 DoD에서 확인").
- 초안은 같은 Idempotency-Key로 1회만 만들어진다. 이미 있는 종류는 건너뛴다(부분 유니크).

**근거**: D:106 "마일스톤 세트 — 신규 등록 시 자동 적용", D:190 "선적 계획 초안(프로파일 마일스톤)", ADR-0021:9, P:709

**대안**
- (a) 세트를 구분 축에만 건다. 기각한다. §4.8 문면 미이행이다.
- (b) SO 확정 시 자동 생성. 기각한다. 확정 통로에 부작용이 생기고 자동 확정 부재 검토 범위가 커진다.

**자율 확정**: 확정. 테이블 소유가 마스터 부록과 겹치면 통합에서 소유처만 조정한다.

**되돌리기 비용**: 낮음.

---

## B17. PO 라인 ETA 슬롯(P-05)

**결정**
- **PO 라인에 열을 추가하지 않는다.**
- PO 라인 입고예정은 **조회 계산값**이다.
  - 그 라인을 참조하는 살아 있는 수입선적들의 ETA 유효값(실적 우선) 목록이다.
  - 대표값은 **가장 늦은 날짜**다. 전량이 도착해야 입고가 완결되므로 보수적인 값이다.
  - 수입선적이 없으면 **"입고예정 미정"**이다.
- SO 라인 `requested_delivery_date`(CONTENT, "S3-2 마일스톤 기준값")는 **참고 표시만** 한다. 납기와 ETA·ETD를 비교하는 규칙은 Incoterms에 따라 의미가 달라지는데 DESIGN이 침묵하므로 계산하지 않는다(부채 등재).

**근거**
- P-05(P:563), design-F.md:269 (a)안
- FREE 4열 핀(`tests/architecture/test_doc_field_policy.py:64-72`), D:110 ⑥(출처 이중화 방지 논리)
- D:269(백오더가 PO 입고예정을 자동 추종)

**대안**: `purchase_order_lines.expected_receipt_on` 열 + FREE 확장(design-F (b)). 기각한다. FREE 핀 ADR이 필요하고 원천이 이중화된다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음. S3-4·P4가 라인 단위 예정일을 요구하면 nullable 열과 FREE 확장 ADR을 가산한다.

---

## B18. QT/PI 만료 임박(D-N) 알림(P-03)

**결정**
- **S3-2에서 구현**한다. 트리거 "기일 엔진 착수"가 충족됐다.
- **후보는 만료 스윕과 같은 정의**다. 정의가 둘로 갈리면 안 된다.
  - QT·PI가 `ISSUED`이고, 삭제되지 않았고, `valid_until`이 있고, 살아 있는 후속이 없어야 한다(`has_live_children`, code:modules/trade_chain/expiry_sweep.py:12, :42-45).
  - 유효 경계는 "당일 KST 24:00까지 유효"(code:modules/trade_docs/expiry.py:1-5)다.
  - **`days_left = valid_until − today_kst()`**이고, 0이면 당일이다. 도과분은 스윕이 EXPIRED로 전이하므로 알림 대상이 아니다.
- **문턱**: event_type `quotations.validity.approaching`, `proforma_invoices.validity.approaching`. 규칙이 없으면 기본 **D-7/3/1**이다.
- **dedup**: `…@{valid_until}` 계보를 쓴다. 유효기간을 정정하면 새 알림이 된다.
- **수신자**: 전표 담당자 → 규칙 → ADMIN이다.
- B13의 `trade-deadline-scan` 잡에서 처리한다(별도 잡 없음).
- 4금: 상태 전이 0, 알림만 생성한다. 바이어에게 보내는 리마인드는 금지다(S5-4 outbound_policies 소관, D:319).

**근거**: W:114, P-03(P:561), D:144, D:324 ①(기존 스윕은 전이만 한다)

**대안**: 미구현 유지. 기각한다. 트리거가 이미 충족됐다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음. 잡 행을 끄거나 규칙 문턱을 비우면 된다.

---

## B19. 공통 계약 적용 요구(§17·§18·§22) — 이 부록 범위

| 계약 | 요구 |
|---|---|
| 1TX(D:340) | 마일스톤 쓰기 = 행 UPDATE/INSERT + `milestone_changes` + outbox를 1TX로. 휴일 연도 교체도 1TX. 트랜잭션 안 외부 호출 0 |
| 잠금(D:342-344) | 마일스톤 쓰기는 선적 헤더 `FOR UPDATE` → 마일스톤 행 version 대조 순서. LOCK_ORDER에서 milestones 위치는 shipments 바로 뒤(ADR, 계층·잠금 부록이 확정). 파생값을 저장하지 않으므로 SO·PO 잠금은 없다 |
| 멱등(D:350-352) | plan/actual/초안/휴일 PUT 전부 `Idempotency-Key`. 부분 유니크 위반은 제약명 → 409 번역 |
| 불변(D:354-358) | `milestone_changes`·`milestone_change_notices` IMMUTABLE + `revoke_mutations`. stock_movements 무접촉 |
| 실패(D:360-362) | 스캔은 건별 독립 TX, 재실행 멱등(dedup) |
| 인가(D:368-370) | 마일스톤 쓰기 역할은 인가 부록(선적 쓰기 역할과 동일 권장), 휴일 쓰기는 ADMIN. 부모-자식(선적↔마일스톤) 불일치 404 |
| 에러(D:374) | 신규: `SHIPMENTS.MILESTONE.{DERIVED_NOT_EDITABLE, ACTUAL_IN_FUTURE, REASON_REQUIRED, TYPE_NOT_APPLICABLE, OWNER_NOT_OEM}`, `HOLIDAYS.CALENDAR.{SOURCE_REQUIRED, YEAR_MISMATCH}`, `HOLIDAYS.COUNTRY.INVALID`. 문구에 조치 힌트 |
| 페이지네이션(D:376) | 마일스톤 변경 이력·휴일·연도 선언 목록 기본 50. 선적 상세 타임라인은 일괄 로딩(쿼리 수 고정 테스트) |
| 시간(렌즈 6 D:452) | B3. 프런트 `lib/datetime.ts`에 현지 병기 함수 신설(UTC 원문 노출 금지 — PR-16 결함①, P:16) |
| 한국어 UI(D:303) | 마일스톤 표의 날짜·D-N은 가운데 정렬, 종류명·국가명은 nowrap, 사유는 break-keep. 390px 가로 스크롤 0 |
| 운영(렌즈 9) | runbook: ① 운영 개시 전 출발·주요 도착국 휴일 연도 선언 ② 잡 표 ~~13행~~ **14행([적대 R-17])** ③ "UNVERIFIED 배지 = 캘린더 미등록" 안내 |
| 문서(렌즈 10) | DESIGN §7.5 부기(~~10종~~ **9→11종 [적대 R-03]**·파생·휴일 의미론·롤오버·통보), §3 표 맵(holiday_calendar_years·milestone_changes·notices·item_profile_milestone_types), §17.5 IMMUTABLE 2표, §15 잡 행. ADR(번호 0074~, 통합에서 부여): 파생 마일스톤 계산값·휴일 국가 축·근거 규약·경고만·OEM 프로파일 판정 |

---

## B20. GC 경계 케이스 표 (GC v1.5 신설 후보 — `golden` 마커)

> 날짜 산술 값은 손계산이다. PR에서 단위 테스트로 실측해 확정한다. "오늘"은 모두 인자로 주입한다.

| # | 함수 | 입력 | 기대 | 지키는 것 |
|---|---|---|---|---|
| GC-01 | payment_due | TT_DEFERRED, anchor BL_DATE, days 30, BL 실적 2026-12-15 | OK 2027-01-14, ACTUAL | 기본 분기·연도 넘김 |
| GC-02 | payment_due | TT_ADVANCE 30%, ETD_DATE, days −7, ETD 계획 2026-11-05(실적 없음) | OK 2026-10-29, **PLANNED** | 음수=ETD 전용, 예정 기준 표기 |
| GC-03 | payment_due | GC-02에 ETD 실적 2026-11-09 입력 | OK 2026-11-02, ACTUAL(재계산) | 실적 우선·재계산 |
| GC-04 | payment_due | TT_ADVANCE 100% | NOT_APPLICABLE | 잔금 없음 ≠ UNKNOWN |
| GC-05 | payment_due | TT_DEFERRED, INVOICE_DATE, days 60 | UNKNOWN `INVOICE_NOT_ISSUED` | ETD 대체 금지(GC-A13) |
| GC-06 | payment_due | TT_DEFERRED, ETD_DATE, ETD 계획·실적 둘 다 없음 | UNKNOWN `ANCHOR_PENDING` | 0일·오늘 대체 금지 |
| GC-07 | payment_due | TT_DEFERRED, ORDER_DATE, days 0, SO `confirmed_at` = 2026-10-03T15:30Z | OK **2026-10-04**(KST 날짜) | ORDER_DATE = KST 확정일 |
| GC-08 | payment_due | LC(운영 경로, lc 입력 None) | UNKNOWN `LC_TERMS_NOT_REGISTERED` | L/C 운영 닫힘 |
| GC-09 | lc_payment_due | SIGHT, 네고 2027-02-10 | OK 2027-02-10 | 네고 기준 |
| GC-10 | lc_payment_due | USANCE 90, 인수 2027-01-31 / 인수 None | OK 2027-05-01 / UNKNOWN | 인수 기준·결측 |
| GC-11 | presentation_deadline | BL 2027-03-01, 유효 2027-03-31 | OK 2027-03-22(B/L+21이 이름) | MIN 한쪽 |
| GC-12 | presentation_deadline | BL 2027-03-01, 유효 2027-03-15 / 유효 2027-03-22 | OK 2027-03-15 / OK 2027-03-22(같은 날 경계) | MIN 반대쪽·동일일 |
| GC-13 | presentation_deadline | 유효 None 또는 BL None / presentation_days 15 | UNKNOWN(B/L+21 대체 금지) / BL+15 | 결측·제시기간 인자 |
| GC-14 | loading_deadline | 수리 실적 2027-01-31 / 2028-01-31 / 수리 실적 None(계획만 있음) | 2027-03-02 / **2028-03-01**(윤년) / UNKNOWN `NOT_CLEARED` | 달력일·윤년·계획 미사용 |
| GC-15 | cutoff_scan_date | 2026-10-11T00:00Z, `America/Los_Angeles` | **2026-10-10**(현지일 10-10 < KST일 10-11) | 이른 쪽 |
| GC-16 | holiday_flag | ETA 2026-10-01 CN, CN 2026 선언에 10-01 국경절 등재 / 10-09 미등재 | HOLIDAY("국경절") / CLEAR | DoD "ETA 현지 연휴 → 경고" 양방향 |
| GC-17 | holiday_flag | ETA 2027-01-04, 도착국 2027 연도 선언 없음 | **UNVERIFIED** | 경고 없음 ≠ 평일 |
| GC-18 | 휴일 이동 금지 | 대금만기가 도착국 휴일과 같은 날 | 값 불변, "휴일 미반영" 주기 | 자동 순연 0 |
| GC-19 | 실적 검증 | 실적 ETD 2026-10-04(현지), 실적 ETA 2026-10-03(LAX) | 저장 성공 + 순서 경고(422 아님) | 날짜변경선 |
| GC-20 | 실적 검증 | today_kst 2026-10-03, 실적 2026-10-04 / 2026-10-05 | 성공 / 422 `ACTUAL_IN_FUTURE` | +1일 여유 경계 |
| GC-21 | 파생 덮어쓰기 | `PUT …/milestones/PAYMENT_DUE/plan` | 422 `DERIVED_NOT_EDITABLE`. DB 직접 INSERT도 CHECK 위반 | 덮어쓰기 금지 2중 |
| GC-22 | 롤오버 | ETD 계획 11-05 → 11-12(사유) → 같은 Idempotency-Key 재요청 | 이력 PLAN_SET 1 + PLAN_CHANGED **1**, 사유 없으면 422 | 이력 누적·멱등 |
| GC-23 | 롤오버 불변 | `milestone_changes` UPDATE/DELETE(앱 역할) | 권한 거부 | IMMUTABLE |
| GC-24 | 스캔 dedup | CARGO_CLOSING D-3 알림 후 롤오버로 기일 +7 | 새 기일 키로 새 알림, 옛 키 재발송 0 | dedup@기일 |
| GC-25 | 스캔 충족 | CARGO_CLOSING 실적 입력 후 재스캔 / 적재기한: ETD 실적 있음 | 알림 0 / MET 또는 MET_LATE, 알림 0 | 충족 신호 |
| GC-26 | 재현성 | 입력 이력을 시점 t까지 재생 → 함수 결과 | t 당시 화면값과 일치 | 파생 비저장의 감사 가능성 |
| GC-27 | tolerance_bounds | 1,000,001, +5%/−5% | (950,001, 1,050,001) | 좁은 쪽 반올림 |
| GC-28 | tolerance_bounds | 1,000,000, +10%/−0% / 경계 x=1,100,000 | (1,000,000, 1,100,000) / 통과 | 경계 포함 |
| GC-29 | QT/PI D-N | ISSUED QT valid_until 2026-10-10, today 2026-10-03 / 살아 있는 SO 있음 / CONVERTED | D-7 알림 / 0 / 0 | 스윕과 같은 후보 |
| GC-30 | QT/PI D-N | valid_until == today | D-0 대상(당일 24:00까지 유효), 다음 날 스윕 EXPIRED | 포함 경계 |
| GC-31 | KST 경계 | 2026-10-03T14:59Z vs 15:00Z에 스캔 | today_kst 10-03 / 10-04 | GC-H5 승계 |
| GC-32 | payment_due(수입) **[적대 R-29]** | TT_DEFERRED, ORDER_DATE, days 0, PO `frozen_at` = 2026-10-03T15:30Z, PO `doc_date` = 2026-10-01 | OK **2026-10-04**(`frozen_at` KST 날짜, `doc_date` 미사용) | 수입 ORDER_DATE 원천 |
| GC-33 | loading_deadline 부분 수리 **[적대 R-06]** | 통관 2건: 수리 2027-01-31 / 미수리 | 값 2027-03-02(MIN 유지) + `customs_state=PARTIAL`(미수리 1) | 부분 수리 표시 |
| GC-34 | loading 이행 **[적대 R-10]** | 기한 2027-03-02, ETD 실적 03-01·BL 실적 03-03 / ETD만 03-01 / BL만 03-02 | MET_LATE(MAX 03-03) / MET / MET | 이행일 = MAX |
| GC-35 | 시각형 실적 **[적대 R-18]** | now = 2026-10-10T05:00Z, `actual_at` 05:00Z / 05:00:01Z | 성공 / 422 `ACTUAL_IN_FUTURE` | 여유 0 |
| GC-36 | 시각형 도과 **[적대 R-20]** | CARGO_CLOSING 2026-10-11T00:00Z `America/Los_Angeles`, 스캔 2026-10-10T21:40Z(KST 10-11 06:40) / 2026-10-11T00:00:01Z | 도과 아님(D-N 문턱은 scan_date 10-10 기준) / 도과 | UTC 시각 비교 |
| GC-37 | 통관 미래일 **[적대 R-18]** | today_kst 2026-10-03, 수리일 2026-10-04 | 422 `SHIPMENTS.CUSTOMS.DATE_IN_FUTURE`(여유 0) | 수리일 ≤ 오늘 |

추가 아키텍처 테스트(GC 외)
- `MilestoneType`↔DB CHECK 대사
- 파생형이 CHECK에 없는지
- schedule.py·holidays/calc.py가 순수한지(세션·`today_kst`·DB 임포트 0)
- 기일 스캔 모듈이 `record_transition`을 임포트하지 않는지
- 스케줄 레지스트리 ~~13~~ **14([적대 R-17])**
- 4금 집합에 `trade-deadline-scan` 포함

---

## 부록 요약 — 멈춰서 보고할 항목(DESIGN·WBS 부기와 ADR로 해소 전제)

1. **§7.5 마일스톤 9종 vs 산식의 B/L일**: BL_ISSUED를 추가하고 저장형·파생형으로 구분한다(B1). **[적대 R-03]** L/C 제시기한(문면상 "자동 계산" 항목)을 파생 마일스톤 **종류로 편입**한 것도 같은 등급의 문면 확장이라 함께 보고한다(9→11종).
2. **WBS S3-2 DoD "L/C 만기 분기"·검증 K "제시기한·tolerance" vs 입력 원천 S3-3 `lc_terms`·L/C 프로덕션 닫힘(P-10)**: 순수 함수와 단위 테스트로 충족하고, 운영은 UNKNOWN으로 둔다(B5·B6). WBS v1.6 주석 후보다.
3. **design-A:249 "휴일 보정은 S3-2" vs DESIGN §7.5 "경고"**: 경고만 한다(B12).
4. **WBS 인계 "OEM `profile_id`"**: 판정 결과 미신설, 트리거를 등재한다(B15).
5. **PAYMENT_DUE 알림 미발동**: 충족 신호가 S3-3에 있어서다(B13). 부채로 등재한다.

신규 부채 후보
- 주말 판정(B12⑤)
- 대금만기·제시기한 알림(B13)
- OEM 마일스톤 알림(B15)
- 납기(requested_delivery_date) 대비 비교 규칙(B17)
- 브리핑 수신자 확장(S2-3 브리핑은 인증 담당자만 — code:modules/deadlines/service.py:476-480)
