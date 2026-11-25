"""OGC Building Blocks transforms: ``topo2jsonfg`` and ``topo2geojson-faces``.

The same plugin convention as ``topo2geojson.Topo2GeoJsonTransform``: a host discovers
``transform_types``, ``default_inputs`` and ``default_outputs`` on each class and calls
``transform(metadata)``, where ``metadata.input_data`` is the Topo Feature document
(a JSON string or a file object) and ``metadata.metadata`` is the transform's
``metadata`` from ``transforms.yaml``.
Each returns the encoding as a JSON string; a document that cannot be encoded raises
:class:`~topo_encodings.diagnostics.EncodingError`, listing every problem.

``topo2jsonfg`` metadata:

- ``crs`` (required): the ``place`` CRS URI, or a list of URIs for a compound CRS;
- ``place_axes`` (required): the ``place`` CRS's axis order, such as
  ``east-north-up``, for the orientation check;
- ``features``: ``per-solid`` (default) or ``multi``;
- ``name``: the collection's name (default: the document's ``surveyTitle`` or
  ``name``).

``topo2geojson-faces`` metadata:

- ``place_axes`` (required): as above;
- ``heights`` (required): ``ellipsoidal-geometry``, stating that the document's
  ``geometry`` heights are ellipsoidal; a register has no geoid grid, so a transformer
  from ``place`` can only be supplied by calling the library directly;
- ``name``: as above.

The transforms always check orientation; only the library and the command line can
turn the check off.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .geojson_faces import to_geojson_faces
from .jsonfg_write import to_jsonfg


def _document(input_data: Any) -> Any:
    if isinstance(input_data, (str, bytes, bytearray)):
        return json.loads(input_data)
    if hasattr(input_data, "read"):
        return json.load(input_data)
    return input_data  # already parsed


def _settings(metadata: Any) -> Mapping[str, Any]:
    return getattr(metadata, "metadata", None) or {}


def _required(settings: Mapping[str, Any], key: str, transform: str) -> Any:
    if settings.get(key) in (None, "", []):
        raise ValueError(f"{transform}: the transform's metadata needs {key!r}")
    return settings[key]


def dumps(encoding: Mapping[str, Any]) -> str:
    """The encoding as written by the transforms and the command line."""
    return json.dumps(encoding, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


class Topo2JsonFgTransform:
    transform_types = ["topo2jsonfg"]
    default_inputs = ["application/json"]
    default_outputs = ["application/geo+json"]

    def transform(self, metadata: Any) -> str:
        settings = _settings(metadata)
        return dumps(
            to_jsonfg(
                _document(metadata.input_data),
                crs=_required(settings, "crs", "topo2jsonfg"),
                place_axes=_required(settings, "place_axes", "topo2jsonfg"),
                features=settings.get("features", "per-solid"),
                name=settings.get("name"),
            )
        )


class Topo2GeoJsonFacesTransform:
    transform_types = ["topo2geojson-faces"]
    default_inputs = ["application/json"]
    default_outputs = ["application/geo+json"]

    def transform(self, metadata: Any) -> str:
        settings = _settings(metadata)
        name = "topo2geojson-faces"
        return dumps(
            to_geojson_faces(
                _document(metadata.input_data),
                place_axes=_required(settings, "place_axes", name),
                heights=_required(settings, "heights", name),
                name=settings.get("name"),
            )
        )
