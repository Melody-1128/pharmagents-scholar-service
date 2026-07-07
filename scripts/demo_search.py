import asyncio
import json

import httpx


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as client:
        response = await client.post("/scholar/search", json={
            "query": "KRAS G12C sotorasib resistance mechanisms",
            "max_results": 10,
            "from_year": 2020,
        })
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
