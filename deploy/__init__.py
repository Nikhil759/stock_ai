"""Wolf Capital deploy orchestration."""

__all__ = [
    "build_deploy_screen_response",
    "deploy_new_wolf",
    "guardrails_from_deploy_request",
    "resolve_deploy_user_id",
    "supabase_deploy_enabled",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from deploy import deploy_wolf

    return getattr(deploy_wolf, name)
