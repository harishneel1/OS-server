from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import uuid
from datetime import datetime
from database import supabase, s3_client, BUCKET_NAME

router = APIRouter(
    tags=["files"]
)

class FileUploadRequest(BaseModel):
    filename: str
    file_size: int
    file_type: str

@router.post("/api/projects/{project_id}/files/upload-url")
async def get_upload_url(project_id: str, file_request: FileUploadRequest, clerk_id: str):
    try:        
        # Verify project exists and belongs to user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Generate unique S3 key
        file_extension = file_request.filename.split('.')[-1] if '.' in file_request.filename else ''
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        s3_key = f"projects/{project_id}/documents/{timestamp}_{unique_id}.{file_extension}"
        
        # Generate presigned URL (expires in 1 hour)
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': s3_key,
                'ContentType': file_request.file_type
            },
            ExpiresIn=3600  # 1 hour
        )
        
        # Create database record with pending status
        document_result = supabase.table('project_documents').insert({
            'project_id': project_id,
            'original_filename': file_request.filename,
            's3_key': s3_key,
            'file_size': file_request.file_size,
            'file_type': file_request.file_type,
            'processing_status': 'uploading',
            'clerk_id': clerk_id
        }).execute()
        
        if not document_result.data:
            raise HTTPException(status_code=500, detail="Failed to create document record")
                
        return {
            "message": "Upload URL generated successfully",
            "data": {
                "upload_url": presigned_url,
                "s3_key": s3_key,
                "document_id": document_result.data[0]['id']
            }
        }
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        print(f"ERROR TYPE: {type(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URL: {str(e)}")

@router.get("/api/projects/{project_id}/files")
async def get_project_files(project_id: str, clerk_id: str):
    try:
        # Verify project exists and belongs to user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Get all files for this project
        files_result = supabase.table('project_documents').select('*').eq('project_id', project_id).order('created_at', desc=True).execute()
        
        return {
            "message": "Project files retrieved successfully",
            "data": files_result.data or []
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get project files: {str(e)}")


@router.post("/api/projects/{project_id}/files/confirm")
async def confirm_file_upload(project_id: str, confirm_request: dict, clerk_id: str):
    try:
        s3_key = confirm_request.get('s3_key')
        
        if not s3_key:
            raise HTTPException(status_code=400, detail="s3_key is required")
        
        # Verify project exists and belongs to user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Update document status to completed
        result = supabase.table('project_documents').update({
            'processing_status': 'queued',     
            'progress_percentage': 0, 
            'updated_at': 'now()'
        }).eq('s3_key', s3_key).eq('project_id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return {
            "message": "Upload confirmed successfully",
            "data": result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR confirming upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to confirm upload: {str(e)}")


@router.delete("/api/projects/{project_id}/files/{file_id}")
async def delete_file(project_id: str, file_id: str, clerk_id: str):
    try:
        # Verify project exists and belongs to user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Get the file record to get the s3_key
        file_result = supabase.table('project_documents').select('*').eq('id', file_id).eq('project_id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not file_result.data:
            raise HTTPException(status_code=404, detail="File not found")
        
        file_record = file_result.data[0]
        s3_key = file_record['s3_key']
        
        # Delete from S3 first
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)
            print(f"DEBUG: Deleted from S3: {s3_key}")
        except Exception as s3_error:
            print(f"WARNING: Failed to delete from S3: {s3_error}")
            # Continue with database deletion even if S3 fails
        
        # Delete from database
        delete_result = supabase.table('project_documents').delete().eq('id', file_id).eq('project_id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not delete_result.data:
            raise HTTPException(status_code=500, detail="Failed to delete file record")
        
        return {
            "message": "File deleted successfully",
            "data": delete_result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR deleting file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")
