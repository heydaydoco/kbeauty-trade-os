// 금액 표시 (DESIGN.md §2 ADR-02 "금액=정수 최소단위+통화" / GC-G1 / 부채 #9).
//
// ■ 두 가지를 타입과 구조로 막는다
//
//   ① **최소단위와 표시값을 섞지 못하게 한다.** USD 12.34는 서버에서 1234로
//      온다. 둘 다 number라 실수로 바꿔 쓰면 100배 틀린 금액이 화면에 뜨고,
//      그 화면을 보고 사람이 견적을 낸다. 브랜디드 타입이 그 혼동을 컴파일
//      단계에서 잡는다.
//
//   ② **통화별 자릿수를 화면에 심지 않는다.** 표를 복사해 두면 통화가 추가될
//      때 한쪽만 갱신되고, 같은 금액이 서버와 화면에서 다르게 보인다 — 그
//      차이는 정산 대사에서야 드러난다. 자릿수의 유일한 출처는 서버의
//      `/v1/system/currencies`이고, 그 사실을 테스트가 지킨다.
//
// ★ 나눗셈을 쓰지 않는다. 1234 / 100 은 부동소수라 통화·자릿수가 늘어나면
//   반드시 어긋난다(§2 ADR-02가 Float을 금지하는 이유 그대로). 자릿수만큼
//   문자열에서 소수점을 옮긴다.

import { usePagedQuery } from "./paging";

/** 서버가 주는 정수 최소단위. 표시값(12.34)과 섞이지 않게 상표를 붙인다. */
export type MinorAmount = number & { readonly __brand: "MinorAmount" };

export interface Currency {
  code: string;
  minor_units: number;
}

export const CURRENCIES_QUERY_KEY = ["currencies"] as const;

/** 통화별 자릿수 표. 서버가 유일한 출처다. */
export function useCurrencies() {
  return usePagedQuery<Currency>(CURRENCIES_QUERY_KEY, "/v1/system/currencies?size=200");
}

export function toMinorAmount(value: number): MinorAmount {
  if (!Number.isInteger(value)) {
    throw new Error(`최소단위 금액은 정수여야 합니다: ${value}`);
  }
  return value as MinorAmount;
}

export class UnknownCurrencyError extends Error {
  constructor(code: string) {
    super(
      `등록되지 않은 통화입니다: ${code}. 통화별 소수 자릿수는 서버(/v1/system/currencies)가 정합니다.`,
    );
    this.name = "UnknownCurrencyError";
  }
}

export function minorUnitsOf(code: string, currencies: readonly Currency[]): number {
  const found = currencies.find((currency) => currency.code === code);
  if (!found) {
    // 조용히 0자리로 떨어뜨리지 않는다 — USD 1234가 "1,234달러"로 보이면
    // 100배 틀린 금액을 사람이 그대로 믿는다.
    throw new UnknownCurrencyError(code);
  }
  return found.minor_units;
}

/** 최소단위 정수를 사람이 읽는 문자열로. 나눗셈 없이 자릿수만 옮긴다. */
export function formatMinorAmount(amount: MinorAmount | number, minorUnits: number): string {
  const negative = amount < 0;
  const digits = String(Math.abs(amount)).padStart(minorUnits + 1, "0");
  const cut = digits.length - minorUnits;
  const whole = digits.slice(0, cut).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = minorUnits === 0 ? "" : `.${digits.slice(cut)}`;
  return `${negative ? "-" : ""}${whole}${fraction}`;
}

/** 화면에 그대로 쓰는 표시 문자열. 통화 코드를 뒤에 붙인다. */
export function formatMoney(
  amount: MinorAmount | number,
  code: string,
  currencies: readonly Currency[],
): string {
  return `${formatMinorAmount(amount, minorUnitsOf(code, currencies))} ${code}`;
}
