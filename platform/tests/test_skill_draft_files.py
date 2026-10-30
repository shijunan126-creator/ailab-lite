import pytest

from ailab_platform.skill_drafts.files import checked_path, classify, store_file, validate_manifest
from ailab_platform.skillfiles import MAX_ENTRIES, MAX_SKILL_MD, MAX_UNPACKED, SkillFileError


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../SKILL.md", "a/../b", "a\\b", "a//b", "a/./b", "a/", "a\x00b"])
def test_rejects_unsafe_paths(path):
    with pytest.raises(SkillFileError):
        checked_path(path)


def test_manifest_limits_and_directory_collisions():
    item = {"path": "SKILL.md", "size": 1, "change": "added"}
    validate_manifest([item], require_skill=True)
    for files in (
        [],
        [item, {**item, "path": "SKILL.md/child"}],
        [item, {**item}],
        [{**item, "size": MAX_SKILL_MD + 1}],
        [item, {**item, "path": "big", "size": MAX_UNPACKED}],
        [{**item, "path": str(i)} for i in range(MAX_ENTRIES + 1)],
    ):
        with pytest.raises(SkillFileError):
            validate_manifest(files, require_skill=True)


def test_content_address_and_classification():
    class Storage:
        def put_bytes(self, key, data):
            self.key, self.data = key, data

    storage = Storage()
    item = store_file(storage, "u", "sd", "reference/a.txt", b"hello", "added")
    assert item["blob"] == storage.key == f"skill-drafts/u/sd/{item['sha256']}"
    assert item["kind"] == "text"
    assert classify("diagram.svg", b"<svg/>") == "text"
    assert classify("asset.dat", b"\x00\xff") == "binary"
