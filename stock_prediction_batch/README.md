# 株価予測システムのバッチ(stock_prediction_batch)

東証の上場銘柄の株価を毎日集め、機械学習で「N営業日後に上がる確率」と「予測株価」を出し、買い候補をメールで知らせるバッチ。
画面は [stock_prediction_web](../stock_prediction_web) で見る。

## 主な機能

- **収集**: J-Quants(ジェイクオンツ)API から日足・銘柄情報・財務情報を取得(リクエストの上限を守って呼ぶ)
- **学習**: LightGBM(ライトジービーエム)で、予測日数(5・10・20・60営業日)ごとにモデルを作る
- **予測**: 上昇確率(補正済み)と予測株価を出し、買い候補と買い候補(業種分散)を選ぶ
- **検証**: 過去の予測と実際の株価を比べ、的中率を記録する
- **通知**: 買い候補と保有銘柄の状態をメールで送る(トレンドラインの抜けなどを備考に付ける)
- **バックアップ・クリーニング**: DB のバックアップと古いデータの削除

日次バッチは「収集 → 予測 → 検証 → 通知」、週次バッチは「学習 → バックアップ → クリーニング」。

## 技術スタック

Python 3.15 / PostgreSQL(psycopg)/ pandas / LightGBM / pytest / ruff / mypy

## フォルダ構成

```
stock_prediction_batch/
├─ *.py            実行ファイル(collect、train、predict、verify、notify、backup、cleanup、日次・週次バッチ)
├─ const.py        設定値
├─ common/         共通の部品(DB、ログ、引数、J-Quants の呼び出し、リクエストの上限、メール、実行履歴、営業日)
├─ core/           各処理の本体、特徴量の計算、トレンドラインの計算
├─ tools/          評価・調査用の道具(モデルの評価など)
├─ etc/db/sql/     テーブル定義(create_tables.sql)
└─ tests/          テスト(pytest)
```

## 準備

1. Python 3.15 と PostgreSQL を用意する
2. 仮想環境を作り、ライブラリを入れる: `pip install -r requirements-dev.txt`
3. `etc/db/sql/create_tables.sql` でテーブルを作る(**実行すると既存のデータは消える**)
4. `.env.example` をコピーして `.env` を作り、J-Quants の API キー、メール、DB の接続先を書く(`.env` は Git の対象外)

## 実行

```powershell
python daily_batch.py --date YYYY-MM-DD      # 日次バッチ
python weekly_batch.py --date YYYY-MM-DD     # 週次バッチ
python collect.py --from YYYY-MM-DD --to YYYY-MM-DD   # 期間を指定して収集
```

個別の処理も `python <処理>.py --date YYYY-MM-DD` で動かせる。終了コードは 0 が成功、1 が失敗。

> `daily_batch.bat`・`weekly_batch.bat`・`sunday_shutdown.bat` は、終わると PC をシャットダウンする(タスクスケジューラ用)。

## テストと検査

```powershell
pytest --cov      # common/ と core/ で 90% 以上
ruff check .
ruff format --check .
mypy
```

テストは本番とは別の DB(`stock_prediction_test`)を使う。J-Quants API・メール・yfinance は本物を呼ばない。

## 注意

- 株価の予測は投資の判断を保証するものではない
- J-Quants のデータの利用は、J-Quants の利用規約に従う
