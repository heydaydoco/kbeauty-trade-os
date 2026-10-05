# ADR-0084: `trade-deadline-scan` daily@06:40 — 선적 마일스톤 4종+QT/PI 만료 임박 D-N 동잡·trade_chain 배치·dedup 키 `deadline:shipments:{id}:{TYPE}/…@기일`·시각형 도과 = UTC 비교·대금만기·제시기한 알림 제외

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.5·§15(S3-2 [M4] 보강) / ADR-0046·0058 / PROGRESS P-03 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B13·B18, sC §C11, X-25, R-17·R-20 / 구현 PR-6(JOB 13→14)

**맥락** — 기일 사고 방지(§7.5·ADR-07 알림 정확성)를 위해 선적 기일 알림이 필요하다. 기존 `deadline-scan`(인증·문서)은 플랫폼 모듈이라 전표를 임포트할 수 없다(임포트 방향). QT/PI 만료 임박(P-03)은 같은 기일 엔진 착수 시 판정하기로 돼 있다. 시각형 기한을 KST 날짜로 도과 판정하면 기한 전 최대 ~16시간 '도과'로 오표시된다(R-20).

**결정** — ① 잡 1행 `trade-deadline-scan` daily@06:40 KST(06:30 deadline-scan 뒤·07:00 앞), 본체 `trade_chain/deadline_scan.py`(L2 — 순수 함수와 `notifications.notify`만 임포트), CLI 수동 실행 겸용. ② 대상: 죽지 않은 선적의 DOC_CUTOFF·CARGO_CLOSING·IMPORT_TAX_DUE(계획 있음·실적 없음 — 충족 = 실적), LOADING_DEADLINE(파생 OK — 충족 = 적재 이행일, ADR-0080) + ISSUED QT/PI `valid_until` D-N(후보 = 만료 스윕 후보와 같은 정의, 살아 있는 후속 있으면 제외). ③ 문턱 = `alert_rules` 데이터(없으면 코드 기본 D-7/3/1+도과, `deadlines._policy` 공개 승격 — 기존 2축 결과 불변 회귀 테스트). S2-3 의미론 승계: '지났다' 판정·도과 시 지난 문턱 소급 없음·D-3 에스컬레이션·수신자 담당자→규칙→ADMIN 폴백. ④ dedup `deadline:shipments:{shipment_id}:{TYPE}/{문턱}@{기일}:{수신자}` — 롤오버로 기일이 바뀌면 새 알림. ⑤ **도과: 시각형 = `now_utc > effective_at`, 날짜형 = `today_kst() > 날짜`**(D-N 문턱은 `scan_date` 유지). ⑥ **PAYMENT_DUE·PRESENTATION_DEADLINE·OEM 마일스톤 알림 제외**(충족 신호 S3-3 — 부채). ⑦ 건별 독립 TX, 1건 실패 → 잡 FAILED. 4금: 전표 상태 무변경·알림 생성만. 이름에 `purchase`·`발주`·`-po-` 미사용.

**근거** — trade_chain 배치는 임포트 방향(플랫폼 → 전표 금지)을 지키면서 화면과 같은 순수 함수를 쓴다(정의 이원화 방지). 충족 신호가 없는 기일을 알리면 이미 입금된 건에 도과 알림이 나간다.

**기각한 대안** — 기존 `deadline-scan`에 합치기(임포트 방향 위반), 대금만기도 알림(오경보), 문턱을 정책 키로(`policy_settings`에 일수 목록 형 없음), 마일스톤 행 id 기준 dedup(파생 LOADING_DEADLINE은 행 없음 — X-25), 시각형 도과를 날짜 비교로.

**되돌리기 비용** — 낮음(`enabled=false`·문턱은 데이터). 대상 종류 확대는 중간(dedup 키 계보 — 가산만).

**부기(2026-10-05 — S3-2 PR-6 이행)** — (위 원문 결정은 고치지 않는다.) ①~⑦ 이행: 본체 `trade_chain/deadline_scan.py`(`scan_trade_deadlines(now=…)`) · 잡 `trade-deadline-scan` daily@06:40(JOB **14행**) · CLI `python -m app.cli trade-deadline-scan`. 판정은 화면과 같은 `milestone_view.assemble`(기준 시각 주입 인자 신설 — 한 실행 = 한 시각)을 재사용하고, `deadlines._policy`는 `policy(…, defaults=)`로 공개 승격(인증·문서 2축 기본 D-180/90/30 불변 — 회귀 시험), 미확인 판정은 `deadlines.has_unacknowledged_alert`(LIKE 와일드카드 이스케이프 — 종류 세그먼트의 `_` 오매칭 방지)로 공용화. 견적·PI 후보 상태는 `trade_docs.expiry.EXPIRY_CANDIDATE_STATUS` 단일 출처(만료 스윕과 공유). 마이그레이션 0(멱등 = 기존 `alerts.dedup_key` 부분 유니크). **이행 중 자율 확정(더 엄격·fail-visible 쪽)**: ⓐ 시각형 기일의 키 표기 = UTC 초 `YYYY-MM-DDTHHMMSSZ`(같은 날 안의 시각 롤오버도 새 기일 = 새 알림, 콜론 없음 — 키 `:` 구분 규약 유지) ⓑ 시간대 해석 불가(TZ_UNRESOLVED) = D-N·도과 추정 대신 '무역 기일 판정 불가' 알림 1회(`…/UNRESOLVED@기일`) ⓒ 에스컬레이션은 **스캔일 KST 0시 이전에 만든** 미확인 기일 알림만 근거(같은 날 재실행이 방금 만든 알림으로 관리자를 부르지 않는다 — S2-3 "받을 틈이 없었다" 취지, 실기동 재실행에서 발견) ⓓ 견적·PI에도 D-3 에스컬레이션 적용 ⓔ 등급 = D-3 이하·도과·에스컬레이션·판정 불가 CRITICAL, D-7 WARN ⓕ CLI에 기준일 인자 없음(시각형 도과는 '지금' UTC 비교라 날짜만 바꾼 실행은 기준이 갈리고, 미래 기준일 알림은 dedup 자리를 선점) ⓖ 후보 id는 keyset 페이지(500)·건별 독립 TX·열린 unit_of_work 합류 거부. 대상 종류 집합(4종)은 시험으로 고정. 근거: PROGRESS 'S3-2 PR-6' 절.
