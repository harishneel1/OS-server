from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from pydantic import BaseModel
from typing import Optional

class ChatCreate(BaseModel):
    title: str = "New Chat"
    project_id: Optional[str] = None
    clerk_id: str

class ChatUpdate(BaseModel):
    title: str = None
    messages: list = None
    updated_at: str = None

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    clerk_id: str

# Load environment variables
load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("Missing Supabase credentials in environment variables")

supabase: Client = create_client(supabase_url, supabase_key)

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

@app.post("/api/users")
async def create_user(user_data: dict):
    try:
        # Extract data from request
        clerk_id = user_data.get("clerk_id")
        
        if not clerk_id:
            raise HTTPException(status_code=400, detail="clerk_id is required")
        
        # Insert new user into database
        result = supabase.table('users').insert({
            "clerk_id": clerk_id
        }).execute()

        print(result)
        
        return {
            "message": "User created successfully",
            "user": result.data[0]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")


@app.post("/api/webhooks/clerk/user-created")
async def clerk_webhook(webhook_data: dict):
    try:
        # Get the event type and user data from Clerk
        event_type = webhook_data.get("type")
        
        if event_type == "user.created":
            user_data = webhook_data.get("data", {})
            clerk_id = user_data.get("id")
            
            if not clerk_id:
                raise HTTPException(status_code=400, detail="No user ID in webhook")
            
            # Create user in our database
            result = supabase.table('users').insert({
                "clerk_id": clerk_id
            }).execute()
            
            return {"message": "User created successfully", "clerk_id": clerk_id}
        
        # For other event types, just acknowledge
        return {"message": "Webhook received", "type": event_type}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

@app.get("/api/db-test")
async def test_database():
    try:
        # Simple query to test connection
        result = supabase.table('users').select("*").execute()
        return {
            "message": "Database connection successful!",
            "table": "users",
            "status": "connected", 
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")




# PROJECTS-RELATED APIS 

@app.get("/api/projects")
async def get_projects(clerk_id: str):
    try:
        result = supabase.table('projects').select('*').eq('clerk_id', clerk_id).execute()
        
        return result.data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get projects: {str(e)}")


@app.post("/api/projects")
async def create_project(project: ProjectCreate):
    try:
        # Insert new project into database
        result = supabase.table('projects').insert({
            "name": project.name,
            "description": project.description,
            "clerk_id": project.clerk_id
        }).execute()
        
        return result.data[0] 
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")

@app.get("/api/projects/{project_id}/chats")
async def get_project_chats(project_id: str):
    try:
        # First verify the project exists
        project_result = supabase.table('projects').select('id').eq('id', project_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Get chats for this project, ordered by most recent activity
        result = supabase.table('chats').select('*').eq('project_id', project_id).order('updated_at', desc=True).execute()
        
        return result.data
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get project chats: {str(e)}")

@app.get("/api/chats")
async def get_chats(clerk_id: str):
    try:
        # Query chats table for this user, ordered by most recent
        result = supabase.table('chats').select('*').eq('clerk_id', clerk_id).order('updated_at', desc=True).execute()
        
        return result.data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get chats: {str(e)}")

@app.get("/api/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str):
    try:
        result = supabase.table('messages').select('*').eq('chat_id', chat_id).order('created_at', desc=False).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get messages: {str(e)}")

@app.post("/api/chats")
async def create_chat(chat: ChatCreate):
    try:
        
        result = supabase.table('chats').insert({
            "title": chat.title,
            "project_id": chat.project_id,
            "clerk_id": chat.clerk_id
        }).execute()
                
        return result.data[0]  
        
    except Exception as e:
        print(f"❌ Error creating chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create chat: {str(e)}")

@app.put("/api/chats/{chat_id}")
async def update_chat(chat_id: str, chat_update: ChatUpdate):
    try:
        update_data = {}
        if chat_update.title:
            update_data["title"] = chat_update.title
        if chat_update.updated_at:
            update_data["updated_at"] = chat_update.updated_at
            
        result = supabase.table('chats').update(update_data).eq('id', chat_id).execute()
        
        return result.data[0]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update chat: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)