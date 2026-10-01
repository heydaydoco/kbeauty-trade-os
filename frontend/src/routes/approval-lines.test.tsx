// 결재선 관리 — 조회(4역할)·ADMIN 전용 쓰기 노출·fail-closed 안내·등록(검증·멱등 키)·수정(무변경 차단·version)·삭제·409·서버 거절 안내 (S3-1 PR-9b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type { Coverage } from "../lib/approval";
import { ADMIN, CURRENCY_HANDLER, line } from "../test/approval-fixtures";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const COVERED: Coverage = { configured: true, entries: [], messages: [] };
const EMPTY_COVERAGE: Coverage = { configured: false, entries: [], messages: ["결재선이 등록되지 않아 여신 초과 수주는 확정할 수 없습니다."] };

function open(me: unknown, extra: Array<[string, string, () => Response]> = [], rows = [line()], coverage = COVERED) {
  const stub = stubFetch(me, [
    ...extra,
    CURRENCY_HANDLER,
    ["/v1/approval-lines/coverage", "GET", () => jsonResponse(coverage)],
    ["/v1/approval-lines", "GET", () => jsonResponse(page(rows))],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/approval-lines" });
  return stub;
}
const ERR = (status: number, code: string, message: string, detail: Record<string, unknown> = {}) => () =>
  jsonResponse({ error: { code, message, detail } }, status);

describe("결재선 — 조회·권한 노출", () => {
  it("행(유형·통화·임계 서버 문자열·역할)과 fail-closed 안내를 보인다", async () => {
    open(ADMIN);
    expect(await screen.findByRole("heading", { name: "결재선" })).toBeInTheDocument();
    expect(await screen.findByText("5,000.00")).toBeInTheDocument();
    expect(within((await screen.findByText("5,000.00")).closest("tr") as HTMLElement).getByText("무역")).toBeInTheDocument();
    expect(screen.getByText("결재선이 없으면 승인 요청이 거절됩니다.")).toBeInTheDocument();
  });

  it("★ 결재선 미등록이면 서버 안내 문구와 빈 목록 조치를 보인다", async () => {
    open(ADMIN, [], [], EMPTY_COVERAGE);
    expect(await screen.findByText("결재선이 등록되지 않아 여신 초과 수주는 확정할 수 없습니다.")).toBeInTheDocument();
    expect(await screen.findByText(/등록된 결재선이 없습니다/)).toBeInTheDocument();
  });

  it("★ ADMIN이 아니면 조회만 — 등록 폼·수정·삭제가 없다(서버 authz_matrix와 같다)", async () => {
    open(TRADER);
    await screen.findByText("5,000.00");
    expect(screen.queryByRole("form", { name: "결재선 등록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "삭제" })).not.toBeInTheDocument();
    expect(screen.getByText(/관리자만 등록·수정·삭제/)).toBeInTheDocument();
  });
});

describe("결재선 — 등록", () => {
  async function fill(form: HTMLElement, threshold = "5000.50") {
    await within(form).findByRole("option", { name: "USD" });
    fireEvent.change(within(form).getByLabelText("통화"), { target: { value: "USD" } });
    fireEvent.change(within(form).getByLabelText("결재 역할"), { target: { value: "CERT" } });
    fireEvent.change(within(form).getByLabelText("임계 금액 (초과 시)"), { target: { value: threshold } });
  }

  it("필수값·금액 형식은 화면에서 막고, 올바르면 사람 표기 문자열 그대로 POST한다", async () => {
    const { calls } = open(ADMIN, [["/v1/approval-lines", "POST", () => jsonResponse(line({ id: 22 }), 201)]]);
    const form = await screen.findByRole("form", { name: "결재선 등록" });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("유형·통화·결재 역할을 모두 선택");
    await fill(form, "5,000");
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("임계 금액은 0 이상의 숫자");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    fireEvent.change(within(form).getByLabelText("임계 금액 (초과 시)"), { target: { value: "5000.50" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ approval_type: "SO_CREDIT_EXCEEDED", threshold: "5000.50", currency: "USD", approver_role: "CERT" });
    expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    expect(await screen.findByText("결재선을 등록했습니다.")).toBeInTheDocument();
  });

  it("중복(409)은 서버 문구를 보이고, 같은 본문 재시도는 같은 키 / 값이 바뀌면 새 키", async () => {
    const { calls } = open(ADMIN, [
      ["/v1/approval-lines", "POST", ERR(409, "APPROVALS.LINE.DUPLICATE", "같은 유형·통화·임계 금액의 결재선이 이미 있습니다.")],
    ]);
    const form = await screen.findByRole("form", { name: "결재선 등록" });
    await fill(form);
    const send = async (n: number) => {
      fireEvent.click(within(form).getByRole("button", { name: "등록" }));
      await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(n));
      expect(await within(form).findByRole("alert")).toHaveTextContent("같은 유형·통화·임계 금액의 결재선이 이미 있습니다.");
      await waitFor(() => expect(within(form).getByRole("button", { name: "등록" })).toBeEnabled());
    };
    await send(1);
    await send(2);
    fireEvent.change(within(form).getByLabelText("임계 금액 (초과 시)"), { target: { value: "6000" } });
    await send(3);
    const keys = calls.filter((c) => c.method === "POST").map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("더블클릭은 1회만 전송한다", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(ADMIN);
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (i?.method === "POST") {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    const form = await screen.findByRole("form", { name: "결재선 등록" });
    await fill(form);
    const submit = within(form).getByRole("button", { name: "등록" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    release(jsonResponse(line(), 201));
  });
});

describe("결재선 — 수정·삭제", () => {
  it("수정은 역할·메모만, 무변경이면 저장이 막히고, 바뀐 필드와 version만 PATCH한다", async () => {
    const { calls } = open(ADMIN, [["/v1/approval-lines/21", "PATCH", () => jsonResponse(line({ approver_role: "CERT", version: 3 }))]]);
    fireEvent.click(await screen.findByRole("button", { name: "수정" }));
    const save = screen.getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    const row = screen.getByRole("button", { name: "저장" }).closest("tr") as HTMLElement;
    fireEvent.change(within(row).getByLabelText("결재 역할"), { target: { value: "CERT" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toBeDefined());
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ version: 2, approver_role: "CERT" });
    expect(await screen.findByText(/결재선을 수정했습니다. 이미 요청된 승인에는 적용되지 않습니다/)).toBeInTheDocument();
  });

  it("수정 409는 '최신 내용 불러오기'를 보인다", async () => {
    open(ADMIN, [["/v1/approval-lines/21", "PATCH", ERR(409, "COMMON.CONCURRENCY.VERSION_CONFLICT", "충돌")]]);
    fireEvent.click(await screen.findByRole("button", { name: "수정" }));
    fireEvent.change(screen.getByLabelText("메모"), { target: { value: "메모" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("다른 곳에서 이 결재선 정보가 먼저 수정되었습니다");
    expect(within(alert).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("삭제는 확인창을 거쳐 version을 쿼리로 보낸다", async () => {
    const calls = open(ADMIN, [["/v1/approval-lines/21", "DELETE", () => ({ ok: true, status: 204, json: () => Promise.resolve(null) }) as unknown as Response]]).calls;
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("이미 요청된 승인에는 영향이 없지만");
    fireEvent.click(within(dialog).getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")).toBeDefined());
    expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/v1/approval-lines/21?version=2");
    expect(await screen.findByText("결재선을 삭제했습니다.")).toBeInTheDocument();
  });

  it("삭제 409는 다이얼로그 안에서 불러오기를 권한다", async () => {
    open(ADMIN, [["/v1/approval-lines/21", "DELETE", ERR(409, "COMMON.CONCURRENCY.VERSION_CONFLICT", "충돌")]]);
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "삭제" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("먼저 수정되었습니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
