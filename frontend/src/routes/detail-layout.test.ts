// 상세 화면 세로 쌓기 그리드의 열 폭 계약 (S3-1 PR-16 워크스루 발견 — 390px에서 페이지 가로 스크롤).
// `grid`의 암묵 열은 auto라 자식의 min-content(넓은 표·nowrap 셀)가 열 전체를 밀어 화면 밖으로 넓힌다. jsdom은 레이아웃을
// 계산하지 못하므로 실브라우저 실측(scrollWidth 780→390)으로 확인했고, 여기서는 그 원인 클래스가 되돌아오지 않게 소스를 고정한다.
import { describe, expect, it } from "vitest";

const sources = import.meta.glob("./*-detail.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("상세 화면 레이아웃", () => {
  it("세로 쌓기 그리드는 열을 minmax(0,1fr)로 묶어 넓은 자식이 페이지를 넓히지 못한다", () => {
    const offenders = Object.entries(sources)
      .filter(([, src]) => /className="mt-6 grid gap-6"/.test(src))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
    const fixed = Object.entries(sources).filter(([, src]) => src.includes('className="mt-6 grid grid-cols-[minmax(0,1fr)] gap-6"'));
    expect(fixed.length).toBeGreaterThanOrEqual(6);
  });
});
