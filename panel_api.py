import httpx
from typing import Any, Optional


class PanelAPI:
    def __init__(self, base_url: str, api_path: str, username: str, password: str, api_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_path = api_path.rstrip("/")
        self.username = username
        self.password = password
        self.api_token = api_token
        self.session_cookie: Optional[str] = None
        self.csrf_token: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}{self.api_path}{endpoint}"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        elif self.session_cookie:
            headers["Cookie"] = self.session_cookie
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(verify=False, timeout=30)
        return self._client

    async def login(self) -> bool:
        if self.api_token:
            return True
        try:
            client = await self._get_client()
            print(f"[PanelAPI] Attempting login to: {self.base_url}{self.api_path}/login")
            print(f"[PanelAPI] Username: {self.username}")

            # First, get CSRF token and set session cookie
            try:
                csrf_resp = await client.get(
                    f"{self.base_url}{self.api_path}/csrf-token",
                )
                print(f"[PanelAPI] CSRF response status: {csrf_resp.status_code}")
                set_cookie = csrf_resp.headers.get("set-cookie", "")
                if set_cookie:
                    self.session_cookie = set_cookie.split(";")[0]
                    print(f"[PanelAPI] Got session cookie from CSRF: {self.session_cookie[:50]}...")
                csrf_data = csrf_resp.json()
                if csrf_data.get("success"):
                    self.csrf_token = csrf_data.get("obj", "")
                    print(f"[PanelAPI] Got CSRF token: {self.csrf_token[:30]}...")
            except Exception as e:
                print(f"[PanelAPI] CSRF token fetch failed: {e}")

            # Build headers for login
            headers = {"Content-Type": "application/json"}
            if self.session_cookie:
                headers["Cookie"] = self.session_cookie
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token

            resp = await client.post(
                f"{self.base_url}{self.api_path}/login",
                json={
                    "username": self.username,
                    "password": self.password,
                    "twoFactorCode": "",
                },
                headers=headers,
            )
            print(f"[PanelAPI] Login response status: {resp.status_code}")
            print(f"[PanelAPI] Login response headers: {dict(resp.headers)}")

            try:
                data = resp.json()
                print(f"[PanelAPI] Login response body: {data}")
            except:
                print(f"[PanelAPI] Login response text: {resp.text[:500]}")
                return False

            if data.get("success"):
                set_cookie = resp.headers.get("set-cookie", "")
                if set_cookie:
                    new_cookie = set_cookie.split(";")[0]
                    if new_cookie:
                        self.session_cookie = new_cookie
                print(f"[PanelAPI] Login successful! Cookie: {self.session_cookie[:50] if self.session_cookie else 'None'}...")
                return True
            else:
                print(f"[PanelAPI] Login failed: {data}")
                return False
        except Exception as e:
            print(f"[PanelAPI] Login error: {e}")
            return False

    async def _get(self, endpoint: str) -> dict:
        try:
            client = await self._get_client()
            url = self._url(endpoint)
            print(f"[PanelAPI] GET {url}")
            resp = await client.get(url, headers=self._headers())
            print(f"[PanelAPI] Response status: {resp.status_code}")
            try:
                resp_data = resp.json()
                print(f"[PanelAPI] Response body: {resp_data}")
            except:
                print(f"[PanelAPI] Response text: {resp.text[:500]}")
                resp_data = {"success": False, "msg": resp.text[:200]}
            if resp.status_code == 404:
                print(f"[PanelAPI] GET {endpoint} -> 404, attempting re-login...")
                if await self.login():
                    resp = await client.get(self._url(endpoint), headers=self._headers())
                    print(f"[PanelAPI] Retry response status: {resp.status_code}")
                    try:
                        resp_data = resp.json()
                        print(f"[PanelAPI] Retry response body: {resp_data}")
                    except:
                        resp_data = {"success": False, "msg": resp.text[:200]}
            return resp_data
        except Exception as e:
            print(f"[PanelAPI] GET {endpoint} error: {e}")
            return {"success": False, "msg": str(e)}

    async def _post(self, endpoint: str, data: Any = None) -> dict:
        try:
            client = await self._get_client()
            url = self._url(endpoint)
            print(f"[PanelAPI] POST {url}")
            print(f"[PanelAPI] Headers: {self._headers()}")
            print(f"[PanelAPI] Data: {data}")
            resp = await client.post(
                url,
                json=data,
                headers=self._headers(),
            )
            print(f"[PanelAPI] Response status: {resp.status_code}")
            try:
                resp_data = resp.json()
                print(f"[PanelAPI] Response body: {resp_data}")
            except:
                print(f"[PanelAPI] Response text: {resp.text[:500]}")
                resp_data = {"success": False, "msg": resp.text[:200]}
            if resp.status_code == 404:
                print(f"[PanelAPI] POST {endpoint} -> 404, attempting re-login...")
                if await self.login():
                    resp = await client.post(
                        self._url(endpoint),
                        json=data,
                        headers=self._headers(),
                    )
                    print(f"[PanelAPI] Retry response status: {resp.status_code}")
                    try:
                        resp_data = resp.json()
                        print(f"[PanelAPI] Retry response body: {resp_data}")
                    except:
                        resp_data = {"success": False, "msg": resp.text[:200]}
            return resp_data
        except Exception as e:
            print(f"[PanelAPI] POST {endpoint} error: {e}")
            return {"success": False, "msg": str(e)}

    # ==================== SERVER ====================
    async def server_status(self) -> dict:
        return await self._get("/panel/api/server/status")

    async def xray_version(self) -> dict:
        return await self._get("/panel/api/server/getXrayVersion")

    async def xray_config(self) -> dict:
        return await self._get("/panel/api/server/getConfigJson")

    async def restart_xray(self) -> dict:
        return await self._post("/panel/api/server/restartXrayService")

    async def stop_xray(self) -> dict:
        return await self._post("/panel/api/server/stopXrayService")

    async def panel_logs(self, count: int = 50) -> dict:
        return await self._post(f"/panel/api/server/logs/{count}")

    async def xray_logs(self, count: int = 50) -> dict:
        return await self._post(f"/panel/api/server/xraylogs/{count}")

    # ==================== INBOUNDS ====================
    async def inbounds_list(self) -> dict:
        return await self._get("/panel/api/inbounds/list")

    async def inbounds_slim(self) -> dict:
        return await self._get("/panel/api/inbounds/list/slim")

    async def inbound_get(self, inbound_id: int) -> dict:
        return await self._get(f"/panel/api/inbounds/get/{inbound_id}")

    async def inbound_add(self, data: dict) -> dict:
        return await self._post("/panel/api/inbounds/add", data)

    async def inbound_update(self, inbound_id: int, data: dict) -> dict:
        return await self._post(f"/panel/api/inbounds/update/{inbound_id}", data)

    async def inbound_delete(self, inbound_id: int) -> dict:
        return await self._post(f"/panel/api/inbounds/del/{inbound_id}")

    async def inbound_set_enable(self, inbound_id: int, enable: bool) -> dict:
        return await self._post(f"/panel/api/inbounds/setEnable/{inbound_id}", {"enable": enable})

    async def inbound_reset_traffic(self, inbound_id: int) -> dict:
        return await self._post(f"/panel/api/inbounds/{inbound_id}/resetTraffic")

    async def inbound_all_links(self) -> dict:
        return await self._get("/panel/api/inbounds/allLinks")

    async def inbound_options(self) -> dict:
        return await self._get("/panel/api/inbounds/options")

    # ==================== CLIENTS ====================
    async def clients_list(self) -> dict:
        return await self._get("/panel/api/clients/list")

    async def client_get(self, email: str) -> dict:
        return await self._get(f"/panel/api/clients/get/{email}")

    async def client_add(self, data: dict) -> dict:
        return await self._post("/panel/api/clients/add", data)

    async def client_update(self, email: str, data: dict) -> dict:
        return await self._post(f"/panel/api/clients/update/{email}", data)

    async def client_delete(self, email: str) -> dict:
        return await self._post(f"/panel/api/clients/del/{email}")

    async def client_links(self, email: str) -> dict:
        return await self._get(f"/panel/api/clients/links/{email}")

    async def client_reset_traffic(self, email: str) -> dict:
        return await self._post(f"/panel/api/clients/resetTraffic/{email}")

    async def client_attach(self, email: str, inbound_ids: list) -> dict:
        return await self._post(f"/panel/api/clients/{email}/attach", {"inboundIds": inbound_ids})

    async def client_detach(self, email: str, inbound_ids: list) -> dict:
        return await self._post(f"/panel/api/clients/{email}/detach", {"inboundIds": inbound_ids})

    async def client_online(self) -> dict:
        return await self._post("/panel/api/clients/onlines")

    async def clients_bulk_enable(self, emails: list) -> dict:
        return await self._post("/panel/api/clients/bulkEnable", {"emails": emails})

    async def clients_bulk_disable(self, emails: list) -> dict:
        return await self._post("/panel/api/clients/bulkDisable", {"emails": emails})

    async def clients_bulk_delete(self, emails: list) -> dict:
        return await self._post("/panel/api/clients/bulkDel", {"emails": emails})

    # ==================== NODES ====================
    async def nodes_list(self) -> dict:
        return await self._get("/panel/api/nodes/list")

    async def node_get(self, node_id: int) -> dict:
        return await self._get(f"/panel/api/nodes/get/{node_id}")

    async def node_add(self, data: dict) -> dict:
        return await self._post("/panel/api/nodes/add", data)

    async def node_update(self, node_id: int, data: dict) -> dict:
        return await self._post(f"/panel/api/nodes/update/{node_id}", data)

    async def node_delete(self, node_id: int) -> dict:
        return await self._post(f"/panel/api/nodes/del/{node_id}")

    async def node_set_enable(self, node_id: int, enable: bool) -> dict:
        return await self._post(f"/panel/api/nodes/setEnable/{node_id}", {"enable": enable})

    async def node_test(self, data: dict) -> dict:
        return await self._post("/panel/api/nodes/test", data)

    # ==================== SETTINGS ====================
    async def settings_all(self) -> dict:
        return await self._post("/panel/api/setting/all")

    async def settings_update(self, data: dict) -> dict:
        return await self._post("/panel/api/setting/update", data)

    async def settings_update_user(self, data: dict) -> dict:
        return await self._post("/panel/api/setting/updateUser", data)

    # ==================== HOSTS ====================
    async def hosts_list(self) -> dict:
        return await self._get("/panel/api/hosts/list")

    async def host_add(self, data: dict) -> dict:
        return await self._post("/panel/api/hosts/add", data)

    async def host_update(self, host_id: int, data: dict) -> dict:
        return await self._post(f"/panel/api/hosts/update/{host_id}", data)

    async def host_delete(self, host_id: int) -> dict:
        return await self._post(f"/panel/api/hosts/del/{host_id}")

    # ==================== XRAY ====================
    async def xray_template(self) -> dict:
        return await self._post("/panel/api/xray/")

    async def xray_update(self, data: dict) -> dict:
        return await self._post("/panel/api/xray/update", data)

    async def xray_outbound_traffic(self) -> dict:
        return await self._get("/panel/api/xray/getOutboundsTraffic")
