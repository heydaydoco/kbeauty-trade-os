# ADR-0003: 코어 데이터 타입·규약 (PK·상태값·시각·금액·로케일·스택)

- **상태**: 채택
- **날짜**: 2026-07-24
- **관련**: DESIGN.md §2 ADR-01·02·11, §14, §17.5 / GC-G1

**맥락** — S0-2가 공통 테이블 12개를 한 번에 깐다. 그 전에 되돌리기 비싼 타입 규약을 고정해야 세션마다 제각각이 되지 않는다.

**결정** — ① PK: `BIGINT GENERATED ALWAYS AS IDENTITY`(UUID 미사용). ② 상태·유형값: 네이티브 ENUM 금지, `VARCHAR + CHECK(col IN(...)) + StrEnum`. ③ 시각: 전 컬럼 `TIMESTAMPTZ`, 앱은 tz-aware UTC만, 클러스터 레벨 `timezone=UTC`, KST는 표시 계층 단일 진입점. ④ 금액: `BIGINT 정수 최소단위 + CHAR(3) 통화`, Float 금지(아키텍처 테스트로 강제). ⑤ DB 로케일: `LC_COLLATE 'C' + LC_CTYPE 'C.UTF-8'`. ⑥ SQLAlchemy 동기 + psycopg3. ⑦ 프런트 스택: react-router·TanStack Query·nginx(prod). ⑧ **물리량은 `NUMERIC`**(§8.2 "중량·CBM=소수") — 자릿수는 컬럼 정의에 고정하고 Float 금지는 금액과 동일. **`skus.unit_weight_g` = 소매포장을 포함한 판매단위 1개의 중량(g)**이며, 선적 서류의 G.W.(포장·팔레트 포함)·N.W.(내용물만)와는 **다른 값**이다 — 그 둘은 마스터가 아니라 선적 라인(§7.6)이 갖는다. [S1-1 보강]

**근거** — §18.1이 IDOR 검증을 무조건 요구해 UUID 추측불가 이점이 무효(§17.3 채번이 외부 식별자 담당). ENUM은 값 삭제·순서 변경 불가 + autogenerate 취약(§17.5가 상태값 CHECK를 명시). LC_CTYPE 'C'는 실측상 한국어 pg_trgm 검색을 DB 레벨에서 영구 폐쇄(`show_trgm('수출제품')={}`)하고 COLLATE로 못 덮음 → 'C.UTF-8'. §17.1이 트랜잭션 내 외부호출을 막는 순간 async 이득이 소멸하고 GC-F1 실동시 실행은 스레드+실커넥션이 위양성 없는 증거를 만든다.

**기각한 대안** — UUID PK(이점 무효·인덱스 팽창) / PG ENUM(마이그레이션 취약) / async SQLAlchemy(픽스처 복잡·이득 소멸) / LC_CTYPE 'C'(한국어 검색 폐쇄) / LC_COLLATE 'C.UTF-8'(glibc 인덱스 파손 위험, 'C'는 datcollversion=NULL로 면역).

**되돌리기 비용** — 로케일·PK 타입은 최상위(덤프 후 DB 재생성). 나머지는 테이블 0개인 지금이 유일하게 싼 시점.

**부기(2026-09-30 — S3-1 계획)** — (자율 확정 — 사후 번복 가능. 위 원문 결정은 고치지 않는다.) DESIGN §2 ADR-02("soft delete·감사 컬럼 전 테이블")는 별도 ADR 파일이 없고 이 ADR이 ADR-02를 구체화하므로 여기에 부기한다. **예외 명문화**: S3-1의 `approvals`·`delegations`·`approval_events`는 삭제 개념이 없어 SoftDelete 믹스인을 두지 않는다(`approvals`·`delegations`는 Timestamp·Version·Actor를 유지하고 종료·소비는 상태·`revoked_at`으로 표현, `approval_events`는 PkMixin+Base의 IMMUTABLE 이력). 기존 관행(`user_sessions`·`events`·`doc_number_seq`·`idempotency_keys`)과 같은 결이며, 같은 논리로 상태이력 4표·`gate_evaluations`·`gate_overrides`·`payments`도 삭제가 없는 원장형 불변 테이블이다(ADR-0040 서식). 근거: ADR-0060·0051·0063·0064 / design-C.md C1.
