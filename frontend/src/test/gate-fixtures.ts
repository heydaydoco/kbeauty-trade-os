// 게이트 패널 테스트 픽스처 (S3-1 PR-11b) — 서버 응답 모양 그대로(backend gates/schemas.py). 값은 서버가 준 것으로 고정한다.

import { vi } from "vitest";
import type { GateReport, GateResult } from "../lib/gate";
import { jsonResponse } from "./render";

export const HASH_A = "a".repeat(64);
export const HASH_B = "b".repeat(64);

export function gate(over: Partial<GateResult> = {}): GateResult {
  return {
    gate_code: "ITEM_MAPPING",
    line_id: null,
    line_no: null,
    level: "PASS",
    resolution: "NONE",
    reason_code: "OK",
    message_ko: "품번 매핑이 확인되었습니다.",
    basis: {},
    detail: {},
    basis_hash: HASH_A,
    override_roles: [],
    settlement: "NOT_REQUIRED",
    can_override: false,
    override: null,
    ...over,
  };
}

/** 가격 편차 BLOCK(라인 1) — 무역·관리자가 override 가능. */
export const PRICE_BLOCK = gate({
  gate_code: "PRICE_DEVIATION",
  line_id: 41,
  line_no: 1,
  level: "BLOCK",
  resolution: "OVERRIDE",
  reason_code: "PRICE_ABOVE_TOLERANCE",
  message_ko: "기준가 대비 편차가 허용치를 넘습니다.",
  basis_hash: HASH_A,
  override_roles: ["TRADE", "ADMIN"],
  settlement: "UNRESOLVED",
  can_override: true,
});

export function report(gates: GateResult[], over: Partial<GateReport> = {}): GateReport {
  return {
    subject_type: "SALES_ORDER",
    subject_id: 9,
    status: "RECEIVED",
    input_digest: "d".repeat(64),
    authoritative: false,
    evaluated_at: "2026-10-01T00:00:00Z",
    note: "준비 상태 안내(법적 판정 아님)",
    readiness_scope_note: "시장 준비 상태는 계산값 표시입니다.",
    clearance: {
      cleared: false,
      needs_approval: false,
      unresolved_count: gates.filter((g) => g.settlement === "UNRESOLVED").length,
    },
    approval: { available: false, approval_id: null },
    policies: {
      pi_advance_gate_mode: { value: "BLOCK", source: "SET" },
      price_deviation_tolerance_bp: { value: 100, source: "SET" },
    },
    gates,
    ...over,
  };
}

/** 7종 전부 — 표시 순서(통합 X-35). */
export const SEVEN: GateResult[] = [
  gate({ gate_code: "ITEM_MAPPING", message_ko: "품번 매핑이 확인되었습니다." }),
  gate({
    gate_code: "DUPLICATE_PO",
    level: "WARN",
    message_ko: "바이어 PO번호가 없어 중복 확인을 하지 못했습니다.",
    basis_hash: HASH_B,
  }),
  PRICE_BLOCK,
  gate({
    gate_code: "CREDIT",
    level: "BLOCK",
    resolution: "APPROVAL",
    message_ko: "여신 한도를 초과합니다. 승인을 요청해 승인된 뒤에 확정할 수 있습니다.",
    settlement: "UNRESOLVED",
    basis: { limit_amount: 123456789 },
  }),
  gate({
    gate_code: "MARKET_READINESS",
    level: "UNKNOWN",
    resolution: "OVERRIDE",
    message_ko: "필수 요건이 없어 준비 상태를 확인할 수 없습니다.",
    override_roles: ["ADMIN"],
    settlement: "UNRESOLVED",
  }),
  gate({ gate_code: "MOQ", level: "PASS", line_id: 41, line_no: 1, message_ko: "최소주문수량 이상입니다." }),
  gate({
    gate_code: "PI_DEPOSIT",
    level: "BLOCK",
    resolution: "OVERRIDE",
    message_ko: "선수금 입금이 부족합니다.",
    override_roles: ["ADMIN"],
    settlement: "UNRESOLVED",
  }),
];

export interface GateCall {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
  headers: Record<string, string>;
}

export type GateHandler = [path: string, method: string, respond: () => Response | Promise<Response>];

/**
 * 정확 경로·메서드 일치 fetch 스텁 — 부분 일치(includes)가 아니라서 잘못된 경로 호출은 404로 터진다(공회전·오라우팅 방지).
 * 핸들러 배열은 호출 뒤에 바꿔도 반영된다(변경 가능). `/auth/me`만 내장.
 */
export function stubGateFetch(me: unknown, handlers: GateHandler[]): { calls: GateCall[] } {
  const calls: GateCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url: input,
        method,
        body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null,
        headers: (init?.headers ?? {}) as Record<string, string>,
      });
      if (input === "/api/v1/auth/me") return Promise.resolve(jsonResponse(me));
      for (const [path, wanted, respond] of handlers) {
        if (input === `/api${path}` && method === wanted) return Promise.resolve(respond());
      }
      return Promise.resolve(
        jsonResponse({ error: { code: "TEST.UNMATCHED", message: `스텁에 없는 호출: ${method} ${input}`, detail: {} } }, 404),
      );
    }),
  );
  return { calls };
}
