// 목록 총계·쪽 이동 표준 컴포넌트 (§18.4 / 판정 ② 2026-08-06).
//
// 이 파일이 고정하는 것: ① 데이터가 오기 전에는 "전체 0건"을 지어내지 않는다.
// ② 한 쪽뿐이면 총계만 있고 이동 버튼이 없다. ③ 여러 쪽이면 경계(1쪽·마지막
// 쪽)에서 해당 방향 버튼이 눌리지 않는다.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ListPager } from "./list-pager";

function pageData(total: number, size = 50) {
  return { items: [], total, page: 1, size };
}

describe("ListPager", () => {
  it("데이터가 오기 전에는 아무것도 그리지 않는다 — '전체 0건'은 거짓이다", () => {
    const { container } = render(
      <ListPager data={undefined} page={1} onPageChange={() => undefined} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("한 쪽뿐이면 총계만 보여주고 이동 버튼이 없다", () => {
    render(<ListPager data={pageData(30)} page={1} onPageChange={() => undefined} />);
    expect(screen.getByText("전체 30건")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("빈 목록도 총계 0건을 보여준다 (빈 상태 문구는 ListState 몫)", () => {
    render(<ListPager data={pageData(0)} page={1} onPageChange={() => undefined} />);
    expect(screen.getByText("전체 0건")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("★ 여러 쪽이면 총계·쪽 표시가 있고 '다음'이 다음 쪽을 요청한다", () => {
    const onPageChange = vi.fn();
    render(<ListPager data={pageData(120)} page={1} onPageChange={onPageChange} />);

    expect(screen.getByText("전체 120건")).toBeInTheDocument();
    expect(screen.getByText("1/3쪽")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("마지막 쪽에서는 '다음'이 눌리지 않고 '이전'만 동작한다", () => {
    const onPageChange = vi.fn();
    render(<ListPager data={pageData(120)} page={3} onPageChange={onPageChange} />);

    expect(screen.getByText("3/3쪽")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "이전" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
