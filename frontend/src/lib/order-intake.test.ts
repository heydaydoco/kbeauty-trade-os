// 오더 인테이크 lib — 오류 한국어화·파서·형식 검증·라벨·조사 (S3-1 PR-13b + 적대 검토 반영).

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
  lineLabels,
  mappingGuide,
  mappingStateLabel,
  newLineForm,
  parseDuplicateGroups,
  parseLineRefs,
  parsePoOccupant,
  poOccupiedText,
  validateLineForms,
  validationProblems,
  withJosaText,
  type IntakeOp,
} from "./order-intake";

const err = (code: string, status: number, detail: Record<string, unknown> = {}, message = "English server message", requestId: string | null = "r") =>
  new ApiError(status, { code, message, detail, requestId });
const offline = () => new ApiError(0, { code: "CLIENT.NETWORK.UNREACHABLE", message: "x", detail: {}, requestId: null });

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
    ["MARKETS.MARKET.NOT_REGISTERED", 422, "confirm", /도착 시장이 등록되지 않았거나 삭제되었습니다/],
  ];
  it.each(cases)("%s → 한국어", (code, status, op, pattern) => {
    const text = intakeErrorMessage(err(code, status), op);
    expect(text).toMatch(pattern);
    expect(text).not.toMatch(/English server message/);
    expect(text).not.toContain(code);
    // 비개발자 화면에 상태 코드를 노출하지 않는다.
    expect(text).not.toMatch(/\((409|422|403|404)\)/);
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
    expect(withSo).toContain("이미 등록된 수주: SO-2026-0007 (접수)");
    const withIntake = intakeErrorMessage(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, { intake_id: 8, status: "PENDING" }), "create");
    expect(withIntake).toContain("이미 등록된 대기 인테이크: #8 (대기)");
    const masked = intakeErrorMessage(err("TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", 409, {}), "create");
    expect(masked).not.toContain("이미 등록된 수주:");
    expect(masked).not.toContain("이미 등록된 대기 인테이크:");
    expect(masked).toContain("이미 다른 문서");
  });

  it("검증 422(서비스) — 등록·수정(lines[i].필드)과 확정(line_<n>.필드 — 인테이크 라인 번호) 위치를 한국어로", () => {
    const create = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { "lines[1].unit_price": "단가는 0보다 커야 합니다." }), "create");
    expect(create).toContain("라인 2 단가: 단가는 0보다 커야 합니다.");
    const confirm = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { "line_3.requested_delivery_date": "요청납기는 오늘(KST)보다 앞설 수 없습니다." }), "confirm");
    expect(confirm).toContain("라인 3 요청납기: 요청납기는 오늘(KST)보다 앞설 수 없습니다.");
    const buyer = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { buyer_partner_id: "바이어 유형 거래처가 아닙니다." }), "confirm");
    expect(buyer).toContain("바이어: 바이어 유형 거래처가 아닙니다.");
    // 한글이 없는 값(영문 코드)은 원문을 노출하지 않는다.
    const english = intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { currency: "unknown currency XX" }), "create");
    expect(english).not.toContain("unknown currency");
    expect(english).toContain("통화를 확인해 주세요.");
    expect(fieldPathLabel("lines[0].nope")).toBe("라인 1 항목");
  });

  it("검증 422(요청 검증 봉투 — handlers.py `{항목:[{위치,사유}]}`) — 위치를 한국어로, 한글 사유는 접두를 떼고, 영문 사유는 숨긴다", () => {
    const text = intakeErrorMessage(
      err("COMMON.VALIDATION.INVALID_FIELD", 422, {
        항목: [
          { 위치: "lines.0.unit_price", 사유: "String should have at most 40 characters" },
          { 위치: "reason", 사유: "Value error, 사유에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다." },
          { 위치: "lines.2", 사유: "Field required" },
        ],
      }),
      "create",
    );
    expect(text).toContain("라인 1 단가를 확인해 주세요.");
    expect(text).toContain("사유: 사유에는 줄바꿈");
    expect(text).not.toContain("Value error");
    expect(text).not.toContain("String should");
    expect(text).toContain("라인 3을 확인해 주세요.");
  });

  it("수정 화면은 lines[i]를 보낸 i번째 행의 이름(서버 라인 번호·'신규 n')으로 옮긴다", () => {
    const labelOf = (i: number) => ["라인 1", "라인 3", "신규 1"][i] ?? "?";
    expect(intakeErrorMessage(err("COMMON.VALIDATION.INVALID_FIELD", 422, { "lines[1].unit_price": "단가는 0보다 커야 합니다." }), "edit", { lineLabelOf: labelOf })).toContain(
      "라인 3 단가: 단가는 0보다 커야 합니다.",
    );
    expect(validationProblems({ 항목: [{ 위치: "lines.2.quantity", 사유: "정수여야 합니다" }] }, labelOf)).toEqual(["신규 1 수량: 정수여야 합니다"]);
  });

  it("시장 미등록·삭제 422 — 서버 detail의 위치·한국어 안내", () => {
    const text = intakeErrorMessage(err("MARKETS.MARKET.NOT_REGISTERED", 422, { dest_market_code: "등록되지 않은 시장입니다: XX. 시장 관리에서 먼저 등록해 주세요." }), "confirm");
    expect(text).toContain("도착 시장: 등록되지 않은 시장입니다: XX.");
  });

  it("평가 불능 409 — 서버 오류 번호(request_id)가 있을 때만 번호를 말한다", () => {
    expect(intakeErrorMessage(err("ORDER_INTAKE.GATE.UNRESOLVED", 409, {}, "x", "req-77"), "confirm")).toContain("오류 번호: req-77");
    const noId = intakeErrorMessage(err("ORDER_INTAKE.GATE.UNRESOLVED", 409, {}, "x", null), "confirm");
    expect(noId).not.toContain("오류 번호");
    expect(noId).toContain("관리자에게 문의");
  });

  it("복제 원본 자격 상실 — 등록 화면은 '복제 없이 등록'(불러오기 지시 없음), 확정은 불러오기", () => {
    const create = intakeErrorMessage(err("TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE", 409), "create");
    expect(create).toContain("'복제 없이 등록'");
    expect(create).not.toContain("최신 내용 불러오기");
    expect(intakeErrorMessage(err("TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE", 409), "confirm")).toContain("최신 내용 불러오기");
  });

  it("연결 끊김 — 확정 등은 같은 버튼 재시도 안내, 수정(PATCH, 멱등 키 없음)은 반영 여부부터 확인", () => {
    const confirm = intakeErrorMessage(offline(), "confirm");
    expect(confirm).toContain("연결이 끊겼습니다. 접수 확정이 처리되었을 수 있으니");
    expect(confirm).toContain("중복 처리되지 않습니다");
    const edit = intakeErrorMessage(offline(), "edit");
    expect(edit).toContain("먼저 '최신 내용 불러오기'로 반영 여부를 확인");
    expect(edit).not.toContain("같은 버튼");
    expect(intakeErrorMessage(offline(), "create")).toContain("등록이 처리되었을 수 있으니 목록에서 확인");
  });

  it("상태별 일반 문구·서버 한국어 message·기본 문구 순서(조사 일관)", () => {
    expect(intakeErrorMessage(err("X", 403), "confirm")).toBe("접수 확정은 무역·관리자만 할 수 있습니다.");
    expect(intakeErrorMessage(err("X", 403), "reject")).toBe("거부는 무역·관리자만 할 수 있습니다.");
    expect(intakeErrorMessage(err("X", 403), "edit")).toBe("수정은 무역·관리자만 할 수 있습니다.");
    expect(intakeErrorMessage(err("X", 404), "edit")).toContain("찾을 수 없습니다");
    expect(intakeErrorMessage(err("X", 409), "edit")).toContain("충돌");
    expect(intakeErrorMessage(err("X", 422), "edit")).toBe("입력값이 올바르지 않습니다. 입력 내용을 확인한 뒤 다시 시도해 주세요.");
    expect(intakeErrorMessage(err("X", 500, {}, "서버 한국어 안내"), "edit")).toBe("서버 한국어 안내");
    expect(intakeErrorMessage(err("X", 500), "edit")).toBe("수정을 처리하지 못했습니다.");
    expect(intakeErrorMessage(new Error("boom"), "resolve")).toBe("품번 다시 확인을 처리하지 못했습니다.");
    expect(intakeErrorMessage(new Error("boom"), "confirm")).toBe("접수 확정을 처리하지 못했습니다.");
  });

  it("조회 오류는 쓰기 문구와 분리된다 — 조사는 대상 이름에 맞춘다", () => {
    expect(intakeLoadErrorMessage(err("X", 403))).toBe("오더 인테이크를 볼 권한이 없습니다.");
    expect(intakeLoadErrorMessage(err("X", 403), "게이트 판정")).toBe("게이트 판정을 볼 권한이 없습니다.");
    expect(intakeLoadErrorMessage(err("X", 500), "게이트 판정")).toBe("게이트 판정을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.");
    expect(intakeLoadErrorMessage(err("X", 404))).toContain("찾을 수 없습니다");
  });

  it("복구(최신 내용 불러오기) 대상 — 409 계열·연결 끊김, 422는 아님", () => {
    expect(isIntakeRecoverable(err("ORDER_INTAKE.LINE.STALE_MAPPING", 409))).toBe(true);
    expect(isIntakeRecoverable(err("ORDER_INTAKE.LINE.UNMAPPED_ITEMS", 422))).toBe(false);
    expect(isIntakeRecoverable(offline())).toBe(true);
    expect(isIntakeRecoverable(new Error("x"))).toBe(false);
  });
});

describe("파서·라벨·조사", () => {
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
  });

  it("매핑 해소 안내 — 쓰기 역할에게만 직접 조작을 안내하고, 읽기 전용 역할에게는 담당자 몫으로", () => {
    expect(mappingGuide("MAPPED", true)).toBeNull();
    expect(mappingGuide("UNMAPPED", true)).toContain("아래 '품번 등록'에서");
    expect(mappingGuide("UNMAPPED", true)).toContain("거래처 화면에서 그 매핑을 먼저 삭제");
    expect(mappingGuide("UNMAPPED", false)).not.toContain("아래 '품번 등록'");
    expect(mappingGuide("UNMAPPED", false)).toContain("무역 담당자가");
    expect(mappingGuide("STALE", true)).toContain("'품번 다시 확인'으로");
    expect(mappingGuide("STALE", false)).not.toContain("'품번 다시 확인'으로");
    expect(mappingGuide("STALE", false)).toContain("자동으로 따라가지 않으며");
  });

  it("조사 — 끝의 괄호를 건너뛰고 앞 글자 받침으로", () => {
    expect(withJosaText("수주 SO-1 (확정)", "이/가")).toBe("수주 SO-1 (확정)이");
    expect(withJosaText("수주 SO-1 (접수)", "이/가")).toBe("수주 SO-1 (접수)가");
    expect(withJosaText("수주 SO-2026-0077", "이/가")).toBe("수주 SO-2026-0077이");
    expect(withJosaText("수주 SO-2026-0079", "이/가")).toBe("수주 SO-2026-0079가");
    expect(withJosaText("게이트 판정", "을/를")).toBe("게이트 판정을");
    expect(withJosaText("오더 인테이크", "을/를")).toBe("오더 인테이크를");
  });

  it("poOccupiedText — 번호·상태는 서버가 줄 때만, 조사는 상태 글자에 맞춘다", () => {
    expect(poOccupiedText({ kind: "SALES_ORDER", doc_number: "SO-1", status: "RECEIVED" })).toBe("수주 SO-1 (접수)가 같은 바이어 PO번호를 점유 중입니다.");
    expect(poOccupiedText({ kind: "SALES_ORDER", doc_number: "SO-1", status: "CONFIRMED" })).toBe("수주 SO-1 (확정)이 같은 바이어 PO번호를 점유 중입니다.");
    expect(poOccupiedText({ kind: "SALES_ORDER", doc_number: null, status: null })).toBe("수주가 같은 바이어 PO번호를 점유 중입니다(문서 번호는 무역·관리자만 확인할 수 있습니다).");
    expect(poOccupiedText({ kind: "INTAKE", doc_number: null, status: null })).toContain("다른 인테이크가");
  });
});

describe("라인 형식 검증 — 형식만(금액 산술 0), 문제는 모두 모은다", () => {
  const ok = (over = {}) => newLineForm({ code: "A-1", qty: "3", price: "12.50", ...over });
  const messages = (lines: ReturnType<typeof ok>[], labels?: string[]) => validateLineForms(lines, labels).map((p) => p.message);
  it("필수·정수 수량·금액 문자열", () => {
    expect(messages([])).toEqual(["라인을 1개 이상 추가해 주세요."]);
    expect(messages([ok({ code: " " })])).toEqual(["라인 1의 바이어 품번을 입력해 주세요."]);
    expect(messages([ok({ qty: "0" })])).toEqual(["라인 1의 수량은 1 이상의 정수로 입력해 주세요."]);
    expect(messages([ok({ qty: "1.5" })])).toHaveLength(1);
    expect(messages([ok({ qty: "100000000" })])).toHaveLength(1);
    expect(messages([ok({ price: "" })])).toEqual(["라인 1의 단가는 숫자(예: 12.34)로 입력해 주세요."]);
    expect(messages([ok({ price: "12,5" })])).toHaveLength(1);
    expect(messages([ok({ price: "-1" })])).toHaveLength(1);
    expect(messages([ok()])).toEqual([]);
    // 같은 SKU 중복·0 단가·납기는 서버 판정 — 형식 검증은 통과시킨다.
    expect(messages([ok({ price: "0" }), ok({ code: "A-1" })])).toEqual([]);
  });

  it("여러 문제를 한 번에 — 각 문제는 그 입력의 id를 가리킨다", () => {
    const a = ok({ code: "", qty: "x" });
    const b = ok({ price: "" });
    const problems = validateLineForms([a, b]);
    expect(problems.map((p) => p.message)).toEqual([
      "라인 1의 바이어 품번을 입력해 주세요.",
      "라인 1의 수량은 1 이상의 정수로 입력해 주세요.",
      "라인 2의 단가는 숫자(예: 12.34)로 입력해 주세요.",
    ]);
    expect(problems.map((p) => p.inputId)).toEqual([`intake-line-${a.key}-code`, `intake-line-${a.key}-qty`, `intake-line-${b.key}-price`]);
  });

  it("라인 이름 — 등록은 위치, 수정은 서버 라인 번호와 '신규 n'(라인을 지워도 남은 이름이 바뀌지 않음)", () => {
    const l1 = ok({ lineNo: 1 });
    const l3 = ok({ lineNo: 3 });
    const n1 = ok();
    const n2 = ok();
    expect(lineLabels([l1, l3, n1, n2], "edit")).toEqual(["라인 1", "라인 3", "신규 1", "신규 2"]);
    expect(lineLabels([l1, l3, n1], "create")).toEqual(["라인 1", "라인 2", "라인 3"]);
    expect(messages([l1, ok({ lineNo: 3, qty: "0" })], lineLabels([l1, ok({ lineNo: 3 })], "edit"))).toEqual(["라인 3의 수량은 1 이상의 정수로 입력해 주세요."]);
  });

  it("라인 수 상한", () => {
    expect(messages(Array.from({ length: MAX_LINES + 1 }, () => ok()))).toEqual(["라인은 200개 이하여야 합니다."]);
    expect(messages(Array.from({ length: MAX_LINES }, () => ok()))).toEqual([]);
  });

  it("lineBodies — 문자열 단가는 그대로, 수량만 정수, 빈 납기는 null", () => {
    expect(lineBodies([ok({ code: " A-1 ", price: " 12.50 ", delivery: "" })])).toEqual([
      { buyer_item_code: "A-1", quantity: 3, unit_price: "12.50", requested_delivery_date: null },
    ]);
  });
});
