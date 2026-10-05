# S3-3 계획서 부록 E — PR 분할·마이그레이션·검증 배치

> **통합 우선순위·정정 색인(2026-10-05 — S3-3 PR-1 ② 표지, 통합 §1.8 + §9 R-40)**: 이 부록과 `design-integrated.md`가 충돌하면 통합이 이긴다. 우선순위는 **통합 §9(적대 검토 정정 R-01~R-40) → 통합 §0~§8 → 이 부록**이다. 이 부록 **원문은 고치지 않았다**(계획 세션 쓰기 범위가 통합·계획서 2파일이었고, PR-1도 원문 대신 이 색인을 단다 — S3-2 R-27 방식의 머리 색인판). 아래 표의 위치를 읽을 때는 오른쪽 결정을 따른다. 표지 `[통합 X-nn]`·`[통합 N-nn]` = 통합 §1 해소 행, `[적대 R-nn]` = 통합 §9 정정 행. 표·통합·계획서가 인용하는 이 부록의 줄 번호(`sX:nn`)는 이 색인을 넣기 전(`1787f724fff2`) 기준이다 — 지금 파일에서는 이 머리 블록 줄 수만큼 아래에 있다. 수치는 §9 '갱신 수치'가 정본이다(신규 표 12·IMMUTABLE 코드 13 → 21[DESIGN 계수 11 → 19]·마이그레이션 8[M16·M16b·M17~M22]·에러 코드 45·PR 16·ADR 0088~0099 + 기존 부기 17·GC v1.6 14건).
> 
> | 위치(이 부록) | 표지 | 따를 결정(요지) |
> |---|---|---|
> | 가정 2·3·6, E-Q1·Q3·Q10, R10, 부채 E-05 | [통합 X-02·X-01·X-34] | 렌더 저장 확정·채권 = 전용 엔드포인트(CI 합류 없음)·sD 실재 재대사(E-05 소멸) |
> | E2 M16·M21 내용 | [통합 X-36]·[적대 R-21·R-26] | payments 확장 = **M16b** 분리, M21의 'DocKind CHECK 전수 확장' 삭제(교차 표 CHECK 0건 — 실측 항목), CHECK 수기 재정의 5건 |
> | E3-0 2a·2b·3b·5a 행 | [통합 N-01~N-03·N-11·N-12] | 응답 블록 배치·오더 보드 COMPLETED 제외(2b)·생성물 다운로드 축소(3b)·팩토리 스캔(5a)·CI `ASSIGNMENT_TARGETS`(5a) |
> | E3-0·E3-3 PR-2a 행·M16 | [적대 R-21] | PR-2a(채권 원장, M16) → **PR-2d(입금 확장, M16b — 3렌즈)** → PR-2b, PR 16개 |
> | E3-1 PR-1 "ADR 0088~0098" | [통합 §3]·[적대 R-34] | ADR **0088~0099(12건)** + 기존 부기 **17건**(+0068·0055) |
> | E3-4 "3 입구"·E6-3 2b "1입구 누락" | [적대 R-24] | 2 입구, 변이 = 입구 1개 등록 누락 2종 |
> | E4 ADR 0088·0093·0095 문면, 11건 | [통합 §3] | 12건(0099 화면 계약 추가) — 문면 정본 = 통합 §3 + §9 |
> | E5-1 ⑦ "S4-2 = 채권 전용 엔드포인트 폐쇄" | [통합 X-01 ⑤] | S4-2는 '채권 선행 선적의 CI 처리(`RECEIVABLE_EXISTS` 409)'와 경로 단일화 여부를 **재판정** |
> | E5-2 GC-A28 "3 입구"·GC-H7 | [적대 R-24·R-05·R-07]·[통합 §5.2] | A28 = 2 입구·SO 묶음 환산 혼합 행·기본 provider UNEVALUABLE 행, H7 += 'CI 발행이 채권을 만들지 않음' |
> | `sE:185` 3b 버전 고정 | [적대 R-03·R-36] | `reportlab ≥ 3.6.13` 단언, `tzdata==2026.5` 줄 diff 0(R-4a-9 발동) |
> | E6-3 변이 최소 목록 | [적대 R-39] | + 2b 기본 provider 분기·묶음 환산 / 2d T13 잠금·ack / 3a 링크 13·판 수·CERT 삭제 / 3b 이스케이프·신뢰 스킴·DRAFT 취소·internal_note·해시 지문 / 4a 첫 마크·SHARE·프런트 계산 / 5a L 계좌 후보 / 5b 첫 S/I 유니크 / 6 미충당 선수금 점검 |

- 기준: main `2092406`(S3-2 종결 — PR-8 #67). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-3 행(`W:127-132`)과 v1.6 주석(`W:117-125`·`W:132`·`W:139`·`W:148`·`W:155`)을 따른다. 진행·부채 정본은 PROGRESS.md '## 현재'(`P:1689-1690`)·'S3-2 부채 최종 목록'(`P:50-166`)·S3-1 계획 등재 P-01~P-60(`P:1614-1675`)이다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `GC:줄` = `kbeauty-golden-cases-v1.md`, `code:경로:줄` = `backend/app/` 아래, `test:경로:줄` = `backend/tests/` 아래, `fe:경로:줄` = `frontend/src/` 아래, `mig:파일` = `backend/migrations/versions/`. `sA`~`sD` = 같은 디렉터리의 `design-A.md`(서류 데이터 모델·생성기)·`design-B.md`(채권·입금·여신 provider·대금만기)·`design-C.md`(동시성·권한·감사·잡)·`design-D.md`(화면·API — **이 부록 작성 시점에 디렉터리에 없다**, 실측 `ls docs/plans/s3-3` = A·B·C뿐). S3-2 선례는 `docs/plans/s3-2-plan.md §4`·`docs/plans/s3-2/design-E.md`(이하 `E32`)다. 줄 번호는 `2092406`에서 실측한 값이다.
- 판정 방식: 오너 상시 지시(2026-09-29, CLAUDE.md "결정·개입 없이 끝까지")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. PROGRESS·ADR 등재 시 "자율 확정 — 사후 번복 가능"(ADR-0011 부기)으로 표기한다. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 / 되돌리기 비용** 순서다(E32 형식).
- **실측한 것(이 부록 작성 중 실행)**: `alembic heads` = **`281da4794717` 1개**(`/home/user/venv-kbos/bin/alembic heads`, `alembic history` 상위 4줄 = 281da4794717 ← acd34f28c11e ← 394c76a7d2b7 ← f2cb6020b2bb), ADR 최대 번호 = **0087**(`docs/adr/` 88파일 = 0000 템플릿 + 0001~0087), `tests/.shard_durations.json` = tests 5171·files 209(S3-2 종결 수집 5774와 603건 차 — Q-15), `requirements.txt`에 `reportlab`·`openpyxl` **없음**(아래 R3).
- **실행 검증 못 했음.** 그 밖은 정적 독해다. 마이그레이션 리비전 해시·테스트 수·CI 소요·총수 핀은 각 PR 첫 커밋과 첫 CI에서 실측해 PROGRESS에 기록한다(P-60 선례).

---

## 0. 경계 — 이 부록이 정하는 것과 넘기는 것

| 이 부록(E)이 정한다 | 다른 부록으로 넘긴다(경계만 적음) |
|---|---|
| PR 분할·순서·의존, PR별 DoD·§20 그룹·GC 배치 | 서류 표·CHECK·검증 카탈로그·렌더 방식·레터헤드 → **sA** |
| 마이그레이션 번호(**M16~M22**)·DAG·`down_revision` 사슬·PR 귀속 | 채권·payments 확장·노출 산식·COMPLETED·aging·L/C 입력·스캔 대상과 문턱 → **sB** |
| ADR 번호 배정(**0088~0098**)과 기존 ADR 부기 목록 | 트랜잭션 T1~T22·LOCK_ORDER 슬롯·권한 매트릭스·멱등·IMMUTABLE·잡 경계 → **sC** |
| WBS v1.7·GC v1.6 갱신 필요 여부와 문면 | 엔드포인트 응답 스키마·화면·셸·라우트 → **sD**(미작성 — b PR 범위는 sD 확정 시 채운다) |
| CI 3샤드·변이 점검·적대 검토·워크스루의 PR별 적용 | 테스트 케이스 **내용**(sA §A21·sB §B21 GB-01~28·sC §C15 CJ/CH/CI/CK) — 이 부록은 **어느 PR에 넣는지**만 정한다 |
| 되돌리기 비용(PR 단위·전체)·예상 위험 | |

**부록 간 충돌 중 PR 배치에 영향을 주는 것 — 이 부록이 배치 목적으로 고정하는 가정 6건**(통합이 다르게 정하면 해당 PR의 **범위만** 바뀌고 순서·DAG는 유지되도록 짰다):
1. **CI 영속 모델 = sA안**(DocKind `COMMERCIAL_INVOICE` + `commercial_invoices`·lines·status_log·PL 2표, sA §A4~A7). sC가 쓴 가칭 `trade_documents`(sC §C1 T5~T7·§C2 ②)는 같은 대상의 이름 차이로 읽는다. **LOCK_ORDER 슬롯 이름은 통합 확정**(배치 무관).
2. **렌더 산출물 = sA §A13(documents FILE + 불변 `trade_document_renditions` 저장)**. sC §C1은 "렌더 결과 비저장(스냅샷 + 결정적 재렌더)"이다 — **사양 충돌**(판정 후보 E-Q1). 배치 가정은 A안(더 엄격: '보낸 그 파일' 보존·백업 세트 자동 편입 `D:461`·sha256 검증). C안이 채택되면 M19에서 renditions 표·document_types 시드가 빠지고 PR-3b 범위가 준다(순서 불변).
3. **채권 발생 경로**: S3-3 운영 경로 = **전용 엔드포인트 `POST /shipments/{id}/receivable`(sC T8)** — sA §A2 검수 게이트로 CI는 S4-2까지 운영 발행 0이기 때문이다(sA §A20 마지막 행 경고). CI 발행 TX의 채권 합류(sC T5)는 PR-5a가 배선하되 "살아 있는 채권이 이미 있으면 새로 만들지 않고 금액 대사만"(부분 유니크가 DB에서 2건을 막는다). T8 폐쇄는 S4-2 INSPECTED 개방 PR 몫(WBS v1.7 주석 — E5-1 ⑥). 판정 후보 E-Q3.
4. **INVOICE_DATE 앵커 원천 = 채권 `invoice_on` 단일**(sB §B10 ②). sA §A20의 `live_ci_for_shipment().doc_date`는 CI 발행 TX가 채권 `invoice_on`에 복사하는 값으로 수렴한다(원천 이원화 0). 배선 PR = **PR-2a**(aging DoD와 같은 PR — sB §B19의 PR-B2 배치를 당김, E-Q4).
5. **`lc_terms`·제시 기록·하자 체크 마크 소유 = sB**(sC §0-2 결론 — sB §B10 ⑥ 대체 조항 발동), 플래그 토글 경로(P-10)도 같은 PR(PR-4a).
6. **화면 부록 = sD(가정)**. 이 부록의 b PR 행은 sA·sB·sC가 "화면 부록 소관"으로 넘긴 항목의 **합집합**으로 범위를 잡았고, sD 확정 시 통합이 재대사한다.

---

## E1. 분할 원칙 — S3-2 선례 승계(문서 PR-1 → 백엔드 a / 프런트 b·c 쌍 → 마감, 직렬 병합)

**결정**
- PR은 **수직 슬라이스**로 자른다. 각 병합 시점에 앱이 동작하고 전체 테스트가 통과해야 한다(E32 §E1, `docs/plans/s3-2-plan.md:105` 공통 절차).
- 백엔드(a)와 화면(b/c)을 나눈다(S3-2 15 PR 중 화면 PR 5개가 전부 분리 — `P:1690` 이전 절들). 화면 PR은 짝 백엔드 병합 후 최신 main에서 시작한다.
- 병합은 **직렬**이다. alembic head가 하나여야 하므로(`test:integration/test_migrations.py:46` `test_single_head`) 마이그레이션 PR은 E2 사슬 순서대로만 병합하고, 병렬 개발분은 병합 직전 `down_revision`을 최신 head로 재정렬한다.
- **같은 PR(같은 커밋 묶음)에 묶어야 하는 변경** — 가르면 CI가 깨지거나 불변식이 비는 것:
  1. **SO COMPLETED 엣지 ↔ provider `reflected=True` 등록 ↔ 채권 전환분 노출 차감**: WBS v1.6 주석(`W:132`) "같은 PR", 결속 시험 `test:architecture/test_doc_machines.py:141-153`(`is_default_provider()`인 동안 COMPLETED ∈ RESERVED·진입 엣지 0)이 기계로 강제한다 → **PR-2b 한 PR**.
  2. CI DocKind 편입 ↔ 커널 dict·FIELD_POLICY·상태 기계·총수 핀(`code:modules/trade_docs/machine.py:7-8` 독스트링, `test_doc_machines.py` EXPECTED) ↔ 모델 CHECK(DocKind·접두어 파생) → **PR-5a 첫 커밋 묶음**(S3-2 적대 R-23 선례: 엣지 0 편입은 도달성 시험을 깬다).
  3. 체인 테이블을 가리키는 FK ↔ `CHILD_LINKS`(`code:modules/trade_docs/chain.py:40`)·`LINE_CONSUMERS` 등재 — receivables(2a), CI(5a), S/I(5b).
  4. 신규 업무 표 ↔ `_NEVER_SEEDED`(`test:architecture/test_scheduler_registry.py:56-87`)·`table_policy` 분류·users FK 분류 — 표를 만드는 PR이 같이 등재.
  5. 신규 LOCK_ORDER 슬롯 ↔ 그 슬롯의 표 — **표가 생기는 PR에서만 슬롯을 더한다**(빈 슬롯 0 — 계측 시험이 공회전하지 않게): `receivables` = 2a, `lc_terms` = 4a, CI 슬롯 = 5a(`code:modules/trade_docs/locking.py:41-54`).
  6. 새 알림 `entity_type` ↔ `fe:lib/alert-routes.ts:6-17` 이동 표 1줄(K 출구 계약 — S3-2 PR-8 선례 `P:20`) → **PR-6이 프런트 1줄을 함께 싣는다**.
- **PR별 공통 절차**(S3-2 승계): 작은 커밋 → 마이그레이션 왕복·`alembic check`·전체 pytest·vitest·ruff·mypy·typecheck·build → 변이 점검(E6-3) → 실기동 관통(렌즈 11) → 자기 적대 검증(E7) → PR·CI **전 체크런(`ci-ok` 포함) success·mergeable clean** → API squash 병합(CLAUDE.md, ADR-0011 부기 2026-09-29) → 지정 브랜치를 최신 main에서 재시작(병합된 PR에 커밋을 쌓지 않는다).
- **완료 보고 요건**: 릴레이 평문 본문에 DDL 전문(마이그레이션 PR)·12자리 커밋 해시를 직접 포함한다(CLAUDE.md 2026-08-12 판정).

**근거**: CLAUDE.md "작은 단위 구현 → 즉시 실행·테스트"·병합 게이트, `D:374`(드라이런), E32 §E1.

**대안**: (a) 레이어 단위(모델 전부 → 서비스 → 화면) — 기각, 쓰기 경로 없는 표 구간·소비자 없는 레지스트리가 생긴다(S3-2 적대 FE-5 "쓰기 경로 없는 표" 선례). (b) 서류·채권 각 1 PR — 기각, CLAUDE.md "큰 덩어리 일괄 생성 금지"·적대 검토 렌즈 분산.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(병합 전 분할 재조정은 문서 수정뿐).

---

## E2. 마이그레이션 번호와 DAG

**결정 — 번호는 S3-2(M13~M15)에서 이어 M16부터 쓴다.** sA §A19의 초안 `M16a~c`·sB §B17의 가칭 `M-B1·M-B2`는 아래로 재번호한다.

| 번호(슬러그) | 내용(형태 정본은 괄호의 부록) | 논리 의존 | `down_revision`(병합 순 사슬) | PR |
|---|---|---|---|---|
| **M16** `s33_receivables_payments` | `receivables`(sB §B2 — 선적 살아 있는 1건 부분 유니크·OPENING `(partner, invoice_ref)` 유니크·열 GRANT) + **`payments` 확장**(sB §B3 — `pi_id` NULL 허용·`receivable_id`·"정확히 하나" CHECK·복합 FK 2·UNIQUE 2, 기존 컬럼·CHECK·kind 값 변경 금지 `W:129`) + 복합 FK 대상 UNIQUE(`sales_orders (id, buyer_partner_id)`·`shipments (id, so_id)`, 금액 복합 FK 대상 — sC §C5) | shipments(M14 `acd34f28c11e`)·payments(S3-1 M08)·SO | **`281da4794717`**(현 단일 head — `mig:20261004_1753_281da4794717_s32_milestones_customs.py:45-46`) | PR-2a |
| **M17** `s33_so_short_close` | SO short-close 3열(+일관성 CHECK — 불변은 CHECK 불가라 단일 대입 통로 스캔 병행, sC §C5) + SO 상태이력 `reason_required` CHECK 재정의(COMPLETED 추가 — sB §B7 ①) | SO·M16 | M16 | PR-2b |
| **M18** `s33_company_profiles_documents_owner` | `company_profiles`(IMMUTABLE — sA §A3) + `documents.owner_type` VARCHAR(13)→(24)·CHECK 재정의 6종 추가(sA §A13, `code:modules/documents/models.py:68,107,143`) | documents | M17 | PR-3a |
| **M19** `s33_trade_document_renditions` | `trade_document_renditions`(IMMUTABLE, **source_type CHECK = QT·PI만**) + `document_types` 2행(QUOTATION·PROFORMA_INVOICE) | documents·M18 | M18 | PR-3b |
| **M20** `s33_lc_terms` | `lc_terms`(SO 1:N·살아 있는 SO당 1·통화 복합 FK)·제시 기록(선적 1:0..1)·하자 체크 마크(IMMUTABLE) (sB §B10 ⑥, sC T15~T17) | SO·shipments | M19 | PR-4a |
| **M21** `s33_commercial_invoices` | CI 헤더·라인·상태이력·PL 2표(sA §A5~A7) + **DocKind 값을 담는 DB CHECK 전수 확장**(PR 첫 커밋 실측) + renditions CHECK에 CI·PL 추가 + `document_types` 2행(COMMERCIAL_INVOICE·PACKING_LIST) | M18(레터헤드 FK)·M19·shipments·bank_accounts | M20 | PR-5a |
| **M22** `s33_shipping_instructions` | `shipping_instructions`(sA §A8) + renditions CHECK에 SI 추가 + `document_types` 1행 | M21 | M21 | PR-5b |

- **사슬**: `281da4794717 → M16 → M17 → M18 → M19 → M20 → M21 → M22`. 논리 의존이 고정하는 것은 M16→M17, M18→M19→M21→M22, M16→M21(CI 발행 TX가 채권 함수 호출 — 스키마 의존은 아님)뿐이다. 나머지 순서는 **병합 순서(E3)에서 나온다**.
- **S3-3의 다른 PR(1·1b·2c·3c·4b·5c·6·7)은 마이그레이션 0건**이다. 잡은 14 유지(sC §C10 ① — `code:modules/platform/scheduler.py:213-318` JobSpec 14개, 핀 `test:architecture/test_scheduler_registry.py:186-191`), 기능 플래그 표 `feature_flags`는 이미 있다(`code:modules/platform/models.py:67-77` — 토글은 첫 PUT이 INSERT, 시드 금지).
- **기존 표 CHECK 재정의 4건**(M17 상태이력 `reason_required`·M18 `owner_type`·M21/M22 renditions·M21 DocKind CHECK): `alembic check`가 CHECK를 감지하지 못하므로(S3-2 M15 `comm_logs` 선례 — E32 §E2) `op.drop_constraint`/`op.create_check_constraint`를 수기로 쓰고 **정의문 시험으로 양방향을 고정**한다. downgrade는 새 값의 행이 1건이라도 있으면 **RAISE로 실패**(데이터 소실 다운그레이드 금지 — sA §A19 방식을 S3-3 전 리비전 공통 규율로 승격).
- **renditions CHECK를 단계 확장(QT·PI → +CI·PL → +SI)으로 둔 이유**: 쓰는 경로가 없는 값을 DB가 받지 않게(fail-closed — DB가 마지막 방어선). 비용은 정의문 시험 3벌(부채 E-03).
- **리비전 공통 규율**(E32 §E2 승계): 상단 체크리스트 헤더(rename 없음·CHECK 수기 이름·부분 유니크 `WHERE deleted_at IS NULL` 또는 `status <> 'CANCELLED'`·시드 = `document_types`만·REVOKE·열 GRANT), 식별자 63자 이내 실측, `upgrade→downgrade→upgrade` 왕복, 드리프트 0(`test_migrations.py:70`), 단일 head.
- **시드 규율**: `document_types`는 Actor 없는 기준 표라 시드 관용구 허용(`test_scheduler_registry.py:50-52` 주석 "roles·document_types 선례"). 신규 업무 표(receivables·company_profiles·renditions·lc_terms 3표·CI 5표·shipping_instructions)는 전부 `_NEVER_SEEDED`에 같은 PR이 등재한다.

**근거**: E32 §E2(S3-2 M13~M15 형식), `D:374`, sA §A19 "번호·DAG 정본은 분할 부록", sB §B17 "번호는 통합".

**대안**
- (a) 마이그레이션을 서류 1건·채권 1건으로 몰기. 기각 — 쓰기 경로 없는 표 구간(S3-2 FE-5)과 무관 PR의 downgrade 결합.
- (b) M16에 SO short-close 3열·`reason_required`까지(sB §B17 원안 "CHECK만 미리"). **기각(자율 확정)** — SO 상태이력 CHECK는 코드 상수에서 파생되므로(`code:modules/trade_docs/machine.py` REASON_REQUIRED_TO — PR 첫 커밋 실측) PR-2a 구간에 DB(COMPLETED 포함)와 모델(미포함)이 어긋나 스키마 대사 시험이 깨지거나, 깨지지 않으면 공회전이다. 상태 기계를 바꾸는 PR-2b가 CHECK도 바꾼다(같은 커밋 묶음 원칙 ①).
- (c) renditions CHECK에 5종을 처음부터. 기각 — 위 fail-closed 사유(되돌리기 비용 비대칭: 넓힌 CHECK를 좁히려면 행 정리 필요).
- (d) M01부터 재시작. 기각 — S3-1·S3-2·S3-3의 "M05" 모호(E32 대안 (a) 승계).

**자율 확정**: 확정. **되돌리기 비용**: 병합 전 낮음(번호·순서 = 파일 rename). 병합 후 순서는 사실상 고정(downgrade 사슬 역행). 각 리비전은 additive라 개별 되돌리기는 downgrade 1회 — 단 업무 행이 생기면 RAISE(의도).

---

## E3. PR 분할 — 15 PR(백엔드 a / 화면 b·c, 직렬 병합)

### E3-0. 개요 (라벨 = 병합 순서)

| PR | 범위 | 마이그 | 프런트 | DoD·검증 배치 | GC v1.6 |
|---|---|---|---|---|---|
| **PR-1** | 문서 전용: 계획서 `docs/plans/s3-3-plan.md`+부록 A~E(+통합) 등재·DESIGN 부기 전건(E3-1)·ADR **0088~0098**+기존 ADR 부기(E4)·WBS v1.7·GC v1.6·PROGRESS(자율 확정 표기·부채 등재)·runbook 'S3-3 운영 개시' 초안 줄 | — | — | 문서 린트(ADR 5줄·WBS 행·GC 이력)·기존 전체 green | 등재만 |
| **PR-1b** | **공통 기반(백엔드, 마이그 0)**: P-39 `Idempotency-Key` 길이·제어문자 422(sC §C5 ②, `code:modules/idempotency/models.py:37` String(128))·`TODAY_IMPORT_POINTS` 일반화+커버리지 시험(sC §C13, `test:support/kst.py:19`)·**`.shard_durations.json` 재갱신**(Q-15 — 209파일/5171건 → 최신 아티팩트) | — | — | J(키 129자 422·128자 정상 — CJ-18)·K(TODAY 커버리지 변이) | — |
| **PR-2a** | **채권 원장**: receivables·채권 발생 T8(사람 1클릭)·OPENING T9(P-51)·취소 T10·채권 입금 T11·역기록 분기 T12·이중 입력 의심(P-14)·통화/초과/만료 PI(P-12 409 유지)·미수 단일 정의 `receivables/outstanding.py`(쓰기 함수 없는 서브모듈 — sC §C10 ②-2)·**INVOICE_DATE 앵커 배선**(가정 4)·aging 읽기·목록 CSV·`ChildLink(SHIPMENT, receivables)`·LOCK_ORDER `receivables` 슬롯·노출 구성 변경 쓰기 거래처 잠금(sC §C3) | **M16** | — | **DoD "aging 정확"**·**검증 A(일부입금 전환)**·A(역순 취소·선적당 1)·J(CJ-02·03·04·11·12·14~17·19·21)·K(authz·Page·IDOR·제약 ⊆ 번역표)·B(채권 CSV 한글·BOM) | A26·A27·A30(앵커 층)·F6 |
| **PR-2b** | **노출 전환(원자)**: provider 실구현 + `app/bootstrap.py` 3 입구 등록(P-08)·`invoiced_by_sales_order` 차감(P-01)·평가 재배치(SO 묶음 1회 환산)·SO COMPLETED 자동 엣지·short-close(Q-04=P-02)·ADR-0076 결속 시험 **대체**·총수 갱신·REGISTRY 엔트리·DoD 공백/이중 0 속성 시험 | **M17** | — | **DoD "노출 공백 0·이중 0"**·A(GB-01~03·08~10)·I(SO 자동 엣지 정확히 3·4금)·J(CJ-05~07·CJ-08 PI 역기록 잠금)·K(3 입구 `not is_default_provider()`) | A28·A29·F5·H7 |
| **PR-2c** | 채권·aging·입금·short-close 화면, SO/선적 상세 채권 패널, 여신 카드 '미수 미반영' 배지 소멸 확인(sB §B6 ⑦) | — | ○ | vitest(DUE_UNKNOWN ≠ 미도래 적색·통화 간 합산 0·사유 빈칸 제출 불가)·390px | — |
| **PR-3a** | **레터헤드·전표 첨부**: company_profiles(불변 판·as-of — P-11, R-3a-5 = Shipper 블록)·ADMIN API·documents `owner_type` 확폭 6종(P-13)·소유자 해석기(테이블 이름 Core 조회)·PO 소유 미개방·수입선적 첨부 422 | **M18** | — | A(as-of 대체 금지)·J(IMMUTABLE 42501)·K(owner_type별 403 순서 CK-03·GOVERNED `documents`·임포트 방향)·G(원가 채널 0 — PO·수입선적 첨부) | G4(첨부 층) |
| **PR-3b** | **렌더 엔진 + QT·PI 렌더**: `doc_render`(순수 — DB·네트워크·`now`·float 0)·**의존성 `reportlab`·`openpyxl` 추가(== 고정)·동봉 폰트(sha256 고정)**·QT/PI PDF·XLSX·EN/KO·renditions 정본·생성물 삭제 잠금·주입 방어 | **M19** | — | B(한글·장문·무상 QT/PI 렌더 — GC-A24 렌더 층)·G(renditions 해시 멱등·원가 키 0)·K(순수성 AST·폰트 digest·외부 임포트 허용 목록·주입 CK-09·다운로드 attachment)·H(복원 리허설 FILE 대상) | A24(QT/PI 층)·G4(렌더 층) |
| **PR-3c** | 레터헤드 관리 화면·전표 상세 첨부 패널·QT/PI 다운로드(형식·언어 선택)·`detail-layout` 390 | — | ○ | vitest(첫 판 미등록 안내·NOT_EFFECTIVE 안내·`new Date` 금지 확장) | — |
| **PR-4a** | **L/C**: lc_terms 등록·개정(SUPERSEDED)·취소·제시 기록·하자 체크 마크(판정 필드 0)·`lc_inputs_for` 어댑터·`milestone_view` 배선(PRESENTATION_DEADLINE·PAYMENT_DUE)·tolerance 차단(채권 발생 422)·`PUT /feature-flags/{code}`(P-10, 폐쇄 레지스트리 `{"lc"}`)·`lc_allows_order_consignee`(없으면 False)·LOCK_ORDER `lc_terms` 슬롯·G-09 종결 | **M20** | — | **검증 K "L/C 제시기한 MIN·tolerance" API 경로**(CK-13)·H(플래그 OFF = 입력만 — CH-09)·A(GB-18·19·20)·J(CJ-13 개정 version)·I(CI-09 판정 필드 0) | A30(L/C 층) |
| **PR-4b** | L/C 입력·개정·제시 기록·하자 체크리스트 화면·설정 '기능 플래그' 토글(ADMIN) | — | ○ | vitest(플래그 OFF 기존 기한 표시 유지·체크리스트에 '적합' 문구 0) | — |
| **PR-5a** | **CI·PL 발행**: DocKind `COMMERCIAL_INVOICE`(접두어 CI)·상태 2·사람 엣지 1·**검수 게이트**(INSPECTED·RELEASED·SHIPPED — 운영 경로 S4-2까지 닫힘, sA §A2)·검증 카탈로그(V1~)·PL 2표(Q-05)·발행 TX 채권 합류(가정 3)·자동 렌더 4·재발행·**R-3a-4 당사자 version+1**·STALE(sC CJ-22)·DG NOTICE(Q-17 부분 완화)·LOCK_ORDER CI 슬롯·총수 갱신 | **M21** | — | **DoD "교차 불일치 저장 거부"·"무상 생성"(서비스 층 — 시험 전용 팩토리)**·**§20 B 검수 미완료 차단(운영 경로 409)**·J(CJ-01·11·22·동시 발행 1건)·K(총수·커널 3자 대사·`force_shipment_status_for_test` 앱 호출 0)·I(CI-08 자동 발행 0) | A22·A23·A24(CI 층)·A25·G4(발행 층) |
| **PR-5b** | **S/I 발행**: `shipping_instructions`(채번 SI·불변·재발행)·TO ORDER 술어 소비(Q-06 종결)·Incoterms→운임 조건 도출·포워더 필수 | **M22** | — | B(sA §A21 ⑨)·J(동시 발행·IMMUTABLE)·K(TO ORDER 술어 False 기본 변이) | — |
| **PR-5c** | 서류 발행 대화상자(미리보기 RELEASE_ORDERED부터·검증 결과 표·NOTICE 확인)·발행 409 `NOT_INSPECTED` 안내 문구·다운로드 | — | ○ | vitest(BLOCK 시 발행 버튼 비활성·NOTICE 미확인 제출 불가)·390px | — |
| **PR-6** | **기일 스캔 확장(마이그 0, JOB 14 유지)**: `trade-deadline-scan` 후보 OR 확장·대금만기·제시기한·L/C 유효/선적기일·OPENING 만기·SO 담당 수신·발송 직전 재확인·허용 임포트 목록·`trade-docs-totals-verify` CI 편입 확인·**`fe:lib/alert-routes.ts`에 `receivables` 1줄**(K 출구 계약) | — | 1줄 | **Q-08 종결**·H(CH-01~15)·I(CI-03 레지스트리·시각 무변경·CI-04·05)·K(CK-14 출구) | A31 |
| **PR-7** | **마감**: runbook 'S3-3 운영 개시'(레터헤드 첫 판·OPENING 반입 순서·채권 발생 버튼·short-close 판단·이중 입력 확인·통화 불일치 입금·`lc` 토글 클릭 단위·**CI/PL/S-I 외부 작성 안내 A-05**·수기 양식 채권/입금 행)·워크스루(렌즈 11)·`docs/testing.md`·GC v1.6 `golden` 대사·WBS/GC 확정·PROGRESS S3-3 종결·부채 최종 목록 | — | 잔여 정비 | 워크스루 3층(ADR-0086)·11렌즈·CH-14 runbook 계약 | 전건 대사 |

**선행 관계(병합 순서 = 라벨 순서)**: **1 → 1b → 2a → 2b → 2c → 3a → 3b → 3c → 4a → 4b → 5a → 5b → 5c → 6 → 7**(15 PR).
- 고정 간선: 2a → 2b(노출 전환은 채권 원장 위) / 2a → 4a(tolerance 차단이 채권 발생에 걸림) / 3a → 3b(렌더가 레터헤드 as-of를 읽음) / 3a·3b·2a → 5a(레터헤드 FK·자동 렌더·채권 합류) / 5a → 5b(S/I 원천 = 살아 있는 CI) / 4a →(연성) 5b(TO ORDER 술어 — 없으면 False라 순서가 바뀌어도 fail-closed) / 2a·4a·5a·2c → 6(스캔 대상·검산·이동처 화면) / 전부 → 7.
- 화면 PR(2c·3c·4b·5c)은 짝 a 이후 어디든 가능하다(마이그 0). 단 **2c는 2b 뒤**다 — 2a 단독 구간에 채권 버튼이 생기면 노출 전환 전 채권이 운영에서 쌓인다(E3-3 가정).
- **채권 축(2a·2b)을 서류 축(3·5)보다 앞에 둔다(자율 확정)**: ① WBS v1.6이 이 세션에 결속한 유일한 "같은 PR" 의무이고 S3-1 부채 P-01·P-08이 두 세션째 이월 중이다(`P:1615`·`P:1622`) ② 기존 여신 평가 함수를 재배치하는 최대 교차 위험(sB §B6 ④)을 일찍 드러낸다 ③ CI(5a)·L/C(4a)가 채권에 의존한다 ④ CI·PL·S/I는 검수 게이트로 운영 가치가 S4-2까지 0이다(sA §A2). 대안(서류 먼저 — QT·PI 렌더의 즉시 운영 가치)은 3a·3b를 2a 앞으로 당기는 것만으로 가능하다(의존 없음) — 통합이 운영 우선순위로 뒤집어도 DAG만 재정렬된다(되돌리기 낮음).

### E3-1. PR-1 — 문서 전용

**결정**
- "계획 세션 → PR-1 이어쓰기" 표준 경로(CLAUDE.md 2026-08-12 S2-3 판정). **착수 블록 A항 세션 확인 가드 필수**: S3-3 계획 맥락(부록 A~E+통합)이 없는 세션이면 작업 없이 정지한다.
- 포함: 계획서 최종본, 부록 등재, DESIGN 부기(아래), ADR **0088~0098** + 기존 ADR 부기(E4), WBS v1.7(E5-1), GC v1.6(E5-2), PROGRESS '## 현재' 갱신·자율 확정 표기·부채 등재.
- **DESIGN 부기 대상**(각 부록 부기 목록의 합집합 — 문면 정본은 부록): §3 표 맵(receivables·payments 확장·company_profiles·renditions·lc_terms 3표·CI 5표·S/I — sA §A22·sB §B20) / §4.7 [M1](owner_type 확폭·6종·PO 제외·생성물 삭제 잠금·문서 종류) / §7.2 [M4](CI 상태 2·엣지 1, **CI/PL 게이트 = INSPECTED·RELEASED·SHIPPED — 운영 경로 S4-2까지 닫힘**, SO COMPLETED 자동 엣지·short-close, 총수) / §7.6 [M4](서류 매트릭스·DGD/B-L/PO 발주서 범위 밖·검증 카탈로그·렌더 방식·산출물 정본) / §7.10 [M4](채권 모델·미수 정의·노출 산식 "미결 SO 잔여(총액 − 채권 전환분)"·SO 묶음 환산·aging·`lc` OFF 의미) / §15(SO 자동 엣지 2 → 3·4금 논증·잡 14 유지·스캔 대상 확장) / §17.2(LOCK_ORDER 3슬롯·PI SHARE 뒤 `lock_chain` 금지·노출 구성 변경 쓰기 거래처 잠금) / §17.4(P-39 키 길이·OPENING 유니크) / §17.5(IMMUTABLE 추가 표·열 GRANT receivables·commercial_invoices) / §2·§18.1(S3-3 권한 행·`GOVERNED_PREFIXES`) / §20 해석 주(S3-3 매핑·B 차단 본체 = S3-3 서비스 층+S4-2 운영 층·H "플래그 오프 완전 비활성" = 입력 진입 비활성·K L/C = API 경로 배선).
- **코드 변경 0** — CI는 기존 테스트 전체 green이면 된다.

**근거**: `docs/plans/s3-2-plan.md:87`(S3-2 PR-1 선례), `D:480`(렌즈 10), CLAUDE.md "설계 변경은 DESIGN.md 갱신 + ADR 5줄이 세트".

**대안**: 문서를 구현 PR에 분산 — 기각(자율 확정 판정이 구현 리뷰에 섞이면 사후 번복 지점을 못 찾고, ADR 번호가 병합 순서에 따라 흔들린다 — E32 §E3-1 승계).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(문서 revert).

### E3-2. PR-1b — 공통 기반 (마이그 0)

**결정**
- **P-39**(`P:1653` — 기존 결함, 소유 "코어 소규모 수정"): S3-3 신규 쓰기 6종 이상이 전부 `Idempotency-Key`를 쓰므로(sB §B20) 그 전에 코어에서 닫는다. 129자·제어문자 = 422 `KEY_INVALID`(sC CJ-18). 기존 전 엔드포인트에 같은 검사가 적용되므로 **독립 PR로 격리**한다(회귀 시 원인 1개).
- **`TODAY_IMPORT_POINTS` 일반화**(sC §C13 — 현행 `MILESTONE_TODAY_IMPORT_POINTS` `test:support/kst.py:19`): 2a(aging 기준일)·4a(제시 기록 ≤ 오늘)·6(스캔)이 쓰는 '오늘' 고정 지점을 미리 공용화하고 커버리지 시험(누락 모듈 추가 변이 → 실패)을 단다.
- **`.shard_durations.json` 재갱신**(Q-15 — 트리거 "다음 아티팩트 교체"): 현재 파일 209·테스트 5171(실측)인데 S3-2 종결 수집은 5774다 — 미등재 신규 파일이 평균값(0.3666초)으로 배정되는 상태를 S3-3 대형 신규 파일(동시성·속성 시험)이 들어오기 전에 정리한다. 재갱신은 **2b·5a 병합 후 한 번 더**(E6-2).

**DoD**: CJ-18 3건·기존 전체 green·샤드 3개 소요 편차 기록(PROGRESS).

**대안**: P-39를 2a에 동석 — 기각(채권 리뷰에 전 엔드포인트 영향 변경이 섞임). durations를 PR-7에서 — 기각(S3-3 내내 불균형 샤드로 40분 timeout 근접 위험, R7).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(검사 1곳 revert).

### E3-3. PR-2a — 채권 원장 (M16)

**결정**
- sB §B1~B5·B12·B14·B15의 쓰기·읽기 전부와 sC T8~T12·T22를 한 PR에 둔다. **노출 영향 0**(provider 기본 — 채권은 노출에 안 보이고 SO 항은 전액 → 과대 방향만, sB §B19 PR-B1a). 이 상태를 **K 시험으로 고정**한다: "PR-2a 구간에서 채권 발생 전후 노출 = SO 총액 그대로(감소 0)".
- **2a·2b 분리 + 2c(화면)를 2b 뒤에 두는 것으로 운영 노출을 막는다(자율 확정)**: 2a~2b 구간에는 채권 화면이 없어 사람이 운영에서 채권을 만들 수 없다(API 직접 호출뿐 — runbook 안내 대상 아님). sB가 허용한 "B1a·B1b 합침"보다 리뷰 면적이 작고, 구간 위험은 '과대 노출'(fail-safe)뿐이다.
- **INVOICE_DATE 앵커 배선을 여기서**(가정 4): aging(DoD)이 만기를 `milestone_view` 조립 결과에서 읽으므로(sB §B12 ③), 앵커 배선 없이 aging을 내면 INVOICE_DATE 결제조건 채권이 전부 `DUE_UNKNOWN`이다(fail-visible이지만 DoD "aging 정확"을 이 PR에서 못 닫는다). `schedule.py` 본문 무변경·`AnchorContext` 가산만(ADR-0081 "산식 재정의 금지").
- `CHILD_LINKS`에 `ChildLink(SHIPMENT, "receivables", "shipment_id")` — 살아 있는 채권이 있는 선적 취소 409 `SUCCESSOR_ALIVE`(GB-07). `test_doc_chain_contract` 갱신 동반.
- LOCK_ORDER에 `receivables` 슬롯(sC §C2 ③) — 채권 발생은 **PI 무잠금**(sC §C2 ⑤ — sB §B9 ②의 "PI FOR SHARE"를 발생에서 제거한 sC 확정 승계).
- 역기록 T12: peek → 대상 분기 → 거래처 잠금 → 대상별 잠금(현행 `lock_chain(PI, None)` 흐름 결함 — sC §C1 T12).

**DoD**: WBS DoD "aging 정확"(GB-25 경계 8점)·검증 A "일부입금 전환"(GB-13) / GB-04~07·11~17 / sC CJ-02~04·11·12·14~17·19·21 / `_NEVER_SEEDED`·table_policy·users FK 분류 / authz 행·`GOVERNED_PREFIXES` `/api/v1/receivables` / Page 50 자동 스캔 / 제약 집합 ⊆ 번역표 / 채권 CSV UTF-8 BOM·수식 이스케이프.

**근거**: `W:129-131`, sB §B19 PR-B1a, sC §C1·§C2·§C3.

**대안**: (a) 2a·2b 합침 — 기각(위). (b) 앵커 배선을 4a(sB 원안 PR-B2) — 기각(DoD 미완 PR 병합).

**자율 확정**: 확정. **되돌리기 비용**: 낮음~중간. M16 downgrade 1회(채권 행 0일 때). payments 확장 되돌리기는 채권 입금 행이 있으면 RAISE(의도).

### E3-4. PR-2b — 노출 전환·COMPLETED·short-close (M17) — S3-3 최대 위험 PR

**결정**
- **쪼개지 않는다**(E1 묶음 ①). provider 실구현 등록 + 채권 전환분 차감 + SO 묶음 환산 + COMPLETED 자동 엣지 + short-close + ADR-0076 결속 시험 대체가 한 PR이다. 커밋 순서(각 커밋 전체 green):
  ① `invoiced_by_sales_order` Protocol 가산(기본 `{}`) + 평가 재배치(SO 묶음 1회 환산) — 기본 provider에서 기존 S3-1 평가 시험 **기대값 무변경** 확인(sB §B6 자율 확정 조건) →
  ② provider 실구현 + `app/bootstrap.py`(현재 없음 — 실측) + 3 입구(API `code:main.py`·worker·CLI `code:cli.py`) 등록 + GB-27 →
  ③ M17 + SO 상태 기계(COMPLETED 자동 엣지 1·RESERVED 축소·TERMINAL·REASON_REQUIRED_TO) + `test_doc_machines.py:141-153` **대체**(새 단언: COMPLETED 진입 엣지 존재 ⇒ 부트스트랩 후 `not is_default_provider()` + `COMPLETED ∈ CLOSED_STATUSES`(`code:modules/credit/exposure.py:19`) + SO 자동 엣지 정확히 3) + 총수 핀 →
  ④ `converge_sales_order_completion`(호출처 핀) + short-close API + 2a 채권 발생 TX에 수렴 호출 배선 →
  ⑤ DoD 시험(B8 표 3벌 + 속성 시험) + 동시성(CJ-05·06·07·08).
- **총수(이 PR 단독 증분)**: 허용 30 → **31**(사람 18·자동 12 → **13**) / 미허용 152 → **151** / 총 **182** 불변(기존 SO 쌍의 이동). `code:modules/trade_docs/machine.py:7-8` 독스트링 동반. 최종 합산(5a 후 32/152/184)은 E3-8.
- §15 4금 논증 갱신(sC §C11 — 도착 COMPLETED = 이행 완료 반영, 트리거 = 사람 1클릭과 같은 TX), `test_no_auto_confirm_code_path_exists` REGISTRY 엔트리 가산.

**DoD**: WBS DoD "미수 provider 등록 후 노출 공백 0·이중 0"(GB-01·02·03 + 속성 시험 + CJ-07 결정적 판) / GB-08~10 / P-08 "기본 구현 잔존 금지"(3 입구 각각) / I(SO 자동 엣지 3·4금) / J(CJ-05·06·08).

**근거**: `W:132`, ADR-0076 되돌리기 비용란("S3-3 provider PR에서 반드시 연다 — 그때 이 ADR을 '대체' 표기"), sB §B6~B9, sC §C3·§C11.

**대안**: (a) COMPLETED만 후속 PR — 기각(WBS·결속 시험 위반, 노출 공백 — ADR-0076 근거). (b) provider 등록만 먼저 — 기각(`invoiced_by_sales_order` 없이 `reflected=True`면 채권분이 SO 항과 이중 계산 — DoD "이중 0" 정면 위반).

**자율 확정**: 확정. **되돌리기 비용**: 중간(상태이력 CHECK·총수 핀·SO 3열·평가 함수). 반대로 공백이 실재하면 **높음**(과소 노출 위의 확정은 소급 불가 — ADR-0076 승계). 그래서 3렌즈+2차 적대 검토(E7).

### E3-5. PR-3a·3b — 레터헤드·전표 첨부 (M18) / 렌더 엔진·QT·PI 렌더 (M19)

**결정**
- **3a**: 레터헤드는 소비자(렌더) 직전 PR에 둔다 — 소비자 대기 구간 1 PR(S3-2 R-2a-4 `holiday_flag` 선례 — 해소까지 3 PR). 전표 첨부(P-13)는 렌더와 독립이고 원가 채널(ADR-0024) 판정이 걸려 있어 렌더 PR과 리뷰 렌즈를 분리한다.
- **3b**: 외부 의존성을 처음 들이는 PR이다(R3). 커밋 ①은 `requirements.txt`에 `reportlab==`·`openpyxl==` 고정 추가(파일 1행 주석 규약 "직접 의존은 전부 == 로 고정"), `requirements-dev.txt`에 PDF 텍스트 추출 시험 도구(예: `pypdf==` — 착수 시 선택·실측), 폰트 파일(라이선스 OFL 확인·sha256 상수·크기 기록), mypy 무타입 모듈 설정, CI 설치 시간 실측. 커밋 ②부터 `doc_render` 순수 엔진.
- QT·PI 렌더는 **운영 즉시 개방**(sA §A2 "QT·PI 렌더는 프로덕션에서 바로").
- 생성물 삭제 잠금(`DOCUMENTS.DOCUMENT.GENERATED_LOCKED`)은 renditions가 생기는 3b에 둔다.

**DoD**: 3a — as-of 대체 금지(NOT_EFFECTIVE·NOT_REGISTERED)·IMMUTABLE 42501·owner_type별 권한/403 순서·PO 소유 422·수입선적 첨부 422·`_require_owner` 임포트 방향 / 3b — sA §A21 B ③(무상 QT/PI 렌더)·⑤(한글·장문 잘림 0)·K(순수성 AST·폰트 digest·외부 임포트 허용 = `doc_render`만)·G(renditions 해시 멱등·경쟁 렌더 산출물 1·고아 0)·CK-09(주입)·H(복원 리허설 FILE 검증 포함).

**근거**: sA §A3·§A12·§A13, sC §C7·§C12, `D:461`.

**대안**: 3a·3b 합침 — 기각(의존성·파일 IO·마스터·documents 권한을 한 리뷰에 — 보안 렌즈 분산). 렌더를 5a(CI)와 같은 PR — 기각(5a가 커널 편입으로 이미 최대 면적).

**자율 확정**: 확정. **되돌리기 비용**: 3a 중간(불변 판 표·owner CHECK 재정의 — 첨부 행 생기면 RAISE). 3b 낮음~중간(의존성 제거는 쉬우나 renditions에 첫 정본 파일이 쌓이면 보존 의무 — 파일은 지우지 않는다).

### E3-6. PR-4a — L/C (M20)

**결정**
- sB §B10·B11과 sC T15~T18을 한 PR에 둔다. **산식 재정의 금지**(`schedule.py` 함수 본문 diff 0을 PR 본문에 명기 — ADR-0081).
- **플래그 토글 경로(P-10)를 lc_terms와 같은 PR**에 둔다(`W:129` "L/C feature flag 행 공급·토글 경로(`lc_terms`와 함께)"). OFF = 새 입력만 닫음(sB §B10 ⑧ — §20 H 해석 부기). G-09(L/C CLI)는 구현하지 않고 종결(sC §C10 ⑤).
- tolerance 차단은 **2a의 채권 발생 함수에 422 분기 추가**다 — 2a를 수정하는 유일한 후속 PR이라 2a 시험(GB-06 등) 재실행을 DoD에 넣는다.
- `lc_allows_order_consignee`(sA §A20)를 여기서 제공(없으면 False) — 5b가 소비.

**DoD**: WBS 검증 K 해석 주 "운영 경로 배선"(CK-13 = GB-18·20) / GB-19(lc_terms 없음 = UNKNOWN 유지 — GC-A16 무변경) / GB-26·CH-09 / CJ-13 / CI-09 / 기존 S3-2 `milestone_view` PRESENTATION_DEADLINE 고정 UNKNOWN 단언 개정(sB §B21 "깨질 기존 시험").

**자율 확정**: 확정. **되돌리기 비용**: 낮음(어댑터·조립 1곳 + M20 downgrade — lc_terms 행 있으면 RAISE).

### E3-7. PR-5a·5b — CI·PL (M21) / S/I (M22)

**결정**
- **5a는 S3-3 두 번째 고위험 PR**이다(DocKind = DB 저장값 — 분리 불가에 가깝다, sA §A4 되돌리기 "높음"). 커밋 ①은 S3-2 R-23 방식: M21 + 모델 + table_policy + DocKind·상수 dict 11종·FIELD_POLICY·**CI 상태 기계(사람 엣지 1 포함)**·사슬 레지스트리·총수 핀을 **한 커밋**(엣지 0 선편입은 도달성 시험을 깬다). 이어서 ② 검수 게이트 상수(L0)+결속 시험 2건 ③ 검증 카탈로그(순수) ④ 발행 서비스(채권 합류·자동 렌더·재발행) ⑤ R-3a-4 version+1·STALE ⑥ 동시성.
- **검수 게이트**: 운영 경로(HTTP)로 만든 RELEASE_ORDERED 선적 발행 = 409 `NOT_INSPECTED`(GC-A25 — §20 B "검수 미완료 선적의 CI·PL 생성 차단"의 **운영 증명**). DoD "교차 불일치 거부·무상 생성"은 시험 전용 팩토리 `force_shipment_status_for_test`(`backend/tests/factories/`에만, `app/` 호출 0 스캔)로 INSPECTED 선적을 만든 **서비스 층** 시험으로 충족한다 — ADR-0081과 같은 구조(sA §A2). WBS v1.7 주석 ①로 문면 대비를 남긴다.
- **R-3a-4**(`P:90` 당사자 변경 version 미증가 — 트리거 "혼선 1건")는 STALE 판정의 전제라 **재트리거·해소**를 이 PR에 둔다(sC §C4 ②). 선적 화면의 409 처리 회귀는 5c vitest로 확인.
- **5b**는 5a 직후. TO ORDER는 4a 술어가 True일 때만(Q-06 종결).

**DoD**: 5a — WBS DoD 2항(서비스 층)·GC-A22~A25·sA §A21 A·J·K·I행·CJ-01·11·22·총수 / 5b — sA §A21 B ⑨·IMMUTABLE·TO ORDER 변이.

**근거**: sA §A2·§A4~A11·§A15, sC §C4·§C11, `D:444`(§20 B), `W:155`(S4-2 주석).

**대안**: CI·PL·S/I 한 PR — 기각(커널 편입 리뷰에 비커널 불변 표·운임 도출이 섞임). 5a를 S4-2로 이연 — 기각(WBS S3-3 산출물·DoD 정면 누락, 게이트를 지금 세우는 sA §A2 판정과 충돌).

**자율 확정**: 확정. **되돌리기 비용**: 5a **운영 데이터 전 중간 / 후 높음**(단, 게이트로 S4-2까지 운영 CI 행 0이 기대 — 되돌리기 창이 S4-2까지 열려 있다는 것이 이 배치의 부수 이점). 5b 낮음.

### E3-8. 총수 핀 — 두 PR이 같은 시험을 고친다

| 시점 | 허용(사람·자동) | 미허용 | 총 쌍 | 근거 |
|---|---|---|---|---|
| S3-2 종결(현재) | 30(18·12) | 152 | 182 | `code:modules/trade_docs/machine.py:7-8` |
| PR-2b 후 | **31(18·13)** | **151** | 182 | SO IN_SHIPMENT→COMPLETED 자동 1(기존 쌍 이동) |
| PR-5a 후 | **32(19·13)** | **152** | **184** | CI 2상태 = 순서쌍 2(허용 1·미허용 1) |

- sA §A4는 "31/153/184"(CI 단독), sB §B7 ⑤는 "31/151/182"(SO 단독)를 적었다 — **통합 합산은 위 표**다(손계산 — 각 PR 첫 커밋 실측 확정). 직렬 병합이므로 5a는 2b가 고친 EXPECTED 위에서 재배치한다(충돌 예상 지점 — R6).

### E3-9. PR-6 — 기일 스캔 확장 / PR-7 — 마감

- **PR-6**(sB §B13·sC §C10 정본): 신규 잡 0, 시각 변경 0, 4금 집합 무변경(`test_scheduler_registry.py:149-183` 주석만 갱신), `_trade_chain_imports` = {expiry_sweep, deadline_scan} 무변경(`test:architecture/test_no_auto_confirm_code_path_exists.py:985`), `_SCAN_ALLOWED_APP_MODULES`(`:1029`)에 `receivables.models`·`receivables.outstanding`·`payments.models`·`sales_orders.models`·lc 모델 가산. **후보 질의 확장 회귀 시험 CH-05**(실적이 다 들어간 동결 선적의 대금만기가 현행 후보에서 빠지는 실측 결함 — sC §C10 ②-1)를 첫 커밋에 **실패하는 시험으로 먼저** 넣는다. `fe:lib/alert-routes.ts`에 `receivables: (id) => \`/receivables/${id}\`` 1줄 + 기존 대상 이름 표(S3-2 PR-8 `alertTargetLabel`) 1줄.
- **PR-7 워크스루 경로(렌즈 11 — 1회 관통, 3층 증거 ADR-0086)**: 관리자 레터헤드 첫 판(과거 유효일) → QT 렌더 KO·EN 다운로드 → PI 렌더 → SO 확정(기존) → 부분선적 2건 출고지시 → 선적1 채권 발생 → **여신 카드 노출 불변**(TT_DEFERRED) → 채권 입금 일부 → aging → 선적2 채권 → SO 완료 / (변형) short-close → `lc` 토글 ON → L/C SO `lc_terms` 등록 → B/L 실적 → 제시기한 표시 → CLI `trade-deadline-scan` → 알림(대금만기·제시기한) → 알림 클릭 → 채권·선적 화면 → 제시 기록 → 재스캔 0 → **CI 발행 시도 = 409 '검수 완료 후 발행' 안내**(운영 게이트 출구 — runbook A-05 문장과 대사) → OPENING 반입 → aging 반영. 증거: 실 HTTP e2e `tests/e2e/test_s3_3_walkthrough.py`(H 입구~출구·J 같은 키 재생·K 출구 계약) + 소스 계약 vitest + 리포 밖 실브라우저 1회(390px 포함).
- PR-7 종결 대사: §20 그룹 수(`docs/testing.md`), `golden` 마커 수 ≥ 85 + GC v1.6 14건, S3-3 부채 최종 목록(S3-2 표 형식 `P:50-166`), Q-04·Q-05·Q-06·Q-08·R-3a-4·R-3a-5·P-01·P-08~P-15·P-39·P-51 종결·유지 표기.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(잡 `enabled=false`·후보 OR 절 상수, 문서 revert).

---

## E4. ADR 목록 (0088부터) — 통합이 번호를 바꾸면 PR-1에서만 재번호

**결정** — 실측 최대 번호 0087(`docs/adr/0087-approval-integrity-check-job.md`)에 이어 **0088~0098(11건)**. 묶음 기준 = "독립적으로 번복될 수 있는 단위"(E32 §E4 승계). 5줄 형식(상태·맥락·결정·근거·기각한 대안·되돌리기 비용 — ADR-0087 서식). 상태는 전부 "자율 확정 — 사후 번복 가능".

| ADR | 제목(요지) | 원천 결정 | 구현 PR |
|---|---|---|---|
| **0088** | 채권 모델 — 수출 선적 1건당 살아 있는 채권 1·발생 = 사람 1클릭(자동 0)·발생 경로 상태 분할(S3-3 = 전용 엔드포인트, CI 발행 TX 합류는 기존 채권 재사용)·미수 단일 파생 정의·aging 30/60/90 + `DUE_UNKNOWN`·INVOICE_DATE 앵커 원천 = 채권 `invoice_on`·OPENING 이월(P-51) | sB §B1·B2·B4·B12·B14·B15, sC T8·T9 | 2a(·5a 합류) |
| **0089** | `payments` 확장(P-09)·채권 입금·역기록 대상 분기·이중 입력 의심 409+ack(P-14)·통화 불일치·초과 거부·EXPIRED/CANCELLED PI 입금 409 유지(P-12)·선수금 충당 FIFO(P-15) | sB §B3·B5, sC T11·T12 | 2a |
| **0090** | 여신 노출 전환 — provider 실구현 3 입구 등록(P-08)·**채권 전환분 차감**(P-01)·SO 묶음 1회 환산·환산 불가 = UNEVALUABLE·노출 구성 변경 쓰기 = 거래처 잠금 선행 | sB §B6·B8·B9, sC §C3 | 2b |
| **0091** | SO COMPLETED 자동 엣지 1·short-close(사람 결정 3열·사유)·완결 판정 함수 1개·총수 31/151/182 — **ADR-0076 대체** | sB §B7, sC §C11 | 2b |
| **0092** | 자사 레터헤드 불변 판 + as-of(대체 금지)·Shipper 블록 = 레터헤드(R-3a-5, 선적 SHIPPER 422 유지)·P-11 | sA §A3 | 3a |
| **0093** | 서류 렌더링 — 서버 내 PDF(ReportLab)·XLSX(openpyxl)·동봉 폰트·템플릿 판 동결·외부 호출 0·**산출물 정본 = 첫 렌더 파일(renditions)**·QT·PI 운영 개방·주입 방어 (**E-Q1 판정 결과에 따라 문면 확정**) | sA §A12·A13, sC §C1·§C7 | 3b |
| **0094** | L/C — `lc_terms` 최소 열·제시 기록·하자 체크 마크(판정 0)·**S3-2 산식 배선만(재정의 금지)**·tolerance 상한 초과 채권 422·플래그 토글 경로(P-10)·**OFF = 입력만**(§20 H 해석)·G-09 종결 | sB §B10·B11, sC T15~T18 | 4a |
| **0095** | CI 커널 편입(DocKind `COMMERCIAL_INVOICE`·접두어 CI)·상태 2·사람 엣지 1·PL 동반(번호 공유, Q-05)·S/I 비커널 불변(채번 SI)·총수 32/152/184 | sA §A4~A8·A11 | 5a·5b |
| **0096** | CI·PL·S/I **검수 게이트 선배치**(INSPECTED·RELEASED·SHIPPED)·운영 경로 S4-2까지 닫힘·DoD = 서비스 층 시험(ADR-0081 구조)·runbook 외부 작성 안내(A-05) | sA §A2 | 5a |
| **0097** | LOCK_ORDER S3-3 개정 — `lc_terms`(SO 뒤)·CI 슬롯·`receivables`(shipment_children 뒤·approvals 앞)·**PI SHARE 뒤 `lock_chain` 금지**·채권 발생 PI 무잠금 | sC §C2, sB §B9 | 2a·4a·5a(슬롯별) |
| **0098** | S3-3 권한·마스킹 — 경로 단위 역할(CI·채권·입금·short-close·L/C = A·T / PL·S/I = A·T·L / OPENING·채권 취소·플래그·레터헤드 = A)·`GOVERNED_PREFIXES` +4(receivables·feature-flags·company-profile·documents)·렌더 다운로드 역할 축소·원가 채널 0·계좌번호 로그 0 | sC §C6·§C7, sA §A16 | 2a·3a·3b·4a·5a |

- **기존 ADR 부기**(새 번호 없이 "부기 2026-10-xx"): ADR-0076(**대체** 표기 → 0091), ADR-0064(노출 산식 문면), ADR-0078(LOCK_ORDER 승계 → 0097), ADR-0081(운영 경로 배선 이행 — 4a), ADR-0084(스캔 대상 확장·JOB 14 유지 — 6), ADR-0074(SHIPPER 자사 = 레터헤드·CI 총수), ADR-0054(접두어 CI·SI), ADR-0028(documents owner 6종 확장)·ADR-0029(생성 파일 다운로드 규약), ADR-0024(원가 10번째 채널 미개방 — 렌더·첨부), ADR-0067(LOGISTICS PL·S/I 쓰기), ADR-0073(durations 재갱신 시점), ADR-0086(S3-3 워크스루 3층 적용).
- **ADR 대상 아님**: PR 분할·마이그레이션 번호(일정 — WBS '버전 규칙'의 세션 분할과 다름), P-39(기존 결함 수정), R-3a-4(버그성 결함 해소), IMMUTABLE·열 GRANT(각 표의 ADR 본문에 §17.5 항목으로 포함 — S3-2 ADR-0083 선례처럼 기능 ADR에 동석).

**근거**: CLAUDE.md "설계 변경은 DESIGN.md 갱신 + ADR 5줄이 세트", `D:480`(렌즈 10), `D:362` ②("LOCK_ORDER 변경은 ADR"), sB §B20 문서 행·sC 자율 확정 판정표 ADR-C①②·sA §A22 후보 4건.

**대안**: (a) 부록마다 1개(4개) — 기각(COMPLETED(0091)는 S4 이후 번복 가능성이 가장 높은데 채권 모델과 한 ADR이면 부분 번복 표현 불가). (b) 결정마다(약 30개) — 기각(검색성). (c) 0090·0091 합침 — 기각(WBS가 같은 PR을 요구할 뿐, 노출 산식과 상태 기계는 따로 번복 가능).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(PR-1 병합 전 재번호 = 파일명. 병합 후 번호 고정·번복은 새 ADR "대체").

---

## E5. WBS v1.7·GC v1.6 갱신 — 둘 다 필요하다

### E5-1. WBS v1.7 (S3-3 행 문면은 원본 유지, 주석으로)

**결정 — 필요하다.** S3-3 판정이 WBS 문면과 어긋나는 지점이 있어 주석 없이 두면 S3-4·S4-2 착수 시 "누락"으로 오독된다(v1.6 선례 `W:117-125`).

| 행 | 주석 문면(요지) | 원천 |
|---|---|---|
| S3-3(`W:129`) ① | "CI·PL·S/I 템플릿 렌더링" → **CI·PL·S/I 운영 발행은 S4-2 INSPECTED 개방까지 닫힘**(검수 게이트 선배치), DoD "교차 불일치 거부·무상 생성"은 서비스 층 시험으로 충족. QT·PI 렌더는 운영 개방 | ADR-0096 |
| S3-3 ② | "receivables(만기 자동)" → 만기는 **저장 없이 파생**(OPENING만 `due_on` 저장), 발생 = 사람 1클릭 | ADR-0088 |
| S3-3 ③ | "lc_terms(feature flag)" → 플래그 OFF = 새 입력만 닫음(기존 L/C 기한 계산·알림 계속) | ADR-0094 |
| S3-3 ④ | DESIGN §7.6 문면의 **DGD**는 WBS 산출물에 없음 → S4-4(DG 게이트) 배정 명시, PO 발주서 렌더 미구현(원가 채널) | sA §A1, 부채 A-01·A-02 |
| S3-3 ⑤ | 검증 매핑 보강: §20 P3 그룹 **A·B·G·H·I·K + J**(E 해당 없음 — 비용 코어 S3-4), GC-A22~A31·F5·F6·G4·H7(14건), PR 15개·마이그레이션 7건(M16~M22) 정본 = `docs/plans/s3-3-plan.md` | E3·E5-2 |
| S3-4(`W:134-139`) ⑥ | **Phase 3 리허설 "실제 수주 1건 QT→채권 관통"의 CI·PL 단계 = 시스템 밖 작성(runbook A-05)** — 채권은 전용 엔드포인트로 발생(S4-2 전) | ADR-0096·0088 |
| S4-2(`W:150-155`) ⑦ | INSPECTED 엣지 개방 PR이 ① `CI_ISSUABLE_SHIPMENT_STATES` 실발효 확인 ② runbook 외부 작성 문장 삭제·운영 관통 1회(A-05) ③ **채권 전용 엔드포인트 폐쇄·CI 발행 단일 경로 전환**(가정 3) ④ CLOSED 선적 재발행(A-04) 재판정 | ADR-0088·0096 |
| 골든 케이스 매핑표(`W:224-241`) | S3-3 행: A22~A31·F5·F6·G4·H7 | E5-2 |
| 변경 이력(`W:247`) | v1.7 항목(자율 확정, ADR-0088~0098, 세션 분할·순서 변경 없음) | — |

**근거**: WBS '버전 규칙'(사유와 함께 판번+ADR), v1.6 선례(`W:249-253`).

**대안**: WBS 무변경·PROGRESS 부채만 — 기각(WBS는 착수 시 범위 정본 — S4-2가 채권 경로 전환을 자기 범위로 인지하지 못함, S3-4 리허설이 CI 단계에서 막힘).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(문서).

### E5-2. GC v1.6 — S3-3 배정 0건 해소

**결정 — 필요하다.** 현행 GC v1.5에 S3-3 대응 케이스가 0건이고 매핑표에도 행이 없다(`W:224-241` 실측 — 마지막 행 S3-2 `A14~A21·F4·G3`). v1.1~v1.5 선례는 매번 "배정 0건 → 판번 상향"이었다(`GC` 변경 이력). GC 절 분류는 S3-2 적대 R-12 선례대로(C = 인증·규제라 서류·채권은 **A 연번**, 동시성 F, 원가 G, 자동화 경계 H) 기존 ID 뒤에 붙인다(GC-A21·F4·G3·H6이 현재 끝 — `GC:125`·`:229`·`:244`·`:272`).

| GC | 내용(경계 값은 sA §A21·sB §B21·sC §C15가 정본) | PR |
|---|---|---|
| **GC-A22** | CI↔PL 라인 수량 불일치 → 422, 행 0·번호 소비 0 (DoD) | 5a |
| **GC-A23** | G.W.<N.W. 거부(서비스 422 + DB CHECK 23514)·**G.W.=N.W. 통과** | 5a |
| **GC-A24** | 무상(금액 0) 서류 생성 — 무상 QT/PI 렌더 'NO COMMERCIAL VALUE'·무상 CI 발행(은행 블록 없음) (DoD) | 3b·5a |
| **GC-A25** | 검수 미완료 선적 CI·PL 생성 차단 — 실제 경로 RELEASE_ORDERED 선적 409·술어 확장 변이 kill (§20 B) | 5a |
| **GC-A26** | 채권 일부입금 전환 UNPAID→PARTIALLY_PAID→PAID→역기록 PARTIALLY_PAID·초과 422 (WBS 검증 A) | 2a |
| **GC-A27** | aging 구간 경계 8점·만기 미상 = `DUE_UNKNOWN`(미도래 아님)·통화 간 합산 0 (DoD) | 2a |
| **GC-A28** | **노출 공백 0·이중 0** — TT_DEFERRED 채권 발생 전후 노출 정확히 불변·TT_ADVANCE 충당·KRW 묶음 환산 동일·3 입구 provider ≠ 기본 (DoD) | 2b |
| **GC-A29** | SO COMPLETED·short-close — 전량 채권화 → COMPLETED / short-close 사유 필수·채권 없는 선적 존재 409 / COMPLETED 후 새 선적·채권 취소 409 | 2b |
| **GC-A30** | 대금만기 배선 — INVOICE_DATE 앵커 = 채권 `invoice_on`(없으면 UNKNOWN 유지) / L/C 배선 API 경로(제시기한 MIN·대금만기) / tolerance 상한 경계 포함·초과 422 | 2a·4a |
| **GC-A31** | 대금만기·제시기한 알림 — 미수 0·제시 기록 = 충족(알림 0)·도과 1·실적 다 들어간 선적도 후보(회귀)·OPENING 이동처 존재 | 6 |
| **GC-F5** | 노출 구성 변경 경합 — 여신 평가 중간 Barrier에 채권 발생 → 평가 노출 ∈ {전, 후}(잠금 제거 변이에서 실패 확인) | 2b |
| **GC-F6** | 같은 선적 채권 발생 실제 동시 2건 → 1 성공·1 409, 500·40P01 0 | 2a |
| **GC-G4** | 서류 원가 채널 0 — 렌더 컨텍스트·발행 스냅샷·첨부(PO 소유 미개방·수입선적 첨부·수입선적 서류 422)에 원가 키 0 | 3a·3b·5a |
| **GC-H7** | 청구 기록·서류 자동 생성 0 — 채권·CI·S/I는 사람 1클릭에서만, COMPLETED는 그 TX 안에서만, 발행이 대외 발송 이벤트 0 | 2b·5a |

- 각 케이스는 pytest `golden` 마커(케이스마다 1↑)와 docstring 케이스 번호를 단다. **대사 기준 = S3-2 종결 `pytest -m golden` 85건 + 14건 → 99건 이상**(PR-7). PR마다 자기 배정 케이스의 마커를 같은 PR에서 단다(S3-1 PR-16 '조용한 누락' 재발 방지 — `GC` v1.4 부기).
- 추가만 하는 변경(삭제 금지·DEPRECATED 규칙).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(DEPRECATED 표기로 철회, 삭제 불가).

---

## E6. 검증 배치 — 그룹 A~K·CI 3샤드·변이 점검

### E6-1. §20 그룹 × PR 배치

P3 해당 그룹은 A·B·E·G·H·I·K(S3-1 부기 `D:439` ①)이고 J는 전 Phase 공통이다. ◎ = 그 PR의 주 검증, ○ = 해당 케이스 있음.

| 그룹(`D:443-453`) | 1b | 2a | 2b | 3a | 3b | 4a | 5a | 5b | 6 | 화면 PR | 비고 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A 전표·정합 | — | ◎ 일부입금·aging·역순 | ◎ 공백/이중·COMPLETED | ○ as-of | — | ○ 배선 | ○ 재발행·역순 | ○ | — | e2e | WBS 검증 A = 2a |
| B 서류(`D:444`) | — | ○ 채권 CSV 한글 | — | — | ◎ 한글·장문·무상 렌더 | — | ◎ 교차·G.W.·무상·검수 차단 | ◎ S/I | — | 발행 대화상자 | **S3-3이 B 그룹 첫 실케이스**(현재 47 — `docs/testing.md`). B/L draft·DGD·신고문안은 S3-3 밖 |
| E 비용·소싱(`D:447`) | — | — | — | — | — | — | — | — | — | — | **해당 없음**(비용 코어 S3-4). PL CBM은 서류 값이지 §20 E 'CBM 경계값·최소요금'(운임) 아님 |
| G AI·보안(`D:449`) | — | — | — | ◎ 첨부 원가 채널 | ◎ 해시 멱등·주입 | — | ○ 스냅샷 원가 0 | — | — | — | S3-1 해석 "G = 파일 해시 멱등·원가 마스킹" 승계 |
| H 운영(`D:450`) | — | ○ 이관 반영 | — | — | ○ 복원 리허설 FILE | ◎ 플래그 OFF | ○ 검산 편입 | — | ◎ dedup·에스컬레이션·재확인 | — | |
| I 자동화(`D:451`) | — | — | ◎ 자동 엣지 3·4금 | — | ○ 외부 호출 0 | ○ 판정 필드 0 | ◎ 자동 발행 0 | — | ◎ 잡 14·실패 감지 | — | |
| J 안전 계약(`D:452`) | ◎ 키 길이 | ◎ 동시 채권·멱등·IMMUTABLE | ◎ 경합(평가 Barrier) | ○ IMMUTABLE | ○ 경쟁 렌더 | ○ version | ◎ 동시 발행·롤백 | ○ | ○ 재실행 멱등 | 더블클릭 표시 | |
| K 보안·품질(`D:453`) | ○ TODAY 커버리지 | ◎ authz·Page·IDOR·번역표 | ○ 3 입구 | ◎ 403 순서·임포트 방향 | ◎ 순수성·폰트·허용 임포트 | ◎ **L/C MIN·tolerance API** | ◎ 총수·3자 대사 | ○ | ◎ 출구 계약 | vitest 계약 | WBS 검증 K 해석 = 4a |

### E6-2. CI 3샤드(ADR-0073) 적용

**결정**
- 신규 시험 파일은 미등재 평균값으로 자동 배정되므로 샤드 설정 변경 0, 완전성은 `backend-coverage` 독립 수집 대조가 매 실행 확인한다(`docs/testing.md` CI 표).
- **durations 갱신 3회**: 1b(Q-15 해소)·2b 병합 후·5a 병합 후(대형 동시성·속성 시험 파일이 평균값으로 한 샤드에 몰리는 것 방지 — 누락 위험은 없고 시간만의 문제). 샤드 소요가 timeout 40분(`.github/workflows/ci.yml:169`)의 80%(32분)를 넘으면 `matrix.shard`에 4를 더한다(번호만 추가 — ADR-0073 규약, 부채 E-02).
- `concurrency` 마커 시험은 파일 단위 샤드 안에서 직렬 → 실제 동시성 의미 유지. 2b의 속성 시험(무작위 사건열)은 **시드 고정 + 사건 수 상한**(실행 시간 예산 30초)으로 둔다 — CI 비결정성 금지.
- 커버리지 94% 게이트 그대로. `doc_render`·`outstanding`·검증 카탈로그(순수 함수)는 분기 100% 목표(게이트는 기존 임계).
- 3b의 신규 의존성은 CI `pip install` 시간을 늘린다 — 첫 CI에서 `backend-checks`(15분)·샤드(40분) 여유를 실측 기록.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

### E6-3. 변이 점검(PR별 최소 목록)

**결정** — S3-2 방식 그대로: 워크트리 사본에서 `-x` 없이 변이마다 `--junitxml`로 `<failure>`·`<error>` 구분, **전원 kill·error 0**이 통과 조건. 아래는 **최소**이고 PR 작업자가 가산한다.

| PR | 변이(최소) |
|---|---|
| 1b | 키 길이 검사 제거(129자 통과) / 제어문자 허용 / TODAY 지점 1개 누락 |
| 2a | 부분 유니크 `status <> 'CANCELLED'` 제거(동시 2건) / receivables `FOR UPDATE` 제거(CJ-03) / 채권 발생 거래처 잠금 제거 / ChildLink 행 제거(선적 취소 통과) / aging 경계 `<`↔`<=` / UNKNOWN을 NOT_DUE로 / INVOICE_DATE 없음을 ETD로 대체 / 역기록 분기 전 `lock_chain` 복원 / 이중 입력 창 ±3일 → ±0 / OPENING 유니크 제거 / 미수 clamp 제거(음수 미수) |
| 2b | provider 등록 1입구 누락(GB-27) / `invoiced_by_sales_order` 기본 `{}` 복귀(이중 계산) / 항별 환산(±1 차) / RESERVED에 COMPLETED 복귀 / 완결 판정 (나) 조건 제거 / short-close 채권 없는 선적 허용 / 채권 발생 TX에서 수렴 호출 누락 / CLOSED_STATUSES에서 COMPLETED 제거 / 평가 Barrier 시험의 거래처 잠금 제거(GC-F5 kill 필수) |
| 3a | as-of 대체(가장 이른 판) / 미래 `effective_from` 허용 / PO owner 허용 / 수입선적 첨부 허용 / 403 전 404(순서 반전) / 레터헤드 UPDATE 권한 부여 |
| 3b | `doc_render`에 `now()` 주입 / 폰트 digest 상수 변경 / 수식 이스케이프 제거(`=HYPERLINK`) / renditions 유일 키 제거(경쟁 2건) / 생성물 삭제 잠금 제거 / DRAFT QT 렌더 허용 / 원가 키를 렌더 컨텍스트에 추가 |
| 4a | `presentation_deadline` 호출을 `max`로 / tolerance 상한 `<=`→`<` / 플래그 OFF가 기존 기한까지 숨김 / lc 없음을 0일로 대체 / 체크리스트에 '적합' 필드 / 개정 시 이전 행 SUPERSEDED 누락 |
| 5a | 게이트 술어에 RELEASE_ORDERED 추가(GC-A25 kill) / CI↔PL 교차 검사 제거 / G.W.≥N.W. 서비스 검사 제거(DB CHECK만 남아 23514 → 500 검출) / 당사자 version+1 제거(STALE 미검출) / 재발행 2TX 분리 / 기존 채권 있으면 새로 생성 / 무상 행 은행 블록 강제 / 자동 렌더를 TX 안으로 |
| 5b | TO ORDER 술어 기본 True / 포워더 필수 제거 / FOB→COLLECT 도출 반전 |
| 6 | 후보 OR 확장 제거(CH-05 kill) / 미수 0 판정을 '입금 1건↑'로 / 발송 직전 재확인 제거 / 수신자를 선적 담당으로 / dedup 키에서 기일 제거 / 이동 표에서 `receivables` 삭제(K 출구 kill) / 스캔이 receivables 쓰기 함수 임포트 |

- **레지스트리 갱신 시험의 공회전 검출**도 변이로 한다(2a·3a·5a): 새 항목을 지웠을 때 갱신한 아키텍처 시험이 실패해야 한다(예: `GOVERNED_PREFIXES`에서 `/api/v1/receivables` 제거, `_NEVER_SEEDED`에서 `receivables` 제거, LOCK_ORDER에서 `receivables` 제거, `CHILD_LINKS` CI 행 제거).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## E7. 적대 검토 배치

**결정**
- 모든 코드 PR: 자기 적대 검증 **2렌즈**(정확성·계약 위반 / 보안·동시성), 검증자는 테스트를 실행하지 않고 반증만 한다. med 이상은 독립 반증을 거치고, 반증에서 낮아져도 실재 결함이면 반영한다(S3-1 PR-14a 2차 선례).
- **2b·5a는 3렌즈 + 2차 적대 검토**: 2b(+ 문서 정합 렌즈 — DESIGN §7.10 부기·ADR-0090·0091·WBS `W:132` 문면과 구현 일치, 특히 "같은 PR" 결속), 5a(+ 커널 3자 대사·되돌리기 불가 렌즈 — DocKind 저장값).
- **3b는 3렌즈**: 정확성 / 보안(렌더 주입·파일 IO·의존성 공급망 — 버전 고정·해시) / 결정성·이식성(폰트·플랫폼 차이로 시험이 깨지는지).
- 화면 PR: 2렌즈(한국어 UI 규칙 `break-keep`·`nowrap`·숫자 가운데 정렬 + 서버 판정 재구현 0).
- 적대 검토 판정 후보도 오너 지시로 엄격 쪽 자율 확정하고 PROGRESS에 표기한다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## E8. §22 11렌즈 — PR별 완료 체크 배치

| 렌즈(`D:471-481`) | 주 체크 PR | 근거 |
|---|---|---|
| 1 기능 | 2a(aging·검증 A)·2b(공백/이중 0)·5a(교차·무상·검수 차단) | WBS DoD 4항·검증 2항이 PR에 1:1(E3-0) |
| 2 데이터 | 2a·2b·3a·3b·4a·5a·5b | 마이그레이션 7건·불변 표·열 GRANT·스냅샷·채번 CI·SI |
| 3 트랜잭션 | 2a·2b·5a | sC T5·T8~T14 1TX·렌더/파일 IO TX 밖 |
| 4 동시성·멱등 | 1b(키)·2a·2b(◎)·5a | GC-F5·F6·LOCK_ORDER 계측(CJ-09·10) |
| 5 보안·권한 | 3a·3b·4a·5a | 권한 매트릭스·원가 채널 0·렌더 주입·계좌 마스킹 |
| 6 시간 | 1b·2a(◎ aging KST)·4a·6 | 한 요청 한 '오늘'·증빙일 미래 불가·D-N 경계 |
| 7 성능 | 2a(aging 쿼리 수 고정)·3b(렌더 시간·파일 크기)·6(후보 확장 질의) | 목록 50·N+1 0 |
| 8 테스트 | 전 PR | E6 그룹·변이·GC 14건 |
| 9 운영 | 1b·3b·4a·6·7 | durations·의존성 설치·플래그 클릭 안내·스캔·runbook 운영 개시 |
| 10 문서 | 1·7 | DESIGN 부기·ADR 0088~0098·WBS v1.7·GC v1.6 |
| 11 워크스루 | 2c·3c·4b·5c(화면 관통)·7(입구~출구 1회) | 3층 증거(ADR-0086) |

미통과분은 조용히 넘기지 않고 PR 본문과 PROGRESS에 부채로 적는다(`D:483`).

---

## E9. 되돌리기 비용 — 전체 요약

| 단위 | 비용 | 방법 |
|---|---|---|
| PR-1(문서) | 낮음 | revert. 병합 후 ADR 번호 고정, 번복은 새 ADR "대체" |
| PR-1b | 낮음 | 검사 revert(마이그 0) |
| PR-2a(M16) | 낮음~중간 | revert + M16 downgrade(채권·채권 입금 행 0일 때 — 있으면 RAISE) |
| PR-2b(M17) | **중간 / 공백 실재 시 높음** | revert + M17 downgrade(COMPLETED SO가 있으면 RAISE — 상태이력 CHECK). 과소 노출 위 확정은 소급 불가(ADR-0076 근거) |
| PR-3a(M18) | 중간 | 불변 판 표·owner CHECK 재정의 — 첨부 행 생기면 RAISE |
| PR-3b(M19) | 낮음~중간 | 의존성 제거 쉬움, renditions 정본 파일은 보존(삭제 0) |
| PR-4a(M20) | 낮음 | 어댑터·조립 1곳, lc 행 있으면 RAISE |
| PR-5a(M21) | **운영 데이터 전 중간 / 후 높음** | DocKind 저장값. 단 게이트로 S4-2까지 운영 CI 행 0 기대 → 되돌리기 창이 S4-2까지 열림 |
| PR-5b(M22)·PR-6 | 낮음 | 마이그 1 / 잡 무변경(후보 상수) |
| 화면 PR | 낮음 | 화면 revert(서버가 정본) |
| 순서(DAG) | 병합 전 낮음 / 후 중간 | 리비전 순서 변경 = downgrade 사슬 역행 |

- **프로덕션 실데이터 투입 여부를 PR-1 착수 시 확인**한다(S3-2 PROGRESS "실행 검증 못 한 것: 실데이터" — 미확인 상태 승계). 실데이터가 있으면 2b·5a는 운영 데이터 투입 전 병합을 권장 일정으로 둔다.

---

## E10. 예상 위험

| # | 위험 | 영향 | 대응(배치) |
|---|---|---|---|
| R1 | **2b 평가 함수 재배치가 S3-1 여신 시험 기대값을 바꾼다**(`code:modules/credit/evaluation.py` 루프·provider 호출 순서) | 회귀·DoD 불성립 | 2b 커밋 ①을 "기본 provider에서 기대값 무변경"만으로 단독 green 확인(E3-4), 3렌즈+2차 |
| R2 | **5a DocKind 편입 되돌리기 불가** | 운영 후 분리 불가 | 커밋 ① 묶음(R-23)·3렌즈+2차·게이트로 운영 행 0 구간 확보 |
| R3 | **신규 외부 의존성**(`reportlab`·`openpyxl` — `requirements.txt` 현재 없음 실측, 시험용 PDF 추출 도구, 동봉 폰트) — 라이선스·이미지 크기(`python:3.13-slim` `backend/Dockerfile:11`)·휠 가용성·mypy 무타입·CI 설치 시간 | 빌드 실패·공급망 | 3b 커밋 ①을 의존성 전용으로(== 고정·폰트 sha256·OFL 확인·CI 실측), 3b 3렌즈(공급망) |
| R4 | **부록 간 충돌 미해소 상태로 구현 착수**(렌더 저장 A↔C·CI 표 이름·발생 경로·앵커 원천) | 재작업 | 통합이 PR-1 **전에** E-Q1~E-Q4 확정(가정 0-1~0-4는 배치만 고정) |
| R5 | **CI 운영 발행 0(S4-2까지)에 대한 사용자 기대 불일치** | 운영 혼선 | runbook A-05 외부 작성 안내·5c 409 안내 문구·워크스루 출구에 409 확인·WBS v1.7 ①⑥⑦ |
| R6 | **총수 핀·LOCK_ORDER·REGISTRY를 여러 PR이 순차 수정**(2b·5a 총수, 2a·4a·5a LOCK_ORDER) | 재배치 충돌 | 직렬 병합·재배치 후 전체 재검증, 핀 수치는 E3-8 표 대사 |
| R7 | **테스트 시간 증가**(S3-2 PR-6 시점 로컬 약 50분, 샤드 timeout 40분) | CI timeout | durations 3회 갱신·32분 초과 시 샤드 4(E6-2)·속성 시험 예산 |
| R8 | **2a~2b 구간 채권이 노출에 미반영** | 노출 과대(안전측) | 화면 2c를 2b 뒤로 → 운영 생성 경로 0, K 시험으로 '감소 0' 고정 |
| R9 | **PDF 결정성·폰트 렌더 플랫폼 차이로 시험 불안정** | 거짓 실패 | 바이트 비교 대신 텍스트 추출 대사·XLSX는 셀 값 대사·폰트 digest 고정 |
| R10 | **sD(화면 부록) 부재** | b PR 범위 미확정 | 화면 PR 범위 = sA·sB·sC "화면 소관" 합집합(가정 6), 통합이 sD로 재대사 |
| R11 | **후보 질의 확장(6)의 질의 비용** — 동결 수출 선적 전체 OR 절 | 06:40 잡 시간 | 부분 인덱스 확인·실행 시간 PROGRESS 기록(렌즈 7) |
| R12 | **S4-2가 채권 경로 전환을 놓침** | 두 경로 공존 | WBS v1.7 S4-2 주석 ⑦·ADR-0088 되돌리기 란에 명기 |

---

## E11. 착수 시 실행 확인 필요 항목 (정적 독해 한계)

1. (**실측 완료**) `alembic heads` = `281da4794717` 단일 — PR-2a 첫 커밋에서 재확인(그 사이 병합 없으면 동일).
2. SO 상태이력 `reason_required` CHECK가 코드 상수 파생인지와 실제 제약명 — PR-2b 첫 커밋(M17 수기 재정의 근거).
3. DocKind 값을 담는 DB CHECK 전수(상태이력·outbox aggregate·doc_number_seq 등) — PR-5a 첫 커밋(S3-2 PR-3a E10 ② 선례).
4. `documents.owner_type` CHECK 실명·`value_in` 파생 여부(`code:modules/documents/models.py:143`) — PR-3a.
5. M16~M22 식별자 63자(예: `uq_trade_document_renditions_source_kind_format_language`, `ck_commercial_invoices_bank_required_for_tt`) — 각 PR.
6. `reportlab`·`openpyxl` 최신 고정 버전의 Python 3.13 휠·라이선스·이미지 증가분, 폰트 파일 크기 — PR-3b 커밋 ①.
7. 프로덕션 실데이터 유무(E9 전제) — PR-1 착수.
8. `.shard_durations.json` 재갱신 후 샤드 3개 소요 편차 — PR-1b 첫 CI.
9. `test_no_auto_confirm_code_path_exists.py` `_SCAN_ALLOWED_CALLS`(`:1051-1066` — sC 인용) 가산 목록이 PR-6에서만 필요한지(2a가 미수 정의를 `outstanding`에 두면 6에서만) — PR-2a.

---

## 멈춰서 보고할 항목 (판정 후보 → 자율 확정)

1. **E-Q1 렌더 산출물 저장(sA §A13) ↔ 비저장(sC §C1)** — **사양 충돌**. 이 부록은 배치만 A안으로 가정했다(가정 2 — '보낸 그 파일' 보존·백업 편입·감사 증거가 더 엄격). 통합이 확정하고 ADR-0093 문면을 맞춘다. C안이면 M19 축소·3b 범위 축소(순서 불변).
2. **E-Q2 CI 영속 모델 이름**(`commercial_invoices` DocKind vs `trade_documents` 가칭) — 통합이 sC 표기를 sA로 정정(배치 무관).
3. **E-Q3 채권 발생 경로** — 자율 확정(배치): S3-3 = 전용 엔드포인트(2a), CI 발행 TX 합류는 5a가 "기존 채권 재사용"으로 배선, 엔드포인트 폐쇄 = S4-2(WBS v1.7 ⑦). sB §B1 ③ "CI 발행 전표가 생기면 닫는다"를 "**CI 발행이 운영에서 가능해지면** 닫는다"로 읽은 것이다 — sA §A2 게이트 아래에서 문자 그대로 읽으면 S3-3 채권 운영 경로가 0이 되어 WBS DoD "aging"의 운영 의미가 사라진다.
4. **E-Q4 INVOICE_DATE 앵커 배선 PR** — 자율 확정: 2a(sB §B19 원안 PR-B2에서 당김 — aging DoD와 같은 PR).
5. **E-Q5 2a·2b 분리** — 자율 확정: 분리 + 화면 2c를 2b 뒤 + "2a 구간 노출 감소 0" K 시험(sB "합쳐도 무방"보다 리뷰 면적 작고 구간 위험은 과대 노출뿐).
6. **E-Q6 채권 축 선행 순서** — 자율 확정: 2(채권) → 3(레터헤드·렌더) → 4(L/C) → 5(CI·S/I). QT·PI 렌더 운영 가치를 우선하려면 3a·3b를 2a 앞으로 당겨도 의존 위반 없음(DAG만 재정렬).
7. **E-Q7 WBS v1.7·GC v1.6 필요** — 확정(E5).
8. **E-Q8 ADR 번호 0088~0098(11건)** — 통합이 바꾸면 PR-1에서만.
9. **E-Q9 S3-4 Phase 3 리허설의 CI 단계** — 게이트로 시스템 CI 발행이 불가하므로 외부 작성으로 관통(WBS v1.7 ⑥). 리허설 '관통' 문면과의 차이를 S3-4 착수 세션이 재판정.
10. **E-Q10 sD 부재** — 화면 PR 범위 가정(가정 6), 통합이 재대사.

## 부채 등재 후보(이 부록)

| ID | 내용 | 소유 | 트리거 |
|---|---|---|---|
| E-01 | `.shard_durations.json` 재갱신(2b·5a 후 — Q-15 계속) | CI | 샤드 소요 편차 2배 초과 또는 5a 병합 |
| E-02 | 샤드 3 → 4 | CI | 샤드 1개 소요 32분 초과 |
| E-03 | renditions CHECK 단계 확장의 정의문 시험 3벌 유지비 | 서류 | 서류 종류 추가 시(DGD — S4-4) |
| E-04 | 시험용 PDF 추출 도구 dev 의존성(3b) | 서류·CI | 도구 판 갱신·취약점 공지 |
| E-05 | 화면 PR 범위 = sD 확정 전 가정 | 계획 | 통합 문서 확정 |
| E-06 | S4-2의 채권 경로 전환(전용 엔드포인트 폐쇄) 미이행 위험 | S4-2 | INSPECTED 엣지 개방 PR |

**실행 검증 못 했음.** 이 부록의 PR·리비전·테스트·GC ID는 설계이며 아직 존재하지 않는다. 실측은 `alembic heads`·ADR 최대 번호·durations 파일 계수·requirements 의존성 유무뿐이고, 인용한 그 밖의 코드 줄은 `2092406` 정적 독해 값이다 — 각 PR 첫 커밋에서 실측해 PROGRESS에 기록한다.
