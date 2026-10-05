# 테스트 가이드

## 정본 실행 명령

```
docker compose run --rm api pytest -q            # 백엔드 전체(로컬은 샤드 없이 전체)
docker compose run --rm api pytest -q -m group_j # J 그룹만
docker compose run --rm api pytest -q -m golden  # 골든 케이스만
docker compose run --rm web npm run test -- --run # 프런트
```

## 그룹 마커 A~K (DESIGN.md §20)

모든 백엔드 테스트는 `@pytest.mark.group_x` 중 하나를 **반드시** 붙인다. 안 붙이면
수집 단계에서 실패한다(conftest가 강제). 한 케이스가 여러 그룹에 걸치면 여러 마커를 붙인다.
수는 **S3-2 종결 시점(2026-10-05, PR-8 — main `4bb71b144ee0` + PR-8 커밋) 수집 실측**이다(한 케이스가 여러 그룹이면 각각 센다). 괄호 = S3-1 종결 대비 증가.

| 마커 | 그룹 | 수 | S3-2가 주로 더한 것 |
|---|---|---|---|
| group_a | 전표·정합 | 1699 (+286) | 선적 커널·부분선적 잔량·역순 취소·실적/통관 생존 취소 가드·마일스톤·산식 순수 함수(대금만기·제시기한·적재기한)·수입선적 배정 가능량 |
| group_b | 서류 | 47 (0) | — (선적 RESERVED 진입 0은 A·K로 고정, 서류 렌더링은 S3-3) |
| group_c | 인증·원산지 | 826 (0) | — |
| group_d | 재고·채널 | 0 | — (Phase 4·5) |
| group_e | 비용·소싱 | 0 | — (S3-4·S5 — S3-2 '해당 없음') |
| group_f | 회계·연동 | 260 (0) | — |
| group_g | AI·보안 | 86 (+19) | 수입선적 원가 비복사(응답·CSV·이벤트 원가 키 0)·선적 CSV 수식 이스케이프 |
| group_h | 운영 | 618 (+72) | 기일 알림 dedup·에스컬레이션(현재 수신자·24h)·롤오버 새 기일·이관·ADMIN 폴백·통보 = 기록·승인 무결성 대사 잡·워크스루 e2e·runbook 계약 |
| group_i | 자동화·통합 | 28 (+5) | SO 자동 엣지 2(선적중 수렴)·스캔 잡 14행·실패 1건 → 잡 FAILED |
| group_j | 안전 계약 | 586 (+103) | 실제 동시 부분선적·수입선적·T13 잠금 순서·멱등 재생(선적·롤오버·통보·통관·휴일 교체)·IMMUTABLE 3표 권한 거부 |
| group_k | 보안·품질 | 2260 (+357) | 권한 매트릭스(물류 첫 쓰기·휴일·세트 = 관리자)·총수 핀(선적 상태·잡 14)·'알림만' 아키텍처·stock_movements 무접촉·Page·출구 계약(스캔 알림 ⊆ 알림 이동 표) |

보조 마커: `golden`(골든 케이스 — **85건** = S3-1 종결 43 + S3-2 42[GC v1.5 10건 전부 — 케이스별 A14 5·A15 2·A16 10·A17 4·A18 5·A19 6·A20 4·A21 4·F4 1·G3 1, 위치 목록은 GC 문서 v1.5 부기]), `concurrency`(실동시 실행 — 68건), `slow`(1건), `meta`(테스트·설정 자체 검사 — 145건).

## 픽스처 3규약 (바꾸려면 ADR)

1. **격리 = 실제 커밋 + TRUNCATE** (롤백/SAVEPOINT 픽스처 금지 — ADR-0005). 서비스가
   스스로 커밋하고(§17.1), GC-F1은 실제 동시 실행을 요구하므로 롤백 픽스처와 원리적 충돌.
2. **스키마는 `alembic upgrade head`로만** 만든다(create_all 금지 — 드라이런이 형식만 남는 것 방지).
3. **동시성 테스트는 스레드별 자기 세션 + Barrier**(`tests/support/concurrency.py`). Session은
   스레드 안전하지 않다.

## `concurrency` 마커 규율 (S3-1 G-08 — 함정 ③)

- **실제 동시 실행**(스레드 2개 이상 + Barrier)으로만 증명한다. 순차 실행은 통과 증거가 아니다(GC-F1·F2·F3).
- 커밋을 동반하므로 **같은 test DB에 pytest를 두 개 동시에 돌리지 않는다**(pytest-xdist 등 병렬 실행 금지 — 서로의 TRUNCATE가 상대의 행을 지워 정상 코드가 빨개진다. PROGRESS 주의 인계 ③). 병렬이 필요하면 **DB를 나눈다** — CI 샤드가 그렇게 한다(아래).
- 잠금을 쓰는 시험은 **변이 점검**으로 잠금 제거 시 실패하는지 확인한다(예: 청소 잡 SKIP LOCKED 제거 → `test_a_locked_claim_is_skipped_not_waited_on…` kill).
- 시간 경계가 있는 시험은 **같은 '오늘'**(KST — `today_kst()`)을 시험과 대상 코드가 공유하게 고정한다(KST 자정·UTC 날짜 차이로 하루 어긋나는 결함 — PR #50 CI 23:55~00:04 KST 실측).
  고정은 `tests/support/kst.pin_today_kst(monkeypatch, 시험모듈)` 하나로 한다 — 원본 `app.core.time.today_kst`와 `TODAY_IMPORT_POINTS`(이름 임포트 지점)를 함께 바꾼다. `TODAY_PINNED_PACKAGES`(trade_chain·receivables·payments·lc_terms·commercial_invoices·renditions·letterhead·trade_docs) 아래에 `from app.core.time import today_kst`를 새로 쓰면 그 모듈을 `TODAY_IMPORT_POINTS`에 등재한다 — 빠지면 `tests/architecture/test_today_pin_coverage.py`가 즉시 실패한다(별칭 임포트 금지). 이름에 milestone·customs·receivable·payment·aging·L/C·commercial_invoice·rendition·letterhead가 든 시험 파일은 `today_kst`를 읽으면 고정 호출이 필수다(`test_milestone_contract`). S3-3 PR-1b(R-37).

## 워크스루(렌즈 11) 3층 증거 (ADR-0086 — 브라우저 e2e 도구 미채택)

1. **실 HTTP e2e**(리포): 세션 종결 관통 1쌍 — `tests/e2e/test_s3_1_walkthrough.py`(S3-1)·`tests/e2e/test_s3_2_walkthrough.py`(S3-2: 휴일 선언·CLI 계정 역할 부여 → SO 확정 → 부분선적 → 마일스톤·통관 → 기일 스캔 알림 → 알림 대상 화면 → 실적 재계산 → 취소 2단, J 같은 키 재생, K 출구 계약).
2. **소스 계약 vitest**(리포): jsdom이 레이아웃을 못 재는 것(390px 그리드 `detail-layout.test.ts`·알림센터 내용 칸 최소 폭)·날짜 문자열 `new Date` 금지·알림 이동 표/대상 이름.
3. **실브라우저 관통 1회**(리포 밖): venv playwright + `/opt/pw-browsers/chromium_headless_shell-1194`(`executable_path` 지정, 설치 0) 스크래치 스크립트 — 단계·`scrollWidth`·발견 결함은 PROGRESS 해당 절 **본문**에 적는다.

## 그룹 매핑 규칙 (분류가 흔들리지 않게)

A~K 어느 원문 항목에도 정확히 안 맞는 인프라·품질 테스트는:
- **K(보안·품질)** = 설정 fail-fast·마이그레이션·로그 마스킹·에러 봉투·CI 계약·아키텍처 규약
- **H(운영)** = 헬스체크·접근 로그·쿼리 계측·request_id·배치(잡) 동시 실행

## CI 구조와 기준선 (ADR-0073 — 3샤드)

| 잡 | 하는 일 |
|---|---|
| `hygiene` | CRLF·대소문자 충돌·`.env` 실값·시크릿 패턴·PG 태그 일치·Tailwind v4 규율 |
| `backend-checks` | ruff check·ruff format --check·mypy app·**마이그레이션 드라이런 4종**(`kbos_migr`) |
| `backend` ×3 (`shard: [1, 2, 3]`) | 샤드마다 독립 PG로 `pytest`(커버리지 데이터만 수집). 파일 단위 LPT 배정(`tests/.shard_durations.json` 실측 초 — 미등재 파일은 테스트당 평균값) |
| `backend-coverage` | 샤드 완전성(합집합 = 독립 전체 수집·교집합 ∅) + 커버리지 병합 **94% 게이트**(ADR-0031) + 소요 시간 갱신본 아티팩트 |
| `frontend` | 타입체크·vitest·빌드 |
| `compose` / `compose-smoke` | dev·prod compose 렌더 검증 / 스택 기동 왕복 1건 |
| `ci-ok` | 위 전부 success일 때만 success — **병합 게이트는 이 체크까지 전 체크런 success**(CLAUDE.md) |

**기준선(S3-2 종결 — 2026-10-05 PR-8 로컬 실측, CI 실행은 push 후)**: pytest **5774건 수집**(로컬 전체 1회 결과는 PROGRESS 'S3-2 PR-8 / S3-2 종결' 절) · vitest **83파일 1474건** · 커버리지 게이트 94. (S3-1 종결 기준선: 5065 수집 · vitest 66파일 1171건.)
샤드가 늘면 `matrix.shard`에 번호만 추가한다(N은 `strategy.job-total`에서 따라온다). 샤드 균형이 나빠지면 `backend-coverage`의 `shard-durations` 아티팩트로 `tests/.shard_durations.json`을 교체한다(수동).

## 마이그레이션 왕복 검사용 DB

`kbos_migr`(왕복 전용) — pytest가 쓰는 `kbos_test`와 분리. `test_migrations.py`가
`ALEMBIC_DATABASE_URL`로 이 DB를 가리켜 upgrade→downgrade→upgrade를 돌린다.

## 속도 예산

S0-1 전체 ≈ 15초 → S3-1 종결 시점 로컬 1프로세스 약 24분 → S3-2 PR-6 시점 약 50분(PROGRESS 실측 — 선적 동시성·e2e 증가, 샤드 재균형은 부채 Q-15). `--durations=10`으로 상위 목록 노출. 대응은 ③ **CI 샤드 분할(ADR-0073)** 까지 시행 중이며, 로컬은 그룹 마커(`-m group_x`)나 파일 단위로 좁혀 돌린다. 커버리지 게이트는 94%(ADR-0031).
