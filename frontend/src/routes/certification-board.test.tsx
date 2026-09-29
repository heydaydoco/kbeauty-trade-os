// 인증 보드 — 칸반 11컬럼·잘림 고지·드래그 비구현 안내·캘린더 월 조회·도과 표시 (S2-3 PR-3 안건 ⑥).
//
// 고정하는 것: ① 칸반 컬럼이 상태 11값을 §5.2 순서·한국어 라벨로 보인다 ② 카드가 요건명·대상·만료일·
// 담당자·도과 배지를 싣고 인증 상세 딥링크로 간다 ③ 200장을 넘으면 총계와 함께 잘림을 알린다(조용한
// 잘림 금지) ④ 캘린더가 KST 기준 이번 달 범위로 서버에 묻고 항목을 날짜 칸에 배치한다 ⑤ 월 이동이
// 새 범위를 묻는다 ⑥ 보드에는 상태를 바꾸는 조작이 없다(전이 통로는 상세 폼 하나).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { VIEWER, jsonResponse, renderWithProviders } from "../test/render";
import type { Certification } from "./certifications";
import type { CalendarItem } from "./certification-board";

function cert(id: number, status: string, extra: Partial<Certification> = {}): Certification {
  return {
    id,
    template_id: 10,
    market_code: "US",
    template_name: `요건 ${id}`,
    requirement_type: "REGISTRATION",
    target_type: "SKU",
    target_id: id,
    target_label: `SKU-${id}`,
    status,
    validity_months: null,
    renewal_cycle_months: null,
    renewal_lead_days: 90,
    source_url: null,
    last_verified_on: null,
    cert_number: null,
    applied_on: null,
    approved_on: null,
    valid_from: null,
    expires_on: null,
    assignee_id: null,
    assignee_name: null,
    note: null,
    version: 1,
    handling_mode: "DIRECT",
    action_owner: "INTERNAL",
    action_owner_changed_on: null,
    agency_partner_id: null,
    agency_partner_name: null,
    is_overdue: false,
    overdue_days: null,
    ...extra,
  };
}

function stubApi(options: {
  certifications?: Certification[];
  /** 시장 코드별 카드 — market_code 파라미터가 오면 이 표로 답한다. */
  byMarket?: Record<string, Certification[]>;
  markets?: { code: string; name_ko: string }[];
  total?: number;
  calendar?: CalendarItem[];
  calendarTotal?: number;
  /** 캘린더 응답을 이 약속이 풀릴 때까지 붙든다(로딩 표시 검증). */
  calendarGate?: Promise<void>;
  calendarStatus?: number;
  seen?: string[];
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      options.seen?.push(input);
      if (input.includes("/auth/me")) return jsonResponse(VIEWER);
      if (input.includes("/deadlines/calendar")) {
        await options.calendarGate;
        if (options.calendarStatus !== undefined && options.calendarStatus >= 400) {
          return jsonResponse(
            { error: { code: "COMMON.INTERNAL", message: "캘린더 오류", detail: {} } },
            options.calendarStatus,
          );
        }
        const items = options.calendar ?? [];
        return jsonResponse({
          items,
          total: options.calendarTotal ?? items.length,
          page: 1,
          size: 200,
        });
      }
      if (input.includes("/v1/markets")) {
        const markets = (options.markets ?? []).map((m, index) => ({ id: index + 1, ...m }));
        return jsonResponse({ items: markets, total: markets.length, page: 1, size: 200 });
      }
      if (input.includes("/v1/certifications")) {
        const code = new URL(input, "http://x").searchParams.get("market_code");
        const items =
          code !== null && options.byMarket
            ? (options.byMarket[code] ?? [])
            : (options.certifications ?? []);
        return jsonResponse({ items, total: options.total ?? items.length, page: 1, size: 200 });
      }
      return jsonResponse({ items: [], total: 0, page: 1, size: 50 });
    }),
  );
}

describe("인증 보드 — 칸반", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("컬럼 11개가 §5.2 순서·한국어 라벨로 보이고 카드가 알맞은 컬럼에 놓인다", async () => {
    stubApi({
      certifications: [
        cert(1, "NOT_STARTED"),
        cert(2, "IN_REVIEW", { assignee_name: "김인증", expires_on: "2027-01-31" }),
        cert(3, "APPROVED"),
        cert(4, "RENEWING", { is_overdue: true, overdue_days: 12, expires_on: "2026-09-17" }),
        cert(5, "SUSPENDED"),
      ],
    });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    const columns = await screen.findAllByRole("region", { name: /컬럼$/ });
    expect(columns.map((column) => column.getAttribute("aria-label"))).toEqual([
      "미착수 컬럼",
      "서류준비 컬럼",
      "신청제출 컬럼",
      "심사중 컬럼",
      "보완요청 컬럼",
      "승인(유효) 컬럼",
      "만료임박 컬럼",
      "갱신중 컬럼",
      "만료 컬럼",
      "반려 컬럼",
      "중단 컬럼",
    ]);
    expect(
      within(screen.getByRole("region", { name: "심사중 컬럼" })).getByText("요건 2"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "미착수 컬럼" })).getByText("요건 1"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "중단 컬럼" })).getByText("요건 5"),
    ).toBeInTheDocument();
    // 컬럼 머리는 줄바꿈을 막고 카드 수를 보인다
    const header = within(screen.getByRole("region", { name: "심사중 컬럼" })).getByRole("heading");
    expect(header).toHaveClass("cell-nowrap");
    expect(header).toHaveTextContent("심사중 1");
  });

  it("카드가 요건명·대상·만료일·담당자·도과 배지를 싣고 인증 상세로 연결된다", async () => {
    stubApi({
      certifications: [
        cert(2, "IN_REVIEW", { assignee_name: "김인증", expires_on: "2027-01-31" }),
        cert(4, "RENEWING", { is_overdue: true, overdue_days: 12, expires_on: "2026-09-17" }),
        cert(1, "NOT_STARTED"),
      ],
    });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    const review = within(await screen.findByRole("region", { name: "심사중 컬럼" }));
    const link = review.getByRole("link");
    expect(link).toHaveAttribute("href", "/certifications?id=2");
    expect(link).toHaveTextContent("US");
    expect(link).toHaveTextContent("요건 2");
    expect(link).toHaveTextContent("SKU-2");
    expect(link).toHaveTextContent("만료 2027-01-31");
    expect(link).toHaveTextContent("담당 김인증");

    const renewing = within(screen.getByRole("region", { name: "갱신중 컬럼" }));
    expect(renewing.getByText("도과 12일")).toBeInTheDocument(); // 상태는 갱신중 그대로, 배지만
    const fresh = within(screen.getByRole("region", { name: "미착수 컬럼" }));
    expect(fresh.getByText("만료일 없음")).toBeInTheDocument();
    expect(fresh.getByText("담당자 없음")).toBeInTheDocument();
  });

  it("200장을 넘으면 총계와 함께 잘림을 알린다 — 조용한 잘림 금지", async () => {
    stubApi({ certifications: [cert(1, "APPROVED")], total: 431 });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("전체 431건 중 1건만 표시했습니다");
  });

  it("다 싣었으면 잘림 안내가 없다", async () => {
    stubApi({ certifications: [cert(1, "APPROVED")] });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    await screen.findByRole("region", { name: "승인(유효) 컬럼" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("드래그로 상태를 바꿀 수 없다는 안내가 있고 카드에 전이 조작이 없다", async () => {
    stubApi({ certifications: [cert(1, "NOT_STARTED")] });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    expect(await screen.findByText(/카드를 끌어서 옮기는 기능은 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /전이|이동|상태 변경/ })).not.toBeInTheDocument();
    expect(document.querySelector("[draggable]")).toBeNull(); // 값과 무관하게 draggable 속성 자체가 없다
  });

  it("칸반은 카드 200장 크기로 묻는다 — 시장을 고르지 않으면 시장 조건이 없다", async () => {
    const seen: string[] = [];
    stubApi({ certifications: [cert(1, "APPROVED")], seen });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    await screen.findByRole("region", { name: "승인(유효) 컬럼" });
    const asked = seen.filter((path) => path.includes("/v1/certifications"));
    expect(asked).toEqual(["/api/v1/certifications?size=200"]);
  });

  it("시장 필터가 서버 질의로 나가고 그 시장의 카드만 보인다", async () => {
    const seen: string[] = [];
    stubApi({
      markets: [
        { code: "CA", name_ko: "캐나다" },
        { code: "US", name_ko: "미국" },
      ],
      certifications: [
        cert(1, "APPROVED", { market_code: "US" }),
        cert(2, "NOT_STARTED", { market_code: "CA" }),
      ],
      byMarket: { CA: [cert(2, "NOT_STARTED", { market_code: "CA" })] },
      seen,
    });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    await screen.findByRole("region", { name: "승인(유효) 컬럼" });
    expect(
      within(screen.getByRole("region", { name: "승인(유효) 컬럼" })).getAllByRole("link"),
    ).toHaveLength(1);
    await screen.findByRole("option", { name: "CA 캐나다" });

    fireEvent.change(screen.getByLabelText("시장"), { target: { value: "CA" } });
    await waitFor(() =>
      expect(seen.some((path) => path.includes("size=200&market_code=CA"))).toBe(true),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "승인(유효) 컬럼" })).queryAllByRole("link"),
      ).toHaveLength(0),
    );
    expect(
      within(screen.getByRole("region", { name: "미착수 컬럼" })).getAllByRole("link"),
    ).toHaveLength(1);

    // 전체로 돌아오면 시장 조건이 빠진다
    fireEvent.change(screen.getByLabelText("시장"), { target: { value: "" } });
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "승인(유효) 컬럼" })).getAllByRole("link"),
      ).toHaveLength(1),
    );
  });

  it("잘림 안내는 실제로 가능한 조치를 말한다 — 시장을 골라 좁히기", async () => {
    stubApi({
      markets: [{ code: "US", name_ko: "미국" }],
      certifications: [cert(1, "APPROVED")],
      byMarket: { US: [cert(1, "APPROVED")] },
      total: 431,
    });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    expect(await screen.findByRole("status")).toHaveTextContent("시장을 골라 좁혀 보거나");
    await screen.findByRole("option", { name: "US 미국" });
    fireEvent.change(screen.getByLabelText("시장"), { target: { value: "US" } });
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("나머지는 인증 화면에서 확인해 주세요"),
    );
  });

  it("도과가 아닌 카드에는 도과 배지가 없다 (배지는 서버 계산값이 참일 때만)", async () => {
    stubApi({ certifications: [cert(1, "APPROVED"), cert(2, "RENEWING")] });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    await screen.findByRole("region", { name: "갱신중 컬럼" });
    expect(screen.queryByText(/도과/)).not.toBeInTheDocument();
  });

  it("서버가 준 상태가 컬럼 표에 없으면 카드가 사라지지 않고 기타 상태 컬럼에 모인다", async () => {
    stubApi({ certifications: [cert(1, "APPROVED"), cert(2, "ON_HOLD")] });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    const other = await screen.findByRole("region", { name: "기타 상태 컬럼" });
    expect(within(other).getByRole("link")).toHaveAttribute("href", "/certifications?id=2");
    expect(other).toHaveTextContent("기타 상태 1");
    expect(screen.getAllByRole("region", { name: /컬럼$/ })).toHaveLength(12);
  });

  it("운영 캐시(staleTime 30초)에서도 다시 들어오면 새로 가져온다 — 전이 직후 옛 카드가 남지 않는다", async () => {
    const seen: string[] = [];
    stubApi({ certifications: [cert(1, "APPROVED")], seen });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    const first = renderWithProviders(<AppRoutes />, { route: "/certification-board", client });
    await screen.findByRole("region", { name: "승인(유효) 컬럼" });
    const count = () => seen.filter((path) => path.includes("/v1/certifications")).length;
    expect(count()).toBe(1);
    first.unmount();

    renderWithProviders(<AppRoutes />, { route: "/certification-board", client });
    await screen.findByRole("region", { name: "승인(유효) 컬럼" });
    await waitFor(() => expect(count()).toBeGreaterThanOrEqual(2));
  });

  it("인증이 없으면 조치를 안내한다", async () => {
    stubApi({ certifications: [] });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    expect(await screen.findByText(/등록된 인증이 없습니다\. 인증 화면에서/)).toBeInTheDocument();
  });

  it("네비게이션에 인증 보드 항목이 있다", async () => {
    stubApi({});
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });

    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "인증 보드" })).toHaveAttribute(
      "href",
      "/certification-board",
    );
  });
});

describe("인증 보드 — 캘린더", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
    // 시각만 고정한다 — 타이머까지 가짜로 만들면 react-query의 비동기 대기가 멈춘다.
    // 2026-09-29 00:30 KST = 2026-09-28 15:30 UTC (KST 날짜가 UTC 날짜와 다른 경계 시각).
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-28T15:30:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const ITEMS: CalendarItem[] = [
    {
      kind: "CERTIFICATION",
      id: 41,
      title: "[US] MoCRA 제품 리스팅 — 수분 세럼",
      date: "2026-09-10",
      status: "RENEWING",
      is_overdue: true,
      assignee_id: 3,
      assignee_name: "김인증",
    },
    {
      kind: "DOCUMENT",
      id: 7,
      title: "자유판매증명서(CFS) · SKU-1",
      date: "2026-09-10",
      status: null,
      is_overdue: false,
      assignee_id: null,
      assignee_name: null,
    },
    {
      kind: "CERTIFICATION",
      id: 42,
      title: "[CA] CNF 통보 — 진정 크림",
      date: "2026-09-29",
      status: "APPROVED",
      is_overdue: false,
      assignee_id: null,
      assignee_name: null,
    },
  ];

  async function openCalendar(seen?: string[]) {
    stubApi({ calendar: ITEMS, seen });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));
  }

  it("KST 기준 이번 달 범위로 묻고 항목을 날짜 칸에 배치한다 (UTC 날짜와 달라도 KST 9월)", async () => {
    const seen: string[] = [];
    await openCalendar(seen);

    expect(await screen.findByRole("heading", { name: "2026년 9월" })).toBeInTheDocument();
    await waitFor(() =>
      expect(seen.some((path) => path.includes("from=2026-09-01&to=2026-09-30&size=200"))).toBe(
        true,
      ),
    );
    const tenth = within(await screen.findByLabelText("9월 10일 2건"));
    expect(
      tenth.getByRole("link", { name: /인증 \[US\] MoCRA 제품 리스팅 — 수분 세럼 \(도과\)/ }),
    ).toHaveAttribute("href", "/certifications?id=41");
    expect(tenth.getByRole("link", { name: /문서 자유판매증명서\(CFS\) · SKU-1/ })).toHaveAttribute(
      "href",
      "/documents",
    );
    // 오늘 칸(KST 29일)은 표시되고, 그 칸의 항목이 놓인다
    const today = within(screen.getByLabelText("9월 29일 1건"));
    expect(today.getByText(/오늘/)).toBeInTheDocument();
    expect(today.getByRole("link", { name: /\[CA\] CNF 통보/ })).toBeInTheDocument();
  });

  it("UTC로는 9월 말인데 KST로는 10월 1일이면 10월을 보여 준다 — 월 경계", async () => {
    vi.setSystemTime(new Date("2026-09-30T16:30:00Z")); // KST 2026-10-01 01:30
    const seen: string[] = [];
    await openCalendar(seen);

    expect(await screen.findByRole("heading", { name: "2026년 10월" })).toBeInTheDocument();
    await waitFor(() =>
      expect(seen.some((path) => path.includes("from=2026-10-01&to=2026-10-31&size=200"))).toBe(
        true,
      ),
    );
    expect(seen.some((path) => path.includes("from=2026-09-01"))).toBe(false);
    expect(within(screen.getByLabelText("10월 1일")).getByText(/오늘/)).toBeInTheDocument();
  });

  it("도과 항목은 붉은 표시와 (도과) 글자를 함께 가진다 — 색만으로 구분하지 않는다", async () => {
    await openCalendar();
    const chip = await screen.findByRole("link", { name: /수분 세럼 \(도과\)/ });
    expect(chip.className).toContain("bg-red-100");
    expect(chip).toHaveTextContent("도과"); // aria-label이 아니라 눈에 보이는 글자
    const ok = screen.getByRole("link", { name: /\[CA\] CNF 통보/ });
    expect(ok.className).not.toContain("bg-red-100");
    expect(ok).not.toHaveTextContent("도과");
    expect(ok.getAttribute("aria-label")).not.toContain("도과");
    // 안내문도 색이 아니라 표시 글자를 가리킨다
    expect(screen.getByText(/도과.{1,3}표시는 기일이 지난 건입니다/)).toBeInTheDocument();
    expect(screen.queryByText(/붉은 항목/)).not.toBeInTheDocument();
  });

  it("월 이동이 새 범위를 서버에 묻는다 — 연말·연초 경계 포함", async () => {
    const seen: string[] = [];
    await openCalendar(seen);
    await screen.findByRole("heading", { name: "2026년 9월" });

    fireEvent.click(screen.getByRole("button", { name: "다음 달" }));
    expect(await screen.findByRole("heading", { name: "2026년 10월" })).toBeInTheDocument();
    await waitFor(() =>
      expect(seen.some((p) => p.includes("from=2026-10-01&to=2026-10-31"))).toBe(true),
    );

    for (let step = 0; step < 3; step += 1)
      fireEvent.click(screen.getByRole("button", { name: "다음 달" }));
    expect(await screen.findByRole("heading", { name: "2027년 1월" })).toBeInTheDocument();
    await waitFor(() =>
      expect(seen.some((p) => p.includes("from=2027-01-01&to=2027-01-31"))).toBe(true),
    );

    for (let step = 0; step < 5; step += 1)
      fireEvent.click(screen.getByRole("button", { name: "이전 달" }));
    expect(await screen.findByRole("heading", { name: "2026년 8월" })).toBeInTheDocument();
    await waitFor(() =>
      expect(seen.some((p) => p.includes("from=2026-08-01&to=2026-08-31"))).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: "이번 달" }));
    expect(await screen.findByRole("heading", { name: "2026년 9월" })).toBeInTheDocument();
  });

  it("한 달 200건을 넘으면 잘림을 알린다", async () => {
    stubApi({ calendar: ITEMS, calendarTotal: 305 });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));

    expect(await screen.findByRole("status")).toHaveTextContent("전체 305건 중 3건만 표시했습니다");
  });

  it("항목이 없는 달은 그렇다고 말한다", async () => {
    stubApi({ calendar: [] });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));

    expect(
      await screen.findByText(/이 달에는 만료일·유효기간이 있는 항목이 없습니다/),
    ).toBeInTheDocument();
  });

  it("탭 전환 상태가 접근성 속성으로 드러난다", async () => {
    await openCalendar();
    expect(screen.getByRole("tab", { name: "캘린더" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "칸반" })).toHaveAttribute("aria-selected", "false");
  });

  it("응답이 오기 전에는 불러오는 중이라고 말한다 — 빈 달과 구분된다", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubApi({ calendar: ITEMS, calendarGate: gate });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));

    expect(await screen.findByText("불러오는 중…")).toBeInTheDocument();
    expect(screen.queryByText(/만료일·유효기간이 있는 항목이 없습니다/)).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument());
    expect(await screen.findByRole("link", { name: /\[CA\] CNF 통보/ })).toBeInTheDocument();
  });

  it("캘린더 조회가 실패하면 서버 문구로 알린다", async () => {
    stubApi({ calendar: [], calendarStatus: 500 });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("캘린더 오류");
  });

  it("요일 머리와 1일의 칸이 맞다 — 2026-09-01은 화요일이라 앞 빈칸 2개", async () => {
    await openCalendar();
    const first = await screen.findByLabelText("9월 1일");
    const grid = first.parentElement as HTMLElement;
    const children = Array.from(grid.children);
    expect(children.slice(0, 7).map((child) => child.textContent)).toEqual([
      "일",
      "월",
      "화",
      "수",
      "목",
      "금",
      "토",
    ]);
    expect(children.indexOf(first)).toBe(7 + 2); // 머리 7칸 + 앞 빈칸 2칸
    expect((children.length - 7) % 7).toBe(0); // 마지막 주는 빈칸으로 채워 7의 배수
    expect(screen.getByLabelText("9월 30일")).toBeInTheDocument();
    expect(screen.queryByLabelText("9월 31일")).not.toBeInTheDocument();
  });

  it("윤년 2월은 29일까지, 평년 2월은 28일까지이고 조회 범위도 그에 맞다", async () => {
    const seen: string[] = [];
    vi.setSystemTime(new Date("2028-02-15T03:00:00Z")); // KST 2028-02-15 (윤년)
    stubApi({ calendar: [], seen });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));

    expect(await screen.findByRole("heading", { name: "2028년 2월" })).toBeInTheDocument();
    expect(screen.getByLabelText("2월 29일")).toBeInTheDocument();
    expect(screen.queryByLabelText("2월 30일")).not.toBeInTheDocument();
    // 2028-02-01은 화요일 — 앞 빈칸 2개
    const first = screen.getByLabelText("2월 1일");
    expect(Array.from((first.parentElement as HTMLElement).children).indexOf(first)).toBe(7 + 2);
    await waitFor(() =>
      expect(seen.some((path) => path.includes("from=2028-02-01&to=2028-02-29"))).toBe(true),
    );
  });

  it("평년 2월은 28일까지", async () => {
    const seen: string[] = [];
    vi.setSystemTime(new Date("2027-02-10T03:00:00Z"));
    stubApi({ calendar: [], seen });
    renderWithProviders(<AppRoutes />, { route: "/certification-board" });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));

    expect(await screen.findByRole("heading", { name: "2027년 2월" })).toBeInTheDocument();
    expect(screen.getByLabelText("2월 28일")).toBeInTheDocument();
    expect(screen.queryByLabelText("2월 29일")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(seen.some((path) => path.includes("from=2027-02-01&to=2027-02-28"))).toBe(true),
    );
  });

  it("운영 캐시(staleTime 30초)에서도 다시 들어오면 기일을 새로 가져온다", async () => {
    const seen: string[] = [];
    stubApi({ calendar: ITEMS, seen });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    const count = () => seen.filter((path) => path.includes("/deadlines/calendar")).length;

    const first = renderWithProviders(<AppRoutes />, { route: "/certification-board", client });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));
    await screen.findByRole("heading", { name: "2026년 9월" });
    await waitFor(() => expect(count()).toBe(1));
    first.unmount();

    renderWithProviders(<AppRoutes />, { route: "/certification-board", client });
    fireEvent.click(await screen.findByRole("tab", { name: "캘린더" }));
    await waitFor(() => expect(count()).toBeGreaterThanOrEqual(2));
  });
});
