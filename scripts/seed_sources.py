import asyncio
import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.dml import Insert

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from buzz_news.db import async_session_factory
from buzz_news.models import Source


async def seed_sources(catalog_path: str | None = None) -> int:
    if catalog_path is None:
        catalog_path = str(Path(__file__).parent.parent / "src" / "buzz_news" / "sources" / "catalog.yaml")

    with open(catalog_path) as f:
        data = yaml.safe_load(f)

    sources = data.get("sources", [])
    if not sources:
        print("No sources found in catalog")
        return 0

    seeded = 0
    async with async_session_factory() as session:
        for src in sources:
            slug = src["slug"]
            result = await session.execute(select(Source).where(Source.slug == slug))
            existing = result.scalar_one_or_none()

            values = {
                "name": src["name"],
                "url": src["url"],
                "kind": src["kind"],
                "language": src["language"],
                "region": src["region"],
                "category": src["category"],
                "authority": src.get("authority", 0.5),
                "is_tabloid": src.get("is_tabloid", False),
                "enabled": src.get("enabled", True),
                "extra": src.get("extra", {}),
            }

            if existing:
                await session.execute(
                    Insert(Source).values(id=existing.id, **values).on_conflict_do_update(
                        constraint="sources_slug_key",
                        set_=values,
                    )
                )
            else:
                values["slug"] = slug
                await session.execute(Insert(Source).values(**values))

            seeded += 1

        await session.commit()

    print(f"Seeded {seeded} sources from {catalog_path}")
    return seeded


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(seed_sources(path))
