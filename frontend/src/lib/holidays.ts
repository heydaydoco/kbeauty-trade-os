// 휴일 캘린더 — 서버 계약 타입·경로·오류 매핑 (S3-2 PR-2b / design-D §D2-3 H1~H5 / 서버 계약: PROGRESS 'S3-2 PR-2a' PR-2b 인계 계약).
//
// ★ 날짜(`holiday_on`·`verified_on`)는 현지 달력일 문자열 'YYYY-MM-DD'다 — Date 객체로 바꾸지 않는다(UTC 자정 해석으로 하루 밀림,
//   PR-16 결함 ① 계보). 비교·연도 판정도 문자열로만 한다. `updated_at`만 UTC ISO 시각이라 `toKstDisplay`로 보인다.
// ★ 미선언(`calendar === null`)은 '확인 불가'(판정 UNVERIFIED)이고, 선언 + 0건('휴일 없음 확인')과 다르다.

import { ApiError, apiFetch } from "./api";
import type { Page } from "./paging";

export const HOLIDAYS_QUERY_KEY = ["holidays"] as const;

export interface CalendarYear {
  id: number;
  country_code: string;
  year: number;
  source_url: string;
  /** 현지 달력일 'YYYY-MM-DD' — Date로 해석 금지. */
  verified_on: string;
  holiday_count: number;
  version: number;
  updated_by_name: string | null;
  /** UTC ISO 시각 — KST로 표시. */
  updated_at: string;
}

export interface Holiday {
  id: number;
  /** 현지 달력일 'YYYY-MM-DD' — Date로 해석 금지. */
  holiday_on: string;
  name: string;
}

/** H2 — 봉투 밖 `calendar`가 null이면 미선언(빈 목록과 다르다). */
export interface HolidayPage extends Page<Holiday> {
  calendar: CalendarYear | null;
}

export interface CsvPreviewRow {
  holiday_on: string;
  name: string;
}

export interface CsvPreviewProblem {
  /** 데이터 행 번호(머리글 다음 줄이 1). */
  row: number;
  field: string;
  message: string;
}

export interface CsvPreview {
  rows: CsvPreviewRow[];
  problems: CsvPreviewProblem[];
}

export interface ReplaceBody {
  source_url: string;
  verified_on: string;
  holidays: Array<{ holiday_on: string; name: string }>;
  version: number | null;
}

/** 서버 `MAX_HOLIDAYS_PER_YEAR`·이름 상한과 같은 값(서버가 정본 — 화면은 입력 편의). */
export const MAX_HOLIDAYS_PER_YEAR = 366;
export const HOLIDAY_NAME_MAX = 100;
export const SOURCE_URL_MAX = 500;
/** 서버 CSV 상한(256KB) — 넘는 파일은 보내기 전에 막는다(서버가 정본). */
export const CSV_MAX_BYTES = 256 * 1024;
/** 편집용 전체 조회 쪽 크기 — 서버 최대(200). 366건이라도 2쪽이다. */
const FULL_FETCH_SIZE = 200;

const COUNTRY_PATTERN = /^[A-Z]{2}$/;
const YEAR_PATTERN = /^[0-9]{4}$/;

/** ISO alpha-2 대문자 2자(서버 `is_country_code`와 같은 형식 — 서버가 정본). */
export const isCountryCode = (value: string): boolean => COUNTRY_PATTERN.test(value);

/** 선언 가능 연도 2000~2999(서버 `is_declarable_year`와 같은 범위). */
export function isDeclarableYear(value: string): boolean {
  if (!YEAR_PATTERN.test(value)) return false;
  const year = Number(value);
  return year >= 2000 && year <= 2999;
}

export const holidaysListPath = (country: string, year: string): string =>
  `/v1/holidays?country=${encodeURIComponent(country)}&year=${encodeURIComponent(year)}`;

export function calendarsPath(country: string, year: string): string {
  const params = new URLSearchParams();
  if (country) params.set("country", country);
  if (year) params.set("year", year);
  const query = params.toString();
  return query ? `/v1/holidays/calendars?${query}` : "/v1/holidays/calendars";
}

export const replacePath = (country: string, year: string): string =>
  `/v1/holidays/${encodeURIComponent(country)}/${encodeURIComponent(year)}`;

export const exportPath = (country: string, year: string): string => `${replacePath(country, year)}/export.csv`;

export const previewPath = (country: string, year: string): string => `${replacePath(country, year)}/import-csv/preview`;

export const exportFallbackName = (country: string, year: string): string => `휴일_${country}_${year}.csv`;

/**
 * 편집용 전체 집합 — H3은 그 국가·연도의 **전체**를 교체하므로 화면 1쪽(50건)만으로 편집본을 만들면 나머지가 조용히 지워진다.
 * 서버 최대 쪽(200)으로 끝까지 받고, 받은 건수가 total과 다르거나 쪽 사이에 선언 version이 바뀌면 거부한다(조용한 잘림·섞인 판 금지).
 */
export async function fetchFullCalendar(
  country: string,
  year: string,
): Promise<{ calendar: CalendarYear | null; holidays: Holiday[] }> {
  const holidays: Holiday[] = [];
  let calendar: CalendarYear | null = null;
  for (let page = 1; page <= Math.ceil(MAX_HOLIDAYS_PER_YEAR / FULL_FETCH_SIZE) + 1; page += 1) {
    const data = await apiFetch<HolidayPage>(`${holidaysListPath(country, year)}&page=${page}&size=${FULL_FETCH_SIZE}`);
    if (page === 1) calendar = data.calendar;
    else if ((data.calendar?.version ?? null) !== (calendar?.version ?? null)) throw new FullFetchMismatch();
    holidays.push(...data.items);
    // 마지막 쪽(덜 찬 쪽) 또는 total에 닿음 — 여기서 건수가 total과 다르면 섞인 판이다.
    if (holidays.length >= data.total || data.items.length < FULL_FETCH_SIZE) {
      if (holidays.length !== data.total) throw new FullFetchMismatch();
      return { calendar, holidays };
    }
  }
  throw new FullFetchMismatch();
}

/** 전체 조회 중 판이 바뀌었거나 건수가 맞지 않음 — 편집을 시작하지 않는다. */
export class FullFetchMismatch extends Error {
  constructor() {
    super("휴일 목록을 읽는 도중 다른 곳에서 바뀌었습니다. 다시 열어 주세요.");
    this.name = "FullFetchMismatch";
  }
}

export const CONFLICT_CODES = new Set(["COMMON.CONCURRENCY.VERSION_CONFLICT", "HOLIDAYS.CALENDAR.YEAR_DUPLICATE"]);

/** 409(낡은 version·동시 최초 선언) — '최신 내용 불러오기'로 다시 시작해야 한다. */
export const isReplaceConflict = (error: unknown): boolean => error instanceof ApiError && CONFLICT_CODES.has(error.code);

export const REPLACE_CONFLICT_GUIDE =
  "다른 곳에서 이 국가·연도의 휴일 캘린더가 먼저 저장되었습니다. '최신 내용 불러오기'로 다시 연 뒤 편집해 주세요. 입력하던 내용은 사라집니다.";

/** H3 오류를 칸 옆 표시용으로 나눈 결과. 행 오류는 보낸 본문의 행 순번(0부터) 기준이다. */
export interface ReplaceErrorView {
  /** 대화상자 상단 요약(서버 message 또는 409 안내). */
  summary: string;
  sourceUrl: string | null;
  verifiedOn: string | null;
  /** 행 순번 → 날짜 칸 문구. */
  rowDate: Record<number, string>;
  /** 행 순번 → 이름 칸 문구. */
  rowName: Record<number, string>;
  conflict: boolean;
}

const asStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

/**
 * 서버 오류 → 칸별 문구. `sent`는 실제로 보낸 holidays(순서 그대로) — detail의 날짜를 행으로 되짚는다.
 * - SOURCE_REQUIRED: detail.source_url·detail.verified_on
 * - YEAR_MISMATCH·DUPLICATE_DATE: detail.holiday_on = [날짜…] → 그 날짜의 행 날짜 칸
 * - INVALID_FIELD(서비스): detail.name = {날짜: 문구} → 그 날짜의 행 이름 칸
 * - INVALID_FIELD(요청 검증): detail.항목 = [{위치: "holidays.3.name", 사유}] → 3번 행
 */
export function mapReplaceError(error: unknown, sent: Array<{ holiday_on: string }>): ReplaceErrorView {
  const view: ReplaceErrorView = {
    summary: "저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    sourceUrl: null,
    verifiedOn: null,
    rowDate: {},
    rowName: {},
    conflict: false,
  };
  if (!(error instanceof ApiError)) return view;
  if (CONFLICT_CODES.has(error.code)) {
    return { ...view, summary: REPLACE_CONFLICT_GUIDE, conflict: true };
  }
  view.summary = error.message;
  const detail = error.detail ?? {};
  const rowsOf = (day: string): number[] =>
    sent.flatMap((row, index) => (row.holiday_on === day ? [index] : []));

  if (typeof detail.source_url === "string") view.sourceUrl = detail.source_url;
  if (typeof detail.verified_on === "string") view.verifiedOn = detail.verified_on;

  if (error.code === "HOLIDAYS.CALENDAR.YEAR_MISMATCH" || error.code === "HOLIDAYS.CALENDAR.DUPLICATE_DATE") {
    const note = error.code === "HOLIDAYS.CALENDAR.YEAR_MISMATCH" ? "이 연도의 날짜가 아닙니다." : "같은 날짜가 두 번 있습니다.";
    for (const day of asStringList(detail.holiday_on)) for (const index of rowsOf(day)) view.rowDate[index] = note;
  }
  const names = detail.name;
  if (names !== null && typeof names === "object" && !Array.isArray(names)) {
    for (const [day, message] of Object.entries(names as Record<string, unknown>)) {
      if (typeof message !== "string") continue;
      for (const index of rowsOf(day)) view.rowName[index] = message;
    }
  }
  const items = detail["항목"];
  if (Array.isArray(items)) {
    for (const item of items as Array<Record<string, unknown>>) {
      const where = typeof item["위치"] === "string" ? item["위치"] : "";
      const why = typeof item["사유"] === "string" ? item["사유"] : "값을 확인해 주세요.";
      const match = /^holidays\.(\d+)\.(holiday_on|name)$/.exec(where);
      if (match) {
        const index = Number(match[1]);
        if (match[2] === "holiday_on") view.rowDate[index] = why;
        else view.rowName[index] = why;
      } else if (where === "source_url") view.sourceUrl = why;
      else if (where === "verified_on") view.verifiedOn = why;
    }
  }
  return view;
}

/** 편집본 사전 검사(입력 편의 — 서버가 정본). 행 순번 기준 문구와 요약을 돌려준다. 문제 없으면 null. */
export function precheckRows(
  year: string,
  rows: Array<{ holiday_on: string; name: string }>,
): { rowDate: Record<number, string>; rowName: Record<number, string> } | null {
  const rowDate: Record<number, string> = {};
  const rowName: Record<number, string> = {};
  const seen = new Map<string, number>();
  rows.forEach((row, index) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(row.holiday_on)) rowDate[index] = "날짜를 입력해 주세요.";
    else if (!row.holiday_on.startsWith(`${year}-`)) rowDate[index] = `${year}년 날짜만 넣을 수 있습니다.`;
    else if (seen.has(row.holiday_on)) rowDate[index] = "같은 날짜가 두 번 있습니다.";
    else seen.set(row.holiday_on, index);
    if (row.name.trim() === "") rowName[index] = "휴일 이름을 입력해 주세요.";
    else if ([...row.name.trim()].length > HOLIDAY_NAME_MAX) rowName[index] = `휴일 이름은 ${HOLIDAY_NAME_MAX}자 이하여야 합니다.`;
  });
  return Object.keys(rowDate).length + Object.keys(rowName).length > 0 ? { rowDate, rowName } : null;
}

/** CSV 파일 사전 검사(확장자·빈 파일·크기) — 내용 검증은 서버 미리보기가 한다. */
export function precheckHolidayCsv(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".csv")) return "CSV 파일(.csv)만 올릴 수 있습니다.";
  if (file.size === 0) return "빈 파일입니다.";
  if (file.size > CSV_MAX_BYTES) return `파일이 너무 큽니다(${CSV_MAX_BYTES / 1024}KB 이하).`;
  return null;
}

/** CSV 미리보기 문제의 열 이름 라벨(모르는 값은 '기타'). */
export function problemFieldLabel(field: string): string {
  switch (field) {
    case "holiday_on":
      return "날짜";
    case "name":
      return "이름";
    case "row":
      return "행 전체";
    default:
      return "기타";
  }
}
