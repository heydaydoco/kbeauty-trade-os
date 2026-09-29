#!/usr/bin/env bash
# 복원 리허설 — 백업 세트를 **스크래치 DB에 실제로 복원**하고 검증한다 (§21 "백업은 복원 리허설까지가 백업").
#
# 사용: restore-rehearsal.sh [세트 디렉터리]   (생략하면 BACKUP_DIR의 최신 세트)
# 환경변수(필수): KBOS_BACKUP_PASSPHRASE, BACKUP_DIR(기본 /backups)
#   RESTORE_PGHOST/PORT/USER/PASSWORD — 스크래치 DB를 만들 수 있는 계정(슈퍼유저). 기본은 PG* 값.
# 검증: ⓐ 복호화·산출물 해시 ⓑ 테이블별 행수 == 매니페스트(여분 테이블도 실패) ⓒ 마이그레이션 head 일치
#       ⓓ FILE 문서 전건의 실물 존재+sha256 일치 ⓔ FILE 문서 수 == 매니페스트
# 결과: BACKUP_DIR/rehearsal-<UTC>.json (ok·검사 항목·실패 사유) — **실패해도 기록**하고 종료 코드 1.
# 스크래치 DB는 성공·실패와 무관하게 삭제한다. 운영 DB에는 아무것도 쓰지 않는다.
set -Eeuo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$here/common.sh"
umask 077

require_passphrase
BACKUP_DIR="${BACKUP_DIR:-/backups}"
export PGHOST="${RESTORE_PGHOST:-${PGHOST:-}}" PGPORT="${RESTORE_PGPORT:-${PGPORT:-5432}}"
export PGUSER="${RESTORE_PGUSER:-${PGUSER:-postgres}}" PGPASSWORD="${RESTORE_PGPASSWORD:-${PGPASSWORD:-}}"
export PGDATABASE=postgres

if [ "$#" -ge 1 ]; then
  set_dir="$1"
else
  latest="$(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' | grep -E "$SET_NAME_REGEX" | sort | tail -n1 || true)"
  [ -n "$latest" ] || die "복원할 백업 세트가 없습니다: $BACKUP_DIR"
  set_dir="$BACKUP_DIR/$latest"
fi
[ -f "$set_dir/manifest.json" ] || die "매니페스트가 없습니다: $set_dir"

started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
result="$BACKUP_DIR/rehearsal-$(date -u +%Y%m%dT%H%M%SZ).json"
work="$(mktemp -d "${TMPDIR:-/tmp}/kbos-rehearsal.XXXXXX")"
scratch="kbos_rehearsal_$(date -u +%s)_$$"
scratch_created=0
declare -a failures=() checks=()
fail() { failures+=("$1"); log "검증 실패: $1" >&2; }
ok() { checks+=("$1"); log "검증 통과: $1"; }

cleanup() {
  if [ "$scratch_created" -eq 1 ]; then
    psql -X -q -d postgres -c "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)" >/dev/null 2>&1 || true
  fi
  rm -rf "$work"
}
write_result() {
  local passed=false
  [ "${#failures[@]}" -eq 0 ] && passed=true
  {
    printf '{\n'
    printf '  "ok": %s,\n' "$passed"
    printf '  "set": %s,\n' "$(json_str "$(basename "$set_dir")")"
    printf '  "started_at_utc": %s,\n' "$(json_str "$started")"
    printf '  "finished_at_utc": %s,\n' "$(json_str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
    printf '  "checks": ['
    local i sep=""
    for i in "${!checks[@]}"; do printf '%s%s' "$sep" "$(json_str "${checks[$i]}")"; sep=", "; done
    printf '],\n  "failures": ['
    sep=""
    for i in "${!failures[@]}"; do printf '%s%s' "$sep" "$(json_str "${failures[$i]}")"; sep=", "; done
    printf ']\n}\n'
  } > "$result"
  chmod 600 "$result"
}
finish() {
  local code=$?
  cleanup
  if [ ! -f "$result" ]; then
    [ "$code" -ne 0 ] && [ "${#failures[@]}" -eq 0 ] && failures+=("예기치 않은 중단(종료 코드 $code)")
    write_result
  fi
  exit "$code"
}
trap finish EXIT

# 매니페스트에서 값 하나를 읽는다(단순 JSON — 한 줄에 한 항목 형식으로 우리가 쓴 파일만 읽는다).
mval() { grep -m1 "\"$1\":" "$set_dir/manifest.json" | sed -E 's/^[^:]*:[[:space:]]*"?([^",]*)"?,?[[:space:]]*$/\1/'; }
art_sha() { grep -m1 "\"$1\":" "$set_dir/manifest.json" | sed -E 's/.*"sha256": "([0-9a-f]+)".*/\1/'; }

log "복원 리허설 시작: $(basename "$set_dir") → 스크래치 DB $scratch"

# ⓐ 산출물 해시 → 복호화 → 평문 해시
for art in db.dump.enc files.tar.gz.enc; do
  [ -f "$set_dir/$art" ] || { fail "산출물 없음: $art"; exit 1; }
  if [ "$(file_sha256 "$set_dir/$art")" != "$(art_sha "$art")" ]; then
    fail "산출물 해시 불일치(변조·손상): $art"; exit 1
  fi
done
ok "산출물 해시 일치"
decrypt_file "$set_dir/db.dump.enc" "$work/db.dump" 2>/dev/null || { fail "DB 덤프 복호화 실패(패스프레이즈 오류·손상)"; exit 1; }
decrypt_file "$set_dir/files.tar.gz.enc" "$work/files.tar.gz" 2>/dev/null || { fail "파일 묶음 복호화 실패(패스프레이즈 오류·손상)"; exit 1; }
[ "$(file_sha256 "$work/db.dump")" = "$(mval db_plain_sha256)" ] || { fail "복호화한 DB 덤프의 해시가 매니페스트와 다릅니다"; exit 1; }
[ "$(file_sha256 "$work/files.tar.gz")" = "$(mval files_plain_sha256)" ] || { fail "복호화한 파일 묶음의 해시가 매니페스트와 다릅니다"; exit 1; }
ok "복호화·평문 해시 일치"

# 스크래치 DB 복원
psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$scratch\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C.UTF-8'"
scratch_created=1
pg_restore --no-owner --no-privileges --exit-on-error -d "$scratch" "$work/db.dump" || { fail "pg_restore 실패"; exit 1; }
ok "스크래치 DB 복원"
q() { psql -X -q -A -t -v ON_ERROR_STOP=1 -d "$scratch" -c "$1"; }

# ⓑ 행수
manifest_tables="$(sed -n '/"tables": {/,/^  },/p' "$set_dir/manifest.json" | grep -E '^    "' | sed -E 's/^    "([^"]+)": ([0-9]+),?$/\1 \2/')"
restored_tables="$(q "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")"
mismatch=0
while read -r tname tcount; do
  [ -n "$tname" ] || continue
  actual="$(q "SELECT count(*) FROM public.\"$tname\"" 2>/dev/null || echo ERR)"
  if [ "$actual" != "$tcount" ]; then fail "행수 불일치 $tname: 매니페스트 $tcount / 복원 $actual"; mismatch=1; fi
done <<< "$manifest_tables"
while read -r rname; do
  [ -n "$rname" ] || continue
  printf '%s\n' "$manifest_tables" | grep -q "^$rname " || { fail "매니페스트에 없는 테이블이 복원됨: $rname"; mismatch=1; }
done <<< "$restored_tables"
[ "$mismatch" -eq 0 ] && ok "테이블별 행수 일치"

# ⓒ 마이그레이션 head
restored_head="$(q "SELECT version_num FROM alembic_version")"
if [ "$restored_head" = "$(mval migration_head)" ]; then ok "마이그레이션 head 일치($restored_head)"; else fail "마이그레이션 head 불일치: 매니페스트 $(mval migration_head) / 복원 $restored_head"; fi

# ⓓ FILE 문서 전건 실물 검증
mkdir -p "$work/files"
tar -C "$work/files" -xzf "$work/files.tar.gz"
purged_filter=""
if [ "$(q "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='purged_at'")" = "1" ]; then
  purged_filter=" AND purged_at IS NULL"   # 물리 정리된 문서는 실물이 없는 것이 정상
fi
doc_total=0; doc_bad=0
while IFS='|' read -r stored sha; do
  [ -n "$stored" ] || continue
  doc_total=$((doc_total + 1))
  if [ ! -f "$work/files/$stored" ]; then fail "FILE 문서 실물 없음: $stored"; doc_bad=$((doc_bad + 1)); continue; fi
  if [ -n "$sha" ] && [ "$(file_sha256 "$work/files/$stored")" != "$sha" ]; then fail "FILE 문서 해시 불일치: $stored"; doc_bad=$((doc_bad + 1)); fi
done < <(q "SELECT stored_name || '|' || COALESCE(sha256, '') FROM documents WHERE storage_kind='FILE'$purged_filter ORDER BY id")
[ "$doc_bad" -eq 0 ] && ok "FILE 문서 ${doc_total}건 실물·해시 일치"

# ⓔ FILE 문서 수
if [ "$(q "SELECT count(*) FROM documents WHERE storage_kind='FILE'")" = "$(mval file_documents)" ]; then ok "FILE 문서 수 일치"; else fail "FILE 문서 수 불일치"; fi

write_result
if [ "${#failures[@]}" -gt 0 ]; then
  log "복원 리허설 실패: ${#failures[@]}건 — $result" >&2
  exit 1
fi
log "복원 리허설 성공: $result"
