@echo off
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0revised\run_web_enrichment.py" run --config "%~dp0inputs\sites_blablador_33_scientific.json" --workspace "%~dp0dossier_blablador_alle_33_v1_5"
set "solve_exit=%ERRORLEVEL%"
popd
exit /b %solve_exit%
