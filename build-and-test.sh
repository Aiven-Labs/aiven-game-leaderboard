#!/bin/bash

# Build and test script for Score Server
set -e

echo "🔨 Building Score Server Docker image..."

# Check if we're in the repo root
if [ ! -f "requirements.txt" ]; then
    echo "❌ requirements.txt not found. Make sure you're in the repo root."
    exit 1
fi

# Build the Docker image
echo "📦 Building Docker image..."
docker build -t aiven-game-leaderboard:latest .

echo "✅ Docker image built successfully!"
echo ""
echo "🏷️  Image: aiven-game-leaderboard:latest"
echo "📊 Image size:"
docker images aiven-game-leaderboard:latest --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

echo ""
echo "🧪 To test locally with external database:"
echo "docker run -p 8000:8000 \\"
echo "  -e DATABASE_URL='postgresql://user:pass@host:5432/db' \\"
echo "  -e ADMIN_KEY='test_key' \\"
echo "  -e CORS_ORIGINS='*' \\"
echo "  aiven-game-leaderboard:latest"

echo ""
echo "🚀 To push to registry:"
echo "docker tag aiven-game-leaderboard:latest your-registry/aiven-game-leaderboard:latest"
echo "docker push your-registry/aiven-game-leaderboard:latest"