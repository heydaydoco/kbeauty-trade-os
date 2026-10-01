// QT/PI → SO 만들기 — 진입 조건·확인 단계·본문에 원천 값 없음·중복 PO 409·잔량 초과·PI당 1건·검증·멱등 키·더블클릭·409 (S3-1 PR-7b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { PAYMENT_SUMMARY } from "../test/payment-fixtures";
import { PI_LOG, piDetail } from "../test/pi-fixtures";
import { LOG, detail, stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { SO_LOG, chainFlow, soDetail } from "../test/so-fixtures";

let keySeq = 0;
const issued: string[] = [];
beforeEach(() => {
  keySeq = 0;
  issued.length = 0;
  vi.stubGlobal("crypto", {
    randomUUID: () => {
      const key = `key-${++keySeq}`;
      issued.push(key);
      return key;
    },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const FROZEN_AT = "2026-09-30T02:00:00Z";
const issuedQt = (over = {}) => detail({ status: "ISSUED", frozen_at: FROZEN_AT, ...over });
const server = { qt: issuedQt(), pi: piDetail() };

const QT_CREATE = "/v1/quotations/7/sales-orders";
const PI_CREATE = "/v1/proforma-invoices/5/sales-orders";

const conflict = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } }, 409);
const duplicatePo = () =>
  jsonResponse(
    {
      error: {
        code: "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO",
        message: "같은 바이어의 같은 PO번호가 이미 다른 수주에 등록되어 있습니다. 기존 수주를 확인하거나, 정정이라면 기존 수주를 취소한 뒤 다시 등록해 주세요.",
        detail: { doc_number: "SO-2026-0007", status: "RECEIVED" },
      },
    },
    409,
  );
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
const alreadyConverted = () =>
  jsonResponse(
    {
      error: {
        code: "TRADE_DOCS.REFERENCE.ALREADY_CONVERTED",
        message: "이 문서에서 이미 수주가 만들어졌습니다.",
        detail: { doc_number: "SO-2026-0003", status: "CONFIRMED" },
      },
    },
    409,
  );

const COMMON: Array<[string, string, () => Response]> = [
  ["/v1/quotations/7/status-log", "GET", () => jsonResponse(LOG)],
  ["/v1/proforma-invoices/5/status-log", "GET", () => jsonResponse(PI_LOG)],
  ["/v1/sales-orders/9/status-log", "GET", () => jsonResponse(SO_LOG)],
  ["/v1/document-flow/SALES_ORDER/9", "GET", () => jsonResponse(chainFlow("SALES_ORDER"))],
  ["/v1/document-flow/QUOTATION/7", "GET", () => jsonResponse(chainFlow("QUOTATION", false))],
  ["/v1/document-flow/PROFORMA_INVOICE/5", "GET", () => jsonResponse(chainFlow("PROFORMA_INVOICE", false))],
  ["/v1/system/currencies", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }]))],
  ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
  ["/v1/markets", "GET", () => jsonResponse(page([]))],
];

function openQt(qt: ReturnType<typeof detail>, extra: Array<[string, string, () => Response]> = [], me: unknown = TRADER) {
  server.qt = qt;
  const stub = stubFetch(me, [
    ...extra,
    ...COMMON,
    ["/v1/sales-orders/9", "GET", () => jsonResponse(soDetail())],
    ["/v1/quotations/7", "GET", () => jsonResponse(server.qt)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/quotations/7" });
  return stub;
}

function openPi(pi: ReturnType<typeof piDetail>, extra: Array<[string, string, () => Response]> = [], me: unknown = TRADER) {
  server.pi = pi;
  const stub = stubFetch(me, [
    ...extra,
    ...COMMON,
    ["/v1/sales-orders/9", "GET", () => jsonResponse(soDetail())],
    ["/v1/proforma-invoices/5/payments", "GET", () => jsonResponse({ ...page([]), summary: PAYMENT_SUMMARY })],
    ["/v1/proforma-invoices/5", "GET", () => jsonResponse(server.pi)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/proforma-invoices/5" });
  return stub;
}

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "SO 만들기" }));
  return screen.findByRole("dialog", { name: "수주 만들기" });
}

const callsTo = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.url.endsWith(path));
const next = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole("button", { name: "다음: 내용 확인" }));
const confirm = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));

describe("'SO 만들기' 진입 조건", () => {
  it.each([
    ["ISSUED", true],
    ["CONVERTED", true],
    ["DRAFT", false],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("QT %s: 노출=%s", async (status, shown) => {
    openQt(detail({ status, frozen_at: status === "DRAFT" ? null : FROZEN_AT }));
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    expect(screen.queryByRole("button", { name: "SO 만들기" }) !== null).toBe(shown);
  });

  it.each([
    ["ISSUED", true],
    ["PARTIALLY_PAID", true],
    ["PAID", true],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("PI %s: 노출=%s", async (status, shown) => {
    openPi(piDetail({ status }));
    await screen.findByRole("heading", { name: /PI-2026-0001/ });
    expect(screen.queryByRole("button", { name: "SO 만들기" }) !== null).toBe(shown);
  });

  it("조회 전용 역할에게는 없다", async () => {
    openQt(issuedQt(), [], VIEWER);
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    expect(screen.queryByRole("button", { name: "SO 만들기" })).not.toBeInTheDocument();
  });

  it("유효기간이 지난 QT·미입금 PI는 비활성, 입금된 PI는 유효기간과 무관하게 활성", async () => {
    openQt(issuedQt({ is_lapsed: true }));
    expect(await screen.findByRole("button", { name: "SO 만들기" })).toBeDisabled();
  });

  it("미입금 발행 PI가 유효기간을 넘기면 비활성", async () => {
    openPi(piDetail({ status: "ISSUED", is_lapsed: true }));
    expect(await screen.findByRole("button", { name: "SO 만들기" })).toBeDisabled();
  });

  it("입금한 PI는 유효기간이 지났어도 활성(선수금이 갇히지 않게 — 서버 규칙과 같다)", async () => {
    openPi(piDetail({ status: "PAID", is_lapsed: true }));
    expect(await screen.findByRole("button", { name: "SO 만들기" })).toBeEnabled();
  });

  it("QT·PI 상세에 문서 흐름 패널이 있다", async () => {
    openQt(issuedQt());
    expect(await screen.findByRole("heading", { name: "문서 흐름" })).toBeInTheDocument();
  });
});

describe("최신 불러오기 — 문서 흐름 재조회", () => {
  it.each([
    ["QT", "/v1/document-flow/QUOTATION/7"],
    ["PI", "/v1/document-flow/PROFORMA_INVOICE/5"],
  ])("%s 상세의 '최신 내용 불러오기'는 문서 흐름도 다시 가져온다", async (kind, path) => {
    const { calls } = kind === "QT" ? openQt(issuedQt()) : openPi(piDetail());
    await screen.findByRole("heading", { name: "문서 흐름" });
    const count = () => calls.filter((c) => c.method === "GET" && c.url.endsWith(path)).length;
    await waitFor(() => expect(count()).toBeGreaterThan(0));
    const before = count();
    fireEvent.click(screen.getAllByRole("button", { name: "최신 내용 불러오기" })[0]!);
    await waitFor(() => expect(count()).toBeGreaterThan(before));
  });
});

describe("QT → SO 만들기", () => {
  it("입력 → 확인 단계(아직 생성 안 됨) → 생성 확정 → SO 상세로 이동, 본문에 원천 값(SKU·단가·통화·거래처)이 없다", async () => {
    const { calls } = openQt(issuedQt(), [[QT_CREATE, "POST", () => jsonResponse(soDetail(), 201)]]);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: " PO-2026-001 " } });
    fireEvent.change(within(dialog).getByLabelText("바이어 PO 일자 (선택)"), { target: { value: "2026-09-29" } });
    next(dialog);

    // 확인 단계 — 보낼 내용 요약, 아직 생성 호출 없음.
    expect(await within(dialog).findByRole("heading", { name: /수주를 만들까요/ })).toBeInTheDocument();
    expect(within(dialog).getByText("PO-2026-001")).toBeInTheDocument();
    expect(within(dialog).getByText("남은 수량 전부")).toBeInTheDocument();
    expect(callsTo(calls, QT_CREATE)).toHaveLength(0);

    confirm(dialog);
    expect(await screen.findByRole("heading", { name: /SO-2026-0001/ })).toBeInTheDocument();
    const create = callsTo(calls, QT_CREATE)[0];
    expect(create?.body).toEqual({ version: 4, buyer_po_no: "PO-2026-001", buyer_po_date: "2026-09-29" });
    for (const key of ["sku_id", "unit_price", "unit_price_amount", "currency", "fx_rate", "buyer_partner_id", "dest_market_code"]) {
      expect(create?.body).not.toHaveProperty(key);
    }
    expect(issued).toContain(create?.headers["Idempotency-Key"]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("바이어 PO 없이도 만들 수 있다(서버가 미기재를 허용) — 본문은 version만", async () => {
    const { calls } = openQt(issuedQt(), [[QT_CREATE, "POST", () => jsonResponse(soDetail({ buyer_po_no: null }), 201)]]);
    const dialog = await openDialog();
    next(dialog);
    confirm(dialog);
    await waitFor(() => expect(callsTo(calls, QT_CREATE)).toHaveLength(1));
    expect(callsTo(calls, QT_CREATE)[0]?.body).toEqual({ version: 4 });
  });

  it("라인·수량 직접 지정: 수량 형식·원천 수량 초과를 화면에서 검증하고, 지정한 라인만 lines로 보낸다", async () => {
    const { calls } = openQt(issuedQt(), [[QT_CREATE, "POST", () => jsonResponse(soDetail(), 201)]]);
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("radio", { name: "일부 라인·수량 선택" }));
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "0" } });
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("라인 1(SKU-001)의 수량은 1 이상의 정수로 입력해 주세요.");
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "11" } });
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("원천 견적 수량(10)을 넘을 수 없습니다.");
    fireEvent.click(within(dialog).getByLabelText("라인 1 선택"));
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("가져올 라인을 하나 이상 선택해 주세요.");
    fireEvent.click(within(dialog).getByLabelText("라인 1 선택"));
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("라인 1 요청납기"), { target: { value: "2026-11-20" } });
    next(dialog);
    confirm(dialog);
    await waitFor(() => expect(callsTo(calls, QT_CREATE)).toHaveLength(1));
    expect(callsTo(calls, QT_CREATE)[0]?.body).toEqual({
      version: 4,
      lines: [{ source_line_id: 11, quantity: 4, requested_delivery_date: "2026-11-20" }],
    });
    expect(callsTo(calls, QT_CREATE)).toHaveLength(1);
  });

  it("화면 검증: PO 일자 미래·증빙일이 원천보다 앞섬·PO번호 60자 초과는 보내지 않는다", async () => {
    const { calls } = openQt(issuedQt());
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("바이어 PO 일자 (선택)"), { target: { value: "2999-01-01" } });
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("바이어 PO 일자는 오늘보다 미래일 수 없습니다.");
    fireEvent.change(within(dialog).getByLabelText("바이어 PO 일자 (선택)"), { target: { value: "" } });
    fireEvent.change(within(dialog).getByLabelText("증빙일 (비우면 오늘)"), { target: { value: "2026-09-01" } });
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("원천 견적의 증빙일(2026-09-30)보다 앞설 수 없습니다.");
    fireEvent.change(within(dialog).getByLabelText("증빙일 (비우면 오늘)"), { target: { value: "" } });
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "A".repeat(61) } });
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("60자 이하");
    expect(callsTo(calls, QT_CREATE)).toHaveLength(0);
  });

  it("중복 바이어 PO(409)는 서버 문구와 점유 수주(번호·상태)를 안내하고 다이얼로그에 머문다", async () => {
    openQt(issuedQt(), [[QT_CREATE, "POST", duplicatePo]]);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-DUP" } });
    next(dialog);
    confirm(dialog);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("같은 바이어의 같은 PO번호가 이미 다른 수주에 등록되어 있습니다.");
    expect(alert).toHaveTextContent("이미 등록된 수주: SO-2026-0007 (접수)");
    expect(screen.getByRole("dialog", { name: "수주 만들기" })).toBeInTheDocument();
    // 뒤로 가서 PO번호를 고칠 수 있다.
    fireEvent.click(within(dialog).getByRole("button", { name: "뒤로" }));
    expect(within(dialog).getByLabelText("바이어 PO번호 (선택)")).toHaveValue("PO-DUP");
  });

  it("잔량 초과(409)는 서버 문구와 라인별 남은 수량을 보인다", async () => {
    openQt(issuedQt(), [[QT_CREATE, "POST", exceeds]]);
    const dialog = await openDialog();
    next(dialog);
    confirm(dialog);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("가져올 수량이 남은 수량을 넘었습니다.");
    expect(alert).toHaveTextContent("라인 1 (SKU-001): 남은 수량 2");
  });

  it("원천이 그 사이 바뀌면(409) 다이얼로그 안에서 '최신 내용 불러오기'를 보이고, 누르면 닫히고 재조회한다", async () => {
    const { calls } = openQt(issuedQt(), [[QT_CREATE, "POST", conflict]]);
    const dialog = await openDialog();
    next(dialog);
    confirm(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다른 곳에서 이 견적이 먼저 수정되었습니다");
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length).toBeGreaterThan(before));
  });

  it("창 재조회로 서버 version이 앞서가면 SO 만들기 버튼이 잠기고, 그 전에 열린 다이얼로그는 화면 기준 version으로 보낸다", async () => {
    const { calls } = openQt(issuedQt(), [[QT_CREATE, "POST", conflict]]);
    const dialog = await openDialog();
    server.qt = issuedQt({ version: 5 });
    window.dispatchEvent(new Event("visibilitychange"));
    await screen.findByText(/다른 곳에서 이 견적이 수정되었습니다/);
    // 열려 있던 버튼은 잠긴다(새 다이얼로그를 옛 기준으로 열 수 없다).
    expect(screen.getByRole("button", { name: "SO 만들기", hidden: true })).toBeDisabled();
    next(dialog);
    confirm(dialog);
    await waitFor(() => expect(callsTo(calls, QT_CREATE)).toHaveLength(1));
    expect(callsTo(calls, QT_CREATE)[0]?.body).toMatchObject({ version: 4 });
  });

  it("더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = openQt(issuedQt());
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith(QT_CREATE)) {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    const dialog = await openDialog();
    next(dialog);
    const button = within(dialog).getByRole("button", { name: "생성 확정" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(callsTo(calls, QT_CREATE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(callsTo(calls, QT_CREATE)).toHaveLength(1);
    // 진행 중에는 뒤로·확정이 잠긴다(입력 잠금).
    expect(within(dialog).getByRole("button", { name: "뒤로" })).toBeDisabled();
    release(jsonResponse(soDetail(), 201));
  });

  it("결과를 모르는 실패 뒤 같은 본문 재확정은 같은 멱등 키, 본문이 달라지면 새 키(되돌려 재확정하면 다시 같은 키가 아니라 직전 본문 기준)", async () => {
    let fail = true;
    const { calls } = openQt(issuedQt(), [
      [QT_CREATE, "POST", () => (fail ? jsonResponse({ error: { code: "X", message: "네트워크 오류" } }, 500) : jsonResponse(soDetail(), 201))],
    ]);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-A" } });
    next(dialog);
    const send = async (n: number) => {
      confirm(dialog);
      await waitFor(() => expect(callsTo(calls, QT_CREATE)).toHaveLength(n));
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "생성 확정" })).toBeEnabled());
    };
    await send(1);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("네트워크 오류");
    await send(2); // 같은 본문 재시도
    // 뒤로 → 입력을 바꿨다가 원래대로 되돌림 → 같은 본문이므로 같은 키.
    fireEvent.click(within(dialog).getByRole("button", { name: "뒤로" }));
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-B" } });
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-A" } });
    next(dialog);
    await send(3);
    // 본문이 실제로 달라지면 새 키.
    fireEvent.click(within(dialog).getByRole("button", { name: "뒤로" }));
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-C" } });
    next(dialog);
    fail = false;
    confirm(dialog);
    await waitFor(() => expect(callsTo(calls, QT_CREATE)).toHaveLength(4));
    const keys = callsTo(calls, QT_CREATE).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
    expect(keys[3]).not.toBe(keys[0]);
  });

  it("Esc로 닫히고, 다시 열면 새 멱등 키를 쓴다", async () => {
    const { calls } = openQt(issuedQt(), [[QT_CREATE, "POST", () => jsonResponse({ error: { code: "X", message: "실패" } }, 500)]]);
    for (let i = 0; i < 2; i += 1) {
      const dialog = await openDialog();
      next(dialog);
      confirm(dialog);
      await within(dialog).findByRole("alert");
      fireEvent.keyDown(document, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }
    const keys = callsTo(calls, QT_CREATE).map((c) => c.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });
});

describe("PI → SO 만들기", () => {
  it("PI 라인을 원천으로 PI 경로에 보내고 성공하면 SO 상세로 이동한다", async () => {
    const { calls } = openPi(piDetail(), [[PI_CREATE, "POST", () => jsonResponse(soDetail(), 201)]]);
    const dialog = await openDialog();
    expect(within(dialog).getByRole("heading", { name: /수주 만들기 — PI-2026-0001/ })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("radio", { name: "일부 라인·수량 선택" }));
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "7" } });
    next(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("원천 PI 수량(6)을 넘을 수 없습니다.");
    fireEvent.change(within(dialog).getByLabelText("라인 1 수량"), { target: { value: "5" } });
    next(dialog);
    confirm(dialog);
    expect(await screen.findByRole("heading", { name: /SO-2026-0001/ })).toBeInTheDocument();
    // PI 라인 id(31)가 source_line_id — PI 값은 본문에 싣지 않는다.
    expect(callsTo(calls, PI_CREATE)[0]?.body).toEqual({ version: 2, lines: [{ source_line_id: 31, quantity: 5 }] });
  });

  it("PI당 활성 수주 1건(409)은 서버 문구와 점유 수주를 안내한다", async () => {
    openPi(piDetail(), [[PI_CREATE, "POST", alreadyConverted]]);
    const dialog = await openDialog();
    next(dialog);
    confirm(dialog);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("이 문서에서 이미 수주가 만들어졌습니다.");
    expect(alert).toHaveTextContent("이미 등록된 수주: SO-2026-0003 (확정)");
  });

  it("중복 바이어 PO(409)도 같은 안내를 쓴다", async () => {
    openPi(piDetail(), [[PI_CREATE, "POST", duplicatePo]]);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-DUP" } });
    next(dialog);
    confirm(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("이미 등록된 수주: SO-2026-0007 (접수)");
  });
});
