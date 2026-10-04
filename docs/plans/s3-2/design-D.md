# S3-2 계획서 부록 D — 화면·API 계약 (선적·마일스톤·통관·휴일·보드 연결·셸·PR-16 부채 ③⑦)


> **통합 우선순위(2026-10-04)**: 이 부록과 `design-integrated.md`가 충돌하면 통합 문서가 이긴다. 통합 검토가 모순 해소에 필요한 최소 문면만 고쳤고, 고친 자리는 "[통합 X-nn]"·"[통합 N-nn]"으로 표시했다(목록: 통합 §1.6).
> **적대 검토 정정(2026-10-04)**: 통합 문서 §9(R-01~R-30)가 이 부록과 통합 §0~§8보다 우선한다. 이 부록에서 고친 자리는 "[적대 R-nn]"으로 표시했다(목록: 통합 §9 R-27).
- 기준: main `a4d91c0`(S3-1 종결). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-2 행(`W:112-116`)을 따른다. 진행·부채는 PROGRESS.md 'S3-1 종결 부채 최종 목록'(`P:32`)과 '## 현재'다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `code:경로:줄` = `backend/app/` 아래 경로, `fe:경로:줄` = `frontend/src/` 아래 경로, `sA:절`·`sB:절` = 같은 디렉터리의 `design-A.md`(선적 데이터 모델)·`design-B.md`(기일 엔진·마일스톤·휴일).
- 판정 방식: 오너 지시(2026-09-29, CLAUDE.md "웹 세션 판정 절차 생략")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. PROGRESS 등재 시 "자율 확정"으로 표기한다. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 여부 / 되돌리기 비용** 순서다.
- **실행 검증 못 했음.** 문서와 코드를 정적으로 읽기만 했다. pytest·vitest·서버·브라우저는 돌리지 않았다. 코드 줄 번호는 `a4d91c0`에서 grep·sed로 확인했다.
- 안건 번호는 D1~D16이다.

---

## 0. 경계 — 이 부록이 정하는 것과 넘기는 것

| 이 부록(D)이 정한다 | 다른 부록으로 넘긴다(경계만 적음) |
|---|---|
| 엔드포인트 목록(경로·메서드·요청/응답 스키마 골격·페이지네이션·에러 코드·역할 행) | 테이블·컬럼·CHECK·상태 머신·잠금 순서·채번 → **sA** |
| 선적 목록·생성(SO/PO 참조 2단)·상세(헤더·라인·당사자·마일스톤 타임라인·휴일 경고·통관·롤오버·상태 이력·문서 흐름) 화면 | 마일스톤 열거·계획/실적 저장·파생 산식·휴일 3값·스캔 잡 → **sB** |
| 실적 입력·롤오버(계획 변경)·통보 기록·통관 기록 대화상자 | 서버 측 재계산 규율·이력 불변·dedup 키 → **sB §B8·B9·B13** |
| 오더 보드 `SO_IN_SHIPMENT` 열 UI, SO·PO 상세의 선적 연결, 문서 흐름 SHIPMENT 노드 | 보드 상수 매핑의 데이터 측(완전성 테스트) → **sA §A7-4 하단**(열 가산 권장) |
| 셸 메뉴·알림 이동 매핑·상태 라벨 단일 표·현지 시각 병기 함수 | 알림 생성·라우팅(담당자→규칙→ADMIN) → **sB §B13** |
| 한국어 UI·390px·접근성 규율의 S3-2 적용 | 휴일 데이터 모델·근거 2필드 → **sB §B11** |
| PR-16 부채 ③(사용자 역할 화면)·⑤(SearchSelect 오선택 — 새 소비처가 생겨 판정)·⑦(브라우저 e2e 도구) 판정 | 권한 행의 근거(LOGISTICS 첫 쓰기 허용)·담당 이관 등록 → **sA §A11** |

**이 부록이 다른 부록에 기대는 가정(통합 검토가 맞춘다)**
1. 선적은 `DocKind.SHIPMENT`로 커널 편입, 접두어 `SH`, 상태 8값 중 활성은 `PLANNED`·`RELEASE_ORDERED`·`CANCELLED`뿐이다(sA §A1·A7-1).
2. 생성 원천은 수출=SO 1건, 수입=PO 1건이고 채널입고·샘플무상은 DB CHECK로 닫혀 있다(sA §A2).
3. 수입선적 라인·헤더에 금액이 없다(`ck_shipments_import_has_no_amount`, sA §A2·A3).
4. 마일스톤은 저장형 8종 + 파생형 3종, 파생값은 비저장 계산값이다(sB §B1·B8).
5. **[통합 X-02 확정]** 수리일 입력처 = 통관 기록 `accepted_on` 하나이고, 마일스톤 실적으로 **복사하지 않고 읽기 시 파생**(구분 일치·살아 있는 통관 기록 `MIN`)한다 — 아래 화면 가정(실적 버튼 대신 통관 기록 안내, `input_source="CUSTOMS_RECORD"`)은 그대로 성립한다. 원문: **수리일의 입력처는 하나다.** sA §A8은 `customs_records.accepted_on`을 적재의무 원천으로, sB §B7은 마일스톤 `CUSTOMS_CLEARED` 실적을 단일 원천으로 적었다. sB §B7 (ii)안("통관 기록 열을 유일 입력처로 하고, 마일스톤 실적은 같은 트랜잭션에서 복사·갱신만")이 두 부록을 동시에 만족하므로 **화면은 (ii)를 가정한다**: `CUSTOMS_CLEARED`의 **실적** 입력 버튼은 마일스톤 행에 두지 않고 통관 기록으로 안내한다(계획 입력은 마일스톤 행에서 허용). 통합이 다르게 정하면 D6의 버튼 1개만 바뀐다.
6. 선적 기일 알림의 `entity_type`은 **소유 전표**(`shipments`, OEM은 `purchase_orders`)이고 `entity_id`는 소유 전표 id다. dedup 키(~~`deadline:milestones:{id}:…`~~ **[통합 X-25]** `deadline:shipments:{shipment_id}:{TYPE}/…`, sB §B13)는 그대로 두고 알림 대상만 소유 전표로 둔다. 이렇게 해야 알림 이동 표(`fe:lib/alert-routes.ts:6-15`)가 행 1줄로 끝나고 "마일스톤 id → 선적" 해석 라우트가 필요 없다(D11).

---

## D1. API 공통 계약 — S3-1 관례를 그대로 승계한다

**결정**
- **접두어와 등재**: `/api/v1/shipments`, `/api/v1/holidays`를 `tests/architecture/authz_matrix.py` `GOVERNED_PREFIXES`(`:24-45`)에 등재한다. SO·PO 하위 생성 경로(`/sales-orders/{id}/shipments…`, `/purchase-orders/{id}/shipments…`, `/purchase-orders/{id}/milestones…`)는 기존 접두어 아래라 행만 추가한다. 등재가 빠지면 매트릭스가 공회전하므로 PR DoD에 넣는다.
- **목록**: 전부 `Page` 봉투, `page`·`size`(기본 50·최대 200, `code:core/pagination.py`). 함수명 `list_*` → Page 봉투 강제 테스트(`tests/architecture/test_auth_coverage.py:131-170`)의 적용을 받는다.
- **상한이 고정된 내장 집합은 Page가 아니다**: 선적 상세의 마일스톤(종류당 1행, 최대 11행 — sB §B1 + `unique_active(shipment_id, milestone_type)` sB §B2), 당사자(역할당 1행, sA §A5)는 상세 응답에 싣는다. 라인은 SO 상세가 라인을 싣는 선례(`code:modules/sales_orders/router.py:84` "수주 상세 (라인·…포함)")를 따른다. 통관 기록·상태 이력·롤오버 이력은 건수 상한이 없으므로 **별도 Page 엔드포인트**다.
- **쓰기**: 요청 스키마 전부 `extra="forbid"`(`tests/architecture/test_write_schema_forbid.py:114-131`). 생성·동결·전이·실적·계획·초안·통관 생성·통보·휴일 교체는 `Idempotency-Key` 필수(`code:api/deps.py:94-109`), 기존 행 수정은 `version` 필수(불일치 409). 프런트는 `fe:lib/api.ts`가 키를 자동 부착하므로 **대화상자 1회 열림 = 키 1개**(재시도는 같은 키, 본문이 바뀌면 새 키 — S3-1 확정 버튼 규칙 승계).
- **오류 순서**: 401→403→404→409→422, 역할 검사가 존재 검사보다 앞선다(`D:370` [M4] 보강 §18.1). 부모-자식 불일치(다른 선적의 라인·당사자·통관·마일스톤 id)는 404이고 부작용 0이다.
- **에러 봉투**: `{error:{code,message,detail,request_id}}`, 코드 `<도메인>.<대상>.<사유>`, 사용자 detail과 log_context 분리(`D:374` §18.3). detail에 금액을 싣지 않는다(sA §A13 규칙).
- **금액·시각 직렬화**: 금액은 서버 문자열(`*_text`)만 화면에 쓰고 프런트 산술 0(`fe:components/document-flow-panel.tsx:4` 선례). 시각은 UTC ISO, **날짜형 마일스톤은 `YYYY-MM-DD` 문자열(현지 달력일, 시간대 없음)**, D-N·도과·휴일 판정은 **서버 계산값**으로 내려 준다(프런트 날짜 산술 0 — "오늘"은 `today_kst()` 하나, sB §B3 ③).

**근거**: `D:376`(§18.4 페이지네이션 50·N+1 금지), `D:350`·`D:352`(멱등), `D:342`(version 409), `D:368`·`D:370`(인가 3축), `D:374`(에러 봉투), `D:26`(UTC 저장·KST 표시·현지 병기).

**대안(기각)**
- (a) 마일스톤도 Page 엔드포인트로만: 상세 타임라인이 요청 2회가 되고, 상한이 DB 유니크로 고정된 집합이라 페이지네이션 이득이 없다.
- (b) D-N을 프런트에서 계산: 브라우저 시간대·`new Date("YYYY-MM-DD")`의 UTC 해석으로 하루 밀림이 생긴다(PR-16 결함 ① 계보, `P:16`).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(스키마 필드 추가·이동).

---

## D2. 엔드포인트 목록

> 역할 약어: A=ADMIN, T=TRADE, L=LOGISTICS, C=CERT, V=VIEWER. "전"=5역할 전부. ADMIN은 `require_roles`에서 상시 통과한다(기존 관례).
> 멱등 = `Idempotency-Key` 필수. ver = 요청 본문 `version` 필수.

### D2-1. 선적 (sA 소관 데이터 — 경로·스키마만 여기서 고정)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 주요 에러 |
|---|---|---|---|---|---|---|
| S1 | `GET /shipments` | 전 | — | `status?`(다중), `shipment_kind?`, `so_id?`, `po_id?`, `assignee_id?`, `q?`(선적번호·거래 상대명 부분 일치, ≤100자), `page`,`size` | `Page[ShipmentListItem]` | 422 파라미터 |
| S2 | `GET /shipments/{id}` | 전 | — | — | `ShipmentDetail`(D3) | 404 |
| S3 | `POST /sales-orders/{so_id}/shipments/preview` | A·T [통합 X-14] | — (비저장, 채번·이벤트·키 소비 0) | `ShipmentCreateFromSo` | `ShipmentPreview` | 409 `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`·`DOCUMENT_NOT_CONSUMABLE`, 422 `SHIPMENTS.SOURCE.LINE_MISMATCH` |
| S4 | `POST /sales-orders/{so_id}/shipments` | A·T [통합 X-14] | 멱등 | `ShipmentCreateFromSo` = `{lines:[{so_line_id, quantity}](≥1), origin_country_code, dest_country_code, parties?:[{role, partner_id}], internal_note?}` | 201 `ShipmentDetail` | S3과 같음 + 409 `LOCK_BUSY` |
| S5 | `POST /purchase-orders/{po_id}/shipments/preview` | A·T [통합 X-14] | — | `ShipmentCreateFromPo` = `{lines:[{po_line_id, quantity}], origin_country_code, dest_country_code, parties?, internal_note?}` | `ShipmentPreview`(금액 키 없음) | 409 `SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE`·`DOCUMENT_NOT_CONSUMABLE` |
| S6 | `POST /purchase-orders/{po_id}/shipments` | A·T [통합 X-14] | 멱등 | 같음 | 201 `ShipmentDetail` | 같음 |
| S7 | `PATCH /shipments/{id}`(**[통합 X-20·적대 R-27]** 아래 `NOT_EDITABLE` → `TRADE_DOCS.DOCUMENT.FROZEN`) | A·T·L | ver | `{internal_note?, assignee_id?}`(FREE, 상태 무관) + `{origin_country_code?, dest_country_code?}`(PLANNED만) | `ShipmentDetail` | 409 `SHIPMENTS.SHIPMENT.NOT_EDITABLE`·version, 422 |
| S8 | `POST /shipments/{id}/lines` | A·T [통합 X-14] | 멱등+헤더 ver | `{source_line_id, quantity}` | `ShipmentDetail` | 409 ~~NOT_EDITABLE~~ **[통합 X-20] `TRADE_DOCS.DOCUMENT.FROZEN`**·EXCEEDS_OPEN/ASSIGNABLE |
| S9 | `PATCH /shipments/{id}/lines/{line_id}` | A·T [통합 X-14] | 헤더 ver | `{quantity}` | `ShipmentDetail` | 같음, 404 소속 |
| S10 | `DELETE /shipments/{id}/lines/{line_id}` | A·T [통합 X-14] | 헤더 ver(쿼리) | — | `ShipmentDetail` | 409 마지막 라인 삭제(라인 ≥1 — 0이면 취소 안내) |
| S11 | `POST /shipments/{id}/release-order` | A·T·L | 멱등+ver | `{version}` | `ShipmentDetail` | 409 `TRADE_DOCS.TRANSITION.NOT_ALLOWED` |
| S12 | `POST /shipments/{id}/transitions` | A·T [통합 X-14] | 멱등+ver | `{to_status: "CANCELLED", reason, version}` — `to_status`는 `Literal` 1값 | `ShipmentDetail` | 409 `SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE`, 422 `TRADE_DOCS.TRANSITION.REASON_REQUIRED` |
| S13 | `GET /shipments/{id}/status-log` | 전 | — | Page | `Page[StatusLogEntry]`(기존 형태) | 404 |
| S14 | `POST /shipments/{id}/parties` | A·T·L | 멱등 | `{role, partner_id}` | `ShipmentDetail` | 409 `SHIPMENTS.PARTY.ROLE_DUPLICATE`·NOT_ACTIVE, 422 `ROLE_NOT_ALLOWED`·`ENGLISH_NAME_MISSING`·거래처 유형 |
| S15 | `DELETE /shipments/{id}/parties/{party_id}` | A·T·L | ver(쿼리) | — | `ShipmentDetail` | 422 `ROLE_NOT_ALLOWED`(자동 스냅샷 행), 404 |
| S16 | `GET /shipments/{id}/customs-records` | 전 | — | Page | `Page[CustomsRecord]` | 404 |
| S17 | `POST /shipments/{id}/customs-records` | A·T·L | 멱등 | `{declaration_kind, declaration_no, declared_on, accepted_on?, customs_broker_partner_id?, note?}` | 201 `CustomsRecord` | 409 `SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE`·NOT_ACTIVE, 422 `KIND_MISMATCH`·**[적대 R-18] `DATE_IN_FUTURE`·[적대 R-26] `ACCEPT_BEFORE_DECLARE`** |
| S18 | `PATCH /shipments/{id}/customs-records/{rid}` | A·T·L | ver | 위 필드 일부 + `reason?`(`accepted_on` 변경 시 필수) | `CustomsRecord` | 422 `SHIPMENTS.CUSTOMS.REASON_REQUIRED`·**`DATE_IN_FUTURE`·`ACCEPT_BEFORE_DECLARE`([적대 R-18·R-26])**, 409 version |
| S19 | `DELETE /shipments/{id}/customs-records/{rid}` | A·T·L | ver+`reason`(본문) | `{version, reason}` | 204 | 422 사유 |

- 선적 "출고지시" 엔드포인트명은 sA §A7-1의 `release-order`를 그대로 쓴다.
- S10의 "마지막 라인 삭제 409"는 이 부록이 정한다(라인 0건 선적은 잔량을 소비하지 않는 빈 껍데기라 SO 수렴 불변식 "IN_SHIPMENT ⇔ 살아 있는 선적 ≥1"을 거짓으로 만든다 — sA §A7-2). 신규 코드 `SHIPMENTS.LINE.LAST_LINE`(409, "선적을 취소해 주세요").
- S19 삭제는 soft delete이며 사유는 audit_log에 남는다(sA §A8 "통관 기록을 먼저 사유와 함께 soft delete").

### D2-2. 마일스톤·롤오버·통보 (sB 소관 데이터)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 주요 에러 |
|---|---|---|---|---|---|---|
| M1 | `GET /shipments/{id}/milestones` | 전 | — | — | `MilestoneBoard`(D3 — 상세에 내장된 것과 같은 형태, 쓰기 후 재조회용) | 404 |
| M2 | `POST /shipments/{id}/milestones/{type}/plan` | A·T·L | 멱등+ver(행이 있을 때) | 날짜형 `{planned_on, reason?, version?}` / 시각형 `{planned_at, tz, reason?, version?}` | ~~`MilestoneBoard`~~ **[적대 R-19]** `{board: MilestoneBoard, change: {id, change_kind}\|null}`(같은 키 재요청 = 같은 `change.id`) · NOT_ACTIVE → **[통합 N-05]** `OWNER_NOT_ACTIVE` | 422 `SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE`·`TYPE_NOT_APPLICABLE`·`REASON_REQUIRED`(기존 계획 변경 = 롤오버), 422 tz 미지원, 409 version·NOT_ACTIVE |
| M3 | `POST /shipments/{id}/milestones/{type}/actual` | A·T·L | 멱등+ver(행이 있을 때) | `{actual_on \| actual_at+tz \| null(정정 삭제), reason?, version?}` | ~~`MilestoneBoard`~~ **[적대 R-19]** `{board, change}` | 위 + 422 `ACTUAL_IN_FUTURE`(**[적대 R-18]** 시각형은 `actual_at ≤ now_utc`) · **[적대 R-01]** 422 `ACTUAL_BEFORE_RELEASE` |
| M4 | `POST /shipments/{id}/milestones/plan-draft` | A·T·L | 멱등 | `{}` | `MilestoneBoard` | 409 ~~NOT_ACTIVE~~ **[통합 N-05]** `SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE` |
| M5 | `GET /shipments/{id}/milestone-changes` | 전 | — | `milestone_type?`, `change_kind?`, Page | `Page[MilestoneChange]`(행마다 `notices:[{comm_log_id, occurred_on, summary}]` 내장 — 변경 1건의 통보는 소수) | 404 |
| M6 | `POST /shipments/{id}/milestone-changes/{change_id}/notices` | A·T·L | 멱등 | `{occurred_on, counterpart_partner_id?, summary}`(**[통합 X-23]** comm_logs에 채널 열이 없어 `channel` 제거 — 수단은 요지에) | 201 `MilestoneChange` | 404 소속, 422 |
| M7 | `GET /purchase-orders/{po_id}/milestones` | 전(원가 키 없음) | — | — | `MilestoneBoard`(OEM 4종) | 404, 422 `SHIPMENTS.MILESTONE.OWNER_NOT_OEM` |
| M8 | `POST /purchase-orders/{po_id}/milestones/{type}/plan`·`/actual` | A·T [통합 X-16] | 멱등+ver | M2·M3과 같음 | `MilestoneBoard` | 같음 |
| M9 | `GET /purchase-orders/{po_id}/milestone-changes` | 전 | — | Page | `Page[MilestoneChange]` | 404 |

- `{type}`은 경로 `Literal`(저장형 종류만). 파생형 3종(`LOADING_DEADLINE`·`PAYMENT_DUE`·`PRESENTATION_DEADLINE`)을 넣으면 **스키마 422가 아니라 도메인 코드 422 `DERIVED_NOT_EDITABLE`**로 낸다 — sB GC-21이 이 코드를 단언하므로 경로 타입은 `MilestoneType` 전체로 받고 서비스가 거부한다.
- 메서드: sB §B8 ⑥은 `POST …/plan|actual`, sB GC-21은 `PUT …/plan`으로 표기가 갈린다. **POST로 확정**한다(동작이 "값 덮어쓰기"가 아니라 "이력 행을 남기는 기록"이라 S3-1 `/confirm`·`/issue` 동작형 POST 관례와 맞다). GC-21은 경로 메서드만 POST로 읽는다.
- **M6 통보 기록**: comm_logs 생성 API(`POST /comm-logs`)의 쓰기 역할은 `CAN_EDIT=(CERT,)`뿐이다(`code:modules/collaboration/router.py:31,122-125`). 무역·물류가 롤오버 통보를 기록하려면 그 역할을 넓혀야 하는데, 넓히면 인증 주제 통신 기록 쓰기까지 열린다. 그래서 **선적 하위 전용 엔드포인트 M6**가 `comm_logs`(주제 `SHIPMENT`, sB §B9) 1행 + `milestone_change_notices` 1행을 **1TX**로 만든다. `/comm-logs`의 역할·주제 검증은 무접촉이다(fail-closed). 발송 기능 0(`D:313`).
- M7~M9 경로에는 `purchase`가 들어가지만 `test_po_no_auto_path.py:352-361`은 **잡 이름**만 검사하므로 무관하다. OEM 마일스톤은 PO 상세 화면의 섹션이다(D7).

### D2-3. 휴일 (sB §B11 데이터)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 주요 에러 |
|---|---|---|---|---|---|---|
| H1 | `GET /holidays/calendars` | 전 | — | `country?`, `year?`, Page | `Page[CalendarYear]`(국가·연도·건수·출처·확인일·수정자·수정 시각) | 422 |
| H2 | `GET /holidays` | 전 | — | `country`(필수), `year`(필수), Page | `Page[Holiday]` + 봉투 밖 `calendar: CalendarYear \| null` | 422 `HOLIDAYS.COUNTRY.INVALID` |
| H3 | `PUT /holidays/{country}/{year}` | A | 멱등+ver(선언이 있을 때) | `{source_url, verified_on, holidays:[{holiday_on, name}](0건 허용 — "휴일 없음 확인"), version?}` | `CalendarYear` | 422 `HOLIDAYS.CALENDAR.SOURCE_REQUIRED`·`YEAR_MISMATCH`·`COUNTRY.INVALID`, 409 version |
| H4 | `GET /holidays/{country}/{year}/export.csv` | 전 | — | — | CSV(UTF-8 BOM·수식 이스케이프) | 404 선언 없음 |
| H5 | `POST /holidays/{country}/{year}/import-csv/preview` | A | — (비저장) | multipart CSV | `{rows:[{holiday_on, name}], problems:[{row, field, message}]}` | 422 `HOLIDAYS.CSV.INVALID_FORMAT`(인코딩·헤더·크기) |

- **CSV는 미리보기 → 사람 확인 → H3 원자 교체**의 2단이다. 쓰기 통로는 H3 하나다(sB §B11 "화면 입력과 CSV 업로드는 이 API의 클라이언트"). 행 문제가 1건이라도 있으면 화면이 H3 버튼을 막는다(파일 전체 원자 — S3-1 인테이크 CSV 판정 선례 `docs/plans/s3-1/design-D.md` §0-2 "CSV 원자성").
- H2의 `calendar=null`은 "미등록"을 뜻하고 **빈 목록과 다르다**(UNVERIFIED의 화면 근거).
- 쓰기 역할 ADMIN 전용은 sB §B11 확정을 따른다.

### D2-4. 기존 엔드포인트 확장

| # | 경로 | 변경 | 근거 |
|---|---|---|---|
| X1 | `GET /document-flow/{doc_kind}/{doc_id}` | `doc_kind`에 `SHIPMENT` 허용, SO 사슬 아래 **수출선적 노드** 추가. 쿼리 5회 이내 계약 유지(선적은 SO id 목록으로 1쿼리) | `code:modules/trade_chain/document_flow.py:4,23-29`, sA §A12 |
| X2 | `GET /orders/board…`(보드 열·카드·CSV·저장 필터) | `SO_IN_SHIPMENT` 열 가산(D8) | `code:modules/order_board/constants.py:1-5,27-57` |
| X3 | `GET /sales-orders/{id}` | 라인마다 `shipment_open_quantity`(선적 잔량 = `open_quantity(SO_LINE)`) 필드 | sA §A4, `D:173` "잔량이 부분·초과 방지의 기준" |
| X4 | `GET /purchase-orders/{id}` | 라인마다 `assignable_quantity`(수입선적 배정 가능량)·`expected_receipt`(sB §B17 계산값 — 가장 늦은 ETA, 없으면 null) | sA §A4, sB §B17 |
| X5 | 사용자 API(`/users`, `/users/{id}/roles`, `/active`, `/unlock`) | **변경 없음** — 화면만 신설(D14) | `code:modules/identity/router.py:90-158` |

---

## D3. 응답 스키마 골격 — 상세는 1요청·쿼리 수 고정

**결정**

```
ShipmentListItem {
  id, doc_number, status, status_label?(없음 — 라벨은 프런트 단일 표), shipment_kind,
  source: {kind: "SALES_ORDER"|"PURCHASE_ORDER", id, doc_number},
  counterparty_name, origin_country_code, dest_country_code,
  etd: EffectiveDate|null, eta: EffectiveDate|null,      # 저장형 유효값(실적 우선) — 1쿼리 조인
  assignee: {id, display_name}|null, updated_at
}
EffectiveDate { value: "YYYY-MM-DD", basis: "ACTUAL"|"PLANNED" }

ShipmentDetail {
  header: { id, doc_number, status, shipment_kind, version, frozen_at, doc_date,
            source{kind,id,doc_number,status}, counterparty{partner_id,name},
            origin_country_code, dest_country_code,
            currency, fx_rate_text, fx_rate_date,                  # 고정 환율(원천 복사)
            payment_terms{type, advance_pct_text?, anchor?, days?}, # 4열 사본, 표시용
            incoterm{code, place, year}|null,
            total_text|null,                                        # 수입은 null(원가 키 0)
            internal_note, assignee{id,display_name},
            dg_line_count },                                        # D9
  lines: [{ id, line_no, sku{id,code,name}, quantity,
            source_line{id, line_no, quantity, remaining_after}, # 수출: SO 잔량 / 수입: 배정 가능량
            line_amount_text|null, dg{flag, un_number?, dg_class?},
            availability: {status: "NOT_IMPLEMENTED"} }],          # §8.3 자리(D10)
  parties: [{ id, role, partner_id, name_en, address_en, auto: bool }],
  milestones: MilestoneBoard,
  customs_summary: { live_count, latest_accepted_on|null },        # 목록은 S16
  allowed_actions: ["RELEASE_ORDER","CANCEL","EDIT_LINES","EDIT_COUNTRIES","PLAN_DRAFT", …]
}

MilestoneBoard {
  today_kst: "YYYY-MM-DD",
  holiday_summary: { holiday: n, unverified: n },
  rows: [{
    milestone_type, kind: "STORED"|"DERIVED", value_shape: "DATE"|"DATETIME",
    applicable: bool,                                   # sB §B1 적용 표(구분·결제유형)
    planned: "YYYY-MM-DD"|{at_utc, tz}|null, actual: 같음|null,
    effective: {value, basis}|null,
    derived: {status:"OK"|"UNKNOWN"|"NOT_APPLICABLE", value|null, basis|null, reason_code|null}|null,
    scan_date: "YYYY-MM-DD"|null, local_date: "YYYY-MM-DD"|null,   # [적대 R-25] 시각형: D-N 기준일(min(현지,KST)) / 현지 날짜(휴일 판정) 구분
    customs_state: "NONE"|"PARTIAL"|"CLEARED"|null,               # [적대 R-06] CUSTOMS_CLEARED 행 — 미수리 기록 1건↑ = PARTIAL("일부 미수리 n건")
    days_left: int|null, is_overdue: bool|null,   # [적대 R-20] 시각형 is_overdue = now_utc > effective_at fulfilment: "MET"|"MET_LATE"|"OPEN"|"OVERDUE"|"UNKNOWN"|null,
    holiday: {flag:"HOLIDAY"|"CLEAR"|"UNVERIFIED", country, name|null}|null,   # 적용 종류만
    rollover_count: int, unnotified_rollovers: int,
    order_warning: "ETA_BEFORE_ETD"|null,
    milestone_id|null, version|null,
    input_source: "MILESTONE"|"CUSTOMS_RECORD"            # CUSTOMS_CLEARED 실적 = 통관 기록(§0 가정 5)
  }]
}
```

- `allowed_actions`는 **표시 편의**다. 서버가 쓰기 시 다시 검사한다(셸 메뉴 주석 "표시 편의일 뿐, 서버가 정본" `fe:routes/shell.tsx:33` 계보). 역할·상태·잔량에서 서버가 계산해 내려 주므로 프런트에 상태 규칙을 복제하지 않는다.
- **쿼리 수 고정**: 상세 1요청 = 헤더 1 + 라인(SKU·원천 라인·잔량 집계 포함) 고정 n + 당사자 1 + 마일스톤 1 + 원천 결제조건 1 + 휴일(관련 국가·연도 일괄) 1 + 통관 요약 1. **쿼리 수 상한 테스트**(K)를 둔다. 타임라인은 일괄 로딩(`D:376`, sB §B10 "조회 조립자").
- 수입선적 응답에는 금액·단가 키가 **아예 없다**(null이 아니라 미포함 — `total_text`는 수입이면 null로 두되 라인 금액 키는 스키마 분기로 제거). K 테스트 "수입선적 응답에 원가 키 0"(sA §A16 K)과 같은 단언을 화면 측에도 둔다.

**근거**: `D:376`(N+1 금지), `D:146` ③("실효 상태 … 한 정의를 공유" — 화면과 스캔이 같은 함수), `D:39` ③(PO 원가 9채널 봉쇄), sB §B10.

**대안(기각)**
- (a) 프런트가 계획·실적으로 유효값·D-N을 계산: 정의 이원화(`D:146` ③ 위반).
- (b) 상세를 헤더/라인/마일스톤 3요청으로 분할: 화면 일관 시점이 깨지고(쓰기 후 부분 갱신) 요청 수만 는다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(필드 가산·분리).

---

## D4. 에러 코드 — 이 부록 신설분(타 부록 신설분은 재열거하지 않음)

| 코드 | HTTP | 상황 | 문구(조치 힌트 포함) |
|---|---|---|---|
| `SHIPMENTS.LINE.LAST_LINE` | 409 | 마지막 라인 삭제 | "선적에는 라인이 1개 이상 있어야 합니다. 선적 전체를 없애려면 선적을 취소해 주세요." |
| `HOLIDAYS.CSV.INVALID_FORMAT` | 422 | CSV 인코딩·헤더·크기 | "휴일 CSV 형식을 확인해 주세요(UTF-8, 머리글 holiday_on,name)." |

- 재사용: sA §A13(SHIPMENTS.* 12종·TRADE_DOCS.* 재사용), sB §B19(`SHIPMENTS.MILESTONE.*` 5종·`HOLIDAYS.*` 3종). 카탈로그 1:1·조치 힌트 테스트(`tests/unit/test_error_catalog.py:19-49`)가 신설 2종에도 걸린다.
- **프런트 표시 규칙**: 서버 `message`를 그대로 보이고, 409 `EXCEEDS_OPEN`·`EXCEEDS_ASSIGNABLE`의 `detail`(라인별 잔량)은 **해당 입력 칸 아래에 줄별로** 붙인다(대화상자 상단 요약 1줄 + 칸별 표시). `LOCK_BUSY`는 "잠시 후 다시 시도해 주세요" + 같은 키 재시도 버튼.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D5. 화면 목록·라우트·셸 메뉴

**결정 — 라우트**(`fe:App.tsx:97-138`에 추가)

| 라우트 | 화면 | 진입 |
|---|---|---|
| `/shipments` | 선적 목록 | 셸 메뉴 "선적" |
| `/shipments/:shipmentId` | 선적 상세 | 목록·SO/PO 상세·문서 흐름·알림 |
| (대화상자) `ShipmentCreateDialog` | 수출선적 생성 2단 | **SO 상세** "선적 만들기" |
| (대화상자) 같은 컴포넌트 `mode="IMPORT"` | 수입선적 생성 2단 | **PO 상세** "수입선적 만들기" |
| `/holidays` | 휴일 캘린더(국가×연도 목록 + 연도 상세·편집) | 셸 메뉴 "휴일 캘린더", 선적 상세의 UNVERIFIED 배지 링크(`?country=CN&year=2027`) |
| `/settings/users` | 사용자·역할(ADMIN) | 셸 ADMIN 메뉴 "사용자·역할" (D14) |

- **`/shipments/new` 독립 생성 화면은 두지 않는다.** 원천 없이 선적을 만드는 화면은 재입력 금지(`D:29`, `D:175` ①)와 생성 경로 2종 제한(sA §A2)을 화면에서 어기는 입구가 된다. 생성은 항상 원천 전표 상세에서 시작한다(S3-1 `SalesOrderCreateDialog`가 QT·PI 상세에서 열리는 선례 `fe:routes/quotation-detail.tsx:379`, `fe:routes/proforma-detail.tsx:395`).
- 상세 파일명은 **`routes/shipment-detail.tsx`**로 한다. `fe:routes/detail-layout.test.ts:6`의 `./*-detail.tsx` glob에 자동 편입되어 390px 그리드 계약(`grid-cols-[minmax(0,1fr)]`)을 상속한다. 이 테스트의 하한(`>= 6`)을 7로 올린다.

**결정 — 셸 메뉴**(`fe:routes/shell.tsx:8-44`)
- `NAV`에 `{ to: "/shipments", label: "선적" }`을 "발주" 다음에, `{ to: "/holidays", label: "휴일 캘린더" }`를 "선적" 다음에 넣는다(열람은 전 역할 — sB §B11).
- `ADMIN_NAV`에 `{ to: "/settings/users", label: "사용자·역할" }`을 넣는다.
- "선적 캘린더"는 **넣지 않는다**(§19 Phase 4 `D:397`, WBS S4-4 `W:152` — §7.5 문면에도 있으나 Phase 순서 정본이 §19).
- 메뉴 표시는 역할 편의일 뿐 서버가 정본이다.

**근거**: `D:303` §14 ⑦ "선적 관리(마일스톤 타임라인·서류 버튼·문서흐름 뷰·DG 표시·피킹/검수)", ⑧ "선적 캘린더", ⑭ "관리(… 사용자 …)". S3-2 몫은 타임라인·문서흐름·DG 표시다. 서류 버튼은 S3-3(`W:118-120`), 피킹/검수는 S4-2(`W:139-140`)다.

**대안(기각)**
- (a) 선적 메뉴를 물류 역할에만 노출: 조회는 전 역할 ALLOW(sA §A11)이고 무역이 생성 주체이기도 하다.
- (b) 휴일 캘린더를 ADMIN 메뉴에만: 열람이 전 역할이고, 물류가 "미등록" 배지를 보고 등록 상태를 확인할 곳이 필요하다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(메뉴 1줄).

---

## D6. 선적 상세 화면 — 구성·마일스톤 타임라인·실적·롤오버·통보·휴일 경고

**결정 — 섹션 순서**(세로 쌓기, `grid grid-cols-[minmax(0,1fr)] gap-6`)
1. **헤더 카드** "선적 정보": 선적번호, 상태 배지, 구분("수출"/"수입"), 원천 전표 링크(SO/PO 번호), 거래 상대, **출발국 → 도착국**(국가명 nowrap + 코드), Incoterms, 통화·**고정 환율**("SO에서 고정 — 바꾸려면 취소 후 새로 만듭니다" 보조문), 결제조건 요약, 담당자, DG 배지 요약(D9), 내부 메모.
2. **동작 바**: [출고지시] [취소] [계획 초안 만들기]. 노출은 `allowed_actions`로만 판단한다.
3. **휴일 요약 배너**(해당 시): "휴일 경고 n건 · 휴일 캘린더 미등록 n건 — 확인 불가". 경고만, 차단 없음(sB §B12).
4. **마일스톤 타임라인**(아래 상세).
5. **라인**: SKU, 수량, 원천 라인 수량, **남은 잔량**(수출 "선적 잔량"/수입 "배정 가능량"), 라인 금액(수출만), DG, **가용재고**("미산정" 배지 — D10). PLANNED이면 수량 편집·라인 추가/삭제.
6. **당사자**: 역할(한글 라벨·nowrap), 거래처명(영문 스냅샷), 주소. 자동 스냅샷 행은 "원천에서 복사" 표시·삭제 버튼 없음.
7. **통관 기록**(D7).
8. **롤오버 이력**: M5 Page. 열 — 종류, 변경 종류("계획 설정"/"롤오버"/"실적 기록"/"실적 정정"), 이전 → 새 값, 사유, 기록자, 기록 시각(KST), **통보**(연결된 통신 기록 요지 또는 "통보 기록 없음" 배지 + [통보 기록] 버튼).
9. **상태 이력**: 기존 `StatusTimeline`(`fe:components/status-timeline.tsx:11-36`) `basePath=/v1/shipments/{id}`.
10. **문서 흐름**: 수출선적은 `DocumentFlowPanel kind="SHIPMENT"`(D8). 수입선적은 패널 없이 헤더의 원천 PO 링크만(문서 흐름은 QT·PI·SO 사슬이라 PO가 없다 — `code:modules/trade_chain/document_flow.py:23-29`).

**결정 — 마일스톤 타임라인**
- **세로 순서 목록(`<ol>`)**이고, 가로 간트·달력 그래픽은 그리지 않는다(캘린더 뷰는 S4-4). 순서는 업무 흐름 고정: 서류마감 → Cargo Closing → 수출 전 검사 → 신고수리 → 적재기한(파생) → ETD → B/L 발행일 → L/C 제시기한(파생) → ETA → 수입 세금 납부기한 → 대금만기(파생). `applicable=false` 행은 숨긴다(구분·결제유형 비해당 — sB §B1 적용 표). 단 **L/C 결제 선적의 `PRESENTATION_DEADLINE`은 숨기지 않고** "산정 불가 — L/C 조건 미등록"으로 보인다(운영 경로 UNKNOWN, sB §B5·GC-08).
- 행 하나 = 카드 1장(390px에서 표가 아니라 카드라 가로 스크롤 0). 카드 내용:
  - 종류명(nowrap), 성격 표시("직접 입력" / "자동 계산 — 직접 수정 불가")
  - **계획**·**실적** 두 값(날짜형은 `YYYY-MM-DD (현지)` 원문 그대로, 시각형은 D12의 병기 형식)
  - **유효값 기준 배지**: 실적이면 "실적", 계획이면 "예정 기준(실적 입력 시 다시 계산)"(sB §B4 "basis")
  - **D-N·도과**: 서버 `days_left`·`is_overdue`로 "D-3"/"D-day"/"3일 지남"(가운데 정렬·nowrap). 실적이 있으면 D-N 대신 "완료"
  - 적재기한은 `fulfilment` 라벨: "기한 내 적재"(MET)/"기한 후 적재"(MET_LATE)/"진행 중"(OPEN)/"도과"(OVERDUE)/"판정 불가"(UNKNOWN)
  - **휴일 배지**(적용 종류만 — ETA=도착국~~, ETD·Cargo Closing·서류마감=출발국~~ **[적대 R-09] ETA만**, sB §B12 ③): HOLIDAY → 황색 "도착국 휴일: 국경절 (CN)"; **UNVERIFIED → 회색 "휴일 캘린더 미등록 — 확인 불가 (CN 2027)" + `/holidays?country=CN&year=2027` 링크**; CLEAR → 배지 없음. 파생 3종과 세금기한은 휴일 판정을 하지 않으므로 대금만기 카드에 보조문 "휴일 미반영(자동 이월 없음)"을 단다(sB §B12·GC-18).
  - 순서 경고(`order_warning`): "ETA가 ETD보다 이릅니다 — 날짜변경선이 아니라면 확인해 주세요"(차단 아님, sB §B8 ⑤·GC-19)
  - 롤오버 횟수 "롤오버 2회"(ETD·ETA·Cargo Closing만, sB §B9) + 미통보가 있으면 "통보 기록 없음 1"
  - **파생 UNKNOWN 사유 한글 표**(프런트 단일 표 — 모르는 코드는 "산정 불가(기타)"): `INVOICE_NOT_ISSUED`="인보이스 미발행(S3-3)", `ANCHOR_PENDING`="기산일 미확정", `LC_TERMS_NOT_REGISTERED`="L/C 조건 미등록", `NOT_CLEARED`="신고수리 전", `RECEIPT_NOT_RECORDED`="입고 미기록", `TERMS_MISSING`="결제조건 없음". **UNKNOWN을 빈칸·"-"·0일로 그리지 않는다**(`D:192` ② "UNKNOWN은 통과가 아니며" 계보, GC-A13).
  - 버튼: 저장형은 [계획 입력/변경] [실적 입력/정정]. 파생형은 버튼 없음. `CUSTOMS_CLEARED` 실적 칸은 버튼 대신 "통관 기록에서 입력합니다 →"(§0 가정 5).

**결정 — 실적 입력 대화상자**(`ConfirmDialog` 재사용 `fe:components/confirm-dialog.tsx:1-16,100`)
- 날짜형: `<input type="date">` 1개 + "현지 날짜로 입력(서류에 찍힌 날짜)" 안내. 시각형: `datetime-local` + 시간대 선택(D12).
- 기존 실적이 있으면 제목이 "실적 정정"으로 바뀌고 **사유 필수**(빈 사유 제출 불가 — `ConfirmDialog` 사유 필수 모드), 실적 지우기 체크도 정정으로만.
- 미래일 경계는 서버가 판정(`ACTUAL_IN_FUTURE`) — 프런트는 `max` 속성을 걸지 않는다(브라우저 시간대 의존 제거, D1).
- 저장 후 `MilestoneBoard`로 타임라인 전체를 교체한다 — 파생값 재계산이 "읽기 결과의 변화"이므로(sB §B8 ①) 별도 재계산 버튼은 없다. 바뀐 파생 행에 `aria-live="polite"` 안내 "대금만기가 2026-11-02로 다시 계산됐습니다"(이전·이후 값 비교는 프런트가 문자열 비교만 — 산술 0).

**결정 — 롤오버(계획 변경) 대화상자**
- 계획이 이미 있는 저장형 행의 [계획 변경] = 롤오버. 이전 값 표시, 새 값, **사유 필수**, 그리고 선택 체크 "통보도 지금 기록"(체크 시 통보 입력 칸 노출).
  - 체크 시 요청은 **2회**(M2 → 응답의 ~~change_id~~ **[적대 R-19] `change.id`**로 M6)이며 각자 키를 갖는다. 1TX로 합치지 않는 이유: 통보는 사후에 생기는 사실이 일반적이고(sB §B9 "이력 행이 불변이라 통보는 사후 연결"), M2의 원자성(마일스톤+이력+outbox)을 통보 실패가 깨면 안 된다. M6 실패 시 "롤오버는 기록됐고 통보 기록은 실패 — 롤오버 이력에서 다시 기록해 주세요"를 보인다.
- 통보 기록 칸: 일시(현지 날짜), ~~수단(comm_logs 채널 열거)~~ **[통합 X-23]** 수단은 요지에 적는다(채널 열 없음), 상대 거래처(SearchSelect — 포워더·관세사·바이어 유형), 요지. **"보내기" 단어를 쓰지 않는다** — 버튼 라벨 "통보 기록 저장", 보조문 "이 시스템은 메일을 보내지 않습니다. 실제로 알린 사실을 기록합니다."(`D:132` ④, `D:313`).

**결정 — 출고지시·취소·초안**
- [출고지시]: 확인 대화상자 "출고지시 후에는 라인·국가를 바꿀 수 없습니다. 바꾸려면 취소 후 새로 만듭니다." 사유 없음(sA §A7-1). "피킹·검수·출고는 재고 기능(Phase 4)에서 이어집니다" 보조문 — RESERVED 상태를 버튼으로 노출하지 않는다.
- [취소]: 사유 필수. `CUSTOMS_RECORD_ALIVE` 409면 "통관 기록을 먼저 삭제해 주세요"와 통관 섹션으로 스크롤 링크.
- [계획 초안 만들기]: 계획 행이 하나도 없을 때만 강조 버튼, 이후엔 "빠진 종류 채우기"로 라벨 변경(sB §B16 "이미 있는 종류는 건너뛴다"). 자동 생성 없음 — 사람 1클릭(sB §B16, `D:190`).

**근거**: `D:203`(§7.5), `D:303` ⑦, `D:26`, `D:132` ③④, `D:313`, `D:192` ②, sB §B1·B4·B8·B9·B12·B16, sA §A7-1·A8.

**대안(기각)**
- (a) 타임라인을 가로 막대(간트)로: 390px에서 가로 스크롤이 필수가 되고 캘린더 뷰(S4-4)와 겹친다.
- (b) CLEAR에도 "평일 확인" 배지: 11행 중 다수에 같은 배지가 붙어 경고 배지의 신호가 묻힌다. 판정이 필요한 차이는 UNVERIFIED뿐이다.
- (c) 롤오버+통보를 1요청 1TX: 통보 실패가 롤오버 기록을 롤백시켜 일정 사실이 사라진다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(화면 컴포넌트). CLEAR 배지 추가는 1줄.

---

## D7. 선적 생성 2단 대화상자·통관 기록·OEM 마일스톤

**결정 — 생성 대화상자**(`ShipmentCreateDialog`, mode EXPORT|IMPORT)
- **1단(입력)**: 원천 라인 표 — SKU, 원천 수량, **남은 잔량**(X3/X4), **이번 선적 수량**(입력). **기본값은 빈칸**이고 행마다 [잔량 전부] 버튼을 둔다. 빈칸·0인 행은 요청에서 제외, 1행 이상이어야 [미리보기] 활성.
  - 출발국·도착국: ISO 3166-1 alpha-2 선택(국가명 한글 + 코드, nowrap). **둘 다 기본값 없음·필수**. 도착국을 SO 시장 코드로 미리 채우지 않는다 — 시장은 EU 같은 비국가 코드를 허용하고(`code:modules/markets/models.py:47-48`), 시장 국가 ≠ 도착항 국가일 수 있다(sA §A2 대안 (d)가 화면 재량으로 남긴 지점을 엄격 쪽으로 닫음).
  - 당사자(선택): 포워더·관세사·Notify 등 역할별 SearchSelect. 원천에서 자동 복사되는 역할(~~수출 SHIPPER·CONSIGNEE 등~~ **[적대 R-27]** 수출 CONSIGNEE·수입 SHIPPER — sA §A5, 수출 SHIPPER는 거부)은 "자동" 표시로 잠금.
  - 입력 칸에 **SKU·단가·통화·환율·거래처·Incoterms·결제조건 입력은 없다**(`D:175` ①). 화면에 보이는 그 값들은 전부 원천에서 읽은 읽기 전용이다.
- **2단(미리보기 → 확정)**: S3/S5 응답을 그대로 보인다(복사될 통화·고정 환율·Incoterms·결제조건·라인 금액[수출만]). [생성 확정]은 같은 대화상자 세션의 키로 S4/S6. 409 잔량 초과면 1단으로 돌아가 칸별 잔량을 표시한다(D4).
- 진입 버튼 노출: SO 상세는 `status ∈ {CONFIRMED, IN_SHIPMENT}`이고 선적 잔량 합 > 0일 때, PO 상세는 `status ∈ {ISSUED, SUPPLIER_CONFIRMED}`이고 배정 가능량 합 > 0일 때(`CONSUMABLE_STATUSES` — sA §A4). 서버가 정본이다.

**결정 — 통관 기록 섹션**
- 표 열: 구분("수출신고"/"수입신고", nowrap), 신고번호(nowrap·가운데), 신고일, **수리일**(없으면 "미수리" 배지), 관세사, 메모(break-keep). 상단 보조문 "세율·세액·HS 판정은 기록하지 않습니다 — 관세사 신고 결과를 사실로만 남깁니다"(`D:313`, `D:19`).
- [통관 기록 추가] 대화상자: 구분은 선적 구분에서 고정(수출선적 → 수출신고만, sA §A8 `KIND_MISMATCH` 사전 차단), 관세사는 SearchSelect(유형 CUSTOMS_BROKER).
- 수리일 수정: **사유 필수** 칸이 나타나고, 보조문 "적재기한(수리일+30일)이 다시 계산됩니다"(수출만).
- 삭제: 사유 필수 확인 대화상자.

**결정 — OEM 생산 마일스톤(PO 상세)**
- `po_kind=OEM_PRODUCTION`인 PO 상세에만 "생산 일정" 섹션(M7)을 둔다: 원료수급 → 충진 → 포장 → 출하검사 4행 카드, 계획·실적·롤오버(사유 필수)는 선적 타임라인과 같은 컴포넌트(`MilestoneTimeline`)를 재사용한다. 알림·휴일 배지 없음(sB §B15 "표시만"). 금액 0.
- 일반 PO 상세에는 "수입선적" 섹션(S1 `po_id=` 필터, Page)과 라인별 "입고예정"(X4 — 없으면 "입고예정 미정")을 둔다.

**근거**: `D:29`, `D:173`, `D:175` ①, `D:98`, `D:313`, sA §A2·A4·A5·A8, sB §B15·B17.

**대안(기각)**
- (a) 이번 수량 기본값 = 잔량 전부: 한 번의 실수 클릭으로 전량 선적이 생성되고, 부분선적이 기본 업무 형태(`D:173` "부분 1:N")라 엄격 쪽은 빈칸이다.
- (b) 출발국 기본 KR: 수입선적·제3국 출발에서 조용히 틀린 국가가 들어가고 휴일 판정 국가가 틀린다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(기본값 1줄).

---

## D8. 오더 보드·SO 상세·문서 흐름 연결

**결정**
- **오더 보드**: `SO_IN_SHIPMENT`("선적중") 열을 `SO_CONFIRMED` 뒤에 **5번째 열로 가산**한다(sA §A7-4 하단 권장과 일치). `EXCLUDED_SO_STATUSES`에 넣는 안은 기각 — IN_SHIPMENT 수주가 보드에서 조용히 사라진다(fail-visible 위반).
  - 파급: `STAGE_ORDER` 4→5, `tests/architecture/test_order_board_contract.py:74-87`(`len(STAGE_ORDER)==4` → 5), 저장 필터의 stage 검증(`tests/e2e/test_order_board.py:356-358`), 보드 CSV의 열 라벨, 프런트 열 렌더(`fe:routes/order-board.tsx:754` 이미 `overflow-x-auto` 컨테이너 — 5열도 보드 영역 안에서만 가로 스크롤, 페이지 가로 스크롤 0).
  - 카드에 "선적 n건 · 다음 ETD 2026-11-05(예정)"을 붙이지 않는다 — 카드 응답에 선적 집계를 넣으면 보드 쿼리 고정 계약이 바뀐다. 카드 클릭 → SO 상세 → 선적 섹션으로 충분하다. (부채 아님 — 필요 시 가산.)
  - 정렬: 선적중 열은 확정 열과 같이 최근 순(`NEWEST_FIRST_STAGES`에 추가).
  - 벌크 동작(`CONFIRM_SO` 등)은 선적중 카드에 비활성 — 기존 벌크는 RECEIVED 전용이라 무변경.
  - COMPLETED는 S3-2에서 도달 불가(RESERVED 유지, sA §A7-3)이므로 열을 만들지 않는다.
- **SO 상세**: "선적" 섹션 신설 — 이 SO의 선적 목록(S1 `so_id=`, Page 50: 선적번호·상태·ETD/ETA 유효값·라인 수), [선적 만들기] 버튼, 라인 표에 "선적 잔량" 열(X3). IN_SHIPMENT 상태에서는 보류·취소 버튼이 서버 `public_transition_targets`상 없으므로(sA §A7-2) 보조문 "선적이 살아 있는 동안 보류·취소할 수 없습니다 — 선적을 먼저 취소해 주세요"를 보인다(왜 버튼이 없는지 설명).
- **문서 흐름**: 백엔드 `FLOW_KINDS`에 SHIPMENT를 넣고 SO 노드의 자식으로 수출선적을 붙인다. 프런트 `FlowKind`에 `"SHIPMENT"`, `KIND_LABEL`에 "선적", `flowRoute`에 `/shipments/{id}`(`fe:components/document-flow-panel.tsx:14-34`). 들여쓰기 상한(`depth < 5`)은 QT→PI→SO→SH 4단이라 여유가 있다.
- **상태 라벨 단일 표**: `fe:lib/doc-status.ts`에 `SHIPMENT_STATUS` 8값 라벨(계획·출고지시·피킹·검수완료·출고·선적·종결·취소)과 배지 색을 추가한다. 모르는 값은 원문이 아니라 "기타"로(`D:307` "서버가 모르는 값은 '기타'"). SO `IN_SHIPMENT`="선적중"은 이미 있다(`fe:lib/doc-status.ts:107`).

**근거**: `D:201` ①(보드 완전성: SO 상태 = 매핑 ∪ {CANCELLED} ∪ RESERVED), `code:modules/order_board/constants.py:1-5`("할당·선적 열은 소비 세션 S3-2·S4-2가 상수 1줄+테스트로 붙인다"), `D:307`, `D:303` ⑦ "문서흐름 뷰".

**대안(기각)**
- (a) IN_SHIPMENT를 SO_CONFIRMED 열에 합침: 열 의미("확정 — 선적 대기")가 흐려지고, 저장 필터 "확정만"이 선적중을 섞는다.
- (b) EXCLUDED: 위 이유.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(상수 1줄 + 테스트, 저장 필터 stage 값은 가산이라 기존 필터 무손상).

---

## D9. DG 표시 — 배지만, 차단 없음

**결정**
- 라인에 SKU `dg_flag`·`un_number`·`dg_class`(`code:modules/catalog/models.py:155-191`)를 응답에 싣고, 화면은 "DG" 배지 + "UN1950 · 2.1"(nowrap)을 보인다. 헤더 요약 "위험물 라인 n개".
- 차단·체크리스트 태스크 자동 생성은 하지 않는다(임시 수동 체크리스트는 S3-4 `W:126`, 게이트 정식은 S4-4 `W:151`). 헤더 요약 옆 보조문 "위험물 선적 점검은 담당자가 수동으로 확인합니다(자동 점검은 이후 단계)".

**근거**: `D:303` ⑦ "DG 표시", `D:394`(Phase 3 임시 규칙 — 수동 체크리스트).

**대안(기각)**: DG 선적 생성 차단 — 문면에 없는 차단(추측 구현)이고 S4-4 게이트와 이중이 된다.

**자율 확정**: 확정. S3-2~S3-4 공백 구간은 runbook 경고와 부채로 등재한다(통합 부록). **되돌리기 비용**: 낮음.

---

## D10. §8.3 가용재고 "자리" — 화면 표시만

**결정**: 선적 라인 응답의 `availability.status = "NOT_IMPLEMENTED"`를 화면은 회색 배지 **"가용재고 미산정"**으로 보이고, 숫자·0·현재고를 그리지 않는다. 생성 대화상자에도 같은 배지(차단 없음).

**근거**: `D:227`, `D:393`, `W:113` "(자리만)", sB §B14 "0이나 현재고를 가용으로 표시하지 않는다".

**대안(기각)**: 열 자체를 숨김 — "자리"를 화면에서 이행하지 않게 되고, S4-2가 열을 새로 넣을 때 레이아웃 변경이 커진다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D11. 알림 이동 매핑

**결정**: `fe:lib/alert-routes.ts`의 `ALERT_ROUTES`에 `shipments: (id) => /shipments/${id}`를 1줄 더하고 테스트 1케이스를 더한다. 선적 기일 알림은 `entity_type="shipments"`, OEM 생산 마일스톤은 알림이 없고(sB §B15), QT/PI 만료 임박(sB §B18)은 기존 `quotations`·`proforma_invoices` 행이 받는다. `milestones` 행은 만들지 않는다(§0 가정 6).

**근거**: `D:307`("entity_type→라우트 표로 일반화"), `fe:lib/alert-routes.ts:3-4`("새 전표·알림 종류는 이 표에 한 줄만 더한다").

**대안(기각)**: `entity_type="milestones"` + `/milestones/:id` 해석 라우트 — 해석용 API와 화면이 하나 더 생기고 OEM(PO 소유)과 분기가 필요하다.

**자율 확정**: 확정(sB §B13과의 정합은 통합이 확인). **되돌리기 비용**: 낮음.

---

## D12. 시간 표시 — KST·현지 병기 함수와 날짜 문자열 규율

**결정**
- `fe:lib/datetime.ts`에 `toZonedPairDisplay(isoUtc, tz)`를 신설한다: `"2026-11-05 14:00 (KST) · 2026-11-05 15:00 (Asia/Tokyo)"`. tz가 `Asia/Seoul`이면 KST 1개만. 형식기는 `Intl.DateTimeFormat`(`timeZone`)만 쓴다.
- **날짜형 마일스톤 문자열(`YYYY-MM-DD`)은 `new Date()`에 넣지 않는다**(UTC 자정으로 해석되어 하루 밀림). 표시는 문자열 그대로 + "(현지)". 이 규칙을 vitest 소스 계약으로 고정한다(`routes/shipment-*.tsx`·`components/milestone-*.tsx`에서 `new Date(` 사용 0 — `detail-layout.test.ts` 방식).
- 시간대 선택: 출발국이 KR이면 기본 `Asia/Seoul`, 그 밖은 **기본값 없음·필수**이고 목록은 `Intl.supportedValuesOf("timeZone")`. 국가→대표 시간대 매핑 표를 프런트에 두지 않는다(항구 마스터 미신설 sB §B3 ⑤ — 다시간대 국가[US 등]에서 틀린 기본값이 조용히 들어가는 것을 막는다). 서버가 `zoneinfo`로 재검증(422).
- 감사성 시각(기록 시각·수정 시각)은 기존 `toKstDisplay`만 쓴다. UTC ISO 원문 노출 0(PR-16 결함 ① `P:16`).

**근거**: `D:26`("시각=UTC 저장·KST 표시(국제 일정은 현지 시간대 병기)"), `D:452`(렌즈 6), sB §B3.

**대안(기각)**: 국가별 대표 시간대 기본값 — 다시간대 국가에서 fail-open.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(함수 1개·선택 기본값).

---

## D13. 한국어 UI·390px·접근성 규율(S3-2 신규 화면 전부)

**결정 — 한국어 UI**(`D:303` 끝 문장, CLAUDE.md 기술 규칙)
- 본문·사유·메모·보조문 `break-keep`. 좁은 셀·표 머리글·**국가명**·종류명·상태 배지·선적번호·신고번호·D-N·날짜 `cell-nowrap`(`fe:styles/index.css:21`).
- 숫자(수량·잔량·D-N·건수·환율)와 기준값(날짜)은 **가운데 정렬**.
- 상태·역할·변경 종류·사유 코드 라벨은 프런트 단일 표에서만 가져오고, 모르는 값은 "기타"(`D:307`).
- 빈 목록·로딩·오류는 `ListState`(`fe:components/list-state.tsx:19`), 목록 페이지는 `ListPager`·`usePagedList`만 쓴다(유일 구현 강제).

**결정 — 390px**
- 상세: `grid-cols-[minmax(0,1fr)]`(`detail-layout.test.ts` 자동 적용, D5).
- 넓은 표(라인·통관·롤오버 이력·휴일)는 **섹션 안 `overflow-x-auto` 래퍼**에서만 가로 스크롤한다 — 페이지 `scrollWidth`는 390 고정.
- 마일스톤 타임라인은 카드 목록(D6)이라 표 래퍼도 필요 없다.
- 대화상자: 폭 `max-w-[calc(100vw-2rem)]`, 생성 대화상자의 라인 입력은 390px에서 행 카드로 쌓는다.
- **증거**: jsdom은 레이아웃을 못 재므로 (1) 소스 계약 vitest + (2) 워크스루 실브라우저 실측(상세·목록·대화상자 `scrollWidth == 390`)을 PROGRESS에 기록한다(PR-16 방식, D16).

**결정 — 접근성**
- 대화상자는 `ConfirmDialog`/`useDialogBehavior`(포커스 트랩·Esc·초점 복귀)만 쓴다.
- 배지는 색만으로 뜻을 전하지 않는다(텍스트 필수 — "휴일", "미등록", "도과").
- 타임라인 `<ol aria-label="마일스톤">`, 각 카드 제목 `<h3>`, 표는 `<th scope="col">`.
- 저장 결과·재계산 안내는 `role="status"`/`aria-live="polite"`, 오류는 `role="alert"`.
- 입력 칸은 `<label>` 연결, 사유 칸은 `aria-required`, 서버 칸별 오류는 `aria-describedby`.
- 아이콘 버튼 금지(전부 글자 버튼) — 기존 화면 관례.

**근거**: `D:303`, `D:452`(렌즈 6), `D:457`(렌즈 11), PR-16 결함 ② `P:16`.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D14. PR-16 부채 ③ — 사용자 역할 변경 화면: **S3-2에 배정, 백엔드 변경 0**

**결정**
- S3-2 프런트 PR 하나(마지막 프런트 PR 또는 소단위 PR)에 `/settings/users`(ADMIN 전용)를 넣는다.
  - 목록: 기존 `GET /users`(ADMIN, Page — `code:modules/identity/router.py:90-97`). 열 — 이메일, 표시명, 활성, 역할 칩(한글 라벨: 관리자·무역·물류·인증·조회 — `code:modules/identity/models.py:52-62`).
  - 동작: 역할 부여 `POST /users/{id}/roles`, 회수 `DELETE /users/{id}/roles/{role}`, 활성·비활성 `PATCH /users/{id}/active`, 잠금 해제 `POST /users/{id}/unlock`. **전부 기존 API**(`:133-158`)이며 audit 기록이 이미 있다(`code:modules/identity/service.py:113-130`, 마지막 관리자 보호 `:572-625`).
  - 각 동작은 확인 대화상자(대상 이메일·변경 내용 표시). 본인 ADMIN 회수 시 경고문 "회수하면 이 화면에 다시 들어올 수 없습니다". `IDENTITY.LAST_ADMIN_PROTECTED`는 서버 메시지 그대로.
- **계정 생성 화면·API는 만들지 않는다.** 계정 생성은 지금 CLI `create-admin`뿐이다(`code:cli.py:145`). 운영 절차는 runbook에 "CLI로 계정 생성(관리자로 생김) → 화면에서 물류/무역 부여 → ADMIN 회수"로 적는다. 계정 생성 API는 인증 표면(비밀번호 초기값 전달·초대) 설계가 필요하고 문면 근거(§14 ⑭ "사용자")만으로 형태가 정해지지 않으므로 **부채로 남긴다**(트리거: 운영 개시 후 계정 개설 2건 이상 또는 사용자 요청).

**근거**: `P:32` ③("사용자 역할 변경 화면 부재 … 소유 S3-2 계획에서 배정 판정 / 트리거: 운영 개시"), sA §A11(LOGISTICS가 첫 전표 쓰기 역할이 되어 물류 계정 개설이 S3-2 운영 개시의 전제), `D:368`("권한변경은 audit 필수" — 기존 API가 충족), `D:303` ⑭.

**대안(기각)**
- (a) 운영 개시까지 미룸: 물류 역할을 여는 세션이 그 역할을 부여할 화면 없이 끝나 API 직접 호출(개발자 개입)이 계속된다.
- (b) 계정 생성까지 포함: 인증 표면 확장이 문면 없는 설계를 요구한다(추측 구현).

**자율 확정**: 자율 확정. **되돌리기 비용**: 낮음(화면 1개, 백엔드 0). 미루는 쪽의 비용은 운영 개시 시 개발자 개입 지속.

---

## D15. PR-16 부채 ⑤ — SearchSelect 오선택: **S3-2에서 고친다**(새 소비처가 생기므로)

**결정**: S3-2 화면이 SearchSelect를 새로 쓰는 곳(당사자·관세사·통보 상대)이 거래처 오선택을 만들면 선적 서류 당사자가 틀린다. 그래서 공용 컴포넌트(`fe:components/search-select.tsx`)를 **같은 S3-2 프런트 PR에서** 고친다: 디바운스 대기·요청 진행 중에는 이전 결과 목록을 숨기거나 비활성화하고 "검색 중"을 보이며, **결과가 현재 입력어에 대한 응답일 때만** 선택을 받는다(요청 시점 질의어와 현재 입력어 대조). 기존 소비처 회귀는 vitest(`search-select.test.tsx`)에 "빠른 입력 직후 클릭 → 선택 0" 케이스로 고정한다.

**근거**: `P:32` ⑤(워크스루 자동화가 실제로 첫 바이어를 잘못 골랐다 — 트리거 "SearchSelect 수정 세션"), `D:192` ⑤(검색형 선택의 신규 화면 적용).

**대안(기각)**: S3-2 신규 화면은 일반 `<select>`(size=200)로 우회 — 200건 초과 조용한 잘림 결함(S3-1 관찰 다수)을 되살린다.

**자율 확정**: 자율 확정. **되돌리기 비용**: 낮음(컴포넌트 1개, 동작은 더 보수적으로만 바뀜).

---

## D16. PR-16 부채 ⑦ — 브라우저 e2e 도구: **채택하지 않는다(의존성 추가 0)**, 렌즈 11 증거 경로를 고정한다

**결정**
- 리포에 `@playwright/test`·Python `playwright`를 **추가하지 않는다.** 프런트 의존성(`frontend/package.json`)·백엔드 requirements 무변경. CI에 브라우저 잡을 만들지 않는다.
- 대신 **렌즈 11(워크스루) 증거를 3층으로 고정**한다.
  1. **반복 가능한 HTTP e2e**(리포): `tests/e2e/test_s3_2_walkthrough.py` — 입구(SO 확정)→부분선적 2건(1:N 잔량)→SO 선적중→계획 초안→ETD·ETA 계획→롤오버(사유)+통보 기록→ETA 도착국 휴일 경고 / 미등록 국가 UNVERIFIED→수리 통관 기록→적재기한 산출→실적 입력 후 대금만기 재계산→선적 1건 취소(선적중 유지)→2건째 취소(확정 복귀). S3-1 `test_s3_1_walkthrough.py` 쌍(H·J) 선례.
  2. **소스 계약 vitest**(리포): 390px 그리드(D5)·날짜 문자열 `new Date` 금지(D12)·상태 라벨 단일 표·알림 이동 표.
  3. **실브라우저 관통 1회**(리포 밖): 이미 컨테이너에 있는 venv playwright와 `/opt/pw-browsers/chromium`(설치 0 — PR-16 방식 `P:14`)으로 스크래치패드 스크립트를 돌리고, 단계별 스크린샷과 `scrollWidth` 실측값·발견 결함을 PROGRESS 해당 PR 절에 **본문으로** 기록한다(위치 제시만으로는 미충족 — CLAUDE.md 완료 보고 수칙).
- 부채 ⑦은 **닫지 않고 트리거를 갱신**해 유지한다: "실브라우저에서만 잡힌 회귀가 2개 PR 연속 발생" 또는 "Phase 4 착수(피킹·검수 화면 — 스캔 입력 흐름)" 중 먼저. 그때 ADR로 채택 판정(도구·CI 시간·캐시).

**근거**: `P:32` ⑦("실브라우저 워크스루 스크립트 리포 미보존(의존성 추가 금지) — 소유 S3-2 계획(브라우저 e2e 도구 채택 판정)"), `P:14`(PR-16 판단 "남기면 의존성 추가가 된다"), `D:457`(렌즈 11은 "1회 관통"을 요구하지 도구를 요구하지 않는다), `D:374`·ADR-0073(CI 샤드·시간 예산).

**대안(기각)**
- (a) `@playwright/test` 채택 + CI 잡: 브라우저 바이너리 캐시·CI 시간 증가·의존성 공급망 표면이 늘고, 문면 근거 없이 CI 구성(ADR-0073 샤드 구조)을 바꾼다. 의존성 추가는 되돌리기도 비싸다(이후 테스트가 의존).
- (b) 스크립트를 리포에 두되 실행 안 함: 의존성 없는 죽은 코드 — 실행되지 않는 테스트는 증거가 아니다.

**자율 확정**: 자율 확정(엄격 쪽 = 새 실행 표면을 ADR 없이 열지 않음). **되돌리기 비용**: 낮음 — 채택은 나중에 가산(ADR+의존성+CI 잡)으로 가능하고, 지금 채택했다가 걷어내는 쪽이 비싸다.

---

## 테스트 배분 (§20 그룹 — 화면·API 몫. 데이터·산식 몫은 sA §A16·sB §B20)

| 그룹 | 케이스 |
|---|---|
| A | (e2e) 생성 대화상자 경로로 부분선적 2건 → 잔량 0, +1 → 409 칸별 잔량 표시 / 수입선적 응답·화면에 금액 키 0 |
| H | 롤오버 사유 없음 422(화면 제출 불가 + 서버 422 이중) / 통보 기록이 comm_logs SHIPMENT 1행 + 연결 1행, `/comm-logs` 역할 무변경(TRADE `POST /comm-logs` 여전히 403) |
| I | M6 통보 경로에 발송 코드 0(아웃바운드 클라이언트 임포트 0 아키텍처 테스트) |
| J | 생성 확정 더블클릭(같은 키) → 선적 1건 / 실적 입력 같은 키 2회 → 이력 1행·응답 동일 / version 409 표시 / 휴일 PUT 같은 키 2회 → 교체 1회 |
| K | authz 행 전부(선적·마일스톤·통관·휴일·OEM 마일스톤: ~~A·T·L 쓰기~~ **[통합 §2.9·X-14·X-16]** 동작별 역할(생성·라인·취소·OEM = A·T, 일정·실적·통관·당사자·출고지시 = A·T·L, 마일스톤 세트 = A — 적대 R-14), C·V 쓰기 403, 휴일 쓰기 A만) / `GOVERNED_PREFIXES` 등재 / Page 봉투 자동 스캔(S1·S13·S16·M5·M9·H1·H2) / 상세 쿼리 수 상한 / 부모-자식 404(다른 선적의 라인·통관·마일스톤 변경 id) / 휴일 CSV BOM·수식 이스케이프 |
| vitest | 타임라인: UNKNOWN 사유 한글·"예정 기준" 배지·UNVERIFIED 배지·링크·CLEAR 무배지·파생 행 버튼 0·CUSTOMS_CLEARED 실적 안내 / 생성 대화상자: 기본 수량 빈칸·[잔량 전부]·국가 필수·409 칸별 표시 / 보드 5열 / 문서 흐름 SHIPMENT 노드 / alert-routes `shipments` / `SHIPMENT_STATUS` 라벨·"기타" / `toZonedPairDisplay` / 날짜 문자열 `new Date` 금지 소스 계약 / `detail-layout` 하한 7 / SearchSelect 빠른 입력 오선택 0 / 사용자·역할 화면(ADMIN 외 메뉴 미노출·마지막 관리자 오류 표시) |
| 워크스루 | D16의 3층 증거 |

## DESIGN·ADR 부기 대상(이 부록 몫)

- §14 [M4] 보강 계열 부기(S3-2 화면 배정): 선적 목록/상세·생성 2단 대화상자(SO·PO 상세 진입, 독립 생성 화면 없음)·휴일 캘린더·사용자·역할(ADMIN)·보드 선적중 열·문서 흐름 SHIPMENT 노드·OEM 생산 일정 섹션. 선적 캘린더 뷰는 S4-4 유지 명기.
- §5.4/§7.9 경계 부기: 롤오버 통보는 선적 하위 전용 엔드포인트가 comm_logs(SHIPMENT)를 만든다 — `/comm-logs` 역할 무변경.
- ADR(번호는 통합이 0074~에서 부여): ① 브라우저 e2e 도구 미채택과 렌즈 11 3층 증거(D16) ② 사용자 역할 화면 배정·계정 생성 API 미신설(D14) — ①②는 한 ADR로 묶어도 된다.

## 멈춰서 보고할 항목(이 부록 범위)

1. ~~**수리일 입력처**~~ **[통합 X-02로 해소 — 통관 기록이 유일 원천, 마일스톤 비복사·읽기 시 파생]**: sA §A8(`customs_records.accepted_on`이 원천)과 sB §B7(마일스톤 실적이 단일 원천) 표현이 갈린다. 화면은 sB §B7 (ii)안(통관 기록이 유일 입력처, 마일스톤 실적은 동TX 복사)을 가정했다(§0 가정 5). 통합이 확정해야 한다.
2. **마일스톤 쓰기 메서드 표기**: sB §B8 ⑥(POST)과 GC-21(PUT)이 갈린다 → POST로 확정(D2-2).
3. **알림 entity_type**: sB §B13 dedup 키는 마일스톤 id 기준이다. 화면은 알림 대상 = 소유 전표를 가정했다(§0 가정 6, D11).

## 부채 등재 후보(이 부록)

- 계정 생성 화면·API 부재(D14) — 트리거: 운영 개시 후 계정 개설 2건 이상 또는 사용자 요청.
- PR-16 ⑦ 브라우저 e2e 도구 — 유지, 트리거 갱신(D16).
- 수입선적의 문서 흐름 노드 없음(PO가 흐름에 없음, D6 ⑩) — 트리거: PO를 문서 흐름에 넣는 세션(S4-1 입고 문서).
- 오더 보드 카드의 선적 요약(건수·다음 ETD) 미표시(D8) — 트리거: 사용자 요구.

**실행 검증 못 했음.** 이 부록은 정적 독해로 작성했다. 엔드포인트·스키마·화면은 구현 PR의 첫 커밋에서 실측해 기록한다.
