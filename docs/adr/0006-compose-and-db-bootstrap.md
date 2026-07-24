# ADR-0006: compose dev/prod 분리 + DB 부트스트랩 매체

- **상태**: 채택
- **날짜**: 2026-07-24
- **관련**: DESIGN.md §18.2 / WBS S0-1 DoD

**맥락** — §18.2("dev/prod compose 완전 분리")와 WBS DoD("`docker compose up` 한 번으로 dev 기동")를 동시에 만족해야 하고, 부트스트랩 SQL을 로컬과 CI가 같은 파일로 실행해야 한다.

**결정** — 루트 `docker-compose.yml`(dev, 무플래그) + 루트 `docker-compose.prod.yml`(prod, 항상 `-f` + `--env-file`) 두 파일. base+override·extends 미사용. 볼륨·컨테이너는 `name: kbos-dev`/`kbos-prod`로 네임스페이스 분리. dev 포트 전부 `127.0.0.1` 바인딩. 부트스트랩은 순수 SQL(`\gexec` 멱등) + one-shot `db-init` 서비스; CI는 같은 파일을 checkout 이후 `psql -f`로 재사용. prod은 db·api 미게시, web만 loopback, 시크릿 전부 `${VAR:?}`.

**근거** — override는 더하기만 되고 base의 dev 바인드마운트를 prod에서 뺄 문법이 없어 완전 분리가 구조적으로 불가. `docker-entrypoint-initdb.d`는 볼륨이 빌 때만 실행돼 수정 반영이 안 되고, GitHub Actions `services:`는 checkout보다 먼저 떠 리포 파일 마운트 불가. `.sh`는 autocrlf=true에서 CRLF 사고. Docker는 자체 iptables로 호스트 방화벽을 우회하므로 IP 미지정 게시는 DB를 인터넷에 연다.

**기각한 대안** — base+override(§18.2 위반) / initdb.d(수정 미반영·CI 불가) / prod을 deploy/ 하위 배치(compose project dir 이동으로 상대경로·.env 탐색 이동).

**되돌리기 비용** — 디렉터리·프로젝트명 변경은 전 경로·CI·볼륨 참조를 동시에 흔든다.
