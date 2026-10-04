@echo off
REM Upload CiHuang to PyPI (Windows)

echo === CiHuang PyPI Upload Script ===
echo.

where python >nul 2>nul || (echo Python required but not installed. & exit /b 1)
where twine >nul 2>nul || (echo Twine not installed. Run: pip install twine & exit /b 1)
where build >nul 2>nul || (echo Build not installed. Run: pip install build & exit /b 1)

echo Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
echo.

echo Building package...
python -m build
echo.

echo Checking package...
twine check dist\*
echo.

echo Uploading to PyPI...
twine upload dist\*

echo.
echo === Upload Complete ===
