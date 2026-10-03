// 오더 보드 저장 필터 (S3-1 PR-15b — 서버 `/order-board/saved-filters`, 본인 것만·사람당 20개·이름 유일).
//
// ★ 정본은 서버(DB)다. localStorage에는 '마지막으로 고른 필터 id' 같은 편의만 두며, 접근이 막혀도(throw) 화면은 정상이다.
// ★ `needs_resave=true`(형식이 바뀌어 낡은 필터)는 적용하지 않는다 — '현재 조건으로 다시 저장' 또는 삭제만.
// ★ 오류는 서버 한국어 문구 그대로: 20개 상한(LIMIT_REACHED)·이름 422(보이지 않는 글자 등)·중복 이름 409·낙관 잠금 409.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ApiError, apiDelete, apiFetch } from "../lib/api";
import {
  SAVED_FILTERS_QUERY_KEY,
  SAVED_FILTER_LIMIT,
  SAVED_FILTER_NAME_MAX,
  checkFilterName,
  normalizeFilter,
  savedFilterError,
  type BoardFilter,
  type SavedFilter,
  type SavedFilterError,
} from "../lib/order-board";
import { usePagedQuery } from "../lib/paging";
import { ConfirmDialog } from "./confirm-dialog";

export const LAST_FILTER_STORAGE_KEY = "kbos.order-board.last-saved-filter-id";

function readLastFilterId(): number | null {
  try {
    const raw = window.localStorage.getItem(LAST_FILTER_STORAGE_KEY);
    const id = raw === null ? NaN : Number(raw);
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

function writeLastFilterId(id: number | null) {
  try {
    if (id === null) window.localStorage.removeItem(LAST_FILTER_STORAGE_KEY);
    else window.localStorage.setItem(LAST_FILTER_STORAGE_KEY, String(id));
  } catch {
    // 저장소가 막힌 환경(사생활 보호 모드 등) — 편의 기능만 빠진다.
  }
}

interface SavedFiltersProps {
  /** 지금 화면에 적용된 조건 — '현재 조건 저장'이 이것을 보낸다. */
  current: BoardFilter;
  onApply: (filter: BoardFilter) => void;
}

const NO_ERROR: SavedFilterError = { name: null, general: null };

export function BoardSavedFilters({ current, onApply }: SavedFiltersProps) {
  const client = useQueryClient();
  const nameId = useId();
  const renameId = useId();
  const list = usePagedQuery<SavedFilter>(SAVED_FILTERS_QUERY_KEY, "/v1/order-board/saved-filters");
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  const [selectedId, setSelectedId] = useState<number | null>(() => readLastFilterId());
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [error, setError] = useState<SavedFilterError>(NO_ERROR);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = () => client.invalidateQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });

  const create = useMutation({
    mutationFn: (body: { name: string; filter_config: BoardFilter }) =>
      apiFetch<SavedFilter>("/v1/order-board/saved-filters", { method: "POST", body }),
    onSuccess: (saved) => {
      setName("");
      setError(NO_ERROR);
      setSelectedId(saved.id);
      writeLastFilterId(saved.id);
      setNotice(`'${saved.name}' 필터를 저장했습니다.`);
      void refresh();
    },
    onError: (caught) => setError(savedFilterError(caught)),
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { version: number; name?: string; filter_config?: BoardFilter } }) =>
      apiFetch<SavedFilter>(`/v1/order-board/saved-filters/${id}`, { method: "PATCH", body }),
    onSuccess: (saved) => {
      setRenaming(null);
      setRenameError(null);
      setError(NO_ERROR);
      setNotice(`'${saved.name}' 필터를 고쳤습니다.`);
      void refresh();
    },
    onError: (caught) => {
      const mapped = savedFilterError(caught);
      if (renaming !== null && mapped.name !== null) setRenameError(mapped.name);
      else setError(mapped);
      // 없어진 필터(404)·먼저 바뀐 필터(409 version)는 목록을 다시 받아 최신 version·존재 여부를 반영한다.
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) void refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (target: SavedFilter) => apiDelete(`/v1/order-board/saved-filters/${target.id}?version=${target.version}`),
    onSuccess: () => {
      setConfirmDelete(false);
      setSelectedId(null);
      writeLastFilterId(null);
      setError(NO_ERROR);
      setNotice("저장 필터를 삭제했습니다.");
      void refresh();
    },
    onError: (caught) => {
      setConfirmDelete(false);
      setError(savedFilterError(caught));
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) void refresh();
    },
  });

  function save() {
    setNotice(null);
    const problem = checkFilterName(name);
    if (problem !== null) {
      setError({ name: problem, general: null });
      return;
    }
    create.mutate({ name, filter_config: normalizeFilter(current) });
  }

  function apply() {
    if (selected === null || selected.filter_config === null || selected.needs_resave) return;
    setNotice(null);
    writeLastFilterId(selected.id);
    onApply(selected.filter_config);
  }

  function submitRename() {
    if (selected === null || renaming === null) return;
    const problem = checkFilterName(renaming);
    if (problem !== null) {
      setRenameError(problem);
      return;
    }
    update.mutate({ id: selected.id, body: { version: selected.version, name: renaming } });
  }

  const busy = create.isPending || update.isPending || remove.isPending;

  return (
    <section aria-label="저장 필터" className="mt-3 rounded-lg border border-gray-200 p-3 text-sm">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">
            저장 필터 <span className="num">{total}</span>/{SAVED_FILTER_LIMIT}개
          </span>
          <select
            value={selectedId ?? ""}
            onChange={(event) => {
              setSelectedId(event.target.value === "" ? null : Number(event.target.value));
              setRenaming(null);
              setRenameError(null);
              setNotice(null);
            }}
            className="min-w-48 rounded border border-gray-300 px-3 py-2"
          >
            <option value="">선택하세요</option>
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.needs_resave ? " (다시 저장 필요)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={apply}
          disabled={selected === null || selected.needs_resave || busy}
          className="cell-nowrap rounded border border-gray-300 px-3 py-2 disabled:opacity-40"
        >
          적용
        </button>
        <button
          type="button"
          onClick={() => {
            if (selected === null) return;
            setRenaming(selected.name);
            setRenameError(null);
          }}
          disabled={selected === null || busy}
          className="cell-nowrap rounded border border-gray-300 px-3 py-2 disabled:opacity-40"
        >
          이름 변경
        </button>
        <button
          type="button"
          onClick={() => {
            if (selected === null) return;
            setNotice(null);
            update.mutate({ id: selected.id, body: { version: selected.version, filter_config: normalizeFilter(current) } });
          }}
          disabled={selected === null || busy}
          className="cell-nowrap rounded border border-gray-300 px-3 py-2 disabled:opacity-40"
        >
          현재 조건으로 다시 저장
        </button>
        <button
          type="button"
          onClick={() => setConfirmDelete(true)}
          disabled={selected === null || busy}
          className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-signal-red disabled:opacity-40"
        >
          삭제
        </button>
      </div>

      {list.error ? (
        <p role="alert" className="mt-2 break-keep text-signal-red">
          저장 필터 목록을 불러오지 못했습니다. {list.error instanceof Error ? list.error.message : ""}
        </p>
      ) : null}
      {selected?.needs_resave && (
        <p role="status" className="mt-2 break-keep text-amber-800">
          '{selected.name}'은(는) 조건 형식이 바뀌어 그대로 적용할 수 없습니다. 조건을 다시 고른 뒤 '현재 조건으로 다시 저장'을 누르거나 삭제해 주세요.
        </p>
      )}

      {renaming !== null && selected !== null && (
        <div className="mt-2 flex flex-wrap items-start gap-2">
          <label htmlFor={renameId} className="cell-nowrap pt-2 text-gray-600">
            새 이름
          </label>
          <div className="flex flex-col gap-1">
            <input
              id={renameId}
              value={renaming}
              maxLength={SAVED_FILTER_NAME_MAX * 2}
              aria-invalid={renameError !== null}
              onChange={(event) => setRenaming(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
            {renameError && (
              <span role="alert" className="break-keep text-xs text-signal-red">
                {renameError}
              </span>
            )}
          </div>
          <button type="button" onClick={submitRename} disabled={busy} className="cell-nowrap rounded border border-gray-300 px-3 py-2 disabled:opacity-40">
            이름 저장
          </button>
          <button type="button" onClick={() => setRenaming(null)} className="cell-nowrap rounded px-3 py-2 text-gray-500 underline">
            취소
          </button>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-start gap-2">
        <label htmlFor={nameId} className="cell-nowrap pt-2 text-gray-600">
          새 필터 이름
        </label>
        <div className="flex flex-col gap-1">
          <input
            id={nameId}
            value={name}
            maxLength={SAVED_FILTER_NAME_MAX * 2}
            aria-invalid={error.name !== null}
            onChange={(event) => setName(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
          {error.name && (
            <span role="alert" className="break-keep text-xs text-signal-red">
              {error.name}
            </span>
          )}
        </div>
        <button type="button" onClick={save} disabled={busy} className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-white disabled:opacity-40">
          현재 조건 저장
        </button>
      </div>
      {total >= SAVED_FILTER_LIMIT && (
        <p className="mt-1 break-keep text-xs text-gray-500">저장 필터가 {SAVED_FILTER_LIMIT}개입니다. 새로 저장하려면 쓰지 않는 필터를 먼저 지워 주세요.</p>
      )}
      {error.general && (
        <p role="alert" className="mt-2 break-keep text-signal-red">
          {error.general}
        </p>
      )}
      {notice && (
        <p role="status" className="mt-2 break-keep text-gray-600">
          {notice}
        </p>
      )}

      {confirmDelete && selected !== null && (
        <ConfirmDialog
          title="저장 필터 삭제"
          description={`'${selected.name}' 필터를 삭제합니다. 보드의 주문 데이터는 바뀌지 않습니다.`}
          confirmLabel="삭제"
          danger
          pending={remove.isPending}
          onConfirm={() => remove.mutate(selected)}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </section>
  );
}
