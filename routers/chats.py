from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import supabase

router = APIRouter(
    tags=["chats"]
)

class ChatCreate(BaseModel):
    title: str
    project_id: Optional[str] = None
    clerk_id: str

class MessageCreate(BaseModel):
    chat_id: str
    content: str
    role: str  # "user" or "assistant"
    clerk_id: str


@router.get("/api/chats")
async def get_chats(clerk_id: str):
    try:
        result = supabase.table('chats').select('*').eq('clerk_id', clerk_id).order('updated_at', desc=True).execute()
        
        return {
            "message": "Chats retrieved successfully",
            "data": result.data
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get chats: {str(e)}")

@router.post("/api/chats")
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


@router.get("/api/chats/{chat_id}")
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

@router.post("/api/messages")
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

