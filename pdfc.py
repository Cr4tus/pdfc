#!/usr/bin/env python3

import os
import time
import tempfile
import argparse
import threading
import subprocess

import psutil

from tqdm import tqdm
from colorama import Fore, init
from concurrent.futures import ThreadPoolExecutor, as_completed
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from pathlib import Path


init(autoreset=True)


# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------

QUALITY_MAP = {1: "/screen", 2: "/ebook", 3: "/printer", 4: "/prepress"}

# Keep track of recently created outputs and temporary files
RECENT_FILES = set()
RECENT_LOCK = threading.Lock()

# Track overall stats for watch mode
WATCH_STATS = {
    "original_total": 0,
    "new_total": 0,
    "files_processed": 0
}


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def humanize_file_size(file_size: float) -> str:
    for unit in ['B','KB','MB','GB']:
        if file_size < 1024:
            return f"{file_size:.1f}{unit}"
        file_size /= 1024
    return f"{file_size:.1f}TB"


def diplay_overall_stats():
    print(Fore.MAGENTA + f"Files processed: {WATCH_STATS['files_processed']}")
    print(f"Total size before: {humanize_file_size(WATCH_STATS['original_total'])}")
    print(f"Total size after:  {humanize_file_size(WATCH_STATS['new_total'])}")
    print(f"Total reduction:  {((WATCH_STATS['original_total'] - WATCH_STATS['new_total']) / WATCH_STATS['original_total'] * 100 if WATCH_STATS['original_total'] > 0 else 0.0):.1f}%")
    print(Fore.MAGENTA + "-------------------")


# -------------------------------------------------------------------
# CPU Auto Tuning
# -------------------------------------------------------------------

def get_computed_number_of_workers():
    cpu = os.cpu_count() or 1
    ram = psutil.virtual_memory().total
    ram_per_job = 700 * 1024 * 1024
    max_by_ram = max(1, ram // ram_per_job)
    max_by_cpu = max(1, cpu - 1)
    workers = min(max_by_ram, max_by_cpu)
    print(Fore.CYAN + f"Detected {cpu} CPU cores")
    print(Fore.CYAN + f"Using {workers} parallel workers\n")
    return int(workers)


# -------------------------------------------------------------------
# Compression
# -------------------------------------------------------------------

def compress_pdf_file(input_file, quality, replace=False):
    """
    Compress a single PDF using qpdf + Ghostscript.
    """

    input_path = Path(input_file)
    preset = QUALITY_MAP[quality]
    base = input_path.stem

    # Create a temp file in /tmp
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir="/tmp")
    tmp_file.close()
    tmp_path = Path(tmp_file.name)

    output = input_path if replace else input_path.with_name(f"{base} - Compressed.pdf")

    # Track temp file
    with RECENT_LOCK:
        RECENT_FILES.add(tmp_path.resolve())

    # Step 1: qpdf optimization
    subprocess.run([
        "qpdf",
        "--stream-data=compress",
        "--recompress-flate",
        "--object-streams=generate",
        str(input_path),
        str(tmp_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 2: Ghostscript compression
    gs_output = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir="/tmp").name) if replace else output
    if replace:
        with RECENT_LOCK:
            RECENT_FILES.add(gs_output.resolve())

    subprocess.run([
        "gs",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={preset}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={str(gs_output)}",
        str(tmp_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Remove the temporary input
    tmp_path.unlink()
    with RECENT_LOCK:
        RECENT_FILES.discard(tmp_path.resolve())

    if replace:
        os.replace(str(gs_output), str(output))
        with RECENT_LOCK:
            RECENT_FILES.discard(gs_output.resolve())

    new_size = output.stat().st_size
    original_size = input_path.stat().st_size if not replace else new_size
    percent = (original_size - new_size) / original_size * 100 if not replace else 0.0

    # Update watch stats
    with RECENT_LOCK:
        WATCH_STATS["original_total"] += original_size
        WATCH_STATS["new_total"] += new_size
        WATCH_STATS["files_processed"] += 1

    return {
        "input": str(input_path),
        "output": str(output),
        "original": original_size,
        "new": new_size,
        "percent": percent
    }


def compress_multiple_pdf_files(files, quality, workers, replace=False):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(compress_pdf_file, f, quality, replace): f for f in files}
        for future in tqdm(as_completed(futures), total=len(files), desc="Compressing", unit="file"):
            try:
                results.append(future.result())
            except Exception as e:
                print(Fore.RED + f"\nError compressing {futures[future]}: {e}")
    return results


# -------------------------------------------------------------------
# Folder Watcher
# -------------------------------------------------------------------

class PDFHandler(FileSystemEventHandler):
    def __init__(self, quality, replace):
        self.quality = quality
        self.replace = replace

    def on_created(self, event):
        if event.is_directory:
            return

        path = Path(str(event.src_path)).resolve()

        if path.suffix.lower() != ".pdf":
            return

        with RECENT_LOCK:
            if path in RECENT_FILES or path.name.endswith(" - Compressed.pdf"):
                return

        time.sleep(1)  # wait for file to stabilize

        print(Fore.CYAN + f"\nNew PDF detected: {path}")

        try:
            result = compress_pdf_file(str(path), self.quality, self.replace)
            print(Fore.GREEN + f"✔ {Path(result['input']).name} → {Path(result['output']).name}")
            print(f"  {humanize_file_size(result['original'])} → {humanize_file_size(result['new'])} ({result['percent']:.1f}% smaller)")
        except Exception as e:
            print(Fore.RED + f"Error compressing {path}: {e}")


def watch_folder(folder, quality, replace):
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

def main():
    parser = argparse.ArgumentParser(description="Compress PDFs using qpdf + Ghostscript")
    parser.add_argument("paths", nargs="*",
                        help="PDF files or folders to compress")
    parser.add_argument("-q", "--quality", type=int, default=2, choices=[1,2,3,4],
                        help="Compression quality (1 smallest, 4 highest)")
    parser.add_argument("-j", "--jobs", type=str, default="auto",
                        help="Parallel jobs (number or 'auto')")
    parser.add_argument("-w", "--watch",
                        help="Watch folder for new PDFs")
    parser.add_argument("--replace", action="store_true",
                        help="Replace original PDFs instead of creating new files")

    args = parser.parse_args()

    if args.watch:
        print(Fore.CYAN + f"Watching folder: {args.watch}")
        watch_folder(args.watch, args.quality, args.replace)
        diplay_overall_stats()
        return

    if not args.paths:
        parser.print_help()
        return

    # Collect all PDFs
    files = []
    for path in args.paths:
        p = Path(path)
        if p.is_file() and p.suffix.lower() == ".pdf":
            files.append(str(p.resolve()))
        elif p.is_dir():
            files.extend([str(f) for f in Path(p).rglob("*.pdf") if f.is_file()])

    if not files:
        print(Fore.RED + "No valid PDF files found.")
        return

    workers = get_computed_number_of_workers() if args.jobs == "auto" else int(args.jobs)
    results = compress_multiple_pdf_files(files, args.quality, workers, replace=args.replace)

    print()
    for r in results:
        print(Fore.GREEN + f"✔ {Path(r['input']).name} → {Path(r['output']).name}")
        print(f"  {humanize_file_size(r['original'])} → {humanize_file_size(r['new'])} ({r['percent']:.1f}% smaller)")
    
    diplay_overall_stats()


if __name__ == "__main__":
    main()