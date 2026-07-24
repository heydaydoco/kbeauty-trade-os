# K 그룹 증거 — "CI 실패 시 병합 차단"

DESIGN.md §20 K의 이 항목은 **코드 거동이 아니라 GitHub 리포 설정**이라 pytest로
재현할 수 없다(§22 렌즈 8을 문자 그대로는 충족 불가). 대신 아래 실증이 증거다.

## 전제 — GitHub Pro

개인(User) 계정 + private 리포는 **branch protection·rulesets 기능 자체가 잠겨 있다**.
실측(2026-07-24):

```
$ gh api repos/heydaydoco/kbeauty-trade-os/rulesets
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":"403"}
```

→ GitHub Pro(월 $4) 결제로 잠금 해제. 리포 public 전환은 사업 설계·거래처 구조
노출이라 기각(§18.1). (ADR-0010)

## 이미 확보된 증거 — 게이트가 red로 간다

룰셋과 무관하게, **ci-ok 게이트 잡이 선행 잡 실패 시 실제로 실패**한다는 것은
확인됐다. 첫 CI 실행(run 30069836553, s0-1-skeleton):

```
jobs: hygiene=success  frontend=failure  backend=success  compose=success  scope-guard=success  ci-ok=failure
ci-ok 로그: "선행 잡 중 실패가 있습니다: success success failure success success"
```

두 번째 실행(run 30070254770)에서 frontend 수정 후 6잡 전부 success → ci-ok success.

즉 "실패가 있으면 ci-ok가 red, 전부 통과해야 green"이 성립한다. 룰셋이
`required_status_checks: [ci-ok]`를 걸면 red인 ci-ok는 병합을 막는다.

## 룰셋 적용 (Pro 반영 후, 1회)

CI가 최소 1회 green으로 돈 뒤에 한다 — 룰셋의 status check 검색창에는
**최소 1회 실행된 체크 이름만** 뜬다(이미 green 실행이 있으므로 충족).

방법 A — 명령 1줄:
```
gh api --method POST repos/heydaydoco/kbeauty-trade-os/rulesets \
  --input infra/github/ruleset-main.json
```
확인: `gh api repos/heydaydoco/kbeauty-trade-os/rulesets --jq '.[]|{name,enforcement}'`
→ `{"name":"main-ci-gate","enforcement":"active"}`

방법 B — 웹 UI: 리포 → Settings → Rules → Rulesets → New branch ruleset →
이름 `main-ci-gate` → Enforcement **Active** → Target: Include default branch →
Rules에서 **Require a pull request before merging**(Required approvals = **0**,
1인 개발자가 자기 PR을 승인 못 해 영구 봉쇄되는 오설정 방지) +
**Require status checks to pass** → Add checks에서 `ci-ok` → Create.

## 병합 차단 실증 (Pro + 룰셋 후, 1회)

1. 일부러 실패하는 커밋으로 브랜치·PR 생성
   ```
   git switch -c ci-block-demo
   # backend/tests에 assert False 한 줄 추가
   git commit -am "demo: 고의 실패"
   git push -u origin ci-block-demo
   gh pr create --fill
   ```
2. `gh pr checks --watch` → ci-ok **빨간불** 확인
3. PR 화면의 **Merge 버튼 비활성** + "Required status check ci-ok is failing" 스크린샷
4. 터미널에서 병합 시도 → **거부 출력 원문 캡처** (이게 §20 K의 증거):
   ```
   gh pr merge --squash
   ```
5. 실패 커밋 제거 → ci-ok green → 병합 → 브랜치 삭제

## 상태

- [x] ci-ok 게이트가 실패 시 red (run 30069836553로 실증)
- [x] 전 잡 통과 시 green (run 30070254770)
- [ ] 룰셋 적용 — **GitHub Pro 반영 대기** (영준이 결제 → `gh api ... rulesets` 403이 `[]`로 바뀌면 위 명령 실행)
- [ ] 고의 실패 PR 병합 차단 실증 — 룰셋 적용 후
