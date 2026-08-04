import importlib.util
from pathlib import Path

import fitz


def _load_reconstructor():
    script = Path(__file__).parents[1] / "scripts" / "legacy" / "test_reconstruct_pdf.py"
    spec = importlib.util.spec_from_file_location("reconstruct_pdf", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flatten_document_replaces_font_resources_with_a_page_image():
    reconstructor = _load_reconstructor()
    source = fitz.open()
    page = source.new_page(width=200, height=100)
    page.insert_text((20, 40), "font compatibility test")

    flattened = reconstructor._flatten_document(source, dpi=72)
    try:
        output_page = flattened[0]
        assert flattened.page_count == 1
        assert output_page.rect.width == page.rect.width
        assert output_page.rect.height == page.rect.height
        assert output_page.get_images(full=True)
        assert not output_page.get_fonts(full=True)
    finally:
        flattened.close()
        source.close()
