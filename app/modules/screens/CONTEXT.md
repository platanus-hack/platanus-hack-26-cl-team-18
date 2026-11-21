# Screens

A Screen is a registered physical display. This context owns its identity,
presence, supported layouts and speech-mode setting. The WebSocket adapter
will resolve a screen's wire identifier to the domain's `ScreenId`. The
auction reads supported layouts, and audience ingest checks speech mode before
accepting an utterance for topic extraction.
