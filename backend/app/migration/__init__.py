"""Data migrations / back-fills for the strangler cutover.

These are *data* transforms (not Alembic schema migrations): they read the live
legacy tables and populate the new immutable content model
(``app/models/content_model.py``) without changing any read path.
"""
