#!/usr/bin/env python3
"""
epub2cbr - Convert EPUB/PDF comic/manga files to CBR

Supports:
- EPUB files with three types of structures:
  1. Single image WITH text in image -> Extract directly (best quality)
  2. Single/Split images WITHOUT text in image -> Screenshot with gowitness
  3. Split images WITH text in image -> Concatenate images (best quality)
- PDF files -> Extract pages as images directly
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

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def print_banner():
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                       epub2cbr                            ║
    ║       EPUB/PDF to CBR Converter for Comics/Manga          ║
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
# PDF Conversion
# =============================================================================


def check_pdf_available() -> bool:
    """Check if PyMuPDF is available for PDF processing."""
    return fitz is not None


def convert_pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = 0,
    jpeg_quality: int = 92,
    normalize: bool = False,
) -> Tuple[Path, int, int]:
    """
    Convert PDF pages to images.
    If normalize=True, it finds the smallest page dimensions and
    center-crops larger pages to match exactly (removing margins).
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed. Install with: pip install PyMuPDF")

    if Image is None and normalize:
        raise RuntimeError(
            "Pillow not installed, which is required for normalization. Install with: pip install Pillow"
        )

    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    print(f"📄 Opening PDF: {pdf_path.name}")

    pdf = fitz.open(pdf_path)
    total_pages = len(pdf)

    print(f"📄 PDF has {total_pages} pages")

    # Auto-detect optimal DPI from embedded images if not specified
    if dpi == 0:
        dpi = _detect_optimal_dpi(pdf)

    print(f"📐 Rendering at {dpi} DPI")

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    target_width, target_height = 0, 0
    if normalize and total_pages > 0:
        print("📏 Analyzing page sizes for normalization...")
        min_area = float("inf")

        # First pass: Find the smallest page area to act as the target
        # Using the smallest page ensures we crop larger ones rather than upscaling (blurring) smaller ones
        for page_num in range(total_pages):
            page = pdf[page_num]
            rect = page.rect
            width = int(rect.width * zoom)
            height = int(rect.height * zoom)

            area = width * height
            if area < min_area:
                min_area = area
                target_width = width
                target_height = height

        if target_width > 0:
            print(f"📏 Normalizing all pages to strict size: {target_width}x{target_height} pixels (Center Crop)")
        else:
            normalize = False

    success_count = 0

    for page_num in range(total_pages):
        print(f"\r📸 Processing page {page_num + 1}/{total_pages}", end="", flush=True)

        try:
            page = pdf[page_num]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png_path = screenshots_dir / f"page_{page_num + 1:04d}.png"

            # Check if processing is needed
            if normalize and (pix.width != target_width or pix.height != target_height):
                # Convert to PIL Image
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                # --- Aspect Fill + Center Crop Logic ---

                # 1. Calculate scale factors for both dimensions
                width_ratio = target_width / img.width
                height_ratio = target_height / img.height

                # 2. Use the LARGER ratio to ensure the image fills the target completely
                scale = max(width_ratio, height_ratio)

                # 3. Calculate new dimensions (will be >= target dimensions)
                new_width = int(round(img.width * scale))
                new_height = int(round(img.height * scale))

                # Safety check to prevent rounding errors making it 1px too small
                new_width = max(new_width, target_width)
                new_height = max(new_height, target_height)

                # 4. Resize the image
                if scale != 1.0:
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # 5. Center Crop to exact target size
                left = (new_width - target_width) // 2
                top = (new_height - target_height) // 2
                right = left + target_width
                bottom = top + target_height

                img = img.crop((left, top, right, bottom))
                img.save(str(png_path))
            else:
                # Save directly if no normalization needed or size already matches exactly
                pix.save(str(png_path))

            success_count += 1

        except Exception as e:
            print(f"\n⚠️  Error on page {page_num + 1}: {e}")

    pdf.close()
    print()  # New line after progress

    print(f"✅ Successfully extracted {success_count}/{total_pages} pages")
    print(f"📸 Images saved to: {screenshots_dir}")

    return screenshots_dir, success_count, total_pages


def _detect_optimal_dpi(pdf) -> int:
    """
    Detect optimal DPI by analyzing embedded images in the PDF.

    Returns DPI that will render pages at the same resolution as embedded images.
    """
    sample_pages = min(5, len(pdf))

    for page_num in range(sample_pages):
        page = pdf[page_num]
        images = page.get_images(full=True)

        if not images:
            continue

        # Get page dimensions in points
        page_width = page.rect.width
        page_height = page.rect.height

        # Find the largest image on this page
        for img in images:
            xref = img[0]
            try:
                base_image = pdf.extract_image(xref)
                img_width = base_image.get("width", 0)
                img_height = base_image.get("height", 0)

                if img_width > 0 and img_height > 0:
                    # Calculate DPI that would produce this image size
                    dpi_x = (img_width / page_width) * 72
                    dpi_y = (img_height / page_height) * 72
                    calculated_dpi = int((dpi_x + dpi_y) / 2)

                    if calculated_dpi > 72:  # Sanity check
                        print(f"📐 Detected image resolution: {img_width}x{img_height} → {calculated_dpi} DPI")
                        return calculated_dpi

            except Exception:
                continue

    # Default fallback
    print("📐 No embedded images found, using default 150 DPI")
    return 150


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

        if not result["has_text_content"]:
            # Look for span elements with id like _idTextSpan
            span_text_match = re.findall(r'<span[^>]+id=["\']_idTextSpan\d*["\'][^>]*>([^<]+)</span>', content)
            if span_text_match:
                all_span_text = "".join(span_text_match).strip()
                text_without_numbers = re.sub(r"\d+", "", all_span_text).strip()
                has_letters = bool(re.search(r"[a-zA-ZáéíóúñÁÉÍÓÚÑàèìòùäëïöüâêîôûçœæ]", text_without_numbers))
                result["has_text_content"] = has_letters

        # Fallback: Check for generic text content if specific containers weren't found
        # This handles standard reflowable EPUBs
        if not result["has_text_content"]:
            # Remove scripts, styles, and tags
            clean_text = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
            clean_text = re.sub(r"<style[^>]*>.*?</style>", "", clean_text, flags=re.DOTALL)
            clean_text = re.sub(r"<[^>]+>", " ", clean_text)

            # Count words (simple split)
            words = clean_text.split()
            # If page has significant text (>50 words), treat as text content
            if len(words) > 50:
                result["has_text_content"] = True

        # Check if images are too small (sprites) - they need screenshot mode
        # If average image is much smaller than viewport, it's likely sprites
        if result["image_paths"] and result["viewport"][0] > 0 and Image:
            try:
                total_area = 0
                viewport_area = result["viewport"][0] * result["viewport"][1]
                for img_path in result["image_paths"][:5]:
                    with Image.open(img_path) as img:
                        total_area += img.width * img.height
                avg_area = total_area / min(len(result["image_paths"]), 5)
                # If average image area is less than 10% of viewport, they're sprites
                if avg_area < viewport_area * 0.1:
                    result["has_text_content"] = True  # Force screenshot mode
            except Exception:
                pass

    except Exception as e:
        print(f"⚠️  Warning: Error analyzing {html_path.name}: {e}")

    return result


def determine_epub_type(
    html_files: List[Path], default_viewport: Tuple[int, int], sample_size: int = 5
) -> Tuple[str, int, int, float, bool]:
    """
    Analyze sample pages to determine EPUB type.

    Returns:
        (epub_type, width, height, scale_factor, needs_autocrop)

    epub_type:
        'extract': Text is embedded in images -> extract directly (best quality)
        'screenshot': Text is in HTML overlay -> need gowitness to render

    Logic:
        - If TextContainer has real text content -> screenshot (text needs rendering)
        - If TextContainer is empty or only has page numbers -> extract images directly

    scale_factor:
        - Ratio between actual image resolution and viewport
        - Used to render screenshots at full resolution

    needs_autocrop:
        - True if viewport is larger than actual image content
        - Used to remove white borders from screenshots
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

    # Check if viewport is larger than image content (needs autocrop)
    # This happens when images don't fill the entire viewport
    needs_autocrop = False
    if max_viewport[0] > 0 and max_image_res[0] > 0:
        # If image is significantly smaller than viewport (< 95%), enable autocrop
        if max_image_res[0] < max_viewport[0] * 0.95 or max_image_res[1] < max_viewport[1] * 0.95:
            needs_autocrop = True

    # If ANY page has text in HTML, we need screenshot mode
    # because text won't appear if we just extract images
    if max_viewport[0] == 0:
        # If no viewport found (reflowable), use default
        max_viewport = default_viewport

    # If ANY page has text in HTML, we need screenshot mode
    # because text won't appear if we just extract images
    if pages_with_text > 0:
        return "screenshot", max_viewport[0], max_viewport[1], scale_factor, needs_autocrop
    else:
        return "extract", max_viewport[0], max_viewport[1], scale_factor, needs_autocrop


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
        # Add MIME type for .xhtml files - Chrome needs text/html to render properly
        # CRITICAL: Force UTF-8 charset to ensure Spanish/special characters render correctly
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            ".xhtml": "text/html; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".htm": "text/html; charset=utf-8",
        }

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


def scale_css_file(css_path: Path, output_path: Path, scale_factor: float) -> None:
    """
    Scale all pixel values in an external CSS file.

    This handles CSS files from InDesign-exported EPUBs that use
    absolute positioning with fixed pixel values.
    """
    if scale_factor <= 1.0:
        shutil.copy(css_path, output_path)
        return

    content = css_path.read_text(encoding="utf-8", errors="ignore")

    # Scale all pixel values in CSS properties
    # Match properties like: width:396.85px; height:572.60px; left:0px; top:0px;
    def scale_px_value(match):
        prop = match.group(1)
        value = float(match.group(2))
        scaled = value * scale_factor
        return f"{prop}:{scaled:.2f}px"

    content = re.sub(
        r"(width|height|left|right|top|bottom|min-width|min-height|max-width|max-height)\s*:\s*(-?\d+\.?\d*)px",
        scale_px_value,
        content,
        flags=re.IGNORECASE,
    )

    # Scale translate values in transform
    def scale_translate(match):
        prefix = match.group(1)
        x = float(match.group(2)) * scale_factor
        y = float(match.group(3)) * scale_factor
        suffix = match.group(4)
        return f"{prefix}translate({x:.3f}px,{y:.3f}px){suffix};"

    content = re.sub(
        r"(-webkit-transform:|transform:)translate\((-?\d+\.?\d*)px,(-?\d+\.?\d*)px\)(.*?);",
        scale_translate,
        content,
    )

    output_path.write_text(content, encoding="utf-8")


def prepare_scaled_html(html_path: Path, output_dir: Path, scale_factor: float, skip_css_scaling: bool = False) -> Path:
    """
    Create a scaled version of the HTML file for high-resolution screenshots.

    This modifies:
    1. viewport meta tag
    2. body dimensions
    3. All absolute pixel values in styles (unless skip_css_scaling=True)
    """
    # 2. READ THE FILE FIRST
    content = html_path.read_text(encoding="utf-8", errors="ignore")

    # 3. Inject XML declaration for .xhtml if missing
    if html_path.suffix.lower() == ".xhtml":
        if "<?xml" not in content[:100]:
            content = '<?xml version="1.0" encoding="utf-8"?>\n' + content

    # 4. APPLY INDESIGN FIX (Force visible overflow) - ONLY IF NOT InDesign (which uses skip_css_scaling)
    # The user reported "worse than before", likely seeing cutting edges or overlapping pages.
    # InDesign files have precise layout, so overflow:hidden is usually correct.
    if not skip_css_scaling:
        content = content.replace("overflow:hidden", "overflow:visible")
        content = content.replace("overflow: hidden", "overflow: visible")

    # 3. Ensure UTF-8 charset meta tag is present (before viewport)
    # This specifically addresses the Spanish character rendering issue
    if "charset=" not in content.lower():
        # Inject <meta charset="utf-8"> if not present
        if "<head" in content.lower():
            # Inject right after opening <head> tag
            content = re.sub(
                r"(<head[^>]*>)", r"\1\n    <meta charset=\"utf-8\"/>", content, count=1, flags=re.IGNORECASE
            )
        else:
            # If no head, just put it at the very top (not ideal but better than nothing)
            content = '<meta charset="utf-8"/>\n' + content
    else:
        # Update existing charset to utf-8 if it's different
        content = re.sub(r"(charset\s*=\s*)[\"']?[a-zA-Z0-9-]+[\"']?", r'\1"utf-8"', content, flags=re.IGNORECASE)

    # 4. If scale_factor is 1.0, we can skip the rest of the scaling logic
    # but we still need to write the file because we potentially injected charset meta tags
    if scale_factor <= 1.0:
        output_path = output_dir / html_path.name
        output_path.write_text(content, encoding="utf-8")
        return output_path

    # 5. Scale viewport meta tag
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

    # Scale ImageContainer/TextContainer/etc styles in <style> block
    # ALWAYS DO THIS - InDesign needs ImageContainer scaling
    def scale_style_block(match):
        style_content = match.group(1)
        # Scale all pixel values - relax regex to handle whitespace
        style_content = re.sub(
            r"(bottom|right|width|height)\s*:\s*(-?\d+\.?\d*)px",
            lambda m: f"{m.group(1)}:{float(m.group(2)) * scale_factor:.2f}px",
            style_content,
            flags=re.IGNORECASE,
        )
        # Also scale top/left for ImageContainers
        style_content = re.sub(
            r"(top|left)\s*:\s*(-?\d+\.?\d*)px",
            lambda m: f"{m.group(1)}:{float(m.group(2)) * scale_factor:.2f}px",
            style_content,
            flags=re.IGNORECASE,
        )
        return f"<style>{style_content}</style>"

    content = re.sub(r"<style[^>]*>(.*?)</style>", scale_style_block, content, flags=re.DOTALL | re.IGNORECASE)

    # Scale inline img dimensions (both attribute and style)
    def scale_img(match):
        full_match = match.group(0)
        # Scale width/height attributes
        full_match = re.sub(r'width="(\d+)"', lambda m: f'width="{int(int(m.group(1)) * scale_factor)}"', full_match)
        full_match = re.sub(r'height="(\d+)"', lambda m: f'height="{int(int(m.group(1)) * scale_factor)}"', full_match)
        # Scale width/height in inline style
        full_match = re.sub(
            r'style="([^"]*)"',
            lambda m: 'style="'
            + re.sub(
                r"(width|height):(\d+)px",
                lambda px: f"{px.group(1)}:{int(int(px.group(2)) * scale_factor)}px",
                m.group(1),
            )
            + '"',
            full_match,
        )
        return full_match

    content = re.sub(r"<img[^>]+>", scale_img, content)

    # Scale TextContainer transform scale
    def scale_transform(match):
        original_scale = float(match.group(1))
        new_scale = original_scale * scale_factor
        return f"scale({new_scale:.6f})"

    # Match scale() with single parameter
    content = re.sub(r"scale\(([0-9.]+)\)(?!\s*,)", scale_transform, content)

    # Scale translate values in inline transform styles
    # Usually safe to always do this as standard transforms are unit-aware?
    # Actually, keep it conditional if in doubt, but usually transforms are scaled.
    # The bug was about pixel values inside the transformed container.
    def scale_translate(match):
        x = float(match.group(1)) * scale_factor
        y = float(match.group(2)) * scale_factor
        return f"translate({x:.2f}px,{y:.2f}px)"

    content = re.sub(r"translate\((-?\d+\.?\d*)px,(-?\d+\.?\d*)px\)", scale_translate, content)

    # CRITICAL: Scale inline styles on SPAN/DIV elements?
    # If using skip_css_scaling (InDesign), we MUST NOT scale spans because
    # they use huge coordinates inside a scaled container.
    # But we MIGHT need to scale other divs?
    # For now, let's keep the behavior:
    # If NOT skip_css_scaling: find all inline style="..." and scale px values.
    # If skip_css_scaling: DO NOTHING to other inline styles.

    if not skip_css_scaling:
        # Scale generic inline styles (top, left, etc) on ANY element
        # This was the logic causing the double scaling on spans
        def scale_inline_style(match):
            style_content = match.group(1)
            style_content = re.sub(
                r"(bottom|right|width|height|top|left):(-?\d+\.?\d*)px",
                lambda m: f"{m.group(1)}:{float(m.group(2)) * scale_factor:.2f}px",
                style_content,
            )
            return f'style="{style_content}"'

        content = re.sub(r'style="([^"]*)"', scale_inline_style, content)

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
    url: str, output_path: Path, width: int, height: int, timeout: int = 30, delay: int = 2, fullpage: bool = True
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
        "--write-none",
        "-q",
    ]

    if fullpage:
        cmd.insert(-2, "--screenshot-fullpage")

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
    fullpage: bool = True,
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
        "--write-none",
        "--no-https",  # URLs are already http://
        "-t",
        str(threads),
        "-q",
    ]

    if fullpage:
        cmd.insert(-4, "--screenshot-fullpage")

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


def autocrop_image(img: "Image.Image", tolerance: int = 20) -> "Image.Image":
    """
    Remove white/near-white borders from an image.

    Args:
        img: PIL Image object
        tolerance: How close to white (255) to consider as border (0-255)

    Returns:
        Cropped image
    """
    import numpy as np

    # Convert to RGB if needed
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Convert to numpy array
    arr = np.array(img)

    # Find non-white pixels (where any channel is below 255-tolerance)
    threshold = 255 - tolerance
    non_white = np.any(arr < threshold, axis=2)

    # Find bounding box of non-white content
    rows = np.any(non_white, axis=1)
    cols = np.any(non_white, axis=0)

    if not rows.any() or not cols.any():
        # Image is entirely white/near-white, return as-is
        return img

    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]

    # Add small padding (1px) to avoid cutting content
    padding = 1
    rmin = max(0, rmin - padding)
    rmax = min(arr.shape[0] - 1, rmax + padding)
    cmin = max(0, cmin - padding)
    cmax = min(arr.shape[1] - 1, cmax + padding)

    # Crop if we're removing any whitespace (no minimum threshold)
    cropped_width = cmax - cmin + 1
    cropped_height = rmax - rmin + 1

    if cropped_width < img.width or cropped_height < img.height:
        return img.crop((cmin, rmin, cmax + 1, rmax + 1))

    return img


def convert_to_jpeg(screenshots_dir: Path, quality: int = 92, autocrop: bool = True) -> int:
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

                # Auto-crop white borders if enabled
                if autocrop:
                    try:
                        img = autocrop_image(img)
                    except ImportError:
                        pass  # numpy not available, skip autocrop

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


def create_cbr(
    screenshots_dir: Path,
    output_path: Path,
    jpeg_quality: int = 92,
    source_type: str = "screenshot",
    autocrop: bool = False,
) -> bool:
    """Create CBR (RAR) or CBZ (ZIP) file from images.

    Args:
        jpeg_quality: JPEG quality (1-100). Set to 0 to keep PNG format.
        source_type: Type of source ('extract', 'screenshot', 'pdf').
                     Converts to JPEG for 'screenshot' and 'pdf' modes.
        autocrop: If True, remove white borders from images (for InDesign EPUBs where viewport > image).

    Falls back to CBZ if RAR is not available.
    """
    use_cbz = not check_rar_available()

    if use_cbz and not check_zip_available():
        print("❌ Error: Neither 'rar' nor 'zip' command found")
        return False

    # Convert PNGs to JPEG (unless disabled)
    if jpeg_quality > 0:
        png_count = len(list(screenshots_dir.glob("page_*.png")))
        if png_count > 0:
            autocrop_msg = " with autocrop" if autocrop else ""
            print(f"🔄 Converting {png_count} images to JPEG (quality={jpeg_quality}){autocrop_msg}...")
            converted = convert_to_jpeg(screenshots_dir, jpeg_quality, autocrop=autocrop)
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
    force_indesign: bool = False,
    viewport_arg: Optional[str] = None,
) -> Tuple[Path, str, int, int, bool]:
    """
    Main conversion function.
    """
    # Parse viewport argument
    default_viewport = (1200, 1600)
    if viewport_arg:
        try:
            w, h = map(int, viewport_arg.lower().split("x"))
            default_viewport = (w, h)
        except ValueError:
            print(f"⚠️  Invalid viewport format '{viewport_arg}', using default 1200x1600")
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
        epub_type, vp_width, vp_height, scale_factor, needs_autocrop = determine_epub_type(html_files, default_viewport)

        # --- SMART INDESIGN DETECTION ---
        is_indesign = force_indesign
        padding_height = 0

        if epub_type == "screenshot" and not is_indesign:
            # Scan first 5 pages for InDesign signatures
            scan_limit = min(5, len(html_files))
            for i in range(scan_limit):
                try:
                    sample_content = html_files[i].read_text(encoding="utf-8", errors="ignore")
                    if (
                        "idGeneratedStyles" in sample_content
                        or "_idContainer" in sample_content
                        or ("TextContainer" in sample_content and "transform:scale" in sample_content.replace(" ", ""))
                    ):
                        is_indesign = True
                        break
                except Exception:
                    continue

        if is_indesign:
            print("🕵️  Detected Adobe InDesign format: Applying layout fixes.")
            print("   -> Disabled AutoCrop")
            print("   -> Enabled FullPage Screenshot (to prevent clipping)")
            print("   -> Added 60px vertical padding")

            # Fix 1: Disable autocrop to keep odd/even pages consistent
            needs_autocrop = False

            # Fix 2: Add padding to bottom to catch page numbers
            padding_height = 60
        # --------------------------------

        # Override if manual mode specified
        if mode == "extract":
            epub_type = "extract"
        elif mode == "screenshot":
            epub_type = "screenshot"

        # Calculate render size (scaled up for better quality)
        render_width = int(vp_width * scale_factor)
        # CRITICAL FIX: Add padding_height here
        render_height = int(vp_height * scale_factor) + padding_height

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
        failed_extractions = []  # Track pages that failed extraction for screenshot fallback

        if epub_type == "extract":
            # Direct extraction - handles both single and multiple images
            for i, html_path in enumerate(html_files, 1):
                print(f"\r📸 Processing page {i}/{total}: {html_path.name[:40]:<40}", end="", flush=True)

                output_path = screenshots_dir / f"page_{i:04d}.png"

                # Use concatenate which handles both single and multi-image pages
                if concatenate_images(html_path, output_path):
                    success_count += 1
                else:
                    # Track failed extractions for screenshot fallback
                    failed_extractions.append((i, html_path, output_path))

            print()

            # Fallback: use screenshot for pages that couldn't be extracted
            if failed_extractions and check_gowitness_available():
                print(
                    f"\n🔄 Using screenshot fallback for {len(failed_extractions)} pages without extractable images..."
                )

                # Find content root for serving
                html_parent = html_files[0].parent
                content_root = None
                for html_file in html_files:
                    if "OEBPS" in str(html_file):
                        content_root = html_file.parent
                        while content_root.name != "OEBPS" and content_root != extract_path:
                            content_root = content_root.parent
                        break
                if content_root is None:
                    parent = html_parent.parent
                    if (parent / "images").exists() or (parent / "styles").exists():
                        content_root = parent
                    else:
                        content_root = html_parent

                # Start server
                port = find_free_port()
                server = start_http_server(content_root, port)
                time.sleep(0.3)

                try:
                    for i, html_path, output_path in failed_extractions:
                        try:
                            url_path = str(html_path.relative_to(content_root))
                        except ValueError:
                            url_path = html_path.name
                        url = f"http://127.0.0.1:{port}/{url_path}"

                        # Fix 3: Disable fullpage if InDesign
                        if capture_with_gowitness(
                            url, output_path, vp_width, vp_height, timeout=15, delay=1, fullpage=not is_indesign
                        ):
                            success_count += 1
                            print(f"   ✅ {html_path.name} (screenshot)")
                        else:
                            print(f"   ❌ {html_path.name} (failed)")
                finally:
                    server.shutdown()

        else:
            # Screenshot mode with gowitness
            # Find the content root directory (OEBPS or epub root)
            # We need to serve from a directory that contains all resources (images, css, etc.)
            content_root = None
            html_parent = html_files[0].parent

            # Check if OEBPS structure
            for html_file in html_files:
                if "OEBPS" in str(html_file):
                    content_root = html_file.parent
                    while content_root.name != "OEBPS" and content_root != extract_path:
                        content_root = content_root.parent
                    break

            # If no OEBPS, find the common root that contains resources
            if content_root is None:
                # Check if resources are in parent directory (e.g., text/ with ../images/)
                parent = html_parent.parent
                if (parent / "images").exists() or (parent / "styles").exists():
                    content_root = parent
                else:
                    content_root = html_parent

            # Get the relative path from content_root to html files
            html_subdir = html_parent.relative_to(content_root) if html_parent != content_root else Path(".")

            # Prepare scaled HTML files if needed
            scaled_dir = None
            if scale_factor > 1.0:
                scaled_dir = temp_path / "scaled_html"
                scaled_dir.mkdir(parents=True, exist_ok=True)

                # Copy and scale CSS resource directories from content_root
                # Expanded list: added singular 'font' and 'image'
                for resource_dir in [
                    "css",
                    "styles",
                    "font",
                    "fonts",
                    "Fonts",
                    "CSS",
                    "Styles",
                    "image",
                    "images",
                    "Images",
                ]:
                    src = content_root / resource_dir
                    if src.exists():
                        dst = scaled_dir / resource_dir
                        dst.mkdir(parents=True, exist_ok=True)
                        for item in src.iterdir():
                            if item.is_file():
                                if item.suffix.lower() == ".css":
                                    # Scale CSS files (needed for InDesign layout positioning)
                                    scale_css_file(item, dst / item.name, scale_factor)
                                else:
                                    # Copy other files (fonts, etc.)
                                    shutil.copy(item, dst / item.name)
                            elif item.is_dir():
                                shutil.copytree(item, dst / item.name, dirs_exist_ok=True)

                # NEW: Also copy and scale CSS files in the same directory as HTML files
                # This handles flat EPUB structures where CSS is not in a subfolder
                for item in html_parent.iterdir():
                    if item.is_file() and item.suffix.lower() == ".css":
                        dst_path = scaled_dir / html_subdir / item.name
                        if not dst_path.exists():
                            scale_css_file(item, dst_path, scale_factor)

                # Link images directory (don't copy, too large)
                for img_dir in ["images", "Images", "image", "img"]:
                    images_src = content_root / img_dir
                    if images_src.exists():
                        images_dst = scaled_dir / img_dir
                        if not images_dst.exists():
                            images_dst.symlink_to(images_src)

                # Create the html subdirectory structure if needed
                if html_subdir != Path("."):
                    (scaled_dir / html_subdir).mkdir(parents=True, exist_ok=True)

            # Start HTTP server on the appropriate directory
            serve_dir = scaled_dir if scaled_dir else content_root
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
                        # Put scaled HTML in the correct subdirectory
                        if html_subdir != Path("."):
                            scaled_html = prepare_scaled_html(
                                html_path, scaled_dir / html_subdir, scale_factor, skip_css_scaling=is_indesign
                            )
                            url_path = str(html_subdir / scaled_html.name)
                        else:
                            scaled_html = prepare_scaled_html(
                                html_path, scaled_dir, scale_factor, skip_css_scaling=is_indesign
                            )
                            url_path = scaled_html.name
                    else:
                        try:
                            url_path = str(html_path.relative_to(content_root))
                        except ValueError:
                            url_path = html_path.name

                    url = f"http://127.0.0.1:{port}/{url_path}"
                    output_path = screenshots_dir / f"page_{i:04d}.png"
                    urls_with_paths.append((url, output_path))

                # Batch capture with gowitness
                print(f"📸 Capturing {total} pages in parallel (threads={threads})...")

                # Fix 3: Enable fullpage for InDesign too (prevents clipping)
                success_count = capture_batch_with_gowitness(
                    urls_with_paths,
                    render_width,
                    render_height,
                    timeout,
                    delay,
                    threads=threads,
                    fullpage=True if is_indesign else (epub_type == "screenshot"),
                )

                # Check for any missing pages and retry individually
                missing = [(url, path) for url, path in urls_with_paths if not path.exists()]
                if missing:
                    print(f"\n🔄 Retrying {len(missing)} failed pages individually...")
                    for url, output_path in missing:
                        # Fix 3: Enable fullpage for InDesign too
                        if capture_with_gowitness(
                            url,
                            output_path,
                            render_width,
                            render_height,
                            timeout,
                            delay,
                            fullpage=True if is_indesign else (epub_type == "screenshot"),
                        ):
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
    return screenshots_dir, epub_type, success_count, total, needs_autocrop


# =============================================================================
# CLI
# =============================================================================


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="Convert EPUB/PDF comic/manga files to CBR/CBZ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes (EPUB only):
  auto        Automatically detect best method (default)
  extract     Force direct image extraction
  screenshot  Force screenshot with gowitness

Examples:
  %(prog)s manga.epub                    # Auto-detect mode
  %(prog)s manga.epub --mode extract     # Force extraction
  %(prog)s manga.epub --mode screenshot  # Force screenshot
  %(prog)s manga.epub --cbr-only         # Create CBR, delete images
  %(prog)s comic.pdf --cbr-only          # Convert PDF to CBR
        """,
    )

    parser.add_argument("input", type=Path, nargs="?", help="EPUB or PDF file to convert")
    parser.add_argument("-o", "--output", type=Path, help="Output directory")
    parser.add_argument(
        "-m", "--mode", choices=["auto", "extract", "screenshot"], default="auto", help="Conversion mode (EPUB only)"
    )
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per page (screenshot mode)")
    parser.add_argument("--delay", type=int, default=2, help="Delay before screenshot")
    parser.add_argument("--dpi", type=int, default=0, help="DPI for PDF rendering (0 = extract original images)")
    parser.add_argument("--normalize", action="store_true", help="Normalize page sizes for PDF conversion")
    parser.add_argument("--keep-extracted", action="store_true", help="Keep extracted EPUB files")
    parser.add_argument("--cbr", action="store_true", help="Create CBR file")
    parser.add_argument("--cbr-only", action="store_true", help="Create CBR and delete images")
    parser.add_argument("--jpeg-quality", type=int, default=92, help="JPEG quality for CBR (1-100, default: 92)")
    parser.add_argument("--no-jpeg", action="store_true", help="Keep PNG format, don't convert to JPEG")
    parser.add_argument(
        "--autocrop", action="store_true", help="Enable auto-cropping of white borders before JPEG conversion"
    )
    parser.add_argument(
        "--threads", type=int, default=4, help="Number of parallel threads for screenshot mode (default: 4)"
    )
    parser.add_argument(
        "--indesign", action="store_true", help="Force InDesign layout mode (disables CSS pixel scaling)"
    )
    parser.add_argument(
        "--viewport", type=str, default="1200x1600", help="Viewport size for screenshot mode (default: 1200x1600)"
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
        if check_pdf_available():
            print("✅ PyMuPDF available for PDF conversion")
        else:
            print("⚠️  PyMuPDF not available (install with: pip install PyMuPDF)")
        sys.exit(0)

    # Validate input file is provided
    if not args.input:
        parser.error("the following arguments are required: input")

    start_time = time.time()
    input_file = args.input.resolve()
    file_ext = input_file.suffix.lower()

    try:
        # Determine file type and process accordingly
        if file_ext == ".pdf":
            # PDF conversion
            if not check_pdf_available():
                print("❌ Error: PyMuPDF not installed")
                print("   Install with: pip install PyMuPDF")
                sys.exit(1)

            screenshots_dir, success_count, total_pages = convert_pdf_to_images(
                pdf_path=input_file,
                output_dir=args.output or input_file.parent / f"{input_file.stem}_images",
                dpi=args.dpi,
                jpeg_quality=args.jpeg_quality,
                normalize=args.normalize,
            )
            file_type = "pdf"
            needs_autocrop = False

        elif file_ext == ".epub":
            # EPUB conversion
            screenshots_dir, file_type, success_count, total_pages, needs_autocrop = convert_epub(
                epub_path=input_file,
                output_dir=args.output,
                mode=args.mode,
                timeout=args.timeout,
                delay=args.delay,
                keep_extracted=args.keep_extracted,
                threads=args.threads,
                force_indesign=args.indesign,
                viewport_arg=args.viewport,
            )
        else:
            print(f"❌ Error: Unsupported file format: {file_ext}")
            print("   Supported formats: .epub, .pdf")
            sys.exit(1)

        # Determine if conversion is complete or incomplete
        is_complete = success_count == total_pages

        # Create CBR if requested
        if args.cbr or args.cbr_only:
            # Create 'converted' directory next to the input file (use absolute path)
            converted_dir = input_file.parent / "converted"
            converted_dir.mkdir(parents=True, exist_ok=True)

            # Incomplete conversions go to 'review' subfolder
            if not is_complete:
                output_subdir = converted_dir / "review"
                output_subdir.mkdir(parents=True, exist_ok=True)
                print(f"⚠️  Incomplete conversion ({success_count}/{total_pages}) → review folder")
            else:
                output_subdir = converted_dir

            cbr_path = output_subdir / f"{input_file.stem}.cbr"
            jpeg_quality = 0 if args.no_jpeg else args.jpeg_quality
            # Auto-crop is now opt-in via --autocrop
            autocrop_flag = args.autocrop
            if create_cbr(screenshots_dir, cbr_path, jpeg_quality, file_type, autocrop=autocrop_flag):
                # Delete the entire _images directory after creating CBR
                if args.cbr_only:
                    images_dir = screenshots_dir.parent
                    if images_dir.exists():
                        shutil.rmtree(images_dir)
                        print("🗑️  Deleted working directory")

            # Log incomplete conversions to error log
            if not is_complete:
                log_file = converted_dir / "conversion_errors.log"
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                log_entry = (
                    f"[{timestamp}] {input_file.name}: "
                    f"{success_count}/{total_pages} pages converted "
                    f"({total_pages - success_count} missing)\n"
                )
                with open(log_file, "a") as f:
                    f.write(log_entry)
                print(f"📝 Error logged to: {log_file}")

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
