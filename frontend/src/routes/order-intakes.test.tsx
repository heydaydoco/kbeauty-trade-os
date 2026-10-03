// 오더 인테이크 목록 — 필터·페이지·po_occupied 표지(서버 값 그대로)·마스킹 역할 번호 비노출·역할별 버튼·내비 (S3-1 PR-13b).
// fetch 스텁은 정확 URL·메서드 일치(`stubGateFetch`) — 오라우팅은 404로 터진다.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateHandler } from "../test/gate-fixtures";
import { intakeSummary } from "../test/intake-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const USERS: GateHandler = ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }, { id: 2, display_name: "다른 담당" }]))];
const ALERTS: GateHandler = ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })];
const INBOX: GateHandler = ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })];

function open(rows = [intakeSummary()], me: unknown = TRADER, extra: GateHandler[] = []) {
  const stub = stubGateFetch(me, [...extra, ["/v1/order-intakes", "GET", () => jsonResponse(page(rows))], USERS, ALERTS, INBOX]);
  renderWithProviders(<AppRoutes />, { route: "/orders/intakes" });
  return stub;
}

describe("인테이크 목록", () => {
  it("행: PO번호 링크·상태 배지·바이어·시장·라인 수·합계(서버 문자열)·담당자 이름·접수일시(KST)", async () => {
    open();
    const row = (await screen.findByRole("link", { name: "PO-2026-001" })).closest("tr") as HTMLElement;
    expect(screen.getByRole("link", { name: "PO-2026-001" })).toHaveAttribute("href", "/orders/intakes/21");
    const r = within(row);
    expect(r.getByText("대기")).toBeInTheDocument();
    expect(r.getByText("ABC Trading")).toBeInTheDocument();
    expect(r.getByText("US")).toBeInTheDocument();
    expect(r.getByText("125.00 USD")).toBeInTheDocument();
    expect(r.getByText("무역 담당")).toBeInTheDocument();
    expect(r.getByText(/KST/)).toBeInTheDocument();
    expect(await screen.findByText("전체 1건")).toBeInTheDocument();
  });

  it("상태 3종 라벨 — 확정 행에는 생성된 수주 링크, 거부 행은 취소선 배지", async () => {
    open([
      intakeSummary({ id: 1, status: "CONFIRMED", buyer_po_no: "PO-C", sales_order_id: 77 }),
      intakeSummary({ id: 2, status: "REJECTED", buyer_po_no: "PO-R" }),
    ]);
    await screen.findByRole("link", { name: "PO-C" });
    const table = within(screen.getByRole("table"));
    expect(table.getByText("확정(수주 접수됨)")).toBeInTheDocument();
    expect(table.getByRole("link", { name: "생성된 수주 보기" })).toHaveAttribute("href", "/sales-orders/77");
    expect(table.getByText("거부")).toHaveClass("line-through");
  });

  it("po_occupied 표지 — 서버가 준 번호·상태만 표시하고 막힘을 알린다", async () => {
    open([intakeSummary({ po_occupied: { kind: "SALES_ORDER", doc_number: "SO-2026-0009", status: "RECEIVED" } })]);
    expect(await screen.findByText("PO 점유됨 — 확정 막힘")).toBeInTheDocument();
    expect(screen.getByText(/수주 SO-2026-0009 \(접수\)가 같은 바이어 PO번호를 점유 중/)).toBeInTheDocument();
  });

  it("마스킹 역할(번호 null) — 표지는 보이되 번호·상태는 지어내지 않는다", async () => {
    open([intakeSummary({ po_occupied: { kind: "SALES_ORDER", doc_number: null, status: null } })], VIEWER);
    expect(await screen.findByText("PO 점유됨 — 확정 막힘")).toBeInTheDocument();
    expect(screen.getByText(/문서 번호는 무역·관리자만 확인/)).toBeInTheDocument();
    expect(screen.queryByText(/SO-\d/)).not.toBeInTheDocument();
  });

  it("po_occupied가 null이면 표지가 없다(프런트가 점유를 추정하지 않는다)", async () => {
    open([intakeSummary({ po_occupied: null })]);
    await screen.findByRole("link", { name: "PO-2026-001" });
    expect(screen.queryByText("PO 점유됨 — 확정 막힘")).not.toBeInTheDocument();
  });

  it("복제 재접수 표시", async () => {
    open([intakeSummary({ copied_from_so_id: 12 })]);
    expect(await screen.findByText("복제 재접수(원본 수주 #12)")).toBeInTheDocument();
  });

  it("상태 필터·검색어·담당자 필터가 서버 쿼리로 간다(1쪽부터)", async () => {
    const stub = stubGateFetch(TRADER, [
      ["/v1/order-intakes", "GET", () => jsonResponse(page([intakeSummary()]))],
      ["/v1/order-intakes?status=PENDING", "GET", () => jsonResponse(page([intakeSummary({ buyer_po_no: "ONLY-PENDING" })]))],
      ["/v1/order-intakes?status=PENDING&assignee_id=2", "GET", () => jsonResponse(page([intakeSummary({ buyer_po_no: "BY-ASSIGNEE" })]))],
      ["/v1/order-intakes?status=PENDING&assignee_id=2&q=abc", "GET", () => jsonResponse(page([intakeSummary({ buyer_po_no: "BY-Q" })]))],
      USERS,
      ALERTS,
      INBOX,
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/intakes" });
    await screen.findByRole("link", { name: "PO-2026-001" });
    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "PENDING" } });
    await screen.findByRole("link", { name: "ONLY-PENDING" });
    await screen.findByRole("option", { name: "다른 담당" });
    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "2" } });
    await screen.findByRole("link", { name: "BY-ASSIGNEE" });
    fireEvent.change(screen.getByLabelText("바이어 PO번호·바이어명"), { target: { value: " abc " } });
    await screen.findByRole("link", { name: "BY-Q" });
    expect(stub.calls.some((c) => c.url === "/api/v1/order-intakes?status=PENDING&assignee_id=2&q=abc")).toBe(true);
  });

  it("2쪽 이동 — page 파라미터를 붙여 서버에 요청한다(기본 50)", async () => {
    stubGateFetch(TRADER, [
      ["/v1/order-intakes", "GET", () => jsonResponse({ items: [intakeSummary({ buyer_po_no: "P-1쪽" })], total: 120, page: 1, size: 50 })],
      ["/v1/order-intakes?page=2", "GET", () => jsonResponse({ items: [intakeSummary({ id: 99, buyer_po_no: "P-2쪽" })], total: 120, page: 2, size: 50 })],
      USERS,
      ALERTS,
      INBOX,
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/intakes" });
    await screen.findByRole("link", { name: "P-1쪽" });
    expect(screen.getByText("전체 120건")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await screen.findByRole("link", { name: "P-2쪽" });
  });

  it("목록은 아직 서버에 매핑 요약이 없다 — 화면이 매핑 상태를 지어내지 않는다", async () => {
    open();
    await screen.findByRole("link", { name: "PO-2026-001" });
    expect(screen.queryByText("미매핑")).not.toBeInTheDocument();
    expect(screen.queryByText("재해석 필요")).not.toBeInTheDocument();
  });

  it("빈 목록과 필터 빈 결과를 다른 문구로 보인다", async () => {
    stubGateFetch(TRADER, [
      ["/v1/order-intakes", "GET", () => jsonResponse(page([]))],
      ["/v1/order-intakes?status=REJECTED", "GET", () => jsonResponse(page([]))],
      USERS,
      ALERTS,
      INBOX,
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/intakes" });
    expect(await screen.findByText("아직 접수된 오더 인테이크가 없습니다.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "REJECTED" } });
    expect(await screen.findByText("조건에 맞는 인테이크가 없습니다. 필터를 바꿔 보세요.")).toBeInTheDocument();
    expect(screen.queryByText("아직 접수된 오더 인테이크가 없습니다.")).not.toBeInTheDocument();
  });

  it("서버 오류는 한국어 message를 그대로 보인다(빈 목록과 다름)", async () => {
    stubGateFetch(TRADER, [
      ["/v1/order-intakes", "GET", () => jsonResponse({ error: { code: "X", message: "서버가 응답하지 못했습니다.", detail: {} } }, 500)],
      USERS,
      ALERTS,
      INBOX,
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/intakes" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버가 응답하지 못했습니다.");
  });
});

describe("역할별 화면", () => {
  it("쓰기 역할(무역)은 '수동 등록' 링크와 담당자 필터가 보인다", async () => {
    open();
    expect(await screen.findByRole("link", { name: "수동 등록" })).toHaveAttribute("href", "/orders/intakes/new");
    expect(screen.getByLabelText("담당자")).toBeInTheDocument();
  });

  it("읽기 전용 역할은 등록 링크·담당자 필터가 없고 담당자 목록 API를 부르지 않는다(403 소음 방지)", async () => {
    const stub = open([intakeSummary()], VIEWER);
    await screen.findByRole("link", { name: "PO-2026-001" });
    expect(screen.queryByRole("link", { name: "수동 등록" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("담당자")).not.toBeInTheDocument();
    expect(stub.calls.some((c) => c.url.includes("/v1/users/lookup"))).toBe(false);
    // 담당자 이름을 모르면 번호로 보인다.
    expect(screen.getByText("담당자 #1")).toBeInTheDocument();
  });

  it("셸 내비에 '주문 접수(인테이크)' 항목이 열람 전 역할에게 보인다", async () => {
    open([intakeSummary()], VIEWER);
    const nav = await screen.findByRole("link", { name: "주문 접수(인테이크)" });
    expect(nav).toHaveAttribute("href", "/orders/intakes");
    await waitFor(() => expect(nav).toHaveClass("font-semibold"));
  });
});
