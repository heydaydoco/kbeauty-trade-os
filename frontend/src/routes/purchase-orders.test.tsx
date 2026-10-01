// 발주 목록 — 렌더·필터·페이지·CSV·역할별 버튼·원가 있는 응답/없는 응답·알림/내비 (S3-1 PR-8b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { SENTINELS, poSummary, poSummaryHidden } from "../test/po-fixtures";
import { assertNoLeakOutsideText, captureConsole } from "../test/po-leak";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

beforeEach(() => vi.stubGlobal("crypto", { randomUUID: () => "test-key" }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [
  poSummary(),
  poSummary({ id: 10, doc_number: "PO-2026-0002", status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", po_kind: "OEM_PRODUCTION" }),
  poSummary({ id: 11, doc_number: "PO-2026-0003", status: "CANCELLED" }),
];
const HIDDEN_ROWS = ROWS.map((row) => poSummaryHidden({ id: row.id, doc_number: row.doc_number, status: row.status, po_kind: row.po_kind, oc_received_on: row.oc_received_on }));

const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;
const csvResponse = () =>
  ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])), headers: new Headers() }) as unknown as Response;

describe("발주 목록 — 원가 열람 역할(Full 응답)", () => {
  it("행·상태 배지·구분·OC 일자·합계(서버 문자열+통화)를 보인다", async () => {
    stubFetch(TRADER, [["/v1/purchase-orders", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    expect(await screen.findByRole("heading", { name: "발주 (PO)" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "PO-2026-0001" })).toHaveAttribute("href", "/purchase-orders/9");
    expect(screen.getByRole("columnheader", { name: "합계" })).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0001")).getByText("76543219.87 USD")).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0001")).getByText("발행")).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0002")).getByText("공급사 확인")).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0002")).getByText("OEM 생산 발주")).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0002")).getByText("2026-10-01")).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0003")).getByText("취소")).toBeInTheDocument();
    expect(screen.getByText("전체 3건")).toBeInTheDocument();
  });

  it("Full 목록에서도 화면 글자 외 채널(콘솔·스토리지·URL·속성)로 원가가 새지 않는다", async () => {
    const logs = captureConsole();
    const { calls } = stubFetch(TRADER, [["/v1/purchase-orders", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    await screen.findAllByText("76543219.87 USD");
    assertNoLeakOutsideText(SENTINELS, logs, calls);
  });

  it("무역은 '발주 만들기' 링크가 있고 만들기 화면으로 간다", async () => {
    stubFetch(TRADER, [["/v1/purchase-orders", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    expect(await screen.findByRole("link", { name: "발주 만들기" })).toHaveAttribute("href", "/purchase-orders/new");
  });
});

describe("발주 목록 — 원가 없는 응답(CostHidden): 열도 칸도 없고 깨지지 않는다", () => {
  it("합계 열이 없고 센티널 원가가 화면에 없다 / 조회 전용은 '발주 만들기'가 없다", async () => {
    stubFetch(VIEWER, [["/v1/purchase-orders", "GET", () => jsonResponse(page(HIDDEN_ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    expect(await screen.findByRole("link", { name: "PO-2026-0001" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "합계" })).not.toBeInTheDocument();
    expect(within(rowOf("PO-2026-0001")).getByText("Seoul Supplier Co.")).toBeInTheDocument();
    expect(within(rowOf("PO-2026-0002")).getByText("공급사 확인")).toBeInTheDocument();
    expect(screen.queryByText("USD")).not.toBeInTheDocument();
    for (const sentinel of SENTINELS) expect(document.body.textContent).not.toContain(sentinel);
    expect(screen.queryByRole("link", { name: "발주 만들기" })).not.toBeInTheDocument();
    // 조회 전용도 CSV 버튼은 있다(서버가 역할에 맞는 열로 만든다).
    expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeInTheDocument();
  });
});

describe("발주 목록 — 상태·필터·CSV·페이지", () => {
  it("빈 목록은 안내(무역은 만들기 안내), 조회 실패는 서버 문구를 오류로 보인다", async () => {
    stubFetch(TRADER, [["/v1/purchase-orders", "GET", () => jsonResponse(page([]))]]);
    const view = renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    expect(await screen.findByText(/아직 발주가 없습니다/)).toBeInTheDocument();
    view.unmount();
    stubFetch(TRADER, [
      ["/v1/purchase-orders", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
    expect(screen.queryByText(/아직 발주가 없습니다/)).not.toBeInTheDocument();
  });

  it("상태·구분·검색어·기간 필터는 서버 쿼리로 나가고, CSV는 같은 필터 그대로 내려받는다(전 역할)", async () => {
    const { calls } = stubFetch(VIEWER, [
      ["/v1/purchase-orders/export.csv", "GET", csvResponse],
      ["/v1/purchase-orders", "GET", () => jsonResponse(page(HIDDEN_ROWS))],
    ]);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    await screen.findByText("PO-2026-0001");

    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "ISSUED" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/purchase-orders?status=ISSUED"));
    fireEvent.change(screen.getByLabelText("구분"), { target: { value: "OEM_PRODUCTION" } });
    fireEvent.change(screen.getByLabelText("발주번호·공급사명·SKU 코드"), { target: { value: "Seoul" } });
    fireEvent.change(screen.getByLabelText("증빙일 시작"), { target: { value: "2026-09-01" } });
    const expected = "status=ISSUED&po_kind=OEM_PRODUCTION&q=Seoul&date_from=2026-09-01";
    await waitFor(() => expect(calls.map((c) => c.url)).toContain(`/api/v1/purchase-orders?${expected}`));

    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() =>
      expect(calls.map((c) => c.url)).toContain(`/api/v1/purchase-orders/export.csv?${expected}`),
    );
  });

  it("CSV 실패는 서버 문구를 보인다", async () => {
    stubFetch(TRADER, [
      ["/v1/purchase-orders/export.csv", "GET", () => jsonResponse({ error: { code: "X", message: "건수가 너무 많습니다." } }, 422)],
      ["/v1/purchase-orders", "GET", () => jsonResponse(page(ROWS))],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    await screen.findByText("PO-2026-0001");
    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    expect(await screen.findByText(/건수가 너무 많습니다/)).toBeInTheDocument();
  });

  it("50건을 넘으면 쪽 이동이 2쪽을 요청한다", async () => {
    const { calls } = stubFetch(TRADER, [
      ["/v1/purchase-orders", "GET", () => jsonResponse({ items: ROWS, total: 120, page: 1, size: 50 })],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    await screen.findByText("전체 120건");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/purchase-orders?page=2"));
  });
});

describe("내비", () => {
  it("내비에 발주 항목이 있다(전 역할 조회 — 쓰기 버튼만 역할로 갈린다)", async () => {
    stubFetch(VIEWER, [["/v1/purchase-orders", "GET", () => jsonResponse(page(HIDDEN_ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders" });
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "발주" })).toHaveAttribute("href", "/purchase-orders");
  });
});
