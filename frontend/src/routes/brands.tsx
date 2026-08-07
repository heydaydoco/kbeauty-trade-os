// 브랜드 목록·등록 (DESIGN.md §4.1 / §14 한국어 UI).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { ApiError, apiFetch } from "../lib/api";
import { orEmpty } from "../lib/labels";
import { usePagedList } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

export interface Brand {
  id: number;
  brand_code: string;
  name_ko: string;
  name_en: string | null;
  description: string | null;
}

export const BRANDS_QUERY_KEY = ["brands"] as const;

/** 서버 오류 봉투의 첫 필드 문구를 꺼낸다(§18.4 — 화면이 문구를 지어내지 않는다). */
export function fieldMessage(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const first = Object.values(error.detail)[0];
  return typeof first === "string" ? first : error.message;
}

export function BrandsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [code, setCode] = useState("");
  const [nameKo, setNameKo] = useState("");

  const list = usePagedList<Brand>(BRANDS_QUERY_KEY, "/v1/brands");

  const register = useMutation({
    mutationFn: (input: { brand_code: string; name_ko: string }) =>
      apiFetch<Brand>("/v1/brands", { method: "POST", body: input }),
    onSuccess: () => {
      setCode("");
      setNameKo("");
      void client.invalidateQueries({ queryKey: BRANDS_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">브랜드</h1>
        <a href="/api/v1/brands/export.csv" className="cell-nowrap text-sm text-gray-700 underline">
          CSV 내보내기
        </a>
      </header>

      {canRegister && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate({ brand_code: code, name_ko: nameKo });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">브랜드코드</span>
            <input
              name="brand_code"
              required
              maxLength={20}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">브랜드명(국문)</span>
            <input
              name="name_ko"
              required
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

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-6" />

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canRegister
              ? "등록된 브랜드가 없습니다. 위에서 코드와 이름을 입력해 등록하세요."
              : "등록된 브랜드가 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">브랜드코드</th>
                <th className="px-4 py-2">브랜드명(국문)</th>
                <th className="px-4 py-2">브랜드명(영문)</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((brand) => (
                <tr key={brand.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{brand.brand_code}</td>
                  <td className="px-4 py-2">{brand.name_ko}</td>
                  <td className="px-4 py-2">{orEmpty(brand.name_en)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
