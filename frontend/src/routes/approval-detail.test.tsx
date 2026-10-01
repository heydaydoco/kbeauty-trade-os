// 승인 상세 — 스냅샷 근거·미수 미반영 badge·결정 자격(서버 can_decide)·본인 기안 SoD(ADMIN 포함)·결정 다이얼로그(승인·반려·회수 본문·사유 필수)·
// 종결 읽기 전용·409·404/403 동일 안내·더블클릭·멱등 키·창 포커스 재조회 (S3-1 PR-9b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { ADMIN, CURRENCY_HANDLER, EVENTS, approval } from "../test/approval-fixtures";
import { stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, jsonResponse, renderWithProviders } from "../test/render";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const server = { approval: approval() };

function open(
  view: ReturnType<typeof approval>,
  extra: Array<[string, string, () => Response]> = [],
  me: unknown = TRADER,
) {
  server.approval = view;
  const stub = stubFetch(me, [
    ...extra,
    CURRENCY_HANDLER,
    ["/v1/approvals/7/events", "GET", () => jsonResponse(EVENTS)],
    ["/v1/approvals/7", "GET", () => jsonResponse(server.approval)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/approvals/7" });
  return stub;
}

const decisionCalls = (calls: Call[]) => calls.filter((c) => c.url.endsWith("/decisions"));
const keysOf = (calls: Call[]) => decisionCalls(calls).map((c) => c.headers["Idempotency-Key"]);
const ERR = (status: number, code: string, message: string, detail: Record<string, unknown> = {}) => () =>
  jsonResponse({ error: { code, message, detail } }, status);

describe("승인 상세 — 근거 표시", () => {
  it("헤더·대상 링크·스냅샷 근거를 서버 값 그대로 보인다(요청 이력은 대결 표기 포함)", async () => {
    open(approval());
    expect(await screen.findByRole("heading", { name: /승인 #7/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SO-2026-0012" })).toHaveAttribute("href", "/sales-orders/12");
    expect(screen.getByText("영업 기안자")).toBeInTheDocument();
    expect(await screen.findByText("1,000.00 USD")).toBeInTheDocument(); // 한도
    expect(screen.getByText("800.00 USD")).toBeInTheDocument(); // 진행 주문
    expect(screen.getByText("500.00 USD")).toBeInTheDocument(); // 이번 수주
    expect(screen.getByText("1,300.00 USD")).toBeInTheDocument(); // 반영 후 노출
    expect(screen.getAllByText("300.00 USD").length).toBeGreaterThanOrEqual(1); // 초과분·기준 금액
    expect(await screen.findByText(/대결 — 위임자 휴가 중 결재자/)).toBeInTheDocument();
    expect(screen.getByText("결재 대기 → 승인")).toBeInTheDocument();
  });

  it("★ 미수 미반영이면 경고 badge와 안내가 보이고, 반영이면 없다", async () => {
    open(approval());
    expect(await screen.findByText("미수 미반영")).toBeInTheDocument();
    expect(screen.getByText(/미수금이 반영되지 않았습니다/)).toBeInTheDocument();
  });

  it("미수가 반영된 스냅샷에는 경고가 없다 / 노출 일부 반영·평가 불능 badge", async () => {
    open(approval({ snapshot: { ...approval().snapshot, receivables_reflected: true, exposure_is_partial: true, verdict: "UNEVALUABLE", reason_codes: "CURRENCY_NOT_CONVERTIBLE" } }));
    await screen.findByText("노출 일부만 반영");
    expect(screen.queryByText("미수 미반영")).not.toBeInTheDocument();
    expect(screen.getAllByText("평가 불능").length).toBeGreaterThan(0);
    expect(screen.getByText(/환산하지 못해 노출 계산에서 빠졌습니다/)).toBeInTheDocument();
  });
});

describe("승인 상세 — 결정 자격(서버 can_decide만)", () => {
  it("결재 자격이 있으면 승인·반려 버튼이 있고 회수는 없다", async () => {
    open(approval());
    expect(await screen.findByRole("button", { name: "승인" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "반려" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "회수" })).not.toBeInTheDocument();
  });

  it("★ 본인이 기안한 승인에는 ADMIN이라도 승인·반려 버튼이 없고 SoD 안내가 보인다", async () => {
    open(approval({ requested_by_id: 9, can_decide: false, decide_blocked_reason: "SELF", can_withdraw: true }), [], ADMIN);
    expect(await screen.findByText(/본인이 요청한 승인은 직접 결정할 수 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "반려" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "회수" })).toBeInTheDocument();
  });

  it("★ 서버가 can_decide를 잘못 true로 주어도 본인 기안에는 결정 버튼을 열지 않는다(방어)", async () => {
    open(approval({ requested_by_id: 9, can_decide: true }), [], ADMIN);
    expect(await screen.findByText(/본인이 요청한 승인은 직접 결정할 수 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
  });

  it("★ ADMIN이어도 서버가 can_decide=false(NOT_APPROVER)면 버튼이 없다 — 역할로 추정하지 않는다", async () => {
    open(approval({ can_decide: false, decide_blocked_reason: "NOT_APPROVER" }), [], ADMIN);
    expect(await screen.findByText(/결재할 수 있는 자격.*열람만 가능/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
  });

  it("회수는 서버가 can_withdraw를 준 기안자에게만 보인다", async () => {
    open(approval({ requested_by_id: 1, can_decide: false, decide_blocked_reason: "SELF", can_withdraw: true }));
    expect(await screen.findByRole("button", { name: "회수" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
  });

  it.each([
    ["REJECTED", { status_reason: "한도 초과 사유 불충분" }, /읽기 전용/, "사유: 한도 초과 사유 불충분"],
    ["WITHDRAWN", { status_reason: "기안 취소" }, /읽기 전용/, "사유: 기안 취소"],
    ["CONSUMED", {}, /수주 확정에 사용된 승인/, null],
    ["VOIDED", { void_reason_code: "TARGET_CHANGED" }, /이 승인은 무효입니다/, null],
  ] as const)("종결 상태 %s는 읽기 전용 — 결정·회수 버튼이 없다", async (status, extra, notice, reason) => {
    open(approval({ status, can_decide: false, can_withdraw: false, ...extra }));
    expect(await screen.findByText(notice)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "반려" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "회수" })).not.toBeInTheDocument();
    if (reason) expect(screen.getByText(reason)).toBeInTheDocument();
  });

  it("무효 승인은 서버 사유 코드를 한국어로 안내한다(digest 불일치=대상 변경)", async () => {
    open(approval({ status: "VOIDED", can_decide: false, void_reason_code: "TARGET_CHANGED" }));
    expect(await screen.findByText(/승인 뒤 대상\(수주\)이 바뀌어 승인이 무효/)).toBeInTheDocument();
  });

  it("없는 승인(404)과 열람권 없음(403)은 같은 안내만 보인다(존재 여부 비노출)", async () => {
    open(approval(), [["/v1/approvals/7", "GET", ERR(404, "COMMON.NOT_FOUND", "대상을 찾을 수 없습니다.")]]);
    expect(await screen.findByRole("alert")).toHaveTextContent("존재하지 않거나 열람 권한이 없습니다.");
  });

  it("열람권 없음(403)도 같은 문구다", async () => {
    open(approval(), [["/v1/approvals/7", "GET", ERR(403, "AUTH.FORBIDDEN", "권한이 없습니다.")]]);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("존재하지 않거나 열람 권한이 없습니다.");
    expect(alert).not.toHaveTextContent("권한이 없습니다.권한");
  });
});

describe("승인 상세 — 결정 다이얼로그", () => {
  it("승인: 본문은 verb·version뿐이고(사유 없음) 성공하면 승인 상태·읽기 전용으로 바뀐다", async () => {
    const { calls } = open(approval(), [
      [
        "/v1/approvals/7/decisions",
        "POST",
        () => jsonResponse((server.approval = approval({ status: "APPROVED", can_decide: false, version: 4, decided_by_name: "무역 담당", decided_at: "2026-10-01T03:00:00Z" }))),
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText(/사유/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
    expect(decisionCalls(calls)[0]?.body).toEqual({ verb: "APPROVE", version: 3 });
    expect(decisionCalls(calls)[0]?.headers["Idempotency-Key"]).toBe("key-1");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("승인했습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "반려" })).not.toBeInTheDocument();
  });

  it("반려: 사유 없이는 확정할 수 없고, 사유와 함께 REJECT가 나간다", async () => {
    const { calls } = open(approval(), [
      ["/v1/approvals/7/decisions", "POST", () => jsonResponse((server.approval = approval({ status: "REJECTED", can_decide: false, status_reason: "근거 부족", version: 4 })))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "반려" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "반려" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("반려 사유 (필수)"), { target: { value: "   " } });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("반려 사유 (필수)"), { target: { value: " 근거 부족 " } });
    fireEvent.click(confirm);
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
    expect(decisionCalls(calls)[0]?.body).toEqual({ verb: "REJECT", version: 3, reason: "근거 부족" });
    expect(await screen.findByText("반려했습니다.")).toBeInTheDocument();
  });

  it("회수: 기안자가 사유와 함께 WITHDRAW를 보낸다", async () => {
    const { calls } = open(approval({ requested_by_id: 1, can_decide: false, decide_blocked_reason: "SELF", can_withdraw: true }), [
      ["/v1/approvals/7/decisions", "POST", () => jsonResponse((server.approval = approval({ status: "WITHDRAWN", requested_by_id: 1, can_decide: false, can_withdraw: false, version: 4 })))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "회수" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("회수 사유 (필수)"), { target: { value: "재기안 예정" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "회수" }));
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
    expect(decisionCalls(calls)[0]?.body).toEqual({ verb: "WITHDRAW", version: 3, reason: "재기안 예정" });
    expect(await screen.findByText("회수했습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "회수" })).not.toBeInTheDocument();
  });

  it("더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(approval());
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith("/decisions")) {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "승인" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(decisionCalls(calls)).toHaveLength(1);
    release(jsonResponse(approval({ status: "APPROVED", can_decide: false, version: 4 })));
  });

  it("같은 본문 재시도는 같은 멱등 키, 사유가 바뀌면 새 키", async () => {
    const { calls } = open(approval(), [["/v1/approvals/7/decisions", "POST", ERR(422, "VALIDATION.INVALID_FIELD", "처리하지 못했습니다.")]]);
    fireEvent.click(await screen.findByRole("button", { name: "반려" }));
    const dialog = await screen.findByRole("dialog");
    const reason = within(dialog).getByLabelText("반려 사유 (필수)");
    const send = async (n: number) => {
      fireEvent.click(within(dialog).getByRole("button", { name: "반려" }));
      await waitFor(() => expect(decisionCalls(calls)).toHaveLength(n));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("처리하지 못했습니다.");
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "반려" })).toBeEnabled());
    };
    fireEvent.change(reason, { target: { value: "가" } });
    await send(1);
    await send(2);
    fireEvent.change(reason, { target: { value: "나" } });
    await send(3);
    const keys = keysOf(calls);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("409는 다이얼로그 안에 '최신 내용 불러오기'를 두고, 누르면 재조회 후 닫힌다", async () => {
    const { calls } = open(approval(), [
      ["/v1/approvals/7/decisions", "POST", ERR(409, "APPROVALS.TRANSITION.NOT_ALLOWED", "현재 승인 상태에서는 할 수 없는 처리입니다.")],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("현재 승인 상태에서는 할 수 없는 처리입니다.");
    server.approval = approval({ status: "REJECTED", can_decide: false, status_reason: "다른 결재자 반려", version: 4 });
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/approvals/7")).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/approvals/7")).length).toBeGreaterThan(before));
    expect(await screen.findByText("사유: 다른 결재자 반려")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "승인" })).not.toBeInTheDocument();
  });

  it("승인 시점에 대상이 바뀌어 STALE(409)이면 서버 문구와 불러오기를 보이고, 불러오면 무효 상태가 된다", async () => {
    open(approval(), [
      ["/v1/approvals/7/decisions", "POST", ERR(409, "APPROVALS.APPROVAL.STALE", "승인 이후 대상이 바뀌어 승인이 무효가 되었습니다. 변경 내용을 확인하고 승인을 다시 요청해 주세요.")],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("승인 이후 대상이 바뀌어 승인이 무효가 되었습니다");
    server.approval = approval({ status: "VOIDED", can_decide: false, void_reason_code: "TARGET_CHANGED", version: 4 });
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    expect(await screen.findByText("이 승인은 무효입니다. 읽기 전용입니다.")).toBeInTheDocument();
  });

  it("403(SELF) 거절은 서버 문구를 다이얼로그 안에 보인다", async () => {
    open(approval(), [["/v1/approvals/7/decisions", "POST", ERR(403, "APPROVALS.DECISION.SELF_APPROVAL", "본인이 올린 승인은 직접 결재할 수 없습니다.")]]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("본인이 올린 승인은 직접 결재할 수 없습니다.");
  });

  it("Esc로 닫히고 포커스가 연 버튼으로 돌아온다", async () => {
    open(approval());
    const opener = await screen.findByRole("button", { name: "승인" });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });

  it("창 포커스 재조회로 이미 결정된 건이 되면 다이얼로그를 닫고 안내한다", async () => {
    open(approval());
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    await screen.findByRole("dialog");
    server.approval = approval({ status: "APPROVED", can_decide: false, version: 4 });
    window.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText(/열려 있던 확인창을 닫았습니다/)).toBeInTheDocument();
  });

  it("서버 version이 앞서가면 배너가 뜨고 결정 버튼이 막히며, 결정은 화면이 본 version으로 나간다", async () => {
    open(approval());
    await screen.findByRole("button", { name: "승인" });
    server.approval = approval({ version: 5 });
    window.dispatchEvent(new Event("visibilitychange"));
    expect(await screen.findByText(/다른 곳에서 이 승인이 수정되었습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "승인" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "반려" })).toBeDisabled();
  });

  it("결정 본문의 version은 다이얼로그를 열 때 화면이 본 값이다(재조회로 서버 version이 앞서가도)", async () => {
    const { calls } = open(approval(), [["/v1/approvals/7/decisions", "POST", ERR(409, "COMMON.CONCURRENCY.VERSION_CONFLICT", "충돌")]]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    server.approval = approval({ version: 5 });
    window.dispatchEvent(new Event("visibilitychange"));
    await screen.findByText(/다른 곳에서 이 승인이 수정되었습니다/);
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
    expect(decisionCalls(calls)[0]?.body).toEqual({ verb: "APPROVE", version: 3 }); // 5가 아니다
  });

  it("★ 요청 중 재조회로 다이얼로그가 닫혀도 잠금이 고착되지 않는다 — 실패는 화면에 알리고, 다시 열어 정상 제출된다", async () => {
    let fail!: (value: Response) => void;
    let n = 0;
    const { calls } = open(approval());
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith("/decisions")) {
          calls.push({ url: u, method: "POST", body: i?.body ? JSON.parse(String(i.body)) : null, headers: (i?.headers ?? {}) as Record<string, string> });
          n += 1;
          if (n === 1) return new Promise<Response>((resolve) => (fail = resolve));
          return Promise.resolve(jsonResponse(approval({ status: "APPROVED", can_decide: false, version: 4 })));
        }
        return base(u, i);
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "승인" }));
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
    // 요청이 나가 있는 동안 다른 곳에서 상태가 바뀌어 다이얼로그가 닫힌다.
    server.approval = approval({ status: "REJECTED", can_decide: false, version: 4 });
    window.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 요청이 실패로 끝나도 조용히 사라지지 않는다.
    fail(jsonResponse({ error: { code: "X", message: "서버 오류입니다." } }, 500));
    expect(await screen.findByText(/결정 요청이 실패했습니다 — 서버 오류입니다/)).toBeInTheDocument();
    // 상태가 다시 결재 가능으로 돌아오면 다시 열어 정상 제출할 수 있다(잠금 해제됨).
    server.approval = approval();
    window.dispatchEvent(new Event("visibilitychange"));
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "승인" }));
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(2));
    expect(await screen.findByText("승인했습니다.")).toBeInTheDocument();
  });

  it("★ 통화표를 못 불러오면 승인은 막히고 안내가 보이며, 반려는 할 수 있다", async () => {
    const { calls } = open(approval(), [
      ["/v1/system/currencies", "GET", ERR(500, "X", "통화 조회 실패")],
      ["/v1/approvals/7/decisions", "POST", () => jsonResponse(approval({ status: "REJECTED", can_decide: false, version: 4 }))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("금액 표시에 필요한 통화 정보를 불러오지 못했습니다 — 새로고침하세요");
    expect(within(dialog).getByRole("button", { name: "승인" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "반려" }));
    const reject = await screen.findByRole("dialog");
    fireEvent.change(within(reject).getByLabelText("반려 사유 (필수)"), { target: { value: "사유" } });
    expect(within(reject).getByRole("button", { name: "반려" })).toBeEnabled();
    fireEvent.click(within(reject).getByRole("button", { name: "반려" }));
    await waitFor(() => expect(decisionCalls(calls)).toHaveLength(1));
  });

  it("결정 오류의 서버 내부 코드(상태·사유 코드)는 한국어로 옮겨 보인다", async () => {
    open(approval(), [
      ["/v1/approvals/7/decisions", "POST", ERR(409, "APPROVALS.TRANSITION.NOT_ALLOWED", "현재 승인 상태에서는 할 수 없는 처리입니다.", { current: "APPROVED", attempted: "APPROVED" })],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("현재 상태: 승인, 시도한 처리: 승인");
    expect(alert).not.toHaveTextContent("APPROVED");
  });

  it("STALE의 사유 코드도 한국어 설명으로 옮긴다", async () => {
    open(approval(), [
      ["/v1/approvals/7/decisions", "POST", ERR(409, "APPROVALS.APPROVAL.STALE", "승인 이후 대상이 바뀌어 승인이 무효가 되었습니다.", { reason: "TARGET_CHANGED" })],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "승인" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "승인" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("승인 뒤 대상(수주)이 바뀌어");
    expect(alert).not.toHaveTextContent("TARGET_CHANGED");
  });
});
