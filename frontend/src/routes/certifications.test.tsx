// 인증 화면 — 최소 렌더 + 권한별 편집 UI + 전이 요청 계약 (S2-2 판정 ⑫).
//
// 실제 차단은 서버가 한다(§18.1) — 여기서 고정하는 것은 ① 화면 게이트 표시
// 규칙(인증+관리자만 등록·전이 UI) ② 전이 요청이 version(낙관 잠금 §17.2)을
// 그대로 되돌려 보낸다는 계약 ③ 종결 상태에는 전이 폼이 없다는 것(전이 표
// UX 사본 — 정본은 서버 machine.py) ④ 이력의 시스템(자동) 행위자 표시.

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const CERT_USER = { id: 2, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

const ROW = {
  id: 1,
  template_id: 10,
  market_code: "US",
  template_name: "MoCRA 제품 리스팅",
  requirement_type: "REGISTRATION",
  target_type: "SKU",
  target_id: 5,
  target_label: "수분 세럼 30ml",
  status: "NOT_STARTED",
  validity_months: null,
  renewal_cycle_months: null,
  renewal_lead_days: 90,
  source_url: "https://example.test/rule",
  last_verified_on: "2026-08-01",
  cert_number: null,
  applied_on: null,
  approved_on: null,
  valid_from: null,
  expires_on: null,
  assignee_id: null,
  assignee_name: null,
  note: null,
  version: 3,
  is_overdue: false,
  overdue_days: null,
};

const AUTO_LOG = {
  id: 7,
  certification_id: 1,
  occurred_at: "2026-08-11T00:00:00+00:00",
  from_status: "APPROVED",
  to_status: "EXPIRING",
  reason: "만료일 임박(자동 — 리드타임 도달)",
  actor_user_id: null,
  expires_on_snapshot: "2026-09-30",
  cert_number_snapshot: null,
};

function stubApi(me: unknown, onTransition?: (body: unknown) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/transitions") && init?.method === "POST") {
        onTransition?.(JSON.parse(String(init.body)));
        return Promise.resolve(jsonResponse({ ...ROW, status: "PREPARING", version: 4 }));
      }
      if (input.includes("/status-log")) return Promise.resolve(jsonResponse(page([AUTO_LOG])));
      if (input.includes("/tasks")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/prerequisites")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/v1/certifications")) return Promise.resolve(jsonResponse(page([ROW])));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("인증 목록", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록·상태 라벨이 표시된다 (최소 렌더 — 조회 역할)", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    expect(await screen.findByRole("heading", { name: "인증 인스턴스" })).toBeInTheDocument();
    expect(await screen.findByText("MoCRA 제품 리스팅")).toBeInTheDocument();
    // "미착수"는 상태 필터 옵션과 표 셀 양쪽에 있다 — 셀 표시가 있는지 본다.
    expect(screen.getAllByText("미착수").length).toBeGreaterThanOrEqual(2);
    // 무역 역할에는 등록 폼이 없다(판정 ⑫ — 편집=인증+관리자).
    expect(screen.queryByText("새 인증 등록")).not.toBeInTheDocument();
  });

  it("갱신중 도과는 상태 라벨 옆 배지로 표시된다 — 상태는 갱신중 그대로 (S2-3 안건 ⑦)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.includes("/v1/certifications"))
          return Promise.resolve(
            jsonResponse(
              page([
                { ...ROW, status: "RENEWING", expires_on: "2026-08-01", is_overdue: true, overdue_days: 19 },
              ]),
            ),
          );
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    expect(await screen.findByText("도과 19일")).toBeInTheDocument();
    expect(screen.getAllByText("갱신중").length).toBeGreaterThanOrEqual(2); // 필터 옵션 + 셀
  });

  it("도과가 아니면 배지가 없다 — 게이트 축은 is_overdue 하나다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.includes("/v1/certifications"))
          return Promise.resolve(
            jsonResponse(page([{ ...ROW, is_overdue: false, overdue_days: 3 }])),
          );
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    expect(await screen.findByText("MoCRA 제품 리스팅")).toBeInTheDocument();
    expect(screen.queryByText(/도과 \d+일/)).not.toBeInTheDocument();
  });

  it("상세 패널에도 같은 배지가 붙는다 (목록+상세 = 2곳)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.includes("/v1/certifications"))
          return Promise.resolve(
            jsonResponse(page([{ ...ROW, status: "RENEWING", is_overdue: true, overdue_days: 19 }])),
          );
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    fireEvent.click(await screen.findByRole("button", { name: "상세" }));
    await waitFor(() => expect(screen.getAllByText("도과 19일")).toHaveLength(2));
  });

  it("인증 역할은 전이 폼을 보고, 전이 요청에 version이 실린다 (§17.2)", async () => {
    const bodies: unknown[] = [];
    stubApi(CERT_USER, (body) => bodies.push(body));
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    fireEvent.click(await screen.findByRole("button", { name: "상세" }));
    const toSelect = await screen.findByLabelText(/다음 상태/);
    fireEvent.change(toSelect, { target: { value: "PREPARING" } });
    fireEvent.click(screen.getByRole("button", { name: "전이" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ to: "PREPARING", version: 3 });
  });

  it("이력의 자동 전이는 시스템(자동)으로 표시된다 (§5.2 자동 부여)", async () => {
    stubApi(CERT_USER);
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    fireEvent.click(await screen.findByRole("button", { name: "상세" }));
    expect(await screen.findByText("시스템(자동)")).toBeInTheDocument();
    expect(screen.getByText("만료일 임박(자동 — 리드타임 도달)")).toBeInTheDocument();
  });
});

// ── 딥링크 (S2-3 PR-3 — 보드·매트릭스가 넘긴 ?id= 를 상세로 연다) ─────────────

describe("딥링크 (?id=) — 보드가 넘긴 인증을 상세로 연다", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록 1쪽에 없는 인증도 단건 조회로 상세 패널이 열리고 담당자 이름이 보인다", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        seen.push(input);
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.endsWith("/v1/certifications/77"))
          return Promise.resolve(
            jsonResponse({
              ...ROW,
              id: 77,
              template_name: "딥링크 요건",
              status: "IN_REVIEW",
              assignee_id: 5,
              assignee_name: "박인증",
            }),
          );
        if (input.includes("/status-log")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/tasks")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/prerequisites")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/v1/certifications")) return Promise.resolve(jsonResponse(page([ROW])));
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications?id=77" });

    expect(await screen.findByText(/딥링크 요건/, { selector: "h2, h3" })).toBeInTheDocument();
    expect(screen.getByText(/담당자 박인증/)).toBeInTheDocument();
    expect(seen.some((path) => path.endsWith("/v1/certifications/77"))).toBe(true);
  });

  it("링크 대상 인증이 없으면(404) 조용히 넘기지 않고 안내한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.endsWith("/v1/certifications/77"))
          return Promise.resolve(
            jsonResponse({ error: { code: "COMMON.NOT_FOUND", message: "없음", detail: {} } }, 404),
          );
        return Promise.resolve(jsonResponse(page([ROW])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications?id=77" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "링크가 가리키는 인증(#77)을 찾을 수 없습니다",
    );
    expect(await screen.findByText("MoCRA 제품 리스팅")).toBeInTheDocument(); // 목록은 그대로
  });

  it("링크 대상 조회가 서버 오류(500)면 다시 시도 안내를 한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.endsWith("/v1/certifications/77"))
          return Promise.resolve(
            jsonResponse({ error: { code: "COMMON.INTERNAL", message: "오류", detail: {} } }, 500),
          );
        return Promise.resolve(jsonResponse(page([ROW])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications?id=77" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("링크가 가리키는 인증(#77)을 불러오지 못했습니다"); // 목록 오류 문구와 구분
  });

  it("사용자가 닫은 상세 패널은 대상 데이터가 갱신돼 다시 도착해도 되살아나지 않는다", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.endsWith("/v1/certifications/77")) {
          calls += 1; // 두 번째 조회는 다른 사용자가 고친 뒤의 데이터(version 증가)
          return Promise.resolve(
            jsonResponse({ ...ROW, id: 77, template_name: "딥링크 요건", version: 3 + calls }),
          );
        }
        if (input.includes("/status-log")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/tasks")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/prerequisites")) return Promise.resolve(jsonResponse(page([])));
        return Promise.resolve(jsonResponse(page([ROW])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications?id=77" });

    expect(await screen.findByText(/딥링크 요건/, { selector: "h2" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "닫기" })); // 목록 쪽에 없는 인증이라 패널 자체의 닫기
    await waitFor(() => expect(screen.queryByText(/딥링크 요건/, { selector: "h2" })).toBeNull());

    // 창 복귀 — react-query가 오래된 조회를 다시 가져온다
    window.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(/딥링크 요건/, { selector: "h2" })).toBeNull();
  });

  it("상세 패널은 열릴 때 화면으로 스크롤된다 — 딥링크·행 클릭 모두, 같은 인증의 갱신에는 다시 하지 않는다", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    fireEvent.click(await screen.findByRole("button", { name: "상세" }));
    await screen.findByText(/MoCRA 제품 리스팅 —/, { selector: "h2" });
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: "nearest" }));

    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.click(screen.getByRole("button", { name: "상세" }));
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(2));
  });

  it("딥링크로 열린 패널도 화면으로 스크롤된다", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
        if (input.endsWith("/v1/certifications/77"))
          return Promise.resolve(jsonResponse({ ...ROW, id: 77, template_name: "딥링크 요건" }));
        if (input.includes("/status-log")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/tasks")) return Promise.resolve(jsonResponse(page([])));
        if (input.includes("/prerequisites")) return Promise.resolve(jsonResponse(page([])));
        return Promise.resolve(jsonResponse(page([ROW])));
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/certifications?id=77" });

    await screen.findByText(/딥링크 요건/, { selector: "h2" });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
  });

  it.each(["abc", "0", "-1", "1.5", ""])(
    "id=%s 처럼 양의 정수가 아니면 단건 조회를 하지 않는다",
    async (bad) => {
      const seen: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn((input: string) => {
          seen.push(input);
          if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
          return Promise.resolve(jsonResponse(page([ROW])));
        }),
      );
      renderWithProviders(<AppRoutes />, { route: `/certifications?id=${bad}` });

      await screen.findByText("MoCRA 제품 리스팅");
      expect(seen.some((path) => /\/v1\/certifications\/[^?]/.test(path))).toBe(false);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );
});

// ── 태스크 서류 링크 (S2-2 PR-2 — §5.1 "서류 링크") ─────────────────────────

const TASK = {
  id: 31,
  certification_id: 1,
  seq: 1,
  item_name: "CFS 준비",
  document_type_id: null,
  is_required: true,
  done: false,
  done_at: null,
  document_id: null as number | null,
  note: null,
  version: 1,
};

const DOC = {
  id: 42,
  owner_type: "SKU",
  owner_id: 5,
  owner_display: "SKU-001",
  document_type: "CFS",
  document_type_name: "판매증명서",
  storage_kind: "FILE",
  original_filename: "CFS.pdf",
  content_type: "application/pdf",
  size_bytes: 100,
  sha256: "a".repeat(64),
  url: null,
  issued_on: null,
  valid_until: null,
  retention_until: null,
  note: null,
};

function stubTaskApi(task: typeof TASK, onTaskPatch?: (body: unknown) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(CERT_USER));
      if (input.includes("/tasks/") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        onTaskPatch?.(body);
        return Promise.resolve(
          jsonResponse({ ...task, document_id: body.document_id ?? task.document_id, version: 2 }),
        );
      }
      if (input.includes("/tasks")) return Promise.resolve(jsonResponse(page([task])));
      if (input.includes("/v1/documents")) return Promise.resolve(jsonResponse(page([DOC])));
      if (input.includes("/status-log")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/prerequisites")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/v1/certifications")) return Promise.resolve(jsonResponse(page([ROW])));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("태스크 서류 링크", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("서류 셀렉트 선택이 PATCH에 document_id·version을 싣는다 (3값 의미론의 연결 방향)", async () => {
    const bodies: unknown[] = [];
    stubTaskApi(TASK, (body) => bodies.push(body));
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    fireEvent.click(await screen.findByRole("button", { name: "상세" }));
    const select = await screen.findByLabelText("CFS 준비 서류 연결");
    fireEvent.change(select, { target: { value: "42" } });

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ version: 1, document_id: 42 });
  });

  it("연결된 파일 서류가 다운로드 앵커로 표시된다 (문서 목록 밖이면 #id 폴백 — 함정 ⑦ 가드)", async () => {
    stubTaskApi({ ...TASK, document_id: 42 });
    renderWithProviders(<AppRoutes />, { route: "/certifications" });

    fireEvent.click(await screen.findByRole("button", { name: "상세" }));
    const anchor = await screen.findByRole("link", { name: "CFS.pdf" });
    expect(anchor).toHaveAttribute("href", "/api/v1/documents/42/download");
  });
});
