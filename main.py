from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from pydantic import BaseModel
from typing import Optional

# Pydantic Models
class UserCreate(BaseModel):
    clerk_id: str

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    clerk_id: str

class ChatCreate(BaseModel):
    title: str
    project_id: Optional[str] = None
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
    allow_origins=["http://localhost:3000"],
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

# User endpoints
@app.post("/api/users")
async def create_user(user: UserCreate):
    try:
        # Insert new user into database
        result = supabase.table('users').insert({
            "clerk_id": user.clerk_id
        }).execute()
        
        return {
            "message": "User created successfully",
            "data": result.data[0]
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
            
            return {
                "message": "User created successfully",
                "data": result.data[0]
            }
        
        # For other event types, just acknowledge
        return {"message": "Webhook received", "type": event_type}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

# Projects endpoints
@app.get("/api/projects")
async def get_projects(clerk_id: str):
    try:
        result = supabase.table('projects').select('*').eq('clerk_id', clerk_id).execute()
        
        return {
            "message": "Projects retrieved successfully",
            "data": result.data
        }
        
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
        
        return {
            "message": "Project created successfully",
            "data": result.data[0]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")

@app.get("/api/projects/{project_id}")
async def get_project(project_id: str, clerk_id: str):
    try:
        result = supabase.table('projects').select('*').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        return {
            "message": "Project retrieved successfully",
            "data": result.data[0]
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get project: {str(e)}")

@app.get("/api/projects/{project_id}/chats")
async def get_project_chats(project_id: str):
    try:
        # First verify the project exists
        project_result = supabase.table('projects').select('id').eq('id', project_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Get chats for this project, ordered by most recent activity
        result = supabase.table('chats').select('*').eq('project_id', project_id).order('updated_at', desc=True).execute()
        
        return {
            "message": "Project chats retrieved successfully",
            "data": result.data
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get project chats: {str(e)}")



@app.post("/api/chats")
async def create_chat(chat: ChatCreate):
    try:
        # If project_id is provided, verify the project exists and belongs to the user
        if chat.project_id:
            project_result = supabase.table('projects').select('id').eq('id', chat.project_id).eq('clerk_id', chat.clerk_id).execute()
            
            if not project_result.data:
                raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Insert new chat into database
        result = supabase.table('chats').insert({
            "title": chat.title,
            "project_id": chat.project_id,
            "clerk_id": chat.clerk_id
        }).execute()
        
        return {
            "message": "Chat created successfully",
            "data": result.data[0]
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create chat: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)