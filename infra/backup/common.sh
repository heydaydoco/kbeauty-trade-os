# shellcheck shell=bash
# 백업·복원 공용 함수 — backup.sh / restore-rehearsal.sh 가 source 한다 (S2-4 PR-3 · ADR-0050).
#
# 이 스크립트들은 postgres:16.14 이미지 안에서 돈다(pg_dump·pg_restore·psql·openssl·tar·sha256sum 동봉).
# 앱 이미지에 클라이언트를 넣지 않는다. 새 비밀을 도입하지 않는다 — 덤프는 기존 소유 계정(kbos_owner),
# 복원 리허설은 기존 postgres 슈퍼유저(스크래치 DB를 만들어야 하므로)를 쓴다.

# 오류 메시지는 한국어로 stderr — 종료 코드는 호출자가 정한다.
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "오류: $*" >&2; exit 1; }

# 백업 세트 디렉터리 이름 규칙 — 이 패턴에 맞는 것만 백업 세트로 취급(삭제·조회 대상). 다른 파일은 절대 건드리지 않는다.
SET_NAME_REGEX='^kbos-[0-9]{8}T[0-9]{6}Z$'

# 문자열을 JSON 문자열로 이스케이프(따옴표·역슬래시·제어문자) — jq가 없다.
json_str() {
  local s=${1//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

file_sha256() { sha256sum "$1" | cut -d' ' -f1; }
file_bytes() { stat -c %s "$1"; }

# AES-256-CBC + PBKDF2(600000회) — 패스프레이즈는 환경변수로만 받는다(프로세스 목록에 노출 금지).
encrypt_file() { openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -pass env:KBOS_BACKUP_PASSPHRASE -in "$1" -out "$2"; }
decrypt_file() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:KBOS_BACKUP_PASSPHRASE -in "$1" -out "$2"; }

require_passphrase() {
  # ★ fail-closed — 패스프레이즈가 없으면 평문 백업을 만들지 않고 즉시 거부한다.
  [ -n "${KBOS_BACKUP_PASSPHRASE:-}" ] || die "KBOS_BACKUP_PASSPHRASE가 비어 있어 실행을 거부합니다(평문 백업 금지)."
  [ "${#KBOS_BACKUP_PASSPHRASE}" -ge 16 ] || die "KBOS_BACKUP_PASSPHRASE는 16자 이상이어야 합니다."
}
