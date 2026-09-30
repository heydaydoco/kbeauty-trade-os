// 견적 목록 — 렌더·필터·페이지·CSV·쓰기 버튼 노출·알림 이동 (S3-1 PR-5b).

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { summary, stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

beforeEach(() => vi.stubGlobal("crypto", { randomUUID: () => "test-key" }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [
  summary(),
  summary({ id: 8, doc_number: "QT-2026-0002", status: "ISSUED", is_lapsed: true, total_text: "1,0" }),
  summary({ id: 9, doc_number: "QT-2026-0003", status: "CANCELLED" }),
];

describe("견적 목록", () => {
  it("행·상태 배지·합계(서버 문자열)·유효기간 경과 배지를 보인다", async () => {
    stubFetch(TRADER, [["/v1/quotations", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });

    expect(await screen.findByRole("heading", { name: "견적" })).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: "QT-2026-0001" });
    expect(link).toHaveAttribute("href", "/quotations/7");
    expect(screen.getAllByText("125.00 USD").length).toBeGreaterThan(0);
    expect(screen.getByText("유효기간 경과")).toBeInTheDocument();
    expect(screen.getAllByText("취소").length).toBeGreaterThan(1); // 배지+필터 옵션
    expect(screen.getByText("전체 3건")).toBeInTheDocument();
  });

  it("빈 목록은 안내, 오류는 오류로 구분한다", async () => {
    stubFetch(TRADER, [["/v1/quotations", "GET", () => jsonResponse(page([]))]]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    expect(await screen.findByText(/아직 견적이 없습니다/)).toBeInTheDocument();
  });

  it("목록 조회 실패는 서버 문구를 오류로 보인다", async () => {
    stubFetch(TRADER, [
      ["/v1/quotations", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
  });

  it("상태 필터는 서버 쿼리(status)로 나가고 1쪽으로 돌아간다", async () => {
    const { calls } = stubFetch(TRADER, [["/v1/quotations", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    await screen.findByText("QT-2026-0001");

    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "ISSUED" } });
    await waitFor(() => {
      expect(calls.map((c) => c.url)).toContain("/api/v1/quotations?status=ISSUED");
    });
    fireEvent.change(screen.getByLabelText("견적번호·바이어명"), { target: { value: "ABC" } });
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("status=ISSUED&q=ABC"))).toBe(true);
    });
  });

  it("페이지 이동은 page 파라미터를 붙인다(기본 50)", async () => {
    const { calls } = stubFetch(TRADER, [
      [
        "/v1/quotations",
        "GET",
        () => jsonResponse({ items: ROWS, total: 120, page: 1, size: 50 }),
      ],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    await screen.findByText("전체 120건");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("page=2"))).toBe(true);
    });
  });

  it("CSV 내보내기는 지금 필터 그대로 export.csv를 부른다", async () => {
    const createObjectURL = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
    const { calls } = stubFetch(TRADER, [
      ["/v1/quotations/export.csv", "GET", () => ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["a"])), headers: new Headers() }) as Response],
      ["/v1/quotations", "GET", () => jsonResponse(page(ROWS))],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    await screen.findByText("QT-2026-0001");
    fireEvent.change(screen.getByLabelText("상태"), { target: { value: "DRAFT" } });
    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() => {
      expect(calls.some((c) => c.url === "/api/v1/quotations/export.csv?status=DRAFT")).toBe(true);
    });
  });

  it("작성 버튼은 무역·관리자에게만 보인다(서버가 다시 막는다)", async () => {
    stubFetch(VIEWER, [["/v1/quotations", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    await screen.findByText("QT-2026-0001");
    expect(screen.queryByRole("button", { name: "견적 작성" })).not.toBeInTheDocument();
  });

  it("무역은 작성 다이얼로그에서 필수 3값을 채워야 초안을 만들 수 있다", async () => {
    const { calls } = stubFetch(TRADER, [
      ["/v1/quotations", "POST", () => jsonResponse(summary({ id: 21 }), 201)],
      ["/v1/quotations", "GET", () => jsonResponse(page(ROWS))],
      ["/v1/markets", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국" }]))],
      ["/v1/system/currencies", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }]))],
      ["/v1/partners", "GET", () => jsonResponse(page([{ id: 3, partner_code: "P-3", name_ko: "ABC 무역" }]))],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    await screen.findByText("QT-2026-0001");
    fireEvent.click(screen.getByRole("button", { name: "견적 작성" }));
    const dialog = await screen.findByRole("dialog", { name: "견적 작성" });
    const submit = screen.getByRole("button", { name: "초안 만들기" });
    expect(submit).toBeDisabled();

    fireEvent.focus(screen.getByRole("combobox", { name: "바이어" }));
    fireEvent.click(await screen.findByRole("option", { name: "ABC 무역 (P-3)" }));
    fireEvent.change(await screen.findByLabelText("목적지 시장"), { target: { value: "US" } });
    await screen.findByRole("option", { name: "USD" });
    fireEvent.change(screen.getByLabelText("통화"), { target: { value: "USD" } });
    expect(dialog).toBeInTheDocument();
    fireEvent.click(submit);
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.url === "/api/v1/quotations");
      expect(post?.body).toEqual({ buyer_partner_id: 3, dest_market_code: "US", currency: "USD" });
      expect(post?.headers["Idempotency-Key"]).toBe("test-key");
    });
  });
});

describe("내비·알림", () => {
  it("내비에 견적 항목이 있다(전 역할 조회 — 쓰기 버튼만 역할로 갈린다)", async () => {
    stubFetch(VIEWER, [["/v1/quotations", "GET", () => jsonResponse(page(ROWS))]]);
    renderWithProviders(<AppRoutes />, { route: "/quotations" });
    const nav = await screen.findByRole("navigation");
    expect(nav).toHaveTextContent("견적");
  });

  it("검산 알림(quotations)은 견적 상세로 이동하는 링크가 된다", async () => {
    stubFetch(TRADER, [
      [
        "/v1/alerts",
        "GET",
        () =>
          jsonResponse(
            page([
              {
                id: 1,
                alert_rule_id: null,
                recipient_user_id: 1,
                title: "견적 합계 불일치",
                body: null,
                severity: "CRITICAL",
                dedup_key: "k1",
                acknowledged_at: null,
                entity_type: "quotations",
                entity_id: 7,
              },
              {
                id: 2,
                alert_rule_id: null,
                recipient_user_id: 1,
                title: "인증",
                body: null,
                severity: "INFO",
                dedup_key: "k2",
                acknowledged_at: null,
                entity_type: "certifications",
                entity_id: 3,
              },
            ]),
          ),
      ],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/alerts" });
    const link = await screen.findByRole("link", { name: "quotations #7 열기" });
    expect(link).toHaveAttribute("href", "/quotations/7");
    // 표에 없는 종류는 링크 없이 글자만.
    expect(screen.getByText("certifications #3")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /certifications/ })).not.toBeInTheDocument();
  });
});
