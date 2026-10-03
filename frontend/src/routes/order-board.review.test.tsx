// 오더 보드 적대 검토 반영 (S3-1 PR-15b) — 조건 변경 시 선택 비움·확인 창 대상 목록·무효화 집합·포커스·aria 연결·조회 역할의 저장 필터 담당자 조건.

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { APPROVALS_QUERY_KEY } from "../lib/approval";
import { EMPTY_FILTER } from "../lib/order-board";
import { ORDER_INTAKES_QUERY_KEY } from "../lib/order-intake";
import { PROFORMAS_QUERY_KEY } from "../lib/proforma";
import { QUOTATIONS_QUERY_KEY } from "../lib/quotation";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY } from "../lib/sales-order";
import { SO_RECEIVED_CARD, baseHandlers, board, bulkReport, bulkResult, savedFilter } from "../test/board-fixtures";
import { stubGateFetch, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, renderWithProviders } from "../test/render";
import { BOARD_DATA_KEY } from "./order-board";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function open(extra: GateHandler[] = [], options: { me?: unknown; client?: QueryClient; filters?: ReturnType<typeof savedFilter>[] } = {}) {
  const stub = stubGateFetch(options.me ?? TRADER, [
    ...extra,
    ["/v1/order-board", "GET", () => jsonResponse(board())],
    ...baseHandlers(options.filters ?? []),
  ]);
  renderWithProviders(<AppRoutes />, { route: "/orders/board", client: options.client });
  return stub;
}

const panel = () => screen.getByRole("region", { name: "저장 필터" });

async function selectTwoSo() {
  fireEvent.click(await screen.findByRole("checkbox", { name: "SO-2026-0011 선택" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "SO-2026-0012 선택" }));
  expect(screen.getByRole("button", { name: "수주 확정 (2건)" })).toBeEnabled();
}

describe("조건이 바뀌면 선택을 비운다(낡은 선택이 벌크로 나가지 않게)", () => {
  it.each([
    [
      "조회",
      [["/v1/order-board?q=ABC", "GET", () => jsonResponse(board())]] as GateHandler[],
      async () => {
        fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "ABC" } });
        fireEvent.click(screen.getByRole("button", { name: "조회" }));
      },
    ],
    [
      "초기화",
      [] as GateHandler[],
      async () => {
        fireEvent.click(screen.getByRole("button", { name: "조건 초기화" }));
      },
    ],
    [
      "저장 필터 적용",
      [["/v1/order-board?q=ABC&dest_market_code=US", "GET", () => jsonResponse(board())]] as GateHandler[],
      async () => {
        const select = within(panel()).getByRole("combobox");
        const option = (await within(select).findByRole("option", { name: "미국 대기 건" })) as HTMLOptionElement;
        fireEvent.change(select, { target: { value: option.value } });
        fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
      },
    ],
  ])("%s → 선택 0건·확정 버튼 비활성", async (_name, extra, act) => {
    open(extra, { filters: [savedFilter()] });
    await selectTwoSo();
    await act();
    await waitFor(() => expect(screen.getByRole("button", { name: "수주 확정 (0건)" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "인테이크 확정 (0건)" })).toBeDisabled();
    expect(screen.getByText(/^선택/, { selector: "section[aria-label='벌크 처리'] > span" })).toHaveTextContent("선택 0건");
  });
});

describe("확인 창 대상 목록", () => {
  it("대상 ref_label을 나열한다", async () => {
    open();
    await selectTwoSo();
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (2건)" }));
    const list = within(await screen.findByRole("dialog", { name: /수주 확정 — 2건/ })).getByRole("list", { name: "대상 목록" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["SO-2026-0011", "SO-2026-0012"]);
  });

  it("10건을 넘으면 앞 10개 + '외 N건'", async () => {
    const many = Array.from({ length: 13 }, (_, i) => ({ ...SO_RECEIVED_CARD, id: 100 + i, ref_label: `SO-M-${String(i).padStart(2, "0")}` }));
    stubGateFetch(TRADER, [["/v1/order-board", "GET", () => jsonResponse(board({ SO_RECEIVED: { items: many, total: 13 } }))], ...baseHandlers()]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    fireEvent.click(within(await screen.findByRole("region", { name: "수주 접수 열" })).getByRole("button", { name: "보이는 카드 모두 선택" }));
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (13건)" }));
    const list = within(await screen.findByRole("dialog", { name: /수주 확정 — 13건/ })).getByRole("list", { name: "대상 목록" });
    const items = within(list).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(11);
    expect(items[0]).toBe("SO-M-00");
    expect(items[9]).toBe("SO-M-09");
    expect(items[10]).toBe("외 3건");
  });
});

describe("벌크 뒤 무효화 집합", () => {
  it("보드·수주·문서 흐름·견적·PI·결재·인테이크를 무효화한다(confirm-panel과 같은 집합 + 보드·인테이크)", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    open([["/v1/order-board/bulk", "POST", () => jsonResponse(bulkReport([bulkResult({ id: 11 }), bulkResult({ id: 12 })]))]], { client });
    await selectTwoSo();
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (2건)" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: /수주 확정 — 2건/ })).getByRole("button", { name: "수주 확정" }));
    await screen.findByRole("dialog", { name: "수주 확정 결과" });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const keys = spy.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
    for (const key of [BOARD_DATA_KEY, SALES_ORDERS_QUERY_KEY, DOCUMENT_FLOW_QUERY_KEY, QUOTATIONS_QUERY_KEY, PROFORMAS_QUERY_KEY, APPROVALS_QUERY_KEY, ORDER_INTAKES_QUERY_KEY]) {
      expect(keys).toContain(JSON.stringify(key));
    }
  });
});

describe("포커스(접근성)", () => {
  it("확정 직후 포커스는 결과 모달 안(제목) — 처리 중 Tab은 밖으로 새지 않고, 닫으면 벌크 영역으로 돌아온다", async () => {
    let release: (value: Response) => void = () => undefined;
    open([["/v1/order-board/bulk", "POST", () => new Promise<Response>((resolve) => (release = resolve))]]);
    await selectTwoSo();
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (2건)" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: /수주 확정 — 2건/ })).getByRole("button", { name: "수주 확정" }));
    const dialog = await screen.findByRole("dialog", { name: "수주 확정 결과" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    expect(document.activeElement).toBe(within(dialog).getByRole("heading", { name: "수주 확정 결과" }));
    // 처리 중 — 버튼이 모두 비활성(포커스 대상 0개)이어도 Tab 기본 동작을 막는다.
    expect(within(dialog).getByRole("button", { name: "닫기" })).toBeDisabled();
    expect(fireEvent.keyDown(document, { key: "Tab" })).toBe(false);

    release(jsonResponse(bulkReport([bulkResult({ id: 11 })])));
    fireEvent.click(await within(dialog).findByRole("button", { name: "닫기" }, {}));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 연 버튼(확인 창의 '수주 확정')은 사라졌다 — 벌크 영역으로 돌아온다.
    expect(document.activeElement).toBe(screen.getByRole("region", { name: "벌크 처리" }));
  });
});

describe("aria 연결", () => {
  it("통화·목적 시장 select와 접수일 시작 칸이 서버 422 문구를 가리킨다", async () => {
    open([
      [
        "/v1/order-board?created_from=2026-10-05&created_to=2026-10-09",
        "GET",
        () =>
          jsonResponse(
            {
              error: {
                code: "COMMON.VALIDATION.INVALID_FIELD",
                message: "입력값이 올바르지 않습니다.",
                detail: {
                  항목: [
                    { 위치: "currency", 사유: "String should match pattern" },
                    { 위치: "dest_market_code", 사유: "String should match pattern" },
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
    fireEvent.change(screen.getByLabelText("접수일 시작"), { target: { value: "2026-10-05" } });
    fireEvent.change(screen.getByLabelText("접수일 끝"), { target: { value: "2026-10-09" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    const currency = screen.getByLabelText("통화");
    await waitFor(() => expect(currency).toHaveAttribute("aria-invalid", "true"));
    expect(currency).toHaveAccessibleDescription("통화는 영문 대문자 3자입니다.");
    const market = screen.getByLabelText("목적 시장");
    expect(market).toHaveAttribute("aria-invalid", "true");
    expect(market).toHaveAccessibleDescription("목적 시장 코드는 영문 대문자 2자입니다.");
    // 범위 오류는 끝 칸 옆에 한 번만 보이고, 시작 칸도 그 문구를 가리킨다.
    expect(screen.getByLabelText("접수일 시작")).toHaveAccessibleDescription("접수일 시작은 끝보다 늦을 수 없습니다.");
    expect(screen.getByLabelText("접수일 끝")).toHaveAccessibleDescription("접수일 시작은 끝보다 늦을 수 없습니다.");
  });

  it("저장 필터 이름 오류가 입력칸 설명으로 연결된다(새 이름·이름 변경 둘 다)", async () => {
    open(
      [
        ["/v1/order-board/saved-filters", "POST", () => jsonResponse({ error: { code: "ORDER_BOARD.FILTER.DUPLICATE_NAME", message: "같은 이름의 저장 필터가 이미 있습니다.", detail: {}, request_id: null } }, 409)],
        ["/v1/order-board/saved-filters/5", "PATCH", () => jsonResponse({ error: { code: "ORDER_BOARD.FILTER.DUPLICATE_NAME", message: "이름이 겹칩니다.", detail: {}, request_id: null } }, 409)],
      ],
      { filters: [savedFilter()] },
    );
    await screen.findByRole("link", { name: "IN-21" });
    const name = within(panel()).getByLabelText("새 필터 이름");
    fireEvent.change(name, { target: { value: "미국 대기 건" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건 저장" }));
    await waitFor(() => expect(name).toHaveAccessibleDescription("같은 이름의 저장 필터가 이미 있습니다."));

    const select = within(panel()).getByRole("combobox");
    const option = (await within(select).findByRole("option", { name: "미국 대기 건" })) as HTMLOptionElement;
    fireEvent.change(select, { target: { value: option.value } });
    fireEvent.click(within(panel()).getByRole("button", { name: "이름 변경" }));
    const rename = within(panel()).getByLabelText("새 이름");
    fireEvent.change(rename, { target: { value: "겹침" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "이름 저장" }));
    await waitFor(() => expect(rename).toHaveAccessibleDescription("이름이 겹칩니다."));
  });
});

describe("조회 역할의 저장 필터 담당자 조건", () => {
  const withAssignee = savedFilter({ filter_config: { ...EMPTY_FILTER, q: "ABC", assignee_id: 7 } });

  it("담당자 조건은 조용히 빼지 않고 안내 — 보드는 담당자 없이 조회", async () => {
    const stub = open([["/v1/order-board?q=ABC", "GET", () => jsonResponse(board())]], { me: VIEWER, filters: [withAssignee] });
    await screen.findByRole("link", { name: "IN-21" });
    const select = within(panel()).getByRole("combobox");
    const option = (await within(select).findByRole("option", { name: "미국 대기 건" })) as HTMLOptionElement;
    fireEvent.change(select, { target: { value: option.value } });
    fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
    expect(await screen.findByText(/담당자 조건은 현재 역할에서 쓸 수 없어 제외했습니다/)).toBeInTheDocument();
    await waitFor(() => expect(stub.calls.map((c) => c.url)).toContain("/api/v1/order-board?q=ABC"));
    expect(stub.calls.some((c) => c.url.includes("assignee_id"))).toBe(false);
  });

  it("'현재 조건으로 다시 저장'은 원래 assignee_id를 보존한다", async () => {
    const stub = open(
      [
        ["/v1/order-board?q=ABC", "GET", () => jsonResponse(board())],
        ["/v1/order-board/saved-filters/5", "PATCH", () => jsonResponse(withAssignee)],
      ],
      { me: VIEWER, filters: [withAssignee] },
    );
    await screen.findByRole("link", { name: "IN-21" });
    const select = within(panel()).getByRole("combobox");
    const option = (await within(select).findByRole("option", { name: "미국 대기 건" })) as HTMLOptionElement;
    fireEvent.change(select, { target: { value: option.value } });
    fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
    await screen.findByText(/담당자 조건은 현재 역할에서 쓸 수 없어 제외했습니다/);
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건으로 다시 저장" }));
    await waitFor(() => expect(stub.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(stub.calls.find((c) => c.method === "PATCH")?.body).toEqual({ version: 2, filter_config: { ...EMPTY_FILTER, q: "ABC", assignee_id: 7 } });
  });

  it("무역 역할은 담당자 조건을 그대로 적용하고 안내가 없다", async () => {
    const stub = open([["/v1/order-board?q=ABC&assignee_id=7", "GET", () => jsonResponse(board())]], { filters: [withAssignee] });
    await screen.findByRole("link", { name: "IN-21" });
    const select = within(panel()).getByRole("combobox");
    const option = (await within(select).findByRole("option", { name: "미국 대기 건" })) as HTMLOptionElement;
    fireEvent.change(select, { target: { value: option.value } });
    fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
    await waitFor(() => expect(stub.calls.map((c) => c.url)).toContain("/api/v1/order-board?q=ABC&assignee_id=7"));
    expect(screen.queryByText(/담당자 조건은 현재 역할에서/)).not.toBeInTheDocument();
  });
});
