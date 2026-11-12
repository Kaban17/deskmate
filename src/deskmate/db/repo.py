from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from deskmate.db.models import Dialog, Message, User


async def get_or_create_user(
    session: AsyncSession, external_id: str, full_name: str | None = None
) -> User:
    user = await session.scalar(select(User).where(User.external_id == external_id))
    if user is None:
        user = User(external_id=external_id, full_name=full_name)
        session.add(user)
        await session.flush()
    return user


async def get_or_create_active_dialog(
    session: AsyncSession, user_id: int, now: datetime, idle: timedelta
) -> Dialog:
    dialog = await session.scalar(
        select(Dialog).where(Dialog.user_id == user_id, Dialog.status == "active")
    )
    if dialog is not None:
        if now - dialog.last_message_at <= idle:
            return dialog
        dialog.status = "closed"
        await session.flush()  # free the partial unique index before inserting
    dialog = Dialog(user_id=user_id, status="active", started_at=now, last_message_at=now)
    session.add(dialog)
    await session.flush()
    return dialog


async def close_active_dialog(session: AsyncSession, user_id: int) -> int | None:
    return await session.scalar(
        update(Dialog)
        .where(Dialog.user_id == user_id, Dialog.status == "active")
        .values(status="closed")
        .returning(Dialog.id)
    )


async def add_message(
    session: AsyncSession,
    dialog_id: int,
    role: str,
    content: str,
    *,
    model: str | None = None,
    prompt_version: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    now: datetime | None = None,
) -> Message:
    now = now or datetime.now(UTC)
    message = Message(
        dialog_id=dialog_id,
        role=role,
        content=content,
        created_at=now,
        model=model,
        prompt_version=prompt_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
    session.add(message)
    await session.execute(update(Dialog).where(Dialog.id == dialog_id).values(last_message_at=now))
    await session.flush()
    return message


async def recent_messages(session: AsyncSession, dialog_id: int, limit: int) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.dialog_id == dialog_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))
