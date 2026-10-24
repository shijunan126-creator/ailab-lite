"""Integration seam: replace this no-op with branch B's transactional create_notification."""


def create_notification(db, user_id, kind, title, conversation_id=None, run_id=None, share_id=None):
    pass
