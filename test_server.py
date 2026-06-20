import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[调试] lifespan 开始执行")
    yield
    print("[调试] lifespan 结束执行")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(
        "test_server:app",
        host="127.0.0.1",
        port=18000,
        log_level="debug",
        reload=False,
        workers=1,
    )
