#!/bin/sh
set -e
MODEL="${OLLAMA_PULL_MODEL:-llama3.2}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"
echo "Waiting for Ollama at $OLLAMA_HOST ..."
i=0
while [ "$i" -lt 90 ]; do
    if ollama list >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 2
done
echo "Pulling model: $MODEL"
ollama pull "$MODEL"
echo "Ollama model ready: $MODEL"