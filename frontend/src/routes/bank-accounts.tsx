// 은행 계좌 마스터 (S3-1 PR-6b — design-A A8 / G-02).
//
// 권한(백엔드 authz_matrix와 일치): 조회 ADMIN·TRADE(PI 발행 때 선택), **쓰기(등록·수정·비활성)는 ADMIN 전용**.
// 조회 권한이 없는 역할(물류·인증·조회)에게는 계좌번호를 그리지 않는다 — 화면 게이트는 편의일 뿐 서버가 403으로 다시 막는다(§18.1).
// 통화는 등록 뒤 바꿀 수 없다(가격·PI가 통화별 — 새 계좌를 등록). 발행된 PI는 스냅샷이라 계좌를 고쳐도 바뀌지 않는다.
// 멱등 키는 등록 다이얼로그를 여는 순간 1개(재시도 재사용·입력이 바뀌면 새 키), 더블클릭은 동기 잠금(ref)으로 1회만 전송.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ConfirmDialog, useDialogBehavior } from "../components/confirm-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { downloadFile } from "../lib/download";
import { useCurrencies } from "../lib/money";
import { usePagedList } from "../lib/paging";
import { BANK_ACCOUNTS_QUERY_KEY, type BankAccount } from "../lib/proforma";
import { hasRole, useSession } from "../lib/session";

const NOUN = "은행 계좌";
const SWIFT_PATTERN = /^[A-Z0-9]{8}([A-Z0-9]{3})?$/;

interface FormValues {
  label: string;
  currency: string;
  beneficiary_name: string;
  beneficiary_address: string;
  bank_name: string;
  bank_address: string;
  account_no: string;
  swift_code: string;
}

const EMPTY_FORM: FormValues = {
  label: "",
  currency: "",
  beneficiary_name: "",
  beneficiary_address: "",
  bank_name: "",
  bank_address: "",
  account_no: "",
  swift_code: "",
};

const TEXT_FIELDS: Array<{ key: Exclude<keyof FormValues, "currency" | "swift_code">; label: string; max: number }> = [
  { key: "label", label: "표시 이름", max: 80 },
  { key: "beneficiary_name", label: "수취인", max: 200 },
  { key: "beneficiary_address", label: "수취인 주소", max: 300 },
  { key: "bank_name", label: "은행명", max: 200 },
  { key: "bank_address", label: "은행 주소", max: 300 },
  { key: "account_no", label: "계좌번호", max: 40 },
];

export function BankAccountsPage() {
  const { me } = useSession();
  const canRead = hasRole(me, "TRADE"); // ADMIN 포함
  const isAdmin = hasRole(me); // 쓰기는 ADMIN 전용
  const [currency, setCurrency] = useState("");
  const [editing, setEditing] = useState<BankAccount | "new" | null>(null);
  const [deactivating, setDeactivating] = useState<BankAccount | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);
  const currencies = useCurrencies();

  const query = currency ? `?currency=${encodeURIComponent(currency)}` : "";
  const list = usePagedList<BankAccount>(
    [...BANK_ACCOUNTS_QUERY_KEY, "list", currency],
    `/v1/bank-accounts${query}`,
    canRead,
  );

  if (!canRead) {
    return (
      <section>
        <h1 className="text-2xl font-bold">은행 계좌</h1>
        <p role="alert" className="mt-3 break-keep text-signal-red">
          은행 계좌는 관리자·무역 담당만 볼 수 있습니다.
        </p>
      </section>
    );
  }

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(`/v1/bank-accounts/export.csv${query}`, "은행계좌목록.csv");
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">은행 계좌</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            PI에 적히는 입금 계좌입니다. 계좌를 고치거나 비활성해도 이미 발행된 PI는 바뀌지 않습니다.
            {!isAdmin && " 등록·수정·비활성은 관리자만 할 수 있습니다."}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void exportCsv()}
            className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm"
          >
            CSV 내보내기
          </button>
          {isAdmin && (
            <button
              type="button"
              onClick={() => setEditing("new")}
              className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-sm text-white"
            >
              계좌 등록
            </button>
          )}
        </div>
      </header>
      {csvError && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          {csvError}
        </p>
      )}

      <form
        role="search"
        aria-label="은행 계좌 필터"
        className="mt-4 flex flex-wrap items-end gap-3 text-sm"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">통화</span>
          <select
            value={currency}
            onChange={(event) => setCurrency(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {currencies.data?.items.map((item) => (
              <option key={item.code} value={item.code}>
                {item.code}
              </option>
            ))}
          </select>
        </label>
      </form>

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-4" />

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            currency
              ? "이 통화의 계좌가 없습니다."
              : isAdmin
                ? "아직 등록된 계좌가 없습니다. '계좌 등록'으로 PI에 쓸 계좌를 먼저 등록하세요."
                : "등록된 계좌가 없습니다. 관리자에게 계좌 등록을 요청하세요."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">표시 이름</th>
                <th className="cell-nowrap px-4 py-2 text-center">통화</th>
                <th className="cell-nowrap px-4 py-2">수취인</th>
                <th className="cell-nowrap px-4 py-2">은행</th>
                <th className="cell-nowrap px-4 py-2 text-center">계좌번호</th>
                <th className="cell-nowrap px-4 py-2 text-center">SWIFT</th>
                {isAdmin && <th className="cell-nowrap px-4 py-2 text-center">관리</th>}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="break-keep px-4 py-2">{row.label}</td>
                  <td className="cell-nowrap px-4 py-2 text-center">{row.currency}</td>
                  <td className="break-keep px-4 py-2">{row.beneficiary_name}</td>
                  <td className="break-keep px-4 py-2">{row.bank_name}</td>
                  <td className="num cell-nowrap px-4 py-2">{row.account_no}</td>
                  <td className="num cell-nowrap px-4 py-2">{row.swift_code}</td>
                  {isAdmin && (
                    <td className="cell-nowrap px-4 py-2 text-center">
                      <button type="button" onClick={() => setEditing(row)} className="underline">
                        수정
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeactivating(row)}
                        className="ml-3 text-gray-500 underline"
                      >
                        비활성
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {isAdmin && editing !== null && (
        <BankAccountFormDialog
          key={editing === "new" ? "new" : editing.id}
          account={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      {isAdmin && deactivating !== null && (
        <DeactivateDialog account={deactivating} onClose={() => setDeactivating(null)} />
      )}
    </section>
  );
}

/** 등록(account=null)·수정 공용 다이얼로그. */
function BankAccountFormDialog({
  account,
  onClose,
}: {
  account: BankAccount | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const currencies = useCurrencies();
  const creating = account === null;
  // 연 시점의 값을 고정한다 — 목록이 재조회돼도 입력 중인 폼을 조용히 덮어쓰지 않는다(충돌은 서버 409가 알린다).
  const initial = useRef<FormValues>(
    account === null
      ? EMPTY_FORM
      : {
          label: account.label,
          currency: account.currency,
          beneficiary_name: account.beneficiary_name,
          beneficiary_address: account.beneficiary_address,
          bank_name: account.bank_name,
          bank_address: account.bank_address,
          account_no: account.account_no,
          swift_code: account.swift_code,
        },
  ).current;
  const [values, setValues] = useState<FormValues>(initial);
  const [formError, setFormError] = useState<string | null>(null);
  const boxRef = useRef<HTMLFormElement | null>(null);
  const lock = useRef(false);
  // 멱등 키(등록 전용): 다이얼로그를 여는 순간 1개 — 입력이 바뀌면(본문이 달라지면) 새 키.
  const [initialKey] = useState(() => crypto.randomUUID());
  const keyRef = useRef(initialKey);
  // 수정의 기준 version — 이 다이얼로그를 열 때 화면이 본 값. 목록이 재조회돼도 바뀌지 않는다.
  const baseVersion = useRef(account?.version ?? 0);
  useDialogBehavior(boxRef, onClose);

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      creating
        ? apiFetch<BankAccount>("/v1/bank-accounts", {
            method: "POST",
            idempotencyKey: keyRef.current,
            body,
          })
        : apiFetch<BankAccount>(`/v1/bank-accounts/${account.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: BANK_ACCOUNTS_QUERY_KEY });
      onClose();
    },
  });

  function set<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    keyRef.current = crypto.randomUUID();
    setFormError(null);
    save.reset();
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function submit() {
    if (lock.current) return;
    const trimmed = Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, value.trim()]),
    ) as unknown as FormValues;
    const swift = trimmed.swift_code.toUpperCase();
    for (const field of TEXT_FIELDS) {
      if (trimmed[field.key] === "") {
        setFormError(`${field.label}을(를) 입력해 주세요.`);
        return;
      }
    }
    if (creating && trimmed.currency === "") {
      setFormError("통화를 선택해 주세요.");
      return;
    }
    if (!SWIFT_PATTERN.test(swift)) {
      setFormError("SWIFT 코드는 영문 대문자·숫자 8자리 또는 11자리입니다.");
      return;
    }
    let body: Record<string, unknown>;
    if (creating) {
      body = { ...trimmed, swift_code: swift };
    } else {
      // 바뀐 필드만 보낸다(통화는 불변이라 절대 보내지 않는다).
      body = { version: baseVersion.current };
      for (const field of TEXT_FIELDS) {
        if (trimmed[field.key] !== initial[field.key]) body[field.key] = trimmed[field.key];
      }
      if (swift !== initial.swift_code) body.swift_code = swift;
      if (Object.keys(body).length === 1) {
        setFormError("바뀐 내용이 없습니다.");
        return;
      }
    }
    lock.current = true; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    save.mutate(body, { onSettled: () => (lock.current = false) });
  }

  const conflict = isVersionConflict(save.error);

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <form
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label={creating ? "은행 계좌 등록" : "은행 계좌 수정"}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 className="text-lg font-bold">{creating ? "은행 계좌 등록" : "은행 계좌 수정"}</h2>
        <p className="mt-1 break-keep text-sm text-gray-500">
          {creating
            ? "PI에 적히는 입금 계좌입니다. 통화는 등록 뒤 바꿀 수 없습니다."
            : "통화는 바꿀 수 없습니다(새 계좌를 등록하세요). 이미 발행된 PI는 바뀌지 않습니다."}
        </p>
        <div className="mt-4 flex flex-col gap-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">통화</span>
            <select
              value={values.currency}
              disabled={!creating}
              onChange={(event) => set("currency", event.target.value)}
              className="rounded border border-gray-300 px-3 py-2 disabled:bg-gray-100"
            >
              <option value="">선택하세요</option>
              {currencies.data?.items.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.code}
                </option>
              ))}
            </select>
          </label>
          {TEXT_FIELDS.map((field) => (
            <label key={field.key} className="flex flex-col gap-1">
              <span className="text-gray-600">{field.label}</span>
              <input
                value={values[field.key]}
                maxLength={field.max}
                onChange={(event) => set(field.key, event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
          ))}
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">SWIFT 코드 (8 또는 11자리)</span>
            <input
              value={values.swift_code}
              maxLength={11}
              onChange={(event) => set("swift_code", event.target.value.toUpperCase())}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
        </div>
        {(formError || save.error) && (
          <div role="alert" className="mt-3 text-sm text-signal-red">
            <p className="break-keep">{formError ?? errorMessage(save.error, "저장하지 못했습니다.", NOUN)}</p>
            {conflict && (
              <button
                type="button"
                onClick={() => {
                  void client.invalidateQueries({ queryKey: BANK_ACCOUNTS_QUERY_KEY });
                  onClose();
                }}
                className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1"
              >
                최신 내용 불러오기
              </button>
            )}
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm"
          >
            닫기
          </button>
          <button
            type="submit"
            disabled={save.isPending}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {save.isPending ? "저장 중…" : creating ? "등록" : "저장"}
          </button>
        </div>
      </form>
    </div>
  );
}

function DeactivateDialog({ account, onClose }: { account: BankAccount; onClose: () => void }) {
  const client = useQueryClient();
  const lock = useRef(false);
  const key = useRef(crypto.randomUUID());
  const version = useRef(account.version); // 다이얼로그를 연 시점에 화면이 본 version

  const deactivate = useMutation({
    mutationFn: () =>
      apiFetch<BankAccount>(`/v1/bank-accounts/${account.id}?version=${version.current}`, {
        method: "DELETE",
        idempotencyKey: key.current,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: BANK_ACCOUNTS_QUERY_KEY });
      onClose();
    },
  });

  return (
    <ConfirmDialog
      title="계좌를 비활성할까요?"
      danger
      confirmLabel="비활성"
      description={
        <p>
          <strong>{account.label}</strong> ({account.currency}) 계좌를 비활성하면 새 PI에서 선택할 수 없습니다. 이미
          발행된 PI는 바뀌지 않습니다.
        </p>
      }
      pending={deactivate.isPending}
      error={deactivate.error ? errorMessage(deactivate.error, "비활성하지 못했습니다.", NOUN) : null}
      onReload={
        isVersionConflict(deactivate.error)
          ? () => {
              void client.invalidateQueries({ queryKey: BANK_ACCOUNTS_QUERY_KEY });
              onClose();
            }
          : undefined
      }
      onCancel={onClose}
      onConfirm={() => {
        if (lock.current) return;
        lock.current = true;
        deactivate.mutate(undefined, { onSettled: () => (lock.current = false) });
      }}
    />
  );
}
