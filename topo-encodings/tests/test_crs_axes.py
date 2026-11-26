"""The place CRS's axis order: taken from the CRS with pyproj (the ``crs`` extra), or
stated, and checked against the CRS when both are known.

Tests marked ``needs_pyproj`` run only with the extra installed; the others hide
pyproj, so they run the same either way.
"""

import importlib.util
import json
import sys
from types import SimpleNamespace

import pytest
from builders import cube_doc, cube_with_void_doc

from topo_encodings import EncodingError, to_geojson_faces, to_jsonfg
from topo_encodings.cli import topo2geojson_faces, topo2jsonfg
from topo_encodings.orientation import crs_axes, crs_uris, resolve_axes
from topo_encodings.transform import Topo2GeoJsonFacesTransform, Topo2JsonFgTransform

EPSG = "http://www.opengis.net/def/crs/EPSG/0/"
MGA50 = EPSG + "7850"  # GDA2020 / MGA zone 50: east, north
NZTM = EPSG + "2193"  # NZGD2000 / NZTM2000: north, east
NZVD = EPSG + "4440"  # NZVD2009 height: up
MSL_DEPTH = EPSG + "5715"  # depth: down
GEOCENTRIC = EPSG + "4978"  # WGS 84 geocentric: X, Y, Z
WGS84_3D = EPSG + "4979"  # latitude, longitude, ellipsoidal height
LOCAL = "http://example.org/crs/site-grid"  # no authority knows it
ENU = "east-north-up"
NEU = "north-east-up"

needs_pyproj = pytest.mark.skipif(
    importlib.util.find_spec("pyproj") is None,
    reason='needs pyproj: pip install "topo-encodings[crs]"',
)


@pytest.fixture
def no_pyproj(monkeypatch):
    """Make ``import pyproj`` fail, as without the extra."""
    monkeypatch.setitem(sys.modules, "pyproj", None)


def _north_east(doc):
    for collection in doc["points"]:
        for point in collection["features"]:
            e, n, *rest = point["place"]["coordinates"]
            point["place"]["coordinates"] = [n, e, *rest]
    return doc


def _transformer(e, n, h):
    return (116.0 + e * 1e-5, -32.0 + n * 1e-5, h)


def _write(tmp_path, doc):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


# The CRS as URIs ---------------------------------------------------------------------


@pytest.mark.parametrize("crs", ["", [], [""], [MGA50, 5711]])
def test_a_crs_must_be_uris(crs):
    with pytest.raises(ValueError):
        crs_uris(crs)


def test_the_geojson_writer_checks_a_crs_even_with_a_stated_order():
    with pytest.raises(ValueError, match="empty"):
        to_geojson_faces(cube_doc(), crs="", place_axes=ENU, transformer=_transformer)


# Without pyproj ----------------------------------------------------------------------


def test_without_pyproj_the_crs_cannot_be_looked_up(no_pyproj):
    with pytest.raises(ImportError):
        crs_axes(MGA50)


def test_without_pyproj_a_crs_alone_says_what_to_install(no_pyproj):
    with pytest.raises(ValueError, match=r"topo-encodings\[crs\]"):
        resolve_axes(crs=MGA50)


def test_without_pyproj_the_stated_order_is_used_unchecked(no_pyproj):
    to_enu = resolve_axes(NEU, crs=MGA50)
    assert to_enu([1.0, 2.0, 3.0]) == [2.0, 1.0, 3.0]


def test_neither_crs_nor_order_is_refused():
    with pytest.raises(ValueError, match="give crs or place_axes"):
        resolve_axes()


def test_without_pyproj_the_cli_needs_the_order(no_pyproj, capsys):
    with pytest.raises(SystemExit) as exc:
        topo2jsonfg(["in.json", "--crs", MGA50])
    assert exc.value.code == 2
    assert "topo-encodings[crs]" in capsys.readouterr().err


def test_without_pyproj_the_transform_needs_the_order(no_pyproj):
    host = SimpleNamespace(input_data=json.dumps(cube_doc()), metadata={"crs": MGA50})
    with pytest.raises(ValueError, match=r"topo-encodings\[crs\]"):
        Topo2JsonFgTransform().transform(host)


# With pyproj -------------------------------------------------------------------------


@needs_pyproj
@pytest.mark.parametrize(
    "crs, expected",
    [
        (MGA50, ENU),
        (NZTM, NEU),
        ([NZTM, NZVD], NEU),
        (EPSG + "2105", NEU),  # NZGD2000 / Mount Eden 2000, a meridional circuit
        (WGS84_3D, NEU),  # latitude first
    ],
)
def test_the_order_is_the_crs_authority_order(crs, expected):
    assert crs_axes(crs) == expected


@needs_pyproj
@pytest.mark.parametrize(
    "crs",
    [[MGA50, MSL_DEPTH], GEOCENTRIC, [NZVD, NZTM]],
    ids=["depth", "xyz", "z-first"],
)
def test_a_crs_without_horizontal_axes_then_up_is_refused(crs):
    with pytest.raises(ValueError, match="two horizontal axes"):
        crs_axes(crs)


@needs_pyproj
def test_an_unknown_crs_is_refused_without_a_stated_order():
    with pytest.raises(ValueError, match="cannot look up"):
        resolve_axes(crs=LOCAL)


@needs_pyproj
def test_an_unknown_crs_uses_the_stated_order():
    assert resolve_axes(NEU, crs=LOCAL)([1.0, 2.0, 3.0]) == [2.0, 1.0, 3.0]


@needs_pyproj
def test_a_stated_order_that_differs_from_the_crs_is_refused():
    with pytest.raises(ValueError, match="differs from the axis order"):
        resolve_axes(ENU, crs=NZTM)
    assert resolve_axes(NEU, crs=NZTM)([1.0, 2.0, 3.0]) == [2.0, 1.0, 3.0]


@needs_pyproj
def test_jsonfg_takes_the_order_from_the_crs():
    nz = to_jsonfg(_north_east(cube_with_void_doc()), crs=NZTM)
    assert nz == to_jsonfg(_north_east(cube_with_void_doc()), crs=NZTM, place_axes=NEU)


@needs_pyproj
def test_north_east_coordinates_under_an_east_north_crs_are_suspected():
    with pytest.raises(EncodingError) as err:
        to_jsonfg(_north_east(cube_with_void_doc()), crs=MGA50)
    assert err.value.codes == {"SHELL_INSIDE_OUT", "AXIS_ORDER_SUSPECTED"}


@needs_pyproj
def test_geojson_faces_take_the_order_from_the_crs():
    out = to_geojson_faces(
        _north_east(cube_doc()),
        crs=[NZTM, NZVD],
        transformer=lambda n, e, h: _transformer(e, n, h),
    )
    assert out == to_geojson_faces(cube_doc(), place_axes=ENU, transformer=_transformer)


@needs_pyproj
def test_the_cli_takes_the_order_from_the_crs(tmp_path, capsys):
    path = _write(tmp_path, _north_east(cube_doc()))
    assert topo2jsonfg([path, "--crs", NZTM]) == 0
    assert json.loads(capsys.readouterr().out)["coordRefSys"] == NZTM
    with pytest.raises(SystemExit) as exc:
        topo2jsonfg([path, "--crs", NZTM, "--place-axes", ENU])
    assert exc.value.code == 2
    assert "differs" in capsys.readouterr().err


@needs_pyproj
def test_the_faces_cli_takes_the_order_from_the_crs(tmp_path):
    doc = _north_east(cube_doc())
    for point in doc["points"][0]["features"]:
        n, e, h = point["place"]["coordinates"]
        point["geometry"] = {"type": "Point", "coordinates": _transformer(e, n, h)}
    path = _write(tmp_path, doc)
    args = [path, "--heights", "ellipsoidal-geometry", "-o", str(tmp_path / "o")]
    assert topo2geojson_faces([*args, "--crs", NZTM]) == 0


@needs_pyproj
def test_the_transforms_take_the_order_from_the_crs():
    doc = _north_east(cube_doc())
    host = SimpleNamespace(input_data=json.dumps(doc), metadata={"crs": NZTM})
    assert json.loads(Topo2JsonFgTransform().transform(host))["coordRefSys"] == NZTM
    for point in doc["points"][0]["features"]:
        point["geometry"] = {"type": "Point", "coordinates": [174.0, -41.0, 10.0]}
    host = SimpleNamespace(
        input_data=json.dumps(doc),
        metadata={"crs": NZTM, "heights": "ellipsoidal-geometry"},
    )
    assert json.loads(Topo2GeoJsonFacesTransform().transform(host))["features"]
