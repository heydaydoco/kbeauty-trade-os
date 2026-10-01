// ConfirmPanel·CreditEvaluationCard·승인 요청 흐름 (S3-1 PR-12b) — 프런트는 통과·미통과·승인 필요를 다시 판정하지 않는다(서버 409 blocked_gates[]·GET gates 값을 그대로 표시).
// fetch 스텁은 정확 경로·메서드 일치(`stubGateFetch`) — 오라우팅은 404로 터진다.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { approvalRequestErrorMessage, confirmErrorMessage, parseBlockedDetail } from "../lib/confirm";
import { ApiError } from "../lib/api";
import type { SalesOrderDetail } from "../lib/sales-order";
import {
  CONFIRM_URL,
  CREDIT_BASIS,
  CREDIT_ROW,
  GATES_URL,
  MAPPING_BLOCKED,
  PRICE_BLOCKED,
  REQUEST_URL,
  apiError,
  approvalRequestOut,
  blockedGate,
  blockedResponse,
  confirmOut,
  type BlockedOver,
} from "../test/confirm-fixtures";
import { gate, report, stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse } from "../test/render";
import { soDetail } from "../test/so-fixtures";
import type { GateReport } from "../lib/gate";
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

const ADMIN = { ...TRADER, id: 2, roles: ["ADMIN"] };
const server: { rep: GateReport } = { rep: report([CREDIT_ROW]) };

interface Opts {
  me?: unknown;
  so?: SalesOrderDetail;
  version?: number;
  gates?: GateReport;
  onReload?: () => void;
}

/** rerender로 prop(version·so)을 바꿀 수 있게 래퍼를 직접 만든다. */
function mount(extra: GateHandler[] = [], opts: Opts = {}) {
  server.rep = opts.gates ?? report([CREDIT_ROW]);
  const stub = stubGateFetch(opts.me ?? TRADER, [...extra, [GATES_URL, "GET", () => jsonResponse(server.rep)]]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onReload = opts.onReload ?? vi.fn();
  const ui = (p: { so: SalesOrderDetail; version: number }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConfirmPanel so={p.so} version={p.version} onReload={onReload} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const initial = { so: opts.so ?? soDetail(), version: opts.version ?? 3 };
  const view = render(ui(initial));
  return { ...stub, ...view, client, onReload, setProps: (p: Partial<{ so: SalesOrderDetail; version: number }>) => view.rerender(ui({ ...initial, ...p })) };
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
const refetchViaFocus = () =>
  act(() => {
    window.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
/** 확정 시도 → 409 BLOCKED 표시까지. */
async function attemptBlocked(over: BlockedOver = {}, extra: GateHandler[] = [], opts: Opts = {}) {
  const view = mount([[CONFIRM_URL, "POST", () => blockedResponse(over)], ...extra], opts);
  const dialog = await openConfirm();
  fireEvent.click(dialogConfirm(dialog));
  const region = await blockedRegion();
  return { ...view, region };
}

const CONFIRMED_SO = soDetail({
  status: "CONFIRMED",
  confirmed_at: "2026-10-01T02:00:00Z",
  credit_verdict: "APPROVED",
  credit_approval_id: 33,
  pi_gate_verdict: "OVERRIDDEN",
  confirm_evaluation_id: 77,
});

describe("ConfirmPanel — 버튼·안내(서버가 최종 판정)", () => {
  it("접수 SO에서만 '수주 확정' 버튼과 '확정 전 게이트 상태를 확인하세요' 안내가 보인다", async () => {
    mount();
    expect(await confirmButton()).toBeInTheDocument();
    expect(screen.getByText(/확정 전 게이트 상태를 확인하세요/)).toBeInTheDocument();
    expect(screen.queryByText(/확정 시도/)).not.toBeInTheDocument();
  });

  it("미해소여도(서버 cleared=false) 버튼은 그대로 서버를 부른다 — 미해소 건수는 서버 값 그대로", async () => {
    mount([], { gates: report([CREDIT_ROW], { clearance: { cleared: false, needs_approval: true, unresolved_count: 5 } }) });
    expect(await screen.findByText(/미해소 게이트 5건이 있습니다/)).toBeInTheDocument();
    expect(await confirmButton()).toBeEnabled();
  });

  it("서버가 cleared=true를 주면 '확정 가능 후보'로 표시하되 최종 판정은 서버라고 알린다", async () => {
    mount([], { gates: report([gate()], { clearance: { cleared: true, needs_approval: false, unresolved_count: 0 } }) });
    expect(await screen.findByText(/확정 가능 후보입니다/)).toBeInTheDocument();
    expect(screen.getByText(/최종 판정은 확정 시 서버가 합니다/)).toBeInTheDocument();
  });

  it("서버가 cleared=false인데 모든 행이 통과여도 '확정 가능 후보'라고 말하지 않는다(프런트 재판정 없음)", async () => {
    mount([], { gates: report([gate()], { clearance: { cleared: false, needs_approval: false, unresolved_count: 0 } }) });
    expect(await screen.findByText(/미해소 게이트 0건이 있습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/확정 가능 후보입니다/)).not.toBeInTheDocument();
  });

  it("서버가 cleared=true인데 미해소 행이 있어도 서버 값을 그대로 보인다(행에서 다시 세지 않음)", async () => {
    mount([], { gates: report([CREDIT_ROW], { clearance: { cleared: true, needs_approval: false, unresolved_count: 0 } }) });
    expect(await screen.findByText(/확정 가능 후보입니다/)).toBeInTheDocument();
  });

  it("게이트 조회 실패여도 확정 버튼은 남고(서버가 판정) 안내 문구가 나온다", async () => {
    mount([[GATES_URL, "GET", () => apiError("COMMON.SERVER.INTERNAL", 500)]]);
    expect(await screen.findByText(/게이트 상태를 불러오지 못했습니다/)).toBeInTheDocument();
    expect(await confirmButton()).toBeInTheDocument();
  });

  it("무역·관리자가 아니면(VIEWER) 확정 버튼이 없고 안내만 보인다", async () => {
    mount([], { me: VIEWER });
    expect(await screen.findByText(/수주 확정은 무역·관리자만 할 수 있습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /승인 요청 올리기/ })).not.toBeInTheDocument();
  });

  it("관리자는 확정 버튼이 보인다(hasRole)", async () => {
    mount([], { me: ADMIN });
    expect(await confirmButton()).toBeInTheDocument();
  });

  it.each([
    ["ON_HOLD", "보류 중인 수주는 확정할 수 없습니다"],
    ["CANCELLED", "취소 상태의 수주는 확정할 수 없습니다"],
  ])("%s SO(확정 전)는 버튼 없이 사유 안내", async (status, text) => {
    mount([], { so: soDetail({ status }) });
    expect(await screen.findByText(new RegExp(text))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
  });
});

describe("ConfirmPanel — 확정 다이얼로그·성공", () => {
  it("다이얼로그가 되돌릴 수 없음·동결·QT 수주전환·서버 재평가를 안내한다", async () => {
    mount();
    const dialog = await openConfirm();
    expect(dialog).toHaveTextContent("확정하면 되돌릴 수 없습니다");
    expect(dialog).toHaveTextContent("단가·환율·결제조건·라인이 동결");
    expect(dialog).toHaveTextContent("'수주전환'으로 표시됩니다");
    expect(dialog).toHaveTextContent("서버가 게이트 7종을 다시 평가합니다");
  });

  it("요청 본문은 {version}뿐이고 prop version을 싣는다(SO 응답 version이 아니라 화면 기준)", async () => {
    const { calls } = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]], { so: soDetail({ version: 9 }), version: 3 });
    fireEvent.click(dialogConfirm(await openConfirm()));
    await waitFor(() => expect(posts(calls, CONFIRM_URL)).toHaveLength(1));
    expect(posts(calls, CONFIRM_URL)[0]?.body).toEqual({ version: 3 });
    expect(posts(calls, CONFIRM_URL)[0]?.headers["Idempotency-Key"]).toBe("key-1");
  });

  it("성공: 결과 요약(QT 전환·예외 승인·경고 사용·할당 결과)·live region·포커스 이동·다이얼로그 닫힘", async () => {
    mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    const region = await screen.findByRole("region", { name: "확정 결과" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(region).toHaveTextContent("수주 SO-2026-0001를 확정했습니다");
    expect(within(region).getByRole("link", { name: "QT-2026-0001" })).toHaveAttribute("href", "/quotations/7");
    expect(region).toHaveTextContent("'수주전환'으로 표시됩니다");
    expect(region).toHaveTextContent("예외 승인을 사용한 게이트: PI 선수금 입금");
    expect(region).toHaveTextContent("경고 상태로 통과한 게이트: 중복 PO");
    expect(region).toHaveTextContent("재고 할당은 아직 구현 전이라 할당되지 않았습니다 — 재고 할당 포트가 아직 연결되지 않았습니다.");
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("수주를 확정했습니다.");
    await waitFor(() => expect(document.activeElement).toBe(region));
    expect(region.textContent).not.toMatch(/NOT_IMPLEMENTED|OVERRIDDEN|PI_DEPOSIT|WARN/);
  });

  it("성공 뒤 확정 증거 요약(증적 3열·승인 링크·증거 번호)이 응답으로 채워진다", async () => {
    mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    const evidence = await screen.findByRole("region", { name: "확정 결과" }).then(() => screen.getByText("확정 증거 요약").closest("section") as HTMLElement);
    expect(evidence).toHaveTextContent("여신 초과 승인을 사용해 확정");
    expect(within(evidence).getByRole("link", { name: "승인 #33" })).toHaveAttribute("href", "/approvals/33");
    expect(evidence).toHaveTextContent("예외 승인을 사용해 확정");
    expect(evidence).toHaveTextContent("#77");
  });

  it("성공 후 SO·문서 흐름·QT·PI·승인 쿼리를 무효화한다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    const spy = vi.spyOn(view.client, "invalidateQueries");
    fireEvent.click(dialogConfirm(await openConfirm()));
    await screen.findByRole("region", { name: "확정 결과" });
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    for (const key of ['["sales-orders"]', '["document-flow"]', '["quotations"]', '["proforma-invoices"]', '["approvals"]']) {
      expect(keys).toContain(key);
    }
  });

  it("성공 후 '최신 내용 불러오기'는 상위 onReload를 부른다(기준 version을 자동으로 옮기지 않는다)", async () => {
    const onReload = vi.fn();
    mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]], { onReload });
    fireEvent.click(dialogConfirm(await openConfirm()));
    const region = await screen.findByRole("region", { name: "확정 결과" });
    expect(onReload).not.toHaveBeenCalled();
    fireEvent.click(within(region).getByRole("button", { name: "최신 내용 불러오기" }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("QT가 없는 수주(qt_id null)는 QT 전환 문구를 만들지 않는다", async () => {
    const out = confirmOut();
    out.sales_order = { ...out.sales_order, qt_id: null, qt_doc_number: null };
    mount([[CONFIRM_URL, "POST", () => jsonResponse(out)]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    const region = await screen.findByRole("region", { name: "확정 결과" });
    expect(region).not.toHaveTextContent("수주전환");
  });
});

describe("ConfirmPanel — 409 GATE_BLOCKED(서버 blocked_gates[] 그대로)", () => {
  it("게이트별 해소 수단 안내: NONE=데이터 수정·OVERRIDE(can_override)=예외 승인 이동·APPROVAL=승인 요청", async () => {
    const { region } = await attemptBlocked({ blocked_gates: [MAPPING_BLOCKED, PRICE_BLOCKED, blockedGate()] });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(region).toHaveTextContent("해소되지 않은 게이트 3건");
    const items = within(region).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("품번 매핑 · 라인 1");
    expect(items[0]).toHaveTextContent("바이어 품번이 SKU에 매핑되지 않았습니다.");
    expect(items[0]).toHaveTextContent("수주 데이터를 고친 뒤 다시 확정하세요");
    expect(items[1]).toHaveTextContent("가격 편차 · 라인 1");
    expect(items[1]).toHaveTextContent("'게이트 판정' 표에서 해당 항목의 '예외 승인' 버튼으로 처리하세요");
    expect(items[2]).toHaveTextContent("여신 한도");
    expect(items[2]).toHaveTextContent("승인(결재)으로만 해소됩니다");
  });

  it("영문 코드·reason_code·판정 열거값·detail은 화면에 나오지 않는다", async () => {
    const { region } = await attemptBlocked({ blocked_gates: [{ ...PRICE_BLOCKED, detail: { other_doc_number: "RAWDOC" } }, blockedGate()] });
    expect(region.textContent).not.toMatch(/PRICE_DEVIATION|CREDIT|PRICE_ABOVE_TOLERANCE|LIMIT_EXCEEDED|OVERRIDE|APPROVAL|BLOCK|GATE_BLOCKED|RAWDOC/);
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("해소되지 않은 게이트가 2건 있습니다");
  });

  it("OVERRIDE인데 서버가 can_override=false를 주면 '현재 역할·상태로는 부여할 수 없다'(프런트가 해소 가능을 추정하지 않음)", async () => {
    const { region } = await attemptBlocked({ blocked_gates: [{ ...PRICE_BLOCKED, can_override: false, override_roles: ["ADMIN"] }] });
    expect(region).toHaveTextContent("현재 역할·상태로는 부여할 수 없습니다");
    expect(region).not.toHaveTextContent("버튼으로 처리하세요");
  });

  it("차단 결과로 포커스가 이동하고 live region이 알린다", async () => {
    const { region } = await attemptBlocked();
    await waitFor(() => expect(document.activeElement).toBe(region));
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("수주가 확정되지 않았습니다");
  });

  it("차단은 확정이 아니다 — 결과 요약·확정 증거가 없고 확정 버튼은 그대로 있다", async () => {
    await attemptBlocked();
    expect(screen.queryByRole("region", { name: "확정 결과" })).not.toBeInTheDocument();
    expect(screen.queryByText("확정 증거 요약")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "수주 확정" })).toBeInTheDocument();
  });

  it("차단 뒤 게이트 판정을 다시 읽는다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => blockedResponse()]]);
    await confirmButton();
    const before = gatesGets(view.calls).length;
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    await waitFor(() => expect(gatesGets(view.calls).length).toBeGreaterThan(before));
  });

  it("모양이 깨진 409(blocked_gates 없음)는 일반 문구로 다이얼로그에 보인다", async () => {
    mount([[CONFIRM_URL, "POST", () => apiError("TRADE_CHAIN.CONFIRM.GATE_BLOCKED", 409, {})]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("해소되지 않은 게이트가 있어 수주를 확정하지 못했습니다");
    expect(screen.queryByRole("region", { name: "확정 차단 결과" })).not.toBeInTheDocument();
  });

  it("blocked_gates가 비어 있어도 터지지 않고 안내한다", async () => {
    const { region } = await attemptBlocked({ blocked_gates: [] });
    expect(region).toHaveTextContent("자세한 내용을 받지 못했습니다");
  });
});

describe("ConfirmPanel — 승인 요청 버튼 조건(서버 resolution·settlement 기준)", () => {
  it("GET에서 CREDIT이 APPROVAL·미해소면 '승인 요청 올리기'가 보인다(확정 시도 전에도)", async () => {
    mount();
    const section = await screen.findByRole("region", { name: "승인 요청" });
    expect(within(section).getByRole("button", { name: "승인 요청 올리기" })).toBeInTheDocument();
  });

  it("CREDIT이 통과·해소됨이면 승인 요청 영역이 없다", async () => {
    mount([], { gates: report([{ ...CREDIT_ROW, level: "PASS", settlement: "NOT_REQUIRED" }]) });
    await confirmButton();
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /승인 요청 올리기|승인 다시 요청/ })).not.toBeInTheDocument();
  });

  it("CREDIT이 이미 승인으로 해소(settlement=APPROVED)면 해소 수단이 APPROVAL이어도 버튼이 없다", async () => {
    mount([], { gates: report([{ ...CREDIT_ROW, settlement: "APPROVED" }]) });
    await confirmButton();
    expect(screen.queryByRole("button", { name: /승인 요청 올리기/ })).not.toBeInTheDocument();
  });

  it("CREDIT 해소 수단이 APPROVAL이 아니면(평가 불능=NONE) 승인 요청 버튼이 없다", async () => {
    mount([], { gates: report([{ ...CREDIT_ROW, level: "UNKNOWN", resolution: "NONE" }]) });
    await confirmButton();
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
  });

  it("무역·관리자가 아니면(VIEWER) 승인 요청 영역이 없다", async () => {
    mount([], { me: VIEWER });
    await screen.findByText(/수주 확정은 무역·관리자만/);
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
  });

  it("접수가 아닌 SO에서는 승인 요청 영역이 없다", async () => {
    mount([], { so: soDetail({ status: "ON_HOLD" }) });
    await screen.findByText(/보류 중인 수주/);
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
  });

  it("차단 응답에서 pending=REQUESTED면 '승인 대기 중' 링크만 있고 요청 버튼은 없다", async () => {
    await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "REQUESTED" });
    const section = screen.getByRole("region", { name: "승인 요청" });
    expect(within(section).getByRole("link", { name: "승인 #61 보기" })).toHaveAttribute("href", "/approvals/61");
    expect(section).toHaveTextContent("결재 대기 중");
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });

  it("차단 응답에서 pending=APPROVED(낡음)면 '승인 다시 요청' 버튼이 있다", async () => {
    await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "APPROVED" });
    const section = screen.getByRole("region", { name: "승인 요청" });
    expect(section).toHaveTextContent("더는 쓸 수 없습니다");
    expect(within(section).getByRole("button", { name: "승인 다시 요청" })).toBeInTheDocument();
  });

  it("차단 응답에서 pending이 없으면(null) 새 요청 버튼", async () => {
    await attemptBlocked({ pending_approval_id: null, pending_approval_status: null });
    expect(within(screen.getByRole("region", { name: "승인 요청" })).getByRole("button", { name: "승인 요청 올리기" })).toBeInTheDocument();
  });

  it("GET 판정이 없을 때도(조회 실패) 차단 응답의 CREDIT(APPROVAL)이면 요청 버튼이 보인다", async () => {
    mount([[GATES_URL, "GET", () => apiError("COMMON.SERVER.INTERNAL", 500)], [CONFIRM_URL, "POST", () => blockedResponse()]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    expect(within(screen.getByRole("region", { name: "승인 요청" })).getByRole("button", { name: "승인 요청 올리기" })).toBeInTheDocument();
  });

  it("승인 요청 영역은 '본인이 요청한 승인은 본인이 결정할 수 없다'를 안내한다", async () => {
    mount();
    expect(await screen.findByRole("region", { name: "승인 요청" })).toHaveTextContent("본인이 요청한 승인은 직접 결정할 수 없습니다");
  });
});

describe("ConfirmPanel — 승인 요청 다이얼로그·성공·오류", () => {
  async function openRequest() {
    fireEvent.click(await screen.findByRole("button", { name: "승인 요청 올리기" }));
    return await screen.findByRole("dialog");
  }

  it("다이얼로그: 요청 설명·같은 내용 중복 안내·변경 시 무효·본인 결정 불가", async () => {
    mount();
    const dialog = await openRequest();
    expect(dialog).toHaveTextContent("결재 자격자의 승인이 필요합니다");
    expect(dialog).toHaveTextContent("이미 있으면 새로 만들지 않고 그 승인을 보여 줍니다");
    expect(dialog).toHaveTextContent("승인은 무효가 되어 다시 요청해야 합니다");
    expect(dialog).toHaveTextContent("본인이 요청한 승인은 직접 결정할 수 없습니다");
  });

  it("성공(201): 본문 {version}·멱등 키·승인 상세 링크·live region·포커스, 쿼리 무효화", async () => {
    const view = mount([[REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut(), 201)]]);
    const spy = vi.spyOn(view.client, "invalidateQueries");
    fireEvent.click(within(await openRequest()).getByRole("button", { name: "승인 요청" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const section = screen.getByRole("region", { name: "승인 요청" });
    expect(posts(view.calls, REQUEST_URL)[0]?.body).toEqual({ version: 3 });
    expect(posts(view.calls, REQUEST_URL)[0]?.headers["Idempotency-Key"]).toBe("key-1");
    expect(within(section).getByRole("link", { name: "승인 #61 보기" })).toHaveAttribute("href", "/approvals/61");
    expect(section).toHaveTextContent("승인을 요청했습니다.");
    expect(section).toHaveTextContent("결재 대기");
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("승인 #61을 요청했습니다");
    await waitFor(() => expect(document.activeElement).toBe(section));
    // 이미 요청했으니 새 요청 버튼은 없다(대기 중).
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain('["approvals"]');
  });

  it("200(created=false): 같은 내용의 진행 중인 승인이 이미 있어 새로 만들지 않았다고 알린다", async () => {
    mount([[REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut({ created: false }), 200)]]);
    fireEvent.click(within(await openRequest()).getByRole("button", { name: "승인 요청" }));
    const section = await screen.findByRole("region", { name: "승인 요청" });
    await waitFor(() => expect(section).toHaveTextContent("이미 있어 새로 만들지 않았습니다"));
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toHaveTextContent("새로 만들지 않았습니다");
  });

  it("낡은 승인 재요청 다이얼로그 제목은 '승인을 다시 요청할까요?'", async () => {
    await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "APPROVED" });
    fireEvent.click(screen.getByRole("button", { name: "승인 다시 요청" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("승인을 다시 요청할까요?");
  });

  it.each([
    ["APPROVALS.LINE.NOT_CONFIGURED", 422, {}, "결재선이 없어"],
    ["APPROVALS.APPROVAL.NO_ELIGIBLE_APPROVER", 422, {}, "결재할 수 있는 사람이 없어"],
    ["COMMON.VALIDATION.INVALID_FIELD", 422, { approval: "초과분이 없어 승인을 요청할 수 없습니다." }, "초과분이 없어 승인을 요청할 수 없습니다."],
    ["COMMON.VALIDATION.INVALID_FIELD", 422, { credit: "미수채권을 조회하지 못해 여신을 평가할 수 없습니다." }, "미수채권을 조회하지 못해"],
    ["COMMON.VALIDATION.INVALID_FIELD", 422, { approval: "RAW_ENGLISH_ONLY" }, "승인을 요청할 수 없는 상태입니다"],
    ["COMMON.CONCURRENCY.VERSION_CONFLICT", 409, {}, "먼저 수정되었거나"],
    ["COMMON.CONCURRENCY.LOCK_BUSY", 409, {}, "잠시 후 같은 버튼을 다시 눌러"],
    ["TRADE_DOCS.TRANSITION.NOT_ALLOWED", 409, {}, "접수 상태가 아닌 수주"],
    ["COMMON.IDEMPOTENCY.KEY_CONFLICT", 409, {}, "같은 요청 키로 다른 내용"],
    ["APPROVALS.APPROVAL.ALREADY_ACTIVE", 409, {}, "이미 진행 중인 승인"],
    ["COMMON.SERVER.INTERNAL", 500, {}, "승인을 요청하지 못했습니다"],
  ])("승인 요청 오류 %s(%s) 한국어화, 영문 코드·detail 비노출", async (code, status, detail, text) => {
    mount([[REQUEST_URL, "POST", () => apiError(code, status, { internal_code: "RAW_DETAIL_CODE", ...detail })]]);
    const dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(text);
    expect(alert.textContent).not.toMatch(/RAW_DETAIL_CODE|Internal raw message|APPROVALS\.|COMMON\./);
  });

  it("승인 요청 409에는 '최신 내용 불러오기'가 있고 누르면 닫고 상위 onReload를 부른다", async () => {
    const onReload = vi.fn();
    mount([[REQUEST_URL, "POST", () => apiError("COMMON.CONCURRENCY.VERSION_CONFLICT", 409)]], { onReload });
    const dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("승인 요청 422에는 '최신 내용 불러오기'를 두지 않는다(409 계열만)", async () => {
    mount([[REQUEST_URL, "POST", () => apiError("APPROVALS.LINE.NOT_CONFIGURED", 422)]]);
    const dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await within(dialog).findByRole("alert");
    expect(within(dialog).queryByRole("button", { name: "최신 내용 불러오기" })).not.toBeInTheDocument();
  });

  it("승인 요청 403(라우트)이면 승인·확정 버튼을 숨긴다", async () => {
    mount([[REQUEST_URL, "POST", () => apiError("COMMON.AUTH.FORBIDDEN", 403)]]);
    const dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "승인 요청 올리기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
  });

  it("더블클릭해도 승인 요청 POST는 1건", async () => {
    const view = mount([[REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut(), 201)]]);
    const dialog = await openRequest();
    const button = within(dialog).getByRole("button", { name: "승인 요청" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(1));
  });

  it("승인 요청 중 Esc·닫기는 무시되고 응답 뒤 오류가 보이며 같은 키로 재시도된다(잠금 해제)", async () => {
    const waiters: Array<(r: Response) => void> = [];
    const view = mount([[REQUEST_URL, "POST", () => new Promise<Response>((resolve) => waiters.push(resolve))]]);
    const dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(1));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(within(dialog).getByRole("button", { name: "닫기" })).toBeDisabled();
    await act(async () => {
      waiters[0]?.(apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409));
    });
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(2));
    expect(posts(view.calls, REQUEST_URL).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-1", "key-1"]);
  });

  it("승인 요청 키: 같은 version 재시도=같은 키, version이 오르면 새 키, 성공 뒤엔 Map을 비워 새 키", async () => {
    let ok = false;
    const view = mount([[REQUEST_URL, "POST", () => (ok ? jsonResponse(approvalRequestOut({ created: false }), 200) : apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409))]]);
    let dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    // version 상승(데이터 수정) → 새 키
    view.setProps({ version: 4 });
    dialog = await openRequest();
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await within(dialog).findByRole("alert");
    expect(posts(view.calls, REQUEST_URL).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-1", "key-2"]);
    ok = true;
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts(view.calls, REQUEST_URL)[2]?.headers["Idempotency-Key"]).toBe("key-2");
  });
});

describe("ConfirmPanel — 멱등 키(SO id, version)당 1개", () => {
  it("같은 version의 재시도(차단 뒤 다시 확정)는 같은 키를 쓴다 — 거부는 키를 소비하지 않는다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => blockedResponse()]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    fireEvent.click(await confirmButton());
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(2));
    expect(posts(view.calls, CONFIRM_URL).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-1", "key-1"]);
  });

  it("version이 오른 뒤(데이터 수정)에는 새 키와 새 version을 보낸다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => blockedResponse()]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    view.setProps({ version: 4 });
    fireEvent.click(await confirmButton());
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(2));
    const sent = posts(view.calls, CONFIRM_URL);
    expect(sent.map((c) => c.body)).toEqual([{ version: 3 }, { version: 4 }]);
    expect(sent[0]?.headers["Idempotency-Key"]).not.toBe(sent[1]?.headers["Idempotency-Key"]);
  });

  it("성공하면 키 Map을 비운다 — 같은 version에서 다음 확정 요청은 새 키", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await screen.findByRole("region", { name: "확정 결과" });
    fireEvent.click(await confirmButton());
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(2));
    expect(posts(view.calls, CONFIRM_URL).map((c) => c.headers["Idempotency-Key"])).toEqual(["key-1", "key-2"]);
  });

  it("crypto.randomUUID가 던져도 폴백 키로 요청한다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    const dialog = await openConfirm();
    vi.stubGlobal("crypto", {
      randomUUID: () => {
        throw new Error("비보안 컨텍스트");
      },
    });
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1));
    expect(posts(view.calls, CONFIRM_URL)[0]?.headers["Idempotency-Key"]).toMatch(/^gate-/);
  });
});

describe("ConfirmPanel — 더블클릭·요청 중·창 포커스·오프라인", () => {
  it("더블클릭해도 확정 POST는 1건", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    const button = dialogConfirm(await openConfirm());
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1));
  });

  it("요청 중에는 Esc·닫기로 닫히지 않고, 응답(오류) 뒤 잠금이 풀려 다시 보낼 수 있다", async () => {
    const waiters: Array<(r: Response) => void> = [];
    const view = mount([[CONFIRM_URL, "POST", () => new Promise<Response>((resolve) => waiters.push(resolve))]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(within(dialog).getByRole("button", { name: "닫기" })).toBeDisabled();
    await act(async () => {
      waiters[0]?.(apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409));
    });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("같은 거래처의 다른 건을 처리 중");
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(2));
  });

  it("오류 표시 중 Esc는 다이얼로그를 닫고 reset한다(요청 중이 아닐 때)", async () => {
    mount([[CONFIRM_URL, "POST", () => apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409)]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await within(dialog).findByRole("alert");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("창 포커스 재조회가 와도 열린 확정 다이얼로그는 닫히지 않는다(판정이 바뀌어도)", async () => {
    const view = mount();
    const dialog = await openConfirm();
    server.rep = report([{ ...CREDIT_ROW, level: "PASS", settlement: "NOT_REQUIRED" }], { clearance: { cleared: true, needs_approval: false, unresolved_count: 0 } });
    const before = gatesGets(view.calls).length;
    refetchViaFocus();
    await waitFor(() => expect(gatesGets(view.calls).length).toBeGreaterThan(before));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.getByRole("dialog")).toBe(dialog);
  });

  it("창 포커스 재조회가 와도 열린 승인 요청 다이얼로그는 닫히지 않는다", async () => {
    const view = mount();
    fireEvent.click(await screen.findByRole("button", { name: "승인 요청 올리기" }));
    const dialog = await screen.findByRole("dialog");
    server.rep = report([{ ...CREDIT_ROW, settlement: "APPROVED" }]);
    const before = gatesGets(view.calls).length;
    refetchViaFocus();
    await waitFor(() => expect(gatesGets(view.calls).length).toBeGreaterThan(before));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.getByRole("dialog")).toBe(dialog);
  });

  it("창 포커스 재조회가 와도 확정 차단 결과(서버 응답 값)는 지워지지 않는다", async () => {
    const { calls, region } = await attemptBlocked();
    server.rep = report([gate()], { clearance: { cleared: true, needs_approval: false, unresolved_count: 0 } });
    const before = gatesGets(calls).length;
    refetchViaFocus();
    await waitFor(() => expect(gatesGets(calls).length).toBeGreaterThan(before));
    expect(screen.getByRole("region", { name: "확정 차단 결과" })).toBe(region);
  });

  it("오류 표시 중 다른 사람이 확정해 SO가 접수가 아니게 돼도(prop 변경) 오류 안내가 있는 다이얼로그는 닫히지 않는다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => apiError("COMMON.CONCURRENCY.VERSION_CONFLICT", 409)]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await within(dialog).findByRole("alert");
    view.setProps({ so: CONFIRMED_SO });
    expect(screen.getByRole("dialog")).toBe(dialog);
  });

  it("접수가 아니게 되면(유휴 다이얼로그) 확정 다이얼로그를 더 두지 않는다", async () => {
    const view = mount();
    await openConfirm();
    view.setProps({ so: CONFIRMED_SO });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
  });

  it("오프라인 상태여도 요청을 보내 본다(paused로 다이얼로그가 갇히지 않는다)", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => jsonResponse(confirmOut())]]);
    const dialog = await openConfirm();
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(view.calls, CONFIRM_URL)).toHaveLength(1));
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
  });

  it("오프라인 상태의 승인 요청도 보낸다", async () => {
    const view = mount([[REQUEST_URL, "POST", () => jsonResponse(approvalRequestOut(), 201)]]);
    fireEvent.click(await screen.findByRole("button", { name: "승인 요청 올리기" }));
    const dialog = await screen.findByRole("dialog");
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "승인 요청" }));
    await waitFor(() => expect(posts(view.calls, REQUEST_URL)).toHaveLength(1));
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
  });
});

describe("ConfirmPanel — 확정 오류 한국어화", () => {
  async function failWith(code: string, status: number, detail?: Record<string, unknown>, opts: Opts = {}) {
    const view = mount([[CONFIRM_URL, "POST", () => apiError(code, status, detail)]], opts);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    const alert = await within(dialog).findByRole("alert");
    return { ...view, dialog, alert };
  }

  it("VERSION_CONFLICT: 거래처 다른 수주 경합까지 설명하는 전용 문구 + 최신 내용 불러오기", async () => {
    const { alert, dialog } = await failWith("COMMON.CONCURRENCY.VERSION_CONFLICT", 409);
    expect(alert).toHaveTextContent("같은 거래처의 다른 수주가 바뀌어(여신 판정 경합)");
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
    expect(alert.textContent).not.toMatch(/RAW_DETAIL_CODE|Internal raw message|COMMON\./);
  });

  it("KEY_CONFLICT: 같은 키 다른 내용 안내 + 최신 내용 불러오기", async () => {
    const { alert, dialog } = await failWith("COMMON.IDEMPOTENCY.KEY_CONFLICT", 409);
    expect(alert).toHaveTextContent("같은 요청 키로 다른 내용이 이미 처리되었습니다");
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("LOCK_BUSY: 잠시 후 같은 버튼 재시도(중복 확정 안 됨) 안내", async () => {
    const { alert } = await failWith("COMMON.CONCURRENCY.LOCK_BUSY", 409);
    expect(alert).toHaveTextContent("잠시 후 같은 버튼을 다시 눌러 주세요");
    expect(alert).toHaveTextContent("중복 확정되지 않습니다");
  });

  it("TRANSITION.NOT_ALLOWED·APPROVAL.STALE(409)·알 수 없는 409는 각 문구 + 최신 내용 불러오기", async () => {
    for (const [code, text] of [
      ["TRADE_DOCS.TRANSITION.NOT_ALLOWED", "접수 상태가 아닌 수주는 확정할 수 없습니다"],
      ["APPROVALS.APPROVAL.STALE", "확정 직전에 승인이 무효가 되었습니다"],
      ["SOMETHING.NEW.CONFLICT", "처리 중 충돌이 발생했습니다"],
    ] as const) {
      const { alert, dialog, unmount } = await failWith(code, 409);
      expect(alert).toHaveTextContent(text);
      expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
      unmount();
    }
  });

  it("APPROVAL.REQUIRED(422)는 승인 요청 안내 + 최신 내용 불러오기", async () => {
    const { alert, dialog } = await failWith("APPROVALS.APPROVAL.REQUIRED", 422);
    expect(alert).toHaveTextContent("승인을 요청하고 결재가 끝난 뒤 다시 확정해 주세요");
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("DOCUMENT.INCOMPLETE(422): 서버가 준 누락 항목 키를 한국어 이름으로 나열(영문 키 비노출)", async () => {
    const { alert, dialog } = await failWith("TRADE_DOCS.DOCUMENT.INCOMPLETE", 422, { fx_rate: "환율을 입력해 주세요.", lines: "라인을 1개 이상 추가해 주세요.", payment_terms: "x" });
    expect(alert).toHaveTextContent("결제조건, 환율, 라인(1개 이상)");
    expect(alert.textContent).not.toMatch(/fx_rate|payment_terms|lines/);
    expect(within(dialog).queryByRole("button", { name: "최신 내용 불러오기" })).not.toBeInTheDocument();
  });

  it("422(검증)·404·5xx는 일반 한국어 문구 — 영문 message·detail 비노출", async () => {
    for (const [code, status, text] of [
      ["COMMON.VALIDATION.INVALID_FIELD", 422, "확정 요청의 값이 올바르지 않습니다"],
      ["COMMON.NOT_FOUND", 404, "수주를 찾을 수 없습니다"],
      ["COMMON.SERVER.INTERNAL", 500, "수주를 확정하지 못했습니다"],
    ] as const) {
      const { alert, unmount } = await failWith(code, status);
      expect(alert).toHaveTextContent(text);
      expect(alert.textContent).not.toMatch(/RAW_DETAIL_CODE|Internal raw message/);
      unmount();
    }
  });

  it("네트워크 오류(서버 한국어 메시지)는 그대로 보인다", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string) => (input === "/api/v1/auth/me" ? Promise.resolve(jsonResponse(TRADER)) : input.endsWith("/gates") ? Promise.resolve(jsonResponse(server.rep)) : Promise.reject(new Error("net")))));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ConfirmPanel so={soDetail()} version={3} onReload={() => undefined} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("서버에 연결할 수 없습니다");
  });

  it("403(라우트 역할 게이트)이면 확정·승인 요청 버튼을 숨기고 같은 화면에서 복구하지 않는다", async () => {
    mount([[CONFIRM_URL, "POST", () => apiError("COMMON.AUTH.FORBIDDEN", 403)]]);
    fireEvent.click(dialogConfirm(await openConfirm()));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
    expect(screen.getByText(/수주 확정은 무역·관리자만 할 수 있습니다/)).toBeInTheDocument();
  });

  it("'최신 내용 불러오기'는 다이얼로그를 닫고 상위 onReload를 부른다", async () => {
    const onReload = vi.fn();
    const { dialog } = await failWith("COMMON.CONCURRENCY.VERSION_CONFLICT", 409, undefined, { onReload });
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("실패(5xx) 뒤 게이트 판정을 다시 읽는다", async () => {
    const view = mount([[CONFIRM_URL, "POST", () => apiError("COMMON.SERVER.INTERNAL", 500)]]);
    const dialog = await openConfirm();
    const before = gatesGets(view.calls).length;
    fireEvent.click(dialogConfirm(dialog));
    await within(dialog).findByRole("alert");
    await waitFor(() => expect(gatesGets(view.calls).length).toBeGreaterThan(before));
    expect(screen.getByRole("dialog")).toBe(dialog);
  });
});

describe("CreditEvaluationCard — 서버 값만 표시(산술·재판정 없음)", () => {
  const card = async () => await screen.findByRole("region", { name: "여신 평가" });

  it("서버가 금액 문자열(*_text)을 주지 않으면 금액을 표시하지 않고 승인 요청 안내를 한다(정수 원값 비노출)", async () => {
    mount();
    const el = await card();
    expect(el.textContent).not.toMatch(/123456789|98765432|555555555|424242424|7500/);
    expect(el).toHaveTextContent("금액은 이 화면에 표시하지 않습니다");
    expect(el).toHaveTextContent("승인을 요청하면 승인 상세에서");
    expect(el).toHaveTextContent("한도 초과");
  });

  it("승인 id를 알면 승인 상세 링크로 안내한다", async () => {
    await attemptBlocked({ pending_approval_id: 61, pending_approval_status: "REQUESTED" });
    const el = screen.getByRole("region", { name: "여신 평가" });
    expect(within(el).getByRole("link", { name: "승인 #61 상세" })).toHaveAttribute("href", "/approvals/61");
  });

  it("서버가 *_text를 주면 그 문자열 그대로(자릿수 계산 없이) 보인다", async () => {
    const basis = { ...CREDIT_BASIS, limit_amount_text: "1,234,567.89", excess_amount_text: "4,242,424.24" };
    mount([], { gates: report([{ ...CREDIT_ROW, basis }]) });
    const el = await card();
    expect(within(el).getByText("1,234,567.89")).toBeInTheDocument();
    expect(within(el).getByText("4,242,424.24")).toBeInTheDocument();
    expect(el).not.toHaveTextContent("금액은 이 화면에 표시하지 않습니다");
    expect(el.textContent).not.toMatch(/123456789|424242424/);
  });

  it("'미수 미반영' 경고: receivables_reflected가 true가 아니면(키 부재 포함) 경고, true면 없음", async () => {
    const first = mount();
    expect(await within(await card()).findByText("미수 미반영")).toBeInTheDocument();
    first.unmount();
    const { receivables_reflected: _omit, ...withoutKey } = CREDIT_BASIS;
    void _omit;
    const second = mount([], { gates: report([{ ...CREDIT_ROW, basis: withoutKey }]) });
    expect(await within(await card()).findByText("미수 미반영")).toBeInTheDocument();
    second.unmount();
    mount([], { gates: report([{ ...CREDIT_ROW, basis: { ...CREDIT_BASIS, receivables_reflected: true } }]) });
    await card();
    expect(screen.queryByText("미수 미반영")).not.toBeInTheDocument();
  });

  it("노출 일부만 반영·평가 불능·사유 코드를 한국어로(영문 코드 비노출)", async () => {
    const basis = { ...CREDIT_BASIS, verdict: "UNEVALUABLE", exposure_is_partial: true, reason_codes: "CURRENCY_NOT_CONVERTIBLE,RECEIVABLE_PROVIDER_ERROR" };
    mount([], { gates: report([{ ...CREDIT_ROW, level: "UNKNOWN", resolution: "NONE", basis }]) });
    const el = await card();
    expect(within(el).getByText("노출 일부만 반영")).toBeInTheDocument();
    expect(within(el).getByText("평가 불능", { selector: "span" })).toBeInTheDocument();
    expect(el).toHaveTextContent("다른 통화의 수주를 환산하지 못해");
    expect(el).toHaveTextContent("미수금 조회에 실패해");
    expect(el.textContent).not.toMatch(/CURRENCY_NOT_CONVERTIBLE|UNEVALUABLE|UNKNOWN/);
  });

  it("마스킹 역할(basis {}·basis_hash 빈 문자열): 수치 비공개 안내만 — 미수 경고·금액 안내를 만들지 않는다", async () => {
    mount([], { me: VIEWER, so: soDetail(), gates: report([{ ...CREDIT_ROW, basis: {}, basis_hash: "" }]) });
    await screen.findByText(/수주 확정은 무역·관리자만/);
    // VIEWER는 카드를 볼 수 있다(열람 전용) — 수치만 가려진다.
    const el = await card();
    expect(el).toHaveTextContent("여신 수치(한도·노출)는 무역·관리자에게만 표시됩니다");
    expect(el.textContent).not.toMatch(/미수 미반영|금액은 이 화면에 표시하지 않습니다/);
  });

  it("확정 시도 뒤에는 차단 응답의 basis를 쓰고 출처를 '확정을 시도한 시점'으로 표시한다", async () => {
    mount([[CONFIRM_URL, "POST", () => blockedResponse({ blocked_gates: [blockedGate({ basis: { ...CREDIT_BASIS, advisory: false, limit_amount_text: "9,999.00" } })] })]]);
    expect(await card()).toHaveTextContent("방금 조회한 참고 평가입니다");
    fireEvent.click(dialogConfirm(await openConfirm()));
    await blockedRegion();
    const el = screen.getByRole("region", { name: "여신 평가" });
    expect(el).toHaveTextContent("확정을 시도한 시점에 서버가 평가한 값입니다");
    expect(within(el).getByText("9,999.00")).toBeInTheDocument();
  });

  it("CREDIT 행이 없으면 카드를 만들지 않는다", async () => {
    mount([], { gates: report([gate()]) });
    await confirmButton();
    expect(screen.queryByRole("region", { name: "여신 평가" })).not.toBeInTheDocument();
  });
});

describe("ConfirmPanel — 확정된 SO(읽기 전용·증거 요약)", () => {
  it("확정 SO: 버튼·승인 요청 없이 읽기 전용 안내와 서버 증적 3열·증거 번호", async () => {
    mount([], { so: CONFIRMED_SO });
    const evidence = (await screen.findByText("확정 증거 요약")).closest("section") as HTMLElement;
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "승인 요청" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "여신 평가" })).not.toBeInTheDocument();
    expect(evidence).toHaveTextContent("읽기 전용");
    expect(evidence).toHaveTextContent("동결되어 고칠 수 없고");
    expect(evidence).toHaveTextContent("여신 초과 승인을 사용해 확정");
    expect(within(evidence).getByRole("link", { name: "승인 #33" })).toHaveAttribute("href", "/approvals/33");
    expect(evidence).toHaveTextContent("예외 승인을 사용해 확정");
    expect(evidence).toHaveTextContent("#77");
    expect(evidence).toHaveTextContent("2026");
    expect(evidence.textContent).not.toMatch(/APPROVED|OVERRIDDEN|WITHIN_LIMIT/);
  });

  it.each([
    ["WITHIN_LIMIT", "PASS", "한도 이내로 확정", "통과"],
    ["NOT_MANAGED", "NOT_APPLICABLE", "여신 관리 대상 아님", "해당 없음"],
    ["WITHIN_LIMIT", "WARN", "한도 이내로 확정", "경고 상태로 확정"],
    ["WITHIN_LIMIT", "SKIPPED_OFF", "한도 이내로 확정", "게이트 꺼짐(미적용)"],
    ["NEW_VERDICT", "NEW_PI", "확인 불가", "확인 불가"],
  ])("증적 라벨: 여신 %s·PI %s → 한국어(모르는 값은 확인 불가)", async (credit, pi, creditText, piText) => {
    mount([], { so: { ...CONFIRMED_SO, credit_verdict: credit, credit_approval_id: null, pi_gate_verdict: pi, confirm_evaluation_id: null } });
    const evidence = (await screen.findByText("확정 증거 요약")).closest("section") as HTMLElement;
    expect(evidence).toHaveTextContent(creditText);
    expect(evidence).toHaveTextContent(piText);
    expect(within(evidence).queryByRole("link")).not.toBeInTheDocument();
    expect(evidence).toHaveTextContent("—");
  });

  it("확정 후 보류(ON_HOLD, confirmed_at 있음)여도 증거 요약은 유지된다", async () => {
    mount([], { so: { ...CONFIRMED_SO, status: "ON_HOLD" } });
    expect(await screen.findByText("확정 증거 요약")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수주 확정" })).not.toBeInTheDocument();
  });

  it("접수 SO에는 증거 요약이 없다", async () => {
    mount();
    await confirmButton();
    expect(screen.queryByText("확정 증거 요약")).not.toBeInTheDocument();
  });

  it("마스킹 역할(VIEWER)도 서버가 준 증적 요약은 읽을 수 있다", async () => {
    mount([], { so: CONFIRMED_SO, me: VIEWER });
    expect(await screen.findByText("확정 증거 요약")).toBeInTheDocument();
  });
});

describe("확정 오류·차단 파서(단위)", () => {
  const blockedError = (detail: Record<string, unknown>) => new ApiError(409, { code: "TRADE_CHAIN.CONFIRM.GATE_BLOCKED", message: "x", detail, requestId: null });

  it("parseBlockedDetail: 다른 코드·비배열은 null, 모양이 이상한 항목은 건너뛴다", () => {
    expect(parseBlockedDetail(new Error("x"))).toBeNull();
    expect(parseBlockedDetail(new ApiError(409, { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "x", detail: { blocked_gates: [] }, requestId: null }))).toBeNull();
    expect(parseBlockedDetail(blockedError({ blocked_gates: "nope" }))).toBeNull();
    const parsed = parseBlockedDetail(blockedError({ blocked_gates: [null, { gate_code: 5 }, { gate_code: "MOQ", level: "BLOCK", can_override: true }], pending_approval_id: "7" }));
    expect(parsed?.blocked_gates).toHaveLength(1);
    expect(parsed?.blocked_gates[0]).toMatchObject({ gate_code: "MOQ", can_override: true, basis: {}, override_roles: [] });
    expect(parsed?.pending_approval_id).toBeNull();
  });

  it("에러 문구 함수는 ApiError가 아니면 기본 문구", () => {
    expect(confirmErrorMessage(new Error("x"))).toBe("수주를 확정하지 못했습니다.");
    expect(approvalRequestErrorMessage(new Error("x"))).toBe("승인을 요청하지 못했습니다.");
  });
});

describe("접근성", () => {
  it("패널은 제목으로 이름 붙은 section이고 결과 영역은 포커스 가능(tabIndex=-1)한 region이다", async () => {
    mount([[CONFIRM_URL, "POST", () => blockedResponse()]]);
    expect(await screen.findByRole("region", { name: "수주 확정" })).toBeInTheDocument();
    fireEvent.click(dialogConfirm(await openConfirm()));
    const region = await blockedRegion();
    expect(region).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("status", { name: "확정 처리 결과" })).toBeInTheDocument();
  });
});
