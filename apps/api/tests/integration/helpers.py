from __future__ import annotations

import uuid

import httpx

from lekha.engines import gstin as G


def unique_gstin(state: str = "36") -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    seed = uuid.uuid4().int
    pan = "".join(letters[(seed >> (i * 5)) % 24] for i in range(3)) + "C" + "X"
    pan += f"{seed % 10000:04d}" + letters[seed % 24]
    return G.make(state, pan)


class Api:
    """Signed-in test client that sends the CSRF header automatically."""

    def __init__(self, client: httpx.AsyncClient):
        self.c = client
        self.csrf = ""

    async def signup(self, name="Asha Rao", email=None, firm="Rao & Associates", password="Str0ngPassword") -> dict:
        email = email or f"{uuid.uuid4().hex[:10]}@example.com"
        r = await self.c.post("/api/auth/signup", json={"name": name, "email": email, "password": password,
                                                        "firm_name": firm})
        assert r.status_code == 200, r.text
        data = r.json()
        self.csrf = data["csrf_token"]
        self.email = email
        return data

    def _h(self) -> dict:
        return {"x-csrf-token": self.csrf}

    async def get(self, url, **kw):
        return await self.c.get(url, **kw)

    async def post(self, url, **kw):
        return await self.c.post(url, headers=self._h(), **kw)

    async def patch(self, url, **kw):
        return await self.c.patch(url, headers=self._h(), **kw)

    async def delete(self, url, **kw):
        return await self.c.delete(url, headers=self._h(), **kw)

    async def client(self, name="Deccan FurnitureWorks", gstin=None) -> dict:
        gstin = gstin or unique_gstin("36")
        r = await self.post("/api/clients", json={"name": name, "gstins": [{"gstin": gstin}]})
        assert r.status_code == 200, r.text
        return r.json()
