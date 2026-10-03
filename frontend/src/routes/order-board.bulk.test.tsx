// 오더 보드 벌크 — 선택·본문·결과 분류별 표시·상세 링크·멱등 키 규칙(클릭마다 새 키, 응답 유실 재전송만 같은 키)·키 충돌 (S3-1 PR-15b, 테스트 그룹 A).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type { BulkReport } from "../lib/order-board";
import { INTAKE_CARD, SO_HOLD_CARD, SO_RECEIVED_CARD, baseHandlers, board, bulkReport, bulkResult } from "../test/board-fixtures";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, jsonResponse, renderWithProviders } from "../test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type Respond = () => Response | Promise<Response>;

function open(bulkResponses: Respond[], extra: GateHandler[] = []) {
  let call = 0;
  const bulk: GateHandler = [
    "/v1/order-board/bulk",
    "POST",
    () => {
      const respond = bulkResponses[Math.min(call, bulkResponses.length - 1)] as Respond;
      call += 1;
      return respond();
    },
  ];
  const stub = stubGateFetch(TRADER, [bulk, ...extra, ["/v1/order-board", "GET", () => jsonResponse(board())], ...baseHandlers()]);
  renderWithProviders(<AppRoutes />, { route: "/orders/board" });
  return stub;
}

const bulkCalls = (calls: GateCall[]) => calls.filter((c) => c.url === "/api/v1/order-board/bulk");
const keyOf = (call: GateCall | undefined) => call?.headers["Idempotency-Key"];

const ok = (report: BulkReport): Respond => () => jsonResponse(report);
const networkDown: Respond = () => Promise.reject(new TypeError("Failed to fetch"));
const gatewayTimeout: Respond = () =>
  ({ ok: false, status: 504, json: () => Promise.reject(new SyntaxError("html")) }) as unknown as Response;
const errorEnvelope = (status: number, code: string, message: string, detail: Record<string, unknown> = {}): Respond => () =>
  jsonResponse({ error: { code, message, detail, request_id: null } }, status);

async function selectSoCardsAndConfirm() {
  fireEvent.click(await screen.findByRole("checkbox", { name: "SO-2026-0012 선택" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "SO-2026-0011 선택" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "IN-21 선택" }));
  fireEvent.click(screen.getByRole("button", { name: "수주 확정 (2건)" }));
  const dialog = await screen.findByRole("dialog", { name: /수주 확정 — 2건/ });
  fireEvent.click(within(dialog).getByRole("button", { name: "수주 확정" }));
}

const resultDialog = () => screen.findByRole("dialog", { name: "수주 확정 결과" });

describe("벌크 — 본문·확인", () => {
  it("수주 확정은 SO만·id 오름차순·카드 version 그대로, 인테이크 선택분은 보내지 않는다고 알린다", async () => {
    const stub = open([ok(bulkReport([bulkResult({ id: 11 }), bulkResult({ id: 12, doc_number: "SO-2026-0012", sales_order_id: 12 })]))]);
    fireEvent.click(await screen.findByRole("checkbox", { name: "SO-2026-0012 선택" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "SO-2026-0011 선택" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "IN-21 선택" }));
    expect(screen.getByText(/선택/, { selector: "span" })).toHaveTextContent("선택 3건");
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (2건)" }));
    const dialog = await screen.findByRole("dialog", { name: /수주 확정 — 2건/ });
    expect(within(dialog).getByText(/대상이 아닌 1건은 보내지 않습니다/)).toBeInTheDocument();
    expect(within(dialog).getByText(/승인·예외 승인은 벌크로 부여되지 않습니다/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "수주 확정" }));
    await resultDialog();
    const [call] = bulkCalls(stub.calls);
    expect(call?.body).toEqual({
      action: "CONFIRM_SO",
      targets: [
        { kind: "SO", id: 11, expected_version: SO_RECEIVED_CARD.version },
        { kind: "SO", id: 12, expected_version: SO_HOLD_CARD.version },
      ],
    });
  });

  it("인테이크 확정은 INTAKE만", async () => {
    const stub = open([ok(bulkReport([bulkResult({ kind: "INTAKE", id: 21, sales_order_id: 77, doc_number: "SO-2026-0077" })], "CONFIRM_INTAKE"))]);
    fireEvent.click(await screen.findByRole("checkbox", { name: "IN-21 선택" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "SO-2026-0011 선택" }));
    fireEvent.click(screen.getByRole("button", { name: "인테이크 확정 (1건)" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "인테이크 확정" }));
    const result = await screen.findByRole("dialog", { name: "인테이크 확정 결과" });
    expect(bulkCalls(stub.calls)[0]?.body).toEqual({ action: "CONFIRM_INTAKE", targets: [{ kind: "INTAKE", id: 21, expected_version: INTAKE_CARD.version }] });
    // OK 인테이크 확정은 생성된 수주로 가는 링크를 준다.
    expect(within(result).getByRole("link", { name: "생성된 수주 보기" })).toHaveAttribute("href", "/sales-orders/77");
  });

  it("담당자 지정은 users/lookup(assignee_target=true)에서 고른 담당자를 두 종류 모두에 보낸다", async () => {
    const stub = open(
      [ok(bulkReport([bulkResult({ kind: "INTAKE", id: 21, outcome: "OK", message_ko: "담당자를 지정했습니다." }), bulkResult({ id: 11, outcome: "SKIPPED", message_ko: "이미 그 담당자입니다." })], "ASSIGN"))],
      [["/v1/users/lookup?assignee_target=true&size=20", "GET", () => jsonResponse({ items: [{ id: 7, display_name: "박담당" }], total: 1, page: 1, size: 20 })]],
    );
    fireEvent.click(await screen.findByRole("checkbox", { name: "IN-21 선택" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "SO-2026-0011 선택" }));
    // 담당자를 고르기 전에는 지정 버튼이 막혀 있다.
    expect(screen.getByRole("button", { name: "담당자 지정 (2건)" })).toBeDisabled();
    fireEvent.focus(screen.getByRole("combobox", { name: "지정할 담당자" }));
    fireEvent.click(await screen.findByRole("option", { name: "박담당" }));
    fireEvent.click(screen.getByRole("button", { name: "담당자 지정 (2건)" }));
    const dialog = await screen.findByRole("dialog", { name: /담당자 지정 — 2건/ });
    expect(within(dialog).getByText(/'박담당' 담당으로 지정합니다/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "담당자 지정" }));
    const result = await screen.findByRole("dialog", { name: "담당자 지정 결과" });
    expect(bulkCalls(stub.calls)[0]?.body).toEqual({
      action: "ASSIGN",
      targets: [
        { kind: "INTAKE", id: 21, expected_version: 1 },
        { kind: "SO", id: 11, expected_version: 3 },
      ],
      assignee_id: 7,
    });
    const skipped = within(result).getByText("이미 그 담당자입니다.").closest("li[data-outcome]") as HTMLElement;
    expect(skipped).toHaveAttribute("data-outcome", "SKIPPED");
    expect(within(skipped).getByText("변경 없음")).toBeInTheDocument();
  });

  it("50건 초과 선택은 버튼을 막고 나눠서 처리하라고 알린다(서버 TOO_MANY 이전 안내)", async () => {
    const many = Array.from({ length: 51 }, (_, i) => ({ ...SO_RECEIVED_CARD, id: 1000 + i, ref_label: `SO-M-${i}` }));
    stubGateFetch(TRADER, [["/v1/order-board", "GET", () => jsonResponse(board({ SO_RECEIVED: { items: many, total: 51 } }))], ...baseHandlers()]);
    renderWithProviders(<AppRoutes />, { route: "/orders/board" });
    const received = await screen.findByRole("region", { name: "수주 접수 열" });
    fireEvent.click(within(received).getByRole("button", { name: "보이는 카드 모두 선택" }));
    expect(screen.getByRole("button", { name: "수주 확정 (51건)" })).toBeDisabled();
    expect(screen.getByText(/한 번에 50건까지 처리할 수 있습니다/)).toBeInTheDocument();
  });
});

describe("벌크 — 결과 분류별 표시", () => {
  const SIX = bulkReport([
    bulkResult({ kind: "SO", id: 11, outcome: "OK", message_ko: "수주를 확정했습니다." }),
    bulkResult({ kind: "SO", id: 12, outcome: "SKIPPED", code: null, message_ko: "이미 확정된 수주입니다.", sales_order_id: null, doc_number: null }),
    bulkResult({
      kind: "SO",
      id: 13,
      outcome: "BLOCKED",
      code: "TRADE_CHAIN.CONFIRM.GATE_BLOCKED",
      message_ko: "해소되지 않은 게이트가 있습니다.",
      version: null,
      sales_order_id: null,
      doc_number: null,
      blocked_gates: [
        { gate_code: "CREDIT", line_id: null, line_no: null, level: "BLOCK", resolution: "APPROVAL", reason_code: "OVER_LIMIT", message_ko: "여신 한도를 초과합니다." },
        { gate_code: "PRICE_DEVIATION", line_id: 41, line_no: 1, level: "BLOCK", resolution: "OVERRIDE", reason_code: "PRICE_ABOVE", message_ko: "단가 편차가 큽니다." },
      ],
    }),
    bulkResult({
      kind: "SO",
      id: 14,
      outcome: "BLOCKED",
      code: "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO",
      message_ko: "같은 바이어 PO번호의 수주가 이미 있습니다.",
      version: null,
      sales_order_id: null,
      doc_number: null,
    }),
    bulkResult({ kind: "SO", id: 15, outcome: "CONFLICT", code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message_ko: "다른 곳에서 먼저 수정되었습니다.", version: null, sales_order_id: null, doc_number: null }),
    bulkResult({ kind: "SO", id: 16, outcome: "FORBIDDEN", code: "COMMON.AUTH.FORBIDDEN", message_ko: "권한이 없습니다.", version: null, sales_order_id: null, doc_number: null }),
    bulkResult({ kind: "SO", id: 17, outcome: "FAILED", code: "COMMON.VALIDATION.INVALID_FIELD", message_ko: "입력값이 올바르지 않습니다.", version: null, sales_order_id: null, doc_number: null }),
  ]);

  const rowOf = (dialog: HTMLElement, message: string) => within(dialog).getByText(message).closest("li[data-outcome]") as HTMLElement;

  it("모든 건에서 message_ko가 첫 줄 — 분류 배지·코드는 보조, 요약 건수는 서버 outcome_counts", async () => {
    open([ok(SIX)]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    const rows = within(dialog).getByRole("list", { name: "건별 결과" }).querySelectorAll("li[data-outcome]");
    expect(rows).toHaveLength(7);
    rows.forEach((row, index) => {
      expect(row.firstElementChild?.textContent).toBe(SIX.results[index]?.message_ko);
    });
    const summary = within(dialog).getAllByRole("status")[0] as HTMLElement;
    expect(summary).toHaveTextContent("전체 7건");
    expect(summary).toHaveTextContent("막힘 — 개별 처리 필요 2");
    expect(summary).toHaveTextContent("처리됨 1");
  });

  it("OK·SKIPPED·FORBIDDEN·FAILED는 서로 다른 배지 문구", async () => {
    open([ok(SIX)]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    expect(within(rowOf(dialog, "수주를 확정했습니다.")).getByText("처리됨")).toBeInTheDocument();
    expect(within(rowOf(dialog, "이미 확정된 수주입니다.")).getByText("변경 없음")).toBeInTheDocument();
    const forbidden = rowOf(dialog, "권한이 없습니다.");
    expect(within(forbidden).getByText("권한 없음")).toBeInTheDocument();
    expect(within(forbidden).getByText("이 건을 처리할 권한이 없습니다.")).toBeInTheDocument();
    const failed = rowOf(dialog, "입력값이 올바르지 않습니다.");
    expect(within(failed).getByText("실패")).toBeInTheDocument();
    expect(within(failed).getByText("COMMON.VALIDATION.INVALID_FIELD")).toBeInTheDocument();
    expect(within(failed).getByRole("link", { name: "상세에서 확인" })).toHaveAttribute("href", "/sales-orders/17");
    // 처리됨·변경 없음·권한 없음 건에는 상세 처리 링크가 없다.
    expect(within(rowOf(dialog, "이미 확정된 수주입니다.")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(forbidden).queryByRole("link")).not.toBeInTheDocument();
    // 처리된 건에도 예외 승인·승인 요청 버튼은 없다(벌크는 통제를 부여하지 않는다).
    expect(within(dialog).queryByRole("button", { name: /예외 승인|승인 요청/ })).not.toBeInTheDocument();
  });

  it("BLOCKED(게이트) — 서버 게이트 문구와 수주 상세 링크", async () => {
    open([ok(SIX)]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    const row = rowOf(dialog, "해소되지 않은 게이트가 있습니다.");
    expect(within(row).getByText("막힘 — 개별 처리 필요")).toBeInTheDocument();
    expect(within(row).getByText("여신 한도를 초과합니다.")).toBeInTheDocument();
    expect(within(row).getByText("단가 편차가 큽니다.")).toBeInTheDocument();
    expect(within(row).getByText(/라인 1/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "수주 상세(확정·게이트)에서 처리" })).toHaveAttribute("href", "/sales-orders/13");
    expect(within(row).queryByText(/개별 처리 필요 — 게이트 판정이 아니라/)).not.toBeInTheDocument();
  });

  it("BLOCKED(게이트 빈 목록 = 업무 거부) — '개별 처리 필요'+code+상세 링크", async () => {
    open([ok(SIX)]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    const row = rowOf(dialog, "같은 바이어 PO번호의 수주가 이미 있습니다.");
    expect(within(row).getByText(/개별 처리 필요 — 게이트 판정이 아니라 업무상 막힌 건입니다\(TRADE_DOCS\.DOCUMENT\.DUPLICATE_BUYER_PO\)/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "수주 상세(확정·게이트)에서 처리" })).toHaveAttribute("href", "/sales-orders/14");
  });

  it("BLOCKED 인테이크는 인테이크 상세 링크", async () => {
    open([
      ok(
        bulkReport(
          [bulkResult({ kind: "INTAKE", id: 21, outcome: "BLOCKED", code: "ORDER_INTAKE.LINE.STALE_MAPPING", message_ko: "품번 매핑이 바뀌었습니다.", sales_order_id: null, doc_number: null, version: null })],
          "CONFIRM_INTAKE",
        ),
      ),
    ]);
    fireEvent.click(await screen.findByRole("checkbox", { name: "IN-21 선택" }));
    fireEvent.click(screen.getByRole("button", { name: "인테이크 확정 (1건)" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "인테이크 확정" }));
    const dialog = await screen.findByRole("dialog", { name: "인테이크 확정 결과" });
    expect(within(dialog).getByRole("link", { name: "인테이크 상세에서 처리" })).toHaveAttribute("href", "/orders/intakes/21");
    expect(within(dialog).getByText("IN-21")).toBeInTheDocument();
  });

  it("CONFLICT — 보드 새로 고침 안내, 누르면 보드를 다시 받는다", async () => {
    const stub = open([ok(SIX)]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    const row = rowOf(dialog, "다른 곳에서 먼저 수정되었습니다.");
    expect(within(row).getByText("경합 — 새로 고침 필요")).toBeInTheDocument();
    expect(within(row).getByText(/보드를 새로 고친 뒤 다시 선택해 주세요/)).toBeInTheDocument();
    const before = stub.calls.filter((c) => c.url === "/api/v1/order-board").length;
    fireEvent.click(within(row).getByRole("button", { name: "보드 새로 고침" }));
    await waitFor(() => expect(stub.calls.filter((c) => c.url === "/api/v1/order-board").length).toBeGreaterThan(before));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("처리 뒤 선택은 비워지고 보드를 다시 받는다", async () => {
    const stub = open([ok(SIX)]);
    await selectSoCardsAndConfirm();
    await resultDialog();
    await waitFor(() => expect(stub.calls.filter((c) => c.url === "/api/v1/order-board").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("button", { name: "수주 확정 (0건)" })).toBeDisabled();
  });
});

describe("벌크 — 멱등 키 규칙", () => {
  it("사용자 클릭마다 새 키 — 같은 선택을 두 번 실행해도 키가 다르다", async () => {
    const report = bulkReport([bulkResult({ id: 11 })]);
    const stub = open([ok(report), ok(report)]);
    await selectSoCardsAndConfirm();
    fireEvent.click(within(await resultDialog()).getByRole("button", { name: "닫기" }));
    await selectSoCardsAndConfirm();
    await resultDialog();
    const calls = bulkCalls(stub.calls);
    expect(calls).toHaveLength(2);
    expect(keyOf(calls[0])).toMatch(/^[0-9a-f-]{36}$/);
    expect(keyOf(calls[0])).not.toBe(keyOf(calls[1]));
  });

  it.each([
    ["네트워크 오류", networkDown, /네트워크 오류/],
    ["504", gatewayTimeout, /응답 시간이 초과/],
  ])("%s 뒤 '결과 다시 받기'는 같은 키·같은 본문으로 재전송하고 리포트를 보인다", async (_name, failure, text) => {
    const stub = open([failure, ok(bulkReport([bulkResult({ id: 11, message_ko: "회수한 결과입니다." })]))]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    expect(await within(dialog).findByText(text)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "결과 다시 받기" }));
    expect(await within(dialog).findByText("회수한 결과입니다.")).toBeInTheDocument();
    const [first, second] = bulkCalls(stub.calls);
    expect(keyOf(second)).toBe(keyOf(first));
    expect(second?.body).toEqual(first?.body);
    // 회수 뒤에는 재전송 버튼이 사라진다.
    expect(within(dialog).queryByRole("button", { name: "결과 다시 받기" })).not.toBeInTheDocument();
  });

  it("응답 유실 뒤 창을 닫아도 화면에 '결과 다시 받기'가 남고, 선택을 바꿔 새로 실행하면 새 키다", async () => {
    const stub = open([networkDown, networkDown, ok(bulkReport([bulkResult({ id: 12 })]))]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    await within(dialog).findByText(/네트워크 오류/);
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    const banner = await screen.findByText(/요청의 응답을 받지 못했습니다/);
    fireEvent.click(within(banner.parentElement as HTMLElement).getByRole("button", { name: "결과 다시 받기" }));
    await waitFor(() => expect(bulkCalls(stub.calls)).toHaveLength(2));
    const [first, second] = bulkCalls(stub.calls);
    expect(keyOf(second)).toBe(keyOf(first));
    fireEvent.click(within(await resultDialog()).getByRole("button", { name: "닫기" }));

    // 선택을 바꾼 새 실행(사용자 클릭) — 새 키, 새 본문.
    fireEvent.click(screen.getByRole("checkbox", { name: "SO-2026-0011 선택" }));
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (1건)" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: /수주 확정 — 1건/ })).getByRole("button", { name: "수주 확정" }));
    await waitFor(() => expect(bulkCalls(stub.calls)).toHaveLength(3));
    const third = bulkCalls(stub.calls)[2];
    expect(keyOf(third)).not.toBe(keyOf(first));
    expect(third?.body).toEqual({ action: "CONFIRM_SO", targets: [{ kind: "SO", id: 12, expected_version: SO_HOLD_CARD.version }] });
  });

  it.each([
    [422, "ORDER_BOARD.BULK.TOO_MANY", "한 번에 처리할 수 있는 건수(50건)를 넘었습니다.", /한 번에 처리할 수 있는 건수/],
    [403, "COMMON.AUTH.FORBIDDEN", "권한이 없습니다.", /벌크 처리 권한이 없습니다/],
    [500, "COMMON.INTERNAL.UNEXPECTED", "일시적인 오류입니다.", /일시적인 오류입니다/],
  ])("응답을 받은 거절(%i)은 재전송 버튼이 없다 — 다음 시도는 새 클릭·새 키", async (status, code, message, shown) => {
    const stub = open([errorEnvelope(status, code, message), ok(bulkReport([bulkResult({ id: 11 })]))]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(shown);
    expect(within(dialog).queryByRole("button", { name: "결과 다시 받기" })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    expect(screen.queryByText(/요청의 응답을 받지 못했습니다/)).not.toBeInTheDocument();
    // 같은 선택 그대로 다시 누르면 새 키다(거절된 키를 재사용하지 않는다).
    fireEvent.click(screen.getByRole("button", { name: "수주 확정 (2건)" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: /수주 확정 — 2건/ })).getByRole("button", { name: "수주 확정" }));
    await waitFor(() => expect(bulkCalls(stub.calls)).toHaveLength(2));
    const [first, second] = bulkCalls(stub.calls);
    expect(keyOf(second)).not.toBe(keyOf(first));
  });

  it("409 KEY_CONFLICT — 서버 문구+실행 안 됨·새로 고침 안내, 재전송 없음", async () => {
    open([errorEnvelope(409, "COMMON.IDEMPOTENCY.KEY_CONFLICT", "같은 요청 키로 다른 내용이 이미 처리되었습니다. 화면을 새로 고쳐 처리 결과를 확인해 주세요.")]);
    await selectSoCardsAndConfirm();
    const dialog = await resultDialog();
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(/^같은 요청 키로 다른 내용이 이미 처리되었습니다/);
    expect(alert).toHaveTextContent("이 요청은 실행되지 않았습니다. 보드를 새로 고친 뒤 대상을 다시 선택해 실행해 주세요.");
    expect(within(dialog).queryByRole("button", { name: "결과 다시 받기" })).not.toBeInTheDocument();
  });
});
