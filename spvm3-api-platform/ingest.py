import os
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import logging

try:
    import requests
    # pyrefly: ignore [missing-import]
    from bs4 import BeautifulSoup
except ImportError:
    print("Please install required dependencies first:\npip install requests beautifulsoup4")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("ingest")

def strip_html(html_content: str) -> str:
    """Strip HTML tags securely without executing scripts."""
    soup = BeautifulSoup(html_content, "html.parser")
    # Remove script and style elements entirely
    for script in soup(["script", "style"]):
        script.decompose()
    return soup.get_text(separator=" ", strip=True)

def strip_markdown(md_content: str) -> str:
    """Basic stripping for Markdown markup."""
    md = re.sub(r'#+\s+', '', md_content)
    md = re.sub(r'\*\*(.*?)\*\*', r'\1', md)
    md = re.sub(r'\*(.*?)\*', r'\1', md)
    md = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', md)
    md = re.sub(r'`(.*?)`', r'\1', md) # inline code
    return md.strip()

def strip_wiki(wiki_content: str) -> str:
    """Basic stripping for Wikipedia/MediaWiki markup."""
    # Remove templates {{...}}
    wiki = re.sub(r'{{.*?}}', '', wiki_content, flags=re.DOTALL)
    # Remove links [[Link]] or [[Link|Text]]
    wiki = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', wiki)
    # Remove references <ref>...</ref>
    wiki = re.sub(r'<ref.*?>.*?</ref>', '', wiki, flags=re.DOTALL)
    # Remove headings == Heading ==
    wiki = re.sub(r'==+\s*(.*?)\s*==+', r'\1', wiki)
    return wiki.strip()

def chunk_text(text: str, min_words=200, max_words=500) -> list:
    """Splits text into chunks of roughly min_words to max_words."""
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = []
    current_word_count = 0
    
    for p in paragraphs:
        words = p.split()
        if not words:
            continue
            
        # If adding this paragraph exceeds max, save current chunk and start a new one
        if current_word_count + len(words) > max_words and current_word_count >= min_words:
            chunks.append(" ".join(current_chunk))
            current_chunk = words
            current_word_count = len(words)
        else:
            current_chunk.extend(words)
            current_word_count += len(words)
            
        # If a single paragraph is too large on its own, split it aggressively
        while current_word_count > max_words:
            chunks.append(" ".join(current_chunk[:max_words]))
            current_chunk = current_chunk[max_words:]
            current_word_count = len(current_chunk)
            
    # Include the last chunk if it meets at least half the minimum length requirement
    if current_chunk and current_word_count >= (min_words / 2):
        chunks.append(" ".join(current_chunk))
        
    return chunks

def get_fingerprint(text: str) -> set:
    """Generate a trigram set for Jaccard similarity comparison."""
    words = text.lower().split()
    return set(" ".join(words[i:i+3]) for i in range(len(words)-2))

def is_near_identical(fp1: set, fp2: set, threshold=0.85) -> bool:
    """Check if two chunk fingerprints are near-identical."""
    if not fp1 or not fp2: 
        return False
    intersection = len(fp1.intersection(fp2))
    union = len(fp1.union(fp2))
    return (intersection / union) > threshold if union else False

def parse_xml(filepath: str):
    """Parses Stack Exchange XML or Wikipedia XML dumps."""
    try:
        # Use iterparse to handle massive XML files without loading entirely into memory
        context = ET.iterparse(filepath, events=('end',))
        for event, elem in context:
            if elem.tag == 'row' and 'Body' in elem.attrib:
                # Looks like a Stack Exchange dump
                title = elem.attrib.get('Title', 'Stack Exchange Post')
                body = strip_html(elem.attrib.get('Body', ''))
                
                tags_str = elem.attrib.get('Tags', '').replace('<', ' ').replace('>', ' ')
                tags = [t for t in tags_str.split() if t]
                category = tags[0] if tags else 'general'
                
                yield {
                    'title': title,
                    'content': body,
                    'category': category,
                    'attribution_tags': ['stackexchange', 'cc-by-sa'] + tags[:2]
                }
                elem.clear() # Free memory
                
            elif elem.tag.endswith('page'):
                # Looks like a MediaWiki/Wikipedia dump
                ns = elem.tag[:-4] # Extract namespace prefix e.g. {http://www.mediawiki.org/xml/export-0.10/}
                title_elem = elem.find(f'{ns}title')
                text_elem = elem.find(f'.//{ns}text')
                
                if title_elem is not None and text_elem is not None and text_elem.text:
                    title = title_elem.text
                    body = strip_wiki(text_elem.text)
                    yield {
                        'title': title,
                        'content': body,
                        'category': 'wikipedia',
                        'attribution_tags': ['wikipedia', 'cc-by-sa']
                    }
                elem.clear() # Free memory
                
    except Exception as e:
        logger.error(f"Failed to parse XML {filepath}: {e}")

def parse_md(filepath: str):
    """Parses standard Markdown files."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Try to infer title from the first heading
        title_match = re.search(r'^#\s+(.+)$', content, flags=re.MULTILINE)
        title = title_match.group(1) if title_match else os.path.basename(filepath)
        
        # Infer category from parent folder name
        category = os.path.basename(os.path.dirname(filepath))
        
        tags = ['markdown']
        # Respect MDN license explicitly if present in path/name
        if 'mdn' in filepath.lower():
            tags.extend(['mdn', 'cc-by-sa'])
            
        yield {
            'title': title,
            'content': strip_markdown(content),
            'category': category if category and category != '.' else 'documentation',
            'attribution_tags': tags
        }
    except Exception as e:
        logger.error(f"Failed to parse Markdown {filepath}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Ingest knowledge sources into SPVM3 Platform")
    parser.add_argument("--source-dir", required=True, help="Folder containing source XML or MD files")
    parser.add_argument("--api-url", required=True, help="Base URL of SPVM3 platform (e.g. http://localhost:5000)")
    parser.add_argument("--email", required=True, help="Admin email for login")
    parser.add_argument("--password", required=True, help="Admin password for login")
    args = parser.parse_args()

    session = requests.Session()
    
    # Authenticate as admin
    login_url = urljoin(args.api_url, "/auth/login")
    resp = session.post(login_url, json={"email": args.email, "password": args.password})
    
    if not resp.ok or not resp.json().get("success"):
        logger.error(f"Login failed: {resp.text}")
        sys.exit(1)
        
    logger.info("Logged in successfully. Starting ingestion process...")
    import_url = urljoin(args.api_url, "/admin/api/knowledge/import")
    
    # Store fingerprints for deduplication
    all_fingerprints = []
    batch = []
    
    def flush_batch():
        if not batch:
            return
        r = session.post(import_url, json=batch)
        if r.ok:
            logger.info(f"Successfully POSTed a batch of {len(batch)} chunks.")
        else:
            logger.error(f"Failed to import batch: {r.text}")
        batch.clear()

    for root, _, files in os.walk(args.source_dir):
        for file in files:
            filepath = os.path.join(root, file)
            generator = None
            
            if file.endswith('.xml'):
                generator = parse_xml(filepath)
            elif file.endswith('.md'):
                generator = parse_md(filepath)
                
            if not generator:
                continue
                
            logger.info(f"Processing source file: {filepath}")
            
            for doc in generator:
                chunks = chunk_text(doc['content'])
                
                for i, chunk in enumerate(chunks):
                    fp = get_fingerprint(chunk)
                    
                    # Deduplicate near-identical chunks using Jaccard Similarity
                    is_dup = False
                    for existing_fp in all_fingerprints:
                        if is_near_identical(fp, existing_fp):
                            is_dup = True
                            break
                    if is_dup:
                        continue
                        
                    all_fingerprints.append(fp)
                    
                    # Append 'Part N' if document was split into multiple chunks
                    title = f"{doc['title']} - Part {i+1}" if len(chunks) > 1 else doc['title']
                    
                    entry = {
                        "category": doc['category'][:60],  # DB limit is likely 60
                        "title": title[:200],              # Safely truncate titles
                        "content": chunk,
                        "tags": doc['attribution_tags']
                    }
                    batch.append(entry)
                    
                    if len(batch) >= 500:
                        flush_batch()
                        
    # Flush any remaining items
    flush_batch()
    logger.info("Ingestion complete.")

if __name__ == "__main__":
    main()
