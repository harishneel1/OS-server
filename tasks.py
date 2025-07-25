from celery import Celery
from database import supabase
import time
import os
from unstructured.partition.pdf import partition_pdf
from unstructured.chunking.title import chunk_by_title
from database import s3_client, BUCKET_NAME
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# Create Celery app
celery_app = Celery(
    'document_processor',
    broker='redis://localhost:6379/0',  # Redis connection
    backend='redis://localhost:6379/0'  # Where to store results
)

# Initialize LLM for summarization
llm = ChatOpenAI(model="gpt-4o", temperature=0)

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
        update_status(document_id, 'chunking', 50)
        chunks = chunk_elements(elements)
        
        # Step 3: Categorize chunks (simple type detection)
        update_status(document_id, 'enrichment', 60)
        raw_text_chunks, raw_table_chunks, raw_image_chunks = categorize_chunks(chunks)
        
        # Step 4: AI Enrichment (summarize tables and images)
        update_status(document_id, 'enrichment', 70)
        enriched_chunks = create_ai_summaries(raw_text_chunks, raw_table_chunks, raw_image_chunks)

        # Step 5: Prepare for database (add metadata)
        text_chunks, table_chunks, image_chunks = convert_chunks_to_db_format(*enriched_chunks)
        
        # Step 6: Store chunks
        update_status(document_id, 'storage', 90)
        total_chunks = store_chunks(document_id, text_chunks, table_chunks, image_chunks)
        
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
        infer_table_structure=True,
        extract_image_block_types=["Image"],
        extract_image_block_to_payload=True
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
    """Separate chunks into text, table, and image chunks - like Claude3.py"""
    print("📂 Categorizing chunks...")
    
    text_chunks = []
    table_chunks = []
    image_chunks = []
    
    # Step 1: Simple categorization like Claude3.py
    for chunk in chunks:
        chunk_type = str(type(chunk))
        
        if 'CompositeElement' in chunk_type:
            text_chunks.append(chunk)
        elif 'TableChunk' in chunk_type:
            table_chunks.append(chunk)
        else:
            text_chunks.append(chunk)
    
    # Step 2: Extract images from CompositeElement chunks (like Claude3.py)
    for chunk in chunks:
        if 'CompositeElement' in str(type(chunk)):
            # Look inside CompositeElement for original Image elements
            orig_elements = getattr(chunk.metadata, 'orig_elements', [])
            for orig_element in orig_elements:
                if 'Image' in str(type(orig_element)) and hasattr(orig_element.metadata, 'image_base64'):
                    image_chunks.append(orig_element)  # Store the original Image element
    
    print(f"✅ Found {len(text_chunks)} text chunks, {len(table_chunks)} table chunks, {len(image_chunks)} image chunks")
    return text_chunks, table_chunks, image_chunks

def create_ai_summaries(text_chunks, table_chunks, image_chunks):
    """Create AI summaries for tables and images"""
    print("🤖 Creating AI summaries...")
    print(table_chunks, "table_chunks")
    
    # Text chunks don't need summarization
    enriched_text_chunks = text_chunks
    
    # Summarize tables
    enriched_table_chunks = []
    for i, chunk in enumerate(table_chunks):
        print(chunk, "table_chunk")
        print(f"   Summarizing table {i+1}/{len(table_chunks)}...")

        # Get table HTML
        table_html = getattr(chunk.metadata, 'text_as_html', chunk.text) if hasattr(chunk, 'metadata') else chunk.text
        
        prompt = f"""
        Give me a summary of this table in 30 words
        
        {table_html}
        """
        
        try:
            response = llm.invoke(prompt)
            table_summary = response.content
            print(table_summary, "table_summary")
        except Exception as e:
            print(f"   Error summarizing table {i+1}: {e}")
            table_summary = "Table summary unavailable"
            
        # Store both summary and original
        enriched_table_chunks.append({
            'chunk': chunk,
            'summary': table_summary,
            'original_html': table_html
        })
    
    # Summarize images
    enriched_image_chunks = []
    for i, image_element in enumerate(image_chunks):
        print(f"   Analyzing image {i+1}/{len(image_chunks)}...")
        
        image_base64 = image_element.metadata.image_base64
        
        message = HumanMessage(content=[
            {
                "type": "text",
                "text": "Please summarize this image in 30 words or less. Focus on key visual elements, text, charts, or important information."
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            }
        ])
        
        try:
            response = llm.invoke([message])
            image_summary = response.content
        except Exception as e:
            print(f"   Error analyzing image {i+1}: {e}")
            image_summary = "Image analysis unavailable"
            
        # Store both summary and original
        enriched_image_chunks.append({
            'image_element': image_element,
            'summary': image_summary,
            'original_base64': image_base64
        })
    
    print(f"✅ Created summaries for {len(enriched_table_chunks)} tables and {len(enriched_image_chunks)} images")
    return enriched_text_chunks, enriched_table_chunks, enriched_image_chunks

def convert_chunks_to_db_format(text_chunks, table_chunks, image_chunks):
    """Convert enriched chunks to database format"""
    print("📋 Converting to database format...")
    
    db_text_chunks = []
    db_table_chunks = []
    db_image_chunks = []
    
    # Convert text chunks (no AI processing needed)
    for chunk in text_chunks:
        page_num = getattr(chunk.metadata, 'page_number', 1) if hasattr(chunk, 'metadata') else 1
        
        db_text_chunks.append({
            'content': chunk.text,
            'original_content': None,  # For text, original = content, so we keep it NULL
            'page_number': page_num,
            'type': 'text',
            'char_count': len(chunk.text)
        })
    
    # Convert table chunks (content = summary, original_content = HTML)
    for enriched_table in table_chunks:
        chunk = enriched_table['chunk']
        page_num = getattr(chunk.metadata, 'page_number', 1) if hasattr(chunk, 'metadata') else 1
        
        db_table_chunks.append({
            'content': enriched_table['summary'],  # AI summary for search
            'original_content': enriched_table['original_html'],  # HTML for display
            'page_number': page_num,
            'type': 'table',
            'char_count': len(enriched_table['summary'])
        })
    
    # Convert image chunks (content = summary, original_content = base64)
    for enriched_image in image_chunks:
        image_element = enriched_image['image_element']
        page_num = getattr(image_element.metadata, 'page_number', 1) if hasattr(image_element, 'metadata') else 1
        
        db_image_chunks.append({
            'content': enriched_image['summary'],  # AI description for search
            'original_content': enriched_image['original_base64'],  # Base64 for display
            'page_number': page_num,
            'type': 'image',
            'char_count': len(enriched_image['summary'])
        })
    
    print(f"✅ Converted {len(db_text_chunks)} text, {len(db_table_chunks)} table, {len(db_image_chunks)} image chunks")
    return db_text_chunks, db_table_chunks, db_image_chunks

def store_chunks(document_id: str, text_chunks: list, table_chunks: list, image_chunks: list):
    """Store all chunks in database"""
    print("💾 Storing chunks in database...")
    
    all_chunks = text_chunks + table_chunks + image_chunks
    
    for i, chunk_data in enumerate(all_chunks):
        # Add document_id and chunk_index
        chunk_data.update({
            'document_id': document_id,
            'chunk_index': i
        })
        
        supabase.table('document_chunks').insert(chunk_data).execute()
    
    print(f"✅ Stored {len(all_chunks)} chunks in database")
    return len(all_chunks)