#!/usr/bin/env python3
"""
epub2cbr - Convert EPUB comic/manga files to CBR

Supports three types of EPUB structures:
1. Single image WITH text in image -> Extract directly (best quality)
2. Single/Split images WITHOUT text in image -> Screenshot with gowitness
3. Split images WITH text in image -> Concatenate images (best quality)
"""

import argparse
import http.server
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

try:
    from PIL import Image
except ImportError:
    Image = None


def print_banner():
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                       epub2cbr                            ║
    ║         EPUB to CBR Converter for Comics/Manga            ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


# =============================================================================
# Dependency Checks
# =============================================================================


def check_rar_available() -> bool:
    """Check if rar command is available for CBR creation."""
    try:
        subprocess.run(["rar"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_gowitness_available() -> bool:
    """Check if gowitness is available."""
    try:
        subprocess.run(["gowitness", "--help"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_dependencies(need_gowitness: bool = False) -> bool:
    """Check required dependencies."""
    if Image is None:
        print("❌ Error: Pillow not installed")
        print("   Install with: pip install Pillow")
        return False

    if need_gowitness and not check_gowitness_available():
        print("❌ Error: gowitness not found")
        print("   Install with: go install github.com/sensepost/gowitness@latest")
        return False

    # Check archive creation capabilities
    has_rar = check_rar_available()
    has_zip = check_zip_available()

    if not has_rar and not has_zip:
        print("❌ Error: Neither rar nor zip command found")
        print("   Install rar or zip to create comic archives")
        return False

    if not has_rar:
        print("⚠️  Warning: rar not available, will create CBZ (zip) instead of CBR")

    return True


# =============================================================================
# EPUB Extraction and Parsing
# =============================================================================


def extract_epub(epub_path: Path, output_dir: Path) -> Path:
    """Extract EPUB file (ZIP archive)."""
    extract_path = output_dir / "extracted"
    extract_path.mkdir(parents=True, exist_ok=True)

    print(f"📦 Extracting EPUB: {epub_path.name}")

    with zipfile.ZipFile(epub_path, "r") as zip_ref:
        zip_ref.extractall(extract_path)

    return extract_path


def find_content_opf(extract_path: Path) -> Optional[Path]:
    """Find the content.opf file."""
    for opf_file in extract_path.rglob("*.opf"):
        return opf_file
    return None


def parse_spine_order(opf_path: Path) -> List[str]:
    """Parse OPF file to get reading order from spine."""
    try:
        tree = ET.parse(opf_path)
        root = tree.getroot()

        ns = {"opf": "http://www.idpf.org/2007/opf"}

        manifest = {}
        for item in root.findall(".//opf:manifest/opf:item", ns):
            item_id = item.get("id")
            href = item.get("href")
            if item_id and href:
                manifest[item_id] = href

        if not manifest:
            for item in root.findall(".//manifest/item"):
                item_id = item.get("id")
                href = item.get("href")
                if item_id and href:
                    manifest[item_id] = href

        spine_order = []
        for itemref in root.findall(".//opf:spine/opf:itemref", ns):
            idref = itemref.get("idref")
            if idref and idref in manifest:
                spine_order.append(manifest[idref])

        if not spine_order:
            for itemref in root.findall(".//spine/itemref"):
                idref = itemref.get("idref")
                if idref and idref in manifest:
                    spine_order.append(manifest[idref])

        return spine_order
    except Exception as e:
        print(f"⚠️  Warning: Could not parse OPF: {e}")
        return []


def get_ordered_html_files(extract_path: Path) -> List[Path]:
    """Get HTML files in reading order."""
    opf_path = find_content_opf(extract_path)

    if opf_path:
        spine_order = parse_spine_order(opf_path)
        if spine_order:
            opf_dir = opf_path.parent
            ordered_files = []
            for href in spine_order:
                file_path = opf_dir / href
                if file_path.exists():
                    ordered_files.append(file_path)
            if ordered_files:
                print(f"📖 Found {len(ordered_files)} pages in reading order")
                return ordered_files

    # Fallback: find all HTML files
    html_files = []
    for ext in ["*.xhtml", "*.html", "*.htm"]:
        html_files.extend(extract_path.rglob(ext))

    # Filter out nav/toc
    filtered = [f for f in html_files if not any(x in f.name.lower() for x in ["nav", "toc"])]
    filtered = sorted(filtered, key=lambda x: x.name)

    print(f"📖 Found {len(filtered)} pages (sorted alphabetically)")
    return filtered


# =============================================================================
# Page Analysis
# =============================================================================


def analyze_html_page(html_path: Path) -> dict:
    """
    Analyze an HTML page to determine its structure.

    Returns dict with:
        - image_count: number of images
        - image_paths: list of image file paths
        - has_text_content: True if TextContainer has real text (not just page numbers)
        - viewport: (width, height) tuple
        - image_resolution: (width, height) of actual image files (max if multiple)
    """
    result = {
        "image_count": 0,
        "image_paths": [],
        "has_text_content": False,
        "viewport": (0, 0),
        "image_resolution": (0, 0),
    }

    try:
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        html_dir = html_path.parent

        # Get viewport
        viewport_match = re.search(r'<meta[^>]+content=["\']width=(\d+),?\s*height=(\d+)["\']', content, re.IGNORECASE)
        if viewport_match:
            result["viewport"] = (int(viewport_match.group(1)), int(viewport_match.group(2)))

        # Find images
        img_patterns = [
            r'src=["\']([^"\']+\.(jpg|jpeg|png|gif|webp))["\']',
            r'xlink:href=["\']([^"\']+\.(jpg|jpeg|png|gif|webp))["\']',
        ]

        for pattern in img_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                img_ref = match[0]
                if img_ref.startswith("../"):
                    img_path = (html_dir / img_ref).resolve()
                else:
                    img_path = html_dir / img_ref

                if img_path.exists() and img_path not in result["image_paths"]:
                    result["image_paths"].append(img_path)

        result["image_count"] = len(result["image_paths"])

        # Get actual image resolution (use first/largest image)
        if result["image_paths"] and Image:
            try:
                max_width, max_height = 0, 0
                for img_path in result["image_paths"][:3]:  # Check first 3 images
                    with Image.open(img_path) as img:
                        if img.width > max_width:
                            max_width = img.width
                            max_height = img.height
                result["image_resolution"] = (max_width, max_height)
            except Exception:
                pass

        # Check TextContainer for real text content
        # Look for TextContainer div and check if it has text spans with actual words
        text_container_match = re.search(
            r'<div\s+id=["\']TextContainer\d*["\'][^>]*>(.*?)</div>', content, re.DOTALL | re.IGNORECASE
        )

        if text_container_match:
            text_content = text_container_match.group(1)

            # Extract text from all spans (including nested koboSpan)
            span_texts = re.findall(r">([^<]+)<", text_content)

            # Join all text fragments and clean
            all_text = "".join(span_texts).strip()

            # Remove just digits (page numbers)
            text_without_numbers = re.sub(r"\d+", "", all_text).strip()

            # If we have any letters remaining, it's real text content
            has_letters = bool(re.search(r"[a-zA-ZáéíóúñÁÉÍÓÚÑàèìòùäëïöüâêîôûçœæ]", text_without_numbers))
            result["has_text_content"] = has_letters

    except Exception as e:
        print(f"⚠️  Warning: Error analyzing {html_path.name}: {e}")

    return result


def determine_epub_type(html_files: List[Path], sample_size: int = 5) -> Tuple[str, int, int, float]:
    """
    Analyze sample pages to determine EPUB type.

    Returns:
        (epub_type, width, height, scale_factor)

    epub_type:
        'extract': Text is embedded in images -> extract directly (best quality)
        'screenshot': Text is in HTML overlay -> need gowitness to render

    Logic:
        - If TextContainer has real text content -> screenshot (text needs rendering)
        - If TextContainer is empty or only has page numbers -> extract images directly

    scale_factor:
        - Ratio between actual image resolution and viewport
        - Used to render screenshots at full resolution
    """
    pages_with_text = 0  # Pages where TextContainer has real text
    pages_without_text = 0  # Pages where text is embedded in image

    max_viewport = (0, 0)
    max_image_res = (0, 0)

    for html_file in html_files[:sample_size]:
        analysis = analyze_html_page(html_file)

        if analysis["viewport"][0] > max_viewport[0]:
            max_viewport = analysis["viewport"]

        if analysis["image_resolution"][0] > max_image_res[0]:
            max_image_res = analysis["image_resolution"]

        if analysis["has_text_content"]:
            pages_with_text += 1
        else:
            pages_without_text += 1

    # Calculate scale factor (how much larger are images vs viewport)
    scale_factor = 1.0
    if max_viewport[0] > 0 and max_image_res[0] > 0:
        scale_factor = max_image_res[0] / max_viewport[0]
        # Round to avoid weird fractions, and cap at reasonable values
        if scale_factor > 0.9 and scale_factor < 1.1:
            scale_factor = 1.0
        elif scale_factor > 4.0:
            scale_factor = 4.0

    # If ANY page has text in HTML, we need screenshot mode
    # because text won't appear if we just extract images
    if pages_with_text > 0:
        return "screenshot", max_viewport[0], max_viewport[1], scale_factor
    else:
        return "extract", max_viewport[0], max_viewport[1], scale_factor


# =============================================================================
# Image Processing Methods
# =============================================================================


def extract_single_image(html_path: Path, output_path: Path) -> bool:
    """Extract single image directly from page."""
    analysis = analyze_html_page(html_path)

    if analysis["image_count"] == 1:
        img_path = analysis["image_paths"][0]
        shutil.copy2(img_path, output_path)
        return True
    return False


def concatenate_images(html_path: Path, output_path: Path) -> bool:
    """Concatenate multiple images vertically."""
    if Image is None:
        return False

    analysis = analyze_html_page(html_path)

    if analysis["image_count"] < 1:
        return False

    if analysis["image_count"] == 1:
        # Just copy the single image
        shutil.copy2(analysis["image_paths"][0], output_path)
        return True

    try:
        # Open all images
        images = [Image.open(p) for p in analysis["image_paths"]]

        # Calculate total size
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        # Create combined image
        combined = Image.new("RGB", (max_width, total_height), (255, 255, 255))

        y_offset = 0
        for img in images:
            combined.paste(img, (0, y_offset))
            y_offset += img.height
            img.close()

        # Save
        combined.save(output_path, "PNG")
        combined.close()
        return True

    except Exception as e:
        print(f"\n⚠️  Error concatenating images: {e}")
        return False


# =============================================================================
# Gowitness Screenshot Method
# =============================================================================


def make_http_handler(directory: Path):
    """Create an HTTP handler that serves files from a specific directory."""

    class DirectoryHTTPHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format, *args):
            pass

    return DirectoryHTTPHandler


def find_free_port() -> int:
    """Find a free port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare_scaled_html(html_path: Path, output_dir: Path, scale_factor: float) -> Path:
    """
    Create a scaled version of the HTML file for high-resolution screenshots.

    This modifies:
    1. viewport meta tag
    2. body dimensions
    3. All absolute pixel values in styles
    """
    if scale_factor <= 1.0:
        return html_path

    content = html_path.read_text(encoding="utf-8", errors="ignore")

    # Scale viewport meta tag
    def scale_viewport(match):
        width = int(int(match.group(1)) * scale_factor)
        height = int(int(match.group(2)) * scale_factor)
        return f'content="width={width}, height={height}"'

    content = re.sub(r'content=["\']width=(\d+),?\s*height=(\d+)["\']', scale_viewport, content)

    # Scale body style dimensions
    def scale_body_style(match):
        style = match.group(1)
        # Scale width
        style = re.sub(r"width:(\d+)px", lambda m: f"width:{int(int(m.group(1)) * scale_factor)}px", style)
        # Scale height
        style = re.sub(r"height:(\d+)px", lambda m: f"height:{int(int(m.group(1)) * scale_factor)}px", style)
        return f'<body style="{style}"'

    content = re.sub(r'<body\s+style="([^"]*)"', scale_body_style, content)

    # Scale PageContainer dimensions
    def scale_page_container(match):
        style = match.group(1)
        style = re.sub(r"width:(\d+)px", lambda m: f"width:{int(int(m.group(1)) * scale_factor)}px", style)
        style = re.sub(r"height:(\d+)px", lambda m: f"height:{int(int(m.group(1)) * scale_factor)}px", style)
        return f'class="PageContainer" id="Page" style="{style}"'

    content = re.sub(r'class="PageContainer"\s+id="Page"\s+style="([^"]*)"', scale_page_container, content)

    # Scale ImageContainer styles in <style> block
    def scale_style_block(match):
        style_content = match.group(1)
        # Scale all pixel values
        style_content = re.sub(
            r"(bottom|right|width|height):(-?\d+\.?\d*)px",
            lambda m: f"{m.group(1)}:{float(m.group(2)) * scale_factor:.2f}px",
            style_content,
        )
        return f"<style>{style_content}</style>"

    content = re.sub(r"<style[^>]*>(.*?)</style>", scale_style_block, content, flags=re.DOTALL)

    # Scale inline img dimensions
    def scale_img(match):
        full_match = match.group(0)
        full_match = re.sub(r'width="(\d+)"', lambda m: f'width="{int(int(m.group(1)) * scale_factor)}"', full_match)
        full_match = re.sub(r'height="(\d+)"', lambda m: f'height="{int(int(m.group(1)) * scale_factor)}"', full_match)
        return full_match

    content = re.sub(r"<img[^>]+>", scale_img, content)

    # Scale TextContainer transform scale (this is the key!)
    # The original scale is something like scale(0.031267)
    # We need to keep the SAME visual scale, so multiply by scale_factor
    def scale_transform(match):
        original_scale = float(match.group(1))
        new_scale = original_scale * scale_factor
        return f"transform:scale({new_scale:.6f})"

    content = re.sub(r"transform:scale\(([0-9.]+)\)", scale_transform, content)

    # Write scaled HTML
    output_path = output_dir / html_path.name
    output_path.write_text(content, encoding="utf-8")

    return output_path


def start_http_server(directory: Path, port: int):
    """Start HTTP server for the directory."""
    handler = make_http_handler(directory.resolve())
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def capture_with_gowitness(
    url: str, output_path: Path, width: int, height: int, timeout: int = 30, delay: int = 2
) -> bool:
    """Capture screenshot using gowitness (single URL - fallback method)."""
    temp_dir = output_path.parent / ".gowitness_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Clean temp dir
    for f in temp_dir.glob("*"):
        if f.is_file():
            f.unlink()

    # Check for custom Chrome path (e.g., in Docker)
    import os

    chrome_path = os.environ.get("CHROME_PATH", "")

    cmd = [
        "gowitness",
        "scan",
        "single",
        "-u",
        url,
        "-s",
        str(temp_dir),
        "--chrome-window-x",
        str(width),
        "--chrome-window-y",
        str(height),
        "-T",
        str(timeout),
        "--delay",
        str(delay),
        "--screenshot-format",
        "png",
        "--screenshot-fullpage",
        "--write-none",
        "-q",
    ]

    if chrome_path:
        cmd.extend(["--chrome-path", chrome_path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)

        if result.returncode != 0:
            if result.stderr and ("executable file not found" in result.stderr or "chrome" in result.stderr.lower()):
                print("\n⚠️  Chrome/Chromium not found. gowitness will download it automatically on first run.")
                result = subprocess.run(cmd, timeout=timeout + 30)
            elif result.returncode != 0:
                return False

        screenshots = list(temp_dir.glob("*.png")) + list(temp_dir.glob("*.jpeg")) + list(temp_dir.glob("*.jpg"))

        if screenshots:
            shutil.move(str(screenshots[0]), str(output_path))
            return True

        return False

    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def capture_batch_with_gowitness(
    urls_with_paths: List[Tuple[str, Path]],
    width: int,
    height: int,
    timeout: int = 30,
    delay: int = 2,
    threads: int = 4,
) -> int:
    """
    Capture multiple screenshots using gowitness scan file (batch mode).

    Args:
        urls_with_paths: List of (url, output_path) tuples
        width, height: Screenshot dimensions
        timeout: Timeout per page
        delay: Delay before screenshot
        threads: Number of parallel threads

    Returns:
        Number of successfully captured screenshots
    """
    if not urls_with_paths:
        return 0

    # Create temp directory for gowitness output
    output_dir = urls_with_paths[0][1].parent
    temp_dir = output_dir / ".gowitness_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Clean temp dir
    for f in temp_dir.glob("*"):
        if f.is_file():
            f.unlink()

    # Create URL file for gowitness
    urls_file = temp_dir / "urls.txt"
    url_to_output = {}  # Map URL to desired output path

    with open(urls_file, "w") as f:
        for url, output_path in urls_with_paths:
            f.write(f"{url}\n")
            url_to_output[url] = output_path

    # Check for custom Chrome path (e.g., in Docker)
    import os

    chrome_path = os.environ.get("CHROME_PATH", "")

    # Run gowitness scan file
    cmd = [
        "gowitness",
        "scan",
        "file",
        "-f",
        str(urls_file),
        "-s",
        str(temp_dir),
        "--chrome-window-x",
        str(width),
        "--chrome-window-y",
        str(height),
        "-T",
        str(timeout),
        "--delay",
        str(delay),
        "--screenshot-format",
        "png",
        "--screenshot-fullpage",
        "--write-none",
        "--no-https",  # URLs are already http://
        "-t",
        str(threads),
        "-q",
    ]

    if chrome_path:
        cmd.extend(["--chrome-path", chrome_path])

    try:
        # Calculate total timeout based on number of URLs
        total_timeout = max(300, len(urls_with_paths) * (timeout + delay) // threads + 60)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=total_timeout)

        if result.returncode != 0 and result.stderr:
            if "executable file not found" in result.stderr or "chrome" in result.stderr.lower():
                print("\n⚠️  Chrome/Chromium not found. gowitness will download it automatically on first run.")
                subprocess.run(cmd, timeout=total_timeout)

    except subprocess.TimeoutExpired:
        print("\n⚠️  Batch capture timed out")
    except Exception as e:
        print(f"\n⚠️  Error in batch capture: {e}")

    # Map generated screenshots to output paths
    # gowitness creates files like: http---127.0.0.1-PORT-filename.xhtml.png
    success_count = 0

    for screenshot in temp_dir.glob("*.png"):
        # Parse the filename to find the original URL
        # Format: http---127.0.0.1-PORT-path.png
        filename = screenshot.name

        # Find matching URL
        matched = False
        for url, output_path in urls_with_paths:
            # Convert URL to expected filename pattern
            # http://127.0.0.1:PORT/file.xhtml -> http---127.0.0.1-PORT-file.xhtml.png
            expected_name = url.replace("://", "---").replace(":", "-").replace("/", "-")
            if expected_name.endswith("-"):
                expected_name = expected_name[:-1]
            expected_name += ".png"

            if filename == expected_name:
                shutil.move(str(screenshot), str(output_path))
                success_count += 1
                matched = True
                break

        # If no exact match, try partial match
        if not matched:
            for url, output_path in urls_with_paths:
                # Extract the page name from URL
                url_path = url.split("/")[-1]
                if url_path in filename and not output_path.exists():
                    shutil.move(str(screenshot), str(output_path))
                    success_count += 1
                    break

    return success_count


# =============================================================================
# CBR Creation
# =============================================================================


def convert_to_jpeg(screenshots_dir: Path, quality: int = 92) -> int:
    """Convert PNG screenshots to JPEG for smaller file size."""
    if Image is None:
        return 0

    png_files = sorted(screenshots_dir.glob("page_*.png"))
    if not png_files:
        return 0

    converted = 0
    for png_path in png_files:
        try:
            with Image.open(png_path) as img:
                # Convert to RGB if necessary (PNG might have alpha)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                jpg_path = png_path.with_suffix(".jpg")
                img.save(jpg_path, "JPEG", quality=quality, optimize=True)

                # Remove original PNG
                png_path.unlink()
                converted += 1
        except Exception as e:
            print(f"\n⚠️  Error converting {png_path.name}: {e}")

    return converted


def check_zip_available() -> bool:
    """Check if zip command is available for CBZ creation."""
    try:
        subprocess.run(["zip", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def create_cbz(screenshots_dir: Path, output_path: Path) -> bool:
    """Create CBZ (ZIP) file from images."""
    images = sorted(screenshots_dir.glob("page_*.jpg"))
    if not images:
        images = sorted(screenshots_dir.glob("page_*.png"))
    if not images:
        images = sorted(screenshots_dir.glob("*.jpg")) + sorted(screenshots_dir.glob("*.png"))

    if not images:
        print("❌ Error: No images found")
        return False

    if output_path.suffix.lower() != ".cbz":
        output_path = output_path.with_suffix(".cbz")

    if output_path.exists():
        output_path.unlink()

    print(f"📦 Creating CBZ: {output_path.name}")
    print(f"   Adding {len(images)} images...")

    output_path_abs = output_path.resolve()

    # zip command: -j = junk paths (store just the file name)
    cmd = ["zip", "-j", "-q", str(output_path_abs)] + [str(img.resolve()) for img in images]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(screenshots_dir.resolve()))

        if result.returncode == 0:
            size_mb = output_path_abs.stat().st_size / (1024 * 1024)
            print(f"✅ CBZ created: {output_path_abs.name} ({size_mb:.1f} MB)")
            return True
        else:
            print(f"❌ Error: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def create_cbr(screenshots_dir: Path, output_path: Path, jpeg_quality: int = 92, epub_type: str = "screenshot") -> bool:
    """Create CBR (RAR) or CBZ (ZIP) file from images.

    Args:
        jpeg_quality: JPEG quality (1-100). Set to 0 to keep PNG format.
        epub_type: Type of EPUB conversion ('extract' or 'screenshot'). Only converts to JPEG for 'screenshot'.

    Falls back to CBZ if RAR is not available.
    """
    use_cbz = not check_rar_available()

    if use_cbz and not check_zip_available():
        print("❌ Error: Neither 'rar' nor 'zip' command found")
        return False

    # Convert PNGs to JPEG only for screenshot mode (unless disabled)
    # Extract mode already has high-quality images, no need to convert
    if jpeg_quality > 0 and epub_type == "screenshot":
        png_count = len(list(screenshots_dir.glob("page_*.png")))
        if png_count > 0:
            print(f"🔄 Converting {png_count} images to JPEG (quality={jpeg_quality})...")
            converted = convert_to_jpeg(screenshots_dir, jpeg_quality)
            if converted > 0:
                print(f"   Converted {converted} images")

    # Use CBZ if RAR not available
    if use_cbz:
        cbz_path = output_path.with_suffix(".cbz")
        return create_cbz(screenshots_dir, cbz_path)

    images = sorted(screenshots_dir.glob("page_*.jpg"))
    if not images:
        images = sorted(screenshots_dir.glob("page_*.png"))
    if not images:
        images = sorted(screenshots_dir.glob("*.jpg")) + sorted(screenshots_dir.glob("*.png"))

    if not images:
        print("❌ Error: No images found")
        return False

    if output_path.suffix.lower() != ".cbr":
        output_path = output_path.with_suffix(".cbr")

    if output_path.exists():
        output_path.unlink()

    print(f"📦 Creating CBR: {output_path.name}")
    print(f"   Adding {len(images)} images...")

    # Use absolute paths for rar command
    output_path_abs = output_path.resolve()
    images_abs = [img.resolve() for img in images]

    cmd = ["rar", "a", "-ep", "-m0", str(output_path_abs)] + [str(img) for img in images_abs]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(screenshots_dir.resolve()))

        if result.returncode == 0:
            size_mb = output_path_abs.stat().st_size / (1024 * 1024)
            print(f"✅ CBR created: {output_path_abs.name} ({size_mb:.1f} MB)")
            return True
        else:
            print(f"❌ Error: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Main Conversion
# =============================================================================


def convert_epub(
    epub_path: Path,
    output_dir: Optional[Path] = None,
    mode: str = "auto",
    timeout: int = 30,
    delay: int = 1,
    keep_extracted: bool = False,
    threads: int = 4,
) -> Tuple[Path, str]:
    """
    Main conversion function.

    Args:
        epub_path: Path to EPUB file
        output_dir: Output directory
        mode: 'auto', 'extract', 'screenshot'
        timeout: Timeout for gowitness
        delay: Delay before screenshot
        keep_extracted: Keep extracted EPUB files
        threads: Number of parallel threads for screenshot mode

    Returns:
        Tuple of (screenshots_dir, epub_type)
    """
    epub_path = Path(epub_path).resolve()

    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")

    if output_dir is None:
        output_dir = epub_path.parent / f"{epub_path.stem}_images"
    else:
        output_dir = Path(output_dir).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Extract EPUB
        extract_path = extract_epub(epub_path, temp_path)

        # Get HTML files
        html_files = get_ordered_html_files(extract_path)
        if not html_files:
            raise ValueError("No HTML files found in EPUB")

        # Determine EPUB type
        epub_type, vp_width, vp_height, scale_factor = determine_epub_type(html_files)

        # Override if manual mode specified
        if mode == "extract":
            epub_type = "extract"
        elif mode == "screenshot":
            epub_type = "screenshot"

        # Calculate render size (scaled up for better quality)
        render_width = int(vp_width * scale_factor)
        render_height = int(vp_height * scale_factor)

        # Print detected type
        type_descriptions = {
            "extract": "📄 Type: Text embedded in images → Direct extraction (best quality)",
            "screenshot": "📄 Type: Text in HTML overlay → Screenshot with gowitness",
        }
        print(type_descriptions.get(epub_type, f"📄 Type: {epub_type}"))
        print(f"📐 Viewport: {vp_width}x{vp_height}")
        if scale_factor > 1.0 and epub_type == "screenshot":
            print(f"📐 Render size: {render_width}x{render_height} (scale: {scale_factor:.2f}x)")
        print(f"📄 Processing {len(html_files)} pages...")

        # Check dependencies
        if epub_type == "screenshot":
            if not check_dependencies(need_gowitness=True):
                raise RuntimeError("Missing dependencies for screenshot mode")
        else:
            if not check_dependencies(need_gowitness=False):
                raise RuntimeError("Missing dependencies")

        # Process based on type
        success_count = 0
        total = len(html_files)

        if epub_type == "extract":
            # Direct extraction - handles both single and multiple images
            for i, html_path in enumerate(html_files, 1):
                print(f"\r📸 Processing page {i}/{total}: {html_path.name[:40]:<40}", end="", flush=True)

                output_path = screenshots_dir / f"page_{i:04d}.png"

                # Use concatenate which handles both single and multi-image pages
                if concatenate_images(html_path, output_path):
                    success_count += 1

            print()

        else:
            # Screenshot mode with gowitness
            # Find OEBPS directory
            oebps_dir = None
            for html_file in html_files:
                if "OEBPS" in str(html_file):
                    oebps_dir = html_file.parent
                    while oebps_dir.name != "OEBPS" and oebps_dir != extract_path:
                        oebps_dir = oebps_dir.parent
                    break

            if oebps_dir is None:
                oebps_dir = html_files[0].parent

            # Prepare scaled HTML files if needed
            scaled_dir = None
            if scale_factor > 1.0:
                scaled_dir = temp_path / "scaled_html"
                scaled_dir.mkdir(parents=True, exist_ok=True)
                # Copy CSS and other resources
                for css_dir in oebps_dir.glob("css"):
                    shutil.copytree(css_dir, scaled_dir / "css", dirs_exist_ok=True)
                for font_dir in oebps_dir.glob("fonts"):
                    shutil.copytree(font_dir, scaled_dir / "fonts", dirs_exist_ok=True)
                # Link images directory (don't copy, too large)
                images_src = oebps_dir / "images"
                if images_src.exists():
                    images_dst = scaled_dir / "images"
                    if not images_dst.exists():
                        images_dst.symlink_to(images_src)

            # Start HTTP server on the appropriate directory
            serve_dir = scaled_dir if scaled_dir else oebps_dir
            port = find_free_port()
            server = start_http_server(serve_dir, port)
            time.sleep(0.5)

            print(f"🌐 Started local server on port {port}")

            try:
                # Prepare all URLs and output paths first
                print(f"📝 Preparing {total} pages for batch capture...")
                urls_with_paths = []

                for i, html_path in enumerate(html_files, 1):
                    # Prepare scaled HTML if needed
                    if scaled_dir and scale_factor > 1.0:
                        scaled_html = prepare_scaled_html(html_path, scaled_dir, scale_factor)
                        url_path = scaled_html.name
                    else:
                        try:
                            url_path = str(html_path.relative_to(oebps_dir))
                        except ValueError:
                            url_path = html_path.name

                    url = f"http://127.0.0.1:{port}/{url_path}"
                    output_path = screenshots_dir / f"page_{i:04d}.png"
                    urls_with_paths.append((url, output_path))

                # Batch capture with gowitness
                print(f"📸 Capturing {total} pages in parallel (threads={threads})...")
                success_count = capture_batch_with_gowitness(
                    urls_with_paths, render_width, render_height, timeout, delay, threads=threads
                )

                # Check for any missing pages and retry individually
                missing = [(url, path) for url, path in urls_with_paths if not path.exists()]
                if missing:
                    print(f"\n🔄 Retrying {len(missing)} failed pages individually...")
                    for url, output_path in missing:
                        if capture_with_gowitness(url, output_path, render_width, render_height, timeout, delay):
                            success_count += 1

                # Final check for failed pages
                final_missing = [(url, path) for url, path in urls_with_paths if not path.exists()]
                if final_missing:
                    print(f"\n⚠️  WARNING: {len(final_missing)} pages failed to capture:")
                    for url, path in final_missing:
                        page_name = url.split("/")[-1]
                        print(f"   - {page_name}")

            finally:
                server.shutdown()

        print(f"✅ Successfully processed {success_count}/{total} pages")

        # Warn if there are missing pages
        if success_count < total:
            print(f"⚠️  {total - success_count} pages are missing from the output!")

        # Keep extracted files if requested
        if keep_extracted:
            kept_path = output_dir / "extracted"
            if kept_path.exists():
                shutil.rmtree(kept_path)
            shutil.copytree(extract_path, kept_path)
            print(f"📁 Extracted files kept at: {kept_path}")

        # Cleanup
        temp_gowitness = screenshots_dir / ".gowitness_temp"
        if temp_gowitness.exists():
            shutil.rmtree(temp_gowitness)

    print(f"📸 Images saved to: {screenshots_dir}")
    return screenshots_dir, epub_type


# =============================================================================
# CLI
# =============================================================================


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="Convert EPUB comic/manga files to images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  auto        Automatically detect best method (default)
  extract     Force direct image extraction
  screenshot  Force screenshot with gowitness

Examples:
  %(prog)s manga.epub                    # Auto-detect mode
  %(prog)s manga.epub --mode extract     # Force extraction
  %(prog)s manga.epub --mode screenshot  # Force screenshot
  %(prog)s manga.epub --cbr-only         # Create CBR, delete images
        """,
    )

    parser.add_argument("epub", type=Path, help="EPUB file to convert")
    parser.add_argument("-o", "--output", type=Path, help="Output directory")
    parser.add_argument(
        "-m", "--mode", choices=["auto", "extract", "screenshot"], default="auto", help="Conversion mode"
    )
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per page (screenshot mode)")
    parser.add_argument("--delay", type=int, default=2, help="Delay before screenshot")
    parser.add_argument("--keep-extracted", action="store_true", help="Keep extracted EPUB files")
    parser.add_argument("--cbr", action="store_true", help="Create CBR file")
    parser.add_argument("--cbr-only", action="store_true", help="Create CBR and delete images")
    parser.add_argument("--jpeg-quality", type=int, default=92, help="JPEG quality for CBR (1-100, default: 92)")
    parser.add_argument("--no-jpeg", action="store_true", help="Keep PNG format, don't convert to JPEG")
    parser.add_argument(
        "--threads", type=int, default=4, help="Number of parallel threads for screenshot mode (default: 4)"
    )
    parser.add_argument("--check-deps", action="store_true", help="Check dependencies")

    args = parser.parse_args()

    if args.check_deps:
        print("Checking dependencies...")
        if check_dependencies(need_gowitness=True):
            print("✅ All dependencies OK")
            if check_rar_available():
                print("✅ RAR available for CBR creation")
            else:
                print("⚠️  RAR not available (CBR creation disabled)")
        sys.exit(0)

    start_time = time.time()

    try:
        screenshots_dir, epub_type = convert_epub(
            epub_path=args.epub,
            output_dir=args.output,
            mode=args.mode,
            timeout=args.timeout,
            delay=args.delay,
            keep_extracted=args.keep_extracted,
            threads=args.threads,
        )

        # Create CBR if requested
        if args.cbr or args.cbr_only:
            # Create 'converted' directory next to the EPUB file (use absolute path)
            converted_dir = args.epub.resolve().parent / "converted"
            converted_dir.mkdir(parents=True, exist_ok=True)

            cbr_path = converted_dir / f"{args.epub.stem}.cbr"
            jpeg_quality = 0 if args.no_jpeg else args.jpeg_quality
            if create_cbr(screenshots_dir, cbr_path, jpeg_quality, epub_type):
                # Delete the entire _images directory after creating CBR
                images_dir = screenshots_dir.parent
                if images_dir.exists():
                    shutil.rmtree(images_dir)
                    print("🗑️  Deleted working directory")

        elapsed = time.time() - start_time
        minutes, seconds = divmod(int(elapsed), 60)
        time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

        print("\n🎉 Conversion complete!")
        print(f"⏱️  Total time: {time_str}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
