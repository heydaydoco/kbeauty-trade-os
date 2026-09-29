# ADR-0011: 병합 차단 폴백 (GitHub Pro 미결제)

- **상태**: 채택
- **날짜**: 2026-07-24
- **관련**: DESIGN.md §18.3, §20 K / WBS S0-1 DoD② / ADR-0010

**맥락** — ADR-0010에서 병합 차단은 GitHub Pro가 필요함을 확인했다. 영준이가 Pro 결제를 하지 않기로 결정(2026-07-24) → rulesets로 웹 Merge 버튼을 기계 차단할 수 없다.

**결정** — 폴백(옵션 B)을 채택한다. ① `.githooks/pre-push`가 push 전 토큰체인 없는 위생 검사(CRLF·.env·시크릿·대소문자 충돌)를 즉시 수행(`git config core.hooksPath .githooks`로 활성). ② 병합은 `scripts/merge-pr.sh`로만 — `gh pr checks --watch --fail-fast`로 CI green 확인 후에만 `gh pr merge --squash`. ③ CLAUDE.md에 "웹 Merge 버튼 금지, 병합은 스크립트로만" 명문화. **DoD② "병합 차단"은 기계 강제가 아니므로 "미충족(부채)"으로 명시**한다.

**근거** — 훅·스크립트는 규율이지 차단이 아니다(웹 버튼을 물리적으로 막지 못함). 그러나 1인 개발 + 비개발자 맥락에서 "병합은 스크립트로만"이라는 단일 규율 + CI green 강제 스크립트면 실무상 오병합을 막는다. `--required` 플래그는 쓰지 않는다 — required check 미설정 시 '검사 없음'으로 통과 처리돼 게이트가 무의미해진다.

**기각한 대안** — Pro 결제(영준이 결정으로 제외) / public 전환(기밀 노출, ADR-0010) / pre-push에서 전체 테스트 실행(DB·Docker 필요로 느리고 비개발자에게 취약 — CI에 위임).

**되돌리기 비용** — 낮음. **재검토 트리거: 협업자(2인 이상) 추가 또는 팀 배포 시점에 Pro(또는 Team) 재검토** — 그때는 웹 병합을 막는 기계 차단이 실제로 필요해진다. 트리거 발동 시 이 ADR을 개정하고 ADR-0010의 룰셋(infra/github/ruleset-main.json)을 적용한다.

**부기(2026-09-29 — 오너 지시: 클라우드 세션 자율 병합)**: 오너가 "PowerShell 없이 클라우드에서 끝까지, PR 생성·병합도 결정·개입 없이"를 지시했다. 결정 ②의 **게이트(CI green 확인 후에만 squash 병합)는 그대로**이고 수단만 늘었다 — 클라우드 세션은 `gh`가 없어 스크립트를 못 돌리므로, PR head SHA의 **전 체크런 success + mergeable clean**을 확인한 뒤 GitHub API로 squash 병합한다(`scripts/merge-pr.sh`와 같은 기준 — `--required`를 쓰지 않고 모든 체크를 본다). 웹 Merge 버튼 금지·pre-push 훅은 유지한다. 병합된 브랜치에는 커밋을 쌓지 않고 최신 main에서 재시작한다. **웹 세션 판정 절차는 생략**되며 판정 후보는 권장안으로 자율 확정 후 PROGRESS에 "자율 확정"으로 남긴다(오너가 사후에 뒤집을 수 있게 되돌리기 비용을 해당 ADR에 적는다). 첫 적용: PR #20(`c948e76e11cf`). **되돌리기 비용**: 낮음 — 이 부기를 지우고 CLAUDE.md의 해당 줄을 제거하면 종전 규율(스크립트로만)로 돌아간다.
