// 코드값 → 한국어 라벨 (DESIGN.md §14 한국어 UI / §18.4 에러 메시지 이원화).
//
// ★ 서버는 코드 그대로 준다(ACTIVE / SINGLE). 라벨을 서버가 붙이면 CSV·API·화면이
//   같은 값을 다르게 부르게 되고, S1-3의 엑셀 왕복 편집(§12.2)에서 되돌릴 수
//   없다. 번역은 화면의 일이고, 그 표는 여기 한 곳에만 둔다.

const STATUS: Record<string, string> = {
  ACTIVE: "판매중",
  DISCONTINUED: "단종",
};

const KIND: Record<string, string> = {
  SINGLE: "단품",
  SET: "세트",
};

/** 모르는 코드는 감추지 않고 그대로 보여 준다 — 빈칸은 원인 추적을 막는다. */
function translate(table: Record<string, string>, code: string): string {
  return table[code] ?? code;
}

export const statusLabel = (code: string): string => translate(STATUS, code);
export const kindLabel = (code: string): string => translate(KIND, code);

/** 값이 없을 때 표에 넣는 표시. 빈칸은 "누락"과 "0"을 구분하지 못한다. */
export const EMPTY = "—";

export function orEmpty(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return EMPTY;
  return String(value);
}
