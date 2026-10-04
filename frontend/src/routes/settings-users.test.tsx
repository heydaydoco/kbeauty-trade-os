// 사용자·역할 화면 (S3-2 PR-7 · D14) — 화면 계약 고정. 실제 차단·마지막 관리자 보호는 서버가 한다(§18.1).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { roleLabel, ROLE_CODES } from "../lib/users";
import { ACCOUNT_CREATION_NOTE } from "./settings-users";

const ADMIN_ME = { id: 9, email: "admin@example.com", display_name: "관리자", roles: ["ADMIN"] };
const ADMIN_ROW = { id: 9, email: "admin@example.com", display_name: "관리자", is_active: true, roles: ["ADMIN"] };
const LOGI_ROW = { id: 12, email: "logi@example.com", display_name: "물류 담당", is_active: true, roles: ["ADMIN", "LOGISTICS"] };

const LAST_ADMIN_MESSAGE =
  "마지막 남은 관리자입니다. 이 계정의 관리자 권한을 회수하거나 비활성화하면 아무도 시스템을 관리할 수 없게 됩니다. 다른 사용자에게 관리자 권한을 먼저 부여해 주세요.";

interface Call {
  url: string;
  init: RequestInit;
}

function stubApi(
  me: unknown,
  rows: unknown[],
  writes: Call[] = [],
  writeResponse: () => Response = () => jsonResponse(LOGI_ROW),
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/alerts/unread-count")) return Promise.resolve(jsonResponse({ count: 0 }));
      if (input.includes("/v1/approvals/inbox-count")) return Promise.resolve(jsonResponse({ count: 0 }));
      const method = init?.method ?? "GET";
      if (input.startsWith("/api/v1/users") && method !== "GET") {
        writes.push({ url: input, init: init! });
        return Promise.resolve(writeResponse());
      }
      if (input.startsWith("/api/v1/users")) return Promise.resolve(jsonResponse(page(rows)));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
  return writes;
}

function usersListCalls(): number {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
    (c: unknown[]) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/v1/users") && ((c[1] as RequestInit | undefined)?.method ?? "GET") === "GET",
  ).length;
}

const header = (call: Call, name: string) => (call.init.headers as Record<string, string>)[name];

describe("사용자·역할 화면", () => {
  let uuid = 0;
  beforeEach(() => {
    uuid = 0;
    vi.stubGlobal("crypto", { randomUUID: () => `key-${++uuid}` });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("역할 라벨은 5종 한글, 모르는 코드는 '기타'", () => {
    expect(ROLE_CODES).toEqual(["ADMIN", "TRADE", "LOGISTICS", "CERT", "VIEWER"]);
    expect(ROLE_CODES.map(roleLabel)).toEqual(["관리자", "무역", "물류", "인증", "조회"]);
    expect(roleLabel("SUPERUSER")).toBe("기타");
  });

  it.each([
    ["무역", TRADER],
    ["조회", VIEWER],
  ])("ADMIN 외(%s)에는 메뉴가 없고 직접 진입하면 권한 안내, 목록 API를 부르지 않는다", async (_label, me) => {
    stubApi(me, [ADMIN_ROW]);
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });

    expect(await screen.findByRole("alert")).toHaveTextContent("사용자·역할은 관리자만 볼 수 있습니다");
    expect(screen.queryByRole("link", { name: "사용자·역할" })).not.toBeInTheDocument();
    expect(usersListCalls()).toBe(0);
  });

  it("ADMIN에게는 메뉴가 보이고 목록·역할 칩·상태·CLI 안내 1줄이 나온다", async () => {
    stubApi(ADMIN_ME, [ADMIN_ROW, { ...LOGI_ROW, is_active: false, roles: ["LOGISTICS", "MYSTERY"] }]);
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });

    expect(await screen.findByText("logi@example.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "사용자·역할" })).toHaveAttribute("href", "/settings/users");
    expect(screen.getByText(ACCOUNT_CREATION_NOTE)).toBeInTheDocument();
    expect(ACCOUNT_CREATION_NOTE).toContain("관리자 CLI");
    expect(screen.getByText("비활성")).toBeInTheDocument();
    expect(screen.getByText("전체 2건")).toBeInTheDocument();
    expect(usersListCalls()).toBeGreaterThan(0); // 미호출 단언(비ADMIN)이 공회전이 아님을 같은 계수기로 확인
    const logiRow = screen.getByText("logi@example.com").closest("tr")!;
    expect(within(logiRow).getByText("물류")).toBeInTheDocument();
    expect(within(logiRow).getByText("기타")).toHaveAttribute("title", "MYSTERY");
    // 계정 생성 화면·버튼은 없다(R-22 — CLI 유지).
    expect(screen.queryByRole("button", { name: /계정 (생성|추가|만들기)/ })).not.toBeInTheDocument();
  });

  it("역할 부여는 확인 대화상자를 거쳐 POST /users/{id}/roles {role}, 키 부착·목록 재조회", async () => {
    const writes = stubApi(ADMIN_ME, [ADMIN_ROW, LOGI_ROW]);
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });

    const select = await screen.findByLabelText("logi@example.com 부여할 역할");
    // 이미 가진 역할은 선택지에 없다.
    expect(within(select).queryByRole("option", { name: "물류" })).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: "TRADE" } });
    const before = usersListCalls();
    fireEvent.click(within(select.closest("td")!).getByRole("button", { name: "부여" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("logi@example.com에게 '무역' 역할을 부여합니다.");
    expect(writes).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "부여" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]!.url).toBe("/api/v1/users/12/roles");
    expect(writes[0]!.init.method).toBe("POST");
    expect(JSON.parse(writes[0]!.init.body as string)).toEqual({ role: "TRADE" });
    expect(header(writes[0]!, "Idempotency-Key")).toBe("key-1");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("status")).toHaveTextContent("logi@example.com: 역할 부여 완료");
    await waitFor(() => expect(usersListCalls()).toBeGreaterThan(before));
  });

  it("역할 회수는 DELETE /users/{id}/roles/{role}, 닫기는 호출 0", async () => {
    const writes = stubApi(ADMIN_ME, [ADMIN_ROW, LOGI_ROW]);
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });

    fireEvent.click(await screen.findByRole("button", { name: "logi@example.com 관리자 역할 회수" }));
    let dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(writes).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "logi@example.com 관리자 역할 회수" }));
    dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("logi@example.com의 '관리자' 역할을 회수합니다.");
    // 남의 관리자 회수에는 본인 경고가 없다.
    expect(dialog).not.toHaveTextContent("다시 들어올 수 없습니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "회수" }));

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]!.url).toBe("/api/v1/users/12/roles/ADMIN");
    expect(writes[0]!.init.method).toBe("DELETE");
    expect(writes[0]!.init.body).toBeUndefined();
    // 닫았다 다시 연 대화상자는 새 키다.
    expect(header(writes[0]!, "Idempotency-Key")).toBe("key-2");
  });

  it("마지막 관리자 회수: 본인 경고 → 서버 409 문구 그대로 대화상자에 표시, 재시도는 같은 키", async () => {
    const writes = stubApi(ADMIN_ME, [ADMIN_ROW], [], () =>
      jsonResponse(
        { error: { code: "IDENTITY.ADMIN.LAST_ONE", message: LAST_ADMIN_MESSAGE, detail: {}, request_id: "req-1" } },
        409,
      ),
    );
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });

    fireEvent.click(await screen.findByRole("button", { name: "admin@example.com 관리자 역할 회수" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("본인의 관리자 역할입니다. 회수하면 이 화면에 다시 들어올 수 없습니다.");
    fireEvent.click(within(dialog).getByRole("button", { name: "회수" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(LAST_ADMIN_MESSAGE);
    // 대화상자는 열린 채 남는다(실패를 성공처럼 닫지 않는다).
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "회수" }));
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(header(writes[0]!, "Idempotency-Key")).toBe("key-1");
    expect(header(writes[1]!, "Idempotency-Key")).toBe("key-1");
  });

  it("마지막 관리자 비활성화도 같은 서버 문구, 활성 토글·잠금 해제 경로·본문", async () => {
    let status = 409;
    const writes = stubApi(ADMIN_ME, [ADMIN_ROW, { ...LOGI_ROW, is_active: false }], [], () =>
      status === 409
        ? jsonResponse({ error: { code: "IDENTITY.ADMIN.LAST_ONE", message: LAST_ADMIN_MESSAGE, detail: {}, request_id: null } }, 409)
        : jsonResponse(LOGI_ROW),
    );
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });

    fireEvent.click(await screen.findByRole("button", { name: "admin@example.com 비활성화" }));
    let dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("본인 계정입니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "비활성화" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(LAST_ADMIN_MESSAGE);
    expect(writes[0]!.url).toBe("/api/v1/users/9/active");
    expect(writes[0]!.init.method).toBe("PATCH");
    expect(JSON.parse(writes[0]!.init.body as string)).toEqual({ is_active: false });
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));

    status = 200;
    fireEvent.click(screen.getByRole("button", { name: "logi@example.com 활성화" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "활성화" }));
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(JSON.parse(writes[1]!.init.body as string)).toEqual({ is_active: true });

    fireEvent.click(await screen.findByRole("button", { name: "logi@example.com 잠금 해제" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "잠금 해제" }));
    await waitFor(() => expect(writes).toHaveLength(3));
    expect(writes[2]!.url).toBe("/api/v1/users/12/unlock");
    expect(writes[2]!.init.method).toBe("POST");
    // 대화상자마다 다른 키.
    expect(new Set(writes.map((w) => header(w, "Idempotency-Key"))).size).toBe(3);
  });

  it("목록 조회 실패(403 등)는 서버 문구로 보이고 '사용자 없음'으로 위장하지 않는다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(ADMIN_ME));
        if (input.startsWith("/api/v1/users"))
          return Promise.resolve(
            jsonResponse({ error: { code: "COMMON.AUTH.FORBIDDEN", message: "권한이 없습니다.", detail: {}, request_id: "r" } }, 403),
          );
        return Promise.resolve(jsonResponse({ count: 0 }));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/settings/users" });
    expect(await screen.findByText("권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/사용자가 없습니다/)).not.toBeInTheDocument();
  });
});

describe("사용자·역할 화면 소스 계약", () => {
  const sources = import.meta.glob(["./settings-users.tsx", "../lib/users.ts"], {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  it("스캔 대상 2파일이 살아 있다", () => {
    expect(Object.keys(sources).sort()).toEqual(["../lib/users.ts", "./settings-users.tsx"]);
  });

  it("날짜 파싱(new Date)·직접 fetch·역할 라벨 중복 표가 없다", () => {
    for (const [path, src] of Object.entries(sources)) {
      expect(src, path).not.toMatch(/new Date\(/);
      expect(src, path).not.toMatch(/\bfetch\(/);
    }
    // 역할 라벨 표는 lib/users.ts 한 곳뿐.
    expect(sources["./settings-users.tsx"]).not.toMatch(/"관리자"|"물류"/);
  });

  it("390px: 표는 overflow-x-auto 안, 헤더 nowrap, 문구 break-keep", () => {
    const src = sources["./settings-users.tsx"]!;
    expect(src).toContain('className="mt-2 overflow-x-auto"');
    const headers = src.match(/<th className="[^"]*"/g) ?? [];
    expect(headers.length).toBe(6);
    for (const th of headers) expect(th).toContain("cell-nowrap");
    expect(src).toContain("break-keep");
  });
});
