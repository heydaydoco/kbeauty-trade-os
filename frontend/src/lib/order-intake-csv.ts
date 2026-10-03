// 오더 인테이크 CSV 업로드 — 응답 타입·오류 분류·한국어 문구 (S3-1 PR-14b — 서버 계약: PROGRESS 'S3-1 PR-14a' PR-14b 프런트 인계 계약 2차 갱신본).
//
// ★ 프런트는 행을 다시 검증하지 않는다 — 파일 사전 검사는 확장자·크기·빈 파일뿐이고(서버가 최종 판정), 리포트는 서버 값 그대로 표시한다.
// ★ 멱등 키 규칙: 업로드 시도(사용자 클릭)마다 새 키. 같은 키는 ① 응답을 받지 못한 경우(네트워크 오류·504) '결과 다시 받기'
//   ② 409 LOCK_BUSY '잠시 후 같은 키로 재시도'에서만 다시 쓴다. 키를 파일 내용에서 파생하지 않는다(같은 파일=같은 키가 되면 정정 재업로드가 막힌다).
// ★ 영문 code는 화면에 노출하지 않는다 — 분기는 code로, 표시는 message_ko·한국어 라벨로.

import { ApiError } from "./api";
import { errorMessage } from "./api-errors";
import { CODE, intakeErrorMessage } from "./order-intake";

export const CSV_IMPORT_PATH = "/v1/order-intakes/import-csv";
export const CSV_TEMPLATE_PATH = "/v1/order-intakes/template.csv";
export const CSV_TEMPLATE_FALLBACK_NAME = "오더인테이크_표준양식.csv";

/** 서버 `imports.MAX_UPLOAD_BYTES`(20MiB)와 같은 값 — 사전 검사용일 뿐, 최종 판정은 서버(413). */
export const CSV_MAX_BYTES = 20 * 1024 * 1024;

export const newUploadKey = (): string => crypto.randomUUID();

// ── 201 응답 (backend order_intake/schemas.py CsvImportResult 그대로) ──────────────

export interface CsvImportIntake {
  id: number;
  version: number;
  buyer_partner_id: number;
  buyer_name: string | null;
  buyer_po_no: string;
  currency: string;
  dest_market_code: string;
  line_count: number;
  unmapped_line_count: number;
  total_amount: number;
  total_text: string | null;
  first_row_no: number;
}

export interface CsvImportResult {
  original_filename: string;
  source_sha256: string;
  row_count: number;
  group_count: number;
  line_count: number;
  unmapped_line_count: number;
  intakes: CsvImportIntake[];
}

// ── 파일 사전 검사 ───────────────────────────────────────────────────────────

/** 문제가 없으면 null. 서버가 최종 판정한다(여기서 통과해도 서버가 거부할 수 있다). */
export function precheckCsvFile(file: { name: string; size: number }): string | null {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".xlsx") || lower.endsWith(".xls")) {
    return "엑셀 파일(.xlsx·.xls)은 올릴 수 없습니다. 엑셀에서 '다른 이름으로 저장 > CSV UTF-8(쉼표로 분리)'로 저장한 뒤 그 파일을 고르세요.";
  }
  if (!lower.endsWith(".csv")) {
    return "CSV 파일(.csv)만 올릴 수 있습니다. 표준 양식을 내려받아 작성한 뒤 'CSV UTF-8(쉼표로 분리)'로 저장해 주세요.";
  }
  if (file.size === 0) {
    return "빈 파일입니다. 내용을 채운 뒤 다시 골라 주세요.";
  }
  if (file.size > CSV_MAX_BYTES) {
    return "파일이 20MB를 넘습니다. 파일을 나누어 올려 주세요.";
  }
  return null;
}

// ── 오류 리포트 ──────────────────────────────────────────────────────────────

export interface CsvRowError {
  /** 엑셀 행번호(헤더=1). 파일 수준 오류면 null. */
  row_no: number | null;
  /** 한글 헤더명 또는 null. */
  column: string | null;
  code: string;
  message_ko: string;
}

export interface CsvReport {
  errors: CsvRowError[];
  totalErrors: number;
  omittedErrors: number;
  /** 서버가 준 순서(코드 사전순) 그대로. */
  countsByCode: Array<[code: string, count: number]>;
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const intOr = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : fallback);

/** `detail.errors` 키가 있을 때만 리포트다(계약: 먼저 키 유무로 분기). 항목 모양이 깨지면 그 항목만 건너뛴다. */
export function parseCsvReport(detail: Record<string, unknown> | undefined): CsvReport | null {
  if (!detail || !("errors" in detail) || !Array.isArray(detail.errors)) return null;
  const errors: CsvRowError[] = [];
  for (const item of detail.errors as unknown[]) {
    if (!isRecord(item) || typeof item.code !== "string" || typeof item.message_ko !== "string") continue;
    errors.push({
      row_no: typeof item.row_no === "number" ? item.row_no : null,
      column: typeof item.column === "string" ? item.column : null,
      code: item.code,
      message_ko: item.message_ko,
    });
  }
  const totalErrors = intOr(detail.total_errors, errors.length);
  const omittedErrors = intOr(detail.omitted_errors, Math.max(0, totalErrors - errors.length));
  const countsByCode: Array<[string, number]> = isRecord(detail.counts_by_code)
    ? Object.entries(detail.counts_by_code).filter((e): e is [string, number] => typeof e[1] === "number")
    : [];
  return { errors, totalErrors, omittedErrors, countsByCode };
}

const ROW_CODE_LABEL: Record<string, string> = {
  REQUIRED: "필수 값 없음",
  TOO_LONG: "값이 너무 김",
  INVISIBLE_CHAR: "보이지 않는 문자",
  NOT_NFC: "한글 조합 형식 오류",
  EXCEL_ERROR: "엑셀 오류 값",
  ENCODING_LOSS: "글자 깨짐(인코딩)",
  EXCEL_EXPONENT: "지수 표기로 바뀐 코드",
  INVALID_FORMAT: "형식 오류",
  INVALID_DATE: "날짜 오류",
  OUT_OF_RANGE: "허용 범위 밖",
  UNKNOWN_CURRENCY: "알 수 없는 통화",
  GROUP_HEADER_MISMATCH: "같은 PO 안 헤더 값 불일치",
  DUPLICATE_ITEM: "같은 PO 안 같은 품번",
  DUPLICATE_SKU: "같은 PO 안 같은 SKU",
  GROUP_LINE_LIMIT: "PO당 줄 수 초과",
  ROW_STRUCTURE: "행 구조 오류",
  CSV_SYNTAX: "CSV 문법 오류",
  BUYER_NOT_REGISTERED: "등록되지 않은 바이어",
  NOT_A_BUYER: "바이어가 아닌 거래처",
  MARKET_NOT_REGISTERED: "등록되지 않은 시장",
  DUPLICATE_BUYER_PO: "이미 등록된 PO",
};

/** 행 오류 code → 한국어 라벨(영문 code 비노출). 모르는 code는 '기타'. */
export const rowCodeLabel = (code: string): string => ROW_CODE_LABEL[code] ?? "기타";

// ── 업로드 결과 분류 ──────────────────────────────────────────────────────────

export const CSV_CODE = {
  FILE_DUPLICATE: "ORDER_INTAKE.FILE.DUPLICATE",
  INVALID_ROWS: "ORDER_INTAKE.FILE.INVALID_ROWS",
  TOO_MANY_GROUPS: "ORDER_INTAKE.FILE.TOO_MANY_GROUPS",
  UNSUPPORTED_FORMAT: "ORDER_INTAKE.FILE.UNSUPPORTED_FORMAT",
  HEADER_MISMATCH: "IMPORTS.FILE.HEADER_MISMATCH",
  ENCODING_INVALID: "IMPORTS.FILE.ENCODING_INVALID",
  DUPLICATE_PO: CODE.DUPLICATE_PO,
  LOCK_BUSY: CODE.LOCK_BUSY,
  KEY_CONFLICT: CODE.KEY_CONFLICT,
} as const;

export interface HeaderDifference {
  columnNo: number;
  expected: string | null;
  actual: string | null;
}

export type UploadOutcome =
  /** 행 오류 리포트(422 INVALID_ROWS 또는 중복 PO만일 때 409 DUPLICATE_BUYER_PO). */
  | { kind: "report"; message: string; duplicateOnly: boolean; report: CsvReport }
  /** 13a 모양 409 DUPLICATE_BUYER_PO(착지 순간 경합 — 다시 올리면 리포트로 수렴). */
  | { kind: "duplicate-po"; message: string; intakeId: number | null }
  /** 409 FILE.DUPLICATE — 같은 파일이 검토 대기 중. */
  | { kind: "file-duplicate"; message: string; intakeIds: number[] }
  /** 409 LOCK_BUSY — 같은 파일을 다른 업로드가 처리 중. 같은 키로 재시도. */
  | { kind: "lock-busy"; message: string }
  /** 응답을 받지 못함(네트워크 오류·504) — 같은 키·같은 파일로 결과 다시 받기. */
  | { kind: "response-lost"; message: string }
  /** 파일 수준 오류(헤더·인코딩·PO 수·형식·크기·행 수 등) — 리포트 없이 안내만. */
  | { kind: "file"; message: string; notes: string[]; differences: HeaderDifference[] }
  | { kind: "forbidden"; message: string }
  | { kind: "other"; message: string };

/** 응답을 받지 못한 경우만 — 서버 처리 여부를 모르므로 같은 키로 결과를 다시 받는다(그 밖의 오류는 서버가 결론을 준 것이다). */
export function isResponseLost(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status === 504);
}

function headerDifferences(detail: Record<string, unknown>): HeaderDifference[] {
  if (!Array.isArray(detail.differences)) return [];
  const out: HeaderDifference[] = [];
  for (const item of detail.differences as unknown[]) {
    if (!isRecord(item) || typeof item.column_no !== "number") continue;
    out.push({
      columnNo: item.column_no,
      expected: typeof item.expected === "string" ? item.expected : null,
      actual: typeof item.actual === "string" ? item.actual : null,
    });
  }
  return out;
}

const FILE_LEVEL_CODES = new Set<string>([
  CSV_CODE.HEADER_MISMATCH,
  CSV_CODE.ENCODING_INVALID,
  CSV_CODE.TOO_MANY_GROUPS,
  CSV_CODE.UNSUPPORTED_FORMAT,
  "IMPORTS.FILE.TYPE_NOT_ALLOWED",
  "IMPORTS.FILE.EMPTY",
  "IMPORTS.FILE.TOO_LARGE",
  CODE.VALIDATION,
]);

export function classifyUploadError(error: unknown): UploadOutcome {
  if (isResponseLost(error)) {
    return {
      kind: "response-lost",
      message:
        "서버 응답을 받지 못했습니다. 업로드가 처리되었을 수도 있습니다 — '결과 다시 받기'를 누르면 같은 파일을 같은 요청으로 다시 보내 결과를 확인합니다(두 번 등록되지 않습니다).",
    };
  }
  if (!(error instanceof ApiError)) return { kind: "other", message: "업로드를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요." };
  const detail = error.detail ?? {};

  // ★ 계약: 먼저 detail.errors 키 유무로 분기한다(같은 409 DUPLICATE_BUYER_PO도 두 모양이 있다).
  const report = parseCsvReport(detail);
  if (report !== null) {
    return { kind: "report", message: error.message, duplicateOnly: error.code === CSV_CODE.DUPLICATE_PO, report };
  }
  if (error.code === CSV_CODE.DUPLICATE_PO) {
    const intakeId = typeof detail.intake_id === "number" ? detail.intake_id : null;
    return {
      kind: "duplicate-po",
      message: `${intakeErrorMessage(error, "create")} 같은 파일을 다시 올리면 어느 행의 PO가 겹치는지 행별 리포트로 확인할 수 있습니다.`,
      intakeId,
    };
  }
  if (error.code === CSV_CODE.FILE_DUPLICATE) {
    const ids = Array.isArray(detail.intake_ids) ? (detail.intake_ids as unknown[]).filter((n): n is number => typeof n === "number") : [];
    return {
      kind: "file-duplicate",
      message:
        "같은 파일이 이미 검토 대기 중인 오더 인테이크로 올라와 있어 이번 업로드는 등록하지 않았습니다. 기존 인테이크를 검토해 확정하거나 거부한 뒤, 필요하면 다시 올려 주세요.",
      intakeIds: ids,
    };
  }
  if (error.code === CSV_CODE.LOCK_BUSY) {
    return {
      kind: "lock-busy",
      message:
        "같은 파일을 다른 업로드가 처리하고 있습니다. 잠시 후 '같은 요청으로 다시 시도'를 눌러 주세요(같은 요청이라 두 번 등록되지 않습니다).",
    };
  }
  if (error.code === CSV_CODE.KEY_CONFLICT) {
    return {
      kind: "other",
      message: "같은 요청 키로 다른 파일이 이미 처리되었습니다. 파일을 확인한 뒤 '업로드'를 다시 눌러 새 요청으로 올려 주세요.",
    };
  }
  if (error.status === 403) {
    return { kind: "forbidden", message: "CSV 업로드는 무역·관리자만 할 수 있습니다." };
  }
  if (FILE_LEVEL_CODES.has(error.code) || error.status === 413) {
    const notes: string[] = [];
    if (typeof detail.header === "string") notes.push(detail.header);
    if (typeof detail.file === "string") notes.push(detail.file);
    if (error.code === CSV_CODE.TOO_MANY_GROUPS && typeof detail.groups === "number" && typeof detail.max_groups === "number") {
      notes.push(`이 파일의 바이어 PO는 ${detail.groups}건이고, 한 파일에 ${detail.max_groups}건까지 올릴 수 있습니다.`);
    }
    return { kind: "file", message: error.message, notes, differences: headerDifferences(detail) };
  }
  return { kind: "other", message: errorMessage(error, "업로드를 처리하지 못했습니다.") };
}

/** 양식 내려받기 오류 문구 — 403은 래치 대상(호출부가 버튼을 숨긴다). */
export function templateErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return "표준 양식은 무역·관리자만 내려받을 수 있습니다.";
  return errorMessage(error, "표준 양식을 내려받지 못했습니다. 잠시 후 다시 시도해 주세요.");
}
