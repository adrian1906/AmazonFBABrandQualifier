import json

from input_loader import brands_from_cli_list, brands_from_file, dedupe_and_flag_aliases


def test_brands_from_cli_list():
    assert brands_from_cli_list("Lemax, Pacific Giftware,  BRK ,") == ["Lemax", "Pacific Giftware", "BRK"]


def test_brands_from_csv_with_recognized_header(tmp_path):
    path = tmp_path / "brands.csv"
    path.write_text("Brand Name,Notes\nLemax,x\nDiamine,y\n", encoding="utf-8")
    assert brands_from_file(path) == ["Lemax", "Diamine"]


def test_brands_from_csv_without_header_falls_back_to_first_column(tmp_path):
    path = tmp_path / "brands.csv"
    path.write_text("Lemax\nDiamine\n", encoding="utf-8")
    assert brands_from_file(path) == ["Lemax", "Diamine"]


def test_brands_from_json_list(tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(["Lemax", "Diamine"]), encoding="utf-8")
    assert brands_from_file(path) == ["Lemax", "Diamine"]


def test_brands_from_json_object_with_brands_key(tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps({"brands": ["Lemax", "Diamine"]}), encoding="utf-8")
    assert brands_from_file(path) == ["Lemax", "Diamine"]


def test_brands_from_txt(tmp_path):
    path = tmp_path / "brands.txt"
    path.write_text("Lemax\n\nDiamine\nZebra\n", encoding="utf-8")
    assert brands_from_file(path) == ["Lemax", "Diamine", "Zebra"]


def test_exact_duplicates_are_collapsed():
    deduped, flagged = dedupe_and_flag_aliases(["Lemax", "LEMAX", "lemax, llc"])
    assert deduped == ["Lemax"]
    assert flagged == []


def test_near_duplicates_are_kept_separate_and_flagged():
    deduped, flagged = dedupe_and_flag_aliases(["Acme", "Acme Outdoor Gear"])
    assert deduped == ["Acme", "Acme Outdoor Gear"]  # never silently merged
    assert flagged == [("Acme", "Acme Outdoor Gear")]


def test_unrelated_brands_are_not_flagged():
    deduped, flagged = dedupe_and_flag_aliases(["Lemax", "Diamine", "Zebra"])
    assert deduped == ["Lemax", "Diamine", "Zebra"]
    assert flagged == []
