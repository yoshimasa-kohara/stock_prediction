# stock_prediction_batch

株価予測システムのバッチ。

## 実行方法

- Python 3.15 系。仮想環境: `.venv\Scripts\activate`(PowerShell)
- ライブラリ: `pip install -r requirements-dev.txt`(本番用だけなら `requirements.txt`)
- 個別の処理: `python <処理>.py --date YYYY-MM-DD`(collect、train、predict、verify、notify、backup、cleanup)
  - 収集は期間も指定できる: `python collect.py --from YYYY-MM-DD --to YYYY-MM-DD`(ネットワークのエラーで取れなかった日は `logs\収集失敗_YYYYMMDD_HHMM.csv` に書く)
- 日次バッチ: `python daily_batch.py --date YYYY-MM-DD`(収集 → 予測 → 検証 → 通知)
- 週次バッチ: `python weekly_batch.py --date YYYY-MM-DD`(学習 → バックアップ → クリーニング)
- 期間の全処理: `python range_date_full_process.py --from ... --to ... --predict-from ...`
- 評価: `python -m tools.evaluate_model ...`
- VS Code からの実行の設定は `.vscode\launch.json`。`YYYY-MM-DD` は実際の日付に書き換えてから実行する
- 終了コード: 0 = 成功、1 = 失敗

## テストと検査

- テスト: `pytest`(カバレッジ付き: `pytest --cov`。`common/` と `core/` で 90% 以上)
- 整形と検査: `ruff format .` と `ruff check .`
- 型ヒントの検査: `mypy`
- テストは本番とは別の DB `stock_prediction_test` を使い、実際に SQL を実行する。テーブルは毎回作り直す。
  接続先は環境変数 `TEST_DB_HOST` / `TEST_DB_PORT` などで変えられる(`tests\conftest.py`)
- J-Quants API・メールの送信・yfinance は、テストでは本物を呼ばない(差し替える)
- コードを直したら、テストを足すか直して、pytest・ruff・mypy を全て通す

## フォルダ構成

- `*.py`(直下)  : 実行ファイル。引数の解釈と、`core` の呼び出しだけを行う
- `const.py`     : 数値・文字列の設定値(`import const` で読む)
- `common\`      : 共通の部品(DB 接続、ログ、引数、J-Quants の呼び出し、リクエストの上限、メール、実行履歴、営業日、銘柄コードの表示)
- `core\`        : 各処理の本体(`*_core.py`)、特徴量の計算(`features.py`)、トレンドラインの計算(`trendline.py`)、日経平均の取得(`index_collect_core.py`)、相場の状態(強い・普通・弱い)の計算と文言(`market_state_core.py`)
- `etc\db\sql\`  : `create_tables.sql`(テーブル定義。**各 CREATE TABLE の前に DROP TABLE があり、実行すると全データが消える**)
- `tests\`       : テスト。`sample_data.py` は架空のデータ
- `tools\`       : 調査・評価用の道具(本番のバッチからは呼ばない)
- `logs\`、`models\` : ログと学習済みモデル(Git の対象外)

## 書き方の決まり

- ログは `common.logger` の `get_logger` を使う。`print` は使わない
- 各処理は `run_history.run_with_history` で実行履歴(run_history テーブル)に記録する。失敗は `BatchError` で上に伝える
- 設定値はコードに直接書かず、`const.py` に置く。秘密情報は `.env` から `common.env.get_env` で読む
- 日付は日本時間(Asia/Tokyo)で扱う

## 注意点

- **`daily_batch.bat`、`weekly_batch.bat`、`sunday_shutdown.bat` は実行しない。** 終わるとすぐ PC をシャットダウンする。動作確認は `.py` を直接実行する
- リクエストの上限は `const.py` の `API_RATE_LIMIT_PER_MIN` などで設定する(`common\rate_limit.py` で使う)。J-Quants のプランの上限より少なくする
- 処理を Ctrl+C などで止めると、実行履歴が RUNNING のまま残り、次の実行が二重起動として止まる。
  Python が動いていないことを確かめてから、run_history の RUNNING の行を FAILED に更新する
- 上昇確率は、学習のときに作る補正の対応表(`core\calibration.py`)で直してから登録する。対応表がない古いモデルは補正せずに使い、WARNING を出す
- 買い候補は2つ。買い候補(`buy_rank`。上位 `BUY_CANDIDATE_COUNT` 件)と、買い候補(業種分散。`buy_rank_sector`。同じ33業種は `SECTOR_CAP_PER_SECTOR` 銘柄まで)。
  条件を満たす銘柄が足りない日は、上昇確率の条件を上位 `BUY_CANDIDATE_FALLBACK_TOP_RATE` に置き換えて選び直す
- 画面とメールの銘柄コードは `common\stock_code.py` の `display_code` で出す(5桁目が 0 なら4桁。DB は5桁のまま)
- 保有銘柄(holding)は購入ごとに1行。メールでは銘柄ごとにまとめて平均買値で表示・判定する(`core\notify_core.py` の `average_buy_price`)。
  購入ごとの行は `HoldingLot`(購入日の古い順)。保有中の合計(評価額・損益)は `holding_totals` で求め、画面の保有銘柄も同じ関数を使う。現在値がない銘柄は評価額と損益に入れない
- 備考の色: 流動性注意は緑(`COLOR_QUIET`)、「MM/DD の終値」は橙(`COLOR_CAUTION`)、デッドクロス・赤字予想・下値支持線の下抜けは青、それ以外は赤。画面(web の `api\notes.py`)もメールと同じ色にする
- ボリンジャーバンドの日数(`const.py` の `BB_DAYS`)を変えたら、必ず学習し直してから予測する
- トレンドライン(`core\trendline.py`)は、谷・山を結び、はみ出しがなく、接触回数が一番多い線を引く。
  接触3回以上の線を3%以上抜けたことを、買い候補と保有銘柄の備考に出す
