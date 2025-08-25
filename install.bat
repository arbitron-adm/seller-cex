@echo off

echo 🚀 Установка приложения для продажи токенов...
echo ================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден!
    echo Пожалуйста, установите Python с официального сайта:
    echo https://www.python.org/downloads/
    echo Обязательно отметьте "Add Python to PATH" при установке
    pause
    exit /b 1
)

echo ✅ Python найден
python --version

pip --version >nul 2>&1
if errorlevel 1 (
    echo ❌ pip не найден! Переустановите Python с официального сайта
    pause
    exit /b 1
)

echo ✅ pip найден

echo 📦 Создаем виртуальное окружение...
python -m venv seller_env

echo 🔧 Активируем виртуальное окружение...
call seller_env\Scripts\activate.bat

echo ⬆️ Обновляем pip...
python -m pip install --upgrade pip

echo 📚 Устанавливаем зависимости...
if exist requirements.txt (
    pip install -r requirements.txt
) else (
    echo ❌ Файл requirements.txt не найден!
    echo Устанавливаем базовые зависимости...
    pip install PyQt6 ccxt qasync aiohttp-socks rich certifi
)

echo 🔍 Проверяем установку...
python -c "import PyQt6; print('✅ PyQt6 установлен успешно')" 2>nul
if errorlevel 1 (
    echo ❌ Ошибка установки PyQt6! Попробуйте переустановить Python
    pause
    exit /b 1
)

echo 📝 Создаем скрипт запуска...
echo @echo off > run.bat
echo cd /d "%%~dp0" >> run.bat
echo call seller_env\Scripts\activate.bat >> run.bat
echo python main.py >> run.bat
echo pause >> run.bat

echo 🔍 Проверяем необходимые файлы...
if not exist main.py (
    echo ❌ Файл main.py не найден!
    echo Убедитесь, что все файлы скопированы в папку
    pause
    exit /b 1
)

echo.
echo 🎉 УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!
echo ================================
echo.
echo 📋 Что делать дальше:
echo 1. Отредактируйте файл api_keys.json - добавьте свои API ключи
echo 2. Запустите программу двойным кликом по файлу run.bat
echo.
echo ⚠️ ВАЖНО: Никому не показывайте содержимое api_keys.json!
echo.
echo 🚀 Для запуска программы