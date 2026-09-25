import os
import sys
import urllib.request
import zipfile
import shutil

DOCS_PAGE = "https://docs.python.org/3/download.html"
TARGET_DIR = "notes/python"
ZIP_PATH = "python-docs.zip"

def download_and_extract():
    print(f"Finding latest Python 3 Text Documentation from {DOCS_PAGE}...")
    import re
    try:
        html = urllib.request.urlopen(DOCS_PAGE).read().decode('utf-8')
        match = re.search(r'href=[\'"]([^\'"]+-docs-text\.zip)[\'"]', html)
        if not match:
            print("Failed to find the text docs zip link on the page.")
            sys.exit(1)
            
        docs_url = urllib.parse.urljoin(DOCS_PAGE, match.group(1))
        print(f"Downloading from {docs_url}...")
        urllib.request.urlretrieve(docs_url, ZIP_PATH)
    except Exception as e:
        print(f"Failed to download docs: {e}")
        sys.exit(1)

    print("Extracting...")
    os.makedirs(TARGET_DIR, exist_ok=True)
    
    with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
        # The zip contains a folder like 'python-3.12.x-docs-text'
        # We want to extract .txt files into notes/python and rename to .md so ingest.py picks them up
        for file_info in zip_ref.infolist():
            if file_info.filename.endswith('.txt'):
                # Extract file name without the top-level directory
                base_name = os.path.basename(file_info.filename)
                if not base_name: 
                    continue # Skip directories
                    
                source_file = zip_ref.open(file_info)
                
                # We save as .md so ingest.py automatically processes it
                target_file_path = os.path.join(TARGET_DIR, base_name.replace('.txt', '.md'))
                
                with open(target_file_path, 'wb') as target_file:
                    shutil.copyfileobj(source_file, target_file)

    print("Cleaning up...")
    os.remove(ZIP_PATH)
    
    print(f"Success! Python documentation text files have been saved to '{TARGET_DIR}'.")
    print(f"You can now run: python ingest.py --source-dir {TARGET_DIR} --api-url http://127.0.0.1:5000 --email you@example.com --password your-password")

if __name__ == "__main__":
    download_and_extract()
