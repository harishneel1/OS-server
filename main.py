from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import users, projects, chats, files

# Create FastAPI app
app = FastAPI(
    title="Claude-like Chat API",
    description="Backend API for Claude-like chat application",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(chats.router)
app.include_router(files.router)

# Health check endpoints
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)