#!/usr/bin/env bash
# This script triggers the agent to respond on its own after one minute.
# It writes a task file that the agent will pick up.

cat << 'EOF' > /tmp/claw_task.txt
You are EWOK, a local operator assistant. This task was placed here by the auto_respond.sh script.
Please reply with exactly: "One minute has passed. I am responding on my own as requested."
EOF

echo "Task file created at /tmp/claw_task.txt"
echo "In one minute, the agent should pick up this task and respond."

# Schedule a background job to check and respond
sleep 60

# Check if the task file still exists (meaning we should respond)
if [ -f /tmp/claw_task.txt ]; then
    echo "=== AUTO RESPONSE ==="
    cat /tmp/claw_task.txt
    echo ""
    echo "One minute has passed. I am responding on my own as requested."
    rm -f /tmp/claw_task.txt
fi