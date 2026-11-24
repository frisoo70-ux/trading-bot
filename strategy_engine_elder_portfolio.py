"""Compatibility import for the video-based Elder PAPER portfolio."""
from strategy_engine_elder_video import ElderVideoPaperEngine


class ElderPaperEngine(ElderVideoPaperEngine):
    """Dashboard/backward-compatible engine name."""
    pass
