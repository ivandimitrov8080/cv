"""itempipe — N stateless workers against one SQLite database.

TODO: rename.  The domain is an implementation detail: the unit of the pipeline
is an `item`, the operations are create/claim/advance/pause/release/archive/
lookup/send/list, and state values live in `config.state_vocab` as data.

Domain wording survives only in `prompts/*.md` and config values.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
