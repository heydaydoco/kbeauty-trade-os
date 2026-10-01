// 발주 상세 — Full/CostHidden 두 응답 렌더·역할별 버튼·상태별 버튼·동결 읽기 전용·OC/취소 다이얼로그(필수값·멱등 키·더블클릭·409)·meta·낙관 잠금 (S3-1 PR-8b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { PO_LOG, SENTINELS, poDetail, poDetailHidden } from "../test/po-fixtures";
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
  ["/v1/purchase-orders/9/status-log", "GET", () => jsonResponse(PO_LOG)],
  ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }, { id: 2, display_name: "관리자" }]))],
];

const server = { po: poDetail() };

function open(
  po: ReturnType<typeof poDetail>,
  extra: Array<[string, string, () => Response]> = [],
  me: unknown = TRADER,
) {
  server.po = po;
  const stub = stubFetch(me, [...extra, ...REFS, ["/v1/purchase-orders/9", "GET", () => jsonResponse(server.po)]]);
  renderWithProviders(<AppRoutes />, { route: "/purchase-orders/9" });
  return stub;
}

const transitionCalls = (calls: Call[]) => calls.filter((c) => c.url.endsWith("/transitions"));
const keysOf = (calls: Call[]) => transitionCalls(calls).map((c) => c.headers["Idempotency-Key"]);
const CONFLICT = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } }, 409);

describe("발주 상세 — Full 응답(원가 열람 역할)", () => {
  it("헤더·서버 계산 금액·라인·타임라인을 서버 값 그대로 보인다", async () => {
    open(poDetail());
    expect(await screen.findByRole("heading", { name: /PO-2026-0001/ })).toBeInTheDocument();
    expect(screen.getByText("후불 T/T · 잔금 입고 확정일 기준 30일")).toBeInTheDocument();
    expect(screen.getByText("1350.5 (기준일 2026-09-29)")).toBeInTheDocument();
    expect(screen.getByText("Seoul Supplier Co.")).toBeInTheDocument();
    expect(screen.getAllByText("7654321.98").length).toBe(1); // 단가(서버 문자열)
    expect(screen.getByText("76543219.80")).toBeInTheDocument(); // 라인 금액
    expect(screen.getByText("76543219.87 USD")).toBeInTheDocument(); // 합계
    expect(screen.getByRole("columnheader", { name: "단가" })).toBeInTheDocument();
    expect(screen.getByText("마스터")).toBeInTheDocument();
    expect(await screen.findByText("발행 (작성)")).toBeInTheDocument();
  });

  it("발행: 공급사 확인·취소 버튼이 있고 라인·헤더 편집 UI는 없다(동결) / 메모·담당자 폼만", async () => {
    open(poDetail());
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.getByRole("button", { name: "공급사 확인(OC)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: /헤더/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "제외" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "라인 추가" })).not.toBeInTheDocument();
    const form = screen.getByRole("form", { name: "내부 메모·담당자" });
    // 발행 중에는 OC 열 입력란이 없다(서버도 422).
    expect(within(form).queryByLabelText("OC 일자")).not.toBeInTheDocument();
  });

  it("공급사 확인: 취소만 가능(OC 버튼 없음) + OC 일자·참조 편집란이 열린다", async () => {
    open(poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", oc_reference: "OC-77" }));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.queryByRole("button", { name: "공급사 확인(OC)" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
    const form = screen.getByRole("form", { name: /내부 메모·담당자/ });
    expect(within(form).getByLabelText("OC 일자")).toHaveValue("2026-10-01");
    expect(within(form).getByLabelText("OC 참조")).toHaveValue("OC-77");
  });

  it.each(["PARTIALLY_RECEIVED", "FULLY_RECEIVED", "CLOSED"])("%s: 전이 버튼이 없다", async (status) => {
    open(poDetail({ status }));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "공급사 확인(OC)" })).not.toBeInTheDocument();
  });

  it("취소됨: 전이 버튼도 메모 편집 폼도 없고 메모는 글로만 보인다(읽기 전용)", async () => {
    open(poDetail({ status: "CANCELLED", internal_note: "취소 건 메모" }));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "공급사 확인(OC)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: /내부 메모·담당자/ })).not.toBeInTheDocument();
    expect(screen.getByText("내부 메모: 취소 건 메모")).toBeInTheDocument();
  });

  it("없는 발주는 404 문구와 목록 링크", async () => {
    stubFetch(TRADER, [
      ["/v1/purchase-orders/9", "GET", () => jsonResponse({ error: { code: "NOT_FOUND", message: "x" } }, 404)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders/9" });
    expect(await screen.findByRole("alert")).toHaveTextContent("발주를 찾을 수 없습니다.");
    expect(screen.getByRole("link", { name: "발주 목록으로" })).toBeInTheDocument();
  });
});

describe("발주 상세 — CostHidden 응답(원가 키 없음): 깨지지 않고 원가가 어디에도 없다", () => {
  it("원가 열·합계·통화·환율이 없고, 센티널 원가가 화면·저장소·콘솔 어디에도 없다 / 쓰기 버튼 없음", async () => {
    const logs: string[] = [];
    for (const method of ["log", "info", "warn", "error", "debug"] as const) {
      vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        logs.push(args.map((arg) => String(arg)).join(" "));
      });
    }
    const { calls } = open(poDetailHidden({ internal_note: "참고용" }), [], VIEWER);
    expect(await screen.findByRole("heading", { name: /PO-2026-0001/ })).toBeInTheDocument();
    // 라인은 보이고(SKU·품명·수량·요청납기) 원가 열은 없다.
    expect(screen.getByText("수분 세럼")).toBeInTheDocument();
    for (const name of ["단가", "금액", "기준"]) {
      expect(screen.queryByRole("columnheader", { name })).not.toBeInTheDocument();
    }
    for (const label of ["통화", "환율"]) expect(screen.queryByText(label)).not.toBeInTheDocument();
    expect(screen.queryByText(/합계/)).not.toBeInTheDocument();
    expect(screen.queryByText(/USD/)).not.toBeInTheDocument();
    expect(screen.queryByText(/undefined|NaN/)).not.toBeInTheDocument();
    expect(await screen.findByText("발행 (작성)")).toBeInTheDocument();
    for (const sentinel of SENTINELS) {
      expect(document.body.textContent).not.toContain(sentinel);
      expect(logs.join("\n")).not.toContain(sentinel);
      expect(JSON.stringify({ ...localStorage })).not.toContain(sentinel);
      expect(JSON.stringify({ ...sessionStorage })).not.toContain(sentinel);
      expect(calls.map((c) => c.url).join("\n")).not.toContain(sentinel);
    }
    // 쓰기 버튼·폼 없음 / 메모는 글로만.
    expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "공급사 확인(OC)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "내부 메모·담당자" })).not.toBeInTheDocument();
    expect(screen.getByText("내부 메모: 참고용")).toBeInTheDocument();
    // 조회 전용 역할은 담당자 후보(무역 전용 API)도 부르지 않는다.
    expect(calls.some((c) => c.url.includes("/v1/users/lookup"))).toBe(false);
  });

  it("같은 PO를 Full로 열면 같은 센티널이 보인다(마스킹이 화면 로직이 아니라 응답 키 유무임을 대조)", async () => {
    open(poDetail());
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(document.body.textContent).toContain("76543219.87");
  });

  it("CostHidden 취소 상태·공급사 확인 상태도 깨지지 않는다(OC 값은 보인다)", async () => {
    open(poDetailHidden({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", oc_reference: "OC-77" }), [], VIEWER);
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.getByText("2026-10-01")).toBeInTheDocument();
    expect(screen.getByText("OC-77")).toBeInTheDocument();
  });
});

describe("발주 상세 — 원가 입력 금지 안내(ADR-0057)", () => {
  const WARNING = "원가(단가·금액)를 적지 마세요 — 원가 열람 권한이 없는 사용자에게도 보입니다.";

  it("내부 메모 입력란 옆에 안내가 있다", async () => {
    open(poDetail());
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    expect(within(form).getByText(WARNING)).toBeInTheDocument();
  });

  it("취소 사유·OC 참조 입력란 옆에도 안내가 있다", async () => {
    open(poDetail());
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    expect(within(await screen.findByRole("dialog")).getByText(WARNING)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "공급사 확인(OC)" }));
    expect(within(await screen.findByRole("dialog")).getByText(WARNING)).toBeInTheDocument();
  });
});

describe("발주 상세 — 공급사 확인(OC) 다이얼로그", () => {
  it("OC 일자가 비면 확정할 수 없고, 일자·참조·기준 version·멱등 키와 함께 transitions로 간다 → 상태가 반영된다", async () => {
    const confirmed = poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", oc_reference: "OC-77", version: 3 });
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/transitions", "POST", () => jsonResponse((server.po = confirmed))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "공급사 확인(OC)" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "공급사 확인 기록" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(transitionCalls(calls)).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText("OC 일자 (필수)"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(dialog).getByLabelText("OC 참조 (선택)"), { target: { value: " OC-77 " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => {
      const post = transitionCalls(calls)[0];
      expect(post?.body).toEqual({ to: "SUPPLIER_CONFIRMED", version: 2, oc_received_on: "2026-10-01", oc_reference: "OC-77" });
      expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole("button", { name: "공급사 확인(OC)" })).not.toBeInTheDocument());
    expect(screen.getByText("공급사 확인", { selector: "span" })).toBeInTheDocument();
  });

  it("OC 참조를 비우면 본문에 싣지 않는다", async () => {
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/transitions", "POST", () => jsonResponse(poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", version: 3 }))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "공급사 확인(OC)" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("OC 일자 (필수)"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "공급사 확인 기록" }));
    await waitFor(() => {
      expect(transitionCalls(calls)[0]).toMatchObject({ body: { to: "SUPPLIER_CONFIRMED", version: 2, oc_received_on: "2026-10-01" } });
    });
    expect(transitionCalls(calls)[0]?.body).not.toHaveProperty("oc_reference");
    expect(transitionCalls(calls)[0]?.body).not.toHaveProperty("reason");
  });

  it("더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(poDetail());
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith("/transitions")) {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "공급사 확인(OC)" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("OC 일자 (필수)"), { target: { value: "2026-10-01" } });
    const confirm = within(dialog).getByRole("button", { name: "공급사 확인 기록" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(transitionCalls(calls)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(transitionCalls(calls)).toHaveLength(1);
    release(jsonResponse(poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", version: 3 })));
  });

  it("서버 검증 오류(OC 일자 경계)는 다이얼로그 안에 보이고, 같은 본문 재시도는 같은 멱등 키 / OC 값이 바뀌면 새 키", async () => {
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/transitions", "POST", () => jsonResponse({ error: { code: "VALIDATION.INVALID_FIELD", message: "OC 일자는 오늘(KST)보다 미래일 수 없습니다." } }, 422)],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "공급사 확인(OC)" }));
    const dialog = await screen.findByRole("dialog");
    const date = within(dialog).getByLabelText("OC 일자 (필수)");
    const send = async (n: number) => {
      fireEvent.click(within(dialog).getByRole("button", { name: "공급사 확인 기록" }));
      await waitFor(() => expect(transitionCalls(calls)).toHaveLength(n));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("OC 일자는 오늘(KST)보다 미래일 수 없습니다.");
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "공급사 확인 기록" })).toBeEnabled());
    };
    fireEvent.change(date, { target: { value: "2099-01-01" } });
    await send(1);
    await send(2);
    fireEvent.change(date, { target: { value: "2026-10-01" } });
    await send(3);
    const keys = keysOf(calls);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("409는 다이얼로그 안에 '최신 내용 불러오기'를 두고, 누르면 재조회 후 닫힌다", async () => {
    const { calls } = open(poDetail(), [["/v1/purchase-orders/9/transitions", "POST", CONFLICT]]);
    fireEvent.click(await screen.findByRole("button", { name: "공급사 확인(OC)" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("OC 일자 (필수)"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "공급사 확인 기록" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다른 곳에서 이 발주 정보가 먼저 수정되었습니다");
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/purchase-orders/9")).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/purchase-orders/9")).length).toBeGreaterThan(before),
    );
  });

  it("Esc로 닫히고 포커스가 연 버튼으로 돌아온다 / Tab은 다이얼로그 안을 돈다", async () => {
    open(poDetail());
    const opener = await screen.findByRole("button", { name: "공급사 확인(OC)" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("OC 일자 (필수)")).toHaveFocus();
    const close = within(dialog).getByRole("button", { name: "닫기" });
    const record = within(dialog).getByRole("button", { name: "공급사 확인 기록" });
    expect(record).toBeDisabled();
    close.focus();
    fireEvent.keyDown(document, { key: "Tab" }); // 마지막 활성 요소(닫기) → 처음으로
    expect(within(dialog).getByLabelText("OC 일자 (필수)")).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });
});

describe("발주 상세 — 취소 다이얼로그", () => {
  it("사유가 비면 확정할 수 없고, 사유·기준 version·멱등 키와 함께 transitions로 간다 → 취소 상태가 반영된다", async () => {
    const cancelled = poDetail({ status: "CANCELLED", version: 3 });
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/transitions", "POST", () => jsonResponse((server.po = cancelled))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/되돌릴 수 없습니다/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "취소 확정" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "  공급사 사정  " } });
    fireEvent.click(confirm);
    await waitFor(() => {
      const post = transitionCalls(calls)[0];
      expect(post?.body).toEqual({ to: "CANCELLED", version: 2, reason: "공급사 사정" });
      expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument());
    expect(screen.queryByRole("form", { name: /내부 메모·담당자/ })).not.toBeInTheDocument();
  });

  it("공급사 확인 상태에서도 취소할 수 있다", async () => {
    const { calls } = open(poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01" }), [
      ["/v1/purchase-orders/9/transitions", "POST", () => jsonResponse(poDetail({ status: "CANCELLED", version: 3 }))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    await waitFor(() => expect(transitionCalls(calls)).toHaveLength(1));
  });

  it("더블클릭은 1회만 전송한다", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(poDetail());
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith("/transitions")) {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    const confirm = within(dialog).getByRole("button", { name: "취소 확정" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(transitionCalls(calls)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(transitionCalls(calls)).toHaveLength(1);
    release(jsonResponse(poDetail({ status: "CANCELLED", version: 3 })));
  });

  it("실패 뒤 같은 사유 재시도는 같은 키, 사유가 바뀌면 새 키, 닫았다 다시 열면 새 키", async () => {
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/transitions", "POST", () => jsonResponse({ error: { code: "X", message: "실패" } }, 500)],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    let dialog = await screen.findByRole("dialog");
    const send = async (n: number) => {
      fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
      await waitFor(() => expect(transitionCalls(calls)).toHaveLength(n));
      await within(dialog).findByRole("alert");
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "취소 확정" })).toBeEnabled());
    };
    const reason = within(dialog).getByLabelText("취소 사유 (필수)");
    fireEvent.change(reason, { target: { value: "사유 A" } });
    await send(1);
    await send(2);
    fireEvent.change(reason, { target: { value: "사유 B" } });
    await send(3);
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유 B" } });
    await send(4);
    const keys = keysOf(calls);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
    expect(keys[3]).not.toBe(keys[2]);
  });
});

describe("발주 상세 — meta(FREE 열)·낙관 잠금", () => {
  it("메모 저장은 바뀐 열만 기준 version과 함께 PATCH하고 응답 version이 새 기준이 된다(가격·조건 필드 없음)", async () => {
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/meta", "PATCH", () => jsonResponse((server.po = poDetail({ internal_note: "추가 메모", version: 3 })))],
    ]);
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: " 추가 메모 " } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.url).toMatch(/\/v1\/purchase-orders\/9\/meta$/);
      expect(patch?.body).toEqual({ version: 2, internal_note: "추가 메모" });
    });
    const patch = calls.find((c) => c.method === "PATCH");
    for (const key of ["total_cost", "status", "lines", "currency", "oc_received_on", "unit_cost"]) {
      expect(patch?.body).not.toHaveProperty(key);
    }
  });

  it("공급사 확인 상태에서 OC 일자·참조·담당자를 고치면 해당 열만 보낸다 / OC 일자를 비우면 화면이 막는다", async () => {
    const { calls } = open(poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-01", oc_reference: "A" }), [
      ["/v1/purchase-orders/9/meta", "PATCH", () => jsonResponse(poDetail({ status: "SUPPLIER_CONFIRMED", oc_received_on: "2026-10-02", assignee_id: 2, version: 3 }))],
    ]);
    const form = await screen.findByRole("form", { name: /내부 메모·담당자/ });
    fireEvent.change(within(form).getByLabelText("OC 일자"), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("OC 일자는 비울 수 없습니다.");
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    fireEvent.change(within(form).getByLabelText("OC 일자"), { target: { value: "2026-10-02" } });
    fireEvent.change(within(form).getByLabelText("OC 참조"), { target: { value: "" } });
    await screen.findByRole("option", { name: "관리자" });
    fireEvent.change(within(form).getByLabelText("담당자"), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toEqual({ version: 2, assignee_id: 2, oc_received_on: "2026-10-02", oc_reference: null });
    });
  });

  it("담당자 422(무역·관리자 아님)는 서버 문구를 배너로 보인다", async () => {
    open(poDetail(), [
      ["/v1/purchase-orders/9/meta", "PATCH", () => jsonResponse({ error: { code: "VALIDATION.INVALID_FIELD", message: "담당자는 무역 또는 관리자여야 합니다." } }, 422)],
    ]);
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    await screen.findByRole("option", { name: "관리자" });
    fireEvent.change(within(form).getByLabelText("담당자"), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("담당자는 무역 또는 관리자여야 합니다.");
  });

  it("meta 저장 409는 안내와 '최신 내용 불러오기'를 보인다", async () => {
    open(poDetail(), [["/v1/purchase-orders/9/meta", "PATCH", CONFLICT]]);
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "변경" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("다른 곳에서 이 발주 정보가 먼저 수정되었습니다");
    expect(within(banner).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("바뀐 게 없으면 저장 버튼이 비활성이고 전송하지 않는다(무변경 저장 차단)", async () => {
    const { calls } = open(poDetail());
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    const save = within(form).getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    fireEvent.submit(form);
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "x" } });
    expect(save).toBeEnabled();
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "" } });
    expect(save).toBeDisabled();
  });

  it("서버 version이 앞서가면 배너가 뜨고 전이 버튼이 막히며, 저장은 화면이 본 version으로 나가 409로 막힌다", async () => {
    const { calls } = open(poDetail(), [["/v1/purchase-orders/9/meta", "PATCH", CONFLICT]]);
    await screen.findByRole("form", { name: "내부 메모·담당자" });
    server.po = poDetail({ version: 3, internal_note: "남이 고침" });
    window.dispatchEvent(new Event("visibilitychange"));
    expect(await screen.findByText(/다른 곳에서 이 발주가 수정되었습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "공급사 확인(OC)" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("내부 메모"), { target: { value: "내 메모" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toMatchObject({ version: 2 }); // 3이 아니다
    });
  });

  it("'최신 내용 불러오기' 뒤에는 새 값으로 폼이 다시 시작되고 기준 version이 갱신된다", async () => {
    const { calls } = open(poDetail(), [
      ["/v1/purchase-orders/9/meta", "PATCH", () => jsonResponse((server.po = poDetail({ version: 4 })))],
    ]);
    await screen.findByRole("form", { name: "내부 메모·담당자" });
    server.po = poDetail({ version: 3, internal_note: "남이 고침" });
    fireEvent.click(screen.getAllByRole("button", { name: "최신 내용 불러오기" })[0] as HTMLElement);
    await waitFor(() => expect(screen.getByLabelText("내부 메모")).toHaveValue("남이 고침"));
    fireEvent.change(screen.getByLabelText("내부 메모"), { target: { value: "내가 고침" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toMatchObject({ version: 3 });
    });
  });
});
