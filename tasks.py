from celery import Celery
from database import supabase
import time
import os
from unstructured.partition.pdf import partition_pdf
from unstructured.chunking.title import chunk_by_title
from database import s3_client, BUCKET_NAME

# Create Celery app
celery_app = Celery(
    'document_processor',
    broker='redis://localhost:6379/0',  # Redis connection
    backend='redis://localhost:6379/0'  # Where to store results
)

def update_status(document_id: str, status: str, progress: int):
    """Update document processing status"""
    supabase.table('project_documents').update({
        'processing_status': status,
        'progress_percentage': progress,
        'updated_at': 'now()'
    }).eq('id', document_id).execute()
    print(f"✅ Document {document_id}: {status} ({progress}%)")


@celery_app.task
def process_document_simple(document_id: str, project_id: str):
    """Simplified version - keep this for quick testing"""
    try:
        print(f"🔄 Starting SIMPLE Celery task for document: {document_id}")
        
        update_status(document_id, 'analysis', 20)
        time.sleep(2)
        
        update_status(document_id, 'partitioning', 50)
        time.sleep(3)
        
        update_status(document_id, 'chunking', 80)
        time.sleep(2)
        
        update_status(document_id, 'completed', 100)
        
        print(f"✅ SIMPLE Celery task completed for document: {document_id}")
        return {"status": "success", "document_id": document_id}
        
    except Exception as e:
        print(f"❌ SIMPLE Celery task failed for document {document_id}: {str(e)}")
        update_status(document_id, 'failed', 0)
        return {"status": "error", "error": str(e)}

@celery_app.task
def process_document_real(document_id: str, project_id: str):
    """Real document processing with actual PDF partition and chunking"""
    try:
        print(f"🔄 Starting REAL Celery task for document: {document_id}")
        
        # Step 1: Download and partition
        update_status(document_id, 'analysis', 10)
        update_status(document_id, 'partitioning', 30)
        elements = download_and_partition(document_id)
        
        # Step 2: Chunk elements
        update_status(document_id, 'chunking', 70)
        chunks = chunk_elements(elements)
        
        # Step 3: Categorize chunks
        text_chunks, table_chunks = categorize_chunks(chunks)
        
        # Step 4: Store chunks
        update_status(document_id, 'storage', 90)
        total_chunks = store_chunks(document_id, text_chunks, table_chunks)
        
        # Mark as completed
        update_status(document_id, 'completed', 100)
        print(f"✅ REAL Celery task completed for document: {document_id} with {total_chunks} chunks")
        
        return {"status": "success", "document_id": document_id, "total_chunks": total_chunks}
        
    except Exception as e:
        print(f"❌ REAL Celery task failed for document {document_id}: {str(e)}")
        update_status(document_id, 'failed', 0)
        return {"status": "error", "error": str(e)}

def download_and_partition(document_id: str):
    """Download PDF from S3 and partition into elements"""
    print(f"📥 Downloading and partitioning document {document_id}")
    
    # Get document info from database
    doc_result = supabase.table('project_documents').select('*').eq('id', document_id).execute()
    if not doc_result.data:
        raise Exception("Document not found")
        
    document = doc_result.data[0]
    s3_key = document['s3_key']
    
    # Download to temporary file
    temp_file = f"/tmp/{document_id}.pdf"
    s3_client.download_file(BUCKET_NAME, s3_key, temp_file)
    
    elements = partition_pdf(
        filename=temp_file,
        strategy="hi_res",
        infer_table_structure=True
    )
    
    # Clean up temp file
    os.remove(temp_file)
    
    print(f"✅ Extracted {len(elements)} elements from PDF")
    return elements

def chunk_elements(elements):
    """Chunk elements using title-based strategy"""
    print("🔨 Chunking elements...")
    
    chunks = chunk_by_title(
        elements,
        max_characters=3000,
        new_after_n_chars=1000,
        combine_text_under_n_chars=500
    )
    
    print(f"✅ Created {len(chunks)} chunks from elements")
    return chunks

def categorize_chunks(chunks):
    """Separate chunks into text and table chunks"""
    print("📂 Categorizing chunks...")
    
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
    
    print(f"✅ Found {len(text_chunks)} text chunks and {len(table_chunks)} table chunks")
    return text_chunks, table_chunks

def store_chunks(document_id: str, text_chunks: list, table_chunks: list):
    """Store all chunks in database"""
    print("💾 Storing chunks in database...")
    
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