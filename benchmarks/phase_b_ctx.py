import subprocess, time, os, json, urllib.request

BUILD = r'C:\Workspace\Infrastructure\llama-cpp-server-cuda-b10488'
MODELS = r'C:\Workspace\Infrastructure\llama-cpp-server\models'
OUT = r'C:\Workspace\Infrastructure\llama-cpp-server\benchmarks'
PORT = 8099

SPECS = {
    'glm': dict(file='GLM-4.7-Flash-UD-IQ3_XXS.gguf', ladder=[32768, 65536, 98304, 131072, 202752],
                extra=['--repeat-penalty', '1.0', '--min-p', '0.01']),
    'cohere': dict(file='North-Mini-Code-1.0-UD-IQ3_XXS.gguf', ladder=[32768, 65536, 98304, 131072, 196608, 262144],
                   extra=[]),
    'gemma': dict(file='gemma-4-26B-A4B-it-UD-IQ4_XS.gguf', ladder=[32768, 65536, 98304, 131072],
                  extra=[]),
    'qwen-control': dict(file='Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf', ladder=[32768, 65536, 98304, 131072],
                         extra=[]),
}

def vram():
    return subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader'],
                          capture_output=True, text=True).stdout.strip()

def ladder(tag):
    spec = SPECS[tag]
    model = os.path.join(MODELS, spec['file'])
    results = {}
    for ctx in spec['ladder']:
        cmd = [os.path.join(BUILD, 'llama-server.exe'),
               '--model', model, '--host', '127.0.0.1', '--port', str(PORT),
               '--gpu-layers', '99', '--ctx-size', str(ctx), '--flash-attn', 'on',
               '--cache-type-k', 'f16', '--cache-type-v', 'f16',
               '--parallel', '1', '--ubatch-size', '1024', '--batch-size', '2048',
               '--jinja', '--no-warmup'] + spec['extra']
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = False
        for _ in range(50):
            time.sleep(2)
            try:
                if urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health', timeout=2).status == 200:
                    up = True
                    break
            except Exception:
                if p.poll() is not None:
                    break
        v = vram()
        results[ctx] = dict(up=up, vram=v)
        print(f'{tag} ctx {ctx:>7}: {"LOADED" if up else "FAILED"}  VRAM={v}', flush=True)
        p.terminate()
        try: p.wait(10)
        except Exception: p.kill()
        time.sleep(8)
        if not up:
            break
    json.dump(results, open(os.path.join(OUT, f'finalist-{tag}-ctx.json'), 'w'), indent=1)

import sys
for tag in sys.argv[1:] or SPECS:
    ladder(tag)
print('PHASE-B-COMPLETE', flush=True)
