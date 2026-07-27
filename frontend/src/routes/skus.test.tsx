import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SkuPage } from "./skus";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const ME = {
  id: 1,
  email: "trade@example.com",
  display_name: "무역 담당",
  roles: ["TRADE"],
};

const VIEWER = { ...ME, roles: ["VIEWER"] };

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

describe("SKU 화면", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-idempotency-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록이 비어 있으면 등록 안내를 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        Promise.resolve(
          input.includes("/auth/me")
            ? jsonResponse(ME)
            : jsonResponse({ items: [], total: 0, page: 1, size: 50 }),
        ),
      ),
    );

    renderWithClient(<SkuPage />);

    expect(await screen.findByText(/등록된 SKU가 없습니다/)).toBeInTheDocument();
  });

  it("목록을 한국어 상태 라벨로 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        Promise.resolve(
          input.includes("/auth/me")
            ? jsonResponse(ME)
            : jsonResponse({
                items: [
                  {
                    id: 1,
                    sku_code: "SER-001",
                    name_ko: "수분 세럼 30ml",
                    name_en: "Hydra Serum",
                    status: "ACTIVE",
                  },
                ],
                total: 1,
                page: 1,
                size: 50,
              }),
        ),
      ),
    );

    renderWithClient(<SkuPage />);

    expect(await screen.findByText("SER-001")).toBeInTheDocument();
    expect(screen.getByText("수분 세럼 30ml")).toBeInTheDocument();
    // 상태 코드를 그대로 노출하지 않는다(§18.4 한국어 UI).
    expect(screen.getByText("판매중")).toBeInTheDocument();
    expect(screen.queryByText("ACTIVE")).not.toBeInTheDocument();
  });

  it("등록 요청에 멱등 키 헤더가 붙는다", async () => {
    const fetchMock = vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(ME));
      if (init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            id: 1,
            sku_code: "SER-001",
            name_ko: "수분 세럼",
            name_en: null,
            status: "ACTIVE",
          }),
        );
      }
      return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, size: 50 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<SkuPage />);

    fireEvent.change(await screen.findByLabelText("품번"), { target: { value: "SER-001" } });
    fireEvent.change(screen.getByLabelText("품명(국문)"), { target: { value: "수분 세럼" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeDefined();
      const headers = (post?.[1]?.headers ?? {}) as Record<string, string>;
      expect(headers["Idempotency-Key"]).toBe("test-idempotency-key");
    });
  });

  it("서버가 준 한국어 오류 문구를 그대로 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(ME));
        if (init?.method === "POST") {
          return Promise.resolve(
            jsonResponse(
              {
                error: {
                  code: "COMMON.VALIDATION.INVALID_FIELD",
                  message: "입력값이 올바르지 않습니다.",
                  detail: { sku_code: "이미 등록된 품번입니다. 다른 품번을 입력해 주세요." },
                  request_id: "abc",
                },
              },
              422,
            ),
          );
        }
        return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, size: 50 }));
      }),
    );

    renderWithClient(<SkuPage />);

    fireEvent.change(await screen.findByLabelText("품번"), { target: { value: "SER-001" } });
    fireEvent.change(screen.getByLabelText("품명(국문)"), { target: { value: "중복" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("이미 등록된 품번입니다");
  });

  it("조회 역할에게는 등록 폼을 보여주지 않는다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        Promise.resolve(
          input.includes("/auth/me")
            ? jsonResponse(VIEWER)
            : jsonResponse({ items: [], total: 0, page: 1, size: 50 }),
        ),
      ),
    );

    renderWithClient(<SkuPage />);

    await screen.findByText(/등록된 SKU가 없습니다/);
    expect(screen.queryByRole("button", { name: "등록" })).not.toBeInTheDocument();
    // 다만 이건 표시일 뿐이고 실제 차단은 서버가 한다(§18.1).
    expect(screen.getByText("CSV 내보내기")).toBeInTheDocument();
  });
});
