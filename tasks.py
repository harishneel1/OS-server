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
        raw_text_chunks, raw_table_chunks = categorize_chunks(chunks)

        # Step 4: Prepare for database (add metadata)
        text_chunks, table_chunks = convert_chunks_to_db_format(raw_text_chunks, raw_table_chunks)

        # Step 5: Store chunks
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
    """Separate chunks into text and table chunks - SIMPLE like Claude3.py"""
    print("📂 Categorizing chunks...")
    
    text_chunks = []
    table_chunks = []
    
    # Simple categorization like Claude3.py
    for chunk in chunks:
        chunk_type = str(type(chunk))
        
        if 'CompositeElement' in chunk_type:
            text_chunks.append(chunk)
        elif 'TableChunk' in chunk_type:
            table_chunks.append(chunk)
        # If it's neither, we'll treat it as text
        else:
            text_chunks.append(chunk)
    
    print(f"✅ Found {len(text_chunks)} text chunks and {len(table_chunks)} table chunks")
    return text_chunks, table_chunks

def convert_chunks_to_db_format(text_chunks, table_chunks):
    """Convert unstructured chunks to database format"""
    
    db_text_chunks = []
    db_table_chunks = []
    
    # Convert text chunks
    for i, chunk in enumerate(text_chunks):
        page_num = getattr(chunk.metadata, 'page_number', 1) if hasattr(chunk, 'metadata') else 1
        
        db_text_chunks.append({
            'content': chunk.text,
            'original_content': None,
            'page_number': page_num,
            'type': 'text',
            'char_count': len(chunk.text)
        })
    
    # Convert table chunks  
    for i, chunk in enumerate(table_chunks):
        page_num = getattr(chunk.metadata, 'page_number', 1) if hasattr(chunk, 'metadata') else 1
        table_html = getattr(chunk.metadata, 'text_as_html', chunk.text) if hasattr(chunk, 'metadata') else chunk.text
        
        db_table_chunks.append({
            'content': chunk.text,
            'original_content': table_html,
            'page_number': page_num,
            'type': 'table',
            'char_count': len(chunk.text)
        })
    
    return db_text_chunks, db_table_chunks

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