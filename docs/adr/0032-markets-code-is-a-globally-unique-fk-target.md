# ADR-0032: markets.code는 전역 UNIQUE인 코드 참조 FK 대상이다 (country_code 승격 종착)

- **상태**: 채택 (S2-1 계획 웹 세션 판정 2026-08-07 — 안건 2·조건 3~6)
- **날짜**: 2026-08-07
- **관련**: DESIGN.md §5.1·§17.4 / ADR-0023(이 ADR이 그 종착) / docs/plans/s2-1-plan.md §0-1 ② / PROGRESS 관찰 항목(country_code FK 승격)

**맥락** — ADR-0023이 예약한 승격 시점(S2-1)이 도달했다. labels·ingredient_rules·sku_hs_codes의 country_code를 markets에 어떻게 묶을지 — 대리키(market_id) 교체는 라벨 유일키 [sku,국가,언어,판번]과 CSV 칸을 바꾼다.

**결정** — **문자열 코드 컬럼 유지 + markets.code 참조 FK 3건**(컬럼·유일키·CSV 전부 불변). 그 성립 조건으로 `markets.code`는 unique_active(부분 인덱스)가 아니라 **전역 UNIQUE**다 — FK는 부분 유니크 인덱스를 참조할 수 없다. soft delete된 시장 코드의 재사용은 신규 행이 아니라 **복원(명시 액션)**이다. 백필 행은 MIG 계보(note 표식·이름=코드)로 만들고 검수는 반입 재개 시 MIG 큐에 합류한다. 미등록 시장의 사용자 안내(422 + 선등록 조치)는 서비스 가드, FK는 안전망이다.

**근거** — §17.4의 부분 유니크 규율은 **멱등 키**(업무 이벤트 중복 흡수) 대상이고, markets.code는 **참조 대상 자연키 레지스트리**다 — 같은 코드의 신·구 행이 공존하면 코드를 값으로 참조하는 FK의 지시 대상이 무너진다. 두 규율은 목적이 달라 공존한다(test_market_constraints가 전역 UNIQUE·soft delete 점유 거동을 실측 고정).

**기각한 대안** — market_id 대리키 교체(승격 선례 e45b79db6198 꼴): 라벨 유일키·왕복 아닌 CSV 계약까지 흔드는 범위라 판정에서 부결 — 그 설계는 착수 전 재판정 대상이었다.

**되돌리기 비용** — 낮다. FK 3건 drop이 전부(컬럼 불변 — downgrade 실재). 전역 UNIQUE→부분 전환은 참조 FK를 먼저 없애야 하므로 이 ADR 폐기와 한 몸이다.

**부기(사후 승인 — 2026-08-08 PR #15 완료 보고 웹 세션 판정, s2-1-plan.md §0-2)** — ① 전역 UNIQUE=FK 참조 대상 구조 강제·부분 유니크 규율=멱등 키 대상이라는 본 결정·근거 문면이 구현 그대로 사후 승인됐다. ② downgrade=FK 3건 drop 완결 논거 승인: 컬럼 이동이 없어 역복사 대상이 부재하고, 백필 행은 downgrade 후 잔존하되 upgrade 백필이 NOT EXISTS 멱등이라 왕복 드라이런을 깨지 않는다(조건 4 "역복사 포함" 문면은 이 논거로 대체 종결).
