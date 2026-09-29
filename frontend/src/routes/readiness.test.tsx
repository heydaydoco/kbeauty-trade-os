// 시장 준비도 매트릭스 화면 — 색+글자 표기·범례(조건 A)·셀 상세·필터 요청 (S2-3 PR-3).
//
// 고정하는 것: ① 색만으로 구분하지 않는다(글자·건수 병기) ② 모집합 안내는 **서버가 준
// 문구 그대로**다 ③ 셀 클릭 상세가 요건별 상태(한국어)·구성품 출처·인증 상세 링크를 보인다
// ④ 열이 없으면 이유를 말한다 ⑤ 필터가 서버 질의로 나간다(화면이 색을 다시 계산하지 않는다).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { VIEWER, jsonResponse, renderWithProviders } from "../test/render";
import type { MatrixCell, MatrixPageData, MatrixRow } from "./readiness";

const SCOPE_NOTE =
  "집계 모집합: 품목군 요건 세트의 확정 요건 중 SKU·제품·자사(기업)·제조사(시설) 단위만 셉니다. 성분(INGREDIENT) 단위 요건은 집계에 포함되지 않으므로 '판매가능'이 성분 요건 충족까지 뜻하지는 않습니다.";

function cell(
  market: number,
  code: string,
  color: string,
  extra: Partial<MatrixCell> = {},
): MatrixCell {
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
    expect(
      within(legend).getByText(/성분\(INGREDIENT\) 단위 요건은 집계에 포함되지 않/),
    ).toBeInTheDocument();
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
      expect(
        seen.some((path) => path.includes("q=%EC%84%B8%EB%9F%BC") && path.includes("kind=SET")),
      ).toBe(true),
    );
  });

  it("SKU가 없으면 조치를 안내한다", async () => {
    stubApi(matrix([]));
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    expect(
      await screen.findByText(/등록된 SKU가 없습니다\. SKU 화면에서 먼저 등록/),
    ).toBeInTheDocument();
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

// ── 적대적 리뷰 반영: 색 클래스·서버 값 신뢰·행 표식·선택 표시·캐시 신선도 ────────────

describe("시장 준비도 매트릭스 — 표시 계약", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("셀 배경이 서버가 준 색 코드를 따른다 (색 표를 맞바꾸면 실패)", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    expect(await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" })).toHaveClass(
      "bg-signal-green/30",
    );
    expect(screen.getByRole("button", { name: "SKU-RED US 진행·임박" })).toHaveClass(
      "bg-signal-amber/35",
    );
    expect(screen.getByRole("button", { name: "SKU-RED CA 미충족" })).toHaveClass(
      "bg-signal-red/30",
    );
    expect(screen.getByRole("button", { name: "SKU-GREEN CA 대상외" })).toHaveClass("bg-gray-100");
  });

  it("화면이 건수로 색을 다시 계산하지 않는다 — 서버 색이 곧 표시다", async () => {
    // 일부러 모순된 응답: 색은 GREEN인데 건수는 승인 0/2. 화면이 건수로 색을 정하면 판매가능이 안 뜬다.
    const contradictory: MatrixRow = {
      ...(ROWS[0] as MatrixRow),
      sku_code: "SKU-LIAR",
      cells: [
        cell(1, "US", "GREEN", { required: 2, approved: 0, unmet: 2 }),
        cell(2, "CA", "GRAY"),
      ],
    };
    stubApi(matrix([contradictory]));
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    const liar = await screen.findByRole("button", { name: "SKU-LIAR US 판매가능" });
    expect(liar).toHaveTextContent("판매가능");
    expect(liar).toHaveTextContent("0/2");
    expect(liar).toHaveClass("bg-signal-green/30");
  });

  it("범례의 모집합 문구는 서버가 준 값을 그대로 쓴다 — 화면에 복사본이 없다", async () => {
    stubApi({ ...matrix(), scope_note: "서버가-준-센티널-문구" });
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    const legend = await screen.findByLabelText("범례");
    expect(await within(legend).findByText("서버가-준-센티널-문구")).toBeInTheDocument();
    expect(within(legend).queryByText(/집계 모집합/)).not.toBeInTheDocument();
  });

  it("단종·품목군 미지정 SKU는 행 머리에 표식이 붙는다 (대상외가 '요건 없음'으로만 읽히지 않게)", async () => {
    const discontinued: MatrixRow = {
      ...(ROWS[0] as MatrixRow),
      sku_id: 9,
      sku_code: "SKU-OLD",
      status: "DISCONTINUED",
      item_profile_id: null,
    };
    stubApi(matrix([discontinued, ROWS[0] as MatrixRow]));
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    const oldRow = (await screen.findByRole("rowheader", { name: /SKU-OLD/ })) as HTMLElement;
    expect(within(oldRow).getByText("단종")).toBeInTheDocument();
    expect(within(oldRow).getByText("품목군 미지정")).toBeInTheDocument();
    const normalRow = screen.getByRole("rowheader", { name: /SKU-GREEN/ });
    expect(within(normalRow).queryByText("단종")).not.toBeInTheDocument();
    expect(within(normalRow).queryByText("품목군 미지정")).not.toBeInTheDocument();
  });

  it("셀을 누르면 선택 표시(링)가 붙고 상세 패널이 화면으로 스크롤되며, 다시 누르면 사라진다", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    const target = await screen.findByRole("button", { name: "SKU-RED CA 미충족" });
    expect(target).not.toHaveClass("ring-2");
    fireEvent.click(target);
    await screen.findByLabelText("셀 상세");
    expect(target).toHaveClass("ring-2");
    expect(target).toHaveAttribute("aria-pressed", "true");
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: "nearest" }));

    // 다른 셀을 고르면 다시 스크롤하고 링은 옮겨 간다
    const other = screen.getByRole("button", { name: "SKU-GREEN US 판매가능" });
    fireEvent.click(other);
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(2));
    expect(other).toHaveClass("ring-2");
    expect(target).not.toHaveClass("ring-2");

    fireEvent.click(other);
    expect(screen.queryByLabelText("셀 상세")).not.toBeInTheDocument();
    expect(other).not.toHaveClass("ring-2");
  });

  it("셀 상세 제목의 시장명은 줄바꿈하지 않는다 (국가명 nowrap 수칙)", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    fireEvent.click(await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" }));
    const panel = await screen.findByLabelText("셀 상세");
    expect(within(panel).getByText("미국(US)")).toHaveClass("cell-nowrap");
  });

  it("화면 부제는 판정 문구 없이 진행 상태 요약이라고 말한다", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/readiness" });

    expect(await screen.findByText(/인증 요건의 진행 상태를 한눈에 봅니다/)).toBeInTheDocument();
    expect(screen.queryByText(/팔 준비/)).not.toBeInTheDocument();
  });

  it("운영 캐시(staleTime 30초)에서도 다시 들어오면 새로 가져온다 — 정정 직후 옛 색이 남지 않는다", async () => {
    const seen: string[] = [];
    stubApi(matrix(), seen);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } }, // 운영(queryClient.ts)과 같은 값
    });
    const first = renderWithProviders(<AppRoutes />, { route: "/readiness", client });
    await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" });
    expect(seen).toHaveLength(1);
    first.unmount();

    renderWithProviders(<AppRoutes />, { route: "/readiness", client }); // 다른 화면에 다녀온 뒤 재진입
    await screen.findByRole("button", { name: "SKU-GREEN US 판매가능" });
    await waitFor(() => expect(seen.length).toBeGreaterThanOrEqual(2));
  });
});
