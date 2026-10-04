// 사용자·역할 화면 (S3-2 PR-7 · design-D D14 · ADR-0086 ② · 통합 §9 R-22) — ADMIN 전용, 백엔드 변경 0.
//
// ★ 화면 게이트는 표시일 뿐이다. 목록·부여·회수·활성·잠금 해제 모두 서버가 ADMIN을 재검증한다(§18.1).
// ★ 계정 생성은 이 화면에 없다 — 관리자 CLI(`create-admin`)로 만들고, 여기서 업무 역할을 부여한 뒤 ADMIN을 회수한다(R-22).
// ★ 모든 변경은 확인 대화상자(대상 이메일·변경 내용)를 거친다. 마지막 관리자 보호 409는 서버 문구 그대로 대화상자에 보인다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ConfirmDialog } from "../components/confirm-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { usePagedList } from "../lib/paging";
import { ME_QUERY_KEY, hasRole, useSession } from "../lib/session";
import {
  ROLE_CODES,
  USERS_PATH,
  USERS_QUERY_KEY,
  roleLabel,
  runUserAction,
  userActionError,
  type UserAction,
  type UserSummary,
} from "../lib/users";

export const ACCOUNT_CREATION_NOTE =
  "계정 생성은 관리자 CLI(create-admin)로 합니다 — 새 계정은 관리자로 생기므로, 여기서 업무 역할을 부여한 뒤 관리자 역할을 회수하세요.";

interface PendingAction {
  action: UserAction;
  /** 대화상자 1회 열림 = 키 1개 — 같은 대화상자 안 재시도는 같은 키를 쓴다. */
  key: string;
}

function dialogText(action: UserAction, meId: number | undefined) {
  const who = action.user.email;
  switch (action.kind) {
    case "grant":
      return {
        title: "역할 부여",
        confirmLabel: "부여",
        danger: false,
        body: `${who}에게 '${roleLabel(action.role)}' 역할을 부여합니다.`,
        warning: null,
      };
    case "revoke":
      return {
        title: "역할 회수",
        confirmLabel: "회수",
        danger: true,
        body: `${who}의 '${roleLabel(action.role)}' 역할을 회수합니다.`,
        warning:
          action.role === "ADMIN" && action.user.id === meId
            ? "본인의 관리자 역할입니다. 회수하면 이 화면에 다시 들어올 수 없습니다."
            : null,
      };
    case "active":
      return action.isActive
        ? { title: "계정 활성화", confirmLabel: "활성화", danger: false, body: `${who} 계정을 활성화합니다.`, warning: null }
        : {
            title: "계정 비활성화",
            confirmLabel: "비활성화",
            danger: true,
            body: `${who} 계정을 비활성화합니다. 로그인 중인 세션도 모두 끊깁니다.`,
            warning:
              action.user.id === meId
                ? "본인 계정입니다. 비활성화하면 바로 로그아웃되고 다시 로그인할 수 없습니다."
                : null,
          };
    case "unlock":
      return {
        title: "로그인 잠금 해제",
        confirmLabel: "잠금 해제",
        danger: false,
        body: `${who} 계정의 로그인 실패 잠금을 풉니다(잠겨 있지 않으면 변화 없음).`,
        warning: null,
      };
  }
}

function ActionDialog({
  pending,
  meId,
  onClose,
}: {
  pending: PendingAction;
  meId: number | undefined;
  onClose: (done: string | null) => void;
}) {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => runUserAction(pending.action, pending.key),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: USERS_QUERY_KEY });
      // 본인 역할·활성 상태가 바뀌면 셸 메뉴·이 화면 게이트도 서버 기준으로 다시 그린다.
      if (pending.action.user.id === meId) void client.invalidateQueries({ queryKey: ME_QUERY_KEY });
      onClose(`${pending.action.user.email}: ${dialogText(pending.action, meId).title} 완료`);
    },
  });
  const text = dialogText(pending.action, meId);

  return (
    <ConfirmDialog
      title={text.title}
      confirmLabel={text.confirmLabel}
      danger={text.danger}
      description={
        <>
          <p>{text.body}</p>
          {text.warning && <p className="mt-2 font-semibold text-signal-red">{text.warning}</p>}
        </>
      }
      pending={mutation.isPending}
      error={mutation.error ? userActionError(mutation.error) : null}
      onConfirm={() => mutation.mutate()}
      onCancel={() => onClose(null)}
    />
  );
}

function GrantControl({ user, onPick }: { user: UserSummary; onPick: (role: string) => void }) {
  const missing = ROLE_CODES.filter((code) => !user.roles.includes(code));
  const [role, setRole] = useState("");
  if (missing.length === 0) return null;
  const selected = missing.includes(role) ? role : "";
  return (
    <span className="flex items-center gap-1">
      <select
        aria-label={`${user.email} 부여할 역할`}
        value={selected}
        onChange={(event) => setRole(event.target.value)}
        className="rounded border border-gray-300 px-1 py-1"
      >
        <option value="">역할 선택</option>
        {missing.map((code) => (
          <option key={code} value={code}>
            {roleLabel(code)}
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={selected === ""}
        onClick={() => onPick(selected)}
        className="cell-nowrap rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
      >
        부여
      </button>
    </span>
  );
}

export function SettingsUsersPage() {
  const { me } = useSession();
  const isAdmin = hasRole(me);
  const users = usePagedList<UserSummary>(USERS_QUERY_KEY, USERS_PATH, isAdmin);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!isAdmin) {
    return (
      <section>
        <h1 className="text-2xl font-bold">사용자·역할</h1>
        <p role="alert" className="mt-4 break-keep text-red-700">
          접근할 수 없습니다 — 사용자·역할은 관리자만 볼 수 있습니다.
        </p>
      </section>
    );
  }

  const open = (action: UserAction) => {
    setNotice(null);
    setPending({ action, key: crypto.randomUUID() });
  };
  const rows = users.data?.items ?? [];

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">사용자·역할</h1>
        <p className="mt-1 break-keep text-sm text-gray-500">{ACCOUNT_CREATION_NOTE}</p>
      </header>

      {notice && (
        <p role="status" className="mt-3 break-keep text-sm text-green-700">
          {notice}
        </p>
      )}

      <ListPager data={users.data} page={users.page} onPageChange={users.setPage} className="mt-4" />
      <ListState
        isPending={users.isPending}
        error={users.error}
        isEmpty={rows.length === 0}
        emptyHint="사용자가 없습니다. 관리자 CLI(create-admin)로 계정을 먼저 만드세요."
      >
        <div className="mt-2 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-600">
                <th className="cell-nowrap p-2">이메일</th>
                <th className="cell-nowrap p-2">표시명</th>
                <th className="cell-nowrap p-2 text-center">상태</th>
                <th className="cell-nowrap p-2">역할</th>
                <th className="cell-nowrap p-2">역할 부여</th>
                <th className="cell-nowrap p-2">계정</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((user) => (
                <tr key={user.id} className="border-b border-gray-100 align-top">
                  <td className="cell-nowrap p-2">
                    {user.email}
                    {user.id === me?.id && <span className="ml-1 text-xs text-gray-500">(나)</span>}
                  </td>
                  <td className="break-keep p-2">{user.display_name}</td>
                  <td className="cell-nowrap p-2 text-center">
                    {user.is_active ? (
                      <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-semibold text-green-800">활성</span>
                    ) : (
                      <span className="rounded bg-gray-200 px-1.5 py-0.5 text-xs font-semibold text-gray-700">비활성</span>
                    )}
                  </td>
                  <td className="p-2">
                    {user.roles.length === 0 ? (
                      <span className="cell-nowrap text-gray-500">역할 없음</span>
                    ) : (
                      <ul className="flex flex-wrap gap-1">
                        {user.roles.map((role) => (
                          <li
                            key={role}
                            title={role}
                            className="cell-nowrap flex items-center gap-1 rounded bg-gray-100 px-1.5 py-0.5"
                          >
                            {roleLabel(role)}
                            <button
                              type="button"
                              aria-label={`${user.email} ${roleLabel(role)} 역할 회수`}
                              onClick={() => open({ kind: "revoke", user, role })}
                              className="text-xs text-signal-red underline"
                            >
                              회수
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td className="p-2">
                    <GrantControl user={user} onPick={(role) => open({ kind: "grant", user, role })} />
                  </td>
                  <td className="p-2">
                    <span className="flex flex-wrap gap-1">
                      <button
                        type="button"
                        aria-label={`${user.email} ${user.is_active ? "비활성화" : "활성화"}`}
                        onClick={() => open({ kind: "active", user, isActive: !user.is_active })}
                        className="cell-nowrap rounded border border-gray-300 px-2 py-1"
                      >
                        {user.is_active ? "비활성화" : "활성화"}
                      </button>
                      <button
                        type="button"
                        aria-label={`${user.email} 잠금 해제`}
                        onClick={() => open({ kind: "unlock", user })}
                        className="cell-nowrap rounded border border-gray-300 px-2 py-1"
                      >
                        잠금 해제
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ListState>

      {pending && (
        <ActionDialog
          key={pending.key}
          pending={pending}
          meId={me?.id}
          onClose={(done) => {
            setPending(null);
            if (done) setNotice(done);
          }}
        />
      )}
    </section>
  );
}
