// 全画面に共通の枠: 上のメニュー、バッチ実行中の表示、エラー、画面の中身
import { Outlet } from "react-router";

import { useStatus } from "../context/contexts";
import ErrorMessage from "./ErrorMessage";
import Header from "./Header";
import RunningBanner from "./RunningBanner";

export default function Layout() {
  const { status, error } = useStatus();
  return (
    <>
      <Header />
      {status && <RunningBanner running={status.running} />}
      <main>
        {error && <ErrorMessage message={error} />}
        <Outlet />
      </main>
    </>
  );
}
