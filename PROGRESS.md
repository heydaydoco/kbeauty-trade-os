# PROGRESS

## 완료
- **S0-1 (리포 골격·인프라·CI) — 종결.** DoD ①③ 충족, ②는 CI 충족·병합차단은 폴백(부채). PR #1이 `main`에 병합됨(`74aa173`).
- **S0-2 PR-1 (신원·감사·인가) — 커밋 0~5 완료, 병합 대기.** 브랜치 `s0-2a-identity-audit`.

## 현재
- **S0-2 진행 중.** PR-1 병합 확인 후 PR-2(`s0-2b-numbering-outbox-slice`, 커밋 6~9) 착수.
  - PR-2 범위: 마이그레이션 #2(doc_number_seq·idempotency_keys·events·tasks·alert_rules·alerts·scheduled_jobs·feature_flags·external_refs·custom_field_defs/values) → 채번·멱등·아웃박스·작업 큐 골격 → skus 수직 슬라이스(등록→목록 50→CSV BOM) → 프런트.

## S0-2 DoD 진행 (4개 중 2개 충족)
- ✅ **audit_log 불변(UPDATE 거부)** — 앱 계정 UPDATE/DELETE/TRUNCATE 전부 42501 실측(커밋 3).
- ✅ **타 사용자 리소스 403** — users 자원으로 증명(커밋 5). *전표가 생기는 Phase 3, tasks가 생기는 PR-2에서 같은 기제로 재확인.*
- ⬜ 채번 동시 100건 중복 0 — PR-2 커밋 7.
- ⬜ idempotency key 더블클릭 → 1건(GC-A3) — PR-2 커밋 7.

## CI 구성 변경 (S0-2 첫 작업)
- `scope-guard` 잡 제거 → **6잡 → 5잡**(hygiene·backend·frontend·compose·ci-ok). `docs/ci.md`·무료 분 예산(5분) 표기 일치 완료.
- PR 템플릿의 "S0-1 범위 가드" 절 삭제 → **증거 절**로 교체(pytest 로그·vitest·CI run·merge-pr.sh).

## 부채 (렌즈 미통과·보류 — 조용한 누락 금지)
1. **병합 차단(DoD② 일부) — 미충족 확정**. GitHub Pro 미결제(ADR-0011). 폴백: pre-push 훅 + `scripts/merge-pr.sh` + 웹 Merge 버튼 금지. **재검토 트리거: 협업자 추가 또는 팀 배포**.
2. **프런트 eslint** — TypeScript 7을 typescript-eslint가 미지원(peer <6.1.0). Phase 1(실제 UI)에서 react-hooks 규칙과 함께 도입. tsc strict가 현 게이트.
3. **pgAdmin** — dev compose `profiles: ["tools"]` 전용으로 PR-2에서 추가(prod 파일 반입 금지).
4. ~~**담당자 라우팅**~~ — **종결**. 구조(담당자 컬럼)=S0-2 / 동작(이벤트→수신자→발송)=S2-3으로 분리 배정. WBS v1.2 + ADR-0012 + DESIGN §19 각주.
5. **compose-smoke CI 잡** — `docker compose up --wait` → /healthz 200 회귀 검사. Free 분 예산 이유로 `config -q`만. Phase 1 이후 재검토.
6. **감사 채널(원가 로그 분리)** — ADR-0008. S6(회계) 착수 시 설계.
7. **J 그룹 아웃박스 케이스** — events 테이블이 PR-2에서 생긴다. PR-2 종료 시 종결 예정.
8. **커버리지 임계 게이트** — 현재 측정만(`--cov`). Phase 1 종료 후 실측 기반 `--cov-fail-under` ADR.
9. **프런트 브랜디드 금액 타입** — S0-2의 skus에 금액 필드가 없어 표시 대상이 없다. **S1-1(단가 이력)로 이월**.
10. **[신규] 담당 일괄 이관 도구 미배정** — §2 권한·통제가 "퇴사·휴가·담당 변경 시 담당 건(전표·인증·태스크·알림) 일괄 재배정 도구 + 계정 비활성 절차"를 요구한다. **계정 비활성은 S0-2에서 구현**(`users.is_active`, 비활성 계정 로그인 403). **일괄 재배정 도구는 어느 세션에도 배정돼 있지 않다** — 대상(전표·인증)이 생기는 Phase 3 이후가 자연스럽다. ADR-0012와 같은 방식으로 배정 필요. §20 H의 "담당 일괄 이관 후 라우팅 즉시 반영"이 이것에 걸려 있다.

## 주의 인계 (이번 세션에서 발견한 함정·결정)
- **S0-1 잠복 결함 2건을 S0-2에서 발견·수정**:
  1. `migrations/script.py.mako`의 mako 필터 구문(`${(down_revision | comma,n) or ...}`)이 깨져 있어 **autogenerate가 파일을 한 번도 렌더링한 적이 없었다**. baseline은 손으로 쓴 파일이라 드러나지 않았다.
  2. 테스트 격리 픽스처가 매 테스트 후 전 테이블을 TRUNCATE → (a) 마이그레이션 시드(`roles`)까지 지웠고, **한 번 지워진 DB는 `upgrade head`가 no-op이라 영구히 시드 없는 상태로 고정**된다(인가 코드가 잘못된 것처럼 보이는 실패), (b) 테스트당 ~2.8초를 태웠다(전체 235s).
     → 수정: `PRESERVED_TABLES`에 시드 테이블 추가 + `_prepare_schema`가 매 세션 `downgrade base → upgrade head`로 바닥부터 재구축 + **더럽혀진 테이블만** TRUNCATE. 전체 235s → 117s.
     → **규칙: 마이그레이션이 시드를 넣는 테이블은 `tests/conftest.py`의 `PRESERVED_TABLES`에 반드시 추가한다**(S2-1 요건 템플릿, S3-4 FTA 협정 시드 등이 해당).
  3. **(위 수정이 만든 후속 결함 — CI가 잡았다)** 정리 대상 테이블 목록을 세션 스코프로 캐시하면서 `_prepare_schema`에 의존을 걸지 않아, 목록이 **스키마 생성보다 먼저** 평가되면 빈 채로 고정되고 이후 모든 정리가 조용히 no-op이 됐다. 테이블이 남아 있는 개발 PC에서는 통과하고 **DB가 새것인 CI에서만** 23건이 무너진다.
     → **규칙: 픽스처 순서를 autouse에 기대지 말고 인자로 명시한다.** 더불어 "빈 목록"은 정상이 아니라 오류로 취급한다(`pytest.UsageError`). 검증은 로컬에서 `kbos_test` 스키마를 통째로 드롭해 CI 조건을 재현한 뒤 181 passed로 확인했다.
- **프런트 HTTP 클라이언트는 `httpx`가 아니라 `httpx2`** — starlette 1.3부터 TestClient가 httpx2를 쓴다. 테스트에서 `from httpx import ...`는 ModuleNotFoundError다.
- **FastAPI 신버전은 `include_router`를 `_IncludedRouter`로 감싼다** — `app.routes`에 라우트가 평탄화되지 않아 구조 순회로 라우트 목록을 만들 수 없다. 인증 커버리지 검사는 **OpenAPI 스키마 + 실제 요청**으로 구현했다(버전 무관).
- **역할 5종 시드는 마이그레이션에 하드코딩**했다. `ROLE_SEED` 상수를 임포트하면 상수를 고치는 순간 과거 마이그레이션의 결과가 바뀐다 — 마이그레이션은 실행된 역사다.
- **실패한 로그인은 커밋되어야 한다** — 예외를 먼저 던지면 `unit_of_work`가 실패 카운터를 롤백해 5회 잠금이 조용히 작동하지 않는다. 서비스는 트랜잭션 안에서 판정만 하고 커밋 후에 예외를 던진다. 같은 함정이 앞으로 "실패도 기록해야 하는" 모든 경로(승인 반려·검수 차이·게이트 거부)에 그대로 있다.
- **S4-1 인계**: `stock_movements`·확정 분개를 만들 때 `table_policy.IMMUTABLE_TABLES` 등록 + 마이그레이션에서 `revoke_mutations()` 호출. 안 하면 `test_every_table_is_classified`가 잡는다.
- 기존 인계(유효): DB 로케일 `LC_COLLATE 'C' + LC_CTYPE 'C.UTF-8'` 고정(ADR-0003) / 커밋 신원 `heydaydoco`+GitHub noreply / `.gitattributes` LF 강제·`.ps1` UTF-8 BOM / 의존성 추가 후 `docker volume rm kbos-dev_web_node_modules` / DB 역할 2종(ADR-0002).

## 브라우저로 확인하는 정확한 URL
- 헬스: **http://localhost:8000/api/v1/system/healthz** → `{"status":"ok",...}`
- 레디니스: **http://localhost:8000/api/v1/system/readyz**
- 프런트: **http://localhost:5173** → "API 연결: 정상"
- ⚠️ 프리픽스 없는 `/healthz`는 **404가 정상**. 로그인 없이 `/api/v1/auth/me`는 **401이 정상**(한국어 봉투 + request_id).

## 다음 세션 첫 명령
```
cd C:\Users\PC\orca\kbeauty-trade-os
git checkout main
git pull origin main
git switch -c s0-2b-numbering-outbox-slice
docker compose up -d --build
docker compose run --rm api pytest -q
```

## 영준이가 지금 할 것
1. **[검토·승인] S0-2 PR-1** — CI green 확인 후 `bash scripts/merge-pr.sh <PR번호>`.
2. **[선택] GitHub Pro 결제 확인** — `gh api repos/heydaydoco/kbeauty-trade-os/rulesets` → `403`이 아니라 `[]`면 룰셋 적용 가능(부채 1 해소).
