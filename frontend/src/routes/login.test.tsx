import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./login";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

function submitLogin(email = "junebee@example.com", password = "비밀번호") {
  fireEvent.change(screen.getByLabelText("이메일"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
}

describe("로그인 화면", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("잠금 안내를 미리 보여준다 (5회 실패 규칙을 나중에 알게 하지 않는다)", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithClient(<LoginPage />);
    expect(screen.getByText(/5회 연속 틀리면/)).toBeInTheDocument();
  });

  it("실패하면 서버가 준 한국어 문구를 그대로 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "COMMON.AUTH.INVALID_CREDENTIALS",
                message: "이메일 또는 비밀번호가 올바르지 않습니다. 다시 확인해 주세요.",
                detail: {},
                request_id: "abc",
              },
            },
            401,
          ),
        ),
      ),
    );

    renderWithClient(<LoginPage />);
    submitLogin();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "이메일 또는 비밀번호가 올바르지 않습니다",
    );
  });

  it("계정 잠금(423)도 서버 문구로 안내한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "COMMON.AUTH.ACCOUNT_LOCKED",
                message:
                  "비밀번호를 5회 연속 틀려 계정이 잠겼습니다. 잠시 후 다시 시도하시거나 관리자에게 잠금 해제를 요청해 주세요.",
                detail: {},
                request_id: "abc",
              },
            },
            423,
          ),
        ),
      ),
    );

    renderWithClient(<LoginPage />);
    submitLogin();

    expect(await screen.findByRole("alert")).toHaveTextContent("계정이 잠겼습니다");
  });

  it("서버에 닿지 못하면 영문 오류가 아니라 한국어 안내가 나온다", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));

    renderWithClient(<LoginPage />);
    submitLogin();

    expect(await screen.findByRole("alert")).toHaveTextContent("서버에 연결할 수 없습니다");
  });
});
