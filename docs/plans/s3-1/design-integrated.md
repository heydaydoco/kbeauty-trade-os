# S3-1 계획 설계 — 통합 검토서 (6개 묶음 A~F 통합)

- 작성: 통합 검토자. 입력: `d-A.md`~`d-F.md` 전문 정독 + 리포 실측(읽기·grep만). **pytest·alembic·DB·서버 미실행 — 정적 독해만 했다. 실행 검증 못 했음.**
- 표기: `A/A2` = d-A.md 안건 A2, `B/B4(c)` = d-B.md 안건 B4의 (c)항. 안건 번호는 각 묶음 판정서의 번호를 그대로 쓴다.
- 성격: 설계 결정. 전 항목 **자율 확정**(오너 지시 2026-09-29, 사후 번복 가능 — 번복 가능성이 높은 항목은 §4.8에 되돌리기 비용과 함께 모았다). "미정" 결론 없음.
- 통합 해소 원칙(충돌 시 이 순서로 골랐다): ① 리포 실측(코드·기존 테스트·DESIGN 문면)과 어긋나는 쪽이 진다 ② 더 좁고 fail-closed인 쪽 ③ 소유 세션이 명확한 쪽(열=A, 상태·불변·잠금 계약=B, 승인=C, 인테이크·게이트·확정 오케스트레이션=D, 여신·입금·정책=E, 역할·마스터·PO=F) ④ 같은 개념은 이름 하나, 저장 위치 하나, 진입 함수 하나.
- 리포 실측으로 이번 통합에서 직접 확인한 사실(묶음 판정서의 인용을 재검증한 것): 에러코드 실제 형태는 `COMMON.CONCURRENCY.VERSION_CONFLICT`·`COMMON.IDEMPOTENCY.KEY_CONFLICT`(→ 잠금 대기 초과 코드는 `COMMON.CONCURRENCY.*` 계열이 맞다) / `JOB_REGISTRY` 현 7행(06:00·interval@1·06:30·09:00·07:00·05:00·08:00) / `PartnerCreateRequest`·`SkuCreateRequest`에 `extra=forbid` **없음**(A/A8·A11의 "forbid 유지"는 사실 오류 — X-44) / 요청 본문의 낙관 잠금 필드는 저장소 관용이 `version`(certifications 등)이며 `expected_version`은 임포트 내부 필드뿐 / DESIGN §15 L3 금지 문구에 "장부 확정(분개·마감·**전표**·원장)"이 들어 있음(B/B7의 G8 용어 충돌은 실재) / DESIGN §7.2 "QT: 작성→발행→(수주전환|만료|취소)" / 골든 케이스 현재 v1.3(A1~A5·F1·G1·H1~H2 등) / ADR 최신 0050.

---

## 0. 결론 요약

1. **모순·중복 52건 + 누락 14건**을 찾았다(§1). 대부분 "같은 개념을 두 묶음이 각자 저장/명명"한 것이고, 진짜 설계 충돌은 네 덩어리다 — ① 승인 결속 토큰(content_rev vs digest) ② PI 입금 게이트 의미(D의 WARN=비차단·BLOCK=ADMIN override vs E의 WARN=사유 요구·BLOCK=우회 불가) ③ 확정 오케스트레이션 이중 정의(D5 vs E4) ④ QT "수주전환" 시점(A: SO 확정 vs B: 후속 전표 첫 생성). 전부 하나로 정했다.
2. **최종 신규 테이블 24개**(A 9 + B 4 + C 4 + D 5 + E 2). D의 `gate_policies`는 E의 `policy_settings`와 중복이라 삭제했다. 기존 테이블 변경은 `partners`(+2열)·`skus`(+1열) 2건뿐이고, 신규 테이블에 대한 사후 ALTER는 확정 배선 마이그레이션 1건(SO 증적 3열·상태이력의 `approval_id`)이다.
3. **에러코드 신규 56종**으로 정리(제안 합계 65종 → 중복·미사용 9종 제거, 도메인 접두를 `TRADE`→`TRADE_DOCS`로 통일).
4. **JOB_REGISTRY 7행 → 12행**(F의 "11행"은 A의 `trade-docs-totals-verify`를 빠뜨린 계산).
5. **ADR 27건 제안 → 17건(0051~0067)으로 통합** + 기존 ADR 부기 10건. 계획서 최초 PR(PR-1)은 문서 전용으로 이 전부를 등재한다.
6. **PR 분할 추천 = 16 PR 수직 슬라이스안**(§3.3). DoD 4항과 A/H/J/K 검증의 PR 배치는 §3.4.
7. 자동화 4금·§15 재점검 결과 **저촉 없음**. 단 (a) §15 L3 금지의 "전표" 용어는 ADR-0058이 "회계 전표"로 명문 해소해야 하고 (b) E1의 '미수 미반영'은 "평가 불능을 통과로 취급 금지" 원칙의 **유일한 의도적 예외**(배지로 fail-visible)이며 이월 미수 문제가 실재하므로 오너 판정 후보로 올렸다(§5).
8. **PR-1에서 코드 착수 전에 사람이 봐야 할 것은 §4.8의 번복 가능성 높은 항목뿐**이다(1인 ADMIN 자기 승인 불가, 전표 소유권 공유 해석, 정책 미설정=BLOCK 운영 개시 절차, L/C 닫힘, 환율 수동 입력, 이월 미수).

---

## 1. 모순·중복·누락 전수 목록

### 1.1 스키마·열·소유

| ID | 충돌·중복(인용) | 권장 해소안 1개 (소유 · 수정 내용) |
|---|---|---|
| X-01 | `doc_number` 유니크. A/A2 인덱스 절 "`unique_active(doc_number)`"(부분 UNIQUE) + `VARCHAR(20)` ↔ B/B4(c) "**전역 UNIQUE — 부분 인덱스 아님**"+접두어 형식 CHECK | **B 채택**. 전표 삭제 경로가 0이고 §17.3이 재발급을 금지하므로 "삭제 후 재유입 허용"이 목적인 부분 인덱스가 오히려 결번 재사용 구멍이 된다. 소유=B. A의 헤더 표 행을 `UNIQUE uq_<t>_doc_number`(전역)+`ck_<t>_doc_number_format`으로 교체, 폭 VARCHAR(20) 유지. §17.4 "문서번호"는 외부 유입 멱등 축((partner, 바이어 PO번호))으로 해석 |
| X-02 | 내부 메모. A/A2 `note VARCHAR(1000)`(헤더)·`note VARCHAR(500)`(라인), 둘 다 "동결 제외"(A/A3) ↔ B/B2 `internal_note TEXT` + **FREE는 정확히 4개**(internal_note·assignee_id·oc_reference·oc_received_on)·라인 열은 전부 CONTENT | 헤더 `internal_note VARCHAR(1000) NULL`(이름=B, 폭=A). **라인 `note` 열 삭제**(소비자 없음, FREE 4개 고정 위반). A/A3의 동결 제외 목록에서 `note` 삭제. 소유=A(열)·B(분류) |
| X-03 | 동결 표식. A/A3 4헤더 공통 `frozen_at`(PI·PO는 NOT NULL DEFAULT now()) ↔ B/B2·D/D5·E/E1·E4의 SO `confirmed_at`(재개 판정·노출 술어·확정 증적 CHECK·부분 인덱스가 전부 이 열). SO에 같은 뜻의 열 2개 | **SO는 `confirmed_at` 하나만**(=동결 시각, `frozen_at` 미생성). QT·PI·PO는 `frozen_at`. 단일 출처 상수 `trade_docs.constants.FREEZE_COLUMN = {QT:'frozen_at', PI:'frozen_at', SO:'confirmed_at', PO:'frozen_at'}`를 A의 `frozen_complete` CHECK·B의 동결 판정·FIELD_POLICY가 공유. QT에 상태 결속 CHECK 추가(`DRAFT`→`frozen_at IS NULL`, `ISSUED·CONVERTED`→NOT NULL; `CANCELLED·EXPIRED`는 무제약). 소유=A(열)·B(CHECK) |
| X-04 | 열 분류 레지스트리 이중. A/A3 `FROZEN_HEADER_COLUMNS`·`FROZEN_LINE_COLUMNS`·`MUTABLE_AFTER_FREEZE` ↔ B/B2 `FIELD_POLICY`(CONTENT/ORIGIN/FREE/SYSTEM, 미등재=CI 실패) | **`FIELD_POLICY`(B)가 유일 정본**. A의 두 집합은 그 초기 분류 내용(CONTENT+ORIGIN=동결, FREE+SYSTEM=동결 제외)으로 흡수하고 `FROZEN_*`는 FIELD_POLICY에서 **파생**(수기 정의 금지). 소유=B(메커니즘)·A(열별 분류표 작성 의무). E의 SO 증적 3열·B의 `confirmed_at`은 SYSTEM |
| X-05 | 라인 수량 열. B/B5(c) `ordered_quantity`(+`CHECK >0`) ↔ A/A2 `quantity`(CHECK 1..99,999,999) | **`quantity`**. B5의 `ConsumerSpec.qty_col`·문구를 개명(A의 CHECK가 `>0`을 이미 포함). 소유=B |
| X-06 | `assignee_id` NULL 여부. A/A2 NOT NULL ↔ B/B2(c) "NULL FK users" | **NOT NULL**(D/D1 인테이크·F/F9 담당자 유효성·F/F10 handover와 정합). 소유=A |
| X-07 | **승인 결속 토큰 3중**. B/B2 SO `content_rev`("승인 결속 토큰")·D/D3 `gate_evaluations.content_rev`·D/0-3(C 가정 `find_consumable(subject_id, content_rev)`) ↔ C/C6 "**digest(version 아님)** — 서비스가 올리는 카운터는 훅이 빠지면 fail-open, 라인만 바뀌고 부모 version이 안 오르는 S1-3 PR-3 결함 유형"·E/E5 `gate_input_digest` | **digest 단일**. `sales_orders.service.gate_input_digest(session, so_id)`(소유=A·B의 SO 모듈)를 C의 `TargetSpec.snapshot.digest`와 D의 `gate_evaluations`가 공유. 입력 집합은 E/E5 기본안 `{buyer_partner_id, currency, fx_rate(str), fx_rate_date, lines:[[sku_id, quantity, unit_price_amount, is_free]…line_no순]}`(정수 문자열·NFC, 골든 벡터 테스트). **`sales_orders.content_rev` 열 삭제**(B2 SYSTEM 목록에서도), `gate_evaluations.content_rev`→`input_digest CHAR(64)`. 게이트별 override 결속은 그대로 `basis_hash`(D3) — 이름을 섞지 않는다 |
| X-08 | QT 개정 vs 복제. A/A10 `revises_qt_id` 자기참조 + "개정 발행 시 원본을 REVISED로 취소", A/A4 `active_descendants`에 **개정본 포함** ↔ B/B2 `copied_from_id`(4종 공통, 원본은 CANCELLED·EXPIRED일 때만 `COPY.SOURCE_NOT_ELIGIBLE`)·B/B3 "copied_from은 사슬 후속이 아니다(살아 있음 판정 제외)". 두 자기참조 열 + 서로 반대인 후속 판정 → 개정 발행 시 초안 개정본이 원본의 살아 있는 후속이 되어 원본 취소가 자기 자신 때문에 막히는 교착 | **`copied_from_id` 하나로 통일, `revises_qt_id` 삭제**. `POST /quotations/{id}/revisions`는 "원본이 ISSUED일 때만 허용되는 유일한 복제 진입점"(B의 COPY 규칙에 QT·ISSUED 한정 예외 1줄). 개정 초안 발행 트랜잭션이 원본을 B의 사람 엣지 #3(ISSUED→CANCELLED, 행위자=발행자, 사유 자동 문구)으로 취소 — 새 엣지·새 사유 코드 불필요(A가 요청한 "합성 전이"는 이 호출로 충족). 개정본은 CHILD_LINKS에서 제외(B3 규칙 유지). A/A2의 "활성 개정본 1개" 부분 유니크는 **4종 공통** `UNIQUE (copied_from_id) WHERE deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED','EXPIRED')`로 일반화("복제=중복 생성 차단"을 DB가 보증). 소유=A(열·인덱스)·B(규칙) |
| X-09 | SO 거래처 변경 가능 여부. A/A2 "거래처 변경 시 재검증·`buyer_name` 재복사"·A/A4 "참조 SO만 거래처 변경 금지" ↔ B/B2 ORIGIN "SO의 partner_id(중복 PO 키·여신 잠금 축)"·D/D1 "거래처·통화 등록 후 불변"·E/E3 "잠금 전 무잠금 조회 후 재확인(SO의 buyer_partner_id 불변)" | **SO `buyer_partner_id`=ORIGIN(생성 후 어느 상태에서도 불변)**. 여신 잠금이 "거래처→SO" 순서를 지키려면 SO의 거래처를 잠금 전에 읽을 수 있어야 하기 때문. QT는 DRAFT에서 CONTENT(편집 가능), PI는 QT에서 복사되는 ORIGIN. SO 통화는 CONTENT이되 라인이 있으면 복합 FK가 막는다(A 유지). A의 SO 편집 문구·"거래처 변경 3시점 재검증" 중 SO 항목 삭제. 소유=A |
| X-10 | SO 헤더 열 목록이 3곳에 흩어짐. A/A2 표에는 `buyer_po_date`(D 요구)·`confirmed_at`(B)·`credit_*`(E)가 없음 | §2.1의 **SO 최종 열 표**를 정본으로 채택(A의 표 + `buyer_po_date DATE NULL` + `confirmed_at` + CHECK + 확정 배선 마이그레이션의 증적 3열). 소유=A가 표를 갱신, 배선 열은 E |
| X-11 | 확정 증적 이중 저장. E/E4 SO 5열(`credit_verdict`·`credit_approval_id`·`pi_gate_verdict`·`pi_gate_override_reason`·`confirm_gate_snapshot` JSONB) ↔ D/D3 불변 `gate_evaluations`(CONFIRMED 행 SO당 1행, `results` JSON에 WARN·override·승인 ref) | **DB가 게이트 없는 확정을 거부하는 데 필요한 3열만 SO에 둔다**: `credit_verdict`·`credit_approval_id`·`pi_gate_verdict`(+E의 CHECK·승인 UNIQUE). `pi_gate_override_reason`(→ D의 `gate_overrides.reason`)과 `confirm_gate_snapshot`(→ `gate_evaluations.results`)은 삭제 — 상세 증적의 원천은 `gate_evaluations` 하나. SO 상세 응답의 확정 증적은 CONFIRMED 행을 조인. SYSTEM 열은 5→3. 소유=E(열)·D(evaluations) |
| X-12 | **정책 저장소 이중**. D/D3 `gate_policies`(gate_code + JSONB config, 0행=UNKNOWN, CLI `seed-gate-policies` 기본 500bp·WARN, `PUT /gate-policies/{gate_code}`, GET=TRADE·ADMIN) ↔ E/E8 `policy_settings`(키 CHECK 폐쇄 2개 `pi_advance_gate_mode`·`price_deviation_tolerance_bp`, 미설정=BLOCK/0bp+source 표시, 시드·CLI 금지, `GET/PUT /policies` ADMIN 전용, 화면 `/settings/policies`). 같은 두 값을 두 표·두 기본값·두 API가 정의 | **E의 `policy_settings` 단일**(E/E8이 "D가 다른 저장을 택하면 키를 제거"라고 스스로 유보). D의 `gate_policies` 테이블·CLI·`GATES.POLICY.INVALID_CONFIG`·`PUT /gate-policies` 삭제. 미설정 동작은 E: PI 모드=BLOCK(`source=UNSET_DEFAULT`), 허용치=0bp(`source=UNSET_DEFAULT`, 정확히 기준가인 라인은 통과·1단위라도 벗어나면 BLOCK/OVERRIDE) — D의 "0행=전 라인 UNKNOWN"보다 소음이 적고 fail-visible 유지. 게이트 응답·증적(`gate_evaluations.results`)에 `policy_source` 포함. GET은 ADMIN 전용, 실효값·출처는 게이트 응답으로 전 역할 열람. 소유=E |
| X-13 | SO·PI 재접수(복제) 진입 공백. B/B2·B8은 "복제=생성 POST의 `copied_from_id`", B 부록A는 "SO copy=인테이크 초안+`copied_from_id`" 전제 ↔ D/D1의 `order_intakes`에 `copied_from` 열 없음, A/A4의 PI·SO 참조 생성 본문에 `copied_from_id` 필드 없음 | ① `order_intakes.copied_from_so_id BIGINT NULL FK sales_orders`(ORIGIN, 서비스가 "같은 바이어·CANCELLED SO" 검증) 추가 → `confirm_intake`가 `sales_orders.copied_from_id`로 복사(소유 D) ② A/A4의 PI·SO 참조 생성 요청 스키마에 선택 필드 `copied_from_id`(B의 COPY 검증 통과 시에만) 추가(소유 A) ③ QT·PO 생성 본문은 B8 그대로 |
| X-14 | 폴리모픽 전표 어휘 3중. A/A10·C/0-2 "`trade_docs.DOC_PREFIXES`(QT/PI/SO/PO)를 폴리모픽 코드로" ↔ B/B4 kernel `DocType(StrEnum)`(QT/PI/SO/PO) ↔ F/F13 `DocKind`(QUOTATION…, 풀네임)+C·D가 이미 저장하는 `SALES_ORDER` | **F/F13의 `DocKind` 단일**(풀네임 4값이 DB 저장 어휘). `DOC_PREFIXES: dict[DocKind,str]`(채번·표시 전용)·`DOC_TABLES: dict[DocKind,str]`. B의 `DocType`·`machine`의 'QT' 문자열 키는 `DocKind`로 통일(이벤트 payload의 `doc_type`도 `DocKind` 값). 소유=B(정의 위치 `trade_docs/constants.py`)·F(테스트) |
| X-15 | users FK 면제/분류 테스트 이중 + 명칭. C/C8 "승인 4테이블의 users FK 전건이 '행위 기록 면제 목록'에 등재" ↔ F/F10 `test_user_fk_classification`(전역, `USER_FK_CLASSIFICATION`) — F는 위임 표를 `approval_delegations`로 부름(C의 테이블명은 `delegations`) | **F10의 전역 테스트 하나로 흡수**(C8의 개별 면제 테스트 삭제). 테이블명은 C의 `delegations`. §2.1의 users FK 분류 표를 신규 24테이블 전량에 대해 PR-1에서 확정 |
| X-16 | `partner_has_open_documents`. A/A12(g)가 제공, F/F11은 "쓰지 않는다 — 삭제하거나 소비처 지정" 요청 | **삭제**(ADR-0041 죽은 코드). A/A1 모듈 표에서 제거 |
| X-17 | PI `advance_due_amount` 가정. B/B1(`derive_pi_status(due_amount)`·"컬럼은 A·E 소유, 가정명 `advance_due_amount`") ↔ A/A5 "선수금은 저장하지 않고 `split_advance()`"·E/0-1#7 "컬럼 미신설" | **컬럼 미신설**. `due = split_advance(PI.total, PI.advance_pct_bp).advance`를 호출자(E의 payments)가 계산해 `converge_payment_status(session, pi_id, received_total_amount, due_amount, actor_user_id)`에 인자로 전달(B1의 시그니처에 `due_amount` 추가). 소유=B |

### 1.2 상태·전이·도메인 규칙

| ID | 충돌·중복(인용) | 권장 해소안 1개 |
|---|---|---|
| X-18 | **QT "수주전환" 시점**. A/A4 "그 QT에서 파생된(직접 또는 PI 경유) **SO가 처음 확정되는 트랜잭션**에서만 CONVERTED — PI 생성·SO 접수만으로는 전환하지 않는다" ↔ B/B1 QT 엣지 4 "**후속 전표(PI 또는 SO) 첫 생성**과 같은 트랜잭션 ISSUED→CONVERTED, 불변식 CONVERTED⇔살아 있는 후속 ≥1" | **A의 의미(SO 확정)를 채택하고 B의 기계를 유지**: 엣지 6개·자동/사람 구분·총수(6,14)는 불변. `converge_parent`의 목표 상태 = "**살아 있는 확정 SO(`confirmed_at IS NOT NULL`, 직접 또는 PI 경유) ≥1 → CONVERTED / 아니면 ISSUED**(유효기간 경과 + 살아 있는 후속 0 → EXPIRED)". 호출 시점: SO 확정 트랜잭션·SO/PI 취소·만료. 부작용 보정: **QT도 살아 있는 후속(PI·SO)이 있으면 만료 스윕 대상에서 제외**(B7의 PI 규칙을 QT에 확장 — 후속이 부모를 붙잡는다), 후속 생성 가드(`is_lapsed`)는 그대로. 근거: PI는 오퍼 회신이라 "수주"가 아니고, §7.2 문면의 "수주전환"은 SO 단계. 소유=B(`converge_parent`·`expiry`), A/A4 문장 유지 |
| X-19 | 소비량 함수 이중. A/A4 `trade_chain.consumed_qty`(QT_LINE·PI_LINE 소비, 재구현 금지 스캔) ↔ B/B5 `LINE_CONSUMERS`(키 `SO_LINE`·`PO_LINE`, **S3-1 등록 0건**)·`open_quantity` | **단일 `open_quantity`/`LINE_CONSUMERS`로 통합**: 키를 `QT_LINE`·`PI_LINE`·`SO_LINE`·`PO_LINE` 4개로 확장하고 S3-1 등록 3건(QT_LINE←PI_LINE.qt_line_id, QT_LINE←SO_LINE.qt_line_id, PI_LINE←SO_LINE.pi_line_id). `consumed_qty`는 이 레지스트리 위의 얇은 별칭으로 삭제 가능. 잠금 규약: 참조 생성은 원천 헤더 `FOR UPDATE`→원천 라인 `FOR UPDATE id순`(A/A4·B/B3), 선적·입고 소비(S3-2·S4-1)는 B/B5의 헤더 `FOR SHARE`→라인 `FOR UPDATE`. 소유=B(계약)·A(등록 3건) |
| X-20 | **모듈 계층 순환**. A/A1 "L1 끼리 임포트 0, L1은 L2 임포트 0, `active_descendants`·`document_flow`는 L2" ↔ B/B3 `chain.py`(CHILD_LINKS·`has_live_children`·`converge_parent`·`lock_chain`)를 **kernel(L0)**에, `sales_orders.service.cancel_order`(L1)가 `converge_parent`(부모 QT 상태 갱신=QT 모델 필요)·`AllocationPort`·승인 무효화를 호출, `expiry.py`도 kernel — L0/L1이 L1/L2 모델을 요구 | 계층 재배치: **L0(`trade_docs`)=상수·믹스인·기계 표·`record_birth/record_transition`·FIELD_POLICY·`CHILD_LINKS` 레지스트리+`has_live_children`(테이블명 기반 Core 쿼리, 모델 무임포트)·`lock_document`·`open_quantity`·상태이력 4표 모델 / L1(`quotations`·`proforma_invoices`·`sales_orders`·`purchase_orders`)=모델·스키마·**CRUD·라인 편집**만 / L2(`trade_chain`)=**모든 전이 오케스트레이션**(issue·confirm·cancel·hold/resume·transitions 라우터, `converge_parent`, `lock_chain`, 참조 생성, 개정, 만료 스윕, `document_flow`)**. 그래서 B/B3의 `sales_orders.service.cancel_order`→`trade_chain.lifecycle.cancel_sales_order`. 단방향 DAG 전체는 §2.8. 소유=A(계층)·B(파일 이동) |
| X-21 | `converge_payment_status` 호출자. B/B1 "S3-1은 직접 호출 테스트로 검증, 호출자는 S3-3(명문화된 소비 계약)" ↔ E/E7 "S3-1 payments가 입금·역기록 시 호출" | **E 채택**(S3-1이 호출자). B/B1·B/D-B3의 "S3-3 소비" 문구 수정 |
| X-22 | 승인 무효화 진입점. B/B3 SO 취소 ④ `approvals.withdraw_open_for_subject(...)`("이미 결정된 APPROVED는 건드리지 않는다") ↔ C/C6 `void_for_target(...)`(취소·편집 시 REQUESTED **및 미소비 APPROVED**를 VOIDED로) — 이름·의미 상이(WITHDRAWN vs VOIDED, 시스템 통로 vs 사람 통로) | **C의 `void_for_target(session, *, approval_type, target_id, actor_user_id, reason_code)` 채택**. B의 취소 ④를 `void_for_target(reason_code='TARGET_CANCELLED')`로 수정(CONSUMED는 종결이라 손대지 않음). 소유=C(함수)·B(호출 순서) |
| X-23 | **라인 편집 API 모양**. A/A13(1) "라인 편집 API는 `/{doc}/{id}/lines/{line_id}` 형태만, 초안 편집은 제자리 UPDATE·삭제+재삽입 금지" ↔ B/B2 "**라인 개별 PATCH/DELETE 엔드포인트 없음**, 편집 가능 상태의 전체 교체 `replace_lines`만" | **A의 라인 단위 엔드포인트 채택**(참조 FK 보존용 안정 id, IDOR 헬퍼 `get_child_or_404`와 정합). B의 `replace_lines`→`add_line`·`update_line`·`remove_line` 3공개함수로 개명(모두 `assert_editable`→헤더 `FOR UPDATE`→version 대조→헤더 version bump→`recompute_total`), B/B9의 공개 함수 화이트리스트도 갱신. 각 라인 엔드포인트 응답에 갱신된 헤더 `version`·`total_amount`를 실어 연속 편집이 자기 자신에게 409를 내지 않게 한다. 소유=A(라우트)·B(함수) |
| X-24 | audit 사용. A/A4 "채번→INSERT→`audit`+outbox" ·F/F5(5) "audit detail `{doc_number, supplier_partner_id, line_count, po_kind}`" ↔ B/B6 "**전표 전이·생성은 audit_log에 이중 기록하지 않는다**(상태이력 4표+outbox가 정본), B는 AuditAction 상수를 추가하지 않는다" | **B 채택**: 4종 전표의 생성·전이·편집은 audit 미기록. F/F5의 audit 문구는 "만약 audit를 쓰는 경로가 생기면 이 화이트리스트만"으로 격하. audit는 권한·보안 성격(C의 승인 거부·결재선 변경, E의 입금·정책·한도 변경)만 |
| X-25 | 이벤트 payload 키. B/B6 `partner_id` ↔ A의 실제 열 `buyer_partner_id`·`supplier_partner_id` | payload 빌더가 `buyer_partner_id`/`supplier_partner_id`를 `partner_id`로 매핑(열 이름은 A 유지). 소유=B |

### 1.3 승인·게이트·확정 흐름 (C·D·E 접점)

| ID | 충돌·중복(인용) | 권장 해소안 1개 |
|---|---|---|
| X-26 | **PI 입금 게이트 의미**. D/D3·D4 표 `PI_DEPOSIT`: 모드 WARN=`WARN`(비차단·기록만), BLOCK=`BLOCK/OVERRIDE(ADMIN)`(별도 액션+해시) ↔ E/E6 WARN=**차단**(422 `PI_ADVANCE_BLOCKED`, 확정 본문에 `pi_gate_override_reason` 5~500자를 넣으면 통과), BLOCK=**우회 불가**(ADMIN 포함, 사유 무시) — 정반대 | **D의 게이트 공통 모델 채택 + E의 평가 로직 채택**: 평가기 `payments.pi_gate.evaluate_pi_advance`(E: 금액 대조·HALF_UP·등호 통과·`TT_ADVANCE`만 활성·PI 참조 SO는 PI 조건 기준·`PI_MISSING`/`SHORT`/`PI_NOT_USABLE`)를 gate_code=`PI_DEPOSIT`의 평가기로 등록. 모드 매핑: OFF→PASS(`reason=SKIPPED_OFF`, 증적에 스킵 기록), WARN→`WARN`(진행·기록), BLOCK→`BLOCK/OVERRIDE(ADMIN)`. **E의 확정 본문 `pi_gate_override_reason`·"BLOCK은 우회 불가"·outbox `trade_docs.sales_order.gate_overridden`은 삭제**(override는 D3의 별도 액션 하나, ADMIN은 어차피 정책 모드를 OFF로 바꿀 수 있어 우회 불가 주장이 실효가 없음). SO 열 `pi_gate_verdict` ∈ {PASS, NOT_APPLICABLE, WARN, OVERRIDDEN, SKIPPED_OFF}(VARCHAR(16)) |
| X-27 | CREDIT 평가 불능의 해소. D/D4 표 "평가 불능(환율 부재 통화 상이 등)=`UNKNOWN/APPROVAL`" ↔ E/E1·E4·C/C6 "**UNEVALUABLE은 승인 경로 없이 확정 거부** — 금액을 모르면 승인 상한(`basis_amount>0`)을 정할 수 없다" | **`UNKNOWN/NONE`**(E·C 채택). D의 `GATE_SPECS`·명세표 수정. D의 CREDIT 평가기는 E의 `credit.evaluation.evaluate_credit(session, locked_buyer, so)`를 감싸는 어댑터(`trade_chain/gate_evaluators.py`): EXCEEDED→`BLOCK/APPROVAL`, UNEVALUABLE→`UNKNOWN/NONE`, WITHIN·NOT_MANAGED→PASS. D/D4의 시그니처 `evaluate_credit(..., additional_exposure_amount, exclude_sales_order_id)`는 폐기 |
| X-28 | **확정 오케스트레이션 이중 정의**. D/D5 ②(1 claim→2 거래처 FOR UPDATE→SO 잠금→3 완결성→4 7종 재평가→5 승인 조회→6 미해소면 `gate_evaluations(BLOCKED)` 커밋 후 409→7 소비+전이) ↔ E/E4(①~⑨: PI 게이트→여신→`consume_approval` 항상 호출→BLOCKED만 커밋 후 raise, 나머지 거부는 **롤백 예외**·쓰기 0). 특히 "거부 시 증적 행을 남기는가"가 반대 | **§2.10의 통합 10단계 시퀀스**(소유=D `trade_chain/confirm.py`): 게이트 전건을 **쓰기 없이** 평가→clearance→미해소면 `gate_evaluations(BLOCKED)` 기록 후 **커밋하고 409**(idempotency 미완료)→해소면 `consume_approval`(BLOCKED면 VOID·감사 커밋 후 raise)→전이. E의 "PI→여신→소비" 순서는 "게이트 전건 평가가 소비보다 먼저"로 일반화됨 |
| X-29 | 승인 코어 호출 규약 불일치. D/0-3·D5 `approvals.find_consumable(session, subject_type, subject_id, content_rev)`(읽기)+`approvals.consume(session, approval, actor)`(원자 UPDATE) ↔ C/C6 `consume_approval(session, approval_type, target_id, actor_user_id) -> ConsumeResult`(단일 호출, digest 재검증·VOID·감사 포함) | C의 `consume_approval`이 정본. D가 "미해소 시도에서는 소비하지 않는다"를 지키려면 읽기 전용 사전 조회가 필요하므로 **C에 `approvals.service.peek_active_approved(session, *, approval_type, target_id) -> ApprovalRef | None`(읽기 전용: APPROVED·digest·통화·상한 일치 시에만 반환, 어떤 쓰기·VOID도 없음)를 추가**. 흐름: clearance는 peek로 `approval_available`을 정하고, 해소 확정 후에만 `consume_approval`(권위 있는 재검증, 경합 시 BLOCKED→커밋 후 raise). `content_rev` 인자 삭제(X-07). 소유=C |
| X-30 | 확정 실패 응답 코드. C/C4(f)·E/E4 "승인 없이 확정 → **422 `APPROVALS.APPROVAL.REQUIRED`**" ↔ D/D5·D7 "미해소 게이트 → **409 `TRADE_CHAIN.CONFIRM.GATE_BLOCKED`**(게이트별 level·resolution·reason)"+`TRADE_CHAIN.CONFIRM.APPROVAL_REQUIRED`(409) ↔ E의 `TRADE_DOCS.GATE.PI_ADVANCE_BLOCKED`(422)·`CREDIT_UNEVALUABLE`(422) | **미해소 게이트는 전부 `TRADE_CHAIN.CONFIRM.GATE_BLOCKED`(409) 하나**(본문 `blocked_gates[]`에 CREDIT·PI_DEPOSIT 등이 각자의 `reason_code`(예: `APPROVAL_REQUIRED`·`UNEVALUABLE`·`SHORT`·`PI_MISSING`)와 `resolution`으로 실림). 삭제: E의 `TRADE_DOCS.GATE.*` 2종, D의 `TRADE_CHAIN.CONFIRM.APPROVAL_REQUIRED`. `APPROVALS.APPROVAL.REQUIRED`(422)·`STALE`(409)는 **consume 시점 경합**(clearance 통과 후 승인 상태가 바뀜)에서만 발생. C의 H 테스트 ①("ADMIN 승인 없이 확정 → 422 REQUIRED")는 "→ 409 GATE_BLOCKED이며 CREDIT 항목이 `APPROVAL_REQUIRED`"로, 프런트(E/E10)의 "422 REQUIRED이면 '승인 요청' 버튼"은 "`blocked_gates`에 `resolution=APPROVAL`이고 승인이 없으면 버튼"으로 변경 |
| X-31 | 우회 시도 감사. C/C4·C6 `approvals.approval.bypass_blocked` audit는 `consume_approval` 3단계(승인 없음)에서 남기는 것으로 설계 → X-28의 흐름에서는 consume 이전(clearance)에서 막혀 **audit이 안 남는다** | 확정 오케스트레이션이 미해소 판정에서 CREDIT 항목이 "승인 필요·승인 없음"이면 `approvals.service.note_bypass_attempt(session, actor, target)`(C 제공, audit 기록 후 커밋)을 BLOCKED 기록과 같은 커밋에서 호출. 소유=C(함수)·D(호출) |
| X-32 | 여신 수치 가시성. D/D7 "여신 수치는 CREDIT 결과 basis에서 **TRADE·ADMIN에게만**(그 외 역할 응답에서 필드 부재)" ↔ E/E2 "**마스킹 비대상 유지**(ADR-0026 재판정 소진), SO 상세의 확정 증적은 전 역할이 본다" | **E2 채택**(D/D7이 "E 결과가 다르면 상수 1곳"이라고 유보). D7의 역할 제한 삭제, 대신 "여신 응답 스키마에 cost/margin 계열 필드 부재" 스캔(E2)을 D의 `GateOutcome.basis`에도 적용. B 판정서의 "원가·마진·**여신**·PO 금액 마스킹" 문구(B/B8 (e)2)에서 '여신' 삭제 |
| X-33 | 여신 조회 엔드포인트 중복. E/E5 `GET /sales-orders/{id}/credit-check`(TRADE·ADMIN, 잠금 없음, `advisory:true`) ↔ D/D4(6)·D7 `GET /sales-orders/{id}/gates`의 CREDIT("잠금 없는 참고값, `authoritative=false`") | **`credit-check` 삭제**. `/gates`가 CREDIT 항목의 `basis`에 `CreditEvaluation.to_snapshot()`+`pending_approval`+`advisory:true`를 싣는다(`evaluate_credit_unlocked` 사용). 프런트 `CreditEvaluationCard`는 `/gates` 응답 소비. AUTHZ 행 1개·엔드포인트 1개 감소 |
| X-34 | 확정 응답 스키마 3중. E/E10 `gates:{credit, pi_advance}` ↔ B/B5 `allocation`(NOT_IMPLEMENTED) ↔ D/D5 `ConfirmOutcome(confirmed, evaluation_id, report)` | 단일 `SalesOrderConfirmOut{sales_order, gates:[GateResultOut…], allocation, evaluation_id}`(200만 성공, 거부는 전부 4xx). 소유=D(스키마)·E(credit/pi 항목 내용)·B(allocation) |
| X-35 | 게이트 순서 표현 충돌. D/D-E 접점 "D 게이트→E 게이트" ↔ E/E4 "PI→여신→소비" | X-28의 "전건 평가(순서 무관, 쓰기 없음)→clearance→소비"로 무의미화. 평가 결과 표시 순서만 D/D4 명세표의 게이트 명단 순서(ITEM_MAPPING·DUPLICATE_PO·PRICE_DEVIATION·CREDIT·MARKET_READINESS·MOQ·PI_DEPOSIT) |
| X-36 | 게이트·확정 관련 열거값 이름. D의 gate_code `PI_DEPOSIT`·E의 정책 키 `pi_advance_gate_mode`·평가 함수 `evaluate_pi_advance`·D의 `evaluate_pi_deposit` | gate_code=`PI_DEPOSIT`(D의 DB CHECK 고정), 평가 함수=`evaluate_pi_advance`(E), 정책 키=`pi_advance_gate_mode`(E). D의 `evaluate_pi_deposit`은 삭제(어댑터 불필요 — 등록만) |

### 1.4 잠금·오류·플랫폼 공용

| ID | 충돌·중복(인용) | 권장 해소안 1개 |
|---|---|---|
| X-37 | **잠금 순서 4종 + 모드 충돌**. A/A13 거래처→QT→PI→SO→PO→라인→채번 / B/B3 동일 / C/C6 "(0)멱등→(1)대상(SO)→(2)직렬화 잠금(거래처)→(3)approvals" / E/E3 멱등→거래처→QT→PI→SO→approvals→라인→채번 / D/D5 인테이크 ⓪ 추가·"거래처 행 `FOR UPDATE`" / F/F3·F11 거래처 `FOR SHARE`(유형 검증 소비자) | **§2.9의 최종 `LOCK_ORDER`**: 멱등→인테이크→거래처→QT→PI→SO→PO→approvals→라인(id순)→채번. C6의 "SO→거래처" 순서 문구는 E/E3 요구대로 수정(승인 결정은 무잠금 조회로 SO·거래처 id를 얻어 거래처→SO→approvals 순). **거래처 잠금 모드**: 여신 직렬화 경로=`FOR NO KEY UPDATE`(E3), 유형·활성 검증 소비자(전표 생성·PO·자재·SKU 참조 검증)=**`FOR KEY SHARE`**(F의 `FOR SHARE`는 `NO KEY UPDATE`와 충돌해 E3 ⑤ "비블록 증명"을 깨므로 KEY SHARE로 약화 — 유형 해제 경로의 `FOR UPDATE`와는 여전히 충돌), 유형 해제=`FOR UPDATE`(임포트 기존). D5의 "FOR UPDATE" 문구는 `lock_buyer_for_credit`(NO KEY UPDATE)로 정정 |
| X-38 | 거래처 잠금 범위 과잉. E/E3 "참조 생성(1→상위 전표 잠금→채번)"·"입금·역기록은 `lock_chain(PI)`로 1→2→3" — 거래처를 모든 경로에서 잠금 | **순서표는 부분수열이 허용**된다: 거래처 잠금은 여신 직렬화가 필요한 경로(SO 확정·승인 요청/결정/소비·advisory 제외)에만 잡고, 참조 생성·편집·취소·입금·역기록은 자기 사슬(QT→PI→SO)만 잠근다(입금은 PI 행 잠금으로 확정과 직렬화됨 — E6(7)). `lock_chain(doc, *, with_partner=False)`. 순서 위반만 금지 |
| X-39 | **LOCK_BUSY 4중 정의·이름**. A/A13(4) `COMMON.CONCURRENCY.LOCK_BUSY`+57014 포함 ↔ B/B8(5) `CONCURRENCY.LOCK.BUSY`(55P03·40P01) ↔ C/X3(5) `COMMON.CONCURRENCY.LOCK_TIMEOUT` 후보+QueryCanceled ↔ E/E3(4) `COMMON.CONCURRENCY.LOCK_BUSY`, 57014 미매핑, 구현은 A13 PR 소유 | **`COMMON.CONCURRENCY.LOCK_BUSY`(409) 1건, 55P03+40P01만 매핑, 57014는 500 유지**(30초 초과 쿼리에 "다른 사용자가 처리 중" 문구는 거짓). **PR-2(플랫폼 기반)가 구현·소유**하고 A13·B8·C의 해당 항목은 이 PR을 참조만 한다. B의 `CONCURRENCY.LOCK.BUSY`·C의 `LOCK_TIMEOUT` 폐기, A13 서문·C X3의 57014 문구 삭제. 기존 코드와 접두가 맞는 이름(`COMMON.CONCURRENCY.*` — 실측) |
| X-40 | **에러코드 도메인 접두·중복**. A `TRADE.*`(12) / B `TRADE_DOCS.*`(11+LOCK) / C `APPROVALS.*`(12) / D `ORDER_INTAKE.*`·`GATES.*`·`TRADE_CHAIN.*`·`ORDER_BOARD.*`(19) / E `TRADE_DOCS.GATE.*`·`PAYMENTS.PAYMENT.*`·`POLICIES.POLICY.*`·`PARTNERS.CREDIT_LIMIT.*`(10). 같은 사건 중복: 후속 생존(A `HAS_ACTIVE_DESCENDANTS`=B `CANCEL.SUCCESSOR_ALIVE`), 원천 자격(A `REFERENCE.SOURCE_NOT_ELIGIBLE`=B `PARENT.NOT_USABLE`+`VALIDITY.EXPIRED`), 수량 초과(A `QTY_EXCEEDS_SOURCE`=B `QUANTITY.EXCEEDS_OPEN`), 바이어 PO 중복(A `DUPLICATE_BUYER_PO`=D `ORDER_INTAKE.PO.DUPLICATE`), 동결(A가 `TRADE.DOCUMENT.FROZEN`이라 부름=B `TRADE_DOCS.DOCUMENT.FROZEN`) | 도메인=**소유 모듈명 대문자**(기존 관용: IMPORTS·CERTIFICATIONS·…). `TRADE`는 실재 모듈이 아니므로 폐기 → `TRADE_DOCS`. 중복은 **B/D/E의 코드 하나로 통합**하고 A의 3종(`SOURCE_NOT_ELIGIBLE`·`QTY_EXCEEDS_SOURCE`·`HAS_ACTIVE_DESCENDANTS`)·D의 3종(`ORDER_INTAKE.PO.DUPLICATE`·`GATES.POLICY.INVALID_CONFIG`·`TRADE_CHAIN.CONFIRM.APPROVAL_REQUIRED`)·E의 2종(`TRADE_DOCS.GATE.*`)·B의 `LOCK.BUSY` 삭제. **최종 56종은 §2.4**. 문구는 `test_error_catalog` 규칙(≥10자·조치 힌트어) 준수 |
| X-41 | 본문 낙관 잠금 필드명. A/A4·D/D6·D/D7 `expected_version` ↔ B/B8 `version`·C/C8 `version`·E/E10 `version`·저장소 실측 관용 `version` | **`version`**(요청 본문·PATCH 전부). A·D의 스키마 문구 개명. 소유=A·D |
| X-42 | API 경로 접두. A/A4 `POST /v1/quotations/...` ↔ C/0.2 X-a "`/api/v1`이 정확(`app/api/router.py`가 한 곳에서 부착)" | 전부 `/api/v1` 접두를 라우터가 부착(경로 표기는 `/quotations/...`). A의 `/v1/`은 표기 오류 |
| X-43 | 금액 파서 사본. A/A9 `trade_docs.money_input.parse_minor_amount`(4번째 사본, 통합 리팩터링 범위 밖) ↔ C/C3·X3 "`core/money.py`에 공용 `parse_minor_amount` 추출 권고(3번째 사본 방지)" | **`core/money.py::parse_minor_amount(raw, currency, *, field, max_digits=15)`를 PR-2가 신설**하고 A·C·D·E의 신규 코드는 이것만 쓴다(`partners.parse_credit_limit`·`materials._minor_amount`·`pricing._to_money`의 소급 통합은 부채 유지) |
| X-44 | **사실 오류: forbid**. A/A8 "`PartnerCreate/Update` 스키마(`extra=forbid` 유지)"·A/A11 "SKU 등록·수정 스키마(`extra=forbid` 유지)" ↔ 실측: `PartnerCreateRequest`·`SkuCreateRequest`는 `BaseModel` 무설정(F/F17 동결 목록에 실제로 있음). 또 C/X3(6)이 "기존 모듈 소급 범위는 통합 판정"으로 넘김 | **F/F17 래칫 채택**(기존 32개 동결·신규 전건 forbid·단조 감소). S3-1이 **수정하는** 스키마(`PartnerCreateRequest`·`SkuCreateRequest`·partners/skus 관련 등록·수정 요청)는 F17 규칙 5("수정하는 세션이 forbid로 전환하고 동결 목록에서 삭제")에 따라 **PR-3에서 forbid로 전환**하고 프런트 회귀를 실측한다. B·C·D의 모듈별 forbid 스캔은 F17 전역 스캔으로 흡수 |
| X-45 | **JOB_REGISTRY 총수**. A/A14 8행 / B/B7 8행 / C/C7 8행(각자 "현 7+1") ↔ F/F18·부록 "B+1·C+1·F+2=11행" — A의 `trade-docs-totals-verify` 누락 | **12행**(7+5): `idempotency-purge` daily@04:20·`session-purge` daily@04:25(F)·`trade-docs-totals-verify` daily@05:30(A)·`document-expiry-sweep` daily@06:10(B)·`approval-stagnation-scan` daily@07:10(C). 시각 충돌 없음(기존 05:00·06:00·06:30·07:00·08:00·09:00 대비 실측). `test_registered_jobs_stay_clear_of_the_four_bans` 집합·`test_scheduler_registry`·`e2e/test_scheduled_jobs`의 총수는 **잡을 추가하는 PR마다** 그 PR에서 갱신(집합 동일성 단정이라 미갱신=CI 실패). CLI: B·C는 병행 서브커맨드, A는 함수 재사용(운영 검산 버튼 §21)이라 CLI 선택, F는 없음 |
| X-46 | 자동 확정 부재 테스트 4중. B `test_no_auto_confirm_code_path_exists`·D `test_no_auto_confirm_paths`(6항)·C 4중 스캔(`DECIDE_CALLERS`)·F PO 스캔·E "SO 확정 전역 확장" | **한 파일 `test_no_auto_confirm_code_path_exists.py`(소유 B)에 엔트리 등록 방식**으로 통합: 각 묶음이 (보호 함수, 허용 호출처 집합, 금지 임포트 모듈 집합)을 1행씩 등록(QT issue·SO confirm·PO create·intake confirm·approval decide/consume/void/request·bulk). 공회전 방지 자기검사는 프레임워크가 일괄 수행 |
| X-47 | 권한 매트릭스 누락·충돌. F/F8의 `EXPECTED` 표에는 `bank-accounts`(A/A8: 쓰기 ADMIN·조회 ADMIN/TRADE)·`payments`(E/E7)·`policies`(E/E8)·`approval-requests`·`credit-check`(E/E5)·`gate-overrides`(D/D3)·참조 생성·`preview`·`revisions`·`status-log`·`export.csv`·`GET /gate-policies`(D) 행이 없음. 완비성 테스트(F8(f)①)는 신규 모듈 경로 전건을 요구 | F/F8 표에 §2.7의 추가 행을 반영(역할 결정 포함). D/D7의 "GET gate-policies TRADE·ADMIN"은 X-12로 삭제. 소유=F |
| X-48 | **신규 모듈이 A/A1의 계층에 없음**. C `approvals`, D `gates`·`order_intake`·`order_board`, E `credit`·`payments`·`policies` — 임포트 방향 규칙 미정(예: `gates`는 도메인 무임포트, `order_intake`→`sales_orders`, `trade_chain`→`credit`·`payments`, `credit/spec.py`→`approvals`) | §2.8의 단일 DAG를 아키텍처 테스트(임포트 방향 스캔)로 고정. 소유=A(계층 테스트)·각 묶음(자기 모듈 등록) |
| X-49 | **마이그레이션 순서 의존**. B/B6 "approvals 마이그레이션이 `sales_order_status_log`보다 앞 리비전" ↔ C/X2 "승인 코어 PR은 SO 모델 뒤(소비 접점 스캔·TargetSpec)" ↔ E/E-3·D/M-D1·A의 SO create_table | **SO·상태이력을 approvals보다 먼저 만들고, `sales_order_status_log.approval_id`(FK)와 SO 증적 3열은 approvals 이후의 "확정 배선" 마이그레이션에서 ADD COLUMN**(표가 비어 있어 백필 없음). B/B6의 앞 리비전 요구는 이 방식으로 대체. 전체 DAG는 §2.11 |
| X-50 | `imports/registry.py`·`imports/service.py` **다중 PR 충돌 위험**. A/A8(partners `name_en`·`address_en` 어댑터)·A/A11(skus `moq` 어댑터)·E/E9(`ImportTarget.admin_only_fields`)·F/F11(`verify_references` 시그니처에 `target_id`)·D/D2(`_read_limited`·`_validate_extension` 공개 승격)가 같은 두 파일을 각자 수정 | **한 PR(PR-3 마스터 보강)에 전부 묶는다**(왕복 diff 테스트 갱신도 1회). 각 묶음 PR 계획서의 임포트 변경 항목은 PR-3을 가리킨다 |
| X-51 | ADR 제안 27건(A 8·B 6·C 3·D 4·E 3·F 3) 중 주제 중복·5줄 서식 부담 | **17건(0051~0067)으로 통합**(§4.2 매핑). 각 ADR은 결정·근거·기각한 대안·되돌리기 비용·영향 세션 5줄. 번호 충돌 없음(최신 0050) |
| X-52 | 프런트 검색형 선택 컴포넌트 소유. D/D6이 컴포넌트를 정의, F/F16이 "S3-1 전 신규 화면에 적용", A·C·E의 화면은 각자 자체 선택기를 상정 | 컴포넌트 `SearchSelect`는 **PR-3에서 신설**하고 이후 모든 PR의 화면이 사용. `size=200` 소스 스캔은 PR-3부터 신규 파일에 적용 |

### 1.5 누락(어느 묶음도 소유하지 않은 것)

| ID | 누락 | 권장 소유·해소 |
|---|---|---|
| G-01 | **QT·PI·SO·PO 핵심 화면(목록·상세·라인 편집·발행/확정/취소 다이얼로그·참조 생성 흐름·PO 미리보기)** — A는 프런트 절이 없고, B는 "전이 UI"만, D는 보드·인테이크·GatePanel, E는 ConfirmPanel 등 패널만 다룸. DoD "재입력 화면 없음"의 실체가 비어 있음 | PR-5~8이 각 전표의 화면을 소유(§3.3). 한국어 규약(break-keep·nowrap 헤더·가운데 숫자·`SearchSelect`·서버 문자열 금액·프런트 산술 0) 공통 |
| G-02 | `bank_accounts` 관리 화면(ADMIN) — A/A8은 API만 | PR-6에 포함 |
| G-03 | `document_flow` 화면(§14 ⑦)·상태이력 타임라인 컴포넌트(B/B6 `GET …/status-log`의 소비처) | PR-5에서 타임라인, PR-7에서 문서 흐름 패널 |
| G-04 | `gate_overrides`의 이벤트·감사 — E/E6은 WARN override에 audit+outbox를 두었으나 X-26으로 삭제되면서 override 부여·철회를 ADMIN이 알림 규칙으로 감시할 수단이 사라짐 | outbox `gates.override.granted`·`gates.override.revoked`(payload=id·gate_code·subject id뿐, 사유·금액 금지) 신설(PR-11). `gate_overrides` 자체가 불변 증적이라 audit 이중 기록은 두지 않음 **[PR-11a 구현 편차 2026-10-01: 지시 문면에 따라 audit도 남긴다 — detail=id·게이트·대상 id·라인·허용 역할뿐(사유·금액 없음). ADR-0069 ⑤(b)]** |
| G-05 | 알림 이동 매핑 — C/C7은 `entity_type='approvals'`→`/approvals`만 언급. A/A14의 검산 알림(`quotations` 등 4종), 신규 알림 entity_type 전건의 프런트 이동 매핑 필요 | PR-9에서 `alerts.tsx` 매핑을 일반화(entity_type→라우트 표)하고 PR-5/6/7/8이 자기 항목 추가 |
| G-06 | 테스트 팩토리·픽스처(역할별 사용자 5종·결재선·대결·BUYER 거래처+여신한도·SKU+판가·시장+준비도 요건) — C/X2가 "F 소유"로만 언급, F에는 항목 없음 | PR-2가 `tests/factories/trade.py` 골격을 만들고 각 PR이 확장(소유=F) |
| G-07 | DESIGN §14 화면 배정 문장 — C/X4는 승인함·대결, D는 오더 보드·인테이크, E는 정책·입금 패널·거래처 폼 | PR-1에서 §14에 화면 6개 일괄 배정(승인함·대결·결재선·인테이크·오더 보드·정책 설정·은행 계좌·전표 4종) |
| G-08 | `docs/testing.md` 테스트 그룹·CI 기준선 카운트 갱신(C/C8 "pytest·vitest 기준선 CI run 줄"), `concurrency` 마커 병렬 금지 규율 | 각 PR의 문서 커밋 + PR-16 최종 대사 |
| G-09 | L/C 플래그 행 공급 경로 — A/A5는 "S3-1 프로덕션에서 L/C 선택이 닫혀 있음"을 인정하고 S3-3 인계 | 오너 판정 후보 5번. 필요 시 CLI `set-feature-flag` 소단위(PR-16 이후) |
| G-10 | 프런트 상태 한글 라벨·색 상수 — 상태 값이 QT5·PI5·SO8·PO6+승인 6+인테이크 3+게이트 결과 4+이력 등 25종 이상인데 라벨 소유 미정 | 프런트 `lib/doc-status.ts` 단일 표(서버 미지 값은 '기타' — 기존 관용). 각 PR이 자기 상태 추가 |
| G-11 | SO 편집 API 계약 — A/A4는 "SO 편집 API(접수 상태 한정)"라고만, B/B2는 편집 스키마 2종(`…UpdateRequest`/`…MetaUpdateRequest`)만 언급 | PR-7에서 확정: `PATCH /sales-orders/{id}`(CONTENT 필드 화이트리스트: 결제조건·Incoterms·환율·`buyer_po_no/date`·`dest_market_code`(직접 SO만)·`internal_note`·담당자), 라인은 X-23의 라인 엔드포인트, `PATCH …/meta`(FREE 4). 참조 SO의 편집 제한은 A/A4 표 |
| G-12 | 게이트 BLOCKED 증적 누적(D-D9)과 보존 — 벌크 재시도가 `gate_evaluations`를 부풀림 | 부채 등재(P-30). 재판정 트리거: 시도 빈도 실측 |
| G-13 | 표시용 서버 계산 필드의 공통 규약 — A(`fx_rate_age_days`·`quantity_delta`·`price_changed`)·C(`can_decide`)·D(`age_days`·`deviation_pct`)·E(`advance_pct`) 등 프런트 산술 0 규칙(`money.test.ts` 가드) 준수 | 응답 스키마 리뷰 체크리스트 1줄(PR 템플릿)로 갈음. 서버가 문자열/정수를 모두 제공 |
| G-14 | 운영 개시 절차 문서(결재선 등록(임계 0·통화별)·제2 결재 계정·정책 설정·은행 계좌 등록·여신한도 입력(ADMIN)·L/C 닫힘·자사 레터헤드 없음) — C/X4·E/E8·A/A8이 각자 조각만 언급 | `docs/runbook/prod.md`에 "S3-1 운영 개시" 절 신설(PR-16) — §4.5 |

---

## 2. 통합 스키마·상수·계약

### 2.1 신규 테이블 24개 (최종)

**공통 규약**: 전 FK `ondelete=RESTRICT`·ORM `relationship` 없음(명시 쿼리)·금액 BIGINT+`CHAR(3)` 통화 쌍(손으로 선언, `money_columns` 미사용)·상태·유형 VARCHAR+`value_in` CHECK+StrEnum·부분 유니크는 `WHERE deleted_at IS NULL`(+술어 추가 시 직접 `Index`)·식별자 63자 이내·CHECK는 `create_table` 안에 `op.f()` 이름으로(alembic check가 못 보므로 `pg_get_constraintdef` 정의문 테스트로 고정)·마이그레이션 시드 0.

#### (a) 전표 헤더 공통 열 (QT·PI·SO·PO)

| 열 | 타입 | 규칙 |
|---|---|---|
| id | BIGINT identity PK | |
| doc_number | VARCHAR(20) NOT NULL | **전역 UNIQUE**+`ck_<t>_doc_number_format`(접두어 고정, X-01) |
| doc_date | DATE NOT NULL | 증빙일(KST 업무일), 미래 422·소급 허용 |
| status | VARCHAR(20) NOT NULL | `value_in`(B/B1 값), 상태 대입=`record_birth/record_transition` 1통로 |
| currency | CHAR(3) NOT NULL | 대문자 CHECK, `CURRENCY_MINOR_UNITS` 소속 서비스 선검사, `UNIQUE(id, currency)`(라인 복합 FK 대상) |
| total_amount \| **total_cost**(PO) | BIGINT NOT NULL DEFAULT 0 | 0..2^53−1, `recompute_total()`에서만 대입 |
| fx_rate / fx_rate_date | NUMERIC(18,8) / DATE | 1통화=x KRW, KRW는 1 고정, `fx_rate_date<=doc_date` |
| payment_type / advance_pct_bp / balance_anchor / balance_days | VARCHAR(12)/INT/VARCHAR(16)/INT | A/A5 형태 CHECK(`TT_ADVANCE·TT_DEFERRED·LC`, 앵커 6종, 음수 일수는 ETD 한정) |
| incoterm_code / incoterm_place / incoterm_year | VARCHAR(3)/VARCHAR(100)/SMALLINT | 12코드·{2010,2020}·all-or-none·DAT는 2010만/DPU는 2020만 |
| assignee_id | BIGINT FK users NOT NULL | 기본=생성 actor, handover 등록(X-06) |
| internal_note | VARCHAR(1000) NULL | FREE(X-02) |
| last_line_no | INT NOT NULL DEFAULT 0 | 라인 번호 카운터(결번 허용·재사용 금지) |
| **동결 시각** | QT·PI·PO=`frozen_at` TIMESTAMPTZ / SO=`confirmed_at` TIMESTAMPTZ | X-03. PI·PO는 `NOT NULL DEFAULT now()`, QT·SO는 NULL(편집 구간) |
| copied_from_id | BIGINT NULL 자기참조 FK | `<>id` CHECK+**4종 공통 부분 유니크 "살아 있는 복제본 1개"**(X-08) |
| Pk·Timestamp·SoftDelete·Version·Actor | | 헤더 5믹스인 |
| CHECK 공통 | | `frozen_complete`(동결 시각 NOT NULL이면 payment_type·incoterm_code·fx_rate 등 필수; 판매=buyer_name; QT·PI=valid_until·buyer_address; PO=supplier_name), 형태·범위 CHECK 전건 |

#### (b) 전표별 고유 열·유니크

| 테이블 | 고유 열 | 고유 CHECK·유니크·인덱스 | 믹스인 | 소유 |
|---|---|---|---|---|
| `quotations` | buyer_partner_id(CONTENT), buyer_name, dest_market_code(FK markets.code), buyer_address, valid_until | `status='DRAFT'`→`frozen_at IS NULL`, `ISSUED·CONVERTED`→NOT NULL; `status<>'DRAFT'`→`valid_until NOT NULL`; `valid_until>=doc_date`; 만료 후보 부분 인덱스 `(valid_until) WHERE status='ISSUED' AND deleted_at IS NULL` | 헤더 5 | A+B |
| `proforma_invoices` | qt_id NOT NULL, buyer_partner_id(ORIGIN), buyer_name, dest_market_code, buyer_address NOT NULL, valid_until NOT NULL, bank_account_id NOT NULL FK, 은행 스냅샷 6열(beneficiary_name/address·bank_name/address·bank_account_no·bank_swift_code, 전부 NOT NULL) | `UNIQUE(id, qt_id)`(SO 복합 FK 대상), 만료 후보 인덱스, `total_amount>0`(서비스) | 헤더 5 | A |
| `sales_orders` | buyer_partner_id(**ORIGIN 불변**), buyer_name, dest_market_code, qt_id NULL, pi_id NULL(복합 FK `(pi_id,qt_id)→PI(id,qt_id)`+CHECK `pi_id IS NULL OR qt_id IS NOT NULL`), buyer_po_no, buyer_po_no_key(VARCHAR(60)), **buyer_po_date DATE NULL**(D 요구), **confirmed_at**, (배선 마이그레이션에서 추가: credit_verdict VARCHAR(12) NULL, credit_approval_id FK approvals NULL, pi_gate_verdict VARCHAR(16) NULL) | 중복 바이어 PO `UNIQUE(buyer_partner_id, buyer_po_no_key) WHERE deleted_at IS NULL AND buyer_po_no_key IS NOT NULL AND status<>'CANCELLED'`; PI→SO 활성 1:1 `UNIQUE(pi_id) WHERE …status<>'CANCELLED'`; `confirmed_at` 결속 CHECK(B: RECEIVED→NULL, CONFIRMED·PARTIALLY_ALLOCATED·ALLOCATED·IN_SHIPMENT·COMPLETED→NOT NULL); 증적 CHECK(E: `(confirmed_at IS NULL)=(credit_verdict IS NULL)`·`=(pi_gate_verdict IS NULL)`, `(credit_verdict='APPROVED') IS TRUE = (credit_approval_id IS NOT NULL)`), `UNIQUE(credit_approval_id) WHERE NOT NULL`, 부분 인덱스 `ix_sales_orders_open_exposure(buyer_partner_id) WHERE confirmed_at IS NOT NULL AND deleted_at IS NULL AND status NOT IN ('COMPLETED','CANCELLED')`. **`content_rev`·`frozen_at` 없음** | 헤더 5 | A+B(+E 3열) |
| `purchase_orders` | supplier_partner_id(SUPPLIER∨OEM), supplier_name, **po_kind VARCHAR(16) NOT NULL DEFAULT 'PURCHASE'**(`PURCHASE·OEM_PRODUCTION`), oc_received_on DATE NULL, oc_reference VARCHAR(100) NULL | OC CHECK(B: ISSUED→둘 다 NULL, SUPPLIER_CONFIRMED·PARTIALLY_RECEIVED·FULLY_RECEIVED·CLOSED→`oc_received_on NOT NULL`), 서비스 경계 `doc_date<=oc_received_on<=today_kst()`(F2) | 헤더 5 | A+B+F |
| `bank_accounts` | label(unique_active), currency, beneficiary_name/address, bank_name/address, account_no(**유니크 키 금지**), swift_code(정규식 CHECK) 전부 NOT NULL | 쓰기 ADMIN·조회 ADMIN/TRADE, 초기 행은 화면/API로만 | 헤더 5 | A |

#### (c) 라인 4테이블 (`quotation_lines`·`proforma_invoice_lines`·`sales_order_lines`·`purchase_order_lines`)

공통: id, `qt_id|pi_id|so_id|po_id`(FK 열 이름은 짧게 — 유니크 인덱스 이름 63자 한도, A/0-1#4), **복합 FK `(hdr_id, currency)→header(id, currency)`**, currency CHAR(3), line_no(≥1, `unique_active(hdr,line_no)`), sku_id FK, sku_code(40)·sku_name_ko(200)·sku_name_en(200 NULL)·sku_kind(SINGLE|SET) 스냅샷, `quantity` INT 1..99,999,999, `unique_active(hdr, sku_id, is_free)`(판매) 또는 `(hdr, sku_id)`(PO). **믹스인: Pk·Timestamp·SoftDelete·Actor(Version 없음 — 헤더 version이 직렬화)**. **`note` 열 없음**(X-02).
- 판매 라인(QT·PI·SO): buyer_item_code(100), unit_price_amount, list_price_amount(NULL 허용, 생성 시 마스터 판가 스냅샷), line_amount(CHECK `quantity::numeric*unit_price_amount=line_amount`), price_basis(`MASTER·MANUAL·BUYER_PO`), is_free, price_reason(200), 무상 양방향 CHECK, 상한 2^53−1. PI 라인 `qt_line_id NOT NULL`, SO 라인 `qt_line_id`·`pi_line_id` NULL 허용+`num_nonnulls<=1`.
- PO 라인: unit_cost>0, line_cost(CHECK 곱), price_basis(`MASTER·MANUAL`). SO·PO 라인만 `requested_delivery_date`.

#### (d) 상태이력 4테이블 (B/B6) — IMMUTABLE

`quotation_status_log`·`proforma_invoice_status_log`·`sales_order_status_log`·`purchase_order_status_log`. PkMixin+Base만. 열: id, `<doc>_id` FK, occurred_at(server now), from_status NULL(=탄생), to_status, reason TEXT NULL, actor_user_id FK NULL, **automatic BOOLEAN NOT NULL**, approval_id(**SO 표만**, 배선 마이그레이션에서 ADD). CHECK 6종(from/to 값·자기전이 금지·탄생 행·사유 필수·사유 길이·`actor_user_id IS NOT NULL OR automatic`), 문서당 탄생 행 1개 부분 유니크, 인덱스 `(<doc>_id, id DESC)`. `REVOKE UPDATE, DELETE, TRUNCATE`.

#### (e) 승인 4테이블 (C/C1) — approvals 3종 MUTABLE(컬럼 UPDATE 권한)·events IMMUTABLE

- `approval_lines`(Pk+Timestamp+SoftDelete+Version+Actor): approval_type, threshold_amount/threshold_currency, approver_role(4값, VIEWER 제외), note(200). 수기 유니크 `(approval_type, threshold_currency, threshold_amount) WHERE deleted_at IS NULL` — **`ALLOWED_SENSITIVE_UNIQUE_KEYS`에 등록+ADR-0018 ㉠ 부기**. 컬럼 UPDATE 허용: approver_role·note·version·updated_at·updated_by_id·deleted_at.
- `approvals`(Pk+Timestamp+Version+Actor, **SoftDelete 없음**): approval_type, target_type(`SALES_ORDER`), target_id(FK 없음), target_label, status(6값), requested_by_id, basis_amount/currency(>0), snapshot_digest CHAR(64), snapshot JSONB(표시용), required_role, approval_line_id FK, decided_by_id·decided_on_behalf_of_id·decided_delegation_id·decided_at, consumed_at·consumed_by_id. 활성 유니크 `(approval_type, target_type, target_id) WHERE status IN ('REQUESTED','APPROVED')`. **스냅샷 컬럼은 컬럼 단위 UPDATE 권한으로 INSERT 이후 불변**(허용: status·decided_*·consumed_*·version·updated_*).
- `approval_events`(PkMixin+Base, IMMUTABLE): approval_id, occurred_at, from_status NULL, to_status, actor_user_id NOT NULL(시스템 행위자 없음), on_behalf_of_user_id, delegation_id, reason(≤1000), reason_code(VOIDED 전용 4값), `pair_allowed` CHECK(허용 7쌍).
- `delegations`(Pk+Timestamp+Version+Actor, SoftDelete 없음): delegator_user_id, delegate_user_id, approval_type, delegated_role, start_on, end_on, note, revoked_at, revoked_by_id. 컬럼 UPDATE 허용: revoked_at·revoked_by_id·version·updated_*.

#### (f) 인테이크·게이트·보드 (D) — `gate_policies` 삭제(X-12)

- `order_intakes`(Pk+Timestamp+SoftDelete+Version+Actor): source_kind(MANUAL|CSV), source_sha256, source_group_key, original_filename, extracted_snapshot JSONB(불변), buyer_partner_id(불변), buyer_po_no, buyer_po_no_key, buyer_po_date, currency(불변), dest_market_code, status(PENDING|CONFIRMED|REJECTED), assignee_id NOT NULL, last_line_no, reject_reason, decided_at·decided_by_id, sales_order_id(단방향 백링크), **copied_from_so_id NULL**(X-13). 부분 유니크 3종(PENDING 중복 PO·PENDING 파일 해시·SO 백링크), `UNIQUE(id, currency)`.
- `order_intake_lines`(Pk+Timestamp+SoftDelete+Actor): intake_id+currency 복합 FK, line_no, buyer_item_code, sku_id NULL(서버 해석 저장본), quantity, unit_price_amount(≥1), requested_delivery_date, source_row_no.
- `gate_evaluations`(Pk+Base, **IMMUTABLE**): subject_type(`SALES_ORDER`), subject_id, outcome(CONFIRMED|BLOCKED), results JSONB(비PASS 결과+passed_gates+사용 override/승인 ref+`policy_source`+E의 credit·pi_advance 스냅샷), **input_digest CHAR(64)**(X-07), evaluated_by_id, evaluated_at. `UNIQUE(subject_type, subject_id) WHERE outcome='CONFIRMED'`.
- `gate_overrides`(Pk+Base, **IMMUTABLE**): subject_type, subject_id, line_id NULL, gate_code(`PRICE_DEVIATION·MOQ·MARKET_READINESS·PI_DEPOSIT`만), action(GRANT|REVOKE), result_at_grant(BLOCK|UNKNOWN), reason(5~500자), basis JSONB, basis_hash CHAR(64), granted_by_id, authorized_role. 유효성=`(subject, gate, line, basis_hash)`별 최신 행이 GRANT.
- `board_saved_filters`(Pk+Timestamp+SoftDelete+Version+Actor): **user_id**(PERSONAL — 이름 주의: `owner_user_id` 금지), name(60), filter_config JSONB. `unique_active(user_id, name)`, 사용자당 20개.

#### (g) 입금·정책 (E)

- `payments`(PkMixin+명시 열, **IMMUTABLE**, Version·SoftDelete·Actor 없음): partner_id, pi_id NOT NULL, kind(RECEIPT|REVERSAL), received_amount(부호 있음)/received_currency, received_on, reference(100, 유니크 없음), reverses_payment_id, reason(300), recorded_by_id NOT NULL, created_at. `UNIQUE(id, pi_id, received_currency)`+복합 FK 역기록, `uq_payments_reverses_payment_id`, `payments_kind_sign` CHECK.
- `policy_settings`(Pk+Timestamp+SoftDelete+Version+Actor): policy_key(CHECK 폐쇄 2개), value_text VARCHAR(20), value_int INT, 값 형 CHECK, `unique_active(policy_key)`. 시드·CLI 금지.

#### (h) table_policy·handover·users FK 분류 (전량)

| 분류 | 테이블 |
|---|---|
| **MUTABLE_TABLES**(16) | bank_accounts, quotations, quotation_lines, proforma_invoices, proforma_invoice_lines, sales_orders, sales_order_lines, purchase_orders, purchase_order_lines, approval_lines, approvals, delegations, order_intakes, order_intake_lines, policy_settings, board_saved_filters |
| **IMMUTABLE_TABLES**(8, `revoke_mutations`) | quotation_status_log, proforma_invoice_status_log, sales_order_status_log, purchase_order_status_log, approval_events, gate_evaluations, gate_overrides, payments |
| **COLUMN_UPDATE_ALLOWLIST**(3, `restrict_update_columns`) | approvals, approval_lines, delegations |
| **`_NEVER_SEEDED`** | 신규 24전량(테스트가 열거형이라 누락=시드 허용) |
| **ASSIGNMENT_TARGETS**(5행, label=테이블명) | quotations, proforma_invoices, sales_orders, purchase_orders, order_intakes (`assignee_id`) |
| **USER_FK_CLASSIFICATION**(F10) | ASSIGNMENT: 위 5행 / HISTORY: 4 상태이력 `actor_user_id`·approvals `requested_by_id`·`decided_by_id`·`decided_on_behalf_of_id`·`consumed_by_id`·approval_events 전 FK·`order_intakes.decided_by_id`·gate_evaluations `evaluated_by_id`·gate_overrides `granted_by_id`·payments `recorded_by_id` / AUTH·PERSONAL: `board_saved_filters.user_id`(PERSONAL) / DELEGATION: `delegations.delegator_user_id`·`delegate_user_id`·`revoked_by_id` / AUDIT: ActorMixin 자동 |

### 2.2 기존 테이블 변경 (전량)

| 대상 | 변경 | 세트 |
|---|---|---|
| `partners` | `name_en VARCHAR(200) NULL`, `address_en VARCHAR(500) NULL` | DESIGN §4.6+ADR-0056, 마이그레이션 M01, `PartnerCreateRequest`(forbid 전환, X-44)·`PartnerView`, CSV 왕복 어댑터(열 끝 추가·열 부재 허용)+왕복 diff 테스트, 화면 |
| `skus` | `moq INT NULL CHECK (moq IS NULL OR moq>0)` | DESIGN §4.1+ADR-0056, M01, `SkuCreateRequest`(forbid 전환)·`SkuView`, CSV 어댑터, 화면 |
| `doc_number_seq` | 스키마 무변경 — `numbering/service.py:36` 연도 기준 UTC→KST 1줄 | ADR-0054, `test_numbering` 연도 경계 신규 |
| `scheduled_jobs` | 행 5개(앱 경로 등록, 마이그레이션 시드 금지) | §2.6 |
| 신규 테이블에 대한 사후 ALTER | 확정 배선 마이그레이션 M10: `sales_orders`(+증적 3열·CHECK·UNIQUE·인덱스), `sales_order_status_log`(+`approval_id` FK) | X-49 |
| 코드(스키마 아님) | `table_policy`(+표·`restrict_update_columns`·`COLUMN_UPDATE_ALLOWLIST`), `core/errors`(56코드+LOCK_BUSY 핸들러), `core/money.py`(파서), `core/logging/redaction.py`(`_account_no`·`_account_number` 접미), `identity`(`holders_of_role`·`list_active_user_names`·`users/lookup`·`purge_expired_sessions_all`), `idempotency.purge_expired_all`, `platform`(`is_feature_enabled`·JOB_REGISTRY 5행), `catalog.pricing.prices_at`, `readiness.cells_for`(읽기 전용), `partners`(`require_partner_of_any_type`·`find_type_release_blockers`·`resolve_buyer_items`·품번 삭제·`q/type`), `imports`(`admin_only_fields`·`verify_references(target_id)`·`_read_limited` 공개 승격), `handover/targets.py`(+5행·`USER_FK_CLASSIFICATION`), `app/registry.py`·`api/router.py`(모듈 12·라우터 다수), `app/cli.py`(+2) |

### 2.3 열거 상수 단일 출처

| 열거 | 단일 출처 | 값 | DB CHECK·소비 |
|---|---|---|---|
| DocKind | `trade_docs/constants.py` | QUOTATION·PROFORMA_INVOICE·SALES_ORDER·PURCHASE_ORDER | approvals.target_type·gate `subject_type`⊂DocKind(정의문 대조) |
| DOC_PREFIXES / DOC_TABLES | 〃 | QT·PI·SO·PO / 테이블명 | 채번·표시 전용, `doc_number` 형식 CHECK와 3자 일치 |
| FREEZE_COLUMN | 〃 | X-03 | frozen_complete·동결 판정 |
| QuotationStatus 5 / ProformaInvoiceStatus 5 / SalesOrderStatus 8 / PurchaseOrderStatus 6 | `trade_docs/machine.py` | B/B1 표 | 각 `ck_<t>_status_valid`·이력 CHECK. RESERVED(SO 4·PO 3)·DEAD_STATUSES=(CANCELLED, EXPIRED) |
| ColumnClass / FIELD_POLICY | `trade_docs/policy.py` | CONTENT·ORIGIN·FREE(4)·SYSTEM | 모델 반사 1:1 |
| PaymentType / BalanceAnchor / IncotermCode / IncotermYear / PriceBasis / PoKind | `trade_docs/constants.py` | A/A5·A6·A9, F/F3 | 각 CHECK |
| ApprovalType(1) / TargetType / ApprovalStatus(6) / ApproverRole(4) / DecisionVerb(3) / VoidReasonCode(4) / ConsumeOutcome(3) / Authority.kind | `approvals/models.py`·`machine.py` | C/C1~C8 | approvals·events·lines·delegations CHECK 자동 대사 |
| GateLevel(4) / GateResolution(3) / gate_code(7) / OVERRIDE_ROLES / GATE_SPECS | `gates/types.py`·`policy.py` | D/D3·D4(X-27 반영) | gate_overrides CHECK(가능 4종) |
| IntakeSourceKind(2) / IntakeStatus(3) | `order_intake/` | D/D1 | order_intakes CHECK |
| POLICY_REGISTRY 키 2 | `policies/registry.py` | pi_advance_gate_mode(OFF·WARN·BLOCK)·price_deviation_tolerance_bp(0..10000) | policy_settings CHECK 1:1 |
| CreditVerdict(계산 4)/저장 3 · ReasonCode 3 | `credit/` | E/E1·E4 | `sales_orders.credit_verdict` |
| pi_gate_verdict | `payments/pi_gate.py` | PASS·NOT_APPLICABLE·WARN·OVERRIDDEN·SKIPPED_OFF | `sales_orders.pi_gate_verdict` CHECK |
| PaymentKind | `payments/` | RECEIPT·REVERSAL | payments CHECK |
| BOARD_STAGES / BulkAction / BulkOutcome | `order_board/` | D/D6 | `BOARD_STAGE_STATUSES`⊇(SO 상태−CANCELLED−RESERVED) 완전성 테스트 |
| USER_FK_CLASSIFICATION 분류 | `handover/targets.py` | ASSIGNMENT·HISTORY·AUTH·PERSONAL·DELEGATION | F/F10 |

### 2.4 에러코드 신규 56종 (최종)

- **`TRADE_DOCS`(19)**: DOCUMENT.INCOMPLETE(422)·DOCUMENT.TOTAL_MISMATCH(409)·DOCUMENT.DUPLICATE_BUYER_PO(409, 인테이크 PENDING·SO 공용, detail=점유 문서번호·상태)·DOCUMENT.FROZEN(409)·REFERENCE.ALREADY_CONVERTED(409)·LINE.SKU_DISCONTINUED(422)·LINE.SKU_DUPLICATE(409)·LINE.AMOUNT_OUT_OF_RANGE(422)·PAYMENT.LC_DISABLED(422)·TRANSITION.NOT_ALLOWED(409)·TRANSITION.REASON_REQUIRED(422)·RESUME.TARGET_MISMATCH(409)·COPY.SOURCE_NOT_ELIGIBLE(409)·CANCEL.SUCCESSOR_ALIVE(409, 개정 발행 거부 포함)·PARENT.NOT_USABLE(409, 원천 자격)·VALIDITY.EXPIRED(422)·QUANTITY.EXCEEDS_OPEN(409, 참조 생성·선적·입고 공용)·QUANTITY.DOCUMENT_NOT_CONSUMABLE(409)·PAYMENT.PI_NOT_OPEN(409)
- **`COMMON`(1)**: CONCURRENCY.LOCK_BUSY(409)
- **`APPROVALS`(12)**: LINE.NOT_CONFIGURED·LINE.DUPLICATE·APPROVAL.NO_ELIGIBLE_APPROVER·APPROVAL.ALREADY_ACTIVE·APPROVAL.REQUIRED·APPROVAL.STALE·TRANSITION.NOT_ALLOWED·TRANSITION.REASON_REQUIRED·DECISION.NOT_APPROVER·DECISION.SELF_APPROVAL·DELEGATION.OVERLAP·DELEGATION.NOT_ACTIVE
- **`ORDER_INTAKE`(9)**: STATE.NOT_PENDING·LINE.UNMAPPED_ITEMS·LINE.STALE_MAPPING·LINE.DUPLICATE_SKU·LINE.LIMIT_EXCEEDED·FILE.DUPLICATE·FILE.INVALID_ROWS·FILE.TOO_MANY_GROUPS·FILE.UNSUPPORTED_FORMAT
- **`GATES`(3)**: OVERRIDE.NOT_ALLOWED(403)·OVERRIDE.NOT_APPLICABLE(422)·OVERRIDE.STALE(409)
- **`TRADE_CHAIN`(1)**: CONFIRM.GATE_BLOCKED(409)
- **`ORDER_BOARD`(3)**: BULK.TOO_MANY·FILTER.LIMIT_REACHED·FILTER.DUPLICATE_NAME
- **`PAYMENTS`(5)**: PAYMENT.CURRENCY_MISMATCH·EXCEEDS_DUE·PI_NOT_ADVANCE·ALREADY_REVERSED·NOT_REVERSIBLE
- **`POLICIES`(2)**: POLICY.UNKNOWN_KEY(404)·POLICY.INVALID_VALUE(422)
- **`PARTNERS`(1)**: CREDIT_LIMIT.ADMIN_ONLY(403)
- 재사용(신규 0): `VALIDATION_INVALID_FIELD`·`CATALOG.PRICE.NOT_EFFECTIVE`·`COMMON.CONCURRENCY.VERSION_CONFLICT`·`COMMON.IDEMPOTENCY.KEY_CONFLICT/KEY_REQUIRED`·`IMPORTS.*`·`IMPORTS.CONFIRM.VERSION_CONFLICT`(유형 해제 차단 문제 행).
- 카탈로그 전수 테스트(3세그먼트·문구 ≥10자+조치 힌트어)는 PR마다 자기 코드를 추가하며 통과해야 한다. 금액·계좌·사유 원문은 detail·로그에 넣지 않는다(G-13과 동일 규율).

### 2.5 이벤트·audit

- **outbox 이벤트**: `quotations.quotation.created|status_changed`, `proforma_invoices.proforma_invoice.created|status_changed`, `sales_orders.sales_order.created|status_changed`, `purchase_orders.purchase_order.created|status_changed`(payload 화이트리스트 `doc_type(DocKind)·doc_id·doc_number·from_status·to_status·automatic·assignee_id·partner_id`, 금액·사유 금지) / `approvals.approval.requested|approved|rejected|withdrawn|voided|consumed`(6, id·유형·대상·from/to·행위자만) / `order_intakes.order_intake.created|status_changed` / `payments.payment.recorded|reversed|reversed_after_confirm`(E는 `reversed`를 명명하지 않아 통합에서 확정, id만) / `policies.policy.changed` / `gates.override.granted|revoked`(G-04 신설). alert_rules event_type `approvals.stagnation`(config.days).
- **audit 액션**: `approvals.line.created|updated|deleted`·`approvals.delegation.created|revoked`·`approvals.decision.denied`·`approvals.approval.bypass_blocked`(C) / `payments.receipt.recorded`·`payments.receipt.reversed`(E) / `policy.update`(E) / `partners.credit_limit.set|changed`(E9). **4종 전표의 생성·전이·편집은 audit 없음**(X-24).

### 2.6 JOB_REGISTRY 12행 (변경 5행)

| code | schedule | 소유 | 4금 논증(ADR-0058) |
|---|---|---|---|
| idempotency-purge | daily@04:20 | F | TTL 기술 행 삭제만(원장·전표 무관) |
| session-purge | daily@04:25 | F | 〃 |
| trade-docs-totals-verify | daily@05:30 | A | 읽기+ADMIN 인앱 알림, 자동 보정 없음 |
| document-expiry-sweep | daily@06:10 | B | (QT,ISSUED→EXPIRED)·(PI,ISSUED→EXPIRED) 두 엣지뿐, 발주·SO 무접촉, 후속 보유 QT·PI 제외(X-18) |
| approval-stagnation-scan | daily@07:10 | C | 알림만, 승인 상태 불변 |

기존 7행: certification-sweep 06:00 · outbox-dispatch interval@1 · deadline-scan 06:30 · daily-briefing 09:00 · stagnation-scan 07:00 · storage-monitor 05:00 · backup-freshness 08:00.

### 2.7 F/F8 `AUTHZ_MATRIX` 추가 행 (X-47 — 역할 결정 포함)

| 경로(신규) | ADMIN | TRADE | LOGISTICS | CERT | VIEWER |
|---|---|---|---|---|---|
| `bank-accounts` 조회 / 쓰기 | R·W | R | 403 | 403 | 403 |
| `POST /quotations/{id}/proforma-invoices(/preview)`·`…/sales-orders`·`…/revisions`(참조 생성·개정) | W | W | 403 | 403 | 403 |
| `POST /purchase-orders/preview` | W | W | 403 | 403 | 403 |
| `GET …/status-log`·`…/export.csv`(QT·PI·SO) | R | R | R | R | R |
| PO `export.csv` | R(원가 열 포함) | 〃 | 〃 | 〃 | R(원가 열 없음) |
| `POST /proforma-invoices/{id}/payments`·`POST /payments/{id}/reversal` | W | W | 403 | 403 | 403 |
| `GET /proforma-invoices/{id}/payments` | R | R | R | R | R |
| `GET /policies`·`PUT /policies/{key}` | R·W | 403 | 403 | 403 | 403 |
| `POST /sales-orders/{id}/approval-requests` | W | W | 403 | 403 | 403 |
| `GET /sales-orders/{id}/gates`(CREDIT 참고값 포함, X-33) | R | R | R | R | R |
| `POST /sales-orders/{id}/gate-overrides(/revoke)` | 서비스 판정(SERVICE_GATED): 가격·MOQ=TRADE·ADMIN, 준비도·PI=ADMIN, 라우트 상한 TRADE·ADMIN | | 403 | 403 | 403 |
| `POST /sales-orders/{id}/confirm`·`…/transitions`·`…/lines…`·`…/meta` | W C X | W C X | 403 | 403 | 403 |
| `GET /users/lookup` | R | R | 403 | 403 | 403 |
| `DELETE /partners/{id}/item-codes/{id}` | 허용 | 허용 | 403 | 403 | 403 |

### 2.8 모듈 계층 (단일 DAG — 임포트 방향 아키텍처 테스트로 고정)

```
platform(도메인 무임포트): core · identity · idempotency · numbering · notifications · outbox · audit
                           approvals(TargetSpec 레지스트리) · gates(평가기 등록부) · policies
L0 trade_docs      : constants · mixins · machine(기계 표) · transition(record_birth/record_transition) · policy(FIELD_POLICY)
                     locking(lock_document·LOCK_ORDER) · chain(CHILD_LINKS 레지스트리·has_live_children, 테이블명 기반) · quantities(open_quantity)
                     payment_terms · incoterms · fx · snapshot · lines(require_sellable_sku) · verify · 상태이력 4모델
L1 (CRUD·라인 편집만): quotations · proforma_invoices · sales_orders(+AllocationPort) · purchase_orders · bank_accounts
L2 : trade_chain(전이 오케스트레이션·확정·취소·참조 생성·개정·만료·converge_parent·document_flow·라우터)
     credit(평가·provider·잠금·TargetSpec)  payments(입금·PI 게이트)  order_intake  order_board
허용 간선: L1→L0·platform / L2 내부는 trade_chain→{credit, payments, gates, policies, approvals} · order_board→{trade_chain, order_intake} · order_intake→{L1 sales_orders, gates, partners} · credit→{L1 sales_orders, approvals} · payments→{L1 proforma_invoices, policies}
금지: L0→L1·L2 / L1→L1·L2 / gates·approvals·policies→도메인 / credit·payments·order_intake→trade_chain / handover→도메인(허용 방향은 handover→도메인)
```
TargetSpec 소비 접점 상수(C/X2·C8): `consume_approval` 임포트 허용 = `trade_chain.confirm` / `request_approval` = `trade_chain.approval_requests` / `void_for_target` = `trade_chain.lifecycle`·`sales_orders.service`(편집) / `decide_approval` = `approvals/router.py` 1곳.

### 2.9 전역 잠금 순서 (`LOCK_ORDER`, 변경은 ADR)

`(0) 멱등 claim 행 → (1) order_intakes → (2) partners(바이어) → (3) QT → (4) PI → (5) SO → (6) PO → (7) approvals → (8) 라인(id 오름차순) → (9) doc_number_seq(항상 마지막)`
- 부분수열 허용(순서만 지키면 건너뛸 수 있음). 한 트랜잭션은 거래처 행을 최대 1개. 잠금 전 무잠금 조회로 부모 id를 얻고 잠근 뒤 재확인(SO 거래처는 ORIGIN 불변이라 안전).
- 거래처 잠금 모드: 여신 직렬화 경로(확정·승인 요청/결정/소비)=`FOR NO KEY UPDATE`(`lock_buyer_for_credit`, `populate_existing`); 유형·활성 검증 소비자=`FOR KEY SHARE`; 유형 해제(임포트)=`FOR UPDATE`.
- 경로별: 확정 0→2→3→4→5→7→8→9 / 승인 결정·소비·무효 0→2→5→7 / 입금·역기록 0→3→4(+PI 게이트 직렬화) / SO 편집·취소 0→3→4→5→7→8 / 참조 생성 0→(상위 전표)→8→9 / 인테이크 확정 0→1→(거래처 KEY SHARE)→9.
- 55P03·40P01 → 409 `COMMON.CONCURRENCY.LOCK_BUSY`(같은 트랜잭션의 멱등 claim도 롤백되어 같은 키 재시도 안전).

### 2.10 SO 확정 통합 시퀀스 (`trade_chain/confirm.py`, 사람 1클릭·`Idempotency-Key` 필수) — X-28의 해소

1. `idempotency.claim`(지문 `{so_id, version}`)
2. 무잠금으로 SO의 `buyer_partner_id`·`pi_id`·`qt_id` 조회
3. `lock_buyer_for_credit` → `lock_chain`(QT→PI) → SO `FOR UPDATE`+`version` 대조(409)+상태=RECEIVED·거래처 재확인
4. 입력 완결성 사전검사(422, 롤백): `frozen_complete`(결제조건·Incoterms·환율·buyer_name) → 500이 아니라 `DOCUMENT.INCOMPLETE`
5. 게이트 7종 **쓰기 없이** 전건 평가(`gates.evaluate_all`; CREDIT=`credit` 어댑터, PI_DEPOSIT=`payments.evaluate_pi_advance`+정책, PRICE_DEVIATION=정책+`list_price_amount`, MARKET=`cells_for`, MOQ=`skus.moq`, DUPLICATE_PO·ITEM_MAPPING). 미등록·예외=UNKNOWN(55P03·40P01은 전파)
6. `clearance`: 유효 override(`gate_overrides`)+`approvals.peek_active_approved`(읽기)로 미해소 판정
7. **미해소면**: (CREDIT이 "승인 필요·승인 없음"이면 `note_bypass_attempt`) → `gate_evaluations(BLOCKED, results, input_digest)` INSERT → **예외 없이 커밋**(idempotency `complete` 안 함) → 409 `GATE_BLOCKED`(게이트별 level·resolution·reason_code, 판매가·수량 값만)
8. **해소면**: `consume_approval`(항상 호출 — NOT_REQUIRED 처리 포함, BLOCKED면 VOID·감사 커밋 후 raise `STALE`/`REQUIRED`)
9. `record_transition(RECEIVED→CONFIRMED, approval_id)`(confirmed_at·증적 3열 같은 flush)+`converge_parent`(QT CONVERTED)+`AllocationPort.on_confirmed`(예외=전체 롤백)+`gate_evaluations(CONFIRMED)`
10. `idempotency.complete(200, SalesOrderConfirmOut{sales_order, gates[], allocation, evaluation_id})`

승인은 미해소 시도에서 소비되지 않는다. 승인 요청은 별도 사람 동작(`POST …/approval-requests`, 확정 시도가 승인을 만들지 않는다). ADMIN 분기 없음(`force·override·bypass` 인자 부재).

### 2.11 마이그레이션 DAG (12건)

| # | 내용 | 선행 | PR |
|---|---|---|---|
| M01 | partners(+name_en·address_en)·skus(+moq)+CHECK | — | PR-3 |
| M02 | policy_settings | — | PR-4 |
| M03 | quotations·quotation_lines·quotation_status_log(+REVOKE) | M01(마스터) | PR-5 |
| M04 | bank_accounts·proforma_invoices·proforma_invoice_lines·proforma_invoice_status_log(+REVOKE) | M03 | PR-6 |
| M05 | sales_orders·sales_order_lines·sales_order_status_log(approval_id 없음, +REVOKE) | M04 | PR-7 |
| M06 | purchase_orders·purchase_order_lines·purchase_order_status_log(+REVOKE) | M01 | PR-8 |
| M07 | approvals 4테이블(+`restrict_update_columns`·approval_events REVOKE) | — | PR-9 |
| M08 | payments(+REVOKE) | M04 | PR-10 |
| M09 | gate_evaluations·gate_overrides(+REVOKE ×2) | M05 | PR-11 |
| M10 | **확정 배선 ALTER**: sales_orders(+증적 3열·CHECK·UNIQUE·open_exposure 인덱스)·sales_order_status_log(+approval_id FK) | M05·M07 | PR-12 |
| M11 | order_intakes·order_intake_lines | M05 | PR-13 |
| M12 | board_saved_filters | — | PR-15 |

각 리비전: `upgrade→downgrade→upgrade` 왕복·`alembic check` 드리프트 0·식별자 63자·CHECK 수기 이름·시드 0·downgrade는 `drop_table`(REVOKE는 소멸). 병합 순서가 곧 리비전 체인 순서이므로 PR 병합 순서를 위 선행 관계에 맞춘다.

---

## 3. PR 분할 제안 (2안 + 추천 1안)

**공통 규율**: 작은 단위·즉시 실행·테스트(CLAUDE.md). 각 PR은 (1) 그 자체로 동작·테스트 통과 (2) 마이그레이션은 additive·왕복·`alembic check` 0 (3) 화면 4요소(API+화면+권한+테스트) 충족 (4) §22 11렌즈 체크·해당 그룹(A~K) 케이스 추가 (5) 병합은 CLAUDE.md 게이트(head SHA 전 체크런 success+mergeable clean — 클라우드 세션은 API squash 병합 후 최신 main 재시작, 로컬은 `scripts/merge-pr.sh`; 웹 Merge 버튼 금지) (6) **완료 보고는 릴레이 평문 본문에 DDL 전문·12자리 해시를 직접 포함**(PR 본문·링크 제시만으로는 미충족 — 2026-08-12 판정). alembic 헤드는 1개여야 하므로 **PR은 §2.11 선행 관계대로 직렬 병합**한다(병렬 개발은 가능하나 병합 직전에 리비전을 최신 main 뒤로 재정렬).

### 3.1 안 1 — 수직 슬라이스(전표 단위 백엔드+프런트) 16 PR

| PR | 범위 | 마이그레이션 | 프런트 | 검증·DoD 배치 |
|---|---|---|---|---|
| **PR-1** | 문서 전용: 통합 계획 최종본(`docs/plans/s3-1-plan.md`)+DESIGN 보강 전건(§4.1)+ADR 0051~0067+기존 ADR 부기 10건+WBS v1.5+GC v1.4+PROGRESS 등재(§4.6) — "계획 세션→PR-1 이어쓰기"(CLAUDE.md 표준 경로, 착수 블록 A항 세션 확인 가드 필수) | 없음 | 없음 | 문서 린트(ADR 5줄·WBS v1.5 행·PROGRESS 문면) |
| **PR-2** | 플랫폼 기반: `COMMON.CONCURRENCY.LOCK_BUSY` 핸들러(55P03·40P01)·채번 KST 연도(1줄)+연도 경계 테스트·`core/money.parse_minor_amount`·`restrict_update_columns`+`COLUMN_UPDATE_ALLOWLIST`+권한 실측 테스트(임시 테이블)·redaction 접미(`_account_no`·`_account_number`)·`identity.holders_of_role`·`list_active_user_names`+`GET /users/lookup`·`is_feature_enabled`(fail-closed)·**테스트 프레임워크**(forbid 래칫 `LEGACY_FORBID_EXEMPT`·`test_user_fk_classification`+기존 users FK 소급 분류·`AUTHZ_MATRIX` 골격·`no_auto_confirm` 등록 프레임·임포트 방향 스캔 골격·`tests/factories/trade.py`) | 없음 | 없음 | K(아키텍처 골격), J(핸들러 단위·55P03 실접촉 1건·채번 KST 경계) |
| **PR-3** | 마스터 보강·읽기 확장(X-50 단일 PR): partners `name_en`·`address_en`·skus `moq`·CSV 왕복 어댑터·`PartnerCreateRequest`/`SkuCreateRequest` forbid 전환·E9(여신한도 변경 ADMIN, `admin_only_fields`)·F11(유형 해제 차단 `verify_references(target_id)`)·F12(품번 삭제)·`GET /partners?q&type`·`GET /skus?q`·`prices_at`·`readiness.cells_for`·`partners.resolve_buyer_items`·`require_partner_of_any_type`·`imports` 공개 승격 | M01 | 거래처·SKU 필드, **`SearchSelect` 공용 컴포넌트**, 품번 삭제 버튼, 여신 입력 비활성 | A(마스터 원천), F(CSV 왕복 diff), K(forbid 래칫 목록 단조 감소), G(redaction) |
| **PR-4** | 정책 저장소: `policy_settings`+`POLICY_REGISTRY`+`get_policy`+ADMIN `GET/PUT /policies`(사유·audit·outbox) | M02 | `/settings/policies`(미설정 적색 배지·%↔bp) | A/H(미설정=BLOCK·source), J(동시 최초 생성 409), K(키 1:1 대사) |
| **PR-5** | 커널+QT: `trade_docs` L0 전체·상태이력 4모델·QT L1(CRUD·라인 3함수)·`trade_chain` 골격(lifecycle: issue·cancel·transitions·revisions, `CHILD_LINKS`·`converge_parent`·`lock_chain`)·`FIELD_POLICY`·handover 등록·QT `export.csv`·`trade-docs-totals-verify` 잡+검산 함수(+4금 집합 갱신) | M03 | QT 목록·상세·라인 편집·발행·취소·개정·**상태 타임라인 컴포넌트** | A(QT 전이 20쌍·동결 불변·개정 원자성), J(채번 동시 100·발행 더블클릭·발행 vs 편집), K(machine 총수·상태 통로 스캔·FIELD_POLICY 완전성) |
| **PR-6** | PI+은행계좌+참조 생성 1단(QT→PI, `preview` 비저장)+`open_quantity`/`LINE_CONSUMERS` 등록 3건+`converge_payment_status`(테스트 호출)+`document-expiry-sweep` 잡(QT·PI, 후속 보유 제외)+PI/은행 `export.csv` | M04 | 은행계좌(ADMIN) 화면, PI 미리보기·생성·상세, QT→PI 흐름 | A(소비·환원·만료 경계·유효기간 직접 검사), J(잔량 초과 동시 PI 생성=1건), H(만료 스윕 정합·건별 TX), K |
| **PR-7** | SO 접수+참조 생성 2단(QT/PI→SO)+SO CRUD·라인·`create_received_sales_order`(L1 단일 착지)+보류·재개·취소(역순 취소 가드·`converge_parent`·`AllocationPort` NOT_IMPLEMENTED)+SO `export.csv`+`document_flow` | M05 | SO 목록·상세·편집·참조 생성 흐름·문서 흐름 패널 — **DoD ① QT→PI→SO 관통(재입력 화면 없음) e2e** | A(**중복 바이어 PO 0건**·취소 후 재사용·PI→SO 1:1·역순 취소 잔액 복원), J(취소 vs 후속 생성 20회·교차 잠금 데드락 0), K(FK 스캔·`buyer_po_no_key` 정규화) |
| **PR-8** | PO: preview·생성(=발행, 사람 1클릭)·transitions(OC·취소)·meta·`CostHidden` 스키마 2종·CSV(역할별 헤더 2종)·`po_kind`·공급사 유형 검증(`FOR KEY SHARE`) | M06 | PO 발주 2단 화면(입력+미리보기→확정), 목록·상세(VIEWER는 원가 열 없음) | G(**PO 원가 9채널 마스킹**), A(PO 전이 30쌍·OC 경계), I(PO 자동 경로 부재), K |
| **PR-9** | 승인 코어+여신 평가: approvals 4테이블·`request_approval`·`decide_approval`·`consume_approval`·`peek_active_approved`·`void_for_target`·`note_bypass_attempt`·결재선/대결 API·알림·`approval-stagnation-scan` 잡·`credit` 모듈(평가·미수 provider·`lock_buyer_for_credit`·`TargetSpec` 등록). **`POST /sales-orders/{id}/approval-requests`와 소비 접점 스캔(`consume_approval(`)·`void_for_target` 훅은 PR-12에 함께 둔다**(요청·소비·무효 훅이 한 PR 안에서 닫히도록 — C/X2, 한 PR 창 동안 스캔 부재를 PROGRESS에 기록) | M07 | `/approvals`(결재함·내 요청)·`/approval-lines`·`/delegations`·셸 배지·`alerts` 이동 매핑 일반화 | H(대결 기간+이력 KST 경계·정체 독촉·SoD 4경로), J(동시 결정 1건·요청 멱등), K(`DECIDE_CALLERS`·column-grant 실측·자동 승인 부재 4중) |
| **PR-10** | 입금: `payments`+입금·역기록+`converge_payment_status` 배선+확정 SO 경고 이벤트 | M08 | `PiPaymentsPanel` | A(일부입금 전환 왕복·역기록·경고), J(동시 입금 due 초과=1건) |
| **PR-11** | 게이트: `gates` 코어(타입·`clearance`·평가기 등록부)+7 평가기(ITEM_MAPPING·DUPLICATE_PO(SO측)·PRICE_DEVIATION·CREDIT 어댑터·MARKET_READINESS·MOQ·PI_DEPOSIT)+`gate_evaluations`·`gate_overrides`+override API+`GET /sales-orders/{id}/gates`+`gates.override.*` 이벤트 | M09 | SO 상세 `GatePanel`·override 다이얼로그 | A(게이트 7종×결과 전수+`GATE_SPECS` 메타, **PI 게이트 활성/비활성 양방향**), H(override 통제·해시 낡음 409·CREDIT override 불가), K(gates 도메인 무임포트·통과 판정 복제 0) |
| **PR-12** | **확정 배선**: `confirm_sales_order`(§2.10)·`POST …/approval-requests`·SO `void_for_target` 훅(편집·취소)·QT CONVERTED 수렴·`AllocationPort.on_confirmed`·`SalesOrderConfirmOut`·소비 접점 스캔 편입 | M10(ALTER) | `ConfirmPanel`·`CreditEvaluationCard`·승인 요청 버튼 흐름 | **DoD ③ 여신 초과→승인 게이트, DoD ④ 확정 후 단가·환율 불변**: H(**승인 우회 차단 ADMIN 포함·승인 후 불변**), A(`test_confirmed_prices_and_fx_are_immutable` 4종 전표), J(여신 결정적 경합+변이 확인·확정 더블클릭·확정×결정×입금 교차 30회 데드락 0), I(자동 확정 부재) |
| **PR-13** | 인테이크(MANUAL): `order_intakes`·라인·`register_intake`·`resolve`·`confirm_intake`(SO 접수 생성, `copied_from_so_id`)·거부·PO 중복 착지 거부 | M11 | `/orders/intakes`(등록·검토·품번 등록 유도·확정) | A(**중복 PO 착지 거부**·스냅샷 값 일치·STALE_MAPPING), J(`confirm_intake` 롤백 원자성·동시 confirm), G(DB 직행 불가·착지=PENDING만), I |
| **PR-14** | CSV 입구: `template.csv`·`import-csv`(파일 전체 원자·9열 양식·xlsx 미수용)·파일 해시 멱등 | 없음 | CSV 업로드·오류 리포트 | F(양식 왕복·날짜/수량/단가/지수표기 경계), G(**파일 해시 멱등**), J(같은 파일 동시 업로드) |
| **PR-15** | 오더 보드: `GET /order-board`(비-Page 객체·4열)·드릴다운·벌크 3종(건별 독립 TX·id순)·저장 필터·보드 CSV | M12 | `/orders/board`(칸반·벌크 결과 모달·저장 필터) | H(**동시 20명 벌크 경합 정합**·벌크 부분 성공·벌크로 승인/override 우회 불가), K(N+1 상수·원가 필드 부재·`BOARD_STAGE_STATUSES` 완전성) |
| **PR-16** | 마감: `idempotency-purge`·`session-purge` 잡(F18)+JOB 12행 총수 대사·runbook 4종(§4.5)·PROGRESS 종결/부채 최종·WBS/GC 확정·docs/testing.md·**워크스루(렌즈 11)**: CSV 업로드→검토→품번 등록→인테이크 확정→SO 편집→override→여신 초과 확정 거부→승인 요청→타 사용자 승인→확정→QT 수주전환 표시→입금·PI 게이트를 입구~출구 1회 실 HTTP+실 브라우저로 관통 | 없음 | 잔여 화면 정비 | J(purge), 워크스루 e2e 1쌍(H·J 대표), 11렌즈 체크 |

**선행 관계**: 1→2→3→4→5→6→7→(8)→9→10→11→12→13→14→15→16. PR-8(PO)은 M06이 M01에만 의존하므로 PR-5 이후 어느 위치에도 끼울 수 있고, PR-10(입금)은 PR-6 이후 PR-9와 순서를 바꿀 수 있다(단 PR-11은 9·10 모두 이후, PR-12는 11 이후).

**PR-5가 최대 규모**(커널+QT 백엔드+프런트+검산 잡): 리뷰 부담이 크면 5a(커널·QT 백엔드·잡), 5b(QT 프런트+타임라인)로 나눠도 무방하다 — 5a 병합 시점에도 API·테스트로 동작한다.

### 3.2 안 2 — 수평(스키마 선행) 8 PR

| PR | 범위 |
|---|---|
| S1 | PR-1+PR-2 (문서·플랫폼 기반) |
| S2 | PR-3+PR-4 (마스터·정책) |
| S3 | **전표·승인·게이트·입금·인테이크 스키마 일괄**(마이그레이션 M03~M12 통합 1~2건, 24테이블 모델·기계 표·`FIELD_POLICY`·상태이력·믹스인) — 서비스·API 없음 |
| S4 | 전표 4종 서비스·API·참조 생성·전이 |
| S5 | 승인·여신·입금·게이트·확정 |
| S6 | 인테이크·CSV·보드 |
| S7 | 프런트 일괄 |
| S8 | 마감 |

### 3.3 비교와 추천

| 기준 | 안 1(수직 16 PR) | 안 2(수평 8 PR) |
|---|---|---|
| 각 병합이 동작 가능 | ○ 매 PR이 API·화면·테스트로 동작 | △ S3는 호출자 없는 모델 24개(ADR-0041 "죽은 문"·소비 접점 스캔 실패 위험) |
| 되돌리기·리뷰 | ○ 작은 diff, 마이그레이션 1~2테이블씩, 문제 시 해당 PR만 되돌림 | × S3가 거대한 단일 마이그레이션·리뷰 불가, 결함 발견 시 전량 재작업 |
| 프런트 시점 | ○ 슬라이스마다 포함 → DoD ①이 PR-7에서 실증 | × S7까지 화면 부재, 워크스루 지연 |
| 총 PR 수·문서 오버헤드 | × 16회 병합 게이트·완료 보고 | ○ 8회 |
| 스키마 조기 확정 | △ 후속 PR이 앞 PR의 열을 재발견(단 X-03~X-11에서 열 표를 미리 확정해 완화) | ○ 한 번에 확정 |
| 변경 충돌(imports/registry 등) | ○ 마스터 변경을 PR-3에 격리 | ○ |

**추천 = 안 1.** 근거: ① CLAUDE.md "작은 단위 구현→즉시 실행·테스트, 큰 덩어리 일괄 생성 금지"와 §22 렌즈 8·11(테스트·워크스루)에 직접 부합 ② 안 2의 S3는 소비 접점 없는 모델을 대량 병합해 C/X2가 경계한 "죽은 문"이 되고 ③ 수직 슬라이스만이 DoD ①(참조 관통)을 PR-7에서, DoD ③④를 PR-12에서 **실동작으로** 증명한다. 안 1의 비용(병합 16회)은 PR-1 문서 일괄·PR-3 마스터 일괄로 상쇄했다.

### 3.4 DoD 4항과 A/H/J/K 검증의 배치

| WBS S3-1 DoD·검증 | 완결 PR | 기여 PR |
|---|---|---|
| ① 참조 생성만으로 QT→PI→SO 관통(재입력 화면 없음) | **PR-7**(e2e+프런트) | PR-5 QT, PR-6 PI, PR-16 워크스루 |
| ② 중복 바이어 PO 0건 | **PR-13**(인테이크 착지, CSV=PR-14) | PR-7 SO 유니크·취소 후 재사용 |
| ③ 여신 초과 → 승인 게이트 | **PR-12** | PR-9 평가·승인, PR-11 게이트 |
| ④ 확정 후 단가·환율 불변 | **PR-12**(SO 확정) | PR-5 QT 발행·PR-6 PI·PR-8 PO 동결 |
| 검증 A: 중복 PO·불변·PI 게이트 | PR-7·13 / PR-5·12 / PR-10·11·12 | |
| 검증 H: 승인 우회 차단·승인 후 불변(+결재선 매핑·대결 기간+이력·정체 독촉·동시 20명 벌크·담당 일괄 이관) | **PR-12**(우회 차단·불변) | PR-9(대결·정체·결재선), PR-15(20명 벌크), PR-5~8(담당 이관 handover 라우팅) |
| J: 확정 더블클릭·채번 동시 100·잔량 초과 동시 생성·취소 vs 후속·여신 경합·롤백 원자성 | PR별 자기 J | PR-5·6·7·9·10·12·13 |
| K: 페이지네이션 스캔·auth-coverage·forbid 래칫·IDOR·로그 마스킹·아키텍처(상태 통로·FIELD_POLICY·no-auto-confirm·임포트 DAG) | 전 PR | PR-2 골격 |
| G(추가 배정): PO 원가 마스킹·파일 해시 멱등·AI 직행 불가 | PR-8·PR-14·PR-13 | §20 헤더 "P3=A·B·E"에 H·I·G·K 매핑 보강(§4.1) |

---

## 4. 계획서에 빠지기 쉬운 것 점검

### 4.1 DESIGN 갱신 문단 후보 (PR-1에서 일괄 — "자율 확정(2026-09-30)" 표기, 원문은 고치지 않고 [M4] 등 보강 문단으로 부기)

| 절 | 갱신 내용 | 출처 묶음 |
|---|---|---|
| §2 권한·통제 | 승인 워크플로우 보강(유형 1종+추가 절차·상태 6·전이 7·소비·SoD·**"관리자는 상시 통과"는 접근 가드 층, 업무 게이트(승인·게이트)는 ADMIN도 예외 없음** 층 분리·대결 규칙) / 역할 매트릭스·**PO 주체=TRADE 판정 부기**(ROLE_SEED 문구는 마이그레이션 없이 유지) / 소유권 정의(역할 스코프+부모-자식 404+당사자성) / 정책 저장소 관리자 통제 / ADR-05 취소 세칙 적용 범위(원장·수량을 쓰지 않는 전표는 "상태 전이+불변 이력"이 반대 부호 기록의 충족) | C·F·E·B |
| §3 표 맵 | PI `(+lines)` 표기 정정, `bank_accounts` 추가, `fx_rates` 항에 "S3-1은 전표 헤더 스냅샷까지·마스터 미신설(재판정 트리거 3종)", `approvals` 외 `approval_lines`·`approval_events`·`delegations`, `payments`(S3-1=선수금 입금·S3-3=채권 확장), `policy_settings`, `order_intakes`·`order_intake_lines`, `gate_evaluations`·`gate_overrides`, `board_saved_filters`, 상태이력 4표, `purchase_orders.po_kind`, M9 부기(스테이징 물리 입구), 채번 SO-2026-0001 문면(발급 시점·KST) | A·B·C·D·E·F |
| §4.1 / §4.6 | `skus.moq`(NULL=미정의) / partners `name_en`·`address_en`, **여신한도 등록·변경=관리자** | A·E |
| §7.1 | 참조 카디널리티(QT→PI 1:N·PI→SO 활성 1:1·QT→SO 직접 1:N), PO 라인=SKU(자재는 P4 가산), 잔량=하위 라인 SUM 파생, 결제조건·Incoterms 열거 | A·B·F |
| §7.2 | 코드값·전이표(25/101)·PI/PO 초안 없음(비저장 `preview`로 편집 요구 충족)·자동 엣지·RESERVED·**수주전환=그 QT에서 파생된 SO가 처음 확정되는 트랜잭션(X-18)**·QT 개정=복제(`copied_from_id`)+원본 취소·동결 시점·SO 접수=인테이크 확정 산출물(2단) | A·B·D |
| §7.3 | PI 게이트=금액 대조(`split_advance` HALF_UP·등호 통과)·PI 참조 SO는 PI 조건 기준·모드 OFF/WARN/BLOCK·미설정=BLOCK·PI 없는 선수금 SO 처리·은행정보 스냅샷 원천=`bank_accounts` | E·A |
| §7.4 | 게이트 명세표(결과 4×해소 3·평가 시점·MOQ NULL=PASS·GRAY=UNKNOWN·DISCONTINUED 시점별)·확정 시점 잠금 하 재평가·미해소 시도는 증적 기록 후 409·override 별도 액션·오더 보드 세부 | D |
| §7.10 | 여신 노출 산식·통화 규약(A7 `comparable_amount`)·NULL=관리 안 함·0=신용불가·strict 초과·UNEVALUABLE=승인 불가 확정 거부·미수 미반영 배지·payments 분담 | E |
| §8.3 | 잔량 SUM 파생·헤더 SHARE+라인 FOR UPDATE는 S3-1 로컬 결정(S4-1 ADR이 시그니처 유지 하에 대체 가능) | B |
| §12.2 | "스테이징 한 입구"는 원칙, 오더 도메인 물리 입구=`order_intakes`·`import_staging`=마스터 왕복 전용 부기 / 전표 CSV 범위(4종+보드)·PO CSV 역할별 열 | D·F |
| §14 | 화면 배정: 승인함·대결 설정·결재선(ADMIN)·오더 인테이크·오더 보드·정책 설정(ADMIN)·은행 계좌(ADMIN)·전표 4종 목록/상세 | C·D·E·A |
| §15 | 잡 레지스트리 5행 추가+4금 논증+"전표" 용어 충돌(G8) 해소(회계 전표 한정) | B·A·C·F |
| §17.2 | 여신 체크=거래처 행 `FOR NO KEY UPDATE`(S3-1 선행 결정, P4 ADR이 대체 가능)·전역 잠금 순서·409 `LOCK_BUSY` | E·A·B |
| §17.3 | 채번 시점=행 최초 저장·연도=발급 시각 KST·전표 `doc_number` 전역 UNIQUE | B·A |
| §17.4 | 중복 바이어 PO·PI→SO·복제본 부분 유니크 술어에 `status<>'CANCELLED'`(정정=취소+신규 경로 보장), "문서번호" 멱등 축 해석 | A·B |
| §17.5 | IMMUTABLE 확장 8테이블(상태이력 4·approval_events·gate_evaluations·gate_overrides·payments)+**컬럼 단위 UPDATE 권한 수단** 명문 | B·C·D·E |
| §18.1 / §20 K | IDOR 정의 1문장 / "타 사용자 전표 URL 403"="역할 밖 사용자의 접근 403" 해석 주 | F |
| §20 헤더 | P3 매핑을 "A·B·E"에서 "A·B·E·**G·H·I·K**"로 보강(H=승인 우회·승인 후 불변·결재선·대결·정체·20명 벌크, I=자동 확정 부재, G=파일 해시 멱등·PO 원가 마스킹) | B·C·D·F |
| §21 | [M2] 잡 7→12행, 소급 입력 runbook 전표 계열 4행 | C·F |
| ADR-11 해석 | "값=데이터, 키·타입·기본값=코드, 폐쇄 키 CHECK" (정책 저장소) | E |

### 4.2 ADR 5줄 후보 (0051~0067) 와 기존 ADR 부기

각 ADR = 결정·근거·기각한 대안·되돌리기 비용·영향 세션 5줄, "자율 확정(2026-09-30) — 사후 번복 가능" 표기.

| 번호 | 제목 | 흡수한 원 ADR 제안 |
|---|---|---|
| 0051 | 전표 상태 기계·전이표·상태 통로·상태이력 4표·이벤트 | B 0051·0055 |
| 0052 | 전표 사슬 구조: 테이블·모듈 계층·참조 카디널리티·소비 파생·수주전환 정의·복제/개정·PI 미리보기·잔량 계약·AllocationPort | A①·B 0054 |
| 0053 | 동결·불변·정정·취소: 동결 시점·`FIELD_POLICY`·트리거/해시 미채택·취소=전이·역순 취소·삭제 금지·GC-A4 해석·§17.4 술어 보강 | A④(동결)·B 0052 |
| 0054 | 채번: 접두어·발급 시점·KST 연도·전역 UNIQUE | A⑦·B 0053 |
| 0055 | 결제조건·Incoterms·환율 규약(`fx_rates` 미신설·비교 불가=None) | A②③ |
| 0056 | 스냅샷·마스터 보강(partners·bank_accounts·skus.moq)·단가·무상·자릿수·서브 미니멈 한계 | A④(스냅샷)⑤⑥ |
| 0057 | 구매 발주(PO): SKU 전용·`po_kind`·`_cost` 명명·마스킹 9채널·idempotency at-rest 수용·OEM/facilities·후반 소유 | A⑧·F-F1 |
| 0058 | 스케줄러 레지스트리 5잡 확장·4금 논증·"전표" 용어 해소 | B 0056·C③(잡)·A14·F18 |
| 0059 | 동시성 계약: 잠금 순서·거래처 행 잠금 모드·409·멱등·제약명 번역·`version` 필드·에러 도메인 규칙 | A13·B8·E③(잠금)·E10 |
| 0060 | 승인 코어: 유형 절차·상태 6/전이 7·소비·무효·digest·컬럼 권한·ADR-02 예외 | C① |
| 0061 | 결재선·SoD·대결·알림·결재함 | C②③ |
| 0062 | 오더 인테이크: 별도 테이블·2단 흐름·착지·CSV·원본 미보관 | D① |
| 0063 | 게이트 공통·원천·확정 시퀀스·PI 게이트 모드 의미 | D②③·E④⑥ |
| 0064 | 여신·입금: 산식·통화·미수 provider·마스킹 유지·`payments` 원장·SO 증적 3열·한도 변경 ADMIN | E①② |
| 0065 | 정책 저장소(`policy_settings`) | E③ |
| 0066 | 오더 보드·벌크·저장 필터·검색형 선택 | D④·F16 |
| 0067 | 역할·소유권·권한 매트릭스·마스터 정합(유형 해제·품번 삭제·DocKind·forbid 래칫·user FK 분류) | F-F2·F-F3 |

**기존 ADR 부기 10건**: ADR-0018 ㉠(`approval_lines.threshold_amount` 유니크 예외 등록) / ADR-02(approvals·delegations·events는 soft delete 없음) / ADR-0026 ②(여신 마스킹 비대상 유지·재판정 트리거=역할 세분화·외부 공유 뷰) / ADR-0041(SO 4·PO 3 RESERVED 값은 "죽은 열거"가 아님 — DESIGN 근거+소비 세션 명시) / ADR-0040(IMMUTABLE 8테이블·컬럼 UPDATE 권한) / ADR-0013·0014(청소 잡 이행) / ADR-0037(OEM 슬롯 `po_kind` 종결·facilities 유지) / ADR-09(스테이징 물리 입구) / ADR-05(취소 세칙 적용 범위) / ADR-0024(PO 필드 부재 적용).

### 4.3 골든 케이스 v1.4 신설 (현재 v1.3 — 케이스 삭제 금지, 변경 이력·매핑표 행 추가, "S3-1 배정 0건" 해소)

| 번호(GC 파일 기존 번호 다음) | 제목 | 요지(Given/When/Then, 성공·거부 양방향) | PR |
|---|---|---|---|
| GC-A6 | 확정 후 단가·환율 불변 | 초안(QT DRAFT·SO RECEIVED) 편집 성공 / 동결 후 CONTENT·ORIGIN 각 열 수정 409·마스터 판가 변경 후에도 전표 값 유지·`assignee_id`·`internal_note`는 허용 | 5·12 |
| GC-A7 | 중복 바이어 PO 0건 | 같은 (바이어, 정규화 PO번호) 비취소 SO/PENDING 인테이크 2건째 거부 / 취소 후 정정 SO는 같은 번호 허용 / soft delete·REJECTED 후 재유입 허용 | 7·13 |
| GC-A8 | 역순 취소와 잔액 복원 | 후속 생존 시 선행 취소 409 / SO 취소→PI 취소→QT 자동 복귀→QT 취소 / 여신 노출·잔량 기준선 복원 | 7 |
| GC-A9 | 참조 생성 관통(재입력 0) | 요청 본문에 SKU·단가·통화·환율·거래처 필드가 없이 QT→PI→SO / 잔량 초과 거부 / 만료·취소 PI 수량 QT로 환원 | 6·7 |
| GC-A10 | PI 입금 게이트(선수금 T/T만 활성) | 선수금 T/T: 입금=요구액 통과·−1 차단(BLOCK) / 같은 조건 L/C·후불은 BLOCK 모드에서도 통과 / 결제유형 편집 우회 차단(PI 조건 기준) | 11·12 |
| GC-A11 | 여신 초과 승인 게이트 | 한도 내 확정 성공 / 초과·무승인 거부(ADMIN 포함) / 승인 후 확정 성공 / 통화 불능(UNEVALUABLE)은 승인 있어도 거부 / SO 쪼개기 순차 확정 | 12 |
| GC-A12 | 입금 역기록 | 반대 부호 신규 기록·원본 불변·재역기록 거부·PI 상태 복원 / 확정 SO 존재 시 경고(자동 취소 없음) | 10 |
| GC-A13 | 평가 불능은 통과가 아니다 | 게이트 평가기 미등록·예외·GRAY 준비도·정책 미설정은 UNKNOWN/BLOCK으로 확정 불가(500·PASS 아님) | 11 |
| GC-F2 | 여신 동시 확정 | 한도 1,000에 600+600 동시 확정 → 정확히 1건(잠금 제거 시 실패 변이 확인) / 다른 거래처는 서로 대기 없음 | 12 |
| GC-F3 | 동시 20명 벌크 확정 | 겹치는 대상 집합·서로 다른 순서 → 건당 정확히 1회·리포트=DB 상태·교착 0 | 15 |
| GC-G2 | PO 원가 마스킹(조회 역할) | VIEWER: 200이되 `*_cost`·`currency`·`price_*` 키 부재·정렬·필터 422·CSV·에러·로그·이벤트·알림에 센티널 원가 0회 | 8 |
| GC-H3 | 승인 우회 차단(ADMIN 포함) | 승인 필요 SO를 상태 PATCH·벌크·인테이크·CLI·ADMIN 토큰으로 확정 시도 전부 거부 / 승인 후 확정 성공 | 12 |
| GC-H4 | 승인 후 불변·재승인 | 승인 후 라인·통화·환율 변경 → 소비 시 무효(VOID)·재승인 요구 / 메모·담당자 변경은 무효화 안 함 / 승인 1회 소비 | 12 |
| GC-H5 | 대결 기간·이력 경계 | KST 날짜 양끝 포함·UTC/KST 갈림 경계·소급 금지·재위임 불가·이력(위임자·수임자·대결 id) 기록 | 9 |
| GC-H6 | 인테이크 자동 확정 불가 | 수동·CSV(결정적 파서) 어느 입구도 PENDING으로만 착지·CONFIRMED는 사람 1클릭 함수 1곳(GC-H1의 CSV 확장) | 13·14 |

매핑표 행: 위 14건을 S3-1(PR 번호 포함)에 배정. 변경 이력 v1.4 항목에 "S3-1 통합 계획 판정 2026-09-30, 매핑표 S3-1 배정 0건 실측(v1.1·v1.2·v1.3 선례)" 기재.

### 4.4 WBS v1.5 변경 이력 (PR-1)

1. **S3-1 산출물 명시 보강**: `payments`(선수금 입금 최소형)·`policy_settings`·`bank_accounts`·상태이력 4표·오더 보드 벌크·`users/lookup`·검색형 선택·정책 화면. S3-1 검증란 H 2항→4항(승인 우회 차단·승인 후 불변·결재선 매핑·대결 기간+이력) 정정, §20 헤더 P3 매핑 보강.
2. **S3-2**: 수입선적의 PO 참조(`po_line_id`)·`customs_records`·`open_order_amount` 선적분 차감은 미수 provider `reflected=True` 등록 릴리스와 같은 PR에서만·`CHILD_LINKS`·`LINE_CONSUMERS`·RESERVED 엣지 추가·SO short-close 판정·OEM 프로파일 `profile_id`·PO 라인 ETA 슬롯 판정·QT/PI 만료 임박 알림 판정.
3. **S3-3**: `payments`를 "S3-1 신설 테이블 확장(채권 연결·잔금·초과분)"으로 수정, 미수 provider 등록+기본 구현 잔존 금지 테스트·"선적 확정~미수 발생 노출 공백·이중 계산 0" DoD, L/C 플래그 토글 경로(`lc_terms`와 함께)·자사 레터헤드 마스터·EXPIRED/CANCELLED PI 입금 처리 재판정·documents 전표 첨부(확폭 경고).
4. **S3-4**: 승인 유형 `EXPENSE_OVER_THRESHOLD` 소비. **S4-1**: PO 후반(입고 문서·PO 잔량 차감·후반 전이·IN_PO ref)·`lock_buyer_for_credit` 교체 여부·잠금 순서표 승계·잔량 표현 최종 ADR. **S4-2**: `AllocationPort` 실구현·SET 구성 변경 가드. **P4**: 자재 PO·DISPOSAL·STOCKTAKE_DIFF 승인 유형.
5. **배정 공백 등재**: `fx_rates` 소유 세션(재판정 트리거: 환율 자동 수집 §15 L3 / S6-2 착수 / 환율 입력 오류 사고), 승인 유형 `NEW_PARTNER`·`SOURCING_NON_RECOMMENDED` 소비 세션. **S5-1**: gate `subject_type` 확장. **S5-4**: 조건부 자동 확정 ADR(자동 확정 부재 테스트 개정). **S6-1**: 원본 파일 보관. **S6-3**: 동결 다이제스트·야간 전건 재해시. **P6**: 헬스체크 결측에 "결재선 미설정"·"정책 미설정". **P7**: `DECIDE_CALLERS` 갱신+ADR.

### 4.5 runbook 양식

| 문서 | 갱신 |
|---|---|
| `docs/runbook/forms/manual-record-form.md` | 소급 입력 대응표에 QT·PI·SO·PO 4행(수기 임시번호·증빙일 `doc_date`·입력일 `created_at` 자동·상대방·품목·수량·단가·비고). 공식 번호는 신규 채번, 수기 임시번호는 `internal_note`에 병기. API 계약: 미래 `doc_date` 422·과거 하한 없음·`created_at` 본문 입력 불가 |
| `docs/runbook/incident-sop.md` | 전표 소급 순서(QT→PI→SO, PO 독립)·소급 후 검산(`trade-docs-totals-verify` 수동 실행) 1항목 |
| `docs/runbook/prod.md` | **"S3-1 운영 개시" 절 신설**: ① ADMIN 2인 이상(제2 결재 계정 — 1인 ADMIN은 자기 기안 승인 불가가 정상 동작) ② 결재선 등록(`SO_CREDIT_EXCEEDED`, 사용 통화별 **임계 0**, 역할 지정 — 등록 전 여신 초과 SO는 확정 불가가 정상) ③ `/settings/policies`에서 PI 게이트 모드·단가 편차 허용치 저장(미설정=차단 동작, 화면에 적색 표시) ④ 통화별 은행 계좌 등록 ⑤ 거래처 영문명·주소(CSV 왕복) ⑥ 여신한도 입력(ADMIN 전용, **시스템 도입 전 미수는 노출에 반영되지 않으므로 잔여 한도로 설정** — P-51) ⑦ SKU MOQ·바이어 품번 매핑 ⑧ L/C는 S3-1에서 닫혀 있음 ⑨ 관리 화면의 잡 12행 확인 ⑩ 통화 혼용 거래처의 한도 통화=KRW |
| `docs/runbook/prod.md` 또는 `local-dev.md` 잡 표 | 5행 추가(F18 포함) |

### 4.6 PROGRESS 부채·관찰 등재 (조용한 누락 금지 — 전건 "트리거·소유" 병기)

| ID | 내용 | 소유·트리거 |
|---|---|---|
| P-01 | `open_order_amount` 선적분 차감은 미수 provider 등록 릴리스와 같은 PR에서만 | S3-2 / S3-3 |
| P-02 | SO 부분출하 후 잔량 종결(short-close) 경로 부재 | S3-2 계획 |
| P-03 | QT/PI 만료 임박(D-N) 알림 | S3-2 기일 엔진 착수 또는 만료 견적 사고 |
| P-04 | 수입선적 PO 참조·`customs_records` | S3-2 |
| P-05 | PO 라인 ETA 슬롯 미도입 | S3-2 계획 또는 P4 백오더 중 먼저 |
| P-06 | OEM 마일스톤 프로파일 `profile_id` 가산 | S3-2 |
| P-07 | RESERVED 상태 엣지·`CHILD_LINKS`·`LINE_CONSUMERS` 등록 | S3-2·S4-1·S4-2 |
| P-08 | 미수 provider 등록(기본 구현 잔존 금지 테스트) | S3-3 DoD |
| P-09 | `payments` 확장(`pi_id` 완화·`receivable_id`·잔금·초과분) | S3-3 |
| P-10 | L/C 플래그 행 공급·토글 경로(현재 S3-1 프로덕션에서 L/C 선택 닫힘) | S3-3, 필요 시 CLI 소단위 |
| P-11 | 자사 레터헤드 마스터 | S3-3 |
| P-12 | EXPIRED/CANCELLED PI에 입금 도착 시 처리(현재 409 후 신규 PI) | S3-3 재판정 |
| P-13 | documents 전표 첨부·`owner_type` 확폭 경고(QUOTATION 9·SALES_ORDER 11·ORDER_INTAKE 12·PURCHASE_ORDER 14·PROFORMA_INVOICE 16자 대 String(13)) | S3-3·S6-1 |
| P-14 | 입금 이중 입력 탐지(은행 참조 유니크 불가) | S3-3·P7 |
| P-15 | 선수금 입금분 노출 미차감 재판정 | S3-3 채권 적용 |
| P-16 | PI 게이트 정책 미설정=BLOCK — 오너가 설정 후 완화 여부 | 운영 개시 |
| P-17 | PO 후반(입고 문서·잔량 차감·후반 전이) 소유 | S4-1 |
| P-18 | 잔량 SUM 파생·`lock_buyer_for_credit`·잠금 순서표 승계 | S4-1 ADR |
| P-19 | SET 구성 변경 드리프트 | S4-2 착수 |
| P-20 | 자재 PO 미지원 | P4 사급 ADR 또는 오너 요구 |
| P-21 | `AllocationPort` 실구현(현재 NOT_IMPLEMENTED 노출) | S4-2 |
| P-22 | `fx_rates` 마스터 미신설·환율 신선도 서버 규칙 없음·수동 입력 신뢰 한계 | 환율 자동 수집 §15 L3 / S6-2 / 환율 입력 오류 사고 |
| P-23 | 원본 파일 보관(`documents.owner_type='ORDER_INTAKE'` 12자)·거래처 NULL 착지 | S6-1 AI 경로 |
| P-24 | 동결 다이제스트·야간 전건 재해시·라인합=헤더합 야간 검산 확장 | S6-3 / 동결 우회 쓰기 실사고 |
| P-25 | 승인 유형 5종 소비 세션(비용=S3-4, 폐기·실사=P4, **NEW_PARTNER·SOURCING_NON_RECOMMENDED=배정 공백**)·비금액 유형 결재선 스키마 재판정 | 각 세션 / 첫 비금액 유형 |
| P-26 | P6 헬스체크 결측 5종에 "결재선 미설정"·"정책 미설정" 편입 | P6 |
| P-27 | P7 Slack 도입 시 `DECIDE_CALLERS` 갱신+ADR | P7 |
| P-28 | S5-1 gate `subject_type` 확장·S5-4 조건부 자동 확정 ADR(자동 확정 부재 테스트 개정) | S5 |
| P-29 | 정정 전표 미채택(취소+신규) 재판정 | 실사용 리허설 마찰 |
| P-30 | BLOCKED 확정 시도 `gate_evaluations` 행 누적·dedup | 시도 빈도 실측 |
| P-31 | 인테이크 편집 단위 이력 | 파서 개선 데이터 필요 / S6-1 |
| P-32 | `.xlsx` 수용·CSV 양식 개정 시 직전 세대 헤더 병행 | 사용자 요구 / 양식 개정 |
| P-33 | 무상(0원) 라인 인테이크 수용 | 실사용 요구 |
| P-34 | 바이어별 단가 허용치·바이어별 MOQ·품목군별 허용치 | 실사용 요구 |
| P-35 | 바이어 PO 개정판(Rev) 전용 개념 | 실사용 마찰 |
| P-36 | 품번 매핑 정정 API(현재 삭제+재등록으로 우회) | 실사용 요구 |
| P-37 | 벌크 HOLD·CANCEL·저장 필터 공유·게이트 배지 보드 표시 | 실사용·배치 평가 비용 실측 |
| P-38 | MARKET_READINESS override 역할에 CERT 추가 여부 | 실사용 |
| P-39 | `Idempotency-Key` 헤더 길이(>128) 미검증(기존 결함) | 코어 소규모 수정 |
| P-40 | 보드 CSV·게이트 응답 접근 audit 미기록(선례 준수) | 대량 반출 우려 |
| P-41 | 서브 미니멈 단가(USD 0.125 등) 미지원 | 실데이터 소수 단가 발생 |
| P-42 | 금액 파서 legacy 3사본 통합 리팩터링 | 별도 판정 |
| P-43 | 동일 SKU 분할 납기(다중 유상 라인) 미지원 | 필요 시 유니크에 납기일 추가 |
| P-44 | 거래처 수정 화면 부재·스냅샷 새로고침 액션 부재·계좌 중복 등록 경합(ADMIN 저빈도) | 실사용 |
| P-45 | 승인 만료 미도입·대상 작성자 SoD·대결 기간 상한·재활성 시 유효 복귀 | 미소비 APPROVED 누적 / 3인 시나리오 관찰 / 장기 위임 남용 |
| P-46 | outbox 요청 이벤트+alert_rules 규칙 시 인앱 알림 이중·알림 이관 후 무자격 링크 403·데일리 브리핑 "결재 대기" 줄 미도입 | 운영 |
| P-47 | `statement_timeout`(57014) 500 누수(벌크 경로) | 벌크 도입 세션 재판정 |
| P-48 | 혼합 통화 거래처는 한도 통화를 KRW로(UNEVALUABLE 하드 블록의 운영 함의) | runbook |
| P-49 | ON_HOLD 재개 시 게이트 재평가 없음(한도 하향·인증 만료 미반영) | 관찰 |
| P-50 | 기존 쓰기 스키마 forbid 32개 소급(S3-1이 수정하는 스키마는 제외 — 단조 감소) | 해당 모듈 수정 세션 |
| P-51 | **시스템 도입 전 이월 미수는 여신 노출에 반영 불가**(E1 '미수 미반영'의 실재 위험 — 한도 판정이 낙관적) | runbook 완화+S3-3 이월 채권 반입 시 재판정 |
| P-52 | handover 일괄 이관이 대상 사용자의 역할을 검증하지 않음(기존 동작) | 관찰 |
| P-53 | 승인·대결·결재선·인테이크 목록 CSV 미포함 | 운영 요청 / S6 대장 정비 |
| P-54 | 마스킹 원장 갱신: #2 "재판정 완료(유지)"·신규 "S3-1 PO 원가 9채널"·idempotency `response_body` 원가 at-rest(생성자 스코프+청소 잡으로 수용) | PR-8 |
| P-55 | 200건 드롭다운 기존 화면 소급 없음(S3-1 신규 화면만 컴포넌트) | 트리거 유지 |
| P-56 | SKU 용량 컬럼 미도입(라인은 품명 스냅샷만) | S3-3·S5-1·§7.7 중 먼저 |
| P-57 | facilities 마스터 미신설·재판정 트리거 3종(F7 문면 교체) | S3-2 OEM 계획 |
| P-58 | 코어 핸들러 `LOCK_BUSY` 부수효과(타 모듈 500→409) 회귀 확인 | PR-2 |
| P-59 | 자기 승인 정책(1인 ADMIN) — 오너 판정 후보 1순위 | §4.8 |
| P-60 | 검증 공백 목록(정적 독해, 실행 검증 못 했음) | §4.9, 각 PR 첫 커밋 |

### 4.7 기타 등재 체크리스트 (빠지기 쉬운 배선)

- `app/registry.py` 모델 모듈 등록 12개(trade_docs·quotations·proforma_invoices·sales_orders·purchase_orders·bank_accounts·approvals·order_intake·order_board·payments·policies·gates) + `api/router.py` include(라우터 누락은 조용한 미노출 → 라우트 존재 테스트) + `test_every_model_module_is_registered`.
- CLI: `document-expiry-sweep`·`approval-stagnation-scan`(선택: `trade-docs-totals-verify`). D의 `seed-gate-policies`는 삭제.
- `docs/testing.md` 그룹 표·CI 기준선(pytest·vitest 총수) 갱신, `concurrency` 마커 테스트는 병렬 pytest 금지(로컬 검증 불가 시 CI 의존 — 함정 ③).
- 프런트: 사이드메뉴 항목(전표 4·승인함·대결·결재선·정책·은행 계좌·인테이크·보드)·`lib/doc-status.ts` 라벨 표(G-10)·`alerts.tsx` entity_type 이동 표(G-05)·금액 산술 가드(`money.test.ts`)·KST 표시.
- 로그 마스킹 테스트: 신규 접미(`_account_no`·`_account_number`)·`test_cost_never_reaches_logs`에 PO·payments 참조 텍스트 미기재 단언.
- 마이그레이션 체크리스트 헤더(rename 없음·CHECK 수기 이름·유니크·시드 없음·downgrade drop_table·REVOKE)를 M01~M12 각 파일 상단에.
- PR 템플릿 항목: 11렌즈·GC 매핑·실행 검증 여부("실행 검증 못 했음" 명시)·기존 동작 변경(E9)·부채 등재.
- 웹 세션 판정 생략에 따른 "자율 확정" 표기(ADR-0011 부기 2026-09-29)를 PR-1 문서 전건에.

### 4.8 오너 판정 후보 — 번복 가능성이 높은 자율 확정과 되돌리기 비용

| # | 통합 결정 | 번복 시 | 비용 |
|---|---|---|---|
| 1 | **1인 ADMIN은 자기 기안을 승인할 수 없다**(C4, ADMIN 포함·예외 없음). 제2 결재 계정 필요 | 서비스 술어 1줄+`sod`·`on_behalf_not_requester` CHECK 재정의+ADR — 단 §20 H 약화라 오너 명시 판정 필요 | 낮음~중간(가장 번복 가능성 높음) |
| 2 | **전표는 회사 공유 자산**(§20 K "타 사용자 전표 403"="역할 밖 403" 해석, F9) | `assert_may_write` 1함수+에러코드+프런트 문구, 이관·벌크·FREE 동반 재설계 | 낮음~중간 |
| 3 | **정책 미설정=BLOCK**(PI 게이트)·허용치 0bp — 운영 개시 전 ADMIN이 설정해야 정상 흐름 | 레지스트리 상수 1곳 | 낮음 |
| 4 | L/C 선택은 S3-1 프로덕션에서 닫힘(플래그 행 공급 경로 없음, fail-closed) | CLI `set-feature-flag` 소단위 | 낮음 |
| 5 | 환율 수동 입력·`fx_rates` 마스터 미신설(TRADE가 낮은 환율로 노출을 낮출 수 있음 — 증적·화면에 환율·기준일 표시로 fail-visible) | 마스터 신설은 additive, 방향·정밀도 변경은 높음(지금 확정) | 중간 |
| 6 | **이월 미수 미반영**(E1)+선수금 미차감 — 한도 판정이 낙관적일 수 있음 | S3-3 provider 등록, runbook 완화(잔여 한도로 설정) | 낮음 |
| 7 | 여신한도 변경=ADMIN 전용(E9) — 운영 마찰 | 가드 함수 1곳 | 낮음 |
| 8 | QT 수주전환=SO 확정(통합 결정 X-18) | `converge_parent` 목표 상태 술어 1곳 | 낮음 |
| 9 | PO 초안 없음·생성=발행(§7.2 문면 그대로) | CHECK 재정의+동결 시점 재정의 | 중간~높음 |
| 10 | 인테이크 CSV 파일 전체 원자·`.xlsx` 미수용 | 어댑터 추가(가산) / 원자성→부분 성공은 서비스 1곳(sha 충돌 재판정) | 낮음 |
| 11 | override 권한(가격·MOQ=TRADE·ADMIN, 준비도·PI=ADMIN) | 상수 1곳 | 낮음 |
| 12 | 정정 전표 미채택(취소+신규) | 신규 테이블·상태·승인 재정의 | 높음(마찰이 실측되기 전엔 불필요) |

### 4.9 첫 커밋에서 실행 확인이 필요한 항목 (정적 독해 한계 — 실행 검증 못 했음)

복합 FK·부분 유니크 인덱스의 `alembic check` 드리프트 0 / 제약·인덱스 이름 63자 실측(A 수기 산정 최장 58자) / `VersionMixin`(`version_id_col`)이 부모 dirty(`updated_at` 명시 대입)로 flush당 1회만 증가하는지 / `FOR NO KEY UPDATE` 렌더 SQL과 `FOR KEY SHARE` 비충돌(E3 ⑤) / `CHECK` 식의 NULL 처리(`IS TRUE`) / psycopg `sqlstate` 속성(55P03·40P01 매핑) / `restrict_update_columns`의 `ALTER DEFAULT PRIVILEGES`·왕복 마이그레이션 상호작용(리포 선례 0건) / FastAPI Union 응답 분기가 아닌 `response_model=None`+스키마 선택(ADR-0024) / `ErrorCode` 열거·카탈로그 완전성 / 동시성 테스트의 실스레드+Barrier 안정성(병렬 pytest 금지) / TRUNCATE 하네스와 IMMUTABLE 테이블(권한 회수)·`_NEVER_SEEDED` 상호작용.

---

## 5. 자동화 4금·§15 저촉 재점검 (설계 전체)

### 5.1 4금 4항별 판정

| 4금 | S3-1의 관련 동작 | 판정 |
|---|---|---|
| ① 지출·**발주 확정** | PO 생성=발행=발주 확정: 라우터 1곳·사람 세션·`Idempotency-Key`·미리보기 2단. 스케줄러·CLI·outbox·imports·order_intake·order_board·approvals·gates 어디서도 `create_purchase_order` 임포트 0(통합 `no_auto_confirm` 프레임워크에 등록). 보드 벌크 열거에 PO 없음. §8.8 발주점 권고도 PO를 만들지 않음. SO 확정은 수주 확정(발주 아님)이나 동일하게 사람 1클릭 | **저촉 없음** |
| ② **법적 판정**(HS·원산지·요건) | MARKET_READINESS 게이트는 기존 `readiness` 계산값의 표시(응답에 `SCOPE_NOTE`+"준비 상태 안내(법적 판정 아님)" 고정 문구·'판매 가능·적합·승인' 워딩 금지 스캔). GRAY=UNKNOWN은 통과가 아님. Incoterms는 코드·연도·장소 **형식** 검증뿐(완전성 판정은 S3-3) | **저촉 없음**(워딩 규칙 준수 조건) |
| ③ **대외 최초 발송·협상·클레임** | 대외 발송 코드 0: 알림은 인앱(`notify()`), 이벤트는 내부 `alert_rules` 라우팅(규칙 0행이면 published 처리만), CSV는 사용자 다운로드. QT 발행·PI 생성은 전표 상태 전이(문서 렌더링 S3-3, 발송은 사람) | **저촉 없음** |
| ④ **장부 확정**(분개·마감·**전표**·원장) | QT·PI·SO·PO는 영업 문서(원장·분개·재고·채권 미생성). `payments`는 사람이 입력한 입금 **사실**의 INSERT-only 원장(회계 기표 아님, 입금 분개 초안은 S3-3 이후). §15 문면의 "전표"는 회계 전표이며 영업 문서와의 용어 충돌을 **ADR-0058이 명문으로 해소**해야 함 | **저촉 없음**(ADR 명문화 조건 — 미기재 시 리뷰어 오독 위험) |

### 5.2 자동 전이·자동 실행 전수 (모두 "확정"이 아님)

- QT 자동 3(ISSUED→CONVERTED·CONVERTED→ISSUED·ISSUED→EXPIRED)·PI 자동 7(스윕 1+입금 수렴 6)·PO 자동 0·SO 자동 0·approvals 시스템 통로 3(VOID 2·CONSUME 1 — 승인을 **좁히는** 방향, 부여는 사람 전용)·인테이크 자동 0. 자동 전이의 도착 상태는 CONVERTED·EXPIRED·ISSUED·입금 3태·VOIDED·CONSUMED뿐이며 약정 진입·승인 부여가 아니다. 사람 입력(입금 기록·확정 클릭·취소)의 파생이거나 달력 산술이다.
- 잡 5종: 알림·읽기·기술 행 청소·EXPIRED 수렴 — 원장·발주·대외 발송·법적 판정 없음(§15 "스케줄 레지스트리는 코드 고정 폐쇄 열거, 마이그레이션 시드 금지" 준수, 12행이 관리 화면에 노출).
- **벌크 확정**(오더 보드)은 사람이 선택·클릭한 건만 단일 통로로 처리하며 승인·override를 부여·기안하지 않는다.
- **CSV(결정적 파서) 입구는 PENDING까지** — ADR-09의 "조건부 자동 확정" 예외는 S3-1에서 구현하지 않는다(S5-4 소비).

### 5.3 통제 완화·우회 경로 재점검

| 경로 | 통제 | 판정 |
|---|---|---|
| ADMIN | 승인 결정 SoD 예외 없음·승인 없이 확정 불가(`force` 인자 부재)·CREDIT·ITEM_MAPPING·DUPLICATE_PO는 override 자체 불가(DB CHECK) | 우회 없음 |
| 정책 OFF/WARN(ADMIN) | 사유·audit·outbox·SO/평가 증적에 `policy_source`·모드 기록 | 사람 통제 결정, 추적됨 |
| gate override | 사유 5자·역할 제한·해시 결속(입력 변경 시 자동 무효)·불변 기록·`gates.override.*` 이벤트 | 통제됨 |
| 상태 PATCH·벌크·인테이크·CLI·임포트 | status 필드 부재+`extra=forbid`·단일 통로·SO는 RECEIVED로만 생성 | 우회 없음 |
| 확정 후 편집 | FIELD_POLICY 완전성(미등재=CI 실패)·CONTENT·ORIGIN 동결·FREE 4개만 | 통제됨 |
| DB 직접 변조 | 상태이력·승인 이벤트·평가·override·입금 IMMUTABLE(REVOKE)+승인 스냅샷 컬럼 UPDATE 권한 없음. 전표 헤더는 앱 계정 UPDATE가 필요해 권한 회수 불가(트리거 미채택 — ADR-0028·0040) → CHECK로 가능한 불변식(SO 증적 3열·confirmed_at 결속)만 DB 백스톱 | 탐지형 다이제스트는 S6-3로 이월(P-24) |

### 5.4 fail-closed·fail-visible 재점검 — "평가 불능을 통과로 취급" 후보

1. 정책 미설정 → 가장 엄격한 값(BLOCK/0bp)+`source=UNSET_DEFAULT` 표시 — 통과 아님.
2. 게이트 평가기 미등록·예외·GRAY·기준가 없음·통화 불일치 → UNKNOWN(=BLOCK 효과). 55P03·40P01은 삼키지 않고 409.
3. 결재선 미설정·자격자 공집합 → 422(요청 생성 불가). 승인 평가 불능(UNEVALUABLE) → 승인 경로 없이 확정 거부.
4. L/C 플래그 행 없음·조회 실패 → 꺼짐(fail-closed).
5. 통화 비교 불가(`comparable_amount`=None) → 통과 아님(승인 게이트 또는 UNEVALUABLE).
6. **유일한 의도적 예외: 여신 노출의 미수 항** — S3-1에는 채권·미수 자체가 없어 provider 기본 구현이 `(reflected=False, amount=None)`을 반환하고 **판정을 막지 않는다**(막으면 S3-3 전 모든 확정 정지). 0으로 합산하지 않고 '미수 미반영' 배지·`exposure_is_partial=true`로 fail-visible. S3-1 프로덕션에서 이 예외의 실질 위험은 **시스템 도입 전 이월 미수**(P-51)뿐이며 runbook(잔여 한도로 설정)+S3-3 DoD(기본 구현 잔존 금지 테스트)로 완화한다. 오너 판정 후보 6번.
7. ON_HOLD→CONFIRMED 재개는 게이트 재평가 없음(확정 시점 판정 유지) — 이미 확정된 약정의 일시 정지 해제이며 노출 산식에 ON_HOLD(확정 이력 있음)가 산입되어 노출이 늘지 않는다. 한도 하향·인증 만료 미반영은 관찰(P-49).

### 5.5 결론

자동화 4금 **저촉 없음**, §15 스케줄 레지스트리 계약 준수. PR-1 문서에서 (a) ADR-0058의 "전표=회계 전표" 용어 해소, (b) MARKET_READINESS 워딩 규칙, (c) §5.4-6의 유일 예외와 이월 미수 안내를 반드시 명문화한다. 실행 검증은 하지 못했고(정적 독해), 위 판정은 각 PR의 아키텍처 테스트(no-auto-confirm 프레임워크·임포트 DAG·FIELD_POLICY 완전성)가 CI에서 강제한다.

---

## 부록. 묶음별 PR 제안 → 통합 PR 대응

| 묶음 원안 | 통합 PR |
|---|---|
| A: PR-A 커널+QT / PR-B PI / PR-C SO / PR-D PO | PR-5 / PR-6 / PR-7 / PR-8 (마스터 마이그레이션 1건 → PR-3) |
| B: PR-1 코어 핸들러·문서 | PR-1(문서)·PR-2(핸들러) |
| C: SO 모델 → 승인 코어 → 확정 배선(소비 접점 스캔 동일 PR) → 화면·알림·잡 | PR-7 → PR-9 → PR-12 (화면·알림·잡은 PR-9에 함께) |
| D: D-PR1 gates → PR2 인테이크 → PR3 CSV → PR4 확정 오케스트레이션 → PR5 보드 | PR-11(gates)·PR-13·PR-14·PR-12·PR-15 |
| E: PR-E1 정책 → PR-E2 payments → PR-E3 여신·배선 | PR-4·PR-10·PR-9(평가)+PR-12(배선) |
| F: PR-1 문서·마지막 PR 운영 정비 | PR-1·PR-16 |
