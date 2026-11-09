// Vite(ヴィート)の設定。開発サーバー、ビルド、テスト(Vitest)をまとめて設定する
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 開発中に API へのリクエストを中継する先(FastAPI。api/web_const.py の PORT と同じ)
const API_SERVER = "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    // 開発サーバーのポート(Vite の初期値)
    port: 5173,
    // /api/ で始まるリクエストは FastAPI に中継する
    proxy: {
      "/api": API_SERVER,
    },
  },
  build: {
    // ビルドした静的ファイルの出力先(FastAPI が配信する。api/web_const.py の FRONTEND_DIST_DIR)
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    // describe・it・expect を import せずに使う
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // 1つのテストの最大の時間(ミリ秒)。findBy の待ち時間(src/test/setup.ts の ASYNC_TIMEOUT_MS)より長くする
    testTimeout: 15000,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/test/**", "src/**/*.test.{ts,tsx}"],
      thresholds: { lines: 90, branches: 85 },
    },
  },
});
