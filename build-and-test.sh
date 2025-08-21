#!/bin/bash

# Build and test script for Score Server
set -e

echo "🔨 Building Score Server Docker image..."

# Check if we're in the score_server directory
if [ ! -f "requirements.txt" ]; then
    echo "❌ requirements.txt not found. Make sure you're in the score_server directory."
    exit 1
fi

# Build the Docker image
echo "📦 Building Docker image..."
docker build -t inkless-score-server:latest .

echo "✅ Docker image built successfully!"
echo ""
echo "🏷️  Image: inkless-score-server:latest"
echo "📊 Image size:"
docker images inkless-score-server:latest --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

echo ""
echo "🧪 To test locally with external database:"
echo "docker run -p 8000:8000 \\"
echo "  -e DATABASE_URL='postgresql://user:pass@host:5432/db' \\"
echo "  -e ADMIN_KEY='test_key' \\"
echo "  -e CORS_ORIGINS='*' \\"
echo "  inkless-score-server:latest"

echo ""
echo "🚀 To push to registry:"
echo "docker tag inkless-score-server:latest your-registry/inkless-score-server:latest"
echo "docker push your-registry/inkless-score-server:latest"