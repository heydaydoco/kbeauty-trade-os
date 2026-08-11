// 요건 템플릿 편집기 — 함정 ⑦ 회귀·권한별 UI·확정 요청 본문 (S2-1 판정 ⑤·조건 11).
//
// 실제 차단은 서버가 한다(§18.1) — 여기서 고정하는 것 셋:
// ① 통화 API가 죽어도(500) 화면이 백지가 되지 않고 비용 셀은 "—"다(함정 ⑦ —
//    부채 #16 동형, sku-detail-prices 선례 양식).
// ② 편집·확정 UI는 인증+관리자에게만 보인다(판정 ⑤ — 무역은 열람만).
// ③ 확정 클릭은 version(낙관 잠금 §17.2)을 그대로 되돌려 보낸다.

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const CERT = { id: 2, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

const TEMPLATE = {
  id: 1,
  market_id: 1,
  market_code: "US",
  market_name: "미국",
  name: "MoCRA 시설등록",
  applies_to: "FACILITY",
  requirement_type: "REGISTRATION",
  validity_months: 24,
  renewal_cycle_months: null,
  renewal_lead_days: null,
  estimated_cost_amount: 123456,
  estimated_cost_currency: "USD",
  source_url: "https://example.test/mocra",
  last_verified_on: "2026-08-01",
  status: "DRAFT",
  note: null,
  version: 1,
};

function stubApi(
  me: unknown,
  { currenciesFail = false, onConfirm }: { currenciesFail?: boolean; onConfirm?: (body: unknown) => void } = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/system/currencies")) {
        return Promise.resolve(
          currenciesFail
            ? jsonResponse(
                { error: { code: "COMMON.INTERNAL.UNEXPECTED", message: "서버 오류", detail: {}, request_id: "r" } },
                500,
              )
            : jsonResponse(page([{ code: "USD", minor_units: 2 }])),
        );
      }
      if (input.includes("/confirm") && init?.method === "POST") {
        onConfirm?.(JSON.parse(String(init.body)));
        return Promise.resolve(jsonResponse({ ...TEMPLATE, status: "CONFIRMED", version: 2 }));
      }
      if (input.includes("/v1/requirement-templates")) {
        return Promise.resolve(jsonResponse(page([TEMPLATE])));
      }
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("요건 템플릿 편집기", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("통화표가 도착하면 예상 비용이 사람 표기로 보인다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/requirement-templates" });

    expect(await screen.findByRole("heading", { name: "요건 템플릿" })).toBeInTheDocument();
    expect(await screen.findByText("1,234.56 USD")).toBeInTheDocument();
  });

  it("통화 API가 500이어도 화면이 살아 있고 비용 셀은 —다 (함정 ⑦ 회귀)", async () => {
    stubApi(TRADER, { currenciesFail: true });
    const { container } = renderWithProviders(<AppRoutes />, { route: "/requirement-templates" });

    expect(await screen.findByRole("heading", { name: "요건 템플릿" })).toBeInTheDocument();
    // 목록 자체는 살아 있다 — 백지(트리 언마운트)가 아니다.
    expect(await screen.findByText("MoCRA 시설등록")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect((container.textContent ?? "").length).toBeGreaterThan(100);
  });

  it("무역에게는 등록 폼·확정 버튼이 없다 (판정 ⑤ — 열람만)", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/requirement-templates" });

    expect(await screen.findByText("MoCRA 시설등록")).toBeInTheDocument();
    expect(screen.queryByText("새 템플릿 등록")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "확정" })).not.toBeInTheDocument();
  });

  it("인증의 확정 클릭은 version을 그대로 되돌려 보낸다 (§17.2)", async () => {
    const bodies: unknown[] = [];
    stubApi(CERT, { onConfirm: (body) => bodies.push(body) });
    renderWithProviders(<AppRoutes />, { route: "/requirement-templates" });

    fireEvent.click(await screen.findByRole("button", { name: "확정" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ version: 1 });
  });
});
