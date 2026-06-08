"""Unit tests for fingerprint database and probes."""

import tempfile
from pathlib import Path

import yaml


def test_fingerprint_db_load():
    from fy_image_canary.probes.fingerprint import FingerprintDB

    data = {
        "models": {
            "test-model": {
                "speed_p50_range_sec": [3.0, 10.0],
                "supported_sizes": ["1024x1024"],
                "unsupported_params": ["style"],
                "unsupported_sizes": ["512x512"],
                "error_patterns": ["invalid"],
                "has_revised_prompt": True,
                "has_c2pa": False,
                "response_format_variants": ["url", "b64_json"],
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        db = FingerprintDB.load(f.name)

    assert "test-model" in db.models
    fp = db.models["test-model"]
    assert fp.speed_p50_range_sec == (3.0, 10.0)
    assert fp.unsupported_params == ["style"]
    assert fp.has_revised_prompt is True


def test_fingerprint_db_load_empty():
    from fy_image_canary.probes.fingerprint import FingerprintDB
    db = FingerprintDB.load("")
    assert db.models == {}


def test_fingerprint_db_load_nonexistent():
    from fy_image_canary.probes.fingerprint import FingerprintDB
    db = FingerprintDB.load("/nonexistent/path.yaml")
    assert db.models == {}


def test_bundled_fingerprints_load():
    """Ensure the bundled fingerprints.yaml is parseable."""
    from fy_image_canary.probes.fingerprint import FingerprintDB
    fp_path = Path(__file__).parent.parent / "fy_image_canary" / "fingerprints.yaml"
    if fp_path.exists():
        db = FingerprintDB.load(fp_path)
        assert len(db.models) >= 5
        assert "dall-e-3" in db.models
        assert "gpt-image-1" in db.models
