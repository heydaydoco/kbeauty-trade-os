# CI 가이드

## 구조

`.github/workflows/ci.yml` — push마다 6잡:

| 잡 | 하는 일 |
|---|---|
| hygiene | CRLF·대소문자 파일명·`.env` 트래킹·시크릿 패턴·postgres 태그·tailwind config 검사 |
| backend | init SQL 실행 → ruff·mypy → 마이그레이션 드라이런 4종 → pytest(+커버리지) |
| frontend | npm ci → typecheck → vitest → build |
| compose | dev·prod compose 문법 검증 |
| scope-guard | 마이그레이션 리비전 1개 초과 시 실패(S0-1 범위 침범 방지 — **S0-2 첫 작업이 이 잡 제거**) |
| ci-ok | 위 전부 성공했는지 확인하는 **단일 게이트** |

## required check는 `ci-ok` 하나만

룰셋의 required status check로 **`ci-ok`만** 지정한다. 잡을 추가·이름변경해도 룰셋을
건드릴 필요가 없다. `ci-ok`의 `needs`에 새 잡을 빠뜨리면 그 잡이 빨개도 게이트가
초록이 되는데, `test_ci_contract.py`가 그 누락을 잡는다. **`ci-ok`는 이름을 바꾸지 말 것**
(바꾸면 룰셋이 영원히 pending).

## 병합 차단 (GitHub Pro 필요)

개인 private 리포는 Free에서 rulesets가 잠겨 있다(ADR-0010). Pro 결제 후:
```
gh api --method POST repos/heydaydoco/kbeauty-trade-os/rulesets --input infra/github/ruleset-main.json
```
상세 절차·실증은 `docs/qa/K-ci-merge-block.md`.

## 빨간불이 뜨면

- GitHub 리포 상단 **Actions** 탭 → 실패한 실행 → 빨간 잡 클릭 → 실패 스텝 펼쳐 오류 원문 복사.
- 로컬에서 같은 검사 재현:
  - 백엔드: `docker compose run --rm api pytest -q` / `ruff check backend` / `mypy` (컨테이너 안)
  - 프런트: `docker compose run --rm web npm run typecheck`
  - compose: `docker compose config -q`

## 무료 분 예산

Free private는 월 2,000분(Pro는 3,000분), 잡당 분 단위 올림 = 잡 6개면 실사용 짧아도
최소 6분 청구. 초과하면 CI가 멈춰 §18.3 계약이 정지한다. 첫 실행들의 Billable time을
PROGRESS "주의 인계"에 기록해 추정이 아닌 실측으로 예산을 관리한다.
- 확인 위치: Actions 탭 → 특정 run → 우측 상단 요약, 또는 Settings → Billing.
