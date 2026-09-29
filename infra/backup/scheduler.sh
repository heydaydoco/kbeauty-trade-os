#!/usr/bin/env bash
# 백업 스케줄 루프 — 일일 백업 03:00 KST, 주 1회 복원 리허설 일요일 04:00 KST (S2-4 PR-3 · ADR-0050).
# 1분마다 KST 시각을 보고, 그 분에 해당하면 실행한다. 같은 분에 두 번 돌지 않게 마지막 실행 키를 기억한다.
# 한 번 실패해도 루프는 죽지 않는다 — 실패는 종료 코드가 아니라 **결과 파일과 신선도 감시(backup-freshness)** 가 알린다.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$here/common.sh"
require_passphrase   # 없으면 시작 자체를 거부(fail-closed)

last_backup=""; last_rehearsal=""
log "백업 스케줄러 시작(일일 03:00 KST · 일요일 04:00 KST 복원 리허설)"
while true; do
  kst_day="$(TZ=Asia/Seoul date +%Y-%m-%d)"
  kst_hm="$(TZ=Asia/Seoul date +%H:%M)"
  kst_dow="$(TZ=Asia/Seoul date +%u)"   # 7 = 일요일
  if [ "$kst_hm" = "03:00" ] && [ "$last_backup" != "$kst_day" ]; then
    last_backup="$kst_day"
    bash "$here/backup.sh" || log "백업 실패(다음 신선도 감시가 알린다)" >&2
  fi
  if [ "$kst_dow" = "7" ] && [ "$kst_hm" = "04:00" ] && [ "$last_rehearsal" != "$kst_day" ]; then
    last_rehearsal="$kst_day"
    bash "$here/restore-rehearsal.sh" || log "복원 리허설 실패(결과 파일 참조)" >&2
  fi
  sleep 30
done
