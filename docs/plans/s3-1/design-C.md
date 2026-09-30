# S3-1 계획서 안건 전문 — 묶음 C: 승인 코어 (approvals · 결재선 매핑 · 대결)

> 작성: 심판(최종 설계자) · 2026-09-30 · 오너 지시(2026-09-29)에 따른 **자율 확정** 안건 — 사후 번복 가능, 안건마다 되돌리기 비용 명기.
> 성격: 설계 결정. 리포 파일 수정 0, 테스트·서버·DB 실행 0(읽기·grep만 — **실행 검증 못 했음**).
> 입력: 3개 관점 제안(strict/risk/fit) + 리포 직접 검증. 제안이 인용한 코드·DESIGN 사실은 아래 "검증 결과"에서 판정했다.

---

## 0. 검증 결과 — 제안들의 사실 주장 판정 (심판이 직접 grep/읽기로 확인)

### 0.1 참으로 확인된 주장 (설계 근거로 채택)

| # | 주장 | 확인 위치 |
|---|---|---|
| V1 | `require_roles`는 ADMIN을 무조건 통과시킨다 → 결재 자격 판정 수단이 못 된다(서비스가 별도 판정) | `backend/app/api/deps.py` `_role_guard` |
| V2 | 프런트 `hasRole`도 ADMIN이면 항상 true → 화면이 `can_decide`를 추정하면 "기안자=승인자 버튼" 상시 노출. **서버 계산 필드 필수** | `frontend/src/lib/session.ts:61` |
| V3 | `Routing`은 정확히 3값(DEADLINE·EVENT·ADMIN)이고 테스트가 고정. `notify()`는 **담당자(assignee_id)가 활성이면 routing과 무관하게 그 사람에게** 보낸다(규칙 0행이어도) | `notifications/service.py:151-249`, `tests/architecture/test_notification_core.py:82` |
| V4 | JOB_REGISTRY는 현재 **7행**, 4금 비저촉 테스트가 코드 집합을 전건 나열한다 → 잡 추가 시 8행·집합·서술 갱신 | `platform/scheduler.py:176-225`, `tests/architecture/test_scheduler_registry.py:117-135` |
| V5 | `test_no_unique_key_contains_a_cost_column`은 `_amount` 접미 컬럼이 유니크 키에 들어가면 실패하고, 예외는 `ALLOWED_SENSITIVE_UNIQUE_KEYS`에 **사유와 함께** 등록해야 한다(ADR-0018 ㉠ 갱신 동반) | `tests/architecture/test_secret_boundaries.py:39-124`, `core/money.py:53-69` |
| V6 | 리포에 **DB 트리거 선례 0건**, 트리거는 ADR-0028(파기 잠금 트리거 기각)·ADR-0040(금지 트리거 기각)에서 반복 기각. 컬럼 단위 GRANT 선례도 0건 | grep `CREATE TRIGGER` 0건, `docs/adr/0028`·`0040` |
| V7 | 상태 열거를 소비 없이 선확장하면 "검증 코드 없는 열거값(죽은 문)" — ADR-0041 판정 문면 | `docs/adr/0041-…md:11` |
| V8 | 활성 한정 부분 유니크(status 술어) 선례 = `uq_certifications_template_target_open`; 상태 이력 테이블 IMMUTABLE 선례 = `certification_status_log`(PkMixin+Base만); 전이 단일 통로 `_record_transition` + 정규식 스캔 `\.status\s*=(?!=)` 선례 | `certifications/models.py:172-190,232-277`, `certifications/service.py:389`, `test_certification_machine.py:132-152` |
| V9 | 실패도 커밋 관용 = UoW 안에서 `blocked` 플래그+감사 기록 → **UoW 밖에서 raise** | `identity/service.py:505-567` |
| V10 | `unit_of_work()`는 이미 열린 UoW에 **합류**한다(안쪽이 따로 커밋하지 않는다) → 중첩 호출에서 "예외를 던지되 기록은 커밋"은 **불가능**. 그래서 소비 실패는 예외가 아니라 결과값으로 돌려줘야 한다 | `core/db/uow.py:48-79` |
| V11 | 모듈 위치는 `app/modules/<name>/`, API 접두는 `/api/v1`(한 곳에서만 부착) | `app/api/router.py`, `app/modules/*` |
| V12 | `lock_timeout=5s`가 kbos_app 역할에 걸려 있고 핸들러에 55P03(LockNotAvailable) 매핑이 **없다** → 잠금 충돌이 500(INTERNAL.UNEXPECTED)으로 샌다 | `infra/postgres/init/00-roles.sql:39`, `core/errors/handlers.py`(StaleDataError만) |
| V13 | 에러 코드 3세그먼트·카탈로그 전수·문구 ≥10자+조치 힌트("주세요/확인/요청…") 테스트. 상태 전이 계열 선례 = `CERTIFICATIONS.TRANSITION.NOT_ALLOWED`(409)·`…REASON_REQUIRED`(422), 전제 미충족 선례 = `…TEMPLATE_NOT_CONFIRMED`(422) | `tests/unit/test_error_catalog.py`, `core/errors/codes.py:112-128` |
| V14 | 담당 이관 컬럼명 스캔은 4종(assignee_id·recipient_user_id·owner_user_id·in_charge_user_id)뿐이고, alerts.recipient_user_id는 이관 대상 | `handover/targets.py:25-49` |
| V15 | 알림 dedup 키 = `subject_key:수신자id`(코어가 꼬리 부착), handover가 이 꼬리로 키를 재작성한다 → subject_key 꼬리 규약 유지 필요 | `notifications/service.py:224`, `deadlines/service.py:497-508` |
| V16 | 멱등 `claim`은 결과 미기록 키를 **이어받아 재실행**한다(막힌 키 없음) → 거부·실패도 커밋 패턴과 공존 가능 | `idempotency/service.py:104-127` |
| V17 | 사용자 목록 `/users`는 관리자 전용, 표시명만 전 역할에 노출하는 선례(`assignee_name`)가 있다 | `identity/router.py:89-99`, `certifications/service.py:115,201` |
| V18 | PROGRESS L144: "쓰기 스키마 forbid 스캔이 certifications 한정 — **다음 신규 모듈 추가 세션에서 app 전역으로 넓힐 것**" → 트리거가 S3-1(신규 모듈 다수)에서 발동 | `PROGRESS.md:144`, `test_certification_machine.py:169-192` |
| V19 | `stagnation-scan` 선례: N은 `alert_rules.config.days`가 덮어쓰고 형이 흐리면 기본값, 건별 독립 트랜잭션·실패 1건이라도 있으면 잡 FAILED, 키 k=경과일÷N | `collaboration/stagnation.py`, `scheduler.py:_fail_if_any_failed` |
| V20 | 금액 API 규약: 요청은 사람 표기(Decimal, `max_digits=15`)+통화 → 서비스가 정수 최소단위로 변환·자릿수 초과 422, 응답은 `*_amount` 정수+통화 | `partners/schemas.py:29-45`, `partners/service.py:127-167` |
| V21 | 마이그레이션 시드 금지(함정 ⑩) 테스트의 `_NEVER_SEEDED` 목록에 앱 소유 테이블을 **열거**해야 한다 | `test_scheduler_registry.py:58-66` |
| V22 | `today_kst()`가 단일 KST 오늘 함수다(테스트는 monkeypatch·인자 주입) | `core/time.py:59` |
| V23 | 상태·유형 CHECK 헬퍼 `value_in`, 부분 유니크 헬퍼 `unique_active`(술어=deleted_at 한정)·수기 Index 선례, 제약 이름 63자 상한 가드 | `core/db/constraints.py` |
| V24 | SoftDelete 없는 가변 테이블 선례: `user_sessions`·`events`·`doc_number_seq`·`idempotency_keys` (ADR-02 "soft delete 전 테이블"의 실제 예외 관행) | `grep "^class .*Base"` |

### 0.2 틀렸거나 리포와 충돌해서 **기각한** 주장

| # | 제안 | 판정 |
|---|---|---|
| X-a | (strict·risk·fit 중 risk·fit) 모듈 경로 `app/mod/approvals/`, API `/api/approvals/…` | **오류**. 실제는 `app/modules/approvals/`, `/api/v1/…` (V11). |
| X-b | (fit) `approval_lines` 유니크에 `min_amount` 포함 | **테스트 실패**(V5). 등록 없이는 `test_no_unique_key_contains_a_cost_column` 위반. 본 계획은 예외 등록+사유를 명시한다(C3). |
| X-c | (risk) `trg_approvals_immutable_snapshot` BEFORE UPDATE **트리거** | 리포 관용 위반(V6). 대신 **컬럼 단위 UPDATE 권한**(C6)을 택한다 — §17.5가 1순위로 든 "권한 제거" 수단이고 ADR-0040이 트리거를 기각한 이유("권한으로 충분한데 수단이 는다")와 같은 계보다. |
| X-d | (risk) consume이 예외(TARGET_CHANGED 409)를 던지면서 "VOIDED 커밋" | **불가능**(V10). 소비는 확정 UoW에 합류해 실행되므로 예외 시 VOID·감사까지 롤백된다. → `ConsumeResult` 반환 방식(C6). |
| X-e | (fit) identity 계정 비활성 트랜잭션에서 `delegations.end_for_user()` 호출 | approvals가 identity를 임포트하므로 **순환 임포트**. 계산 술어(비활성 즉시 무효)가 동등한 fail-closed를 이미 달성 → 훅 기각(C5). |
| X-f | (risk·fit) 승인 알림/정체 잡을 09:30(브리핑 이후) 또는 승인 만료 스윕과 합침 | 브리핑(09:00)의 "미확인 알림 집계"에 독촉분이 들어가야 한다는 선례(스캔 07:00) 위반, 그리고 만료 자체를 두지 않으므로(C2) 합칠 것이 없다 → 07:10 단독 잡. |
| X-g | (fit) C8에서 `POST /api/v1/approvals`(요청 생성)를 API 목록에 넣고 다른 문장에서는 "범용 생성 API 없음" | **자기모순**. 생성은 HTTP로 노출하지 않는다(C8). |
| X-h | (strict) `approval_lines` 유니크 `(type, currency, approver_role)` | TRADE·LOGISTICS·CERT는 서열 없는 동료 역할이라 같은 역할이 서로 다른 금액 구간에 다시 나오는 구성을 DESIGN이 금지하지 않는다 — 역할 유니크는 정당한 조합을 부당하게 막는다. 유일성의 축은 임계값이다(C3). |
| X-i | (risk) 대상 **작성자** ≠ 결재자 SoD 확장 | DESIGN 문면·r1 D1 어디에도 없고 SO 묶음에 `target_author_id` 계약을 강요한다. 요청자≠결재자로 충분히 좁다 → 채택 안 함, 관찰 등재(C4). |
| X-j | (risk) 대결 90일 상한 CHECK, (risk·fit) 승인 유효기간 7일·만료 상태 | DESIGN 문면 근거 없는 수치·자동 전이 표면 → 채택 안 함(C2·C5). |
| X-k | (fit) `approvals`에 SoftDelete 믹스인+술어에 `deleted_at` | 삭제 개념이 없는 기록에 죽은 컬럼. 상태 술어 유니크 선례(V8)를 따른다(C1). |

---

## 1. 안건 지도 (본 묶음 결정 12건)

| 안건 | 주제 | 성격 |
|---|---|---|
| C1 | 승인 유형 열거·신규 테이블 4종·믹스인·CHECK·인덱스 | 데이터 모델 |
| C2 | "기안→승인 2단" 표현·상태 6값·전이 7방향·재기안·만료 없음 | 상태 머신 |
| C3 | 결재선 매핑(approval_lines) — 구간·경계·통화·fail-closed·공급 경로 | 설정 데이터 |
| C4 | 결재 자격·직무분리(SoD)·ADMIN 해석 | 권한 규칙 |
| C5 | 대결(delegations) — 범위·기간·중첩·종료·이력·수임자 선택 | 위임 |
| C6 | "승인 후 불변"·스냅샷·1회 소비·무효화·락 순서 | 불변·동시성 |
| C7 | 알림·내 결재함·정체 독촉 잡 | 알림 |
| C8 | 단일 결정 통로·API·권한·에러 코드·화면 4요소·이관 취급 | 표면 |
| X1 | 소비자(SO 게이트) 계약 — 요청은 사람의 의사, 소비 호출 규약 | 타 묶음 계약 |
| X2 | PR 순서·타 묶음 요구 | 계획 |
| X3 | 기존 코어 확장(holders_of_role·column-grant 헬퍼·시드 금지 목록·잠금 매핑) | 공용 |
| X4 | 문서·ADR·GC·WBS 갱신 세트 | 문서 |

---

## 안건 C1. 승인 유형 열거 · 신규 테이블 4종 · 믹스인 · CHECK · 인덱스

**(a) 목적·경계**
- 승인 워크플로우의 저장 골격을 완결한다: 결재선 매핑(`approval_lines`)·요청/결정 본체(`approvals`)·결정 이력(`approval_events`)·대결(`delegations`).
- **승인 유형은 코드 고정 열거(ADR-11), 매핑(금액 임계→역할)만 데이터**다. 유형 테이블(`approval_types`)은 만들지 않는다 — 유형은 소비 코드(게이트·TargetSpec)가 있어야 의미가 있어 데이터화하면 "유형은 있는데 동작 없음"이 생긴다.
- **S3-1이 실소비하는 값은 `SO_CREDIT_EXCEEDED` 1종뿐**이다. DESIGN §2가 대상으로 든 나머지 5종(임계 초과 비용·신규 파트너·권고 외 소싱·폐기·실사 차이)은 **열거·CHECK·상수 어디에도 만들지 않는다**(ADR-0041 "소비 없는 선확장=죽은 문", r1 A8의 "예약"은 문서 예약으로 해석). 소비 세션이 값 추가 절차를 밟는다(아래 ⑤).
- 경계 밖: 준비도·단가 편차 override(승인이 아니라 사유 필수 기록 — 다른 묶음), 승인 요청 HTTP 생성(C8), 다단 결재(§1 "BPM식 동적 워크플로우 엔진 금지").

**(b) DESIGN 근거**
§2 "승인 워크플로우: 기안→승인 2단, 승인 후 불변. 정(正)결재선=승인유형×금액임계→승인 역할 매핑 테이블(설정화), 승인자 부재 시 대결(기간·범위·이력)" / §3 표 맵 `approvals` / §17.5 확장문("상태 변경 이력 성격 테이블은 신설 세션이 IMMUTABLE 등재" — ADR-0040 서식) / ADR-11 / ADR-0041 / §17.4·§18.1 / V5~V8.

**(c) 데이터 변경** — 마이그레이션 1건 `YYYYMMDD_HHMM_<rev>_approvals_core`(**additive: 신규 4테이블·시드 0·데이터 이관 0**). 생성 순서 lines → delegations → approvals → events, downgrade는 역순 DROP(데이터 손실 없음: 신규 테이블).

공통 상수(`approvals/models.py`, 단일 출처):
`APPROVAL_TYPES=("SO_CREDIT_EXCEEDED",)`, `TYPE_TARGET={"SO_CREDIT_EXCEEDED":"SALES_ORDER"}`, `TARGET_TYPES=("SALES_ORDER",)`, `APPROVER_ROLES=("ADMIN","TRADE","LOGISTICS","CERT")`(= RoleCode에서 VIEWER 제외), `APPROVAL_STATUSES`(C2).

**① `approval_lines`** — Pk+Timestamp+SoftDelete+Version+Actor
| 컬럼 | 타입 | 비고 |
|---|---|---|
| approval_type | VARCHAR(30) NOT NULL | CHECK `approval_type_valid`(value_in) |
| threshold_amount / threshold_currency | BIGINT NOT NULL / CHAR(3) NOT NULL | `money_columns("threshold")`, CHECK `threshold_amount_nonneg`(>=0), `threshold_currency_upper`(`~ '^[A-Z]{3}$'`) |
| approver_role | VARCHAR(20) NOT NULL | CHECK `approver_role_valid`(4값 — VIEWER는 CHECK로 배제) |
| note | VARCHAR(200) NULL | CHECK `note_not_blank` |
- 유니크: **수기 Index** `uq_approval_lines_threshold_active` UNIQUE(approval_type, threshold_currency, threshold_amount) WHERE deleted_at IS NULL(이름 63자 이내 — `unique_active` 헬퍼는 이름이 초과해 못 쓴다). 같은 (유형·통화)에서 임계값 중복을 DB가 막는다 → 선택 규칙이 결정적.
- **`test_secret_boundaries.ALLOWED_SENSITIVE_UNIQUE_KEYS`에 `("approval_lines","threshold_amount")` 등록**, 사유: "결재 임계는 관리자가 쓰는 정책값이며 원가·마진이 아니다. 쓰기는 ADMIN 전용, 읽기는 비조회 4역할 — 유니크 위반 DETAIL에 실려도 열람 권한 밖 노출이 없다." **ADR-0018 ㉠에 부기 필요**(테스트 문면이 요구하는 '재검토 트리거의 기계적 형태').
- **불변 규율**: 행은 생성 후 `approver_role`·`note`만 수정 가능(유형·통화·임계 변경은 삭제+신규). 컬럼 UPDATE 권한으로 DB가 강제(C6·X3).

**② `approvals`** — Pk+Timestamp+Version+Actor (**SoftDelete 제외**)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| approval_type | VARCHAR(30) NOT NULL | CHECK |
| target_type | VARCHAR(20) NOT NULL | CHECK IN('SALES_ORDER') |
| target_id | BIGINT NOT NULL | **FK 없음**(폴리모픽 — documents·tasks 선례, ADR-0028 계보). 실재는 TargetSpec이 서비스에서 검증 |
| target_label | VARCHAR(200) NOT NULL | 요청 시점 표시명(전표번호 등) 동결 — 결재함 목록의 폴리모픽 N+1 방지 |
| status | VARCHAR(12) NOT NULL DEFAULT 'REQUESTED' | CHECK `status_valid`(6값) |
| requested_by_id | BIGINT FK users RESTRICT NOT NULL | |
| basis_amount / basis_currency | BIGINT NOT NULL / CHAR(3) NOT NULL | `money_columns("basis")` — **승인이 허용하는 금액 상한**(SO_CREDIT_EXCEEDED에서는 '여신 초과분'). CHECK `basis_amount_positive`(>0), `basis_currency_upper` |
| snapshot_digest | CHAR(64) NOT NULL | CHECK `~ '^[0-9a-f]{64}$'` — sha256 hex |
| snapshot | JSONB NOT NULL DEFAULT '{}' | **표시용**(판정에 쓰지 않음). CHECK `jsonb_typeof(snapshot)='object'`. 서비스가 스칼라(int/str/bool/None)만·키 20개·4KB 이하·`is_sensitive_key` 키 금지를 검증(원가·마진·비밀 불가) |
| required_role | VARCHAR(20) NOT NULL | 요청 시점 **동결**(C3). CHECK 4값 |
| approval_line_id | BIGINT FK approval_lines RESTRICT NOT NULL | 매핑 계보 |
| decided_by_id | BIGINT FK users NULL | 실제 행위자(대결이면 수임자) |
| decided_on_behalf_of_id | BIGINT FK users NULL | 대결이면 위임자 |
| decided_delegation_id | BIGINT FK delegations RESTRICT NULL | |
| decided_at | TIMESTAMPTZ NULL | 승인·반려 시각 |
| consumed_at / consumed_by_id | TIMESTAMPTZ NULL / BIGINT FK users NULL | |
- CHECK(모델에는 접두 없이 — 규약): `type_target_pair`(TYPE_TARGET 상수에서 SQL 생성), `decided_pair`((decided_by_id IS NULL)=(decided_at IS NULL)), `decided_state`(status='REQUESTED'→decided_at NULL / status IN('APPROVED','REJECTED','CONSUMED')→decided_at NOT NULL), **`sod`**(decided_by_id IS NULL OR decided_by_id<>requested_by_id), `on_behalf_pair`((decided_on_behalf_of_id IS NULL)=(decided_delegation_id IS NULL)), `on_behalf_needs_decider`, **`on_behalf_not_requester`**(위임자≠기안자), `consumed_pair`((status='CONSUMED')=(consumed_at IS NOT NULL)), `consumed_by_pair`.
- **사유 컬럼을 approvals에 두지 않는다** — 반려·회수·무효 사유는 `approval_events`가 단일 출처(이중 저장=드리프트). 목록·상세는 최신 이벤트를 `LEFT JOIN LATERAL`로 1쿼리에 붙인다(N+1 금지).
- 인덱스: **`uq_approvals_active_target`** UNIQUE(approval_type, target_type, target_id) WHERE status IN ('REQUESTED','APPROVED')(대상당 활성 승인 1건 — 종결 4태 밖이라 재기안은 신규 행), `ix_approvals_inbox`(required_role, id) WHERE status='REQUESTED', `ix_approvals_requested_by`(requested_by_id, id), `ix_approvals_target`(target_type, target_id, id).
- 컬럼 UPDATE 허용 목록(그 외 UPDATE·DELETE·TRUNCATE는 kbos_app에서 회수): `status, decided_by_id, decided_on_behalf_of_id, decided_delegation_id, decided_at, consumed_at, consumed_by_id, version, updated_at, updated_by_id`. → 스냅샷 컬럼(유형·대상·금액·digest·snapshot·required_role·approval_line_id·requested_by_id)은 **DB 권한으로 INSERT 이후 불변**.

**③ `approval_events`** — PkMixin+Base만(certification_status_log 서식), **IMMUTABLE_TABLES 등재+`revoke_mutations` 수기 호출**(§17.5 확장, ADR-0040 서식)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| approval_id | BIGINT FK approvals RESTRICT NOT NULL | |
| occurred_at | TIMESTAMPTZ NOT NULL server_default now() | |
| from_status | VARCHAR(12) NULL | 생성 이벤트만 NULL |
| to_status | VARCHAR(12) NOT NULL | |
| actor_user_id | BIGINT FK users RESTRICT **NOT NULL** | **시스템 행위자 없음** — 자동 결정 경로 부재의 DB 표현. 무효화도 "그 전표를 고친/취소한 실제 사용자"가 행위자 |
| on_behalf_of_user_id / delegation_id | BIGINT FK NULL | 대결 |
| reason | TEXT NULL | CHECK 길이 ≤1000 |
| reason_code | VARCHAR(20) NULL | VOIDED 전용 구조화 코드(C6) |
- CHECK: `from_status_valid`, `to_status_valid`, **`pair_allowed`**((from IS NULL AND to='REQUESTED') OR (from,to) IN(허용 7쌍) — 상태머신을 DB가 한 번 더 강제. SQL은 `machine.ALLOWED`에서 생성해 이중 정의 방지), `delegation_pair`, `actor_not_on_behalf`, **`reason_required`**(to_status NOT IN('REJECTED','WITHDRAWN') OR nullif(btrim(reason),'') IS NOT NULL), `void_code`((to_status='VOIDED')=(reason_code IS NOT NULL)), `reason_code_valid`(4값).
- 인덱스 `ix_approval_events_approval`(approval_id, id).

**④ `delegations`** — Pk+Timestamp+Version+Actor (**SoftDelete 제외** — 종료는 `revoked_at`) → 컬럼은 C5.

**table_policy 등재**: `IMMUTABLE_TABLES += approval_events`; `MUTABLE_TABLES += approvals, approval_lines, delegations`(사유 주석: 상태 UPDATE / 설정 편집·soft delete / 종료 UPDATE — 불변은 events가 담당). ADR-02 "soft delete·감사 컬럼 전 테이블"의 **예외 명문화**(approvals·delegations·events는 삭제 개념 없음 — V24 관행): **ADR 부기 필요**.

**⑤ 유형 추가 절차(후속 세션 규약, ADR에 명문)**: ①`ApprovalType`·`APPROVAL_TYPES`·`TYPE_TARGET`에 값 추가 ②`TargetSpec` 등록(C6) ③**CHECK 재정의 마이그레이션**(drop→create, `op.f()` 이름, 대상: approvals.approval_type·type_target_pair·target_type / approval_lines.approval_type / delegations.approval_type — 아키텍처 테스트가 "approval_type 컬럼을 가진 전 테이블의 CHECK == 열거"를 자동 대사) ④소비 게이트 테스트+`consumer_module` 소비 호출 존재 스캔 ⑤`approval_type` VARCHAR(30)이라 확폭 없음(예약 5종 중 최장 SOURCING_NON_RECOMMENDED=24자).

**(d) 4금 저촉** — 저촉 없음. 승인은 사람 결정의 기록이며 자동 승인 행 생성 경로가 없다(C8 강제). `SO_CREDIT_EXCEEDED`의 대상은 수주(SO) 확정이지 "발주(PO) 확정"(4금 ①)이 아니다. 지출·법적 판정·대외 발송·장부 확정 코드 0.

**(e) 상태·전이·불변** — C2·C6에 정의. 본 안건은 저장 골격과 DB 불변 3층(컬럼 권한·CHECK·IMMUTABLE 이력)을 확정한다.

**(f) 테스트 배분**
- K(아키텍처): ①`approval_type`·`target_type`·`status`·`approver_role`·`reason_code` CHECK ↔ StrEnum/상수 1:1(pg_get_constraintdef 파싱) ②`approval_type` 컬럼 보유 전 테이블 자동 대사 ③`test_every_approval_type_has_a_registered_target_spec`(공회전 방지 len≥1) ④table_policy 분류(4행: MUTABLE 3+IMMUTABLE 1) ⑤`test_no_unique_key_contains_a_cost_column` 통과+허용 목록 항목이 살아 있음(`test_the_allowlist_has_no_dead_entries`) ⑥금액 컬럼 BIGINT+CHAR(3) 쌍·Float 부재(기존 스캔 자동 편입) ⑦approvals 4테이블에 ASSIGNMENT 이름 컬럼 부재+users FK 컬럼이 "행위 기록 면제 목록"에 전수 등재(C8) ⑧alembic check 드리프트 0·왕복 downgrade/upgrade ⑨`_NEVER_SEEDED`에 4테이블 추가(마이그레이션 INSERT 0).
- 통합(K/J 관용 — 실 PG): 각 CHECK 위반 raw INSERT 실측 거부(sod·on_behalf_pair·decided_state·consumed_pair·basis>0·digest 형식), 활성 유니크(같은 대상 2번째 REQUESTED/APPROVED INSERT 실패 / REJECTED·WITHDRAWN·CONSUMED·VOIDED 뒤 재기안 성공), 결재선 임계 중복 거부·삭제 뒤 동일 임계 재등록 신규, **kbos_app가 approval_events UPDATE/DELETE/TRUNCATE → SQLSTATE 42501+`has_table_privilege` 전수**, **approvals 스냅샷 컬럼 UPDATE → 42501, 허용 컬럼 UPDATE 성공**.
- 변이 점검 대상: `pair_allowed`에서 한 쌍 삭제/추가, `sod` CHECK 제거, 활성 유니크 술어에서 APPROVED 제거, 컬럼 허용 목록에 `basis_amount` 추가.

**(g) 소비·등재**
- 소비: S3-4(`EXPENSE_OVER_THRESHOLD`), Phase 4(`DISPOSAL`·`STOCKTAKE_DIFF`)가 ⑤ 절차로 값 추가. `NEW_PARTNER`·`SOURCING_NON_RECOMMENDED`는 **소비 세션 미배정 → WBS 배정 공백 부채 등재**.
- 관찰: 금액 없는 유형(NEW_PARTNER 등)은 `basis_amount>0` CHECK·strict 초과 구간과 맞지 않을 수 있다 — 소비 세션이 재판정(트리거: 첫 비금액 유형 추가).
- ADR 필요: 승인 코어 계약 / ADR-02 예외 부기 / ADR-0018 ㉠ 부기.

**(h) 되돌리기 비용: 낮음~중간.** 유형 추가=값+CHECK 재정의 1건(ADR-0041 선례). 컬럼 추가는 additive. `target_label`·pair CHECK 제거는 마이그레이션 1건. approvals에 SoftDelete를 사후 추가하면 컬럼 추가+유니크 술어 재작성이라 중간.

---

## 안건 C2. "기안→승인 2단"의 표현 · 상태 6값 · 전이 7방향 · 재기안 · 만료 없음

**(a) 목적·경계**
- **2단 = 기안(요청 행위 1회) + 결정(결재 자격자 1명의 1회 결정)**. 검토·합의·다단·조건 분기는 구조적으로 불가(단계 컬럼·부모 컬럼·복수 결정 자리 없음). 기안은 별도 상태가 아니라 `request_approval()`이 REQUESTED 행+`NULL→REQUESTED` 이벤트를 만드는 행위다.
- 상태 6값(코드 고정 `machine.py`, VARCHAR(12)+CHECK+StrEnum): **REQUESTED(요청됨) / APPROVED(승인됨·미소비) / REJECTED(반려) / WITHDRAWN(회수) / CONSUMED(소비됨=확정에 사용) / VOIDED(무효)**. 종결 4태 = REJECTED·WITHDRAWN·CONSUMED·VOIDED(탈출 전이 0).
- CONSUMED를 상태로 둔 이유: "APPROVED이면서 소비됨"을 불리언으로 두면 활성 유니크·결재함 필터·CHECK가 전부 복합 조건이 된다. 소비 세션이 상태 분기를 배우는 비용보다 술어 단순성이 크다(제안 fit의 '종결 속성' 안 기각).

**(b) DESIGN 근거** §2 "기안→승인 2단·승인 후 불변" / §1 비범위(동적 워크플로우 엔진 금지) / §17.1(상태+이력+이벤트 한 트랜잭션) / §17.5 확장 / ADR-11 / CLAUDE.md 함정 ⑨ / V8·V9.

**(c) 데이터 변경** — C1의 `approvals.status`·`approval_events` CHECK. 코드 상수(`machine.py`):
```
ALLOWED = { (REQUESTED,APPROVED), (REQUESTED,REJECTED), (REQUESTED,WITHDRAWN), (REQUESTED,VOIDED),
            (APPROVED,CONSUMED), (APPROVED,VOIDED), (APPROVED,WITHDRAWN) }   # len == 7
HUMAN  = {T1,T2,T3,T7}   # 사람 결정 통로 decide_approval
SYSTEM = {T4,T5,T6}      # 도메인 통로: void_for_target·consume_approval
TERMINAL = {REJECTED, WITHDRAWN, CONSUMED, VOIDED}
REASON_REQUIRED_TO = {REJECTED, WITHDRAWN}          # VOIDED는 reason_code 필수(C6)
```
| # | 전이 | 행위자 | 조건 |
|---|---|---|---|
| T1 | REQUESTED→APPROVED | 결재 자격자·수임자(사람) | C4 자격+SoD, 스냅샷 재검증(C6) |
| T2 | REQUESTED→REJECTED | 위와 동일 | **사유 필수** |
| T3 | REQUESTED→WITHDRAWN | 기안자 본인 또는 ADMIN | 사유 필수 |
| T4 | REQUESTED→VOIDED | 도메인 통로(대상 변경·취소·상한 초과) | reason_code |
| T5 | APPROVED→CONSUMED | **확정 트랜잭션 안에서만** | C6 |
| T6 | APPROVED→VOIDED | 도메인 통로 | reason_code |
| T7 | APPROVED→WITHDRAWN | 기안자 본인 또는 ADMIN | 사유 필수 — **잘못 내린 승인의 철회 통로**("승인 후 불변"은 결정 내용의 불변이지 소비 전 철회 금지가 아니다. 철회는 새 종결 기록이며 원 결정 컬럼은 남는다) |
- **재기안**: REJECTED·WITHDRAWN·VOIDED 뒤 재요청은 되돌림 전이가 아니라 **신규 approvals 행**(이력 보존·전이 최소). 종결 행 재활용·부활 금지(SoD 세탁 차단).
- **만료 없음**: 시간 기반 전이·스윕 없음. DESIGN 문면 없음+승인 유효성은 소비 시점 재검증(C6)이 담보(digest·상한·통화). 만료를 두면 "시스템이 승인 상태를 바꾸는" 자동 전이 표면만 는다.
- 사유 규칙: REJECTED·WITHDRAWN은 **공백 제거 후 1~1000자 필수** — 서비스 422(`APPROVALS.TRANSITION.REASON_REQUIRED`)+DB `reason_required` CHECK 이중.
- **단일 상태 대입 통로**: `approval.status` 대입은 `service._record_transition(session, approval, *, to, actor_user_id, reason=None, reason_code=None, on_behalf_of=None, delegation_id=None)` **1곳뿐** — 상태 UPDATE+`approval_events` INSERT+`outbox.publish('approvals.approval.<과거형>')`(payload=id·유형·대상·from/to·행위자만, **금액·digest 없음**)가 한 함수·한 트랜잭션. 생성 시 INSERT+초기 이벤트도 같은 함수. 스키마·PATCH에 status 필드 없음(extra=forbid).
- **실패도 커밋(함정 ⑨)**: 반려·회수는 정상 전이라 그대로 커밋. **거부된 결정 시도**(자격 없음·자기 승인)와 **stale 감지 VOID**는 UoW 안에서 기록·상태 변경 후 **UoW 밖에서 raise**(V9).

**(d) 4금** — 사람 전이 4방향은 전부 인증된 실 사용자의 행위. 시스템 전이 3방향(T4~T6)은 "승인을 새로 부여"하지 않는다(승인 부여 T1은 사람 전용). 자동 승인 경로 부재는 C8의 3중 강제.

**(e) 상태·전이·불변** — 허용 **7방향**(사람 4+도메인 통로 3), 미허용: 서로 다른 상태 순서쌍 30−7=**23** + 자기 전이 6 = 29쌍 전건 거부(409 `APPROVALS.TRANSITION.NOT_ALLOWED`, detail에 현재·시도 상태). 불변: 종결 4태 탈출 0, 도달성(모든 상태가 REQUESTED에서 도달), 비종결 상태(REQUESTED·APPROVED)는 탈출 전이 존재.

**(f) 테스트 배분**
- K(아키텍처, 인증 상태머신 테스트 계보): 허용 7·HUMAN 4·SYSTEM 3·종결 4 총수 고정, 도달성·탈출성, `REASON_REQUIRED_TO` ↔ CHECK 문면 대사, 유니크 술어 ↔ 종결 집합 대사(`models._ACTIVE_PREDICATE`가 REQUESTED·APPROVED만 포함), 상태 대입 스캔 `\.status\s*=(?!=)`이 service.py 밖 0건(+공회전 방지 자기검사), 요청 스키마에 status·approver·decided_* 필드 부재+전건 extra=forbid.
- H(서비스 전수, e2e는 대표 쌍): **6상태×3동사(APPROVE·REJECT·WITHDRAW)=18조합**을 실제 상태로 끌고 가 허용 4(REQUESTED×3 중 APPROVE·REJECT·WITHDRAW, APPROVED×WITHDRAW)는 성공, 14는 409. 시스템 3방향은 각 통로 함수로 성공+미허용 출발 상태 거부. 재기안: REJECTED 뒤 신규 요청 성공·이력 2건 병존. 회수 권한: 기안자 성공, 타 TRADE 403, ADMIN 사유 없으면 422. 승인 후 회수(T7) 후 확정 시도 → `REQUIRED`.
- J: 반려 트랜잭션 강제 롤백 시 이력·이벤트 0행(한 트랜잭션), 거부된 결정 시도가 audit 행을 남기고 상태 불변(실패도 커밋).
- 변이 점검: 허용표에서 T7 삭제, T5 출발을 REQUESTED로 변경, 사유 필수 조건 제거, 종결 집합에서 VOIDED 제거 → 반드시 실패.

**(g) 소비·등재** — S3-2·Phase 4 소비자는 status 열거를 읽지 않고 서비스 함수(consume·void)만 쓴다. 관찰 등재: "승인 만료 도입 여부 — 트리거: 미소비 APPROVED가 운영에서 누적되어 결재함이 오염될 때. 도입은 additive(expires_at+VOID 사유 코드 1개, 신규 전이 불필요)". 이벤트 6종(requested·approved·rejected·withdrawn·voided·consumed)은 P7 Slack 소비 자리.

**(h) 되돌리기 비용: 낮음.** 전이 추가=machine 표+`pair_allowed`·status CHECK 재정의 1건+테스트 총수 갱신. 만료 도입은 additive. 상태 값 삭제는 행 존재 시 위험 → 처음부터 최소로 잡았다.

---

## 안건 C3. 결재선 매핑 (`approval_lines`) — 구간·경계·통화·fail-closed·공급 경로

**(a) 목적·경계** — "승인유형×금액임계→승인 역할"의 결정적 선택 규칙과 운영 공급 경로를 확정한다. 결재선은 **'누가'만** 정한다. **승인이 필요한지는 도메인 게이트(여신 초과 판정)가 정하며, 결재선에 '이 금액 미만은 자동 통과' 의미는 없다**(자동 승인 경로 부재).

**(b) DESIGN 근거** §2 L37("매핑 테이블(설정화)") / ADR-11(임계·역할=데이터) / §14 ⑭ "결재선" 관리 화면 / §20 H "결재선 매핑" / fail-closed 계약 / 함정 ⑩(시드 금지) / r1 A10.

**(c) 데이터·규칙**
- 1행 = "이 (유형·통화)에서 금액이 `threshold_amount`를 **초과(strict >)**하면 `approver_role`이 결재한다"(계단식 구간). 임계 0 = 양(+)의 금액 전부. **경계 = 초과**(`basis > threshold`): DESIGN "임계 초과"의 직독, 임계와 같은 금액은 한 단계 아래 구간.
- **선택 규칙(단일 함수 `lines.resolve_line(session, *, approval_type, amount, currency)`)**: 후보 = 활성 행 중 유형 일치 ∧ `threshold_currency == currency`(정확히 같은 통화, **환산 없음**) ∧ `threshold_amount < amount`. 후보 중 임계 최대 1행. 동률은 DB 유니크가 배제(방어적 tie-break `id` 최소).
- **환산 배제 근거**: `fx_rates`·환율 규약(r1 A4·A5)이 WBS 미배정·미결이라, 승인 라우팅이 환율 원천에 종속되면 다른 묶음 결정이 뒤집힐 때 함께 뒤집힌다. 통화 결정·환산은 승인 코어 밖(도메인 게이트 — 여신 통화 기준). 같은 통화 비교는 결정이 뒤집힐 일이 없다.
- **매핑 없음 = fail-closed**: 후보 0행(유형·통화 행이 없거나 금액이 최소 임계 이하) → **요청 생성 불가** 422 `APPROVALS.LINE.NOT_CONFIGURED`(메시지: "결재선이 없어 승인 요청을 만들 수 없습니다. 관리자에게 결재선 등록을 요청해 주세요."), 대상 전표는 확정 불가 상태 유지, approvals 행 0.
- **역할 보유자 0명 / 자격자 공집합**: 요청 생성 시 자격자 집합(C4: 역할 보유 활성 사용자 ∪ 활성 ADMIN ∪ 유효 수임자, 기안자 제외)을 계산해 **공집합이면 422 `APPROVALS.APPROVAL.NO_ELIGIBLE_APPROVER`**(조용한 정체 금지). ADMIN은 항상 자격자라 실제 공집합은 "유일한 ADMIN이 기안자이고 매핑 역할 보유자가 없을 때"뿐.
- **결재선 변경은 진행 중 승인에 소급하지 않는다**: 요청 시점에 `required_role`·`approval_line_id`를 approvals에 **동결**. (제안 risk의 "결정 시점 재해소"를 기각한 이유: ⓐ§2 "승인 후 불변"과 충돌 — 이미 내린 승인이 설정 편집으로 소비 불가가 됨 ⓑ결재선을 편집할 수 있는 주체가 ADMIN뿐이고 ADMIN은 이미 모든 승인의 자격자라 '라우팅 낮춰 우회'의 이득이 없음 ⓒ정책 강화를 진행 건에 적용하려면 회수 후 재요청이라는 명시 경로가 있음). 회수는 기안자 또는 ADMIN(C2 T3·T7).
- **수정 범위**: PATCH는 `approver_role`·`note`만(유형·통화·임계는 삭제+신규 — 매핑 계보의 정확성). 삭제 = soft delete(진행 중 승인이 FK로 참조해도 행이 남는다). 컬럼 UPDATE 권한으로 DB 강제(C6·X3).
- **초기 행 공급(함정 ⑩)**: 마이그레이션 시드·앱 기본값·CLI 시드 **모두 만들지 않는다**(임계 금액은 업무 정책 — 코드가 지어낼 수 없다). 공급 경로는 **ADMIN 결재선 관리 화면/API 하나**. 운영 개시 절차(runbook)에 "결재선 등록(SO_CREDIT_EXCEEDED, 사용 통화별, **임계 0**, 역할 지정)"을 1단계로 넣는다 — 등록 전 여신 초과 SO는 확정 불가(정상 fail-closed). `GET /approval-lines/coverage`가 서버 문구 안내를 준다: 등록 0행이면 "여신 초과 수주는 확정할 수 없습니다", 통화에 임계 0 행이 없으면 "이 통화는 최소 임계 이하 금액의 승인 요청이 불가합니다".
- **감사**: 생성·수정·삭제 전부 `audit.record` — `approvals.line.created|updated|deleted`, detail=행 id·유형·통화·전후 임계·전후 역할(원가·마진 아님), 변경과 같은 트랜잭션.
- **API**: `GET /api/v1/approval-lines`(ADMIN·TRADE·LOGISTICS·CERT, Page 기본 50, 함수명 `list_approval_lines`) / `GET /approval-lines/coverage`(같은 권한) / `POST`(ADMIN, Idempotency-Key) / `PATCH /{id}`(ADMIN, version) / `DELETE /{id}?version=`(ADMIN). 요청 스키마(extra=forbid): `approval_type`, `threshold`(사람 표기 Decimal `max_digits=15`, 최소단위 변환·자릿수 초과 422 — V20), `currency`(3자 대문자·`money.minor_units` 등록 통화), `approver_role`(4값), `note`(≤200). 응답은 `threshold_amount`(정수)+`threshold_currency`. 중복 임계는 서비스가 선검사 409 `APPROVALS.LINE.DUPLICATE`+DB 유니크 IntegrityError를 같은 코드로 매핑(500 금지), 동시 등록 경합은 DB 유니크가 최종 방어.
- 금액 파싱: `partners.service.parse_credit_limit`와 같은 3번째 사본이 생기므로 **`core/money.py`에 공용 `parse_minor_amount(raw, currency, *, field)` 추출을 권고**(X3 관찰 등재 — 기존 2곳 소급은 별도 판정).

**(d) 4금** — 저촉 없음(설정 데이터+선택 함수). 결재선이 새 **동작**(자동 승인)을 만들 수 없다는 점이 요점.

**(e) 상태·전이·불변** — approval_lines는 상태 없음. 불변: 임계·통화·유형 불변(UPDATE 권한), 삭제=soft delete, 변경 이력=audit_log.

**(f) 테스트 배분**
- H(핵심): 경계 3점(임계 1,000,000일 때 A=1,000,000→하위 구간/NOT_CONFIGURED, 1,000,001→해당) · 다중 구간(0→TRADE, 5,000,000→ADMIN: 5,000,000→TRADE·5,000,001→ADMIN) · **통화 불일치는 무시**(USD 요청에 KRW 행만→NOT_CONFIGURED, fx 모듈 임포트 스캔 0) · 행 0개/최소 임계 이하→422·approvals 0행 · 삭제된 행 무시 · **결재선 변경 후 진행 중 승인의 required_role 불변**(신규만 새 규칙) · 임계 동률 409/DB IntegrityError→409(500 금지) · VIEWER 역할 CHECK 거부 · audit 3종+같은 트랜잭션 롤백 시 0행 · 권한(TRADE 쓰기 403·VIEWER 읽기 403·비인증 401).
- K: 마이그레이션 직후 4테이블 행수 0 실측+`_NEVER_SEEDED` 스캔, 목록 페이지네이션 자동 스캔·`list_` 명명, `threshold_amount` 허용 목록 유효.
- J: 2스레드가 같은 (유형·통화·임계)를 동시 등록 → 1건만 성공.
- 변이 점검: `ORDER BY threshold DESC`→ASC, `<`→`<=`, 통화 조건 삭제 → 경계·다통화 테스트가 실패해야 한다.

**(g) 소비·등재** — 소비: SO 게이트가 `request_approval` 안에서 `resolve_line` 호출. 이월: runbook 1단계(결재선 등록), P6 헬스체크 결측 5종에 "결재선 미설정" 편입 관찰, DESIGN §14 ⑭ 결재선 화면 배정 문장. 관찰: 통화별 행 등록 부담(사용 통화 소수) — 트리거: 환율 규약(A4·A5) 확정 시 환산 도입 재판정(스키마 변경 없음: `basis_currency` 이미 저장).

**(h) 되돌리기 비용: 중간.** 환산 도입=`resolve_line` 1함수+환율 계약 의존(스키마 무변경). 경계(strict→이상)는 함수 1줄+테스트+운영 데이터 재해석 주의(구간 의미 반전 시 재입력 필요 → 지금 확정). 시드 도입은 CLI 추가로 낮음.

---

## 안건 C4. 결재 자격 · 직무분리(SoD) · ADMIN 해석

**(a) 목적·경계** — "누가 결정할 수 있는가"의 단일 정의와 자기 승인 차단. §2 L37의 "관리자는 상시 통과"(문서보관소 문맥의 접근 가드)와 §20 H "승인 우회 차단(ADMIN 포함)"의 층 충돌을 해소한다.

**(b) DESIGN 근거** §2 L37 / §20 H / ADR-07(상급자=ADMIN 폴백) / §18.1(역할+소유권) / r1 D1 / V1.

**(c) 규칙(서비스 판정, DB CHECK가 마지막 방어선)**
1. **자격 = 역할 기반(지정 사용자 컬럼 없음)**. 결정 시점 자격자 = ①활성 계정 ∧ `required_role` 보유자 ②**활성 ADMIN 전원**(모든 승인 유형·구간의 자격자 — 결재선 0행·역할 보유자 0명 상황에서 결재가 영구 정체하지 않게) ③`required_role`·유형에 대해 오늘(KST) 유효한 대결 수임자(C5). 모두 **요청자 본인 제외**.
2. **SoD: 기안자 ≠ 결정자, 예외 없음 — ADMIN도 자기 기안을 승인·반려할 수 없다**(403 `APPROVALS.DECISION.SELF_APPROVAL`). 대결 경유일 때 **수임자 ≠ 기안자 ∧ 위임자 ≠ 기안자**. 그 밖의 무자격(VIEWER·무관 역할·대결 없음)은 403 `APPROVALS.DECISION.NOT_APPROVER`. 둘 다 `approvals.decision.denied` audit 기록 후 커밋·예외(실패도 커밋). DB: `approvals.sod`·`on_behalf_not_requester`.
3. **ADMIN 정리**: §2 "상시 통과" = 역할 가드(엔드포인트 진입). §20 H "승인 우회 차단" = 업무 게이트. 층이 달라 충돌하지 않는다. 결론: **ADMIN도 승인 기록(CONSUMED로 전이할 APPROVED 행) 없이 여신 초과 SO를 확정할 수 없다.** `consume_approval`·`decide_approval` 시그니처에 `force`·`override`·`admin` 파라미터가 존재하지 않는다(아키텍처 테스트).
4. **단일 판정 함수** `authority.decision_authority(session, *, actor, approval, today=None) -> Authority(kind: OWN|ADMIN|DELEGATED|DENIED_SELF|DENIED_NOT_APPROVER, delegation_id, on_behalf_of_id)`. 결재함·`can_decide`·결정 API·알림 수신자가 **같은 SQL 술어 조각**(`_eligible_clause`)을 공유한다 — Python/SQL 이중 정의를 만들지 않는다. 대결 탐색은 직접 자격이 없을 때만, 유효 대결이 여럿이면 `id` 최소(결정적). `today`는 `core.time.today_kst()` 기본, 테스트 주입.
5. 결정 엔드포인트 역할 게이트는 `require_roles(TRADE, LOGISTICS, CERT)`(+ADMIN 자동) — **VIEWER 전면 403**(여신 초과액 노출 재판정 E2를 열지 않는 가장 좁은 결정 — 다른 묶음이 비마스킹으로 확정해도 유효).
6. **1인 ADMIN 회사**: 기안자가 유일한 자격자면 결정 불가가 **의도된 정상 동작**이다. 해소책은 제2 결재 자격 계정(다른 ADMIN 또는 매핑 역할 보유 계정)이며 runbook에 명기한다. **오너 판정 후보 1순위**로 PROGRESS에 등재(완화 옵션과 되돌리기 비용은 아래 (h)).

**(d) 4금** — 4금 ④(장부 확정)·① 어느 것도 아님. 오히려 "승인 없는 확정 통로" 부재가 4금 정신(사람 판단 필수)을 지킨다.

**(e) 상태·전이·불변** — 자격은 상태가 아니라 조회 시점 계산값. 결정 트랜잭션 안에서 **잠금 후 재계산**(목록에서 보였던 사람이 그 사이 비활성/역할 변경될 수 있음 — TOCTOU).

**(f) 테스트 배분**
- H(핵심, "승인 우회 차단"): ①ADMIN이 승인 없이 여신 초과 SO 확정 → 422 `REQUIRED`·상태 불변·`approvals.approval.bypass_blocked` audit ②승인 후 같은 ADMIN이 확정 성공(양방향 자기검사) ③**자기 승인 4경로 전부 403**: 일반 TRADE 기안자·ADMIN 기안자·수임자=기안자·위임자=기안자 ④VIEWER·(TRADE 행에서 LOGISTICS)·대결 없는 타 역할 → NOT_APPROVER ⑤ADMIN 기안+다른 ADMIN 승인 성공 ⑥유일 ADMIN 기안+자격자 0 → 요청 422 NO_ELIGIBLE_APPROVER ⑦`require_roles`(ADMIN 통과) 경로로 결정 API 호출해도 서비스 SoD가 막는지 e2e ⑧결재함 노출 ⇔ 결정 가능 동등성(무작위 조합 100건, 결재함 SQL vs `decision_authority`) ⑨결정 트랜잭션 중 승인자 비활성화 경합 → 거부(스레드 인터리브).
- K: `decide_approval`·`consume_approval` 시그니처에 force/override/admin_bypass 파라미터 부재(AST), `require_roles`만으로 결정 자격을 판정하는 라우터 부재.
- 변이 점검: ADMIN 분기를 무조건 통과로, SoD 비교 삭제, 위임자 활성 확인 삭제 → 실패해야 한다.

**(g) 소비·등재** — 소비: 결재함·알림 수신자(C7)·P7 Slack(같은 함수 재사용). 관찰 등재: "대상 **작성자**≠결재자 SoD 확장(요청자와 작성자가 다른 3인 시나리오) — 트리거: 오너 요구 또는 실운영에서 요청자≠작성자 결재가 관찰될 때. 추가는 nullable 컬럼+CHECK 1건". **오너 판정 후보**: 1인 운영 시 자기 승인 허용 모드(기각 사유: §20 H·fail-closed).

**(h) 되돌리기 비용: 낮음~중간.** SoD 완화(ADMIN 자기 승인 허용)는 서비스 술어 1줄+`sod`·`on_behalf_not_requester` CHECK 재정의 마이그레이션+ADR 갱신이며 §20 H를 약화하는 방향이라 **오너 명시 판정 필요** — 이 안건이 사후 번복 가능성이 가장 높다(1인 운영 현실). 지정 결재자 컬럼 도입은 additive(단 handover 스캔 이름 확장 동반 — 비권장).

---

## 안건 C5. 대결(`delegations`) — 범위·기간·중첩·종료·이력·수임자 선택

**(a) 목적·경계** — "승인자 부재 시 대결(위임 — 기간·범위·이력 기록)"의 저장·유효 판정·수명 규칙. 위임은 **개인 결재 권한의 임시 위탁**이며 담당 이관(handover)과 다른 개념이다.

**(b) DESIGN 근거** §2 L37 / §20 H "대결 기간 유효+이력" / §18.1(권한 변경 audit) / §17.5 확장(사용 이력 불변) / r1 A11 / r2 B4·Q7 / V17·V22.

**(c) 데이터·규칙**
- 테이블 `delegations`(Pk+Timestamp+Version+Actor, SoftDelete 없음):
  `delegator_user_id`·`delegate_user_id` BIGINT FK users RESTRICT NOT NULL / `approval_type` VARCHAR(30) NOT NULL(CHECK) / `delegated_role` VARCHAR(20) NOT NULL(CHECK 4값) / `start_on`·`end_on` DATE NOT NULL(KST 달력 날짜, 양끝 포함) / `note` VARCHAR(200) NULL / `revoked_at` TIMESTAMPTZ NULL / `revoked_by_id` BIGINT FK users NULL.
  CHECK: `parties_differ`, `period_order`(end_on>=start_on), `revoked_pair`((revoked_at IS NULL)=(revoked_by_id IS NULL)), `note_not_blank`. 유니크 `uq_delegations_start_active` (delegator_user_id, approval_type, delegated_role, start_on) WHERE revoked_at IS NULL(더블클릭·재전송 DB 방어망). 인덱스 `ix_delegations_delegate`(delegate_user_id, start_on, end_on) WHERE revoked_at IS NULL, `ix_delegations_delegator`(delegator_user_id).
  컬럼 UPDATE 허용: `revoked_at, revoked_by_id, version, updated_at, updated_by_id` — **기간·당사자·범위는 DB가 불변으로 강제**(이력 위변조 차단).
- **범위 = (승인 유형 1개, 위임하는 결재 역할 1개)**. 역할로 한정하는 이유: 위임자가 ADMIN이어도 수임자가 받는 권한은 그 역할 계단(예: TRADE 구간)까지이고 ADMIN 전권이 아니다(제안 fit/risk의 '유형만' 범위는 ADMIN 위임 시 전 구간 권한 이양 = 권한 초과). 유형을 함께 명시하는 이유: 새 승인 유형이 추가돼도 기존 위임이 **조용히 확장되지 않는다**(fail-closed). 금액 상한 컬럼은 없다(금액 구간은 결재선 역할 계단이 이미 표현 — 이중 정의 방지).
- **위임자 권한 초과 금지**: 위임자는 `delegated_role`을 본인 자격으로 보유하거나 ADMIN이어야 한다. **등록 시와 결정 시 두 번 검증**. **재위임 불가**: 자격은 위임받은 권한이 아니라 본인 역할 보유로만 인정하므로 A→B→C 사슬이 구조적으로 불가.
- **수임자 조건**: 활성 계정 ∧ 조회(VIEWER) 단독이 아님(ADMIN·TRADE·LOGISTICS·CERT 중 1개 이상). 수임자=기안자는 등록 시점이 아니라 **결정 시점** SoD(C4)에서 건별로 막는다(사전 설정이라 기안을 모른다).
- **기간**: `start_on >= today_kst()`(**소급 등록 금지** — 권한을 과거로 부여하는 기록 금지), `end_on >= start_on`, 상한 일수 제한 없음(DESIGN 문면 없음 — 관찰).
- **유효 = 조회·결정 시점 계산값**(agency_contracts `is_current` 계보, 스윕 잡 없음): `revoked_at IS NULL ∧ start_on ≤ 오늘(KST) ≤ end_on ∧ 위임자·수임자 활성 ∧ 위임자가 지금도 delegated_role(또는 ADMIN) 보유 ∧ 수임자가 비조회 역할 보유`. **계정 비활성·역할 상실은 즉시 무효로 계산된다(fail-closed)** — identity→approvals 콜백 훅을 만들지 않는다(순환 임포트+TOCTOU 창). 재활성 시 남은 기간이 있으면 다시 유효해지는 점은 **수용·문서화**하고, 화면이 계산 사유를 표시한다: `state ∈ {ACTIVE, UPCOMING, EXPIRED, REVOKED, INERT}` + INERT 사유 코드 `delegator_inactive|delegate_inactive|delegator_lost_role|delegate_no_role`.
- **중첩 금지**: 같은 (위임자, 유형, 역할)에 **미종료 대결과 기간이 겹치는** 새 대결은 409 `APPROVALS.DELEGATION.OVERLAP`(겹침 = `new.start<=old.end ∧ old.start<=new.end`, 종료된 행 제외). 직렬화는 **위임자 users 행 `SELECT … FOR UPDATE`** 후 서비스 검사(btree_gist 미도입 — DB EXCLUDE 불가). 다른 역할·다른 위임자와는 병존.
- **조기 종료**: `POST /delegations/{id}/revoke {version}` — 위임자 본인 또는 ADMIN, `revoked_at/by` 기록, 이미 종료·기간 만료 건은 409 `APPROVALS.DELEGATION.NOT_ACTIVE`. **기간 수정 API 없음**(종료 후 신규 등록 — 이력 단순화, PATCH 라우트 부재를 테스트).
- **등록 권한**: 위임자 본인(비조회 역할 보유) 또는 ADMIN(부재자 대리 등록, 행위자 기록). 위임자 미지정 시 본인. 서비스가 판정.
- **이력**: 사용 이력 = `approvals.decided_by_id`(수임자)·`decided_on_behalf_of_id`(위임자)·`decided_delegation_id` + `approval_events`의 같은 3값. 설정 이력 = audit `approvals.delegation.created|revoked`(detail=위임자·수임자·역할·기간). `delegations`는 MUTABLE(revoke UPDATE)이지만 컬럼 권한으로 종료 필드만 변경 가능, FK RESTRICT라 사용된 대결 행은 삭제 불가. **대결 사용 시 위임자에게 알림 1건**(`approval:{id}:delegated`, 위임자 모르는 행사 방지 — fail-visible).
- **수임자 선택 디렉터리**: `/users`는 관리자 전용 유지. **`GET /api/v1/approvals/delegation-candidates?q=`**(비조회 4역할, Page 기본 50·상한 100) — 응답은 `{id, display_name}` 두 키뿐(이메일·역할·활성 사유·잠금 미노출), 활성이며 비조회 역할 보유자, 본인 제외. 인증 뷰 `assignee_name` 전 역할 표시 선례와 같은 수준.
- API: `GET /api/v1/delegations?scope=mine|all`(all=ADMIN, Page) / `POST /api/v1/delegations`(Idempotency-Key; `delegate_user_id, approval_type, delegated_role, start_on, end_on, note?, delegator_user_id?`[ADMIN만]) / `POST /{id}/revoke`. 검증 오류는 `VALIDATION_INVALID_FIELD`+필드별 안내(신규 코드는 OVERLAP·NOT_ACTIVE 2종만).

**(d) 4금** — 저촉 없음. 대결은 권한을 사람 사이에서 옮기는 것이며 시스템이 결정하지 않는다.

**(e) 상태·전이·불변** — 저장 상태는 revoked 유무뿐, 유효성은 계산값. 불변: 당사자·기간·범위(컬럼 권한), 사용된 행 삭제 불가(FK).

**(f) 테스트 배분**
- H("대결 기간 유효+이력"): ①**경계일 포함**(고정 today 주입: start_on 당일·end_on 당일 유효, end_on+1·start_on−1 무효) ②**UTC/KST 갈림**(UTC 2026-10-14 15:30 = KST 15일 00:30에서 end_on=14일 대결 무효·15일이면 유효) ③기간 밖·미래 대결로 결정 시도 → 403 NOT_APPROVER ④수임자 결정 성공 시 approvals·events 동일 3값+위임자 알림 ⑤위임자 권한 초과(TRADE가 ADMIN 위임)→422·ADMIN이 TRADE 위임→성공·결정 시점 위임자 역할 회수 후 사용 불가 ⑥수임자=위임자 CHECK·수임자=기안자 결정 403·위임자=기안자 시 그 대결로 결정 403 ⑦**재위임 불가**(A→B, B→C 후 C가 A 몫 결정 403) ⑧**중첩**: 겹침 409·인접(end+1=start) 성공·종료된 행과 겹침 성공·다른 역할 병존 ⑨조기 종료 후 즉시 무효·이미 종료 409·타인 종료 403 ⑩위임자/수임자 비활성 즉시 무효·재활성 시 유효 복귀(문서화된 동작 확인) ⑪소급 start_on<오늘 422 ⑫VIEWER 등록 403·VIEWER 수임자 422 ⑬디렉터리 응답 키 집합이 정확히 `{id, display_name}`·VIEWER 403·비활성·본인 제외 ⑭사용된 대결 행 삭제 불가(FK RESTRICT)·raw UPDATE로 기간 변경 시 42501.
- J: 같은 위임자·유형·역할 동시 등록 Barrier → 정확히 1건 성공.
- K: 목록 페이지네이션·`list_` 명명·PATCH/PUT 라우트 부재.
- e2e 대표: 대결 경유 승인 관통(등록→수임자 결재→확정 성공). 프런트 vitest: KST 오늘 기본값·시작≤종료 검증·상태 배지·종료 뮤테이션.
- 변이 점검: 기간 부등호 `<=`→`<`, 위임자 활성 확인 삭제, `delegated_role` 일치 조건 삭제 → 실패해야 한다.

**(g) 소비·등재** — 소비: C4 자격 함수·C7 수신자. 이월/관찰: 대결 기간 상한(현재 없음) — 트리거: 장기 위임 남용 관찰; 재활성 시 유효 복귀 수용 사유 ADR 명기; identity 계정 비활성 화면에 "진행 중 대결 N건" 표시는 미구현(관찰).

**(h) 되돌리기 비용: 중간.** 다중 유형/금액 상한은 nullable 컬럼 추가+유효 술어 확장(하위 호환). 90일 상한 도입은 CHECK 1건. 자동 종료 훅 도입은 identity→approvals 의존 신설(비권장). 소급 허용은 서비스 1줄이나 감사 문제로 처음부터 금지.

---

## 안건 C6. "승인 후 불변" · 스냅샷 · 1회 소비 · 무효화 · 락 순서

**(a) 목적·경계** — "승인 후 불변"이 무엇을 지키는지 4대상(approvals 행·이력·대상 전표·결재선)으로 정의하고, 승인의 **1회 소비**와 **대상 변경 시 재승인**의 통로를 확정한다.

**(b) DESIGN 근거** §2 L37 "승인 후 불변" / §20 H "승인 우회 차단·승인 후 불변" / §17.1(확정+승인 소비 한 트랜잭션) / §17.2(확인→기록 직렬화=행 잠금) / §17.5 / ADR-05 / r2 D2·D4 / S1-3 PR-3 ① 교훈(부모 version이 자식 라인 변경을 못 잡음) / V9·V10.

**(c) 규칙**
- **4대상의 불변**: ①`approvals`: 스냅샷 컬럼(C1 목록)은 INSERT 이후 **DB 컬럼 권한으로 UPDATE 불가**, 상태·결정·소비 컬럼은 `_record_transition` 1통로만 변경, 삭제 불가(DELETE 권한 회수+events FK RESTRICT), 수정·삭제 엔드포인트 없음. ②`approval_events`: IMMUTABLE. ③대상 전표: 승인(REQUESTED·APPROVED) 상태에서 대상이 바뀌면 그 승인은 **무효(VOIDED)** — 재승인 필요(편집 금지가 아니라 "승인 자동 무효화 후 편집 허용" — 요청 중 편집으로 화면이 막혀 정체하는 것보다 안전). ④결재선: 요청 시점 동결(C3). ⑤대결: 수정 없음, 종료만(C5).
- **승인 시점 스냅샷** = `TargetSnapshot(label, digest, amount, currency, detail, gate_open)`. `digest` = sha256(정규화 JSON) — 대상 모듈(SO 묶음)이 정의하는 **게이트 판정 입력 동결 필드 집합**(바이어·통화·환율 스냅샷·라인별 SKU·수량·단가; **메모·담당자·version 제외**). **version 비교를 쓰지 않는다**: 라인만 바뀌고 헤더 version이 안 오르는 결함 유형(S1-3 PR-3 ①)에 무력하고, 메모 수정 같은 무관 변경으로 재승인이 생겨 우회 압력을 만든다. `amount`·`currency` = **현재 승인이 필요한 금액**(SO_CREDIT_EXCEEDED: 바이어 여신 통화 기준 초과분, 산식·평가 불능 처리=E 묶음 — 평가 불능이면 spec이 자기 `AppError`를 던져 fail-closed). `gate_open` = 대상이 아직 게이트 대상 상태인가(확정 전·미취소).
- **TargetSpec 레지스트리**(승인 모듈은 orders를 임포트하지 않고 소비 모듈이 등록): `TargetSpec(approval_type, target_type, consumer_module, snapshot(session, target_id, *, lock: bool) -> TargetSnapshot | None)`. `lock=True`는 **대상 행 → 대상이 정한 직렬화 잠금(거래처 행 등)** 을 확정 통로와 같은 순서로 잡고, 대상이 없거나 soft-deleted면 None(→404 또는 무효).
- **1회 소비** `consume_approval(session, *, approval_type, target_id, actor_user_id) -> ConsumeResult(outcome: CONSUMED|NOT_REQUIRED|BLOCKED, approval_id, error: AppError|None)` — SO 확정 트랜잭션 **안에서**(대상 행 잠금 후) 호출:
  1. `spec.snapshot(lock=True)` — None/`gate_open=False` → 활성 승인 VOID(TARGET_CANCELLED) → BLOCKED(STALE).
  2. `amount ≤ 0`(더 이상 여신 초과 아님) → 활성 승인이 있으면 VOID(NOT_REQUIRED) → **NOT_REQUIRED**(확정 진행 가능, 승인 불필요).
  3. 활성 APPROVED 행을 `FOR UPDATE` — 없으면 `approvals.approval.bypass_blocked` audit 기록 → BLOCKED(`APPROVALS.APPROVAL.REQUIRED` 422, detail에 REQUESTED 대기 여부 안내).
  4. `digest`·`currency` 불일치 → VOID(TARGET_CHANGED) → BLOCKED(STALE 409). `amount > basis_amount`(승인 상한 초과 — D4) → VOID(CAP_EXCEEDED) → BLOCKED(STALE) (재요청은 새 금액으로 결재선을 다시 해석 — 더 높은 역할 요구 가능).
  5. 통과 → APPROVED→CONSUMED(`consumed_at/by`, 이벤트). **ADMIN 포함 예외 없음.**
  - **호출 규약(함정 ⑨·V10)**: `consume_approval`은 **예외를 던지지 않고** 결과를 돌려준다. VOID·감사를 이미 기록했으므로 호출자(SO 확정 서비스)는 `BLOCKED`면 **UoW를 정상 종료(커밋)한 뒤 `raise result.error`**. UoW 안에서 raise하면 VOID·감사까지 롤백되어 "재시도해도 같은 결과"가 사라진다. 소비된 승인은 CONSUMED 종결이고 활성 유니크에서 빠지며 target_id 고정이라 다른 대상에 재사용 불가.
- **무효화 훅** `void_for_target(session, *, approval_type, target_id, actor_user_id, reason_code) -> int`: SO **수정(게이트 입력 필드)·취소** 서비스가 **같은 트랜잭션에서** 호출(활성 승인 없으면 무동작). 훅이 빠진 경로(임포트·벌크)가 있어도 **지연 검증(lazy)** 이 백스톱: 결정(APPROVE) 시점과 소비 시점에 digest·통화·상한·`gate_open`을 다시 본다. 훅=결재함 위생(eager), 지연=fail-closed 안전망 — 둘 다 둔다(eager만이면 훅 누락이 조용한 fail-open, lazy만이면 죽은 승인이 결재함에 쌓임). VOID 사유 코드 4종: `TARGET_CHANGED|TARGET_CANCELLED|CAP_EXCEEDED|NOT_REQUIRED`(events.reason_code).
- **요청 생성 시 기존 활성 승인 처리**(`request_approval`, 대상 잠금 후): (digest, amount, currency)가 모두 같으면 **기존 행 그대로 반환**(멱등 재요청 — 알림·이벤트 재발생 없음), 하나라도 다르면 기존을 VOID(TARGET_CHANGED) 후 신규 생성. 동시 요청은 대상 행 잠금이 직렬화하므로 `ALREADY_ACTIVE` 409는 안전망(IntegrityError 매핑)일 뿐 정상 경로에서 도달 불가.
- **결정(APPROVE) 시점 재검증**: digest·통화 불일치→VOID(TARGET_CHANGED)+409 STALE, `amount>basis_amount`→VOID(CAP_EXCEEDED)+STALE, `amount≤0`→VOID(NOT_REQUIRED)+STALE("더 이상 승인이 필요하지 않습니다"). 반려·회수는 재검증 없이 항상 허용.
- **락 순서 규약(교착 방지, 전 경로 동일)**: (0)멱등 키 행 → **(1)대상 행 → (2)대상이 정한 직렬화 잠금 → (3)approvals 행**. `decide_approval`은 승인 id로 대상 id를 무잠금 조회 → 대상 잠금 → approvals `FOR UPDATE` → status·version 재확인. 소스 스캔 테스트가 결정·소비·무효 경로의 순서를 고정.
- **승인 유효기간 없음**(C2) — 무효 사유는 전부 사건 기반.

**(c') 컬럼 권한 메커니즘(신규 공용 헬퍼 — X3)** `restrict_update_columns(op, table, allowed_columns)`: `REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public."t" FROM kbos_app` 후 `GRANT UPDATE ("c1","c2",…) ON TABLE public."t" TO kbos_app`. 레지스트리 `COLUMN_UPDATE_ALLOWLIST: dict[str, frozenset[str]]`(approvals·approval_lines·delegations) in `table_policy.py`. GRANT는 autogenerate가 못 보므로 마이그레이션에서 수기 호출(`revoke_mutations`와 같은 규율). **실측 테스트 필수**(권한 상호작용: `ALTER DEFAULT PRIVILEGES`·왕복 마이그레이션).

**(d) 4금** — 소비·무효화는 승인 상태를 **좁히는** 방향(권한 축소)이다. 시스템이 승인을 부여하는 경로는 없다. VOID·CONSUME은 도메인 통로가 인증된 실 사용자의 행위(확정·편집·취소) 안에서만 일어난다.

**(e) 상태·전이·불변** — T4·T5·T6 시스템 통로(C2). 불변 3층(컬럼 권한·CHECK·IMMUTABLE 이력)+서비스 단일 통로+아키텍처 스캔(스냅샷 컬럼 UPDATE 문 부재).

**(f) 테스트 배분**
- H("승인 후 불변"·"승인 우회 차단"): ①승인 후 라인 수량/단가 변경(서비스 경유) → 소비 시 STALE 409+VOIDED(TARGET_CHANGED)·SO 미확정 ②**version을 올리지 않는 raw UPDATE로 단가 변경 → digest 불일치로 소비 거부**(훅 부재 백스톱 실증 — 이 테스트가 lazy 층을 대표) ③메모만 변경 → 소비 성공(무관 변경 비무효) ④승인 후 노출 증가로 초과분>상한 → STALE(CAP_EXCEEDED)·재요청 시 새 금액 기준 결재선 재해석 ⑤노출 감소로 초과분 0 → NOT_REQUIRED(미소비 승인이 void되어 활성 0) ⑥**소비 1회**: 같은 승인으로 2번째 확정·다른 SO 재사용 시도 → REQUIRED ⑦소비 실패(BLOCKED)가 **호출자 커밋 후 raise**되어 VOID·감사가 남는다(중첩 UoW 계약) ⑧요청 재전송(같은 스냅샷)=기존 행 반환·스냅샷 변경 후 재요청=stale VOID+신규 ⑨승인 후 SO 취소 → `void_for_target`로 결재함에서 사라짐 ⑩eager 훅 누락 시나리오에서도 lazy가 막는지 ⑪스냅샷 digest 골든 벡터(정규화 순서·NFC·정수 문자열 고정 — SO 묶음 소유, 승인 코어는 입력 계약 테스트) ⑫`spec.snapshot`이 평가 불능(AppError)이면 통과 취급 없이 소비 실패.
- J(동시성·안전 계약, Barrier 실동시): 동시 확정 2요청+승인 1건 → 정확히 1건 CONSUMED·다른 쪽 REQUIRED / **동시 '승인'과 'SO 수정'** → 교착·500 없이 [승인→무효] 또는 [무효→승인 불가] 중 일관 / 두 결재자 동시 승인·반려 → 정확히 1건 성공·이력 1행 / 확정 트랜잭션 중간 실패(소비 후 예외 주입) → 승인 여전히 APPROVED(롤백 실측) / **락 순서 소스 스캔**(대상→approvals).
- K(아키텍처): 스냅샷 컬럼 UPDATE 스캔 0(공회전 방지 자기검사 — `session.execute(update(Approval)…)`·`__table__.update`·raw `UPDATE approvals` 패턴 포함), `has_column_privilege` 전수 실측, 소비 접점 스캔(`TargetSpec.consumer_module` 소스에 `consume_approval(` 존재 — **소비 없는 spec 금지**, 소비 호출 PR과 같은 PR에 편입 — X2), snapshot detail에 민감 키 부재.
- 변이 점검: digest 비교 삭제, 상한 비교 삭제, consume의 status 술어 삭제, 락 순서 역전, 컬럼 허용 목록에 `snapshot_digest` 추가 → 실패해야 한다.

**(g) 소비·등재** — 소비: SO 확정·편집·취소 서비스(B 묶음), 여신 산식(E 묶음)이 `spec.snapshot` 제공. 이월: digest 입력 필드 집합은 SO 묶음 확정 후 ADR에 부기, "락 순서 규약"을 S4-1 직렬화 ADR(G6)이 흡수·대체 규정. 관찰: `approvals.snapshot` 표시용 JSON 스키마 진화(키 추가는 호환, 판정에 쓰지 않음을 ADR에 명기).

**(h) 되돌리기 비용: 중간.** digest 입력 집합 변경은 SO 함수 1개+테스트(기존 미소비 승인은 재요청 필요). 컬럼 권한→트리거 교체는 마이그레이션 1건. 상한 정책을 "금액 동일 요구"로 조이는 것은 비교식 1줄. 유효기간 도입은 additive.

---

## 안건 C7. 알림 · 내 결재함 · 정체 독촉 잡

**(a) 목적·경계** — 승인 요청이 **조용히 정체하지 않게** 하는 fail-visible 경로: ①요청·결과 알림 ②알림과 무관한 서버 계산 결재함 ③정체 독촉 잡. 알림은 내부(알림센터)뿐 — 대외 발송 없음.

**(b) DESIGN 근거** ADR-07(담당자 라우팅 기본·전사 폭포 금지) / ADR-0045(규칙 0행이면 일반 이벤트 미발송 — 기일 계열만 폴백) / §17.1(알림 INSERT는 외부 호출이 아님) / §20 H "정체 N일 독촉"(계열) / r2 B3·Q6 / V3·V4·V15·V19.

**(c) 규칙**
- **요청 알림 = `notifications.notify()` 직접 호출**(아웃박스 비경유 — 기일 엔진·정체 스캔 선례, 요청 트랜잭션 안에서: "승인 행이 있으면 알림도 있다"). `Routing`은 3값 유지(테스트 고정). 수신자마다 `notify(assignee_id=uid, routing=Routing.DEADLINE, severity='WARN', entity_type='approvals', entity_id=approval_id, subject_key=f'approval:{id}:requested', event_type='approvals.approval.requested')`. **DEADLINE을 쓰는 이유**: 수신자를 이미 활성으로 골라 넘기므로 폴백은 평시 발동하지 않고, 넘긴 뒤 비활성이 되는 경합에서만 ADMIN 폴백으로 관통(fail-visible; EVENT였다면 그 경우 무발송). `alert_rules` 0행이어도 발송된다.
- **수신자 결정 `authority.notification_recipients(session, approval, today) -> (list[int], fallback: bool)`**: ①`required_role` 보유 활성 사용자 ∪ 오늘 유효한 그 (유형·역할) 수임자(`required_role=ADMIN`이면 ADMIN 전원) − 기안자 − (위임자=기안자인 수임자) ②①이 공집합이면 **활성 ADMIN 전원(기안자 제외)** 폴백, 본문에 "결재 역할 보유자가 없어 관리자에게 전달되었습니다" 문구(코어 `FALLBACK_NOTE`는 "규칙이 없어서" 뜻이라 쓰지 않는다). ADMIN은 역할 보유자가 있으면 수신 제외(전사 폭포 금지) — 단 결재함에는 항상 보인다.
- **결과 통지**: 승인·반려·무효(외부 트리거)·회수(ADMIN이 한 경우만)를 기안자에게 `approval:{id}:{approved|rejected|voided|withdrawn}` 1건(기안자 비활성이면 생략). 대결 사용 시 위임자에게 `approval:{id}:delegated`. 소비(CONSUMED)는 확정 사실이 곧 통지라 알림 없음. 제목은 `ALERT_TITLE_MAX`(200) 절단 재사용. **알림 본문·제목에 금액·이메일·원가 미기재**(확장 시 누출 방지). dedup 키 꼬리 = 수신자 id(코어 부착, handover 재작성 호환 — V15).
- **알림 비의존 가시성**: `GET /approvals?scope=inbox`(결재 자격 있는 REQUESTED, 기안자 제외·요청 오래된 순, Page 기본 50)+`GET /approvals/inbox-count`(셸 배지 — 알림 unread-count 선례). 알림이 없거나 늦어도 결재함에는 뜬다. 알림→화면 이동: `entity_type='approvals'`를 `/approvals`로 매핑(프런트 신설 — 현재 alerts.tsx는 평문 표기).
- **정체 독촉 잡** `approval-stagnation-scan`, **`daily@07:10` KST**(기일 스캔 06:30·정체 스캔 07:00 뒤, 브리핑 09:00 앞 — 브리핑 "미확인 알림 집계"에 독촉분 포함), JOB_REGISTRY 8행째. REQUESTED 승인 중 경과일(KST 달력 기준 `created_at` 날짜 차) ≥ N이면 요청 알림과 **같은 수신자 결정**으로 독촉. **N 기본 2일**(결재가 전표 확정을 막는 업무라 인증 정체 7일보다 짧게 — 운영값이므로 `alert_rules` event_type `approvals.stagnation`의 `config.days`(정수)가 덮어쓰며 형이 흐리면 기본값 — 정체 스캔 선례). dedup `approval:{id}:stagnation#{k}`, k=경과일÷N의 몫(N일마다 1건, 스캔 누락 시 그 시점 몫 1건만 — 소급 없음). **건별 독립 트랜잭션·실패 1건이라도 있으면 잡 FAILED**(`_fail_if_any_failed`). 잡은 **알림만** 만들고 승인 상태·전표를 바꾸지 않는다(만료·자동 결정 없음). CLI `approval-stagnation-scan [--date]`(정체 스캔 CLI 선례). 잡·CLI는 `approvals.stagnation`만 임포트하고 **`decide/consume/void/request` 함수를 임포트하지 않는다**(C8 스캔).
- 아웃박스 이벤트 6종은 상태 전이 전건 발행(payload=id·유형·대상·from/to·행위자만, **금액·digest 없음**) — P7 Slack 소비 자리. 관리자가 `approvals.approval.requested` 규칙을 만들면 인앱 알림이 이중이 될 수 있다 → 요청 알림 정본은 직접 notify이고 규칙은 추가 채널용임을 ADR·runbook에 명기(관찰).

**(d) 4금** — 잡은 읽기+알림 INSERT뿐. 대외 발송 없음(이메일·슬랙 코드 0), 지출·법적 판정·장부 확정 아님 → 비저촉. `test_registered_jobs_stay_clear_of_the_four_bans`의 코드 집합에 `approval-stagnation-scan`(+비저촉 서술) 추가.

**(e) 상태·전이·불변** — 잡은 상태 전이를 하지 않는다(잡 전후 approvals.status 동일을 테스트가 고정).

**(f) 테스트 배분**
- H("정체 N일 독촉" 계열): 경과일 N 미만 미발송·N 이상 1건·같은 k 재실행 0건·2N 시점 k=2 신규·규칙으로 N 변경 반영·건별 실패 1건이면 잡 FAILED이고 성공 건 커밋·잡이 approvals.status를 바꾸지 않음.
- J: 요청 알림이 같은 트랜잭션(승인 생성 롤백 시 알림 0), 같은 요청 재전송 → 알림 중복 0.
- 알림(통합): ①`alert_rules` 0행에서 요청 → 수신자 알림 생성(핵심) ②수신자 집합(역할 보유자만·기안자 제외·비활성 제외·유효 수임자 포함·미유효 수임자 제외·위임자=기안자 수임자 제외) ③역할 보유자 0명→ADMIN 폴백+문구 ④결과 통지·기안자 비활성이면 미발송 ⑤결재함이 알림 삭제 후에도 자격자에게 보임·기안자·무자격자에게 안 보임·inbox-count 일치.
- K: `test_alerts_are_created_only_by_the_notification_core` 통과(Alert 직접 생성 0), 스케줄러 4금 집합·매핑·스케줄 파서 통과(8행), 알림 본문·outbox payload에 금액·digest·이메일 부재(G/K 마스킹 계열), 잡·CLI의 approvals 상태 변경 함수 비임포트.
- 변이 점검: dedup 키에서 k 삭제, 수신자 폴백 제거, 기안자 제외 삭제 → 실패해야 한다.

**(g) 소비·등재** — 이월: `JOB_REGISTRY` 8행 단언·DESIGN §21 [M2] 보강 ④ "레지스트리 7행" 문구 갱신, 프런트 `entity_type` 이동 매핑, 알림 이관(handover) 후 수신자 무자격 시 링크 403 가능(관찰), "승인 대기" 데일리 브리핑 줄 추가는 미도입(관찰 — 트리거: 결재함 미확인 방치 보고). 4금 비저촉 논증을 ADR에 남김.

**(h) 되돌리기 비용: 낮음.** N·수신자 정책은 함수/데이터 1개, 잡 폐기는 레지스트리 행+테스트 집합 수정. Routing 4번째 값(역할 직접 지정)은 K 테스트·ADR-0045 변경이라 비용이 크므로 채택하지 않는다.

---

## 안건 C8. 단일 결정 통로 · API · 권한 · 에러 코드 · 화면 4요소 · 이관 취급

**(a) 목적·경계** — 사람이 승인 상태를 바꾸는 통로를 **하나**로 만들고(P7 Slack 재사용), 자동 승인 경로 부재를 아키텍처 테스트로 강제하며, API·권한·에러·화면을 한 세트로 확정한다. 승인 **요청 생성은 HTTP로 노출하지 않는다** — 소비자 전표 엔드포인트가 `request_approval()`을 호출한다(임의 대상·금액의 승인 행 위조 표면 제거).

**(b) DESIGN 근거** §16 L294("슬랙 승인=시스템 승인 기록" → 단일 통로) / §15(자동화 4금·자동 승인 경로 부재) / §17.1·17.2·17.4 / §18.1·18.4 / §14 ⑭ / CLAUDE.md "화면 4요소(API+화면+권한+테스트)" / V1·V2·V9·V13·V16.

**(c) 규칙·계약**
- **`approvals.service.decide_approval(*, approval_id, actor: AuthenticatedUser, verb: Literal['APPROVE','REJECT','WITHDRAW'], reason: str|None, version: int, idempotency_key: str)`** — T1·T2·T3·T7의 유일한 통로(자체 UoW). 처리 순서(한 트랜잭션): 멱등 claim(endpoint=`POST /approvals/{id}/decisions`) → 승인 무잠금 조회(404) → 대상 잠금(C6 락 순서) → 승인 `FOR UPDATE` → **상태 확인(409 TRANSITION_NOT_ALLOWED)** → version 확인(409 `COMMON.CONCURRENCY.VERSION_CONFLICT`) → **`decision_authority`**(DENIED면 audit 후 커밋·403) → APPROVE는 스냅샷 재검증(불일치 VOID 커밋·409 STALE) → `_record_transition` → 결과 알림 → 멱등 complete → UoW 밖에서 raise(있다면). `actor`는 항상 실 사용자(None·시스템 불가 — 이벤트 actor NOT NULL). 파라미터에 우회·자동 표식 없음.
- **자동 승인 경로 부재 — 아키텍처 4중 강제**: ①`decide_approval` 호출 모듈 집합 상수 `DECIDE_CALLERS={approvals/router.py}`(소스 스캔 — P7이 Slack 어댑터를 더할 때 이 상수 갱신+ADR) ②`APPROVED`로의 `_record_transition` 호출은 `decide_approval` 안 1곳뿐(AST) ③JOB_REGISTRY 함수·CLI·seeds·imports·outbox 디스패처가 `approvals.service`의 상태 변경 함수(`decide/consume/void/request`)를 임포트하지 않음(잡·CLI는 `approvals.stagnation`만) ④`request_approval`·`consume_approval`·`void_for_target` 임포트 허용 집합 = {SO 확정·편집·취소 서비스(B 묶음이 확정한 모듈 경로를 상수에 등재)}.
- **권한 표(서버측 역할+소유권, ADMIN은 `require_roles` 통과이되 SoD는 서비스 별도)**:
  | 엔드포인트 | 권한 |
  |---|---|
  | `GET /approval-lines`·`/coverage` | ADMIN·TRADE·LOGISTICS·CERT |
  | `POST/PATCH/DELETE /approval-lines` | ADMIN |
  | `GET /approvals?scope=inbox|mine|all&status=&page` | inbox=비조회 4역할 중 자격자만, mine=본인 기안, **all=ADMIN**, Page 기본 50, `list_approvals` |
  | `GET /approvals/inbox-count` | 비조회 4역할 |
  | `GET /approvals/delegation-candidates` | 비조회 4역할 |
  | `GET /approvals/{id}`·`/{id}/events` | 기안자·(결정자)·현재 결재 자격자(대결 포함)·ADMIN, **그 외 403(IDOR)**, VIEWER 403 |
  | `POST /approvals/{id}/decisions` | 비조회 4역할+서비스 자격 판정, **Idempotency-Key 필수** |
  | `/delegations…` | C5 |
  정적 경로(`inbox-count`·`delegation-candidates`)는 `/{id}`보다 **먼저** 선언(int 경로 파라미터 충돌 방지). `POST /approvals`(요청 생성)는 **존재하지 않는다**.
- **요청 스키마** `DecisionRequest{verb, reason?(≤1000), version}` extra=forbid → 200 `ApprovalView`. 응답 스키마의 새 필드는 **기본값 필수**(멱등 재생 시 구 응답 500 방지 — R8). `ApprovalView`에 서버 계산 필드 `can_decide`(bool)·`decide_blocked_reason`(null|'SELF'|'NOT_APPROVER')·`can_withdraw`(bool)·`required_role`·`requester_name`·`decided_by_name`·`decided_on_behalf_of_name`·`target_label`·`basis_amount`(정수)·`basis_currency`·`status`·`snapshot`(표시용)·`status_reason`·`void_reason_code`·`version`. **프런트 `hasRole` 추정 금지**(V2). 이름은 표시명만.
- **에러 코드 신설(3세그먼트, `ErrorCode`+`ERROR_CATALOG` 한국어 원인·조치 문구, 선례 V13 명명)**:
  `APPROVALS.LINE.NOT_CONFIGURED` 422 / `APPROVALS.LINE.DUPLICATE` 409 / `APPROVALS.APPROVAL.NO_ELIGIBLE_APPROVER` 422 / `APPROVALS.APPROVAL.ALREADY_ACTIVE` 409(안전망, 기존 활성 id를 detail로) / `APPROVALS.APPROVAL.REQUIRED` 422(승인 없이 확정 시도=우회 차단 — 승인 요청 안내 포함) / `APPROVALS.APPROVAL.STALE` 409 / `APPROVALS.TRANSITION.NOT_ALLOWED` 409 / `APPROVALS.TRANSITION.REASON_REQUIRED` 422 / `APPROVALS.DECISION.NOT_APPROVER` 403 / `APPROVALS.DECISION.SELF_APPROVAL` 403 / `APPROVALS.DELEGATION.OVERLAP` 409 / `APPROVALS.DELEGATION.NOT_ACTIVE` 409 (**12종**). 그 외 입력 검증(기간 순서·소급·역할 초과 위임·자기 위임·VIEWER 수임자)은 `VALIDATION_INVALID_FIELD`+필드 안내(클라이언트가 분기할 필요 없음), 미존재 404·낙관 잠금 409·권한 403(`AUTH_FORBIDDEN`)은 기존 공용 코드. **잠금 대기 초과(55P03)→409 매핑은 플랫폼 소유 공용 핸들러(V12) — 승인 결정·소비 경로 이전에 필요(X3)**.
- **멱등·동시성**: 쓰기 POST 전건 Idempotency-Key, PATCH·DELETE는 version(알림 규칙 선례). 서로 다른 두 결재자의 동시 결정은 승인 행 잠금으로 직렬화되어 1건만 성공하고 나머지는 `TRANSITION.NOT_ALLOWED`. IntegrityError(유니크·FK)는 전용 코드로 번역(500 금지).
- **감사(audit, 정상 결정은 `approval_events`가 정본이라 중복 행 없음)**: `approvals.line.created|updated|deleted`, `approvals.delegation.created|revoked`, `approvals.decision.denied`(detail=approval_id·verb·blocked 코드), `approvals.approval.bypass_blocked`. `AuditAction`은 상수 클래스(CHECK 아님)에 추가.
- **화면 4요소(API+화면+권한+테스트) — 라우트 3+셸 배지**(기존 flat `routes/*.tsx` 관용):
  ① **`/approvals`** — 탭 2: **결재함**(API `list_approvals scope=inbox`+`decisions`; 행 액션 [승인][반려(사유 필수)]는 `can_decide`로만 노출, 기안자 본인 행 미노출, 성공 시 행 소멸, version 충돌 시 새로고침 안내) / **내 요청 현황**(scope=mine, 상태 배지 6종[색+글자], [회수]는 `can_withdraw`만, 반려·무효 사유 서버 문구, 재기안 진입은 SO 상세 ApprovalPanel — SO 묶음). ② **`/approval-lines`**(ADMIN 전용 내비 항목) — 결재선 관리: 임계 사람 표기 전송·수정 폼 초기값 `toDecimalInput`·version PATCH·coverage 안내 배너. ③ **`/delegations`** — 대결 설정: 본인 등록·조회·종료, ADMIN 전체, 수임자 선택은 delegation-candidates 검색, **KST 오늘 기본값**·시작≤종료 검증·상태 배지(ACTIVE/UPCOMING/EXPIRED/REVOKED/INERT+사유). ④ **셸 배지** = inbox-count(알림 unread-count 선례). 한국어 UI 규약: `break-keep`, 헤더·유형명·상태명 `nowrap`, 금액·임계·건수 가운데 정렬, 서버 미지의 상태 값은 '기타' 표기, Idempotency-Key는 폼 열림/진입 시 1회 생성해 재시도에 재사용, 결재함 쿼리 staleTime 0.
  테스트: vitest — `can_decide` true/false 픽스처(VIEWER·**ADMIN이 기안자인 본인 행**)·반려 사유 필수·POST 모양·중복 클릭 동일 키·결재선 패널 관리자 한정·금액 왕복·대결 KST 경계(NOW 고정 UTC/KST 갈림)·후보 검색·종료 뮤테이션·알 수 없는 상태 '기타'. 라우트 진입 자체는 URL로 가능한 기존 관용 — 서버 403이 정본.
- **담당 이관(handover) 취급**: 승인 대기 건은 **역할 기반이라 이관 대상이 아니다**. `approvals`·`approval_events`·`delegations`·`approval_lines`에는 ASSIGNMENT 이름 4종 컬럼을 두지 않고 `handover/targets.py`에 등록하지 않는다(requested_by·decided_by·delegator·delegate는 이력·개인 권한이라 ADR-0015 "담당(현재 쥐고 있는 자)만 이관" 밖). 위임은 이관하지 않는다: 이관 도구가 대상을 비활성화하면 C5 술어로 즉시 무효, 비활성화 없이 이관만 하면 권한은 사람에 붙어 유효. **면제 판정을 테스트로 못 박는다**: 승인 4테이블의 모든 users FK 컬럼이 "행위 기록 면제 목록(사유 포함)"에 등재돼 있고, 목록 밖 새 users FK가 생기면 실패(이름 4종 밖 컬럼의 조용한 누락 방지). 기안자 퇴사로 남은 REQUESTED는 결재자 반려·ADMIN 회수·정체 독촉으로 정리.

**(d) 4금** — 자동 승인 경로 부재를 4중 강제. Slack 승인(P7)도 같은 함수에 실 사용자 actor를 넘긴다(대외 발송 아님 — 승인 버튼은 수신 채널). 요청 생성 HTTP 부재로 승인 위조 표면 없음.

**(e) 상태·전이·불변** — T1·T2·T3·T7은 `decide_approval` 1통로, T4·T5·T6은 도메인 통로 3함수, 전부 `_record_transition` 1곳으로 수렴.

**(f) 테스트 배분**
- K(아키텍처, 전건): `DECIDE_CALLERS` 스캔·APPROVED 진입 1곳(AST)·잡/CLI/seeds/imports 비임포트·요청 스키마 status 필드 부재·전 쓰기 스키마 `…Request` extra=forbid·**HTTP 요청 생성 부재**(`POST /approvals` 라우트 0)·시스템 행위자 불가(시그니처)·에러 코드 12종이 `test_error_catalog` 통과·`list_` 명명·Page 봉투·비인증 401 커버리지·페이지네이션 자동 스캔 편입·ASSIGNMENT 면제 목록 테스트·소비 접점 스캔·approvals 모듈 스캔이 자기 스키마를 실제로 집는지(공회전 방지 len≥N).
- H(e2e 대표): 여신 초과 SO 확정 시도(거부)→승인 요청(SO 묶음 엔드포인트)→결재함에서 **다른 사용자** 승인→확정 성공→재확정 시도 `REQUIRED`, 반려·회수·재기안, 대결 경유 승인 — **실 HTTP+실 브라우저 워크스루(§22 렌즈 11)**.
- J: 같은 Idempotency-Key 재전송(응답 동일·이력 1행·알림 1건), 같은 키·다른 본문 409, 다른 키 같은 결정 → 409 TRANSITION.NOT_ALLOWED, 두 결재자 동시 결정 Barrier(정확히 1건 성공), 낡은 화면 version 409, 결정 트랜잭션 강제 롤백 시 이력·알림·이벤트·audit 동시 롤백(단 denied audit는 별도 커밋 규약대로).
- K/G(권한 매트릭스 — 서비스 전수, e2e 대표): 역할 5×엔드포인트 전수(비인증 401·VIEWER 403·무관 사용자 상세 403·관리자 전용 403), IDOR(타인 승인 상세·이력 403, mine은 본인 행만), IntegrityError→4xx 매핑 표(중복 임계·겹침 대결·존재하지 않는 delegate_id·중복 활성 승인, 500 없음).
- I(P7 자리): `decide_approval`을 Slack 핸들러 대역이 호출해도 동일 기록·SoD 적용(호출 집합 상수 갱신 테스트).
- 프런트 vitest 위 목록. 단위 테스트 총수 대사(pytest·vitest 기준선 CI run 줄).
- 변이 점검 총 목록(생존 0이 완료 조건): 결정 통로의 자격 판정 호출 제거, `_record_transition` 전이표 조회 제거, CONSUMED 술어 제거, 결재선 ORDER BY 방향·임계 부등호, SoD 비교, 대결 기간 부등호, 정체 dedup 키 k, `void_for_target` 훅 호출 제거(lazy 백스톱이 잡는지).

**(g) 소비·등재** — 소비: P7 Slack(같은 함수), S3-4·Phase 4(유형 추가 ⑤ 절차), 오더 보드 벌크 확정(건별 독립 `confirm` 호출 — 승인 우회 경로 다중화 금지, ADR-0038 ⑥ 계보), SO 상세 ApprovalPanel. 이월/부채: PROGRESS L144 forbid 스캔 전역화 트리거 도달(X3), 알림 이동 매핑, DESIGN §14 화면 배정 문장, handover 면제 ADR 명기, 승인 API에서 VIEWER 접근 전면 차단(마스킹 원장 #2 결론 부기: 승인 코어 범위에서는 재판정 불필요).

**(h) 되돌리기 비용: 낮음~중간.** 결정 통로가 하나라 정책 변경은 한 함수. 권한 완화(VIEWER 열람)는 라우터 가드+마스킹 판정. 에러 코드 명 변경은 프런트 분기 영향이 있어 지금 확정. 요청 생성 HTTP 추가는 새 표면이라 비권장. handover 편입은 스캔 확장+targets 1줄이나 이력 오염 위험으로 비권장.

---

## 안건 X1. 소비자(SO 게이트)와의 계약 — 요청은 사람의 의사

**(a) 목적·경계** — SO 묶음이 승인 코어를 호출하는 규약을 승인 코어 계획에서 못 박는다(SO 묶음이 다른 계약을 상상하지 않게).

**(b) DESIGN 근거** §2 "기안→승인"(기안=사람 행위) / §7.10 "여신 초과 수주 승인 게이트" / §17.1 / r1 D2·D4.

**(c) 계약**
1. **승인 요청은 SO 확정 시도의 부작용이 아니다.** 확정 시도는 초과 시 거부+안내(`REQUIRED`)만 하고 승인 행을 만들지 않는다. 승인 요청은 **두 번째 사용자 동작(명시 버튼 '승인 요청')** — SO 묶음 엔드포인트(예: `POST /sales-orders/{id}/approval-requests`+Idempotency-Key)가 내부에서 `approvals.request_approval(session, *, approval_type, target_id, requested_by) -> (ApprovalView, created: bool)`를 호출(호출 트랜잭션에 합류). 요청 시 `snapshot.amount ≤ 0`이면 422 `VALIDATION_INVALID_FIELD`(불필요한 승인 금지).
2. **요청자 권한**: SO 엔드포인트의 역할(ADMIN·TRADE 권고 — E1)이 정본, `request_approval`은 방어적으로 VIEWER 단독 actor를 403.
3. **확정 통로 호출 순서**: ①SO 행 `FOR UPDATE` ②(여신 산식 직렬화 잠금) ③`consume_approval` ④`BLOCKED`면 UoW 커밋 후 `raise result.error` ⑤`CONSUMED`/`NOT_REQUIRED`면 헤더 상태 확정. SO **수정(게이트 입력 필드)·취소** 서비스는 같은 트랜잭션에서 `void_for_target`.
4. 오더 보드 벌크 확정·인테이크 확정·엑셀·CLI는 SO 확정 통로를 **건별 독립**으로 호출(승인 우회 경로 다중화 금지). 확정 통로가 아닌 상태 대입 경로는 SO 묶음 스캔이 봉쇄.
5. (권고 — A/B/E가 채택 여부 판정) DB 백스톱: `sales_orders`에 `credit_approval_id BIGINT NULL FK approvals`+ "CONFIRMED이면서 승인 소비로 확정된 건은 credit_approval_id NOT NULL" 형태의 CHECK(정상 한도 내 확정은 NULL)로 "승인 없이 초과 SO가 CONFIRMED" 상태를 DB가 거부.

**(d) 4금** — 요청=사람 행위, 자동 요청 생성 없음(승인 폭주·알림 스팸·"요청은 사람의 의사" 훼손 방지).

**(e)** 없음(계약).

**(f) 테스트 배분** — A/H: 확정 시도만으로 approvals 행 0 / 명시 요청 후 1건 / 초과분 0 요청 422 / 같은 SO 재요청은 기존 활성 승인 반환(멱등) / 반려 후 재요청 신규 행 / 소비 실패 시 커밋 후 raise(C6 ⑦). SO 묶음 테스트와 함께 편입.

**(g)** 소비: B·E·D 묶음. 이월: SO 묶음 계획서가 이 계약을 인용.

**(h) 되돌리기 비용: 낮음**(자동 요청 생성으로 바꾸는 것은 SO 묶음 호출 위치 이동뿐이나 통지 폭주 위험).

---

## 안건 X2. PR 순서 의존 · 타 묶음에 요구하는 것

**(a)** 승인 코어는 `target_type='SALES_ORDER'`·TargetSpec 레지스트리·소비 접점 스캔 때문에 **SO 모델·확정 서비스보다 앞설 수 없다**.
**(c) 요구·가정(통합 검토가 정합)**
- **[A 데이터모델]** `sales_orders`(id·deleted_at·상태·표시용 전표번호) 존재. `SO_CREDIT_EXCEEDED` TargetSpec.snapshot의 **digest 입력 = 게이트 판정 동결 필드 집합**(바이어·통화·환율 스냅샷·라인별 SKU/수량/단가; 메모·담당자·version 제외) 확정·구현 — 라인만 바뀌어도 digest가 바뀌므로 헤더 version 증가에 의존하지 않는다.
- **[B 상태/불변]** SO 확정 통로 호출 순서(X1 3), 수정·취소에서 `void_for_target`, **`ConsumeResult` 커밋 후 raise 규약**, 락 순서 준수. 승인 REQUESTED·APPROVED 동안 초안 SO 편집은 허용(digest 가드가 대신함).
- **[E 여신/입금]** 초과분 산식(바이어 `credit_limit` 통화 기준·미수채권 항 미반영 플래그는 `snapshot.detail.receivable_included=false`로 fail-visible)·`amount`·`currency` 제공, 평가 불능은 spec 자체 AppError, `credit_limit NULL`=여신 미관리=승인 불필요는 E 판정(approvals는 `amount>0`일 때만 요청 수용). 마스킹 재판정 E2는 본 묶음 VIEWER 전면 403과 독립.
- **[D 인테이크/보드]** 벌크 확정=건별 독립 호출, 인테이크 확정은 사람 확정 경로, 화면 ApprovalPanel(요청·상태·이력)은 approvals 조회 API 소비.
- **[F PO/역할/이월]** PR 분할, ADR 번호 배정, 이월 원장·문서 갱신(X4), 역할 헬퍼 픽스처(ADMIN·LOGISTICS·CERT 사용자·결재선·대결 팩토리) 확대.
- **PR 순서 권고**: SO 데이터모델 → **승인 코어 PR**(4테이블+table_policy+column-grant 헬퍼+에러 12종+ADR+registry 등록+approval-lines·delegations 라우터·서비스+`request_approval`·`decide_approval`·`consume_approval`·`void_for_target`+`approvals` 라우터·결재함) → SO 확정 게이트 배선(같은 PR에 소비 접점 스캔 편입) → 화면·알림·잡. 소비 접점 스캔은 배선 PR과 **같은 PR**에 둔다(순환 회피 — 한 PR 창 동안 스캔 부재를 PROGRESS에 기록).

**(h) 되돌리기 비용: 낮음**(PR 재배치).

---

## 안건 X3. 승인 코어가 소비·신설하는 공용 코어 변경

**(c)**
1. **`identity.service.holders_of_role(session, role, *, active_only=True) -> list[int]`** 공개 헬퍼 — 현재 `notifications.service._users_with_role`(private)과 같은 쿼리. approvals·notifications가 공유(중복 구현 금지), `_users_with_role`은 위임 래퍼로 축소(동작 불변).
2. **`table_policy.restrict_update_columns`+`COLUMN_UPDATE_ALLOWLIST`**(C6 c'): 새 분류 테스트(허용 목록 ⊆ MUTABLE, `has_column_privilege` 실측, 허용 목록 컬럼이 모델에 실재).
3. **`_NEVER_SEEDED`에 `approvals, approval_lines, approval_events, delegations` 추가**.
4. **`test_secret_boundaries.ALLOWED_SENSITIVE_UNIQUE_KEYS`에 `("approval_lines","threshold_amount")`** 사유와 함께.
5. **잠금 대기 초과(55P03 LockNotAvailable·QueryCanceled)→409 매핑 핸들러**(`COMMON.CONCURRENCY.LOCK_TIMEOUT` 후보) — **플랫폼 소유**, 승인 결정·소비 경로가 대상+승인 2행을 잠그므로 S3-1 SO 확정 경로 이전 편입 권장(V12: 현재 500 노출).
6. **쓰기 스키마 forbid 스캔 app 전역화**(PROGRESS L144 트리거 — 신규 모듈 추가 세션 = S3-1): approvals 스키마 전건이 통과하도록 작성. 기존 모듈 소급 범위는 통합 검토 판정.
7. **`core/money.py`에 `parse_minor_amount` 공용 추출 권고**(3번째 사본 방지) — 소급은 별도 판정(관찰).

**(f) 테스트** — `holders_of_role`: 비활성·삭제된 user_roles 제외·정렬 안정. 나머지는 위 각 항의 테스트.
**(h) 되돌리기 비용: 낮음.**

---

## 안건 X4. 문서·ADR·GC·WBS 갱신 세트 (설계 변경 = DESIGN 갱신 + ADR 5줄 세트)

| 문서 | 갱신 내용 | 구분 |
|---|---|---|
| DESIGN §3 표 맵 | `approvals` 외 `approval_lines`·`approval_events`·`delegations` 3표 추가 | **설계 보강 필요** |
| DESIGN §2/§7 승인 워크플로우 [M4] 보강 문단 | 유형(1종+추가 절차)·상태 6·전이 7·소비·SoD·ADMIN 해석·대결 규칙·알림 | **설계 보강 필요** |
| DESIGN §14 | '승인함·대결 설정' 화면 배정 문장(현재 ⑭에 결재선만 — r1 G7) | **설계 보강 필요(§14)** |
| DESIGN §17.5 확장 | `approval_events` IMMUTABLE 등재(ADR-0040 서식 — "이후 상태 변경 이력은 신설 세션이 등재") + 컬럼 단위 UPDATE 권한 수단 명문 | **설계 보강 필요(§17.5)** |
| DESIGN §20 헤더 H 매핑 | P3 추가(현재 P2·P6만 — r1 G1), WBS S3-1 검증란 H 2항 → 4항(승인 우회 차단·승인 후 불변·결재선 매핑·대결 기간+이력) 정정 | **WBS/§20 충돌 정정** |
| DESIGN §21 [M2] 보강 ④ | 잡 레지스트리 7행 → 8행(`approval-stagnation-scan`) | 문구 갱신 |
| ADR ×3 (번호는 총괄이 0051~ 배정) | ①**승인 코어 계약**(C1·C2·C6·C8: 유형 절차·상태 6·전이 7·소비·무효·락 순서·컬럼 권한·ADR-02 SoftDelete 예외) ②**결재선·SoD·대결**(C3·C4·C5: strict 초과·통화 환산 배제·동결·ADMIN 해석·1인 운영 귀결·재위임 불가·계산 유효성·ADR-0018 ㉠ 부기) ③**알림·결재함·정체**(C7·C8: 직접 notify·DEADLINE 사유·잡 4금 논증·이관 면제) — 각 5줄 서식(결정·근거·기각한 대안·재검토 트리거) | ADR 필요 |
| PROGRESS | "자율 확정" 표기, 부채·관찰 등재(아래), 운영 개시 절차 "결재선 등록 전 fail-closed" | |
| GC v1.4 | S3-1 배정 0건(r1 G3) → 케이스 신설 제안: **GC-H3 승인 우회 차단(ADMIN 포함·양방향)** / **GC-H4 승인 후 불변·재승인** / **GC-H5 대결 기간·이력 경계** — 각 Given/When/Then+거부·성공 양방향+매핑표 행 추가(번호는 통합 검토가 타 묶음과 조율) | 문서 |
| runbook | 운영 개시 1단계 "결재선 등록", 1인 운영 시 제2 결재 계정 안내 | |

**부채·관찰 등재(조용히 넘기지 않는다)**: ①승인 유형 5종 소비 세션(비용=S3-4·폐기/실사=P4·NEW_PARTNER/SOURCING_NON_RECOMMENDED=**WBS 배정 공백**) ②비금액 유형 결재선 스키마 재판정(트리거: 첫 비금액 유형) ③승인 만료 도입 여부 ④대상 작성자 SoD 확장 ⑤1인 운영 자기 승인 정책(오너 판정 후보) ⑥대결 기간 상한·재활성 시 유효 복귀 수용 ⑦lock_timeout→409 핸들러(플랫폼) ⑧금액 파서 3번째 사본 ⑨P6 헬스체크에 "결재선 미설정" 편입 ⑩outbox 요청 이벤트+alert_rules 규칙 시 인앱 이중 알림 ⑪알림 이관 후 수신자 무자격 링크 403 ⑫데일리 브리핑 '결재 대기' 줄 미도입 ⑬P7 Slack `DECIDE_CALLERS` 갱신.

**(h) 되돌리기 비용: 낮음.**

---

## 자율 확정 판정표

| 번호 | 결정 요지 | 근거 한 줄 | 되돌리기 비용 |
|---|---|---|---|
| C1 | 승인 유형=코드 고정 StrEnum, S3-1 값은 `SO_CREDIT_EXCEEDED` 1종만(5종 미예약), 테이블 4종(approval_lines·approvals·approval_events[IMMUTABLE]·delegations), approvals·delegations는 SoftDelete 없음, 스냅샷 컬럼은 컬럼 단위 UPDATE 권한으로 DB 불변, 임계 유니크는 허용 목록 등록 | ADR-0041 죽은 열거 금지·§17.5 권한 수단·V5·V6·V8 | 낮음~중간 |
| C2 | 기안→승인 2단 고정, 상태 6값·전이 7방향(사람 4·도메인 통로 3)·미허용 29쌍, 재기안=신규 행, 만료 없음, 반려·회수 사유 필수, 단일 `_record_transition` | §2 문면 최소 구현·V8·V9 | 낮음 |
| C3 | 결재선=(유형·통화·임계 strict 초과→역할), 같은 통화만(환산 없음), 매핑 없음=422 fail-closed, 요청 시점 역할 동결, 시드·CLI 없음(관리 화면만), PATCH는 역할·메모만 | "임계 초과" 직독·환율 미결 회피·"승인 후 불변"·함정 ⑩ | 중간 |
| C4 | 자격=역할 기반+활성 ADMIN+유효 수임자, 요청자≠결정자(ADMIN 포함·대결 양쪽), ADMIN도 승인 기록 없이 확정 불가, VIEWER 전면 403, 판정 술어 단일 출처 | §2 상시 통과(접근)와 §20 H(게이트)의 층 분리·V1 | 낮음~중간(오너 판정 후보 1순위) |
| C5 | 대결=(유형+역할) 범위·KST 날짜 양끝 포함·소급 금지·중첩 409·재위임 불가·유효성은 계산값(비활성 즉시 무효, 훅 없음)·기간 수정 API 없음·종료 revoke·후보 디렉터리 `{id,display_name}` | ADMIN 위임 시 권한 초과 방지·순환 임포트 회피·V17·V22 | 중간 |
| C6 | 승인 후 불변=스냅샷 컬럼 DB 권한+이력 IMMUTABLE+대상 변경 시 VOID, digest(version 아님)·상한 재검증·1회 소비, `consume_approval`은 결과 반환(커밋 후 raise), 락 순서 대상→직렬화→approvals, eager 훅+lazy 백스톱 | V9·V10·S1-3 PR-3 ① 교훈·§17.2 | 중간 |
| C7 | 요청 알림=직접 notify(DEADLINE·assignee 지정), 역할 보유자→없으면 ADMIN 폴백, 알림 비의존 결재함+배지, 정체 잡 `approval-stagnation-scan` daily@07:10 N=2(규칙 override)·알림만 | ADR-0045·ADR-07·정체 스캔 선례·4금 비저촉 | 낮음 |
| C8 | 결정 통로 `decide_approval` 1개(verb 3), 자동 승인 부재 4중 아키텍처 강제, 요청 생성 HTTP 없음, 에러 12종, 라우트 3+배지, 이관 제외+면제 테스트 | §16 L294·§15·V2·V13·V14 | 낮음~중간 |
| X1 | 승인 요청은 사람의 명시 동작(확정 시도가 자동 생성 안 함), 확정 통로 호출 순서·NOT_REQUIRED·VOID 훅 규약 | §2 "기안=사람 행위"·승인 폭주 방지 | 낮음 |
| X2 | 승인 코어는 SO 모델 뒤·소비 접점 스캔은 배선 PR과 같은 PR, 타 묶음 요구 5건 명시 | 죽은 열거 금지·순환 회피 | 낮음 |
| X3 | 공용 변경 7건(holders_of_role·column-grant 헬퍼·시드 금지 목록·허용 목록·55P03 매핑·forbid 전역화·money 파서) | 중복 구현·조용한 500 방지, PROGRESS L144 트리거 | 낮음 |
| X4 | DESIGN §3·§2/§7·§14·§17.5·§20·§21 갱신+ADR 3건+GC v1.4+runbook+부채 13건 등재 | 설계 변경=DESIGN 갱신+ADR 세트(CLAUDE.md) | 낮음 |
