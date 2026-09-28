@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0revised\run_web_enrichment.py" run --config "%~dp0inputs\sites_blablador.json" --workspace "%~dp0dossier_arendsee_serper_v1_6_1" %*
exit /b %ERRORLEVEL%
