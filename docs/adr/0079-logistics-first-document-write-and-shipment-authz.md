# ADR-0079: 권한 — LOGISTICS 첫 전표 쓰기(동작별)·휴일 쓰기 ADMIN 전용·OEM 마일스톤 A·T·출고지시 전용 경로·담당 이관 등록·마일스톤 세트 쓰기 ADMIN 전용

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §2(S3-2 [M4] 보강 — 문면 변경) / ADR-0067 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sC §C6, sA §A11, X-14~X-16, R-14 / 구현 PR-2a(휴일)·PR-3a·PR-4a·PR-4c

**맥락** — §2는 역할 5종을 두지만 물류(LOGISTICS)에 업무를 배정하지 않았고 S3-1까지 전 전표 쓰기는 무역(`CAN_WRITE=(TRADE,)`)이었다. 선적 일정·실적·통관·당사자는 물류 실무다. 반대로 참조 생성·수량은 원천 전표(SO·PO)의 무역 책임이다. 마일스톤 세트(§4.8 item_profiles)는 기존 품목군 쓰기 선례가 인증(CERT)이지만 §2의 인증 편집은 시장·요건 템플릿 한정이다.

**결정** — 동작별 좁은 쪽(기계 정본 `AUTHZ_MATRIX`): ① 조회(선적·마일스톤·변경 이력·통관·휴일·마일스톤 세트·export.csv) = 전 역할 ② 선적 생성(SO·PO 참조)·라인·취소 = A·T ③ 헤더(FREE·국가)·**출고지시(전용 `release-order`)**·당사자·통관 기록·선적 마일스톤 계획/실적/초안·통보 = A·T·**L** ④ OEM 생산 마일스톤(PO 소유) = A·T ⑤ 휴일 연도 선언·원자 교체·CSV 미리보기 = **A 전용** ⑥ **마일스톤 세트 쓰기 = A 전용**(CERT 미배정). ⑦ `GOVERNED_PREFIXES` += `/api/v1/shipments`·`/api/v1/holidays`·`/api/v1/item-profiles/{profile_id}/milestone-types`(행 누락 = 완전성 테스트 실패). ⑧ 부모-자식 경로 불일치 404·부작용 0, 401→403→404→409→422, 쓰기 스키마 `extra="forbid"`. ⑨ 담당 이관 대상에 shipments 등록(ADR-0078 순서).

**근거** — 넓히기는 행 1줄로 싸고 좁히기는 운영 중 회수라 비싸다(좁은 쪽 원칙). 전표는 회사 공유 자산(§18.1 부기)이라 소유자 한정은 두지 않는다. 휴일은 기일 판정 전체에 영향을 주는 기준 데이터라 관리자 전용.

**기각한 대안** — A·T·L 전 동작 쓰기(물류가 수량·취소까지 — 원천 책임 혼재), 물류 쓰기 0(선적 실무 주체 부재 — 무역이 대행 입력), 마일스톤 세트 = CERT(§2 인증 편집 범위 밖 — R-14), 휴일 = A·T.

**되돌리기 비용** — 넓히기 낮음(권한 행 1줄) / 좁히기 중간(운영 중 역할 회수 공지). **오너 판정 권장 1순위**(물류 역할의 업무 배정이 처음 문면화됨) — 번복 시 이 ADR을 "대체" 표기.

**부기(2026-10-04 — S3-2 PR-2a 이행: 휴일 권한)** — 휴일 행만 이행했다: `GET /holidays/calendars`·`GET /holidays`·`GET /holidays/{country}/{year}/export.csv` = 전 역할, `PUT /holidays/{country}/{year}`(원자 교체)·`POST /holidays/{country}/{year}/import-csv/preview` = **ADMIN 전용**(`require_roles(ADMIN)` + `AdminUser` — 역할 검사가 입력·존재 검사보다 먼저: 물류가 틀린 국가·본문으로 PUT해도 403). `/api/v1/holidays`를 `GOVERNED_PREFIXES`에 등재하고 5행을 매트릭스에 더했으며, "표의 모든 행은 통제 접두어 아래" 단언을 더해 접두어 삭제가 조용히 통과하지 않게 했다(변이 kill). 선적·마일스톤 행(LOGISTICS 첫 전표 쓰기)은 PR-3a·4a·4c 몫 그대로.

**부기(2026-10-04 — S3-2 PR-3a 이행: 선적 권한)** — 선적 행을 이행했다: 조회 3종(`GET /shipments`·`/{id}`·`/{id}/status-log`) = 전 역할, SO 참조 생성·미리보기(`POST /sales-orders/{so_id}/shipments[/preview]`)·라인 추가/수정/삭제·취소(`/transitions`) = **A·T**, 헤더 편집(`PATCH`)·**출고지시(전용 `POST /{id}/release-order`)**·당사자 추가/제외 = **A·T·L**(LOGISTICS 첫 전표 쓰기). `GOVERNED_PREFIXES += /api/v1/shipments`, 매트릭스 13행(선적 11 + SO 하위 2 — 후자는 SO 접두어가 통제). 응답 `allowed_actions`(RELEASE_ORDER·CANCEL·EDIT_LINES·EDIT_COUNTRIES·EDIT_META·EDIT_PARTIES)가 같은 역할 규칙을 화면에 내린다. 부모-자식 경로 불일치(다른 선적의 라인·당사자) 404·부작용 0, 쓰기 스키마 전부 `extra="forbid"`·원천 값 필드 없음(아키텍처 시험). 담당 이관 대상 등록(⑨).

**부기(2026-10-04 — S3-2 PR-4a 이행: 마일스톤·통관 권한)** — ①·③의 선적 마일스톤·통관 행을 이행했다(매트릭스 +10행 — `/api/v1/shipments` 통제 접두어 아래): 조회 3종(`GET /{id}/milestones`·`/milestone-changes`·`/customs-records`) = 전 역할, 계획 초안·계획·실적(`POST /{id}/milestones/plan-draft`·`/{type}/plan`·`/{type}/actual`)·통보(`POST /{id}/milestone-changes/{change_id}/notices`)·통관 추가/정정/삭제 = **A·T·L**(라우터 `require_roles` — 403이 존재·입력 검사보다 먼저). 오류 순서 401→403→404→409→422(⑧)를 e2e로 고정했다(없는 선적 + 파생 종류·미래 실적·빈 요지·구분 불일치 본문 = 404, 취소된 선적 + 미래 실적·빈 요지 = 409 `OWNER_NOT_ACTIVE` — 통보·통관은 거래처 잠금 전 무잠금 peek). 다른 선적의 이력·통관 기록 id = 404·부작용 0. 응답 `allowed_actions` += `EDIT_MILESTONES`·`PLAN_DRAFT`·`EDIT_CUSTOMS`(A·T·L, 계획·출고지시 중). ④ OEM 생산 마일스톤·⑥ 마일스톤 세트 쓰기는 PR-4c 몫 그대로.

**부기(2026-10-04 — S3-2 PR-4c 이행: OEM 마일스톤·마일스톤 세트 권한)** — ④·⑥·⑦을 이행했다(매트릭스 +7행). OEM 생산 일정 `GET /purchase-orders/{po_id}/milestones`·`/milestone-changes` = 전 역할(원가 키 0), `POST …/milestones/{type}/plan`·`/actual` = **A·T**(물류·인증·조회 403 — 물류의 PO 쓰기 0, X-16). 품목군 마일스톤 세트 `GET /item-profiles/{profile_id}/milestone-types` = 전 역할, `POST`·`DELETE …/{link_id}` = **ADMIN 전용**(`require_roles(ADMIN)`+`AdminUser` — 무역·물류·인증·조회 403이 존재·입력 검사보다 먼저). `GOVERNED_PREFIXES` += `/api/v1/item-profiles/{profile_id}/milestone-types`(접두어를 빼면 '표의 모든 행은 통제 접두어 아래' 단언이 실패 — 변이 kill). ⑧ 오류 순서: OEM = PO 404 → 취소 409 → 행 version 409 → 일반 구매 PO 422 `OWNER_NOT_OEM` → 종류 422(파생·선적 종류), 세트 = 품목군 404(서류 세트 선례 422보다 엄격) → 중복 409 → 비적용 422. 응답 `allowed_actions`(OEM 보드 — EDIT_MILESTONES: A·T, 발행·공급사 확인 중).

**부기(2026-10-04 — S3-2 PR-5a 이행: 수입선적 생성 권한)** — `POST /purchase-orders/{po_id}/shipments[/preview]` = **A·T**(배정 가능량 소비 = 상업 사실 — 물류 쓰기 아님, X-14). 매트릭스 +2행(`/api/v1/purchase-orders` 통제 접두어 아래 — 행만 추가). 수입선적의 헤더·출고지시·당사자·마일스톤·통관 권한은 수출과 같다(경로 단위). 오류 우선순위 404→409→422(⑧): 없는 PO + 잘못된 역할 = 404, 취소 PO + 잘못된 역할 = 409 `DOCUMENT_NOT_CONSUMABLE`(거래처 잠금 전 무잠금 peek).
