// 마일스톤·통관·OEM 생산 일정 테스트 픽스처 (S3-2 PR-4b) — 응답 모양은 backend shipments/schemas.py
// (MilestoneBoardOut·MilestoneRowOut·OemMilestoneBoardOut·MilestoneChangeOut·CustomsRecordOut) + milestone_view `_empty_row` 그대로.

import type {
  CustomsRecord,
  MilestoneBoard,
  MilestoneChange,
  MilestoneRow,
  OemMilestoneBoard,
} from "../lib/milestone";

const DATETIME_TYPES = new Set(["DOC_CUTOFF", "CARGO_CLOSING"]);
const DERIVED_TYPES = new Set(["LOADING_DEADLINE", "PAYMENT_DUE", "PRESENTATION_DEADLINE"]);

/** 서버 `_empty_row` + 저장형 `input_source` — 값 없는 행. */
export function milestoneRow(milestoneType: string, over: Partial<MilestoneRow> = {}): MilestoneRow {
  const derived = DERIVED_TYPES.has(milestoneType);
  return {
    milestone_type: milestoneType,
    kind: derived ? "DERIVED" : "STORED",
    value_shape: DATETIME_TYPES.has(milestoneType) ? "DATETIME" : "DATE",
    applicable: true,
    planned: null,
    actual: null,
    effective: null,
    derived: null,
    scan_date: null,
    local_date: null,
    customs_state: null,
    customs_pending_count: null,
    days_left: null,
    is_overdue: null,
    unknown_reason: null,
    fulfilment: null,
    holiday: null,
    rollover_count: 0,
    unnotified_rollovers: 0,
    order_warning: null,
    milestone_id: null,
    version: null,
    input_source: derived ? null : milestoneType === "CUSTOMS_CLEARED" ? "CUSTOMS_RECORD" : "MILESTONE",
    ...over,
  };
}

/** 수출·T/T 후불·행 없음(계획 전) 선적의 서버 보드 11행 — 업무 흐름 순서(SHIPMENT_BOARD_ORDER). */
export function exportBoard(overrides: Record<string, Partial<MilestoneRow>> = {}, todayKst = "2026-10-04"): MilestoneBoard {
  const base: MilestoneRow[] = [
    milestoneRow("DOC_CUTOFF"),
    milestoneRow("CARGO_CLOSING"),
    milestoneRow("PSI"),
    milestoneRow("CUSTOMS_CLEARED", { customs_state: "NONE", customs_pending_count: 0 }),
    milestoneRow("LOADING_DEADLINE", {
      derived: { status: "UNKNOWN", value: null, basis: null, reason_code: "NOT_CLEARED" },
      fulfilment: "UNKNOWN",
    }),
    milestoneRow("ETD"),
    milestoneRow("BL_ISSUED"),
    milestoneRow("PRESENTATION_DEADLINE", { applicable: false }),
    milestoneRow("ETA"),
    milestoneRow("IMPORT_TAX_DUE", { applicable: false }),
    milestoneRow("PAYMENT_DUE", {
      derived: { status: "UNKNOWN", value: null, basis: null, reason_code: "ANCHOR_PENDING" },
    }),
  ];
  const rows = base.map((row) => ({ ...row, ...(overrides[row.milestone_type] ?? {}) }));
  const holiday = rows.filter((row) => row.holiday?.flag === "HOLIDAY").length;
  const unverified = rows.filter((row) => row.holiday?.flag === "UNVERIFIED").length;
  return { today_kst: todayKst, holiday_summary: { holiday, unverified }, rows };
}

/** OEM 생산 일정 4행(전부 날짜형·휴일·통관·롤오버 0 — PR-4c 인계 계약). */
export function oemBoard(over: Partial<OemMilestoneBoard> = {}, overrides: Record<string, Partial<MilestoneRow>> = {}): OemMilestoneBoard {
  const rows = ["RAW_MATERIAL_READY", "FILLING", "PACKING", "OUTGOING_INSPECTION"].map((type) => ({
    ...milestoneRow(type),
    ...(overrides[type] ?? {}),
  }));
  return {
    today_kst: "2026-10-04",
    holiday_summary: { holiday: 0, unverified: 0 },
    rows,
    po_id: 7,
    allowed_actions: ["EDIT_MILESTONES"],
    ...over,
  };
}

export function milestoneChange(over: Partial<MilestoneChange> = {}): MilestoneChange {
  return {
    id: 900,
    milestone_id: 61,
    milestone_type: "ETA",
    change_kind: "PLAN_CHANGED",
    old: "2026-11-01",
    new: "2026-11-05",
    reason: "선사 스케줄 변경",
    actor_user_id: 1,
    actor_name: "무역 담당",
    created_at: "2026-10-04T03:00:00Z",
    notices: [],
    ...over,
  };
}

export function customsRecord(over: Partial<CustomsRecord> = {}): CustomsRecord {
  return {
    id: 301,
    shipment_id: 31,
    declaration_kind: "EXPORT",
    declaration_no: "AB-1001",
    declared_on: "2026-10-01",
    accepted_on: null,
    customs_broker: { partner_id: 12, name: "서울관세" },
    note: null,
    version: 1,
    created_at: "2026-10-01T01:00:00Z",
    updated_at: "2026-10-01T01:00:00Z",
    ...over,
  };
}
