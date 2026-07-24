# PROGRESS

## 완료
- **S0-1 (리포 골격·인프라·CI)** — DoD ①③ 충족, ② 부분(아래 부채 참고). 브랜치 `s0-1-skeleton`.

## 현재
- S0-1 마무리 단계. 백엔드·프런트·CI·문서 완료. **미결 1건**: 병합 차단 룰셋(GitHub Pro 반영 대기).

## 다음
- **S0-2 (공통 테이블·인증·채번·수직 슬라이스)**. 첫 작업 2가지 잊지 말 것:
  1. `.github/workflows/ci.yml`의 **scope-guard 잡 제거**(마이그레이션 리비전 1개 제한 — S0-2는 여러 개 추가).
  2. `.github/pull_request_template.md`의 **"S0-1 범위 가드" 절 삭제**.

## DoD 판정 (S0-1)
- ① `docker compose up` 한 번으로 dev 기동 — **충족**(실행 확인: db·db-init·migrate·api·web 정상).
- ② CI가 push에 실제로 돌고 실패 시 빨간불 — **CI는 충족**(6잡 green: run 30070254770·30070822909). **병합 차단은 미충족(부채)** — GitHub Pro 미결제 결정(ADR-0011), 폴백(pre-push 훅 + merge-pr.sh + 웹버튼 금지 규율)로 대체.
- ③ 헬스체크 200 — **충족**.
- 검증: 백엔드 **122 passed, 1 skipped** / 프런트 vitest **3 passed** / ruff·mypy 통과.

## 브라우저로 확인하는 정확한 URL (헷갈리기 쉬움)
- 헬스: **http://localhost:8000/api/v1/system/healthz** → `{"status":"ok",...}`
- 레디니스: **http://localhost:8000/api/v1/system/readyz** → `{"status":"ok",...}`
- 프런트: **http://localhost:5173** → "API 연결: 정상"
- ⚠️ 프리픽스 없는 `/healthz`는 **404가 정상**(경로가 `/api/v1/system/` 하위). 404가 한국어
  오류(`COMMON.RESOURCE.NOT_FOUND`)로 뜨는 건 에러 처리가 동작하는 증거이지 버그 아님.

## 부채 (렌즈 미통과·보류 — 조용한 누락 금지)
1. **병합 차단(DoD② 일부) — 미충족 확정**. GitHub Pro 미결제 결정(ADR-0011). 폴백으로 대체: pre-push 훅(위생 검사) + `scripts/merge-pr.sh`(CI green 후에만 병합) + 웹 Merge 버튼 금지(CLAUDE.md). 훅·스크립트는 규율이지 물리 차단이 아님. **재검토 트리거: 협업자 추가 또는 팀 배포 시 Pro/Team 재검토 → 룰셋(infra/github/ruleset-main.json) 적용**.
2. **프런트 eslint** — TypeScript 7(네이티브 컴파일러)을 typescript-eslint가 아직 미지원(peer <6.1.0)이라 제거. Phase 1(실제 UI)에서 react-hooks 규칙과 함께 도입. tsc strict가 현 게이트.
3. **pgAdmin(스텝 15)** — DoD 무관이라 이번 세션에서 절단. 영준이 DB 자립 조회 수단으로 S0-2나 여유 시 추가(`profiles: ["tools"]`).
4. **담당자 라우팅** — §19 Phase 0·ADR-07이 "담당자 라우팅 기본"을 요구하나 WBS S0-2 산출물에 동작 명시 없음. WBS v1.1 갱신 후보(어느 세션에 배정할지).
5. **compose-smoke CI 잡** — `docker compose up --wait` → /healthz 200 회귀 검사. Free 분 예산·빌드 시간 이유로 S0-1은 `config -q`만. compose 회귀를 기계가 못 잡는 구간이 남음 → Phase 1 이후 재검토.
6. **감사 채널(원가 로그 분리)** — ADR-0008. 원가·마진은 로그 마스킹하되 §12.4 검산 진단용 별도 감사 채널은 S6(회계) 착수 시 설계.
7. **J 그룹 아웃박스 케이스** — §20 J의 "아웃박스 커밋 실패 시 발송 0"은 events 테이블(S0-2)이 있어야 검증 가능. S0-2 종료 시 부채 여부 재확인.
8. **커버리지 임계 게이트** — 현재 측정만(`--cov`). Phase 1 종료 후 실측 기반 `--cov-fail-under` ADR.
9. **프런트 브랜디드 금액 타입** — 백엔드 money.py는 완료, 프런트는 첫 금액 표시(S0-2)에서.

## 다음 세션 첫 명령
```
cd C:\Users\PC\orca\kbeauty-trade-os
git checkout s0-1-skeleton   # (S0-1 PR 병합 후면 main)
Copy-Item .env.example .env
docker compose up -d --build
docker compose run --rm api pytest -q
```

## 주의 인계 (이번 세션에서 발견한 함정·결정)
- **환경 검증 완료**: Docker 29.5(WSL2 linux), Python 3.13.14, Node 24.16, PostgreSQL 16.14. gh는 heydaydoco 계정 로그인.
- **DB 로케일은 되돌리기 불가 최상위 결정** — `LC_COLLATE 'C' + LC_CTYPE 'C.UTF-8'`로 고정(실측: `C`면 한국어 pg_trgm 검색이 DB 레벨에서 영구 폐쇄). ADR-0003.
- **DB 역할 2종 분리**를 S0-1로 당김(WBS는 S4-1) — 없으면 S0-2가 `permission denied for table skus`로 중반에 죽는데 테이블 0개인 지금은 증상이 안 보임. ADR-0002. **S0-2가 audit_log를 만들면 `table_policy.IMMUTABLE_TABLES`에 등록 + 마이그레이션에서 `revoke_mutations()` 호출**(안 하면 분류 완전성 테스트가 잡는다).
- **커밋 신원**: 리포 로컬로 `heydaydoco` + GitHub noreply 이메일 설정함(네이버 주소는 공개 이력에 안 남음).
- **Windows 함정 대응 완료**: `core.autocrlf=true` → .gitattributes로 LF 강제. `.ps1`은 UTF-8 BOM. bind mount inotify 미전달 → 백엔드 `WATCHFILES_FORCE_POLLING`, 프런트 `VITE_USE_POLLING`. web `node_modules`는 named volume 격리(호스트 네이티브 모듈 오염 차단). **@types/lint 등 의존성 추가 후엔 `docker volume rm kbos-dev_web_node_modules`로 볼륨 재생성 필요**(안 하면 고인 node_modules를 씀).
- **CI billable time 실측**(run 30070254770): hygiene 5s·frontend 14s·backend 58s·compose 7s·scope-guard 6s·ci-ok 4s → 잡당 1분 올림 = **실행당 약 6 billable분**(Free 월 2,000분). Pro 결제 시 3,000분.
- **DESIGN.md 4곳 갱신 완료**(영준이 승인): ADR-01 스택 / §18.1 로그 마스킹 / §18.3 드라이런·에러 계약 / §14 헬스체크 용어 각주. ADR-0003·0007·0008·0010.
- **WBS v1.1 갱신 완료**: S2-4 백업 배정(ADR-0001) / S0-1 DB 역할(ADR-0002) / S0-1 검증란 보강.

## 영준이가 지금 할 것 (우선순위)
1. **[최우선] GitHub Pro 결제 확인** — 터미널에 `gh api repos/heydaydoco/kbeauty-trade-os/rulesets` → `403`이 아니라 `[]`면 반영 완료. 그러면 나에게 알려줘 → 룰셋 적용 + 병합 차단 실증한다.
2. **[검증] dev 기동 눈으로 확인** — `docker compose up -d --build` 후 http://localhost:5173 에서 "정상", `docker compose stop api` 후 새로고침 시 한국어 "이상", `docker compose start api` 복구. (자세히는 docs/runbook/local-dev.md)
3. **[병합]** S0-1 PR 만들어 병합할지 결정. `gh pr create --fill` (브랜치 s0-1-skeleton).
