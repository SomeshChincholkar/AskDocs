from io import BytesIO

import pandas as pd

from app.services.extraction import _excel_pages


def test_csv_is_converted_to_labeled_text_rows():
    csv = b"Name,Role\nAda,Engineer\nLin,Researcher\n"

    pages = _excel_pages(csv, ".csv")

    assert len(pages) == 1
    assert pages[0][0] == 1
    assert "Sheet: CSV | Name: Ada | Role: Engineer" in pages[0][1]
    assert "Sheet: CSV | Name: Lin | Role: Researcher" in pages[0][1]


def test_excel_rows_are_grouped_into_pages():
    workbook = BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"Item": range(1, 23), "Count": [2] * 22}).to_excel(
            writer, sheet_name="Inventory", index=False
        )

    pages = _excel_pages(workbook.getvalue(), ".xlsx")

    assert len(pages) == 2
    assert pages[0][0] == 1 and pages[1][0] == 2
    assert pages[0][1].count("Sheet: Inventory") == 20
    assert "Item: 21" in pages[1][1]
