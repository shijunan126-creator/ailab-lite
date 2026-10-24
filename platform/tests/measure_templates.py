"""Small local template parser probe; no developer profile, network or model."""

import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from ailab_platform.worker.isolate import execute


def main():
    with tempfile.TemporaryDirectory(prefix="ailab-template-measure-") as tmp:
        root = Path(tmp)
        source = root / "template.pptx"
        prs = Presentation()
        for index in range(20):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = f"Template sample {index}"
            slide.placeholders[1].text = "Example body\n" * 8
        prs.save(source)
        samples = []
        for index in range(5):
            output = root / str(index)
            started = time.perf_counter()
            result = execute("template", source, output, measure=True)
            samples.append(
                {
                    **result["measurement"],
                    "wall_seconds": time.perf_counter() - started,
                    "output_bytes": sum(p.stat().st_size for p in output.iterdir() if p.is_file()),
                }
            )
        print(
            json.dumps(
                {
                    "slides": 20,
                    "input_bytes": source.stat().st_size,
                    "cpu_seconds_median": statistics.median(r["cpu_seconds"] for r in samples),
                    "wall_seconds_median": statistics.median(r["wall_seconds"] for r in samples),
                    "peak_rss_bytes_max": max(r["peak_rss_bytes"] for r in samples),
                    "output_bytes_max": max(r["output_bytes"] for r in samples),
                },
                indent=2,
            )
        )

        if os.environ.get("AILAB_MEASURE_LARGE") == "1":
            prs = Presentation()
            for index in range(9):
                image = root / f"image-{index}.png"
                Image.frombytes("RGB", (1024, 1024), os.urandom(1024 * 1024 * 3)).save(image)
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                slide.shapes.add_picture(str(image), 0, 0, width=Inches(10))
            source = root / "large.pptx"
            prs.save(source)
            started = time.perf_counter()
            output = root / "large-output"
            result = execute("template", source, output, measure=True)
            print(
                json.dumps(
                    {
                        "input_bytes": source.stat().st_size,
                        "slides": result["slides"],
                        "wall_seconds": time.perf_counter() - started,
                        **result["measurement"],
                        "output_bytes": sum(p.stat().st_size for p in output.iterdir() if p.is_file()),
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
