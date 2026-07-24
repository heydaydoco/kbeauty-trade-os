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

기동 절차(비개발자용 클릭 단위)는 `docs/runbook/local-dev.md` 참고.

### 브라우저로 확인하는 URL (정확히 이 주소여야 함)

| 용도 | URL | 정상 응답 |
|---|---|---|
| 헬스(살아있음) | http://localhost:8000/api/v1/system/healthz | `{"status":"ok",...}` |
| 레디니스(DB·마이그레이션) | http://localhost:8000/api/v1/system/readyz | `{"status":"ok",...}` |
| 프런트 화면 | http://localhost:5173 | "API 연결: 정상" |

> ⚠️ `/healthz`(프리픽스 없이)는 **404**가 정상이다 — 경로가 `/api/v1/system/` 하위에 있다.
> 404가 한국어 오류 메시지(`COMMON.RESOURCE.NOT_FOUND`)로 뜨는 건 에러 처리가 제대로 동작하는 것이지 버그가 아니다.

## 병합 규칙

**웹의 Merge 버튼을 쓰지 않는다.** 병합은 항상 `bash scripts/merge-pr.sh <PR번호>`로만 —
이 스크립트가 CI green을 확인한 뒤에만 병합한다(자세히는 `docs/ci.md`).

## 버전 고정 규칙

아래 3종의 버전은 **네 곳에 동시에** 적혀 있다. 하나만 바꾸면 "로컬은 되는데 CI 실패"가 난다.

| 대상 | 적힌 곳 |
|---|---|
| Python | `backend/.python-version`, `backend/pyproject.toml`, `backend/Dockerfile`, `.github/workflows/ci.yml` |
| Node | `frontend/.nvmrc`, `frontend/package.json`, `frontend/Dockerfile`, `.github/workflows/ci.yml` |
| PostgreSQL | `docker-compose.yml`, `docker-compose.prod.yml`, `.github/workflows/ci.yml` |

버전을 올릴 때는 해당 행의 파일을 전부 같이 고친다. CI의 `hygiene` 잡이 PostgreSQL 태그 불일치를 검사한다.
