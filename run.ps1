Write-Host "=== Starting Enterprise Contract Compliance Analyzer ===" -ForegroundColor Cyan
if (!(Test-Path ".venv")) {
    # 'python' may be the Microsoft Store stub on Windows, so prefer the 'py' launcher
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -m venv .venv
    } else {
        python -m venv .venv
    }
}

# Call modules through the venv's python (python -m ...) instead of pip.exe / streamlit.exe.
# Locked-down machines (AppLocker / Application Control) often block those generated launcher .exe files.
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: dependency installation failed (see messages above). Not starting the app." -ForegroundColor Red
    exit 1
}

Write-Host "Open http://localhost:8501 once you see 'You can now view your Streamlit app'" -ForegroundColor Yellow
# headless skips Streamlit's first-run email prompt, which otherwise blocks the server from starting
& $venvPython -m streamlit run app.py --server.headless true --server.port 8501
