# 마이그레이션

스키마 변경은 **오직 이 폴더의 파일로만** 한다 (CLAUDE.md / DESIGN.md §2 ADR-02).
psql이나 pgAdmin에서 손으로 테이블을 고치면 dev·test·CI·운영의 스키마가 갈라진다.

## 명령 (컨테이너 안에서)

```bash
docker compose exec api alembic revision --autogenerate -m "설명"
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
docker compose exec api alembic check      # 모델 ↔ 마이그레이션 드리프트 검사
docker compose exec api alembic heads      # 정확히 1개여야 한다
```

## autogenerate가 **놓치는** 것 — 손으로 써야 한다

- CHECK 제약 (`value_in()`, `nonzero()` 등)
- 부분 인덱스의 조건 변경 (`WHERE deleted_at IS NULL`)
- `server_default` 변경 (비교가 꺼져 있다 — env.py 주석 참고)
- GRANT/REVOKE (§17.5의 불변 강제)
- 트리거·뷰·함수
- 컬럼 **rename** — autogenerate는 drop + add로 만든다. 그대로 두면 실데이터가 사라진다.

## 접속 계정

alembic은 항상 `kbos_owner`로 붙는다. 런타임 계정 `kbos_app`은 DDL 권한이 없다
(ADR-0002). 접속 문자열은 `MIGRATION_DATABASE_URL` 환경변수에서 온다 —
`alembic.ini`에는 적혀 있지 않다.
