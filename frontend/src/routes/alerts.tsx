// 알림센터 (DESIGN.md §2 ADR-07 "화면 알림센터" / §18.1 소유권 / S2-3 PR-1).
//
// ★ 이 화면은 **내 알림함**이다 — 관리자도 남의 알림을 보지 않는다. 서버가
//   수신자로 좁혀 주고(§18.1), 화면은 그 결과를 그대로 보여 준다.
// ★ '확인'은 재발송 금지의 기준이다(ADR-07) — 누르면 같은 사건이 다시 오지
//   않는다. 되돌리는 버튼을 두지 않았다(취소 경로는 문면에 없다 — 관찰 등재).
// ★ 관리자에게는 알림 규칙·배치 레지스트리 패널이 함께 보인다(§14 ⑭ 관리).
//   규칙 편집이 관리자 전용인 이유: 기일 알림의 관리자 폴백 게이트가 여기 걸린다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { orEmpty } from "../lib/labels";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

export interface Alert {
  id: number;
  alert_rule_id: number | null;
  recipient_user_id: number;
  title: string;
  body: string | null;
  severity: string;
  dedup_key: string;
  acknowledged_at: string | null;
  entity_type: string | null;
  entity_id: number | null;
}

export interface AlertRule {
  id: number;
  code: string;
  name_ko: string;
  event_type: string;
  severity: string;
  recipient_user_id: number | null;
  recipient_role: string | null;
  is_enabled: boolean;
  version: number;
}

export interface ScheduledJob {
  id: number;
  code: string;
  name_ko: string;
  schedule: string;
  is_enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  is_mapped: boolean;
  version: number;
}

export const ALERTS_QUERY_KEY = ["alerts"] as const;
export const ALERT_RULES_QUERY_KEY = ["alert-rules"] as const;
export const SCHEDULED_JOBS_QUERY_KEY = ["scheduled-jobs"] as const;

const SEVERITY_LABEL: Record<string, string> = {
  INFO: "안내",
  WARN: "주의",
  CRITICAL: "긴급",
};

const JOB_STATUS_LABEL: Record<string, string> = {
  OK: "정상",
  FAILED: "실패",
  RUNNING: "실행 중",
};

/** 수신 대상 표기 — 두 컬럼이 다 비면 "담당자"다(서버 라우팅 규칙과 같은 말).
 *
 *  ★ 값이 있어도 담당자가 있으면 담당자가 이긴다 — 이 열은 "담당자가 없을 때
 *    누구에게 가는가"를 뜻한다(판정 요청 7 (i) 순서). */
function recipientLabel(rule: AlertRule): string {
  if (rule.recipient_user_id !== null) return `사용자 #${rule.recipient_user_id}`;
  if (rule.recipient_role !== null) return `${rule.recipient_role} 역할 전원`;
  return "담당자";
}

export function AlertsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const isAdmin = hasRole(me);

  const list = usePagedList<Alert>(ALERTS_QUERY_KEY, "/v1/alerts");
  const rules = usePagedQuery<AlertRule>(ALERT_RULES_QUERY_KEY, "/v1/alert-rules?size=200", isAdmin);
  const jobs = usePagedQuery<ScheduledJob>(
    SCHEDULED_JOBS_QUERY_KEY,
    "/v1/scheduled-jobs?size=200",
    isAdmin,
  );

  const [rule, setRule] = useState({ code: "", name_ko: "", event_type: "", recipient_role: "" });

  const acknowledge = useMutation({
    mutationFn: (alertId: number) =>
      apiFetch<Alert>(`/v1/alerts/${alertId}/ack`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ALERTS_QUERY_KEY });
    },
  });

  const createRule = useMutation({
    mutationFn: () =>
      apiFetch<AlertRule>("/v1/alert-rules", {
        method: "POST",
        body: {
          code: rule.code,
          name_ko: rule.name_ko,
          event_type: rule.event_type,
          // 비우면 "담당자에게" — 담당자가 없을 때만 이 값이 쓰인다(서버 규칙).
          recipient_role: rule.recipient_role === "" ? undefined : rule.recipient_role,
        },
      }),
    onSuccess: () => {
      setRule({ code: "", name_ko: "", event_type: "", recipient_role: "" });
      void client.invalidateQueries({ queryKey: ALERT_RULES_QUERY_KEY });
    },
  });

  const toggleRule = useMutation({
    mutationFn: (input: AlertRule) =>
      apiFetch<AlertRule>(`/v1/alert-rules/${input.id}`, {
        method: "PATCH",
        body: { version: input.version, is_enabled: !input.is_enabled },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ALERT_RULES_QUERY_KEY });
    },
  });

  const removeRule = useMutation({
    mutationFn: (input: AlertRule) =>
      apiFetch<void>(`/v1/alert-rules/${input.id}?version=${input.version}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ALERT_RULES_QUERY_KEY });
    },
  });

  const toggleJob = useMutation({
    mutationFn: (input: ScheduledJob) =>
      apiFetch<ScheduledJob>(`/v1/scheduled-jobs/${input.id}`, {
        method: "PATCH",
        // version을 싣지 않는다 — 실행기가 같은 행을 계속 갱신해 낙관 잠금이
        // 성립하지 않는다(서버 set_enabled 독스트링).
        body: { is_enabled: !input.is_enabled },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SCHEDULED_JOBS_QUERY_KEY });
    },
  });

  const ruleError = createRule.error;

  const unread = list.data?.items.filter((alert) => alert.acknowledged_at === null).length ?? 0;

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">알림센터</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            나에게 온 알림입니다. '확인'을 누르면 같은 사건으로는 다시 알림이 오지 않습니다.
          </p>
        </div>
        <span className="cell-nowrap text-sm text-gray-500">이 쪽 미확인 {unread}건</span>
      </header>

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-6" />

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint="받은 알림이 없습니다. 기일·상태 변화가 생기면 여기에 쌓입니다."
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">등급</th>
                <th className="px-4 py-2">내용</th>
                <th className="cell-nowrap px-4 py-2">대상</th>
                <th className="cell-nowrap px-4 py-2 text-center">확인</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((alert) => (
                <tr key={alert.id} className="border-t border-gray-100 align-top">
                  <td className="cell-nowrap px-4 py-2 text-center">
                    {SEVERITY_LABEL[alert.severity] ?? alert.severity}
                  </td>
                  <td className="break-keep px-4 py-2">
                    <p className="font-medium">{alert.title}</p>
                    {alert.body && <p className="mt-1 text-gray-500">{alert.body}</p>}
                  </td>
                  <td className="cell-nowrap px-4 py-2 text-gray-500">
                    {alert.entity_type ? `${alert.entity_type} #${alert.entity_id}` : orEmpty(null)}
                  </td>
                  <td className="cell-nowrap px-4 py-2 text-center">
                    {alert.acknowledged_at ? (
                      <span className="text-gray-500">{toKstDisplay(alert.acknowledged_at)}</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => acknowledge.mutate(alert.id)}
                        disabled={acknowledge.isPending}
                        className="rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
                      >
                        확인
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {isAdmin && (
        <>
          <section className="mt-10">
            <h2 className="text-lg font-semibold">알림 규칙</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              어떤 사건을 누구에게 보낼지 정합니다. 알림은 <strong>담당자가 우선</strong>이고, 담당자가
              없을 때만 여기 적은 수신 대상이 쓰입니다. 기일 알림은 담당자도 수신 대상도 없을 때
              관리자에게 갑니다.
            </p>
            <form
              className="mt-4 rounded-lg border border-gray-200 p-4"
              onSubmit={(event) => {
                event.preventDefault();
                createRule.mutate();
              }}
            >
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">규칙 코드</span>
                  <input
                    name="code"
                    required
                    maxLength={60}
                    value={rule.code}
                    onChange={(event) =>
                      setRule((previous) => ({ ...previous, code: event.target.value }))
                    }
                    className="w-40 rounded border border-gray-300 px-3 py-2"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">이름</span>
                  <input
                    name="name_ko"
                    required
                    maxLength={100}
                    value={rule.name_ko}
                    onChange={(event) =>
                      setRule((previous) => ({ ...previous, name_ko: event.target.value }))
                    }
                    className="rounded border border-gray-300 px-3 py-2"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">사건 코드</span>
                  <input
                    name="event_type"
                    required
                    maxLength={60}
                    value={rule.event_type}
                    onChange={(event) =>
                      setRule((previous) => ({ ...previous, event_type: event.target.value }))
                    }
                    className="w-72 rounded border border-gray-300 px-3 py-2"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">수신 역할 (비우면 담당자)</span>
                  <select
                    name="recipient_role"
                    value={rule.recipient_role}
                    onChange={(event) =>
                      setRule((previous) => ({ ...previous, recipient_role: event.target.value }))
                    }
                    className="rounded border border-gray-300 px-3 py-2"
                  >
                    <option value="">담당자</option>
                    <option value="ADMIN">관리자</option>
                    <option value="TRADE">무역</option>
                    <option value="LOGISTICS">물류</option>
                    <option value="CERT">인증</option>
                  </select>
                </label>
                <button
                  type="submit"
                  disabled={createRule.isPending}
                  className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
                >
                  {createRule.isPending ? "등록 중…" : "규칙 등록"}
                </button>
              </div>
              {ruleError && (
                <p role="alert" className="mt-3 text-sm text-signal-red">
                  {ruleError instanceof Error ? ruleError.message : "규칙을 등록하지 못했습니다."}
                </p>
              )}
            </form>

            <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
              <ListState
                isPending={rules.isPending}
                error={rules.error}
                isEmpty={rules.data?.items.length === 0}
                emptyHint="등록된 알림 규칙이 없습니다. 기일 알림은 관리자에게 전달되고, 그 밖의 사건은 발송되지 않습니다."
              >
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-gray-600">
                    <tr>
                      <th className="cell-nowrap px-4 py-2">코드</th>
                      <th className="px-4 py-2">이름</th>
                      <th className="cell-nowrap px-4 py-2">사건</th>
                      <th className="cell-nowrap px-4 py-2">수신 대상</th>
                      <th className="cell-nowrap px-4 py-2 text-center">사용</th>
                      <th className="cell-nowrap px-4 py-2 text-center">삭제</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.data?.items.map((row) => (
                      <tr key={row.id} className="border-t border-gray-100">
                        <td className="cell-nowrap px-4 py-2">{row.code}</td>
                        <td className="break-keep px-4 py-2">{row.name_ko}</td>
                        <td className="cell-nowrap px-4 py-2 text-gray-500">{row.event_type}</td>
                        <td className="cell-nowrap px-4 py-2">{recipientLabel(row)}</td>
                        <td className="cell-nowrap px-4 py-2 text-center">
                          <button
                            type="button"
                            onClick={() => toggleRule.mutate(row)}
                            disabled={toggleRule.isPending}
                            className="rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
                          >
                            {row.is_enabled ? "사용" : "중지"}
                          </button>
                        </td>
                        <td className="cell-nowrap px-4 py-2 text-center">
                          <button
                            type="button"
                            onClick={() => removeRule.mutate(row)}
                            disabled={removeRule.isPending}
                            className="text-gray-500 underline disabled:opacity-50"
                          >
                            삭제
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ListState>
            </div>
          </section>

          <section className="mt-10">
            <h2 className="text-lg font-semibold">배치 레지스트리</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              등록된 배치와 마지막 실행 결과입니다. 실행 주기는 코드와 함께 관리되며 화면에서
              바꾸지 않습니다.
            </p>
            <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
              <ListState
                isPending={jobs.isPending}
                error={jobs.error}
                isEmpty={jobs.data?.items.length === 0}
                emptyHint="등록된 배치가 없습니다."
              >
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-gray-600">
                    <tr>
                      <th className="cell-nowrap px-4 py-2">코드</th>
                      <th className="px-4 py-2">이름</th>
                      <th className="cell-nowrap px-4 py-2">주기</th>
                      <th className="cell-nowrap px-4 py-2">마지막 실행</th>
                      <th className="cell-nowrap px-4 py-2 text-center">결과</th>
                      <th className="cell-nowrap px-4 py-2 text-center">사용</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.data?.items.map((job) => (
                      <tr key={job.id} className="border-t border-gray-100">
                        <td className="cell-nowrap px-4 py-2">
                          {job.code}
                          {!job.is_mapped && (
                            <span className="ml-2 text-signal-red">실행 함수 없음</span>
                          )}
                        </td>
                        <td className="break-keep px-4 py-2">{job.name_ko}</td>
                        <td className="cell-nowrap px-4 py-2 text-gray-500">{job.schedule}</td>
                        <td className="cell-nowrap px-4 py-2 text-gray-500">
                          {job.last_run_at ? toKstDisplay(job.last_run_at) : orEmpty(null)}
                        </td>
                        <td className="cell-nowrap px-4 py-2 text-center">
                          {job.is_enabled
                            ? (JOB_STATUS_LABEL[job.last_status ?? ""] ?? orEmpty(null))
                            : "중지"}
                        </td>
                        <td className="cell-nowrap px-4 py-2 text-center">
                          <button
                            type="button"
                            onClick={() => toggleJob.mutate(job)}
                            disabled={toggleJob.isPending}
                            className="rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
                          >
                            {job.is_enabled ? "끄기" : "켜기"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ListState>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
