// 오더 보드 화면 — 열·카드 렌더·더 보기·역할별 숨김·필터 직렬화·422 필드 오류·CSV (S3-1 PR-15b, 테스트 그룹 A).
// fetch 스텁은 정확 URL·메서드 일치(`stubGateFetch`) — 오라우팅·필터 직렬화 오류는 404로 터진다.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { SO_CONFIRMED_CARD, baseHandlers, board, boardCard } from "../test/board-fixtures";
import { stubGateFetch, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, renderWithProviders } from "../test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const BOARD: GateHandler = ["/v1/order-board", "GET", () => jsonResponse(board())];

function open(me: unknown = TRADER, extra: GateHandler[] = []) {
  const stub = stubGateFetch(me, [...extra, BOARD, ...baseHandlers()]);
  renderWithProviders(<AppRoutes />, { route: "/orders/board" });
  return stub;
}

const columnOf = (label: string) => screen.getByRole("region", { name: `${label} 열` });

describe("오더 보드 — 열·카드", () => {
  it("서버가 준 5열을 순서대로(선적중이 마지막), 열 제목에 total, 카드에 번호 링크·바이어·PO·합계(서버 문자열)·경과일·담당자", async () => {
    open();
    await screen.findByRole("link", { name: "IN-21" });
    const regions = screen.getAllByRole("region").filter((r) => r.getAttribute("aria-label")?.endsWith(" 열"));
    expect(regions.map((r) => r.getAttribute("aria-label"))).toEqual([
      "인테이크 대기 열",
      "수주 접수 열",
      "수주 보류 열",
      "수주 확정 열",
      "선적중 열",
    ]);
    // 선적중 카드도 다른 SO 카드처럼 수주 상세로 이어진다(보드에서 조용히 사라지지 않는다 — S3-2 PR-3a 5열).
    const shipping = within(columnOf("선적중"));
    expect(shipping.getByRole("link", { name: "SO-2026-0014" })).toHaveAttribute("href", "/sales-orders/14");
    expect(shipping.getByText("Ship Co")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "IN-21" })).toHaveAttribute("href", "/orders/intakes/21");
    expect(screen.getByRole("link", { name: "SO-2026-0011" })).toHaveAttribute("href", "/sales-orders/11");
    const hold = within(columnOf("수주 보류"));
    expect(hold.getByText("XYZ Corp")).toBeInTheDocument();
    expect(hold.getByText("PO PO-ABC-1")).toBeInTheDocument();
    expect(hold.getByText("125.00 USD")).toBeInTheDocument();
    expect(hold.getByText(/접수 후/)).toHaveTextContent("접수 후 4일");
    expect(hold.getByText("담당 무역 담당")).toBeInTheDocument();
    expect(hold.getByText(/수정 .*KST/)).toBeInTheDocument();
    expect(screen.getByText(/기준 시각 .*KST/)).toBeInTheDocument();
  });

  it("has_more 열은 잘림 고지와 '더 보기' — 드릴다운은 같은 필터+stage로 Page를 받아 쪽 이동한다", async () => {
    const many = Array.from({ length: 3 }, (_, i) => boardCard({ id: 100 + i, ref_label: `SO-C-${i}` }));
    const stub = stubGateFetch(TRADER, [
      ["/v1/order-board", "GET", () => jsonResponse(board({ SO_CONFIRMED: { items: many, total: 120, has_more: true } }))],
      [
        "/v1/order-board/items?stage=SO_CONFIRMED",
        "GET",
        () => jsonResponse({ items: [boardCard({ id: 500, ref_label: "SO-DRILL-1" })], total: 120, page: 1, size: 50 }),
      ],
      [
        "/v1/order-board/items?stage=SO_CONFIRMED&page=2",
        "GET",
        () => jsonResponse({ items: [boardCard({ id: 600, ref_label: "SO-DRILL-2" })], total: 120, page: 2, size: 50 }),
      ],
      ...baseHandlers(),
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    await screen.findByRole("link", { name: "SO-C-0" });
    const confirmed = within(columnOf("수주 확정"));
    expect(confirmed.getByRole("heading")).toHaveTextContent("수주 확정 120");
    expect(confirmed.getByRole("status")).toHaveTextContent("전체 120건 중 3건만 표시했습니다.");
    // 다른 열은 잘림 고지·더 보기가 없다.
    expect(within(columnOf("수주 접수")).queryByRole("button", { name: /더 보기/ })).not.toBeInTheDocument();

    fireEvent.click(confirmed.getByRole("button", { name: "수주 확정 더 보기" }));
    const drill = await screen.findByRole("region", { name: "수주 확정 전체 보기" });
    expect(await within(drill).findByRole("link", { name: "SO-DRILL-1" })).toBeInTheDocument();
    expect(within(drill).getByText("전체 120건")).toBeInTheDocument();
    fireEvent.click(within(drill).getByRole("button", { name: "다음" }));
    expect(await within(drill).findByRole("link", { name: "SO-DRILL-2" })).toBeInTheDocument();
    expect(stub.calls.map((c) => c.url)).toContain("/api/v1/order-board/items?stage=SO_CONFIRMED&page=2");
  });

  it("빈 열은 '해당 건 없음' — 보드 조회 오류는 서버 문구로", async () => {
    stubGateFetch(TRADER, [
      ["/v1/order-board", "GET", () => jsonResponse(board({ SO_ON_HOLD: { items: [], total: 0 } }))],
      ...baseHandlers(),
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    await screen.findByRole("link", { name: "IN-21" });
    expect(within(columnOf("수주 보류")).getByText("해당 건 없음")).toBeInTheDocument();
  });

  it("보드 조회 실패는 서버 한국어 문구 그대로", async () => {
    stubGateFetch(TRADER, [
      ["/v1/order-board", "GET", () => jsonResponse({ error: { code: "COMMON.INTERNAL.UNEXPECTED", message: "일시적인 오류입니다.", detail: {}, request_id: "req-1" } }, 500)],
      ...baseHandlers(),
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    expect(await screen.findByText("일시적인 오류입니다.")).toBeInTheDocument();
    expect(screen.getByText("오류 번호: req-1")).toBeInTheDocument();
  });

  it("셸 내비게이션에 '오더 보드'가 있다", async () => {
    open();
    await screen.findByRole("link", { name: "IN-21" });
    expect(screen.getByRole("link", { name: "오더 보드" })).toHaveAttribute("href", "/orders/board");
  });
});

describe("오더 보드 — 역할별 숨김", () => {
  it("조회 역할(VIEWER): 담당자 필터·벌크 영역·카드 선택 상자를 숨기고 users/lookup을 부르지 않는다", async () => {
    const stub = open(VIEWER);
    await screen.findByRole("link", { name: "IN-21" });
    expect(screen.getByRole("combobox", { name: "바이어" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "담당자" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "벌크 처리" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /보이는 카드 모두 선택/ })).not.toBeInTheDocument();
    expect(stub.calls.some((c) => c.url.includes("/users/lookup"))).toBe(false);
  });

  it.each([["LOGISTICS"], ["CERT"]])("%s도 조회 역할처럼 벌크·담당자 필터가 없다", async (role) => {
    open({ ...TRADER, roles: [role] });
    await screen.findByRole("link", { name: "IN-21" });
    expect(screen.queryByRole("region", { name: "벌크 처리" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "담당자" })).not.toBeInTheDocument();
  });

  it.each([["TRADE"], ["ADMIN"]])("%s: 담당자 필터·벌크 영역·카드 선택 상자가 보인다", async (role) => {
    open({ ...TRADER, roles: [role] });
    await screen.findByRole("link", { name: "IN-21" });
    expect(screen.getByRole("combobox", { name: "담당자" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "벌크 처리" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "IN-21 선택" })).toBeInTheDocument();
  });
});

describe("오더 보드 — 필터", () => {
  it("검색어·통화·목적 시장·접수일 범위가 고정 순서 쿼리로 보드에 간다", async () => {
    const expected = "/v1/order-board?q=ABC+Co&currency=USD&dest_market_code=US&created_from=2026-09-01&created_to=2026-09-30";
    const stub = open(TRADER, [[expected, "GET", () => jsonResponse(board({ SO_RECEIVED: { items: [boardCard({ ref_label: "SO-FILTERED" })] } }))]]);
    await screen.findByRole("link", { name: "IN-21" });
    await screen.findByRole("option", { name: "US 미국" });
    await screen.findByRole("option", { name: "USD" });
    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "  ABC Co " } });
    fireEvent.change(screen.getByLabelText("통화"), { target: { value: "USD" } });
    fireEvent.change(screen.getByLabelText("목적 시장"), { target: { value: "US" } });
    fireEvent.change(screen.getByLabelText("접수일 시작"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("접수일 끝"), { target: { value: "2026-09-30" } });
    // 입력만으로는 조회하지 않는다(키 입력마다 요청 금지) — '조회'를 눌러야 간다.
    expect(stub.calls.some((c) => c.url === `/api${expected}`)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    expect(await screen.findByRole("link", { name: "SO-FILTERED" })).toBeInTheDocument();
  });

  it("바이어 검색형 선택(type=BUYER) → buyer_partner_id 필터", async () => {
    open(TRADER, [
      ["/v1/partners?type=BUYER&size=20", "GET", () => jsonResponse({ items: [{ id: 3, name_ko: "에이비씨", partner_code: "B-003" }], total: 1, page: 1, size: 20 })],
      ["/v1/order-board?buyer_partner_id=3", "GET", () => jsonResponse(board({ SO_RECEIVED: { items: [boardCard({ ref_label: "SO-BUYER" })] } }))],
    ]);
    await screen.findByRole("link", { name: "IN-21" });
    fireEvent.focus(screen.getByRole("combobox", { name: "바이어" }));
    fireEvent.click(await screen.findByRole("option", { name: "에이비씨 (B-003)" }));
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    expect(await screen.findByRole("link", { name: "SO-BUYER" })).toBeInTheDocument();
  });

  it("담당자 검색형 선택은 assignee_target=true로 찾고 assignee_id 필터가 된다", async () => {
    open(TRADER, [
      ["/v1/users/lookup?assignee_target=true&size=20", "GET", () => jsonResponse({ items: [{ id: 2, display_name: "김무역" }], total: 1, page: 1, size: 20 })],
      ["/v1/order-board?assignee_id=2", "GET", () => jsonResponse(board({ SO_RECEIVED: { items: [boardCard({ ref_label: "SO-MINE" })] } }))],
    ]);
    await screen.findByRole("link", { name: "IN-21" });
    fireEvent.focus(screen.getByRole("combobox", { name: "담당자" }));
    fireEvent.click(await screen.findByRole("option", { name: "김무역" }));
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    expect(await screen.findByRole("link", { name: "SO-MINE" })).toBeInTheDocument();
  });

  it("접수일 시작>끝·보이지 않는 글자는 보내지 않고 필드 옆에 표시", async () => {
    const stub = open();
    await screen.findByRole("link", { name: "IN-21" });
    const before = stub.calls.length;
    fireEvent.change(screen.getByLabelText("접수일 시작"), { target: { value: "2026-10-05" } });
    fireEvent.change(screen.getByLabelText("접수일 끝"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "AB​C" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    expect(await screen.findByText("접수일 시작은 끝보다 늦을 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByText(/검색어에는 줄바꿈·탭·보이지 않는 글자/)).toBeInTheDocument();
    expect(screen.getByLabelText("검색어")).toHaveAttribute("aria-invalid", "true");
    expect(stub.calls.slice(before).some((c) => c.url.startsWith("/api/v1/order-board?"))).toBe(false);
  });

  it("서버 422(접수일 범위·q 금지 문자)는 해당 필드 옆에 서버 사유로", async () => {
    open(TRADER, [
      [
        "/v1/order-board?q=REJECT-ME&created_from=2026-09-01",
        "GET",
        () =>
          jsonResponse(
            {
              error: {
                code: "COMMON.VALIDATION.INVALID_FIELD",
                message: "입력값이 올바르지 않습니다.",
                detail: {
                  항목: [
                    { 위치: "q", 사유: "Value error, 검색어에는 서버가 거절한 글자가 있습니다." },
                    { 위치: "(본문)", 사유: "Value error, 접수일 시작은 끝보다 늦을 수 없습니다." },
                  ],
                },
                request_id: null,
              },
            },
            422,
          ),
      ],
    ]);
    await screen.findByRole("link", { name: "IN-21" });
    // 화면 사전 검사를 통과하는 값을 서버가 거절한 경우(규칙 차이·버전 차이)를 흉내 낸다 — 표시는 서버 사유 그대로.
    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "REJECT-ME" } });
    fireEvent.change(screen.getByLabelText("접수일 시작"), { target: { value: "2026-09-01" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    expect(await screen.findByText("검색어에는 서버가 거절한 글자가 있습니다.")).toBeInTheDocument();
    const toField = (screen.getByLabelText("접수일 끝").closest("label") as HTMLElement).parentElement as HTMLElement;
    expect(within(toField).getByText("접수일 시작은 끝보다 늦을 수 없습니다.")).toBeInTheDocument();
  });
});

describe("오더 보드 — CSV", () => {
  function stubDownload() {
    const names: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    return names;
  }

  const csv = (name: string) =>
    ({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(new Blob(["﻿구분"])),
      headers: new Headers({ "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(name)}` }),
    }) as unknown as Response;

  it("지금 적용된 필터 그대로 서버 CSV를 받고 파일명은 서버 헤더를 따른다", async () => {
    const names = stubDownload();
    const stub = open(TRADER, [
      ["/v1/order-board?q=ABC", "GET", () => jsonResponse(board())],
      ["/v1/order-board/export.csv?q=ABC", "GET", () => csv("오더보드.csv")],
    ]);
    await screen.findByRole("link", { name: "IN-21" });
    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "ABC" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    await waitFor(() => expect(stub.calls.map((c) => c.url)).toContain("/api/v1/order-board?q=ABC"));
    fireEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
    await waitFor(() => expect(names).toEqual(["오더보드.csv"]));
    expect(stub.calls.map((c) => c.url)).toContain("/api/v1/order-board/export.csv?q=ABC");
  });

  it("드릴다운의 '이 열 CSV'는 stage를 붙인다·50,000행 초과 422는 서버 문구로", async () => {
    stubDownload();
    stubGateFetch(TRADER, [
      ["/v1/order-board", "GET", () => jsonResponse(board({ SO_CONFIRMED: { items: [SO_CONFIRMED_CARD], total: 60, has_more: true } }))],
      ["/v1/order-board/items?stage=SO_CONFIRMED", "GET", () => jsonResponse({ items: [SO_CONFIRMED_CARD], total: 60, page: 1, size: 50 })],
      [
        "/v1/order-board/export.csv?stage=SO_CONFIRMED",
        "GET",
        () =>
          jsonResponse(
            { error: { code: "COMMON.VALIDATION.INVALID_FIELD", message: "내보낼 행이 너무 많습니다. 조건을 좁혀 주세요.", detail: {}, request_id: null } },
            422,
          ),
      ],
      ...baseHandlers(),
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    fireEvent.click(await screen.findByRole("button", { name: "수주 확정 더 보기" }));
    const drill = await screen.findByRole("region", { name: "수주 확정 전체 보기" });
    fireEvent.click(within(drill).getByRole("button", { name: "이 열 CSV" }));
    expect(await within(drill).findByText("내보낼 행이 너무 많습니다. 조건을 좁혀 주세요.")).toBeInTheDocument();
  });
});
