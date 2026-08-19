import subprocess, json, time, os, sys
BENCH = r'C:\Workspace\Infrastructure\llama-cpp-server-cuda-b10488\llama-bench.exe'
MODELS = r'C:\Workspace\Infrastructure\llama-cpp-server\models'
OUT = r'C:\Workspace\Infrastructure\llama-cpp-server\benchmarks'

def matrix(tag, fname):
    out = os.path.join(OUT, f'finalist-{tag}-matrix.json')
    cmd = [BENCH, '-m', os.path.join(MODELS, fname), '-ngl','99','-fa','1',
           '-ctk','f16,q8_0','-ctv','f16,q8_0','-ub','512,1024,2048','-b','2048',
           '-p','2048','-n','128','-r','2','-o','json']
    print(f'=== {tag} ===', flush=True)
    t0=time.time()
    with open(out,'w') as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True, timeout=1500)
    if p.returncode != 0:
        print(f'{tag} rc={p.returncode}', p.stderr[-300:]); return
    try: rows = json.load(open(out))
    except Exception as e:
        print(f'{tag} parse fail', e); return
    best_pp = max((r for r in rows if r['n_prompt']>0), key=lambda r: r['avg_ts'])
    best_tg = max((r for r in rows if r['n_gen']>0), key=lambda r: r['avg_ts'])
    for r in rows:
        kind = f"pp{r['n_prompt']}" if r['n_prompt'] else f"tg{r['n_gen']}"
        print(f"  {kind:>6} kv={r['type_v']:>5} ub={r['n_ubatch']:>4}: {r['avg_ts']:8.1f} t/s", flush=True)
    print(f"  BEST pp {best_pp['avg_ts']:.0f} (kv={best_pp['type_v']},ub={best_pp['n_ubatch']}) | BEST tg {best_tg['avg_ts']:.1f} (kv={best_tg['type_v']},ub={best_tg['n_ubatch']}) [{time.time()-t0:.0f}s]", flush=True)

for tag, f in [('glm','GLM-4.7-Flash-UD-IQ3_XXS.gguf'),
               ('cohere','North-Mini-Code-1.0-UD-IQ3_XXS.gguf'),
               ('gemma','gemma-4-26B-A4B-it-UD-IQ4_XS.gguf'),
               ('qwen-control','Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf')]:
    try:
        matrix(tag, f)
    except Exception as e:
        print(f'{tag} EXCEPTION {e}', flush=True)
print('PHASE-A-COMPLETE', flush=True)
