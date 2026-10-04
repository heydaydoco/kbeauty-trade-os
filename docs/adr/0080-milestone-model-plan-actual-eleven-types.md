# ADR-0080: 마일스톤 모델 — 계획/실적 이중값 행·§7.5 9→11종(BL_ISSUED 저장형 추가·PRESENTATION_DEADLINE 파생형 편입)·날짜형/시각형·파생 3종 비저장·덮어쓰기 금지·수리일 = 통관 기록 MIN 파생·적재 이행일 = ETD·BL 실적 MAX(가정)

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.5(S3-2 [M4] 보강 — 문면 변경) / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B1~B4·B7·B8, X-01·X-02, R-03·R-06·R-10·R-18·R-25·R-29 / 구현 PR-2a(순수 함수)·PR-4a(표·API)

**맥락** — §7.5 마일스톤 9종에는 B/L 발행일이 없는데 제시기한 산식 MIN(B/L일+21, 유효기일)은 B/L일을 요구하고, 제시기한은 '자동 계산' 항목이지 마일스톤 목록이 아니다. 계획·실적·재계산·롤오버 이력을 한 값으로 덮어쓰면 이력이 사라진다. 수리일은 통관 기록과 마일스톤 실적 양쪽에 둘 수 있어 원천이 이원화될 위험이 있다(X-02). 문면은 적재 사실의 원천을 정하지 않는다.

**결정** — ① 종류: **저장형 8**(DOC_CUTOFF·CARGO_CLOSING·PSI·CUSTOMS_CLEARED·ETD·**BL_ISSUED[추가]**·ETA·IMPORT_TAX_DUE) + OEM 4 / **파생형 3**(LOADING_DEADLINE·PAYMENT_DUE·**PRESENTATION_DEADLINE[편입]** — 저장 안 함, DB CHECK가 거부, 쓰기 422 `DERIVED_NOT_EDITABLE`). ② 행 = 소유자(선적 또는 PO 정확히 하나)×종류 1행, **계획/실적 이중값**, 날짜형(현지 DATE)·시각형(UTC+IANA tz, `scan_date`·`local_date` 응답 필드 구분). 값 변경은 이력(ADR-0083), 덮어쓰기 경로 0. ③ 실적 미래 거부: 날짜형 ≤ KST 오늘+1일, 시각형 `actual_at ≤ now_utc`(여유 0). ④ **`CUSTOMS_CLEARED` 실적 = 살아 있는 통관 기록 `MIN(accepted_on)` 읽기 시 파생**(비복사, 직접 입력 422·CHECK), 미수리 기록 ≥1이면 `customs_state=PARTIAL`+'일부 미수리 n건'(산식 MIN 유지). ⑤ 산식 입력 결제조건은 선적 헤더 사본(ADR-0074). 수입 ORDER_DATE = PO `frozen_at`의 KST 날짜. ⑥ **적재 이행일 = ETD·BL_ISSUED 실적 중 존재값의 MAX — 가정**(근거: 둘 다 있으면 늦은 쪽이라 MET_LATE를 놓치지 않는다).

**근거** — 산식이 요구하는 날짜를 종류로 두지 않으면 사람이 매번 별도 입력하거나 ETD로 대체하게 된다(대체 금지 — GC-A13). 파생값을 저장하지 않으면 입력 재생으로 언제든 같은 값이 나와 감사 가능하고 재계산 누락이 없다. 수리일 단일 원천이면 정정이 한 곳에서 끝난다.

**기각한 대안** — 9종 유지 + B/L일 별도 열(FREE 4열 핀 깨짐), 파생값 저장+재계산 잡(이원화·누락), 마일스톤 실적에 수리일 동TX 복사(백필·불일치), 이행일 = BL 단독(B/L 지연 시 결측)·ETD 단독(본선적재일 불일치 누락).

**되돌리기 비용** — 종류 가산은 낮음(열거·CHECK 1줄 — 제거는 데이터 이전). 이행일 가정 변경은 낮음(함수 1개). 수리일 복사형 전환은 낮음(조립 함수 1개+백필 1회). §7.5 문면 확장이라 번복 시 DESIGN 부기 원복이 같이 움직인다.

**부기(2026-10-04 — S3-2 PR-2a 이행: 산식 순수 함수 층)** — `trade_docs/schedule.py`(L0, DB·세션·시계 무의존 — 임포트 허용 목록·시계 호출 금지를 아키텍처 시험 `test_schedule_purity.py`가 고정)에 파생 3종의 산식을 두었다: `payment_due`(결제유형 분기 — 100% 선수금 NOT_APPLICABLE `NO_BALANCE`, 앵커 결측 UNKNOWN `ANCHOR_PENDING`, 인보이스 `INVOICE_NOT_ISSUED`·입고 `RECEIPT_NOT_RECORDED` 대체 금지, ORDER_DATE = 확정·발행 시각의 KST 날짜[수입 = PO `frozen_at` — `AnchorContext`에 `doc_date` 자리 없음]), `effective`(실적 우선), `loading_deadline`(수리일+30 — 수리 실적 없으면 UNKNOWN `NOT_CLEARED`), `customs_clearance`(살아 있는 통관 `accepted_on`들 → MIN·`CLEARED`/`PARTIAL`/`NONE` — R-06), `loading_fulfilment`(이행일 = ETD·BL_ISSUED 실적 존재값의 MAX — R-10 가정, `MET`/`MET_LATE`/`OPEN`/`OVERDUE`/`UNKNOWN`), `cutoff_scan_date`(min(현지, KST) — D-N 기준일 전용). 결과형은 `DueResult(status, value, basis, reason)`이고 사유 코드는 `DueReason` 열거(와이어 값). 설계 시그니처 대비 편차 2: ① `loading_fulfilment`에 `bl_actual_on` 인자 추가(R-10 MAX를 함수가 직접 판정) ② 수리일 요약 함수 `customs_clearance` 신설(R-06 PARTIAL을 함수 층에서 고정 — 조립자 PR-4a는 살아 있는 기록만 넘긴다). 전표 모듈의 내장 `max` 호출 금지 스캔(채번 MAX+1 방지 — `test_doc_status_channel`) 때문에 MAX는 정렬 후 마지막 값으로 계산한다. 배선(선적 상세 `MilestoneBoard`·스캔)은 PR-4a·PR-6 몫 그대로. 시험: GC-A16·A18(함수 층) `golden` 마커 + design-B §B20 행 01~07·14·15·32~34.
