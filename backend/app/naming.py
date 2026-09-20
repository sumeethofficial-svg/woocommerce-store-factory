from app.config import Settings


def namespace_for(name: str) -> str:
    return f"store-{name}"


def store_host(name: str, settings: Settings) -> str:
    return f"{name}.{settings.store_base_domain}"


def site_url(name: str, settings: Settings) -> str:
    port = "" if settings.store_public_port == 80 else f":{settings.store_public_port}"
    if settings.ingress_enabled:
        return f"http://{store_host(name, settings)}{port}"
    return f"http://localhost:{settings.store_public_port}"
