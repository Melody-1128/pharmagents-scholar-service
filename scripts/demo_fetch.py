import asyncio
import json

import httpx


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as client:
        response = await client.post("/scholar/fetch", json={
            "pmcid": "PMC9715446",
            "max_chars": 50_000,
        })
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
