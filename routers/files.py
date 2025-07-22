from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uuid
from datetime import datetime
from database import supabase, s3_client, BUCKET_NAME
import asyncio
import os
from unstructured.partition.pdf import partition_pdf
from unstructured.chunking.title import chunk_by_title


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
                "document": document_result.data[0]
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





def update_status(document_id: str, status: str, progress: int):
    """Update document processing status"""
    supabase.table('project_documents').update({
        'processing_status': status,
        'progress_percentage': progress,
        'updated_at': 'now()'
    }).eq('id', document_id).execute()
    print(f"Document {document_id}: {status} ({progress}%)")


async def step1_download_and_partition(document_id: str):
    """Step 1: Download PDF from S3 and partition into elements"""
    print(f"Step 1: Downloading and partitioning document {document_id}")
    
    # Get document info from database
    doc_result = supabase.table('project_documents').select('*').eq('id', document_id).execute()
    if not doc_result.data:
        raise Exception("Document not found")
        
    document = doc_result.data[0]
    s3_key = document['s3_key']
    
    # Download to temporary file
    temp_file = f"/tmp/{document_id}.pdf"
    s3_client.download_file(BUCKET_NAME, s3_key, temp_file)
    
    from unstructured.partition.pdf import partition_pdf
    
    elements = partition_pdf(
        filename=temp_file,
        strategy="hi_res",
        infer_table_structure=True
    )
    
    # Clean up temp file
    os.remove(temp_file)
    
    print(f"✅ Extracted {len(elements)} elements from PDF")
    return elements

def step2_chunk_elements(elements):
    """Step 2: Chunk elements using title-based strategy"""
    print("Step 2: Chunking elements...")
    
    from unstructured.chunking.title import chunk_by_title
    
    chunks = chunk_by_title(
        elements,
        max_characters=3000,
        new_after_n_chars=1000,
        combine_text_under_n_chars=500
    )
    
    print(f"✅ Created {len(chunks)} chunks from elements")
    return chunks

def step3_categorize_chunks(chunks):
    """Step 3: Separate chunks into text and table chunks"""
    print("Step 3: Categorizing chunks...")
    
    text_chunks = []
    table_chunks = []
    
    for chunk in chunks:
        chunk_type = "text"
        content = chunk.text
        original_content = None
        
        # Look for table elements in the original elements that formed this chunk
        if hasattr(chunk, 'metadata') and hasattr(chunk.metadata, 'orig_elements'):
            for orig_element in chunk.metadata.orig_elements:
                if hasattr(orig_element, 'category') and orig_element.category == 'Table':
                    if hasattr(orig_element.metadata, 'text_as_html') and orig_element.metadata.text_as_html:
                        chunk_type = "table"
                        original_content = orig_element.metadata.text_as_html
                        break
        
        # Extract page number from metadata if available
        page_num = 1
        if hasattr(chunk, 'metadata') and hasattr(chunk.metadata, 'page_number'):
            page_num = chunk.metadata.page_number
        
        chunk_data = {
            'content': content,
            'original_content': original_content,
            'page_number': page_num,
            'type': chunk_type,
            'char_count': len(content)
        }
        
        if chunk_type == "table":
            table_chunks.append(chunk_data)
        else:
            text_chunks.append(chunk_data)
    
    print(f"✅ Found:")
    print(f"   📝 Text chunks: {len(text_chunks)}")
    print(f"   📊 Table chunks: {len(table_chunks)}")
    
    return text_chunks, table_chunks


def step4_store_chunks(document_id: str, text_chunks: list, table_chunks: list):
    """Step 4: Store all chunks in database"""
    print("Step 4: Storing chunks in database...")
    
    all_chunks = text_chunks + table_chunks
    
    for i, chunk_data in enumerate(all_chunks):
        # Add document_id and chunk_index
        chunk_data.update({
            'document_id': document_id,
            'chunk_index': i
        })
        
        supabase.table('document_chunks').insert(chunk_data).execute()
    
    print(f"✅ Stored {len(all_chunks)} chunks in database")
    return len(all_chunks)

async def process_document(document_id: str, project_id: str):
    """Main orchestrator for document processing"""
    try:
        print(f"\n🔄 Starting document processing: {document_id}")
        
        # Step 1: Download and partition
        update_status(document_id, 'analysis', 10)
        update_status(document_id, 'partitioning', 30)
        elements = await step1_download_and_partition(document_id)
        
        # Step 2: Chunk elements
        update_status(document_id, 'chunking', 70)
        chunks = step2_chunk_elements(elements)
        
        # Step 3: Categorize chunks
        text_chunks, table_chunks = step3_categorize_chunks(chunks)
        
        # Step 4: Store chunks
        total_chunks = step4_store_chunks(document_id, text_chunks, table_chunks)
        
        # Mark as completed
        update_status(document_id, 'completed', 100)
        print(f"✅ Document {document_id}: Processing completed with {total_chunks} chunks")
        
    except Exception as e:
        print(f"❌ Error processing document {document_id}: {str(e)}")
        update_status(document_id, 'failed', 0)
        raise e





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
        background_tasks.add_task(process_document, document_id, project_id)
        
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
