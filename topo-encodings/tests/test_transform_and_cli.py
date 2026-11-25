import io
import json
from types import SimpleNamespace

import pytest
from builders import cube_doc

from topo_encodings import EncodingError, to_geojson_faces, to_jsonfg
from topo_encodings.cli import topo2geojson_faces, topo2jsonfg
from topo_encodings.transform import Topo2GeoJsonFacesTransform, Topo2JsonFgTransform

CRS = "http://www.opengis.net/def/crs/EPSG/0/7850"
ENU = "east-north-up"


def _with_geometry(doc):
    """Stand-in ellipsoidal geometry for every point."""
    for point in doc["points"][0]["features"]:
        e, n, h = point["place"]["coordinates"]
        point["geometry"] = {
            "type": "Point",
            "coordinates": [116.0 + e * 1e-5, -32.0 + n * 1e-5, h - 31.25],
        }
    return doc


def _host(input_data, **metadata):
    """What a Building Blocks host passes to transform()."""
    return SimpleNamespace(input_data=input_data, metadata=metadata)


# Transforms -------------------------------------------------------------------------


def test_transform_classes_declare_the_plugin_convention():
    assert Topo2JsonFgTransform.transform_types == ["topo2jsonfg"]
    assert Topo2GeoJsonFacesTransform.transform_types == ["topo2geojson-faces"]
    for cls in (Topo2JsonFgTransform, Topo2GeoJsonFacesTransform):
        assert cls.default_inputs == ["application/json"]
        assert cls.default_outputs == ["application/geo+json"]


@pytest.mark.parametrize("as_", [json.dumps, lambda d: io.StringIO(json.dumps(d))])
def test_topo2jsonfg_transform(as_):
    out = Topo2JsonFgTransform().transform(
        _host(
            as_(cube_doc()),
            crs=[CRS, "http://www.opengis.net/def/crs/EPSG/0/5711"],
            place_axes=ENU,
        )
    )
    assert isinstance(out, str)
    assert json.loads(out) == to_jsonfg(
        cube_doc(),
        place_axes=ENU,
        crs=[CRS, "http://www.opengis.net/def/crs/EPSG/0/5711"],
    )


def test_topo2jsonfg_transform_multi_and_name():
    out = json.loads(
        Topo2JsonFgTransform().transform(
            _host(
                json.dumps(cube_doc()),
                crs=CRS,
                place_axes=ENU,
                features="multi",
                name="N",
            )
        )
    )
    assert out["name"] == "N"
    assert out["features"][0]["place"]["type"] == "MultiPolyhedron"


@pytest.mark.parametrize(
    "metadata, missing",
    [({"place_axes": ENU}, "'crs'"), ({"crs": CRS}, "'place_axes'")],
)
def test_topo2jsonfg_transform_needs_crs_and_axes(metadata, missing):
    with pytest.raises(ValueError, match=missing):
        Topo2JsonFgTransform().transform(_host(json.dumps(cube_doc()), **metadata))


def test_topo2geojson_faces_transform():
    doc = _with_geometry(cube_doc())
    out = Topo2GeoJsonFacesTransform().transform(
        _host(
            json.dumps(doc), place_axes="east-north-up", heights="ellipsoidal-geometry"
        )
    )
    assert json.loads(out) == to_geojson_faces(
        doc, place_axes="east-north-up", heights="ellipsoidal-geometry"
    )


@pytest.mark.parametrize(
    "metadata",
    [{"heights": "ellipsoidal-geometry"}, {"place_axes": "east-north-up"}],
)
def test_topo2geojson_faces_transform_needs_axes_and_heights(metadata):
    with pytest.raises(ValueError):
        Topo2GeoJsonFacesTransform().transform(
            _host(json.dumps(_with_geometry(cube_doc())), **metadata)
        )


def test_a_transform_reports_every_problem():
    doc = cube_doc()
    doc["solids"] = []
    with pytest.raises(EncodingError) as err:
        Topo2JsonFgTransform().transform(
            _host(json.dumps(doc), crs=CRS, place_axes=ENU)
        )
    assert err.value.codes == {"NO_SOLIDS"}


# Command line -----------------------------------------------------------------------


def _write(tmp_path, doc, name="in.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_topo2jsonfg_cli_writes_a_file(tmp_path):
    out = tmp_path / "out.jsonfg.json"
    code = topo2jsonfg(
        [
            _write(tmp_path, cube_doc()),
            "--crs",
            CRS,
            "--crs",
            "http://www.opengis.net/def/crs/EPSG/0/5711",
            "--place-axes",
            ENU,
            "-o",
            str(out),
        ]
    )
    assert code == 0
    data = out.read_bytes()
    assert b"\r\n" not in data and data.endswith(b"}\n")
    assert json.loads(data)["coordRefSys"] == [
        CRS,
        "http://www.opengis.net/def/crs/EPSG/0/5711",
    ]


def test_topo2jsonfg_cli_stdin_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(cube_doc())))
    assert topo2jsonfg(["-", "--crs", CRS, "--place-axes", ENU, "--name", "Cube"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "Cube" and out["coordRefSys"] == CRS


def test_topo2geojson_faces_cli(tmp_path, capsys):
    path = _write(tmp_path, _with_geometry(cube_doc()))
    args = [path, "--place-axes", "east-north-up", "--heights", "ellipsoidal-geometry"]
    assert topo2geojson_faces(args) == 0
    assert len(json.loads(capsys.readouterr().out)["features"]) == 6


def test_cli_reports_problems_and_exits_1(tmp_path, capsys):
    doc = cube_doc()
    doc["solids"] = []
    assert topo2jsonfg([_write(tmp_path, doc), "--crs", CRS, "--place-axes", ENU]) == 1
    err = capsys.readouterr().err
    assert "NO_SOLIDS" in err


def test_cli_reports_unreadable_input(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert topo2jsonfg([str(bad), "--crs", CRS, "--place-axes", ENU]) == 1
    assert (
        topo2jsonfg([str(tmp_path / "missing.json"), "--crs", CRS, "--place-axes", ENU])
        == 1
    )
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["in.json", "--place-axes", ENU],  # no --crs
        ["in.json", "--crs", CRS],  # no --place-axes
        ["in.json", "--crs", CRS, "--place-axes", ENU, "--features", "per-face"],
    ],
)
def test_topo2jsonfg_cli_usage_errors_exit_2(args):
    with pytest.raises(SystemExit) as exc:
        topo2jsonfg(args)
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["in.json", "--heights", "ellipsoidal-geometry"],
        ["in.json", "--place-axes", "east-north-up"],
        [
            "in.json",
            "--place-axes",
            "up-east-north",
            "--heights",
            "ellipsoidal-geometry",
        ],
        ["in.json", "--place-axes", "east-north-up", "--heights", "ahd"],
    ],
)
def test_topo2geojson_faces_cli_usage_errors_exit_2(args):
    with pytest.raises(SystemExit) as exc:
        topo2geojson_faces(args)
    assert exc.value.code == 2
