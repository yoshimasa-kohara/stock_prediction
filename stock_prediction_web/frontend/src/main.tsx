// 画面の入り口。設定値を読んでから、画面を表示する
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import App from "./App";
import SettingsProvider from "./context/SettingsProvider";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html に id=root の要素がありません");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <SettingsProvider>
        <App />
      </SettingsProvider>
    </BrowserRouter>
  </StrictMode>,
);
