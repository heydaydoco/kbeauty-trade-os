// 검색형 선택 콤보박스 (design-F F16 — 200건 드롭다운 상한 해소).
//
// ★ 선택지를 `?size=200`으로 통째로 받으면 201번째부터 조용히 사라진다.
//   이 컴포넌트는 입력이 멈춘 뒤(300ms) 서버 검색(`q`)으로 한 화면(기본 20건)만 받고,
//   total보다 적게 받았으면 그 사실을 화면에 말한다(fail-visible).
// ★ 화면이 막는 것은 편의일 뿐이다 — 선택값의 유효성은 서버가 다시 판정한다.
// ★ 오선택 차단(S3-1 PR-16 부채 ⑤ → S3-2 PR-3b): 입력 직후~디바운스 만료 전에는 화면의 목록이 **옛 검색어의 결과**다.
//   예전에는 그 목록을 그대로 그려 빠른 클릭이 다른 항목(예: 검색 전 첫 바이어)을 골랐다(PR-16 워크스루 자동화 실측).
//   지금은 결과가 **현재 입력어에 대한 응답일 때만** 목록을 그리고 선택을 받는다 — 대기·요청 중에는 "검색 중…"만 보이고,
//   클릭·Enter 처리 시점에도 최신 입력어(ref)와 결과의 검색어를 다시 대조한다(렌더 사이 경합 방어).

import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
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
  // 클릭·Enter가 처리되는 순간의 최신 입력어 — 렌더된 목록이 그 입력어의 결과인지 다시 대조한다.
  const latestText = useRef("");

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

  // 입력 직후~디바운스 만료 전에는 쿼리 키가 옛 검색어라 data도 옛 검색어의 결과다 — 보이지도, 고를 수도 없게 한다.
  const settling = text.trim() !== debounced;
  // 이 렌더의 결과가 속한 검색어(= 쿼리 키의 q). 현재 입력어와 같을 때만 '신선한' 결과다.
  const resultQuery = debounced;
  const fresh = !settling && query.data !== undefined;
  const items = fresh ? (query.data?.items ?? []) : [];
  const total = fresh ? (query.data?.total ?? 0) : 0;
  const searching = !fresh && !query.error;

  function choose(item: T) {
    // 렌더와 이벤트 사이에 입력이 바뀌었다면(경합) 이 목록은 옛 결과다 — 고르지 않는다.
    if (latestText.current.trim() !== resultQuery) return;
    onChange(item);
    setOpen(false);
    setText("");
    setDebounced("");
    latestText.current = "";
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
      if (open && fresh && items[active] !== undefined) {
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
          latestText.current = event.target.value;
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
          ) : searching ? (
            // 옛 결과를 숨긴다 — 고를 수 있는 항목이 화면에 없어야 빠른 클릭이 엉뚱한 항목을 고르지 못한다.
            <p role="status" className="p-3 text-gray-500">
              검색 중…
            </p>
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
