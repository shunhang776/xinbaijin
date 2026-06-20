from fastapi import FastAPI
import uvicorn
import time

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True}

@app.get("/test")
def test():
    time.sleep(1)
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
