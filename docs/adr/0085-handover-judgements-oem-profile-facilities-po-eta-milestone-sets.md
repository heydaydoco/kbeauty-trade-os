# ADR-0085: 인계 판정 묶음 — OEM `profile_id` 미신설·facilities 미신설 유지·PO 라인 ETA 열 미신설(계산값)·마일스톤 세트 `item_profile_milestone_types`+쓰기 경로(ADMIN 전용)

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §3·§4.8·§7.5(S3-2 [M4] 보강) / WBS S3-2 인계(v1.6 주석) / ADR-0021·0037 / PROGRESS P-05·P-06·P-57·부채 #15 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B15~B17, N-01, R-14 / 구현 PR-4a(표)·PR-4c(쓰기 경로·OEM)·PR-5a(PO ETA 계산값)

**맥락** — WBS S3-2는 OEM 마일스톤 프로파일(`profile_id`)·PO 라인 ETA 슬롯을 '판정'하라고 하고, P-57은 facilities 재판정 트리거 (a)를 S3-2 OEM 계획으로 둔다. ADR-0021은 item_profiles의 마일스톤 몫을 S3-2에 남겼다(부채 #15). DESIGN이 정한 OEM 프로파일은 4단계 1종뿐이고, PO 라인 ETA는 수입선적 ETA에서 계산할 수 있다.

**결정** — ① **OEM `profile_id`·프로파일 마스터 미신설** — OEM 생산 마일스톤은 `milestones`의 PO 소유 행(코드 고정 4종 RAW_MATERIAL_READY·FILLING·PACKING·OUTGOING_INSPECTION ⇔ `po_kind='OEM_PRODUCTION'`, 쓰기 A·T, 알림 없음 — 표시만). 재트리거: 두 번째 프로파일 요구. ② **facilities 미신설 유지** — P-57 트리거 (a) 불발동(4단계 일정은 PO=거래처 1곳 단위로 충분), (b)·(c) 유지. ③ **PO 라인 ETA 열 미신설** — 입고예정 = 그 라인을 참조하는 살아 있는 수입선적 ETA 유효값 중 **가장 늦은 날짜**(계산값, 없으면 '입고예정 미정'), SO `requested_delivery_date`는 참고 표시만(비교 규칙 부채). 재트리거: S3-4/P4 라인 단위 예정일 요구. ④ **마일스톤 세트 `item_profile_milestone_types`**(품목군 × 선적 저장형 8종) 신설 + 쓰기 경로 `/item-profiles/{id}/milestone-types`(**ADMIN 전용** — CERT 미배정 사유: §2 인증 편집 = 시장·요건 템플릿, 마일스톤은 물류·무역 업무 / `GOVERNED_PREFIXES` 등재), 중복 409 `MILESTONE.DUPLICATE_TYPE`·파생/OEM 종류 422 `TYPE_NOT_APPLICABLE` 재사용. 선적 계획 초안(사람 1클릭) = 구분별 적용 집합 ∩ 라인 SKU 품목군 세트 합집합(세트 없으면 구분별 전부 — 누락보다 과다). 부채 #15 마일스톤 몫 종결.

**근거** — 프로파일이 1개뿐인 마스터는 죽은 일반화이고 화면·권한·시드가 딸려 온다. 계산값은 원천 이중화를 막고 FREE 4열 핀을 지킨다. 세트 쓰기는 좁은 쪽(ADMIN)에서 시작한다.

**기각한 대안** — 프로파일 마스터+`profile_id` 지금 신설, facilities 신설, `purchase_order_lines.expected_receipt_on`+FREE 확장(FREE 핀 ADR·원천 이중화), 세트를 구분 축에만(§4.8 미이행), SO 확정 시 계획 자동 생성(확정 통로 부작용), 세트 쓰기 = CERT.

**되돌리기 비용** — 전부 **낮음**: `profile_id`·ETA 열은 nullable 가산, facilities는 트리거 시 신설, 세트 쓰기 역할은 권한 행 1줄. WBS 문면 '판정'의 결과라 PROGRESS에 자율 확정으로 표기.

**부기(2026-10-04 — S3-2 PR-4a 이행: 표 선생성·계획 초안)** — M15에 `milestones`의 PO 소유 열(`po_id` — OEM 4종 ⇔ PO 소유 CHECK)과 `item_profile_milestone_types`(품목군 × 선적 저장형 8종 CHECK, 살아 있는 (품목군, 종류) 유일 — 409 `MILESTONE.DUPLICATE_TYPE`)를 만들었다. **쓰기 경로는 0**(① OEM API·④ 세트 쓰기는 PR-4c — 표는 비어 있다). 계획 초안 `POST /shipments/{id}/milestones/plan-draft`(사람 1클릭, A·T·L)는 구분별 적용 집합 ∩ 라인 SKU 품목군 세트 합집합이고, **품목군이 없거나 세트가 빈 SKU가 하나라도 있으면 구분별 전부**(누락보다 과다 — 자율 확정), 이미 있는 종류는 건너뛴다(값 없는 행 — 이력 0).

**부기(2026-10-04 — S3-2 PR-4c 이행: OEM 쓰기 경로·세트 쓰기 경로)** — ①·④의 쓰기 경로를 열었다(마이그레이션 0 — 표는 M15). ① OEM 4종은 OEM 생산 PO(`po_kind=OEM_PRODUCTION`)에만 — 일반 구매 PO의 보드·계획·실적·이력 = 422 `SHIPMENTS.MILESTONE.OWNER_NOT_OEM`(신설, 빈 보드로 숨기지 않는다), PO 경로의 선적 종류 422 `TYPE_NOT_APPLICABLE`·파생 422 `DERIVED_NOT_EDITABLE`, 선적 경로의 OEM 종류 422. 교차 표 규칙이라 아키텍처 시험이 'PO 소유 행에 닿는 공개 경로 4개가 OEM 판정을 부른다'를 소스로 고정한다(B15). 보드 = 원료수급 → 충진 → 포장 → 출하검사 4행(선적 보드와 같은 행 모양 — 알림·휴일 배지 0, 롤오버 배지 대상[ROLLOVER_TYPES] 밖 → 롤오버 횟수·미통보 0). ④ 세트 = 관리자 전용 추가(멱등 키·201)·제거(soft delete·재추가 = 신규)·전 역할 조회(업무 흐름 순), 중복 409 `DUPLICATE_TYPE`·파생/OEM 종류 422 `TYPE_NOT_APPLICABLE`(서비스 1차 + DB 번역 2차). 세트를 쓰면 다음 선적 계획 초안부터 교집합이 적용된다(실기동 관통: 세트 ETD·ETA·IMPORT_TAX_DUE → 수출선적 초안 = ETA·ETD).

**부기(2026-10-04 — S3-2 PR-5a 이행: PO 라인 입고예정 계산값)** — ③을 이행했다(PO 라인 열 0 — 마이그레이션 0): PO 상세 라인 `expected_receipt = {status, value, basis, shipment_count, unscheduled_count}` — 정의는 순수 함수 `schedule.expected_receipt` 하나, 입력은 커널 `receipts.po_line_receipts`(배정 가능량과 **같은 소비자 등록**에서 살아 있는 수입선적을 읽고 ETA 유효값[실적 우선 — `schedule.effective`]을 질의 1회로 모음). 대표값 = 가장 늦은 ETA(SCHEDULED). **더 엄격하게 자율 확정**(design-D X4 'date|null' 대비): ETA가 없는 선적이 하나라도 있으면 UNSCHEDULED·값 없음(아는 날짜 중 가장 늦은 값으로 대신 채우지 않음 — 가장 늦은 날을 알 수 없다), 선적 없음은 null 대신 NONE('입고예정 미정' — null은 이 필드 이전의 멱등 재생 본문뿐), basis = 전 선적 ETA가 실적이면 ACTUAL·하나라도 계획이면 PLANNED. 배정되지 않은 수량(배정 가능량 > 0)은 이 값이 덮지 않는다(화면이 함께 보인다 — PR-5b). 되돌리기 비용: 낮음(순수 함수 1개·응답 필드).
