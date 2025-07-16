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

class MessageCreate(BaseModel):
    chat_id: str
    content: str
    role: str  # "user" or "assistant"
    clerk_id: str

class ProjectSettingsCreate(BaseModel):
    embedding_model: str = "text-embedding-3-large"
    rag_strategy: str = "basic"
    chunks_per_search: int = 10
    final_context_size: int = 5
    similarity_threshold: float = 0.1
    number_of_queries: int = 5
    reranking_enabled: bool = True
    reranking_model: str = "ms-marco-MiniLM-L-12-v2"
    vector_weight: float = 0.7
    keyword_weight: float = 0.3

class ProjectSettingsUpdate(BaseModel):
    embedding_model: Optional[str] = None
    rag_strategy: Optional[str] = None
    chunks_per_search: Optional[int] = None
    final_context_size: Optional[int] = None
    similarity_threshold: Optional[float] = None
    number_of_queries: Optional[int] = None
    reranking_enabled: Optional[bool] = None
    reranking_model: Optional[str] = None
    vector_weight: Optional[float] = None
    keyword_weight: Optional[float] = None

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
        project_result = supabase.table('projects').insert({
            "name": project.name,
            "description": project.description,
            "clerk_id": project.clerk_id
        }).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=500, detail="Failed to create project")
        
        created_project = project_result.data[0]
        project_id = created_project["id"]
        
        # Create default settings for the project
        settings_result = supabase.table('project_settings').insert({
            "project_id": project_id,
            "embedding_model": "text-embedding-3-large",
            "rag_strategy": "basic",
            "chunks_per_search": 10,
            "final_context_size": 5,
            "similarity_threshold": 0.1,
            "number_of_queries": 5,
            "reranking_enabled": True,
            "reranking_model": "ms-marco-MiniLM-L-12-v2",
            "vector_weight": 0.7,
            "keyword_weight": 0.3
        }).execute()
        
        if not settings_result.data:
            # If settings creation fails, we should clean up the project
            supabase.table('projects').delete().eq('id', project_id).execute()
            raise HTTPException(status_code=500, detail="Failed to create project settings")
        
        return {
            "message": "Project created successfully",
            "data": created_project
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
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


@app.get("/api/projects/{project_id}/settings")
async def get_project_settings(project_id: str, clerk_id: str):
    try:
        # First verify the project exists and belongs to the user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Get the project settings
        settings_result = supabase.table('project_settings').select('*').eq('project_id', project_id).execute()
        
        if not settings_result.data:
            raise HTTPException(status_code=404, detail="Project settings not found")
        
        return {
            "message": "Project settings retrieved successfully",
            "data": settings_result.data[0]
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get project settings: {str(e)}")


@app.put("/api/projects/{project_id}/settings")
async def update_project_settings(project_id: str, settings: ProjectSettingsUpdate, clerk_id: str):
    try:
        # First verify the project exists and belongs to the user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Verify settings exist for this project
        existing_settings = supabase.table('project_settings').select('*').eq('project_id', project_id).execute()
        
        if not existing_settings.data:
            raise HTTPException(status_code=404, detail="Project settings not found")
        
        # Build update data (only include fields that were provided)
        update_data = {}
        for field, value in settings.model_dump(exclude_unset=True).items():
            update_data[field] = value
        
        # Add updated timestamp
        update_data["updated_at"] = "now()"
        
        # Perform the update
        result = supabase.table('project_settings').update(update_data).eq('project_id', project_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update project settings")
        
        return {
            "message": "Project settings updated successfully",
            "data": result.data[0]
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update project settings: {str(e)}")

@app.get("/api/chats")
async def get_chats(clerk_id: str):
    try:
        result = supabase.table('chats').select('*').eq('clerk_id', clerk_id).order('updated_at', desc=True).execute()
        
        return {
            "message": "Chats retrieved successfully",
            "data": result.data
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get chats: {str(e)}")

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


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, clerk_id: str):
    try:
        # Get the chat and verify it belongs to the user
        result = supabase.table('chats').select('*').eq('id', chat_id).eq('clerk_id', clerk_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Chat not found or access denied")
        
        chat = result.data[0]
        
        # Get messages for this chat
        messages_result = supabase.table('messages').select('*').eq('chat_id', chat_id).order('created_at', desc=False).execute()
        
        # Add messages to chat object
        chat['messages'] = messages_result.data or []
        
        return {
            "message": "Chat retrieved successfully",
            "data": chat
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get chat: {str(e)}")

@app.post("/api/messages")
async def create_message(message: MessageCreate):
    try:
        # Verify chat exists and belongs to user
        chat_result = supabase.table('chats').select('*').eq('id', message.chat_id).eq('clerk_id', message.clerk_id).execute()
        
        if not chat_result.data:
            raise HTTPException(status_code=404, detail="Chat not found or access denied")
        
        chat = chat_result.data[0]
        
        # Create the message
        message_result = supabase.table('messages').insert({
            "chat_id": message.chat_id,
            "content": message.content,
            "role": message.role,
            "clerk_id": message.clerk_id
        }).execute()
        
        # Update chat title if it's the first message and it's from user
        if message.role == "user":
            # Check if this is the first user message
            existing_messages = supabase.table('messages').select('id').eq('chat_id', message.chat_id).execute()
            
            if len(existing_messages.data) == 1:  # This is the first message
                new_title = message.content[:30] + ("..." if len(message.content) > 30 else "")
                supabase.table('chats').update({
                    "title": new_title,
                    "updated_at": "now()"
                }).eq('id', message.chat_id).execute()
        
        # Always update the chat's updated_at timestamp
        supabase.table('chats').update({
            "updated_at": "now()"
        }).eq('id', message.chat_id).execute()
        
        return {
            "message": "Message created successfully",
            "data": message_result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create message: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)