// 画面の URL が見つからないとき
import { Link } from "react-router";
import { usePageTitle } from "../lib/usePageTitle";

export default function NotFound() {
  usePageTitle("画面が見つかりません");
  return (
    <>
      <h1>画面が見つかりません</h1>
      <div className="panel">
        <p className="info">
          URL が正しいか確認してください。<Link to="/">トップへ戻る</Link>
        </p>
      </div>
    </>
  );
}
