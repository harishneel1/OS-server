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
    """Real document processing with CompositeElement approach"""
    try:
        print(f"🔄 Starting REAL Celery task for document: {document_id}")
        
        # Step 1: Download and partition
        update_status(document_id, 'analysis', 10)
        update_status(document_id, 'partitioning', 30)
        elements = download_and_partition(document_id) 
        
        # Step 2: Chunk elements
        update_status(document_id, 'chunking', 50)
        chunks = chunk_elements(elements)
        
        # Step 3: Process CompositeElements with AI summarization
        update_status(document_id, 'enrichment', 70)
        processed_chunks = process_composite_elements(chunks)
        
        # Step 4: Store chunks
        update_status(document_id, 'storage', 90)
        total_chunks = store_chunks(document_id, processed_chunks)
        
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
        max_characters=10000,
        combine_text_under_n_chars=2000,
        new_after_n_chars=6000
    )
    
    print(f"✅ Created {len(chunks)} chunks from elements")
    return chunks

def process_composite_elements(chunks):
    """Process each CompositeElement and analyze its content types with AI summarization"""
    print("📂 Processing CompositeElement chunks with AI summarization...")
    
    processed_chunks = []
    
    for i, chunk in enumerate(chunks):
        if "CompositeElement" in str(type(chunk)):
            print(f"   Processing chunk {i+1}/{len(chunks)}...")
            
            # Start with basic info
            chunk_types = ["text"]  # Always has text
            tables_html = []
            images_base64 = []
            
            # Analyze what's inside this CompositeElement
            if hasattr(chunk, 'metadata') and hasattr(chunk.metadata, 'orig_elements'):
                for orig_element in chunk.metadata.orig_elements:
                    # Check for tables
                    if "Table" in str(type(orig_element)):
                        if "table" not in chunk_types:
                            chunk_types.append("table")
                        # Get table HTML
                        table_html = getattr(orig_element.metadata, 'text_as_html', orig_element.text) if hasattr(orig_element, 'metadata') else orig_element.text
                        tables_html.append(table_html)
                    
                    # Check for images
                    elif "Image" in str(type(orig_element)) and hasattr(orig_element.metadata, 'image_base64'):
                        if "image" not in chunk_types:
                            chunk_types.append("image")
                        images_base64.append(orig_element.metadata.image_base64)
            
            # Get page number
            page_num = getattr(chunk.metadata, 'page_number', 1) if hasattr(chunk, 'metadata') else 1
            
            # Create original_content JSON structure
            original_content = {
                "text": chunk.text
            }
            if tables_html:
                original_content["tables"] = tables_html
            if images_base64:
                original_content["images"] = images_base64
            
            # Determine if we need AI summarization
            needs_ai_summary = len(chunk_types) > 1  # Has tables or images
            
            if needs_ai_summary:
                print(f"     Creating AI summary for mixed content chunk (types: {chunk_types})...")
                content = create_ai_summary_for_chunk(chunk.text, tables_html, images_base64)
            else:
                # Pure text chunk - no AI needed
                content = chunk.text
            
            processed_chunks.append({
                'content': content,
                'original_content': original_content,
                'type': chunk_types,
                'page_number': page_num,
                'char_count': len(content)
            })
    
    print(f"✅ Processed {len(processed_chunks)} CompositeElement chunks")
    return processed_chunks

def create_ai_summary_for_chunk(text: str, tables_html: list, images_base64: list):
    """Create AI summary for a chunk with mixed content (text + tables + images)"""
    
    # Build the text part of the prompt
    text_content = f"TEXT CONTENT:\n{text}\n\n"
    
    # Add tables to the prompt
    tables_content = ""
    if tables_html:
        tables_content = "TABLES:\n"
        for i, table in enumerate(tables_html):
            tables_content += f"Table {i+1}:\n{table}\n\n"
    
    # Create the message content for multi-modal input
    message_content = [
        {
            "type": "text",
            "text": f"""Create a comprehensive summary of this document content that will be used for semantic search and retrieval. Include specific data points, key concepts, entities, and numbers that users might search for.

{text_content}{tables_content}

INSTRUCTIONS:
- Extract and mention specific data points, numbers, percentages, dates from tables
- Include key entities, concepts, and technical terms
- Describe visual elements and data trends from images  
- Make the summary dense with searchable keywords
- Focus on factual information that users would query
- Limit to 50 words but pack with relevant details

SUMMARY:"""
        }
    ]
    
    # Add images to the message
    for i, image_base64 in enumerate(images_base64):
        message_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
        })
    
    try:
        message = HumanMessage(content=message_content)
        response = llm.invoke([message])
        ai_summary = response.content
        
        print(f"     ✅ AI summary created ({len(ai_summary)} chars)")
        return ai_summary
        
    except Exception as e:
        print(f"     ❌ Error creating AI summary: {e}")
        # Fallback to basic summary
        fallback = f"{text[:200]}..."
        if tables_html:
            fallback += f" [Contains {len(tables_html)} table(s)]"
        if images_base64:
            fallback += f" [Contains {len(images_base64)} image(s)]"
        return fallback

def store_chunks(document_id: str, processed_chunks: list):
    """Store all processed chunks in database"""
    print("💾 Storing chunks in database...")
    
    for i, chunk_data in enumerate(processed_chunks):
        # Add document_id and chunk_index
        chunk_data.update({
            'document_id': document_id,
            'chunk_index': i
        })
        
        supabase.table('document_chunks').insert(chunk_data).execute()
    
    print(f"✅ Stored {len(processed_chunks)} chunks in database")
    return len(processed_chunks)