import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import NotesText, { NOTE_SEPARATOR } from "./NotesText";

describe("NotesText", () => {
  it("色のクラスを付け、bold が true の備考だけ太字のクラスを付ける", () => {
    render(
      <NotesText
        notes={[
          { text: "損切りライン10%の値を下回っています。", color: "blue", bold: true },
          { text: "ストップ安", color: "blue" },
          { text: "ストップ高", color: "red", bold: false },
        ]}
      />,
    );
    const stopLoss = screen.getByText("損切りライン10%の値を下回っています。");
    expect(stopLoss).toHaveClass("bl", "bd");
    const lower = screen.getByText("ストップ安");
    expect(lower).toHaveClass("bl");
    expect(lower).not.toHaveClass("bd");
    const upper = screen.getByText("ストップ高");
    expect(upper).toHaveClass("r");
    expect(upper).not.toHaveClass("bd");
    expect(stopLoss.parentElement?.textContent).toContain(NOTE_SEPARATOR);
  });
});
