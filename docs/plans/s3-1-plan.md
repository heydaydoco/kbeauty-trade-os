# S3-1 계획서 — 전표 사슬 전반부·오더 인테이크·승인 코어

> 상태: **자율 확정(오너 지시 2026-09-29 — "PowerShell 없이 클라우드에서 끝까지, 결정·개입 없이")**. 웹 세션 판정 절차는 생략됐고 판정 후보는 전부 권장안으로 확정했다(사후 번복 가능 — 번복 가능성이 높은 항목은 §5에 되돌리기 비용과 함께 모았다). 이 문서는 S3-1 PR-1(문서 전용) 첫 커밋으로 등재된다. 승인 범위 밖 구현 금지.
> 작성일 2026-09-30(Phase 2 종결 직후 — 같은 클라우드 세션).
> 기준 커밋: origin/main `3c0900e82948`(PR #24 병합 — Phase 2 종결). 기준선: pytest 1884 · vitest 199 · 커버리지 게이트 94(CI 6잡).
> 상시 수칙 유지: 자동화 L3 금지 4영역(§15) 비접촉(PO 발행=사람 1클릭 단일 경로·자동 확정/자동 승인 경로 부재) / 마스킹·승인 밖 신규 발견분은 구현 없이 등재만 / **병합은 클라우드 세션이 게이트(head SHA 전 체크런 success + mergeable clean) 확인 후 API로 수행 — 웹 Merge 버튼 금지 유지** / 원천 실데이터 파일 커밋 금지(픽스처는 익명 합성) / 규제·세율은 시스템이 단정하지 않는다(§21) — 준비도 게이트 문구는 판정 워딩 금지.

## 0. 이 계획서의 구성

이 문서는 **결정 요약·범위·PR 분할·검증 배치**를 담는 본문이다. 안건 전문(테이블·컬럼·CHECK·전이표·에러코드·테스트 배분·되돌리기 비용)은 다음 부록이 정본이다 — 구현 세션은 해당 PR이 소비하는 묶음 문서를 먼저 읽는다.

| 부록 | 내용 |
|---|---|
| `docs/plans/s3-1/design-A.md` | 전표 데이터 모델·참조 카디널리티·스냅샷·결제조건·Incoterms·환율·단가·마스터 보강 |
| `docs/plans/s3-1/design-B.md` | 상태머신 전이표·확정/동결·취소/정정·채번·잔량·상태 이력·만료 스윕 |
| `docs/plans/s3-1/design-C.md` | 승인 코어(유형·결재선 매핑·SoD·대결·소비/무효·알림·결재함) |
| `docs/plans/s3-1/design-D.md` | 오더 인테이크·게이트 6종+PI 입금 게이트·확정 시퀀스·오더 보드 |
| `docs/plans/s3-1/design-E.md` | 여신 노출·직렬화·PI 입금(payments)·정책 저장소 |
| `docs/plans/s3-1/design-F.md` | PO 범위·역할/소유권 매트릭스·마스터 정합·이월 소비 |
| `docs/plans/s3-1/design-integrated.md` | **통합 정합 검토(모순 52건·누락 14건 해소)·통합 스키마 24테이블·상수·에러코드 56종·잡·마이그레이션 DAG·PR 분할·ADR 목록·GC v1.4·WBS v1.5** — 묶음 문서와 충돌하면 이 문서가 이긴다 |

## 1. 세션 정의 (WBS S3-1 문면 그대로)

- **범위**: §7.1~7.4, §2 승인 워크플로우, §3 채번·스냅샷 규율
- **산출물**: quotations/proforma_invoices/sales_orders/purchase_orders(+lines·잔량·스냅샷), 상태 열거 구현(§7.2 그대로), 오더 인테이크 스테이징+게이트 6종(품번 매핑·단가 편차·여신·준비도·MOQ·중복 PO), 오더 보드, approvals+결재선 매핑+대결, PI 입금 게이트(선수금 T/T만 활성)
- **DoD**: ① 참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음) ② 중복 바이어 PO 0건 ③ 여신 초과 → 승인 게이트 ④ 확정 후 단가·환율 불변
- **검증**: A(중복 PO·불변·PI 게이트), H(승인 우회 차단·승인 후 불변 — DESIGN §20 H 4항 전부: +결재선 매핑·대결 기간 유효+이력). 아울러 J·K, G(PO 원가 마스킹·파일 해시 멱등), I(자동 확정 부재)를 S3-1 몫으로 매핑 보강한다.
- **범위 밖 경계**: 선적·마일스톤·휴일(S3-2) / 서류 렌더링·채권·L/C 터미널(S3-3) / 판정·비용·백오더(S3-4) / 할당·재고 잔량 ADR·PO 후반(입고)(P4) / 자동 확정 규칙·이메일·채널 입구(S5) / AI 추출(S6).

## 2. 통합 결정 요약 (전건 자율 확정)

| # | 결정 | 근거 요지 |
|---|---|---|
| 1 | **전표 8테이블+bank_accounts**. PI도 라인 보유(§3 표 맵 "(+lines)" 누락은 표기 누락). 전 FK RESTRICT, 라인은 헤더와 (id,currency) 복합 FK | WBS "4종 +lines·잔량·스냅샷" |
| 2 | **참조 카디널리티**: QT→PI 1:N, PI→SO 활성 1:1(부분 유니크), QT→SO 직접 1:N, SO의 qt/pi 참조 nullable(바이어 PO 직접). 소비량은 저장하지 않고 하위 라인 SUM 파생(잔량 S3-1 로컬 결정, S4-1 ADR이 대체 가능) | §7.1·§17.2 |
| 3 | **상태 전이표 명문화**(25/101 등 총수 고정·사람/자동 구분·RESERVED 엣지). PI·PO는 초안 없음(비저장 `preview`로 편집 요구 충족, 생성=발행). QT 수주전환 = 그 QT에서 파생된 SO가 처음 확정되는 트랜잭션 | §7.2 문면 그대로 |
| 4 | **동결**: `frozen_at` 1회 세팅(QT 발행/PI 생성/SO 확정/PO 발행), `FIELD_POLICY` 단일 출처, 강제=서비스 409+아키텍처 테스트(DB 트리거·해시 미채택), assignee_id·status·note는 동결 제외. 정정 = 취소+신규(정정 전표 미채택) | ADR-0033·0038 선례 |
| 5 | **결제조건 4열**(payment_type TT_ADVANCE/TT_DEFERRED/LC·advance_pct_bp·balance_anchor 6종·balance_days), **Incoterms 3열**(12코드·연도·장소), 전 전표 헤더에 복사. L/C 선택은 feature_flags 'lc' 켜짐 시에만(fail-closed) | S3-2/S3-3 재입력 방지(ADR-05) |
| 6 | **환율은 헤더 스냅샷**(NUMERIC(18,8), 1통화=x KRW, 수동 입력)·`fx_rates` 마스터 미신설(재판정 트리거 등재). 통화 불일치 비교는 `comparable_amount` — None=비교 불가=통과 아님 | 소비자 없는 마스터 금지 |
| 7 | **채번**: 접두어 QT/PI/SO/PO·최초 저장 트랜잭션에서 발급·**연도=KST 발급 시각**(numbering 1줄 수정)·전표 doc_number 전역 UNIQUE | §17.3 |
| 8 | **승인 코어**: 유형은 코드 고정(S3-1 소비=SO_CREDIT_EXCEEDED, 나머지 예약)·상태 6/전이 7·1회 소비·대상 변경 시 무효(digest)·기안≠승인(**ADMIN도 예외 없음**)·대결(KST 날짜 양끝 포함·중첩 금지·이력 IMMUTABLE)·알림은 notify() 직접(역할 보유자→ADMIN 폴백)+결재함 화면 | §2·§20 H |
| 9 | **오더 인테이크는 별도 order_intakes(+lines)**(import_staging은 마스터 왕복 전용 유지). 2단 흐름: 인테이크 확정→SO(접수)→게이트 재평가·승인·입금 게이트→SO(확정). 입구는 수동+CSV 표준 양식(.xlsx 미수용), AI/이메일/채널은 자리만. 자동 확정 경로 없음(아키텍처 테스트로 고정) | ADR-09 |
| 10 | **게이트 7종**(6종+PI 입금) 결과 PASS/WARN/BLOCK/UNKNOWN×해소 3, 평가 불능은 통과와 분리(fail-closed), 확정 시점 잠금 하 재평가, override는 별도 액션·사유 필수·IMMUTABLE 증적 | §7.4 |
| 11 | **여신**: 노출=미결 SO 잔액(+신규)·거래처 행 `FOR NO KEY UPDATE` 직렬화·lock_timeout→409 `COMMON.CONCURRENCY.LOCK_BUSY`·NULL=관리 안 함·0=신용불가·strict 초과. 미수(S3-3)는 provider로 두고 '미수 미반영' 배지 | §7.10 |
| 12 | **PI 입금 게이트**=금액 대조(입금액 ≥ split_advance)·활성=TT_ADVANCE만·모드 OFF/WARN/BLOCK(정책 미설정=BLOCK). 입금 기록은 `payments`(S3-1 최소형, S3-3이 채권 연결로 확장) | §7.3 |
| 13 | **정책 저장소 policy_settings**(폐쇄 키·값=데이터·ADMIN 사유+audit) | ADR-11 |
| 14 | **PO**: SKU 전용 라인·공급사 SUPPLIER/OEM·원가 열명 `unit_cost/line_cost/total_cost`(기존 마스킹 자동 편입)·VIEWER 필드 부재 스키마·생성=발행(사람 1클릭)·PO 후반(입고)은 P4로 이월 등재 | ADR-0018·0024 |
| 15 | **역할·소유권**: 전표는 회사 공유 자산(§20 K "타 사용자 403"="역할 밖 403"), 쓰기=ADMIN+TRADE, PO 주체=TRADE | F 묶음 |
| 16 | **마스터 정합**: 거래처 유형 해제 시 활성 전표 참조 확인(409)·partners name_en/address_en·skus.moq·여신한도 변경 ADMIN·품번 삭제·forbid 래칫 | 이월 소비 |
| 17 | 잡 7→12행(document-expiry-sweep·trade-docs-totals-verify·approval-stagnation-scan·idempotency-purge·session-purge), 4금 논증: 만료·검산·독촉은 '기록만' | ADR-0058 |
| 18 | 모듈 DAG(L0 trade_docs 커널·L1 전표별·L2 trade_chain·gates·credit·approvals·order_intake·order_board) 단방향, 전역 잠금 순서 `LOCK_ORDER` | ADR-0059 |

## 3. 신규·변경 스키마 (정본: design-integrated §2)

**신규 24테이블**: bank_accounts / quotations·quotation_lines·quotation_status_log / proforma_invoices·proforma_invoice_lines·proforma_invoice_status_log / sales_orders·sales_order_lines·sales_order_status_log / purchase_orders·purchase_order_lines·purchase_order_status_log / approval_lines·approvals·approval_events·delegations / payments / gate_evaluations·gate_overrides / policy_settings / order_intakes·order_intake_lines / board_saved_filters.
**기존 변경**: partners(+name_en·address_en) · skus(+moq) · sales_orders 증적 3열(확정 배선 ALTER) · numbering 서비스 연도 기준(코드).
**IMMUTABLE 확장 8**: 상태이력 4 · approval_events · gate_evaluations · gate_overrides · payments. **마이그레이션 12건**(M01~M12, design-integrated §2.11). 마이그레이션 시드 0(함정 ⑩ — 결재선·정책·잡은 앱 경로).

## 4. PR 분할 (16 PR 수직 슬라이스 — 각 병합이 동작 가능, 직렬 병합)

| PR | 범위 | 마이그 | DoD·검증 배치 |
|---|---|---|---|
| **PR-1** | 문서 전용: 이 계획서+부록·DESIGN [M4] 보강·ADR 0051~0067(+기존 부기)·WBS v1.5·GC v1.4·runbook·PROGRESS | — | 문서 린트 |
| **PR-2** | 플랫폼 기반: LOCK_BUSY 핸들러·채번 KST·parse_minor_amount·column-grant 수단·redaction 접미·holders_of_role/users lookup·is_feature_enabled·테스트 프레임워크(forbid 래칫·user FK 분류·AUTHZ 골격·임포트 DAG·factories) | — | K·J |
| **PR-3** | 마스터 보강(partners·skus)·CSV 왕복·유형 해제 차단·품번 삭제·prices_at·readiness.cells_for·SearchSelect | M01 | A·F·K·G |
| **PR-4** | 정책 저장소 policy_settings+/settings/policies | M02 | A·H·J·K |
| **PR-5** | 커널+QT(+프런트·타임라인)·검산 잡 | M03 | A·J·K |
| **PR-6** | PI+은행계좌+QT→PI 참조 생성+만료 스윕 | M04 | A·J·H |
| **PR-7** | SO 접수+QT/PI→SO 참조 생성+취소/보류+문서 흐름 | M05 | **DoD①**·**②(SO 유니크)**·A·J |
| **PR-8** | PO(preview·생성=발행·OC·취소·CostHidden·CSV) | M06 | G·A·I |
| **PR-9** | 승인 코어+여신 평가+결재함/결재선/대결 화면 | M07 | H·J·K |
| **PR-10** | payments(입금·역기록·PI 상태 수렴) | M08 | A·J |
| **PR-11** | 게이트 코어+7 평가기+override | M09 | A·H·K |
| **PR-12** | **확정 배선**(confirm_sales_order·승인 요청/소비/무효·AllocationPort·QT 수렴) | M10 | **DoD③④**·H·A·J·I |
| **PR-13** | 인테이크(MANUAL)+SO 접수 착지 | M11 | **DoD②**·A·J·G·I |
| **PR-14** | CSV 입구(9열 양식·파일 해시 멱등) | — | F·G·J |
| **PR-15** | 오더 보드·벌크 3종·저장 필터 | M12 | H(동시 20명)·K |
| **PR-16** | 마감: 청소 잡 2·runbook·워크스루(렌즈 11) 입구~출구 관통·PROGRESS 종결 | — | 워크스루 |

PR-5가 최대 규모이면 5a(커널·QT 백엔드·잡)/5b(QT 프런트)로 나눈다. PR-8(PO)은 PR-5 이후 어느 위치에도 끼울 수 있고 PR-10은 PR-9와 순서를 바꿀 수 있다(PR-11은 9·10 이후, PR-12는 11 이후).
**PR별 공통 절차**: 작은 커밋 → 마이그레이션 왕복·`alembic check`·전체 pytest·vitest·ruff·mypy·typecheck·build → 변이 점검 → 실기동 관통(렌즈 11) → 자기 적대 검증(2렌즈, 검증자는 테스트 실행 금지) → PR·CI 6잡 green·mergeable clean → squash 병합 → 브랜치를 최신 main에서 재시작.

## 5. 오너 판정 후보 — 번복 가능성이 높은 자율 확정 (되돌리기 비용)

1. **1인 ADMIN은 자기 기안을 승인할 수 없다**(예외 없음, 제2 결재 계정 필요) — 낮음~중간(가장 번복 가능성 높음, §20 H 약화라 오너 명시 판정 필요)
2. 전표는 회사 공유 자산(§20 K 해석) — 낮음~중간
3. 정책 미설정=BLOCK(운영 개시 전 ADMIN이 정책 설정) — 낮음
4. L/C 선택은 S3-1 프로덕션에서 닫힘(플래그 행 공급 경로 없음) — 낮음
5. 환율 수동 입력·fx_rates 미신설 — 중간(방향·정밀도는 지금 확정)
6. 이월 미수 미반영+선수금 미차감(배지로 fail-visible) — 낮음
7. 여신한도 변경=ADMIN 전용 — 낮음
8. QT 수주전환=SO 확정 시점 — 낮음
9. PO 초안 없음·생성=발행 — 중간~높음
10. CSV 파일 전체 원자·.xlsx 미수용 — 낮음
11. override 권한(가격·MOQ=TRADE·ADMIN, 준비도·PI=ADMIN) — 낮음
12. 정정 전표 미채택(취소+신규) — 높음(마찰이 실측되기 전엔 불필요)

## 6. 부채·관찰·소비한 이월 항목

design-integrated §4.6이 전건(트리거·소유 병기)을 정본으로 한다. 소비 종결: 여신한도 마스킹 재판정(유지·트리거=역할 세분화)·거래처 유형 해제 갭(PR-3 구현)·쓰기 스키마 forbid 전역 래칫(PR-2)·전표 담당자 handover 등록·ADR-0013/0014 청소 잡(PR-16)·runbook 전표 소급 양식(PR-1). 재이월: facilities 마스터(트리거 재설정)·OEM 프로파일 슬롯(po_kind)·fx_rates 소유 세션(WBS v1.5 등재)·PO 후반(P4)·프런트 eslint 부채 #2(프런트 PR마다 재확인 한 줄).

## 7. §22 11렌즈 계획 시점 통과 근거 (완료 시 PR별 체크)

| 렌즈 | 근거 |
|---|---|
| 1 기능 | WBS 산출물·DoD 4항이 PR-7/12/13에 1:1 배치(§4) |
| 2 데이터 | 스냅샷·동결·채번·soft delete·IMMUTABLE 8·table_policy 분류 |
| 3 트랜잭션 | 확정=헤더+라인+이력+승인 소비+할당 포트+이벤트 단일 UoW, 외부 호출 0(알림=outbox/notify) |
| 4 동시성·멱등 | 잠금 순서·여신 행 잠금·(partner, PO키) 부분 유니크·Idempotency-Key·version 409 |
| 5 보안·권한 | AUTHZ_MATRIX·SoD·PO 필드 부재 스키마·extra=forbid·IDOR |
| 6 시간 | KST 채번 연도·대결 KST 날짜 경계·유효기간 당일 24:00 |
| 7 성능 | 목록 50·N+1 상수·보드 질의 수 테스트·인덱스 |
| 8 테스트 | 그룹 A·G·H·I·J·K+GC v1.4 14건+변이 점검 |
| 9 운영 | 잡 12행·미설정 fail-visible 배지·runbook 운영 개시 절 |
| 10 문서 | DESIGN [M4] 보강·ADR 0051~0067·WBS v1.5·GC v1.4 |
| 11 워크스루 | PR-7/12/16 실 HTTP+실 브라우저 관통 |

## 8. 착수 시 실행 확인 필요 항목 (정적 독해 한계 — 실행 검증 못 했음)

복합 FK·부분 유니크의 alembic check 드리프트 0 / 식별자 63자 / VersionMixin의 flush당 1회 증가 / FOR NO KEY UPDATE·FOR KEY SHARE 비충돌 / CHECK NULL 처리 / psycopg sqlstate(55P03·40P01) / column-grant와 왕복 마이그레이션 상호작용 / ErrorCode 카탈로그 완전성 — 세부는 design-integrated §4.9. 첫 해당 PR에서 실측하고 결과를 PROGRESS에 기록한다.
