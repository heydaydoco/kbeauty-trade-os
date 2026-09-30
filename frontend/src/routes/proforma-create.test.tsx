// QT → PI 만들기 — 진입 조건·미리보기 먼저·생성 확정·잔량 초과·검증·멱등 키·더블클릭·409 (S3-1 PR-6b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { PI_LOG, bankAccount, piDetail, piPreview } from "../test/pi-fixtures";
import { LOG, detail, stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const FROZEN_AT = "2026-09-30T02:00:00Z";
const issuedQt = (over = {}) => detail({ status: "ISSUED", frozen_at: FROZEN_AT, ...over });
const server = { qt: issuedQt() };

const PREVIEW_PATH = "/v1/quotations/7/proforma-invoices/preview";
const CREATE_PATH = "/v1/quotations/7/proforma-invoices";

const conflict = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } }, 409);
const exceeds = () =>
  jsonResponse(
    {
      error: {
        code: "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN",
        message: "가져올 수량이 남은 수량을 넘었습니다.",
        detail: { open_quantity: { "11": 2 } },
      },
    },
    409,
  );

function open(
  qt: ReturnType<typeof detail>,
  extra: Array<[string, string, () => Response]> = [],
  me: unknown = TRADER,
  banks: unknown[] = [bankAccount()],
) {
  server.qt = qt;
  const stub = stubFetch(me, [
    ...extra,
    ["/v1/bank-accounts", "GET", () => jsonResponse(page(banks))],
    ["/v1/quotations/7/status-log", "GET", () => jsonResponse(LOG)],
    ["/v1/proforma-invoices/5/status-log", "GET", () => jsonResponse(PI_LOG)],
    ["/v1/proforma-invoices/5", "GET", () => jsonResponse(piDetail())],
    ["/v1/system/currencies", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }]))],
    ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ["/v1/quotations/7", "GET", () => jsonResponse(server.qt)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/quotations/7" });
  return stub;
}

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "PI 만들기" }));
  return screen.findByRole("dialog", { name: "PI 만들기" });
}

/** 유효기간·은행 계좌를 채운다. */
async function fillRequired(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByLabelText("유효기간 *"), { target: { value: "2026-10-30" } });
  await within(dialog).findByRole("option", { name: "신한 USD — Shinhan Bank" });
  fireEvent.change(within(dialog).getByLabelText(/입금 은행 계좌/), { target: { value: "2" } });
}

const callsTo = (calls: Call[], path: string, method = "POST") =>
  calls.filter((c) => c.method === method && c.url.endsWith(path));

describe("QT 상세 — 'PI 만들기' 진입 조건", () => {
  it.each([
    ["ISSUED", true],
    ["CONVERTED", true],
    ["DRAFT", false],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("%s 견적: PI 만들기 노출=%s", async (status, shown) => {
    open(detail({ status, frozen_at: status === "DRAFT" ? null : FROZEN_AT }));
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    const button = screen.queryByRole("button", { name: "PI 만들기" });
    expect(button !== null).toBe(shown);
  });

  it("조회 전용 역할에게는 없다", async () => {
    open(issuedQt(), [], VIEWER);
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    expect(screen.queryByRole("button", { name: "PI 만들기" })).not.toBeInTheDocument();
  });

  it("유효기간이 지난 견적은 비활성", async () => {
    open(issuedQt({ is_lapsed: true }));
    expect(await screen.findByRole("button", { name: "PI 만들기" })).toBeDisabled();
  });
});

describe("PI 만들기 — 미리보기 먼저, 확정은 그다음", () => {
  it("입력 → 미리보기(저장 안 됨, 본문에 가격·통화 없음) → 서버 계산값 확인 → 생성 확정 → PI 상세로 이동", async () => {
    const { calls } = open(issuedQt(), [
      [PREVIEW_PATH, "POST", () => jsonResponse(piPreview())],
      [CREATE_PATH, "POST", () => jsonResponse(piDetail(), 201)],
    ]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));

    // 미리보기 화면 — 서버 계산 문자열 그대로, 아직 생성 호출 없음.
    expect(await within(dialog).findByText(/아직 저장되지 않았습니다/)).toBeInTheDocument();
    expect(within(dialog).getByText("75.00 USD")).toBeInTheDocument();
    expect(within(dialog).getByText("22.50 USD")).toBeInTheDocument();
    expect(within(dialog).getByText("Shinhan Bank")).toBeInTheDocument();
    expect(within(dialog).getByText("남은 수량")).toBeInTheDocument();
    expect(callsTo(calls, CREATE_PATH)).toHaveLength(0);
    const preview = callsTo(calls, PREVIEW_PATH)[0];
    expect(preview?.body).toEqual({ version: 4, valid_until: "2026-10-30", bank_account_id: 2 });
    // 재입력 필드가 없다 — 원천 값(SKU·단가·통화·환율·거래처)을 본문에 싣지 않는다.
    for (const key of ["sku_id", "unit_price_amount", "currency", "fx_rate", "buyer_partner_id", "lines"]) {
      expect(preview?.body).not.toHaveProperty(key);
    }

    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    expect(await screen.findByRole("heading", { name: /PI-2026-0001/ })).toBeInTheDocument();
    const create = callsTo(calls, CREATE_PATH)[0];
    expect(create?.body).toEqual(preview?.body); // 미리보기 때의 본문 그대로
    expect(create?.headers["Idempotency-Key"]).toMatch(/^key-\d+$/); // 다이얼로그가 들고 있는 키(멱등 키 헤더 필수)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("필수값 검증: 유효기간·은행 계좌 없이는 미리보기를 보내지 않는다", async () => {
    const { calls } = open(issuedQt(), [[PREVIEW_PATH, "POST", () => jsonResponse(piPreview())]]);
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("유효기간을 입력해 주세요.");
    fireEvent.change(within(dialog).getByLabelText("유효기간 *"), { target: { value: "2026-10-30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("입금 은행 계좌를 선택해 주세요.");
    fireEvent.change(within(dialog).getByLabelText("증빙일 (비우면 오늘)"), { target: { value: "2026-11-05" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("유효기간은 증빙일보다 앞설 수 없습니다.");
    expect(callsTo(calls, PREVIEW_PATH)).toHaveLength(0);
  });

  it("은행 계좌는 견적 통화로 좁혀 조회하고, 없으면 등록 안내를 보인다", async () => {
    const { calls } = open(issuedQt(), [], TRADER, []);
    const dialog = await openDialog();
    expect(await within(dialog).findByRole("status")).toHaveTextContent("USD 계좌가 없습니다. 관리자에게 계좌 등록을 요청하세요.");
    expect(calls.some((c) => c.method === "GET" && c.url.includes("/v1/bank-accounts?currency=USD"))).toBe(true);
  });

  it("라인·수량 직접 지정: 수량 형식을 화면에서 검증하고, 지정한 라인만 lines로 보낸다", async () => {
    const { calls } = open(issuedQt(), [[PREVIEW_PATH, "POST", () => jsonResponse(piPreview())]]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("radio", { name: "라인·수량 직접 지정" }));
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("라인 1(SKU-001)의 수량은 1 이상의 정수로 입력해 주세요.");
    fireEvent.click(within(dialog).getByLabelText("라인 1 선택"));
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("가져올 라인을 하나 이상 선택해 주세요.");
    fireEvent.click(within(dialog).getByLabelText("라인 1 선택"));
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "6" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    await waitFor(() => expect(callsTo(calls, PREVIEW_PATH)).toHaveLength(1));
    expect(callsTo(calls, PREVIEW_PATH)[0]?.body).toEqual({
      version: 4,
      valid_until: "2026-10-30",
      bank_account_id: 2,
      lines: [{ source_line_id: 11, quantity: 6 }],
    });
  });

  it("잔량 초과(409)는 서버 문구와 라인별 남은 수량을 보인다", async () => {
    open(issuedQt(), [[PREVIEW_PATH, "POST", exceeds]]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("가져올 수량이 남은 수량을 넘었습니다.");
    expect(alert).toHaveTextContent("라인 1 (SKU-001): 남은 수량 2");
    // 미리보기로 넘어가지 않고 입력 화면에 머문다.
    expect(within(dialog).getByRole("button", { name: "미리보기" })).toBeInTheDocument();
  });

  it("생성 단계의 잔량 초과(다른 사람이 그 사이 생성)도 미리보기 화면에서 라인별 잔량을 보이고, 입력 수정으로 돌아갈 수 있다", async () => {
    open(issuedQt(), [
      [PREVIEW_PATH, "POST", () => jsonResponse(piPreview())],
      [CREATE_PATH, "POST", exceeds],
    ]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("라인 1 (SKU-001): 남은 수량 2");
    fireEvent.click(within(dialog).getByRole("button", { name: "입력 수정" }));
    expect(await within(dialog).findByRole("button", { name: "미리보기" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("생성 확정 더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(issuedQt(), [[PREVIEW_PATH, "POST", () => jsonResponse(piPreview())]]);
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith(CREATE_PATH) && i?.method === "POST") {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    const confirm = await within(dialog).findByRole("button", { name: "생성 확정" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(callsTo(calls, CREATE_PATH)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(callsTo(calls, CREATE_PATH)).toHaveLength(1);
    expect(within(dialog).getByRole("button", { name: "만드는 중…" })).toBeDisabled();
    release(jsonResponse(piDetail(), 201));
  });

  it("생성 실패 뒤 재시도는 같은 멱등 키·같은 본문으로 나간다", async () => {
    let fail = true;
    const { calls } = open(issuedQt(), [
      [PREVIEW_PATH, "POST", () => jsonResponse(piPreview())],
      [
        CREATE_PATH,
        "POST",
        () => (fail ? jsonResponse({ error: { code: "X", message: "일시 오류입니다." } }, 500) : jsonResponse(piDetail(), 201)),
      ],
    ]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("일시 오류입니다.");
    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(callsTo(calls, CREATE_PATH)).toHaveLength(2));
    fail = false;
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "생성 확정" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    await screen.findByRole("heading", { name: /PI-2026-0001/ });
    const sent = callsTo(calls, CREATE_PATH);
    expect(new Set(sent.map((c) => c.headers["Idempotency-Key"])).size).toBe(1); // 3회 모두 같은 키
    expect(new Set(sent.map((c) => JSON.stringify(c.body))).size).toBe(1);
  });

  it("입력을 바꾸면 이전 시도와 다른 새 멱등 키로 생성한다", async () => {
    const { calls } = open(issuedQt(), [
      [PREVIEW_PATH, "POST", () => jsonResponse(piPreview())],
      [CREATE_PATH, "POST", () => jsonResponse({ error: { code: "X", message: "일시 오류입니다." } }, 500)],
    ]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "입력 수정" }));
    fireEvent.change(await within(dialog).findByLabelText("유효기간 *"), { target: { value: "2026-11-15" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(callsTo(calls, CREATE_PATH)).toHaveLength(2));
    const [first, second] = callsTo(calls, CREATE_PATH);
    expect(first?.headers["Idempotency-Key"]).not.toBe(second?.headers["Idempotency-Key"]);
    expect(second?.body).toMatchObject({ valid_until: "2026-11-15" });
  });

  it("원천 견적이 그 사이 바뀌면(409) 다이얼로그 안에서 '최신 내용 불러오기' → 닫고 견적을 재조회한다", async () => {
    const { calls } = open(issuedQt(), [[PREVIEW_PATH, "POST", conflict]]);
    const dialog = await openDialog();
    await fillRequired(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("다른 곳에서 이 견적이 먼저 수정되었습니다");
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length;
    fireEvent.click(within(alert).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length).toBeGreaterThan(before),
    );
  });

  it("Esc로 닫히고 포커스가 'PI 만들기' 버튼으로 돌아온다 / 포커스는 다이얼로그 안으로 들어간다", async () => {
    open(issuedQt());
    const opener = await screen.findByRole("button", { name: "PI 만들기" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "PI 만들기" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });
});
