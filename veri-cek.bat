@echo off
rem Beta Social: veriyi ceker ve gunluk raporu olusturur.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
beta-social daily
echo.
echo Bitti. Simdi Claude ile sohbete "analiz et" yazabilirsin.
pause
