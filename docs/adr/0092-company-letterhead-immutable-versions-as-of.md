# ADR-0092: 자사 레터헤드 = 불변 판 + as-of(대체 금지·소급 판은 판 0개일 때만·advisory 직렬화)·Shipper 블록 = 레터헤드(R-3a-5)·조회 ADMIN 전용·로고 없음(P-11)

- **상태**: 자율 확정 — 사후 번복 가능 (S3-3 계획 2026-10-05 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-05
- **관련**: DESIGN.md §3·§7.6·§17.5(S3-3 [M4] 보강) / WBS S3-3 v1.5 '자사 레터헤드 마스터' / ADR-0074 / PROGRESS P-11·R-3a-5 / docs/plans/s3-3-plan.md · docs/plans/s3-3/design-integrated.md(§9 적대 검토 정정 R-01~R-40 우선) — sA §A3, X-07·X-08·X-29, N-07, R-15 / 구현 PR-3a(M18)

**맥락** — 서류(QT·PI·CI·PL·S/I)는 자사 이름·주소·사업자번호가 필요한데 마스터가 없다(P-11). 선적 당사자 SHIPPER(자사) 자리도 비어 있다(R-3a-5). 동결된 QT·PI는 레터헤드 열을 갖지 않으므로, 레터헤드를 단일 행 UPDATE로 두면 과거 서류를 다시 찍을 때 새 주소가 찍힌다.

**결정** — ① `company_profiles` **IMMUTABLE**(INSERT-only) — 수정 = 새 판 INSERT, 삭제 경로 0. ② `letterhead_as_of(session, day)` = `effective_from ≤ day` 중 `(effective_from DESC, id DESC)` 첫 행, 없으면 None → 409 `LETTERHEAD.NOT_EFFECTIVE`(가장 이른 판 대체 금지), 판 0 = 409 `NOT_REGISTERED`. 기준일 = 원천 `frozen_at`의 KST 날짜(QT·PI)·CI 발행일. ③ **과거 `effective_from`은 판이 0개일 때만**(첫 판 과거 유효일 등록이 이월 서류의 유일한 해소), 판이 1개 이상이면 새 판은 `= today_kst()`·`≥ max(기존)`만 — 위반 422 `EXPORT_DOCS.LETTERHEAD.BACKDATED`, 미래 422 `EFFECTIVE_IN_FUTURE`, 사업자번호 체크섬 422(R-15·X-29). ④ 등록 TX = `pg_advisory_xact_lock(LETTERHEAD_LOCK_KEY)`(다른 잠금보다 먼저 — 첫 판 동시 2건 직렬화). ⑤ Shipper 블록 = 레터헤드 as-of 행(선적 SHIPPER 당사자 422 유지). ⑥ 조회·등록 = **ADMIN 전용**(미리보기·CI 상세는 자기 응답에 판 요약을 싣는다), 로고·서명 이미지 없음(부채 A-06).

**근거** — 불변 판 + as-of는 열 복사 없이 '그때의 레터헤드'를 보증한다(§3 스냅샷 규율). 두 번째 판의 과거 날짜를 허용하면 이미 보낸 서류의 재출력이 다른 주소로 바뀐다 — 소급 판을 첫 판 한 번으로 묶는 것이 더 엄격하다.

**기각한 대안** — 단일 행 UPDATE + version(과거 서류 재출력 변형 — X-07), 서류마다 레터헤드 열 복사(QT·PI 동결 열 확장 — 스냅샷 규율 위반 없이 같은 보증을 불변 판이 준다), 소급 판 자유 허용(R-15), 로고 업로드(이미지 서빙 보안 재판정 필요 — A-06).

**되돌리기 비용** — **중간** — 불변 표(단일 행 전환 시 CI FK 재지정). 소급 판 규칙은 낮음(판 수 검사 1곳). 단 첫 판 등록 뒤 과거 주소 정정이 필요해지면 현재 탈출로가 없다(오너 판단 필요 — 계획서 §5-14).
