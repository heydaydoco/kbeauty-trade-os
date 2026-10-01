// 승인 목록 — 결재함·내 요청 탭·필터·쪽 이동·상태 배지·대결 표시·빈/오류·내비·셸 배지 (S3-1 PR-9b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { CERT_USER, CURRENCY_HANDLER, approval } from "../test/approval-fixtures";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

beforeEach(() => vi.stubGlobal("crypto", { randomUUID: () => "test-key" }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const INBOX = [
  approval(),
  approval({ id: 8, target_id: 13, target_label: "SO-2026-0013", requester_name: "다른 기안자", basis_amount: 5, required_role: "CERT" }),
];
const MINE = [
  approval({ id: 10, status: "APPROVED", requested_by_id: 1, can_decide: false }),
  approval({ id: 11, status: "REJECTED", requested_by_id: 1, can_decide: false }),
  approval({ id: 12, status: "VOIDED", requested_by_id: 1, can_decide: false }),
  approval({ id: 13, status: "WITHDRAWN", requested_by_id: 1, can_decide: false }),
  approval({ id: 14, status: "CONSUMED", requested_by_id: 1, can_decide: false }),
  approval({ id: 15, status: "NEW_STATUS", requested_by_id: 1, can_decide: false }),
];

const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;

function stubList(me: unknown = TRADER, extra: Array<[string, string, () => Response]> = []) {
  return stubFetch(me, [
    ...extra,
    CURRENCY_HANDLER,
    ["scope=mine", "GET", () => jsonResponse(page(MINE))],
    ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 2 })],
    ["scope=inbox", "GET", () => jsonResponse(page(INBOX))],
  ]);
}

describe("결재함", () => {
  it("결재함이 기본 탭이고 요청 행·대상 링크·금액(서버 정수의 자릿수 표시)·역할을 보인다", async () => {
    const { calls } = stubList();
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    expect(await screen.findByRole("heading", { name: "승인" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "승인 #7" })).toHaveAttribute("href", "/approvals/7");
    expect(screen.getByRole("tab", { name: "결재함" })).toHaveAttribute("aria-selected", "true");
    const first = rowOf("승인 #7");
    expect(within(first).getByRole("link", { name: "SO-2026-0012" })).toHaveAttribute("href", "/sales-orders/12");
    expect(within(first).getByText("300.00 USD")).toBeInTheDocument();
    expect(within(first).getByText("결재 대기")).toBeInTheDocument();
    expect(screen.getByText("전체 2건")).toBeInTheDocument();
    expect(calls.map((c) => c.url)).toContain("/api/v1/approvals?scope=inbox");
  });

  it("내 역할이 아닌데 결재할 수 있는 건은 '(대결)'로 표시한다", async () => {
    stubList(CERT_USER);
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    await screen.findByRole("link", { name: "승인 #7" });
    expect(within(rowOf("승인 #7")).getByText("(대결)")).toBeInTheDocument(); // TRADE 요청을 CERT가 대결로
    expect(within(rowOf("승인 #8")).queryByText("(대결)")).not.toBeInTheDocument(); // 본인 역할 CERT
  });

  it("빈 결재함은 안내를, 조회 실패는 서버 문구를 오류로 보인다(구분)", async () => {
    stubFetch(TRADER, [["scope=inbox", "GET", () => jsonResponse(page([]))]]);
    const view = renderWithProviders(<AppRoutes />, { route: "/approvals" });
    expect(await screen.findByText(/결재할 요청이 없습니다/)).toBeInTheDocument();
    view.unmount();
    stubFetch(TRADER, [["scope=inbox", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)]]);
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
    expect(screen.queryByText(/결재할 요청이 없습니다/)).not.toBeInTheDocument();
  });
});

describe("내 요청", () => {
  it("전 상태가 배지(색+글자)로 보이고 모르는 상태는 '기타'다", async () => {
    stubList();
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    await screen.findByRole("link", { name: "승인 #7" });
    fireEvent.click(screen.getByRole("tab", { name: "내 요청" }));
    await screen.findByRole("link", { name: "승인 #10" });
    expect(within(rowOf("승인 #10")).getByText("승인")).toBeInTheDocument();
    expect(within(rowOf("승인 #11")).getByText("반려")).toBeInTheDocument();
    expect(within(rowOf("승인 #12")).getByText("무효")).toBeInTheDocument();
    expect(within(rowOf("승인 #13")).getByText("회수")).toBeInTheDocument();
    expect(within(rowOf("승인 #14")).getByText("사용됨")).toBeInTheDocument();
    expect(within(rowOf("승인 #15")).getByText("기타")).toBeInTheDocument();
  });

  it("상태 필터는 서버 쿼리로 나간다(결재함에는 필터가 없다)", async () => {
    const { calls } = stubList();
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    await screen.findByRole("link", { name: "승인 #7" });
    expect(screen.queryByLabelText("상태")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "내 요청" }));
    fireEvent.change(await screen.findByLabelText("상태"), { target: { value: "REJECTED" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/approvals?scope=mine&status=REJECTED"));
  });

  it("쪽 이동: 전체 건수와 쪽 번호가 보인다(페이지 50)", async () => {
    stubFetch(TRADER, [
      CURRENCY_HANDLER,
      ["scope=inbox", "GET", () => jsonResponse({ items: INBOX, total: 120, page: 1, size: 50 })],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    expect(await screen.findByText("전체 120건")).toBeInTheDocument();
    expect(screen.getByText("1/3쪽")).toBeInTheDocument();
  });
});

describe("셸 — 내비·결재함 배지", () => {
  it("비조회 역할은 결재함·결재선·대결 내비와 대기 건수 배지를 본다", async () => {
    stubList();
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    const nav = await screen.findByRole("navigation");
    expect(await within(nav).findByRole("link", { name: "결재함 2" })).toHaveAttribute("href", "/approvals");
    expect(within(nav).getByRole("link", { name: "결재선" })).toHaveAttribute("href", "/approval-lines");
    expect(within(nav).getByRole("link", { name: "대결" })).toHaveAttribute("href", "/delegations");
  });

  it("건수 0이면 배지 숫자가 없고, 배지 조회 실패에도 셸은 그대로다", async () => {
    stubFetch(TRADER, [["/v1/approvals/inbox-count", "GET", () => jsonResponse({ error: { code: "X", message: "오류" } }, 500)]]);
    renderWithProviders(<AppRoutes />, { route: "/approvals" });
    const nav = await screen.findByRole("navigation");
    expect(await within(nav).findByRole("link", { name: "결재함" })).toBeInTheDocument();
  });

  it("조회 전용 역할에는 승인 메뉴가 없고 배지 API를 부르지 않는다", async () => {
    const { calls } = stubFetch(VIEWER, [["/v1/sales-orders", "GET", () => jsonResponse(page([]))]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    const nav = await screen.findByRole("navigation");
    await screen.findByRole("heading", { name: "수주 (SO)" });
    expect(within(nav).queryByRole("link", { name: /결재함/ })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "결재선" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/inbox-count"))).toBe(false);
  });
});
