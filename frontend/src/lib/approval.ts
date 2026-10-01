// 승인(결재함·결재선·대결) 공용 타입·라벨·표 (S3-1 PR-9b — design-C C3·C4·C5·C8).
//
// ★ 승인 통제의 정본은 서버다. 화면은 서버가 계산한 `can_decide`·`can_withdraw`·`decide_blocked_reason`만 따른다
//   (ADMIN은 hasRole이 늘 true라 역할로 추정하면 '기안자=승인자' 버튼이 열린다 — 추정 금지).
// ★ 금액은 서버가 준 정수 최소단위를 lib/money의 자릿수 옮김으로만 표시한다(프런트 산술 0).

import { formatMoney, type Currency } from "./money";

export const APPROVALS_QUERY_KEY = ["approvals"] as const;
export const approvalDetailKey = (id: number) => ["approvals", "detail", id] as const;
export const APPROVAL_LINES_QUERY_KEY = ["approval-lines"] as const;
export const DELEGATIONS_QUERY_KEY = ["delegations"] as const;

export interface ApprovalView {
  id: number;
  approval_type: string;
  target_type: string;
  target_id: number;
  target_label: string;
  status: string;
  required_role: string;
  requested_by_id: number | null;
  requester_name: string | null;
  basis_amount: number;
  basis_currency: string;
  snapshot: Record<string, string | number | boolean | null>;
  decided_by_name: string | null;
  decided_on_behalf_of_name: string | null;
  decided_at: string | null;
  consumed_at: string | null;
  status_reason: string | null;
  void_reason_code: string | null;
  can_decide: boolean;
  decide_blocked_reason: "SELF" | "NOT_APPROVER" | null;
  can_withdraw: boolean;
  created_at: string | null;
  version: number;
}

export interface ApprovalEvent {
  id: number;
  occurred_at: string;
  from_status: string | null;
  to_status: string;
  actor_name: string | null;
  on_behalf_of_name: string | null;
  reason: string | null;
  reason_code: string | null;
}

export interface ApprovalLine {
  id: number;
  approval_type: string;
  threshold_amount: number;
  threshold_currency: string;
  threshold_text: string;
  approver_role: string;
  note: string | null;
  version: number;
}

export interface Coverage {
  configured: boolean;
  entries: { approval_type: string; currency: string; lines: number; has_zero_threshold: boolean }[];
  messages: string[];
}

export interface Delegation {
  id: number;
  delegator_user_id: number;
  delegator_name: string | null;
  delegate_user_id: number;
  delegate_name: string | null;
  approval_type: string;
  delegated_role: string;
  start_on: string;
  end_on: string;
  note: string | null;
  state: string;
  inert_reason: string | null;
  revoked_at: string | null;
  can_revoke: boolean;
  version: number;
}

export interface Candidate {
  id: number;
  display_name: string;
}

// ── 라벨 ─────────────────────────────────────────────────────────────────────

const STATUS: Record<string, string> = {
  REQUESTED: "결재 대기",
  APPROVED: "승인",
  REJECTED: "반려",
  WITHDRAWN: "회수",
  CONSUMED: "사용됨",
  VOIDED: "무효",
};
/** 모르는 코드는 감추지 않고 '기타'로 보인다(서버가 새 상태를 더해도 화면이 깨지지 않게). */
export const approvalStatusLabel = (code: string): string => STATUS[code] ?? "기타";
export const APPROVAL_STATUS_FILTERS = Object.keys(STATUS);

/** 색만으로 구분하지 않는다 — 배지에는 늘 글자가 함께 있다. */
export function approvalBadgeClass(code: string): string {
  switch (code) {
    case "REQUESTED":
      return "border-gray-500 bg-gray-200 text-gray-800";
    case "APPROVED":
    case "CONSUMED":
      return "border-gray-900 bg-gray-900 text-white";
    case "REJECTED":
    case "VOIDED":
      return "border-signal-red bg-white text-signal-red";
    default:
      return "border-gray-300 bg-gray-100 text-gray-500 line-through";
  }
}

const TYPE: Record<string, string> = { SO_CREDIT_EXCEEDED: "여신 초과 수주 확정" };
export const approvalTypeLabel = (code: string): string => TYPE[code] ?? "기타 승인";
export const APPROVAL_TYPE_CODES = Object.keys(TYPE);

const ROLE: Record<string, string> = {
  ADMIN: "관리자",
  TRADE: "무역",
  LOGISTICS: "물류",
  CERT: "인증",
};
export const approverRoleLabel = (code: string): string => ROLE[code] ?? code;
export const APPROVER_ROLE_CODES = Object.keys(ROLE);

const VOID_REASON: Record<string, string> = {
  TARGET_CHANGED: "승인 뒤 대상(수주)이 바뀌어 승인이 무효가 되었습니다. 변경 내용을 확인하고 다시 요청해야 합니다.",
  TARGET_CANCELLED: "대상(수주)이 취소·확정되었거나 더는 승인 대상이 아니어서 무효가 되었습니다.",
  CAP_EXCEEDED: "수주 금액이 승인된 한도를 넘게 바뀌어 무효가 되었습니다. 새 금액으로 다시 요청해야 합니다.",
  NOT_REQUIRED: "여신 초과가 해소되어 더는 승인이 필요하지 않아 무효가 되었습니다.",
};
export const voidReasonText = (code: string | null): string | null =>
  code === null ? null : (VOID_REASON[code] ?? "승인이 무효가 되었습니다.");

/** 대상 종류 → 화면 경로. 표에 없는 종류는 링크 없이 글자로만(없는 화면으로 보내지 않는다). */
const TARGET_ROUTES: Record<string, (id: number) => string> = {
  SALES_ORDER: (id) => `/sales-orders/${id}`,
};
export const approvalTargetRoute = (targetType: string, targetId: number): string | null =>
  TARGET_ROUTES[targetType]?.(targetId) ?? null;

export const SELF_DECISION_NOTICE = "본인이 요청한 승인은 직접 결정할 수 없습니다.";
export const NOT_APPROVER_NOTICE = "이 승인을 결재할 수 있는 자격(결재 역할·유효한 대결)이 없어 열람만 가능합니다.";
export const NO_ACCESS_NOTICE = "존재하지 않거나 열람 권한이 없습니다.";

const DELEGATION_STATE: Record<string, string> = {
  ACTIVE: "진행 중",
  UPCOMING: "예정",
  EXPIRED: "기간 만료",
  REVOKED: "종료됨",
  INERT: "효력 없음",
};
export const delegationStateLabel = (code: string): string => DELEGATION_STATE[code] ?? "기타";

const INERT_REASON: Record<string, string> = {
  delegator_inactive: "위임자 계정이 비활성입니다",
  delegate_inactive: "수임자 계정이 비활성입니다",
  delegator_lost_role: "위임자가 위임한 역할을 더는 갖고 있지 않습니다",
  delegate_no_role: "수임자가 결재 가능한 역할을 갖고 있지 않습니다",
};
export const inertReasonText = (code: string | null): string | null =>
  code === null ? null : (INERT_REASON[code] ?? "현재 효력이 없습니다");

export function delegationBadgeClass(state: string): string {
  switch (state) {
    case "ACTIVE":
      return "border-gray-900 bg-gray-900 text-white";
    case "INERT":
      return "border-signal-red bg-white text-signal-red";
    case "UPCOMING":
      return "border-gray-500 bg-gray-200 text-gray-800";
    default:
      return "border-gray-300 bg-gray-100 text-gray-500";
  }
}

// ── 금액·스냅샷 표시(서버 정수 + 통화 → 자릿수 옮김만) ─────────────────────────

/** 통화표가 없거나 모르는 통화면 값을 지어내지 않고 최소단위 그대로 밝혀 보인다. */
export function moneyText(
  amount: number | null | undefined,
  code: string | null | undefined,
  currencies: readonly Currency[] | undefined,
): string {
  if (amount === null || amount === undefined) return "—";
  if (!code) return `${amount} (최소단위)`;
  if (currencies === undefined) return "…";
  try {
    return formatMoney(amount, code, currencies);
  } catch {
    return `${amount} ${code} (최소단위)`;
  }
}

export interface SnapshotFacts {
  verdict: string | null;
  limit: number | null;
  limitCurrency: string | null;
  openOrders: number | null;
  thisOrder: number | null;
  receivablesReflected: boolean;
  exposureAfter: number | null;
  excess: number | null;
  exposureIsPartial: boolean;
  reasonCodes: string[];
  fxRate: string | null;
  fxRateDate: string | null;
  fxDocCurrency: string | null;
}

const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

export function snapshotFacts(snapshot: Record<string, unknown>): SnapshotFacts {
  const codes = str(snapshot.reason_codes);
  return {
    verdict: str(snapshot.verdict),
    limit: num(snapshot.limit_amount),
    limitCurrency: str(snapshot.limit_currency),
    openOrders: num(snapshot.open_orders_amount),
    thisOrder: num(snapshot.this_order_amount),
    // 미수 provider 미등록(기본)이면 false — 키가 없어도 '반영됨'으로 읽지 않는다(fail-visible).
    receivablesReflected: snapshot.receivables_reflected === true,
    exposureAfter: num(snapshot.exposure_after_amount),
    excess: num(snapshot.excess_amount),
    exposureIsPartial: snapshot.exposure_is_partial === true,
    reasonCodes: codes === null ? [] : codes.split(","),
    fxRate: str(snapshot.fx_rate),
    fxRateDate: str(snapshot.fx_rate_date),
    fxDocCurrency: str(snapshot.fx_doc_currency),
  };
}

const VERDICT: Record<string, string> = {
  EXCEEDED: "한도 초과",
  WITHIN_LIMIT: "한도 이내",
  NOT_MANAGED: "여신 관리 안 함",
  UNEVALUABLE: "평가 불능",
};
export const verdictLabel = (code: string): string => VERDICT[code] ?? "기타";

const REASON_CODE: Record<string, string> = {
  CURRENCY_NOT_CONVERTIBLE: "다른 통화의 수주를 환산하지 못해 노출 계산에서 빠졌습니다",
  RECEIVABLE_PROVIDER_ERROR: "미수금 조회에 실패해 미수가 반영되지 않았습니다",
  NO_INCREMENT: "이번 수주로 늘어나는 노출이 없습니다",
};
export const reasonCodeText = (code: string): string => REASON_CODE[code] ?? "기타 평가 사유";

/** 결정 3종의 다이얼로그 문구 — 승인은 사유 선택(없음), 반려·회수는 사유 필수. */
export const DECISION_VERBS = {
  APPROVE: { title: "승인할까요?", confirm: "승인", reason: null },
  REJECT: { title: "반려할까요?", confirm: "반려", reason: "반려 사유 (필수)" },
  WITHDRAW: { title: "회수할까요?", confirm: "회수", reason: "회수 사유 (필수)" },
} as const;
export type DecisionVerb = keyof typeof DECISION_VERBS;
