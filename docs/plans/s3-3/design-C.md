# S3-3 계획 설계 — 부록 C: 동시성·권한·감사·잡

- 기준: main `2092406`(S3-2 종결 — PR-8 #67). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-3 행(W:127-132)과 v1.6 주석(W:132·W:251·W:258)을 따른다. 부채 정본은 PROGRESS 'S3-2 부채 최종 목록'(P:50-171)과 S3-1 계획 등재 P-01~P-60(P:1614-1675)이다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `code:경로:줄` = `backend/app/` 아래 경로, `test:경로:줄` = `backend/tests/` 아래 경로, `fe:경로:줄` = `frontend/src/` 아래 경로, `ADR-nnnn` = `docs/adr/`. 줄 번호는 `2092406`에서 읽은 값이다.
- 판정 방식: 오너 상시 지시(2026-09-29, CLAUDE.md "결정·개입 없이 끝까지")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. PROGRESS·ADR 등재 시 "자율 확정 — 사후 번복 가능"으로 표기한다.
- 형식: 안건 C1~C16. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 / 되돌리기 비용** 순이다(S3-2 부록 C 선례 — `docs/plans/s3-2/design-C.md`).
- 신규 ADR은 통합에서 0088부터 부여한다 — 이 부록은 `ADR-C①…` 가칭을 쓴다. 마이그레이션 번호도 통합이 부여한다.
- **실행 검증 못 했음.** 정적 독해만 했다. pytest·alembic·DB·서버는 돌리지 않았다. "깨질 테스트"는 소스 단언문을 읽고 추론한 것이고, 교착 시나리오는 잠금 순서를 손으로 따라간 것이다. 각 PR 첫 커밋에서 실측해 기록한다(P-60 선례).

---

## 0. 범위와 다른 부록과의 경계

### 0-1. 이 부록이 확정하는 것
- 부록 A(서류 생성기)·부록 B(채권·입금·여신 provider·대금만기 — `docs/plans/s3-3/design-B.md`)가 정하는 업무 동작의 **트랜잭션 경계(T1~T22)**(C1)
- **LOCK_ORDER** 신규 슬롯(`lc_terms`·`trade_documents`·`receivables`)과 잠금 모드, **PI SHARE 뒤 QT·PI UPDATE 금지** 규칙, 노출 구성 변경 쓰기의 거래처 잠금 의무(C2·C3)
- 낙관 잠금(version) — **당사자 변경의 헤더 version 증가(R-3a-4 재트리거)** 포함(C4)
- 멱등(API 키 + DB 부분 유니크·복합 FK) — **P-39 키 길이 검사 해소**(C5)
- **역할 5종 매트릭스 행**·`GOVERNED_PREFIXES` 추가·본문 기반 역할 재판정 순서(C6)
- **원가·민감정보 마스킹**(수입선적 서류 금지, 렌더 다운로드 역할 축소, 렌더 주입 방어)(C7)
- **감사** 기록 위치 분담과 **IMMUTABLE/열 GRANT** 등재(C8)
- **아웃박스 이벤트** 목록과 payload 화이트리스트(C9)
- **잡 변경 — `JOB_REGISTRY` 14 유지**, `trade-deadline-scan`·`trade-docs-totals-verify` 확장의 호출 경계(C10)
- **§15 L3 자동화 4금 논증**(C11)
- **import 방향**(신규 모듈 계층 등록·documents 소유자 해석기·부트스트랩)(C12)
- **KST 자정 고정 규율**(C13)
- 에러 코드 번역 규약(C14), **H·I·J·K 시험 설계**(C15), 부채 매핑(C16)

### 0-2. 경계만 적고 넘기는 것

> **부록 문자 대응(주의)**: 부록 B는 작성 시점에 "인가·잠금 부록(D)"·"L/C 부록(C)"·"화면 부록(E)"을 가정했다(design-B 0-2). 실제 S3-3 배정은 **C = 이 부록(동시성·권한·감사·잡)**이다. 따라서 design-B의 "D 소관" 표기(LOCK_ORDER 삽입·권한 매트릭스·임포트 계층·에러코드 1:1)는 **이 부록이 확정**하고, "C 소관"으로 가정한 `lc_terms` 본체는 design-B B10 ⑥의 대체 조항("C가 이 표를 두지 않으면 B가 최소 열만 소유")에 따라 **B 소유로 자율 확정**한다(이 부록은 `lc_terms`의 TX·잠금·권한·감사만 정한다). 화면은 통합이 정하는 화면 부록 소관이다.

| 주제 | 소관 | 이 부록이 가정하는 인터페이스 |
|---|---|---|
| 서류 템플릿·렌더링(PDF·엑셀·언어 변형)·저장 전 검증(금액 정합·G.W.≥N.W.·Incoterms 완전성·CI↔PL 교차)·자사 레터헤드(P-11)·SHIPPER 자리(R-3a-5)·PL 중량/CBM(Q-05)·TO ORDER 수하인(Q-06)·documents 전표 첨부 모델(P-13) | **부록 A** | A가 CI·PL·S/I를 **발행 스냅샷 영속 행**(가칭 `trade_documents` 헤더 + `trade_document_lines`)으로 둔다고 가정한다. A가 렌더링만(영속 0)으로 정하면 T5~T7 행은 "렌더 다운로드(T3)"로 접히고 채권 발생은 B 전용 엔드포인트(T8)가 유일 경로가 된다(design-B B1 ③과 같은 분기). QT·PI 서류는 기존 동결 전표에서 렌더링만 한다(영속 0) |
| 채권 모델·미수 정의·노출 산식·COMPLETED·short-close·aging·대금만기 배선·`lc_terms` 최소 열·스캔 대상과 문턱 | **부록 B** | design-B B1~B21. 이 부록은 **대상·문턱·산식을 다시 정하지 않고** TX·잠금·권한·감사·잡 경계만 확정한다 |
| 선적 RESERVED 5상태 엣지·출고 원장·검수 미완료 CI/PL 차단 본체·`AllocationPort` | S4-2 | 서류 발행 허용 상태 = "동결 이후 살아 있는 선적"(design-B B1 ④와 같은 술어) — S4-2 개방 시 재작업 0 |
| B/L draft 업로드·AI 대조·적합 확인 자동 기록(D:215 후단·D:333) | WBS에 S3-3 배정 없음 | 이 부록은 "S3-3에서 열지 않는다"만 확인한다(C11) |
| DGD(D:215 "QT·PI·CI·PL·S/I·DGD") | S3-4(DG 게이트 — W:139) | 서류 종류 레지스트리에 DGD 값을 두지 않는다(C11 ⑥) |
| 바이어 대상 독촉·발송 | S5-4 outbound_policies(D:335) | 대외 채널 0(C9·C11) |

---

## C1. 트랜잭션 경계 — 업무 동작 = 1 DB 트랜잭션

**결정**

아래 표의 각 행이 정확히 1개의 DB 트랜잭션이다(`unit_of_work()` 합류 규약 — 바깥 UoW에 합류하므로 중첩 호출이 따로 커밋하지 않는다). **트랜잭션 안에서 외부 호출(HTTP·메일·슬랙·AI)은 0**이고, **파일 IO·렌더링(CPU)도 트랜잭션 밖**이다(documents 선례 `code:modules/documents/service.py:611-648` — "파일 IO는 트랜잭션 밖, 실패·멱등 재생이면 방금 쓴 파일을 지운다"). 잠금 열은 C2의 순서표 순서로 적는다(멱등 claim = 항상 첫 행 잠금).

| # | 업무 동작(엔드포인트 가칭) | 잠금(LOCK_ORDER 순) | 쓰기 | 이력·감사 | outbox |
|---|---|---|---|---|---|
| T1 | 레터헤드 마스터 수정 `PUT /company-profile`(P-11, A) | 멱등 → `company_profile` 단일 행 `FOR UPDATE`+version (순서표 밖 — 전표와 섞이지 않는 독립 TX) | UPDATE, version+1. 로고·서명 이미지는 documents 업로드 경로(파일 IO는 TX 밖)로 먼저 올리고 문서 id만 연결 | audit `company_profile.updated`(바뀐 필드 이름·전후 텍스트 — 사업자번호 포함 공개 정보) | 없음 |
| T2 | 서류 미리보기(QT·PI·CI·PL·S/I — 저장 0) | 읽기 세션(잠금 0) → 렌더 컨텍스트 DTO 조립 → **세션 닫은 뒤** 렌더 | 0 | 0 | 0 |
| T3 | 서류 렌더 다운로드(QT·PI = 동결 전표에서, CI·PL·S/I = 발행 스냅샷에서) | 짧은 TX: 원천 읽기(잠금 0) + audit INSERT → 커밋 → **TX 밖에서 렌더**·스트리밍 | audit 1행 | `trade_docs.document.rendered`(종류·id·형식·언어 — 내용·금액 0, C8) | 0 |
| T4 | 서류 발행 미리보기(저장 전 검증 결과만) | 읽기(잠금 0) | 0 | 0 | 0 |
| T5 | **CI 발행**(A) — CI 스냅샷 + **채권 발생 같은 TX**(design-B B1 ③) | 멱등 → partners `FOR NO KEY UPDATE`(`lock_buyer_for_credit` — 노출 구성 변경, C3) → SO `FOR UPDATE`(`lock_document` 직접 — COMPLETED 수렴, C2 ⑤) → shipments `FOR UPDATE`(동결 상태·version 재검사) → `trade_documents`(같은 선적·같은 종류 살아 있는 발행본 `FOR UPDATE` — 대체 판정) → `receivables` INSERT → lines(스냅샷 라인 INSERT) → `doc_number_seq`(채번은 TX 마지막 — D:366) | CI 헤더·라인 INSERT, receivables INSERT, (완결이면) SO IN_SHIPMENT→COMPLETED | 스냅샷 행 자체가 기록(IMMUTABLE — C8), SO 상태이력(automatic, `cause_receivable_id`), audit `receivables.receivable.opened` | `trade_docs.document.issued`·`receivables.receivable.opened`·(완결 시) 커널 SO 전이 이벤트 |
| T6 | **PL·S/I 발행**(A) | 멱등 → shipments `FOR UPDATE`(동결 재검사·CI↔PL 교차의 상대 발행본과 직렬화) → `trade_documents`(같은 선적 살아 있는 CI·PL 발행본 `FOR SHARE` — 교차 검증 대상) → lines → `doc_number_seq` | 스냅샷 INSERT | 스냅샷 행 | `trade_docs.document.issued` |
| T7 | 서류 발행본 무효화·재발행(A — 정정 = 무효 + 신규, D:352 ① 승계) | T5(CI)·T6(PL·S/I)과 같은 잠금 | 무효 행 INSERT(IMMUTABLE 무효 기록 — C8) + 신규 스냅샷 | 무효 사유 필수 | `trade_docs.document.voided`(+ 신규 `issued`) |
| T8 | 채권 발생 — CI 영속 발행본이 **없는 경우만** `POST /shipments/{id}/receivable`(design-B B1 ③) | T5와 같은 잠금에서 `trade_documents` 단계 생략(**PI 잠금 없음** — C2 ⑤) | receivables INSERT, (완결이면) SO 전이 | 위와 같음 | 위와 같음 |
| T9 | 이월 채권(OPENING) 등록 `POST /receivables/opening`(ADMIN — design-B B14) | 멱등 → partners `FOR NO KEY UPDATE` | receivables INSERT(source_kind OPENING) | audit `receivables.opening.registered`(거래처·통화·금액·만기 — `invoice_ref`·메모 원문 0) | `receivables.receivable.opened` |
| T10 | 채권 취소 `POST /receivables/{id}/cancel`(ADMIN — B15) | 멱등 → partners NKU → SO `FOR UPDATE`(COMPLETED 재검사 — `lock_document`) → shipments `FOR SHARE` → receivables 행 `FOR UPDATE`+version | status·cancel_* UPDATE(열 GRANT — C8) | audit `receivables.receivable.cancelled`(id·이전 상태 — 사유 원문은 행이 정본) | `receivables.receivable.cancelled` |
| T11 | 채권 입금 `POST /receivables/{id}/payments`(B5) | 멱등 → partners NKU → PI `FOR SHARE`(있으면 — 충당액 읽기) → SO `FOR SHARE`(`lock_document(..., read=True)` 상당 — `lock_chain` 금지, C2 ⑤) → receivables 행 `FOR UPDATE` | payments INSERT(receivable_id) | audit `payments.receipt.recorded`(기존 액션 — detail에 `receivable_id`), 이중 입력 확인 시 `payments.duplicate.acknowledged`(확인한 입금 id 목록) | `payments.payment.recorded`(payload `receivable_id`) |
| T12 | 입금 역기록 `POST /payments/{id}/reversal` — **대상 분기**(B5 ①) | 멱등 → 무잠금 peek로 대상 판별(입금 행은 불변 — 잠금 전 조회 안전, 기존 `code:modules/trade_chain/payment_flow.py:121-123`) → **분기 전 `lock_chain(PI, None)` 호출 금지** → PI 입금: partners NKU(**신규** — B5 ⑨) → `lock_chain(PI)`(QT→PI `FOR UPDATE`) / 채권 입금: T11과 같은 잠금 | payments INSERT(음수) | 기존 `payments.reversal.recorded` 계열 | 기존 `payments.payment.reversed`(+ `receivable_id`) |
| T13 | 기존 PI 입금 `POST /proforma-invoices/{id}/payments` | **무변경**(QT→PI `FOR UPDATE` — 노출을 줄이기만 하는 쓰기라 거래처 잠금 없음, design-B B9 ①) | — | — | — |
| T14 | **SO short-close** `POST /sales-orders/{id}/short-close`(B7 ③) | 멱등 → partners NKU → SO `FOR UPDATE`+version(`lock_document` — QT·PI 재잠금 불필요) → shipments(SO 소속 살아 있는 선적 id순 `FOR SHARE` — 채권화 판정) → receivables 읽기(잠금 0 — SO `FOR UPDATE`가 채권 발생 T5·T8과 직렬화) | SO short-close 3열 UPDATE + IN_SHIPMENT→COMPLETED | SO 상태이력(사유 = short-close 사유, 행위자 = 클릭자) — audit 이중 기록 0(C8) | 커널 SO 전이 이벤트 + `sales_orders.sales_order.short_closed`(id만) |
| T15 | `lc_terms` 등록·개정·취소(B10 ⑥) | 멱등 → SO `FOR UPDATE`(`lock_document`) → `lc_terms`(살아 있는 행 `FOR UPDATE`+version — 개정은 이전 행 SUPERSEDED + 신규 INSERT) | INSERT/UPDATE(status) | audit `lc_terms.lc.registered`·`.amended`·`.cancelled`(바뀐 필드 이름·전후 날짜·bp — 금액은 L/C 금액이라 비원가, 기재) | `lc_terms.lc.registered`·`.amended`(id·so_id만) |
| T16 | L/C 제시 기록(제시일·네고일·인수일 — 선적 1:0..1) | 멱등 → SO `FOR SHARE` → shipments `FOR SHARE` → shipment_children(제시 기록 행 `FOR UPDATE`+version) | INSERT/UPDATE | 정정 시 audit `lc_terms.presentation.corrected`(통관 정정 선례 `code:modules/audit/models.py:85-88`) | `lc_terms.presentation.recorded`(id만) |
| T17 | L/C 하자 체크리스트 표시(항목 체크+근거) | 멱등 → shipments `FOR SHARE` → shipment_children(체크 마크 INSERT — 직전 마크 id 대조) | IMMUTABLE 마크 INSERT(C8) | 마크 행 자체 | 없음 |
| T18 | 기능 플래그 토글 `PUT /feature-flags/{code}`(ADMIN — B11) | 멱등 → `feature_flags` 행 `FOR UPDATE`+version(행 없으면 INSERT — 부분 유니크 `unique_active(code)`가 동시 최초 INSERT를 409로) (순서표 밖 — 독립 TX) | UPDATE/INSERT | audit `platform.feature_flag.changed`(코드·전후 값) | 없음 |
| T19 | documents 전표 첨부(P-13 — A) | documents 기존 경로(파일 IO TX 밖 → 짧은 TX: 멱등 → 소유자 해석기 존재 확인(잠금 0 — 전표는 삭제 경로가 없고 취소만, `code:modules/trade_docs/locking.py:41-54` 대상 아님) → INSERT) | documents INSERT | 기존 | 기존 |
| T20 | `trade-deadline-scan` 확장 — 대금만기·제시기한·L/C 유효/선적기일·OPENING 만기(B13) | 건별 독립 TX, 잠금 0(읽기) | alerts INSERT(`notify` 코어) | — | 없음(코어 직접 — `code:modules/trade_chain/deadline_scan.py:3-5`) |
| T21 | `trade-docs-totals-verify` 확장 — 발행 스냅샷 헤더 합 = Σ 라인(C10 ③) | 읽기 전용·잠금 0 | ADMIN 알림 | — | 없음 |
| T22 | 채권·aging 목록·상세·CSV(B12) | 읽기 세션 | 0 | 0 | 0 |

- **T5의 "CI 발행 = 채권 발생 1TX"**: 청구 서류와 청구 기록이 갈라지는 중간 상태(CI는 있는데 채권 없음 → 노출 SO 항 유지·aging 누락, 또는 그 반대)를 관측할 수 없게 한다. 렌더링(PDF 바이트 생성)은 이 TX에 들어가지 않는다 — 스냅샷이 정본이고 렌더는 T3에서 결정적으로 다시 만든다(자율 확정 — 아래).
- **렌더 결과 저장 0(자율 확정)**: 발행 시 PDF를 파일로 저장하지 않는다. 저장하면 ① 파일 IO와 DB 행의 2단 커밋 문제(고아 파일·행 없는 파일) ② 렌더 엔진 버전이 바뀌면 같은 스냅샷의 두 '정본'이 생긴다. 스냅샷(IMMUTABLE) + 결정적 렌더가 단일 정본이다. 바이어에게 보낸 바로 그 파일 보존이 필요하면 사용자가 documents에 첨부한다(T19 — 파일 IO TX 밖 규약 그대로).
- **T5 거래처 잠금의 근거**: CI 발행이 채권을 만들면 노출의 항이 SO 항 → 채권 항으로 옮겨진다(design-B B6 ②). 여신 평가는 READ COMMITTED에서 **SO 항 질의와 provider 질의가 서로 다른 문장 스냅샷**이다(`code:modules/credit/evaluation.py:165-182` — 루프 뒤 provider 호출). 두 질의 사이에 채권 발생 TX가 커밋되면 같은 금액이 **양쪽에 다 보이거나(이중) 어느 쪽에도 안 보인다(공백)**. 평가는 거래처 행을 `FOR NO KEY UPDATE`로 쥔 채 실행되므로(`code:modules/credit/locking.py:32-42`), 노출 구성을 바꾸는 쓰기도 같은 행을 먼저 잡아야 평가와 직렬화된다. 이것이 DoD "노출 공백 0·이중 계산 0"(W:130)의 **동시성 쪽 증명 조건**이다(C15 CJ-07이 기계로 고정).
- **T12 분기 위치**: 현행 `reverse_payment`는 peek 직후 `lock_chain(session, KIND, peek.pi_id)`를 부른다(`code:modules/trade_chain/payment_flow.py:121-123`). 채권 입금은 `pi_id`가 NULL이라 이 호출이 `session.get(ProformaInvoice, None)`으로 흘러 의미 없는 404가 된다. **대상 판별 → 분기 → 거래처 잠금 → 대상별 잠금** 순서로 재배치한다.
- **문서 정합 메모(코드 수정 아님 — 보고만)**: `code:modules/trade_chain/payment_flow.py:6` 독스트링은 PI 잠금을 "거래처→QT→PI"라고 적지만 `lock_chain`은 거래처를 잠그지 않는다(`code:modules/trade_chain/chain_ops.py:56-77` — QT→PI만). S3-3이 역기록에 거래처 잠금을 실제로 더하면서 독스트링을 실측과 맞춘다(T12).

**근거**: D:358(17.1 "헤더+라인+원장+이력+이벤트 전부 커밋 or 전부 롤백", "트랜잭션 안에서 외부 호출 금지"), D:362 ①(여신 직렬화 = 거래처 행), D:366(채번 마지막), D:374(라인합 저장 시점 검증+야간 검산), ADR-0059·0064·0068·0078, design-B B1·B5·B7·B9.

**대안**
- (a) CI 발행과 채권 발생을 2TX(발행 커밋 후 아웃박스 핸들러가 채권 생성). **기각**: 중간 상태 관측 가능 + 핸들러 실패 시 영구 불일치 + "아웃박스 = 통지" 계약 훼손(S3-2 C1 대안 (a)와 같은 이유).
- (b) 발행 시 PDF를 같은 흐름에서 저장(파일 먼저 쓰고 행 INSERT). **기각**: 위 '렌더 결과 저장 0' 근거. 
- (c) 렌더 다운로드를 audit 없이(GET 순수 읽기). **기각(더 엄격 쪽 채택)**: 렌더 결과물은 대외 전달물이고 은행 계좌번호를 담는다(C7) — 백업 열람 매 호출 감사 선례(`code:modules/audit/models.py:51-52` `BACKUPS_VIEWED`).

**자율 확정**: 확정(렌더 결과 비저장·렌더 감사·CI=채권 1TX 포함). A가 CI를 영속 발행 전표로 두지 않으면 T5~T7은 접히고 T8이 유일 경로다(통합이 고정).

**되돌리기 비용**: 낮음~중간. 렌더 저장으로 바꾸는 것은 파일 IO 규약 추가(중간). CI·채권 분리는 불일치 정리 잡이 필요해 중간.

---

## C2. LOCK_ORDER 개정 — 신규 슬롯 3개

**결정**

`code:modules/trade_docs/locking.py:41-54` `LOCK_ORDER`를 다음으로 개정한다(굵게 = 신규).

```
idempotency_keys → order_intakes → partners → quotations → proforma_invoices
→ sales_orders → **lc_terms** → purchase_orders → shipments → shipment_children
→ **trade_documents** → **receivables** → approvals → lines → doc_number_seq
```

- ① **`lc_terms`**(SO 하위, SO 1:N — design-B B10 ⑥): SO를 잡은 뒤에만 잡는다. 채권 발생(T5·T8)의 tolerance 판정(B10 ⑦)은 `lc_terms`를 **읽기만** 하고(잠금 0), 개정(T15)은 SO `FOR UPDATE`를 먼저 잡으므로 **SO 잠금이 둘을 직렬화**한다 — 채권 발생이 개정 중간값으로 tolerance를 판정하는 창이 없다. PO보다 앞인 이유: PO와 L/C를 한 TX에서 같이 잡는 경로가 없고, SO 바로 뒤라야 "조상 → 자기" 관용이 유지된다.
- ② **`trade_documents`**(선적 후속 — ADR-05 사슬 "선적→CI/PL→C/O→채권", D:29): `shipment_children` 뒤. 발행 스냅샷 라인은 기존 `lines` 범주(id 오름차순)에 들어간다.
- ③ **`receivables`**: `trade_documents` 뒤·`approvals` 앞(design-B B9 ② 요구 "shipment_children 뒤·approvals 앞"을 CI 사슬 순서까지 반영해 확정). 채권은 CI의 후속이다.
- ④ **순서표 밖(전표와 한 TX에서 섞이지 않는 독립 잠금)**: `company_profile`(T1)·`feature_flags`(T18)·`payments`(INSERT-only — 행 잠금 대상 아님)·`alerts`(ON CONFLICT). 휴일 선례(S3-2 C2 "휴일 비편입")와 같은 논리 — 업무 TX는 이들을 MVCC로 **읽기만** 한다(`is_feature_enabled`는 SAVEPOINT 읽기 — `code:modules/trade_docs/payment_terms.py:140-152`, 레터헤드는 렌더 컨텍스트 읽기).
- ⑤ **PI SHARE 뒤 `lock_chain` 금지(신규 규칙)**: T11은 PI를 `FOR SHARE`로 잡는다. 같은 TX가 그 뒤 `lock_chain(SO)`을 부르면 `ANCESTORS[SO] = (QT, PI)`(`code:modules/trade_chain/chain_ops.py:45-55`)라 **QT `FOR UPDATE`(순서 (3)이 (4) 뒤로 — 위반)**와 **PI SHARE→UPDATE 승격**이 동시에 생긴다. 동시 PI 입금(T13: QT UPDATE → PI UPDATE)과 엮이면 "채권 쪽: PI SHARE 보유·QT 대기 / PI 입금 쪽: QT 보유·PI UPDATE 대기" 교착(40P01 → 409 LOCK_BUSY)이 정상 경로에서 난다. 따라서 **PI를 잡은 S3-3 경로의 SO 잠금은 `lock_document(SO)` 직접**이다(T10·T11·T14·T15). 채권 발생(T5·T8)은 PI를 아예 잡지 않는다 — 발생은 선적 `total_amount`만 쓰고 충당액은 파생(design-B B4)이라 PI 값에 의존하지 않는다(design-B B9 ②의 "PI FOR SHARE"는 **채권 입금에만 남기고 발생에서는 제거** — 자율 확정).
- ⑥ **거래처 잠금 모드**: S3-3 신규 경로의 거래처 잠금은 전부 `lock_buyer_for_credit`(`FOR NO KEY UPDATE`) **단일 진입점**을 부른다 — 다른 파일의 `Partner` `with_for_update`는 기존 아키텍처 스캔(`test:architecture/test_approval_contract.py:578`)이 실패시킨다. 한 TX 거래처 1행(`code:modules/credit/locking.py:11`) — OPENING 등록·채권 발생·입금·역기록·short-close·취소 모두 바이어 1명이다.
- ⑦ 담당 이관 순서(`code:modules/handover/targets.py` `ASSIGNMENT_TARGETS` — "순서가 곧 잠금 순서"): S3-3 신규 표 중 `assignee_id`를 갖는 표는 없다(채권·발행본·L/C는 SO·선적 담당을 따른다 — B13 ⑥) → 이관 대상 추가 0. 신규 users FK는 전부 `ACTOR_LOG` 분류(C8).
- ⑧ 독스트링(`code:modules/trade_docs/locking.py:1-36`)·DESIGN §17.2 부기(D:364 서식)·ADR-0078 부기를 같은 PR에서 고친다(**변경은 ADR** — D:362 ②).

**잠금 모드 요약(S3-3 신규 경로)**

| 대상 | 모드 | 사용처 |
|---|---|---|
| partners | `FOR NO KEY UPDATE`(`lock_buyer_for_credit`) | T5·T8·T9·T10·T11·T12(양 분기)·T14 — 노출 구성 변경 쓰기 전부 |
| proforma_invoices | `FOR SHARE` | T11·T12(채권 분기) — 충당액 읽기. 이후 같은 TX에서 QT·PI UPDATE 금지(⑤) |
| sales_orders | `FOR UPDATE`(`lock_document`) | T5·T8·T10·T14·T15 — COMPLETED 수렴·재검사·L/C 개정 직렬화 |
| sales_orders | `FOR SHARE` | T11·T12(채권 분기)·T16 |
| lc_terms | `FOR UPDATE`+version | T15 |
| shipments | `FOR UPDATE` | T5·T6·T7·T8(동결·version 재검사) |
| shipments | `FOR SHARE` | T10·T14(id순)·T16·T17 |
| trade_documents | `FOR UPDATE`(같은 종류 살아 있는 발행본) / `FOR SHARE`(교차 대상) | T5·T6·T7 |
| receivables | `FOR UPDATE`(행) | T10·T11·T12(채권 분기) |

**근거**: D:362 ②(순서표·부분수열 허용·위반 금지·거래처 잠금 모드·변경은 ADR), D:364(S3-2 개정본), ADR-0059·0078, `code:modules/trade_chain/chain_ops.py:45-77`, `code:modules/trade_chain/payment_flow.py:83,123`(PI 입금·역기록의 `lock_chain`).

**대안**
- (a) `receivables`를 `shipment_children`에 섞는다. **기각**: 선적 하위 행(당사자·마일스톤·통관)은 선적 잠금 아래 편집 행이고, 채권은 SO 수렴·노출의 축이라 의미가 다르다. 계측 시험이 슬롯 단위로 단언하므로 분리가 진단에 유리하다.
- (b) `lc_terms`를 순서표 밖(SO 잠금 아래 암묵). **기각**: 개정 TX가 `lc_terms` 행을 실제로 `FOR UPDATE` 잡으므로 순서표에 없으면 계측 시험이 "알 수 없는 표"로 실패하거나 공회전한다.
- (c) T11에서 PI 잠금을 빼고 충당액을 무잠금 읽기. **기각(이번엔)**: 동시 PI 입금으로 상한이 1회 낡아도 FIFO clamp가 음수 미수를 막지만(design-B B4 ③), 422 `EXCEEDS_DUE` 응답이 실측과 어긋나는 창이 생긴다. PI SHARE 1개 비용으로 응답 정확성을 산다.

**자율 확정**: 확정. **ADR-C①**(LOCK_ORDER S3-3 개정 — ADR-0078 부기 + 신규 ADR).

**되돌리기 비용**: 중간. 순서 변경은 교착 재검증이 필요하다(CJ-09 계측 시험이 비용을 줄인다).

---

## C3. 노출 구성 변경 쓰기 규칙과 경합 증명 조건

**결정**
- ① **규칙 문면**(DESIGN §17.2 부기 후보): "여신 노출의 **항을 옮기거나 늘리는** 쓰기는 거래처 행을 `lock_buyer_for_credit`로 **먼저** 잡는다. 노출을 **줄이기만** 하는 쓰기(PI 선수금 입금 T13)는 잠그지 않는다 — 평가가 낡게 읽어도 과대 방향이다." 해당 쓰기 = 채권 발생(T5·T8)·OPENING(T9)·채권 취소(T10)·채권 입금(T11 — 미수 감소지만 충당 재배분과 엮여 항 이동으로 분류)·역기록 양 분기(T12)·short-close(T14).
- ② **선적 생성·출고지시·취소는 노출 구성을 바꾸지 않는다**(선적분은 채권 전환 시점에만 차감 — design-B B6 ②) → 기존 S3-2 경로(T1~T5, S3-2 C1)는 **무변경**. 이것이 S3-2 경로에 거래처 잠금을 소급하지 않아도 되는 근거이고, 아키텍처 시험으로 고정한다: `open_order_amount`(단일 정의 — `test:architecture/test_approval_contract.py:610`)가 읽는 데이터 = SO 행 + provider의 `invoiced_by_sales_order`뿐(선적 표 무참조).
- ③ **COMPLETED 수렴의 원자성**: SO가 미결 술어(`CLOSED_STATUSES` — `code:modules/credit/exposure.py:19`)에서 빠지는 순간과 마지막 채권이 생기는 순간이 **같은 TX·같은 거래처 잠금 아래**다(T5·T8·T14). design-B B8 표 t5 행의 "술어 제외와 채권 전환이 원자적"을 잠금으로 보증한다.
- ④ **provider 호출 위치 재배치(design-B B6 ④)와의 관계**: provider를 SO 루프 앞에 두든 뒤에 두든 ①이 지켜지면 결과가 같다. 반대로 ①이 없으면 어떤 배치든 문장 스냅샷 사이 창이 남는다 — **순서 의존 금지**(design-B B9 대안 기각 근거 승계).

**근거**: D:360(잔량을 깨뜨리는 지점 = 여신 체크 — 행 잠금 직렬화), D:362 ①, `code:modules/credit/evaluation.py:165-193`, design-B B6·B8·B9.

**대안**: SERIALIZABLE 격리로 평가 TX를 올린다. **기각**: D:360 "전면 SERIALIZABLE 금지", 재시도 루프가 필요해진다.

**자율 확정**: 확정(§17.2 부기 1문단 + ADR-C① 동석). **되돌리기 비용**: 낮음(잠금 호출 1줄씩 — 제거는 쉽지만 제거하면 CJ-07이 잡는다).

---

## C4. 낙관 잠금(version)

**결정**
- **version 대조 대상**: `receivables`(취소 T10), SO(short-close T14 — 요청 본문 `version`), `lc_terms`(개정·취소 T15), 제시 기록(T16), `company_profile`(T1), `feature_flags`(T18 — `VersionMixin` 기존 `code:modules/platform/models.py:67`), 서류 발행(T5·T6 — 선적 version, 아래 ②).
- **채권 입금(T11)은 version을 대조하지 않는다**: 입금은 "동시 편집"이 아니라 누적 사실이고, 상한 직렬화는 receivables 행 `FOR UPDATE`가 한다(GC-F 패턴). 같은 금액 재전송은 멱등 키(C5)와 이중 입력 의심(design-B B5 ④)이 막는다.
- ① **자동 수렴(SO COMPLETED)**은 커널 `record_transition` 규약대로 SO version을 다룬다(새 규칙 없음).
- ② **R-3a-4 재트리거 — 당사자 변경이 선적 헤더 version을 올린다(자율 확정)**: 현행 당사자 추가·삭제는 `lock_document(Shipment)`만 잡고 version을 올리지 않는다(`code:modules/trade_chain/shipment_flow.py:1048,1086`; 부채 R-3a-4 P:85). 당사자는 계획·출고지시 상태 모두에서 편집 가능하므로(`code:modules/trade_chain/shipment_view.py:54` `PARTY_EDITABLE_STATES = RECORD_EDITABLE_STATES`) **CI 발행 뒤 수하인·통지처가 바뀔 수 있다**. 발행 스냅샷은 `source_shipment_version`(발행 시점 선적 version)을 기록하고, 당사자·헤더 FREE 변경이 version을 올리면 발행본이 **'원천 변경됨(STALE)' 배지**로 드러난다(fail-visible). CI↔PL 교차 검증(A)은 **같은 `source_shipment_version`의 발행본끼리만 일치로 인정**한다 — 버전이 다르면 422 `TRADE_DOCS.DOCUMENT.SOURCE_CHANGED`(재발행 요구). R-3a-4의 원 트리거("당사자 동시 편집 혼선 1건")가 아니라 **"서류 스냅샷 착수"라는 새 의존**으로 재트리거한다.
  - 발행을 막는 대신 배지로 두는 이유: 출고지시 뒤 포워더 교체는 실무상 정상이고(S/I 수신자가 바뀐다), 막으면 물류 업무가 정지한다. 대신 **교차 검증·발행은 최신 version 기준**이라 낡은 조합으로는 새 발행이 안 된다.
- ③ 라인 편집은 PLANNED에서만(D:192 ①) — 서류는 동결 이후에만 발행되므로 라인과 발행본의 경합은 구조적으로 없다(T5·T6의 선적 `FOR UPDATE` 동결 재검사가 마지막 방어).

**근거**: D:360(전표 헤더·주요 마스터에 version), D:364 ④ 관용(하위 변경이 헤더 version+1), P:85 R-3a-4.

**대안**: 당사자 변경을 CI 발행 후 409로 차단. **기각**: 위 이유(물류 정지). 반대로 version 미증가 유지는 **STALE 검출 불가**(조용한 불일치 — fail-open)라 기각.

**자율 확정**: 확정. R-3a-4는 **S3-3에서 해소**(PROGRESS 부채표 갱신). **깨질 기존 시험(예상)**: 당사자 추가·삭제 후 선적 version 불변을 단언하는 e2e(있다면 — PR 첫 커밋에서 grep 실측).

**되돌리기 비용**: 낮음(version 증가 1줄 + 배지 계산).

---

## C5. 멱등 — API 키·DB 부분 유니크·복합 FK

**결정**

① **`Idempotency-Key` 필수**(`IdempotencyKey` — `code:api/deps.py:94-109`): T1·T5~T12·T14~T19 전부. 같은 키 재수신은 최초 결과를 돌려주고 이력·감사·outbox도 1회만(claim·complete가 업무 TX 안 — `code:modules/idempotency/service.py:1-21`). **T3 렌더 다운로드는 GET이라 키 없음**(부작용 = audit 1행뿐 — 재시도 시 audit가 더 쌓이는 것은 "요청 사실"이라 정상).

② **P-39 해소(자율 확정 — S3-3 첫 PR)**: 헤더 길이 128 초과는 현재 검사 없이 DB `String(128)`(`code:modules/idempotency/models.py:37`) 위반 → 22001이 500으로 샐 수 있다. S3-3이 키 필수 엔드포인트를 10개 이상 더하므로 트리거("코어 소규모 수정" — P:1653)를 지금 당긴다: `get_idempotency_key`에서 `1 ≤ len ≤ 128` + 제어문자 금지 → 422 `COMMON.IDEMPOTENCY.KEY_INVALID`(신규). 프런트 `lib/api.ts`가 만드는 키(UUID 36자)는 영향 0.

③ **DB UNIQUE(부분 인덱스)·복합 FK** — "코드에서 확인 후 INSERT" 금지(D:370):

| 표 | 유니크·FK | 술어 | 위반 시 |
|---|---|---|---|
| receivables | `uq_receivables_shipment_live (shipment_id)` | `status <> 'CANCELLED'`(deleted_at은 CHECK로 항상 NULL — design-B B2) | 409 `RECEIVABLES.RECEIVABLE.ALREADY_OPEN` |
| receivables(OPENING) | **`(partner_id, invoice_ref)`** — 자율 확정 신규 | `source_kind = 'OPENING' AND status <> 'CANCELLED'` | 409 `RECEIVABLES.OPENING.DUPLICATE_REF`(이월 반입 이중 등록 차단 — 사람이 같은 송장을 두 번 넣는 사고가 가장 흔하다) |
| receivables | **복합 FK `(shipment_id, currency, gross_amount)` → `shipments(id, currency, total_amount)`**(신규 UNIQUE) — 자율 확정 권고(B 모델에 반영 요청) | MATCH SIMPLE(OPENING은 NULL로 비검사) | 23503 → 409 `RECEIVABLES.RECEIVABLE.AMOUNT_MISMATCH` — **채권 금액 = 선적 금액을 DB가 강제**하고, 채권이 살아 있는 동안 선적 `total_amount` UPDATE를 FK가 거부한다(야간 검산보다 강한 1차망 — D:374 "가능한 불변식은 CHECK") |
| payments | `target_exactly_one`·역기록 복합 FK 2벌·`(receivable_id, partner_id, received_currency)` 복합 FK(design-B B3) | — | 번역표(`CONSTRAINT_ERRORS`) 확장 — 제약 집합 ⊆ 번역표 대사 시험 |
| trade_documents | `(shipment_id, doc_kind)` 살아 있는 발행본 1개(A) | `status = 'ISSUED'`(무효 = 별도 IMMUTABLE 행 → 생존 판정은 A가 정한 술어) | 409 `TRADE_DOCS.DOCUMENT.ALREADY_ISSUED` |
| trade_documents | `doc_number` 전역 UNIQUE(채번 시) | 전역 | 발생 불가 — 발생 시 409 번역 |
| lc_terms | `(so_id)` 살아 있는 1행 | `status = 'ACTIVE'` | 409 `LC_TERMS.LC.ALREADY_ACTIVE` |
| lc_terms | **`(lc_number)` 살아 있는 1행** — 자율 확정(같은 L/C를 두 SO에 등록 차단) | `status = 'ACTIVE'` | 409 `LC_TERMS.LC.NUMBER_IN_USE` — 1 L/C : N SO 실수요 시 완화(부채) |
| 제시 기록 | `(shipment_id)` | `deleted_at IS NULL` | 409 |
| company_profile | 단일 행 — `singleton BOOLEAN NOT NULL DEFAULT true CHECK (singleton)` + UNIQUE(singleton) | — | 최초 동시 INSERT 1건만 |
| feature_flags | 기존 `unique_active(code)`(`code:modules/platform/models.py:77`) | `deleted_at IS NULL` | 동시 최초 토글 409 |
| sales_orders short-close | 3열 일관성 CHECK(셋 다 NULL 또는 셋 다 NOT NULL·사유 ≥ 2자) | — | 23514 → 422 |

- **design-B B7 ③ 정정(멈춰서 보고 → 자율 확정)**: "short-close 3열 — 한 번 쓰면 불변 CHECK"는 **CHECK로 표현할 수 없다**(CHECK는 행의 현재 값만 본다 — 이전 값과의 비교 불가). 대신 ⓐ 일관성 CHECK(위) ⓑ COMPLETED는 종결 상태(`TERMINAL_STATUSES` — 출구 엣지 0)라 short-close 이후 SO 쓰기 경로가 구조적으로 닫힘 ⓒ **대입 통로 스캔**: `short_closed_at`·`short_closed_by_id`·`short_close_reason`에 대입하는 앱 코드는 short-close 함수 1곳뿐(`test_doc_field_policy` 계열에 SYSTEM 열 단일 대입 단언 추가) — 셋으로 같은 보증을 낸다. 트리거(DB)는 쓰지 않는다(ADR-0028·0040 계보 "트리거 미채택" — `code:core/db/table_policy.py:153`).
- soft delete 뒤 같은 키 재유입 = 신규 행(D:370) — 채권·발행본은 soft delete 경로가 없고(상태 취소·무효 행), `lc_terms`·제시 기록만 해당.
- 제약명 → 에러 코드 번역은 IntegrityError 번역 계층(제약명 매핑)에 등록한다. 미등록 제약 위반이 500으로 새지 않는지 CJ-11이 고정한다.

**근거**: D:370, D:372 ①(취소 행이 키를 점유하지 않게 `status <> 'CANCELLED'` 술어), D:374, design-B B2·B3, P-39(P:1653).

**대안**: OPENING 중복을 이중 입력 의심(경고·확인)으로만. **기각**: 이월 반입은 ADMIN 1회성 작업이라 확인 피로가 없고, 같은 `invoice_ref`는 사실상 같은 채권이다 — 막는 쪽이 엄격하다.

**자율 확정**: 확정. 복합 FK `gross_amount`는 B 모델 권고 — B가 수용하지 않으면 T21(야간 검산)에 "채권 금액 = 선적 금액" 대사를 넣어 이중망을 유지한다(통합 확정).

**되돌리기 비용**: 낮음(인덱스·FK 교체 마이그레이션). P-39 검사는 상수 1개.

---

## C6. 역할·소유권 매트릭스와 GOVERNED_PREFIXES

**결정**

역할 축 A(ADMIN — `_role_guard` 상시 통과, `code:api/deps.py:62-71`)·T(TRADE)·L(LOGISTICS)·C(CERT)·V(VIEWER). 아래 행은 `test:architecture/authz_matrix.py` `EXPECTED`(`:55-`)에 그대로 들어간다. **경로 단위 역할**(S3-2 통합 X-15 선례 — 하나의 경로에 본문 값별 역할을 두지 않는다)을 원칙으로 한다.

| 엔드포인트(가칭 — A·B가 경로명 확정) | A | T | L | C | V | 비고 |
|---|---|---|---|---|---|---|
| `GET/PUT /company-profile` | ✓ | ✗ | ✗ | ✗ | ✗ | 레터헤드 설정은 관리자 화면 — 렌더는 서버가 읽는다(자율 확정: 조회도 A 전용, 화면 수요 시 확대) |
| `GET /quotations/{id}/document`·`/proforma-invoices/{id}/document`(렌더 — `format`·`lang` 쿼리) | ✓ | ✓ | ✗ | ✗ | ✗ | QT·PI 대외 전달물 = 무역. 상세 GET(전 역할)과 별개 채널(C7) |
| `POST …/document/preview`(서류 미리보기 T2·T4) | ✓ | ✓ | (CI ✗ / PL·S/I ✓) | ✗ | ✗ | 종류별 경로로 나눈다(아래 행과 같은 역할) |
| `POST /shipments/{id}/commercial-invoice`(CI 발행 = 채권 발생 T5)·`…/commercial-invoice/{doc_id}/void` | ✓ | ✓ | ✗ | ✗ | ✗ | 청구 기록 = 상업 사실(ADR-0079 ② "잔량·상태 = 무역" 계보) |
| `POST /shipments/{id}/packing-list`·`/shipping-instruction`(+`/void`) | ✓ | ✓ | ✓ | ✗ | ✗ | 금액 없는 물류 서류 — 물류 첫 쓰기 범위(ADR-0079 ③) 안 |
| `GET /shipments/{id}/trade-documents`(발행본 목록·메타) | ✓ | ✓ | ✓ | ✓ | ✓ | 전표 공유 자산(D:390 부기 ③ — 당사자성 비적용) |
| `GET /shipments/{id}/trade-documents/{doc_id}/file`(렌더 T3) | ✓ | ✓ | ✓ | ✗ | ✗ | 대외 전달물(은행정보 포함 — C7) |
| `POST /shipments/{id}/receivable`(T8) | ✓ | ✓ | ✗ | ✗ | ✗ | design-B B18 |
| `POST /receivables/opening`(T9)·`/receivables/{id}/cancel`(T10) | ✓ | ✗ | ✗ | ✗ | ✗ | design-B B14·B15 — 관리자 전용 |
| `POST /receivables/{id}/payments`(T11)·`POST /payments/{id}/reversal`(T12 — 기존 행 유지) | ✓ | ✓ | ✗ | ✗ | ✗ | 기존 PI 입금 행과 같은 역할(`test:architecture/authz_matrix.py:151-164`) |
| `GET /receivables`·`/{id}`·`/aging`·`/{id}/payments`·`export.csv` | ✓ | ✓ | ✓ | ✓ | ✓ | 판매금액(비원가 — D:227 ⑥) |
| `POST /sales-orders/{id}/short-close`(T14) | ✓ | ✓ | ✗ | ✗ | ✗ | SO 접두어 아래 행 추가 |
| `POST/PATCH /sales-orders/{id}/lc-terms…`(T15)·`GET` | 쓰기 ✓·조회 ✓ | 쓰기 ✓·조회 ✓ | 조회만 | 조회만 | 조회만 | L/C = 무역·금융 실무 |
| `PUT /shipments/{id}/lc-presentation`(T16)·`POST /shipments/{id}/lc-checklist/marks`(T17) | ✓ | ✓ | ✗ | ✗ | ✗ | 자율 확정: 제시·하자 판단은 무역(물류 미배정 — 열 때는 행 수정 1개) |
| `GET/PUT /feature-flags[/{code}]`(T18) | ✓ | ✗ | ✗ | ✗ | ✗ | design-B B11 |
| `POST /documents`(owner_type = 전표 종류 — T19) | ✓ | ✓ | ✗ | ✗ | ✗ | **owner_type별 역할**(아래 ②) — 기존 SKU·LABEL·CERTIFICATION·COMM_LOG 소유는 기존 `CAN_MANAGE=(TRADE, CERT)`(`code:modules/documents/router.py:32`) 유지 |

- ① **`GOVERNED_PREFIXES` 추가**(`test:architecture/authz_matrix.py:25-53`): `/api/v1/receivables`·`/api/v1/feature-flags`·`/api/v1/company-profile`·**`/api/v1/documents`**(자율 확정 — 아래). SO·선적·QT·PI 하위 경로는 기존 접두어가 통제(행만 추가). 등재 누락 = 매트릭스 공회전 → **계획 DoD 항목**이고, 접두어 제거 변이가 완전성 단언을 실패시키는지 확인한다(S3-2 PR-4c 선례 — ADR-0079 부기).
  - `/api/v1/documents`를 이번에 통제 접두어로 올리는 이유: owner_type 확폭(P-13)이 **같은 경로의 권한 의미를 바꾼다**(무역 전표 첨부). 지금까지 통제 밖이었으므로(`:25-53`에 없음) 기존 documents 행 전부(목록·상세·다운로드·CSV·삭제·문서 종류·품목군 세트)를 실측 역할 그대로 등재한다 — 행 수가 늘지만 조용한 공백이 없어진다.
- ② **본문 기반 역할 재판정 순서(documents T19)**: `owner_type`이 multipart 폼 필드라 라우터 가드(`require_roles(TRADE, CERT)`)만으로는 CERT의 SO 첨부를 막을 수 없다. **폼 파싱 직후·소유자 존재 조회 전**에 "owner_type ∈ 전표 종류 ⇒ 행위자 ∈ {A, T}"를 판정해 403(401→403→404→409→422 — ADR-0079 ⑧). 소유자 없음은 그 뒤 404. 이 재판정은 documents 서비스의 **단일 함수**(`require_owner_role(owner_type, actor)`)에 두고 매트릭스는 owner_type별 2행 프로브로 적는다(S3-2 C6 전이 `to`별 프로브 선례).
- ③ **소유권 3축**(D:392 부기)
  1. 역할 스코프: 위 표(403).
  2. 부모-자식 소속: 경로의 `doc_id`가 경로 `shipment_id` 소속이 아니면 **404·부작용 0**, `/receivables/{id}/payments`의 입금이 그 채권 소속이 아니면 404, 역기록 경로의 입금 id가 채권·PI 어느 쪽이든 존재하지 않으면 404. 제시 기록·체크 마크의 선적이 L/C SO 소속이 아니면 422 `LC_TERMS.LC.NOT_LC_SHIPMENT`(본문 불일치 = 422 — S3-2 통합 X-12 선례).
  3. 당사자성: 채권·발행본·L/C는 회사 공유 자산 — 비적용(담당자는 라우팅 단위).
- ④ 신규 쓰기 스키마 전부 `extra="forbid"`(`test:architecture/test_write_schema_forbid.py:114` — 레거시 목록 단조 감소). CI 발행 본문에 **금액·통화·환율·단가 필드가 구조적으로 없다**(선적·SO 스냅샷에서만 — 사람이 금액을 쳐서 청구하는 경로 0).
- ⑤ 응답 `allowed_actions`(화면 버튼 판정)는 위 표와 같은 상수에서 만든다 — 프런트가 역할을 따로 판정하지 않는다(R-4b-5 부채의 반복 방지).

**근거**: D:37(역할 5종), D:390·D:392 부기(IDOR 3축), ADR-0067 ①·0079, `test:architecture/authz_matrix.py:25-164`, design-B B18.

**대안**
- (a) CI 발행 = T+L(서류는 물류도 만든다). **기각**: CI 발행이 채권을 만드는 1TX라(T5) 물류가 청구 기록·SO 종결을 일으킨다. 좁혀 두고 필요 시 행 1개로 연다.
- (b) 렌더 다운로드 전 역할(상세 GET과 같게). **기각**: C7 근거.
- (c) documents를 통제 접두어 밖에 둔 채 owner_type 행만 서비스 검사. **기각**: 매트릭스가 모르는 권한 분기는 회귀 시험이 없다.

**자율 확정**: 확정. **ADR-C②**(S3-3 권한 — 채권·서류·L/C·플래그·documents 통제 편입).

**되돌리기 비용**: 낮음(매트릭스 행·라우터 상수). 여는 쪽만 싸다(닫으면 그 사이 사용자의 업무 경로가 막힌다).

---

## C7. 원가·민감정보 마스킹

**결정**
- ① **원가 채널 신설 0**: 서류 렌더 컨텍스트 조립 모듈은 `purchase_orders`·`catalog.pricing`의 원가 함수·`*_cost` 열을 **임포트·참조하지 않는다**(아키텍처 스캔 — `test:architecture/test_po_cost_masking.py:306-339` 누설 스캐너 패턴 확장). QT·PI·CI·PL·S/I의 금액은 전부 판매가 축(SO·선적 스냅샷)이다.
- ② **수입선적 서류 발행·채권 = 422**(자율 확정): 수입선적은 금액 축이 없고(`import_has_no_amount` — `code:modules/shipments/models.py:118-120`), 수입 CI를 우리가 만들려면 PO 단가가 필요하다 = ADR-0024 **10번째 채널**. 수입선적에 대한 CI·PL·S/I 발행 경로를 열지 않는다(422 `TRADE_DOCS.DOCUMENT.NOT_EXPORT` — 채권 쪽은 design-B B1 ④ `NOT_EXPORT`와 같은 결론). 수입 서류는 공급사 발행본을 documents에 첨부(P-13 경로)한다.
- ③ **은행 계좌번호**: PI는 은행정보 6열을 스냅샷으로 갖고(`code:modules/proforma_invoices/models.py:7,84-91`), PI 상세 응답이 전 역할에게 `account_no`를 싣는다(`code:modules/proforma_invoices/service.py:144-153`) — 반면 은행 계좌 마스터 조회는 A·T만이다(`code:modules/bank_accounts/router.py:25-26`). **기존 불일치는 이 부록이 고치지 않는다**(PI 모듈 소관 — 부채 후보 C-D1). 새 채널인 **렌더 다운로드는 A·T(·L: CI·PL·S/I)로 좁히고**(C6), 로그·audit·outbox에는 계좌번호를 싣지 않는다(redaction 접미 `_account_no` — `code:core/logging/redaction.py:92-94`).
- ④ **렌더 주입 방어(자율 확정 — 신규 공격면)**: 품명·비고·당사자 주소 등 사람이 입력한 텍스트가 템플릿에 들어간다. ⓐ HTML·템플릿 엔진은 **자동 이스케이프 + 샌드박스**(템플릿 문법이 데이터로 평가되지 않음 — `{{ 7*7 }}`이 그대로 찍힘) ⓑ 엑셀 셀은 `=`·`+`·`-`·`@`·탭·CR 시작 값 이스케이프(CSV 수식 이스케이프 선례 D:279 — S3-2 선적 CSV G 시험) ⓒ PDF 엔진의 외부 리소스 로드(원격 URL 이미지·폰트) **차단** — 로고·서명은 documents 저장소 경로만(파일 보안 4항 `code:modules/documents/service.py:1-14`). 원격 로드는 "트랜잭션 밖 외부 호출"이자 SSRF 경로다.
- ⑤ **outbox payload 금액 0**(`code:modules/outbox/service.py:31-32`): 채권·발행본·L/C 이벤트는 id·종류·상태·문서번호만(C9). audit의 금액 기재는 판매·여신·입금 금액에 한해 기존 선례대로 허용(입금 audit `code:modules/payments/service.py:185-197`) — 원가·마진·계좌번호·참조 텍스트·사유 원문은 0.
- ⑥ **CSV**(`/receivables/export.csv`·aging): 전 역할 같은 헤더(원가 열 없음), UTF-8 BOM·수식 이스케이프(WBS 검증 B "한글 CSV" — W:131).

**근거**: D:37(원가·마진 API 응답 레벨 마스킹), D:390(업로드·다운로드 검증), ADR-0024(9채널 봉쇄), `code:modules/catalog/pricing.py:45-51`(`COST_VISIBLE_ROLES`), `code:modules/purchase_orders/service.py:94-96`.

**대안**: 수입 CI 생성(PO 단가 조인 + CostHidden 이원화). **기각**: S3-3에 수입 서류 생성 수요가 문면에 없다(W:129는 수출 서류 흐름). 채널을 늘리지 않는 쪽이 엄격하다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(경로 추가는 ADR 동반 시 언제든).

---

## C8. 감사·불변 — 기록 위치 분담

**결정**

| 사건 | 기록 위치 | 이유 |
|---|---|---|
| 서류 발행·무효(CI·PL·S/I) | **발행 스냅샷 헤더·라인 + 무효 행 = IMMUTABLE**(권고 — A가 수용, `IMMUTABLE_TABLES` 등재 + `revoke_mutations` — `code:core/db/table_policy.py:20-48,220`) | 대외 전달물의 정본. 정정은 무효 행 + 신규 발행(gate_overrides REVOKE 행 선례 `:37-40`). audit 이중 기록 0 |
| 서류 렌더 다운로드 | audit `trade_docs.document.rendered`(T3) | 대외 전달물 반출 기록(C1 대안 (c)) |
| 채권 발생·취소·OPENING | audit 1행씩(design-B B2) + receivables 행 | receivables는 상태 열이 바뀌는 MUTABLE 표 → **`COLUMN_UPDATE_ALLOWLIST["receivables"] = {status, cancel_reason, cancelled_at, cancelled_by_id, version, updated_at}`**(`code:core/db/table_policy.py:189` 등재 + 마이그레이션 `restrict_update_columns` `:239-257`) — 금액·통화·환율·날짜·소속 열 UPDATE = 42501. DELETE·TRUNCATE revoke |
| 채권 입금·역기록 | payments(IMMUTABLE 기존 — `:36`) + 기존 audit 액션(detail에 `receivable_id`) | INSERT-only 원장 유지 |
| 이중 입력 의심 확인(P-14) | audit `payments.duplicate.acknowledged`(확인한 입금 id 목록) | 사람 확인의 증적 — 막힌 시도·예외 통과는 남아야 한다(`APPROVAL_BYPASS_BLOCKED` 계보) |
| SO COMPLETED(채권 발생·short-close) | SO 상태이력(IMMUTABLE — 기존 `:31`), 사유·행위자·payload `cause_receivable_id` | 상태 변경 이력 지위(D:376) — audit 이중 0 |
| SO short-close 결정 사실 | SO 3열(일관성 CHECK + 단일 대입 통로 — C5) | 사람 결정 기록 |
| `lc_terms` 등록·개정·취소 | audit `lc_terms.lc.*` + 행(개정 = SUPERSEDED + 신규 행) | 이전 조건이 행으로 남고, 상태 변경이 audit로 추적 |
| L/C 제시 기록 정정 | audit `lc_terms.presentation.corrected`(바뀐 열·전후 날짜) | 제시·네고·인수일은 대금만기·제시 충족의 단일 원천 — 통관 정정 선례(`code:modules/audit/models.py:85-88`) |
| 하자 체크리스트 마크 | **IMMUTABLE 마크 행**(현재값 = 항목별 최신 마크 — 자율 확정) | 근거 기록은 덮어쓰지 않는다(사후 분쟁 증적). 직전 마크 id 대조로 동시 표시 409 |
| 레터헤드 수정 | audit `company_profile.updated` | 대외 서류의 발신 정보 변경 = 정책 변경 성격(정책 저장소 선례 `POLICY_UPDATED` `code:modules/audit/models.py:60`) |
| 기능 플래그 토글 | audit `platform.feature_flag.changed` | design-B B11 ② |

- `AuditAction` 상수에 신규 코드를 모은다(CHECK 없음 — `code:modules/audit/models.py:28-33` 설계 의도). 감사 기록은 업무 TX 안(업무 롤백이면 감사도 롤백 — `audit.record`가 현재 세션에 추가).
- 신규 users FK(receivables `created_by_id`·`cancelled_by_id`, SO `short_closed_by_id`, 발행본 `issued_by_id`·무효 `voided_by_id`, `lc_terms`·제시 기록·체크 마크 행위자)는 전부 `USER_FK_CLASSIFICATION`(`code:modules/handover/targets.py:77`)에 **`ACTOR_LOG`**. 이관 대상(ASSIGNMENT) 추가 0(C2 ⑦).
- 신규 표는 전부 `IMMUTABLE_TABLES` 또는 `MUTABLE_TABLES`에 분류(`classified_tables` — `code:core/db/table_policy.py:216`), DESIGN §17.5 부기(D:380 서식 — "S3-3은 IMMUTABLE 대상을 n테이블 더해 …") + ADR 세트(D:376 "상태 변경 이력 성격 — 신설 세션 등재").

**근거**: D:54(audit_log·상태 변경 이력), D:374·D:376·D:378·D:380, D:390(권한변경·민감 동작 감사), `code:core/db/table_policy.py`.

**대안**: 발행 스냅샷 MUTABLE + 재발행 시 UPDATE. **기각**: 바이어에게 보낸 서류와 시스템 기록이 갈릴 수 있다(정본 소실). 체크 마크 MUTABLE + audit. **기각**: 근거 텍스트 원문이 audit에 들어가야 이력이 되는데, audit에는 원문을 싣지 않는 규약(입금 참조·사유 선례)과 충돌한다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음~중간(IMMUTABLE 해제는 권한 마이그레이션).

---

## C9. 아웃박스 이벤트

**결정**

| 이벤트 | 발생 동작 | payload(화이트리스트) |
|---|---|---|
| `trade_docs.document.issued`·`.voided` | T5·T6·T7 | document_id, shipment_id, doc_kind, doc_number, voided_document_id(무효 시) |
| `receivables.receivable.opened`·`.cancelled` | T5·T8·T9·T10 | receivable_id, source_kind, shipment_id, so_id, partner_id |
| `payments.payment.recorded`·`.reversed`(기존 이름) | T11·T12 | payment_id, kind, **pi_id 또는 receivable_id**(정확히 하나 — 기존 payload `code:modules/payments/service.py:199-205`에 키 가산) |
| `sales_orders.sales_order.status_changed`(커널 — `code:modules/trade_docs/transition.py:216`) | T5·T8·T14의 COMPLETED | 커널 `PAYLOAD_KEYS` + **`cause_receivable_id`**(신규 — `code:modules/trade_docs/transition.py:47-62` 화이트리스트에 가산, 자동 전이에만) |
| `sales_orders.sales_order.short_closed` | T14 | so_id, doc_number |
| `lc_terms.lc.registered`·`.amended`·`.cancelled` | T15 | lc_terms_id, so_id, superseded_id |
| `lc_terms.presentation.recorded` | T16 | shipment_id, presentation_id |

- 이름 형식 `<도메인>.<대상>.<사건>` 60자 이내(`code:modules/outbox/models.py:80-82`). **금액·원가·계좌번호·참조 텍스트·사유 원문 0**(C7 ⑤).
- **기본 `alert_rules` 시드 0**(시드 금지 D:340 ② 계보·§15 부기) — 규칙이 없으면 디스패처는 발송 처리만 하고 알림 0(`Routing.EVENT` — S3-2 C10 선례). 대외 채널 0(인앱뿐 — S5-4 소관).
- **렌더 다운로드·체크 마크·플래그 토글·레터헤드 수정은 이벤트 0**(구독자 없음 — 감사로 충분). 이벤트는 가산은 싸고 제거는 구독자 확인이 필요하므로 **소비자 없는 이벤트를 미리 만들지 않는다**.
- 기일 알림(대금만기·제시기한)은 아웃박스를 거치지 않는다(스캔이 `notify` 코어 직접 — `code:modules/trade_chain/deadline_scan.py:4-5`, `test:architecture/test_notification_core.py`).

**근거**: D:358, D:384(디스패처 클레임=처리=표식 1TX), ADR-07, `code:modules/outbox/service.py:18-41`.

**대안**: COMPLETED를 별도 이벤트(`sales_order.completed`)로. **기각**: SO 소비자는 커널 전이 이벤트를 구독한다 — 이원화 금지(S3-2 C10 대안 기각 승계).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## C10. 잡 — JOB_REGISTRY 14 유지, 기존 잡 2개 확장

**결정**
- ① **신규 잡 0 — 총수 14 유지**(`code:modules/platform/scheduler.py:212-320`, 핀 `test:architecture/test_scheduler_registry.py:186-201` 무변경). 대금만기·제시기한·L/C 기일·OPENING 만기는 **`trade-deadline-scan`(daily@06:40) 같은 잡·같은 판정 함수**(design-B B13 ①)이고, 서류 스냅샷 검산은 **`trade-docs-totals-verify`(daily@05:30)** 확장이다. 렌더 결과를 저장하지 않으므로(C1) 파일 정리 잡도 없다. aging은 읽기 시점 계산(design-B B12)이라 집계 잡도 없다.
- ② **`trade-deadline-scan` 확장의 호출 경계(이 부록 확정분)**
  1. **후보 질의 확장(실측 결함 예방)**: 현행 후보는 "저장형 대상 마일스톤에 계획 있음·실적 없음 **또는** 수리된 수출 통관"뿐이다(`code:modules/trade_chain/deadline_scan.py:525-555`). ETD 실적까지 들어가 열린 저장형 행이 없는 동결 수출 선적은 **후보에서 빠져** 대금만기(파생 — ETD·INVOICE_DATE 앵커)가 조용히 지나간다. 후보에 **"동결 이후 살아 있는 수출 선적 중 살아 있는 채권의 미수 > 0 이거나 채권이 없는 것"**과 **"살아 있는 `lc_terms`를 가진 SO의 살아 있는 선적"**을 OR로 더한다(상위 집합 — 최종 판정은 건별 TX의 `milestone_view.assemble`, 판정 이원화 금지 `:13-14`). OPENING 채권은 별도 후보 페이지(entity `receivables`).
  2. **허용 임포트 확장**(`test:architecture/test_no_auto_confirm_code_path_exists.py:1029-1047` `_SCAN_ALLOWED_APP_MODULES`): `app.modules.receivables.models`, **`app.modules.receivables.outstanding`**(미수 단일 정의 `outstanding_for`를 **쓰기 함수가 없는 서브모듈**에 둔다 — design-B B4 ①의 `receivables/service.py`에 두면 서브모듈 허용이 쓰기 함수까지 열린다: 자율 확정), `app.modules.payments.models`, `app.modules.sales_orders.models`(SO 담당자 수신 — B13 ⑥), `lc_terms` 모델 모듈. `_SCAN_ALLOWED_CALLS`(`:1051-1066`)에 새 호출명을 사람이 보고 추가하고, `_SCAN_FORBIDDEN_CALLS`(`:1068-1073`)는 무변경(쓰기·전이·잠금·채번·publish 0 유지).
  3. **발송 직전 재확인**(`:32-35` 규약 승계): 같은 건별 TX에서 미수·제시 기록을 다시 읽어 그 사이 입금·제시가 커밋됐으면 `deferred`(잠금 없음).
  4. **수신자**: 대금만기·제시기한·L/C 기일은 **SO 담당자**(→ 규칙 → ADMIN 폴백 `Routing.DEADLINE`)이고 선적 담당자가 아니다(design-B B13 ⑥). dedup 키 종류는 B13 ⑥ 그대로 — 에스컬레이션 근거(현재 수신자·24h·`created_before` — `:21-23`)도 그대로.
  5. **K 출구 계약**: 스캔이 내는 entity_type = {shipments, quotations, proforma_invoices, **receivables**} ⊆ `fe:lib/alert-routes.ts`(`:3-14`) 이동 표 — OPENING 알림이 이동처 없는 알림이 되지 않게(S3-2 PR-8 워크스루 K 선례 P:20).
  6. 1건 실패 → 잡 FAILED → 관리자 알림(`_fail_if_any_failed` — `code:modules/platform/scheduler.py:140-150,191-192`) 무변경.
- ③ **`trade-docs-totals-verify` 확장**: 검산은 테이블 이름 기반이라(`code:modules/trade_docs/verify.py:1-8` — "테이블을 만들면 자동 편입") 발행 스냅샷이 `DOC_TABLES`·`LINE_TABLES` 규약을 따르면 자동 편입된다. 따르지 않으면(A가 DocKind로 두지 않으면) verify에 **테이블 이름 기반 1항목**을 더한다 — `trade_docs`(L0)가 A 모듈을 **임포트하지 않고**(C12) 메타데이터 이름으로 읽는다. 불일치 = ADMIN 인앱 알림, 자동 보정 0(`:4`).
- ④ **시각·순서 의존**: 시각 변경 0. 기존 순서 그대로 — 05:30 검산 → 06:10 만료 스윕 → 06:30 인증·문서 기일 → **06:40 무역 기일(대금만기 포함)** → 09:00 브리핑(생성 알림이 브리핑 미확인 집계에 들어간다). 06:40 스캔은 그날 새벽 입금이 없다는 가정을 하지 않는다 — 재확인(②-3)이 경합을 처리한다.
- ⑤ **CLI**: 새 서브커맨드 0(기존 `trade-deadline-scan` CLI가 확장분까지 실행). **G-09(L/C CLI 소단위 — P:1087)는 구현하지 않고 종결**(플래그 토글은 화면·API — design-B B11 ④와 같은 결론).
- ⑥ **같은 PR에서 고칠 곳**: `test_registered_jobs_stay_clear_of_the_four_bans`(`test:architecture/test_scheduler_registry.py:149-183`) 집합은 무변경이되 `trade-deadline-scan` 주석을 "채권·입금·L/C를 **읽기만** — 미수 0 판정·제시 기록 확인, 쓰기 0"으로 갱신 / ADR-0084 부기(대상 확장) / `docs/runbook/prod.md` 잡 표 14행의 해당 행 문구 / DESIGN §15 부기(S3-3 — "레지스트리 14행 유지, 기일 스캔 대상 확장").

**근거**: D:336·D:338(코드 고정 폐쇄 열거·시드 금지·실패 알림), D:342 ②(14행), D:374(검산 이중망), D:382·D:384(실패·재시도), ADR-0084, design-B B13.

**대안**
- (a) 대금만기 전용 잡 `receivable-due-scan`. **기각**: 판정 함수 이원화(화면 D-N ≠ 알림 D-N)·시각 슬롯·4금 논증만 늘어난다(design-B B13 대안 (b)와 같은 결론).
- (b) 후보 질의 확장 없이 판정만 추가. **기각**: 위 ②-1 실측 — 실적이 다 들어간 선적의 대금만기가 무알림(fail-open).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(후보 OR 절·허용 목록 상수).

---

## C11. §15 L3 자동화 4금 논증 — S3-3 동작 대조

**결정**

§15 L3 금지 4영역(D:329: 지출·발주 확정 / 법적 판정(HS·원산지·요건) / 대외 최초 발송·협상·클레임 / 장부 확정(분개·마감·전표·원장))과 S3-3 동작을 대조한다. "전표"는 회계 전표다(ADR-0058 — P:1612 ①).

| 금지 | S3-3 위험 지점 | 판정과 기계 고정 |
|---|---|---|
| 장부 확정 | 채권(청구 사실 기록)·입금 원장 | 채권은 회계 원장이 아니지만(design-B B2) **자동 생성 0** — 사람 1클릭(CI 발행 T5 또는 채권 발생 T8)만. 마일스톤 실적·스캔·선적 상태 변화·아웃박스 핸들러가 채권을 만들지 않는다. 입금 자동 매칭 0(은행 CSV = P7, D:225). 대손·분개 0(P6) |
| 발주 확정 | — | PO 무접촉(서류 생성은 수출만 — C7 ②). PO 자동 엣지 0 유지 |
| 법적 판정 | L/C 하자 판단·tolerance·Incoterms 완전성 | ⓐ **하자 체크리스트 = 사람 체크 + 근거**(T17). 시스템은 "하자 없음/적합" 판정 필드·상태를 산출하지 않는다 — 응답은 "체크 n/m"뿐(§21 "규제·판정은 시스템이 단정하지 않는다" D:459). ⓑ tolerance 초과 422(design-B B10 ⑦)는 **산술 차단**(초과 청구 금지)이지 대외 판정이 아니다 — 하한 미달은 경고만. ⓒ 저장 전 검증(금액 정합·G.W.≥N.W.·CI↔PL 교차·Incoterms 필드 완전성 — D:215)은 데이터 정합 검사이고 HS·원산지·요건 판정을 하지 않는다 |
| 대외 발송 | 서류 생성·대금만기 알림 | **메일·팩스·포털 전송 기능 0** — 렌더 다운로드는 사람이 받아 사람이 보낸다(L2). 대금만기·제시기한 알림 = 인앱·내부 수신자(SO 담당 → ADMIN). **바이어 독촉 0**(S5-4 outbound_policies D:335). S3-3 신규 모듈의 `smtplib`·`httpx`·`requests`·`urllib` 임포트 0(CI-07) |
| **자동 확정 부재** | SO COMPLETED 자동 엣지(design-B B7) | 아래 |

- **SO COMPLETED 자동 엣지 1개(IN_SHIPMENT→COMPLETED) 개방의 4금 논증**: 도착 상태 COMPLETED는 **이행 완료의 반영**이지 약정 진입(확정)이 아니다. 발동 원인은 사람 1클릭 동작(CI 발행·채권 발생·short-close)의 **같은 TX**뿐이고 스케줄러·CLI·스캔 경로는 0이다. RECEIVED→CONFIRMED(확정)는 여전히 사람 전용이고 자동 엣지 총 SO 3개(CONFIRMED↔IN_SHIPMENT 2 + IN_SHIPMENT→COMPLETED 1)가 모두 "CONFIRMED에 RECEIVED에서 도착하지 않는다". DESIGN §15 S3-2 부기 ①(D:342 — "SO 자동 엣지 2")을 **"3"으로 개정**하는 부기 + ADR(ADR-0076 **대체** — design-B B7).
- **`test_no_auto_confirm_code_path_exists.py` REGISTRY 엔트리 추가**(엔트리 방식 `test:architecture/test_no_auto_confirm_code_path_exists.py:25-41`)

| 보호 함수(가칭) | 허용 호출처 | 금지 경로 |
|---|---|---|
| `open_receivable_for_shipment`(채권 발생 단일 착지) | CI 발행 flow + 채권 발생 flow + 라우터, actor 필수 | platform·scheduler·cli·deadlines·notifications·outbox·imports·handover·order_board·order_intake·confirm·milestone_flow·customs_flow·`trade_chain.deadline_scan` |
| `converge_sales_order_completion` | 위 2 flow + short-close flow(design-B B7 ② "호출처 2곳"을 CI 발행 경로 포함 **3 파일**로 확정 — CI 발행이 채권 발생을 부르므로) | 위와 같음 + 선적 수렴(`converge_sales_order_shipping`) 파일 |
| `short_close_sales_order` | 라우터 1곳, actor 필수 | 위와 같음 |
| `issue_trade_document`(CI·PL·S/I 발행) | 라우터, actor 필수 | 위와 같음 — **발행 자동화(선적 출고지시 시 자동 발행 등) 0** |
| `register_opening_receivable` | 라우터(ADMIN), actor 필수 | CLI 포함 금지(일괄 반입 미개방 — design-B B14 ④) |
| `set_feature_flag` | 라우터(ADMIN), actor 필수 | migrations·seeds·CLI(시드 금지 — `code:modules/platform/service.py:93-99` "함정 ⑩") |

  - `record_transition` 허용 호출처(`:65-96`)·`record_birth`(`:97-114`) 목록에 신규 flow 파일을 더한다(추가하지 않으면 실패 = 설계된 안전망).
  - `test_scheduler_and_cli_reach_only_the_totals_check_and_the_expiry_sweep`(`:977-997`)는 **무변경 통과가 정답**: 스케줄러·CLI의 trade_chain 임포트 = {expiry_sweep, deadline_scan} 그대로, 채권 함수는 언급 0. 스캔을 이 시험 밖 새 모듈에 두어 우회하지 않는다(S3-2 C11 ③ 승계).
- ⑥ **열지 않는 것(명시)**: B/L draft 업로드·AI 대조·"불일치 0 자동 적합 기록"(D:215·D:333 — WBS S3-3 산출물에 없음, 열면 §15 조건부 자동 기록 논증이 별도로 필요), DGD(S3-4), 서류 자동 발송, 입금 자동 매칭, 채권 자동 생성, 이월 채권 CSV 일괄 반입.

**근거**: D:329·D:332·D:333·D:335·D:340 ②·D:342 ①, D:459, ADR-0058·0076·0084, design-B B7 ⑦·B13 ⑧.

**대안**: COMPLETED를 사람 엣지(전용 "종결" 버튼)로. **기각**: design-B B7 대안 (a) — 같은 쌍 이중 분류·완결 누락 시 SO가 노출에 영구 잔존하는 대신 채권도 노출에 들어가 **이중 계산**(DoD 위반).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(엔트리 가산). COMPLETED 엣지를 되돌리는 것은 design-B B7 되돌리기 비용(중간).

---

## C12. import 방향 — 신규 모듈의 계층 등록

**결정**

`test:architecture/test_import_direction.py:23-52`(L0·L1·L2·S3_PLATFORM·L2_NO_CHAIN·ALLOWED_OUTSIDE_IMPORTERS)와 `test_every_s3_domain_module_directory_is_registered_in_a_layer`(`:108`)에 따라 신규 모듈을 등록한다(가칭 — A·B가 모듈명 확정).

| 모듈 | 계층 | 근거·제약 |
|---|---|---|
| `receivables` | **L2 + L2_NO_CHAIN** | 선적·SO·입금을 읽고 provider를 구현(`credit.providers` 임포트 — L2→L2). trade_chain 임포트 금지(오케스트레이터 `trade_chain/receivable_flow.py`가 부른다 — payments 방향 반전 선례 `code:modules/payments/service.py:3-6`) |
| `credit` | L2(기존) | **`receivables` 임포트 금지**(design-B B6 ③ — 차감 데이터는 provider Protocol로만). 신규 단언 1개 |
| `lc_terms`(모델·스키마) | L1 | SO 모델을 임포트하지 않는다(FK는 테이블명 문자열 — L1→다른 L1 금지 `:67-68`). 등록·개정 flow는 trade_chain |
| 서류 생성기(가칭 `trade_documents`) | L2 + L2_NO_CHAIN | QT·PI·SO·선적 모델 읽기(L1 여럿) + 렌더. 발행 flow(채권 동반)는 trade_chain이 조합 |
| 자사 레터헤드(가칭 `company_profile`) | **S3_PLATFORM**(전표 무임포트) | 렌더가 레터헤드를 읽는다(역방향 0) |
| `documents`(기존, S3 밖) | 무변경 — **전표 모듈 임포트 금지 유지** | P-13 전표 첨부의 소유자 존재 확인은 documents가 SO·QT 모델을 임포트하면 "허용 목록 밖에서 전표 모듈을 임포트"(`:75-80`) 위반이다 → **소유자 해석기 레지스트리**(`documents.owners.register_owner_resolver(owner_type, fn)`)를 두고 전표 쪽이 등록한다(등록 배선 = `api/router.py` — 이미 허용 목록, approvals `TargetSpec` 선례 `:8-9`). 자율 확정 |
| `app/bootstrap.py`(provider 등록 — design-B B6 ①) | **ALLOWED_OUTSIDE_IMPORTERS에 추가** | `receivables`를 임포트하는 모듈 밖 파일. `main.py`·worker·`cli.py`는 bootstrap만 부른다 |

- **P-13 owner_type 확폭 경고 처리**: `documents.owner_type`는 `String(13)`(`code:modules/documents/models.py:107`)이고 전표 종류 이름이 넘친다(`PROFORMA_INVOICE` 16자 — P:1627). VARCHAR 확폭은 PostgreSQL에서 **메타데이터만 바뀌는 ALTER(테이블 재작성 없음)**라 잠금 비용이 작다 — 다만 `ACCESS EXCLUSIVE` 잠금을 짧게 잡으므로 마이그레이션 드라이런에서 실측하고 runbook 배포 절차에 "업로드 없는 시간대" 1줄(A·통합). `DOCUMENT_OWNER_TYPES`(`:68`) CHECK 확장은 같은 마이그레이션.

**근거**: ADR-0052(계층 DAG), `test:architecture/test_import_direction.py:1-12`, design-B B6 ①.

**대안**: documents를 S3_DOMAIN(L2)로 승격해 전표 임포트 허용. **기각**: documents는 인증·SKU·라벨 공용 모듈이라 전표 계층에 넣으면 인증 쪽이 전표에 의존하는 경로가 열린다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(집합 상수·레지스트리 1개).

---

## C13. KST 자정 고정 규율

**결정**
- ① **업무 날짜는 KST**(`today_kst()` — `code:core/time.py:40-48`), 시각은 UTC 저장·KST 표시(D:476 렌즈 6). S3-3의 날짜 경계: CI 발행일·`invoice_on`(≤ 오늘 KST — design-B B2), 입금 `received_on`(≤ 오늘), aging 기준일(오늘 − 만기), 제시·네고·인수일(≤ 오늘), L/C `expiry_on`·`latest_shipment_on` 도과(`오늘 > 기일`), OPENING 만기, 스캔 기준일.
- ② **한 요청·한 실행 = 한 '오늘'**: 서비스 진입점이 `today = today_kst()`를 **한 번** 잡아 하위 함수에 인자로 주입한다(aging·검증·렌더 날짜 표기 모두). 하위 함수가 각자 `today_kst()`를 부르면 KST 자정을 걸친 요청이 두 날짜로 판정된다(예: 입금일 검증은 어제, aging은 오늘). 스캔은 기존대로 `now` 1개 → KST 오늘(`code:modules/trade_chain/deadline_scan.py:29` "한 실행의 기준 시각 1개").
- ③ **시험 고정 지점 일반화(자율 확정)**: `pin_today_kst`는 import 지점마다 패치한다(`test:support/kst.py:1-32` — "이름을 각자 쥐고 있어 원본 하나를 바꿔서는 고정되지 않는다"). S3-3 신규 모듈이 `from app.core.time import today_kst`를 쓰면 `MILESTONE_TODAY_IMPORT_POINTS`(`:18-25`)에 빠지는 순간 **CI가 KST 23:55~00:04에만 실패**한다(PR #50 실측 — `docs/testing.md` concurrency 규율). 그래서 ⓐ 목록을 `TODAY_IMPORT_POINTS`로 일반화해 receivable·payment·서류·L/C flow와 `payments.service`(이미 이름 임포트 — `code:modules/payments/service.py:26`)를 등재하고 ⓑ **아키텍처 시험 신설**: `app/modules/{trade_chain,receivables,payments,lc_terms,trade_documents}` 아래에서 `today_kst`를 이름으로 임포트하는 모듈 집합 ⊆ `TODAY_IMPORT_POINTS`(누락 = 즉시 실패 — 자정 운에 맡기지 않는다).
- ④ **렌더 날짜 표기**: 서류에 찍히는 날짜는 저장된 DATE 그대로(변환 0) — 시각(발행 시각)은 서류 본문에 찍지 않는다(시간대 혼선 차단). 발행 메타 응답의 `issued_at`은 UTC ISO 문자열, 화면이 KST로 표시.
- ⑤ **프런트**: 날짜 문자열(`YYYY-MM-DD`)을 `new Date(...)`로 파싱 금지(UTC 자정 해석 → KST −1일) — 기존 소스 계약 vitest(`fe:routes/shipment-detail.test.tsx` 등 5파일)에 채권·aging·L/C·서류 화면을 편입한다(화면 부록이 파일 소유, 규칙은 이 부록).
- ⑥ **경계 시험 위치**: aging 구간 경계(design-B GB-25), `invoice_on = 오늘`(허용)·`오늘+1`(422), L/C 유효기일 당일(미도과)·+1(도과)은 전부 `pin_today_kst` 아래에서 단언한다. 동시성 시험은 같은 '오늘'을 시험과 앱이 공유한다(`docs/testing.md` "시간 경계가 있는 시험" 규율).

**근거**: D:476(렌즈 6), `code:core/time.py:40-48`, `test:support/kst.py`, `docs/testing.md` concurrency 규율.

**대안**: `today_kst`를 모듈 속성 접근(`time_mod.today_kst()`)으로 바꿔 패치 1곳. **기각(이번엔)**: 기존 수십 지점의 관용 변경이라 S3-3 범위를 넘는다 — 커버리지 단언(③ⓑ)이 같은 효과를 더 싸게 낸다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(시험 지원 코드).

---

## C14. 에러 코드와 번역

**결정**
- 재사용: `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(채권·발행본이 살아 있는 선적 취소 — CHILD_LINKS 등록만으로 자동), `TRADE_DOCS.TRANSITION.NOT_ALLOWED`, `COMMON.CONCURRENCY.LOCK_BUSY`(55P03·40P01 → 409 — `code:modules/trade_docs/locking.py:23-24`), `PAYMENTS.PAYMENT.CURRENCY_MISMATCH`·`EXCEEDS_DUE`(기존), 403은 기존 ForbiddenError 단일 의미(역할 재판정 전용 코드 신설 0 — S3-2 C13 선례).
- 신규(이 부록 범위 — 최종 목록은 통합): `COMMON.IDEMPOTENCY.KEY_INVALID`(C5 ②), `RECEIVABLES.OPENING.DUPLICATE_REF`·`RECEIVABLES.RECEIVABLE.AMOUNT_MISMATCH`(C5), `TRADE_DOCS.DOCUMENT.{ALREADY_ISSUED, SOURCE_CHANGED, NOT_EXPORT}`(C4·C5·C7 — 이름은 A와 통일), `LC_TERMS.LC.{ALREADY_ACTIVE, NUMBER_IN_USE, NOT_LC_SHIPMENT}`·`LC_TERMS.CHECKLIST.STALE_MARK`(직전 마크 불일치 409). design-B B18 목록과 합쳐 카탈로그 1:1.
- 형식: 3세그먼트·카탈로그 1:1·문구에 조치 힌트(`test:unit/test_error_catalog.py`), detail과 log_context 분리. **409 detail에 금액 없음**(잔량·id만 — 예외: `EXCEEDS_DUE`의 미수는 판매금액이라 detail 허용 여부를 B와 통일 — 기존 PI 입금 detail 실측 따름).
- 오류 우선순위 401→403→404→409→422(ADR-0079 ⑧). 노출 구성 변경 쓰기는 **거래처 잠금 전 무잠금 peek**로 404·409를 먼저 판정해 잠금을 헛되이 잡지 않는다(S3-2 PR-4a·5a 선례 — ADR-0079 부기).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## C15. 시험 설계 — H·I·J·K(+G 마스킹 경계)

원칙: 동시성 항목은 **실제 동시 실행**(스레드별 세션 + Barrier — `docs/testing.md` 픽스처 규약 ③)으로만 증명한다. 잠금을 쓰는 시험은 **변이 점검**(잠금 제거 시 실패)을 함께 적는다. ★ = `golden` 후보(GC v1.6 등재는 통합). design-B B21의 GB-xx와 겹치는 것은 참조만 단다.

### J. 안전 계약

| ID | 시나리오 | 기대 | 변이 |
|---|---|---|---|
| CJ-01 | CI 발행 더블클릭(같은 키 2회, 동시) | 발행본 1·채권 1·채번 1·SO 상태이력 ≤ 1·outbox 각 1·audit 1, 응답 동일 | 멱등 claim 제거 → 2건 |
| CJ-02 ★ | 같은 선적 채권 발생 동시 2건(다른 키) | 1 성공·1 409 `ALREADY_OPEN`, 500·40P01 0(=GB-06 동시판) | 부분 유니크 제거 → 2건 |
| CJ-03 | 같은 채권 입금 동시 2건, 합 > 미수 | 1 성공·1 422 `EXCEEDS_DUE`, 미수 ≥ 0 | receivables `FOR UPDATE` 제거 → 둘 다 성공(kill) |
| CJ-04 | 채권 발생 vs 같은 선적 취소 동시 | 하나만 성공: 취소 먼저 → 채권 409(동결 살아 있음 아님) / 채권 먼저 → 취소 409 `SUCCESSOR_ALIVE`. 둘 다 성공 0 | |
| CJ-05 | 마지막 선적 채권 발생(→ COMPLETED) vs 같은 SO 새 선적 생성 동시 | 최종 상태 일관: COMPLETED면 새 선적 0, 새 선적이 있으면 SO ≠ COMPLETED — 거짓 종결 0 | SO `FOR UPDATE` → 무잠금 변이 시 불일치 검출 |
| CJ-06 | short-close vs 다른 선적 채권 발생 동시 | 결과 ∈ {COMPLETED(둘 다 반영), short-close 409 후 채권만} — `SHIPMENT_PENDING` 판정이 낡지 않음 | |
| CJ-07 ★ | **노출 공백·이중 0의 동시성 판**: 여신 평가(잠금 평가)가 SO 항 질의를 마친 직후 시험 훅(provider 래퍼 Barrier)에서 멈추고, 그 사이 같은 거래처 채권 발생을 시도 | 채권 발생이 거래처 잠금에서 **대기** → 평가가 본 노출 ∈ {전, 후}, 정확히 일치(이중·공백 0)(=GB-28 결정적 판) | 채권 발생의 `lock_buyer_for_credit` 제거 → 평가가 600,000을 두 항에 다 보거나 0으로 봄(kill) |
| CJ-08 | PI 입금 역기록 vs 같은 거래처 여신 평가 동시(역기록 = 노출 증가) | 직렬화 — 평가가 역기록 전/후 값 중 하나 | T12 거래처 잠금 제거 → kill |
| CJ-09 | 잠금 순서 계측: T5·T6·T7·T8·T9·T10·T11·T12(양 분기)·T14·T15·T16·T17 | 첫 접촉 순서 = LOCK_ORDER 색인 오름차순(부분수열), **PI SHARE 이후 QT·PI UPDATE 0**(C2 ⑤) | `lock_chain(SO)` 변이 → 순서 위반 검출 |
| CJ-10 | 교착 재현 시도: T11(채권 입금) 20회 vs T13(PI 입금) 20회 같은 PI 동시 | 40P01·55P03 0, 전건 성공 또는 업무 409/422 | C2 ⑤ 위반 변이(T11이 lock_chain 사용) → 40P01 발생(kill) |
| CJ-11 | TX 중간 실패 주입: CI 발행에서 채권 INSERT 뒤·채번 직전 예외 / short-close에서 SO 전이 뒤 예외 | 발행본 0·채권 0·SO 상태 불변·상태이력 0·audit 0·outbox 0·채번 카운터 불변·멱등 claim 미완 | |
| CJ-12 | 제약 위반 전수(C5 표·design-B B3) | 각각 지정 409/422, **500 0**(제약 집합 ⊆ 번역표 대사) | 번역표 1행 삭제 → 500 검출 |
| CJ-13 | version 충돌: 채권 취소·short-close·lc 개정·제시 기록·레터헤드·플래그·체크 마크(직전 id) | 두 번째 409, 데이터 = 첫 번째 | |
| CJ-14 | 앱 계정 직접 쓰기: receivables 금액·통화·날짜 열 UPDATE / DELETE, 발행 스냅샷·무효 행·체크 마크 UPDATE/DELETE, payments UPDATE | 전부 42501(열 GRANT·revoke 실측) | GRANT 마이그레이션 누락 변이 |
| CJ-15 | 복합 FK: 채권 살아 있는 선적의 `total_amount` 직접 UPDATE / 채권 금액 ≠ 선적 금액 INSERT | 23503(C5 — DB 1차망) | |
| CJ-16 | MATCH SIMPLE 함정(=GB-14)·채권 입금 통화/거래처 불일치 직접 INSERT(=GB-15) | 23503 | |
| CJ-17 | 같은 키 재생 6종: 채권 입금·이중 입력 ack 재요청·short-close·OPENING·플래그 토글·lc 등록 | 각 1회·응답 동일·audit 1 | |
| CJ-18 | `Idempotency-Key` 129자 / 128자 / 제어문자 | 422 `KEY_INVALID` / 정상 / 422(P-39) | |
| CJ-19 | 55P03 주입(lock_timeout 단축 + 상대 TX가 거래처 행 보유) 중 채권 발생 | 409 `LOCK_BUSY`, 부작용 0 | |
| CJ-20 | outbox: 채권 TX 롤백 → 이벤트 0 / 커밋 후 디스패치 실패 → 재시도 기록 | | |
| CJ-21 | OPENING 같은 (거래처, invoice_ref) 동시 2건(다른 키) | 1건·409 `DUPLICATE_REF` | |
| CJ-22 | 당사자 추가 → 선적 version+1 → 그 전 CI 발행본 STALE, 옛 version PL과 교차 = 422 `SOURCE_CHANGED`(C4 ②) | | version 증가 제거 → STALE 미검출(kill) |

### H. 운영

| ID | 시나리오 | 기대 |
|---|---|---|
| CH-01 | 출고지시 선적, 대금만기 D-3, 채권 없음 | 알림 1(SO 담당)(=GB-21) |
| CH-02 | 미수 0 후 재스캔 / 미수 > 0 만기+1 | 0 / 도과 1(=GB-22) |
| CH-03 | 같은 날 재실행 / 확인(ack) 후 재실행 | 신규 0 / 재발송 0 |
| CH-04 | D-3 미확인(현재 수신자·24h 전 생성) | ADMIN 에스컬레이션 1회, 재실행 추가 0(기존 규칙 승계) |
| CH-05 | **후보 확장 회귀**: ETD 실적까지 입력돼 열린 저장형 행 0·통관 0인 동결 수출 선적, 대금만기 D-1 | 후보 포함·알림 1(C10 ②-1 — 현행 후보 질의로는 0) |
| CH-06 | SO 담당 일괄 이관 후 스캔 | 새 담당 수신(즉시 반영) |
| CH-07 | 발송 직전 재확인: 스캔 판정 후·INSERT 전에 입금 커밋(Barrier) | `deferred` 1·알림 0 |
| CH-08 | 대금만기 UNKNOWN + ETD 실적 / 실적 없음 | UNRESOLVED 1회 / 0(=GB-23) |
| CH-09 | `lc` 플래그: 행 부재 = OFF(신규 L/C 입력 422) / ON → 등록 → OFF → 기존 L/C 기한 계산·알림 계속(=GB-26) / 토글 audit 1 | |
| CH-10 | 제시기한 D-1 → 제시 기록 → 재스캔 / L/C 유효기일·최종선적일 임박 | 0 / 각 1(=GB-24) |
| CH-11 | OPENING 만기 D-7 | 알림 entity `receivables`, 이동 표 존재(K 출구) |
| CH-12 | 검산: 발행 스냅샷 헤더 ≠ Σ 라인(직접 INSERT로 오염) | ADMIN 알림 1, 자동 보정 0 |
| CH-13 | 동시 20건 경합: 같은 거래처 채권 입금 10 + 채권 발생 5 + PI 입금 5 | 교착 0·미수 ≥ 0·노출 = Σ 정의(사후 재계산 일치) |
| CH-14 | runbook 계약: OPENING 전환 순서(design-B B14 ③)·`lc` 토글 클릭 단위·수기 양식 채권/입금 행의 필드명이 실제 요청 스키마에 존재 | `test:unit/test_runbook_and_compose_contract.py` 확장(S3-2 PR-8 선례 P:12) |
| CH-15 | 스캔 1건 실패 주입(한 채권 조회 예외) | 나머지 처리·잡 FAILED·관리자 알림 1 |

### I. 자동화

| ID | 시나리오 | 기대 |
|---|---|---|
| CI-01 | REGISTRY 신규 6엔트리(C11) | 허용 호출처 밖 언급·임포트 0, 엔트리 비공회전(프레임워크 자기검사) |
| CI-02 | SO 자동 엣지 정확히 3·COMPLETED 진입 엣지 존재 ⇒ 부트스트랩 후 `not is_default_provider()`·`COMPLETED ∈ CLOSED_STATUSES`(design-B B7 ⑥ — `test:architecture/test_doc_machines.py:141-153` 대체) | |
| CI-03 | 레지스트리 14·시각 무변경·4금 집합 무변경(`test_scheduler_registry.py:149-201` 통과 유지) | |
| CI-04 | scheduler·cli의 trade_chain 임포트 = {expiry_sweep, deadline_scan}(`:977-997` 무변경 통과), 채권·서류·L/C 쓰기 함수 언급 0 | |
| CI-05 | 스캔 허용 목록: receivables는 `models`·`outstanding` 서브모듈만, 금지 호출 무변경 | 쓰기 함수 서브모듈 임포트 변이 → 실패 |
| CI-06 | 부트스트랩 3 입구(API·worker·CLI) 각각 provider 등록·2회 호출 무해(=GB-27) | |
| CI-07 | S3-3 신규 모듈의 네트워크·메일 클라이언트 임포트 0, 렌더 엔진 원격 리소스 로드 차단 설정 | |
| CI-08 | PL·S/I 발행 경로가 receivables·SO 전이 함수를 임포트하지 않음 / 출고지시·마일스톤 실적·통관 flow가 발행·채권 함수 언급 0 | 자동 발행·자동 채권 0 |
| CI-09 | 하자 체크리스트 응답·모델에 판정 필드(적합·하자 없음) 0, 마크는 actor 필수 | 법적 판정 0 |

### K. 보안·품질(+G 마스킹)

| ID | 시나리오 | 기대 |
|---|---|---|
| CK-01 | 매트릭스 신규 행 전부 5역할 프로브(C6) + documents owner_type별 2행 | 표와 일치 |
| CK-02 | `GOVERNED_PREFIXES` 신규 4개 — 각 접두어 제거 변이 | 완전성 단언 실패(kill) |
| CK-03 | CERT가 `owner_type=SALES_ORDER`로 없는 SO에 첨부 | 403(404보다 먼저) |
| CK-04 | 부모-자식 불일치: 다른 선적의 발행본 file / 다른 채권의 입금 목록·역기록 / 다른 SO 선적의 제시 기록 | 404(본문 불일치는 422), 부작용 0 |
| CK-05 | 없는 채권 id + VIEWER 쓰기 | 403 |
| CK-06 | 페이지네이션 자동 스캔: 채권·aging·채권 입금·발행본·L/C·플래그 목록 | Page 봉투·기본 50(`test:architecture/test_auth_coverage.py:131-170` 자동) |
| CK-07 (G) | 렌더 컨텍스트·발행 스냅샷·CSV·outbox·audit·로그에 원가 키 0, 계좌번호는 로그·audit·outbox 0, 수입선적 서류 발행 422 | C7 |
| CK-08 | 렌더 다운로드 VIEWER·CERT 403 / QT·PI 렌더 LOGISTICS 403 | C6 |
| CK-09 | 렌더 주입: 품명 `{{ 7*7 }}`·`<script>`·`=HYPERLINK(...)` | PDF 텍스트·엑셀 셀에 원문 그대로(평가 0·수식 0) |
| CK-10 | 신규 쓰기 스키마 `extra="forbid"`, CI 발행 본문에 금액 필드 부재 | |
| CK-11 | 에러 카탈로그 1:1·3세그먼트 | |
| CK-12 | import 방향: 신규 모듈 계층 등록·`credit ↛ receivables`·`receivables ↛ trade_chain`·`documents ↛ S3 도메인`·`bootstrap.py` 허용 | C12 |
| CK-13 ★ | K 문면 "L/C 제시기한 MIN·tolerance 상하한"(D:453) — 순수 함수 + **배선 후 API 경로**(=GB-18·GB-20) | S3-2 산식 그대로 |
| CK-14 | 출구 계약: 스캔 알림 entity_type ⊆ `fe:lib/alert-routes.ts` | `receivables` 포함 |
| CK-15 | `TODAY_IMPORT_POINTS` 커버리지(C13 ③ⓑ) | 누락 모듈 추가 변이 → 실패 |
| CK-16 | 신규 표 분류(IMMUTABLE/MUTABLE)·users FK 분류·열 GRANT 상수 ↔ 마이그레이션 리터럴 대사 | |
| CK-17 | 레터헤드 로고·서명 업로드: MIME·크기·확장자·저장명(documents 4항 재사용) | |
| CK-18 | aging·채권 CSV: UTF-8 BOM·한글·장문·수식 이스케이프(W:131 검증 B "한글 CSV") | |

### 기존 시험 갱신 목록(실패가 정상인 안전망 — 이 부록 범위)
- `test:architecture/test_doc_machines.py:125-153`(SO 자동 엣지 2→3·ADR-0076 결속 시험 대체 — CI-02), 상태 총수 핀(`code:modules/trade_docs/machine.py:7-8` 독스트링 동반 — 수치는 design-B B7 ⑤), `test:architecture/test_no_auto_confirm_code_path_exists.py:41-114,1029-1066`(REGISTRY·스캔 허용 목록), `test:architecture/authz_matrix.py:25-53`(접두어·행), `test:architecture/test_import_direction.py:23-52`, `test:architecture/test_approval_contract.py:578-620`(거래처 잠금 단일 진입점 — 통과 유지 확인, `open_order_amount` 단일 정의 단언 갱신), `test:integration/test_trade_deadline_scan.py`의 `SCAN_TYPES`·후보 시험, `test:support/kst.py`(일반화), `test_user_fk_classification.py`·`table_policy` 분류 시험, 당사자 version 불변 단언(있다면 — C4 ②).

**근거**: D:450(H), D:451(I), D:452(J), D:453(K), D:449(G "조회 역할 원가 마스킹"), D:439 ①·D:441(P3 매핑 = A·B·E·G·H·I·K + J), GC-F1/F2 실제 동시 실행 규칙.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(시험 가산).

---

## C16. S3-2·S3-1 부채 중 S3-3 소유분 — 이 부록 관점의 처리

| ID | 내용 | 주 소관 | 이 부록이 정한 것 |
|---|---|---|---|
| Q-04 = P-02 | SO COMPLETED·short-close | B(B7) | T14 잠금·TX(C1), COMPLETED 4금 논증·REGISTRY(C11), 3열 불변의 실현 수단 정정(C5), CJ-05·06 |
| Q-05 | 중량·CBM·박스 열 | A | PL 발행 T6의 동결 재검사(라인은 PLANNED만 편집 — C4 ③) |
| Q-06 | 비거래처 수하인 'TO ORDER' | A(·B L/C 판정) | 당사자 변경 version+1 → STALE(C4 ②), 당사자 쓰기 잠금 무변경 |
| Q-08(+R-6-2) | 대금만기·제시기한 알림 | B(B13) | 잡 14 유지·후보 확장·허용 목록·수신자·재확인(C10), CH-01~11 |
| R-3a-5 | SHIPPER(자사) 자리 | A(design-B B16 권고 = 레터헤드 스냅샷) | 레터헤드 T1 TX·감사·A 전용 권한(C1·C6·C8) |
| R-3a-4 | 당사자 변경 version 미증가 | 선적 | **S3-3 재트리거·해소**(C4 ②) |
| P-01 | 선적분 노출 차감 | B(B6) | 차감 = 채권 전환 시점, 거래처 잠금 의무·동시성 증명 조건(C3), CJ-07 |
| P-08 | 미수 provider 등록 | B(B6 ①) | 부트스트랩 import 허용(C12), CI-06 |
| P-09 | payments 확장 | B(B3) | 역기록 분기 위치(C1 T12)·번역표 대사(CJ-12) |
| P-10 | L/C 플래그 토글 | B(B11) | T18·ADMIN·audit·`feature-flags` 통제 접두어(C6), G-09 종결(C10 ⑤) |
| P-11 | 자사 레터헤드 | A | T1·singleton 유니크(C5)·A 전용(C6) |
| P-12 | 만료/취소 PI 입금 | B(B5 ⑦ — 409 유지) | 변경 0 |
| P-13 | documents 전표 첨부·owner_type 확폭 | A | 소유자 해석기 레지스트리(C12)·owner_type별 403 순서(C6 ②)·`/documents` 통제 편입(C6 ①)·확폭 잠금 비용(C12) |
| P-14 | 입금 이중 입력 | B(B5 ④) | ack 감사(C8) |
| P-15 | 선수금 미차감 재판정 | B(B4 ⑤) | — |
| P-39 | Idempotency-Key 길이 | 코어 | **S3-3 해소**(C5 ②) |
| P-51 | 이월 미수 | B(B14) | OPENING `(partner, invoice_ref)` 유니크(C5)·CLI 금지(C11) |
| P-56 | SKU 용량 컬럼 | A(서류 품명 표기) | 해당 없음(권한·TX 영향 0) |

---

## 자율 확정 판정표

| 안건 | 결정 한 줄 | 문서 갱신 | 되돌리기 |
|---|---|---|---|
| C1 | T1~T22 각 1TX, CI 발행 = 채권 발생 1TX, 렌더는 TX 밖·결과 비저장, 렌더 다운로드 audit, 역기록 분기를 잠금 전에 | §7.6·§7.10 부기 | 낮음~중간 |
| C2 | LOCK_ORDER에 `lc_terms`(SO 뒤)·`trade_documents`·`receivables`(shipment_children 뒤) 삽입, PI SHARE 뒤 `lock_chain` 금지, 채권 발생은 PI 무잠금 | §17.2 부기 + **ADR-C①**(ADR-0078 부기) | 중간 |
| C3 | 노출 구성 변경 쓰기 = `lock_buyer_for_credit` 선행, 선적 경로는 무변경 | §17.2 부기 1문단 | 낮음 |
| C4 | 채권·SO short-close·L/C·제시·레터헤드·플래그 version, **당사자 변경 version+1(R-3a-4 해소)**·발행본 STALE | — | 낮음 |
| C5 | 키 필수 + **P-39 해소**, OPENING `(partner, invoice_ref)` 유니크, 채권 금액 복합 FK 권고, short-close 불변 = CHECK 아닌 3중 수단 | §17.4 부기 | 낮음 |
| C6 | 경로 단위 역할 — CI·채권·입금·short-close·L/C = A·T, PL·S/I = A·T·L, OPENING·취소·플래그·레터헤드 = A, 조회 전 역할(렌더 제외), `GOVERNED_PREFIXES` += receivables·feature-flags·company-profile·**documents** | §2·§18.1 부기 + **ADR-C②** | 낮음 |
| C7 | 원가 채널 0, 수입선적 서류 422, 렌더 다운로드 역할 축소, 렌더 주입·원격 로드 차단 | — | 낮음 |
| C8 | 발행 스냅샷·무효·체크 마크 IMMUTABLE, receivables 열 GRANT, 감사 액션 6종, users FK = ACTOR_LOG | §17.5 부기 + ADR | 낮음~중간 |
| C9 | 이벤트 7종(금액 0), `cause_receivable_id` 화이트리스트, 규칙 시드 0, 소비자 없는 이벤트 0 | — | 낮음 |
| C10 | **잡 14 유지**, `trade-deadline-scan` 후보 확장·허용 목록(쓰기 없는 서브모듈)·SO 담당 수신, 검산 확장, G-09 종결 | §15 부기 + runbook + ADR-0084 부기 | 낮음 |
| C11 | 4금 대조표, SO 자동 엣지 3(COMPLETED 1 추가) 논증, REGISTRY 6엔트리, B/L draft·DGD·자동 발송 미개방 | §15 부기(S3-2 ① "2" → "3") + ADR(0076 대체) | 낮음 |
| C12 | receivables·서류 = L2+NO_CHAIN, lc_terms = L1, 레터헤드 = 플랫폼, documents 소유자 해석기, bootstrap 허용 | — | 낮음 |
| C13 | 한 요청 한 '오늘' 주입, `TODAY_IMPORT_POINTS` 일반화 + 커버리지 시험, 서류에 시각 비표기, 프런트 `new Date` 금지 확장 | testing.md | 낮음 |
| C14 | 기존 코드 재사용 + 신규 10종 내외, 403 단일 의미, peek로 404·409 선판정 | — | 낮음 |
| C15 | J 22·H 15·I 9·K 18, 실제 동시 실행 + 변이 | testing.md 그룹 표 | 낮음 |
| C16 | S3-3 소유 부채 18행 매핑 — R-3a-4·P-39 해소, G-09 종결 | PROGRESS 부채표 | — |

## 멈춰서 보고할 항목 (통합 확정 전제 — DESIGN·ADR 부기로 해소)
1. **부록 문자 불일치**: design-B가 가정한 "D(인가·잠금)"·"C(L/C)"는 실제 배정과 다르다 → 인가·잠금 = 이 부록, `lc_terms` 본체 = B(B10 ⑥ 대체 조항 발동)로 자율 확정. 통합이 design-B 0-2 표의 문자를 정정한다.
2. **design-B B9 ② 잠금 순서의 교착 위험**: 채권 발생의 "PI `FOR SHARE` → SO `FOR UPDATE`"에서 SO를 `lock_chain`으로 잡으면 QT·PI UPDATE가 PI SHARE 뒤에 와 순서 위반 + 승격 교착(C2 ⑤). → 채권 발생은 PI 무잠금, PI를 잡는 경로의 SO는 `lock_document` 직접.
3. **design-B B7 ③ "한 번 쓰면 불변 CHECK"는 CHECK로 불가** → 일관성 CHECK + 종결 상태 + 단일 대입 통로 스캔(C5).
4. **design-B B13 후보 집합**: 현행 스캔 후보 질의(`code:modules/trade_chain/deadline_scan.py:525-555`)로는 실적이 다 들어간 동결 선적의 대금만기가 후보에서 빠진다 → 후보 OR 확장(C10 ②-1). 미수 정의는 쓰기 함수 없는 서브모듈에(C10 ②-2).
5. **design-B B7 ② "호출처 2곳"**: CI 발행이 채권 발생을 부르면 `converge_sales_order_completion` 호출 파일은 3개(C11) — 핀 수치 통합 확정.
6. **WBS 문면 대비**: "렌더링"을 "발행 스냅샷 + 결정적 재렌더(파일 비저장)"로 해석(C1) — A가 파일 보존을 요구하면 통합 재판정. "lc_terms(feature flag)"의 OFF 의미 = 입력만 닫음(design-B B10 ⑧ — §20 H 해석 부기)에 동의.
7. **기존 불일치(보고만)**: PI 상세가 은행 계좌번호를 전 역할에 싣는데 계좌 마스터 조회는 A·T뿐(C7 ③) — PI 모듈 소관 부채 후보. `payment_flow.py:6` 독스트링의 "거래처→QT→PI"는 실측(QT→PI)과 다름(C1 — S3-3 T12 PR에서 실측과 맞춤).

## 부채 등재 후보(이 부록)
- **C-D1** PI 상세의 은행 계좌번호 전 역할 노출(마스터 조회는 A·T) — 소유 PI·은행계좌 / 트리거: 계좌 정보 문의 1건 또는 VIEWER 계정 외부인 부여.
- **C-D2** 1 L/C : N SO 등록(`lc_number` 살아 있는 유니크 완화) — 소유 B(L/C) / 트리거: 실수요 1건.
- **C-D3** `/api/v1/scheduled-jobs`(ADMIN — `code:modules/platform/router.py:16-25`)가 `GOVERNED_PREFIXES` 밖 — 소유 플랫폼 / 트리거: 플랫폼 라우터 수정 세션.
- **C-D4** `today_kst` 이름 임포트 관용 → 모듈 속성 접근 전환(패치 1곳) — 소유 플랫폼 / 트리거: `TODAY_IMPORT_POINTS` 30개 초과.
- **C-D5** 렌더 다운로드 audit의 양(대량 다운로드 시 audit_log 증가) — 소유 서류 / 트리거: audit_log 일 1만 행 초과.
- **C-D6** 하자 체크리스트 물류 배정(T17 = A·T) — 소유 B / 트리거: 물류가 제시 서류를 준비하는 운영 확인.

**실행 검증 못 했음.** 위 시험 ID는 설계이며 아직 존재하지 않는다. 인용한 코드 줄은 `2092406` 정적 독해 값이고, 교착·경합 시나리오는 잠금 순서를 손으로 따라간 추론이다 — 각 PR 첫 커밋에서 계측 시험(CJ-09·CJ-10)으로 실측 기록한다.
