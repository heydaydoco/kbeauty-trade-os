# S3-3 계획 설계 — 통합 검토서 (부록 A~E 통합)

- 기준: main `2092406`(S3-2 종결 — PR-8 #67). 기준선(PROGRESS 'S3-2 PR-8 / S3-2 종결' 절 `P:32-42` 실측값): pytest **5774 수집**(최종 코드 1회 실행 5723 passed · 34 skipped · 15 errors[실행 옵션 실수 — 해당 4파일 재실행 83 passed]) · vitest **83파일 1474** · golden **85** · concurrency **68** · §20 그룹 A 1699·B 47·C 826·D 0·E 0·F 260·G 86·H 618·I 28·J 586·K 2260 · 커버리지 게이트 94(CI 3샤드, ADR-0073) · `alembic heads` = `281da4794717` 1개 · ADR 최대 0087 · JOB_REGISTRY 14 · 상태 총수 30/152/182 · IMMUTABLE 14표.
- 사양 정본은 DESIGN.md, 일정은 WBS.md S3-3 행(`W:127-132`)과 v1.6 주석(`W:117-125`·`W:132`·`W:139`·`W:148`·`W:155`), 진행·부채는 PROGRESS.md('S3-2 PR-8 / S3-2 종결' 절 `P:3-175`, S3-2 부채 최종 목록 `P:50-173`, S3-1 등재 P-01~P-60 `P:1614-1675`, '## 현재' `P:1689-1690`)다.
- 표기: `D:줄`=DESIGN.md, `W:줄`=WBS.md, `P:줄`=PROGRESS.md, `GC:줄`=`kbeauty-golden-cases-v1.md`, `code:경로:줄`=`backend/app/` 아래, `test:경로:줄`=`backend/tests/` 아래, `fe:경로:줄`=`frontend/src/` 아래, `sA`~`sE`=같은 디렉터리 `design-A.md`(서류 데이터 모델·생성기)·`design-B.md`(채권·입금·여신 provider·대금만기)·`design-C.md`(동시성·권한·감사·잡)·`design-D.md`(API·화면 계약)·`design-E.md`(PR 분할·마이그레이션·검증 배치).
- **부록 문자 정정**: sB는 작성 시점에 "L/C 부록(C)"·"인가·잠금 부록(D)"·"화면 부록(E)"을 가정했고 sA는 문자를 쓰지 않았다. **확정 배정은 위 표기와 같다**(A 서류·B 채권·C 동시성/권한/감사/잡·D API/화면·E 분할). sB 본문의 "D 소관"은 sC로, "C 소관(lc_terms 본체)"은 sB B10 ⑥ 대체 조항에 따라 **sB 소유**로, "E 소관(화면)"은 sD로 읽는다(sC §0-2·sD §0 보고와 같은 결론). sE 작성 시점에 sD가 없었다는 기록(`sE:4`)은 이 통합에서 sD로 재대사했다(§1.6 X-34).
- **우선순위**: **§9(적대 검토 정정, 2026-10-05)가 이 문서의 다른 절보다 우선**한다. 부록 A~E와 이 문서가 충돌하면 **이 문서가 이긴다**(S3-1 선례 `docs/plans/s3-1-plan.md:20`, S3-2 선례 `docs/plans/s3-2/design-integrated.md:5`). 이 세션의 쓰기 범위가 통합·계획서 2파일로 한정되어 **부록 원문은 고치지 않았다** — 통합에 진 부록 문면의 색인은 §1.8 표가 대신하고, PR-1 첫 커밋에서 각 부록 머리에 우선순위 1줄과 "[통합 X-nn]" 표지를 단다(S3-2 R-27 방식).
- 판정: 오너 상시 지시(2026-09-29 "결정·개입 없이 끝까지")에 따라 판정 후보는 전부 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. 사후 번복 비용은 각 해소 항목과 §3 ADR, 계획서 §5에 적었다. PROGRESS 등재 시 "자율 확정"으로 표기한다(ADR-0011 부기).
- **실행 검증 못 했음.** 정적 독해(파일 열람·grep·sed)만 했다. pytest·vitest·alembic·서버는 돌리지 않았다. 이 통합에서 다시 실측해 인용한 코드 줄(`2092406`): `code:modules/trade_docs/machine.py:7-8,108-144,237-243`·`code:modules/credit/exposure.py:19,37-39`·`code:modules/credit/evaluation.py:165-216`·`code:modules/credit/providers.py:55-75`·`code:modules/credit/locking.py:32-42`·`code:modules/trade_docs/locking.py:41-54`·`code:modules/trade_chain/deadline_scan.py:523-555`·`code:modules/trade_chain/milestone_view.py:378-393`·`code:modules/trade_docs/schedule.py:146-158,254-255`·`code:modules/trade_chain/payment_flow.py:121-123`·`code:modules/documents/models.py:68,107`·`code:modules/documents/router.py:31,176-189`·`code:modules/shipments/models.py:142`·`code:modules/platform/models.py:67-77`·`code:modules/idempotency/models.py:37`·`code:api/deps.py:94-109`·`code:core/db/table_policy.py:19-48,189-215`·`test:architecture/test_doc_machines.py:141-153`·`test:architecture/authz_matrix.py:25-53`. 나머지 줄은 부록 인용이며 각 PR 첫 커밋에서 실측 기록한다(P-60 선례).

---

## 0. 결론 요약

1. **해소 49건** — 부록 간 모순·중복 **37건**(X-01~X-37) + 어느 부록도 소유하지 않은 누락 **12건**(N-01~N-12). 진짜 설계 충돌은 4덩어리다: ① **CI 운영 닫힘(sA §A2 검수 게이트) ↔ "CI 발행 = 채권 발생 1TX"(sB B1 ③·sC T5)** — 문면대로 합치면 S3-3 운영에서 채권이 0건이 되어 WBS DoD "aging 정확"·"노출 공백 0·이중 0"의 운영 의미가 사라진다(X-01) ② **렌더 산출물 저장(sA §A13) ↔ 비저장·결정적 재렌더(sC §C1)**(X-02) ③ **자사 레터헤드 불변 판(sA §A3) ↔ 단일 행 UPDATE(sC T1·sB B16)**(X-07) ④ **대금만기 INVOICE_DATE 앵커 원천 이원화(CI `doc_date` ↔ 채권 `invoice_on`)**(X-04).
2. **결정 ① — 채권과 CI를 분리한다**: 채권 발생 = **전용 엔드포인트 `POST /shipments/{id}/receivable` 단일 경로**(사람 1클릭, A·T). **CI 발행은 채권을 만들지도 바꾸지도 않는다**(sC T5의 채권 합류 철회). CI가 살아 있는 선적의 채권은 `invoice_on`·`invoice_ref`를 **서버가 CI에서 복사**(본문에 보내면 422)하고 `ci_id`로 잇는다 — CI 취소·재발행은 살아 있는 채권이 막는다(`ChildLink(CI→receivables)`). 채권이 먼저 있는 선적의 CI 발행은 **409**(외부 인보이스로 이미 청구한 선적에 두 번째 인보이스 번호 금지 — S4-2 재판정). 앵커 원천 = **채권 `invoice_on` 단일**.
3. **결정 ② — 렌더 산출물은 저장한다**(sA): documents FILE + 불변 `trade_document_renditions`, (원천·종류·형식·언어)당 첫 파일이 정본. sC의 다운로드 축소·감사는 **생성물 다운로드에 한해** 승계(QT·PI 생성물 = A·T, CI·PL·S/I 생성물 = A·T·L, 다운로드마다 audit 1행).
4. **신규 테이블 12개**(채권 1·레터헤드 1·렌더 연결 1·L/C 3·CI 5·S/I 1), **기존 테이블 변경 8건**(payments 확장·sales_orders UNIQUE+short-close 3열·SO 상태이력 CHECK·shipments UNIQUE 2·documents `owner_type`·document_types 시드 5행·receivables `ci_id` 가산·DocKind CHECK 확장), **IMMUTABLE 14 → 22표**(+8), **열 단위 UPDATE 허용 표 3 → 5**(+commercial_invoices·receivables), **마이그레이션 7건**(M16~M22).
5. **상태 총수** 30/152/182 → **2b 후 31/151/182**(SO IN_SHIPMENT→COMPLETED 자동 1) → **5a 후 32/152/184**(CI 2상태·사람 엣지 1). SO 자동 엣지 2 → 3, ADR-0076 **대체**.
6. **신규 에러 코드 41종**(EXPORT_DOCS 17·DOCUMENTS 2·RECEIVABLES 12·PAYMENTS 1·SALES_ORDERS 3·PLATFORM 1·COMMON 1·LC_TERMS 4), 부록에서 제안됐다가 철회한 코드 3종(sC의 `TRADE_DOCS.DOCUMENT.*` — 이름 통일).
7. **JOB_REGISTRY 14 유지**(신규 잡 0) — `trade-deadline-scan` 대상·후보 확장(대금만기·제시기한·L/C 유효/선적기일·OPENING 만기), `trade-docs-totals-verify`는 CI DocKind 자동 편입.
8. **ADR 0088~0099(12건)** + 기존 ADR 부기 15건. **WBS v1.7·GC v1.6 필요**(GC 14건: A22~A31·F5·F6·G4·H7, `golden` 대사 85 + 14 → **99 이상**).
9. **PR 15개**(직렬): 1 → 1b → 2a → 2b → 2c → 3a → 3b → 3c → 4a → 4b → 5a → 5b → 5c → 6 → 7.
10. **멈춰서 보고할 항목**(DESIGN·WBS 부기+ADR로 해소 전제, §4·§5): ① **CI·PL·S/I 운영 발행은 S4-2까지 닫힘**(WBS S3-3 산출물 "CI·PL·S/I 템플릿 렌더링" 대비 — 운영 경로 0, DoD는 서비스 층) ② **채권 발생 ↔ CI 분리**(DESIGN 사슬 "선적→CI/PL→…→채권" `D:177` 대비 — 채권이 CI 없이 생긴다) ③ **노출 산식 문면 변경**(`D:227` ① "미결 SO 합" → "미결 SO 잔여(총액 − 채권 전환분) 합", SO 묶음 1회 환산) ④ **§15 SO 자동 엣지 2 → 3** ⑤ **§20 H "기능 플래그 오프 완전 비활성" → 입력 진입 비활성**(기존 L/C 기한 계산·알림은 계속) ⑥ **§7.6 DGD·B/L draft 대조는 S3-3 밖** ⑦ **WBS "receivables(만기 자동)" → 만기 비저장 파생**(OPENING만 저장). 계획서 §1은 이를 WBS 대비 4건 + DESIGN 대비 5건으로 등재한다.

---

## 1. 모순·중복·누락 해소표

> 서식: **문제(출처) → 해소 / 근거 / 번복 비용**.

### 1.1 서류 ↔ 채권 결속 (최대 충돌)

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-01** | **채권 발생 경로**: sB B1 ③ "CI 발행 전표가 생기면 CI 발행 TX가 채권 발생 함수를 같은 TX로 호출하고 B 엔드포인트는 닫는다" + sC T5 "CI 발행 = 채권 발생 1TX"(PI·SO·거래처 잠금 포함, CI 발행 역할 A·T) ↔ sA §A2 "S4-2 전 운영 CI 발행 0"·sA §A20 마지막 행 경고 ↔ sD §10 ①~③ 권장(분리 — CI A·T·L / 채권 A·T, CI 있으면 값 복사) ↔ sE 가정 3(S3-3 = 엔드포인트, CI TX 합류는 "기존 채권 재사용") | **분리 확정.** ① 채권 발생 = `POST /shipments/{id}/receivable`(+`/preview`) **단일 경로**, 역할 A·T, 사람 1클릭. ② **CI 발행·재발행 TX는 receivables에 쓰지 않는다**(sC T5의 거래처·SO 잠금·채권 INSERT·SO 수렴 삭제 — §2.12 T5). ③ 살아 있는 CI가 있는 선적의 채권 등록: 본문 `invoice_on`·`invoice_ref`를 **받지 않고**(보내면 422 `RECEIVABLES.RECEIVABLE.INVOICE_FIELDS_FROM_CI`) 서버가 CI `doc_date`·`doc_number`를 복사, `receivables.ci_id`에 CI id 기록(M21 가산 열 — 5a). ④ `ChildLink(COMMERCIAL_INVOICE, "receivables", "ci_id")`(5a) — 살아 있는 채권이 있는 CI의 취소·재발행 = 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(채권 취소 = ADMIN·순입금 0 선행). ⑤ 살아 있는 채권이 **먼저** 있는 선적의 CI 발행 = 409 **`EXPORT_DOCS.CI.RECEIVABLE_EXISTS`**(`detail.receivable_id`) — 외부 인보이스(`invoice_ref`)로 이미 청구한 선적에 시스템이 두 번째 인보이스 번호를 내지 않는다. S3-3 운영에선 CI 발행이 0이라 발동하지 않고, **S4-2 INSPECTED 개방 PR이 "채권 선행 선적의 CI 처리"를 재판정**(WBS v1.7 ⑦·부채 I-01). ⑥ `converge_sales_order_completion` 호출처 = 채권 발생 flow + short-close flow **2곳**(sB B7 ② 원안 — sC §C11의 "3 파일"은 CI 합류 전제라 철회, X-14) | 문면대로 합치면 S3-3 운영 채권 0건 → aging·COMPLETED·대금만기 충족 신호·P-01 차감이 전부 S4-2까지 죽고 '채권 미등록' 알림만 쌓인다(sD §10 ①). 분리하면 청구 기록(상업 사실)은 무역만 만들고(ADR-0079 ② "잔량·상태 = 무역" 계보), 청구 기록과 서류의 불일치는 **경로 폐쇄 대신 '값 원천 고정'(③)과 '선행 409'(⑤)**로 0이 된다. 2TX 아웃박스 합류(sC 대안 (a))는 처음부터 없다 | 중간(S4-2가 단일 경로로 합치면 엔드포인트 폐쇄 + 호출부 1곳 — ADR-0088 되돌리기 란) |
| **X-02** | **렌더 산출물**: sA §A13 저장(documents FILE + 불변 `trade_document_renditions`, 첫 파일 정본, 다운로드 = documents 기존 경로 전 역할) ↔ sC §C1·T3 비저장(스냅샷 + 결정적 재렌더, 다운로드 GET `/…/document`·`/…/file`마다 audit, 역할 A·T(·L)) ↔ sD D1 ②(파일은 documents 통로 하나) ↔ sE 가정 2·E-Q1(A안 가정) | **저장(sA) 확정 + 생성물 다운로드 축소·감사(sC) 승계.** ① 산출물 = documents FILE + `trade_document_renditions`(IMMUTABLE), 첫 렌더 정본, 재출력 = 같은 바이트. ② 다운로드 통로는 기존 `GET /documents/{id}/download` 하나(sD D1 ②) — **단 renditions가 가리키는 문서(생성물)는** 역할을 좁힌다: QT·PI 생성물 = A·T, CI·PL·S/I 생성물 = A·T·L, 그 밖 403(sC C7 ③ — 계좌번호를 담는 대외 전달물). ③ 생성물 다운로드마다 audit `documents.generated.downloaded` 1행(document_id·doc_kind·format — 내용·금액 0)(sC C1 대안 (c) 채택분). ④ 생성물 판정은 documents가 전표·렌더 모듈을 임포트하지 않고 **해석기 레지스트리**(X-10) 경유. ⑤ 렌더는 TX 밖(파일 IO — ADR-0049), 실패 시 `renditions_pending` + 명시 `render` 액션 | '보낸 그 파일' 증명·백업 세트·sha256 검증·고아 탐지 자동 편입(`D:461`)이 더 엄격하다. sC가 든 2단 커밋 위험은 TX1(발행)→TX 밖(렌더·쓰기)→TX2(연결 행) + 유일 키 경쟁 해소 + `storage-monitor` 고아 보고로 닫힌다(sA §A13). 재렌더 정본은 라이브러리·폰트 판이 바뀌면 과거 서류가 달라진다 | 중간(저장 → 비저장 전환은 쌓인 정본 파일 보존 의무 때문에 단방향 — 파일은 지우지 않는다) |
| **X-03** | **CI 발행 역할**: sA §A16 A·T·L ↔ sC §C6 A·T(채권 1TX 근거) ↔ sD §10 ③(분리 시 A·T·L) | **CI(+PL) 발행·재발행·취소 = A·T**(좁은 쪽). 미리보기(CI1·CI9)·`render` 재시도(CI11)·메타 PATCH(CI7, FREE 2열) = A·T·L. S/I 발행·미리보기·렌더 = A·T·L | X-01로 채권 결속 근거는 사라졌지만 CI는 **판매가·은행 정보가 담긴 상업 송장**(청구 서류)이라 무역 소관이 엄격 쪽이다. PL은 CI와 같은 발행 TX의 구성 요소(sA §A7)라 따로 열 수 없다. 물류는 미리보기로 포장 데이터를 맞추고 S/I(금액 없는 물류 서류)는 만든다 | 낮음(행 1줄 — 여는 쪽) |
| **X-04** | **INVOICE_DATE 앵커 원천**: sA §0·§A20 "살아 있는 CI `doc_date`"(`live_invoice_date`) ↔ sB B10 ② "채권 `invoice_on`" | **채권 `invoice_on` 단일 원천**(sB). 불변식 "살아 있는 CI가 있으면 `invoice_on = CI.doc_date`"는 X-01 ③이 보증. `AnchorContext`에 `invoice` 필드 **가산**(`code:modules/trade_docs/schedule.py:146-158` — 독스트링 `:151`이 예고), `resolve_anchor` INVOICE_DATE 분기(`:254-255`)는 "있으면 ACTUAL, 없으면 UNKNOWN `INVOICE_NOT_ISSUED`"(ETD 대체 금지 — GC-A13). 산식 본문 무변경(ADR-0081) | S3-3 운영에서 CI는 0건이라 sA안이면 INVOICE_DATE 결제조건 만기가 S4-2까지 전부 UNKNOWN(sD §10 ④) | 낮음(조립 1곳) |
| **X-05** | **앵커 배선 PR**: sB B19 PR-B2(L/C와 함께) ↔ sE E-Q4 PR-2a | **PR-2a**(aging DoD와 같은 PR) | aging 만기는 `milestone_view` 조립 결과에서 읽는다(sB B12 ③) — 앵커 없이 aging을 내면 INVOICE_DATE 채권이 전부 `DUE_UNKNOWN`이라 DoD "aging 정확"을 그 PR에서 못 닫는다 | 없음 |
| **X-06** | **SHIPMENT 채권 `invoice_ref` NULL 허용**(sB B2) ↔ 필수(sD §10 ②) | **필수**(422). CI가 있으면 CI 번호 복사(X-01 ③), 없으면 외부 작성 CI 번호를 사람이 입력(runbook A-05 문구와 연결). OPENING도 필수(sB B14 ① 그대로) | S5-3 B/L 대조·P7 입금 매칭에서 외부 인보이스 번호가 유일한 연결 키 | 낮음 |

### 1.2 스키마·열·소유

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-07** | **자사 레터헤드**: sA §A3 불변 판 표 `company_profiles`(INSERT-only·as-of·대체 금지·로고 없음) ↔ sC T1·C5 단일 행 `company_profile`(PUT UPDATE+version, singleton UNIQUE, 로고·서명 이미지 documents) ↔ sB B16 단일 행+로고 권고 ↔ sD D8(sA안) | **sA 확정**(불변 판 + as-of). sC의 T1(UPDATE·version)·singleton 유니크·CK-17(로고·서명 업로드)·`/company-profile` 단수 경로는 **철회**. 경로 = `/company-profiles`(sD LH1~LH3). 로고·서명 이미지 = 부채 A-06 | 단일 행 UPDATE면 동결된 QT·PI(레터헤드 열 없음)가 렌더 시점 현재값으로 찍혀 **과거 서류가 새 주소로 다시 찍힌다**(sA §A3 대안 (a)). 불변 판 + as-of가 열 복사 없이 같은 보증을 준다(§3 스냅샷 규율) | 중간(불변 표 — 단일 행 전환 시 CI FK 재지정) |
| **X-08** | **레터헤드 조회 역할**: sA §A16·sD LH1·LH2 전 역할 ↔ sC §C6 A 전용 | **LH1·LH2 = A 전용**(좁은 쪽). 서류 미리보기·CI 상세의 `seller` 블록은 그 응답의 역할 규칙을 따른다(별도 노출) | LH1·LH2의 소비자는 관리자 설정 화면뿐(sD D8) — 미리보기는 자기 응답에 판 요약을 싣는다. 레터헤드 409 안내의 링크도 관리자에게만(sD D5) | 낮음 |
| **X-09** | **CI 영속 모델 이름**: sA `commercial_invoices`(DocKind)+lines+status_log+PL 2표 ↔ sC 가칭 `trade_documents`(헤더)+`trade_document_lines`+무효 IMMUTABLE 행·`(shipment_id, doc_kind)` 유일 | **sA 확정.** sC의 `trade_documents`·무효 행·`TRADE_DOCS.DOCUMENT.ALREADY_ISSUED` 등은 같은 대상의 이름 차이로 읽고 철회. 무효 = 커널 `ISSUED→CANCELLED`(사유 필수, 상태이력 IMMUTABLE), 선적당 살아 있는 CI 1 = `uq_commercial_invoices_shipment_id_live`. LOCK_ORDER 슬롯 이름 = `commercial_invoices`(X-17) | 커널 편입이 역순 가드·검산·상태이력·채번을 재구현 없이 승계(sA §A4) | 높음(DocKind 저장값 — ADR-0095) |
| **X-10** | **documents 소유자 존재 확인**: sA §A13 "테이블 이름 Core 조회" ↔ sC §C12 "소유자 해석기 레지스트리(`documents.owners.register_owner_resolver`, 등록 배선 `api/router.py`)" | **해석기 레지스트리(sC)** — owner_type별 규칙(SHIPMENT = EXPORT만, 전표 존재·삭제 여부)을 소유 모듈이 등록하고, documents는 플랫폼 모듈로서 전표·렌더 모듈을 임포트하지 않는다. **생성물 판정(X-02 ②)도 같은 레지스트리**(`register_generated_resolver`) | 규칙이 표마다 달라(수입선적 거부 등) 이름 조회 한 함수로는 분기가 documents에 쌓인다. 등록 배선 지점은 이미 허용 목록(`test:architecture/test_import_direction.py:75-80`) | 낮음 |
| **X-11** | **documents 전표 첨부 쓰기 역할**: sA §A13 선적·CI·S/I 소유에 LOGISTICS 추가 제안 ↔ sC §C6 ② owner_type ∈ 전표 6종 ⇒ A·T만(CERT 403을 404보다 먼저) | **sC(A·T)**. 기존 SKU·LABEL·CERTIFICATION·COMM_LOG 소유는 `CAN_MANAGE=(TRADE, CERT)`(`code:modules/documents/router.py:31`) 유지. 물류 B/L 사본 업로드는 부채 C-D6과 같은 트리거로 재판정 | 좁은 쪽, 여는 것은 판정 함수 1줄 | 낮음 |
| **X-12** | **Q-05 중량·CBM·박스**: sB B16 선적 **라인** 열(그램·0.001㎥, PLANNED 편집) ↔ sA §A7 PL 2표(그룹·내용물, 그램·mm 정수, CBM 파생) | **sA 확정**(sB는 "소유 = A, 권고"로 적었다) | 포장은 피킹·검수 뒤 확정되는데 선적 라인은 출고지시 후 동결, 혼합 포장 표현 불가(sA §A7 대안 (a)) | 중간(불변 표) |
| **X-13** | **Q-06 'TO ORDER'**: sB B16 `shipment_parties` CONSIGNEE 자유 문구 행(LC일 때만) ↔ sA §A8 S/I `consignee_mode`(PARTY·TO_ORDER·TO_ORDER_OF) + L/C 술어 `lc_allows_order_consignee`(없으면 False) | **sA 확정.** CI 수하인은 언제나 바이어, 지시식은 S/I 영역. 술어는 **sB(L/C) 소유로 PR-4a가 제공**(sE E3-6), 5b가 소비 | 선적 당사자 표에 비거래처 행을 넣으면 `partner_id NOT NULL` 계약(`code:modules/shipments/models.py:257-263`)이 깨진다 | 낮음 |
| **X-14** | sB B7 ③ "short-close 3열 — 한 번 쓰면 불변 **CHECK**" | **CHECK로 표현 불가 — sC §C5 3중 수단으로 정정**: ⓐ 일관성 CHECK(셋 다 NULL 또는 셋 다 NOT NULL·사유 ≥ 2자) ⓑ COMPLETED = 종결 상태(출구 엣지 0) ⓒ 3열 대입 앱 코드 = short-close 함수 1곳(아키텍처 스캔). 트리거 미채택(ADR-0028·0040 계보) | CHECK는 행의 현재 값만 본다 | 없음 |
| **X-15** | **채권 금액 DB 강제**: sB B2 복합 FK `(shipment_id, currency)` → `shipments(id, currency)` ↔ sC §C5 권고 `(shipment_id, currency, gross_amount)` → `shipments(id, currency, total_amount)` | **sC 채택**: `shipments`에 `UNIQUE(id, currency, total_amount)` 신설(M16), 복합 FK 3열(MATCH SIMPLE — OPENING은 NULL로 비검사). 위반 23503 → 409 `RECEIVABLES.RECEIVABLE.AMOUNT_MISMATCH`. `(shipment_id, so_id)`·`(so_id, partner_id)` 복합 FK(sB)는 그대로 | "채권 금액 = 선적 금액"을 야간 검산보다 강한 1차망으로(`D:374`), 채권이 살아 있는 동안 선적 `total_amount` UPDATE를 FK가 거부 | 낮음(인덱스·FK 교체) |
| **X-16** | **OPENING 이중 등록**: sB B14 무규정 ↔ sC §C5 `(partner_id, invoice_ref)` 부분 유니크 | **sC 채택**(`WHERE source_kind='OPENING' AND status <> 'CANCELLED'`, 409 `RECEIVABLES.OPENING.DUPLICATE_REF`) | 이월 반입의 최빈 사고 = 같은 송장 2회 입력 | 낮음 |

### 1.3 잠금·트랜잭션·version

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-17** | **LOCK_ORDER 슬롯**: sA §A14 `… shipment_children → commercial_invoices → approvals …` / sB B9 ② `receivables`를 `shipment_children` 뒤·`approvals` 앞 / sC §C2 `… sales_orders → lc_terms → purchase_orders → … shipment_children → trade_documents → receivables → approvals …` | **최종**: `idempotency_keys → order_intakes → partners → quotations → proforma_invoices → sales_orders → lc_terms → purchase_orders → shipments → shipment_children → commercial_invoices → receivables → approvals → lines → doc_number_seq`(§2.11). 슬롯은 **표가 생기는 PR에서만**(receivables = 2a, lc_terms = 4a, commercial_invoices = 5a — sE E1 ⑤). `shipment_children`에 `lc_presentations`·`lc_checklist_marks` 편입(sC T16·T17) | `D:362` ②(부분수열·변경은 ADR), 빈 슬롯은 계측 시험 공회전 | 중간(ADR-0097) |
| **X-18** | **채권 발생 잠금**: sB B9 ② "멱등 → 거래처 → PI `FOR SHARE` → SO `FOR UPDATE` → 선적 `FOR UPDATE`" ↔ sC §C2 ⑤ "PI SHARE 뒤 `lock_chain(SO)` = QT `FOR UPDATE`가 PI 뒤 + SHARE→UPDATE 승격 교착 → 채권 발생은 PI 무잠금" | **sC 채택**: 채권 발생(T8)은 PI를 잠그지 않는다(발생은 선적 `total_amount`만, 충당액은 파생). PI `FOR SHARE`는 **채권 입금(T11)·채권 분기 역기록(T12)에만**, 그 TX의 SO 잠금은 `lock_document(SO)` 직접(`lock_chain` 금지 — 신규 규칙). 아키텍처 계측 CJ-09가 "PI SHARE 이후 QT·PI UPDATE 0"을 단언 | `code:modules/trade_chain/chain_ops.py:45-77`(`ANCESTORS[SO]=(QT, PI)`), 40P01 구조적 차단 | 중간 |
| **X-19** | **역기록 분기 위치**: sB B5 ① "기존 `/payments/{id}/reversal`을 대상 열로 분기" ↔ 현행 `reverse_payment`가 peek 직후 `lock_chain(session, KIND, peek.pi_id)`(`code:modules/trade_chain/payment_flow.py:121-123`) — 채권 입금은 `pi_id` NULL이라 의미 없는 404(sC T12) | **sC 순서 채택**: peek → 대상 판별 → 거래처 잠금(`lock_buyer_for_credit` — 양 분기, 역기록 = 노출 증가) → 대상별 잠금(PI: `lock_chain(PI)` / 채권: T11과 같음). 독스트링 `code:modules/trade_chain/payment_flow.py:6` "거래처→QT→PI"를 실측(QT→PI)에 맞춘다 | 기존 결함 예방 | 낮음 |
| **X-20** | **노출 구성 변경 쓰기의 거래처 잠금**: sB B9 ① 대상 열거(발생·취소·입금·역기록·short-close) ↔ sC §C3 동일 + OPENING 포함·근거(문장 스냅샷 사이 창) | **sC 문면으로 통일**: 채권 발생(T8)·OPENING(T9)·채권 취소(T10)·채권 입금(T11)·역기록 양 분기(T12)·short-close(T14)는 `lock_buyer_for_credit`(`FOR NO KEY UPDATE` — `code:modules/credit/locking.py:32-42`) **선행**. PI 선수금 입금(T13)·선적 생성·출고지시·취소는 무변경(노출 구성 불변·감소 방향만). 아키텍처 단언: `open_order_amount`가 읽는 데이터 = SO 행 + provider `invoiced_by_sales_order`뿐(선적 표 무참조) | `code:modules/credit/evaluation.py:165-193`(SO 루프 뒤 provider — 서로 다른 문장 스냅샷), `D:362` ① | 낮음 |
| **X-21** | **R-3a-4 당사자 version·발행본 STALE**: sC §C4 ② "당사자·헤더 FREE 변경이 선적 version+1 → 발행본 `source_shipment_version`과 다르면 STALE 배지, CI↔PL 교차는 같은 version끼리만(422 `TRADE_DOCS.DOCUMENT.SOURCE_CHANGED`)" ↔ sA 모델(PL은 CI와 같은 TX — CI↔PL version 불일치 불가) | **부분 채택**: ① 당사자 추가·삭제가 선적 version+1(`code:modules/trade_chain/shipment_flow.py:1048` 부근 — R-3a-4 **해소**, 5a). ② **STALE = CI의 수하인·통지처 스냅샷 ≠ 현재 선적 CONSIGNEE·NOTIFY 행**(파생 판정 — version 비교가 아니라 값 비교: 내부 메모·담당자 변경의 거짓 STALE 0) → CI 상세 배지. ③ **S/I 발행 시 STALE이면 409 `EXPORT_DOCS.CI.SOURCE_CHANGED`**(CI 재발행 먼저 — S/I가 CI와 다른 수하인을 찍지 않게). 포워더 교체는 STALE이 아니다(S/I가 발행 시점 FORWARDER를 복사 — sA §A8). CI↔PL version 교차 규칙은 철회(같은 TX) | sC 우려(낡은 조합 발행)는 ③이 막고, 출고지시 뒤 포워더 교체(실무 정상)는 막지 않는다 | 낮음 |
| **X-22** | **CI 발행 잠금**: sA §A14 D3 "claim → shipments FOR UPDATE → shipment_children FOR SHARE → seq" ↔ sC T5(거래처·SO·receivables 포함) | **sA 채택 + receivables 읽기**: claim → shipments `FOR UPDATE`(+본문 `shipment_version`) → shipment_children `FOR SHARE`(당사자) → commercial_invoices(살아 있는 CI 검사 — 부분 유니크가 2차망) → receivables(살아 있는 채권 존재 읽기 → 409 X-01 ⑤; 채권 발생 T8도 선적 `FOR UPDATE`라 직렬화) → lines → `doc_number_seq`. SO·거래처 잠금 없음(X-01 ②) | X-01 | 낮음 |

### 1.4 권한·API·에러 코드

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-23** | **`GOVERNED_PREFIXES`**: sC §C6 ① +4(receivables·feature-flags·company-profile·**documents**) ↔ sD D1 +5(company-profiles·commercial-invoices·shipping-instructions·receivables·feature-flags) ↔ sE ADR-0098 +4 | **합집합 +6**: `/api/v1/company-profiles`·`/api/v1/commercial-invoices`·`/api/v1/shipping-instructions`·`/api/v1/receivables`·`/api/v1/feature-flags`·`/api/v1/documents`(현행 목록 `test:architecture/authz_matrix.py:25-53`에 documents 없음 — owner_type 확폭과 생성물 다운로드 축소가 같은 경로의 권한 의미를 바꾸므로 기존 documents 행 전부를 실측 역할 그대로 등재) | 매트릭스가 모르는 분기는 회귀 시험이 없다(sC 대안 (c)) | 낮음 |
| **X-24** | **렌더 경로**: sC §C6 `GET /quotations/{id}/document`(렌더 스트리밍)·`GET /shipments/{id}/trade-documents/{doc_id}/file` ↔ sA·sD `POST /quotations/{id}/render`(산출물 생성·멱등) + documents 다운로드 | **sA·sD 경로**(X-02 귀결). sC 경로 철회 | GET 부작용(쓰기) 0, 다운로드 통로 단일(sD D1 ②) | 낮음 |
| **X-25** | **CI·발행 경로 이름**: sC `POST /shipments/{id}/commercial-invoice`(단수)·`/packing-list`·`/shipping-instruction`·`…/void` ↔ sD CI1~CI11·SI1~SI5 | **sD 확정**(PL은 CI 본문의 `packages`로 같은 TX — 별도 경로 없음, 무효 = `/transitions {to: CANCELLED}`, 재발행 = `/reissue`) | sA 모델(PL ⊂ CI, 커널 전이) | 없음 |
| **X-26** | **에러 코드 도메인**: sC §C14 `TRADE_DOCS.DOCUMENT.{ALREADY_ISSUED, SOURCE_CHANGED, NOT_EXPORT}` ↔ sA §A17 `EXPORT_DOCS.*` | **sA 도메인**: `EXPORT_DOCS.CI.ALREADY_ISSUED`·`EXPORT_DOCS.CI.SOURCE_CHANGED`(409 — X-21)·`EXPORT_DOCS.SHIPMENT.KIND_NOT_SUPPORTED`(422 — 수입선적 서류). sC 3종 철회. 커널 공통 사유(`TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`·`TRADE_DOCS.DOCUMENT.FROZEN`·`TRADE_DOCS.TRANSITION.*`)는 재사용 | `TRADE_DOCS.*` = 전표 공통, `EXPORT_DOCS.*` = 서류 생성(sA §A17) | 없음 |
| **X-27** | **CI 상세의 은행 계좌번호**: sC §C7 ③ "PI 상세가 계좌번호를 전 역할에 싣는 기존 불일치는 고치지 않음(부채 C-D1), 새 채널은 축소" ↔ sD CI5 `GET /commercial-invoices/{id}` 전 역할(은행 블록 포함) | **CI 상세 응답의 `bank.account_no`는 A·T·L에게만**(C·V = 끝 4자리 외 마스킹 `****1234`). PI 기존 불일치는 부채 C-D1 유지 | 새 채널은 처음부터 좁힌다(sC C7 ③ 원칙을 응답에도 적용 — 렌더 다운로드만 좁히면 상세 JSON이 같은 값을 흘린다) | 낮음 |
| **X-28** | **receivables 목록 필터**: sB B12 ⑤ "필터 거래처·구간·상태" ↔ sD D2-5 "저장 열만 — 파생 필터 금지(페이지 정합·미수 정의 단일)" | **sD 채택**(구간·입금 상태 필터 없음 — aging 요약 탭으로). 부채 D-03 | 페이지 뽑은 뒤 거르면 `total` 불일치, SQL로 FIFO 재현하면 정의 2곳 | 낮음 |
| **X-29** | **사업자등록번호·적용 시작일 검증 코드 부재**(sA §A3 "체크섬 422"·"미래 판 금지", sD LH3 "422") | `EXPORT_DOCS.LETTERHEAD.BUSINESS_REG_NO_INVALID`(422)·`EXPORT_DOCS.LETTERHEAD.EFFECTIVE_IN_FUTURE`(422) 신설 | 카탈로그 1:1(`test:unit/test_error_catalog.py`) | 없음 |

### 1.5 잡·알림·시간

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-30** | **스캔 후보**: sB B13은 판정 대상만 추가 ↔ sC §C10 ②-1 실측 "현행 후보(`code:modules/trade_chain/deadline_scan.py:523-555` — 열린 저장형 행 또는 수리된 수출 통관)는 ETD 실적까지 들어간 동결 수출 선적을 빠뜨린다" | **sC 채택**: 후보 OR 확장 — "동결 이후 살아 있는 수출 선적 중 채권 없음 또는 미수 > 0" + "살아 있는 `lc_terms`를 가진 SO의 살아 있는 선적", OPENING 채권은 별도 후보 페이지(entity `receivables`). CH-05를 PR-6 **첫 커밋에 실패하는 시험으로 먼저** | fail-open 결함 예방(대금만기 무알림) | 낮음 |
| **X-31** | **미수 정의 위치**: sB B4 ① `receivables/service.py::outstanding_for` ↔ sC §C10 ②-2 쓰기 함수 없는 서브모듈 `receivables/outstanding.py`(스캔 허용 목록이 서브모듈 단위라 service면 쓰기 함수까지 열림) | **sC 채택**(`receivables/outstanding.py`) | `test:architecture/test_no_auto_confirm_code_path_exists.py:1029-1047` 허용 목록 구조 | 없음 |
| **X-32** | **대금만기 알림 수신자**: sB B13 ⑥·sC C10 ②-4 SO 담당자 ↔ S3-2 무역 기일 스캔은 선적 담당자 | **SO 담당자(무역) → 규칙 → ADMIN**(두 부록 일치 — 확인만). 선적 축 기존 4종(DOC_CUTOFF·CARGO_CLOSING·IMPORT_TAX_DUE·LOADING_DEADLINE)은 선적 담당 그대로 | 대금·L/C 서류는 무역 업무 | 낮음 |
| **X-33** | **R-6-4 라벨 대사**: sD D17 "pytest가 프런트 TS 라벨 키를 읽어 백엔드 열거와 대사(4표)" — 배치 PR 미정(sE에 없음) | **PR-6**(알림 종류 신설과 같은 PR — 트리거 '라벨 변경 시' 발동). 4표 중 ② 서류 검증 항목·④ 채권 상태/구간은 각 표가 생기는 PR(5a·2c)에서 대사 행을 더하고 PR-6이 ① 알림 종류·③ 파생 사유를 닫는다 | 표가 생기는 PR이 대사도 단다(sE E1 원칙) | 없음 |

### 1.6 PR·마이그레이션·번호

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-34** | **화면 PR 범위**: sE 가정 6·R10 "sD 부재 — sA·sB·sC 화면 소관 합집합으로 가정" ↔ sD 존재(D1~D20) | **sD로 재대사**: 2c = D11·D12·D13·D14(채권·대금만기 표시)·D15·D19(PR-16 ⑧ PI 라벨·R-8-2 셸)·D20 / 3c = D7·D8·D17(문서보관소 라벨·생성물 표시) / 4b = D16 + L/C 화면 / 5c = D9·D10 / 6 = D17 알림 이동 표·라벨 대사(X-33). 부채 E-05 **소멸** | sD 실재 | 없음 |
| **X-35** | **상태 총수**: sA §A4 31/153/184(CI 단독) ↔ sB B7 ⑤ 31/151/182(SO 단독) | **sE E3-8 합산**: 2b 후 31(18·13)/151/182 → 5a 후 32(19·13)/152/184. `code:modules/trade_docs/machine.py:7-8` 독스트링 동반, 5a는 2b가 고친 EXPECTED 위에서 재배치 | 손계산 — 각 PR 첫 커밋 실측 | 없음 |
| **X-36** | **마이그레이션 번호**: sA M16a~c·sB M-B1·M-B2 ↔ sE M16~M22 | **sE 번호**(§2.13) + 통합 가산 2건: M16에 `shipments UNIQUE(id, currency, total_amount)`(X-15), M21에 `receivables.ci_id`+FK(X-01 ③) | 쓰기 경로 없는 열·표 구간 0 | 병합 전 낮음 |
| **X-37** | **IMMUTABLE 계수**: sA §A15 "S3-2까지 11표 → 18표" | **실측 14표**(`code:core/db/table_policy.py:19-48` — audit_log·certification_status_log·QT/PI/SO/PO 상태이력 4·approval_events·payments·gate_evaluations·gate_overrides·shipment_status_log·milestone_changes·milestone_change_notices) → **22표**(+8: §2.3) | sA 계수 오류(정정) | 없음 |

### 1.7 누락(어느 부록도 소유하지 않은 것)

| # | 누락 | 해소(자율 확정) | 근거 | 번복 비용 |
|---|---|---|---|---|
| **N-01** | sD가 정한 기존 응답 확장 X1(선적 `export_docs`·`receivable` 블록)·X2(SO `short_close`·`receivables_summary`)·X4(마일스톤 보드 `settlement`·`presented_on`)·X6(documents `is_generated`)·D15(평가 응답 `opening_receivables_count`)의 **백엔드 PR 배치**가 sE에 없다 | X1 `receivable`·X2·X4 `settlement`·`is_overdue` = **2a** / X2 COMPLETED 분기·D15 = **2b** / X6 = **3b** / X4 `presented_on` = **4a** / X1 `export_docs` = **5a** | 응답 필드는 그 데이터가 생기는 PR이 낸다 | 없음 |
| **N-02** | 오더 보드 COMPLETED 처리(sD D13 — `EXCLUDED_SO_STATUSES`에 추가, 완결성 시험 `test:architecture/test_order_board_contract.py:72-78`이 COMPLETED의 RESERVED 이탈 순간 실패)가 sE 2b 범위에 없다 | **2b 같은 커밋 묶음**(SO 상태 기계 커밋 ③)에 백엔드 상수 1줄, 화면 문구는 2c | 같은 커밋 묶음 원칙 | 없음 |
| **N-03** | 생성물 다운로드 역할 축소·감사(X-02 ②③)의 PR·시험 배치 없음 | **3b**(renditions가 생기는 PR) — K: QT 생성물 L·C·V 403 / CI 생성물 C·V 403(5a), audit 1행·내용 0, documents 일반 파일은 무변경 | X-02 | 낮음 |
| **N-04** | 채권 등록 미리보기(sD RV1)·`will_complete`가 sB에 없다 | **2a에 RV1**(저장·잠금·채번·멱등 0, `will_complete`는 2a에선 항상 false — COMPLETED는 2b), 2b가 판정 배선 | sD D11 2단 대화상자 | 낮음 |
| **N-05** | `receivables.invoice_on` 하한(sB B2 "≥ SO 확정 KST일")의 코드 | `RECEIVABLES.RECEIVABLE.INVOICE_DATE_INVALID`(422 — 미래·확정일 이전 공통, `detail.reason`)로 통합(sB 목록 그대로) | 코드 증식 회피 | 없음 |
| **N-06** | OPENING `due_on < invoice_on`(sD RV7 422)의 코드 | `RECEIVABLES.OPENING.DUE_BEFORE_INVOICE`(422) 신설 | 카탈로그 1:1 | 없음 |
| **N-07** | 레터헤드 판 등록 TX가 순서표에 없다(sC T1은 UPDATE형이라 철회 — X-07) | **T1' 레터헤드 판 등록** = 멱등 → INSERT(잠금 0 — INSERT-only 표, 순서표 밖) | sA §A14 D1 | 없음 |
| **N-08** | `lc_terms` 행에 L/C 번호 열이 sB B10 ⑥ 최소 열에 없는데 sC §C5가 `(lc_number)` 부분 유니크를 건다 | `lc_terms.lc_number` VARCHAR(60) NOT NULL 가산(제어문자 금지·대문자 정규화는 서비스). 부분 유니크 `WHERE status='ACTIVE'`, 409 `LC_TERMS.LC.NUMBER_IN_USE`(1 L/C : N SO 완화 = 부채 C-D2) | 같은 L/C를 두 SO에 등록하면 tolerance 상한이 이중 사용된다 | 낮음 |
| **N-09** | `lc` 플래그 OFF 상태의 `lc_terms` 등록 거부 코드 | 기존 L/C 비활성 코드 재사용(`code:modules/trade_docs/payment_terms.py:140-152`의 거부 경로 — 코드명은 4a 첫 커밋 실측). 신설 0 | 입력 진입 단일 판정 | 없음 |
| **N-10** | 렌더 템플릿의 무상 표기('NO COMMERCIAL VALUE' — sA §A21 B ③)가 CI만 언급, QT·PI 무상 렌더(GC-A24)의 문구 단일 출처 없음 | 템플릿 공통 상수 `FREE_OF_CHARGE_TEXT`(doc_render L0 상수) — QT·PI·CI 동일 문구, K 시험 1행 | GC-A24 | 없음 |
| **N-11** | `force_shipment_status_for_test`(sA §A2 시험 전용 팩토리)의 앱 호출 0 스캔 소유 | **5a**의 K 시험(`app/` AST 스캔 — 함수 이름·raw `UPDATE shipments SET status` 리터럴 0) | 운영 게이트 우회 표면 차단 | 없음 |
| **N-12** | S3-3 신규 표가 `assignee_id`를 갖는가(handover 이관 대상) | **CI는 `assignee_id`를 갖는다**(TradeHeaderMixin — sA §A5 "선적 담당자 사본, FREE") → `ASSIGNMENT_TARGETS`에 `commercial_invoices`(shipments 뒤) 등재(5a). receivables·lc_terms·S/I는 없음(sC §C2 ⑦은 CI를 가칭 표로 보고 "추가 0"이라 함 — 정정) | `code:modules/handover/targets.py` "순서가 곧 잠금 순서" | 낮음 |

### 1.8 부록 문면 정정 색인 (PR-1 첫 커밋에서 표지 부착 — 이 세션은 부록 무수정)

| 부록 | 위치 | 통합 결정 |
|---|---|---|
| A | §0 계약 1·2줄(`live_invoice_date`·`ChildLink(CI, receivables, ci_id)`는 "채권 부록이 등록") | X-01·X-04 — 앵커 원천 = 채권, ChildLink는 **5a가** 등록 |
| A | §A11 단순 취소 "통관 기록 생존 무차단" | 유지. 단 살아 있는 채권은 차단(X-01 ④) |
| A | §A13 "documents 다운로드 = 전 역할(현행)"·§A16 표 마지막 행 | X-02 ②③ — 생성물 축소·감사 |
| A | §A13 전표 첨부 L 추가 제안 | X-11 — A·T |
| A | §A15 "11표 → 18표" | X-37 — 14 → 22 |
| A | §A16 CI 발행 A·T·L·레터헤드 조회 전 역할 | X-03·X-08 |
| A | §A20 "`live_ci_for_shipment` → 기일 앵커" | X-04 |
| B | 0-2 표 부록 문자(C·D·E) | 머리 '부록 문자 정정' |
| B | B1 ③·대안 (c)·B18 첫 행 "CI 경로가 생기면 닫힘" | X-01 |
| B | B2 `invoice_ref` NULL·복합 FK 2열 | X-06·X-15 |
| B | B7 ② 호출처·B7 ③ "불변 CHECK" | X-01 ⑥·X-14 |
| B | B9 ② 채권 발생 PI `FOR SHARE` | X-18 |
| B | B12 ⑤ 구간 필터 | X-28 |
| B | B16 표 처리안 4행 | X-07·X-12·X-13 |
| B | B19 PR-B1a/B1b/B2/B3 | §2.13·계획서 §4(2a·2b·4a·6) |
| C | §0-2 표 1행(`trade_documents` 가정)·T5·T6·T7 | X-01·X-09·X-22·X-25 |
| C | T1·C4 `company_profile`·C5 singleton·C6 `/company-profile`·CK-17 | X-07·X-08 |
| C | §C1 "렌더 결과 비저장"·T3·C6 렌더 경로 | X-02·X-24 |
| C | C2 `trade_documents` 슬롯 | X-17 |
| C | C4 ② CI↔PL version 교차·`SOURCE_CHANGED` 422 | X-21·X-26 |
| C | C6 documents L 미배정·CI 발행 A·T | X-11·X-03(일치 — 근거만 정정) |
| C | C11 "호출 파일 3개" | X-01 ⑥ |
| C | C14 `TRADE_DOCS.DOCUMENT.*` | X-26 |
| D | §0 가정 3(다운로드 전 역할)·LH1·LH2 역할 | X-02·X-08 |
| D | CI2·CI8·CI10 역할 A·T·L | X-03 |
| D | CI5 은행 블록 | X-27 |
| E | 가정 2·3·6, E-Q1·Q3·Q10, R10, 부채 E-05 | X-02·X-01·X-34 |
| E | E2 M16·M21 내용 | X-36 |
| E | E3-0 2a·2b·3b·5a 행 | N-01~N-03·N-11·N-12 |
| E | E4 ADR 0088·0093·0095 문면, 11건 | §3(12건 — 0099 추가) |
| E | E5-1 ⑦ "S4-2 = 채권 전용 엔드포인트 폐쇄" | X-01 ⑤ — S4-2는 '채권 선행 선적의 CI' 재판정 |
| E | E5-2 GC-H7 "채권·CI·S/I는 사람 1클릭에서만" | 유지 + "CI 발행이 채권을 만들지 않음" 단언 가산 |

---

## 2. 통합 스키마·상수·계약

### 2.1 신규 테이블 12개 (최종)

공통: 감사 컬럼(`created_at/updated_at/created_by_id/updated_by_id` — INSERT-only 표는 `created_*`만), CHECK는 `create_table` 안에서 `op.f()` 이름(함정 ①·⑪), 부분 유니크 술어 명시, 식별자 63자 이내(첫 커밋 실측), 전 FK `ondelete=RESTRICT`, 금액 = 정수 최소단위+통화, 날짜 = KST DATE, 시각 = TIMESTAMPTZ.

#### (a) `receivables` · MUTABLE · **열 단위 UPDATE**(`status`·`cancel_reason`·`cancelled_at`·`cancelled_by_id`·`version`·`updated_at`·`updated_by_id`) · M16 (sB B2 + X-06·X-15·X-16·X-01)

| 열 | 타입 | NULL | 비고 |
|---|---|---|---|
| `source_kind` | VARCHAR(10) | NOT NULL | `SHIPMENT`·`OPENING` |
| `partner_id` | BIGINT FK partners | NOT NULL | SHIPMENT = SO 바이어 — 복합 FK `(so_id, partner_id)` → `sales_orders(id, buyer_partner_id)` |
| `so_id` | BIGINT | NULL | SHIPMENT 필수 — 복합 FK `(shipment_id, so_id)` → `shipments(id, so_id)`(수입선적 구조적 불가) |
| `shipment_id` | BIGINT | NULL | SHIPMENT 필수 — **복합 FK `(shipment_id, currency, gross_amount)` → `shipments(id, currency, total_amount)`(X-15)** |
| `ci_id` | BIGINT FK commercial_invoices | NULL | **M21 가산(X-01 ③)** — CI가 있을 때 등록된 채권만. `ChildLink(CI, receivables, ci_id)` |
| `currency` | CHAR(3) | NOT NULL | |
| `gross_amount` | BIGINT | NOT NULL | 0 ≤ x ≤ 2^53−1(무상 선적 0 허용) |
| `fx_rate`·`fx_rate_date` | NUMERIC(18,8)·DATE | NULL | SHIPMENT = SO 환율 사본, OPENING = 입력(통화 ≠ KRW 필수) — `fx_pair`·`krw_fx_is_one` 승계 |
| `invoice_on` | DATE | NOT NULL | ≤ KST 오늘, SHIPMENT ≥ SO 확정 KST일. **INVOICE_DATE 앵커 단일 원천(X-04)** |
| `invoice_ref` | VARCHAR(60) | **NOT NULL**(X-06) | 비유니크(OPENING만 부분 유니크 X-16)·제어문자 금지 |
| `due_on` | DATE | NULL | **OPENING만 필수, SHIPMENT는 NULL**(CHECK) — 선적 채권 만기는 파생 |
| `note` | VARCHAR(500) | NULL | OPENING 근거 메모(필수 — 서비스) |
| `status` | VARCHAR(10) | NOT NULL | `OPEN`·`CANCELLED` |
| `cancel_reason`·`cancelled_at`·`cancelled_by_id` | | NULL | CANCELLED ⇔ 3열 NOT NULL·사유 ≥ 2자 |
| `deleted_at` | TIMESTAMPTZ | NULL | CHECK `IS NULL`(ChildLink 생존 술어 호환) |
| `version` | INTEGER | NOT NULL | 취소 낙관 잠금 |

CHECK: `source_kind_valid` · `source_shape`(`(source_kind='SHIPMENT' AND so_id·shipment_id NOT NULL AND due_on NULL) OR (source_kind='OPENING' AND so_id·shipment_id·ci_id NULL AND due_on NOT NULL)`) · `gross_range` · `cancel_triple` · `never_deleted` · `due_after_invoice`(OPENING) · `currency_format` · `fx_pair`·`krw_fx_is_one`.
인덱스: `uq_receivables_shipment_live (shipment_id) WHERE status <> 'CANCELLED'` · `uq_receivables_opening_ref_live (partner_id, invoice_ref) WHERE source_kind='OPENING' AND status <> 'CANCELLED'` · `uq_receivables_id_partner_currency (id, partner_id, currency)`(payments 복합 FK 대상) · `ix_receivables_partner_open (partner_id) WHERE status='OPEN'` · `ix_receivables_so_open (so_id) WHERE status='OPEN'` · `ix_receivables_ci_id`.

#### (b) `company_profiles` · **IMMUTABLE** · M18 (sA §A3 그대로 — X-07)
열·CHECK·as-of 규칙은 sA §A3·§A19 M16a 초안이 정본. 수정 = 새 판 INSERT, 삭제 경로 0, `letterhead_as_of(session, day)` = `effective_from ≤ day` 중 `(effective_from DESC, id DESC)` 첫 행, 없으면 None(대체 금지).

#### (c) `trade_document_renditions` · **IMMUTABLE** · M19 (sA §A13 + sE 단계 확장)
`source_type`·`source_id`·`doc_kind`·`format`·`language`·`template_version`·`renderer_version`·`document_id`(FK documents **UNIQUE**)·`created_*`. UNIQUE `(source_type, source_id, doc_kind, format, language)`. CHECK `source_type`: **M19 = QT·PI만 → M21 +COMMERCIAL_INVOICE(doc_kind CI·PL) → M22 +SHIPPING_INSTRUCTION**(쓰는 경로 없는 값 거부 — sE E2). CI·PL·SI ⇒ `language='EN'`.

#### (d) `lc_terms` · MUTABLE · VersionMixin · M20 (sB B10 ⑥ + N-08)
`so_id` FK · `lc_number`(N-08) · `status`(`ACTIVE`·`SUPERSEDED`·`CANCELLED`) · `supersedes_id` FK self · `expiry_on` · `presentation_days`(1~365, 기본 21 — `code:modules/trade_docs/schedule.py:32-33`) · `tenor`(`SIGHT`·`USANCE`) · `usance_days`(USANCE ⇔ NOT NULL) · `lc_amount` BIGINT · `currency`(복합 FK `(so_id, currency)` → `sales_orders(id, currency)` — 실측 UNIQUE 존재 여부 4a 첫 커밋, 없으면 M20에서 신설) · `tolerance_plus_bp`·`tolerance_minus_bp`(0~10000) · `latest_shipment_on`. 부분 유니크 `(so_id) WHERE status='ACTIVE'`(409 `LC_TERMS.LC.ALREADY_ACTIVE`)·`(lc_number) WHERE status='ACTIVE'`(409 `NUMBER_IN_USE`). 개정 = 이전 행 SUPERSEDED + 신규 INSERT(1TX).

#### (e) `lc_presentations` · MUTABLE · VersionMixin · M20 (sB B10 ⑥ "제시 기록", sC T16)
`shipment_id` FK(선적 1:0..1 — `uq_lc_presentations_shipment_live WHERE deleted_at IS NULL`) · `presented_on`·`negotiated_on`·`accepted_on` DATE NULL(≤ KST 오늘). 정정 = audit `lc_terms.presentation.corrected`. 선적이 L/C SO 소속이 아니면 422 `LC_TERMS.LC.NOT_LC_SHIPMENT`.

#### (f) `lc_checklist_marks` · **IMMUTABLE** · M20 (sC T17·C8)
`shipment_id` FK · `item_code`(하자 체크 항목 — L0 StrEnum) · `checked` BOOLEAN · `evidence` VARCHAR(500) NOT NULL · `prev_mark_id` FK self NULL(직전 마크 대조 — 불일치 409 `LC_TERMS.CHECKLIST.STALE_MARK`) · `created_*`. 현재값 = 항목별 최신 마크(파생). **'적합'·'하자 없음' 판정 열·상태 0**(CI-09 — §15 법적 판정 L3 금지).

#### (g) `commercial_invoices` · MUTABLE · **열 단위 UPDATE**(`status`·`internal_note`·`assignee_id`·`version`·`updated_at`·`updated_by_id`) · DocKind `COMMERCIAL_INVOICE`·접두어 `CI` · M21 (sA §A5)
열·CHECK·유니크는 sA §A5·§A19 M16b 초안이 정본. 통합 가산: 없음(채권 열을 두지 않는다 — 연결은 `receivables.ci_id` 단방향). `source_shipment_version` 열은 **두지 않는다**(X-21 — STALE은 값 비교 파생).

#### (h) `commercial_invoice_lines` · **IMMUTABLE** · M21 (sA §A6)
#### (i) `commercial_invoice_status_log` · **IMMUTABLE** · M21 (`status_log_checks` 동형)
#### (j) `packing_list_packages` · **IMMUTABLE** · M21 (sA §A7 — G.W. ≥ N.W. CHECK·mark 범위)
#### (k) `packing_list_package_items` · **IMMUTABLE** · M21 (sA §A7)
#### (l) `shipping_instructions` · **IMMUTABLE** · 채번 `SI`(비커널) · M22 (sA §A8)

### 2.2 기존 테이블 변경 (전량)

| 테이블 | 변경 | 마이그레이션 | 비고 |
|---|---|---|---|
| `payments` | `pi_id` NOT NULL 해제 + `receivable_id` FK + CHECK `num_nonnulls(pi_id, receivable_id) = 1` + `UNIQUE(id, receivable_id, received_currency)` + 역기록 복합 FK 2벌째 + `(receivable_id, partner_id, received_currency)` → `receivables(id, partner_id, currency)` 복합 FK + 인덱스 | M16 | **기존 열·CHECK·kind 값 무변경**(`W:129`), 번역표 `CONSTRAINT_ERRORS` 확장. payments에 `partner_id` 열이 없으면(실측) 3열 FK 대신 서비스 검증 + 2열 `(receivable_id, received_currency)` 복합 FK로 축소(2a 첫 커밋 실측 기록) |
| `sales_orders` | `UNIQUE(id, buyer_partner_id)`(M16) · short-close 3열 + 일관성 CHECK(M17) | M16·M17 | |
| `sales_order_status_log` | `reason_required` CHECK 재정의(COMPLETED 포함 — `REASON_REQUIRED_TO[SO]` = {CANCELLED, ON_HOLD} + COMPLETED, `code:modules/trade_docs/machine.py:237-243`) | M17 | 수기 drop/create, 정의문 시험 |
| `shipments` | `UNIQUE(id, so_id)` · **`UNIQUE(id, currency, total_amount)`(X-15)** | M16 | 기존 `uq_shipments_id_currency`(`code:modules/shipments/models.py:142`) 유지 |
| `documents` | `owner_type` VARCHAR(13) → **VARCHAR(24)** · CHECK에 QUOTATION·PROFORMA_INVOICE·SALES_ORDER·SHIPMENT·COMMERCIAL_INVOICE·SHIPPING_INSTRUCTION 6종(`code:modules/documents/models.py:68,107`) | M18 | PURCHASE_ORDER·ORDER_INTAKE 미개방(원가 채널·S6-1) |
| `document_types` | 시드 QUOTATION·PROFORMA_INVOICE(M19)·COMMERCIAL_INVOICE·PACKING_LIST(M21)·SHIPPING_INSTRUCTION(M22) | M19·M21·M22 | 기준 표 시드 관용(`retention_years NULL`) |
| DocKind 값을 담는 CHECK 전수(상태이력·outbox aggregate·doc_number_seq 등) | `COMMERCIAL_INVOICE` 가산 | M21 | 5a 첫 커밋 실측 목록 |
| `receivables` | `ci_id` 가산(X-01 ③) | M21 | |
| `credit/exposure.py` | `open_order_amount(order, invoiced)` = 총액 − 채권 전환분(provider 공급), `CLOSED_STATUSES` 무변경(`code:modules/credit/exposure.py:19,37-39`) | — | 2b |
| `feature_flags` | **없음**(기존 표 `code:modules/platform/models.py:67-77` — 첫 PUT이 INSERT, 시드 0) | — | 4a |

### 2.3 table_policy·불변·시드·users FK (신규 12표 전량)

| 표 | table_policy | `_NEVER_SEEDED` | users FK 분류 |
|---|---|---|---|
| receivables | MUTABLE + **COLUMN_UPDATE_ALLOWLIST** | 등재 | `created_by_id`·`cancelled_by_id`·감사 → ACTOR_LOG/ACTOR |
| company_profiles | **IMMUTABLE** | 등재 | `created_by_id` → ACTOR_LOG |
| trade_document_renditions | **IMMUTABLE** | 등재 | ACTOR_LOG |
| lc_terms·lc_presentations | MUTABLE | 등재 | 감사 2열 → ACTOR |
| lc_checklist_marks | **IMMUTABLE** | 등재 | ACTOR_LOG |
| commercial_invoices | MUTABLE + **COLUMN_UPDATE_ALLOWLIST** | 등재 | `assignee_id` → ASSIGNMENT_TARGETS(shipments 뒤 — N-12), 감사 → ACTOR |
| commercial_invoice_lines·_status_log·packing_list_packages·packing_list_package_items·shipping_instructions | **IMMUTABLE** | 등재 | ACTOR_LOG |

IMMUTABLE **14 → 22**(X-37), COLUMN_UPDATE_ALLOWLIST **3 → 5**(approvals·approval_lines·delegations + commercial_invoices·receivables — `code:core/db/table_policy.py:189-215`). 허용 열 목록은 마이그레이션이 리터럴로 넘긴다(앱 상수 임포트 금지). DESIGN §17.5 확장 등재 + ADR(각 기능 ADR 본문에 §17.5 항목 동석 — sE E4). stock_movements 무접촉.

### 2.4 열거·레지스트리 단일 출처 (변경 전량)

| 위치 | 변경 | PR |
|---|---|---|
| `trade_docs/constants.py` | `DocKind.COMMERCIAL_INVOICE` + 커널 dict 11행(sA §A4), `CI_ISSUABLE_SHIPMENT_STATES={INSPECTED, RELEASED, SHIPPED}`, `SHIPPING_INSTRUCTION_PREFIX="SI"`, 검증 항목 StrEnum(sA §A17 20종), 하자 체크 항목 StrEnum | 4a·5a |
| `trade_docs/machine.py` | SO: `AUTO_TRANSITIONS` += (IN_SHIPMENT, COMPLETED), `RESERVED[SO]` −= COMPLETED, `TERMINAL[SO]` += COMPLETED, `REASON_REQUIRED_TO[SO]` += COMPLETED / CI: 2상태·HUMAN 1·TERMINAL {CANCELLED}·EDITABLE ∅·RESERVED ∅ | 2b·5a |
| `trade_docs/chain.py` | `CHILD_LINKS` += (SHIPMENT, receivables, shipment_id)[2a]·(SHIPMENT, commercial_invoices, shipment_id)[5a]·**(COMMERCIAL_INVOICE, receivables, ci_id)[5a — X-01 ④]**, `NON_CHILD_FK_ALLOWLIST` += payments.receivable_id·receivables.so_id·CI so_id/supersedes/copied_from·CI 라인 3·상태이력·PL·S/I 2(사유 10자↑) | 2a·5a·5b |
| `trade_docs/locking.py` | LOCK_ORDER §2.11(슬롯은 표 생성 PR별) | 2a·4a·5a |
| `trade_docs/policy.py` | `FIELD_POLICY["commercial_invoices"]`(sA §A15 — FREE = internal_note·assignee_id) | 5a |
| `trade_docs/transition.py` | `PAYLOAD_KEYS` += `cause_receivable_id`(자동 전이) | 2b |
| `trade_docs/schedule.py` | `AnchorContext.invoice` 가산·INVOICE_DATE 분기만(산식 본문 무변경) | 2a |
| `trade_docs/doc_validation.py`(신규, L0 순수) | V1~V14(sA §A10), `incoterms.py`에 `incoterms_completeness` 가산 | 5a |
| `credit/providers.py` | Protocol += `invoiced_by_sales_order(session, so_ids) -> dict[int, int]`(기본 `{}`) | 2b |
| `credit/evaluation.py` | provider 먼저 1회(SAVEPOINT) → SO 묶음(SO 잔여 + 그 SO 미수) 1회 HALF_UP 환산, `ReceivableNotConvertible` → `CURRENCY_NOT_CONVERTIBLE`, 응답 `opening_receivables_count` 가산(D15) | 2b |
| `app/bootstrap.py`(신규) | `register_runtime_providers()` — `is_default_provider()`일 때만 등록(`register_receivable_provider` 2회 = RuntimeError — `code:modules/credit/providers.py:55-60`), API·worker·CLI 3 입구 | 2b |
| `trade_chain/milestone_view.py` | PAYMENT_DUE: 앵커 invoice·`settlement`·미충족 시 `is_overdue`(2a) / PRESENTATION_DEADLINE: `lc_inputs_for` 배선·`presented_on`(4a) — `code:modules/trade_chain/milestone_view.py:378-393` | 2a·4a |
| `trade_chain/deadline_scan.py` | 후보 OR 확장·대상 4종(PAYMENT_DUE·PRESENTATION_DEADLINE·LC_EXPIRY·LC_LATEST_SHIPMENT)·OPENING 후보·SO 담당 수신·발송 직전 재확인 | 6 |
| `order_board/constants.py` | `EXCLUDED_SO_STATUSES` += COMPLETED(N-02) | 2b |
| `documents`(플랫폼) | `owners.register_owner_resolver`·`register_generated_resolver`(X-10), 생성물 삭제 잠금·다운로드 역할·audit(X-02) | 3a·3b |
| `handover/targets.py` | `ASSIGNMENT_TARGETS` += commercial_invoices(N-12) | 5a |
| `platform`(feature flags) | `FEATURE_FLAG_REGISTRY = {"lc": "L/C 결제"}` 폐쇄 + PUT/GET | 4a |
| `idempotency`·`api/deps.py` | 키 1~128자·제어문자 금지 → 422 `COMMON.IDEMPOTENCY.KEY_INVALID`(`code:api/deps.py:94-109`, `code:modules/idempotency/models.py:37`) | 1b |
| 프런트 `lib/alert-routes.ts`·`lib/doc-status.ts`·`lib/labels.ts`·`lib/milestone.ts` | `receivables`·`commercial_invoices` 이동·라벨, CI 상태 라벨, PI '선수금 입금완료', owner_type 6종, `DERIVED_REASON` 개정 | 2c·3c·5c·6 |

### 2.5 상태 총수 (갱신 후 핀)

| 문서 | 상태 | 허용(사람·자동) | 미허용 | 쌍 |
|---|---|---|---|---|
| QT | 5 | 6 (3·3) | 14 | 20 |
| PI | 5 | 8 (1·7) | 12 | 20 |
| SO | 8 | **11 (8·3)** | **45** | 56 |
| PO | 6 | 3 (3·0) | 27 | 30 |
| SHIPMENT | 8 | 3 (3·0) | 53 | 56 |
| **COMMERCIAL_INVOICE** | **2** | **1 (1·0)** | **1** | **2** |
| **합** | | **32 (19·13)** | **152** | **184** |

2b 단독 시점 = 31(18·13)/151/182. SO 자동 엣지 3 = CONFIRMED↔IN_SHIPMENT 2 + IN_SHIPMENT→COMPLETED 1, 셋 다 "RECEIVED에서 CONFIRMED로 오지 않는다"(4금 — §7). COMPLETED 불변식: 살아 있는 선적 전부 살아 있는 채권 보유 + 살아 있는 채권 ≥ 1 + (전 라인 잔량 0 또는 `short_closed_at` NOT NULL). 되돌림 엣지 0.

### 2.6 에러 코드 — 신규 41종

| 코드 | HTTP | 상황 | 출처 |
|---|---|---|---|
| `EXPORT_DOCS.SHIPMENT.NOT_INSPECTED` | 409 | 선적 ∉ `CI_ISSUABLE_SHIPMENT_STATES` | sA |
| `EXPORT_DOCS.SHIPMENT.KIND_NOT_SUPPORTED` | 422 | 수입선적 CI·S/I·미리보기 | sA(+sC `NOT_EXPORT` 흡수) |
| `EXPORT_DOCS.PREVIEW.SHIPMENT_NOT_FROZEN` | 409 | PLANNED 선적 CI 미리보기 | sD |
| `EXPORT_DOCS.CI.ALREADY_ISSUED` | 409 | 선적에 살아 있는 CI | sA |
| `EXPORT_DOCS.CI.RECEIVABLE_EXISTS` | 409 | 살아 있는 채권이 먼저 있는 선적의 CI 발행 | 통합 X-01 ⑤ |
| `EXPORT_DOCS.CI.SOURCE_CHANGED` | 409 | STALE CI로 S/I 발행 | 통합 X-21(sC 422 정정) |
| `EXPORT_DOCS.VALIDATION.FAILED` | 422 | V1~V14 BLOCK·미확인 NOTICE(`detail.items`) | sA |
| `EXPORT_DOCS.LETTERHEAD.NOT_REGISTERED` | 409 | 판 0 | sA |
| `EXPORT_DOCS.LETTERHEAD.NOT_EFFECTIVE` | 409 | as-of 판 없음 | sA |
| `EXPORT_DOCS.LETTERHEAD.BUSINESS_REG_NO_INVALID` | 422 | 사업자번호 체크섬 | 통합 X-29 |
| `EXPORT_DOCS.LETTERHEAD.EFFECTIVE_IN_FUTURE` | 422 | `effective_from > today_kst()` | 통합 X-29 |
| `EXPORT_DOCS.SI.CI_NOT_LIVE` | 409 | S/I 원천 CI 취소 | sA |
| `EXPORT_DOCS.SI.FORWARDER_MISSING` | 422 | FORWARDER 당사자 없음 | sA |
| `EXPORT_DOCS.SI.CONSIGNEE_MODE_NOT_ALLOWED` | 422 | 지시식 + L/C 근거 없음 | sA |
| `EXPORT_DOCS.RENDITION.SOURCE_NOT_FROZEN` | 409 | QT DRAFT 렌더 | sA |
| `EXPORT_DOCS.RENDITION.PENDING` | 409 | 산출물 없음 | sA |
| `EXPORT_DOCS.RENDITION.LANGUAGE_NOT_SUPPORTED` | 422 | CI·PL·S/I에 KO | sD |
| `DOCUMENTS.DOCUMENT.GENERATED_LOCKED` | 409 | 생성물 삭제 | sA |
| `DOCUMENTS.OWNER.KIND_NOT_ALLOWED` | 422 | 수입선적 첨부 등 | sA |
| `RECEIVABLES.RECEIVABLE.SHIPMENT_NOT_FROZEN` | 409 | PLANNED 선적 | sB |
| `RECEIVABLES.RECEIVABLE.NOT_EXPORT` | 422 | 수입선적 | sB |
| `RECEIVABLES.RECEIVABLE.ALREADY_OPEN` | 409 | 선적당 살아 있는 1(부분 유니크 번역) | sB |
| `RECEIVABLES.RECEIVABLE.NOT_OPEN` | 409 | 취소 채권 입금·재취소 | sB |
| `RECEIVABLES.RECEIVABLE.PAYMENTS_EXIST` | 409 | 순입금 ≠ 0 취소 | sB |
| `RECEIVABLES.RECEIVABLE.SO_COMPLETED` | 409 | COMPLETED SO 채권 취소 | sB |
| `RECEIVABLES.RECEIVABLE.LC_AMOUNT_EXCEEDED` | 422 | tolerance 상한 초과 | sB |
| `RECEIVABLES.RECEIVABLE.INVOICE_DATE_INVALID` | 422 | 미래·SO 확정일 이전(N-05) | sB |
| `RECEIVABLES.RECEIVABLE.AMOUNT_MISMATCH` | 409 | 금액 복합 FK 위반(X-15) | sC |
| `RECEIVABLES.RECEIVABLE.INVOICE_FIELDS_FROM_CI` | 422 | CI가 있는데 `invoice_on`·`invoice_ref` 송신 | 통합 X-01 ③ |
| `RECEIVABLES.OPENING.DUPLICATE_REF` | 409 | (거래처, invoice_ref) 중복 | sC |
| `RECEIVABLES.OPENING.DUE_BEFORE_INVOICE` | 422 | 만기 < 인보이스일 | 통합 N-06 |
| `PAYMENTS.PAYMENT.POSSIBLE_DUPLICATE` | 409 | 이중 입력 의심(`detail.candidates` — 금액 0) | sB·sD |
| `SALES_ORDERS.SHORT_CLOSE.NOT_IN_SHIPMENT` | 409 | | sB |
| `SALES_ORDERS.SHORT_CLOSE.NOTHING_TO_CLOSE` | 409 | | sB |
| `SALES_ORDERS.SHORT_CLOSE.SHIPMENT_PENDING` | 409 | 채권 없는 살아 있는 선적(`detail.shipments`) | sB |
| `PLATFORM.FEATURE_FLAG.UNKNOWN_CODE` | 404 | 레지스트리 밖 | sB |
| `COMMON.IDEMPOTENCY.KEY_INVALID` | 422 | 키 길이·제어문자(P-39) | sC |
| `LC_TERMS.LC.ALREADY_ACTIVE` | 409 | SO당 살아 있는 1 | sC |
| `LC_TERMS.LC.NUMBER_IN_USE` | 409 | L/C 번호 중복(N-08) | sC |
| `LC_TERMS.LC.NOT_LC_SHIPMENT` | 422 | 제시 기록·체크 마크 대상 선적이 L/C SO 아님 | sC |
| `LC_TERMS.CHECKLIST.STALE_MARK` | 409 | 직전 마크 불일치 | sC |

**재사용(신설 금지)**: `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(채권·CI 생존 선적 취소, 채권 생존 CI 취소·재발행) · `TRADE_DOCS.TRANSITION.NOT_ALLOWED`·`REASON_REQUIRED` · `TRADE_DOCS.DOCUMENT.FROZEN` · `PAYMENTS.PAYMENT.CURRENCY_MISMATCH`·`EXCEEDS_DUE` · `COMMON.CONCURRENCY.VERSION_CONFLICT`·`LOCK_BUSY`(55P03·40P01) · `COMMON.RESOURCE.NOT_FOUND` · `COMMON.AUTH.FORBIDDEN` · 기존 L/C 비활성 코드(N-09). **철회 3**: sC `TRADE_DOCS.DOCUMENT.{ALREADY_ISSUED, SOURCE_CHANGED, NOT_EXPORT}`(X-26). 규칙: 3세그먼트·조치 힌트·카탈로그 1:1·detail 금액 0(`EXCEEDS_DUE`의 미수는 기존 PI 입금 detail 실측 형태를 따름)·log_context 분리. 검증 항목 코드(`detail.items[].code`)는 에러 코드가 아니라 L0 StrEnum(sA §A17 20종).

### 2.7 이벤트·audit

| 이벤트(outbox) | 동작 | payload(화이트리스트 — 금액·원가·계좌번호·참조 텍스트·사유 원문 0) |
|---|---|---|
| `commercial_invoices.commercial_invoice.created`·커널 전이 | T5·T7 | ci_id·doc_number·shipment_id·so_id·partner_id·revision_no(sA §A20의 currency·total은 **제외** — sC C7 ⑤ 우선) |
| `shipping_instructions.shipping_instruction.created` | T6 | si_id·doc_number·ci_id·shipment_id |
| `receivables.receivable.opened`·`.cancelled` | T8·T9·T10 | receivable_id·source_kind·shipment_id·so_id·partner_id·ci_id |
| `payments.payment.recorded`·`.reversed`(기존) | T11·T12 | 기존 + `receivable_id`(pi_id와 정확히 하나) |
| 커널 SO 전이(COMPLETED) | T8·T14 | 커널 `PAYLOAD_KEYS` + `cause_receivable_id` |
| `sales_orders.sales_order.short_closed` | T14 | so_id·doc_number |
| `lc_terms.lc.registered`·`.amended`·`.cancelled` | T15 | lc_terms_id·so_id·superseded_id |
| `lc_terms.presentation.recorded` | T16 | shipment_id·presentation_id |

**이벤트 0**: 렌더·생성물 다운로드·체크 마크·플래그 토글·레터헤드 판 등록(구독자 없음 — 감사로 충분, 소비자 없는 이벤트 금지). 기본 `alert_rules` 시드 0, 대외 채널 0.
audit(`AuditAction`): `company_profiles.company_profile.created` · `documents.generated.downloaded`(X-02 ③) · `receivables.receivable.opened`·`.cancelled`·`receivables.opening.registered` · `payments.duplicate.acknowledged`(확인 id 목록) · `lc_terms.lc.registered`·`.amended`·`.cancelled`·`lc_terms.presentation.corrected` · `platform.feature_flag.changed`. 업무 TX 안(롤백 동반). SO COMPLETED·CI 전이는 상태이력이 정본(audit 이중 0).

### 2.8 잡 — JOB_REGISTRY **14 유지**

| 시각(KST) | 코드 | S3-3 변경 |
|---|---|---|
| 05:30 | `trade-docs-totals-verify` | CI DocKind가 `DOC_TABLES`·`LINE_TABLES` 규약을 따라 **자동 편입**(`code:modules/trade_docs/verify.py:1-8`) — 코드 변경 0, CH-12로 확인(5a) |
| 06:40 | `trade-deadline-scan` | 대상 += PAYMENT_DUE(충족 = 미수 0, 채권 없음 = 미충족)·PRESENTATION_DEADLINE(충족 = `presented_on`)·LC_EXPIRY·LC_LATEST_SHIPMENT(충족 = ETD·BL 실적)·OPENING 만기(entity `receivables`), 후보 OR 확장(X-30), UNRESOLVED 1회(적재 실적 있을 때만), SO 담당 수신(X-32), 발송 직전 재확인(`deferred`), 허용 임포트 += `receivables.models`·`receivables.outstanding`·`payments.models`·`sales_orders.models`·`lc_terms.models`(X-31) |
| 나머지 12행 | 기존 | 무변경 |

4금(두 확장 공통): 상태 전이 0·대외 발송 0·원장 쓰기 0(receivables·payments **읽기만**). 갱신 위치: `test:architecture/test_scheduler_registry.py:149-201`(집합·총수 14·시각 무변경 — 주석만), `test:architecture/test_no_auto_confirm_code_path_exists.py:977-997`(`_trade_chain_imports` = {expiry_sweep, deadline_scan} **무변경 통과가 정답**)·`:1029-1066`(허용 목록), `test:integration/test_trade_deadline_scan.py:760`(`SCAN_TYPES`), runbook 잡 표, DESIGN §15. G-09(L/C CLI)는 **구현 없이 종결**. aging 집계 잡·파일 정리 잡 **만들지 않는다**.

### 2.9 AUTHZ 행 (최종 — X-03·X-08·X-11·X-23·X-27)

`GOVERNED_PREFIXES` += `/api/v1/company-profiles`·`/api/v1/commercial-invoices`·`/api/v1/shipping-instructions`·`/api/v1/receivables`·`/api/v1/feature-flags`·`/api/v1/documents`(X-23). QT·PI·SO·선적 하위 신규 경로는 행만.

| 엔드포인트 | A | T | L | C | V | PR |
|---|---|---|---|---|---|---|
| `GET /company-profiles`·`/as-of` | ✓ | ✗ | ✗ | ✗ | ✗ | 3a |
| `POST /company-profiles` | ✓ | ✗ | ✗ | ✗ | ✗ | 3a |
| `POST /quotations/{id}/render`·`/proforma-invoices/{id}/render` | ✓ | ✓ | ✗ | ✗ | ✗ | 3b |
| `GET /documents/{id}/download` — **QT·PI 생성물** | ✓ | ✓ | ✗ | ✗ | ✗ | 3b |
| `GET /documents/{id}/download` — **CI·PL·S/I 생성물** | ✓ | ✓ | ✓ | ✗ | ✗ | 5a |
| `GET /documents/{id}/download` — 일반 파일 | 기존(전 역할) | | | | | — |
| `POST /documents`(owner_type ∈ 전표 6종) | ✓ | ✓ | ✗ | ✗(403, 404보다 먼저) | ✗ | 3a |
| `DELETE /documents/{id}`(생성물) | 409 `GENERATED_LOCKED`(역할 무관) | | | | | 3b |
| `POST /shipments/{id}/commercial-invoices/preview`·`/commercial-invoices/{id}/reissue/preview` | ✓ | ✓ | ✓ | ✗ | ✗ | 5a |
| `POST /shipments/{id}/commercial-invoices`·`/commercial-invoices/{id}/transitions`·`/reissue` | ✓ | ✓ | ✗ | ✗ | ✗ | 5a |
| `PATCH /commercial-invoices/{id}`(FREE 2열)·`POST …/render` | ✓ | ✓ | ✓ | ✗ | ✗ | 5a |
| `GET /commercial-invoices`·`/export.csv`·`/{id}`(계좌번호 A·T·L만 — X-27)·`/status-log` | ✓ | ✓ | ✓ | ✓ | ✓ | 5a |
| `GET /commercial-invoices/{id}/shipping-instructions`·`/shipping-instructions/{id}` | ✓ | ✓ | ✓ | ✓ | ✓ | 5b |
| `POST …/shipping-instructions[/preview]`·`/shipping-instructions/{id}/render` | ✓ | ✓ | ✓ | ✗ | ✗ | 5b |
| `POST /shipments/{id}/receivable[/preview]` | ✓ | ✓ | ✗ | ✗ | ✗ | 2a |
| `GET /receivables`·`/export.csv`·`/aging`·`/{id}`·`/{id}/payments` | ✓ | ✓ | ✓ | ✓ | ✓ | 2a |
| `POST /receivables/opening`·`/receivables/{id}/cancel` | ✓ | ✗ | ✗ | ✗ | ✗ | 2a |
| `POST /receivables/{id}/payments`·`POST /payments/{id}/reversal`(기존 행) | ✓ | ✓ | ✗ | ✗ | ✗ | 2a |
| `POST /proforma-invoices/{id}/payments`(기존 — `acknowledge_duplicates` 가산) | 기존 | | | | | 2a |
| `POST /sales-orders/{id}/short-close` | ✓ | ✓ | ✗ | ✗ | ✗ | 2b |
| `GET /sales-orders/{id}/lc-terms` / `POST·PATCH …/lc-terms…` | ✓/✓ | ✓/✓ | ✓/✗ | ✓/✗ | ✓/✗ | 4a |
| `GET` / `PUT /shipments/{id}/lc-presentation` · `GET` / `POST …/lc-checklist/marks` | ✓/✓ | ✓/✓ | ✓/✗ | ✓/✗ | ✓/✗ | 4a |
| `GET /feature-flags`·`PUT /feature-flags/{code}` | ✓ | ✗ | ✗ | ✗ | ✗ | 4a |

부모-자식(경로) 404·부작용 0, 본문 소속 불일치 422(S3-2 X-12 선례), 401→403→404→409→422(ADR-0079 ⑧). 쓰기 스키마 전부 `extra="forbid"`, CI 발행 본문에 금액·통화·환율·단가 필드 구조적 부재. 응답 `allowed_actions`는 위 표와 같은 상수에서 생성(sD D4 — `ACTION_ENDPOINTS` ↔ 매트릭스 대사 K). QT·PI·SO 응답의 `allowed_actions`는 **S3-3 신규 동작만**(부분 집합 — 부채 D-01).

### 2.10 모듈 계층 (import-direction 등재)

| 모듈 | 계층 | 내용 |
|---|---|---|
| `trade_docs` | L0 | DocKind·상수·`doc_validation.py`(순수)·`incoterms` 완전성·검증 항목 StrEnum |
| `doc_render` | **플랫폼 리프(신규)** | 템플릿 레지스트리·레이아웃·폰트·글리프 커버리지 — 앱 모듈·DB·네트워크·`now`·float 금액 0, **`reportlab`·`openpyxl` 임포트는 이 모듈만** |
| `letterhead` | **S3_PLATFORM(신규)** | `company_profiles` 모델·as-of·판 등록(전표 무임포트 — sC C12의 '플랫폼' 배치 + sA 모듈명) |
| `renditions` | **L1(신규)** | `trade_document_renditions` 모델·조회(3b — CI 모듈보다 먼저 생기므로 분리) |
| `commercial_invoices` | **L1(신규)** | CI·라인·상태이력·PL 2표·S/I 모델·조회·CSV(선적·SO 모델 무임포트 — 테이블명 FK) |
| `lc_terms` | **L1(신규)** | `lc_terms`·`lc_presentations`·`lc_checklist_marks` 모델 |
| `receivables` | **L2 + L2_NO_CHAIN(신규)** | 모델·`outstanding.py`(미수 단일 정의 — 쓰기 0)·`exposure.py`(provider 구현 — `credit.providers` 임포트) |
| `credit` | L2(기존) | **`receivables` 임포트 금지**(신규 단언) |
| `trade_chain` | L2 | `receivable_flow.py`·`short_close_flow.py`·`export_doc_flow.py`·`lc_flow.py`·`lc_inputs.py`·렌더 오케스트레이션 |
| `documents` | 기존(S3 밖) | 전표·렌더 모듈 임포트 금지 유지 — 해석기 레지스트리(X-10) |
| `app/bootstrap.py` | **ALLOWED_OUTSIDE_IMPORTERS 추가** | provider 등록 |

`today_kst` 이름 임포트 지점 커버리지: `TODAY_IMPORT_POINTS`(1b — `test:support/kst.py:19` 일반화) ⊇ `app/modules/{trade_chain,receivables,payments,lc_terms,commercial_invoices,renditions,letterhead}` 아래 이름 임포트 모듈(아키텍처 시험 — 누락 즉시 실패, sC C13 ③).

### 2.11 LOCK_ORDER (ADR-0097 — ADR-0078 개정)

```
idempotency_keys → order_intakes → partners → quotations → proforma_invoices
→ sales_orders → lc_terms → purchase_orders → shipments → shipment_children
→ commercial_invoices → receivables → approvals → lines → doc_number_seq
```
- 신규 슬롯 3: `lc_terms`(SO 하위 — 4a), `commercial_invoices`(선적 후속 — 5a), `receivables`(CI 후속·approvals 앞 — 2a). `shipment_children` += `lc_presentations`·`lc_checklist_marks`(4a).
- **신규 규칙 ① 노출 구성 변경 쓰기는 `lock_buyer_for_credit` 선행**(X-20). **신규 규칙 ② PI를 `FOR SHARE`로 잡은 TX는 SO를 `lock_chain`으로 잡지 않는다 — `lock_document(SO)` 직접**(X-18).
- 순서표 밖(독립 TX·MVCC 읽기): `company_profiles`(INSERT-only)·`feature_flags`·`payments`(INSERT-only)·`alerts`·`trade_document_renditions`(INSERT-only·유일 키 경쟁 해소).
- 계측(CJ-09): T5~T17 첫 접촉 순서 = 색인 오름차순(부분수열), PI SHARE 이후 QT·PI UPDATE 0.

### 2.12 트랜잭션 경계 T1~T22 (sC §C1 정정본)

| # | 동작 | 잠금(LOCK_ORDER 순) | 쓰기 | 변경점(통합) |
|---|---|---|---|---|
| T1 | 레터헤드 판 등록 | 멱등 → INSERT(잠금 0) | company_profiles | **N-07 — sC T1(UPDATE) 대체** |
| T2·T4 | 미리보기(QT·PI·CI·S/I·채권) | 읽기(잠금 0) | 0 | 채번·이벤트·audit·멱등 소비 0 |
| T3 | 렌더(QT·PI·CI·SI) | TX 밖 렌더·파일 쓰기 → TX2: 멱등 → documents·renditions INSERT(유일 위반 = 승자 반환·내 파일 삭제) | documents·renditions | **X-02 — 저장형** |
| T3' | 생성물 다운로드 | 짧은 TX: 역할 판정 → audit INSERT → 커밋 → 스트리밍 | audit 1 | X-02 ③ |
| T5 | CI(+PL) 발행 | 멱등 → shipments `FOR UPDATE`(+`shipment_version`) → shipment_children `FOR SHARE` → commercial_invoices(살아 있는 CI) → receivables(존재 읽기 → 409) → lines → seq | CI·라인·PL·상태이력·outbox | **X-01 ②·X-22 — 거래처·SO 잠금·채권 INSERT 삭제**. TX 밖 자동 렌더 4 |
| T6 | S/I 발행 | 멱등 → shipments `FOR SHARE` → shipment_children `FOR SHARE`(STALE 판정 X-21) → commercial_invoices `FOR SHARE` → seq | S/I·outbox | TX 밖 자동 렌더 2 |
| T7 | CI 취소·재발행 | 멱등 → shipments `FOR UPDATE` → shipment_children `FOR SHARE` → commercial_invoices `FOR UPDATE` → (ChildLink 채권 생존 검사) → lines → seq | 취소 + 신규 CI(재발행 1TX) | sA §A11 |
| T8 | 채권 발생 | 멱등 → partners `FOR NO KEY UPDATE` → SO `FOR UPDATE`(`lock_document`) → shipments `FOR UPDATE`(+version) → commercial_invoices `FOR SHARE`(살아 있는 CI 값 복사) → receivables INSERT. `lc_terms` 읽기(무잠금 — SO 잠금이 개정과 직렬화) | receivables·(2b부터) SO COMPLETED 수렴·audit·outbox | **PI 무잠금(X-18)**, CI 복사(X-01 ③) |
| T9 | OPENING 등록 | 멱등 → partners NKU | receivables | |
| T10 | 채권 취소(ADMIN) | 멱등 → partners NKU → SO `FOR UPDATE` → shipments `FOR SHARE` → receivables `FOR UPDATE`+version | 상태 열(열 GRANT) | |
| T11 | 채권 입금 | 멱등 → partners NKU → PI `FOR SHARE`(있으면) → SO `FOR SHARE`(`lock_document(read)`) → receivables `FOR UPDATE` | payments | `lock_chain` 금지 |
| T12 | 역기록(대상 분기) | 멱등 → peek → partners NKU → PI 분기 `lock_chain(PI)` / 채권 분기 T11과 같음 | payments(음수) | X-19 |
| T13 | PI 입금(기존) | 무변경 | — | 이중 입력 의심만 가산(P-14) |
| T14 | short-close | 멱등 → partners NKU → SO `FOR UPDATE`+version → shipments(id순 `FOR SHARE`) → receivables 읽기 | SO 3열 + COMPLETED | |
| T15 | lc_terms 등록·개정·취소 | 멱등 → SO `FOR UPDATE` → lc_terms `FOR UPDATE`+version | | |
| T16 | 제시 기록 | 멱등 → SO `FOR SHARE` → shipments `FOR SHARE` → shipment_children(`lc_presentations` `FOR UPDATE`+version) | | |
| T17 | 하자 체크 마크 | 멱등 → shipments `FOR SHARE` → shipment_children(직전 마크 대조) | 마크 INSERT | |
| T18 | 플래그 토글 | 멱등 → feature_flags `FOR UPDATE`+version(없으면 INSERT — `unique_active`가 동시 최초 409) | | |
| T19 | documents 전표 첨부 | 기존 경로(파일 IO TX 밖) + 해석기 소유 확인 | | X-10·X-11 |
| T20 | 기일 스캔 확장 | 건별 독립 TX·잠금 0 | alerts | |
| T21 | 검산(CI 자동 편입) | 읽기 | ADMIN 알림 | |
| T22 | 채권·aging 읽기 | 읽기 | 0 | |

트랜잭션 안 외부 호출·파일 IO·렌더 CPU 0(`D:358`). 한 요청 한 '오늘'(진입점 `today_kst()` 1회 주입 — sC C13 ②).

### 2.13 마이그레이션 DAG (7건, 직렬)

| 번호 | 내용 | `down_revision` | PR |
|---|---|---|---|
| **M16** `s33_receivables_payments` | `receivables`(+열 GRANT·부분 유니크 2) + `payments` 확장 + `sales_orders UNIQUE(id, buyer_partner_id)` + `shipments UNIQUE(id, so_id)`·**`UNIQUE(id, currency, total_amount)`** | `281da4794717`(현 단일 head — `mig:20261004_1753_281da4794717_s32_milestones_customs.py`) | 2a |
| **M17** `s33_so_short_close` | SO short-close 3열 + 일관성 CHECK + SO 상태이력 `reason_required` 재정의 | M16 | 2b |
| **M18** `s33_company_profiles_documents_owner` | `company_profiles`(+REVOKE) + `documents.owner_type` 확폭·CHECK 6종 | M17 | 3a |
| **M19** `s33_trade_document_renditions` | `trade_document_renditions`(+REVOKE, source_type QT·PI) + `document_types` 2행 | M18 | 3b |
| **M20** `s33_lc_terms` | `lc_terms`(+`lc_number`)·`lc_presentations`·`lc_checklist_marks`(+REVOKE) | M19 | 4a |
| **M21** `s33_commercial_invoices` | CI 5표(+REVOKE 4·열 GRANT 1) + DocKind CHECK 전수 확장 + renditions CHECK +CI·PL + `document_types` 2행 + **`receivables.ci_id`**+FK | M20 | 5a |
| **M22** `s33_shipping_instructions` | `shipping_instructions`(+REVOKE) + renditions CHECK +SI + `document_types` 1행 | M21 | 5b |

공통: 드라이런 4종(heads 단일·빈 DB upgrade·base 왕복·`alembic check` 드리프트 0, `D:396`), 시드 = `document_types`만, CHECK 수기 재정의 4건(M17·M18·M21·M22)은 정의문 시험 양방향, **downgrade는 새 값·새 행이 1건이라도 있으면 RAISE**(데이터 소실 다운그레이드 금지 — S3-3 전 리비전 공통 규율), `owner_type` 확폭은 ACCESS EXCLUSIVE 짧은 잠금 — runbook 배포 절차에 "업로드 없는 시간대" 1줄. PR-1·1b·2c·3c·4b·5c·6·7은 마이그레이션 0.

---

## 3. ADR 목록 (0088~0099) — PR-1에서 일괄 등재, 5줄 서식, 상태 전부 "자율 확정 — 사후 번복 가능"

| ADR | 제목(요지) | 원천 | 구현 PR |
|---|---|---|---|
| **0088** | 채권 모델 — 수출 선적당 살아 있는 1·발생 = 사람 1클릭 **전용 엔드포인트 단일 경로**(CI 발행은 채권을 만들지 않음, CI가 있으면 `invoice_on`·`invoice_ref` 서버 복사·`ci_id`, 채권 선행 선적의 CI 409 — S4-2 재판정)·`invoice_ref` 필수·금액 복합 FK·미수 파생 단일 정의·aging 30/60/90 + `DUE_UNKNOWN`·INVOICE_DATE 앵커 = 채권 `invoice_on`·OPENING 이월(P-51)·채권 취소 ADMIN | sB B1·B2·B4·B12·B14·B15, X-01·X-04·X-06·X-15·X-16 | 2a(·5a `ci_id`) |
| **0089** | `payments` 확장(P-09)·채권 입금·역기록 대상 분기(peek → 거래처 잠금 → 대상)·이중 입력 의심 409+ack(P-14 — PI 경로 포함)·통화 불일치 422 유지·초과 422·EXPIRED/CANCELLED PI 입금 409 유지(P-12)·선수금 충당 FIFO(P-15 채권 항) | sB B3·B5, X-19 | 2a |
| **0090** | 여신 노출 전환 — provider 실구현 3 입구 등록(P-08)·**채권 전환분 차감**(P-01)·SO 묶음 1회 환산·환산 불가 = UNEVALUABLE·노출 구성 변경 쓰기 = 거래처 잠금 선행·선적 경로 무변경 단언 | sB B6·B8·B9, sC C3, X-20 | 2b |
| **0091** | SO COMPLETED 자동 엣지 1·short-close(사람 결정 3열·사유 — 불변 = 일관성 CHECK+종결 상태+단일 대입 통로)·완결 판정 함수 1개(호출처 2)·오더 보드 제외·총수 31/151/182 — **ADR-0076 대체** | sB B7, sC C5·C11, X-14, N-02 | 2b |
| **0092** | 자사 레터헤드 불변 판 + as-of(대체 금지·관리자 과거 유효일 판 = 유일한 해소)·Shipper 블록 = 레터헤드(R-3a-5, 선적 SHIPPER 422 유지)·P-11·조회 A 전용·로고 없음 | sA §A3, X-07·X-08 | 3a |
| **0093** | 서류 렌더링 — 서버 내 PDF(ReportLab invariant)·XLSX(openpyxl, 문자열 셀 수식 방어)·동봉 NanumGothic(sha256)·템플릿 판 동결·외부 호출 0·**산출물 저장(documents FILE + 불변 renditions, 첫 파일 정본)**·생성물 삭제 잠금·**생성물 다운로드 역할 축소 + audit**·QT·PI 운영 개방(DRAFT 0)·주입 방어 | sA §A12·A13, sC C7, X-02·X-24 | 3b |
| **0094** | L/C — `lc_terms`(lc_number·개정 SUPERSEDED)·제시 기록·하자 체크 마크(IMMUTABLE·판정 필드 0)·**S3-2 산식 배선만(재정의 금지)**·tolerance 상한 초과 채권 422·플래그 토글(P-10, 폐쇄 레지스트리)·**OFF = 입력만**(§20 H 해석)·`lc_allows_order_consignee`(없으면 False)·G-09 종결 | sB B10·B11, sC T15~T18, X-13, N-08 | 4a |
| **0095** | CI 커널 편입(DocKind `COMMERCIAL_INVOICE`·접두어 CI)·상태 2·사람 엣지 1·PL 동반(번호 공유, Q-05)·S/I 비커널 불변(채번 SI, Q-06)·재발행 = 새 번호 1TX·발행 A·T·계좌번호 응답 마스킹·당사자 version+1(R-3a-4)·STALE 값 비교·총수 32/152/184 | sA §A4~A11, X-03·X-09·X-21·X-27 | 5a·5b |
| **0096** | CI·PL·S/I **검수 게이트 선배치**(INSPECTED·RELEASED·SHIPPED)·운영 경로 S4-2까지 닫힘·DoD = 서비스 층 시험(시험 전용 팩토리 — ADR-0081 구조)·미리보기 RELEASE_ORDERED부터·runbook 외부 작성 안내(A-05) | sA §A2, N-11 | 5a |
| **0097** | LOCK_ORDER S3-3 개정 — `lc_terms`·`commercial_invoices`·`receivables` 슬롯·**PI SHARE 뒤 `lock_chain` 금지**·채권 발생 PI 무잠금·노출 구성 변경 쓰기 거래처 잠금 | sC C2·C3, sB B9, X-17·X-18·X-20 | 2a·4a·5a |
| **0098** | S3-3 권한·마스킹 — §2.9 표·`GOVERNED_PREFIXES` +6(documents 통제 편입)·documents 전표 첨부 A·T·생성물 다운로드 축소·원가 채널 0(PO·수입선적 첨부·수입선적 서류 미개방)·계좌번호 로그·audit·outbox 0·렌더 주입·원격 리소스 로드 차단 | sC C6·C7, sA §A13·A16, X-08·X-11·X-23·X-27 | 2a·3a·3b·4a·5a |
| **0099** | S3-3 화면 계약 — 서버 `allowed_actions`(S3-3 신규 동작만 — 부분 도입)·서류 파일 단일 다운로드 통로·CI 작성 전용 화면(원천 선적 경로)·멱등 키 화면 수준 Map(`useIdempotencyKeys`)·목록 파생 필터 금지·aging 기준일 서버 고정·CI 목록 메뉴 S4-2 | sD D1·D4·D6·D9·D20, X-28·X-34 | 2c·3c·4b·5c |

**기존 ADR 부기**(번호 없이 "부기 2026-10-xx" — 15건): 0076(**대체** → 0091) · 0064(노출 산식 문면) · 0078(LOCK_ORDER 승계 → 0097) · 0081(운영 경로 배선 이행 — 4a) · 0084(스캔 대상·후보 확장·JOB 14 유지 — 6) · 0074(SHIPPER 자사 = 레터헤드·CI 총수) · 0054(접두어 CI·SI) · 0028(documents owner 6종) · 0029(생성 파일 다운로드 역할·감사) · 0024(원가 10번째 채널 미개방 — 렌더·첨부·PO 발주서) · 0067·0079(LOGISTICS 범위 — S/I·CI 미리보기, CI 발행 제외) · 0073(durations 재갱신 시점) · 0086(S3-3 워크스루 3층 적용) · 0066(보드 제외 집합 COMPLETED).
**ADR 대상 아님**: PR 분할·마이그레이션 번호(일정), P-39(기존 결함 수정), R-3a-4(결함 해소 — 0095 본문에 동석), IMMUTABLE·열 GRANT(기능 ADR 본문 §17.5 항목).

---

## 4. DESIGN 부기 목록 (PR-1, 원문 불변·[M5] 보강 문단 "S3-3 계획 — 자율 확정 2026-10-05")

§3 표 맵(receivables·payments 확장·company_profiles·renditions·lc_terms 3표·CI 5표·S/I) · §4.7 [M1](owner_type 확폭·6종·PO·ORDER_INTAKE 제외·생성물 삭제 잠금·생성물 다운로드 역할·문서 종류 5행) · §7.1(사슬 `D:177` "선적→CI/PL→…→채권"에 대한 **문면 변경**: 채권은 CI 없이 선적에서 발생할 수 있고 CI가 있으면 CI 값을 복사 — X-01) · §7.2 [M4](CI 상태 2·엣지 1, **CI/PL 게이트 = INSPECTED·RELEASED·SHIPPED — 운영 경로 S4-2까지 닫힘**, SO COMPLETED 자동 엣지·short-close, 총수 32/152/184) · §7.6 [M4](서류 매트릭스·DGD/B-L/PO 발주서 범위 밖·B/L draft 대조 S5-3·검증 카탈로그 BLOCK/NOTICE·렌더 방식·산출물 정본 = 첫 렌더 파일·재발행 = 새 번호) · §7.10 [M4](채권 모델·미수 정의·**노출 산식 "미결 SO 잔여(총액 − 채권 전환분)" — 문면 변경**·SO 묶음 환산·aging·`lc` OFF 의미·OPENING) · §14 [M4](S3-3 화면 배정·CI 목록 메뉴 S4-2·보드 COMPLETED 제외) · §15(SO 자동 엣지 2 → 3 **문면 개정**·4금 논증·잡 14 유지·스캔 대상 확장·B/L draft 자동 적합 기록 미개방) · §17.2(LOCK_ORDER 3슬롯·PI SHARE 뒤 `lock_chain` 금지·노출 구성 변경 쓰기 거래처 잠금) · §17.4(P-39 키 길이·OPENING 유니크·채권 금액 복합 FK) · §17.5(IMMUTABLE 14 → 22·열 GRANT receivables·commercial_invoices) · §2·§18.1(S3-3 권한 행·`GOVERNED_PREFIXES` +6·`allowed_actions` 대사) · §18.4(파생값 필터 금지·aging 기준일 서버 고정) · §20 해석 주(S3-3 매핑 A·B·G·H·I·K + J, B 차단 본체 = S3-3 서비스 층 + S4-2 운영 층, **H "플래그 오프 완전 비활성" = 입력 진입 비활성 — 해석**, K L/C = API 경로 배선).

---

## 5. WBS v1.7·GC v1.6 갱신안

### 5.1 WBS v1.7 (S3-3 행 문면 원본 유지, 주석으로 — sE E5-1 + 통합 정정)

| 행 | 주석 문면(요지) | 원천 |
|---|---|---|
| S3-3(`W:129`) ① | "CI·PL·S/I 템플릿 렌더링" → **CI·PL·S/I 운영 발행은 S4-2 INSPECTED 개방까지 닫힘**(검수 게이트 선배치), DoD "교차 불일치 거부·무상 생성"은 서비스 층 시험으로 충족. QT·PI 렌더는 운영 개방 | ADR-0096 |
| S3-3 ② | "receivables(만기 자동)" → 만기 **저장 없이 파생**(OPENING만 저장), 발생 = 사람 1클릭 **전용 경로(CI와 분리)** | ADR-0088 |
| S3-3 ③ | "lc_terms(feature flag)" → OFF = 새 입력만 닫음 | ADR-0094 |
| S3-3 ④ | DESIGN §7.6의 DGD는 WBS 산출물 밖 → S4-4, PO 발주서 렌더 미구현(원가 채널), B/L draft 대조는 S5-3 | sA §A1 |
| S3-3 ⑤ | 검증 매핑 보강: §20 P3 그룹 **A·B·G·H·I·K + J**(E 해당 없음), GC-A22~A31·F5·F6·G4·H7(14건), PR 15·마이그레이션 7(M16~M22) 정본 = `docs/plans/s3-3-plan.md` | §5.2 |
| S3-4(`W:138`) ⑥ | Phase 3 리허설 "QT→채권 관통"의 CI·PL 단계 = **시스템 밖 작성(runbook A-05)**, 채권은 전용 경로로 발생 — S3-4 착수 세션이 '관통' 문면 재판정 | ADR-0096·0088 |
| S4-2(`W:150-155`) ⑦ | INSPECTED 개방 PR이 ① `CI_ISSUABLE_SHIPMENT_STATES` 실발효 ② runbook 외부 작성 문장 삭제·운영 관통 1회(A-05) ③ **채권 선행 선적의 CI 처리(`RECEIVABLE_EXISTS` 409) 재판정과 채권 경로 단일화 여부**(X-01 ⑤) ④ CLOSED 선적 재발행(A-04) ⑤ CI 목록 화면·메뉴(D-02) | ADR-0088·0096 |
| 골든 매핑표(`W:224-241`) | S3-3 행: A22~A31·F5·F6·G4·H7 | §5.2 |
| 변경 이력(`W:247`) | v1.7 항목(자율 확정, ADR-0088~0099, 세션 분할·순서 변경 없음) | — |

### 5.2 GC v1.6 — 14건 (S3-3 배정 0건 해소, 추가만 — 현재 끝 GC-A21·F4·G3·H6 `GC:125`·`:229`·`:244`·`:272`)

| GC | 내용(경계 값 정본 = sA §A21·sB §B21·sC §C15) | PR |
|---|---|---|
| **A22** | CI↔PL 라인 수량 불일치 → 422, 행 0·번호 소비 0 (DoD) | 5a |
| **A23** | G.W.<N.W. 거부(서비스 422 + DB CHECK 23514)·**G.W.=N.W. 통과** | 5a |
| **A24** | 무상(금액 0) 서류 — QT/PI 무상 렌더 'NO COMMERCIAL VALUE'(N-10)·무상 CI 발행(은행 블록 없음) (DoD) | 3b·5a |
| **A25** | 검수 미완료 선적 CI·PL 생성 차단 — 실제 경로 RELEASE_ORDERED 409·술어 확장 변이 kill (§20 B) | 5a |
| **A26** | 채권 일부입금 전환 UNPAID→PARTIALLY_PAID→PAID→역기록 PARTIALLY_PAID·초과 422 (WBS 검증 A) | 2a |
| **A27** | aging 구간 경계 8점·만기 미상 = `DUE_UNKNOWN`(미도래 아님)·통화 간 합산 0 (DoD) | 2a |
| **A28** | 노출 공백 0·이중 0 — TT_DEFERRED 채권 발생 전후 노출 정확히 불변·TT_ADVANCE 충당·KRW 묶음 환산 동일·3 입구 provider ≠ 기본 (DoD) | 2b |
| **A29** | SO COMPLETED·short-close — 전량 채권화 → COMPLETED / 사유 필수·채권 없는 선적 409 / COMPLETED 후 새 선적·채권 취소 409 | 2b |
| **A30** | 대금만기 배선 — INVOICE_DATE = 채권 `invoice_on`(없으면 UNKNOWN) / **CI가 있으면 `invoice_on` = CI `doc_date`(본문 송신 422)** / L/C API 경로(제시기한 MIN·대금만기) / tolerance 상한 경계 포함·초과 422 | 2a·4a·5a |
| **A31** | 대금만기·제시기한 알림 — 미수 0·제시 기록 = 충족(0)·도과 1·실적 다 들어간 선적도 후보(회귀)·OPENING 이동처 존재 | 6 |
| **F5** | 노출 구성 변경 경합 — 평가 중간 Barrier에 채권 발생 → 평가 노출 ∈ {전, 후}(거래처 잠금 제거 변이에서 실패) | 2b |
| **F6** | 같은 선적 채권 발생 실제 동시 2건 → 1 성공·1 409, 500·40P01 0 | 2a |
| **G4** | 서류 원가 채널 0 — 렌더 컨텍스트·발행 스냅샷·첨부(PO 소유 미개방·수입선적 첨부 422·수입선적 서류 422)에 원가 키 0 | 3a·3b·5a |
| **H7** | 청구 기록·서류 자동 생성 0 — 채권·CI·S/I는 사람 1클릭에서만, **CI 발행이 채권을 만들지 않음**, COMPLETED는 채권 발생·short-close TX 안에서만, 발행이 대외 발송 이벤트 0 | 2b·5a |

`pytest -m golden` 대사: S3-2 종결 85 + 14 → **PR-7에서 99 이상**. PR마다 자기 배정 케이스 마커를 같은 PR에서 단다.

---

## 6. 부채 이월·소비 대사 (조용한 누락 금지)

### 6.1 S3-3 소유분 처리 (S3-2 부채 최종 목록 `P:56-171` + S3-1 등재 P-xx `P:1614-1675` + S3-1 종결 목록 `P:1088`)

| ID | 내용 | 원 소유·트리거 | S3-3 처리 | 결과 |
|---|---|---|---|---|
| **Q-04 = P-02** | SO COMPLETED·short-close | S3-3 provider PR | 구현(2b — ADR-0091, ADR-0076 대체) | 종결 |
| **Q-05** | 중량·CBM·박스 | S3-3 PL | PL 2표(5a — X-12) | 종결 |
| **Q-06** | 'TO ORDER' 수하인 | S3-3 L/C | S/I `consignee_mode`(5b) + 술어(4a) | 종결 |
| **Q-08**(+R-6-2) | 대금만기·제시기한 알림 | S3-3 receivables·lc_terms | 스캔 확장(6 — X-30·X-32) | 종결 |
| **R-3a-5** | SHIPPER(자사) 자리 | S3-3 서류 | 레터헤드 판 = Shipper 블록(3a·5a) | 종결 |
| **P-01** | 선적분 노출 차감 | S3-2/S3-3 | 채권 전환분 차감(2b — provider와 같은 PR) | 종결 |
| **P-08** | 미수 provider 등록·기본 잔존 금지 시험 | S3-3 DoD | 3 입구 부트스트랩(2b) | 종결 |
| **P-09** | payments 확장 | S3-3 | M16(2a) | 종결 |
| **P-10** | L/C 플래그 토글 | S3-3 | PUT `/feature-flags/{code}`(4a) | 종결 |
| **P-11** | 자사 레터헤드 | S3-3 | 불변 판(3a) | 종결 |
| **P-12** | EXPIRED/CANCELLED PI 입금 | S3-3 재판정 | **재판정: 409 유지**(잔금은 채권으로 — runbook 새 PI 발행) | 종결(판정) |
| **P-13** | documents 전표 첨부·확폭 | S3-3·S6-1 | 확폭·6종(3a). PURCHASE_ORDER(원가)·ORDER_INTAKE(S6-1) 미개방 | S3-3 몫 종결 — ORDER_INTAKE 이월 S6-1(P-23), PO 소유 이월(부채 A-02와 같은 트리거) |
| **P-14** | 입금 이중 입력 | S3-3·P7 | 409+ack(2a, PI 경로 포함) | S3-3 몫 종결 — 은행 CSV 매칭 P7 이월 |
| **P-15** | 선수금 노출 미차감 | S3-3 | **재판정**: 채권 항 = 충당 후 미수, SO 항 = 미차감 유지 | 종결(판정) — SO 항 차감은 신규 부채 B-04 |
| **P-51** | 이월 미수 | S3-3 재판정 | OPENING 등록(2a)·runbook 순서(7)·여신 카드 안내(2c) | 종결 |
| **P-39 = PR-16 ⑨** | Idempotency-Key 길이 | 코어 소규모 | 422(1b) | 종결 |
| **PR-16 ⑧** | PI '입금완료' = 선수금 기준 | S3-3 | 라벨 '선수금 입금완료'(2c) | 종결 |
| **R-3a-4** | 당사자 변경 version 미증가 | 선적 / 혼선 1건 | **재트리거(서류 스냅샷 의존) → 해소**(5a — X-21) | 종결 |
| **R-6-4** | 알림 라벨 별도 출처 | 라벨 변경 시(발동) | 라벨 대사 시험(6 + 표 생성 PR — X-33) | 종결 |
| **R-8-2** | 셸 역할 코드 원문 | 화면 정리 세션 | `roleLabel` 적용(2c — 셸 수정 PR) | 종결 |
| **G-09** | L/C CLI 소단위 | P-10 | 구현 없이 종결(화면·API로 대체) | 종결 |
| **P-56** | SKU 용량 컬럼 | S3-3·S5-1·§7.7 중 먼저 | **재판정: 열 미신설** — CI `description_en` MANUAL로 우회(sA §A6) | 이월(S5-1·§7.7) |
| **Q-15** | durations 재갱신 | CI | 1b 재갱신 + 2b·5a 후 재갱신 | 이월(E-01로 계속) |
| **Q-17** | DG 수동 점검 공백 | S3-4 | 부분 완화 — 서류 발행 NOTICE `DG_MANUAL_CHECK`(5a, 운영 발행 0이라 실효는 S4-2) | 이월 S3-4 |
| **R-3b-8·P-44** | 거래처 영문 편집 부재 | 거래처 | CI 한정 주소 보충 탈출로(5a)만 | 이월 |
| **R-3b-4** | 품명 칸 최소 폭 8곳 | 해당 화면 수정 | 부분 — S3-3이 만지는 QT·PI·SO 상세 품명 칸만(2c·3c) | 나머지 이월 |
| **R-3b-9·R-4b-4** | '확인 없이 닫기' 키 상실 | 중복 1건 | 신규 대화상자는 D20 회피, 기존 이월 | 이월(D-07) |
| **R-3c-3** | CSV 상태 = 코드값 | 요청 1건 | 신규 CSV도 같은 규칙 | 이월 |
| **R-8-1** | 알림센터 대상 번호 없음 | 혼동 1건 | 신규 알림 제목에 SH·인보이스 번호(6) | 이월 |
| **R-6-3** | RESERVED 선적 스캔 대상 | S4-2 | 무접촉 | 이월 S4-2 |
| **P-21** | AllocationPort | S4-2 | 무접촉 | 이월 |
| **P-22** | fx_rates | 원문 | 통화 불일치 입금 422 유지의 근거로 재확인 | 이월 |
| **P-23** | ORDER_INTAKE 원본 보관 | S6-1 | owner_type 미개방 확인 | 이월 |
| **P-48** | 혼합 통화 → KRW 한도 | runbook | 환산 불가 사유 라벨(2c) 연결 | 이월 |
| PR-16 ①②④ | 잡 결과 미저장·잡 화면·게이트 문구 | P6·프런트 | 무접촉 | 이월 |
| 기타 S3-2 이월 74행(위 외) | — | 원문 | 무접촉 | 원문 그대로 |

### 6.2 S3-3 신규 부채 후보 (부록 합집합 — PROGRESS 등재 시 소유·트리거 병기)

| # | 부채 | 소유 | 트리거 | 출처 |
|---|---|---|---|---|
| A-01 | DGD 미생성 | S4-4 | S4-4 착수 | sA |
| A-02 | PO 발주서 렌더·PO 소유 첨부 없음(원가 채널) | 발주 | 공급사 발주서 PDF 요구 1건 + 원가 역할 한정 다운로드 설계 | sA |
| A-03 | CI·PL·S/I KO 변형 없음 | 서류 | 국내 CI 요구 1건 | sA |
| A-04 | CLOSED 선적 CI 재발행 불가 | S4-2 | 선적 종결 의미 확정 | sA |
| A-05 | S3-3~S4-2 운영 CI/PL/S-I 발행 0(runbook 외부 작성) | S4-2 | INSPECTED 엣지 개방 PR | sA·sE E-06 합류 |
| A-06 | 레터헤드 로고·서명 이미지 없음 | 서류 | 로고 요구 1건(이미지 서빙 보안 재판정) | sA(+sC CK-17 철회분) |
| A-07 | PL N.W. ↔ SKU 단위 중량 대조 없음 | 서류·마스터 | 중량 오기 사고 1건 | sA |
| A-08 | S/I 운임 조건 override 없음 | 서류 | 판매자 대납 요구 1건 | sA |
| A-09 | 무상 세관 신고가액 열 없음 | 서류 | 무상 샘플 통관 문의 1건 | sA |
| A-10 | CIF·CIP 보험 정보 열 없음 | 서류 | 보험 서류 요구 1건 | sA |
| A-11 | 생성물 ↔ 서명본 업로드 짝 표시 없음 | 서류 | 서명본 관리 요구 | sA |
| B-01 | 선적 1건 다중 인보이스(부분 청구) | 채권 | 실수요 1건 | sB |
| B-02 | 초과·미배정 입금·환불 경로 | 입금 | 초과 입금 1건 | sB |
| B-03 | 대손·환차손익(미수 영구 산입) | 회계 P6 | 장기 미수 정리 요구 | sB |
| B-04 | SO 항 선수금 차감(P-15 SO 측) | 채권·여신 | 노출 과대로 정당 수주 승인 반복 보고 | sB |
| B-05 | 이중 입력 창 ±3일 정책화 | 입금 | 오탐 3건 | sB |
| B-06 | OPENING CSV 일괄 반입 | 채권 | 30건 초과 | sB |
| B-07 | L/C 하한 tolerance 미달 판정 | L/C | L/C 잔액 미청구 사고 | sB |
| B-08 | 이중 통화 L/C 미지원 | L/C | 실수요 | sB |
| C-D1 | PI 상세 계좌번호 전 역할 노출(기존) | PI·은행 | 계좌 문의 1건 또는 VIEWER 외부인 부여 | sC |
| C-D2 | 1 L/C : N SO 등록 불가 | L/C | 실수요 1건 | sC |
| C-D3 | `/api/v1/scheduled-jobs` 통제 접두어 밖 | 플랫폼 | 플랫폼 라우터 수정 세션 | sC |
| C-D4 | `today_kst` 이름 임포트 관용 | 플랫폼 | `TODAY_IMPORT_POINTS` 30개 초과 | sC |
| C-D5 | 생성물 다운로드 audit 양 | 서류 | audit_log 일 1만 행 초과 | sC |
| C-D6 | 하자 체크·전표 첨부 물류 미배정(X-11) | L/C·documents | 물류가 제시 서류·B/L 사본을 다루는 운영 확인 | sC(+X-11 합류) |
| D-01 | QT·PI·SO 기존 동작 클라이언트 판정(`allowed_actions` 부분) | 프런트 | 상태 규칙 불일치 1건 또는 S4-2 SO 할당 엣지 | sD |
| D-02 | CI 목록 화면·메뉴 없음 | S4-2 | INSPECTED 개방 PR | sD |
| D-03 | 채권 목록 파생 필터 없음 | 채권 | 요청 1건 | sD |
| D-04 | aging 과거 기준일 없음 | 채권·회계 | 월마감 보고 요구 | sD |
| D-05 | PDF 인라인 미리보기 없음 | 서류 | 사용자 마찰 | sD |
| D-06 | CI 작성 서버 초안 없음(탭 임시 저장뿐) | 서류 | 유실 보고 1건 | sD |
| D-07 | 기존 S3-2 대화상자 키 보존 미적용(R-3b-9·R-4b-4) | 프런트 공용 | 원문 | sD |
| D-08 | 이중 입력 확인 = 후보 전부만 | 입금 | 후보 3건↑ 혼선 | sD |
| D-09 | 플래그 화면 = 레지스트리 1항목 전제 | 플랫폼 | 플래그 3개↑ | sD |
| E-01 | durations 재갱신(2b·5a 후 — Q-15 계속) | CI | 편차 2배 또는 5a 병합 | sE |
| E-02 | 샤드 3 → 4 | CI | 샤드 32분 초과 | sE |
| E-03 | renditions CHECK 단계 확장 정의문 시험 유지비 | 서류 | 서류 종류 추가(DGD) | sE |
| E-04 | PDF 추출 dev 의존성 | 서류·CI | 도구 판 갱신·취약점 | sE |
| I-01 | 채권 선행 선적의 CI 발행 409(외부 인보이스 → 시스템 CI 전환 경로 없음) | S4-2 | INSPECTED 개방 PR(재판정 필수) | 통합 X-01 ⑤ |
| I-02 | documents 일반 파일 다운로드 audit 없음(생성물만) | documents | 대량 반출 우려(P-40 계보) | 통합 X-02 |

(sE E-05 "sD 부재"는 **소멸** — X-34.) 합계 신규 40건(A 11·B 8·C 6·D 9·E 4·I 2 — 실측 등재 시 대사).

---

## 7. 자동화 4금·§15 재점검 (설계 전체)

| 금지 | 판정 | 기계 고정 |
|---|---|---|
| 지출·발주 확정 | PO 무접촉(서류 = 수출만, PO 첨부·발주서 미개방) | `test_doc_machines` PO 단언 유지, G4 |
| 법적 판정 | 원산지·HS = 사람 입력·선택만(서버 기본값·자동 선택 0), 하자 체크 = 사람 체크+근거(판정 필드 0), tolerance 초과 422 = 산술 차단, Incoterms 완전성 = 데이터 정합 | `test_no_hs_auto_classification`, CI-09, 스키마(열 부재) |
| 대외 최초 발송 | 렌더 = 파일 생성뿐(사람이 내려받아 보냄 — L1/L2), S/I 문구 "보내지 않습니다", 알림 = 인앱·내부, 바이어 독촉 0 | CI-07(네트워크·메일 임포트 0)·H7(발행 outbox에 발송 채널 행 0) |
| 장부 확정 | 채권 = 청구 사실 기록(회계 원장 아님)이지만 **자동 생성 0** — 사람 1클릭 전용 경로만, **CI 발행이 채권을 만들지 않음**, 대손·분개 0, stock_movements 무접촉 | REGISTRY 엔트리(§7 아래)·H7 |
| 자동 확정 부재 | SO 자동 엣지 3(CONFIRMED↔IN_SHIPMENT 2 + IN_SHIPMENT→COMPLETED 1) — COMPLETED = 이행 완료 반영, 트리거 = 사람 1클릭(채권 발생·short-close) 같은 TX, 스케줄러·CLI·스캔 경로 0. RECEIVED→CONFIRMED 자동 0 유지 | CI-02(SO 자동 엣지 정확히 3·COMPLETED 진입 ⇒ provider ≠ 기본·COMPLETED ∈ CLOSED_STATUSES — `test:architecture/test_doc_machines.py:141-153` 대체) |

`test_no_auto_confirm_code_path_exists.py` REGISTRY 엔트리 6(sC C11 정정): `open_receivable_for_shipment`(채권 flow·라우터, actor 필수) · `converge_sales_order_completion`(채권 flow·short-close flow **2파일** — X-01 ⑥) · `short_close_sales_order`(라우터) · `issue_commercial_invoice`(라우터 — 출고지시·마일스톤·통관 flow 언급 0) · `register_opening_receivable`(라우터 ADMIN — CLI 금지) · `set_feature_flag`(라우터 ADMIN — migrations·seeds·CLI 금지). `record_transition`·`record_birth` 허용 호출처에 신규 flow 파일 가산.
**열지 않는 것(명시)**: B/L draft 업로드·AI 대조·"불일치 0 자동 적합 기록"(`D:215`·`D:333` — S5-3), DGD(S4-4), 서류 자동 발송, 입금 자동 매칭(P7), 채권 자동 생성, 이월 CSV 일괄 반입.
fail-visible 재점검(평가 불능 ≠ 통과): 만기 UNKNOWN = `DUE_UNKNOWN` 적색 구간·사유 코드, 레터헤드 as-of 없음 = 409(가장 이른 판 대체 0), 은행 미지정 = BLOCK(같은 통화 1개여도 자동 0), 원산지 결측 = 422, 채권 미등록 선적의 대금만기 = '채권 미등록' 배지·알림, 환산 불가 = UNEVALUABLE·"환산 불가 n건"(0 대체 0), provider 실패 = UNEVALUABLE.

---

## 8. 첫 커밋 실행 확인 항목 (정적 독해 한계 — 실행 검증 못 했음)

1. `alembic heads` = `281da4794717` 단일(2a — sE 실측 완료분 재확인).
2. `payments`에 `partner_id` 열 유무 — 3열 복합 FK 가능 여부(2a, §2.2 축소 규칙).
3. `sales_orders (id, currency)` UNIQUE 존재 여부 — `lc_terms` 통화 복합 FK 대상(4a).
4. SO 상태이력 `reason_required` CHECK 실제 제약명·`REASON_REQUIRED_TO` 파생 여부(2b).
5. DocKind 값을 담는 DB CHECK 전수(5a — S3-2 PR-3a 선례).
6. `documents.owner_type` CHECK 실명·`value_in` 파생(3a), 확폭 ALTER 잠금 시간.
7. M16~M22 식별자 63자(`uq_receivables_opening_ref_live`·`uq_trade_document_renditions_source_kind_format_language` 등).
8. `reportlab`·`openpyxl` 고정 버전의 Python 3.13 휠·라이선스·이미지 증가분, NanumGothic TTF 크기·OFL(3b 커밋 ①).
9. 2b 커밋 ① — 평가 재배치 후 **기본 provider에서 S3-1 여신 시험 기대값 무변경**.
10. `test_scheduler_and_cli_reach_only_the_totals_check_and_the_expiry_sweep`(`:977-997`) 무변경 통과(6).
11. 당사자 추가·삭제 후 선적 version 불변을 단언하는 기존 시험 유무(5a — grep 실측).
12. 프로덕션 실데이터 유무(2b·5a 되돌리기 비용 전제 — PR-1 착수).
13. `.shard_durations.json` 재갱신 후 샤드 편차(1b 첫 CI).
14. `trade-docs-totals-verify`가 CI를 자동 편입하는지(검산 루프가 DocKind 전체 순회인지 — 5a, CH-12).

---

## 9. 적대 검토 정정 (2026-10-05 — 3렌즈 spec-fidelity·safety·feasibility, **이 절이 §0~§8과 부록 A~E보다 우선**)

> 지적마다 원문(파일:줄·코드)을 먼저 열어 확인했다. 실재하면 더 엄격한(fail-closed) 쪽으로 자율 확정했다. 지적 번호(`H`·`M`·`L` = spec-fidelity, `S` = safety, `F` = feasibility)와 판정 표는 계획서 §9에 있다. **전부 기각 0건, 부분 기각 2건**이다. 부분 기각은 S-11과 M-6이고, 지적의 전제 일부가 반증됐다. 반증 근거는 아래 R-29·R-13에 적었다. **실행 검증 못 했음**(정적 독해). 코드 줄은 `2092406` 기준이다.

| R | 출처 | 원문 확인 | 결정(자율 확정) | 번복 비용 |
|---|---|---|---|---|
| **R-01** | H-1 | 부록 A~E 어디에도 「L/C 부록」이 없다(`sA:7·18`·`sB:34`·`sD:18·563`이 그 부록에 위임). `D:225` "lc_terms 등록(MT700 인테이크 → 초안), 하자 체크리스트 화면(lc-doc-check 점검 항목 체크박스+근거), tolerance 상·하한 자동 표시, 유효·선적기일 임박 적색". 통합 §2.1(f)의 `item_code`는 "L0 StrEnum"이라는 말뿐이고 값이 없다. §2.9의 쓰기 API는 1줄뿐이다 | **N-13 "L/C 부록 부재"를 등재하고, L/C 본체를 sB 소유(통합 §0 부록 문자 정정)로 아래처럼 확정한다.** ① **하자 체크 항목 카탈로그 14종**(`trade_docs/constants.py` `LcCheckItem` StrEnum — 코드·한국어 라벨·근거 조항. 근거 조항은 **표시용 출처이지 판정이 아니다**): `PRESENTED_WITHIN_PERIOD`(제시기간 내 제시 — UCP600 14(c)) · `PRESENTED_BEFORE_EXPIRY`(유효기일 내 제시 — 6(d)) · `SHIPPED_BY_LATEST_DATE`(최종선적일 준수 — 20(a)(ii)) · `INVOICE_AMOUNT_WITHIN_CREDIT`(송장 금액 ≤ 신용장 금액·과부족 — 18(b)·30) · `INVOICE_DESCRIPTION_MATCHES`(송장 상품명세 = L/C 문면 — 18(c)) · `INVOICE_PARTIES_MATCH`(발행인 = 수익자, 수신인 = 개설의뢰인 — 18(a)) · `TRANSPORT_ON_BOARD`(본선적재 표기 — 20(a)(ii)) · `TRANSPORT_CLEAN`(무사고 — 27) · `PORTS_MATCH`(선적항·양륙항 — 20(a)(iii)) · `CONSIGNEE_NOTIFY_MATCH`(수하인·통지처 = L/C 지시) · `PARTIAL_TRANSHIP_TERMS`(분할선적·환적 조건 — 31·20(c)) · `DOCUMENT_SET_COMPLETE`(요구 서류 종류·통수 — 14(a)) · `INSURANCE_COVERAGE`(CIF·CIP일 때 부보 범위 — 28. 그 외 Incoterms는 화면에서 '해당 없음' 체크+근거) · `DATA_CONSISTENT`(서류 간 자료 상충 없음 — 14(d)). **'적합' 판정 필드·전체 결과값은 0이다(CI-09 유지).** 화면 문구는 "점검했음"만 쓴다. ② **lc_terms API(LT1~LT4, 스키마 `extra="forbid"`)**: LT1 `GET /sales-orders/{id}/lc-terms`(현재 ACTIVE + 개정 이력, 전 역할) / LT2 `POST /sales-orders/{id}/lc-terms`(등록) / LT3 `POST /sales-orders/{id}/lc-terms/{lc_id}/amend`(본문 = LT2 + `version`, 이전 행 SUPERSEDED + 신규 INSERT, 1TX) / LT4 `POST /sales-orders/{id}/lc-terms/{lc_id}/cancel`(`reason` ≥ 2자 — R-12 비공백 규칙). PATCH 경로는 없다(§2.9의 "PATCH"는 이 3경로로 대체). LT2 본문 = `lc_number`(1~60자·제어문자 금지·서버 대문자 정규화) · `expiry_on`(필수) · `latest_shipment_on`(선택 — L/C에 없으면 생략, 이때 임박 행 없음) · `presentation_days`(1~365. 생략 시 21 — UCP600 14(c) 규정값, `code:modules/trade_docs/schedule.py:32-33`) · `tenor`·`usance_days`(쌍) · `lc_amount`(문자열 → `parse_minor_amount`, > 0) · `currency`(= SO 통화 — 복합 FK가 2차망) · `tolerance_plus_bp`·`tolerance_minus_bp`(0~10000). 검증: `latest_shipment_on ≤ expiry_on`(서비스 422 + DB CHECK) / SO `payment_type ≠ 'LC'`이면 422 **`LC_TERMS.LC.SO_NOT_LC`(신설)** / SO가 CANCELLED·COMPLETED면 409 **`LC_TERMS.LC.SO_CLOSED`(신설)** / `lc` 플래그 OFF면 기존 L/C 비활성 코드(N-09)이고 판정은 R-31의 잠금 아래에서 한다. ③ **화면 계약(PR-4b)**: SO 상세 'L/C 조건' 섹션(결제유형 L/C일 때만) = L/C 번호·금액·**tolerance 하한·상한 금액**·유효기일·최종선적일 D-N·개정 이력. 하한·상한 금액은 **서버 응답 `tolerance_bounds: {lower_text, upper_text}`**로 받는다. 서버는 S3-2 tolerance 순수 함수를 재사용하고, 프런트 계산은 0이다. **임박 적색** = 서버 필드 `urgency ∈ {OVERDUE, IMMINENT, NONE}`. 문턱은 기일 스캔과 같은 원천(`alert_rules.config.thresholds` — 4a 첫 커밋에서 실측)을 쓰고 프런트 상수는 0이다. 표시는 색과 글자를 함께 쓴다("임박"·"경과"). 선적 상세 '제시 기록'(제시·네고·인수일)과 '하자 체크리스트'(14항목 체크박스 + 근거 필수 + 마지막 마크 시각·사람 표시). ④ **MT700 인테이크 → 초안은 S3-3 밖**: **DESIGN 대비 ⑬**(계획서 §1). 소유는 S6-1(AI 레이어)이다. 부채 **I-03**(트리거: S6-1 착수 또는 L/C 수기 입력 오기 1건). ADR-0094 문면에 "MT700 인테이크 미구현 — 수기 입력만"을 넣는다. ⑤ PR-4b vitest에 "tolerance 상·하한 표시 = 서버 문자열 그대로", "`IMMINENT`·`OVERDUE` 적색+글자", "체크리스트 근거 빈칸 제출 불가", "'적합' 문구 0"을 더한다. PR-4a K에 "항목 카탈로그 14 = 프런트 라벨 키 대사"를 더한다 | 낮음(항목 StrEnum 추가는 열 변경 0) |
| **R-02** | H-2 | `D:227` ④ "선수금 입금분은 노출에서 차감하지 않는다(과대 방향의 안전측 — 부채 P-15)". 결정 #4·sB B8은 채권 항을 "충당 후 미수"로 바꿔 노출을 줄인다. 이 변경이 "DESIGN 대비" 목록과 §4 부기 문구에 없다 | **문면 변경으로 정식 등재한다(DESIGN 대비 ⑭ — 계획서 §1).** 다만 문면(미차감)으로 되돌리지 않는다. 문면을 그대로 두면 완납 채권의 선수금 몫이 노출에 **영구히 남는다**. 노출이 이력과 함께 단조 증가하므로 결국 정당한 수주까지 막혀 운영이 정지된다. 이는 fail-closed가 아니라 정지다. 차감은 **현금 사실**(입금 원장 행)에만 근거하고 대상은 **채권 항에 한정**한다. SO 항은 미차감을 유지하고 부채 B-04로 남긴다. ④의 "미수 provider 기본값 = 유일한 의도적 예외" 문장은 **2b에서 은퇴**한다(R-07이 기본 provider를 fail-closed로 바꾼다). 이 점을 §4 §7.10 부기 문구와 ADR-0089·ADR-0064 부기에 함께 넣는다. 계획서 §5에 **오너 확인 권장 2순위(노출 산식과 한 묶음)**로 적는다 | 중간(노출 산식과 같음) |
| **R-03** | S-01 | 장문 줄바꿈 인쇄(sA §A12)에는 ReportLab `Paragraph`가 필요하고, `Paragraph`는 입력을 자체 마크업(`<img>`·`<a>`·`<font>`)으로 해석한다. CK-09(`sC:538`)의 입력은 `{{ 7*7 }}`·`<script>`·`=HYPERLINK`뿐이다. `requirements.txt`에 reportlab이 없어 버전 하한도 없다 | ① `doc_render`에 **사람 입력 문자열 단일 이스케이프 함수** `markup_safe(text)`(= `xml.sax.saxutils.escape` + 제어문자 제거)를 둔다. `Paragraph(...)` 생성 지점은 그 함수를 거친 값만 받는다. K 아키텍처 시험(AST)으로 `Paragraph` 호출 위치를 `doc_render/layout.py` 1곳으로 고정하고, 첫 인자가 `markup_safe(...)` 호출이거나 템플릿 상수 리터럴인지 확인한다. ② `reportlab` **하한 ≥ 3.6.13**(CVE-2023-33733 수정판). 실제로는 `==`로 고정하되 고정 버전이 하한 이상인지 시험으로 단언한다. `rl_config.trustedSchemes = []`·`trustedHosts = []`를 모듈 import 시 설정한다(이름·지원 여부는 3b 커밋 ①에서 실측 — 없으면 이미지·링크 태그를 생성하지 않는다는 AST 단언으로 대체). ③ CK-09에 `<img src="/etc/passwd">`·`<img src="http://169.254.169.254/">`·`<a href="…">`·`<font color="[[…]]">`·`&`·`<`·`>`를 더한다. 각각 **원문 글자 그대로 출력**되고 파일·네트워크 접근은 0이어야 한다(시험은 소켓·`open` 감시). ④ 변이 점검 3b에 "이스케이프 제거"·"신뢰 스킴 기본값 복원"을 더한다. ⑤ 3b는 3렌즈(공급망·주입) 유지 | 낮음 |
| **R-04** | F-01 | provider는 모듈 전역 변수다(`code:modules/credit/providers.py:51-52,55-75`). `code:main.py:44`가 모듈 수준에서 `create_app()`을 부르고, `from app.main import app`을 import하는 시험 파일이 63개다. `test:integration/test_credit_evaluation.py:26-30`의 autouse 픽스처는 teardown에서 기본값으로 되돌린다. 그래서 같은 프로세스에서 이후에 도는 시험의 provider 상태가 실행 순서와 샤드 배정(`.github/workflows/ci.yml:160-174` LPT)에 따라 달라진다 | **PR-2b 커밋 ②에 다음을 명시한다.** (a) `tests/conftest.py`에 **세션 단위 autouse** 픽스처를 두어 `register_runtime_providers()`를 보장한다(import 부작용에 기대지 않는다). (b) `reset_receivable_provider_for_tests()`를 **`use_receivable_provider(fake)` 컨텍스트(직전 provider 저장 → 교체 → 복원)**로 바꾼다. 기본값을 단언해야 하는 시험만 `use_receivable_provider(DEFAULT)`로 명시적으로 opt-in한다. (c) "깨질 기존 시험"(sB:459) 목록에 `test:integration/test_confirm_credit_approval.py:121`(`receivables_reflected is False`)·`test:integration/test_credit_evaluation.py:196`(`is_default_provider()`)·`test:architecture/test_doc_machines.py:141-153`을 더한다. (d) **documents 소유자·생성물 해석기 등록(X-10)도 `api/router.py` import 부작용 대신 같은 `register_runtime_providers()`에서 한다**. 해석기가 미등록이면 전표 소유 첨부와 생성물 판정이 403(fail-closed, R10)이다. (e) K 시험: 무작위 순서 2회(`-p random_order` 등 — 도구는 2b 커밋 ①에서 실측, 없으면 파일 역순 실행)로 provider·해석기 상태 의존 0을 확인한다 | 낮음 |
| **R-05** | F-02·S-10 | 기존 `outstanding(session, partner_id, limit_currency) -> ReceivableTerm`(`code:modules/credit/providers.py:38-41`)은 **거래처 전체 미수를 한도 통화로 이미 환산한 값**이다. 통합 §2.4가 더하는 `invoiced_by_sales_order`는 SO별 gross뿐이라 "SO 묶음(SO 잔여 + 그 SO 미수) 1회 환산"을 만들 수 없다. credit은 receivables를 import할 수 없다(§2.10) | **Protocol을 재정의한다.** 메서드는 `exposure_parts(session, partner_id, so_ids) -> ReceivableExposure` 1개다. `ReceivableExposure` = `by_so: Mapping[so_id, SoReceivables(invoiced_gross, outstanding)]`(**SO 통화 원액** — 채권 통화 = 선적 통화 = SO 통화, 복합 FK로 보장) + `unattached: Sequence[UnattachedReceivable(outstanding, currency, fx_rate, fx_rate_date)]`(OPENING 채권, 그리고 `so_ids` 밖 SO(COMPLETED 등)의 미수 — 행 단위). **한도 통화 환산은 평가 함수만 한다.** SO마다 `(총액 − invoiced_gross) + outstanding`을 SO 통화로 합한 뒤 1회 HALF_UP하고, unattached는 행마다 1회 환산한다(sB B6 ④의 "이중 0·반올림 차 0"). 기존 `outstanding`은 **폐기**한다(파생 함수로 남기지 않는다 — 두 정의 금지). `ReceivableTerm`도 은퇴한다(R-07). 이 내용을 ADR-0090 문면과 sB B6 ③④ 표지에 반영한다. GC-A28에 "SO 2건·OPENING 1건·COMPLETED SO 미수 1건 혼합 → 환산 1회/묶음" 행을 더한다 | 낮음(병합 전) |
| **R-06** | F-03 | 가짜 provider 7개가 `outstanding`만 구현한다(`test:integration/test_credit_evaluation.py:216·235·261·277`, `test:integration/test_approval_hardening.py:179·204·223`). `PROVIDER_FAILURES`(`code:modules/credit/evaluation.py:53-60`)에 `AttributeError`가 없어 500이 난다 | **PR-2b 커밋 ①에서 가짜 7개를 `exposure_parts`로 다시 쓴다**(같은 실패 의미 유지 — Broken = 예외, Busy = `OperationalError`, Negative = 음수 outstanding → `ValueError` 계약). 그리고 "깨질 기존 시험" 목록에 7개를 등재한다. `AttributeError`를 `PROVIDER_FAILURES`에 더하지 않는다(버그는 전파 — 기존 원칙 `:52`). 기반 클래스 기본 구현안은 기각한다(기본 구현이 0을 돌려주면 "못 셈 = 0" 위험이 재발한다) | 없음 |
| **R-07** | S-07 (+H-2 ④ 문장) | 기본 provider의 허용 근거는 "막으면 S3-3 전 모든 확정이 영구 정지"(`code:modules/credit/providers.py:1-10`)다. 미반영 상태에서도 WITHIN_LIMIT가 가능하다(`code:modules/credit/evaluation.py:209-226`). 2b부터 COMPLETED SO는 노출에서 빠진다(`code:modules/credit/exposure.py:19`). 그래서 기본 provider로 평가하는 경로가 남으면 COMPLETED SO 금액이 통째로 빠지는 **과소 노출(fail-open)**이 된다 | **2b에서 fail-closed로 전환한다.** `_evaluate`가 기본 provider를 만나면 **UNEVALUABLE `RECEIVABLE_PROVIDER_NOT_REGISTERED`(신규 사유 코드 — credit `ReasonCode`, 에러 카탈로그 아님)**로 확정을 거부한다. '부분 노출' 경로(`exposure_is_partial`·'미수 미반영' 배지)는 **은퇴**한다(응답 필드는 2b부터 항상 false, 2c에서 화면 배지 코드 삭제 + 사유 라벨 1행 추가). 결속 시험(`test_doc_machines.py:141-153` 대체 — CI-02)을 "**COMPLETED로 들어가는 엣지가 존재하면 ⇒ 기본 provider 평가는 WITHIN_LIMIT가 될 수 없다**"로 강화한다. 기본 provider 클래스는 시험 opt-in 전용으로 남긴다(R-04 (b)). 2 입구 등록 시험(R-24)은 그대로 둔다. S3-1 여신 시험 중 기본 provider로 WITHIN_LIMIT·EXCEEDED를 기대하던 것은 **0 미수 가짜**(`FixedReceivables({})`)로 갈아 끼운다(기대값 무변경). 기본 provider 그대로 둔 단언(R-35 목록)은 UNEVALUABLE로 개정한다. GC-A28에 "기본 provider → UNEVALUABLE" 행을 더한다. 변이 2b: "기본 provider 분기 제거" | 낮음(분기 1곳) |
| **R-08** | M-1 | `code:modules/trade_chain/milestone_view.py:386` "대금만기 — 수출·수입 공통"·`:390-393` `is_overdue` null. ADR-0081 ② "수출·수입 L/C 공통". PO도 `payment_type`이 있다(`code:modules/purchase_orders/service.py:387`). `lc_terms`는 SO FK뿐이다 | **수출분은 종결하고 수입분은 이월한다.** §6.1 Q-08 행을 "**수출 대금만기·제시기한 알림 = 종결 / 수입(매입 채무 만기·수입 L/C 제시기한) = 이월**"로 나눈다. 수입 선적의 INVOICE_DATE 앵커·`is_overdue`·수입 L/C 제시기한은 S3-3 뒤에도 **UNKNOWN 표시를 유지**한다(조용한 0 아님 — 사유 코드 그대로). 신규 부채 **I-04**(소유: S6-2 회계 인터페이스 — 매입 채무. 트리거: 수입 L/C 또는 후불 매입[PO TT_DEFERRED] 1건 운영 발생 중 먼저). ADR-0094에 "**수입 L/C 미배선(lc_terms = SO 전용)**"을 명시한다. WBS v1.7 주석 ⑩(계획서 §1 **WBS 대비 ⑫**)을 더한다 | 없음 |
| **R-09** | M-2 | `W:129` "선수금 초과분". 현행 PI 입금은 초과 시 422다(`code:modules/payments/service.py:158-169` `PAYMENTS_PAYMENT_EXCEEDS_DUE`). 계획은 422를 유지한다 | **WBS 대비 ⑩ "선수금 초과분 = 미개방(422 유지)"**를 등재하고 WBS v1.7 주석 ⑧을 더한다. 근거: 초과 현금의 귀속(다음 SO 선수금·환불·채권 직접 입금)은 사람 판단이고, 받은 뒤 정정할 경로(B-02)가 없다. 부채 **B-02 트리거를 "PI 초과 입금 422 문의 1건 또는 R-29 검산 알림 1건"**으로 구체화한다. ADR-0089 요지에 "선수금 초과분 미개방"을 명기한다 | 낮음 |
| **R-10** | M-3 | `D:187`·`D:444`는 **CI·PL**만 검수 게이트 대상이다. S/I가 닫히는 이유는 sA §A8의 설계 선택 "`ci_id` NOT NULL — 원천 = 살아 있는 CI"다. `D:215`는 "선적/전표 한 원천"이다 | WBS 대비 ①을 **①-a(CI·PL — `D:187`·`D:444` 근거)**와 **①-b(S/I — 설계 선택 "원천 = CI"에 따른 폐쇄)**로 나눈다. **선적 원천 S/I 운영 개방안은 기각한다**(재판정 결과 폐쇄 유지). 근거: S/I의 수하인·품명·포장은 CI·PL과 같아야 하는데, CI 없이 선적에서 바로 만들면 CI 발행 뒤 수하인 불일치(R-3a-4 계보)를 막을 기준값이 없다. B/L draft 대조(S5-3)의 기준값도 둘로 갈린다. ADR-0096에 대안("선적 원천 S/I — 수하인 불일치 위험")과 번복 비용(중간: `ci_id` NULL 허용 + 원천 2종 CHECK + 대조 기준 재정의)을 적는다 | 중간(번복 시) |
| **R-11** | M-4 | `code:modules/documents/schemas.py:42` `LinkDocumentCreateRequest.owner_type: str = Field(min_length=2, max_length=13)`. 통합 §2.2는 DB 열만 24로 넓힌다 | PR-3a 범위에 넣는다: documents 요청 스키마의 `owner_type` 길이를 **모델 열 길이와 결속된 단일 상수**(`OWNER_TYPE_MAX_LEN = 24`)로 바꾸고, 업로드·링크 양 경로에 같은 상수를 쓴다. K 시험을 더한다: **6종 × 업로드·링크 2경로 = 12행**이 스키마를 통과하고, 열 길이 ≠ 상수이면 실패하는 정의문 시험을 둔다. 변이 3a: "링크 스키마 13 복원" | 없음 |
| **R-12** | M-5 | R-4a-8(`P:117`·`P:579`, 트리거 "해당 표 수정 세션")은 M17이 `sales_order_status_log`를 고치면서 발동한다. 같은 표의 `reason_not_blank`는 btrim 기반이다(`code:modules/trade_docs/models.py:79`). 대체 수단은 `code:core/db/constraints.py:19` `BLANK_CHAR_CLASS`다. P-50(`P:1664`, 트리거 "해당 모듈 수정 세션")은 S3-3이 documents를 고치면서 발동한다. `code:modules/documents/schemas.py:39` 클래스에 `extra="forbid"`가 없다 | **둘 다 발동 → 처리한다.** ① **M17**: `sales_order_status_log.reason_not_blank`를 `BLANK_CHAR_CLASS` 형식으로 수기 재정의한다(위반 기존 행 사전 계수 — 1건이라도 있으면 RAISE·원인 PROGRESS 기록. 정의문 시험 양방향). ② **S3-3 신규 비공백 CHECK 전부**(receivables `cancel_reason`·`invoice_ref`·OPENING `note` / SO short-close 사유 / lc_terms 취소 사유·`lc_number` / `lc_checklist_marks.evidence` / CI 상태이력 사유 / S/I 문구 열)는 **btrim 대신 `BLANK_CHAR_CLASS`**를 쓴다. 마이그레이션이 같은 값을 스스로 만들고 시험이 대사한다(M15 선례). CI 상태이력은 `status_log_checks` 동형이지만 비공백 술어만 이 형식으로 바꾼다. ③ **PR-3a**: documents 쓰기 요청 스키마 전부에 `extra="forbid"`를 건다(forbid 래칫 계수 감소 — P-50 documents 몫 종결). ④ R-4a-8의 나머지 표(QT·PI·PO 상태이력·거래처 메모 등)는 **이월 유지** | 낮음 |
| **R-13** | M-6 | `P:1493` ⑦·`P:1494` 판정 후보 ①. 역기록 수렴이 `PI_NOT_OPEN`을 낸다(`code:modules/trade_chain/payment_status.py:61-66`) | **재판정: 닫힌(CANCELLED·EXPIRED) PI 역기록 = 409 유지.** **"미수가 영구히 틀어진다"는 전제는 반증된다.** EXPIRED는 미입금 ISSUED에서만 가고 살아 있는 후속(SO)이 있으면 제외된다(`code:modules/trade_chain/expiry_sweep.py:3-4,12-14`). CANCELLED는 후속 생존 시 차단된다(커널 `SUCCESSOR_ALIVE`). 그래서 닫힌 PI에는 살아 있는 SO가 없고, FIFO 충당(살아 있는 SO의 PI만)에도 들어가지 않는다. 닫힌 PI에 잘못 기록된 입금을 정정할 경로가 없는 것은 실재하므로 **부채 B-02(초과·미배정 입금·환불)에 합류**시킨다(트리거 동일). ADR-0089에 판정·근거를 넣고 §6.1 P-12 행에 "역기록 = 409 유지(근거 R-13)"를 더한다. S3-1 판정 후보 ①은 이로써 종결한다 | 낮음 |
| **R-14** | S-02 | `code:modules/trade_docs/machine.py:158` `("DRAFT","CANCELLED")`. `code:modules/quotations/models.py:4,78-79`는 CANCELLED·EXPIRED의 `frozen_at`을 무제약으로 둔다. sA §A1:40·§A13:411 술어는 "`status ≠ DRAFT`"다 | 렌더 게이트 술어를 **원천 헤더 `frozen_at IS NOT NULL`**(QT·PI 공통)로 바꾼다. 그 밖은 409 `EXPORT_DOCS.RENDITION.SOURCE_NOT_FROZEN`이다(코드 재사용 — §2.6 상황 문구를 "동결 이력 없음(DRAFT·초안 폐기 취소)"로 정정). 레터헤드 as-of 기준일(`frozen_at` KST 날짜)도 이 술어 덕분에 None이 될 수 없다. B·K 시험과 변이 3b에 "DRAFT에서 취소된 QT 렌더 409"를 더한다 | 없음 |
| **R-15** | S-03 | sA §A3:97 `effective_from` "서비스는 `≤ today_kst()`(미래 판 금지)"뿐이다. 두 번째 판의 과거 날짜를 막지 않고, 등록 잠금도 0이다(N-07) | ① **판이 0개일 때만 과거 `effective_from`을 허용한다.** 판이 1개 이상이면 새 판은 **`effective_from ≥ max(기존)` 그리고 `= today_kst()`**일 때만 받는다(미래는 X-29로 이미 422). 위반은 422 **`EXPORT_DOCS.LETTERHEAD.BACKDATED`(신설)**이다. ② 등록 TX(T1')는 **`pg_advisory_xact_lock(LETTERHEAD_LOCK_KEY)`**(고정 키 상수 1개 — 순서표 밖 단일 자원, 다른 잠금을 잡기 전에 획득)로 직렬화한다. 그래서 "첫 판 동시 2건이 둘 다 과거 날짜" 경합이 0이 된다. ③ A 시험: 두 번째 판 과거 날짜 422 / 같은 날 두 번째 판 허용(as-of = id DESC) / J: 첫 판 동시 2건 → 1건 과거 허용·1건 422. 변이 3a: "판 수 검사 제거" | 낮음 |
| **R-16** | S-04 | T6는 shipments·CI를 `FOR SHARE`로 잡는다. sA §A8:276은 부분 유니크를 `supersedes_si_id`에만 둔다. CI당 첫 S/I(`supersedes_si_id IS NULL`)에는 유일 제약이 없다 | ① 부분 유니크 **`uq_shipping_instructions_ci_first (ci_id) WHERE supersedes_si_id IS NULL`**(M22) — 위반 시 409 **`EXPORT_DOCS.SI.ALREADY_ISSUED`(신설, 번역표 등재)**. ② T6의 **shipments 잠금을 `FOR UPDATE`로 올린다**(CI 발행 T5와 같은 직렬화 축). ③ PR-5b J "동시 발행" = 첫 발행 실제 동시 2건 → 1 성공·1 409, 같은 S/I 재발행 동시 2건 → 1 성공·1 409. 변이 5b: "첫 S/I 유니크 제거" | 낮음 |
| **R-17** | S-05 | `sD:202` `CommercialInvoicePreview.bank_candidates`·`bank.summary_text`. 미리보기는 A·T·L이다(X-03). 계좌 조회는 `code:modules/bank_accounts/router.py:25` `CAN_READ = (RoleCode.TRADE,)`(+A)뿐이다 | CI 미리보기(CI1·CI9) 응답의 `bank_candidates`·`bank.summary_text`는 **A·T에게만** 준다. **L에게는 `bank.source`만** 준다(BLOCK 사유 "은행 미지정"은 그대로 보인다 — 발행은 어차피 A·T). CI 목록·`/commercial-invoices/export.csv`에는 **계좌번호 열이 없다**고 명기한다. K 시험: L·C·V의 CI 미리보기·목록·CSV·상세 응답 본문에서 `account_no` 원문 0(정규식 대사). 상세의 C·V 마스킹은 X-27 그대로다. 변이 5a: "미리보기 L 계좌 후보 노출" | 낮음 |
| **R-18** | S-06 | `code:modules/documents/router.py:32` `CAN_MANAGE = (TRADE, CERT)`. `:191-197` DELETE가 `require_roles(*CAN_MANAGE)`다. `code:modules/documents/service.py:752-783`에는 소유 유형별 분기가 없다 | `DELETE /documents/{id}`: **owner_type ∈ 전표 6종이면 A·T만**(CERT 403). 순서는 `404(문서 없음) → 403(전표 소유 + CERT) → 409(생성물 GENERATED_LOCKED·보존기한)`이다(삭제는 본문에 owner_type이 없어 404가 먼저 — 업로드의 "403을 404보다 먼저"와 다른 이유를 매트릭스 비고에 적는다). §2.9 매트릭스에 `DELETE /documents/{id}`(전표 소유) 행을 분리해 등재한다. 기존 SKU·LABEL·CERTIFICATION·COMM_LOG 소유 삭제는 `CAN_MANAGE` 그대로다. PR-3a K 2행 | 낮음 |
| **R-19** | S-08 | 통합 §2.1(f) `prev_mark_id`에 유일 제약이 없다. T17은 shipments `FOR SHARE`다 | ① 부분 유니크 **`uq_lc_checklist_marks_prev (prev_mark_id) WHERE prev_mark_id IS NOT NULL`** + **`uq_lc_checklist_marks_first (shipment_id, item_code) WHERE prev_mark_id IS NULL`**(M20) — 위반 시 409 `LC_TERMS.CHECKLIST.STALE_MARK`(번역표). ② `prev_mark_id`가 같은 선적·같은 항목의 행인지는 복합 FK `(prev_mark_id, shipment_id, item_code)` → `lc_checklist_marks(id, shipment_id, item_code)`(UNIQUE 신설)로 DB가 강제한다. ③ J: 같은 최신 마크 기준 동시 2건 → 1 성공·1 409. 이력은 갈라지지 않는다(현재값 = 사슬 끝 — 파생) | 낮음 |
| **R-20** | F-04·S-12 | PR-2a가 기존 `POST /proforma-invoices/{id}/payments`에 `POSSIBLE_DUPLICATE` 409를 더하지만, `fe:components/pi-payments-panel.tsx`에는 ack 경로가 없다(확인 블록은 2c). PI 입금은 `lock_chain(PI)`만 잡는다(`code:modules/trade_chain/payment_flow.py:75` 부근) | ① **P-14(채권·PI 양 경로)는 신설 PR-2d(R-21)로 옮기고, PR-2d가 `pi-payments-panel.tsx`에 최소 확인 블록**(후보 목록 + "모두 확인했음" 체크 → `acknowledge_duplicates` 송신)을 동반한다. 그래서 각 병합 시점에 PI 입금이 막히는 구간이 0이다. 2c의 `PaymentsPanel` 공용화는 이 블록을 흡수한다. ② **T13(PI 입금)에도 `lock_buyer_for_credit`을 선행한다**(멱등 → partners NKU → `lock_chain(PI)`. LOCK_ORDER partners → quotations → proforma_invoices 부분수열 유지). 그래서 같은 거래처의 채권 입금(T11)·PI 입금(T13)이 직렬화되고 이중 입력 판정 창이 0이 된다. 이로써 X-20의 "T13 무변경"은 **철회**된다. T13은 FIFO 충당을 통해 미수를 줄이는 노출 구성 변경이기도 하다. CJ에 "다른 SO 채권 입금 ∥ PI 입금, 같은 송금 → 1건 409" 결정적 판을 더한다. 변이 2d: "T13 거래처 잠금 제거" | 낮음 |
| **R-21** | F-07 | PR-2a 1개에 트랜잭션 5종·INSERT-only 원장 확장(되돌리기 "높음" — 계획 §5-6)·미수·앵커·aging·CSV·이중 입력이 함께 있다 | **분할한다. PR-2a(채권 원장) → PR-2d(입금 확장) → PR-2b.** PR-2a = **M16**(receivables + `sales_orders UNIQUE(id, buyer_partner_id)` + shipments UNIQUE 2) · T8·T9·T10 · 미수 `outstanding.py`(이 시점 미수 = 총액 − 선수금 FIFO 충당. 채권 입금 항은 2d가 더한다) · INVOICE_DATE 앵커 · `milestone_view` · aging · CSV · 응답 블록 · RV1. PR-2d = **M16b `s33_payments_receivable`**(payments 확장만 — 독립 검토) · T11·T12·T13(R-20) · 미수에 채권 입금 항 가산 · P-14 양 경로 + PI 패널 최소 확인 블록 · **검증 A "일부입금 전환"(GB-13)·GC-A26**. **PR-2d는 3렌즈**(INSERT-only 원장 스키마). 마이그레이션은 **8건**(M16·M16b·M17~M22. 기존 번호는 바꾸지 않고 `down_revision` 사슬 `281da4794717 → M16 → M16b → M17 → … → M22`). 의존: 2a → 2d → 2b. 2d → 4a는 없다(tolerance는 채권 발생 = 2a). 2c는 2b 뒤 그대로 | 병합 전 낮음 |
| **R-22** | F-05 | `IMMUTABLE_TABLES`를 실측하면 **13개**다(`code:core/db/table_policy.py:19-48`). X-37이 나열한 이름도 13개다. DESIGN §17.5 부기는 S3-x 확장분만 센다("8테이블"·"11테이블" — `D:378`·`D:380`) | 전 문서에서 **코드 `IMMUTABLE_TABLES` 13 → 21(+8)**로 정정한다. DESIGN §17.5 부기 계수는 **11 → 19**("S3-x 확장분" 기준 — audit_log·certification_status_log 제외)로 적고 두 기준을 병기한다. 계획 머리·§3, 통합 §0-4·X-37·§2.3의 "14 → 22"는 이 행이 대체한다. 시험 핀은 `len(IMMUTABLE_TABLES)`를 직접 쓰지 않는다(집합 대사 — 기존 `test:integration/test_table_policy.py` 방식) | 없음 |
| **R-23** | F-06·S-13·L-6 | `payments.partner_id` 존재(`code:modules/payments/models.py:54-57` NOT NULL FK, 주석 "S3-3 거래처 단위 입금이 같은 열"). `uq_sales_orders_id_currency` 존재(`code:modules/sales_orders/models.py:160`). X-11 인용 `router.py:31`은 주석 줄이다 | **조건부 분기를 삭제하고 확정 문면으로 바꾼다.** payments 복합 FK = **`(receivable_id, partner_id, received_currency)` → `receivables(id, partner_id, currency)` 3열 확정**(§2.2의 "2열로 축소" 문장 삭제). lc_terms 통화 복합 FK 대상 = **기존 `uq_sales_orders_id_currency`**(M20 UNIQUE 신설 불필요, §2.1(d) 문장 삭제). §8 ②③과 계획 §8의 두 항목은 "**확인됨(정적)**"으로 바꾼다. X-11 인용을 `code:modules/documents/router.py:32`로 정정한다 | 없음 |
| **R-24** | F-08 | worker = `python -m app.cli run-scheduler`(`docker-compose.prod.yml:141`, `code:cli.py:277,402`). 즉 `cli.main`과 같은 경로다 | 전 문서에서 "3 입구(API·worker·CLI)" → **"2 입구(`create_app`·`cli.main` — worker = `cli run-scheduler`)"**. 등록 시험은 2행이고, 변이는 "입구 1개 등록 누락" 2종이다. GC-A28·GB-27·CI-06·ADR-0090·§2.4 `app/bootstrap.py` 행의 문구도 정정한다 | 없음 |
| **R-25** | F-09 | `lock_document`는 `FOR UPDATE` 전용이다(`code:modules/trade_docs/locking.py:59-77`) | PR-2a(→ T11은 2d)에서 **`lock_document(..., read: bool = False)` 키워드를 확장**한다(`read=True` → `with_for_update(read=True)`, `populate_existing` 유지 — 잠금 뒤 재조회 원칙이 SHARE에도 필요). 직접 `read=True` 3곳(`quantities.py:257`·`reference.py:137`·`milestone_view.py:464`)은 무접촉이다. 단위 시험 1행 | 없음 |
| **R-26** | F-10 | `doc_number_seq`에는 DocKind CHECK가 없다(`code:modules/numbering/models.py:30-38`). `events.aggregate_type`은 `String(60)` 무CHECK다(`code:modules/outbox/models.py:33`). DocKind별 CHECK는 각 헤더의 `header_common_checks`(`code:modules/trade_docs/mixins.py:173`) 안에 있다 | §2.2의 "DocKind 값을 담는 CHECK 전수" 행과 M21의 "DocKind CHECK 전수 확장"을 **삭제**한다(교차 표 CHECK 수정 0 — S3-2 M14 선례). 5a 첫 커밋 실측은 "**0건 확인**" 항목으로 남긴다(1건이라도 나오면 M21에 더하고 PROGRESS 기록). M21의 수기 재정의는 **renditions `source_type` CHECK뿐**이다. §2.13의 CHECK 수기 재정의는 **4 → 5건**(M17 2건 `reason_required`·`reason_not_blank`[R-12]·M18·M21·M22) | 없음 |
| **R-27** | F-11 | `create_file_document`는 멱등 지문에 sha256을 넣는다(`code:modules/documents/service.py:611-648` — `:631-632`). sA §A12-④는 XLSX 바이트가 매번 다르다고 인정한다 | **렌더 요청의 멱등 지문 = 요청 본문(원천 종류·id·format·language)만**으로 하고 파일 해시는 제외한다. 렌더 경로는 `create_file_document`를 재사용하지 않는다(내부 저장 함수만 공유). 재시도 응답은 **renditions에 저장된 첫 파일의 document_id**다. "해시 동일" 시험은 **PDF(invariant)에 한정**한다. XLSX는 "같은 키 재요청 = 같은 document_id·저장 바이트 불변"으로 단언한다 | 없음 |
| **R-28** | S-09 | QT·PI·CI 헤더는 `internal_note`(1000자)를 가진다(`code:modules/trade_docs/mixins.py:74-75` "서류에 출력하지 않음"). 렌더 컨텍스트 차단 대상은 원가 키뿐이다(G4·CK-07) | `doc_render` 입력 뷰 데이터클래스(`QuotationView`·`ProformaView`·`CommercialInvoiceView`·`PackingListView`·`ShippingInstructionView`)를 **명시적 허용 필드 목록으로 고정**한다. K 시험은 필드명 집합 = 핀이다(추가는 핀 갱신이 필요한 의도된 변경). **G4·CK-07을 "원가 키·`internal_note`·`assignee_id`·`created_by_id`·`updated_by_id` 0"으로 넓힌다**. 변이 3b: "뷰에 internal_note 추가" | 없음 |
| **R-29** | S-11 (부분 기각) | **전제 "초과 현금이 어디에도 드러나지 않는다"는 반증된다.** sB B4 ②③(`sB:129-130`)은 남은 A를 **미충당 선수금**으로 남기고, GB-12(`sB:440`)가 "미수 0 유지·미충당 선수금 300,000"을 단언하며, SO 응답 `receivables_summary.unallocated_advance`(`sD:171`)·SO 상세 '채권' 요약(`sD:501`)이 표시한다. 남는 공백은 하나다. **종결(COMPLETED·CANCELLED) SO의 미충당 선수금 > 0은 다른 채권에 충당될 수 없으므로 실제 초과 입금인데, 이를 알리는 신호가 없다** | 남는 공백만 막는다. PR-6에서 `trade-docs-totals-verify`(05:30, JOB 14 유지)에 **"종결 SO의 미충당 선수금 > 0 → ADMIN 알림(dedup `unallocated-advance:{so_id}`)"** 점검 1종을 더한다(읽기만 — 4금 무접촉). SO 상세에 "초과 입금 의심" 배지를 단다(2c — 서버 파생 필드 `overpaid_suspect`). 이 알림이 **부채 B-02의 감지 트리거**다(R-09). `overpaid_amount` 음수 노출안은 기각한다(미수는 구성상 ≥ 0이라 음수가 생기지 않는다) | 낮음 |
| **R-30** | S-14 | X-24 근거 "GET 부작용(쓰기) 0" ↔ T3'(생성물 다운로드 GET마다 audit INSERT·커밋) | X-24 근거 문구를 **"업무 데이터 쓰기 0(감사 1행 예외 — 백업 열람 선례 `code:modules/audit/models.py:51-52` `BACKUPS_VIEWED`)"**로 정정한다. sA §A13 대안 (e) 표지도 같다 | 없음 |
| **R-31** | S-15 | `code:modules/platform/service.py:93-105` `is_feature_enabled`는 잠금 없는 읽기다. T18은 `FOR UPDATE`다 | **T15·T16·T17(L/C 입력 진입 3종)은 `lc` 플래그 행을 `FOR SHARE`로 읽는다**(멱등 직후, 다른 잠금보다 먼저 — feature_flags는 순서표 밖 단일 행이고 T18은 이 행만 잡으므로 순환 0). 행이 없으면 꺼짐이다(기존 fail-closed). 그래서 OFF 커밋과 동시에 들어온 입력은 OFF 뒤에 저장되지 않는다. `platform`에 `is_feature_enabled_for_write(session, code)`(SHARE 판)를 더하고 기존 함수는 무변경이다. J: OFF 토글 Barrier ∥ 등록 → 둘 중 하나만 성공하고 OFF 뒤 저장 0 | 낮음 |
| **R-32** | L-1 | `D:439` ① 원문 "P3=A·B·**E**·G·H·I·K" | 계획 §1 매핑을 "**`D:439` ① = A·B·E·G·H·I·K — 이 중 E는 S3-3 해당 없음**(비용 코어 S3-4, PL CBM은 서류 값) + J"로 정정한다. WBS v1.7 ⑤도 같은 표기로 바꾼다 | 없음 |
| **R-33** | L-2 | `W:129` "언어 변형"·`D:215` "언어 변형 지원" ↔ §2.1(c) "CI·PL·SI ⇒ `language='EN'`" | **WBS 대비 ⑪ "언어 변형 = QT·PI(EN·KO)만, CI·PL·S/I = EN만"**을 등재하고 WBS v1.7 주석 ⑨를 더한다(부채 A-03과 연결). 근거: CI·PL·S/I는 수출 통관·은행 제출 서류라 영문이 사실상 표준이다. KO 변형은 수요가 없는데 템플릿 유지비만 두 배가 된다 | 낮음 |
| **R-34** | L-3 | ADR-0068 ① "PI `lock_chain` 단일 진입"을 X-19·R-20이 바꾼다. ADR-0055 영향 세션란 "S3-3(`lc_terms`·L/C 플래그 토글·Incoterms 완전성)"을 계획이 이행한다 | §3 기존 ADR 부기를 **15 → 17건**으로 늘린다: + **0068**(역기록 = peek → 거래처 잠금 → 대상 분기, PI 입금도 거래처 잠금 선행 — R-20) + **0055**(S3-3 이행: lc_terms·토글 경로·Incoterms 완전성 V-항목) | 없음 |
| **R-35** | L-4 | GC-A11 비고(`GC:64`) "미수는 S3-1에 없어 '미수 미반영' 배지", GC-A13 비고(`GC:75`) "미수 항만 의도적 예외". `test:integration/test_confirm_credit_approval.py:121` | GC v1.6에 **A11·A13 비고 부기**를 단다(문면 변경 없이 "2b 이후 미수 반영 — 기본 provider = UNEVALUABLE(R-07), '미수 미반영' 배지 은퇴"). 2b 커밋 ① 실측 항목(§8)에 "**provider·`receivables_reflected`·`exposure_is_partial`·`is_default_provider`를 단언하는 기존 시험 전수 목록화**"(grep 결과를 PROGRESS에 기록)를 더한다 | 없음 |
| **R-36** | L-5 | R-4a-9(`P:118`·`P:580`) 트리거 "requirements 갱신 PR". PR-3b가 `reportlab`·`openpyxl`을 넣는다 | **3b에서 발동한다.** 3b 커밋 ①에서 `tzdata==2026.5` 줄 diff 0을 확인·기록한다(시험: requirements의 tzdata 고정값 = 핀). 바뀌면 R-4a-9 절차(저장된 시각형 tz 전수 대사)를 같은 PR에서 수행한다. §6.1에 행을 더한다 | 없음 |
| **R-37** | F-12 | 파일 길이·이름 실측 | 인용 정정: `code:modules/outbox/models.py:80-82`(sC:328) → **`:32`**(`event_type String(60)`) / `fe:styles/index.css:5-31`(sD:596) → **`:5-30`**(파일 30줄) / sB B7 ④ "COMPLETED SO 새 선적 409 = `chain_ops.py:129` `SO_SHIPPING_STATES`" → **`code:modules/trade_docs/quantities.py:43-46` `CONSUMABLE_STATUSES[SO]`**(`code:modules/trade_chain/shipment_flow.py:321,380`) / §2.10 `TODAY_IMPORT_POINTS`(`test:support/kst.py:19`)의 실제 이름은 **`MILESTONE_TODAY_IMPORT_POINTS`**이므로 1b가 이름 변경(일반화)을 명시한다 | 없음 |
| **R-38** | 합산 | R-01·R-15·R-16 신설 코드 | **에러 코드 신규 41 → 45종**: + `EXPORT_DOCS.LETTERHEAD.BACKDATED`(422) · `EXPORT_DOCS.SI.ALREADY_ISSUED`(409) · `LC_TERMS.LC.SO_NOT_LC`(422) · `LC_TERMS.LC.SO_CLOSED`(409). 도메인별: EXPORT_DOCS 19·DOCUMENTS 2·RECEIVABLES 12·PAYMENTS 1·SALES_ORDERS 3·PLATFORM 1·COMMON 1·LC_TERMS 6. 전부 카탈로그 1:1 시험 대상이다. 신규 credit 사유 코드 1(`RECEIVABLE_PROVIDER_NOT_REGISTERED` — 라벨 대사 2c) | 없음 |
| **R-39** | 변이 보강 | 위 결정이 변이로 고정되지 않으면 조용히 약해질 위험(R5) | sE E6-3 최소 목록에 더한다. 2b: 기본 provider 분기 제거(R-07)·SO 묶음 환산을 항별로(R-05) / 2d: T13 거래처 잠금 제거(R-20)·PI 경로 ack 무시 / 3a: 링크 스키마 13 복원(R-11)·판 수 검사 제거(R-15)·CERT 전표 첨부 삭제 허용(R-18) / 3b: 이스케이프 제거·신뢰 스킴 복원(R-03)·DRAFT 취소 QT 렌더 허용(R-14)·뷰에 internal_note(R-28)·멱등 지문에 해시 포함(R-27) / 4a: 첫 마크 유니크 제거(R-19)·플래그 SHARE 읽기 제거(R-31)·`tolerance_bounds` 프런트 계산 / 5a: 미리보기 L 계좌 후보 노출(R-17) / 5b: 첫 S/I 유니크 제거(R-16) / 6: 종결 SO 미충당 선수금 점검 누락(R-29) | 없음 |
| **R-40** | 표지 | 통합에 진 부록 문면이 표지 없이 남는다(S3-2 R-27 선례) | PR-1 첫 커밋에서 §1.8 색인에 더해 "**[적대 R-nn]**" 표지를 단다. **sA** §A1:40·§A13:411 렌더 술어(R-14)·§A3:97 effective_from(R-15)·§A8:276 S/I 유니크(R-16)·§A12 Paragraph·버전(R-03)·§A13 대안 (e)(R-30)·§A15 계수(R-22) / **sB** 0-2 표 `sB:34` L/C 부록(R-01)·B5 ⑤ 초과분(R-09)·B5 ⑦ 닫힌 PI(R-13)·B6 ①③④ 입구·Protocol(R-05·R-24)·B7 ④ 인용(R-37)·B8 ④ 문면(R-02)·B10 ⑥ 최소 열 → R-01·B13 ② 수출 한정(R-08)·B19 PR 배치(R-21)·`sB:459` 깨질 시험(R-04·R-06·R-35) / **sC** T6·T13·T15~T17(R-16·R-20·R-31·R-19)·C6 CERT 삭제(R-18)·C7 ④·CK-07·CK-09(R-03·R-28)·CI-06(R-24)·`sC:328`(R-37) / **sD** `sD:18·563` L/C 화면(R-01)·`sD:202` 계좌 후보(R-17)·`sD:596`(R-37)·PY4 PI 확인 블록(R-20) / **sE** PR-2a 행·M16(R-21)·`sE:185` 버전 고정(R-03)·E6-3 변이(R-39) | 없음 |

**갱신 수치(이 절 기준 — §0~§8의 해당 수치를 대체)**: 해소 49건 + **N-13**(R-01) = 50건 / 신규 테이블 12(변경 없음) / **IMMUTABLE 13 → 21**(DESIGN 계수 11 → 19) / 열 단위 UPDATE 3 → 5 / **마이그레이션 8건(M16·M16b·M17~M22)** / **에러 코드 신규 45종** / ADR **0088~0099(12건, 번호 변경 없음)** + 기존 부기 **17건** / GC v1.6 **14건 유지**(A28·G4·A30에 행 가산, A11·A13 비고 부기) / JOB 14 유지 / 신규 부채 40 → **42건**(+I-03 MT700 인테이크·I-04 수입 대금만기·L/C) / 문면과 다르게 확정 **9 → 14건**(WBS 대비 7: ①[①-a·①-b]·②·③·④·⑩·⑪·⑫ / DESIGN 대비 7: ⑤~⑨·⑬·⑭) / CHECK 수기 재정의 **5건**(R-26).

**§6.1 대사 가산**: Q-08 = 수출 종결·수입 이월(I-04, R-08) / P-12 = 입금 도착·역기록 모두 409 유지(R-13) / **R-4a-8 = 부분 종결**(SO 상태이력 + S3-3 신규 표 — R-12, 나머지 이월) / **P-50 = documents 몫 종결**(R-12) / **R-4a-9 = 3b 발동·처리**(R-36) / S3-1 PR-10a 판정 후보 ① 종결(R-13) / B-02 트리거 구체화(R-09·R-29).

**갱신된 PR 목록(최종, 병합 순서)**: PR-1 → PR-1b → PR-2a → **PR-2d** → PR-2b → PR-2c → PR-3a → PR-3b → PR-3c → PR-4a → PR-4b → PR-5a → PR-5b → PR-5c → PR-6 → PR-7(**16개**). 3렌즈 대상: **2b·5a(+2차)·3b·2d**.
