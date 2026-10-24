# Disposable CI fixtures

`Dockerfile.sandbox` exercises Docker provider lifecycle, bounded transfer, expiry and Redis-loss recovery with Python/GNU tools, user 10001 and an owned `/workspace`. It does not certify Office, installed skills, gVisor, OpenSandbox or production isolation. Those need the actual sandbox image and runtime.

`ci-realm.json` is an import-only Keycloak realm (`ci`) with one public PKCE client, one exact loopback callback and a disposable test user. The password is public fixture data, not an account on any deployed service. The weekly workflow uses an ephemeral container with no persistent volume or production credentials. Keycloak startup import follows the [official import guide](https://www.keycloak.org/server/importExport).

ClamAV uses the same immutable image digest as the deployment manifest and fresh ephemeral signatures. Readiness waits for a protocol PONG, not just an open TCP port. A failed signature download fails the component job; it never substitutes the scanner stub.
