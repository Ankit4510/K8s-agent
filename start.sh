#!/bin/bash

# K8s Agent Startup Script
# Starts Redis and the FastAPI server

set -e

cd "$(dirname "$0")"

echo "🚀 Starting K8s Agent..."

# Start Redis (if not already running)
if ! redis-cli ping > /dev/null 2>&1; then
    echo "📦 Starting Redis..."
    brew services start redis 2>/dev/null || redis-server --daemonize yes
    sleep 2
    if redis-cli ping > /dev/null 2>&1; then
        echo "✅ Redis is running"
    else
        echo "❌ Failed to start Redis. Please install Redis: brew install redis"
        exit 1
    fi
else
    echo "✅ Redis is already running"
fi

# Start FastAPI server
echo "🌐 Starting FastAPI server..."
uvicorn main:app --reload --host 0.0.0.0 --port 8000
