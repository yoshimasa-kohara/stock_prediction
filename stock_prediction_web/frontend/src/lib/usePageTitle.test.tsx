import { render } from "@testing-library/react";

import { pageTitle, usePageTitle } from "./usePageTitle";

function Sample({ name }: { name: string }) {
  usePageTitle(name);
  return null;
}

describe("usePageTitle", () => {
  it("画面名から「株価予測-画面名」を作る(ハイフンの前後に空白なし)", () => {
    expect(pageTitle("トップ")).toBe("株価予測-トップ");
  });

  it("表示している間、タブの title を「株価予測-画面名」にし、画面名が変われば変える", () => {
    const { rerender } = render(<Sample name="トップ" />);
    expect(document.title).toBe("株価予測-トップ");
    rerender(<Sample name="銘柄詳細" />);
    expect(document.title).toBe("株価予測-銘柄詳細");
  });
});
