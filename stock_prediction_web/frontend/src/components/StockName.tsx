// 銘柄名のリンク。押すと銘柄詳細を新しいタブで開く
import { Link } from "react-router";

export default function StockName({ code, name }: { code: string; name: string }) {
  return (
    <Link to={`/stocks/${encodeURIComponent(code)}`} target="_blank" rel="noopener noreferrer">
      {name || code}
    </Link>
  );
}
