@echo off
chcp 65001 >nul
REM 免费部署/本地服务模式：只使用 RDAP + WHOIS，不使用 GoDaddy 或其他付费 API。

set "DOMAIN_CHECKER_SOURCE=rdap"
cd /d "%~dp0"

echo ========================================
echo   域名批量查询 - 免费服务端模式
echo ========================================
echo.
echo 当前配置:
echo   数据源:    免费 RDAP / WHOIS
echo   API Key:   不需要
echo   访问地址:  http://127.0.0.1:8000/
echo.

python domain_server.py --host 127.0.0.1 --port 8000
echo.
pause
