# ADR-0023: 규제 마스터의 국가 축은 markets(S2-1) 전까지 country_code 문자열이다

- **상태**: 채택 (웹 세션 판정 2026-07-30 — 조건 B)
- **날짜**: 2026-07-30
- **관련**: DESIGN.md §4.3·§4.5·§5.1 / WBS.md S1-2·S2-1 / ADR-0019·ADR-0020

**맥락** — S1-2의 `ingredient_rules`(성분×국가)와 `labels`(SKU×시장)는 국가/시장 축이 필요한데, 실체인 `markets` 테이블은 S2-1(§5.1)이다. S1-1의 `sku_hs_codes`가 이미 같은 처지로 country_code 문자열을 쓰고 있다.

**결정** — `ingredient_rules.country_code`·`labels.country_code`는 ISO 3166-1 alpha-2 문자열로 두고, sku_hs_codes의 3중 방어(pydantic 형식 → 서비스 `.strip().upper()` → DB CHECK `^[A-Z]{2}$`)를 그대로 쓴다. **markets FK 승격 여부는 S2-1 계획 보고에서 sku_hs_codes와 함께 일괄 재판정한다.**

**근거** — ADR-0020과 같은 원리(대상 없는 FK는 문자열로 두되 승격 시점을 못박는다) + ADR-0019의 선례 일관성. 세 테이블이 같은 축을 다르게 표현하면 S2-1 승격 때 한쪽만 바뀌고 남는다 — 같은 처지는 한 ADR로 묶고 같은 시점에 재판정한다.

**기각한 대안** — S1-2에서 markets 테이블을 미리 생성: §5.1의 markets는 요건 템플릿과 한 몸이라(적용단위·Tier) 헤더만 만들면 ADR-0021이 경고한 "먼저 만든 빈 축을 후속 세션이 재설계"가 재발한다.

**되돌리기 비용** — 낮다. 문자열 → FK 승격은 마이그레이션 1건 + 세 테이블 동시 적용(재판정 트리거는 PROGRESS 관찰 항목에 등재).
