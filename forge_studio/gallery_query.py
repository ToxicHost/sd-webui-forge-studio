"""Reading the index back: what the grid asks for, and what it is told.

`gallery.js` builds one query -- character, folder, search, rating, sort, order,
page, per_page -- and renders whatever comes back. This module answers it.

**Paths do not leave this module.** A row identifies its file by `id` and by
`fphash`, an opaque digest of the path, and never by the path itself. The
Extension's list route already worked this way; its detail route did not, and
`gallery.js` reads `filepath` exactly once, in a tooltip, with a filename
fallback. So nothing is lost by never sending it, and what is gained is that a
page cannot learn the shape of the owner's disk, and a compromised or merely
curious client cannot ask for a file by naming one.

**Filtering a folder means its subtree.** Selecting `output` shows what is in
`output\\2026\\may` as well, which is what a folder tree implies. GLOB rather
than LIKE for the subtree test: LIKE treats `_` as a wildcard, and an owner
with a folder called `my_pictures` would otherwise see `myXpictures` too.

**Search is AND across terms, OR across fields.** Typing two words narrows;
each word may match the filename, a character tag, or the indexed prompt text.
Negative prompts were already excluded when `search_text` was built, so a
search for "blurry" does not return every image that asked not to be blurry.

**Ordering happens in the query.** `natural_key` is registered on the
connection so `image2` sorts before `image10` without loading the library into
memory to sort it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .gallery_store import GalleryStore

#: Bumped if the handle scheme changes, so stale handles from an older client
#: cannot be mistaken for current ones.
HANDLE_VERSION = "1.1"

DEFAULT_PER_PAGE = 60
MAX_PER_PAGE = 500

#: The sorts `gallery.js` offers. Anything else falls back to filename rather
#: than reaching the query, so a sort name can never become SQL.
SORTS = ("filename", "folder", "newest")

#: Columns read for a list row. `filepath` is deliberately absent.
_LIST_COLUMNS = (
    "i.id, i.filename, i.folder, i.filepath, i.width, i.height, "
    "i.file_date, i.media_type, i.rating"
)


def handle_for(filepath: str) -> str:
    """An opaque, stable handle for a file.

    Used by the page to name an image in a thumbnail or download request
    without ever holding its path. Stable across restarts, because it is a
    function of the path alone -- a random token would need a server-side table
    and would break every open tab on restart.

    Not a secret and not a capability: the thumbnail route still checks that
    the handle belongs to an indexed image before serving anything.
    """

    return hashlib.md5(
        f"{HANDLE_VERSION}:{filepath}".encode("utf-8", "surrogatepass")
    ).hexdigest()[:8]


@dataclass(frozen=True)
class ImageQuery:
    """One request from the grid."""

    character: str = ""
    folder: str = ""
    search: str = ""
    rating: int = 0
    sort: str = "filename"
    order: str = "asc"
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    @classmethod
    def from_request(cls, values: dict[str, Any]) -> "ImageQuery":
        """Build from untrusted query parameters.

        Every field is clamped or rejected here rather than at the point of
        use, so the query builder below can assume its inputs are sane. A
        `per_page` of a million is a denial of service; a negative page is a
        negative OFFSET, which SQLite rejects at runtime.
        """

        def text(name: str) -> str:
            return str(values.get(name) or "").strip()

        def number(name: str, fallback: int) -> int:
            try:
                return int(values.get(name, fallback))
            except (TypeError, ValueError):
                return fallback

        sort = text("sort") or "filename"
        return cls(
            character=text("character"),
            folder=text("folder"),
            search=text("search"),
            rating=max(0, min(5, number("rating", 0))),
            sort=sort if sort in SORTS else "filename",
            order="desc" if text("order").lower() == "desc" else "asc",
            page=max(1, number("page", 1)),
            per_page=max(1, min(MAX_PER_PAGE, number("per_page",
                                                     DEFAULT_PER_PAGE))),
        )


def _terms(search: str) -> list[str]:
    """Split a search box into words. Commas and spaces both separate."""

    return [part for part in re.split(r"[,\s]+", search) if part]


def _where(query: ImageQuery) -> tuple[str, list[str], list[Any]]:
    """The FROM joins, the conditions, and their parameters."""

    joins: list[str] = []
    conditions: list[str] = []
    parameters: list[Any] = []

    if query.character:
        joins.append(
            "JOIN image_characters ic ON i.id = ic.image_id "
            "JOIN characters c ON ic.character_id = c.id"
        )
        conditions.append("c.name = ? COLLATE NOCASE")
        parameters.append(query.character)

    if query.folder:
        # The folder itself, or anything beneath it. GLOB, not LIKE: LIKE
        # would treat an underscore in the owner's folder name as a wildcard.
        conditions.append("(i.folder = ? OR i.folder GLOB ?)")
        parameters.extend([query.folder, f"{query.folder}\\*"])

    for term in _terms(query.search):
        conditions.append(
            "(i.filename LIKE ? "
            "OR i.search_text LIKE ? "
            "OR i.id IN (SELECT ic2.image_id FROM image_characters ic2 "
            "JOIN characters c2 ON ic2.character_id = c2.id "
            "WHERE c2.name LIKE ? COLLATE NOCASE))"
        )
        parameters.extend([f"%{term}%", f"%{term.lower()}%", f"%{term}%"])

    if query.rating > 0:
        conditions.append("i.rating = ?")
        parameters.append(query.rating)

    return " ".join(joins), conditions, parameters


def _order_by(query: ImageQuery) -> str:
    """ORDER BY, built only from values already restricted to `SORTS`."""

    direction = "DESC" if query.order == "desc" else "ASC"
    if query.sort == "folder":
        return (f"ORDER BY natural_key(i.folder) {direction}, "
                f"natural_key(i.filename) {direction}")
    if query.sort == "newest":
        # Filename descending as the tie-break, so images made in the same
        # second keep the order they were numbered in.
        return f"ORDER BY i.file_date {direction}, natural_key(i.filename) DESC"
    return (f"ORDER BY natural_key(i.filename) {direction}, "
            f"natural_key(i.folder) {direction}")


def list_images(store: GalleryStore, query: ImageQuery) -> dict[str, Any]:
    """One page of the grid, plus how many there are in total."""

    joins, conditions, parameters = _where(query)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    source = f"FROM images i {joins}{where}"

    with store.read() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(DISTINCT i.id) {source}",  # noqa: S608
                parameters,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"SELECT DISTINCT {_LIST_COLUMNS} {source} "  # noqa: S608
            f"{_order_by(query)} LIMIT ? OFFSET ?",
            [*parameters, query.per_page, (query.page - 1) * query.per_page],
        ).fetchall()
        images = [_row(connection, row) for row in rows]

    return {
        "images": images,
        "total": total,
        "page": query.page,
        "pages": (total + query.per_page - 1) // query.per_page,
    }


def _row(connection: Any, row: Any) -> dict[str, Any]:
    """One list row, carrying a handle where the path would have been."""

    characters = [
        str(found["name"]) for found in connection.execute(
            "SELECT c.name FROM characters c "
            "JOIN image_characters ic ON c.id = ic.character_id "
            "WHERE ic.image_id = ? ORDER BY ic.position",
            (row["id"],),
        )
    ]
    media_type = row["media_type"] or "image"
    return {
        "id": row["id"],
        "filename": row["filename"],
        "folder": row["folder"],
        "width": row["width"],
        "height": row["height"],
        "file_date": row["file_date"],
        "fphash": handle_for(str(row["filepath"])),
        "media_type": media_type,
        "is_video": media_type == "video",
        "rating": row["rating"] or 0,
        "characters": characters,
    }


def image(store: GalleryStore, image_id: int) -> dict[str, Any] | None:
    """One image by id, or None. Still no path."""

    with store.read() as connection:
        row = connection.execute(
            f"SELECT {_LIST_COLUMNS} FROM images i WHERE i.id = ?",  # noqa: S608
            (image_id,),
        ).fetchone()
        return _row(connection, row) if row else None


def image_by_hash(store: GalleryStore, content_hash: str) -> dict[str, Any] | None:
    """One image by its pixel hash.

    How the Canvas hands an image it just generated to the Gallery: it knows
    the hash of what it made, not the row id the scan will eventually give it.
    """

    if not content_hash:
        return None
    with store.read() as connection:
        row = connection.execute(
            f"SELECT {_LIST_COLUMNS} FROM images i "  # noqa: S608
            "WHERE i.content_hash = ?",
            (content_hash,),
        ).fetchone()
        return _row(connection, row) if row else None


def resolve_handle(store: GalleryStore, handle: str) -> str | None:
    """The path behind a handle, for internal use only.

    The one place a handle turns back into a path, and it does so only for a
    file that is actually in the index -- so a handle for something the owner
    never indexed resolves to nothing, whatever it hashes to.
    """

    if not handle:
        return None
    with store.read() as connection:
        for row in connection.execute("SELECT filepath FROM images"):
            if handle_for(str(row["filepath"])) == handle:
                return str(row["filepath"])
    return None


def statistics(store: GalleryStore) -> dict[str, Any]:
    """Counts for the header. Never a path."""

    with store.read() as connection:
        def count(sql: str) -> int:
            return int(connection.execute(sql).fetchone()[0])

        return {
            "images": count("SELECT COUNT(*) FROM images"),
            "folders": count("SELECT COUNT(*) FROM scan_folders"),
            "characters": count("SELECT COUNT(*) FROM characters"),
            "trash": count("SELECT COUNT(*) FROM trash"),
            "rated": count("SELECT COUNT(*) FROM images WHERE rating > 0"),
            "with_metadata": count(
                "SELECT COUNT(*) FROM image_metadata WHERE image_id IS NOT NULL"
            ),
        }


def suggest(store: GalleryStore, text: str, limit: int = 10) -> list[str]:
    """Character names starting with what has been typed so far."""

    text = (text or "").strip()
    if not text:
        return []
    with store.read() as connection:
        rows = connection.execute(
            "SELECT name FROM characters WHERE name LIKE ? COLLATE NOCASE "
            "ORDER BY natural_key(name) LIMIT ?",
            (f"{text}%", max(1, min(50, limit))),
        ).fetchall()
    return [str(row["name"]) for row in rows]


__all__ = (
    "DEFAULT_PER_PAGE",
    "HANDLE_VERSION",
    "MAX_PER_PAGE",
    "SORTS",
    "ImageQuery",
    "handle_for",
    "image",
    "image_by_hash",
    "list_images",
    "resolve_handle",
    "statistics",
    "suggest",
)
