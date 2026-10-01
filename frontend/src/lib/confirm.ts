// 수주 확정·여신 초과 승인 요청 (S3-1 PR-12b) — backend trade_chain/{schemas,confirm,approval_requests}.py 그대로.
// ★ 프런트는 통과·미통과·승인 필요 여부를 다시 판정하지 않는다: 409 `blocked_gates[]`·`pending_approval_*`·`can_override`·`resolution`은 서버 값을 그대로 표시한다.
// 금액은 서버 `*_text`가 있을 때만 표시한다(여신 basis는 정수 최소단위뿐 — 없으면 승인 상세로 안내). 프런트 산술 0.
// 멱등 키 규약: 확정·승인 요청 키는 (SO id, version)당 1개 — 본문 `{version}` 기준 Map으로 재사용하고 version이 오르면 새 키, 성공하면 비운다.

import { ApiError } from "./api";
import type { ApprovalView } from "./approval";
import type { GateResult } from "./gate";
import type { SalesOrderDetail } from "./sales-order";

export const CONFIRM_GATE_BLOCKED = "TRADE_CHAIN.CONFIRM.GATE_BLOCKED";
const VERSION_CONFLICT = "COMMON.CONCURRENCY.VERSION_CONFLICT";
const KEY_CONFLICT = "COMMON.IDEMPOTENCY.KEY_CONFLICT";
const LOCK_BUSY = "COMMON.CONCURRENCY.LOCK_BUSY";
const NOT_ALLOWED = "TRADE_DOCS.TRANSITION.NOT_ALLOWED";
const INCOMPLETE = "TRADE_DOCS.DOCUMENT.INCOMPLETE";
const VALIDATION = "COMMON.VALIDATION.INVALID_FIELD";
const APPROVAL_STALE = "APPROVALS.APPROVAL.STALE";
const APPROVAL_REQUIRED = "APPROVALS.APPROVAL.REQUIRED";
const APPROVAL_ALREADY_ACTIVE = "APPROVALS.APPROVAL.ALREADY_ACTIVE";
const LINE_NOT_CONFIGURED = "APPROVALS.LINE.NOT_CONFIGURED";
const NO_ELIGIBLE_APPROVER = "APPROVALS.APPROVAL.NO_ELIGIBLE_APPROVER";

export interface ConfirmBody {
  version: number;
}

export interface AllocationOut {
  status: string;
  note: string;
}

/** POST /sales-orders/{id}/confirm 200 — `SalesOrderConfirmOut`. gates는 확정 시점 결과 전건(settlement 포함). */
export interface ConfirmResult {
  sales_order: SalesOrderDetail;
  gates: GateResult[];
  allocation: AllocationOut;
  evaluation_id: number;
}

/** 409 detail의 미해소 게이트 1건 — settlement·override 정보는 없다(전건 미해소). */
export type BlockedGate = Pick<
  GateResult,
  | "gate_code"
  | "line_id"
  | "line_no"
  | "level"
  | "resolution"
  | "reason_code"
  | "message_ko"
  | "basis"
  | "detail"
  | "basis_hash"
  | "override_roles"
  | "can_override"
>;

export interface BlockedDetail {
  blocked_gates: BlockedGate[];
  needs_approval: boolean;
  pending_approval_id: number | null;
  pending_approval_status: string | null;
  evaluation_id: number | null;
  input_digest: string | null;
}

export type ApprovalRequestResult = ApprovalView & { created: boolean };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const asStr = (v: unknown): string => (typeof v === "string" ? v : "");
const asIntOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) ? v : null);

/** 409 GATE_BLOCKED의 detail을 읽는다 — 다른 오류나 모양이 깨진 응답이면 null(일반 오류 문구로). 값은 서버가 준 대로(판정·정렬 없음). */
export function parseBlockedDetail(error: unknown): BlockedDetail | null {
  if (!(error instanceof ApiError) || error.code !== CONFIRM_GATE_BLOCKED) return null;
  const raw = error.detail;
  if (!isRecord(raw) || !Array.isArray(raw.blocked_gates)) return null;
  const gates: BlockedGate[] = [];
  for (const item of raw.blocked_gates) {
    if (!isRecord(item) || typeof item.gate_code !== "string") continue;
    gates.push({
      gate_code: item.gate_code,
      line_id: asIntOrNull(item.line_id),
      line_no: asIntOrNull(item.line_no),
      level: asStr(item.level),
      resolution: asStr(item.resolution),
      reason_code: asStr(item.reason_code),
      message_ko: asStr(item.message_ko),
      basis: isRecord(item.basis) ? item.basis : {},
      detail: isRecord(item.detail) ? item.detail : {},
      basis_hash: asStr(item.basis_hash),
      override_roles: Array.isArray(item.override_roles) ? item.override_roles.filter((r): r is string => typeof r === "string") : [],
      can_override: item.can_override === true,
    });
  }
  return {
    blocked_gates: gates,
    needs_approval: raw.needs_approval === true,
    pending_approval_id: asIntOrNull(raw.pending_approval_id),
    pending_approval_status: typeof raw.pending_approval_status === "string" ? raw.pending_approval_status : null,
    evaluation_id: asIntOrNull(raw.evaluation_id),
    input_digest: typeof raw.input_digest === "string" ? raw.input_digest : null,
  };
}

// ── 한국어 라벨(서버 열거값 → 문구, 모르는 값은 감추지 않고 '확인 불가') ──────────────

const CREDIT_VERDICT: Record<string, string> = {
  WITHIN_LIMIT: "한도 이내로 확정",
  NOT_MANAGED: "여신 관리 대상 아님",
  APPROVED: "여신 초과 승인을 사용해 확정",
};
export const creditVerdictLabel = (code: string | null | undefined): string => (code ? (CREDIT_VERDICT[code] ?? "확인 불가") : "—");

const PI_VERDICT: Record<string, string> = {
  PASS: "통과",
  NOT_APPLICABLE: "해당 없음",
  WARN: "경고 상태로 확정",
  OVERRIDDEN: "예외 승인을 사용해 확정",
  SKIPPED_OFF: "게이트 꺼짐(미적용)",
};
export const piVerdictLabel = (code: string | null | undefined): string => (code ? (PI_VERDICT[code] ?? "확인 불가") : "—");

const ALLOCATION: Record<string, string> = {
  NOT_IMPLEMENTED: "재고 할당은 아직 구현 전이라 할당되지 않았습니다",
};
export const allocationLabel = (status: string): string => ALLOCATION[status] ?? "재고 할당 결과를 확인할 수 없습니다";

/** 미해소 게이트의 해소 안내 — 서버가 준 resolution·can_override를 문구로만 옮긴다(프런트가 해소 가능 여부를 계산하지 않는다). */
export function resolutionGuide(gate: Pick<BlockedGate, "resolution" | "can_override">): string {
  switch (gate.resolution) {
    case "OVERRIDE":
      return gate.can_override
        ? "예외 승인(override)으로 해소할 수 있습니다. 위 '게이트 판정' 표에서 해당 항목의 '예외 승인' 버튼으로 처리하세요."
        : "예외 승인(override)이 필요하지만 현재 역할·상태로는 부여할 수 없습니다. 위 '게이트 판정' 표의 안내를 확인하거나 권한이 있는 담당자에게 요청하세요.";
    case "APPROVAL":
      return "승인(결재)으로만 해소됩니다. 아래 '승인 요청'에서 승인을 올리고 결재가 끝난 뒤 다시 확정하세요.";
    case "NONE":
      return "승인·예외 승인으로 해소할 수 없는 항목입니다. 수주 데이터를 고친 뒤 다시 확정하세요.";
    default:
      return "해소 방법을 확인할 수 없습니다. 게이트 판정 표를 확인하세요.";
  }
}

// ── 오류 문구 ────────────────────────────────────────────────────────────────

const HANGUL = /[가-힣]/;

const INCOMPLETE_LABEL: Record<string, string> = {
  payment_terms: "결제조건",
  incoterm: "Incoterms",
  fx_rate: "환율",
  buyer_name: "바이어 표기",
  lines: "라인(1개 이상)",
};

/** 확정 전 완결성 422의 누락 항목 이름 — 서버가 준 키를 한국어 이름으로(모르는 키는 건너뜀). */
export function missingFieldLabels(detail: Record<string, unknown> | undefined): string[] {
  const given = detail ?? {};
  // 서버 키 순서와 무관하게 고정 순서(결제조건→…→라인)로 — 화면이 흔들리지 않게.
  return Object.entries(INCOMPLETE_LABEL).flatMap(([key, label]) => (key in given ? [label] : []));
}

export const CONFIRM_ERROR_TEXT: Record<string, string> = {
  [VERSION_CONFLICT]:
    "다른 곳에서 이 수주가 먼저 수정되었거나, 확정 중에 같은 거래처의 다른 수주가 바뀌어(여신 판정 경합) 확정하지 않았습니다. '최신 내용 불러오기'로 화면을 새로 고친 뒤 게이트 상태를 확인하고 다시 확정해 주세요.",
  [KEY_CONFLICT]:
    "같은 요청 키로 다른 내용이 이미 처리되었습니다. '최신 내용 불러오기'로 처리 결과(수주 상태)를 확인해 주세요.",
  [LOCK_BUSY]:
    "같은 거래처의 다른 건을 처리 중이라 확정하지 못했습니다. 잠시 후 같은 버튼을 다시 눌러 주세요(같은 요청이라 중복 확정되지 않습니다).",
  [NOT_ALLOWED]: "접수 상태가 아닌 수주는 확정할 수 없습니다. '최신 내용 불러오기'로 수주의 현재 상태를 확인해 주세요.",
  [APPROVAL_STALE]:
    "확정 직전에 승인이 무효가 되었습니다(수주 내용·금액이 바뀜). '최신 내용 불러오기' 후 게이트를 확인하고 승인을 다시 요청해 주세요.",
  [APPROVAL_REQUIRED]:
    "승인이 필요한 건이라 승인 없이는 확정할 수 없습니다. '최신 내용 불러오기' 후 승인을 요청하고 결재가 끝난 뒤 다시 확정해 주세요.",
  [CONFIRM_GATE_BLOCKED]:
    "해소되지 않은 게이트가 있어 수주를 확정하지 못했습니다. 위 '게이트 판정' 표에서 항목을 확인하고 해소한 뒤 다시 확정해 주세요.",
  [VALIDATION]: "확정 요청의 값이 올바르지 않습니다. '최신 내용 불러오기' 후 다시 시도해 주세요.",
};

/** 확정 오류 — 코드 사전 → 상태별 일반 문구 → 서버 한국어 message → 기본 문구. 영문 코드·detail은 노출하지 않는다(누락 항목 이름만 한국어로). */
export function confirmErrorMessage(error: unknown, fallback = "수주를 확정하지 못했습니다."): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.code === INCOMPLETE) {
    const names = missingFieldLabels(error.detail);
    return names.length > 0
      ? `확정에 필요한 항목이 비어 있습니다: ${names.join(", ")}. 항목을 입력한 뒤 다시 확정해 주세요.`
      : "확정에 필요한 항목이 비어 있습니다. 수주 내용을 확인한 뒤 다시 확정해 주세요.";
  }
  const mapped = CONFIRM_ERROR_TEXT[error.code];
  if (mapped !== undefined) return mapped;
  if (error.status === 403) return "수주 확정은 무역·관리자만 할 수 있습니다.";
  if (error.status === 404) return "수주를 찾을 수 없습니다. 수주 목록에서 다시 확인해 주세요.";
  if (error.status === 422) return CONFIRM_ERROR_TEXT[VALIDATION] as string;
  if (error.status === 409) return "처리 중 충돌이 발생했습니다. '최신 내용 불러오기'로 확인한 뒤 다시 시도해 주세요.";
  return HANGUL.test(error.message) ? error.message : fallback;
}

/** 확정 다이얼로그에 '최신 내용 불러오기'를 둘 오류 — 409 계열 전체와 승인 필요(422 REQUIRED). */
export const isConfirmRecoverable = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 409 || error.code === APPROVAL_REQUIRED);

export const APPROVAL_REQUEST_ERROR_TEXT: Record<string, string> = {
  [VERSION_CONFLICT]:
    "다른 곳에서 이 수주가 먼저 수정되었거나 같은 거래처의 다른 수주가 바뀌었습니다. '최신 내용 불러오기'로 화면을 새로 고친 뒤 다시 요청해 주세요.",
  [KEY_CONFLICT]: "같은 요청 키로 다른 내용이 이미 처리되었습니다. '최신 내용 불러오기'로 처리 결과를 확인해 주세요.",
  [LOCK_BUSY]: "같은 거래처의 다른 건을 처리 중입니다. 잠시 후 같은 버튼을 다시 눌러 주세요(같은 요청이라 중복 요청되지 않습니다).",
  [NOT_ALLOWED]: "접수 상태가 아닌 수주에는 승인을 요청할 수 없습니다. '최신 내용 불러오기'로 수주의 현재 상태를 확인해 주세요.",
  [LINE_NOT_CONFIGURED]: "여신 초과 승인의 결재선이 없어 승인 요청을 만들 수 없습니다. 관리자에게 결재선 등록을 요청해 주세요.",
  [NO_ELIGIBLE_APPROVER]:
    "결재할 수 있는 사람이 없어 승인 요청을 만들 수 없습니다(요청자 본인은 결재할 수 없습니다). 관리자에게 결재 역할 보유자 지정을 요청해 주세요.",
  [APPROVAL_ALREADY_ACTIVE]: "이 건에는 이미 진행 중인 승인이 있습니다. 승인 목록에서 진행 상태를 확인해 주세요.",
  [VALIDATION]:
    "승인을 요청할 수 없는 상태입니다. 여신 초과분이 없거나(승인이 필요 없음) 여신을 평가하지 못한 경우입니다. '최신 내용 불러오기' 후 게이트 판정을 확인해 주세요.",
};

/** 승인 요청 오류 — 422(초과분 0·평가 불능)는 서버가 detail에 준 한국어 안내가 있으면 그것을, 없으면 일반 문구를 쓴다. 영문 코드는 노출하지 않는다. */
export function approvalRequestErrorMessage(error: unknown, fallback = "승인을 요청하지 못했습니다."): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.code === VALIDATION) {
    const text = [error.detail?.credit, error.detail?.approval].find((v): v is string => typeof v === "string" && HANGUL.test(v));
    return text ?? (APPROVAL_REQUEST_ERROR_TEXT[VALIDATION] as string);
  }
  const mapped = APPROVAL_REQUEST_ERROR_TEXT[error.code];
  if (mapped !== undefined) return mapped;
  if (error.status === 403) return "승인 요청은 무역·관리자만 할 수 있습니다.";
  if (error.status === 404) return "수주를 찾을 수 없습니다. 수주 목록에서 다시 확인해 주세요.";
  if (error.status === 422) return APPROVAL_REQUEST_ERROR_TEXT[VALIDATION] as string;
  if (error.status === 409) return "처리 중 충돌이 발생했습니다. '최신 내용 불러오기'로 확인한 뒤 다시 시도해 주세요.";
  return HANGUL.test(error.message) ? error.message : fallback;
}

export const isApprovalRequestRecoverable = (error: unknown): boolean => error instanceof ApiError && error.status === 409;

/** 같은 본문 → 같은 키(재시도 흡수). 본문(version)이 달라지면 새 키. */
export function keyFor(map: Map<string, string>, serialized: string, make: () => string): string {
  const found = map.get(serialized);
  if (found !== undefined) return found;
  const created = make();
  map.set(serialized, created);
  return created;
}
