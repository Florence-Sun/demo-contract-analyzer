Write-Host "=== Starting Enterprise Contract Compliance Analyzer ===" -ForegroundColor Cyan
if (!(Test-Path ".venv")) {
    # 'python' may be the Microsoft Store stub on Windows, so prefer the 'py' launcher
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -m venv .venv
    } else {
        python -m venv .venv
    }
}
& ".\.venv\Scripts\Activate.ps1"
pip install -r requirements.txt
Write-Host "Open http://localhost:8501 once you see 'You can now view your Streamlit app'" -ForegroundColor Yellow
# headless skips Streamlit's first-run email prompt, which otherwise blocks the server from starting
streamlit run app.py --server.headless true --server.port 8501
