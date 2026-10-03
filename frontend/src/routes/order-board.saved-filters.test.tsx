// 오더 보드 저장 필터 — 목록·적용·저장·이름 변경·삭제·낡은 필터·20개 상한·이름 422·중복 409·localStorage 차단 (S3-1 PR-15b, 테스트 그룹 A).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { LAST_FILTER_STORAGE_KEY } from "../components/board-saved-filters";
import { EMPTY_FILTER, type SavedFilter } from "../lib/order-board";
import { baseHandlers, board, boardCard, savedFilter } from "../test/board-fixtures";
import { stubGateFetch, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  try {
    window.localStorage.clear();
  } catch {
    // 일부 테스트가 저장소를 막는다.
  }
});

const errorEnvelope = (status: number, code: string, message: string, detail: Record<string, unknown> = {}) => () =>
  jsonResponse({ error: { code, message, detail, request_id: null } }, status);

function open(filters: SavedFilter[], extra: GateHandler[] = [], me: unknown = TRADER) {
  const stub = stubGateFetch(me, [...extra, ["/v1/order-board", "GET", () => jsonResponse(board())], ...baseHandlers(filters)]);
  renderWithProviders(<AppRoutes />, { route: "/orders/board" });
  return stub;
}

const panel = () => screen.getByRole("region", { name: "저장 필터" });
const choose = async (name: string) => {
  const select = within(panel()).getByRole("combobox");
  await within(select).findByRole("option", { name });
  const option = within(select).getByRole("option", { name }) as HTMLOptionElement;
  fireEvent.change(select, { target: { value: option.value } });
};

describe("저장 필터 — 목록·적용", () => {
  it("개수/상한을 보이고, 적용하면 그 조건으로 보드를 다시 받고 입력칸에도 채운다·마지막 선택 id는 localStorage", async () => {
    const stub = open(
      [savedFilter()],
      [["/v1/order-board?q=ABC&dest_market_code=US", "GET", () => jsonResponse(board({ SO_RECEIVED: { items: [boardCard({ ref_label: "SO-SAVED" })] } }))]],
    );
    await screen.findByRole("link", { name: "IN-21" });
    expect(await within(panel()).findByRole("combobox", { name: "저장 필터 1/20개" })).toBeInTheDocument();
    await choose("미국 대기 건");
    fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
    expect(await screen.findByRole("link", { name: "SO-SAVED" })).toBeInTheDocument();
    expect(screen.getByLabelText("검색어")).toHaveValue("ABC");
    expect(screen.getByLabelText("목적 시장")).toHaveValue("US");
    expect(window.localStorage.getItem(LAST_FILTER_STORAGE_KEY)).toBe("5");
    expect(stub.calls.some((c) => c.url === "/api/v1/order-board?q=ABC&dest_market_code=US")).toBe(true);
  });

  it("바이어 id만 있는 저장 필터를 적용하면 카드의 바이어명으로 선택값을 보인다", async () => {
    open(
      [savedFilter({ filter_config: { ...EMPTY_FILTER, buyer_partner_id: 3 } })],
      [["/v1/order-board?buyer_partner_id=3", "GET", () => jsonResponse(board())]],
    );
    await screen.findByRole("link", { name: "IN-21" });
    await choose("미국 대기 건");
    fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
    expect(await screen.findByRole("button", { name: "바이어 선택 해제" })).toBeInTheDocument();
    expect(screen.getByText("ABC Trading", { selector: "span.break-keep" })).toBeInTheDocument();
  });

  it("needs_resave=true는 '다시 저장 필요' 표시·적용 불가", async () => {
    open([savedFilter({ name: "옛 필터", filter_config: null, needs_resave: true })]);
    await screen.findByRole("link", { name: "IN-21" });
    await choose("옛 필터 (다시 저장 필요)");
    expect(within(panel()).getByRole("button", { name: "적용" })).toBeDisabled();
    expect(within(panel()).getByText(/조건 형식이 바뀌어 그대로 적용할 수 없습니다/)).toBeInTheDocument();
  });

  it("조회 역할도 저장 필터를 쓴다(본인 것 — 서버가 소유권을 가른다)", async () => {
    open([savedFilter()], [], VIEWER);
    await screen.findByRole("link", { name: "IN-21" });
    expect(await within(panel()).findByRole("option", { name: "미국 대기 건" })).toBeInTheDocument();
  });

  it("localStorage 접근이 막혀도(throw) 화면은 정상", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    open([savedFilter()], [["/v1/order-board?q=ABC&dest_market_code=US", "GET", () => jsonResponse(board())]]);
    await screen.findByRole("link", { name: "IN-21" });
    await choose("미국 대기 건");
    fireEvent.click(within(panel()).getByRole("button", { name: "적용" }));
    await waitFor(() => expect(screen.getByLabelText("검색어")).toHaveValue("ABC"));
  });

  it("마지막으로 고른 필터를 다시 들어왔을 때 미리 골라 둔다(적용은 사람이)", async () => {
    window.localStorage.setItem(LAST_FILTER_STORAGE_KEY, "5");
    open([savedFilter()]);
    await screen.findByRole("link", { name: "IN-21" });
    await within(panel()).findByRole("option", { name: "미국 대기 건" });
    expect(within(panel()).getByRole("combobox")).toHaveValue("5");
  });
});

describe("저장 필터 — 저장·이름 변경·삭제", () => {
  it("현재 적용 조건을 이름과 함께 POST(필터 키 7개 그대로)·Idempotency-Key 부착", async () => {
    const stub = open(
      [],
      [
        ["/v1/order-board?q=XYZ", "GET", () => jsonResponse(board())],
        ["/v1/order-board/saved-filters", "POST", () => jsonResponse(savedFilter({ id: 9, name: "XYZ 건" }), 201)],
      ],
    );
    await screen.findByRole("link", { name: "IN-21" });
    fireEvent.change(screen.getByLabelText("검색어"), { target: { value: "XYZ" } });
    fireEvent.click(screen.getByRole("button", { name: "조회" }));
    await waitFor(() => expect(stub.calls.map((c) => c.url)).toContain("/api/v1/order-board?q=XYZ"));
    fireEvent.change(within(panel()).getByLabelText("새 필터 이름"), { target: { value: "XYZ 건" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건 저장" }));
    expect(await within(panel()).findByText("'XYZ 건' 필터를 저장했습니다.")).toBeInTheDocument();
    const post = stub.calls.find((c) => c.method === "POST" && c.url === "/api/v1/order-board/saved-filters");
    expect(post?.body).toEqual({ name: "XYZ 건", filter_config: { ...EMPTY_FILTER, q: "XYZ" } });
    expect(post?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("20개 상한(422 LIMIT_REACHED)은 서버 문구로", async () => {
    open(
      Array.from({ length: 20 }, (_, i) => savedFilter({ id: i + 1, name: `필터 ${i + 1}` })),
      [["/v1/order-board/saved-filters", "POST", errorEnvelope(422, "ORDER_BOARD.FILTER.LIMIT_REACHED", "저장 필터는 한 사람당 20개까지 만들 수 있습니다. 쓰지 않는 필터를 지운 뒤 다시 저장해 주세요.")]],
    );
    await screen.findByRole("link", { name: "IN-21" });
    expect(await within(panel()).findByText(/저장 필터가 20개입니다/)).toBeInTheDocument();
    fireEvent.change(within(panel()).getByLabelText("새 필터 이름"), { target: { value: "21번째" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건 저장" }));
    expect(await within(panel()).findByText(/한 사람당 20개까지/, { selector: "p[role=alert]" })).toBeInTheDocument();
  });

  it("이름 422(서버 — 서식 문자 등)는 이름 칸 옆에, 보이지 않는 글자는 보내기 전에 막는다", async () => {
    const stub = open([], [["/v1/order-board/saved-filters", "POST", errorEnvelope(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값이 올바르지 않습니다.", { name: "필터 이름에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다." })]]);
    await screen.findByRole("link", { name: "IN-21" });
    const input = within(panel()).getByLabelText("새 필터 이름");
    fireEvent.change(input, { target: { value: "탭\t이름" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건 저장" }));
    expect(await within(panel()).findByText(/필터 이름에는 줄바꿈·탭·보이지 않는 글자/)).toBeInTheDocument();
    expect(stub.calls.some((c) => c.method === "POST")).toBe(false);
    expect(input).toHaveAttribute("aria-invalid", "true");

    // 화면 검사를 지나는 이름을 서버가 거절 — 서버 문구가 이름 칸 옆에.
    fireEvent.change(input, { target: { value: "정상처럼 보이는 이름" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건 저장" }));
    await waitFor(() => expect(stub.calls.some((c) => c.method === "POST")).toBe(true));
    expect(await within(panel()).findByText(/필터 이름에는 줄바꿈·탭·보이지 않는 글자/)).toBeInTheDocument();
  });

  it("중복 이름 409는 이름 칸 옆에 서버 문구", async () => {
    open([savedFilter()], [["/v1/order-board/saved-filters", "POST", errorEnvelope(409, "ORDER_BOARD.FILTER.DUPLICATE_NAME", "같은 이름의 저장 필터가 이미 있습니다. 다른 이름으로 저장하거나 기존 필터를 수정해 주세요.")]]);
    await screen.findByRole("link", { name: "IN-21" });
    fireEvent.change(within(panel()).getByLabelText("새 필터 이름"), { target: { value: "미국 대기 건" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건 저장" }));
    const message = await within(panel()).findByText(/같은 이름의 저장 필터가 이미 있습니다/);
    expect(message.previousElementSibling).toBe(within(panel()).getByLabelText("새 필터 이름"));
  });

  it("이름 변경은 version과 새 이름만 PATCH, 중복이면 새 이름 칸 옆에", async () => {
    let patchCount = 0;
    const stub = open(
      [savedFilter()],
      [
        [
          "/v1/order-board/saved-filters/5",
          "PATCH",
          () => {
            patchCount += 1;
            return patchCount === 1
              ? errorEnvelope(409, "ORDER_BOARD.FILTER.DUPLICATE_NAME", "같은 이름의 저장 필터가 이미 있습니다.")()
              : jsonResponse(savedFilter({ name: "새 이름", version: 3 }));
          },
        ],
      ],
    );
    await screen.findByRole("link", { name: "IN-21" });
    await choose("미국 대기 건");
    fireEvent.click(within(panel()).getByRole("button", { name: "이름 변경" }));
    const rename = within(panel()).getByLabelText("새 이름");
    fireEvent.change(rename, { target: { value: "겹치는 이름" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "이름 저장" }));
    expect(await within(panel()).findByText("같은 이름의 저장 필터가 이미 있습니다.")).toBeInTheDocument();
    expect(rename).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(rename, { target: { value: "새 이름" } });
    fireEvent.click(within(panel()).getByRole("button", { name: "이름 저장" }));
    expect(await within(panel()).findByText("'새 이름' 필터를 고쳤습니다.")).toBeInTheDocument();
    const patches = stub.calls.filter((c) => c.method === "PATCH");
    expect(patches.map((c) => c.body)).toEqual([
      { version: 2, name: "겹치는 이름" },
      { version: 2, name: "새 이름" },
    ]);
  });

  it("'현재 조건으로 다시 저장'은 filter_config만 PATCH — version 충돌 409는 다시 불러오기 안내", async () => {
    const stub = open(
      [savedFilter({ name: "옛 필터", filter_config: null, needs_resave: true })],
      [["/v1/order-board/saved-filters/5", "PATCH", errorEnvelope(409, "COMMON.CONCURRENCY.VERSION_CONFLICT", "다른 곳에서 먼저 수정되었습니다.")]],
    );
    await screen.findByRole("link", { name: "IN-21" });
    await choose("옛 필터 (다시 저장 필요)");
    fireEvent.click(within(panel()).getByRole("button", { name: "현재 조건으로 다시 저장" }));
    expect(await within(panel()).findByText(/먼저 바뀌었습니다. 목록을 다시 불러온 뒤/)).toBeInTheDocument();
    expect(stub.calls.find((c) => c.method === "PATCH")?.body).toEqual({ version: 2, filter_config: EMPTY_FILTER });
  });

  it("삭제는 확인 후 DELETE ?version= — 목록을 다시 받는다", async () => {
    let listed: SavedFilter[] = [savedFilter()];
    const stub = stubGateFetch(TRADER, [
      ["/v1/order-board/saved-filters/5?version=2", "DELETE", () => ({ ok: true, status: 204, json: () => Promise.resolve(null) }) as unknown as Response],
      ["/v1/order-board", "GET", () => jsonResponse(board())],
      ["/v1/order-board/saved-filters", "GET", () => jsonResponse(page(listed))],
      ...baseHandlers(),
    ]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    await screen.findByRole("link", { name: "IN-21" });
    await choose("미국 대기 건");
    fireEvent.click(within(panel()).getByRole("button", { name: "삭제" }));
    const dialog = await screen.findByRole("dialog", { name: /저장 필터 삭제/ });
    listed = [];
    fireEvent.click(within(dialog).getByRole("button", { name: "삭제" }));
    expect(await within(panel()).findByText("저장 필터를 삭제했습니다.")).toBeInTheDocument();
    expect(stub.calls.some((c) => c.method === "DELETE" && c.url === "/api/v1/order-board/saved-filters/5?version=2")).toBe(true);
    await waitFor(() => expect(within(panel()).queryByRole("option", { name: "미국 대기 건" })).not.toBeInTheDocument());
  });
});
