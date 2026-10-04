// 선적(수출선적) 응답 타입·라벨·키 — backend/app/modules/shipments/schemas.py·trade_chain/shipment_view.py 그대로 (S3-2 PR-3b).
//
// ★ 버튼 노출은 서버가 준 `allowed_actions`만 따른다(PROGRESS 'S3-2 PR-3a' 인계 계약) — 화면이 역할·상태로 다시 판정하지 않는다.
// ★ 금액은 서버 문자열(*_text) 그대로, 날짜(`doc_date`·`fx_rate_date`)는 'YYYY-MM-DD' 문자열 그대로(시각 객체로 바꾸지 않는다 —
//   UTC 자정 해석으로 하루 밀림). 시각(`created_at`·`updated_at`·`frozen_at`·`occurred_at`)만 `toKstDisplay`로 KST 표시.
// ★ 마일스톤(ETD·ETA)·통관은 PR-4a/4b — 이 응답에는 그 필드가 없다(화면도 만들지 않는다).

import { ApiError } from "./api";
import { QUANTITY_EXCEEDS_OPEN_CODE, type Incoterm, type PaymentTerms } from "./proforma";

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

export interface ShipmentListItem {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  shipment_kind: string;
  source: ShipmentSource;
  counterparty_name: string;
  origin_country_code: string;
  dest_country_code: string;
  currency: string;
  total_amount: number;
  total_text: string;
  line_count: number;
  assignee: ShipmentAssignee;
  created_at: string;
  updated_at: string;
}

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

export interface ShipmentLine {
  id: number;
  line_no: number;
  so_line_id: number | null;
  sku: ShipmentSku;
  quantity: number;
  currency: string;
  unit_price_amount: number | null;
  unit_price_text: string | null;
  is_free: boolean;
  line_amount: number;
  line_amount_text: string;
  /** 원천 라인 대비 — `remaining_after`는 이 선적까지 반영한 원천 잔량(서버 파생값, 화면 산술 0). */
  source_line: { id: number | null; line_no: number; quantity: number; remaining_after: number };
  dg: ShipmentDg;
  /** §8.3 '자리' — 항상 NOT_IMPLEMENTED('가용재고 미산정' 배지, 숫자·0 표시 금지). */
  availability: { status: string };
}

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

export interface ShipmentDetail {
  id: number;
  doc_number: string;
  doc_date: string;
  status: string;
  shipment_kind: string;
  version: number;
  frozen_at: string | null;
  source: ShipmentSource;
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
  internal_note: string | null;
  assignee: ShipmentAssignee;
  last_line_no: number;
  dg_line_count: number;
  lines: ShipmentLine[];
  parties: ShipmentParty[];
  allowed_actions: string[];
  created_at: string;
  updated_at: string;
}

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

/** 사람이 고를 수 있는 상태 필터 — S3-2 활성 상태(계획·출고지시·취소). 피킹~종결은 RESERVED(S4-2)라 아직 도달하지 않는다. */
export const SHIPMENT_STATUS_FILTERS = ["PLANNED", "RELEASE_ORDERED", "CANCELLED"] as const;

// ── 서버가 준 동작(allowed_actions) ──

export type ShipmentAction = "RELEASE_ORDER" | "CANCEL" | "EDIT_LINES" | "EDIT_COUNTRIES" | "EDIT_META" | "EDIT_PARTIES";
export const can = (detail: Pick<ShipmentDetail, "allowed_actions">, action: ShipmentAction): boolean =>
  detail.allowed_actions.includes(action);

// ── 국가 코드(ISO 3166-1 alpha-2) — 국가 마스터 API가 없어 입력 2자 + 한국어 이름 확인(Intl) ──

/** CLDR이 이름을 주지만 국가가 아닌 코드(지역·기구·미지정) — 출발·도착국으로 받지 않는다. */
const NOT_A_COUNTRY = new Set(["EU", "EZ", "UN", "QO", "ZZ", "XA", "XB"]);
const REGION_NAMES = new Intl.DisplayNames(["ko"], { type: "region", fallback: "none" });

/** 국가 코드 → 한국어 이름. 형식이 아니거나 모르는 코드·국가가 아닌 코드는 null(화면이 막는다 — 서버는 형식만 본다). */
export function countryName(code: string): string | null {
  if (!/^[A-Z]{2}$/.test(code) || NOT_A_COUNTRY.has(code)) return null;
  try {
    return REGION_NAMES.of(code) ?? null;
  } catch {
    return null;
  }
}

/** "미국 (US)" — 모르는 코드는 코드만(서버가 저장한 값은 그대로 보인다). */
export const countryText = (code: string): string => {
  const name = countryName(code);
  return name === null ? code : `${name} (${code})`;
};

// ── 오류 ──

export const SUCCESSOR_ALIVE_CODE = "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE";
export const LOCK_BUSY_CODE = "COMMON.CONCURRENCY.LOCK_BUSY";
export const ENGLISH_NAME_MISSING_CODE = "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING";

/** 잔량 초과 409의 원천 라인별 남은 수량 — `detail.open_quantity = {so_line_id: 남은 수량}`(칸별 표시용). 다른 오류는 빈 맵. */
export function openQuantityByLine(error: unknown): Map<number, number> {
  const result = new Map<number, number>();
  if (!(error instanceof ApiError) || error.code !== QUANTITY_EXCEEDS_OPEN_CODE) return result;
  const map = error.detail.open_quantity;
  if (typeof map !== "object" || map === null) return result;
  for (const [lineId, open] of Object.entries(map as Record<string, unknown>)) {
    if (typeof open === "number") result.set(Number(lineId), open);
  }
  return result;
}

/** SO 취소 409 `SUCCESSOR_ALIVE`의 먼저 취소할 선적 번호들(`detail.successors`). 다른 오류는 빈 목록. */
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
