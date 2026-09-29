// 인증 보드 — 칸반 11컬럼·잘림 고지·드래그 비구현 안내·캘린더 월 조회·도과 표시 (S2-3 PR-3 안건 ⑥).
//
// 고정하는 것: ① 칸반 컬럼이 상태 11값을 §5.2 순서·한국어 라벨로 보인다 ② 카드가 요건명·대상·만료일·
// 담당자·도과 배지를 싣고 인증 상세 딥링크로 간다 ③ 200장을 넘으면 총계와 함께 잘림을 알린다(조용한
// 잘림 금지) ④ 캘린더가 KST 기준 이번 달 범위로 서버에 묻고 항목을 날짜 칸에 배치한다 ⑤ 월 이동이
// 새 범위를 묻는다 ⑥ 보드에는 상태를 바꾸는 조작이 없다(전이 통로는 상세 폼 하나).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
    is_overdue: false,
    overdue_days: null,
    ...extra,
  };
}

function stubApi(options: {
  certifications?: Certification[];
  total?: number;
  calendar?: CalendarItem[];
  calendarTotal?: number;
  seen?: string[];
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      options.seen?.push(input);
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(VIEWER));
      if (input.includes("/deadlines/calendar")) {
        const items = options.calendar ?? [];
        return Promise.resolve(
          jsonResponse({ items, total: options.calendarTotal ?? items.length, page: 1, size: 200 }),
        );
      }
      if (input.includes("/v1/certifications")) {
        const items = options.certifications ?? [];
        return Promise.resolve(
          jsonResponse({ items, total: options.total ?? items.length, page: 1, size: 200 }),
        );
      }
      return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, size: 50 }));
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
    expect(document.querySelector("[draggable='true']")).toBeNull();
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
    const ok = screen.getByRole("link", { name: /\[CA\] CNF 통보/ });
    expect(ok.className).not.toContain("bg-red-100");
    expect(ok.getAttribute("aria-label")).not.toContain("도과");
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
});
