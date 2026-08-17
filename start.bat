@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%

echo.
echo ============================================
echo  物理实验智能排课 - 重启启动脚本
echo ============================================
echo.

echo [1/4] 停止旧的后端/前端进程（仅按端口清理，不影响其他程序）...
for %%P in (8000 8001 5173) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r ":%%P[ ]" ^| findstr LISTENING') do (
        echo   正在结束监听 %%P 端口的进程 PID %%a
        taskkill /f /pid %%a >nul 2>&1
    )
)
timeout /t 2 /nobreak >nul

echo.
echo [2/4] 启动后端（项目 venv，端口 8000，与前端代理一致）...
cd /d "%ROOT%\backend"
start "物理实验-后端" .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --backlog 8192
echo   后端日志请查看新打开的窗口；注意 --reload 在本机不生效，改后端代码后需重新运行本脚本。

echo.
echo [3/4] 启动前端（Vite，端口 5173）...
cd /d "%ROOT%\frontend"
start "物理实验-前端" cmd /c "npm run dev"

echo.
echo [4/4] 等待后端就绪后初始化库存数据...
timeout /t 10 /nobreak >nul
cd /d "%ROOT%\backend"
.venv\Scripts\python.exe -m scripts.init_stock

echo.
echo ============================================
echo  完成：后端 http://127.0.0.1:8000 / 前端 http://localhost:5173
echo  注意：Redis 运行在虚拟机 192.168.100.128:6379，本脚本不管理，请确保虚拟机已开机。
echo ============================================
pause
