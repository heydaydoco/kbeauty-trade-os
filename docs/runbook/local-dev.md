# 개발 환경 실행 (영준이용 · 클릭/명령 단위)

PowerShell을 열고 아래를 위에서부터 순서대로. `>` 로 시작하는 줄이 붙여넣을 명령이다.

## 0. 처음 한 번만

1. Docker Desktop이 켜져 있는지 확인(작업표시줄 고래 아이콘이 초록).
2. 프로젝트 폴더로 이동:
   ```
   > cd C:\Users\PC\orca\kbeauty-trade-os
   ```
3. 환경변수 파일 만들기(**반드시 이 명령으로** — 메모장/`>`로 만들면 인코딩이 깨져 안 됨):
   ```
   > Copy-Item .env.example .env
   ```

## 1. 기동

```
> docker compose up -d --build
```
- 처음엔 이미지 빌드로 몇 분 걸린다. 두 번째부터는 빠르다.

상태 확인:
```
> docker compose ps
```
- `db`=healthy, `db-init`=exited(0), `migrate`=exited(0), `api`=healthy, `web`=running 이면 정상.
  (`db-init`·`migrate`가 exited인 건 "할 일 하고 정상 종료"라는 뜻 — 오류 아님.)

## 2. 눈으로 확인

- 브라우저에서 **http://localhost:5173** → "API 연결: 정상" + 환경 dev + KST 시각.
- API 직접 확인(선택):
  ```
  > curl.exe http://localhost:8000/api/v1/system/readyz
  ```
  `{"status":"ok",...}` 가 나오면 정상.

## 3. "이상"이 뜨면

- 화면에 한국어로 "API 연결: 이상"이 뜨면 백엔드가 아직 안 떴거나 죽은 것.
  ```
  > docker compose ps
  > docker compose logs api --tail 30
  ```
- api가 `Up (healthy)`가 될 때까지 20~30초 기다렸다가 새로고침.

## 4. 테스트 돌리기 (완료 보고 전 필수)

```
> docker compose run --rm api pytest -q
```
- 마지막 줄이 `NNN passed` 면 통과. 이 출력 전체를 복사해서 나(클로드)에게 붙여넣어 줘.

프런트 테스트:
```
> docker compose run --rm web npm run test -- --run
```

## 5. 끄기 / 리셋

- 그냥 끄기(데이터 유지):
  ```
  > docker compose down
  ```
- **DB 데이터까지 싹 지우고 처음부터**(개발용만, 운영과 무관):
  ```
  > .\scripts\dev-reset.ps1
  ```
  안내에 따라 `RESET` 을 그대로 타이핑해야 진행된다.

## 자주 나는 에러 5종

| 증상 | 원인 | 조치 |
|---|---|---|
| `.env` 관련 오류로 기동 실패 | `.env`가 없음 | `Copy-Item .env.example .env` |
| 포트 충돌(5433/8000/5173) | 다른 프로그램이 점유 | `netstat -ano \| findstr :8000` 로 확인 후 그 프로그램 종료 |
| 화면은 뜨는데 "이상" | 백엔드 기동 전 | 20~30초 기다렸다 새로고침 / `docker compose ps` |
| 코드 고쳤는데 반영 안 됨 | (드묾) 파일 감시 실패 | `docker compose restart api` 또는 `web` |
| DB 권한/역할 SQL 고쳤는데 반영 안 됨 | 부트스트랩은 재기동 시 실행 | `docker compose up -d db-init` |

## pgAdmin으로 DB 들여다보기 (선택)

```
> docker compose --profile tools up -d pgadmin
```
- (pgAdmin은 아직 S0-1에 미포함일 수 있음 — 없으면 PROGRESS의 부채 항목 참고.)
- **주의**: 조회 전용으로만. 여기서 데이터를 손으로 고치지 말 것(§18.2 규율).
