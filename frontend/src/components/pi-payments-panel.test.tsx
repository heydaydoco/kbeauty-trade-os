// PiPaymentsPanel — 요약·내역·입금 폼·역기록·확정 SO 경고·권한·멱등·오류 한국어화·페이지네이션 (S3-1 PR-10b).

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { todayKst } from "../lib/datetime";
import { PAYMENT_ERROR_TEXT } from "../lib/payment";
import { paymentPage, paymentRow, summary, writeResult } from "../test/payment-fixtures";
import { stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, renderWithProviders } from "../test/render";
import { PiPaymentsPanel, type PaymentPanelPi } from "./pi-payments-panel";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PI: PaymentPanelPi = { id: 5, currency: "USD", minor_units: 2, status: "ISSUED", payment_type: "TT_ADVANCE" };
const err = (code: string, status: number, message = "서버 메시지") =>
  jsonResponse({ error: { code, message, detail: { pi_currency: "USD", payment_type: "TT_DEFERRED" } } }, status);

type Handler = [string, string, () => Response];

function open(handlers: Handler[], opts: { me?: unknown; pi?: PaymentPanelPi; onWritten?: () => void } = {}) {
  const stub = stubFetch(opts.me ?? TRADER, handlers);
  const view = renderWithProviders(<PiPaymentsPanel pi={opts.pi ?? PI} onWritten={opts.onWritten} />);
  return { ...stub, ...view };
}

const LIST = "/v1/proforma-invoices/5/payments";
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
const gets = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.url.includes("/payments"));

function fillForm(over: { amount?: string; on?: string; reference?: string } = {}) {
  fireEvent.change(screen.getByLabelText(/입금액/), { target: { value: over.amount ?? "10.00" } });
  if (over.on !== undefined) fireEvent.change(screen.getByLabelText(/입금일/), { target: { value: over.on } });
  fireEvent.change(screen.getByLabelText(/입금 확인 근거/), { target: { value: over.reference ?? "SHB-REF-9" } });
}

async function openConfirm(over: Parameters<typeof fillForm>[0] = {}) {
  await screen.findByRole("form", { name: "입금 기록" });
  fillForm(over);
  fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
  return await screen.findByRole("dialog");
}

describe("PiPaymentsPanel — 요약과 내역", () => {
  it("순입금·선수금·잔여·PI 상태를 서버 문자열 그대로 보이고, 입금/역기록 구분·역기록된 입금·근거·처리일을 보인다", async () => {
    const rows = [
      paymentRow({ id: 12, kind: "REVERSAL", received_amount: -1000, received_amount_text: "-10.00", reverses_payment_id: 11, reason: "금액 오입력", reference: "SHB-REF-001" }),
      paymentRow({ id: 11, reversed_by_payment_id: 12 }),
      paymentRow({ id: 10, received_amount: 500, received_amount_text: "5.00", reference: "SHB-REF-000", received_on: "2026-09-29" }),
    ];
    open([[LIST, "GET", () => jsonResponse(paymentPage(rows, summary({ net_received_text: "5.00", remaining_text: "17.50", pi_status: "PARTIALLY_PAID" })))]]);
    const sum = within(await screen.findByLabelText("입금 요약"));
    expect(sum.getByText("순입금").nextElementSibling).toHaveTextContent("5.00 USD");
    expect(sum.getByText("선수금").nextElementSibling).toHaveTextContent("22.50 USD");
    expect(sum.getByText("잔여").nextElementSibling).toHaveTextContent("17.50 USD");
    expect(sum.getByText("PI 상태").nextElementSibling).toHaveTextContent("일부입금");
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("입금", { selector: "span" }).length).toBe(2);
    expect(within(table).getByText("역기록", { selector: "span" })).toBeInTheDocument();
    expect(within(table).getByText("-10.00 USD")).toBeInTheDocument();
    expect(within(table).getByText("역기록됨 (#12)")).toBeInTheDocument();
    expect(within(table).getByText("원 입금 #11")).toBeInTheDocument();
    expect(within(table).getByText("사유: 금액 오입력")).toBeInTheDocument();
    expect(within(table).getByText("SHB-REF-000")).toBeInTheDocument();
    expect(within(table).getByText("2026-09-29")).toBeInTheDocument();
    // 역기록 버튼은 미역기록 RECEIPT(#10)에만
    expect(within(table).getAllByRole("button", { name: /역기록$/ })).toHaveLength(1);
    expect(within(table).getByRole("button", { name: "입금 #10 역기록" })).toBeInTheDocument();
  });

  it("비선수금 PI: 안내 문구를 보이고 입금 폼은 숨긴다", async () => {
    open(
      [[LIST, "GET", () => jsonResponse(paymentPage([], summary({ payment_type: "TT_DEFERRED", due_text: "0.00", remaining_text: "0.00", due_amount: 0, remaining_amount: 0 })))]],
      { pi: { ...PI, payment_type: "TT_DEFERRED" } },
    );
    expect(await screen.findByText(/선수금 입금은 선수금 T\/T 전표만 기록합니다\(잔금 입금은 후속 기능\)/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "입금 기록" })).not.toBeInTheDocument();
  });

  it("목록 페이지네이션: 전체 건수·쪽 이동이 page 파라미터로 요청된다", async () => {
    const { calls } = open([[LIST, "GET", () => jsonResponse(paymentPage([paymentRow()], summary(), { total: 120 }))]]);
    expect(await screen.findByText("전체 120건")).toBeInTheDocument();
    expect(screen.getByText("1/3쪽")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(gets(calls).some((c) => c.url.includes("page=2"))).toBe(true));
  });
});

describe("PiPaymentsPanel — 권한", () => {
  it("읽기 전용 역할(VIEWER): 폼·역기록 버튼이 없고 내역은 보인다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([paymentRow()]))]], { me: VIEWER });
    expect(await screen.findByText("SHB-REF-001")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "입금 기록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /역기록$/ })).not.toBeInTheDocument();
    expect(screen.getByText(/무역·관리자만 할 수 있습니다/)).toBeInTheDocument();
  });

  it("서버 403이 오면 폼과 역기록 버튼을 숨긴다(역할은 서버가 최종)", async () => {
    open([
      [LIST, "POST", () => err("COMMON.AUTH.FORBIDDEN", 403, "Forbidden")],
      [LIST, "GET", () => jsonResponse(paymentPage([paymentRow()]))],
    ]);
    const dialog = await openConfirm();
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "입금 기록" })).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /역기록$/ })).not.toBeInTheDocument();
  });
});

describe("PiPaymentsPanel — 입금 폼", () => {
  const base: Handler = [LIST, "GET", () => jsonResponse(paymentPage([]))];

  it("통화는 PI 통화로 고정 표시되고, 선수금이 모두 입금되었으면 버튼을 막고 사유를 안내한다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([], summary({ remaining_amount: 0, remaining_text: "0.00", net_received_text: "22.50", pi_status: "PAID" })))]]);
    const form = await screen.findByRole("form", { name: "입금 기록" });
    expect(within(form).getByLabelText("통화(PI 통화로 고정)")).toHaveTextContent("USD");
    const button = await within(form).findByRole("button", { name: "입금 기록" });
    await waitFor(() => expect(button).toBeDisabled());
    expect(within(form).getByText(/선수금이 모두 입금되었습니다/)).toBeInTheDocument();
  });

  it("근거 필수·미래일 거부·금액 형식 — 잘못된 입력은 전송하지 않고 한국어로 안내한다", async () => {
    const { calls } = open([base]);
    await screen.findByRole("form", { name: "입금 기록" });
    fillForm({ amount: "1.234", on: "2999-01-01", reference: "   " });
    fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("소수점 2자리 이내");
    expect(alert).toHaveTextContent("오늘(KST) 이후");
    expect(alert).toHaveTextContent("입금 확인 근거");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(calls)).toHaveLength(0);
    // 0·문자·빈 금액
    for (const bad of ["0", "0.00", "abc", "", "-5", "1e3"]) {
      fillForm({ amount: bad, on: todayKst() });
      fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(/금액은/);
    }
    expect(posts(calls)).toHaveLength(0);
  });

  it("확인 다이얼로그에 금액·통화·잔여·되돌릴 수 없음·정정 안내를 보이고, 확정하면 PI 통화를 명시해 전송한다", async () => {
    const written = vi.fn();
    const { calls } = open(
      [
        [LIST, "POST", () => jsonResponse(writeResult(), 201)],
        [LIST, "GET", () => jsonResponse(paymentPage([], summary()))],
      ],
      { onWritten: written },
    );
    const dialog = await openConfirm({ amount: "10.00", on: "2026-09-30", reference: " SHB-REF-9 " });
    expect(dialog).toHaveAccessibleName(/입금을 기록할까요\?/);
    expect(dialog).toHaveTextContent("10.00 USD");
    expect(dialog).toHaveTextContent("남은 선수금: 22.50 USD");
    expect(dialog).toHaveTextContent("되돌릴 수 없습니다");
    expect(dialog).toHaveTextContent("역기록한 뒤 다시 입금");
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const [post] = posts(calls);
    expect(post?.url).toBe("/api/v1/proforma-invoices/5/payments");
    expect(post?.body).toEqual({
      received_amount: "10.00",
      received_currency: "USD",
      received_on: "2026-09-30",
      reference: "SHB-REF-9",
    });
    expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    // 쓰기 성공 → 입금 목록 재조회 + 상위(PI 상세) 갱신 알림 + 폼 비움
    await waitFor(() => expect(written).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(1));
    expect(screen.getByLabelText(/입금액/)).toHaveValue("");
  });

  it("더블클릭은 1회만 전송한다", async () => {
    const { calls } = open([
      [LIST, "POST", () => new Promise<Response>(() => undefined) as unknown as Response], // 응답이 오지 않는 요청
      [LIST, "GET", () => jsonResponse(paymentPage([]))],
    ]);
    const dialog = await openConfirm();
    const confirm = within(dialog).getByRole("button", { name: "입금 기록 확정" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(posts(calls)).toHaveLength(1);
  });

  it("같은 본문 재시도는 같은 멱등 키, 본문이 바뀌면 새 키, 성공 뒤 같은 본문 새 입금은 새 키를 쓴다", async () => {
    let mode: "fail" | "ok" = "fail";
    const { calls } = open([
      [LIST, "POST", () => (mode === "fail" ? err("COMMON.INTERNAL", 500, "서버 오류가 발생했습니다.") : jsonResponse(writeResult(), 201))],
      [LIST, "GET", () => jsonResponse(paymentPage([]))],
    ]);
    // 1) 실패 후 같은 본문 재시도 → 같은 키
    let dialog = await openConfirm({ amount: "10.00", on: "2026-09-30", reference: "A" });
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await within(dialog).findByRole("alert");
    mode = "ok";
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts(calls).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-1", "key-1"]);
    // 2) 성공 뒤 같은 본문으로 새 입금 → 새 키(이전 응답 재생 방지)
    dialog = await openConfirm({ amount: "10.00", on: "2026-09-30", reference: "A" });
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2]?.headers["Idempotency-Key"]).toBe("key-2");
  });

  it("본문이 바뀌면 새 키를 쓴다", async () => {
    const { calls } = open([
      [LIST, "POST", () => err("COMMON.INTERNAL", 500, "서버 오류가 발생했습니다.")],
      [LIST, "GET", () => jsonResponse(paymentPage([]))],
    ]);
    let dialog = await openConfirm({ amount: "10.00", on: "2026-09-30", reference: "A" });
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    dialog = await openConfirm({ amount: "11.00", on: "2026-09-30", reference: "A" });
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-1", "key-2"]);
  });
});

describe("PiPaymentsPanel — 알려진 함정", () => {
  it("요청 중 Esc로 닫으려 해도 닫히지 않고, 요청이 끝나면 잠금이 풀려 다시 보낼 수 있다(submitLock 고착 방지)", async () => {
    const pending: Array<(value: Response) => void> = [];
    const { calls } = open([
      [LIST, "POST", () => new Promise<Response>((resolve) => pending.push(resolve)) as unknown as Response],
      [LIST, "GET", () => jsonResponse(paymentPage([]))],
    ]);
    const dialog = await openConfirm();
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // 요청 중 닫힘 무시
    await act(async () => {
      pending[0]?.(err("PAYMENTS.PAYMENT.EXCEEDS_DUE", 422));
    });
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("남은 선수금을 넘습니다");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "입금 기록 확정" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2)); // 잠금 고착이면 여기서 막힌다
  });

  it("창 포커스 재조회가 와도 열린 확인 다이얼로그·입력은 유지된다", async () => {
    const { calls } = open([[LIST, "GET", () => jsonResponse(paymentPage([]))]]);
    await openConfirm({ amount: "10.00", on: "2026-09-30", reference: "KEEP" });
    const before = gets(calls).length;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(gets(calls).length).toBeGreaterThanOrEqual(before));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText(/입금 확인 근거/)).toHaveValue("KEEP");
  });
});

describe("PiPaymentsPanel — 역기록", () => {
  const REV = "/v1/payments/11/reversal";
  const withRow: Handler = [LIST, "GET", () => jsonResponse(paymentPage([paymentRow()], summary({ net_received_text: "10.00", remaining_text: "12.50" })))];

  it("사유(필수·2자 이상) 없이는 확정할 수 없고, 사유를 넣으면 사유만 담아 전송한다", async () => {
    const { calls } = open([[REV, "POST", () => jsonResponse(writeResult({ payment: paymentRow({ id: 12, kind: "REVERSAL", received_amount: -1000, received_amount_text: "-10.00", reverses_payment_id: 11, reason: "오입력" }) }), 201)], withRow]);
    fireEvent.click(await screen.findByRole("button", { name: "입금 #11 역기록" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName(/입금을 역기록할까요\?/);
    expect(dialog).toHaveTextContent("되돌릴 수 없습니다");
    expect(dialog).toHaveTextContent("역기록 후 새로 입금");
    const confirm = within(dialog).getByRole("button", { name: "역기록 확정" });
    expect(confirm).toBeDisabled();
    const reason = within(dialog).getByRole("textbox");
    fireEvent.change(reason, { target: { value: "오" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: "  오입력  " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts(calls)).toHaveLength(1);
    expect(posts(calls)[0]?.body).toEqual({ reason: "오입력" });
    expect(posts(calls)[0]?.url).toBe("/api/v1/payments/11/reversal");
    expect(screen.queryByText(/자동 취소되지 않았습니다/)).not.toBeInTheDocument();
  });

  it("응답 warnings에 CONFIRMED_SO_ADVANCE_UNMET가 있으면 확정 SO 경고 배너(SO 링크·자동 취소 안 됨 문구)를 띄운다", async () => {
    open([
      [REV, "POST", () => jsonResponse(writeResult({ warnings: [{ code: "CONFIRMED_SO_ADVANCE_UNMET", sales_order_ids: [31, 32] }] }), 201)],
      withRow,
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "입금 #11 역기록" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "이중 입력" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "역기록 확정" }));
    const banner = await screen.findByText(/자동 취소되지 않았습니다/);
    const box = banner.closest("[role=alert]") as HTMLElement;
    expect(box).toHaveTextContent("사람이 후속 조치(취소·재확인)를 하세요");
    expect(within(box).getByRole("link", { name: "수주 #31" })).toHaveAttribute("href", "/sales-orders/31");
    expect(within(box).getByRole("link", { name: "수주 #32" })).toHaveAttribute("href", "/sales-orders/32");
    fireEvent.click(within(box).getByRole("button", { name: "확인했습니다" }));
    expect(screen.queryByText(/자동 취소되지 않았습니다/)).not.toBeInTheDocument();
  });

  it("409 이미 역기록: 한국어 안내와 '최신 내용 불러오기'로 재조회한다", async () => {
    const { calls } = open([[REV, "POST", () => err("PAYMENTS.PAYMENT.ALREADY_REVERSED", 409, "Already reversed")], withRow]);
    fireEvent.click(await screen.findByRole("button", { name: "입금 #11 역기록" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "사유 있음" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "역기록 확정" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("이미 역기록된 입금입니다");
    expect(alert).not.toHaveTextContent("ALREADY_REVERSED");
    const before = gets(calls).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
  });

  it("닫힌(취소·만료) PI에서는 역기록 버튼이 비활성이고 사유를 안내한다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([paymentRow()], summary({ pi_status: "CANCELLED" })))]], { pi: { ...PI, status: "CANCELLED" } });
    const button = await screen.findByRole("button", { name: "입금 #11 역기록" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("역기록할 수 없습니다"));
  });
});

describe("PiPaymentsPanel — 오류 한국어화", () => {
  const cases: Array<[string, number, RegExp]> = [
    ["PAYMENTS.PAYMENT.CURRENCY_MISMATCH", 422, /통화가 PI 통화와 다릅니다/],
    ["PAYMENTS.PAYMENT.EXCEEDS_DUE", 422, /남은 선수금을 넘습니다/],
    ["PAYMENTS.PAYMENT.PI_NOT_ADVANCE", 422, /선수금 T\/T가 아닌 PI/],
    ["PAYMENTS.PAYMENT.ALREADY_REVERSED", 409, /이미 역기록된 입금/],
    ["PAYMENTS.PAYMENT.NOT_REVERSIBLE", 409, /역기록 행은 다시 역기록할 수 없습니다/],
    ["TRADE_DOCS.PAYMENT.PI_NOT_OPEN", 409, /취소되었거나 만료된 PI/],
    ["COMMON.VALIDATION.INVALID_FIELD", 422, /입력값이 올바르지 않습니다/],
    ["SOMETHING.UNKNOWN.CODE", 409, /다른 곳에서 먼저 처리되었습니다/],
  ];
  it.each(cases)("%s → 한국어 문구, 영문 코드·detail 비노출", async (code, status, expected) => {
    open([
      [LIST, "POST", () => err(code, status, "English only message")],
      [LIST, "GET", () => jsonResponse(paymentPage([]))],
    ]);
    const dialog = await openConfirm();
    fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(expected);
    expect(alert.textContent).not.toMatch(/[A-Z]{3,}_[A-Z]|TT_DEFERRED|English only|pi_currency/);
  });

  it("사전은 서버 PAYMENTS.PAYMENT.* 5종·PI_NOT_OPEN을 모두 덮는다", () => {
    for (const code of [
      "PAYMENTS.PAYMENT.CURRENCY_MISMATCH",
      "PAYMENTS.PAYMENT.EXCEEDS_DUE",
      "PAYMENTS.PAYMENT.PI_NOT_ADVANCE",
      "PAYMENTS.PAYMENT.ALREADY_REVERSED",
      "PAYMENTS.PAYMENT.NOT_REVERSIBLE",
      "TRADE_DOCS.PAYMENT.PI_NOT_OPEN",
    ]) {
      expect(PAYMENT_ERROR_TEXT[code]).toMatch(/[가-힣]/);
    }
  });
});
