// 인테이크 등록·수정 폼의 형식 문제 표시 (S3-1 PR-13b 적대 검토 반영) — 모든 문제를 한 번에 보이고, 첫 문제 입력으로 포커스를 옮긴다.
// 입력의 aria-invalid·aria-describedby는 호출부가 `invalidIds`로 붙인다(오류 목록 id = `errorId`).

import { useEffect, useState } from "react";
import type { FormProblem } from "../lib/order-intake";

/** 형식 문제 상태 — set할 때마다 첫 문제 입력으로 포커스를 옮긴다(같은 문제가 다시 나와도). */
export function useFormProblems() {
  const [state, setState] = useState<{ problems: FormProblem[]; n: number }>({ problems: [], n: 0 });
  useEffect(() => {
    if (state.n === 0) return;
    const first = state.problems.find((p) => p.inputId !== null);
    if (first?.inputId) document.getElementById(first.inputId)?.focus();
  }, [state]);
  const invalidIds = new Set(state.problems.flatMap((p) => (p.inputId === null ? [] : [p.inputId])));
  return {
    problems: state.problems,
    invalidIds,
    show: (problems: FormProblem[]) => setState((prev) => ({ problems, n: prev.n + 1 })),
    clear: () => setState((prev) => (prev.problems.length === 0 ? prev : { problems: [], n: prev.n })),
  };
}

export function FormProblemList({ id, problems }: { id: string; problems: FormProblem[] }) {
  if (problems.length === 0) return null;
  return (
    <div id={id} role="alert" className="break-keep text-sm text-signal-red">
      {problems.length === 1 ? (
        <p>{problems[0]?.message}</p>
      ) : (
        <>
          <p>입력을 확인해 주세요({problems.length}건):</p>
          <ul className="list-disc pl-5">
            {problems.map((p, i) => (
              <li key={i}>{p.message}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
