# ADR-0004: alembic 규약 + "마이그레이션 드라이런" 정의

- **상태**: 채택
- **날짜**: 2026-07-24
- **관련**: DESIGN.md §2 ADR-02, §17.4, §18.3

**맥락** — §18.3·WBS S0-1의 "마이그레이션 드라이런"에 정의가 없고, alembic 제약 이름 규약도 정해지지 않으면 테이블 100개 시점에 회수 불가다.

**결정** — `MetaData(naming_convention)` 5종(pk_/fk_/uq_/ix_/ck_) 필수. 부분 유니크는 `unique_active()` 헬퍼(`Index(postgresql_where=text("deleted_at IS NULL"))`)로만. `compare_type=True`·`compare_server_default=False`. 테스트 스키마도 `alembic upgrade head`로만(create_all 금지). 드라이런 = ① heads 단일 ② 빈 DB upgrade ③ downgrade base→upgrade 왕복 ④ check 드리프트 0.

**근거** — 이름 없는 제약은 alembic이 지목 못 해 변경·다운그레이드가 막힌다. §17.4 멱등 UNIQUE는 UniqueConstraint로 표현 불가(부분 조건). server_default 비교는 PG가 상시 위양성을 내 게이트를 무력화. create_all 허용 시 테스트 스키마와 마이그레이션 스키마가 갈려 드라이런이 형식만 남는다. 오프라인 `--sql`은 순서·의존 오류를 놓친다.

**기각한 대안** — 익명 제약(회수 불가) / raw SQL 부분 인덱스(이름·조건 갈림) / create_all 테스트 스키마(드리프트 은폐) / `--sql` 오프라인 드라이런(데이터 마이그레이션에서 파손).

**되돌리기 비용** — naming_convention 변경은 전 제약 재생성. 지금이 유일하게 싼 시점.
