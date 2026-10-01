// 오더 인테이크 라인 입력 표 (S3-1 PR-13b) — 등록·수정 화면 공용. 값은 문자열 그대로 들고 있고 산술하지 않는다(단가는 사람 표기 문자열).
// 라인 검증(형식 외 — 통화 자릿수·0 초과·납기·같은 SKU 중복)은 서버가 판정하고 한국어 안내로 돌려준다.

import { MAX_LINES, newLineForm, type LineForm } from "../lib/order-intake";

const cell = "rounded border border-gray-300 px-2 py-1 text-sm";

export function LineEditor({
  lines,
  onChange,
  disabled = false,
  currency,
}: {
  lines: LineForm[];
  onChange: (next: LineForm[]) => void;
  disabled?: boolean;
  /** 단가 열 머리에 붙일 통화(표기용). */
  currency: string;
}) {
  function patch(key: number, over: Partial<LineForm>) {
    onChange(lines.map((line) => (line.key === key ? { ...line, ...over } : line)));
  }

  return (
    <fieldset className="grid gap-2" disabled={disabled}>
      <legend className="text-sm font-semibold">라인 ({lines.length}개)</legend>
      <div className="overflow-x-auto rounded border border-gray-200">
        <table className="w-full text-sm">
          <caption className="sr-only">인테이크 라인 입력</caption>
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th scope="col" className="cell-nowrap px-2 py-2 text-center">번호</th>
              <th scope="col" className="cell-nowrap px-2 py-2">바이어 품번</th>
              <th scope="col" className="cell-nowrap px-2 py-2 text-center">수량</th>
              <th scope="col" className="cell-nowrap px-2 py-2 text-center">단가{currency ? ` (${currency})` : ""}</th>
              <th scope="col" className="cell-nowrap px-2 py-2 text-center">요청납기</th>
              <th scope="col" className="cell-nowrap px-2 py-2">
                <span className="sr-only">라인 삭제</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line, index) => (
              <tr key={line.key} className="border-t border-gray-100">
                <td className="num cell-nowrap px-2 py-2">{index + 1}</td>
                <td className="px-2 py-2">
                  <input
                    aria-label={`라인 ${index + 1} 바이어 품번`}
                    value={line.code}
                    maxLength={100}
                    onChange={(e) => patch(line.key, { code: e.target.value })}
                    className={`${cell} w-full min-w-40`}
                  />
                </td>
                <td className="px-2 py-2 text-center">
                  <input
                    aria-label={`라인 ${index + 1} 수량`}
                    inputMode="numeric"
                    value={line.qty}
                    onChange={(e) => patch(line.key, { qty: e.target.value })}
                    className={`${cell} num w-24`}
                  />
                </td>
                <td className="px-2 py-2 text-center">
                  <input
                    aria-label={`라인 ${index + 1} 단가`}
                    inputMode="decimal"
                    value={line.price}
                    onChange={(e) => patch(line.key, { price: e.target.value })}
                    className={`${cell} num w-28`}
                  />
                </td>
                <td className="px-2 py-2 text-center">
                  <input
                    type="date"
                    aria-label={`라인 ${index + 1} 요청납기`}
                    value={line.delivery}
                    onChange={(e) => patch(line.key, { delivery: e.target.value })}
                    className={cell}
                  />
                </td>
                <td className="px-2 py-2">
                  <button
                    type="button"
                    aria-label={`라인 ${index + 1} 삭제`}
                    onClick={() => onChange(lines.filter((l) => l.key !== line.key))}
                    className="cell-nowrap text-signal-red underline"
                  >
                    삭제
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => onChange([...lines, newLineForm()])}
          disabled={lines.length >= MAX_LINES}
          className="cell-nowrap rounded border border-gray-300 px-3 py-1 text-sm disabled:opacity-50"
        >
          라인 추가
        </button>
        <span className="break-keep text-xs text-gray-500">
          단가는 숫자로 입력합니다(예: 12.34). 같은 SKU로 해석되는 라인이 둘 이상이면 서버가 거절합니다.
        </span>
      </div>
    </fieldset>
  );
}
