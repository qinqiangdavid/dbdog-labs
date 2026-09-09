@echo off
rem 可选:把工作区里的两个 skill 复制到 Claude Code 的 skills 目录,会话里说「反向取证」「正向」就能触发
setlocal
set DST=%USERPROFILE%\.claude\skills
if not exist "%DST%" mkdir "%DST%"
for %%s in (evidence-chain span-graph) do (
  if exist "%DST%\%%s" rmdir /s /q "%DST%\%%s"
  xcopy /e /i /q "%~dp0skills\%%s" "%DST%\%%s" >nul && echo 已安装 %%s → %DST%\%%s
)
endlocal
