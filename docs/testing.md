# 테스트 가이드

## 정본 실행 명령

```
docker compose run --rm api pytest -q            # 백엔드 전체
docker compose run --rm api pytest -q -m group_j # J 그룹만
docker compose run --rm web npm run test -- --run # 프런트
```

## 그룹 마커 A~K (DESIGN.md §20)

모든 백엔드 테스트는 `@pytest.mark.group_x` 중 하나를 **반드시** 붙인다. 안 붙이면
수집 단계에서 실패한다(conftest가 강제). 한 케이스가 여러 그룹에 걸치면 여러 마커를 붙인다.

| 마커 | 그룹 |
|---|---|
| group_a | 전표·정합 |
| group_b | 서류 |
| group_c | 인증·원산지 |
| group_d | 재고·채널 |
| group_e | 비용·소싱 |
| group_f | 회계·연동 |
| group_g | AI·보안 |
| group_h | 운영 |
| group_i | 자동화·통합 |
| group_j | 안전 계약 |
| group_k | 보안·품질 |

보조 마커: `golden`(골든 케이스), `concurrency`(실동시 실행), `slow`, `meta`(테스트·설정 자체 검사).

## 픽스처 3규약 (바꾸려면 ADR)

1. **격리 = 실제 커밋 + TRUNCATE** (롤백/SAVEPOINT 픽스처 금지 — ADR-0005). 서비스가
   스스로 커밋하고(§17.1), GC-F1은 실제 동시 실행을 요구하므로 롤백 픽스처와 원리적 충돌.
2. **스키마는 `alembic upgrade head`로만** 만든다(create_all 금지 — 드라이런이 형식만 남는 것 방지).
3. **동시성 테스트는 스레드별 자기 세션 + Barrier**(`tests/support/concurrency.py`). Session은
   스레드 안전하지 않다.

## 그룹 매핑 규칙 (분류가 흔들리지 않게)

A~K 어느 원문 항목에도 정확히 안 맞는 인프라·품질 테스트는:
- **K(보안·품질)** = 설정 fail-fast·마이그레이션·로그 마스킹·에러 봉투·CI 계약·아키텍처 규약
- **H(운영)** = 헬스체크·접근 로그·쿼리 계측·request_id

## 마이그레이션 왕복 검사용 DB

`kbos_migr`(왕복 전용) — pytest가 쓰는 `kbos_test`와 분리. `test_migrations.py`가
`ALEMBIC_DATABASE_URL`로 이 DB를 가리켜 upgrade→downgrade→upgrade를 돌린다.

## 속도 예산

S0-1 전체 ≈ 15초, `--durations=10`으로 상위 목록 노출. 초과 시 대응 순서:
① 세션 스코프 픽스처로 이동 → ② 템플릿 DB 클론(Phase 2~3) → ③ pytest-xdist 병렬(worker별 DB 분리).
커버리지 임계 게이트(`--cov-fail-under`)는 측정만 하고 Phase 1 종료 후 실측 기반 ADR로 도입.
