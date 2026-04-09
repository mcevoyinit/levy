# levy

> *lev·y* — to impose or collect a charge by authority.

One decorator to monetize any FastAPI endpoint via [Tempo](https://tempo.xyz)/MPP.

## How it works

```python
from fastapi import FastAPI, Request
from levy import levy

app = FastAPI()

@app.get("/search")
@levy("0.01")
async def search(request: Request, query: str, *, credential, receipt):
    results = do_search(query)
    return {"results": results, "receipt": receipt.reference}
```

That's it. Callers without payment credentials get a `402 Payment Required` response with a `WWW-Authenticate: Payment` challenge. Callers who pay get through.

On the client side, pympp's `Client` handles the 402 automatically:

```python
from mpp.client import Client
from mpp.methods.tempo import tempo, TempoAccount, ChargeIntent

async with Client(methods=[tempo(...)]) as client:
    r = await client.get("https://your-api.com/search?query=tempo")
    print(r.json())  # paid automatically, got results
```

## What happens under the hood

```
Client                          Server (@levy endpoint)
  |                                |
  |--- GET /search?query=foo ----->|
  |                                | no auth header → Mpp.charge(None)
  |<--- 402 + WWW-Authenticate ---|  returns Challenge
  |                                |
  | parse challenge, sign payment  |
  |                                |
  |--- GET /search + Authorization>|
  |                                | has auth → Mpp.charge(auth)
  |                                | verifies payment on-chain
  |<--- 200 + results ------------|  returns (Credential, Receipt)
  |                                |
```

## Configuration

Via environment variables:

```bash
export LEVY_RECIPIENT=0xYourAddress    # who receives payments
export LEVY_SECRET_KEY=your-secret     # HMAC secret for challenge IDs
export LEVY_CHAIN_ID=4217              # Tempo mainnet (42431 for testnet)
export LEVY_CURRENCY=0x20C0000000000000000000000000000000000000  # pathUSD
```

Or in code:

```python
from levy import LevyConfig
from levy.decorator import configure

configure(LevyConfig(
    recipient="0xYourAddress",
    secret_key="your-secret",
    chain_id=4217,
))
```

## Multiple price points

```python
@app.get("/basic")
@levy("0.01", description="Basic search")
async def basic(request: Request, *, credential, receipt):
    ...

@app.get("/premium")
@levy("0.25", description="Premium analysis")
async def premium(request: Request, *, credential, receipt):
    ...
```

## Install

```bash
pip install levy
```

## Run the example

```bash
cd examples
python server.py  # starts on :8402
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Related

- [Salvo](https://github.com/mcevoyinit/salvo) — atomic swap+pay (batch multiple payments in one tx)
- [Maestro](https://github.com/mcevoyinit/maestro) — zero-gas agent orchestrator (session keys, fee sponsorship)
- [Parley](https://github.com/mcevoyinit/parley) — tiered pricing for MPP endpoints

## License

MIT
