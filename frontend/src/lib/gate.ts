// 게이트(SO 상세 GatePanel) 응답 타입·키·한국어 문구 (S3-1 PR-11b) — backend/app/modules/gates/schemas.py 그대로.
// ★ 화면은 통과·미통과를 다시 판정하지 않는다: level·resolution·settlement·can_override·clearance·approval은 서버 값을 그대로 표시한다.
// 금액은 표시하지 않는다(여신 수치는 정수 최소단위뿐이고 *_text 계약이 없다 — PROGRESS 11b 부채). 프런트 산술 0.

import { ApiError } from "./api";
import { salesOrderDetailKey } from "./sales-order";

export type GateCode =
  | "ITEM_MAPPING"
  | "DUPLICATE_PO"
  | "PRICE_DEVIATION"
  | "CREDIT"
  | "MARKET_READINESS"
  | "MOQ"
  | "PI_DEPOSIT";

export interface GateOverrideInfo {
  id: number;
  /** 무역·관리자에게만 — 그 외 역할은 null. */
  reason: string | null;
  authorized_role: string;
  granted_by_id: number;
  created_at: string;
}

export interface GateResult {
  gate_code: string;
  line_id: number | null;
  line_no: number | null;
  level: string;
  resolution: string;
  reason_code: string;
  message_ko: string;
  basis: Record<string, unknown>;
  detail: Record<string, unknown>;
  /** 마스킹 역할의 CREDIT은 빈 문자열. */
  basis_hash: string;
  override_roles: string[];
  settlement: string;
  can_override: boolean;
  override: GateOverrideInfo | null;
}

export interface GateReport {
  subject_type: string;
  subject_id: number;
  status: string;
  input_digest: string;
  authoritative: boolean;
  evaluated_at: string;
  note: string;
  readiness_scope_note: string;
  clearance: { cleared: boolean; needs_approval: boolean; unresolved_count: number };
  approval: { available: boolean; approval_id: number | null };
  policies: Record<string, { value: string | number; source: string }>;
  gates: GateResult[];
}

export interface GateOverrideBody {
  gate_code: string;
  line_id: number | null;
  basis_hash: string;
  reason: string;
}

// SALES_ORDERS_QUERY_KEY 접두 아래 — SO 편집·전이 뒤의 prefix 무효화가 게이트 판정도 함께 새로 읽게 한다(판정 입력이 바뀌었으므로).
export const gatesKey = (soId: number) => ["sales-orders", "gates", soId] as const;
export { salesOrderDetailKey };

export const GATE_LABEL: Record<string, string> = {
  ITEM_MAPPING: "품번 매핑",
  DUPLICATE_PO: "중복 PO",
  PRICE_DEVIATION: "가격 편차",
  CREDIT: "여신 한도",
  MARKET_READINESS: "시장 준비 상태",
  MOQ: "최소주문수량(MOQ)",
  PI_DEPOSIT: "PI 선수금 입금",
};
export const gateLabel = (code: string): string => GATE_LABEL[code] ?? "기타 게이트";

const LEVEL_LABEL: Record<string, string> = { PASS: "통과", WARN: "경고", BLOCK: "차단", UNKNOWN: "판정 불가" };
export const levelLabel = (level: string): string => LEVEL_LABEL[level] ?? "확인 불가";

/** 색만으로 구분하지 않는다 — 배지에는 늘 글자가 함께 있다. 판정 불가(UNKNOWN)는 차단과 다른 점선 테두리. */
export function levelBadgeClass(level: string): string {
  switch (level) {
    case "PASS":
      return "border-gray-300 bg-white text-gray-700";
    case "WARN":
      return "border-signal-amber bg-white text-gray-900";
    case "BLOCK":
      return "border-signal-red bg-signal-red text-white";
    case "UNKNOWN":
      return "border-dashed border-signal-red bg-white text-signal-red";
    default:
      return "border-gray-300 bg-gray-100 text-gray-700";
  }
}

const RESOLUTION_LABEL: Record<string, string> = {
  NONE: "없음(데이터를 고쳐야 함)",
  OVERRIDE: "예외 승인(override)",
  APPROVAL: "승인(결재)",
};
export const resolutionLabel = (resolution: string): string => RESOLUTION_LABEL[resolution] ?? "확인 불가";

const SETTLEMENT_LABEL: Record<string, string> = {
  NOT_REQUIRED: "해소 불필요",
  OVERRIDDEN: "해소됨(예외 승인)",
  APPROVED: "해소됨(승인)",
  UNRESOLVED: "미해소",
};
export const settlementLabel = (settlement: string): string => SETTLEMENT_LABEL[settlement] ?? "확인 불가";

export const PI_MODE_KEY = "pi_advance_gate_mode";
export const PRICE_TOLERANCE_KEY = "price_deviation_tolerance_bp";
/** 서버가 정책 미설정을 알리는 출처 값 — 가장 엄격한 기본 동작이 적용 중이다. */
export const POLICY_UNSET = "UNSET_DEFAULT";

const PI_MODE_LABEL: Record<string, string> = { OFF: "끔(OFF)", WARN: "경고(WARN)", BLOCK: "차단(BLOCK)" };
export const piModeLabel = (value: string | number): string => PI_MODE_LABEL[String(value)] ?? "확인 불가";

export const gateTargetLabel = (row: Pick<GateResult, "gate_code" | "line_no" | "line_id">): string =>
  `${gateLabel(row.gate_code)}${row.line_id !== null ? ` · 라인 ${row.line_no ?? "?"}` : ""}`;

// ── 오류 문구 ────────────────────────────────────────────────────────────────

const VALIDATION_CODE = "COMMON.VALIDATION.INVALID_FIELD";
export const OVERRIDE_NOT_ALLOWED = "GATES.OVERRIDE.NOT_ALLOWED";
const STALE = "GATES.OVERRIDE.STALE";
const ALREADY_GRANTED = "GATES.OVERRIDE.ALREADY_GRANTED";
const NOT_GRANTED = "GATES.OVERRIDE.NOT_GRANTED";
const ORDER_NOT_OPEN = "GATES.OVERRIDE.ORDER_NOT_OPEN";
const NOT_APPLICABLE = "GATES.OVERRIDE.NOT_APPLICABLE";

export const GATE_ERROR_TEXT: Record<string, string> = {
  [OVERRIDE_NOT_ALLOWED]:
    "이 항목의 예외 승인·철회는 현재 역할로 할 수 없습니다(가격 편차·MOQ는 무역·관리자, 시장 준비 상태·PI 입금은 관리자, 철회는 부여한 본인 또는 관리자, 철회된 항목의 재부여는 관리자만 가능합니다).",
  [NOT_APPLICABLE]:
    "예외 승인 대상이 아닌 항목입니다. 품번 매핑·중복 PO는 데이터를 고쳐야 하고, 여신 한도는 승인(결재)으로만 해소됩니다. '최신 내용 불러오기'로 현재 판정을 확인해 주세요.",
  [STALE]:
    "확인하신 판정이 이미 바뀌었습니다. '최신 내용 불러오기'로 새 판정을 확인한 뒤 다시 시도해 주세요(입력한 사유는 지워집니다).",
  [ALREADY_GRANTED]:
    "이미 같은 판정에 유효한 예외 승인이 있습니다. 되돌리려면 먼저 철회해야 하며, 철회한 뒤의 재부여는 관리자만 할 수 있습니다. '최신 내용 불러오기'로 현재 상태를 확인해 주세요.",
  [NOT_GRANTED]:
    "철회할 유효한 예외 승인이 없습니다(없거나 이미 철회되었습니다). '최신 내용 불러오기'로 현재 상태를 확인해 주세요.",
  [ORDER_NOT_OPEN]:
    "접수 상태가 아닌 수주에는 예외 승인을 부여하거나 철회할 수 없습니다. 수주의 현재 상태를 확인해 주세요.",
  [VALIDATION_CODE]:
    "입력값이 올바르지 않습니다. 사유는 5~500자이며 줄바꿈·탭·보이지 않는 글자는 쓸 수 없고, 공백을 뺀 실제 글자가 5자 이상이어야 합니다.",
  "COMMON.IDEMPOTENCY.KEY_CONFLICT":
    "같은 요청 키로 다른 내용이 이미 처리되었습니다. '최신 내용 불러오기'로 처리 결과를 확인해 주세요.",
};

const HANGUL = /[가-힣]/;

/** 영문 코드·detail은 노출하지 않는다 — 코드 사전 → 상태별 일반 문구 → 서버 한국어 message → 기본 문구 순. */
export function gateErrorMessage(error: unknown, fallback = "요청을 처리하지 못했습니다."): string {
  if (!(error instanceof ApiError)) return fallback;
  const mapped = GATE_ERROR_TEXT[error.code];
  if (mapped !== undefined) return mapped;
  if (error.status === 403) return "예외 승인·철회 권한이 없습니다(무역·관리자만 가능합니다).";
  if (error.status === 422) return GATE_ERROR_TEXT[VALIDATION_CODE] as string;
  if (error.status === 409) return "처리 중 충돌이 발생했습니다. '최신 내용 불러오기'로 확인한 뒤 다시 시도해 주세요.";
  if (error.status === 404) return "수주를 찾을 수 없습니다. 목록에서 다시 확인해 주세요.";
  return HANGUL.test(error.message) ? error.message : fallback;
}

/** 다이얼로그 안에 '최신 내용 불러오기'를 둘 충돌·낡음 계열(409·대상 아님·철회할 부여 없음). */
export const isGateRecoverable = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 409 || error.code === NOT_APPLICABLE || error.code === NOT_GRANTED);

/** 게이트 전역 403(라우트 역할 게이트)만 — 게이트별 NOT_ALLOWED는 다른 항목에서는 가능하므로 버튼을 숨기지 않는다. */
export const isRouteForbidden = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 403 && error.code !== OVERRIDE_NOT_ALLOWED;

// ── 사유 사전 검증(서버가 최종 — gates/text.py와 같은 규칙) ───────────────────

export const REASON_MIN = 5;
export const REASON_MAX = 500;
const INVISIBLE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}ᅟᅠㅤﾠ]/u;

/** 코드포인트 기준 5~500자 · 제어·서식·채움 문자 거부 · 공백 뺀 실질 5자. 문제 없으면 null. */
export function validateOverrideReason(reason: string): string | null {
  if (INVISIBLE.test(reason)) return "사유에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다.";
  const trimmed = reason.trim();
  const length = [...trimmed].length;
  if (length < REASON_MIN) return `사유는 ${REASON_MIN}자 이상 입력해 주세요.`;
  if (length > REASON_MAX) return `사유는 ${REASON_MAX}자 이내로 입력해 주세요.`;
  if ([...trimmed.replace(/\s+/gu, "")].length < REASON_MIN) return `사유는 공백을 뺀 실제 글자가 ${REASON_MIN}자 이상이어야 합니다.`;
  return null;
}

/** 멱등 키 — crypto.randomUUID가 없거나 던지는 환경에서도 키를 만든다. */
export function newGateKey(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch {
    // 폴백으로
  }
  const rand = () => Math.random().toString(16).slice(2, 10).padEnd(8, "0");
  return `gate-${Date.now().toString(16)}-${rand()}-${rand()}`;
}
