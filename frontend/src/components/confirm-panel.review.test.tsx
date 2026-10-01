// ConfirmPanel — PR-12b 적대 검토(2렌즈) 반영 시험: 승인 응답 APPROVED 의미·로컬 '마지막 확인' 상태 소거·확정 직후 화면·연결 끊김·조사·접근성.
// 헬퍼는 confirm-panel.test.tsx와 같은 규칙(정확 경로·메서드 스텁).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { josaOf, withJosa } from "../lib/confirm";
import type { GateReport } from "../lib/gate";
import type { SalesOrderDetail } from "../lib/sales-order";
import {
  CONFIRM_URL,
  CREDIT_BASIS,
  CREDIT_ROW,
  GATES_URL,
  PRICE_BLOCKED,
  REQUEST_URL,
  apiError,
  approvalRequestOut,
  blockedResponse,
  confirmOut,
  type BlockedOver,
} from "../test/confirm-fixtures";
import { gate, report, stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, jsonResponse } from "../test/render";
import { soDetail } from "../test/so-fixtures";
import { ConfirmPanel } from "./confirm-panel";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const server: { rep: GateReport } = { rep: report([CREDIT_ROW]) };
const CONFIRMED_SO = soDetail({ status: "CONFIRMED", confirmed_at: "2026-10-01T02:00:00Z", credit_verdict: "APPROVED", credit_approval_id: 33, pi_gate_verdict: "OVERRIDDEN", confirm_evaluation_id: 77 });

function mount(extra: GateHandler[] = [], opts: { so?: SalesOrderDetail; gates?: GateReport } = {}) {
  server.rep = opts.gates ?? report([CREDIT_ROW]);
  const stub = stubGateFetch(TRADER, [...extra, [GATES_URL, "GET", () => jsonResponse(server.rep)]]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onReload = vi.fn();
  const onConfirmed = vi.fn();
  const ui = (p: { so: SalesOrderDetail; version: number; reloadToken: number }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConfirmPanel so={p.so} version={p.version} onReload={onReload} reloadToken={p.reloadToken} onConfirmed={onConfirmed} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  let current = { so: opts.so ?? soDetail(), version: 3, reloadToken: 0 };
  const view = render(ui(current));
  return {
    ...stub,
    ...view,
    onReload,
    onConfirmed,
    setProps: (p: Partial<typeof current>) => {
      current = { ...current, ...p };
      view.rerender(ui(current));
    },
  };
}

const posts = (calls: GateCall[], url: string) => calls.filter((c) => c.method === "POST" && c.url === `/api${url}`);
const gatesGets = (calls: GateCall[]) => calls.filter((c) => c.method === "GET" && c.url === `/api${GATES_URL}`);
const confirmButton = async () => await screen.findByRole("button", { name: "수주 확정" });
const openConfirm = async () => {
  fireEvent.click(await confirmButton());
  return await screen.findByRole("dialog");
};
const dialogConfirm = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: "수주 확정" });
const blockedRegion = () => screen.findByRole("region", { name: "확정 차단 결과" });
const requestButton = () => screen.findByRole("button", { name: "승인 요청 올리기" });
const dialogRequest = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: "승인 요청" });
const refetchViaFocus = () =>
  act(() => {
    window.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
async function attemptBlocked(over: BlockedOver = {}, extra: GateHandler[] = []) {
  const view = mount([[CONFIRM_URL, "POST", () => blockedResponse(over)], ...extra]);
  fireEvent.click(dialogConfirm(await openConfirm()));
  const region = await blockedRegion();
  return { ...view, region };
}
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

describe("승인 요청 응답의 APPROVED는 '낡음'이 아니라 '유효한 승인'이다(검토 1)", () => {
  async function requestOnce(out: ReturnType<typeof approvalRequestOut>, status: number) {
    const view = mount([[REQUEST_URL, "POST", () => jsonResponse(out, status)]]);
    fireEvent.click(await requestButton());
    fireEvent.click(dialogRequest(await screen.findByRole("dialog")));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    return { view, section: screen.getByRole("region", { name: "승인 요청" }) };
  }

  it("200 created=false status=APPROVED → '이미 승인되었습니다. 수주를 다시 확정하세요' — 낡음·다시 요청 문구/버튼 없음", async () => {
    const { section } = await requestOnce(approvalRequestOut({ created: false, status: "APPROVED" }), 200);
    expect(section).toHaveTextContent("이미 승인되었습니다. 수주를 다시 확정하세요.");
    expect(section.textContent).not.toMatch(/더는 쓸 수 없습니다|다시 요청해야|새로 만들지 않았습니다/);
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
    expect(within(section).getByRole("link", { name: "승인 #61 보기" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("승인 #61은 이미 승인되었습니다. 수주를 다시 확정하세요.");
  });

  it("승인 뒤 GET이 CREDIT 해소(APPROVED)로 바뀌어도 방금 요청한 결과 영역은 사라지지 않는다", async () => {
    const { section } = await requestOnce(approvalRequestOut({ created: false, status: "APPROVED" }), 200);
    server.rep = report([{ ...CREDIT_ROW, settlement: "APPROVED" }]);
    refetchViaFocus();
    await settle();
    expect(screen.getByRole("region", { name: "승인 요청" })).toBe(section);
  });

  it("'낡음'은 확정 거부(409)로 받은 APPROVED 때만 — 거기서는 '다시 요청' 버튼이 있다", async () => {
    await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "APPROVED" });
    const section = screen.getByRole("region", { name: "승인 요청" });
    expect(section).toHaveTextContent("더는 쓸 수 없습니다");
    expect(within(section).getByRole("button", { name: "승인 다시 요청" })).toBeInTheDocument();
  });

  it("201 새 요청은 '승인을 요청했습니다'와 결재 대기 배지", async () => {
    const { section } = await requestOnce(approvalRequestOut(), 201);
    expect(section).toHaveTextContent("승인을 요청했습니다. 결재가 끝나면 다시 확정하세요.");
    expect(section).toHaveTextContent("결재 대기");
  });

  it("승인 요청 성공 뒤 같은 version에서 낡은 승인(409 차단)을 다시 만나 재요청하면 새 키(키 Map을 비운다)", async () => {
    const view = mount([
      [CONFIRM_URL, "POST", () => blockedResponse({ pending_approval_id: 61, pending_approval_status: "APPROVED" })],
      [REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut(), 201)],
    ]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    fireEvent.click(screen.getByRole("button", { name: "승인 다시 요청" }));
    fireEvent.click(dialogRequest(await screen.findByRole("dialog")));
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(1));
    // 같은 version에서 다시 확정 시도 → 또 낡은 승인으로 차단 → 재요청
    fireEvent.click(await confirmButton());
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(2));
    fireEvent.click(await screen.findByRole("button", { name: "승인 다시 요청" }));
    fireEvent.click(dialogRequest(await screen.findByRole("dialog")));
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(2));
    const keys = posts(view.calls, REQUEST_URL).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).not.toBe(keys[1]);
  });
});

describe("로컬 '마지막 확인' 상태는 재조회·편집 때 비운다(검토 2·5)", () => {
  it("반려된 승인: 대기 중 링크가 '최신 내용 불러오기'(reloadToken) 뒤 사라지고 서버 GET 기준으로 요청 버튼이 다시 보인다", async () => {
    const view = await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "REQUESTED" });
    expect(screen.getByRole("region", { name: "승인 요청" })).toHaveTextContent("결재 대기 중입니다(마지막으로 확인한 시점)");
    view.setProps({ reloadToken: 1 });
    await waitFor(() => expect(screen.queryByRole("region", { name: "확정 차단 결과" })).not.toBeInTheDocument());
    const section = screen.getByRole("region", { name: "승인 요청" });
    expect(within(section).queryByRole("link", { name: "승인 #61 보기" })).not.toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "승인 요청 올리기" })).toBeInTheDocument();
  });

  it("편집으로 기준 version이 오르면(version prop) 차단 결과·승인 대기를 비운다", async () => {
    const view = await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "REQUESTED" });
    view.setProps({ version: 4 });
    await waitFor(() => expect(screen.queryByRole("region", { name: "확정 차단 결과" })).not.toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "승인 #61 보기" })).not.toBeInTheDocument();
  });

  it("서버 SO version이 바뀌어도(다른 곳 편집) 비운다", async () => {
    const view = await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "REQUESTED" });
    view.setProps({ so: soDetail({ version: 8 }) });
    await waitFor(() => expect(screen.queryByRole("region", { name: "확정 차단 결과" })).not.toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "승인 #61 보기" })).not.toBeInTheDocument();
  });

  it("방금 직접 요청한 결과도 재조회 뒤에는 비운다", async () => {
    const view = mount([[REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut(), 201)]]);
    fireEvent.click(await requestButton());
    fireEvent.click(dialogRequest(await screen.findByRole("dialog")));
    await waitFor(() => expect(screen.getByRole("region", { name: "승인 요청" })).toHaveTextContent("승인을 요청했습니다"));
    view.setProps({ reloadToken: 1 });
    await waitFor(() => expect(screen.getByRole("region", { name: "승인 요청" })).not.toHaveTextContent("승인을 요청했습니다"));
  });

  it("다이얼로그의 '최신 내용 불러오기'(409)가 로컬 차단 결과·승인 대기를 비우고 상위 onReload를 부른다", async () => {
    let conflict = false;
    const view = mount([[CONFIRM_URL, "POST", () => (conflict ? apiError("COMMON.CONCURRENCY.VERSION_CONFLICT", 409) : blockedResponse({ pending_approval_id: 61, pending_approval_status: "REQUESTED" }))]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    conflict = true;
    fireEvent.click(await confirmButton());
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(dialogConfirm(dialog));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    expect(view.onReload).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("region", { name: "확정 차단 결과" })).not.toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "승인 #61 보기" })).not.toBeInTheDocument();
  });

  it("카드의 승인 링크는 서버 GET의 사용 가능 승인이 로컬 값보다 우선한다", async () => {
    const gates = report([{ ...CREDIT_ROW }], { approval: { available: true, approval_id: 99 } });
    mount([[CONFIRM_URL, "POST", () => blockedResponse({ pending_approval_id: 61, pending_approval_status: "REQUESTED" })]], { gates });
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    expect(within(screen.getByRole("region", { name: "여신 평가" })).getByRole("link", { name: "승인 #99 상세" })).toBeInTheDocument();
  });

  it("차단 뒤 서버 참고 판정이 확정 가능 후보로 바뀌면 시도 당시 값임을 알린다(잔존 차단 안내를 거짓으로 두지 않음)", async () => {
    const { region } = await attemptBlocked();
    expect(region).not.toHaveTextContent("지금은 확정 가능 후보입니다");
    server.rep = report([gate()], { clearance: { cleared: true, needs_approval: false, unresolved_count: 0 } });
    refetchViaFocus();
    await waitFor(() => expect(screen.getByRole("region", { name: "확정 차단 결과" })).toHaveTextContent("지금은 확정 가능 후보입니다"));
    expect(screen.getByRole("region", { name: "확정 차단 결과" })).toHaveTextContent("시도 당시 값");
  });

  it("재진입(로컬 상태 없음)·GET만으로 REQUESTED 승인이 있는 SO: 요청 버튼이 보이고 누르면 서버가 기존 승인을 돌려준다", async () => {
    mount([[REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut({ created: false }), 200)]]);
    fireEvent.click(await requestButton());
    fireEvent.click(dialogRequest(await screen.findByRole("dialog")));
    await waitFor(() => expect(screen.getByRole("region", { name: "승인 요청" })).toHaveTextContent("이미 있어 새로 만들지 않았습니다"));
  });

  it("승인 id만 있고 상태를 모르면(otherActive) 링크만 있고 요청 버튼은 없다", async () => {
    await attemptBlocked({ pending_approval_id: 61, pending_approval_status: null });
    const section = screen.getByRole("region", { name: "승인 요청" });
    expect(section).toHaveTextContent("진행 중인 승인이 있습니다");
    expect(within(section).getByRole("link", { name: "승인 #61 보기" })).toBeInTheDocument();
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("확정 성공 직후 화면(검토 3)", () => {
  it("버튼·카드·판정 안내·승인 요청·차단 결과를 숨기고 결과만 보인다, 상위에 알린다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await screen.findByRole("region", { name: "확정 결과" });
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "여신 평가" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
    expect(screen.queryByText(/확정 전 게이트 상태를 확인하세요/)).not.toBeInTheDocument();
    expect(screen.queryByText(/서버 참고 판정/)).not.toBeInTheDocument();
    expect(view.onConfirmed).toHaveBeenCalledTimes(1);
  });

  it("차단 결과가 있던 상태에서 확정이 성공하면 차단 영역이 사라진다", async () => {
    let blocked = true;
    mount([[CONFIRM_URL, "POST", () => (blocked ? blockedResponse() : jsonResponse(confirmOut()))]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    blocked = false;
    fireEvent.click(await confirmButton());
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await screen.findByRole("region", { name: "확정 결과" });
    expect(screen.queryByRole("region", { name: "확정 차단 결과" })).not.toBeInTheDocument();
  });
});

describe("연결 끊김·사전·403 래치 해제(검토 6·7)", () => {
  it("승인 요청의 네트워크 오류도 처리됐을 수 있다는 안내+불러오기 버튼, 개발자 어조 없음", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        input === "/api/v1/auth/me" ? Promise.resolve(jsonResponse(TRADER)) : input.endsWith("/gates") ? Promise.resolve(jsonResponse(server.rep)) : Promise.reject(new Error("net")),
      ),
    );
    server.rep = report([CREDIT_ROW]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ConfirmPanel so={soDetail()} version={3} onReload={() => undefined} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(await requestButton());
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(dialogRequest(dialog));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("승인 요청이 처리되었을 수 있으니");
    expect(alert.textContent).not.toMatch(/백엔드|기동/);
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("qt_id 없는 SO의 확정 다이얼로그에는 QT 수주전환 안내가 없고, 있으면 있다", async () => {
    const first = mount([], { so: soDetail({ qt_id: null, qt_doc_number: null }) });
    expect((await openConfirm()).textContent).not.toMatch(/수주전환/);
    first.unmount();
    mount();
    expect(await openConfirm()).toHaveTextContent("수주전환");
  });

  it("승인 요청 STALE(409) 한국어 안내 + 불러오기, 영문 비노출", async () => {
    mount([[REQUEST_URL, "POST", () => apiError("APPROVALS.APPROVAL.STALE", 409)]]);
    fireEvent.click(await requestButton());
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(dialogRequest(dialog));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("승인을 요청할 수 있는 상태가 아닙니다");
    expect(alert.textContent).not.toMatch(/APPROVALS|STALE|RAW_DETAIL_CODE/i);
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("403 래치는 재조회(reloadToken) 뒤 해제되어 버튼이 돌아온다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => apiError("COMMON.AUTH.FORBIDDEN", 403)]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await waitFor(() => expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument());
    view.setProps({ reloadToken: 1 });
    expect(await confirmButton()).toBeInTheDocument();
  });

  it("403 래치로 다이얼로그가 닫히면 포커스가 안내문으로 이동한다(body로 빠지지 않음)", async () => {
    mount([[CONFIRM_URL, "POST", () => apiError("COMMON.AUTH.FORBIDDEN", 403)]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    const note = await screen.findByText(/수주 확정은 무역·관리자만 할 수 있습니다/);
    await waitFor(() => expect(document.activeElement).toBe(note));
  });

  it("승인 요청 403 래치도 안내문으로 포커스 이동", async () => {
    mount([[REQUEST_URL, "POST", () => apiError("COMMON.AUTH.FORBIDDEN", 403)]]);
    fireEvent.click(await requestButton());
    fireEvent.click(dialogRequest(await screen.findByRole("dialog")));
    const note = await screen.findByText(/수주 확정은 무역·관리자만 할 수 있습니다/);
    await waitFor(() => expect(document.activeElement).toBe(note));
  });
});

describe("요청 중 prop 변화·창 포커스(검토 8)", () => {
  it("확정 요청 중에 SO prop이 비접수로 바뀌어도 다이얼로그는 유지된다(응답 뒤 처리)", async () => {
    const waiters: Array<(r: Response) => void> = [];
    const view = mount([[CONFIRM_URL, "POST", () => new Promise<Response>((resolve) => waiters.push(resolve))]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1));
    view.setProps({ so: CONFIRMED_SO });
    expect(screen.getByRole("dialog")).toBe(dialog);
    await act(async () => {
      waiters[0]?.(jsonResponse(confirmOut()));
    });
    await screen.findByRole("region", { name: "확정 결과" });
  });

  it("승인 요청 중에 SO prop이 비접수로 바뀌어도 다이얼로그는 유지된다", async () => {
    const waiters: Array<(r: Response) => void> = [];
    const view = mount([[REQUEST_URL, "POST", () => new Promise<Response>((resolve) => waiters.push(resolve))]]);
    fireEvent.click(await requestButton());
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(dialogRequest(dialog));
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(1));
    view.setProps({ so: CONFIRMED_SO });
    expect(screen.getByRole("dialog")).toBe(dialog);
    await act(async () => {
      waiters[0]?.(jsonResponse(approvalRequestOut(), 201));
    });
  });

  it("확정 요청 중 창 포커스 재조회가 와도 다이얼로그가 유지되고 POST는 1건", async () => {
    const waiters: Array<(r: Response) => void> = [];
    const view = mount([[CONFIRM_URL, "POST", () => new Promise<Response>((resolve) => waiters.push(resolve))]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1));
    const before = gatesGets(view.calls).length;
    refetchViaFocus();
    await waitFor(() => expect(gatesGets(view.calls).length).toBeGreaterThan(before));
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(within(dialog).getByRole("button", { name: "닫기" })).toBeDisabled();
    expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1);
  });
});

describe("문구·조사·접근성(검토 4·9·10·11)", () => {
  it("조사 헬퍼: 한글 받침·숫자 끝자리", () => {
    expect(withJosa("승인 #61", "을/를")).toBe("승인 #61을");
    expect(withJosa("승인 #62", "을/를")).toBe("승인 #62를");
    expect(withJosa("승인 #60", "이/가")).toBe("승인 #60이");
    expect(withJosa("승인 #64", "이/가")).toBe("승인 #64가");
    expect(withJosa("승인 #63", "은/는")).toBe("승인 #63은");
    expect(withJosa("승인 #65", "은/는")).toBe("승인 #65는");
    expect(withJosa("SO-2026-0007", "을/를")).toBe("SO-2026-0007을");
    expect(withJosa("SO-2026-0009", "을/를")).toBe("SO-2026-0009를");
    expect(withJosa("승인 #68", "을/를")).toBe("승인 #68을");
    expect(withJosa("승인 #66", "이/가")).toBe("승인 #66이");
    expect(withJosa("승인 #67", "이/가")).toBe("승인 #67이");
    expect(withJosa("승인 #69", "이/가")).toBe("승인 #69가");
    expect(josaOf("견적", "이/가")).toBe("이");
    expect(josaOf("수주", "이/가")).toBe("가");
    expect(josaOf("ABC", "이/가")).toBe("가");
    expect(josaOf("", "이/가")).toBe("가");
  });

  it("조사가 들어가는 화면 문구: 수주 번호·견적 번호 끝자리에 맞춘다", async () => {
    const out = confirmOut();
    out.sales_order = { ...out.sales_order, doc_number: "SO-2026-0002", qt_doc_number: "QT-2026-0004" };
    mount([[CONFIRM_URL, "POST", () => jsonResponse(out)]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    const region = await screen.findByRole("region", { name: "확정 결과" });
    expect(region).toHaveTextContent("수주 SO-2026-0002를 확정했습니다");
    expect(region).toHaveTextContent("QT-2026-0004가 '수주전환'");
    expect(region.textContent).not.toMatch(/을\(를\)|이\(가\)|은\(는\)/);
  });

  it("승인 대기 문구의 조사: 승인 #62가", async () => {
    await attemptBlocked({ pending_approval_id: 62, pending_approval_status: "REQUESTED" });
    expect(screen.getByRole("region", { name: "승인 요청" })).toHaveTextContent("승인 #62가 결재 대기 중");
  });

  it("사용자 대면 문구에 영문 '(override)'가 없다(해소 안내)", async () => {
    const { region } = await attemptBlocked({ blocked_gates: [PRICE_BLOCKED, { ...PRICE_BLOCKED, line_id: 42, can_override: false }] });
    expect(region.textContent).not.toMatch(/override/i);
  });

  it("확정 다이얼로그: '관리자도 승인 없이 확정할 수 없습니다'(예외 오해 문구 없음)", async () => {
    mount();
    const dialog = await openConfirm();
    expect(dialog).toHaveTextContent("관리자도 승인 없이 확정할 수 없습니다");
    expect(dialog.textContent).not.toMatch(/관리자도 예외가 없습니다/);
  });

  it("같은 알림 문구가 연속으로 나와도 live region 노드가 새로 만들어진다(재발화)", async () => {
    mount([[CONFIRM_URL, "POST", () => blockedResponse()]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    const first = screen.getByRole("status", { name: "확정 처리 결과" }).firstElementChild;
    fireEvent.click(await confirmButton());
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await waitFor(() => expect(screen.getByRole("status", { name: "확정 처리 결과" }).firstElementChild).not.toBe(first));
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("수주가 확정되지 않았습니다");
  });

  it("카드의 정적 배지는 role=status 소음이 없다", async () => {
    mount();
    const el = await screen.findByRole("region", { name: "여신 평가" });
    expect(within(el).queryAllByRole("status")).toHaveLength(0);
    expect(within(el).getByText("미수 미반영")).toBeInTheDocument();
  });

  it("증거 요약의 dt는 break-keep", async () => {
    mount([], { so: CONFIRMED_SO });
    const evidence = (await screen.findByText("확정 증거 요약")).closest("section") as HTMLElement;
    const dts = Array.from(evidence.querySelectorAll("dt"));
    expect(dts.length).toBe(4);
    for (const dt of dts) expect(dt.className).toContain("break-keep");
  });

  it("카드의 dt도 break-keep", async () => {
    mount([], { gates: report([{ ...CREDIT_ROW, basis: { ...CREDIT_BASIS, limit_amount_text: "1.00" } }]) });
    const el = await screen.findByRole("region", { name: "여신 평가" });
    const dts = Array.from(el.querySelectorAll("dt"));
    expect(dts.length).toBeGreaterThan(0);
    for (const dt of dts) expect(dt.className).toContain("break-keep");
  });

  it("노출 일부 반영 문구는 서버 의미('미수 채권이 아직 노출에 반영되지 않아')이고 '환산하지 못한 주문' 설명이 없다", async () => {
    mount([], { gates: report([{ ...CREDIT_ROW, basis: { ...CREDIT_BASIS, exposure_is_partial: true } }]) });
    const el = await screen.findByRole("region", { name: "여신 평가" });
    expect(el).toHaveTextContent("미수 채권이 아직 노출에 반영되지 않아 실제 노출은 더 클 수 있습니다");
    expect(el.textContent).not.toMatch(/환산하지 못한 주문/);
  });

  it("여신 비관리(NOT_MANAGED): 한도 없음 안내만, 빨간 미수 경고·부분 반영 문구와 병기하지 않는다", async () => {
    const basis = { ...CREDIT_BASIS, verdict: "NOT_MANAGED", limit_amount: null, receivables_reflected: false, exposure_is_partial: true };
    mount([], { gates: report([{ ...CREDIT_ROW, level: "PASS", resolution: "NONE", settlement: "NOT_REQUIRED", basis }]) });
    const el = await screen.findByRole("region", { name: "여신 평가" });
    expect(el).toHaveTextContent("여신 한도가 없는(여신 비관리) 거래처");
    expect(el.textContent).not.toMatch(/미수 미반영|노출 일부만 반영|미수금이 반영되지 않았습니다|미수 채권이 아직/);
  });
});
