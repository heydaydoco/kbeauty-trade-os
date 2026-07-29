import { describe, expect, it } from "vitest";
import {
  UnknownCurrencyError,
  formatMinorAmount,
  formatMoney,
  minorUnitsOf,
  toMinorAmount,
  type Currency,
} from "./money";

/** 서버 /v1/system/currencies가 주는 모양 그대로. 자릿수의 출처는 여기 하나다. */
const CURRENCIES: Currency[] = [
  { code: "KRW", minor_units: 0 },
  { code: "USD", minor_units: 2 },
  { code: "JPY", minor_units: 0 },
];

describe("금액 표시", () => {
  it("KRW는 소수 자릿수가 0이다", () => {
    expect(formatMoney(12000, "KRW", CURRENCIES)).toBe("12,000 KRW");
    expect(formatMinorAmount(12000, minorUnitsOf("KRW", CURRENCIES))).toBe("12,000");
  });

  it("USD는 소수 두 자리다 — 1234는 12.34다", () => {
    expect(formatMoney(1234, "USD", CURRENCIES)).toBe("12.34 USD");
  });

  it("★ 등록되지 않은 통화는 실패한다 (조용히 0자리로 떨어지지 않는다)", () => {
    // 0자리로 떨어뜨리면 USD 1234가 "1,234"로 보이고, 백 배 틀린 금액을
    // 사람이 그대로 믿는다.
    expect(() => formatMoney(1234, "XYZ", CURRENCIES)).toThrow(UnknownCurrencyError);
  });

  it("최소단위보다 짧은 금액도 자릿수를 채운다", () => {
    expect(formatMoney(5, "USD", CURRENCIES)).toBe("0.05 USD");
    expect(formatMoney(0, "USD", CURRENCIES)).toBe("0.00 USD");
  });

  it("음수도 부호를 잃지 않는다 (환불·차감)", () => {
    expect(formatMoney(-1234, "USD", CURRENCIES)).toBe("-12.34 USD");
  });

  it("큰 금액에 자리 구분이 들어간다", () => {
    expect(formatMoney(123456789, "USD", CURRENCIES)).toBe("1,234,567.89 USD");
    expect(formatMoney(1234567890, "KRW", CURRENCIES)).toBe("1,234,567,890 KRW");
  });

  it("최소단위는 정수만 받는다", () => {
    expect(() => toMinorAmount(12.34)).toThrow();
    expect(toMinorAmount(1234)).toBe(1234);
  });
});

// ── 자기검사: 자릿수를 화면에 심어 두지 않았는가 (부채 #9 출처 단일화) ────

/** 통화 코드를 키로 쓰는 표 (예: `{ KRW: 0, USD: 2 }`). */
const CURRENCY_TABLE = /["']?(KRW|USD|EUR|JPY|CNY|GBP)["']?\s*:\s*\d/;
/** 자릿수를 박아 넣은 상수. */
const HARDCODED_UNITS = /minor_?[Uu]nits\s*[:=]\s*\d/;
/** 최소단위 환산 산술 — 부동소수라 금지다(§2 ADR-02). */
const MINOR_ARITHMETIC = /\w\s*[/*]\s*100\b/;

/**
 * 주석을 걷어낸다.
 *
 * ★ 이 검사는 **코드**를 보는 것이지 설명을 보는 것이 아니다. 주석까지 훑으면
 *   "왜 나눗셈을 쓰면 안 되는지"를 예시와 함께 적는 순간 검사가 빨개진다 —
 *   실제로 money.ts가 자기 설명(`1234 / 100`) 때문에 걸렸다. 규칙을 지키려고
 *   설명을 지우는 일이 생기면 그건 규칙이 잘못된 것이다.
 *
 * 문자열 안의 `//`(예: URL)도 함께 잘리지만, 잘라 내는 방향이라 없는 위반을
 * 만들지는 않는다(놓칠 수는 있고, 그건 감수한다).
 */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

function violates(source: string): boolean {
  const code = stripComments(source);
  return CURRENCY_TABLE.test(code) || HARDCODED_UNITS.test(code) || MINOR_ARITHMETIC.test(code);
}

/**
 * 검사 대상 소스 전건.
 *
 * ★ 파일 시스템을 직접 읽지 않는다. 번들러가 이미 알고 있는 목록을 쓰면
 *   node 타입도, 컨테이너 바인드 마운트의 느린 IO도 필요 없다.
 */
const SOURCES = Object.entries(
  import.meta.glob("../**/*.{ts,tsx}", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>,
).filter(([path]) => !/\.test\.tsx?$/.test(path));

describe("통화 자릿수 출처 단일화", () => {
  it("스캔이 공회전이 아니다", () => {
    expect(SOURCES.length).toBeGreaterThan(10);
  });

  it("★ 통화별 자릿수를 코드에 심어 두지 않는다", () => {
    const offenders = SOURCES.filter(([, text]) => violates(text)).map(([path]) => path);

    expect(offenders).toEqual([]);
  });

  it("검사기가 실제로 잡는다 (자기검사)", () => {
    // 이 검사가 없으면 규칙이 조용히 고장 나도 위 스캔이 영원히 초록이다.
    expect(violates("const UNITS = { KRW: 0, USD: 2 };")).toBe(true);
    expect(violates("const minorUnits = 2;")).toBe(true);
    expect(violates("const won = cents / 100;")).toBe(true);
    // ★ 설명은 검사 대상이 아니다 — 주석에 예시를 적을 수 있어야 한다.
    expect(violates("// 1234 / 100 은 부동소수라 쓰지 않는다")).toBe(false);
    expect(violates("/* const UNITS = { KRW: 0 }; 이렇게 하지 말 것 */")).toBe(false);
    expect(violates("const units = minorUnitsOf(code, currencies);")).toBe(false);
  });
});
