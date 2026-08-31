from cascade_compression.resources import resource_dir, resource_path


def test_data_resource_is_available():
    assert resource_path("data", "hardware_profiles.json").is_file()


def test_frontend_resource_is_available():
    frontend = resource_dir("frontend")
    assert frontend is not None
    assert (frontend / "index.html").is_file()
