# 운영(prod) 실행 절차

> S0-1 단계에서는 **파일만 준비**돼 있고 실배포(VM·도메인·TLS)는 후속이다.
> 이 문서는 규칙을 고정한다.

## 절대 규칙

- dev와 **항상 명시적으로 분리**해서 실행한다. prod은 절대 무플래그로 뜨지 않는다:
  ```
  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
  ```
- `.env.prod`는 `.env.prod.example`을 복사해 실제 값으로 채운다. **커밋 금지**(.gitignore).
  - 모든 값이 필수다. 비우면 기동이 거부된다(`${VAR:?}`).
  - `DEV_ONLY_DO_NOT_USE_IN_PROD` 마커가 든 값을 넣으면 앱이 시작을 거부한다(2중 방어).
- prod 명령에 **`-v`(볼륨 삭제)를 절대 붙이지 않는다** — 운영 데이터가 사라진다.
- 마이그레이션은 기본 up에서 제외돼 있다(profiles). 배포 절차에서 수동 실행:
  ```
  docker compose -f docker-compose.prod.yml --env-file .env.prod --profile migrate run --rm migrate
  ```

## 렌더 검증(배포 전)

```
docker compose -f docker-compose.prod.yml --env-file .env.prod config -q
```
- 출력 없이 통과하면 문법 OK. (실제 값이 든 `config` 출력을 아무 데도 붙여넣지 말 것 — 시크릿 노출.)

## 포트·노출

- db·api는 포트를 게시하지 않는다(내부망 전용).
- web(nginx)만 `127.0.0.1:8080`. 외부 공개는 앞단에 리버스 프록시 + TLS 종단을 두는 것을 전제로 한다.
- `/api/v1/system/readyz`는 nginx가 사설 대역에서만 허용(내부 상태 점검용).

## 시크릿 업그레이드 경로

현재는 600 권한 env 파일. 향후 Docker secrets / 외부 시크릿 매니저로 갈 때는
compose의 `environment:`를 `*_FILE` 규약으로 바꾼다(이 문서 갱신 + ADR).

## 백업·복원·장애 수칙 (S2-4 PR-3)

- 백업은 compose `backup` 서비스가 한다(운영 기본 포함 — `.env.prod`의 `KBOS_BACKUP_PASSPHRASE` 필수, 16자 이상). 절차·판독·복원은 [backup-restore.md](backup-restore.md).
- **`backups` 볼륨을 호스트 밖으로 복제**하는 것은 운영자 몫이다(같은 디스크의 백업은 디스크 장애에 무력).
- 패스프레이즈는 `.env.prod`와 **별도 장소**에 보관한다 — 잃으면 백업을 풀 수 없다.
- 시스템이 멈추면 [incident-sop.md](incident-sop.md)(수기 기록 → 소급 입력 → 검산 1회). 인쇄용 양식은 [forms/manual-record-form.md](forms/manual-record-form.md).
- 배치는 **worker 기동 시 자동 등록**된다(멱등 — 이미 있는 잡·관리자가 끈 상태는 건드리지 않는다). 수동 등록이 필요하면 `python -m app.cli register-jobs`.
- 문서 실물 물리 정리는 **기본 OFF**(`KBOS_FILE_PURGE_ENABLED=false`)다. 켜기 전에 `python -m app.cli purge-files`(dry-run)로 후보를 확인한다 — 되돌릴 수 없다.

