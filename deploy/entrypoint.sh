#!/bin/sh
set -e
if [ "$SERVICE" = "app" ]; then
  exec streamlit run apps/trader/app.py --server.port "${PORT}" --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
else
  exec uvicorn edgeforge.api.app:app_factory --factory --host 0.0.0.0 --port "${PORT}"
fi
