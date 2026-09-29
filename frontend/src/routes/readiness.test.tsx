// 시장 준비도 매트릭스 화면 — 색+글자 표기·범례(조건 A)·셀 상세·필터 요청 (S2-3 PR-3).
//
// 고정하는 것: ① 색만으로 구분하지 않는다(글자·건수 병기) ② 모집합 안내는 **서버가 준
// 문구 그대로**다 ③ 셀 클릭 상세가 요건별 상태(한국어)·구성품 출처·인증 상세 링크를 보인다
// ④ 열이 없으면 이유를 말한다 ⑤ 필터가 서버 질의로 나간다(화면이 색을 다시 계산하지 않는다).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { VIEWER, jsonResponse, renderWithProviders } from "../test/render";
import type { MatrixCell, MatrixPageData, MatrixRow } from "./readiness";

const SCOPE_NOTE =
  "집계 모집합: 품목군 요건 세트의 확정 요건 중 SKU·제품·자사(기업)·제조사(시설) 단위만 셉니다. 성분(INGREDIENT) 단위 요건은 집계에 포함되지 않으므로 '판매가능'이 성분 요건 충족까지 뜻하지는 않습니다.";

function cell(market: number, code: string, color: string, extra: Partial<MatrixCell> = {}): MatrixCell {
  return {
    market_id: market,
    market_code: code,
    color,
    required: 0,
    approved: 0,
    in_progress: 0,
    unmet: 0,
    items: [],
    ...extra,
  };
}

const ROWS: MatrixRow[] = [
  {
    sku_id: 1,
    sku_code: "SKU-GREEN",
    name_ko: "수분 세럼 30ml",
    kind: "SINGLE",
    status: "ACTIVE",
    item_profile_id: 1,
    components: [],
    cells: [
      cell(1, "US", "GREEN", {
        required: 2,
        approved: 2,
        items: [
          {
            template_id: 10,
            template_name: "MoCRA 제품 리스팅",
            axis: "SKU",
            target_type: "SKU",
            target_id: 1,
            status: "APPROVED",
            color: "GREEN",
            certification_id: 77,
            note: null,
            via_component_sku_id: null,
            via_component_sku_code: null,
          },
          {
            template_id: 11,
            template_name: "US Agent 지정",
            axis: "COMPANY",
            target_type: "COMPANY",
            target_id: null,
            status: "APPROVED",
            color: "GREEN",
            certification_id: 78,
            note: null,
            via_component_sku_id: null,
            via_component_sku_code: null,
          },
        ],
      }),
      cell(2, "CA", "GRAY"),
    ],
  },
  {
    sku_id: 2,
    sku_code: "SKU-RED",
    name_ko: "진정 크림",
    kind: "SINGLE",
    status: "ACTIVE",
    item_profile_id: 1,
    components: [],
    cells: [
      cell(1, "US", "YELLOW", { required: 2, approved: 1, in_progress: 1 }),
      cell(2, "CA", "RED", { required: 1, unmet: 1 }),
    ],
  },
  {
    sku_id: 3,
    sku_code: "SET-1",
    name_ko: "세럼+크림 세트",
    kind: "SET",
    status: "ACTIVE",
    item_profile_id: null,
    components: [
      { sku_id: 1, sku_code: "SKU-GREEN" },
      { sku_id: 2, sku_code: "SKU-RED" },
    ],
    cells: [
      cell(1, "US", "RED", {
        required: 2,
        approved: 1,
        unmet: 1,
        items: [
          {
            template_id: 10,
            template_name: "MoCRA 제품 리스팅",
            axis: "SKU",
            target_type: "SKU",
            target_id: 2,
            status: null,
            color: "RED",
            certification_id: null,
            note: "활성 인증 인스턴스가 없습니다(미등록·반려·중단).",
            via_component_sku_id: 2,
            via_component_sku_code: "SKU-RED",
          },
        ],
      }),
      cell(2, "CA", "GRAY"),
    ],
  },
];

function matrix(rows: MatrixRow[] = ROWS, markets = true): MatrixPageData {
  return {
    items: rows,
    total: rows.length,
    page: 1,
    size: 50,
    markets: markets
      ? [
          { id: 1, code: "US", name_ko: "미국" },
          { id: 2, code: "CA", name_ko: "캐나다" },
        ]
      : [],
    as_of: "2026-09-29",
    scope_note: SCOPE_NOTE,
  };
}

function stubApi(body: MatrixPageData = matrix(), seen?: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(VIEWER));
      if (input.includes("/readiness/matrix")) {
        seen?.push(input);
        return Promise.resolve(jsonResponse(body));
      }
      return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, size: 50 }));
    }),
  );
}

describe("시장 준비도 매트릭스", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("셀이 색과 함께 글자·건수로 표시된다 — 색만으로 구분하지 않는다", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    expect(await screen.findByRole("heading", { name: "시장 준비도" })).toBeInTheDocument();
    const green = await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" });
    expect(green).toHaveTextContent("판매가능");
    expect(green).toHaveTextContent("2/2");
    expect(screen.getByRole("button", { name: "SKU-RED US 진행·임박" })).toHaveTextContent("1/2");
    expect(screen.getByRole("button", { name: "SKU-RED CA 미충족" })).toHaveTextContent("0/1");
    // 대상외 셀은 건수가 없다(0/0을 보이지 않는다)
    const gray = screen.getByRole("button", { name: "SKU-GREEN CA 대상외" });
    expect(gray).toHaveTextContent("대상외");
    expect(gray).not.toHaveTextContent("0/0");
    // 열 머리는 시장 코드(줄바꿈 금지 클래스)
    expect(screen.getByRole("columnheader", { name: "US" })).toHaveClass("cell-nowrap");
  });

  it("범례에 색 의미 4개와 서버가 준 모집합 안내가 그대로 보인다 (조건 A)", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    const legend = await screen.findByLabelText("범례");
    for (const label of ["판매가능", "진행·임박", "미충족", "대상외"]) {
      expect(within(legend).getByText(label)).toBeInTheDocument();
    }
    // 범례 틀은 데이터보다 먼저 그려진다 — 서버 문구는 응답이 온 뒤에 붙는다.
    expect(await within(legend).findByText(SCOPE_NOTE)).toBeInTheDocument();
    expect(within(legend).getByText(/성분\(INGREDIENT\) 단위 요건은 집계에 포함되지 않/)).toBeInTheDocument();
    // 저장값이 아니라는 안내와 기준일
    expect(screen.getByText(/열 때마다 인증 진행 상태와 만료일로 계산한 값/)).toBeInTheDocument();
    expect(screen.getByText(/기준일 2026-09-29\(KST\)/)).toBeInTheDocument();
  });

  it("셀을 누르면 요건별 상태·구성품 출처·인증 상세 링크가 나온다", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    fireEvent.click(await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" }));
    const panel = await screen.findByLabelText("셀 상세");
    expect(within(panel).getByText("MoCRA 제품 리스팅")).toBeInTheDocument();
    expect(within(panel).getByText("US Agent 지정")).toBeInTheDocument();
    expect(within(panel).getAllByText("승인(유효)")).toHaveLength(2);
    expect(within(panel).getByText("자사(기업)")).toBeInTheDocument();
    const links = within(panel).getAllByRole("link", { name: "인증 상세" });
    expect(links[0]).toHaveAttribute("href", "/certifications?id=77");

    // 세트 셀: 어느 구성품에서 온 요건인지, 인스턴스가 없다는 사실과 사유
    fireEvent.click(screen.getByRole("button", { name: "SET-1 US 미충족" }));
    const setPanel = await screen.findByLabelText("셀 상세");
    expect(within(setPanel).getByText("(구성품 SKU-RED)")).toBeInTheDocument();
    expect(within(setPanel).getByText("인스턴스 없음")).toBeInTheDocument();
    expect(within(setPanel).getByText(/활성 인증 인스턴스가 없습니다/)).toBeInTheDocument();
    expect(within(setPanel).getByText(/세트 구성품: SKU-GREEN, SKU-RED/)).toBeInTheDocument();
    expect(within(setPanel).queryByRole("link")).not.toBeInTheDocument(); // 인스턴스 없으면 링크 없음

    // 같은 셀을 다시 누르면 닫힌다
    fireEvent.click(screen.getByRole("button", { name: "SET-1 US 미충족" }));
    expect(screen.queryByLabelText("셀 상세")).not.toBeInTheDocument();
  });

  it("요건이 없는 셀의 상세는 대상외의 이유와 조치를 말한다", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    fireEvent.click(await screen.findByRole("button", { name: "SKU-GREEN CA 대상외" }));
    const panel = await screen.findByLabelText("셀 상세");
    expect(within(panel).getByText(/필수 요건이 없습니다\(대상외\)/)).toBeInTheDocument();
    expect(within(panel).getByText(/품목군의 요건 세트에 확정된 요건/)).toBeInTheDocument();
  });

  it("시장이 하나도 없으면 열이 없는 이유를 안내한다", async () => {
    stubApi(matrix([{ ...(ROWS[0] as MatrixRow), cells: [] }], false));
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    expect(await screen.findByText(/등록된 시장이 없어 표에 열이 없습니다/)).toBeInTheDocument();
  });

  it("검색어·구분이 서버 질의로 나간다 — 화면이 색을 다시 계산하지 않는다", async () => {
    const seen: string[] = [];
    stubApi(matrix(), seen);
    renderWithProviders(<AppRoutes />, { route: "/readiness" });
    await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" });
    expect(seen[0]).toBe("/api/v1/readiness/matrix");

    fireEvent.change(screen.getByLabelText("구분"), { target: { value: "SET" } });
    await waitFor(() => expect(seen.some((path) => path.includes("kind=SET"))).toBe(true));

    fireEvent.change(screen.getByLabelText("SKU 검색"), { target: { value: "세럼" } });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));
    await waitFor(() =>
      expect(seen.some((path) => path.includes("q=%EC%84%B8%EB%9F%BC") && path.includes("kind=SET"))).toBe(true),
    );
  });

  it("SKU가 없으면 조치를 안내한다", async () => {
    stubApi(matrix([]));
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    expect(await screen.findByText(/등록된 SKU가 없습니다\. SKU 화면에서 먼저 등록/)).toBeInTheDocument();
  });

  it("네비게이션에 시장 준비도 항목이 있다", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "시장 준비도" })).toHaveAttribute(
      "href",
      "/readiness",
    );
  });
});
