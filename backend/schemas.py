from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProcessPdfOptions:
    method: str
    penalty: Optional[float]
    n_bkps: Optional[int]
    min_chunk: int
    use_embeddings: bool
    use_cache: bool
    skip_generation: bool
    previous_context: Optional[str]
    render_dpi: int
