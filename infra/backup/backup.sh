#!/usr/bin/env bash
# 일일 백업 — DB(pg_dump -Fc)와 files/ 를 한 세트로, 암호화해서 보관한다 (§21 · S2-4 PR-3 · ADR-0050).
#
# 환경변수(필수): KBOS_BACKUP_PASSPHRASE(16자 이상 — 없으면 실행 거부)
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE  (덤프 계정 — 소유 계정 kbos_owner)
# 선택: FILES_DIR(기본 /data/files) BACKUP_DIR(기본 /backups) KBOS_BACKUP_RETENTION(기본 14)
#
# 세트 = BACKUP_DIR/kbos-YYYYMMDDTHHMMSSZ/ { db.dump.enc, files.tar.gz.enc, manifest.json }  (파일 600·디렉터리 700)
#   manifest.json은 평문이다 — 행수·해시·크기·마이그레이션 head뿐이고 데이터 본문이 없다(앱이 읽기 전용으로 목록화).
# ★ 행수는 **pg_dump와 같은 스냅샷**(pg_export_snapshot)에서 센다 — 백업 도중 쓰기가 있어도 덤프와 매니페스트가 일치한다.
# ★ 임시 디렉터리에 만들고 성공했을 때만 최종 이름으로 rename한다 — 반쯤 만들어진 세트가 세트로 보이지 않는다.
set -Eeuo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$here/common.sh"
umask 077

require_passphrase
: "${PGDATABASE:?PGDATABASE 필요}"
FILES_DIR="${FILES_DIR:-/data/files}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION="${KBOS_BACKUP_RETENTION:-14}"
[[ "$RETENTION" =~ ^[0-9]+$ ]] && [ "$RETENTION" -ge 1 ] || die "KBOS_BACKUP_RETENTION은 1 이상의 정수여야 합니다."
[ -d "$FILES_DIR" ] || die "파일 저장소가 없습니다: $FILES_DIR"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/kbos-$stamp"
tmp="$BACKUP_DIR/.tmp-kbos-$stamp-$$"
[ ! -e "$final" ] || die "같은 시각의 세트가 이미 있습니다: $final"
snap_pid=""
cleanup() {
  [ -n "$snap_pid" ] && kill "$snap_pid" 2>/dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT
mkdir -p "$tmp"

log "백업 시작: DB=$PGDATABASE files=$FILES_DIR → $final"

# ── 1) 같은 스냅샷에서 덤프와 행수를 함께 뜬다 ───────────────────────────
coproc SNAP { psql -X -q -A -t -v ON_ERROR_STOP=1 -d "$PGDATABASE"; }
snap_pid=$SNAP_PID
printf '%s\n' "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" "SELECT 'SNAP:' || pg_export_snapshot();" >&"${SNAP[1]}"
snapshot=""
while IFS= read -r -u "${SNAP[0]}" line; do
  case "$line" in SNAP:*) snapshot="${line#SNAP:}"; break ;; esac
done
[ -n "$snapshot" ] || die "스냅샷을 내보내지 못했습니다."

cat >&"${SNAP[1]}" <<'SQL'
SELECT 'CNT:' || tablename || ':' ||
       (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, tablename), false, true, '')))[1]::text
  FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
SELECT 'HEAD:' || version_num FROM alembic_version;
SELECT 'FILEDOCS:' || count(*) FROM documents WHERE storage_kind = 'FILE';
SELECT 'END';
SQL
declare -a table_names=() table_counts=()
head_ver=""; file_docs=""
while IFS= read -r -u "${SNAP[0]}" line; do
  case "$line" in
    CNT:*) rest="${line#CNT:}"; table_names+=("${rest%%:*}"); table_counts+=("${rest#*:}") ;;
    HEAD:*) head_ver="${line#HEAD:}" ;;
    FILEDOCS:*) file_docs="${line#FILEDOCS:}" ;;
    END) break ;;
  esac
done
[ -n "$head_ver" ] || die "마이그레이션 head(alembic_version)를 읽지 못했습니다."
[ "${#table_names[@]}" -gt 0 ] || die "public 스키마에 테이블이 없습니다."

# 테스트 전용 훅 — 스냅샷을 잡은 뒤 덤프 전에 명령을 실행한다(스냅샷 일관성 검증에서 "그 사이의 쓰기"를 만든다).
[ -z "${KBOS_BACKUP_TEST_AFTER_SNAPSHOT_CMD:-}" ] || bash -c "$KBOS_BACKUP_TEST_AFTER_SNAPSHOT_CMD"

pg_dump -Fc --snapshot="$snapshot" -f "$tmp/db.dump" "$PGDATABASE"

printf '%s\n' "COMMIT;" '\q' >&"${SNAP[1]}" || true
wait "$snap_pid" 2>/dev/null || true
snap_pid=""

# ── 2) files/ 를 묶고, 둘 다 암호화 ──────────────────────────────────────
tar -C "$FILES_DIR" -czf "$tmp/files.tar.gz" .
db_plain_sha="$(file_sha256 "$tmp/db.dump")"
files_plain_sha="$(file_sha256 "$tmp/files.tar.gz")"
encrypt_file "$tmp/db.dump" "$tmp/db.dump.enc"
encrypt_file "$tmp/files.tar.gz" "$tmp/files.tar.gz.enc"
rm -f "$tmp/db.dump" "$tmp/files.tar.gz"   # 평문은 남기지 않는다

# ── 3) 매니페스트 ───────────────────────────────────────────────────────
{
  printf '{\n'
  printf '  "format_version": 1,\n'
  printf '  "created_at_utc": %s,\n' "$(json_str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
  printf '  "database": %s,\n' "$(json_str "$PGDATABASE")"
  printf '  "migration_head": %s,\n' "$(json_str "$head_ver")"
  printf '  "kdf": "aes-256-cbc/pbkdf2-sha256-600000",\n'
  printf '  "file_documents": %s,\n' "${file_docs:-0}"
  printf '  "tables": {\n'
  for i in "${!table_names[@]}"; do
    sep=","; [ "$i" -eq $((${#table_names[@]} - 1)) ] && sep=""
    printf '    %s: %s%s\n' "$(json_str "${table_names[$i]}")" "${table_counts[$i]}" "$sep"
  done
  printf '  },\n'
  printf '  "db_plain_sha256": "%s",\n' "$db_plain_sha"
  printf '  "files_plain_sha256": "%s",\n' "$files_plain_sha"
  printf '  "artifacts": {\n'
  printf '    "db.dump.enc": {"sha256": "%s", "bytes": %s},\n' "$(file_sha256 "$tmp/db.dump.enc")" "$(file_bytes "$tmp/db.dump.enc")"
  printf '    "files.tar.gz.enc": {"sha256": "%s", "bytes": %s}\n' "$(file_sha256 "$tmp/files.tar.gz.enc")" "$(file_bytes "$tmp/files.tar.gz.enc")"
  printf '  }\n'
  printf '}\n'
} > "$tmp/manifest.json"

chmod 600 "$tmp"/db.dump.enc "$tmp"/files.tar.gz.enc "$tmp"/manifest.json
chmod 700 "$tmp"
mv "$tmp" "$final"
trap - EXIT
log "백업 완료: $final"

# ── 4) 보관 — 이름 규칙에 맞는 세트만, 오래된 것부터 RETENTION개만 남기고 삭제 ─
mapfile -t sets < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' | grep -E "$SET_NAME_REGEX" | sort)
excess=$(( ${#sets[@]} - RETENTION ))
if [ "$excess" -gt 0 ]; then
  for name in "${sets[@]:0:$excess}"; do
    rm -rf "${BACKUP_DIR:?}/$name"
    log "보관 초과 세트 삭제: $name"
  done
fi
