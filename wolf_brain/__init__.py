"""Wolf Brain — LLM judgment for deploy and daily review."""

__all__ = ["run_wolf_brain"]


def __getattr__(name: str):
    if name == "run_wolf_brain":
        from wolf_brain.brain import run_wolf_brain as fn

        return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
