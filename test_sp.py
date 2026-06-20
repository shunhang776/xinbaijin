import sys, time
sys.stdout.reconfigure(encoding='utf-8')
print('1. warmup...')
from embedding.embedder import get_embedder
emb = get_embedder()
wv = emb.encode_query(["warmup_init"])[0]
print('2. engine...')
from memory_engine import get_engine
engine = get_engine()
engine.vector_index.search_vec(wv, top_k=1)
print('3. search_sync...')
t0 = time.time()
results, qv = engine.retrieve.search_sync('测试查询')
print(f'   done: {time.time()-t0:.1f}s, results={len(results)}')
for r in results[:3]:
    print(f'   [{r.get("_source","?")}] {r.get("content","")[:80]}')
print('4. ALL DONE')
