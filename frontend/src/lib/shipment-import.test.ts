// 수입선적 lib (S3-2 PR-5b — 그룹 A·G) — 판별자 금액 축(수출만)·동결 전이 문구(R-5a-8)·배정 가능량 409 칸별(EXCEEDS_ASSIGNABLE —
// PO 잔량 EXCEEDS_OPEN과 섞지 않음)·422 라인 칸 오류(보낸 본문 순서)·발주 라인 배정 가능량 합·입고예정 문구(NONE/UNSCHEDULED/SCHEDULED).

import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { assignableTotal, expectedReceiptText, lacksReceiptFields, type ExpectedReceipt } from "./purchase-order";
import {
  EXCEEDS_ASSIGNABLE_CODE,
  assignableByLine,
  isExportShipment,
  lineFieldErrors,
  openQuantityByLine,
  quantityConflictByLine,
  releaseActionLabel,
  shipmentStatusText,
  shipmentTotalText,
} from "./shipment";
import { importShipmentListItem, shipmentListItem } from "../test/shipment-fixtures";

const apiError = (status: number, code: string, detail: Record<string, unknown>) =>
  new ApiError(status, { code, message: "서버 문구", detail, requestId: null });

describe("금액 축 — 수출만(판별자), 그 밖은 '—'", () => {
  it("수출 행은 서버 문자열 + 통화, 수입 행은 '—'(0.00·통화로 흉내 내지 않는다)", () => {
    expect(shipmentTotalText(shipmentListItem())).toBe("50.00 USD");
    const imported = importShipmentListItem();
    expect(shipmentTotalText(imported)).toBe("—");
    expect(isExportShipment(imported)).toBe(false);
    // 수입 응답에는 금액·통화 키 자체가 없다(픽스처 = 백엔드 ImportShipmentListItem 모양).
    expect(Object.keys(imported).filter((key) => /currency|total|amount|price|fx_rate|minor/.test(key))).toEqual([]);
  });

  it("모르는 구분은 금액 축 없음으로(fail-closed)", () => {
    expect(isExportShipment({ shipment_kind: "CHANNEL_INBOUND" })).toBe(false);
    expect(isExportShipment({ shipment_kind: "EXPORT" })).toBe(true);
  });
});

describe("동결 전이 문구(R-5a-8) — 수입은 '선적 확정', 상태 코드는 그대로", () => {
  it.each([
    ["EXPORT", "RELEASE_ORDERED", "출고지시"],
    ["IMPORT", "RELEASE_ORDERED", "선적 확정"],
    ["IMPORT", "PLANNED", "계획"],
    ["IMPORT", "CANCELLED", "취소"],
    ["EXPORT", "X", "기타"],
  ])("%s %s → %s", (kind, status, label) => {
    expect(shipmentStatusText(status, kind)).toBe(label);
  });

  it("동작 이름", () => {
    expect(releaseActionLabel("EXPORT")).toBe("출고지시");
    expect(releaseActionLabel("IMPORT")).toBe("선적 확정");
  });
});

describe("수량 초과 409 — 구분별 코드·키를 섞지 않는다", () => {
  const assignable = apiError(409, EXCEEDS_ASSIGNABLE_CODE, { assignable_quantity: { "77": 40, "78": 0, bad: "x" } });
  const open = apiError(409, "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", { open_quantity: { "41": 2 } });

  it("배정 가능량 409는 PO 라인별 남은 배정 가능량(숫자만)", () => {
    expect([...assignableByLine(assignable)]).toEqual([
      [77, 40],
      [78, 0],
    ]);
    expect(quantityConflictByLine(assignable, "IMPORT").get(77)).toBe(40);
  });

  it("PO 잔량 오류(EXCEEDS_OPEN)를 배정 가능량으로 읽지 않는다 — 반대도 같다", () => {
    expect(assignableByLine(open).size).toBe(0);
    expect(openQuantityByLine(assignable).size).toBe(0);
    expect(quantityConflictByLine(open, "IMPORT").size).toBe(0);
    expect(quantityConflictByLine(assignable, "EXPORT").size).toBe(0);
    expect(quantityConflictByLine(open, "EXPORT").get(41)).toBe(2);
  });
});

describe("422 라인 칸 오류 — 보낸 본문의 i번째 라인에", () => {
  it("`lines[i].po_line_id`·`lines[i].quantity`를 원천 라인 id로, 범위 밖·문자열 아닌 값은 버린다", () => {
    const error = apiError(422, "SHIPMENTS.SOURCE.LINE_MISMATCH", {
      "lines[1].po_line_id": "이 발주의 라인이 아닙니다.",
      "lines[0].quantity": "1 이상의 정수",
      "lines[5].po_line_id": "범위 밖",
      "lines[0].x": 3,
      internal_note: "메모",
    });
    expect([...lineFieldErrors(error, [77, 78])]).toEqual([
      [78, "이 발주의 라인이 아닙니다."],
      [77, "1 이상의 정수"],
    ]);
  });

  it("422가 아니면 빈 맵", () => {
    expect(lineFieldErrors(apiError(409, "X", { "lines[0].po_line_id": "x" }), [77]).size).toBe(0);
    expect(lineFieldErrors(new Error("x"), [77]).size).toBe(0);
  });
});

describe("발주 라인 — 배정 가능량 합·입고예정 문구(인계 계약 표시 규칙)", () => {
  const receipt = (over: Partial<ExpectedReceipt>): ExpectedReceipt => ({
    status: "NONE",
    value: null,
    basis: null,
    shipment_count: 0,
    unscheduled_count: 0,
    ...over,
  });

  it("합은 숫자만 — null·없음·음수는 0(모르면 '만들기'를 열지 않는다)", () => {
    expect(assignableTotal([{ assignable_quantity: 3 }, { assignable_quantity: 0 }])).toBe(3);
    expect(assignableTotal([{ assignable_quantity: null }, {}, { assignable_quantity: -5 }])).toBe(0);
  });

  it("재생 본문(두 필드 null·없음) 판정", () => {
    expect(lacksReceiptFields([{ assignable_quantity: 1, expected_receipt: receipt({}) }])).toBe(false);
    expect(lacksReceiptFields([{ assignable_quantity: null, expected_receipt: receipt({}) }])).toBe(true);
    expect(lacksReceiptFields([{ assignable_quantity: 1, expected_receipt: null }])).toBe(true);
    expect(lacksReceiptFields([{}])).toBe(true);
  });

  it("NONE·UNSCHEDULED·SCHEDULED 세 갈래 — 날짜는 문자열 그대로, 모양이 어긋나면 null", () => {
    expect(expectedReceiptText(receipt({}))).toBe("입고예정 미정(수입선적 없음)");
    expect(expectedReceiptText(receipt({ status: "UNSCHEDULED", shipment_count: 3, unscheduled_count: 2 }))).toBe("ETA 미정 2건");
    expect(expectedReceiptText(receipt({ status: "SCHEDULED", value: "2026-10-31", basis: "PLANNED", shipment_count: 1 }))).toBe("2026-10-31");
    expect(expectedReceiptText(receipt({ status: "SCHEDULED", value: null }))).toBeNull();
    expect(expectedReceiptText(receipt({ status: "SCHEDULED", value: "2026-10-31T00:00:00Z" }))).toBeNull();
    expect(expectedReceiptText(receipt({ status: "WEIRD" as ExpectedReceipt["status"] }))).toBeNull();
    expect(expectedReceiptText(null)).toBeNull();
    expect(expectedReceiptText(undefined)).toBeNull();
  });
});
