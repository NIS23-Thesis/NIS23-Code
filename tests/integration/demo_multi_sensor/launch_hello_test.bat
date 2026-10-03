```bat
@echo off
setlocal

cd /d "%~dp0..\..\.."
set "PYTHONPATH=%CD%"

echo ================================================
echo FLAME HELLO BROADCAST TEST
echo ================================================
echo.
echo Starting Cluster Head...
echo.

start "FLAME Cluster Head" cmd /k "cd /d %CD% && set PYTHONPATH=%CD% && python tests\integration\demo_multi_sensor\cluster_head.py"

timeout /t 2 /nobreak >nul

echo Starting Sensor 1...
echo.

start "FLAME Sensor 1" cmd /k "cd /d %CD% && set PYTHONPATH=%CD% && python tests\integration\demo_multi_sensor\sensor_1.py"

echo.
echo ================================================
echo HELLO broadcast test started.
echo ================================================
echo.
echo The Cluster Head broadcasts HELLO every 5 seconds.
echo Sensor 1 listens for the HELLO messages.
echo.
pause
```
