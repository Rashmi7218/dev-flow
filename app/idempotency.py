from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProcessedDelivery


async def is_duplicate_delivery(db: AsyncSession, key: str) -> bool:
    """Marks `key` as processed and returns whether it was already seen.

    Uses the DB's unique constraint (via flush) rather than a check-then-insert,
    so concurrent/racing retries of the same delivery can't both slip through.
    The marker row is only durably committed if the caller's own commit succeeds,
    so a delivery that fails partway through is correctly left eligible for retry.
    """
    db.add(ProcessedDelivery(id=key))
    try:
        await db.flush()
        return False
    except IntegrityError:
        await db.rollback()
        return True
