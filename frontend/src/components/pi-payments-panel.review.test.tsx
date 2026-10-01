// PiPaymentsPanel — 적대 검토 반영 테스트 (S3-1 PR-10b 2차): 역기록 422·사유 검증·키 폴백·오프라인·PI 닫힘·실패 후 재조회·접근성.

import { onlineManager } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { todayKst } from "../lib/datetime";
import { paymentPage, paymentRow, summary } from "../test/payment-fixtures";
import { stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, jsonResponse, renderWithProviders } from "../test/render";
import { PiPaymentsPanel, type PaymentPanelPi } from "./pi-payments-panel";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  onlineManager.setOnline(true);
});

const PI: PaymentPanelPi = { id: 5, currency: "USD", minor_units: 2, status: "ISSUED", payment_type: "TT_ADVANCE" };
const err = (code: string, status: number, message = "서버 메시지") =>
  jsonResponse({ error: { code, message, detail: {} } }, status);
type Handler = [string, string, () => Response];
const LIST = "/v1/proforma-invoices/5/payments";
const REV = "/v1/payments/11/reversal";
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
const gets = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.url.includes("/payments"));
const never = () => new Promise<Response>(() => undefined) as unknown as Response;

function open(handlers: Handler[], pi: PaymentPanelPi = PI, onWritten?: () => void) {
  const stub = stubFetch(TRADER, handlers);
  renderWithProviders(<PiPaymentsPanel pi={pi} onWritten={onWritten} />);
  return stub;
}
function fillForm(over: { amount?: string; on?: string; reference?: string } = {}) {
  fireEvent.change(screen.getByLabelText(/입금액/), { target: { value: over.amount ?? "10.00" } });
  if (over.on !== undefined) fireEvent.change(screen.getByLabelText(/입금일/), { target: { value: over.on } });
  fireEvent.change(screen.getByLabelText(/입금 확인 근거/), { target: { value: over.reference ?? "REF-9" } });
}
async function openConfirm() {
  await screen.findByRole("form", { name: "입금 기록" });
  fillForm();
  fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
  return await screen.findByRole("dialog");
}
async function openReversal() {
  fireEvent.click(await screen.findByRole("button", { name: "입금 #11 역기록" }));
  return await screen.findByRole("dialog");
}
const withRow: Handler = [LIST, "GET", () => jsonResponse(paymentPage([paymentRow()]))];
const confirmReceipt = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole("button", { name: "입금 기록 확정" }));
const serverError = () => err("COMMON.INTERNAL", 500, "서버 오류가 발생했습니다.");

describe("역기록 사유", () => {
  it("역기록 422는 역기록 전용 문구, 제어문자(개행·탭)는 사전 거부, 길이는 코드포인트 기준", async () => {
    const { calls } = open([[REV, "POST", () => err("COMMON.VALIDATION.INVALID_FIELD", 422)], withRow]);
    const dialog = await openReversal();
    const reason = within(dialog).getByRole("textbox");
    const confirm = within(dialog).getByRole("button", { name: "역기록 확정" });
    fireEvent.change(reason, { target: { value: "가\n나" } });
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByText(/제어문자/)).toBeInTheDocument();
    fireEvent.change(reason, { target: { value: "가\t나" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: "😀" } }); // UTF-16 2칸이지만 1글자
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: "😀😀" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    const alert = await within(dialog).findByText(/역기록 사유를 확인해 주세요/);
    expect(alert).not.toHaveTextContent("금액");
    expect(posts(calls)).toHaveLength(1);
  });

  it("역기록의 422(코드 무관)·입금의 422는 각자 문구를 쓴다", async () => {
    open([[REV, "POST", () => err("SOME.OTHER.CODE", 422)], withRow]);
    const dialog = await openReversal();
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "사유 있음" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "역기록 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("역기록 사유를 확인해 주세요");
  });
});

describe("키·오프라인·PI 닫힘", () => {
  it("crypto.randomUUID가 던져도 폴백 키로 전송되고 잠금이 고착되지 않는다", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: () => {
        throw new Error("insecure context");
      },
    });
    const { calls } = open([[LIST, "POST", serverError], [LIST, "GET", () => jsonResponse(paymentPage([]))]]);
    const dialog = await openConfirm();
    confirmReceipt(dialog);
    await within(dialog).findByRole("alert");
    confirmReceipt(dialog);
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    const keys = posts(calls).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toMatch(/^pay-/);
    expect(keys[1]).toBe(keys[0]);
  });

  it("오프라인(paused)이어도 요청을 보내고 결과를 다이얼로그에 보인다 — 모달에 갇히지 않는다", async () => {
    const { calls } = open([[LIST, "POST", serverError], [LIST, "GET", () => jsonResponse(paymentPage([]))]]);
    const dialog = await openConfirm();
    onlineManager.setOnline(false);
    confirmReceipt(dialog);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "닫기" })).toBeEnabled();
  });

  it("요청 중 PI가 닫힌 상태로 재조회돼도 입금 다이얼로그는 닫히지 않고, 응답(오류)을 보여 준다", async () => {
    let status = "ISSUED";
    const pending: Array<(value: Response) => void> = [];
    const { calls } = open([
      [LIST, "POST", () => new Promise<Response>((resolve) => pending.push(resolve)) as unknown as Response],
      [LIST, "GET", () => jsonResponse(paymentPage([], summary({ pi_status: status })))],
    ]);
    const dialog = await openConfirm();
    confirmReceipt(dialog);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    status = "CANCELLED";
    const before = gets(calls).length;
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await act(async () => {
      pending[0]?.(err("TRADE_DOCS.PAYMENT.PI_NOT_OPEN", 409));
    });
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("취소되었거나 만료된 PI");
  });

  it("역기록 다이얼로그가 열린 채 PI가 닫혀도 유지되고, 서버 거절(PI_NOT_OPEN)을 한국어로 보인다", async () => {
    let status = "ISSUED";
    const { calls } = open([
      [REV, "POST", () => err("TRADE_DOCS.PAYMENT.PI_NOT_OPEN", 409)],
      [LIST, "GET", () => jsonResponse(paymentPage([paymentRow()], summary({ pi_status: status })))],
    ]);
    const dialog = await openReversal();
    status = "CANCELLED";
    const before = gets(calls).length;
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "사유 있음" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "역기록 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("취소되었거나 만료된 PI");
  });
});

describe("실패·방어", () => {
  it("실패(5xx·422 초과) 뒤 입금 목록·요약을 재조회해 다이얼로그의 남은 선수금이 갱신된다", async () => {
    let remaining = "22.50";
    let post: () => Response = serverError;
    const { calls } = open([
      [LIST, "POST", () => post()],
      [LIST, "GET", () => jsonResponse(paymentPage([], summary({ remaining_text: remaining })))],
    ]);
    const dialog = await openConfirm();
    expect(dialog).toHaveTextContent("남은 선수금: 22.50 USD");
    const steps: Array<[() => Response, string]> = [
      [serverError, "12.50"],
      [() => err("PAYMENTS.PAYMENT.EXCEEDS_DUE", 422), "2.50"],
    ];
    for (const [next, after] of steps) {
      post = next;
      remaining = after;
      const before = gets(calls).length;
      confirmReceipt(dialog);
      await within(dialog).findByRole("alert");
      await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
      await waitFor(() => expect(dialog).toHaveTextContent(`남은 선수금: ${after} USD`));
    }
  });

  it("응답에 warnings가 없어도(방어) 성공 처리가 깨지지 않는다", async () => {
    const written = vi.fn();
    const { calls } = open(
      [
        [LIST, "POST", () => jsonResponse({ payment: paymentRow(), summary: summary() }, 201)],
        [LIST, "GET", () => jsonResponse(paymentPage([]))],
      ],
      PI,
      written,
    );
    const dialog = await openConfirm();
    const before = gets(calls).length;
    confirmReceipt(dialog);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // onSuccess가 중간에 던지면 뒤따르는 재조회(afterWrite)가 빠진다
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
    expect(screen.getByLabelText(/입금액/)).toHaveValue("");
    expect(written).toHaveBeenCalledTimes(1); // 성공 경로 끝(afterWrite)까지 도달 — 오류 경로의 재조회와 구분
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("역기록 403이면 폼과 역기록 버튼을 숨긴다", async () => {
    open([[REV, "POST", () => err("COMMON.AUTH.FORBIDDEN", 403, "Forbidden")], withRow]);
    const dialog = await openReversal();
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "사유 있음" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "역기록 확정" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("form", { name: "입금 기록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /역기록$/ })).not.toBeInTheDocument();
  });
});

describe("입력·분기", () => {
  it("소수 0자리 통화(JPY): 소수점 입력을 거부하고 정수는 통과한다", async () => {
    const { calls } = open([[LIST, "GET", () => jsonResponse(paymentPage([], summary({ currency: "JPY" })))]], {
      ...PI,
      currency: "JPY",
      minor_units: 0,
    });
    await screen.findByRole("form", { name: "입금 기록" });
    fillForm({ amount: "10.5", on: todayKst() });
    fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("소수점 없는 숫자");
    fillForm({ amount: "1000", on: todayKst() });
    fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("1000 JPY");
    expect(posts(calls)).toHaveLength(0);
  });

  it("근거 100자 초과·탭 제어문자는 거부한다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([]))]]);
    await screen.findByRole("form", { name: "입금 기록" });
    fillForm({ reference: "가".repeat(101), on: todayKst() });
    fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("100자 이내");
    fillForm({ reference: "A\tB", on: todayKst() });
    fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("제어문자");
  });

  it("결제유형을 알 수 없는(null) PI도 안내문을 보이고 폼은 숨긴다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([], summary({ payment_type: null })))]], { ...PI, payment_type: null });
    expect(await screen.findByText(/선수금 입금은 선수금 T\/T 전표만 기록합니다/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "입금 기록" })).not.toBeInTheDocument();
  });
});

describe("접근성", () => {
  it("표에 caption·th scope, 비활성 역기록 버튼 사유는 aria-describedby로 읽힌다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([paymentRow()], summary({ pi_status: "CANCELLED" })))]], {
      ...PI,
      status: "CANCELLED",
    });
    const blocked = await screen.findByRole("button", { name: "입금 #11 역기록" });
    expect(blocked).toBeDisabled();
    const desc = blocked.getAttribute("aria-describedby");
    expect(desc).toBeTruthy();
    expect(document.getElementById(desc as string)).toHaveTextContent("역기록할 수 없습니다");
    expect(screen.getByRole("table")).toHaveAccessibleName("입금 내역 (입금과 역기록)");
    for (const th of screen.getAllByRole("columnheader")) expect(th).toHaveAttribute("scope", "col");
  });

  it("검증 실패 시 해당 입력만 aria-invalid, 오류 영역과 aria-describedby로 연결된다", async () => {
    open([[LIST, "GET", () => jsonResponse(paymentPage([]))]]);
    await screen.findByRole("form", { name: "입금 기록" });
    fillForm({ amount: "abc", reference: "REF" });
    fireEvent.click(screen.getByRole("button", { name: "입금 기록" }));
    const alert = await screen.findByRole("alert");
    const amount = screen.getByLabelText(/입금액/);
    expect(amount).toHaveAttribute("aria-invalid", "true");
    expect(amount.getAttribute("aria-describedby")).toContain(alert.id);
    expect(screen.getByLabelText(/입금 확인 근거/)).toHaveAttribute("aria-invalid", "false");
    // 도움말은 label 밖 — 라벨 접근 가능한 이름에 섞이지 않는다
    expect(screen.getByLabelText(/입금액/)).toBe(amount);
    expect(screen.getByText("통화는 PI 통화로 고정됩니다(환산 입금 불가).").closest("label")).toBeNull();
  });
});

void never;
