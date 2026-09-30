# S3-1 계획서 안건 전문 — 묶음 B: 상태머신·확정/불변·취소/정정·채번·잔량·만료

- 작성: 심판(최종 설계자) · 근거 정본: DESIGN.md §2·§3·§7·§17·§18·§20·§22, WBS.md S3-1, 코드 실측(2026-09-30 리포)
- 성격: 설계 결정(구현 아님). 전 안건 **자율 확정**(오너 지시 2026-09-29 — 판정 후보를 권장안으로 확정, 사후 번복 가능). "미정" 결론 없음.
- 안건 번호: B1~B7(원 묶음 안건) + B8(전이·생성 API·잠금·멱등 계약) + B9(삭제 금지·우회 경로 차단표). 제안 3종의 X 안건은 B8·B9로 흡수했다.

---

## 0. 심판 노트 — 제안 3종 사실 검증과 충돌 판정

### 0.1 직접 검증한 사실(코드·DESIGN 인용)

| 제안의 주장 | 검증 결과 |
|---|---|
| `numbering/service.py:36 year = (at or utcnow()).year` (UTC 연도) | **사실.** `to_kst`는 `app/core/time.py:35`에 있고 naive datetime은 `ensure_aware_utc`가 ValueError로 거부한다 — 수정은 1줄. |
| 기존 채번 테스트가 연도를 단정하지 않는다 | **사실.** `test_numbering.py`는 `year.isdigit() and len(year)==4`만 본다. 연도 경계 케이스는 신규 추가. |
| `JOB_REGISTRY` 현 7종 → 신규 1행이면 8종 | **사실.** certification-sweep·outbox-dispatch·deadline-scan·daily-briefing·stagnation-scan·storage-monitor·backup-freshness = 7. `test_registered_jobs_stay_clear_of_the_four_bans`가 **집합 동일성**을 단정하므로 신규 잡은 그 목록을 손으로 고쳐야 통과한다. `test_backup_freshness:367`·`e2e/test_scheduled_jobs:49`는 `len(JOB_REGISTRY)`·정렬 비교라 자동 추종. |
| (risk) JobSpec에 `func=` 필드 | **틀림.** 필드는 `code/name_ko/schedule/run`이다. 이 계획서는 `run=`으로 적는다. |
| 리포 전체 `CREATE TRIGGER` 0건, 불변 강제는 `revoke_mutations` 권한으로 통일 | **사실.**(grep 0건, `table_policy.IMMUTABLE_TABLES`={audit_log, certification_status_log}). |
| `certification_status_log`가 PkMixin+Base만·actor NULL=시스템·`automatic`은 actor NULL에서 파생 | **사실**(`automatic: actor_user_id is None`). **이번 전표는 다르다** — 입금 수렴·QT 연쇄는 "자동이지만 행위자(입금 기록자·후속 생성자)가 있다". 따라서 `automatic`을 **명시 컬럼**으로 둔다(제안 3종 공통이자 정당한 이탈). |
| `_AUTO_REASONS` 선례, 아웃박스 이벤트명 `certifications.certification.status_changed`, payload에 `automatic` | **사실**(`certifications/service.py:82,389-425`). |
| 예외 핸들러: `StaleDataError`→409만 있고 lock_timeout(55P03)·데드락(40P01) 핸들러 없음 | **사실**(`handlers.py:132`). → 신규 FOR UPDATE 흐름이 많은 이번 세션에서 500 노출이 현실적이다. **B8에 소규모 코어 보강으로 채택.**(risk 단독 주장, 검증됨) |
| DB 세션 `lock_timeout`은 역할에 붙어 있음(risk의 5s) | `session.py:73` 주석으로 확인(수치 5s는 r4 보고 근거). 구현 시 실측할 것. |
| `VersionMixin`은 `version_id_col`이라 ORM UPDATE마다 version 자동 증가 | **사실**(`mixins.py:96-110`). → "시스템 수렴은 version 미증가"는 불가능한 주장이다. **시스템 수렴도 version이 오른다**(fit·risk의 X1 ③ 후반과 같은 결론, strict의 "자동 전이는 version 인자 없음"과 결합). |
| 에러코드는 `<도메인>.<대상>.<사유>` 3세그먼트, 도메인=모듈명 대문자, 문구 ≥10자+조치 힌트어("주세요/하십시오/요청/문의/확인") | **사실**(`test_error_catalog.py`; 정규식은 도메인에 `_` 허용). |
| ADR 최신 번호 0050 → 다음 0051~ | **사실**(`docs/adr/0050-…`). 번호는 통합 검토가 최종 배정. |
| §7.2 PO 문면 = "발행→공급사확인(OC)→(수입선적 연결)→부분입고→입고완료→종결 / 취소" — **초안 없음** | **사실.** risk의 PO DRAFT·SHIPMENT_LINKED 상태 추가는 문면 밖 발명이라 **기각**(WBS "상태 열거 구현(§7.2 그대로)"와도 충돌). |
| DESIGN §7.2 QT = "작성→발행→(수주전환\|만료\|취소)", PI = "발행→…" | **사실.** QT만 '작성'(=DRAFT)이 있고 PI·PO는 발행으로 시작한다(대조가 의도적). |
| WBS에 PO 입고 문서·PO 잔량 차감 소유 세션 없음 | **사실**(`grep 입고` → S4-3 채널입고·S5-2 채널입고 계획뿐; PO 입고 없음). 부채 등재(D-B1). |
| GC-A4 = "확정된 출고 전표…UPDATE/DELETE 불가(DB 권한)" — S4-1 매핑 | r1·r2 보고 인용과 일치. S3-1의 4종 전표는 초안/접수 편집·상태 갱신이 앱 계정 정상 UPDATE라 **권한 회수 불가**. |

### 0.2 충돌 지점별 판정(요약 — 상세는 각 안건)

| 충돌 | strict | risk | fit | **판정** | 이유 |
|---|---|---|---|---|---|
| 채번 시점·QT 초안 폐기 | 최초 저장 시 채번·초안 폐기=CANCELLED·삭제 금지 | 동결 시 채번·초안 soft delete | 동결 시 채번(QT)·SO는 접수 시 | **최초 저장 시(4종 공통)·폐기=CANCELLED·전표 삭제 경로 0** | 한 규칙으로 4종 통일, doc_number NOT NULL, 결번 0이 삭제 금지로 구조 보장. risk의 "SO 확정 시 채번"은 접수 SO가 번호 없이 보드에 뜨고 취소/삭제 이중 경로가 생겨 기각. |
| QT의 CONVERTED 지위 | ISSUED→CONVERTED(SO 생성)+CONVERTED→CANCELLED 사람 | 유사 | 후속 존재 ⇔ CONVERTED **양방향 자동** | **fit(양방향 파생) 채택, CONVERTED→CANCELLED 직접 엣지 없음** | ADR-0038 교훈("부여만 있고 해소 없으면 정정 후 상태가 거짓"). 후속이 사라지면 자동 복귀하므로 취소 경로는 ISSUED→CANCELLED 하나로 충분. |
| 예약 상태(SO 4·PO 3) | 값은 CHECK, 엣지 0(RESERVED) | 파생 20엣지·유지 컬럼까지 지금 구현 | 예약 엣지 36개를 owner 라벨로 지금 고정 | **strict — 값만 싣고 엣지 0** | 후속 세션의 흐름(할당 파생·선적 파생)을 추측해 엣지 36개를 고정하는 것은 "추측 구현 금지". 값(CHECK)은 §7.2 문면·WBS로 정당하고, 엣지는 소비 세션이 ADR-0038 서식으로 더한다. |
| 동결 필드 강제 | FIELD_POLICY 완전성+FROZEN/FREE/SYSTEM | 화이트리스트+`frozen_hash` 재검증 | 열 분류 4종+`content_digest` | **분류 레지스트리 채택, 해시/다이제스트 미채택** | 다이제스트는 (1) 후속 세션이 CONTENT 컬럼을 더하면 기존 동결 행의 재계산이 **전부 불일치**하는 스키마 진화 위험이 있고 (2) DESIGN·WBS 근거가 없다. 대신 SO에만 `content_rev`(승인 결속 토큰) 1컬럼. 관찰 등재(D-B6). |
| 이력 테이블 | 다형 1개 | 4개 | 4개 | **4개(문서별)** | FK RESTRICT 가능·CHECK가 문서별 상태 집합을 정확히 표현·선례(certification_status_log). 구조 중복은 믹스인. |
| 잔량 | SUM 파생+부모 라인 잠금 | 유지 컬럼+CHECK+검산 | SUM 파생+소비자 레지스트리 | **SUM 파생(소비자 레지스트리)** | 소비 코드 없는 유지 컬럼은 죽은 컬럼이고 §8.3이 P4 ADR로 남긴 결정을 선점한다. 계약 시그니처만 고정. |
| 만료 스윕 시각 | 06:10 | 00:20 | 06:10 | **06:10** | certification-sweep 06:00 뒤·deadline-scan 06:30 앞(기존 순서 관용). |
| 만료 알림 | 없음 | 담당자 notify | 없음 | **없음(이벤트만)** | 문면 근거 없음. alert_rules로 즉시 구성 가능. |
| 정정 경로 | 취소+신규(`copied_from_id`) | 취소+신규(`cancel-and-clone` 단일 액션) | 취소+신규(`replaces_id`+copy) | **취소+신규, 두 단계 독립 동작(단일 액션 없음), `copied_from_id`** | 두 동작 각각이 원자·멱등·감사 가능. 원자 결합 액션은 "취소 성공·복제 실패" 부분 상태를 부른다. |
| 전이 API | 단일 `/transitions` | 전용 액션 다수 | 동결 액션 전용+나머지 `/transitions` | **fit** | 동결 액션(QT issue·SO confirm)은 게이트·승인·포트가 걸려 응답 형태가 다르므로 별도. 범용 전이 스키마에서 동결 엣지를 구조적으로 제외해 우회 표면 제거. |

---

## B1. 상태 열거·전이표(코드 고정)·상태 대입 단일 통로·사유 규칙·예약 상태

### (a) 목적·경계
QT·PI·SO·PO 4종의 상태 값(§7.2)과 허용 전이를 **한 정의 파일**(`trade_docs/machine.py`, ADR-0038 서식)에 코드로 고정하고, `status`를 대입하는 통로를 함수 하나(`record_transition`)로 못 박는다. 경계: 전이 API 모양·역할은 B8, 동결 필드는 B2, 후속 생존 검사·취소 부수 처리는 B3, 만료 스윕은 B7, PI 입금액 산정·여신·게이트는 타 묶음.
용어 정의 — **자동 전이** = "대상 상태를 사람이 고르지 않고 규칙이 도출한 전이". `actor`는 NULL(스윕)이거나 유발자(입금 기록자·후속 전표 생성자)일 수 있다. **사람 전이** = 공개 API로 대상 상태를 지정.

### (b) DESIGN 근거
§7.2(상태 열거·코드 고정), §2 ADR-11(상태는 코드 고정), §17.2(행 잠금+version 409), ADR-0038(총수 고정·사람/자동 구분·차단 3층)·ADR-0041("죽은 열거" 정의 = DESIGN 근거·소비 세션 없는 값)·§7.3(PI "입금 매칭 시 상태 자동 전환")·WBS S3-1 "상태 열거 구현(§7.2 그대로)".

### (c) 데이터 변경
공통: `status VARCHAR(20) NOT NULL` + `CHECK ck_<table>_status_valid`(`value_in`, 네이티브 ENUM 금지) + StrEnum. 최장값 PARTIALLY_ALLOCATED(19자). 헤더 테이블 생성은 A 묶음 마이그레이션에 포함되며 B는 컬럼·CHECK 요구만 낸다(신규 테이블이라 추가 마이그레이션 성격 아님).

**열거 값 (CHECK·StrEnum에 전부 싣는다)**

| 문서 | 값 | 비고 |
|---|---|---|
| QT (5) | DRAFT·ISSUED·CONVERTED·EXPIRED·CANCELLED | 종결: EXPIRED·CANCELLED. 초기: DRAFT |
| PI (5) | ISSUED·PARTIALLY_PAID·PAID·EXPIRED·CANCELLED | **초안 없음**(§7.2 "발행→"). 종결: EXPIRED·CANCELLED. 초기: ISSUED |
| SO (8) | RECEIVED·CONFIRMED·PARTIALLY_ALLOCATED·ALLOCATED·IN_SHIPMENT·COMPLETED·ON_HOLD·CANCELLED | 초기: RECEIVED. 종결: CANCELLED(COMPLETED는 S3-2 소관). **RESERVED 4**: PARTIALLY_ALLOCATED·ALLOCATED(S4-2)·IN_SHIPMENT·COMPLETED(S3-2) |
| PO (6) | ISSUED·SUPPLIER_CONFIRMED·PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED·CANCELLED | **초안 없음**(§7.2 "발행→"). 초기: ISSUED. "(수입선적 연결)"은 괄호 = 선택 단계이며 **상태가 아니라 수입선적 라인→PO 라인 참조 관계**(소유: S3-2 — 상태가 필요하면 그때 CHECK 1값 추가). **RESERVED 3**: PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED(입고 후반 — 소유 세션 부재, D-B1). 종결: CLOSED·CANCELLED |

이름 판정: SO "선적진행"=`IN_SHIPMENT`(제안 2:1 다수·S3-2 선적 상태 IN_* 계열과 일관), PO "공급사확인(OC)"=`SUPPLIER_CONFIRMED`(§7.2 문면 직역 — 'CONFIRMED'가 "발주 확정=PO 발행"과 혼동되지 않도록 접두 SUPPLIER_ 고정), PO "입고완료"=`FULLY_RECEIVED`(SO의 RECEIVED=접수와 어휘 충돌 회피).

**전이표 — 허용 25방향(사람 15 + 자동 10) / 미허용 101 / 총 126쌍(자기전이 제외)**

| 문서 | 쌍 | 허용 | 미허용 | 사람 | 자동 |
|---|---|---|---|---|---|
| QT | 20 | 6 | 14 | 3 | 3 |
| PI | 20 | 8 | 12 | 1 | 7 |
| SO | 56 | 8 | 48 | 8 | 0 |
| PO | 30 | 3 | 27 | 3 | 0 |
| 합 | 126 | 25 | 101 | 15 | 10 |

QT 6방향
1. DRAFT→ISSUED — 사람·**동결 액션(`issue`)**·사유 불요
2. DRAFT→CANCELLED — 사람·사유 필수(초안 폐기=취소, 번호는 남는다 — B4·B9)
3. ISSUED→CANCELLED — 사람·사유 필수(후속 생존 시 409 — B3)
4. ISSUED→CONVERTED — **자동(연쇄)**: 후속 전표(PI 또는 SO) 첫 생성과 같은 트랜잭션
5. CONVERTED→ISSUED — **자동(연쇄)**: 마지막 살아 있는 후속이 CANCELLED·EXPIRED가 되는 같은 트랜잭션(복귀 대칭)
6. ISSUED→EXPIRED — **자동**: 만료 스윕, 또는 5의 복귀 직후 유효기간 경과 시 같은 트랜잭션 즉시 수렴(조건 A). CONVERTED→EXPIRED 직접 엣지는 없다(복귀 후 2행으로 기록).
   → 불변식: **QT.status='CONVERTED' ⇔ 살아 있는 후속(PI·SO) ≥ 1**(ISSUED 이후 상태 한정). 그래서 CONVERTED→CANCELLED 직접 엣지는 두지 않는다(후속을 먼저 취소하면 자동 복귀하고 그때 ISSUED→CANCELLED).

PI 8방향
1. ISSUED→CANCELLED — 사람·사유 필수(입금이 붙은 PI[PARTIALLY_PAID·PAID]는 취소 불가 — 입금 역기록으로 ISSUED 복귀 후에만, 살아 있는 후속 SO가 있으면 409)
2. ISSUED→EXPIRED — 자동(스윕·B7. 입금 0·후속 SO 없을 때만)
3~8. **입금 수렴 6방향** {ISSUED, PARTIALLY_PAID, PAID} 상호 전부 — 자동. 누적 입금액의 순수 함수 `derive_pi_status(received_total_amount, due_amount)`만이 대상 상태를 정한다(0→ISSUED, 0<x<due→PARTIALLY_PAID, x≥due>0→PAID; `due_amount`=PI의 선수금 청구액 — 컬럼은 A·E 소유, 가정명 `advance_due_amount`; `due_amount=0`(선수금 T/T 아님)이면 수렴 대상 아님·no-op). 진입 함수는 **`pi.service.converge_payment_status(session, pi_id, received_total_amount, actor_user_id)` 단일 진입점**. 호출자: S3-3 payments(입금 기록·정정 시). S3-1은 이 함수를 직접 호출하는 테스트로 검증하고(호출자 없는 죽은 문이 아니라 **명문화된 S3-3 소비 계약** + PI 입금 게이트가 이 상태를 읽는다), 역방향 3방향은 입금 정정 후 복원용이다. 수렴 결과 ISSUED가 되었는데 유효기간 경과 & 후속 SO 없음이면 같은 트랜잭션에서 EXPIRED로 추가 수렴(로그 2행).
   - **EXPIRED·CANCELLED PI에 입금이 오면 수렴 불가**(엣지 없음) → `converge_payment_status`는 409 `TRADE_DOCS.PAYMENT.PI_NOT_OPEN`으로 fail-closed 거부한다. 사람은 새 PI를 복제 발행(B2 `copied_from_id`)한 뒤 입금을 기록한다. 이 계약은 S3-3이 소비한다(D-B3).

SO 8방향(전부 사람)
1. RECEIVED→CONFIRMED — 사람·**동결 액션(`confirm`)**·게이트·승인·할당 포트(C·D·E 소비, B5)
2. RECEIVED→ON_HOLD — 사유 필수
3. RECEIVED→CANCELLED — 사유 필수
4. CONFIRMED→ON_HOLD — 사유 필수
5. CONFIRMED→CANCELLED — 사유 필수(후속 생존 시 409, 열린 승인 철회·할당 해제 포트 — B3)
6. ON_HOLD→RECEIVED — 재개(보류 직전이 RECEIVED였을 때만)
7. ON_HOLD→CONFIRMED — 재개(보류 직전이 CONFIRMED였을 때만·**게이트 재평가 없음**: 확정 시점 판정 유지·승인은 확정에 이미 소비)
8. ON_HOLD→CANCELLED — 사유 필수
   - **재개 목표 판정 = `confirmed_at`(SO 헤더 SYSTEM 컬럼)**: `confirmed_at IS NULL`이면 직전=RECEIVED, 아니면 CONFIRMED. 불일치 to는 409 `TRADE_DOCS.RESUME.TARGET_MISMATCH`. (상태이력 최신 hold 행 조회 대신 컬럼을 원천으로 삼는 이유 — B2의 DB CHECK로 "확정된 SO가 RECEIVED로 되돌아가는" 위반을 구조로 막을 수 있다. 상태이력과의 일치는 테스트로 대사.) 보류는 RECEIVED·CONFIRMED에서만 진입.
   - 자동 0. RESERVED 4상태는 in/out 엣지 0.

PO 3방향(전부 사람)
1. ISSUED→SUPPLIER_CONFIRMED — OC 기록(부속: `oc_received_on` 필수·`oc_reference` 선택, 같은 요청·같은 트랜잭션)
2. ISSUED→CANCELLED — 사유 필수
3. SUPPLIER_CONFIRMED→CANCELLED — 사유 필수(수입선적·입고 후속 생존 시 409)
   - PO 생성 = 발행 = **발주 확정(사람 1클릭 + Idempotency-Key)** — 유일 생성 경로(4금 ①). 자동·시스템·인테이크가 PO를 만들 수 없음을 아키텍처 테스트로 고정.
   - RESERVED 3상태 in/out 엣지 0.

**사유 규칙**: 모든 CANCELLED·SO→ON_HOLD는 사람 입력 필수(TEXT 1~500자, `btrim` 후 공백 불가). 모든 EXPIRED는 서비스가 자동 문구 기록(`_AUTO_REASONS` 선례). 그 외 전이는 사유 선택(자동 전이도 서비스가 사유 문구를 채워 이력을 읽을 수 있게 한다).

**죽은 문 처리(ADR-0041 정합)** — ADR-0041의 "죽은 열거" = DESIGN 근거·소비 세션 없는 값. SO 4·PO 3 값은 §7.2 문면·WBS "그대로 열거"로 근거가 있고 소비 세션이 확정(S3-2·S4-2) 또는 공백 등재(PO 입고: D-B1)이다. 따라서 **값은 CHECK·StrEnum·UI 라벨에 지금 전부 싣는다**(후속 세션이 기존 테이블 CHECK를 수기 재정의하는 위험 제거). 그러나 **엣지는 0**이고 `machine.RESERVED[doc]`에 명시한다. 도달성 테스트는 RESERVED를 제외하고, "RESERVED에는 in·out 엣지·행이 0"을 별도 테스트가 고정한다. 후속 세션은 엣지를 추가하며 RESERVED에서 값을 빼고 총수 테스트·ADR 부기를 함께 갱신한다(ADR-0038 관용).

**상태 대입 단일 통로**: `trade_docs/transition.py`의 두 함수만 `status`를 대입하고 이력을 쓴다.
- `record_birth(session, doc, *, actor_user_id)` — 생성 시 초기 상태(이력 from NULL 행 + `.created` 이벤트).
- `record_transition(session, doc, to, *, actor_user_id, reason, automatic, approval_id=None)` — (엣지 조회 → RESERVED 거부 → 사유 검사 → `doc.status` 대입 → 이력 INSERT → 아웃박스 발행)을 한 함수에서. `automatic=False`면 사람 엣지 집합, True면 자동 엣지 집합에 속해야 한다(공개 API는 automatic을 전달할 수 없다). 검사 순서: version(409) → (to=CANCELLED이고 대상이 비종결이면) 후속 생존 검사 → 엣지 존재 → 동결 액션 전용 여부 → 사유 → 부속 필드 → 문서별 가드.
- 모델 생성자·`doc.status =`·`update().values(status=…)`·`setattr(…,'status')`는 이 모듈 밖 0건(아키텍처 스캔 + 공회전 방지 자기검사). 생성·수정 스키마에 `status`·`doc_number`·`version`·`confirmed_at`·`content_rev` 필드가 구조적으로 없고 모든 쓰기 스키마 `extra='forbid'`.

### (d) 자동화 4금 저촉 여부
① 발주 확정: PO 생성=사람 1클릭 단일 경로. 스윕·연쇄·입금 수렴 어느 자동 엣지도 PO에 없고(PO 자동 엣지 0), SO/QT/PI 자동 엣지의 도착 상태는 CONVERTED·EXPIRED·ISSUED·입금 3태뿐(약정 진입 아님). ② 법적 판정: 없음(달력·입금액 산술). ③ 대외 발송: 없음(아웃박스 내부 라우팅). ④ 장부 확정: QT/PI/SO/PO는 원장·분개·재고·채권을 만들지 않는 영업 문서(용어 충돌 G8은 ADR에서 해소 — "장부 확정=분개·마감·회계 전표·원장"). **저촉 없음.** 자동 확정 경로 부재 = `test_no_auto_confirm_code_path_exists`(PO 생성·SO confirm·QT issue 호출자가 라우터 1곳+`actor` 필수·`scheduler`·`imports`·`intake` 모듈에서 import 0).

### (e) 상태·전이·불변
위 전이표가 정본. 불변: 종결(EXPIRED·CANCELLED)에서 출구 0. 열거 StrEnum ↔ machine ↔ DB CHECK 정의문(`pg_get_constraintdef`) 3자 1:1. 총수 고정 EXPECTED={QT:(6,14), PI:(8,12), SO:(8,48), PO:(3,27)}(사람 15·자동 10). 자동 엣지를 공개 API `to`로 요청하면 409. QT 불변식(CONVERTED⇔살아 있는 후속)은 B3 수렴 함수와 테스트로 유지.

### (f) 테스트 배분
- **K(architecture)** `test_doc_machines.py`: 총수(문서별 허용·미허용, 사람/자동 집계) / 도달성·탈출성(RESERVED 제외) / RESERVED in·out 0 / StrEnum↔machine↔CHECK 3자 대조 / 상태 대입 스캔(+자기검사) / 쓰기 스키마에 status 등 부재·`extra=forbid` 전수 / `to` Literal에 자동 엣지·RESERVED·동결 엣지 부재 / 자동 엣지 집합이 정확히 QT{4,5,6번}·PI{2~8번} / `test_no_auto_confirm_code_path_exists` / PO 자동 엣지 0.
- **A(integration, 서비스 전수)**: 4종×전 쌍 파라미터라이즈 **126쌍** — 허용 25(사람 15는 API/서비스, 자동 10은 서비스 직접 호출 — `converge_payment_status`·연쇄·스윕 함수)는 성공, 미허용 101은 409 전건(ADR-0038의 110쌍 전수 선례). 사유 누락·공백 422. 재개 목표 일치/불일치(+`confirmed_at`↔이력 대사). 입금 수렴 6방향 왕복(0→일부→완납→역기록 복원)·EXPIRED/CANCELLED PI 입금 거부·PARTIALLY_PAID/PAID 취소 409. 자동 전이도 이력 1행+`automatic=true`.
- **e2e 대표**: QT 발행→PI→SO 접수 관통 중 상태 표시, SO 보류→재개→취소, PO 발행→OC.
- **J**: 더블클릭 전이(같은 Idempotency-Key → 이력 1행), 동시 두 전이 직렬화(하나 성공·하나 409).
- **변이 점검 대상**: 전이표 한 행 삭제·추가, `record_transition` 엣지 검사/사유 검사 제거, `confirmed_at` 기반 재개 판정 제거, 자동/사람 집합 혼용.

### (g) 소비·등재
소비: S3-2(SO CONFIRMED→IN_SHIPMENT 등 엣지 추가+RESERVED 감소+총수 갱신), S3-3(`converge_payment_status` 호출), S4-2(할당 상태 엣지), 입고 소유 세션(PO 후반), D 묶음(SO 생성 = `record_birth` + 벌크 = 건별 독립 TX로 같은 함수 재사용), C 묶음(confirm 응답에 `approval_id` 기록). 부채: D-B1(PO 후반 소유 공백), D-B4(SO 부분출하 잔량 종결). 설계 보강 필요: **DESIGN §7.2에 코드값·전이표 명문화 절(ADR-0038 서식)** + ADR-0051.

### (h) 되돌리기 비용 — **중간**
전이표·총수는 코드+테스트 상수라 엣지 추가는 낮다(ADR 부기+총수 갱신). PI/PO에 초안을 "나중에 추가"하려면 CHECK 재정의 마이그레이션 1건+동결 시점·생성 API 재정의가 따라와 중간~높음(그래서 §7.2 문면 대조로 지금 확정). 값 8·6을 CHECK에 미리 싣는 결정은 되돌릴 이유가 없다(후속이 채우는 쪽이 항상 저렴).

---

## B2. 동결 시점·열 분류 레지스트리·수량/납기/단가 변경 경로·강제 계층

### (a) 목적·경계
"확정 후 단가·환율 불변"(WBS S3-1 DoD, §20 A)을 **열 단위 기본 동결**로 만든다. 경계: 컬럼 이름·환율 저장 위치는 A, 승인 결속은 C, 게이트는 D·E. B는 (1) 동결 시점 (2) 열 분류 규칙과 레지스트리 (3) 확정 후 변경 경로 (4) 3층 강제를 정한다.

### (b) DESIGN 근거
§2 ADR-05(확정 전표 불변·취소/정정 세칙 "역순 취소만·원본 불변+반대 부호 신규 기록")·§20 A·WBS S3-1 DoD·§17.5(가능한 불변식은 DB, 행 교차는 CHECK 불가)·ADR-0033(확정 후 편집 409)·ADR-0038(3층 관용)·ADR-0028/0040(트리거 반복 기각).

### (c) 데이터 변경(A 소유 테이블에 대한 B 요구 컬럼·CHECK)
- 4 헤더 공통: `doc_number`(B4), `status`(B1), `version`(VersionMixin), `internal_note TEXT NULL`, `assignee_id BIGINT NULL FK users`(handover targets 등록 — F), `copied_from_id BIGINT NULL FK 자기참조 RESTRICT`(**QT·PI·SO·PO 4종**) + `CHECK copied_from_id <> id`.
- QT: `valid_until DATE NULL` + `CHECK status='DRAFT' OR valid_until IS NOT NULL`. PI: `valid_until DATE NOT NULL`.
- SO 전용: `confirmed_at TIMESTAMPTZ NULL`(최초 확정 시각, SYSTEM — 재설정·해제 없음) + `CHECK ck_sales_orders_confirmed_at_consistent`: `status='RECEIVED' → confirmed_at IS NULL`, `status IN ('CONFIRMED','PARTIALLY_ALLOCATED','ALLOCATED','IN_SHIPMENT','COMPLETED') → confirmed_at IS NOT NULL`(ON_HOLD·CANCELLED는 제약 없음). 이로써 "확정된 SO가 다시 RECEIVED(편집 가능)로 되돌아가는" 위반이 DB에서 불가능하다. SO 전용 `content_rev INTEGER NOT NULL DEFAULT 0`(SYSTEM — CONTENT 컬럼(라인 포함) 변경마다 서비스가 +1) — **승인 결속 토큰**(C가 approvals에 `(subject_id, content_rev)`로 묶는다는 가정 — 승인 후 내용이 바뀌면 자동 무효, FREE 편집은 무효화하지 않는다).
- PO 전용: `oc_received_on DATE NULL`, `oc_reference VARCHAR(100) NULL` + `CHECK (status<>'ISSUED') OR (oc_received_on IS NULL AND oc_reference IS NULL)` / `status IN ('SUPPLIER_CONFIRMED','PARTIALLY_RECEIVED','FULLY_RECEIVED','CLOSED') → oc_received_on IS NOT NULL`(CANCELLED는 제약 없음 — ISSUED에서 바로 취소한 경우 NULL).
- 마이그레이션 성격: **신규 테이블 생성분**(A 마이그레이션에 동승). 기존 테이블 변경 없음. 스키마 변경은 마이그레이션 파일로만(계약).

### (d) 4금
정정 전표·자동 정정 없음. 동결 우회 자동 경로 없음. 저촉 없음.

### (e) 상태·전이·불변
**동결 시점**: QT=DRAFT→ISSUED / PI=생성(=발행) / SO=RECEIVED→CONFIRMED / PO=생성(=발행). **편집 가능 상태 = QT:DRAFT, SO:RECEIVED 두 곳뿐**(`EDITABLE_STATES={'QT':{DRAFT},'SO':{RECEIVED},'PI':∅,'PO':∅}`). ON_HOLD는 동결 취급(편집은 재개→RECEIVED에서). PI·PO는 편집 구간이 없다(잘못 입력=취소+신규).

**열 분류 레지스트리** `trade_docs/policy.py::FIELD_POLICY[table][column]` — 4 헤더 + 4 라인 테이블의 **모든 컬럼**을 정확히 하나로 등재(미등재·유령 = CI 실패, fail-closed):

| 분류 | 의미 | 예 |
|---|---|---|
| CONTENT | 편집 가능 상태에서만 수정, **동결 후 불변** | 거래처·통화·환율 스냅샷(요율·기준일)·결제조건·Incoterms·유효기간·바이어 PO번호·요청납기·은행정보 스냅샷·헤더 합계·라인 전 컬럼(품목·수량·단가·금액·라인번호) |
| ORIGIN | **생성 시 1회 결정, 이후 어느 상태에서도 불변** | 참조 FK(qt_id·pi_id·copied_from_id·인테이크 출처), SO의 partner_id(중복 PO 키·여신 잠금의 축) |
| FREE | 동결 후에도 편집 가능 | **정확히 4개: internal_note, assignee_id, oc_reference, oc_received_on** |
| SYSTEM | 서비스 통로 외 대입 불가 | id·doc_number·status·version·confirmed_at·content_rev·감사 컬럼·deleted_at |

- FREE를 이 4개로 좁힌 근거: 인계 일괄 UPDATE(ADR-0015)가 동결 전표에서도 assignee_id를 바꿔야 하고, 내부 메모·OC 기록(외부 사실의 기록이지 거래 조건이 아님)은 오기 정정이 필요하다. `expected_receipt_date`(strict 제안)는 §11 "예상 가용일이 PO 입고예정에 자동 추종"의 원천이 PO 라인 컬럼인지 수입선적 ETA인지 문면이 침묵해 **만들지 않는다**(S3-2·S3-4 소비 세션이 필요하면 가산 nullable 컬럼 1건 — 저비용).
- **요청납기는 CONTENT**(S3-2 마일스톤·§11 백오더의 기준값 — 조용한 변경 금지). SO RECEIVED 중에는 단가·통화·환율 포함 CONTENT 전부 편집 가능(게이트 단가 편차 해소·오입력 정정) — ORIGIN(거래처·참조)만 잠긴다.
- 라인 변경(추가·삭제·수정·순서)은 헤더 version과 SO `content_rev`를 함께 올린다(라인만 바뀌어도 부모 낙관 잠금이 상승 — S1-3 PR-3 결함 재발 방지). 동결 이후 라인 id 삭제·재생성 금지(후속 FK 대상 안정 id).

**수량·납기·단가 변경 경로 = 취소+신규(단일 경로)**. 정정 전표(승인 동반 개정본)는 만들지 않는다(문면에 없고 신규 테이블·상태·승인 유형·잔량 재계산을 부른다). 절차: ① 원 전표 취소(사유, 역순 규칙 B3) ② 신규 생성 시 `copied_from_id`로 원본 지정 → 프런트가 원 값을 프리필(재입력 화면 없음 — ADR-05). 서비스가 `TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE`(409)로 검증: 동일 문서 유형·동일 거래처·**원본 상태 ∈ {CANCELLED, EXPIRED}**(살아 있는 전표 복제 = 중복 생성 차단). 이 참조는 계보 표시이며 사슬 후속이 아니다(B3의 살아 있음 판정에서 제외). 취소와 복제는 **두 개의 독립 동작**(각각 멱등 키·원자) — 결합 액션은 두지 않는다. 새 SO는 새 승인이 필요하다(옛 승인은 옛 content_rev/SO에 묶임).

**강제 3층 + 트리거·해시 미채택**
1. DB: 위 CHECK들(상태값·confirmed_at 정합·QT valid_until·PO OC·copied_from 자기참조 금지)·수량>0·금액≥0·통화 CHAR(3) 대문자·FK RESTRICT·doc_number UNIQUE·형식 CHECK. (행 교차인 "상태 의존 동결"은 CHECK로 표현 불가 — 인정.)
2. 서비스: 모든 수정 진입점은 `update_document`/`replace_lines`/`update_meta` 3함수뿐 — 행 `FOR UPDATE` → 상태 재조회 → CONTENT/ORIGIN 필드가 현재값과 다르게 오면 409 `TRADE_DOCS.DOCUMENT.FROZEN`(detail=필드명 목록만, 값·금액 미기재 — fail-visible). 갱신 스키마 분리: `…UpdateRequest`(편집 가능 상태: CONTENT+FREE) / `…MetaUpdateRequest`(FREE 4개만) — 가격 필드가 메타 경로에 구조적으로 도달 불가. 라인 개별 PATCH/DELETE 엔드포인트 없음(편집 가능 상태의 전체 교체 `replace_lines`만).
3. 아키텍처 테스트: FIELD_POLICY 완전성(모델 반사 ↔ 등재 1:1), **핀 고정** — 단가·통화·환율 컬럼·수량·요청납기·거래처가 CONTENT/ORIGIN임(조용한 FREE 재분류 불가), FREE 집합 정확히 4개(추가는 ADR), 라인 쓰기 함수가 모두 `assert_editable` 경유(AST/정규식), 상태 대입 단일 통로(B1).
- **DB 트리거 미채택**: 리포 `CREATE TRIGGER` 0건, 불변 강제는 REVOKE 권한(ADR-0040)으로 통일. 이 4종은 초안 편집·FREE 인계 UPDATE·상태 갱신이 앱 계정의 정상 쓰기라 권한 회수 불가, 상태 의존 동결은 CHECK로 표현 불가, 트리거는 인계 일괄 UPDATE 예외 로직·plpgsql 마이그레이션 부담을 만든다. **GC-A4 해석 명기(ADR-0052)**: GC-A4("UPDATE/DELETE 불가—DB 권한")는 S4-1 매핑의 출고 전표·원장 문면이며, S3-1은 그 중 ADR-05 취소 세칙(원본 불변·취소가 원본 참조 기록)만 유추 적용한다 — 리뷰어가 S3-1 부채로 오독하지 않게 한다.
- **동결 해시/다이제스트 미채택**: 후속 세션이 CONTENT 컬럼을 추가하면 기존 동결 행의 해시가 전부 불일치하는 스키마 진화 위험, DESIGN·WBS 근거 없음, 직접 SQL 변조는 앱 경로 밖 위협. 재판정 트리거는 D-B6.

### (f) 테스트 배분
- **A(integration) `test_confirmed_prices_and_fx_are_immutable`(DoD 증명)**: (1) 문서 유형×FIELD_POLICY의 CONTENT·ORIGIN 컬럼 전수 parametrize PATCH → 409·행 전체 스냅샷(컬럼·version·updated_at) 불변 (2) 라인 추가·삭제·수량·단가 변경 409 (3) 마스터 가격표에 새 유효 단가·환율(A가 정한 위치)을 넣은 뒤에도 확정 전표 라인·환율 값 불변 (4) `price_at` 부재 라인의 QT 발행·PI 생성·SO 확정은 422(0원 확정 금지) (5) **성공 방향(양방향 원칙)**: DRAFT/RECEIVED 편집 허용, 동결 후 FREE 4종은 허용+version 증가, RECEIVED SO의 CONTENT 편집이 `content_rev`를 올리고 FREE 편집은 올리지 않음, 확정 SO가 RECEIVED로 되돌아갈 수 없음(DB CHECK 직접 UPDATE 시 23514) (6) 정정 경로: 원본 CANCELLED+새 번호 새 문서+`copied_from_id`, 원본 CONTENT 불변, 타 거래처·타 유형·라이브 원본 복제 거부 (7) 라인 변경이 헤더 version을 올림·stale version PATCH 409.
- **J**: 동결 직전 PATCH와 동결(issue/confirm) 동시(실스레드+Barrier) → 한쪽만 성공, 최종 값=동결 시점 값 / 확정 더블클릭 → 전표 1건·이력 1행.
- **K**: FIELD_POLICY 완전성·핀·FREE 정확히 4·`assert_editable` 미경유 라인 쓰기 0·갱신 스키마 extra=forbid·PO OC CHECK·SO confirmed_at CHECK 서비스 우회 SQL 위반(23514).
- **GC 후보(F 묶음 GC v1.4)**: "확정 후 단가·환율 불변" 양방향(거부+초안 편집 성공).
- **변이 점검 대상**: FIELD_POLICY에서 CONTENT 1개 제거, 라인 쓰기의 부모 잠금·상태 재조회 제거, `replace_lines`의 version/content_rev 증가 제거, `confirmed_at` CHECK 제거.

### (g) 소비·등재
소비: A(컬럼 전수 분류표 작성 의무 — 특히 환율 스냅샷 컬럼명·기준일, 라인 통화·할인, PI 은행정보), C(`content_rev` 결속), D(SO 접수 편집·copy), F(handover targets 4헤더 assignee 등록·인계 UPDATE는 서비스 우회 스캔의 예외 목록에 assignee_id 한정). 등재: 신규 CONTENT 컬럼을 더하는 후속 세션은 분류를 강제당한다(조용한 미동결 불가). 관찰: 정정 전표 재판정 트리거(취소+신규 빈도 실측 후 — D-B5), 동결 다이제스트(D-B6). 설계 보강 필요: DESIGN §7 보강(동결 시점·분류·정정 경로)+ADR-0052.

### (h) 되돌리기 비용
FREE 확장(분류 1줄+핀 테스트 수정+ADR): 낮음. 취소+신규 → 정정 전표 전환: **높음**(신규 테이블·상태·승인 재정의 — 문면 근거 없어 지금은 하지 않음, 관찰 트리거 등재). 트리거 추가: 낮음(마이그레이션 1건). 분류 레지스트리·`confirmed_at`·`content_rev`는 신규 테이블 시점이라 지금이 최저가.

---

## B3. 후속 생존 판정·취소=상태 전이·역순 취소 직렬화·SO 취소 부수 처리

### (a) 목적·경계
ADR-05 "후속 전표가 살아 있으면 선행 취소 불가(역순 취소만)"와 "역기록 = 원본 불변 + 반대 부호 신규 기록(취소가 원본 참조)"을 4종 전표에 구현한다. 경계: 원장 수량을 만지는 반대 부호 기록은 S3-2 선적·S4-1 원장 몫. 열린 승인 철회 함수는 C, 여신 노출 산식은 E.

### (b) DESIGN 근거
§2 ADR-05, §20 A("역방향 정정 후 잔액 복원(후속 생존 시 선행 취소 차단 포함)"), §8.3("SO 취소 시 할당 해제"), §17.2(깨지는 지점만 행 잠금), §17.3(취소 번호 재사용 금지).

### (c) 데이터 변경
**신규 컬럼·테이블 없음**(취소 사유·행위자·일시의 유일 원천 = 상태이력 행 — B6; 헤더에 `cancelled_*` 복제 컬럼을 두지 않는다 — 원천 이중화 금지). 코드 상수·등록부만.
- `chain.py::CHILD_LINKS: tuple[ChildLink(parent_doc, child_table, fk_col, dead_statuses, live_exists=None)]` — S3-1 등록: QT←PI(qt_id)·QT←SO(qt_id)·PI←SO(pi_id). `live_exists` 콜백은 라인 경유 후속(선적 라인→SO 라인 등)용 확장점. 검사 술어 = `deleted_at IS NULL AND status NOT IN dead_statuses`.
- `chain.has_live_children(session, doc_type, doc_id)` / `chain.live_children_numbers(...)`(에러 detail용 문서번호 목록).
- `NON_CHILD_FK_ALLOWLIST`(사유 필수): `copied_from_id` 자기참조(계보), 상태이력 FK, approvals·documents·comm_logs 등 폴리모픽(FK 없음).

### (d) 4금
취소는 사람만(자동 취소 경로 없음). QT/PI 연쇄 전이(B1의 4·5번)는 취소·확정이 아니라 파생 상태 수렴이며 기록만 한다. 저촉 없음.

### (e) 상태·전이·불변
**살아 있음(LIVE) = `deleted_at IS NULL AND status NOT IN ('CANCELLED','EXPIRED')`**. COMPLETED·CLOSED·FULLY_RECEIVED·ON_HOLD도 살아 있다(이행된 체인의 선행 취소 사고 차단). 판정 기준은 `machine.DEAD_STATUSES=(CANCELLED, EXPIRED)`에서 생성해 인덱스 술어 등과 이중 정의하지 않는다.

**취소 = 상태 전이(역전표 없음)**: QT/PI/SO/PO는 원장·수량·회계를 직접 쓰지 않으므로 "반대 부호 신규 기록"은 (1) 원본 CONTENT 불변 (2) 원본을 참조하는 신규 불변 이력 행(사유·행위자·일시)이 충족한다. 여신 노출·미결 잔량·백오더는 LIVE 전표에서 **파생**되므로 취소만으로 자동 복원되고 별도 복원 쓰기가 없다(E 산식은 `status<>'CANCELLED'`/LIVE 필터를 써야 한다 — 의존). 이 해석을 ADR-0052에 명기(ADR-05 문면의 "전표·원장 공통" 적용 범위 판정).

**역순 취소 강제**: 취소 요청(to=CANCELLED, 대상 비종결) 시 **엣지 검사보다 먼저** 살아 있는 후속을 검사해 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(detail=후속 문서번호 목록 — 금액 없음; 예: CONVERTED QT를 취소하려 하면 "먼저 취소할 PI/SO" 안내). 취소 성공 후 **부모 연쇄 수렴**: `chain.converge_parent(session, child, actor)` — 부모 QT를 (살아 있는 후속 있음→CONVERTED / 없음→유효기간 미경과 ISSUED·경과 EXPIRED)로 맞춘다(B1 QT 4·5·6번, `automatic=True`, 행위자=취소한 사람). 후속이 EXPIRED(스윕)로 죽을 때도 동일 호출.

**직렬화(잠금 순서 규약)**: 거래처 행(E의 여신 잠금) → **사슬 상위→하위(QT→PI→SO)** → 라인(id 오름차순) → doc_number_seq(항상 마지막). 취소·후속 생성 모두 `chain.lock_chain(session, doc)`로 조상을 위에서부터 잠근 뒤 자기 행을 잠근다(ORIGIN 컬럼이라 참조는 불변 → 재검증 불요). 후속 생성은 부모를 `FOR UPDATE`로 잠그고 상태·유효기간을 재확인(부모가 CANCELLED/EXPIRED/DRAFT면 409 `TRADE_DOCS.PARENT.NOT_USABLE`, 유효기간 경과면 422 `TRADE_DOCS.VALIDITY.EXPIRED` — B7), 취소도 부모·자기 `FOR UPDATE` 후 후속 검사 → 동시 "취소 vs 후속 생성"에서 정확히 한쪽만 성공한다. (제안의 FOR SHARE는 부모 QT 상태 갱신(연쇄)이 어차피 UPDATE 잠금을 요구하므로 FOR UPDATE로 통일.)

**SO 취소 트랜잭션 순서(한 함수 `sales_orders.service.cancel_order`에 고정)**: ① 조상→SO 잠금 ② 상태 검사(RECEIVED/CONFIRMED/ON_HOLD) ③ 살아 있는 후속 검사(S3-1은 SUCCESSOR 없음, S3-2가 선적을 등록) ④ **열린 승인 요청 철회**: C 제공 `approvals.service.withdraw_open_for_subject(session, subject_type='SALES_ORDER', subject_id, actor_user_id)` — 이미 결정된(APPROVED·소비 완료) 승인은 건드리지 않는다(승인은 그 SO에 묶인 소비 완료 기록, 새 SO는 새 승인) ⑤ `AllocationPort.on_cancelled(session, order)`(B5 — P3 기본은 NOT_IMPLEMENTED) ⑥ `record_transition(CANCELLED, 사유)` ⑦ 부모 연쇄 수렴 ⑧ 아웃박스는 ⑥·⑦ 안에서. 어느 단계든 실패하면 전체 롤백.

**번호 재사용 금지**: 채번은 단조 증가(감소·재발급 경로 없음), 전표 삭제 경로 없음(B9), doc_number 전역 UNIQUE(B4). 취소 SO의 **바이어 PO번호는 재사용 가능**(정정=취소+신규가 같은 번호를 다시 받아야 함 — D·E의 부분 유니크 술어에서 `status<>'CANCELLED'` 제외; 자사 doc_number와 별개 축).

### (f) 테스트 배분
- **A** `test_reverse_order_cancellation_restores_balances`(§20 A): QT→PI→SO(확정) 체인에서 QT 취소·PI 취소 409, SO 취소 성공 → 여신 노출·SO 미결 잔량이 착수 전 기준선으로 복원 → PI 취소 성공(QT 자동 복귀 ISSUED, 이력 `automatic`) → QT 취소 성공 / 순서 어긴 시도는 이력 0행 추가·상태 불변 / EXPIRED·CANCELLED 후속은 취소를 막지 않고 ON_HOLD·COMPLETED 후속은 막음 / SO 취소 시 열린 승인 철회·결정된 승인 불변(C와 결합) / 취소 후 같은 PI에서 새 SO 생성 가능 / 후속 사망 시 QT 복귀(ISSUED)·유효기간 경과면 같은 TX에 EXPIRED 2행 / 취소 번호 재사용 0(새 번호=마지막+1) / `CHILD_LINKS`에 QT·PI·SO·PO 4키 존재.
- **J**: 동시 "취소 vs 후속 생성"(QT 취소 vs PI 생성, PI 취소 vs SO 생성) 20회 반복 — 정확히 1건 성공·취소된 부모 아래 생존 자식 0 / 교차 순서 잠금 데드락 0 / 취소 중간 실패 → 상태·이력·이벤트·승인 철회 전부 롤백.
- **K**: `test_every_fk_to_chain_docs_is_registered`(4 헤더·라인 테이블을 가리키는 모든 FK가 `CHILD_LINKS` 또는 `NON_CHILD_FK_ALLOWLIST`에 있어야 함 — S3-2 선적이 등록을 잊으면 CI가 잡는다) / 취소 스캔(`cancel_order`가 위 순서 호출).
- **변이 점검 대상**: 살아 있음 술어에서 `deleted_at`·CANCELLED/EXPIRED 제외 제거, 부모 FOR UPDATE 제거, 취소 시 승인 철회 제거, 부모 연쇄 수렴 제거.

### (g) 소비·등재
S3-2: `CHILD_LINKS`에 shipments 등록(누락은 K 테스트가 실패로 알림)·SO 취소 시 후속 선적 판정. S4-2: `AllocationPort` 실구현. C: `withdraw_open_for_subject`. E: 노출 산식 LIVE 필터. D: 취소 SO의 바이어 PO번호 재사용. 설계 보강 필요: DESIGN §2 ADR-05 취소 세칙 적용 범위 부기+ADR-0052.

### (h) 되돌리기 비용 — **낮음~중간**
LIVE 정의·레지스트리·훅 순서는 코드 상수. "취소=전이"→"역전표" 전환은 높지만(신규 테이블·번호체계) 원장·수량을 쓰지 않는 한 그럴 이유가 없고, S3-2/S4의 원장 역기록은 별도 세칙이다.

---

## B4. 채번 — 접두어·발급 시점·연도 기준·UNIQUE

### (a) 목적·경계
`SO-2026-0001` 형식(§3)을 4종에 적용하고 발급 시점·연도 기준·전표 쪽 UNIQUE를 확정한다. 경계: 스테이징 행에는 번호를 발급하지 않는다(D — 자체 식별자·파일 해시로 멱등, 승격해 SO 행이 될 때만 발급).

### (b) DESIGN 근거
§3 채번 SO-2026-0001, §17.3(MAX+1 금지·행 잠금·발급 번호 UNIQUE 이중 안전·**취소 번호 재사용 금지**), §2 ADR-02·§22 렌즈 6(UTC 저장·KST 표시), §17.4(멱등 UNIQUE는 부분 인덱스 — 삭제 후 재유입 허용).

### (c) 데이터 변경
- **접두어 = QT·PI·SO·PO**(4종). `doc_number_seq.prefix`는 `String(20)`·CHECK 없음 그대로(마이그레이션 없이 후속 SH·CI·PL 확장). 정본은 kernel `DocType(StrEnum)` 1곳, 채번 호출은 kernel 래퍼 `issue_document_number(session, doc_type, *, at=None)` 하나(`next_document_number` 직접 호출은 kernel 밖 0 — AST 스캔).
- **연도 기준 = 발급 시각의 KST 연도**: `numbering/service.py`의 `year = (at or utcnow()).year` → `year = to_kst(at or utcnow()).year`(1줄+import+독스트링, 모델·카운터 스키마·시그니처 불변). 문서일자 기준은 배제(과거일자 입력이 지난 해 카운터를 재개방·비단조). naive `at`은 `to_kst`가 ValueError로 거부.
- 각 헤더: `doc_number VARCHAR(20) NOT NULL`, `UNIQUE uq_<table>_doc_number`(**전역 UNIQUE — 부분 인덱스 아님**), `CHECK ck_<table>_doc_number_format`(`doc_number ~ '^QT-[0-9]{4}-[0-9]{4,}$'` 등 접두어 고정 — 타 접두어 거부). 자릿수 4(초과 시 증가 허용, 중복 아님).
- 마이그레이션 성격: 신규 테이블 동승. 채번 서비스는 코드 변경만.

### (d) 4금
해당 없음.

### (e) 상태·전이·불변
**발급 시점 = 행 최초 저장 시(4종 공통), 같은 트랜잭션, 서비스의 마지막 단계(모든 검증·라인 구성·게이트 후, INSERT 직전)**: QT=DRAFT 생성 시 / PI=생성(발행) 시 / SO=접수(RECEIVED) 행 생성 시(스테이징 승격 TX) / PO=생성(발행) 시. **잠금 보유 시간 최소화**(카운터 행 잠금은 TX 끝까지 유지되고 lock_timeout이 500으로 노출되는 위험 — B8 핸들러와 세트). 결번 = 0: 발급된 번호는 항상 실재 행으로 남고(취소도 행, 전표 삭제 경로 없음 — B9), 롤백 시 카운터도 같은 트랜잭션이라 되돌아간다. 초안 폐기는 CANCELLED 전이(사유 필수 — UI가 "초안 폐기" 문구를 프리필). doc_number는 SYSTEM(생성 후 불변, PATCH·스키마에 없음).
**전역 UNIQUE 판정(§17.4와의 관계)**: §17.4의 "부분 인덱스"는 삭제 후 **재유입을 허용**하려는 장치인데, 자사 발급 번호는 재발급이 **금지**(§17.3) 대상이라 목적이 정반대다. 카운터가 단조라 전역 UNIQUE가 정상 흐름을 막을 일이 없다(markets.code 전역 UNIQUE 선례 — ADR-0032). §17.4 열거의 "문서번호"는 외부 유입 멱등의 (partner_id, 바이어 PO번호) 축을 가리키는 것으로 해석하고 ADR-0053·DESIGN §17.3 부기에 명기한다.

### (f) 테스트 배분
- **J**: 4종 각각 서비스 레벨 동시 100건 create(`run_concurrently`) → 번호 100종·1..100 연속·UNIQUE 위반 0 / 4접두어 혼합 동시 / 생성 도중 실패 주입 → 카운터 복귀·다음 번호 동일 / 게이트·검증 실패 요청이 번호를 소비하지 않음 / 취소 후 새 전표 번호=마지막+1 / DB 안전망: 같은 doc_number 직접 INSERT → 23505, soft delete 행이 있어도 동일 번호 INSERT 거부, 타 접두어 번호 → 23514.
- **연도 경계(신규 test_numbering 확장)**: at=UTC 2026-12-31T15:00:00Z → `SO-2027-0001`(doc_number_seq에 (SO,2027) 행 생성), 2026-12-31T14:59:59Z → `SO-2026-…`, naive at → ValueError.
- **K**: 접두어 등록부(`DocType`)↔모델 `DOC_PREFIX` 상수↔CHECK 정규식 3자 일치 메타 테스트 / `next_document_number` 호출은 kernel 래퍼만(AST) / MAX+1 패턴 스캔.
- **변이 점검 대상**: FOR UPDATE 제거, year를 UTC로 되돌림, 채번을 별도 트랜잭션으로 이동, 전역 UNIQUE→부분 전환.

### (g) 소비·등재
D: 스테이징 승격 TX에서 SO 번호 발급. 후속 세션: 접두어는 DocType 멤버 추가만(마이그레이션 없음). 설계 보강 필요: **DESIGN §17.3 부기**(KST 연도·전역 UNIQUE·발급 시점) + §3 채번 문면, ADR-0053.

### (h) 되돌리기 비용 — **낮음(첫 실번호 발급 전 한정)**
연도 1줄·CHECK는 저렴하나, **실 번호가 발급된 이후에는 KST↔UTC/문서일자 기준 변경이 기발급 번호 체계와 단절되어 사실상 비가역** — 지금(호출부 0곳)이 무비용 창구다. 발급 시점을 "발행 시"로 바꾸려면 doc_number nullable화·CHECK·UNIQUE 재정의가 필요해 중간.

---

## B5. 잔량(open qty)·소비 직렬화·할당/백오더 분기 포트

### (a) 목적·경계
§7.1 "잔량(open qty)이 부분·초과 방지의 기준"을 S3-1이 선점하는 범위 — **주문수량 동결 + 잔량 산식·직렬화 계약 + 할당 포트**. 경계: 소비 코드(선적 라인·입고 라인)는 S3-2/입고 세션, 재고 잔량·할당 저장·격리는 S4-1(§8.3 "잔량 파생 테이블 유무·갱신 방식은 Phase 4 착수 ADR").

### (b) DESIGN 근거
§7.1, §7.4("확정→할당/백오더 분기(§8.3)"), §8.3, §17.1(하나의 업무 동작=하나의 TX)·§17.2(깨지는 지점만 행 잠금)·§19 P4 ADR, WBS S3-2 DoD "부분선적 1:N 잔량 정확·초과 거부", r1 G5·G6(분기 훅 주체 미배정·직렬화 시점 역전).

### (c) 데이터 변경
- SO·PO 라인: `ordered_quantity`(A 소유·타입은 A 확정 — B는 `CHECK ordered_quantity > 0` 요구, 기본단위 정수, CONTENT 동결 — B2). **소비량 유지 컬럼(shipped/allocated/received)은 만들지 않는다.**
- 코드 계약(`trade_docs/quantities.py`): `LINE_CONSUMERS: dict[Literal['SO_LINE','PO_LINE'], tuple[ConsumerSpec(name, child_table, line_fk_col, qty_col, live_predicate, kind)]]` — **S3-1 등록 0건**. `open_quantity(session, doc_type, line_ids) -> dict[int, OpenQuantity(ordered, consumed, open)]`(open = ordered − Σ(FULFILL 소비자 SUM, 살아 있는 행)). `lock_lines_for_consumption(session, doc_type, doc_id, line_ids) -> list[Line]`: **부모 헤더 `FOR SHARE`(상태 ∈ `CONSUMABLE_STATUSES` 검증 — SO={CONFIRMED}, PO={ISSUED, SUPPLIER_CONFIRMED}; 그 외 409 `TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE`) → 라인 `FOR UPDATE ORDER BY id`(교착 방지)**. 소비 절차(소비 세션 의무): ① `lock_lines_for_consumption` ② `open_quantity` 재계산 ③ 요청≤잔량 검증(초과 409 `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`·부분 허용) ④ 자기 행 INSERT — 한 트랜잭션. 헤더 SHARE가 SO 취소(헤더 FOR UPDATE)와 직렬화한다(취소 vs 소비 경쟁).
- 이 결정은 **S3-1 로컬 결정**이며 S4-1 착수 ADR이 유지 컬럼/파생 테이블로 대체할 수 있다 — `lock_lines_for_consumption`·`open_quantity` **시그니처와 라인 안정 id**가 유지되면 소비자 코드는 무영향임을 ADR-0054에 이행 계약으로 명기.
- **할당/백오더 분기 = 같은 트랜잭션 동기 포트**: `sales_orders/ports.py`(위치는 A의 모듈 배치 따름) `AllocationPort(Protocol)`: `on_confirmed(session, order) -> AllocationOutcome`, `on_cancelled(session, order) -> AllocationOutcome`; `AllocationOutcome(status: AllocationStatus, note: str)`, `AllocationStatus`는 **`NOT_IMPLEMENTED` 1값**(S4-2가 확장). P3 기본 구현 `NoAllocationPort`는 아무것도 쓰지 않고 `NOT_IMPLEMENTED`(note "재고 할당은 Phase 4에서 구현됩니다")를 반환하며 **SO confirm 응답 본문 `allocation` 필드로 그대로 노출**(할당이 된 것처럼 보이지 않게 — fail-visible). 바인딩은 `get_allocation_port()` 1곳(S4-2가 교체·항상 non-None). 포트가 예외를 던지면 확정 전체 롤백(fail-closed). 백오더 분기는 S3-4 소비 → 포트에 넣지 않는다(죽은 메서드 배제 — G5 판정: **분기 훅의 주체는 S3-1의 포트, 분기 계산·보드는 S3-4**). SO는 ALLOCATED 계열로 전이하지 않는다(RESERVED).
- 마이그레이션: 없음(스키마 변경 없음 — 컬럼은 A 소유 신규 테이블).

### (d) 4금
할당·잔량 소비는 원장을 쓰지 않는 계약 단계. 포트 구현은 외부 호출 금지(트랜잭션 안 외부 호출 금지 — 구현 모듈의 네트워크 라이브러리 import 0 아키텍처 테스트). 저촉 없음.

### (e) 상태·전이·불변
`ordered_quantity` 동결(SO=CONFIRMED부터, PO=생성부터). 소비량은 하위 문서 라인의 파생이므로 드리프트가 구조적으로 불가(진실 하나). ON_HOLD·CANCELLED·RECEIVED SO는 소비 불가.

### (f) 테스트 배분
- **A**: 소비자 없음 → open=ordered / **가짜 소비자 픽스처**(테스트 전용 임시 테이블+레지스트리 monkeypatch — 운영 스키마에 소비 코드 없는 테이블을 만들지 않는다)로 SUM 산식·소비 상태 술어·경계(정확히 0 잔량, +1 초과)·부분 소비 후 잔량·취소 후 복원 / CONSUMABLE 상태 위반 409.
- **J(실스레드+Barrier — 순차 테스트는 증거 불인정, GC-F1 문면)**: 잔량 10에 동시 7+7 소비 → 한쪽 성공·한쪽 EXCEEDS_OPEN·합계 ≤ ordered / N스레드 1씩 소비 → 정확히 ordered건만 성공 / 서로 반대 순서 라인 입력 2스레드 → 교착 0(id 오름차순) / 소비 vs SO 취소 동시 → 한쪽만 성공 / SO confirm 응답에 `allocation.status='NOT_IMPLEMENTED'`가 항상 존재하고 어떤 할당·재고 행도 생성되지 않음 / 포트 예외 주입 → 확정 롤백(상태·이력·이벤트·승인 소비 원복).
- **K**: `get_allocation_port()` 구현이 Protocol 적합·non-None / SO 서비스·라우터의 재고 원장 직접 접근 0 / 포트 구현 모듈의 외부 호출 라이브러리 import 0.
- **변이 점검 대상**: 헤더 SHARE 제거·라인 FOR UPDATE 제거·ORDER BY 제거, 잔량 검증 제거, 포트 호출 제거·결과 미노출.

### (g) 소비·등재
S3-2: `LINE_CONSUMERS['SO_LINE']`에 선적 라인 등록·`CONSUMABLE_STATUSES` 확장·`CHILD_LINKS`(B3)·SO 완료 엣지·**부분출하 후 잔량 종결 경로(D-B4)**. S4-1: 표현 교체 시 시그니처 유지. S4-2: 포트 실구현·ALLOCATED 엣지. S3-4: 백오더=open−allocated 파생. 관찰: 잔량 표현 최종안은 S4-1 ADR. 설계 보강 필요: DESIGN §8.3 부기(로컬 결정·대체 규정)+ADR-0054.

### (h) 되돌리기 비용 — **낮음**
S3-1 로컬·계약 시그니처 유지 시 표현 교체가 소비자에 무영향. 포트 반환 타입 좁힘은 S4-2 확장으로 끝. 유지 컬럼을 지금 도입하는 쪽이 오히려 되돌리기 비싸다(컬럼 제거 마이그레이션).

---

## B6. 상태 이력 테이블·이벤트(아웃박스)·audit_log 분담

### (a) 목적·경계
"상태 변경 이력"(§3 공통 규율)을 §17.5 확장 불변 테이블로 세우고, 생성·전이 전건의 아웃박스 발행 규칙을 고정한다. 경계: 이벤트 소비(알림 규칙)·승인 이력은 타 묶음.

### (b) DESIGN 근거
§3 공통 규율("상태 변경 이력"), §17.5 확장문구("이후 상태 변경 이력 성격 테이블은 신설 세션이 같은 지위로 등재")·ADR-0040(IMMUTABLE+`revoke_mutations` 서식), `certification_status_log` 원형, §17.1(아웃박스), `outbox/service.py`(payload에 원가 금지), ADR-0038 ⑤(생성+전이 전건 발행·automatic 표식).

### (c) 데이터 변경
**신규 테이블 4개(문서별)**: `quotation_status_log`·`proforma_invoice_status_log`·`sales_order_status_log`·`purchase_order_status_log`. 공용 SQLAlchemy 믹스인(`StatusLogColumns`, declared_attr로 문서별 FK)으로 코드 중복만 제거. 다형 단일 테이블 기각(FK RESTRICT 불가·상태값 CHECK가 합집합으로 약화·신규 문서 유형마다 CHECK 재정의 — 문서별 테이블은 후속이 자기 `<doc>_status_log`를 추가하는 certification 계보).

컬럼(PkMixin+Base만 — Version·Actor·SoftDelete 믹스인 제외, 함정 ⑩ 회피):

| 컬럼 | 타입 | 규칙 |
|---|---|---|
| id | BIGINT PK | |
| `<doc>_id` | BIGINT NOT NULL FK RESTRICT | |
| occurred_at | TIMESTAMPTZ NOT NULL server_default now() | UTC 저장 |
| from_status | VARCHAR(20) NULL | NULL = 생성(탄생) 행 |
| to_status | VARCHAR(20) NOT NULL | |
| reason | TEXT NULL | 사유(자유 텍스트 — 이벤트 payload·로그에 싣지 않음) |
| actor_user_id | BIGINT NULL FK users RESTRICT | NULL = 시스템(스윕) |
| automatic | BOOLEAN NOT NULL | 규칙 도출 전이(B1 정의) |
| approval_id | BIGINT NULL FK approvals | **sales_order_status_log에만** — 확정 행에 소비한 승인 참조(승인 불요 확정은 NULL). approvals 마이그레이션이 이 테이블보다 앞 리비전(C 의존) |

CHECK(문서별): `from_status_valid`(NULL 또는 문서 상태값)·`to_status_valid`·`no_self_transition`(`from_status IS NULL OR from_status<>to_status`)·`birth_row`(`from_status IS NOT NULL OR to_status = 초기상태` — QT DRAFT·PI ISSUED·SO RECEIVED·PO ISSUED)·`reason_required`(to_status가 CANCELLED [SO는 +ON_HOLD, QT/PI는 +EXPIRED] 이면 `reason IS NOT NULL`)·`reason_not_blank`(`reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500`)·`actor_or_automatic`(`actor_user_id IS NOT NULL OR automatic`). 부분 UNIQUE: `(<doc>_id) WHERE from_status IS NULL`(문서당 탄생 행 1개). 인덱스 `(<doc>_id, id DESC)`.

**불변**: `table_policy.IMMUTABLE_TABLES`에 4개 추가(사유 주석 "§17.5 확장 — 상태 변경 이력") + 마이그레이션에서 `revoke_mutations(op, table)` 4회(downgrade는 `drop_table`이 REVOKE 소멸). 조회 `GET /{docs}/{id}/status-log`(읽기 전용·페이지네이션 기본 50·id desc·금액 필드 없음). 쓰기 API 없음.

**audit_log와의 분담**: 상태 전이의 유일 이력 = 상태이력 4표(전이를 audit_log에 이중 기록하지 않는다 — certification 관용 확인: 도메인 전이는 audit를 쓰지 않는다). audit_log는 권한·보안 성격의 "막힌 시도"·권한 변경만(승인 우회 시도 등 — C 소관, commit-then-raise 선례). B는 `AuditAction` 상수를 추가하지 않는다. 일반 편집(초안·FREE)은 version·updated_by로 충분(도메인 편집 audit 없음 — 기존 관용).

**이벤트(아웃박스)**: 발행 범위 = **생성 + 상태 전이 전건**(사람·자동·연쇄 공히 `automatic` 표식), 같은 트랜잭션, `record_birth`/`record_transition` 안에서만. `event_type`: `quotations.quotation.created|status_changed`, `proforma_invoices.proforma_invoice.created|status_changed`, `sales_orders.sales_order.created|status_changed`, `purchase_orders.purchase_order.created|status_changed`; `aggregate_type`=복수형 테이블명. 탄생은 `.created` 1건(status_changed 아님 — 이중 발행 회피). **전이별 개별 이벤트(confirmed·cancelled)는 만들지 않는다**(소비자 부재·선례가 단일 status_changed+payload·producer 측 추가는 가산). payload 허용 키 **화이트리스트**(단일 빌더 `_payload(doc)`에서 강제): `doc_type, doc_id, doc_number, from_status, to_status, automatic, assignee_id, partner_id` — **금액·단가·원가·마진·여신 값·사유 원문·메모 금지**(외부 채널로 나갈 수 있음).

마이그레이션 성격: 신규 테이블 4 + REVOKE(체크리스트 헤더 관용: rename 없음·CHECK 수기 여부·유니크·시드 없음·downgrade drop_table·REVOKE).

### (d) 4금
이벤트는 내부 alert_rules 라우팅 원료(규칙 0행이면 published 처리만) — 대외 발송 아님. 저촉 없음.

### (e) 상태·전이·불변
이력 4표는 INSERT/SELECT만(DB 권한). 모든 문서는 탄생 행 ≥1(불변식), 최신 `to_status`==문서 `status`, 연쇄 `from==직전 to`(정합 검증 함수 `verify_status_log(doc)` — 테스트·후속 야간 검산이 소비).

### (f) 테스트 배분
- **K**: 4표 `IMMUTABLE_TABLES` 등재·`table_policy` 분류 테스트 통과·`has_table_privilege('kbos_app', …, 'UPDATE'|'DELETE'|'TRUNCATE')` 전부 false·실제 UPDATE/DELETE/TRUNCATE 시도 → SQLSTATE 42501(선례 test_certification_constraints 관용, 4표 parametrize) / StatusLog 생성이 `transition.py` 밖 0(AST+자기검사) / payload 키 ⊆ 화이트리스트 & 금지 문자열(amount|price|cost|margin|credit|reason 원문) 0.
- **A**: 6 CHECK 각각 서비스 우회 INSERT 위반 23514(사유 누락·자기전이·타 문서 상태값·actor NULL & automatic=false·비탄생 행의 from NULL) / 탄생 행 2개 23505 / **이력 행 수 대사**: 4종×전 엣지 시나리오 후 이력 행 수 = 전이 수+생성 수, 자동 전이(스윕·입금 수렴·연쇄)도 1행+`automatic=true` / `verify_status_log`가 정상 체인 통과·슈퍼계정 변조 시 실패 보고 / 상태이력 조회 API 페이지네이션 기본 50.
- **J**: 이벤트 수 대사(생성 1+전이당 1)·커밋 실패/롤백 시 발행 0.
- **변이 점검 대상**: `record_transition`에서 이력 기록 제거·이벤트 발행 제거, `revoke_mutations` 호출 제거, payload에 금액 추가.

### (g) 소비·등재
C: `approval_id` FK 순서. S2-3 기일·알림: 이벤트 소비. 후속 문서(선적 등): 자기 `<doc>_status_log` 추가. 설계 보강 필요: **DESIGN §17.5 확장 부기 1문장**(이번 4표 등재)+ADR-0055.

### (h) 되돌리기 비용 — **중간**
다형 통합·컬럼 추가는 IMMUTABLE 테이블이라 새 마이그레이션+이관이 필요(데이터 쌓이기 전 지금이 최저가). CHECK 값 집합 확장은 후속 세션의 예상된 수기 재정의 1건. 이벤트 종류 분화는 가산이라 낮음.

---

## B7. 만료 처리 — 스윕 잡·즉시 수렴·재발행·임박 알림

### (a) 목적·경계
QT·PI의 `EXPIRED`(§7.2)를 저장 상태로 도달 가능하게 한다(계산값만이면 목록·보드·필터의 저장 상태가 거짓 — ADR-0039 기각 논리). 경계: 확정 SO·PO는 스윕 대상 아님.

### (b) DESIGN 근거
§7.2("QT 만료·PI 만료"), §15 스케줄 레지스트리(코드 고정 폐쇄 열거+CLI, 마이그레이션 시드 금지)·자동화 4금, §17.6(건별 독립 TX)·§17.2(행 잠금 후 재확인), ADR-0038·0039·0044·0046 ⑧(스윕의 건별 TX·실패 시 잡 FAILED).

### (c) 데이터 변경
- **테이블·컬럼 없음**(`valid_until`은 B2/A). `JOB_REGISTRY`에 8번째 행 `JobSpec(code='document-expiry-sweep', name_ko='견적·PI 유효기간 만료 수렴', schedule='daily@06:10', run=_run_document_expiry_sweep)` — certification-sweep(06:00) 뒤·deadline-scan(06:30) 앞. 함수 `docstatus`가 아닌 `trade_docs/expiry.py::sweep_expired_documents(base_date=None)->dict`(`{expired_qt, expired_pi, skipped, failed}`), 술어 `is_lapsed(doc_type, status, valid_until, today)`를 스윕과 생성 가드가 **공유**. CLI `python -m app.cli document-expiry-sweep --base-date`(certification-sweep 병렬 — 과거 재수렴·관통 실측). 등록은 앱 경로(`register-jobs`·worker bootstrap)만 — **마이그레이션 시드 금지**(`test_no_migration_seeds_the_app_owned_tables` 유지).
- 후보 산출: QT `status='ISSUED' AND valid_until < base_date AND deleted_at IS NULL`; PI `status='ISSUED' AND valid_until < base_date AND 살아 있는 후속 SO 없음`(PI에 SO가 만들어진 뒤에는 입금이 계속 들어와야 하므로 시스템이 닫지 않는다 — 인증 RENEWING 스윕 제외 선례). PARTIALLY_PAID·PAID PI는 상태로 이미 비대상. **유효기간 당일까지 유효**(`valid_until < 기준일`일 때만 만료; 기준일=`today_kst()`).
- 부분 인덱스(성능): `ix_<t>_expiry (valid_until) WHERE status='ISSUED' AND deleted_at IS NULL`(A 마이그레이션 동승 — 후보 수 소규모면 생략 가능, 구현자 판단 아님: **포함으로 확정**).

### (d) 4금 논증(ADR-0056 필수 문단)
이 잡이 만드는 전이는 정확히 **(QT,ISSUED→EXPIRED)·(PI,ISSUED→EXPIRED)** 두 엣지뿐이다. ① **발주 확정**: PO·SO를 만들거나 바꾸지 않는다(PO는 만료 개념·스윕 대상 없음). 만료는 "사용 불능 쪽" 이동이며 약정 진입이 아니다. ② **법적 판정**: 사람이 입력해 동결한 `valid_until`과 달력의 산술 비교일 뿐 법적·규제 판정이 아니다. ③ **대외 발송**: 바이어·외부 채널로 아무것도 보내지 않는다(아웃박스 이벤트는 내부 라우팅, 규칙 0행이면 published 처리만). ④ **장부 확정**: QT·PI는 원장·분개·채권을 만들지 않는 영업 문서(G8 용어 충돌 — "장부 확정=분개·마감·회계 전표·원장"으로 ADR에 해소)이며 만료는 CONTENT를 바꾸지 않는다. 사람 통제 유지: 만료는 종결이지만 문서는 삭제되지 않고 재발행 경로가 사람 손에 있다. §15 자동화 레벨상 "기록만"(L3 허용 범위). 돈이 걸린 상태(PARTIALLY_PAID·PAID)·살아 있는 후속이 있는 문서는 제외. `test_registered_jobs_stay_clear_of_the_four_bans` 집합에 코드 추가(+비저촉 서술 갱신).

### (e) 상태·전이·불변
- 건별 독립 `unit_of_work`: 후보 id 수집(TX1) → 건마다 새 TX: 행 `FOR UPDATE` → 상태·조건 재확인(사람 취소·입금·후속 생성과 직렬화 — 이미 바뀌었으면 skipped) → `record_transition(to=EXPIRED, automatic=True, actor=None, reason='자동: 유효기간 경과 (valid_until=YYYY-MM-DD, 기준일=YYYY-MM-DD)')` → 아웃박스(`status_changed`, `automatic=true`). 실패 건은 격리·로그(scrub)·카운트하고 나머지를 계속 처리하며 마지막에 `_fail_if_any_failed`로 잡 FAILED(§15 실패 알림 소비). 멱등(재실행 변화 0).
- **조건 A "즉시 수렴" 해석**: 유효기간은 동결 필드라 정정 트리거가 없어 시간 경과만이 원인이므로 스윕이 수렴 수단이다. **fail-closed 보강**: (1) 후속 생성 가드 — QT→PI·QT→SO·PI→SO 생성 시 원천 문서가 `is_lapsed`(QT: 상태 ∈ {ISSUED, CONVERTED}이고 `valid_until < today_kst()`; **PI: 상태=ISSUED일 때만** — PARTIALLY_PAID·PAID는 입금으로 수락이 이행됐으므로 유효기간 경과가 SO 생성을 막지 않는다[제안 3종 대비 정제])이면 스윕 실행 여부와 무관하게 422 `TRADE_DOCS.VALIDITY.EXPIRED`("유효기간이 지난 견적/PI로는 진행할 수 없습니다. 새 견적/PI를 발행해 주세요."), **상태를 쓰지 않고 거절**(롤백과 함께 소실되므로). (2) QT 복귀(CONVERTED→ISSUED)·PI 입금 역기록 복귀(→ISSUED) 시 이미 경과했으면 같은 TX에서 EXPIRED로 추가 수렴(이력 2행). (3) QT 발행·PI 생성 시 `valid_until ≥ today_kst()` 검증(이미 만료된 견적 발행 불가). API 응답에 파생 필드 `is_lapsed`(계산값, 저장 컬럼 아님)로 만료 배지.
- EXPIRED는 종결(재활성 엣지 없음). **재발행 경로**: 만료 QT → 새 QT를 `copied_from_id`=만료 QT로 생성(프리필, 재입력 금지, 원본 EXPIRED 허용, 새 valid_until 필수). 만료 PI → 원천 QT가 ISSUED/CONVERTED면 같은 QT에서 새 PI 생성(PI 생성 경로 그 자체, `copied_from_id`로 프리필 가능). 유효기간 연장 편집은 없다(CONTENT 동결).
- **임박(D-N) 알림은 만들지 않는다**: §5.2·§7.2 어디에도 QT/PI 만료 임박 알림 문면이 없고 기일 엔진(ADR-0046)의 소비자는 인증·문서 유효기간·선적 기일이다. 기일 엔진에 QT/PI를 등록하지 않는다. 만료 사건은 `status_changed(automatic=true)` 이벤트가 나가므로 관리자가 `alert_rules`(event_type 일치, 데이터)로 즉시 알림을 구성할 수 있다(코드 추가 0). 재판정 트리거 D-B2.

### (f) 테스트 배분
- **A/H(integration)**: 경계(`valid_until=기준일` 유지·기준일−1 EXPIRED — UTC/KST 날짜가 갈리는 시각[예: UTC 15:30]으로) / QT CONVERTED·PI 후속 SO 생존·PARTIALLY_PAID·PAID 비대상 / 재실행 변화 0(이력·이벤트 중복 0) / 건별 독립 TX: 3건 중 1건 실패 주입 → 2건 EXPIRED 커밋·`failed=1`·잡 FAILED / 스윕 vs 사람 취소·입금·후속 생성 동시 → 행 잠금 후 재확인으로 이중 전이 0·이력 정합 / 스윕 미실행 상태에서 만료 QT/PI 원천으로 후속 생성 → 422 & 상태 불변 / PARTIALLY_PAID PI로 SO 생성은 유효기간 경과여도 성공 / 복귀 즉시 수렴(QT·PI) 2행 / 만료 후 `copied_from_id` 재발행 성공 / 스윕 로그 `actor NULL`·`automatic=true`.
- **K**: 4금 테스트 집합에 `document-expiry-sweep` 추가 / 스윕 모듈이 발주·SO·numbering·notify·외부 호출을 import 0 / 스윕이 만드는 (doc_type,from,to)가 정확히 {(QT,ISSUED,EXPIRED),(PI,ISSUED,EXPIRED)}(SO·PO 행 무변) / 마이그레이션 시드 부재 유지 / JOB_REGISTRY 8종·`register-jobs` 멱등 / CLI 서브커맨드 등록.
- **변이 점검 대상**: 행 잠금 후 재확인 제거, `is_lapsed` 가드 제거, 후속 SO·입금 제외 술어 제거, 실패 시 잡 상태 OK화, 경계(`<`→`<=`).

### (g) 소비·등재
S3-2 기일 엔진 착수 시 QT/PI D-N 알림 편입 판정(D-B2). 설계 보강 필요: DESIGN §15 잡 매핑 행 추가+ADR-0056(4금 논증 포함), 기존 "7행" 단언 테스트(4금 테스트) 갱신.

### (h) 되돌리기 비용 — **낮음**
잡 1행·함수 1개라 폐기·시각 조정이 저렴하고 상태 표시 전용이라 데이터 손상 위험이 없다. **PI 후속 SO 제외 조건은 되돌리면 입금 교착이 재발하므로 유지 권장.** 임박 알림 추가는 가산.

---

## B8. 전이·생성 API 계약·잠금 순서·멱등·낙관 잠금

### (a) 목적·경계
B1~B7이 라우터마다 갈라지지 않도록 API·잠금 공통 계약을 고정한다. 경계: 역할 매트릭스·마스킹 응답 2종·소유권(IDOR)은 F·E 묶음.

### (b) DESIGN 근거
§17.1·§17.2·§17.4(쓰기 API 멱등 키)·§2 권한·ADR-0033/0038(전이 전용 엔드포인트 선례)·`handlers.py`(StaleDataError→409).

### (c) 데이터 변경
스키마 없음. 코드: `trade_docs/locking.py::lock_document(session, model, id, *, expected_version=None)`(FOR UPDATE + 존재·삭제 검증 + version 비교 단일 헬퍼), 공통 핸들러 확장(아래), 에러코드·카탈로그 등재(문구 ≥10자+조치 힌트어).

### (d) 4금
전이 엔드포인트는 사람 호출만. 자동 엣지는 공개 API에서 요청 불가. 저촉 없음.

### (e) 계약(전부 확정)
1. **엔드포인트**: 생성 `POST /{quotations|proforma-invoices|sales-orders|purchase-orders}`(SO는 인테이크 승격이 생성 — D) — **Idempotency-Key 필수**(결여 400, 동일 키 재수신=최초 결과). **동결 액션 전용**: `POST /quotations/{id}/issue`, `POST /sales-orders/{id}/confirm`(게이트·승인·포트 동반, 응답에 `allocation`·게이트 결과; **승인은 서버가 SO에 결속된 APPROVED 승인(`content_rev` 일치)을 조회해 소비 — 클라이언트가 approval_id를 넘기지 않는다**[C 가정]). 그 외 사람 전이 `POST /{docs}/{id}/transitions`(`TransitionRequest(extra='forbid')`: `to: Literal[문서별 공개 대상]`, `version: int`, `reason: str|None(≤500)`, PO의 SUPPLIER_CONFIRMED는 부속 `oc_received_on`·`oc_reference`) + Idempotency-Key. `to` Literal에서 **동결 엣지·자동 엣지·RESERVED 제외**(SO 재개용 `CONFIRMED`는 from=ON_HOLD일 때만 허용 — RECEIVED→CONFIRMED는 `/confirm` 전용). 편집 `PATCH /{docs}/{id}`(편집 가능 상태 전용 `…UpdateRequest`, version 필수)·`PATCH /{docs}/{id}/meta`(FREE 4개, 동결 후 허용) — 편집은 멱등 키 없이 version만. 목록은 전부 페이지네이션 기본 50. **DELETE 메서드 없음**(B9). 복제 = 생성 POST의 `copied_from_id`(별도 copy 엔드포인트 없음 — 프런트는 GET 상세로 프리필).
2. **권한**: QT·PI·SO·PO의 생성·전이·편집 = TRADE+ADMIN(서버 `require_roles`), 조회는 전 역할(VIEWER 포함)이되 원가·마진·여신·PO 금액은 응답 레벨 마스킹(E·F 묶음의 응답 스키마 2종 규약 — B는 이벤트·로그·에러 detail에 금액 미기재만 책임). 소유권: 역할 기준(개인 소유 필터 없음 — 협업 관용, F가 §20 K "타 사용자 전표 403"의 해석 확정).
3. **낙관 잠금**: 사용자 편집·전이는 행 `FOR UPDATE` 후 `expected_version` 비교(불일치 409). 시스템 수렴(스윕·입금 수렴·연쇄)은 행 잠금 후 조건 재확인만 하고 version 인자 없음 — 단 `VersionMixin`(`version_id_col`)이 ORM UPDATE마다 version을 올리므로 시스템 수렴도 version이 상승하고 편집 중이던 사용자의 stale 저장은 409로 걸러진다(정상·의도). 라인 변경은 헤더 version 증가(B2).
4. **잠금 순서**: 거래처 → 사슬 상위→하위(QT→PI→SO) → 라인(id 오름차순) → doc_number_seq(마지막). 위반은 코드 리뷰가 아니라 헬퍼(`lock_chain`·`lock_lines_for_consumption`)로 구조화.
5. **잠금 대기 초과·교착 → 409(재시도 안내), 500 금지**: 공통 예외 핸들러에 `sqlalchemy.exc.OperationalError` 중 SQLSTATE 55P03(lock_not_available)·40P01(deadlock_detected)을 `CONCURRENCY.LOCK.BUSY`(409, "다른 작업이 처리 중입니다. 잠시 후 다시 시도해 주세요.")로 매핑. 현재 핸들러에 이 매핑이 없음을 확인(500 노출). **이번 세션 코어 소규모 수정**(`core/errors`: codes+catalog+handlers).
6. **벌크(오더 보드)**: 건별 독립 트랜잭션+성공/거부 결과 표, 같은 `record_transition`/`confirm` 함수 재사용(벌크 전용 확정 함수 신설 금지 — D 소비).
7. **에러코드(신규, 3세그먼트·도메인 `TRADE_DOCS`)**: `TRANSITION.NOT_ALLOWED`(409)·`TRANSITION.REASON_REQUIRED`(422)·`RESUME.TARGET_MISMATCH`(409)·`DOCUMENT.FROZEN`(409)·`COPY.SOURCE_NOT_ELIGIBLE`(409)·`CANCEL.SUCCESSOR_ALIVE`(409)·`PARENT.NOT_USABLE`(409)·`VALIDITY.EXPIRED`(422)·`QUANTITY.EXCEEDS_OPEN`(409)·`QUANTITY.DOCUMENT_NOT_CONSUMABLE`(409)·`PAYMENT.PI_NOT_OPEN`(409) + `CONCURRENCY.LOCK.BUSY`(409). 커널 패키지명 `app/modules/trade_docs/`(A의 모듈 배치 전제 — 다르면 기계적 개명).

### (f) 테스트 배분
- **K**: 4 라우터 전 쓰기 엔드포인트 `require_roles` 존재 스캔·VIEWER/LOGISTICS/CERT 쓰기 403 / 목록 페이지네이션 기본 50 스캔 / `TransitionRequest` extra 거부·자동·동결·RESERVED `to` 거부 / 에러코드 3세그먼트·카탈로그 전수(기존 자동 테스트가 확장) / auth-coverage(401).
- **J**: 멱등 키 결여 400·더블클릭 1건·동일 키 다른 본문 처리(기존 idempotency 계약) / version 충돌 409 / **lock_timeout 유도(타 TX가 행 잠금 보유) → 409 BUSY(500 아님)**·데드락 유발(A→B/B→A 병렬) → 409 / 시스템 수렴이 편집 중 사용자 stale 저장을 409로 거름.
- **변이 점검 대상**: expected_version 비교 제거, 55P03/40P01 매핑 제거, `lock_chain` 순서 뒤집기.

### (g) 소비·등재
D(벌크·인테이크 생성)·C(confirm)·프런트(전이 UI). 설계 보강: 없음(§17 계약 적용). 부채 없음.

### (h) 되돌리기 비용 — **낮음**
엔드포인트 형태는 프런트와 세트라 초기 변경은 저렴. 핸들러 매핑은 코어 1곳.

---

## B9. 전표 삭제 금지·soft delete 규칙·승인/게이트/불변 우회 경로 차단표

### (a) 목적·경계
결번·번호 재사용·고아 이력 논쟁을 원천 봉쇄하고, "한 곳만 지키는" 우회 결함을 계획 단계에서 전 경로 표로 닫는다.

### (b) DESIGN 근거
§17.3(취소 번호 재사용 금지)·ADR-05·§15 4금·§20 H("승인 우회 차단·승인 후 불변")·§17.1(자동 확정 경로 부재의 선례 ADR-0033 ③·ADR-0030 ⑤).

### (c) 데이터 변경
스키마 없음. 4 헤더의 `deleted_at`(표준 믹스인)은 유지하되 **쓰는 서비스 경로가 없다**. 라인 테이블은 편집 가능 상태(QT DRAFT·SO RECEIVED)에서만 `replace_lines`가 교체·삭제하며 동결 후 삭제·재생성 금지(A가 라인 soft delete 여부 결정 — 동결 이후 불변은 B).

### (d) 4금
아래 차단표가 4금(발주 확정·장부 확정 등)의 자동 진입을 막는 표 자체다.

### (e) 규칙
**삭제 금지**: 4종에 DELETE 엔드포인트·soft delete 쓰기 경로 없음. 폐기의 유일한 방법 = CANCELLED 전이(사유 필수)이며 행과 번호는 남는다(B4 결번 0·B3 번호 재사용 0·B6 고아 로그 0의 전제). 헤더 대상 `deleted_at\s*=`·`soft_delete(`가 4 전표 서비스에 0건.
**우회 경로 차단표**

| # | 우회 경로 | 차단 |
|---|---|---|
| 1 | 상태 PATCH | 스키마에 status 부재+`extra=forbid` → 422 |
| 2 | 벌크 액션 | 건별 독립 TX가 `record_transition`/`confirm`을 그대로 호출(벌크 전용 확정 함수 금지) |
| 3 | 인테이크·임포트 확정 | 스테이징 승격은 SO를 **RECEIVED로만** 생성(`record_birth`); 승격 코드에서 `confirm`·`record_transition(CONFIRMED)` 호출 0(스캔) |
| 4 | CLI | 전표 상태를 바꾸는 CLI는 만료 스윕 1개뿐 |
| 5 | ADMIN 역할 | 게이트 판정은 역할과 독립(승인 요구 SO는 ADMIN도 승인 기록 없이 확정 불가 — C와 합의) |
| 6 | soft delete로 후속 존재 은닉 | 삭제 경로 없음+LIVE 술어에 `deleted_at` 포함 |
| 7 | 라인 직접 쓰기 | `replace_lines`/`assert_editable` 밖 라인 모델 쓰기 0(스캔) |
| 8 | 이벤트·재시도 이중 실행 | Idempotency-Key + 행 잠금 후 상태 재확인 |
| 9 | 평가 불능(UNKNOWN·미평가)을 통과로 취급 | `confirm`은 게이트 결과가 PASS/APPROVED가 아니면 거부(UNKNOWN 포함) — **확정 시점에 잠금 하 재평가**(스테이징 시점 결과를 신뢰하지 않음 — TOCTOU) |
| 10 | 자동 발주 확정 | PO 생성 호출자 = 라우터 1곳+`actor` 필수, `scheduler`·`imports`·`intake` import 0 |

모델 쓰기 공개 함수 화이트리스트(`create_*`·`update_document`·`replace_lines`·`update_meta`·`issue`·`confirm`·`cancel`·`record_*`·`converge_*`·`sweep_expired_documents`) 외 문서 모델 쓰기 함수 부재를 아키텍처 테스트가 고정.

### (f) 테스트 배분
- **K**: 4 라우터 HTTP 메서드 집합에 DELETE 부재 / `deleted_at`·soft delete 대입 스캔 0(+자기검사) / 위 표 3·7·10번 스캔 / 공개 함수 화이트리스트.
- **H(승인 우회 차단)**: 상태 PATCH·벌크·인테이크 커밋·CLI 각 경로로 승인 필요 SO 확정 시도 → 전부 거부 / ADMIN이 승인 없이 확정 시도 거부 / 게이트 UNKNOWN(설정·매핑 부재)에서 확정 거부 / 벌크에서 일부 거부 시 나머지 정상 커밋·결과 표 정확.
- **A**: 초안 QT 폐기 흐름 = CANCELLED+사유+이력, 번호 연속성 검사(결번 0).
- **변이 점검 대상**: 확정 함수의 게이트 재평가 제거, 벌크가 전이 함수를 우회, 승격 코드의 확정 호출 추가.

### (g) 소비·등재
C·D(승인·게이트 결합 테스트), F(GC/H 매핑 보강 — §20 헤더 매핑은 H를 P2·P6에만 배정: P3 H 항목 매핑 보강 문장 필요). 부채 없음.

### (h) 되돌리기 비용 — **낮음**
삭제 API는 필요 시 추가하면 되나 결번·번호 재사용 정책과 충돌하므로 비권장. 테스트·스캔 추가는 가산.

---

## 부록 A. 타 묶음 의존(전제 가정) — 통합 검토가 정합을 맞춘다

| 묶음 | B가 전제한 것 | 어긋날 때 영향 |
|---|---|---|
| A 데이터모델 | ① 커널 패키지 `app/modules/trade_docs/`(models·machine·policy·transition·chain·quantities·locking·expiry)와 4 전표 모듈 분리 ② 4 헤더의 컬럼(§B2 (c) 목록 전부)·FIELD_POLICY 전수 분류표 작성 의무 ③ 환율 스냅샷 컬럼명·기준일·`price_at` 스냅샷 시점(라인 추가 시 vs 확정 시 — B2 테스트 (3)(4)가 컬럼명·시점에 의존) ④ SO 참조: `qt_id`·`pi_id` nullable(직접 인테이크 SO는 둘 다 NULL 가능) ⑤ 참조 카디널리티 "동일 QT/PI에 **살아 있는** SO는 최대 1건(LIVE 술어 부분 유니크)" 권장(B3 검사는 카디널리티와 무관하게 동작) ⑥ PI `advance_due_amount`(선수금 청구액) 컬럼 ⑦ 라인합=헤더합 저장 시점 검증 | 컬럼명 변경은 기계적. ③ 시점이 "확정 시"면 B2 (4)가 확정 액션 422로 옮겨진다 |
| C 승인 | ① `approvals`가 `sales_order_status_log` 마이그레이션보다 **앞 리비전** ② `withdraw_open_for_subject(session, subject_type, subject_id, actor_user_id)` ③ 승인은 `(SO id, content_rev)`에 결속·소비 후 종결·내용 변경 시 무효 ④ 승인 결정 후 SO 확정은 **사람 1클릭**(승인 완료가 확정을 유발하지 않음) ⑤ confirm이 서버 조회로 승인 소비 | ①이 뒤집히면 log.approval_id FK 순서 조정. ③이 `version` 결속이면 FREE 편집도 승인을 무효화(사용성 저하) — 권장 `content_rev` |
| D 인테이크·보드 | ① 스테이징 승격=SO 행 생성(RECEIVED)+채번(TX 마지막)+`record_birth`(SO는 스테이징 단계 번호 없음) ② 확정은 `confirm` 단일 함수·게이트 UNKNOWN 거부·잠금 하 재평가 ③ 벌크=건별 TX 재사용 ④ SO copy(정정 재접수)=인테이크 초안+`copied_from_id` | ①: §7.2 "접수(스테이징)"를 "스테이징 확정=SO(접수) 생성"의 2단으로 해석(r1 C1 권장) |
| E 여신·입금·정책 | ① 여신 노출 산식은 LIVE(취소·만료 제외) 파생, RECEIVED·ON_HOLD 포함 여부는 E 판정 ② PI 누적 입금 기록 수단(S3-1 최소 수단 vs S3-3 당김)과 무관하게 `converge_payment_status`가 단일 진입점 ③ 바이어 PO 중복 유니크 술어 `(partner_id, 정규화 buyer_po_no) WHERE deleted_at IS NULL AND status<>'CANCELLED'` ④ 응답 마스킹 2종에서 이벤트·로그·에러 detail 금액 금지 준수 | ② 입금 원천이 픽스처면 A·H 테스트는 픽스처로 입금액 세팅 |
| F PO·역할·이월 | ① 전이별 역할(TRADE+ADMIN) ② PO 금액 VIEWER 마스킹 ③ 4 헤더 `assignee_id` handover targets 등록(아키텍처 테스트 강제)·인계 UPDATE를 동결 스캔 예외 처리(assignee_id 한정) ④ WBS v1.5: PO 입고 후반·수입선적 연결 소유 명기(권장 S4-1) ⑤ ADR 세트·DESIGN 보강·GC v1.4 | |
| S3-2·S3-3·S4 | 위 (g) 항 참조 | |

## 부록 B. 신규·변경 산출 목록(요약)

**신규 테이블(B 소유)**: `quotation_status_log`, `proforma_invoice_status_log`, `sales_order_status_log`, `purchase_order_status_log`(IMMUTABLE+REVOKE).
**A 소유 테이블에 대한 B 요구 컬럼**: 4 헤더 `doc_number`(+UNIQUE+형식 CHECK)·`status`(+CHECK)·`internal_note`·`assignee_id`·`copied_from_id`; QT/PI `valid_until`(+QT CHECK); SO `confirmed_at`·`content_rev`(+CHECK); PO `oc_received_on`·`oc_reference`(+CHECK); 라인 `ordered_quantity>0` CHECK; 만료 후보 부분 인덱스.
**기존 코드 변경**: `numbering/service.py` 1줄(KST 연도)·`table_policy.IMMUTABLE_TABLES` 4행·`scheduler.JOB_REGISTRY` 1행+`app/cli.py` 서브커맨드+4금 테스트 집합·`core/errors`(LOCK.BUSY 핸들러+`TRADE_DOCS.*` 코드·카탈로그).
**신규 코드 모듈**: `trade_docs/`(machine·policy·transition·chain·quantities·locking·expiry), `sales_orders/ports.py`.
**아키텍처 테스트 신규**: `test_doc_machines.py`, `test_doc_field_policy.py`, `test_doc_chain_contract.py`, `test_doc_status_log_contract.py`(+ 기존 `test_scheduler_registry.py`·`test_numbering.py` 확장).

## 부록 C. 부채·관찰 등재

| ID | 내용 | 트리거/소유 |
|---|---|---|
| D-B1 | **PO 입고 후반(입고 문서·PO 잔량 차감·PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED 전이·수입선적 연결) 소유 세션이 WBS에 없다** — 값은 CHECK에 있고 엣지 0(RESERVED). WBS v1.5 갱신 요청, 소유 권장 S4-1(입고 확정=IN_PO 원장 기록과 같은 TX여야 하므로 — §7.1·§17.1) | WBS 갱신(F) |
| D-B2 | QT/PI 만료 임박(D-N) 알림 미구현 | S3-2 기일 엔진 착수 또는 만료로 놓친 견적 사고 1건 |
| D-B3 | EXPIRED·CANCELLED PI에 입금이 도착하는 처리(현재: 입금 기록 거부 409 → 신규 PI 발행 후 기록) | S3-3 payments 계획·실사용 발생 |
| D-B4 | **SO 부분출하 후 잔량 종결(short-close) 경로 부재** — IN_SHIPMENT 후 남은 수량이 있으면 취소(선적 역순)도 완료도 안 되는 잔존 상태 가능. PO는 PARTIALLY_RECEIVED→CLOSED(사유)가 설계 가능하나 SO엔 없음 | S3-2 계획에서 판정(후보: 사유+미출하 수량 종결 엣지) |
| D-B5 | 정정 전표(승인 동반 개정본) 미채택 — 취소+신규가 실사용에서 과도한 마찰(납기 변경 빈도 압도)로 확인되면 재판정 | 실사용 리허설 |
| D-B6 | 동결 다이제스트·야간 전건 재해시 미채택 / 라인합=헤더합 야간 검산 이중망 | S6-3 야간 검산 설계 시 편입 판정, 또는 동결 컬럼 우회 쓰기 실사고 1건 |
| D-B7 | 잔량 표현(SUM 파생) 최종 결정 | S4-1 착수 ADR이 대체 가능(시그니처 유지 계약) |
| D-B8 | 코어 예외 핸들러 LOCK.BUSY 보강이 타 모듈에도 적용됨(부수 효과 — 500→409) | PR-1 회귀 확인 |

## 부록 D. 설계·WBS와의 충돌·보강 필요 목록

1. **DESIGN §7.2 보강 필요** — 코드값 열거·전이표(25/101)·PI/PO 초안 없음·QT/PI 자동 엣지·예약 상태를 [M4] 보강 문단으로 명문화(ADR-0038 서식) + ADR-0051.
2. **DESIGN §17.3 부기 필요** — 채번 발급 시점=행 최초 저장·연도=발급 시각 KST·전표 doc_number 전역 UNIQUE(§17.4 부분 인덱스 규칙과의 관계 해석) + §3 채번 문면 + ADR-0053.
3. **DESIGN §17.5 확장 부기 1문장** — 상태이력 4표 IMMUTABLE 등재 + ADR-0055. **GC-A4 해석 명기**(S4-1 문면, S3-1은 ADR-05 취소 세칙 유추 적용) + ADR-0052.
4. **DESIGN §8.3 부기** — 잔량 SUM 파생·헤더 SHARE+라인 FOR UPDATE는 S3-1 로컬 결정, S4-1 ADR이 시그니처 유지 하에 대체 가능 + ADR-0054.
5. **DESIGN §15 스케줄 레지스트리 행 추가**(document-expiry-sweep, 4금 논증) + ADR-0056.
6. **WBS 충돌/공백**: PO 입고 후반 소유 세션 부재(D-B1)→WBS v1.5 갱신; WBS S3-1 "PI 입금 게이트" ↔ S3-3 payments(E가 해소, B는 `converge_payment_status` 단일 진입점만 제공); §20 헤더의 H 매핑 P3 보강(F).
7. **ADR-0041 정합**: "죽은 열거 금지"는 DESIGN 근거·소비 세션 없는 값이 대상이며, SO 4·PO 3 값은 §7.2·WBS 근거로 CHECK에 싣고 엣지는 RESERVED — 이 해석을 ADR-0051에 한 문장으로 명기.
8. ADR 번호는 제안(0051 전이표·상태 통로 / 0052 동결·불변·정정·취소 세칙(GC-A4 해석 포함) / 0053 채번 / 0054 잔량·할당 포트 / 0055 상태이력 4표 / 0056 만료 스윕) — 최종 배정은 통합 검토(타 묶음 ADR과 합산).

---

## 자율 확정 판정표

| 번호 | 결정 요지 | 근거 한 줄 | 되돌리기 비용 |
|---|---|---|---|
| B1 | 상태 QT5·PI5·SO8·PO6(PI·PO 초안 없음), 허용 25(사람15·자동10)/미허용 101, SO·PO 예약 상태 7값은 CHECK만·엣지 0, `record_birth`/`record_transition` 단일 통로, QT CONVERTED⇔살아 있는 후속 양방향 파생, SO 재개 목표=`confirmed_at`, PI 입금 6방향 수렴=`converge_payment_status` 단일 진입점 | §7.2 문면 그대로+ADR-0038 서식·비대칭 결손 금지 | 중간 |
| B2 | 동결 시점 QT 발행·PI/PO 생성·SO 확정, 열 분류 CONTENT/ORIGIN/FREE(4개)/SYSTEM 전수 등재(미등재 CI 실패), 변경 경로=취소+신규(`copied_from_id`, 두 독립 동작), SO `confirmed_at`·`content_rev`, 트리거·다이제스트 미채택 | DoD "확정 후 단가·환율 불변"을 기본 동결 fail-closed로, 트리거는 선례상 기각 | 낮음(분류)~높음(정정 전표 전환) |
| B3 | LIVE=`deleted_at IS NULL AND status NOT IN (CANCELLED,EXPIRED)`, 취소=전이(이력이 유일 원천), `CHILD_LINKS`+FK 스캔 테스트, 잠금 순서 거래처→QT→PI→SO→라인→채번, SO 취소 훅 순서 고정(승인 철회·할당 포트·부모 수렴) | ADR-05 역순 취소·§20 A 잔액 복원 | 낮음~중간 |
| B4 | 접두어 QT/PI/SO/PO, 채번은 행 최초 저장 시(TX 마지막)·폐기=CANCELLED·삭제 경로 0, 연도=발급 시각 KST(1줄), 전표 doc_number 전역 UNIQUE+형식 CHECK | §17.3 취소 번호 재사용 금지·§2 KST 표시·결번 0 | 낮음(첫 실번호 전)/이후 사실상 비가역 |
| B5 | 잔량=하위 라인 SUM 파생(소비자 레지스트리 0건), `lock_lines_for_consumption`(헤더 SHARE→라인 FOR UPDATE id순)·`open_quantity` 계약, 할당/백오더 분기=동기 `AllocationPort`(P3 NOT_IMPLEMENTED를 confirm 응답에 노출) | §17.1·§17.2 원자성, §8.3 P4 ADR 선점 회피 | 낮음 |
| B6 | 문서별 상태이력 4표(IMMUTABLE+REVOKE, 탄생 행·사유·automatic·SO만 approval_id), 이벤트=생성+전이 전건(단일 status_changed, payload 화이트리스트·금액 금지), audit_log 이중 기록 없음 | §17.5 확장 의무·certification 원형 | 중간 |
| B7 | `document-expiry-sweep`(daily@06:10, QT/PI ISSUED 전용·PI 후속 SO 제외·건별 TX·실패 시 FAILED), 생성 가드 422(PI는 ISSUED만 — PARTIALLY_PAID·PAID 면제), 복귀 즉시 수렴, 재발행=`copied_from_id`, 임박 알림 없음 | §7.2 EXPIRED 저장 상태+§15 폐쇄 레지스트리·4금 논증 | 낮음 |
| B8 | 동결 액션 전용(issue·confirm)+범용 `/transitions`, 생성·전이 Idempotency-Key, 잠금 순서 규약, 시스템 수렴도 version 상승, 55P03/40P01→409 `CONCURRENCY.LOCK.BUSY`, 에러코드 `TRADE_DOCS.*` 12종 | §17.1~4·핸들러 실측(500 노출) | 낮음 |
| B9 | 4종 삭제 경로 0·폐기=CANCELLED, 우회 경로 10건 차단표(벌크·인테이크·ADMIN·CLI·UNKNOWN 재평가·자동 발주) | 결번·번호 재사용·승인 우회 전 경로 봉쇄 | 낮음 |
