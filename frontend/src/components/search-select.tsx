// 검색형 선택 콤보박스 (design-F F16 — 200건 드롭다운 상한 해소).
//
// ★ 선택지를 `?size=200`으로 통째로 받으면 201번째부터 조용히 사라진다.
//   이 컴포넌트는 입력이 멈춘 뒤(300ms) 서버 검색(`q`)으로 한 화면(기본 20건)만 받고,
//   total보다 적게 받았으면 그 사실을 화면에 말한다(fail-visible).
// ★ 화면이 막는 것은 편의일 뿐이다 — 선택값의 유효성은 서버가 다시 판정한다.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { ApiError, apiFetch } from "../lib/api";
import type { Page } from "../lib/paging";

export const SEARCH_DEBOUNCE_MS = 300;
export const SEARCH_PAGE_SIZE = 20;

interface SearchSelectProps<T> {
  /** 접근성 이름(라벨 문구). */
  label: string;
  /** 검색 엔드포인트 경로(쿼리스트링 제외), 예: "/v1/partners". */
  path: string;
  /** 고정 필터(예: {type: "OEM"}). q·size는 컴포넌트가 붙인다. */
  params?: Record<string, string>;
  /** react-query 키 접두 — 무효화가 부분 일치로 걸리도록 목록 키를 그대로 쓴다. */
  queryKey: readonly unknown[];
  value: T | null;
  onChange: (next: T | null) => void;
  getKey: (item: T) => string | number;
  getLabel: (item: T) => string;
  size?: number;
  placeholder?: string;
  disabled?: boolean;
}

export function SearchSelect<T>({
  label,
  path,
  params,
  queryKey,
  value,
  onChange,
  getKey,
  getLabel,
  size = SEARCH_PAGE_SIZE,
  placeholder = "검색어를 입력하세요",
  disabled = false,
}: SearchSelectProps<T>) {
  const listId = useId();
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(text.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const query = useQuery({
    queryKey: [...queryKey, "search", { path, params, q: debounced, size }],
    queryFn: () => {
      const search = new URLSearchParams({ ...params });
      if (debounced !== "") search.set("q", debounced);
      search.set("size", String(size));
      return apiFetch<Page<T>>(`${path}?${search.toString()}`);
    },
    enabled: open && value === null,
  });

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  // 입력 직후~디바운스 만료 전에는 옛 검색어의 결과라 "없음"을 단정하지 않는다.
  const settling = text.trim() !== debounced;
  const loading = query.isFetching || settling;

  function choose(item: T) {
    onChange(item);
    setOpen(false);
    setText("");
    setDebounced("");
    setActive(0);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((previous) => Math.min(previous + 1, Math.max(items.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((previous) => Math.max(previous - 1, 0));
    } else if (event.key === "Enter") {
      // 목록이 열려 있을 때만 선택으로 쓴다 — 아니면 폼 제출 동작을 막지 않는다.
      if (open && !loading && items[active] !== undefined) {
        event.preventDefault();
        choose(items[active]);
      } else if (open) {
        event.preventDefault();
      }
    } else if (event.key === "Escape") {
      if (open) event.stopPropagation();
      setOpen(false);
    }
  }

  if (value !== null) {
    return (
      <div className="flex items-center gap-2 rounded border border-gray-300 px-3 py-2 text-sm">
        <span className="break-keep">{getLabel(value)}</span>
        <button
          type="button"
          disabled={disabled}
          aria-label={`${label} 선택 해제`}
          onClick={() => onChange(null)}
          className="cell-nowrap text-gray-500 underline disabled:opacity-50"
        >
          선택 해제
        </button>
      </div>
    );
  }

  const error = query.error;

  return (
    <div className="relative">
      <input
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && items[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        disabled={disabled}
        placeholder={placeholder}
        value={text}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(event) => {
          setText(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
      />
      {open && (
        <div
          id={listId}
          // onMouseDown 선택이 blur보다 먼저 일어나도록 목록 안 클릭은 포커스를 뺏지 않는다.
          onMouseDown={(event) => event.preventDefault()}
          className="absolute z-10 mt-1 max-h-72 w-full min-w-64 overflow-auto rounded border border-gray-200 bg-white text-sm shadow"
        >
          {error ? (
            <p role="alert" className="p-3 text-signal-red">
              {error instanceof ApiError ? error.message : "검색하지 못했습니다."}
            </p>
          ) : loading && items.length === 0 ? (
            <p className="p-3 text-gray-500">불러오는 중…</p>
          ) : items.length === 0 ? (
            <p className="p-3 text-gray-500">검색 결과가 없습니다</p>
          ) : (
            <>
              <ul role="listbox" aria-label={`${label} 검색 결과`}>
                {items.map((item, index) => (
                  <li
                    key={getKey(item)}
                    id={`${listId}-${index}`}
                    role="option"
                    aria-selected={index === active}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => choose(item)}
                    className={`cursor-pointer px-3 py-2 break-keep ${
                      index === active ? "bg-gray-100" : ""
                    }`}
                  >
                    {getLabel(item)}
                  </li>
                ))}
              </ul>
              {total > items.length && (
                <p role="status" className="border-t border-gray-100 p-3 text-gray-500">
                  {total}건 중 {items.length}건만 표시 — 검색어를 더 입력하세요
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
