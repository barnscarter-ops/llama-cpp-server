import urllib.request
import json
import time

def wait_for_health():
    print("Waiting for model to load...")
    while True:
        try:
            req = urllib.request.Request("http://127.0.0.1:8085/v1/health")
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    if data.get("status") == "ok":
                        print("Model is ready!")
                        return
        except Exception:
            pass
        time.sleep(2)

def test_chat():
    prompt = "Write a python function to compute the 10th fibonacci number. Ensure the function is highly optimized."
    url = "http://127.0.0.1:8085/v1/chat/completions"
    data = {
        "model": "deepseek-r1-14b",
        "messages": [
            {"role": "system", "content": "You are a highly capable AI assistant that uses reasoning."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.6,
        "max_tokens": 512,
        "stream": False
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    start_time = time.time()
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            end_time = time.time()
            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            gen_tokens = usage.get("completion_tokens", 0)
            
            # Extract timing from llama.cpp custom fields if available
            timings = result.get("timings", {})
            if "predicted_per_second" in timings:
                tps = timings["predicted_per_second"]
                total_time = timings["predicted_ms"] / 1000.0
            else:
                total_time = end_time - start_time
                tps = gen_tokens / total_time if total_time > 0 else 0
            
            print("--- Output ---")
            print(content)
            print("--------------")
            print(f"Time taken (wall clock): {end_time - start_time:.2f}s")
            print(f"Prompt tokens: {prompt_tokens}")
            print(f"Generated tokens: {gen_tokens}")
            print(f"Generation speed: {tps:.2f} tokens/s")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    wait_for_health()
    test_chat()
