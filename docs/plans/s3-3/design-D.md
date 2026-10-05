# S3-3 계획서 부록 D — API·화면 계약 (서류 생성·미리보기·다운로드·재발행 / 채권·입금 / SO 완료·잔량 종결 / 대금만기 표시 / 셸·한국어 UI·390px / S3-2 화면 부채)

- 기준: main `2092406`(S3-2 종결 — PR-8 #67). 사양 정본은 DESIGN.md이고, 일정은 WBS.md S3-3 행(`W:127-132`)과 v1.6 주석(`W:132`·`W:251`·`W:258`)을 따른다. 부채 정본은 PROGRESS 'S3-2 부채 최종 목록'(`P:50-166`)과 S3-1 계획 등재 P-01~P-60(`P:1614-1675`)이다.
- 표기: `D:줄` = DESIGN.md, `W:줄` = WBS.md, `P:줄` = PROGRESS.md, `code:경로:줄` = `backend/app/` 아래, `tests:경로:줄` = `backend/tests/` 아래, `fe:경로:줄` = `frontend/src/` 아래, `sA:절`·`sB:절` = 같은 디렉터리의 `design-A.md`(서류 데이터 모델·생성기)·`design-B.md`(채권·입금·여신 provider·대금만기). 줄 번호는 `2092406`에서 grep·sed로 실측한 값이다.
- **부록 문자 주의**: sA는 이 부록을 「화면 부록」, sB는 "화면 부록(E)"·"인가·잠금 부록(D)"으로 부른다. 이 문서는 오케스트레이터 배정대로 **부록 D = API·화면 계약**이다. 문자 확정은 통합 문서 몫이고, 이 부록은 다른 부록을 **주제로** 가리킨다 — 「서류 부록」(sA), 「채권 부록」(sB), 「L/C 부록」(lc_terms·하자 체크리스트·제시 기록), 「안전 부록」(LOCK_ORDER·권한 매트릭스 정본), 「분할 부록」(PR·마이그레이션·ADR·GC 번호).
- 판정 방식: 오너 상시 지시(2026-09-29 "결정·개입 없이 끝까지")에 따라 판정 후보는 모두 **더 엄격한(fail-closed) 권장안으로 '자율 확정'**했다(ADR-0011 부기). PROGRESS·ADR 등재 시 "자율 확정 — 사후 번복 가능"으로 표기한다. 각 안건은 **결정 / 근거 / 대안(기각) / 자율 확정 / 되돌리기 비용** 순서다(S3-2 부록 D 선례).
- **실행 검증 못 했음.** 문서와 코드를 정적으로 읽기만 했다. pytest·vitest·서버·브라우저는 돌리지 않았다. 응답 필드 이름은 구현 PR 첫 커밋에서 스키마로 실측 고정한다.
- 안건 번호는 D1~D20이다.

---

## 0. 경계 — 이 부록이 정하는 것과 넘기는 것

| 이 부록(D)이 정한다 | 다른 부록으로 넘긴다(경계만 적음) |
|---|---|
| 엔드포인트 목록(경로·메서드·역할 행·멱등/version·요청/응답 골격·페이지네이션·화면이 처리할 에러 코드) | 테이블·컬럼·CHECK·상태 기계·채번·검증 카탈로그(V1~V14)·렌더링 방식 → **서류 부록 sA** |
| `allowed_actions` 확장 규칙과 S3-3 신규 액션 표(D4) | 채권 모델·미수 정의·노출 산식·COMPLETED 판정·aging 산식·알림 대상 → **채권 부록 sB** |
| 화면 목록·라우트·셸 메뉴(D6), QT·PI 서류 파일 패널(D7), 레터헤드 관리(D8) | `lc_terms` 입력·하자 체크리스트·tolerance 표시·제시 기록 화면 → **L/C 부록**(이 부록은 자리·라우트 슬롯만 — D16) |
| 선적 상세 '수출 서류'·'채권' 섹션, CI·PL 작성 화면(2단), CI 상세·재발행·취소·S/I(D9~D11) | LOCK_ORDER 슬롯·TX 경계·권한 매트릭스 **정본** → **안전 부록**(이 부록의 역할 열은 sA §A16·sB §B18을 옮긴 **화면 계약용 사본**이다) |
| 채권 목록·aging·상세·입금 패널·이월 채권·채권 취소 화면(D12) | PR 번호·마이그레이션 DAG·ADR/GC 번호 → **분할 부록** |
| SO 상세 '완료'·잔량 종결·채권 요약, SO 목록 필터, 오더 보드 처리(D13) | runbook 클릭 단위 문안 → 마감 PR(이 부록은 화면 버튼 이름만 고정) |
| 대금만기·제시기한 표시(타임라인·aging·사유 라벨 — D14), 여신 카드 '미수 반영'(D15) | |
| 문서보관소 라벨·생성 서류 잠금 표시·알림 이동 표·라벨 대사 시험(D17) | |
| 한국어 UI·390px·접근성의 S3-3 적용(D18), S3-2 이월 화면 부채 처리(D19), 금액 대화상자 멱등 키 보존(D20) | |

**이 부록이 다른 부록에 기대는 가정(통합 검토가 맞춘다 — 어긋나면 해당 화면 1곳만 바뀐다)**
1. CI는 커널 전표(`DocKind.COMMERCIAL_INVOICE`, 접두어 `CI`, 상태 `ISSUED`·`CANCELLED`, 생성=발행=동결)이고 PL은 CI의 구성 요소, S/I는 상태 없는 불변 행(접두어 `SI`)이다(sA §A4·§A8).
2. **CI·PL·S/I 발행은 선적 `{INSPECTED, RELEASED, SHIPPED}`에서만** 열리고 S4-2 전까지 운영 경로에서 발행 0이다. 미리보기(JSON·저장 0)는 RELEASE_ORDERED부터 허용된다(sA §A2).
3. QT·PI 렌더는 프로덕션에서 바로 쓴다(QT는 DRAFT 제외). 렌더 산출물은 documents FILE 행 + `trade_document_renditions`이고 **(원천·종류·형식·언어)당 첫 파일이 정본**이다(sA §A13).
4. 채권은 수출 선적당 살아 있는 1건, 발생은 사람 1클릭, 만기는 파생(OPENING만 저장), 미수는 파생(선수금 FIFO 충당)이다(sB §B1·§B2·§B4).
5. SO COMPLETED는 자동 엣지 1개(채권 발생·short-close TX에서만), short-close는 사람 동작 + SO 3열 기록이다(sB §B7).

---

## D1. API 공통 계약 — S3-1·S3-2 관례 승계 + S3-3 추가 3규칙

**결정**
- **승계(무변경)**: 목록 = `Page` 봉투·`page`/`size`(기본 50·최대 200 — `code:core/pagination.py`, `list_*` 함수명 강제 `tests:architecture/test_auth_coverage.py:158-178`) / 쓰기 스키마 `extra="forbid"` / 생성·발행·전이·입금·렌더는 `Idempotency-Key` 필수(`fe:lib/api.ts:68-77`이 자동 부착), 기존 행 변경은 `version`(불일치 409) / 오류 순서 401→403→404→409→422(`D:392` §18.1 부기) / 에러 봉투 `{error:{code,message,detail,request_id}}`·코드 `<도메인>.<대상>.<사유>`·문구 단일 출처 `code:core/errors/catalog.py:1-9` / 금액은 서버 `*_text`만 화면에 쓰고 프런트 산술 0 / 날짜 `YYYY-MM-DD` 문자열(시간대 없음)·감사 시각 UTC ISO → `toKstDisplay`.
- **신규 접두어 5개를 `GOVERNED_PREFIXES`에 등재**(`tests:architecture/authz_matrix.py:24-55`): `/api/v1/company-profiles`, `/api/v1/commercial-invoices`, `/api/v1/shipping-instructions`, `/api/v1/receivables`, `/api/v1/feature-flags`. 기존 접두어 아래 신규 경로(`/quotations/{id}/render`, `/proforma-invoices/{id}/render`, `/shipments/{id}/commercial-invoices…`, `/shipments/{id}/receivable…`, `/sales-orders/{id}/short-close`)는 행만 추가한다. 등재 누락 = 매트릭스 공회전이라 각 PR DoD에 넣는다.
- **S3-3 추가 규칙 ① — 미리보기는 같은 검증 함수·저장 0**: `…/preview`는 POST(본문 필요)이지만 **채번·이벤트·audit·멱등 키 소비·잠금 0**이며 발행과 **같은 순수 검증 함수**의 결과를 싣는다(sA §A10 — 화면·서버 이중 판정 금지). 화면은 검증 결과를 다시 계산하지 않는다.
- **S3-3 추가 규칙 ② — 파일은 documents 통로 하나로만 나간다**: 렌더 산출물의 다운로드는 기존 `GET /api/v1/documents/{document_id}/download`(attachment·octet-stream·nosniff — ADR-0029, `code:modules/documents/router.py:176-189`)뿐이다. 서류 전용 다운로드 경로·인라인 PDF 응답을 만들지 않는다. 화면은 `downloadFile`(`fe:lib/download.ts:5-14`)만 쓴다.
- **S3-3 추가 규칙 ③ — 버튼 근거는 서버 `allowed_actions`**(D4). S3-3이 새로 여는 동작은 화면이 역할·상태로 다시 판정하지 않는다.

**근거**: `D:398`(§18.4), `D:370`(§17.4), `D:360`(§17.2 version), `D:390-392`(§18.1), `D:396`(§18.3 에러 봉투), `D:26`(ADR-02 금액·시각), ADR-0029.

**대안(기각)**: (a) 서류 전용 `GET /commercial-invoices/{id}/files/{kind}.pdf` — 다운로드 권한·헤더·감사가 두 갈래가 된다. (b) PDF 인라인 미리보기(`Content-Disposition: inline` 또는 `<iframe src=blob>`) — ADR-0029 attachment 고정 계약을 서류만 예외로 여는 것이고, 화면 미리보기는 JSON(D9)이 이미 맡는다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(경로·필드 추가).

---

## D2. 엔드포인트 목록

> 역할 약어: A=ADMIN, T=TRADE, L=LOGISTICS, C=CERT, V=VIEWER, "전"=5역할. ADMIN은 `require_roles`에서 상시 통과. 멱등 = `Idempotency-Key` 필수, ver = 본문 `version` 필수. 역할 열은 sA §A16·sB §B18의 **화면 계약용 사본**이며 정본은 안전 부록 매트릭스다(어긋나면 매트릭스가 이긴다).

### D2-1. 자사 레터헤드 (sA §A3 데이터)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| LH1 | `GET /company-profiles` | 전 | — | Page(최신 `effective_from`·id 내림차순) | `Page[CompanyProfile]` | — |
| LH2 | `GET /company-profiles/as-of` | 전 | — | `date`(필수, `YYYY-MM-DD`) | `{date, profile: CompanyProfile \| null}` | 422 날짜 형식 |
| LH3 | `POST /company-profiles` | A | 멱등 | `{legal_name_en, legal_name_ko?, address_en, address_ko?, business_reg_no, phone?, email?, signer_name_en?, signer_title_en?, effective_from}` | 201 `CompanyProfile` | 422 사업자번호 체크섬·서명 쌍·`effective_from` 미래 |

- 수정·삭제 엔드포인트는 없다(불변 판 — 수정 = 새 판 등록). `LH2`의 `profile=null`은 "그날 유효한 판 없음"이고 **가장 이른 판으로 메우지 않는다**(sA §A3 as-of 규칙).

### D2-2. QT·PI 렌더 (sA §A13)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| R1 | `POST /quotations/{id}/render` | A·T | 멱등 | `{format: "PDF"\|"XLSX", language: "EN"\|"KO"}` | 201(신규) / 200(이미 있음) `Rendition` | 409 `EXPORT_DOCS.RENDITION.SOURCE_NOT_FROZEN`(DRAFT)·`EXPORT_DOCS.LETTERHEAD.NOT_REGISTERED`·`NOT_EFFECTIVE`, 422 `GLYPH_UNSUPPORTED`(항목) |
| R2 | `POST /proforma-invoices/{id}/render` | A·T | 멱등 | 같음 | 같음 | 같음(SOURCE_NOT_FROZEN 제외 — PI는 생성=동결) |

- 산출물 목록은 **상세 응답에 내장**한다(`renditions` 블록 — D3). 상한이 (형식 2 × 언어 2 = 4건)으로 고정된 집합이라 Page가 아니다(S3-2 D1 "상한 고정 내장 집합" 선례).
- 취소·만료된 QT·PI도 렌더할 수 있다(sA §A13 — 기록 보존). 화면은 상태 배지로 구분하고 파일에는 워터마크가 없다는 안내를 단다(D7).

### D2-3. CI·PL (sA §A4~§A7·§A10~§A13)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| CI1 | `POST /shipments/{id}/commercial-invoices/preview` | A·T·L | — (저장 0) | `CommercialInvoiceDraft`(아래) | `CommercialInvoicePreview`(D3) | 409 `EXPORT_DOCS.PREVIEW.SHIPMENT_NOT_FROZEN`(PLANNED — 이 부록 신설), 422 `EXPORT_DOCS.SHIPMENT.KIND_NOT_SUPPORTED`(수입) |
| CI2 | `POST /shipments/{id}/commercial-invoices` | A·T·L | 멱등+선적 ver | `CommercialInvoiceDraft` + `{shipment_version}` | 201 `CommercialInvoiceDetail` | 409 `EXPORT_DOCS.SHIPMENT.NOT_INSPECTED`·`EXPORT_DOCS.CI.ALREADY_ISSUED`·`LETTERHEAD.*`·version·`LOCK_BUSY`, 422 `EXPORT_DOCS.VALIDATION.FAILED`(`detail.items`) |
| CI3 | `GET /commercial-invoices` | 전 | — | `shipment_id?`, `so_id?`, `status?`, `q?`(CI 번호 부분 일치 ≤100자), Page | `Page[CommercialInvoiceListItem]` | 422 파라미터 |
| CI4 | `GET /commercial-invoices/export.csv` | 전 | — | CI3과 같은 조건 | CSV(UTF-8 BOM·수식 이스케이프) | — |
| CI5 | `GET /commercial-invoices/{id}` | 전 | — | — | `CommercialInvoiceDetail`(라인·포장·합계·산출물·S/I 요약·`allowed_actions`) | 404 |
| CI6 | `GET /commercial-invoices/{id}/status-log` | 전 | — | Page | `Page[StatusLogEntry]`(기존 형태) | 404 |
| CI7 | `PATCH /commercial-invoices/{id}` | A·T·L | ver | `{internal_note?, assignee_id?}`(FREE 2열뿐) | `CommercialInvoiceDetail` | 409 version |
| CI8 | `POST /commercial-invoices/{id}/transitions` | A·T·L | 멱등+ver | `{to_status: "CANCELLED", reason, version}`(`Literal` 1값) | `CommercialInvoiceDetail` | 409 `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE`(+`successors`), 422 `TRADE_DOCS.TRANSITION.REASON_REQUIRED` |
| CI9 | `POST /commercial-invoices/{id}/reissue/preview` | A·T·L | — | `CommercialInvoiceDraft` | `CommercialInvoicePreview` | CI1과 같음 + 409 `TRADE_DOCS.TRANSITION.NOT_ALLOWED`(취소된 CI) |
| CI10 | `POST /commercial-invoices/{id}/reissue` | A·T·L | 멱등+ver | `CommercialInvoiceDraft` + `{reason, version}` | 201 `CommercialInvoiceDetail`(새 CI) | CI2와 같음 + 422 사유 |
| CI11 | `POST /commercial-invoices/{id}/render` | A·T·L | 멱등 | `{}` | `CommercialInvoiceDetail`(빠진 산출물만 채움) | 409 `TRADE_DOCS.TRANSITION.NOT_ALLOWED`(취소 CI의 미생성분) |

`CommercialInvoiceDraft`(발행·재발행·미리보기 공통 본문 — sA §A5·A6·A7·A9의 사람 입력만, 원천 사본 필드는 **스키마에 없다**):
```
{ transport_mode, port_of_loading, port_of_discharge, final_destination?, vessel_voyage?, sailing_on?,
  goods_origin_country_code, shipping_marks?, remarks?,
  consignee_address_en?,          // 스냅샷이 NULL일 때만 허용 — 비NULL 스냅샷에 보내면 422(덮어쓰기 금지, sA §A9 ④)
  bank_account_id?,               // SO가 PI 은행 스냅샷을 가지면 보내면 422(sA §A9 ⑧) — 자동 선택 0
  lines: [{ shipment_line_id, description_en?, sku_hs_code_id?, origin_country_code? }],   // 선적 살아 있는 라인 전부(V11)
  packages: [{ seq, package_type, mark_from, mark_to,
               net_weight_kg: "12.345", gross_weight_kg: "13.000",                    // 십진 문자열 — 서버가 정확 파싱해 그램 정수
               length_cm: "45.0", width_cm: "30.0", height_cm: "25.5",                // 십진 문자열 — 서버가 mm 정수
               items: [{ shipment_line_id, quantity_per_pkg }] }],
  acknowledge_notices: [code…] }
```
- **중량·치수는 십진 문자열로 받고 서버가 정확 파싱**한다(kg 소수 3자리 → g, cm 소수 1자리 → mm, 자릿수 초과·0·음수 422). 금액 입력의 `parse_minor_amount` 관례(서버가 파서의 유일 출처)를 따른다. 프런트는 형식 힌트만 검사하고 g·mm 환산·합계·CBM을 계산하지 않는다(미리보기 응답의 `*_text`).
- `packages.items`는 아직 CI 라인이 없으므로 `shipment_line_id`로 가리킨다(서버가 발행 TX에서 `ci_line_id`로 바꿔 저장).

### D2-4. S/I (sA §A8)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| SI1 | `GET /commercial-invoices/{id}/shipping-instructions` | 전 | — | Page(최신순) | `Page[ShippingInstructionItem]`(`is_current`·`superseded_by`·`void_by_ci_cancel` 파생 표시) | 404 |
| SI2 | `POST /commercial-invoices/{id}/shipping-instructions/preview` | A·T·L | — | `ShippingInstructionDraft` | `{freight_term(도출), forwarder, consignee_preview, notify, dg_block, validation:{items}}` | 409 `EXPORT_DOCS.SI.CI_NOT_LIVE`, 422 `SI.FORWARDER_MISSING` |
| SI3 | `POST /commercial-invoices/{id}/shipping-instructions` | A·T·L | 멱등 | `ShippingInstructionDraft` | 201 `ShippingInstructionDetail` | 409 `CI_NOT_LIVE`, 422 `SI.FORWARDER_MISSING`·`SI.CONSIGNEE_MODE_NOT_ALLOWED`·`VALIDATION.FAILED` |
| SI4 | `GET /shipping-instructions/{id}` | 전 | — | — | `ShippingInstructionDetail`(+`renditions`) | 404 |
| SI5 | `POST /shipping-instructions/{id}/render` | A·T·L | 멱등 | `{}` | `ShippingInstructionDetail` | — |

`ShippingInstructionDraft` = `{consignee_mode: "PARTY"|"TO_ORDER"|"TO_ORDER_OF", consignee_order_of?, bl_type, original_bl_count?, special_instructions?, acknowledge_notices:[…]}`. **`freight_term`은 본문에 없다**(Incoterms에서 도출 — sA §A8). S/I 재발행 = SI3 재호출(서버가 `supersedes_si_id`를 현재 S/I로 채움).

### D2-5. 채권·aging (sB §B1·§B2·§B12·§B14·§B15)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| RV1 | `POST /shipments/{id}/receivable/preview` | A·T | — (저장 0·잠금 0) | `{invoice_on, invoice_ref}` | `ReceivablePreview`(D3 — 충당 예정·미수·만기·**SO 완료 여부**·L/C 상한 판정) | 409 `RECEIVABLES.RECEIVABLE.SHIPMENT_NOT_FROZEN`·`ALREADY_OPEN`, 422 `NOT_EXPORT`·`INVOICE_DATE_INVALID` |
| RV2 | `POST /shipments/{id}/receivable` | A·T | 멱등+선적 ver | `{invoice_on, invoice_ref, shipment_version}` | 201 `ReceivableDetail`(+`sales_order:{status, completed}`) | RV1과 같음 + 422 `LC_AMOUNT_EXCEEDED`, 409 version·`LOCK_BUSY` |
| RV3 | `GET /receivables` | 전 | — | `partner_id?`, `so_id?`, `shipment_id?`, `currency?`, `source_kind?`, `status?`(OPEN·CANCELLED, **기본 OPEN**), Page | `Page[ReceivableListItem]` | 422 |
| RV4 | `GET /receivables/export.csv` | 전 | — | RV3 조건 | CSV(UTF-8 BOM) | — |
| RV5 | `GET /receivables/aging` | 전 | — | `partner_id?`, `currency?`, Page(거래처×통화 행) | `Page[AgingRow]` + 봉투 밖 `as_of`(KST 오늘)·`krw_total`(전부 환산 가능할 때만) | — |
| RV6 | `GET /receivables/{id}` | 전 | — | — | `ReceivableDetail`(미수 분해·만기·SO·선적·`allowed_actions`) | 404 |
| RV7 | `POST /receivables/opening` | A | 멱등 | `{partner_id, currency, gross_amount, fx_rate?, fx_rate_date?, invoice_on, due_on, invoice_ref, note}` | 201 `ReceivableDetail` | 422 환율 필수(통화≠KRW)·`due_on < invoice_on` |
| RV8 | `POST /receivables/{id}/cancel` | A | 멱등+ver | `{reason, version}` | `ReceivableDetail` | 409 `NOT_OPEN`·`PAYMENTS_EXIST`·`SO_COMPLETED`·version |

- **목록 필터는 저장 열만**이다. 파생값(미수·입금 상태·aging 구간)으로 거르는 필터는 열지 않는다 — 페이지를 뽑은 뒤 파이썬에서 거르면 `total`과 페이지가 틀어지고, SQL로 FIFO 충당을 재현하면 미수 정의가 두 곳이 된다(sB §B4 "단일 정의 한 곳"). 구간별로 보려면 RV5(요약)를 쓴다.
- **aging에 `as_of` 파라미터를 열지 않는다**(기준일 = 서버 KST 오늘 1개). 과거 기준일을 받으면 그 뒤에 들어온 입금까지 섞인 '가짜 과거 aging'이 된다(입금 원장에 기준일 절단 정의가 없다). 시험은 서비스 함수 인자 주입으로 한다(sB §B12 ①).
- `invoice_ref`는 **SHIPMENT 채권에서 필수**로 받는다(sB §B2는 NULL 허용 — 판정 후보 ① → 자율 확정 더 엄격, §10 ②).

### D2-6. 입금 (sB §B3·§B5 — S3-1 PI 입금 계약 승계)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| PY1 | `GET /receivables/{id}/payments` | 전 | — | Page | `Page[PaymentRow]` + `summary`(채권 총액·순입금·선수금 충당·미수·입금 상태) | 404 |
| PY2 | `POST /receivables/{id}/payments` | A·T | 멱등 | `{received_on, amount, reference, acknowledge_duplicates?: [payment_id…]}` | 201 `{payment, summary, warnings}` | 409 `RECEIVABLES.RECEIVABLE.NOT_OPEN`·`PAYMENTS.PAYMENT.POSSIBLE_DUPLICATE`(`detail.candidates`), 422 `CURRENCY_MISMATCH`·`EXCEEDS_DUE`·금액·날짜 |
| PY3 | `POST /payments/{id}/reversal`(기존) | A·T | 멱등 | `{reason}`(기존) | 201 `{payment, summary, warnings}` — `summary`는 대상(PI 또는 채권)에 맞는 형태 | 기존 + 409 `POSSIBLE_DUPLICATE` 비해당 |
| PY4 | `POST /proforma-invoices/{id}/payments`(기존) | A·T | 멱등 | 기존 + `acknowledge_duplicates?` | 기존 | 기존 + 409 `POSSIBLE_DUPLICATE`(sB §B5 ④ PI 경로 확장) |

- `PaymentRow`(`fe:lib/payment.ts:6-21`)의 `pi_id`는 `number | null`이 되고 `receivable_id: number | null`이 더해진다(정확히 하나 — sB §B3).
- **`POSSIBLE_DUPLICATE`의 `detail.candidates`**는 `[{payment_id, received_on, reference, target_label("PI-2026-0003" 또는 "SH-2026-0001 채권")}]`이다. **금액은 싣지 않는다** — 정의상 입력한 금액과 같아 화면이 "같은 금액"으로 표기하면 되고, 에러 detail에 금액을 싣지 않는 S3-2 규칙(S3-2 부록 D D1)을 지킨다. sB §B5 ④는 `detail.payment_ids`만 적었다 — 화면이 사람에게 무엇을 확인시키는지 보이려면 날짜·참조·대상이 필요하므로 이 부록이 형태를 확정한다.

### D2-7. SO 잔량 종결 (sB §B7)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| SC1 | `POST /sales-orders/{id}/short-close` | A·T | 멱등+ver | `{reason, version}`(사유 2~500자·제어문자 불가) | `SalesOrderDetail`(COMPLETED·`short_close` 블록) | 409 `SALES_ORDERS.SHORT_CLOSE.NOT_IN_SHIPMENT`·`NOTHING_TO_CLOSE`·`SHIPMENT_PENDING`(`detail.shipments:[{id, doc_number}]`)·version, 422 사유 |

- 미리보기 엔드포인트는 두지 않는다: 가드(살아 있는 선적 전부 채권 보유)를 통과하면 **반드시 COMPLETED**가 된다(sB §B7 ②의 (가)(나)(다)가 가드로 보장됨 — IN_SHIPMENT는 살아 있는 선적 ≥1을 함의). 대화상자는 SO 상세가 이미 가진 라인별 `shipment_open_quantity`(S3-2 X3)로 종결될 수량을 보여 준다.

### D2-8. 기능 플래그 (sB §B11 — L/C 부록이 같은 경로를 정하면 그쪽 우선)

| # | 메서드·경로 | 역할 | 멱등/ver | 요청 골격 | 응답 | 화면이 처리할 에러 |
|---|---|---|---|---|---|---|
| FF1 | `GET /feature-flags` | A | — | Page | `Page[{code, name_ko, is_enabled, registered(행 존재), version \| null, updated_at, updated_by_name}]` — **레지스트리 전 항목**(행이 없으면 `registered=false, is_enabled=false`) | — |
| FF2 | `PUT /feature-flags/{code}` | A | 멱등+ver(행이 있을 때) | `{is_enabled, version?}` | 위 1행 | 404(레지스트리 밖 code), 409 version |

- 행이 없는 플래그도 목록에 나와야 화면이 "꺼짐(미등록)"을 보여 주고 켤 수 있다(`code:modules/platform/service.py:93-105` "행이 없으면 꺼짐"). 목록 원천 = 코드 레지스트리, 값 = 행.

### D2-9. 기존 엔드포인트 확장

| # | 경로 | 변경 | 근거 |
|---|---|---|---|
| X1 | `GET /shipments/{id}` | `allowed_actions`에 D4 신규 4종, 블록 2개 가산: `export_docs: {gate:{issuable, reason_code}, live_ci: {id, doc_number, doc_date, revision_no} \| null, cancelled_count}` · `receivable: {id, status, gross_text, outstanding_text, settlement} \| null` | sA §A2·§A20, sB §B1 |
| X2 | `GET /sales-orders/{id}` | `allowed_actions`(D4 — S3-3 신규분만 `SHORT_CLOSE`) · `short_close: {at, by_name, reason} \| null` · `receivables_summary: {count, gross_total_text, outstanding_total_text, unallocated_advance_text}`(SO 통화 — 미충당 선수금 sB §B4 ②) | sB §B4·§B7 |
| X3 | `GET /quotations/{id}`·`GET /proforma-invoices/{id}` | `allowed_actions`(S3-3 신규분만 `RENDER`) · `renditions` 블록(D3) | sA §A13 |
| X4 | `GET /shipments/{id}/milestones`(보드) | `PAYMENT_DUE` 행에 `settlement: {state, outstanding_text} \| null`, `is_overdue`는 **미충족일 때만** 불리언(충족·NOT_APPLICABLE = false, 만기 UNKNOWN = null) · `PRESENTATION_DEADLINE` 행에 `presented_on \| null`(L/C 부록 제시 기록) | sB §B13 ⑤, D14 |
| X5 | `GET /document-flow/{kind}/{id}` | 수출선적 노드 아래 **CI 노드**(취소 포함, `revision_no`) 가산. 쿼리 상한 5 → **6**(CI는 선적 id 목록으로 1쿼리). 채권은 DocKind가 아니라 노드가 아니다(선적 상세에서 본다) | `code:modules/trade_chain/document_flow.py:1-14` |
| X6 | `GET /documents`·`GET /documents/{id}` | 항목에 `is_generated: bool`·`generated_from: {source_type, source_id, doc_number, doc_kind, format, language} \| null` 가산(삭제 버튼 숨김 근거 — 서버 409 `GENERATED_LOCKED`가 정본) | sA §A13 삭제 잠금 |
| X7 | 여신 평가 응답(확정 패널·승인 스냅샷) | **무변경**(`receivables_reflected`·`receivable_amount`·`exposure_is_partial` 그대로 — `code:modules/credit/evaluation.py:80-107`). 값만 바뀐다 | sB §B6 ⑦ |

**근거**: `D:398`(페이지네이션·N+1), S3-2 부록 D D1(상한 고정 내장 집합), sA·sB 해당 절.

**대안(기각)**
- (a) 채권을 `GET /shipments/{id}/receivables`(Page)로 — 선적당 살아 있는 채권 ≤1(부분 유니크)이라 내장 블록이 맞다. 취소 이력은 RV3 `shipment_id=&status=CANCELLED`로 본다.
- (b) CI 발행을 `/commercial-invoices`(본문에 `shipment_id`)로 — 원천이 경로에 있어야 부모-자식 검증(§18.1 ②)과 S3-2 참조 생성 관례(`/sales-orders/{so_id}/shipments`)가 맞는다.
- (c) aging에 파생 필터(구간)를 목록 RV3에 — 위 "저장 열만" 사유.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(경로·필드 가산 — 단 PY2 `detail.candidates` 형태는 화면·시험이 함께 바뀐다).

---

## D3. 응답 스키마 골격 — 상세는 1요청·쿼리 수 고정

```
Rendition { id, doc_kind: "QT"|"PI"|"CI"|"PL"|"SI", format: "PDF"|"XLSX", language: "EN"|"KO",
            document_id, file_name, size_bytes, template_version, created_at, created_by_name }
RenditionSet { items: Rendition[], expected: [{doc_kind, format, language}], pending: bool }
  // expected = 그 원천의 형식×언어 행렬(QT·PI 4, CI 4[CI·PL × PDF·XLSX × EN], SI 2). pending = 자동 렌더 대상(CI·SI)인데 빠진 칸이 있음.

CommercialInvoicePreview {
  gate: { issuable: bool, reason_code: "SHIPMENT_NOT_INSPECTED" | null, shipment_status },
  seller: CompanyProfile | null,                       // 오늘(KST) as-of 레터헤드 — null이면 V9 BLOCK 항목도 함께
  consignee: { partner_id, name_en, address_en | null, address_source: "SNAPSHOT"|"MANUAL"|null },
  notify: {...} | null, buyer_po: { no, date } | null, terms: { currency, incoterm_text, payment_terms_text, fx_rate_text },
  bank: { source: "PI_SNAPSHOT"|"BODY"|null, summary_text | null }, bank_candidates: [{id, label}],   // PI 스냅샷이 없을 때만 — 선택은 사람
  lines: [{ shipment_line_id, line_no, sku_code, description_en_snapshot | null, quantity, unit_price_text,
            line_amount_text, is_free, hs_candidates: [{sku_hs_code_id, hs_code, hs_version, label}],
            dg: {flag, un_number, dg_class} , reference: { unit_weight_text | null, box_qty | null } }],
  packages: [{ seq, package_count, nw_total_text, gw_total_text, cbm_text }],      // 본문 포장 그룹의 서버 계산 결과
  totals: { total_amount_text, package_count, nw_text, gw_text, cbm_text },
  reference_dates: { etd_planned | null, etd_actual | null },                      // 참고값 — 본문에 자동 기입 0(sA §A9 ⑬)
  validation: { items: [{code, severity: "BLOCK"|"NOTICE", path, message_ko}], blocking_count, unacknowledged_notices: [code] }
}

CommercialInvoiceDetail { id, doc_number, doc_date, status, revision_no, supersedes: {id, doc_number}|null,
  superseded_by: {id, doc_number}|null, shipment: {id, doc_number}, sales_order: {id, doc_number},
  seller(판 고정 스냅샷), consignee, notify, transport{…}, terms, bank, lines[…], packages[…], totals,
  acknowledged_notices: [code], renditions: RenditionSet, current_si: {id, doc_number, doc_date}|null, si_count,
  receivable: {id}|null, internal_note, assignee, version, allowed_actions }

ReceivablePreview { gross_text, currency, advance_alloc_text, outstanding_text,
  due: { value | null, basis: "PLANNED"|"ACTUAL"|null, reason_code | null },
  sales_order: { will_complete: bool, shipments_without_receivable: [{id, doc_number}] },
  lc_check: { applicable: bool, upper_text | null, after_total_text | null, exceeds: bool } }

ReceivableListItem { id, source_kind, partner: {id, name}, currency, gross_text, paid_text, advance_alloc_text,
  outstanding_text, settlement: "UNPAID"|"PARTIALLY_PAID"|"PAID"|"CANCELLED", invoice_on, invoice_ref,
  due: { value|null, basis|null, reason_code|null }, days_overdue | null, bucket, shipment: {id, doc_number}|null,
  sales_order: {id, doc_number}|null, status }
ReceivableDetail = ReceivableListItem + { fx_rate_text, cancel: {at, by_name, reason}|null, note|null, version, allowed_actions }

AgingRow { partner: {id, name}, currency, not_due_text, d1_30_text, d31_60_text, d61_90_text, d91_plus_text,
  due_unknown_text, due_unknown_count, total_text, krw_total_text | null, not_convertible_count }
```

- **쿼리 수 상한(K 시험으로 고정)**: CI 상세 = 헤더 1·라인 1·포장 1·포장 내용 1·산출물 1·S/I 요약 1·채권 1·담당자 1 = **8**(라인·포장 수와 무관). 채권 목록 1페이지 = 채권 1·선적 1·마일스톤 1·통관 1·SO 1·PI 순입금 1·채권 입금 합 1·거래처 1 = **고정**(sB §B12 ⑤ 묶음 로딩 — 실측값을 PR이 상한으로 고정). aging = 거래처×통화 행과 무관한 상수.
- **`*_text`가 없으면 화면은 그 칸을 '—'로 그린다**(0으로 그리지 않는다 — `fe:lib/labels.ts:184-189` `EMPTY`).
- `bucket` 값: `NOT_DUE`·`D1_30`·`D31_60`·`D61_90`·`D91_PLUS`·`DUE_UNKNOWN`·`SETTLED`(미수 0 — 목록에서만 보이고 aging 합에는 없다).

**근거**: `D:398`(N+1 금지), sB §B4·§B12, sA §A10·§A13, S3-2 부록 D D3(상세 쿼리 수 고정 선례 — `code:modules/trade_chain/shipment_view.py:3-4` "고정 쿼리 수 8").

**대안(기각)**: 미수·aging 구간을 프런트에서 계산(입금 합 − 충당) — FIFO 충당 정의가 화면에 복제된다(sB §B4 단일 정의 위반).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(필드 이동).

---

## D4. `allowed_actions` 패턴 — S3-3이 여는 동작은 전부 서버 계산

**결정**
- **규칙**: S3-3이 새로 여는 쓰기 동작의 버튼 노출 근거는 상세 응답의 `allowed_actions: string[]`뿐이다. 서버는 역할·상태·사슬 조건에서 계산하고(표시 편의 — 쓰기 시 서버가 다시 검사), 화면은 `includes`만 한다(`code:modules/trade_chain/shipment_view.py:12`, `fe:lib/shipment.ts:331-345` 선례).
- **기존 전표 화면의 클라이언트 판정은 이번에 옮기지 않는다**: QT·PI·SO 상세는 `canEditSalesOrder` 같은 상태 규칙 복제(`fe:lib/doc-status.ts:115-129`)를 쓰고 있다. S3-3은 그 응답에 `allowed_actions`를 **S3-3 신규 동작만 담아** 더한다. 필드 이름이 같아 '전부 담겼다'고 오해할 수 있으므로 서버 상수 `S33_ACTIONS_ONLY_KINDS = {QT, PI, SO}`와 독스트링·vitest로 "이 세 응답의 allowed_actions는 부분 집합"을 명시한다(전환은 부채 D-01).

| 응답 | 액션 | 서버 조건(요지 — 정본은 각 부록) | 근거 |
|---|---|---|---|
| 선적 상세 | `PREVIEW_EXPORT_DOCS` | EXPORT · 상태 ∉ {PLANNED, CANCELLED} · 역할 A·T·L | sA §A2 미리보기 |
| | `ISSUE_EXPORT_DOCS` | EXPORT · 상태 ∈ `CI_ISSUABLE_SHIPMENT_STATES` · 살아 있는 CI 없음 · A·T·L (**S3-3 운영 경로에서 항상 부재**) | sA §A2 |
| | `CREATE_RECEIVABLE` | EXPORT · 동결 이후 살아 있음 · 살아 있는 채권 없음 · A·T (CI 경로와의 관계는 §10 ①) | sB §B1 |
| | (S3-2 9종 무변경) | `RELEASE_ORDER`…`EDIT_CUSTOMS` | `code:modules/trade_chain/shipment_view.py:70-91` |
| CI 상세 | `CANCEL` · `REISSUE` · `RENDER`(pending일 때만) · `ISSUE_SI`(살아 있음) · `EDIT_META` | 살아 있는 CI · A·T·L / `EDIT_META`는 취소 후에도(메모 사후 정정 — PO 선례 `fe:lib/doc-status.ts:149-150`) | sA §A11·§A13 |
| S/I 상세 | `RENDER`(pending일 때만) | A·T·L | sA §A13 |
| 채권 상세 | `RECORD_PAYMENT` · `CANCEL` | OPEN · A·T / `CANCEL`은 A · 순입금 0 · SO ≠ COMPLETED | sB §B5·§B15 |
| 입금 행(PY1 항목) | `reversible: bool` | RECEIPT · 역기록 없음 · 대상 열림 · 역할 A·T | sB §B5 ① |
| SO 상세 | `SHORT_CLOSE` | IN_SHIPMENT · 잔량 > 0 · A·T(채권 없는 선적이 있어도 **노출** — 누르면 409 `SHIPMENT_PENDING`이 조치 목록을 준다) | sB §B7 ③ |
| QT·PI 상세 | `RENDER` | QT ≠ DRAFT / PI 항상 · A·T | sA §A13·§A16 |
| 채권 목록(봉투 밖) | `CREATE_OPENING` | A | sB §B14 |

- `SHORT_CLOSE`를 채권 미등록 선적이 있을 때 숨기지 않는 이유: 숨기면 사람이 "왜 버튼이 없는지" 알 수 없다(막다른 길). 서버 409가 어떤 선적을 먼저 채권화·취소해야 하는지 알려 준다. 반대로 `ISSUE_EXPORT_DOCS`는 숨기고 **게이트 사유 문구**(D9)로 이유를 보인다 — S4-2 전에는 사람이 할 수 있는 조치가 없기 때문이다.
- **대사 시험(K)**: ① 서버 — 각 액션 ↔ (메서드, 경로) 매핑 상수 `ACTION_ENDPOINTS`를 두고, 액션이 나오는 역할 집합 ⊆ authz 매트릭스 ALLOW 집합임을 역할×상태 전수로 단언(액션이 보이는데 403이 나는 버튼 0). ② 프런트 — vitest 소스 계약: S3-3 신규 화면 파일(D6 표의 신규 라우트·컴포넌트)에서 `hasRole(`·`status ===` 비교로 버튼을 거는 코드 0(셸 메뉴 제외).

**근거**: `D:390`(인가는 API에서 강제 — 화면은 편의), PROGRESS 'S3-2 PR-3a' 인계 계약(`fe:lib/shipment.ts:6` "버튼 노출은 서버가 준 allowed_actions만"), S3-2 부채 R-4b-5(세트 편집 = 화면 역할 판정 — 같은 결함 재발 방지).

**대안(기각)**: (a) QT·PI·SO의 전체 `allowed_actions`를 이번에 산출 — 기존 동작 수십 개의 규칙을 서버로 옮기는 회귀 표면이 S3-3 범위를 넘는다. (b) S3-3 동작도 클라이언트 판정 — `CI_ISSUABLE_SHIPMENT_STATES`·채권 존재·COMPLETED 같은 사슬 조건을 화면에 복제하게 되고, sA §A2 게이트가 S4-2에서 열릴 때 화면도 고쳐야 한다(서버 상수 1곳이 아니게 된다).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(필드·상수).

---

## D5. 에러 코드 — 이 부록 신설분과 화면 반응표

**이 부록 신설(2건)**

| 코드 | HTTP | 언제 | 문구(원인+조치) |
|---|---|---|---|
| `EXPORT_DOCS.PREVIEW.SHIPMENT_NOT_FROZEN` | 409 | PLANNED 선적의 CI 미리보기 | "계획 단계 선적은 라인이 바뀔 수 있어 서류를 미리볼 수 없습니다. 출고지시 후 다시 시도해 주세요." |
| `EXPORT_DOCS.RENDITION.LANGUAGE_NOT_SUPPORTED` | 422 | CI·PL·S/I에 `language=KO`(스키마는 공용 `Literal`이라 서비스가 거부) | "이 서류는 영문으로만 만듭니다. 언어를 EN으로 선택해 주세요." |

**화면 반응표(타 부록 신설분 — 재정의하지 않고 화면 처리만 고정)**

| 코드 | 화면 반응 |
|---|---|
| `EXPORT_DOCS.VALIDATION.FAILED` | 작성 화면 상단 오류 요약 + `detail.items[].path`로 해당 칸에 `aria-describedby` 연결(BLOCK 빨강·NOTICE 확인 체크박스). 프런트 라벨은 항목 코드 단일 표(D17 대사 시험) |
| `EXPORT_DOCS.SHIPMENT.NOT_INSPECTED` | 발행 버튼은 원래 숨김(D4). 경합으로 뜨면 게이트 안내 문구(D9)로 교체 |
| `EXPORT_DOCS.CI.ALREADY_ISSUED` | "이미 발행된 CI가 있습니다 — 그 CI에서 '재발행'을 쓰세요" + `detail.doc_number` 링크 |
| `EXPORT_DOCS.LETTERHEAD.NOT_REGISTERED`·`NOT_EFFECTIVE` | 관리자에게는 `/settings/company-profile` 링크, 그 밖 역할에는 "관리자에게 자사 정보 등록을 요청하세요" + `detail.as_of` |
| `EXPORT_DOCS.RENDITION.PENDING` | 파일 칸에 "파일 생성 실패 — 다시 만들기" 버튼(`RENDER` 액션) |
| `DOCUMENTS.DOCUMENT.GENERATED_LOCKED` | 삭제 버튼은 원래 숨김(X6). 경합 시 "시스템이 만든 서류 파일은 삭제할 수 없습니다" |
| `RECEIVABLES.RECEIVABLE.LC_AMOUNT_EXCEEDED` | 채권 등록 대화상자에 L/C 상한·등록 후 합계(RV1 `lc_check`) 표시, 제출 버튼 비활성(미리보기가 `exceeds=true`면) |
| `PAYMENTS.PAYMENT.POSSIBLE_DUPLICATE` | D12 이중 입력 확인 블록 |
| `SALES_ORDERS.SHORT_CLOSE.SHIPMENT_PENDING` | 대화상자 안에 `detail.shipments` 링크 목록 + "이 선적에 채권을 등록하거나 취소한 뒤 다시 시도하세요" |
| `TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE` | 기존 처리(후속 전표 링크) — CI 취소(채권 생존)·선적 취소(CI·채권 생존)에 그대로 |
| `COMMON.CONCURRENCY.LOCK_BUSY`·`VERSION_CONFLICT` | 기존 처리(재시도 안내·최신 불러오기) |

**근거**: `D:396`(코드 3세그먼트·카탈로그 1:1), `D:398`(사용자=한국어+원인+조치), `code:core/errors/catalog.py:1-9`.

**대안(기각)**: PLANNED 미리보기에 `NOT_INSPECTED` 재사용 — 조치가 다르다(출고지시 vs 검수 대기). 같은 코드면 화면 안내가 틀린다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D6. 화면 목록·라우트·셸 메뉴

**결정 — 라우트**(`fe:App.tsx:94-147`에 가산)

| 라우트 | 파일 | 성격 |
|---|---|---|
| `/receivables` | `routes/receivables.tsx` | 채권 목록 + 'aging 요약' 탭(쿼리 `?view=aging`) |
| `/receivables/:receivableId` | `routes/receivable-detail.tsx` | 채권 상세·입금 패널 |
| `/shipments/:shipmentId/export-docs/new` | `routes/export-docs-create.tsx` | CI·PL 작성 2단(입력 → 서버 미리보기 → 발행) |
| `/commercial-invoices/:ciId` | `routes/commercial-invoice-detail.tsx` | CI 상세(PL·S/I·산출물) |
| `/commercial-invoices/:ciId/reissue` | `routes/export-docs-create.tsx`(재발행 모드) | 현재 CI 값으로 채운 작성 화면 + 사유 |
| `/shipping-instructions/:siId` | `routes/shipping-instruction-detail.tsx` | S/I 상세(읽기·파일) |
| `/settings/company-profile` | `routes/settings-company-profile.tsx` | 레터헤드 판 목록·새 판 등록(관리자) |

- 상세 파일명은 전부 `*-detail.tsx`라 `fe:routes/detail-layout.test.ts:6`의 glob에 자동 편입되어 390px 그리드 계약을 상속한다. 하한 7(`:16`)을 **10**으로 올리고 신규 3파일 포함을 단언한다.
- **CI 목록 화면·메뉴는 두지 않는다**(S3-3). 운영 경로에서 CI가 0건이므로(§0 가정 2) 빈 메뉴는 "고장"처럼 보인다 — 오더 보드의 '죽은 열 금지'(`code:modules/order_board/constants.py:3`) 원칙. CI3 목록 API는 문서 흐름·선적 상세·시험이 쓴다. 목록 화면·메뉴는 S4-2 INSPECTED 개방 PR이 붙인다(부채 D-02).
- **CI 작성은 대화상자가 아니라 전용 화면**이다: 운송 6칸·원산국·라인별 품명/HS/원산국·포장 그룹(그룹마다 내용물 N행)·NOTICE 확인까지 들어가 대화상자 안 스크롤로는 390px에서 입력을 잃기 쉽다. S3-2의 "독립 생성 화면 없음" 원칙(원천 없는 생성 금지)은 경로에 원천 선적 id가 있어 지켜진다.

**결정 — 셸 메뉴**(`fe:routes/shell.tsx:8-41`)
- `NAV`: `{ to: "/receivables", label: "채권" }`을 "선적" 다음에 넣는다(열람 전 역할 — sB §B12 ⑥). 순서: … 수주 · 발주 · 선적 · **채권** · 휴일 캘린더 ….
- `ADMIN_NAV`: `{ to: "/settings/company-profile", label: "자사 정보" }`를 "사용자·역할" 다음에 넣는다. 기능 플래그는 메뉴를 늘리지 않고 '정책 설정' 화면의 섹션으로 둔다(D16).
- 메뉴 표시는 역할 편의일 뿐 서버가 정본이다(`fe:routes/shell.tsx:37`).

**근거**: `D:317` §14 ① "미수 aging"(대시보드 — S3-3은 목록 화면의 요약 탭으로 먼저 제공, 대시보드는 P6) ⑦ "선적 관리(… 서류 버튼 …)" ⑭ "관리(템플릿 편집기 …)", `D:323` S3-2 화면 배정(서류 버튼은 S3-3), `W:129`.

**대안(기각)**: (a) '서류' 독립 메뉴(QT·PI·CI 파일 모음) — 파일은 문서보관소(`/documents`)가 이미 한 곳이다(§4.7). (b) aging을 별도 메뉴 — 채권과 같은 데이터의 다른 보기라 탭이면 충분하다. (c) 레터헤드를 '정책 설정' 섹션으로 — 정책 저장소는 키·값 레지스트리(ADR-0065)이고 레터헤드는 판 이력 표라 화면 모양이 다르다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(메뉴·라우트 1줄씩).

---

## D7. QT·PI 서류 파일 패널 — 만들기·내려받기·재출력

**결정**
- QT·PI 상세에 섹션 **"서류 파일"**을 '문서 흐름' 위에 둔다. 내용은 **형식×언어 2×2 표**(행 = PDF·엑셀, 열 = 영문·한글):
  - 칸이 비었으면 `RENDER` 액션이 있을 때 **[만들기]**, 없으면 '—'.
  - 칸에 파일이 있으면 **[내려받기]**(`downloadFile('/v1/documents/{document_id}/download', file_name)`) + 생성 시각(KST)·생성자.
- 보조문(고정): "처음 만든 파일이 정본으로 보관됩니다. 다시 내려받아도 같은 파일이며, 이후 레터헤드·양식이 바뀌어도 이 파일은 바뀌지 않습니다." — 재출력 = 같은 바이트(sA §A11·§A13).
- QT DRAFT: 섹션은 보이고 표 대신 "발행 후 만들 수 있습니다(초안은 회사 밖으로 나가는 파일을 만들지 않습니다)."
- 취소·만료 QT·PI: 표는 그대로, 상단에 "이 전표는 {취소/만료} 상태입니다 — 파일에는 상태가 표시되지 않으니 보낼 때 주의하세요."(sA §A13 워터마크 없음).
- 레터헤드 409: D5 반응표. **관리자에게만** 링크가 보이는 근거는 셸 `ADMIN_NAV`와 같은 표시 편의다(쓰기는 서버 A 전용).
- 동시 클릭: [만들기]는 칸마다 동기 잠금(ref) + 키 1개(D20). 같은 칸 경쟁은 서버 유일 키가 1건으로 수렴시키므로 응답 200/201 어느 쪽이든 칸을 채운다.
- '미리보기'는 상세 화면 자체다(동결된 원천의 값 = 파일 내용). PDF 인라인 보기는 두지 않는다(D1 ②).

**근거**: `D:215`(§7.6 "PDF·엑셀, 언어 변형"), sA §A1(QT·PI = EN·KO, DRAFT 렌더 0)·§A13, ADR-0029.

**대안(기각)**: (a) [PDF 내려받기] 한 번에 '없으면 만들고 내려받기' — GET이 쓰기를 하거나(GET 부작용) POST 응답이 파일 바이트가 되어 다운로드 통로가 두 갈래가 된다. (b) 4개 일괄 생성 버튼 — 쓰지 않을 한글 엑셀까지 정본 파일로 굳는다(정본은 지울 수 없다 — 삭제 잠금).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D8. 자사 정보(레터헤드) 관리 화면 — 판 목록 + 새 판 등록

**결정**
- `/settings/company-profile`(관리자): 위 **"현재 적용 판"** 카드(LH2 `date=오늘`) — 없으면 적색 "등록된 자사 정보가 없습니다 — 견적·PI·CI 파일을 만들 수 없습니다." / 아래 **판 목록**(LH1 Page — 적용 시작일·영문 상호·주소 요약·등록자·등록 시각) / [새 판 등록] 대화상자.
- 새 판 대화상자: 영문 상호·영문 주소(여러 줄)·사업자등록번호(숫자 10자리 — 형식 힌트만, 체크섬은 서버)·연락처·서명자(이름·직함 **둘 다 또는 둘 다 비움** 안내)·**적용 시작일(필수, 기본값 없음)**. 한글 상호·주소는 "한글 견적서·PI에 쓰입니다(선택)".
- 적용 시작일 보조문(고정): "이 날짜 이후 발행된 서류에 이 정보가 찍힙니다. 이미 발행된 견적·PI를 처음 출력하려면 그 발행일 이전 날짜로 등록해야 합니다(과거 날짜 등록은 기록에 남습니다)." — sA §A3 "관리자가 과거 `effective_from`으로 첫 판을 등록하면 풀린다(명시적 사람 결정)"를 화면이 사람에게 알린다. 기본값을 '오늘'로 두지 않는 이유: 기본값이 오늘이면 기존 QT·PI가 전부 `NOT_EFFECTIVE`가 되는데 사람은 그 이유를 모른다.
- 저장 = 확인 대화상자 "판을 등록하면 수정·삭제할 수 없습니다. 바꾸려면 새 판을 등록합니다." 수정·삭제 버튼 없음.
- 로고 없음(sA 부채 A-06) — 화면에 "로고는 아직 지원하지 않습니다" 한 줄.

**근거**: sA §A3, `D:323`(관리 화면 관례 — 관리자 전용 `/settings/*`), S3-2 휴일 캘린더 '근거 필수' 화면 선례.

**대안(기각)**: 적용 시작일 기본값 = 오늘 — 위 사유(조용한 실패). 기본값 = 가장 오래된 QT 발행일 — 시스템이 사람 대신 소급 결정을 한다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D9. 선적 상세 '수출 서류' 섹션 + CI·PL 작성 화면(2단)

**결정 — 섹션 위치**: 선적 상세(S3-2 D6 순서)의 **5 라인 → 6 당사자** 다음, **7 통관 기록** 앞에 **"수출 서류(CI·PL·S/I)"**와 **"채권"**(D11)을 이 순서로 넣는다(수출선적만 — 수입선적은 섹션 없음).
- 섹션 내용(X1 `export_docs` 블록):
  - 살아 있는 CI가 있으면: CI 번호 링크·발행일·개정 차수("2차 발행")·산출물 4칸 상태 요약·현재 S/I 번호 → CI 상세로.
  - 없으면 **게이트 문구**(`gate.issuable=false`일 때 고정): "CI·PL은 검수완료 이후에만 발행합니다(DESIGN §7.2). 검수 단계는 재고 기능(Phase 4)과 함께 열립니다 — 그 전에는 서류를 시스템 밖에서 작성하고 선적 내부 메모에 `CI 외부 작성 <번호> <날짜>`로 남겨 주세요." (sA §A2 공백 완화 문장 — runbook과 같은 문구, 부채 A-05 트리거에서 함께 삭제)
  - 버튼: `PREVIEW_EXPORT_DOCS`면 **[서류 미리 맞춰 보기]**(작성 화면으로 — 발행 버튼 없이 미리보기만), `ISSUE_EXPORT_DOCS`면 **[CI·PL 만들기]**. 취소된 CI가 있으면 "취소된 CI n건" 링크(CI3 `shipment_id`·`status=CANCELLED`).

**결정 — 작성 화면 `/shipments/:id/export-docs/new`**(세로 쌓기 `grid-cols-[minmax(0,1fr)]`)
1. **머리**: 선적번호·SO 번호·거래 상대(nowrap)·통화·Incoterms·결제조건 — 원천 사본이라 **입력 칸 없음**(sA §A9 ①). 보조문 "가격·조건·거래처는 선적(=수주)에서 복사됩니다 — 바꾸려면 원천을 정정해야 합니다."
2. **판매자(레터헤드)**: 오늘 판 요약 또는 적색 미등록 안내(D8 링크).
3. **수하인·통지처**: 스냅샷 표시. 주소 스냅샷이 NULL이면 **그때만** 주소 입력 칸 + "거래처 마스터에 영문 주소가 없어 이 서류에만 적습니다(마스터는 바뀌지 않습니다)."(sA §A9 ④·부채 P-44).
4. **운송**: 운송 방식(선택 6종, 기본값 없음)·선적항·양하항(필수)·최종 목적지·선명/항차·출항일(선택). 출항일 칸 옆에 **참고값** "ETD 계획 2026-11-02 / 실적 —"(미리보기 `reference_dates`) — 자동 기입 0, [참고값 넣기] 버튼도 두지 않는다(사람이 서류 날짜를 입력해야 한다 — sA §A9 ⑬).
5. **원산지**: 헤더 원산국(필수, 기본값 없음 — §15 법적 판정 L3 금지) + 라인별 개별 지정 토글.
6. **품목(라인 카드)**: 품번·수량·단가·금액(서버 문자열)·영문 품명(스냅샷 표시, 없거나 바꿀 때 입력 — "세관용 일반 품명으로 바꿀 수 있습니다")·**HS 선택**(후보 목록 select, 기본 '선택 안 함' — 후보가 1개여도 미선택이 기본, "HS는 사람이 고릅니다")·원산국(개별 지정 시)·DG 배지(S3-2 D9 배지 재사용)·참고 "단위 중량 120g · 박스당 24개(마스터 참고값 — 검증에 쓰지 않음)".
7. **포장(PL)**: 그룹 카드 반복 — 포장 종류·번호 범위(시작~끝)·1개당 순중량/총중량(kg)·치수(cm, 가로×세로×높이)·내용물 행(품목 선택 + 포장 1개당 수량). [그룹 추가]·[그룹 삭제]. 그룹별 서버 계산 결과(포장 수·총 N.W./G.W.·CBM)는 미리보기 뒤에만 채운다(프런트 계산 0).
8. **은행**: PI 스냅샷이 있으면 표시만, 없고 TT·금액>0이면 계좌 select(기본 미선택 — 같은 통화 1개여도 자동 선택 0).
9. **화인·비고**(여러 줄).
10. **검증 결과**: [미리보기] 클릭 → CI1 응답의 `validation.items`를 BLOCK(적색)·NOTICE(황색 — **[ ] 확인했습니다** 체크박스, 체크해야 발행 가능) 목록으로. 항목 클릭 → 해당 칸으로 스크롤·포커스. 첫 항목이 게이트(`SHIPMENT_NOT_INSPECTED`)면 "발행 불가(검수 전) — 입력값 점검용 미리보기입니다" 배너.
11. **동작**: [미리보기](항상) → BLOCK 0 · 미확인 NOTICE 0 · `ISSUE_EXPORT_DOCS` 있음일 때만 **[발행]** 활성 → 확인 대화상자 "발행하면 CI 번호가 붙고 내용을 고칠 수 없습니다. 고치려면 재발행(새 번호)합니다. CI·PL 파일 4개(PDF·엑셀)가 만들어집니다." → CI2 → 성공 시 CI 상세로 이동. `renditions_pending`이면 상세에서 "파일 생성 실패 — 다시 만들기".
- **입력 보존**: 작성 중 값은 `sessionStorage`(키 `export-docs-draft:{shipment_id}`)에 저장하고, 발행 성공·[작성 취소] 시 지운다. 같은 탭 새로고침·뒤로 가기 유실을 막는다(sA §A7 (b)가 화면 부록에 위임한 '브라우저 임시 저장'). 원가·비밀값이 없는 판매 서류 입력이라 저장 범위를 탭으로 좁히는 것으로 충분하다. 복원 시 "임시 저장된 입력을 불러왔습니다(서버에 저장된 것이 아닙니다)" 안내.
- **미리보기 낡음 표시**: 입력이 바뀌면 이전 검증 결과 위에 "입력이 바뀌었습니다 — 다시 미리보기 하세요" 표시, [발행] 비활성(본문 해시 비교 — 산술 아님).

**근거**: sA §A2·§A5~§A10·§A9 ⑬, `D:215`(저장 전 검증 강제), `D:329`(§15 L3 — 원산지·HS 법적 판정 자동 금지), S3-2 D7 생성 2단 선례, `fe:components/shipment-create-dialog.tsx:195-216`(2단 문구 관례).

**대안(기각)**: (a) 발행 버튼을 비활성으로 상시 노출(+툴팁) — 390px·터치에서 툴팁이 안 보이고, 고정 게이트 문구가 더 명확하다. (b) 출항일에 ETD 자동 기입 — 계획값이 서류 날짜로 조용히 들어간다. (c) 포장 합계를 프런트 계산 — 그램·mm 환산과 CBM 반올림(HALF_UP 상수)이 화면에 복제된다. (d) `localStorage` 임시 저장 — 공용 PC에서 다른 사용자에게 남는다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(화면).

---

## D10. CI 상세·재발행·취소·S/I

**결정 — CI 상세 `/commercial-invoices/:ciId`**
1. 머리: CI 번호·상태 배지(발행/취소)·개정 차수·원천 선적·SO 링크·"이전 판 CI-… / 다음 판 CI-…" 링크(`supersedes`·`superseded_by`).
2. 동작 바(`allowed_actions`): **[재발행]** → `/commercial-invoices/:id/reissue`(현재 CI 값으로 채운 작성 화면 + 사유 필수 칸, CI9 미리보기 → CI10) / **[취소]** → 사유 필수 대화상자(채권이 살아 있으면 `SUCCESSOR_ALIVE` → 채권 링크) / **[S/I 만들기]**.
3. **파일**: CI·PL × PDF·엑셀 4칸 표(D7과 같은 컴포넌트 `RenditionTable` 공용) — `pending`이면 [다시 만들기](`RENDER`).
4. 판매자·수하인·통지처·운송·원산지·은행 카드(읽기).
5. 품목 표(품번·영문 품명[+'직접 입력' 표시]·HS·원산국·수량·단가·금액·DG) — `overflow-x-auto`, 품명 칸 `min-w-[12rem]`(R-3b-4 계보 — D19).
6. 포장(PL) 표(그룹·번호 범위·포장 수·N.W./G.W.·치수·CBM·내용물) + 합계 줄.
7. 확인한 경고(`acknowledged_notices` 라벨) — "발행자가 확인한 경고: Incoterms·운송 방식 불일치 …".
8. **S/I**: 현재 S/I 카드 + 이력 Page(SI1 — '현재'/'대체됨'/'무효(CI 취소)' 배지).
9. 상태 이력(`StatusTimeline basePath=/v1/commercial-invoices/{id}`)·내부 메모·담당자(CI7).
- 취소된 CI 상세: 상단 "취소된 CI입니다 — 파일은 기록으로 남습니다. 회사 밖으로 보내지 마세요." S/I는 전부 '무효(CI 취소)'.

**결정 — S/I 만들기 대화상자**(서류량이 작아 대화상자, 390px 카드 1열)
- 수하인 방식: '거래처(CI 수하인)' 기본 선택. 'TO ORDER'·'TO ORDER OF …'는 **L/C 결제 선적에서만 선택지 노출**하되 최종 판정은 서버(422 `CONSIGNEE_MODE_NOT_ALLOWED` — L/C 부록 술어 fail-closed). 지시식이면 "통지처가 필요합니다" 안내(서버 V14).
- B/L 종류(4종 — 운송 방식이 항공이면 '항공 화물운송장'만 안내, 판정은 서버)·원본 B/L 부수(원본일 때 1~3)·특별 지시(여러 줄).
- **운임 조건은 입력이 아니라 표시**: "운임: 착불(COLLECT) — Incoterms FOB에서 정해집니다".
- DG 라인이 있으면 DG 블록 미리보기 + "위험물 신고서(DGD)는 화주가 별도 작성합니다." + `DG_MANUAL_CHECK` 확인 체크(sA §A10 V13).
- 버튼 라벨 **"S/I 만들기"**, 보조문 "이 시스템은 포워더에게 보내지 않습니다. 파일을 내려받아 직접 전달하고, 전달 사실은 선적의 '통보 기록'에 남길 수 있습니다."(§15 L3 '대외 최초 발송' 금지 — `D:329`, S3-2 D6 "보내기 단어 금지" 관례).

**근거**: sA §A8·§A11·§A13, `D:215`, `D:329`.

**대안(기각)**: (a) 재발행을 '취소' + '새로 만들기' 2단계 화면 — sA §A11 (b) 사유(살아 있는 CI 0인 창). (b) 운임 조건 선택 칸 — sA §A8 (b).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D11. 선적 상세 '채권' 섹션 + 채권 등록 대화상자

**결정 — 섹션**(수출선적만, D9 다음)
- 채권이 있으면: 채권 링크·입금 상태 배지(미입금/일부입금/입금완료)·청구액·미수·만기(값·기준 배지·D-N — D14) → 채권 상세.
- 없으면: "아직 채권이 없습니다." + 상황 문구 — PLANNED: "출고지시 후 등록할 수 있습니다(금액이 확정된 뒤)." / RELEASE_ORDERED 이후: "인보이스를 보냈다면 채권을 등록하세요. 등록 전에는 대금만기 알림이 '채권 미등록'으로 계속 나갑니다."(sB §B13 ② fail-closed를 사람에게 설명) / 취소: 섹션 숨김.
- 버튼: `CREATE_RECEIVABLE`이면 **[채권 등록]**.

**결정 — 채권 등록 대화상자**(2단: 입력 → 서버 미리보기 → 등록)
- 입력: **인보이스일**(필수, `type="date"`, `max` 속성 없음 — 미래·SO 확정 이전 판정은 서버 422) · **인보이스 번호**(필수 — "외부에서 작성한 CI 번호를 적어 주세요. 나중에 대조할 때 씁니다"). 금액은 입력 칸이 없다 — "청구액 = 선적 합계(자동)".
- [확인] → RV1 미리보기: 청구액·**선수금 충당 예정**·**등록 후 미수**·만기(값/미상 사유)·**"이 등록으로 수주 SO-…가 '완료'됩니다"**(`will_complete`) 또는 "채권이 아직 없는 선적: SH-…, SH-…"(완료 조건 안내)·L/C 상한 판정(`exceeds=true`면 적색 + [등록] 비활성).
- [등록] → RV2. 성공 시 `role="status"` "채권을 등록했습니다." + `sales_order.completed`면 "수주가 '완료'되었습니다." 선적·SO·채권 쿼리 무효화.
- 보조문(고정): "채권은 수주 노출에서 빠지고 미수로 옮겨집니다(합계는 변하지 않습니다). 등록 취소는 관리자만 할 수 있습니다."(sB §B6 ②·§B15 — 노출 공백 0을 사람 언어로).

**근거**: sB §B1·§B6·§B7·§B8·§B13, `D:225`(§7.10).

**대안(기각)**: (a) 1단(확인 대화상자만) — SO 완료처럼 되돌리기 어려운 결과(COMPLETED는 복귀 엣지 0 — sB §B7 ④)를 누르기 전에 보여 주지 못한다. (b) 인보이스일 기본값 = 오늘 — 서류 날짜를 사람이 확인하지 않고 넘기게 된다(대금만기 앵커의 원천).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D12. 채권 목록·aging·상세·입금 패널·이월 채권·채권 취소

**결정 — `/receivables` 목록 탭**
- 필터: 거래처(SearchSelect)·통화·출처(선적/이월)·상태(기본 '열림' — 취소 포함 보기 체크). 파생 필터 없음(D2-5).
- 열: 거래처(nowrap)·출처·선적/SO 번호·인보이스 번호·인보이스일·**만기**(값 + '예정' 배지 또는 미상 사유)·**D-N/경과**·통화·청구액·입금·충당·**미수**·입금 상태. 미수·D-N·날짜 가운데 정렬, 금액은 서버 문자열.
- 관리자에게 `CREATE_OPENING`이면 **[이월 채권 등록]**.
- CSV 내보내기(RV4) — 기존 CSV 관례(상태 코드값 — R-3c-3 유지, D19).

**결정 — 'aging 요약' 탭**(`?view=aging`)
- 머리: "기준일 2026-10-05(KST 오늘) — 미수가 남은 채권만, 통화별로 따로 더합니다."
- 표: 거래처 × 통화 행, 열 = 만기 전·1~30일·31~60일·61~90일·91일 이상·**만기 미상(적색, 건수 병기)**·합계·원화 환산 합계(불가면 "환산 불가 n건" — 0으로 그리지 않음). 각 금액 칸 클릭 → 목록 탭(거래처·통화 필터 적용).
- '만기 미상'을 '만기 전'에 합치지 않는다(sB §B12 ② fail-visible).
- 통화 간 합계 줄 없음(sB §B12 ④). 원화 총계는 봉투 밖 `krw_total`이 있을 때만 한 줄.

**결정 — `/receivables/:id` 상세**
1. 머리: 출처 배지(선적/이월)·거래처·선적·SO 링크(이월은 "이월(도입 전 미수)")·입금 상태 배지.
2. **미수 분해 카드**(서버 문자열): 청구액 − 입금 합 − 선수금 충당 = **미수**. 충당 설명 "같은 수주 PI 선수금 중 이 채권에 먼저 배정된 금액(오래된 채권부터)". 수주에 미충당 선수금이 남으면 "이 수주에 아직 배정되지 않은 선수금 …"(X2).
3. **만기 카드**: 값·기준(실적/예정)·D-N·사유. L/C면 "L/C 조건(유효기일·제시기간)은 수주의 L/C 조건에서 옵니다" 링크(L/C 부록 화면).
4. **입금 패널**: 기존 `PiPaymentsPanel`(`fe:components/pi-payments-panel.tsx`)을 대상 일반화 컴포넌트 `PaymentsPanel target={{kind:"pi"|"receivable", id}}`로 옮겨 재사용(입력 칸·역기록·키 Map `:88-181` 그대로). 채권 입금은 `RECORD_PAYMENT`일 때만 [입금 기록], 역기록은 행의 `reversible`.
   - **이중 입력 확인 블록**(409 `POSSIBLE_DUPLICATE`): "같은 거래처·같은 금액·입금일 ±3일 안의 입금이 이미 있습니다" + 후보 표(입금일·참조·대상 링크) + 체크 "[ ] 위 입금과 다른 입금임을 확인했습니다" → 다시 저장 시 `acknowledge_duplicates = 후보 id 전부`(부분 선택 UI 없음 — 서버가 정확히 덮기를 요구, sB §B5 ④). 체크하지 않으면 저장 비활성.
   - 통화 불일치 422 문구 + runbook 연결 "원화 환전 입금은 은행 통지서의 원통화 금액으로 기록합니다"(sB §B5 ⑥).
5. **채권 취소**(관리자 — `CANCEL`): 사유 필수. `PAYMENTS_EXIST` → "입금을 먼저 역기록해 주세요(역순)" / `SO_COMPLETED` → "완료된 수주의 채권은 취소할 수 없습니다".
6. 감사 정보: 등록자·등록 시각(KST)·취소 정보.

**결정 — 이월 채권 등록 대화상자**(관리자, sB §B14)
- 거래처·통화·금액(통화 자릿수 문자열)·환율(통화 ≠ KRW면 필수 — 표시만, 판정 서버)·인보이스일·**만기(필수)**·인보이스 번호(필수)·근거 메모(필수).
- 상단 경고(고정, 적색 테두리): "이월 미수를 **모두 등록한 뒤에** 줄여 두었던 여신 한도를 원래대로 되돌리세요. 순서를 바꾸면 그 사이 노출이 실제보다 작게 계산됩니다."(sB §B14 ③ — runbook 순서를 화면에서도 반복).

**근거**: `D:225`(§7.10 receivables·payments·aging 30/60/90), `D:317` §14 ① 미수 aging, sB §B4·§B5·§B12·§B14·§B15.

**대안(기각)**: (a) aging을 막대 그래프로 — 통화별 분리·'미상' 구분이 표보다 약하고 390px에서 수치 판독이 어렵다(대시보드 시각화는 P6). (b) 입금 패널을 채권 전용으로 새로 작성 — 같은 입력 규칙(`fe:lib/payment.ts:134-166`)·키 관리가 두 벌이 된다. (c) 이중 입력 후보 일부만 확인 — 서버 계약(정확히 덮기)과 어긋난다.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(공용 컴포넌트 일반화는 중간 — 기존 PI 패널 vitest가 회귀를 잡는다).

---

## D13. SO 상세 '완료'·잔량 종결·채권 요약 / SO 목록 / 오더 보드

**결정 — SO 상세**(`fe:routes/sales-order-detail.tsx`)
- 머리 안내문(`:208-215` 상태별 문구)에 COMPLETED 추가: "완료된 수주입니다. 모든 선적이 채권으로 넘어갔습니다(또는 남은 수량을 종결했습니다). 새 선적을 만들 수 없고 읽기 전용입니다(내부 메모·담당자만 수정 가능)." `short_close`가 있으면 배지 "완료(잔량 종결)".
- **[잔량 종결]**(`SHORT_CLOSE`): 대화상자 — 라인별 "종결될 수량"(= `shipment_open_quantity`, 서버 값 표시만)·합계 개수, **사유 필수**, 경고 "종결하면 수주가 '완료'되고 남은 수량은 더 이상 선적할 수 없습니다. 되돌릴 수 없습니다." 409 `SHIPMENT_PENDING` → D5 반응표.
- 종결 기록 표시: "잔량 종결 2026-10-12 · 무역담당 · 사유: 바이어 요청으로 잔량 취소" + 라인 표에 "종결 n개" 열(잔량>0이고 `short_close`일 때만 — sB §B7 ⑧ "잔량은 계산상 남지만 소비 경로가 닫힌다").
- **'채권' 요약 섹션**(S3-2 '선적' 섹션 `:431-525` 다음): 건수·청구 합·미수 합·미충당 선수금(X2) + 이 수주 채권 목록(RV3 `so_id=` Page — 선적번호·인보이스 번호·미수·입금 상태·만기).
- 선적 섹션 안내문(`:458-466`)에 COMPLETED 분기: "완료된 수주라 선적을 만들 수 없습니다."
- `SHIPPABLE_SO_STATUSES`(`:431`)는 그대로(CONFIRMED·IN_SHIPMENT) — COMPLETED에서 '선적 만들기'가 자동으로 사라진다. 이 상수는 S3-2 클라이언트 판정이라 부채 D-01에 함께 둔다.

**결정 — SO 목록**: `SO_STATUS_FILTERS`(`fe:routes/sales-orders.tsx:20`)에 `"COMPLETED"`를 '선적중' 다음에 추가(라벨 '완료' — `fe:lib/doc-status.ts:110`). 필터에 없으면 완료 수주가 목록 필터로 안 잡힌다(S3-2 R-21 같은 결함).

**결정 — 오더 보드: COMPLETED는 열을 만들지 않고 제외 집합에 넣는다**
- `EXCLUDED_SO_STATUSES`(`code:modules/order_board/constants.py:61`)에 `COMPLETED` 추가. 보드 범위는 "접수 이후~선적중"(`:3`)이고 완료는 취소처럼 종결 상태라 목록 화면 필터로 본다. 완결성 시험(`tests:architecture/test_order_board_contract.py:72-78` — 모든 SO 상태 = 매핑 ∪ 제외 ∪ RESERVED)이 COMPLETED의 RESERVED 이탈(sB §B7 ①) 순간 실패하므로 **sB의 상태 기계 PR과 같은 PR**에서 바꾼다(분할 부록 결속 조건).
- 선적중 열의 카드가 완료로 넘어가면 보드에서 빠진다 — 보드 상단 문구에 "완료·취소된 수주는 수주 목록에서 봅니다." 추가.

**근거**: sB §B7, `code:modules/order_board/constants.py:1-7`(카드 소실 방지 완결성), `D:317` §14 ⑥ 오더 보드.

**대안(기각)**: (a) 보드에 '완료' 6번째 열 — 종결 카드가 무한히 쌓이는 열은 파이프라인 뷰가 아니다(열당 50 + 더 보기라 성능은 되지만 신호가 묻힌다). (b) 잔량 종결 버튼을 채권 미등록 선적이 있으면 숨김 — D4 사유.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(상수 1줄·문구).

---

## D14. 대금만기·제시기한 표시 — 타임라인·aging·사유 라벨

**결정**
- **D-N 문구 개정**(`fe:lib/milestone.ts:347-359`): 대금만기 행의 `is_overdue`가 이제 서버 불리언이다(X4). `settlement.state`별:
  - `PAID` → **"입금 완료"**(D-N 대신 — 저장형 '완료'와 같은 자리), 회색.
  - `OPEN`·도과 → "n일 지남" + 적색 '도과' 배지(`fe:components/milestone-timeline.tsx:166` 재사용).
  - `NO_RECEIVABLE` → D-N 그대로 + 황색 배지 **"채권 미등록"**(선적 상세 '채권' 섹션 링크).
  - `is_overdue = null`(만기 UNKNOWN) → 기존 사유 라벨.
  - 기존 "n일 경과(도과 판정 없음)" 분기는 **이월 채권 외에는 나오지 않게** 되지만 지우지 않는다(서버가 null을 주는 경우의 안전망 — 그때도 '도과'라고 말하지 않는다).
- **사유 라벨 개정**(`fe:lib/milestone.ts:238-252` `DERIVED_REASON`): `INVOICE_NOT_ISSUED` "인보이스 미발행(인보이스 기능 이후 산정)" → **"인보이스일 미등록 — 채권을 등록하면 계산됩니다"**. `LC_TERMS_NOT_REGISTERED` "L/C 조건 미등록" → **"L/C 조건 미등록 — 수주의 L/C 조건을 등록하세요"**(L/C 부록 화면 링크). `LC_INPUT_MISSING`·`EXPIRY_MISSING`·`BL_MISSING` 그대로.
- **제시기한 행**: `presented_on`이 있으면 "제시 완료 2027-03-08"(D-N 대신), 없고 L/C 조건 등록됨이면 D-N, 미등록이면 사유 라벨(S3-2 "숨기지 않고 산정 불가" 유지).
- 대금만기 보조문 "휴일 미반영(자동 이월 없음)."(`fe:components/milestone-timeline.tsx:196-198`) 유지.
- **aging·채권 목록·채권 상세의 만기 표시는 같은 함수** `dueText(due, settlement)`(신설 `fe:lib/receivable.ts`) — 타임라인과 문구 단일 출처(라벨 표는 `milestone.ts` 것을 import — 사본 0).

**근거**: sB §B10·§B12·§B13 ⑤, S3-2 부채 Q-08·R-6-2(`P:62`·`P:232`), S3-2 화면 규칙 "is_overdue null = 도과라고 말하지 않음"(`P:380`).

**대안(기각)**: (a) 채권 미등록 선적의 대금만기를 숨김 — 등록 누락이 조용히 지나간다(sB §B13 (a) 기각 사유와 같다). (b) 사유 라벨을 채권 화면에 따로 정의 — R-6-4(라벨 이원화) 재발.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D15. 여신 카드·승인 스냅샷 — '미수 반영' 표시

**결정**
- `CreditEvaluationCard`(`fe:components/credit-evaluation-card.tsx:7,78-98`)·`ApprovalSnapshot`(`fe:components/approval-snapshot.tsx:4,40-68`)의 **'미수 미반영' 경고 조건은 그대로**(`receivables_reflected !== true`). provider 등록 후 값이 true가 되면 경고가 자연히 사라진다 — 코드 변경 0이 목표이고 PR이 실측한다(sB §B6 ⑦).
- 가산 1: `receivables_reflected === true`일 때 '미수금' 칸 옆 링크 **"미수 내역"** → `/receivables?partner_id={id}`. 노출 숫자의 근거를 사람이 바로 확인하게 한다.
- 가산 2: 조회 실패(provider 예외 → UNEVALUABLE `RECEIVABLE_PROVIDER_ERROR`·`CURRENCY_NOT_CONVERTIBLE` — sB §B6 ⑤)의 사유 라벨을 기존 게이트 사유 표에 더한다: "미수 환산 불가 — 거래처 한도 통화를 KRW로 설정하거나 환율을 확인하세요" / "미수 조회 실패 — 잠시 후 다시 평가하세요".
- **이월 미수 안내**(P-51): 한도가 있는 거래처인데 OPENING 채권이 0건이면 카드에 회색 한 줄 "시스템 도입 전 미수가 있다면 '이월 채권 등록'으로 넣어야 노출에 반영됩니다." — 판정 변경 없음(표시만). 근거: provider가 reflected=true가 되면 '미수 미반영' 배지가 사라져 이월 누락이 **안 보이게** 된다(sB §B14 대안 기각 사유 — 가시성 후퇴 방지). 이 줄은 서버 필드 `opening_receivables_count`(평가 응답 가산 1필드)로 그린다.

**근거**: sB §B6·§B14, S3-1 부기 `D:227` ④("'미수 미반영' 배지로 드러낸다").

**대안(기각)**: 이월 안내 없음 — 위 사유(배지 소멸 = 이월 누락 비가시화).

**자율 확정**: 확정. **되돌리기 비용**: 낮음(평가 응답 1필드·문구).

---

## D16. L/C 플래그 토글 화면 + L/C 화면 경계

**결정**
- '정책 설정'(`/settings/policies`) 화면 아래에 섹션 **"기능 켜기·끄기"**를 둔다(FF1 목록 — 레지스트리 `lc` 1행). 열: 기능 이름("L/C 결제")·상태("켜짐"/"꺼짐"/"꺼짐(미등록)")·최근 변경자·변경 시각. 버튼 **[켜기]/[끄기]**(확인 대화상자).
- 끄기 확인 문구(고정): "끄면 **새 L/C 입력**(결제유형 L/C 선택·L/C 조건 등록)이 막힙니다. 이미 등록된 L/C 건의 만기·제시기한 계산과 알림은 계속됩니다." — sB §B10 ⑧(플래그 = 입력 진입만, fail-open 방지)을 사람에게 그대로 말한다.
- 켜기 확인 문구: "켜면 견적·PI·수주에서 결제유형 'L/C'를 고를 수 있고 수주에 L/C 조건을 등록할 수 있습니다."
- **L/C 화면 본체(L/C 부록 소관 — 자리만)**: SO 상세 'L/C 조건' 섹션(결제유형 L/C일 때 — 등록·개정·tolerance 상·하한 표시·유효/선적기일 임박 적색), 선적 상세 '제시 기록' 섹션(제시·네고·인수일), 하자 체크리스트 화면(라우트 슬롯 `/sales-orders/:id/lc-checklist` 제안). 응답에 `allowed_actions`(D4 규칙)를 따르도록 요구한다.

**근거**: sB §B11, P-10(`P:1624`), `D:225`(§7.10 L/C), `D:450` §20 H("기능 플래그 오프 완전 비활성" — sB §10 ③ 해석 부기).

**대안(기각)**: (a) 별도 메뉴 '기능 설정' — 플래그 1개에 메뉴 1개는 과하다(레지스트리가 늘면 재판정). (b) CLI 전용 — 비개발자 운영(CLAUDE.md "클릭 단위 안내").

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D17. 문서보관소·알림 이동 매핑·라벨 대사(R-6-4)

**결정 — 문서보관소**(`/documents`)
- `DOCUMENT_OWNER_TYPE`(`fe:lib/labels.ts:124-129`)에 6종 라벨: 견적·PI·수주·선적·상업송장(CI)·선적요청서(S/I)(sA §A13 owner_type 확폭). 문서 종류 5행(견적서·PI·상업송장·포장명세서·선적요청서)은 서버 `name_ko`를 그대로 쓴다.
- `is_generated` 행: 배지 "시스템 생성"·원천 링크(`generated_from`), **삭제 버튼 숨김**(X6).
- 전표 첨부 업로드(사람 업로드 — B/L 사본 등)는 선적·CI 상세의 '첨부' 섹션에서 기존 문서 업로드 대화상자를 `owner_type`·`owner_id` 고정으로 연다. 수입선적 상세에는 첨부 섹션 없음(sA §A13 — 원가 채널 0).

**결정 — 알림 이동 표**(`fe:lib/alert-routes.ts:6-17`·`:27-36`)
- `ALERT_ROUTES`에 `receivables: (id) => /receivables/${id}`(이월 채권 만기 알림 — sB §B13 ⑦), `commercial_invoices: (id) => /commercial-invoices/${id}`(CI 이벤트 알림 규칙이 생길 때 대비 — 표에 있어도 해롭지 않고 없으면 404 링크 0 원칙만 지키면 된다).
- `ALERT_TARGET_LABEL`에 `receivables: "채권"`, `commercial_invoices: "상업송장"`.
- 출구 계약 시험(`tests:e2e/test_s3_2_walkthrough.py:417-419` 선례): 스캔이 내는 entity_type ⊆ 이동 표 — sB §B13이 `receivables`를 더하면 같은 시험이 자동으로 잡는다.

**결정 — 라벨 대사 시험(R-6-4 해소)**
- R-6-4 트리거 "라벨 변경 시"가 S3-3에서 발동한다(알림 종류 PAYMENT_DUE·PRESENTATION_DEADLINE·LC_EXPIRY·LC_LATEST_SHIPMENT 신설 — sB §B13 ⑥). 해소 방식: **pytest가 프런트 TS 라벨 표의 키 집합을 읽어 백엔드 열거와 대사**한다(위 출구 계약과 같은 수법 — 의존성 추가 0). 대상 4표: ① 알림 종류(백엔드 알림 사전 ↔ 프런트 알림 종류 라벨) ② 서류 검증 항목 코드(sA §A17 StrEnum ↔ 신설 `fe:lib/export-docs.ts` `VALIDATION_ITEM_LABEL`) ③ 파생 사유(`DueReason` ↔ `DERIVED_REASON`) ④ 채권 입금 상태·aging 구간·출처. 키가 한쪽에만 있으면 실패. 모르는 값은 화면에서 "기타"(`D:321`) — 대사 시험이 그 경로를 운영에서 쓰지 않게 한다.

**근거**: `D:321`(entity_type→라우트 표·모르는 값 '기타'), S3-2 부채 R-6-4(`P:149`), sA §A17(화면 부록에 라벨 위임·대사 시험 요구).

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D18. 한국어 UI·390px·접근성 — S3-3 적용표

**결정 — 한국어 UI**(`fe:styles/index.css:5-31`, CLAUDE.md 기술 규칙)
- 전역 `break-keep` 유지. 본문·사유·메모·보조문·검증 메시지는 break-keep.
- **nowrap 대상(S3-3 신규)**: CI·S/I·채권 번호, 선적·SO 번호, 상태·입금 상태·aging 구간 배지, 통화 코드, 금액, 날짜, D-N, **국가명(원산국·출발·도착)**, **Incoterms 표기**(예 `FOB Busan 2020` — 협정명과 같은 '나눠지면 뜻이 바뀌는 고유 표기'), HS 코드, UN 번호, 포장 번호 범위(`1–20`), 거래처명 셀(표 안 — 상세 카드에서는 break-keep).
- **가운데 정렬(`.num`)**: 수량·포장 수·중량·CBM·금액·미수·D-N·일수·건수·환율·날짜. 금액 우측 정렬 금지(ADR-0009 — `fe:styles/index.css:25-30`).
- 라벨은 단일 표만(D17 대사), 코드 원문 노출 0. 영문은 **서류 내용 값**(품명·주소·항구)만 — 그 칸은 `lang="en"` 속성(스크린리더 발음).

**결정 — 390px**
- 상세 4종(채권·CI·S/I·레터헤드 설정 제외 — 설정은 목록 화면 관례)은 `grid grid-cols-[minmax(0,1fr)] gap-6`(detail-layout 시험 자동 적용 — D6).
- 넓은 표(채권 목록·aging·CI 품목·PL 포장·입금 원장·S/I 이력)는 섹션 안 `overflow-x-auto` 상자에서만 가로로 민다. 내용이 긴 열(거래처명·품명·사유)은 `min-w-[12rem]`~`[14rem]`(S3-2 PR-8 알림센터 결함 ② 계보 — `scrollWidth`만으로는 못 잡는 '한 글자씩 꺾임' 방지).
- **CI 작성 화면**: 라인·포장 그룹은 표가 아니라 **카드 1열**(390px), 640px 이상에서 2열 그리드. 포장 치수 3칸은 390px에서 한 줄 3칸(`grid-cols-3`, 칸 최소 폭 5rem)·라벨은 위.
- aging 표는 390px에서 첫 열(거래처·통화) `sticky left-0` + 배경색(가로 스크롤 시 행 식별).
- 대화상자: `max-w-[calc(100vw-2rem)]`(S3-2 관례), 채권 등록 2단·S/I·이월 채권·잔량 종결·이중 입력 확인 전부.
- **증거**: jsdom은 레이아웃을 못 잰다 → 소스 계약 vitest(위 클래스·sticky·카드 1열) + 워크스루 실브라우저 `scrollWidth == 390` 실측 + 스크린샷 육안(PR-8 결함 ② 교훈)을 PROGRESS에 기록(ADR-0086 3층 증거).

**결정 — 접근성**
- 대화상자는 `ConfirmDialog`/`useDialogBehavior`만. 검증 항목 목록 `role="alert"`(발행 실패) / 미리보기 결과 `aria-live="polite"`. 칸별 오류 `aria-describedby`, NOTICE 확인 체크박스 `aria-required`.
- 배지는 텍스트 필수("도과", "채권 미등록", "만기 미상", "시스템 생성").
- 표 `<th scope="col">`, aging 행 머리 `<th scope="row">`(거래처·통화).
- 다운로드 버튼 라벨에 파일 종류 포함("CI PDF 내려받기") — 같은 글자 버튼 4개가 스크린리더에서 구분되게.

**근거**: `D:317` §14 끝 문장(한국어 UI), CLAUDE.md "한국어 UI: break-keep 기본, 좁은 셀·헤더·협정명·국가명 nowrap, 숫자·기준값 가운데 정렬", `D:476`(렌즈 6)·`D:481`(렌즈 11), S3-2 부록 D D13.

**자율 확정**: 확정. **되돌리기 비용**: 낮음.

---

## D19. S3-2(·S3-1) 이월 화면 부채 처리 — 트리거 판정

| ID | 내용 | 트리거 원문 | S3-3 판정 | 처리 |
|---|---|---|---|---|
| PR-16 ⑧ | PI '입금완료'는 선수금 기준 | S3-3 | **발동 → 해소** | `PROFORMA_STATUS`(`fe:lib/doc-status.ts:48-51`) 라벨을 PI 문맥에서 '선수금 일부입금'·**'선수금 입금완료'**로 바꾼다. 채권의 '입금완료'(D12)와 같은 글자가 다른 뜻이 되는 혼동을 막는다(자율 확정 — 더 명확한 쪽). 코드값 PAID는 무변경 |
| R-6-4 | 알림 종류명 백엔드 ↔ 프런트 별도 출처 | 라벨 변경 시 | **발동(알림 종류 신설) → 해소** | D17 대사 시험 4표 |
| R-8-2 | 셸 머리 역할 코드 원문(`물류담당 (LOGISTICS)`) | 화면 정리 세션 또는 사용자 보고 | **해소(같은 파일을 만지는 PR에서 — 자율 확정)** | `fe:routes/shell.tsx:113-115`에 `roleLabel`(`fe:lib/users.ts:26-37`) 적용 — 메뉴 1줄 추가와 같은 PR. 한국어 UI 규칙 위반을 알면서 남기지 않는다 |
| R-3b-9·R-4b-4 | 결과 미확인 '확인 없이 닫기' 시 멱등 키 상실 | 중복 선적·통관 1건 | **미발동 — 기존 대화상자는 이월**, 신규 금액 대화상자는 D20 규칙으로 처음부터 회피 | D20 |
| R-3b-4 | 다른 화면 품명 칸 최소 폭 8곳 | 해당 화면 수정 | **부분 발동** — S3-3이 수정하는 QT·PI·SO 상세의 품명 칸만 `min-w` 적용(파일을 만지므로), 나머지 이월 | 해당 PR |
| R-3c-3 | CSV 상태·구분 = 코드값 | 사용자 요청 1건 | **미발동 — 이월**. 신규 CSV(채권·CI)도 같은 규칙(코드값)으로 맞춘다(규칙이 둘이 되지 않게 — 바꿀 때 전 CSV를 한 번에) | — |
| R-8-1 | 알림센터 대상에 문서 번호 없음 | 대상 혼동 1건 | **미발동 — 이월**. 신규 알림 제목에 SH·인보이스 번호를 넣도록 sB에 요구(제목 = 백엔드) | sB §B13 |
| R-3b-10 | ConfirmDialog pending 중 Esc 무시 | 처리 중 닫기 요구 | 미발동 — 이월(신규 대화상자도 같은 공용 동작) | — |
| PR-16 ④ | 게이트 표 안내 nowrap | 사용자 마찰 보고 | 미발동 — 이월(무접촉) | — |
| R-4b-1·2·3, R-5b-1·4 | 마일스톤·수입선적 화면 | 원문 | 무접촉 — 이월 | — |
| Q-04·Q-05·Q-06·Q-08·R-3a-5·P-01·P-10·P-11 | S3-3 소유분(데이터·산식) | — | 각 부록 해소 계획 — **이 부록은 화면 축만**: Q-04 → D13 / Q-05 → D9 ⑦ / Q-06 → D10 S/I 수하인 방식 / Q-08 → D14 / R-3a-5·P-11 → D8 / P-01 → D11 보조문·D15 / P-10 → D16 | — |
| P-13 | documents 전표 첨부 | S3-3·S6-1 | sA 해소 계획 — 화면 축 D17 | — |
| P-12·P-14·P-15 | 만료 PI 입금·이중 입력·선수금 노출 | S3-3 | sB 재판정 — 화면 축: P-14 → D12 이중 입력 확인 블록, P-12 → 기존 409 문구에 "선수금이 만료 PI로 오면 새 PI를 발행해 기록합니다" 추가 | — |
| P-51 | 이월 미수 | S3-3 | sB §B14 — 화면 축 D12 이월 등록·D15 안내 | — |

**근거**: PROGRESS 부채 최종 목록(`P:56-165`)·S3-1 부채(`P:1088` ⑧).

**자율 확정**: 확정(R-8-2·R-3b-4 부분 해소는 '같은 파일을 만지는 PR에서 함께'라는 저비용 근거로 확정). **되돌리기 비용**: 낮음.

---

## D20. 금액·발행 대화상자의 멱등 키 보존 — R-3b-9 구조를 신규 화면에서 반복하지 않는다

**결정**
- S3-3 신규 쓰기 대화상자(채권 등록·채권 입금·이월 채권·잔량 종결·CI 발행·재발행·S/I·렌더)는 멱등 키를 **대화상자 상태가 아니라 화면(페이지) 수준 Map**에 둔다: 키 = `(엔드포인트, 정규화 본문 JSON)` → `Idempotency-Key`. `PiPaymentsPanel`의 `keyFor(map, body)`(`fe:components/pi-payments-panel.tsx:88-181`)·확정 키 규약(`fe:lib/confirm.ts:4`)을 공용 훅 `useIdempotencyKeys()`로 올려 쓴다.
  - 결과 미확인(네트워크 끊김·타임아웃)으로 대화상자를 닫았다가 **같은 본문으로 다시 제출하면 같은 키** → 서버가 첫 응답을 재생(중복 0). 본문이 바뀌면 새 키. 성공 응답을 받으면 그 키를 지운다.
  - '결과 미확인' 상태에서 닫기 버튼 문구는 "닫기(같은 내용으로 다시 저장하면 중복되지 않습니다)" — '확인 없이 닫기'를 쓰지 않는다.
- 범위 밖: 페이지 새로고침 뒤에는 Map이 사라진다 — 그 경우의 중복은 서버 측 방어(채권 = 선적당 살아 있는 1 부분 유니크, CI = 선적당 1, 입금 = 이중 입력 의심 409 — sB §B5 ④)가 막는다. 기존 S3-2 대화상자(선적 생성·통관)는 트리거 미발동으로 이월(D19).

**근거**: `D:370`(§17.4 클라이언트 idempotency key — 더블클릭·재시도), S3-2 부채 R-3b-9·R-4b-4(`P:108`·`P:130`), 금액 기록은 중복 시 비용이 크다(입금 2배 기록 = 미수 과소 = 노출 과소).

**대안(기각)**: (a) 키를 `sessionStorage`에 — 새로고침 후 재사용은 '다른 의도의 같은 본문'(진짜 두 번째 같은 금액 입금)을 막을 수 있어 사람 의도를 시스템이 덮는다. 서버 이중 입력 확인(사람 확인 경로)이 그 경우를 맡는다. (b) 기존 대화상자까지 이번에 일괄 교체 — 트리거 미발동, 회귀 표면.

**자율 확정**: 확정. **되돌리기 비용**: 낮음(훅 1개).

---

## 테스트 배분 (§20 그룹 — API·화면 몫. 데이터·산식 몫은 sA §A21·sB §B21)

| 그룹 | 케이스 |
|---|---|
| A | (API) CI 발행 응답 = 상세 형태·`allowed_actions` / 재발행 응답 `supersedes`·이전 CI 취소 / 채권 등록 미리보기 `will_complete` true·false / 잔량 종결 409 `SHIPMENT_PENDING`의 `detail.shipments` / SO 상세 `short_close`·`receivables_summary` |
| B | (API) 검증 422 `detail.items[].path`가 본문 경로와 1:1(화면 칸 연결 근거) / 중량 문자열 `"12.3456"` 422·`"0"` 422·`"12.345"` → 12345g / CI·PL·S/I `language=KO` 422 `LANGUAGE_NOT_SUPPORTED` / 렌더 파일명 ASCII·다운로드 attachment / CSV(채권·CI) UTF-8 BOM·수식 이스케이프 |
| H | 플래그 FF1 미등록 행 표시·FF2 첫 PUT INSERT·레지스트리 밖 404 / 대금만기 보드 `settlement` 3상태·`is_overdue` 불리언 |
| J | 같은 키 재생: 채권 등록·입금·잔량 종결·렌더·CI 발행 각각 응답 동일·행 1 / version 409(CI 취소·채권 취소·잔량 종결) / 미리보기 3종 호출 전후 채번·이벤트·멱등 행 수 불변 |
| K | authz 행 전부(신규 접두어 5·신규 경로 — C·V 쓰기 403, 레터헤드·이월 채권·채권 취소·플래그 = A만, 렌더 QT·PI L 403) / `GOVERNED_PREFIXES` 등재 / Page 봉투 자동 스캔(LH1·CI3·CI6·SI1·RV3·RV5·PY1·FF1) / 상세 쿼리 수 상한(CI 8·채권 목록 고정·aging 상수·문서 흐름 6) / 부모-자식 404(다른 CI의 S/I id·다른 채권의 입금 역기록) / **`allowed_actions` ↔ authz 매트릭스 대사**(D4) / **라벨 대사 4표**(D17) / 이동 표 출구 계약(receivables) / RV3 파생 필터 파라미터 부재(스키마 스캔) / aging `as_of` 파라미터 부재 |
| vitest | 서류 파일 2×2 표(만들기·내려받기·DRAFT 안내·취소 상태 경고) / 작성 화면: 게이트 배너·NOTICE 체크 전 발행 비활성·입력 변경 시 미리보기 낡음·sessionStorage 복원 안내·HS 기본 미선택·출항일 자동 기입 0 / CI 상세 재발행 링크·S/I 운임 표시(입력 칸 0) / 채권 등록 2단(미리보기 전 등록 버튼 0·`exceeds` 비활성) / 채권 목록 파생 필터 UI 0·aging '만기 미상' 분리·환산 불가 문구 / 이중 입력 확인 체크 전 저장 비활성·`acknowledge_duplicates` = 후보 전부 / 잔량 종결 사유 필수·409 링크 / SO 목록 '완료' 필터 / 대금만기 D-N 3상태·사유 라벨 개정 / PI 라벨 '선수금 입금완료' / 셸 '채권'·'자사 정보'(ADMIN만)·역할 한국어 표기 / alert-routes `receivables`·`commercial_invoices` / 소스 계약: 신규 화면 `hasRole(`·상태 비교 버튼 0, `new Date(` 날짜 문자열 0, detail-layout 하한 10, `min-w`·sticky·카드 1열 클래스 / 멱등 키 Map: 같은 본문 재제출 = 같은 키·본문 변경 = 새 키 |
| 워크스루(렌즈 11) | 실브라우저 1회 관통(리포 밖, ADR-0086 3층): 관리자 자사 정보 판 등록(과거 적용일) → 무역 QT 발행 → QT PDF 만들기·내려받기 → PI 엑셀 KO → SO 확정 → 선적 2건 출고지시 → '서류 미리 맞춰 보기'(게이트 배너·검증 항목·NOTICE) → 채권 등록 2단(1건) → 대금만기 '입금 완료/채권 미등록' 배지 → 입금(이중 입력 확인 1회) → 잔량 종결 또는 2번 채권 → SO '완료'·보드에서 빠짐 → aging 탭 → 여신 카드 '미수 내역' 링크 → 알림 대상 클릭 이동 → **390px 9화면 `scrollWidth`·스크린샷 육안**(채권 목록·aging·채권 상세·작성 화면·CI 상세[팩토리 상태]·SO 상세·자사 정보·서류 파일 패널·이중 입력 블록) |

---

## DESIGN·ADR 부기 대상(이 부록 몫)

- **§14 [M4] 보강(S3-3 화면 배정)**: 채권 목록·aging 요약 탭·채권 상세(입금 패널 공용화·이중 입력 확인)·이월 채권(관리자)·선적 상세 '수출 서류'·'채권' 섹션·CI·PL 작성 전용 화면(원천 선적 경로)·CI 상세·S/I·QT/PI '서류 파일' 패널·자사 정보(관리자)·정책 설정 '기능 켜기·끄기'. **CI 목록·메뉴는 S4-2**(발행 0 기간의 빈 메뉴 금지). 오더 보드는 COMPLETED 제외.
- **§18.4 부기**: 파생값 필터 금지(목록 필터는 저장 열만 — 페이지 정합)·aging 기준일 서버 고정.
- **§18.1 부기**: `allowed_actions` = S3-3 신규 동작의 화면 근거, 액션↔매트릭스 대사 시험.
- **ADR 후보 1건**(번호는 분할 부록): "S3-3 화면 계약 — 서버 `allowed_actions`(부분 도입)·서류 파일 단일 다운로드 통로·CI 작성 전용 화면·멱등 키 화면 수준 보존". 부기: ADR-0086(3층 증거 — S3-3 적용), ADR-0066(보드 제외 집합에 COMPLETED).

---

## 10. 멈춰서 보고할 항목 (판정 후보 → 자율 확정, 더 엄격)

1. **채권 발생 경로 ↔ CI 운영 경로 닫힘(sB §B1 ③ ↔ sA §A2)** — sB는 "CI 발행 전표가 생기면 B 엔드포인트를 닫는다", sA는 "S4-2 전 운영 CI 발행 0"이다. 둘을 문면대로 합치면 **S3-3 운영에서 채권이 0건**(aging·COMPLETED·대금만기 충족 신호 전부 사망, sB §B13 ②로 '채권 미등록' 알림만 쌓임). **화면 측 자율 확정**: 버튼은 서버 `CREATE_RECEIVABLE`만 따르므로 어느 결론이든 화면 변경 0. **통합에 내는 권장안(더 엄격)**: ① S3-3은 RV2 엔드포인트를 연다(인보이스 번호 **필수** — 외부 작성 CI 번호로 청구 기록과 서류를 잇는다, runbook A-05 문구와 연결) ② S4-2에서 CI가 열리면 **CI가 살아 있는 선적의 RV2 본문은 `invoice_on`·`invoice_ref`를 받지 않고 서버가 CI에서 복사**(보내면 422) — 청구 기록과 서류의 불일치 경로 0을 '경로 폐쇄'가 아니라 '값 원천 고정'으로 달성. ③ 그때 CI 발행 TX가 채권을 자동으로 만들지 않는다(아래 3).
2. **SHIPMENT 채권의 `invoice_ref` NULL 허용(sB §B2)** — **자율 확정: 필수(422)**. 근거: 위 1 ①, 그리고 S/I·B/L 대조(S5-3)·입금 매칭(P7)에서 외부 인보이스 번호가 유일한 연결 키다. 되돌리기 비용 낮음(스키마 1필드).
3. **CI 발행 역할(sA §A16: A·T·L) ↔ 채권 발생 역할(sB §B18: A·T)** — sB §B1 ③처럼 CI 발행 TX가 채권을 만들면 물류가 청구 기록(상업 사실)을 만든다. **자율 확정(권장): CI 발행과 채권 등록을 분리**(CI는 A·T·L, 채권은 A·T, 채권 값은 위 1 ②로 CI에서 복사). 통합이 sB ③을 유지하면 CI 발행 역할을 A·T로 좁혀야 한다(더 엄격한 쪽은 둘 다 만족 — 화면은 `allowed_actions`라 영향 0).
4. **대금만기 `INVOICE_DATE` 앵커 원천 이원화(sA §0 "살아 있는 CI `doc_date`" ↔ sB §B10 ② "채권 `invoice_on`")** — **자율 확정(권장): sB(채권 `invoice_on`) 단일 원천** + 불변식 "CI가 있으면 `invoice_on` = CI `doc_date`"(위 1 ②가 보증). 근거: S3-3 운영에서 CI는 0건이라 sA안이면 INVOICE_DATE 결제조건의 만기가 S4-2까지 전부 UNKNOWN이다. 화면 사유 라벨(D14 "인보이스일 미등록 — 채권을 등록하면 계산됩니다")이 이 결정에 묶인다.
5. **오더 보드 COMPLETED** — 완결성 시험이 COMPLETED의 RESERVED 이탈과 동시에 실패한다. **자율 확정: 제외 집합(열 없음), sB 상태 기계 PR과 같은 PR**(D13).
6. **PI '입금완료' 라벨** — 채권 '입금완료'와 충돌. **자율 확정: PI 문맥 '선수금 입금완료'로 개명**(D19).
7. **부록 문자 불일치** — sA「화면 부록」, sB "화면 부록(E)·인가·잠금 부록(D)", 오케스트레이터 배정 "부록 D = API·화면". 통합 문서가 문자를 확정하고 sB 본문의 "D"(인가·잠금)를 안전 부록 참조로 읽는다.

---

## 부채 등재 후보(이 부록)

| ID | 내용 | 소유 | 트리거 |
|---|---|---|---|
| D-01 | QT·PI·SO 상세의 기존 동작은 클라이언트 상태 판정(`canEditSalesOrder`·`SHIPPABLE_SO_STATUSES` 등) — `allowed_actions`는 S3-3 신규분만 | 프런트·각 전표 | 상태 규칙 불일치 버그 1건 또는 S4-2 SO 할당 엣지 개방(규칙이 늘 때) |
| D-02 | CI 목록 화면·메뉴 없음(API만) | S4-2 | INSPECTED 엣지 개방 PR(운영 CI 발행 시작) |
| D-03 | 채권 목록 파생 필터(입금 상태·구간) 없음 — aging 탭으로 대체 | 채권 | 사용자 요청 1건(SQL 단일 정의 설계 필요) |
| D-04 | aging 과거 기준일 조회 없음 | 채권·회계 | 월마감 보고 요구(P6 — 입금 원장 기준일 절단 정의 후) |
| D-05 | 서류 PDF 인라인 미리보기 없음(내려받아 열기) | 서류 | 사용자 마찰 보고 |
| D-06 | CI 작성 입력은 탭 단위 임시 저장뿐(서버 초안 없음 — sA §A7 (b)) | 서류 | 작성 중 유실 보고 1건 |
| D-07 | 기존 S3-2 대화상자(선적 생성·통관)는 키 보존 미적용(R-3b-9·R-4b-4 원문 유지) | 프런트 공용 | 원문 그대로 |
| D-08 | 이중 입력 확인은 후보 '전부' 확인만(부분 선택 없음) | 입금 | 후보 3건↑ 혼선 보고 |
| D-09 | 기능 플래그 화면은 레지스트리 1항목 전제(정책 설정 섹션) | 플랫폼 | 플래그 3개↑ |

**실행 검증 못 했음.** 이 부록은 정적 독해로 작성했다(pytest·vitest·서버·브라우저 미실행). 엔드포인트·응답 필드·쿼리 수 상한·화면 문구는 구현 PR의 첫 커밋에서 스키마·시험으로 실측 고정한다.
