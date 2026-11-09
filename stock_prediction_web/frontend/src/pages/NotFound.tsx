// 画面の URL が見つからないとき
import { Link } from "react-router";

export default function NotFound() {
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
