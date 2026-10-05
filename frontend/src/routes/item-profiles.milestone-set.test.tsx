// 품목군 '마일스톤 세트' 섹션(S3-2 PR-4b, 그룹 K): 조회 = 전 역할, 추가·제거 = 관리자만(R-14 — 서버 이중 가드) · 선택지 = 선적 저장형 8종 중
// 세트에 없는 것 · 추가 본문·멱등 키 · 제거 경로 · 409 중복 서버 문구 + 세트 재조회 · 빈 세트 안내(구분별 전체 종류 폴백).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";
import { apiErrorResponse } from "../test/shipment-fixtures";
import type { ProfileMilestoneType } from "../lib/milestone";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ADMIN = { ...TRADER, id: 9, display_name: "관리자", roles: ["ADMIN"] };
const PROFILE = { id: 3, code: "SERUM", name_ko: "세럼류", description: null };
const SET = "/v1/item-profiles/3/milestone-types";
const link = (id: number, milestone_type: string): ProfileMilestoneType => ({ id, item_profile_id: 3, milestone_type, created_at: "2026-10-04T00:00:00Z" });
const server = { items: [] as ProfileMilestoneType[] };

function open(me: unknown, items: ProfileMilestoneType[], extra: GateHandler[] = []) {
  server.items = items;
  const stub = stubGateFetch(me, [
    ...extra,
    ["/v1/item-profiles", "GET", () => jsonResponse(page([PROFILE]))],
    [SET, "GET", () => jsonResponse(page(server.items))],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/item-profiles" });
  return stub;
}

const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);

async function openPanel() {
  fireEvent.click(await screen.findByRole("button", { name: "마일스톤 세트" }));
  return screen.findByRole("region", { name: "마일스톤 세트 — 세럼류" });
}

describe("품목군 마일스톤 세트", () => {
  it("무역 역할: 세트는 보이고(업무 흐름 순 그대로) 추가·제거 버튼 0 + '관리자가 합니다' 안내", async () => {
    open(TRADER, [link(11, "ETD"), link(12, "ETA")]);
    const panel = await openPanel();
    expect(await within(panel).findByText("ETD")).toBeInTheDocument();
    expect(within(panel).getByText("ETA")).toBeInTheDocument();
    expect(within(panel).queryAllByRole("button")).toHaveLength(0);
    expect(within(panel).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(panel).getByText("세트 추가·제거는 관리자가 합니다.")).toBeInTheDocument();
  });

  it("빈 세트 = '구분별 전체 종류' 폴백 안내, 세트 변경은 다음 초안부터", async () => {
    open(TRADER, []);
    const panel = await openPanel();
    expect(await within(panel).findByText(/세트가 비어 있습니다 — 선적 계획 초안은 구분별 전체 종류를 만듭니다/)).toBeInTheDocument();
    expect(within(panel).getByText(/세트 변경은 다음 초안부터 반영됩니다/)).toBeInTheDocument();
  });

  it("관리자: 선택지 = 선적 저장형 8종 중 세트에 없는 것(파생·OEM 종류 없음) → POST {milestone_type}·멱등 키", async () => {
    const stub = open(ADMIN, [link(11, "ETD")], [[SET, "POST", () => jsonResponse(link(13, "ETA"), 201)]]);
    const panel = await openPanel();
    await within(panel).findByText("ETD");
    const select = within(panel).getByLabelText("마일스톤 종류");
    const options = within(select).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["선택", "서류마감", "Cargo Closing", "수출 전 검사", "신고수리", "B/L(AWB) 발행일", "ETA", "수입 세금 납부기한"]);
    expect(options).not.toContain("대금만기");
    expect(options).not.toContain("충진");
    fireEvent.change(select, { target: { value: "ETA" } });
    fireEvent.click(within(panel).getByRole("button", { name: "세트에 추가" }));
    await waitFor(() => expect(sent(stub.calls, SET, "POST")).toHaveLength(1));
    const [call] = sent(stub.calls, SET, "POST");
    expect(call?.body).toEqual({ milestone_type: "ETA" });
    expect(call?.headers["Idempotency-Key"]).toMatch(/^key-/);
  });

  it("관리자 제거 → DELETE /item-profiles/3/milestone-types/{id}(글자 버튼), 세트 재조회", async () => {
    const stub = open(ADMIN, [link(11, "ETD")], [[`${SET}/11`, "DELETE", () => ({ ok: true, status: 204, json: () => Promise.reject(new Error("no body")) }) as Response]]);
    const panel = await openPanel();
    fireEvent.click(await within(panel).findByRole("button", { name: "ETD 제거" }));
    await waitFor(() => expect(sent(stub.calls, `${SET}/11`, "DELETE")).toHaveLength(1));
    await waitFor(() => expect(sent(stub.calls, SET, "GET").length).toBeGreaterThan(1));
  });

  it("409 중복(다른 곳에서 먼저 추가) → 서버 문구 그대로 + 세트를 다시 받는다", async () => {
    const stub = open(ADMIN, [], [
      [SET, "POST", () => jsonResponse(apiErrorResponse("SHIPMENTS.MILESTONE.DUPLICATE_TYPE", "이미 세트에 있는 종류입니다."), 409)],
    ]);
    const panel = await openPanel();
    await within(panel).findByText(/세트가 비어 있습니다/);
    const before = sent(stub.calls, SET, "GET").length;
    fireEvent.change(within(panel).getByLabelText("마일스톤 종류"), { target: { value: "ETD" } });
    fireEvent.click(within(panel).getByRole("button", { name: "세트에 추가" }));
    expect(await within(panel).findByText("이미 세트에 있는 종류입니다.")).toBeInTheDocument();
    await waitFor(() => expect(sent(stub.calls, SET, "GET").length).toBeGreaterThan(before));
  });
});
