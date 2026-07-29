import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "./App";
import { TRADER, jsonResponse, page, renderWithProviders } from "./test/render";

/** 라우팅 도입(부채 #13) — 주소가 화면을 정한다. */
describe("라우팅", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function stubLoggedIn() {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        Promise.resolve(
          input.includes("/auth/me") ? jsonResponse(TRADER) : jsonResponse(page([])),
        ),
      ),
    );
  }

  function stubLoggedOut() {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "COMMON.AUTH.UNAUTHENTICATED",
                message: "로그인이 필요합니다.",
                detail: {},
                request_id: null,
              },
            },
            401,
          ),
        ),
      ),
    );
  }

  it("/brands는 브랜드 화면을 연다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/brands" });

    expect(await screen.findByRole("heading", { name: "브랜드" })).toBeInTheDocument();
  });

  it("/products는 제품 화면을 연다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/products" });

    expect(await screen.findByRole("heading", { name: "제품(처방)" })).toBeInTheDocument();
  });

  it("/item-profiles는 품목군 화면을 연다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/item-profiles" });

    expect(await screen.findByRole("heading", { name: "품목군" })).toBeInTheDocument();
  });

  it("/ 는 SKU 목록으로 보낸다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/" });

    expect(await screen.findByRole("heading", { name: "SKU" })).toBeInTheDocument();
  });

  it("★ 로그인하지 않으면 어느 주소로 와도 로그인 화면이다 (표시상의 게이트)", async () => {
    stubLoggedOut();
    renderWithProviders(<AppRoutes />, { route: "/products" });

    expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "제품(처방)" })).not.toBeInTheDocument();
  });

  it("없는 주소는 안내와 돌아갈 길을 준다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/없는주소" });

    expect(await screen.findByText("주소를 찾을 수 없습니다")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /SKU 목록으로 가기/ })).toBeInTheDocument();
  });

  it("로그인한 사람이 /login으로 오면 목록으로 돌려보낸다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/login" });

    expect(await screen.findByRole("heading", { name: "SKU" })).toBeInTheDocument();
  });

  it("상단 네비에 세 화면이 모두 있다", async () => {
    stubLoggedIn();
    renderWithProviders(<AppRoutes />, { route: "/skus" });

    expect(await screen.findByRole("link", { name: "SKU" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "제품(처방)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "브랜드" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "품목군" })).toBeInTheDocument();
  });
});
