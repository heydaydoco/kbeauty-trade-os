// 오더 인테이크(주문 접수) 응답 타입·키·한국어 문구 (S3-1 PR-13b) — backend order_intake/schemas.py·trade_chain/{router,intake_flow}.py 그대로.
// ★ 프런트는 하드 게이트 통과·확정 가능 여부를 다시 판정하지 않는다: mapping_state·intake_confirmable·blocks_intake_confirm·po_occupied·409/422 응답은 서버 값을 그대로 표시한다.
//   `intake_confirmable`은 하드 게이트 2종(품번 매핑·중복 PO)의 서버 사전 점검일 뿐 확정 가능 보장이 아니다 — 확정은 항상 서버가 최종 판정한다.
// 금액은 서버 `*_text`(단가·합계)만 표시한다. 프런트 산술 0.
// 멱등 키 규약: 확정·품번 재해석 키는 (인테이크 id, version)당 1개 — 같은 본문=같은 키, version이 오르면 새 키, 성공하면 비운다.

import { ApiError } from "./api";
import { salesOrderStatusLabel } from "./doc-status";
import { josaOf } from "./confirm";

export const ORDER_INTAKES_QUERY_KEY = ["order-intakes"] as const;
export const orderIntakeDetailKey = (id: number) => [...ORDER_INTAKES_QUERY_KEY, "detail", id] as const;
export const orderIntakeGatesKey = (id: number) => [...ORDER_INTAKES_QUERY_KEY, "gates", id] as const;

export const INTAKE_STATUS_FILTERS = ["PENDING", "CONFIRMED", "REJECTED"] as const;

export interface IntakeLine {
  id: number;
  line_no: number;
  buyer_item_code: string;
  sku_id: number | null;
  sku_code: string | null;
  sku_name_ko: string | null;
  sku_status: string | null;
  /** MAPPED · UNMAPPED · STALE — 서버 파생값(화면이 다시 계산하지 않는다). */
  mapping_state: string;
  quantity: number;
  unit_price_amount: number;
  unit_price_text: string;
  line_amount: number;
  requested_delivery_date: string | null;
  source_row_no: number | null;
}

/** 다른 문서가 같은 PO를 점유 — 번호·상태는 무역·관리자에게만(그 외 null). */
export interface PoOccupied {
  kind: string;
  doc_number: string | null;
  status: string | null;
}

export interface IntakeSummary {
  id: number;
  version: number;
  source_kind: string;
  status: string;
  buyer_partner_id: number;
  buyer_name: string | null;
  buyer_po_no: string;
  buyer_po_date: string | null;
  currency: string;
  dest_market_code: string;
  assignee_id: number;
  line_count: number;
  total_amount: number;
  total_text: string;
  sales_order_id: number | null;
  copied_from_so_id: number | null;
  po_occupied: PoOccupied | null;
  created_at: string;
  decided_at: string | null;
}

export interface IntakeOriginal {
  kind?: string;
  header?: Record<string, unknown>;
  lines?: Array<Record<string, unknown>>;
}

export interface IntakeDetail {
  id: number;
  version: number;
  source_kind: string;
  status: string;
  buyer_partner_id: number;
  buyer_name: string | null;
  buyer_po_no: string;
  buyer_po_date: string | null;
  currency: string;
  dest_market_code: string;
  assignee_id: number;
  /** 무역·관리자에게만 — 그 외 null. */
  reject_reason: string | null;
  decided_at: string | null;
  decided_by_id: number | null;
  sales_order_id: number | null;
  copied_from_so_id: number | null;
  po_occupied: PoOccupied | null;
  last_line_no: number;
  created_at: string;
  updated_at: string;
  total_amount: number;
  total_text: string;
  lines: IntakeLine[];
  original: IntakeOriginal;
}

export interface IntakeConfirmOut {
  intake_id: number;
  sales_order_id: number;
  doc_number: string;
  intake: IntakeDetail;
}

export interface IntakeGateResult {
  gate_code: string;
  line_id: number | null;
  line_no: number | null;
  level: string;
  resolution: string;
  reason_code: string;
  message_ko: string;
  basis: Record<string, unknown>;
  /** 무역·관리자에게만 의미 있는 값(다른 점유 문서 등). */
  detail: Record<string, unknown>;
  settlement: string;
  blocks_intake_confirm: boolean;
}

export interface IntakeGateReport {
  intake_id: number;
  status: string;
  phase: string;
  evaluated_at: string;
  note: string;
  readiness_scope_note: string;
  intake_confirmable: boolean;
  gates: IntakeGateResult[];
}

// ── 요청 본문 ────────────────────────────────────────────────────────────────

export interface IntakeLineBody {
  buyer_item_code: string;
  quantity: number;
  unit_price: string;
  requested_delivery_date: string | null;
}

export interface IntakeCreateBody {
  buyer_partner_id: number;
  buyer_po_no: string;
  buyer_po_date: string | null;
  currency: string;
  dest_market_code: string;
  assignee_id?: number;
  copied_from_so_id?: number;
  lines: IntakeLineBody[];
}

export interface IntakeEditLineBody extends IntakeLineBody {
  id?: number;
}

// ── 한국어 라벨 ──────────────────────────────────────────────────────────────

const INTAKE_STATUS: Record<string, string> = { PENDING: "대기", CONFIRMED: "확정(수주 접수됨)", REJECTED: "거부" };
export const intakeStatusLabel = (code: string): string => INTAKE_STATUS[code] ?? "확인 불가";

/** 색만으로 구분하지 않는다 — 배지에는 늘 글자가 함께 있다. */
export function intakeBadgeClass(code: string): string {
  switch (code) {
    case "CONFIRMED":
      return "border-gray-900 bg-gray-900 text-white";
    case "REJECTED":
      return "border-gray-300 bg-gray-100 text-gray-500 line-through";
    case "PENDING":
      return "border-gray-500 bg-white text-gray-900";
    default:
      return "border-gray-300 bg-gray-100 text-gray-700";
  }
}

const MAPPING_STATE: Record<string, string> = { MAPPED: "매핑됨", UNMAPPED: "미매핑", STALE: "재해석 필요" };
export const mappingStateLabel = (state: string): string => MAPPING_STATE[state] ?? "확인 불가";

export function mappingBadgeClass(state: string): string {
  switch (state) {
    case "MAPPED":
      return "border-gray-300 bg-white text-gray-700";
    case "UNMAPPED":
      return "border-signal-red bg-signal-red text-white";
    case "STALE":
      return "border-dashed border-signal-red bg-white text-signal-red";
    default:
      return "border-gray-300 bg-gray-100 text-gray-700";
  }
}

/** 해소 안내 — 서버 mapping_state를 문구로만 옮긴다(해소 가능 여부를 계산하지 않는다). 읽기 전용 역할에게는 직접 할 수 없는 조작을 안내하지 않는다. */
export function mappingGuide(state: string, canWrite: boolean): string | null {
  switch (state) {
    case "UNMAPPED":
      return canWrite
        ? "이 바이어 품번에 연결된 SKU가 없거나 연결된 SKU가 삭제되었습니다. 아래 '품번 등록'에서 SKU를 연결한 뒤 '품번 다시 확인'을 누르세요(기존 매핑의 SKU가 삭제된 경우에는 거래처 화면에서 그 매핑을 먼저 삭제해야 새로 등록됩니다)."
        : "이 바이어 품번에 연결된 SKU가 없거나 연결된 SKU가 삭제되었습니다. 무역 담당자가 품번 매핑을 정리한 뒤 다시 확인해야 접수할 수 있습니다.";
    case "STALE":
      return canWrite
        ? "검토한 뒤 바이어 품번 매핑이 바뀌었습니다. 자동으로 따라가지 않습니다 — '품번 다시 확인'으로 새 해석을 반영하고 내용을 다시 검토하세요."
        : "검토한 뒤 바이어 품번 매핑이 바뀌었습니다. 자동으로 따라가지 않으며, 무역 담당자가 품번을 다시 확인해야 합니다.";
    default:
      return null;
  }
}

const SKU_STATUS: Record<string, string> = { ACTIVE: "활성", DISCONTINUED: "단종", DELETED: "삭제됨", DRAFT: "초안", INACTIVE: "비활성" };
export const skuStatusLabel = (status: string | null): string => (status === null ? "—" : (SKU_STATUS[status] ?? "확인 불가"));

const SOURCE_KIND: Record<string, string> = { MANUAL: "수동 등록", CSV: "CSV 업로드" };
export const sourceKindLabel = (kind: string): string => SOURCE_KIND[kind] ?? "확인 불가";

/**
 * 조사 붙이기 — 끝의 닫는 괄호·따옴표·공백은 건너뛰고 그 앞 글자의 받침으로 고른다("SO-1 (확정)" → "…(확정)이").
 * 영문 등 판정할 수 없는 끝 글자는 받침 없음으로 본다(confirm.ts `josaOf` 규칙).
 */
export function withJosaText(text: string, kind: "을/를" | "이/가" | "은/는"): string {
  const core = text.replace(/[)\]}'"’”\s]+$/u, "");
  return `${text}${josaOf(core === "" ? text : core, kind)}`;
}

/** 점유 문서 안내 — 번호·상태는 서버가 줄 때만 쓴다(마스킹 역할은 null이라 번호 없이). */
export function poOccupiedText(occ: PoOccupied): string {
  const who = occ.kind === "SALES_ORDER" ? "수주" : "다른 인테이크";
  if (occ.doc_number === null) return `${withJosaText(who, "이/가")} 같은 바이어 PO번호를 점유 중입니다(문서 번호는 무역·관리자만 확인할 수 있습니다).`;
  const status = occ.status === null ? "" : ` (${occ.kind === "SALES_ORDER" ? salesOrderStatusLabel(occ.status) : intakeStatusLabel(occ.status)})`;
  return `${withJosaText(`${who} ${occ.doc_number}${status}`, "이/가")} 같은 바이어 PO번호를 점유 중입니다.`;
}

// ── 오류 문구 ────────────────────────────────────────────────────────────────

const HANGUL = /[가-힣]/;
const NETWORK_CODE = "CLIENT.NETWORK.UNREACHABLE";
const isNetworkError = (error: ApiError): boolean => error.status === 0 || error.code === NETWORK_CODE;

export const CODE = {
  VERSION_CONFLICT: "COMMON.CONCURRENCY.VERSION_CONFLICT",
  KEY_CONFLICT: "COMMON.IDEMPOTENCY.KEY_CONFLICT",
  LOCK_BUSY: "COMMON.CONCURRENCY.LOCK_BUSY",
  NOT_PENDING: "ORDER_INTAKE.STATE.NOT_PENDING",
  UNMAPPED_ITEMS: "ORDER_INTAKE.LINE.UNMAPPED_ITEMS",
  STALE_MAPPING: "ORDER_INTAKE.LINE.STALE_MAPPING",
  DUPLICATE_SKU: "ORDER_INTAKE.LINE.DUPLICATE_SKU",
  LIMIT_EXCEEDED: "ORDER_INTAKE.LINE.LIMIT_EXCEEDED",
  GATE_UNRESOLVED: "ORDER_INTAKE.GATE.UNRESOLVED",
  DUPLICATE_PO: "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO",
  COPY_NOT_ELIGIBLE: "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE",
  AMOUNT_OUT_OF_RANGE: "TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE",
  VALIDATION: "COMMON.VALIDATION.INVALID_FIELD",
  MARKET_NOT_REGISTERED: "MARKETS.MARKET.NOT_REGISTERED",
} as const;

export type IntakeOp = "create" | "edit" | "resolve" | "reject" | "confirm" | "item-code";

const OP_NOUN: Record<IntakeOp, string> = {
  create: "등록",
  edit: "수정",
  resolve: "품번 다시 확인",
  reject: "거부",
  confirm: "접수 확정",
  "item-code": "품번 등록",
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** 서버 detail.lines — 항목 모양이 깨지면 건너뛴다. 라인 번호·품번은 서버가 준 값 그대로. */
export function parseLineRefs(detail: Record<string, unknown> | undefined): Array<{ lineNo: number | null; code: string | null }> {
  const raw = detail?.lines;
  if (!Array.isArray(raw)) return [];
  const out: Array<{ lineNo: number | null; code: string | null }> = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    out.push({
      lineNo: typeof item.line_no === "number" ? item.line_no : null,
      code: typeof item.buyer_item_code === "string" ? item.buyer_item_code : null,
    });
  }
  return out;
}

const lineRefsText = (refs: Array<{ lineNo: number | null; code: string | null }>): string =>
  refs.map((r) => `${r.lineNo === null ? "라인" : `라인 ${r.lineNo}`}${r.code === null ? "" : `(${r.code})`}`).join(", ");

/** 중복 SKU detail.lines — [[1,3],[2,4]] (같은 SKU로 해석된 라인 번호 묶음). */
export function parseDuplicateGroups(detail: Record<string, unknown> | undefined): number[][] {
  const raw = detail?.lines;
  if (!Array.isArray(raw)) return [];
  return raw.filter(Array.isArray).map((group) => (group as unknown[]).filter((n): n is number => typeof n === "number"));
}

/** 중복 PO 점유 — 번호·상태는 서버가 줄 때만(intake_id 또는 doc_number). */
export interface PoOccupant {
  intakeId: number | null;
  docNumber: string | null;
  status: string | null;
}
export function parsePoOccupant(error: unknown): PoOccupant | null {
  if (!(error instanceof ApiError) || error.code !== CODE.DUPLICATE_PO) return null;
  const d = error.detail ?? {};
  const intakeId = typeof d.intake_id === "number" ? d.intake_id : null;
  const docNumber = typeof d.doc_number === "string" ? d.doc_number : null;
  const status = typeof d.status === "string" ? d.status : null;
  if (intakeId === null && docNumber === null && status === null) return null;
  return { intakeId, docNumber, status };
}

export function occupantText(occ: PoOccupant): string {
  if (occ.docNumber !== null) {
    const st = occ.status === null ? "" : ` (${salesOrderStatusLabel(occ.status)})`;
    return `이미 등록된 수주: ${occ.docNumber}${st}`;
  }
  if (occ.intakeId !== null) {
    const st = occ.status === null ? "" : ` (${intakeStatusLabel(occ.status)})`;
    return `이미 등록된 대기 인테이크: #${occ.intakeId}${st}`;
  }
  return "";
}

const FIELD_LABEL: Record<string, string> = {
  buyer_partner_id: "바이어",
  buyer_po_no: "바이어 PO번호",
  buyer_po_date: "바이어 PO 일자",
  currency: "통화",
  dest_market_code: "도착 시장",
  assignee_id: "담당자",
  copied_from_so_id: "복제 원본 수주",
  lines: "라인",
  reason: "사유",
  buyer_item_code: "바이어 품번",
  quantity: "수량",
  unit_price: "단가",
  requested_delivery_date: "요청납기",
  sku_id: "SKU",
};

/** 라인 순번(요청 본문의 lines 배열 index) → 화면 라벨. 지정하지 않으면 "라인 index+1"(등록 화면 — 서버가 1..n을 부여). */
export type LineLabelOf = (index: number) => string;
const positionLabel: LineLabelOf = (index) => `라인 ${index + 1}`;

/**
 * 검증 오류 키 → 한국어 위치명.
 * - `lines[0].unit_price`(서비스 검증 — 등록·수정) · `lines.0.unit_price`(요청 검증 봉투 `항목[].위치`): 요청 본문의 라인 순번 → `lineLabelOf`(수정 화면은 서버 라인 번호·'신규 n')
 * - `line_3.requested_delivery_date`(확정 — 인테이크 라인 번호 그대로)
 */
export function fieldPathLabel(key: string, lineLabelOf: LineLabelOf = positionLabel): string {
  const indexed = /^lines(?:\[(\d+)\]|\.(\d+))(?:\.(.+))?$/.exec(key);
  if (indexed) {
    const index = Number(indexed[1] ?? indexed[2]);
    const field = indexed[3];
    return `${lineLabelOf(index)} ${field === undefined ? "" : (FIELD_LABEL[field] ?? "항목")}`.trim();
  }
  const byNo = /^line_(\d+)\.(.+)$/.exec(key);
  if (byNo) return `라인 ${byNo[1]} ${FIELD_LABEL[byNo[2] as string] ?? "항목"}`;
  return FIELD_LABEL[key] ?? "입력값";
}

/** Pydantic 사유의 접두("Value error, ")를 떼어 낸다 — 서버 검증기가 던진 한국어 문구만 남긴다. */
const cleanReason = (reason: string): string => reason.replace(/^Value error,\s*/u, "").trim();

/**
 * 422 detail → '위치: 사유' 목록. 두 모양을 읽는다:
 * ① 서비스 검증 `{위치키: 한국어 메시지}` ② 요청 검증 봉투(core/errors/handlers.py) `{"항목": [{"위치": "lines.0.unit_price", "사유": "..."}]}`.
 * 한글이 없는 사유(Pydantic 영문 메시지·코드)는 원문을 노출하지 않고 위치만 안내한다.
 */
export function validationProblems(detail: Record<string, unknown> | undefined, lineLabelOf: LineLabelOf = positionLabel): string[] {
  const out: string[] = [];
  const add = (where: string, reason: string) =>
    out.push(HANGUL.test(reason) ? `${where}: ${cleanReason(reason)}` : `${withJosaText(where, "을/를")} 확인해 주세요.`);
  for (const [key, value] of Object.entries(detail ?? {})) {
    if (key === "항목" && Array.isArray(value)) {
      for (const item of value) {
        if (!isRecord(item)) continue;
        const where = typeof item["위치"] === "string" ? item["위치"] : "";
        add(fieldPathLabel(where, lineLabelOf), typeof item["사유"] === "string" ? item["사유"] : "");
      }
      continue;
    }
    if (typeof value === "string") add(fieldPathLabel(key, lineLabelOf), value);
  }
  return out;
}

const STALE_ACTION = "'최신 내용 불러오기'";

export interface IntakeErrorOptions {
  fallback?: string;
  /** 요청 본문 lines[index] → 화면 라벨(수정 화면은 서버 라인 번호·'신규 n'). */
  lineLabelOf?: LineLabelOf;
}

const INVALID_GENERIC = "입력값이 올바르지 않습니다. 입력 내용을 확인한 뒤 다시 시도해 주세요.";

/** 서버가 준 오류 번호(request_id)가 있으면 문의 안내에 붙인다 — 없는 번호를 말하지 않는다. */
const contactAdmin = (error: ApiError): string =>
  error.requestId ? `계속되면 관리자에게 문의해 주세요(오류 번호: ${error.requestId}).` : "계속되면 관리자에게 문의해 주세요.";

/**
 * 인테이크 오류 → 한국어 문구. 영문 코드·detail 원문은 노출하지 않는다(라인 번호·품번·점유 문서번호·검증 위치만 한국어로 옮김).
 * 순서: 코드 사전 → 상태별 일반 문구 → 서버 한국어 message → 기본 문구.
 */
export function intakeErrorMessage(error: unknown, op: IntakeOp, options: IntakeErrorOptions = {}): string {
  const noun = OP_NOUN[op];
  const base = options.fallback ?? `${withJosaText(noun, "을/를")} 처리하지 못했습니다.`;
  if (!(error instanceof ApiError)) return base;
  if (isNetworkError(error)) {
    if (op === "create" || op === "item-code") {
      return `연결이 끊겼습니다. ${withJosaText(noun, "이/가")} 처리되었을 수 있으니 목록에서 확인한 뒤 필요하면 같은 버튼을 다시 누르세요(같은 요청이라 중복 처리되지 않습니다).`;
    }
    if (op === "edit") {
      // 수정(PATCH)은 멱등 키 계약이 없다 — 다시 누르기 전에 반영 여부부터 확인하게 한다(반영됐다면 version이 올라 충돌로 거절된다).
      return `연결이 끊겼습니다. 수정이 반영되었을 수 있으니 먼저 ${STALE_ACTION}로 반영 여부를 확인해 주세요.`;
    }
    return `연결이 끊겼습니다. ${withJosaText(noun, "이/가")} 처리되었을 수 있으니 ${STALE_ACTION}로 상태를 확인한 뒤 필요하면 같은 버튼을 다시 누르세요(중복 처리되지 않습니다).`;
  }
  switch (error.code) {
    case CODE.VERSION_CONFLICT:
      return `다른 곳에서 이 인테이크가 먼저 수정되었습니다. ${STALE_ACTION}로 화면을 새로 고친 뒤 다시 시도해 주세요. 입력하던 내용은 새로 고치면 사라집니다.`;
    case CODE.KEY_CONFLICT:
      return `같은 요청 키로 다른 내용이 이미 처리되었습니다. ${STALE_ACTION}로 처리 결과를 확인해 주세요.`;
    case CODE.LOCK_BUSY:
      return "같은 거래처의 다른 건을 처리 중이라 처리하지 못했습니다. 잠시 후 같은 버튼을 다시 눌러 주세요(같은 요청이라 중복 처리되지 않습니다).";
    case CODE.NOT_PENDING:
      return `이미 확정되었거나 거부된 인테이크라 더 처리할 수 없습니다. ${STALE_ACTION}로 현재 상태를 확인해 주세요.`;
    case CODE.DUPLICATE_PO: {
      const occ = parsePoOccupant(error);
      const where = occ !== null ? occupantText(occ) : "";
      return `같은 바이어의 같은 PO번호가 이미 다른 문서(수주 또는 대기 중인 인테이크)에 등록되어 있습니다.${where === "" ? "" : ` ${where}.`} 기존 문서를 확인하거나, 정정이라면 기존 수주를 취소(또는 대기 인테이크를 거부)한 뒤 다시 처리해 주세요.`;
    }
    case CODE.UNMAPPED_ITEMS: {
      const refs = lineRefsText(parseLineRefs(error.detail));
      return `바이어 품번이 SKU에 매핑되지 않았거나 삭제된 품목이 있어 접수할 수 없습니다${refs === "" ? "" : `: ${refs}`}. 품번을 등록한 뒤 '품번 다시 확인'을 눌러 주세요.`;
    }
    case CODE.STALE_MAPPING: {
      const refs = lineRefsText(parseLineRefs(error.detail));
      return `검토한 뒤 바이어 품번 매핑이 바뀌었습니다${refs === "" ? "" : `: ${refs}`}. '품번 다시 확인'으로 새 해석을 반영하고 내용을 검토한 뒤 확정해 주세요(자동으로 따라가지 않습니다).`;
    }
    case CODE.DUPLICATE_SKU: {
      const groups = parseDuplicateGroups(error.detail)
        .map((nos) => nos.map((n) => `라인 ${n}`).join("·"))
        .join(" / ");
      return `같은 SKU로 매핑된 라인이 둘 이상입니다${groups === "" ? "" : `(${groups})`}. 수주는 SKU마다 라인 1줄이므로 라인을 합치거나 고쳐 주세요.`;
    }
    case CODE.LIMIT_EXCEEDED:
      return "라인은 1개 이상 200개 이하여야 합니다. 라인 수를 확인해 주세요.";
    case CODE.GATE_UNRESOLVED:
      return `접수 확정에 필요한 확인 항목을 서버가 평가하지 못했습니다. 잠시 후 다시 시도해 주세요. ${contactAdmin(error)}`;
    case CODE.COPY_NOT_ELIGIBLE:
      return op === "create"
        ? "복제 원본 수주가 복제할 수 있는 상태가 아닙니다(취소된 같은 바이어의 수주만 원본이 될 수 있습니다). 원본 없이 등록하려면 '복제 없이 등록'을 누르세요 — 입력한 내용은 그대로 남습니다."
        : `복제 원본 수주가 더 이상 복제할 수 있는 상태가 아닙니다(취소된 같은 바이어의 수주만 원본이 될 수 있습니다). ${STALE_ACTION}로 확인하고, 접수가 필요하면 이 인테이크를 거부한 뒤 복제 없이 새로 등록해 주세요.`;
    case CODE.AMOUNT_OUT_OF_RANGE:
      return "금액 또는 라인 수가 허용 범위를 넘었습니다. 수량·단가를 확인해 주세요.";
    case CODE.MARKET_NOT_REGISTERED: {
      const problems = validationProblems(error.detail, options.lineLabelOf);
      return problems.length > 0
        ? `등록되지 않았거나 삭제된 시장입니다 — ${problems.join(" / ")}`
        : "도착 시장이 등록되지 않았거나 삭제되었습니다. 시장 관리 화면에서 확인한 뒤 다시 시도해 주세요.";
    }
    case CODE.VALIDATION: {
      const problems = validationProblems(error.detail, options.lineLabelOf);
      return problems.length > 0 ? `입력값을 확인해 주세요 — ${problems.join(" / ")}` : INVALID_GENERIC;
    }
    default:
  }
  if (error.status === 403) return `${withJosaText(noun, "은/는")} 무역·관리자만 할 수 있습니다.`;
  if (error.status === 404) return "인테이크를 찾을 수 없습니다. 목록에서 다시 확인해 주세요.";
  if (error.status === 422) return INVALID_GENERIC;
  if (error.status === 409) return `처리 중 충돌이 발생했습니다. ${STALE_ACTION}로 확인한 뒤 다시 시도해 주세요.`;
  return HANGUL.test(error.message) ? error.message : base;
}

/** 다이얼로그에 '최신 내용 불러오기'를 둘 오류 — 409 계열 전체·복제 원본 상실·연결 끊김. */
export const isIntakeRecoverable = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 409 || isNetworkError(error));

/** 목록·상세 조회(GET) 실패 전용 문구 — 쓰기 문구를 재사용하지 않는다. */
export function intakeLoadErrorMessage(error: unknown, what = "오더 인테이크"): string {
  const fallback = `${withJosaText(what, "을/를")} 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.`;
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 403) return `${withJosaText(what, "을/를")} 볼 권한이 없습니다.`;
  if (error.status === 404) return "인테이크를 찾을 수 없습니다. 목록에서 다시 확인해 주세요.";
  return HANGUL.test(error.message) ? error.message : fallback;
}

/** 쓰기 라우트 403(역할 게이트)만 — 그 밖의 403 코드는 건별 거절이라 버튼 전체를 숨기지 않는다. */
export const isWriteForbidden = (error: unknown): boolean => error instanceof ApiError && error.status === 403;

// ── 입력 사전 검증(형식만 — 서버가 최종: 통화 자릿수·단가>0·납기·중복 SKU는 서버 판정) ──────────

export const MAX_QUANTITY = 99_999_999; // 서버 MAX_QUANTITY와 같은 상한 — 형식 검증용
export const MAX_LINES = 200;
const DECIMAL = /^\d+(\.\d+)?$/;

export interface LineForm {
  key: number;
  /** 기존 라인 id — 있으면 제자리 수정, 없으면 신규. */
  id: number | null;
  /** 서버 라인 번호(line_no) — 수정 화면의 기존 라인만. 위치가 아니라 이 번호로 라인을 가리킨다. */
  lineNo: number | null;
  code: string;
  qty: string;
  price: string;
  delivery: string;
}

let lineSeq = 0;
export const newLineForm = (over: Partial<LineForm> = {}): LineForm => ({ key: ++lineSeq, id: null, lineNo: null, code: "", qty: "", price: "", delivery: "", ...over });

/**
 * 라인 표시 라벨 — 등록 화면은 위치(서버가 1..n을 부여), 수정 화면은 기존 라인=서버 line_no("라인 3"), 새 라인="신규 n".
 * 라인 하나를 지워도 남은 라인의 이름이 바뀌지 않는다(오류 안내가 엉뚱한 라인을 가리키지 않게).
 */
export function lineLabels(lines: LineForm[], mode: "create" | "edit"): string[] {
  let fresh = 0;
  return lines.map((line, index) => {
    if (mode === "create") return `라인 ${index + 1}`;
    if (line.lineNo !== null) return `라인 ${line.lineNo}`;
    fresh += 1;
    return `신규 ${fresh}`;
  });
}

export type LineField = "code" | "qty" | "price";
export interface FormProblem {
  message: string;
  /** 문제 입력의 DOM id(포커스·aria-invalid 대상) — 없으면 폼 전체 문제. */
  inputId: string | null;
}

/** 라인 입력의 DOM id — 편집기·검증·포커스가 같은 규칙을 쓴다. */
export const lineInputId = (line: Pick<LineForm, "key">, field: LineField): string => `intake-line-${line.key}-${field}`;

/** 라인 형식 검증 — **모든** 문제를 모은다(첫 문제만이 아니라). 금액은 문자열 그대로이며 산술하지 않는다. */
export function validateLineForms(lines: LineForm[], labels: string[] = lineLabels(lines, "create")): FormProblem[] {
  if (lines.length === 0) return [{ message: "라인을 1개 이상 추가해 주세요.", inputId: null }];
  if (lines.length > MAX_LINES) return [{ message: `라인은 ${MAX_LINES}개 이하여야 합니다.`, inputId: null }];
  const out: FormProblem[] = [];
  for (const [index, line] of lines.entries()) {
    const label = labels[index] ?? `라인 ${index + 1}`;
    if (line.code.trim() === "") out.push({ message: `${label}의 바이어 품번을 입력해 주세요.`, inputId: lineInputId(line, "code") });
    const qty = line.qty.trim();
    if (!/^[1-9][0-9]*$/.test(qty) || Number(qty) > MAX_QUANTITY) out.push({ message: `${label}의 수량은 1 이상의 정수로 입력해 주세요.`, inputId: lineInputId(line, "qty") });
    if (!DECIMAL.test(line.price.trim())) out.push({ message: `${label}의 단가는 숫자(예: 12.34)로 입력해 주세요.`, inputId: lineInputId(line, "price") });
  }
  return out;
}

export function lineBodies(lines: LineForm[]): IntakeLineBody[] {
  return lines.map((line) => ({
    buyer_item_code: line.code.trim(),
    quantity: Number(line.qty.trim()),
    unit_price: line.price.trim(),
    requested_delivery_date: line.delivery === "" ? null : line.delivery,
  }));
}
