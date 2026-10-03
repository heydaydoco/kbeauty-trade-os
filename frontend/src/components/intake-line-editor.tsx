// 오더 인테이크 라인 입력 표 (S3-1 PR-13b) — 등록·수정 화면 공용. 값은 문자열 그대로 들고 있고 산술하지 않는다(단가는 사람 표기 문자열).
// 라인 검증(형식 외 — 통화 자릿수·0 초과·납기·같은 SKU 중복)은 서버가 판정하고 한국어 안내로 돌려준다.
// 라인 이름은 상위가 준 `labels`(등록=위치, 수정=서버 라인 번호·'신규 n') — 표시·aria-label·오류 안내가 같은 이름을 쓴다.

import { MAX_LINES, lineInputId, newLineForm, type LineField, type LineForm } from "../lib/order-intake";

const cell = "rounded border border-gray-300 px-2 py-1 text-sm";

export function LineEditor({
  lines,
  labels,
  onChange,
  disabled = false,
  currency,
  invalidIds,
  errorId,
}: {
  lines: LineForm[];
  /** 행별 표시 이름(lines와 같은 길이). */
  labels: string[];
  onChange: (next: LineForm[]) => void;
  disabled?: boolean;
  /** 단가 열 머리에 붙일 통화(표기용). */
  currency: string;
  /** 형식 오류가 있는 입력의 id(aria-invalid). */
  invalidIds?: ReadonlySet<string>;
  /** 오류 목록 요소 id — 잘못된 입력이 aria-describedby로 가리킨다. */
  errorId?: string;
}) {
  function patch(key: number, over: Partial<LineForm>) {
    onChange(lines.map((line) => (line.key === key ? { ...line, ...over } : line)));
  }
  const invalidProps = (line: LineForm, field: LineField) => {
    const bad = invalidIds?.has(lineInputId(line, field)) ?? false;
    return { id: lineInputId(line, field), "aria-invalid": bad, "aria-describedby": bad ? errorId : undefined };
  };

  return (
    <fieldset className="grid gap-2" disabled={disabled}>
      <legend className="text-sm font-semibold">라인 ({lines.length}개)</legend>
      <div className="overflow-x-auto rounded border border-gray-200">
        <table className="w-full text-sm">
          <caption className="sr-only">인테이크 라인 입력</caption>
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th scope="col" className="cell-nowrap px-2 py-2 text-center">라인</th>
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
            {lines.map((line, index) => {
              const label = labels[index] ?? `라인 ${index + 1}`;
              return (
                <tr key={line.key} className="border-t border-gray-100">
                  <td className="cell-nowrap px-2 py-2 text-center">{label.replace(/^라인 /u, "")}</td>
                  <td className="px-2 py-2">
                    <input
                      aria-label={`${label} 바이어 품번`}
                      {...invalidProps(line, "code")}
                      value={line.code}
                      maxLength={100}
                      onChange={(e) => patch(line.key, { code: e.target.value })}
                      className={`${cell} w-full min-w-40`}
                    />
                  </td>
                  <td className="px-2 py-2 text-center">
                    <input
                      aria-label={`${label} 수량`}
                      {...invalidProps(line, "qty")}
                      inputMode="numeric"
                      maxLength={12}
                      value={line.qty}
                      onChange={(e) => patch(line.key, { qty: e.target.value })}
                      className={`${cell} num w-24`}
                    />
                  </td>
                  <td className="px-2 py-2 text-center">
                    <input
                      aria-label={`${label} 단가`}
                      {...invalidProps(line, "price")}
                      inputMode="decimal"
                      // 서버 단가 문자열 상한(40자)과 같은 길이 — 그 이상은 서버가 어차피 거절한다.
                      maxLength={40}
                      value={line.price}
                      onChange={(e) => patch(line.key, { price: e.target.value })}
                      className={`${cell} num w-28`}
                    />
                  </td>
                  <td className="px-2 py-2 text-center">
                    <input
                      type="date"
                      aria-label={`${label} 요청납기`}
                      value={line.delivery}
                      onChange={(e) => patch(line.key, { delivery: e.target.value })}
                      className={cell}
                    />
                  </td>
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      aria-label={`${label} 삭제`}
                      onClick={() => onChange(lines.filter((l) => l.key !== line.key))}
                      className="cell-nowrap text-signal-red underline"
                    >
                      삭제
                    </button>
                  </td>
                </tr>
              );
            })}
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
