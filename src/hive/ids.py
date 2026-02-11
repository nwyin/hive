"""Hash-based ID generation for human-readable, collision-free identifiers."""

import hashlib
import uuid


def generate_id(prefix: str = "w") -> str:
    """
    Generate a hash-based ID with a prefix.

    Args:
        prefix: String prefix for the ID (e.g., "w" for work, "agent" for agents)

    Returns:
        ID string in format "{prefix}-{hash[:6]}" (e.g., "w-a3f8b1")

    Example:
        >>> id1 = generate_id("w")
        >>> id1.startswith("w-")
        True
        >>> len(id1)
        8
    """
    raw = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    return f"{prefix}-{raw[:6]}"
