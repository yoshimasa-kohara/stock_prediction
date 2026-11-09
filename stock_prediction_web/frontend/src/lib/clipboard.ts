// 文字をクリップボードにコピーする(精度の比較の Markdown のコピー)。
// Tailscale から http で開くと、ブラウザーの navigator.clipboard が使えない(https か localhost のときだけ使える)ので、
// そのときは選んだ文字をコピーする古い方法(execCommand)を使う。どちらもできなければ false を返す

/** 古い方法: 見えないテキストの枠に入れて選び、コピーする */
function copyByCommand(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(area);
  }
}

/** コピーできたら true */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 使えなければ古い方法を試す
    }
  }
  return copyByCommand(text);
}
