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


## S3-1 운영 개시 (전표 사슬·승인·게이트) — 운영에서 전표를 쓰기 전에 1회

> 아래 항목은 **각 기능이 병합된 뒤에만** 적용된다 — 항목 끝의 *(S3-1 PR-N 구현 후 유효)*가 그 기준이다. 이 절은 DESIGN §2·§7.3·§7.10 [M4] 보강(S3-1 계획 자율 확정 2026-09-30)의 운영 귀결이다. 등록 전 상태에서 막히는 것은 **오류가 아니라 fail-closed 정상 동작**이다.

1. **관리자 계정을 2명 이상** 둔다 — 제2 결재 계정. 여신 초과 SO의 승인은 요청자와 다른 사람만 할 수 있어서, 관리자가 1명이면 자기 기안을 승인할 수 없다(정상 동작). *(S3-1 PR-9 구현 후 유효)*
2. **결재선을 등록**한다(관리 화면 — 승인 유형 `SO_CREDIT_EXCEEDED`, **사용하는 통화별로 임계 0**, 승인 역할 지정). 등록 전에는 여신을 초과한 SO가 확정되지 않는다(정상 동작). *(S3-1 PR-9 구현 후 유효)*
3. **`/settings/policies`에서 PI 게이트 모드와 단가 편차 허용치를 저장**한다. 저장 전에는 PI 게이트가 **차단(BLOCK)** 으로, 허용치는 0으로 동작하고 화면에 적색으로 표시된다(정상 동작 — 저장 후 완화 여부는 오너 판단, 부채 P-16). *(S3-1 PR-4 구현 후 유효)*
4. **통화별 은행 계좌를 등록**한다(관리자 — PI에 복사되는 수취인·은행·계좌·SWIFT). 계좌가 없는 통화로는 PI를 만들 수 없다. *(S3-1 PR-6 구현 후 유효)*
5. **거래처 영문명·주소**를 채운다(마스터 CSV 왕복 — 열 끝에 추가된 `name_en`·`address_en`). *(S3-1 PR-3 구현 후 유효)*
6. **여신한도를 입력**한다(관리자 전용). **시스템 도입 전에 발생한 미수는 여신 노출에 반영되지 않으므로(화면에 '미수 미반영' 배지), 한도를 그 미수를 뺀 잔여 한도로 설정한다**(부채 P-51 — S3-3 이월 채권 반입 시 재판정). *(S3-1 PR-3·PR-12 구현 후 유효)*
7. **SKU MOQ와 바이어 품번 매핑**을 입력한다(MOQ가 비어 있으면 MOQ 게이트는 통과, 품번 매핑이 없으면 인테이크 확정이 막힌다). *(S3-1 PR-3 구현 후 유효)*
8. **L/C 결제유형은 S3-1에서 닫혀 있다**(feature flag 행을 만드는 경로가 없어 fail-closed — S3-3에서 열린다, 부채 P-10). 선수금 T/T·후불 T/T만 선택 가능하다. *(S3-1 PR-5 구현 후 유효)*
9. **관리 화면의 잡이 12행**인지 확인한다(기존 7행 + 아래 5행). *(S3-1 PR-16 구현 후 유효 — 행은 잡을 추가하는 PR마다 늘어난다)*
10. **통화를 섞어 거래하는 거래처는 여신 한도 통화를 KRW로** 둔다 — 한도 통화와 전표 통화를 환산할 수 없으면 승인 경로 없이 확정이 거부된다(부채 P-48). *(S3-1 PR-9·PR-12 구현 후 유효)*

### 잡 표 — S3-1이 추가하는 5행 (기존 7행: certification-sweep 06:00 · outbox-dispatch interval@1 · deadline-scan 06:30 · daily-briefing 09:00 · stagnation-scan 07:00 · storage-monitor 05:00 · backup-freshness 08:00)

| 코드 | 스케줄(KST) | 하는 일 | 유효 시점 |
|---|---|---|---|
| `idempotency-purge` | daily@04:20 | 기간이 지난 멱등 키 행 삭제(기술 행만 — 전표·원장 무관) | S3-1 PR-16 구현 후 유효 |
| `session-purge` | daily@04:25 | 기간이 지난 세션 행 삭제(기술 행만) | S3-1 PR-16 구현 후 유효 |
| `trade-docs-totals-verify` | daily@05:30 | 전표 라인합=헤더합 검산 — 읽기 전용, 불일치는 관리자 알림(자동 보정 없음) | S3-1 PR-5 구현 후 유효 |
| `document-expiry-sweep` | daily@06:10 | 유효기간이 지난 QT·PI(발행 상태, 살아 있는 후속 없음)를 만료로 전환 | S3-1 PR-6 구현 후 유효 |
| `approval-stagnation-scan` | daily@07:10 | 결재 대기 정체 독촉 알림(승인 상태는 바꾸지 않음) | S3-1 PR-9 구현 후 유효 |

- 잡은 worker 기동 시 자동 등록되고(멱등), 수동 등록은 `python -m app.cli register-jobs`다(위 [백업·복원·장애 수칙](#백업복원장애-수칙-s2-4-pr-3) 절 참조).
