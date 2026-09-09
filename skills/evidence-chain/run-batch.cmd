@echo off
rem evidence-chain 批次一键入口(Windows):先自检,通过才跑。用法:run-batch.cmd D:\pair\batch.md [--force] [--only ID1,ID2]
setlocal
set HERE=%~dp0
if "%~1"=="" ( echo 用法: run-batch.cmd 批次文件.md [--force] [--only ID1,ID2] & exit /b 2 )
python "%HERE%scripts\batch.py" "%~1" --check || ( echo 自检未过,按上面的提示修好再跑 & exit /b 1 )
python "%HERE%scripts\batch.py" %*
endlocal
