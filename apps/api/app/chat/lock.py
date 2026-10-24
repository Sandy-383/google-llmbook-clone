import uuid

# Set while an answer is being generated in a notebook. Also how background generation knows a
# person is waiting on the GPU (see app/generation/scheduling.py).
CHAT_LOCK_PATTERN = "chat:busy:*"


def chat_lock_key(notebook_id: uuid.UUID) -> str:
    return f"chat:busy:{notebook_id}"
