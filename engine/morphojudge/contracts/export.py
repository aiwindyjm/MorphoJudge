"""JSON Schema export for packages/contracts/schema.json.

Run inside the daemon container:

    docker compose run --rm --no-deps daemon \
        python -m morphojudge.contracts.export > packages/contracts/schema.json

Output is canonical (sorted keys, indent 2) so a pytest drift test can compare
byte-for-byte with the committed artifact.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import TypeAdapter

from .domain import CONTRACT_MODELS, SCHEMA_VERSION, ContractModel
from .errors import ErrorCode, ErrorObject, ErrorResponse


def _model_names() -> list[str]:
    names = [m.__name__ for m in CONTRACT_MODELS] + [ErrorResponse.__name__, ErrorObject.__name__, ErrorCode.__name__]
    return sorted(set(names))


def _hoist_inner_defs(definitions: dict[str, Any]) -> None:
    """Lift every nested ``$defs`` entry into the root definitions.

    ref_template points all refs at ``#/definitions/<name>``; keeping enums
    nested would produce unresolvable local refs (audit B02-R2-02).
    Same-name entries must be byte-identical or the generator is broken.
    """

    hoisted: dict[str, Any] = {}
    for name in list(definitions):
        schema = definitions[name]
        inner = schema.pop("$defs", None)
        if not inner:
            continue
        for inner_name, inner_schema in inner.items():
            previous = hoisted.get(inner_name)
            if previous is not None and previous != inner_schema:
                raise RuntimeError(
                    f"conflicting schema definitions for {inner_name}: "
                    "contract models disagree on a shared type"
                )
            hoisted[inner_name] = inner_schema
    for name, schema in hoisted.items():
        existing = definitions.get(name)
        if existing is not None and existing != schema:
            raise RuntimeError(
                f"conflicting schema definitions for {name}: "
                "hoisted $defs clashes with a top-level definition"
            )
        definitions[name] = schema


def generate_schema() -> Dict[str, Any]:
    definitions: Dict[str, Any] = {}

    for model in CONTRACT_MODELS:
        if isinstance(model, type) and issubclass(model, ContractModel):
            definitions[model.__name__] = model.model_json_schema(
                ref_template="#/definitions/{model}"
            )

    for model in (ErrorResponse, ErrorObject):
        definitions[model.__name__] = model.model_json_schema(
            ref_template="#/definitions/{model}"
        )

    definitions[ErrorCode.__name__] = TypeAdapter(ErrorCode).json_schema()

    _hoist_inner_defs(definitions)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "MorphoJudge core contracts",
        "schema_version": SCHEMA_VERSION,
        "models": _model_names(),
        "definitions": {name: definitions[name] for name in sorted(definitions)},
    }


def render_schema() -> str:
    return json.dumps(generate_schema(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> None:
    print(render_schema(), end="")


if __name__ == "__main__":
    main()
