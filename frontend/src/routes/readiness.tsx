// 시장 준비도 매트릭스 (DESIGN.md §5.3 — 메인 화면 / S2-3 PR-3 안건 ①).
//
// ★ 이 화면은 서버가 계산해 준 값을 **그리기만** 한다. 색 규칙의 정본은 서버(rules.py)
//   하나이고, 화면이 상태로부터 색을 다시 계산하면 두 벌의 규칙이 갈라진다.
// ★ 색만으로 구분하지 않는다 — 셀마다 글자("판매가능"·"진행·임박"·"미충족"·"대상외")와
//   건수(승인/필수)를 함께 적는다(색맹·흑백 인쇄에서도 읽힌다).
// ★ 범례의 모집합 안내는 서버가 준 문구(scope_note)를 그대로 보여 준다(조건 A) — 화면과
//   API가 각자 문구를 들면 한쪽만 고쳐져 "🟢 과신 방지" 안내가 어긋난다.
// ★ 저장된 값이 아니다 — 열 때마다 계산한다(기준일을 화면에 밝힌다). 만료일을 고치면
//   다음 조회에서 바로 바뀐다.

import { useState } from "react";
import { Link } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { certificationStatusLabel, readinessAxisLabel, readinessColorLabel } from "../lib/labels";
import { type Page, usePagedList } from "../lib/paging";

export interface MatrixMarket {
  id: number;
  code: string;
  name_ko: string;
}

export interface MatrixRequirement {
  template_id: number;
  template_name: string;
  axis: string;
  target_type: string;
  target_id: number | null;
  status: string | null;
  color: string;
  certification_id: number | null;
  note: string | null;
  via_component_sku_id: number | null;
  via_component_sku_code: string | null;
}

export interface MatrixCell {
  market_id: number;
  market_code: string;
  color: string;
  required: number;
  approved: number;
  in_progress: number;
  unmet: number;
  items: MatrixRequirement[];
}

export interface MatrixRow {
  sku_id: number;
  sku_code: string;
  name_ko: string;
  kind: string;
  status: string;
  item_profile_id: number | null;
  cells: MatrixCell[];
  components: { sku_id: number; sku_code: string }[];
}

export interface MatrixPageData extends Page<MatrixRow> {
  markets: MatrixMarket[];
  as_of: string;
  scope_note: string;
}

export const MATRIX_QUERY_KEY = ["readiness", "matrix"] as const;

/** 셀 배경 — 신호등 토큰(theme.css) 위에 투명도를 얹는다. 글자색은 고정이라 대비가 유지된다. */
const CELL_CLASS: Record<string, string> = {
  GREEN: "bg-signal-green/30",
  YELLOW: "bg-signal-amber/35",
  RED: "bg-signal-red/30",
  GRAY: "bg-gray-100 text-gray-500",
};

const LEGEND_ORDER = ["GREEN", "YELLOW", "RED", "GRAY"] as const;

const LEGEND_HINT: Record<string, string> = {
  GREEN: "필수 요건이 모두 승인(만료임박 아님)",
  YELLOW: "진행 중이거나 만료임박·갱신중인 요건이 있음",
  RED: "미착수·미등록·만료·갱신중 도과·반려/중단 요건이 있음",
  GRAY: "이 시장에서 이 SKU에 걸리는 필수 요건이 없음",
};

function CellBadge({ cell }: { cell: MatrixCell }) {
  const label = readinessColorLabel(cell.color);
  return (
    <>
      <span className="cell-nowrap font-medium">{label}</span>
      {cell.required > 0 && (
        <span className="num block text-xs text-gray-700">
          {cell.approved}/{cell.required}
        </span>
      )}
    </>
  );
}

function tooltip(row: MatrixRow, cell: MatrixCell): string {
  if (cell.required === 0) return `${row.sku_code} · ${cell.market_code}: 필수 요건 없음(대상외)`;
  return (
    `${row.sku_code} · ${cell.market_code}: 필수 ${cell.required}건 — ` +
    `승인 ${cell.approved} · 진행 ${cell.in_progress} · 미충족 ${cell.unmet}`
  );
}

function DetailPanel({
  row,
  cell,
  market,
  onClose,
}: {
  row: MatrixRow;
  cell: MatrixCell;
  market: MatrixMarket | undefined;
  onClose: () => void;
}) {
  return (
    <div className="mt-6 rounded-lg border border-gray-300 p-4" aria-label="셀 상세">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">
          {row.sku_code} · {market?.name_ko ?? cell.market_code}({cell.market_code})
        </h2>
        <span className={`cell-nowrap rounded px-2 py-0.5 text-sm ${CELL_CLASS[cell.color] ?? ""}`}>
          {readinessColorLabel(cell.color)}
        </span>
        <button type="button" onClick={onClose} className="ml-auto text-sm text-gray-500 underline">
          닫기
        </button>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        {row.name_ko}
        {row.kind === "SET" && row.components.length > 0 && (
          <> · 세트 구성품: {row.components.map((c) => c.sku_code).join(", ")}</>
        )}
      </p>
      {cell.items.length === 0 ? (
        <p className="mt-3 text-sm text-gray-500">
          이 시장에서 이 SKU에 걸리는 필수 요건이 없습니다(대상외). 품목군의 요건 세트에 확정된 요건이
          연결돼 있어야 집계됩니다.
        </p>
      ) : (
        <ul className="mt-3 space-y-2 text-sm">
          {cell.items.map((item) => (
            <li
              key={`${item.template_id}-${item.axis}-${item.target_id ?? "x"}-${item.via_component_sku_id ?? "own"}`}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-1"
            >
              <span
                className={`cell-nowrap rounded px-1.5 py-0.5 text-xs ${CELL_CLASS[item.color] ?? ""}`}
              >
                {readinessColorLabel(item.color)}
              </span>
              <span className="cell-nowrap text-gray-500">{readinessAxisLabel(item.axis)}</span>
              <span className="font-medium">{item.template_name}</span>
              <span className="cell-nowrap">
                {item.status === null ? "인스턴스 없음" : certificationStatusLabel(item.status)}
              </span>
              {item.via_component_sku_code !== null && (
                <span className="cell-nowrap text-gray-500">(구성품 {item.via_component_sku_code})</span>
              )}
              {item.note !== null && <span className="text-gray-600">— {item.note}</span>}
              {item.certification_id !== null && (
                <Link
                  to={`/certifications?id=${item.certification_id}`}
                  className="cell-nowrap text-gray-700 underline"
                >
                  인증 상세
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-gray-500">
        SKU 단위 요건은 이 SKU의 인증 인스턴스와, 제품·자사·제조사 단위 요건은 각 대상의 인증
        인스턴스와 대조한 결과입니다. 요건이 등록돼 있지 않은 대상은 &quot;인스턴스
        없음&quot;(미충족)으로 표시됩니다.
      </p>
    </div>
  );
}

export function ReadinessPage() {
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState({ q: "", kind: "" });
  const [selected, setSelected] = useState<{ skuId: number; marketId: number } | null>(null);

  const params = new URLSearchParams();
  if (applied.q !== "") params.set("q", applied.q);
  if (applied.kind !== "") params.set("kind", applied.kind);
  const query = params.toString();
  const path = `/v1/readiness/matrix${query === "" ? "" : `?${query}`}`;
  const list = usePagedList<MatrixRow, MatrixPageData>([...MATRIX_QUERY_KEY, applied], path);

  const data = list.data;
  const markets = data?.markets ?? [];
  const selectedRow = data?.items.find((row) => row.sku_id === selected?.skuId);
  const selectedCell = selectedRow?.cells.find((cell) => cell.market_id === selected?.marketId);

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">시장 준비도</h1>
        <p className="mt-1 text-sm text-gray-500">
          SKU가 각 시장에서 팔 준비가 됐는지를 한눈에 봅니다. 저장된 값이 아니라 열 때마다 인증 진행
          상태와 만료일로 계산한 값이라, 만료일을 고치면 바로 바뀝니다.
          {data !== undefined && <> 기준일 {data.as_of}(KST).</>}
        </p>
      </header>

      <form
        className="mt-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setSelected(null);
          setApplied((prev) => ({ ...prev, q: search.trim() }));
        }}
      >
        <label className="text-sm">
          <span className="block text-gray-600">SKU 검색</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="코드 또는 품명"
            className="rounded border border-gray-300 px-2 py-1"
          />
        </label>
        <label className="text-sm">
          <span className="block text-gray-600">구분</span>
          <select
            value={applied.kind}
            onChange={(event) => {
              setSelected(null);
              setApplied((prev) => ({ ...prev, kind: event.target.value }));
            }}
            className="rounded border border-gray-300 px-2 py-1"
          >
            <option value="">전체</option>
            <option value="SINGLE">단품</option>
            <option value="SET">세트</option>
          </select>
        </label>
        <button type="submit" className="rounded border border-gray-300 px-3 py-1 text-sm">
          검색
        </button>
      </form>

      {/* 범례 — 색 의미와 모집합 안내(조건 A). 모집합 문구는 서버가 정본이다. */}
      <div className="mt-4 rounded-lg border border-gray-200 p-3 text-sm" aria-label="범례">
        <ul className="flex flex-wrap gap-x-5 gap-y-2">
          {LEGEND_ORDER.map((color) => (
            <li key={color} className="flex items-center gap-2" title={LEGEND_HINT[color]}>
              <span className={`cell-nowrap rounded px-2 py-0.5 ${CELL_CLASS[color]}`}>
                {readinessColorLabel(color)}
              </span>
              <span className="text-gray-600">{LEGEND_HINT[color]}</span>
            </li>
          ))}
        </ul>
        {data !== undefined && <p className="mt-2 text-gray-700">{data.scope_note}</p>}
      </div>

      {data !== undefined && markets.length === 0 && (
        <p className="mt-4 rounded border border-gray-200 p-3 text-sm text-gray-600">
          등록된 시장이 없어 표에 열이 없습니다. 시장 화면에서 시장을 먼저 등록해 주세요.
        </p>
      )}

      <div className="mt-4 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={data?.items.length === 0}
          emptyHint={
            applied.q !== "" || applied.kind !== ""
              ? "조건에 맞는 SKU가 없습니다. 검색어와 구분을 바꿔 보세요."
              : "등록된 SKU가 없습니다. SKU 화면에서 먼저 등록해 주세요."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap sticky left-0 bg-gray-50 px-3 py-2">SKU</th>
                {markets.map((market) => (
                  <th
                    key={market.id}
                    className="cell-nowrap px-3 py-2 text-center"
                    title={market.name_ko}
                  >
                    {market.code}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((row) => (
                <tr key={row.sku_id} className="border-t border-gray-100">
                  <th
                    scope="row"
                    className="sticky left-0 bg-white px-3 py-2 text-left font-normal dark:bg-black"
                  >
                    <span className="cell-nowrap font-medium">{row.sku_code}</span>
                    {row.kind === "SET" && (
                      <span className="cell-nowrap ml-1 rounded bg-gray-200 px-1 text-xs">세트</span>
                    )}
                    <span className="block text-xs text-gray-500">{row.name_ko}</span>
                  </th>
                  {row.cells.map((cell) => (
                    <td key={cell.market_id} className="p-1 text-center">
                      <button
                        type="button"
                        title={tooltip(row, cell)}
                        aria-label={`${row.sku_code} ${cell.market_code} ${readinessColorLabel(cell.color)}`}
                        aria-pressed={
                          selected?.skuId === row.sku_id && selected.marketId === cell.market_id
                        }
                        onClick={() =>
                          setSelected((prev) =>
                            prev?.skuId === row.sku_id && prev.marketId === cell.market_id
                              ? null
                              : { skuId: row.sku_id, marketId: cell.market_id },
                          )
                        }
                        className={`w-full rounded px-2 py-1 ${CELL_CLASS[cell.color] ?? ""}`}
                      >
                        <CellBadge cell={cell} />
                      </button>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      <ListPager data={data} page={list.page} onPageChange={list.setPage} className="mt-3" />

      {selectedRow !== undefined && selectedCell !== undefined && (
        <DetailPanel
          row={selectedRow}
          cell={selectedCell}
          market={markets.find((market) => market.id === selectedCell.market_id)}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  );
}
