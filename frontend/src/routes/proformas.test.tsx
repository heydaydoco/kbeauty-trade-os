// PI 목록 — 렌더·필터·페이지·CSV·빈/오류 상태 (S3-1 PR-6b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { piSummary } from "../test/pi-fixtures";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

beforeEach(() => vi.stubGlobal("crypto", { randomUUID: () => "test-key" }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [
  piSummary(),
  piSummary({ id: 6, doc_number: "PI-2026-0002", status: "ISSUED", is_lapsed: true }),
  piSummary({ id: 7, doc_number: "PI-2026-0003", status: "PARTIALLY_PAID" }),
  piSummary({ id: 8, doc_number: "PI-2026-0004", status: "CANCELLED" }),
];

const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;

describe("PI 목록", () => {
  it("행·상태 배지·만료 배지·합계(서버 문자열)·원천 견적 링크를 보인다", async () => {
    stubFetch(TRADER, [["/v1/proforma-invoices", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });

    expect(await screen.findByRole("heading", { name: "PI (선수금 청구서)" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "PI-2026-0001" })).toHaveAttribute("href", "/proforma-invoices/5");
    expect(within(rowOf("PI-2026-0001")).getByRole("link", { name: "QT-2026-0001" })).toHaveAttribute("href", "/quotations/7");
    expect(within(rowOf("PI-2026-0001")).getAllByText("75.00 USD").length).toBe(1);
    // 만료 배지는 유효기간이 지난 발행 PI 행에만.
    expect(within(rowOf("PI-2026-0002")).getByText("유효기간 경과")).toBeInTheDocument();
    expect(within(rowOf("PI-2026-0001")).queryByText("유효기간 경과")).toBeNull();
    expect(within(rowOf("PI-2026-0003")).getByText("일부입금")).toBeInTheDocument();
    expect(within(rowOf("PI-2026-0004")).getByText("취소")).toBeInTheDocument();
    expect(screen.getByText("전체 4건")).toBeInTheDocument();
  });

  it("빈 목록은 만드는 방법을 안내하고, 목록 화면에는 'PI 만들기'가 없다", async () => {
    stubFetch(TRADER, [["/v1/proforma-invoices", "GET", () => jsonResponse(page([]))]]);
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });
    expect(await screen.findByText(/아직 PI가 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "PI 만들기" })).not.toBeInTheDocument();
  });

  it("조회 실패는 서버 문구를 오류로 보인다(빈 목록과 구분)", async () => {
    stubFetch(TRADER, [
      ["/v1/proforma-invoices", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
    expect(screen.queryByText(/아직 PI가 없습니다/)).not.toBeInTheDocument();
  });

  it("조회 전용 역할도 목록을 본다(서버 authz: 전 역할 조회)", async () => {
    stubFetch(VIEWER, [["/v1/proforma-invoices", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });
    expect(await screen.findByRole("link", { name: "PI-2026-0001" })).toBeInTheDocument();
  });

  it("상태·검색어·기간 필터는 서버 쿼리로 나가고, CSV는 같은 필터 그대로 내려받는다", async () => {
    const { calls } = stubFetch(TRADER, [
      ["/v1/proforma-invoices/export.csv", "GET", () => ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])), headers: new Headers() }) as unknown as Response],
      ["/v1/proforma-invoices", "GET", () => jsonResponse(page(ROWS))],
    ]);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });
    await screen.findByText("PI-2026-0001");

    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "PAID" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/proforma-invoices?status=PAID"));
    fireEvent.change(screen.getByLabelText("PI번호·바이어명"), { target: { value: "ABC" } });
    fireEvent.change(screen.getByLabelText("증빙일 시작"), { target: { value: "2026-09-01" } });
    const expected = "/api/v1/proforma-invoices?status=PAID&q=ABC&date_from=2026-09-01";
    await waitFor(() => expect(calls.map((c) => c.url)).toContain(expected));

    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() =>
      expect(calls.map((c) => c.url)).toContain("/api/v1/proforma-invoices/export.csv?status=PAID&q=ABC&date_from=2026-09-01"),
    );
  });

  it("50건을 넘으면 쪽 이동이 2쪽을 요청한다", async () => {
    const { calls } = stubFetch(TRADER, [
      ["/v1/proforma-invoices", "GET", () => jsonResponse({ items: ROWS, total: 120, page: 1, size: 50 })],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });
    await screen.findByText("전체 120건");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/proforma-invoices?page=2"));
  });
});
