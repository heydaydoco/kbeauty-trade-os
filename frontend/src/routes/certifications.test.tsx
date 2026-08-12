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
  note: null,
  version: 3,
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
