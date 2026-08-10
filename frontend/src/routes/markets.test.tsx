// 시장 화면 — 최소 렌더 + 권한별 편집 UI + 정식화 PATCH 본문 (S2-1 판정 ⑤).
//
// 실제 차단은 서버가 한다(§18.1) — 여기서 고정하는 것은 화면 게이트의 표시
// 규칙(인증+관리자만 등록·정식화 UI)과 정식화 요청이 version(낙관 잠금
// §17.2)을 그대로 되돌려 보낸다는 계약이다.

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const CERT = { id: 2, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

const MIG_MARKET = {
  id: 1,
  code: "US",
  name_ko: "US",
  name_en: null,
  note: "MIG: country_code FK 승격 백필(S2-1) — 이름 정식화·검수 전",
  version: 1,
};

function stubApi(me: unknown, onPatch?: (body: unknown) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/markets") && init?.method === "PATCH") {
        onPatch?.(JSON.parse(String(init.body)));
        return Promise.resolve(
          jsonResponse({ ...MIG_MARKET, name_ko: "미국", version: 2 }),
        );
      }
      if (input.includes("/v1/markets")) return Promise.resolve(jsonResponse(page([MIG_MARKET])));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("시장 목록", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록·MIG 표식 메모가 표시된다 (최소 렌더)", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/markets" });

    expect(await screen.findByRole("heading", { name: "시장" })).toBeInTheDocument();
    // MIG 백필 행은 코드=이름이라 "US"가 코드·이름 두 셀에 나온다.
    expect(await screen.findAllByText("US")).toHaveLength(2);
    expect(
      screen.getByText("MIG: country_code FK 승격 백필(S2-1) — 이름 정식화·검수 전"),
    ).toBeInTheDocument();
  });

  it("무역 역할에는 등록 폼·정식화 버튼이 없다 (판정 ⑤ — 열람만)", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/markets" });

    expect(await screen.findAllByText("US")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "등록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
  });

  it("인증 역할의 정식화가 version을 그대로 되돌려 보낸다 (§17.2)", async () => {
    const bodies: unknown[] = [];
    stubApi(CERT, (body) => bodies.push(body));
    renderWithProviders(<AppRoutes />, { route: "/markets" });

    fireEvent.click(await screen.findByRole("button", { name: "수정" }));
    const nameInput = await screen.findByLabelText("시장명 수정");
    fireEvent.change(nameInput, { target: { value: "미국" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => {
      expect(bodies).toHaveLength(1);
    });
    expect(bodies[0]).toMatchObject({ version: 1, name_ko: "미국" });
  });
});
