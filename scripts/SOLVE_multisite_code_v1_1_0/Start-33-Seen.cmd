@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0revised\run_web_enrichment.py" run --config "%~dp0inputs\sites_blablador_33_serper_v1_6_2.json" --workspace "%~dp0dossier_33_seen_serper_v1_6_2_20260925" %*
exit /b %ERRORLEVEL%
