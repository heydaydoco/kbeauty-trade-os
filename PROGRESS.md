# PROGRESS

## 완료
- **S0-1 (리포 골격·인프라·CI) — 종결.** PR #1(`74aa173`).
- **S0-2 (공통 테이블·인증·채번·수직 슬라이스) — 병합 완료·종결.** PR #3(`c236163`) + PR #4(`2070504`). DoD 5건 전건 충족(아래 표).

## 현재
- **S1-1 (제품 계층·세트·HS·프로파일) 진행 중.** 브랜치 `s1-1-product-hierarchy`. 범위 §4.1·4.2·4.8.
- 계획 승인 완료(웹 리뷰 세션) — 비가역 결정 5건 사전 승인(ADR-0016~0021), PR 2분할(PR-1 커밋1~5 / PR-2 커밋6~9).
- **다음**: S1-2(성분·자재 BOM·라벨).

## S0-2 DoD — 전건 충족 (종결 기록)
| DoD | 상태 | 증거 |
|:--|:--|:--|
| audit_log 불변(UPDATE 거부) | ✅ | 앱 계정 UPDATE·DELETE·TRUNCATE 전부 42501 실측 |
| 타 사용자 리소스 403 | ✅ | users(PR-1) + tasks(PR-2) 양쪽에서 검증 |
| 채번 동시 100건 중복 0 | ✅ | 실제 100 스레드 동시 발급 → 중복 0·결번 0(1..100) |
| idempotency 더블클릭 → 1건 (GC-A3) | ✅ | 서비스·HTTP 양쪽 + 동시 2요청까지 |
| 담당 이관 후 원담당자 잔여 0건 (WBS v1.3) | ✅ | 이관 3건 → 원담당자 0 / 인수자 3 |

**실기동 관통(§22 렌즈 11)**: 관리자 부트스트랩 → 로그인 → SKU 등록 → 더블클릭(중복 0) → 목록 → CSV(BOM) → 로그아웃 401까지 실제 HTTP로 확인.

## S1-1 승인된 비가역 결정 (구현 계약)
| # | 결정 | ADR |
|:--|:--|:--|
| A1 | 세트 SKU는 처방에 속하지 않는다 — `skus.kind`(SINGLE/SET) + 양방향 CHECK, 중첩 세트 금지 | ADR-0016 |
| A2 | 기존 skus 백필 = 마이그레이션이 제품 자동 생성 후 연결(**생성 출처 비고 필수**) | (마이그레이션 주석) |
| A3 | 단가 이력은 종료일 없는 발효일 이력, "단가 없음"은 오류로 실패(0·null 금지) | ADR-0017 |
| A4 | 매입가 = 원가 — 조회 역할에는 **행 단위** 비노출(목록·건수 제외, 단건 404) | ADR-0018 |
| A5 | sku_hs_codes 근거링크·확인일 NOT NULL + 유일키 `(sku_id, country_code, hs_version)` | ADR-0019 |
| A6 | MSDS는 URL 문자열 → S1-3에서 documents로 승격 | ADR-0020 |
| A7 | item_profiles는 헤더+적용 FK까지 | ADR-0021 |
| A8 | 세트를 구성품으로 등록 → 거부 | ADR-0016 부기 |

## 첫 실행 방법 (영준이용)
```
docker compose up -d --build
docker compose run --rm api python -m app.cli create-admin --email <내이메일> --display-name "영준이"
# 비밀번호는 화면에서 입력받는다(셸 기록에 안 남는다). 12자 이상.
```
→ http://localhost:5173 에서 로그인. pgAdmin이 필요하면 `docker compose --profile tools up -d pgadmin` → http://localhost:5050

## 부채 (렌즈 미통과·보류 — 조용한 누락 금지)
1. **병합 차단(DoD② 일부) — 미충족 확정**. GitHub Pro 미결제(ADR-0011). 폴백: pre-push 훅 + `merge-pr.sh` + 웹 Merge 버튼 금지. **재검토 트리거: 협업자 추가 또는 팀 배포**.
2. **프런트 eslint** — TypeScript 7을 typescript-eslint가 미지원(peer <6.1.0). **S1-1 커밋 8에서 재확인**한다(도입 가능하면 react-hooks 규칙과 함께 투입, 여전히 미지원이면 사유와 함께 Phase 1 잔여 세션으로 이월). tsc strict가 현 게이트.
3. ~~pgAdmin~~ — **종결**(dev compose `profiles: ["tools"]`, prod 미반입 확인).
4. ~~담당자 라우팅 미배정~~ — **종결**(ADR-0012 / WBS v1.2).
5. **compose-smoke CI 잡** — `docker compose up --wait` → /healthz 200 회귀 검사. Free 분 예산 이유로 `config -q`만. Phase 1 이후 재검토.
6. **감사 채널(원가 로그 분리)** — ADR-0008. S6(회계) 착수 시. **S1-1이 원가(매입가)를 처음 들이므로 로그 마스킹은 여기서 선반영**(ADR-0018).
7. ~~J 그룹 아웃박스 케이스~~ — **종결**(events 생성 + 롤백 시 0행 검증).
8. **커버리지 임계 게이트** — 측정만(`--cov`). Phase 1 종료 후 실측 기반 `--cov-fail-under` ADR.
9. **프런트 브랜디드 금액 타입** — **S1-1 커밋 8에서 처리 중**(단가 이력 화면이 금액을 처음 노출).
10. ~~담당 일괄 이관 도구 미배정~~ — **종결**(ADR-0015 / WBS v1.3).
11. **아웃박스 디스패처 미구현** — events에 쌓이기만 하고 보내는 주체가 없다. **의도된 상태**(S2-3 알림 엔진). 그전까지 events는 단조 증가한다 — S2-3에서 디스패처와 함께 보존 정책도 정할 것.
12. **scheduled_jobs 비어 있음** — 배치 레지스트리 테이블만 있고 등록된 배치가 0건이다. **실행기(S2-3) 구현 전 행 삽입 금지 — 위반 커밋은 반려 대상**(웹 리뷰 세션 판정). 1순위 등록 예정: 멱등 키 TTL 청소(그전까지는 키 발급 경로가 겸한다, ADR-0014).
13. **프런트 라우팅 없음** — **S1-1 커밋 8에서 처리 중**(화면이 2→6개가 되어 도입 조건 충족).
14. **[신규] MSDS 문자열 → documents 승격** — S1-1은 `skus.msds_url` 문자열. §4.7 `documents`가 서는 **S1-3**에서 승격하고 문자열 컬럼을 제거한다. 안 하면 §7.7 DG 게이트(P4)의 "MSDS 유효본 미첨부 차단"이 검증 불가로 남는다. (ADR-0020)
15. **[신규] item_profiles 내용 연결 미배정분** — S1-1은 헤더+적용 FK까지. 연결 테이블은 **서류=S1-3 / 요건=S2-1 / 마일스톤=S3-2**가 각각 추가하며, 각 세션 DoD에서 확인한다. §4.8의 "신규 등록 시 자동 적용" 동작도 그 이후다. (ADR-0021)

## 주의 인계 (발견한 함정·결정)
- **S0-1 잠복 결함 2건 수정**: ① `script.py.mako`의 mako 필터 구문이 깨져 **autogenerate가 한 번도 렌더링된 적이 없었다**(baseline은 손으로 쓴 파일). ② 테스트 격리 픽스처의 전 테이블 TRUNCATE가 마이그레이션 시드(`roles`)까지 지웠고, 한 번 지워진 DB는 `upgrade head`가 no-op이라 **영구히 시드 없는 상태로 고정**된다.
  → **규칙: 마이그레이션이 시드를 넣는 테이블은 `tests/conftest.py`의 `PRESERVED_TABLES`에 반드시 추가한다**(S2-1 요건 템플릿, S3-4 FTA 시드 등).
- **CI가 잡은 회귀**: 위 수정이 만든 후속 결함 — 정리 대상 목록을 세션 캐시하면서 `_prepare_schema` 의존을 안 걸어, 목록이 스키마 생성보다 먼저 평가되면 빈 채로 고정돼 정리가 조용히 no-op이 됐다. **테이블이 남아 있는 개발 PC는 통과, DB가 새것인 CI만 23건 실패.**
  → **규칙: 픽스처 순서를 autouse에 기대지 말고 인자로 명시한다. "빈 목록"은 정상이 아니라 오류로 취급한다.**
- **"실패도 커밋해야 하는" 패턴** — 예외를 먼저 던지면 `unit_of_work`가 실패 기록까지 롤백한다. 로그인 5회 잠금·마지막 관리자 보호가 이 패턴이다. 앞으로 승인 반려·검수 차이·게이트 거부에 **그대로 반복된다**: 트랜잭션 안에서 판정만 하고, 커밋 뒤에 예외를 던진다.
- **Windows Git Bash의 argv 인코딩** — `curl -d '{"name":"한글"}'` 처럼 **인자로 넘긴 한글은 CP949로 깨져** 서버가 본문을 못 읽는다(400). 파이프·파일(`--data-binary @file`)은 UTF-8 그대로다. 이걸 앱 결함으로 오진해 시간을 썼다 — **로컬에서 한글 본문을 보낼 때는 반드시 파일로.**
- **병합 스크립트 호출 형식(이 PC)** — PowerShell에서 `bash` 단독 표기는 WSL을 물어 오류가 난다. 반드시 `& "C:\Program Files\Git\bin\bash.exe" scripts/merge-pr.sh <번호>`.
- **400 응답의 코드가 INTERNAL_UNEXPECTED로 나가던 결함 수정** — `_STATUS_TO_CODE`에 400이 없어 상태(400)와 코드(서버 내부 오류)가 모순됐고, 클라이언트 잘못을 서버 장애처럼 보이게 만들었다. `COMMON.REQUEST.MALFORMED` 추가 + 회귀 테스트.
- **테스트가 못 잡는 영역이 있다** — TestClient(httpx)는 헤더 이름을 정규화하고 본문을 항상 UTF-8로 보낸다. 실제 브라우저·curl과 다른 지점이 있으므로 **관통은 실기동으로 한 번 더** 본다(§22 렌즈 11이 요구하는 바 그대로).
- **첫 관리자는 `python -m app.cli create-admin`으로만 만든다.** 마이그레이션 시드를 쓰지 않는 이유: 기본 비밀번호가 리포에 박히고 그 계정은 아무도 안 지운다.
- **S4-1 인계**: `stock_movements`·확정 분개 생성 시 `table_policy.IMMUTABLE_TABLES` 등록 + 마이그레이션에서 `revoke_mutations()` 호출.
- **Phase 3 인계**: 전표에 담당자 컬럼을 추가하면 `handover/targets.py`에 한 줄 추가(아키텍처 테스트가 강제).
- 기존 인계(유효): DB 로케일 `LC_COLLATE 'C' + LC_CTYPE 'C.UTF-8'`(ADR-0003) / `.gitattributes` LF 강제 / 의존성 추가 후 `docker volume rm kbos-dev_web_node_modules` / DB 역할 2종(ADR-0002) / 프런트 HTTP 클라이언트는 `httpx`가 아니라 **`httpx2`** / FastAPI는 `include_router`를 `_IncludedRouter`로 감싸 `app.routes`에 평탄화하지 않는다(인증 커버리지 검사는 OpenAPI+실요청으로 구현).

## 다음 세션 첫 명령
```
cd C:\Users\PC\orca\kbeauty-trade-os
git checkout main
git pull origin main
git switch -c s1-1-product-hierarchy   # 이미 있으면 git switch s1-1-product-hierarchy
docker compose up -d --build
docker compose run --rm api pytest -q
```

## 영준이가 지금 할 것
- **대기** — S1-1 PR-1(커밋 1~5) 완료 보고와 증거 3종(테스트 결과 줄·CI run·커밋 해시)이 올라오면 검토 후 병합.
