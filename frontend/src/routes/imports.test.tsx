// 엑셀 왕복 임포트 화면 — 스테이징 검토·확정 1클릭 (§12.2 / ADR-0027).
//
// 이 파일이 고정하는 것: ① 스테이징 목록·행(신규/변경/오류)이 검토 가능한 형태로
// 보인다 — 변경 행은 "이전 → 이후", 오류 행은 사유. ② 확정은 멱등 키를 붙인
// POST 1번이다(이중 확인 모달 없음 — §17.4가 더블클릭을 흡수한다). ③ 업로드
// 폼·확정 버튼은 TRADE에게만 보인다(표시일 뿐, 차단은 서버 — §18.1).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

const REGISTRY = {
  items: [
    { code: "materials", label_ko: "자재", headers: ["ID", "자재코드"] },
    { code: "partners", label_ko: "거래처", headers: ["ID", "거래처코드"] },
  ],
};

const STAGING = {
  id: 1,
  registry_code: "partners",
  registry_label: "거래처",
  original_filename: "거래처_왕복.csv",
  sha256: "a".repeat(64),
  status: "PENDING",
  total_data_rows: 4,
  unchanged_rows: 1,
  new_rows: 1,
  changed_rows: 1,
  error_rows: 1,
  created_at: "2026-08-06T00:00:00Z",
  confirmed_at: null,
};

const ROWS = [
  {
    id: 11,
    row_no: 2,
    kind: "NEW",
    target_id: null,
    payload: { partner_code: "PTN-002", name_ko: "새 거래처" },
    changes: null,
    error_reason: null,
  },
  {
    id: 12,
    row_no: 3,
    kind: "CHANGED",
    target_id: 7,
    payload: { partner_code: "PTN-001", name_ko: "B" },
    changes: { 거래처명: ["A", "B"] },
    error_reason: null,
  },
  {
    id: 13,
    row_no: 4,
    kind: "ERROR",
    target_id: null,
    payload: {},
    changes: null,
    error_reason: "거래처명이(가) 비어 있습니다.",
  },
];

function stubApi(me: unknown) {
  const fetchMock = vi.fn((input: string, _init?: RequestInit) => {
    if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
    if (input.includes("/v1/imports/registry")) return Promise.resolve(jsonResponse(REGISTRY));
    if (input.includes("/v1/imports/staging/1/rows"))
      return Promise.resolve(jsonResponse(page(ROWS)));
    if (input.includes("/v1/imports/staging/1/confirm"))
      return Promise.resolve(
        jsonResponse({ id: 1, status: "CONFIRMED", created_rows: 1, updated_rows: 1 }),
      );
    if (input.includes("/v1/imports/staging")) return Promise.resolve(jsonResponse(page([STAGING])));
    return Promise.resolve(jsonResponse(page([])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** 스테이징 행을 눌러 아래 행 표가 뜰 때까지 — 행 표는 "행번호" 헤더로 찾는다. */
async function openStaging() {
  fireEvent.click(await screen.findByText("거래처_왕복.csv"));
  const header = await screen.findByRole("columnheader", { name: "행번호" });
  return within(header.closest("table") as HTMLElement);
}

describe("엑셀 임포트", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("스테이징 목록과 신규·변경·오류 행이 검토 가능한 형태로 보인다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/imports" });

    expect(await screen.findByRole("heading", { name: "엑셀 임포트" })).toBeInTheDocument();
    expect(await screen.findByText("거래처_왕복.csv")).toBeInTheDocument();

    // "신규/변경/오류"는 목록 표의 요약 헤더에도 있으니 행 표 안으로 좁힌다.
    const rowsTable = await openStaging();
    expect(rowsTable.getByText("신규")).toBeInTheDocument();
    expect(rowsTable.getByText("변경")).toBeInTheDocument();
    expect(rowsTable.getByText("오류")).toBeInTheDocument();
    // 변경 행은 "헤더: 이전 → 이후"로, 오류 행은 사유 그대로.
    expect(rowsTable.getByText(/거래처명: A → B/)).toBeInTheDocument();
    expect(rowsTable.getByText("거래처명이(가) 비어 있습니다.")).toBeInTheDocument();
  });

  it("확정은 멱등 키를 붙인 confirm POST 1번이다 (이중 확인 모달 없음 — §17.4)", async () => {
    const fetchMock = stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/imports" });

    await openStaging();
    fireEvent.click(screen.getByRole("button", { name: "확정(변경 반영)" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) =>
        String(url).includes("/v1/imports/staging/1/confirm"),
      );
      expect(call).toBeDefined();
      expect(call?.[1]?.method).toBe("POST");
      const headers = (call?.[1]?.headers ?? {}) as Record<string, string>;
      expect(headers["Idempotency-Key"]).toBe("test-key");
    });
  });

  it("조회 역할에게는 업로드 폼·확정 버튼이 없다 (표시일 뿐, 차단은 서버 — §18.1)", async () => {
    stubApi(VIEWER);
    renderWithProviders(<AppRoutes />, { route: "/imports" });

    // 목록·행 검토는 조회 역할도 볼 수 있다.
    await openStaging();

    expect(screen.queryByLabelText("대상")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("CSV 파일")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "업로드" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "확정(변경 반영)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "파기" })).not.toBeInTheDocument();
  });
});
