# ADR-0082: holidays — 국가 ISO alpha-2(markets 비FK)·연도 선언 단위 근거 2필드 필수·연도·국가 정합 DB 강제·경고만(자동 순연 0)·UNVERIFIED ≠ 평일·적용 = ETA(도착국)만·시드 0

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.5(S3-2 [M4] 보강) / ADR-0055('휴일 보정'의 해석) / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B11·B12, R-09·R-24 / 구현 PR-2a(M13)·PR-2b(화면)·PR-4a/4b(배선)

**맥락** — §7.5는 "국가별 휴일 캘린더 반영(ETA 현지 연휴 경고)"을 정한다. 휴일 데이터가 없는 국가·연도를 '휴일 아님'으로 읽으면 fail-open이고, 날짜를 자동으로 미루면 만기가 조용히 바뀐다(계약 해석). 휴일의 국가는 출발·도착국이라 시장(markets — 판매 시장) 축과 다르다. S3-1 계획 부록의 "휴일 보정은 S3-2" 문구는 DESIGN의 "경고"와 어긋난다.

**결정** — ① 표 2개: `holiday_calendar_years`(국가 CHAR(2) `^[A-Z]{2}$`·연도 2000~2999·`source_url`·`verified_on` 필수·version) / `holidays`(날짜·이름). **연도·국가 정합은 DB 강제** — `holiday_calendar_years` UNIQUE(id, country_code, year)·`holidays.year` NOT NULL·복합 FK `(calendar_year_id, country_code, year)`·CHECK `extract(year from holiday_on) = year`(위반 422 `HOLIDAYS.CALENDAR.YEAR_MISMATCH`). 국가는 **markets FK 아님**. ② 쓰기 = ADMIN 전용 `PUT /holidays/{c}/{y}` **원자 교체**(0건 = '휴일 없음 확인'), CSV 미리보기(비저장)·export, 시드 0. ③ 판정 3값: HOLIDAY(이름)·CLEAR·**UNVERIFIED**(미선언 — '휴일 캘린더 미등록 — 확인 불가', 평일 아님). ④ **경고만** — 자동 순연·당김·차단 0, 대금만기 등 산식 값은 휴일과 겹쳐도 불변('휴일 미반영' 주기). ⑤ **적용 = ETA(도착국)만**(문면 그대로) — 출발국(ETD·Cargo Closing·서류마감)·주말 판정은 부채. ⑥ ADR-0055의 "휴일 보정"은 이 경고로 해석한다. 휴일 경고는 조회 계산값(알림·잡 없음).

**근거** — UNVERIFIED를 별값으로 두는 것이 '평가 불능은 통과가 아니다'(GC-A13) 원칙이다. 연도 단위 선언+근거 링크는 휴일 데이터의 출처를 감사 가능하게 하고, 원자 교체는 부분 갱신 불일치를 없앤다. ETA 한정은 문면 충실이며, 수입선적 출발국 미선언 UNVERIFIED 배지가 대량으로 생겨 신호가 묻히는 것을 피한다(R-09).

**기각한 대안** — 영업일 자동 순연(문면 없음·만기 조용히 변함), 미선언 = CLEAR(fail-open), markets FK(판매 시장 축과 불일치 — 출발국 등록 강요), 서비스·테스트만으로 연도 정합(서비스 결함 1건이 판정을 조용히 틀어지게 함 — R-24), 외부 휴일 API 자동 수집(§15 L3·출처 검증 불가), 출발국까지 확장.

**되돌리기 비용** — 경고 범위 확대·축소 낮음(함수·화면 — 출발국 확장 포함) / markets FK 전환 중간 / 자동 순연 도입 중간(계산 결과 변경 — 재계산 공지). 복합 FK 형태는 M13 병합 전 낮음.
