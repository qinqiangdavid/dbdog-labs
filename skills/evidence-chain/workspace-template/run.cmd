@echo off
rem 反向取证工作区入口:自检 → 顺序跑 batch.md 全部用例。用法:run.cmd [--force] [--only ID1,ID2] [--dry-run]
setlocal
set WS=%~dp0
set BATCH=%WS%skills\evidence-chain\scripts\batch.py
if not exist "%BATCH%" ( echo 找不到 %BATCH%,工作区不完整 & exit /b 2 )
if not exist "%WS%logs" mkdir "%WS%logs"
for /f "tokens=1-4 delims=/:. " %%a in ("%date% %time%") do set STAMP=%%a%%b%%c-%%d
set LOG=%WS%logs\run-%STAMP%.log
echo [%date% %time%] 自检 >> "%LOG%"
python "%BATCH%" "%WS%batch.md" --check >> "%LOG%" 2>&1
python "%BATCH%" "%WS%batch.md" --check
if errorlevel 1 ( echo 自检未过,按上面的提示修好再跑(日志 %LOG%) & exit /b 1 )
echo [%date% %time%] 开始跑 >> "%LOG%"
python "%BATCH%" "%WS%batch.md" %* >> "%LOG%" 2>&1
set RC=%errorlevel%
echo [%date% %time%] 结束 rc=%RC% >> "%LOG%"
type "%WS%out\batch-summary.md" 2>nul
if %RC% neq 0 echo 有用例失败,看 %LOG% 和 out\^<单号^>\reverse\work\claude.err
endlocal & exit /b %RC%
