// 휴일 캘린더 화면 (S3-2 PR-2b) — 역할별 노출·미선언(UNVERIFIED) ≠ 빈 선언·저장 성공/409/422 칸별 매핑·멱등 키 규칙·
// CSV 미리보기 차단·전체 집합 편집·날짜 문자열 표시(new Date 금지 소스 계약). 응답 모양은 서버 계약(PROGRESS 'S3-2 PR-2a' 인계 계약) 그대로.
// fetch 스텁은 정확 URL·메서드 일치(stubGateFetch).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type { CalendarYear, Holiday, HolidayPage } from "../lib/holidays";
import { ADMIN } from "../test/approval-fixtures";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

const LOGISTICS = { ...TRADER, roles: ["LOGISTICS"] };

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const CAL: CalendarYear = {
  id: 5,
  country_code: "CN",
  year: 2026,
  source_url: "https://www.gov.cn/holidays-2026",
  verified_on: "2026-01-01",
  holiday_count: 2,
  version: 3,
  updated_by_name: "관리자",
  updated_at: "2026-09-30T15:30:00Z",
};
const HOLIDAYS: Holiday[] = [
  { id: 11, holiday_on: "2026-01-01", name: "元旦 신정" },
  { id: 12, holiday_on: "2026-10-01", name: "国庆节 국경절" },
];

const holidayPage = (items: Holiday[], calendar: CalendarYear | null, total = items.length): HolidayPage => ({
  items,
  total,
  page: 1,
  size: 50,
  calendar,
});

const LIST_PATH = "/v1/holidays?country=CN&year=2026";
const FULL_PATH = "/v1/holidays?country=CN&year=2026&page=1&size=200";
const PUT_PATH = "/v1/holidays/CN/2026";
const PREVIEW_PATH = "/v1/holidays/CN/2026/import-csv/preview";
const ROUTE = "/holidays?country=CN&year=2026";

const errorBody = (status: number, code: string, message: string, detail: Record<string, unknown> = {}) =>
  () => jsonResponse({ error: { code, message, detail, request_id: "req-1" } }, status);

function handlers(calendar: CalendarYear | null, items: Holiday[], extra: GateHandler[] = []): GateHandler[] {
  return [
    ...extra,
    [LIST_PATH, "GET", () => jsonResponse(holidayPage(items, calendar))],
    [FULL_PATH, "GET", () => jsonResponse({ ...holidayPage(items, calendar), size: 200 })],
    ["/v1/holidays/calendars?country=CN", "GET", () => jsonResponse(page(calendar ? [calendar] : []))],
    ["/v1/holidays/calendars", "GET", () => jsonResponse(page(calendar ? [calendar] : []))],
    ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })],
  ];
}

function open(me: unknown, calendar: CalendarYear | null = CAL, items: Holiday[] = HOLIDAYS, extra: GateHandler[] = [], route = ROUTE) {
  const stub = stubGateFetch(me, handlers(calendar, items, extra));
  renderWithProviders(<AppRoutes />, { route });
  return stub;
}

async function openEditor(label = "편집·CSV 불러오기") {
  fireEvent.click(await screen.findByRole("button", { name: label }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByLabelText("근거 링크");
  return dialog;
}

const puts = (calls: GateCall[]) => calls.filter((c) => c.method === "PUT");

describe("휴일 캘린더 — 역할별 노출", () => {
  it("ADMIN: 목록·CSV 내보내기·편집 버튼이 보인다", async () => {
    open(ADMIN);
    expect(await screen.findByText("国庆节 국경절")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "편집·CSV 불러오기" })).toBeInTheDocument();
  });

  it.each([
    ["TRADE", TRADER],
    ["LOGISTICS", LOGISTICS],
    ["VIEWER", VIEWER],
  ])("%s: 열람·CSV 내보내기만 — 편집·등록 UI가 없고 관리자 안내가 뜬다", async (_name, me) => {
    const { calls } = open(me);
    expect(await screen.findByText("国庆节 국경절")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "편집·CSV 불러오기" })).not.toBeInTheDocument();
    expect(screen.getByText(/등록·편집은 관리자만 할 수 있습니다/)).toBeInTheDocument();
    expect(calls.some((c) => c.method !== "GET")).toBe(false);
  });

  it("비관리자: 미선언이어도 등록 버튼이 없다", async () => {
    open(VIEWER, null, []);
    expect(await screen.findByText(/휴일 캘린더 미등록 — 확인 불가/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "휴일 캘린더 등록" })).not.toBeInTheDocument();
    expect(screen.getAllByText(/관리자에게 등록을 요청하세요/).length).toBeGreaterThan(0);
  });

  it("내비: 전 역할에게 '휴일 캘린더'가 보인다", async () => {
    for (const me of [ADMIN, TRADER, VIEWER, LOGISTICS]) {
      stubGateFetch(me, handlers(CAL, HOLIDAYS));
      const { unmount } = renderWithProviders(<AppRoutes />, { route: "/holidays" });
      const nav = await screen.findByRole("navigation");
      expect(within(nav).getByRole("link", { name: "휴일 캘린더" })).toHaveAttribute("href", "/holidays");
      unmount();
    }
  });

  it("CSV 내보내기: 전 역할이 H4를 부른다", async () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    const csv: GateHandler = [
      "/v1/holidays/CN/2026/export.csv",
      "GET",
      () => ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])), headers: new Headers() }) as unknown as Response,
    ];
    const { calls } = open(VIEWER, CAL, HOLIDAYS, [csv]);
    fireEvent.click(await screen.findByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/holidays/CN/2026/export.csv"));
  });
});

describe("휴일 캘린더 — 미선언(UNVERIFIED)과 빈 선언은 다르다", () => {
  it("calendar=null: '미등록 — 확인 불가' 배지·안내, 휴일 없음 확인 문구·CSV 버튼 없음", async () => {
    open(ADMIN, null, []);
    expect(await screen.findByText(/휴일 캘린더 미등록 — 확인 불가/)).toBeInTheDocument();
    expect(screen.getByText("미등록")).toBeInTheDocument();
    expect(screen.getByText(/평일로 간주하지 않습니다\)/)).toBeInTheDocument();
    expect(screen.queryByText("휴일 없음 확인")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "CSV 내보내기" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "휴일 캘린더 등록" })).toBeInTheDocument();
  });

  it("calendar 있음 + 0건: '휴일 없음 확인'·근거 표시, 미등록 문구 없음", async () => {
    open(VIEWER, { ...CAL, holiday_count: 0 }, []);
    expect(await screen.findByText("휴일 없음 확인")).toBeInTheDocument();
    expect(screen.queryByText(/미등록 — 확인 불가/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: CAL.source_url })).toHaveAttribute("href", CAL.source_url);
    expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeInTheDocument();
  });
});

describe("휴일 캘린더 — 날짜 표시", () => {
  it("holiday_on·verified_on은 서버 문자열 그대로(하루 밀림 없음), updated_at만 KST", async () => {
    // 서버가 UTC 자정 직전 시간대에서도 문자열이 바뀌지 않는지 — Date 해석이 끼면 2025-12-31로 밀린다.
    open(VIEWER);
    await screen.findByText("国庆节 국경절");
    const section = screen.getByRole("heading", { name: /CN 2026\s*휴일/ }).closest("section") as HTMLElement;
    const rows = within(section).getAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByText("2026-01-01")).toBeInTheDocument();
    expect(within(section).getAllByText("2026-01-01").length).toBeGreaterThanOrEqual(2); // 확인일 + 휴일
    // updated_at 2026-09-30T15:30Z = KST 10-01 00:30
    expect(within(section).getByText(/2026\. 10\. 01\. 00:30 \(KST\)/)).toBeInTheDocument();
  });

  it("소스 계약: 휴일 화면·대화상자·lib에 new Date( 사용 0", () => {
    const sources = import.meta.glob(["./holidays.tsx", "../components/holiday-*.tsx", "../lib/holidays.ts"], {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    expect(Object.keys(sources).length).toBe(3);
    const offenders = Object.entries(sources)
      .filter(([, src]) => /new Date\(/.test(src.replace(/\/\/.*$/gm, "")))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it("소스 계약: 표는 섹션 안 overflow-x-auto, 날짜·국가 칸 cell-nowrap·가운데", () => {
    const src = import.meta.glob("./holidays.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const text = (Object.values(src)[0] as string).replace(/\/\/.*$/gm, "");
    expect((text.match(/<table/g) ?? []).length).toBe((text.match(/overflow-x-auto/g) ?? []).length);
    expect(text).toContain('className="num cell-nowrap px-3 py-2 text-center">{holiday.holiday_on}');
    expect(text).toContain('className="cell-nowrap px-3 py-2 text-center">{row.country_code}');
  });
});

describe("휴일 캘린더 — 선택(주소)", () => {
  it("주소의 국가·연도로 바로 조회한다(선적 배지 링크 진입)", async () => {
    const { calls } = open(VIEWER);
    await screen.findByText("国庆节 국경절");
    expect(calls.map((c) => c.url)).toContain(`/api${LIST_PATH}`);
    expect(screen.getByLabelText("국가 코드")).toHaveValue("CN");
    expect(screen.getByLabelText("연도")).toHaveValue("2026");
  });

  it("국가·연도 형식이 틀리면 조회하지 않고 칸 아래 안내", async () => {
    const { calls } = open(VIEWER, CAL, HOLIDAYS, [], "/holidays");
    fireEvent.change(await screen.findByLabelText("국가 코드"), { target: { value: "c" } });
    fireEvent.change(screen.getByLabelText("연도"), { target: { value: "1999" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("국가는 영문 대문자 2자");
    expect(alert).toHaveTextContent("연도는 2000~2999");
    expect(calls.some((c) => c.url.startsWith("/api/v1/holidays?"))).toBe(false);
  });

  it("선언 목록 '보기'가 그 국가·연도를 연다", async () => {
    const { calls } = open(VIEWER, CAL, HOLIDAYS, [], "/holidays");
    fireEvent.click(await screen.findByRole("button", { name: "CN 2026 보기" }));
    await screen.findByText("国庆节 국경절");
    expect(calls.map((c) => c.url)).toContain(`/api${LIST_PATH}`);
  });
});

describe("휴일 캘린더 편집 — 저장·오류 매핑", () => {
  it("저장 성공: 전체 집합 + 근거 + version을 PUT, 키 1개, 완료 안내", async () => {
    const saved = { ...CAL, version: 4, holiday_count: 3 };
    const { calls } = open(ADMIN, CAL, HOLIDAYS, [[PUT_PATH, "PUT", () => jsonResponse(saved)]]);
    const dialog = await openEditor();
    expect(within(dialog).getByLabelText("근거 링크")).toHaveValue(CAL.source_url);
    fireEvent.click(within(dialog).getByRole("button", { name: "휴일 추가" }));
    fireEvent.change(within(dialog).getByLabelText("3번째 휴일 날짜"), { target: { value: "2026-05-01" } });
    fireEvent.change(within(dialog).getByLabelText("3번째 휴일 이름"), { target: { value: " 劳动节 " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    const put = puts(calls)[0]!;
    expect(put.url).toBe(`/api${PUT_PATH}`);
    expect(put.body).toEqual({
      source_url: CAL.source_url,
      verified_on: "2026-01-01",
      holidays: [
        { holiday_on: "2026-01-01", name: "元旦 신정" },
        { holiday_on: "2026-10-01", name: "国庆节 국경절" },
        { holiday_on: "2026-05-01", name: "劳动节" },
      ],
      version: 3,
    });
    expect(put.headers["Idempotency-Key"]).toMatch(/^key-\d+$/);
    expect(await screen.findByRole("status")).toHaveTextContent("CN 2026 휴일 캘린더를 저장했습니다(3건)");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("편집은 화면 1쪽이 아니라 전체 집합(size=200 전 쪽)에서 시작한다", async () => {
    const many = Array.from({ length: 60 }, (_, i) => ({
      id: 100 + i,
      holiday_on: `2026-03-${String((i % 28) + 1).padStart(2, "0")}`,
      name: `휴일${i}`,
    }));
    const { calls } = open(ADMIN, { ...CAL, holiday_count: 60 }, many);
    const dialog = await openEditor();
    expect(calls.map((c) => c.url)).toContain(`/api${FULL_PATH}`);
    expect(within(dialog).getAllByLabelText(/번째 휴일 이름/)).toHaveLength(60);
  });

  it("전체 조회 건수가 total과 다르면 편집을 시작하지 않는다(조용한 잘림 금지)", async () => {
    open(ADMIN, CAL, HOLIDAYS, [[FULL_PATH, "GET", () => jsonResponse({ ...holidayPage(HOLIDAYS, CAL, 5), size: 200 })]]);
    fireEvent.click(await screen.findByRole("button", { name: "편집·CSV 불러오기" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다시 열어 주세요");
    expect(within(dialog).queryByRole("button", { name: "저장" })).not.toBeInTheDocument();
  });

  it("미선언 최초 등록: version null, 빈 목록 저장 = 휴일 없음 확인", async () => {
    const { calls } = open(ADMIN, null, [], [[PUT_PATH, "PUT", () => jsonResponse({ ...CAL, holiday_count: 0, version: 1 })]]);
    const dialog = await openEditor("휴일 캘린더 등록");
    expect(within(dialog).getByText(/처음 선언합니다\(0건\)/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("근거 링크"), { target: { value: "https://example.org/cn" } });
    fireEvent.change(within(dialog).getByLabelText(/확인일/), { target: { value: "2026-09-01" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0]!.body).toEqual({
      source_url: "https://example.org/cn",
      verified_on: "2026-09-01",
      holidays: [],
      version: null,
    });
  });

  it("409 VERSION_CONFLICT: 다시 불러오기 안내 + '최신 내용 불러오기'가 대화상자를 닫고 재조회", async () => {
    const { calls } = open(ADMIN, CAL, HOLIDAYS, [
      [PUT_PATH, "PUT", errorBody(409, "COMMON.CONCURRENCY.VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다.")],
    ]);
    const dialog = await openEditor();
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    expect(await within(dialog).findByText(/먼저 저장되었습니다/)).toBeInTheDocument();
    const before = calls.filter((c) => c.url === `/api${LIST_PATH}`).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(calls.filter((c) => c.url === `/api${LIST_PATH}`).length).toBeGreaterThan(before));
  });

  it("409 YEAR_DUPLICATE(동시 최초 선언)도 같은 다시 불러오기 안내", async () => {
    open(ADMIN, null, [], [[PUT_PATH, "PUT", errorBody(409, "HOLIDAYS.CALENDAR.YEAR_DUPLICATE", "이미 선언되었습니다.")]]);
    const dialog = await openEditor("휴일 캘린더 등록");
    fireEvent.change(within(dialog).getByLabelText("근거 링크"), { target: { value: "https://example.org/cn" } });
    fireEvent.change(within(dialog).getByLabelText(/확인일/), { target: { value: "2026-09-01" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    expect(await within(dialog).findByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("422 SOURCE_REQUIRED: detail.source_url·verified_on을 각 칸 아래에", async () => {
    open(ADMIN, CAL, HOLIDAYS, [
      [
        PUT_PATH,
        "PUT",
        errorBody(422, "HOLIDAYS.CALENDAR.SOURCE_REQUIRED", "근거가 필요합니다.", {
          source_url: "근거 링크에 공백·보이지 않는 문자가 있습니다.",
          verified_on: "확인일은 오늘(한국 날짜) 또는 그 이전이어야 합니다.",
        }),
      ],
    ]);
    const dialog = await openEditor();
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    const url = within(dialog).getByLabelText("근거 링크");
    await waitFor(() => expect(url).toHaveAttribute("aria-invalid", "true"));
    expect(document.getElementById(url.getAttribute("aria-describedby")!)).toHaveTextContent("공백·보이지 않는 문자");
    const verified = within(dialog).getByLabelText(/확인일/);
    expect(document.getElementById(verified.getAttribute("aria-describedby")!)).toHaveTextContent("오늘(한국 날짜)");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("근거가 필요합니다.");
  });

  it.each([
    ["HOLIDAYS.CALENDAR.YEAR_MISMATCH", "이 연도의 날짜가 아닙니다."],
    ["HOLIDAYS.CALENDAR.DUPLICATE_DATE", "같은 날짜가 두 번 있습니다."],
  ])("422 %s: detail.holiday_on의 날짜 행 날짜 칸 아래", async (code, note) => {
    open(ADMIN, CAL, HOLIDAYS, [[PUT_PATH, "PUT", errorBody(422, code, "날짜를 확인해 주세요.", { holiday_on: ["2026-10-01"] })]]);
    const dialog = await openEditor();
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    const second = within(dialog).getByLabelText("2번째 휴일 날짜");
    await waitFor(() => expect(second).toHaveAttribute("aria-invalid", "true"));
    expect(document.getElementById(second.getAttribute("aria-describedby")!)).toHaveTextContent(note);
    expect(within(dialog).getByLabelText("1번째 휴일 날짜")).toHaveAttribute("aria-invalid", "false");
  });

  it("422 INVALID_FIELD detail.name {날짜: 문구}: 그 행 이름 칸 아래 — 행을 지워도 칸이 어긋나지 않는다", async () => {
    open(ADMIN, CAL, HOLIDAYS, [
      [
        PUT_PATH,
        "PUT",
        errorBody(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값을 확인해 주세요.", {
          name: { "2026-10-01": "휴일 이름에 보이지 않는 문자가 있습니다." },
        }),
      ],
    ]);
    const dialog = await openEditor();
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    const name2 = within(dialog).getByLabelText("2번째 휴일 이름");
    await waitFor(() => expect(name2).toHaveAttribute("aria-invalid", "true"));
    expect(document.getElementById(name2.getAttribute("aria-describedby")!)).toHaveTextContent("보이지 않는 문자");
    fireEvent.click(within(dialog).getByRole("button", { name: "1번째 휴일 삭제" }));
    // 첫 행이 지워져 국경절이 1번째가 되어도 문구는 그 행에 붙어 있다.
    const moved = within(dialog).getByLabelText("1번째 휴일 이름");
    expect(moved).toHaveValue("国庆节 국경절");
    expect(moved).toHaveAttribute("aria-invalid", "true");
  });

  it("422 요청 검증(detail.항목 위치 holidays.N.name)도 N번째 행으로", async () => {
    open(ADMIN, CAL, HOLIDAYS, [
      [
        PUT_PATH,
        "PUT",
        errorBody(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값을 확인해 주세요.", {
          항목: [{ 위치: "holidays.0.name", 사유: "String should have at most 100 characters" }],
        }),
      ],
    ]);
    const dialog = await openEditor();
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    const name1 = within(dialog).getByLabelText("1번째 휴일 이름");
    await waitFor(() => expect(name1).toHaveAttribute("aria-invalid", "true"));
  });

  it("사전 검사: 다른 연도·빈 이름·미래 확인일은 보내지 않고 칸에 표시", async () => {
    const { calls } = open(ADMIN);
    const dialog = await openEditor();
    fireEvent.change(within(dialog).getByLabelText("1번째 휴일 날짜"), { target: { value: "2027-01-01" } });
    fireEvent.change(within(dialog).getByLabelText("2번째 휴일 이름"), { target: { value: "   " } });
    fireEvent.change(within(dialog).getByLabelText(/확인일/), { target: { value: "2999-01-01" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    expect(await within(dialog).findByText("2026년 날짜만 넣을 수 있습니다.")).toBeInTheDocument();
    expect(within(dialog).getByText("휴일 이름을 입력해 주세요.")).toBeInTheDocument();
    expect(within(dialog).getByText(/확인일은 오늘/)).toBeInTheDocument();
    expect(puts(calls)).toHaveLength(0);
  });
});

describe("휴일 캘린더 편집 — 멱등 키 규칙", () => {
  it("같은 본문 재시도 = 같은 키, 본문이 바뀌면 새 키, 다시 열면 새 키", async () => {
    // 매번 일시 오류(500) — 재시도·본문 변경·다시 열기마다 실린 키만 본다.
    const respond = errorBody(500, "COMMON.INTERNAL.UNEXPECTED", "일시 오류");
    const { calls } = open(ADMIN, CAL, HOLIDAYS, [[PUT_PATH, "PUT", respond]]);
    let dialog = await openEditor();
    const save = () => fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    save();
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    await within(dialog).findByText("일시 오류");
    save();
    await waitFor(() => expect(puts(calls)).toHaveLength(2));
    const keyOf = (i: number) => puts(calls)[i]?.headers["Idempotency-Key"];
    expect(keyOf(1)).toBe(keyOf(0));
    fireEvent.change(within(dialog).getByLabelText("1번째 휴일 이름"), { target: { value: "신정" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "저장" })).not.toBeDisabled());
    save();
    await waitFor(() => expect(puts(calls)).toHaveLength(3));
    expect(keyOf(2)).not.toBe(keyOf(1));
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openEditor();
    save();
    await waitFor(() => expect(puts(calls)).toHaveLength(4));
    expect(new Set([keyOf(0), keyOf(2), keyOf(3)]).size).toBe(3);
  });

  it("더블클릭은 1회만 전송", async () => {
    const { calls } = open(ADMIN, CAL, HOLIDAYS, [[PUT_PATH, "PUT", () => jsonResponse({ ...CAL, version: 4 })]]);
    const dialog = await openEditor();
    const button = within(dialog).getByRole("button", { name: "저장" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(puts(calls)).toHaveLength(1);
  });
});

describe("휴일 캘린더 편집 — CSV 미리보기(H5)", () => {
  const csv = (content = "holiday_on,name\r\n2026-05-01,劳动节\r\n") => new File([content], "cn.csv", { type: "text/csv" });
  const pick = (dialog: HTMLElement, file: File) =>
    fireEvent.change(within(dialog).getByLabelText("휴일 CSV 파일"), { target: { files: [file] } });

  it("문제 0: 미리보기 결과로 편집본을 채우고 저장하면 그 행들이 H3 본문이 된다(새 키)", async () => {
    const { calls } = open(ADMIN, CAL, HOLIDAYS, [
      [PREVIEW_PATH, "POST", () => jsonResponse({ rows: [{ holiday_on: "2026-05-01", name: "劳动节" }], problems: [] })],
      [PUT_PATH, "PUT", () => jsonResponse({ ...CAL, version: 4, holiday_count: 1 })],
    ]);
    const dialog = await openEditor();
    pick(dialog, csv());
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByText(/cn\.csv의/)).toHaveTextContent("1건으로 아래 목록을 채웠습니다");
    expect(within(dialog).getAllByLabelText(/번째 휴일 이름/)).toHaveLength(1);
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe(`/api${PREVIEW_PATH}`);
    expect(post.rawBody).toBeInstanceOf(FormData);
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    expect(puts(calls)[0]!.body).toMatchObject({ holidays: [{ holiday_on: "2026-05-01", name: "劳动节" }], version: 3 });
  });

  it("문제 1건↑: 목록을 채우지 않고 저장을 막는다 — 'CSV 결과 버리기' 뒤에야 저장 가능", async () => {
    const { calls } = open(ADMIN, CAL, HOLIDAYS, [
      [
        PREVIEW_PATH,
        "POST",
        () =>
          jsonResponse({
            rows: [{ holiday_on: "2026-05-01", name: "劳动节" }],
            problems: [{ row: 2, field: "holiday_on", message: "날짜 형식이 아닙니다." }],
          }),
      ],
    ]);
    const dialog = await openEditor();
    pick(dialog, csv());
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByText("날짜 형식이 아닙니다.")).toBeInTheDocument();
    expect(within(dialog).getByText(/있어 목록을 채우지 않았습니다/)).toHaveTextContent("문제가 1건 있어 목록을 채우지 않았습니다");
    expect(within(dialog).getAllByLabelText(/번째 휴일 이름/)).toHaveLength(2); // 기존 편집본 그대로
    const save = within(dialog).getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(puts(calls)).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "CSV 결과 버리기" }));
    expect(within(dialog).getByRole("button", { name: "저장" })).not.toBeDisabled();
  });

  it("파일 단위 거부 422 INVALID_FORMAT: detail.file 문구를 파일 칸 아래에, 목록은 그대로", async () => {
    open(ADMIN, CAL, HOLIDAYS, [
      [
        PREVIEW_PATH,
        "POST",
        errorBody(422, "HOLIDAYS.CSV.INVALID_FORMAT", "휴일 CSV 형식을 확인해 주세요(UTF-8, 머리글 holiday_on,name).", {
          file: "첫 줄 머리글은 정확히 holiday_on,name 이어야 합니다.",
        }),
      ],
    ]);
    const dialog = await openEditor();
    pick(dialog, csv("a,b\r\n"));
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    const input = within(dialog).getByLabelText("휴일 CSV 파일");
    await waitFor(() => expect(input).toHaveAttribute("aria-invalid", "true"));
    expect(document.getElementById(input.getAttribute("aria-describedby")!)).toHaveTextContent("첫 줄 머리글은");
    expect(within(dialog).getAllByLabelText(/번째 휴일 이름/)).toHaveLength(2);
  });

  it("보내기 전 검사: .csv 아님·빈 파일은 서버를 부르지 않는다", async () => {
    const { calls } = open(ADMIN);
    const dialog = await openEditor();
    pick(dialog, new File(["x"], "cn.xlsx"));
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByText("CSV 파일(.csv)만 올릴 수 있습니다.")).toBeInTheDocument();
    pick(dialog, new File([], "cn.csv"));
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByText("빈 파일입니다.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});
