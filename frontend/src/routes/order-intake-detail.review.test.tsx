// 오더 인테이크 상세 — 적대 검토(2렌즈) 반영 시험 (S3-1 PR-13b 2차).
// 인테이크 간 이동 시 상태 격리·라인 이름(서버 라인 번호)·재해석 키 비움·재조회 실패 인라인·단일 403 래치·포커스·비활성 버튼 사유·읽기 전용 안내.
// fetch 스텁은 정확 URL·메서드 일치(`stubGateFetch`) — 오라우팅은 404로 터진다.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useNavigate, type NavigateFunction } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type { IntakeDetail } from "../lib/order-intake";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { apiError, intakeDetail, intakeGateReport, intakeLine } from "../test/intake-fixtures";
import { TRADER, VIEWER, jsonResponse, page } from "../test/render";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const url = (id: number, tail = "") => `/v1/order-intakes/${id}${tail}`;
const server: Record<number, IntakeDetail> = {};
const nav: { go: NavigateFunction | null } = { go: null };

function NavProbe() {
  nav.go = useNavigate();
  return null;
}

interface Opts {
  me?: unknown;
  detail?: IntakeDetail;
  other?: IntakeDetail;
  route?: string;
}

/** 인테이크 #21(기본)과 #8(다른 인테이크)을 함께 둔다 — 같은 라우트 컴포넌트로 오가는 시험용. */
function open(extra: GateHandler[] = [], opts: Opts = {}) {
  server[21] = opts.detail ?? intakeDetail();
  server[8] = opts.other ?? intakeDetail({ id: 8, buyer_po_no: "PO-OTHER-8", lines: [intakeLine({ id: 801 })] });
  const stub = stubGateFetch(opts.me ?? TRADER, [
    ...extra,
    [url(21), "GET", () => jsonResponse(server[21])],
    [url(8), "GET", () => jsonResponse(server[8])],
    [url(21, "/gates"), "GET", () => jsonResponse(intakeGateReport())],
    [url(8, "/gates"), "GET", () => jsonResponse(intakeGateReport({ intake_id: 8 }))],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ["/v1/markets?size=200", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국", name_en: null, note: null, version: 1 }]))],
    ["/v1/skus?size=20", "GET", () => jsonResponse(page([{ id: 8, sku_code: "SKU-008", name_ko: "진정 크림" }]))],
    ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/order-intakes", "GET", () => jsonResponse(page([]))],
  ]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[opts.route ?? "/orders/intakes/21"]}>
        <NavProbe />
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...stub, client };
}

const posts = (calls: GateCall[], path: string) => calls.filter((c) => c.method === "POST" && c.url === `/api${path}`);
const patches = (calls: GateCall[], id = 21) => calls.filter((c) => c.method === "PATCH" && c.url === `/api${url(id)}`);
const detailGets = (calls: GateCall[], id = 21) => calls.filter((c) => c.method === "GET" && c.url === `/api${url(id)}`);
const refetchViaFocus = () =>
  act(() => {
    window.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
const heading = (po: string) => screen.findByRole("heading", { name: new RegExp(`PO ${po}`) });
const go = (to: string | number) =>
  act(() => {
    if (typeof to === "number") void nav.go?.(to);
    else void nav.go?.(to);
  });
const confirmed = (id: number, doc = "SO-2026-0077") =>
  jsonResponse({ intake_id: id, sales_order_id: 77, doc_number: doc, intake: intakeDetail({ id, status: "CONFIRMED", version: 3, sales_order_id: 77 }) }, 201);
const editForm = () => within(screen.getByRole("form", { name: "인테이크 편집" }));
const actionsPresent = () => {
  expect(screen.getByRole("button", { name: "접수 확정" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "거부" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "품번 다시 확인" })).toBeInTheDocument();
};

describe("인테이크 간 이동 — 검토 결과·키·잠금·래치가 다른 인테이크로 새지 않는다", () => {
  it("#21 → '점유 중인 인테이크 #8 보기' → #8 거부 → 뒤로 #21: '거부했습니다' 없음·조치 버튼 있음", async () => {
    open([
      [url(21), "PATCH", () => apiError("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, "x", { intake_id: 8, status: "PENDING" })],
      [url(8, "/reject"), "POST", () => jsonResponse(intakeDetail({ id: 8, status: "REJECTED", version: 3, buyer_po_no: "PO-OTHER-8" }))],
    ]);
    await heading("PO-2026-001");
    fireEvent.change(editForm().getByLabelText("바이어 PO번호"), { target: { value: "PO-OTHER-8" } });
    fireEvent.click(editForm().getByRole("button", { name: "수정 저장" }));
    fireEvent.click(await screen.findByRole("link", { name: "점유 중인 인테이크 #8 보기" }));
    await heading("PO-OTHER-8");
    fireEvent.click(screen.getByRole("button", { name: "거부" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "중복 접수라 거부합니다" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "거부" }));
    await screen.findByRole("region", { name: "거부 결과" });
    go(-1);
    await heading("PO-2026-001");
    expect(screen.queryByRole("region", { name: "거부 결과" })).not.toBeInTheDocument();
    expect(screen.queryByText(/인테이크를 거부했습니다/)).not.toBeInTheDocument();
    expect(screen.queryByText(/방금 이 화면에서 처리되어/)).not.toBeInTheDocument();
    actionsPresent();
  });

  it("#21 확정 결과·기준 version이 #8로 새지 않는다", async () => {
    open([[url(21, "/confirm"), "POST", () => confirmed(21)]]);
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "접수 확정" }));
    await screen.findByRole("region", { name: "접수 확정 결과" });
    go("/orders/intakes/8");
    await heading("PO-OTHER-8");
    expect(screen.queryByRole("region", { name: "접수 확정 결과" })).not.toBeInTheDocument();
    expect(screen.queryByText(/방금 이 화면에서 처리되어/)).not.toBeInTheDocument();
    expect(screen.queryByText(/다른 곳에서 이 인테이크가 수정되었습니다/)).not.toBeInTheDocument();
    actionsPresent();
  });

  it("확정 요청 중 다른 인테이크로 이동 — 늦게 온 성공 응답이 새 인테이크의 화면을 바꾸지 않는다(새 인테이크 확정은 새 키·자기 version)", async () => {
    let release: (r: Response) => void = () => undefined;
    const stub = open([
      [url(21, "/confirm"), "POST", () => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response],
      [url(8, "/confirm"), "POST", () => apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409)],
    ]);
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "접수 확정" }));
    await waitFor(() => expect(posts(stub.calls, url(21, "/confirm"))).toHaveLength(1));
    go("/orders/intakes/8");
    await heading("PO-OTHER-8");
    await act(async () => release(confirmed(21)));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.queryByRole("region", { name: "접수 확정 결과" })).not.toBeInTheDocument();
    expect(screen.queryByText(/방금 이 화면에서 처리되어/)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    actionsPresent();
    // #8의 확정은 #21의 잠금·키와 무관하게 바로 나간다.
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "접수 확정" }));
    await waitFor(() => expect(posts(stub.calls, url(8, "/confirm"))).toHaveLength(1));
    const [first] = posts(stub.calls, url(21, "/confirm"));
    const [eight] = posts(stub.calls, url(8, "/confirm"));
    expect(eight?.headers["Idempotency-Key"]).not.toBe(first?.headers["Idempotency-Key"]);
  });

  it("403 래치는 인테이크마다 — #21에서 403을 받아도 #8에서는 조치 버튼이 다시 보인다", async () => {
    open([[url(21, "/resolve"), "POST", () => apiError("AUTH.FORBIDDEN", 403)]]);
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await screen.findByText(/이 작업을 할 권한이 없습니다/);
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    go("/orders/intakes/8");
    await heading("PO-OTHER-8");
    expect(screen.queryByText(/이 작업을 할 권한이 없습니다/)).not.toBeInTheDocument();
    actionsPresent();
  });
});

describe("편집 라인 이름 — 위치가 아니라 서버 라인 번호", () => {
  const threeLines = () =>
    intakeDetail({
      lines: [
        intakeLine({ id: 101, line_no: 1, buyer_item_code: "A-1" }),
        intakeLine({ id: 102, line_no: 2, buyer_item_code: "B-2" }),
        intakeLine({ id: 103, line_no: 3, buyer_item_code: "C-3" }),
      ],
    });

  it("라인 2를 지운 뒤 라인 3의 형식 오류는 '라인 3'으로 안내한다(위치 2번째라도)", async () => {
    const stub = open([], { detail: threeLines() });
    await heading("PO-2026-001");
    fireEvent.click(editForm().getByRole("button", { name: "라인 2 삭제" }));
    fireEvent.change(editForm().getByLabelText("라인 3 수량"), { target: { value: "0" } });
    fireEvent.click(editForm().getByRole("button", { name: "라인 추가" }));
    fireEvent.click(editForm().getByRole("button", { name: "수정 저장" }));
    const alert = await editForm().findByRole("alert");
    expect(alert).toHaveTextContent("라인 3의 수량은 1 이상의 정수로 입력해 주세요.");
    expect(alert).toHaveTextContent("신규 1의 바이어 품번을 입력해 주세요.");
    expect(alert).not.toHaveTextContent("라인 2의");
    expect(editForm().getByLabelText("라인 3 수량")).toHaveAttribute("aria-invalid", "true");
    await waitFor(() => expect(editForm().getByLabelText("라인 3 수량")).toHaveFocus());
    expect(patches(stub.calls)).toHaveLength(0);
  });

  it("서버 422 `lines[1]`은 보낸 두 번째 행 — 라인 2를 지운 뒤라면 '라인 3'으로 옮긴다", async () => {
    open([[url(21), "PATCH", () => apiError("COMMON.VALIDATION.INVALID_FIELD", 422, "x", { "lines[1].unit_price": "단가는 0보다 커야 합니다." })]], { detail: threeLines() });
    await heading("PO-2026-001");
    fireEvent.click(editForm().getByRole("button", { name: "라인 2 삭제" }));
    fireEvent.change(editForm().getByLabelText("라인 3 단가"), { target: { value: "0" } });
    fireEvent.click(editForm().getByRole("button", { name: "수정 저장" }));
    expect(await editForm().findByRole("alert")).toHaveTextContent("라인 3 단가: 단가는 0보다 커야 합니다.");
  });

  it("편집 표의 라인 칸은 서버 번호·'신규 n'을 보인다", async () => {
    open([], { detail: threeLines() });
    await heading("PO-2026-001");
    fireEvent.click(editForm().getByRole("button", { name: "라인 1 삭제" }));
    fireEvent.click(editForm().getByRole("button", { name: "라인 추가" }));
    const table = within(editForm().getByRole("table", { name: "인테이크 라인 입력" }));
    const firstCells = table.getAllByRole("row").slice(1).map((row) => (row.querySelector("td") as HTMLElement).textContent);
    expect(firstCells).toEqual(["2", "3", "신규 1"]);
  });
});

describe("품번 재해석 키 — 매핑 등록·불러오기 뒤에는 새 키, 옛 응답은 덮지 않음", () => {
  const unmapped = () => intakeDetail({ lines: [intakeLine({ buyer_item_code: "NEW-1", sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" })] });

  it("재해석 실패 뒤 품번을 등록하면 같은 version이라도 다음 재해석은 새 키(옛 결과 재생 방지)", async () => {
    let n = 0;
    const stub = open(
      [
        [url(21, "/resolve"), "POST", () => (++n === 1 ? apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409) : jsonResponse(intakeDetail({ version: 3 })))],
        ["/v1/partners/3/item-codes", "POST", () => jsonResponse({ id: 1 }, 201)],
      ],
      { detail: unmapped() },
    );
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await screen.findByText(/같은 거래처의 다른 건을 처리 중/);
    fireEvent.focus(screen.getByRole("combobox", { name: "라인 1 연결할 SKU" }));
    fireEvent.click(await screen.findByRole("option", { name: "SKU-008 진정 크림" }));
    fireEvent.click(screen.getByRole("button", { name: "품번 등록" }));
    await screen.findByText(/바이어 품번 매핑을 등록했습니다/);
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await waitFor(() => expect(posts(stub.calls, url(21, "/resolve"))).toHaveLength(2));
    const [a, b] = posts(stub.calls, url(21, "/resolve"));
    expect(a?.body).toEqual({ version: 2 });
    expect(b?.body).toEqual({ version: 2 });
    expect(b?.headers["Idempotency-Key"]).not.toBe(a?.headers["Idempotency-Key"]);
  });

  it("'최신 내용 불러오기' 뒤에도 재해석 키를 비운다", async () => {
    const stub = open([[url(21, "/resolve"), "POST", () => apiError("COMMON.CONCURRENCY.LOCK_BUSY", 409)]]);
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await screen.findByText(/같은 거래처의 다른 건을 처리 중/);
    fireEvent.click(screen.getByRole("button", { name: "최신 내용 불러오기" }));
    // 실패 뒤 재조회 1회 + 불러오기 재조회 1회(첫 조회 포함 3회) — 불러오기가 끝나면 오류 안내가 비워진다.
    await waitFor(() => expect(detailGets(stub.calls)).toHaveLength(3));
    await waitFor(() => expect(screen.queryByText(/같은 거래처의 다른 건을 처리 중/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await waitFor(() => expect(posts(stub.calls, url(21, "/resolve"))).toHaveLength(2));
    const [a, b] = posts(stub.calls, url(21, "/resolve"));
    expect(b?.headers["Idempotency-Key"]).not.toBe(a?.headers["Idempotency-Key"]);
  });

  it("캐시가 이미 더 새 version이면 늦은 재해석 응답으로 덮지 않고 다시 읽는다", async () => {
    let release: (r: Response) => void = () => undefined;
    const stub = open([[url(21, "/resolve"), "POST", () => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response]]);
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "품번 다시 확인" }));
    await waitFor(() => expect(posts(stub.calls, url(21, "/resolve"))).toHaveLength(1));
    server[21] = intakeDetail({ version: 6, buyer_po_no: "PO-2026-001", lines: [intakeLine({ quantity: 66 })] });
    refetchViaFocus();
    await screen.findByText(/버전/);
    await waitFor(() => expect(screen.getByText("6")).toBeInTheDocument());
    const before = detailGets(stub.calls).length;
    await act(async () => release(jsonResponse(intakeDetail({ version: 3, lines: [intakeLine({ quantity: 33 })] }))));
    await waitFor(() => expect(detailGets(stub.calls).length).toBeGreaterThan(before));
    expect(screen.queryByText("33")).not.toBeInTheDocument();
    expect(screen.getAllByText("66").length).toBeGreaterThan(0);
  });
});

describe("쓰기 실패 뒤 재조회도 실패 — 전체 오류 화면으로 바꾸지 않는다", () => {
  it("확정 422 뒤 상세 재조회가 500이어도 다이얼로그·오류 안내가 남고, 인라인 경고와 다시 시도를 보인다", async () => {
    let failGet = false;
    const stub = open([
      [url(21), "GET", () => (failGet ? apiError("X", 500, "서버가 응답하지 못했습니다.") : jsonResponse(server[21]))],
      [
        url(21, "/confirm"),
        "POST",
        () => {
          failGet = true;
          return apiError("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422, "x", { lines: [{ line_no: 1, buyer_item_code: "ABC-1", reason_code: "ITEM_UNMAPPED" }] });
        },
      ],
    ]);
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "접수 확정" }));
    expect(await screen.findByText(/최신 상태를 불러오지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent("라인 1(ABC-1)");
    expect(screen.getByRole("heading", { name: /PO PO-2026-001/ })).toBeInTheDocument();
    failGet = false;
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(screen.queryByText(/최신 상태를 불러오지 못했습니다/)).not.toBeInTheDocument());
    expect(detailGets(stub.calls).length).toBeGreaterThanOrEqual(3);
  });
});

describe("단일 403 래치·포커스", () => {
  const unmapped = () => intakeDetail({ lines: [intakeLine({ buyer_item_code: "NEW-1", sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" })] });

  it("확정 403 — 편집 폼·SKU 연결 입력까지 숨기고 권한 안내로 포커스", async () => {
    open([[url(21, "/confirm"), "POST", () => apiError("AUTH.FORBIDDEN", 403)]], { detail: unmapped() });
    await heading("PO-2026-001");
    expect(screen.getByRole("combobox", { name: "라인 1 연결할 SKU" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "접수 확정" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "접수 확정" }));
    const alert = await screen.findByText(/이 작업을 할 권한이 없습니다/);
    await waitFor(() => expect(alert).toHaveFocus());
    expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "라인 1 연결할 SKU" })).not.toBeInTheDocument();
  });

  it("품번 등록 403 — 같은 래치(조치 버튼·편집 폼 숨김, 안내로 포커스)", async () => {
    open([["/v1/partners/3/item-codes", "POST", () => apiError("AUTH.FORBIDDEN", 403)]], { detail: unmapped() });
    await heading("PO-2026-001");
    fireEvent.focus(screen.getByRole("combobox", { name: "라인 1 연결할 SKU" }));
    fireEvent.click(await screen.findByRole("option", { name: "SKU-008 진정 크림" }));
    fireEvent.click(screen.getByRole("button", { name: "품번 등록" }));
    const alert = await screen.findByText(/이 작업을 할 권한이 없습니다/);
    await waitFor(() => expect(alert).toHaveFocus());
    expect(screen.queryByRole("button", { name: "접수 확정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "인테이크 편집" })).not.toBeInTheDocument();
  });

  it("편집 403 — 권한 안내로 포커스", async () => {
    open([[url(21), "PATCH", () => apiError("AUTH.FORBIDDEN", 403)]]);
    await heading("PO-2026-001");
    fireEvent.change(editForm().getByLabelText("바이어 PO번호"), { target: { value: "PO-X" } });
    fireEvent.click(editForm().getByRole("button", { name: "수정 저장" }));
    const alert = await screen.findByText(/이 작업을 할 권한이 없습니다/);
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("편집 저장 성공 — 폼이 다시 시드되어도 포커스가 안내 영역으로 간다", async () => {
    open([[url(21), "PATCH", () => jsonResponse(intakeDetail({ version: 3, buyer_po_no: "PO-NEW" }))]]);
    await heading("PO-2026-001");
    fireEvent.change(editForm().getByLabelText("바이어 PO번호"), { target: { value: "PO-NEW" } });
    fireEvent.click(editForm().getByRole("button", { name: "수정 저장" }));
    const notice = await screen.findByRole("status", { name: "화면 안내" });
    await waitFor(() => expect(notice).toHaveTextContent("수정을 저장했습니다"));
    await waitFor(() => expect(notice).toHaveFocus());
  });

  it("품번 등록 성공 — 안내 영역으로 포커스", async () => {
    open([["/v1/partners/3/item-codes", "POST", () => jsonResponse({ id: 1 }, 201)]], { detail: unmapped() });
    await heading("PO-2026-001");
    fireEvent.focus(screen.getByRole("combobox", { name: "라인 1 연결할 SKU" }));
    fireEvent.click(await screen.findByRole("option", { name: "SKU-008 진정 크림" }));
    fireEvent.click(screen.getByRole("button", { name: "품번 등록" }));
    const notice = await screen.findByRole("status", { name: "화면 안내" });
    await waitFor(() => expect(notice).toHaveTextContent("바이어 품번 매핑을 등록했습니다"));
    await waitFor(() => expect(notice).toHaveFocus());
  });
});

describe("비활성 버튼 사유·문구", () => {
  it("거부 다이얼로그의 비활성 버튼 사유는 '거부할'로(확정이 아님)", async () => {
    open();
    await heading("PO-2026-001");
    fireEvent.click(screen.getByRole("button", { name: "거부" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "거부" })).toHaveAccessibleDescription("사유를 5자 이상 입력해야 거부할 수 있습니다.");
  });

  it("무변경 '수정 저장'·SKU 미선택 '품번 등록'은 비활성 사유를 읽을 수 있다", async () => {
    open([], { detail: intakeDetail({ lines: [intakeLine({ sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" })] }) });
    await heading("PO-2026-001");
    expect(editForm().getByRole("button", { name: "수정 저장" })).toHaveAccessibleDescription("바뀐 내용이 없어 저장할 수 없습니다.");
    expect(screen.getByRole("button", { name: "품번 등록" })).toHaveAccessibleDescription("연결할 SKU를 먼저 선택해야 등록할 수 있습니다.");
  });

  it("거부도 창 포커스 재조회로 앞서간 version이 아니라 화면 기준 version을 싣는다", async () => {
    const stub = open([[url(21, "/reject"), "POST", () => apiError("COMMON.CONCURRENCY.VERSION_CONFLICT", 409)]]);
    await heading("PO-2026-001");
    server[21] = intakeDetail({ version: 7 });
    refetchViaFocus();
    await screen.findByText(/다른 곳에서 이 인테이크가 수정되었습니다/);
    fireEvent.click(screen.getByRole("button", { name: "거부" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "충분히 긴 거부 사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "거부" }));
    await waitFor(() => expect(posts(stub.calls, url(21, "/reject"))).toHaveLength(1));
    expect(posts(stub.calls, url(21, "/reject"))[0]?.body).toEqual({ version: 2, reason: "충분히 긴 거부 사유" });
  });

  it("stale 배너·안내에 상태 코드(409 등)를 노출하지 않는다", async () => {
    open();
    await heading("PO-2026-001");
    server[21] = intakeDetail({ version: 5 });
    refetchViaFocus();
    const banner = await screen.findByText(/다른 곳에서 이 인테이크가 수정되었습니다/);
    expect(banner.textContent).not.toMatch(/\(\d{3}\)/);
  });

  it("수정 연결 끊김 — 같은 버튼 재시도가 아니라 반영 여부부터 확인하라고 안내(멱등 키 없음)", async () => {
    open([
      [
        url(21),
        "PATCH",
        () => {
          throw new TypeError("Failed to fetch");
        },
      ],
    ]);
    await heading("PO-2026-001");
    fireEvent.change(editForm().getByLabelText("바이어 PO번호"), { target: { value: "PO-X" } });
    fireEvent.click(editForm().getByRole("button", { name: "수정 저장" }));
    const alert = await editForm().findByRole("alert");
    expect(alert).toHaveTextContent("먼저 '최신 내용 불러오기'로 반영 여부를 확인");
    expect(alert).not.toHaveTextContent("같은 버튼");
    expect(editForm().getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("긴 바이어 PO번호는 좁은 화면에서 끊긴다(제목 nowrap 금지)", async () => {
    open([], { detail: intakeDetail({ buyer_po_no: "PO-".padEnd(120, "X") }) });
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1.querySelector("span")).toHaveClass("break-all");
    expect(h1.querySelector("span")).not.toHaveClass("cell-nowrap");
  });
});

describe("읽기 전용 역할의 매핑 안내", () => {
  it("미매핑·재해석 필요 라인에 '아래 품번 등록에서'·'품번 다시 확인으로' 같은 직접 조작을 안내하지 않는다", async () => {
    open([], {
      me: VIEWER,
      detail: intakeDetail({
        lines: [
          intakeLine({ id: 1, line_no: 1, sku_id: null, sku_code: null, sku_name_ko: null, sku_status: null, mapping_state: "UNMAPPED" }),
          intakeLine({ id: 2, line_no: 2, mapping_state: "STALE" }),
        ],
      }),
    });
    await heading("PO-2026-001");
    const table = within(screen.getByRole("table", { name: "인테이크 라인과 품번 매핑 상태" }));
    expect(table.queryByText(/아래 '품번 등록'에서/)).not.toBeInTheDocument();
    expect(table.queryByText(/'품번 다시 확인'으로/)).not.toBeInTheDocument();
    expect(table.getAllByText(/무역 담당자가/)).toHaveLength(2);
  });
});
