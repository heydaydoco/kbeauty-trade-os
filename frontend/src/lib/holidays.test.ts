// 휴일 lib — 편집용 전체 조회(쪽 합치기·섞인 판 거부)·오류 매핑·사전 검사 (S3-2 PR-2b).

import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse } from "../test/render";
import { ApiError } from "./api";
import { FullFetchMismatch, fetchFullCalendar, isDeclarableYear, isCountryCode, mapReplaceError, precheckRows, type CalendarYear } from "./holidays";

afterEach(() => vi.unstubAllGlobals());

const CAL = { id: 1, country_code: "CN", year: 2026, version: 2 } as CalendarYear;

function day(i: number): string {
  // 2026년 1월 1일부터 i일째 — 문자열로만 만든다(테스트도 Date 산술 없이 월별 일수 표).
  const days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let rest = i;
  let month = 0;
  while (rest >= (days[month] as number)) {
    rest -= days[month] as number;
    month += 1;
  }
  return `2026-${String(month + 1).padStart(2, "0")}-${String(rest + 1).padStart(2, "0")}`;
}

function stubPages(total: number, versions: number[] = [2, 2]) {
  const all = Array.from({ length: total }, (_, i) => ({ id: i + 1, holiday_on: day(i), name: `휴일${i}` }));
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      urls.push(url);
      const page = Number(/page=(\d+)/.exec(url)?.[1]);
      const items = all.slice((page - 1) * 200, page * 200);
      return Promise.resolve(jsonResponse({ items, total, page, size: 200, calendar: { ...CAL, version: versions[page - 1] } }));
    }),
  );
  return urls;
}

describe("fetchFullCalendar", () => {
  it("365건을 200건 쪽 2개로 끝까지 합친다", async () => {
    const urls = stubPages(365);
    const result = await fetchFullCalendar("CN", "2026");
    expect(result.holidays).toHaveLength(365);
    expect(result.holidays[364]?.holiday_on).toBe("2026-12-31");
    expect(urls).toEqual([
      "/api/v1/holidays?country=CN&year=2026&page=1&size=200",
      "/api/v1/holidays?country=CN&year=2026&page=2&size=200",
    ]);
  });

  it("쪽 사이에 version이 바뀌면 거부(섞인 판)", async () => {
    stubPages(365, [2, 3]);
    await expect(fetchFullCalendar("CN", "2026")).rejects.toBeInstanceOf(FullFetchMismatch);
  });
});

describe("mapReplaceError", () => {
  const err = (code: string, detail: Record<string, unknown>) => new ApiError(422, { code, message: "m", detail, requestId: null });
  const sent = [{ holiday_on: "2026-01-01" }, { holiday_on: "2026-10-01" }, { holiday_on: "2026-10-01" }];

  it("DUPLICATE_DATE는 그 날짜의 모든 행", () => {
    expect(mapReplaceError(err("HOLIDAYS.CALENDAR.DUPLICATE_DATE", { holiday_on: ["2026-10-01"] }), sent).rowDate).toEqual({
      1: "같은 날짜가 두 번 있습니다.",
      2: "같은 날짜가 두 번 있습니다.",
    });
  });

  it("ApiError가 아니면 일반 문구·칸 오류 0", () => {
    const view = mapReplaceError(new Error("x"), sent);
    expect(view.rowDate).toEqual({});
    expect(view.conflict).toBe(false);
  });
});

describe("형식 검사", () => {
  it("국가 대문자 2자·연도 2000~2999", () => {
    expect(isCountryCode("CN")).toBe(true);
    expect(isCountryCode("cn")).toBe(false);
    expect(isDeclarableYear("2026")).toBe(true);
    expect(isDeclarableYear("1999")).toBe(false);
    expect(isDeclarableYear("20260")).toBe(false);
  });

  it("precheckRows: 연도는 문자열 접두로 판정(Date 해석 없음)", () => {
    expect(precheckRows("2026", [{ holiday_on: "2026-12-31", name: "a" }])).toBeNull();
    expect(precheckRows("2026", [{ holiday_on: "2027-01-01", name: "a" }])?.rowDate).toEqual({ 0: "2026년 날짜만 넣을 수 있습니다." });
  });
});
