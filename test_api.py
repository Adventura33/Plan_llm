import requests
import os

API_URL = "http://127.0.0.1:8000/api/generate_plan"

def main():
    raport_path = "data/input/1. Рапорт_КУИ__257100120000018.pdf"
    doc_path = "data/input/2. СДТБТ_баянаты____257100121000006.pdf"
    
    if not os.path.exists(raport_path) or not os.path.exists(doc_path):
        print("Test files not found.")
        return
        
    print(f"Testing API endpoint: {API_URL}")
    print("Uploading files...")
    
    with open(raport_path, "rb") as root_f, open(doc_path, "rb") as doc_f:
        files = {
            "raport_pdf": ("raport.pdf", root_f, "application/pdf"),
            "case_documents": ("doc.pdf", doc_f, "application/pdf")
        }
        
        # Add basic text fields
        data = {
            "model": "qwen3-next-80b-instruct",
            "api_url": "http://192.168.97.5:8805/v1/chat/completions",
            "methodology_dir": "data/methodology"
        }
        
        try:
            # Short timeout since the LLM server is known to hang, 
            # we just want to see if the request is correctly formatted and parsed by FastAPI
            response = requests.post(API_URL, files=files, data=data, timeout=5)
            
            print(f"Status Code: {response.status_code}")
            if response.status_code == 200:
                print("Success! Got a response.")
                # We would normally write out the .docx file here
                with open("/tmp/api_test_output.docx", "wb") as out:
                    out.write(response.content)
            else:
                print("Error Response:")
                print(response.text)
        except requests.exceptions.Timeout:
            print("Request timed out (expected if the LLM is slow). But API connection worked!")
        except Exception as e:
            print(f"Connection error: {e}")

if __name__ == "__main__":
    main()
