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
