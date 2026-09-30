// 거래처 화면 — 최소 렌더 + 여신한도 셀의 통화표 가드 회귀 (웹 세션 지적 이행).
//
// PR-1의 vitest 증가폭 +2는 sku-detail-prices.test.tsx였고 거래처 화면 vitest는
// 없었다 — PR-2 첫 프런트 커밋에서 보강한다. 가드 자체는 PR-1부터 있었다
// (partners.tsx 여신 셀 — 주의 인계 ⑦, product-detail-bom.test.tsx 선례).

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

const PARTNER = {
  id: 1,
  partner_code: "PTN-001",
  name_ko: "한국콜마",
  type_codes: ["OEM", "SUPPLIER"],
  credit_limit_amount: 1234,
  credit_limit_currency: "USD",
  dg_capable: null,
  strengths: null,
  weaknesses: null,
  note: null,
};

const CURRENCIES = { items: [{ code: "USD", minor_units: 2 }], total: 1, page: 1, size: 200 };

function stubApi(currenciesResponse: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
      if (input.includes("/v1/partners")) return Promise.resolve(jsonResponse(page([PARTNER])));
      if (input.includes("/v1/system/currencies")) return Promise.resolve(currenciesResponse());
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("거래처 목록", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록·유형·여신한도가 표시된다 (최소 렌더)", async () => {
    stubApi(() => jsonResponse(CURRENCIES));
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    expect(await screen.findByRole("heading", { name: "거래처" })).toBeInTheDocument();
    expect(await screen.findByText("한국콜마")).toBeInTheDocument();
    expect(screen.getByText("OEM · 공급사")).toBeInTheDocument();
    // 정수 최소단위 1234 → 통화표(minor_units 2)로 12.34 USD.
    await waitFor(() => {
      expect(screen.getByText("12.34 USD")).toBeInTheDocument();
    });
  });

  it("★ 통화표가 실패해도 화면이 살아 있다 — 여신 셀 가드 회귀 (주의 인계 ⑦)", async () => {
    stubApi(() =>
      jsonResponse(
        {
          error: {
            code: "COMMON.INTERNAL.UNEXPECTED",
            message: "일시적인 오류입니다.",
            detail: {},
            request_id: null,
          },
        },
        500,
      ),
    );
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    // 목록·거래처명이 계속 보인다(백지가 아니다). 가드가 없으면 여신 셀의
    // formatMoney가 빈 통화표에 예외를 던져 트리가 통째로 사라진다.
    expect(await screen.findByRole("heading", { name: "거래처" })).toBeInTheDocument();
    expect(await screen.findByText("한국콜마")).toBeInTheDocument();
    // 여신한도 값은 만들지 않는다 — 빈 표시(—)로 남는다.
    expect(screen.queryByText("12.34 USD")).not.toBeInTheDocument();
    expect(document.body.textContent?.length ?? 0).toBeGreaterThan(100);
  });
});

// ── S3-1 PR-3: 영문명·여신 비활성·품번 삭제 ─────────────────────────────

const ADMIN = { ...TRADER, roles: ["ADMIN"] };
const CODE = {
  id: 9,
  partner_id: 1,
  sku_id: 4,
  sku_code: "SER-001",
  buyer_item_code: "BUY-777",
  note: null,
};

function stubFull(me: unknown) {
  let codes = [CODE];
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
    if (init?.method === "DELETE") {
      codes = [];
      return Promise.resolve({ ok: true, status: 204, json: () => Promise.resolve(null) } as Response);
    }
    if (init?.method === "POST") return Promise.resolve(jsonResponse(PARTNER, 201));
    if (input.includes("/item-codes")) return Promise.resolve(jsonResponse(page(codes)));
    if (input.includes("/v1/partners"))
      return Promise.resolve(jsonResponse(page([{ ...PARTNER, name_en: "Kolmar Korea" }])));
    if (input.includes("/v1/system/currencies")) return Promise.resolve(jsonResponse(CURRENCIES));
    return Promise.resolve(jsonResponse(page([])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("거래처 — 영문명·여신·품번 삭제", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("비관리자(TRADE)는 여신 입력이 비활성이고 안내가 보이며, 영문명·주소가 전송되고 여신은 안 나간다", async () => {
    const fetchMock = stubFull(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    const limit = await screen.findByLabelText("여신한도 (선택)");
    expect(limit).toBeDisabled();
    expect(screen.getByLabelText("여신통화")).toBeDisabled();
    expect(screen.getByText("여신 한도는 관리자만 설정할 수 있습니다")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("거래처코드"), { target: { value: "PTN-002" } });
    fireEvent.change(screen.getByLabelText("거래처명"), { target: { value: "신규" } });
    fireEvent.change(screen.getByLabelText("거래처명(영문, 선택)"), { target: { value: "New Co" } });
    fireEvent.change(screen.getByLabelText("영문주소 (선택)"), { target: { value: "1 Main St" } });
    fireEvent.click(screen.getByLabelText("OEM"));
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeDefined();
      const body = JSON.parse(String(post?.[1]?.body)) as Record<string, unknown>;
      expect(body.name_en).toBe("New Co");
      expect(body.address_en).toBe("1 Main St");
      expect(body).not.toHaveProperty("credit_limit");
      expect(body).not.toHaveProperty("credit_limit_currency");
    });
  });

  it("관리자는 여신 입력이 활성이고 안내가 없다", async () => {
    stubFull(ADMIN);
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    expect(await screen.findByLabelText("여신한도 (선택)")).toBeEnabled();
    expect(screen.getByLabelText("여신통화")).toBeEnabled();
    expect(screen.queryByText("여신 한도는 관리자만 설정할 수 있습니다")).not.toBeInTheDocument();
  });

  it("목록에 영문명이 보인다", async () => {
    stubFull(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    expect(await screen.findByText("Kolmar Korea")).toBeInTheDocument();
  });

  it("TRADE는 품번 매핑 삭제 버튼이 보이고, 확인 후 DELETE·목록 갱신", async () => {
    const fetchMock = stubFull(TRADER);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    fireEvent.click(await screen.findByRole("button", { name: "상세·품번" }));
    expect(await screen.findByText("BUY-777")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "품번 BUY-777 삭제" }));

    await waitFor(() => {
      const del = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
      expect(String(del?.[0])).toBe("/api/v1/partners/1/item-codes/9");
    });
    expect(await screen.findByText("등록된 품번 매핑이 없습니다.")).toBeInTheDocument();
  });

  it("확인 대화상자를 취소하면 DELETE하지 않는다", async () => {
    const fetchMock = stubFull(TRADER);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    fireEvent.click(await screen.findByRole("button", { name: "상세·품번" }));
    fireEvent.click(await screen.findByRole("button", { name: "품번 BUY-777 삭제" }));

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });

  it("VIEWER에게는 삭제 버튼이 없다", async () => {
    stubFull(VIEWER);
    renderWithProviders(<AppRoutes />, { route: "/partners" });

    fireEvent.click(await screen.findByRole("button", { name: "상세·품번" }));
    expect(await screen.findByText("BUY-777")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /삭제/ })).not.toBeInTheDocument();
  });
});
