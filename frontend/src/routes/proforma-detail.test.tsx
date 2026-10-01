// PI 상세 — 상태별 버튼·동결 읽기 전용·취소 다이얼로그(사유·멱등 키·더블클릭)·낙관 잠금 409·meta 편집 (S3-1 PR-6b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { PAYMENT_SUMMARY } from "../test/payment-fixtures";
import { PI_LOG, piDetail } from "../test/pi-fixtures";
import { stubFetch } from "../test/qt-fixtures";
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
  ["/v1/proforma-invoices/5/status-log", "GET", () => jsonResponse(PI_LOG)],
  // 입금 패널(10b) — 상세 핸들러(/v1/proforma-invoices/5)가 접두 일치로 가로채지 않게 먼저 둔다.
  ["/v1/proforma-invoices/5/payments", "GET", () => jsonResponse({ ...page([]), summary: PAYMENT_SUMMARY })],
  ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
];

const server = { pi: piDetail() };

function open(
  pi: ReturnType<typeof piDetail>,
  extra: Array<[string, string, () => Response]> = [],
  me: unknown = TRADER,
) {
  server.pi = pi;
  const stub = stubFetch(me, [...extra, ...REFS, ["/v1/proforma-invoices/5", "GET", () => jsonResponse(server.pi)]]);
  renderWithProviders(<AppRoutes />, { route: "/proforma-invoices/5" });
  return stub;
}

const CONFLICT = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } }, 409);

describe("PI 상세 — 표시와 상태별 버튼", () => {
  it("헤더·서버 계산 금액·은행 스냅샷·선수금·라인·타임라인을 서버 값 그대로 보인다", async () => {
    open(piDetail());
    expect(await screen.findByRole("heading", { name: /PI-2026-0001/ })).toBeInTheDocument();
    expect(screen.getByText("선수금 T/T · 선수금 30% · 잔금 B/L일 기준 30일")).toBeInTheDocument();
    expect(screen.getByText("75.00 USD")).toBeInTheDocument();
    expect(screen.getAllByText("22.50 USD").length).toBeGreaterThan(0); // 선수금 청구액(서버 계산 — 입금 패널 요약에도 보인다)
    expect(screen.getByText("52.50 USD")).toBeInTheDocument();
    expect(screen.getByText("Shinhan Bank")).toBeInTheDocument();
    expect(screen.getByText("110-123-456789")).toBeInTheDocument();
    expect(screen.getByText("SHBKKRSE")).toBeInTheDocument();
    expect(screen.getByText("수분 세럼")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "QT-2026-0001" })).toHaveAttribute("href", "/quotations/7");
    // 타임라인 재사용
    expect(await screen.findByText("발행 (작성)")).toBeInTheDocument();
  });

  it("발행(미입금): 취소 버튼이 있다 / 헤더·라인 편집 UI는 어떤 상태에서도 없다(동결)", async () => {
    open(piDetail());
    await screen.findByRole("heading", { name: /PI-2026-0001/ });
    expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: /헤더/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "라인 추가" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "제외" })).not.toBeInTheDocument();
    expect(screen.getByRole("form", { name: "내부 메모·담당자" })).toBeInTheDocument();
  });

  it.each(["PARTIALLY_PAID", "PAID", "EXPIRED", "CANCELLED"])("%s: 취소 버튼이 없다", async (status) => {
    open(piDetail({ status }));
    await screen.findByRole("heading", { name: /PI-2026-0001/ });
    expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    // FREE 열 편집은 동결·종결 뒤에도 열려 있다.
    expect(screen.getByRole("form", { name: "내부 메모·담당자" })).toBeInTheDocument();
  });

  it("유효기간이 지난 발행 PI는 만료 배지를 보인다", async () => {
    open(piDetail({ is_lapsed: true }));
    expect(await screen.findByText("유효기간 경과")).toBeInTheDocument();
  });

  it("조회 전용 역할은 취소 버튼·메모 편집 폼이 없고 메모는 글로만 보인다", async () => {
    open(piDetail({ internal_note: "내부 참고" }), [], VIEWER);
    await screen.findByRole("heading", { name: /PI-2026-0001/ });
    expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "내부 메모·담당자" })).not.toBeInTheDocument();
    expect(screen.getByText("내부 메모: 내부 참고")).toBeInTheDocument();
  });

  it("없는 PI는 404 문구와 목록 링크", async () => {
    stubFetch(TRADER, [
      ["/v1/proforma-invoices/5", "GET", () => jsonResponse({ error: { code: "NOT_FOUND", message: "x" } }, 404)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/proforma-invoices/5" });
    expect(await screen.findByRole("alert")).toHaveTextContent("PI를 찾을 수 없습니다.");
    expect(screen.getByRole("link", { name: "PI 목록으로" })).toBeInTheDocument();
  });
});

describe("PI 상세 — 취소 다이얼로그", () => {
  it("사유가 비면 확정할 수 없고, 사유·기준 version·멱등 키와 함께 transitions로 간다 → 취소 상태가 반영된다", async () => {
    const cancelled = piDetail({ status: "CANCELLED", version: 3 });
    const { calls } = open(piDetail(), [
      ["/v1/proforma-invoices/5/transitions", "POST", () => jsonResponse((server.pi = cancelled))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/되돌릴 수 없습니다/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "취소 확정" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "  바이어 요청  " } });
    fireEvent.click(confirm);
    await waitFor(() => {
      const post = calls.find((c) => c.url.endsWith("/v1/proforma-invoices/5/transitions"));
      expect(post?.body).toEqual({ to: "CANCELLED", version: 2, reason: "바이어 요청" });
      expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument());
  });

  it("더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(piDetail());
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
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(1);
    release(jsonResponse(piDetail({ status: "CANCELLED", version: 3 })));
  });

  it("실패 뒤 재시도는 같은 멱등 키, 다이얼로그를 다시 열면 새 키", async () => {
    let fail = true;
    const { calls } = open(piDetail(), [
      [
        "/v1/proforma-invoices/5/transitions",
        "POST",
        () =>
          fail
            ? jsonResponse({ error: { code: "TRADE_DOCS.PAYMENT.RECEIVED", message: "입금이 붙은 PI는 취소할 수 없습니다." } }, 409)
            : jsonResponse((server.pi = piDetail({ status: "CANCELLED", version: 3 }))),
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("입금이 붙은 PI는 취소할 수 없습니다.");
    fail = false;
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(2));
    const keys = calls.filter((c) => c.url.endsWith("/transitions")).map((c) => c.headers["Idempotency-Key"]);
    expect(keys).toEqual(["key-1", "key-1"]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("재시도 사이에 사유가 바뀌면 새 키, 같은 사유면 같은 키", async () => {
    const { calls } = open(piDetail(), [
      ["/v1/proforma-invoices/5/transitions", "POST", () => jsonResponse({ error: { code: "X", message: "실패" } }, 500)],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    const reason = within(dialog).getByLabelText("취소 사유 (필수)");
    const send = async (n: number) => {
      fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
      await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(n));
      await within(dialog).findByRole("alert");
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "취소 확정" })).toBeEnabled());
    };
    fireEvent.change(reason, { target: { value: "사유 A" } });
    await send(1);
    await send(2); // 같은 사유 재시도
    fireEvent.change(reason, { target: { value: "사유 B" } });
    await send(3);
    const keys = calls.filter((c) => c.url.endsWith("/transitions")).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("다이얼로그를 닫았다 다시 열면 새 멱등 키를 쓴다", async () => {
    const { calls } = open(piDetail(), [
      ["/v1/proforma-invoices/5/transitions", "POST", () => jsonResponse({ error: { code: "X", message: "실패" } }, 500)],
    ]);
    for (let attempt = 0; attempt < 2; attempt++) {
      fireEvent.click(await screen.findByRole("button", { name: "취소" }));
      const dialog = await screen.findByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
      await within(dialog).findByRole("alert");
      fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }
    const keys = calls.filter((c) => c.url.endsWith("/transitions")).map((c) => c.headers["Idempotency-Key"]);
    expect(keys).toEqual(["key-1", "key-2"]);
  });

  it("취소 409(낙관 잠금)는 다이얼로그 안에 '최신 내용 불러오기'를 두고, 누르면 재조회 후 닫힌다", async () => {
    const { calls } = open(piDetail(), [["/v1/proforma-invoices/5/transitions", "POST", CONFLICT]]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다른 곳에서 이 PI 정보가 먼저 수정되었습니다");
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/proforma-invoices/5")).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/proforma-invoices/5")).length).toBeGreaterThan(before),
    );
  });

  it("Esc로 닫히고 포커스가 연 버튼으로 돌아온다", async () => {
    open(piDetail());
    const opener = await screen.findByRole("button", { name: "취소" });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });
});

describe("PI 상세 — meta(FREE 열)·낙관 잠금", () => {
  it("메모·담당자 저장은 meta로 기준 version과 함께 PATCH하고 응답 version이 새 기준이 된다", async () => {
    const { calls } = open(piDetail(), [
      [
        "/v1/proforma-invoices/5/meta",
        "PATCH",
        () => jsonResponse((server.pi = piDetail({ internal_note: "추가 메모", version: 3 }))),
      ],
    ]);
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: " 추가 메모 " } });
    fireEvent.click(within(form).getByRole("button", { name: "메모·담당자 저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.url).toMatch(/\/v1\/proforma-invoices\/5\/meta$/);
      expect(patch?.body).toEqual({ version: 2, internal_note: "추가 메모", assignee_id: 1 });
    });
    // 가격·조건 필드는 구조적으로 보내지 않는다.
    const patch = calls.find((c) => c.method === "PATCH");
    for (const key of ["total_amount", "status", "bank", "lines", "currency"]) {
      expect(patch?.body).not.toHaveProperty(key);
    }
  });

  it("meta 저장 409는 안내와 '최신 내용 불러오기'를 보인다", async () => {
    open(piDetail(), [["/v1/proforma-invoices/5/meta", "PATCH", CONFLICT]]);
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "변경" } });
    fireEvent.click(within(form).getByRole("button", { name: "메모·담당자 저장" }));
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("다른 곳에서 이 PI 정보가 먼저 수정되었습니다");
    expect(within(banner).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("창 포커스 재조회로 서버 version이 앞서가도 저장은 화면이 본 version으로 나가 409로 막히고, 배너가 뜬다", async () => {
    const { calls } = open(piDetail(), [["/v1/proforma-invoices/5/meta", "PATCH", CONFLICT]]);
    await screen.findByRole("form", { name: "내부 메모·담당자" });
    server.pi = piDetail({ version: 3, internal_note: "남이 고침" });
    window.dispatchEvent(new Event("visibilitychange"));
    expect(await screen.findByText(/다른 곳에서 이 PI가 수정되었습니다/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("내부 메모"), { target: { value: "내 메모" } });
    fireEvent.click(screen.getByRole("button", { name: "메모·담당자 저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toMatchObject({ version: 2 }); // 3이 아니다
    });
  });

  it("바뀐 게 없으면 저장 버튼이 비활성이고 전송하지 않는다", async () => {
    const { calls } = open(piDetail());
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    const save = within(form).getByRole("button", { name: "메모·담당자 저장" });
    expect(save).toBeDisabled();
    fireEvent.submit(form);
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "x" } });
    expect(save).toBeEnabled();
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "" } });
    expect(save).toBeDisabled();
  });

  it("창 포커스 재조회로 서버 version이 앞서면 취소도 화면 기준 version을 싣는다", async () => {
    const { calls } = open(piDetail(), [["/v1/proforma-invoices/5/transitions", "POST", CONFLICT]]);
    await screen.findByRole("button", { name: "취소" });
    server.pi = piDetail({ version: 3 });
    window.dispatchEvent(new Event("visibilitychange"));
    await screen.findByText(/다른 곳에서 이 PI가 수정되었습니다/);
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    await waitFor(() => {
      const post = calls.find((c) => c.url.endsWith("/transitions"));
      expect(post?.body).toEqual({ to: "CANCELLED", version: 2, reason: "사유" });
    });
  });

  it("'최신 내용 불러오기' 뒤에는 새 값으로 폼이 다시 시작되고 기준 version이 갱신된다", async () => {
    const { calls } = open(piDetail(), [
      ["/v1/proforma-invoices/5/meta", "PATCH", () => jsonResponse((server.pi = piDetail({ version: 4 })))],
    ]);
    await screen.findByRole("form", { name: "내부 메모·담당자" });
    server.pi = piDetail({ version: 3, internal_note: "남이 고침" });
    fireEvent.click(screen.getAllByRole("button", { name: "최신 내용 불러오기" })[0] as HTMLElement);
    await waitFor(() => expect(screen.getByLabelText("내부 메모")).toHaveValue("남이 고침"));
    fireEvent.change(screen.getByLabelText("내부 메모"), { target: { value: "내가 고침" } });
    fireEvent.click(screen.getByRole("button", { name: "메모·담당자 저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toMatchObject({ version: 3 });
    });
  });
});
