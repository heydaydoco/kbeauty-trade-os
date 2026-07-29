// 제품(처방) 목록·등록 (DESIGN.md §4.1 — 인증·원산지 판정의 단위).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { orEmpty, statusLabel } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { BRANDS_QUERY_KEY, fieldMessage, type Brand } from "./brands";

export interface Product {
  id: number;
  product_code: string;
  name_ko: string;
  name_en: string | null;
  description: string | null;
  status: string;
  brand_id: number;
  brand_code: string;
  brand_name_ko: string;
}

export const PRODUCTS_QUERY_KEY = ["products"] as const;

export function ProductsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [code, setCode] = useState("");
  const [nameKo, setNameKo] = useState("");
  const [brandId, setBrandId] = useState("");

  const list = usePagedQuery<Product>(PRODUCTS_QUERY_KEY, "/v1/products");
  // 브랜드가 없으면 제품을 만들 수 없다 — 그 사실을 폼에서 바로 알려 준다.
  const brands = usePagedQuery<Brand>(BRANDS_QUERY_KEY, "/v1/brands", canRegister);

  const register = useMutation({
    mutationFn: (input: { brand_id: number; product_code: string; name_ko: string }) =>
      apiFetch<Product>("/v1/products", { method: "POST", body: input }),
    onSuccess: () => {
      setCode("");
      setNameKo("");
      void client.invalidateQueries({ queryKey: PRODUCTS_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);
  const noBrands = brands.data?.items.length === 0;

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">제품(처방)</h1>
          <p className="mt-1 text-sm text-gray-500">
            인증·원산지 판정은 처방 단위입니다. 용량이 다른 SKU도 처방이 같으면 한 건입니다.
          </p>
        </div>
        <a
          href="/api/v1/products/export.csv"
          className="cell-nowrap text-sm text-gray-700 underline"
        >
          CSV 내보내기
        </a>
      </header>

      {canRegister && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate({
              brand_id: Number(brandId),
              product_code: code,
              name_ko: nameKo,
            });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">브랜드</span>
            <select
              name="brand_id"
              required
              value={brandId}
              onChange={(event) => setBrandId(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">선택하세요</option>
              {brands.data?.items.map((brand) => (
                <option key={brand.id} value={brand.id}>
                  {brand.name_ko} ({brand.brand_code})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">제품코드</span>
            <input
              name="product_code"
              required
              maxLength={40}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">제품명(국문)</span>
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
          {noBrands && (
            <p className="w-full text-sm text-gray-500">
              먼저 브랜드를 등록하세요. 제품은 브랜드에 속합니다.
            </p>
          )}
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
              ? "등록된 제품이 없습니다. 위에서 브랜드를 고르고 코드·이름을 입력해 등록하세요."
              : "등록된 제품이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">제품코드</th>
                <th className="px-4 py-2">제품명(국문)</th>
                <th className="px-4 py-2">제품명(영문)</th>
                <th className="cell-nowrap px-4 py-2">브랜드</th>
                <th className="cell-nowrap px-4 py-2 num">상태</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((product) => (
                <tr key={product.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{product.product_code}</td>
                  <td className="px-4 py-2">{product.name_ko}</td>
                  <td className="px-4 py-2">{orEmpty(product.name_en)}</td>
                  <td className="cell-nowrap px-4 py-2">{product.brand_name_ko}</td>
                  <td className="cell-nowrap px-4 py-2 num">{statusLabel(product.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
