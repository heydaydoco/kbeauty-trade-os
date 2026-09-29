# 백업·복원 운영 절차

> 근거: DESIGN §21 "백업은 DB+파일 세트 복원 리허설까지가 백업", ADR-0050.
> 구성: 스크립트 `infra/backup/`(backup.sh · restore-rehearsal.sh · scheduler.sh) + compose `backup` 서비스(postgres:16.14 이미지 재사용).

## 1. 무엇이 어떻게 백업되나

- **세트 = DB 덤프(pg_dump -Fc) + files/ 묶음(tar.gz) + manifest.json**, 두 산출물은 **AES-256으로 암호화**돼 `backups` 볼륨의 `kbos-YYYYMMDDTHHMMSSZ/` 폴더에 쌓인다(파일 600·폴더 700).
- **일일 백업 03:00 KST**, **복원 리허설 주 1회 일요일 04:00 KST**. 세트는 최근 **14개**만 보관(`KBOS_BACKUP_RETENTION`, 이름 규칙에 맞는 폴더만 삭제).
- 행수는 덤프와 **같은 스냅샷**에서 세어 manifest에 기록한다 — 백업 중 쓰기가 있어도 덤프와 manifest가 일치한다.
- **패스프레이즈(`KBOS_BACKUP_PASSPHRASE`, 16자 이상)가 없으면 백업은 실행을 거부한다**(평문 백업 금지). **패스프레이즈를 잃으면 백업을 풀 수 없다** — `.env.prod`와 **별도 장소**(금고·비밀번호 관리자)에 보관한다.

## 2. 켜는 법

- **운영(prod)**: 기본 포함. `.env.prod`에 `KBOS_BACKUP_PASSPHRASE`를 채우고 `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` — `backup` 서비스가 함께 뜬다.
- **개발(dev)**: 기본 미기동(영향 없음). 시험하려면 `.env`에 `KBOS_BACKUP_PASSPHRASE`를 넣고 `docker compose --profile backup up -d backup`.
- **⚠ 같은 디스크의 백업은 디스크 장애에 무력하다.** `backups` 볼륨(`kbos-prod_backups`)을 **호스트 밖(다른 디스크·클라우드 저장소)으로 주기 복제**하는 것은 운영자 몫이다(암호화돼 있어 복제 자체는 안전하다).

## 3. 상태 확인 (매일 1분)

1. 알림센터 — `백업 점검 필요 — …` 알림이 없으면 정상이다. 매일 08:00 KST **백업 신선도 감시**가 돈다: 최신 백업 26시간 초과 / 복원 리허설 8일 초과(또는 첫 리허설 부재) / 마지막 리허설 실패 → 관리자 알림.
2. 관리자 화면·API `GET /api/v1/system/backups` — 세트 목록(시각·마이그레이션 head·행수·크기)과 최근 리허설 결과. **조회는 감사 로그에 남는다.** 산출물 본체를 내려받는 API는 없다.
3. 저장소 점검 알림(`storage-monitor`, 05:00 KST) — 파일 저장소 여유율·문서 실물 유실·고아 파일.

## 4. 복원 리허설 판독

리허설은 최신 세트를 **스크래치 DB에 실제로 복원**해 검증한다. 결과는 `backups/rehearsal-<시각>.json`.

| 검사 | 통과 조건 |
|---|---|
| 산출물 해시 / 복호화 | 암호화 파일 해시가 manifest와 같고, 패스프레이즈로 풀리고, 풀린 평문 해시가 같다 |
| 행수 | 복원한 테이블별 행수 = manifest (여분 테이블도 실패) |
| 마이그레이션 head | 복원본 = manifest |
| FILE 문서 | 전건 실물 존재 + sha256 일치 (물리 정리된 문서는 제외), 개수 = manifest |

- 결과의 `ok: false`이면 **지금 백업으로는 복원이 안 될 수 있다** — 신선도 감시가 알린다. `failures` 항목을 보고 조치한다(패스프레이즈 오류·디스크 손상·저장소 유실 등).
- 수동 실행: `docker compose -f docker-compose.prod.yml exec backup bash /scripts/restore-rehearsal.sh` (세트를 지정하려면 폴더 경로를 인자로).

## 5. 실제 복원 절차 (장애 시)

> 복원은 마지막 백업 시점으로 **되돌리는 것**이다. 그 뒤 발생분은 [장애 운영 수칙](incident-sop.md) §2~§4로 다시 입력한다. **반드시 관리자 승인 후 진행한다.**

1. **서비스 중지**: `docker compose -f docker-compose.prod.yml stop api worker web` (DB·backup은 둔다).
2. **복원할 세트 선택**: 가장 최근의 **리허설을 통과한 세트**. 폴더 이름의 시각이 백업 시점(UTC)이다.
3. **작업 폴더로 복호화**(패스프레이즈 필요):
   ```
   docker compose -f docker-compose.prod.yml exec backup bash -c '
     set -e; S=/backups/<세트폴더>; W=/tmp/restore; mkdir -p $W
     openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:KBOS_BACKUP_PASSPHRASE -in $S/db.dump.enc -out $W/db.dump
     openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:KBOS_BACKUP_PASSPHRASE -in $S/files.tar.gz.enc -out $W/files.tar.gz'
   ```
4. **DB 복원**(슈퍼유저): 새 DB에 복원해 확인한 뒤 교체하는 것을 권장한다 — `createdb`로 `kbos_restore`를 만들고 `pg_restore --no-owner --no-privileges -d kbos_restore /tmp/restore/db.dump` → 행수 확인 → 기존 `kbos_dev`를 이름 변경으로 보존하고 `kbos_restore`를 `kbos_dev`로 변경 → `infra/postgres/init/20-grants.sql`을 다시 실행해 권한 복구(복원은 `--no-privileges`).
5. **파일 복원**: `files_data` 볼륨에 `tar -xzf /tmp/restore/files.tar.gz -C /data/files` (기존 파일 보존이 필요하면 먼저 다른 이름으로 옮긴다).
6. **서비스 기동** → `readyz` 확인 → 관리자가 [장애 운영 수칙](incident-sop.md) §4 검산을 수행한다.
7. **작업 폴더의 평문을 즉시 삭제**: `rm -rf /tmp/restore` (컨테이너 안이라 재기동해도 사라지지만 명시적으로 지운다).

## 6. 문제 해결

| 증상 | 원인·조치 |
|---|---|
| 백업 컨테이너가 계속 재시작 | `KBOS_BACKUP_PASSPHRASE` 누락/16자 미만 — 로그의 "실행을 거부" 메시지 확인 |
| `BACKUP_STALE` 알림 | 백업 컨테이너가 죽었거나 디스크가 가득 참 — `docker compose logs backup`, 저장소 여유율 확인 |
| `REHEARSAL_FAILED` 알림 | 결과 JSON의 `failures`를 본다. 해시 불일치=산출물 손상, 복호화 실패=패스프레이즈 변경/오류, 행수 불일치=백업 중 이상 |
| `NO_REHEARSAL` | 첫 일요일 리허설 전이면 정상(백업 8일 초과 시에만 알림) |
