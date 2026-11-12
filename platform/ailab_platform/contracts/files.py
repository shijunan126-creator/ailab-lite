"""The Broker's file-transfer status contract.

The Agent decides its next action from these numbers, and its test fakes have to raise the
same ones the route returns: a fake that says 400 for a missing path keeps a broken call site
green until a real sandbox answers 404.
"""

FILE_NOT_FOUND = 404  # the path does not exist
FILE_TOO_LARGE = 413  # over the caller's max_bytes or the service limit
FILE_UNREADABLE = 400  # it exists, but cannot be returned as one file (a directory, a link)
