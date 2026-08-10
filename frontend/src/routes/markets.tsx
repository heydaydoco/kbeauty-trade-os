// 시장 목록·등록·정식화 (DESIGN.md §5.1 / S2-1 판정 §0-1).
//
// ★ 편집(등록·정식화)은 인증+관리자다(판정 ⑤ — 무역·물류·조회는 열람).
//   화면 게이트는 표시일 뿐 실제 차단은 서버가 한다(§18.1).
// ★ 정식화가 이 화면의 존재 이유다 — FK 승격 백필이 만든 MIG 계보 행
//   (이름=코드 그대로)의 이름을 사람이 채운다. 코드는 불변(FK 참조 값).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { orEmpty } from "../lib/labels";
import { usePagedList } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";

export interface Market {
  id: number;
  code: string;
  name_ko: string;
  name_en: string | null;
  note: string | null;
  version: number;
}

export const MARKETS_QUERY_KEY = ["markets"] as const;

/** 드롭다운 소비용 — ?size=200 우회(50건 표시 한계 관찰 항목·계획서 §3-4). */
export const MARKETS_SELECT_PATH = "/v1/markets?size=200";

interface EditState {
  id: number;
  version: number;
  name_ko: string;
  name_en: string;
  note: string;
}

export function MarketsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "CERT");

  const [form, setForm] = useState({ code: "", name_ko: "", name_en: "" });
  const [edit, setEdit] = useState<EditState | null>(null);

  const list = usePagedList<Market>(MARKETS_QUERY_KEY, "/v1/markets");

  const register = useMutation({
    mutationFn: () =>
      apiFetch<Market>("/v1/markets", {
        method: "POST",
        body: {
          code: form.code,
          name_ko: form.name_ko,
          name_en: form.name_en === "" ? undefined : form.name_en,
        },
      }),
    onSuccess: () => {
      setForm({ code: "", name_ko: "", name_en: "" });
      void client.invalidateQueries({ queryKey: MARKETS_QUERY_KEY });
    },
  });

  const update = useMutation({
    mutationFn: (input: EditState) =>
      apiFetch<Market>(`/v1/markets/${input.id}`, {
        method: "PATCH",
        body: {
          version: input.version,
          name_ko: input.name_ko,
          name_en: input.name_en === "" ? undefined : input.name_en,
          note: input.note === "" ? undefined : input.note,
        },
      }),
    onSuccess: () => {
      setEdit(null);
      void client.invalidateQueries({ queryKey: MARKETS_QUERY_KEY });
    },
  });

  const registerMessage = fieldMessage(register.error);
  const updateMessage = fieldMessage(update.error);

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">시장</h1>
          <p className="mt-1 text-sm text-gray-500">
            요건 템플릿·라벨·성분 규칙·HS 세번이 참조하는 시장(국가) 대장입니다. 라벨·규칙·세번을
            등록하려면 해당 시장이 먼저 여기 있어야 합니다. 승격으로 자동 생성된 행(메모에 MIG
            표식)은 이름을 정식화해 주세요.
          </p>
        </div>
        <a href="/api/v1/markets/export.csv" className="cell-nowrap text-sm text-gray-700 underline">
          CSV 내보내기
        </a>
      </header>

      {canEdit && (
        <form
          className="mt-6 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">시장코드 (대문자 2자)</span>
              <input
                name="code"
                required
                minLength={2}
                maxLength={2}
                value={form.code}
                onChange={(event) =>
                  setForm((previous) => ({ ...previous, code: event.target.value }))
                }
                className="w-24 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">시장명</span>
              <input
                name="name_ko"
                required
                maxLength={100}
                value={form.name_ko}
                onChange={(event) =>
                  setForm((previous) => ({ ...previous, name_ko: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">영문명 (선택)</span>
              <input
                name="name_en"
                maxLength={100}
                value={form.name_en}
                onChange={(event) =>
                  setForm((previous) => ({ ...previous, name_en: event.target.value }))
                }
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
          </div>
          {registerMessage && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {registerMessage}
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
            canEdit
              ? "등록된 시장이 없습니다. 위에서 코드(예: US)와 이름을 입력해 등록하세요."
              : "등록된 시장이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">시장코드</th>
                <th className="px-4 py-2">시장명</th>
                <th className="px-4 py-2">영문명</th>
                <th className="px-4 py-2">메모</th>
                {canEdit && <th className="cell-nowrap px-4 py-2">정식화</th>}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((market) =>
                edit?.id === market.id ? (
                  <tr key={market.id} className="border-t border-gray-100 bg-gray-50">
                    <td className="cell-nowrap px-4 py-2">{market.code}</td>
                    <td className="px-4 py-2">
                      <input
                        aria-label="시장명 수정"
                        required
                        maxLength={100}
                        value={edit.name_ko}
                        onChange={(event) =>
                          setEdit((previous) =>
                            previous ? { ...previous, name_ko: event.target.value } : previous,
                          )
                        }
                        className="w-full rounded border border-gray-300 px-2 py-1"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <input
                        aria-label="영문명 수정"
                        maxLength={100}
                        value={edit.name_en}
                        onChange={(event) =>
                          setEdit((previous) =>
                            previous ? { ...previous, name_en: event.target.value } : previous,
                          )
                        }
                        className="w-full rounded border border-gray-300 px-2 py-1"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <input
                        aria-label="메모 수정"
                        value={edit.note}
                        onChange={(event) =>
                          setEdit((previous) =>
                            previous ? { ...previous, note: event.target.value } : previous,
                          )
                        }
                        className="w-full rounded border border-gray-300 px-2 py-1"
                      />
                    </td>
                    <td className="cell-nowrap px-4 py-2">
                      <button
                        type="button"
                        disabled={update.isPending || edit.name_ko.trim() === ""}
                        onClick={() => update.mutate(edit)}
                        className="rounded bg-gray-900 px-2 py-1 text-xs text-white disabled:opacity-50"
                      >
                        저장
                      </button>
                      <button
                        type="button"
                        onClick={() => setEdit(null)}
                        className="ml-2 rounded border border-gray-300 px-2 py-1 text-xs"
                      >
                        취소
                      </button>
                    </td>
                  </tr>
                ) : (
                  <tr key={market.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2">{market.code}</td>
                    <td className="px-4 py-2">{market.name_ko}</td>
                    <td className="px-4 py-2">{orEmpty(market.name_en)}</td>
                    <td className="px-4 py-2">{orEmpty(market.note)}</td>
                    {canEdit && (
                      <td className="cell-nowrap px-4 py-2">
                        <button
                          type="button"
                          onClick={() =>
                            setEdit({
                              id: market.id,
                              version: market.version,
                              name_ko: market.name_ko,
                              name_en: market.name_en ?? "",
                              note: market.note ?? "",
                            })
                          }
                          className="rounded border border-gray-300 px-2 py-1 text-xs"
                        >
                          수정
                        </button>
                      </td>
                    )}
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </ListState>
      </div>

      {updateMessage && (
        <p role="alert" className="mt-3 text-sm text-signal-red">
          {updateMessage}
        </p>
      )}
    </section>
  );
}
