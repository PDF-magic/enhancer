#!/usr/bin/env python3
"""Losslessly optimize PDF streams and verify every decoded scan image."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zlib
from pathlib import Path

import pikepdf
from pikepdf import Name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--reported-output",
        type=Path,
        help="final derivative path to record when OUTPUT is an intermediate file",
    )
    return parser.parse_args()


def image_fingerprints(path: Path) -> tuple[int, list[tuple[object, ...]]]:
    fingerprints: list[tuple[object, ...]] = []
    with pikepdf.open(path) as pdf:
        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, 1):
            for image in page.Resources.get("/XObject", {}).values():
                if image.get("/Subtype") != Name("/Image"):
                    continue
                decoded = image.read_bytes()
                fingerprints.append(
                    (
                        page_number,
                        int(image.Width),
                        int(image.Height),
                        str(image.get("/ColorSpace")),
                        int(image.get("/BitsPerComponent", 0)),
                        hashlib.sha256(decoded).hexdigest(),
                    )
                )
    return page_count, fingerprints


def main() -> int:
    args = parse_args()
    report_path = args.report or args.output.with_suffix(".compression.json")
    if not args.input.is_file():
        print(f"Input PDF not found: {args.input}", file=sys.stderr)
        return 1
    if args.input.resolve() == args.output.resolve():
        print("Input and output must be different files.", file=sys.stderr)
        return 1
    if args.output.exists() or report_path.exists():
        print("Refusing to overwrite output or report.", file=sys.stderr)
        return 1

    before_size = args.input.stat().st_size
    page_count, before_images = image_fingerprints(args.input)
    streams_recompressed = 0
    with pikepdf.open(args.input) as pdf:
        seen_objects: set[tuple[int, int]] = set()
        for page in pdf.pages:
            for image in page.Resources.get("/XObject", {}).values():
                if image.get("/Subtype") != Name("/Image"):
                    continue
                identity = tuple(image.objgen)
                if identity in seen_objects:
                    continue
                seen_objects.add(identity)
                if image.get("/Filter") != Name("/FlateDecode"):
                    continue
                decode_parameters = image.get("/DecodeParms")
                original = image.read_raw_bytes()
                recompressed = zlib.compress(zlib.decompress(original), level=9)
                if len(recompressed) >= len(original):
                    continue
                image.write(recompressed)
                image["/Filter"] = Name("/FlateDecode")
                if decode_parameters is not None:
                    image["/DecodeParms"] = decode_parameters
                streams_recompressed += 1
        pdf.save(
            args.output,
            compress_streams=False,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
        )
    output_pages, after_images = image_fingerprints(args.output)
    if output_pages != page_count:
        raise ValueError("page count changed during lossless compression")
    if after_images != before_images:
        raise ValueError("decoded scan image changed during lossless compression")

    after_size = args.output.stat().st_size
    report = {
        "input": str(args.input),
        "output": str(args.reported_output or args.output),
        "method": "zlib level 9 recompression of existing predicted Flate streams",
        "lossless": True,
        "pages": page_count,
        "images_verified": len(before_images),
        "image_streams_recompressed": streams_recompressed,
        "decoded_image_sha256_match": True,
        "input_bytes": before_size,
        "compressed_pdf_bytes": after_size,
        "saved_bytes": before_size - after_size,
        "reduction_percent": round((1 - after_size / before_size) * 100, 2),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Created lossless PDF: {args.output}")
    print(f"Created compression report: {report_path}")
    print(f"Verified decoded images: {len(before_images)}")
    print(f"Lossless reduction: {report['reduction_percent']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
