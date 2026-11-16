async def test_health_live(client):
    response = await client.get('/health/live')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    assert 'x-request-id' in response.headers


async def test_unknown_route_uses_error_format(client):
    response = await client.get('/does-not-exist')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'http_error'
