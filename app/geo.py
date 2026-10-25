"""Browser-native geolocation; no local Chrome or extension dependency."""
from urllib.parse import urlsplit


async def configure_geolocation(context, config):
    if config.geolocation is None:
        return
    await context.set_geolocation(config.geolocation)
    for url in (config.hall_url, config.checkin_url):
        parsed = urlsplit(url)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            await context.grant_permissions(['geolocation'], origin=f'{parsed.scheme}://{parsed.netloc}')
