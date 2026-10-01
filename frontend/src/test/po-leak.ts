// 원가 누출 채널 전수 점검 도우미 (S3-1 PR-8b 적대 검토 4) — 화면 텍스트 외 채널(콘솔·스토리지·요청 URL·속성)에 원가가 새지 않는지.

import { expect, vi } from "vitest";
import type { Call } from "./qt-fixtures";

/** 콘솔 5종을 가로채 기록한다 — 테스트 시작 시 호출하고 반환값을 assertNoLeak에 넘긴다. */
export function captureConsole(): string[] {
  const logs: string[] = [];
  for (const method of ["log", "info", "warn", "error", "debug"] as const) {
    vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
      logs.push(args.map((arg) => String(arg)).join(" "));
    });
  }
  return logs;
}

/** 화면에 보이는 글자를 뺀 모든 채널에 센티널이 없다 — 원가가 보이는(Full) 응답에서도 지킨다. */
export function assertNoLeakOutsideText(sentinels: string[], logs: string[], calls: Call[]): void {
  const attributes: string[] = [];
  for (const element of Array.from(document.body.querySelectorAll("*"))) {
    for (const attr of Array.from(element.attributes)) attributes.push(`${attr.name}=${attr.value}`);
  }
  for (const sentinel of sentinels) {
    expect(logs.join("\n")).not.toContain(sentinel);
    expect(JSON.stringify({ ...localStorage })).not.toContain(sentinel);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(sentinel);
    expect(calls.map((c) => c.url).join("\n")).not.toContain(sentinel);
    expect(attributes.join("\n")).not.toContain(sentinel); // aria-label·title·href 등
    expect(document.title).not.toContain(sentinel);
    expect(window.location.href).not.toContain(sentinel);
    expect(JSON.stringify(window.history.state)).not.toContain(sentinel);
  }
}
