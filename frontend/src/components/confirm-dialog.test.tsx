// 확인 다이얼로그 접근성 — 포커스 트랩·Esc(문서 레벨)·포커스 복귀.

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ConfirmDialog } from "./confirm-dialog";

function Host() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        열기
      </button>
      {open && (
        <ConfirmDialog
          title="제목"
          description="설명"
          confirmLabel="확정"
          reasonLabel="사유"
          onCancel={() => setOpen(false)}
          onConfirm={() => undefined}
        />
      )}
    </>
  );
}

describe("ConfirmDialog", () => {
  it("열리면 첫 입력에 포커스, Tab은 안에서만 돌고, Esc(문서)로 닫히며 연 버튼으로 돌아간다", () => {
    render(<Host />);
    const opener = screen.getByRole("button", { name: "열기" });
    opener.focus();
    fireEvent.click(opener);
    const textarea = screen.getByLabelText("사유");
    expect(textarea).toHaveFocus();
    // 사유를 채워 확정 버튼을 활성화한 뒤 마지막 요소에서 Tab → 첫 요소
    fireEvent.change(textarea, { target: { value: "x" } });
    screen.getByRole("button", { name: "확정" }).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(textarea).toHaveFocus();
    // 첫 요소에서 Shift+Tab → 마지막 요소
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "확정" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("reasonMinLength: 최소 글자 수 미만이면 확정 버튼이 막힌다", () => {
    render(
      <ConfirmDialog
        title="제목"
        description="설명"
        confirmLabel="확정"
        reasonLabel="사유"
        reasonMinLength={2}
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    );
    const confirm = screen.getByRole("button", { name: "확정" });
    fireEvent.change(screen.getByLabelText("사유"), { target: { value: " 가 " } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("사유"), { target: { value: "가나" } });
    expect(confirm).toBeEnabled();
  });

  it("사유 접근성: 부적합 사유는 aria-invalid·aria-describedby로 연결되고, 비활성 확정 버튼은 사유 안내와 연결된다", () => {
    render(
      <ConfirmDialog
        title="제목"
        description="설명"
        confirmLabel="확정"
        reasonLabel="사유"
        reasonMinLength={3}
        reasonHint="힌트 문구"
        reasonValidator={(r) => (r.includes("!") ? "느낌표는 쓸 수 없습니다." : null)}
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    );
    const box = screen.getByRole("textbox");
    const confirmButton = screen.getByRole("button", { name: "확정" });
    expect(box).toHaveAttribute("aria-invalid", "false");
    expect(box).toHaveAccessibleDescription("힌트 문구");
    expect(confirmButton).toHaveAccessibleDescription("사유를 3자 이상 입력해야 확정할 수 있습니다.");
    fireEvent.change(box, { target: { value: "가나다!" } });
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box).toHaveAccessibleDescription("느낌표는 쓸 수 없습니다. 힌트 문구");
    expect(confirmButton).toBeDisabled();
    expect(confirmButton).toHaveAccessibleDescription("느낌표는 쓸 수 없습니다.");
    fireEvent.change(box, { target: { value: "가나다" } });
    expect(confirmButton).toBeEnabled();
    expect(confirmButton).not.toHaveAttribute("aria-describedby");
  });

  it("처리 중(버튼 전부 비활성 — 포커스 대상 0개)에도 Tab이 모달 밖으로 새지 않는다(PR-15b 검토)", () => {
    render(
      <ConfirmDialog title="제목" description="설명" confirmLabel="확정" pending onCancel={() => undefined} onConfirm={() => undefined} />,
    );
    // fireEvent는 기본 동작이 막히면 false를 돌려준다.
    expect(fireEvent.keyDown(document, { key: "Tab" })).toBe(false);
    expect(fireEvent.keyDown(document, { key: "Tab", shiftKey: true })).toBe(false);
  });
});
