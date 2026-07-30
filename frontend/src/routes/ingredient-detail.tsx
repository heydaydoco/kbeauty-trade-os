// 성분 상세 — 국가별 금지·제한 규칙 (DESIGN.md §4.3 / ADR-03).
//
// ★ 규칙은 **기록**이지 판정이 아니다(§1 비범위 — HS 세번과 같은 지위).
//   화면에 "규제 자동 조회" 버튼을 만들지 않는다.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { orEmpty, ruleTypeLabel } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { Ingredient } from "./ingredients";

interface IngredientRule {
  id: number;
  country_code: string;
  rule_type: string;
  max_concentration_pct: string | null;
  source_url: string;
  last_verified_on: string;
}

export function IngredientDetailPage() {
  const { ingredientId } = useParams();
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "TRADE");

  const ingredientKey = ["ingredient", ingredientId] as const;
  const rulesKey = ["ingredient", ingredientId, "rules"] as const;

  const ingredient = useQuery({
    queryKey: ingredientKey,
    queryFn: () => apiFetch<Ingredient>(`/v1/ingredients/${ingredientId}`),
  });
  const rules = usePagedQuery<IngredientRule>(rulesKey, `/v1/ingredients/${ingredientId}/rules`);

  const [rule, setRule] = useState({
    country_code: "",
    rule_type: "PROHIBITED",
    max_concentration_pct: "",
    source_url: "",
    last_verified_on: "",
  });
  const isRestricted = rule.rule_type === "RESTRICTED";

  const addRule = useMutation({
    mutationFn: () =>
      apiFetch<IngredientRule>(`/v1/ingredients/${ingredientId}/rules`, {
        method: "POST",
        body: {
          ...rule,
          // 금지는 한도를 갖지 않는다 — 서버·DB가 양방향으로 거부한다.
          max_concentration_pct:
            rule.max_concentration_pct === "" ? undefined : rule.max_concentration_pct,
        },
      }),
    onSuccess: () => {
      setRule((previous) => ({
        ...previous,
        country_code: "",
        max_concentration_pct: "",
        source_url: "",
      }));
      void client.invalidateQueries({ queryKey: rulesKey });
    },
  });

  if (ingredient.isPending) return <p className="text-gray-500">불러오는 중…</p>;
  if (ingredient.error || !ingredient.data) {
    return (
      <div role="alert" className="text-signal-red">
        <p>{fieldMessage(ingredient.error) ?? "성분을 찾을 수 없습니다."}</p>
        <Link to="/ingredients" className="mt-2 inline-block text-sm text-gray-700 underline">
          목록으로
        </Link>
      </div>
    );
  }

  const item = ingredient.data;

  return (
    <section className="flex flex-col gap-8">
      <header>
        <Link to="/ingredients" className="text-sm text-gray-500 underline">
          ← 성분 목록
        </Link>
        <h1 className="mt-2 text-2xl font-bold">{item.inci_name}</h1>
        <p className="mt-1 text-sm text-gray-500">{orEmpty(item.name_ko)}</p>
      </header>

      <div>
        <h2 className="text-lg font-semibold">국가별 규칙</h2>
        <p className="mt-1 text-sm text-gray-500">
          시스템은 규제를 판정하지 않습니다. 확인한 금지·제한 규칙과{" "}
          <strong>근거 링크·확인일</strong>을 함께 적습니다. 같은 국가에 규칙은 한 건입니다 —
          금지와 제한이 공존할 수 없습니다.
        </p>

        {canEdit && (
          <form
            className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              addRule.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">국가(2자리)</span>
              <input
                name="country_code"
                required
                maxLength={2}
                value={rule.country_code}
                onChange={(event) =>
                  setRule((previous) => ({ ...previous, country_code: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">규칙</span>
              <select
                name="rule_type"
                value={rule.rule_type}
                onChange={(event) =>
                  // 금지로 바꾸면 한도 입력칸이 사라진다 — 값을 함께 지우지
                  // 않으면 보이지 않는 필드가 422를 만든다(리뷰 검출).
                  setRule((previous) => ({
                    ...previous,
                    rule_type: event.target.value,
                    max_concentration_pct:
                      event.target.value === "RESTRICTED" ? previous.max_concentration_pct : "",
                  }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="PROHIBITED">금지</option>
                <option value="RESTRICTED">제한(농도 한도)</option>
              </select>
            </label>
            {isRestricted && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">농도 한도(%)</span>
                <input
                  name="max_concentration_pct"
                  required
                  inputMode="decimal"
                  value={rule.max_concentration_pct}
                  onChange={(event) =>
                    setRule((previous) => ({
                      ...previous,
                      max_concentration_pct: event.target.value,
                    }))
                  }
                  className="rounded border border-gray-300 px-3 py-2"
                />
              </label>
            )}
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">근거 링크</span>
              <input
                name="source_url"
                required
                type="url"
                placeholder="https://"
                value={rule.source_url}
                onChange={(event) =>
                  setRule((previous) => ({ ...previous, source_url: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">최종확인일</span>
              <input
                name="last_verified_on"
                required
                type="date"
                value={rule.last_verified_on}
                onChange={(event) =>
                  setRule((previous) => ({ ...previous, last_verified_on: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <button
              type="submit"
              disabled={addRule.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              규칙 기록
            </button>
            {fieldMessage(addRule.error) && (
              <p role="alert" className="w-full text-sm text-signal-red">
                {fieldMessage(addRule.error)}
              </p>
            )}
          </form>
        )}

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={rules.isPending}
            error={rules.error}
            isEmpty={rules.data?.items.length === 0}
            emptyHint="기록된 규칙이 없습니다. 확인한 금지·제한 규칙과 근거 링크를 적어 주세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2 num">국가</th>
                  <th className="cell-nowrap px-4 py-2 num">규칙</th>
                  <th className="cell-nowrap px-4 py-2 num">농도 한도(%)</th>
                  <th className="px-4 py-2">근거</th>
                  <th className="cell-nowrap px-4 py-2 num">최종확인일</th>
                </tr>
              </thead>
              <tbody>
                {rules.data?.items.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2 num">{row.country_code}</td>
                    <td className="cell-nowrap px-4 py-2 num">{ruleTypeLabel(row.rule_type)}</td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {orEmpty(row.max_concentration_pct)}
                    </td>
                    <td className="px-4 py-2">
                      <a href={row.source_url} className="underline" rel="noreferrer noopener">
                        링크
                      </a>
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{row.last_verified_on}</td>
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
