"""Video generation provider interface and lifecycle.

This package contains the abstract provider interface and concrete implementations.
Providers are isolated behind this interface — the rest of the application
never communicates directly with any video API.

Adding a new provider:
    1. Create core/video_providers/<provider_name>.py
    2. Subclass BaseVideoProvider
    3. Implement submit() and poll()
    4. Register in core/video_dispatcher.py
"""

from core.video_providers.base import (
    BaseVideoProvider,
    VideoTask,
    VideoTaskStatus,
)

__all__ = ["BaseVideoProvider", "VideoTask", "VideoTaskStatus"]
