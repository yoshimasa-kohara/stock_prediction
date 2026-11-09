@echo off
rem 日次バッチを実行し、終わったらPCをシャットダウンする(ログオン中のユーザーがいれば、しない)
rem タスクスケジューラから、月～金曜の 19:00 に起動する
rem Python の終了コード(成功・失敗)にかかわらず、終了時にシャットダウンを判定する。失敗メールは Python が送る
cd /d 【プログラムへのパス】\stock_prediction\stock_prediction_batch
set PYTHONUTF8=1
.venv\Scripts\python.exe daily_batch.py
call :shutdown_if_no_user
exit /b 0

rem ===== 終了時のシャットダウンの判定(daily_batch.bat、weekly_batch.bat、sunday_shutdown.bat で同じ書き方) =====
:shutdown_if_no_user
rem コンソールにログオン中のユーザーがいれば(画面ロック中も含む)シャットダウンしない。判定はこの1回だけ
rem 終了コード 1=ログオン中、0=ログオンなし。これ以外(powershell の失敗)は、今までどおりシャットダウンする
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "if ((Get-CimInstance Win32_ComputerSystem).UserName) { exit 1 } else { exit 0 }"
rem if の中で %ERRORLEVEL% を使うと遅延展開の問題が出るため、if の外ですぐ変数に取る
set "JUDGE_RC=%ERRORLEVEL%"
if "%JUDGE_RC%"=="1" goto :logged_on
if "%JUDGE_RC%"=="0" echo [%date% %time%] ログオン中のユーザーはいません。シャットダウンします
if not "%JUDGE_RC%"=="0" echo [%date% %time%] ログオン中かどうかの判定に失敗しました(終了コード %JUDGE_RC%)。今までどおりシャットダウンします
goto :do_shutdown

:logged_on
set "LOGON_USER="
for /f "usebackq delims=" %%u in (`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "(Get-CimInstance Win32_ComputerSystem).UserName"`) do set "LOGON_USER=%%u"
echo [%date% %time%] ユーザー %LOGON_USER% がログオン中のため、シャットダウンしません
exit /b 0

:do_shutdown
rem 同じフォルダの sunday_shutdown.bat を呼ばないよう、フルパスで shutdown.exe を実行する
%SystemRoot%\System32\shutdown.exe /s /t 0
