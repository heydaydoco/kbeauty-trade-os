// 오더 보드 순수 규칙 — 필터 직렬화·사전 검사·422 필드 매핑·벌크 본문·응답 유실 판정·저장 필터 오류 (S3-1 PR-15b, 테스트 그룹 A).

import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  EMPTY_FILTER,
  boardExportPath,
  boardItemsPath,
  boardPath,
  buildBulkRequest,
  bulkErrorMessage,
  checkFilter,
  checkFilterName,
  filterFieldErrors,
  filterToQuery,
  isResponseLost,
  savedFilterError,
  type BoardCard,
} from "./order-board";

const apiError = (status: number, code: string, message = "서버 문구", detail: Record<string, unknown> = {}) =>
  new ApiError(status, { code, message, detail, requestId: null });

function card(over: Partial<BoardCard>): BoardCard {
  return {
    kind: "SO",
    id: 1,
    ref_label: "SO-2026-0001",
    buyer_partner_id: 3,
    buyer_name: "ABC",
    buyer_po_no: "PO-1",
    line_count: 1,
    total_amount: 100,
    total_text: "1.00",
    currency: "USD",
    age_days: 0,
    assignee_id: 1,
    assignee_name: "무역 담당",
    updated_at: "2026-10-01T00:00:00Z",
    version: 1,
    ...over,
  };
}

describe("필터 직렬화", () => {
  it("빈 필터는 쿼리 없음 — 보드 주소는 /v1/order-board", () => {
    expect(filterToQuery(EMPTY_FILTER)).toBe("");
    expect(boardPath(EMPTY_FILTER)).toBe("/v1/order-board");
  });

  it("키 순서 고정·q 공백 제거·통화/시장 대문자·빈 값 제외", () => {
    const filter = {
      q: "  ABC  ",
      buyer_partner_id: 7,
      assignee_id: 2,
      currency: "usd",
      dest_market_code: "us",
      created_from: "2026-09-01",
      created_to: "2026-09-30",
    };
    expect(filterToQuery(filter)).toBe(
      "q=ABC&buyer_partner_id=7&assignee_id=2&currency=USD&dest_market_code=US&created_from=2026-09-01&created_to=2026-09-30",
    );
    expect(filterToQuery({ ...EMPTY_FILTER, q: "   " })).toBe("");
  });

  it("드릴다운은 stage를 앞에, CSV는 stage 선택", () => {
    const filter = { ...EMPTY_FILTER, q: "IN-3" };
    expect(boardItemsPath(filter, "SO_ON_HOLD")).toBe("/v1/order-board/items?stage=SO_ON_HOLD&q=IN-3");
    expect(boardExportPath(filter)).toBe("/v1/order-board/export.csv?q=IN-3");
    expect(boardExportPath(EMPTY_FILTER, "SO_CONFIRMED")).toBe("/v1/order-board/export.csv?stage=SO_CONFIRMED");
  });

  it("q의 특수문자(%·&·#)는 인코딩되어 서버로 간다", () => {
    expect(filterToQuery({ ...EMPTY_FILTER, q: "50%&#" })).toBe("q=50%25%26%23");
  });
});

describe("필터 사전 검사(서버와 같은 규칙)", () => {
  it("보이지 않는 글자(탭·제로폭·한글 채움)는 q 오류", () => {
    for (const bad of ["a\tb", "a​b", "ㅤ", "a "]) {
      expect(checkFilter({ ...EMPTY_FILTER, q: bad }).q).toMatch(/보이지 않는 글자/);
    }
    expect(checkFilter({ ...EMPTY_FILTER, q: "정상 검색" })).toEqual({});
  });

  it("접수일 시작>끝은 범위 오류, 2000~2999 밖은 해당 칸 오류", () => {
    expect(checkFilter({ ...EMPTY_FILTER, created_from: "2026-10-02", created_to: "2026-10-01" }).created_range).toMatch(/늦을 수 없습니다/);
    expect(checkFilter({ ...EMPTY_FILTER, created_from: "1999-12-31" }).created_from).toMatch(/2000-01-01/);
    expect(checkFilter({ ...EMPTY_FILTER, created_to: "3000-01-01" }).created_to).toMatch(/2999-12-31/);
    expect(checkFilter({ ...EMPTY_FILTER, created_from: "2026-10-01", created_to: "2026-10-01" })).toEqual({});
  });
});

describe("422 → 필드 문구", () => {
  it("요청 검증 봉투의 위치별로 나누고 'Value error,' 접두를 뗀다", () => {
    const error = apiError(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값이 올바르지 않습니다.", {
      항목: [
        { 위치: "q", 사유: "Value error, 검색어에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다." },
        { 위치: "created_to", 사유: "Input should be less than or equal to 2999-12-31" },
        { 위치: "(본문)", 사유: "Value error, 접수일 시작은 끝보다 늦을 수 없습니다." },
      ],
    });
    const fields = filterFieldErrors(error);
    expect(fields?.q).toBe("검색어에는 줄바꿈·탭·보이지 않는 글자(제어·서식·채움 문자)를 쓸 수 없습니다.");
    // 영문 Pydantic 사유는 노출하지 않고 한국어 안내로.
    expect(fields?.created_to).toMatch(/접수일 끝을 확인/);
    expect(fields?.created_range).toBe("접수일 시작은 끝보다 늦을 수 없습니다.");
  });

  it("422가 아니거나 봉투가 아니면 null", () => {
    expect(filterFieldErrors(apiError(500, "X"))).toBeNull();
    expect(filterFieldErrors(apiError(422, "X", "m", { name: "x" }))).toBeNull();
    expect(filterFieldErrors(new Error("x"))).toBeNull();
  });
});

describe("벌크 본문", () => {
  const cards = [
    card({ kind: "SO", id: 9, version: 4 }),
    card({ kind: "INTAKE", id: 5, version: 2 }),
    card({ kind: "SO", id: 3, version: 1 }),
    card({ kind: "INTAKE", id: 1, version: 7 }),
  ];

  it("확정 액션은 자기 종류만·(종류, id) 정렬·카드 version 그대로·assignee_id 없음", () => {
    expect(buildBulkRequest("CONFIRM_SO", cards)).toEqual({
      action: "CONFIRM_SO",
      targets: [
        { kind: "SO", id: 3, expected_version: 1 },
        { kind: "SO", id: 9, expected_version: 4 },
      ],
    });
    expect(buildBulkRequest("CONFIRM_INTAKE", cards).targets.map((t) => t.id)).toEqual([1, 5]);
    expect("assignee_id" in buildBulkRequest("CONFIRM_INTAKE", cards)).toBe(false);
  });

  it("ASSIGN은 두 종류 모두(인테이크→수주)·담당자 포함 — 선택 순서가 달라도 같은 본문", () => {
    const a = buildBulkRequest("ASSIGN", cards, 2);
    const b = buildBulkRequest("ASSIGN", [...cards].reverse(), 2);
    expect(a).toEqual(b);
    expect(a.targets.map((t) => `${t.kind}:${t.id}`)).toEqual(["INTAKE:1", "INTAKE:5", "SO:3", "SO:9"]);
    expect(a.assignee_id).toBe(2);
  });
});

describe("응답 유실 판정·벌크 오류 문구", () => {
  it("네트워크(0)·504만 재전송 대상", () => {
    expect(isResponseLost(apiError(0, "CLIENT.NETWORK.UNREACHABLE"))).toBe(true);
    expect(isResponseLost(apiError(504, "CLIENT.RESPONSE.UNKNOWN"))).toBe(true);
    for (const status of [400, 403, 409, 422, 500, 502]) expect(isResponseLost(apiError(status, "X"))).toBe(false);
    expect(isResponseLost(new Error("x"))).toBe(false);
  });

  it("KEY_CONFLICT는 서버 문구+실행 안 됨·새로 고침 안내", () => {
    const text = bulkErrorMessage(apiError(409, "COMMON.IDEMPOTENCY.KEY_CONFLICT", "같은 요청 키로 다른 내용이 이미 처리되었습니다."));
    expect(text).toMatch(/^같은 요청 키로 다른 내용이 이미 처리되었습니다\./);
    expect(text).toMatch(/실행되지 않았습니다/);
  });

  it("422는 서버 문구+한글 사유만(영문 원문 제외)", () => {
    const text = bulkErrorMessage(
      apiError(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값이 올바르지 않습니다.", {
        항목: [{ 위치: "targets", 사유: "List should have at most 500 items" }],
        assignee_id: "담당자는 무역·관리자 역할이어야 합니다.",
      }),
    );
    expect(text).toBe("입력값이 올바르지 않습니다. (담당자는 무역·관리자 역할이어야 합니다.)");
  });
});

describe("저장 필터 이름·오류", () => {
  it("이름 사전 검사 — 빈 이름·60자 초과·보이지 않는 글자", () => {
    expect(checkFilterName("   ")).toMatch(/입력해 주세요/);
    expect(checkFilterName("가".repeat(61))).toMatch(/60자/);
    expect(checkFilterName("이름​")).toMatch(/보이지 않는 글자/);
    expect(checkFilterName("끝\u0085")).toMatch(/보이지 않는 글자/);
    expect(checkFilterName("미국 대기 건")).toBeNull();
  });

  it("중복 이름 409·서비스 422 name → 이름 칸, 상한 422·version 409 → 일반 문구", () => {
    expect(savedFilterError(apiError(409, "ORDER_BOARD.FILTER.DUPLICATE_NAME", "같은 이름의 저장 필터가 이미 있습니다."))).toEqual({
      name: "같은 이름의 저장 필터가 이미 있습니다.",
      general: null,
    });
    expect(savedFilterError(apiError(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값", { name: "필터 이름에는 줄바꿈…" })).name).toBe(
      "필터 이름에는 줄바꿈…",
    );
    expect(
      savedFilterError(apiError(422, "COMMON.VALIDATION.INVALID_FIELD", "입력값", { 항목: [{ 위치: "name", 사유: "String should have at most 60 characters" }] })).name,
    ).toMatch(/1~60자/);
    expect(savedFilterError(apiError(422, "ORDER_BOARD.FILTER.LIMIT_REACHED", "저장 필터는 한 사람당 20개까지"))).toEqual({
      name: null,
      general: "저장 필터는 한 사람당 20개까지",
    });
    expect(savedFilterError(apiError(409, "COMMON.CONCURRENCY.VERSION_CONFLICT")).general).toMatch(/먼저 바뀌었습니다/);
  });
});
