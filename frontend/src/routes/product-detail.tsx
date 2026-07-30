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
import {
  approvalStatusLabel,
  classificationLabel,
  materialTypeLabel,
  orEmpty,
  originStatusLabel,
  statusLabel,
} from "../lib/labels";
import { formatMoney, useCurrencies } from "../lib/money";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import { INGREDIENTS_QUERY_KEY, type Ingredient } from "./ingredients";
import { MATERIALS_QUERY_KEY, type Material } from "./materials";
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

interface BomLine {
  id: number;
  material_id: number;
  material_code: string;
  material_name_ko: string;
  material_type: string;
  hs6: string | null;
  quantity: string;
  quantity_unit: string;
  /**
   * 원가 — **권한이 없으면 필드 자체가 없다**(ADR-0024). null이 아니라 부재라
   * 옵셔널로 선언한다. 화면은 `in` 검사가 아니라 undefined로 갈린다.
   */
  unit_cost?: number | null;
  currency?: string | null;
  origin_status: string;
  note: string | null;
}

interface LabelRow {
  id: number;
  sku_id: number;
  sku_code: string;
  country_code: string;
  label_version: number;
  language: string;
  approval_status: string;
  cut_in_date: string | null;
  inci_local_verified: boolean;
  origin_mark_verified: boolean;
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
  const screeningPrefix = ["product", productId, "screening"] as const;
  const bomKey = ["product", productId, "boms"] as const;
  const labelsKey = ["product", productId, "labels"] as const;

  const product = useQuery({
    queryKey: productKey,
    queryFn: () => apiFetch<Product>(`/v1/products/${productId}`),
  });
  const formula = usePagedQuery<FormulaLine>(formulaKey, `/v1/products/${productId}/ingredients`);
  // 드롭다운은 첫 페이지(50건)로는 모자란다 — 통화 선례(money.ts)대로 상한을
  // 명시해 받는다. 목록 화면의 키(INGREDIENTS_QUERY_KEY)와 섞이면 안 된다.
  const ingredientOptions = usePagedQuery<Ingredient>(
    [...INGREDIENTS_QUERY_KEY, "options"] as const,
    "/v1/ingredients?size=200",
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
      // 전성분이 바뀌면 떠 있는 스크리닝 리포트는 낡은 것이다 — 무효화하지
      // 않으면 "매 요청 계산"(ADR-0022)이 화면 캐시에서 무너진다(리뷰 검출).
      void client.invalidateQueries({ queryKey: screeningPrefix });
    },
  });

  // 자재 BOM (§4.4). 단가는 권한에 따라 응답에서 빠진다(ADR-0024).
  const bom = usePagedQuery<BomLine>(bomKey, `/v1/products/${productId}/boms`);
  const materialOptions = usePagedQuery<Material>(
    [...MATERIALS_QUERY_KEY, "options"] as const,
    "/v1/materials?size=200",
    canEdit,
  );
  const currencies = useCurrencies();
  const [bomLine, setBomLine] = useState({
    material_id: "",
    quantity: "",
    quantity_unit: "g",
    unit_cost: "",
    currency: "KRW",
    origin_status: "UNKNOWN",
  });

  const addBomLine = useMutation({
    mutationFn: () =>
      apiFetch<BomLine>(`/v1/products/${productId}/boms`, {
        method: "POST",
        body: {
          material_id: Number(bomLine.material_id),
          quantity: bomLine.quantity,
          quantity_unit: bomLine.quantity_unit,
          // 단가는 통화와 한 쌍이다 — 비우면 "단가 미상"으로 등록된다.
          unit_cost: bomLine.unit_cost === "" ? undefined : bomLine.unit_cost,
          currency: bomLine.unit_cost === "" ? undefined : bomLine.currency,
          origin_status: bomLine.origin_status,
        },
      }),
    onSuccess: () => {
      setBomLine((previous) => ({ ...previous, material_id: "", quantity: "", unit_cost: "" }));
      void client.invalidateQueries({ queryKey: bomKey });
    },
  });

  // 원가 열을 보일지는 **서버가 필드를 줬는지**로 정한다. 화면이 역할을 다시
  // 판정하면 두 판정이 갈리고, 그때 화면 쪽이 틀리면 원가가 새거나 권한자에게
  // 안 보인다(§18.1 "인가는 API에서 강제" — 화면은 그 결과를 따른다).
  const showsCost = (bom.data?.items ?? []).some((line) => "unit_cost" in line);

  // 라벨 롤업 — 읽기 전용이다(§14 ③). 등록·수정은 SKU 상세에서 한다(§4.5).
  const labels = usePagedQuery<LabelRow>(labelsKey, `/v1/products/${productId}/labels`);

  // 스크리닝 — 대상국을 정하고 실행해야 조회한다(자동 실행하지 않는다).
  const [countryInput, setCountryInput] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [countryInputError, setCountryInputError] = useState<string | null>(null);

  const screening = useQuery({
    queryKey: [...screeningPrefix, targets] as const,
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
                {ingredientOptions.data?.items.map((ingredient) => (
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
            {ingredientOptions.data?.items.length === 0 && (
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

        <p className="mt-3 text-sm text-gray-500">전체 {formula.data?.total ?? 0}건</p>

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
            const parsed = parseCountries(countryInput);
            if (parsed.length === 0) {
              // 구분자만 넣으면 required는 통과한다 — 무반응으로 두면 사용자는
              // 고장으로 읽는다(리뷰 검출).
              setCountryInputError("국가코드는 영문 2자입니다. 예: US, EU");
              return;
            }
            setCountryInputError(null);
            setTargets(parsed);
            // 같은 대상국 재실행도 새로 계산한다 — 키가 같으면 React Query는
            // 다시 요청하지 않으므로, 실행 버튼이 곧 무효화다(ADR-0022 "매 요청
            // 계산"의 화면 몫 — 리뷰 검출).
            void client.invalidateQueries({ queryKey: screeningPrefix });
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
          {(countryInputError ?? fieldMessage(screening.error)) && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {countryInputError ?? fieldMessage(screening.error)}
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
                      <th className="cell-nowrap px-4 py-2 num">최종확인일</th>
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
                        <td className="cell-nowrap px-4 py-2 num">
                          {orEmpty(finding.last_verified_on)}
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

      <div>
        <h2 className="text-lg font-semibold">자재 BOM</h2>
        <p className="mt-1 text-sm text-gray-500">
          이 처방에 들어가는 자재와 소요량·원산지입니다. 원산지를 고르지 않으면{" "}
          <strong>미상</strong>이고, 미상은 원산지 판정에서 역외로 간주됩니다. 매입 단가는 원가라
          권한이 없으면 열이 보이지 않습니다.
        </p>

        {canEdit && (
          <form
            className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              addBomLine.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">자재</span>
              <select
                name="material_id"
                required
                value={bomLine.material_id}
                onChange={(event) =>
                  setBomLine((previous) => ({ ...previous, material_id: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">선택하세요</option>
                {materialOptions.data?.items.map((material) => (
                  <option key={material.id} value={material.id}>
                    {material.name_ko} ({material.material_code})
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">소요량</span>
              <input
                name="quantity"
                required
                inputMode="decimal"
                value={bomLine.quantity}
                onChange={(event) =>
                  setBomLine((previous) => ({ ...previous, quantity: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">단위</span>
              <input
                name="quantity_unit"
                required
                maxLength={10}
                value={bomLine.quantity_unit}
                onChange={(event) =>
                  setBomLine((previous) => ({ ...previous, quantity_unit: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">원산지</span>
              <select
                name="origin_status"
                value={bomLine.origin_status}
                onChange={(event) =>
                  setBomLine((previous) => ({ ...previous, origin_status: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="UNKNOWN">미상 (판정 시 역외 간주)</option>
                <option value="DOMESTIC">역내</option>
                <option value="FOREIGN">역외</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">매입단가 (선택)</span>
              {/* 사람이 쓰는 표기 그대로 보낸다 — 최소단위 환산은 서버가 한다. */}
              <input
                name="unit_cost"
                inputMode="decimal"
                value={bomLine.unit_cost}
                onChange={(event) =>
                  setBomLine((previous) => ({ ...previous, unit_cost: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">통화</span>
              <select
                name="currency"
                value={bomLine.currency}
                onChange={(event) =>
                  setBomLine((previous) => ({ ...previous, currency: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                {currencies.data?.items.map((currency) => (
                  <option key={currency.code} value={currency.code}>
                    {currency.code}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={addBomLine.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              자재 추가
            </button>
            {materialOptions.data?.items.length === 0 && (
              <p className="w-full text-sm text-gray-500">
                먼저 자재 화면에서 자재를 등록하세요. BOM은 등록된 자재에서 고릅니다.
              </p>
            )}
            {fieldMessage(addBomLine.error) && (
              <p role="alert" className="w-full text-sm text-signal-red">
                {fieldMessage(addBomLine.error)}
              </p>
            )}
          </form>
        )}

        <p className="mt-3 text-sm text-gray-500">전체 {bom.data?.total ?? 0}건</p>

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={bom.isPending}
            error={bom.error}
            isEmpty={bom.data?.items.length === 0}
            emptyHint="등록된 BOM이 없습니다. 자재와 소요량을 추가하세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2">자재코드</th>
                  <th className="px-4 py-2">자재명(국문)</th>
                  <th className="cell-nowrap px-4 py-2">유형</th>
                  <th className="cell-nowrap px-4 py-2 num">HS6</th>
                  <th className="cell-nowrap px-4 py-2 num">소요량</th>
                  <th className="cell-nowrap px-4 py-2 num">원산지</th>
                  {/* 원가 열은 권한이 있을 때만 나온다 — 응답에 필드가 없으면
                      열도 없다(빈 열을 두면 "0원"으로 읽힌다). */}
                  {showsCost && <th className="cell-nowrap px-4 py-2 num">매입단가</th>}
                </tr>
              </thead>
              <tbody>
                {bom.data?.items.map((line) => (
                  <tr key={line.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2">{line.material_code}</td>
                    <td className="px-4 py-2">{line.material_name_ko}</td>
                    <td className="cell-nowrap px-4 py-2">
                      {materialTypeLabel(line.material_type)}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{orEmpty(line.hs6)}</td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {line.quantity} {line.quantity_unit}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {originStatusLabel(line.origin_status)}
                    </td>
                    {showsCost && (
                      <td className="cell-nowrap px-4 py-2 num">
                        {line.unit_cost === null || line.unit_cost === undefined
                          ? orEmpty(null)
                          : formatMoney(
                              line.unit_cost,
                              line.currency ?? "KRW",
                              currencies.data?.items ?? [],
                            )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">라벨 (소속 SKU 롤업)</h2>
        <p className="mt-1 text-sm text-gray-500">
          라벨은 SKU×시장 단위입니다 — 여기서는 이 처방에 속한 SKU들의 라벨 판을 모아 보여 줍니다.
          등록·수정은 SKU 상세에서 합니다.
        </p>

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={labels.isPending}
            error={labels.error}
            isEmpty={labels.data?.items.length === 0}
            emptyHint="이 처방의 SKU에 등록된 라벨이 없습니다. SKU 상세에서 라벨 판을 등록하세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2">품번</th>
                  <th className="cell-nowrap px-4 py-2 num">시장</th>
                  <th className="cell-nowrap px-4 py-2 num">판번</th>
                  <th className="cell-nowrap px-4 py-2 num">언어</th>
                  <th className="cell-nowrap px-4 py-2 num">승인상태</th>
                  <th className="cell-nowrap px-4 py-2 num">컷인일</th>
                  <th className="cell-nowrap px-4 py-2 num">INCI 현지어</th>
                  <th className="cell-nowrap px-4 py-2 num">원산지 표기</th>
                </tr>
              </thead>
              <tbody>
                {labels.data?.items.map((label) => (
                  <tr key={label.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2">
                      <Link to={`/skus/${label.sku_id}`} className="underline">
                        {label.sku_code}
                      </Link>
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{label.country_code}</td>
                    <td className="cell-nowrap px-4 py-2 num">{label.label_version}</td>
                    <td className="cell-nowrap px-4 py-2 num">{label.language}</td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {approvalStatusLabel(label.approval_status)}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{orEmpty(label.cut_in_date)}</td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {label.inci_local_verified ? "○" : ""}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {label.origin_mark_verified ? "○" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </div>
    </section>
  );
}
