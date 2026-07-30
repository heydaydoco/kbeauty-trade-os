// 제품(처방) 상세 — 전성분 + 성분 스크리닝 (DESIGN.md §4.3 / §14 ③ / WBS S1-2).
//
// ★ 스크리닝은 판정이 아니다(§1 비범위). 등록된 규칙과 전성분의 기계적 대조를
//   보여줄 뿐이고, 결과는 저장되지 않으며, 어떤 것도 차단하지 않는다. 화면 문구에
//   "적합/통과/합격" 류의 판정 워딩을 쓰지 않는다(웹 세션 판정 D-② — 테스트가
//   부재를 지킨다).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { classificationLabel, orEmpty, statusLabel } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import { INGREDIENTS_QUERY_KEY, type Ingredient } from "./ingredients";
import type { Product } from "./products";

interface FormulaLine {
  id: number;
  ingredient_id: number;
  inci_name: string;
  ingredient_name_ko: string | null;
  concentration_pct: string | null;
  display_order: number;
}

interface ScreeningFinding {
  classification: string;
  country_code: string;
  ingredient_id: number;
  inci_name: string;
  ingredient_name_ko: string | null;
  display_order: number;
  concentration_pct: string | null;
  max_concentration_pct: string | null;
  source_url: string | null;
  last_verified_on: string | null;
  note: string | null;
}

interface ScreeningReport {
  product_id: number;
  countries: string[];
  notice: string;
  checked_ingredient_count: number;
  within_limit_count: number;
  findings: ScreeningFinding[];
}

/** "us, eu" → ["US","EU"] (중복 제거·순서 보존). */
function parseCountries(raw: string): string[] {
  const seen: string[] = [];
  for (const token of raw.split(/[,\s]+/)) {
    const code = token.trim().toUpperCase();
    if (code !== "" && !seen.includes(code)) seen.push(code);
  }
  return seen;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 border-b border-gray-100 py-2 text-sm">
      <span className="cell-nowrap w-32 shrink-0 text-gray-500">{label}</span>
      <span>{children}</span>
    </div>
  );
}

export function ProductDetailPage() {
  const { productId } = useParams();
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "TRADE");

  const productKey = ["product", productId] as const;
  const formulaKey = ["product", productId, "ingredients"] as const;

  const product = useQuery({
    queryKey: productKey,
    queryFn: () => apiFetch<Product>(`/v1/products/${productId}`),
  });
  const formula = usePagedQuery<FormulaLine>(formulaKey, `/v1/products/${productId}/ingredients`);
  const ingredients = usePagedQuery<Ingredient>(
    INGREDIENTS_QUERY_KEY,
    "/v1/ingredients",
    canEdit,
  );

  const [line, setLine] = useState({ ingredient_id: "", concentration_pct: "", display_order: "" });

  const addLine = useMutation({
    mutationFn: () =>
      apiFetch<FormulaLine>(`/v1/products/${productId}/ingredients`, {
        method: "POST",
        body: {
          ingredient_id: Number(line.ingredient_id),
          // 함량은 비공개 처방이 실무에 있다 — 비우면 아예 보내지 않는다.
          concentration_pct: line.concentration_pct === "" ? undefined : line.concentration_pct,
          display_order: Number(line.display_order),
        },
      }),
    onSuccess: () => {
      setLine({ ingredient_id: "", concentration_pct: "", display_order: "" });
      void client.invalidateQueries({ queryKey: formulaKey });
    },
  });

  // 스크리닝 — 대상국을 정하고 실행해야 조회한다(자동 실행하지 않는다).
  const [countryInput, setCountryInput] = useState("");
  const [targets, setTargets] = useState<string[]>([]);

  const screening = useQuery({
    queryKey: ["product", productId, "screening", targets] as const,
    queryFn: () => {
      const query = new URLSearchParams();
      for (const code of targets) query.append("country", code);
      return apiFetch<ScreeningReport>(`/v1/products/${productId}/screening?${query.toString()}`);
    },
    enabled: targets.length > 0,
  });

  if (product.isPending) return <p className="text-gray-500">불러오는 중…</p>;
  if (product.error || !product.data) {
    return (
      <div role="alert" className="text-signal-red">
        <p>{fieldMessage(product.error) ?? "제품을 찾을 수 없습니다."}</p>
        <Link to="/products" className="mt-2 inline-block text-sm text-gray-700 underline">
          목록으로
        </Link>
      </div>
    );
  }

  const item = product.data;
  const report = screening.data;

  return (
    <section className="flex flex-col gap-8">
      <header>
        <Link to="/products" className="text-sm text-gray-500 underline">
          ← 제품 목록
        </Link>
        <h1 className="mt-2 text-2xl font-bold">{item.name_ko}</h1>
        <p className="mt-1 text-sm text-gray-500">
          {item.product_code} · {statusLabel(item.status)}
        </p>
      </header>

      <div>
        <h2 className="text-lg font-semibold">기본 정보</h2>
        <div className="mt-2">
          <Row label="브랜드">{item.brand_name_ko}</Row>
          <Row label="제품명(영문)">{orEmpty(item.name_en)}</Row>
          <Row label="품목군">{orEmpty(item.item_profile_name_ko)}</Row>
          <Row label="설명">{orEmpty(item.description)}</Row>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">전성분</h2>
        <p className="mt-1 text-sm text-gray-500">
          표시순서대로 나열됩니다 — INCI 라벨 표기 순서의 원천입니다. 함량은 비공개 처방이면 비워
          둘 수 있고, 그 성분은 스크리닝에서 보수적으로 분류됩니다.
        </p>

        {canEdit && (
          <form
            className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              addLine.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">성분</span>
              <select
                name="ingredient_id"
                required
                value={line.ingredient_id}
                onChange={(event) =>
                  setLine((previous) => ({ ...previous, ingredient_id: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">선택하세요</option>
                {ingredients.data?.items.map((ingredient) => (
                  <option key={ingredient.id} value={ingredient.id}>
                    {ingredient.inci_name}
                    {ingredient.name_ko ? ` (${ingredient.name_ko})` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">함량(%, 선택)</span>
              <input
                name="concentration_pct"
                inputMode="decimal"
                value={line.concentration_pct}
                onChange={(event) =>
                  setLine((previous) => ({ ...previous, concentration_pct: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">표시순서</span>
              <input
                name="display_order"
                required
                inputMode="numeric"
                value={line.display_order}
                onChange={(event) =>
                  setLine((previous) => ({ ...previous, display_order: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <button
              type="submit"
              disabled={addLine.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              성분 추가
            </button>
            {ingredients.data?.items.length === 0 && (
              <p className="w-full text-sm text-gray-500">
                먼저 성분 화면에서 성분을 등록하세요. 전성분은 등록된 성분에서 고릅니다.
              </p>
            )}
            {fieldMessage(addLine.error) && (
              <p role="alert" className="w-full text-sm text-signal-red">
                {fieldMessage(addLine.error)}
              </p>
            )}
          </form>
        )}

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={formula.isPending}
            error={formula.error}
            isEmpty={formula.data?.items.length === 0}
            emptyHint="등록된 전성분이 없습니다. 성분과 표시순서를 추가하세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2 num">순서</th>
                  <th className="px-4 py-2">INCI명</th>
                  <th className="px-4 py-2">표시명(국문)</th>
                  <th className="cell-nowrap px-4 py-2 num">함량(%)</th>
                </tr>
              </thead>
              <tbody>
                {formula.data?.items.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2 num">{row.display_order}</td>
                    <td className="px-4 py-2">{row.inci_name}</td>
                    <td className="px-4 py-2">{orEmpty(row.ingredient_name_ko)}</td>
                    <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.concentration_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">성분 스크리닝</h2>
        <p className="mt-1 text-sm text-gray-500">
          등록된 국가별 규칙과 전성분의 기계적 대조입니다. 결과는 저장되지 않고, 아무것도 차단하지
          않습니다 — 판매 가부 판단은 근거를 확인한 사람이 합니다.
        </p>

        <form
          className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            setTargets(parseCountries(countryInput));
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">대상국 (쉼표로 여러 개)</span>
            <input
              name="countries"
              required
              placeholder="US, EU"
              value={countryInput}
              onChange={(event) => setCountryInput(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <button
            type="submit"
            disabled={screening.isFetching}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {screening.isFetching ? "대조 중…" : "스크리닝 실행"}
          </button>
          {fieldMessage(screening.error) && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {fieldMessage(screening.error)}
            </p>
          )}
        </form>

        {report && (
          <div className="mt-3">
            <p className="text-sm">
              <span className="rounded bg-gray-100 px-2 py-1 font-semibold">{report.notice}</span>
              <span className="ml-3 text-gray-600">
                대상국 {report.countries.join("·")} · 검사 성분 {report.checked_ingredient_count}건
                · 한도 이내 {report.within_limit_count}건 · 검출 {report.findings.length}건
              </span>
            </p>

            <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
              <ListState
                isPending={false}
                error={null}
                isEmpty={report.findings.length === 0}
                emptyHint="검출된 항목이 없습니다. 근거는 성분별 규칙 화면에서 직접 확인하세요."
              >
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left">
                    <tr>
                      <th className="cell-nowrap px-4 py-2 num">분류</th>
                      <th className="cell-nowrap px-4 py-2 num">국가</th>
                      <th className="px-4 py-2">INCI명</th>
                      <th className="cell-nowrap px-4 py-2 num">함량(%)</th>
                      <th className="cell-nowrap px-4 py-2 num">한도(%)</th>
                      <th className="px-4 py-2">근거</th>
                      <th className="px-4 py-2">비고</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.findings.map((finding) => (
                      <tr
                        key={`${finding.ingredient_id}-${finding.country_code}`}
                        className="border-t border-gray-100"
                      >
                        <td className="cell-nowrap px-4 py-2 num">
                          {classificationLabel(finding.classification)}
                        </td>
                        <td className="cell-nowrap px-4 py-2 num">{finding.country_code}</td>
                        <td className="px-4 py-2">{finding.inci_name}</td>
                        <td className="cell-nowrap px-4 py-2 num">
                          {orEmpty(finding.concentration_pct)}
                        </td>
                        <td className="cell-nowrap px-4 py-2 num">
                          {orEmpty(finding.max_concentration_pct)}
                        </td>
                        <td className="px-4 py-2">
                          {finding.source_url ? (
                            <a
                              href={finding.source_url}
                              className="underline"
                              rel="noreferrer noopener"
                            >
                              링크
                            </a>
                          ) : (
                            <span className="text-gray-500">근거 없음 — 확인 필요</span>
                          )}
                        </td>
                        <td className="px-4 py-2">{orEmpty(finding.note)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ListState>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
