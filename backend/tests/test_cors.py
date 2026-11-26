async def test_cors_exposes_retry_after_header(client):
    response = await client.get(
        "/openapi.json",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.headers.get("access-control-expose-headers") == "Retry-After"
