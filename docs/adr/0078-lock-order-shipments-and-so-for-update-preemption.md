# ADR-0078: LOCK_ORDER 개정(…PO → shipments → shipment_children → approvals → lines → seq)·SHARE→UPDATE 승격 금지·SO `FOR UPDATE` 선점·PO `FOR SHARE`는 수입선적 생성만

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §8.3(문면 변경)·§17.2(S3-2 [M4] 보강) / ADR-0059 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sC §C2·C3, sA §A10, X-09·X-10, R-08 / 구현 PR-3a(PR-4a·4c는 하위 기록 순서 소비)

**맥락** — §17.2 S3-1 부기 ②의 순서표에 선적·선적 하위 기록이 없고(변경은 ADR), §8.3 S3-1 부기 ②는 "선적·입고 소비 = 상위 헤더 `FOR SHARE`→라인 `FOR UPDATE`"다. 그러나 선적 생성은 같은 TX에서 SO 상태 수렴(ADR-0075)을 하므로 SO를 SHARE로 잡은 두 TX가 UPDATE로 승격하면 40P01 교착이 구조적으로 난다. 당사자·통관 기록 쓰기는 거래처 검증 잠금을 선적 뒤에서 잡을 위험이 있다(R-08).

**결정** — ① 순서: 멱등 claim → 인테이크 → **거래처** → QT → PI → SO → PO → **`shipments` → `shipment_children`(milestones·customs_records·shipment_parties)** → approvals → 라인(원천 라인 → 선적 라인, id 순) → `doc_number_seq`. ② 모드(경로별): **T1 수출 생성 = SO `FOR UPDATE` 선점**(그 뒤 `lock_lines_for_consumption`의 SHARE는 흡수 — **승격 금지**) / **T2 수입 생성만 PO `FOR SHARE`** / **선적 기점 경로(라인 편집·취소 T4·T5)는 `lock_chain` 그대로 SO·PO 모두 `FOR UPDATE`** / **T8·T9(당사자·통관) = 멱등 → 거래처 `FOR KEY SHARE`(id 순) → shipments → shipment_children** / T13 OEM = PO `FOR SHARE` → milestones `FOR UPDATE`. ③ 휴일 2표·`comm_logs`는 순서표 밖(교차 없음). ④ 담당 이관 `ASSIGNMENT_TARGETS`에 shipments를 purchase_orders 뒤로. ⑤ `locking.py` 독스트링·DESIGN §17.2·§8.3 부기를 같은 PR에서 고치고 J-07 계측 테스트에 T8·T9를 명시한다.

**근거** — 선점으로 SHARE→UPDATE 승격 교착을 구조적으로 없앤다. PO는 상태를 바꾸지 않으므로 SHARE로 충분하고 PO 취소(`FOR UPDATE`)와 직렬화된다. 부분수열 허용·순서 위반만 금지라는 ADR-0059 원칙을 그대로 승계한다.

**기각한 대안** — SHARE 유지 + 수렴을 별도 TX(§17.1 위반), PO도 `FOR UPDATE`로 통일(불필요하게 강함 — X-10), `ANCESTORS`에 경로별 모드를 싣기(헬퍼 확장 대비 실익 없음 — 교착 무관), 당사자 쓰기에서 거래처 검증을 선적 잠금 뒤에.

**되돌리기 비용** — **중간**(교착 재검증 필요 — 계측 테스트가 비용을 줄인다). 잠금 강도 낮추기는 헬퍼 1곳으로 낮음. DESIGN §8.3 부기 문면 변경이라 번복 시 부기 원복이 같이 움직인다.
