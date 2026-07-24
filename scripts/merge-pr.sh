#!/usr/bin/env bash
# 병합 차단 폴백(옵션 B) — CI가 green일 때만 PR을 병합한다.
#
# GitHub Pro가 없어 웹 Merge 버튼을 기계로 막을 수 없으므로(ADR-0011),
# 병합은 반드시 이 스크립트로만 한다. 웹 버튼 사용 금지(CLAUDE.md).
#
# 사용:  bash scripts/merge-pr.sh <PR번호>
#   예:  bash scripts/merge-pr.sh 1
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "사용법: bash scripts/merge-pr.sh <PR번호>"
  echo "  PR 목록:  gh pr list"
  exit 2
fi

pr="$1"

echo "PR #$pr 의 CI를 확인합니다 (green일 때까지 대기)..."
# --required 플래그는 쓰지 않는다 — required check가 설정돼 있지 않으면
# (Pro 없음) '검사 없음'으로 통과 처리돼 게이트가 무의미해진다.
# 모든 체크가 통과할 때까지 기다리고, 하나라도 실패하면 즉시 종료한다.
if ! gh pr checks "$pr" --watch --fail-fast; then
  echo ""
  echo "✗ CI가 통과하지 않았습니다. 병합을 중단합니다."
  echo "  실패 원문:  gh run view --log-failed   또는 Actions 탭"
  exit 1
fi

echo ""
echo "✓ CI 통과. squash 병합합니다."
gh pr merge "$pr" --squash --delete-branch

echo "✓ 병합 완료."
