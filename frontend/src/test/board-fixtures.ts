// 오더 보드 테스트 픽스처 (S3-1 PR-15b) — 서버 응답 모양 그대로(backend order_board/schemas.py).

import type { BoardCard, BoardColumn, BulkItemResult, BulkReport, OrderBoard, SavedFilter } from "../lib/order-board";
import { EMPTY_FILTER } from "../lib/order-board";
import type { GateHandler } from "./gate-fixtures";
import { jsonResponse, page } from "./render";

export function boardCard(over: Partial<BoardCard> = {}): BoardCard {
  return {
    kind: "SO",
    id: 11,
    ref_label: "SO-2026-0011",
    buyer_partner_id: 3,
    buyer_name: "ABC Trading",
    buyer_po_no: "PO-ABC-1",
    line_count: 2,
    total_amount: 12500,
    total_text: "125.00",
    currency: "USD",
    age_days: 4,
    assignee_id: 1,
    assignee_name: "무역 담당",
    updated_at: "2026-10-01T00:00:00Z",
    version: 3,
    ...over,
  };
}

export const INTAKE_CARD = boardCard({
  kind: "INTAKE",
  id: 21,
  ref_label: "IN-21",
  buyer_po_no: "PO-IN-21",
  version: 1,
});
export const SO_RECEIVED_CARD = boardCard({ id: 11, ref_label: "SO-2026-0011", version: 3 });
export const SO_HOLD_CARD = boardCard({ id: 12, ref_label: "SO-2026-0012", version: 5, buyer_name: "XYZ Corp" });
export const SO_CONFIRMED_CARD = boardCard({ id: 13, ref_label: "SO-2026-0013", version: 8 });

export function column(stage: BoardColumn["stage"], label: string, items: BoardCard[], over: Partial<BoardColumn> = {}): BoardColumn {
  return { stage, label_ko: label, total: items.length, has_more: false, items, ...over };
}

export function board(over: Partial<Record<BoardColumn["stage"], Partial<BoardColumn>>> = {}): OrderBoard {
  return {
    columns: [
      column("INTAKE_PENDING", "인테이크 대기", [INTAKE_CARD], over.INTAKE_PENDING),
      column("SO_RECEIVED", "수주 접수", [SO_RECEIVED_CARD], over.SO_RECEIVED),
      column("SO_ON_HOLD", "수주 보류", [SO_HOLD_CARD], over.SO_ON_HOLD),
      column("SO_CONFIRMED", "수주 확정", [SO_CONFIRMED_CARD], over.SO_CONFIRMED),
    ],
    generated_at: "2026-10-03T01:00:00Z",
  };
}

export function bulkResult(over: Partial<BulkItemResult> = {}): BulkItemResult {
  return {
    kind: "SO",
    id: 11,
    outcome: "OK",
    code: null,
    message_ko: "확정했습니다.",
    blocked_gates: [],
    version: 4,
    sales_order_id: 11,
    doc_number: "SO-2026-0011",
    ...over,
  };
}

export function bulkReport(results: BulkItemResult[], action: BulkReport["action"] = "CONFIRM_SO"): BulkReport {
  const counts: Record<string, number> = { OK: 0, SKIPPED: 0, BLOCKED: 0, CONFLICT: 0, FORBIDDEN: 0, FAILED: 0 };
  for (const row of results) counts[row.outcome] = (counts[row.outcome] ?? 0) + 1;
  return {
    action,
    results,
    total: results.length,
    ok_count: counts.OK ?? 0,
    skipped_count: counts.SKIPPED ?? 0,
    fail_count: results.length - (counts.OK ?? 0) - (counts.SKIPPED ?? 0),
    outcome_counts: counts,
  };
}

export function savedFilter(over: Partial<SavedFilter> = {}): SavedFilter {
  return {
    id: 5,
    name: "미국 대기 건",
    filter_config: { ...EMPTY_FILTER, q: "ABC", dest_market_code: "US" },
    needs_resave: false,
    version: 2,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...over,
  };
}

/** 보드 화면이 마운트 때 부르는 공통 호출(셸 배지·통화·시장·저장 필터). */
export function baseHandlers(filters: SavedFilter[] = []): GateHandler[] {
  return [
    ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })],
    ["/v1/system/currencies?size=200", "GET", () => jsonResponse(page([{ code: "USD" }, { code: "KRW" }])) /* 보드는 통화 코드만 쓴다(자릿수는 서버 total_text) */],
    ["/v1/markets?size=200", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국" }, { id: 2, code: "JP", name_ko: "일본" }]))],
    ["/v1/order-board/saved-filters", "GET", () => jsonResponse(page(filters))],
  ];
}
