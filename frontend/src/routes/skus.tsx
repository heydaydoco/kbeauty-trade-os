import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "../lib/api";
import { hasRole, useLogout, useSession } from "../lib/session";

interface Sku {
  id: number;
  sku_code: string;
  name_ko: string;
  name_en: string | null;
  status: string;
}

interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

const SKUS_QUERY_KEY = ["skus"] as const;

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "판매중",
  DISCONTINUED: "단종",
};

export function SkuPage() {
  const { me } = useSession();
  const logout = useLogout();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [code, setCode] = useState("");
  const [nameKo, setNameKo] = useState("");

  const list = useQuery({
    queryKey: SKUS_QUERY_KEY,
    queryFn: () => apiFetch<Page<Sku>>("/v1/skus"),
  });

  const register = useMutation({
    mutationFn: (input: { sku_code: string; name_ko: string }) =>
      // 멱등 키는 apiFetch가 자동으로 붙인다(§17.4) — 더블클릭해도 1건이다.
      apiFetch<Sku>("/v1/skus", { method: "POST", body: input }),
    onSuccess: () => {
      setCode("");
      setNameKo("");
      void client.invalidateQueries({ queryKey: SKUS_QUERY_KEY });
    },
  });

  const registerMessage =
    register.error instanceof ApiError
      ? (Object.values(register.error.detail)[0] as string | undefined) ??
        register.error.message
      : null;

  return (
    <main className="mx-auto max-w-3xl p-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">SKU 목록</h1>
          <p className="mt-1 text-sm text-gray-500">
            {me?.display_name} ({me?.roles.join(", ") || "역할 없음"})
          </p>
        </div>
        <button
          type="button"
          onClick={() => logout.mutate()}
          className="text-sm text-gray-500 underline"
        >
          로그아웃
        </button>
      </header>

      {canRegister && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate({ sku_code: code, name_ko: nameKo });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">품번</span>
            <input
              name="sku_code"
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">품명(국문)</span>
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
          {registerMessage && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {registerMessage}
            </p>
          )}
        </form>
      )}

      <div className="mt-6 flex items-center justify-between">
        <p className="text-sm text-gray-500">
          전체 {list.data?.total ?? 0}건
          {list.data ? ` (${list.data.page}쪽 · ${list.data.size}건씩)` : ""}
        </p>
        {/* CSV는 파일 다운로드라 fetch가 아니라 링크로 받는다(브라우저가 저장 대화상자를 연다). */}
        <a href="/api/v1/skus/export.csv" className="text-sm text-gray-700 underline">
          CSV 내보내기
        </a>
      </div>

      <section className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        {list.isPending && <p className="p-5 text-gray-500">불러오는 중…</p>}
        {list.isError && (
          <p className="p-5 text-signal-red">{(list.error as Error).message}</p>
        )}
        {list.data && list.data.items.length === 0 && (
          <p className="p-5 text-gray-500">
            등록된 SKU가 없습니다. {canRegister ? "위에서 품번과 품명을 입력해 등록하세요." : ""}
          </p>
        )}
        {list.data && list.data.items.length > 0 && (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">품번</th>
                <th className="px-4 py-2">품명(국문)</th>
                <th className="px-4 py-2">품명(영문)</th>
                <th className="cell-nowrap px-4 py-2 num">상태</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((sku) => (
                <tr key={sku.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{sku.sku_code}</td>
                  <td className="px-4 py-2">{sku.name_ko}</td>
                  <td className="px-4 py-2">{sku.name_en ?? "—"}</td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {STATUS_LABEL[sku.status] ?? sku.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
