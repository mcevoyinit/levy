"""Example: a paid API with three endpoints at different prices."""

from fastapi import FastAPI, Request
from levy import levy, LevyConfig
from levy.decorator import configure

# Configure Levy (in production, use env vars)
configure(LevyConfig(
    recipient="0xB02abaA5FD4Caf4E16b7583232cddbE43BeC66AF",
    secret_key="demo-secret-key",
    chain_id=42431,  # Moderato testnet
    rpc_url="https://rpc.moderato.tempo.xyz",
))

app = FastAPI(title="Levy Demo")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "levy-demo",
            "endpoints": {"/search": "$0.01", "/analyze": "$0.05", "/premium": "$0.25"}}


@app.get("/search")
@levy("0.01", description="Web search - $0.01 per query")
async def search(request: Request, query: str = "tempo", *, credential, receipt):
    return {
        "query": query,
        "results": [
            {"title": f"Result for: {query}", "score": 0.95},
            {"title": f"Related: {query} ecosystem", "score": 0.82},
        ],
        "paid": True,
        "price": "$0.01",
        "receipt": receipt.reference,
        "payer": credential.source,
    }


@app.get("/analyze")
@levy("0.05", description="Deep analysis - $0.05 per request")
async def analyze(request: Request, topic: str = "stablecoins", *, credential, receipt):
    return {
        "topic": topic,
        "analysis": f"Deep analysis of {topic} on Tempo ecosystem.",
        "confidence": 0.91,
        "paid": True,
        "price": "$0.05",
        "receipt": receipt.reference,
    }


@app.get("/premium")
@levy("0.25", description="Premium intelligence - $0.25 per request")
async def premium(request: Request, *, credential, receipt):
    return {
        "tier": "premium",
        "data": "Premium market intelligence report.",
        "paid": True,
        "price": "$0.25",
        "receipt": receipt.reference,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8402)
