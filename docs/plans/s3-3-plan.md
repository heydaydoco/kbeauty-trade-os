# S3-3 계획서 — 서류 생성기·채권/입금

> 상태: **자율 확정(오너 지시 2026-09-29 — "PowerShell 없이 클라우드에서 끝까지, 결정·개입 없이")**. 웹 세션 판정 절차는 생략했고, 판정 후보는 전부 **더 엄격한(fail-closed) 권장안**으로 확정했다(사후 번복 가능 — 번복 가능성·비용이 큰 항목은 §5). 이 문서는 S3-3 PR-1(문서 전용) 첫 커밋으로 등재된다. 승인 범위 밖 구현 금지.
> 작성일 2026-10-05(S3-2 종결 직후 — 클라우드 계획 세션). **적대 검토 3렌즈(spec-fidelity·safety·feasibility) 반영 완료(2026-10-05) — 기록은 §9, 정정 정본은 design-integrated §9(R-01~R-40, 이 계획서 본문·통합 §0~§8·부록보다 우선).**
> 기준 커밋: main `2092406`(S3-2 종결 — PR-8 #67). 기준선(PROGRESS 'S3-2 PR-8 / S3-2 종결' 절 `P:32-42`): pytest **5774 수집**(최종 코드 1회 5723 passed · 34 skipped · 15 errors[실행 옵션 실수 — 4파일 재실행 83 passed]) · vitest **83파일 1474** · golden **85** · concurrency **68** · 커버리지 게이트 94(CI 3샤드+병합, ADR-0073) · `alembic heads` `281da4794717` · ADR 최대 0087 · JOB 14 · 상태 총수 30/152/182 · IMMUTABLE **13표**(코드 `IMMUTABLE_TABLES` 실측 — 적대 R-22. 종전 '14'는 오기).
> 상시 수칙 유지: 자동화 L3 금지 4영역(§15) 비접촉(채권·CI·S/I = 사람 1클릭, CI 발행이 채권을 만들지 않음, 대외 발송 0, 원산지·HS 자동 판정 0, stock_movements 무접촉) / 규제·세율·HS는 시스템이 단정하지 않는다 / **병합은 클라우드 세션이 게이트(head SHA 전 체크런 success[`ci-ok` 포함] + mergeable clean) 확인 후 API squash — 웹 Merge 버튼 금지 유지** / 원천 실데이터 커밋 금지 / 완료 보고는 릴레이 본문에 DDL 전문·12자리 해시 직접 포함.
> **실행 검증 못 했음** — 이 계획은 정적 독해(파일 열람·grep)로 작성했다. 코드 줄 번호는 `2092406` 기준이고 각 PR 첫 커밋에서 실측한다(P-60 선례).

## 0. 이 계획서의 구성

이 문서는 **결정 요약·범위·PR 분할·검증 배치**를 담는 본문이다. 안건 전문(테이블·컬럼·CHECK·전이표·에러코드·테스트·되돌리기 비용)은 다음 부록이 정본이다 — 구현 세션은 해당 PR이 소비하는 부록을 먼저 읽되, **통합 §9(적대 검토 정정) → 통합 §0~§8 → 부록 A~E** 순으로 우선한다. 부록 원문은 이 세션에서 고치지 않았고, 통합에 진 문면의 색인은 통합 §1.8이다(PR-1 첫 커밋에서 부록 머리에 우선순위 1줄·"[통합 X-nn]" 표지 부착).

| 부록 | 내용 |
|---|---|
| `docs/plans/s3-3/design-A.md` | 서류 데이터 모델·생성기: 서류 매트릭스(DGD·B/L·PO 발주서 범위 밖)·**§20 B 검수 게이트 선배치**·레터헤드 불변 판(as-of)·CI 커널 편입·PL 2표(Q-05)·S/I(Q-06)·원천 스냅샷 규칙표·검증 카탈로그 V1~V14(BLOCK/NOTICE)·재발행·렌더링(ReportLab·openpyxl·동봉 폰트)·산출물 저장(renditions)·documents 확폭(P-13) |
| `docs/plans/s3-3/design-B.md` | 채권·입금·여신 provider·대금만기: receivables·payments 확장(P-09)·미수 단일 정의·선수금 FIFO 충당(P-15)·이중 입력(P-14)·provider 실구현·채권 전환분 차감(P-01·P-08)·SO COMPLETED·short-close(Q-04)·DoD 공백/이중 0 증명표·L/C 배선(lc_terms 소유)·플래그 토글(P-10)·aging·대금만기/제시기한 알림(Q-08)·OPENING(P-51)·GB-01~28 |
| `docs/plans/s3-3/design-C.md` | 트랜잭션 경계 T1~T22·LOCK_ORDER 3슬롯·PI SHARE 뒤 `lock_chain` 금지·노출 구성 변경 쓰기 거래처 잠금·version(R-3a-4)·멱등(P-39)·역할 매트릭스·마스킹·감사·이벤트·잡 14 유지·4금 논증·import 방향·KST·CJ/CH/CI/CK 시험 |
| `docs/plans/s3-3/design-D.md` | API·화면 계약: 엔드포인트(LH·R·CI·SI·RV·PY·SC·FF)·응답 스키마·`allowed_actions`(S3-3 신규분)·에러 반응표·화면·셸·QT/PI 서류 파일 패널·레터헤드 화면·CI 작성 2단·채권/aging/입금·SO 완료·대금만기 표시·한국어 UI·390px·S3-2 화면 부채·멱등 키 보존 |
| `docs/plans/s3-3/design-E.md` | PR 분할·마이그레이션 DAG(M16~M22)·ADR 배정·WBS v1.7·GC v1.6·검증 배치·변이 점검·적대 검토 배치·되돌리기 비용·위험 |
| `docs/plans/s3-3/design-integrated.md` | **통합 정합 검토(모순·중복 37건 + 누락 12건 해소)·통합 스키마 12테이블·상수·에러코드 41종·이벤트·잡 14·AUTHZ·LOCK_ORDER·TX T1~T22 정정본·마이그레이션 DAG·ADR 0088~0099·DESIGN 부기·WBS v1.7·GC v1.6·부채 대사** — 부록과 충돌하면 이 문서가 이긴다. **§9 적대 검토 정정(R-01~R-40 — 누락 +1·에러 코드 45종·마이그레이션 8·PR 16)이 최우선** |

## 1. 세션 정의 (WBS S3-3 문면 그대로 — `WBS.md:127-132`)

- **범위**: §7.6·7.10
- **산출물**: QT·PI·CI·PL·S/I 템플릿 렌더링(PDF·엑셀·언어 변형), 저장 전 검증 강제(금액 정합·G.W.≥N.W.·Incoterms 완전성·CI↔PL 교차), receivables(만기 자동·aging 30/60/90)/payments(부분), lc_terms(feature flag·하자 체크리스트 화면); **(v1.5 수정·추가)** `payments`는 **S3-1이 신설한 테이블의 확장**(채권 연결[`receivable_id`]·`pi_id` 완화·잔금·선수금 초과분 — 기존 컬럼·CHECK·kind 값 변경 금지), 미수 provider 등록(S3-1 기본 구현 `reflected=False`의 잔존 금지 아키텍처 테스트 포함), L/C feature flag 행 공급·토글 경로(`lc_terms`와 함께 — S3-1 프로덕션에서 L/C 선택은 닫혀 있음), 자사 레터헤드 마스터, EXPIRED/CANCELLED PI에 도착한 입금 처리 재판정, documents 전표 첨부(`owner_type` 확폭 경고), 선수금 미차감 노출·통화 불일치 입금의 재판정
- **DoD**: 교차 불일치 서류 저장 거부 / 무상(금액 0) 생성 가능 / aging 정확 / **(v1.5 추가)** 미수 provider 등록 후 "선적 확정~미수 발생 구간의 노출 공백 0·이중 계산 0" 테스트
- **검증**: B(교차 일치·G.W.·무상·한글 CSV), A(일부입금 전환)
- **(v1.6 주석 — S3-2 판정, `W:132`)** `lc_terms`는 S3-2 산식 순수 함수(L/C 대금만기·제시기한·tolerance — ADR-0081)에 **배선**한다(산식 재정의 금지). **SO COMPLETED 엣지·short-close는 미수 provider `reflected=True` 등록·선적분 노출 차감과 같은 PR**에서 연다. 대금만기·제시기한 **알림**(충족 신호 = 입금·제시)도 이 세션 판정 대상.
- **매핑 보강(이 계획 — 그룹 이름은 `D:443-453` 정의 그대로)**: §20 P3 = **`D:439` ① A·B·E·G·H·I·K — 이 중 E는 S3-3 해당 없음**(아래 E 행) + J [적대 R-32].
  - **A 전표·정합**: 채권 일부입금 전환·aging·역순 취소(채권·CI 생존 시 선행 취소 차단)·노출 공백/이중 0·COMPLETED/short-close.
  - **B 서류**: CI↔PL 교차 일치·G.W.<N.W. 거부(등호 통과)·무상 생성·한글·장문·BOM CSV·**검수 미완료 선적 CI·PL 차단**(`D:444` — S3-3이 B 그룹 첫 실케이스, 현재 47건). B/L draft 불일치 검출은 S5-3.
  - **E 비용·소싱**: **해당 없음**(비용 코어 S3-4 — PL CBM은 서류 값이지 §20 E 'CBM 경계값·최소요금'이 아니다).
  - **G AI·보안**: DESIGN 이름 그대로, 내용은 S3-1 부기(`D:439` ① "G = 파일 해시 멱등·원가 마스킹") 승계 — 렌더 산출물 해시 멱등·서류 원가 채널 0.
  - **H 운영**: 대금만기·제시기한 알림 dedup·에스컬레이션·재확인·플래그 OFF·복원 리허설 FILE 편입·검산.
  - **I 자동화·통합**: SO 자동 엣지 3·4금 논증·채권/CI/S/I 자동 생성 0·잡 14 실패 감지.
  - **J 안전 계약**: 실제 동시 채권 발생·입금·CI 발행·멱등·롤백·IMMUTABLE·열 GRANT 42501·평가 Barrier 경합.
  - **K 보안·품질**: **L/C 제시기한 MIN·tolerance API 경로 배선**·총수·authz·IDOR·Page·렌더 순수성·폰트·외부 임포트 허용·라벨 대사·출구 계약.
  - **GC v1.6 14건**(A22~A31·F5·F6·G4·H7 — 통합 §5.2) — 현행 GC v1.5는 S3-3 배정 0건(`W:224-241`).
- **문면과 다르게 확정한 14건(멈춰서 보고 → DESIGN 부기·ADR·WBS v1.7 주석으로 해소 — 적대 검토로 9 → 14)**
  - **WBS 대비 7건**: ① **"CI·PL·S/I 템플릿 렌더링"의 CI·PL·S/I 운영 발행은 S4-2 INSPECTED 개방까지 닫힌다** — **①-a CI·PL**: DESIGN §7.2 "CI/PL 생성은 검수완료 이후만"(`D:187`)·§20 B(`D:444`)가 검수 전 생성을 금지하고 검수(INSPECTED)는 S4-2에서야 도달 가능하므로 게이트를 지금 세운다. **①-b S/I**: DESIGN 근거가 아니라 **설계 선택(S/I 원천 = 살아 있는 CI)**에 따른 폐쇄다 — 선적 원천 S/I 개방안은 수하인 불일치 위험으로 기각(적대 R-10, ADR-0096). DoD "교차 불일치 거부·무상 생성"은 **서비스 층 시험**(시험 전용 팩토리 — ADR-0081 구조)으로 충족, QT·PI 렌더는 운영 개방(ADR-0096) ② **"receivables(만기 자동)"** → 만기는 **저장 없이 파생**(OPENING만 `due_on` 저장, ADR-0088) ③ **"lc_terms(feature flag)"** → 플래그 OFF = **새 입력만 닫음**(기존 L/C 기한 계산·알림 계속, ADR-0094) ④ **S3-4 Phase 3 리허설 "QT→채권 관통"의 CI·PL 단계 = 시스템 밖 작성**(runbook A-05 — S3-4 착수 세션이 '관통' 문면 재판정) ⑩ **"선수금 초과분"(`W:129`) = 미개방**(PI 초과 입금 422 유지 — 초과 현금 귀속은 사람 판단, 부채 B-02, 적대 R-09) ⑪ **"언어 변형" = QT·PI(EN·KO)만, CI·PL·S/I = EN만**(부채 A-03, 적대 R-33) ⑫ **대금만기·제시기한 알림(`W:132` v1.6 주석) = 수출만** — 수입(매입 채무 만기·수입 L/C 제시기한)은 UNKNOWN 표시 유지·알림 0, 부채 I-04(S6-2, 적대 R-08).
  - **DESIGN 대비 7건**: ⑤ §7.1 사슬 "선적 → CI/PL → … → 채권"(`D:177`) ↔ **채권은 CI 없이 선적에서 사람 1클릭으로 발생**(CI가 있으면 CI 값을 복사·CI 취소를 채권이 막음 — 통합 X-01, ADR-0088) — **문면 변경** ⑥ §7.10 [M4] 부기 ①(`D:227`) "미결 SO 합" ↔ **"미결 SO 잔여(총액 − 채권 전환분) 합" + SO 묶음 1회 환산**(ADR-0090) — 문면 변경 ⑦ §15 S3-2 부기 "SO 자동 엣지 2" ↔ **3**(IN_SHIPMENT→COMPLETED, ADR-0091) ⑧ §20 H "기능 플래그 오프 완전 비활성"(`D:450`) ↔ **입력 진입 비활성**으로 해석(끄면 기존 L/C 기한이 사라지는 fail-open 방지 — 해석 부기) ⑨ §7.6 "QT·PI·CI·PL·S/I·**DGD**"·"S/I 생성 + B/L draft 업로드 → 대조"(`D:215`) ↔ **DGD(S4-4)·B/L draft 대조(S5-3)는 S3-3 밖** ⑬ §7.10 "lc_terms 등록(**MT700 인테이크 → 초안**)"(`D:225`) ↔ **수기 입력만**(MT700 인테이크 = S6-1 AI 레이어, 부채 I-03 — 적대 R-01) ⑭ §7.10 [M4] ④(`D:227`) "선수금 입금분은 노출에서 차감하지 않는다" ↔ **채권 항은 선수금 FIFO 충당 후 미수**(노출 **감소** 방향 — 근거 = 현금 사실, 문면대로면 완납 채권의 선수금 몫이 노출에 영구 잔존해 운영 정지. SO 항 미차감은 유지·B-04. ④의 "기본 provider = 유일한 의도적 예외" 문장은 2b에서 은퇴 — 적대 R-02·R-07).
- **범위 밖 경계**: MT700 인테이크(S6-1) / 수입 대금만기·수입 L/C 배선(S6-2) / CI·PL·S/I 운영 발행(S4-2 — 게이트 실발효) / 선적 RESERVED 5상태 엣지·검수·출고 원장(S4-2) / DGD·MSDS 게이트(S4-4) / B/L draft 업로드·AI 대조·자동 적합 기록(S5-3) / PO 발주서 렌더·PO 소유 첨부(원가 채널) / 은행 CSV 입금 매칭(P7) / 대손·환차손익·분개(P6) / 바이어 독촉 발송(S5-4) / aging 대시보드 시각화(P6) / CI 목록 화면·메뉴(S4-2) / 레터헤드 로고·서명 이미지 / 이월 채권 CSV 일괄 반입.

## 2. 통합 결정 요약 (전건 자율 확정)

| # | 결정 | 근거 요지 |
|---|---|---|
| 1 | **채권 = 수출 선적당 살아 있는 1건, 발생 = 사람 1클릭 전용 경로 `POST /shipments/{id}/receivable`(A·T)** — 출고지시 이후 살아 있는 수출 선적만(PLANNED 409·수입 422), 금액 = 선적 `total_amount`(복합 FK `(shipment_id, currency, gross_amount)` → `shipments(id, currency, total_amount)`로 DB 강제), `invoice_ref` 필수, 만기 비저장 파생(OPENING만 저장), 상태 OPEN·CANCELLED, 금액 열 UPDATE 42501(열 GRANT), 채번 없음, `ChildLink(SHIPMENT→receivables)` | 통합 X-01·X-06·X-15, sB B1·B2, §20 A 역순 취소 |
| 2 | **채권과 CI를 분리한다** — CI 발행 TX는 receivables에 쓰지 않는다. CI가 살아 있는 선적의 채권은 `invoice_on`·`invoice_ref`를 서버가 CI에서 복사(본문 송신 422)·`ci_id`로 연결, `ChildLink(CI→receivables)`로 CI 취소·재발행 차단. 채권이 먼저 있는 선적의 CI 발행 = 409 `EXPORT_DOCS.CI.RECEIVABLE_EXISTS`(S4-2 재판정) | 문면대로 "CI 발행 = 채권 발생"을 합치면 검수 게이트 때문에 S3-3 운영 채권 0건 → DoD 운영 의미 소멸(통합 X-01) |
| 3 | **INVOICE_DATE 앵커 원천 = 채권 `invoice_on` 단일**(`AnchorContext.invoice` 가산·INVOICE_DATE 분기만 — 산식 본문 무변경, ETD 대체 금지), 배선은 aging DoD와 같은 PR-2a | X-04·X-05, ADR-0081, GC-A13 |
| 4 | **미수 = 채권 총액 − 채권 입금 − 선수금 충당(파생·비저장, `receivables/outstanding.py` 단일 정의)** — 충당 = 같은 SO PI 순입금의 id 오름차순 FIFO, 미수 ≥ 0(clamp), 입금 상태 UNPAID·PARTIALLY_PAID·PAID 파생. P-15: 채권 항 = 충당 후, SO 항 = 미차감 유지 — **`D:227` ④ 문면 변경(DESIGN 대비 ⑭)**. 종결 SO의 미충당 선수금 > 0은 검산 잡이 ADMIN 알림(B-02 감지 — 적대 R-02·R-29) | sB B4, X-31 |
| 5 | **payments 확장(P-09)** — `pi_id` 완화·`receivable_id`·"정확히 하나" CHECK·역기록 복합 FK 2벌·통화/거래처 복합 FK, 기존 열·CHECK·kind 무변경. 역기록 = peek → 대상 분기 → 거래처 잠금 → 대상별 잠금. 이중 입력 의심 409 + 정확히 덮는 ack(P-14, PI 경로 포함 — **PR-2d, PI 패널 최소 확인 블록 동반**). **PI 입금(T13)도 거래처 잠금 선행**(같은 송금 교차 경합 차단). 통화 불일치 422·만료/취소 PI 입금·**역기록** 409 **유지**(P-12 재판정 — 닫힌 PI엔 살아 있는 SO가 없어 충당 무관). 선수금 초과분 미개방(WBS 대비 ⑩) — 적대 R-09·R-13·R-20·R-21 | sB B3·B5, X-19, `W:129` |
| 6 | **여신 노출 전환(P-01·P-08)** — provider 실구현을 `app/bootstrap.py`로 **2 입구(`create_app`·`cli.main` — worker = `cli run-scheduler`)** 등록(시험은 세션 autouse 픽스처로 보장·교체는 저장/복원 컨텍스트), **Protocol = `exposure_parts(session, partner_id, so_ids)`**(SO별 `(invoiced_gross, outstanding)` SO 통화 원액 + SO 밖 미수 행 — 기존 `outstanding` 폐기), 차감 = **채권 전환분만**(선적 생성·출고지시는 차감 근거 아님 → 공백 0), 환산은 평가 함수만 — SO 묶음(SO 잔여 + 그 SO 미수) 1회 HALF_UP(반올림 차 0), **2b부터 기본 provider 평가 = UNEVALUABLE `RECEIVABLE_PROVIDER_NOT_REGISTERED`**('부분 노출' 경로 은퇴), `CLOSED_STATUSES` 무변경 — 적대 R-04~R-07·R-24 | sB B6·B8, `code:modules/credit/exposure.py:19,37-39` |
| 7 | **노출 구성 변경 쓰기 = `lock_buyer_for_credit` 선행**(채권 발생·OPENING·취소·입금·역기록 양 분기·short-close) — 평가의 SO 항·provider 문장 스냅샷 사이 창 차단, DoD "공백 0·이중 0"의 동시성 조건(GC-F5 결정적 판) | sC C3, X-20, `code:modules/credit/evaluation.py:165-193` |
| 8 | **SO COMPLETED 자동 엣지 1(IN_SHIPMENT→COMPLETED)·short-close(사람 결정 3열·사유)** — 완결 판정 함수 1개(호출처 = 채권 발생·short-close 2곳), 채권 없는 살아 있는 선적이 있으면 short-close 409, COMPLETED 되돌림 엣지 0·채권 취소 409, 오더 보드 제외 집합. 총수 31/151/182(2b) → 32/152/184(5a). **ADR-0076 대체**, 결속 시험 `test_doc_machines.py:141-153` 대체 | sB B7, X-14·X-35, N-02, `W:132` "같은 PR" |
| 9 | **LOCK_ORDER** `… sales_orders → lc_terms → purchase_orders → shipments → shipment_children → commercial_invoices → receivables → approvals → lines → seq` — 슬롯은 표가 생기는 PR에서만, **PI SHARE 뒤 `lock_chain` 금지**(채권 발생은 PI 무잠금) | sC C2, X-17·X-18 |
| 10 | **CI 커널 편입**(DocKind `COMMERCIAL_INVOICE`·접두어 CI·상태 ISSUED·CANCELLED·사람 엣지 1·생성=발행=동결), PL = CI 구성 요소(번호 공유, 그램·mm 정수·CBM 파생·G.W. ≥ N.W. DB CHECK·포장 번호 연속), S/I = 비커널 불변(채번 SI·운임 조건 Incoterms 도출·포워더 필수·지시식 = L/C 술어 True일 때만), 재발행 = 새 번호 1TX(`supersedes_ci_id`·`revision_no`), 선적당 살아 있는 CI 1, 헤더 열 단위 UPDATE | sA §A4~A8·A11, X-09·X-12·X-13 |
| 11 | **검수 게이트 선배치** — CI(+PL)·S/I 발행 = 선적 INSPECTED·RELEASED·SHIPPED만(409 `NOT_INSPECTED`. CI·PL은 `D:187` 근거, S/I는 원천 = CI 설계 선택 — R-10), **S4-2까지 운영 발행 0**, 미리보기(JSON·저장 0)는 RELEASE_ORDERED부터, DoD는 시험 전용 팩토리로 서비스 층, runbook 외부 작성 안내(A-05) | sA §A2, `D:187`·`D:444` |
| 12 | **원천 스냅샷 규칙** — 참조 원천(선적·SO·PI)은 복사, 마스터(SKU DG·HS 행·레터헤드·은행)는 발행 시 1회 읽기, 판단(원산지·HS 선택·운송 사실)은 사람 입력. 결측을 다른 값으로 메우지 않는다(서버 기본값·자동 선택 0 — 같은 통화 계좌 1개여도) | sA §A9, §15 L3 법적 판정 |
| 13 | **저장 전 검증 = 순수 함수 1개(`trade_docs/doc_validation.py`)** — 미리보기·발행 공용, BLOCK(422)·NOTICE(사람 확인 코드 저장 — Incoterms 해상 전용+비해상·DG 수동 점검), override 0. 반올림은 표시 전용 2곳 | sA §A10, `D:215` "검증 강제" |
| 14 | **렌더 = 서버 내 ReportLab(invariant)·openpyxl(문자열 셀)·동봉 NanumGothic(sha256)·템플릿 판 동결·외부 호출 0, 산출물 저장(documents FILE + 불변 renditions — 첫 파일 정본)·생성물 삭제 잠금·**생성물 다운로드 = QT·PI A·T / CI·PL·S/I A·T·L + audit 1행**·QT/PI 운영 개방(렌더 게이트 = 원천 `frozen_at IS NOT NULL` — 초안 폐기 취소 QT 409, R-14)·**ReportLab 마크업 이스케이프 단일 함수 + `reportlab ≥ 3.6.13` + 신뢰 스킴·호스트 비움**(R-03)·뷰 데이터클래스 허용 필드 고정(`internal_note`·담당자 0 — R-28)·렌더 멱등 지문 = 요청 본문만(R-27)·원격 로드 차단 | sA §A12·A13 + sC C7, X-02 |
| 15 | **자사 레터헤드 = 불변 판 + as-of**(대체 금지, 첫 판 과거 유효일 등록이 유일한 해소 — **과거 날짜는 판 0개일 때만, 이후 판은 오늘·기존 최댓값 이상만**(422 `BACKDATED`), 등록은 advisory 잠금 직렬화 — R-15), Shipper 블록 = 레터헤드(R-3a-5), 등록·조회 ADMIN, 로고 없음 | sA §A3, X-07·X-08 |
| 16 | **documents 확폭(P-13)** — VARCHAR(24)·6종(QT·PI·SO·SHIPMENT[수출만]·CI·SI), PO·ORDER_INTAKE 미개방, 전표 첨부 쓰기 A·T(CERT 403을 404보다 먼저)·**전표 소유 첨부 삭제도 A·T**(404 → 403 → 409 — R-18)·업로드·링크 스키마 `owner_type` 길이 = 열 길이 상수(R-11)·documents 쓰기 스키마 forbid(P-50 — R-12), 소유 확인 = 해석기 레지스트리(documents → 전표 임포트 0), `/documents` 통제 접두어 편입 | sA §A13, sC C6·C12, X-10·X-11·X-23 |
| 17 | **L/C(P-10)** — `lc_terms`(lc_number·SO당 살아 있는 1·개정 SUPERSEDED)·제시 기록·하자 체크 마크(IMMUTABLE·판정 필드 0), S3-2 순수 함수에 **배선만**(`milestone_view` 1곳), **하자 체크 항목 카탈로그 14종(UCP600 출처 표시 — 판정 0)·LT1~LT4 API·화면(tolerance 상·하한 = 서버 문자열, 임박 적색 = 서버 `urgency`)**, 첫 마크·직전 마크 부분 유니크(R-19), 입력 3종은 플래그 행 `FOR SHARE`(R-31), MT700 인테이크 범위 밖(R-01), tolerance 상한 초과 채권 422, 플래그 = `PUT /feature-flags/{code}`(ADMIN·폐쇄 레지스트리 `{"lc"}`·시드 0), **OFF = 입력만**, G-09 종결 | sB B10·B11, X-13, N-08 |
| 18 | **aging 30/60/90 + `DUE_UNKNOWN`**(미도래 아님·적색·사유 코드), 거래처×통화 합(통화 간 합산 0), 기준일 = 서버 KST 오늘(파라미터 없음), 목록 필터 = 저장 열만 | sB B12, sD D2-5, X-28 |
| 19 | **잡 14 유지** — `trade-deadline-scan`에 PAYMENT_DUE(충족 = 미수 0, **채권 없음 = 미충족 → 알림**)·PRESENTATION_DEADLINE(충족 = 제시 기록)·L/C 유효/선적기일·OPENING 만기 가산, 후보 OR 확장(실적 다 들어간 동결 선적 회귀), **대상 = 수출 선적만**(수입 이월 I-04 — R-08), 검산 잡에 '종결 SO 미충당 선수금' 점검(R-29), 수신 = SO 담당, 발송 직전 재확인, 허용 임포트 = 쓰기 없는 서브모듈만 | sB B13, sC C10, X-30~X-32 |
| 20 | **권한(경로 단위)** — CI 발행·재발행·취소·채권·입금·short-close·L/C 쓰기 = A·T / CI 미리보기·S/I·렌더 재시도 = A·T·L / OPENING·채권 취소·플래그·레터헤드 = A / 조회 전 역할(CI 상세 계좌번호는 A·T·L만, **CI 미리보기 계좌 후보·요약은 A·T만 — L은 `bank.source`만, 목록·CSV 계좌번호 열 0** — R-17), `GOVERNED_PREFIXES` +6 | sC C6, X-03·X-23·X-27 |
| 21 | **R-3a-4 해소** — 당사자 추가·삭제가 선적 version+1, CI STALE = 수하인·통지처 **값 비교**(파생 배지), STALE CI로 S/I 발행 409 `EXPORT_DOCS.CI.SOURCE_CHANGED` | sC C4, X-21 |
| 22 | **P-39 해소** — Idempotency-Key 1~128자·제어문자 금지 422, `TODAY_IMPORT_POINTS` 일반화+커버리지 시험(KST 자정 운 제거) | sC C5·C13 |
| 23 | **화면 계약** — S3-3 신규 동작 버튼 = 서버 `allowed_actions`만(매트릭스 대사 K), 파일 다운로드 통로 단일, CI 작성 전용 화면(원천 선적 경로·탭 임시 저장), 채권 등록 2단(`will_complete` 표시), 멱등 키 화면 수준 Map, PI '선수금 입금완료'(PR-16 ⑧), 셸 역할 한국어(R-8-2), 라벨 대사 4표(R-6-4) | sD D1·D4·D9·D11·D17·D19·D20 |

## 3. 범위·비범위

**신규 12테이블**(정본: design-integrated §2.1): receivables / company_profiles / trade_document_renditions / lc_terms · lc_presentations · lc_checklist_marks / commercial_invoices · commercial_invoice_lines · commercial_invoice_status_log · packing_list_packages · packing_list_package_items / shipping_instructions.
**기존 변경**: payments 확장(**M16b** — R-21) · sales_orders `UNIQUE(id, buyer_partner_id)`(M16)·short-close 3열(M17) · SO 상태이력 `reason_required`·`reason_not_blank`(BLANK_CHAR_CLASS — R-12) CHECK(M17) · shipments `UNIQUE(id, so_id)`·`UNIQUE(id, currency, total_amount)`(M16) · documents `owner_type` 확폭·6종(M18) · document_types 시드 5행(M19·M21·M22) · receivables `ci_id`(M21). (DocKind 교차 표 CHECK 확장은 실측 0 — 삭제, R-26) 코드 레지스트리 변경(DocKind·machine·chain·locking·policy·providers·evaluation·milestone_view·deadline_scan·order_board·handover·documents 해석기·bootstrap)은 design-integrated §2.4.
**IMMUTABLE 13 → 21표**(코드 실측 — DESIGN §17.5 확장분 계수 11 → 19, R-22)(+company_profiles·renditions·lc_checklist_marks·CI 라인·CI 상태이력·PL 2표·S/I), **열 단위 UPDATE 3 → 5**(+commercial_invoices·receivables). **마이그레이션 8건**(M16·**M16b**·M17~M22, `down_revision` 사슬 `281da4794717`→M16→M16b→M17→…→M22, downgrade는 새 행·값이 있으면 RAISE). 시드 = `document_types`만.
**에러 코드 신규 45종**(EXPORT_DOCS 19·DOCUMENTS 2·RECEIVABLES 12·PAYMENTS 1·SALES_ORDERS 3·PLATFORM 1·COMMON 1·LC_TERMS 6 — 통합 §2.6 + §9 R-38), 커널 기존 코드 재사용 우선.
**신규 외부 의존성 2**: `reportlab`(== 고정, **하한 ≥ 3.6.13 단언**)·`openpyxl`(== 고정, `doc_render`만 임포트, tzdata 줄 diff 0 — R-4a-9 발동 R-36) + 동봉 폰트 + 시험용 PDF 추출 dev 도구(3b 커밋 ① 실측).

**비범위(이 세션에서 만들지 않는 것 — 명시)**
- CI·PL·S/I **운영** 발행(S4-2 INSPECTED 개방) / CI 목록 화면·메뉴 / 채권 선행 선적의 CI 전환 경로(S4-2 재판정)
- DGD / B/L draft 업로드·대조·자동 적합 기록 / 서류 자동 발송 / PO 발주서 렌더·PO·수입선적·주문 접수 소유 첨부
- 채권 자동 생성·CI 발행 동반 채권 / 선적 1건 다중 인보이스 / 초과·미배정 입금·환불 / 대손·환차손익 / 은행 CSV 입금 매칭 / SO 항 선수금 차감 / 이월 채권 CSV
- 1 L/C : N SO / 이중 통화 L/C / L/C 하한 tolerance 판정 / 하자 '적합' 판정 / **MT700 인테이크**(S6-1) / **수입 L/C·수입 대금만기 알림**(S6-2) / 선수금 초과 입금 수용
- 레터헤드 로고·서명 / CI·PL·S/I KO 변형 / 무상 세관 신고가액·보험 정보 열 / SKU 용량 열 / PDF 인라인 미리보기 / aging 과거 기준일·대시보드

## 4. PR 분할 (16 PR 수직 슬라이스 — 각 병합이 동작 가능, 직렬 병합. 적대 R-21로 PR-2d 신설)

| PR | 범위 | 마이그 | DoD·검증 배치 | GC |
|---|---|---|---|---|
| **PR-1** | 문서 전용: 이 계획서+부록 A~E+통합 등재(부록 머리 우선순위·[통합 X-nn] 표지) · DESIGN [M5] 보강 부기(통합 §4) · ADR **0088~0099** + 기존 ADR 부기 **17건**(+0068·0055 — R-34) · WBS v1.7 · GC v1.6(**등재 전 그룹 배치 확정 — A22~A31·F5·F6·G4·H7**, A11·A13 비고 부기 — R-35) · 부록 머리 [적대 R-nn] 표지(R-40) · PROGRESS '## 현재'·자율 확정 표기·부채 등재(통합 §6) · runbook 'S3-3 운영 개시' 초안 줄 | — | 문서 린트(ADR 5줄·WBS 행·GC 이력) / 기존 전체 테스트 green | 등재 |
| **PR-1b** | **공통 기반(마이그 0)**: P-39 키 길이·제어문자 422(`code:api/deps.py:94-109`) · `MILESTONE_TODAY_IMPORT_POINTS` → `TODAY_IMPORT_POINTS` 이름 변경·일반화+커버리지 시험(R-37) · `.shard_durations.json` 재갱신(Q-15) | — | J(CJ-18 129자 422·128자 정상·제어문자)·K(TODAY 커버리지 변이) | — |
| **PR-2a** | **채권 원장**: receivables·채권 발생 T8(미리보기 RV1 포함 — `will_complete`는 2b 배선)·OPENING T9(P-51)·취소 T10·미수 `receivables/outstanding.py`(이 시점 = 총액 − 선수금 FIFO 충당, 채권 입금 항은 2d)·**INVOICE_DATE 앵커 배선**·`milestone_view` PAYMENT_DUE `settlement`·`is_overdue`(미충족만)·aging·목록 CSV·선적/SO 응답 채권 블록·`ChildLink(SHIPMENT→receivables)`·LOCK_ORDER `receivables`·거래처 잠금 규칙·`lock_document(read=)` 확장(R-25)·신규 비공백 CHECK = `BLANK_CHAR_CLASS`(R-12)·authz·`GOVERNED_PREFIXES` receivables. **노출 영향 0**(provider 기본 — K 시험 "채권 발생 전후 노출 감소 0"으로 고정) | **M16** | **DoD "aging 정확"**(GB-25 경계 8점)·A(GB-04~07·17)·J(CJ-02·04·12[receivables 제약분]·14·15·17[OPENING·채권 발생 재생]·19·21, GC-F6)·K(authz·Page·IDOR·제약 ⊆ 번역표)·B(채권 CSV 한글·BOM) | A27·A30(앵커 층)·F6 |
| **PR-2d** | **입금 확장(INSERT-only 원장 — 독립 검토, 3렌즈)**: M16b payments 확장(`pi_id` 완화·`receivable_id`·정확히 하나 CHECK·역기록 복합 FK 2벌·**3열 복합 FK `(receivable_id, partner_id, received_currency)` 확정** — R-23)·채권 입금 T11·역기록 분기 T12(peek → 거래처 잠금 → 대상)·**PI 입금 T13 거래처 잠금 선행**(R-20)·미수에 채권 입금 항 가산·이중 입력 의심(P-14, **채권·PI 양 경로** + `pi-payments-panel.tsx` 최소 확인 블록)·`payment_flow.py:6` 독스트링 정정 | **M16b** | **검증 A "일부입금 전환"**(GB-13)·A(GB-11·12·16, 채권 입금·역기록·초과 422)·J(CJ-03·12[payments 제약분]·16·17[채권 입금·ack 재생], GB-14·15, 다른 SO 채권 입금 ∥ PI 입금 같은 송금 → 1건 409)·K(번역표 `CONSTRAINT_ERRORS` 확장)·vitest(PI 패널 ack 전 저장 비활성) | A26 |
| **PR-2b** | **노출 전환(원자 — 쪼개지 않음)**: 커밋 ① Protocol **`exposure_parts` 재정의**(기존 `outstanding` 폐기)+가짜 provider 7개 재작성+평가 재배치(SO 묶음 환산 — R-05·R-06) → ② provider 실구현+`app/bootstrap.py` **2 입구**+세션 autouse 등록·저장/복원 교체 컨텍스트·documents 해석기 같은 함수에서 등록(R-04·R-24)+**기본 provider = UNEVALUABLE**(R-07) → M17+SO COMPLETED 자동 엣지·RESERVED 축소·TERMINAL·REASON_REQUIRED_TO+오더 보드 제외+결속 시험 **대체**+총수 31/151/182 → `converge_sales_order_completion`+short-close API+채권 발생 수렴 배선+응답 COMPLETED 분기·`opening_receivables_count` → DoD 시험·동시성. REGISTRY 엔트리·§15 4금 | **M17** | **DoD "노출 공백 0·이중 0"**(GB-01~03 + 속성 시험 + CJ-07 결정적 판)·A(GB-08~10)·I(SO 자동 엣지 정확히 3·4금·REGISTRY·**COMPLETED 엣지 존재 ⇒ 기본 provider 평가 WITHIN_LIMIT 불가**)·J(CJ-05·06·08, GC-F5)·K(2 입구 `not is_default_provider()` — GB-27, 무작위 순서 2회 provider·해석기 상태 의존 0) | A28·A29·F5·H7(채권 층) |
| **PR-2c** | **채권 화면**: 채권 목록·aging 탭·채권 상세·입금 패널 공용화(`PaymentsPanel`)·이중 입력 확인 블록·이월 채권(ADMIN)·채권 취소·선적 상세 '채권' 섹션·채권 등록 2단·SO '완료'·잔량 종결·채권 요약·SO 목록 '완료' 필터·보드 문구·대금만기 D-N 3상태·사유 라벨 개정·여신 카드 '미수 내역'·이월 안내·PI '선수금 입금완료'(PR-16 ⑧)·'미수 미반영' 배지 삭제+`RECEIVABLE_PROVIDER_NOT_REGISTERED` 라벨(R-07)·SO '초과 입금 의심' 배지(R-29)·셸 '채권' 메뉴·역할 한국어(R-8-2)·`useIdempotencyKeys` | — | vitest(`DUE_UNKNOWN` ≠ 미도래 적색·통화 간 합산 0·파생 필터 UI 0·사유 빈칸 제출 불가·ack 전 저장 비활성·미리보기 전 등록 버튼 0·같은 본문 = 같은 키)·`detail-layout` 하한 상향·390px | — |
| **PR-3a** | **레터헤드·전표 첨부**: company_profiles(불변 판·as-of·사업자번호 체크섬·미래 판 422·**소급 판 422 `BACKDATED`·advisory 직렬화** — R-15)·ADMIN API·documents `owner_type` 확폭 6종·**업로드·링크 스키마 길이 상수 결속**(R-11)·documents 쓰기 스키마 forbid(R-12)·해석기 레지스트리·PO 소유 미개방·수입선적 첨부 422·전표 첨부 A·T(403 순서)·**전표 소유 첨부 삭제 A·T**(R-18)·`GOVERNED_PREFIXES` company-profiles·documents(기존 행 실측 등재) | **M18** | A(as-of 대체 금지·두 번째 판 과거 날짜 422)·J(IMMUTABLE 42501·첫 판 동시 2건)·K(owner_type별 403 순서 CK-03·6종 × 업로드·링크 12행·CERT 삭제 403·임포트 방향)·G(원가 채널 0) | G4(첨부 층) |
| **PR-3b** | **렌더 엔진 + QT·PI 렌더**: 커밋 ① 의존성 `reportlab`(≥ 3.6.13 단언)·`openpyxl` ==·폰트(OFL·sha256)·mypy 설정·CI 설치 시간 실측·tzdata diff 0(R-36) → `doc_render`(순수 — `markup_safe` 단일 이스케이프·`Paragraph` 1곳·신뢰 스킴 비움 R-03·뷰 허용 필드 R-28)·템플릿 판 digest·글리프 커버리지·QT/PI PDF·XLSX·EN/KO·renditions·**생성물 삭제 잠금·다운로드 역할(QT·PI A·T)·audit**·documents `is_generated` 응답·주입 방어·무상 표기 상수·렌더 게이트 `frozen_at IS NOT NULL`(R-14)·렌더 멱등 지문 = 본문만(R-27) | **M19** | B(한글·장문·무상 QT/PI 렌더·DRAFT 취소 QT 409)·G(renditions 해시 멱등 **PDF 한정**·XLSX 같은 키 = 같은 document_id·경쟁 렌더 1·고아 0·원가 키·`internal_note`·담당자 0)·K(순수성 AST·폰트 digest·외부 임포트 허용 = `doc_render`만·CK-09 주입 + `<img>`·`<a>`·`<font color>` 원문 출력·파일/소켓 접근 0·다운로드 attachment·생성물 L·C·V 403)·H(복원 리허설 FILE 대상) | A24(QT/PI 층)·G4(렌더 층) |
| **PR-3c** | 자사 정보 화면(판 목록·새 판 — 적용 시작일 기본값 없음)·QT/PI '서류 파일' 2×2 패널·문서보관소 라벨 6종·생성물 배지·삭제 버튼 숨김·전표 상세 첨부 패널 | — | vitest(첫 판 미등록·NOT_EFFECTIVE 안내·DRAFT 안내·`new Date` 금지 확장)·390px | — |
| **PR-4a** | **L/C**: lc_terms **LT1~LT4**(R-01 — `SO_NOT_LC`·`SO_CLOSED`·`latest_shipment_on ≤ expiry_on`)·제시 기록·하자 체크 마크(**항목 카탈로그 14종**·첫 마크/직전 마크 부분 유니크·복합 FK — R-19)·입력 3종 플래그 행 `FOR SHARE`(R-31)·응답 `tolerance_bounds`·`urgency`·`lc_inputs_for` 어댑터·`milestone_view` PRESENTATION_DEADLINE·PAYMENT_DUE L/C 배선·`presented_on`·tolerance 상한 채권 422(2a 함수 분기 — 2a 시험 재실행)·`PUT/GET /feature-flags`(P-10)·`lc_allows_order_consignee`·LOCK_ORDER `lc_terms`·`GOVERNED_PREFIXES` feature-flags·G-09 종결. `schedule.py` 함수 본문 diff 0 명기 | **M20** | **검증 K "L/C 제시기한 MIN·tolerance" API 경로**(CK-13 = GB-18·20)·A(GB-19 — GC-A16 무변경)·H(GB-26·CH-09 플래그 OFF = 입력만)·J(CJ-13·동시 마크 2건 → 1 409·OFF 토글 ∥ 등록)·I(CI-09 판정 필드 0)·K(항목 14 = 프런트 라벨 키 대사) | A30(L/C 층) |
| **PR-4b** | L/C 조건·개정·제시 기록·하자 체크리스트 화면·정책 설정 '기능 켜기·끄기' | — | vitest(OFF 후 기존 기한 표시 유지·체크리스트 '적합' 문구 0·근거 빈칸 제출 불가·**tolerance 상·하한 = 서버 문자열**·**`IMMINENT`·`OVERDUE` 적색+글자**·끄기 확인 문구 — R-01) | — |
| **PR-5a** | **CI·PL 발행**: 커밋 ① M21+모델+table_policy+DocKind·dict 11·FIELD_POLICY·**CI 상태 기계(사람 엣지 1)**·사슬 레지스트리·총수 32/152/184 **한 커밋** → ② 검수 게이트 상수+결속 시험 2 → ③ 검증 카탈로그(순수) → ④ 발행·재발행·취소·자동 렌더 4·채권 선행 409·`receivables.ci_id`·`ChildLink(CI→receivables)`·채권 발생의 CI 값 복사(2a 함수 분기)·계좌번호 마스킹·**미리보기 계좌 후보 A·T만·목록/CSV 계좌번호 열 0**(R-17)·CI 상태이력 비공백 = `BLANK_CHAR_CLASS`(R-12)·생성물 다운로드 A·T·L·`ASSIGNMENT_TARGETS` → ⑤ R-3a-4 version+1·STALE → ⑥ 동시성 | **M21**(DocKind 교차 표 CHECK 0 — R-26) | **DoD "교차 불일치 저장 거부"·"무상 생성"(서비스 층 — 시험 전용 팩토리)**·**§20 B 검수 미완료 차단(운영 경로 409)**·A(재발행 1TX·역순·CI 생존 채권 취소 순서)·J(CJ-01·11·22·동시 발행 1건·IMMUTABLE·열 GRANT)·K(총수·커널 3자 대사·팩토리 앱 호출 0·계좌번호 마스킹·L·C·V 응답 `account_no` 원문 0)·I(CI-08 자동 발행 0·CI 발행이 receivables 미접촉)·H(CH-12 검산 자동 편입) | A22·A23·A24(CI 층)·A25·A30(CI 복사 층)·G4(발행 층)·H7(CI 층) |
| **PR-5b** | **S/I 발행**: `shipping_instructions`(채번 SI·불변·재발행)·TO ORDER 술어 소비(Q-06)·Incoterms → 운임 도출·포워더 필수·DG UN·Class 필수·STALE CI 409·**첫 S/I 부분 유니크 + 409 `SI.ALREADY_ISSUED`·T6 선적 `FOR UPDATE`**(R-16) | **M22** | B(sA §A21 ⑨)·J(첫 발행 동시 2건 → 1 409·재발행 동시·IMMUTABLE)·K(TO ORDER 술어 기본 False 변이) | — |
| **PR-5c** | 선적 상세 '수출 서류' 섹션(게이트 문구)·CI·PL 작성 전용 화면(2단·NOTICE 확인·입력 보존·낡은 미리보기 표시)·CI 상세·재발행·취소·S/I 대화상자 | — | vitest(BLOCK 시 발행 비활성·NOTICE 미확인 불가·HS 기본 미선택·출항일 자동 기입 0·운임 입력 칸 0·게이트 배너)·390px | — |
| **PR-6** | **기일 스캔 확장(마이그 0, JOB 14 유지)**: 첫 커밋 = CH-05 **실패하는 시험 먼저** → 후보 OR 확장·PAYMENT_DUE·PRESENTATION_DEADLINE·LC_EXPIRY·LC_LATEST_SHIPMENT·OPENING 만기(**수출 선적만** — R-08)·검산 잡 '종결 SO 미충당 선수금 > 0' ADMIN 알림(R-29)·SO 담당 수신·재확인·허용 임포트·`alert-routes.ts` `receivables`·`commercial_invoices` 1줄씩·라벨 대사(알림 종류·파생 사유 — R-6-4) | — | **Q-08 수출분 종결**(수입 이월 I-04)·H(CH-01~15·종결 SO 미충당 선수금 알림 dedup)·I(CI-03 레지스트리·시각 무변경·CI-04·05)·K(CK-14 출구·라벨 대사) | A31 |
| **PR-7** | **마감**: runbook 'S3-3 운영 개시'(레터헤드 첫 판[과거 유효일]·OPENING 반입 순서[등록 → 확인 → 한도 복원]·채권 등록 버튼·short-close 판단·이중 입력 확인·통화 불일치 입금·`lc` 토글 클릭 단위·**CI/PL/S-I 외부 작성 안내 A-05**·`owner_type` 확폭 배포 시간대·수기 양식 채권/입금 행)·워크스루(렌즈 11)·`docs/testing.md`·`golden` 대사(≥99)·WBS/GC 확정·PROGRESS 종결·부채 최종 목록 | — | 워크스루 3층(ADR-0086)·11렌즈·CH-14 runbook 계약 | 전건 대사 |

**의존(병합 순서 정본)**: 1 → 1b → 2a → **2d** → 2b → 2c → 3a → 3b → 3c → 4a → 4b → 5a → 5b → 5c → 6 → 7. 고정 간선: 2a → 2d(입금은 채권 원장 위) / 2d → 2b(노출 전환은 미수 정의 완성 위) / 2a → 2b(노출 전환은 채권 원장 위) / 2a → 4a(tolerance 차단이 채권 발생에) / 3a → 3b(렌더가 레터헤드 as-of) / 2a·3a·3b → 5a(채권 복사·레터헤드 FK·자동 렌더) / 5a → 5b(S/I 원천 = 살아 있는 CI) / 4a →(연성) 5b(TO ORDER 술어 — 없으면 False라 fail-closed) / 2a·2d·2b·4a·5a·2c → 6 / 전부 → 7. **2c는 2b 뒤**(2a 단독 구간에 채권 버튼이 생기면 노출 전환 전 채권이 운영에 쌓인다 — 2a~2b 구간은 API뿐). 채권 축(2)을 서류 축(3·5)보다 앞에 둔다(WBS가 결속한 유일한 "같은 PR" 의무·P-01/P-08 두 세션째 이월·최대 교차 위험 조기 노출·CI는 S4-2까지 운영 가치 0) — QT·PI 렌더 운영 가치를 앞당기려면 3a·3b를 2a 앞으로 옮겨도 의존 위반이 없다(DAG만 재정렬). b·c PR은 짝 a 병합 후 최신 main에서 시작.
**같은 커밋 묶음(분리 금지)**: provider 등록 ↔ 채권 전환분 차감 ↔ SO COMPLETED 엣지 ↔ 결속 시험 대체(2b — `W:132`) / SO 상태 기계 ↔ M17 상태이력 CHECK ↔ 오더 보드 제외 집합 ↔ 총수 핀(2b 커밋 ③) / CI DocKind·dict 11·상태 기계(사람 엣지 1)·M21 모델·FIELD_POLICY·사슬 레지스트리·총수 핀(5a 커밋 ① — S3-2 R-23 선례) / 체인 FK ↔ `CHILD_LINKS`·허용목록(2a·5a·5b) / 신규 표 ↔ `_NEVER_SEEDED`·table_policy·users FK 분류 / LOCK_ORDER 슬롯 ↔ 그 표 / 새 알림 entity_type ↔ `alert-routes.ts` 1줄.
**PR별 공통 절차**: 작은 커밋 → 마이그레이션 왕복·`alembic check`·전체 pytest·vitest·ruff·mypy·typecheck·build → 변이 점검(design-E §E6-3 최소 목록 + 통합 §9 R-39 + 통합 결정분: 2a "CI 있는데 본문 invoice 수용"은 5a, 3b "생성물 다운로드 역할 해제", 5a "CI 발행이 receivables INSERT"·"채권 선행 CI 허용"·"계좌번호 마스킹 제거"·"STALE 판정 제거") — 전원 kill·error 0, junitxml 판정 → 실기동 관통(렌즈 11) → 자기 적대 검증(2렌즈 — **2b·5a는 3렌즈+2차, 3b·2d는 3렌즈**, 검증자는 테스트 실행 금지) → PR·CI 전 체크런(`ci-ok` 포함) green·mergeable clean → API squash 병합 → 브랜치를 최신 main에서 재시작. PR-1은 계획 세션 이어쓰기(착수 블록 A항 세션 확인 가드 — 부록 A~E·통합 맥락이 없는 세션이면 작업 없이 정지).

## 5. 되돌리기 비용이 높은 항목 — 번복 가능성과 함께 (자율 확정)

1. **CI 커널 편입(DocKind 저장값, 5a)** — 운영 데이터 전 중간 / 후 **높음**. 단 검수 게이트로 S4-2까지 운영 CI 행 0이 기대되어 되돌리기 창이 S4-2까지 열린다. → PR-1 착수 시 프로덕션 실데이터 유무 확인.
2. **채권·CI 분리(결정 #2, DESIGN §7.1 문면 변경)** — 번복 중간(S4-2가 단일 경로로 합치면 엔드포인트 폐쇄 + 호출부 1곳 + 기존 채권의 `ci_id` 백필 판정). **오너 확인 권장 1순위**(DESIGN 사슬 문면 변경 + S4-2 전환 비용을 지금 확정).
3. **노출 산식 전환(2b — `D:227` ①·**④** 문면 변경)** — ① 미결 SO 잔여(채권 전환분 차감)와 ④ 채권 항 선수금 충당 차감(DESIGN 대비 ⑭, 노출 감소 방향 — 적대 R-02)을 한 묶음으로 본다. 기본 provider fail-closed 전환(R-07)도 같은 PR. 번복 중간. 반대로 공백이 실재하면 **높음**(과소 노출 위 확정은 소급 불가 — ADR-0076 근거 승계). **오너 확인 권장 2순위**.
4. **검수 게이트 선배치(S3-3~S4-2 운영 CI 0)** — 번복 낮음(상수 1개 + 시험 2건 + runbook 문장). 단 번복 후 발행된 CI는 소급 회수 불가라 **비대칭**. 운영 사용자 기대와 어긋나므로 **오너 확인 권장 3순위**.
5. **SO COMPLETED 자동 엣지·short-close(§15 문면 개정)** — 중간(상태이력 CHECK·총수 핀·SO 3열·COMPLETED 행이 생기면 downgrade RAISE).
6. **payments 확장(M16b — PR-2d로 분리, 적대 R-21)** — 높음(INSERT-only 원장 스키마 — 채권 입금 행이 쌓이면 되돌릴 수 없다). 그래서 독립 PR·3렌즈, MATCH SIMPLE·3열 통화/거래처 복합 FK 시험을 2d 첫 커밋에.
7. **렌더 산출물 저장(첫 파일 정본)** — 중간(비저장 전환 시에도 쌓인 정본 파일은 보존 의무 — 단방향).
8. **불변 판 레터헤드·IMMUTABLE 8표** — 중간(열 추가는 NULL로만, 해제는 권한 마이그레이션).
9. **LOCK_ORDER 개정** — 중간(교착 재검증 — 계측 CJ-09·CJ-10이 비용을 줄인다).
10. **생성물 다운로드 역할 축소·CI 계좌번호 마스킹** — 낮음(넓히기 행 1줄) / 좁히기 중간.
11. **`lc` OFF = 입력만(§20 H 해석)** — 낮음(술어 1곳). 해석 부기라 **오너 확인 권장 4순위**.
12. **CI 발행 A·T(물류 제외)** — 낮음(넓히기 행 1줄).
13. **S/I 원천 = CI(선적 원천 S/I 기각 — 적대 R-10)** — 번복 중간(`ci_id` NULL 허용 + 원천 2종 CHECK + B/L 대조 기준 재정의).
14. **레터헤드 소급 판 금지(R-15)** — 낮음(판 수 검사 1곳). 단 첫 판 등록 후 과거 주소 정정이 필요해지면 오너 판단 필요(현재 탈출로 = 없음, 새 판은 오늘부터).

**남은 판정 후보: 0건**(전건 자율 확정 — 위 2·3·4·11은 오너가 번복하면 해당 ADR을 "대체" 표기로 갱신).

## 6. 리스크·부채

**주요 리스크(완화)**
- **R1 2b 평가 함수 재배치가 S3-1 여신 시험 기대값을 바꾼다**(`code:modules/credit/evaluation.py:165-216`) → 커밋 ①(Protocol 재정의·가짜 7개 재작성 — 적대 R-05·R-06)을 "0 미수 가짜 provider에서 기대값 무변경"으로 단독 green 확인, 기본 provider 단언 기존 시험 전수 목록화(R-35), 3렌즈+2차.
- **R1b provider·해석기 전역 등록 상태가 시험 실행 순서·샤드에 따라 달라진다**(`code:main.py:44` 모듈 수준 `create_app()`·리셋 픽스처) → 세션 autouse 등록·저장/복원 교체·무작위 순서 2회(적대 R-04).
- **R2 5a DocKind 편입 되돌리기 불가** → 커밋 ① 묶음(R-23 선례)·3렌즈+2차·게이트로 운영 행 0 구간 확보.
- **R3 신규 외부 의존성**(`reportlab`·`openpyxl`·폰트·PDF 추출 dev 도구 — `requirements.txt` 현재 없음 실측) → 3b 커밋 ①을 의존성 전용으로(== 고정·**reportlab ≥ 3.6.13 단언**·sha256·OFL·CI 설치 시간 실측), 3b 3렌즈(공급망·**ReportLab 마크업 주입** — 적대 R-03).
- **R4 CI 운영 발행 0에 대한 사용자 기대 불일치** → runbook A-05·5c 게이트 문구·워크스루 출구에 409 확인·WBS v1.7 ①④⑦.
- **R5 S4-2가 채권 선행 CI 재판정을 놓침**(두 경로 공존) → WBS v1.7 S4-2 주석 ⑦·ADR-0088 되돌리기 란·부채 I-01.
- **R6 총수 핀·LOCK_ORDER·REGISTRY를 여러 PR이 순차 수정**(2b·5a 총수, 2a·4a·5a LOCK_ORDER, 2a·5a 채권 발생 함수) → 직렬 병합·재배치 후 전체 재검증, 핀 수치는 통합 §2.5.
- **R7 테스트 시간 증가**(S3-2 종결 로컬 46분 46초, 샤드 timeout 40분) → durations 3회 갱신(1b·2b 후·5a 후)·샤드 32분 초과 시 4샤드·속성 시험 시드 고정+예산 30초.
- **R8 2a~2b 구간 채권이 노출에 미반영** → 과대 방향만(fail-safe)·화면 2c를 2b 뒤로·K 시험 "감소 0".
- **R9 PDF 결정성·폰트 플랫폼 차이로 시험 불안정** → 바이트 비교 대신 텍스트 추출 대사·XLSX 셀 값 대사·폰트 digest 고정.
- **R10 생성물 다운로드 축소가 documents 공용 경로 회귀** → documents 전 행을 매트릭스에 실측 등재(`GOVERNED_PREFIXES`)·일반 파일 무변경 단언·해석기 미등록 시 일반 파일로 취급되지 않게(생성물 판정 실패 = 403, fail-closed) 시험.
- **R11 스캔 후보 확장 질의 비용**(06:40 잡) → 부분 인덱스 확인·실행 시간 PROGRESS 기록(렌즈 7).
- **R12 프로덕션 실데이터 여부 미확인** → PR-1 착수 시 확인, 2b·5a 일정 전제.

**소비·이월 대사**: design-integrated §6.1 — 종결 Q-04(=P-02)·Q-05·Q-06·Q-08(+R-6-2)·R-3a-5·P-01·P-08·P-09·P-10·P-11·P-51·P-39(=PR-16 ⑨)·PR-16 ⑧·R-3a-4(재트리거)·R-6-4·R-8-2·G-09 / 재판정 종결 P-12(409 유지)·P-15(채권 항 충당·SO 항 유지) / 부분 종결 P-13(ORDER_INTAKE·PO 이월)·P-14(P7 이월)·Q-17(NOTICE 완화 — S3-4 이월)·R-3b-4 / 유지 이월 P-56·Q-15·R-3b-8·P-44·R-3b-9·R-4b-4·R-3c-3·R-8-1·R-6-3·P-21·P-22·P-23·P-48 외.
**신규 부채 42건**(design-integrated §6.2 — A-01~A-11·B-01~B-08·C-D1~D6·D-01~D-09·E-01~E-04·I-01·I-02 + 적대 검토 **I-03**[MT700 인테이크 — S6-1, R-01]·**I-04**[수입 대금만기·수입 L/C — S6-2, R-08], 소유·트리거 병기, PR-1에서 PROGRESS 등재). sE E-05는 소멸. **적대 검토 가산 대사**(통합 §9 끝): Q-08 수출 종결·수입 이월 / P-12 역기록 포함 409 유지·S3-1 PR-10a 판정 후보 ① 종결(R-13) / R-4a-8 부분 종결·P-50 documents 몫 종결(R-12) / R-4a-9 3b 발동(R-36) / B-02 트리거 = PI 초과 입금 422 문의 또는 종결 SO 미충당 선수금 알림(R-09·R-29).

## 7. §22 11렌즈 계획 시점 통과 근거 (완료 시 PR별 체크)

| 렌즈 | 근거 |
|---|---|
| 1 기능 | WBS DoD 4항·검증 2항이 PR-2a(aging·검증 A)·PR-2b(공백/이중 0)·PR-5a(교차 거부·무상 생성 — 서비스 층)·PR-3b(무상 QT/PI)에 1:1. 운영 경로 CI 0은 §1 ①로 보고 |
| 2 데이터 | 신규 12표·마이그레이션 8(M16b 분리)·IMMUTABLE 13 → 21·열 GRANT +2·스냅샷 규칙표·채번 CI·SI·금액 복합 FK·downgrade RAISE·시드 = document_types만 |
| 3 트랜잭션 | T1~T22 각 1TX(통합 §2.12), 렌더·파일 IO TX 밖, CI 재발행 1TX, 채권 발생 + COMPLETED 수렴 같은 TX, 외부 호출 0 |
| 4 동시성·멱등 | LOCK_ORDER 3슬롯·PI SHARE 규칙·거래처 잠금·부분 유니크(채권·CI·OPENING·lc)·Idempotency-Key(P-39)·version·실제 동시 실행(GC-F5·F6) |
| 5 보안·권한 | AUTHZ 표·`GOVERNED_PREFIXES` +6·documents 403 순서·생성물 다운로드 축소+audit·계좌번호 마스킹·원가 채널 0·렌더 주입·원격 로드 차단·`allowed_actions` ↔ 매트릭스 대사 |
| 6 시간 | 한 요청 한 '오늘'·`TODAY_IMPORT_POINTS` 커버리지·invoice_on/입금일/제시일 ≤ KST 오늘·aging 경계 8점·L/C 유효기일 당일/익일·서류 날짜 무변환 |
| 7 성능 | 목록 50·CI 상세 쿼리 8·채권 목록 고정 쿼리·aging 상수 쿼리·문서 흐름 6·스캔 후보 질의 시간 기록 |
| 8 테스트 | 그룹 A·B·G·H·I·J·K(`D:439` ①의 E는 해당 없음) + GC v1.6 14건 + 변이 점검(통합 §9 R-39 포함) + 라벨 대사 |
| 9 운영 | 잡 14 유지(대상 확장)·runbook S3-3 운영 개시(OPENING 순서·외부 CI 작성·`lc` 토글)·durations·의존성 설치 |
| 10 문서 | DESIGN [M5] 보강(문면 변경 5건 명시)·ADR 0088~0099·WBS v1.7·GC v1.6 |
| 11 워크스루 | 2c·3c·4b·5c 화면 관통 + PR-7 입구(레터헤드 과거 판·QT 렌더)~출구(채권·COMPLETED·알림 이동·CI 409 게이트) 1회, 3층 증거·390px 9화면 |

## 8. 착수 시 실행 확인 필요 항목 (정적 독해 한계 — 실행 검증 못 했음)

`alembic heads` 단일 / ~~`payments.partner_id` 유무~~ **확인됨(정적 — `code:modules/payments/models.py:55`, 3열 FK 확정, R-23)** / ~~`sales_orders (id, currency)` UNIQUE 유무~~ **확인됨(`code:modules/sales_orders/models.py:160` `uq_sales_orders_id_currency`, R-23)** / SO 상태이력 CHECK 제약명·`reason_not_blank` 위반 기존 행 0(R-12) / DocKind 교차 표 CHECK **0건 확인**(R-26) / provider·`receivables_reflected` 단언 기존 시험 전수(R-35) / `rl_config` 신뢰 스킴·호스트 설정 이름(R-03) / 기일 스캔 문턱 원천(`urgency` — R-01) / `owner_type` CHECK 실명·확폭 잠금 시간 / 식별자 63자 / `reportlab`·`openpyxl` 3.13 휠·폰트 크기 / 2b 커밋 ① 기대값 무변경 / 스캔 `_trade_chain_imports` 무변경 통과 / 당사자 version 불변 단언 기존 시험 유무 / 프로덕션 실데이터 유무 / 샤드 편차 / 검산 CI 자동 편입 — 세부는 design-integrated §8. 첫 해당 PR에서 실측하고 결과를 PROGRESS에 기록한다.

## 9. 적대 검토 반영 기록 (2026-10-05 — 3렌즈, 정적 독해, 실행 검증 못 했음)

> 지적마다 **원문(파일:줄·코드)을 먼저 확인**했다. 실재하면 더 엄격한 쪽으로 고쳤다. 정정 정본은 **design-integrated §9 R-01~R-40**이다. 부록 해당 줄에는 PR-1 첫 커밋에서 "[적대 R-nn]" 표지를 단다(R-40). 번호: `H`·`M`·`L` = spec-fidelity, `S` = safety, `F` = feasibility. **41행 중 반영 39 · 부분 반영(전제 일부 반증) 2 · 전면 기각 0**이다. 렌즈 간 중복 4쌍(S-10 = F-02, S-13 = F-06 = L-6 일부, S-12·F-04 합류, S-07·H-2 ④ 문장 합류)을 빼면 고유 지적은 37건이다.

| 지적 | 요지 | 원문 확인 | 판정 | 조치(통합 §9) |
|---|---|---|---|---|
| H-1 | L/C 부록 부재 — §7.10 L/C 산출물(MT700·체크 항목·tolerance 표시·임박 적색·쓰기 API) 소유 없음 | `sA:7·18`·`sB:34`·`sD:18·563`, `D:225`, 통합 §2.1(f)·§2.9 | **반영** | R-01: N-13 등재, 항목 카탈로그 14종(UCP600 출처 표시·판정 0), LT1~LT4, 화면(서버 `tolerance_bounds`·`urgency`), MT700 = DESIGN 대비 ⑬(S6-1·I-03), 4a K·4b vitest |
| H-2 | `D:227` ④ 선수금 미차감 문면 변경 미보고 | `D:227` ④, sB B8 | **반영(등재 — 문면 복귀는 기각)** | R-02: DESIGN 대비 ⑭, 감소 방향·현금 사실 근거, 문면 복귀 시 노출 영구 잔존 → 운영 정지라 기각, '의도적 예외' 문장 은퇴, ADR-0089·0064 부기, §5-3 |
| M-1 | 수입 대금만기·수입 L/C 무판정·무부채 | `milestone_view.py:386,390-393`, ADR-0081 ②, `purchase_orders/service.py:387` | **반영** | R-08: Q-08 수출 종결·수입 이월, I-04(S6-2), ADR-0094 명시, WBS 대비 ⑫ |
| M-2 | "선수금 초과분" 미구현 미보고 | `W:129`, `payments/service.py:158-169` | **반영** | R-09: WBS 대비 ⑩, B-02 트리거 구체화 |
| M-3 | S/I 폐쇄 근거가 DESIGN이 아님 | `D:187`·`D:444`(CI·PL만), `D:215`, sA §A8 | **반영(분리 등재 — 개방안 기각)** | R-10: ①-a·①-b, 선적 원천 S/I 기각 근거·번복 비용 ADR-0096 |
| M-4 | LINK 스키마 `max_length=13` 누락 | `documents/schemas.py:42` | **반영** | R-11: 길이 상수 결속, 6종 × 2경로 K 12행 |
| M-5 | R-4a-8·P-50 트리거 발동 누락 | `P:117`·`P:1664`, `trade_docs/models.py:79`, `constraints.py:19`, `documents/schemas.py:39` | **반영** | R-12: M17 `reason_not_blank` 재정의·신규 CHECK 전부 `BLANK_CHAR_CLASS`·documents forbid |
| M-6 | 닫힌 PI 역기록 재판정 누락 | `P:1493-1494`, `payment_status.py:61-66` | **부분 반영** | R-13: 409 유지 판정·근거 등재. "미수 영구 틀어짐" 전제는 반증(`expiry_sweep.py:12-14` — 닫힌 PI엔 살아 있는 SO 없음 → 충당 무관). 정정 경로 부재는 B-02 합류 |
| L-1 | `D:439` ① 인용 오기(E 누락) | `D:439` | **반영** | R-32 |
| L-2 | 언어 변형 축소 미보고 | `W:129`·`D:215`, 통합 §2.1(c) | **반영** | R-33: WBS 대비 ⑪ |
| L-3 | ADR 부기에 0068·0055 누락 | ADR-0068 ①, ADR-0055 영향 세션 | **반영** | R-34: 부기 17건 |
| L-4 | 2b 뒤 GC 비고·기존 단언 불일치 | `GC:64`·`GC:75`, `test_confirm_credit_approval.py:121` | **반영** | R-35: GC 비고 부기·단언 전수 목록화 |
| L-5 | R-4a-9 발동 미등재 | `P:118` | **반영** | R-36: 3b tzdata diff 0 |
| L-6 | 확정 가능 항목 '미확인'·X-11 줄 어긋남 | `payments/models.py:55`, `documents/router.py:32` | **반영** | R-23 |
| S-01 | ReportLab `Paragraph` 마크업 주입(로컬 파일·SSRF·CVE-2023-33733) | sA §A12, `sC:538` CK-09, requirements 부재 | **반영** | R-03: `markup_safe` 단일 함수·`Paragraph` 1곳 AST·≥ 3.6.13·신뢰 스킴 비움·CK-09 확장·변이 |
| S-02 | DRAFT→CANCELLED QT 렌더 통과 | `machine.py:158`, `quotations/models.py:4,78-79` | **반영** | R-14: `frozen_at IS NOT NULL` |
| S-03 | 레터헤드 소급 판 허용·등록 비직렬 | sA §A3:97, N-07 | **반영** | R-15: 과거 날짜는 판 0개일 때만·`BACKDATED` 422·advisory 잠금 |
| S-04 | 첫 S/I 동시 2건 | T6 `FOR SHARE`, sA §A8:276 | **반영** | R-16: 첫 S/I 부분 유니크·T6 `FOR UPDATE`·`SI.ALREADY_ISSUED` |
| S-05 | CI 미리보기가 계좌 후보를 L에 노출 | `sD:202`, `bank_accounts/router.py:25` | **반영** | R-17: 후보·요약 A·T만, 목록·CSV 계좌번호 열 0, K |
| S-06 | CERT가 전표 첨부 삭제 가능 | `documents/router.py:32,191-197`, `service.py:752-783` | **반영** | R-18: 전표 소유 삭제 A·T, 404 → 403 → 409 |
| S-07 | 2b 뒤 기본 provider 경로 = 조용한 과소 노출 | `providers.py:1-10`, `evaluation.py:209-226`, `exposure.py:19` | **반영** | R-07: 기본 provider = UNEVALUABLE, 부분 노출 경로 은퇴, 결속 시험 강화 |
| S-08 | 체크 마크 직전 대조 동시 무력 | 통합 §2.1(f)·T17 | **반영** | R-19: 부분 유니크 2·복합 FK |
| S-09 | 렌더 컨텍스트에 내부 메모 차단 없음 | `mixins.py:74-75` | **반영** | R-28: 뷰 허용 필드 고정·G4 확장 |
| S-10 | provider 계약에 SO별 미수 없음 | 통합 §2.4 | **반영** | R-05(= F-02) |
| S-11 | 채권 완납 뒤 선수금이 clamp로 사라짐 | sB B4 ②③ | **부분 반영** | R-29: 전제 반증(미충당 선수금으로 표시 — `sB:129-130`·GB-12·`sD:171·501`). 남는 공백(종결 SO 미충당 선수금 = 실제 초과)만 검산 잡 ADMIN 알림·배지 |
| S-12 | PI·채권 교차 경합으로 이중 입력 검사 우회 | `payment_flow.py:75` 부근 | **반영** | R-20: T13 거래처 잠금 선행·CJ 결정적 판 |
| S-13 | payments 축소 분기 불필요 | `payments/models.py:55` | **반영** | R-23 |
| S-14 | 'GET 부작용 0' 근거 모순 | X-24 ↔ T3' | **반영** | R-30 |
| S-15 | `lc` 플래그 TOCTOU | `platform/service.py:93-105` | **반영** | R-31: 입력 3종 플래그 행 `FOR SHARE` |
| F-01 | provider 전역 등록이 실행 순서·샤드에 의존 | `main.py:44`, import 63파일, `test_credit_evaluation.py:26-30`, `ci.yml:160-174` | **반영** | R-04: 세션 autouse·저장/복원·해석기 같은 함수 등록·깨질 시험 3 가산 |
| F-02 | Protocol로 SO 묶음 1회 환산 불가 | `providers.py:38-41` | **반영** | R-05: `exposure_parts` 재정의, `outstanding` 폐기 |
| F-03 | 가짜 provider 7개 500 | `test_credit_evaluation.py:216·235·261·277`, `test_approval_hardening.py:179·204·223`, `evaluation.py:53-60` | **반영** | R-06: 7개 재작성·목록 등재(기반 클래스 기본 구현안 기각) |
| F-04 | 2a의 PI 경로 409가 화면보다 3 PR 먼저 | 계획 §4, sD PY4 | **반영** | R-20: P-14를 2d로 + PI 패널 최소 확인 블록 동반 |
| F-05 | IMMUTABLE 13 → 21 | `table_policy.py:19-48` | **반영** | R-22: 코드 13 → 21, DESIGN 계수 11 → 19 |
| F-06 | 확정 가능 분기 잔존 | `payments/models.py:55-57`, `sales_orders/models.py:160` | **반영** | R-23 |
| F-07 | PR-2a 과대 | 계획 §4 | **반영** | R-21: PR-2d(M16b) 분리·3렌즈 |
| F-08 | 입구는 2개 | `docker-compose.prod.yml:141`, `cli.py:277,402` | **반영** | R-24 |
| F-09 | `lock_document(read)` 부재 | `locking.py:59-77` | **반영** | R-25: `read=` 키워드 확장 |
| F-10 | DocKind CHECK 전수 대상 비실재 | `numbering/models.py:30-38`, `outbox/models.py:33`, `mixins.py:173` | **반영** | R-26: 삭제·0건 확인 항목 |
| F-11 | XLSX 비결정성 ↔ 해시 멱등 | `documents/service.py:611-648`, sA §A12-④ | **반영** | R-27: 지문 = 본문만, 해시 시험 PDF 한정 |
| F-12 | 인용 실측 오류 4건 | `outbox/models.py` 65줄, `index.css` 30줄, `quantities.py:43-46`, `kst.py:19` | **반영** | R-37 |

**결과 PR 목록(최종, 병합 순서)**: PR-1 → PR-1b → PR-2a → **PR-2d** → PR-2b → PR-2c → PR-3a → PR-3b → PR-3c → PR-4a → PR-4b → PR-5a → PR-5b → PR-5c → PR-6 → PR-7 (**16개**). 마이그레이션 **8건**(M16 = 2a, **M16b = 2d**, M17 = 2b, M18 = 3a, M19 = 3b, M20 = 4a, M21 = 5a, M22 = 5b). ADR **0088~0099**(12건, 번호 변경 없음) + 기존 부기 17건. 에러 코드 신규 **45종**. 3렌즈 대상 = 2b·5a(+2차)·3b·**2d**.
