#!/usr/bin/env bash
# 복원 리허설 — 백업 세트를 **스크래치 DB에 실제로 복원**하고 검증한다 (§21 "백업은 복원 리허설까지가 백업").
#
# 사용: restore-rehearsal.sh [세트 디렉터리]   (생략하면 BACKUP_DIR의 최신 세트)
# 환경변수(필수): KBOS_BACKUP_PASSPHRASE, BACKUP_DIR(기본 /backups)
#   RESTORE_PGHOST/PORT/USER/PASSWORD — 스크래치 DB를 만들 수 있는 계정(슈퍼유저). 기본은 PG* 값.
#   RESTORE_ROLE(선택, 예: kbos_owner)·RESTORE_APP_ROLE(선택, 예: kbos_app) — 있으면 소유·권한까지 실제 복원과 같게 복원·검증(ⓕ)
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
  chmod 644 "$result"   # 앱이 읽는 메타(실패 사유 문자열 — 데이터 본문 없음)
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

log "복원 리허설 시작: $(basename "$set_dir") → 스크래치 DB $scratch"

# ⓐ 산출물 해시 → 복호화 → 평문 해시
if msg="$(verify_and_decrypt "$work")"; then ok "산출물 해시 일치·복호화·평문 해시 일치"; else fail "$msg"; exit 1; fi

# 스크래치 DB 복원
# RESTORE_ROLE(예: kbos_owner)이 있으면 실제 복원 절차와 같게 **소유를 그 역할로, 권한(ACL)은 그대로** 복원한다 —
# 그래야 "복원본으로 앱이 도는가"(ⓕ)까지 검증된다. 없으면(역할이 없는 클러스터) 소유·권한 없이 복원한다.
if [ -n "${RESTORE_ROLE:-}" ]; then
  psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$scratch\" OWNER \"$RESTORE_ROLE\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C.UTF-8'"
  scratch_created=1
  pg_restore --no-owner --role="$RESTORE_ROLE" --exit-on-error -d "$scratch" "$work/db.dump" || { fail "pg_restore 실패"; exit 1; }
else
  psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$scratch\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C.UTF-8'"
  scratch_created=1
  pg_restore --no-owner --no-privileges --exit-on-error -d "$scratch" "$work/db.dump" || { fail "pg_restore 실패"; exit 1; }
fi
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
# ★ 결과를 먼저 변수에 받는다 — `done < <(...)`(프로세스 치환)는 쿼리 실패가 set -e에 잡히지 않아 0건 검증으로 통과한다.
doc_rows="$(q "SELECT stored_name || '|' || COALESCE(sha256, '') FROM documents WHERE storage_kind='FILE'$purged_filter ORDER BY id")"
expected_docs="$(q "SELECT count(*) FROM documents WHERE storage_kind='FILE'$purged_filter")"
while IFS='|' read -r stored sha; do
  [ -n "$stored" ] || continue
  doc_total=$((doc_total + 1))
  if [ ! -f "$work/files/$stored" ]; then fail "FILE 문서 실물 없음: $stored"; doc_bad=$((doc_bad + 1)); continue; fi
  if [ -n "$sha" ] && [ "$(file_sha256 "$work/files/$stored")" != "$sha" ]; then fail "FILE 문서 해시 불일치: $stored"; doc_bad=$((doc_bad + 1)); fi
done <<< "$doc_rows"
[ "$doc_total" = "$expected_docs" ] || fail "FILE 문서 검증 건수 불일치: 대상 $expected_docs건 중 $doc_total건만 검증"
[ "$doc_bad" -eq 0 ] && [ "$doc_total" = "$expected_docs" ] && ok "FILE 문서 ${doc_total}건 실물·해시 일치"

# ⓔ FILE 문서 수
if [ "$(q "SELECT count(*) FROM documents WHERE storage_kind='FILE'")" = "$(mval file_documents)" ]; then ok "FILE 문서 수 일치"; else fail "FILE 문서 수 불일치"; fi

# ⓕ 복원본으로 앱이 도는가 — RESTORE_APP_ROLE(예: kbos_app)이 있을 때: 소유 역할 확인·앱 역할의 읽기 권한·append-only 유지
if [ -n "${RESTORE_ROLE:-}" ] && [ -n "${RESTORE_APP_ROLE:-}" ]; then
  wrong_owner="$(q "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner <> '$RESTORE_ROLE'")"
  [ "$wrong_owner" = "0" ] && ok "모든 테이블 소유자=$RESTORE_ROLE" || fail "소유자가 $RESTORE_ROLE 아닌 테이블 ${wrong_owner}개"
  if psql -X -q -v ON_ERROR_STOP=1 -d "$scratch" -c "SET ROLE \"$RESTORE_APP_ROLE\"; SELECT count(*) FROM documents" >/dev/null 2>&1; then
    ok "앱 역할($RESTORE_APP_ROLE)이 복원본을 읽을 수 있음"
  else
    fail "앱 역할($RESTORE_APP_ROLE)이 복원본의 documents를 읽지 못함(권한 복원 실패)"
  fi
  if [ "$(q "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='audit_log'")" = "1" ]; then
    if [ "$(q "SELECT has_table_privilege('$RESTORE_APP_ROLE', 'public.audit_log', 'UPDATE')")" = "f" ]; then
      ok "append-only 유지(audit_log에 앱 역할 UPDATE 불가)"
    else
      fail "append-only 권한이 복원되지 않음(audit_log에 앱 역할 UPDATE 가능)"
    fi
  fi
fi

write_result
# 결과 파일은 최근 30개만 보관한다(주 1회 — 무한 누적 방지).
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'rehearsal-*.json' -printf '%f\n' | sort | head -n -30 | while read -r old; do rm -f "$BACKUP_DIR/$old"; done
if [ "${#failures[@]}" -gt 0 ]; then
  log "복원 리허설 실패: ${#failures[@]}건 — $result" >&2
  exit 1
fi
log "복원 리허설 성공: $result"
