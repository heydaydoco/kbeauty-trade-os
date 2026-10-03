// 오더 인테이크 상세·검토 — 표시(mapping_state 3종·po_occupied·마스킹)·게이트 요약(서버 값 그대로)·편집 PATCH 본문·품번 재해석·품번 등록 유도·거부·확정 (S3-1 PR-13b).
// ★ 프런트가 하드 게이트·확정 가능 여부를 다시 판정하지 않는다: intake_confirmable=false여도 확정은 서버를 부르고, 서버의 422/409를 한국어로 안내한다.
// fetch 스텁은 정확 URL·메서드 일치(`stubGateFetch`) — 오라우팅은 404로 터진다.

import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { apiError, intakeDetail, intakeGate, intakeGateReport, intakeLine } from "../test/intake-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import type { IntakeDetail, IntakeGateReport } from "../lib/order-intake";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ID = "/v1/order-intakes/21";
const GATES = "/v1/order-intakes/21/gates";
const RESOLVE = "/v1/order-intakes/21/resolve";
const CONFIRM = "/v1/order-intakes/21/confirm";
const REJECT = "/v1/order-intakes/21/reject";
const ITEM_CODES = "/v1/partners/3/item-codes";

/** 서버 상태 — 테스트가 바꾸면 다음 GET에 반영된다. */
const server: { detail: IntakeDetail; gates: IntakeGateReport } = { detail: intakeDetail(), gates: intakeGateReport() };

interface Opts {
  me?: unknown;
  detail?: IntakeDetail;
  gates?: IntakeGateReport;
}

function open(extra: GateHandler[] = [], opts: Opts = {}) {
  server.detail = opts.detail ?? intakeDetail();
  server.gates = opts.gates ?? intakeGateReport();
  const stub = stubGateFetch(opts.me ?? TRADER, [
    ...extra,
    [ID, "GET", () => jsonResponse(server.detail)],
    [GATES, "GET", () => jsonResponse(server.gates)],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }, { id: 2, display_name: "다른 담당" }]))],
    ["/v1/markets?size=200", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국", name_en: null, note: null, version: 1 }, { id: 2, code: "JP", name_ko: "일본", name_en: null, note: null, version: 1 }]))],
    ["/v1/skus?size=20", "GET", () => jsonResponse(page([{ id: 8, sku_code: "SKU-008", name_ko: "진정 크림" }]))],
    ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/order-intakes", "GET", () => jsonResponse(page([]))],
  ]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = renderWithProviders(<AppRoutes />, { route: "/orders/intakes/21", client });
  return { ...stub, ...view, client };
}

const posts = (calls: GateCall[], path: string) => calls.filter((c) => c.method === "POST" && c.url === `/api${path}`);
const patches = (calls: GateCall[]) => calls.filter((c) => c.method === "PATCH" && c.url === `/api${ID}`);
const gateGets = (calls: GateCall[]) => calls.filter((c) => c.method === "GET" && c.url === `/api${GATES}`);
const detailGets = (calls: GateCall[]) => calls.filter((c) => c.method === "GET" && c.url === `/api${ID}`);
const refetchViaFocus = () =>
  act(() => {
    window.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });

/** 게이트 요약의 판정 글자(strong) — 부분 일치('통과'⊂'통과하지 못함')로 공회전하지 않게 정확히 집는다. */
const verdict = async () => (await screen.findByText(/서버 사전 점검/)).querySelector("strong") as HTMLElement;
const ready = async () => await screen.findByRole("heading", { name: /PO PO-2026-001/ });
const openConfirm = async () => {
  await ready();
  fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
  return await screen.findByRole("dialog");
};
const dialogConfirm = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: "접수 확정" });
const openReject = async () => {
  await ready();
  fireEvent.click(screen.getByRole("button", { name: "거부" }));
  return await screen.findByRole("dialog");
};
const typeReason = (dialog: HTMLElement, value: string) => fireEvent.change(within(dialog).getByRole("textbox"), { target: { value } });
const confirmed = (over: Partial<IntakeDetail> = {}) => {
  const intake = intakeDetail({ status: "CONFIRMED", version: 3, sales_order_id: 77, ...over });
  return jsonResponse({ intake_id: 21, sales_order_id: 77, doc_number: "SO-2026-0077", intake }, 201);
};

describe("표시 — 서버 값 그대로", () => {
  it("헤더·접수 내용·라인(매핑됨)·합계는 서버 문자열 그대로, 원 단위 정수 금액은 찍지 않는다", async () => {
    open();
    await ready();
    expect(screen.getByText("대기")).toBeInTheDocument();
    expect(screen.getByText(/ABC Trading · 수동 등록/)).toBeInTheDocument();
    expect(screen.getByText("125.00 USD")).toBeInTheDocument();
    expect(screen.getByText("12.50 USD")).toBeInTheDocument();
    expect(screen.getByText("매핑됨")).toBeInTheDocument();
    expect(screen.getByText("SKU-001")).toBeInTheDocument();
    expect(screen.getByText("활성")).toBeInTheDocument();
    expect(screen.queryByText("12500")).not.toBeInTheDocument();
    expect(screen.queryByText("1250")).not.toBeInTheDocument();
    // 담당자 이름은 담당자 목록 조회가 끝나면 이름으로.
    expect((await screen.findAllByText("무역 담당")).length).toBeGreaterThan(0);
  });

  it("매핑 상태 3종 — 미매핑·재해석 필요·매핑됨 배지와 해소 안내(품번 등록 유도·자동 추종 없음)", async () => {
    open([], {
      detail: intakeDetail({
        lines: [
          intakeLine({ id: 1, line_no: 1, buyer_item_code: "OK-1" }),
          intakeLine({ id: 2, line_no: 2, buyer_item_code: "NEW-2", sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" }),
          intakeLine({ id: 3, line_no: 3, buyer_item_code: "OLD-3", mapping_state: "STALE", sku_id: 9, sku_code: "SKU-009" }),
        ],
      }),
    });
    await ready();
    const table = within(screen.getByRole("table", { name: "인테이크 라인과 품번 매핑 상태" }));
    expect(table.getByText("매핑됨")).toBeInTheDocument();
    expect(table.getByText("미매핑")).toHaveClass("bg-signal-red");
    expect(table.getByText("재해석 필요")).toHaveClass("border-dashed");
    expect(table.getByText(/SKU를 연결한 뒤 '품번 다시 확인'/)).toBeInTheDocument();
    expect(table.getByText(/자동으로 따라가지 않습니다/)).toBeInTheDocument();
    // 매핑됨 라인에는 안내가 없다.
    expect(table.getAllByText(/품번 다시 확인/)).toHaveLength(2);
  });

  it("미매핑 라인이 있으면 품번 등록 유도(거래처 화면·SKU 목록 링크)를 보인다", async () => {
    open([], { detail: intakeDetail({ lines: [intakeLine({ sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" })] }) });
    await ready();
    expect(screen.getByText("품번 등록 — 미매핑 라인 1개")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "거래처 화면의 품번 매핑" })).toHaveAttribute("href", "/partners");
    expect(screen.getByRole("link", { name: "SKU 목록" })).toHaveAttribute("href", "/skus");
    expect(screen.getByRole("combobox", { name: "라인 1 연결할 SKU" })).toBeInTheDocument();
  });

  it("삭제된 SKU로 저장된 라인은 SKU 상태를 '삭제됨'으로 보인다(서버 sku_status 그대로)", async () => {
    open([], { detail: intakeDetail({ lines: [intakeLine({ sku_status: "DELETED", mapping_state: "UNMAPPED" })] }) });
    await ready();
    expect(screen.getByText("삭제됨")).toBeInTheDocument();
  });

  it("po_occupied(막다른 대기) — 서버가 준 번호·상태와 해소 방법을 안내한다", async () => {
    open([], { detail: intakeDetail({ po_occupied: { kind: "SALES_ORDER", doc_number: "SO-2026-0009", status: "RECEIVED" } }) });
    await ready();
    const alert = screen.getByText(/막다른 대기/).closest("div") as HTMLElement;
    expect(alert).toHaveTextContent("수주 SO-2026-0009 (접수)가 같은 바이어 PO번호를 점유 중");
    expect(alert).toHaveTextContent("기존 수주를 취소하거나");
  });

  it("po_occupied의 번호가 가려진 역할 — 번호를 지어내지 않는다", async () => {
    open([], { me: VIEWER, detail: intakeDetail({ po_occupied: { kind: "SALES_ORDER", doc_number: null, status: null } }) });
    await ready();
    expect(screen.getByText(/문서 번호는 무역·관리자만 확인/)).toBeInTheDocument();
    expect(screen.queryByText(/SO-\d/)).not.toBeInTheDocument();
  });

  it("po_occupied가 없으면 막다른 대기 배너가 없다(프런트가 점유를 추정하지 않는다)", async () => {
    open();
    await ready();
    expect(screen.queryByText(/막다른 대기/)).not.toBeInTheDocument();
  });

  it("접수 원본(불변)을 접어 두고 최초 제출 값을 나란히 볼 수 있다", async () => {
    open([], { detail: intakeDetail({ lines: [intakeLine({ quantity: 99, unit_price_text: "99.00" })] }) });
    await ready();
    const details = screen.getByText(/접수 원본 보기/).closest("details") as HTMLElement;
    expect(within(details).getByText("10")).toBeInTheDocument();
    expect(within(details).getByText("12.50")).toBeInTheDocument();
    expect(within(details).getByText("ABC-1")).toBeInTheDocument();
  });

  it("복제 원본·확정 수주 링크, 거부 사유(서버가 줄 때만)", async () => {
    open([], { detail: intakeDetail({ status: "REJECTED", copied_from_so_id: 12, reject_reason: "중복 접수라 거부", decided_at: "2026-09-30T02:00:00Z" }) });
    await ready();
    expect(screen.getByRole("link", { name: "수주 #12" })).toHaveAttribute("href", "/sales-orders/12");
    expect(screen.getByText("중복 접수라 거부")).toBeInTheDocument();
  });

  it("거부 사유가 가려진 역할 — 안내만(null을 빈칸·'없음'으로 오독하지 않는다)", async () => {
    open([], { me: VIEWER, detail: intakeDetail({ status: "REJECTED", reject_reason: null }) });
    await ready();
    expect(screen.getByText("거부 사유는 무역·관리자만 볼 수 있습니다.")).toBeInTheDocument();
  });

  it("없는 인테이크(404) — 한국어 안내와 목록 링크", async () => {
    stubGateFetch(TRADER, [[ID, "GET", () => apiError("COMMON.NOT_FOUND", 404, "Not Found")]]);
    renderWithProviders(<AppRoutes />, { route: "/orders/intakes/21" });
    expect(await screen.findByRole("alert")).toHaveTextContent("인테이크를 찾을 수 없습니다.");
    expect(screen.getByRole("link", { name: "인테이크 목록으로" })).toBeInTheDocument();
  });
});

describe("게이트 요약 — 서버 값 그대로(intake_confirmable은 사전 점검일 뿐)", () => {
  it("intake_confirmable=true — '통과'이되 확정 가능 보장이 아님을 밝힌다", async () => {
    open();
    expect(await verdict()).toHaveTextContent(/^통과$/);
    expect(screen.getByText(/확정 가능 보장이 아닙니다/)).toBeInTheDocument();
    expect(screen.getByText("품번 매핑")).toBeInTheDocument();
    expect(screen.getByText("품번 매핑이 확인되었습니다.")).toBeInTheDocument();
    expect(screen.getByText(/법적 판정이 아닙니다/)).toBeInTheDocument();
  });

  it("intake_confirmable=false — 게이트 행이 모두 통과여도 서버 값을 따른다(프런트 재계산 금지)", async () => {
    open([], { gates: intakeGateReport({ intake_confirmable: false, gates: [intakeGate()] }) });
    expect(await verdict()).toHaveTextContent(/^통과하지 못함$/);
  });

  it("intake_confirmable=true — 차단 결과 행이 있어도 서버 값을 따른다", async () => {
    open([], { gates: intakeGateReport({ intake_confirmable: true, gates: [intakeGate({ level: "BLOCK", blocks_intake_confirm: true, settlement: "UNRESOLVED", message_ko: "서버 메시지" })] }) });
    expect(await verdict()).toHaveTextContent(/^통과$/);
    expect(screen.getByText("확정을 막는 항목")).toBeInTheDocument();
    expect(screen.getByText("차단")).toBeInTheDocument();
    expect(screen.getByText("미해소")).toBeInTheDocument();
    expect(screen.getByText("서버 메시지")).toBeInTheDocument();
  });

  it("중복 PO 결과의 점유 문서(detail)는 서버가 줄 때만 — 마스킹 역할(detail 비어 있음)은 번호가 없다", async () => {
    const withDetail = intakeGate({ gate_code: "DUPLICATE_PO", level: "BLOCK", blocks_intake_confirm: true, message_ko: "같은 PO가 있습니다.", detail: { other_doc_number: "SO-2026-0003", other_status: "RECEIVED" } });
    open([], { gates: intakeGateReport({ intake_confirmable: false, gates: [withDetail] }) });
    expect(await screen.findByText("점유 문서: SO-2026-0003 (접수)")).toBeInTheDocument();
  });

  it("마스킹 역할 — detail이 비면 점유 문서 줄이 없다", async () => {
    const masked = intakeGate({ gate_code: "DUPLICATE_PO", level: "BLOCK", blocks_intake_confirm: true, message_ko: "같은 PO가 있습니다.", detail: {} });
    open([], { me: VIEWER, gates: intakeGateReport({ intake_confirmable: false, gates: [masked] }) });
    await screen.findByText("같은 PO가 있습니다.");
    expect(screen.queryByText(/점유 문서/)).not.toBeInTheDocument();
  });

  it("비대기(확정·거부) 인테이크는 게이트를 요청하지 않고 안내만 한다", async () => {
    const stub = open([], { detail: intakeDetail({ status: "CONFIRMED", sales_order_id: 5 }) });
    await ready();
    expect(screen.getByText("게이트 요약은 대기 상태의 인테이크에서만 계산합니다.")).toBeInTheDocument();
    expect(gateGets(stub.calls)).toHaveLength(0);
  });

  it("게이트 조회 실패 — 다시 시도 버튼과 '그래도 확정은 서버가 판정' 안내(확정 버튼은 그대로)", async () => {
    open([[GATES, "GET", () => apiError("X", 500, "서버 한국어 오류")]]);
    const alert = await screen.findByText(/그래도 확정은 서버가 다시 판정합니다/);
    expect(alert).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "접수 확정" })).toBeEnabled();
  });
});

describe("접수 확정 — 항상 서버가 최종 판정", () => {
  it("intake_confirmable=false여도 확정 버튼은 활성이고 서버를 부른다(프런트가 막지 않는다)", async () => {
    const stub = open([[CONFIRM, "POST", () => confirmed()]], { gates: intakeGateReport({ intake_confirmable: false, gates: [intakeGate({ level: "BLOCK", blocks_intake_confirm: true, settlement: "UNRESOLVED" })] }) });
    const dialog = await openConfirm();
    expect(dialogConfirm(dialog)).toBeEnabled();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(1));
  });

  it("다이얼로그는 되돌릴 수 없음·수주 접수 생성·서버 재검사·중복 안 됨을 안내한다", async () => {
    open();
    const dialog = await openConfirm();
    expect(dialog).toHaveTextContent("확정하면 되돌릴 수 없습니다.");
    expect(dialog).toHaveTextContent("수주(접수 상태)가 새로 만들어지고");
    expect(dialog).toHaveTextContent("관리자도 우회할 수 없습니다");
    expect(dialog).toHaveTextContent("중복으로 만들어지지 않습니다");
  });

  it("성공 — 본문 {version}·멱등 키, 생성된 수주 링크, live region 안내, 결과 영역 포커스, 쿼리 무효화(인테이크·수주·문서 흐름)", async () => {
    const stub = open([[CONFIRM, "POST", () => confirmed()]]);
    const invalidate = vi.spyOn(stub.client, "invalidateQueries");
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    const region = await screen.findByRole("region", { name: "접수 확정 결과" });
    const [call] = posts(stub.calls, CONFIRM);
    expect(call?.body).toEqual({ version: 2 });
    expect(call?.headers["Idempotency-Key"]).toBe("key-1");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(within(region).getByRole("link", { name: "SO-2026-0077" })).toHaveAttribute("href", "/sales-orders/77");
    expect(screen.getByRole("status", { name: "처리 결과" })).toHaveTextContent("접수를 확정했습니다. 수주 SO-2026-0077이 접수 상태로 만들어졌습니다.");
    await waitFor(() => expect(region).toHaveFocus());
    const keys = invalidate.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toEqual(expect.arrayContaining([JSON.stringify(["order-intakes"]), JSON.stringify(["sales-orders"]), JSON.stringify(["document-flow"])]));
    // 확정 뒤에는 조치 버튼이 사라지고 읽기 전용이 된다.
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument();
  });

  it("더블클릭(같은 틱 두 번)은 1회만 전송한다", async () => {
    let release: (r: Response) => void = () => undefined;
    const stub = open([[CONFIRM, "POST", () => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response]]);
    const dialog = await openConfirm();
    const button = dialogConfirm(dialog);
    act(() => {
      button.click();
      button.click();
    });
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(1));
    await act(async () => release(confirmed()));
    await screen.findByRole("region", { name: "접수 확정 결과" });
    expect(posts(stub.calls, CONFIRM)).toHaveLength(1);
  });

  it("요청 중에는 Esc·닫기로 닫히지 않고, 응답 뒤에 잠금이 풀린다", async () => {
    let release: (r: Response) => void = () => undefined;
    let first = true;
    const stub = open([
      [
        CONFIRM,
        "POST",
        () => {
          if (!first) return confirmed();
          first = false;
          return new Promise<Response>((resolve) => (release = resolve)) as unknown as Response;
        },
      ],
    ]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(1));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "닫기" })).toBeDisabled();
    await act(async () => release(apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409)));
    await screen.findByText(/같은 거래처의 다른 건을 처리 중/);
    // 잠금이 풀려 같은 버튼을 다시 누를 수 있다.
    fireEvent.click(dialogConfirm(screen.getByRole("dialog")));
    await screen.findByRole("region", { name: "접수 확정 결과" });
    expect(posts(stub.calls, CONFIRM)).toHaveLength(2);
  });

  it("멱등 키는 (인테이크 id, version)당 1개 — 같은 version 재시도=같은 키, version이 오르면 새 키, 성공하면 비운다", async () => {
    let call = 0;
    const stub = open([
      [
        CONFIRM,
        "POST",
        () => {
          call += 1;
          return call <= 2 ? apiError("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422, "x", { lines: [{ line_no: 1, buyer_item_code: "ABC-1", reason_code: "ITEM_UNMAPPED" }] }) : confirmed();
        },
      ],
    ]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await screen.findByText(/매핑되지 않았거나 삭제된 품목/);
    fireEvent.click(dialogConfirm(screen.getByRole("dialog"))); // 같은 version 재시도
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(2));
    // 서버에서 version이 올랐다 → '최신 내용 불러오기'로 기준을 옮긴 뒤 다시 확정하면 새 키·새 version.
    server.detail = intakeDetail({ version: 5 });
    refetchViaFocus();
    fireEvent.click((await screen.findAllByRole("button", { name: "최신 내용 불러오기" }))[0] as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText(/다른 곳에서 이 인테이크가 수정되었습니다/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await screen.findByRole("region", { name: "접수 확정 결과" });
    const [a, b, c] = posts(stub.calls, CONFIRM);
    expect(a?.headers["Idempotency-Key"]).toBe(b?.headers["Idempotency-Key"]);
    expect(c?.headers["Idempotency-Key"]).not.toBe(a?.headers["Idempotency-Key"]);
    expect(a?.body).toEqual({ version: 2 });
    expect(c?.body).toEqual({ version: 5 });
  });

  it("쓰기 기준 version은 창 포커스 재조회로 앞서가지 않는다 — 낡은 화면은 안내 배너와 함께 이전 version으로 보내 서버 409가 막는다", async () => {
    const stub = open([[CONFIRM, "POST", () => apiError("COMMON.CONCURRENCY.VERSION_CONFLICT", 409)]]);
    await ready();
    server.detail = intakeDetail({ version: 9 });
    refetchViaFocus();
    expect(await screen.findByText(/다른 곳에서 이 인테이크가 수정되었습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await screen.findByText(/다른 곳에서 이 인테이크가 먼저 수정되었습니다/);
    expect(posts(stub.calls, CONFIRM)[0]?.body).toEqual({ version: 2 });
  });

  it("창 포커스 재조회로 서버 값이 바뀌어도 열려 있는 거부 다이얼로그와 입력한 사유가 지워지지 않는다", async () => {
    open();
    const dialog = await openReject();
    typeReason(dialog, "중복 접수라서 거부합니다");
    server.detail = intakeDetail({ version: 3 });
    refetchViaFocus();
    await screen.findByText(/다른 곳에서 이 인테이크가 수정되었습니다/);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByRole("textbox")).toHaveValue("중복 접수라서 거부합니다");
  });
});

describe("접수 확정 오류 — 한국어 안내(영문 코드·서버 원문 비노출)", () => {
  async function failWith(response: Response) {
    const stub = open([[CONFIRM, "POST", () => response]]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(1));
    return { stub, dialog: () => screen.getByRole("dialog") };
  }

  it("UNMAPPED_ITEMS 422 — 라인 번호·품번을 보이고 다이얼로그는 유지(복구 버튼 없음)", async () => {
    const { dialog } = await failWith(apiError("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422, "english raw", { lines: [{ line_no: 2, buyer_item_code: "X-9", reason_code: "ITEM_UNMAPPED" }] }));
    await waitFor(() => expect(dialog()).toHaveTextContent("라인 2(X-9)"));
    expect(dialog()).not.toHaveTextContent("english raw");
    expect(within(dialog()).queryByRole("button", { name: "최신 내용 불러오기" })).not.toBeInTheDocument();
  });

  it("STALE_MAPPING 409 — 재해석 안내와 '최신 내용 불러오기'", async () => {
    const { dialog } = await failWith(apiError("ORDER_INTAKE.LINE.STALE_MAPPING", 409, "english raw", { lines: [{ line_no: 1, buyer_item_code: "ABC-1", reason_code: "MAPPING_CHANGED" }] }));
    await waitFor(() => expect(dialog()).toHaveTextContent("'품번 다시 확인'으로 새 해석을 반영"));
    expect(within(dialog()).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("DUPLICATE_SKU 422 — 같은 SKU 라인 묶음", async () => {
    const { dialog } = await failWith(apiError("ORDER_INTAKE.LINE.DUPLICATE_SKU", 422, "x", { lines: [[1, 3]] }));
    await waitFor(() => expect(dialog()).toHaveTextContent("(라인 1·라인 3)"));
  });

  it("중복 바이어 PO 409 — 점유 문서번호·상태는 서버가 준 값만, 복구 버튼", async () => {
    const { dialog } = await failWith(apiError("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, "x", { doc_number: "SO-2026-0004", status: "CONFIRMED" }));
    await waitFor(() => expect(dialog()).toHaveTextContent("이미 등록된 수주: SO-2026-0004 (확정)"));
    expect(within(dialog()).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it.each([
    ["ORDER_INTAKE.GATE.UNRESOLVED", 409, /평가하지 못했습니다/],
    ["ORDER_INTAKE.STATE.NOT_PENDING", 409, /이미 확정되었거나 거부된/],
    ["COMMON.CONCURRENCY.VERSION_CONFLICT", 409, /다른 곳에서 이 인테이크가 먼저 수정/],
    ["COMMON.IDEMPOTENCY.KEY_CONFLICT", 409, /같은 요청 키로 다른 내용/],
    ["COMMON.CONCURRENCY.LOCK_BUSY", 409, /같은 거래처의 다른 건을 처리 중/],
    ["TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE", 409, /복제 원본 수주가 더 이상/],
    ["TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE", 422, /허용 범위/],
    ["ORDER_INTAKE.LINE.LIMIT_EXCEEDED", 422, /1개 이상 200개 이하/],
  ])("%s → 한국어", async (code, status, pattern) => {
    const { dialog } = await failWith(apiError(code, status, "ENGLISH RAW"));
    await waitFor(() => expect(dialog()).toHaveTextContent(pattern));
    expect(dialog()).not.toHaveTextContent("ENGLISH RAW");
  });

  it("확정 시점 납기 경과 422 — 인테이크 라인 번호·필드를 한국어로(SO 순번 아님)", async () => {
    const { dialog } = await failWith(apiError("COMMON.VALIDATION.INVALID_FIELD", 422, "x", { "line_3.requested_delivery_date": "요청납기는 오늘(KST)보다 앞설 수 없습니다." }));
    await waitFor(() => expect(dialog()).toHaveTextContent("라인 3 요청납기: 요청납기는 오늘(KST)보다 앞설 수 없습니다."));
  });

  it("시장 미등록·삭제 422(MARKETS.MARKET.NOT_REGISTERED) — 서버 detail의 위치·안내를 한국어로", async () => {
    const { dialog } = await failWith(
      apiError("MARKETS.MARKET.NOT_REGISTERED", 422, "ENGLISH RAW", { dest_market_code: "등록되지 않은 시장입니다: US. 시장 관리에서 먼저 등록해 주세요." }),
    );
    await waitFor(() => expect(dialog()).toHaveTextContent("도착 시장: 등록되지 않은 시장입니다: US."));
    expect(dialog()).not.toHaveTextContent("ENGLISH RAW");
  });

  it("실패 뒤 매핑 상태·게이트를 서버에서 다시 읽는다(화면이 옛 판정을 들고 있지 않게)", async () => {
    const { stub } = await failWith(apiError("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422, "x", { lines: [] }));
    await waitFor(() => expect(gateGets(stub.calls)).toHaveLength(2));
    await waitFor(() => expect(detailGets(stub.calls)).toHaveLength(2));
  });

  it("라우트 403 — 다이얼로그를 닫고 쓰기 버튼을 숨기고 안내로 포커스를 옮긴다(래치)", async () => {
    await failWith(apiError("AUTH.FORBIDDEN", 403));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    // 화면 단일 래치 — 편집 폼도 함께 숨긴다.
    expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument();
    const note = screen.getByText(/이 작업을 할 권한이 없습니다/);
    await waitFor(() => expect(note).toHaveFocus());
  });

  it("연결 끊김 — 처리되었을 수 있음·중복 처리 안 됨, 복구 버튼", async () => {
    open([
      [
        CONFIRM,
        "POST",
        () => {
          throw new TypeError("Failed to fetch");
        },
      ],
    ]);
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("연결이 끊겼습니다"));
    expect(screen.getByRole("dialog")).toHaveTextContent("중복 처리되지 않습니다");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });
});

describe("거부", () => {
  it("사유 5자 미만은 거부 버튼이 비활성이고 이유를 읽을 수 있다", async () => {
    const stub = open([[REJECT, "POST", () => jsonResponse(intakeDetail({ status: "REJECTED", version: 3 }))]]);
    const dialog = await openReject();
    const button = within(dialog).getByRole("button", { name: "거부" });
    typeReason(dialog, "짧음");
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("사유는 5자 이상 입력해 주세요.");
    expect(posts(stub.calls, REJECT)).toHaveLength(0);
  });

  it("제어문자(줄바꿈·탭)·보이지 않는 글자는 거부 — 화면에 이유가 나온다", async () => {
    open();
    const dialog = await openReject();
    typeReason(dialog, "첫 줄\n둘째 줄 사유");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("줄바꿈·탭·보이지 않는 글자");
    expect(within(dialog).getByRole("button", { name: "거부" })).toBeDisabled();
    typeReason(dialog, "사유​입니다 충분히");
    expect(within(dialog).getByRole("button", { name: "거부" })).toBeDisabled();
  });

  it("코드포인트 기준 — 이모지 5개는 통과·4개는 불가, 500자 통과·501자 불가", async () => {
    open();
    const dialog = await openReject();
    const button = () => within(dialog).getByRole("button", { name: "거부" });
    typeReason(dialog, "😀😀😀😀");
    expect(button()).toBeDisabled();
    typeReason(dialog, "😀😀😀😀😀");
    expect(button()).toBeEnabled();
    // 이모지 300개 = 300자(코드포인트) — UTF-16 단위(600)로 세면 500 초과로 오판한다.
    typeReason(dialog, "😀".repeat(300));
    expect(button()).toBeEnabled();
    typeReason(dialog, "가".repeat(500));
    expect(button()).toBeEnabled();
    typeReason(dialog, "가".repeat(501));
    expect(button()).toBeDisabled();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("500자 이내");
  });

  it("공백을 뺀 실질 글자가 5자 미만이면 불가", async () => {
    open();
    const dialog = await openReject();
    typeReason(dialog, "가 나 다 라");
    expect(within(dialog).getByRole("button", { name: "거부" })).toBeDisabled();
  });

  it("성공 — 본문 {version, 사유(trim)}·멱등 키, 거부 결과 안내·포커스, 버튼 제거", async () => {
    const stub = open([[REJECT, "POST", () => jsonResponse(intakeDetail({ status: "REJECTED", version: 3, reject_reason: "중복 접수라서 거부합니다" }))]]);
    const dialog = await openReject();
    typeReason(dialog, "  중복 접수라서 거부합니다  ");
    fireEvent.click(within(dialog).getByRole("button", { name: "거부" }));
    const region = await screen.findByRole("region", { name: "거부 결과" });
    const [call] = posts(stub.calls, REJECT);
    expect(call?.body).toEqual({ version: 2, reason: "중복 접수라서 거부합니다" });
    expect(call?.headers["Idempotency-Key"]).toBe("key-1");
    await waitFor(() => expect(region).toHaveFocus());
    expect(screen.getByRole("status", { name: "처리 결과" })).toHaveTextContent("인테이크를 거부했습니다");
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
  });

  it("같은 본문 재시도(실패 후)=같은 키, 사유가 바뀌면 새 키", async () => {
    let n = 0;
    const stub = open([[REJECT, "POST", () => (++n <= 2 ? apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409) : jsonResponse(intakeDetail({ status: "REJECTED", version: 3 })))]]);
    const dialog = await openReject();
    typeReason(dialog, "첫 번째 사유입니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "거부" }));
    await screen.findByText(/같은 거래처의 다른 건을 처리 중/);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "거부" }));
    await waitFor(() => expect(posts(stub.calls, REJECT)).toHaveLength(2));
    typeReason(screen.getByRole("dialog"), "두 번째 사유입니다");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "거부" }));
    await screen.findByRole("region", { name: "거부 결과" });
    const keys = posts(stub.calls, REJECT).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("NOT_PENDING 409 — 한국어 안내와 최신 내용 불러오기", async () => {
    open([[REJECT, "POST", () => apiError("ORDER_INTAKE.STATE.NOT_PENDING", 409, "ENGLISH RAW")]]);
    const dialog = await openReject();
    typeReason(dialog, "충분히 긴 거부 사유");
    fireEvent.click(within(dialog).getByRole("button", { name: "거부" }));
    expect(await screen.findByText(/이미 확정되었거나 거부된/)).toBeInTheDocument();
    expect(screen.queryByText("ENGLISH RAW")).not.toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });
});

describe("품번 다시 확인(resolve)", () => {
  it("본문 {version}·멱등 키(같은 version 재시도=같은 키), 성공 시 응답 version으로 기준 이동·live region", async () => {
    let n = 0;
    const stub = open([
      [
        RESOLVE,
        "POST",
        () => {
          n += 1;
          return n === 1 ? apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409) : jsonResponse(intakeDetail({ version: 3 }));
        },
      ],
      [CONFIRM, "POST", () => confirmed()],
    ]);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await screen.findByText(/같은 거래처의 다른 건을 처리 중/);
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await waitFor(() => expect(posts(stub.calls, RESOLVE)).toHaveLength(2));
    const [a, b] = posts(stub.calls, RESOLVE);
    expect(a?.body).toEqual({ version: 2 });
    expect(a?.headers["Idempotency-Key"]).toBe(b?.headers["Idempotency-Key"]);
    expect(await screen.findByText(/바뀐 해석을 반영했으니/)).toBeInTheDocument();
    // 응답을 화면에서 확인했으므로 이어지는 쓰기는 새 version(3)을 싣는다.
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(dialogConfirm(await screen.findByRole("dialog")));
    await screen.findByRole("region", { name: "접수 확정 결과" });
    expect(posts(stub.calls, CONFIRM)[0]?.body).toEqual({ version: 3 });
  });

  it("version이 오르지 않으면 '바뀐 해석이 없습니다'", async () => {
    open([[RESOLVE, "POST", () => jsonResponse(intakeDetail({ version: 2 }))]]);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    expect(await screen.findByText(/바뀐 해석이 없습니다/)).toBeInTheDocument();
  });

  it("재해석이 매핑 상태를 바꾸면 라인 표가 서버 응답대로 갱신된다", async () => {
    open(
      [
        [
          RESOLVE,
          "POST",
          () => {
            server.detail = intakeDetail({ version: 3, lines: [intakeLine({ mapping_state: "MAPPED" })] });
            return jsonResponse(server.detail);
          },
        ],
      ],
      { detail: intakeDetail({ lines: [intakeLine({ mapping_state: "STALE" })] }) },
    );
    await screen.findByText("재해석 필요");
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await screen.findByText("매핑됨");
    expect(screen.queryByText("재해석 필요")).not.toBeInTheDocument();
  });

  it("실패 시 한국어 안내(409는 최신 내용 불러오기)", async () => {
    open([[RESOLVE, "POST", () => apiError("ORDER_INTAKE.STATE.NOT_PENDING", 409, "ENGLISH RAW")]]);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    expect(await screen.findByText(/이미 확정되었거나 거부된/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "최신 내용 불러오기" }).length).toBeGreaterThan(0);
  });
});

describe("편집 PATCH — 헤더+라인 전체 교체", () => {
  it("시드·무변경 저장 불가", async () => {
    open();
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    expect(form.getByLabelText("바이어 PO번호")).toHaveValue("PO-2026-001");
    expect(form.getByLabelText("라인 1 바이어 품번")).toHaveValue("ABC-1");
    expect(form.getByLabelText("라인 1 수량")).toHaveValue("10");
    expect(form.getByLabelText("라인 1 단가")).toHaveValue("12.50");
    expect(form.getByRole("button", { name: "수정 저장" })).toBeDisabled();
  });

  it("바뀐 헤더 필드만 보내고 라인은 보내지 않는다(version은 기준 version)", async () => {
    const stub = open([[ID, "PATCH", () => jsonResponse(intakeDetail({ version: 3, buyer_po_no: "PO-NEW" }))]]);
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form.getByLabelText("바이어 PO번호"), { target: { value: " PO-NEW " } });
    fireEvent.click(form.getByRole("button", { name: "수정 저장" }));
    await waitFor(() => expect(patches(stub.calls)).toHaveLength(1));
    expect(patches(stub.calls)[0]?.body).toEqual({ version: 2, buyer_po_no: "PO-NEW" });
  });

  it("라인: 기존 라인은 id 유지·수정, 신규는 id 없음, 목록에 없는 기존 라인은 제외 — 전체 목록을 보낸다", async () => {
    const stub = open(
      [[ID, "PATCH", () => jsonResponse(intakeDetail({ version: 3 }))]],
      {
        detail: intakeDetail({
          lines: [
            intakeLine({ id: 101, line_no: 1, buyer_item_code: "A-1", quantity: 1, unit_price_text: "1.00" }),
            intakeLine({ id: 102, line_no: 2, buyer_item_code: "B-2", quantity: 2, unit_price_text: "2.00", sku_id: 6, sku_code: "SKU-6" }),
            intakeLine({ id: 103, line_no: 3, buyer_item_code: "C-3", quantity: 3, unit_price_text: "3.00", sku_id: 7, sku_code: "SKU-7" }),
          ],
        }),
      },
    );
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form.getByLabelText("라인 1 수량"), { target: { value: "5" } }); // 수정(id 유지)
    fireEvent.click(form.getByRole("button", { name: "라인 2 삭제" })); // 제외
    fireEvent.click(form.getByRole("button", { name: "라인 추가" })); // 신규
    // 새 라인은 위치(3번째)가 아니라 '신규 1' — 남은 기존 라인은 서버 번호(라인 1·라인 3)를 유지한다.
    expect(form.getByLabelText("라인 3 바이어 품번")).toHaveValue("C-3");
    fireEvent.change(form.getByLabelText("신규 1 바이어 품번"), { target: { value: "N-4" } });
    fireEvent.change(form.getByLabelText("신규 1 수량"), { target: { value: "4" } });
    fireEvent.change(form.getByLabelText("신규 1 단가"), { target: { value: "4.5" } });
    fireEvent.click(form.getByRole("button", { name: "수정 저장" }));
    await waitFor(() => expect(patches(stub.calls)).toHaveLength(1));
    expect(patches(stub.calls)[0]?.body).toEqual({
      version: 2,
      lines: [
        { id: 101, buyer_item_code: "A-1", quantity: 5, unit_price: "1.00", requested_delivery_date: "2099-12-31" },
        { id: 103, buyer_item_code: "C-3", quantity: 3, unit_price: "3.00", requested_delivery_date: "2099-12-31" },
        { buyer_item_code: "N-4", quantity: 4, unit_price: "4.5", requested_delivery_date: null },
      ],
    });
  });

  it("형식 오류(수량·단가)는 서버를 부르기 전에 막는다 — 같은 SKU·0 단가·납기는 서버 판정", async () => {
    const stub = open();
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form.getByLabelText("라인 1 수량"), { target: { value: "0" } });
    fireEvent.click(form.getByRole("button", { name: "수정 저장" }));
    expect(await form.findByRole("alert")).toHaveTextContent("라인 1의 수량은 1 이상의 정수");
    expect(patches(stub.calls)).toHaveLength(0);
  });

  it("저장 성공 — 응답이 화면 상태가 되고 기준 version이 응답 version으로 옮겨져 다음 쓰기에 실린다·live region", async () => {
    let n = 0;
    const stub = open([
      [
        ID,
        "PATCH",
        () => {
          n += 1;
          server.detail = intakeDetail({ version: 2 + n, buyer_po_no: `PO-${n}` });
          return jsonResponse(server.detail);
        },
      ],
    ]);
    await ready();
    const form = () => within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form().getByLabelText("바이어 PO번호"), { target: { value: "PO-1" } });
    fireEvent.click(form().getByRole("button", { name: "수정 저장" }));
    await screen.findByText(/수정을 저장했습니다/);
    await waitFor(() => expect(form().getByLabelText("바이어 PO번호")).toHaveValue("PO-1"));
    fireEvent.change(form().getByLabelText("바이어 PO번호"), { target: { value: "PO-2" } });
    fireEvent.click(form().getByRole("button", { name: "수정 저장" }));
    await waitFor(() => expect(patches(stub.calls)).toHaveLength(2));
    expect(patches(stub.calls).map((c) => c.body?.version)).toEqual([2, 3]);
  });

  it("창 포커스 재조회로 서버 값이 바뀌어도 입력 중인 편집 내용이 지워지지 않는다", async () => {
    open();
    await ready();
    const form = () => within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form().getByLabelText("바이어 PO번호"), { target: { value: "PO-내가-고치는-중" } });
    server.detail = intakeDetail({ version: 4, buyer_po_no: "PO-남이-고침" });
    refetchViaFocus();
    await screen.findByText(/다른 곳에서 이 인테이크가 수정되었습니다/);
    expect(form().getByLabelText("바이어 PO번호")).toHaveValue("PO-내가-고치는-중");
  });

  it("중복 PO 409 — 점유 인테이크 링크, 충돌 시 최신 내용 불러오기", async () => {
    open([[ID, "PATCH", () => apiError("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, "ENGLISH RAW", { intake_id: 8, status: "PENDING" })]]);
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form.getByLabelText("바이어 PO번호"), { target: { value: "PO-DUP" } });
    fireEvent.click(form.getByRole("button", { name: "수정 저장" }));
    const alert = await form.findByRole("alert");
    expect(alert).toHaveTextContent("이미 등록된 대기 인테이크: #8 (대기)");
    expect(alert).not.toHaveTextContent("ENGLISH RAW");
    expect(form.getByRole("link", { name: "점유 중인 인테이크 #8 보기" })).toHaveAttribute("href", "/orders/intakes/8");
    expect(form.getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("라우트 403 — 편집 폼·조치 버튼을 숨기고 조회 전용 안내를 보인다(래치 — 같은 화면에서는 복구하지 않음)", async () => {
    const stub = open([[ID, "PATCH", () => apiError("AUTH.FORBIDDEN", 403)]]);
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form.getByLabelText("바이어 PO번호"), { target: { value: "PO-X" } });
    fireEvent.click(form.getByRole("button", { name: "수정 저장" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    expect(screen.getByText(/수정·품번 재해석·확정·거부는 무역·관리자만/)).toBeInTheDocument();
    expect(patches(stub.calls)).toHaveLength(1);
  });

  it("거래처·통화는 편집 필드가 없다(등록 뒤 변경 불가)", async () => {
    open();
    await ready();
    const form = within(screen.getByRole("form", { name: "인테이크 편집" }));
    expect(form.queryByLabelText(/통화/)).not.toBeInTheDocument();
    expect(form.queryByLabelText(/바이어$/)).not.toBeInTheDocument();
    expect(form.getByText(/바이어·통화는 등록 뒤 바꿀 수 없습니다/)).toBeInTheDocument();
  });
});

describe("품번 등록 유도 — SKU 연결 후 사람이 '품번 다시 확인'", () => {
  const unmappedDetail = () =>
    intakeDetail({ lines: [intakeLine({ line_no: 1, buyer_item_code: " NEW-2 ", sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" })] });

  async function pickSku() {
    fireEvent.focus(screen.getByRole("combobox", { name: "라인 1 연결할 SKU" }));
    fireEvent.click(await screen.findByRole("option", { name: "SKU-008 진정 크림" }));
  }

  it("SKU를 고르면 POST /partners/{바이어}/item-codes — 본문·멱등 키, 성공 안내, resolve는 자동 호출하지 않는다", async () => {
    const stub = open([[ITEM_CODES, "POST", () => jsonResponse({ id: 1 }, 201)]], { detail: unmappedDetail() });
    await ready();
    const register = screen.getByRole("button", { name: "품번 등록" });
    expect(register).toBeDisabled();
    await pickSku();
    fireEvent.click(screen.getByRole("button", { name: "품번 등록" }));
    await screen.findByText(/바이어 품번 매핑을 등록했습니다/);
    const [call] = posts(stub.calls, ITEM_CODES);
    expect(call?.body).toEqual({ sku_id: 8, buyer_item_code: "NEW-2" });
    expect(call?.headers["Idempotency-Key"]).toBe("key-1");
    expect(posts(stub.calls, RESOLVE)).toHaveLength(0);
  });

  it("실패 후 같은 본문 재시도=같은 키, 서버 한국어 오류를 그대로 보인다", async () => {
    let n = 0;
    const stub = open([[ITEM_CODES, "POST", () => (++n === 1 ? apiError("PARTNERS.ITEM_CODE.DUPLICATE", 409, "이미 등록된 품번입니다.") : jsonResponse({ id: 1 }, 201))]], { detail: unmappedDetail() });
    await ready();
    await pickSku();
    fireEvent.click(screen.getByRole("button", { name: "품번 등록" }));
    await screen.findByText(/이미 등록된 품번입니다/);
    fireEvent.click(screen.getByRole("button", { name: "품번 등록" }));
    await screen.findByText(/바이어 품번 매핑을 등록했습니다/);
    const keys = posts(stub.calls, ITEM_CODES).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
  });

  it("읽기 전용 역할에게는 등록 입력이 없고 무역 담당자가 해야 한다고 안내한다", async () => {
    open([], { me: VIEWER, detail: unmappedDetail() });
    await ready();
    expect(screen.queryByRole("combobox", { name: "라인 1 연결할 SKU" })).not.toBeInTheDocument();
    expect(screen.getByText(/무역 담당자가 품번을 등록해야 합니다/)).toBeInTheDocument();
  });
});

describe("읽기 전용", () => {
  it("읽기 전용 역할 — 조치 버튼·편집 폼이 없고 안내만 보인다(게이트 요약·라인은 그대로)", async () => {
    open([], { me: VIEWER });
    await ready();
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "거부" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "품번 다시 확인" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument();
    expect(screen.getByText(/품번 재해석·확정·거부는 무역·관리자만 할 수 있습니다/)).toBeInTheDocument();
    expect(await screen.findByText(/서버 사전 점검/)).toBeInTheDocument();
  });

  it.each([
    ["CONFIRMED", "이미 확정된 인테이크라 읽기 전용입니다."],
    ["REJECTED", "거부된 인테이크라 읽기 전용입니다."],
  ])("%s는 쓰기 역할에게도 버튼·편집 폼이 없다", async (status, text) => {
    open([], { detail: intakeDetail({ status, sales_order_id: status === "CONFIRMED" ? 5 : null }) });
    await ready();
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "거부" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /연결할 SKU/ })).not.toBeInTheDocument();
  });

  it("확정 인테이크는 생성된 수주로 가는 링크를 보인다", async () => {
    open([], { detail: intakeDetail({ status: "CONFIRMED", sales_order_id: 5 }) });
    await ready();
    expect(screen.getByRole("link", { name: "수주 #5" })).toHaveAttribute("href", "/sales-orders/5");
  });
});

describe("재판정 금지·키·오프라인 보강", () => {
  it("미매핑 라인이 있고 서버 사전 점검이 미통과여도 확정 버튼은 서버를 부르고, 서버의 422를 그대로 안내한다", async () => {
    const stub = open(
      [[CONFIRM, "POST", () => apiError("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422, "x", { lines: [{ line_no: 1, buyer_item_code: "NEW-1", reason_code: "ITEM_UNMAPPED" }] })]],
      {
        detail: intakeDetail({ lines: [intakeLine({ buyer_item_code: "NEW-1", sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" })] }),
        gates: intakeGateReport({ intake_confirmable: false, gates: [intakeGate({ level: "BLOCK", blocks_intake_confirm: true, settlement: "UNRESOLVED" })] }),
      },
    );
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("라인 1(NEW-1)"));
  });

  it("STALE 라인이 있어도 확정 버튼은 서버를 부른다(409 STALE_MAPPING은 서버가 판정)", async () => {
    const stub = open([[CONFIRM, "POST", () => apiError("ORDER_INTAKE.LINE.STALE_MAPPING", 409, "x", { lines: [] })]], {
      detail: intakeDetail({ lines: [intakeLine({ mapping_state: "STALE" })] }),
    });
    const dialog = await openConfirm();
    fireEvent.click(dialogConfirm(dialog));
    await waitFor(() => expect(posts(stub.calls, CONFIRM)).toHaveLength(1));
  });

  it("품번 다시 확인 성공 뒤에는 키를 비운다 — 바뀐 것이 없어 version이 같아도 다음 요청은 새 키", async () => {
    const stub = open([[RESOLVE, "POST", () => jsonResponse(intakeDetail({ version: 2 }))]]);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await screen.findByText(/바뀐 해석이 없습니다/);
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await waitFor(() => expect(posts(stub.calls, RESOLVE)).toHaveLength(2));
    const [a, b] = posts(stub.calls, RESOLVE);
    expect(a?.body).toEqual({ version: 2 });
    expect(b?.body).toEqual({ version: 2 });
    expect(a?.headers["Idempotency-Key"]).not.toBe(b?.headers["Idempotency-Key"]);
  });

  it("편집 저장도 창 포커스 재조회로 앞서간 version이 아니라 화면 기준 version을 싣는다", async () => {
    const stub = open([[ID, "PATCH", () => apiError("COMMON.CONCURRENCY.VERSION_CONFLICT", 409)]]);
    await ready();
    const form = () => within(screen.getByRole("form", { name: "인테이크 편집" }));
    fireEvent.change(form().getByLabelText("바이어 PO번호"), { target: { value: "PO-내가-고침" } });
    server.detail = intakeDetail({ version: 9 });
    refetchViaFocus();
    await screen.findByText(/다른 곳에서 이 인테이크가 수정되었습니다/);
    fireEvent.click(form().getByRole("button", { name: "수정 저장" }));
    await waitFor(() => expect(patches(stub.calls)).toHaveLength(1));
    expect(patches(stub.calls)[0]?.body?.version).toBe(2);
    expect(await form().findByRole("alert")).toHaveTextContent("다른 곳에서 이 인테이크가 먼저 수정되었습니다");
  });

  it.each([
    ["확정", CONFIRM],
    ["거부", REJECT],
    ["품번 다시 확인", RESOLVE],
  ])("오프라인 상태여도 %s 요청을 보내 본다(paused로 갇히지 않는다 — networkMode always)", async (kind, path) => {
    const stub = open([
      [CONFIRM, "POST", () => confirmed()],
      [REJECT, "POST", () => jsonResponse(intakeDetail({ status: "REJECTED", version: 3 }))],
      [RESOLVE, "POST", () => jsonResponse(intakeDetail({ version: 2 }))],
    ]);
    await ready();
    let dialog: HTMLElement | null = null;
    if (kind === "확정") dialog = await openConfirm();
    if (kind === "거부") {
      dialog = await openReject();
      typeReason(dialog, "충분히 긴 거부 사유");
    }
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    try {
      if (kind === "확정" && dialog) fireEvent.click(dialogConfirm(dialog));
      if (kind === "거부" && dialog) fireEvent.click(within(dialog).getByRole("button", { name: "거부" }));
      if (kind === "품번 다시 확인") fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
      await waitFor(() => expect(posts(stub.calls, path)).toHaveLength(1));
    } finally {
      // 실패해도 온라인으로 되돌린다 — 다음 시험이 오프라인 상태를 물려받지 않게.
      act(() => {
        window.dispatchEvent(new Event("online"));
      });
    }
  });
});
