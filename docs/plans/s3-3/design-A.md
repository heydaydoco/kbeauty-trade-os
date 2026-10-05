# S3-3 계획서 부록 A — 서류 데이터 모델·생성기 (레터헤드·CI·PL·S/I·렌더링·파일 저장·검수 게이트)

- 성격: 설계 결정이다(구현 아님). 기준은 main `2092406`(S3-2 종결)이다. 근거 정본은 DESIGN.md §3·§4.1·§4.7·§7.1·§7.2·§7.5·§7.6·§7.7·§7.9·§7.10·§15·§17·§18·§20 B·§22, WBS.md S3-3 행(`W:127-132`)·S4-2 행(`W:150-155`)·v1.6 주석, PROGRESS.md 'S3-2 PR-8 / S3-2 종결'의 **S3-2 부채 최종 목록**(`P:56-165`)·'## 현재'(`P:1689-1690`)다.
- 표기: `D:줄`=DESIGN.md, `W:줄`=WBS.md, `P:줄`=PROGRESS.md, `code:경로:줄`=`backend/app/` 기준 현행 코드, `tests:경로:줄`=`backend/tests/` 기준, `mig:파일`=`backend/migrations/versions/`. 줄 번호는 `2092406`에서 실측한 값이다.
- 판정: 전 안건 **자율 확정**이다(오너 상시 지시 2026-09-29 "결정·개입 없이 끝까지" — 판정 후보는 더 엄격한 fail-closed 권장안으로 확정, 사후 번복 가능, ADR-0011 부기). "미정" 결론은 두지 않는다.
- 안건 서식: 각 안건은 **결정 / 근거 / 대안(기각 사유) / 자율 확정 여부 / 되돌리기 비용**을 적는다.
- 다른 부록 표기: 이 부록이 정하지 않는 것은 **주제로** 가리킨다 — 「채권 부록」(receivables·aging·미수 provider·노출 차감·SO COMPLETED/short-close), 「입금 부록」(payments 확장·PI 입금 재판정), 「L/C 부록」(lc_terms·플래그 공급·하자 체크리스트·S3-2 산식 배선), 「화면 부록」(화면·셸·한국어 UI), 「안전 부록」(트랜잭션·잠금 순서·멱등·권한 매트릭스 정본), 「분할 부록」(PR 분할·마이그레이션 DAG·ADR 번호·WBS/GC 판번). 부록 문자(B~E)는 통합 문서가 확정한다.
- **실행 검증 못 했음.** 정적 독해(파일 열람·grep)만 했다. 라이브러리 버전·폰트 파일 크기·렌더 시간은 추정치이며 착수 PR 첫 커밋에서 실측 기록한다(P-60 선례).

---

## 0. 경계 — 이 부록이 정하는 것과 넘기는 것

| 이 부록(A)이 정한다 | 다른 부록으로 넘긴다(경계만 적음) |
|---|---|
| 서류 종류·형식·언어 매트릭스, 범위 밖 서류(DGD·B/L·PO 발주서) | **채권 부록**: receivables 생성 시점·원천, aging, 미수 provider `reflected=True`, `open_order_amount` 선적분 차감, SO COMPLETED/short-close(Q-04·P-01·P-02) |
| 자사 레터헤드 마스터 `company_profiles`(P-11·R-3a-5) | **입금 부록**: payments 확장(P-09)·EXPIRED/CANCELLED PI 입금(P-12)·선수금 노출(P-15) |
| CI 커널 편입(DocKind `COMMERCIAL_INVOICE`·접두어 `CI`)·상태 2값·엣지 1, `commercial_invoices`·lines·status_log | **L/C 부록**: `lc_terms`, L/C 플래그 공급(P-10), 하자 체크리스트, S/I 수하인 `TO ORDER` 허용 술어(Q-06)의 **참·거짓 판정 함수**(이 부록은 그 함수를 호출하는 자리만 둔다) |
| PL 포장 데이터 `packing_list_packages`·`…_items`(Q-05) | **기일·알림**: 대금만기 `INVOICE_DATE` 앵커 배선·대금만기/제시기한 알림(Q-08) — 이 부록은 "살아 있는 CI의 `doc_date`" 읽기 함수만 낸다(§A20) |
| S/I `shipping_instructions`(채번 `SI`·불변·재발행) | **화면 부록**: 발행 대화상자·미리보기·검증 결과 표·다운로드 버튼·레터헤드 관리 화면 |
| 원천 스냅샷 규칙(선적·SO·당사자 영문명/주소·DG·HS·은행·레터헤드) | **안전 부록**: 전역 LOCK_ORDER 개정 정본·권한 매트릭스 정본(이 부록은 슬롯·역할 **제안**만 — §A14·§A16) |
| 저장 전 검증 카탈로그(§7.6 4항 + 보강)·검증 함수 위치 | **분할 부록**: 마이그레이션 번호·DAG·PR 배정·ADR 번호(이 부록은 M16a~c **초안**) |
| 렌더링 방식(ReportLab PDF·openpyxl XLSX·내장 폰트·결정성·템플릿 동결)·외부 호출 0 | |
| §20 B "검수 미완료 선적의 CI·PL 생성 차단" 게이트(선적 RESERVED 전제) | **S4-2**: 선적 PICKING~CLOSED 엣지 개방(게이트를 실제로 통과시키는 쪽) |
| 렌더 산출물 저장 = documents FILE + `trade_document_renditions`, documents `owner_type` 확폭(P-13)·전표 첨부 개방 범위 | |

겹치는 지점에서 이 부록이 내는 **계약 1줄**:
- 채권 부록이 CI를 원천으로 쓰면 `ChildLink(COMMERCIAL_INVOICE, "receivables", "ci_id")`를 **그 부록이** 등록한다(CI 취소 역순 가드). 단 §A2의 결과로 **S3-3 운영 경로에서 CI는 발행되지 않는다** — 채권 생성 원천을 CI 발행에만 묶으면 채권도 S4-2까지 죽는다(§10 ①, 채권 부록 판단 대상).
- 대금만기 `INVOICE_DATE` 앵커의 원천 = 그 선적의 **살아 있는 CI `doc_date`**(읽기 함수 `live_invoice_date(session, shipment_id) -> date | None`, 없으면 기존 `INVOICE_NOT_ISSUED` 유지 — `code:modules/trade_docs/schedule.py:62-63,254-255`).
- L/C 부록은 `lc_allows_order_consignee(session, shipment_id) -> bool`(없으면 False = fail-closed)을 제공한다. S/I의 `consignee_mode ≠ PARTY`는 이 함수가 True일 때만 받는다(§A8).

---

## A1. 서류 종류·형식·언어 매트릭스 — 범위 밖 3종 명시

**결정**

| 서류 | 원천(한 원천) | 저장 단위 | 형식 | 언어 | 생성 게이트 |
|---|---|---|---|---|---|
| QT(견적서) | `quotations`(+lines) — 발행(동결) 후만 | 기존 전표 + 렌더 산출물 | PDF·XLSX | EN·KO | QT `status ≠ DRAFT`(동결) |
| PI | `proforma_invoices`(+lines·은행 스냅샷) — 생성=동결 | 기존 전표 + 렌더 산출물 | PDF·XLSX | EN·KO | 항상(생성=발행) |
| CI | **선적**(EXPORT) + SO + 레터헤드 + 사람 입력(운송·원산지) → `commercial_invoices` 불변 스냅샷 | 신규 전표(DocKind) | PDF·XLSX | **EN만** | §A2 검수 게이트 + §A10 검증 전건 |
| PL | 같은 발행 TX의 포장 입력 → `packing_list_*` 불변 행(CI 자식) | CI와 한 세트(번호 공유) | PDF·XLSX | EN만 | CI와 동일(같은 TX) |
| S/I | **살아 있는 CI/PL 세트** + 선적 당사자(FORWARDER·NOTIFY) + 사람 입력(B/L 종류 등) → `shipping_instructions` 불변 행 | 신규(채번 `SI`, 비커널) | PDF·XLSX | EN만 | 살아 있는 CI 존재(따라서 §A2 게이트를 상속) |

- **범위 밖(이 세션에서 만들지 않는다)**: ① **DGD** — §7.6 문면에는 있으나 WBS S3-3 산출물에 없고(`W:129`), DGD 작성 책임은 화주이며 §7.7 DG 게이트(MSDS 유효본 차단·DGD 태스크)가 S4-4다 → S/I·CI·PL이 DG 정보를 **스냅샷·표기**만 하고 DGD는 부채(§부채 A-01). ② **B/L·AWB** — §7.6 "생성하지 않는다(발행 주체=운송인)". S/I가 B/L draft 대조(S5-3)의 기준값이 된다(§A8 계약). ③ **PO 발주서 렌더** — WBS 목록 밖이고, PO 원가가 들어간 파일은 ADR-0024 원가 9채널에 **10번째 채널**(문서 다운로드는 전 역할)을 연다 → 만들지 않는다(부채 A-02).
- **언어 변형**: QT·PI는 EN·KO 두 변형(라벨 사전 2종, 품명은 `sku_name_en`/`sku_name_ko` 스냅샷). CI·PL·S/I는 대외 통관 서류라 EN 단일 — KO 변형을 만들지 않는다(KO CI는 수요 근거 없음 — 부채 A-03 재트리거 '국내 CI 요구 1건').
- **DRAFT 렌더 0**: 동결 전(QT DRAFT) 문서의 PDF/XLSX는 만들지 않는다. 미리보기는 화면(JSON)이다 — 회사 밖으로 나가는 파일은 동결된 원천에서만 나온다(§15 L3 '대외 최초 발송'은 사람이 파일을 내려받아 보내는 L1 — 발송 코드 0).

**근거**: §7.6(`D:215`) "QT·PI·CI·PL·S/I·DGD 전부 선적/전표 한 원천에서 템플릿 렌더링(PDF·엑셀, 언어 변형 지원)", WBS S3-3 산출물 "QT·PI·CI·PL·S/I 템플릿 렌더링(PDF·엑셀·언어 변형)"(`W:129` — DGD 없음), §7.7(`D:217`), ADR-0024(PO 원가 채널), §15(`D:329`).

**대안(기각)**: (a) DGD 템플릿 선제 생성 — MSDS 게이트(S4-4) 없이 DGD 양식만 내면 '시스템이 만든 위험물 신고서'가 검증 없이 나간다(더 위험). (b) CI KO 변형 — 근거 없는 양식 증식. (c) DRAFT QT 워터마크 PDF — 워터마크는 잘라내면 그만이고, 미동결 값이 회사 밖으로 나간다.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 낮음**(형식·언어는 렌더 레지스트리 키 추가, DGD는 S4-4 가산).

---

## A2. §20 B "검수 미완료 선적의 CI·PL 생성 차단" — 게이트를 S3-3에 지금 세운다 (사양 해석 충돌 → 자율 확정)

**결정**
- CI(+PL) 발행의 상태 술어 = **`CI_ISSUABLE_SHIPMENT_STATES = {INSPECTED, RELEASED, SHIPPED}`**(선적 구분 EXPORT만). CLOSED는 제외(종결 후 재발행은 S4-2가 종결 의미를 정할 때 재판정 — 부채 A-04), PLANNED·RELEASE_ORDERED·PICKING·CANCELLED는 409 **`EXPORT_DOCS.SHIPMENT.NOT_INSPECTED`**(+`detail.status`).
- 상수는 L0(`trade_docs/constants.py`)에 두고 아키텍처 시험 2건으로 결속: ① `CI_ISSUABLE_SHIPMENT_STATES ⊆ RESERVED[SHIPMENT]`인 동안 "운영 경로(HTTP)에서 CI 발행 성공 0" — 실제 선적 생성·출고지시 경로로 만든 RELEASE_ORDERED 선적에 발행 시도 → 409(B 그룹, GC 후보 §A21) ② 술어에 PLANNED·RELEASE_ORDERED·PICKING이 들어가면 실패(변이 kill).
- **결과(명시)**: S4-2가 PICKING→INSPECTED 엣지를 열기 전까지 **프로덕션에서 CI·PL·S/I는 발행되지 않는다**. S3-3 DoD("교차 불일치 서류 저장 거부 / 무상(금액 0) 생성 가능")는 **서비스 층 시험**으로 충족한다 — 시험 전용 팩토리 `force_shipment_status_for_test`(raw SQL로 status·상태이력 1행 삽입, `backend/tests/factories/`에만 존재, `app/`에서 호출 0을 아키텍처 시험이 스캔)로 INSPECTED 선적을 만든 뒤 실제 발행 서비스를 호출한다. ADR-0081(L/C 산식 = 순수 함수·K 시험, 운영 경로 UNKNOWN)과 같은 구조다.
- **공백 완화**: QT·PI 렌더는 프로덕션에서 바로 쓴다. CI/PL/S-I는 runbook 'S3-3 운영 개시'에 "S4-2 전까지 CI·PL은 시스템 밖(기존 엑셀 양식)에서 작성, 선적 내부 메모에 `CI 외부 작성 <번호> <날짜>` 기록"을 적는다(부채 A-05, 트리거 = S4-2 INSPECTED 엣지 개방 → 그 PR이 runbook 문장 삭제).
- **미리보기(JSON, 저장 0)는 RELEASE_ORDERED부터 허용**하고 검증 결과 목록 첫 항목에 게이트 결과(`SHIPMENT_NOT_INSPECTED` BLOCK)를 싣는다 — 포장 데이터를 미리 맞춰 볼 수 있게 하되 PDF·XLSX·번호는 나오지 않는다.

**근거**
- §7.2 문면(`D:187`) "CI/PL 생성은 **검수완료 이후만** 허용", §20 B(`D:444`) "검수 미완료 선적의 CI·PL 생성 차단", S3-2 부기(`D:192` ①) "PICKING~CLOSED 5값은 RESERVED(S4-2)이며 **CI/PL 차단의 전제**(RESERVED 진입 0)", §20 S3-2 해석 주(`D:441` ①) "차단 본체는 **S3-3·S4-2**", 코드 주석 `code:modules/trade_docs/machine.py:77`("INSPECTED … CI/PL 허용 기준점"), `:129-137`(선적 RESERVED 5값).
- WBS v1.6 S4-2 주석(`W:155`) "검수 미완료 CI/PL 차단 본체는 이 세션(S4-2)", S4-2 DoD "검수 전 CI 생성 시도 거부"(`W:153`).
- 충돌: WBS S3-3 DoD는 CI 생성이 '쓸 수 있음'을 전제하는데, DESIGN 문면은 검수 전 생성을 금지하고 검수(INSPECTED)는 S4-2에서야 도달 가능하다.

**대안(기각)**
- (a) **S3-3은 RELEASE_ORDERED에서 CI/PL 허용, S4-2가 게이트 추가**(덜 엄격): S3-3~S4-2 구간 프로덕션이 DESIGN §7.2 문면을 위반한다. 그 구간에 발행된 CI는 '검수 없이 나간 CI'로 남아 S4-2가 소급 판정할 수 없다.
- (b) **사람 1클릭 '검수 완료(수기)' 엣지 RELEASE_ORDERED→INSPECTED를 S3-3이 개방**: INSPECTED의 의미(로트 대조·차이 차단 — §8.4)와 원장 시점(RELEASED)이 S4-2 소관이다. 원장 없는 INSPECTED 행이 쌓이면 S4-2가 그 행들을 이관·재판정해야 한다(되돌리기 비용 높음). PICKING을 건너뛰는 엣지도 S4-2 전이표와 충돌한다.
- (c) 워터마크 '검수 전 초안' PDF: §A1 (c)와 같은 이유.

**자율 확정 여부**: **자율 확정 — 사양 해석 충돌(멈춰서 보고 §10 ①)**. 더 엄격한 쪽(DESIGN 문면 우선 = 게이트 지금)으로 확정했다. ADR 후보 "CI·PL 검수 게이트 선배치·운영 경로 S4-2까지 닫힘".

**되돌리기 비용: 낮음**(상수 1개 + 시험 2건. (a)로 번복하면 상수에 RELEASE_ORDERED를 넣고 runbook 문장 삭제). 단 번복 후 발행된 CI는 소급 회수가 안 되므로 **번복 방향의 비용은 비대칭**이다.

---

## A3. 자사 레터헤드 마스터 `company_profiles` — 불변 판(版) 행 + 유효일 as-of (P-11·R-3a-5)

**결정**

| 열 | 타입 | NULL | 의미·CHECK |
|---|---|---|---|
| `id` | BIGINT PK | | 판 식별자 — CI가 FK로 고정 |
| `legal_name_en` | VARCHAR(200) | NOT NULL | 수출자(Shipper/Exporter) 영문 법인명. 보이는 글자 1↑·제어문자 0 |
| `legal_name_ko` | VARCHAR(200) | NULL | KO 변형(QT·PI) 표기 |
| `address_en` | VARCHAR(500) | NOT NULL | 여러 줄(탭·LF·CR만 허용 — `shipment_parties.address_en_clean` 식 승계, `code:modules/shipments/models.py:273-277`) |
| `address_ko` | VARCHAR(500) | NULL | KO 변형 |
| `business_reg_no` | CHAR(10) | NOT NULL | 사업자등록번호 숫자 10자리 CHECK `^[0-9]{10}$` + 서비스 체크섬 검증(422) |
| `phone`·`email` | VARCHAR(40)·VARCHAR(200) | NULL | 표기용 |
| `signer_name_en`·`signer_title_en` | VARCHAR(100)·VARCHAR(100) | NULL | 서명란. **둘 다 있거나 둘 다 없다**(CHECK) |
| `effective_from` | DATE | NOT NULL | 이 판이 적용되는 KST 날짜. 2000~2999 범위 CHECK, 서비스는 `≤ today_kst()`(미래 판 금지) |
| `created_at`·`created_by_id` | TIMESTAMPTZ·BIGINT FK users | NOT NULL | 행위자 |

- **IMMUTABLE**(`revoke_mutations` — INSERT·SELECT만). 수정 = 새 판 INSERT. 삭제 경로 없음. 유니크 `(effective_from, id)` 정렬로 as-of가 결정적이다.
- **as-of 규칙(한 함수)** `letterhead_as_of(session, day: date) -> CompanyProfile | None` = `effective_from ≤ day` 중 `(effective_from DESC, id DESC)` 첫 행. **없으면 None이고 대체 금지**(가장 이른 판·현재 판으로 메우지 않는다 — GC-A13 계보).
  - CI 발행: 발행일(`doc_date` = KST 오늘)의 as-of 판을 `company_profile_id`로 **고정 저장**(이후 판이 바뀌어도 그 CI는 그 판).
  - QT·PI 렌더: 동결 시각(`frozen_at`)의 KST 날짜 as-of. 없으면 409 **`EXPORT_DOCS.LETTERHEAD.NOT_EFFECTIVE`**(+`detail.as_of`) — 관리자가 **과거 `effective_from`으로 첫 판을 등록**하면 풀린다(명시적 사람 결정·audit). 첫 판 등록 전 레터헤드 0이면 CI도 409 `EXPORT_DOCS.LETTERHEAD.NOT_REGISTERED`.
- **수출자 당사자(R-3a-5) 해소 방식**: `shipment_parties`에 자사 SHIPPER 행을 만들지 않는다(수출 SHIPPER = 422 `ROLE_NOT_ALLOWED` 유지 — `code:modules/trade_docs/constants.py:190-200`). 서류의 Shipper/Exporter 블록 = 레터헤드 판. 자사를 `partners`에 넣는 방식은 기각(아래 (b)).
- 쓰기 = ADMIN(Idempotency-Key, audit `company_profiles.company_profile.created`), 읽기 = 전 역할. 로고 이미지는 이번에 두지 않는다(부채 A-06 — 이미지 업로드·서빙 보안 재판정 필요).

**근거**: WBS S3-3 "자사 레터헤드 마스터"(`W:258` ③ — v1.5 수정 목록), S3-1 부채 P-11(`docs/plans/s3-1/design-integrated.md:516`), S3-1 §7.3 부기 ③ "자사 레터헤드는 S3-3"(`D:196`), S3-2 부채 R-3a-5(`P:91`), §3 스냅샷 규율(발행된 서류는 마스터 변경을 소급하지 않는다 — ADR-0017·0056).

**대안(기각)**
- (a) 단일 행 UPDATE 마스터 + CI에 레터헤드 열 전부 복사: 동작은 같지만 QT·PI(이미 동결된 행에 열이 없다)는 렌더 시점 현재값을 쓰게 되어 **과거 서류가 새 주소로 다시 찍힌다**. 불변 판 + as-of가 열 복사 없이 같은 보증을 준다.
- (b) 자사를 `partners` 행(유형 SELF)으로: 거래처 유형 CHECK(§4.6 10종 열거) 문면 밖 값이고, 여신·DG·거래처 화면·CSV 왕복에 자사가 섞인다.
- (c) as-of 없음 → 가장 이른 판으로 대체: '그때 레터헤드'가 아닌 것을 그때 것처럼 찍는다(조용한 대체).

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 중간**(불변 표라 열 추가 = 새 판 규칙 유지한 채 ADD COLUMN NULL. 단일 행 모델로 되돌리면 CI FK 재지정 필요).

---

## A4. CI를 전표 커널에 편입한다 — DocKind `COMMERCIAL_INVOICE`, 접두어 `CI`, 상태 2값·사람 엣지 1

**결정**
- `DocKind.COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"`(18자). 커널 dict 11개에 행 추가(`code:modules/trade_docs/constants.py:26-113`): `DOC_PREFIXES "CI"` / `DOC_TABLES "commercial_invoices"` / `LINE_TABLES "commercial_invoice_lines"` / `STATUS_LOG_TABLES "commercial_invoice_status_log"` / `STATUS_LOG_FK "commercial_invoice_id"` / `LINE_HEADER_FK "ci_id"` / `HEADER_TOTAL_COLUMN "total_amount"` / `LINE_AMOUNT_COLUMN "line_amount"` / `EVENT_PREFIX "commercial_invoices.commercial_invoice"` / `PARTNER_COLUMN "buyer_partner_id"` / `FREEZE_COLUMN "frozen_at"`.
- **상태**: `CommercialInvoiceStatus` = `ISSUED`(발행 — 출생 상태, 생성=발행=동결, PI 선례) · `CANCELLED`. **사람 엣지 1개** `ISSUED→CANCELLED`(사유 필수 — `REASON_REQUIRED_TO`), 자동 엣지 0, 동결 액션 엣지 0, `EDITABLE_STATES[CI] = ∅`, `TERMINAL = {CANCELLED}`, `RESERVED = ∅`.
- **총수(이 부록 몫 증분만)**: 허용 30 → **31**(사람 18 → 19, 자동 12 그대로) / 미허용 152 → **153** / 총 182 → **184**쌍(CI 2상태 = 순서쌍 2: 허용 1·미허용 1). 채권 부록이 여는 SO COMPLETED·short-close 엣지 증분은 그 부록이 더한다 — 두 증분의 합이 `tests:architecture/test_doc_machines.py`의 최종 `EXPECTED`다(통합 문서가 합산 고정). 독스트링 `code:modules/trade_docs/machine.py:7` 함께 갱신.
- `approvals.target_type`·`gates.subject_type` CHECK는 넓히지 않는다(CI 승인·게이트 없음 — 선적 선례 `code:modules/trade_docs/constants.py:20-21`).
- **S/I는 커널에 넣지 않는다**(아래 대안 (b)) — 상태가 없는 불변 행(§A8)이라 `record_transition` 통로가 필요 없다.
- **PL은 별도 DocKind가 아니다** — CI의 구성 요소(번호 공유, 수명 = CI 상태 상속).

**근거**: 사슬 주석 `code:modules/trade_docs/chain.py:95` "CI/PL = S3-3이 `ChildLink(SHIPMENT, …)`를 더한다", §7.1 사슬(`D:177`) `선적 → CI/PL → 수출신고 → C/O → 채권`, ADR-0054(채번 = DocKind/DOC_PREFIXES 멤버 추가), ADR-0051(단일 전이 통로·상태이력 IMMUTABLE), S3-2 A1 선례(선적 편입). 커널에 넣어야 ① 선적 취소 역순 가드(살아 있는 CI) ② 채권의 CI 역순 가드 ③ 야간 합계 검산(`code:modules/trade_docs/verify.py:39-60`) ④ 상태이력 IMMUTABLE ⑤ 채번 KST 연도를 **재구현 없이** 승계한다.

**대안(기각)**
- (a) 커널 밖 독립 표: 위 ①~⑤ 재구현 = 우회 표면.
- (b) S/I도 DocKind: `TradeHeaderMixin`이 통화·결제조건·합계 열을 강제하는데 S/I에는 금액 축이 없다(빈 열·0 합계 핀이 생긴다). S/I 취소가 CI 취소를 막을 이유도 없다(§A8).
- (c) PL 별도 DocKind(`PL` 접두어): CI·PL이 다른 번호를 가지면 '어느 PL이 어느 CI의 짝인가'가 데이터가 되어 교차 불일치의 새 원천이 된다. 실무 PL은 Invoice No.를 그대로 쓴다.
- (d) CI 상태에 `SUPERSEDED` 추가: 재발행 계보는 `supersedes_ci_id`가 표현한다(§A11). 상태 하나 늘면 총수·전이·CHECK만 늘고 정보는 같다.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 높음**(DocKind 값은 상태이력·아웃박스 aggregate에 저장되는 값 — 분리 시 데이터 마이그레이션).

**파급 시험(갱신 의무)**: `tests:architecture/test_doc_machines.py`(EXPECTED·접두어·StrEnum↔machine↔DB CHECK 3자 대사), `test_doc_field_policy.py`(§A15 분류), `test_doc_status_log_contract.py`, `test_doc_status_channel.py`, `test_doc_chain_contract.py`(새 FK 전부 등록), `code:modules/trade_chain/chain_ops.py`의 `DOC_MODELS`·`ANCESTORS`.

---

## A5. `commercial_invoices` 헤더 — 사람 입력은 운송·원산지·서류 문구뿐, 나머지는 원천 스냅샷

`TradeHeaderMixin`(`code:modules/trade_docs/mixins.py:51-96`)에서 doc_number·doc_date·status·currency·fx_rate·fx_rate_date·결제조건 4열·Incoterms 3열·internal_note·last_line_no·assignee_id·copied_from_id가 온다. 그 위에 Timestamp·SoftDelete·Version·Actor 믹스인과 아래 열을 더한다. 분류 = `FIELD_POLICY`(ORIGIN = 원천 사본·발행 시 1회, INPUT* = 발행 본문의 사람 입력이지만 발행 후 ORIGIN과 같은 불변, SYSTEM, FREE).

| 열 | 타입 | NULL | 분류 | 원천·의미 |
|---|---|---|---|---|
| `shipment_id` | BIGINT FK shipments RESTRICT | NOT NULL | ORIGIN | 원천 선적(EXPORT만 — 서비스 422 `EXPORT_DOCS.SHIPMENT.KIND_NOT_SUPPORTED`) |
| `so_id` | BIGINT FK sales_orders RESTRICT | NOT NULL | ORIGIN | 선적 `so_id` 사본(채권·문서 흐름 축) |
| `buyer_partner_id` | BIGINT FK partners RESTRICT | NOT NULL | ORIGIN | 선적 `counterparty_partner_id` 사본 |
| `company_profile_id` | BIGINT FK company_profiles RESTRICT | NOT NULL | ORIGIN | 발행일 as-of 레터헤드 판(§A3) |
| `consignee_partner_id` | BIGINT FK partners | NOT NULL | ORIGIN | 선적 CONSIGNEE 자동 행 사본 |
| `consignee_name_en` | VARCHAR(200) | NOT NULL | ORIGIN | 〃 `name_en` |
| `consignee_address_en` | VARCHAR(500) | NOT NULL | ORIGIN/INPUT* | 〃 `address_en`. **스냅샷이 NULL일 때만** 본문 `consignee_address_en`으로 채움(덮어쓰기 금지) |
| `consignee_address_source` | VARCHAR(8) | NOT NULL | SYSTEM | `SNAPSHOT`·`MANUAL` CHECK |
| `notify_partner_id`·`notify_name_en`·`notify_address_en` | BIGINT·VARCHAR(200)·VARCHAR(500) | NULL | ORIGIN | 선적 NOTIFY 행(있을 때). id·이름은 둘 다 있거나 둘 다 없음 CHECK |
| `buyer_po_no`·`buyer_po_date` | VARCHAR(60)·DATE | NULL | ORIGIN | SO 사본(`code:modules/sales_orders/models.py:103-105`) |
| `origin_country_code`·`dest_country_code` | CHAR(2) | NOT NULL | ORIGIN | 선적 사본 |
| `transport_mode` | VARCHAR(12) | NOT NULL | INPUT* | `SEA`·`AIR`·`ROAD`·`RAIL`·`COURIER`·`MULTIMODAL` CHECK |
| `port_of_loading`·`port_of_discharge` | VARCHAR(100) | NOT NULL | INPUT* | 보이는 글자 1↑·제어문자 0 |
| `final_destination`·`vessel_voyage` | VARCHAR(100) | NULL | INPUT* | 〃 |
| `sailing_on` | DATE | NULL | INPUT* | 'on or about' — 본문 기본값 제시 없음(화면이 ETD 계획·실적을 보여 주고 사람이 고른다) |
| `goods_origin_country_code` | CHAR(2) | NOT NULL | INPUT* | 헤더 원산국 기본값 — **서버 기본값 없음**(본문 필수). 라인이 같은 값 또는 개별값을 가진다 |
| `shipping_marks` | VARCHAR(1000) | NULL | INPUT* | 여러 줄 — PL·S/I가 같은 값을 인쇄 |
| `remarks` | VARCHAR(1000) | NULL | INPUT* | 서류 문구(여러 줄) |
| `bank_account_id` + 6열(`bank_beneficiary_name`·`bank_beneficiary_address`·`bank_name`·`bank_address`·`bank_account_no`·`bank_swift_code`) | PI 은행 스냅샷과 같은 타입 | NULL | ORIGIN | 원천 규칙 §A9-⑧. **7열 all-or-none** CHECK, `payment_type IN ('TT_ADVANCE','TT_DEFERRED') AND total_amount > 0 ⇒ 존재` CHECK, SWIFT 형식 CHECK(`SWIFT_PATTERN`) |
| `total_amount` | BIGINT | NOT NULL | ORIGIN | Σ 라인 `line_amount`(= 선적 `total_amount` — 검증 V2) |
| `template_version` | SMALLINT | NOT NULL | SYSTEM | 발행 시점 CI·PL 템플릿 판(§A12) — 이후 렌더는 이 판으로만 |
| `acknowledged_notices` | VARCHAR(40)[] | NOT NULL DEFAULT '{}' | SYSTEM | 사람이 확인한 NOTICE 코드(§A10) |
| `supersedes_ci_id` | BIGINT FK commercial_invoices RESTRICT | NULL | ORIGIN | 재발행 계보(§A11) — 부분 유니크(한 CI의 후속 판은 1개) |
| `revision_no` | SMALLINT | NOT NULL | SYSTEM | 1부터. CHECK `(revision_no = 1) = (supersedes_ci_id IS NULL)` |
| `frozen_at` | TIMESTAMPTZ | NOT NULL | SYSTEM | 발행 시각(생성=동결) |

- 믹스인 열 처리: `doc_date` = 발행 TX의 `today_kst()` 1회(인보이스 일자 — 대금만기 `INVOICE_DATE`의 원천, §0 계약). `currency`·`fx_rate`·`fx_rate_date`·결제조건 4열·Incoterms 3열 = 선적 사본(선적 CHECK `source_terms_complete`가 완결을 보증 — `code:modules/shipments/models.py:128-132`). `copied_from_id`는 CHECK `IS NULL`(복제 경로 없음 — 재발행 계보는 `supersedes_ci_id`). `assignee_id` = 선적 담당자 사본(FREE — 라우팅용), `internal_note` FREE.
- **CHECK(요지)**: `header_common_checks(COMMERCIAL_INVOICE)`(`code:modules/trade_docs/mixins.py:173`) / 상태 2값 / `frozen_at IS NOT NULL` / `doc_number ~ '^CI-[0-9]{4}-[0-9]{4,}$'` + 전역 UNIQUE / `UNIQUE(id, currency)`(라인 복합 FK 대상) / 국가 형식 / `deleted_at IS NULL`(전표는 삭제 경로 없음 — CHECK로 고정) / `copied_from_id IS NULL`.
- **유니크**: **선적당 살아 있는 CI 1건** — `uq_commercial_invoices_shipment_id_live ON (shipment_id) WHERE status <> 'CANCELLED'`(위반 409 `EXPORT_DOCS.CI.ALREADY_ISSUED`). 부분 인보이스(한 선적을 여러 CI로)는 없다 — 선적을 나누면 된다(부분선적 1:N이 이미 있다).
- **UPDATE 권한 = 열 단위**: `restrict_update_columns(op, "commercial_invoices", {status, internal_note, assignee_id, version, updated_at, updated_by_id})` — S3-1 전표 헤더 4종은 앱 계정 UPDATE를 회수할 수 없었지만(`D:378`) CI는 편집 경로가 0이라 **DB가 스냅샷 열 UPDATE를 42501로 거부**한다(`code:core/db/table_policy.py:189-213,239-248` approvals 선례). DELETE·TRUNCATE 회수.

**근거**: §7.6 "선적/전표 한 원천"(`D:215`), §3 스냅샷 규율, S3-2 선적 헤더 스냅샷 선례(`code:modules/shipments/models.py:94-132`), PI 은행 스냅샷 선례(`code:modules/proforma_invoices/models.py:84-92`), §17.5(`D:374`) "가능한 불변식은 CHECK", ADR-0053(동결·불변).

**대안(기각)**: (a) 운송 정보(항구·선명)를 선적 헤더 열로 — 선적은 출고지시 후 CONTENT 동결이라 롤오버로 바뀌는 선명을 담으려면 FREE 열을 늘려야 하고(FREE 4열 핀 개정), 서류와 선적이 다른 값을 가질 때 어느 쪽이 진본인지 답이 없다. **발행된 CI가 그 서류 세트의 진본**이고 바뀌면 재발행한다. (b) 스냅샷 블록을 JSONB 1열로 — CHECK·FK·채권의 열 소비가 불가해진다. (c) 원산국 서버 기본값(= 선적 출발국) — 원산지는 §15 법적 판정 영역(L3 금지)이라 시스템이 채우지 않는다.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 중간**(열 추가는 NULL ADD로 낮음. 열 단위 UPDATE 회수를 되돌리면 GRANT 1줄이지만 반대 방향[나중에 회수]은 기존 우회 쓰기 감사가 필요).

---

## A6. `commercial_invoice_lines` — 선적 라인 1:1, 설명·HS·원산지·DG 스냅샷 (불변)

| 열 | 타입 | NULL | 원천·의미 |
|---|---|---|---|
| `ci_id` + `currency` | BIGINT·CHAR(3) | NOT NULL | 복합 FK → `commercial_invoices(id, currency)`(혼합 통화 불가 — S3-1 라인 규약) |
| `line_no` | INTEGER | NOT NULL | 선적 라인 `line_no` 순서대로 1부터 재부여(결번 없음) |
| `shipment_line_id` | BIGINT FK shipment_lines | NOT NULL | 원천 선적 라인. `UNIQUE(ci_id, shipment_line_id)` |
| `so_line_id` | BIGINT FK sales_order_lines | NOT NULL | 선적 라인 사본 |
| `sku_id`·`sku_code`·`sku_kind` | | NOT NULL | 선적 라인 사본 |
| `description_en` | VARCHAR(300) | NOT NULL | 기본 = 선적 라인 `sku_name_en` 사본. 본문 `description_en`으로 대체 가능(세관용 일반 품명 관행) |
| `description_source` | VARCHAR(8) | NOT NULL | `SNAPSHOT`·`MANUAL` — 스냅샷이 NULL이면 본문 필수(422), 대체 시 MANUAL |
| `buyer_item_code` | VARCHAR(100) | NULL | SO 라인 사본(바이어 품번 — 원천 규칙 그대로, 재추정 0) |
| `sku_hs_code_id`·`hs_code`·`hs_version` | BIGINT FK sku_hs_codes·VARCHAR(12)·VARCHAR(10) | NULL | **사람이 고른** 등록 HS 행의 사본. 셋 다 있거나 다 없음 CHECK. 서버 자동 선택 0(§A9-⑥) |
| `origin_country_code` | CHAR(2) | NOT NULL | 본문 라인값 또는 헤더 `goods_origin_country_code` — 둘 다 사람 입력 |
| `quantity` | INTEGER | NOT NULL | 선적 라인 수량 사본(1..`MAX_QUANTITY`) |
| `unit_price_amount` | BIGINT | NOT NULL | 선적 라인 단가 사본(0 허용 — 무상) |
| `is_free` | BOOLEAN | NOT NULL | 사본. CHECK `is_free = (unit_price_amount = 0)` |
| `line_amount` | BIGINT | NOT NULL | CHECK `quantity::numeric * unit_price_amount = line_amount`(R-04 numeric 곱 — 오버플로 500 방지) |
| `dg_flag`·`un_number`·`dg_class`·`packing_group`·`is_limited_quantity`·`is_aerosol` | | | **발행 시점 SKU 마스터 1회 읽기**(선적 라인에 DG 사본이 없다). CHECK `NOT dg_flag ⇒ DG 4열 NULL·LQ false` |

- **IMMUTABLE**(`revoke_mutations`). Timestamp·Actor만, Version 없음. `deleted_at` 열은 `verify.py`가 라인 `deleted_at IS NULL`을 읽으므로(`code:modules/trade_docs/verify.py:52-55`) 두되 CHECK `deleted_at IS NULL`.
- **선적 라인 집합 = CI 라인 집합**(검증 V11): 선적의 살아 있는 라인마다 정확히 1행, 수량 동일. 부분 인보이스 없음(§A5).
- DG 스냅샷을 CI 라인에 두는 이유: PL·S/I가 **CI 라인을 원천**으로 DG를 인쇄한다(한 원천). 선적 라인에 DG 열을 더하는 안은 선적 표 개정(M14 범위 재진입)이라 기각.

**근거**: `code:modules/shipments/models.py:163-242`(선적 라인 — `sku_name_en` NULL 허용 `:184`, 단가 사본 `:189-190`), S3-1 스냅샷 2함수 규율(`code:modules/trade_docs/snapshot.py:1-12` — 참조 생성은 선행 값 복사·마스터 재조회 금지, 수기 추가는 마스터 1회 읽기), §4.1 DG 속성(`D:96`), DG 완결성은 선적 시점 게이트(`code:modules/catalog/models.py:150-158` 주석), §15 HS 자동 판정 금지(`tests:architecture/test_no_hs_auto_classification.py:1-40`).

**대안(기각)**: (a) `description_en` NULL이면 `sku_name_ko`로 대체 — 영문 통관 서류에 한글 품명이 조용히 들어간다. (b) HS 1건이면 자동 선택 — 어느 나라 HS(수출국·도착국)를 쓸지가 판단이다(L3 금지 영역). (c) DG를 렌더 시점 마스터에서 읽기 — 발행 후 마스터 수정이 과거 서류를 바꾼다.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 중간**(불변 표라 열 추가만 가능 — 기존 행은 NULL).

---

## A7. PL — 포장 그룹·그룹 내용물 2표 (Q-05 해소)

**결정**

`packing_list_packages`(그룹 = 번호 연속 구간의 동일 포장)

| 열 | 타입 | NULL | CHECK·의미 |
|---|---|---|---|
| `ci_id` | BIGINT FK commercial_invoices | NOT NULL | PL은 CI의 구성 요소 |
| `seq` | INTEGER | NOT NULL | 1부터. `UNIQUE(ci_id, seq)` |
| `package_type` | VARCHAR(10) | NOT NULL | `CARTON`·`PALLET`·`CRATE`·`DRUM`·`BAG`·`OTHER` CHECK |
| `mark_from`·`mark_to` | INTEGER | NOT NULL | 포장 번호 구간(C/T No. 1-20). CHECK `1 ≤ mark_from ≤ mark_to ≤ 99999` |
| `net_weight_g_per_pkg` | BIGINT | NOT NULL | 포장 1개 순중량(그램 정수). CHECK `> 0` |
| `gross_weight_g_per_pkg` | BIGINT | NOT NULL | CHECK `gross_weight_g_per_pkg >= net_weight_g_per_pkg`(**§7.6 G.W.≥N.W. — DB 층**) |
| `length_mm`·`width_mm`·`height_mm` | INTEGER | NOT NULL | CHECK 각 `1..100000` |

`packing_list_package_items`(그룹 내 SKU 구성 — 구간 내 모든 포장이 같은 구성)

| 열 | 타입 | NULL | CHECK·의미 |
|---|---|---|---|
| `package_id` | BIGINT FK packing_list_packages | NOT NULL | |
| `ci_line_id` | BIGINT FK commercial_invoice_lines | NOT NULL | `UNIQUE(package_id, ci_line_id)` |
| `quantity_per_pkg` | INTEGER | NOT NULL | CHECK `1..MAX_QUANTITY` |

- 두 표 **IMMUTABLE**. 파생값(저장 0): 포장 수 = `mark_to − mark_from + 1`, 그룹 N.W./G.W. = 포장 수 × 1개 값, 부피(cm³ 정수) = 포장 수 × L×W×H ÷ 1000 → 표시 CBM = m³ 소수 3자리(HALF_UP — 표시 전용 상수 `CBM_DISPLAY_ROUNDING`), 합계 = Σ 그룹. 중량 표시 = kg 소수 3자리(정확 — 그램 정수 ÷ 1000).
- 교차 검증(§A10 V5): CI 라인마다 `Σ_그룹(quantity_per_pkg × 포장 수) = CI 라인 수량`, PL에 없는 CI 라인 0, 포장 번호 구간은 1부터 **빈틈·겹침 없이 연속**(서비스 검증 — `btree_gist` EXCLUDE는 확장 설치 권한이 필요해 기각), 그룹당 내용물 1행↑.
- 포장 데이터는 **발행 본문**으로만 들어온다(무상태 미리보기 → 발행 2단 — S3-1 PI·PO preview 선례). 편집 가능한 '포장 초안' 표는 두지 않는다(아래 (b)).
- SKU 마스터 `unit_weight_g`·`box_qty`(`code:modules/catalog/models.py:137,139`)는 **화면 참고값**(예상 N.W.·예상 상자 수)으로만 응답에 싣고 검증에 쓰지 않는다 — 마스터 중량이 순중량인지 문면이 정하지 않았고 실측 계량이 진본이다(허용 오차 발명 금지 — 부채 A-07).

**근거**: §7.6 "G.W.≥N.W."·"CI↔PL 교차 일치"(`D:215`), §20 B "G.W.<N.W. 거부"(`D:444`) — 등호는 통과, S3-2 부채 Q-05 "중량·CBM·박스 열 — S3-3 PL, 트리거 PL 생성 착수"(`P:59`), 금액·수량 정수 규율(ADR-0003 — 중량·치수도 정수 최소단위).

**대안(기각)**: (a) 중량·CBM을 선적 라인 열로 — 선적 라인은 PLANNED에서만 편집(이후 동결)인데 포장은 피킹·검수 뒤에 확정된다. 또 혼합 포장(한 상자 여러 SKU)을 라인 열로 표현할 수 없다. (b) 가변 '포장 초안' 표 — 발행 전 상태를 하나 더 만들고 동시 편집·잠금·정리 규칙이 생긴다. 무상태 본문 + 미리보기로 충분하다(화면이 브라우저 임시 저장으로 입력 유실을 막는다 — 화면 부록). (c) 그룹 총중량 저장 — 포장 수 × 1개 값과 이중 진실. (d) 중량 Decimal(kg) — float/Decimal 혼용 위험, 정수 그램이 정확하다.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 중간**(불변 표 — 열 추가는 NULL 허용으로만).

---

## A8. S/I — `shipping_instructions`: 상태 없는 불변 발행 기록, 채번 `SI`, 살아 있는 CI 세트가 원천

**결정**

| 열 | 타입 | NULL | 원천·CHECK |
|---|---|---|---|
| `doc_number` | VARCHAR(20) | NOT NULL | `^SI-[0-9]{4}-[0-9]{4,}$` 전역 UNIQUE, `numbering.next_document_number(session, "SI")`(KST 연도·행 잠금 — `code:modules/numbering/service.py:24-56`). 접두어 상수 `SHIPPING_INSTRUCTION_PREFIX = "SI"`(L0) |
| `ci_id` | BIGINT FK commercial_invoices | NOT NULL | 발행 시 **살아 있는** CI(아니면 409 `EXPORT_DOCS.SI.CI_NOT_LIVE`) |
| `shipment_id` | BIGINT FK shipments | NOT NULL | CI 사본 |
| `doc_date` | DATE | NOT NULL | KST 오늘 1회 |
| `forwarder_partner_id`·`forwarder_name_en`·`forwarder_address_en` | | id·이름 NOT NULL | 선적 FORWARDER 당사자 사본 — 없으면 422 `EXPORT_DOCS.SI.FORWARDER_MISSING` |
| `consignee_mode` | VARCHAR(16) | NOT NULL | `PARTY`·`TO_ORDER`·`TO_ORDER_OF` CHECK. `PARTY` = CI 수하인 그대로 |
| `consignee_order_of` | VARCHAR(200) | NULL | CHECK `(consignee_mode = 'TO_ORDER_OF') = (consignee_order_of IS NOT NULL)` |
| `notify_name_en`·`notify_address_en` | | NULL | CI NOTIFY 사본. CHECK `consignee_mode = 'PARTY' OR notify_name_en IS NOT NULL`(지시식이면 통지처 필수) |
| `freight_term` | VARCHAR(8) | NOT NULL | `PREPAID`·`COLLECT` — **Incoterms에서 결정적 도출**(E·F군 → COLLECT, C·D군 → PREPAID). 본문 필드 없음 |
| `bl_type` | VARCHAR(12) | NOT NULL | `ORIGINAL`·`SURRENDER`·`SEA_WAYBILL`·`AIR_WAYBILL` CHECK. 운송 방식 정합: AIR ⇔ `AIR_WAYBILL`(CHECK는 CI 열이라 서비스 검증) |
| `original_bl_count` | SMALLINT | NULL | CHECK `(bl_type = 'ORIGINAL') = (original_bl_count BETWEEN 1 AND 3)` 아니면 NULL |
| `has_dg` | BOOLEAN | NOT NULL | CI 라인 DG 사본에서 도출 — DG 라인은 UN 번호·Class 필수(422 `EXPORT_DOCS.VALIDATION.FAILED` 항목 `DG_DATA_INCOMPLETE`), PDF에 DG 블록 + 고정 문구 "Shipper's Declaration for Dangerous Goods (DGD) to be submitted separately by the shipper." |
| `special_instructions` | VARCHAR(1000) | NULL | 여러 줄 |
| `template_version` | SMALLINT | NOT NULL | §A12 |
| `supersedes_si_id` | BIGINT FK self | NULL | 같은 CI의 직전 S/I. 부분 유니크(후속 1개) |
| `created_at`·`created_by_id` | | NOT NULL | |

- **IMMUTABLE**. 상태 열 없음 — '현재 S/I' = 살아 있는 CI의 S/I 중 후속이 없는 행(파생). CI가 취소되면 그 CI의 S/I는 전부 '무효(CI 취소)'로 **파생 표시**된다(저장 0).
- **CI 취소를 막지 않는다**: S/I는 포워더 전달용 작업 서류이지 상거래 약정이 아니다 — 정정 경로(CI 취소 → 재발행 → S/I 재발행)를 막으면 안 된다. 그래서 커널 사슬(`CHILD_LINKS`)에 넣지 않고 `NON_CHILD_FK_ALLOWLIST`에 사유와 함께 등재한다(§A15).
- **`TO ORDER` 계열(Q-06)**: `consignee_mode ≠ PARTY`는 선적 `payment_type = 'LC'` **그리고** L/C 부록의 `lc_allows_order_consignee(...)`가 True일 때만(아니면 422 `EXPORT_DOCS.SI.CONSIGNEE_MODE_NOT_ALLOWED`). 함수 미제공·조회 실패 = False(fail-closed). CI의 수하인은 언제나 바이어(지시식은 B/L·S/I 영역).
- 대외 발송 0: S/I 발행은 파일을 만들 뿐이다. 포워더 전달은 사람이 내려받아 보내고, 원하면 선적 통보 기록(S3-2 comm_logs SHIPMENT 통로)에 남긴다(§15 L3 '대외 최초 발송' 금지 — `D:329`).
- **B/L draft 대조(S5-3) 계약**: 대조 기준값 = 현재 S/I + 그 CI/PL(당사자·품명·수량·중량·포트·문구 — `D:215`). S/I가 불변 행이라 대조 시점의 기준이 흔들리지 않는다.

**근거**: §7.6 "S/I 생성 + B/L draft 업로드 → 대조"(`D:215`), §7.7 "S/I에 DG 자동 기입"(`D:217`), §7.9 "포워더 — S/I 전달"(`D:221`), 접두어 예약 `code:modules/trade_docs/constants.py:31`(`SI`는 S3-3 Shipping Instruction), ADR-0074 기각 대안(수입 접두어 `SI` 충돌), S3-2 부채 Q-06(`P:60`).

**대안(기각)**: (a) S/I DocKind(§A4 (b)). (b) 운임 조건 사람 입력 — Incoterms와 모순되는 조합(FOB + PREPAID)이 조용히 나간다. 예외 관행(판매자 대납 후 청구)은 수요가 생기면 사유 필수 override로 연다(부채 A-08). (c) DG 선적 S/I 차단(S4-4까지) — CI/PL과 같은 게이트를 이미 통과한 선적의 S/I를 막을 근거가 문면에 없고, DG 정보가 **빠진** S/I보다 **명시된** S/I가 안전하다. MSDS 유효본 게이트는 S4-4(§7.7). (d) 지시식 수하인 자유 허용 — L/C 근거 없는 지시식 B/L은 바이어 인수 분쟁 원천.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 낮음~중간**(불변 표 열 추가는 NULL로만. DocKind 편입으로 바꾸면 중간).

---

## A9. 원천 스냅샷 규칙표 — 필드별 원천·복사 시점·결측 시 동작 (대체 금지)

| # | 서류 필드 | 원천 | 복사 시점 | 결측·모호 시 |
|---|---|---|---|---|
| ① | 통화·환율·결제조건 4·Incoterms 3·거래 상대·국가 | 선적 헤더(이미 SO 사본) | CI 발행 TX | 선적 CHECK가 완결 보증 — 결측 불가 |
| ② | 품번·품명(EN)·수량·단가·무상·금액 | 선적 라인 | 〃 | 품명 EN NULL → 본문 `description_en` 필수(없으면 V9 BLOCK) |
| ③ | 바이어 PO 번호·일자, 바이어 품번 | SO 헤더·SO 라인 | 〃 | NULL 그대로(인쇄 생략) |
| ④ | 수하인 이름·주소 | 선적 CONSIGNEE 자동 행(`code:modules/shipments/models.py:257-263`) | 〃 | 주소 NULL → 본문 보충(MANUAL 기록), 없으면 V9 BLOCK. **비NULL 덮어쓰기 금지** |
| ⑤ | 통지처 | 선적 NOTIFY 행 | 〃 | 없으면 인쇄 생략(S/I 지시식이면 필수) |
| ⑥ | HS 코드 | 사람이 고른 `sku_hs_codes.id`(그 SKU 소속 검증, 살아 있는 행) | 〃 | 미선택 = NULL(인쇄 생략). **서버 후보 자동 선택 0** |
| ⑦ | 원산국 | 본문(헤더 기본 + 라인 개별) | 〃 | 본문 누락 422 — 서버 기본값 0 |
| ⑧ | 은행 7열 | **SO가 PI를 참조하면 그 PI의 은행 스냅샷**(`code:modules/proforma_invoices/models.py:84-92`), 아니면 본문 `bank_account_id`(통화 일치·살아 있는 행 — `code:modules/bank_accounts/models.py:28-40`) | 〃 | TT·금액>0인데 둘 다 없음 → V10 BLOCK. **통화가 같은 계좌가 1개여도 자동 선택 0**(사람 지정) |
| ⑨ | DG 6열 | SKU 마스터(`code:modules/catalog/models.py:158-170`) | 〃(1회 읽기) | S/I는 DG 라인 UN·Class 필수, CI·PL은 있는 그대로 인쇄 |
| ⑩ | 레터헤드 | `company_profiles` as-of(발행일) | 〃 | 없음 → 409 |
| ⑪ | 포워더(S/I) | 선적 FORWARDER 행 | S/I 발행 TX | 없음 → 422 |
| ⑫ | 운송·포트·선명·출항일·화인·비고 | 본문(사람) | CI 발행 TX | 필수 열 누락 422 |
| ⑬ | 출항일 참고값 | 마일스톤 ETD 계획·실적(S3-2 보드) | 미리보기 응답 | **응답 참고값만** — 본문에 넣지 않으면 NULL(자동 기입 0) |

- 원칙: **참조 원천(선적·SO·PI)은 복사, 마스터(SKU DG·HS·레터헤드·은행)는 발행 시 1회 읽기, 판단(원산지·HS 선택·운송 사실)은 사람 입력**. 어떤 결측도 다른 값으로 메우지 않는다(GC-A13 계보 — "다른 날짜·0·현재값으로 대체 금지").
- 주소·이름 문자 규약: 이름 = 보이는 글자 1↑·제어문자 0, 주소·여러 줄 = 탭·LF·CR만 허용(`shipment_parties` CHECK 승계), 그리고 렌더 폰트가 그리지 못하는 글자는 V12 BLOCK(§A12).

**근거**: §3 스냅샷 규율, ADR-0056(스냅샷·마스터 보완), S3-1 `buyer_item_code` 규칙(`code:modules/trade_docs/snapshot.py:9-10` — 모호하면 사람이 지정, 추측 선택 금지), §15 L3 금지(법적 판정 — HS·원산지), S3-2 부채 R-3b-8/P-44(거래처 영문 편집 경로 부재 — `P:107`)는 ④의 '본문 보충'이 CI 한정 탈출로가 된다(마스터 편집 경로는 여전히 부채).

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 낮음**(규칙은 서비스 코드 — 스키마는 §A5·A6 열).

---

## A10. 저장 전 검증 카탈로그 — 순수 함수, BLOCK은 거부·NOTICE는 사람 확인 필수(조용한 통과 0)

**결정**
- 위치: `trade_docs/doc_validation.py`(L0 — DB·모델 무임포트, 입력 = 발행 후보 데이터클래스, 출력 = `[ValidationItem(code, severity, path, message)]`, 순서 결정적). 미리보기·발행이 **같은 함수**를 부른다(화면·서버 이중 판정 금지 — S3-2 `schedule.py` 순수성 시험 선례 `tests:architecture/test_schedule_purity.py`).
- 결과 2등급: **BLOCK**(하나라도 있으면 발행 422 `EXPORT_DOCS.VALIDATION.FAILED` + `detail.items`) / **NOTICE**(발행 본문 `acknowledge_notices`에 해당 코드가 **전부** 있어야 통과, 아니면 같은 422 + 미확인 목록. 확인한 코드는 `acknowledged_notices`에 저장). override 없음.

| ID | 검증 | 등급 | 근거 |
|---|---|---|---|
| V1 | 라인 `수량 × 단가 = 금액`(정수 정확 — numeric) | BLOCK | §7.6 |
| V2 | `Σ 라인 금액 = 헤더 total_amount = 선적 total_amount` | BLOCK | §7.6 '라인합=총액' |
| V3 | 그룹마다 G.W. ≥ N.W.(등호 통과)·N.W. > 0, 합계도 동일 | BLOCK | §7.6·§20 B |
| V4 | Incoterms 완전성: 3열 존재·장소 보이는 글자 1↑·판↔코드(DAT=2010·DPU=2020) | BLOCK | §7.6, S3-1 부기 "완전성 판정은 S3-3"(`D:179` ⑤) |
| V4b | 해상 전용 4규칙(FAS·FOB·CFR·CIF)인데 `transport_mode ≠ SEA` | **NOTICE**(`INCOTERMS_MODE_MISMATCH`) | Incoterms 판 규정(해상·내수로 전용). BLOCK이면 동결된 SO 조건 때문에 영구 발행 불가 + 운송 방식 허위 입력 유인 → 확인 필수 경고 |
| V5 | CI↔PL: SKU 라인 집합 일치·라인별 Σ(포장당 수량 × 포장 수) = CI 수량·그룹당 내용물 1↑ | BLOCK | §7.6·§20 B |
| V6 | 포장 번호 1부터 빈틈·겹침 없이 연속 | BLOCK | 교차 일치의 전제(총 포장 수 = 최대 번호) |
| V7 | 무상: 총액 0 허용, `is_free ⇔ 단가 0` | (통과 규칙) | WBS DoD "무상(금액 0) 생성 가능" — 0을 거부하는 검증을 두지 않는다는 단언 |
| V8 | 선적 상태 ∈ `CI_ISSUABLE_SHIPMENT_STATES`·구분 EXPORT | BLOCK(발행은 409/422로 먼저 끊고 미리보기는 항목으로 표시) | §A2 |
| V9 | 영문 완결: 품명 EN(스냅샷 또는 본문)·수하인 주소·레터헤드 존재 | BLOCK | §A9 ②④⑩ |
| V10 | 은행: TT·총액>0 ⇒ 은행 7열, 계좌 통화 = CI 통화 | BLOCK | §A9 ⑧ |
| V11 | 선적 살아 있는 라인 집합 = CI 라인 집합(수량 동일) | BLOCK | 부분 인보이스 없음(§A5) |
| V12 | 렌더 폰트 글리프 커버리지(모든 인쇄 문자열) | BLOCK(`GLYPH_UNSUPPORTED` + 필드·코드포인트) | §A12 — 발행 후 렌더 실패 방지 |
| V13 | DG 라인 존재 | **NOTICE**(`DG_MANUAL_CHECK`) — DGD·MSDS 게이트가 S4-4라 runbook 3항 수동 확인을 했다는 사람 확인 | §7.7, S3-2 부채 Q-17(`P:71`) |
| V14 | (S/I) 포워더 존재·DG 라인 UN·Class·지시식 허용·B/L 종류↔운송 방식 | BLOCK | §A8 |

- Incoterms 판정 함수는 기존 `code:modules/trade_docs/incoterms.py`(L0)에 `incoterms_completeness(code, place, year, transport_mode) -> list[ValidationItem]`로 가산한다(형식 검증과 같은 모듈 — 이중 정의 금지).
- "반올림 상수"(§7.6): 금액은 정수 최소단위라 곱·합에 반올림이 없다. 반올림은 **표시 전용** 2곳(CBM 소수 3자리 HALF_UP, 통화 표시는 `minor_units` 자릿수 그대로 — `code:core/money.py:20,73-80`)뿐이고 둘 다 상수로 고정한다. 렌더러는 float를 쓰지 않는다(K 시험 — AST에서 `float(`·`/` 금액 연산 스캔).

**근거**: §7.6(`D:215`), §20 B(`D:444`), WBS DoD "교차 불일치 서류 저장 거부 / 무상(금액 0) 생성 가능"(`W:130`), S3-1 게이트 'UNKNOWN은 통과가 아니다'·조용한 통과 금지(`D:200` ②).

**대안(기각)**: (a) WARN(진행·기록만) — 사람이 보지 않아도 통과한다(조용한 통과). (b) V4b BLOCK — 위 표 사유. (c) 관리자 override — 검증 '강제'(§7.6) 문면과 충돌하고, 잘못된 서류를 통과시키는 표면이 된다. (d) N.W. 마스터 대조 BLOCK(§A7 사유 — 허용 오차 발명).

**자율 확정 여부**: 자율 확정(V4b·V13 NOTICE 등급은 '더 엄격'의 예외 — 사유: BLOCK이 허위 입력을 유인하거나 S4-4 몫을 선점한다. NOTICE도 사람 확인 없이는 통과 0이라 fail-closed는 유지). **되돌리기 비용: 낮음**(순수 함수 표 — 등급 변경은 상수+시험).

---

## A11. 채번·버전·재발행·재출력

**결정**
- **채번**: CI = `issue_document_number(session, DocKind.COMMERCIAL_INVOICE)`(`code:modules/trade_docs/doc_number.py:17-19`), S/I = `next_document_number(session, "SI")`. 둘 다 **발행 TX의 마지막 단계**(모든 검증·행 구성 뒤, LOCK_ORDER `doc_number_seq` 항상 마지막 — §17.3 `D:366-368`). 롤백 시 번호도 돌아간다(결번 0). PL·렌더 산출물은 채번하지 않는다(PL은 CI 번호를 인쇄).
- **재발행 = 새 번호**: `POST /commercial-invoices/{id}/reissue`(Idempotency-Key, 사유 필수) = **한 TX**에서 ① 기존 CI `ISSUED→CANCELLED`(사유 = 사람 입력 + 자동 접미 "재발행 {새 번호}") ② 새 CI 발행(검증 전건 재실행, `supersedes_ci_id` = 기존, `revision_no` = 기존+1) — S3-1 QT 개정 선례(복제+원본 취소 한 TX, `D:190` ⑥). 같은 번호에 'Rev.1'을 붙이는 방식은 ADR-0054(전역 UNIQUE·재발급 금지) 위반이라 기각. 새 CI PDF에 "This invoice supersedes Invoice No. {이전 번호}" 문구를 인쇄한다.
- **단순 취소**: `POST /commercial-invoices/{id}/transitions {to: CANCELLED, reason}`. 가드: 채권 부록이 `ChildLink(CI→receivables)`를 등록하면 살아 있는 채권이 있을 때 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(커널 공통). 통관 기록(수출신고)이 살아 있어도 **막지 않는다** — 통관 기록은 CI 번호를 FK로 갖지 않는 사실 기록이고, 정정 신고는 시스템 밖 절차다(취소 detail에 `customs_records_alive: n` 경고 표기).
- **S/I 재발행** = 새 S/I 행(새 번호, `supersedes_si_id`). 이전 S/I는 '대체됨'(파생).
- **재출력(reprint)** = 저장된 렌더 파일을 다시 내려받기 — 바이트 동일(§A13). 새 템플릿 판으로 다시 찍는 경로는 없다(§A12).

**근거**: §17.3(`D:366-368`), ADR-0053(정정 = 취소+신규), ADR-0054, §7.2 S3-1 부기 ⑥(`D:190`).

**대안(기각)**: (a) 같은 번호 개정판 — 위. (b) 재발행을 '취소 후 새 발행' 2요청으로 — 그 사이 선적에 살아 있는 CI 0인 창이 생기고(채권·대금만기 앵커가 흔들림), 두 번째 요청 실패 시 CI 없는 상태로 남는다. (c) S/I도 상태+취소 — §A8 사유.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 낮음**(번호 체계는 신규분부터 바뀐다).

---

## A12. 렌더링 방식 — 서버 내 PDF(ReportLab)·XLSX(openpyxl), 내장 폰트, 템플릿 판 동결, 외부 호출 0

**결정**
- **PDF = ReportLab**(BSD, 순수 Python + Pillow 휠 — `python:3.13-slim`에 apt 시스템 라이브러리 추가 0). `rl_config.invariant = 1`로 **결정적 출력**(생성 시각·ID 고정 → 같은 입력·같은 라이브러리 판이면 같은 바이트). **XLSX = openpyxl**(MIT, 순수 Python). 두 패키지는 `requirements.txt`에 `==` 고정(현재 PDF·엑셀 의존성 0 — `backend/requirements.txt`). 버전 값은 착수 PR이 PyPI·3.13 휠을 실측해 고정한다(**실행 검증 못 했음**).
- **폰트 = NanumGothic Regular·Bold TTF(SIL OFL 1.1)를 리포에 동봉**(`backend/app/modules/doc_render/fonts/` — 한글 KO 변형 + 라틴 + 주소의 확장 라틴). ReportLab TTFont는 TrueType 윤곽만 받으므로 CFF 기반 OTF(Noto Sans KR OTF)는 쓰지 않는다. 파일 sha256을 상수로 고정하고 K 시험이 대사(폰트 교체 = 판 변경). 라이선스 전문을 같은 폴더에 둔다. PDF에는 쓰인 글자만 서브셋 임베드된다.
- **글리프 커버리지(V12)**: 폰트 cmap으로 인쇄 문자열 전 글자를 검사하는 순수 함수 — 발행 **전** 검증(발행 후 렌더 실패로 '번호는 났는데 파일이 없는' 상태를 원천 차단).
- **템플릿 = 코드 레이아웃 함수 레지스트리** `TEMPLATES[(kind, language, version)]`(kind ∈ QT·PI·CI·PL·SI). **출시된 판은 수정 금지**: 판마다 소스 sha256을 `TEMPLATE_DIGESTS`에 고정하고 K 시험이 대사 — 바꾸려면 새 판 키를 더하고 `CURRENT_TEMPLATE[(kind, language)]`를 올린다. 옛 판 코드는 지우지 않는다(이미 발행된 CI가 그 판을 가리킨다 — §A5 `template_version`).
- **렌더러 순수성**(`doc_render` 모듈): 입력 = 뷰 데이터클래스(모든 날짜·문자열·정수 금액 주입), 출력 = bytes. DB·SQLAlchemy·도메인 모듈·네트워크(`httpx`·`urllib`·`socket`)·현재 시각(`datetime.now`·`utcnow`)·float 금액 연산 임포트/호출 0을 아키텍처 시험이 AST로 고정한다.
- **XLSX 수식 주입 방어**: 모든 문자열 셀을 `data_type='s'`(+`quotePrefix`)로 쓴다 — `=`·`+`·`-`·`@`로 시작하는 바이어 주소·비고가 수식이 되지 않는다(ADR-0027 CSV 이스케이프의 XLSX판). 금액 셀은 정수 최소단위를 `Decimal` 문자열로 쓰고 통화 자릿수 서식을 건다(float 변환 0).
- **장문**: 표 셀은 줄바꿈·페이지 넘김으로 전부 인쇄한다(잘라내기 0 — §20 B '장문'). 라인 상한은 커널 `MAX_LINES = 500`(`code:modules/trade_docs/constants.py:119`) 그대로.
- **외부 렌더 서비스·헤드리스 브라우저 0**: 서류 내용(가격·거래처)이 밖으로 나가지 않고, §17.1 외부 호출 문제가 생기지 않는다.

**근거**: §7.6 "템플릿 렌더링(PDF·엑셀, 언어 변형)"(`D:215`), §17.1 외부 호출 금지(`D:358`), ADR-0029(서빙은 attachment·octet-stream — PDF·XLSX 허용 확장자 `code:modules/documents/service.py:73-92`), ADR-0027(수식 이스케이프), 금액 float 금지(ADR-0003·GC-G1), `backend/Dockerfile`(slim 이미지·비root — 시스템 라이브러리 무추가가 유리).

**대안(기각)**

| 안 | 기각 사유 |
|---|---|
| WeasyPrint(HTML/CSS→PDF) | pango·cairo·harfbuzz 시스템 라이브러리를 이미지·CI 러너 둘 다에 apt 설치해야 한다(재현성·이미지 크기·보안 패치 표면). |
| 헤드리스 Chromium(Playwright) | 수백 MB, 프로세스 관리, 렌더 시간. 리포가 브라우저 e2e도 비채택(ADR-0086). |
| fpdf2 | LGPL-3.0, 결정적 출력 공식 옵션 부재(생성 시각 주입은 가능하나 보증 약함). |
| 외부 SaaS 렌더 API | 거래 데이터 반출·§17.1·가용성 의존. |
| 브라우저 인쇄(화면 HTML → 사용자 PDF 저장) | 서버 보관 바이트가 없어 '보낸 그 파일'을 증명할 수 없고 XLSX가 없다. |
| 폰트 apt 설치(`fonts-nanum`) | CI 러너·dev·prod가 같은 폰트 판을 보장하지 않는다 — 렌더 결과가 환경마다 달라진다. |

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 중간**(라이브러리 교체 = 렌더러 재작성. 단 이미 저장된 파일은 영향 0 — §A13이 바이트를 보관하므로 라이브러리 교체가 과거 서류를 바꾸지 않는다). 폰트 동봉에 따른 리포 크기 증가(TTF 2종 수 MB — 착수 시 실측)는 감수한다.

---

## A13. 파일 저장·documents 연계 — 렌더 산출물은 documents FILE, 연결은 불변 `trade_document_renditions`

**결정**
- **산출물 = documents FILE 행**(§4.7 '무역서류 한 곳' — 백업 세트·복원 리허설의 FILE 실물·sha256 검증·용량 감시·고아 탐지에 **자동 편입**, `D:461` ①③). 저장은 기존 계약 그대로: 서버 생성 hex 파일명·저장 루트 정적 서빙 밖·다운로드 attachment+octet-stream+nosniff(ADR-0029, `code:modules/documents/service.py:423-489`). 원본 파일명 = `{번호}_{종류}_{언어}.{pdf|xlsx}`(ASCII, 예: `CI-2026-0001_PL_EN.pdf`).
- **연결 표 `trade_document_renditions`(IMMUTABLE)**: `source_type`(`QUOTATION`·`PROFORMA_INVOICE`·`COMMERCIAL_INVOICE`·`SHIPPING_INSTRUCTION`) · `source_id` · `doc_kind`(`QT`·`PI`·`CI`·`PL`·`SI`) · `format`(`PDF`·`XLSX`) · `language`(`EN`·`KO`) · `template_version` · `renderer_version`(라이브러리 판 문자열) · `document_id` FK documents **UNIQUE** · `created_at`·`created_by_id`.
  - **유일**: `UNIQUE(source_type, source_id, doc_kind, format, language)` — **원천·종류·형식·언어당 첫 렌더 1건이 정본**이고 이후 같은 요청은 그 행을 돌려준다(템플릿 판이 올라도 이미 렌더된 서류는 다시 찍지 않는다 — '보낸 그 파일' 보존).
  - CHECK: (`doc_kind`, `source_type`) 정합(PL ⇒ COMMERCIAL_INVOICE), CI·PL·SI ⇒ `language = 'EN'`.
- **트랜잭션 경계(§17.1 — 파일 IO는 TX 밖, ADR-0049 선례)**: ① 발행 TX1(CI·라인·PL·상태이력·이벤트·멱등 완료) 커밋 → ② TX 밖에서 렌더(바이트) + 파일 쓰기(`documents.service`에 공개 함수 `write_generated_blob(data: bytes) -> (stored_name, size, sha256)` 신설 — 기존 `_store_stream` 재사용) → ③ TX2에서 documents 행 + renditions 행 INSERT(유일 위반 = 경쟁 렌더 → 내 파일 `discard_stored_file` 후 승자 반환). 실패 시 쓴 파일을 지운다(`code:modules/documents/service.py:487-489`).
  - ②·③이 실패해도 CI는 발행된 상태다(TX1 커밋) — 응답 `renditions_pending: true`, 그리고 **명시 액션 `POST /commercial-invoices/{id}/render`**(Idempotency-Key, 무역·물류)가 빠진 산출물만 다시 만든다(멱등 — 유일 키). 다운로드 GET은 쓰기를 하지 않는다(산출물 없으면 409 `EXPORT_DOCS.RENDITION.PENDING`). 크래시로 남은 고아 파일은 기존 `storage-monitor`가 보고한다(자동 삭제 0).
  - 결정성: PDF는 invariant 모드라 재시도가 같은 바이트를 낸다(테스트 고정 가능). XLSX는 zip 시각 때문에 바이트가 매번 다르다 → **저장된 첫 파일이 정본**이라 문제없다.
- **QT·PI 렌더**: `POST /quotations/{id}/render`·`/proforma-invoices/{id}/render`(본문 `format`·`language`) — 동결(QT `status ≠ DRAFT`, PI 항상) 확인 → 같은 ②③. 이미 있으면 그 행 반환(200). 취소·만료된 QT·PI도 렌더 가능(기록 보존 — 인쇄물에 상태 표기 'CANCELLED' 워터마크는 **안 한다**: 정본 파일이 상태에 따라 바뀌면 안 되므로, 상태는 화면이 보여 준다).
- **CI 발행 직후 자동 렌더 = 4건**(CI PDF·CI XLSX·PL PDF·PL XLSX), S/I = 2건(PDF·XLSX). 사람 1클릭(발행)의 산출물이라 L3 아님.
- **문서 종류 시드 5행**(마이그레이션 — `document_types`는 Actor 없는 시드 표라 함정 ⑩ 무관, `code:modules/documents/models.py:74-97`): `QUOTATION`·`PROFORMA_INVOICE`·`COMMERCIAL_INVOICE`·`PACKING_LIST`·`SHIPPING_INSTRUCTION`(`retention_years NULL` — 법정 보존연수는 시스템이 단정하지 않는다, §21). 기존 `TRADE_DOC`(포괄 — `mig:20260806_0300_c9a2e4f7b1d3_document_types_documents_item_profile_.py:233`)은 사람 업로드용으로 그대로.
- **삭제 잠금**: renditions가 가리키는 documents 행의 soft delete = 409 **`DOCUMENTS.DOCUMENT.GENERATED_LOCKED`**(`delete_document` 가드 추가 — `code:modules/documents/service.py:752-791`, 기존 보존기한·태스크 링크 가드와 같은 자리). 강제 삭제 경로 없음.
- **`owner_type` 확폭·전표 첨부(P-13)**: `documents.owner_type` VARCHAR(13) → **VARCHAR(24)**(`code:modules/documents/models.py:106-107` 주석의 확폭 경고 해소 — PG varchar 확폭은 재작성 없음), CHECK 열거에 **`QUOTATION`·`PROFORMA_INVOICE`·`SALES_ORDER`·`SHIPMENT`·`COMMERCIAL_INVOICE`·`SHIPPING_INSTRUCTION` 6종 추가**(`code:modules/documents/models.py:68`).
  - **`PURCHASE_ORDER`는 열지 않는다**: documents 목록·다운로드는 전 역할 조회라(`code:modules/documents/router.py:126-189`) 공급사 견적·OC 파일이 원가 10번째 채널이 된다(ADR-0024). **`SHIPMENT` 첨부는 EXPORT 선적만**(수입선적 첨부 = 공급사 인보이스 = 원가 — 서비스 422 `DOCUMENTS.OWNER.KIND_NOT_ALLOWED`). `ORDER_INTAKE`는 S6-1(P-23).
  - 소유 실재 검증은 **테이블 이름 Core 조회**로 한다(`_require_owner`가 전표 모델을 임포트하면 '공용→전표 금지' 임포트 방향 위반 — `tests:architecture/test_import_direction.py:23-49`, 사슬 레지스트리 `code:modules/trade_docs/chain.py:1-12`와 같은 수법).
  - 전표 첨부 쓰기 역할: 현재 `CAN_MANAGE = (TRADE, CERT)`(`code:modules/documents/router.py:31`) — 선적·CI·S/I 소유 첨부에 **LOGISTICS 추가**를 제안한다(B/L 사본 등 물류가 받는 서류). 정본은 안전 부록 권한 매트릭스.

**근거**: §4.7(`D:108`), WBS "documents 전표 첨부(owner_type 확폭 경고)"(`W:258` ③), S3-1 부채 P-13(`docs/plans/s3-1/design-integrated.md:518`), ADR-0028·0029·0049·0050, §17.1(`D:358`).

**대안(기각)**: (a) 렌더 산출물 미저장(다운로드마다 즉석 렌더) — 템플릿·라이브러리·폰트가 바뀌면 과거 서류가 다르게 찍힌다(보낸 파일 증명 불가). (b) 발행 TX 안에서 렌더+파일 쓰기 — §17.1·ADR-0049 위반(IO를 TX에 묶음), 잠금 보유 시간 증가. (c) 비동기 렌더 잡(아웃박스 소비) — 상태 하나(렌더 대기)와 재시도 잡이 늘고, 사람이 발행 직후 파일을 못 받는다. 동기 렌더 + 명시 재시도 액션이 더 단순하다. (d) documents에 `generated` 열 추가 — 연결 표가 출처·판·형식을 함께 담고 불변이다(열 1개로는 출처를 못 단다). (e) 다운로드 GET에서 지연 렌더 — GET 부작용·조회 역할 행위자로 쓰기 발생.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 중간**(연결 표·종류 시드는 마이그레이션 1건. 저장된 파일은 지우지 않으므로 방식 변경이 과거 산출물을 바꾸지 않는다).

---

## A14. 트랜잭션·잠금·멱등 — 이 부록 몫 제안(정본은 안전 부록)

| # | 동작 | TX | 잠금(LOCK_ORDER 순) | 멱등 |
|---|---|---|---|---|
| D1 | 레터헤드 판 등록 | 1 | 없음(INSERT-only 표) | Idempotency-Key |
| D2 | CI 미리보기 | 읽기 1(저장 0) | 없음 | 불요 |
| D3 | CI 발행(+PL) | 1 + 렌더 TX2 | claim → **shipments `FOR UPDATE`** → shipment_children(당사자) `FOR SHARE` → `doc_number_seq` | Idempotency-Key(본문 해시 결속) + 부분 유니크(선적당 살아 있는 CI 1) |
| D4 | CI 취소 | 1 | claim → shipments `FOR UPDATE` → **commercial_invoices `FOR UPDATE`** | Idempotency-Key |
| D5 | CI 재발행 | 1 + 렌더 TX2 | claim → shipments `FOR UPDATE` → shipment_children `FOR SHARE` → commercial_invoices `FOR UPDATE` → seq | Idempotency-Key |
| D6 | S/I 발행 | 1 + 렌더 TX2 | claim → shipments `FOR SHARE` → shipment_children `FOR SHARE` → commercial_invoices `FOR SHARE` → seq | Idempotency-Key |
| D7 | 렌더(QT·PI·CI·S/I) | TX2만(+읽기) | 없음(불변 원천·유일 키) | Idempotency-Key + `UNIQUE(source…, language)` |

- **LOCK_ORDER 슬롯 제안**: `… PO → shipments → shipment_children → **commercial_invoices(+CI 하위)** → approvals → lines → doc_number_seq`(ADR-0078 개정본에 1칸 삽입). 채권 부록이 CI를 잠그면 같은 슬롯을 쓴다. SO·PO는 잠그지 않는다(SO는 확정 동결 — 읽기만, 선적이 원천 사본을 이미 가짐).
- 발행 경합: 같은 선적 동시 발행 2건 → 선적 `FOR UPDATE`로 직렬화, 뒤 요청은 잠금 후 '살아 있는 CI 존재' 검사에서 409 `ALREADY_ISSUED`(부분 유니크가 2차 안전망 — 위반 시 같은 409로 번역, 500 금지).
- 55P03·40P01 = 409 `COMMON.CONCURRENCY.LOCK_BUSY`(S3-1 공통 — `D:362` ③).

**근거**: §17.1~17.4(`D:358-372`), ADR-0059·0078, S3-2 T 표 선례.

**자율 확정 여부**: 자율 확정(정본 이관). **되돌리기 비용: 낮음**(LOCK_ORDER 슬롯은 ADR 부기 1건).

---

## A15. 사슬 등록·FIELD_POLICY·table_policy

- **`CHILD_LINKS` 추가 1**: `ChildLink(DocKind.SHIPMENT, "commercial_invoices", "shipment_id")` — 살아 있는 CI가 있는 선적은 취소 409 `SUCCESSOR_ALIVE`(+`successors=[CI-…]`). S3-3 운영 경로에선 발행 대상 선적(INSPECTED+)에 취소 엣지가 없어 실효는 S4-2부터지만 지금 등록한다(FK가 생기는 PR이 등록 — S3-2 PO→선적 선례 `code:modules/trade_docs/chain.py:44-47`).
- **`NON_CHILD_FK_ALLOWLIST` 추가**(사유 필수 — `test_every_fk_to_chain_docs_is_registered`):
  - (`commercial_invoices`, `so_id`) "선적 원천 SO의 사본 — 사슬 후속은 선적이다(SO 취소 가드는 선적이 이미 건다)"
  - (`commercial_invoices`, `supersedes_ci_id`) "재발행 계보 표시 — 원본은 같은 TX에서 이미 취소됨"
  - (`commercial_invoices`, `copied_from_id`) "믹스인 열 — CHECK로 항상 NULL"
  - (`commercial_invoice_lines`, `ci_id`)·(`commercial_invoice_lines`, `so_line_id`)·(`commercial_invoice_lines`, `shipment_line_id`) "라인 구성·원천 라인 사본"
  - (`commercial_invoice_status_log`, `commercial_invoice_id`) "상태이력"
  - (`packing_list_packages`, `ci_id`) "PL은 CI의 구성 요소(수명 = CI 상태)"
  - (`shipping_instructions`, `ci_id`)·(`shipping_instructions`, `shipment_id`) "S/I는 불변 작업 서류 — CI 정정을 막지 않는다(§A8)"
- **FIELD_POLICY(CI)**: ORIGIN = §A5 표의 ORIGIN·INPUT* 전부 / SYSTEM = status·frozen_at·doc_number·doc_date·template_version·acknowledged_notices·revision_no·consignee_address_source·last_line_no / FREE = `internal_note`·`assignee_id`(2개 — 선적과 같은 집합). 미등재 열 = CI 실패(`tests:architecture/test_doc_field_policy.py`).
- **table_policy**: IMMUTABLE 추가 **7표** — `company_profiles`·`commercial_invoice_lines`·`commercial_invoice_status_log`·`packing_list_packages`·`packing_list_package_items`·`shipping_instructions`·`trade_document_renditions`(S3-2까지 11표 → 18표, `code:core/db/table_policy.py:20-47`). **COLUMN_UPDATE_ALLOWLIST 추가 1** — `commercial_invoices`(§A5). DESIGN §17.5 부기 대상.

**자율 확정 여부**: 자율 확정. **되돌리기 비용: 낮음~중간**(IMMUTABLE 해제는 GRANT 복원 1줄이지만 '해제'는 감사상 설명이 필요).

---

## A16. 권한 — 제안(정본은 안전 부록 AUTHZ 행)

| 자원·동작 | ADMIN | TRADE | LOGISTICS | CERT | VIEWER |
|---|---|---|---|---|---|
| 레터헤드 조회 / 등록 | ✓ / ✓ | ✓ / 403 | ✓ / 403 | ✓ / 403 | ✓ / 403 |
| CI·PL 미리보기·발행·취소·재발행·렌더 | ✓ | ✓ | ✓ | 403 | 403 |
| CI 목록·상세(Page 50) | ✓ | ✓ | ✓ | ✓ | ✓ |
| S/I 발행·렌더 / 조회 | ✓ / ✓ | ✓ / ✓ | ✓ / ✓ | 403 / ✓ | 403 / ✓ |
| QT·PI 렌더 | ✓ | ✓ | 403 | 403 | 403 |
| 렌더 파일 다운로드(documents) | 전 역할(현행 documents 규칙) | | | | |

- 근거: LOGISTICS 선적 쓰기(ADR-0079), 전표 = 회사 공유 자산(§18.1 부기 `D:392`), 판매가는 마스킹 대상 아님(원가만 — ADR-0018·0024). 확인 순서 401→403→404→409→422.

---

## A17. 에러코드 (신규 — `<도메인>.<대상>.<사유>`, 카탈로그 1:1·문구에 조치 힌트)

| 코드 | HTTP | 언제 |
|---|---|---|
| `EXPORT_DOCS.SHIPMENT.NOT_INSPECTED` | 409 | 선적이 `CI_ISSUABLE_SHIPMENT_STATES` 밖(§A2) — "검수완료 이후에만 CI·PL을 만들 수 있습니다" |
| `EXPORT_DOCS.SHIPMENT.KIND_NOT_SUPPORTED` | 422 | 수입선적 CI·S/I |
| `EXPORT_DOCS.CI.ALREADY_ISSUED` | 409 | 선적에 살아 있는 CI 존재(+`detail.doc_number`) — "재발행을 쓰세요" |
| `EXPORT_DOCS.VALIDATION.FAILED` | 422 | §A10 BLOCK 또는 미확인 NOTICE(+`detail.items[{code,severity,path,message}]`) |
| `EXPORT_DOCS.LETTERHEAD.NOT_REGISTERED` | 409 | 레터헤드 판 0 |
| `EXPORT_DOCS.LETTERHEAD.NOT_EFFECTIVE` | 409 | as-of 판 없음(+`detail.as_of`) |
| `EXPORT_DOCS.SI.CI_NOT_LIVE` | 409 | S/I 원천 CI가 취소됨 |
| `EXPORT_DOCS.SI.FORWARDER_MISSING` | 422 | 선적 FORWARDER 당사자 없음 |
| `EXPORT_DOCS.SI.CONSIGNEE_MODE_NOT_ALLOWED` | 422 | 지시식 수하인인데 L/C 근거 없음 |
| `EXPORT_DOCS.RENDITION.SOURCE_NOT_FROZEN` | 409 | QT DRAFT 렌더 |
| `EXPORT_DOCS.RENDITION.PENDING` | 409 | 산출물 없음(렌더 액션 안내) |
| `DOCUMENTS.DOCUMENT.GENERATED_LOCKED` | 409 | 렌더 산출물 삭제 시도 |
| `DOCUMENTS.OWNER.KIND_NOT_ALLOWED` | 422 | 수입선적 첨부 등 |

- 검증 항목 코드(`detail.items[].code` — 에러코드가 아니라 항목 코드): `LINE_AMOUNT_MISMATCH`·`TOTAL_MISMATCH`·`GW_LT_NW`·`NW_NOT_POSITIVE`·`INCOTERMS_INCOMPLETE`·`INCOTERMS_MODE_MISMATCH`·`CI_PL_LINE_SET_MISMATCH`·`CI_PL_QTY_MISMATCH`·`PACKAGE_MARKS_NOT_CONTIGUOUS`·`SHIPMENT_NOT_INSPECTED`·`SHIPMENT_LINE_SET_MISMATCH`·`DESCRIPTION_EN_MISSING`·`CONSIGNEE_ADDRESS_MISSING`·`BANK_REQUIRED`·`BANK_CURRENCY_MISMATCH`·`GLYPH_UNSUPPORTED`·`DG_MANUAL_CHECK`·`DG_DATA_INCOMPLETE`·`BL_TYPE_MODE_MISMATCH`·`NOTICE_NOT_ACKNOWLEDGED`. 이 목록은 L0 StrEnum 단일 출처, 화면 한국어 라벨은 화면 부록(R-6-4 '백엔드 사전 ↔ 프런트 라벨 별도 출처' 재발 방지 — 대사 시험).
- 도메인 이름 `EXPORT_DOCS`: 커널 `TRADE_DOCS.*`(전표 공통)와 구분되는 '서류 생성' 도메인. 커널 공통 사유(`TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`·`TRADE_DOCS.DOCUMENT.FROZEN`)는 그대로 쓴다.

---

## A18. 모듈 배치·임포트 방향·외부 의존성 허용 목록

| 모듈(신규) | 계층 | 담는 것 |
|---|---|---|
| `trade_docs`(기존 L0) | L0 | DocKind·상태·커널 dict 행, `CI_ISSUABLE_SHIPMENT_STATES`, `SHIPPING_INSTRUCTION_PREFIX`, `doc_validation.py`(§A10 순수 함수), `incoterms.py` 완전성 가산, 검증 항목 StrEnum |
| `letterhead` | L1 | `company_profiles` 모델·as-of 조회·등록 서비스 |
| `commercial_invoices` | L1 | CI·라인·PL 2표·S/I·renditions 모델, 조회·목록·CSV(Page 50) — **선적·SO 모델 무임포트**(테이블 이름 FK) |
| `doc_render` | 플랫폼 리프 | 템플릿 레지스트리·레이아웃·폰트·커버리지 — 앱 모듈 임포트 0(순수) |
| `trade_chain/export_doc_flow.py`·`export_doc_router.py` | L2 | 발행·취소·재발행·S/I·렌더 오케스트레이션(선적·SO·PI·은행·레터헤드·documents 호출) |

- `tests:architecture/test_import_direction.py:23-49`의 L1 집합에 `letterhead`·`commercial_invoices` 등록, `doc_render`는 '도메인 무임포트 플랫폼'으로 등록, **외부 임포트 허용 목록에 `reportlab`·`openpyxl`을 `doc_render`에만** 허용(다른 모듈 임포트 = 실패).
- `documents`(플랫폼)는 전표 모듈을 임포트하지 않는다 — 소유 검증은 테이블 이름 Core 조회(§A13).
- stock_movements 무접촉 단언(S3-2 K)을 신규 모듈로 확장.

---

## A19. 마이그레이션 M16+ 초안 (번호·DAG 정본은 분할 부록 — 현재 head `281da4794717`, `mig:20261004_1753_281da4794717_s32_milestones_customs.py`)

**M16a `s33_letterhead_documents_owner`**(down_revision = 281da4794717 또는 다른 부록의 선행 M)
```sql
CREATE TABLE company_profiles (
  id BIGSERIAL PRIMARY KEY,
  legal_name_en VARCHAR(200) NOT NULL, legal_name_ko VARCHAR(200),
  address_en VARCHAR(500) NOT NULL, address_ko VARCHAR(500),
  business_reg_no CHAR(10) NOT NULL, phone VARCHAR(40), email VARCHAR(200),
  signer_name_en VARCHAR(100), signer_title_en VARCHAR(100),
  effective_from DATE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by_id BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  CONSTRAINT ck_company_profiles_brn CHECK (business_reg_no ~ '^[0-9]{10}$'),
  CONSTRAINT ck_company_profiles_signer_pair CHECK ((signer_name_en IS NULL) = (signer_title_en IS NULL)),
  CONSTRAINT ck_company_profiles_effective_range CHECK (effective_from BETWEEN DATE '2000-01-01' AND DATE '2999-12-31'),
  CONSTRAINT ck_company_profiles_names_clean CHECK (legal_name_en ~ '[^[:space:]]' AND legal_name_en !~ '[[:cntrl:]]'),
  CONSTRAINT ck_company_profiles_address_clean CHECK (translate(address_en, chr(9)||chr(10)||chr(13), '') !~ '[[:cntrl:]]')
);
CREATE INDEX ix_company_profiles_asof ON company_profiles (effective_from DESC, id DESC);
-- revoke_mutations(op, "company_profiles")

ALTER TABLE documents ALTER COLUMN owner_type TYPE VARCHAR(24);
ALTER TABLE documents DROP CONSTRAINT ck_documents_owner_type_valid;
ALTER TABLE documents ADD CONSTRAINT ck_documents_owner_type_valid CHECK (owner_type IN
  ('SKU','LABEL','CERTIFICATION','COMM_LOG','QUOTATION','PROFORMA_INVOICE','SALES_ORDER',
   'SHIPMENT','COMMERCIAL_INVOICE','SHIPPING_INSTRUCTION'));
INSERT INTO document_types (code, name_ko, retention_years, note) VALUES
  ('QUOTATION','견적서(QT)',NULL,'§7.6 생성 산출물'), ('PROFORMA_INVOICE','PI',NULL,'§7.6'),
  ('COMMERCIAL_INVOICE','상업송장(CI)',NULL,'§7.6'), ('PACKING_LIST','포장명세서(PL)',NULL,'§7.6'),
  ('SHIPPING_INSTRUCTION','선적요청서(S/I)',NULL,'§7.6');
```
- downgrade: 새 소유 유형 행·`company_profiles` 행이 1건이라도 있으면 **RAISE로 거부**(데이터 소실 다운그레이드 금지 — fail-closed), 없으면 역순 원복. CI 드라이런(빈 DB 왕복)은 통과한다.

**M16b `s33_commercial_invoices`**(→ M16a)
```sql
-- DocKind CHECK 확장: 상태이력·아웃박스 aggregate 등 DocKind 값 CHECK가 있는 곳 전부(커널 3자 대사 시험이 목록을 고정)
CREATE TABLE commercial_invoices (
  id BIGSERIAL PRIMARY KEY,
  -- TradeHeaderMixin 열(doc_number·doc_date·status·currency·fx_rate·fx_rate_date·결제 4·Incoterms 3·
  --   internal_note·last_line_no·assignee_id·copied_from_id) + created/updated/deleted/version/actor
  shipment_id BIGINT NOT NULL REFERENCES shipments(id) ON DELETE RESTRICT,
  so_id BIGINT NOT NULL REFERENCES sales_orders(id) ON DELETE RESTRICT,
  buyer_partner_id BIGINT NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
  company_profile_id BIGINT NOT NULL REFERENCES company_profiles(id) ON DELETE RESTRICT,
  consignee_partner_id BIGINT NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
  consignee_name_en VARCHAR(200) NOT NULL, consignee_address_en VARCHAR(500) NOT NULL,
  consignee_address_source VARCHAR(8) NOT NULL,
  notify_partner_id BIGINT REFERENCES partners(id) ON DELETE RESTRICT,
  notify_name_en VARCHAR(200), notify_address_en VARCHAR(500),
  buyer_po_no VARCHAR(60), buyer_po_date DATE,
  origin_country_code CHAR(2) NOT NULL, dest_country_code CHAR(2) NOT NULL,
  transport_mode VARCHAR(12) NOT NULL,
  port_of_loading VARCHAR(100) NOT NULL, port_of_discharge VARCHAR(100) NOT NULL,
  final_destination VARCHAR(100), vessel_voyage VARCHAR(100), sailing_on DATE,
  goods_origin_country_code CHAR(2) NOT NULL,
  shipping_marks VARCHAR(1000), remarks VARCHAR(1000),
  bank_account_id BIGINT REFERENCES bank_accounts(id) ON DELETE RESTRICT,
  bank_beneficiary_name VARCHAR(200), bank_beneficiary_address VARCHAR(300), bank_name VARCHAR(200),
  bank_address VARCHAR(300), bank_account_no VARCHAR(40), bank_swift_code VARCHAR(11),
  total_amount BIGINT NOT NULL,
  template_version SMALLINT NOT NULL, acknowledged_notices VARCHAR(40)[] NOT NULL DEFAULT '{}',
  supersedes_ci_id BIGINT REFERENCES commercial_invoices(id) ON DELETE RESTRICT,
  revision_no SMALLINT NOT NULL, frozen_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_commercial_invoices_doc_number UNIQUE (doc_number),
  CONSTRAINT uq_commercial_invoices_id_currency UNIQUE (id, currency),
  CONSTRAINT ck_commercial_invoices_doc_number_format CHECK (doc_number ~ '^CI-[0-9]{4}-[0-9]{4,}$'),
  CONSTRAINT ck_commercial_invoices_status_valid CHECK (status IN ('ISSUED','CANCELLED')),
  CONSTRAINT ck_commercial_invoices_never_deleted CHECK (deleted_at IS NULL),
  CONSTRAINT ck_commercial_invoices_no_copy_lineage CHECK (copied_from_id IS NULL),
  CONSTRAINT ck_commercial_invoices_revision CHECK (revision_no >= 1 AND (revision_no = 1) = (supersedes_ci_id IS NULL)),
  CONSTRAINT ck_commercial_invoices_transport_mode CHECK (transport_mode IN ('SEA','AIR','ROAD','RAIL','COURIER','MULTIMODAL')),
  CONSTRAINT ck_commercial_invoices_address_source CHECK (consignee_address_source IN ('SNAPSHOT','MANUAL')),
  CONSTRAINT ck_commercial_invoices_notify_pair CHECK ((notify_partner_id IS NULL) = (notify_name_en IS NULL)),
  CONSTRAINT ck_commercial_invoices_bank_all_or_none CHECK (
    num_nonnulls(bank_account_id, bank_beneficiary_name, bank_beneficiary_address, bank_name,
                 bank_address, bank_account_no, bank_swift_code) IN (0, 7)),
  CONSTRAINT ck_commercial_invoices_bank_required_for_tt CHECK (
    payment_type NOT IN ('TT_ADVANCE','TT_DEFERRED') OR total_amount = 0 OR bank_account_id IS NOT NULL),
  CONSTRAINT ck_commercial_invoices_swift CHECK (bank_swift_code IS NULL OR bank_swift_code ~ '^[A-Z0-9]{8}([A-Z0-9]{3})?$'),
  CONSTRAINT ck_commercial_invoices_total_range CHECK (total_amount BETWEEN 0 AND 9007199254740991)
  -- + header_common_checks(COMMERCIAL_INVOICE)·국가 형식·문자 규약 CHECK
);
CREATE UNIQUE INDEX uq_commercial_invoices_shipment_id_live ON commercial_invoices (shipment_id) WHERE status <> 'CANCELLED';
CREATE UNIQUE INDEX uq_commercial_invoices_supersedes ON commercial_invoices (supersedes_ci_id) WHERE supersedes_ci_id IS NOT NULL;
CREATE INDEX ix_commercial_invoices_list ON commercial_invoices (status, id DESC);
CREATE INDEX ix_commercial_invoices_so_id ON commercial_invoices (so_id);
-- restrict_update_columns(op, "commercial_invoices", {"status","internal_note","assignee_id","version","updated_at","updated_by_id"})

CREATE TABLE commercial_invoice_lines ( ... §A6 열 ...,
  FOREIGN KEY (ci_id, currency) REFERENCES commercial_invoices (id, currency) ON DELETE RESTRICT,
  CONSTRAINT uq_commercial_invoice_lines_ci_line_no UNIQUE (ci_id, line_no),
  CONSTRAINT uq_commercial_invoice_lines_ci_shipment_line UNIQUE (ci_id, shipment_line_id),
  CONSTRAINT ck_commercial_invoice_lines_amount CHECK (quantity::numeric * unit_price_amount = line_amount),
  CONSTRAINT ck_commercial_invoice_lines_free CHECK (is_free = (unit_price_amount = 0)),
  CONSTRAINT ck_commercial_invoice_lines_hs_triple CHECK (num_nonnulls(sku_hs_code_id, hs_code, hs_version) IN (0, 3)),
  CONSTRAINT ck_commercial_invoice_lines_dg CHECK (dg_flag OR (un_number IS NULL AND dg_class IS NULL AND packing_group IS NULL AND NOT is_limited_quantity)),
  CONSTRAINT ck_commercial_invoice_lines_never_deleted CHECK (deleted_at IS NULL));
CREATE TABLE commercial_invoice_status_log ( ... status_log_checks 믹스인 동형 ... );
CREATE TABLE packing_list_packages ( ... §A7 ...,
  CONSTRAINT ck_packing_list_packages_gw_ge_nw CHECK (gross_weight_g_per_pkg >= net_weight_g_per_pkg),
  CONSTRAINT ck_packing_list_packages_nw_positive CHECK (net_weight_g_per_pkg > 0),
  CONSTRAINT ck_packing_list_packages_marks CHECK (mark_from >= 1 AND mark_to >= mark_from AND mark_to <= 99999),
  CONSTRAINT uq_packing_list_packages_ci_seq UNIQUE (ci_id, seq));
CREATE TABLE packing_list_package_items ( ... §A7 ...,
  CONSTRAINT uq_packing_list_package_items_pkg_line UNIQUE (package_id, ci_line_id));
-- revoke_mutations × 4 (lines·status_log·packages·items)
```

**M16c `s33_shipping_instructions_renditions`**(→ M16b): `shipping_instructions`(§A8 — 전역 UNIQUE `doc_number`·형식 CHECK `^SI-…`·모드/지시처·B/L 원본 수 CHECK·부분 유니크 `supersedes_si_id`)·`trade_document_renditions`(§A13 — UNIQUE `document_id`·UNIQUE(source_type, source_id, doc_kind, format, language)·정합 CHECK) + `revoke_mutations` × 2.

- 세 파일로 나눈 이유: M16a는 CI 없이도 서는 QT·PI 렌더 PR의 전제, M16b는 커널 편입(총수 시험 갱신과 같은 PR), M16c는 S/I PR. 분할 부록이 다른 부록 마이그레이션과 합쳐 번호를 매긴다.
- 실측 의무(착수 PR): `alembic heads` 단일·빈 DB 왕복·`alembic check` 드리프트 0(§18.3 `D:396`), IMMUTABLE·열 단위 UPDATE 권한 실측(42501).

**자율 확정 여부**: 자율 확정(초안). **되돌리기 비용: 중간**(불변 표는 데이터가 쌓이면 구조 변경이 어렵다 — 그래서 열은 처음부터 넓게 NULL 허용으로 둔다).

---

## A20. 다른 부록에 내는 계약 (함수 시그니처·불변식)

| 계약 | 제공 | 소비 |
|---|---|---|
| `live_ci_for_shipment(session, shipment_id) -> CommercialInvoiceRef \| None`(id·번호·doc_date·통화·total·결제조건) | 이 부록(L1 조회) | 채권 부록·기일(대금만기 `INVOICE_DATE` 앵커) |
| CI 취소 역순 가드: 채권이 CI FK를 가지면 `ChildLink(COMMERCIAL_INVOICE, "receivables", "ci_id")` | 채권 부록이 등록 | 커널 `has_live_children` |
| 이벤트 `commercial_invoices.commercial_invoice.created`·`.status_changed`(payload: ci_id·shipment_id·so_id·partner_id·currency·total — 금액은 판매가) | 이 부록 | 채권 부록(필요 시), 알림 |
| `lc_allows_order_consignee(session, shipment_id) -> bool`(실패 = False) | L/C 부록 | 이 부록 S/I |
| 검증 항목 StrEnum·NOTICE 확인 계약(`acknowledge_notices`) | 이 부록 | 화면 부록(라벨·대화상자) |
| 미리보기 응답: 스냅샷 후보·참고값(ETD·예상 N.W.·예상 상자 수)·검증 항목 | 이 부록 | 화면 부록 |
| **경고(§10 ①)**: S3-3 운영 경로 CI 발행 0(S4-2까지) | 이 부록 | 채권 부록 — 채권 생성 원천을 CI 발행에만 묶지 말 것(또는 그 결과를 명시 판정) |

---

## A21. 테스트 배분 (§20 그룹 — 서류 몫)

| 그룹 | 케이스(요지) |
|---|---|
| **B 서류** | ① CI↔PL 라인 수량 불일치 → 422 `CI_PL_QTY_MISMATCH`, 행 0·번호 소비 0(DoD) ② G.W.<N.W. → 422(서비스) + DB CHECK 직접 INSERT 23514 / **G.W.=N.W. 통과**(경계) ③ 무상 전 라인 0원 CI 발행 성공·은행 블록 없음·PDF 'NO COMMERCIAL VALUE'(DoD) ④ **검수 미완료**: 실제 경로로 만든 RELEASE_ORDERED 선적 발행 409 `NOT_INSPECTED`·미리보기는 항목 표시 ⑤ 한글 주소 PI KO 렌더·장문(1,000자 비고·500라인) 잘림 0(PDF 텍스트 추출 대사) ⑥ CI 목록 CSV UTF-8 BOM·수식 이스케이프 ⑦ Incoterms 완전성(장소 공백 BLOCK·FOB+AIR NOTICE 미확인 422·확인 시 통과+저장) ⑧ 포장 번호 빈틈·겹침 BLOCK ⑨ S/I: 포워더 없음 422·DG UN 결측 422·지시식+TT 422·FOB→COLLECT 도출 |
| **A 전표·정합** | 살아 있는 CI가 있는 선적 취소 409 `SUCCESSOR_ALIVE`(팩토리 상태) / 재발행 = 원본 CANCELLED + 새 번호 + `revision_no` 2 한 TX(중간 실패 시 둘 다 롤백) / 선적당 살아 있는 CI 1 |
| **J 안전 계약** | 같은 선적 실제 동시 발행 2 → 1건 성공·1건 409(500 0) / 더블클릭(같은 키) → CI 1·렌더 4 / 발행 중 예외 → 번호·행·파일 0 / IMMUTABLE 7표 UPDATE·DELETE → 42501 / CI 헤더 스냅샷 열 UPDATE → 42501, status UPDATE는 통과 / 렌더 경쟁 2 → 산출물 1·고아 파일 0 |
| **K 보안·품질** | `doc_render` 순수성 AST(DB·네트워크·`now`·float 0) / 템플릿 판 digest 고정 / 폰트 sha256 / 외부 임포트 허용(`reportlab`·`openpyxl` → `doc_render`만) / 권한 매트릭스·Page 50 자동 스캔 / 렌더 파일 다운로드 = attachment·octet-stream / XLSX `=HYPERLINK(...)` 주소가 문자열 셀 / 글리프 미지원 문자(예: U+1F600) BLOCK / stock_movements 무접촉 / `force_shipment_status_for_test` 앱 코드 호출 0 / 총수 184(이 부록 증분 — 통합 합산) |
| **G AI·보안** | 렌더 산출물 해시 멱등(같은 원천·형식·언어 = 1행) / PO 소유 첨부 422·수입선적 첨부 422(원가 채널 0) / 렌더 산출물 삭제 409 |
| **H 운영** | 렌더 산출물이 복원 리허설 FILE 검증 대상에 들어감(기존 리허설 시험에 rendition 문서 1건 포함) / 렌더 실패 후 `render` 액션 복구 |
| **I 자동화** | 발행·S/I가 대외 발송 이벤트를 만들지 않음(아웃박스 행 = 생성 이벤트뿐, 채널 행 0) |

- **GC 후보(판번은 분할 부록 — A 연번 GC-A22~)**: ① CI↔PL 교차 불일치 거부 ② G.W.<N.W. 거부·등호 통과 ③ 무상 CI 생성 ④ 검수 미완료 선적 CI 거부(운영 경로). WBS S3-3 DoD 2항·검증 B와 1:1.

---

## A22. DESIGN·ADR 부기 대상 (이 부록 몫)

- **DESIGN §7.6 [M4] 보강(S3-3)**: 서류 매트릭스·DGD/B-L/PO 발주서 범위 밖·검증 카탈로그(BLOCK/NOTICE)·렌더링 방식·산출물 정본 = 첫 렌더 파일·재발행 = 새 번호.
- **DESIGN §7.2 [M4] 보강**: CI 상태 2값·엣지 1·총수 증분, **CI/PL 게이트 = INSPECTED·RELEASED·SHIPPED — S3-3~S4-2 운영 경로 닫힘**.
- **DESIGN §4.7 [M1] 보강**: owner_type 확폭·열거 6종(PO 제외 사유)·생성 산출물 삭제 잠금·문서 종류 5행.
- **DESIGN §17.5 [M4] 보강**: IMMUTABLE 11 → 18표, 열 단위 UPDATE 표에 `commercial_invoices` 추가.
- **DESIGN §17.2 보강**: LOCK_ORDER에 `commercial_invoices` 슬롯(안전 부록과 합의분).
- **ADR 후보 4건**(번호는 분할 부록): ① CI 커널 편입·PL 동반·S/I 비커널 불변 ② CI·PL 검수 게이트 선배치(운영 경로 S4-2까지 닫힘 — §A2) ③ 서류 렌더링 = ReportLab·openpyxl·동봉 폰트·템플릿 판 동결·외부 호출 0 ④ 레터헤드 불변 판 + as-of. 부기: ADR-0028(owner 열거 확장)·ADR-0029(생성 파일)·ADR-0054(접두어 CI·SI)·ADR-0074(SHIPPER 자사 = 레터헤드)·ADR-0078(LOCK_ORDER 슬롯).

---

## 10. 멈춰서 보고할 항목 (판정 후보 → 자율 확정, 더 엄격)

1. **§7.2·§20 B(검수 전 CI/PL 금지) ↔ WBS S3-3 DoD(CI 생성 가능 전제) ↔ S4-2(INSPECTED 소유)** — **자율 확정: 게이트를 S3-3에 지금 세우고 운영 경로 CI/PL/S-I는 S4-2까지 닫는다**(§A2). 파급: 채권 부록이 채권 원천을 CI 발행에 묶으면 채권·aging도 S4-2까지 운영 0 → 채권 부록 판정 필요(통합 문서가 대사).
2. **Incoterms 해상 전용 규칙 위반(FOB+항공) 처리** — BLOCK 시 동결 SO 때문에 영구 발행 불가·허위 운송 방식 유인 → **자율 확정: NOTICE(사람 확인 필수·저장)**(§A10 V4b).
3. **DG 선적 서류** — S4-4 DG 게이트 전 — **자율 확정: 차단 0, DG 스냅샷 인쇄 + S/I는 UN·Class 필수 + 수동 점검 NOTICE**(§A8·§A10 V13). DGD 미생성(부채 A-01).
4. **원산지·HS 표기** — §15 법적 판정(L3 금지) — **자율 확정: 서버 기본값·자동 선택 0, 사람 입력·사람 선택만**(§A9 ⑥⑦).
5. **수하인 주소 결측 + 자동 스냅샷 행 불변 + 거래처 영문 편집 경로 부재(P-44)** — **자율 확정: CI 발행 본문 보충(결측일 때만·MANUAL 기록)**(§A9 ④). 마스터 편집 경로는 부채 유지.
6. **documents 전표 첨부의 원가 채널** — **자율 확정: PO 소유 미개방·수입선적 첨부 거부**(§A13).
7. **레터헤드가 없던 시기에 동결된 QT·PI 렌더** — **자율 확정: 대체 금지(409) + 관리자 과거 유효일 판 등록이 유일한 해소**(§A3).

---

## 부채 등재 후보 (이 부록)

| ID | 내용 | 소유 | 트리거 |
|---|---|---|---|
| A-01 | DGD 생성 미구현(§7.6 문면 — WBS 밖) | S4-4 DG 게이트 | S4-4 착수 |
| A-02 | PO 발주서 렌더 없음(원가 10번째 채널 회피) | 발주 | 공급사 발주서 PDF 요구 1건 + 원가 열람 역할 한정 다운로드 설계 |
| A-03 | CI·PL·S/I KO 변형 없음 | 서류 | 국내 CI 요구 1건 |
| A-04 | CLOSED 선적 CI 재발행 불가 | S4-2 | 선적 종결 의미 확정 |
| A-05 | S3-3~S4-2 프로덕션 CI/PL/S-I 발행 0(runbook 외부 작성 안내) | S4-2 | INSPECTED 엣지 개방 PR(runbook 문장 삭제·운영 관통 1회) |
| A-06 | 레터헤드 로고 이미지 없음 | 서류 | 로고 요구 1건(이미지 서빙 보안 재판정) |
| A-07 | PL N.W. ↔ SKU 단위 중량 대조 없음(참고값만) | 서류·마스터 | 중량 오기 사고 1건 또는 SKU 순중량 정의 확정 |
| A-08 | S/I 운임 조건 override 없음(Incoterms 도출 고정) | 서류 | 판매자 대납 관행 요구 1건 |
| A-09 | CI 세관용 무상 신고가액(Value for customs) 열 없음 | 서류 | 무상 샘플 통관 문의 1건 |
| A-10 | CIF·CIP 보험 정보(부보 금액·증권 번호) 열 없음 — Incoterms 완전성에서 제외 | 서류 | 보험 서류 요구 1건(문면 근거 확보 후) |
| A-11 | 렌더 산출물 사람 업로드본(서명·날인 스캔)과의 짝 표시 없음 | 서류 | 서명본 관리 요구 |

**S3-2·S3-1 부채 대사(이 부록 소유분)**

| ID | 처리 |
|---|---|
| Q-05 중량·CBM·박스(`P:59`) | **해소 계획** — §A7 PL 2표(그램·mm 정수, 파생 CBM) |
| R-3a-5 SHIPPER(자사) 자리(`P:91`) | **해소 계획** — §A3 레터헤드 판이 Shipper/Exporter, 선적 당사자 SHIPPER(수출) 422 유지 |
| P-11 자사 레터헤드 마스터 | **해소 계획** — §A3 |
| P-13 documents owner_type 확폭 | **해소 계획** — §A13(VARCHAR(24)·6종, PO·ORDER_INTAKE 제외 사유 명기) |
| Q-06 비거래처 수하인 'TO ORDER'(`P:60`) | **경계 분담** — S/I `consignee_mode`(이 부록) + 허용 술어(L/C 부록) |
| Q-17 DG 수동 점검 공백(`P:71`) | **부분 완화** — 서류 발행 시 NOTICE `DG_MANUAL_CHECK` 확인 필수(본체는 S3-4 체크리스트 그대로) |
| P-56 SKU 용량 컬럼 | **이월 유지** — CI 품명 본문 대체(`description_en` MANUAL)로 우회, 열 미신설(트리거 원문) |
| R-3b-8·P-44 거래처 영문 편집 | **이월 유지** — CI 한정 주소 보충 탈출로만(§A9 ④) |
| Q-04·Q-08·P-01·P-10 | **무접촉** — 채권·기일·L/C 부록 소유(§0 계약만) |
