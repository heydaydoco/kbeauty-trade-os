# kbeauty-trade-os

화장품 수출입 무역의 인증·원산지·로트·채널·기일·비용을 하나의 전표 사슬로 잇는 미니 ERP.

## 문서 지도

| 문서 | 역할 |
|---|---|
| **`DESIGN.md`** | **유일한 사양서.** 충돌 시 항상 우선한다. |
| `WBS.md` | 세션 단위 실행 레일 (태스크 ID 순서대로 진행) |
| `kbeauty-golden-cases-v1.md` | 거동 고정 케이스 — 구현물은 이걸로 검수한다 |
| `PROGRESS.md` | 현재 진행 상황·부채·다음 세션 인계 |
| `CLAUDE.md` | 작업 수칙 |
| `docs/adr/` | 되돌리기 어려운 결정의 기록 (5줄) |

## 개발 환경

> S0-1 진행 중 — 기동 절차는 `docs/runbook/local-dev.md`에서 확정된다.

## 버전 고정 규칙

아래 3종의 버전은 **네 곳에 동시에** 적혀 있다. 하나만 바꾸면 "로컬은 되는데 CI 실패"가 난다.

| 대상 | 적힌 곳 |
|---|---|
| Python | `backend/.python-version`, `backend/pyproject.toml`, `backend/Dockerfile`, `.github/workflows/ci.yml` |
| Node | `frontend/.nvmrc`, `frontend/package.json`, `frontend/Dockerfile`, `.github/workflows/ci.yml` |
| PostgreSQL | `docker-compose.yml`, `docker-compose.prod.yml`, `.github/workflows/ci.yml` |

버전을 올릴 때는 해당 행의 파일을 전부 같이 고친다. CI의 `hygiene` 잡이 PostgreSQL 태그 불일치를 검사한다.
