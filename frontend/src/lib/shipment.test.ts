// 선적 lib — 라벨('기타')·국가 코드 확인·409 칸별 잔량·선행 선적 번호·멱등 키 규칙 (S3-2 PR-3b).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  canonicalCountryOf,
  countryName,
  countryText,
  createKeyKeeper,
  isResultUnknown,
  openQuantityByLine,
  partyRoleLabel,
  shipmentKindLabel,
  successorNumbers,
} from "./shipment";

const apiError = (code: string, detail: Record<string, unknown>) =>
  new ApiError(409, { code, message: "서버 문구", detail, requestId: null });

describe("선적 라벨", () => {
  it("구분·당사자 역할 한국어, 모르는 값은 '기타'", () => {
    expect(shipmentKindLabel("EXPORT")).toBe("수출");
    expect(shipmentKindLabel("IMPORT")).toBe("수입");
    expect(shipmentKindLabel("X")).toBe("기타");
    expect(partyRoleLabel("CONSIGNEE")).toBe("수하인");
    expect(partyRoleLabel("NOTIFY")).toBe("통지처");
    expect(partyRoleLabel("FORWARDER")).toBe("포워더");
    expect(partyRoleLabel("CUSTOMS_BROKER")).toBe("관세사");
    expect(partyRoleLabel("SHIPPER")).toBe("송하인");
    expect(partyRoleLabel("X")).toBe("기타");
  });
});

describe("국가 코드(ISO 3166-1 alpha-2 — 국가 마스터 없음, Intl 이름으로 확인)", () => {
  it("아는 국가는 한국어 이름, 형식 밖·모르는 코드·국가가 아닌 코드(EU·UN·ZZ)는 null", () => {
    expect(countryName("US")).toBe("미국");
    expect(countryName("KR")).toBe("대한민국");
    expect(countryName("us")).toBeNull();
    expect(countryName("USA")).toBeNull();
    expect(countryName("XX")).toBeNull();
    expect(countryName("EU")).toBeNull();
    expect(countryName("UN")).toBeNull();
    expect(countryName("ZZ")).toBeNull();
  });

  // 적대 검토 med ① — Intl.DisplayNames는 CLDR 별칭에도 이름을 준다(UK → '영국'). 비ISO 코드가 정상처럼 보이며 저장되지 않게 막는다.
  it.each([
    ["UK", "GB"],
    ["DD", "DE"],
    ["SU", "RU"],
    ["FX", "FR"],
    ["BU", "MM"],
    ["ZR", "CD"],
    ["YU", "RS"],
    ["CS", "RS"],
    ["AN", "CW"],
    ["TP", "TL"],
    ["DY", "BJ"],
    ["YD", "YE"],
  ])("별칭 %s → 이름 null·정식 코드 %s", (alias, canonical) => {
    expect(countryName(alias)).toBeNull();
    expect(canonicalCountryOf(alias)).toBe(canonical);
    expect(countryText(alias)).toBe(alias);
  });

  it("정식 코드는 별칭이 아니다", () => {
    for (const code of ["GB", "DE", "RU", "US", "KR", "CN", "JP", "XK"]) expect(canonicalCountryOf(code)).toBeNull();
    expect(countryName("GB")).toBe("영국");
  });

  it("표시는 '이름 (코드)' — 서버에 저장된 모르는 코드는 코드 그대로", () => {
    expect(countryText("JP")).toBe("일본 (JP)");
    expect(countryText("XX")).toBe("XX");
  });
});

describe("오류 detail 해석", () => {
  it("409 EXCEEDS_OPEN의 원천 라인별 남은 수량(칸별 표시)", () => {
    const map = openQuantityByLine(apiError("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", { open_quantity: { "41": 0, "42": 3 } }));
    expect([...map.entries()]).toEqual([
      [41, 0],
      [42, 3],
    ]);
    expect(openQuantityByLine(apiError("OTHER", { open_quantity: { "41": 0 } })).size).toBe(0);
    expect(openQuantityByLine(new Error("x")).size).toBe(0);
  });

  it("결과를 모르는 실패 = 네트워크(0)·5xx(504 포함), 4xx는 결과가 확정된 실패", () => {
    const at = (status: number) => new ApiError(status, { code: "X", message: "m", detail: {}, requestId: null });
    expect(isResultUnknown(at(0))).toBe(true);
    expect(isResultUnknown(at(500))).toBe(true);
    expect(isResultUnknown(at(503))).toBe(true);
    expect(isResultUnknown(at(504))).toBe(true);
    expect(isResultUnknown(at(409))).toBe(false);
    expect(isResultUnknown(at(422))).toBe(false);
    expect(isResultUnknown(new Error("x"))).toBe(false);
  });

  it("409 SUCCESSOR_ALIVE의 먼저 취소할 선적 번호", () => {
    expect(successorNumbers(apiError("TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE", { successors: ["SH-2026-0001", 3] }))).toEqual([
      "SH-2026-0001",
    ]);
    expect(successorNumbers(apiError("OTHER", { successors: ["SH-1"] }))).toEqual([]);
  });
});

describe("멱등 키 — 같은 본문 재시도 = 같은 키, 본문이 바뀌면 새 키, 성공 뒤 새 키", () => {
  let seq = 0;
  beforeEach(() => {
    seq = 0;
    vi.stubGlobal("crypto", { randomUUID: () => `k-${++seq}` });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keyFor·reset", () => {
    const keeper = createKeyKeeper();
    expect(keeper.keyFor("A")).toBe("k-1");
    expect(keeper.keyFor("A")).toBe("k-1");
    expect(keeper.keyFor("B")).toBe("k-2");
    expect(keeper.keyFor("B")).toBe("k-2");
    keeper.reset();
    expect(keeper.keyFor("B")).toBe("k-3");
  });
});
