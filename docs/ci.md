# CI 가이드

## 구조

`.github/workflows/ci.yml` — push마다 5잡:

| 잡 | 하는 일 |
|---|---|
| hygiene | CRLF·대소문자 파일명·`.env` 트래킹·시크릿 패턴·postgres 태그·tailwind config 검사 |
| backend | init SQL 실행 → ruff·mypy → 마이그레이션 드라이런 4종 → pytest(+커버리지) |
| frontend | npm ci → typecheck → vitest → build |
| compose | dev·prod compose 문법 검증 |
| ci-ok | 위 전부 성공했는지 확인하는 **단일 게이트** |

> S0-1의 `scope-guard` 잡(마이그레이션 리비전 1개 제한)은 S0-2 시작과 함께 제거했다 —
> S0-2가 리비전을 여러 개 추가한다. 6잡 → **5잡**.

## required check는 `ci-ok` 하나만

룰셋의 required status check로 **`ci-ok`만** 지정한다. 잡을 추가·이름변경해도 룰셋을
건드릴 필요가 없다. `ci-ok`의 `needs`에 새 잡을 빠뜨리면 그 잡이 빨개도 게이트가
초록이 되는데, `test_ci_contract.py`가 그 누락을 잡는다. **`ci-ok`는 이름을 바꾸지 말 것**
(바꾸면 룰셋이 영원히 pending).

## 병합 차단 — 폴백(옵션 B) 채택

개인 private 리포는 Free에서 rulesets가 잠겨 있고(ADR-0010), Pro 결제는 하지 않기로
결정했다(ADR-0011). 그래서 **기계 차단 대신 규율**로 간다:

1. **훅 활성화(1회)**: `git config core.hooksPath .githooks`
   → 이후 push 전에 `.githooks/pre-push`가 CRLF·.env·시크릿·대소문자 충돌을 즉시 검사.
2. **병합은 스크립트로만**: `bash scripts/merge-pr.sh <PR번호>`
   → `gh pr checks --watch --fail-fast`로 CI green을 확인한 뒤에만 `gh pr merge --squash`.
3. **웹 Merge 버튼 사용 금지**(CLAUDE.md 명문화). 훅·스크립트는 규율이지 물리적 차단이 아니다.

> **DoD② "병합 차단"은 미충족(부채)** 상태다. 협업자 추가 또는 팀 배포 시 Pro/Team을
> 재검토하고, 그때 `infra/github/ruleset-main.json` 룰셋을 적용한다(ADR-0011).
> Pro 반영 시 절차·실증: `docs/qa/K-ci-merge-block.md`.

## 빨간불이 뜨면

- GitHub 리포 상단 **Actions** 탭 → 실패한 실행 → 빨간 잡 클릭 → 실패 스텝 펼쳐 오류 원문 복사.
- 로컬에서 같은 검사 재현:
  - 백엔드: `docker compose run --rm api pytest -q` / `ruff check backend` / `mypy` (컨테이너 안)
  - 프런트: `docker compose run --rm web npm run typecheck`
  - compose: `docker compose config -q`

## 무료 분 예산

Free private는 월 2,000분(Pro는 3,000분), 잡당 분 단위 올림 = 잡 5개면 실사용 짧아도
최소 5분 청구. 초과하면 CI가 멈춰 §18.3 계약이 정지한다. 첫 실행들의 Billable time을
PROGRESS "주의 인계"에 기록해 추정이 아닌 실측으로 예산을 관리한다.
- 확인 위치: Actions 탭 → 특정 run → 우측 상단 요약, 또는 Settings → Billing.
