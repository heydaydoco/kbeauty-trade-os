// 성분(INCI) 목록·등록 (DESIGN.md §4.3 — 자사 취급 성분만).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { orEmpty } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";

export interface Ingredient {
  id: number;
  inci_name: string;
  name_ko: string | null;
  note: string | null;
}

export const INGREDIENTS_QUERY_KEY = ["ingredients"] as const;

export function IngredientsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [inciName, setInciName] = useState("");
  const [nameKo, setNameKo] = useState("");

  const list = usePagedQuery<Ingredient>(INGREDIENTS_QUERY_KEY, "/v1/ingredients");

  const register = useMutation({
    mutationFn: (input: Record<string, unknown>) =>
      apiFetch<Ingredient>("/v1/ingredients", { method: "POST", body: input }),
    onSuccess: () => {
      setInciName("");
      setNameKo("");
      void client.invalidateQueries({ queryKey: INGREDIENTS_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">성분</h1>
          <p className="mt-1 text-sm text-gray-500">
            우리 처방에 실제로 들어가는 성분만 등록합니다. 대소문자·공백 차이는 같은 성분으로
            봅니다. 국가별 금지·제한 규칙은 성분을 눌러 안에서 적습니다.
          </p>
        </div>
        <div className="flex items-baseline gap-4">
          <a
            href="/api/v1/ingredients/export.csv"
            className="cell-nowrap text-sm text-gray-700 underline"
          >
            CSV 내보내기
          </a>
          <a
            href="/api/v1/ingredient-rules/export.csv"
            className="cell-nowrap text-sm text-gray-700 underline"
          >
            성분규칙 CSV 내보내기
          </a>
        </div>
      </header>

      {canRegister && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate({
              inci_name: inciName,
              // 국문명은 선택이다 — 신규 원료는 국문명이 늦게 정해진다.
              name_ko: nameKo === "" ? undefined : nameKo,
            });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">INCI명</span>
            <input
              name="inci_name"
              required
              maxLength={200}
              value={inciName}
              onChange={(event) => setInciName(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">표시명(국문, 선택)</span>
            <input
              name="name_ko"
              maxLength={200}
              value={nameKo}
              onChange={(event) => setNameKo(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <button
            type="submit"
            disabled={register.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {register.isPending ? "등록 중…" : "등록"}
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}

      <p className="mt-6 text-sm text-gray-500">전체 {list.data?.total ?? 0}건</p>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canRegister
              ? "등록된 성분이 없습니다. 위에서 INCI명을 입력해 등록하세요."
              : "등록된 성분이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="px-4 py-2">INCI명</th>
                <th className="px-4 py-2">표시명(국문)</th>
                <th className="px-4 py-2">비고</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((ingredient) => (
                <tr key={ingredient.id} className="border-t border-gray-100">
                  <td className="px-4 py-2">
                    <Link to={`/ingredients/${ingredient.id}`} className="underline">
                      {ingredient.inci_name}
                    </Link>
                  </td>
                  <td className="px-4 py-2">{orEmpty(ingredient.name_ko)}</td>
                  <td className="px-4 py-2">{orEmpty(ingredient.note)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
