"""ValleyMind Music Engine.

A provider-abstraction layer over the real Music workstation work: AI music
generation (create / remix / extend / reference), Vocal Lab, Stem Lab,
AI mastering, Lyrics Lab and the Elena Music Co-Pilot.

The engine never fakes results. Creative packages (lyrics, arrangement,
production plans) are produced by the LLM layer and are real output. Anything
that requires a *server-side audio renderer* (full mixes, stems, mastered
files, AI vocals) is exposed behind a provider; when no provider is configured
the engine says exactly that and returns a structured, honest "provider
unavailable" state instead of pretending.

  * core.music.african   — African music intelligence (genres, patterns, keys)
  * core.music.providers — MusicProvider abstraction (local + stub providers)
  * core.music.engine    — LLM producers + facade over the active provider
  * core.music.jobs      — reliable async job state machine for heavy work
  * core.music.storage   — per-user audio file storage
  * core.music.projects  — project + version persistence
  * core.music.routes    — Flask endpoint registration helper
"""

# Imported lazily by the modules that use them (each submodule imports exactly
# what it needs), so partially-created packages never crash on import.
from . import african  # noqa: F401


def register_routes(app, require_login, **kwargs):
    """Register all /api/music workstation endpoints onto the Flask app.

    ``require_login`` is the app's own session gate (app._require_login) so the
    music blueprint shares the exact same authentication/verification rules as
    every other protected endpoint instead of re-implementing them. Optional
    ``safe_user_id`` / ``projects_dir`` let the host reuse its own user-id
    hasher and project directory, keeping one project store.
    """
    from . import routes

    routes.init_routes(app, require_login, **kwargs)