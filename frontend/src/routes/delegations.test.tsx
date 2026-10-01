// 대결 관리 — 내 위임·나에게 위임·상태 배지(INERT 사유)·종료 버튼(can_revoke)·등록 검증(KST 오늘·기간)·수임자 후보 검색·서버 거절 사유·멱등 키·종료 (S3-1 PR-9b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { ADMIN, delegation } from "../test/approval-fixtures";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

// KST 2026-10-01 23:30(UTC 14:30) — 브라우저 UTC 날짜(10-01)와 KST 날짜(10-01)가 같지만, 아래 경계 테스트는 UTC 날짜가 하루 늦은 시각을 쓴다.
let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const CANDIDATES = [
  { id: 4, display_name: "인증 담당" },
  { id: 5, display_name: "물류 담당" },
];
const ROWS = [
  delegation(),
  delegation({ id: 32, delegator_user_id: 4, delegator_name: "인증 담당", delegate_user_id: 1, delegate_name: "무역 담당", delegated_role: "CERT", can_revoke: false }),
  delegation({ id: 33, state: "INERT", inert_reason: "delegate_inactive", start_on: "2026-09-01", end_on: "2026-12-31" }),
  delegation({ id: 34, state: "EXPIRED", can_revoke: false }),
  delegation({ id: 35, state: "REVOKED", can_revoke: false }),
  delegation({ id: 36, state: "UPCOMING", start_on: "2026-11-01", end_on: "2026-11-05" }),
];
const ERR = (status: number, code: string, message: string, detail: Record<string, unknown> = {}) => () =>
  jsonResponse({ error: { code, message, detail } }, status);

function open(me: unknown = TRADER, extra: Array<[string, string, () => Response]> = [], rows = ROWS) {
  const stub = stubFetch(me, [
    ...extra,
    ["/v1/approvals/delegation-candidates", "GET", () => jsonResponse(page(CANDIDATES))],
    ["/v1/delegations", "GET", () => jsonResponse(page(rows))],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/delegations" });
  return stub;
}

describe("대결 — 목록", () => {
  it("내가 위임한 것과 나에게 위임된 것을 나눠 보이고 상태 배지·INERT 사유를 보인다", async () => {
    open();
    expect(await screen.findByRole("heading", { name: "대결 설정" })).toBeInTheDocument();
    const out = await screen.findByRole("region", { name: "내가 위임한 대결" });
    const into = screen.getByRole("region", { name: "나에게 위임된 대결" });
    expect(within(out).getAllByText("진행 중").length).toBe(1);
    expect(within(out).getByText("효력 없음")).toBeInTheDocument();
    expect(within(out).getByText("수임자 계정이 비활성입니다")).toBeInTheDocument();
    expect(within(out).getByText("기간 만료")).toBeInTheDocument();
    expect(within(out).getByText("종료됨")).toBeInTheDocument();
    expect(within(out).getByText("예정")).toBeInTheDocument();
    expect(within(into).getByText("인증 담당")).toBeInTheDocument();
    expect(within(out).getAllByText(/~/).length).toBe(5);
  });

  it("종료 버튼은 서버 can_revoke가 true인 행에만 있다", async () => {
    open();
    const out = await screen.findByRole("region", { name: "내가 위임한 대결" });
    expect(within(out).getAllByRole("button", { name: "종료" })).toHaveLength(3); // 31·33·36
    const into = screen.getByRole("region", { name: "나에게 위임된 대결" });
    expect(within(into).queryByRole("button", { name: "종료" })).not.toBeInTheDocument();
  });

  it("전체 보기는 ADMIN에게만 있고 scope=all로 나간다", async () => {
    const { calls } = open(ADMIN);
    const scope = await screen.findByLabelText("보기 범위");
    fireEvent.change(scope, { target: { value: "all" } });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain("/api/v1/delegations?scope=all"));
  });

  it("관리자가 아니면 보기 범위 선택이 없다", async () => {
    open();
    await screen.findByRole("heading", { name: "대결 설정" });
    expect(screen.queryByLabelText("보기 범위")).not.toBeInTheDocument();
  });

  it("빈 목록은 등록 안내를 보인다", async () => {
    open(TRADER, [], []);
    expect(await screen.findByText(/등록된 대결이 없습니다/)).toBeInTheDocument();
  });
});

describe("대결 — 등록", () => {
  async function pickDelegate() {
    const box = await screen.findByRole("combobox", { name: "수임자" });
    fireEvent.focus(box);
    fireEvent.click(await screen.findByRole("option", { name: "인증 담당" }));
  }

  it("수임자·역할·기간을 검증하고 서버 계약대로 POST한다(기본 기간은 KST 오늘)", async () => {
    const { calls } = open(TRADER, [["/v1/delegations", "POST", () => jsonResponse(delegation({ id: 40 }), 201)]], []);
    const form = await screen.findByRole("form", { name: "대결 등록" });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("수임자");
    await pickDelegate();
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("위임할 결재 역할");
    fireEvent.change(within(form).getByLabelText("위임할 결재 역할"), { target: { value: "TRADE" } });
    const today = (within(form).getByLabelText("시작일") as HTMLInputElement).value;
    expect(today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // 종료일이 시작일보다 앞서면 막는다.
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2099-01-10" } });
    fireEvent.change(within(form).getByLabelText("종료일"), { target: { value: "2099-01-05" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("종료일은 시작일 이후여야 합니다");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    fireEvent.change(within(form).getByLabelText("종료일"), { target: { value: "2099-01-12" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({
      delegate_user_id: 4,
      approval_type: "SO_CREDIT_EXCEEDED",
      delegated_role: "TRADE",
      start_on: "2099-01-10",
      end_on: "2099-01-12",
    });
    expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    expect(await screen.findByText("대결을 등록했습니다.")).toBeInTheDocument();
  });

  it("★ 소급(과거 시작일)은 화면에서 막는다(서버도 422) — 기본값은 KST 날짜", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-01T16:30:00Z") }); // KST 10-02 01:30, UTC 날짜는 10-01
    const { calls } = open(TRADER, [], []);
    const form = await screen.findByRole("form", { name: "대결 등록" });
    expect(within(form).getByLabelText("시작일")).toHaveValue("2026-10-02");
    await pickDelegate();
    fireEvent.change(within(form).getByLabelText("위임할 결재 역할"), { target: { value: "TRADE" } });
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("과거 날짜로는 대결을 등록할 수 없습니다");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("서버 거절 사유(겹침·본인 지정·조회 전용·미보유 역할)는 서버 한국어 문구 그대로 안내하고, 같은 본문 재시도는 같은 키", async () => {
    const { calls } = open(
      TRADER,
      [["/v1/delegations", "POST", ERR(422, "VALIDATION.INVALID_FIELD", "입력값이 올바르지 않습니다.", { delegated_role: "위임자가 보유하지 않은 역할은 위임할 수 없습니다. 위임자의 결재 역할을 확인해 주세요." })]],
      [],
    );
    const form = await screen.findByRole("form", { name: "대결 등록" });
    await pickDelegate();
    fireEvent.change(within(form).getByLabelText("위임할 결재 역할"), { target: { value: "CERT" } });
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2099-01-10" } });
    fireEvent.change(within(form).getByLabelText("종료일"), { target: { value: "2099-01-12" } });
    const send = async (n: number) => {
      fireEvent.click(within(form).getByRole("button", { name: "등록" }));
      await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(n));
      expect(await within(form).findByRole("alert")).toHaveTextContent("위임자가 보유하지 않은 역할은 위임할 수 없습니다");
      await waitFor(() => expect(within(form).getByRole("button", { name: "등록" })).toBeEnabled());
    };
    await send(1);
    await send(2);
    fireEvent.change(within(form).getByLabelText("종료일"), { target: { value: "2099-01-13" } });
    await send(3);
    const keys = calls.filter((c) => c.method === "POST").map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("기간 겹침(409)도 서버 문구를 그대로 보인다", async () => {
    open(TRADER, [["/v1/delegations", "POST", ERR(409, "APPROVALS.DELEGATION.OVERLAP", "같은 위임자·유형·역할의 대결 기간이 겹칩니다. 기존 대결을 종료하거나 기간을 조정해 주세요.")]], []);
    const form = await screen.findByRole("form", { name: "대결 등록" });
    await pickDelegate();
    fireEvent.change(within(form).getByLabelText("위임할 결재 역할"), { target: { value: "TRADE" } });
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2099-01-10" } });
    fireEvent.change(within(form).getByLabelText("종료일"), { target: { value: "2099-01-12" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("대결 기간이 겹칩니다");
  });

  it("관리자는 위임자를 지정해 대리 등록할 수 있다(비관리자에게는 위임자 칸이 없다)", async () => {
    open(TRADER, [], []);
    await screen.findByRole("form", { name: "대결 등록" });
    expect(screen.queryByRole("combobox", { name: "위임자" })).not.toBeInTheDocument();
  });

  it("ADMIN 대리 등록은 delegator_user_id를 싣는다", async () => {
    const { calls } = open(ADMIN, [["/v1/delegations", "POST", () => jsonResponse(delegation({ id: 41 }), 201)]], []);
    const form = await screen.findByRole("form", { name: "대결 등록" });
    await pickDelegate();
    const box = screen.getByRole("combobox", { name: "위임자" });
    fireEvent.focus(box);
    const options = await screen.findAllByRole("option", { name: "물류 담당" });
    fireEvent.click(options[0] as HTMLElement);
    fireEvent.change(within(form).getByLabelText("위임할 결재 역할"), { target: { value: "TRADE" } });
    fireEvent.change(within(form).getByLabelText("시작일"), { target: { value: "2099-01-10" } });
    fireEvent.change(within(form).getByLabelText("종료일"), { target: { value: "2099-01-12" } });
    // 타인 명의 부여는 안내 확인 없이는 보내지 않는다.
    expect(within(form).getByText(/물류 담당 님 명의로 대결 권한을 부여합니다 — 감사 기록에 남습니다/)).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("대리 등록 안내를 확인");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    fireEvent.click(within(form).getByRole("checkbox"));
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ delegate_user_id: 4, delegator_user_id: 5 });
  });
});

describe("대결 — 대리 등록 UX·배지 갱신", () => {
  it("위임자를 비우면 '본인 명의' 안내가 보이고, 위임자=수임자는 화면에서 막는다", async () => {
    const { calls } = open(ADMIN, [], []);
    const form = await screen.findByRole("form", { name: "대결 등록" });
    expect(within(form).getByText("선택하지 않으면 본인 명의로 등록됩니다.")).toBeInTheDocument();
    fireEvent.focus(await screen.findByRole("combobox", { name: "수임자" }));
    fireEvent.click(await screen.findByRole("option", { name: "인증 담당" }));
    fireEvent.focus(screen.getByRole("combobox", { name: "위임자" }));
    fireEvent.click((await screen.findAllByRole("option", { name: "인증 담당" }))[0] as HTMLElement);
    fireEvent.change(within(form).getByLabelText("위임할 결재 역할"), { target: { value: "TRADE" } });
    fireEvent.click(within(form).getByRole("button", { name: "등록" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("위임자와 수임자가 같을 수 없습니다.");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("등록·종료 뒤 결재함 배지(inbox-count)도 다시 조회한다", async () => {
    const { calls } = open(TRADER, [["/v1/delegations/31/revoke", "POST", () => jsonResponse(delegation({ state: "REVOKED", can_revoke: false, version: 2 }))]]);
    const out = await screen.findByRole("region", { name: "내가 위임한 대결" });
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/inbox-count")).length).toBeGreaterThan(0));
    const before = calls.filter((c) => c.url.includes("/inbox-count")).length;
    fireEvent.click(within(out).getAllByRole("button", { name: "종료" })[0] as HTMLElement);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "대결 종료" }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/inbox-count")).length).toBeGreaterThan(before));
  });
});

describe("대결 — 종료", () => {
  it("종료는 확인창을 거쳐 version을 싣고, 성공하면 안내한다", async () => {
    const { calls } = open(TRADER, [["/v1/delegations/31/revoke", "POST", () => jsonResponse(delegation({ state: "REVOKED", can_revoke: false, version: 2 }))]]);
    const out = await screen.findByRole("region", { name: "내가 위임한 대결" });
    fireEvent.click(within(out).getAllByRole("button", { name: "종료" })[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "대결 종료" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/revoke"))).toBeDefined());
    expect(calls.find((c) => c.url.endsWith("/revoke"))?.body).toEqual({ version: 1 });
    expect(await screen.findByText("대결을 종료했습니다.")).toBeInTheDocument();
  });

  it("이미 종료된 건(409 NOT_ACTIVE)은 서버 문구와 불러오기를 보인다", async () => {
    open(TRADER, [["/v1/delegations/31/revoke", "POST", ERR(409, "APPROVALS.DELEGATION.NOT_ACTIVE", "이미 종료되었거나 기간이 지난 대결입니다.")]]);
    const out = await screen.findByRole("region", { name: "내가 위임한 대결" });
    fireEvent.click(within(out).getAllByRole("button", { name: "종료" })[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "대결 종료" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("이미 종료되었거나 기간이 지난 대결입니다.");
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });
});
