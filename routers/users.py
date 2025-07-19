from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import supabase

router = APIRouter(
    tags=["users"]
)

# Pydantic models for this router
class UserCreate(BaseModel):
    clerk_id: str

# User endpoints
@router.post("/users")
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

@router.post("/webhooks/clerk/user-created")
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