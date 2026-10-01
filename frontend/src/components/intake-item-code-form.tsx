// 품번 등록 유도 (S3-1 PR-13b) — 미매핑(UNMAPPED) 라인의 바이어 품번을 SKU에 연결한다(POST /partners/{id}/item-codes).
//
// ★ 등록만 한다 — 이 인테이크에 반영하는 것은 사람이 '품번 다시 확인'(resolve)을 눌러 한다(자동 추종 없음). 매핑 규칙·중복 검사는 서버가 판정한다.
// 멱등 키는 본문 → 키 Map(같은 본문 재시도·더블클릭 = 같은 키, 성공하면 비움), 잠금은 mutationFn finally에서 해제, networkMode:"always".

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import { keyFor } from "../lib/confirm";
import { newGateKey } from "../lib/gate";
import { ORDER_INTAKES_QUERY_KEY } from "../lib/order-intake";
import type { Sku } from "../routes/skus";
import { SearchSelect } from "./search-select";

export function IntakeItemCodeForm({
  partnerId,
  lineNo,
  buyerItemCode,
  onRegistered,
}: {
  partnerId: number;
  lineNo: number;
  buyerItemCode: string;
  /** 등록 성공 — 상위가 안내 문구를 live region으로 알린다. */
  onRegistered: (lineNo: number) => void;
}) {
  const client = useQueryClient();
  const [sku, setSku] = useState<Sku | null>(null);
  const lock = useRef(false);
  const keys = useRef(new Map<string, string>());

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
      setSku(null);
      // 서버가 매핑 상태를 다시 계산한다 — 상세·게이트를 새로 읽는다(기준 version은 옮기지 않는다). 바이어 품번 목록도.
      void client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY });
      void client.invalidateQueries({ queryKey: ["partners"] });
      onRegistered(lineNo);
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
      <p className="break-keep">
        <strong className="cell-nowrap">라인 {lineNo}</strong> 바이어 품번 <span className="cell-nowrap font-mono">{buyerItemCode}</span>에 연결할 SKU를 선택해 등록합니다.
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
          className="cell-nowrap rounded border border-gray-900 px-3 py-1 text-sm disabled:opacity-50"
        >
          {register.isPending ? "등록 중…" : "품번 등록"}
        </button>
        {register.isError && (
          <span role="alert" className="break-keep text-signal-red">
            {errorMessage(register.error, "품번을 등록하지 못했습니다.")}
          </span>
        )}
      </div>
    </div>
  );
}
