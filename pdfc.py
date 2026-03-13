#!/usr/bin/env python3

import os
import time
import tempfile
import argparse
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil
from tqdm import tqdm
from colorama import Fore, init
from concurrent.futures import ThreadPoolExecutor, as_completed
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

init(autoreset=True)


# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

QUALITY_MAP: dict[int, str] = {
    1: "/screen",
    2: "/ebook",
    3: "/printer",
    4: "/prepress",
}

RAM_PER_JOB_BYTES: int = 700 * 1024 * 1024
COMPRESSED_SUFFIX: str = " - Compressed.pdf"

# Keep track of recently created outputs and temporary files
RECENT_FILES: set[Path] = set()
RECENT_LOCK = threading.Lock()

# Track overall stats for watch mode
WATCH_STATS: dict[str, int] = {
    "original_total": 0,
    "new_total": 0,
    "files_processed": 0,
}


# -------------------------------------------------------------------
# Data types
# -------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CompressionResult:
    input_path: str
    output_path: str
    original_size: int
    new_size: int
    reduction_percent: float


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def humanize_file_size(file_size: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if file_size < 1024:
            return f"{file_size:.1f}{unit}"
        file_size /= 1024
    return f"{file_size:.1f}TB"


def display_overall_stats() -> None:
    original = WATCH_STATS["original_total"]
    new = WATCH_STATS["new_total"]
    reduction = (original - new) / original * 100 if original > 0 else 0.0

    print(Fore.MAGENTA + f"Files processed: {WATCH_STATS['files_processed']}")
    print(f"Total size before: {humanize_file_size(original)}")
    print(f"Total size after:  {humanize_file_size(new)}")
    print(f"Total reduction:   {reduction:.1f}%")
    print(Fore.MAGENTA + "-------------------")


def wait_for_stable(path: Path, interval: float = 0.5, checks: int = 3) -> None:
    """Block until *path*'s size stops changing."""
    last_size = -1
    stable_count = 0
    while stable_count < checks:
        size = path.stat().st_size
        if size == last_size:
            stable_count += 1
        else:
            stable_count = 0
            last_size = size
        time.sleep(interval)


# -------------------------------------------------------------------
# CPU Auto Tuning
# -------------------------------------------------------------------

def get_computed_number_of_workers() -> int:
    cpu = os.cpu_count() or 1
    ram = psutil.virtual_memory().total
    max_by_ram = max(1, ram // RAM_PER_JOB_BYTES)
    max_by_cpu = max(1, cpu - 1)
    workers = int(min(max_by_ram, max_by_cpu))

    print(Fore.CYAN + f"Detected {cpu} CPU cores")
    print(Fore.CYAN + f"Using {workers} parallel workers\n")
    return workers


# -------------------------------------------------------------------
# Subprocess runners
# -------------------------------------------------------------------

def run_qpdf(input_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "qpdf",
            "--stream-data=compress",
            "--recompress-flate",
            "--object-streams=generate",
            str(input_path),
            str(output_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def run_ghostscript(input_path: Path, output_path: Path, preset: str) -> None:
    subprocess.run(
        [
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={preset}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            f"-sOutputFile={output_path}",
            str(input_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


# -------------------------------------------------------------------
# Compression
# -------------------------------------------------------------------

def _make_temp_pdf() -> Path:
    """Create a named temp PDF in the system temp directory and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pdf", dir=tempfile.gettempdir()
    )
    tmp.close()
    return Path(tmp.name)


def compress_pdf_file(
    input_file: str, quality: int, replace: bool = False
) -> CompressionResult:
    """Compress a single PDF using qpdf + Ghostscript."""

    input_path = Path(input_file)
    preset = QUALITY_MAP[quality]
    original_size = input_path.stat().st_size

    output = (
        input_path
        if replace
        else input_path.with_name(f"{input_path.stem}{COMPRESSED_SUFFIX}")
    )

    tmp_path = _make_temp_pdf()
    tmp_resolved = tmp_path.resolve()

    with RECENT_LOCK:
        RECENT_FILES.add(tmp_resolved)

    try:
        # Step 1: qpdf optimisation
        run_qpdf(input_path, tmp_path)

        # Step 2: Ghostscript compression
        if replace:
            gs_output = _make_temp_pdf()
            gs_resolved = gs_output.resolve()
            with RECENT_LOCK:
                RECENT_FILES.add(gs_resolved)
        else:
            gs_output = output
            gs_resolved = None

        try:
            run_ghostscript(tmp_path, gs_output, preset)
        finally:
            if gs_resolved is not None and gs_output.exists():
                # Move the GS temp to the final destination
                os.replace(str(gs_output), str(output))
                with RECENT_LOCK:
                    RECENT_FILES.discard(gs_resolved)

    finally:
        if tmp_path.exists():
            tmp_path.unlink()
        with RECENT_LOCK:
            RECENT_FILES.discard(tmp_resolved)

    new_size = output.stat().st_size
    percent = (original_size - new_size) / original_size * 100 if original_size > 0 else 0.0

    with RECENT_LOCK:
        WATCH_STATS["original_total"] += original_size
        WATCH_STATS["new_total"] += new_size
        WATCH_STATS["files_processed"] += 1

    return CompressionResult(
        input_path=str(input_path),
        output_path=str(output),
        original_size=original_size,
        new_size=new_size,
        reduction_percent=percent,
    )


def compress_multiple_pdf_files(
    files: list[str], quality: int, workers: int, replace: bool = False
) -> list[CompressionResult]:
    results: list[CompressionResult] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(compress_pdf_file, f, quality, replace): f for f in files
        }
        for future in tqdm(
            as_completed(futures), total=len(files), desc="Compressing", unit="file"
        ):
            try:
                results.append(future.result())
            except Exception as e:
                print(Fore.RED + f"\nError compressing {futures[future]}: {e}")

    return results


# -------------------------------------------------------------------
# Folder Watcher
# -------------------------------------------------------------------

class PDFHandler(FileSystemEventHandler):
    def __init__(self, quality: int, replace: bool) -> None:
        self.quality = quality
        self.replace = replace

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return

        path = Path(str(event.src_path)).resolve()

        if path.suffix.lower() != ".pdf":
            return

        with RECENT_LOCK:
            if path in RECENT_FILES or path.name.endswith(COMPRESSED_SUFFIX):
                return

        wait_for_stable(path)

        print(Fore.CYAN + f"\nNew PDF detected: {path}")

        try:
            result = compress_pdf_file(str(path), self.quality, self.replace)
            print(
                Fore.GREEN
                + f"✔ {Path(result.input_path).name} → {Path(result.output_path).name}"
            )
            print(
                f"  {humanize_file_size(result.original_size)} → "
                f"{humanize_file_size(result.new_size)} "
                f"({result.reduction_percent:.1f}% smaller)"
            )
        except Exception as e:
            print(Fore.RED + f"Error compressing {path}: {e}")


def watch_folder(folder: str, quality: int, replace: bool) -> None:
    event_handler = PDFHandler(quality, replace)
    observer = Observer()
    observer.schedule(event_handler, str(Path(folder).resolve()), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compress PDFs using qpdf + Ghostscript"
    )
    parser.add_argument(
        "paths", nargs="*", help="PDF files or folders to compress"
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=2, choices=[1, 2, 3, 4],
        help="Compression quality (1 smallest, 4 highest)",
    )
    parser.add_argument(
        "-j", "--jobs", type=str, default="auto",
        help="Parallel jobs (number or 'auto')",
    )
    parser.add_argument(
        "-w", "--watch", help="Watch folder for new PDFs"
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Replace original PDFs instead of creating new files",
    )

    args = parser.parse_args()

    if args.watch:
        print(Fore.CYAN + f"Watching folder: {args.watch}")
        watch_folder(args.watch, args.quality, args.replace)
        display_overall_stats()
        return

    if not args.paths:
        parser.print_help()
        return

    # Collect all PDFs
    files: list[str] = []
    for path in args.paths:
        p = Path(path)
        if p.is_file() and p.suffix.lower() == ".pdf":
            files.append(str(p.resolve()))
        elif p.is_dir():
            files.extend(str(f) for f in p.rglob("*.pdf") if f.is_file())

    if not files:
        print(Fore.RED + "No valid PDF files found.")
        return

    workers = (
        get_computed_number_of_workers() if args.jobs == "auto" else int(args.jobs)
    )
    results = compress_multiple_pdf_files(
        files, args.quality, workers, replace=args.replace
    )

    print()
    for r in results:
        print(Fore.GREEN + f"✔ {Path(r.input_path).name} → {Path(r.output_path).name}")
        print(
            f"  {humanize_file_size(r.original_size)} → "
            f"{humanize_file_size(r.new_size)} "
            f"({r.reduction_percent:.1f}% smaller)"
        )

    display_overall_stats()


if __name__ == "__main__":
    main()