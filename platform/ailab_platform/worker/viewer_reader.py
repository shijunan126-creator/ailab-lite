"""One bounded native reader process; no parser or document bytes in the API."""

import json


def read(source, output, options):
    from ..sandbox_tools.render_preview import check_archive

    if source.stat().st_size > 20 * 1024**2:
        raise ValueError("input_limit")
    check_archive(source)
    request = output / "request.json"
    request.write_text(json.dumps({"selections": options.get("selections", [])}))
    result = output / "viewer.json"
    from ..sandbox_tools.office_exec import run

    command = ["node", "--max-old-space-size=96", options["reader"], str(source), str(result), str(request)]
    code = run(command, 18, 192 * 1024**2)
    if code or not result.exists() or result.stat().st_size > 4 * 1024**2:
        raise ValueError("viewer_reader_failed")
    data = json.loads(result.read_text())
    return data
