# 株価予測システムの画面(stock_prediction_web)

[stock_prediction_batch](../stock_prediction_batch) の予測結果をブラウザーで見る画面。

## 主な画面

- トップ(市場の状態、日経平均)
- 買い候補・買い候補(業種分散)
- 銘柄の詳細(チャート、トレンドライン、予測)
- 保有銘柄(登録・売却、状態の確認)
- 的中率の推移・比較
- 銘柄の検索、実行履歴

## 技術スタック

- API: Python 3.15 / FastAPI(ファストエーピーアイ)/ Uvicorn(ユーヴィコーン)
- 画面: React(リアクト)19 / TypeScript / Vite(ヴィート)/ Lightweight Charts(ライトウェイトチャーツ)・Recharts(リチャーツ)
- テスト: pytest / Vitest(ヴィテスト)

API は 1 つのプロセス・1 つのポート(8080)で、`/api/...` と画面をビルドした静的ファイルの両方を返す。
DB の接続などはバッチのコード(`const`・`common`・`core`)を読み込んで使う。

## 構成

```
stock_prediction_web/
├─ api/          API(FastAPI)。routers/ に画面ごとの部品
├─ frontend/     画面(React + TypeScript)
└─ tests/        API のテスト(pytest)
```

## 準備(PowerShell)

Node.js(ノードジェイエス)v24 と Python 3.15 が入っていること。

```powershell
cd 【プログラムへのパス】\stock_prediction\stock_prediction_web
py -3.15 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
# バッチのコードを読み込めるようにする
Set-Content -Path .venv\Lib\site-packages\stock_prediction_batch.pth -Value "【プログラムへのパス】\stock_prediction\stock_prediction_batch" -Encoding ascii
cd frontend
npm ci
```

## 動かす

| すること | コマンド | URL |
| --- | --- | --- |
| 本番と同じ形 | `cd frontend` → `npm run build` → `cd ..` → `.venv\Scripts\python -m api` | http://localhost:8080/ |
| 開発 | `.venv\Scripts\python -m api --reload` と `cd frontend` → `npm run dev` | http://localhost:5173/ |

## テストと検査

| 対象 | コマンド |
| --- | --- |
| API | `.venv\Scripts\python -m pytest --cov`、`ruff check .`、`ruff format --check .`、`mypy` |
| 画面 | `cd frontend` → `npm run coverage`、`npm run lint`、`npm run typecheck` |
