// 은행 계좌 화면 — 역할별 노출(ADMIN 쓰기 / TRADE 조회 / 그 외 차단)·등록·수정·비활성·검증·멱등 키·409 (S3-1 PR-6b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { bankAccount } from "../test/pi-fixtures";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

const ADMIN = { id: 9, email: "admin@example.com", display_name: "관리자", roles: ["ADMIN"] };
const LOGISTICS = { ...TRADER, roles: ["LOGISTICS"] };

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [bankAccount(), bankAccount({ id: 3, label: "국민 KRW", currency: "KRW", account_no: "999-000-1111", version: 1 })];
const CURRENCIES: [string, string, () => Response] = [
  "/v1/system/currencies",
  "GET",
  () => jsonResponse(page([{ code: "USD", minor_units: 2 }, { code: "KRW", minor_units: 0 }])),
];

function open(me: unknown, extra: Array<[string, string, () => Response]> = []) {
  const stub = stubFetch(me, [...extra, CURRENCIES, ["/v1/bank-accounts", "GET", () => jsonResponse(page(ROWS))]]);
  renderWithProviders(<AppRoutes />, { route: "/bank-accounts" });
  return stub;
}

const CONFLICT = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } }, 409);

const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;

describe("은행 계좌 — 역할별 노출", () => {
  it("ADMIN: 목록·계좌번호와 등록·수정·비활성 버튼이 보인다", async () => {
    open(ADMIN);
    expect(await screen.findByText("110-123-456789")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "계좌 등록" })).toBeInTheDocument();
    expect(within(rowOf("신한 USD")).getByRole("button", { name: "수정" })).toBeInTheDocument();
    expect(within(rowOf("신한 USD")).getByRole("button", { name: "비활성" })).toBeInTheDocument();
  });

  it("TRADE: 조회(계좌번호 포함)만 — 쓰기 버튼이 없고 관리자 안내가 뜬다", async () => {
    open(TRADER);
    expect(await screen.findByText("110-123-456789")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "계좌 등록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "비활성" })).not.toBeInTheDocument();
    expect(screen.getByText(/등록·수정·비활성은 관리자만 할 수 있습니다/)).toBeInTheDocument();
  });

  it.each([
    ["VIEWER", VIEWER],
    ["LOGISTICS", LOGISTICS],
  ])("%s: 계좌번호를 그리지도 요청하지도 않는다", async (_name, me) => {
    const { calls } = open(me);
    expect(await screen.findByRole("alert")).toHaveTextContent("은행 계좌는 관리자·무역 담당만 볼 수 있습니다.");
    expect(screen.queryByText("110-123-456789")).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/v1/bank-accounts"))).toBe(false);
  });

  it("내비: ADMIN·TRADE에게만 '은행 계좌'가 보이고 PI는 전 역할에 보인다", async () => {
    for (const [me, expected] of [
      [ADMIN, true],
      [TRADER, true],
      [VIEWER, false],
      [LOGISTICS, false],
    ] as const) {
      const { unmount } = (() => {
        stubFetch(me, []);
        return renderWithProviders(<AppRoutes />, { route: "/proforma-invoices" });
      })();
      const nav = await screen.findByRole("navigation");
      expect(within(nav).getByRole("link", { name: "PI" })).toBeInTheDocument();
      expect(within(nav).queryByRole("link", { name: "은행 계좌" }) !== null).toBe(expected);
      unmount();
      vi.unstubAllGlobals();
      keySeq = 0;
      vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
    }
  });

  it("빈 목록·오류를 구분하고 통화 필터는 서버 쿼리로 나간다", async () => {
    const { calls } = stubFetch(ADMIN, [CURRENCIES, ["/v1/bank-accounts", "GET", () => jsonResponse(page([]))]]);
    renderWithProviders(<AppRoutes />, { route: "/bank-accounts" });
    expect(await screen.findByText(/아직 등록된 계좌가 없습니다/)).toBeInTheDocument();
    await screen.findByRole("option", { name: "USD" });
    fireEvent.change(screen.getByLabelText("통화"), { target: { value: "USD" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/bank-accounts?currency=USD"));
  });

  it("조회 실패는 서버 문구를 오류로 보인다", async () => {
    stubFetch(ADMIN, [CURRENCIES, ["/v1/bank-accounts", "GET", () => jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500)]]);
    renderWithProviders(<AppRoutes />, { route: "/bank-accounts" });
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류입니다.");
  });
});

async function fillNewForm(dialog: HTMLElement) {
  await within(dialog).findByRole("option", { name: "USD" });
  fireEvent.change(within(dialog).getByLabelText("통화"), { target: { value: "USD" } });
  fireEvent.change(within(dialog).getByLabelText("표시 이름"), { target: { value: " 우리 USD " } });
  fireEvent.change(within(dialog).getByLabelText("수취인"), { target: { value: "K-Beauty" } });
  fireEvent.change(within(dialog).getByLabelText("수취인 주소"), { target: { value: "Seoul" } });
  fireEvent.change(within(dialog).getByLabelText("은행명"), { target: { value: "Woori" } });
  fireEvent.change(within(dialog).getByLabelText("은행 주소"), { target: { value: "Seoul HQ" } });
  fireEvent.change(within(dialog).getByLabelText("계좌번호"), { target: { value: "1002-111" } });
  fireEvent.change(within(dialog).getByLabelText(/SWIFT/), { target: { value: "hvbkkrse" } });
}

describe("은행 계좌 — 등록", () => {
  it("필수·SWIFT 형식을 화면에서 검증하고, 통과하면 멱등 키와 함께 POST한다(SWIFT는 대문자)", async () => {
    const created = bankAccount({ id: 4, label: "우리 USD" });
    const { calls } = open(ADMIN, [["/v1/bank-accounts", "POST", () => jsonResponse(created, 201)]]);
    fireEvent.click(await screen.findByRole("button", { name: "계좌 등록" }));
    const dialog = await screen.findByRole("dialog", { name: "은행 계좌 등록" });
    fireEvent.click(within(dialog).getByRole("button", { name: "등록" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("표시 이름을(를) 입력해 주세요.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await fillNewForm(dialog);
    fireEvent.change(within(dialog).getByLabelText(/SWIFT/), { target: { value: "ABC" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "등록" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("SWIFT 코드는 영문 대문자·숫자 8자리 또는 11자리입니다.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.change(within(dialog).getByLabelText(/SWIFT/), { target: { value: "hvbkkrse" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "등록" }));
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST");
      expect(post?.body).toEqual({
        label: "우리 USD",
        currency: "USD",
        beneficiary_name: "K-Beauty",
        beneficiary_address: "Seoul",
        bank_name: "Woori",
        bank_address: "Seoul HQ",
        account_no: "1002-111",
        swift_code: "HVBKKRSE",
      });
      expect(post?.headers["Idempotency-Key"]).toMatch(/^key-\d+$/);
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("등록 더블클릭은 1회만 전송한다", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(ADMIN);
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (i?.method === "POST") {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "계좌 등록" }));
    const dialog = await screen.findByRole("dialog", { name: "은행 계좌 등록" });
    await fillNewForm(dialog);
    const submit = within(dialog).getByRole("button", { name: "등록" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    release(jsonResponse(bankAccount({ id: 4 }), 201));
  });

  it("중복 계좌 등 서버 422는 서버 문구를 다이얼로그에 보인다", async () => {
    open(ADMIN, [
      [
        "/v1/bank-accounts",
        "POST",
        () => jsonResponse({ error: { code: "VALIDATION_INVALID_FIELD", message: "이미 등록된 계좌입니다.", detail: { account_no: "중복" } } }, 422),
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "계좌 등록" }));
    const dialog = await screen.findByRole("dialog", { name: "은행 계좌 등록" });
    await fillNewForm(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "등록" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("이미 등록된 계좌입니다. (중복)");
  });

  it("Esc로 닫히고 포커스가 '계좌 등록' 버튼으로 돌아온다", async () => {
    open(ADMIN);
    const opener = await screen.findByRole("button", { name: "계좌 등록" });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("dialog", { name: "은행 계좌 등록" });
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });
});

describe("은행 계좌 — 수정·비활성", () => {
  it("수정은 바뀐 필드만 열 때 본 version과 함께 PATCH하고 통화는 잠겨 있으며 보내지 않는다", async () => {
    const { calls } = open(ADMIN, [["/v1/bank-accounts/2", "PATCH", () => jsonResponse(bankAccount({ version: 4 }))]]);
    await screen.findByText("110-123-456789");
    fireEvent.click(within(rowOf("신한 USD")).getByRole("button", { name: "수정" }));
    const dialog = await screen.findByRole("dialog", { name: "은행 계좌 수정" });
    expect(within(dialog).getByLabelText("통화")).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("바뀐 내용이 없습니다.");
    fireEvent.change(within(dialog).getByLabelText("은행명"), { target: { value: "Shinhan Bank Seoul" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.url).toMatch(/\/v1\/bank-accounts\/2$/);
      expect(patch?.body).toEqual({ version: 3, bank_name: "Shinhan Bank Seoul" });
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("수정 409는 안내와 '최신 내용 불러오기'(닫고 목록 재조회)를 보인다", async () => {
    const { calls } = open(ADMIN, [["/v1/bank-accounts/2", "PATCH", CONFLICT]]);
    await screen.findByText("110-123-456789");
    fireEvent.click(within(rowOf("신한 USD")).getByRole("button", { name: "수정" }));
    const dialog = await screen.findByRole("dialog", { name: "은행 계좌 수정" });
    fireEvent.change(within(dialog).getByLabelText("표시 이름"), { target: { value: "신한 달러" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "저장" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("다른 곳에서 이 은행 계좌 정보가 먼저 수정되었습니다");
    const before = calls.filter((c) => c.method === "GET" && c.url.includes("/v1/bank-accounts")).length;
    fireEvent.click(within(alert).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.url.includes("/v1/bank-accounts")).length).toBeGreaterThan(before),
    );
  });

  it("비활성은 확인 다이얼로그를 거쳐 version 쿼리와 함께 DELETE한다", async () => {
    const { calls } = open(ADMIN, [["/v1/bank-accounts/2", "DELETE", () => jsonResponse(bankAccount())]]);
    await screen.findByText("110-123-456789");
    fireEvent.click(within(rowOf("신한 USD")).getByRole("button", { name: "비활성" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/이미\s*발행된 PI는 바뀌지 않습니다/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "비활성" }));
    await waitFor(() => {
      const del = calls.find((c) => c.method === "DELETE");
      expect(del?.url).toMatch(/\/v1\/bank-accounts\/2\?version=3$/);
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("비활성 409는 다이얼로그 안 '최신 내용 불러오기'로 닫고 재조회한다", async () => {
    open(ADMIN, [["/v1/bank-accounts/2", "DELETE", CONFLICT]]);
    await screen.findByText("110-123-456789");
    fireEvent.click(within(rowOf("신한 USD")).getByRole("button", { name: "비활성" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "비활성" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다른 곳에서 이 은행 계좌 정보가 먼저 수정되었습니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
