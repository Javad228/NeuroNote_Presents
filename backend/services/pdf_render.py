from pathlib import Path


def render_pdf_to_images(pdf_path: Path, images_dir: Path, dpi: int) -> int:
    """Render all PDF pages to PNG images in page_XXX format."""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        raise RuntimeError(
            "PyMuPDF is required for PDF rendering. Install with `pip install pymupdf`."
        ) from exc

    images_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    page_count = doc.page_count
    if page_count <= 0:
        doc.close()
        raise RuntimeError("Uploaded PDF has zero pages.")

    for i in range(page_count):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        out_path = images_dir / f"page_{i + 1:03d}.png"
        pix.save(str(out_path))

    doc.close()
    return page_count
