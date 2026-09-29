#!/usr/bin/env bash
# 백업 스케줄 루프 — 일일 백업 03:00 KST, 주 1회 복원 리허설 일요일 04:00 KST (S2-4 PR-3 · ADR-0050).
# 30초마다 판단한다(아래 plan) — 디스크의 세트·결과 기록이 기준이라 재기동·다운에도 만회하고 중복 실행하지 않는다.
# 한 번 실패해도 루프는 죽지 않는다 — 실패는 종료 코드가 아니라 **결과 파일과 신선도 감시(backup-freshness)** 가 알린다.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$here/common.sh"
require_passphrase   # 없으면 시작 자체를 거부(fail-closed)

# ── 무엇을 돌릴지 판단(순수 판단 — 실행하지 않는다; --plan으로 시험한다) ───────────────────
# ★ "정확히 03:00 분"이 아니라 "오늘 KST 03:00 이후이고 오늘 세트가 아직 없으면"이다 — 컨테이너·호스트가 내려가 있다 올라와도
#   그날 백업이 건너뛰어지지 않고(catch-up), 03:00:30에 재기동해도 같은 날 두 번 돌지 않는다(디스크의 세트 기록이 기준).
#   실패한 시도는 RETRY_MINUTES(기본 30분) 안에 다시 하지 않는다(실패 폭주 방지).
RETRY_MINUTES="${KBOS_BACKUP_RETRY_MINUTES:-30}"
now_epoch() { if [ -n "${KBOS_SCHEDULER_NOW:-}" ]; then date -u -d "$KBOS_SCHEDULER_NOW" +%s; else date -u +%s; fi; }
kst_field() { TZ=Asia/Seoul date -d "@$1" "+$2"; }
stamp_to_epoch() { date -u -d "${1:0:4}-${1:4:2}-${1:6:2} ${1:9:2}:${1:11:2}:${1:13:2} UTC" +%s; }
newest_epoch() {   # $1=패턴(kbos-* | rehearsal-*) — 디스크에 있는 가장 최근 항목의 시각(없으면 0)
  local name
  name="$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -name "$1" -printf '%f\n' | grep -E "^(kbos|rehearsal)-[0-9]{8}T[0-9]{6}Z(\.json)?$" | sort | tail -n1 || true)"
  [ -n "$name" ] || { echo 0; return; }
  name="${name#*-}"; name="${name%.json}"
  stamp_to_epoch "$name"
}
plan() {
  local now day hour dow newest_b newest_r
  now="$(now_epoch)"
  day="$(kst_field "$now" %Y-%m-%d)"; hour="$(kst_field "$now" %H)"; dow="$(kst_field "$now" %u)"
  newest_b="$(newest_epoch 'kbos-*')"; newest_r="$(newest_epoch 'rehearsal-*')"
  local backup_today=0 rehearsal_today=0
  [ "$newest_b" -gt 0 ] && [ "$(kst_field "$newest_b" %Y-%m-%d)" = "$day" ] && backup_today=1
  [ "$newest_r" -gt 0 ] && [ "$(kst_field "$newest_r" %Y-%m-%d)" = "$day" ] && rehearsal_today=1
  local since_attempt=$(( now - ${last_attempt_epoch:-0} ))
  if [ "$((10#$hour))" -ge 3 ] && [ "$backup_today" -eq 0 ] && [ "$since_attempt" -ge $((RETRY_MINUTES * 60)) ]; then echo backup; fi
  if [ "$dow" = "7" ] && [ "$((10#$hour))" -ge 4 ] && [ "$rehearsal_today" -eq 0 ] && [ "$newest_b" -gt 0 ] && [ "$since_attempt" -ge $((RETRY_MINUTES * 60)) ]; then echo rehearsal; fi
}

BACKUP_DIR="${BACKUP_DIR:-/backups}"
last_attempt_epoch=0
if [ "${1:-}" = "--plan" ]; then plan; exit 0; fi   # 시험용: 지금(KBOS_SCHEDULER_NOW) 무엇을 돌릴지만 출력

log "백업 스케줄러 시작(일일 03:00 KST 이후 1회 · 일요일 04:00 KST 이후 복원 리허설 — 놓치면 다음 판단에 만회)"
while true; do
  actions="$(plan)"
  if echo "$actions" | grep -qx backup; then
    last_attempt_epoch="$(now_epoch)"
    bash "$here/backup.sh" || log "백업 실패(다음 신선도 감시가 알린다)" >&2
  fi
  # 리허설은 백업 뒤에 다시 판단한다(직렬 실행 — 백업이 길어져도 같은 날 안에 만회)
  if plan | grep -qx rehearsal; then
    last_attempt_epoch="$(now_epoch)"
    bash "$here/restore-rehearsal.sh" || log "복원 리허설 실패(결과 파일 참조)" >&2
  fi
  sleep 30
done
