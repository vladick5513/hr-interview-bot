from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base


class BaseDAO[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, **values: Any) -> ModelT:
        obj = self.model(**values)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def add_many(self, items: list[dict[str, Any]]) -> list[ModelT]:
        objs = [self.model(**values) for values in items]
        self.session.add_all(objs)
        await self.session.flush()
        return objs
