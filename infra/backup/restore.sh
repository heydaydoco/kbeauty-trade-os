#!/usr/bin/env bash
# 실제 복원 — 백업 세트를 **새 DB**로 복원하고 파일을 지정 폴더에 푼다 (S2-4 PR-3 · ADR-0050 · docs/runbook/backup-restore.md §5).
#
# 사용: restore.sh <세트 디렉터리> --target-db <새 DB 이름> [--files-dir <빈 폴더>]
# 환경변수(필수): KBOS_BACKUP_PASSPHRASE, RESTORE_PGHOST/PORT/USER/PASSWORD(슈퍼유저) — 기본은 PG* 값
#   RESTORE_ROLE(기본 kbos_owner) — 복원한 객체의 소유 역할. 권한(ACL)은 백업 그대로 복원한다(append-only 유지).
# ★ 운영 DB를 덮어쓰지 않는다 — **새 이름**으로만 만들고(이미 있으면 실패), 교체는 사람이 확인 후 이름 변경으로 한다.
# ★ 파일은 **빈 폴더**에만 푼다(--allow-nonempty를 줘야 기존 파일 위에 푼다).
# ★ 평문 덤프·묶음은 임시 폴더에 잠깐만 있고 종료 시 항상 지운다.
set -Eeuo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$here/common.sh"
umask 077

[ "$#" -ge 1 ] || die "사용법: restore.sh <세트 디렉터리> --target-db <새 DB 이름> [--files-dir <빈 폴더>] [--allow-nonempty]"
set_dir="$1"; shift
target_db=""; files_dir=""; allow_nonempty=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --target-db) target_db="${2:-}"; shift 2 ;;
    --files-dir) files_dir="${2:-}"; shift 2 ;;
    --allow-nonempty) allow_nonempty=1; shift ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done
[[ "$target_db" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || die "--target-db는 소문자·숫자·밑줄 이름이어야 합니다(예: kbos_restore)."
require_passphrase
[ -f "$set_dir/manifest.json" ] || die "매니페스트가 없습니다: $set_dir"
role="${RESTORE_ROLE:-kbos_owner}"
export PGHOST="${RESTORE_PGHOST:-${PGHOST:-}}" PGPORT="${RESTORE_PGPORT:-${PGPORT:-5432}}"
export PGUSER="${RESTORE_PGUSER:-${PGUSER:-postgres}}" PGPASSWORD="${RESTORE_PGPASSWORD:-${PGPASSWORD:-}}"
if [ -n "$files_dir" ]; then
  [ -d "$files_dir" ] || die "파일 폴더가 없습니다: $files_dir"
  if [ "$allow_nonempty" -eq 0 ] && [ -n "$(ls -A "$files_dir")" ]; then die "파일 폴더가 비어 있지 않습니다: $files_dir (--allow-nonempty로 덮어쓰기 허용)"; fi
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/kbos-restore.XXXXXX")"
trap 'rm -rf "$work"' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

log "복원 시작: $(basename "$set_dir") → DB $target_db"
if msg="$(verify_and_decrypt "$work")"; then log "산출물 해시·복호화 확인"; else die "$msg"; fi
[ "$(psql -X -q -A -t -d postgres -c "SELECT count(*) FROM pg_database WHERE datname='$target_db'")" = "0" ] || die "이미 있는 DB입니다: $target_db (덮어쓰지 않습니다 — 다른 이름을 쓰세요)"
psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$target_db\" OWNER \"$role\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C.UTF-8'"
if ! pg_restore --no-owner --role="$role" --exit-on-error -d "$target_db" "$work/db.dump"; then
  psql -X -q -d postgres -c "DROP DATABASE IF EXISTS \"$target_db\" WITH (FORCE)" >/dev/null 2>&1 || true
  die "pg_restore 실패 — 만든 DB($target_db)를 지웠습니다."
fi
log "DB 복원 완료: $target_db (소유 $role, 권한 복원)"
if [ -n "$files_dir" ]; then
  tar -C "$files_dir" -xzf "$work/files.tar.gz"
  log "파일 복원 완료: $files_dir"
fi
log "복원 끝 — 서비스 교체 전 docs/runbook/backup-restore.md §5의 확인 단계를 수행하세요."
