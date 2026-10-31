"""HTTP agent declarations cannot persist credentials embedded in hosted MCP URLs."""


async def test_hosted_mcp_agent_urls_reject_query_fragment_and_userinfo(http_platform):
    _, client, headers, _ = http_platform
    specification = {
        "id": "remote",
        "name": "Remote",
        "backend": "http_test",
        "hosted_tools": [
            {
                "type": "mcp",
                "server_label": "remote",
                "server_url": "https://mcp.example.test/api",
                "authorization_secret_ref": "env:MCP_CREDENTIAL",
                "allowed_tools": ["search"],
            }
        ],
    }
    response = await client.post("/api/v1/agents", json=specification, headers=headers["alice"])
    assert response.status_code == 201, response.text
    revision = response.json()["revision"]
    for url in (
        "https://mcp.example.test/api?token=nonsecret-test-value",
        "https://mcp.example.test/api#nonsecret-test-fragment",
        "https://user:nonsecret-test-value@mcp.example.test/api",
    ):
        unsafe = {
            **specification,
            "id": "unsafe",
            "hosted_tools": [
                {
                    **specification["hosted_tools"][0],
                    "server_url": url,
                }
            ],
        }
        created = await client.post("/api/v1/agents", json=unsafe, headers=headers["alice"])
        assert created.status_code == 422, created.text
        updated = await client.put(
            "/api/v1/agents/remote",
            json={"specification": {**unsafe, "id": "remote"}, "expected_revision": revision},
            headers=headers["alice"],
        )
        assert updated.status_code == 422, updated.text
    assert (await client.get("/api/v1/agents/unsafe", headers=headers["alice"])).status_code == 404
    unchanged = await client.get("/api/v1/agents/remote", headers=headers["alice"])
    assert unchanged.json()["draft"] == response.json()["draft"]
