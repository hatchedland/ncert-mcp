#!/bin/zsh
# Watchdog: monitors pipeline, restarts if stuck (no progress in 5 min)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="$SCRIPT_DIR/../data/content.db"
PYTHON="$SCRIPT_DIR/../.venv313/bin/python"
LOG="/tmp/watchdog.log"
CHECK_INTERVAL=300  # 5 minutes
LAST_COUNT=0

get_count() {
  $PYTHON -c "import sqlite3; c=sqlite3.connect('$DB'); print(c.execute('SELECT COUNT(*) FROM content_chunks').fetchone()[0]); c.close()" 2>/dev/null
}

start_pipeline() {
  pkill -f "pipeline.py" 2>/dev/null
  sleep 2
  cd "$SCRIPT_DIR"
  nohup $PYTHON pipeline.py >> /tmp/pipeline_run.log 2>&1 &
  echo "[$(date)] Pipeline started (PID $!)" | tee -a $LOG
}

echo "[$(date)] Watchdog started" | tee $LOG

# Start pipeline if not running
if ! pgrep -f "pipeline.py" > /dev/null; then
  start_pipeline
fi

while true; do
  sleep $CHECK_INTERVAL

  CURRENT_COUNT=$(get_count)
  IS_RUNNING=$(pgrep -f "pipeline.py" > /dev/null && echo "yes" || echo "no")

  echo "[$(date)] Chunks: $CURRENT_COUNT (was $LAST_COUNT) | Running: $IS_RUNNING" | tee -a $LOG

  if [ "$IS_RUNNING" = "no" ] || [ "$CURRENT_COUNT" = "$LAST_COUNT" ]; then
    echo "[$(date)] Stuck or dead — restarting..." | tee -a $LOG
    start_pipeline
  fi

  LAST_COUNT=$CURRENT_COUNT
done
