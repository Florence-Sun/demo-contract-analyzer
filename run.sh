#!/usr/bin/env bash
set -e
echo "=== Starting Enterprise Contract Compliance Analyzer ==="
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# Run modules through the venv's python (not pip / streamlit launchers) for locked-down machines
.venv/bin/python -m pip install -r requirements.txt
echo "Open http://localhost:8501 once you see 'You can now view your Streamlit app'"
# headless skips Streamlit's first-run email prompt, which otherwise blocks the server from starting
.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8501
