# S3-2 계획서 — 선적·기일 엔진·휴일

> 상태: **자율 확정(오너 지시 2026-09-29 — "PowerShell 없이 클라우드에서 끝까지, 결정·개입 없이")**. 웹 세션 판정 절차는 생략했고, 판정 후보는 전부 **더 엄격한(fail-closed) 권장안**으로 확정했다(사후 번복 가능 — 번복 가능성·비용이 큰 항목은 §5). 이 문서는 S3-2 PR-1(문서 전용) 첫 커밋으로 등재된다. 승인 범위 밖 구현 금지.
> 작성일 2026-10-04(S3-1 종결 직후 — 클라우드 계획 세션). **적대 검토 3렌즈(spec-fidelity·safety·feasibility) 반영 완료(2026-10-04) — 기록은 §9, 정정 정본은 design-integrated §9.**
> 기준 커밋: main `a4d91c0`(S3-1 종결). 기준선: pytest **5031 passed · 34 skipped** · vitest **1171** · 커버리지 게이트 94(CI 3샤드+병합, ADR-0073).
> 상시 수칙 유지: 자동화 L3 금지 4영역(§15) 비접촉(선적 생성·초안 = 사람 1클릭, SO 자동 엣지는 이행 진행 수렴뿐, 대외 발송 0, stock_movements 무접촉) / 규제·세율·HS는 시스템이 단정하지 않는다(통관 = 사실 기록) / **병합은 클라우드 세션이 게이트(head SHA 전 체크런 success[`ci-ok` 포함] + mergeable clean) 확인 후 API squash — 웹 Merge 버튼 금지 유지** / 원천 실데이터 커밋 금지 / 완료 보고는 릴레이 본문에 DDL 전문·12자리 해시 직접 포함.
> **실행 검증 못 했음** — 이 계획은 정적 독해(파일 열람·grep)로 작성했다. 코드 줄 번호는 `a4d91c0` 기준이고 각 PR 첫 커밋에서 실측한다(P-60 선례).

## 0. 이 계획서의 구성

이 문서는 **결정 요약·범위·PR 분할·검증 배치**를 담는 본문이다. 안건 전문(테이블·컬럼·CHECK·전이표·에러코드·테스트·되돌리기 비용)은 다음 부록이 정본이다 — 구현 세션은 해당 PR이 소비하는 부록을 먼저 읽되, **통합 §9(적대 검토 정정) → 통합 §0~§8 → 부록 A~E** 순으로 우선한다.

| 부록 | 내용 |
|---|---|
| `docs/plans/s3-2/design-A.md` | 선적 데이터 모델: shipments·lines·parties·status_log·customs_records, 커널 편입, 상태 머신, 사슬 등록(CHILD_LINKS·LINE_CONSUMERS), 잔량·배정 가능량, 여신 노출 무접촉 |
| `docs/plans/s3-2/design-B.md` | 기일 엔진·마일스톤(**§7.5 9종 → 11종**: 저장 8[+BL_ISSUED]·파생 3[+PRESENTATION_DEADLINE 편입])·자동 계산 순수 함수·롤오버·통보·holidays·휴일 경고·스캔·OEM·PO ETA·QT/PI D-N·GC 경계 케이스 31행(+통합 §9 추가 행) |
| `docs/plans/s3-2/design-C.md` | 트랜잭션 경계 T1~T12(+T13)·LOCK_ORDER·소비 잠금·version·멱등·역할 매트릭스·마스킹·감사·이벤트·잡·L3 4금·H/J/I/K 테스트 |
| `docs/plans/s3-2/design-D.md` | API 계약(엔드포인트 S1~S20·M1~M9·H1~H5)·응답 스키마·화면·셸·보드·DG·§8.3 자리·시간 표시·한국어 UI·PR-16 부채 ③⑤⑦ |
| `docs/plans/s3-2/design-E.md` | PR 분할·마이그레이션 DAG·ADR 배정·WBS v1.6·GC v1.5·검증 배치·변이 점검·적대 검토 |
| `docs/plans/s3-2/design-integrated.md` | **통합 정합 검토(모순·중복 30건 + 누락 7건 해소)·통합 스키마 11테이블·상수·에러코드·잡 14행·AUTHZ·LOCK_ORDER·마이그레이션 DAG·ADR 0074~0087·DESIGN 부기·WBS v1.6·GC v1.5·부채 대사 + §9 적대 검토 정정(R-01~R-30)** — 부록과 충돌하면 이 문서가 이기고, 이 문서 안에서는 §9가 이긴다 |

## 1. 세션 정의 (WBS S3-2 문면 그대로 — `WBS.md:112-116`)

- **범위**: §7.5, holidays, §8.3 산식 선적용(자리만)
- **산출물**: shipments(+lines/parties·환율 고정·구분 4종)/milestones, 자동 계산(대금만기 결제유형 분기·적재의무 수리일+30·L/C 제시기한 MIN(B/L+21, 유효)), 실적 입력 후속 재계산, 롤오버 이력+통보 기록, 국가별 휴일 경고; **(v1.5 추가 — 인계 판정)** 수입선적의 PO 참조(`po_line_id`)·`customs_records`(PO 후반 전이와 별개), 이에 따른 `CHILD_LINKS`·`LINE_CONSUMERS`·RESERVED 상태 엣지(SO IN_SHIPMENT·COMPLETED) 추가와 상태 총수 테스트 갱신, `open_order_amount`(여신 노출)의 선적분 차감은 **S3-3 미수 provider `reflected=True` 등록 릴리스와 같은 PR에서만**, SO 부분출하 후 잔량 종결(short-close) 판정, OEM 마일스톤 프로파일(`profile_id`)·PO 라인 ETA 슬롯 판정, QT/PI 만료 임박(D-N) 알림 판정(기일 엔진 착수 시)
- **DoD**: T/T와 L/C 만기 계산 분기 테스트 / ETA 현지 연휴 → 경고 / 부분선적 1:N 잔량 정확
- **검증**: A(부분선적 잔량 0·초과 거부), K(L/C 제시기한 MIN·tolerance)
- **매핑 보강(이 계획 — 그룹 이름은 `D:419-429` 정의 그대로 — 검토 인용 `D:421-429`는 실측상 419부터)**: §20 P3 해당 그룹(A·B·E·G·H·I·K, `D:417` ①) + J.
  - **A 전표·정합**: 부분선적 잔량 0·초과 거부·역순 취소(후속 생존 시 선행 취소 차단)·실적 기록 선적 취소 차단.
  - **B 서류**: §20 B 마지막 항목 "검수 미완료 선적의 CI·PL 생성 차단"(`D:420`)의 **전제** = 선적 RESERVED 5상태 진입 0(차단 본체는 S3-3·S4-2).
  - **E 비용·소싱**: **해당 없음**(비용 코어 = S3-4, `W:124-128`).
  - **G AI·보안**: DESIGN 이름 그대로 쓰되 내용은 S3-1 부기(`D:417` ① "G=파일 해시 멱등·PO 원가 마스킹")를 승계 — 수입선적 원가 비복사·응답/CSV 원가 키 0.
  - **H 운영**: 기일 알림 dedup·에스컬레이션·담당 이관.
  - **I 자동화·통합**: 자동 엣지 4금 논증·잡 2종 실패 감지.
  - **J 안전 계약**: 실제 동시 부분선적·멱등·롤백·IMMUTABLE 3표 권한 거부.
  - **K 보안·품질**: L/C 제시기한 MIN·tolerance·총수·authz·**아키텍처 단언 "stock_movements 무접촉"**(선적·마일스톤 모듈의 원장 임포트·쓰기 0).
  - **GC v1.5 10건**(A14~A21·F4·G3 — 통합 §9 R-12) — 현행 GC v1.4는 S3-2 배정 0건(`W:209-225`).
- **문면과 다르게 확정한 8건(멈춰서 보고 → DESIGN 부기·ADR·WBS v1.6 주석으로 해소)**
  - **WBS 대비 4건**: ① "RESERVED 엣지 **COMPLETED** 추가" 미이행 — 노출 술어가 COMPLETED를 제외(`code:modules/credit/exposure.py:19`)하므로 S3-3 provider PR로 이관(ADR-0076) ② DoD "L/C 분기"·검증 K는 **순수 함수 단위·K 테스트로 충족**, 운영 경로는 UNKNOWN(L/C 입력 원천 `lc_terms`는 S3-3, 프로덕션 L/C 닫힘 P-10 — ADR-0081) ③ OEM `profile_id`는 **판정 결과 미신설**(ADR-0085) ④ 구분 4종 중 **채널입고·샘플무상은 값만 싣고 생성 경로 미개방**(ADR-0074).
  - **DESIGN 대비 4건**: ⑤ §8.3 [M4] 부기 ②(`D:229`) "선적 소비 = 상위 헤더 `FOR SHARE`" ↔ **SO는 `FOR UPDATE` 선점**(수렴 동반 — SHARE→UPDATE 승격 교착 차단, ADR-0078) — 해석이 아니라 **문면 변경**이다 ⑥ §15 [M4] 부기 ②(`D:324`) "SO 자동 엣지 0" ↔ **SO CONFIRMED↔IN_SHIPMENT 자동 수렴 2엣지**(ADR-0075) ⑦ §7.5(`D:203`) 마일스톤 9종 ↔ **11종 — BL_ISSUED(저장) 추가 + L/C 제시기한(문면상 "자동 계산" 항목)을 파생 마일스톤 종류로 편입**(ADR-0080) ⑧ §2(`D:37`) 역할 서술(물류 업무 미배정) ↔ **LOGISTICS 첫 전표 쓰기**(ADR-0079).
- **범위 밖 경계**: 선적 캘린더 뷰(S4-4 — `D:397`, `W:152`) / 피킹·검수·출고 원장·할당·검수 미완료 CI/PL 차단(S4-2) / PO 후반·입고(S4-1) / CI·PL·S-I·lc_terms·receivables·선적분 노출 차감·L/C 플래그 공급(S3-3) / DG 임시 체크리스트(S3-4)·DG 게이트(S4-4) / B/L draft 대조·통관 이슈 킷(S5-3) / 바이어 추적 링크(P7) / **출발국 휴일 경고(ETD·Cargo Closing·서류마감) — 문면 밖이라 부채(통합 §9 R-09)**.

## 2. 통합 결정 요약 (전건 자율 확정)

| # | 결정 | 근거 요지 |
|---|---|---|
| 1 | **선적을 전표 커널에 편입**(DocKind `SHIPMENT`, 접두어 `SH`, 수출·수입 한 kind + `shipment_kind` 4값). 헤더 원천 FK는 `so_id`(수출)·`po_id`(수입) **정확히 하나**(CHECK), 채널입고·샘플무상 행은 DB가 거부. 다중 SO 합적 없음 | 단일 전이 통로·이력 IMMUTABLE·총수 핀·역순 취소 승계(`D:181`, `D:356`), ADR-0054 |
| 2 | **스냅샷 복사**: 통화·고정 환율·결제조건 4열·Incoterms 3열·거래 상대·SKU 품명 = 원천에서 ORIGIN 복사, 생성 본문에 해당 필드 없음(`extra="forbid"`). 수입선적은 **단가·금액 미복사**(NULL/0 — PO 원가 9채널 봉쇄). **`doc_date`는 복사가 아니라 생성 시 `today_kst()` 1회 설정, 이후 불변(ORIGIN)** | `D:175` ①, S3-1 결정 #5, ADR-0024, 통합 §9 R-15 |
| 3 | **상태 8값·활성 3엣지**(계획→출고지시[전용 `release-order`]·계획/출고지시→취소[사유]), 피킹·검수완료·출고·선적·종결 = RESERVED(S4-2). ETD·B/L 실적은 상태와 독립된 마일스톤 데이터 — **단 ETD·BL_ISSUED·ETA 실적은 RELEASE_ORDERED에서만 받고(422), 실적이 살아 있는 선적은 취소 409 `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`** | §8.4 "검수 통과 후에만", 출고 = 원장 시점(`D:231`), §20 A "후속 생존 시 선행 취소 차단", 통합 §9 R-01 |
| 4 | **SO CONFIRMED↔IN_SHIPMENT 자동 수렴 2엣지**(같은 TX, 불변식 IN_SHIPMENT ⇔ 살아 있는 선적 ≥1). IN_SHIPMENT의 보류·취소 엣지 없음(선적 선취소). **SO 취소는 후속 생존 검사를 상태 검사보다 먼저 — IN_SHIPMENT SO 취소 = 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`+`detail.successors=[SH-…]`**. §15 "SO 자동 엣지 0" 부기 개정 | 이행 진행 ≠ 약정 진입(4금 논증), QT CONVERTED 수렴 선례, `code:modules/trade_docs/transition.py:136-146` 원칙, 통합 §9 R-02 |
| 5 | **SO COMPLETED·short-close 미개방**(RESERVED 유지), `credit/exposure.py` 무변경, "COMPLETED ∈ RESERVED while default provider" 아키텍처 테스트 | 노출 공백 방지(P-01, ADR-0064, S3-3 DoD `W:121`) |
| 6 | **잔량**: SO_LINE에 선적 FULFILL 소비자, PO_LINE에 **IN_TRANSIT** 소비자 + `open_quantity` **kind 필터**(PO 잔량은 입고 시만 감소), 수입 초과 기준 = 배정 가능량. `CONSUMABLE_STATUSES[SO]`에 IN_SHIPMENT 추가 | `D:173`, `D:229` ② 시그니처 1개, `quantities.py:133` 결함 선제 차단 |
| 7 | **CHILD_LINKS** SO→선적·PO→선적(역순 취소), 통관 기록 생존 시 선적 취소 409(**PR-4a** — 표가 M15로 이동), 실적 기록 생존 시 선적 취소 409(PR-4a) | `D:29`, `D:419`, 통합 §9 R-01·R-16 |
| 8 | **LOCK_ORDER** `… PO → shipments → shipment_children → approvals → lines → seq`. 모드: **T1 SO `FOR UPDATE` 선점**(SHARE 흡수·승격 금지) / **T2 PO `FOR SHARE`** / **선적 기점 경로(T4·T5)는 `lock_chain` 그대로 SO·PO 모두 `FOR UPDATE`** / T8·T9는 partners `FOR KEY SHARE`를 shipments 앞에 | `D:344` ②, 40P01 구조적 차단, 통합 §9 R-08 |
| 9 | **마일스톤 11종**(§7.5 9종 → 11종: 서류마감·Cargo Closing·PSI·신고수리·ETD·**B/L 발행일[추가]**·ETA·수입 세금 납부기한 = 저장 8, 적재기한·대금만기·**L/C 제시기한[편입]** = 파생 비저장 3), 계획/실적 이중값 행, 날짜형(현지 DATE)/시각형(UTC+IANA tz), 덮어쓰기 금지 | `D:203` 산식이 B/L일 요구, FREE 4열 핀 회피, 통합 §9 R-03 |
| 10 | **수리일 = 통관 기록 `accepted_on` 유일 원천**, 마일스톤 비복사·읽기 시 `MIN` 파생, 통관은 1:N 사실 기록(세율·HS 열 0). **미수리 기록이 1건↑이면 신고수리 행 `customs_state=PARTIAL` + "일부 미수리 n건" 배지**(적재기한 산식은 MIN 유지). 신고일·수리일 ≤ `today_kst()`(422) | `D:313`, 통합 §9 R-06·R-18 |
| 11 | **자동 계산 = 순수 함수**(`trade_docs/schedule.py` — 화면·스캔이 같은 함수): 대금만기 6앵커 매핑(INVOICE_DATE·앵커 미확정 = UNKNOWN, 대체 금지, **수입 ORDER_DATE = PO `frozen_at`의 KST 날짜**), L/C 만기·제시기한 MIN·tolerance(좁은 쪽 반올림, **수출·수입 LC 공통**) — **운영 L/C = UNKNOWN**, 적재의무 = 수리일+30, **적재 이행일 = ETD·BL_ISSUED 실적 중 존재하는 값의 MAX(가정 — ADR-0080)** | `D:146` ③, GC-A13, 통합 §9 R-10·R-11·R-29 |
| 12 | **holidays**: 국가 ISO alpha-2(**markets 비FK**), 연도 선언 단위 근거 링크+확인일 필수, **연도·국가 정합을 DB 복합 FK·CHECK로 강제**, `PUT` 원자 교체·CSV 미리보기, ADMIN 전용 쓰기, 시드 0. **경고만**(자동 순연 0), 미선언 = **UNVERIFIED**(≠ 평일), **적용 = ETA(도착국)만 — `D:203` 문면 그대로**(출발국 확장은 부채) | `D:27`, `D:203`, `D:192` ②, 통합 §9 R-09·R-24 |
| 13 | **롤오버 이력 `milestone_changes`·통보 `milestone_change_notices` IMMUTABLE**, 통보 = comm_logs `SHIPMENT` 주제(선적 전용 통로만). **범용 `/comm-logs`는 쓰기(POST 스키마 422)·읽기(목록 기본 제외·id 접근 404)·documents `COMM_LOG` 첨부(SHIPMENT 주제 거부) 모두 차단**, 발송 코드 0. M2·M3 응답 = `{board, change}` | `D:203`, `D:132` ③④, `D:356`, 통합 §9 R-05·R-19 |
| 14 | **잡 12→14**: `approval-integrity-check` daily@05:40(**PR-1b로 앞당김**, 12→13 — 불일치는 `approval_id`·문제별 dedup 알림, 잡 FAILED는 실행 예외만) + `trade-deadline-scan` daily@06:40(PR-6, 13→14 — 선적 4종 + QT/PI D-N, 기본 D-7/3/1, dedup `deadline:shipments:{id}:{TYPE}/…@기일`, **시각형 도과 = `now_utc > effective_at`**). 대금만기·제시기한 알림 제외(충족 신호 S3-3) | `D:144`, `D:320-324`, `P:461`, 통합 §9 R-17·R-20 |
| 15 | **권한**: 조회 전 역할 / 생성·라인·취소 = A·T / 헤더·출고지시·당사자·통관·마일스톤·통보 = A·T·**L**(물류 첫 전표 쓰기) / OEM 마일스톤 = A·T / 휴일 = A / **마일스톤 세트 = A 전용**(`D:37`은 인증 역할 편집을 "시장·요건 템플릿"으로 한정) | `D:37`, `D:370`, 좁은 쪽, 통합 §9 R-14 |
| 16 | **인계 판정**: short-close·PO 라인 ETA 열·OEM `profile_id`·facilities = 미신설(계산값·트리거 등재), QT/PI D-N = 구현, 마일스톤 세트 `item_profile_milestone_types` + 쓰기 경로 = 구현(부채 #15 종결) | `W:114`, P-02~P-07·P-57 |
| 17 | **§8.3 "자리"** = 선적 라인 응답 `availability.status=NOT_IMPLEMENTED` + "가용재고 미산정" 배지(포트 무변경, 0·현재고 표시 금지) | `W:113`, `D:229` ③ |
| 18 | **화면**: 선적 목록·상세(세로 마일스톤 카드 타임라인)·SO/PO 상세 2단 생성 대화상자(독립 생성 화면 없음)·휴일 캘린더·사용자·역할(ADMIN)·보드 "선적중" 5번째 열·**SO 목록 상태 필터 "선적중"**·문서 흐름 SHIPMENT 노드·DG 배지(차단 0)·현지 시각 병기(시각형 카드에 `scan_date`·`local_date` 구분 표기) | `D:303` ⑦⑧⑭, 통합 §9 R-21·R-25 |
| 19 | **PR-16 부채**: ③ 역할 화면 = S3-2(PR-7, 백엔드 0 — **병합 순서를 3a 앞으로**) / ⑤ SearchSelect 오선택 = S3-2 수정(PR-3b) / ⑥ shard durations = PR-2a / ⑦ 브라우저 e2e 도구 = **미채택**, 렌즈 11 3층 증거(실 HTTP e2e·소스 계약 vitest·리포 밖 실브라우저 1회) | `P:32`, 통합 §9 R-22 |

## 3. 범위·비범위

**신규 11테이블**(정본: design-integrated §2.1 + §9): shipments · shipment_lines · shipment_parties · shipment_status_log · customs_records / milestones · milestone_changes · milestone_change_notices · item_profile_milestone_types / holiday_calendar_years · holidays.
**기존 변경**: `comm_logs` 주제 CHECK += SHIPMENT(M15). SO·PO·credit 스키마 **무변경**. 코드 레지스트리 변경(DocKind·machine·quantities·chain·locking·policy·board·handover·scheduler 등)은 design-integrated §2.4. **`trade_chain/lifecycle.py` `_cancel_sales_order` 검사 순서 변경**(통합 §9 R-02).
**IMMUTABLE 확장 3**: shipment_status_log · milestone_changes · milestone_change_notices. **마이그레이션 3건**(M13 휴일 → M14 선적 4표 → M15 마일스톤 계열 4표 + **customs_records** + comm_logs CHECK, `down_revision` 사슬 `f2cb6020b2bb`→M13→M14→M15). 마이그레이션 시드 0(함정 ⑩).
**에러 코드 신규 32종**(SHIPMENTS 26·HOLIDAYS 6 — 통합 §9 R-26) — 커널 기존 코드 재사용 우선(`TRADE_DOCS.DOCUMENT.FROZEN`·`EXCEEDS_OPEN`·`SUCCESSOR_ALIVE` 등).

**비범위(이 세션에서 만들지 않는 것 — 명시)**
- SO COMPLETED 엣지·short-close·`open_order_amount` 선적분 차감(S3-3 provider PR) / `lc_terms`·L/C 플래그 공급 경로·tolerance 화면(S3-3) / CI·PL·서류 버튼·선적 문서 첨부(S3-3·S6-1)
- 선적 피킹·검수·출고·선적·종결 엣지, 출고 원장, 가용재고 산식(S4-2) / PO 잔량 차감·입고(S4-1) / 선적 캘린더 뷰(S4-4)
- 채널입고·샘플무상 생성, 다중 SO 합적, 중량·CBM·박스 열, 비거래처 수하인
- 휴일 자동 순연·주말 판정·외부 자동 수집·**출발국 휴일 경고**, 대금만기·제시기한 알림, OEM 마일스톤 알림, 브리핑 수신자 확장
- 계정 생성 화면·API, 브라우저 e2e 의존성·CI 잡, facilities·프로파일 마스터·PO 라인 ETA 열·`fx_rates`

## 4. PR 분할 (15 PR 수직 슬라이스 — 각 병합이 동작 가능, 직렬 병합)

> 적대 검토 반영으로 **PR-1b·PR-3c·PR-4c 신설**, **PR-7 병합 순서를 3a 앞으로** 옮겼다. 라벨은 부록 참조 안정성을 위해 유지한다(병합 순서는 아래 "의존" 줄이 정본).

| PR | 범위 | 마이그 | DoD·검증 배치 | GC |
|---|---|---|---|---|
| **PR-1** | 문서 전용: 이 계획서+부록 A~E+통합 등재 · DESIGN [M5] 보강 부기(통합 §4) · ADR 0074~0087 + 기존 ADR 부기 13건 · WBS v1.6 · GC v1.5(**등재 전 그룹 배치 확정 — A14~A21·F4·G3**) · PROGRESS '## 현재'·자율 확정 표기·부채 등재 · runbook 초안 줄("PR-6 병합 시 활성" 주석) | — | 문서 린트(ADR 5줄·WBS 행·GC 이력) / 기존 전체 테스트 green | 등재 |
| **PR-1b** | **승인 무결성 대사 잡** `approval-integrity-check` daily@05:40(PR-9a 부채 ① — 트리거 "S3-2 이전" 도과분 우선 소비)·CLI 수동 실행·JOB **12→13**·4금 집합. **첫 커밋에서 기존 DB 전건 대사 실측("불일치 0" 기록)**. 불일치 = `approval_id`·문제별 dedup 관리자 알림, 잡 FAILED = 실행 예외만 | — | H(불일치 → 알림 1회·재실행 재발송 0)·I(스케줄 13·예외 → FAILED)·J(읽기 전용 — 승인 행 무수정) | — |
| **PR-2a** | 산식 순수 함수(`trade_docs/schedule.py`·`holidays/calc.py`) + holidays 백엔드(연도 선언·원자 교체 PUT·CSV 미리보기·목록·export, **연도·국가 복합 FK·연도 CHECK**) + `open_quantity` kind 필터 선행 + `.shard_durations.json` 갱신 | **M13** | **DoD①**(T/T·L/C 분기)·**검증 K**(제시기한 MIN·tolerance)·A(산식)·J(휴일 교체 멱등·1TX)·K(휴일 A 전용·Page·CSV BOM)·순수성 아키텍처 테스트 | A16·A17·A18·A19(함수 층) |
| **PR-2b** | 휴일 캘린더 화면·CSV 업로드 2단·`toZonedPairDisplay`·셸 "휴일 캘린더" | — | vitest(UNVERIFIED ≠ 빈 목록·날짜 문자열 `new Date` 금지 소스 계약)·390px | — |
| **PR-7** | 사용자·역할 화면 `/settings/users`(ADMIN, 백엔드 0) — **병합 순서 2b 다음**(3b·4b의 L 역할 실기동 관통에서 API 직접 호출 0) | — | vitest(ADMIN 외 미노출·마지막 관리자 오류)·K(기존 authz 무변경) | — |
| **PR-3a** | **수출선적 커널**: DocKind·상태 8값·SO 자동 수렴·**SO 취소 검사 순서(후속 생존 먼저)**·COMPLETED 보류·CHILD_LINKS·LINE_CONSUMERS(SO FULFILL·PO IN_TRANSIT 등록)·CONSUMABLE·LOCK_ORDER·SO 참조 생성(preview/생성)·라인·당사자·출고지시·취소·이관 등록·authz(L 첫 쓰기)·보드 상수·가용 자리 필드·에러 코드·no_auto_confirm·계층·`known_s3` | **M14** | **DoD③**(부분선적 1:N)·**검증 A**(잔량 0·초과·역순 취소 — SO 취소 기대 코드 `SUCCESSOR_ALIVE`)·B(RESERVED 진입 0 = §20 B CI·PL 차단 전제)·I(자동 엣지 2·COMPLETED 0)·J(실제 동시 7+7·더블클릭·롤백·version·55P03)·K(총수 182·사슬 대사·FIELD_POLICY·authz·Page·보드 완전성·stock_movements 무접촉) | A14·F4 |
| **PR-3c** | **선적 읽기 확장(백엔드, 마이그 0)**: `GET /shipments/export.csv`(S20)·문서 흐름 SHIPMENT 노드(수출, 5쿼리 이내) | — | G(CSV 원가 열 0·BOM·수식 이스케이프)·K(authz 전 역할·쿼리 수 상한) | — |
| **PR-3b** | 선적 목록·상세(헤더·라인·당사자·상태이력·문서 흐름·DG·가용 미산정)·SO 상세 "선적 만들기" 2단·보드 "선적중" 열·**SO 목록 상태 필터 IN_SHIPMENT**·SO 선적 잔량·alert-routes·상태 라벨·**SearchSelect 오선택 수정**·셸 "선적" | — | vitest(보드 5열·노드·409 칸별 잔량·빠른 입력 오선택 0·SO 필터 "선적중")·`detail-layout` 하한 7·e2e A | — |
| **PR-4a** | **마일스톤·롤오버·통보·통관**: 계획/실적·파생 배선·**실적 가드(ETD·BL·ETA 실적은 RELEASE_ORDERED에서만, 실적 생존 시 취소 409)**·`milestone_changes`·통보(SHIPMENT 전용 통로, 범용 쓰기·읽기·첨부 차단)·M2·M3 응답 `{board, change}`·계획 초안 1클릭·휴일 경고 배선(ETA)·**`customs_records` 표+통관 기록 API**(수리일 유일 원천·파생·PARTIAL·미래일 422)·**통관 생존 취소 가드** | **M15** | **DoD②**(ETA 현지 연휴 경고 — API 층)·A(재계산·덮어쓰기 금지 2중·실적 생존 취소 409)·H(통보 = 기록, 발송 0)·J(롤오버 멱등·IMMUTABLE·재유입=신규·같은 키 같은 `change.id`)·K(부모-자식 404·Page·범용 `/comm-logs` SHIPMENT 쓰기 422·목록 제외·id 404·첨부 거부) | A18·A19(배선 층)·A20 |
| **PR-4c** | **OEM 생산 마일스톤(T13, M7~M9)·마일스톤 세트 쓰기 경로**(`/item-profiles/{id}/milestone-types`, A 전용, `GOVERNED_PREFIXES` 등재) — 표는 M15에 이미 있음 | — | A(OEM 4종 ⇔ PO 소유)·J(T13 잠금 순서)·K(authz·GOVERNED·중복 409·비적용 종류 422) | — |
| **PR-4b** | 마일스톤 타임라인·실적·롤오버(사유)·통보·통관 대화상자·OEM 생산 일정 섹션·품목군 "마일스톤 세트" 섹션 | — | vitest(파생 행 버튼 0·CUSTOMS_CLEARED 실적 안내·**부분 수리 배지**·사유 빈칸 제출 불가·UNKNOWN 사유 한글·**ETA 도착국 휴일 → 경고 배지+휴일 이름 / 미선언 → "휴일 캘린더 미등록" 배지 / CLEAR → 배지 0**·시각형 `scan_date`/`local_date` 구분 문구) | — |
| **PR-5a** | **수입선적**: PO 참조 생성·배정 가능량 409·원가 비복사·PO 상세 `assignable_quantity`·`expected_receipt`(가장 늦은 ETA 계산값)·PO 대역 테스트를 실 테이블로 교체 | — | A(PO `open_quantity`·상태 불변)·J(동시 수입 2건)·G(원가 열람 역할로도 응답 원가 키 0) | A15·G3 |
| **PR-5b** | PO 상세 "수입선적 만들기"·라인 입고예정 표시 | — | vitest(금액 칸 0)·e2e A | — |
| **PR-6** | **기일 스캔 잡** `trade-deadline-scan`(06:40)·CLI 수동 실행·JOB **13→14**·보호 테스트 갱신(4금 집합·총수·`_trade_chain_imports`) | — | H(ack 재발송 0·D-3 에스컬레이션·롤오버 새 기일 새 알림·이관 즉시 반영·ADMIN 폴백·L/C 오프 알림 0·시각형 도과 = `now_utc > effective_at`)·I(스케줄 14·실패 1건 → FAILED) | A21 |
| **PR-8** | 마감: runbook(운영 개시 — 휴일 연도 선언·물류 계정[CLI 생성 → 화면 역할 부여 → ADMIN 회수]·잡 표 14행·수기 양식 선적 행·DG 경고·통관 이슈 임시 규칙)·**워크스루(렌즈 11)**·`docs/testing.md` 대사·`golden` 마커 대사(≥53)·WBS/GC 확정·PROGRESS 종결·부채 최종 목록 | — | 워크스루 3층 증거·11렌즈 | 전건 대사 |

**의존(병합 순서 정본)**: 1 → 1b → 2a → 2b → 7 → 3a → 3c → 3b → 4a → 4c → 4b → 5a → 5b → 6 → 8. 고정 사슬은 마이그레이션(2a→3a→4a), 5a·6은 4a 이후(ETA·마일스톤 행), 3b는 3c 이후(문서 흐름 노드 소비), 4b는 3b·4c 이후(OEM·품목군 섹션 소비). b PR은 짝 a 병합 후 최신 main에서 시작.
**같은 커밋 묶음(분리 금지)**: 선적 DocKind·상수 dict 11종·**선적 상태 기계(사람 엣지 3 포함)**·M14 모델·FIELD_POLICY·사슬 레지스트리(**한 커밋 — 모델 CHECK가 `DOC_PREFIXES`·`STATUSES`를 읽고, 엣지 0이면 도달성 테스트가 깨진다**) / SO 자동 엣지 2 ↔ RESERVED에서 IN_SHIPMENT 제거 ↔ 보드 매핑 1줄(**SO Literal은 무변경** — `public_transition_targets`는 사람 엣지만 셈하므로 기존 assert가 그대로 통과함을 확인; 신설은 `ShipmentTarget=Literal["CANCELLED"]`+assert뿐) / 체인 FK 생성 ↔ CHILD_LINKS·LINE_CONSUMERS·허용목록 / 잡 추가 ↔ 4금 집합·총수 핀.
**PR별 공통 절차**: 작은 커밋 → 마이그레이션 왕복·`alembic check`·전체 pytest·vitest·ruff·mypy·typecheck·build → 변이 점검(design-E §E6-3 최소 목록 + 통합 §9 R-30 추가분, 전원 kill·error 0, junitxml 판정) → 실기동 관통(렌즈 11) → 자기 적대 검증(2렌즈 — **3a·4a는 3렌즈+2차**, 검증자는 테스트 실행 금지) → PR·CI 전 체크런(`ci-ok` 포함) green·mergeable clean → API squash 병합 → 브랜치를 최신 main에서 재시작. PR-1은 계획 세션 이어쓰기(착수 블록 A항 세션 확인 가드 — 부록 A~E·통합 맥락이 없는 세션이면 작업 없이 정지).

## 5. 되돌리기 비용이 높은 항목 — 번복 가능성과 함께 (자율 확정)

1. **선적 커널 편입(DocKind 저장값)** — 운영 데이터 전 중간 / 후 **높음**(분리 불가에 가깝다). → 3a·4a는 운영 실데이터 투입 전 병합 권장(PR-1 착수 시 프로덕션 실데이터 유무 확인).
2. **헤더 원천 FK 정확히 하나(다중 SO 합적 불가)** — 열면 **높음**(링크 표 이전·chain 판정 재작성). 지금 조이는 쪽을 택했다.
3. **SO COMPLETED 미개방(WBS 문면 미이행)** — 번복 비용 낮음(S3-3이 엣지 1개 가산), **번복 가능성 가장 높음**(S3-3 provider PR에서 반드시 연다). 반대로 지금 열면 과소 노출 소급 불가로 높음.
4. **IMMUTABLE 3표 형태(롤오버 이력·통보·선적 상태이력)** — 중간~높음(권한 해제 마이그레이션 후 데이터 이전).
5. **LOGISTICS 첫 쓰기 범위**(동작별) — 넓히기 낮음 / 좁히기 중간. **오너 판정 권장 1순위**(물류 역할의 업무 배정이 처음 문면화됨 — §2 부기).
6. **`approval-integrity-check` 잡 배선(PR-9a 부채 ①, PR-1b)** — 번복 낮음(`enabled=false`), 오너=영준(보안 판정) 항목을 자율 확정한 것이라 **오너 확인 권장 2순위**. 불일치 알림 형식(문제별 1회 dedup, FAILED = 예외만)을 바꾸는 것은 낮음(키 형식은 신규 알림부터).
7. **SO 자동 수렴 2엣지(§15 "SO 자동 엣지 0" 문면 개정, 결정 #4)** — 번복 중간(엣지 제거 시 IN_SHIPMENT 행을 CONFIRMED로 되돌리는 데이터 정정 마이그레이션 1건 + 보드 열·SO 취소 안내 원복), DESIGN 문면 변경이라 **오너 확인 권장 3순위**.
8. **수리일 = 통관 기록 유일 원천(비복사)** — 낮음(조립 함수 1개, 복사형 전환 시 백필 1회).
9. **IN_TRANSIT 소비 kind·kind 필터** — 중간(S4-1 입고 FULFILL 계약과 함께 재판정).
10. **LOCK_ORDER 개정** — 중간(교착 재검증, 계측 테스트가 비용을 줄인다).
11. **L/C 검증을 순수 함수로 충족(WBS DoD 해석)** — 낮음(S3-3 배선 가산), 문면 해석이라 **오너 확인 권장 4순위**.
12. **휴일 = 경고만·markets 비FK·ETA 한정** — 경고 범위 낮음(출발국 확장은 함수·화면만) / FK 전환 중간 / 자동 순연 도입 중간(재계산 공지).
13. **실적 기록 선적 취소 차단(`ACTUAL_RECORDED`)** — 낮음(가드 제거). 오입력 실적은 사유와 함께 정정·삭제 후 취소하는 탈출로가 있다.
14. **브라우저 e2e 도구 미채택** — 낮음(나중에 ADR+의존성 가산).

**남은 판정 후보: 0건**(전건 자율 확정 — 위 5·6·7·11은 오너가 번복하면 해당 ADR을 "대체" 표기로 갱신).

## 6. 리스크·부채

**주요 리스크(완화)**
- **R1 PR-3a 면적 최대**(커널 편입·같은 커밋 묶음·레지스트리 10여 곳 갱신) → 면적 축소(export.csv·문서 흐름 → 3c, 통관 표·가드 → 4a), 커밋 순서 고정(design-integrated §9 R-23: ① M14+DocKind·dict 11종·선적 상태 기계[사람 엣지 3]·FIELD_POLICY·사슬 레지스트리 한 커밋 → ② SO 자동 엣지 2+RESERVED 축소+보드 매핑 → ③ SO 취소 검사 순서 → ④ 서비스·라우터·authz → ⑤ 동시성), 각 커밋 전체 pytest, 레지스트리 공회전 변이 검출, 3렌즈+2차 적대 검토.
- **R2 SO 자동 수렴 정합**(선적만 있고 SO CONFIRMED 거짓 상태) → 같은 TX·정의 1개·불변식 통합 테스트·J-05 동시 실행.
- **R3 잔량 동시성**(SHARE→UPDATE 승격 교착) → SO `FOR UPDATE` 선점, GC-F4 실제 동시 실행(잠금 제거 변이에서 실패 확인).
- **R4 정의 이원화**(화면·스캔의 기일·휴일 판정) → 순수 함수 단일 출처, 스캔 후보 = 만료 스윕 후보(변이 고정).
- **R5 기존 계약 테스트 대량 갱신에 의한 조용한 약화** → "갱신 후에도 새 항목을 실제로 검사하는가"를 변이로 확인(`known_s3`·`GOVERNED_PREFIXES`·CHILD_LINKS 행 제거 시 실패). **4a 갱신 목록에 `test_collaboration_constraints.py:151-156`(둘로 분할: DB는 SHIPMENT 허용 / 범용 API는 거부)·`e2e/test_collaboration.py:505-517`(스키마 422 그대로 유지) 포함**.
- **R6 CI 시간**(신규 동시성 파일 편중) → durations 갱신(2a·3a·4a 후), 파일 10분 초과 시 분할.
- **R7 프로덕션 실데이터 여부 미확인** → PR-1 착수 시 확인, 되돌리기 판단 전제.

**소비·이월 대사**: design-integrated §6.1 — 종결 P-03·P-04·PR-16 ③⑤⑥·PR-9a ①·부채 #15 마일스톤 몫·관찰 [S1-2], 판정 후 재트리거 P-02·P-05·P-06·P-57·PR-16 ⑦, 유지 이월 P-01·P-07(S4 몫)·P-10·P-13·P-21·P-22·P-39 외.
**신규 부채 19건**(design-integrated §6.2 + §9 R-09 — 소유·트리거 병기, PR-1에서 PROGRESS 등재): 다중 SO 합적·원천 라인 복합 FK·채널입고/샘플 경로·COMPLETED/short-close·중량/CBM·TO ORDER 수하인·주말 판정·대금만기/제시기한 알림·OEM 알림·납기 대비 비교·브리핑 수신자·계정 생성 API·수입선적 문서 흐름 노드·보드 카드 선적 요약·durations 재갱신·동시성 파일 분할·DG 수동 점검 공백·통관 이슈 임시 규칙(runbook만)·**출발국 휴일 경고(ETD·Cargo Closing·서류마감)**.

## 7. §22 11렌즈 계획 시점 통과 근거 (완료 시 PR별 체크)

| 렌즈 | 근거 |
|---|---|
| 1 기능 | WBS DoD 3항·검증 2항이 PR-2a(DoD①·K)·PR-3a(DoD③·A)·PR-4a(DoD② API)·PR-4b(DoD② 화면 경고)에 1:1 |
| 2 데이터 | 스냅샷·채번 `SH`·soft delete·IMMUTABLE 3·table_policy 11표·마이그레이션 3·시드 0·휴일 연도·국가 DB 강제 |
| 3 트랜잭션 | T1~T13 각 1TX(design-C §C1 + 통합 N-07), SO 수렴 동TX, 외부 호출 0(알림 = outbox/notify) |
| 4 동시성·멱등 | LOCK_ORDER(T8·T9 partners 선행 포함)·SO FOR UPDATE 선점·부분 유니크 7종·Idempotency-Key·version 409·실제 동시 실행 |
| 5 보안·권한 | AUTHZ 행(L 첫 쓰기·마일스톤 세트 A)·원가 비복사·부모-자식 404·forbid·범용 comm_logs 쓰기·읽기·첨부 차단·`GOVERNED_PREFIXES` 누락 0 |
| 6 시간 | KST 오늘·현지 DATE·시각형 D-N = min(현지, KST)·**시각형 도과 = UTC 시각 비교**·실적/신고 미래일 거부·윤년·D-N 경계·현지 병기 |
| 7 성능 | 목록 50·상세 쿼리 수 상한·문서 흐름 5회·N+1 0 |
| 8 테스트 | 그룹 A·B·G·H·I·J·K(E 해당 없음) + GC v1.5 10건 + 변이 점검 |
| 9 운영 | 잡 14행·UNVERIFIED fail-visible·무결성 알림 dedup·runbook 운영 개시 절 |
| 10 문서 | DESIGN [M5] 보강(문면 변경 4건 명시)·ADR 0074~0087·WBS v1.6·GC v1.5 |
| 11 워크스루 | PR-3b·4b 화면 관통 + PR-8 입구(휴일 선언·역할 부여·SO 확정)~출구(알림 이동) 1회, 3층 증거 |

## 8. 착수 시 실행 확인 필요 항목 (정적 독해 한계 — 실행 검증 못 했음)

`alembic heads` 단일 / SO 상태·이력 CHECK 재생성 불요 / `lock_lines_for_consumption` 헤더 소속 필터 / 식별자 63자 / `comm_logs` CHECK 실명·downgrade 실패·범용 경로 거부 / dedup 키 신형식과 에스컬레이션 prefix·담당 이관 키 재작성 호환 / `check_integrity` 전건 순회 시간·**기존 DB 불일치 0 실측(PR-1b 첫 커밋)** / 샤드 편중 / 프로덕션 실데이터 유무 / **SO Literal assert 무변경 통과(3a)** — 세부는 design-integrated §8. 첫 해당 PR에서 실측하고 결과를 PROGRESS에 기록한다.

## 9. 적대 검토 반영 기록 (2026-10-04 — 3렌즈, 정적 독해, 실행 검증 못 했음)

> 지적마다 **원문(파일:줄·코드)을 먼저 확인**했고, 실재하면 더 엄격한 쪽으로 고쳤다. 정정 정본은 **design-integrated §9 R-01~R-30**이고, 부록의 해당 줄에는 "[적대 R-nn]" 표지를 붙였다. 번호: `SF`=spec-fidelity, `SA`=safety, `FE`=feasibility. **44행 전건 반영 / 반려 0건**(렌즈 간 중복 4행 — SA-참고·FE-1·FE-9·FE-12 — 을 빼면 고유 지적 40건. 일부는 방식을 바꿔 반영 — "부분 변형" 표기).

| 지적 | 요지 | 원문 확인 | 판정 | 조치(통합 §9) |
|---|---|---|---|---|
| SF-1 | ETD·B/L 실적 입력 선적의 취소·라인 수정 가능(fail-open) | 취소 가드 = 통관만(통합 §2.6), sA:336 실적 독립 | **반영** | R-01: 실적 생존 취소 409 `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`(ETD·BL_ISSUED·ETA), 실적은 RELEASE_ORDERED에서만 422 `SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE`(라인 수정은 출고지시 이후 `FROZEN`이 이미 막음) |
| SF-2 | 마일스톤 "10종" 계수 오류·제시기한 편입 보고 누락 | `D:203` 9종, 계획 :44 11개 나열 | **반영** | R-03: "9→11종(저장 8·파생 3)", 보고 항목·ADR-0080 제목 정정 |
| SF-3 | DoD① L/C 만기 분기가 GC에서 추적 안 됨 | B20 행 09·10이 C12 범위 | **반영** | R-12: 행 09·10을 A16(구 C11)으로 이동, A17은 11~13·27·28 |
| SF-4 | §20 그룹 이름 오기(E·B·G) | `D:419-429`(B=`:420`·E=`:423`·G=`:425`) | **반영** | §1 매핑·PR 표·sE E6-1 정정(E 해당 없음, 원장 무접촉 → K) |
| SF-5 | §1 "문면과 다르게" 목록 불완전 | 통합 §0-8 7항 vs 계획 4건 | **반영** | §1 8건(WBS 4+DESIGN 4), §5-7 SO 자동 엣지 번복 비용·오너 3순위 |
| SF-6 | 부분 수리인데 신고수리 "완료"로 보임 | X-02 MIN이 NULL 무시 | **반영** | R-06: `customs_state=PARTIAL`+"일부 미수리 n건" 배지, 산식 MIN 유지, A18·4b vitest |
| SF-7 | 잡 총수 13 잔존 | sB:482,622,670 / sC:324,336,339,450,490 / sE:101,192,226,305,362 | **반영** | 부록 표지 + R-17(13은 PR-1b 직후 중간값, 최종 14) |
| SF-8 | 철회 코드·잠금 모드 잔존 | sD:71 / sC T4·T5 / sE:90,114 | **반영** | R-08·R-27 표지 |
| SF-9 | 휴일 경고 대상 문면 이상 확장 | `D:203` "ETA 현지 연휴 경고"만 | **반영(문면 쪽)** | R-09: ETA(도착국)만, 출발국 확장은 부채 19번 |
| SF-10 | 적재 이행 신호(ETD 실적) 근거 없음 | `D:203` 원천 미정 | **반영(부분 변형)** | R-10: 이행일 = ETD·BL_ISSUED 실적 중 존재값의 MAX(가정, ADR-0080에 근거·대안·번복 비용) |
| SF-11 | 제시기한 "수출+LC" 한정 근거 없음 | `D:203` 구분 없음 | **반영** | R-11: 수출·수입 LC 공통(운영 경로는 어차피 UNKNOWN) |
| SF-12 | GC 그룹 배치 어긋남(C=인증·규제) | GC v1 `:91`·`:174` | **반영** | R-12: C11~C16 → **A16~A21**, A15 원가 단언 → **G3** 분리(10건), PR-1 등재 전 확정 |
| SF-13 | DoD② 화면 층 미검증 | 계획 :80 | **반영** | PR-4b vitest 3행 추가 |
| SF-14 | 마일스톤 세트 쓰기 C 배정 근거 약함 | `D:37` 인증 = 시장·요건 템플릿 | **반영** | R-14: A 전용 |
| SF-15 | `doc_date` 의미 분열 | sA:83 vs 통합 §2.1(a) | **반영** | R-15: 생성 시 `today_kst()` 1회, 원천 복사 아님, ORIGIN |
| SA-1 | PR-3a 커밋 순서대로면 커밋마다 green 불가 | `mixins.py:178-181`, `test_doc_machines.py:73-87` | **반영** | R-23: DocKind·상태 기계(사람 엣지 3)·M14 모델 한 커밋 |
| SA-2 | IN_SHIPMENT SO 취소 = `NOT_ALLOWED`(GC는 `SUCCESSOR_ALIVE`) | `lifecycle.py:342-359` | **반영** | R-02: 후속 생존 검사를 상태 검사 앞으로, A14·J-04 기대 코드 명시, ON_HOLD 요청은 `NOT_ALLOWED` 유지 |
| SA-3 | comm_logs 주제 고정 테스트 누락 | `test_collaboration_constraints.py:151-156`, `e2e/test_collaboration.py:505-517` | **반영** | R-05: 4a 갱신 목록, 범용 `SubjectType` = `{CERTIFICATION}` 유지 |
| SA-4 | T8·T9 partners 잠금이 LOCK_ORDER 위반 가능 | sC:52-53 | **반영** | R-08: partners `FOR KEY SHARE`(id 순)를 shipments 앞, J-07 계측 대상 |
| SA-5 | 범용 comm_logs 읽기·첨부가 SHIPMENT 행 노출 | `collaboration/service.py:681-711`, `documents/service.py:278,344` | **반영** | R-05: 목록 기본 제외·id 404·첨부 거부 + K 1행 |
| SA-6 | 철회 문면 부록 잔존 | sE:148,163,400 / sD:72,509 / sC:60,217 | **반영** | R-27 표지 |
| SA-7 | "SO 엣지 ↔ Literal 같은 커밋" 전제 오류 | `machine.py:195-207` | **반영** | R-23: Literal 무변경·assert 통과 확인, 변이 대상 제외 |
| SA-8 | PO 잠금 모드 문면 3중 불일치 | `chain_ops.py:58-63` | **반영** | R-08: T2만 PO `FOR SHARE`, T4·T5는 `lock_chain` `FOR UPDATE` |
| SA-9 | `export_priced` bigint 곱 | `mixins.py:272-285` | **반영** | R-04: `quantity::numeric * unit_price_amount = line_amount`, 수출 라인 `free_iff_zero_price` 승계 |
| SA-10 | `/item-profiles/…/milestone-types` authz 완전성 밖 | `authz_matrix.py:25-44` | **반영** | R-14: `GOVERNED_PREFIXES` 등재(PR-4c) |
| SA-11 | 무결성 잡 ack 수단 없음·알림 피로 | `integrity.py:1-7`, `scheduler.py:139-148` | **반영(부분 변형)** | R-17: 첫 커밋 전건 실측, `approval-integrity:{approval_id}:{problem}` dedup(일자 제외 — 문제별 1회, 미해소 알림은 받은편지함에 잔존), FAILED = 실행 예외만, §5-6 비용 재기재 |
| SA-12 | 휴일 국가·연도 정합 DB 미강제 | 통합 §2.1(k) | **반영** | R-24: UNIQUE(id,country_code,year)+`holidays.year`+복합 FK+연도 CHECK |
| SA-13 | 프런트 SO 상태 필터에 IN_SHIPMENT 없음 | `sales-orders.tsx:17` | **반영** | R-21: PR-3b 필터+vitest |
| SA-참고 | `doc_date` 모호 | = SF-15 | **반영** | R-15 |
| FE-1 | (high) SO 취소 코드 불일치 | = SA-2 | **반영** | R-02 |
| FE-2 | 롤오버+통보 응답에 `change_id` 없음 | sD §D3·D6:283 | **반영** | R-19: M2·M3 응답 `{board, change:{id,change_kind}|null}`, 같은 키 같은 `change.id` J 테스트 |
| FE-3 | 시각형 기한 전 "도과" 표시(최대 ~16h) | sB §B3 ④ | **반영** | R-20: 도과 = `now_utc > effective_at`, D-N 문턱은 scan_date 유지, A21 경계 행 |
| FE-4 | `accepted_on` 미래일 검증 없음 | 통합 §2.1(e) | **반영** | R-18: 신고·수리일 ≤ `today_kst()`(여유 0), 422 `SHIPMENTS.CUSTOMS.DATE_IN_FUTURE`, 4a 변이 |
| FE-5 | `customs_records`가 쓰기 경로 없이 한 PR 구간 존재 | sE:47 기각 사유, X-02로 근거 소멸 | **반영** | R-16: 표를 M15로, `CUSTOMS_RECORD_ALIVE` 가드 PR-4a |
| FE-6 | PR-3a·4a 과대 | 계획 §4 | **반영** | R-22: PR-3c(export.csv·문서 흐름)·PR-4c(OEM·마일스톤 세트 쓰기) 분리 |
| FE-7 | 무결성 잡 배치가 트리거보다 늦음 | `P:461` "S3-2 이전" | **반영** | R-17: PR-1b, JOB 12→13→14 |
| FE-8 | 통합에 진 부록 문면 무표시 잔존 | sB·sC·sD·sE 다수 | **반영** | R-27 표지 + 부록 머리 색인 |
| FE-9 | Literal 묶음 비실재 | = SA-7 | **반영** | R-23 |
| FE-10 | 계정 생성 경로 서술 모순 | sE E3-7 vs sD:445 | **반영** | R-22: "CLI 생성 → 화면 역할 부여 → ADMIN 회수", PR-7을 3a 앞으로 |
| FE-11 | 시각형 실적 미래 검증 규칙 없음 | sB §B8 ④ 날짜형만 | **반영** | R-18: `actual_at ≤ now_utc`(여유 0), A20 경계 행 |
| FE-12 | `doc_date` 모호 | = SF-15 | **반영** | R-15 |
| FE-13 | 시각형 카드에 두 날짜 혼재 | sB §B3 ④·§B12 ③ | **반영** | R-25: `scan_date`·`local_date` 필드·문구 구분 |
| FE-14 | 오류 코드 누락·미결정 | 통합 §2.6·N-03 | **반영** | R-26: 세트 중복 409 `MILESTONE.DUPLICATE_TYPE` 재사용·비적용 422 `TYPE_NOT_APPLICABLE` 재사용·`CUSTOMS.ACCEPT_BEFORE_DECLARE` 신설·N-03 POST 422 고정(id 접근은 R-05 404) |
| FE-15 | 수입 ORDER_DATE 원천 모호 | sB:160 | **반영** | R-29: PO `frozen_at`(발행 시각)의 KST 날짜, A16에 수입 행 |

**결과 PR 목록(최종, 병합 순서)**: PR-1 → PR-1b → PR-2a → PR-2b → PR-7 → PR-3a → PR-3c → PR-3b → PR-4a → PR-4c → PR-4b → PR-5a → PR-5b → PR-6 → PR-8 (**15개**).
