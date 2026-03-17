"""
ncert-mcp — NCERT PDF Ingestion

Downloads all NCERT textbook chapter PDFs from ncert.nic.in for Grades 7–12.
Each book is fetched as a zip archive; individual chapter PDFs are extracted
and saved with a JSON metadata sidecar alongside each file.

Usage:
    python src/ingest.py                         # all grades + subjects
    python src/ingest.py --grades 9 10           # specific grades
    python src/ingest.py --grades 7 --subjects Mathematics Science

Safe to re-run — already-downloaded files are skipped.
"""

import asyncio
import argparse
import io
import json
import zipfile
from pathlib import Path
from datetime import datetime

import httpx
import aiofiles
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

import sys
sys.path.insert(0, str(Path(__file__).parent))

from config import NCERT_PDF_DIR
from tools.filesystem import NCERT_TEXTBOOK_CHAPTERS

console = Console()


async def _download_zip(
    client: httpx.AsyncClient,
    code: str,
    num_chapters: int,
    dest_dir: Path,
    grade: int,
    subject: str,
) -> tuple[int, int]:
    """
    Download the full-book zip from ncert.nic.in and extract chapter PDFs.
    Returns (saved_count, failed_count).
    """
    zip_url = f"https://ncert.nic.in/textbook/pdf/{code}dd.zip"
    try:
        resp = await client.get(zip_url, timeout=120.0)
        resp.raise_for_status()
    except Exception as e:
        console.print(f"[red]  ✗ {code} zip: {e}[/red]")
        return 0, num_chapters

    saved = failed = 0
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for ch in range(1, num_chapters + 1):
            filename = f"{code}{ch:02d}.pdf"
            if filename not in zf.namelist():
                failed += 1
                continue

            dest = dest_dir / filename
            if dest.exists():
                saved += 1
                continue

            dest.write_bytes(zf.read(filename))
            meta = {
                "grade": grade, "subject": subject, "book_code": code,
                "chapter": ch, "source": "NCERT_nic_in_zip", "url": zip_url,
                "downloaded_at": datetime.utcnow().isoformat(),
                "local_file": filename,
            }
            async with aiofiles.open(dest_dir / f"{filename}.meta.json", "w") as f:
                await f.write(json.dumps(meta, indent=2))
            saved += 1

    return saved, failed


async def ingest_ncert(grades: list[int], subjects: list[str]) -> dict:
    """Download NCERT chapter PDFs for the given grades and subjects."""
    results: dict[str, int] = {"downloaded": 0, "skipped": 0, "failed": 0}
    NCERT_PDF_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for grade in grades:
            for subject in subjects:
                entry = NCERT_TEXTBOOK_CHAPTERS.get((grade, subject))
                if not entry:
                    results["skipped"] += 1
                    continue

                code, num_chapters = entry
                dest_dir = NCERT_PDF_DIR / f"grade_{grade}" / subject
                dest_dir.mkdir(parents=True, exist_ok=True)

                # Skip if all chapters already on disk
                existing = sum(1 for ch in range(1, num_chapters + 1)
                               if (dest_dir / f"{code}{ch:02d}.pdf").exists())
                if existing == num_chapters:
                    console.print(f"  [dim]Grade {grade} {subject} — all {num_chapters} chapters cached[/dim]")
                    results["skipped"] += num_chapters
                    continue

                console.print(f"  Grade {grade} {subject} ({num_chapters} chapters, {existing} cached)…")
                saved, failed = await _download_zip(client, code, num_chapters, dest_dir, grade, subject)
                results["downloaded"] += saved
                results["failed"] += failed

    return results


def parse_args() -> argparse.Namespace:
    all_grades = sorted({g for g, _ in NCERT_TEXTBOOK_CHAPTERS})
    all_subjects = sorted({s for _, s in NCERT_TEXTBOOK_CHAPTERS})

    parser = argparse.ArgumentParser(
        description="Download NCERT textbook PDFs (Grades 7–12) from ncert.nic.in",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available grades: {all_grades}\nAvailable subjects: {all_subjects}",
    )
    parser.add_argument("--grades",   nargs="+", type=int, default=all_grades,
                        help="Grades to download (default: all)")
    parser.add_argument("--subjects", nargs="+", default=all_subjects,
                        help="Subjects to download (default: all)")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    console.rule("[bold blue]ncert-mcp — NCERT Ingestion[/bold blue]")
    console.print(f"Grades:   {args.grades}")
    console.print(f"Subjects: {args.subjects}\n")

    summary = await ingest_ncert(args.grades, args.subjects)

    table = Table(title="Ingestion Summary")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    table.add_row("[green]Downloaded[/green]", str(summary["downloaded"]))
    table.add_row("[dim]Skipped (cached)[/dim]",  str(summary["skipped"]))
    table.add_row("[red]Failed[/red]",    str(summary["failed"]))
    console.print(table)


if __name__ == "__main__":
    asyncio.run(main())
