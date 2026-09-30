// 정책 설정 화면 (S3-1 PR-4 · E8) — 화면 계약 고정. 실제 차단은 서버가 한다(§18.1).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";
import { bpToPercentText, percentToBp } from "./policies";

const ADMIN = { id: 9, email: "admin@example.com", display_name: "관리자", roles: ["ADMIN"] };

const MODE_UNSET = {
  key: "pi_advance_gate_mode",
  kind: "MODE",
  label_ko: "PI 선수금 게이트 모드",
  description_ko: "선수금 미입금 SO 확정 처리",
  allowed: ["OFF", "WARN", "BLOCK"],
  minimum: null,
  maximum: null,
  value: "BLOCK",
  source: "UNSET_DEFAULT",
  version: null,
  updated_by_name: null,
  updated_at: null,
};

const BP_SET = {
  key: "price_deviation_tolerance_bp",
  kind: "BASIS_POINTS",
  label_ko: "가격 편차 허용치",
  description_ko: "표준가 대비 허용 편차",
  allowed: [],
  minimum: 0,
  maximum: 1000,
  value: 500,
  source: "SET",
  version: 3,
  updated_by_name: "오너",
  updated_at: "2026-09-29T15:30:00Z",
};

const BP_UNSET = { ...BP_SET, value: 0, source: "UNSET_DEFAULT", version: null, updated_by_name: null, updated_at: null };

interface Puts {
  calls: { url: string; init: RequestInit }[];
}

function stubApi(me: unknown, items: unknown[], puts: Puts = { calls: [] }, putStatus = 200, putBody: unknown = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/alerts/unread-count")) return Promise.resolve(jsonResponse({ count: 0 }));
      if (input.includes("/v1/policies/") && init?.method === "PUT") {
        puts.calls.push({ url: input, init });
        return Promise.resolve(jsonResponse(putBody, putStatus));
      }
      if (input.includes("/v1/policies")) return Promise.resolve(jsonResponse(page(items)));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
  return puts;
}

function putCalls(): number {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
    (c: unknown[]) => (c[1] as RequestInit | undefined)?.method === "PUT",
  ).length;
}

async function openEdit(name: string) {
  fireEvent.click(await screen.findByRole("button", { name }));
}

describe("정책 설정 화면", () => {
  let uuid = 0;
  beforeEach(() => {
    uuid = 0;
    vi.stubGlobal("crypto", { randomUUID: () => `key-${++uuid}` });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("%↔bp 변환은 정수 연산이다", () => {
    expect(percentToBp("5")).toBe(500);
    expect(percentToBp("0.25")).toBe(25);
    expect(percentToBp("0.1")).toBe(10);
    expect(percentToBp("1.234")).toBeNull();
    expect(percentToBp("abc")).toBeNull();
    expect(bpToPercentText(500)).toBe("5%");
    expect(bpToPercentText(25)).toBe("0.25%");
    expect(bpToPercentText(10)).toBe("0.1%");
  });

  it("미설정은 적색 배지+글자, kind별 문구가 다르다", async () => {
    stubApi(ADMIN, [MODE_UNSET, BP_UNSET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });

    const modeBadge = await screen.findByText("미설정 — 기본 동작(차단)");
    expect(modeBadge.className).toContain("bg-red-100");
    const bpBadge = screen.getByText("미설정 — 기본값 0bp(편차 불허)");
    expect(bpBadge.className).toContain("bg-red-100");
  });

  it("설정됨은 배지·변경자·KST 시각·bp 병기를 보여 준다", async () => {
    stubApi(ADMIN, [BP_SET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });

    expect(await screen.findByText("설정됨")).toBeInTheDocument();
    expect(screen.getByText("5% (500bp)")).toBeInTheDocument();
    expect(screen.getByText("오너")).toBeInTheDocument();
    expect(screen.getByText(/2026\. 09\. 30\. 00:30 \(KST\)/)).toBeInTheDocument();
  });

  it("모드 셀렉트는 한국어 라벨, 저장 본문은 코드+version null", async () => {
    const puts = stubApi(ADMIN, [MODE_UNSET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("PI 선수금 게이트 모드 편집");

    const select = screen.getByRole("combobox");
    expect(within(select).getByRole("option", { name: "끔" })).toHaveValue("OFF");
    expect(within(select).getByRole("option", { name: "경고" })).toHaveValue("WARN");
    fireEvent.change(select, { target: { value: "WARN" } });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "시범 운영" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(puts.calls).toHaveLength(1));
    expect(puts.calls[0]!.url).toBe("/api/v1/policies/pi_advance_gate_mode");
    expect(JSON.parse(puts.calls[0]!.init.body as string)).toEqual({
      value: "WARN",
      version: null,
      reason: "시범 운영",
    });
  });

  it("허용치는 % 입력을 bp로 변환해 보내고 version 숫자를 싣는다, 저장 후 재조회", async () => {
    const puts = stubApi(ADMIN, [BP_SET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("가격 편차 허용치 편집");

    fireEvent.change(screen.getByLabelText("허용치 (%)"), { target: { value: "2.5" } });
    expect(screen.getByText(/= 250bp/)).toBeInTheDocument();
    expect(screen.getByText(/허용 범위 0%\(0bp\) ~ 10%\(1000bp\)/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "분기 조정" } });
    const listCallsBefore = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c: unknown[]) => c[0] === "/api/v1/policies",
    ).length;
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(puts.calls).toHaveLength(1));
    expect(JSON.parse(puts.calls[0]!.init.body as string)).toEqual({
      value: 250,
      version: 3,
      reason: "분기 조정",
    });
    await waitFor(() => {
      const after = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
        (c: unknown[]) => c[0] === "/api/v1/policies",
      ).length;
      expect(after).toBeGreaterThan(listCallsBefore);
    });
  });

  it("상한 초과 입력은 저장할 수 없다", async () => {
    stubApi(ADMIN, [BP_SET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("가격 편차 허용치 편집");

    fireEvent.change(screen.getByLabelText("허용치 (%)"), { target: { value: "10.01" } });
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "초과 시도" } });

    expect(screen.getByRole("alert")).toHaveTextContent("허용 범위를 벗어났습니다");
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
    expect(putCalls()).toBe(0);
  });

  it("사유가 없거나 1자면 저장 버튼이 비활성이다", async () => {
    stubApi(ADMIN, [MODE_UNSET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("PI 선수금 게이트 모드 편집");

    const save = screen.getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "가" } });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "가나" } });
    expect(save).toBeEnabled();
  });

  it("같은 본문 재시도는 같은 Idempotency-Key, 본문이 바뀌면 새 키", async () => {
    const puts = stubApi(ADMIN, [MODE_UNSET], { calls: [] }, 500, {
      error: { code: "X", message: "일시 오류", detail: {}, request_id: null },
    });
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("PI 선수금 게이트 모드 편집");

    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "사유" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(puts.calls).toHaveLength(1));
    await screen.findByText("일시 오류");
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(puts.calls).toHaveLength(2));
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "사유 수정" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(puts.calls).toHaveLength(3));

    const keys = puts.calls.map((c) => (c.init.headers as Record<string, string>)["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("409는 새로고침 안내 후 목록을 재조회한다", async () => {
    stubApi(ADMIN, [BP_SET], { calls: [] }, 409, {
      error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "충돌", detail: {}, request_id: null },
    });
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("가격 편차 허용치 편집");
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "동시 수정" } });
    const before = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c: unknown[]) => c[0] === "/api/v1/policies",
    ).length;
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(
      await screen.findByText("다른 관리자가 먼저 수정했습니다. 새로 고침 후 다시 저장해 주세요."),
    ).toBeInTheDocument();
    await waitFor(() => {
      const after = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
        (c: unknown[]) => c[0] === "/api/v1/policies",
      ).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("422는 서버 오류 봉투 message를 그대로 보여 준다", async () => {
    stubApi(ADMIN, [MODE_UNSET], { calls: [] }, 422, {
      error: { code: "X", message: "허용되지 않는 값입니다.", detail: {}, request_id: null },
    });
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    await openEdit("PI 선수금 게이트 모드 편집");
    fireEvent.change(screen.getByLabelText(/변경 사유/), { target: { value: "사유" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(await screen.findByText("허용되지 않는 값입니다.")).toBeInTheDocument();
  });

  it("ADMIN에게만 메뉴 항목이 보인다", async () => {
    stubApi(ADMIN, [MODE_UNSET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });
    expect(await screen.findByRole("link", { name: "정책 설정" })).toBeInTheDocument();
  });

  it("비관리자는 메뉴가 없고 URL로 들어와도 접근 불가 안내만 본다(조회도 하지 않는다)", async () => {
    stubApi(TRADER, [MODE_UNSET]);
    renderWithProviders(<AppRoutes />, { route: "/settings/policies" });

    expect(await screen.findByText(/접근할 수 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "정책 설정" })).not.toBeInTheDocument();
    const listed = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(
      (c: unknown[]) => c[0] === "/api/v1/policies",
    );
    expect(listed).toBe(false);
  });
});
