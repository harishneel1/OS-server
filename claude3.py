import os
import base64
import uuid
from typing import List

# Core libraries
from unstructured.partition.pdf import partition_pdf
from unstructured.chunking.title import chunk_by_title

# LangChain imports
from langchain.schema.document import Document
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
# from langchain_chroma import Chroma
from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain.storage import InMemoryStore
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PDF_FILE = "AMTAGVI Commercial FAQ Document.pdf"


def step1_partition_pdf():
    """Step 1: Extract raw elements from PDF"""
    print("Step 1: Partitioning PDF...")
    
    elements = partition_pdf(
        filename=PDF_FILE,
        strategy="hi_res",
        infer_table_structure=True,
        extract_image_block_types=["Image"],
        extract_image_block_to_payload=True
    )
    
    print(f"✅ Extracted {len(elements)} raw elements from PDF")
    return elements

def step2_chunk_elements(elements):
    """Step 2: Chunk elements using by_title strategy"""
    print("Step 2: Chunking elements...")
    
    chunks = chunk_by_title(
        elements,
        max_characters=3000,
        new_after_n_chars=1000,
        combine_text_under_n_chars=500
    )
    
    print(f"✅ Created {len(chunks)} chunks from elements")
    return chunks

def step3_categorize_chunks(chunks):
    """Step 3: Separate chunks into text, tables, and images"""
    print("Step 3: Categorizing chunks...")
    
    text_chunks = []
    table_chunks = []
    image_chunks = []
    
    # Loop through all chunks and categorize them
    for chunk in chunks:
        chunk_type = str(type(chunk))
        
        if 'CompositeElement' in chunk_type:
            text_chunks.append(chunk)
            
        elif 'TableChunk' in chunk_type:  # Changed from 'Table' to 'TableChunk'
            table_chunks.append(chunk)
    
    # Extract images from CompositeElement chunks
    for chunk in chunks:
        if 'CompositeElement' in str(type(chunk)):
            # Look inside CompositeElement for original Image elements
            orig_elements = getattr(chunk.metadata, 'orig_elements', [])
            for orig_element in orig_elements:
                if 'Image' in str(type(orig_element)) and hasattr(orig_element.metadata, 'image_base64'):
                    image_chunks.append(orig_element)  # Store the original Image element
    
    print(f"✅ Found:")
    print(f"   📝 Text chunks: {len(text_chunks)}")
    print(f"   📊 Table chunks: {len(table_chunks)}")
    print(f"   🖼️ Image chunks: {len(image_chunks)}")
    
    return text_chunks, table_chunks, image_chunks

def step4_extract_content(text_chunks, table_chunks, image_chunks):
    """Step 4: Extract text, HTML, and base64 from chunks"""
    print("Step 4: Extracting content...")
    
    # Extract text content
    texts = [chunk.text for chunk in text_chunks]
    
    # Extract table HTML (updated for TableChunk)
    table_htmls = [getattr(chunk.metadata, 'text_as_html', chunk.text) for chunk in table_chunks]
    
    # Extract image base64 data (these are now original Image elements)
    image_base64s = [chunk.metadata.image_base64 for chunk in image_chunks]
    
    print(f"✅ Extracted content:")
    print(f"   📝 Text chunks: {len(texts)}")
    print(f"   📊 Table HTMLs: {len(table_htmls)}")
    print(f"   🖼️ Image base64s: {len(image_base64s)}")
    
    return texts, table_htmls, image_base64s

def step5_create_summaries(table_htmls, image_base64s):
    """Step 5: Create summaries for tables and images"""
    print("Step 5: Creating summaries...")
    
    llm = ChatOpenAI(model="gpt-4o", api_key=OPENAI_API_KEY, temperature=0)
    
    # Summarize tables
    table_summaries = []
    for i, table_html in enumerate(table_htmls):
        print(f"   Summarizing table {i+1}...")
        prompt = f"""
        Analyze this table and provide a concise summary:
        
        {table_html}
        
        Include key data points, trends, and what type of information it contains.
        Keep it concise (100-150 words).
        """
        
        try:
            response = llm.invoke(prompt)
            table_summaries.append(response.content)
        except Exception as e:
            print(f"   Error summarizing table {i+1}: {e}")
            table_summaries.append("Table summary unavailable")
    
    # Summarize images
    image_summaries = []
    for i, image_base64 in enumerate(image_base64s):
        print(f"   Analyzing image {i+1}...")
        message = HumanMessage(content=[
            {
                "type": "text",
                "text": "Analyze this image and provide a concise summary. Include key information about charts, tables, text, or visual elements."
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            }
        ])
        
        try:
            response = llm.invoke([message])
            image_summaries.append(response.content)
        except Exception as e:
            print(f"   Error analyzing image {i+1}: {e}")
            image_summaries.append("Image analysis unavailable")
    
    print(f"✅ Created:")
    print(f"   📊 Table summaries: {len(table_summaries)}")
    print(f"   🖼️ Image summaries: {len(image_summaries)}")
    
    return table_summaries, image_summaries

def step6_setup_retriever():
    """Step 6: Setup MultiVectorRetriever"""
    print("Step 6: Setting up MultiVectorRetriever...")
    
    # Create vector store for summaries
    vectorstore = Chroma(
        collection_name="pdf_summaries", 
        embedding_function=OpenAIEmbeddings(api_key=OPENAI_API_KEY)
    )
    
    # Create document store for original content
    docstore = InMemoryStore()
    
    # Create retriever
    retriever = MultiVectorRetriever(
        vectorstore=vectorstore,
        docstore=docstore,
        id_key="doc_id"
    )
    
    print("✅ MultiVectorRetriever setup complete")
    return retriever

def step7_add_to_retriever(retriever, texts, table_summaries, table_htmls, image_summaries, image_base64s):
    """Step 7: Add all content to retriever"""
    print("Step 7: Adding content to retriever...")
    
    def add_documents(summaries, originals, content_type):
        """Helper function to add documents to retriever"""
        if not summaries:
            return
            
        doc_ids = [str(uuid.uuid4()) for _ in summaries]
        
        # Create summary documents for vector search
        summary_docs = [
            Document(page_content=summary, metadata={"doc_id": doc_ids[i], "type": content_type})
            for i, summary in enumerate(summaries)
        ]
        
        # Add summaries to vector store (these get embedded and searched)
        retriever.vectorstore.add_documents(summary_docs)
        
        # Add original content to doc store (these get returned to LLM)
        retriever.docstore.mset(list(zip(doc_ids, originals)))
        
        print(f"   ✅ Added {len(summaries)} {content_type} documents")
    
    # Add text (summary = original for text)
    add_documents(texts, texts, "text")
    
    # Add tables (summary for search, HTML for LLM)
    add_documents(table_summaries, table_htmls, "table")
    
    # Add images (summary for search, base64 for LLM)
    add_documents(image_summaries, image_base64s, "image")
    
    print("✅ All content added to retriever")

def step8_create_qa_chain(retriever):
    """Step 8: Create question-answering chain"""
    print("Step 8: Creating QA chain...")
    
    template = """Answer the question based on the following context, which can include text, images, and tables:

{context}

Question: {question}

Answer:"""

    prompt = ChatPromptTemplate.from_template(template)
    model = ChatOpenAI(temperature=0, model="gpt-4o", api_key=OPENAI_API_KEY)
    
    chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt
        | model
        | StrOutputParser()
    )
    
    print("✅ QA chain created")
    return chain

def step9_interactive_search(retriever, qa_chain):
    """Step 9: Interactive search and QA"""
    print("Step 9: Starting interactive mode...")
    print("\n" + "="*60)
    print("🔍 INTERACTIVE MODE")
    print("Commands:")
    print("  'search: your query' - see what gets retrieved")
    print("  'ask: your question' - get AI answer")
    print("  'quit' - exit")
    print("="*60)
    
    while True:
        user_input = input("\n💬 Enter command: ").strip()
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            print("👋 Goodbye!")
            break
        
        if not user_input:
            continue
            
        if user_input.startswith('search:'):
            query = user_input[7:].strip()
            print(f"\n🔍 Searching for: '{query}'")
            
            try:
                results = retriever.get_relevant_documents(query, k=3)
                print(f"Found {len(results)} results:")
                
                for i, doc in enumerate(results, 1):
                    content = str(doc)
                    if content.startswith('data:image'):
                        print(f"\n{i}. 🖼️ Image (base64 data)")
                    elif '<table' in content.lower():
                        print(f"\n{i}. 📊 Table HTML")
                        print(f"   Preview: {content[:150]}...")
                    else:
                        print(f"\n{i}. 📝 Text")
                        print(f"   Content: {content[:200]}...")
                        
            except Exception as e:
                print(f"Search error: {e}")
                
        elif user_input.startswith('ask:'):
            question = user_input[4:].strip()
            print(f"\n🤖 AI Answer:")
            
            try:
                answer = qa_chain.invoke(question)
                print(answer)
            except Exception as e:
                print(f"QA error: {e}")
        else:
            print("Please use 'search: query' or 'ask: question' format")

# Step 1: Partition PDF
elements = step1_partition_pdf()

# Step 2: 
chunks = step2_chunk_elements(elements)

# Step 3: Categorize chunks
text_chunks, table_chunks, image_chunks = step3_categorize_chunks(chunks)

# Step 4:
texts, table_htmls, image_base64s = step4_extract_content(text_chunks, table_chunks, image_chunks)

# Step 5:
table_summaries, image_summaries = step5_create_summaries(table_htmls, image_base64s)

# Step 6:
retriever = step6_setup_retriever()

# Step 7: Add to retriever
step7_add_to_retriever(retriever, texts, table_summaries, table_htmls, image_summaries, image_base64s)

# Step 8: Create QA chain
qa_chain = step8_create_qa_chain(retriever)

# Step 9: Interactive mode
step9_interactive_search(retriever, qa_chain)