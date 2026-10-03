// 품번 등록 유도 (S3-1 PR-13b) — 미매핑(UNMAPPED) 라인의 바이어 품번을 SKU에 연결한다(POST /partners/{id}/item-codes).
//
// ★ 등록만 한다 — 이 인테이크에 반영하는 것은 사람이 '품번 다시 확인'(resolve)을 눌러 한다(자동 추종 없음). 매핑 규칙·중복 검사는 서버가 판정한다.
//   미매핑 사유(매핑 없음/연결된 SKU 삭제)는 서버가 주지 않으므로 화면이 추론하지 않는다 — 같은 품번의 기존 매핑이 남아 있으면 서버가 거절하고, 안내는 그 경로(거래처 화면에서 정리)를 함께 적는다.
// 멱등 키는 본문 → 키 Map(같은 본문 재시도·더블클릭 = 같은 키, 성공하면 비움), 잠금은 mutationFn finally에서 해제, networkMode:"always".
// 403(라우트 역할 게이트)은 상위 화면 단일 래치(onForbidden)로 올린다. 화면을 떠난 뒤 도착한 응답은 상위 콜백을 부르지 않는다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import { keyFor } from "../lib/confirm";
import { newGateKey } from "../lib/gate";
import { ORDER_INTAKES_QUERY_KEY, isWriteForbidden } from "../lib/order-intake";
import type { Sku } from "../routes/skus";
import { SearchSelect } from "./search-select";

export function IntakeItemCodeForm({
  partnerId,
  lineNo,
  buyerItemCode,
  onRegistered,
  onForbidden,
}: {
  partnerId: number;
  lineNo: number;
  buyerItemCode: string;
  /** 등록 성공 — 상위가 안내 문구를 live region으로 알리고 포커스를 옮긴다. */
  onRegistered: (lineNo: number) => void;
  /** 라우트 403 — 상위 화면의 쓰기 래치. */
  onForbidden: () => void;
}) {
  const client = useQueryClient();
  const noteId = useId();
  const [sku, setSku] = useState<Sku | null>(null);
  const lock = useRef(false);
  const keys = useRef(new Map<string, string>());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const register = useMutation({
    networkMode: "always",
    mutationFn: async (input: { body: { sku_id: number; buyer_item_code: string }; key: string }) => {
      try {
        return await apiFetch<unknown>(`/v1/partners/${partnerId}/item-codes`, { method: "POST", idempotencyKey: input.key, body: input.body });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: () => {
      keys.current.clear();
      // 서버가 매핑 상태를 다시 계산한다 — 상세·게이트를 새로 읽는다(기준 version은 옮기지 않는다). 바이어 품번 목록도.
      void client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY });
      void client.invalidateQueries({ queryKey: ["partners"] });
      if (!mounted.current) return;
      setSku(null);
      onRegistered(lineNo);
    },
    onError: (error) => {
      if (mounted.current && isWriteForbidden(error)) onForbidden();
    },
  });

  function submit() {
    if (lock.current || sku === null) return;
    const body = { sku_id: sku.id, buyer_item_code: buyerItemCode.trim() };
    const key = keyFor(keys.current, JSON.stringify(body), newGateKey);
    lock.current = true;
    register.mutate({ body, key });
  }

  return (
    <div className="flex flex-col gap-2 rounded border border-gray-200 p-3 text-sm">
      <p className="break-all">
        <strong className="cell-nowrap">라인 {lineNo}</strong> 바이어 품번 <span className="font-mono">{buyerItemCode}</span>에 연결할 SKU를 선택해 등록합니다.
      </p>
      <SearchSelect<Sku>
        label={`라인 ${lineNo} 연결할 SKU`}
        path="/v1/skus"
        queryKey={["skus"]}
        value={sku}
        onChange={setSku}
        getKey={(item) => item.id}
        getLabel={(item) => `${item.sku_code} ${item.name_ko}`}
        disabled={register.isPending}
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={submit}
          disabled={sku === null || register.isPending}
          aria-describedby={sku === null && !register.isPending ? noteId : undefined}
          className="cell-nowrap rounded border border-gray-900 px-3 py-1 text-sm disabled:opacity-50"
        >
          {register.isPending ? "등록 중…" : "품번 등록"}
        </button>
        {sku === null && (
          <span id={noteId} className="sr-only">
            연결할 SKU를 먼저 선택해야 등록할 수 있습니다.
          </span>
        )}
        {register.isError && !isWriteForbidden(register.error) && (
          <span role="alert" className="break-keep text-signal-red">
            {errorMessage(register.error, "품번을 등록하지 못했습니다.")}
            {register.error instanceof ApiError && (register.error.status === 409 || register.error.status === 422)
              ? " 같은 바이어 품번의 기존 매핑(삭제된 SKU에 연결된 것 포함)이 남아 있으면 거래처 화면에서 그 매핑을 먼저 삭제해 주세요."
              : ""}
          </span>
        )}
      </div>
    </div>
  );
}
