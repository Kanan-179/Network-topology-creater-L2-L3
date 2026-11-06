@echo off
title Aztelekom L3 Topology - Python Kitabxanalarinin Qurasdirilmasi
color 0A

echo ================================================================
echo   Aztelekom L3 Topology Weather Map (with Bandwidth)
echo   Python Kitabxanalarinin Avtomatik Qurasdirilmasi
echo ================================================================
echo.

:: Python-un sistemdə mövcudluğunu yoxla
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [XETA] Komputerinizde Python tapilmadi!
    echo Zehmet olmasa evvelce Python 3.8+ qurasdirin ve qurasdirma zamani
    echo "Add Python to PATH" secimini mutleq isareleyin.
    echo Yukleme linki: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [1/2] Komputerinizdeki Python versiyasi:
python --version
echo.

:: Pip-i yenilə və requirements.txt daxilindəki kitabxanaları quraşdır
echo [2/2] "requirements.txt" faylindan kitabxanalar yuklenir...
echo Komanda icra olunur: python -m pip install -r requirements.txt
echo.
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"

if %errorlevel% equ 0 (
    echo.
    echo ================================================================
    echo [UGURLU] Butun lazimi kitabxanalar (netmiko, matplotlib, pillow)
    echo          ugurla qurasdirildi!
    echo.
    echo Proqrami ise salmaq ucun asagidaki komandani yaza bilersiniz:
    echo   python l3_topology_with_draw.io-and-png_with_BW.py
    echo ================================================================
) else (
    color 0C
    echo.
    echo ================================================================
    echo [XETA] Qurasdirma zamani xeta bas verdi.
    echo Zehmet olmasa internet baglantinizi ve VPN parametrlərinizi yoxlayin.
    echo ================================================================
)

echo.
pause
