import sys, time, logging
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S", force=True)

# warmup
from embedding.embedder import get_embedder
emb = get_embedder()
wv = emb.encode_query(["warmup_init"])[0]
from memory_engine import get_engine
engine = get_engine()
engine.vector_index.search_vec(wv, top_k=1)
print('engine ready', flush=True)

db = engine.db
user_msg = '测试'

print('1. format_recent_chat...', flush=True)
from prompt.builder import format_recent_chat
rc = format_recent_chat(db, n=6)
print(f'   ok', flush=True)

print('2. get_cloud_and_candidates...', flush=True)
t0=time.time()
cloud, cand, qv = engine.backgrounder.get_cloud_and_candidates(user_msg, rc[-200:] if rc else '', 119)
print(f'   ok: {time.time()-t0:.1f}s', flush=True)

print('3. get_relevant_memories...', flush=True)
from memory_engine.retrieve import get_relevant_memories
memories = get_relevant_memories(user_msg, prefetched=cand)
print(f'   ok: {len(memories)}', flush=True)

print('4. get_recent_emotions...', flush=True)
from memory_engine.emotion import get_recent_emotions
emotions = get_recent_emotions(db)
print(f'   ok', flush=True)

print('5. get_current_state...', flush=True)
from memory_engine.state import get_current_state
state = get_current_state(db)
print(f'   ok', flush=True)

print('6. senses snapshot...', flush=True)
from memory_engine.config import SENSE_ENABLE
if SENSE_ENABLE:
    from memory_engine.senses import get_sense_collector
    sc = get_sense_collector()
    snap = sc.snapshot(msg_count=0)
    print(f'   ok', flush=True)

print('7. life stream...', flush=True)
try:
    from memory_engine.life_stream import get_life_stream_engine
    eng = get_life_stream_engine()
    ln = eng._get_activity_status(0.6)
    print(f'   ok', flush=True)
except Exception as e:
    print(f'   err: {e}', flush=True)

print('8. idle thoughts...', flush=True)
try:
    from memory_engine.idle_thoughts import get_idle_thought_store
    active = get_idle_thought_store().get_active()
    print(f'   ok: {len(active) if active else 0}', flush=True)
except Exception as e:
    print(f'   err: {e}', flush=True)

print('9. breathing state...', flush=True)
from memory_engine.state import get_breathing_state
st = get_breathing_state()
st.calc_depth_index()
print(f'   ok', flush=True)

print('10. mood hint...', flush=True)
from prompt.builder import _get_mood_hint
_get_mood_hint(db, memories)
print(f'   ok', flush=True)

print('11. preferences...', flush=True)
from memory_engine.preferences import get_preferences
get_preferences()
print(f'   ok', flush=True)

print('12. ALL DONE', flush=True)
