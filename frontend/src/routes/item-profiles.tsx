// 품목군 목록·등록 + 서류 세트·요건 세트 편집 (DESIGN.md §4.8 / ADR-0021·0035).
//
// ★ S1-3이 서류 세트(item_profile_document_types)를, S2-1이 요건 세트
//   (item_profile_requirement_templates)를 연결했다 — §4.8의 세 세트 중 둘.
//   마일스톤 세트는 S3-2가 붙인다. "신규 등록 시 자동 적용"은 적용 결과물
//   (인증 인스턴스)이 생기는 다음 단계의 일이다.
// ★ 요건 세트 편집은 인증+관리자다(판정 ⑤ — 서류 세트의 무역+인증과 다르다).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiFetch } from "../lib/api";
import { orEmpty, templateStatusLabel } from "../lib/labels";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { DocumentTypeRow } from "./documents";
import { DOCUMENT_TYPES_QUERY_KEY } from "./documents";
import type { RequirementTemplate } from "./requirement-templates";
import {
  REQUIREMENT_TEMPLATES_QUERY_KEY,
  REQUIREMENT_TEMPLATES_SELECT_PATH,
} from "./requirement-templates";

export interface ItemProfile {
  id: number;
  code: string;
  name_ko: string;
  description: string | null;
}

interface ProfileDocumentType {
  id: number;
  item_profile_id: number;
  document_type_id: number;
  document_type: string;
  document_type_name: string;
}

interface ProfileRequirementTemplate {
  id: number;
  item_profile_id: number;
  requirement_template_id: number;
  template_name: string;
  template_status: string;
  market_code: string;
  version: number;
}

export const ITEM_PROFILES_QUERY_KEY = ["item-profiles"] as const;

/** 한 품목군의 요건 세트 편집 패널 (§4.8 — ADR-0035, S2-1). */
function RequirementSetEditor({ profile, canEdit }: { profile: ItemProfile; canEdit: boolean }) {
  const client = useQueryClient();
  const [templateId, setTemplateId] = useState("");

  const setKey = ["item-profiles", profile.id, "requirement-templates"] as const;
  const requirementSet = usePagedQuery<ProfileRequirementTemplate>(
    setKey,
    `/v1/item-profiles/${profile.id}/requirement-templates`,
  );
  const templates = usePagedQuery<RequirementTemplate>(
    REQUIREMENT_TEMPLATES_QUERY_KEY,
    REQUIREMENT_TEMPLATES_SELECT_PATH,
    canEdit,
  );

  const add = useMutation({
    mutationFn: () =>
      apiFetch<ProfileRequirementTemplate>(
        `/v1/item-profiles/${profile.id}/requirement-templates`,
        { method: "POST", body: { requirement_template_id: Number(templateId) } },
      ),
    onSuccess: () => {
      setTemplateId("");
      void client.invalidateQueries({ queryKey: setKey });
    },
  });

  const remove = useMutation({
    mutationFn: (linkId: number) =>
      apiDelete(`/v1/item-profiles/${profile.id}/requirement-templates/${linkId}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: setKey }),
  });

  const message = fieldMessage(add.error) ?? fieldMessage(remove.error);

  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">
        요건 세트 — {profile.name_ko} ({profile.code})
      </h2>
      <p className="mt-1 text-sm text-gray-500">
        이 품목군에 기본으로 따라붙는 요건 템플릿의 목록입니다. 신규 등록 시 자동 적용은 인증
        진행 기능이 생기는 단계에서 이어집니다.
      </p>

      <ListState
        isPending={requirementSet.isPending}
        error={requirementSet.error}
        isEmpty={requirementSet.data?.items.length === 0}
        emptyHint={
          canEdit ? "세트가 비어 있습니다. 아래에서 템플릿을 추가하세요." : "세트가 비어 있습니다."
        }
      >
        <ul className="mt-3 flex flex-wrap gap-2">
          {requirementSet.data?.items.map((entry) => (
            <li
              key={entry.id}
              className="flex items-center gap-2 rounded-full border border-gray-300 px-3 py-1 text-sm"
            >
              <span className="cell-nowrap">
                [{entry.market_code}] {entry.template_name} (
                {templateStatusLabel(entry.template_status)})
              </span>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => remove.mutate(entry.id)}
                  disabled={remove.isPending}
                  aria-label={`${entry.template_name} 제거`}
                  className="text-gray-500 disabled:opacity-50"
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      </ListState>

      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">요건 템플릿</span>
            <select
              name="requirement_template_id"
              required
              value={templateId}
              onChange={(event) => setTemplateId(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">선택</option>
              {templates.data?.items.map((template) => (
                <option key={template.id} value={template.id}>
                  [{template.market_code}] {template.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            세트에 추가
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

/** 한 품목군의 서류 세트 편집 패널 (§4.8). */
function DocumentSetEditor({ profile, canEdit }: { profile: ItemProfile; canEdit: boolean }) {
  const client = useQueryClient();
  const [typeCode, setTypeCode] = useState("");

  const setKey = ["item-profiles", profile.id, "document-types"] as const;
  const documentSet = usePagedQuery<ProfileDocumentType>(
    setKey,
    `/v1/item-profiles/${profile.id}/document-types`,
  );
  const types = usePagedQuery<DocumentTypeRow>(
    DOCUMENT_TYPES_QUERY_KEY,
    "/v1/document-types?size=200",
  );

  const add = useMutation({
    mutationFn: () =>
      apiFetch<ProfileDocumentType>(`/v1/item-profiles/${profile.id}/document-types`, {
        method: "POST",
        body: { document_type: typeCode },
      }),
    onSuccess: () => {
      setTypeCode("");
      void client.invalidateQueries({ queryKey: setKey });
    },
  });

  const remove = useMutation({
    mutationFn: (linkId: number) =>
      apiDelete(`/v1/item-profiles/${profile.id}/document-types/${linkId}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: setKey }),
  });

  const message = fieldMessage(add.error) ?? fieldMessage(remove.error);

  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">
        서류 세트 — {profile.name_ko} ({profile.code})
      </h2>
      <p className="mt-1 text-sm text-gray-500">
        이 품목군에 기본으로 필요한 문서 종류의 목록입니다. 신규 등록 시 자동 적용은 요건·태스크
        기능이 생기는 단계에서 이어집니다.
      </p>

      <ListState
        isPending={documentSet.isPending}
        error={documentSet.error}
        isEmpty={documentSet.data?.items.length === 0}
        emptyHint={
          canEdit ? "세트가 비어 있습니다. 아래에서 종류를 추가하세요." : "세트가 비어 있습니다."
        }
      >
        <ul className="mt-3 flex flex-wrap gap-2">
          {documentSet.data?.items.map((entry) => (
            <li
              key={entry.id}
              className="flex items-center gap-2 rounded-full border border-gray-300 px-3 py-1 text-sm"
            >
              <span className="cell-nowrap">{entry.document_type_name}</span>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => remove.mutate(entry.id)}
                  disabled={remove.isPending}
                  aria-label={`${entry.document_type_name} 제거`}
                  className="text-gray-500 disabled:opacity-50"
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      </ListState>

      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">문서 종류</span>
            <select
              name="document_type"
              required
              value={typeCode}
              onChange={(event) => setTypeCode(event.target.value)}
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
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            세트에 추가
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

export function ItemProfilesPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");
  // 서류 세트는 무역+인증이 편집한다(문서보관소와 같은 규약 — CERT 시드 문면).
  const canEditDocumentSet = hasRole(me, "TRADE") || hasRole(me, "CERT");
  // 요건 세트는 인증+관리자만이다(판정 ⑤ — 요건 데이터의 실무 취급 주체).
  const canEditRequirementSet = hasRole(me, "CERT");

  const [code, setCode] = useState("");
  const [nameKo, setNameKo] = useState("");
  // 선택은 행 객체로 보관한다 — 현재 쪽 items에서 매번 찾으면 목록 쪽 이동만으로
  // 편집 패널이 조용히 닫힌다(리뷰 확정 발견). 패널 내용은 id로 따로 조회한다.
  const [selected, setSelected] = useState<{ profile: ItemProfile; panel: "doc" | "req" } | null>(
    null,
  );

  const list = usePagedList<ItemProfile>(ITEM_PROFILES_QUERY_KEY, "/v1/item-profiles");

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
          제품·SKU를 묶는 분류입니다. 서류 세트는 여기서 편집합니다 — 기본 요건·마일스톤 세트는
          각 기능이 생기는 단계에서 연결됩니다.
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

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-6" />

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
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((profile) => (
                <tr key={profile.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{profile.code}</td>
                  <td className="px-4 py-2">{profile.name_ko}</td>
                  <td className="px-4 py-2">{orEmpty(profile.description)}</td>
                  <td className="cell-nowrap px-4 py-2">
                    <button
                      type="button"
                      onClick={() =>
                        setSelected((previous) =>
                          previous?.profile.id === profile.id && previous.panel === "doc"
                            ? null
                            : { profile, panel: "doc" },
                        )
                      }
                      className="text-sm text-gray-700 underline"
                    >
                      {selected?.profile.id === profile.id && selected.panel === "doc"
                        ? "서류 세트 닫기"
                        : "서류 세트"}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setSelected((previous) =>
                          previous?.profile.id === profile.id && previous.panel === "req"
                            ? null
                            : { profile, panel: "req" },
                        )
                      }
                      className="ml-3 text-sm text-gray-700 underline"
                    >
                      {selected?.profile.id === profile.id && selected.panel === "req"
                        ? "요건 세트 닫기"
                        : "요건 세트"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {selected?.panel === "doc" && (
        <DocumentSetEditor profile={selected.profile} canEdit={canEditDocumentSet} />
      )}
      {selected?.panel === "req" && (
        <RequirementSetEditor profile={selected.profile} canEdit={canEditRequirementSet} />
      )}
    </section>
  );
}
