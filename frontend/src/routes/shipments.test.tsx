// 선적 목록 `/shipments` — 행·배지·원천 링크·국가·수입 합계 '—'·필터(서버 쿼리)·주소 진입·CSV·빈/오류·내비 (S3-2 PR-3b, 그룹 A·K).
// fetch 스텁은 정확 URL·메서드 일치(stubGateFetch) — 필터 직렬화 오류는 404로 터진다.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { importShipmentListItem, shipmentListItem } from "../test/shipment-fixtures";

beforeEach(() => vi.stubGlobal("crypto", { randomUUID: () => "test-key" }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [
  shipmentListItem(),
  shipmentListItem({ id: 32, doc_number: "SH-2026-0002", status: "RELEASE_ORDERED", origin_country_code: "KR", dest_country_code: "JP" }),
  shipmentListItem({ id: 33, doc_number: "SH-2026-0003", status: "CANCELLED", line_count: 2 }),
  // 수입선적 — 백엔드 ImportShipmentListItem 그대로(통화·합계 키 자체가 없다 — PR-5a, 0·"0.00"으로 흉내 내지 않는다).
  importShipmentListItem({ id: 34, doc_number: "SH-2026-0004", counterparty_name: "공급사 A" }),
];

const LIST: GateHandler = ["/v1/shipments", "GET", () => jsonResponse(page(ROWS))];
const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;

describe("선적 목록", () => {
  it("ETD·ETA 열(부채 R-3b-3 — PR-4a 유효값): 날짜 문자열 그대로 + 실적/예정 표지, 값 없으면 '—'", async () => {
    stubGateFetch(TRADER, [
      [
        "/v1/shipments",
        "GET",
        () =>
          jsonResponse(
            page([
              shipmentListItem({
                etd: { value: "2026-10-03", basis: "ACTUAL" },
                eta: { value: "2026-10-20", basis: "PLANNED" },
              }),
              shipmentListItem({ id: 32, doc_number: "SH-2026-0002" }),
            ]),
          ),
      ],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    await screen.findByRole("link", { name: "SH-2026-0001" });
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual(expect.arrayContaining(["ETD", "ETA"]));
    const cells = within(rowOf("SH-2026-0001")).getAllByRole("cell");
    expect(cells[headers.indexOf("ETD")]).toHaveTextContent("2026-10-03 실적");
    expect(cells[headers.indexOf("ETA")]).toHaveTextContent("2026-10-20 예정");
    const empty = within(rowOf("SH-2026-0002")).getAllByRole("cell");
    expect(empty[headers.indexOf("ETD")]).toHaveTextContent(/^—$/);
    expect(empty[headers.indexOf("ETA")]).toHaveTextContent(/^—$/);
  });

  it("행·상태 배지·구분·원천 링크·출발→도착·라인 수·합계(서버 문자열)·담당을 보인다", async () => {
    stubGateFetch(TRADER, [LIST]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });

    expect(await screen.findByRole("heading", { name: "선적" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "SH-2026-0001" })).toHaveAttribute("href", "/shipments/31");
    const first = rowOf("SH-2026-0001");
    expect(within(first).getByText("계획")).toBeInTheDocument();
    expect(within(first).getByText("수출")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "SO-2026-0001" })).toHaveAttribute("href", "/sales-orders/9");
    expect(within(first).getByText("2026-10-04")).toBeInTheDocument(); // 날짜 문자열 그대로
    expect(within(first).getByLabelText("출발국 KR 도착국 US")).toHaveAttribute("title", "대한민국 → 미국");
    expect(within(first).getByText("50.00 USD")).toBeInTheDocument();
    expect(within(first).getByText("무역 담당")).toBeInTheDocument();
    expect(within(rowOf("SH-2026-0002")).getByText("출고지시")).toBeInTheDocument();
    expect(within(rowOf("SH-2026-0003")).getByText("취소")).toBeInTheDocument();
    expect(screen.getByText("전체 4건")).toBeInTheDocument();
  });

  it("수입선적은 금액 축이 없어 합계를 '—'로(0.00으로 그리지 않는다), 원천은 발주 링크", async () => {
    stubGateFetch(TRADER, [LIST]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    await screen.findByText("SH-2026-0004");
    const row = rowOf("SH-2026-0004");
    expect(within(row).getByText("수입")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "PO-2026-0004" })).toHaveAttribute("href", "/purchase-orders/4");
    expect(within(row).queryByText(/0\.00/)).not.toBeInTheDocument();
    // 합계 칸 자체가 '—'(PR-4b가 ETD·ETA 열을 더해 같은 행에 '—'가 여럿 — 칸을 머리글 위치로 찾는다).
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    const cells = within(row).getAllByRole("cell");
    expect(cells[headers.indexOf("합계")]).toHaveTextContent(/^—$/);
  });

  it("목록에는 '선적 만들기'가 없고, 빈 목록은 수주 상세에서 만든다고 안내한다", async () => {
    stubGateFetch(TRADER, [["/v1/shipments", "GET", () => jsonResponse(page([]))]]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    expect(await screen.findByText(/아직 선적이 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /선적 만들기/ })).not.toBeInTheDocument();
  });

  it("조회 실패는 서버 문구를 오류로 보인다(빈 목록과 구분)", async () => {
    stubGateFetch(TRADER, [["/v1/shipments", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)]]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
    expect(screen.queryByText(/아직 선적이 없습니다/)).not.toBeInTheDocument();
  });

  it("상태·검색어 필터는 서버 쿼리로 나가고, CSV는 같은 조건 그대로 내려받는다", async () => {
    const { calls } = stubGateFetch(TRADER, [
      LIST,
      ["/v1/shipments?status=RELEASE_ORDERED", "GET", () => jsonResponse(page([ROWS[1]!]))],
      ["/v1/shipments?status=RELEASE_ORDERED&q=ABC", "GET", () => jsonResponse(page([ROWS[1]!]))],
      [
        "/v1/shipments/export.csv?status=RELEASE_ORDERED&q=ABC",
        "GET",
        () => ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])), headers: new Headers() }) as unknown as Response,
      ],
    ]);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    await screen.findByText("SH-2026-0001");

    const select = screen.getByLabelText("상태");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["전체", "계획", "출고지시(수입: 선적 확정)", "취소"]);
    fireEvent.change(select, { target: { value: "RELEASE_ORDERED" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/shipments?status=RELEASE_ORDERED"));
    fireEvent.change(screen.getByLabelText("선적번호·거래 상대"), { target: { value: " ABC " } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/shipments?status=RELEASE_ORDERED&q=ABC"));

    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/shipments/export.csv?status=RELEASE_ORDERED&q=ABC"));
  });

  it("주소의 ?q=로 들어오면(수주 취소 409 '먼저 취소할 선적' 링크) 그 조건으로 조회한다", async () => {
    const { calls } = stubGateFetch(TRADER, [["/v1/shipments?q=SH-2026-0001", "GET", () => jsonResponse(page([ROWS[0]!]))]]);
    renderWithProviders(<AppRoutes />, { route: "/shipments?q=SH-2026-0001" });
    expect(await screen.findByRole("link", { name: "SH-2026-0001" })).toBeInTheDocument();
    expect(screen.getByLabelText("선적번호·거래 상대")).toHaveValue("SH-2026-0001");
    expect(calls.map((c) => c.url)).not.toContain("/api/v1/shipments");
  });

  it("50건을 넘으면 쪽 이동이 2쪽을 요청한다", async () => {
    const { calls } = stubGateFetch(TRADER, [
      ["/v1/shipments", "GET", () => jsonResponse({ items: ROWS, total: 120, page: 1, size: 50 })],
      ["/v1/shipments?page=2", "GET", () => jsonResponse({ items: ROWS, total: 120, page: 2, size: 50 })],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    await screen.findByText("전체 120건");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/shipments?page=2"));
  });

  it("조회 전용 역할도 목록을 본다, 셸 내비 '선적'은 '발주'와 '휴일 캘린더' 사이", async () => {
    stubGateFetch(VIEWER, [LIST]);
    renderWithProviders(<AppRoutes />, { route: "/shipments" });
    expect(await screen.findByRole("link", { name: "SH-2026-0001" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation");
    const labels = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(within(nav).getByRole("link", { name: "선적" })).toHaveAttribute("href", "/shipments");
    const at = labels.indexOf("선적");
    expect(labels[at - 1]).toBe("발주");
    expect(labels[at + 1]).toBe("휴일 캘린더");
  });
});
