// 발주 만들기 — 2단 흐름(입력→미리보기→확정)·필수값 검증·더블클릭 1회·멱등 키 규율·서버 오류 안내·원가 입력 금지 안내 (S3-1 PR-8b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { PO_LOG, SENTINELS, SENTINEL_LINE, SENTINEL_TOTAL, SENTINEL_UNIT, poDetail, poPreview } from "../test/po-fixtures";
import { assertNoLeakOutsideText, captureConsole } from "../test/po-leak";
import { stubFetch, type Call } from "../test/qt-fixtures";
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

const REFS: Array<[string, string, () => Response]> = [
  ["/v1/partners", "GET", () => jsonResponse(page([{ id: 4, partner_code: "P-4", name_ko: "서울 공급사" }]))],
  [
    "/v1/skus",
    "GET",
    () =>
      jsonResponse(
        page([
          { id: 5, sku_code: "SKU-001", name_ko: "수분 세럼" },
          { id: 6, sku_code: "SKU-002", name_ko: "진정 크림" },
        ]),
      ),
  ],
  ["/v1/system/currencies", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }, { code: "KRW", minor_units: 0 }]))],
  ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
  ["/v1/purchase-orders/9/status-log", "GET", () => jsonResponse(PO_LOG)],
  ["/v1/purchase-orders/9", "GET", () => jsonResponse(poDetail())],
];

function open(extra: Array<[string, string, () => Response]> = [], me: unknown = TRADER) {
  const stub = stubFetch(me, [...extra, ...REFS]);
  renderWithProviders(<AppRoutes />, { route: "/purchase-orders/new" });
  return stub;
}

const previewCalls = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.url.endsWith("/v1/purchase-orders/preview"));
const createCalls = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.url.endsWith("/api/v1/purchase-orders"));

async function pickSupplier() {
  fireEvent.focus(screen.getByRole("combobox", { name: "공급사" }));
  fireEvent.click(await screen.findByRole("option", { name: "서울 공급사 (P-4)" }));
}
async function pickSku(index: number, name: string) {
  fireEvent.focus(screen.getByRole("combobox", { name: `라인 ${index} SKU` }));
  fireEvent.click(await screen.findByRole("option", { name }));
}

/** 필수값을 모두 채운다(통화 USD·후불 T/T·FOB·라인 1개). */
async function fillValid({ currency = "USD" }: { currency?: string } = {}) {
  await screen.findByRole("form", { name: "발주 입력" });
  await pickSupplier();
  await screen.findByRole("option", { name: currency });
  fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: currency } });
  if (currency !== "KRW") fireEvent.change(screen.getByLabelText("환율 *"), { target: { value: "1350.5" } });
  fireEvent.change(screen.getByLabelText("결제조건 *"), { target: { value: "TT_DEFERRED" } });
  fireEvent.change(screen.getByLabelText("잔금 기준"), { target: { value: "RECEIPT_DATE" } });
  fireEvent.change(screen.getByLabelText("잔금 일수"), { target: { value: "30" } });
  fireEvent.change(screen.getByLabelText("인코텀즈 *"), { target: { value: "FOB" } });
  fireEvent.change(screen.getByLabelText("인도 장소 *"), { target: { value: " Busan " } });
  await pickSku(1, "SKU-001 수분 세럼");
  fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "10" } });
}

const clickPreview = () => fireEvent.click(screen.getByRole("button", { name: "미리보기" }));

describe("발주 만들기 — 권한·안내", () => {
  it("조회 전용 역할은 입력 폼 없이 안내만 보인다(서버도 403)", async () => {
    open([], VIEWER);
    expect(await screen.findByRole("alert")).toHaveTextContent("발주는 무역·관리자만 만들 수 있습니다.");
    expect(screen.queryByRole("form", { name: "발주 입력" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "발주 목록으로" })).toBeInTheDocument();
  });

  it("공급사명·내부 메모 입력란 옆에 원가 입력 금지 안내가 있고 입력란에 연결돼 있다", async () => {
    open();
    const form = await screen.findByRole("form", { name: "발주 입력" });
    const warning = "원가(단가·금액)를 적지 마세요 — 원가 열람 권한이 없는 사용자에게도 보입니다.";
    expect(within(form).getAllByText(warning)).toHaveLength(2);
    expect(within(form).getByLabelText(/내부 메모/)).toHaveAccessibleDescription(warning);
    expect(within(form).getByLabelText(/공급사명 표기/)).toHaveAccessibleDescription(warning);
  });

  it("공급사 유형 규칙은 서버가 판정한다는 안내가 있다(화면은 유형 필터를 걸지 않는다)", async () => {
    const { calls } = open();
    await screen.findByRole("form", { name: "발주 입력" });
    expect(screen.getByText(/구매 발주는 공급사 또는 OEM 유형/)).toBeInTheDocument();
    fireEvent.focus(screen.getByRole("combobox", { name: "공급사" }));
    await screen.findByRole("option", { name: "서울 공급사 (P-4)" });
    const search = calls.find((c) => c.url.startsWith("/api/v1/partners?"));
    expect(search?.url).not.toContain("type=");
  });
});

describe("발주 만들기 — 필수값 화면 검증(서버 호출 전)", () => {
  it("공급사·통화·환율·결제조건·인코텀즈·라인을 차례로 요구한다", async () => {
    const { calls } = open();
    await screen.findByRole("form", { name: "발주 입력" });
    clickPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent("공급사를 선택해 주세요.");
    await pickSupplier();
    clickPreview();
    expect(await screen.findByText("통화를 선택해 주세요.")).toBeInTheDocument();
    await screen.findByRole("option", { name: "USD" });
    fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: "USD" } });
    clickPreview();
    expect(await screen.findByText(/환율을 입력해 주세요/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("환율 *"), { target: { value: "1350" } });
    clickPreview();
    expect(await screen.findByText("결제조건을 선택해 주세요.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("결제조건 *"), { target: { value: "TT_ADVANCE" } });
    clickPreview();
    expect(await screen.findByText("선수금 T/T는 선수금 비율을 입력해 주세요.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("선수금 비율(%) *"), { target: { value: "30" } });
    clickPreview();
    expect(await screen.findByText("Incoterms 코드와 인도 장소를 입력해 주세요.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("인코텀즈 *"), { target: { value: "FOB" } });
    fireEvent.change(screen.getByLabelText("인도 장소 *"), { target: { value: "Busan" } });
    clickPreview();
    expect(await screen.findByText("라인 1의 SKU를 선택해 주세요.")).toBeInTheDocument();
    await pickSku(1, "SKU-001 수분 세럼");
    clickPreview();
    expect(await screen.findByText("라인 1의 수량은 1 이상의 정수로 입력해 주세요.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "0" } });
    clickPreview();
    expect(await screen.findByText("라인 1의 수량은 1 이상의 정수로 입력해 주세요.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("라인 1 단가"), { target: { value: "abc" } });
    clickPreview();
    expect(await screen.findByText(/단가는 숫자/)).toBeInTheDocument();
    expect(previewCalls(calls)).toHaveLength(0);
  });

  it("같은 SKU를 두 라인에 넣으면 막는다 / 후불 T/T는 잔금 기준·일수가 필요하다", async () => {
    const { calls } = open();
    await fillValid();
    fireEvent.click(screen.getByRole("button", { name: "라인 추가" }));
    await pickSku(2, "SKU-001 수분 세럼");
    fireEvent.change(screen.getByLabelText("라인 2 수량"), { target: { value: "3" } });
    clickPreview();
    expect(await screen.findByText(/같은 SKU\(SKU-001\)가 이미 다른 라인에 있습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "라인 2 삭제" }));
    fireEvent.change(screen.getByLabelText("잔금 일수"), { target: { value: "" } });
    clickPreview();
    expect(await screen.findByText("후불 T/T는 잔금 기준과 잔금 일수를 입력해 주세요.")).toBeInTheDocument();
    expect(previewCalls(calls)).toHaveLength(0);
    // 마지막 한 줄은 삭제할 수 없다.
    expect(screen.getByRole("button", { name: "라인 1 삭제" })).toBeDisabled();
  });
});

describe("발주 만들기 — 2단 흐름", () => {
  it("입력 → 미리보기(서버 계산값·저장 안 됨 안내) → 확정 다이얼로그(되돌릴 수 없음) → 생성 → 상세로 이동", async () => {
    const { calls } = open([
      ["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview())],
      ["/v1/purchase-orders", "POST", () => jsonResponse(poDetail(), 201)],
    ]);
    await fillValid();
    clickPreview();

    // 미리보기: 서버 값 그대로.
    expect(await screen.findByRole("heading", { name: "발주 미리보기" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("아직 저장되지 않았습니다");
    expect(screen.getByText("12.50")).toBeInTheDocument();
    expect(screen.getByText("125.00", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByText("125.00 USD")).toBeInTheDocument();
    expect(screen.getByText("후불 T/T · 잔금 입고 확정일 기준 30일")).toBeInTheDocument();
    expect(screen.getByText("마스터")).toBeInTheDocument();
    // 미리보기 본문: 생성 본문과 같은 꼴, version·status·합계·금액 필드 없음, 단가 생략=마스터.
    expect(previewCalls(calls)).toHaveLength(1);
    const body = previewCalls(calls)[0]?.body;
    expect(body).toEqual({
      supplier_partner_id: 4,
      po_kind: "PURCHASE",
      currency: "USD",
      fx_rate: "1350.5",
      payment_terms: { payment_type: "TT_DEFERRED", balance_anchor: "RECEIPT_DATE", balance_days: 30 },
      incoterm: { code: "FOB", place: "Busan", year: 2020 },
      lines: [{ sku_id: 5, quantity: 10 }],
    });
    // 미리보기 단계에서는 생성이 일어나지 않았다.
    expect(createCalls(calls)).toHaveLength(0);

    // 확정 → 위험 안내 다이얼로그.
    fireEvent.click(screen.getByRole("button", { name: "발주 확정…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/되돌릴 수 없는 공급사 발송 대상/)).toBeInTheDocument();
    expect(within(dialog).getByText("125.00 USD")).toBeInTheDocument();
    expect(createCalls(calls)).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    await waitFor(() => expect(createCalls(calls)).toHaveLength(1));
    const post = createCalls(calls)[0];
    // 확정 본문 = 미리보기 본문 + 미리보기가 준 증빙일 고정. 멱등 키 1개.
    expect(post?.body).toEqual({ ...body, doc_date: "2026-09-30" });
    expect(post?.body).not.toHaveProperty("version");
    expect(post?.headers["Idempotency-Key"]).toBe("key-2");
    // 상세로 이동.
    expect(await screen.findByRole("heading", { name: /PO-2026-0001/ })).toBeInTheDocument();
  });

  it("단가·납기·메모·담당자·공급사명을 넣으면 본문에 실리고, KRW는 환율 입력란이 없고 본문에도 없다", async () => {
    const { calls } = open([["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview({ currency: "KRW", fx_rate: "1" }))]]);
    await fillValid({ currency: "KRW" });
    expect(screen.queryByLabelText("환율 *")).not.toBeInTheDocument();
    expect(screen.getByText(/원화\(KRW\) 발주는 환율 1로 서버가 채웁니다/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("라인 1 단가"), { target: { value: " 12.34 " } });
    fireEvent.change(screen.getByLabelText("라인 1 요청납기"), { target: { value: "2026-10-30" } });
    fireEvent.change(screen.getByLabelText(/공급사명 표기/), { target: { value: "Seoul Co" } });
    fireEvent.change(screen.getByLabelText(/내부 메모/), { target: { value: "급건" } });
    fireEvent.change(screen.getByLabelText(/담당자/), { target: { value: "1" } });
    await screen.findByRole("option", { name: "무역 담당" });
    fireEvent.change(screen.getByLabelText(/담당자/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("증빙일 (비우면 오늘)"), { target: { value: "2026-09-30" } });
    clickPreview();
    await screen.findByRole("heading", { name: "발주 미리보기" });
    const body = previewCalls(calls)[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({
      currency: "KRW",
      doc_date: "2026-09-30",
      supplier_name: "Seoul Co",
      internal_note: "급건",
      assignee_id: 1,
      lines: [{ sku_id: 5, quantity: 10, unit_cost: "12.34", requested_delivery_date: "2026-10-30" }],
    });
    expect(body).not.toHaveProperty("fx_rate");
  });

  it("미리보기 계산 중에는 입력이 잠긴다(진행 중 입력 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    open();
    await fillValid();
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => (u.endsWith("/preview") ? pending : base(u, i))),
    );
    clickPreview();
    const button = await screen.findByRole("button", { name: "계산 중…" });
    expect(button).toBeDisabled();
    expect(screen.getByLabelText("라인 1 수량")).toBeDisabled();
    expect(screen.getByLabelText("인도 장소 *")).toBeDisabled();
    expect(screen.getByRole("button", { name: "공급사 선택 해제" })).toBeDisabled();
    release(jsonResponse(poPreview()));
    await screen.findByRole("heading", { name: "발주 미리보기" });
  });

  it("미리보기 서버 오류(공급사 유형 등)는 서버 한국어 문구를 입력 화면에 보이고 확정 단계로 가지 않는다", async () => {
    open([
      ["/v1/purchase-orders/preview", "POST", () => jsonResponse({ error: { code: "VALIDATION.INVALID_FIELD", message: "공급사 또는 OEM 유형의 거래처만 선택할 수 있습니다." } }, 422)],
    ]);
    await fillValid();
    clickPreview();
    expect(await screen.findByRole("alert")).toHaveTextContent("공급사 또는 OEM 유형의 거래처만 선택할 수 있습니다.");
    expect(screen.queryByRole("heading", { name: "발주 미리보기" })).not.toBeInTheDocument();
    expect(screen.getByRole("form", { name: "발주 입력" })).toBeInTheDocument();
  });

  it("'입력 수정'으로 돌아가면 입력값이 유지된다", async () => {
    open([["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview())]]);
    await fillValid();
    clickPreview();
    await screen.findByRole("heading", { name: "발주 미리보기" });
    fireEvent.click(screen.getByRole("button", { name: "입력 수정" }));
    expect(await screen.findByLabelText("라인 1 수량")).toHaveValue("10");
    expect(screen.getByLabelText("인도 장소 *")).toHaveValue(" Busan ");
  });
});

describe("발주 만들기 — 확정 다이얼로그 규율", () => {
  async function toDialog(extra: Array<[string, string, () => Response]>) {
    const stub = open([["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview())], ...extra]);
    await fillValid();
    clickPreview();
    await screen.findByRole("heading", { name: "발주 미리보기" });
    fireEvent.click(screen.getByRole("button", { name: "발주 확정…" }));
    return { ...stub, dialog: await screen.findByRole("dialog") };
  }

  it("더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls, dialog } = await toDialog([]);
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith("/api/v1/purchase-orders") && i?.method === "POST") {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    const confirm = within(dialog).getByRole("button", { name: "발주 확정" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(createCalls(calls)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(createCalls(calls)).toHaveLength(1);
    release(jsonResponse(poDetail(), 201));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
  });

  it("실패 뒤 같은 본문 재시도는 같은 멱등 키 — 오류는 다이얼로그 안에 서버 문구로 보인다", async () => {
    let fail = true;
    const { calls, dialog } = await toDialog([
      [
        "/v1/purchase-orders",
        "POST",
        () =>
          fail
            ? jsonResponse({ error: { code: "TRADE_DOCS.LINE.SKU_DUPLICATE", message: "같은 SKU가 이미 있습니다." } }, 409)
            : jsonResponse(poDetail(), 201),
      ],
    ]);
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("같은 SKU가 이미 있습니다.");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "발주 확정" })).toBeEnabled());
    fail = false;
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    await waitFor(() => expect(createCalls(calls)).toHaveLength(2));
    expect(createCalls(calls).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-2", "key-2"]);
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
  });

  it("닫았다 같은 본문으로 다시 확정해도 같은 키, 입력을 고쳐 본문이 달라지면 새 키", async () => {
    const { calls, dialog } = await toDialog([
      ["/v1/purchase-orders", "POST", () => jsonResponse({ error: { code: "X", message: "잠시 실패" } }, 500)],
    ]);
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 같은 본문 재확정 → 같은 키(결과를 모르는 실패 뒤 중복 발주 방지).
    fireEvent.click(screen.getByRole("button", { name: "발주 확정…" }));
    let again = await screen.findByRole("dialog");
    fireEvent.click(within(again).getByRole("button", { name: "발주 확정" }));
    await waitFor(() => expect(createCalls(calls)).toHaveLength(2));
    await within(again).findByRole("alert");
    fireEvent.click(within(again).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 입력 수정 → 수량이 달라진 새 미리보기 → 새 본문 → 새 키.
    fireEvent.click(screen.getByRole("button", { name: "입력 수정" }));
    fireEvent.change(await screen.findByLabelText("라인 1 수량"), { target: { value: "11" } });
    clickPreview();
    await screen.findByRole("heading", { name: "발주 미리보기" });
    fireEvent.click(screen.getByRole("button", { name: "발주 확정…" }));
    again = await screen.findByRole("dialog");
    fireEvent.click(within(again).getByRole("button", { name: "발주 확정" }));
    await waitFor(() => expect(createCalls(calls)).toHaveLength(3));
    const keys = createCalls(calls).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
    expect((createCalls(calls)[2]?.body as { lines: Array<{ quantity: number }> }).lines[0]?.quantity).toBe(11);
  });

  it("Esc로 다이얼로그가 닫히고 확정 전에는 생성 호출이 없다", async () => {
    const { calls } = await toDialog([]);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(createCalls(calls)).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "발주 미리보기" })).toBeInTheDocument();
  });
});


describe("발주 만들기 — 적대 검토 반영", () => {
  async function previewAndOpenDialog() {
    clickPreview();
    await screen.findByRole("heading", { name: "발주 미리보기" });
    fireEvent.click(screen.getByRole("button", { name: "발주 확정…" }));
    return screen.findByRole("dialog");
  }

  it("확정 다이얼로그에 '확정 시점에 단가를 다시 읽어 합계가 달라질 수 있다' 고지가 있다", async () => {
    open([["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview())]]);
    await fillValid();
    const dialog = await previewAndOpenDialog();
    expect(
      within(dialog).getByText("마스터 매입가 단가는 확정 시점에 서버가 다시 읽으므로 미리보기와 합계가 달라질 수 있습니다."),
    ).toBeInTheDocument();
  });

  it("확정 합계가 미리보기 합계와 문자열로 다르면 상세 진입 시 안내한다 / 같으면 안내하지 않는다", async () => {
    open([
      ["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview())], // 125.00
      ["/v1/purchase-orders", "POST", () => jsonResponse(poDetail(), 201)], // 센티널 합계
    ]);
    await fillValid();
    const dialog = await previewAndOpenDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    expect(await screen.findByText(/미리보기와 합계가 달라졌습니다\(미리보기 125\.00 → 확정 76543219\.87\)/)).toBeInTheDocument();
  });

  it("합계가 같으면 안내가 없다", async () => {
    open([
      ["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview({ total_text: SENTINEL_TOTAL }))],
      ["/v1/purchase-orders", "POST", () => jsonResponse(poDetail(), 201)],
    ]);
    await fillValid();
    const dialog = await previewAndOpenDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.queryByText(/미리보기와 합계가 달라졌습니다/)).not.toBeInTheDocument();
  });

  it("멱등 키는 본문별 — A(실패) → B로 수정 확정 → 다시 A로 돌아가 확정하면 A의 원래 키를 쓴다", async () => {
    const { calls } = open([
      ["/v1/purchase-orders/preview", "POST", () => jsonResponse(poPreview())],
      ["/v1/purchase-orders", "POST", () => jsonResponse({ error: { code: "X", message: "잠시 실패" } }, 500)],
    ]);
    await fillValid();
    const confirmWith = async (qty: string | null) => {
      if (qty !== null) {
        fireEvent.click(screen.getByRole("button", { name: "입력 수정" }));
        fireEvent.change(await screen.findByLabelText("라인 1 수량"), { target: { value: qty } });
      }
      const dialog = await previewAndOpenDialog();
      const before = createCalls(calls).length;
      fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
      await waitFor(() => expect(createCalls(calls)).toHaveLength(before + 1));
      await within(dialog).findByRole("alert");
      fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    };
    await confirmWith(null); // A(수량 10)
    await confirmWith("11"); // B
    await confirmWith("10"); // 다시 A
    const keys = createCalls(calls).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).not.toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
  });

  it("통화를 KRW로 바꾸면 환율·기준일 입력이 비워지고, 비KRW로 복귀해도 이전 값이 되살아나지 않는다", async () => {
    open();
    await screen.findByRole("form", { name: "발주 입력" });
    await screen.findByRole("option", { name: "USD" });
    fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: "USD" } });
    fireEvent.change(screen.getByLabelText("환율 *"), { target: { value: "1350.5" } });
    fireEvent.change(screen.getByLabelText("환율 기준일 (비우면 증빙일)"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: "KRW" } });
    fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: "USD" } });
    expect(screen.getByLabelText("환율 *")).toHaveValue("");
    expect(screen.getByLabelText("환율 기준일 (비우면 증빙일)")).toHaveValue("");
  });

  it("원가가 있는 미리보기·확정 경로에서도 화면 글자 외 채널(콘솔·스토리지·URL·속성·history)로 원가가 새지 않는다", async () => {
    const logs = captureConsole();
    const { calls } = open([
      ["/v1/purchase-orders/preview", "POST", () =>
        jsonResponse(
          poPreview({
            total_text: SENTINEL_TOTAL,
            lines: [{ ...poPreview().lines[0]!, unit_cost_text: SENTINEL_UNIT, line_cost_text: SENTINEL_LINE }],
          }),
        )],
      ["/v1/purchase-orders", "POST", () => jsonResponse(poDetail(), 201)],
    ]);
    await fillValid();
    clickPreview();
    await screen.findByRole("heading", { name: "발주 미리보기" });
    expect(document.body.textContent).toContain(SENTINEL_TOTAL); // 쓰기 역할에겐 보인다
    assertNoLeakOutsideText(SENTINELS, logs, calls);
    fireEvent.click(screen.getByRole("button", { name: "발주 확정…" }));
    const dialog = await screen.findByRole("dialog");
    assertNoLeakOutsideText(SENTINELS, logs, calls);
    fireEvent.click(within(dialog).getByRole("button", { name: "발주 확정" }));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    assertNoLeakOutsideText(SENTINELS, logs, calls);
  });
});
