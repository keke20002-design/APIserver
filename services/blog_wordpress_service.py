"""WordPress XML-RPC 기반 블로그 발행 서비스."""
import asyncio
import base64
import logging
import os
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from functools import partial

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)


def _get_config() -> tuple[str, str, str]:
    # XML-RPC 호출은 내부 URL 사용 (외부 URL은 Cloudflare 경유로 404 발생)
    internal_url = os.getenv("WORDPRESS_INTERNAL_URL", "").rstrip("/")
    public_url = os.getenv("WORDPRESS_URL", "").rstrip("/")
    base_url = internal_url or public_url
    username = os.getenv("WORDPRESS_USERNAME", "")
    password = os.getenv("WORDPRESS_APP_PASSWORD", "")
    if not all([base_url, username, password]):
        raise RuntimeError("WORDPRESS_URL / WORDPRESS_USERNAME / WORDPRESS_APP_PASSWORD 환경변수 필요")
    xmlrpc_url = f"{base_url}/xmlrpc.php"
    return xmlrpc_url, username, password


def _sync_publish_post(
    title: str,
    content: str,
    tags: list[str],
    category: str,
    meta_description: str,
    status: str,
    featured_image_id: int | None = None,
) -> dict:
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)

    author_id = int(os.getenv("WORDPRESS_AUTHOR_ID", "1"))
    post_data = {
        "post_title": title,
        "post_content": content,
        "post_status": status,
        "post_excerpt": meta_description,
        "post_author": str(author_id),
        "terms_names": {
            "post_tag": tags[:5],
            "category": [category] if category else [],
        },
    }
    if featured_image_id:
        post_data["post_thumbnail"] = featured_image_id

    post_id = server.wp.newPost(0, username, password, post_data)
    post_id = int(post_id)

    # 발행된 포스트 URL 조회
    post_info = server.wp.getPost(0, username, password, post_id, ["link"])
    post_url = post_info.get("link", "")

    logger.info(
        "WordPress XML-RPC post created — id=%d, url=%s, status=%s, thumbnail=%s",
        post_id, post_url, status, featured_image_id,
    )
    return {"post_id": post_id, "url": post_url, "status": status}


def _sync_update_post_status(post_id: int, status: str) -> dict:
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)
    server.wp.editPost(0, username, password, post_id, {"post_status": status})
    post_info = server.wp.getPost(0, username, password, post_id, ["link"])
    post_url = post_info.get("link", "")
    logger.info("WordPress post updated — id=%d, status=%s", post_id, status)
    return {"post_id": post_id, "url": post_url, "status": status}


async def publish_post(
    title: str,
    content: str,
    tags: list[str],
    category: str,
    meta_description: str,
    status: str = "draft",
    featured_image_id: int | None = None,
) -> dict:
    loop = asyncio.get_event_loop()
    fn = partial(
        _sync_publish_post,
        title, content, tags, category, meta_description, status, featured_image_id,
    )
    return await loop.run_in_executor(_executor, fn)


def _sync_upload_image(image_bytes: bytes, mime_type: str, filename: str) -> dict:
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)
    data = {
        "name": filename,
        "type": mime_type,
        "bits": xmlrpc.client.Binary(image_bytes),
        "overwrite": False,
    }
    result = server.wp.uploadFile(0, username, password, data)
    url = result.get("url", "")
    logger.info("WordPress image uploaded — file=%s, url=%s", filename, url)
    return {"url": url, "id": result.get("id", "")}


async def upload_image(image_bytes: bytes, mime_type: str, filename: str) -> dict:
    loop = asyncio.get_event_loop()
    fn = partial(_sync_upload_image, image_bytes, mime_type, filename)
    return await loop.run_in_executor(_executor, fn)


async def update_post_status(post_id: int, status: str) -> dict:
    loop = asyncio.get_event_loop()
    fn = partial(_sync_update_post_status, post_id, status)
    return await loop.run_in_executor(_executor, fn)


def _sync_delete_post(post_id: int) -> dict:
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)
    result = server.wp.deletePost(0, username, password, post_id)
    logger.info("WordPress post deleted — id=%d", post_id)
    return {"post_id": post_id, "deleted": bool(result)}


async def delete_post(post_id: int) -> dict:
    loop = asyncio.get_event_loop()
    fn = partial(_sync_delete_post, post_id)
    return await loop.run_in_executor(_executor, fn)


def _sync_edit_post(post_id: int, fields: dict) -> dict:
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)
    server.wp.editPost(0, username, password, post_id, fields)
    post_info = server.wp.getPost(0, username, password, post_id, ["link", "post_status"])
    logger.info("WordPress post edited — id=%d, fields=%s", post_id, list(fields.keys()))
    return {"post_id": post_id, "url": post_info.get("link", ""), "status": post_info.get("post_status", "")}


async def edit_post(post_id: int, fields: dict) -> dict:
    loop = asyncio.get_event_loop()
    fn = partial(_sync_edit_post, post_id, fields)
    return await loop.run_in_executor(_executor, fn)


def _sync_update_additional_css(css: str) -> dict:
    """XML-RPC로 WordPress Additional CSS 업데이트 (custom_css CPT 생성/수정)."""
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)

    # 활성 테마 stylesheet 이름 조회
    opts = server.wp.getOptions(0, username, password, ["stylesheet"])
    stylesheet = opts.get("stylesheet", {}).get("value", "custom")

    # 기존 custom_css 포스트 조회
    existing = server.wp.getPosts(0, username, password, {
        "post_type": "custom_css",
        "number": 1,
    })

    post_data = {
        "post_type": "custom_css",
        "post_status": "publish",
        "post_name": stylesheet,
        "post_title": "KekeGroup Theme CSS",
        "post_content": css,
    }

    if existing:
        post_id = int(existing[0]["post_id"])
        server.wp.editPost(0, username, password, post_id, post_data)
        logger.info("WordPress custom_css updated — id=%d, theme=%s", post_id, stylesheet)
    else:
        post_id = int(server.wp.newPost(0, username, password, post_data))
        server.wp.setOptions(0, username, password, {"custom_css_post_id": str(post_id)})
        logger.info("WordPress custom_css created — id=%d, theme=%s", post_id, stylesheet)

    return {"success": True, "post_id": post_id, "theme": stylesheet}


async def update_additional_css(css: str) -> dict:
    """WordPress Additional CSS를 XML-RPC로 업데이트."""
    loop = asyncio.get_event_loop()
    fn = partial(_sync_update_additional_css, css)
    return await loop.run_in_executor(_executor, fn)


def _sync_setup_branding(title: str, tagline: str, category_map: dict) -> dict:
    """사이트명·태그라인 변경 + 카테고리 일괄 리네임."""
    xmlrpc_url, username, password = _get_config()
    server = xmlrpc.client.ServerProxy(xmlrpc_url, allow_none=True)

    # 1. 사이트 옵션 변경
    server.wp.setOptions(0, username, password, {
        "blog_title":   title,
        "blog_tagline": tagline,
    })
    logger.info("WordPress site title set to '%s'", title)

    # 2. 기존 카테고리 목록 조회
    terms = server.wp.getTerms(0, username, password, "category", {})
    renamed, created = [], []

    existing_names = {t["name"]: int(t["term_id"]) for t in terms}

    _SLUG_MAP = {
        "Market":      "market",
        "AI":          "ai-tech",
        "Crypto":      "crypto",
        "Economy":     "economy",
        "Game":        "game",
        "Automation":  "automation",
        "Guides":      "guides",
    }

    for old_name, new_name in category_map.items():
        slug = _SLUG_MAP.get(new_name, new_name.encode("ascii", "ignore").decode().replace(" ", "-").lower() or "cat")
        if old_name in existing_names:
            term_id = existing_names[old_name]
            server.wp.editTerm(0, username, password, term_id, "category", {
                "name": new_name,
                "slug": slug,
            })
            renamed.append(f"{old_name} → {new_name}")
        elif new_name not in existing_names:
            server.wp.newTerm(0, username, password, {
                "name":     new_name,
                "taxonomy": "category",
                "slug":     slug,
            })
            created.append(new_name)

    logger.info("Categories renamed=%s created=%s", renamed, created)
    return {"title": title, "tagline": tagline, "renamed": renamed, "created": created}


async def setup_branding(title: str, tagline: str, category_map: dict) -> dict:
    loop = asyncio.get_event_loop()
    fn = partial(_sync_setup_branding, title, tagline, category_map)
    return await loop.run_in_executor(_executor, fn)


def _get_rest_headers() -> tuple[str, dict]:
    """WP REST API base URL + Basic Auth 헤더 반환."""
    internal_url = os.getenv("WORDPRESS_INTERNAL_URL", "").rstrip("/")
    public_url = os.getenv("WORDPRESS_URL", "").rstrip("/")
    base_url = internal_url or public_url
    username = os.getenv("WORDPRESS_USERNAME", "")
    password = os.getenv("WORDPRESS_APP_PASSWORD", "")
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }
    return base_url, headers


async def setup_nav_menu(menu_items: list[dict]) -> dict:
    """
    WordPress REST API로 네비게이션 메뉴를 영어로 교체.
    menu_items: [{"title": "AI", "url": "/category/ai-tech/"}, ...]
    기존 'Primary Menu' 메뉴를 찾아 항목을 교체. 없으면 새로 생성.
    """
    import httpx

    base_url, headers = _get_rest_headers()
    # XML-RPC는 내부 URL이지만 REST API는 외부 public URL 경유
    public_url = os.getenv("WORDPRESS_URL", "").rstrip("/")
    api = f"{public_url}/wp-json/wp/v2"

    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        # 1. 기존 메뉴 목록 조회
        r = await client.get(f"{api}/menus")
        r.raise_for_status()
        menus = r.json()

        # Primary Menu 또는 첫 번째 메뉴 선택
        menu_id = None
        for m in menus:
            if "primary" in m.get("slug", "").lower() or "main" in m.get("slug", "").lower():
                menu_id = m["id"]
                break
        if menu_id is None and menus:
            menu_id = menus[0]["id"]

        # 메뉴가 없으면 새로 생성
        if menu_id is None:
            r = await client.post(f"{api}/menus", json={"name": "Primary Menu", "slug": "primary"})
            r.raise_for_status()
            menu_id = r.json()["id"]
            logger.info("Created new menu id=%d", menu_id)

        # 2. 기존 메뉴 항목 삭제
        r = await client.get(f"{api}/menu-items", params={"menus": menu_id, "per_page": 100})
        r.raise_for_status()
        existing_items = r.json()
        for item in existing_items:
            await client.delete(f"{api}/menu-items/{item['id']}", params={"force": True})

        # 3. 새 메뉴 항목 추가
        created = []
        for order, item in enumerate(menu_items, start=1):
            payload = {
                "title": item["title"],
                "url": item.get("url", "/"),
                "status": "publish",
                "menus": menu_id,
                "menu_order": order,
            }
            r = await client.post(f"{api}/menu-items", json=payload)
            r.raise_for_status()
            created.append(r.json().get("title", {}).get("rendered", item["title"]))

        logger.info("Nav menu updated — menu_id=%d items=%s", menu_id, created)
        return {"menu_id": menu_id, "items": created}
