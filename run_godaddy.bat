@echo off
chcp 65001 >nul
REM 域名批量查询 - GoDaddy 一键启动脚本
REM 双击此文件即可运行，Key 已硬编码（仅本机使用，勿外传）

set "GODADDY_KEY=3mM44YwfTEm8NC_Q8ADGagoJ9k5Va2nif6u3w"
set "GODADDY_SECRET=JCo8UqNjXE9RcCBY27xamx"
set "GODADDY_BASE=https://api.ote-godaddy.com"

cd /d "%~dp0"

echo ========================================
echo   域名批量查询 - GoDaddy 模式
echo ========================================
echo.
echo 当前配置:
echo   数据源:    GoDaddy OTE 沙箱 (不扣费)
echo   Key:       %GODADDY_KEY:~0,8%***
echo.
echo 可用参数示例:
echo   --source godaddy        数据源 (rdap/godaddy/auto)
echo   --prefix ai             域名前缀
echo   --length 5              域名主体总长度
echo   --suffix com cn         顶级后缀
echo   --letters all           中间字母范围 (all/a-m/n-z 等)
echo   --workers 30            并发数
echo   --output results.csv    输出文件
echo.
echo 默认查询: ai+3字母+空+.com (2x2x2x2=8 个域名, a~b 字母)
echo.

"C:\Users\mingren\.workbuddy\binaries\python\versions\3.13.12\python.exe" domain_checker.py --source godaddy %*
echo.
pause
