import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { SkuListPage } from "./skus";

const PRODUCT = {
  id: 7,
  product_code: "PRD-001",
  name_ko: "수분 세럼 처방",
  name_en: null,
  description: null,
  status: "ACTIVE",
  brand_id: 3,
  brand_code: "BRD-001",
  brand_name_ko: "테스트 브랜드",
};

const SKU = {
  id: 1,
  sku_code: "SER-001",
  name_ko: "수분 세럼 30ml",
  name_en: "Hydra Serum",
  status: "ACTIVE",
  kind: "SINGLE",
  product_id: 7,
  product_code: "PRD-001",
  product_name_ko: "수분 세럼 처방",
  brand_name_ko: "테스트 브랜드",
  barcode: null,
  unit_weight_g: null,
  box_qty: null,
  shelf_life_months: null,
  manufacturer_partner_id: null,
  manufacturer_name: null,
  dg_flag: false,
  un_number: null,
  dg_class: null,
  packing_group: null,
  flash_point_c: null,
  alcohol_content_pct: null,
  is_aerosol: false,
  is_limited_quantity: false,
  msds_url: null,
};

/** /auth/me·/products·/skus를 한 번에 흉내 낸다. */
function stubApi(options: { me?: unknown; skus?: unknown[]; onPost?: () => Response } = {}) {
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(options.me ?? TRADER));
    if (init?.method === "POST") {
      return Promise.resolve(options.onPost ? options.onPost() : jsonResponse(SKU));
    }
    if (input.includes("/products")) return Promise.resolve(jsonResponse(page([PRODUCT])));
    return Promise.resolve(jsonResponse(page(options.skus ?? [])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
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
    stubApi();
    renderWithProviders(<SkuListPage />);

    expect(await screen.findByText(/등록된 SKU가 없습니다/)).toBeInTheDocument();
  });

  it("목록을 한국어 라벨로 보여주고 처방·브랜드를 함께 보여준다", async () => {
    stubApi({ skus: [SKU] });
    renderWithProviders(<SkuListPage />);

    await screen.findByText("SER-001");
    // ★ 표 안으로 범위를 좁힌다. "단품"은 등록 폼의 선택지에도 있어서, 화면
    //   전체에서 찾으면 무엇을 검사하는지가 흐려진다.
    const table = within(screen.getByRole("table"));

    expect(table.getByText("수분 세럼 30ml")).toBeInTheDocument();
    expect(table.getByText("수분 세럼 처방")).toBeInTheDocument();
    expect(table.getByText("테스트 브랜드")).toBeInTheDocument();
    // 코드값을 그대로 노출하지 않는다(§14 한국어 UI).
    expect(table.getByText("판매중")).toBeInTheDocument();
    expect(table.getByText("단품")).toBeInTheDocument();
    expect(screen.queryByText("ACTIVE")).not.toBeInTheDocument();
    expect(screen.queryByText("SINGLE")).not.toBeInTheDocument();
  });

  it("단품 등록에는 처방 선택 칸이 있다 (§4.1)", async () => {
    stubApi();
    renderWithProviders(<SkuListPage />);

    const select = await screen.findByLabelText("제품(처방)");
    expect(select).toBeRequired();
    expect(await screen.findByRole("option", { name: /수분 세럼 처방/ })).toBeInTheDocument();
  });

  it("★ 세트를 고르면 처방 칸이 사라진다 (§4.2 — 세트는 처방을 갖지 않는다)", async () => {
    stubApi();
    renderWithProviders(<SkuListPage />);

    await screen.findByLabelText("제품(처방)");
    fireEvent.change(screen.getByLabelText("종류"), { target: { value: "SET" } });

    expect(screen.queryByLabelText("제품(처방)")).not.toBeInTheDocument();
    expect(screen.getByText(/세트에는 위험물 정보를 입력하지 않습니다/)).toBeInTheDocument();
  });

  it("단품 등록이 처방 id와 멱등 키를 함께 보낸다", async () => {
    const fetchMock = stubApi();
    renderWithProviders(<SkuListPage />);

    // ★ 선택지가 도착하기 전에 값을 넣으면 select에 반영되지 않고, 그러면
    //   required 검증에 걸려 제출 자체가 일어나지 않는다(원인이 "POST 없음"으로
    //   나타나 엉뚱한 곳을 보게 된다).
    await screen.findByRole("option", { name: /수분 세럼 처방/ });
    fireEvent.change(screen.getByLabelText("제품(처방)"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("품번"), { target: { value: "SER-001" } });
    fireEvent.change(screen.getByLabelText("품명(국문)"), { target: { value: "수분 세럼" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeDefined();
      const headers = (post?.[1]?.headers ?? {}) as Record<string, string>;
      expect(headers["Idempotency-Key"]).toBe("test-idempotency-key");
      const body = JSON.parse(String(post?.[1]?.body)) as Record<string, unknown>;
      expect(body.product_id).toBe(7);
      expect(body.kind).toBe("SINGLE");
    });
  });

  it("★ 세트 등록은 처방을 보내지 않는다", async () => {
    const fetchMock = stubApi({ onPost: () => jsonResponse({ ...SKU, kind: "SET" }) });
    renderWithProviders(<SkuListPage />);

    await screen.findByLabelText("종류");
    fireEvent.change(screen.getByLabelText("종류"), { target: { value: "SET" } });
    fireEvent.change(screen.getByLabelText("품번"), { target: { value: "SET-001" } });
    fireEvent.change(screen.getByLabelText("품명(국문)"), { target: { value: "기획 세트" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      const body = JSON.parse(String(post?.[1]?.body)) as Record<string, unknown>;
      expect(body.kind).toBe("SET");
      expect(body).not.toHaveProperty("product_id");
      expect(body.dg_flag).toBe(false);
    });
  });

  it("위험물이 꺼져 있으면 분류 칸을 쓸 수 없다 (ADR-0016 정정)", async () => {
    stubApi();
    renderWithProviders(<SkuListPage />);

    const unNumber = await screen.findByLabelText("UN번호");
    expect(unNumber).toBeDisabled();
    // 인화점·알코올함량은 "왜 위험물이 아닌지"의 근거라 항상 쓸 수 있다.
    expect(screen.getByLabelText("인화점(℃)")).toBeEnabled();
    expect(screen.getByLabelText("알코올함량(%)")).toBeEnabled();

    fireEvent.click(screen.getByLabelText("위험물"));
    expect(screen.getByLabelText("UN번호")).toBeEnabled();
  });

  it("서버가 준 한국어 오류 문구를 그대로 보여준다", async () => {
    stubApi({
      onPost: () =>
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
    });
    renderWithProviders(<SkuListPage />);

    await screen.findByRole("option", { name: /수분 세럼 처방/ });
    fireEvent.change(screen.getByLabelText("제품(처방)"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("품번"), { target: { value: "SER-001" } });
    fireEvent.change(screen.getByLabelText("품명(국문)"), { target: { value: "중복" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("이미 등록된 품번입니다");
  });

  it("조회 역할에게는 등록 폼을 보여주지 않는다", async () => {
    stubApi({ me: VIEWER });
    renderWithProviders(<SkuListPage />);

    await screen.findByText(/등록된 SKU가 없습니다/);
    expect(screen.queryByRole("button", { name: "등록" })).not.toBeInTheDocument();
    // 다만 이건 표시일 뿐이고 실제 차단은 서버가 한다(§18.1).
    expect(screen.getByText("CSV 내보내기")).toBeInTheDocument();
  });

  it("목록을 못 불러오면 빈 목록이 아니라 오류로 보여준다 (§18.4)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        input.includes("/auth/me")
          ? Promise.resolve(jsonResponse(TRADER))
          : Promise.resolve(
              jsonResponse(
                {
                  error: {
                    code: "COMMON.INTERNAL.UNEXPECTED",
                    message: "요청을 처리하지 못했습니다.",
                    detail: {},
                    request_id: "req-1",
                  },
                },
                500,
              ),
            ),
      ),
    );

    renderWithProviders(<SkuListPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("요청을 처리하지 못했습니다");
    // 신고할 때 로그를 집어낼 수 있도록 오류 번호를 함께 보여 준다.
    expect(alert).toHaveTextContent("req-1");
    expect(screen.queryByText(/등록된 SKU가 없습니다/)).not.toBeInTheDocument();
  });
});
