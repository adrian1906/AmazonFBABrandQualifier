import gui_command_builder as cmd


def test_brand_batch_command_minimal():
    command, explanation = cmd.brand_batch_command("brands.csv", None, 5, False)
    assert command == "python batch_runner.py --csv brands.csv"
    assert "brands.csv" in explanation


def test_brand_batch_command_with_options():
    command, _ = cmd.brand_batch_command("brands.csv", 5, 3, True)
    assert command == "python batch_runner.py --csv brands.csv --limit 5 --concurrency 3 --no-web-search"


def test_brand_batch_command_quotes_paths_with_spaces():
    command, _ = cmd.brand_batch_command("my brands.csv", None, 5, False)
    assert "'my brands.csv'" in command


def test_supplier_batch_command_integrated_default():
    command, explanation = cmd.supplier_batch_command("integrated", from_brand_batch="summary_x.csv")
    assert command == "python supplier_batch_runner.py --from-brand-batch summary_x.csv"
    assert "PURSUE" in explanation


def test_supplier_batch_command_integrated_with_include_exclude():
    command, _ = cmd.supplier_batch_command(
        "integrated", from_brand_batch="summary_x.csv", status="INVESTIGATE",
        include="Brand A,Brand B", exclude="Brand C",
    )
    assert "--status INVESTIGATE" in command
    assert "--include 'Brand A,Brand B'" in command
    assert "--exclude 'Brand C'" in command


def test_supplier_batch_command_standalone_list():
    command, _ = cmd.supplier_batch_command("standalone_list", brands="Lemax,Diamine")
    assert command == "python supplier_batch_runner.py --brands Lemax,Diamine"  # no spaces, shlex leaves it unquoted


def test_supplier_batch_command_standalone_list_with_spaces_gets_quoted():
    command, _ = cmd.supplier_batch_command("standalone_list", brands="Lemax, Pacific Giftware")
    assert command == "python supplier_batch_runner.py --brands 'Lemax, Pacific Giftware'"


def test_supplier_batch_command_standalone_file():
    command, _ = cmd.supplier_batch_command("standalone_file", input_path="brands.json")
    assert command == "python supplier_batch_runner.py --input brands.json"


def test_supplier_batch_command_dry_run_and_limit():
    command, explanation = cmd.supplier_batch_command("standalone_list", brands="Lemax", limit=2, dry_run=True)
    assert "--limit 2" in command
    assert "--dry-run" in command
    assert "no paid calls" in explanation


def test_supplier_resume_command():
    command, explanation = cmd.supplier_resume_command("supbatch_20260920_101500")
    assert command == "python supplier_batch_runner.py --resume supbatch_20260920_101500"
    assert "supbatch_20260920_101500" in explanation


def test_supplier_report_command_batch_vs_brand():
    command, _ = cmd.supplier_report_command(batch_id="supbatch_1")
    assert command == "python supplier_report_cli.py --batch supbatch_1"

    command, _ = cmd.supplier_report_command(brand_fragment="Lemax", save=True)
    assert command == "python supplier_report_cli.py --brand Lemax --save"


def test_review_commands_quote_fragments_with_spaces():
    command, _ = cmd.supplier_review_command("Northwind Outdoor")
    assert command == "python supplier_review_one.py 'Northwind Outdoor'"

    command, _ = cmd.brand_review_command("Northwind Outdoor")
    assert command == "python review_one.py 'Northwind Outdoor'"
