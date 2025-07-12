from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create FastAPI app
app = FastAPI(
    title="Claude-like Chat API",
    description="Backend API for Claude-like chat application",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Your Next.js frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/")
async def root():
    return {"message": "Claude-like Chat API is running!"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "claude-like-chat-api",
        "version": "1.0.0"
    }

# Test endpoint to verify frontend connectivity
@app.get("/api/test")
async def test_endpoint():
    return {
        "message": "Frontend-Backend connection successful!",
        "timestamp": "2024-01-01T00:00:00Z"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)