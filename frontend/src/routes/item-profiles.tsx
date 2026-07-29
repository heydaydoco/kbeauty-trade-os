// 품목군 목록·등록 (DESIGN.md §4.8 / ADR-0021).
//
// ★ 지금은 **분류**일 뿐이다. §4.8의 요건·서류·마일스톤 세트는 대상 테이블이
//   생기는 세션이 붙인다(요건 S2-1 / 서류 S1-3 / 마일스톤 S3-2). 화면에도 그
//   사실을 적어 둔다 — 안 그러면 "설정했는데 아무 일도 안 일어난다"가 된다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { orEmpty } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";

export interface ItemProfile {
  id: number;
  code: string;
  name_ko: string;
  description: string | null;
}

export const ITEM_PROFILES_QUERY_KEY = ["item-profiles"] as const;

export function ItemProfilesPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [code, setCode] = useState("");
  const [nameKo, setNameKo] = useState("");

  const list = usePagedQuery<ItemProfile>(ITEM_PROFILES_QUERY_KEY, "/v1/item-profiles");

  const register = useMutation({
    mutationFn: (input: { code: string; name_ko: string }) =>
      apiFetch<ItemProfile>("/v1/item-profiles", { method: "POST", body: input }),
    onSuccess: () => {
      setCode("");
      setNameKo("");
      void client.invalidateQueries({ queryKey: ITEM_PROFILES_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">품목군</h1>
        <p className="mt-1 text-sm text-gray-500">
          제품·SKU를 묶는 분류입니다. 품목군에 딸린 기본 요건·서류·마일스톤 세트는 각 기능이
          생기는 단계에서 연결됩니다 — 지금은 분류만 합니다.
        </p>
      </header>

      {canRegister && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate({ code, name_ko: nameKo });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">품목군코드</span>
            <input
              name="code"
              required
              maxLength={40}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">품목군명</span>
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

      <p className="mt-6 text-sm text-gray-500">전체 {list.data?.total ?? 0}건</p>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canRegister
              ? "등록된 품목군이 없습니다. 위에서 코드와 이름을 입력해 등록하세요."
              : "등록된 품목군이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">품목군코드</th>
                <th className="px-4 py-2">품목군명</th>
                <th className="px-4 py-2">설명</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((profile) => (
                <tr key={profile.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{profile.code}</td>
                  <td className="px-4 py-2">{profile.name_ko}</td>
                  <td className="px-4 py-2">{orEmpty(profile.description)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
