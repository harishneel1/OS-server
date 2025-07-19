from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import supabase

router = APIRouter(
    tags=["projects"]
)


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
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

# Projects endpoints
@router.get("/api/projects")
async def get_projects(clerk_id: str):
    try:
        result = supabase.table('projects').select('*').eq('clerk_id', clerk_id).execute()
        
        return {
            "message": "Projects retrieved successfully",
            "data": result.data
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get projects: {str(e)}")



@router.post("/api/projects")
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

@router.get("/api/projects/{project_id}")
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

@router.get("/api/projects/{project_id}/chats")
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


@router.get("/api/projects/{project_id}/settings")
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


@router.put("/api/projects/{project_id}/settings")
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
