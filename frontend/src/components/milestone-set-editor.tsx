// 품목군 '마일스톤 세트' 섹션 — 선적 계획 초안의 적용 종류 (S3-2 PR-4b — DESIGN §4.8 / design-B B16 / PROGRESS 'S3-2 PR-4c' 인계 보강).
//
// ★ 조회 = 전 역할, 추가·제거 = **관리자만**(서버 이중 가드 — 다른 역할 403, R-14). 이 API에는 allowed_actions가 없어 계약대로 관리자에게만
//   버튼을 보인다(서버가 정본 — 화면은 편의).
// ★ 선택지 = 선적 저장형 8종(파생·OEM 종류 제외) 중 아직 세트에 없는 것. 중복 409·비적용 422·없는 품목군 404는 서버 문구 그대로.
// ★ 추가 = 멱등 키(같은 본문 재시도 = 같은 키), 제거 = soft delete(재추가 = 새 행). 동기 잠금은 요청 finally에서 푼다. 글자 버튼만(아이콘 0).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { ListState } from "./list-state";
import { ApiError, apiDelete, apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import {
  DUPLICATE_TYPE_CODE,
  SET_MILESTONE_TYPES,
  milestoneTypeLabel,
  profileMilestoneTypesKey,
  type ProfileMilestoneType,
} from "../lib/milestone";
import { usePagedQuery } from "../lib/paging";
import { createKeyKeeper } from "../lib/shipment";

interface Profile {
  id: number;
  code: string;
  name_ko: string;
}

export function MilestoneSetEditor({ profile, canEdit }: { profile: Profile; canEdit: boolean }) {
  const client = useQueryClient();
  const selectId = useId();
  const key = profileMilestoneTypesKey(profile.id);
  const set = usePagedQuery<ProfileMilestoneType>(key, `/v1/item-profiles/${profile.id}/milestone-types`, true, { staleTime: 0 });
  const [type, setType] = useState("");
  const [keys, setKeys] = useState(() => createKeyKeeper());
  const addLock = useRef(false);
  const removeLock = useRef(false);

  const add = useMutation({
    mutationFn: async (input: { key: string; milestoneType: string }) => {
      try {
        return await apiFetch<ProfileMilestoneType>(`/v1/item-profiles/${profile.id}/milestone-types`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { milestone_type: input.milestoneType },
        });
      } finally {
        addLock.current = false;
      }
    },
    onSuccess: () => {
      setType("");
      setKeys(createKeyKeeper());
      void client.invalidateQueries({ queryKey: key });
    },
    onError: (error) => {
      // 다른 곳에서 먼저 추가됨(409) — 세트를 다시 받아 선택지에서 빠지게 한다(서버 문구는 그대로 보인다).
      if (error instanceof ApiError && error.code === DUPLICATE_TYPE_CODE) void client.invalidateQueries({ queryKey: key });
    },
  });

  const remove = useMutation({
    mutationFn: async (link: ProfileMilestoneType) => {
      try {
        await apiDelete(`/v1/item-profiles/${profile.id}/milestone-types/${link.id}`);
      } finally {
        removeLock.current = false;
      }
    },
    onSettled: () => void client.invalidateQueries({ queryKey: key }),
  });

  const items = set.data?.items ?? [];
  const present = new Set(items.map((item) => item.milestone_type));
  const options = SET_MILESTONE_TYPES.filter((code) => !present.has(code));
  const error = add.error ?? remove.error;

  return (
    <section aria-label={`마일스톤 세트 — ${profile.name_ko}`} className="mt-4 rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">
        마일스톤 세트 — {profile.name_ko} ({profile.code})
      </h2>
      <p className="mt-1 break-keep text-sm text-gray-500">
        이 품목군 SKU가 실린 선적의 &lsquo;계획 초안 만들기&rsquo;가 만드는 마일스톤 종류입니다. 세트가 비어 있으면 선적 계획 초안은 구분별 전체
        종류를 만듭니다. 세트 변경은 다음 초안부터 반영됩니다(이미 만든 선적의 행은 그대로).
      </p>
      {!canEdit && <p className="mt-1 break-keep text-xs text-gray-500">세트 추가·제거는 관리자가 합니다.</p>}

      <ListState
        isPending={set.isPending}
        error={set.error}
        isEmpty={items.length === 0}
        emptyHint="세트가 비어 있습니다 — 선적 계획 초안은 구분별 전체 종류를 만듭니다."
      >
        <ul className="mt-3 flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 rounded border border-gray-200 px-3 py-2 text-sm">
              <span className="cell-nowrap">{milestoneTypeLabel(item.milestone_type)}</span>
              {canEdit && (
                <button
                  type="button"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (removeLock.current) return;
                    removeLock.current = true;
                    add.reset();
                    remove.mutate(item);
                  }}
                  className="cell-nowrap rounded border border-gray-300 px-2 py-0.5 text-xs disabled:opacity-50"
                >
                  {`${milestoneTypeLabel(item.milestone_type)} 제거`}
                </button>
              )}
            </li>
          ))}
        </ul>
        {set.data !== undefined && set.data.total > items.length && (
          <p role="status" className="mt-2 text-xs text-gray-500">
            {set.data.total}건 중 {items.length}건만 표시합니다.
          </p>
        )}
      </ListState>

      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (type === "" || addLock.current) return;
            addLock.current = true;
            remove.reset();
            add.mutate({ key: keys.keyFor(type), milestoneType: type });
          }}
        >
          <div className="flex flex-col gap-1 text-sm">
            <label htmlFor={selectId} className="cell-nowrap text-gray-600">
              마일스톤 종류
            </label>
            <select
              id={selectId}
              value={type}
              disabled={add.isPending || options.length === 0}
              onChange={(event) => setType(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">{options.length === 0 ? "모든 종류가 세트에 있습니다" : "선택"}</option>
              {options.map((code) => (
                <option key={code} value={code}>
                  {milestoneTypeLabel(code)}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={add.isPending || type === ""}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {add.isPending ? "추가 중…" : "세트에 추가"}
          </button>
        </form>
      )}
      {error && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {errorMessage(error, "처리하지 못했습니다.", "마일스톤 세트")}
        </p>
      )}
    </section>
  );
}
