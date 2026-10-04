// 수주(SO) 목록 — 렌더·상태 배지·원천 링크·필터·CSV·쪽 이동·빈/오류 상태·내비·알림 이동 (S3-1 PR-7b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { soSummary } from "../test/so-fixtures";

beforeEach(() => vi.stubGlobal("crypto", { randomUUID: () => "test-key" }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [
  soSummary(),
  soSummary({ id: 10, doc_number: "SO-2026-0002", status: "ON_HOLD", buyer_po_no: null, pi_id: null, pi_doc_number: null }),
  soSummary({ id: 11, doc_number: "SO-2026-0003", status: "CONFIRMED", confirmed_at: "2026-09-30T02:00:00Z" }),
  soSummary({ id: 12, doc_number: "SO-2026-0004", status: "CANCELLED" }),
  soSummary({ id: 13, doc_number: "SO-2026-0005", qt_id: null, qt_doc_number: null, pi_id: null, pi_doc_number: null }),
];

const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;

describe("수주 목록", () => {
  it("행·상태 배지·바이어 PO·원천 링크·합계(서버 문자열)를 보인다", async () => {
    stubFetch(TRADER, [["/v1/sales-orders", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });

    expect(await screen.findByRole("heading", { name: "수주 (SO)" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "SO-2026-0001" })).toHaveAttribute("href", "/sales-orders/9");
    const first = rowOf("SO-2026-0001");
    expect(within(first).getByText("접수")).toBeInTheDocument();
    expect(within(first).getByText("PO-2026-001")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "QT-2026-0001" })).toHaveAttribute("href", "/quotations/7");
    expect(within(first).getByRole("link", { name: "PI-2026-0001" })).toHaveAttribute("href", "/proforma-invoices/5");
    expect(within(first).getByText("75.00 USD")).toBeInTheDocument();
    expect(within(rowOf("SO-2026-0002")).getByText("보류")).toBeInTheDocument();
    expect(within(rowOf("SO-2026-0002")).queryByRole("link", { name: "PI-2026-0001" })).toBeNull();
    expect(within(rowOf("SO-2026-0003")).getByText("확정")).toBeInTheDocument();
    expect(within(rowOf("SO-2026-0004")).getByText("취소")).toBeInTheDocument();
    expect(within(rowOf("SO-2026-0005")).getByText("직접 수주")).toBeInTheDocument();
    expect(screen.getByText("전체 5건")).toBeInTheDocument();
  });

  it("빈 목록은 만드는 방법을 안내하고, 목록 화면에는 'SO 만들기'가 없다", async () => {
    stubFetch(TRADER, [["/v1/sales-orders", "GET", () => jsonResponse(page([]))]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    expect(await screen.findByText(/아직 수주가 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "SO 만들기" })).not.toBeInTheDocument();
  });

  it("조회 실패는 서버 문구를 오류로 보인다(빈 목록과 구분)", async () => {
    stubFetch(TRADER, [["/v1/sales-orders", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
    expect(screen.queryByText(/아직 수주가 없습니다/)).not.toBeInTheDocument();
  });

  it("조회 전용 역할도 목록을 본다", async () => {
    stubFetch(VIEWER, [["/v1/sales-orders", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    expect(await screen.findByRole("link", { name: "SO-2026-0001" })).toBeInTheDocument();
  });

  it("상태·검색어·기간 필터는 서버 쿼리로 나가고, CSV는 같은 필터 그대로 내려받는다", async () => {
    const { calls } = stubFetch(TRADER, [
      ["/v1/sales-orders/export.csv", "GET", () => ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])), headers: new Headers() }) as unknown as Response],
      ["/v1/sales-orders", "GET", () => jsonResponse(page(ROWS))],
    ]);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    await screen.findByText("SO-2026-0001");

    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "ON_HOLD" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/sales-orders?status=ON_HOLD"));
    fireEvent.change(screen.getByLabelText("수주번호·바이어명·바이어 PO"), { target: { value: "PO-2026" } });
    fireEvent.change(screen.getByLabelText("증빙일 시작"), { target: { value: "2026-09-01" } });
    const expected = "/api/v1/sales-orders?status=ON_HOLD&q=PO-2026&date_from=2026-09-01";
    await waitFor(() => expect(calls.map((c) => c.url)).toContain(expected));

    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() =>
      expect(calls.map((c) => c.url)).toContain("/api/v1/sales-orders/export.csv?status=ON_HOLD&q=PO-2026&date_from=2026-09-01"),
    );
  });

  it("CSV 실패는 화면에 오류로 보인다", async () => {
    stubFetch(TRADER, [
      ["/v1/sales-orders/export.csv", "GET", () => jsonResponse({ error: { code: "X", message: "내보내기 실패" } }, 500)],
      ["/v1/sales-orders", "GET", () => jsonResponse(page(ROWS))],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    await screen.findByText("SO-2026-0001");
    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("내보내기 실패");
  });

  // R-21(S3-2 PR-3b) — 첫 선적 뒤 수주는 선적중(IN_SHIPMENT)으로 자동 수렴한다. 필터에 없으면 그 수주를 상태로 못 찾는다.
  it("상태 필터에 '선적중'이 있고 IN_SHIPMENT로 서버에 묻는다 — 선적중 수주는 배지로 보인다", async () => {
    const shipping = soSummary({ id: 14, doc_number: "SO-2026-0014", status: "IN_SHIPMENT", confirmed_at: "2026-09-30T02:00:00Z" });
    const { calls } = stubFetch(TRADER, [["/v1/sales-orders", "GET", () => jsonResponse(page([...ROWS, shipping]))]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    await screen.findByText("SO-2026-0001");
    expect(within(rowOf("SO-2026-0014")).getByText("선적중")).toBeInTheDocument();

    const select = screen.getByLabelText("상태");
    const labels = within(select).getAllByRole("option").map((option) => option.textContent);
    expect(labels).toEqual(["전체", "접수", "확정", "선적중", "보류", "취소"]);
    fireEvent.change(select, { target: { value: "IN_SHIPMENT" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/sales-orders?status=IN_SHIPMENT"));
  });

  it("50건을 넘으면 쪽 이동이 2쪽을 요청한다", async () => {
    const { calls } = stubFetch(TRADER, [["/v1/sales-orders", "GET", () => jsonResponse({ items: ROWS, total: 120, page: 1, size: 50 })]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    await screen.findByText("전체 120건");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/sales-orders?page=2"));
  });

  it("내비에 '수주'가 있고 목록으로 이동한다", async () => {
    stubFetch(TRADER, [["/v1/sales-orders", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders" });
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "수주" })).toHaveAttribute("href", "/sales-orders");
  });
});
