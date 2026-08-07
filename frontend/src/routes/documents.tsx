// 문서보관소 — 목록·업로드·링크 등록·다운로드·삭제 (DESIGN.md §4.7 / ADR-0028·0029).
//
// ★ 다운로드는 권한 검증 엔드포인트 하나뿐이다(조건 4-②) — 화면은 그 주소로
//   anchor를 걸 뿐, 파일 경로를 알지 못한다. 서버가 attachment로 고정하므로(4-③)
//   브라우저 안에서 열리지 않고 저장된다.
// ★ 라벨 소유 문서는 SKU 상세의 라벨 행에서 올린다 — 여기 업로드 폼의 소유자는
//   SKU다(라벨을 식별자로 고르게 하면 실수가 는다).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiUpload, apiFetch } from "../lib/api";
import { documentOwnerTypeLabel, orEmpty, storageKindLabel } from "../lib/labels";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { Sku } from "./skus";

export interface DocumentTypeRow {
  id: number;
  code: string;
  name_ko: string;
  retention_years: number | null;
  note: string | null;
}

export interface DocumentRow {
  id: number;
  owner_type: string;
  owner_id: number;
  owner_display: string | null;
  document_type: string;
  document_type_name: string;
  storage_kind: string;
  original_filename: string | null;
  content_type: string | null;
  size_bytes: number | null;
  sha256: string | null;
  url: string | null;
  issued_on: string | null;
  valid_until: string | null;
  retention_until: string | null;
  note: string | null;
}

export const DOCUMENT_TYPES_QUERY_KEY = ["document-types"] as const;

const BLANK_FORM = {
  mode: "FILE",
  owner_id: "",
  document_type: "",
  url: "",
  issued_on: "",
  valid_until: "",
  note: "",
};

export function DocumentsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  // 문서 등록·삭제는 무역+인증이 한다(CERT 역할 시드 문면 "…문서").
  const canManage = hasRole(me, "TRADE") || hasRole(me, "CERT");

  const [typeFilter, setTypeFilter] = useState("");
  const [form, setForm] = useState({ ...BLANK_FORM });
  const [file, setFile] = useState<File | null>(null);
  // 파일 input은 값을 코드로 비울 수 없다 — key를 바꿔 새로 그린다.
  const [fileInputKey, setFileInputKey] = useState(0);

  const listKey = ["documents", typeFilter] as const;
  const listPath =
    typeFilter === "" ? "/v1/documents" : `/v1/documents?document_type=${typeFilter}`;
  const list = usePagedList<DocumentRow>(listKey, listPath);
  const types = usePagedQuery<DocumentTypeRow>(
    DOCUMENT_TYPES_QUERY_KEY,
    "/v1/document-types?size=200",
  );
  // 업로드 폼의 소유자 선택용 — 드롭다운은 통화 선례대로 size=200 우회.
  const skus = usePagedQuery<Sku>(["documents", "sku-options"], "/v1/skus?size=200", canManage);

  const register = useMutation({
    mutationFn: () => {
      const shared = {
        owner_type: "SKU",
        owner_id: form.owner_id,
        document_type: form.document_type,
        issued_on: form.issued_on === "" ? undefined : form.issued_on,
        valid_until: form.valid_until === "" ? undefined : form.valid_until,
        note: form.note === "" ? undefined : form.note,
      };
      if (form.mode === "LINK") {
        return apiFetch<DocumentRow>("/v1/documents/links", {
          method: "POST",
          body: { ...shared, url: form.url },
        });
      }
      const data = new FormData();
      if (file) data.append("file", file);
      for (const [key, value] of Object.entries(shared)) {
        if (value !== undefined) data.append(key, String(value));
      }
      return apiUpload<DocumentRow>("/v1/documents/files", data);
    },
    onSuccess: () => {
      setForm((previous) => ({ ...BLANK_FORM, mode: previous.mode }));
      setFile(null);
      setFileInputKey((previous) => previous + 1);
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const remove = useMutation({
    mutationFn: (documentId: number) => apiDelete(`/v1/documents/${documentId}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["documents"] }),
  });

  const message = fieldMessage(register.error);
  const removeMessage = fieldMessage(remove.error);

  const input = (key: "owner_id" | "url" | "issued_on" | "valid_until" | "note") => ({
    value: form[key],
    onChange: (
      event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
    ) => setForm((previous) => ({ ...previous, [key]: event.target.value })),
    className: "rounded border border-gray-300 px-3 py-2",
  });

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">문서보관소</h1>
        <a
          href="/api/v1/documents/export.csv"
          className="cell-nowrap text-sm text-gray-700 underline"
        >
          CSV 내보내기
        </a>
      </header>
      <p className="mt-1 text-sm text-gray-500">
        CFS·GMP·CoA·MSDS·인증서 등 서류를 한 곳에서 보관합니다. 원산지 증빙은 발급일 기준
        보존기한(기본 5년)이 걸리고, 보존기한이 지나기 전에는 삭제할 수 없습니다(파기 잠금).
      </p>

      {canManage && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">형태</span>
            <select
              name="mode"
              value={form.mode}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, mode: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="FILE">파일 업로드</option>
              <option value="LINK">외부 링크</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">대상 SKU</span>
            <select name="owner_id" required {...input("owner_id")}>
              <option value="">선택</option>
              {skus.data?.items.map((sku) => (
                <option key={sku.id} value={sku.id}>
                  {sku.sku_code} — {sku.name_ko}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">종류</span>
            <select
              name="document_type"
              required
              value={form.document_type}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, document_type: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">선택</option>
              {types.data?.items.map((type) => (
                <option key={type.id} value={type.code}>
                  {type.name_ko}
                </option>
              ))}
            </select>
          </label>
          {form.mode === "FILE" ? (
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">파일 (최대 20MB)</span>
              <input
                key={fileInputKey}
                name="file"
                type="file"
                required
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="text-sm"
              />
            </label>
          ) : (
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">링크(URL)</span>
              <input name="url" required maxLength={500} {...input("url")} />
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">발급일 (선택)</span>
            <input name="issued_on" type="date" {...input("issued_on")} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">유효기간 만료일 (선택)</span>
            <input name="valid_until" type="date" {...input("valid_until")} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">비고 (선택)</span>
            <input name="note" {...input("note")} />
          </label>
          <button
            type="submit"
            disabled={register.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {register.isPending ? "등록 중…" : "문서 등록"}
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}

      <div className="mt-6 flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <span className="cell-nowrap text-gray-600">종류 필터</span>
          <select
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {types.data?.items.map((type) => (
              <option key={type.id} value={type.code}>
                {type.name_ko}
              </option>
            ))}
          </select>
        </label>
        <ListPager data={list.data} page={list.page} onPageChange={list.setPage} />
      </div>

      {removeMessage && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          {removeMessage}
        </p>
      )}

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canManage
              ? "보관된 문서가 없습니다. 위에서 파일을 올리거나 링크를 등록하세요."
              : "보관된 문서가 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">종류</th>
                <th className="cell-nowrap px-4 py-2">소유</th>
                <th className="cell-nowrap px-4 py-2 num">형태</th>
                <th className="px-4 py-2">파일·링크</th>
                <th className="cell-nowrap px-4 py-2 num">발급일</th>
                <th className="cell-nowrap px-4 py-2 num">유효기간</th>
                <th className="cell-nowrap px-4 py-2 num">보존기한</th>
                <th className="px-4 py-2">비고</th>
                {canManage && <th className="px-4 py-2" />}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{row.document_type_name}</td>
                  <td className="cell-nowrap px-4 py-2">
                    {documentOwnerTypeLabel(row.owner_type)} · {orEmpty(row.owner_display)}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {storageKindLabel(row.storage_kind)}
                  </td>
                  <td className="px-4 py-2">
                    {row.storage_kind === "FILE" ? (
                      <a
                        href={`/api/v1/documents/${row.id}/download`}
                        className="underline"
                      >
                        {orEmpty(row.original_filename)}
                      </a>
                    ) : row.url ? (
                      <a href={row.url} className="underline" rel="noreferrer noopener">
                        링크 열기
                      </a>
                    ) : (
                      orEmpty(null)
                    )}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.issued_on)}</td>
                  <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.valid_until)}</td>
                  <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.retention_until)}</td>
                  <td className="px-4 py-2">{orEmpty(row.note)}</td>
                  {canManage && (
                    <td className="cell-nowrap px-4 py-2">
                      <button
                        type="button"
                        onClick={() => remove.mutate(row.id)}
                        disabled={remove.isPending}
                        className="text-sm text-gray-500 underline disabled:opacity-50"
                      >
                        삭제
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
