# S3-2 계획서 부록 E — PR 분할·마이그레이션·검증 배치


> **통합 우선순위(2026-10-04)**: 이 부록과 `design-integrated.md`가 충돌하면 통합 문서가 이긴다. 통합 검토가 모순 해소에 필요한 최소 문면만 고쳤고, 고친 자리는 "[통합 X-nn]"·"[통합 N-nn]"으로 표시했다(목록: 통합 §1.6).
> **적대 검토 정정(2026-10-04)**: 통합 문서 §9(R-01~R-30)가 이 부록과 통합 §0~§8보다 우선한다. 이 부록에서 고친 자리는 "[적대 R-nn]"으로 표시했다(목록: 통합 §9 R-27).
- 기준: main `a4d91c0`(S3-1 종결). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-2 행(`W:112-116`)을 따른다. 진행·부채는 PROGRESS.md '## 현재'(`P:633`)와 'S3-1 종결 부채 최종 목록'(`P:32`)이다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `code:경로:줄` = `backend/app/` 아래, `test:경로:줄` = `backend/tests/` 아래, `sA`~`sD` = 같은 디렉터리의 `design-A.md`(선적 데이터 모델)·`design-B.md`(기일 엔진·마일스톤·휴일)·`design-C.md`(동시성·권한·감사·잡)·`design-D.md`(화면·API). S3-1 선례는 `docs/plans/s3-1-plan.md §4`(`P1:59-81`)와 `docs/plans/s3-1/design-integrated.md §2.11·§3`(`DI:318-364`)이다.
- 판정 방식: 오너 지시(2026-09-29, CLAUDE.md "웹 세션 판정 절차 생략")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. PROGRESS 등재 시 "자율 확정"으로 표기한다. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 여부 / 되돌리기 비용** 순서다.
- **실행 검증 못 했음.** 이 부록은 정적 독해로 작성했다. 마이그레이션 리비전 해시·테스트 수·CI 소요 시간은 각 PR 첫 커밋과 첫 CI에서 실측해 PROGRESS에 기록한다(P-60 선례).

---

## 0. 경계 — 이 부록이 정하는 것과 넘기는 것

| 이 부록(E)이 정한다 | 다른 부록으로 넘긴다(경계만 적음) |
|---|---|
| PR 분할·순서·의존, PR별 DoD·§20 그룹·GC 배치 | 테이블·컬럼·CHECK·상태 머신·레지스트리 내용 → **sA** |
| 마이그레이션 번호(M13~)·DAG·`down_revision` 사슬·PR 귀속 | 마일스톤·산식·휴일·스캔 대상과 문턱 → **sB** |
| ADR 번호 배정(0074~)과 기존 ADR 부기 목록 | 트랜잭션·잠금·멱등·권한 행·잡 시각 → **sC** |
| WBS v1.6·GC v1.5 갱신 필요 여부와 문면 | 엔드포인트·화면·라우트 → **sD** |
| CI 3샤드·변이 점검·적대 검토·워크스루의 PR별 적용 | 테스트 케이스 **내용**(sA §A16·sB §B20·sC §C14·sD 테스트 배분) — 이 부록은 **어느 PR에 넣는지**만 정한다 |
| 되돌리기 비용(PR 단위·전체) | |

이 부록이 다른 부록의 표현 차이를 **PR 배치 목적으로** 고정하는 가정 3건(통합이 다르게 정하면 해당 PR 배치만 바뀐다):
1. **수리일 입력처** = 통관 기록 `accepted_on`이 유일 입력처. **[통합 X-02]** 마일스톤 `CUSTOMS_CLEARED` 실적으로 **복사하지 않고 읽기 시 파생**한다(원안의 동TX 복사 철회). **통관 기록 쓰기 API는 마일스톤 PR(PR-4a)에 그대로 둔다**(E3-4 — 파생 조립과 적재기한 배선이 같은 PR이고 3a 면적을 줄인다).
2. **마일스톤 쓰기 메서드** = POST(sD D2-2 확정).
3. **선적 기일 알림 `entity_type`** = 소유 전표(`shipments`/`purchase_orders`)(sD §0 가정 6).

---

## E1. 분할 원칙 — S3-1 선례 승계(백엔드 a / 프런트 b 쌍, 직렬 병합)

**결정**
- PR은 **수직 슬라이스**로 자른다. 각 병합 시점에 앱이 동작하고 전체 테스트가 통과해야 한다(`DI:341` 공통 규율 (1)).
- 화면이 딸린 슬라이스는 **백엔드 a / 프런트 b 쌍**으로 나눈다. S3-1 실행에서 PR-5~PR-15가 전부 a/b로 나뉘었다(PROGRESS 태스크 #35~#56). b는 a 병합 후 최신 main에서 시작한다.
- 병합은 **직렬**이다. alembic head가 하나여야 하므로 마이그레이션을 가진 PR은 E2 DAG 순서대로만 병합한다. 병렬 개발은 허용하지만 병합 직전에 `down_revision`을 최신 main head로 재정렬한다(`DI:341`).
- **같은 커밋에 묶어야 하는 변경**(분리하면 CI가 깨지거나 앱 임포트가 실패하는 것)은 PR을 가르지 않는다.
  - ~~SO IN_SHIPMENT 엣지 추가 ↔ `trade_chain/router.py:95-96,109-110` Literal 갱신(임포트 시 assert — code-chain R7).~~ **[적대 R-23]** SO Literal 무변경(자동 엣지는 `public_transition_targets` 밖). 대신 **선적 DocKind·dict 11종·상태 기계(사람 엣지 3)·M14 모델·FIELD_POLICY·사슬 레지스트리**가 한 커밋 묶음이다.
  - RESERVED에서 IN_SHIPMENT 제거 ↔ 오더 보드 매핑 1줄(`test:architecture/test_order_board_contract.py:74-87` "모든 SO 상태 = 매핑 ∪ {CANCELLED} ∪ RESERVED").
  - 체인 테이블을 가리키는 FK 생성 ↔ `CHILD_LINKS`·`LINE_CONSUMERS`·`NON_CHILD_FK_ALLOWLIST` 등재(`test:architecture/test_doc_chain_contract.py:55-64`).
  - 잡 추가 ↔ 4금 집합·총수 핀(`test:architecture/test_scheduler_registry.py:143-174`).
- **PR별 공통 절차**(`P1:81` 승계): 작은 커밋 → 마이그레이션 왕복·`alembic check`·전체 pytest·vitest·ruff·mypy·typecheck·build → 변이 점검(E6) → 실기동 관통(렌즈 11) → 자기 적대 검증(E7) → PR·CI 전 체크런(`ci-ok` 포함) green·mergeable clean → API squash 병합(CLAUDE.md, ADR-0011 부기 2026-09-29) → 브랜치를 최신 main에서 재시작.
- **완료 보고 요건**: 릴레이 평문 본문에 DDL 전문(마이그레이션 PR)·12자리 커밋 해시를 직접 포함한다(CLAUDE.md 2026-08-12 판정, `DI:341` (6)).

**근거**: CLAUDE.md "작은 단위 구현 → 즉시 실행·테스트", "체크포인트 커밋을 작게 자주", 병합 게이트 / `D:374`(드라이런 4종) / `DI:341,364`.

**대안**
- (a) 레이어 단위 분할(모델 전부 → 서비스 전부 → 화면 전부). 기각한다. 중간 병합 시점에 쓰기 경로 없는 표·소비자 없는 레지스트리가 생겨 "각 병합이 동작" 원칙과 4요소(API+화면+권한+테스트)를 깬다.
- (b) S3-2 전체를 단일 PR. 기각한다. CLAUDE.md "큰 덩어리 일괄 생성 금지".

**자율 확정**: 확정. **되돌리기 비용**: 낮음(병합 전에는 분할 재조정이 문서 수정뿐).

---

## E2. 마이그레이션 번호와 DAG

**결정 — 번호는 S3-1에서 이어 M13부터 쓴다.**

| 번호 | 내용(테이블 형태의 정본은 괄호의 부록) | 논리 의존 | `down_revision`(병합 순 사슬) | PR |
|---|---|---|---|---|
| **M13** | `holiday_calendar_years`·`holidays` (sB §B11) | 없음(전표 무관, S3_PLATFORM) | `f2cb6020b2bb`(현 단일 head — `migrations/versions/20261003_0633_f2cb6020b2bb_s31_board_saved_filters.py:31-32`) | PR-2a |
| **M14** | `shipments`·`shipment_lines`·`shipment_parties`·`shipment_status_log`(+REVOKE)·`customs_records` (sA §A14 — 1건 5표) | SO·PO·partners·markets 기존 표 | M13 | PR-3a |
| **M15** | `milestones`·`milestone_changes`(+REVOKE)·`milestone_change_notices`(+REVOKE)·`item_profile_milestone_types` + **기존 `comm_logs` 주제 CHECK 재정의**(SHIPMENT 추가) (sB §B2·B9·B16) | M14(`shipment_id` FK), 기존 `purchase_orders`(`po_id` FK)·`comm_logs`·`item_profiles` | M14 | PR-4a |

- **M13을 맨 앞에 둔다.** 휴일은 논리 의존이 없어 어디든 갈 수 있지만, 마일스톤 PR(PR-4a)이 DoD② "ETA 현지 연휴 → 경고"를 **처음부터 완결된 3값(HOLIDAY/CLEAR/UNVERIFIED)**으로 내려면 휴일 표가 먼저 있어야 한다. 반대로 두면 PR-4a는 휴일 표 없이 전 건 UNVERIFIED만 내는 반쪽 상태로 병합된다.
- **S3-2의 다른 PR(PR-5a·PR-6·PR-7·PR-8)은 마이그레이션 0건**이다.
  - 수입선적(PR-5a): 열은 M14에 이미 있다(sA §A2·A3). 배정 가능량·입고예정은 계산값이다(sA §A4, sB §B17).
  - 잡(PR-6): `scheduled_jobs`는 앱 경로 `register_jobs`로 등록한다(`code:modules/platform/scheduler.py:299-325`, 시드 금지 `D:322`).
  - 기존 SO 표: SO 상태 CHECK에 IN_SHIPMENT가 이미 있고 `confirmed_at_consistent`가 수용한다(`code:modules/sales_orders/models.py:118-124`, sA §A14). SO 상태이력 CHECK는 `STATUSES` 파생이라 재생성이 불필요하다(`code:modules/trade_docs/models.py:60-82`). **PR-3a 첫 커밋에서 실측**한다.
- **M15의 `comm_logs` CHECK 재정의는 기존 표 CHECK 변경**이다. `alembic check`가 CHECK를 감지하지 못하므로(함정 ①·⑪) `op.drop_constraint`/`op.create_check_constraint`를 수기로 쓰고, downgrade에서 원래 정의(`("CERTIFICATION",)`, `code:modules/collaboration/models.py:55`)로 복원하며, **정의문 테스트**로 양방향을 고정한다. downgrade 시 SHIPMENT 행이 있으면 실패해야 한다(데이터 소실 금지 — 조용한 삭제 대신 오류).
- **리비전 파일 공통 규율**(`DI:362`·`DI:574` 승계): 상단 체크리스트 헤더(rename 없음·CHECK 수기 `op.f()` 이름·부분 유니크 `WHERE deleted_at IS NULL`·시드 0·downgrade `drop_table`·REVOKE), 식별자 63자 이내(실측), `upgrade→downgrade→upgrade` 왕복, 드리프트 0, 단일 head(`test:integration/test_migrations.py:46-83`, `D:374`).
- **시드 0**: 휴일 데이터·마일스톤 세트·잡 행은 전부 앱 경로다(함정 ⑩, `D:322`, `test:architecture/test_scheduler_registry.py:52,76-97` 시드 스캔).

**근거**: `DI:318-339`(S3-1 M01~M12 표 형식·직렬 규율), `D:374`, sA §A14 "마일스톤·휴일 부록 마이그레이션과의 순서는 PR 분할에서 정한다".

**대안**
- (a) 번호를 S3-2에서 M01부터 다시 시작. 기각한다. PROGRESS·ADR에서 "M05"가 S3-1인지 S3-2인지 모호해진다. 계획 문서 간 교차 참조가 늘어나는 단계라 전역 연번이 안전하다.
- (b) M14를 선적 4표 / 통관 1표로 쪼갠다. 기각한다. 표 구성은 sA 소관(§A14 "1건으로 5표")이고, 통관 쓰기 경로 개방 시점은 API로 통제할 수 있다(E3-4). 쪼개면 리비전만 늘고 얻는 안전은 없다.
- (c) M15에서 `comm_logs` 확장을 분리한 별도 리비전. 기각한다. 통보 연결 표(`milestone_change_notices.comm_log_id`)와 주제 확장은 같은 동작의 양면이라 한 리비전이 왕복 시험에 유리하다.

**자율 확정**: 확정. **되돌리기 비용**: 병합 전 낮음(번호·순서 재배정은 파일 rename). 병합 후 중간 — 리비전 순서를 바꾸려면 downgrade 사슬을 거슬러 재적용해야 하므로 순서는 사실상 고정이다. 각 리비전은 additive라 개별 되돌리기는 downgrade 1회다(M15는 SHIPMENT 주제 행이 있으면 먼저 정리 필요).

---

## E3. PR 분할 — ~~12 PR~~ **15 PR([적대 R-22] — PR-1b·3c·4c 신설, PR-7 순서 이동)**(백엔드 a / 프런트 b 쌍)

### E3-0. 개요

| PR | 범위 | 마이그 | 프런트 | DoD·검증 배치 | GC v1.5 |
|---|---|---|---|---|---|
| **PR-1** | 문서 전용: 계획서 최종본(`docs/plans/s3-2-plan.md`)+부록 A~E(+통합) 등재·DESIGN 부기 전건·ADR **0074~0087**(0086 → 0087 정정 — [통합 N-02])(E4)+기존 ADR 부기·WBS v1.6·GC v1.5·PROGRESS 등재(자율 확정 표기)·runbook 초안 줄 | — | — | 문서 린트(ADR 5줄·WBS 행·GC 이력) | 등재만 |
| **PR-2a** | **산식 순수 함수**(대금만기 분기·L/C 만기·제시기한 MIN·tolerance·적재의무·`holiday_flag`·현지일 `cutoff_scan_date`) + **holidays 백엔드**(연도 선언·원자 교체 PUT·CSV 미리보기·목록·export) + `open_quantity` kind 필터 선행 + `.shard_durations.json` 갱신 | **M13** | — | **DoD①**(T/T·L/C 분기)·**검증 K**(제시기한 MIN·tolerance)·A(산식)·J(휴일 교체 멱등·1TX)·K(authz 휴일 A 전용·Page·CSV BOM) | ~~C11·C12·C13·C14~~ **[적대 R-12] A16·A17·A18·A19**(함수 층) |
| **PR-2b** | 휴일 캘린더 화면(`/holidays`)·CSV 업로드 2단·현지 병기 함수(`lib/datetime.ts`)·셸 메뉴 "휴일 캘린더" | — | ○ | vitest(UNVERIFIED ≠ 빈 목록, `new Date` 날짜 문자열 금지 소스 계약)·390px | — |
| **PR-3a** | **수출선적 커널**: DocKind `SHIPMENT`(접두어 `SH`)·상태 8값(활성 3엣지)·SO CONFIRMED↔IN_SHIPMENT 자동 수렴·COMPLETED 보류·`CHILD_LINKS`(SO·PO 부모)·`LINE_CONSUMERS`(SO_LINE FULFILL·PO_LINE IN_TRANSIT 등록)·`CONSUMABLE_STATUSES`+IN_SHIPMENT·LOCK_ORDER 개정·SO 참조 생성(preview/생성)·라인·당사자·출고지시·취소·담당 이관 등록·authz 행(L 첫 쓰기)·오더 보드 `SO_IN_SHIPMENT` 상수·~~문서 흐름 SHIPMENT 노드(백엔드)~~ **[적대 R-22] → PR-3c**·가용 "자리" 응답 필드(**[통합 X-26]** 포트 무변경 — `NOT_IMPLEMENTED` 조립 함수)·~~`GET /shipments/export.csv`(**[통합 X-24]**)~~ **[적대 R-22] → PR-3c**·**[적대 R-02] SO 취소 검사 순서**·에러 코드·no_auto_confirm 엔트리·임포트 계층·`known_s3` | **M14** | — | **DoD③**(부분선적 1:N)·**검증 A**(잔량 0·초과 거부·역순 취소)·B(RESERVED 5상태 진입 0)·I(자동 엣지 2개 4금 논증·COMPLETED 0)·J(실제 동시 부분선적·더블클릭·롤백·version·55P03)·K(총수 182·사슬 대사·FIELD_POLICY·authz·Page·원가 키 0) | A14·F4 |
| **PR-3b** | 선적 목록·상세(헤더·라인·당사자·상태이력·문서 흐름 노드·DG 배지·가용재고 미산정 표시)·SO 상세 "선적 만들기" 2단 대화상자·오더 보드 "선적중" 열·SO 상세 선적 잔량·`alert-routes` `shipments`·상태 라벨 단일 표·**SearchSelect 오선택 수정**(PR-16 부채 ⑤, sD D15)·셸 메뉴 "선적" | — | ○ | vitest(보드 5열·문서 흐름 노드·409 칸별 잔량·SearchSelect 빠른 입력 오선택 0)·`detail-layout` 하한 7·e2e A(대화상자 경로 부분선적) | — |
| **PR-4a** | **마일스톤·롤오버·통보·통관**: 마일스톤 계획/실적(저장형 + 파생 3종 계산값 배선)·`milestone_changes`·통보(comm_logs SHIPMENT)·계획 초안 1클릭·`item_profile_milestone_types`·휴일 경고 배선·**통관 기록 API**(**[통합 X-02]** 수리일은 통관 기록에만 저장 — `CUSTOMS_CLEARED` 실적은 읽기 시 파생)·~~OEM 생산 마일스톤(PO 소유 4종)·마일스톤 세트 쓰기 경로 `/item-profiles/{id}/milestone-types`(**[통합 N-01]**)~~ **[적대 R-22] → PR-4c**·**[적대 R-16] `customs_records` 표(M15)·통관 생존 취소 가드**·**[적대 R-01] 실적 가드**·범용 `/comm-logs`의 SHIPMENT 거부(**[통합 N-03]**) | **M15** | — | **DoD②**(ETA 현지 연휴 경고)·A(재계산·파생 덮어쓰기 금지)·H(통보 기록 = 발송 0)·J(롤오버 멱등·이력 IMMUTABLE·soft delete 재유입)·K(부모-자식 404·Page) | ~~C14(배선 층)·C15~~ **[적대 R-12] A19(배선 층)·A20** |
| **PR-4b** | 마일스톤 타임라인(UNKNOWN 사유·"예정 기준"·UNVERIFIED 배지)·실적 입력·롤오버(사유 필수)·통보 기록·통관 기록 대화상자·OEM 생산 일정 섹션(PO 상세) | — | ○ | vitest(파생 행 버튼 0·CUSTOMS_CLEARED 실적 안내·사유 빈칸 제출 불가) | — |
| **PR-5a** | **수입선적**: PO 참조 생성(preview/생성)·배정 가능량 409·PO 원가 비복사·PO 상세 `assignable_quantity`·`expected_receipt`(가장 늦은 ETA 계산값, P-05) | — | — | A(PO `open_quantity` 불변·배정 가능량 초과 409)·J(동시 수입선적 2건)·K(수입 응답 원가 키 0 — 원가 열람 역할 포함)·대역 테스트 `test_purchase_order_lifecycle.py:425-436`을 실 테이블로 교체 | A15 |
| **PR-5b** | PO 상세 "수입선적 만들기"(같은 대화상자 `mode="IMPORT"`)·PO 라인 입고예정 표시 | — | ○ | vitest(금액 칸 0)·e2e A | — |
| **PR-6** | **기일 스캔 잡** `trade-deadline-scan` `daily@06:40`(선적 마일스톤 + QT/PI 만료 임박 D-N, P-03)·**[통합 N-02]** `approval-integrity-check` `daily@05:40`(PR-9a 부채 ①)·CLI 수동 실행·JOB ~~12→**14**~~ **[적대 R-17] 13→14(무결성 잡은 PR-1b로 이동)**·4금 집합·보호 테스트 갱신(sC C11 6곳) | — | — | H(ack 재발송 0·D-3 에스컬레이션·롤오버 새 기일 = 새 알림·담당 이관 즉시 반영·ADMIN 폴백·L/C 플래그 오프 알림 0)·I(스케줄 ~~13~~ **14**·실패 1건 → 잡 FAILED) | ~~C16~~ **A21** |
| **PR-7** | 사용자·역할 화면(`/settings/users`, ADMIN, 백엔드 0 — PR-16 부채 ③, sC C7·sD D14) | — | ○ | vitest(ADMIN 외 메뉴 미노출·마지막 관리자 오류 표시)·K(기존 authz 무변경 확인) | — |
| **PR-8** | **마감**: runbook(운영 개시: 휴일 연도 선언·물류 계정·잡 표 ~~13행~~ **14행**·수기 양식 선적 행·DG 선적 경고·통관 이슈 임시 규칙 안내)·**워크스루(렌즈 11)**·`docs/testing.md` 그룹 수 대사·GC v1.5 `golden` 마커 대사·WBS/GC 확정·PROGRESS S3-2 종결·부채 최종 목록 | — | 잔여 정비 | 워크스루 3층 증거(sD D16)·11렌즈 체크 | 전건 대사 |

**선행 관계**: ~~1 → 2a → 2b → 3a → 3b → 4a → 4b → 5a → 5b → 6 → 7 → 8.~~ **[적대 R-22] 1 → 1b → 2a → 2b → 7 → 3a → 3c → 3b → 4a → 4c → 4b → 5a → 5b → 6 → 8**(15 PR — 통합 §9 R-22).
- 마이그레이션 사슬(M13→M14→M15) 때문에 **2a → 3a → 4a**는 고정이다.
- b는 짝 a 이후 어디든 가능하다(마이그레이션 0). 단 4b는 3b의 상세 화면을 확장하므로 3b 이후다.
- **5a는 4a 이후**다. `expected_receipt`가 마일스톤 ETA를 읽는다(sB §B17).
- **6은 4a 이후**다. 스캔 대상이 마일스톤 행이다. QT/PI D-N만 먼저 내는 분할은 하지 않는다(같은 잡·같은 4금 논증을 두 번 갱신하게 된다).
- **7은 1 이후 어디든** 가능하다(백엔드 0). 8(워크스루)이 물류 계정으로 관통하려면 8보다 앞이어야 한다.

### E3-1. PR-1 — 문서 전용

**결정**
- "계획 세션 → PR-1 이어쓰기" 표준 경로를 쓴다(CLAUDE.md 2026-08-12 S2-3 판정). **착수 블록 A항 세션 확인 가드 필수**: S3-2 계획 맥락(부록 A~E)이 없는 세션이면 작업 없이 정지한다.
- 포함: 계획서 최종본, 부록 등재, DESIGN 부기(아래 목록), ADR **0074~0087**(0086 → 0087 정정) + 기존 ADR 부기(E4), WBS v1.6(E5), GC v1.5(E5), PROGRESS '## 현재' 갱신과 자율 확정 표기·부채 등재.
- DESIGN 부기 대상(각 부록 §부기 목록의 합집합 — 문면은 부록이 정본): §2(LOGISTICS 첫 전표 쓰기), §3 표 맵(customs_records 형태·holiday_calendar_years·milestone_changes·notices·item_profile_milestone_types), §5.4/§7.9(comm_logs SHIPMENT 주제·`/comm-logs` 역할 무변경), §7.1(구분 4종 중 2종 경로 미개방·원천 FK 정확히 하나·수입 원가 비복사), §7.2(선적 코드값·전이표·RESERVED·SO 자동 수렴·COMPLETED 보류·총수 30/152/182), §7.5(마일스톤 ~~10종~~ **9→11종 [적대 R-03]**·파생 계산값·휴일 의미론·롤오버·통보·선적 캘린더 S4-4 재확인), §8.3 부기 ②(kind 필터·IN_TRANSIT·SO FOR UPDATE 선점 해석), §14 [M4] 보강(S3-2 화면 배정), §15 부기(SO 자동 엣지 0 개정·잡 ~~13행~~ **14행**), §17.2 부기 ②(LOCK_ORDER), §17.5(IMMUTABLE 3표), §20 해석 주(검증 K L/C = 순수 함수).
- **코드 변경 0**. 그래서 CI는 기존 테스트 전체 green이면 된다.

**근거**: `P1:63`(S3-1 PR-1 선례), `D:456`(렌즈 10 문서), CLAUDE.md "설계 변경은 DESIGN.md 갱신 + ADR 5줄이 세트".

**대안**: 문서를 각 구현 PR에 흩어 싣는다. 기각한다. 자율 확정 판정이 구현 PR 리뷰에 섞이면 사후 번복 지점을 찾기 어렵고, ADR 번호가 병합 순서에 따라 흔들린다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(문서 커밋 revert).

### E3-2. PR-2a — 산식 순수 함수 + 휴일 백엔드 (M13)

**결정**
- **산식 순수 함수를 첫 코드 PR에 둔다.** 세션·DB·`today_kst` 임포트 0(sB §B20 아키텍처 테스트 "schedule.py·holidays/calc.py가 순수한지"). 그래서 선적 표 없이도 시험할 수 있고, **WBS DoD①과 검증 K가 첫 코드 PR에서 green**이 된다.
- 휴일 백엔드(`holidays` 모듈, S3_PLATFORM)와 `PUT /holidays/{country}/{year}` 원자 교체·CSV 미리보기·목록·export를 같은 PR에 둔다(sB §B11, sD D2-3 H1~H5). 휴일 쓰기 = ADMIN 전용.
- **`open_quantity` kind 필터를 여기서 선행**한다(code-chain R3: `code:modules/trade_docs/quantities.py:133`이 `ConsumerSpec.kind`를 무시). 현재 등록 소비자 3건이 전부 FULFILL이라 동작 변화 0이고(`test:architecture/test_pi_contract.py:48`), PR-3a의 IN_TRANSIT 등록 전에 필터가 존재하도록 순서를 강제한다. 기존 `test_trade_docs_kernel.py:75-203` 기대값 재확인을 이 PR이 맡는다.
- **`.shard_durations.json` 갱신**(PR-16 부채 ⑥, `P:32`): 직전 main CI 아티팩트로 교체한다. 신규 테스트 파일은 미등재 추정치(테스트 수×테스트당 초)로 배정되며 누락은 구조적으로 불가하다(ADR-0073 ①③).

**DoD**: 함수 GC-01~18·27~28(sB §B20) 전부 단위 테스트 통과·`golden` 마커 / 휴일 PUT 같은 키 2회 → 교체 1회 / 연도 선언 근거 2필드 NOT NULL / 국가 ISO CHECK(markets 비FK) / CSV BOM·수식 이스케이프 / authz 행·`GOVERNED_PREFIXES` 등재 / `known_s3`에 `holidays` 등재 / table_policy 2표 MUTABLE.

**근거**: `W:115-116`, sB §B5·B6·B10·B11·B12, ADR-0073.

**대안**: 순수 함수를 마일스톤 PR(4a)에 같이 둔다. 기각한다. 4a가 최대 규모가 되고, L/C 산식(운영 경로 닫힘)의 검증이 배선 리뷰에 묻힌다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(M13 downgrade 1회 + 모듈 삭제. kind 필터는 FULFILL만 있는 동안 무해).

### E3-3. PR-3a — 수출선적 커널 (M14)

**결정**
- **S3-2 최대 위험 PR**이다. 같은 커밋 묶음 4종(E1)이 전부 여기 걸린다. 그래서 다음을 지킨다.
  - ~~커밋 순서: ① M14 + 모델 + table_policy + 사슬 레지스트리 전건 → ② DocKind·상수 dict 11종·FIELD_POLICY·상태 기계(엣지 0으로 선 편입) → ③ SO 엣지 + router Literal + 보드 상수(한 커밋) → ④ 서비스·라우터·authz → ⑤ 동시성·원자성 테스트.~~ **[적대 R-23]** 모델 CHECK가 `DOC_PREFIXES`·`STATUSES`를 읽고(`code:modules/trade_docs/mixins.py:178-181`) 엣지 0 편입은 도달성 테스트(`test:architecture/test_doc_machines.py:73-87`)를 깨므로: ① M14(선적 4표) + 모델 + table_policy + DocKind·상수 dict 11종·FIELD_POLICY·**선적 상태 기계(사람 엣지 3 포함)**·사슬 레지스트리 전건(한 커밋) → ② SO 자동 엣지 2 + RESERVED 축소 + 보드 상수(Literal 무변경 assert 통과 확인) → ③ SO 취소 검사 순서(R-02) → ④ 서비스·라우터·authz → ⑤ 동시성·원자성 테스트. 각 커밋에서 전체 pytest를 돈다.
  - **수입선적 생성 API는 열지 않는다**(PR-5a). 단 PO_LINE IN_TRANSIT 소비자 등록과 PO→SH CHILD_LINKS는 **FK가 생기는 이 PR에서** 한다(미등록 FK = `test_doc_chain_contract.py:55-64` 실패). 표는 IMPORT 행을 허용하지만 쓰기 경로가 없으므로 PO 취소 가드는 무해하다.
  - ~~**통관 기록 쓰기 API는 열지 않는다**(PR-4a, 가정 1). 단 "통관 기록 생존 시 선적 취소 409 `CUSTOMS_RECORD_ALIVE`" 가드는 표가 있으므로 이 PR에 둔다(팩토리로 시험).~~ **[적대 R-16]** `customs_records` 표·API·생존 가드는 전부 PR-4a(M15) — 3a 구간엔 통관 행이 0이라 공백 없음.
- SO COMPLETED는 RESERVED 유지, `credit/exposure.py` 무변경(sA §A15). "COMPLETED ∈ RESERVED while default provider" 결속 테스트를 이 PR에 둔다(sA §A16 K).
- §8.3 자리: ~~선적 라인 preview에서 `AllocationPort` 읽기 호출 결과(NOT_IMPLEMENTED)를 노출만 한다(sB §B14).~~ **[통합 X-26]** 포트 무변경 — `NOT_IMPLEMENTED` 값을 싣는 조립 함수(`trade_chain/shipment_view.py`). 차단 0.

**DoD**: WBS DoD③·검증 A 전건(sA §A16 A행)·B(RESERVED 5상태 진입 0)·sC C14 J-01~J-11·J-15·I-01·I-04·I-05·K-01~K-04·K-06·sA §A16 K행 / 갱신 테스트 목록(code-chain §4 표의 SO IN_SHIPMENT·CHILD_LINKS·LINE_CONSUMERS·DocKind·LOCK_ORDER·신규 표·라우터·에러 코드·no_auto_confirm·임포트 계층 행) 전부 갱신·**공회전 0**(갱신 후 테스트가 실제로 새 항목을 검사하는지 변이로 확인 — E6).

**근거**: `W:114-116`, sA §A1~A13, sC §C1~C5·C12, code-chain R5~R9.

**대안**
- (a) 3a를 "커널 편입·레지스트리" / "SO 수렴·생성 API"로 더 쪼갠다. 기각한다. 앞쪽 PR은 쓰기 경로 없는 선적 표와 RESERVED가 그대로인 SO를 병합하게 되고, 뒤쪽에서 수렴을 켜는 순간 앞에서 만든 데이터(테스트 외에는 없지만)와의 정합 논증이 다시 필요하다. 같은 커밋 묶음 규칙상 실익도 작다.
- (b) 수출·수입을 한 PR. 기각한다. 3a의 리뷰 면적이 최대인데 배정 가능량·원가 비복사·PO 대역 테스트 교체까지 더하면 적대 검토 렌즈가 분산된다.

**자율 확정**: 확정. **되돌리기 비용**: 중간~높음. M14 downgrade는 1회지만 DocKind 편입은 DB 저장값이라(code-chain R9) 운영 데이터가 생긴 뒤에는 분리가 불가에 가깝다. 운영 개시 전(현재)이면 revert + downgrade로 끝난다.

### E3-4. PR-4a — 마일스톤·롤오버·통보·통관 (M15)

**결정**
- 마일스톤 쓰기(M2~M4)·변경 이력(M5)·통보(M6)·OEM 마일스톤(M7~M9)·**통관 기록(S16~S19)**을 한 PR에 둔다. ~~통관 수리일이 `CUSTOMS_CLEARED` 실적의 유일 입력처이고 동TX 복사이므로(가정 1), 둘을 갈라 병합하면 그 사이 입력된 수리일이 마일스톤에 없는 상태가 생긴다(적재의무 산식이 UNKNOWN으로 남는 fail-closed이지만 백필 필요).~~ **[통합 X-02·적대 R-16]** 수리일은 읽기 시 파생(복사 없음)이라 백필 근거는 소멸했다. 통관을 4a에 두는 근거 = 표 자체를 M15로 옮겨 쓰기 경로 없는 표 구간을 없애고 3a 면적을 줄이는 것.
- 휴일 경고를 마일스톤 보드 응답에 배선한다. DoD② "ETA 현지 연휴 → 경고"를 **API 층에서** 여기서 닫는다(함수 층은 2a).
- 파생 3종은 저장하지 않는다(sB §B8). 파생 종류 코드가 DB CHECK에 없음을 아키텍처 테스트로 고정한다.
- `comm_logs` 주제 확장은 **선적 하위 전용 엔드포인트(M6)만** SHIPMENT 행을 만든다. `/comm-logs`의 쓰기 역할(`CAN_EDIT=(CERT,)`)은 무변경이다(sD D2-2).
- 마일스톤 세트(`item_profile_milestone_types`)로 부채 #15 마일스톤 몫을 종결한다(ADR-0021 "각 세션 DoD에서 확인", `P:709`).

**DoD**: WBS DoD② / sB §B20 GC-19~26 / sC J-13·J-14·J-11(IMMUTABLE 2표 권한 거부) / sD 테스트 배분 H행(사유 422 이중·통보 = comm_logs 1행+연결 1행) / I(M6 경로 아웃바운드 클라이언트 임포트 0) / 부모-자식 404 / Page.

**근거**: `W:114-115`, sB §B1~B9·B12·B15·B16, sD D2-2·D2-1 S16~S19.

**대안**: 통관을 3a에서 연다. 기각한다(위 백필 이유). 마일스톤을 시각형·날짜형 2 PR로 나눈다. 기각한다. 같은 표·같은 이력 규율이라 나누면 같은 테스트 골격을 두 번 갱신한다.

**자율 확정**: 확정. **되돌리기 비용**: 중간. M15에 IMMUTABLE 2표가 있어 운영 데이터 이후에는 권한 해제 후 이전이 필요하다(sB §B9 되돌리기). 운영 개시 전이면 revert + downgrade.

### E3-5. PR-5a — 수입선적

**결정**
- PO 참조 생성(S5·S6), 배정 가능량 = PO 라인 수량 − 살아 있는 수입선적 라인 합, 초과 409 `EXCEEDS_ASSIGNABLE`, **PO `open_quantity`·PO 상태 무접촉**(S4-1 소관, sA §A4·code-chain R3).
- 수입선적 라인·헤더에 금액 열 없음(sA `ck_shipments_import_has_no_amount`). 원가 9채널 봉쇄 승계(ADR-0024, sC C8).
- `test:integration/test_purchase_order_lifecycle.py:425-436`의 `fake_successors(fk_column="po_id")` 대역을 **실 수입선적 테이블 테스트로 교체**한다(PO 취소 역순 가드 실증).
- PO 상세 `expected_receipt`(sB §B17): 열 없이 계산값. 잡 이름·코드에 `purchase`·`발주`·`-po-` 금지(`test:architecture/test_po_no_auto_path.py:352-361`) — 이 PR은 잡을 만들지 않으므로 해당 없음을 PR 본문에 명기.

**DoD**: sA §A16 A행 수입분·sC J-06·K-07·sD A행(수입 응답 금액 키 0) / GC A15.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(마이그레이션 0, 라우터·서비스 제거).

### E3-6. PR-6 — 기일 스캔 잡

**결정**
- `trade-deadline-scan` `daily@06:40` 1행, ~~12 → 13~~ **[적대 R-17] 13 → 14**(sC C11 — 시각·순서·배치·보호 테스트 6곳 갱신 방식은 sC가 정본). 스캔 본체는 `trade_chain/deadline_scan.py`(L2).
- QT/PI 만료 임박 D-N(P-03)을 같은 잡에서 처리한다(sB §B18). 후보 정의는 만료 스윕과 공유한다(정의 이원화 금지 — 변이 E6로 고정).
- runbook 잡 표·DESIGN §15 잡 행은 PR-1에서 "PR-6 병합 시 활성" 주석으로 먼저 싣고 PR-6이 주석을 지운다.

**DoD**: sC H-01~H-07·I-02·I-03 / sB GC-24·25·29·30·31 / GC ~~C16~~ **A21([적대 R-12])**.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(잡 행 `enabled=false` 또는 레지스트리 행 삭제 + 총수 테스트).

### E3-7. PR-7 — 사용자·역할 화면 / E3-8. PR-8 — 마감

- PR-7은 sC C7·sD D14가 내용 정본이다. 이 부록은 **위치만** 정한다: 백엔드 0이라 어디든 가능하지만, PR-8 워크스루가 물류 계정에 ~~화면으로 만들어~~ **[적대 R-22] (CLI `create-admin`으로 생성 → 화면에서 역할 부여 → ADMIN 회수 — sD D14)** 역할을 부여해 관통해야 렌즈 11이 ~~"개발자 개입 없는 운영 개시"~~ **"역할 부여 단계의 개발자 개입 0"**을 증명하므로 **PR-8 앞**이다. **[적대 R-22]** 병합 순서는 2b 다음(3a 앞)으로 당긴다 — 3b·4b의 L 역할 실기동 관통에서 API 직접 호출이 필요 없게.
- PR-8 워크스루 경로(1회 관통, 렌즈 11 `D:457`): 휴일 연도 선언(ADMIN) → 사용자·역할에서 물류 계정 역할 부여 → SO 확정(기존) → 선적 2건 부분 생성(무역) → SO 선적중 → 마일스톤 계획·초안(물류) → ETD 롤오버(사유) + 통보 기록 → ETA 휴일 경고·UNVERIFIED 배지 → 통관 수리일 → 적재의무 표시 → 선적 1건 취소·SO 유지 → 수입선적(PO 상세) → 잡 수동 실행(CLI) → 알림 → 알림 이동. 증거는 sD D16의 3층(실 HTTP·실 브라우저 스크래치 스크립트·스크린샷 기록, 의존성 추가 0).
- PR-8 종결 대사: §20 그룹 수(`docs/testing.md`), `golden` 마커 수 = GC v1.5 S3-2 배정 건수, 부채 최종 목록, PR-16 부채 ③⑤⑥⑦ 종결·유지 표기.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## E4. ADR 목록 (0074부터) — 통합이 번호를 바꾸면 PR-1에서만 재번호

**결정** — 번호는 PR-1에서 한 번에 등재한다(병합 순서와 무관하게 고정). 각 ADR은 5줄 형식(상태·맥락·결정·근거·기각한 대안·되돌리기 비용 — ADR-0073 서식).

| ADR | 제목(요지) | 원천 결정 | 구현 PR |
|---|---|---|---|
| **0074** | 선적을 전표 커널에 편입(DocKind `SHIPMENT`, 접두어 `SH`)·상태 8값(활성 3엣지, RESERVED 5)·총수 30/152/182 | sA §A1·A7 | 3a |
| **0075** | SO CONFIRMED↔IN_SHIPMENT 자동 수렴 2엣지 — §15 [M4] 부기 "SO 자동 엣지 0" 개정과 4금 논증·`test_po_and_so_have_no_automatic_edges` 개명 | sA §A7-2, sC C12 | 3a |
| **0076** | SO COMPLETED 엣지·short-close(P-02) **S3-2 미개방**(여신 노출 공백 방지)·provider 결속 테스트·WBS 문면 이관 | sA §A7-3·A15, sC C12, AMB-15·33 | 3a |
| **0077** | 수입선적 소비 kind `IN_TRANSIT`·`open_quantity` kind 필터·배정 가능량·S4-1 FULFILL과 비중첩 승계 계약 | sA §A4, code-chain R3 | 2a(필터)·3a(등록)·5a(생성) |
| **0078** | LOCK_ORDER 개정(…PO → shipments → shipment_children → approvals → lines → seq)·SHARE→UPDATE 승격 금지·SO FOR UPDATE 선점 | sC C2·C3, sA §A10 | 3a |
| **0079** | LOGISTICS 첫 전표 쓰기 권한(선적·마일스톤·통관)·휴일 쓰기 ADMIN 전용·담당 이관 등록 | sC C6, sA §A11 | 2a(휴일)·3a |
| **0080** | 마일스톤 모델: 계획/실적 이중값 행·~~10종(BL_ISSUED 추가)~~ **[적대 R-03] 9→11종(BL_ISSUED 추가·PRESENTATION_DEADLINE 편입)**·날짜형/시각형·파생 3종 비저장 계산값·덮어쓰기 금지 | sB §B1~B3·B8 | 2a(함수)·4a |
| **0081** | L/C 대금만기·제시기한·tolerance는 순수 함수와 K 테스트로만, 운영 경로 UNKNOWN(`LC_TERMS_NOT_REGISTERED`)·tolerance 좁은 쪽 반올림·플래그 공급 미개방 승계 | sB §B5·B6, AMB-05·06 | 2a |
| **0082** | holidays 국가 축 ISO alpha-2(markets 비FK)·연도 선언 단위 근거 2필드 필수·**경고만**(자동 순연 0)·UNVERIFIED ≠ 평일·시드 0 | sB §B11·B12 | 2a |
| **0083** | 롤오버 이력 `milestone_changes`·통보 `milestone_change_notices` IMMUTABLE·comm_logs SHIPMENT 주제 확장(§17.5 확장)·발송 0 | sB §B9, sC C9 | 4a |
| **0084** | `trade-deadline-scan` daily@06:40(JOB ~~13행~~ **14행**)·QT/PI D-N 동잡·trade_chain 배치(플랫폼 deadlines 임포트 확장 금지) | sB §B13·B18, sC C11 | 6 |
| **0085** | 인계 판정 묶음: OEM `profile_id` 미신설(트리거 등재)·facilities 미신설 유지(P-57 (a) 불발동)·PO 라인 ETA 열 미신설(계산값, P-05)·마일스톤 세트 `item_profile_milestone_types` | sB §B15~B17 | 4a·5a |
| **0086** | 브라우저 e2e 도구 미채택과 렌즈 11 3층 증거·사용자 역할 화면 S3-2 배정(계정 생성 API 미신설) | sD D14·D16, sC C7 | 7·8 |
| **0087** | **[통합 N-02]** 승인 무결성 대사 잡 `approval-integrity-check` daily@05:40 배선(PR-9a 부채 ① 소비) | 통합 N-02 | 6 |

- **기존 ADR 부기**(새 번호 없이 "부기 2026-10-xx" 줄 추가): ADR-0021(마일스톤 세트 몫 종결), ADR-0037(OEM 프로파일 판정), ADR-0051·0052(RESERVED·CHILD_LINKS 소비), ADR-0053·0054(선적 복사·채번 접두어), ADR-0055("휴일 보정은 S3-2" → 경고만), ADR-0058(잡 ~~13행~~ **14행**), ADR-0059(LOCK_ORDER 승계), ADR-0064(P-01 유지 + COMPLETED 보류), ADR-0066(보드 열 가산), ADR-0067(LOGISTICS 행).
- PR 분할·마이그레이션 번호 자체는 ADR 대상이 아니다(설계 변경이 아니라 일정 — WBS 규칙 "순서 변경·분할은 ADR" 대상인 **세션 분할**과 다름, `W` '버전 규칙').

**근거**: CLAUDE.md "설계 변경은 DESIGN.md 갱신 + `docs/adr/`에 ADR 5줄이 세트", `P:633`(다음 번호 0074), 렌즈 10 `D:456`, `D:344` ②("LOCK_ORDER 변경은 ADR").

**대안**
- (a) 부록마다 ADR 1개(5개). 기각한다. 번복 단위가 섞인다 — 예컨대 COMPLETED 보류(0076)는 S3-3에서 뒤집힐 가능성이 가장 높은데 커널 편입(0074)과 한 ADR이면 부분 번복이 문서상 표현되지 않는다.
- (b) 결정마다 ADR(약 25개). 기각한다. 5줄 ADR이 사소한 열 결정까지 늘어나면 검색성이 떨어진다. 묶음 기준은 "독립적으로 번복될 수 있는 단위"다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(PR-1 병합 전 재번호는 파일명 변경. 병합 후 번호는 고정이고 번복은 새 ADR "대체" 표기).

---

## E5. WBS v1.6·GC v1.5 갱신 — 필요하다

### E5-1. WBS v1.6

**결정 — 필요하다.** S3-2 판정이 WBS 문면과 어긋나는 지점이 있어 주석 없이 두면 S3-3·S4 착수 시 "누락"으로 오독된다.

| 행 | 갱신 문면(요지) | 원천 |
|---|---|---|
| S3-2(`W:114`) | ① "RESERVED 엣지 COMPLETED 추가" → **S3-3 provider PR로 이관**(노출 공백, ADR-0076) ② "OEM `profile_id`" → 판정 결과 미신설·트리거 등재(ADR-0085) ③ short-close·PO 라인 ETA → 판정 결과 미개방·계산값 ④ 구분 4종 중 채널입고·샘플무상 경로 미개방 | sA §10 ①②, sB 요약 ④ |
| S3-2(`W:115-116`) | DoD① L/C 분기·검증 K = **순수 함수 단위·K 테스트로 충족, 운영 경로는 S3-3 `lc_terms`** 주석 | ADR-0081 |
| S3-3(`W:118-122`) | `lc_terms` → S3-2 산식 함수 배선, **SO COMPLETED 엣지·short-close를 provider `reflected=True`·선적분 차감과 같은 PR** | ADR-0076·0081 |
| S4-1(`W:134`) | 입고 FULFILL 소비는 수입선적 IN_TRANSIT과 비중첩 계약 승계 | ADR-0077 |
| S4-2(`W:139-140`) | 선적 RESERVED 5상태(PICKING~CLOSED) 엣지·출고 원장 시점·검수 미완료 CI/PL 차단 | ADR-0074 |
| S3-4 | DG 임시 체크리스트까지 S3-2~S3-4 구간 DG 선적 = 배지·runbook 경고만 | sD D9 |
| 골든 케이스 매핑표(`W:209-225`) | S3-2 행 추가: ~~A14·A15·C11~C16·F4~~ **[적대 R-12] A14~A21·F4·G3** | E5-2 |
| 변경 이력 | v1.6 항목(자율 확정, ADR-**0074~0087**(0086 → 0087 정정)) | — |

- 통관 이슈 임시 규칙(`D:394`)이 어느 세션 산출물에도 없다는 공백(요구 목록 §4-8)은 **S3-2 PR-8 runbook 안내(기존 `tasks` 활용)로 배정**하고 WBS S3-2 행에 한 줄 적는다. 코드 0.

**근거**: WBS '버전 규칙'(사유와 함께 판번 갱신+ADR), v1.5 선례(`W:235`).

**대안**: WBS 무변경·PROGRESS 부채로만. 기각한다. WBS는 세션 착수 시 범위 정본이라 S3-3 착수 세션이 COMPLETED 엣지를 자기 범위로 인지하지 못한다(조용한 누락).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(문서).

### E5-2. GC v1.5 — S3-2 배정 0건 해소

**결정 — 필요하다.** 현행 GC v1.4에 S3-2 직접 대응 케이스가 0건이고 매핑표에도 행이 없다(`W:209-225`). v1.1~v1.4 선례는 매번 "배정 0건 → 판번 상향"이었다(GC 변경 이력). 기존 ID는 A1~A13·B1~B3·C1~C10·D1·D2·E1·F1~F3·G1·G2·H1~H6이므로 이어 붙인다.

| GC | 내용(경계 값은 sB §B20·sA §A16·sC §C14가 정본) | PR |
|---|---|---|
| **GC-A14** | 부분선적 1:N: 합 = SO 수량 → 잔량 0 / +1 → 409 EXCEEDS_OPEN / 선적 취소 → 잔량 복원 / 살아 있는 선적 → SO 취소 409 | 3a |
| **GC-A15** | 수입선적: PO `open_quantity`·PO 상태 불변 / 배정 가능량 초과 409 / 응답 원가 키 0 | 5a |
| **GC-F4** | 동시 부분선적 7+7(수량 10) **실제 동시 실행** → 1 성공·1 409, 500·40P01 0(잠금 제거 변이에서 실패 확인) | 3a |
| **GC-C11** → **A16**([적대 R-12]) | 대금만기 분기: T/T 앵커+일수(음수 = ETD 전용)·KST 확정일 / 앵커 미확정·INVOICE_DATE = UNKNOWN(대체 금지) / TT_ADVANCE 100% = 해당 없음 / L/C 운영 = UNKNOWN | 2a |
| **GC-C12** → **A17** | L/C 제시기한 MIN 양방향·동일일 경계·유효기일 결측 UNKNOWN / tolerance 좁은 쪽 반올림·경계 포함 | 2a |
| **GC-C13** → **A18** | 적재의무 = 수리 실적 + 30(달력일·윤년)·계획 미사용 | 2a |
| **GC-C14** → **A19** | ETA 현지 연휴 경고 양방향 + 연도 미선언 = UNVERIFIED + 휴일과 겹친 만기 값 불변 | 2a(함수)·4a(배선) |
| **GC-C15** → **A20** | 롤오버: 이력 누적·같은 키 1행·사유 필수·IMMUTABLE / 실적 입력 → 파생 재계산 | 4a |
| **GC-C16** → **A21** | 기일 스캔: 롤오버 → 새 기일 새 알림·옛 키 재발송 0 / QT/PI D-N 후보 = 스윕 후보 / KST 경계 | 6 |

- 각 케이스는 pytest `golden` 마커를 단다. PR-8이 마커 수 = ~~9건~~ **10건([적대 R-12])**(S3-2 배정)을 대사한다(S3-1 PR-16 "조용한 누락" 재발 방지, `P:11`).
- GC 규칙(삭제 금지·DEPRECATED 표기)상 추가만 하는 변경이다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(DEPRECATED 표기로 철회 가능, 삭제 불가).

---

## E6. 검증 배치 — 그룹 A~K·CI 3샤드·변이 점검

### E6-1. §20 그룹 × PR 배치

P3 해당 그룹은 A·B·E·G·H·I·K(`D:417` ①)이고 J는 전 Phase 공통이다.

| 그룹 | 2a | 3a | 4a | 5a | 6 | b PR들 | 비고 |
|---|---|---|---|---|---|---|---|
| A 정합성 | ○ 산식 | ◎ 잔량·역순·불변 | ○ 재계산 | ○ 수입 | — | e2e | WBS 검증 A = 3a |
| B ~~검수·흐름~~ **서류([적대 R-07] `D:420`)** | — | ○ RESERVED 진입 0 | — | — | — | — | §20 B "검수 미완료 선적의 CI·PL 생성 차단"의 전제. 차단 본체는 S3-3·S4-2 |
| E ~~재고~~ **비용·소싱([적대 R-07] `D:423`)** | — | — | — | — | — | — | **해당 없음**(비용 코어 = S3-4, `W:124-128`). ~~stock_movements 무접촉~~ → K 아키텍처 단언으로 이동 |
| G ~~마스킹·내보내기~~ **AI·보안([적대 R-07] `D:425`)** | ○ 휴일 CSV | ○ 원가 키 0(3c CSV) | — | ◎ 원가 비복사(GC-G3) | — | — | 내용은 S3-1 부기 `D:417` ① "G=파일 해시 멱등·PO 원가 마스킹" 승계 |
| H 운영·알림 | — | ○ 이관 | ○ 통보 = 기록 | — | ◎ dedup·에스컬레이션·플래그 오프 | — | |
| I 자동화 | — | ◎ 자동 엣지 4금·no_auto_confirm | ○ 발송 0 | — | ◎ 잡 ~~13~~ **14**·실패 감지 | — | |
| J 안전 계약 | ○ 휴일 1TX | ◎ 동시성·원자성 | ○ 롤오버 멱등 | ○ 동시 수입 | ○ 재실행 멱등 | 더블클릭 표시 | |
| K 보안·품질 | ◎ L/C MIN·tolerance | ◎ 총수·사슬·authz·Page·**stock_movements 무접촉(아키텍처)** | ○ 404·Page | ○ | ○ 4금 집합 | vitest 계약 | WBS 검증 K = 2a |

(◎ = 그 PR의 주 검증, ○ = 해당 케이스 있음)

### E6-2. CI 3샤드(ADR-0073) 적용

**결정**
- 신규 테스트 파일은 `.shard_durations.json` 미등재 추정치로 자동 배정되므로 **샤드 설정 변경은 0**이다. 완전성은 `backend-coverage`의 독립 수집 대조가 매 실행 확인한다(ADR-0073 ③).
- **동시성 테스트(`concurrency` 마커, GC-F4·J-02~J-07·J-15)는 파일 단위 샤드 안에서 직렬**이므로 실제 동시성 의미가 유지된다(xdist 미채택 근거와 같다). 3a의 `test_shipment_concurrency.py`는 장벽·별도 커넥션 방식으로 쓰고, 한 파일이 샤드 timeout(40분)의 1/4을 넘으면 파일을 나눈다.
- 2a에서 durations를 갱신하고(PR-16 부채 ⑥ 해소), **3a·4a 병합 후 다시 갱신**한다(신규 대형 파일이 추정치로 한 샤드에 몰리는 불균형 방지 — 누락 위험은 없고 시간만의 문제).
- 커버리지 94% 게이트는 병합 커버리지에 건다(ADR-0031·0073). 순수 함수 모듈(2a)은 분기 100%를 목표로 하되 게이트는 기존 임계 그대로다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

### E6-3. 변이 점검(PR별 최소 목록)

**결정** — S3-1 방식 그대로: 워크트리 사본에서 `-x`/`--maxfail` 없이 변이마다 `--junitxml`로 `<failure>`·`<error>`를 구분하고, **전원 kill·error 0**이 통과 조건이다(PROGRESS PR-14a·PR-16 변이 점검 선례). 아래는 **최소** 목록이고 PR 작업자가 가산한다.

| PR | 변이(최소) |
|---|---|
| 2a | MIN→MAX / +21→+20 / tolerance 반올림 방향 반전 / 윤년 무시(+30을 월+1로) / UNKNOWN을 ETD로 대체 / UNVERIFIED→CLEAR / 휴일 날짜를 자동 순연 / kind 필터 제거(IN_TRANSIT 임시 spec이 잔량을 줄임) / 휴일 PUT 비원자(soft delete와 INSERT 분리 커밋) |
| 3a | **[적대 R-30]** SO 취소 검사 순서 원복(IN_SHIPMENT 취소가 `NOT_ALLOWED`) / `require_within_open` `<=`→`<` / SO 헤더 FOR UPDATE 제거(GC-F4 실패 필수) / 선적 취소 시 SO 수렴 생략(거짓 IN_SHIPMENT) / CHILD_LINKS SO→SH 행 제거(SO 취소 통과) / CONSUMABLE에서 IN_SHIPMENT 제거(두 번째 부분선적 409) / RESERVED에서 COMPLETED 제거 / 자동 엣지를 사람 엣지로 / LOCK_ORDER 순서 역전 / authz L 쓰기 DENY / 수입 응답에 단가 키 추가 |
| 4a | **[적대 R-30]** 실적 생존 취소 가드 제거 / PLANNED 실적 허용 / 미래 수리일 허용 / PARTIAL을 CLEARED로 / 범용 `/comm-logs` 목록에 SHIPMENT 노출 / M2 응답 `change` 누락 / 파생 종류를 쓰기 허용 / 사유 CHECK 제거 / 이력 INSERT 생략 / 같은 키 재요청 시 이력 2행 / **[통합 X-02]** 유효 수리일 MIN→MAX·죽은 통관 기록 포함 / 실적 미래 +1일 경계 이동 / comm_logs 직접 경로로 SHIPMENT 허용 |
| 5a | PO `open_quantity` 차감 / 배정 가능량 산식에서 죽은 선적 포함 / 원가 복사 |
| 6 | **[적대 R-30]** 시각형 도과를 날짜 비교로 / dedup 키에서 기일 제거 / 에스컬레이션 일수 변경 / 실패 1건을 무시(잡 SUCCESS) / D-N 후보에서 후속 생존 검사 제거 / 잡 시각 06:30(중복) |

- **레지스트리 갱신 테스트의 공회전 검출**도 변이로 한다(3a): 신규 항목을 레지스트리에서 지웠을 때 갱신한 아키텍처 테스트가 실패해야 한다(예: `known_s3`에서 `shipments` 제거, `GOVERNED_PREFIXES`에서 `/api/v1/shipments` 제거). 실패하지 않으면 테스트가 새 항목을 보지 않는 것이다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## E7. 적대 검토 배치

**결정**
- 모든 코드 PR: 자기 적대 검증 **2렌즈**(정확성·계약 위반 / 보안·동시성), 검증자는 테스트를 실행하지 않고 반증만 한다(`P1:81`). med 이상은 독립 반증을 거치고, 반증에서 낮아져도 실재 결함이면 반영한다(PR-14a 2차 선례 `P:50`).
- **3a·4a는 3렌즈**(+ 문서 정합 렌즈: DESIGN 부기·ADR·부록 A~D 문면과 구현 일치)와 2차 적대 검토를 둔다. 3a는 같은 커밋 묶음 4종과 커널 편입, 4a는 IMMUTABLE 2표와 기존 표 CHECK 재정의가 있어 되돌리기 비용이 가장 높다(E3-3·E3-4).
- 적대 검토 판정 후보도 오너 지시로 엄격 쪽 자율 확정하고 PROGRESS에 표기한다(`P:48` 선례).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## E8. §22 11렌즈 — PR별 완료 체크 배치

| 렌즈 | 주 체크 PR | 근거 |
|---|---|---|
| 1 기능 | 2a(DoD①·K)·3a(DoD③·A)·4a(DoD②) | WBS DoD 3항·검증 2항이 PR에 1:1 배치(E3-0) |
| 2 데이터 | 2a·3a·4a | 마이그레이션 3건·스냅샷·채번·IMMUTABLE 3표·table_policy |
| 3 트랜잭션 | 3a·4a | sC C1 동작 12종 1TX |
| 4 동시성·멱등 | 3a(◎)·5a·6 | GC-F4·LOCK_ORDER·Idempotency-Key |
| 5 보안·권한 | 3a·5a·7 | L 첫 쓰기·원가 비복사·ADMIN 화면 |
| 6 시간 | 2a(◎)·4a·6 | KST 오늘·현지일·윤년·D-N 경계 |
| 7 성능 | 3a·4a | 목록 50·상세 쿼리 수 상한·문서 흐름 5회 |
| 8 테스트 | 전 PR | E6 그룹·변이·GC ~~9건~~ **10건** |
| 9 운영 | 1b·6·8 | 잡 ~~13행~~ **14행**·runbook 운영 개시 절 |
| 10 문서 | 1·8 | DESIGN 부기·ADR **0074~0087**(0086 → 0087 정정)·WBS v1.6·GC v1.5 |
| 11 워크스루 | 3b·4b·8 | b PR은 해당 화면 실기동 관통, 8은 입구~출구 1회 |

미통과분은 조용히 넘기지 않고 PR 본문과 PROGRESS에 부채로 적는다(`D:459`).

---

## E9. 되돌리기 비용 — 전체 요약

| 단위 | 비용 | 방법 |
|---|---|---|
| PR-1(문서) | 낮음 | revert. 병합 후 ADR 번호는 고정, 번복은 새 ADR "대체" |
| PR-2a(M13·순수 함수) | 낮음 | M13 downgrade + 모듈 제거. kind 필터는 FULFILL만 있는 동안 무해 |
| PR-3a(M14·커널 편입) | **운영 데이터 전 중간 / 후 높음** | revert + M14 downgrade. 운영 후에는 DocKind 저장값·상태이력이 남아 분리 불가에 가깝다(code-chain R9) |
| PR-4a(M15) | 중간 | IMMUTABLE 2표 데이터가 있으면 권한 해제 마이그레이션 후 이전. `comm_logs` CHECK 원복은 SHIPMENT 행 정리 선행 |
| PR-5a·PR-6·PR-7 | 낮음 | 마이그레이션 0. 잡은 `enabled=false` |
| b PR들 | 낮음 | 화면 revert(서버가 정본) |
| 순서(DAG) | 병합 전 낮음 / 후 중간 | 리비전 순서 변경은 downgrade 사슬 역행 필요 |

- **현재 프로덕션 운영 개시 전**이라면(runbook 운영 개시 절이 PR-16에서 마련됐으나 실데이터 투입 여부는 PR-1 착수 시 PROGRESS로 확인) 3a·4a도 revert + downgrade로 끝난다. 그래서 **3a·4a는 운영 데이터 투입 전에 병합을 끝내는 것**을 권장 일정으로 둔다(확인 항목 E10 ③).

---

## E10. 착수 시 실행 확인 필요 항목 (정적 독해 한계 — 실행 검증 못 했음)

1. 현 head가 `f2cb6020b2bb` 하나인지(`alembic heads`) — PR-2a 첫 커밋.
2. SO 상태 CHECK·상태이력 CHECK가 IN_SHIPMENT 엣지 추가만으로 재생성 불요인지 — PR-3a 첫 커밋(sA §A14).
3. 프로덕션에 S3-1 전표 실데이터가 있는지(E9 되돌리기 비용 판정의 전제) — PR-1 착수 시.
4. `comm_logs` 주제 CHECK의 실제 제약명과 downgrade 시 SHIPMENT 행 존재 시 실패 동작 — PR-4a.
5. M14·M15 식별자 63자(예: `uq_shipment_lines_so_line_live`, `ck_milestones_one_owner`) — 각 PR.
6. 신규 테스트 파일이 추정치 배정으로 한 샤드에 몰려 40분 timeout에 근접하는지 — 3a 첫 CI.
7. `test_no_auto_confirm_code_path_exists.py:776-795`의 `_trade_chain_imports` 집합 갱신이 PR-6에서만 필요한지(3a가 trade_chain에 선적 오케스트레이터를 추가하면 3a에서도 갱신) — 3a 첫 커밋.

---

## 멈춰서 보고할 항목(이 부록 범위)

1. ~~**sA §A14 "1건 5표"와 통관 API 개방 시점**~~ **[적대 R-16로 해소 — `customs_records`를 M15(PR-4a)로 이동, 쓰기 경로 없는 표 구간 0]**: 표는 M14(PR-3a)에 생기지만 쓰기 API는 PR-4a에서 연다(가정 1, 수리일 동TX 복사 때문). 한 PR 구간 동안 쓰기 경로 없는 표가 존재한다. 통합이 sB §B7 (i)안(마일스톤 실적이 원천)을 택하면 통관 API를 3a로 당길 수 있다.
2. **ADR 번호 배정 권한**: sA·sB·sD는 "통합이 0074~에서 부여"라고 적었다. 이 부록 E4가 13개 번호를 제안했으므로 통합은 **번호를 바꿀 경우 PR-1에서만** 바꾼다.
3. **WBS v1.6 필요**: S3-2 판정 4건(COMPLETED 이관·profile_id 미신설·L/C 검증 해석·채널입고/샘플 경로)이 WBS 문면과 어긋난다. 주석 없이 두면 S3-3·S4 착수 세션이 누락으로 읽는다(E5-1).
4. **통관 이슈 임시 규칙(`D:394`) 배정 공백**: 어느 세션에도 없다. PR-8 runbook 안내(코드 0)로 배정하고 WBS에 한 줄 적는다.

## 부채 등재 후보(이 부록)

- `.shard_durations.json` 재갱신(3a·4a 병합 후) — 트리거: 샤드 간 소요 편차 2배 초과.
- 사용자·역할 화면(PR-7)이 다른 PR과 순서 경합 시 PR-8 직전 고정 — 트리거 없음(일정 규칙).
- 3a 동시성 파일 분할 — 트리거: 단일 파일 10분 초과.

**실행 검증 못 했음.** 이 부록의 PR·리비전·테스트 ID는 설계이며 아직 존재하지 않는다. 인용한 코드 줄은 `a4d91c0` 정적 독해 값이고, 각 PR 첫 커밋에서 실측해 PROGRESS에 기록한다.
