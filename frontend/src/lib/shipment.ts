// 선적(수출·수입) 응답 타입·라벨·키 — backend/app/modules/shipments/schemas.py·trade_chain/shipment_view.py 그대로 (S3-2 PR-3b·PR-5b).
//
// ★ (PR-5b) 상세·목록 응답은 `shipment_kind` 판별자 합집합이다 — 수입 변형엔 통화·환율·합계·라인 단가/금액/무상 키가 **아예 없다**
//   (원가 비복사 — 원가 열람 역할로도 0). 화면은 `isExportShipment`로 좁힌 뒤에만 금액 칸을 그린다(R-5a-4 해소).
//
// ★ 버튼 노출은 서버가 준 `allowed_actions`만 따른다(PROGRESS 'S3-2 PR-3a' 인계 계약) — 화면이 역할·상태로 다시 판정하지 않는다.
// ★ 금액은 서버 문자열(*_text) 그대로, 날짜(`doc_date`·`fx_rate_date`)는 'YYYY-MM-DD' 문자열 그대로(시각 객체로 바꾸지 않는다 —
//   UTC 자정 해석으로 하루 밀림). 시각(`created_at`·`updated_at`·`frozen_at`·`occurred_at`)만 `toKstDisplay`로 KST 표시.
// ★ 마일스톤 보드·통관 요약(PR-4a 응답 — 상세 `milestones`·`customs_summary`, 목록 `etd`·`eta`)의 타입·라벨은 lib/milestone.ts(PR-4b).

import type { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api";
import { shipmentStatusLabel } from "./doc-status";
import { ORDER_BOARD_QUERY_KEY } from "./order-board";
import { purchaseOrderDetailKey } from "./purchase-order";
import { QUANTITY_EXCEEDS_OPEN_CODE, type Incoterm, type PaymentTerms } from "./proforma";
import { DOCUMENT_FLOW_QUERY_KEY, salesOrderDetailKey } from "./sales-order";
import type { CustomsSummary, EffectiveValue, MilestoneBoard } from "./milestone";

export interface ShipmentSource {
  kind: "SALES_ORDER" | "PURCHASE_ORDER";
  id: number;
  doc_number: string;
  status: string;
}

export interface ShipmentAssignee {
  id: number;
  display_name: string | null;
}

/** 목록 행 공통 — **금액·통화 키가 없다**(수출 변형에만 — S3-2 PR-5a 판별자 합집합, R-5a-4). */
interface ShipmentListItemBase {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  source: ShipmentSource;
  counterparty_name: string;
  origin_country_code: string;
  dest_country_code: string;
  line_count: number;
  /** ETD·ETA 유효값(실적 우선 — 'YYYY-MM-DD' 현지 날짜, 행·값 없으면 null) — 목록 열(부채 R-3b-3, PR-4a ⑨). */
  etd: EffectiveValue | null;
  eta: EffectiveValue | null;
  assignee: ShipmentAssignee;
  created_at: string;
  updated_at: string;
}

export interface ExportShipmentListItem extends ShipmentListItemBase {
  shipment_kind: "EXPORT";
  currency: string;
  total_amount: number;
  total_text: string;
}

/** 수입선적 목록 행 — 통화·합계 키 자체가 없다(원가 비복사 — 원가 열람 역할로도 0, GC-G3). */
export interface ImportShipmentListItem extends ShipmentListItemBase {
  shipment_kind: "IMPORT";
}

/** 목록 행 — `shipment_kind` 판별자 합집합(서버 `ExportShipmentListItem | ImportShipmentListItem`). */
export type ShipmentListItem = ExportShipmentListItem | ImportShipmentListItem;

export interface ShipmentSku {
  id: number;
  code: string;
  name_ko: string;
  name_en: string | null;
  kind: string;
}

export interface ShipmentDg {
  flag: boolean;
  un_number: string | null;
  dg_class: string | null;
}

interface ShipmentLineBase {
  id: number;
  line_no: number;
  sku: ShipmentSku;
  quantity: number;
  /**
   * 원천 라인 대비 — `remaining_after`는 이 선적까지 반영한 서버 파생값(화면 산술 0). 수출 = SO 라인 수량·선적 잔량,
   * 수입 = PO 라인 수량·**배정 가능량**(PO 잔량 아님 — PR-5a).
   */
  source_line: { id: number | null; line_no: number; quantity: number; remaining_after: number };
  dg: ShipmentDg;
  /** §8.3 '자리' — 항상 NOT_IMPLEMENTED('가용재고 미산정' 배지, 숫자·0 표시 금지). */
  availability: { status: string };
}

export interface ExportShipmentLine extends ShipmentLineBase {
  so_line_id: number | null;
  currency: string;
  unit_price_amount: number | null;
  unit_price_text: string | null;
  is_free: boolean;
  line_amount: number;
  line_amount_text: string;
}

/** 수입선적 라인 — 단가·금액·통화·무상 키 없음(원가 비복사). 원천 = PO 라인. */
export interface ImportShipmentLine extends ShipmentLineBase {
  po_line_id: number;
}

export type ShipmentLine = ExportShipmentLine | ImportShipmentLine;

export interface ShipmentParty {
  id: number;
  role: string;
  partner_id: number;
  name_en: string;
  address_en: string | null;
  /** 원천 자동 스냅샷 행(수출 수하인) — 제외 버튼을 두지 않는다. */
  auto: boolean;
  version: number;
}

/** 상세 공통 — **금액·통화 계열 필드가 없다**(서버 `_ShipmentDetailCommon`). */
interface ShipmentDetailBase {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  version: number;
  frozen_at: string | null;
  source: ShipmentSource;
  counterparty: { partner_id: number; name: string };
  origin_country_code: string;
  dest_country_code: string;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  internal_note: string | null;
  assignee: ShipmentAssignee;
  last_line_no: number;
  dg_line_count: number;
  parties: ShipmentParty[];
  /** 마일스톤 보드(PR-4a — `GET /shipments/{id}/milestones`와 같은 모양). 쓰기 응답의 `board`로 이 칸만 바꾼다. */
  milestones: MilestoneBoard;
  customs_summary: CustomsSummary;
  allowed_actions: string[];
  created_at: string;
  updated_at: string;
}

/** 수출선적 상세 — 통화·고정 환율(원천 SO 사본)·판매가 합계. */
export interface ExportShipmentDetail extends ShipmentDetailBase {
  shipment_kind: "EXPORT";
  currency: string;
  minor_units: number;
  fx_rate: string | null;
  fx_rate_date: string | null;
  total_amount: number;
  total_text: string;
  lines: ExportShipmentLine[];
}

/** 수입선적 상세 — 통화·소수 자릿수·환율·합계 키 자체가 없다(null 아닌 미포함 — PR-5a, R-5a-4 해소). */
export interface ImportShipmentDetail extends ShipmentDetailBase {
  shipment_kind: "IMPORT";
  lines: ImportShipmentLine[];
}

/** 상세 — `shipment_kind` 판별자 합집합(서버 `ExportShipmentDetail | ImportShipmentDetail`). */
export type ShipmentDetail = ExportShipmentDetail | ImportShipmentDetail;

export interface ShipmentPreviewLine {
  so_line_id: number;
  line_no: number;
  sku: ShipmentSku;
  quantity: number;
  open_quantity_before: number;
  remaining_after: number;
  unit_price_amount: number;
  unit_price_text: string;
  is_free: boolean;
  line_amount: number;
  line_amount_text: string;
  dg: ShipmentDg;
}

export interface ShipmentPreviewParty {
  role: string;
  partner_id: number;
  name_en: string;
  address_en: string | null;
  auto: boolean;
}

export interface ShipmentPreview {
  so_id: number;
  so_doc_number: string;
  so_status: string;
  doc_date: string;
  shipment_kind: string;
  counterparty: { partner_id: number; name: string };
  origin_country_code: string;
  dest_country_code: string;
  currency: string;
  minor_units: number;
  fx_rate: string | null;
  fx_rate_date: string | null;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  total_amount: number;
  total_text: string;
  lines: ShipmentPreviewLine[];
  parties: ShipmentPreviewParty[];
}

/** 수입선적 미리보기 라인 — 단가·금액·통화·무상 키 없음. 배정 가능량 전/후는 서버 파생값(화면 산술 0). */
export interface ImportShipmentPreviewLine {
  po_line_id: number;
  line_no: number;
  sku: ShipmentSku;
  quantity: number;
  assignable_before: number;
  remaining_after: number;
  dg: ShipmentDg;
}

/** 수입선적 비저장 미리보기(`POST /purchase-orders/{po_id}/shipments/preview`) — **통화·환율·합계 키 없음**(원가 비복사). */
export interface ImportShipmentPreview {
  po_id: number;
  po_doc_number: string;
  po_status: string;
  doc_date: string;
  shipment_kind: "IMPORT";
  counterparty: { partner_id: number; name: string };
  origin_country_code: string;
  dest_country_code: string;
  payment_terms: PaymentTerms;
  incoterm: Incoterm;
  lines: ImportShipmentPreviewLine[];
  parties: ShipmentPreviewParty[];
}

/** 직접 지정 가능한 당사자 역할 — 수하인(자동 스냅샷)·송하인(자사)은 본문으로 보내지 않는다(서버 422 `ROLE_NOT_ALLOWED`). */
export const SELECTABLE_PARTY_ROLES = ["NOTIFY", "FORWARDER", "CUSTOMS_BROKER"] as const;
export type SelectablePartyRole = (typeof SELECTABLE_PARTY_ROLES)[number];

/** 역할별 거래처 검색 유형 — 포워더·관세사는 같은 이름 유형, 통지처는 전 유형(서버 규칙과 같다 — 불일치는 서버 422). */
export const PARTY_PARTNER_TYPE: Record<SelectablePartyRole, string | null> = {
  NOTIFY: null,
  FORWARDER: "FORWARDER",
  CUSTOMS_BROKER: "CUSTOMS_BROKER",
};

/** SO 참조 수출선적 생성·미리보기 본문 — 원천 값(SKU·단가·통화·환율·조건·거래 상대) 필드가 구조적으로 없다(서버 extra=forbid). */
export interface ShipmentCreateBody {
  lines: Array<{ so_line_id: number; quantity: number }>;
  origin_country_code: string;
  dest_country_code: string;
  parties?: Array<{ role: SelectablePartyRole; partner_id: number }>;
  internal_note?: string;
}

/** PO 참조 수입선적 생성·미리보기 본문 — 원천 값·금액 필드가 구조적으로 없다(서버 extra=forbid). 송하인 = 공급사 자동·수하인 = 자사. */
export interface ImportShipmentCreateBody {
  lines: Array<{ po_line_id: number; quantity: number }>;
  origin_country_code: string;
  dest_country_code: string;
  parties?: Array<{ role: SelectablePartyRole; partner_id: number }>;
  internal_note?: string;
}

// ── 라벨(프런트 단일 표 — 모르는 값은 원문이 아니라 '기타', design-D D8·`D:307`). 상태 라벨은 전표 공용 표 `doc-status.ts`에 있다. ──

const OTHER = "기타";

const SHIPMENT_KIND: Record<string, string> = {
  EXPORT: "수출",
  IMPORT: "수입",
  CHANNEL_INBOUND: "채널입고",
  SAMPLE_FREE: "샘플무상",
};

const PARTY_ROLE: Record<string, string> = {
  SHIPPER: "송하인",
  CONSIGNEE: "수하인",
  NOTIFY: "통지처",
  FORWARDER: "포워더",
  CUSTOMS_BROKER: "관세사",
};

export const shipmentKindLabel = (code: string): string => SHIPMENT_KIND[code] ?? OTHER;
export const partyRoleLabel = (code: string): string => PARTY_ROLE[code] ?? OTHER;

/**
 * 금액 축이 있는 선적인가 — **수출만**(판별자 `shipment_kind === "EXPORT"`). 그 밖(수입·모르는 구분)은 금액·통화 칸을 그리지 않는다
 * (fail-closed — 수입 응답엔 금액·통화 키가 없다, PR-5a G3).
 */
export function isExportShipment<T extends { shipment_kind: string }>(row: T): row is Extract<T, { shipment_kind: "EXPORT" }> {
  return row.shipment_kind === "EXPORT";
}

/** 목록·섹션의 합계 칸 — 수출 = 서버 문자열 + 통화, 그 밖 = '—'(0으로 그리지 않는다). */
export const shipmentTotalText = (row: ShipmentListItem): string =>
  isExportShipment(row) ? `${row.total_text} ${row.currency}` : "—";

// ── 수입선적 동결 전이 문구(부채 R-5a-8) — 커널 공용 전이 PLANNED → RELEASE_ORDERED를 수입에도 쓴다. 수입에서는 '공급사 출하 지시로
//    선적을 확정'한다는 뜻이라 화면 문구만 구분별로 가른다(상태 코드·서버 경로 무변경 — 의미 재판정은 S4-1). ──

/** 동결 전이(`RELEASE_ORDER`) 이름 — 수출 '출고지시', 수입 '선적 확정'. 모르는 구분은 수출 문구(서버 상태명 그대로). */
export const releaseActionLabel = (kind: string): string => (kind === "IMPORT" ? "선적 확정" : "출고지시");

/** 서버 422 — ETD·B/L 발행·ETA 실적은 동결 전이 뒤에만(서버 문구는 수출 기준 '출고지시'). */
export const ACTUAL_BEFORE_RELEASE_CODE = "SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE";

/**
 * 수입선적에서 서버 422 `ACTUAL_BEFORE_RELEASE`의 '출고지시' 문구를 화면 이름('선적 확정')으로 바꾼 문구(적대 검토 low ③ — 백엔드 앱 코드 0이라
 * 화면에서 대체). 수출·다른 오류는 null(서버 문구 그대로).
 */
export function releaseAwareErrorText(error: unknown, kind: string): string | null {
  if (kind !== "IMPORT" || !(error instanceof ApiError) || error.code !== ACTUAL_BEFORE_RELEASE_CODE) return null;
  const name = releaseActionLabel(kind);
  return `ETD·B/L 발행·ETA 실적은 ${name} 뒤에만 기록할 수 있습니다. ${name}을 먼저 진행해 주세요.`;
}

/** 선적 상태 라벨(구분 반영) — 수입의 RELEASE_ORDERED는 '선적 확정', 나머지는 공용 표(`shipmentStatusLabel`). */
export const shipmentStatusText = (status: string, kind: string): string =>
  status === "RELEASE_ORDERED" ? releaseActionLabel(kind) : shipmentStatusLabel(status);

/** 사람이 고를 수 있는 상태 필터 — S3-2 활성 상태(계획·출고지시·취소). 피킹~종결은 RESERVED(S4-2)라 아직 도달하지 않는다. */
export const SHIPMENT_STATUS_FILTERS = ["PLANNED", "RELEASE_ORDERED", "CANCELLED"] as const;

// ── 서버가 준 동작(allowed_actions) ──

/** 서버 `shipment_view.allowed_actions` 9종 — PR-4a가 EDIT_MILESTONES(계획·실적·통보)·PLAN_DRAFT(초안 1클릭)·EDIT_CUSTOMS(통관)를 더했다. */
export type ShipmentAction =
  | "RELEASE_ORDER"
  | "CANCEL"
  | "EDIT_LINES"
  | "EDIT_COUNTRIES"
  | "EDIT_META"
  | "EDIT_PARTIES"
  | "EDIT_MILESTONES"
  | "PLAN_DRAFT"
  | "EDIT_CUSTOMS";
export const can = (detail: Pick<ShipmentDetail, "allowed_actions">, action: ShipmentAction): boolean =>
  detail.allowed_actions.includes(action);

// ── 국가 코드(ISO 3166-1 alpha-2) — 국가 마스터 API가 없어 입력 2자 + 한국어 이름 확인(Intl) ──

/** CLDR이 이름을 주지만 국가가 아닌 코드(지역·기구·미지정) — 출발·도착국으로 받지 않는다. */
const NOT_A_COUNTRY = new Set(["EU", "EZ", "UN", "QO", "ZZ", "XA", "XB"]);
const REGION_NAMES = new Intl.DisplayNames(["ko"], { type: "region", fallback: "none" });

/**
 * CLDR 별칭(옛·비ISO 코드)의 정식 코드 — 예: UK → GB, DD → DE, SU → RU, YU·CS → RS, ZR → CD, BU → MM, TP → TL.
 * `Intl.DisplayNames`는 별칭에도 이름을 주어(UK → '영국') 비ISO 코드가 정상처럼 보이며 저장되므로, 정식 코드와 다르면 별칭이다.
 * 별칭이 아니면 null(형식 밖 포함).
 */
export function canonicalCountryOf(code: string): string | null {
  if (!/^[A-Z]{2}$/.test(code)) return null;
  try {
    const canonical = Intl.getCanonicalLocales(`und-${code}`)[0] ?? "";
    const region = canonical.startsWith("und-") ? canonical.slice(4) : "";
    return region !== "" && region !== code ? region : null;
  } catch {
    return null;
  }
}

/** 국가 코드 → 한국어 이름. 형식이 아니거나 모르는 코드·국가가 아닌 코드·별칭 코드는 null(화면이 막는다 — 서버는 형식만 본다). */
export function countryName(code: string): string | null {
  if (!/^[A-Z]{2}$/.test(code) || NOT_A_COUNTRY.has(code) || canonicalCountryOf(code) !== null) return null;
  try {
    return REGION_NAMES.of(code) ?? null;
  } catch {
    return null;
  }
}

/** "미국 (US)" — 모르는 코드·별칭은 코드만(서버가 저장한 값은 그대로 보인다 — 정상 국가처럼 이름을 붙이지 않는다). */
export const countryText = (code: string): string => {
  const name = countryName(code);
  return name === null ? code : `${name} (${code})`;
};

// ── 오류 ──

export const SUCCESSOR_ALIVE_CODE = "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE";
export const LOCK_BUSY_CODE = "COMMON.CONCURRENCY.LOCK_BUSY";
export const ENGLISH_NAME_MISSING_CODE = "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING";

/**
 * 결과를 모르는 실패 — 네트워크 단절(0)·서버 오류(5xx·504 게이트웨이 시간 초과). 서버가 이미 처리했을 수 있으므로 같은 키로 다시 보내
 * 결과를 확인해야 한다(새 키로 보내면 중복 생성 위험).
 */
export const isResultUnknown = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 0 || error.status >= 500);

/** 409 `detail[key] = {원천 라인 id: 남은 수량}`을 라인별 맵으로 — 코드가 다르면 빈 맵(수출 잔량과 수입 배정 가능량을 섞지 않는다). */
function quantityMapOf(error: unknown, code: string, key: string): Map<number, number> {
  const result = new Map<number, number>();
  if (!(error instanceof ApiError) || error.code !== code) return result;
  const map = error.detail[key];
  if (typeof map !== "object" || map === null) return result;
  for (const [lineId, open] of Object.entries(map as Record<string, unknown>)) {
    if (typeof open === "number") result.set(Number(lineId), open);
  }
  return result;
}

/** 잔량 초과 409의 원천 라인별 남은 수량 — `detail.open_quantity = {so_line_id: 남은 수량}`(칸별 표시용). 다른 오류는 빈 맵. */
export const openQuantityByLine = (error: unknown): Map<number, number> =>
  quantityMapOf(error, QUANTITY_EXCEEDS_OPEN_CODE, "open_quantity");

/** 수입선적 배정 가능량 초과 409(`SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE`) — PO 잔량 오류(`EXCEEDS_OPEN`)와 다른 코드·다른 키. */
export const EXCEEDS_ASSIGNABLE_CODE = "SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE";

/** 배정 가능량 초과 409의 PO 라인별 남은 배정 가능량 — `detail.assignable_quantity = {po_line_id: n}`. 다른 오류는 빈 맵. */
export const assignableByLine = (error: unknown): Map<number, number> =>
  quantityMapOf(error, EXCEEDS_ASSIGNABLE_CODE, "assignable_quantity");

/** 선적 구분에 맞는 수량 초과 맵 — 수출 = 잔량(`EXCEEDS_OPEN`), 수입 = 배정 가능량(`EXCEEDS_ASSIGNABLE`). */
export const quantityConflictByLine = (error: unknown, kind: string): Map<number, number> =>
  kind === "IMPORT" ? assignableByLine(error) : openQuantityByLine(error);

/**
 * 422의 라인별 칸 오류 — `detail["lines[i].<필드>"] = 문구`를 **보낸 본문의 i번째 라인**에 붙인다(예: `LINE_MISMATCH` "이 발주의 라인이
 * 아닙니다."). `ids`는 보낸 본문 라인 순서의 원천 라인 id. 범위 밖 색인·문자열 아닌 값은 버린다(요약 문구에는 서버가 이미 싣는다).
 */
export function lineFieldErrors(error: unknown, ids: readonly number[]): Map<number, string> {
  const result = new Map<number, string>();
  if (!(error instanceof ApiError) || error.status !== 422) return result;
  for (const [field, message] of Object.entries(error.detail)) {
    const match = /^lines\[(\d+)\]\./.exec(field);
    if (match === null || typeof message !== "string") continue;
    const id = ids[Number(match[1])];
    if (id !== undefined && !result.has(id)) result.set(id, message);
  }
  return result;
}

/** SO·PO 취소 409 `SUCCESSOR_ALIVE`의 먼저 취소할 선적 번호들(`detail.successors`). 다른 오류는 빈 목록. */
export function successorNumbers(error: unknown): string[] {
  if (!(error instanceof ApiError) || error.code !== SUCCESSOR_ALIVE_CODE) return [];
  const list = error.detail.successors;
  return Array.isArray(list) ? list.filter((item): item is string => typeof item === "string") : [];
}

// ── 멱등 키 — 대화상자·폼 1회 = 키 1개, 같은 본문 재시도는 같은 키, 본문이 바뀌면 새 키 ──

export interface KeyKeeper {
  /** 이번에 보낼 본문(직렬화)에 쓸 키 — 마지막으로 보낸 본문과 다를 때만 새 키를 만든다. */
  keyFor: (serialized: string) => string;
  /** 성공 뒤 — 다음 쓰기는 새 키로 시작한다. */
  reset: () => void;
}

export function createKeyKeeper(): KeyKeeper {
  let key = crypto.randomUUID();
  let last: string | null = null;
  return {
    keyFor(serialized) {
      if (last !== null && last !== serialized) key = crypto.randomUUID();
      last = serialized;
      return key;
    },
    reset() {
      key = crypto.randomUUID();
      last = null;
    },
  };
}

// ── 쿼리 키 ──

export const SHIPMENTS_QUERY_KEY = ["shipments"] as const;
export const shipmentDetailKey = (id: number) => ["shipments", "detail", id] as const;
/** SO 상세의 '선적' 섹션 목록 키(이 수주의 선적 Page). */
export const soShipmentsKey = (soId: number) => [...SHIPMENTS_QUERY_KEY, "list", "so", soId] as const;
/** PO 상세의 '수입선적' 섹션 목록 키(이 발주의 수입선적 Page). */
export const poShipmentsKey = (poId: number) => [...SHIPMENTS_QUERY_KEY, "list", "po", poId] as const;

/**
 * 잔량 초과 409 뒤 — 다른 선적이 먼저 가져갔다는 뜻이다. 잔량을 보여 주는 화면(원천 수주 상세·이 수주의 선적 목록·보드·문서 흐름,
 * 그리고 지금 보는 선적 상세)을 서버 값으로 다시 받는다 — 칸 옆의 옛 잔량·[잔량 전부]·상한 안내가 서버 안내와 엇갈리지 않게.
 * 생성 대화상자·라인 추가·라인 수량 수정이 이 함수 하나를 쓴다(실브라우저 관통 발견 → 적대 검토 low ⑥·⑩).
 */
export function refreshAfterQuantityConflict(client: QueryClient, soId: number | null, shipmentId?: number): void {
  if (soId !== null) {
    void client.invalidateQueries({ queryKey: salesOrderDetailKey(soId), exact: true });
    void client.invalidateQueries({ queryKey: soShipmentsKey(soId) });
  }
  void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
  void client.invalidateQueries({ queryKey: ORDER_BOARD_QUERY_KEY });
  if (shipmentId !== undefined) void client.invalidateQueries({ queryKey: shipmentDetailKey(shipmentId), exact: true });
}

/**
 * 배정 가능량 초과 409 뒤(수입 — PR-5b) — 원천 발주 상세(라인 배정 가능량·입고예정)·이 발주의 수입선적 목록·지금 보는 선적 상세를 다시 받는다.
 * 수입선적은 수주·보드·문서 흐름과 무관하다(PO 상태·잔량 불변 — PR-5a).
 */
export function refreshAfterAssignableConflict(client: QueryClient, poId: number, shipmentId?: number): void {
  void client.invalidateQueries({ queryKey: purchaseOrderDetailKey(poId), exact: true });
  void client.invalidateQueries({ queryKey: poShipmentsKey(poId) });
  if (shipmentId !== undefined) void client.invalidateQueries({ queryKey: shipmentDetailKey(shipmentId), exact: true });
}
