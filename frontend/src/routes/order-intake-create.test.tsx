// 오더 인테이크 수동 등록 — 필수 검증(형식만)·본문 모양(상태·SKU 필드 없음)·멱등 키 규약·더블클릭 1회·서버 오류 한국어화·복제 원본 필드·읽기 전용 (S3-1 PR-13b).

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { apiError, intakeDetail, intakeGateReport } from "../test/intake-fixtures";
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

const REFS: GateHandler[] = [
  ["/v1/partners?type=BUYER&size=20", "GET", () => jsonResponse(page([{ id: 3, partner_code: "P-3", name_ko: "ABC Trading" }]))],
  ["/v1/system/currencies?size=200", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }, { code: "KRW", minor_units: 0 }]))],
  ["/v1/markets?size=200", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국", name_en: null, note: null, version: 1 }]))],
  ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }, { id: 2, display_name: "다른 담당" }]))],
  ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })],
  ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })],
  // 등록 성공 뒤 이동하는 상세 화면
  ["/v1/order-intakes/21", "GET", () => jsonResponse(intakeDetail())],
  ["/v1/order-intakes/21/gates", "GET", () => jsonResponse(intakeGateReport())],
  ["/v1/order-intakes", "GET", () => jsonResponse(page([]))],
];

function open(extra: GateHandler[] = [], me: unknown = TRADER, route = "/orders/intakes/new") {
  const stub = stubGateFetch(me, [...extra, ...REFS]);
  renderWithProviders(<AppRoutes />, { route });
  return stub;
}

const created = () => jsonResponse(intakeDetail(), 201);
const posts = (calls: GateCall[]) => calls.filter((c) => c.method === "POST" && c.url === "/api/v1/order-intakes");

async function fillValid(over: { code?: string; qty?: string; price?: string } = {}) {
  await screen.findByRole("form", { name: "오더 인테이크 등록" });
  fireEvent.focus(screen.getByRole("combobox", { name: "바이어" }));
  fireEvent.click(await screen.findByRole("option", { name: "ABC Trading (P-3)" }));
  await screen.findByRole("option", { name: "USD" });
  fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: "USD" } });
  await screen.findByRole("option", { name: "미국 (US)" });
  fireEvent.change(screen.getByLabelText("도착 시장 *"), { target: { value: "US" } });
  fireEvent.change(screen.getByLabelText("바이어 PO번호 *"), { target: { value: " PO-2026-001 " } });
  fireEvent.change(screen.getByLabelText("라인 1 바이어 품번"), { target: { value: over.code ?? " ABC-1 " } });
  fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: over.qty ?? "10" } });
  fireEvent.change(screen.getByLabelText("라인 1 단가"), { target: { value: over.price ?? "12.50" } });
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "인테이크 등록" }));

describe("필수·형식 검증 — 서버를 부르기 전 형식만(통화 자릿수·0 초과·납기·SKU 중복은 서버)", () => {
  it("필수값이 비면 항목별 한국어 안내가 나오고 요청은 가지 않는다", async () => {
    const stub = open();
    await screen.findByRole("form", { name: "오더 인테이크 등록" });
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("바이어를 선택해 주세요.");
    fireEvent.focus(screen.getByRole("combobox", { name: "바이어" }));
    fireEvent.click(await screen.findByRole("option", { name: "ABC Trading (P-3)" }));
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent("통화를 선택해 주세요.");
    await screen.findByRole("option", { name: "USD" });
    fireEvent.change(screen.getByLabelText("통화 *"), { target: { value: "USD" } });
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent("도착 시장을 선택해 주세요.");
    await screen.findByRole("option", { name: "미국 (US)" });
    fireEvent.change(screen.getByLabelText("도착 시장 *"), { target: { value: "US" } });
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent("바이어 PO번호를 입력해 주세요.");
    fireEvent.change(screen.getByLabelText("바이어 PO번호 *"), { target: { value: "PO-1" } });
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent("라인 1의 바이어 품번을 입력해 주세요.");
    expect(posts(stub.calls)).toHaveLength(0);
  });

  it.each([
    ["수량 0", { qty: "0" }, "라인 1의 수량은 1 이상의 정수"],
    ["수량 소수", { qty: "1.5" }, "라인 1의 수량은 1 이상의 정수"],
    ["단가 콤마", { price: "1,250" }, "라인 1의 단가는 숫자"],
    ["단가 비움", { price: "" }, "라인 1의 단가는 숫자"],
  ])("%s → 형식 오류 안내, 요청 없음", async (_name, over, message) => {
    const stub = open();
    await fillValid(over);
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(posts(stub.calls)).toHaveLength(0);
  });

  it("같은 품번 두 라인·단가 0은 형식상 통과 — 서버 판정으로 넘긴다", async () => {
    const stub = open([["/v1/order-intakes", "POST", created]]);
    await fillValid({ price: "0" });
    fireEvent.click(screen.getByRole("button", { name: "라인 추가" }));
    fireEvent.change(screen.getByLabelText("라인 2 바이어 품번"), { target: { value: "ABC-1" } });
    fireEvent.change(screen.getByLabelText("라인 2 수량"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("라인 2 단가"), { target: { value: "1" } });
    submit();
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
  });
});

describe("등록 요청", () => {
  it("본문: 헤더+라인(단가는 문자열 그대로), 상태·SKU·PO키 필드 없음, 멱등 키 헤더, 성공 시 상세로 이동", async () => {
    const stub = open([["/v1/order-intakes", "POST", created]]);
    await fillValid();
    fireEvent.change(screen.getByLabelText("바이어 PO 일자"), { target: { value: "2026-09-29" } });
    fireEvent.change(screen.getByLabelText("라인 1 요청납기"), { target: { value: "2099-12-31" } });
    submit();
    expect(await screen.findByRole("heading", { name: /PO PO-2026-001/ })).toBeInTheDocument();
    const [call] = posts(stub.calls);
    expect(call?.body).toEqual({
      buyer_partner_id: 3,
      buyer_po_no: "PO-2026-001",
      buyer_po_date: "2026-09-29",
      currency: "USD",
      dest_market_code: "US",
      lines: [{ buyer_item_code: "ABC-1", quantity: 10, unit_price: "12.50", requested_delivery_date: "2099-12-31" }],
    });
    // 상태·SKU·PO 키·SO 백링크 필드는 하나하나 없어야 한다(서버가 산출 — 착지는 항상 대기).
    for (const forbidden of ["status", "sku_id", "buyer_po_no_key", "sales_order_id"]) expect(call?.body).not.toHaveProperty(forbidden);
    expect(call?.body?.lines).toEqual([expect.not.objectContaining({ sku_id: expect.anything() })]);
    expect(call?.headers["Idempotency-Key"]).toBe("key-1");
  });

  it("담당자를 고르면 assignee_id를 보내고, 비우면 보내지 않는다(서버 기본=나)", async () => {
    const stub = open([["/v1/order-intakes", "POST", created]]);
    await fillValid();
    await screen.findByRole("option", { name: "다른 담당" });
    fireEvent.change(screen.getByLabelText("담당자 (비우면 나)"), { target: { value: "2" } });
    submit();
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
    expect(posts(stub.calls)[0]?.body?.assignee_id).toBe(2);
  });

  it("더블클릭은 1회만 전송한다(잠금은 응답 뒤 finally에서 해제)", async () => {
    let release: (r: Response) => void = () => undefined;
    const stub = open([["/v1/order-intakes", "POST", () => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response]]);
    await fillValid();
    // 버튼이 비활성으로 바뀌기 전의 연타·엔터 반복을 재현한다 — 폼 제출을 직접 여러 번 보낸다(잠금이 막아야 한다).
    const form = screen.getByRole("form", { name: "오더 인테이크 등록" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
    expect(screen.getByRole("button", { name: "등록 중…" })).toBeDisabled();
    await act(async () => release(created()));
    await screen.findByRole("heading", { name: /PO PO-2026-001/ });
    expect(posts(stub.calls)).toHaveLength(1);
  });

  it("오프라인 상태여도 요청을 보내 본다(paused로 화면이 갇히지 않는다 — networkMode always)", async () => {
    const stub = open([["/v1/order-intakes", "POST", created]]);
    await fillValid();
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    try {
      submit();
      await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
    } finally {
      act(() => {
        window.dispatchEvent(new Event("online"));
      });
    }
  });

  it("멱등 키: 같은 본문 재시도(실패 후)=같은 키, 본문이 바뀌면 새 키, A로 되돌아가면 A의 키", async () => {
    let fail = true;
    const stub = open([["/v1/order-intakes", "POST", () => (fail ? apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409) : created())]]);
    await fillValid();
    submit();
    await screen.findByRole("alert");
    submit(); // 같은 본문 재시도
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "11" } });
    submit(); // 다른 본문
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(3));
    fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "10" } });
    fail = false;
    submit(); // 다시 A — A의 키
    await screen.findByRole("heading", { name: /PO PO-2026-001/ });
    const keys = posts(stub.calls).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
    expect(keys[3]).toBe(keys[0]);
  });
});

describe("서버 오류 안내(한국어 — 영문 코드·원문 비노출)", () => {
  it("중복 바이어 PO — 점유 수주 번호·상태는 서버가 준 값만", async () => {
    open([["/v1/order-intakes", "POST", () => apiError("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, "english", { doc_number: "SO-2026-0007", status: "RECEIVED" })]]);
    await fillValid();
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("이미 등록된 수주: SO-2026-0007 (접수)");
    expect(alert).not.toHaveTextContent("english");
  });

  it("중복 바이어 PO — 대기 인테이크가 점유하면 그 인테이크로 가는 링크를 둔다", async () => {
    open([["/v1/order-intakes", "POST", () => apiError("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, "english", { intake_id: 8, status: "PENDING" })]]);
    await fillValid();
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("이미 등록된 대기 인테이크: #8 (대기)");
    expect(screen.getByRole("link", { name: "점유 중인 인테이크 #8 보기" })).toHaveAttribute("href", "/orders/intakes/8");
  });

  it("마스킹 역할이 받은 번호 없는 중복 PO 오류 — 번호를 지어내지 않는다", async () => {
    open([["/v1/order-intakes", "POST", () => apiError("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, "english", {})]]);
    await fillValid();
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("이미 다른 문서");
    expect(alert).not.toHaveTextContent(/SO-|#\d/);
  });

  it("검증 422 — 라인 위치와 서버 한국어 메시지(단가·납기)", async () => {
    open([["/v1/order-intakes", "POST", () => apiError("COMMON.VALIDATION.INVALID_FIELD", 422, "english", { "lines[0].unit_price": "단가는 0보다 커야 합니다." })]]);
    await fillValid();
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("라인 1 단가: 단가는 0보다 커야 합니다.");
  });

  it("요청 검증 422 봉투(`항목[].위치·사유`) — 위치는 한국어로, 영문 사유는 숨기고 위치만", async () => {
    open([
      [
        "/v1/order-intakes",
        "POST",
        () => apiError("COMMON.VALIDATION.INVALID_FIELD", 422, "english", { 항목: [{ 위치: "lines.0.unit_price", 사유: "String should have at most 40 characters" }] }),
      ],
    ]);
    await fillValid();
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("라인 1 단가를 확인해 주세요.");
    expect(alert).not.toHaveTextContent("String should");
  });

  it("403·연결 끊김(TypeError) — 한국어 안내", async () => {
    let next: () => Response = () => apiError("AUTH.FORBIDDEN", 403);
    open([["/v1/order-intakes", "POST", () => next()]]);
    await fillValid();
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("등록은 무역·관리자만 할 수 있습니다.");
    next = () => {
      throw new TypeError("Failed to fetch");
    };
    fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "12" } });
    submit();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("연결이 끊겼습니다. 등록이 처리되었을 수 있으니 목록에서 확인"));
  });

  it("입력을 고치면 이전 오류 안내가 사라진다", async () => {
    open([["/v1/order-intakes", "POST", () => apiError("ORDER_INTAKE.LINE.LIMIT_EXCEEDED", 422)]]);
    await fillValid();
    submit();
    await screen.findByRole("alert");
    fireEvent.change(screen.getByLabelText("바이어 PO번호 *"), { target: { value: "PO-2" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("복제 재접수·읽기 전용", () => {
  it("?copied_from_so_id=12 — 원본 안내를 보이고 본문에 싣는다", async () => {
    const stub = open([["/v1/order-intakes", "POST", created]], TRADER, "/orders/intakes/new?copied_from_so_id=12");
    await fillValid();
    expect(screen.getByRole("link", { name: "#12" })).toHaveAttribute("href", "/sales-orders/12");
    submit();
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
    expect(posts(stub.calls)[0]?.body?.copied_from_so_id).toBe(12);
  });

  it("원본 자격 거절(409 COPY.SOURCE_NOT_ELIGIBLE) — 불러오기 지시 없이 '복제 없이 등록'으로 원본만 빼고 입력을 유지해 다시 보낸다", async () => {
    let n = 0;
    const stub = open([["/v1/order-intakes", "POST", () => (++n === 1 ? apiError("TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE", 409, "english") : created())]], TRADER, "/orders/intakes/new?copied_from_so_id=12");
    await fillValid();
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("복제 원본 수주가 복제할 수 있는 상태가 아닙니다");
    expect(alert).not.toHaveTextContent("최신 내용 불러오기");
    expect(screen.queryByRole("button", { name: "최신 내용 불러오기" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "복제 없이 등록" }));
    await screen.findByRole("heading", { name: /PO PO-2026-001/ });
    const [first, second] = posts(stub.calls);
    expect(first?.body?.copied_from_so_id).toBe(12);
    expect(second?.body).not.toHaveProperty("copied_from_so_id");
    // 원본만 빠지고 나머지 입력은 그대로.
    expect(second?.body?.buyer_po_no).toBe("PO-2026-001");
    expect(second?.body?.lines).toEqual(first?.body?.lines);
    expect(second?.headers["Idempotency-Key"]).not.toBe(first?.headers["Idempotency-Key"]);
  });

  it("값이 없거나 올바르지 않으면 필드·안내가 없고 본문에도 싣지 않는다(서버 검증 메시지로 안내)", async () => {
    const stub = open([["/v1/order-intakes", "POST", created]], TRADER, "/orders/intakes/new?copied_from_so_id=abc");
    await fillValid();
    expect(screen.queryByText(/복제 재접수/)).not.toBeInTheDocument();
    submit();
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
    expect(posts(stub.calls)[0]?.body).not.toHaveProperty("copied_from_so_id");
  });

  it("읽기 전용 역할은 폼 없이 안내만 본다", async () => {
    open([], VIEWER);
    expect(await screen.findByText(/인테이크 등록은 무역·관리자만 할 수 있습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "오더 인테이크 등록" })).not.toBeInTheDocument();
  });
});

describe("적대 검토 반영 — 형식 문제 모아 보기·이동 안전", () => {
  it("형식 문제를 모두 모아 보이고, 문제 입력에 aria-invalid를 달고 첫 문제 입력으로 포커스를 옮긴다", async () => {
    const stub = open();
    await screen.findByRole("form", { name: "오더 인테이크 등록" });
    fireEvent.focus(screen.getByRole("combobox", { name: "바이어" }));
    fireEvent.click(await screen.findByRole("option", { name: "ABC Trading (P-3)" }));
    fireEvent.change(screen.getByLabelText("라인 1 수량"), { target: { value: "0" } });
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("통화를 선택해 주세요.");
    expect(alert).toHaveTextContent("도착 시장을 선택해 주세요.");
    expect(alert).toHaveTextContent("바이어 PO번호를 입력해 주세요.");
    expect(alert).toHaveTextContent("라인 1의 바이어 품번을 입력해 주세요.");
    expect(alert).toHaveTextContent("라인 1의 수량은 1 이상의 정수");
    expect(alert).toHaveTextContent("라인 1의 단가는 숫자");
    expect(screen.getByLabelText("통화 *")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("라인 1 수량")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("라인 1 수량")).toHaveAccessibleDescription(expect.stringContaining("라인 1의 수량은 1 이상의 정수"));
    expect(screen.getByLabelText("바이어 PO 일자")).not.toHaveAttribute("aria-invalid", "true");
    await waitFor(() => expect(screen.getByLabelText("통화 *")).toHaveFocus());
    expect(posts(stub.calls)).toHaveLength(0);
  });

  it("요청 중 다른 화면으로 떠나면 응답이 성공해도 상세로 끌고 가지 않는다", async () => {
    let release: (r: Response) => void = () => undefined;
    const stub = open([["/v1/order-intakes", "POST", () => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response]]);
    await fillValid();
    submit();
    await waitFor(() => expect(posts(stub.calls)).toHaveLength(1));
    fireEvent.click(screen.getByRole("link", { name: "← 인테이크 목록" }));
    await screen.findByRole("heading", { name: "주문 접수 (오더 인테이크)" });
    await act(async () => release(created()));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.getByRole("heading", { name: "주문 접수 (오더 인테이크)" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /PO PO-2026-001/ })).not.toBeInTheDocument();
  });
});
