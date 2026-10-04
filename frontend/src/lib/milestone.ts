// 마일스톤·통관·OEM 생산 일정·품목군 마일스톤 세트 — 응답 타입·라벨·배지 규칙·요청 본문·쿼리 키 (S3-2 PR-4b).
//
// 응답 모양 원천: backend shipments/schemas.py(MilestoneBoardOut·MilestoneRowOut·MilestoneWriteOut·OemMilestoneBoardOut·
// MilestoneChangeOut·CustomsRecordOut·ProfileMilestoneTypeOut) + trade_chain/milestone_view.py — 추측 필드 0.
// ★ 판정(D-N·도과·유효값·파생값·휴일·부분 수리)은 전부 서버 계산값이다 — 화면 날짜 산술 0(정의 이원화 금지, design-D D3).
// ★ 날짜형 값('YYYY-MM-DD' — 현지 달력일)은 문자열 그대로 쓴다. 시각 객체로 바꾸지 않는다(UTC 자정 해석으로 하루 밀림).
//   시각형(서류마감·Cargo Closing)만 `{at_utc, tz}`이고 표시·변환은 lib/datetime.ts가 맡는다.
// ★ 라벨은 이 파일의 단일 표에서만 — 모르는 종류는 '기타', 모르는 파생 사유는 '산정 불가(기타)'(빈칸·0·'-'로 그리지 않는다).

import { ApiError } from "./api";

// ── 응답 타입 ──

/** 시각형 값 — UTC 시각 + IANA 시간대(서버 정규 이름 — 입력 Asia/Saigon → Asia/Ho_Chi_Minh). */
export interface InstantValue {
  at_utc: string;
  tz: string;
}

/** 날짜형 = 'YYYY-MM-DD'(현지 날짜), 시각형 = InstantValue, 값 없음 = null. */
export type MilestoneValue = string | InstantValue | null;

export interface EffectiveValue {
  /** 날짜형 'YYYY-MM-DD' / 시각형 UTC ISO 문자열. */
  value: string;
  basis: "ACTUAL" | "PLANNED";
}

export interface DerivedValue {
  status: "OK" | "UNKNOWN" | "NOT_APPLICABLE";
  value: string | null;
  basis: "ACTUAL" | "PLANNED" | null;
  reason_code: string | null;
}

export interface HolidayFlag {
  flag: "HOLIDAY" | "CLEAR" | "UNVERIFIED";
  country: string;
  name: string | null;
}

export interface MilestoneRow {
  milestone_type: string;
  kind: "STORED" | "DERIVED";
  value_shape: "DATE" | "DATETIME";
  /** false = 이 선적(구분·결제유형)에 해당 없음 — 화면이 숨긴다(L/C 제시기한은 L/C 결제에서만 true). */
  applicable: boolean;
  planned: MilestoneValue;
  actual: MilestoneValue;
  effective: EffectiveValue | null;
  derived: DerivedValue | null;
  /** 시각형만 — D-N 기준일(min(현지, KST))·현지 날짜. 날짜형은 null(R-25). */
  scan_date: string | null;
  local_date: string | null;
  /** 신고수리 행만 — NONE·CLEARED·PARTIAL(미수리 1건↑, R-06). */
  customs_state: "NONE" | "PARTIAL" | "CLEARED" | null;
  customs_pending_count: number | null;
  days_left: number | null;
  is_overdue: boolean | null;
  /** 판정 불가 사유(저장형) — TZ_UNRESOLVED면 scan_date·local_date·days_left·is_overdue가 null(KST 추정 금지). */
  unknown_reason: string | null;
  /** 적재기한만 — MET·MET_LATE·OPEN·OVERDUE·UNKNOWN. */
  fulfilment: string | null;
  /** ETA(도착국)만 — R-09. */
  holiday: HolidayFlag | null;
  /** ETD·ETA·Cargo Closing만 0을 넘는다(ROLLOVER_TYPES — 화면은 값만 따른다). */
  rollover_count: number;
  unnotified_rollovers: number;
  order_warning: string | null;
  /** 행이 있으면(초안 포함) 그 id·version — 쓰기 때 version 필수, 없으면 생략(서버 409). */
  milestone_id: number | null;
  version: number | null;
  /** 신고수리 실적 = 통관 기록(마일스톤 실적 버튼 없음), 파생 행 = null. */
  input_source: "MILESTONE" | "CUSTOMS_RECORD" | null;
}

export interface MilestoneBoard {
  today_kst: string;
  holiday_summary: { holiday: number; unverified: number };
  rows: MilestoneRow[];
}

/** OEM 생산 일정 보드(M7) — 행 모양은 선적 보드와 같다. 버튼 근거 = `allowed_actions`(EDIT_MILESTONES). */
export interface OemMilestoneBoard extends MilestoneBoard {
  po_id: number;
  allowed_actions: string[];
}

export interface ChangeRef {
  id: number;
  change_kind: string;
}

/** M2·M3·M8 응답(R-19) — no-op이면 change = null. 같은 Idempotency-Key 재요청 = 같은 change.id + 지금 시점 보드. */
export interface MilestoneWriteResult<B extends MilestoneBoard = MilestoneBoard> {
  board: B;
  change: ChangeRef | null;
}

export interface MilestoneNotice {
  comm_log_id: number;
  occurred_on: string;
  summary: string;
  partner_id: number | null;
  partner_name: string | null;
  actor_user_id: number;
  created_at: string;
}

export interface MilestoneChange {
  id: number;
  milestone_id: number;
  milestone_type: string;
  change_kind: string;
  old: MilestoneValue;
  new: MilestoneValue;
  reason: string | null;
  actor_user_id: number;
  actor_name: string | null;
  created_at: string;
  notices: MilestoneNotice[];
}

export interface CustomsRecord {
  id: number;
  shipment_id: number;
  declaration_kind: string;
  declaration_no: string;
  declared_on: string;
  /** null = 미수리. */
  accepted_on: string | null;
  customs_broker: { partner_id: number; name: string } | null;
  note: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CustomsSummary {
  live_count: number;
  pending_count: number;
  latest_accepted_on: string | null;
}

export interface ProfileMilestoneType {
  id: number;
  item_profile_id: number;
  milestone_type: string;
  created_at: string;
}

// ── 상수(서버 상수의 화면 사본 — 바뀌면 시험이 잡는다) ──

/** 롤오버 배지·'롤오버' 표기 대상(서버 ROLLOVER_TYPES — design-B B9). 다른 종류의 계획 변경은 '계획 변경'. */
export const ROLLOVER_TYPES: ReadonlySet<string> = new Set(["ETD", "ETA", "CARGO_CLOSING"]);
/** 품목군 세트에 담을 수 있는 선적 저장형 8종(업무 흐름 순 — 서버 SHIPMENT_BOARD_ORDER에서 파생 3종을 뺀 것). */
export const SET_MILESTONE_TYPES = [
  "DOC_CUTOFF",
  "CARGO_CLOSING",
  "PSI",
  "CUSTOMS_CLEARED",
  "ETD",
  "BL_ISSUED",
  "ETA",
  "IMPORT_TAX_DUE",
] as const;
/** 업무 날짜 범위(서버 BUSINESS_DATE_MIN~MAX — 휴일 연도 규약과 같다). 밖은 서버 422 INVALID_FIELD. */
export const DATE_MIN = "2000-01-01";
export const DATE_MAX = "2999-12-31";
export const DATETIME_MIN = "2000-01-01T00:00";
export const DATETIME_MAX = "2999-12-31T23:59";
/** 변경 1건당 통보 기록 상한(서버 NOTICE_LIMIT_PER_CHANGE) — 이 수에 닿으면 '통보 기록' 버튼을 끈다(21번째는 서버 422). */
export const NOTICE_LIMIT_PER_CHANGE = 20;
/** 통보 상대 거래처 유형(서버 NOTICE_PARTNER_TYPES) — 유형을 먼저 고르고 그 유형만 검색한다(불일치는 서버 422). */
export const NOTICE_PARTNER_TYPES = ["FORWARDER", "CUSTOMS_BROKER", "THREE_PL", "BUYER", "SUPPLIER", "OEM"] as const;
/** 통관 신고번호 규약(서버 `_DECLARATION_NO` — 대문자화 전 원문, 40자 이내). 서버가 대문자로 저장한다. */
export const DECLARATION_NO_PATTERN = /^[A-Za-z0-9][A-Za-z0-9/-]*$/;
export const DECLARATION_NO_MAX = 40;

// ── 오류 코드 ──

export const MILESTONE_REASON_REQUIRED_CODE = "SHIPMENTS.MILESTONE.REASON_REQUIRED";
export const MILESTONE_OWNER_NOT_ACTIVE_CODE = "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE";
export const NOTICE_LIMIT_REACHED_CODE = "SHIPMENTS.MILESTONE.NOTICE_LIMIT_REACHED";
export const DUPLICATE_TYPE_CODE = "SHIPMENTS.MILESTONE.DUPLICATE_TYPE";
export const CUSTOMS_RECORD_ALIVE_CODE = "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE";
export const ACTUAL_RECORDED_CODE = "SHIPMENTS.SHIPMENT.ACTUAL_RECORDED";
export const DECLARATION_DUPLICATE_CODE = "SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE";

/**
 * 마일스톤 쓰기의 '겹친 편집' — 낙관 잠금(VERSION_CONFLICT)과 최초 계획 동시 입력(409 DUPLICATE_TYPE — PR-4c 적대 검토 반영으로 OEM 경로에도
 * 생긴다)을 같은 탈출로('최신 내용 불러오기' = 보드 재조회)로 처리한다. 문구는 서버 message 그대로(하드코딩 0).
 */
export const needsBoardReload = (error: unknown): boolean =>
  error instanceof ApiError &&
  error.status === 409 &&
  (error.code === "COMMON.CONCURRENCY.VERSION_CONFLICT" || error.code === DUPLICATE_TYPE_CODE);

/** 선적 취소 409의 `detail` 목록(통관 = declaration_nos, 실적 = milestone_types). 다른 오류·모양이면 빈 목록. */
export function cancelBlockers(error: unknown): { customs: string[]; actuals: string[] } {
  if (!(error instanceof ApiError)) return { customs: [], actuals: [] };
  const strings = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  return {
    customs: error.code === CUSTOMS_RECORD_ALIVE_CODE ? strings(error.detail.declaration_nos) : [],
    actuals: error.code === ACTUAL_RECORDED_CODE ? strings(error.detail.milestone_types) : [],
  };
}

// ── 라벨(단일 표 — 모르는 값은 '기타') ──

const OTHER = "기타";

const MILESTONE_TYPE: Record<string, string> = {
  DOC_CUTOFF: "서류마감",
  CARGO_CLOSING: "Cargo Closing",
  PSI: "수출 전 검사",
  CUSTOMS_CLEARED: "신고수리",
  LOADING_DEADLINE: "적재기한",
  ETD: "ETD",
  BL_ISSUED: "B/L(AWB) 발행일",
  PRESENTATION_DEADLINE: "L/C 제시기한",
  ETA: "ETA",
  IMPORT_TAX_DUE: "수입 세금 납부기한",
  PAYMENT_DUE: "대금만기",
  RAW_MATERIAL_READY: "원료수급",
  FILLING: "충진",
  PACKING: "포장",
  OUTGOING_INSPECTION: "출하검사",
};

/** 파생 UNKNOWN·NOT_APPLICABLE 사유(design-D D6 표 + 서버 DueReason 전체). 모르는 코드는 null → '산정 불가(기타)'. */
const DERIVED_REASON: Record<string, string> = {
  INVOICE_NOT_ISSUED: "인보이스 미발행(인보이스 기능 이후 산정)",
  ANCHOR_PENDING: "기산일 미확정",
  LC_TERMS_NOT_REGISTERED: "L/C 조건 미등록",
  NOT_CLEARED: "신고수리 전",
  RECEIPT_NOT_RECORDED: "입고 미기록",
  TERMS_MISSING: "결제조건 없음",
  LC_TENOR_UNSUPPORTED: "지원하지 않는 L/C 지급 형태",
  LC_INPUT_MISSING: "L/C 네고·인수 정보 없음",
  BL_MISSING: "B/L 발행일 없음",
  EXPIRY_MISSING: "L/C 유효기일 없음",
  NO_BALANCE: "잔금 없음(전액 선수금)",
  DATE_OUT_OF_RANGE: "날짜 범위 밖(2000~2999년)",
  TZ_UNRESOLVED: "시간대 확인 불가",
};

const FULFILMENT: Record<string, string> = {
  MET: "기한 내 적재",
  MET_LATE: "기한 후 적재",
  OPEN: "진행 중",
  OVERDUE: "도과",
  UNKNOWN: "판정 불가",
};

const DECLARATION_KIND: Record<string, string> = { EXPORT: "수출신고", IMPORT: "수입신고" };

export const milestoneTypeLabel = (code: string): string => MILESTONE_TYPE[code] ?? OTHER;
export const fulfilmentLabel = (code: string): string => FULFILMENT[code] ?? OTHER;
export const declarationKindLabel = (code: string): string => DECLARATION_KIND[code] ?? OTHER;
export const derivedReasonLabel = (code: string | null): string | null => (code === null ? null : (DERIVED_REASON[code] ?? null));

/** 변경 종류 — PLAN_CHANGED는 ETD·ETA·Cargo Closing만 '롤오버'(B9), 나머지 종류는 '계획 변경'. */
export function changeKindLabel(changeKind: string, milestoneType: string): string {
  switch (changeKind) {
    case "PLAN_SET":
      return "계획 설정";
    case "PLAN_CHANGED":
      return ROLLOVER_TYPES.has(milestoneType) ? "롤오버" : "계획 변경";
    case "ACTUAL_RECORDED":
      return "실적 기록";
    case "ACTUAL_CORRECTED":
      return "실적 정정";
    default:
      return OTHER;
  }
}

/** 파생값 문구 — OK = 날짜, UNKNOWN = '산정 불가 — 사유'(빈칸·0일 대체 금지), NOT_APPLICABLE = '해당 없음 — 사유'. */
export function derivedText(derived: DerivedValue): string {
  const reason = derivedReasonLabel(derived.reason_code);
  switch (derived.status) {
    case "OK":
      return derived.value ?? "산정 불가(기타)";
    case "UNKNOWN":
      return reason === null ? "산정 불가(기타)" : `산정 불가 — ${reason}`;
    case "NOT_APPLICABLE":
      return reason === null ? "해당 없음" : `해당 없음 — ${reason}`;
    default:
      return "산정 불가(기타)";
  }
}

// ── 값 표시 ──

export const isInstant = (value: MilestoneValue): value is InstantValue =>
  typeof value === "object" && value !== null && typeof value.at_utc === "string";

// ── 배지 규칙(서버 값만 읽는다 — 판정 복제 0) ──

export interface Badge {
  tone: "warn" | "muted" | "alert";
  text: string;
  /** 이어서 할 일이 있는 배지(미등록 → 휴일 캘린더). */
  link?: string;
}

/**
 * 휴일 배지(ETA·도착국만 — R-09/R-28): HOLIDAY → 경고 + 휴일 이름 / UNVERIFIED → '휴일 캘린더 미등록 — 확인 불가' + 캘린더 링크 /
 * CLEAR → 배지 없음(신호가 묻히지 않게 — design-D D6 대안 (b) 기각). 모르는 값은 확인 불가 쪽으로(fail-visible).
 */
export function holidayBadge(row: Pick<MilestoneRow, "holiday" | "effective">): Badge | null {
  const holiday = row.holiday;
  if (holiday === null) return null;
  // 연도는 유효값 문자열의 앞 4자리(날짜형 ETA — 시각 객체 변환 없음).
  const year = row.effective !== null && /^\d{4}-/.test(row.effective.value) ? row.effective.value.slice(0, 4) : null;
  switch (holiday.flag) {
    case "CLEAR":
      return null;
    case "HOLIDAY":
      return { tone: "warn", text: `도착국 휴일: ${holiday.name ?? "이름 미등록"} (${holiday.country})` };
    case "UNVERIFIED":
      return {
        tone: "muted",
        text: `휴일 캘린더 미등록 — 확인 불가 (${holiday.country}${year === null ? "" : ` ${year}`})`,
        link: `/holidays?country=${encodeURIComponent(holiday.country)}${year === null ? "" : `&year=${year}`}`,
      };
    default:
      return { tone: "muted", text: `휴일 판정 확인 불가 (${holiday.country})` };
  }
}

/** 신고수리 부분 수리 배지(R-06) — PARTIAL이면 '일부 미수리 n건'(n = 서버 customs_pending_count). */
export function customsBadge(row: Pick<MilestoneRow, "customs_state" | "customs_pending_count">): Badge | null {
  if (row.customs_state !== "PARTIAL") return null;
  return { tone: "warn", text: `일부 미수리 ${row.customs_pending_count ?? "?"}건` };
}

/**
 * D-N 문구 — 서버 `days_left`·`is_overdue`만 읽는다. 실적이 있는 저장형 = '완료'(부분 수리는 완료가 아니다).
 * 판정 불가(`unknown_reason`) 행은 null(배지가 따로 말한다 — KST로 추정하지 않는다).
 * 대금만기처럼 `is_overdue`가 null인 행(입금 확인 전 — 도과 판정 안 함)은 '경과(도과 판정 없음)'로 그린다.
 */
export function dueText(row: MilestoneRow): string | null {
  if (row.unknown_reason !== null) return null;
  if (row.kind === "STORED" && row.actual !== null) return row.customs_state === "PARTIAL" ? null : "완료";
  const days = row.days_left;
  if (days === null) return null;
  if (row.is_overdue === true) return days < 0 ? `${-days}일 지남` : "기한 지남";
  if (days > 0) return `D-${days}`;
  if (days === 0) return "D-day";
  return row.is_overdue === null ? `${-days}일 경과(도과 판정 없음)` : "D-day";
}

// ── 요청 본문(서버 스키마 그대로 — extra=forbid) ──

/** 행이 있으면 그 version, 없으면 생략(서버 규칙 — 어긋나면 409). 대화상자를 연 순간의 행으로 고정한다(기준 version 점프 금지). */
const versionPart = (row: Pick<MilestoneRow, "milestone_id" | "version">): { version?: number } =>
  row.milestone_id !== null && row.version !== null ? { version: row.version } : {};

const reasonPart = (reason: string | null): { reason?: string } => (reason === null ? {} : { reason });

/** 계획(M2·M8) — 날짜형 `{planned_on}` / 시각형 `{planned_at, tz}` (+version·사유). */
export function planBody(
  row: Pick<MilestoneRow, "milestone_id" | "version" | "value_shape">,
  value: { on: string } | { at: string; tz: string },
  reason: string | null,
): Record<string, unknown> {
  const valuePart = "on" in value ? { planned_on: value.on } : { planned_at: value.at, tz: value.tz };
  return { ...valuePart, ...versionPart(row), ...reasonPart(reason) };
}

/** 실적(M3·M8) — 값 키는 늘 보낸다(지우기 = 명시적 null — tz는 보내지 않는다, 서버 형태 422). */
export function actualBody(
  row: Pick<MilestoneRow, "milestone_id" | "version" | "value_shape">,
  value: { on: string } | { at: string; tz: string } | null,
  reason: string | null,
): Record<string, unknown> {
  let valuePart: Record<string, unknown>;
  if (value === null) valuePart = row.value_shape === "DATETIME" ? { actual_at: null } : { actual_on: null };
  else valuePart = "on" in value ? { actual_on: value.on } : { actual_at: value.at, tz: value.tz };
  return { ...valuePart, ...versionPart(row), ...reasonPart(reason) };
}

// ── 쿼리 키 ──

export const milestoneChangesKey = (owner: "SHIPMENT" | "PURCHASE_ORDER", id: number) =>
  ["milestone-changes", owner, id] as const;
/** 선적 키 접두(["shipments"]) 아래 — 선적 무효화가 함께 건다. */
export const customsRecordsKey = (shipmentId: number) => ["shipments", "customs", shipmentId] as const;
/** 발주 키 접두(["purchase-orders"]) 아래 — 발주 쓰기(취소 등) 뒤 무효화가 보드·버튼도 새로 받는다. */
export const oemBoardKey = (poId: number) => ["purchase-orders", "oem-milestones", poId] as const;
export const profileMilestoneTypesKey = (profileId: number) => ["item-profiles", profileId, "milestone-types"] as const;

/** 같은 종류의 파생 행이 바뀐 것만 안내문으로(문자열 비교만 — 산술 0). design-D D6 '대금만기가 …로 다시 계산됐습니다'. */
export function derivedChanges(before: MilestoneBoard | undefined, after: MilestoneBoard): string[] {
  if (before === undefined) return [];
  const old = new Map(before.rows.filter((row) => row.kind === "DERIVED").map((row) => [row.milestone_type, row]));
  const notes: string[] = [];
  for (const row of after.rows) {
    if (row.kind !== "DERIVED" || !row.applicable || row.derived === null) continue;
    const previous = old.get(row.milestone_type)?.derived ?? null;
    const now = derivedText(row.derived);
    if (previous === null || derivedText(previous) !== now) {
      notes.push(
        row.derived.status === "OK"
          ? `${milestoneTypeLabel(row.milestone_type)}가 ${now}로 다시 계산됐습니다.`
          : `${milestoneTypeLabel(row.milestone_type)}: ${now}`,
      );
    }
  }
  return notes;
}
