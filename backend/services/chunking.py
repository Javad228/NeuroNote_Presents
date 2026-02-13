import sys
from pathlib import Path
from typing import Any, Optional

from .chunking_ocr_bridge import install_chunking_ocr_override


class ChunkingService:
    def __init__(
        self,
        *,
        chunking_root: Path,
        neuronote_pipeline_root: Path,
        azure_ocr_level: str,
        azure_ocr_min_conf: float,
    ):
        self.chunking_root = chunking_root
        self.neuronote_pipeline_root = neuronote_pipeline_root
        self.azure_ocr_level = azure_ocr_level
        self.azure_ocr_min_conf = azure_ocr_min_conf

    def _load_chunking_symbols(self):
        """Load chunking modules from the external chunking repository."""
        if not self.chunking_root.exists():
            raise RuntimeError(f"CHUNKING_ROOT does not exist: {self.chunking_root}")

        root_str = str(self.chunking_root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        try:
            from src.chunking.base import chunks_to_json  # type: ignore
            from src.chunking.changepoint import changepoint_similarity_chunking  # type: ignore
            from src.pipeline import SlideChunkingPipeline  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Failed to import chunking modules. Ensure chunking dependencies are installed "
                f"and CHUNKING_ROOT is correct. Root={self.chunking_root}"
            ) from exc

        return SlideChunkingPipeline, changepoint_similarity_chunking, chunks_to_json

    async def run_changepoint_chunking(
        self,
        *,
        slide_dir: Path,
        method: str,
        penalty: Optional[float],
        n_bkps: Optional[int],
        min_chunk: int,
        use_embeddings: bool,
        use_cache: bool,
    ) -> dict[str, Any]:
        """Run OCR+embedding extraction, then changepoint chunking."""
        (
            SlideChunkingPipeline,
            changepoint_similarity_chunking,
            chunks_to_json,
        ) = self._load_chunking_symbols()

        install_chunking_ocr_override(
            chunking_root=self.chunking_root,
            neuronote_pipeline_root=self.neuronote_pipeline_root,
            azure_level=self.azure_ocr_level,
            azure_min_conf=self.azure_ocr_min_conf,
        )

        pipeline = SlideChunkingPipeline(
            slide_dir=slide_dir,
            use_vision=False,
            use_cache=use_cache,
        )

        await pipeline.process_all_slides(show_progress=False)
        if not pipeline.slide_contents:
            raise RuntimeError(f"No slide images found under: {slide_dir / 'images'}")

        pipeline.compute_embeddings(show_progress=False)

        embeddings = [slide.embedding for slide in pipeline.slide_contents]
        chunks = changepoint_similarity_chunking(
            embeddings=embeddings,
            method=method,
            penalty=penalty,
            n_bkps=n_bkps,
            min_chunk=min_chunk,
            use_embeddings=use_embeddings,
        )

        result = {
            "num_slides": len(pipeline.slide_contents),
            "chunks": chunks_to_json(chunks),
        }

        pipeline_results = pipeline.get_results()
        if "similarities" in pipeline_results:
            result["adjacent_similarities"] = pipeline_results["similarities"]

        return result

    @staticmethod
    def _parse_slide_range(slide_range: Any) -> tuple[int, int]:
        """
        Normalize chunk slide range to (start, end).

        Supported formats:
        - [1, 3]
        - (1, 3)
        - "1-3"
        - "1 - 3"
        """
        if isinstance(slide_range, (list, tuple)) and len(slide_range) == 2:
            start = int(slide_range[0])
            end = int(slide_range[1])
            return start, end

        if isinstance(slide_range, str):
            normalized = slide_range.replace(" ", "")
            if "-" in normalized:
                left, right = normalized.split("-", 1)
                if left.isdigit() and right.isdigit():
                    return int(left), int(right)

        raise RuntimeError(f"Invalid slide_range: {slide_range}")

    @staticmethod
    def collect_chunk_images(images_dir: Path, slide_range: Any) -> list[Path]:
        start, end = ChunkingService._parse_slide_range(slide_range)
        if start > end:
            raise RuntimeError(f"Invalid slide range [{start}, {end}]")

        chunk_images: list[Path] = []
        for slide_num in range(start, end + 1):
            img_path = images_dir / f"page_{slide_num:03d}.png"
            if not img_path.exists():
                raise RuntimeError(f"Missing slide image for chunk: {img_path}")
            chunk_images.append(img_path)

        return chunk_images
