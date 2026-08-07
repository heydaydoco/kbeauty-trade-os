// 엑셀 왕복 임포트 — 레지스트리·업로드·스테이징 검토·확정 (DESIGN.md §12.2 / ADR-0027).
//
// ★ 왕복의 출발점은 목록 CSV다 — 내려받은 파일을 수정해 올리면 변경분 diff만
//   스테이징되고, 사람이 검토한 뒤 1클릭으로 확정한다(§15 L2 — 자동 확정 없음).
//   확정에 이중 확인 모달을 두지 않는다 — 더블클릭은 멱등 키가 흡수한다(§17.4).
// ★ 화면의 역할 게이트는 표시일 뿐이다 — 실제 차단은 서버가 한다(§18.1).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { ApiError, apiDelete, apiFetch, apiUpload } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { importRowKindLabel, importStatusLabel, orEmpty } from "../lib/labels";
import { usePagedList } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";

interface ImportRegistryEntry {
  code: string;
  label_ko: string;
  headers: string[];
}

export interface ImportStagingSummary {
  id: number;
  registry_code: string;
  registry_label: string;
  original_filename: string;
  sha256: string;
  status: string;
  total_data_rows: number;
  unchanged_rows: number;
  new_rows: number;
  changed_rows: number;
  error_rows: number;
  created_at: string;
  confirmed_at: string | null;
}

interface ImportStagingRow {
  id: number;
  row_no: number;
  kind: string;
  target_id: number | null;
  payload: Record<string, unknown>;
  /** {CSV 헤더: [이전 표기, 이후 표기]} — 변경 행에만 있다. */
  changes: Record<string, string[]> | null;
  error_reason: string | null;
}

interface ImportConfirmResult {
  id: number;
  status: string;
  created_rows: number;
  updated_rows: number;
}

export const IMPORT_REGISTRY_QUERY_KEY = ["import-registry"] as const;
export const IMPORT_STAGINGS_QUERY_KEY = ["import-stagings"] as const;

/** 왕복의 출발점 — 대상별 목록 내보내기 주소. 레지스트리 코드에 결합된다. */
const EXPORT_CSV_BY_CODE: Record<string, string> = {
  partners: "/api/v1/partners/export.csv",
  materials: "/api/v1/materials/export.csv",
  // SKU만 왕복 내보내기가 표시용 목록 CSV와 분리다(S1.5 판정 ① — ADR-0030 부기).
  skus: "/api/v1/skus/roundtrip.csv",
};

/** 분류 badge — 신호등 팔레트(theme.css §5.3 토큰) 재사용. 모르는 값은 회색. */
const KIND_BADGE: Record<string, string> = {
  NEW: "bg-signal-green/20",
  CHANGED: "bg-signal-amber/30",
  ERROR: "bg-signal-red/20",
};

/** NEW 행의 payload를 "키: 값" 한 줄 요약으로 — 빈 값은 요약에서 뺀다. */
function payloadSummary(payload: Record<string, unknown>): string {
  return Object.entries(payload)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("|") : String(value)}`)
    .join(" · ");
}

/** 서버 message 그대로 + detail의 부가 설명(충돌 행·실패 행 등) 전부 함께 (§18.4). */
function actionErrorText(error: unknown): string | null {
  if (!error) return null;
  if (!(error instanceof ApiError)) {
    return "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  }
  // detail 키를 고르지 않는다 — rows(충돌 행)든 row(실패 행)든 사용자용 문장이다.
  const parts = Object.values(error.detail).filter(
    (value): value is string => typeof value === "string" && value !== "",
  );
  return parts.length > 0 ? `${error.message} — ${parts.join(" / ")}` : error.message;
}

export function ImportsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  // 마스터를 만드는 경로이므로 등록과 같은 역할이다(서버 CAN_IMPORT와 동일).
  const canImport = hasRole(me, "TRADE");

  const [targetCode, setTargetCode] = useState("");
  const [file, setFile] = useState<File | null>(null);
  // 파일 input은 값을 코드로 비울 수 없다 — key를 바꿔 새로 그린다(documents 선례).
  const [fileInputKey, setFileInputKey] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // 레지스트리는 Page 봉투가 아니다 — 코드에 결합된 고정 목록(ADR-11)이라
  // 페이지네이션이 없고, usePagedQuery 대신 useQuery를 그대로 쓴다.
  const registry = useQuery({
    queryKey: IMPORT_REGISTRY_QUERY_KEY,
    queryFn: () => apiFetch<{ items: ImportRegistryEntry[] }>("/v1/imports/registry"),
    enabled: canImport,
  });

  const list = usePagedList<ImportStagingSummary>(IMPORT_STAGINGS_QUERY_KEY, "/v1/imports/staging");
  const selected = list.data?.items.find((staging) => staging.id === selectedId) ?? null;

  // 검토 표는 한 쪽에 200행(리뷰 밀도 유지 — PR-3 리뷰 수정 ④)이고, 초과분은
  // 쪽 이동으로 본다. 스테이징이 바뀌면 경로가 바뀌어 1쪽으로 돌아간다.
  const rows = usePagedList<ImportStagingRow>(
    [...IMPORT_STAGINGS_QUERY_KEY, selectedId, "rows"] as const,
    `/v1/imports/staging/${selectedId}/rows?size=200`,
    selectedId !== null,
  );

  const upload = useMutation({
    mutationFn: () => {
      const data = new FormData();
      if (file) data.append("file", file);
      return apiUpload<ImportStagingSummary>(`/v1/imports/${targetCode}/staging`, data);
    },
    onSuccess: (staging) => {
      setFile(null);
      setFileInputKey((previous) => previous + 1);
      // selectStaging을 거쳐야 이전 스테이징의 확정 결과·오류 잔상이 지워진다.
      selectStaging(staging.id);
      void client.invalidateQueries({ queryKey: IMPORT_STAGINGS_QUERY_KEY });
    },
  });

  // 확정 키는 선택된 스테이징마다 하나로 고정한다 — 클릭마다 새 키면 성공 직후
  // 재클릭이 "이미 PENDING이 아니다" 409로 보인다. 같은 키 재수신은 최초 결과
  // 재생이다(§17.4 — 서버 멱등이 더블클릭을 흡수하는 전제).
  const confirmKey = useMemo(() => crypto.randomUUID(), [selectedId]);

  const confirm = useMutation({
    mutationFn: (stagingId: number) =>
      apiFetch<ImportConfirmResult>(`/v1/imports/staging/${stagingId}/confirm`, {
        method: "POST",
        idempotencyKey: confirmKey,
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: IMPORT_STAGINGS_QUERY_KEY }),
  });

  const discard = useMutation({
    mutationFn: (stagingId: number) => apiDelete(`/v1/imports/staging/${stagingId}`),
    onSuccess: () => {
      setSelectedId(null);
      void client.invalidateQueries({ queryKey: IMPORT_STAGINGS_QUERY_KEY });
    },
  });

  function selectStaging(stagingId: number) {
    setSelectedId(stagingId);
    // 이전 스테이징의 반영 결과·오류가 새 선택에 남아 보이지 않게 비운다.
    confirm.reset();
    discard.reset();
  }

  const uploadMessage = fieldMessage(upload.error);
  const actionMessage = actionErrorText(confirm.error ?? discard.error);

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">엑셀 임포트</h1>
        <p className="mt-1 text-sm text-gray-500">
          목록 CSV를 내려받아 오프라인에서 수정한 뒤 그대로 올리면 변경분만 스테이징됩니다.
          검토 후 확정해야 반영됩니다 — 자동 확정은 없습니다.
        </p>
      </header>

      {canImport && (
        <form
          className="mt-6 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            upload.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">대상</span>
              <select
                name="registry_code"
                required
                value={targetCode}
                onChange={(event) => setTargetCode(event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">선택</option>
                {registry.data?.items.map((entry) => (
                  <option key={entry.code} value={entry.code}>
                    {entry.label_ko}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">CSV 파일</span>
              <input
                key={fileInputKey}
                name="file"
                type="file"
                accept=".csv"
                required
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="text-sm"
              />
            </label>
            <button
              type="submit"
              disabled={upload.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {upload.isPending ? "올리는 중…" : "업로드"}
            </button>
          </div>

          {targetCode !== "" && (
            <p className="mt-3 text-sm text-gray-500">
              <a
                href={`/api/v1/imports/${targetCode}/template.csv`}
                className="cell-nowrap underline"
              >
                표준 양식 다운로드
              </a>
              {EXPORT_CSV_BY_CODE[targetCode] && (
                <>
                  {" · "}
                  <a href={EXPORT_CSV_BY_CODE[targetCode]} className="cell-nowrap underline">
                    목록 CSV 내려받기
                  </a>
                </>
              )}{" "}
              — 왕복 편집은 내려받은 목록 CSV를 수정해 그대로 올리는 흐름입니다. ID가 빈 행은
              신규로, ID가 있는 행은 변경분만 반영됩니다.
            </p>
          )}

          {uploadMessage && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {uploadMessage}
            </p>
          )}
        </form>
      )}

      <h2 className="mt-8 text-lg font-semibold">검토 대기·이력</h2>
      <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-gray-500">
        <ListPager data={list.data} page={list.page} onPageChange={list.setPage} />
        <span>행을 누르면 아래에서 신규·변경·오류 행을 검토합니다.</span>
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canImport
              ? "올린 파일이 없습니다. 위에서 대상을 고르고 CSV 파일을 올리세요."
              : "올린 파일이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2 num">ID</th>
                <th className="cell-nowrap px-4 py-2">대상</th>
                <th className="px-4 py-2">파일명</th>
                <th className="cell-nowrap px-4 py-2 num">상태</th>
                <th className="cell-nowrap px-4 py-2 num">전체</th>
                <th className="cell-nowrap px-4 py-2 num">무변경</th>
                <th className="cell-nowrap px-4 py-2 num">신규</th>
                <th className="cell-nowrap px-4 py-2 num">변경</th>
                <th className="cell-nowrap px-4 py-2 num">오류</th>
                <th className="cell-nowrap px-4 py-2 num">등록일</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((staging) => (
                <tr
                  key={staging.id}
                  onClick={() => selectStaging(staging.id)}
                  className={`cursor-pointer border-t border-gray-100 ${
                    staging.id === selectedId ? "bg-gray-50" : ""
                  }`}
                >
                  <td className="cell-nowrap px-4 py-2 num">{staging.id}</td>
                  <td className="cell-nowrap px-4 py-2">{staging.registry_label}</td>
                  <td className="px-4 py-2">{staging.original_filename}</td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {importStatusLabel(staging.status)}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">{staging.total_data_rows}</td>
                  <td className="cell-nowrap px-4 py-2 num">{staging.unchanged_rows}</td>
                  <td className="cell-nowrap px-4 py-2 num">{staging.new_rows}</td>
                  <td className="cell-nowrap px-4 py-2 num">{staging.changed_rows}</td>
                  <td className="cell-nowrap px-4 py-2 num">{staging.error_rows}</td>
                  <td className="cell-nowrap px-4 py-2 num">{toKstDisplay(staging.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {selected && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold">
            스테이징 #{selected.id} — {selected.registry_label} · {selected.original_filename}
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            상태 {importStatusLabel(selected.status)} · 등록 {toKstDisplay(selected.created_at)}
            {selected.confirmed_at ? ` · 확정 ${toKstDisplay(selected.confirmed_at)}` : ""}
          </p>

          {canImport && selected.status === "PENDING" && (
            <div className="mt-3 flex items-center gap-3">
              {/* 확정은 클릭 1번 — 이중 확인 모달 금지. 더블클릭은 멱등 키가 흡수한다(§17.4). */}
              <button
                type="button"
                onClick={() => confirm.mutate(selected.id)}
                disabled={confirm.isPending}
                className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {confirm.isPending ? "반영 중…" : "확정(변경 반영)"}
              </button>
              <button
                type="button"
                onClick={() => discard.mutate(selected.id)}
                disabled={discard.isPending}
                className="text-sm text-gray-500 underline disabled:opacity-50"
              >
                파기
              </button>
            </div>
          )}

          {confirm.data && (
            <p className="mt-2 text-sm font-semibold text-signal-green">
              신규 {confirm.data.created_rows}건·수정 {confirm.data.updated_rows}건 반영됨
            </p>
          )}
          {actionMessage && (
            <p role="alert" className="mt-2 text-sm text-signal-red">
              {actionMessage}
            </p>
          )}

          {/* 잘림 은닉 금지(리뷰 검출)의 후신 — 이제 초과분은 쪽 이동으로 다 볼 수
              있지만, 확정 범위가 "보이는 쪽"이 아니라 전체라는 사실은 계속 말한다. */}
          {rows.data && rows.data.total > rows.data.items.length && (
            <p className="mt-2 text-sm text-gray-500">
              검토 행이 여러 쪽입니다 — 확정은 화면에 표시된 쪽만이 아니라 전체{" "}
              {rows.data.total}건에 반영됩니다.
            </p>
          )}

          <ListPager
            data={rows.data}
            page={rows.page}
            onPageChange={rows.setPage}
            className="mt-3"
          />

          <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
            <ListState
              isPending={rows.isPending}
              error={rows.error}
              isEmpty={rows.data?.items.length === 0}
              emptyHint="검토할 행이 없습니다. 파일이 현재 목록과 같습니다(전부 무변경)."
            >
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left">
                  <tr>
                    <th className="cell-nowrap px-4 py-2 num">행번호</th>
                    <th className="cell-nowrap px-4 py-2 num">분류</th>
                    <th className="cell-nowrap px-4 py-2 num">대상 ID</th>
                    <th className="px-4 py-2">내용</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.data?.items.map((row) => (
                    <tr key={row.id} className="border-t border-gray-100">
                      <td className="cell-nowrap px-4 py-2 num">{row.row_no}</td>
                      <td className="cell-nowrap px-4 py-2 num">
                        <span
                          className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${
                            KIND_BADGE[row.kind] ?? "bg-gray-100"
                          }`}
                        >
                          {importRowKindLabel(row.kind)}
                        </span>
                      </td>
                      <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.target_id)}</td>
                      <td className="px-4 py-2">
                        {row.kind === "CHANGED" && row.changes ? (
                          Object.entries(row.changes).map(([header, pair]) => (
                            <p key={header}>
                              {header}: {pair[0]} → {pair[1]}
                            </p>
                          ))
                        ) : row.kind === "ERROR" ? (
                          <p className="text-signal-red">{orEmpty(row.error_reason)}</p>
                        ) : (
                          <p>{payloadSummary(row.payload)}</p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ListState>
          </div>
        </div>
      )}
    </section>
  );
}
