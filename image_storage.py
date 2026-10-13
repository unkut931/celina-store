from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import current_app


class ImageStorageError(ValueError):
    pass


def supabase_storage_enabled():
    return bool(current_app.config.get("SUPABASE_URL"))


def store_product_image(filename, content, content_type):
    if not supabase_storage_enabled():
        return None

    base_url = current_app.config["SUPABASE_URL"].rstrip("/")
    bucket = quote(current_app.config["SUPABASE_STORAGE_BUCKET"], safe="")
    object_name = quote(filename, safe="")
    request = Request(
        f"{base_url}/storage/v1/object/{bucket}/{object_name}",
        data=content,
        headers={
            "Authorization": f"Bearer {current_app.config['SUPABASE_SERVICE_ROLE_KEY']}",
            "apikey": current_app.config["SUPABASE_SERVICE_ROLE_KEY"],
            "Content-Type": content_type,
            "x-upsert": "false",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except HTTPError as error:
        current_app.logger.error(
            "Supabase Storage rejected product image upload (HTTP %s).",
            error.code,
        )
        raise ImageStorageError(
            f"Le stockage des photos a refusé l'image (HTTP {error.code})."
        ) from error
    except URLError as error:
        current_app.logger.error("Could not connect to Supabase Storage: %s", error.reason)
        raise ImageStorageError(
            "Impossible de joindre le stockage des photos. Réessayez."
        ) from error
    return filename


def public_product_image_url(filename):
    if not supabase_storage_enabled():
        return None
    base_url = current_app.config["SUPABASE_URL"].rstrip("/")
    bucket = quote(current_app.config["SUPABASE_STORAGE_BUCKET"], safe="")
    object_name = quote(filename, safe="")
    return f"{base_url}/storage/v1/object/public/{bucket}/{object_name}"
