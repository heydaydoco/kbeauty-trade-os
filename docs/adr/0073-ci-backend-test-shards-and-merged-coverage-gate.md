# ADR-0073: CI 백엔드 테스트를 파일 단위 샤드 매트릭스로 나누고, 94% 게이트는 병합 커버리지에·샤드 완전성은 독립 수집 대조로 건다

- **상태**: 자율 확정(2026-10-03) — 사후 번복 가능 (PROGRESS 부채 "테스트 샤드 분할 — 트리거: 백엔드 잡 40분 초과 또는 PR-14 이후" 발동)
- **날짜**: 2026-10-03
- **관련**: DESIGN.md §18.3 / **ADR-0031**(커버리지 94% 게이트 — 임계·측정 대상·조정 절차는 그대로, 거는 위치만 병합 잡으로 이동) / ADR-0011(병합 게이트 = 전 체크런 success) / .github/workflows/ci.yml · backend/tests/support/sharding.py · shard_tools.py · tests/.shard_durations.json

**맥락** — backend 잡이 테스트 4,500+건을 한 러너에서 직렬로 돌려 PR마다 20~25분, 느린 날은 한도(15→30→60분)에 연달아 걸렸다. conftest가 롤백 픽스처를 금지(실제 커밋+TRUNCATE·단일 테스트 DB)하므로 한 러너 안 병렬(xdist)은 DB를 워커별로 나누지 않고는 불가하다.

**결정** — backend 잡을 `matrix.shard: [1,2,3]`로 나눈다. ① 분배는 의존성 없이 `tests/support/sharding.py`가 `KBOS_TEST_SHARD="i/N"`(N=`strategy.job-total`)일 때만 테스트 **파일** 단위로 `tests/.shard_durations.json` 실측 초에 따라 LPT 배정한다(미등재 파일은 테스트 수×테스트당 초, 환경변수가 없으면 무동작). ② 샤드는 `--cov=app`로 `.coverage.shard-i`만 남기고 **`backend-coverage` 잡이 `coverage combine` 뒤 `coverage report --fail-under=94`**를 건다(ADR-0031 임계 그대로). ③ 완전성은 배정 함수 단위 시험 + `backend-coverage`가 각 샤드 배정 보고서를 플러그인 없는 독립 `pytest --collect-only`와 대조(합집합=전체·교집합=∅·샤드 1..N 전부·테스트 수 일치)하는 이중 방어로 고정한다. ④ 린트·mypy·드라이런 4종은 `backend-checks` 잡에서 1회만. ⑤ `ci-ok`는 needs에 세 잡을 넣고 `join(needs.*.result)`로 needs 전부를 본다. 샤드 timeout 40분.

**근거** — 파일 단위면 모듈 픽스처·파일 안 순서가 보존되고 각 샤드는 지금과 같은 직렬·단일 DB 의미론이라 conftest 격리 원칙을 건드리지 않는다. 배정은 수집 결과만의 결정적 함수(정수 ms·경로 tie-break)라 러너마다 같고, 소요 시간 파일이 낡아도 **균형만 나빠질 뿐 누락은 구조적으로 불가능**하며 CI가 매 실행 독립 수집으로 재확인한다. 게이트는 병합 데이터에 걸어 단일 실행과 같은 수치를 본다(로컬 재현: 병합 97.17% vs 단일 97.19%, 3샤드 1,522+1,851+1,251 = 수집 4,624건 전건 통과 — PROGRESS 'CI 샤드 분할' 절).

**기각한 대안** — pytest-xdist(워커별 DB 분리·GC-F1 실제 동시성 시험과 TRUNCATE 정리의 워커 간 간섭·의존성 추가), pytest-split/pytest-shard(의존성 추가·테스트 단위 분할 시 모듈 픽스처 중복 실행), 해시·라운드로빈 배정(균형 불량), 샤드별 커버리지 게이트(부분 실행이라 무의미), 린트·드라이런을 샤드 1에서만(샤드 1만 길어지고 실패 원인 식별이 늦다).

**되돌리기 비용** — 낮음. ci.yml을 이 ADR 직전 커밋의 단일 backend 잡으로 되돌리고 test_ci_contract의 샤드 단언을 걷으면 된다(플러그인은 환경변수가 없으면 무동작이라 남겨도 무해). 대가로 러너 분 사용량이 늘어난다(잡 셋업 반복 — 푸시당 약 +8~12분 추정, 실측은 첫 CI 실행에서 확인).

**부기(2026-10-05 — S3-3 계획)** — (자율 확정 — 사후 번복 가능. 위 원문 결정은 고치지 않는다.) **durations 재갱신 시점(S3-3)**: `.shard_durations.json`을 PR-1b(Q-15 — 첫 갱신)·PR-2b 후·PR-5a 후 3회 갱신한다(시험 수 증가 — 부채 E-01로 계속). 샤드 1개가 32분을 넘으면 3 → 4샤드(부채 E-02). 속성 시험은 시드 고정 + 예산 30초. 병합 게이트(`ci-ok` 포함 전 체크런 success)는 그대로.
