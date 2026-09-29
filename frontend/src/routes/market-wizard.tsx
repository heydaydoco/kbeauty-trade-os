// 신규 시장 개설 위저드 — 5단계 진행 체크리스트+딥링크 (DESIGN.md §5.5 / S2-4 PR-2).
//
// ★ 저장하지 않는다 — 각 단계의 완료는 서버가 데이터에서 계산한 값이다(진행 상태 컬럼 없음).
// ★ 협정(4단계)은 FTA 마스터(Phase 4) 도래 전이라 "모듈 도래 전"으로 보이고 완료 계산에서 빠진다(서버 계산).
// ★ T1 초안 투입은 인증+관리자다(화면 게이트는 표시일 — 실제 차단은 서버 §18.1). 투입된 템플릿은 전부
//   초안이고 확인일이 비어 있다 — 근거링크를 열어 확인한 뒤 요건 템플릿 화면에서 사람이 확정한다.
// ★ 고지문(notice)은 서버 카탈로그가 준 문구를 그대로 보인다 — 화면에 복사본이 없다.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { apiFetch } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import { MARKETS_QUERY_KEY } from "./markets";
import { REQUIREMENT_TEMPLATES_QUERY_KEY } from "./requirement-templates";

interface WizardStep {
  step: number;
  key: string;
  title: string;
  status: "DONE" | "IN_PROGRESS" | "TODO" | "NOT_AVAILABLE";
  counts: Record<string, number>;
  note: string | null;
}

interface Wizard {
  code: string;
  market_id: number | null;
  market_name: string | null;
  catalog_available: boolean;
  catalog_template_count: number;
  steps: WizardStep[];
  counted_steps: number;
  done_steps: number;
}

interface SeedStatus {
  version: string;
  notice: string[];
  markets: { code: string; name_ko: string; registered: boolean }[];
}

interface ApplyResult {
  version: string;
  results: { code: string; market_created: boolean; created: string[]; skipped: string[] }[];
}

const FRESH = { staleTime: 0 } as const;
const WIZARD_KEY = ["market-wizard"] as const;
const SEED_KEY = ["seeds", "t1"] as const;

const STATUS_LABEL: Record<WizardStep["status"], string> = {
  DONE: "✔ 완료",
  IN_PROGRESS: "◐ 진행 중",
  TODO: "○ 할 일",
  NOT_AVAILABLE: "– 모듈 도래 전",
};

const STATUS_CLASS: Record<WizardStep["status"], string> = {
  DONE: "text-signal-green",
  IN_PROGRESS: "text-signal-amber",
  TODO: "text-gray-700",
  NOT_AVAILABLE: "text-gray-500",
};

/** 단계별 이동처 — 저장하는 화면이 아니라 이미 있는 화면으로 보낸다. */
const LINKS: Record<string, { to: string; label: string }> = {
  market: { to: "/markets", label: "시장 화면" },
  templates: { to: "/requirement-templates", label: "요건 템플릿 화면" },
  hs: { to: "/skus", label: "SKU 화면(HS 세번)" },
  ingredient_rules: { to: "/ingredients", label: "성분 화면(국가별 규칙)" },
};

function countText(step: WizardStep): string | null {
  const c = step.counts;
  if (step.key === "templates") {
    return `초안 ${c.draft ?? 0}건 · 확정 ${c.confirmed ?? 0}건`;
  }
  if (step.key === "hs") return `등록된 HS 세번 ${c.hs_codes ?? 0}건`;
  if (step.key === "ingredient_rules") return `성분 규칙 ${c.rules ?? 0}건`;
  return null;
}

export function MarketWizardPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "CERT");
  const [params, setParams] = useSearchParams();
  const code = (params.get("market") ?? "").trim().toUpperCase();
  const [draft, setDraft] = useState(code);

  const seeds = useQuery({
    queryKey: SEED_KEY,
    queryFn: () => apiFetch<SeedStatus>("/v1/seeds/t1"),
    ...FRESH,
  });
  const wizard = useQuery({
    queryKey: [...WIZARD_KEY, code],
    queryFn: () => apiFetch<Wizard>(`/v1/market-wizard/${encodeURIComponent(code)}`),
    enabled: code !== "",
    ...FRESH,
  });

  const apply = useMutation({
    mutationFn: () =>
      apiFetch<ApplyResult>("/v1/seeds/t1/apply", { method: "POST", body: { markets: [code] } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: WIZARD_KEY });
      void client.invalidateQueries({ queryKey: SEED_KEY });
      // 투입은 시장·요건 템플릿 목록도 바꾼다 — 30초 캐시가 옛 목록을 보이지 않게 한다.
      void client.invalidateQueries({ queryKey: MARKETS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: REQUIREMENT_TEMPLATES_QUERY_KEY });
    },
  });

  const result = apply.data?.results[0];
  const data = wizard.data;

  return (
    <section className="p-8">
      <h1 className="text-xl font-semibold">신규 시장 개설</h1>
      <p className="mt-1 break-keep text-sm text-gray-600">
        시장 → 요건 템플릿 → HS·세율 → 협정 → 성분 규칙 순서로 준비합니다. 각 단계의 완료는 저장된 데이터에서
        자동 계산됩니다.
      </p>

      <form
        className="mt-4 flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          apply.reset();
          setParams(draft.trim() === "" ? {} : { market: draft.trim().toUpperCase() });
        }}
      >
        <label className="text-sm">
          시장 코드
          <input
            className="ml-2 w-20 rounded border px-2 py-1 uppercase"
            value={draft}
            maxLength={2}
            onChange={(event) => setDraft(event.target.value)}
            aria-label="시장 코드"
          />
        </label>
        <button type="submit" className="cell-nowrap rounded bg-gray-800 px-3 py-1 text-sm text-white">
          열기
        </button>
        {seeds.data?.markets.map((market) => (
          <button
            key={market.code}
            type="button"
            className="cell-nowrap rounded border px-2 py-1 text-xs"
            onClick={() => {
              apply.reset();
              setDraft(market.code);
              setParams({ market: market.code });
            }}
          >
            {market.code} {market.name_ko}
            {market.registered ? "" : " ·미등록"}
          </button>
        ))}
      </form>

      {code === "" && <p className="mt-6 text-gray-500">시장 코드를 고르거나 입력해 주세요.</p>}
      {wizard.isPending && code !== "" && <p className="mt-6 text-gray-500">확인 중…</p>}
      {wizard.isError && (
        <p role="alert" className="mt-6 text-signal-red">
          {wizard.error.message}
        </p>
      )}

      {data !== undefined && (
        <div className="mt-6">
          <h2 className="text-lg font-medium">
            {data.code} {data.market_name ?? "(미등록)"}
          </h2>
          <p className="mt-1 text-sm text-gray-600">
            {data.done_steps}/{data.counted_steps} 단계 완료 (모듈 도래 전 단계는 제외)
          </p>
          <ol className="mt-4 space-y-3">
            {data.steps.map((step) => {
              const link = LINKS[step.key];
              const counts = countText(step);
              return (
                <li key={step.key} className="rounded border p-3" data-step={step.key}>
                  <div className="flex flex-wrap items-baseline gap-3">
                    <span className="cell-nowrap text-sm text-gray-500">{step.step}단계</span>
                    <span className="font-medium">{step.title}</span>
                    <span className={`cell-nowrap text-sm ${STATUS_CLASS[step.status]}`}>
                      {STATUS_LABEL[step.status]}
                    </span>
                    {counts !== null && <span className="text-sm text-gray-600">{counts}</span>}
                    {link !== undefined && (
                      <Link className="cell-nowrap text-sm underline" to={link.to}>
                        {link.label}
                      </Link>
                    )}
                  </div>
                  {step.status === "TODO" && step.key === "market" && (
                    <p className="mt-1 text-sm text-gray-600">
                      시장이 등록돼 있지 않습니다. 아래 T1 초안 투입 시 함께 등록되거나, 시장 화면에서 직접
                      등록하세요.
                    </p>
                  )}
                  {step.note !== null && (
                    <p className="mt-1 break-keep text-sm text-gray-600">{step.note}</p>
                  )}
                  {step.key === "templates" && (
                    <div className="mt-2">
                      {data.catalog_available ? (
                        <>
                          {canEdit && (
                            <button
                              type="button"
                              className="cell-nowrap rounded bg-gray-800 px-3 py-1 text-sm text-white disabled:opacity-50"
                              disabled={apply.isPending}
                              onClick={() => apply.mutate()}
                            >
                              T1 초안 {data.catalog_template_count}건 투입
                            </button>
                          )}
                          <span className="ml-2 text-sm text-gray-600">
                            카탈로그 {data.catalog_template_count}건 — 이미 있는 템플릿은 건너뜁니다.
                          </span>
                        </>
                      ) : (
                        <span className="text-sm text-gray-600">
                          이 시장은 T1 카탈로그에 없습니다 — 요건 템플릿 화면에서 직접 등록하세요.
                        </span>
                      )}
                      {apply.isError && (
                        <p role="alert" className="mt-1 text-sm text-signal-red">
                          {fieldMessage(apply.error) ?? apply.error.message}
                        </p>
                      )}
                      {result !== undefined && (
                        <p role="status" className="mt-1 text-sm text-signal-green">
                          {result.market_created ? "시장 등록 · " : ""}초안 {result.created.length}건 투입 ·{" "}
                          {result.skipped.length}건 건너뜀
                        </p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
          {seeds.data !== undefined && (
            <ul className="mt-6 list-disc space-y-1 pl-5 text-sm text-gray-600">
              {seeds.data.notice.map((line) => (
                <li key={line} className="break-keep">
                  {line}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
