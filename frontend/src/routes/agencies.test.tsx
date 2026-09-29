// 대행사 화면 — 스코어카드(계산값)·대행 계약 (S2-4 PR-1 안건 ③·①).
//
// 고정하는 계약: ① 서버 값을 그대로 보인다(비율·소요일·수수료 서식 — 화면이 다시 계산·환산하지 않는다)
// ② 분모 0(None)은 0%가 아니라 "—" ③ 지표 정의 문구는 서버 note ④ 편집 UI는 인증+관리자만
// ⑤ 등록·수정 요청의 모양(수수료 표기·version·null 해제) ⑥ 계산값 화면이라 캐시를 믿지 않는다.

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const CERT_USER = { id: 2, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

const CURRENCIES = {
  items: [
    { code: "KRW", minor_units: 0 },
    { code: "USD", minor_units: 2 },
  ],
  total: 2,
  page: 1,
  size: 200,
};

const SCORE = {
  partner_id: 1,
  partner_code: "AGY-1",
  partner_name: "가 대행사",
  case_count: 5,
  submitted_count: 4,
  approved_count: 3,
  supplemented_count: 1,
  supplement_rate: 0.25,
  lead_sample_count: 3,
  lead_days_avg: 8.3,
  lead_days_median: 10,
  current_contract: {
    id: 9,
    contract_no: "CT-2026-01",
    start_on: "2026-01-01",
    end_on: null,
    fee_amount: 123456,
    fee_currency: "USD",
    scope_note: "건당",
  },
};

const EMPTY_SCORE = {
  ...SCORE,
  partner_id: 2,
  partner_code: "AGY-2",
  partner_name: "나 대행사",
  case_count: 0,
  submitted_count: 0,
  approved_count: 0,
  supplemented_count: 0,
  supplement_rate: null,
  lead_sample_count: 0,
  lead_days_avg: null,
  lead_days_median: null,
  current_contract: null,
};

const CONTRACT = {
  id: 9,
  partner_id: 1,
  partner_name: "가 대행사",
  contract_no: "CT-2026-01",
  scope_note: "건당",
  start_on: "2026-01-01",
  end_on: null,
  fee_amount: 123456,
  fee_currency: "USD",
  note: null,
  version: 3,
  is_current: true,
};

const EXPIRED_CONTRACT = {
  ...CONTRACT,
  id: 10,
  contract_no: "CT-OLD",
  start_on: "2025-01-01",
  end_on: "2025-12-31",
  fee_amount: null,
  fee_currency: null,
  is_current: false,
};

const AGENCY_PARTNER = {
  id: 1,
  partner_code: "AGY-1",
  name_ko: "가 대행사",
  type_codes: ["CERT_AGENCY"],
  credit_limit_amount: null,
  credit_limit_currency: null,
  dg_capable: null,
  strengths: null,
  weaknesses: null,
  note: null,
};
const SUPPLIER_PARTNER = { ...AGENCY_PARTNER, id: 5, name_ko: "공급사", type_codes: ["SUPPLIER"] };

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

function stubApi(me: unknown, calls: Recorded[] = [], scores: unknown[] = [SCORE, EMPTY_SCORE]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url: input,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/agencies/scorecard")) {
        return Promise.resolve(
          jsonResponse({ ...page(scores), note: "서버가 준 지표 정의 문구입니다." }),
        );
      }
      if (input.includes("/v1/agency-contracts")) {
        if (method === "POST") return Promise.resolve(jsonResponse(CONTRACT, 201));
        if (method === "PATCH") return Promise.resolve(jsonResponse({ ...CONTRACT, version: 4 }));
        if (method === "DELETE") return Promise.resolve({ ok: true, status: 204 } as Response);
        return Promise.resolve(jsonResponse(page([CONTRACT, EXPIRED_CONTRACT])));
      }
      if (input.includes("/v1/partners")) {
        return Promise.resolve(jsonResponse(page([AGENCY_PARTNER, SUPPLIER_PARTNER])));
      }
      if (input.includes("/v1/system/currencies")) return Promise.resolve(jsonResponse(CURRENCIES));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

/** 스코어카드 표의 한 행 — 같은 이름이 계약 표에도 나오므로 표를 이름으로 좁힌다. */
async function scoreRow(name: string): Promise<HTMLElement> {
  const table = await screen.findByRole("table", { name: "대행사 스코어카드" });
  const cell = await within(table).findByText(name, { selector: "td" });
  return cell.closest("tr") as HTMLElement;
}

/** 계약 표의 한 행(계약 번호 기준). */
async function contractRow(number: string): Promise<HTMLElement> {
  const table = await screen.findByRole("table", { name: "대행 계약 목록" });
  const cell = await within(table).findByText(number, { selector: "td" });
  return cell.closest("tr") as HTMLElement;
}

describe("대행사 스코어카드", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("서버가 계산한 값을 서식만 입혀 보인다 — 비율·소요일·현행 계약 수수료(통화표 서식)", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    expect(await screen.findByRole("heading", { name: "대행사" })).toBeInTheDocument();
    const cells = within(await scoreRow("가 대행사"));
    expect(cells.getByText("25.0% (1/4)")).toBeInTheDocument();
    expect(cells.getByText("평균 8.3일 · 중앙 10일 (3건)")).toBeInTheDocument();
    await waitFor(() => expect(cells.getByText("1,234.56 USD")).toBeInTheDocument());
    expect(cells.getByText("CT-2026-01")).toBeInTheDocument();
  });

  it("분모 0·표본 0은 0%가 아니라 '—'다 — 아직 모른다와 0은 다른 사실이다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const cells = within(await scoreRow("나 대행사"));
    expect(cells.getAllByText("—").length).toBeGreaterThanOrEqual(3); // 소요일·보완율·계약
    expect(cells.queryByText(/0\.0%/)).not.toBeInTheDocument();
  });

  it("지표 정의 문구는 서버 note 그대로다 — 화면에 복사본이 없다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    expect(await screen.findByTestId("scorecard-note")).toHaveTextContent(
      "서버가 준 지표 정의 문구입니다.",
    );
  });

  it("빈 명단은 안내를 보인다", async () => {
    stubApi(TRADER, [], []);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    expect(await screen.findByText(/집계할 대행사가 없습니다/)).toBeInTheDocument();
  });

  it("운영 캐시(staleTime 30초)에서도 다시 들어오면 새로 가져온다 — 지정·전이 직후 옛 숫자가 남지 않는다", async () => {
    const calls: Recorded[] = [];
    stubApi(TRADER, calls);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } }, // 운영(queryClient.ts)과 같은 값
    });
    const scorecardCalls = () =>
      calls.filter((call) => call.url.includes("/v1/agencies/scorecard")).length;

    const first = renderWithProviders(<AppRoutes />, { route: "/agencies", client });
    await scoreRow("가 대행사");
    expect(scorecardCalls()).toBe(1);
    first.unmount();

    renderWithProviders(<AppRoutes />, { route: "/agencies", client });
    await scoreRow("가 대행사");
    await waitFor(() => expect(scorecardCalls()).toBeGreaterThanOrEqual(2));
  });
});

describe("대행 계약 화면", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("계약 목록 — 현행 배지·기간 미정 표기·수수료 서식, 미기재 수수료는 '—'", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const current = await contractRow("CT-2026-01");
    expect(within(current).getByText("현행")).toBeInTheDocument();
    expect(within(current).getByText("2026-01-01 ~ 기간 미정")).toBeInTheDocument();
    const old = await contractRow("CT-OLD");
    expect(within(old).getByText("현행 아님")).toBeInTheDocument();
    expect(within(old).getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("통화표가 늦게 오면 수수료 자리에 '…'를 보이고 화면이 죽지 않는다 — 도착하면 서식이 붙는다", async () => {
    let releaseCurrencies: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseCurrencies = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.includes("/v1/system/currencies")) {
          return gate.then(() => jsonResponse(CURRENCIES));
        }
        if (input.includes("/v1/agencies/scorecard")) {
          return Promise.resolve(jsonResponse({ ...page([SCORE]), note: "정의" }));
        }
        if (input.includes("/v1/agency-contracts")) {
          return Promise.resolve(jsonResponse(page([CONTRACT])));
        }
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const row = await contractRow("CT-2026-01");
    expect(within(row).getByText("…")).toBeInTheDocument(); // 통화표 전 — 서식을 만들지 않는다
    releaseCurrencies();
    await waitFor(() => expect(within(row).getByText("1,234.56 USD")).toBeInTheDocument());
  });

  it("통화표에 없는 코드는 화면을 죽이지 않고 원값을 그대로 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.includes("/v1/system/currencies")) return Promise.resolve(jsonResponse(CURRENCIES));
        if (input.includes("/v1/agencies/scorecard")) {
          return Promise.resolve(jsonResponse({ ...page([]), note: "정의" }));
        }
        if (input.includes("/v1/agency-contracts")) {
          return Promise.resolve(jsonResponse(page([{ ...CONTRACT, fee_amount: 777, fee_currency: "XYZ" }])));
        }
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const row = await contractRow("CT-2026-01");
    await waitFor(() => expect(within(row).getByText("777 XYZ")).toBeInTheDocument());
  });

  it("무역 역할에는 등록 폼·수정 버튼이 없다 (편집=인증+관리자)", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    await contractRow("CT-2026-01");
    expect(screen.queryByRole("form", { name: "대행 계약 등록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
  });

  it("인증 역할이 등록하면 사람이 쓰는 표기로 요청한다 — 대행사는 인증대행 유형만 고른다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, calls);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const form = await screen.findByRole("form", { name: "대행 계약 등록" });
    await waitFor(() => expect(within(form).getByRole("option", { name: "가 대행사" })).toBeInTheDocument());
    // 공급사 유형 거래처는 대행사 선택지에 없다(서버 검증과 같은 경계).
    expect(within(form).queryByRole("option", { name: "공급사" })).not.toBeInTheDocument();
    await waitFor(() => expect(within(form).getByRole("option", { name: "USD" })).toBeInTheDocument());

    fireEvent.change(within(form).getByLabelText("대행사"), { target: { value: "1" } });
    fireEvent.change(within(form).getByLabelText("계약 번호"), { target: { value: "CT-NEW" } });
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("수수료"), { target: { value: "12.34" } });
    fireEvent.change(within(form).getByLabelText("통화"), { target: { value: "USD" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.includes("agency-contracts"));
    expect(post?.body).toMatchObject({
      partner_id: 1,
      contract_no: "CT-NEW",
      start_on: "2026-10-01",
      fee: "12.34",
      fee_currency: "USD",
    });
    // 비운 선택 필드는 아예 보내지 않는다(빈 문자열이 서버 검증에 걸리지 않게).
    expect(post?.body).not.toHaveProperty("end_on");
  });

  it("수정 폼은 최소단위 수수료를 입력 표기로 되돌리고, 비운 항목은 null로 보낸다(낙관 잠금 version 동반)", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, calls);
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const row = await contractRow("CT-2026-01");
    await waitFor(() => expect(within(row).getByText("1,234.56 USD")).toBeInTheDocument());
    fireEvent.click(within(row).getByRole("button", { name: "수정" }));

    const form = await screen.findByRole("form", { name: "대행 계약 수정" });
    expect(within(form).getByLabelText("수수료")).toHaveValue("1234.56"); // 123456 ÷ 100, 쉼표 없음
    expect(within(form).getByLabelText("계약 번호")).toHaveValue("CT-2026-01");
    fireEvent.change(within(form).getByLabelText("범위·수수료 기준"), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toContain("/v1/agency-contracts/9");
    expect(patch?.body).toMatchObject({
      version: 3,
      contract_no: "CT-2026-01",
      fee: "1234.56",
      fee_currency: "USD",
      end_on: null,
      scope_note: null,
    });
  });

  it("삭제는 확인을 거친다 — 취소하면 요청이 나가지 않는다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, calls);
    const confirm = vi.spyOn(window, "confirm");
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const row = await contractRow("CT-2026-01");
    await waitFor(() => expect(within(row).getByText("1,234.56 USD")).toBeInTheDocument());
    fireEvent.click(within(row).getByRole("button", { name: "수정" }));
    const form = await screen.findByRole("form", { name: "대행 계약 수정" });

    confirm.mockReturnValueOnce(false);
    fireEvent.click(within(form).getByRole("button", { name: "삭제" }));
    // 요청은 비동기로 나간다 — 확인 창이 뜬 뒤 한 박자 기다려야 "안 나갔다"가 빈 검증이 되지 않는다.
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    confirm.mockReturnValueOnce(true);
    fireEvent.click(within(form).getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("서버가 거절하면 필드 문구를 그대로 보인다(화면이 문구를 지어내지 않는다)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(CERT_USER));
        if (input.includes("/v1/agency-contracts") && init?.method === "POST") {
          return Promise.resolve(
            jsonResponse(
              {
                error: {
                  code: "COMMON.VALIDATION.INVALID_FIELD",
                  message: "입력값을 확인해 주세요.",
                  detail: { contract_no: "같은 대행사에 이미 등록된 계약 번호입니다." },
                  request_id: null,
                },
              },
              422,
            ),
          );
        }
        if (input.includes("/v1/partners")) return Promise.resolve(jsonResponse(page([AGENCY_PARTNER])));
        if (input.includes("/v1/system/currencies")) return Promise.resolve(jsonResponse(CURRENCIES));
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/agencies" });

    const form = await screen.findByRole("form", { name: "대행 계약 등록" });
    await waitFor(() => expect(within(form).getByRole("option", { name: "가 대행사" })).toBeInTheDocument());
    fireEvent.change(within(form).getByLabelText("대행사"), { target: { value: "1" } });
    fireEvent.change(within(form).getByLabelText("계약 번호"), { target: { value: "DUP" } });
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "같은 대행사에 이미 등록된 계약 번호입니다.",
    );
  });
});
