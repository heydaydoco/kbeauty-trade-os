# ADR-0010: CI 병합 차단은 GitHub Pro 필요

- **상태**: 채택
- **날짜**: 2026-07-24
- **관련**: DESIGN.md §18.3, §20 K / WBS S0-1 DoD

**맥락** — WBS S0-1 DoD·§18.3·§20 K가 "CI 실패 시 병합 금지"를 요구하나, 이 리포는 개인(User) 계정 + private이라 branch protection·rulesets API가 둘 다 403("Upgrade to GitHub Pro")로 잠겨 있다(실측 2026-07-24).

**결정** — GitHub Pro(월 $4)로 잠금 해제하고 ruleset `main-ci-gate`(required check = `ci-ok`, PR 필수, 승인 0)를 적용한다. 리포 public 전환은 사업 설계·거래처 구조 노출로 기각(§18.1). 룰셋 정의는 `infra/github/ruleset-main.json`, 실증 절차는 `docs/qa/K-ci-merge-block.md`.

**근거** — Free Organization 이전도 해결 안 됨(Team 유료, Pro와 동가·절차만 복잡). CI 자체는 Pro 없이도 push마다 돌며, ci-ok 게이트가 실패 시 red가 됨은 실증됨(run 30069836553) — 남은 것은 GitHub 쪽 강제 토글뿐.

**기각한 대안** — public 전환(기밀 노출) / Free Org 이전(미해결) / pre-push 훅 폴백만(웹 Merge 버튼을 못 막아 "차단"이 아니라 "규율").

**되돌리기 비용** — 낮음(구독 변경). Pro 미결제 시 이 DoD는 "미충족"으로 명시하고 PROGRESS 부채로 남긴다.
