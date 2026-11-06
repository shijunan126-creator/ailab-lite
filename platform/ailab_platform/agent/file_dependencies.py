"""Durable suspension signal: no model polling or sandbox sleep for platform jobs."""


class FileDependencyPending(Exception):
    def __init__(self, file_id, action, pages, deadline, operation_id=None):
        self.dependency = {"file_id": file_id, "action": action, "pages": pages, "deadline": deadline}
        if operation_id:
            self.dependency["operation_id"] = operation_id
