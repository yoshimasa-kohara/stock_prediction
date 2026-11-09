import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";

import DateInput, { MSG_BAD_FORMAT, MSG_NOT_ENABLED } from "./DateInput";

/** 予測がある日(2026-07 の平日の一部。20日は祝日で予測なし) */
const DATES = ["2026-07-16", "2026-07-17", "2026-07-21", "2026-07-22"];

function setup(props: { value?: string; enabledDates?: string[] } = {}) {
  const onChange = vi.fn();
  const user = userEvent.setup();
  render(
    <DateInput label="予測日" value={props.value ?? "2026-07-22"} onChange={onChange} enabledDates={props.enabledDates} />,
  );
  const input = screen.getByRole("textbox", { name: "予測日" });
  return { onChange, user, input };
}

describe("DateInput(日付の入力欄)", () => {
  it("文字で正しい日を入れて Enter を押すと onChange を呼ぶ", async () => {
    const { onChange, user, input } = setup({ enabledDates: DATES });
    await user.clear(input);
    await user.type(input, "2026-07-21{Enter}");
    expect(onChange).toHaveBeenCalledWith("2026-07-21");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("フォーカスが外れたときも確かめる", async () => {
    const { onChange, user, input } = setup();
    await user.clear(input);
    await user.type(input, "2026-08-01");
    await user.tab();
    expect(onChange).toHaveBeenCalledWith("2026-08-01");
  });

  it("形が違うときはエラーを出し、onChange を呼ばない", async () => {
    const { onChange, user, input } = setup();
    await user.clear(input);
    await user.type(input, "2026/07/21{Enter}");
    expect(screen.getByText(MSG_BAD_FORMAT)).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("選べない日(予測がない日)はエラーを出す", async () => {
    const { onChange, user, input } = setup({ enabledDates: DATES });
    await user.clear(input);
    await user.type(input, "2026-07-20{Enter}");
    expect(screen.getByText(MSG_NOT_ENABLED)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("今と同じ値なら onChange を呼ばず、エラーも消す", async () => {
    const { onChange, user, input } = setup();
    await user.clear(input);
    await user.type(input, "x{Enter}");
    expect(screen.getByText(MSG_BAD_FORMAT)).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, "2026-07-22{Enter}");
    expect(screen.queryByText(MSG_BAD_FORMAT)).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("カレンダーで予測がない日は選べず、予測がある日を選ぶと onChange を呼んで閉じる", async () => {
    const { onChange, user } = setup({ enabledDates: DATES });
    await user.click(screen.getByRole("button", { name: "予測日をカレンダーで選ぶ" }));
    const cal = screen.getByRole("dialog", { name: "予測日のカレンダー" });
    expect(within(cal).getByText("予測がない日(土日・祝日など)は薄く表示し、選べない")).toBeInTheDocument();
    // 20日(予測なし)は選べない
    const day20 = within(cal).getByText("20").closest("button");
    expect(day20).toBeDisabled();
    // 21日(予測あり)を選ぶ
    const day21 = within(cal).getByText("21").closest("button");
    expect(day21).toBeEnabled();
    await user.click(day21 as HTMLButtonElement);
    expect(onChange).toHaveBeenCalledWith("2026-07-21");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-21");
  });

  it("選べる日を渡さなければ、どの日でも選べる", async () => {
    const { onChange, user } = setup();
    await user.click(screen.getByRole("button", { name: "予測日をカレンダーで選ぶ" }));
    const cal = screen.getByRole("dialog");
    expect(within(cal).queryByText("予測がない日(土日・祝日など)は薄く表示し、選べない")).toBeNull();
    await user.click(within(cal).getByText("25").closest("button") as HTMLButtonElement);
    expect(onChange).toHaveBeenCalledWith("2026-07-25");
  });

  it("カレンダーで今と同じ日を選んでも onChange を呼ばない", async () => {
    const { onChange, user } = setup();
    await user.click(screen.getByRole("button", { name: "予測日をカレンダーで選ぶ" }));
    // 選んでいる日をもう一度押すと、選択の解除(undefined)になるので何もしない
    await user.click(within(screen.getByRole("dialog")).getByText("22").closest("button") as HTMLButtonElement);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("値がないときは、選べる最後の日の月を出す", async () => {
    const { user } = setup({ value: "", enabledDates: ["2026-05-15"] });
    await user.click(screen.getByRole("button", { name: "予測日をカレンダーで選ぶ" }));
    expect(within(screen.getByRole("dialog")).getByText(/2026年5月/)).toBeInTheDocument();
  });

  it("カレンダーの外を押す、または Escape で閉じる", async () => {
    const { user, input } = setup();
    const btn = screen.getByRole("button", { name: "予測日をカレンダーで選ぶ" });
    await user.click(btn);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(btn);
    await user.click(input);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("外から値が変わったら、入力欄も変わりエラーが消える", async () => {
    function Wrapper() {
      const [v, setV] = useState("2026-07-22");
      return (
        <>
          <DateInput label="予測日" value={v} onChange={setV} />
          <button type="button" onClick={() => setV("2026-07-21")}>
            前の日
          </button>
        </>
      );
    }
    const user = userEvent.setup();
    render(<Wrapper />);
    const input = screen.getByRole("textbox", { name: "予測日" });
    await user.clear(input);
    await user.type(input, "x{Enter}");
    expect(screen.getByText(MSG_BAD_FORMAT)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "前の日" }));
    expect(input).toHaveValue("2026-07-21");
    expect(screen.queryByText(MSG_BAD_FORMAT)).toBeNull();
  });
});
