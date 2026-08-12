// 알림센터 화면 — 목록·확인 버튼·관리자 패널·미확인 배지 (S2-3 PR-1).
//
// 실제 차단은 서버가 한다(§18.1) — 여기서 고정하는 것은 화면 계약이다:
// ① 확인한 알림은 버튼 대신 확인 시각(KST)을 보여 준다(되돌리는 버튼 없음).
// ② 관리자에게만 알림 규칙·배치 패널이 보인다(§14 ⑭).
// ③ 수신 대상이 빈 규칙은 "담당자"로 읽힌다 — 서버 라우팅 규칙과 같은 말이어야
//    화면을 보고 규칙을 설계할 수 있다.
// ④ 미확인 수 배지가 셸에 상시 노출된다(열어야만 아는 알림은 안 읽힌다).

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const ADMIN = { id: 9, email: "admin@example.com", display_name: "관리자", roles: ["ADMIN"] };

const UNREAD_ALERT = {
  id: 11,
  alert_rule_id: 3,
  recipient_user_id: 1,
  title: "인증 만료 임박",
  body: "US SKU 등록 · D-30",
  severity: "WARN",
  dedup_key: "cert:SKU:3:D-30:1",
  acknowledged_at: null,
  entity_type: "certifications",
  entity_id: 3,
};

const ACKED_ALERT = {
  ...UNREAD_ALERT,
  id: 12,
  title: "확인한 알림",
  body: null,
  acknowledged_at: "2026-08-12T01:46:00Z",
};

const RULE_TO_ASSIGNEE = {
  id: 3,
  code: "CERT-EXPIRING",
  name_ko: "인증 만료 임박",
  event_type: "certifications.certification.status_changed",
  severity: "WARN",
  recipient_user_id: null,
  recipient_role: null,
  is_enabled: true,
  version: 1,
};

const JOB = {
  id: 1,
  code: "outbox-dispatch",
  name_ko: "아웃박스 알림 디스패치",
  schedule: "interval@1",
  is_enabled: true,
  last_run_at: "2026-08-12T01:40:00Z",
  last_status: "OK",
  last_error: null,
  is_mapped: true,
  version: 1,
};

function stubApi(me: unknown, options: { onAck?: () => void; unread?: number } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/alerts/unread-count")) {
        return Promise.resolve(jsonResponse({ count: options.unread ?? 1 }));
      }
      if (input.includes("/ack") && init?.method === "POST") {
        options.onAck?.();
        return Promise.resolve(
          jsonResponse({ ...UNREAD_ALERT, acknowledged_at: "2026-08-12T02:00:00Z" }),
        );
      }
      if (input.includes("/v1/alerts")) {
        return Promise.resolve(jsonResponse(page([UNREAD_ALERT, ACKED_ALERT])));
      }
      if (input.includes("/v1/alert-rules")) {
        return Promise.resolve(jsonResponse(page([RULE_TO_ASSIGNEE])));
      }
      if (input.includes("/v1/scheduled-jobs")) return Promise.resolve(jsonResponse(page([JOB])));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("알림센터", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("확인한 알림은 버튼 대신 확인 시각(KST)을 보여 준다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/alerts" });

    expect(await screen.findByRole("heading", { name: "알림센터" })).toBeInTheDocument();
    // 제목은 즉시 뜨고 표는 조회가 끝나야 뜬다 — 동기 조회로 단언하면 아직
    // "불러오는 중…"이라 늘 빨개진다.
    expect(await screen.findByText("인증 만료 임박")).toBeInTheDocument();
    // 미확인 1건에만 확인 버튼이 있다.
    expect(screen.getAllByRole("button", { name: "확인" })).toHaveLength(1);
    // 2026-08-12T01:46Z = KST 10:46
    expect(screen.getByText(/10:46 \(KST\)/)).toBeInTheDocument();
  });

  it("확인 버튼이 ack를 부른다", async () => {
    const onAck = vi.fn();
    stubApi(TRADER, { onAck });
    renderWithProviders(<AppRoutes />, { route: "/alerts" });

    fireEvent.click(await screen.findByRole("button", { name: "확인" }));
    await waitFor(() => expect(onAck).toHaveBeenCalledTimes(1));
  });

  it("일반 역할에게는 관리 패널이 보이지 않는다", async () => {
    stubApi(TRADER);
    renderWithProviders(<AppRoutes />, { route: "/alerts" });

    await screen.findByRole("heading", { name: "알림센터" });
    expect(screen.queryByRole("heading", { name: "알림 규칙" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "배치 레지스트리" })).not.toBeInTheDocument();
  });

  it("관리자에게는 규칙·배치가 보이고 빈 수신 대상은 '담당자'로 읽힌다", async () => {
    stubApi(ADMIN);
    renderWithProviders(<AppRoutes />, { route: "/alerts" });

    expect(await screen.findByRole("heading", { name: "알림 규칙" })).toBeInTheDocument();
    expect(await screen.findByText("담당자")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "배치 레지스트리" })).toBeInTheDocument();
    expect(await screen.findByText("interval@1")).toBeInTheDocument();
    expect(screen.getByText("정상")).toBeInTheDocument();
  });

  it("셸의 알림 링크에 미확인 수가 붙는다", async () => {
    stubApi(TRADER, { unread: 3 });
    renderWithProviders(<AppRoutes />, { route: "/skus" });

    expect(await screen.findByRole("link", { name: "알림 3" })).toBeInTheDocument();
  });

  it("미확인이 0이면 숫자를 붙이지 않는다", async () => {
    stubApi(TRADER, { unread: 0 });
    renderWithProviders(<AppRoutes />, { route: "/skus" });

    expect(await screen.findByRole("link", { name: "알림" })).toBeInTheDocument();
  });
});
