// 銘柄名のリンク。押すと銘柄詳細へ移る
import { Link } from "react-router";

export default function StockName({ code, name }: { code: string; name: string }) {
  return <Link to={`/stocks/${encodeURIComponent(code)}`}>{name || code}</Link>;
}
