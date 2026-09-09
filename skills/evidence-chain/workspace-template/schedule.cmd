@echo off
rem 注册每天 02:00 自动跑一次(幂等:已完成的用例跳过,新加的用例被捡起来)。删除:schtasks /delete /tn evidence-chain-nightly /f
schtasks /create /tn evidence-chain-nightly /tr "\"%~dp0run.cmd\"" /sc daily /st 02:00 /f
if errorlevel 1 ( echo 注册失败,可能要管理员权限 & exit /b 1 )
echo 已注册计划任务 evidence-chain-nightly,每天 02:00 跑 %~dp0run.cmd
