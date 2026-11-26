@echo off
setlocal
pushd "%~dp0.." || exit /b 1
set "LOCAL_PG_ROOT=%CD%\.tools\rds-demo"
set "ARCHIVE=output\inspections\events.csv"
set "PGHOST=127.0.0.1"
set "PGPORT=55437"
set "PGDATABASE=road_inspection"
set "PGUSER=inspection_demo"
set "PGPASSWORD="
set "PGSERVICE="
set "PGSSLMODE=disable"
set "PGPASSFILE=%LOCAL_PG_ROOT%\pgpass.conf"
set "PYTHONPATH=%CD%\.tools\rds-test-deps"
set "PGCTL=%LOCAL_PG_ROOT%\package\native\bin\pg_ctl.exe"
if not exist "%PGCTL%" (
    echo Local PostgreSQL runtime is missing. See backend\README.md for PostgreSQL setup.
    popd
    exit /b 1
)
if not exist "%ARCHIVE%" (
    echo Missing %ARCHIVE%. Put the inspection CSV and its crops directory under output\inspections.
    popd
    exit /b 1
)
set "OWN_PG="
"%PGCTL%" -D "%LOCAL_PG_ROOT%\data" status >nul 2>&1
if errorlevel 1 (
    "%PGCTL%" -D "%LOCAL_PG_ROOT%\data" -l "%LOCAL_PG_ROOT%\postgres.log" -o "-h 127.0.0.1 -p 55437" -w start || goto finish
    set "OWN_PG=1"
)
python -m backend.import_events "%ARCHIVE%"
if errorlevel 1 goto finish
echo Open http://127.0.0.1:8017/ for the inspection preview
echo Open http://127.0.0.1:8017/map/ for the inspection map
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8017
:finish
set "APP_STATUS=%ERRORLEVEL%"
if defined OWN_PG "%PGCTL%" -D "%LOCAL_PG_ROOT%\data" -m fast -w stop
popd
exit /b %APP_STATUS%
