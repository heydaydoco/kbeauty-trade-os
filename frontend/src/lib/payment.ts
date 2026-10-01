// 입금 원장(PI 선수금) 응답 타입·키·오류 문구 (S3-1 PR-10b) — backend/app/modules/payments/schemas.py 그대로.
// 금액은 서버가 주는 문자열(*_text)을 그대로 표시한다. 프런트 산술 0.

import { ApiError } from "./api";

export interface PaymentRow {
  id: number;
  pi_id: number;
  partner_id: number;
  kind: "RECEIPT" | "REVERSAL";
  received_amount: number;
  received_amount_text: string;
  received_currency: string;
  received_on: string;
  reference: string;
  reverses_payment_id: number | null;
  reversed_by_payment_id: number | null;
  reason: string | null;
  recorded_by_id: number;
  created_at: string;
}

export interface PaymentSummary {
  pi_id: number;
  pi_status: string;
  currency: string;
  payment_type: string | null;
  due_amount: number;
  due_text: string | null;
  net_received_amount: number;
  net_received_text: string | null;
  remaining_amount: number;
  remaining_text: string | null;
}

export interface PaymentWarning {
  code: "CONFIRMED_SO_ADVANCE_UNMET";
  sales_order_ids: number[];
}

export interface PaymentPageData {
  items: PaymentRow[];
  total: number;
  page: number;
  size: number;
  summary: PaymentSummary;
}

export interface PaymentWriteResult {
  payment: PaymentRow;
  summary: PaymentSummary;
  warnings: PaymentWarning[];
}

export const piPaymentsKey = (piId: number) => ["pi-payments", piId] as const;

/** 입금·역기록을 받을 수 있는 PI 상태(서버 규칙: 그 밖은 409 PI_NOT_OPEN). */
export const PI_PAYMENT_OPEN_STATUSES: readonly string[] = ["ISSUED", "PARTIALLY_PAID", "PAID"];

const VALIDATION_CODE = "COMMON.VALIDATION.INVALID_FIELD";

export const PAYMENT_ERROR_TEXT: Record<string, string> = {
  "PAYMENTS.PAYMENT.CURRENCY_MISMATCH":
    "입금 통화가 PI 통화와 다릅니다. PI와 같은 통화로 입금액을 다시 입력해 주세요(환산 입금은 지원하지 않습니다).",
  "PAYMENTS.PAYMENT.EXCEEDS_DUE":
    "입금액이 남은 선수금을 넘습니다. 남은 선수금 이하로 입력해 주세요(선수금을 넘는 금액은 잔금 입금으로 따로 기록합니다).",
  "PAYMENTS.PAYMENT.PI_NOT_ADVANCE":
    "선수금 T/T가 아닌 PI에는 입금을 기록할 수 없습니다(잔금 입금은 후속 기능입니다).",
  "PAYMENTS.PAYMENT.ALREADY_REVERSED":
    "이미 역기록된 입금입니다. 입금 목록을 새로 고쳐 확인해 주세요(정정이 필요하면 새 입금을 기록해 주세요).",
  "PAYMENTS.PAYMENT.NOT_REVERSIBLE":
    "역기록 행은 다시 역기록할 수 없습니다. 정정이 필요하면 새 입금을 기록해 주세요.",
  "TRADE_DOCS.PAYMENT.PI_NOT_OPEN":
    "취소되었거나 만료된 PI에는 입금을 반영할 수 없습니다. 새 PI를 발행한 뒤 다시 시도해 주세요.",
  [VALIDATION_CODE]:
    "입력값이 올바르지 않습니다. 금액(통화 자릿수 이내)·입금일(오늘 이전)·입금 확인 근거·사유(2자 이상)를 확인해 주세요.",
};

const HANGUL = /[가-힣]/;

export const REVERSAL_VALIDATION_TEXT =
  "역기록 사유를 확인해 주세요(2~300자, 줄바꿈·탭 같은 제어문자는 쓸 수 없습니다).";

/** 영문 내부 코드·detail은 노출하지 않는다 — 코드 사전 → 상태별 일반 문구 → 서버 한국어 message → 기본 문구 순. */
export function paymentErrorMessage(
  error: unknown,
  kind: "receipt" | "reversal" = "receipt",
  fallback = "요청을 처리하지 못했습니다.",
): string {
  if (!(error instanceof ApiError)) return fallback;
  const mapped = PAYMENT_ERROR_TEXT[error.code];
  if (mapped !== undefined) {
    return kind === "reversal" && error.code === VALIDATION_CODE ? REVERSAL_VALIDATION_TEXT : mapped;
  }
  if (error.status === 403) return "입금 기록·역기록 권한이 없습니다(무역·관리자만 가능합니다).";
  if (error.status === 422) return kind === "reversal" ? REVERSAL_VALIDATION_TEXT : (PAYMENT_ERROR_TEXT[VALIDATION_CODE] as string);
  if (error.status === 409) return "처리 중 충돌이 발생했습니다. 새로고침 후 다시 시도하세요.";
  return HANGUL.test(error.message) ? error.message : fallback;
}

/** 멱등 키 — crypto.randomUUID가 없거나 던지는 환경(비보안 컨텍스트 등)에서도 키를 만든다. */
export function newPaymentKey(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch {
    // 폴백으로
  }
  const rand = () => Math.random().toString(16).slice(2, 10).padEnd(8, "0");
  return `pay-${Date.now().toString(16)}-${rand()}-${rand()}`;
}

// eslint-disable-next-line no-control-regex
const REASON_CONTROL = /[\u0000-\u001f\u007f]/;

/** 역기록 사유 사전 검증(서버가 최종) — 제어문자 거부, 길이는 코드포인트 기준. 문제 없으면 null. */
export function validateReversalReason(reason: string): string | null {
  const trimmed = reason.trim();
  if (REASON_CONTROL.test(reason)) return "사유에 줄바꿈·탭 같은 제어문자는 쓸 수 없습니다.";
  const length = [...trimmed].length;
  if (length < 2) return "사유는 2자 이상 입력해 주세요.";
  if (length > 300) return "사유는 300자 이내로 입력해 주세요.";
  return null;
}

/** 409 충돌(이미 역기록·동시 처리 등) — 재조회 안내 버튼을 보인다. */
export const isRecoverableConflict = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 409;

export const isForbidden = (error: unknown): boolean => error instanceof ApiError && error.status === 403;

// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

export interface ReceiptInput {
  amount: string;
  receivedOn: string;
  reference: string;
}

export type ReceiptFieldErrors = Partial<Record<keyof ReceiptInput, string>>;

/** 입력 형식 검사(서버가 최종) — 금액은 통화 자릿수 이내의 양수 십진 문자열. 산술 없이 문자열 규칙만 쓴다. */
export function receiptFieldErrors(input: ReceiptInput, minorUnits: number, today: string): ReceiptFieldErrors {
  const errors: ReceiptFieldErrors = {};
  const amount = input.amount.trim();
  const pattern = minorUnits === 0 ? /^\d+$/ : new RegExp(`^\\d+(\\.\\d{1,${minorUnits}})?$`);
  if (!pattern.test(amount)) {
    errors.amount =
      minorUnits === 0
        ? "금액은 소수점 없는 숫자로 입력해 주세요."
        : `금액은 소수점 ${minorUnits}자리 이내의 숫자로 입력해 주세요(예: 12.34).`;
  } else if (/^0+(\.0*)?$/.test(amount)) {
    errors.amount = "금액은 0보다 커야 합니다.";
  }
  if (input.receivedOn === "") errors.receivedOn = "입금일을 입력해 주세요.";
  else if (input.receivedOn > today) errors.receivedOn = "입금일은 오늘(KST) 이후일 수 없습니다.";
  const reference = input.reference.trim();
  if (reference === "") errors.reference = "입금 확인 근거(은행 거래 참조·확인 메모)는 필수입니다.";
  else if ([...reference].length > 100) errors.reference = "입금 확인 근거는 100자 이내로 입력해 주세요.";
  else if (CONTROL.test(input.reference)) errors.reference = "입금 확인 근거에 줄바꿈·탭 같은 제어문자는 쓸 수 없습니다.";
  return errors;
}

export function validateReceipt(input: ReceiptInput, minorUnits: number, today: string): string[] {
  return Object.values(receiptFieldErrors(input, minorUnits, today));
}
