// 오더 인테이크 lib — 오류 한국어화·파서·형식 검증·라벨 (S3-1 PR-13b).

import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  MAX_LINES,
  fieldPathLabel,
  intakeErrorMessage,
  intakeLoadErrorMessage,
  intakeStatusLabel,
  isIntakeRecoverable,
  lineBodies,
  mappingGuide,
  mappingStateLabel,
  newLineForm,
  parseDuplicateGroups,
  parseLineRefs,
  parsePoOccupant,
  poOccupiedText,
  validateLineForms,
  type IntakeOp,
} from "./order-intake";

const err = (code: string, status: number, detail: Record<string, unknown> = {}, message = "English server message") =>
  new ApiError(status, { code, message, detail, requestId: "r" });

describe("intakeErrorMessage — 코드별 한국어 안내(영문 코드·원문 비노출)", () => {
  const cases: Array<[string, number, IntakeOp, RegExp]> = [
    ["COMMON.CONCURRENCY.VERSION_CONFLICT", 409, "edit", /다른 곳에서 이 인테이크가 먼저 수정/],
    ["COMMON.IDEMPOTENCY.KEY_CONFLICT", 409, "confirm", /같은 요청 키로 다른 내용/],
    ["COMMON.CONCURRENCY.LOCK_BUSY", 409, "confirm", /같은 거래처의 다른 건을 처리 중/],
    ["ORDER_INTAKE.STATE.NOT_PENDING", 409, "confirm", /이미 확정되었거나 거부된 인테이크/],
    ["ORDER_INTAKE.GATE.UNRESOLVED", 409, "confirm", /평가하지 못했습니다/],
    ["ORDER_INTAKE.LINE.LIMIT_EXCEEDED", 422, "create", /1개 이상 200개 이하/],
    ["TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE", 409, "confirm", /복제 원본 수주가 더 이상/],
    ["TRADE_DOCS.LINE.AMOUNT_OUT_OF_RANGE", 422, "confirm", /금액 또는 라인 수가 허용 범위/],
  ];
  it.each(cases)("%s → 한국어", (code, status, op, pattern) => {
    const text = intakeErrorMessage(err(code, status), op);
    expect(text).toMatch(pattern);
    expect(text).not.toMatch(/English server message/);
    expect(text).not.toContain(code);
  });

  it("미매핑 422 — 라인 번호·품번을 서버 detail에서 그대로 옮긴다", () => {
    const text = intakeErrorMessage(
      err("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422, { lines: [{ line_no: 2, buyer_item_code: "X-9", reason_code: "ITEM_UNMAPPED" }, { line_no: 4, buyer_item_code: "Y-1", reason_code: "SKU_DELETED" }] }),
      "confirm",
    );
    expect(text).toContain("라인 2(X-9), 라인 4(Y-1)");
    expect(text).toContain("품번 다시 확인");
    expect(text).not.toContain("ITEM_UNMAPPED");
  });

  it("STALE_MAPPING 409 — 재해석 안내(자동 추종 없음)", () => {
    const text = intakeErrorMessage(err("ORDER_INTAKE.LINE.STALE_MAPPING", 409, { lines: [{ line_no: 1, buyer_item_code: "A", reason_code: "MAPPING_CHANGED" }] }), "confirm");
    expect(text).toContain("라인 1(A)");
    expect(text).toContain("자동으로 따라가지 않습니다");
  });

  it("DUPLICATE_SKU 422 — 라인 번호 묶음(중첩 배열)", () => {
    const text = intakeErrorMessage(err("ORDER_INTAKE.LINE.DUPLICATE_SKU", 422, { lines: [[1, 3], [2, 4]] }), "confirm");
    expect(text).toContain("라인 1·라인 3 / 라인 2·라인 4");
  });

  it("중복 바이어 PO 409 — 점유 수주 번호·상태는 서버가 줄 때만 쓴다", () => {
    const withSo = intakeErrorMessage(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, { doc_number: "SO-2026-0007", status: "RECEIVED" }), "create");
    expect(withSo).toContain("SO-2026-0007");
    expect(withSo).toContain("접수");
    const withIntake = intakeErrorMessage(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, { intake_id: 8, status: "PENDING" }), "create");
    expect(withIntake).toContain("#8");
    expect(withIntake).toContain("대기");
    const masked = intakeErrorMessage(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, {}), "create");
    expect(masked).not.toMatch(/SO-|#\d/);
    expect(masked).toContain("이미 다른 문서");
  });

  it("검증 422 — 등록·수정(lines[i].필드)과 확정(line_<n>.필드 — 인테이크 라인 번호) 위치를 한국어로", () => {
    const create = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { "lines[1].unit_price": "단가는 0보다 커야 합니다." }), "create");
    expect(create).toContain("라인 2 단가: 단가는 0보다 커야 합니다.");
    const confirm = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { "line_3.requested_delivery_date": "요청납기는 오늘(KST)보다 앞설 수 없습니다." }), "confirm");
    expect(confirm).toContain("라인 3 요청납기: 요청납기는 오늘(KST)보다 앞설 수 없습니다.");
    const market = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { dest_market_code: "비활성 시장입니다." }), "confirm");
    expect(market).toContain("도착 시장: 비활성 시장입니다.");
    // 한글이 없는 값(영문 코드)은 원문을 노출하지 않는다.
    const english = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { currency: "unknown currency XX" }), "create");
    expect(english).not.toContain("unknown currency");
    expect(english).toContain("통화");
    expect(fieldPathLabel("lines[0].nope")).toBe("라인 1 항목");
  });

  it("연결 끊김 — 처리되었을 수 있음을 알리고 중복 처리되지 않음을 안내한다", () => {
    const text = intakeErrorMessage(new ApiError(0, { code: "CLIENT.NETWORK.UNREACHABLE", message: "x", detail: {}, requestId: null }), "confirm");
    expect(text).toContain("연결이 끊겼습니다");
    expect(text).toContain("접수 확정이");
    expect(text).toContain("중복 처리되지 않습니다");
  });

  it("상태별 일반 문구·서버 한국어 message·기본 문구 순서", () => {
    expect(intakeErrorMessage(err("X", 403), "confirm")).toBe("접수 확정은 무역·관리자만 할 수 있습니다.");
    expect(intakeErrorMessage(err("X", 403), "reject")).toBe("거부는 무역·관리자만 할 수 있습니다.");
    expect(intakeErrorMessage(err("X", 404), "edit")).toContain("찾을 수 없습니다");
    expect(intakeErrorMessage(err("X", 409), "edit")).toContain("충돌");
    expect(intakeErrorMessage(err("X", 422), "edit")).toContain("입력값이 올바르지 않습니다");
    expect(intakeErrorMessage(err("X", 500, {}, "서버 한국어 안내"), "edit")).toBe("서버 한국어 안내");
    expect(intakeErrorMessage(err("X", 500), "edit")).toBe("수정을 처리하지 못했습니다.");
    expect(intakeErrorMessage(new Error("boom"), "resolve")).toBe("품번 다시 확인을 처리하지 못했습니다.");
  });

  it("조회 오류는 쓰기 문구와 분리된다", () => {
    expect(intakeLoadErrorMessage(err("X", 403))).toContain("볼 권한이 없습니다");
    expect(intakeLoadErrorMessage(err("X", 404))).toContain("찾을 수 없습니다");
    expect(intakeLoadErrorMessage(err("X", 500))).toContain("불러오지 못했습니다");
  });

  it("복구(최신 내용 불러오기) 대상 — 409 계열·연결 끊김, 422는 아님", () => {
    expect(isIntakeRecoverable(err("ORDER_INTAKE.LINE.STALE_MAPPING", 409))).toBe(true);
    expect(isIntakeRecoverable(err("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422))).toBe(false);
    expect(isIntakeRecoverable(new ApiError(0, { code: "CLIENT.NETWORK.UNREACHABLE", message: "x", detail: {}, requestId: null }))).toBe(true);
    expect(isIntakeRecoverable(new Error("x"))).toBe(false);
  });
});

describe("파서·라벨", () => {
  it("parseLineRefs·parseDuplicateGroups — 모양이 깨져도 터지지 않는다", () => {
    expect(parseLineRefs({ lines: "문자열" })).toEqual([]);
    expect(parseLineRefs({ lines: [null, 3, { line_no: 1, buyer_item_code: "A" }] })).toEqual([{ lineNo: 1, code: "A" }]);
    expect(parseLineRefs(undefined)).toEqual([]);
    expect(parseDuplicateGroups({ lines: [[1, 2], "x", [3, "y"]] })).toEqual([[1, 2], [3]]);
    expect(parseDuplicateGroups({})).toEqual([]);
  });

  it("parsePoOccupant — 중복 PO 오류에서만, 값이 하나도 없으면 null", () => {
    expect(parsePoOccupant(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, { intake_id: 5, status: "PENDING" }))).toEqual({ intakeId: 5, docNumber: null, status: "PENDING" });
    expect(parsePoOccupant(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, {}))).toBeNull();
    expect(parsePoOccupant(err("OTHER", 409, { intake_id: 5 }))).toBeNull();
  });

  it("상태·매핑 라벨은 모르는 값을 '확인 불가'로(감추지 않음)", () => {
    expect(intakeStatusLabel("PENDING")).toBe("대기");
    expect(intakeStatusLabel("???")).toBe("확인 불가");
    expect(mappingStateLabel("MAPPED")).toBe("매핑됨");
    expect(mappingStateLabel("UNMAPPED")).toBe("미매핑");
    expect(mappingStateLabel("STALE")).toBe("재해석 필요");
    expect(mappingStateLabel("???")).toBe("확인 불가");
    expect(mappingGuide("MAPPED")).toBeNull();
    expect(mappingGuide("UNMAPPED")).toContain("품번 등록");
    expect(mappingGuide("STALE")).toContain("자동으로 따라가지 않습니다");
  });

  it("poOccupiedText — 번호·상태는 서버가 줄 때만", () => {
    expect(poOccupiedText({ kind: "SALES_ORDER", doc_number: "SO-1", status: "RECEIVED" })).toContain("SO-1 (접수)");
    expect(poOccupiedText({ kind: "SALES_ORDER", doc_number: null, status: null })).toContain("무역·관리자만");
    expect(poOccupiedText({ kind: "INTAKE", doc_number: null, status: null })).toContain("다른 인테이크");
  });
});

describe("라인 형식 검증 — 형식만(금액 산술 0)", () => {
  const ok = (over = {}) => newLineForm({ code: "A-1", qty: "3", price: "12.50", ...over });
  it("필수·정수 수량·금액 문자열", () => {
    expect(validateLineForms([])).toContain("1개 이상");
    expect(validateLineForms([ok({ code: " " })])).toContain("바이어 품번");
    expect(validateLineForms([ok({ qty: "0" })])).toContain("수량");
    expect(validateLineForms([ok({ qty: "1.5" })])).toContain("수량");
    expect(validateLineForms([ok({ qty: "100000000" })])).toContain("수량");
    expect(validateLineForms([ok({ price: "" })])).toContain("단가");
    expect(validateLineForms([ok({ price: "12,5" })])).toContain("단가");
    expect(validateLineForms([ok({ price: "-1" })])).toContain("단가");
    expect(validateLineForms([ok()])).toBeNull();
    // 같은 SKU 중복·0 단가·납기는 서버 판정 — 형식 검증은 통과시킨다.
    expect(validateLineForms([ok({ price: "0" }), ok({ code: "A-1" })])).toBeNull();
  });
  it("라인 수 상한", () => {
    expect(validateLineForms(Array.from({ length: MAX_LINES + 1 }, () => ok()))).toContain("200개 이하");
    expect(validateLineForms(Array.from({ length: MAX_LINES }, () => ok()))).toBeNull();
  });
  it("lineBodies — 문자열 단가는 그대로, 수량만 정수, 빈 납기는 null", () => {
    expect(lineBodies([ok({ code: " A-1 ", price: " 12.50 ", delivery: "" })])).toEqual([
      { buyer_item_code: "A-1", quantity: 3, unit_price: "12.50", requested_delivery_date: null },
    ]);
  });
});
