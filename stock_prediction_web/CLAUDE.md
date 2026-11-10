# 株価予測システムの画面(stock_prediction_web)

予測の詳細、買い候補と実際の株価、保有銘柄の状態、的中率の推移を見る画面。保有銘柄の登録・売却もここで行う。

## 構成

- `api\` : API(FastAPI)。`python -m api` で起動し、ポート 8080 で `/api/...` と画面の静的ファイル(`frontend\dist`)を返す
  - `api\web_const.py` : 画面の設定値。画面(React)には数値を直接書かず、`/api/settings` で渡す
  - `api\routers\` : 画面ごとの部品(パスの登録と、DB の読み書き)
  - `api\errors.py` : エラーは HTTP の状態コードと `{"detail": "..."}` で返す
  - `api\logger.py` : ログ(`logs\web_YYYYMMDD.log`、30日分)。**API の関数には必ず `@log_api` を付ける**(`@router.get(...)` の下)
- `frontend\` : 画面(React + TypeScript、Vite)。`src\pages\` 画面、`src\components\` 部品、`src\lib\format.ts` 書式
- 買い候補の画面は、予測日の期間(開始日・終了日)で `/api/candidates/range` を呼び、予測日ごとに表示する(新しい日が上)。
  期間の初期値は、予測がある日を新しい順に `CANDIDATES_DEFAULT_DAYS` 日分(`src\lib\candidateRange.ts`)。1回に表示できるのは `CANDIDATES_RANGE_MAX_MONTHS` か月まで。
  日経平均のブロック(`src\components\LatestMarketBlock.tsx`)は、期間やタブに関係なく最新の日のものを出す
- `tests\` : API のテスト(pytest)。画面のテストは `frontend\src` の `*.test.ts(x)`(Vitest)

## バッチのコードの読み込み

- `.venv\Lib\site-packages\stock_prediction_batch.pth` で、バッチのフォルダを読み込む。
  `const`、`common`、`core`、`tools` はバッチのもの。**web 側にこの名前のフォルダやファイルを作らない**(`tests` には `__init__.py` を置かない)
- DB の接続はバッチの `common.db.connect`(同じ `.env`)。画面が書き込むのは holding(保有銘柄)だけ
- 備考(流動性注意、クロス、トレンドラインの抜けなど)はバッチの core の関数で計算し、メールと同じ結果にする(色もメールと同じ)
- 保有銘柄の合計(評価額・損益)は、メールと同じ `core.notify_core.holding_totals` で求める

## 書き方の決まり

- コメントは日本語。Python の型ヒントは省略しない(mypy の strict)。TypeScript も strict
- 設定値はコードに直接書かず、`api\web_const.py` に置く(バッチの値はバッチの `const.py` を読む)
- 数字の書式: 株価は小数1桁・3桁区切り、確率と率は % で小数1桁、率の差は符号付き
- 画面に出す銘柄コードは `displayCode`(5桁目が 0 なら4桁。API は5桁のまま返す)

## 確認のしかた(全て通す)

- API: `.venv\Scripts\python -m pytest --cov`(api/ で 90% 以上)、`ruff check .`、`ruff format --check .`、`mypy`
- 画面(`frontend` で): `npm test`(または `npm run coverage`)、`npm run lint`、`npm run build`
- テストの DB はバッチと同じ `stock_prediction_test`(テストのたびに全てのテーブルを作り直す)
