from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uuid
from datetime import datetime
from database import supabase, s3_client, BUCKET_NAME
import asyncio


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

async def simulate_document_processing(document_id: str, project_id: str):
    """Simulate realistic document processing with status updates"""
    
    pipeline_steps = [
        ('analysis', 1),     # status, seconds to wait
        ('partitioning', 2),  
        ('enrichment', 1),
        ('chunking', 1),
        ('embedding', 2),
        ('storage', 1),
        ('indexing', 1),
        ('completed', 0)     # no wait for completed
    ]
    
    try:
        for i, (status, wait_time) in enumerate(pipeline_steps):
            # Calculate progress percentage
            progress = int((i / (len(pipeline_steps) - 1)) * 100)  # -1 because completed is 100%
            
            # Update status in database
            supabase.table('project_documents').update({
                'processing_status': status,
                'progress_percentage': progress,
                'updated_at': 'now()'
            }).eq('id', document_id).execute()
            
            print(f"Document {document_id}: {status} ({progress}%)")
            
            # Wait before next step (except for completed)
            if status != 'completed' and wait_time > 0:
                await asyncio.sleep(wait_time)
        
        # Create mock chunks when processing is completed
        if status == 'completed':
            test_chunks = [
                {
                    'document_id': document_id,
                    'content': f'Executive Summary: This is test chunk {i+1} from the document. This represents content that would be extracted during real document processing. It contains meaningful text that would help answer user questions about the document content.',
                    'chunk_index': i,
                    'page_number': (i % 5) + 1,  # Distribute across pages 1-5
                    'type': "text",
                    'char_count': 150 + (i * 20)  # Varying lengths
                }
                for i in range(6)  
            ]
            
            # Add a couple of image and table chunks for variety
            test_chunks.extend([
                {
                    'document_id': document_id,
                    'content': 'Chart showing quarterly revenue growth: Q1: $2.1M, Q2: $2.8M, Q3: $3.2M, Q4: $3.9M. Shows consistent upward trend with 23% average quarterly growth.',
                    'chunk_index': 6,
                    'page_number': 3,
                    'type': "image",
                    'char_count': 0
                },
                {
                    'document_id': document_id,
                    'content': 'Performance comparison table: Model A achieved 94.2% accuracy, Model B: 87.5%, Model C: 96.8%. Model C shows best performance across all metrics.',
                    'chunk_index': 7,
                    'page_number': 4,
                    'type': "table", 
                    'char_count': 0
                }
            ])

            # Insert all chunks
            for chunk_data in test_chunks:
                supabase.table('document_chunks').insert(chunk_data).execute()
            
            print(f"Document {document_id}: Created {len(test_chunks)} chunks")
            
    except Exception as e:
        print(f"Error processing document {document_id}: {str(e)}")
        # Mark as failed if something goes wrong
        supabase.table('project_documents').update({
            'processing_status': 'failed',
            'updated_at': 'now()'
        }).eq('id', document_id).execute()

@router.post("/api/projects/{project_id}/files/confirm")
async def confirm_file_upload(project_id: str, confirm_request: dict, clerk_id: str, background_tasks: BackgroundTasks):
    try:
        s3_key = confirm_request.get('s3_key')
        
        if not s3_key:
            raise HTTPException(status_code=400, detail="s3_key is required")
        
        # Verify project exists and belongs to user
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        # Update document status to queued (processing will start in background)
        result = supabase.table('project_documents').update({
            'processing_status': 'queued',     
            'progress_percentage': 0, 
            'updated_at': 'now()'
        }).eq('s3_key', s3_key).eq('project_id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Document not found")

        document = result.data[0]
        document_id = document['id']

        # Start background processing (this runs asynchronously)
        background_tasks.add_task(simulate_document_processing, document_id, project_id)
        
        return {
            "message": "Upload confirmed, processing started",
            "data": document
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


@router.get("/api/projects/{project_id}/files/{file_id}/chunks")
async def get_document_chunks(project_id: str, file_id: str, clerk_id: str):
    try:
        project_result = supabase.table('projects').select('id').eq('id', project_id).eq('clerk_id', clerk_id).execute()
        
        if not project_result.data:
            raise HTTPException(status_code=404, detail="Project not found or access denied")
        
        doc_result = supabase.table('project_documents').select('id').eq('id', file_id).eq('project_id', project_id).execute()
        
        if not doc_result.data:
            raise HTTPException(status_code=404, detail="Document not found")
        
        chunks_result = supabase.table('document_chunks').select('*').eq('document_id', file_id).order('chunk_index').execute()
        
        return {
            "message": "Document chunks retrieved successfully",
            "data": chunks_result.data or []
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR getting chunks: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get document chunks: {str(e)}")
