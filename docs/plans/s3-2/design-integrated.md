# S3-2 계획 설계 — 통합 검토서 (부록 A~E 통합)

- 기준: main `a4d91c0`(S3-1 종결). 기준선: pytest **5031 passed · 34 skipped**(5065 수집) · vitest **1171**. 사양 정본은 DESIGN.md, 일정은 WBS.md S3-2 행(`W:112-116`), 진행·부채는 PROGRESS.md('S3-1 PR-16 / S3-1 종결' 절 `P:32`, P-01~P-60 `P:559-619`, '## 현재' `P:632-633`)다.
- 표기: `D:줄`=DESIGN.md, `W:줄`=WBS.md, `P:줄`=PROGRESS.md, `code:경로:줄`=`backend/app/` 아래, `test:경로:줄`=`backend/tests/` 아래, `sA`~`sE`=같은 디렉터리 `design-A.md`(선적 데이터 모델)·`design-B.md`(기일 엔진·마일스톤·휴일)·`design-C.md`(동시성·권한·감사·잡)·`design-D.md`(화면·API)·`design-E.md`(PR 분할·마이그레이션·검증 배치).
- **우선순위**: **§9(적대 검토 정정, 2026-10-04)가 이 문서의 다른 절보다 우선**한다. 부록 A~E와 이 문서가 충돌하면 **이 문서가 이긴다**(S3-1 선례 `docs/plans/s3-1-plan.md:20`). 부록은 모순 해소에 필요한 최소 수정만 했고(§1.6 목록), 각 부록 머리에 이 우선순위를 한 줄 적었다.
- 판정: 오너 지시(2026-09-29)에 따라 판정 후보는 전부 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. 사후 번복 비용은 각 해소 항목과 §3 ADR, 계획서 §5에 적었다. PROGRESS 등재 시 "자율 확정"으로 표기한다(ADR-0011 부기).
- **실행 검증 못 했음.** 정적 독해만 했다(파일 열람·grep·sed). pytest·vitest·alembic·서버는 돌리지 않았다. 코드 줄 번호는 `a4d91c0` 기준이며 각 PR 첫 커밋에서 실측 기록한다(P-60 선례).

---

## 0. 결론 요약

1. **해소 37건** — 부록 간 모순·중복 **30건**(X-01~X-30) + 어느 부록도 소유하지 않은 누락 **7건**(N-01~N-07). 진짜 설계 충돌은 4덩어리다: ① 수리일의 원천(통관 기록 vs 마일스톤 실적 — X-02) ② 쓰기 역할의 폭(A·T·L 전부 vs 동작별 — X-14·X-15) ③ 결제조건 사본 여부(X-01) ④ 기일 알림 dedup 키의 대상(마일스톤 행 vs 소유 전표 — X-25).
2. **신규 테이블 11개**(선적 5·마일스톤 계열 4·휴일 2), **기존 테이블 변경 1건**(`comm_logs` 주제 CHECK에 `SHIPMENT`), **IMMUTABLE 확장 3표**(`shipment_status_log`·`milestone_changes`·`milestone_change_notices`), **마이그레이션 3건**(M13 휴일 → M14 선적 → M15 마일스톤), 시드 0.
3. **상태 총수** 25/101/126 → **30/152/182**(SHIPMENT 8상태·활성 3엣지·RESERVED 5, SO 자동 수렴 2엣지, SO COMPLETED는 RESERVED 유지).
4. **신규 에러 코드 28종**(SHIPMENTS 22·HOLIDAYS 6), 부록에서 제안됐다가 철회한 코드 5종(중복·도달 불가). **[적대 R-26] 최종 32종(SHIPMENTS 26·HOLIDAYS 6).**
5. **JOB_REGISTRY 12 → 14**: `trade-deadline-scan`(daily@06:40 — 선적 마일스톤 + QT/PI 만료 임박) + `approval-integrity-check`(daily@05:40 — PR-9a 신규 부채 ① 소비, N-02). **[적대 R-17] 무결성 잡은 PR-1b(12→13), 스캔은 PR-6(13→14).**
6. **ADR 0074~0087(14건)** + 기존 ADR 부기 13건. **WBS v1.6·GC v1.5 필요**(~~GC 9건: A14·A15·F4·C11~C16~~ **[적대 R-12] GC 10건: A14~A21·F4·G3**).
7. ~~PR 12개(E3 그대로 + PR-6에 무결성 잡 동석)~~ **[적대 R-22] PR 15개**: 1 → 1b → 2a → 2b → 7 → 3a → 3c → 3b → 4a → 4c → 4b → 5a → 5b → 6 → 8.
8. **멈춰서 보고할 항목**(DESIGN·WBS 부기+ADR로 해소 전제, §4·§5): WBS "COMPLETED 엣지 추가" 미이행(노출 공백), DoD "L/C 분기"·검증 K를 순수 함수로 충족, §15 "SO 자동 엣지 0" 개정, §8.3 부기 ② 잠금 모드 **문면 변경**(SO `FOR UPDATE` 선점 — 해석 아님), §7.5 마일스톤 **9→11종(BL_ISSUED 추가·PRESENTATION_DEADLINE 편입 — [적대 R-03])**, 구분 4종 중 2종 경로 미개방, LOGISTICS 첫 전표 쓰기. 계획서 §1은 이를 WBS 대비 4건 + DESIGN 대비 4건으로 등재한다([적대 R-13]).

---

## 1. 모순·중복·누락 해소표

> 서식: **문제(출처) → 해소 / 근거 / 번복 비용**. "부록 정정"은 §1.6에서 해당 부록 문면을 고친 항목이다.

### 1.1 스키마·열·소유

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-01** | 결제조건 4열: sA §A2는 선적 헤더에 ORIGIN으로 **복사**(TradeHeaderMixin 재사용), sB §B4는 "선적에 복사하지 않고 원천 전표에서 읽는다(파생값 이중 저장 금지)" | **복사한다(sA).** 대금만기 함수는 **선적 헤더 사본**을 읽는다. K 테스트: 생성 직후 선적 4열 == 원천 4열(동결 원천이라 영구 동일). 부록 B 정정 | S3-1 결정 #5 "결제조건 4열 … 전 전표 헤더에 복사(S3-2/S3-3 재입력 방지)"(`docs/plans/s3-1-plan.md:38`), 스냅샷 규율 `D:54`, `header_common_checks` 결제 규약 CHECK 자동 승계(`code:modules/trade_docs/mixins.py:173`). sB가 인용한 "이중 저장 금지"는 **파생값(만기일)**에 대한 것이지 입력 사본이 아니다. 원천 조인 1회 절감(상세 쿼리 수 고정) | 낮음(읽는 위치 1곳) |
| **X-02** | **수리일 원천**: sA §A8 `customs_records.accepted_on`이 적재의무 원천 / sB §B7 마일스톤 `CUSTOMS_CLEARED` 실적이 단일 원천(또는 (ii) 통관이 유일 입력처 + 마일스톤 동TX 복사) / sD §0-5·sE 가정 1은 (ii) 복사를 가정 | **`customs_records.accepted_on`이 유일 원천이고 복사하지 않는다(읽기 시 파생).** `CUSTOMS_CLEARED` 행은 **계획(`planned_on`)만** 저장하고 `actual_on`은 CHECK로 NULL 강제(`ck_milestones_customs_actual_from_records`). 유효 실적 = 같은 선적의 **구분 일치·살아 있는 통관 기록 `MIN(accepted_on)`**(분할 신고 중 가장 이른 수리일 → 적재기한도 가장 이르게 — fail-closed). 실적 API에 `CUSTOMS_CLEARED` → 422 `SHIPMENTS.MILESTONE.ACTUAL_FROM_CUSTOMS_RECORD`. 수리일 변경 추적은 audit_log(사유 필수, sC §C9)이며 `milestone_changes`가 아니다. 화면(`input_source="CUSTOMS_RECORD"`, sD §D6)·PR 배치(통관 API = PR-4a)는 그대로. 부록 A·B·D·E 정정 | sB §B7 "사람이 두 곳에 입력하는 구조 금지", `D:146` ③(한 정의 공유). (ii) 복사안 기각: 같은 사실 2곳 저장 = 동기화 결함 표면(정정·삭제·분할 신고마다 재복사). (i) 통관에 수리일 없음 기각: 통관 기록이 핵심 사실을 못 담는다 | 낮음(조립 함수 1개). 복사형으로 바꾸면 백필 1회 |
| **X-03** | 통관 카디널리티: sA §A8 선적:통관 **1:N**(분할 신고), 유니크 (구분, 신고번호) / sC §C5 유니크 **(shipment_id, declaration_kind)** 추가 = 구분당 1건 | **1:N(sA).** 유니크는 `(declaration_kind, declaration_no) WHERE deleted_at IS NULL` 1개 | 1:1이 겉보기엔 엄격하나 분할 신고 2건째의 수리 사실을 기록할 수 없어 적재기한 원천이 결측된다(fail-open). 1:N + MIN이 기한 쪽으로 더 엄격 | 낮음(인덱스 1개) |
| **X-04** | 열 이름: sC §C5 `declaration_number`·`holiday_date` vs sA §A8 `declaration_no`·sB §B11 `holiday_on` | 정본 부록 이름(`declaration_no`·`holiday_on`). 부록 C 정정 | 표 형태 정본은 sA·sB(sC §0-2 경계) | 없음 |
| **X-05** | 수입선적 통화: sC §C8 "단가·**통화**·원가를 복사하지 않는다" vs sA §A2·A3 헤더·라인 `currency`·`fx_rate` NOT NULL 복사 | **통화·환율은 복사**(원가 아님 — TradeHeaderMixin NOT NULL·`krw_fx_is_one`·`fx_pair` CHECK 승계), **단가·금액은 미복사**(`unit_price_amount` NULL·`line_amount`=0·`total_amount`=0). 부록 C 정정 | 원가 9채널 봉쇄 대상은 `unit_cost`·`line_cost`·`total_cost`(`code:modules/trade_docs/mixins.py` PurchaseLineMixin `_cost` 접미), 통화는 마스킹 대상 아님 | 낮음 |
| **X-06** | `milestone_changes`: sB §B9 `actor_id`·`idempotency_key` 열 / sC §C9 `actor_user_id` | `actor_user_id`(상태이력 관례·`USER_FK_CLASSIFICATION` ACTOR_LOG). **`idempotency_key` 열은 두지 않는다** — 멱등 정본은 `idempotency_keys`(claim·complete가 업무 TX 안, sC §C5 ①)이고 IMMUTABLE 표에 두 번째 멱등 저장소를 만들지 않는다(중복 해소). `milestone_change_notices.actor_id`도 `actor_user_id`. 부록 B 정정 | `code:modules/idempotency/service.py:1-21`, `D:350` | 낮음(IMMUTABLE 표라 열 가산은 마이그레이션 1건) |
| **X-07** | `holiday_calendar_years`: sD H3는 `version`(선언 있을 때 ver 409)을 요구, sB §B11 열 목록에 version 없음 | **VersionMixin 가산**(누락 보완) | `D:342`(주요 마스터 version), sD §D2-3 H3 | 없음 |
| **X-08** | sA §A3 "`lock_lines_for_consumption`이 헤더 소속을 걸러 주는지 PR 첫 커밋 실측" 미결 | **정적 확인: 걸러 준다**(`code:modules/trade_docs/quantities.py:199-216` `lines.c[LINE_HEADER_FK]==doc_id`). 요청 id 중 반환에 없는 것 = 소속 다름·삭제 → 422 `SHIPMENTS.SOURCE.LINE_MISMATCH`(X-12). 첫 커밋 실측은 유지 | 정적 독해 | 없음 |

### 1.2 상태·잠금·트랜잭션

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-09** | LOCK_ORDER 표기: sA §A10 `… → shipments → customs_records → approvals …` / sC §C2 `… → shipments → shipment_children → approvals …` / sB §B19 "milestones는 shipments 바로 뒤" | **sC §C2.** `shipment_children` = 선적 계열 비-라인 하위 행(`milestones`·`customs_records`·`shipment_parties`) — **PO 소유 OEM 마일스톤 행도 이 슬롯**(purchase_orders 뒤이므로 부분수열 성립). 여러 행이면 id 순. 부록 A 정정 | `D:344` ②(부분수열·변경은 ADR) | 중간(ADR-0078) |
| **X-10** ([적대 R-08]로 경로별 확정 — T2만 SHARE, T4·T5는 `lock_chain` UPDATE) | PO 헤더 잠금: sA §A4 "수입도 PO `FOR UPDATE`로 통일(단순성)" / sC §C2·C3 "PO `FOR SHARE`(계약 문면)" | **`FOR SHARE`(sC).** PO 상태·잔량 불변이라 SHARE→UPDATE 승격이 없고, PO 취소(`FOR UPDATE`)와 직렬화된다. SO는 수렴 동반이라 `FOR UPDATE` 선점(sC §C3). 부록 A 정정 | `D:229` ② 문면 그대로("상위 헤더 FOR SHARE"), 불필요한 쓰기 잠금 확대 회피 | 낮음(헬퍼 1곳) |
| **X-11** | 생성 TX의 마일스톤: sC §C1 T1 "마일스톤 슬롯 INSERT(부록 B)" / sB §B16·sD M4 "계획 초안은 별도 사람 1클릭" | **생성 TX는 마일스톤 0행.** 초안은 M4 별도 TX(같은 키 1회, 이미 있는 종류는 건너뜀). 부록 C 정정 | `D:190`, 생성 TX 쓰기 면적 최소화(J-08 롤백 단언 단순화) | 낮음 |
| **X-12** | 원천 라인 소속 불일치: sA §A3·sD S3 422 `LINE_MISMATCH` / sC §C6 ③-2 "본문 so_line_id가 so_id 소속이 아니면 404" | **422**(본문 참조). `D:370`의 404는 **경로**의 부모-자식(경로 `line_id`·`customs_record_id`·`change_id`·`milestone type`)에 한정. 부록 C 정정 | S3-1 참조 생성 선례 `code:modules/trade_chain/reference.py:185-215`("다른 원천의 라인 id를 섞으면 422 — 존재 여부를 알려 주지 않는다") | 낮음 |
| **X-13** | 수렴 판정 정의: sC §C1 "살아 있는 선적 **라인**을 가진 선적이 있는가" / sA §A7-2 "살아 있는 선적 ≥ 1" | **sA 정의 1개**(`status NOT IN DEAD_STATUSES AND deleted_at IS NULL`인 선적 ≥ 1). 불변식 "살아 있는 선적은 라인 ≥ 1"(sD S10 `SHIPMENTS.LINE.LAST_LINE` 409)이 두 정의를 동치로 만든다. 라인 삭제 경로도 같은 함수를 부른다(정의 공유 검증 — 결과 no-op). 선적 헤더 삭제 경로는 없다(취소만) | 비대칭 결손 선례 `D:140` ② | 낮음 |

### 1.3 권한·API·에러 코드

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-14** | 쓰기 역할: sA §A11 **모든 쓰기 A·T·L** / sC §C6 생성·라인·취소 = T, 헤더·출고지시·마일스톤·통보·통관 = T+L / sD §D2 대부분 A·T·L | **sC §C6(좁은 쪽)** — §2.9 표. SO·PO 잔량 소비·SO 수렴을 일으키는 동작(생성·라인·취소)은 무역, 일정·실적·통관·당사자·출고지시는 무역+물류. 부록 A·D 정정 | 물류가 상업 사실(잔량 소비·SO 상태)을 일으키지 않게 하는 쪽이 fail-closed. 여는 것은 행 1줄 | 낮음(넓히기) / 중간(좁히기 — 물류 업무 경로 차단) |
| **X-15** | 출고지시 통로: sC §C6 `/transitions to=출고지시`(서비스가 `to`별 역할 재판정) / sA §A7-1·sD S11 전용 `POST /shipments/{id}/release-order`(`FREEZE_ACTION_EDGES`) | **전용 경로(sA·sD).** `/transitions`는 `to_status="CANCELLED"` 1값(T)뿐 → **경로 단위 역할**이 되어 서비스 재판정이 필요 없다(라우터 가드만으로 403이 존재 검사 전에 성립). 부록 C 정정 | S3-1 동결 액션 관례(QT 발행·SO 확정 전용 경로), `D:370` 순서 | 낮음 |
| **X-16** | OEM 생산 마일스톤 쓰기 역할: sD M8 A·T·L / sC 미기재 | **A·T**(PO는 무역 소관 — 물류의 PO 쓰기 0 유지). 부록 D 정정 | 좁은 쪽, PO 라우터 `CAN_WRITE=(TRADE,)` 선례 | 낮음 |
| **X-17** | 휴일 경로: sB §B11 `PUT /holidays/{country}/{year}` / sC §C6 `/holiday-calendar-years` / sD H1 `/holidays/calendars` | **sD §D2-3 H1~H5**(쓰기 통로는 H3 `PUT /holidays/{country}/{year}` 하나) | API 정본은 sD(sD §0) | 없음 |
| **X-18** | 마일스톤 쓰기 메서드: sB §B8 ⑥ POST / sB GC-21 PUT | **POST**(sD §D2-2 확정 승계). GC-21은 POST로 읽는다 | 기록형 동작 POST 관례 | 없음 |
| **X-19** | 에러 코드 이름: sC §C5·C13 `PARTY.DUPLICATE_ROLE`·`IMPORT.EXCEEDS_ASSIGNABLE`·`CUSTOMS.DUPLICATE` / sA §A13 `PARTY.ROLE_DUPLICATE`·`QUANTITY.EXCEEDS_ASSIGNABLE`·`CUSTOMS.DECLARATION_DUPLICATE` | **sA 이름**(sD가 이미 소비). 부록 C 정정 | 표 정본 sA | 없음 |
| **X-20** | sA §A13 `SHIPMENTS.SHIPMENT.NOT_EDITABLE` vs 커널 `TRADE_DOCS.DOCUMENT.FROZEN`(EDITABLE_STATES 밖 편집 — `code:modules/trade_docs/editing.py:33-35`) | **기존 코드 재사용, 신설 철회**(중복). sD S7·S8·S9·S10의 409도 `FROZEN` | 커널 편입(sA §A1) 시 편집 가드가 자동 적용 | 없음 |
| **X-21** | sA §A13 `SHIPMENTS.SOURCE.KIND_NOT_OPEN`(채널입고·샘플 생성 요청) | **철회** — 생성 경로가 SO·PO 하위 2개뿐(sD S4·S6)이라 구분 입력이 없어 도달 불가. 닫힘은 DB `ck_shipments_kind_source`가 보증 | 죽은 코드 배제 | 없음 |
| **X-22** | `HOLIDAYS.CALENDAR.DUPLICATE_DATE` 409(sC §C5) — 본문 중복 날짜는 검증 오류 | 본문 내 중복 날짜 → **422** `HOLIDAYS.CALENDAR.DUPLICATE_DATE`. 동시 최초 선언 경합(연도 선언 행이 없어 둘 다 INSERT) → `uq_holiday_calendar_years_country_year_live` 위반 → **409 `HOLIDAYS.CALENDAR.YEAR_DUPLICATE`**(신설, "다시 불러온 뒤 시도해 주세요"). 부록 C 정정 | `D:352` 제약명 → 409 번역(500 금지) | 낮음 |
| **X-23** | 통보 기록 요청 `channel`(sD M6 "comm_logs 채널 열거 그대로"): comm_logs에 **채널 열이 없다**(`code:modules/collaboration/models.py:95-130`) | 요청에서 `channel` 제거. 수단은 요지(`summary`)에 적는다. 열 신설 안 함(문면 부재). 부록 D 정정 | 추측 구현 금지 | 낮음(열 가산) |
| **X-24** | 선적 목록 CSV: sC §C6·C8 `GET /shipments/export.csv` / sD §D2 엔드포인트 없음 | **S20 `GET /shipments/export.csv` 신설**(전 역할, S1과 같은 필터, UTF-8 BOM·수식 이스케이프, 원가 열 없음 → 역할별 헤더 분기 없음). PR-3a | `D:277-279`(목록 CSV 규약) | 낮음 |

### 1.4 마일스톤·휴일·스캔·계층

| # | 문제(출처) | 해소 | 근거 | 번복 비용 |
|---|---|---|---|---|
| **X-25** | **dedup 키 대상**: sB §B13 `deadline:milestones:{id}:{문턱}@{기일}` / sD §0-6·D11 알림 `entity_type`=소유 전표. 게다가 **`LOADING_DEADLINE`은 파생이라 행 id가 없다** | 키 = **`deadline:shipments:{shipment_id}:{MILESTONE_TYPE}/{문턱}@{YYYY-MM-DD}`** + 코어가 `:{recipient}`(`code:modules/notifications/service.py:13-16,221`). QT/PI는 `deadline:quotations:{id}:VALIDITY/{문턱}@{valid_until}`(PI 동형). **에스컬레이션 조회 prefix는 TYPE까지 포함**한다 — 현 조회 `like(f"{prefix}%@{stamp}:%")`(`code:modules/deadlines/service.py:242`)에 종류를 빼면 같은 선적·같은 날짜의 다른 종류 알림과 섞인다. 담당 이관 키 재작성(`_rewrite_alert_dedup_keys`, 같은 파일 `:502` 부근)과의 호환은 PR-6 첫 커밋 실측. 부록 B 정정 | sD §D11(alert-routes 1줄), 롤오버 = 새 기일 = 새 알림 의미론 유지(sB §B13) | 낮음(키 형식은 신규 알림부터) |
| **X-26** | §8.3 "자리": sB §B14·sE PR-3a "선적 라인에서 **AllocationPort 읽기 호출**" / 포트에는 읽기 메서드가 없다(`code:modules/sales_orders/ports.py:31-34` `on_confirmed`·`on_cancelled`뿐, `:6` 죽은 메서드 배제) | **포트 무변경.** 선적 라인 응답 `availability.status`는 `AllocationStatus.NOT_IMPLEMENTED`(`ports.py:20-22`) 값을 그대로 싣는 조립 함수 1개(`trade_chain/shipment_view.py`)가 만든다. S4-2가 포트에 읽기를 더할 때 이 함수만 교체. 화면은 "가용재고 미산정"(sD §D10) 그대로. 부록 B·E 정정 | 포트에 소비자 없는 메서드를 더하지 않는 S3-1 원칙, `D:229` ③, P-21 | 낮음 |
| **X-27** | 휴일 편집 역할: 읽기 결과 spec AMB-26 권장(ADMIN+물류) / sB §B11·sC §C6·sD H3 ADMIN 전용 | **ADMIN 전용**(부록 3개 일치·좁은 쪽) | ADR-03 기한 데이터 관리 주체 확대 금지 | 낮음(행 1줄) |
| **X-28** | sB §B12 ④ "국가 NULL이면 UNVERIFIED" / sA §A2 국가 2열 NOT NULL | **NOT NULL(sA).** `holiday_flag`의 None 분기는 순수 함수 방어 계약으로 남긴다(무해) | 입력 필수(sD §D7 "기본값 없음·필수") | 없음 |
| **X-29** | `item_profile_milestone_types`·`MilestoneType` 소유 미정(sB §B16 "마스터 부록과 겹치면 통합에서 조정") — item_profiles는 `requirements` 모듈(S3 계층 밖)이라 그 모듈이 전표 계층 열거를 임포트하면 import-direction 위반(`test:architecture/test_import_direction.py:38-44,64-69`) | **`MilestoneType` 열거 = `trade_docs/constants.py`(L0)**, **`milestones`·`milestone_changes`·`milestone_change_notices`·`item_profile_milestone_types` 모델 = `shipments` 모듈(L1)** — item_profiles·purchase_orders는 **테이블명 FK만**(임포트 0). 쓰기 서비스는 `trade_chain`(L2). `requirements`는 무접촉 | 계층 단방향(ADR-0059) | 낮음 |
| **X-30** | GC 번호: sB §B20 로컬 GC-01~31 / sE §E5-2 공식 GC-A14·A15·F4·C11~C16 | **공식 ID = sE §E5-2.** sB §B20 번호는 "경계 케이스 표의 행 번호"로 읽는다(대응: §5.2 표). `golden` 마커는 공식 ID에만 | GC 문서 ID 규칙(그룹 문자+연번) | 없음 |

### 1.5 누락(어느 부록도 소유하지 않은 것)

| # | 누락 | 해소(자율 확정) | 근거 | 번복 비용 |
|---|---|---|---|---|
| **N-01** | `item_profile_milestone_types`에 **쓰기 경로가 없다**(sB §B16은 표만, sD는 화면·API 없음) → 영구 빈 표(죽은 표)이고 초안은 항상 "구분별 전부"로 폴백 | `GET/POST/DELETE /item-profiles/{profile_id}/milestone-types` 신설(서류 세트 선례 `code:modules/documents/router.py:36,204-238` 동형 — 멱등 키·부분 유니크·soft delete). 쓰기 역할 = ~~선례 `CAN_EDIT=(CERT,)`+ADMIN(`code:modules/requirements/router.py:33`)~~ **[적대 R-14] ADMIN 전용**(+`GOVERNED_PREFIXES` 등재, PR-4c). 화면 = 기존 품목군 화면에 "마일스톤 세트" 섹션(PR-4b). 이로써 부채 #15 마일스톤 몫이 실제로 종결된다(ADR-0021) | `D:106` "신규 등록 시 자동 적용", `D:108` ⑥ | 낮음 |
| **N-02** | **PR-9a 신규 부채 ①**(승인 무결성 대사 `check_integrity`의 일일 잡 배선 또는 deferred 트리거 판정 — 오너=영준, 트리거 "감사·운영 투입 전(S3-2 이전)", `P:461`)이 **이미 트리거 도달**인데 PR-16 종결 목록·부록 A~E 어디에도 없다(조용한 누락) | **자율 확정: 잡으로 배선한다.** `approval-integrity-check` daily@05:40 KST(05:30 검산 뒤·06:00 앞, 빈 슬롯) — `check_integrity`(`code:modules/approvals/integrity.py:24`)를 `after_id` 페이지로 전건 순회, 불일치 1건 이상이면 잡 FAILED → 관리자 알림(`_fail_if_any_failed`, `code:modules/platform/scheduler.py:139-148`). 읽기 전용(상태 무수정). deferred 트리거는 계속 미채택(ADR-0028·0040). ~~PR-6 동석~~ **[적대 R-17] PR-1b 단독·불일치 = 문제별 dedup 알림·FAILED = 예외만**, ADR-0087 | 탐지 0보다 탐지 1이 엄격(fail-visible). 함수는 이미 있고 배선만 빠짐 | 낮음(잡 `enabled=false`). **오너 판정 대상으로 계획서 §5에 명시** |
| **N-03** | `comm_logs` 주제 CHECK에 `SHIPMENT`를 넣으면 **범용 `/comm-logs` POST·PATCH가 SHIPMENT 행을 만들거나 고칠 수 있는지**가 어느 부록에도 없다(sD는 "역할·주제 검증 무접촉"만) | collaboration 서비스의 **범용 쓰기 허용 주제를 `{CERTIFICATION}`로 명시**(SHIPMENT 행은 선적 전용 통로 M6만 생성, 범용 PATCH·DELETE도 SHIPMENT 주제 거부 — ~~422 기존 주제 오류 재사용 또는 404~~ **[적대 R-05] POST = 스키마 422 고정, id 접근(상세·PATCH·DELETE) = 404, 목록 기본 제외, documents 첨부 거부**). K 테스트: CERT가 `/comm-logs`로 SHIPMENT 생성·수정 시도 → 거부. PR-4a | 통보 기록은 롤오버 이력과 결속된 사실(sB §B9) — 다른 역할이 범용 경로로 고치면 결속이 깨진다 | 낮음 |
| **N-04** | 마일스톤 시간대 오류(sD M2 "422 tz 미지원")·값 형태 불일치(날짜형 종류에 시각 값)의 **코드가 없다** | `SHIPMENTS.MILESTONE.TIMEZONE_INVALID`(422, `zoneinfo` 검증 실패), `SHIPMENTS.MILESTONE.VALUE_SHAPE_MISMATCH`(422) 신설 | 카탈로그 1:1(`test:unit/test_error_catalog.py:19-49`) | 없음 |
| **N-05** | 마일스톤 쓰기 시 소유자(선적·PO)가 취소 상태일 때 코드가 sD에 "NOT_ACTIVE"로만 있고 PO 소유분 코드가 없다 | `SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE`(409) 1개로 통일(선적 CANCELLED·PO CANCELLED). 당사자·통관은 `SHIPMENTS.SHIPMENT.NOT_ACTIVE` 유지 | — | 없음 |
| **N-06** | sA §A9는 선적 5표만 table_policy·`_NEVER_SEEDED`·users FK 분류를 적었고, sB의 6표(마일스톤 계열 4·휴일 2)는 분류가 없다 | §2.3 표로 **11표 전량** 분류 | `test:integration/test_table_policy.py:35-64`, `test:architecture/test_scheduler_registry.py:56-73`, `test:architecture/test_user_fk_classification.py:39-48` | 없음 |
| **N-07** | OEM 마일스톤 쓰기의 잠금 순서·TX가 sC §C1 표에 없다 | **T13**: 멱등 → `purchase_orders` `FOR SHARE`(상태 확인, PO 무수정) → `milestones` 행 `FOR UPDATE`+행 version → UPDATE + `milestone_changes` INSERT + outbox ~~`shipments.milestone.changed`~~ **[PR-4c 적대 검토 정정] `purchase_orders.milestone.changed`**(payload `owner_type=PURCHASE_ORDER` — 알림 규칙은 event_type으로만 매칭하므로 선적과 같은 이름이면 선적 규칙이 OEM에 발화해 B15 'OEM 알림 없음'을 어긴다 → 이름 분리, fail-closed). PO 취소(`FOR UPDATE`)와 직렬화 | `D:340`, `D:344` ② | 낮음 |

### 1.6 부록 최소 수정 내역(이 통합에서 고친 문면)

| 부록 | 위치 | 수정 |
|---|---|---|
| A | 머리 | 우선순위 1줄 |
| A | §A4 잠금 마지막 줄 | PO `FOR UPDATE` 통일 → `FOR SHARE`(X-10) |
| A | §A8 규칙 | 수리일 = 유일 원천·마일스톤 비복사(X-02), 1:N 유지 확인(X-03) |
| A | §A10 결정 | `customs_records` → `shipment_children`(X-09) |
| A | §A11 권한 | 동작별 역할(X-14) |
| A | §A13 표 | `SHIPMENT.NOT_EDITABLE`·`SOURCE.KIND_NOT_OPEN` 철회 표기(X-20·X-21) |
| B | 머리 | 우선순위 1줄 |
| B | §B4 입력 | 선적 헤더 사본을 읽음(X-01) |
| B | §B7 단일 원천 | 통관 기록 유일·비복사·CHECK(X-02) |
| B | §B9 열 | `actor_user_id`, `idempotency_key` 열 없음(X-06) |
| B | §B13 dedup | 소유 전표 키·TYPE 포함 prefix(X-25) |
| B | §B14 | 포트 무변경·조립 함수(X-26) |
| C | 머리 | 우선순위 1줄 |
| C | §C1 T1 | 마일스톤 슬롯 INSERT 삭제(X-11) |
| C | §C5 표 | 통관 유니크 1개·열 이름·코드 이름(X-03·X-04·X-19·X-22) |
| C | §C6 | 본문 참조 불일치 422(X-12), 출고지시 전용 경로(X-15) |
| C | §C8 | 수입선적 통화 복사·단가 미복사(X-05) |
| C | §C13 | 코드 이름(X-19) |
| D | 머리 | 우선순위 1줄 |
| D | §0 가정 5 | 복사 → 읽기 시 파생(X-02) |
| D | §D2-1·D2-2 역할 열 | 동작별 역할(X-14·X-16) |
| D | M6 요청 | `channel` 제거(X-23) |
| E | 머리·§0 가정 1 | 우선순위 1줄, 복사 → 파생(X-02) |
| E | §E3-0 PR-3a·PR-4a·PR-6 행 | AllocationPort 문구(X-26)·통관 동TX 복사 문구(X-02)·무결성 잡(N-02) |
| E | §E4 ADR 표 | 0087 추가(N-02) |
| E | §E6-3 4a 변이 | "통관 수리일 복사 생략" → "수리일 MIN→MAX·죽은 통관 기록 포함"(X-02) |
| A~E | §9 R-27 목록 | **[적대 검토 2026-10-04]** 통합에 진 문면이 표지 없이 남아 있던 자리에 "[적대 R-nn]" 표지·취소선 추가(목록: §9 R-27) |

---

## 2. 통합 스키마·상수·계약

### 2.1 신규 테이블 11개 (최종)

공통: 감사 컬럼(`created_at/updated_at/created_by_id/updated_by_id`), soft delete(`deleted_at`), CHECK는 `create_table` 안에서 `op.f()` 이름(함정 ①·⑪), 부분 유니크 `WHERE deleted_at IS NULL`, 식별자 63자 이내(첫 커밋 실측), 전 FK `ondelete=RESTRICT`.

#### (a) `shipments` — 전표 커널 편입(DocKind `SHIPMENT`, 접두어 `SH`) · MUTABLE · VersionMixin

| 열 | 타입 | NULL | FIELD_POLICY | 비고 |
|---|---|---|---|---|
| TradeHeaderMixin 전 열 | (`code:modules/trade_docs/mixins.py:51-96`) | | 통화·환율 2열·결제조건 4열·Incoterms 3열·`doc_date` = **ORIGIN**, `internal_note`·`assignee_id` = **FREE**(FREE_COLUMNS 4개 불변), `doc_number`·`status`·`last_line_no`·`copied_from_id` = SYSTEM | 생성 시 원천 헤더에서 복사(X-01), 환율 고정 = 원천 스냅샷 복사. **[적대 R-15] `doc_date`는 복사 묶음에서 제외 — 생성 시 `today_kst()` 1회 설정(시스템 산출), 이후 불변(ORIGIN)** |
| `shipment_kind` | VARCHAR(16) | NOT NULL | ORIGIN | `EXPORT`·`IMPORT`·`CHANNEL_INBOUND`·`SAMPLE_FREE` |
| `so_id` | BIGINT FK sales_orders | NULL | ORIGIN | 수출 원천 |
| `po_id` | BIGINT FK purchase_orders | NULL | ORIGIN | 수입 원천 |
| `counterparty_partner_id` | BIGINT FK partners | NOT NULL | ORIGIN | 수출=SO 바이어, 수입=PO 공급사 (`PARTNER_COLUMN[SHIPMENT]`) |
| `counterparty_name` | VARCHAR(200) | NOT NULL | ORIGIN | 스냅샷 |
| `origin_country_code`·`dest_country_code` | CHAR(2) | NOT NULL | CONTENT(PLANNED만 편집) | ISO alpha-2, **markets FK 아님** |
| `total_amount` | BIGINT | NOT NULL | CONTENT | 수출 라인 합, 수입 0 |
| `frozen_at` | TIMESTAMPTZ | NULL | SYSTEM | 출고지시 시각 |

CHECK: `header_common_checks(SHIPMENT)` 전부 · `ck_shipments_kind_valid` · **`ck_shipments_kind_source`**(`(kind='EXPORT' AND so_id NOT NULL AND po_id NULL) OR (kind='IMPORT' AND po_id NOT NULL AND so_id NULL)` — 채널입고·샘플 행 DB 거부) · `ck_shipments_import_has_no_amount` · `ck_shipments_country_format` · `ck_shipments_planned_not_frozen` · `ck_shipments_released_frozen`.
인덱스: `uq_shipments_doc_number`(전역) · `ix_shipments_so_id_live` · `ix_shipments_po_id_live` · `ix_shipments_list(status, id DESC)` · `ix_shipments_assignee`.

#### (b) `shipment_lines` · MUTABLE (라인 version 없음 — 헤더 version+1)

열: `shipment_id` FK · `line_no` · `so_line_id` FK NULL · `po_line_id` FK NULL · `sku_id` FK·`sku_code`·`sku_name_ko`·`sku_name_en`·`sku_kind`(ORIGIN 스냅샷) · `currency` CHAR(3) NOT NULL · `quantity` INTEGER(CONTENT) · `unit_price_amount` BIGINT **NULL**(수출=SO 단가 사본, 수입 NULL) · `is_free` · `line_amount` BIGINT. **`SalesLineMixin`을 재사용하지 않는다**(그 믹스인의 `unit_price_amount`가 NOT NULL — `code:modules/trade_docs/mixins.py:137`). 중량·CBM·박스 열 없음(S3-3 PL 가산).
CHECK: `one_source` · `quantity_range`(1~99,999,999) · `import_no_price` · `export_priced`(~~`quantity::bigint *`~~ **[적대 R-04]** `so_line_id IS NULL OR quantity::numeric * unit_price_amount = line_amount` — S3-1 numeric 곱 규약) · **[적대 R-04]** `export_free_iff_zero_price`(`so_line_id IS NULL OR is_free = (unit_price_amount = 0)`) · `amount_range` · `line_no_positive`.
인덱스: `uq_shipment_lines_line_no_live` · `uq_shipment_lines_so_line_live(shipment_id, so_line_id)` · `uq_shipment_lines_po_line_live` · `ix_shipment_lines_so_line_id` · `ix_shipment_lines_po_line_id`.

#### (c) `shipment_parties` · MUTABLE · VersionMixin

열: `shipment_id` · `role` VARCHAR(16) · `partner_id` FK NOT NULL · `name_en` VARCHAR(200) NOT NULL · `address_en` VARCHAR(500) NULL · `is_auto` BOOLEAN(원천 자동 스냅샷 행 표식 — sD `auto` 필드의 원천, 누락 보완).
CHECK: `role IN ('SHIPPER','CONSIGNEE','NOTIFY','FORWARDER','CUSTOMS_BROKER')` · 제어문자 금지. 인덱스: `uq_shipment_parties_role_live(shipment_id, role)`.

#### (d) `shipment_status_log` · **IMMUTABLE** + `revoke_mutations`
`status_log_checks` 믹스인(상태 CHECK·`reason_required`(CANCELLED)·`actor_or_automatic`) — S3-1 상태이력 4표와 동형.

#### (e) `customs_records` · MUTABLE · VersionMixin · **[적대 R-16] M15(PR-4a)로 이동**
열: `shipment_id` · `declaration_kind` VARCHAR(8)(`EXPORT`·`IMPORT`) · `declaration_no` VARCHAR(40) · `declared_on` DATE · `accepted_on` DATE NULL(**수리일 유일 원천**, X-02) · `customs_broker_partner_id` FK NULL · `note` VARCHAR(1000). 세율·과세가격·세액·HS 열 **없음**.
CHECK: `kind_valid` · `accept_after_declare`(서비스 선검증 422 `SHIPMENTS.CUSTOMS.ACCEPT_BEFORE_DECLARE` + 제약명 번역 — [적대 R-26]) · `declaration_no_shape`. **[적대 R-18]** `declared_on`·`accepted_on` ≤ `today_kst()` 서버 검증(422 `SHIPMENTS.CUSTOMS.DATE_IN_FUTURE`, 여유 0). 인덱스: `uq_customs_records_declaration_live(declaration_kind, declaration_no)` · `ix_customs_records_shipment_live`. 1:N(X-03).

#### (f) `milestones` · MUTABLE · VersionMixin (모델 위치 `shipments` 모듈 — X-29)
열: `shipment_id` FK NULL · `po_id` FK NULL · `milestone_type` VARCHAR(24) · `planned_on`·`actual_on` DATE NULL · `planned_at`·`actual_at` TIMESTAMPTZ NULL · `tz` VARCHAR(64) NULL.
CHECK: `ck_milestones_one_owner`(정확히 하나) · `ck_milestones_type_valid`(**저장형만** — 선적 8종 + OEM 4종, 파생 3종 거부) · `ck_milestones_owner_type_scope`(OEM 4종 ⇔ `po_id` NOT NULL) · 날짜형/시각형 형태 CHECK · `ck_milestones_tz_required`(시각 값 있으면 tz NOT NULL) · **`ck_milestones_customs_actual_from_records`**(`milestone_type <> 'CUSTOMS_CLEARED' OR (actual_on IS NULL AND actual_at IS NULL)` — X-02).
인덱스: `uq_milestones_shipment_type_live(shipment_id, milestone_type)` · `uq_milestones_po_type_live(po_id, milestone_type)`.

#### (g) `milestone_changes` · **IMMUTABLE** + `revoke_mutations`
열: `milestone_id` FK · `change_kind`(`PLAN_SET`·`PLAN_CHANGED`·`ACTUAL_RECORDED`·`ACTUAL_CORRECTED`) · `old_on`·`new_on` DATE · `old_at`·`new_at` TIMESTAMPTZ · `old_tz`·`new_tz` · `reason` TEXT NULL · `actor_user_id` FK users · `created_at`. (`idempotency_key` 열 없음 — X-06)
CHECK: `change_kind_valid` · `reason_required`(`PLAN_CHANGED`·`ACTUAL_CORRECTED` ⇒ 공백 아닌 사유) · 제어문자 금지.

#### (h) `milestone_change_notices` · **IMMUTABLE** + `revoke_mutations`
열: `change_id` FK milestone_changes · `comm_log_id` FK comm_logs · `actor_user_id` FK users · `created_at`. UNIQUE `(change_id, comm_log_id)`.

#### (i) `item_profile_milestone_types` · MUTABLE (모델 위치 `shipments` 모듈)
열: `profile_id` FK item_profiles · `milestone_type`(선적 저장형 8종만). 인덱스 `uq_…_profile_type_live`. 쓰기 경로 N-01.

#### (j) `holiday_calendar_years` · MUTABLE · **VersionMixin**(X-07) · 모듈 `holidays`(S3_PLATFORM)
열: `country_code` CHAR(2) CHECK `^[A-Z]{2}$` · `year` SMALLINT(2000~2999) · `source_url` NOT NULL · `verified_on` DATE NOT NULL. 인덱스 `uq_holiday_calendar_years_country_year_live`. **[적대 R-24]** `uq_holiday_calendar_years_id_country_year` UNIQUE(id, country_code, year)(복합 FK 대상).

#### (k) `holidays` · MUTABLE
열: `calendar_year_id` FK NOT NULL · `country_code` · **`year` SMALLINT NOT NULL([적대 R-24])** · `holiday_on` DATE · `name`(제어문자 금지). 인덱스 `uq_holidays_country_day_live(country_code, holiday_on)`. ~~연도 일치는 서비스+테스트(교차 테이블)~~ **[적대 R-24]** 복합 FK `(calendar_year_id, country_code, year)` → `holiday_calendar_years(id, country_code, year)` + CHECK `ck_holidays_day_in_year`(`extract(year from holiday_on) = year`) — DB가 강제(위반은 422 `HOLIDAYS.CALENDAR.YEAR_MISMATCH` 번역).

### 2.2 기존 테이블 변경 (전량)

| 테이블 | 변경 | 마이그레이션 | 비고 |
|---|---|---|---|
| `comm_logs` | 주제 CHECK `subject_type IN ('CERTIFICATION','SHIPMENT')` 재정의 | M15(수기 `drop_constraint`/`create_check_constraint`, downgrade는 SHIPMENT 행 있으면 실패) | 범용 경로는 SHIPMENT 거부(N-03) |
| `sales_orders` | **없음** — 상태 CHECK에 IN_SHIPMENT 기존, `confirmed_at_consistent` 수용(`code:modules/sales_orders/models.py:118-124`), 상태이력 CHECK는 `STATUSES` 파생 | — | PR-3a 첫 커밋 실측 |
| `purchase_orders`·`purchase_order_lines` | **없음** — `profile_id`·ETA 열 미신설(ADR-0085) | — | |
| `credit/exposure.py` | **무변경**(P-01) | — | |

### 2.3 table_policy·불변·시드·users FK (11표 전량 — N-06)

| 표 | table_policy | `_NEVER_SEEDED` | users FK 분류 |
|---|---|---|---|
| shipments | MUTABLE | 등재 | `assignee_id` → `ASSIGNMENT_TARGETS`(purchase_orders 뒤), 감사 2열 → ACTOR |
| shipment_lines·shipment_parties·customs_records·milestones·item_profile_milestone_types·holiday_calendar_years·holidays | MUTABLE | 등재 | 감사 2열 → ACTOR |
| shipment_status_log | **IMMUTABLE** | 등재 | `actor_user_id` → ACTOR_LOG |
| milestone_changes·milestone_change_notices | **IMMUTABLE** | 등재 | `actor_user_id` → ACTOR_LOG |

DESIGN §17.5 확장 3표(`D:356` "상태 변경 이력 성격 — 신설 세션 등재"). stock_movements 무접촉.

### 2.4 열거·레지스트리 단일 출처 (변경 전량)

| 위치 | 변경 |
|---|---|
| `trade_docs/constants.py` | `DocKind.SHIPMENT`, `DOC_PREFIXES["SH"]`, DocKind 키 dict 11종 행(sA §A1), `ShipmentKind`(4), `PartyRole`(5), `DeclarationKind`(2), **`MilestoneType`**(저장형 선적 8: DOC_CUTOFF·CARGO_CLOSING·PSI·CUSTOMS_CLEARED·ETD·BL_ISSUED·ETA·IMPORT_TAX_DUE / OEM 4: RAW_MATERIAL_READY·FILLING·PACKING·OUTGOING_INSPECTION / 파생 3: LOADING_DEADLINE·PAYMENT_DUE·PRESENTATION_DEADLINE — 파생은 DB CHECK에 없음) |
| `trade_docs/machine.py` | SHIPMENT 8상태·HUMAN 3·AUTO 0·RESERVED 5·TERMINAL {CANCELLED}·`FREEZE_ACTION_EDGES`(PLANNED→RELEASE_ORDERED)·`REASON_REQUIRED_TO`{CANCELLED}·`EDITABLE_STATES`{PLANNED}; SO `AUTO_TRANSITIONS` += (CONFIRMED,IN_SHIPMENT)·(IN_SHIPMENT,CONFIRMED), `RESERVED[SO]` −= IN_SHIPMENT(COMPLETED 유지) |
| `trade_docs/quantities.py` | `CONSUMABLE_STATUSES[SO]` = {CONFIRMED, IN_SHIPMENT}; `LINE_CONSUMERS["SO_LINE"]` += FULFILL `SHIPMENT_LINE.so_line_id`, `["PO_LINE"]` += **IN_TRANSIT** `SHIPMENT_LINE.po_line_id`; `open_quantity(..., *, kinds=frozenset({"FULFILL"}))` |
| `trade_docs/chain.py` | `CHILD_LINKS` += (SO, shipments, so_id)·(PO, shipments, po_id); `NON_CHILD_FK_ALLOWLIST` += shipment_lines·shipment_status_log·shipment_parties·customs_records·milestones(shipment_id·po_id)의 FK(사유 10자↑) |
| `trade_docs/locking.py` | LOCK_ORDER(§2.11) |
| `trade_docs/policy.py` | `FIELD_POLICY["shipments"]`·`["shipment_lines"]` 전 열 분류, FREE 4 불변 |
| `trade_docs/transition.py` | `PAYLOAD_KEYS` += `cause_shipment_id`(SO 자동 수렴) |
| `trade_chain/chain_ops.py` | `DOC_MODELS`·`ANCESTORS[SHIPMENT]=((SO,"so_id"),(PO,"po_id"))`, `converge_parent` SHIPMENT 분기 |
| `trade_chain/router.py` | ~~SO Literal 갱신(임포트 시 assert — 엣지와 같은 커밋)~~ **[적대 R-23] SO Literal 무변경**(`public_transition_targets`는 사람 엣지만 셈 — `code:modules/trade_docs/machine.py:195-207`, 자동 2엣지는 값 공간 불변 → 기존 assert 통과 확인). 신설은 `ShipmentTarget=Literal["CANCELLED"]`+assert |
| `trade_chain/lifecycle.py` | **[적대 R-02]** `_cancel_sales_order`: `live_children_numbers` 검사를 `SO_CANCELLABLE` 상태 검사보다 **먼저**(`record_transition` 원칙 `code:modules/trade_docs/transition.py:136-146`과 정렬) |
| `trade_chain/document_flow.py` | `FLOW_KINDS` += SHIPMENT(수출만, 5쿼리 이내) |
| `order_board/constants.py` | `SO_IN_SHIPMENT`("선적중") 5번째 열·`NEWEST_FIRST_STAGES` |
| `collaboration/models.py`·`schemas.py`·`service.py` | `COMM_SUBJECT_TYPES` += SHIPMENT + 범용 허용 주제 상수 {CERTIFICATION}(N-03). **[적대 R-05]** 범용 `SubjectType` Literal은 `{CERTIFICATION}` 유지(쓰기·목록 필터), 목록 기본 조건 `subject_type IN 범용 허용 주제`, id 접근(상세·PATCH·DELETE)에서 SHIPMENT 행 404 |
| `documents/service.py` | **[적대 R-05]** `COMM_LOG` 소유 첨부는 범용 허용 주제의 comm_log만(SHIPMENT 주제 → 422 기존 소유자 검증 코드 재사용) |
| `handover/targets.py` | shipments(purchase_orders 뒤) |
| `deadlines/service.py` | `_policy` 공개 승격(기본 문턱 인자) — 기존 2축 결과 불변 회귀 테스트 |
| `platform/scheduler.py` | JOB 2행(§2.8) |
| 프런트 `lib/doc-status.ts`·`lib/alert-routes.ts`·`components/document-flow-panel.tsx`·`lib/datetime.ts` | SHIPMENT 라벨 8·`shipments` 이동·FlowKind·`toZonedPairDisplay` |

### 2.5 상태 총수 (갱신 후 핀)

| 문서 | 상태 | 허용(사람·자동) | 미허용 | 쌍 |
|---|---|---|---|---|
| QT | 5 | 6 (3·3) | 14 | 20 |
| PI | 5 | 8 (1·7) | 12 | 20 |
| SO | 8 | **10 (8·2)** | **46** | 56 |
| PO | 6 | 3 (3·0) | 27 | 30 |
| **SHIPMENT** | **8** | **3 (3·0)** | **53** | **56** |
| **합** | | **30 (18·12)** | **152** | **182** |

선적 활성 엣지: PLANNED→RELEASE_ORDERED(전용 `release-order`), PLANNED→CANCELLED·RELEASE_ORDERED→CANCELLED(`/transitions`, 사유 필수). RESERVED: PICKING·INSPECTED·RELEASED·SHIPPED·CLOSED(S4-2). 불변식: **확정 SO의 `IN_SHIPMENT` ⇔ 살아 있는 선적 ≥ 1**(X-13).

### 2.6 에러 코드 — 신규 28종 (→ **[적대 R-26] 32종 — 아래 4행 추가**)

| 코드 | HTTP | 상황 | 출처 |
|---|---|---|---|
| `SHIPMENTS.SOURCE.LINE_MISMATCH` | 422 | 본문 원천 라인이 원천 전표 소속 아님(X-12) | sA |
| `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE` | 409 | 수입선적 > PO 라인 배정 가능량 | sA |
| `SHIPMENTS.SHIPMENT.NOT_ACTIVE` | 409 | 취소된 선적에 당사자·통관 | sA |
| `SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE` | 409 | 살아 있는 통관 기록이 있는 선적 취소 | sA |
| `SHIPMENTS.LINE.DUPLICATE_SOURCE` | 409 | 한 선적에 같은 원천 라인(부분 유니크 번역) | sC |
| `SHIPMENTS.LINE.LAST_LINE` | 409 | 마지막 라인 삭제 | sD |
| `SHIPMENTS.PARTY.ROLE_DUPLICATE` | 409 | (선적, 역할) 부분 유니크 | sA |
| `SHIPMENTS.PARTY.ROLE_NOT_ALLOWED` | 422 | 수출 SHIPPER·수입 CONSIGNEE·자동 행 수정 | sA |
| `SHIPMENTS.PARTY.ENGLISH_NAME_MISSING` | 422 | 거래처 `name_en` 결측 | sA |
| `SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE` | 409 | (구분, 신고번호) 부분 유니크 | sA |
| `SHIPMENTS.CUSTOMS.KIND_MISMATCH` | 422 | 선적 구분 ≠ 신고 구분 | sA |
| `SHIPMENTS.CUSTOMS.REASON_REQUIRED` | 422 | 수리일 변경·통관 삭제 사유 없음 | sA |
| `SHIPMENTS.MILESTONE.DUPLICATE_TYPE` | 409 | (소유자, 종류) 부분 유니크 | sC |
| `SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE` | 422 | 파생 3종 쓰기 | sB |
| `SHIPMENTS.MILESTONE.ACTUAL_IN_FUTURE` | 422 | 실적 > today_kst+1 | sB |
| `SHIPMENTS.MILESTONE.REASON_REQUIRED` | 422 | 롤오버·실적 정정 사유 없음 | sB |
| `SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE` | 422 | 구분·결제유형 비적용 종류 | sB |
| `SHIPMENTS.MILESTONE.OWNER_NOT_OEM` | 422 | 일반 PO에 OEM 마일스톤 | sB |
| `SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE` | 409 | 취소된 선적·PO의 마일스톤 쓰기(N-05) | 통합 |
| `SHIPMENTS.MILESTONE.ACTUAL_FROM_CUSTOMS_RECORD` | 422 | `CUSTOMS_CLEARED` 실적 직접 입력(X-02) | 통합 |
| `SHIPMENTS.MILESTONE.TIMEZONE_INVALID` | 422 | IANA 시간대 아님(N-04) | 통합 |
| `SHIPMENTS.MILESTONE.VALUE_SHAPE_MISMATCH` | 422 | 날짜형/시각형 값 불일치(N-04) | 통합 |
| `HOLIDAYS.CALENDAR.SOURCE_REQUIRED` | 422 | 근거 링크·확인일 결측 | sB |
| `HOLIDAYS.CALENDAR.YEAR_MISMATCH` | 422 | 휴일 날짜 연도 ≠ 선언 연도 | sB |
| `HOLIDAYS.CALENDAR.DUPLICATE_DATE` | 422 | 본문 내 같은 날짜 2건(X-22) | sC→통합 |
| `HOLIDAYS.CALENDAR.YEAR_DUPLICATE` | 409 | 동시 최초 선언 경합(X-22) | 통합 |
| `HOLIDAYS.COUNTRY.INVALID` | 422 | ISO alpha-2 아님 | sB |
| `HOLIDAYS.CSV.INVALID_FORMAT` | 422 | CSV 인코딩·헤더·크기 | sD |
| `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED` | 409 | ETD·BL_ISSUED·ETA 실적이 살아 있는 선적 취소 | 적대 R-01 |
| `SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE` | 422 | PLANNED 선적에 ETD·BL_ISSUED·ETA 실적 입력 | 적대 R-01 |
| `SHIPMENTS.CUSTOMS.DATE_IN_FUTURE` | 422 | 신고일·수리일 > `today_kst()` | 적대 R-18 |
| `SHIPMENTS.CUSTOMS.ACCEPT_BEFORE_DECLARE` | 422 | 수리일 < 신고일(CHECK 선검증·제약명 번역) | 적대 R-26 |

**재사용(신설 금지)**: `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`·`DOCUMENT_NOT_CONSUMABLE` · `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE` · `TRADE_DOCS.TRANSITION.NOT_ALLOWED`·`REASON_REQUIRED` · **`TRADE_DOCS.DOCUMENT.FROZEN`**(X-20) · `COMMON.CONCURRENCY.VERSION_CONFLICT`·`LOCK_BUSY`(55P03·40P01; 57014는 500 — P-47) · `COMMON.RESOURCE.NOT_FOUND` · `COMMON.AUTH.FORBIDDEN` · 거래처 유형 불일치 기존 코드 · `IDENTITY.ADMIN.LAST_ONE`.
**철회(5)**: `SHIPMENTS.SOURCE.KIND_NOT_OPEN`(X-21) · `SHIPMENTS.SHIPMENT.NOT_EDITABLE`(X-20) · `SHIPMENTS.PARTY.DUPLICATE_ROLE`·`SHIPMENTS.IMPORT.EXCEEDS_ASSIGNABLE`·`SHIPMENTS.CUSTOMS.DUPLICATE`(X-19).
규칙: 3세그먼트·문구 조치 힌트·카탈로그 1:1·detail에 금액 0·log_context 분리(`D:374`).

### 2.7 이벤트·audit

| 이벤트(outbox) | 동작 | payload(화이트리스트 — 금액·원가 0) |
|---|---|---|
| `shipments.shipment.created` | T1·T2 | shipment_id·doc_number·shipment_kind·so_id/po_id·partner_id·assignee_id |
| `shipments.shipment.<전이>`(커널 `EVENT_PREFIX`) | T5·출고지시 | 커널 `PAYLOAD_KEYS` |
| 기존 `sales_orders.sales_order.*` | 자동 수렴 | 기존 + `cause_shipment_id` |
| `shipments.milestone.changed` | T6·T7 ~~·T13~~ | owner_type·owner_id·milestone_type·change_kind·전후 날짜 |
| **[PR-4c 적대 검토 정정 — N-07]** `purchase_orders.milestone.changed` | T13(OEM 생산 일정) | 같은 화이트리스트(owner_type=PURCHASE_ORDER, aggregate = purchase_orders) — 선적 이벤트와 이름을 갈라 선적용 알림 규칙이 OEM 변경에 발화하지 않게 한다(B15 'OEM 알림 없음' — 규칙 매칭은 event_type 일치뿐). 소비자 0(PR-6 스캔은 이벤트가 아니라 `milestones` 표를 읽고 OEM은 스캔 대상 밖) |
| `shipments.customs.recorded` | T9 | shipment_id·declaration_kind·수리일 유무 |

audit_log(`AuditAction` 상수): `shipments.customs.corrected`·`shipments.customs.deleted`(사유 필수) · `shipments.party.added`·`shipments.party.removed` · `holidays.calendar.replaced`(국가·연도·건수·근거) · 기존 `identity.role.*`·handover. 기본 `alert_rules` 시드 0. 대외 채널 0.

### 2.8 잡 — JOB_REGISTRY 12 → **14**

| 시각(KST) | 코드 | 상태 |
|---|---|---|
| 04:20 / 04:25 | idempotency-purge / session-purge | 기존 |
| 05:00 / 05:30 | storage-monitor / trade-docs-totals-verify | 기존(검산은 DocKind 루프로 SHIPMENT 자동 편입 — 수입 0=0) |
| **05:40** | **`approval-integrity-check`** | **신규(N-02)** — 읽기 전용 대사. ~~불일치 ≥1 → FAILED+관리자 알림~~ **[적대 R-17] PR-1b. 불일치 = `approval-integrity:{approval_id}:{problem}` dedup 관리자 알림(문제별 1회), 잡 FAILED = 실행 예외만** |
| 06:00 / 06:10 / 06:30 | certification-sweep / document-expiry-sweep / deadline-scan | 기존 |
| **06:40** | **`trade-deadline-scan`** | **신규** — 선적 마일스톤 4종(DOC_CUTOFF·CARGO_CLOSING·IMPORT_TAX_DUE 계획 미실적, LOADING_DEADLINE 파생 OK+ETD 실적 없음) + QT/PI 만료 임박 D-N, 기본 문턱 D-7/3/1+도과, 건별 TX, 실패 1건 → FAILED. 본체 `trade_chain/deadline_scan.py` |
| 07:00 / 07:10 / 08:00 / 09:00 | stagnation-scan / approval-stagnation-scan / backup-freshness / daily-briefing | 기존(브리핑 수신자 확장 없음) |
| interval@1 | outbox-dispatch | 기존 |

4금 논증(두 신규 잡 공통): 전표 상태 무변경·알림 생성만·대외 발송 0·발주·원장·법적 판정 0. 갱신 위치: `test_scheduler_registry.py:143-174`(4금 집합·**총수 14**·06:40·05:40·daily 중복 0), `test_no_auto_confirm_code_path_exists.py:759-795`(`_trade_chain_imports == {"expiry_sweep","deadline_scan"}` — 우회 금지), `test_po_no_auto_path.py:352-361`(이름 통과), runbook 잡 표, DESIGN §15. 휴일 경고·파생 재계산 잡은 **만들지 않는다**(읽기 시점 계산값).

### 2.9 AUTHZ 행 (최종 — X-14·X-15·X-16·X-27)

`GOVERNED_PREFIXES` += `/api/v1/shipments`·`/api/v1/holidays` + **[적대 R-14] `/api/v1/item-profiles/{profile_id}/milestone-types`**(현재 `/item-profiles`는 통제 접두어 밖이라 행 누락이 조용히 통과 — `test:architecture/authz_matrix.py:25-44`). SO·PO 하위는 행만 추가.

| 엔드포인트 | A | T | L | C | V |
|---|---|---|---|---|---|
| `GET /shipments`·`/{id}`·`/status-log`·`/milestones`·`/milestone-changes`·`/customs-records`·`/export.csv` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `POST /sales-orders/{id}/shipments[/preview]`·`/purchase-orders/{id}/shipments[/preview]` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `POST/PATCH/DELETE /shipments/{id}/lines…` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `POST /shipments/{id}/transitions`(`to=CANCELLED` 1값) | ✓ | ✓ | ✗ | ✗ | ✗ |
| `PATCH /shipments/{id}`(FREE·국가) | ✓ | ✓ | ✓ | ✗ | ✗ |
| `POST /shipments/{id}/release-order` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `POST/DELETE /shipments/{id}/parties…` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `POST/PATCH/DELETE /shipments/{id}/customs-records…` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `POST /shipments/{id}/milestones/{type}/plan·actual`·`/plan-draft`·`/milestone-changes/{cid}/notices` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `GET /purchase-orders/{id}/milestones·milestone-changes` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `POST /purchase-orders/{id}/milestones/{type}/plan·actual` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `GET /item-profiles/{id}/milestone-types` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `POST/DELETE /item-profiles/{id}/milestone-types…`(**[적대 R-14] A 전용**, PR-4c) | ✓ | ✗ | ✗ | ✗ | ✗ |
| `GET /holidays/calendars`·`/holidays`·`/{c}/{y}/export.csv` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `PUT /holidays/{c}/{y}`·`POST …/import-csv/preview` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `GET /document-flow/{doc_kind}/{doc_id}` | 기존 행 유지(값 공간만 확장) | | | | |

LOGISTICS 첫 전표 쓰기(§2 부기+ADR-0079). 부모-자식(경로) 404·부작용 0, 401→403→404→409→422. 쓰기 스키마 전부 `extra="forbid"`.

### 2.10 모듈 계층 (import-direction 등재 — `known_s3` 포함)

| 모듈 | 계층 | 내용 |
|---|---|---|
| `trade_docs` | L0 | 상수·`MilestoneType`·커널·**`schedule.py`(순수 산식)** |
| `shipments` | **L1(신규)** | 선적 5표·마일스톤 계열 4표 모델·단건 조회(SO·PO 모델 임포트 0) |
| `trade_chain` | L2 | `shipment_flow.py`(참조 생성·수렴·취소 가드)·`milestone_flow.py`·`shipment_view.py`(조립·가용 자리)·`deadline_scan.py` |
| `holidays` | **S3_PLATFORM(신규)** | 2표·`calc.py`(순수)·API — 전표 임포트 0 |
| `deadlines`·`notifications` | 기존 플랫폼 | 전표 임포트 금지 유지(스캔은 trade_chain이 순수 함수·`notify`만 임포트) |
| `collaboration` | 기존 | 주제 상수만 변경 |

### 2.11 LOCK_ORDER (ADR-0078)

```
idempotency_keys → order_intakes → partners → quotations → proforma_invoices
→ sales_orders → purchase_orders → shipments → shipment_children → approvals → lines → doc_number_seq
```
- `shipment_children` = milestones(선적·PO 소유)·customs_records·shipment_parties(X-09). 라인 범주는 원천 라인 → 선적 라인(id 순).
- 잠금 모드: partners `FOR KEY SHARE` / sales_orders `FOR UPDATE`(수렴 동반 — SHARE 흡수, 승격 금지) / purchase_orders `FOR SHARE`(X-10) / shipments `FOR UPDATE`(쓰기)·`FOR SHARE`(T8) / 원천 라인 `FOR UPDATE` id 순.
- **[적대 R-08] 경로별 확정**: PO `FOR SHARE`는 **T2(수입 생성)만**. 선적 기점 경로 T4·T5는 `lock_chain`(`code:modules/trade_chain/chain_ops.py:58-63`)을 그대로 써서 SO·PO 모두 `FOR UPDATE`(승격 없음 — 교착 무관). **T8·T9는 멱등 → partners `FOR KEY SHARE`(id 순) → shipments → shipment_children**(관세사·상대 거래처 검증이 선적 잠금 뒤로 가지 않게), J-07 계측 대상.
- holidays·holiday_calendar_years·comm_logs는 순서표 밖(교차 없음 — sC §C2).
- TX 경계 T1~T12는 sC §C1 그대로(T1의 마일스톤 INSERT 삭제 — X-11) + **T13 OEM 마일스톤 쓰기**(N-07).

### 2.12 마이그레이션 DAG (3건, 직렬)

| 번호 | 내용 | `down_revision` | PR |
|---|---|---|---|
| **M13** | `holiday_calendar_years`(+version·[적대 R-24] UNIQUE(id,country_code,year))·`holidays`(+`year`·복합 FK·연도 CHECK) | `f2cb6020b2bb`(현 단일 head) | PR-2a |
| **M14** | `shipments`·`shipment_lines`·`shipment_parties`·`shipment_status_log`(+REVOKE) ~~·`customs_records`~~ **[적대 R-16]** | M13 | PR-3a |
| **M15** | **`customs_records`([적대 R-16])**·`milestones`(+`customs_actual_from_records` CHECK)·`milestone_changes`(+REVOKE)·`milestone_change_notices`(+REVOKE)·`item_profile_milestone_types` + **`comm_logs` 주제 CHECK 재정의** | M14 | PR-4a |

공통: 드라이런 4종(heads 단일·빈 DB upgrade·base 왕복·check 드리프트 0, `D:374`), 시드 0, CHECK 정의문 테스트, downgrade 완결(M15는 SHIPMENT 주제 행 있으면 실패). PR-1b·3c·4c·5a·6·7·8·b PR은 마이그레이션 0.

---

## 3. ADR 목록 (0074~0087) — PR-1에서 일괄 등재, 5줄 서식

| ADR | 제목(요지) | 원천 | 구현 PR |
|---|---|---|---|
| **0074** | 선적 전표 커널 편입(DocKind `SHIPMENT`·`SH`)·5표 데이터 모델(헤더 원천 FK 정확히 하나·수입 원가 비복사·통관 1:N 사실 기록 — **통관 표는 M15/PR-4a([적대 R-16])**·**실적 기록 선적 취소 409([적대 R-01])**)·상태 8값 활성 3엣지·RESERVED 5·총수 30/152/182·구분 4종 중 채널입고·샘플 경로 미개방 | sA §A1~A9, X-03·X-05 | 3a |
| **0075** | SO CONFIRMED↔IN_SHIPMENT 자동 수렴 2엣지 — §15 [M4] 부기 "SO 자동 엣지 0" 개정·4금 논증·`test_po_and_so_have_no_automatic_edges` 개명·**[적대 R-02] SO 취소 후속 생존 검사 선행**·**[적대 R-13] 번복 비용 중간·오너 확인 3순위** | sA §A7-2, sC §C12 | 3a |
| **0076** | SO COMPLETED 엣지·short-close(P-02) **S3-2 미개방**(노출 공백 방지)·provider 결속 아키텍처 테스트·WBS 문면 S3-3 이관 | sA §A7-3, sC §C12 | 3a |
| **0077** | 수입선적 소비 kind `IN_TRANSIT`·`open_quantity` kind 필터·배정 가능량·S4-1 FULFILL 비중첩 승계 계약 | sA §A4 | 2a·3a·5a |
| **0078** | LOCK_ORDER 개정(shipments·shipment_children)·SHARE→UPDATE 승격 금지·SO `FOR UPDATE` 선점·PO `FOR SHARE` | sC §C2·C3, X-09·X-10 | 3a |
| **0079** | 권한: LOGISTICS 첫 전표 쓰기(동작별 범위 §2.9)·휴일 쓰기 ADMIN·OEM 마일스톤 A·T·출고지시 전용 경로·담당 이관 등록·**[적대 R-14] 마일스톤 세트 쓰기 A 전용** | sC §C6, X-14~X-16 | 2a·3a·4a·4c |
| **0080** | 마일스톤 모델: 계획/실적 이중값 행·**[적대 R-03] §7.5 9종 → 11종(BL_ISSUED 저장형 추가·PRESENTATION_DEADLINE 파생형 편입)**·**[적대 R-10] 적재 이행일 = ETD·BL_ISSUED 실적 중 존재값의 MAX(가정 — 근거·대안·번복 비용 병기)**·날짜형/시각형·파생 3종 비저장·덮어쓰기 금지·**`CUSTOMS_CLEARED` 실적 = 통관 기록 `MIN(accepted_on)` 파생(비복사)**·결제조건은 선적 헤더 사본 | sB §B1~B4·B7·B8, X-01·X-02 | 2a·4a |
| **0081** | L/C 대금만기·제시기한·tolerance = 순수 함수·K 테스트, 운영 경로 UNKNOWN(`LC_TERMS_NOT_REGISTERED`), tolerance 좁은 쪽 반올림, L/C 플래그 공급 미개방 승계 | sB §B5·B6 | 2a |
| **0082** | holidays: 국가 ISO alpha-2(markets 비FK)·연도 선언 단위 근거 2필드 필수·**경고만**(자동 순연 0)·UNVERIFIED ≠ 평일·시드 0·ADR-0055 "휴일 보정" → 경고 해석·**[적대 R-09] 적용 = ETA(도착국)만(`D:203` 문면), 출발국 확장 = 부채**·**[적대 R-24] 연도·국가 DB 강제** | sB §B11·B12 | 2a |
| **0083** | 롤오버 이력 `milestone_changes`·통보 `milestone_change_notices` IMMUTABLE·comm_logs SHIPMENT 주제(선적 전용 통로만, 범용 경로 **쓰기·읽기·첨부** 거부 — [적대 R-05])·발송 0·**M2·M3 응답 `{board, change}`([적대 R-19])** | sB §B9, sC §C9, N-03 | 4a |
| **0084** | `trade-deadline-scan` daily@06:40·QT/PI D-N 동잡·trade_chain 배치·dedup 키 형식 `deadline:shipments:{id}:{TYPE}/…`·PAYMENT_DUE·제시기한 알림 제외 | sB §B13·B18, sC §C11, X-25 | 6 |
| **0085** | 인계 판정 묶음: OEM `profile_id` 미신설·facilities 미신설 유지·PO 라인 ETA 열 미신설(계산값)·마일스톤 세트 `item_profile_milestone_types`+쓰기 경로(**A 전용 — CERT 미배정 사유: `D:37` 인증 편집 = 시장·요건 템플릿, 마일스톤은 물류·무역 업무. 번복 = authz 행 1줄, 낮음**) | sB §B15~B17, N-01 | 4a·4c·5a |
| **0086** | 브라우저 e2e 도구 미채택·렌즈 11 3층 증거·사용자 역할 화면 S3-2 배정·계정 생성 API 미신설·SearchSelect 오선택 수정 | sD §D14~D16, sC §C7 | 3b·7·8 |
| **0087** | 승인 무결성 대사 잡 `approval-integrity-check` daily@05:40 배선(PR-9a 부채 ① 소비, deferred 트리거 미채택 유지)·**[적대 R-17] 문제별 dedup 알림·FAILED = 예외만·첫 커밋 전건 실측** | N-02 | ~~6~~ **1b** |

**기존 ADR 부기**(번호 없이 "부기 2026-10-xx"): 0021(마일스톤 세트 종결) · 0024(수입선적 원가 비복사 — 10번째 채널 미개설) · 0037(OEM 프로파일 판정) · 0051·0052(RESERVED·CHILD_LINKS 소비) · 0053·0054(선적 복사·채번 접두어) · 0055("휴일 보정" → 경고만) · 0058(잡 14행) · 0059(LOCK_ORDER 승계) · 0060(무결성 대사 잡 배선) · 0064(P-01 유지+COMPLETED 보류) · 0066(보드 열 가산) · 0067(LOGISTICS 행) — 13건.

---

## 4. DESIGN 부기 목록 (PR-1, 원문 불변·[M5] 보강 문단 "S3-2 계획 — 자율 확정 2026-10-04")

§2(LOGISTICS 첫 전표 쓰기·동작별 범위) · §3 표 맵(customs_records 형태·holiday_calendar_years·milestone_changes·notices·item_profile_milestone_types) · §5.4/§7.9(comm_logs SHIPMENT 주제 — 선적 전용 통로, `/comm-logs` 역할 무변경) · §7.1(구분 4종 중 2종 미개방·원천 FK 정확히 하나·수입 원가 비복사·결제조건 사본) · §7.2(선적 코드값·전이표·RESERVED·SO 자동 수렴·COMPLETED 보류·총수 30/152/182) · §7.5(마일스톤 ~~10종~~ **9→11종 — BL_ISSUED 추가·PRESENTATION_DEADLINE 편입([적대 R-03])**·파생 계산값·수리일 = 통관 기록·휴일 의미론(**ETA 한정 — [적대 R-09]**)·롤오버·통보·선적 캘린더 S4-4 재확인) · §8.3 부기 ②(kind 필터·IN_TRANSIT·SO FOR UPDATE 선점 — **해석이 아니라 문면 변경으로 명기([적대 R-13])**, 가용 "자리" = 응답 필드) · §14 [M4] 보강(S3-2 화면 배정) · §15(SO 자동 엣지 0 개정·잡 14행) · §17.2 부기 ②(LOCK_ORDER) · §17.5(IMMUTABLE 3표) · §20 해석 주(검증 K L/C = 순수 함수).

---

## 5. WBS v1.6·GC v1.5 갱신안

### 5.1 WBS v1.6 (sE §E5-1 + 통합 추가)

| 행 | 갱신 문면(요지) |
|---|---|
| S3-2(`W:114`) | ① "RESERVED 엣지 COMPLETED 추가" → S3-3 provider PR로 이관(ADR-0076) ② OEM `profile_id` → 판정 결과 미신설(ADR-0085) ③ short-close·PO 라인 ETA → 미개방·계산값 ④ 구분 4종 중 채널입고·샘플무상 경로 미개방 ⑤ **(통합) 승인 무결성 대사 잡 배선(PR-9a 부채 ①)** ⑥ 통관 이슈 임시 규칙(`D:394`) runbook 안내(코드 0) |
| S3-2(`W:115-116`) | DoD① L/C 분기·검증 K = 순수 함수 단위·K 테스트로 충족, 운영 경로는 S3-3 `lc_terms` 주석 |
| S3-3(`W:118-122`) | `lc_terms` → S3-2 산식 배선, SO COMPLETED 엣지·short-close를 provider `reflected=True`·선적분 차감과 같은 PR |
| S3-4 | S3-2~S3-4 구간 DG 선적 = 배지·runbook 경고만 |
| S4-1(`W:134`) | 입고 FULFILL 소비 ↔ 수입선적 IN_TRANSIT 비중첩 계약 승계 |
| S4-2(`W:139-140`) | 선적 RESERVED 5상태 엣지·출고 원장 시점·검수 미완료 CI/PL 차단·가용 "자리" 함수 교체 |
| 매핑표(`W:209-225`) | S3-2 행: ~~A14·A15·F4·C11~C16~~ **[적대 R-12] A14~A21·F4·G3** |
| 변경 이력 | v1.6 항목(자율 확정, ADR-0074~0087) |

### 5.2 GC v1.5 — ~~9건~~ **10건([적대 R-12] — 무역 기일 케이스는 C[인증·규제] 대신 A 연번, A15 원가 단언은 G3로 분리)** (S3-2 배정 0건 해소, 추가만)

| GC | 내용 | 경계 케이스(sB §B20 행·sA §A16·sC §C14) | PR |
|---|---|---|---|
| **A14** | 부분선적 1:N: 합=SO 수량 → 잔량 0 / +1 → 409 EXCEEDS_OPEN / 취소 → 복원 / 살아 있는 선적 → SO 취소 409 | sA §A16 A행 | 3a |
| **A14 기대 코드([적대 R-02])** | 살아 있는 선적 → SO 취소 = 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`, `detail.successors=[SH-…]` / ETD 실적 선적 취소 = 409 `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`(R-01, 4a 가산) | | 3a·4a |
| **A15** | 수입선적: PO `open_quantity`·상태 불변 / 배정 가능량 초과 409 ~~/ 응답 원가 키 0~~(→ G3) | sA §A16 A행 | 5a |
| **G3** | 수입선적 응답·CSV·outbox에 원가 키 0(원가 열람 역할로도) — ADR-0024 10번째 채널 미개설 | sC K-07 | 5a |
| **F4** | 동시 부분선적 7+7(수량 10) 실제 동시 실행 → 1 성공·1 409, 500·40P01 0 | sC J-02 | 3a |
| **A16**(구 C11) | 대금만기 분기(T/T 앵커+일수·음수 ETD 전용·KST 확정일·**수입 ORDER_DATE = PO `frozen_at` KST 날짜([적대 R-29])** / UNKNOWN 대체 금지 / TT_ADVANCE 100% 해당 없음 / **L/C SIGHT = 네고일 / USANCE = 인수일+N / 결측 UNKNOWN** / L/C 운영 UNKNOWN) | B20 행 01~**10**·32 | 2a |
| **A17**(구 C12) | L/C 제시기한 MIN 양방향·동일일·결측 UNKNOWN(**수출·수입 LC 공통 — R-11**) / tolerance 좁은 쪽·경계 포함 | B20 행 **11**~13·27·28 | 2a |
| **A18**(구 C13) | 적재의무 = 수리일+30(윤년)·계획 미사용 / **수리일 = 살아 있는 통관 기록 MIN**(X-02) / **부분 수리 = PARTIAL 배지·산식 MIN 유지(R-06)** / **이행일 = ETD·BL 실적 MAX(R-10)** / **미래 수리일 422(R-18)** | B20 행 14·33·34·37 | 2a·4a |
| **A19**(구 C14) | ETA 현지 연휴 경고 양방향 + 미선언 UNVERIFIED + 휴일과 겹친 만기 값 불변(**ETA 한정 — R-09**) | B20 행 16~18 | 2a·4a·4b(화면 배지) |
| **A20**(구 C15) | 롤오버 이력 누적·같은 키 1행·**같은 `change.id`(R-19)**·사유 필수·IMMUTABLE / 실적 입력 → 파생 재계산 / **시각형 실적 `actual_at` > now_utc → 422(R-18)** | B20 행 03·22·23·35 | 4a |
| **A21**(구 C16) | 기일 스캔: 롤오버 → 새 기일 새 알림·옛 키 재발송 0 / QT/PI D-N 후보 = 스윕 후보 / KST 경계 / **시각형 도과 경계 — LA 10-11T00:00Z 기한을 10-10T21:40Z에 스캔 → 도과 아님(R-20)** | B20 행 24·29~31·36 | 6 |

`pytest -m golden` 대사: S3-1 종결 43건 + ~~9건 → 52건~~ **10건 → PR-8에서 53건 이상([적대 R-12])**(마커 수 = 케이스 수 이상, 케이스마다 최소 1). C 배치 사유를 GC 이력에 적지 않고 A·G로 옮긴 이유: GC 삭제 금지 규칙(`kbeauty-golden-cases-v1.md:226`)상 등재 후 재배치가 불가하므로 PR-1 등재 전에 그룹 정의(`:91` C = 인증·규제, `:174` G = 수입원가)에 맞춘다.

---

## 6. 부채 이월·소비 대사 (조용한 누락 금지)

### 6.1 S3-2 입력 부채 처리

| ID | 내용 | S3-2 처리 | 결과·남는 트리거 |
|---|---|---|---|
| P-01 | 선적분 노출 차감 = S3-3 provider PR에서만 | **유지**(exposure 무변경, sA §A15) | 이월 S3-3 |
| P-02 | short-close | **판정: 미개방**(ADR-0076) | 이월 S3-3 provider PR |
| P-03 | QT/PI D-N 알림 | **구현**(PR-6) | 종결 |
| P-04 | 수입선적 PO 참조·customs_records | **구현**(PR-3a 표·PR-4a 통관 API·PR-5a 생성) | 종결 |
| P-05 | PO 라인 ETA 슬롯 | **판정: 열 미신설·계산값**(ADR-0085, PR-5a) | 재트리거: S3-4/P4 라인 단위 예정일 요구 |
| P-06 | OEM `profile_id` | **판정: 미신설**, OEM 4종은 PO 소유 마일스톤(PR-4a) | 재트리거: 두 번째 프로파일 요구 |
| P-07 | RESERVED 엣지·CHILD_LINKS·LINE_CONSUMERS | **S3-2 몫 구현**(PR-3a) | S4-1·S4-2 몫 이월 |
| P-57 | facilities 마스터 | **판정 (a) 불발동** — 미신설 유지 | 트리거 (b)·(c) 유지 |
| PR-16 ③ | 사용자 역할 화면 | **구현**(PR-7) | 계정 생성 API 부채 신설 |
| PR-16 ⑤ | SearchSelect 오선택 | **구현**(PR-3b) | 종결 |
| PR-16 ⑥ | `.shard_durations.json` | **갱신**(PR-2a, 3a·4a 후 재갱신) | 종결(재갱신 부채) |
| PR-16 ⑦ | 브라우저 e2e 도구 | **판정: 미채택**, 3층 증거(ADR-0086) | 유지 — 트리거 갱신(실브라우저 전용 회귀 2 PR 연속 또는 P4 착수) |
| PR-16 ⑨ / P-39 | Idempotency-Key 길이 | 무접촉 | 이월(코어 소규모) |
| PR-16 ①②④⑧ | 잡 결과 저장·잡 화면·게이트 문구·PI 선수금 기준 | 무접촉 | 이월(P6·프런트 정비·S3-3) |
| **PR-9a ①** | 승인 무결성 대사 배선(`P:461`) | **구현**(N-02, PR-6, ADR-0087) | 종결(deferred 트리거는 미채택 유지) |
| 부채 #15 마일스톤 몫(`P:709`) | item_profiles 마일스톤 세트 | **구현**(PR-4a/4b + 쓰기 경로 N-01) | 종결 |
| 관찰 [S1-2](`P:734`) | OEM 생산 마일스톤 프로파일 | P-06과 함께 판정 | 종결(판정) |
| P-10 | L/C 닫힘 | 유지(산식만, 플래그 공급 0) | 이월 S3-3 |
| P-13 | documents owner_type 확폭 | 선적 첨부 미개방(무접촉) | 이월 S3-3·S6-1 |
| P-21 | AllocationPort 실구현 | "자리" = 응답 필드(X-26) | 이월 S4-2 |
| P-22 | fx_rates | 재확인만(선적 환율 = 원천 사본) | 이월 |
| P-43 | 동일 SKU 분할 납기 | 상호작용 없음 확인(분할은 선적 단위) | 이월 |
| P-46 | 브리핑 줄 | 선적 줄 미편입 | 이월 + 신규 부채(브리핑 수신자) |
| P-56 | SKU 용량 | 비해당 | 이월 |
| 기타 P-08·09·11·12·14~20·23~42·44·45·47~53·55·59 | — | 무접촉 | 소유·트리거 원문 그대로 이월 |

### 6.2 S3-2 신규 부채 후보 (부록 합집합 — PROGRESS 등재 시 소유·트리거 병기)

| # | 부채 | 트리거 | 출처 |
|---|---|---|---|
| 1 | 다중 SO 합적(N:M) | 합적 실수요 | sA |
| 2 | 원천 라인 소속 DB 강제(복합 FK) | 소속 불일치 사고 1건 | sA |
| 3 | 채널입고·샘플무상 생성 경로 | S4-3·S5-2·무상 SO 판정 | sA |
| 4 | SO COMPLETED 엣지·short-close | S3-3 provider PR | sA·sC |
| 5 | 중량·CBM·박스 열 | S3-3 PL | sA |
| 6 | 비거래처 수하인("TO ORDER") | S3-3 L/C | sA |
| 7 | 주말 판정(국가별 주말 규칙) | 사용자 요구 또는 S4-4 캘린더 | sB |
| 8 | 대금만기·제시기한 알림 | S3-3 receivables·lc_terms | sB |
| 9 | OEM 마일스톤 알림 | 사용자 요구 | sB |
| 10 | 납기(`requested_delivery_date`) 대비 ETD/ETA 비교 규칙 | 사용자 요구(Incoterms별 의미 판정) | sB |
| 11 | 브리핑 수신자 확장(무역·물류 담당) | 사용자 요구 | sB |
| 12 | 계정 생성 화면·API | 운영 개시 후 계정 개설 2건↑ 또는 요청 | sD |
| 13 | 수입선적 문서 흐름 노드(PO 미편입) | PO를 문서 흐름에 넣는 세션(S4-1) | sD |
| 14 | 보드 카드 선적 요약(건수·다음 ETD) | 사용자 요구 | sD |
| 15 | `.shard_durations.json` 재갱신 | 샤드 소요 편차 2배 초과 | sE |
| 16 | 3a 동시성 파일 분할 | 단일 파일 10분 초과 | sE |
| 17 | S3-2~S3-4 DG 선적 수동 점검 공백 | S3-4 체크리스트 | sD §D9 |
| 18 | 통관 이슈 임시 규칙 = runbook 안내뿐(코드 0) | 통관 이슈 사고 또는 S5-3 | sE §E5-1 |
| 19 | **출발국 휴일 경고(ETD·Cargo Closing·서류마감)** — `D:203` 문면 밖이라 S3-2는 ETA만 | 사용자 요구 또는 S4-4 캘린더 뷰 | 적대 R-09 |

---

## 7. 자동화 4금·§15 재점검 (설계 전체)

| 금지 | 판정 | 기계 고정 |
|---|---|---|
| 지출·발주 확정 | PO 상태·잔량 무변경, PO 자동 엣지 0 | `test_doc_machines` PO 단언 유지, sA §A16 A(PO `open_quantity` 불변) |
| 법적 판정 | 통관 = 사실 열만(세율·HS 0), 휴일 = 경고 | 스키마(열 부재)·sB §B12 |
| 대외 최초 발송 | 통보 = 기록(발송 코드 0), 알림 = 인앱 | I: M6 경로 아웃바운드 임포트 0 |
| 장부 확정 | 출고 이후 RESERVED, stock_movements 무접촉 | B: RESERVED 진입 0 |
| 자동 확정 부재 | SO 자동 엣지는 CONFIRMED↔IN_SHIPMENT(약정 진입 아님)뿐, 선적 생성·초안 = 사람 1클릭, 잡 2개 = 읽기·알림만 | `test_no_auto_confirm_code_path_exists` 엔트리 4종(sC §C12)·SO 자동 엣지 정확히 2·RECEIVED→CONFIRMED 자동 0 |

fail-visible 재점검(평가 불능 ≠ 통과): 앵커 미확정·INVOICE_DATE·L/C 조건·수리 전 = UNKNOWN(사유 코드), 휴일 미선언 = UNVERIFIED, 가용재고 = 미산정 배지, 영문명 결측 = 422 — 0·오늘·평일로 대체하는 경로 없음.

---

## 8. 첫 커밋 실행 확인 항목 (정적 독해 한계 — 실행 검증 못 했음)

1. `alembic heads` = `f2cb6020b2bb` 단일(PR-2a).
2. SO 상태·상태이력 CHECK가 엣지 추가만으로 재생성 불요(PR-3a).
3. `lock_lines_for_consumption`의 헤더 소속 필터(X-08) 실측(PR-3a).
4. M14·M15 식별자 63자(`ck_milestones_customs_actual_from_records` 등 — 넘으면 이름 단축).
5. `comm_logs` 주제 CHECK 실제 제약명·downgrade 실패 동작·범용 경로 SHIPMENT 거부(PR-4a).
6. 에스컬레이션 prefix·담당 이관 키 재작성과 신규 dedup 키 형식 호환(PR-6, X-25).
7. `check_integrity` 전건 순회 시간(승인 행 수 × 이벤트) — 잡 timeout 여유(PR-6).
8. `test_no_auto_confirm_code_path_exists.py:759-795` 갱신 시점(3a에서 trade_chain 선적 파일이 scheduler·cli 임포트 대상이 아님을 확인).
9. 신규 테스트 파일의 샤드 배정 편중(3a 첫 CI).
10. 프로덕션 실데이터 유무(되돌리기 비용 전제 — PR-1 착수 시).
11. **[적대 R-17]** 기존 DB 승인 전건 대사 "불일치 0" 실측(PR-1b 첫 커밋 — 1건↑이면 알림 형식 확정 전에 PROGRESS에 원인 기록).
12. **[적대 R-23]** SO 자동 엣지 추가 후 `SalesOrderTarget` assert(`code:modules/trade_chain/router.py:95-96`) 무변경 통과(3a).

---

## 9. 적대 검토 정정 (2026-10-04 — 3렌즈 spec-fidelity·safety·feasibility, **이 절이 §0~§8과 부록 A~E보다 우선**)

> 지적마다 원문(파일:줄·코드)을 먼저 확인했고 실재하면 더 엄격한(fail-closed) 쪽으로 자율 확정했다. 반려 0건. 지적 번호(`SF`·`SA`·`FE`)와 판정 표는 계획서 §9. **실행 검증 못 했음**(정적 독해 — 코드 줄은 `a4d91c0`).

| R | 출처 | 원문 확인 | 결정(자율 확정) | 번복 비용 |
|---|---|---|---|---|
| **R-01** | SF-1 | 취소 가드 = 통관만(§2.6 `CUSTOMS_RECORD_ALIVE`), 실적은 상태 독립(sA §A7-1) → PLANNED 선적에 ETD 실적 입력 후 취소하면 SO 잔량 복원(`D:419` A "후속 생존 시 선행 취소 차단" 위반) | ① **ETD·BL_ISSUED·ETA 실적은 RELEASE_ORDERED에서만** 받는다(PLANNED = 422 `SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE`). ② 그 실적이 하나라도 살아 있으면 선적 취소 **409 `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`**(`detail.milestone_types`). 탈출로 = 실적을 사유와 함께 정정·삭제(이력 남음) 후 취소. ③ 라인 수정·삭제는 RELEASE_ORDERED에서 이미 `TRADE_DOCS.DOCUMENT.FROZEN`(`EDITABLE_STATES={PLANNED}`)이라 ①로 자동 차단. ETA를 포함한 이유: 도착 실적은 출항을 함의. PR-4a(마일스톤 표가 M15) — 3a 구간엔 실적 입력 경로가 없어 공백 없음. GC-A14·J 케이스 가산 | 낮음(가드 제거) |
| **R-02** | SA-2·FE-1 | `code:modules/trade_chain/lifecycle.py:342-359` — `SO_CANCELLABLE` 상태 검사가 `live_children_numbers`보다 먼저 → IN_SHIPMENT SO 취소 = `TRANSITION.NOT_ALLOWED`. 커널 원칙(`code:modules/trade_docs/transition.py:136-146`)과 어긋남 | PR-3a에서 `_cancel_sales_order`의 **후속 생존 검사를 상태 검사 앞으로** 옮긴다. 기대 = **409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`, `detail.successors=[SH-…]`**(GC-A14·J-04에 명시). IN_SHIPMENT의 ON_HOLD 요청은 엣지가 없으므로 `TRANSITION.NOT_ALLOWED` 유지(보류는 역순 취소가 아님 — 화면은 IN_SHIPMENT에서 보류 버튼 비노출). 변이 "검사 순서 원복"이 A14에서 실패해야 함 | 낮음(검사 순서) |
| **R-03** | SF-2 | `D:203` 9종. 계획·sB B1·ADR-0080·§4가 11개를 나열하며 "10종" | 전 문서 **"9→11종(저장 8: +BL_ISSUED / 파생 3: +PRESENTATION_DEADLINE 편입)"**. L/C 제시기한은 §7.5에서 "자동 계산" 항목이지 마일스톤 목록이 아니므로 **종류 편입 = 문면 확장**으로 보고 항목·ADR-0080에 명기 | 낮음(문서) |
| **R-04** | SA-9 | §2.1(b) `quantity::bigint * unit_price_amount` ↔ S3-1 numeric 곱 규약(`code:modules/trade_docs/mixins.py:270-285`) | `ck_shipment_lines_export_priced`: `so_line_id IS NULL OR quantity::numeric * unit_price_amount = line_amount`. 수출 라인은 SO 라인 무상 규약 승계: `ck_shipment_lines_export_free_iff_zero_price`(`so_line_id IS NULL OR is_free = (unit_price_amount = 0)`). 수입 라인은 `import_no_price`(단가 NULL·금액 0·`is_free=false`) | 낮음(CHECK) |
| **R-05** | SA-3·SA-5·FE-14 | `test:integration/test_collaboration_constraints.py:151-156`(`COMM_SUBJECT_TYPES == ("CERTIFICATION",)`), `test:e2e/test_collaboration.py:505-517`(SHIPMENT POST 422), 범용 목록 `code:modules/collaboration/service.py:681-711` 주제 필터 없음·라벨 `:392-397,448-453` CERT만, documents `COMM_LOG` 첨부 `code:modules/documents/service.py:276-282,344` | ① 범용 `SubjectType` Literal(`code:modules/collaboration/schemas.py:25`)은 **`{CERTIFICATION}` 유지** → POST·목록 필터의 SHIPMENT = 스키마 **422 고정**(N-03 "또는 404" 해소). ② 범용 목록은 기본 조건 `subject_type IN 범용 허용 주제` — SHIPMENT 행 미노출(선적 통보는 M5·M6로만). ③ id 접근(상세·PATCH·DELETE)의 SHIPMENT 행 = **404**(범용 범위 밖, 부작용 0). ④ documents `COMM_LOG` 소유 첨부는 범용 허용 주제의 comm_log만(SHIPMENT 주제 → 기존 소유자 검증 422). ⑤ 갱신 목록: constraints 테스트를 둘로 분할(DB는 SHIPMENT 허용 / 범용 API는 거부), e2e 422 단언 유지. K 테스트 3행(목록 미노출·id 404·첨부 거부) | 낮음 |
| **R-06** | SF-6 | X-02 `MIN(accepted_on)`은 NULL 무시, 통관 1:N(X-03), 미수리 배지는 통관 표에만(sD:313) | 같은 선적·구분에 미수리 살아 있는 기록 ≥1이면 `MilestoneBoard` 신고수리 행 **`customs_state="PARTIAL"`** + "일부 미수리 n건" 배지(0건 = `CLEARED`, 기록 없음 = `NONE`). 유효 실적 값·적재기한 산식은 MIN 유지(fail-closed). GC-A18(B20 행 33)·PR-4b vitest | 낮음 |
| **R-07** | SF-4 | `D:419-429`: B=서류(`:420`), E=비용·소싱(`:423`), G=AI·보안(`:425`) | 계획 §1·§4·sE E6-1 그룹 이름 정정. E = **해당 없음**(비용 코어 S3-4, `W:124-128`). "stock_movements 무접촉" = **K 아키텍처 단언**. B 근거 = §20 B "검수 미완료 선적의 CI·PL 생성 차단"의 전제(RESERVED 진입 0). G = 이름은 DESIGN대로, 내용은 S3-1 부기 `D:417` ① 승계 | 없음 |
| **R-08** | SA-4·SA-8·SF-8 | `lock_chain`은 조상을 무조건 `FOR UPDATE`(`code:modules/trade_chain/chain_ops.py:50-65`), X-10은 PO SHARE, sC T5는 "SO(또는 PO) FOR UPDATE", T8·T9에 partners 순서 없음(sA:417 관세사 KEY SHARE) | **경로별 확정**: T1 SO `FOR UPDATE` 선점 / **T2만 PO `FOR SHARE`** / **T4·T5는 `lock_chain` 그대로(SO·PO 모두 `FOR UPDATE`, 승격 없음)** / **T8·T9 = 멱등 → partners `FOR KEY SHARE`(id 순) → shipments → shipment_children**. J-07 LOCK_ORDER 계측 대상에 T8·T9 명시. ANCESTORS에 모드를 싣는 안은 기각(헬퍼 확장 대비 실익 없음 — 교착 무관) | 낮음 |
| **R-09** | SF-9 | `D:203` "ETA 현지 연휴 경고"만. sB B12 ③은 ETD·Cargo Closing·서류마감(출발국)까지 확장 | **ETA(도착국)만** 판정·표시(문면 충실). 출발국 확장은 **부채 19**(트리거: 사용자 요구 또는 S4-4 캘린더). 수입선적 출발국 미선언 UNVERIFIED 배지 대량 발생으로 경고 신호가 묻히는 것도 피한다. ADR-0082에 명기 | 낮음(함수·화면) |
| **R-10** | SF-10 | sB B7·B13 MET 판정 = ETD 실적, `D:203`은 적재 사실 원천 미정 | **이행일 = ETD·BL_ISSUED 실적 중 존재값의 MAX**(둘 다 있으면 늦은 쪽 → MET_LATE를 놓치지 않음). **가정**으로 ADR-0080에 근거·대안(BL 단독 = B/L 지연 시 결측, ETD 단독 = 본선적재일 불일치 누락)·번복 비용 기재. 스캔 충족 신호도 동일(B13). GC B20 행 34 | 낮음(함수 1개) |
| **R-11** | SF-11 | sB B1 "수출 + LC" ↔ `D:203` 구분 없음 | **수출·수입 LC 공통**(수입 L/C도 기한 추적 가치 — 운영 경로는 어차피 `LC_TERMS_NOT_REGISTERED` UNKNOWN) | 낮음 |
| **R-12** | SF-3·SF-12 | GC v1 `kbeauty-golden-cases-v1.md:91` C = 인증·규제, `:174` G = 수입원가, 삭제 금지 `:226`. L/C 만기 양성 분기(B20 09·10)가 제시기한 케이스(C12)에 섞임 | GC v1.5 **10건**: A14·A15·**A16~A21**(구 C11~C16)·F4·**G3**(A15의 원가 키 0 단언 분리). A16에 "L/C SIGHT = 네고일 / USANCE = 인수일+N / 결측 UNKNOWN"(행 09·10 이동) + 수입 ORDER_DATE(행 32), A17은 행 11~13·27·28. `golden` 대사 43+10 = **53 이상**. **PR-1 등재 전 확정**(등재 후 재배치 불가) | 낮음(등재 전) / 등재 후 DEPRECATED만 |
| **R-13** | SF-5 | 계획 §1 "문면과 다르게" 4건(WBS 대비)만, DESIGN 대비 3건이 §2 표에 묻힘. §4 부기가 §8.3 ②를 "해석"이라 부름 | 계획 §1을 **WBS 대비 4 + DESIGN 대비 4**(§8.3 ② 잠금 모드 문면 변경·§15 SO 자동 엣지·§7.5 마일스톤 편입 2종·§2 LOGISTICS 쓰기)로 전량 등재. 계획 §5-7에 SO 자동 엣지 번복 비용(중간)·오너 확인 3순위 | 없음 |
| **R-14** | SF-14·SA-10 | N-01 쓰기 역할 = item_profiles 선례 `CAN_EDIT=(CERT,)`만 인용, `D:37`은 인증 편집을 "시장·요건 템플릿"으로 한정. `GOVERNED_PREFIXES`에 `/api/v1/item-profiles` 없음(`test:architecture/authz_matrix.py:25-44`, 완전성 검사 `test_authz_matrix.py:43`) | 마일스톤 세트 쓰기 = **ADMIN 전용**(좁은 쪽). `/api/v1/item-profiles/{profile_id}/milestone-types`를 **`GOVERNED_PREFIXES`에 등재**(PR-4c) — 행 누락 시 완전성 테스트 실패. ADR-0079·0085에 CERT 미배정 사유·번복 비용(행 1줄, 낮음) | 낮음 |
| **R-15** | SF-15·SA-참고·FE-12 | sA:83 "ORIGIN(발급 KST 날짜)" ↔ §2.1(a) "원천 헤더에서 복사" 묶음. `fx_date_not_future`(`mixins.py:186-191`)는 두 해석 모두 통과 → 테스트로 안 드러남 | `doc_date` = **생성 시 `today_kst()` 1회 설정(시스템 산출, 원천 복사 아님), 이후 불변(FIELD_POLICY ORIGIN)**. K 테스트: 원천 SO `doc_date`와 다른 날짜로 생성 시 선적 `doc_date == today_kst()` | 낮음 |
| **R-16** | FE-5 | sE §E1 대안 (a)가 "쓰기 경로 없는 표"를 기각 사유로 썼는데 M14가 통관 표를 PR-4a API보다 한 PR 먼저 만듦. sE E3-4 근거("동TX 복사라 백필")는 X-02로 소멸 | **`customs_records`를 M15로 이동**, `CUSTOMS_RECORD_ALIVE` 가드도 **PR-4a**. 3a 구간엔 통관 행이 0이라 가드 공백 없음, 3a 면적 감소. §2.12·계획 §3·§4·ADR-0074 정정 | 낮음(병합 전) |
| **R-17** | SA-11·FE-7·SF-7 | `P:461` PR-9a 부채 ① 트리거 "S3-2 이전" 도과, 잡은 의존 0인데 PR-6(뒤에서 두 번째)에 묶임. 불일치 1건이면 매일 FAILED(§2.8) — `check_integrity`는 읽기 전용(`code:modules/approvals/integrity.py:1-7`)이라 정정 수단이 없어 알림 피로·실행 실패와 구분 불가. 부록 다수가 잡 총수 13 | ① **PR-1b 신설**(PR-1 직후): 무결성 잡만, JOB **12→13**(PR-6이 13→14). ② **첫 커밋에서 기존 DB 전건 대사 실측**(불일치 0 기록, 1건↑이면 원인 PROGRESS 기록). ③ 불일치 = 관리자 알림 dedup 키 **`approval-integrity:{approval_id}:{problem}`**(일자 제외 — 같은 문제는 1회, 문제가 바뀌면 새 알림, 미해소 알림은 받은편지함에 남음), **잡 FAILED = 실행 예외만**(검토 제안 "일자 포함"보다 피로가 적고, 문제별로는 동일하게 fail-visible). ④ 부록의 "13"은 PR-1b 직후 중간값 — 최종 핀 14(표지 R-27). 계획 §5-6 비용 재기재, 오너 확인 2순위 유지 | 낮음(`enabled=false`·키 형식은 신규 알림부터) |
| **R-18** | FE-4·FE-11 | §2.1(e) CHECK는 `accept_after_declare`·형식뿐, `accepted_on`이 적재기한 유일 원천(X-02). sB B8 ④ "+1일"은 날짜형에만 성립, M3는 `actual_at+tz`도 받음 | ① 통관 `declared_on`·`accepted_on` **≤ `today_kst()`**(한국 세관 — 여유 0), 422 **`SHIPMENTS.CUSTOMS.DATE_IN_FUTURE`**. ② 시각형 실적 **`actual_at ≤ now_utc`**(여유 0), 기존 `ACTUAL_IN_FUTURE` 재사용. GC B20 행 35·37, 변이 "미래 수리일 허용" | 낮음 |
| **R-19** | FE-2 | sD D6:283 "M2 → 응답의 change_id로 M6" ↔ M2·M3 응답 `MilestoneBoard`에 `change_id` 없음 | M2·M3 응답 = **`{board: MilestoneBoard, change: {id, change_kind} \| null}`**(이력 행이 안 생기는 no-op이면 null). 같은 Idempotency-Key 재요청 = 같은 `change.id`(J 테스트). 프런트는 재조회로 추정하지 않는다 | 낮음 |
| **R-20** | FE-3 | sB B3 ④ `min(현지, KST)` 날짜로 `days_left`·`is_overdue` 판정 → LA 10-11T00:00Z 기한을 10-10T21:40Z(KST 10-11 06:40) 스캔 시 "도과" 오표시(최대 ~16h) | D-N 문턱 = `scan_date` 유지(이른 경고). **도과(`is_overdue`·OVERDUE 알림) = 시각형은 `now_utc > effective_at`**, 날짜형은 기존(`today_kst() > 날짜`). GC-A21(B20 행 36), 변이 "시각형 도과를 날짜 비교로" | 낮음(함수 1개) |
| **R-21** | SA-13 | `frontend/src/routes/sales-orders.tsx:17` `SO_STATUS_FILTERS` = RECEIVED·CONFIRMED·ON_HOLD·CANCELLED — 첫 선적 후 SO가 필터로 안 잡힘 | PR-3b: 필터에 `IN_SHIPMENT`("선적중") 추가 + vitest 1행(주석 "예약 상태 4값" → 3값) | 없음 |
| **R-22** | FE-6·FE-10 | PR-3a·4a 검토 면적 최대(sE E7). sE E3-7 "물류 계정을 화면으로 만들어" ↔ sD D14 "계정 생성은 CLI뿐"(`code:cli.py:145`) | **PR 15개**: 1 → **1b** → 2a → 2b → **7** → 3a → **3c**(export.csv S20·문서 흐름 SHIPMENT 노드, 마이그 0) → 3b → 4a → **4c**(OEM T13·M7~M9·마일스톤 세트 쓰기, 마이그 0 — 표는 M15) → 4b → 5a → 5b → 6 → 8. 같은 커밋 묶음은 깨지 않음(3c·4c 항목은 묶음 밖). 계정 = **CLI 생성 → 화면 역할 부여 → ADMIN 회수**, 렌즈 11 근거 = "역할 부여 단계의 개발자 개입 0". PR-7을 3a 앞으로(3b·4b의 L 관통에서 API 직접 호출 0) | 낮음(병합 전 순서) |
| **R-23** | SA-1·SA-7·FE-9 | 모델 CHECK가 `DOC_PREFIXES[kind]`·`STATUSES[kind]`를 읽음(`code:modules/trade_docs/mixins.py:178-181`), 엣지 0 편입은 `test:architecture/test_doc_machines.py:73-87` 도달성 실패. `public_transition_targets`는 사람 엣지만(`code:modules/trade_docs/machine.py:195-207`) → SO 자동 2엣지는 `SalesOrderTarget`(`router.py:95-96`)·`test_doc_machines.py:131-136` 불변 | PR-3a 커밋: ① M14(선적 4표)+모델+table_policy+DocKind·dict 11종·FIELD_POLICY·**선적 상태 기계(사람 엣지 3)**·사슬 레지스트리 = 한 커밋 ② SO 자동 엣지 2+RESERVED 축소+보드 매핑(SO Literal **무변경** — assert 통과 확인) ③ R-02 ④ 서비스·라우터·authz ⑤ 동시성. "SO 엣지 ↔ Literal" 묶음·변이 대상에서 제거, 신설 `ShipmentTarget=Literal["CANCELLED"]`+assert만 | 없음 |
| **R-24** | SA-12 | §2.1(k) 연도·국가 일치 = 서비스+테스트만, `uq_holidays_country_day_live`는 `holidays.country_code` 기준 → 서비스 결함 1건이 UNVERIFIED/CLEAR 판정을 조용히 틀어지게 함 | M13: `holiday_calendar_years` UNIQUE(id, country_code, year) / `holidays.year` SMALLINT NOT NULL + 복합 FK `(calendar_year_id, country_code, year)` → `holiday_calendar_years(id, country_code, year)` + CHECK `extract(year from holiday_on) = year`. 위반 → 422 `HOLIDAYS.CALENDAR.YEAR_MISMATCH`(제약명 번역). 63자 실측 | 낮음(M13 병합 전) |
| **R-25** | FE-13 | 시각형 카드: D-N = `min(현지,KST)` 날짜, 휴일 = 현지 날짜 → 동쪽 시간대에서 두 날짜 상이 | `MilestoneBoard` 행에 **`scan_date`·`local_date`** 명시 필드. 화면 문구 "D-3(기준일 10-10)"·"현지 10-11" 구분. R-09로 휴일 판정은 ETA(날짜형)만이라 시각형 휴일 혼재는 현재 0이나, 필드는 확장 대비로 둔다 | 낮음 |
| **R-26** | FE-14 + R-01·R-18 | §2.6에 마일스톤 세트 중복·비적용 종류, `accept_after_declare` 번역 코드 없음, N-03 "422 또는 404" 미결 | **최종 32종**(SHIPMENTS 26·HOLIDAYS 6) — 신설 4: `SHIPMENTS.SHIPMENT.ACTUAL_RECORDED`(409)·`SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE`(422)·`SHIPMENTS.CUSTOMS.DATE_IN_FUTURE`(422)·`SHIPMENTS.CUSTOMS.ACCEPT_BEFORE_DECLARE`(422). 재사용: 세트 부분 유니크 위반 → 409 `SHIPMENTS.MILESTONE.DUPLICATE_TYPE`, 세트에 파생·OEM 종류 → 422 `SHIPMENTS.MILESTONE.TYPE_NOT_APPLICABLE`. N-03 = R-05(POST 422 고정, id 접근 404). 전부 카탈로그 1:1 테스트 대상 | 낮음 |
| **R-27** | SA-6·FE-8·SF-7·SF-8 | 통합에 진 부록 문면이 표지 없이 잔존 | 부록 머리에 정정 색인 1줄 + 아래 자리에 "[적대 R-nn]"/"[통합 X-nn]" 표지·취소선: **sA** §A2 `doc_date`(R-15)·§A6 SO 취소 코드(R-02)·ANCESTORS 잠금 모드(R-08)·§A7-1 실적 독립·취소 부수 처리(R-01)·§A7 Literal 갱신(R-23)·§A8 관세사 KEY SHARE·CHECK(R-08·R-18·R-26)·§10-4(R-13) / **sB** B1 확장 계수·제시기한 적용·휴일 열(R-03·R-11·R-09)·B3 ④(R-20·R-25)·B4 ORDER_DATE(R-29)·B7 이행일·부분 수리(R-10·R-06)·B8 ④(R-18·R-01)·B12 ③(R-09)·B13 충족 신호·총수(R-10·R-17)·B19 잡 표·문서(R-17·R-03)·B20 레지스트리 13·신규 행 32~37·요약 ①(R-03) / **sC** T1·T4·T5·T8·T9(X-15·R-08)·수렴 판정(X-13)·authz `POST /shipments`(§2.9)·`require_roles(T,L)` 문단(X-15 철회)·C11 총수·I-02·C11 요약(R-17)·갱신 목록(R-05) / **sD** §0-6 dedup(X-25)·S7·S8 `NOT_EDITABLE`(X-20)·S17·S18 코드(R-18·R-26)·M2·M3·M4 응답·코드(R-19·N-05·R-18·R-01)·D3 `scan_date`·`local_date`·`customs_state`·`is_overdue`(R-25·R-06·R-20)·D6 change_id·휴일 배지(R-19·R-09)·D7 자동 행 역할·K행 역할(X-14·X-16·R-14)·보고 ①(X-02) / **sE** E1 묶음(R-23)·E3 제목·E3-0 PR-1·2a·3a·4a·6·8 행·선행 관계(R-22·R-12·R-16·R-17)·E3-1 ADR 범위·§7.5·§15(N-02·R-03·R-17)·E3-3 커밋 순서·통관 가드·AllocationPort(R-23·R-16·X-26)·E3-4 근거(X-02·R-16)·E3-6(R-17·R-12)·E3-7 계정 경로(R-22)·E4 ADR-0080·0084·0058 부기(R-03·R-17)·E5 매핑·이력·GC 표(R-12)·E6-1 그룹(R-07)·E6-3 변이(R-30)·E8 렌즈 8·9·10·보고 ① | 없음 |
| **R-28** | SF-13 | 계획 PR-4b vitest에 휴일 배지 검증 없음 — DoD② 실체는 사용자가 보는 경고(`W:115`, 렌즈 11) | PR-4b vitest: ETA 도착국 휴일 → 경고 배지+휴일 이름 / 미선언 → "휴일 캘린더 미등록 — 확인 불가" 배지 / CLEAR → 배지 0. GC-A19에 4b(화면 배지) 배치 | 없음 |
| **R-29** | FE-15 | sB B4 수입 ORDER_DATE = "PO 발행일(KST 날짜)" — `doc_date`/`frozen_at` 모호. PO는 생성=발행, `frozen_at NOT NULL DEFAULT now()`(`code:modules/purchase_orders/models.py:4,84`) | 수입 ORDER_DATE = **PO `frozen_at`의 KST 날짜**(수출 SO `confirmed_at` KST 날짜와 대칭, 사람 입력 `doc_date`는 쓰지 않음). GC-A16(B20 행 32) | 낮음 |
| **R-30** | 변이 보강 | 위 결정들이 변이로 고정되지 않으면 조용한 약화 위험(R5) | sE E6-3 최소 목록 가산 — 3a: SO 취소 검사 순서 원복 / 4a: 실적 생존 취소 가드 제거·PLANNED 실적 허용·미래 수리일 허용·PARTIAL→CLEARED·범용 목록 SHIPMENT 노출·M2 응답 `change` 누락 / 6: 시각형 도과 날짜 비교 / 1b: 불일치 시 알림 누락·예외를 SUCCESS로 / 2a: 휴일 연도 CHECK 제거·복합 FK 제거 / 4c: `GOVERNED_PREFIXES`에서 milestone-types 제거 | 없음 |

**갱신된 PR 목록(최종, 병합 순서)**: PR-1 → PR-1b → PR-2a → PR-2b → PR-7 → PR-3a → PR-3c → PR-3b → PR-4a → PR-4c → PR-4b → PR-5a → PR-5b → PR-6 → PR-8(15개). 마이그레이션은 여전히 3건(M13 = 2a, M14 = 3a, M15 = 4a).
