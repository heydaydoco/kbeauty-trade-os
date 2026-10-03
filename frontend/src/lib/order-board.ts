// 오더 보드 (S3-1 PR-15b — design-D D6 / ADR-0066·0067) — backend modules/order_board/{schemas,constants,router}.py 그대로.
//
// ★ 열·카드·건수·has_more·벌크 결과 분류는 **서버 값 그대로** 쓴다 — 화면이 열을 다시 나누거나 확정 가능 여부를 판정하지 않는다.
// ★ 벌크 멱등 키 규약(PR-15a 인계 B7 ③④): 사용자 클릭마다 새 키. 응답을 못 받았을 때(504·네트워크)만 **완전히 같은 요청 본문**을
//   같은 키로 재전송해 리포트를 회수한다. 같은 키+다른 요청은 서버가 요청 전체를 409 `COMMON.IDEMPOTENCY.KEY_CONFLICT`로 거절한다.
// ★ 금액은 서버 `total_text` 그대로(프런트 산술 0), 시각은 KST 표시(`toKstDisplay`).

import { ApiError } from "./api";

export const ORDER_BOARD_QUERY_KEY = ["order-board"] as const;
export const SAVED_FILTERS_QUERY_KEY = ["order-board", "saved-filters"] as const;

export type BoardStage = "INTAKE_PENDING" | "SO_RECEIVED" | "SO_ON_HOLD" | "SO_CONFIRMED";
export type CardKind = "INTAKE" | "SO";
export type BulkAction = "CONFIRM_INTAKE" | "CONFIRM_SO" | "ASSIGN";
export type BulkOutcome = "OK" | "SKIPPED" | "BLOCKED" | "CONFLICT" | "FORBIDDEN" | "FAILED";

/** 서버 상한(constants.py) — 화면은 미리 알리기만 하고 최종 판정은 서버다. */
export const BULK_MAX_TARGETS = 50;
export const SAVED_FILTER_LIMIT = 20;
export const SAVED_FILTER_NAME_MAX = 60;
export const QUERY_MAX = 100;
export const FILTER_DATE_MIN = "2000-01-01";
export const FILTER_DATE_MAX = "2999-12-31";

export interface BoardCard {
  kind: CardKind;
  id: number;
  ref_label: string;
  buyer_partner_id: number;
  buyer_name: string | null;
  buyer_po_no: string | null;
  line_count: number;
  total_amount: number;
  total_text: string;
  currency: string;
  age_days: number;
  assignee_id: number;
  assignee_name: string | null;
  updated_at: string;
  version: number;
}

export interface BoardColumn {
  stage: BoardStage;
  label_ko: string;
  total: number;
  has_more: boolean;
  items: BoardCard[];
}

export interface OrderBoard {
  columns: BoardColumn[];
  generated_at: string;
}

/** `BoardFilter` — 쿼리 파라미터이자 저장 필터 `filter_config`의 모양(서버 extra=forbid — 이 키 밖은 보내지 않는다). */
export interface BoardFilter {
  q: string | null;
  buyer_partner_id: number | null;
  assignee_id: number | null;
  currency: string | null;
  dest_market_code: string | null;
  created_from: string | null;
  created_to: string | null;
}

export const EMPTY_FILTER: BoardFilter = {
  q: null,
  buyer_partner_id: null,
  assignee_id: null,
  currency: null,
  dest_market_code: null,
  created_from: null,
  created_to: null,
};

/** 쿼리 파라미터 순서 고정 — 같은 조건이면 같은 주소(쿼리 키·CSV 주소가 한 쌍). */
const FILTER_KEYS = [
  "q",
  "buyer_partner_id",
  "assignee_id",
  "currency",
  "dest_market_code",
  "created_from",
  "created_to",
] as const satisfies readonly (keyof BoardFilter)[];

/** 빈 값은 빼고, q는 앞뒤 공백을 떼고, 통화·시장 코드는 대문자로(서버 패턴 ^[A-Z]{3}$·^[A-Z]{2}$). */
export function normalizeFilter(filter: BoardFilter): BoardFilter {
  const text = (value: string | null): string | null => {
    const trimmed = (value ?? "").trim();
    return trimmed === "" ? null : trimmed;
  };
  const upper = (value: string | null): string | null => text(value)?.toUpperCase() ?? null;
  return {
    q: text(filter.q),
    buyer_partner_id: filter.buyer_partner_id,
    assignee_id: filter.assignee_id,
    currency: upper(filter.currency),
    dest_market_code: upper(filter.dest_market_code),
    created_from: text(filter.created_from),
    created_to: text(filter.created_to),
  };
}

/** 필터 → 쿼리 문자열(앞의 '?' 없음). 비어 있으면 "". */
export function filterToQuery(filter: BoardFilter, extra: Record<string, string | number> = {}): string {
  const normalized = normalizeFilter(filter);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(extra)) params.set(key, String(value));
  for (const key of FILTER_KEYS) {
    const value = normalized[key];
    if (value !== null) params.set(key, String(value));
  }
  return params.toString();
}

export const withQuery = (path: string, query: string): string => (query === "" ? path : `${path}?${query}`);

export const boardPath = (filter: BoardFilter): string => withQuery("/v1/order-board", filterToQuery(filter));
export const boardItemsPath = (filter: BoardFilter, stage: BoardStage): string =>
  withQuery("/v1/order-board/items", filterToQuery(filter, { stage }));
export const boardExportPath = (filter: BoardFilter, stage?: BoardStage): string =>
  withQuery("/v1/order-board/export.csv", filterToQuery(filter, stage ? { stage } : {}));

/** 서버 `invisible_char_problem`과 같은 글자 집합(Cc·Cf·Zl·Zp·한글 채움) — 화면 사전 안내용, 최종 판정은 서버 422. */
const INVISIBLE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}ᅟᅠㅤﾠ]/u;
export const hasInvisibleChar = (value: string): boolean => INVISIBLE.test(value);

export type FilterField = "q" | "created_from" | "created_to" | "created_range" | "currency" | "dest_market_code" | "form";
export type FieldErrors = Partial<Record<FilterField, string>>;

/** 서버로 보내기 전 화면 검사 — 서버와 같은 규칙만(더 엄격하게 만들지 않는다). 최종 판정은 서버 422. */
export function checkFilter(filter: BoardFilter): FieldErrors {
  const errors: FieldErrors = {};
  if (filter.q !== null && hasInvisibleChar(filter.q)) {
    errors.q = "검색어에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다.";
  } else if (filter.q !== null && [...filter.q.trim()].length > QUERY_MAX) {
    errors.q = `검색어는 ${QUERY_MAX}자까지 입력할 수 있습니다.`;
  }
  const outOfRange = (value: string | null) => value !== null && (value < FILTER_DATE_MIN || value > FILTER_DATE_MAX);
  if (outOfRange(filter.created_from)) errors.created_from = "접수일은 2000-01-01부터 2999-12-31 사이로 입력해 주세요.";
  if (outOfRange(filter.created_to)) errors.created_to = "접수일은 2000-01-01부터 2999-12-31 사이로 입력해 주세요.";
  if (
    errors.created_from === undefined &&
    errors.created_to === undefined &&
    filter.created_from !== null &&
    filter.created_to !== null &&
    filter.created_from > filter.created_to
  ) {
    errors.created_range = "접수일 시작은 끝보다 늦을 수 없습니다.";
  }
  return errors;
}

const HANGUL = /[가-힣]/u;
const cleanReason = (reason: string): string => reason.replace(/^Value error,\s*/u, "").trim();
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

const FIELD_FALLBACK: Record<FilterField, string> = {
  q: "검색어를 확인해 주세요(보이지 않는 글자·100자 초과 불가).",
  created_from: "접수일 시작을 확인해 주세요(2000-01-01~2999-12-31).",
  created_to: "접수일 끝을 확인해 주세요(2000-01-01~2999-12-31).",
  created_range: "접수일 범위를 확인해 주세요.",
  currency: "통화는 영문 대문자 3자입니다.",
  dest_market_code: "목적 시장 코드는 영문 대문자 2자입니다.",
  form: "조건을 확인해 주세요.",
};

/**
 * 보드 조회 422(`COMMON.VALIDATION.INVALID_FIELD`) → 필드별 문구. 요청 검증 봉투 `{"항목":[{"위치","사유"}]}`를 읽는다.
 * 쿼리 모델 전체 검증(접수일 시작>끝)은 위치가 "(본문)"으로 오므로 사유에 '접수일'이 있으면 범위 오류로 본다.
 * 한글 사유만 그대로 옮기고 영문(Pydantic 기본) 사유는 필드별 한국어 안내로 바꾼다.
 */
export function filterFieldErrors(error: unknown): FieldErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const items = error.detail?.["항목"];
  if (!Array.isArray(items)) return null;
  const out: FieldErrors = {};
  for (const item of items) {
    if (!isRecord(item)) continue;
    const where = typeof item["위치"] === "string" ? item["위치"] : "";
    const reason = typeof item["사유"] === "string" ? cleanReason(item["사유"]) : "";
    let field: FilterField;
    if (where === "q" || where === "created_from" || where === "created_to" || where === "currency" || where === "dest_market_code") {
      field = where;
    } else if (reason.includes("접수일")) {
      field = "created_range";
    } else {
      field = "form";
    }
    out[field] = HANGUL.test(reason) ? reason : FIELD_FALLBACK[field];
  }
  return Object.keys(out).length > 0 ? out : null;
}

// ── 벌크 ─────────────────────────────────────────────────────────────────────

export interface BulkTarget {
  kind: CardKind;
  id: number;
  expected_version: number;
}

export interface BulkRequest {
  action: BulkAction;
  targets: BulkTarget[];
  assignee_id?: number;
}

export interface BulkBlockedGate {
  gate_code: string;
  line_id: number | null;
  line_no: number | null;
  level: string;
  resolution: string;
  reason_code: string;
  message_ko: string;
}

export interface BulkItemResult {
  kind: CardKind;
  id: number;
  outcome: BulkOutcome;
  code: string | null;
  message_ko: string;
  blocked_gates: BulkBlockedGate[];
  version: number | null;
  sales_order_id: number | null;
  doc_number: string | null;
}

export interface BulkReport {
  action: BulkAction;
  results: BulkItemResult[];
  total: number;
  ok_count: number;
  skipped_count: number;
  fail_count: number;
  outcome_counts: Record<string, number>;
}

/** 액션별 허용 대상 종류(서버 ACTION_KINDS) — 다른 종류를 보내면 서버가 422로 거절하므로 화면이 걸러서 보낸다. */
export const ACTION_KINDS: Record<BulkAction, readonly CardKind[]> = {
  CONFIRM_INTAKE: ["INTAKE"],
  CONFIRM_SO: ["SO"],
  ASSIGN: ["INTAKE", "SO"],
};

export const ACTION_LABEL: Record<BulkAction, string> = {
  CONFIRM_INTAKE: "인테이크 확정",
  CONFIRM_SO: "수주 확정",
  ASSIGN: "담당자 지정",
};

export const cardKey = (card: { kind: CardKind; id: number }): string => `${card.kind}:${card.id}`;

/**
 * 선택한 카드 → 벌크 요청 본문. 액션이 받는 종류만, (종류, id) 순으로 정렬해 같은 선택이면 같은 본문이 된다.
 * expected_version은 화면이 본 카드의 version 그대로(낙관 잠금).
 */
export function buildBulkRequest(action: BulkAction, cards: readonly BoardCard[], assigneeId?: number): BulkRequest {
  const allowed = ACTION_KINDS[action];
  const rank = (kind: CardKind) => (kind === "INTAKE" ? 0 : 1);
  const targets = cards
    .filter((card) => allowed.includes(card.kind))
    .map((card) => ({ kind: card.kind, id: card.id, expected_version: card.version }))
    .sort((a, b) => rank(a.kind) - rank(b.kind) || a.id - b.id);
  return action === "ASSIGN" ? { action, targets, assignee_id: assigneeId } : { action, targets };
}

/** 응답을 못 받은 경우(서버 처리 여부를 모름)만 같은 키 재전송 대상이다 — 네트워크 단절(0)·게이트웨이 시간 초과(504). */
export const isResponseLost = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 0 || error.status === 504);

export const KEY_CONFLICT_CODE = "COMMON.IDEMPOTENCY.KEY_CONFLICT";

export const OUTCOME_LABEL: Record<BulkOutcome, string> = {
  OK: "처리됨",
  SKIPPED: "변경 없음",
  BLOCKED: "막힘 — 개별 처리 필요",
  CONFLICT: "경합 — 새로 고침 필요",
  FORBIDDEN: "권한 없음",
  FAILED: "실패",
};

export const outcomeLabel = (outcome: string): string => OUTCOME_LABEL[outcome as BulkOutcome] ?? "확인 불가";

export function outcomeBadgeClass(outcome: string): string {
  switch (outcome) {
    case "OK":
      return "border-green-600 text-green-800";
    case "SKIPPED":
      return "border-gray-400 text-gray-600";
    case "BLOCKED":
      return "border-signal-amber text-amber-800";
    case "CONFLICT":
      return "border-blue-500 text-blue-800";
    case "FORBIDDEN":
      return "border-signal-red text-signal-red border-dashed";
    default:
      return "border-signal-red text-signal-red";
  }
}

/** 결과 표시 순서(요약 칸) — 서버 outcome_counts 6키. */
export const OUTCOME_ORDER: readonly BulkOutcome[] = ["OK", "SKIPPED", "BLOCKED", "CONFLICT", "FORBIDDEN", "FAILED"];

/** 대상 상세 화면 주소 — BLOCKED는 여기서 개별 처리한다(벌크에는 override·승인 버튼이 없다). */
export const detailPath = (kind: CardKind, id: number): string =>
  kind === "INTAKE" ? `/orders/intakes/${id}` : `/sales-orders/${id}`;

/** 벌크 요청 자체의 오류(리포트를 못 받음) → 한국어. 서버 message 우선, 키 충돌·응답 유실은 다음 할 일을 붙인다. */
export function bulkErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === KEY_CONFLICT_CODE) {
      return `${error.message} 이 요청은 실행되지 않았습니다. 보드를 새로 고친 뒤 대상을 다시 선택해 실행해 주세요.`;
    }
    if (error.status === 0) {
      return "서버 응답을 받지 못했습니다(네트워크 오류). 서버에서는 처리되었을 수 있습니다 — '결과 다시 받기'를 누르면 같은 요청으로 처리 결과를 받아 옵니다(두 번 처리되지 않습니다).";
    }
    if (error.status === 504) {
      return "서버 응답 시간이 초과되었습니다. 서버에서는 처리되었을 수 있습니다 — '결과 다시 받기'를 누르면 같은 요청으로 처리 결과를 받아 옵니다(두 번 처리되지 않습니다).";
    }
    if (error.status === 403) {
      return "벌크 처리 권한이 없습니다(무역·관리자만 가능합니다).";
    }
    const problems = filterProblemsText(error);
    return problems ? `${error.message} (${problems})` : error.message;
  }
  return "요청을 처리하지 못했습니다.";
}

/** 422 detail의 한글 사유만 이어 붙인다(영문 원문·입력값은 노출하지 않는다). */
function filterProblemsText(error: ApiError): string {
  const out: string[] = [];
  for (const [key, value] of Object.entries(error.detail ?? {})) {
    if (key === "항목" && Array.isArray(value)) {
      for (const item of value) {
        if (isRecord(item) && typeof item["사유"] === "string" && HANGUL.test(item["사유"])) out.push(cleanReason(item["사유"]));
      }
    } else if (typeof value === "string" && HANGUL.test(value)) {
      out.push(value);
    }
  }
  return out.join(" / ");
}

// ── 저장 필터 ─────────────────────────────────────────────────────────────────

export interface SavedFilter {
  id: number;
  name: string;
  filter_config: BoardFilter | null;
  needs_resave: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export const FILTER_LIMIT_CODE = "ORDER_BOARD.FILTER.LIMIT_REACHED";
export const FILTER_DUPLICATE_CODE = "ORDER_BOARD.FILTER.DUPLICATE_NAME";
const VERSION_CONFLICT_CODE = "COMMON.CONCURRENCY.VERSION_CONFLICT";

/** 이름 사전 검사 — 서버 규칙(1~60자·보이지 않는 글자 불가, strip 전 원문 검사)과 같다. 최종 판정은 서버. */
export function checkFilterName(name: string): string | null {
  if (hasInvisibleChar(name)) return "필터 이름에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다.";
  if (name.trim() === "") return "필터 이름을 입력해 주세요.";
  if ([...name].length > SAVED_FILTER_NAME_MAX) return `필터 이름은 ${SAVED_FILTER_NAME_MAX}자까지 입력할 수 있습니다.`;
  return null;
}

export interface SavedFilterError {
  /** 이름 입력칸 옆에 붙일 문구(422 name·409 중복 이름). */
  name: string | null;
  /** 저장 필터 영역 상단 문구. */
  general: string | null;
}

/** 저장 필터 오류 → 이름 칸/일반 문구. 서버 한국어 message를 그대로 쓰고, 낙관 잠금은 다시 불러오기를 안내한다. */
export function savedFilterError(error: unknown): SavedFilterError {
  if (!(error instanceof ApiError)) return { name: null, general: "요청을 처리하지 못했습니다." };
  if (error.code === FILTER_DUPLICATE_CODE) return { name: error.message, general: null };
  if (error.code === FILTER_LIMIT_CODE) return { name: null, general: error.message };
  if (error.code === VERSION_CONFLICT_CODE) {
    return {
      name: null,
      general: "다른 곳에서 이 저장 필터가 먼저 바뀌었습니다. 목록을 다시 불러온 뒤 다시 시도해 주세요.",
    };
  }
  if (error.status === 404) {
    return { name: null, general: "저장 필터를 찾을 수 없습니다(이미 삭제되었을 수 있습니다). 목록을 다시 불러옵니다." };
  }
  if (error.status === 422) {
    const detail = error.detail ?? {};
    const direct = detail["name"];
    if (typeof direct === "string") return { name: direct, general: null };
    const items = detail["항목"];
    if (Array.isArray(items)) {
      for (const item of items) {
        if (!isRecord(item)) continue;
        const where = typeof item["위치"] === "string" ? item["위치"] : "";
        const reason = typeof item["사유"] === "string" ? cleanReason(item["사유"]) : "";
        if (where === "name") {
          return {
            name: HANGUL.test(reason) ? reason : `필터 이름은 1~${SAVED_FILTER_NAME_MAX}자로, 보이지 않는 글자 없이 입력해 주세요.`,
            general: null,
          };
        }
      }
    }
    const problems = filterProblemsText(error);
    return { name: null, general: problems ? `${error.message} (${problems})` : error.message };
  }
  return { name: null, general: error.message };
}
