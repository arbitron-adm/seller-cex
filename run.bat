@echo off

title Приложение для продажи токенов
cd /d "%~dp0"

echo 🚀 Запуск приложения для продажи токенов...
echo.

if not exist seller_env\Scripts\activate.bat (
    echo ❌ Виртуальное окружение не найдено!
    echo Запустите сначала install.bat
    echo.
    pause
    exit /b 1
)

call seller_env\Scripts\activate.bat

if not exist main.py (
    echo ❌ Файл main.py не найден!
    echo Убедитесь, что все файлы на месте
    echo.
    pause
    exit /b 1
)

if not exist api_keys.json (
    echo ❌ Файл api_keys.json не найден!
    echo Создайте файл с API ключами или запустите install.bat
    echo.
    pause
    exit /b 1
)

echo ✅ Все проверки пройдены, запускаем программу...
echo.

python main.py

if errorlevel 1 (
    echo.
    echo ❌ Программа завершилась с ошибкой!
    echo Проверьте:
    echo - Правильность API ключей в api_keys.json
    echo - Подключение к интернету
    echo - Не запущена ли уже другая копия программы
    echo.