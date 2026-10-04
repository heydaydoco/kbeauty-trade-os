# ADR-0084: `trade-deadline-scan` daily@06:40 — 선적 마일스톤 4종+QT/PI 만료 임박 D-N 동잡·trade_chain 배치·dedup 키 `deadline:shipments:{id}:{TYPE}/…@기일`·시각형 도과 = UTC 비교·대금만기·제시기한 알림 제외

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.5·§15(S3-2 [M4] 보강) / ADR-0046·0058 / PROGRESS P-03 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B13·B18, sC §C11, X-25, R-17·R-20 / 구현 PR-6(JOB 13→14)

**맥락** — 기일 사고 방지(§7.5·ADR-07 알림 정확성)를 위해 선적 기일 알림이 필요하다. 기존 `deadline-scan`(인증·문서)은 플랫폼 모듈이라 전표를 임포트할 수 없다(임포트 방향). QT/PI 만료 임박(P-03)은 같은 기일 엔진 착수 시 판정하기로 돼 있다. 시각형 기한을 KST 날짜로 도과 판정하면 기한 전 최대 ~16시간 '도과'로 오표시된다(R-20).

**결정** — ① 잡 1행 `trade-deadline-scan` daily@06:40 KST(06:30 deadline-scan 뒤·07:00 앞), 본체 `trade_chain/deadline_scan.py`(L2 — 순수 함수와 `notifications.notify`만 임포트), CLI 수동 실행 겸용. ② 대상: 죽지 않은 선적의 DOC_CUTOFF·CARGO_CLOSING·IMPORT_TAX_DUE(계획 있음·실적 없음 — 충족 = 실적), LOADING_DEADLINE(파생 OK — 충족 = 적재 이행일, ADR-0080) + ISSUED QT/PI `valid_until` D-N(후보 = 만료 스윕 후보와 같은 정의, 살아 있는 후속 있으면 제외). ③ 문턱 = `alert_rules` 데이터(없으면 코드 기본 D-7/3/1+도과, `deadlines._policy` 공개 승격 — 기존 2축 결과 불변 회귀 테스트). S2-3 의미론 승계: '지났다' 판정·도과 시 지난 문턱 소급 없음·D-3 에스컬레이션·수신자 담당자→규칙→ADMIN 폴백. ④ dedup `deadline:shipments:{shipment_id}:{TYPE}/{문턱}@{기일}:{수신자}` — 롤오버로 기일이 바뀌면 새 알림. ⑤ **도과: 시각형 = `now_utc > effective_at`, 날짜형 = `today_kst() > 날짜`**(D-N 문턱은 `scan_date` 유지). ⑥ **PAYMENT_DUE·PRESENTATION_DEADLINE·OEM 마일스톤 알림 제외**(충족 신호 S3-3 — 부채). ⑦ 건별 독립 TX, 1건 실패 → 잡 FAILED. 4금: 전표 상태 무변경·알림 생성만. 이름에 `purchase`·`발주`·`-po-` 미사용.

**근거** — trade_chain 배치는 임포트 방향(플랫폼 → 전표 금지)을 지키면서 화면과 같은 순수 함수를 쓴다(정의 이원화 방지). 충족 신호가 없는 기일을 알리면 이미 입금된 건에 도과 알림이 나간다.

**기각한 대안** — 기존 `deadline-scan`에 합치기(임포트 방향 위반), 대금만기도 알림(오경보), 문턱을 정책 키로(`policy_settings`에 일수 목록 형 없음), 마일스톤 행 id 기준 dedup(파생 LOADING_DEADLINE은 행 없음 — X-25), 시각형 도과를 날짜 비교로.

**되돌리기 비용** — 낮음(`enabled=false`·문턱은 데이터). 대상 종류 확대는 중간(dedup 키 계보 — 가산만).
