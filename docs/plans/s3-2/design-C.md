# S3-2 계획 설계 — 부록 C: 동시성·권한·감사·잡

- 기준: main `a4d91c0`(S3-1 종결). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-2 행(W:112-116)을 따른다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `code:경로:줄` = `backend/app/` 아래 경로, `test:경로:줄` = `backend/tests/` 아래 경로. 줄 번호는 `a4d91c0`에서 읽은 값이다.
- 판정 방식: 오너 지시(2026-09-29, CLAUDE.md "웹 세션 판정 절차 생략")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다. PROGRESS 등재 시 "자율 확정"으로 표기한다.
- 형식: 안건 C1~C14. 각 안건은 **결정 / 근거 / 대안 / 자율 확정 / 되돌리기 비용** 순이다.
- **실행 검증 못 했음.** 정적 독해만 했다. pytest·alembic·DB·서버는 돌리지 않았다. "깨질 테스트"는 소스 단언문을 읽고 추론한 것이다.

---

## 0. 범위와 다른 부록과의 경계

### 0-1. 이 부록이 확정하는 것
- 선적 생성·수정·취소·전이, 마일스톤 계획·실적·롤오버, 통관 기록, 휴일 등록의 **트랜잭션 경계**(C1)
- **LOCK_ORDER** 삽입 위치와 잠금 모드, SHARE→UPDATE 승격 금지(C2·C3)
- 낙관 잠금(C4)과 **멱등 키**(API 키 + DB 부분 유니크)(C5)
- **역할·소유권 매트릭스**(A·T·L·C·V)와 역할 변경 화면 배정(C6·C7)
- **마스킹**(원가 9채널 봉쇄 승계)(C8)
- **감사** 기록 위치의 분담(C9)
- **아웃박스 이벤트** 목록과 payload 규약(C10)
- **스케줄 잡** 시각·총수·4금 논증·호출 경계(C11)
- **자동화 L3 금지** 확인과 자동 확정 부재 레지스트리 확장(C12)
- 에러 코드 번역 규약(C13), **H·J(+I·K 일부) 테스트 설계**(C14)

### 0-2. 경계만 적고 넘기는 것

| 주제 | 소관 | 이 부록이 가정하는 인터페이스 |
|---|---|---|
| 선적 헤더·라인·당사자 열, 상태 머신 코드값·전이표, DocKind 편입, 채번 접두어, `CHILD_LINKS`·`LINE_CONSUMERS` 등록 형태, SO RESERVED 엣지 범위 | 선적 모델·상태 부록 | DocKind `SHIPMENT` 편입(커널의 `record_transition`·`record_birth` 단일 통로 승계). 헤더는 `so_id`(수출)·`po_id`(수입) 중 정확히 하나. **S3-2 활성 SO 엣지는 CONFIRMED↔IN_SHIPMENT 자동 수렴 2개뿐, COMPLETED는 RESERVED 유지**(AMB-15, P-01 — 노출 공백 방지). 선적의 피킹·검수완료·출고·선적·종결은 RESERVED(AMB-01). `assignee_id` 보유(FREE 열) |
| 마일스톤 저장 모델·계산 순수 함수·휴일 의미론·스캔 대상·문턱·dedup 키·QT/PI D-N 후보 정의 | 부록 B(`docs/plans/s3-2/design-B.md`) | B9(`milestone_changes`·`milestone_change_notices` IMMUTABLE), B13(`trade-deadline-scan` daily@06:40), B18(QT/PI D-N 같은 잡). 이 부록은 B13·B18의 **잡 배정·호출 경계·실패 처리**만 확정하고 대상·문턱은 다시 정하지 않는다 |
| `lc_terms`·L/C 플래그 공급·tolerance 표시·미수 provider·`open_order_amount` 차감 | S3-3 | 이 부록은 "열지 않는다"만 확인한다(C12) |
| 피킹·검수·OUT_SHIP 원장·할당 | S4-1·S4-2 | stock_movements 무접촉(C12) |

---

## C1. 트랜잭션 경계 — 업무 동작 = 1 DB 트랜잭션

**결정**

아래 표의 각 행이 정확히 1개의 DB 트랜잭션이다. 트랜잭션 시작·종료는 서비스 레이어만 한다(`unit_of_work()` 합류 규약 — 바깥 UoW에 합류하므로 중첩 호출이 따로 커밋하지 않는다). **트랜잭션 안에서 외부 호출(HTTP·메일·슬랙·AI)은 0**이며, 알림은 같은 트랜잭션의 outbox 기록 또는 스캔 잡의 알림 코어 직접 호출(건별 TX)로만 나간다.

| # | 업무 동작 (엔드포인트 가칭) | 잠금(LOCK_ORDER 순, C2) | 쓰기 | 이력·감사 | outbox |
|---|---|---|---|---|---|
| T1 | 수출 선적 참조 생성 `POST /shipments`(SO 참조) | 멱등 claim → partners `KEY SHARE`(당사자 유형 검증, ADR-0067) → SO `FOR UPDATE`(lock_chain) → SO 라인 `FOR UPDATE` id순(`lock_lines_for_consumption`, FOR SHARE는 기보유 FOR UPDATE에 흡수 — C3) → `doc_number_seq` | shipments·lines·parties INSERT, 마일스톤 슬롯 INSERT(부록 B), **첫 살아 있는 선적이면 SO CONFIRMED→IN_SHIPMENT 자동 수렴** | `record_birth`(선적 상태이력 1행), SO 상태이력 1행(automatic=True) | `shipments.shipment.created` + 커널 SO 전이 이벤트 |
| T2 | 수입 선적 참조 생성(PO 참조) | 멱등 → partners `KEY SHARE` → PO 헤더 `FOR SHARE`(상태 검증) → PO 라인 `FOR UPDATE` id순 → `doc_number_seq` | 위와 같음. **PO 상태·PO 잔량 무변경**(S32-IM-03), 단가 복사 0(C8) | `record_birth` | `shipments.shipment.created` |
| T3 | 선적 헤더 수정(FREE·당사자·국가 등 편집 가능 열) | 멱등 → (당사자 변경 시) partners `KEY SHARE` → shipments `FOR UPDATE` + version 대조 | UPDATE, version+1 | (FIELD_POLICY상 CONTENT 열 변경은 409 — 모델 부록) | 없음(내부 편집) |
| T4 | 선적 라인 수정·삭제(계획 상태 한정) | 멱등 → SO(또는 PO) `FOR UPDATE`/`FOR SHARE` → shipments `FOR UPDATE` + 헤더 version 대조 → 원천 라인 `FOR UPDATE` id순 | 라인 UPDATE/soft delete, **헤더 version+1**(D:344 ④) | — | 없음 |
| T5 | 선적 사람 전이(계획→출고지시, 계획/출고지시→취소) `POST /shipments/{id}/transitions` | 멱등 → SO(또는 PO) `FOR UPDATE`(취소 시 수렴이 필요할 수 있으므로 항상) → shipments `FOR UPDATE` + version | `record_transition`. **취소로 살아 있는 선적이 0이 되면 SO IN_SHIPMENT→CONFIRMED 자동 수렴** | 선적·SO 상태이력, 취소 사유 필수 | 커널 전이 이벤트 |
| T6 | 마일스톤 계획 설정·변경(롤오버) | 멱등 → shipments `FOR UPDATE`(version 대조 없음 — 헤더 비수정) → milestones 행 `FOR UPDATE` + 행 version 대조 | milestones UPDATE | `milestone_changes` INSERT(사유 필수 종류는 CHECK — 부록 B9) | `shipments.milestone.changed` |
| T7 | 마일스톤 실적 입력·정정 | T6과 같음 | milestones UPDATE. **파생값은 저장하지 않으므로 "후속 재계산"은 같은 TX에서 쓰기 0**(부록 B 계산값 원칙) | `milestone_changes` INSERT | `shipments.milestone.changed` |
| T8 | 통보 기록 연결(comm_log 생성 + 롤오버 행 연결) | 멱등 → shipments `FOR SHARE`(존재·소속 확인) | comm_logs INSERT(SHIPMENT 주제), `milestone_change_notices` INSERT | 둘 다 기록 자체가 이력 | 없음(발송 0) |
| T9 | 통관 기록 입력·정정 `…/customs-records` | 멱등 → shipments `FOR UPDATE` → customs_records 행 `FOR UPDATE` + version | INSERT/UPDATE | 정정 시 `audit_log` 1행(C9) | `shipments.customs.recorded` |
| T10 | 휴일 연도 등록·교체(ADMIN) | 멱등 → (해당 국가·연도 선언 행) `FOR UPDATE` | 연도 단위 교체(삭제 표시 + 신규 INSERT) | `audit_log` 1행(C9) | 없음 |
| T11 | 담당 일괄 이관(기존 handover) | ASSIGNMENT_TARGETS 순서 = LOCK_ORDER(C2) | shipments.assignee_id UPDATE(version 불변 — 기존 규약) | 기존 handover audit | 기존 |
| T12 | 기일 스캔 잡(건별) | 잠금 없음(읽기) | alerts INSERT(`notify` 코어) | — | 없음(코어 직접, D:144) |

- 선적 생성(T1·T2)에서 **채번은 트랜잭션 마지막**이다(`code:modules/trade_docs/doc_number.py:4-5`). 중간 실패 시 번호도 함께 롤백된다(번호 구멍은 허용, 재사용 금지 — D:346).
- T1의 SO 자동 수렴은 **별도 트랜잭션이나 잡이 아니라 같은 트랜잭션**이다. 선적만 생기고 SO가 CONFIRMED로 남는 중간 상태는 관측될 수 없다(QT CONVERTED 수렴 선례 `code:modules/trade_chain/chain_ops.py:68-116`).
- 선적 취소(T5)와 선적 라인 삭제(T4)는 **같은 수렴 함수**를 부른다(수렴 판정 = "살아 있는 선적 라인을 가진 선적이 있는가", 단일 정의). 정의가 둘로 갈리면 거짓 상태가 남는다(비대칭 결손 선례 D:140 ②).

**근거**: D:340(17.1 "헤더+라인+원장+이력+이벤트 전부 커밋 or 전부 롤백", "트랜잭션 안에서 외부 호출 금지"), D:362(디스패처는 커밋 후), D:144(기일 스캔은 코어 직접), D:229 ②(소비 잠금), D:346(채번).

**대안**
- (a) SO 수렴을 커밋 후 이벤트 소비자(아웃박스 핸들러)가 한다. **기각**: 선적·SO 상태가 일시 불일치하고, 소비자 실패 시 영구 불일치가 남는다. 전이가 상태 변경인데 이벤트 경로로 상태를 바꾸면 "아웃박스=통지" 계약이 흐려진다.
- (b) 실적 입력 시 파생 마일스톤을 같은 TX에 저장. **기각**: 부록 B가 계산값으로 확정했다(저장하면 재계산 쓰기와 이력 폭증).

**자율 확정**: 확정.

**되돌리기 비용**: 낮음. 경계를 쪼개는 것은 서비스 함수 분리뿐이다. 단 SO 수렴을 TX 밖으로 빼는 쪽으로 바꾸면 불일치 정리 잡이 필요해져 중간.

---

## C2. LOCK_ORDER 삽입 위치와 잠금 모드

**결정**

`code:modules/trade_docs/locking.py:32-43` `LOCK_ORDER`를 다음으로 개정한다(굵게 = 신규).

```
idempotency_keys → order_intakes → partners → quotations → proforma_invoices
→ sales_orders → purchase_orders → **shipments** → **shipment_children** → approvals
→ lines → doc_number_seq
```

- `shipments`: 선적 헤더. SO·PO의 후속이므로 조상 → 자기 순서(`lock_chain` 관용, `code:modules/trade_chain/chain_ops.py:47-65`). `ANCESTORS[SHIPMENT] = ((SALES_ORDER,"so_id"), (PURCHASE_ORDER,"po_id"))` — None 조상은 건너뛰는 현 로직(`chain_ops.py:58-63`)에 기댄다. 선적이 SO의 조상(QT·PI)까지는 잠그지 않는다(선적은 SO만 수렴시킨다).
- `shipment_children`: 선적 1건에 딸린 **비-라인 하위 행**(milestones·customs_records·shipment_parties). 헤더를 잠근 뒤에만 잡는다. 여러 행이면 id 오름차순.
- 선적 라인과 원천(SO·PO) 라인은 기존 `lines` 범주(id 오름차순)에 들어간다. 원천 라인과 선적 라인을 한 TX에서 같이 잠글 때는 **원천 라인 → 선적 라인** 순서(조상→자기)로 한다.
- `approvals` 위치: 선적은 승인을 만들거나 소비하지 않는다(S3-2). 위치만 순서표 일관성을 위해 shipments 뒤에 둔다.
- `holidays`·`holiday_calendar_years`는 **순서표에 넣지 않는다**: 업무 TX(T1~T9)는 휴일을 잠그지 않고 읽기만 한다(MVCC 스냅샷). 휴일 쓰기(T10)는 전표를 잠그지 않는 독립 TX다 — 두 집합이 교차하지 않으므로 사이클이 없다.
- `comm_logs`(T8): 선적 `FOR SHARE` 뒤 INSERT만 한다(잠금 대상 아님).
- 잠금 모드 요약

| 대상 | 모드 | 사용처 |
|---|---|---|
| partners | `FOR KEY SHARE` | 당사자(포워더·관세사·3PL·바이어) 유형·활성 검증(`require_partner_of_any_type(lock=True)`, ADR-0067). **NO KEY UPDATE 금지** — 여신 잠금은 `credit/locking.py` 전용(`test:architecture/test_approval_contract.py:585-601`) |
| sales_orders | `FOR UPDATE` | T1·T4·T5 — 수렴 가능성이 있는 모든 선적 쓰기 |
| purchase_orders | `FOR SHARE` | T2·T4(수입) — PO 상태 불변, 상태 검증용 |
| shipments | `FOR UPDATE`(쓰기) / `FOR SHARE`(T8 소속 확인) | |
| 원천 라인 | `FOR UPDATE` id순 | 잔량·배정 가능량 직렬화 |

- **담당 이관 순서**: `code:modules/handover/targets.py` `ASSIGNMENT_TARGETS`는 "순서가 곧 잠금 순서"다(같은 파일 57줄 주석). `shipments`를 `purchase_orders` **뒤**에 추가한다(`test:e2e/test_order_intakes_review.py:117-125`가 이관 순서 = LOCK_ORDER를 검사).
- `locking.py:1-28` 독스트링과 DESIGN §17.2 부기 ②(D:344)의 순서표를 같은 PR에서 고친다. **ADR 필수**(D:344 ② "변경은 ADR").

**근거**: D:344 ②(순서표·부분수열 허용·위반 금지·거래처 잠금 모드), D:229 ②, `code:modules/trade_docs/locking.py:32-43`.

**대안**
- (a) shipments를 `lines` 뒤에 둔다. **기각**: 원천 라인 잠금 후 선적 헤더를 잡게 되어, 선적 헤더 → SO 라인 순서로 가는 편집 경로(T4)와 사이클이 생긴다.
- (b) milestones를 `lines` 범주에 섞는다. **기각**: 마일스톤은 라인이 아니며, 라인 범주(id순)는 원천↔선적 라인 직렬화용이라 의미가 섞인다.

**자율 확정**: 확정. 기존 계측 테스트(`test_quotation_concurrency.py:476-477` 등 7종 — code-chain 읽기 §1.6)는 상대 순서만 단언하므로 튜플 중간 삽입으로 깨지지 않는다(추론). **선적 경로 계측 테스트는 신설**(C14 J-07).

**되돌리기 비용**: 중간. 순서를 바꾸면 교착 위험을 전 경로에서 재검증해야 한다(계측 테스트가 그 비용을 줄인다).

---

## C3. 소비 잠금과 SO 수렴 — SHARE→UPDATE 승격 금지

**결정**
- 소비 시그니처는 그대로 둔다: `open_quantity`·`lock_lines_for_consumption`(D:229 ②가 "하나"로 고정). 새 잠금 헬퍼를 만들지 않는다.
- **SO 수렴을 동반할 수 있는 수출 선적 쓰기(T1·T4·T5)는 SO 헤더를 먼저 `lock_chain`으로 `FOR UPDATE` 잡은 뒤** `lock_lines_for_consumption`을 부른다. 같은 트랜잭션이 이미 `FOR UPDATE`를 쥔 행에 `FOR SHARE`를 요청하면 PostgreSQL은 대기 없이 통과한다(더 강한 잠금 기보유). 따라서 시그니처는 유지되고 승격(SHARE를 쥔 두 TX가 서로 UPDATE를 기다리는 40P01)은 구조적으로 불가능해진다.
- 결과적으로 **같은 SO에 대한 선적 쓰기는 SO 단위로 직렬화**된다. 동시 부분선적 2건은 순차 처리되고, 두 번째는 첫 번째 커밋 뒤의 잔량을 본다.
- 수입 선적(T2)은 PO 상태를 바꾸지 않으므로 계약 문면 그대로 PO `FOR SHARE` → PO 라인 `FOR UPDATE`다. 배정 가능량(PO 라인 수량 − 살아 있는 수입선적 라인 합, AMB-17) 초과 검사는 라인 `FOR UPDATE` 아래에서 하므로 직렬화된다.
- `CONSUMABLE_STATUSES[SO]`에 `IN_SHIPMENT`를 추가한다(`code:modules/trade_docs/quantities.py:42-45`). 추가하지 않으면 두 번째 부분선적이 `DOCUMENT_NOT_CONSUMABLE` 409로 막힌다. **ON_HOLD는 넣지 않는다**(보류 중 선적 생성 금지 — fail-closed).
- DESIGN §8.3 부기 ②(D:229)에 한 문장 부기: "SO 상태 수렴을 동반하는 소비는 헤더를 `FOR UPDATE`로 먼저 잡은 뒤 같은 시그니처를 호출한다(FOR SHARE는 기보유 잠금에 흡수)."

**근거**: D:229 ②, D:342(잔량을 깨뜨리는 지점만 행 잠금, 전면 SERIALIZABLE 금지), code-chain 읽기 R6(`quantities.py:176-190` `with_for_update(read=True)`, `chain_ops.py:47-65`).

**대안**
- (a) SHARE로 잡고 수렴 시점에 UPDATE로 승격. **기각**: 동시 2건에서 교착(40P01 → 409 LOCK_BUSY)이 정상 경로에서 상시 발생한다.
- (b) advisory lock(SO id)으로 직렬화. **기각**: 행 잠금으로 충분하고, advisory 키 공간 규약이 새로 생긴다(D:342 "방식은 Phase 4 ADR").
- (c) 소비 헬퍼에 `mode` 인자 추가. **기각**: 시그니처 "하나" 계약 위반.

**자율 확정**: 확정(§8.3 부기 1문장 + LOCK_ORDER ADR에 동석).

**되돌리기 비용**: 낮음. 헬퍼 호출 순서만의 문제다.

---

## C4. 낙관 잠금(version)

**결정**
- `shipments`·`milestones`·`customs_records`·`shipment_parties`는 `VersionMixin`(D:342 "전표 헤더·주요 마스터에 version").
- 헤더 편집(T3)·전이(T5)는 요청 본문 `version` 대조, 불일치는 409 `COMMON.…`(기존 VersionConflictError).
- 라인 편집(T4)은 헤더 잠금 → 헤더 version 대조 → **헤더 version+1**(D:344 ④ 관용). 라인 자체는 version을 대조하지 않는다.
- 마일스톤(T6·T7)은 **행 version** 대조(타임라인에서 서로 다른 마일스톤을 동시에 고치는 것은 충돌이 아니다). 헤더 version은 올리지 않는다(헤더 내용 불변).
- 이관(T11)은 기존 규약대로 version을 올리지 않는다(`handover/targets.py` 주석 "일괄 UPDATE는 version을 올리지 않는다").
- 자동 수렴(SO 상태)은 SO version을 기존 커널 규약대로 다룬다(`record_transition`이 하는 대로 — 새 규칙 없음).

**근거**: D:342, D:344 ④.

**대안**: 마일스톤 편집도 헤더 version 대조. **기각**: 실적 입력이 잦아 무관한 편집끼리 409가 나고, 사람이 재시도하며 덮어쓰기를 학습하게 된다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## C5. 멱등 — API 키와 DB 부분 유니크

**결정**

① **`Idempotency-Key` 필수**(`IdempotencyKey` 의존성, `code:api/deps.py:94-109`): T1·T2(생성), T5(전이), T6·T7(마일스톤 쓰기 — PUT/POST 모두), T8(통보 기록), T9(통관 기록 생성·정정), T10(휴일 등록·교체). 같은 키 재수신은 최초 결과를 돌려주고 **이력 행(`milestone_changes`·상태이력·audit)도 1행만** 남는다(claim·complete가 업무 TX 안 — `code:modules/idempotency/service.py:1-21`).

② **DB UNIQUE(부분 인덱스 `WHERE deleted_at IS NULL`)** — "코드에서 확인 후 INSERT" 금지(D:350).

| 표 | 유니크 | 술어 | 위반 시 |
|---|---|---|---|
| shipments | `doc_number` 전역 UNIQUE + 형식 CHECK | (전역 — 삭제 행도 점유, D:348) | 발생 불가(채번) — 발생 시 500이 아니라 409로 번역 |
| shipment_lines | (shipment_id, so_line_id) / (shipment_id, po_line_id) | `deleted_at IS NULL` | 409 `SHIPMENTS.LINE.DUPLICATE_SOURCE` |
| shipment_parties | (shipment_id, role) | `deleted_at IS NULL` | 409 `SHIPMENTS.PARTY.DUPLICATE_ROLE` |
| milestones | (shipment_id, milestone_type) | `deleted_at IS NULL` | 409 `SHIPMENTS.MILESTONE.DUPLICATE_TYPE` |
| customs_records | (shipment_id, declaration_kind) 그리고 (declaration_kind, declaration_number) | `deleted_at IS NULL` | 409 `SHIPMENTS.CUSTOMS.DUPLICATE` |
| holidays | (country_code, holiday_date) | `deleted_at IS NULL` | 409 `HOLIDAYS.CALENDAR.DUPLICATE_DATE` |
| milestone_change_notices | (change_id, comm_log_id) | (IMMUTABLE, 술어 없음) | 409 |
| alerts | 기존 dedup 키(`notify` 코어 ON CONFLICT DO NOTHING) | 기존 | 조용히 0건(정상) |

- soft delete 뒤 같은 키 재유입은 **부활이 아니라 신규 행**이다(D:350). 복원 액션은 S3-2에 두지 않는다.
- 라인 유니크에서 원천 라인 중복을 막는 것은 "한 선적에 같은 SO 라인 2행"을 금지해 잔량 계산을 단순하게 한다(분할은 선적 단위로 한다 — P-43과 상호작용 없음).
- **제약명 → 에러 코드 번역 표**를 서비스가 아니라 기존 IntegrityError 번역 계층(제약명 매핑)에 등록한다. 미등록 제약 위반이 500으로 새지 않는지 C14 J-09가 고정한다.

**근거**: D:350, D:352 ①(정정=취소+신규), D:348, `code:modules/idempotency/service.py`, `code:api/deps.py:94-109`.

**대안**: 마일스톤 유니크에 상태 술어 추가. **기각**: 마일스톤은 상태가 없고 삭제만 의미가 있다.

**자율 확정**: 확정. P-39(멱등 키 길이 128 검사 부재)는 S3-2에서 고치지 않고 부채 유지(코어 소규모 수정 트리거 그대로).

**되돌리기 비용**: 낮음(인덱스 교체 마이그레이션).

---

## C6. 역할·소유권 매트릭스

**결정**

역할 축은 A(ADMIN, `_role_guard`가 상시 통과 — `code:api/deps.py:62-71`)·T(TRADE)·L(LOGISTICS)·C(CERT)·V(VIEWER). 아래 표는 `test:architecture/authz_matrix.py` `EXPECTED`에 그대로 행으로 들어간다.

| 엔드포인트(가칭) | A | T | L | C | V | 비고 |
|---|---|---|---|---|---|---|
| `GET /shipments`·`/{id}`·`/{id}/status-log`·`/{id}/milestones`·`/{id}/milestone-changes`·`/{id}/customs-records`·`export.csv` | ✓ | ✓ | ✓ | ✓ | ✓ | 전표는 회사 공유 자산(D:370). 원가 필드 없음(C8) |
| `POST /shipments`(수출·수입 참조 생성) | ✓ | ✓ | ✗ | ✗ | ✗ | SO·PO 잔량을 소비하고 SO 상태를 수렴시키는 상업 동작 = 무역 |
| `PATCH /shipments/{id}`(헤더 편집 가능 열·당사자) | ✓ | ✓ | ✓ | ✗ | ✗ | 포워더·관세사 지정은 물류 실무 |
| `PATCH/DELETE /shipments/{id}/lines/{line_id}` | ✓ | ✓ | ✗ | ✗ | ✗ | 잔량 소비 변경 = 무역 |
| `POST /shipments/{id}/transitions` to=출고지시 | ✓ | ✓ | ✓ | ✗ | ✗ | 출고 준비 지시는 물류도 |
| `POST /shipments/{id}/transitions` to=취소 | ✓ | ✓ | ✗ | ✗ | ✗ | SO 수렴 동반 = 무역. **같은 경로에서 대상 상태별로 서비스가 역할 판정** |
| `PUT /shipments/{id}/milestones/{type}/plan`·`/actual` | ✓ | ✓ | ✓ | ✗ | ✗ | 일정·실적은 물류 실무 |
| `POST /shipments/{id}/milestone-changes/{cid}/notices`(통보 기록) | ✓ | ✓ | ✓ | ✗ | ✗ | |
| `POST/PATCH /shipments/{id}/customs-records` | ✓ | ✓ | ✓ | ✗ | ✗ | 사실 기록만(C12) |
| `GET /holidays`·`/holiday-calendar-years` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `POST/PUT/DELETE /holidays…`(연도 선언·등록·교체·CSV 가져오기) | ✓ | ✗ | ✗ | ✗ | ✗ | 기한 데이터(ADR-03) — 관리자 전용 |
| `GET /document-flow/{doc_kind}/{doc_id}`(SHIPMENT 확장) | 기존 행 유지 | | | | | 경로 템플릿 불변(`authz_matrix.py:347-353`) — 값 공간만 확장 |

- **LOGISTICS 첫 쓰기 허용**: 지금까지 전표 쓰기는 전부 `CAN_WRITE=(RoleCode.TRADE,)`(quotations·PI·SO·PO·trade_chain·intake 라우터)였다. 선적 일정·실적·통관·당사자에서 **처음으로 L ALLOW**가 생긴다. 이는 D:37 "역할 5종(관리자/무역/물류/…)"의 물류 업무 배정이 처음 문면화되는 지점이므로 **DESIGN §2 부기 + ADR**이 필요하다.
- **단일 경로 다중 역할**(전이 엔드포인트): 라우터 가드는 `require_roles(T, L)`로 열고, 서비스가 `to` 값별로 역할을 재판정해 취소는 T만 통과시킨다(403). 이 재판정은 **존재 검사보다 먼저**(401→403→404→409→422, D:370) 해야 하는데, `to`는 본문에 있으므로 본문 검증 직후·조회 전에 판정한다. authz 매트릭스 프로브는 `to`별 2행으로 나눠 적는다(L의 취소 시도 = 403 프로브).
- **소유권 3축**(D:370)
  1. 역할 스코프: 위 표(403).
  2. 부모-자식 소속: 경로의 `line_id`·`milestone type`·`customs_record_id`·`change_id`가 경로의 `shipment_id` 소속이 아니면 **404, 부작용 0**. 참조 생성 본문의 `so_line_id`가 본문 `so_id` 소속이 아니면 404(존재하지만 소속 다름 = 404).
  3. 당사자성: 선적은 회사 공유 자산이고 담당자는 라우팅 단위이지 접근 제어가 아니다(D:370) — 당사자 축 비적용. 통보 기록의 comm_log도 공유.
- **GOVERNED_PREFIXES**에 `/api/v1/shipments`·`/api/v1/holidays`(+`/api/v1/holiday-calendar-years`를 쓰면 그것도) 추가. 등재하지 않으면 매트릭스 검사가 공회전한다(조용한 누락 — **계획 DoD 항목**).
- 신규 쓰기 스키마는 전부 `extra="forbid"`(`test:architecture/test_write_schema_forbid.py:114-131`). 참조 생성 본문에는 SKU·단가·통화·환율·거래처 필드가 **구조적으로 없다**(D:175 ①).

**근거**: D:37, D:368, D:370, `test:architecture/authz_matrix.py:24-45`, code-chain 읽기 R10.

**대안**
- (a) 쓰기 전부 T+L(AMB-13 원안). **기각**: 물류가 SO 잔량 소비·SO 수렴(상업 사실)을 일으키게 된다. 좁혀 두는 쪽이 fail-closed이며, 필요 시 행 수정 하나로 연다.
- (b) 쓰기 전부 T만. **기각**: 물류 역할이 업무에서 계속 비어 있고, 일정·실적 입력 주체(포워더 응대)가 무역에 몰린다. 렌즈 11 워크스루에서 물류 계정이 쓸 것이 없다.
- (c) 휴일 A+L. **기각**: 기한 데이터의 근거 규약(ADR-03) 관리 주체를 넓히지 않는다(부록 B B19와 일치).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(매트릭스 행·라우터 상수). 단 L을 열었다가 닫으면 그 사이 L 사용자의 업무 경로가 막히므로 "여는 쪽"만 싸다.

---

## C7. 사용자 역할 변경 화면(PR-16 부채 ③) 배정

**결정**
- **S3-2에 소단위 PR로 배정**한다. C6이 물류 쓰기를 처음 열기 때문에 운영 개시 시 물류 계정 개설이 필요해지고, 지금은 API(`POST/DELETE /users/{id}/roles`, `code:modules/identity/router.py:133-140`)뿐이라 개발자 개입이 필요하다(P:32 ③).
- 화면은 ADMIN 전용, 백엔드 신규 코드 0(기존 API 재사용). 감사는 기존 `identity.role.granted`/`identity.role.revoked`(`code:modules/audit/models.py:42-43`)가 이미 기록한다 — 화면 PR은 이 기록이 남는지 e2e로 확인만 한다(D:368 "권한변경은 audit 필수").
- 자기 자신의 ADMIN 회수 금지 같은 가드가 API에 있는지는 **PR 첫 커밋에서 실측**하고, 없으면 부채로 기록한다(추측 구현 금지 — 이 부록은 가드 신설을 결정하지 않는다).

**근거**: P:32 ③, D:368, code-platform 읽기 판정 후보 ⑧.

**대안**: 운영 개시 직전 세션으로 미룸. **기각**: S3-2 워크스루(렌즈 11)에서 물류 계정이 필요하다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(화면만).

---

## C8. 마스킹 — 원가 9채널 봉쇄 승계

**결정**
- 선적 계열 표(shipments·lines·parties·milestones·customs_records·이력·notices)에 **원가 계열 열(`*_cost`·`unit_cost`·`price_basis`)을 두지 않는다.**
- **수입 선적은 PO 라인에서 SKU·수량만 복사**하고 단가·통화·원가를 복사하지 않는다(AMB-37). 응답 스키마도 PO 원가를 조인하지 않는다. PO 원가는 `may_see_po_cost`로 라우터 1곳에서 갈리는 구조(`code:modules/purchase_orders/router.py:8`)인데, 선적이 PO를 조인해 원가를 실으면 그 갈림을 우회하는 **10번째 채널**이 된다(ADR-0024 필드 부재 방식).
- 수출 선적 라인의 판매 단가(SO 스냅샷)는 원가·마진이 아니므로 마스킹 비대상(여신·판매합계 선례 D:116 ③). 단 **금액 열 보유 여부 자체는 모델 부록 소관**이다 — 이 부록은 "보유한다면 전 역할 노출, 원가와 혼합 산출(마진) 필드는 금지"만 정한다.
- outbox payload에 금액·원가를 넣지 않는다(`code:modules/outbox/service.py:31-32`). 선적 이벤트 payload는 id·종류·상태·날짜만(C10).
- 로그: 기존 로거 마스킹 프로세서 사용. `log_context`에 원가 키를 넣지 않는다.
- CSV 내보내기(`/shipments/export.csv`)는 전 역할 동일 헤더(원가 열 없음 → 역할별 헤더 분기 불필요), UTF-8 BOM·수식 이스케이프(D:279).
- **아키텍처 테스트**: 선적 응답 스키마 전 필드에 원가 계열 이름이 없음을 스캔(`test_secret_boundaries` 계열에 선적 스키마 모듈 추가). `_amount` 접미 열이 유니크 키에 들어가지 않음(기존 `test_no_unique_key_contains_a_cost_column`이 자동 적용).

**근거**: D:37("조회는 원가·마진을 API 응답 레벨에서 마스킹"), D:368, D:39 ③(ADR-0024 9채널), `code:modules/catalog/pricing.py:50-51`(`may_see_cost`).

**대안**: 수입 선적에 PO 단가 복사 후 CostHidden 스키마 이원화. **기각**: S3-2에 원가 소비처가 없다(관세·비용 원장은 S3-4). 채널을 늘리지 않는 쪽이 엄격하다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(S3-4가 원가가 필요하면 PO에서 조인하는 별도 채널을 ADR과 함께 연다).

---

## C9. 감사 — 기록 위치 분담

**결정**

| 사건 | 기록 위치 | 이유 |
|---|---|---|
| 선적 생성·전이·취소(사유) | 선적 상태이력(`record_birth`/`record_transition`, IMMUTABLE) | "상태 변경 이력" 지위(D:356). audit_log와 이중 기록하지 않는다 |
| SO 자동 수렴 | SO 상태이력(automatic=True, 행위자 NULL은 `actor_or_automatic` CHECK가 automatic일 때만 허용 — code-chain 읽기 §1.9) | 수렴 원인 선적 id를 이력 payload에 남긴다(`PAYLOAD_KEYS` 화이트리스트 확장 필요 — `code:modules/trade_docs/transition.py:47-56`) |
| 마일스톤 계획·실적·롤오버·정정 | `milestone_changes`(IMMUTABLE, 부록 B9) | 이전값·새값·사유·행위자·시각 |
| 통보 기록 | comm_logs + `milestone_change_notices`(IMMUTABLE) | 발송이 아니라 기록(D:132 ④) |
| **통관 기록 정정**(신고번호·수리일 등) | `audit_log` 1행 `shipments.customs.corrected`(detail: 바뀐 열 이름과 전후 **날짜·번호**만) + 정정 사유 필수 | 수리일은 적재의무(+30) 산식의 단일 원천(부록 B7)이다. 원천 값이 조용히 바뀌면 파생 기한이 이력 없이 바뀐다. 표 자체를 IMMUTABLE로 하지 않는 대신 정정을 감사에 남긴다 |
| **휴일 연도 등록·교체·삭제** | `audit_log` `holidays.calendar.replaced` 등(detail: 국가·연도·건수·근거 링크·확인일) | 경고 판정 데이터의 관리자 변경 = 정책 변경 성격(정책 저장소 선례 `code:modules/policies/service.py:222`) |
| 역할 부여·회수(C7) | 기존 `identity.role.*` | D:368 |
| 담당 이관 | 기존 handover audit(`code:modules/handover/service.py:72`) | |

- `AuditAction` 상수에 신규 코드를 모은다(CHECK 없음 — `audit/models.py` 설계 의도).
- 감사 기록은 업무 TX 안(`audit.record`가 현재 TX에 추가 — `code:modules/audit/service.py:17-33`)이다. 업무 롤백이면 감사도 롤백(일어나지 않은 일을 기록하지 않는다).
- 신규 users FK(`milestone_changes.actor_user_id`, notices `actor_user_id`, 선적 상태이력 `actor_user_id`)는 `USER_FK_CLASSIFICATION`에 `ACTOR_LOG`로, `shipments.assignee_id`는 `ASSIGNMENT_TARGETS`로 등록(`test:architecture/test_user_fk_classification.py:39-48`, `test_assignment_coverage.py:47-61`).
- IMMUTABLE 등재(선적 상태이력·`milestone_changes`·`milestone_change_notices`)는 `IMMUTABLE_TABLES` + 마이그레이션 `revoke_mutations`. DESIGN §17.5 확장 부기 + ADR 세트(D:356).

**근거**: D:54(audit_log·상태 변경 이력), D:356, D:368, `code:modules/audit/service.py`.

**대안**: customs_records를 IMMUTABLE(정정=새 행). **기각**: 신고 1건당 현재값 조회가 "최신 행" 판정을 요구해 단일 원천 계약(부록 B7)이 복잡해진다. 감사 1행이 같은 추적성을 더 싸게 준다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(감사 코드는 CHECK 없음). IMMUTABLE 해제는 권한 마이그레이션이 필요해 중간.

---

## C10. 아웃박스 이벤트

**결정**

| 이벤트 | 발생 동작 | payload(화이트리스트) |
|---|---|---|
| `shipments.shipment.created` | T1·T2 | shipment_id, doc_number, shipment_kind, so_id 또는 po_id, partner_id, assignee_id |
| `shipments.shipment.<전이>` (커널 `EVENT_PREFIX[SHIPMENT]="shipments.shipment"`) | T5 | 커널 `PAYLOAD_KEYS` 그대로(from·to·reason 유무). `PARTNER_COLUMN[SHIPMENT]` 등록 필요(`code:modules/trade_docs/constants.py:76-84`) |
| SO 전이 이벤트(기존 `sales_orders.sales_order.*`) | T1·T4·T5의 자동 수렴 | 기존 + `cause_shipment_id` |
| `shipments.milestone.changed` | T6·T7 | shipment_id, milestone_type, change_kind, 전후 날짜 |
| `shipments.customs.recorded` | T9 | shipment_id, declaration_kind, 수리일 유무 |

- 이벤트 이름 형식 `<도메인>.<대상>.<사건>`, 60자 이내(`code:modules/outbox/models.py:80-82`).
- **금액·원가·개인 연락처 0**(C8).
- 매칭 `alert_rules`가 없으면 디스패처는 발송 처리만 하고 알림 0건이다(Routing.EVENT 규칙 없으면 미발송 — `code:modules/notifications/service.py:183-187`). S3-2는 **기본 규칙을 시드하지 않는다**(시드 금지 D:322). 운영자가 원하면 alert_rules로 구독한다.
- **대외 채널 0**: 디스패처의 대상은 인앱 알림뿐이다. 웹훅·메일 어댑터 연결은 ADR-07 채널 어댑터·S5-4 화이트리스트 소관이다.
- 기일 알림은 아웃박스를 거치지 않는다(스캔이 코어 직접 — D:144, `test:architecture/test_notification_core.py:57`).

**근거**: D:340, D:362, D:31(ADR-07), `code:modules/outbox/service.py:18-41`.

**대안**: SO 수렴 이벤트를 선적 이벤트에 합침. **기각**: SO 소비자가 SO 이벤트만 구독하면 수렴을 놓친다(커널 규약 그대로 쓰는 쪽이 일관).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(이벤트 추가는 가산, 제거는 구독자 확인 필요).

---

## C11. 스케줄 잡 — 배정·시각·총수·호출 경계

**결정**
- **신규 잡 1행**: `trade-deadline-scan`, `daily@06:40` KST(부록 B13·B18이 대상·문턱을 정함). 선적 마일스톤 기일 + QT/PI 만료 임박(D-N)을 한 잡에서 처리한다. **총수 12 → 13.**
- **시각 충돌 검증**(현행 12행, `code:modules/platform/scheduler.py:202-294`): 04:20·04:25·05:00·05:30·06:00·06:10·06:30·07:00·07:10·08:00·09:00 + interval@1. **06:40은 비어 있다.** 레지스트리 밖 외부 백업 03:00·일요일 복원 리허설 04:00과도 겹치지 않는다.
- **순서 의존(시각 차로만 보장 — 기존 관례 `scheduler.py:219-237`)**
  - 06:10 `document-expiry-sweep`보다 **뒤**: 그날 만료된 QT/PI는 이미 EXPIRED라 D-N 후보에서 빠진다(후보 정의 = 스윕과 동일, 부록 B18).
  - 06:30 `deadline-scan`보다 뒤, 09:00 `daily-briefing`보다 **앞**: 생성 알림이 브리핑 미확인 집계에 들어간다(브리핑 수신자 확장은 하지 않음 — 부록 B·code-platform 판정 ⑥).
  - 같은 advisory lock 규약으로 중복 기동 방지(`scheduler.py:26-33`).
- **배치**: 스캔 본체는 `app/modules/trade_chain/deadline_scan.py`(L2). `deadlines`의 순수 함수(`days_left`·`passed_thresholds` 등)와 `notifications.notify`만 임포트한다. 플랫폼 `deadlines`가 전표를 임포트하는 것은 금지(`test:architecture/test_import_direction.py:64-69`).
- **건별 독립 TX + 실패 집계**: 선적 1건(또는 QT/PI 1건)씩 별도 TX, 실패는 마스킹 로그 + `failed` 카운트, **1건이라도 실패면 잡 FAILED → 관리자 알림**(`_fail_if_any_failed`, `scheduler.py:139-148`; D:144 ⑧, D:360). 재실행은 dedup 키로 멱등.
- **휴일 경고·파생 재계산 잡은 만들지 않는다**: 둘 다 읽기 시점 계산값(부록 B). 잡을 만들면 저장 파생값이 생긴다.
- **CLI 수동 실행 서브커맨드** 1개(`app/cli.py:157-262` 선례).
- **같은 PR에서 함께 고칠 곳**
  1. `test:architecture/test_scheduler_registry.py:143-163` 4금 집합에 `"trade-deadline-scan"` 추가 + 주석 논증("읽기+alerts INSERT뿐 — 전표 상태 불변·대외 발송 없음·발주·원장 무접촉").
  2. 같은 파일 `:167-174` `test_the_registry_has_exactly_twelve_jobs…` → **thirteen**으로 개명·총수 13·`schedules["trade-deadline-scan"]=="daily@06:40"`·daily 중복 금지 유지.
  3. `test:architecture/test_no_auto_confirm_code_path_exists.py:776-795` `test_scheduler_and_cli_reach_only_the_totals_check_and_the_expiry_sweep`: `_trade_chain_imports == {"expiry_sweep"}` → `{"expiry_sweep", "deadline_scan"}`. **이 테스트를 피하려고 스캔을 trade_chain 밖 새 모듈에 두지 않는다**(보호 테스트 우회 = 공회전). 대신 신규 단언 추가: `deadline_scan`은 `record_transition`·`lock_chain`·`issue_*`·`confirm_*`·`outbox.publish`·`numbering`을 언급하지 않는다.
  4. `test:architecture/test_po_no_auto_path.py:352-361`: 잡 code·name_ko에 `purchase`·`발주`·`-po-` 금지 — `trade-deadline-scan`/"무역 기일 스캔"은 통과.
  5. `docs/runbook/prod.md:126-141` 잡 표 13행, DESIGN §15 잡 매핑 부기(D:324 ① 서식), ADR.
  6. `len(JOB_REGISTRY)` 비교 테스트(`test:integration/test_scheduler.py` 등)는 자동 추종(수정 불요 — 추론).

**근거**: D:320, D:322(코드 고정 폐쇄 열거·시드 금지), D:324 ①②(행 추가 서식·4금 논증), D:144 ⑧, `code:modules/platform/scheduler.py`.

**대안**
- (a) 선적 스캔·QT/PI D-N을 별도 2잡. **기각**: 같은 엔진·같은 의미론을 두 잡으로 나누면 시각 슬롯과 4금 논증만 늘어난다.
- (b) 기존 `deadline-scan`(06:30)에 합침. **기각**: 임포트 방향 위반.
- (c) interval 잡으로 수시 스캔. **기각**: 기존 기일 엔진이 daily이고, 알림 의미론("지났다" 판정)은 일 단위다.

**자율 확정**: 확정(부록 B13과 같은 결론 — 이 부록은 시각 충돌·호출 경계·보호 테스트 갱신 방식을 추가로 확정).

**되돌리기 비용**: 낮음. 잡 행 `enabled=false` 또는 삭제 + 총수 테스트.

---

## C12. 자동화 L3 금지 확인 — 자동 확정·자동 선적 없음

**결정**

§15 L3 금지 4영역(D:313: 지출·발주 확정 / 법적 판정 / 대외 최초 발송 / 장부 확정)과 S3-2 동작을 대조한다.

| 금지 | S3-2 위험 지점 | 판정과 기계 고정 |
|---|---|---|
| 장부 확정(원장) | 선적 "출고"는 원장 기록 시점(D:181) | 출고·선적·종결은 RESERVED(엣지 0, 모델 부록). **stock_movements 무접촉** — 선적 모듈이 재고 원장 모듈을 임포트하지 않음(현재 원장 모듈 미존재, S4-1 착수 시 import-direction 규칙으로 확인) |
| 발주 확정 | 수입선적이 PO를 바꾸는가 | PO 상태·잔량 무변경. PO 자동 엣지 0 유지(`test:architecture/test_doc_machines.py:123-126`의 PO 단언 유지) |
| 법적 판정 | customs_records의 세율·HS, 휴일 판단 | 사실 기록 열만(세율·과세가격·HS 판정 열 0). 휴일은 경고이지 판정이 아니다(부록 B12) |
| 대외 발송 | 롤오버 통보, 기일 알림 | 통보 = 기록(T8, 발송 코드 0). 기일 알림 = 인앱(C10·C11). 바이어 리마인드 0(S5-4 소관 D:319) |
| **자동 확정 부재** | SO 확정, 선적 생성 | 아래 |

- **자동 선적 금지**: 선적 생성(`record_birth` for SHIPMENT)은 **사람 1클릭 단일 경로**다. SO 확정(`confirm_sales_order`)·인테이크·보드 벌크·스케줄러·CLI·임포트·이관·알림·아웃박스 핸들러에서 선적 생성 함수를 호출·임포트하지 않는다. "선적 계획 초안" 자동 생성(D:190)도 S3-2에서는 사람 1클릭(AMB-23 — L2).
- **SO 자동 엣지 개방의 4금 논증**: SO CONFIRMED↔IN_SHIPMENT는 `AUTO_TRANSITIONS[SO]`에 처음 들어가는 엣지다. 도착 상태 IN_SHIPMENT·CONFIRMED는 **약정 진입(확정)이 아니라 이행 진행·복귀**이고, 발동 원인은 사람 1클릭 선적 동작의 같은 TX뿐이다(스케줄러 경로 0). RECEIVED→CONFIRMED(확정)는 여전히 사람 전용이다. `test_po_and_so_have_no_automatic_edges`(`test_doc_machines.py:123-126`)는 **개명·의미 변경**: "PO 자동 엣지 0, SO 자동 엣지 = {(CONFIRMED,IN_SHIPMENT),(IN_SHIPMENT,CONFIRMED)} 정확히, 그리고 어느 자동 엣지도 CONFIRMED에 RECEIVED에서 도착하지 않는다." DESIGN §15 [M4] 부기 ②(D:324)의 "SO 0" 문면을 같은 PR에서 부기 개정 + ADR.
- **`test_no_auto_confirm_code_path_exists.py` REGISTRY 엔트리 추가**(엔트리 방식 `:40-`)

| 보호 함수(가칭) | 허용 호출처 | 금지 경로 |
|---|---|---|
| `create_shipment_from_sales_order` / `create_shipment_from_purchase_order`(`record_birth` SHIPMENT 호출 단일 착지) | trade_chain 선적 서비스 + 라우터 1곳, 행위자 필수, 멱등 키 | platform·imports·handover·notifications·outbox·worklist·deadlines·collaboration·order_board·order_intake·confirm |
| `transition_shipment` | 라우터 1곳, 행위자 필수 | 위와 같음 |
| `converge_sales_order_shipping`(SO 자동 수렴) | 선적 생성·라인 삭제·선적 취소 서비스 파일만 | 스케줄러·CLI·deadline_scan·확정·보드 |
| `trade_chain/deadline_scan.py` | scheduler·cli | `record_transition`·`record_birth`·`lock_chain` 언급 0 |

  - `record_transition` 허용 호출처 6파일(`:64-93`)에 선적 서비스 파일 추가, `record_birth` 허용 호출처(`:94-110`)에 선적 생성 파일 추가. 추가하지 않으면 실패하는 것이 설계된 안전망이다.
- **COMPLETED 미개방**(AMB-15): SO→COMPLETED 엣지를 열지 않는다. 노출 술어 `CLOSED_STATUSES=("COMPLETED","CANCELLED")`(`code:modules/credit/exposure.py:19`)가 COMPLETED를 노출에서 빼므로 S3-3 미수 provider 전 개방 시 노출 공백이 생긴다. 기계 고정: 아키텍처 테스트 "SO의 COMPLETED 진입 엣지 수 > 0 ⇒ 미수 provider가 기본값이 아니다(`is_default_provider()==False`)"(`code:modules/credit/providers.py:94-108` 훅). `open_order_amount`·노출 술어는 **무접촉**(`test:architecture/test_approval_contract.py:604-612` 정의 단일 위치 유지).

**근거**: D:313, D:324 ②, D:184 ③, D:190, W:114(P-01 조건부 금지), code-chain 읽기 R1·§3.4.

**대안**
- (a) SO IN_SHIPMENT를 사람 엣지로. **기각**: 선적이 있는데 SO가 CONFIRMED인 거짓 상태가 사람 누락으로 생긴다. 또 라우터 Literal 공개(`code:modules/trade_chain/router.py:95-96,109-110` 임포트 assert)와 `test:e2e/test_sales_order_editing.py:529-537`(IN_SHIPMENT 범용 전이 422) 계약이 깨진다.
- (b) COMPLETED를 열고 노출 술어를 `("CANCELLED",)`로 좁힘. **기각**: 인덱스 술어 마이그레이션(`sales_orders/models.py:196-204`)과 노출 의미 변경이 S3-3 provider와 분리되어 이중 계산 위험(S3-3 DoD W:121)이 생긴다.

**자율 확정**: 확정. **WBS 문면("RESERVED 엣지 COMPLETED 추가", W:114)과 어긋나므로 멈춰서 보고할 항목**이며, ADR + WBS 주석(S3-3 provider PR로 이관)으로 해소한다.

**되돌리기 비용**: 낮음(엣지 가산). 반대로 COMPLETED를 먼저 열고 공백이 실재하면 과소 노출 상태에서 확정된 SO를 소급 재평가할 수 없어 높다.

---

## C13. 에러 코드와 번역

**결정**
- 재사용: `TRADE_DOCS.TRANSITION.NOT_ALLOWED`, `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(SO 취소 시 살아 있는 선적 — CHILD_LINKS 등록만으로 자동), `TRADE_DOCS.QUANTITY.EXCEEDS_OPEN`, `TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE`, `COMMON.CONCURRENCY.LOCK_BUSY`(55P03·40P01 → 409, `code:core/errors/handlers.py:122-133`; 57014는 500 유지 — P-47).
- 신규(이 부록 범위): `SHIPMENTS.LINE.DUPLICATE_SOURCE`, `SHIPMENTS.PARTY.DUPLICATE_ROLE`, `SHIPMENTS.MILESTONE.DUPLICATE_TYPE`, `SHIPMENTS.CUSTOMS.DUPLICATE`, `SHIPMENTS.CUSTOMS.REASON_REQUIRED`(정정 사유), `SHIPMENTS.TRANSITION.ROLE_NOT_ALLOWED`는 만들지 않고 **기존 403(ForbiddenError)** 을 쓴다(역할 판정은 403 단일 의미), `SHIPMENTS.IMPORT.EXCEEDS_ASSIGNABLE`(수입선적 배정 가능량 초과 409), `HOLIDAYS.CALENDAR.DUPLICATE_DATE`. 마일스톤·휴일 내용 검증 코드는 부록 B B19 목록을 따른다.
- 형식: 3세그먼트·카탈로그 1:1·문구에 조치 힌트(`test:unit/test_error_catalog.py:19-49`), detail과 log_context 분리(`test:e2e/test_error_contract.py:56-85`). 409 detail에 금액 없음(잔량 수량만).

**근거**: D:374, D:344 ③.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## C14. 테스트 설계 — H·J 중심(+I·K 경계 항목)

원칙: 동시성 항목은 **실제 동시 실행**(스레드/별도 커넥션 + 장벽)으로 증명한다(GC-F1/F2 규칙). 단일 커넥션 순차 호출로 "동시"를 흉내 내지 않는다. 신규 GC(v1.5) 등재 여부는 계획 통합에서 정하고, 아래 ★는 `golden` 마커 후보다.

### J. 안전 계약

| ID | 시나리오 | 기대 | 위치(가칭) |
|---|---|---|---|
| J-01 | 선적 생성 더블클릭(같은 Idempotency-Key 2회, 동시) | 선적 1건·채번 1회·SO 상태이력 1행·outbox 1건, 두 응답 동일 | `tests/integration/test_shipment_concurrency.py` |
| J-02 ★ | SO 라인 수량 10, **다른 키로 동시 선적 7+7** | 1건 성공·1건 409 `EXCEEDS_OPEN`, 잔량 3, 500·40P01 0 | 같은 파일 |
| J-03 | 같은 SO에 동시 선적 20건(각 1) + 수량 15 | 성공 15·거부 5, 잔량 0, 교착 0(H "동시 20명 벌크 경합"과 공유) | 같은 파일 |
| J-04 | 선적 생성 vs 같은 SO 취소 동시 | 둘 중 하나만 성공: 선적 먼저면 취소 409 `SUCCESSOR_ALIVE`, 취소 먼저면 선적 409 `NOT_CONSUMABLE`. 둘 다 성공 0·500 0 | 같은 파일 |
| J-05 | 마지막 살아 있는 선적 취소 vs 같은 SO 새 선적 생성 동시 | 최종 SO 상태 = 살아 있는 선적 유무와 일치(IN_SHIPMENT⇔선적≥1) — 거짓 상태 0 | 같은 파일 |
| J-06 | 수입선적 동시 2건이 PO 라인 배정 가능량 초과 | 1건 409 `EXCEEDS_ASSIGNABLE`, PO 상태·PO `open_quantity` 불변 | 같은 파일 |
| J-07 | 잠금 순서 계측: T1·T4·T5·T6·T9 경로에서 잡힌 테이블 순서 기록 | LOCK_ORDER 부분수열(기존 계측 패턴 `test_sales_order_concurrency.py:752-753` 등) | 같은 파일 |
| J-08 | 트랜잭션 중간 실패 주입: 라인 INSERT 후·채번 직전 예외 | 선적 0·라인 0·SO 상태 불변·상태이력 0·outbox 0·채번 카운터 불변 | `tests/integration/test_shipment_atomicity.py` |
| J-09 | 부분 유니크 위반 5종(C5 표) | 각각 지정 409 코드, **500 0**(제약명 번역 누락 검출) | 같은 파일 |
| J-10 | version 충돌: 헤더 편집 2회(같은 version) / 마일스톤 행 2회 / 라인 편집 vs 헤더 편집 | 두 번째 409, 데이터는 첫 번째 값 | `tests/e2e/test_shipments.py` |
| J-11 | 앱 계정으로 선적 상태이력·`milestone_changes`·notices UPDATE/DELETE | DB 권한 거부(`test_table_policy.py:51-64` 자동 + 표별 직접 시도 1건씩) | `tests/integration/test_shipment_constraints.py` |
| J-12 | outbox: 업무 TX 롤백 시 이벤트 0, 커밋 후 디스패치 실패 → 재시도(next_retry_at) | 발송 0 / 재시도 기록 | `tests/integration/test_shipment_outbox.py` |
| J-13 | soft delete 후 동일 키 재유입: 마일스톤 (선적,종류), 휴일 (국가,날짜), 당사자 (선적,역할) | 신규 행(부활 아님), 삭제 행 그대로 | `tests/integration/test_shipment_constraints.py` |
| J-14 | 롤오버 같은 키 재요청 | `milestone_changes` 1행 | `tests/e2e/test_shipment_milestones.py` |
| J-15 | 55P03 주입(lock_timeout 단축 + 상대 TX가 SO 보유) | 409 `LOCK_BUSY`, 부작용 0 | `test_shipment_concurrency.py` |

### H. 운영

| ID | 시나리오 | 기대 | 위치 |
|---|---|---|---|
| H-01 | 확인(ack)한 마일스톤 알림 후 같은 날 잡 재실행 | 재발송 0(dedup) | `tests/integration/test_trade_deadline_scan.py` |
| H-02 | 계획일 D-3에 이전 알림 미확인 | 에스컬레이션 1회(`ESCALATION_DAYS=3`, `code:modules/deadlines/service.py:64,266`), 재실행 시 추가 0 | 같은 파일 |
| H-03 ★ | 롤오버로 기일 변경 | 새 기일 = 새 알림(dedup 키 `@기일`), 옛 기일 알림 재발송 0 | 같은 파일 |
| H-04 | 담당 일괄 이관(선적 담당 A→B) 후 스캔 | 새 알림 수신자 = B(즉시 반영), 기존 미확인 알림도 이관 규약대로 B | `tests/e2e/test_handover.py` 확장 |
| H-05 | 담당자 없음 + 규칙 없음 | ADMIN 폴백(Routing.DEADLINE) | scan 파일 |
| H-06 | 기능 플래그 오프(L/C): L/C 결제 SO에서 선적 생성 | 생성은 되나 L/C 파생 마일스톤 = "산정 불가 — L/C 조건 미등록"(UNKNOWN), 알림 대상 0, L/C 입력 엔드포인트 0(라우트 스캔) | `tests/e2e/test_shipment_milestones.py` |
| H-07 | QT/PI D-N: ISSUED·후속 없음·valid_until D-3 / 후속 있음 / 이미 EXPIRED | 첫째만 알림 | scan 파일 |
| H-08 | 동시 20 선적 경합 | = J-03 | — |

### I. 자동화(경계 항목)

| ID | 시나리오 | 기대 |
|---|---|---|
| I-01 | `test_no_auto_confirm_code_path_exists` 신규 엔트리 4종(C12) | 허용 호출처 밖 언급·임포트 0, 엔트리 비공회전 |
| I-02 | 스케줄 레지스트리 13·06:40·daily 중복 0·4금 집합 | C11 갱신 |
| I-03 | 스캔 1건 실패 주입(한 선적 조회에서 예외) | 나머지 건 처리 완료, 잡 FAILED, 관리자 알림 1건 |
| I-04 | SO 자동 엣지 정확히 2개·COMPLETED 진입 0·provider 훅 단언 | C12 |
| I-05 | `confirm.py`·`order_board/bulk.py`·인테이크가 선적 모듈을 임포트하지 않음 | 자동 선적 0 |

### K. 보안·품질(경계 항목)

| ID | 시나리오 | 기대 |
|---|---|---|
| K-01 | authz 매트릭스 C6 전 행 프로브(5역할) + 전이 `to`별 2행 | 표와 일치(`test_authz_matrix.py:56-70` 메커니즘) |
| K-02 | VIEWER·CERT가 선적 쓰기 URL 호출 | 403, 부작용 0 |
| K-03 | 부모-자식 불일치(선적 A 경로 + 선적 B 소속 마일스톤·라인·통관·변경 id) | 404, 부작용 0 |
| K-04 | 역할 403이 존재 404보다 먼저(없는 선적 id에 VIEWER 쓰기) | 403 |
| K-05 | 전 목록 페이지네이션 자동 스캔(선적·변경 이력·통관·휴일) | Page 봉투·기본 50(`test_auth_coverage.py:131-170` 자동) |
| K-06 | 선적 응답·CSV·outbox payload·로그에 원가 계열 키 0 | C8 스캔 |
| K-07 | 수입선적 응답에 PO `unit_cost` 부재(원가 열람 가능 역할로도) | 필드 부재 |

### 기존 테스트 갱신 목록(이 부록 범위 — 실패가 정상인 안전망)
- `test_scheduler_registry.py:143-174`(C11), `test_no_auto_confirm_code_path_exists.py:64-110,776-795`(C11·C12), `test_doc_machines.py:123-126`(C12 개명), `authz_matrix.py` GOVERNED_PREFIXES·EXPECTED(C6), `test_write_schema_forbid.py`(C6), `test_user_fk_classification.py`·`test_assignment_coverage.py`·`handover/targets.py`(C9·C2), `test_table_policy.py`(C9), `test_error_catalog.py`(C13). 상태 총수·CHILD_LINKS·LINE_CONSUMERS·FIELD_POLICY·보드 매핑 테스트는 모델·상태 부록 소관.

**근거**: D:426(H), D:427(I), D:428(J), D:429(K), D:417 ①(P3=A·B·E·G·H·I·K), GC-F1/F2 실제 동시 실행 규칙.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(테스트 가산).

---

## 자율 확정 판정표

| 안건 | 결정 한 줄 | 문서 갱신 | 되돌리기 |
|---|---|---|---|
| C1 | 동작 12종 각 1TX, SO 수렴은 같은 TX, 외부 호출 0 | §7.5 부기 | 낮음 |
| C2 | LOCK_ORDER: …PO → **shipments → shipment_children** → approvals → lines → seq, 휴일 비편입, 이관 순서 동기 | §17.2 부기 ② + **ADR** | 중간 |
| C3 | 수렴 동반 소비는 SO FOR UPDATE 선점 후 기존 시그니처(SHARE 흡수), 승격 금지, CONSUMABLE에 IN_SHIPMENT | §8.3 부기 ② 1문장 | 낮음 |
| C4 | 헤더·마일스톤 행·통관·당사자 version, 라인 편집은 헤더 version+1 | — | 낮음 |
| C5 | 생성·전이·마일스톤·통보·통관·휴일 쓰기 전부 Idempotency-Key, 부분 유니크 7종, 제약명→409 | — | 낮음 |
| C6 | 조회 전 역할 / 생성·라인·취소 = T / 헤더·출고지시·마일스톤·통보·통관 = T+L / 휴일 = A | §2 부기 + **ADR**(물류 첫 쓰기) | 낮음 |
| C7 | 역할 변경 화면 S3-2 소PR, 백엔드 0 | PROGRESS 부채 ③ 종결 | 낮음 |
| C8 | 선적 계열 원가 열 0, 수입선적 단가 비복사, payload 금액 0 | — | 낮음 |
| C9 | 상태·마일스톤은 IMMUTABLE 이력, 통관 정정·휴일 교체는 audit_log | §17.5 확장 + ADR | 낮음~중간 |
| C10 | 이벤트 5종, 규칙 시드 0, 대외 채널 0 | — | 낮음 |
| C11 | `trade-deadline-scan` daily@06:40, 12→13, scan은 trade_chain 내부, 보호 테스트 갱신(우회 금지) | §15 부기 + runbook + ADR | 낮음 |
| C12 | SO 자동 엣지 CONFIRMED↔IN_SHIPMENT만(4금 논증), COMPLETED 미개방, 자동 선적·초안 0, 레지스트리 4엔트리 | §15 부기 ② 개정 + ADR + **WBS 주석** | 낮음 |
| C13 | 기존 코드 재사용 + 신규 6종, 역할 재판정은 403 | — | 낮음 |
| C14 | J 15·H 8·I 5·K 7 케이스, 실제 동시 실행 | — | 낮음 |

## 멈춰서 보고할 항목 (DESIGN·WBS 부기와 ADR로 해소 전제)
1. **W:114 "RESERVED 엣지 COMPLETED 추가" vs 노출 술어의 COMPLETED 제외·P-01** → C12에서 COMPLETED 미개방으로 자율 확정. WBS 주석으로 S3-3 provider PR로 이관.
2. **D:324 [M4] 부기 ② 및 `test_po_and_so_have_no_automatic_edges`의 "SO 자동 엣지 0" vs SO CONFIRMED↔IN_SHIPMENT 자동 수렴** → C12에서 4금 논증과 함께 부기 개정.
3. **D:229 ② "FOR SHARE→라인 FOR UPDATE" 문면 vs 같은 TX의 SO 수렴(FOR UPDATE 필요)** → C3에서 시그니처 유지 + 선점 해석 1문장 부기.
4. **D:344 ② LOCK_ORDER 변경 = ADR 필수** → C2.
5. **D:37 물류 역할의 첫 쓰기 권한** → C6에서 §2 부기 + ADR.

**실행 검증 못 했음.** 위 테스트 ID는 설계이며 아직 존재하지 않는다. 인용한 코드 줄은 `a4d91c0` 정적 독해 값이고, 각 PR 첫 커밋에서 실측 기록한다(P-60 선례).
