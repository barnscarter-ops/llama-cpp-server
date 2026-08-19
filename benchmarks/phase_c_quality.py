import subprocess, time, os, json, sys, urllib.request

BUILD = r'C:\Workspace\Infrastructure\llama-cpp-server-cuda-b10488'
ROOT = r'C:\Workspace\Infrastructure\llama-cpp-server'
MODELS = os.path.join(ROOT, 'models')
OUT = os.path.join(ROOT, 'benchmarks')
PORT = 8099
URL = f'http://127.0.0.1:{PORT}'

# tuned Phase-A winners (f16 KV everywhere; q8_0 KV is a net loss on this GPU)
SPECS = {
    'glm': dict(file='GLM-4.7-Flash-UD-IQ3_XXS.gguf', ctx=131072, ub=1024,
                extra=['--repeat-penalty', '1.0', '--min-p', '0.01', '--reasoning', 'off']),
    'cohere': dict(file='North-Mini-Code-1.0-UD-IQ3_XXS.gguf', ctx=131072, ub=2048,
                   extra=['--reasoning', 'off']),
    'gemma': dict(file='gemma-4-26B-A4B-it-UD-IQ4_XS.gguf', ctx=65536, ub=1024, extra=[]),
    'qwen-control': dict(file='Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf', ctx=131072, ub=2048,
                         extra=['--reasoning', 'off']),
}

def vram():
    return subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                          capture_output=True, text=True).stdout.strip()

def run(tag):
    spec = SPECS[tag]
    model = os.path.join(MODELS, spec['file'])
    cmd = [os.path.join(BUILD, 'llama-server.exe'),
           '--model', model, '--host', '127.0.0.1', '--port', str(PORT),
           '--gpu-layers', '99', '--ctx-size', str(spec['ctx']), '--flash-attn', 'on',
           '--cache-type-k', 'f16', '--cache-type-v', 'f16',
           '--ubatch-size', str(spec['ub']), '--batch-size', '2048',
           '--alias', tag, '--cont-batching', '--jinja'] + spec['extra']
    log = os.path.join(OUT, f'finalist-{tag}-server.log')
    lf = open(log, 'w')
    p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
    try:
        up = False
        for _ in range(90):
            time.sleep(2)
            try:
                if urllib.request.urlopen(f'{URL}/health', timeout=2).status == 200:
                    up = True; break
            except Exception:
                if p.poll() is not None: break
        if not up:
            print(f'{tag}: SERVER FAILED', flush=True); return
        print(f'{tag}: server up, VRAM={vram()}', flush=True)
        out_json = os.path.join(OUT, f'finalist-{tag}-quality.json')
        qc = subprocess.run([sys.executable, os.path.join(OUT, 'bench-coding.py'),
                             '--url', f'{URL}/v1/chat/completions', '--model', tag, '--out', out_json],
                            capture_output=True, text=True, timeout=1800)
        print(qc.stdout[-2500:], flush=True)
        if qc.returncode != 0:
            print(f'{tag} bench-coding rc={qc.returncode}', qc.stderr[-500:], flush=True)
        print(f'{tag}: post-bench VRAM={vram()}', flush=True)
    finally:
        p.terminate()
        try: p.wait(15)
        except Exception: p.kill()
        lf.close()
        time.sleep(8)

for tag in sys.argv[1:] or SPECS:
    run(tag)
print('PHASE-C-COMPLETE', flush=True)
