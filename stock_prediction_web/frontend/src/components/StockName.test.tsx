import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import StockName from "./StockName";

describe("StockName", () => {
  it("銘柄詳細を新しいタブで開くリンクにする", () => {
    render(
      <MemoryRouter>
        <StockName code="11110" name="サンプル銀行A" />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "サンプル銀行A" });
    expect(link).toHaveAttribute("href", "/stocks/11110");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("銘柄名がなければ銘柄コードを出す", () => {
    render(
      <MemoryRouter>
        <StockName code="11110" name="" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "11110" })).toBeInTheDocument();
  });
});
